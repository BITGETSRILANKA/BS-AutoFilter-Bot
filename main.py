import os
import json
import math
import logging
import asyncio
import threading
import re
import time
import psutil
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler

# Libraries
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
)
import firebase_admin
from firebase_admin import credentials, db
from imdb import Cinemagoer  # IMDb Search

# -----------------------------------------------------------------------------
# 1. CONFIGURATION
# -----------------------------------------------------------------------------
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "") 
PORT = int(os.environ.get('PORT', 8080)) # Vital for Health Check

# Timers
FILE_MSG_DELETE_TIME = 120
RESULT_MSG_DELETE_TIME = 600
USER_MSG_DELETE_TIME = 300
SUGGESTION_DELETE_TIME = 300

# -----------------------------------------------------------------------------
# 2. INITIALIZATION
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BSFilterBot")

ia = Cinemagoer() # IMDb Client

if not firebase_admin._apps:
    try:
        cred_dict = json.loads(FIREBASE_KEY)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
        logger.info("✅ Firebase Initialized")
    except Exception as e:
        logger.error(f"❌ Firebase Init Error: {e}")

FILES_CACHE = []
SEARCH_DATA_CACHE = {} 
BOT_USERNAME = ""
RESULTS_PER_PAGE = 10

# -----------------------------------------------------------------------------
# 3. HEALTH CHECK SERVER (Prevents the bot from being killed)
# -----------------------------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Live and Running!")

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    logger.info(f"🚀 Health check server started on port {PORT}")
    server.serve_forever()

# -----------------------------------------------------------------------------
# 4. HELPER FUNCTIONS
# -----------------------------------------------------------------------------
def refresh_cache():
    global FILES_CACHE
    try:
        ref = db.reference('files')
        snapshot = ref.get()
        if snapshot:
            FILES_CACHE = list(snapshot.values())
        logger.info(f"📂 Cache Updated: {len(FILES_CACHE)} files in memory")
    except Exception as e:
        logger.error(f"Cache Refresh Error: {e}")

def get_size(size):
    if not size: return "0B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def clean_text(text):
    return re.sub(r'[\W_]+', ' ', text).lower().strip()

# IMDb Suggestion Engine
def get_imdb_suggestions(query):
    try:
        # ia.search_movie handles spelling mistakes automatically
        search_results = ia.search_movie(query)
        suggestions = []
        for movie in search_results:
            if movie.get('kind') in ['movie', 'tv series']:
                title = movie.get('title')
                year = movie.get('year')
                # Returns: "Movie Name (2024)"
                suggestions.append(f"{title} ({year})" if year else title)
            if len(suggestions) >= 6: break
        return suggestions
    except: return []

def add_delete_task(chat_id, message_id, delete_time):
    try:
        db.reference(f'delete_queue/{chat_id}_{message_id}').set({
            "chat_id": chat_id, "message_id": message_id, "delete_time": delete_time
        })
    except: pass

# -----------------------------------------------------------------------------
# 5. SEARCH LOGIC
# -----------------------------------------------------------------------------
async def perform_search(client, message, query, is_correction=False):
    # Auto-delete user request
    add_delete_task(message.chat.id, message.id, time.time() + USER_MSG_DELETE_TIME)
    
    clean_query = clean_text(query)
    raw_query = query.lower().split()
    results = []
    
    # 1. Search Local Database
    for file in FILES_CACHE:
        fname = clean_text(file.get('file_name', ''))
        # Match "Kaavalan" even if file is "Fm Kaavalan"
        if clean_query in fname or all(w in fname for w in raw_query):
            results.append(file)

    if results:
        search_id = str(uuid.uuid4())[:8] 
        SEARCH_DATA_CACHE[search_id] = results
        await send_results_page(message, search_id, page=1, is_edit=is_correction)
        return

    # 2. If nothing found, get suggestions from IMDb
    suggestions = get_imdb_suggestions(query)
    
    if suggestions:
        btn = []
        for sugg in suggestions:
            # Strip year " (2011)" to keep search efficient
            clean_sugg = re.sub(r'\(.*?\)', '', sugg).strip()
            btn.append([InlineKeyboardButton(sugg, callback_data=f"sp|{clean_sugg[:40]}")])
        btn.append([InlineKeyboardButton("🚫 CLOSE 🚫", callback_data="close_data")])
        
        text = f"⚠️ **I couldn't find anything for:** `{query}`\n\n‼️ **Is it one of these?** 👇"
        if is_correction:
            sent = await message.edit_text(text, reply_markup=InlineKeyboardMarkup(btn))
        else:
            sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn))
        add_delete_task(sent.chat.id, sent.id, time.time() + SUGGESTION_DELETE_TIME)
    else:
        text = f"🚫 **No movie found for:** `{query}`"
        if is_correction: await message.edit_text(text)
        else: await message.reply_text(text)

async def send_results_page(message, search_id, page=1, is_edit=False):
    results = SEARCH_DATA_CACHE.get(search_id)
    if not results: return
    
    total = len(results)
    total_pages = math.ceil(total / RESULTS_PER_PAGE)
    start = (page - 1) * RESULTS_PER_PAGE
    current = results[start : start + RESULTS_PER_PAGE]
    
    buttons = []
    for file in current:
        name = file['file_name'][:35]
        size = get_size(file['file_size'])
        if message.chat.type == enums.ChatType.PRIVATE:
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", callback_data=f"dl|{file['unique_id']}")])
        else:
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", url=url)])

    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{search_id}|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{search_id}|{page+1}"))
    if nav: buttons.append(nav)
    
    text = f"🔍 **Found {total} results**"
    try:
        if is_edit: await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
            add_delete_task(sent.chat.id, sent.id, time.time() + RESULT_MSG_DELETE_TIME)
    except: pass

# -----------------------------------------------------------------------------
# 6. BOT CLIENT & BACKGROUND TASKS
# -----------------------------------------------------------------------------
app = Client("BSFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def auto_delete_monitor():
    while True:
        try:
            ref = db.reference('delete_queue')
            tasks = ref.get()
            now = time.time()
            if tasks:
                for key, val in tasks.items():
                    if val['delete_time'] <= now:
                        try: await app.delete_messages(val['chat_id'], val['message_id'])
                        except: pass
                        ref.child(key).delete()
        except: pass
        await asyncio.sleep(20)

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if len(message.command) > 1 and message.command[1].startswith("dl_"):
        uid = message.command[1].split("_")[1]
        for f in FILES_CACHE:
            if f['unique_id'] == uid:
                sent = await client.send_cached_media(message.chat.id, f['file_id'], caption=f"📁 **{f['file_name']}**")
                add_delete_task(message.chat.id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
                return
    await message.reply_text(f"👋 Hi {message.from_user.first_name}!\nSend me a movie name to search.")

@app.on_message(filters.text & (filters.private | filters.group))
async def search_handler(client, message):
    if message.text.startswith("/") or message.via_bot: return
    await perform_search(client, message, message.text.strip())

@app.on_callback_query()
async def callback_handler(client, cb):
    data = cb.data.split("|")
    if data[0] == "dl":
        for f in FILES_CACHE:
            if f['unique_id'] == data[1]:
                sent = await client.send_cached_media(cb.message.chat.id, f['file_id'], caption=f"📁 **{f['file_name']}**")
                add_delete_task(cb.message.chat.id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
                await cb.answer()
                return
    elif data[0] == "sp":
        await cb.answer("🔍 Re-searching for correct title...")
        await perform_search(client, cb.message, data[1], is_correction=True)
    elif data[0] == "page":
        await send_results_page(cb.message, data[1], int(data[2]), is_edit=True)
        await cb.answer()
    elif data[0] == "close_data":
        await cb.message.delete()

# -----------------------------------------------------------------------------
# 7. MAIN EXECUTION
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # 1. Start Health Check Server in a separate thread
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # 2. Sync Files
    refresh_cache()
    
    # 3. Start Pyrogram
    app.start()
    
    BOT_USERNAME = app.get_me().username
    print(f"✅ @{BOT_USERNAME} is Live!")
    
    # 4. Start Background Tasks
    loop = asyncio.get_event_loop()
    loop.create_task(auto_delete_monitor())
    
    from pyrogram import idle
    idle()
    app.stop()
