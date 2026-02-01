import os, json, math, logging, asyncio, threading, re, time, uuid, psutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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

# TIMERS
FILE_MSG_DELETE_TIME = 120
RESULT_MSG_DELETE_TIME = 600
USER_MSG_DELETE_TIME = 300
SUGGESTION_DELETE_TIME = 300

# -----------------------------------------------------------------------------
# 2. INITIALIZATION
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s")
logger = logging.getLogger("BSMoviesBot")
ia = Cinemagoer()

if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(FIREBASE_KEY))
    firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})

FILES_CACHE = []
SEARCH_DATA_CACHE = {} 

# -----------------------------------------------------------------------------
# 3. ADVANCED CLEANING MECHANICS (Shobana/AutoFilter Logic)
# -----------------------------------------------------------------------------

def get_clean_title(text):
    """The magic function that extracts 'Stranger Things' from messy names."""
    # 1. Remove website prefixes & tags (Add more here as you find them)
    text = re.sub(r'(?i)(www|http|https|t\.me|@)\S+', '', text)
    text = re.sub(r'(?i)(1Tamilmv|Tamilmv|Maptap|Team|Fm|Bsmovies|Bot|Mep|Dvdrip|Brrip)', '', text)
    
    # 2. Extract Year
    year = re.search(r'\b(19|20)\d{2}\b', text)
    year_str = f" ({year.group(0)})" if year else ""
    
    # 3. Remove Season/Episode patterns (S01, E01, Ep 1, Season 1)
    text = re.sub(r'(?i)\b(S\d+|E\d+|Season\s?\d+|Episode\s?\d+|Ep\s?\d+)\b', '', text)
    
    # 4. Remove Quality & Technical tags
    text = re.sub(r'(?i)\b(720p|1080p|4k|2160p|mkv|mp4|avi|h264|hevc|x264|x265|bluray|hindi|tamil|telugu|english|dual|multi|esub)\b', '', text)
    
    # 5. Clean symbols and capitalization
    text = re.sub(r'[\W_]+', ' ', text)
    final_title = re.sub(r'\s+', ' ', text).strip().title()
    
    return f"{final_title}{year_str}".strip()

def refresh_cache():
    global FILES_CACHE
    try:
        snap = db.reference('files').get()
        if snap: FILES_CACHE = list(snap.values())
        logger.info(f"🚀 Cached {len(FILES_CACHE)} files")
    except: pass

def get_size(size):
    if not size: return "0B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def add_delete_task(chat_id, msg_id, d_time):
    try: db.reference(f'delete_queue/{chat_id}_{msg_id}').set({"chat_id": chat_id, "message_id": msg_id, "delete_time": d_time})
    except: pass

# -----------------------------------------------------------------------------
# 4. SMART SUGGESTION ENGINE (IMDb + Local Rhyme)
# -----------------------------------------------------------------------------

async def get_smart_suggestions(query):
    suggestions = set()
    
    # 1. IMDb Official Correction (Good for spelling)
    try:
        movies = ia.search_movie(query)
        for m in movies:
            if m.get('kind') in ['movie', 'tv series']:
                suggestions.add(f"{m.get('title')} ({m.get('year')})" if m.get('year') else m.get('title'))
            if len(suggestions) >= 3: break
    except: pass

    # 2. Local Fuzzy Rhyme (Good for matching your specific database)
    # We create a set of UNIQUE clean titles from your files
    local_titles = list(set([get_clean_title(f['file_name']) for f in FILES_CACHE if len(f['file_name']) > 3]))
    
    # Find rhyming/similar titles
    matches = process.extract(query, local_titles, limit=5, scorer=fuzz.WRatio)
    for m, score, idx in matches:
        if score > 55: # Similarity threshold
            suggestions.add(m)
            
    return list(suggestions)[:6]

# -----------------------------------------------------------------------------
# 5. SEARCH LOGIC
# -----------------------------------------------------------------------------

async def perform_search(client, message, query, is_correction=False):
    add_delete_task(message.chat.id, message.id, time.time() + USER_MSG_DELETE_TIME)
    
    raw_words = query.lower().split()
    results = []
    
    for file in FILES_CACHE:
        fname = file.get('file_name', '').lower()
        if all(w in fname for w in raw_words):
            results.append(file)

    if results:
        s_id = str(uuid.uuid4())[:8]
        SEARCH_DATA_CACHE[s_id] = results
        await send_results_page(message, s_id, page=1, is_edit=is_correction)
        return

    # No Match -> Show Grouped Suggestions
    suggestions = await get_smart_suggestions(query)
    btn = []
    for s in suggestions:
        clean_s = re.sub(r'\(.*?\)', '', s).strip() # Strip year for re-search
        btn.append([InlineKeyboardButton(s, callback_data=f"sp|{clean_s[:40]}")])
    
    btn.append([InlineKeyboardButton("📝 Request from Admin", callback_data=f"req|{query[:30]}")])
    btn.append([InlineKeyboardButton("🚫 CLOSE 🚫", callback_data="close_data")])
    
    text = f"⚠️ **No files found for:** `{query}`\n\n‼️ **Is there any of this?** 👇"
    if is_correction: sent = await message.edit_text(text, reply_markup=InlineKeyboardMarkup(btn))
    else: sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn))
    add_delete_task(sent.chat.id, sent.id, time.time() + SUGGESTION_DELETE_TIME)

async def send_results_page(message, s_id, page=1, is_edit=False):
    res = SEARCH_DATA_CACHE.get(s_id)
    if not res: return
    total_pages = math.ceil(len(res) / 10)
    current = res[(page-1)*10 : page*10]
    
    btn = []
    for f in current:
        n, s = f['file_name'][:30], get_size(f['file_size'])
        if message.chat.type == enums.ChatType.PRIVATE:
            btn.append([InlineKeyboardButton(f"[{s}] {n}", callback_data=f"dl|{f['unique_id']}")])
        else:
            btn.append([InlineKeyboardButton(f"[{s}] {n}", url=f"https://t.me/{BOT_USERNAME}?start=dl_{f['unique_id']}")])

    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{s_id}|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{s_id}|{page+1}"))
    if nav: btn.append(nav)
    
    text = f"🔍 **Found {len(res)} files**"
    if is_edit: await message.edit_text(text, reply_markup=InlineKeyboardMarkup(btn))
    else:
        sent = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn))
        add_delete_task(sent.chat.id, sent.id, time.time() + RESULT_MSG_DELETE_TIME)

# -----------------------------------------------------------------------------
# 6. HANDLERS
# -----------------------------------------------------------------------------
app = Client("BSMoviesBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    if len(message.command) > 1 and message.command[1].startswith("dl_"):
        uid = message.command[1].split("_")[1]
        for f in FILES_CACHE:
            if f['unique_id'] == uid:
                sent = await client.send_cached_media(message.chat.id, f['file_id'], caption=f"📁 **{f['file_name']}**")
                add_delete_task(message.chat.id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
                return
    btn = [[InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
           [InlineKeyboardButton("🔎 Inline Search", switch_inline_query_current_chat="")]]
    await message.reply_text(f"👋 Hi **{message.from_user.first_name}**!\nType movie name to search.", reply_markup=InlineKeyboardMarkup(btn))

@app.on_message(filters.command("broadcast") & filters.user(ADMIN_ID))
async def broadcast(client, message):
    if not message.reply_to_message: return await message.reply_text("Reply to a message.")
    users = list((db.reference('users').get()).keys())
    for u in users:
        try: await message.reply_to_message.copy(int(u)); await asyncio.sleep(0.1)
        except: pass
    await message.reply_text("✅ Done.")

@app.on_message(filters.text & (filters.private | filters.group))
async def search(client, message):
    if not message.text.startswith("/") and not message.via_bot:
        await perform_search(client, message, message.text.strip())

@app.on_callback_query()
async def cb_handler(client, cb):
    data = cb.data.split("|")
    if data[0] == "dl":
        for f in FILES_CACHE:
            if f['unique_id'] == data[1]:
                sent = await client.send_cached_media(cb.message.chat.id, f['file_id'], caption=f"📁 **{f['file_name']}**")
                add_delete_task(cb.message.chat.id, sent.id, time.time() + FILE_MSG_DELETE_TIME)
                await cb.answer(); return
    elif data[0] == "sp":
        await cb.answer(); await perform_search(client, cb.message, data[1], is_correction=True)
    elif data[0] == "req":
        btn = [[InlineKeyboardButton("✅ Mark Uploaded", callback_data=f"done|{cb.from_user.id}|{data[1]}")]]
        await client.send_message(ADMIN_ID, f"📝 **Request**\nUser: {cb.from_user.first_name}\nMovie: `{data[1]}`", reply_markup=InlineKeyboardMarkup(btn))
        await cb.answer("✅ Request Sent!", show_alert=True); await cb.message.delete()
    elif data[0] == "done":
        try: await client.send_message(int(data[1]), f"🍿 Good News! `{data[2]}` is available now!")
        except: pass
        await cb.message.edit_text(f"✅ Resolved: {data[2]}")
    elif data[0] == "close_data": await cb.message.delete()

# -----------------------------------------------------------------------------
# 7. EXECUTION
# -----------------------------------------------------------------------------
async def auto_del():
    while True:
        tasks = db.reference('delete_queue').get()
        if tasks:
            for k, v in tasks.items():
                if v['delete_time'] <= time.time():
                    try: await app.delete_messages(v['chat_id'], v['message_id'])
                    except: pass
                    db.reference(f'delete_queue/{k}').delete()
        await asyncio.sleep(20)

if __name__ == "__main__":
    # Health Check server for hosting providers
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), H).serve_forever(), daemon=True).start()
    
    refresh_cache()
    app.start()
    BOT_USERNAME = app.get_me().username
    print(f"🤖 @{BOT_USERNAME} Started")
    asyncio.get_event_loop().create_task(auto_del())
    from pyrogram import idle
    idle()
