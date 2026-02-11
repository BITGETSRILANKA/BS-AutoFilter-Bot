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
from collections import defaultdict

# Pyrogram
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultCachedDocument
)

# Firebase
import firebase_admin
from firebase_admin import credentials, db

# Fuzzy Search (Optional fallback)
try:
    from rapidfuzz import process, fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    print("⚠️ RapidFuzz not installed. Fuzzy search disabled.")
    FUZZY_AVAILABLE = False

# 🆕 IMDB MODULE (Cinemagoer) - The "Real Movie DB"
try:
    from imdb import Cinemagoer
    ia = Cinemagoer()
    IMDB_AVAILABLE = True
    print("✅ IMDb Module (Cinemagoer) Loaded Successfully")
except ImportError:
    print("⚠️ Cinemagoer not installed. Run: pip install cinemagoer")
    IMDB_AVAILABLE = False

# CONFIGURATION
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")
PORT = int(os.environ.get('PORT', 8080))

# --- TIMERS (Seconds) ---
FILE_MSG_DELETE_TIME = 120     # 2 Minutes
RESULT_MSG_DELETE_TIME = 600   # 10 Minutes
USER_MSG_DELETE_TIME = 300     # 5 Minutes
SUGGESTION_DELETE_TIME = 300   # 5 Minutes

# LOGGING & FIREBASE SETUP
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

# GLOBAL VARIABLES & CACHE
FILES_CACHE = []
SEARCH_DATA_CACHE = {}
MOVIE_TITLES_CACHE = []  # Cache for extracted movie titles
SUGGESTION_CACHE = {}    # 🆕 Cache for Button IDs (Fixes Error 400)
BOT_USERNAME = ""
RESULTS_PER_PAGE = 10

# DATABASE & HELPER FUNCTIONS
def refresh_cache():
    global FILES_CACHE, MOVIE_TITLES_CACHE
    try:
        ref = db.reference('files')
        snapshot = ref.get()
        if snapshot:
            FILES_CACHE = list(snapshot.values())
            logger.info(f"🚀 Cache Refreshed: {len(FILES_CACHE)} files in RAM")
            # Refresh movie titles cache
            MOVIE_TITLES_CACHE = extract_movie_titles_from_files()
            logger.info(f"📝 Movie titles extracted: {len(MOVIE_TITLES_CACHE)} unique titles")
        else:
            FILES_CACHE = []
            MOVIE_TITLES_CACHE = []
            logger.warning("⚠️ No files found in database")
    except Exception as e:
        logger.error(f"Cache Refresh Error: {e}")

def extract_movie_titles_from_files():
    """Extract unique movie titles from all files in cache"""
    titles_set = set()
    for file in FILES_CACHE:
        filename = file.get('file_name', '')
        title = extract_proper_movie_title(filename)
        if title: titles_set.add(title)
    return sorted(list(titles_set))

def extract_proper_movie_title(text):
    """Extract clean movie title from text"""
    if not text: return None
    text = re.sub(r'\.(mkv|mp4|avi|mov|flv|wmv|webm|m4v|3gp|vob)$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[\s\._-]*(720p|1080p|4k|2160p|hd|fullhd|bluray|webdl|webrip|dvdrip|brrip|hdtv|hdcam|camrip|ts|tc|scr|dvdscr|r5|bdrip)[\s\._-]*', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'[\s\._-]*(x264|x265|h264|h265|aac|ac3|dd5\.1|dts)[\s\._-]*', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'[._-]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    year_match = re.search(r'\((\d{4})\)', text)
    year = year_match.group(1) if year_match else None
    text_without_year = re.sub(r'\s*\(\d{4}\)', '', text).strip()
    patterns = [
        r'^([A-Za-z0-9\s\.]+?)(?:\s*\(\d{4}\)|\s+\d{4}|\s+season|\s+episode|\s+s\d+e\d+|\s+part|\s+vol\.|\s+cd\d+|$)',
        r'^([A-Za-z0-9\s\.\-]+?)(?:\s*-\s*\d{4}|$)',
    ]
    for pattern in patterns:
        match = re.match(pattern, text_without_year, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r'\.', ' ', title)
            title = re.sub(r'\s+', ' ', title).strip()
            if year and title: title = f"{title} ({year})"
            if title and len(title) > 2 and len(title.split()) <= 10: return title.title()
    return None

def add_file_to_db(file_data):
    for f in FILES_CACHE:
        if f['unique_id'] == file_data['unique_id']: return False
    try:
        ref = db.reference(f'files/{file_data["unique_id"]}')
        ref.set(file_data)
        FILES_CACHE.append(file_data)
        global MOVIE_TITLES_CACHE
        MOVIE_TITLES_CACHE = extract_movie_titles_from_files()
        logger.info(f"✅ Added file: {file_data['file_name'][:50]}")
        return True
    except Exception as e:
        logger.error(f"DB Write Error: {e}")
        return False

def delete_file_from_db(unique_id):
    global FILES_CACHE, MOVIE_TITLES_CACHE
    try:
        db.reference(f'files/{unique_id}').delete()
        FILES_CACHE = [f for f in FILES_CACHE if f['unique_id'] != unique_id]
        MOVIE_TITLES_CACHE = extract_movie_titles_from_files()
        logger.info(f"🗑️ Deleted file: {unique_id}")
        return True
    except Exception as e:
        logger.error(f"DB Delete Error: {e}")
        return False

def get_file_by_id(unique_id):
    for file in FILES_CACHE:
        if file['unique_id'] == unique_id: return file
    return None

def add_user(user_id):
    if user_id < 0: return
    try:
        ref = db.reference(f'users/{user_id}')
        if not ref.get(): ref.set({"active": True})
    except: pass

def get_all_users():
    try:
        snap = db.reference('users').get()
        return list(snap.keys()) if snap else []
    except: return []

# --- Auto Delete Logic ---
def add_delete_task(chat_id, message_id, delete_time):
    try:
        task_id = f"{chat_id}_{message_id}"
        db.reference(f'delete_queue/{task_id}').set({
            "chat_id": chat_id, "message_id": message_id, "delete_time": delete_time
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

def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

def clean_text(text):
    if not text: return ""
    return re.sub(r'[\W_]+', ' ', text).lower().strip()

def get_system_stats():
    process = psutil.Process(os.getpid())
    return get_size(process.memory_info().rss)

# BOT CLIENT
app = Client("BSFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# HTTP SERVER
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.wfile.write(b"Bot is Running")
def run_http_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

# BACKGROUND TASKS
async def check_auto_delete():
    while True:
        try:
            tasks = get_due_delete_tasks()
            for task in tasks:
                try: await app.delete_messages(task['chat_id'], task['message_id'])
                except: pass
                remove_delete_task(task['key'])
        except: pass
        await asyncio.sleep(10)

# ==============================================================================
# 🆕 IMDB SUGGESTION LOGIC
# ==============================================================================
async def get_imdb_suggestions(query):
    """Fetches real movie names from IMDb (Prevents 400 error logic)"""
    if not IMDB_AVAILABLE or not query: return []
    
    try:
        loop = asyncio.get_running_loop()
        # IMDb search is blocking, run in executor
        movies = await loop.run_in_executor(None, ia.search_movie, query)
        
        results = []
        for m in movies[:6]: # Limit to top 6 results
            title = m.get('title')
            year = m.get('year')
            if title:
                full_name = f"{title} ({year})" if year else title
                results.append(full_name)
        return results
    except Exception as e:
        logger.error(f"IMDb Error: {e}")
        return []

# ==============================================================================
# 🔍 MAIN SEARCH LOGIC
# ==============================================================================
async def perform_search(client, message, query, is_correction=False):
    if not query or len(query) < 2:
        return await message.reply_text("❌ Query too short.")
    
    # Auto-delete User Input
    if not is_correction:
        add_delete_task(message.chat.id, message.id, time.time() + USER_MSG_DELETE_TIME)
    
    clean_query = clean_text(query)
    raw_query = query.lower().split()
    results = []
    
    # 1. SEARCH INTERNAL DB
    for file in FILES_CACHE:
        fname = clean_text(file.get('file_name', ''))
        capt = clean_text(file.get('caption', ''))
        
        if clean_query in fname or clean_query in capt:
            results.append(file)
            continue
        if raw_query and all(w in file.get('file_name', '').lower() for w in raw_query):
            results.append(file)
            continue
        # Check against extracted title
        extracted_title = extract_proper_movie_title(file.get('file_name', ''))
        if extracted_title and clean_query in extracted_title.lower():
            results.append(file)
    
    # 2. IF RESULTS FOUND -> SHOW FILES
    if results:
        search_id = str(uuid.uuid4())[:8]
        SEARCH_DATA_CACHE[search_id] = results
        await send_results_page(message, search_id, page=1, is_edit=is_correction)
        return
    
    # 3. IF NO RESULTS -> GET IMDB SUGGESTIONS
    # Send "Checking..." status if it's a correction click
    if is_correction:
        await message.edit_text(f"🔎 Checking IMDb for '{query}'...")
    
    suggestions = await get_imdb_suggestions(query)
    
    if suggestions:
        buttons = []
        for sugg in suggestions:
            # 🆕 THE FIX FOR ERROR 400
            # Generate Short ID
            short_id = str(uuid.uuid4())[:8]
            # Store Full Name in Cache
            SUGGESTION_CACHE[short_id] = sugg
            # Send Short ID in Button
            buttons.append([InlineKeyboardButton(f"🎬 {sugg}", callback_data=f"suggest|{short_id}")])
        
        buttons.append([InlineKeyboardButton("❌ Close", callback_data="close_data")])
        
        text = (
            f"🤔 **No exact matches found for:** `{query}`\n\n"
            f"**Did you mean any of these?** 👇\n"
            f"_Select a button to search again with the correct name._"
        )
        
        if is_correction:
            sent_msg = await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            sent_msg = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        
        add_delete_task(sent_msg.chat.id, sent_msg.id, time.time() + SUGGESTION_DELETE_TIME)
    
    else:
        # No results anywhere
        btn = [[InlineKeyboardButton(f"📝 Request '{query[:15]}...'", callback_data=f"req|{query[:20]}")]]
        text = f"🚫 **No results found for:** `{query}`\n\nCheck spelling or request this content."
        
        if is_correction:
            sent_msg = await message.edit_text(text, reply_markup=InlineKeyboardMarkup(btn))
        else:
            sent_msg = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn))
        
        add_delete_task(sent_msg.chat.id, sent_msg.id, time.time() + SUGGESTION_DELETE_TIME)

# HANDLERS
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    add_user(message.from_user.id)
    if len(message.command) > 1 and message.command[1].startswith("dl_"):
        unique_id = message.command[1].split("_")[1]
        await send_file_to_user(client, message.chat.id, unique_id)
        return
    
    buttons = [
        [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("🔎 Inline Search", switch_inline_query_current_chat="")]
    ]
    await message.reply_text(
        f"👋 Hi {message.from_user.first_name}!\nI am a Pro Auto-Filter Bot.\nType a movie name to search.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_handler(client, message):
    msg = await message.reply_text("⏳ Calculating...")
    files = len(FILES_CACHE)
    users = len(get_all_users())
    ram = get_system_stats()
    await msg.edit(f"📊 Bot Stats\n\n📂 Files: {files}\n👤 Users: {users}\n💾 RAM: {ram}")

@app.on_message(filters.command("delete") & filters.user(ADMIN_ID))
async def delete_handler(client, message):
    unique_id = None
    if message.reply_to_message:
        media = message.reply_to_message.document or message.reply_to_message.video
        if media: unique_id = media.file_unique_id
    elif len(message.command) > 1:
        unique_id = message.command[1]
    
    if not unique_id: return await message.reply_text("❌ Reply to a file or provide Unique ID.")
    if delete_file_from_db(unique_id): await message.reply_text(f"🗑️ File {unique_id} deleted.")
    else: await message.reply_text("❌ File not found.")

@app.on_message(filters.command("index") & filters.user(ADMIN_ID))
async def index_channel(client, message):
    if len(message.command) < 2: return await message.reply_text("❌ Usage: /index https://t.me/channel")
    target = message.command[1]
    status_msg = await message.reply_text(f"⏳ Connecting to {target}...")
    try:
        chat = await client.get_chat(target)
        chat_id = chat.id
        await status_msg.edit(f"✅ Connected to {chat.title}\n⏳ Starting index...")
    except Exception as e: return await status_msg.edit(f"❌ Error: {e}")
    
    count = 0
    new_files = 0
    try:
        async for msg in client.get_chat_history(chat_id):
            if msg.document or msg.video:
                media = msg.document or msg.video
                filename = getattr(media, "file_name", None) or "Unknown"
                if (not filename or filename == "Unknown" or filename.startswith("Video_")) and msg.caption:
                    caption_lines = msg.caption.split('\n')
                    if caption_lines: filename = caption_lines[0].strip()
                data = {
                    "file_name": filename,
                    "file_size": media.file_size,
                    "file_id": media.file_id,
                    "unique_id": media.file_unique_id,
                    "caption": msg.caption or filename
                }
                if add_file_to_db(data): new_files += 1
                count += 1
                if count % 50 == 0: await status_msg.edit(f"🔄 Scanned: {count}\n✅ Added: {new_files}")
        await status_msg.edit(f"✅ Indexing Complete\n\n📄 Scanned: {count}\n📂 Added: {new_files}")
    except Exception as e: await status_msg.edit(f"❌ Indexing Stopped: {e}")

@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_new_post(client, message):
    media = message.document or message.video
    if not media: return
    filename = getattr(media, "file_name", None) or "Unknown"
    if (not filename or filename == "Unknown" or filename.startswith("Video_")) and message.caption:
        caption_lines = message.caption.split('\n')
        if caption_lines: filename = caption_lines[0].strip()
    data = {
        "file_name": filename,
        "file_size": media.file_size,
        "file_id": media.file_id,
        "unique_id": media.file_unique_id,
        "caption": message.caption or filename
    }
    add_file_to_db(data)

@app.on_message(filters.text & (filters.private | filters.group))
async def search_handler(client, message):
    if message.text.startswith("/") or message.via_bot: return
    query = message.text.strip()
    if len(query) < 2: return await message.reply_text("❌ Query too short.")
    status_msg = await message.reply_text(f"🔍 Searching: `{query}`...")
    try:
        await perform_search(client, message, query, is_correction=False)
    except Exception as e:
        logger.error(f"Search error: {e}")
    try: await status_msg.delete()
    except: pass

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
        fname = clean_text(file.get('file_name', ''))
        if clean_q in fname or (raw_q and all(w in file.get('file_name', '').lower() for w in raw_q)):
            count += 1
            size = get_size(file['file_size'])
            results.append(InlineQueryResultCachedDocument(
                id=file['unique_id'], title=file['file_name'][:50], document_file_id=file['file_id'],
                description=f"Size: {size}", caption=f"📁 {file['file_name']}\n📊 Size: {size}\n\n🔗 via @{BOT_USERNAME}"
            ))
    await query.answer(results, cache_time=10)

async def send_file_to_user(client, chat_id, unique_id):
    file_data = get_file_by_id(unique_id)
    if not file_data: return await client.send_message(chat_id, "❌ File removed or not found.")
    caption = f"📁 {file_data['file_name']}\n📊 Size: {get_size(file_data['file_size'])}\n\n⏳ This message will be deleted in 2 minutes."
    try:
        sent = await client.send_cached_media(chat_id=chat_id, file_id=file_data['file_id'], caption=caption)
        add_delete_task(chat_id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
    except Exception as e: await client.send_message(chat_id, f"❌ Error sending file: {e}")

async def send_results_page(message, search_id, page=1, is_edit=False):
    results = SEARCH_DATA_CACHE.get(search_id)
    if not results: return await message.edit_text("⚠️ Search expired.") if is_edit else None
    total = len(results)
    total_pages = math.ceil(total / RESULTS_PER_PAGE)
    start = (page - 1) * RESULTS_PER_PAGE
    current = results[start:start + RESULTS_PER_PAGE]
    
    buttons = []
    for file in current:
        name = file['file_name'][:27] + "..." if len(file['file_name']) > 30 else file['file_name']
        size = get_size(file['file_size'])
        if message.chat.type == enums.ChatType.PRIVATE:
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", callback_data=f"dl|{file['unique_id']}")])
        else:
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", url=url)])
    
    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page|{search_id}|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page|{search_id}|{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close_data")])
    
    text = f"🔍 **Found {total} files**\n📄 Page {page}/{total_pages}"
    if is_edit: await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else: 
        sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        add_delete_task(sent.chat.id, sent.id, time.time() + RESULT_MSG_DELETE_TIME)

@app.on_callback_query()
async def callback_handler(client, cb):
    data = cb.data.split("|")
    
    if data[0] == "dl":
        await cb.answer("📥 Downloading file...")
        await send_file_to_user(client, cb.message.chat.id, data[1])
    elif data[0] == "page":
        await send_results_page(cb.message, data[1], int(data[2]), is_edit=True)
        await cb.answer()
    elif data[0] == "req":
        await client.send_message(ADMIN_ID, f"📝 Request: {data[1]} from {cb.from_user.mention}")
        await cb.answer("✅ Request Sent!", show_alert=True)
        await cb.message.delete()
    
    elif data[0] == "suggest":
        # 🆕 FIXED SUGGESTION HANDLER
        short_id = data[1]
        
        # 1. Retrieve Real Name from Cache
        correct_title = SUGGESTION_CACHE.get(short_id)
        
        if correct_title:
            await cb.answer(f"🔍 Searching: {correct_title}")
            await cb.message.edit_text(f"🔍 Searching: `{correct_title}`...")
            # 2. Re-run search with correct name
            await perform_search(client, cb.message, correct_title, is_correction=True)
        else:
            await cb.answer("⚠️ Search expired. Please type again.", show_alert=True)
            
    elif data[0] == "close_data":
        await cb.message.delete()
    elif data[0] == "noop":
        await cb.answer()

@app.on_message(filters.command("broadcast") & filters.user(ADMIN_ID))
async def broadcast_handler(client, message):
    if not message.reply_to_message: return await message.reply_text("❌ Reply to a message.")
    users = get_all_users()
    status = await message.reply_text(f"📤 Broadcasting to {len(users)} users...")
    success = 0
    for user_id in users:
        try:
            await message.reply_to_message.copy(int(user_id))
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    await status.edit(f"✅ Broadcast Sent to {success} users.")

# MAIN EXECUTION
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    refresh_cache()
    print("🤖 Bot Starting...")
    app.start()
    me = app.get_me()
    BOT_USERNAME = me.username
    loop = asyncio.get_event_loop()
    loop.create_task(check_auto_delete())
    print(f"✅ Bot Started as @{BOT_USERNAME}")
    from pyrogram import idle
    idle()
    app.stop()
