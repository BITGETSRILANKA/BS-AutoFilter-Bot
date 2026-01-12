import os
import json
import math
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
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
            cred_dict = json.loads(FIREBASE_KEY)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
            logger.info("✅ Firebase Initialized Successfully")
        else:
            logger.error("❌ FIREBASE_KEY is missing")
    except Exception as e:
        logger.error(f"❌ Firebase Error: {e}")

# --- SIMPLE HTTP SERVER FOR KOYEB HEALTH CHECKS ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/health', '/ping']:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Bot is running')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress the default logging
        pass

def run_http_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"🌐 HTTP Health Check Server started on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

# --- SETUP BOT ---
app = Client("MnSearchBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- GLOBAL STORAGE ---
USER_SEARCHES = {}
RESULTS_PER_PAGE = 10

# --- HELPER: Size ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

# -----------------------------------------------------------------------------
# 1. SMARTER FILE INDEXING (Fixes the "Row/Album" Issue)
# -----------------------------------------------------------------------------
@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    try:
        media = message.document or message.video
        if not media: return

        # --- LOGIC TO FIX MISSING FILENAMES IN ALBUMS ---
        filename = getattr(media, "file_name", None)
        
        # 1. If filename is missing, try to use the caption
        if not filename:
            if message.caption:
                # Use first line of caption as filename
                filename = message.caption.split("\n")[0].strip()
                # Append extension if missing
                if message.video and not "." in filename:
                    filename += ".mp4"
                elif message.document and not "." in filename:
                    filename += ".mkv"
            else:
                # 2. If no caption, generate a name (so it still saves)
                filename = f"Video_{message.id}.mp4"

        # Replace dots with spaces for better search (optional)
        # filename = filename.replace(".", " ")

        # Validate Extension (Relaxed for Videos)
        valid_exts = ('.mkv', '.mp4', '.avi', '.webm', '.mov')
        if not filename.lower().endswith(valid_exts) and not message.video:
            # If it's a document but not a video file, ignore it
            return

        # Prepare Data
        file_data = {
            "file_name": filename,
            "file_size": media.file_size,
            "file_id": media.file_id,
            "unique_id": media.file_unique_id,
            "caption": message.caption or filename # Use filename as caption if caption is empty
        }

        # Save to Firebase
        # We use unique_id to prevent duplicates (Same file = Same ID)
        ref = db.reference(f'files/{media.file_unique_id}')
        ref.set(file_data)
        
        logger.info(f"✅ Indexed: {filename} (ID: {message.id})")

    except Exception as e:
        logger.error(f"❌ Error indexing file: {e}")

# -----------------------------------------------------------------------------
# 2. COMMANDS & SEARCH
# -----------------------------------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply_text(
        f"👋 **Hey {message.from_user.first_name}!**\n"
        "Send me a movie name and I'll search for it."
    )

@app.on_message(filters.text & filters.private)
async def search_handler(client, message):
    query = message.text.strip().lower()
    msg = await message.reply_text("⏳ **Searching...**")

    try:
        ref = db.reference('files')
        snapshot = ref.get()

        if not snapshot:
            await msg.edit("❌ Database is empty.")
            return

        results = []
        for key, val in snapshot.items():
            # Search Logic: Check if query exists in filename
            f_name = val.get('file_name', '').lower().replace(".", " ")
            if query in f_name:
                results.append(val)
        
        if not results:
            await msg.edit(f"❌ No results found for: `{query}`")
            return

        USER_SEARCHES[message.from_user.id] = results
        await send_results_page(message, msg, page=1)

    except Exception as e:
        logger.error(f"Search Error: {e}")
        await msg.edit("❌ Error occurred.")

# -----------------------------------------------------------------------------
# 3. PAGINATION
# -----------------------------------------------------------------------------
async def send_results_page(message, editable_msg, page=1):
    user_id = message.from_user.id
    results = USER_SEARCHES.get(user_id)

    if not results:
        await editable_msg.edit("⚠️ Session expired.")
        return

    total_results = len(results)
    total_pages = math.ceil(total_results / RESULTS_PER_PAGE)
    start_i = (page - 1) * RESULTS_PER_PAGE
    current_files = results[start_i : start_i + RESULTS_PER_PAGE]

    buttons = []
    for file in current_files:
        size = get_size(file.get('file_size', 0))
        name = file.get('file_name', 'Unknown')
        # Cleanup name for button
        btn_name = name.replace("[", "").replace("]", "")
        if len(btn_name) > 40: btn_name = btn_name[:40] + "..."
        
        buttons.append([InlineKeyboardButton(f"[{size}] {btn_name}", callback_data=f"dl|{file['unique_id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav: buttons.append(nav)

    await editable_msg.edit_text(
        f"🎬 **Found {total_results} Files**\n👇 Click to download:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# -----------------------------------------------------------------------------
# 4. CALLBACKS
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
                await cb.answer("❌ File not found.", show_alert=True)
                return
            
            await cb.answer("📂 Sending...")
            await client.send_cached_media(
                chat_id=cb.message.chat.id,
                file_id=file_data['file_id'],
                caption=file_data.get('caption', "")
            )

        elif action == "page":
            await send_results_page(cb, cb.message, page=int(data[1]))
        elif action == "noop":
            await cb.answer("Current Page")

    except Exception as e:
        logger.error(f"Callback Error: {e}")

def main():
    # Start HTTP server in a separate thread for Koyeb health checks
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Start the Telegram bot
    print("Bot Started...")
    app.run()

if __name__ == "__main__":
    main()
