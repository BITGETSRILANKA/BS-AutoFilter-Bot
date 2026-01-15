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
USER_SEARCH_CACHE = {} # Temp storage for pagination

# --- HTTP SERVER ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', config.PORT), HealthHandler)
    server.serve_forever()

# --- BACKGROUND TASK: AUTO DELETE (Improvement 5) ---
async def check_auto_delete():
    """Runs forever. Checks DB for messages to delete."""
    while True:
        try:
            tasks = database.get_due_delete_tasks()
            for task in tasks:
                try:
                    await app.delete_messages(task['chat_id'], task['message_id'])
                    config.logger.info(f"🗑️ Auto-deleted {task['message_id']}")
                except Exception as e:
                    config.logger.warning(f"Delete fail: {e}") # Msg likely already deleted
                
                # Remove from DB regardless of success
                database.remove_delete_task(task['key'])
                
        except Exception as e:
            config.logger.error(f"Auto-Delete Loop Error: {e}")
        
        await asyncio.sleep(20) # Check every 20 seconds

# --- COMMAND: START ---
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    database.add_user(message.from_user.id)
    
    # 1. Force Sub Check
    if not await utils.get_fsub(client, message):
        btn = [[InlineKeyboardButton("📢 Join Update Channel", url=config.FSUB_LINK)]]
        return await message.reply_text(
            "⚠️ **You must join our channel to use this bot!**\n\nJoin below and click /start again.",
            reply_markup=InlineKeyboardMarkup(btn)
        )

    # 2. Handle Deep Linking
    if len(message.command) > 1 and message.command[1].startswith("dl_"):
        unique_id = message.command[1].split("_")[1]
        await send_file_to_user(client, message.chat.id, unique_id)
        return

    # 3. Normal Welcome
    buttons = [
        [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("🔎 Inline Search", switch_inline_query_current_chat="")]
    ]
    await message.reply_text(f"👋 Hi **{message.from_user.first_name}**! I am an advanced Filter Bot.", reply_markup=InlineKeyboardMarkup(buttons))

# --- COMMAND: STATS (Improvement 4) ---
@app.on_message(filters.command("stats") & filters.user(config.ADMIN_ID))
async def stats_handler(client, message):
    msg = await message.reply_text("⏳ Calculating...")
    
    files = len(database.FILES_CACHE)
    users = database.get_total_users()
    ram = utils.get_system_stats()
    
    text = (
        f"📊 **System Statistics**\n\n"
        f"📂 **Files Indexed:** `{files}`\n"
        f"👤 **Total Users:** `{users}`\n"
        f"💾 **RAM Usage:** `{ram}`"
    )
    await msg.edit(text)

# --- INDEXING ---
@app.on_message(filters.chat(config.CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    media = message.document or message.video
    if not media: return
    
    filename = getattr(media, "file_name", None) or "Unknown"
    if not filename and message.caption:
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

# --- SEARCH HANDLER ---
@app.on_message(filters.text & (filters.private | filters.group))
async def text_search(client, message):
    if message.text.startswith("/") or message.via_bot: return
    
    # FSub Check (Optional for groups, mandatory for PM)
    if message.chat.type == enums.ChatType.PRIVATE:
        if not await utils.get_fsub(client, message):
            btn = [[InlineKeyboardButton("Join Channel", url=config.FSUB_LINK)]]
            await message.reply_text("⚠️ Join channel first.", reply_markup=InlineKeyboardMarkup(btn))
            return

    query = message.text.strip().lower()
    if len(query) < 2: return

    # SEARCH IN RAM (Improvement 1)
    query_words = re.sub(r'[._-]', ' ', query).split()
    results = []
    
    for file in database.FILES_CACHE:
        fname = re.sub(r'[._-]', ' ', file.get('file_name', '').lower())
        if all(w in fname for w in query_words):
            results.append(file)
            
    if not results:
        msg = await message.reply_text(f"❌ No files found for: `{query}`")
        asyncio.create_task(temp_del(msg, 10)) # Helper to delete "No results" msg
        return
        
    USER_SEARCH_CACHE[message.from_user.id] = results
    await send_results_page(message, page=1)

# --- INLINE SEARCH ---
@app.on_inline_query()
async def inline_search(client, query):
    text = query.query.strip().lower()
    if not text: return
    
    query_words = re.sub(r'[._-]', ' ', text).split()
    results = []
    count = 0
    
    for file in database.FILES_CACHE:
        if count >= 50: break
        fname = re.sub(r'[._-]', ' ', file.get('file_name', '').lower())
        
        if all(w in fname for w in query_words):
            count += 1
            size = utils.get_size(file['file_size'])
            results.append(
                InlineQueryResultCachedDocument(
                    id=file['unique_id'],
                    title=file['file_name'],
                    document_file_id=file['file_id'],
                    description=f"Size: {size}",
                    caption=file['caption']
                )
            )
    await query.answer(results, cache_time=10)

# --- SENDING FILE LOGIC ---
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
            caption=caption,
            protect_content=True  # Security Improvement (No forwarding)
        )
        
        # Add to Persistent Delete Queue (Improvement 5)
        delete_time = time.time() + config.DELETE_DELAY
        database.add_delete_task(chat_id, sent.id, delete_time)
        
    except Exception as e:
        config.logger.error(f"Send Error: {e}")

# --- PAGINATION ---
async def send_results_page(message, page=1):
    user_id = message.from_user.id
    results = USER_SEARCH_CACHE.get(user_id)
    if not results: return
    
    total = len(results)
    total_pages = math.ceil(total / config.RESULTS_PER_PAGE)
    start = (page - 1) * config.RESULTS_PER_PAGE
    current = results[start : start + config.RESULTS_PER_PAGE]
    
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

    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav: buttons.append(nav)
    
    text = f"🔍 **Found {total} files**\nPage {page}/{total_pages}"
    
    if isinstance(message, str): # Edited message
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else: # New message
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query()
async def callbacks(client, cb):
    data = cb.data.split("|")
    if data[0] == "dl":
        await cb.answer()
        await send_file_to_user(client, cb.message.chat.id, data[1])
    elif data[0] == "page":
        await send_results_page(cb.message, page=int(data[1]))

async def temp_del(msg, seconds):
    await asyncio.sleep(seconds)
    try: await msg.delete()
    except: pass

# --- MAIN ---
if __name__ == "__main__":
    # Start HTTP Server
    threading.Thread(target=run_http_server, daemon=True).start()
    
    # Load Cache
    database.refresh_cache()
    
    print("🤖 Bot Starting...")
    app.start()
    me = app.get_me()
    BOT_USERNAME = me.username
    
    # Start Auto-Delete Loop
    loop = asyncio.get_event_loop()
    loop.create_task(check_auto_delete())
    
    print(f"✅ Bot Started as @{BOT_USERNAME}")
    from pyrogram import idle
    idle()
    app.stop()
