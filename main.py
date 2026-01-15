import os
import json
import math
import logging
import asyncio
import threading
import re
import time
import psutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    InlineQueryResultCachedDocument
)
import firebase_admin
from firebase_admin import credentials, db

# -----------------------------------------------------------------------------
# 1. CONFIGURATION
# -----------------------------------------------------------------------------
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "") # Paste JSON content here
PORT = int(os.environ.get('PORT', 8080))
DELETE_DELAY = 120  # 2 Minutes

# -----------------------------------------------------------------------------
# 2. LOGGING & FIREBASE SETUP
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BSFilterBot")

if not firebase_admin._apps:
    try:
        if FIREBASE_KEY:
            cred_dict = json.loads(FIREBASE_KEY)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
            logger.info("✅ Firebase Initialized")
        else:
            logger.error("❌ FIREBASE_KEY missing")
    except Exception as e:
        logger.error(f"❌ Firebase Init Error: {e}")

# -----------------------------------------------------------------------------
# 3. GLOBAL VARIABLES & CACHE
# -----------------------------------------------------------------------------
FILES_CACHE = []
USER_SEARCH_CACHE = {}
BOT_USERNAME = ""

# -----------------------------------------------------------------------------
# 4. DATABASE & HELPER FUNCTIONS
# -----------------------------------------------------------------------------
def refresh_cache():
    """Loads all files from DB to RAM at startup."""
    global FILES_CACHE
    try:
        ref = db.reference('files')
        snapshot = ref.get()
        if snapshot:
            FILES_CACHE = list(snapshot.values())
        logger.info(f"🚀 Cache Refreshed: {len(FILES_CACHE)} files in RAM")
    except Exception as e:
        logger.error(f"Cache Refresh Error: {e}")

def add_file_to_db(file_data):
    try:
        ref = db.reference(f'files/{file_data["unique_id"]}')
        ref.set(file_data)
        FILES_CACHE.append(file_data) # Add to RAM immediately
        return True
    except Exception as e:
        logger.error(f"DB Write Error: {e}")
        return False

def get_file_by_id(unique_id):
    # Try RAM first
    for file in FILES_CACHE:
        if file['unique_id'] == unique_id:
            return file
    # Fallback to DB
    return db.reference(f'files/{unique_id}').get()

def add_user(user_id):
    if user_id < 0: return
    try:
        ref = db.reference(f'users/{user_id}')
        if not ref.get(): ref.set({"active": True})
    except: pass

def get_total_users():
    try:
        snap = db.reference('users').get()
        return len(snap) if snap else 0
    except: return 0

# --- Persistent Auto Delete Logic ---
def add_delete_task(chat_id, message_id, delete_time):
    try:
        task_id = f"{chat_id}_{message_id}"
        db.reference(f'delete_queue/{task_id}').set({
            "chat_id": chat_id,
            "message_id": message_id,
            "delete_time": delete_time
        })
    except: pass

def get_due_delete_tasks():
    try:
        ref = db.reference('delete_queue')
        snapshot = ref.get()
        tasks = []
        now = time.time()
        if snapshot:
            for key, val in snapshot.items():
                if val['delete_time'] <= now:
                    val['key'] = key
                    tasks.append(val)
        return tasks
    except: return []

def remove_delete_task(key):
    try: db.reference(f'delete_queue/{key}').delete()
    except: pass

# --- Utilities ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

def clean_text(text):
    """Removes special chars for smart search."""
    return re.sub(r'[\W_]+', '', text).lower()

def get_system_stats():
    process = psutil.Process(os.getpid())
    return get_size(process.memory_info().rss)

# -----------------------------------------------------------------------------
# 5. BOT CLIENT & HTTP SERVER
# -----------------------------------------------------------------------------
app = Client("BSFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

async def temp_del(msg, seconds):
    await asyncio.sleep(seconds)
    try: await msg.delete()
    except: pass

# -----------------------------------------------------------------------------
# 6. BACKGROUND TASKS
# -----------------------------------------------------------------------------
async def check_auto_delete():
    """Persistent Auto-Delete Loop"""
    while True:
        try:
            tasks = get_due_delete_tasks()
            for task in tasks:
                try:
                    await app.delete_messages(task['chat_id'], task['message_id'])
                    logger.info(f"🗑️ Auto-deleted {task['message_id']}")
                except Exception: pass
                # Clean DB even if delete fails (msg likely already gone)
                remove_delete_task(task['key'])
        except Exception as e:
            logger.error(f"Auto-Delete Error: {e}")
        await asyncio.sleep(20)

# -----------------------------------------------------------------------------
# 7. BOT HANDLERS
# -----------------------------------------------------------------------------

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    add_user(message.from_user.id)
    
    # Deep Link Check (File Download)
    if len(message.command) > 1 and message.command[1].startswith("dl_"):
        unique_id = message.command[1].split("_")[1]
        await send_file_to_user(client, message.chat.id, unique_id)
        return

    buttons = [
        [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("🔎 Inline Search", switch_inline_query_current_chat="")]
    ]
    await message.reply_text(
        f"👋 Hi **{message.from_user.first_name}**!\n"
        "I am a high-speed Auto-Filter Bot.\n"
        "Type a movie name to search.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_handler(client, message):
    msg = await message.reply_text("⏳ Calculating...")
    files = len(FILES_CACHE)
    users = get_total_users()
    ram = get_system_stats()
    await msg.edit(f"📊 **Bot Stats**\n\n📂 Files: `{files}`\n👤 Users: `{users}`\n💾 RAM: `{ram}`")

@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_handler(client, message):
    media = message.document or message.video
    if not media: return
    
    filename = getattr(media, "file_name", None) or "Unknown"
    # Fallback to caption if filename is weird
    if (not filename or filename == "Unknown" or filename.startswith("Video_")) and message.caption:
        filename = message.caption.splitlines()[0]
    
    data = {
        "file_name": filename,
        "file_size": media.file_size,
        "file_id": media.file_id,
        "unique_id": media.file_unique_id,
        "caption": message.caption or filename 
    }
    
    if add_file_to_db(data):
        logger.info(f"✅ Indexed: {filename}")

@app.on_message(filters.text & (filters.private | filters.group))
async def search_handler(client, message):
    if message.text.startswith("/") or message.via_bot: return
    
    query = message.text.strip()
    if len(query) < 2: return

    # --- RAM SEARCH LOGIC ---
    raw_query = query.lower().split()
    clean_query = clean_text(query)
    results = []
    
    for file in FILES_CACHE:
        # Prepare data
        fname_raw = file.get('file_name', '').lower()
        fname_clean = clean_text(fname_raw)
        capt_raw = file.get('caption', '').lower()
        capt_clean = clean_text(capt_raw)
        
        # Match against Filename OR Caption
        if clean_query in fname_clean or clean_query in capt_clean:
            results.append(file)
            continue
        if all(w in fname_raw for w in raw_query):
            results.append(file)
            
    if not results:
        if message.chat.type == enums.ChatType.PRIVATE:
            msg = await message.reply_text(f"❌ No files found for: `{query}`")
            asyncio.create_task(temp_del(msg, 5))
        return
        
    USER_SEARCH_CACHE[message.from_user.id] = results
    await send_results_page(message, page=1)

@app.on_inline_query()
async def inline_handler(client, query):
    text = query.query.strip()
    if not text: return
    
    clean_q = clean_text(text)
    raw_q = text.lower().split()
    results = []
    count = 0
    
    for file in FILES_CACHE:
        if count >= 50: break
        
        fname_raw = file.get('file_name', '').lower()
        fname_clean = clean_text(fname_raw)
        
        matched = False
        if clean_q in fname_clean: matched = True
        elif all(w in fname_raw for w in raw_q): matched = True
        
        if matched:
            count += 1
            size = get_size(file['file_size'])
            
            # CLEAN CAPTION (No Ads)
            clean_caption = (
                f"📁 **{file['file_name']}**\n"
                f"📊 Size: {size}\n\n"
                f"🤖 Bot: @{BOT_USERNAME}"
            )

            results.append(
                InlineQueryResultCachedDocument(
                    id=file['unique_id'],
                    title=file['file_name'],
                    document_file_id=file['file_id'],
                    description=f"Size: {size}",
                    caption=clean_caption 
                )
            )
    await query.answer(results, cache_time=10)

async def send_file_to_user(client, chat_id, unique_id):
    file_data = get_file_by_id(unique_id)
    if not file_data:
        return await client.send_message(chat_id, "❌ File removed.")
    
    caption = (
        f"📁 **{file_data['file_name']}**\n"
        f"📊 Size: {get_size(file_data['file_size'])}\n\n"
        f"⏳ **This message will be deleted in 2 minutes.**"
    )
    
    try:
        sent = await client.send_cached_media(
            chat_id=chat_id,
            file_id=file_data['file_id'],
            caption=caption
        )
        delete_time = time.time() + DELETE_DELAY
        add_delete_task(chat_id, sent.id, delete_time)
    except Exception as e:
        logger.error(f"Send Error: {e}")

async def send_results_page(message, page=1):
    user_id = message.from_user.id
    results = USER_SEARCH_CACHE.get(user_id)
    if not results: return
    
    total = len(results)
    total_pages = math.ceil(total / RESULTS_PER_PAGE)
    start = (page - 1) * RESULTS_PER_PAGE
    current = results[start : start + RESULTS_PER_PAGE]
    
    buttons = []
    for file in current:
        name = file['file_name'][:30]
        size = get_size(file['file_size'])
        if message.chat.type == enums.ChatType.PRIVATE:
            cb_data = f"dl|{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", callback_data=cb_data)])
        else:
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", url=url)])

    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav: buttons.append(nav)
    
    text = f"🔍 **Found {total} files**\nPage {page}/{total_pages}"
    
    if isinstance(message, str): 
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else: 
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query()
async def callback_handler(client, cb):
    data = cb.data.split("|")
    if data[0] == "dl":
        await cb.answer()
        await send_file_to_user(client, cb.message.chat.id, data[1])
    elif data[0] == "page":
        await send_results_page(cb.message, page=int(data[1]))
    elif data[0] == "noop":
        await cb.answer()

# -----------------------------------------------------------------------------
# 8. MAIN EXECUTION
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Start HTTP Server for Health Check
    threading.Thread(target=run_http_server, daemon=True).start()
    
    # Refresh Cache from DB
    refresh_cache()
    
    print("🤖 Bot Starting...")
    app.start()
    
    # Get Username
    me = app.get_me()
    BOT_USERNAME = me.username
    
    # Start Auto-Delete Loop
    loop = asyncio.get_event_loop()
    loop.create_task(check_auto_delete())
    
    print(f"✅ Bot Started as @{BOT_USERNAME}")
    from pyrogram import idle
    idle()
    app.stop()
