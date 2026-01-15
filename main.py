import os
import json
import math
import logging
import asyncio
import threading
import re
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    InlineQueryResultCachedDocument
)
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0)) 
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0)) 
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# --- SETUP LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BSFilterBot")

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
app = Client("BSFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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

# --- HELPER: Add User to Database ---
def add_user(user_id):
    try:
        if user_id < 0: return
        ref = db.reference(f'users/{user_id}')
        if not ref.get():
            ref.set({"active": True})
            logger.info(f"🆕 New User Added: {user_id}")
    except Exception as e:
        logger.error(f"DB Error: {e}")

# --- AUTO DELETE FUNCTION ---
async def delete_file_after_delay(message_id, chat_id, delay_minutes=2):
    try:
        await asyncio.sleep(delay_minutes * 60)
        try:
            await app.delete_messages(chat_id, message_id)
            logger.info(f"🗑️ Deleted message {message_id} in {chat_id}")
        except: pass
        if message_id in DELETE_TASKS: del DELETE_TASKS[message_id]
    except: pass

# --- FILE SENDING LOGIC (DUMP CHANNEL + INVITE LINK) ---
async def send_file_to_user(client, chat_id, unique_id):
    try:
        ref = db.reference(f'files/{unique_id}')
        file_data = ref.get()

        if not file_data:
            await client.send_message(chat_id, "❌ File not found or removed.")
            return

        # 1. Prepare Caption for Dump Channel
        filename = file_data.get('file_name', 'Unknown File')
        size = get_size(file_data.get('file_size', 0))
        
        caption = f"📁 **{filename}**\n" \
                  f"📊 Size: {size}\n" \
                  f"👤 Requested by: {chat_id}\n\n" \
                  f"⏰ **Auto-Delete in 2 minutes.**"
        
        # 2. Send File to DUMP CHANNEL
        try:
            dump_msg = await client.send_cached_media(
                chat_id=DUMP_CHANNEL_ID,
                file_id=file_data['file_id'],
                caption=caption
            )
        except Exception as e:
            logger.error(f"Dump Channel Error: {e}")
            await client.send_message(chat_id, "❌ Error: Bot needs Admin (Invite Users) in Dump Channel.")
            return

        # 3. Schedule Deletion from Dump Channel
        task_dump = asyncio.create_task(delete_file_after_delay(dump_msg.id, DUMP_CHANNEL_ID, 2))
        DELETE_TASKS[dump_msg.id] = task_dump

        # 4. Generate Links
        # A. Invite Link (To Join)
        try:
            # Tries to get the primary invite link
            invite_link = await client.export_chat_invite_link(DUMP_CHANNEL_ID)
        except Exception as e:
            logger.error(f"Link Gen Error: {e}")
            await client.send_message(chat_id, "❌ Bot cannot generate Invite Link. Make sure it's Admin.")
            return

        # B. Message Link (To View File)
        clean_id = str(DUMP_CHANNEL_ID).replace("-100", "")
        file_link = f"https://t.me/c/{clean_id}/{dump_msg.id}"
        
        # 5. Send Message to User with Buttons
        user_text = f"✅ **Your file is ready!**\n\n" \
                    f"📁 **{filename}**\n\n" \
                    f"Join the channel to view your file."

        buttons = [
            [InlineKeyboardButton("📢 Join Channel", url=invite_link)],
            [InlineKeyboardButton("🔗 View File", url=file_link)]
        ]

        user_msg = await client.send_message(
            chat_id=chat_id,
            text=user_text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        # 6. Schedule Deletion of User's Link Message
        task_user = asyncio.create_task(delete_file_after_delay(user_msg.id, chat_id, 2))
        DELETE_TASKS[user_msg.id] = task_user
            
    except Exception as e:
        logger.error(f"Send Error: {e}")
        await client.send_message(chat_id, "❌ Error processing request.")

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
# 2. START COMMAND
# -----------------------------------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    add_user(message.from_user.id)

    if len(message.command) > 1:
        data = message.command[1]
        if data.startswith("dl_"):
            unique_id = data.split("_")[1]
            await message.reply_text("📂 **Fetching your file...**")
            await send_file_to_user(client, message.chat.id, unique_id)
            return

    buttons = [
        [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("🔎 Go Inline Here", switch_inline_query_current_chat="")]
    ]
    
    await message.reply_text(
        f"👋 **Hey {message.from_user.first_name}!**\n"
        "I am BSFilterBot.\n"
        "You can search for movies in this chat OR in groups.\n"
        "You can also use Inline Search (@BotName query).\n\n"
        "Files are sent via Dump Channel and auto-deleted.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# -----------------------------------------------------------------------------
# 3. BROADCAST COMMAND
# -----------------------------------------------------------------------------
@app.on_message(filters.command("broadcast") & filters.private & filters.reply & filters.user(ADMIN_ID))
async def broadcast_handler(client, message):
    msg = await message.reply_text("⏳ **Starting Broadcast...**")
    
    try:
        ref = db.reference('users')
        users_snapshot = ref.get()
        
        if not users_snapshot:
            await msg.edit("❌ No users found.")
            return

        total_users = len(users_snapshot)
        success = 0
        blocked = 0
        failed = 0
        
        await msg.edit(f"📣 Broadcasting to {total_users} users...")
        broadcast_msg = message.reply_to_message
        
        for user_id in users_snapshot.keys():
            try:
                await asyncio.sleep(0.2)
                await broadcast_msg.copy(chat_id=int(user_id))
                success += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await broadcast_msg.copy(chat_id=int(user_id))
                success += 1
            except (InputUserDeactivated, UserIsBlocked, PeerIdInvalid):
                db.reference(f'users/{user_id}').delete()
                blocked += 1
            except:
                failed += 1
        
        await msg.edit(f"✅ **Done**\nActive: {success}\nBlocked: {blocked}")
        
    except Exception as e:
        logger.error(f"Broadcast Error: {e}")
        await msg.edit(f"❌ Error: {e}")

# -----------------------------------------------------------------------------
# 4. TEXT SEARCH HANDLER
# -----------------------------------------------------------------------------
@app.on_message(filters.text & (filters.private | filters.group))
async def search_handler(client, message):
    if message.text.startswith("/") or message.via_bot: return

    add_user(message.from_user.id)
    query = message.text.strip()
    is_group = message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]

    if is_group and len(query) < 2: return

    msg = await message.reply_text("⏳ **Searching...**", quote=True)

    # Auto-Delete Bot Reply & User Request (10 Mins)
    asyncio.create_task(delete_file_after_delay(msg.id, message.chat.id, 10))
    if is_group:
        asyncio.create_task(delete_file_after_delay(message.id, message.chat.id, 10))

    try:
        ref = db.reference('files')
        snapshot = ref.get()

        if not snapshot:
            await msg.edit("❌ Database is empty.")
            return

        clean_query = re.sub(r'[._-]', ' ', query).lower()
        query_words = clean_query.split() 

        results = []
        for key, val in snapshot.items():
            file_name = val.get('file_name', '')
            clean_filename = re.sub(r'[._-]', ' ', file_name).lower()
            
            if all(word in clean_filename for word in query_words):
                results.append(val)
        
        if not results:
            await msg.edit(f"❌ No results found for: `{query}`")
            return

        USER_SEARCHES[message.from_user.id] = results
        await send_results_page(message, msg, page=1, user_id=message.from_user.id)

    except Exception as e:
        logger.error(f"Search Error: {e}")
        await msg.edit("❌ Error occurred.")

# -----------------------------------------------------------------------------
# 5. INLINE SEARCH HANDLER
# -----------------------------------------------------------------------------
@app.on_inline_query()
async def inline_search(client, query):
    text = query.query.strip().lower()
    add_user(query.from_user.id)

    if not text: return

    try:
        ref = db.reference('files')
        snapshot = ref.get()
        if not snapshot: return

        clean_query = re.sub(r'[._-]', ' ', text).lower()
        query_words = clean_query.split() 

        results = []
        count = 0
        
        for key, val in snapshot.items():
            if count >= 50: break
            
            file_name = val.get('file_name', '')
            clean_filename = re.sub(r'[._-]', ' ', file_name).lower()
            
            if all(word in clean_filename for word in query_words):
                count += 1
                size = get_size(val.get('file_size', 0))
                
                caption = f"📁 **{file_name}**\n" \
                          f"📊 Size: {size}\n\n" \
                          f"⚠️ **Note:** Auto-delete works best via Bot PM."

                results.append(
                    InlineQueryResultCachedDocument(
                        id=val['unique_id'],
                        title=file_name,
                        document_file_id=val['file_id'],
                        description=f"Size: {size}",
                        caption=caption 
                    )
                )

        await query.answer(results, cache_time=10)
    except: pass

# -----------------------------------------------------------------------------
# 6. PAGINATION
# -----------------------------------------------------------------------------
async def send_results_page(message, editable_msg, page=1, user_id=None):
    results = USER_SEARCHES.get(user_id)
    if not results:
        await editable_msg.edit("⚠️ Session expired.")
        return

    total_results = len(results)
    total_pages = math.ceil(total_results / RESULTS_PER_PAGE)
    start_i = (page - 1) * RESULTS_PER_PAGE
    current_files = results[start_i : start_i + RESULTS_PER_PAGE]

    buttons = []
    is_group = message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]

    for file in current_files:
        size = get_size(file.get('file_size', 0))
        name = file.get('file_name', 'Unknown').replace("[", "").replace("]", "")
        if len(name) > 30: name = name[:30] + "..."
        
        btn_text = f"[{size}] {name}"
        
        if is_group:
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{file['unique_id']}"
            buttons.append([InlineKeyboardButton(btn_text, url=url)])
        else:
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"dl|{file['unique_id']}")])

    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}|{user_id}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}|{user_id}"))
    if nav: buttons.append(nav)
    
    buttons.append([InlineKeyboardButton("❌ Close", callback_data=f"close|{user_id}")])

    try:
        user = await app.get_users(user_id)
        mention = user.mention
    except: mention = "User"

    text = f"🎬 **Found {total_results} Files** for {mention}\n" \
           f"👇 Click to get file in PM:" if is_group else \
           f"🎬 **Found {total_results} Files**\n👇 Click to download:"

    await editable_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# -----------------------------------------------------------------------------
# 7. CALLBACKS & MAIN
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
                await cb.answer("⚠️ Not your results!", show_alert=True)
                return
            await send_results_page(cb.message, cb.message, page=page_num, user_id=target_user_id)

        elif action == "close":
            target_user_id = int(data[1])
            if cb.from_user.id != target_user_id:
                await cb.answer("⚠️ Cannot close this.", show_alert=True)
                return
            await cb.message.delete()

        elif action == "noop": await cb.answer("Current Page")
    except: pass

async def cancel_all_delete_tasks():
    for task in DELETE_TASKS.values(): task.cancel()

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
        try: loop.run_until_complete(idle_event.wait())
        except KeyboardInterrupt: pass
            
    except Exception as e:
        from pyrogram import idle
        idle()
    finally:
        asyncio.run(cancel_all_delete_tasks())
        app.stop()

if __name__ == "__main__":
    main()
