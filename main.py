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
    InlineQueryResultCachedDocument
)
import firebase_admin
from firebase_admin import credentials, db
from imdb import Cinemagoer 
from rapidfuzz import process, fuzz 

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
PORT = int(os.environ.get('PORT', 8080))

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

ia = Cinemagoer()

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
# 3. HELPER FUNCTIONS & CLEANER
# -----------------------------------------------------------------------------

def extract_proper_title(text):
    """Turns messy filenames into Clean Movie (Year)"""
    text = re.sub(r'\[.*?\]|@\w+', '', text) # Remove tags
    year_match = re.search(r'\b(19|20)\d{2}\b', text)
    year = f" ({year_match.group(0)})" if year_match else ""
    junk = r'(?i)\b(720p|1080p|4k|mkv|mp4|avi|hindi|tamil|telugu|eng|dual|brrip|dvdrip|web|h264|hevc|x264|x265)\b'
    text = re.sub(junk, ' ', text)
    text = re.sub(r'[\.\_\-\(\)]', ' ', text)
    clean_name = re.sub(r'\s+', ' ', text).strip().title()
    return f"{clean_name}{year}".strip()

def refresh_cache():
    global FILES_CACHE
    try:
        ref = db.reference('files')
        snapshot = ref.get()
        if snapshot: FILES_CACHE = list(snapshot.values())
        logger.info(f"🚀 Cache Refreshed: {len(FILES_CACHE)} files")
    except Exception as e: logger.error(f"Cache Error: {e}")

def get_size(size):
    if not size: return "0B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def clean_text(text):
    return re.sub(r'[\W_]+', ' ', text).lower().strip()

def add_user(user_id):
    try:
        ref = db.reference(f'users/{user_id}')
        if not ref.get(): ref.set({"active": True})
    except: pass

def get_all_users():
    try:
        snap = db.reference('users').get()
        return list(snap.keys()) if snap else []
    except: return []

# -----------------------------------------------------------------------------
# 4. HEALTH CHECK & BACKGROUND TASKS
# -----------------------------------------------------------------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

def add_delete_task(chat_id, message_id, delete_time):
    try:
        db.reference(f'delete_queue/{chat_id}_{message_id}').set({
            "chat_id": chat_id, "message_id": message_id, "delete_time": delete_time
        })
    except: pass

async def check_auto_delete():
    while True:
        try:
            ref = db.reference('delete_queue')
            tasks = ref.get()
            now = time.time()
            if tasks:
                for k, v in tasks.items():
                    if v['delete_time'] <= now:
                        try: await app.delete_messages(v['chat_id'], v['message_id'])
                        except: pass
                        ref.child(k).delete()
        except: pass
        await asyncio.sleep(15)

# -----------------------------------------------------------------------------
# 5. SEARCH LOGIC
# -----------------------------------------------------------------------------

async def perform_search(client, message, query, is_correction=False):
    add_delete_task(message.chat.id, message.id, time.time() + USER_MSG_DELETE_TIME)
    clean_q = clean_text(query)
    raw_words = query.lower().split()
    results = []
    
    for file in FILES_CACHE:
        fname = clean_text(file.get('file_name', ''))
        if clean_q in fname or all(w in fname for w in raw_words):
            results.append(file)

    if results:
        search_id = str(uuid.uuid4())[:8] 
        SEARCH_DATA_CACHE[search_id] = results
        await send_results_page(message, search_id, page=1, is_edit=is_correction)
        return

    # SUGGESTIONS (IMDb + Local Fuzzy)
    suggestions = []
    try:
        movies = ia.search_movie(query)
        for m in movies:
            if m.get('kind') in ['movie', 'tv series']:
                suggestions.append(f"{m.get('title')} ({m.get('year')})" if m.get('year') else m.get('title'))
            if len(suggestions) >= 3: break
    except: pass

    local_titles = list(set([extract_proper_title(f['file_name']) for f in FILES_CACHE]))
    matches = process.extract(query, local_titles, limit=3, scorer=fuzz.WRatio)
    for m, score, idx in matches:
        if score > 55 and m not in suggestions: suggestions.append(m)

    btn = []
    for s in suggestions[:6]:
        cb_search = re.sub(r'\(.*?\)', '', s).strip()
        btn.append([InlineKeyboardButton(s, callback_data=f"sp|{cb_search[:40]}")])
    
    btn.append([InlineKeyboardButton("📝 Request Movie", callback_data=f"req|{query[:30]}")])
    btn.append([InlineKeyboardButton("🚫 CLOSE 🚫", callback_data="close_data")])
    
    text = f"⚠️ **I couldn't find:** `{query}`\nMaybe spelling is wrong.\n\n‼️ **Did you mean?** 👇"
    if is_correction: sent = await message.edit_text(text, reply_markup=InlineKeyboardMarkup(btn))
    else: sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn))
    add_delete_task(sent.chat.id, sent.id, time.time() + SUGGESTION_DELETE_TIME)

async def send_results_page(message, search_id, page=1, is_edit=False):
    results = SEARCH_DATA_CACHE.get(search_id)
    if not results: return
    total = len(results)
    total_pages = math.ceil(total / RESULTS_PER_PAGE)
    start = (page - 1) * RESULTS_PER_PAGE
    current = results[start : start + RESULTS_PER_PAGE]
    
    buttons = []
    for f in current:
        size, name = get_size(f['file_size']), f['file_name'][:30]
        if message.chat.type == enums.ChatType.PRIVATE:
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", callback_data=f"dl|{f['unique_id']}")])
        else:
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", url=f"https://t.me/{BOT_USERNAME}?start=dl_{f['unique_id']}")])

    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{search_id}|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{search_id}|{page+1}"))
    if nav: buttons.append(nav)
    
    text = f"🔍 **Found {total} files**"
    if is_edit: await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        add_delete_task(sent.chat.id, sent.id, time.time() + RESULT_MSG_DELETE_TIME)

# -----------------------------------------------------------------------------
# 6. BOT CLIENT & HANDLERS
# -----------------------------------------------------------------------------
app = Client("BSFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    add_user(message.from_user.id)
    if len(message.command) > 1 and message.command[1].startswith("dl_"):
        uid = message.command[1].split("_")[1]
        for f in FILES_CACHE:
            if f['unique_id'] == uid:
                sent = await client.send_cached_media(message.chat.id, f['file_id'], caption=f"📁 **{f['file_name']}**")
                add_delete_task(message.chat.id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
                return

    buttons = [
        [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("🔎 Inline Search", switch_inline_query_current_chat="")]
    ]
    await message.reply_text(f"👋 Hi **{message.from_user.first_name}**!\nType a movie name to search.", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_handler(client, message):
    users, files = len(get_all_users()), len(FILES_CACHE)
    await message.reply_text(f"📊 **Bot Stats**\n\n📂 Files: `{files}`\n👤 Users: `{users}`")

@app.on_message(filters.command("broadcast") & filters.user(ADMIN_ID))
async def broadcast_handler(client, message):
    if not message.reply_to_message: return await message.reply_text("Reply to a message.")
    users = get_all_users()
    for u in users:
        try: await message.reply_to_message.copy(int(u)); await asyncio.sleep(0.1)
        except: pass
    await message.reply_text("✅ Broadcast Done.")

@app.on_message(filters.command("index") & filters.user(ADMIN_ID))
async def index_channel(client, message):
    if len(message.command) < 2: return await message.reply_text("Usage: `/index @channel`")
    target = message.command[1]
    status = await message.reply_text("⏳ Indexing...")
    new = 0
    try:
        async for msg in client.get_chat_history(target):
            media = msg.document or msg.video
            if media:
                data = {"file_name": getattr(media, "file_name", "Video"), "file_size": media.file_size, "file_id": media.file_id, "unique_id": media.file_unique_id}
                ref = db.reference(f'files/{media.file_unique_id}')
                if not ref.get(): ref.set(data); new += 1
        refresh_cache()
        await status.edit(f"✅ Added {new} files.")
    except Exception as e: await status.edit(f"❌ Error: {e}")

@app.on_message(filters.text & (filters.private | filters.group))
async def search_handler(client, message):
    if not message.text.startswith("/") and not message.via_bot:
        await perform_search(client, message, message.text.strip())

@app.on_callback_query()
async def callback_handler(client, cb):
    data = cb.data.split("|")
    if data[0] == "dl":
        for f in FILES_CACHE:
            if f['unique_id'] == data[1]:
                sent = await client.send_cached_media(cb.message.chat.id, f['file_id'], caption=f"📁 **{f['file_name']}**")
                add_delete_task(cb.message.chat.id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
                await cb.answer(); return
    elif data[0] == "sp":
        await cb.answer(); await perform_search(client, cb.message, data[1], is_correction=True)
    elif data[0] == "page":
        await send_results_page(cb.message, data[1], int(data[2]), is_edit=True); await cb.answer()
    elif data[0] == "req":
        btn = [[InlineKeyboardButton("✅ Mark Uploaded", callback_data=f"done|{cb.from_user.id}|{data[1]}")]]
        await client.send_message(ADMIN_ID, f"📝 **New Request**\nUser: {cb.from_user.first_name}\nMovie: `{data[1]}`", reply_markup=InlineKeyboardMarkup(btn))
        await cb.answer("✅ Request Sent!", show_alert=True); await cb.message.delete()
    elif data[0] == "done":
        try: await client.send_message(int(data[1]), f"🍿 Good News! `{data[2]}` is now available!")
        except: pass
        await cb.answer("Notified!"); await cb.message.edit_text(f"✅ Resolved: {data[2]}")
    elif data[0] == "close_data": await cb.message.delete()

# -----------------------------------------------------------------------------
# 7. STARTUP
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    refresh_cache()
    app.start()
    BOT_USERNAME = app.get_me().username
    print(f"✅ @{BOT_USERNAME} Started")
    loop = asyncio.get_event_loop()
    loop.create_task(check_auto_delete())
    from pyrogram import idle
    idle()
