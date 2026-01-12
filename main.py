import os
import json
import math
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
# These are loaded from Koyeb Environment Variables
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0)) 
DB_URL = os.environ.get("DB_URL", "") 
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "") 

# --- SETUP LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MnSearchBot")

# --- SETUP FIREBASE ---
if not firebase_admin._apps:
    try:
        if FIREBASE_KEY:
            # Parse the JSON string from Env Var
            cred_dict = json.loads(FIREBASE_KEY)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
            logger.info("✅ Firebase Initialized Successfully")
        else:
            logger.error("❌ FIREBASE_KEY is missing in Env Vars")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Firebase: {e}")

# --- SETUP BOT ---
app = Client("MnSearchBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- GLOBAL STORAGE (For Pagination) ---
# Format: { user_id: [list_of_files] }
USER_SEARCHES = {}
RESULTS_PER_PAGE = 10

# --- HELPER: Human Readable Size ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

# -----------------------------------------------------------------------------
# 1. INDEX FILES (Auto-Save from Channel)
# -----------------------------------------------------------------------------
@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    try:
        media = message.document or message.video
        if not media: return

        # Validate Extension
        valid_exts = ('.mkv', '.mp4', '.avi', '.webm', '.mov')
        fname = media.file_name or "Unknown_File"
        if not fname.lower().endswith(valid_exts):
            return

        # Prepare Data
        # Using file_unique_id as key prevents duplicates
        file_data = {
            "file_name": fname,
            "file_size": media.file_size,
            "file_id": media.file_id,
            "unique_id": media.file_unique_id,
            "caption": message.caption or ""
        }

        # Save to Firebase
        ref = db.reference(f'files/{media.file_unique_id}')
        ref.set(file_data)
        logger.info(f"💾 Indexed: {fname}")

    except Exception as e:
        logger.error(f"Error indexing: {e}")

# -----------------------------------------------------------------------------
# 2. START COMMAND
# -----------------------------------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply_text(
        f"👋 **Hello {message.from_user.first_name}!**\n\n"
        "I am a Movie Search Bot connected to Firebase.\n"
        "Send me a movie name to search."
    )

# -----------------------------------------------------------------------------
# 3. SEARCH LOGIC
# -----------------------------------------------------------------------------
@app.on_message(filters.text & filters.private)
async def search_handler(client, message):
    query = message.text.strip().lower()
    if query.startswith("/"): return

    msg = await message.reply_text("🔎 **Searching database...**")

    try:
        # Fetch all data (For large DBs, consider Firebase Querying methods)
        ref = db.reference('files')
        snapshot = ref.get()

        if not snapshot:
            await msg.edit("❌ **Database is empty.**")
            return

        # Filter Results locally
        results = []
        for key, val in snapshot.items():
            if val.get('file_name') and query in val['file_name'].lower().replace(".", " "):
                results.append(val)
        
        if not results:
            await msg.edit(f"❌ No results found for: `{message.text}`")
            return

        # Save results to memory for pagination
        USER_SEARCHES[message.from_user.id] = results
        
        # Send Page 1
        await send_results_page(message, msg, page=1)

    except Exception as e:
        logger.error(f"Search Error: {e}")
        await msg.edit("❌ Error occurred while searching.")

# -----------------------------------------------------------------------------
# 4. PAGINATION & BUTTON BUILDER
# -----------------------------------------------------------------------------
async def send_results_page(message, editable_msg, page=1):
    user_id = message.from_user.id
    results = USER_SEARCHES.get(user_id)

    if not results:
        await editable_msg.edit("⚠️ Session expired. Please search again.")
        return

    total_results = len(results)
    total_pages = math.ceil(total_results / RESULTS_PER_PAGE)
    
    # Slice list for current page
    start_i = (page - 1) * RESULTS_PER_PAGE
    end_i = start_i + RESULTS_PER_PAGE
    current_files = results[start_i:end_i]

    # Build File Buttons
    buttons = []
    for file in current_files:
        size = get_size(file['file_size'])
        # Clean name for button
        name = file['file_name'].replace("[", "").replace("]", "")
        if len(name) > 40: name = name[:40] + "..."
        
        btn_text = f"[{size}] {name}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"dl|{file['unique_id']}")])

    # Build Navigation Buttons
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    
    if nav: buttons.append(nav)

    await editable_msg.edit_text(
        f"👋 **Results for your search**\n"
        f"Found {total_results} files.\n"
        f"👇 Click to download:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# -----------------------------------------------------------------------------
# 5. CALLBACK HANDLER
# -----------------------------------------------------------------------------
@app.on_callback_query()
async def callback_handler(client, cb):
    try:
        data = cb.data.split("|")
        action = data[0]

        if action == "dl":
            unique_id = data[1]
            ref = db.reference(f'files/{unique_id}')
            file_data = ref.get()

            if not file_data:
                await cb.answer("❌ File not found in DB", show_alert=True)
                return
            
            await cb.answer("📂 Sending File...")
            # Send Cached Media (Fast)
            await client.send_cached_media(
                chat_id=cb.message.chat.id,
                file_id=file_data['file_id'],
                caption=file_data.get('caption', "")
            )

        elif action == "page":
            page_no = int(data[1])
            await send_results_page(cb, cb.message, page=page_no)

        elif action == "noop":
            await cb.answer("Current Page")

    except Exception as e:
        logger.error(f"Callback Error: {e}")

if __name__ == "__main__":
    print("🤖 Bot Started...")
    app.run()
