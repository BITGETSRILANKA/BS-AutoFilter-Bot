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

# Fuzzy Search
try:
    from rapidfuzz import process, fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    print("⚠️ RapidFuzz not installed. Fuzzy search disabled.")
    FUZZY_AVAILABLE = False

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

# --- TIMERS (Seconds) ---
FILE_MSG_DELETE_TIME = 120     # 2 Minutes
RESULT_MSG_DELETE_TIME = 600   # 10 Minutes
USER_MSG_DELETE_TIME = 300     # 5 Minutes
SUGGESTION_DELETE_TIME = 300   # 5 Minutes

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
RESULTS_PER_PAGE = 10

# -----------------------------------------------------------------------------
# 4. DATABASE & HELPER FUNCTIONS
# -----------------------------------------------------------------------------
def refresh_cache():
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
    for f in FILES_CACHE:
        if f['unique_id'] == file_data['unique_id']:
            return False
    try:
        ref = db.reference(f'files/{file_data["unique_id"]}')
        ref.set(file_data)
        FILES_CACHE.append(file_data) 
        return True
    except Exception as e:
        logger.error(f"DB Write Error: {e}")
        return False

def delete_file_from_db(unique_id):
    global FILES_CACHE
    try:
        db.reference(f'files/{unique_id}').delete()
        FILES_CACHE = [f for f in FILES_CACHE if f['unique_id'] != unique_id]
        return True
    except Exception as e:
        logger.error(f"DB Delete Error: {e}")
        return False

def get_file_by_id(unique_id):
    for file in FILES_CACHE:
        if file['unique_id'] == unique_id:
            return file
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

def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

def clean_text(text):
    return re.sub(r'[\W_]+', ' ', text).lower().strip()

# --- FIXED TITLE CLEANER ---
def extract_proper_title(text):
    """
    Cleans filenames to just the Movie/Series Name.
    Fixes: S01, E01, Season 1, Year, etc.
    """
    # 1. Replace special chars with space
    text = re.sub(r'[\.\_\[\]\(\)\-]', ' ', text)
    
    # 2. Regex to find the junk (Case Insensitive)
    # \b(?: ... )\b ensures it matches whole words/codes
    # s\d+ matches S1, S01, S100
    junk_pattern = r'(?i)\b(?:s\d+|e\d+|season|episode|\d{4}|720p|1080p|480p|4k|bluray|web-dl|dvdrip|mkv|mp4|avi|hindi|eng|dual)\b'
    
    match = re.search(junk_pattern, text)
    if match:
        # Cut off text before the first junk match
        text = text[:match.start()]
    
    # Clean up whitespace
    return text.strip().title()

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

# -----------------------------------------------------------------------------
# 6. BACKGROUND TASKS
# -----------------------------------------------------------------------------
async def check_auto_delete():
    while True:
        try:
            tasks = get_due_delete_tasks()
            for task in tasks:
                try:
                    await app.delete_messages(task['chat_id'], task['message_id'])
                except Exception: pass
                remove_delete_task(task['key'])
        except Exception: pass
        await asyncio.sleep(10)

# -----------------------------------------------------------------------------
# 7. BOT HANDLERS
# -----------------------------------------------------------------------------

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
        f"👋 Hi **{message.from_user.first_name}**!\n"
        "I am a Pro Auto-Filter Bot.\n"
        "Type a movie name to search.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_handler(client, message):
    msg = await message.reply_text("⏳ Calculating...")
    files = len(FILES_CACHE)
    users = len(get_all_users())
    ram = get_system_stats()
    await msg.edit(f"📊 **Bot Stats**\n\n📂 Files: `{files}`\n👤 Users: `{users}`\n💾 RAM: `{ram}`")

@app.on_message(filters.command("delete") & filters.user(ADMIN_ID))
async def delete_handler(client, message):
    unique_id = None
    if message.reply_to_message:
        media = message.reply_to_message.document or message.reply_to_message.video
        if media: unique_id = media.file_unique_id
    elif len(message.command) > 1:
        unique_id = message.command[1]
        
    if not unique_id:
        return await message.reply_text("❌ Reply to a file or provide Unique ID.")
        
    if delete_file_from_db(unique_id):
        await message.reply_text(f"🗑️ File `{unique_id}` deleted.")
    else:
        await message.reply_text("❌ File not found.")

@app.on_message(filters.command("index") & filters.user(ADMIN_ID))
async def index_channel(client, message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Usage: `/index https://t.me/channel`")
    
    target = message.command[1]
    status_msg = await message.reply_text(f"⏳ Connecting to {target}...")
    try:
        chat = await client.get_chat(target)
        chat_id = chat.id
    except Exception as e:
        return await status_msg.edit(f"❌ Error: {e}")

    count = 0
    new_files = 0
    try:
        async for msg in client.get_chat_history(chat_id):
            media = msg.document or msg.video
            if media:
                filename = getattr(media, "file_name", None) or "Unknown"
                if (not filename or filename == "Unknown" or filename.startswith("Video_")) and msg.caption:
                    filename = msg.caption.splitlines()[0]
                data = {
                    "file_name": filename,
                    "file_size": media.file_size,
                    "file_id": media.file_id,
                    "unique_id": media.file_unique_id,
                    "caption": msg.caption or filename 
                }
                if add_file_to_db(data):
                    new_files += 1
            count += 1
            if count % 200 == 0:
                await status_msg.edit(f"🔄 Scanned: {count}\n✅ Added: {new_files}")
        await status_msg.edit(f"✅ **Indexing Complete**\n\n📄 Scanned: {count}\n📂 Added: {new_files}")
    except Exception as e:
        await status_msg.edit(f"❌ Indexing Stopped: {e}")

@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_new_post(client, message):
    media = message.document or message.video
    if not media: return
    filename = getattr(media, "file_name", None) or "Unknown"
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

# ==============================================================================
# MAIN SEARCH LOGIC
# ==============================================================================

async def perform_search(client, message, query, is_correction=False):
    # Auto-delete User Input after 5 Minutes
    add_delete_task(message.chat.id, message.id, time.time() + USER_MSG_DELETE_TIME)
    
    clean_query = clean_text(query)
    raw_query = query.lower().split()
    results = []
    
    # 1. Exact & Split Match
    for file in FILES_CACHE:
        fname = clean_text(file.get('file_name', ''))
        capt = clean_text(file.get('caption', ''))
        
        if clean_query in fname or clean_query in capt:
            results.append(file)
            continue
        if all(w in file.get('file_name', '').lower() for w in raw_query):
            results.append(file)

    # 2. IF RESULTS FOUND -> Show File List
    if results:
        USER_SEARCH_CACHE[message.from_user.id] = results
        await send_results_page(message, user_id=message.from_user.id, page=1, is_edit=is_correction)
        return

    # 3. SUGGESTIONS
    suggestions = []
    if FUZZY_AVAILABLE:
        # A. EXTRACT CLEAN TITLES (SQUASH DUPLICATES)
        unique_titles = set()
        for f in FILES_CACHE:
            clean_t = extract_proper_title(f.get('file_name', ''))
            if len(clean_t) > 2:
                unique_titles.add(clean_t)
        
        choices = list(unique_titles)
        
        # B. FUZZY MATCH
        matches = process.extract(clean_query, choices, limit=10, scorer=fuzz.WRatio)
        
        seen = set()
        for match_name, score, index in matches:
            if score > 50:
                if match_name not in seen:
                    suggestions.append(match_name)
                    seen.add(match_name)
            if len(suggestions) >= 6: break 

    # 4. SEND RESPONSE
    if suggestions:
        btn = []
        for sugg in suggestions:
            # Button Text = Clean Name (e.g. "Stranger Things")
            cb_data = f"sp|{sugg[:40]}"
            btn.append([InlineKeyboardButton(f"{sugg}", callback_data=cb_data)])
        
        btn.append([InlineKeyboardButton("🚫 CLOSE 🚫", callback_data="close_data")])
        
        text = (
            f"⚠️ **I am not able to search with your given query.**\n"
            f"Maybe your spelling is wrong.\n\n"
            f"‼️ **Is there any of this?** 👇"
        )
        
        if is_correction:
            sent_msg = await message.edit_text(text, reply_markup=InlineKeyboardMarkup(btn))
        else:
            sent_msg = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn))
        
        # Auto-delete Suggestion after 5 Minutes
        add_delete_task(message.chat.id, sent_msg.id, time.time() + SUGGESTION_DELETE_TIME)

    else:
        # No results, No suggestions
        btn = [[InlineKeyboardButton(f"🙋‍♂️ Request {query[:15]}...", callback_data=f"req|{query[:20]}")]]
        text = f"🚫 **No movie found for:** `{query}`\nCheck spelling or request it."
        
        if is_correction:
            sent_msg = await message.edit_text(text, reply_markup=InlineKeyboardMarkup(btn))
        else:
            sent_msg = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn))
            
        # Auto-delete "Not Found" Msg after 5 Minutes
        add_delete_task(message.chat.id, sent_msg.id, time.time() + SUGGESTION_DELETE_TIME)

@app.on_message(filters.text & (filters.private | filters.group))
async def search_handler(client, message):
    if message.text.startswith("/") or message.via_bot: return
    query = message.text.strip()
    if len(query) < 2: return
    
    await perform_search(client, message, query, is_correction=False)

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
        if clean_q in fname or all(w in file.get('file_name', '').lower() for w in raw_q):
            count += 1
            size = get_size(file['file_size'])
            results.append(
                InlineQueryResultCachedDocument(
                    id=file['unique_id'],
                    title=file['file_name'],
                    document_file_id=file['file_id'],
                    description=f"Size: {size}",
                    caption=f"📁 **{file['file_name']}**\n📊 Size: {size}"
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
        f"⏳ **Deleted in 2 mins.**"
    )
    try:
        sent = await client.send_cached_media(
            chat_id=chat_id,
            file_id=file_data['file_id'],
            caption=caption
        )
        add_delete_task(chat_id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
    except Exception as e:
        logger.error(f"Send Error: {e}")

async def send_results_page(message, user_id, page=1, is_edit=False):
    results = USER_SEARCH_CACHE.get(user_id)
    if not results: 
        if is_edit: await message.edit_text("⚠️ Expired. Search again.")
        return
    
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
    
    try:
        if is_edit:
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
            add_delete_task(sent.chat.id, sent.id, time.time() + RESULT_MSG_DELETE_TIME)
    except Exception as e:
        logger.error(f"Display Error: {e}")

@app.on_callback_query()
async def callback_handler(client, cb):
    data = cb.data.split("|")
    
    if data[0] == "dl":
        await cb.answer()
        await send_file_to_user(client, cb.message.chat.id, data[1])
    
    elif data[0] == "page":
        await send_results_page(cb.message, user_id=cb.from_user.id, page=int(data[1]), is_edit=True)
    
    elif data[0] == "req":
        query = data[1]
        user = cb.from_user
        text = f"📝 **New Request**\n\n👤 User: {user.mention} (`{user.id}`)\n🎬 Movie: `{query}`"
        try:
            await client.send_message(ADMIN_ID, text)
            await cb.answer("✅ Request Sent to Admin!", show_alert=True)
            await cb.message.delete()
        except:
            await cb.answer("❌ Failed to send request.")

    elif data[0] == "sp":
        correct_query = data[1]
        await cb.answer()
        await perform_search(client, cb.message, correct_query, is_correction=True)

    elif data[0] == "close_data":
        await cb.message.delete()

    elif data[0] == "noop":
        await cb.answer()

# -----------------------------------------------------------------------------
# 8. MAIN EXECUTION
# -----------------------------------------------------------------------------
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
