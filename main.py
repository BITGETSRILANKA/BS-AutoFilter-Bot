import os
import json
import logging
import asyncio
import threading
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
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
URL = os.environ.get("URL", "") # Your Koyeb URL

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

# --- WEB DATA FILTER ---
def web_data_filter(_, __, message):
    return bool(message.web_app_data)
web_data = filters.create(web_data_filter)

# --- HANDLERS ---
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    if not URL.startswith("http"):
        await message.reply("⚠️ **Config Error:** Please set the `URL` variable in Koyeb.")
        return

    await message.reply_text(
        f"🎬 **Movie Club**\n\n"
        f"Hello {message.from_user.first_name}! Tap below to open the library.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Open App", web_app=WebAppInfo(url=URL))]
        ])
    )

@app.on_message(web_data)
async def web_app_data_handler(client, message):
    try:
        unique_id = message.web_app_data.data
        ref = db.reference(f'files/{unique_id}')
        file_data = ref.get()
        
        if not file_data:
            await message.reply("❌ File not found.", quote=True)
            return
            
        await message.reply(f"⬇️ **Sending:** `{file_data['file_name']}`...", quote=True)
        
        caption = f"🎬 **{file_data['file_name']}**\n\n⚠️ **Auto-delete in 2 mins.**"
        sent = await client.send_cached_media(
            chat_id=message.chat.id,
            file_id=file_data['file_id'],
            caption=caption
        )
        
        asyncio.create_task(delete_later(sent, 120))
        await asyncio.sleep(1)
        await start(client, message)
        
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
