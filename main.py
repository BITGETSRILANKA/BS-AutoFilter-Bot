import os
import json
import math
import logging
import asyncio
import threading
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
logger = logging.getLogger("MnSearchBot")

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

# --- HEALTH CHECK SERVER ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/health', '/ping']:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Bot is running')
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args): pass

def run_http_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"🌐 Server started on port {port}")
    try:
        server.serve_forever()
    except: pass
    finally: server.server_close()

# --- SETUP BOT ---
app = Client("MnSearchBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- GLOBAL STORAGE ---
USER_SEARCHES = {}
RESULTS_PER_PAGE = 10
DELETE_TASKS = {}
BOT_USERNAME = "" 

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
async def delete_file_after_delay(message_id, chat_id, delay_minutes=2):
    try:
        await asyncio.sleep(delay_minutes * 60)
        try:
            await app.delete_messages(chat_id, message_id)
            logger.info(f"🗑️ Deleted message {message_id}")
        except: pass
        if message_id in DELETE_TASKS: del DELETE_TASKS[message_id]
    except: pass

# --- FILE SENDING LOGIC (COMMON) ---
async def send_file_to_user(client, chat_id, unique_id):
    try:
        ref = db.reference(f'files/{unique_id}')
        file_data = ref.get()

        if not file_data:
            await client.send_message(chat_id, "❌ File not found or removed.")
            return

        caption = f"{file_data.get('caption', '')}\n\n" \
                  f"⏰ **This file will be automatically deleted in 2 minutes**"
        
        sent_msg = await client.send_cached_media(
            chat_id=chat_id,
            file_id=file_data['file_id'],
            caption=caption
        )

        if sent_msg:
            # Schedule File Deletion (2 Minutes)
            task = asyncio.create_task(delete_file_after_delay(sent_msg.id, chat_id, 2))
            DELETE_TASKS[sent_msg.id] = task
            
            # Send Reminder & Schedule its deletion too
            rem_msg = await client.send_message(
                chat_id,
                f"⏰ **File: {file_data.get('file_name', 'Unknown')}**\nDeleting in 2 mins."
            )
            task_rem = asyncio.create_task(delete_file_after_delay(rem_msg.id, chat_id, 2))
            DELETE_TASKS[rem_msg.id] = task_rem
            
    except Exception as e:
        logger.error(f"Send Error: {e}")
        await client.send_message(chat_id, "❌ Error sending file.")

# -----------------------------------------------------------------------------
# 1. INDEXING (CHANNEL)
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
                if message.video and "." not in filename: filename += ".mp4"
                elif message.document and "." not in filename: filename += ".mkv"
            else:
                filename = f"Video_{message.id}.mp4"

        file_data = {
            "file_name": filename,
            "file_size": media.file_size,
            "file_id": media.file_id,
            "unique_id": media.file_unique_id,
            "caption": message.caption or filename
        }

        ref = db.reference(f'files/{media.file_unique_id}')
        ref.set(file_data)
        logger.info(f"✅ Indexed: {filename}")
    except Exception as e:
        logger.error(f"Indexing Error: {e}")

# -----------------------------------------------------------------------------
# 2. START COMMAND (HANDLES DEEP LINKS)
# -----------------------------------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    if len(message.command) > 1:
        data = message.command[1]
        if data.startswith("dl_"):
            unique_id = data.split("_")[1]
            await message.reply_text("📂 **Fetching your file...**")
            await send_file_to_user(client, message.chat.id, unique_id)
            return

    await message.reply_text(
        f"👋 **Hey {message.from_user.first_name}!**\n"
        "I am a Movie Search Bot.\n"
        "You can search for movies in this chat OR in groups.\n\n"
        "Files are auto-deleted after 2 minutes."
    )

# -----------------------------------------------------------------------------
# 3. SEARCH HANDLER (ADVANCED SEARCH)
# -----------------------------------------------------------------------------
@app.on_message(filters.text & (filters.private | filters.group))
async def search_handler(client, message):
    if message.text.startswith("/"): return

    query = message.text.strip()
    
    # In groups, ignore very short queries
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP] and len(query) < 2:
        return

    msg = await message.reply_text("⏳ **Searching...**", quote=True)

    # --- NEW: SCHEDULE DELETION OF SEARCH RESULT (10 MINUTES) ---
    asyncio.create_task(delete_file_after_delay(msg.id, message.chat.id, 10))
    # ------------------------------------------------------------

    try:
        ref = db.reference('files')
        snapshot = ref.get()

        if not snapshot:
            await msg.edit("❌ Database is empty.")
            return

        # --- ADVANCED SEARCH LOGIC ---
        # 1. Clean the query
        clean_query = re.sub(r'[._-]', ' ', query).lower()
        query_words = clean_query.split() 

        results = []
        for key, val in snapshot.items():
            file_name = val.get('file_name', '')
            # 2. Clean the filename
            clean_filename = re.sub(r'[._-]', ' ', file_name).lower()
            
            # 3. Check if ALL words in query exist in filename
            if all(word in clean_filename for word in query_words):
                results.append(val)
        
        if not results:
            await msg.edit(f"❌ No results found for: `{query}`")
            return

        # Store results
        USER_SEARCHES[message.from_user.id] = results
        
        await send_results_page(message, msg, page=1, user_id=message.from_user.id)

    except Exception as e:
        logger.error(f"Search Error: {e}")
        await msg.edit("❌ Error occurred.")

# -----------------------------------------------------------------------------
# 4. PAGINATION & RESULTS DISPLAY
# -----------------------------------------------------------------------------
async def send_results_page(message, editable_msg, page=1, user_id=None):
    results = USER_SEARCHES.get(user_id)

    if not results:
        await editable_msg.edit("⚠️ Session expired. Please search again.")
        return

    total_results = len(results)
    total_pages = math.ceil(total_results / RESULTS_PER_PAGE)
    start_i = (page - 1) * RESULTS_PER_PAGE
    current_files = results[start_i : start_i + RESULTS_PER_PAGE]

    buttons = []
    
    # Check Context
    is_group = message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]

    for file in current_files:
        size = get_size(file.get('file_size', 0))
        name = file.get('file_name', 'Unknown').replace("[", "").replace("]", "")
        if len(name) > 30: name = name[:30] + "..."
        
        btn_text = f"[{size}] {name}"
        
        if is_group:
            # GROUP: Deep Link
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{file['unique_id']}"
            buttons.append([InlineKeyboardButton(btn_text, url=url)])
        else:
            # PRIVATE: Callback
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"dl|{file['unique_id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}|{user_id}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}|{user_id}"))
    
    if nav: buttons.append(nav)
    
    # Close Button
    buttons.append([InlineKeyboardButton("❌ Close", callback_data=f"close|{user_id}")])

    # --- NEW: ADD TO GROUP BUTTON ---
    add_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=true"
    buttons.append([InlineKeyboardButton("➕ Add Me To Your Group", url=add_group_url)])
    # -------------------------------

    try:
        user = await app.get_users(user_id)
        mention = user.mention
    except:
        mention = "User"

    text = f"🎬 **Found {total_results} Files** for {mention}\n" \
           f"👇 Click to get file in PM:" if is_group else \
           f"🎬 **Found {total_results} Files**\n👇 Click to download:"

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
            await cb.answer("📂 Sending file...")
            await send_file_to_user(client, cb.message.chat.id, data[1])

        elif action == "page":
            page_num = int(data[1])
            target_user_id = int(data[2])

            if cb.from_user.id != target_user_id:
                await cb.answer("⚠️ These aren't your results!", show_alert=True)
                return

            await send_results_page(cb.message, cb.message, page=page_num, user_id=target_user_id)

        elif action == "close":
            target_user_id = int(data[1])
            if cb.from_user.id != target_user_id:
                await cb.answer("⚠️ Only the searcher can close this.", show_alert=True)
                return
            await cb.message.delete()

        elif action == "noop":
            await cb.answer("Current Page")

    except Exception as e:
        logger.error(f"Callback Error: {e}")

# -----------------------------------------------------------------------------
# 6. MAIN
# -----------------------------------------------------------------------------
async def cancel_all_delete_tasks():
    for task in DELETE_TASKS.values():
        task.cancel()

def main():
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    print("Bot Started...")
    try:
        app.start()
        global BOT_USERNAME
        me = app.get_me()
        BOT_USERNAME = me.username
        logger.info(f"🤖 Bot Username: @{BOT_USERNAME}")
        
        import signal
        idle_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(idle_event.wait())
        except KeyboardInterrupt:
            pass
            
    except Exception as e:
        from pyrogram import idle
        idle()
    finally:
        asyncio.run(cancel_all_delete_tasks())
        app.stop()

if __name__ == "__main__":
    main()
