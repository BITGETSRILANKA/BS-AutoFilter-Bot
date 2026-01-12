import os
import json
import logging
import asyncio
import threading
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, CallbackQuery
import firebase_admin
from firebase_admin import credentials, db
from app import run_flask_server

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")
URL = os.environ.get("URL", "") 

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BSAutoFilterBot")

# --- FIREBASE INIT ---
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(json.loads(FIREBASE_KEY))
        firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
    except Exception as e:
        logger.error(f"Firebase Error: {e}")

# --- BOT SETUP ---
app = Client("BSAutoFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- CUSTOM FILTER (Fail-safe for Web App) ---
def check_web_app(_, __, message):
    return bool(message.web_app_data)
web_filter = filters.create(check_web_app)

# --- KEYBOARDS ---
def get_start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Open Movie App", web_app=WebAppInfo(url=URL))],
        [
            InlineKeyboardButton("ℹ️ Help", callback_data="help"),
            InlineKeyboardButton("📊 Stats", callback_data="stats")
        ]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data="home")]
    ])

# --- COMMAND HANDLERS ---

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    if not URL.startswith("http"):
        await message.reply("⚠️ **Config Error:** URL variable is missing in Koyeb.")
        return

    # 1. Track User (Save ID to Firebase for Stats)
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    try:
        # We only save the name to avoid huge data. The key is the ID.
        db.reference(f'users/{user_id}').update({'name': user_name})
    except Exception as e:
        logger.error(f"User tracking error: {e}")

    # 2. Send Welcome Message
    await message.reply_text(
        f"🎬 **Movie Club**\n\n"
        f"Hey {message.from_user.first_name}! \n"
        f"Click the button below to open the library and watch your favorite content.",
        reply_markup=get_start_keyboard()
    )

# --- CALLBACK HANDLER (Buttons) ---
@app.on_callback_query()
async def callback_handler(client, cb: CallbackQuery):
    try:
        data = cb.data

        if data == "home":
            await cb.message.edit_text(
                f"🎬 **Movie Club**\n\n"
                f"Hey {cb.from_user.first_name}! \n"
                f"Click the button below to open the library and watch your favorite content.",
                reply_markup=get_start_keyboard()
            )

        elif data == "help":
            text = (
                "**📖 How to use:**\n\n"
                "1. Click **📱 Open Movie App**.\n"
                "2. Type a movie name in the search bar.\n"
                "3. Click on the poster to see details.\n"
                "4. Scroll down and click on a file (720p, 1080p, etc).\n"
                "5. The bot will send the file here instantly!\n\n"
                "⚠️ Files auto-delete in **2 minutes** to protect copyright."
            )
            await cb.message.edit_text(text, reply_markup=get_back_keyboard())

        elif data == "stats":
            await cb.answer("🔄 Fetching stats...")
            
            # Fetch Counts from Firebase
            try:
                # Note: getting full snapshots might be slow if DB is huge. 
                # For small/medium bots, this is fine.
                users_snapshot = db.reference('users').get()
                files_snapshot = db.reference('files').get()
                
                total_users = len(users_snapshot) if users_snapshot else 0
                total_files = len(files_snapshot) if files_snapshot else 0
            except Exception as e:
                total_users = "N/A"
                total_files = "N/A"
                logger.error(f"Stats error: {e}")

            text = (
                "**📊 Bot Statistics**\n\n"
                f"👥 **Total Users:** {total_users}\n"
                f"🎬 **Total Movies:** {total_files}"
            )
            await cb.message.edit_text(text, reply_markup=get_back_keyboard())

    except Exception as e:
        logger.error(f"Callback Error: {e}")

# --- WEB APP DATA HANDLER ---
@app.on_message(web_filter)
async def web_app_data_handler(client, message):
    try:
        unique_id = message.web_app_data.data
        
        # 1. Get file from DB
        ref = db.reference(f'files/{unique_id}')
        file_data = ref.get()
        
        if not file_data:
            await message.reply("❌ File not found in database.", quote=True)
            return
            
        # 2. Notify user
        status_msg = await message.reply(f"⬇️ **Sending:** `{file_data['file_name']}`...", quote=True)
        
        # 3. Send the file
        caption = f"🎬 **{file_data['file_name']}**\n\n⚠️ **Auto-delete in 2 mins.**"
        sent_file = await client.send_cached_media(
            chat_id=message.chat.id,
            file_id=file_data['file_id'],
            caption=caption
        )
        
        # 4. Clean up
        await status_msg.delete()
        asyncio.create_task(delete_later(sent_file, 120))
        
        # 5. Send Start menu again
        await asyncio.sleep(1)
        await message.reply_text(
            "👇 **Search for more movies:**",
            reply_markup=get_start_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Web App Error: {e}")

async def delete_later(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass

@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    try:
        media = message.document or message.video
        if not media: return
        
        filename = getattr(media, "file_name", None)
        if not filename:
             filename = message.caption.split("\n")[0].strip() if message.caption else f"Video_{message.id}.mp4"

        data = {
            "file_name": filename,
            "file_size": media.file_size,
            "file_id": media.file_id,
            "unique_id": media.file_unique_id
        }
        db.reference(f'files/{media.file_unique_id}').set(data)
        logger.info(f"Indexed: {filename}")
    except Exception as e:
        logger.error(e)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask_server, daemon=True)
    t.start()
    app.run()
