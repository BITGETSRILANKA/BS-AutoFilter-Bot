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

from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultCachedDocument,
    CallbackQuery,
    Message
)
from pyrogram.errors import UserIsBlocked, PeerIdInvalid

import firebase_admin
from firebase_admin import credentials, db

# --- FUZZY SEARCH SETUP ---
try:
    from rapidfuzz import process, fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    print("⚠️ RapidFuzz not installed. Fuzzy search disabled.")
    FUZZY_AVAILABLE = False

# -----------------------------------------------------------------------------
# 1. CONFIGURATION
# -----------------------------------------------------------------------------

API_ID = int(os.environ.get("API_ID", 12345))       # Replace with your API ID
API_HASH = os.environ.get("API_HASH", "your_hash") # Replace with your API Hash
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", -100123456789)) # Channel to Index
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))         # Your ID
DB_URL = os.environ.get("DB_URL", "")                         # Firebase DB URL
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")             # Firebase JSON Content
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
            logger.warning("❌ FIREBASE_KEY missing. Database will fail.")
    except Exception as e:
        logger.error(f"❌ Firebase Init Error: {e}")

# -----------------------------------------------------------------------------
# 3. GLOBAL VARIABLES & CACHE
# -----------------------------------------------------------------------------

FILES_CACHE = []
SEARCH_DATA_CACHE = {}
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

def get_system_stats():
    process = psutil.Process(os.getpid())
    return get_size(process.memory_info().rss)

# --- 🚀 SMART FILENAME PARSER 🚀 ---

def parse_filename(text):
    """
    Cleans filename: removes Mep/Fm, extract Year, removes quality tags.
    Returns: (Clean Name, Year or '')
    """
    if not text: return "Unknown", ""

    # 1. Remove [Tags] and {Tags}
    text = re.sub(r'[\[\{].*?[\]\}]', '', text)

    # 2. Remove Specific Prefixes (Mep, Fm, Video_, etc) - Case Insensitive
    # ^ means start of string, \s* means optional space
    text = re.sub(r'^(Mep|Fm|Video_|Audio_|File_)\s*', '', text, flags=re.IGNORECASE)

    # 3. Replace separators (., _, -) with space
    text = re.sub(r'[\.\_\-]', ' ', text)

    # 4. Extract Year: looks for (1999) or 1999 surrounded by spaces
    year_match = re.search(r'(?:\(|^|\s)(19\d{2}|20\d{2})(?:\)|$|\s)', text)
    year = year_match.group(1) if year_match else ""

    # 5. Remove junk keywords (Quality, Ext, Codec)
    # This regex stops the name when it finds these words
    stop_words = r'(?i)\b(s\d+|e\d+|season|episode|720p|1080p|480p|4k|hdcam|hdrip|bluray|web-dl|mkv|mp4|avi|hevc|x264|hindi|eng|tamil|dual)\b'
    match = re.search(stop_words, text)
    if match:
        text = text[:match.start()]

    # 6. Remove Year from the name text (since we extracted it separately)
    if year:
        text = text.replace(year, "")

    # 7. Final Clean
    text = re.sub(r'\s+', ' ', text).strip().title() # Remove double spaces
    
    return text, year

def clean_text(text):
    # Basic cleaner for comparison
    return re.sub(r'[\W_]+', ' ', text).lower().strip()

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
        "I am a Smart Filter Bot.\n"
        "I can understand movie names like `Mep Kaavalan` as just `Kaavalan`.\n\n"
        "Type a movie name to search.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_handler(client, message):
    msg = await message.reply_text("⏳ Calculating...")
    files = len(FILES_CACHE)
    ram = get_system_stats()
    await msg.edit(f"📊 Bot Stats\n\n📂 Files: {files}\n💾 RAM: {ram}")

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
        return await message.reply_text("❌ Usage: /index https://t.me/channel")
    
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
                
                # We store the RAW filename, but we will Parse it during Search
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
# MAIN SEARCH LOGIC (IMPROVED)
# ==============================================================================

async def perform_search(client, message, query, is_correction=False):
    # Auto-delete User Input after 5 Minutes
    add_delete_task(message.chat.id, message.id, time.time() + USER_MSG_DELETE_TIME)
    
    user_query_clean = clean_text(query)
    results = []

    # 1. SEARCH LOGIC (Using parsed names)
    for file in FILES_CACHE:
        raw_name = file.get('file_name', '')
        # Parse the name dynamically (removes Mep, Fm, adds Year support)
        parsed_name, parsed_year = parse_filename(raw_name)
        
        # Clean for comparison
        clean_parsed = clean_text(parsed_name)
        
        # Logic: 
        # A. If user query is inside the Parsed Name (Exact substring)
        # B. If user query matches raw filename (Fallback)
        if user_query_clean in clean_parsed or user_query_clean in clean_text(raw_name):
            file['parsed_name'] = f"{parsed_name} ({parsed_year})" if parsed_year else parsed_name
            results.append(file)
            continue
            
    # 2. IF RESULTS FOUND -> Show File List
    if results:
        search_id = str(uuid.uuid4())[:8] 
        SEARCH_DATA_CACHE[search_id] = results
        await send_results_page(message, search_id, page=1, is_edit=is_correction)
        return

    # 3. SUGGESTIONS (Using RapidFuzz on PARSED names)
    suggestions = []
    if FUZZY_AVAILABLE:
        # Create a dictionary of {ParsedName: RawName} to map back later if needed
        # But for suggestions we just want titles
        unique_titles = set()
        for f in FILES_CACHE:
            p_name, _ = parse_filename(f.get('file_name', ''))
            if len(p_name) > 2:
                unique_titles.add(p_name)
        
        choices = list(unique_titles)
        # Find close matches to the user's query
        matches = process.extract(user_query_clean, choices, limit=5, scorer=fuzz.WRatio)
        
        for match_name, score, index in matches:
            if score > 60:
                suggestions.append(match_name)

    # 4. SEND RESPONSE (Suggestions or Request Button)
    if suggestions:
        btn = []
        for sugg in suggestions:
            cb_data = f"sp|{sugg[:40]}"
            btn.append([InlineKeyboardButton(f"🎬 {sugg}", callback_data=cb_data)])
        
        btn.append([InlineKeyboardButton("🚫 CLOSE", callback_data="close_data")])
        
        text = (
            f"❌ Couldn't find: **{query}**\n"
            f"Did you mean one of these? 👇"
        )
        markup = InlineKeyboardMarkup(btn)
    else:
        # NO RESULTS & NO SUGGESTIONS -> REQUEST BUTTON
        btn = [[InlineKeyboardButton("📝 Request from Admin", callback_data=f"req|{query[:20]}")]]
        text = f"❌ **No Movie Found:** `{query}`\n\nNot in our database? Request it now!"
        markup = InlineKeyboardMarkup(btn)
    
    if is_correction:
        sent_msg = await message.edit_text(text, reply_markup=markup)
    else:
        sent_msg = await message.reply_text(text, reply_markup=markup)
        
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
    results = []
    count = 0
    
    for file in FILES_CACHE:
        if count >= 20: break
        
        p_name, p_year = parse_filename(file.get('file_name', ''))
        clean_p = clean_text(p_name)
        
        if clean_q in clean_p:
            count += 1
            size = get_size(file['file_size'])
            display_title = f"{p_name} ({p_year})" if p_year else p_name
            
            results.append(
                InlineQueryResultCachedDocument(
                    id=file['unique_id'],
                    title=display_title,
                    document_file_id=file['file_id'],
                    description=f"Size: {size}",
                    caption=f"📁 **{display_title}**\n📊 Size: {size}"
                )
            )
    await query.answer(results, cache_time=10)

async def send_file_to_user(client, chat_id, unique_id):
    file_data = get_file_by_id(unique_id)
    if not file_data:
        return await client.send_message(chat_id, "❌ File removed.")
    
    # Display the Clean Name to the user, not "Mep Kaavalan"
    p_name, p_year = parse_filename(file_data['file_name'])
    display_name = f"{p_name} {p_year}".strip()

    caption = (
        f"📁 **{display_name}**\n"
        f"Filename: `{file_data['file_name']}`\n"
        f"📊 Size: {get_size(file_data['file_size'])}\n\n"
        f"⏳ **This message deletes in 2 mins.**"
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

async def send_results_page(message, search_id, page=1, is_edit=False):
    results = SEARCH_DATA_CACHE.get(search_id)
    if not results:
        if is_edit: await message.edit_text("⚠️ Expired. Search again.")
        return

    total = len(results)
    total_pages = math.ceil(total / RESULTS_PER_PAGE)
    start = (page - 1) * RESULTS_PER_PAGE
    current = results[start : start + RESULTS_PER_PAGE]

    buttons = []
    for file in current:
        # Use the parsed name we stored in search loop, or parse now
        display_name = file.get('parsed_name', file['file_name'])
        size = get_size(file['file_size'])
        
        if message.chat.type == enums.ChatType.PRIVATE:
            cb_data = f"dl|{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {display_name}", callback_data=cb_data)])
        else:
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {display_name}", url=url)])

    nav = []
    if page > 1: 
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{search_id}|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: 
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{search_id}|{page+1}"))
    
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

# ==============================================================================
# CALLBACK HANDLERS (Admin Notify, Request, etc)
# ==============================================================================

@app.on_callback_query()
async def callback_handler(client, cb):
    data = cb.data.split("|")
    
    if data[0] == "dl":
        await cb.answer()
        await send_file_to_user(client, cb.message.chat.id, data[1])

    elif data[0] == "page":
        search_id = data[1]
        page_num = int(data[2])
        await send_results_page(cb.message, search_id, page=page_num, is_edit=True)

    # --- 1. USER REQUESTS MOVIE ---
    elif data[0] == "req":
        query = data[1]
        user = cb.from_user
        
        # Send Message to ADMIN with "Notify User" button
        # Format: notify|USER_ID
        notify_btn = [[InlineKeyboardButton("🔔 Notify User (Added)", callback_data=f"notify|{user.id}")]]
        
        admin_text = (
            f"📨 **New Movie Request!**\n\n"
            f"🎬 **Movie:** {query}\n"
            f"👤 **User:** {user.mention} (`{user.id}`)"
        )
        
        try:
            await client.send_message(ADMIN_ID, admin_text, reply_markup=InlineKeyboardMarkup(notify_btn))
            await cb.answer("✅ Request Sent to Admin!", show_alert=True)
            await cb.message.edit_text(f"✅ Your request for **{query}** has been sent to the Admin.")
        except Exception as e:
            await cb.answer("❌ Failed to send request.", show_alert=True)
            logger.error(f"Req Error: {e}")

    # --- 2. ADMIN NOTIFIES USER ---
    elif data[0] == "notify":
        # Only Admin can use this
        if cb.from_user.id != ADMIN_ID:
            return await cb.answer("❌ You are not admin.", show_alert=True)
            
        user_id = int(data[1])
        
        # Extract movie name from the Admin's message
        try:
            original_msg = cb.message.text
            # Simple string parsing assuming the format above
            movie_name = original_msg.split("Movie:**")[1].split("\n")[0].strip()
        except:
            movie_name = "your requested movie"

        try:
            # Send alert to user
            await client.send_message(
                chat_id=user_id,
                text=f"✅ **Request Fulfilled!**\n\nThe movie **{movie_name}** has been added.\nCheck it out now!"
            )
            
            # Update Admin Msg
            await cb.answer("✅ User Notified!", show_alert=False)
            await cb.message.edit_reply_markup(reply_markup=None) # Remove button
            await cb.message.reply_text(f"✅ Notification sent to user for **{movie_name}**.")

        except (UserIsBlocked, PeerIdInvalid):
            # --- 3. BLOCKED USER POPUP ---
            await cb.answer("⚠️ FAILED: User has BLOCKED the bot!", show_alert=True)
            
        except Exception as e:
            await cb.answer(f"Error: {e}", show_alert=True)

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
    idle()
    app.stop()
