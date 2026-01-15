import asyncio
import threading
import re
import time
import math
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultCachedDocument

# Import modules
import config
import database
import utils

# --- SETUP ---
app = Client(
    "BSFilterBot", 
    api_id=config.API_ID, 
    api_hash=config.API_HASH, 
    bot_token=config.BOT_TOKEN
)
BOT_USERNAME = ""
RESULTS_PER_PAGE = 10
USER_SEARCH_CACHE = {} 

# --- HELPER: CLEAN TEXT ---
def clean_text(text):
    return re.sub(r'[\W_]+', '', text).lower()

# --- HTTP SERVER ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', config.PORT), HealthHandler)
    server.serve_forever()

# --- BACKGROUND TASK: AUTO DELETE ---
async def check_auto_delete():
    while True:
        try:
            tasks = database.get_due_delete_tasks()
            for task in tasks:
                try:
                    await app.delete_messages(task['chat_id'], task['message_id'])
                    config.logger.info(f"🗑️ Auto-deleted {task['message_id']}")
                except Exception: pass 
                database.remove_delete_task(task['key'])
        except Exception: pass
        await asyncio.sleep(20) 

# --- COMMAND: START ---
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    database.add_user(message.from_user.id)
    if len(message.command) > 1 and message.command[1].startswith("dl_"):
        unique_id = message.command[1].split("_")[1]
        await send_file_to_user(client, message.chat.id, unique_id)
        return

    buttons = [
        [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("🔎 Inline Search", switch_inline_query_current_chat="")]
    ]
    await message.reply_text(f"👋 Hi **{message.from_user.first_name}**! I am an advanced Filter Bot.\n\nType a movie name to search.", reply_markup=InlineKeyboardMarkup(buttons))

# --- INDEXING ---
@app.on_message(filters.chat(config.CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
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
    if database.add_file_to_db(data):
        config.logger.info(f"✅ Indexed: {filename}")

# --- SEARCH HANDLER (TEXT + TMDB) ---
@app.on_message(filters.text & (filters.private | filters.group))
async def text_search(client, message):
    if message.text.startswith("/") or message.via_bot: return
    
    query = message.text.strip()
    if len(query) < 2: return

    # 1. Search Files in RAM
    raw_query = query.lower().split()
    clean_query = clean_text(query)
    results = []
    
    for file in database.FILES_CACHE:
        fname_raw = file.get('file_name', '').lower()
        fname_clean = clean_text(fname_raw)
        capt_raw = file.get('caption', '').lower()
        capt_clean = clean_text(capt_raw)
        
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

    # 2. Get TMDB Data (Only if in Private Chat or User explicitly searched)
    tmdb_data = None
    if config.TMDB_API_KEY:
        tmdb_data = utils.search_tmdb(query)

    # 3. Cache Results
    USER_SEARCH_CACHE[message.from_user.id] = {
        "files": results,
        "tmdb": tmdb_data,
        "query": query
    }

    # 4. Send First Page
    await send_results_page(message, page=1, is_new=True)

# --- PAGINATION & SENDING ---
async def send_results_page(message, page=1, is_new=False):
    user_id = message.from_user.id
    cached_data = USER_SEARCH_CACHE.get(user_id)
    if not cached_data: return
    
    results = cached_data['files']
    tmdb = cached_data['tmdb']
    
    total = len(results)
    total_pages = math.ceil(total / config.RESULTS_PER_PAGE)
    start = (page - 1) * config.RESULTS_PER_PAGE
    current = results[start : start + config.RESULTS_PER_PAGE]
    
    # Generate Buttons
    buttons = []
    for file in current:
        name = file['file_name'][:30]
        size = utils.get_size(file['file_size'])
        if message.chat.type == enums.ChatType.PRIVATE:
            cb_data = f"dl|{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", callback_data=cb_data)])
        else:
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{file['unique_id']}"
            buttons.append([InlineKeyboardButton(f"[{size}] {name}", url=url)])

    # Navigation Buttons
    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav: buttons.append(nav)
    
    # Generate Caption
    if tmdb:
        caption = (
            f"🎬 **{tmdb['title']}**\n"
            f"⭐️ Rating: {tmdb['rating']}/10\n"
            f"📖 {tmdb['overview']}\n\n"
            f"📂 **Found {total} Files**"
        )
    else:
        caption = f"🎬 **Found {total} files** for `{cached_data['query']}`\nPage {page}/{total_pages}"
    
    reply_markup = InlineKeyboardMarkup(buttons)

    # Sending Logic
    if is_new:
        # SEND NEW MESSAGE
        if tmdb and tmdb['poster']:
            await message.reply_photo(tmdb['poster'], caption=caption, reply_markup=reply_markup)
        else:
            await message.reply_text(caption, reply_markup=reply_markup)
    else:
        # EDIT EXISTING MESSAGE
        try:
            if message.photo:
                await message.edit_caption(caption=caption, reply_markup=reply_markup)
            else:
                await message.edit_text(text=caption, reply_markup=reply_markup)
        except Exception as e:
            pass # Message might be too old or deleted

# --- CALLBACKS ---
@app.on_callback_query()
async def callbacks(client, cb):
    data = cb.data.split("|")
    if data[0] == "dl":
        await cb.answer()
        await send_file_to_user(client, cb.message.chat.id, data[1])
    elif data[0] == "page":
        await send_results_page(cb.message, page=int(data[1]), is_new=False)
    elif data[0] == "noop":
        await cb.answer()

# --- FILE SENDING ---
async def send_file_to_user(client, chat_id, unique_id):
    file_data = database.get_file_by_id(unique_id)
    if not file_data:
        return await client.send_message(chat_id, "❌ File removed.")
    
    caption = (
        f"📁 **{file_data['file_name']}**\n"
        f"📊 Size: {utils.get_size(file_data['file_size'])}\n\n"
        f"⏳ **This message will be deleted in 2 minutes.**"
    )
    
    try:
        sent = await client.send_cached_media(
            chat_id=chat_id,
            file_id=file_data['file_id'],
            caption=caption
        )
        delete_time = time.time() + config.DELETE_DELAY
        database.add_delete_task(chat_id, sent.id, delete_time)
    except Exception as e:
        config.logger.error(f"Send Error: {e}")

# --- INLINE SEARCH (Kept Simple without TMDB to be fast) ---
@app.on_inline_query()
async def inline_search(client, query):
    text = query.query.strip()
    if not text: return
    
    clean_q = clean_text(text)
    raw_q = text.lower().split()
    results = []
    count = 0
    
    for file in database.FILES_CACHE:
        if count >= 50: break
        fname_raw = file.get('file_name', '').lower()
        fname_clean = clean_text(fname_raw)
        
        matched = False
        if clean_q in fname_clean: matched = True
        elif all(w in fname_raw for w in raw_q): matched = True
        
        if matched:
            count += 1
            size = utils.get_size(file['file_size'])
            clean_caption = f"📁 **{file['file_name']}**\n📊 Size: {size}\n\n🤖 Bot: @{BOT_USERNAME}"
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

async def temp_del(msg, seconds):
    await asyncio.sleep(seconds)
    try: await msg.delete()
    except: pass

# --- MAIN ---
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    database.refresh_cache()
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
