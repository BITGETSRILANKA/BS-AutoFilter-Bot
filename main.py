import os
import json
import math
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    InlineQueryResultCachedDocument
)
from pyrogram.errors import MessageDeleteForbidden
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# --- SETUP LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BSAutoFilterBot")

# --- SETUP FIREBASE ---
if not firebase_admin._apps:
    try:
        if FIREBASE_KEY:
            cred_dict = json.loads(FIREBASE_KEY)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
            logger.info("✅ Firebase Initialized Successfully")
        else:
            logger.error("❌ FIREBASE_KEY is missing")
    except Exception as e:
        logger.error(f"❌ Firebase Error: {e}")

# --- SIMPLE HTTP SERVER FOR KOYEB HEALTH CHECKS ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/health', '/ping']:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'BS Auto Filter Bot is running')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_http_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"🌐 HTTP Health Check Server started on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

# --- SETUP BOT ---
app = Client("BSAutoFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- GLOBAL STORAGE ---
USER_SEARCHES = {}
RESULTS_PER_PAGE = 10
DELETE_TASKS = {}

# --- HELPER: Size ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

# --- AUTO DELETE FUNCTION ---
async def delete_message_after_delay(message_id, chat_id, delay_minutes=2, message_type="file"):
    """Delete a message after specified delay"""
    try:
        # Wait for the delay
        await asyncio.sleep(delay_minutes * 60)
        
        # Try to delete
        try:
            await app.delete_messages(chat_id, message_id)
            logger.info(f"🗑️ Deleted {message_type} message {message_id}")
        except MessageDeleteForbidden:
            logger.warning(f"⚠️ Cannot delete {message_type} message {message_id} - forbidden")
        except Exception as e:
            logger.error(f"❌ Error deleting {message_type} message {message_id}: {e}")
        
        # Cleanup task
        task_key = f"{message_id}_{chat_id}"
        if task_key in DELETE_TASKS:
            del DELETE_TASKS[task_key]
            
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"❌ Error in delete_message_after_delay: {e}")

# -----------------------------------------------------------------------------
# 1. SMARTER FILE INDEXING
# -----------------------------------------------------------------------------
@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    try:
        media = message.document or message.video
        if not media: return

        filename = getattr(media, "file_name", None)
        
        if not filename:
            if message.caption:
                filename = message.caption.split("\n")[0].strip()
                if message.video and not "." in filename:
                    filename += ".mp4"
                elif message.document and not "." in filename:
                    filename += ".mkv"
            else:
                filename = f"Video_{message.id}.mp4"

        valid_exts = ('.mkv', '.mp4', '.avi', '.webm', '.mov')
        if not filename.lower().endswith(valid_exts) and not message.video:
            return

        file_data = {
            "file_name": filename,
            "file_size": media.file_size,
            "file_id": media.file_id,
            "unique_id": media.file_unique_id,
            "caption": message.caption or filename
        }

        ref = db.reference(f'files/{media.file_unique_id}')
        ref.set(file_data)
        
        logger.info(f"✅ Indexed: {filename} (ID: {message.id})")

    except Exception as e:
        logger.error(f"❌ Error indexing file: {e}")

# -----------------------------------------------------------------------------
# 2. INLINE SEARCH HANDLER (NEW FEATURE)
# -----------------------------------------------------------------------------
@app.on_inline_query()
async def answer_inline(client, inline_query):
    try:
        query = inline_query.query.lower().strip()
        
        # If query is empty, don't search
        if not query:
            return

        # Fetch files from DB
        ref = db.reference('files')
        snapshot = ref.get()

        if not snapshot:
            return

        results = []
        count = 0
        
        for key, val in snapshot.items():
            # Stop if we have enough results (Telegram limit is usually 50)
            if count >= 50:
                break
                
            f_name = val.get('file_name', '').lower().replace(".", " ")
            
            # Simple substring match
            if query in f_name:
                count += 1
                size = get_size(val.get('file_size', 0))
                file_name_display = val.get('file_name', 'Unknown')
                
                # Caption for the sent file (Like in your screenshot)
                caption = (
                    f"📂 **{file_name_display}**\n"
                    f"⚙️ **Size:** {size}\n\n"
                    f"⚠️ **Note:** This message may auto-delete due to copyright."
                )
                
                # Buttons for the result
                buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Search Again", switch_inline_query_current_chat="")]
                ])

                results.append(
                    InlineQueryResultCachedDocument(
                        title=f"{file_name_display}",
                        description=f"Size: {size}",
                        file_id=val['file_id'],
                        caption=caption,
                        reply_markup=buttons
                    )
                )

        # Cache results for 1 minute (60s) to reduce DB load
        if results:
            await inline_query.answer(results, cache_time=60)
        else:
            # Send a "No results" article if nothing found
            pass 

    except Exception as e:
        logger.error(f"Inline Error: {e}")

# -----------------------------------------------------------------------------
# 3. TEXT SEARCH & COMMANDS
# -----------------------------------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply_text(
        f"👋 **Hey {message.from_user.first_name}!**\n"
        "Welcome to **BS Auto Filter Bot** 🎬\n\n"
        "You can search by typing text here, or use **Inline Mode**!\n"
        "Try typing: `@YourBotUserName kaththi` in any chat."
    )

@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    await message.reply_text("Send me a movie name or use inline search.")

@app.on_message(filters.text & filters.private)
async def search_handler(client, message):
    query = message.text.strip().lower()
    if query.startswith('/'): return
        
    msg = await message.reply_text("⏳ **Searching...**")

    try:
        ref = db.reference('files')
        snapshot = ref.get()

        if not snapshot:
            await msg.edit("❌ Database is empty.")
            return

        results = []
        for key, val in snapshot.items():
            f_name = val.get('file_name', '').lower().replace(".", " ")
            if query in f_name:
                results.append(val)
        
        if not results:
            await msg.edit(f"❌ No results found for: `{query}`")
            return

        # --- FIX FOR CALLBACK ERROR: STORE QUERY IN DICT ---
        USER_SEARCHES[message.from_user.id] = {
            "query": message.text,   # Store the query text here
            "results": results       # Store results list here
        }
        
        await send_results_page(message, msg, page=1)
        
        if msg:
            task_key = f"{msg.id}_{msg.chat.id}"
            delete_task = asyncio.create_task(
                delete_message_after_delay(msg.id, msg.chat.id, 10, "search_results")
            )
            DELETE_TASKS[task_key] = delete_task

    except Exception as e:
        logger.error(f"Search Error: {e}")
        await msg.edit("❌ Error occurred.")

# -----------------------------------------------------------------------------
# 4. PAGINATION
# -----------------------------------------------------------------------------
async def send_results_page(message, editable_msg, page=1):
    user_id = message.from_user.id
    
    # --- FIX FOR CALLBACK ERROR: GET DATA SAFELY ---
    user_data = USER_SEARCHES.get(user_id)
    if not user_data:
        await editable_msg.edit("⚠️ Session expired.")
        return

    results = user_data['results']
    search_query = user_data['query']

    total_results = len(results)
    total_pages = math.ceil(total_results / RESULTS_PER_PAGE)
    start_i = (page - 1) * RESULTS_PER_PAGE
    current_files = results[start_i : start_i + RESULTS_PER_PAGE]

    buttons = []
    for file in current_files:
        size = get_size(file.get('file_size', 0))
        name = file.get('file_name', 'Unknown')
        btn_name = name.replace("[", "").replace("]", "")
        if len(btn_name) > 40: btn_name = btn_name[:40] + "..."
        
        buttons.append([InlineKeyboardButton(f"[{size}] {btn_name}", callback_data=f"dl|{file['unique_id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav: buttons.append(nav)
    
    current_time = datetime.now().strftime("%H:%M")
    
    # Use stored search_query
    text = f"**Found {total_results} Files** 🎬\n" \
           f"Click to download:\n\n" \
           f"⏰ **Auto-Delete:** 2 mins\n" \
           f"*Search: {search_query}*"

    await editable_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# -----------------------------------------------------------------------------
# 5. CALLBACK HANDLER
# -----------------------------------------------------------------------------
@app.on_callback_query()
async def callback_handler(client, cb):
    try:
        data = cb.data.split("|")
        action = data[0]

        if action == "dl":
            unique_id = data[1]
            ref = db.reference(f'files/{unique_id}')
            file_data = ref.get()

            if not file_data:
                await cb.answer("❌ File not found.", show_alert=True)
                return
            
            await cb.answer("📂 Sending...")
            
            file_name = file_data.get('file_name', 'Unknown')
            caption = f"**{file_name}**\n\n⏰ **Auto-delete in 2 minutes**"
            
            sent_message = await client.send_cached_media(
                chat_id=cb.message.chat.id,
                file_id=file_data['file_id'],
                caption=caption
            )
            
            if sent_message:
                task_key = f"{sent_message.id}_{sent_message.chat.id}"
                DELETE_TASKS[task_key] = asyncio.create_task(
                    delete_message_after_delay(sent_message.id, cb.message.chat.id, 2, "file")
                )
                
                reminder = await cb.message.reply_text(f"⏰ `{file_name}` will auto-delete in 2 mins!")
                reminder_key = f"{reminder.id}_{reminder.chat.id}"
                DELETE_TASKS[reminder_key] = asyncio.create_task(
                    delete_message_after_delay(reminder.id, cb.message.chat.id, 1, "reminder")
                )

        elif action == "page":
            user_id = cb.from_user.id
            if user_id in USER_SEARCHES:
                await send_results_page(cb, cb.message, page=int(data[1]))
                await cb.answer()
            else:
                await cb.answer("⚠️ Expired", show_alert=True)
            
        elif action == "noop":
            await cb.answer()

    except Exception as e:
        logger.error(f"Callback Error: {e}")

# -----------------------------------------------------------------------------
# 6. MAIN & CLEANUP
# -----------------------------------------------------------------------------
async def cancel_all_delete_tasks():
    for task in DELETE_TASKS.values():
        task.cancel()
    DELETE_TASKS.clear()

def main():
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    print("BS Auto Filter Bot Started...")
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(cancel_all_delete_tasks())

if __name__ == "__main__":
    main()
