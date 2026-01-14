import os
import json
import math
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageDeleteForbidden, UserNotParticipant
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", ""))
BOT_TOKEN = os.environ.get("BOT_TOKEN", ""))
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
DB_URL = os.environ.get("DB_URL", ""))
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", ""))

# Add these new configurations
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(','))) if os.environ.get("ADMIN_IDS") else []
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "")  # Channel username without @
RESULTS_PER_PAGE = 10

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
DELETE_TASKS = {}

# --- HELPER FUNCTIONS ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

async def force_sub_check(user_id):
    """Check if user is subscribed to force subscription channel"""
    if not FORCE_SUB_CHANNEL:
        return True
    
    try:
        user = await app.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        if user.status not in ["left", "kicked", "banned"]:
            return True
    except UserNotParticipant:
        pass
    except Exception as e:
        logger.error(f"Force sub check error: {e}")
    
    return False

async def send_force_sub_message(chat_id, user_id):
    """Send force subscription message"""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL}"),
        InlineKeyboardButton("🔄 Try Again", callback_data=f"checksub|{user_id}")
    ]])
    
    message = await app.send_message(
        chat_id,
        f"⚠️ **Please join our channel to use this bot**\n\n"
        f"Join: @{FORCE_SUB_CHANNEL}\n"
        f"Then click 'Try Again'",
        reply_markup=keyboard
    )
    
    # Delete after 2 minutes
    asyncio.create_task(delete_message_delayed(message.id, chat_id, 120))

async def delete_message_delayed(message_id, chat_id, delay_seconds=120):
    """Delete a message after specified delay"""
    await asyncio.sleep(delay_seconds)
    try:
        await app.delete_messages(chat_id, message_id)
    except:
        pass

# --- AUTO DELETE FUNCTION ---
async def delete_file_after_delay(message_id, chat_id, delay_minutes=2):
    """Delete a file after specified delay"""
    try:
        logger.info(f"⏰ Scheduled deletion for message {message_id} in {delay_minutes} minutes")
        await asyncio.sleep(delay_minutes * 60)
        
        try:
            await app.delete_messages(chat_id, message_id)
            logger.info(f"🗑️ Deleted message {message_id}")
        except MessageDeleteForbidden:
            logger.warning(f"⚠️ Cannot delete message {message_id} - forbidden")
        except Exception as e:
            logger.error(f"❌ Error deleting message {message_id}: {e}")
        
        if message_id in DELETE_TASKS:
            del DELETE_TASKS[message_id]
            
    except asyncio.CancelledError:
        logger.info(f"⏹️ Deletion cancelled for message {message_id}")
    except Exception as e:
        logger.error(f"❌ Error in delete_file_after_delay: {e}")

# -----------------------------------------------------------------------------
# 1. FILE INDEXING
# -----------------------------------------------------------------------------
@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    try:
        media = message.document or message.video
        if not media: return

        filename = getattr(media, "file_name", None)
        
        if not filename:
            if message.caption:
                filename = message.caption.split("\n")[0].strip()
                if message.video and not "." in filename:
                    filename += ".mp4"
                elif message.document and not "." in filename:
                    filename += ".mkv"
            else:
                filename = f"Video_{message.id}.mp4"

        valid_exts = ('.mkv', '.mp4', '.avi', '.webm', '.mov')
        if not filename.lower().endswith(valid_exts) and not message.video:
            return

        file_data = {
            "file_name": filename,
            "file_size": media.file_size,
            "file_id": media.file_id,
            "unique_id": media.file_unique_id,
            "caption": message.caption or filename,
            "message_id": message.id,
            "channel_id": CHANNEL_ID
        }

        ref = db.reference(f'files/{media.file_unique_id}')
        ref.set(file_data)
        
        logger.info(f"✅ Indexed: {filename} (ID: {message.id})")

    except Exception as e:
        logger.error(f"❌ Error indexing file: {e}")

# -----------------------------------------------------------------------------
# 2. COMMANDS - WORK IN BOTH PRIVATE AND GROUPS
# -----------------------------------------------------------------------------
@app.on_message(filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    chat_type = message.chat.type
    
    # Check force subscription
    if not await force_sub_check(user_id):
        await send_force_sub_message(message.chat.id, user_id)
        return
    
    welcome_text = f"👋 **Hey {message.from_user.first_name}!**\n"
    
    if chat_type == "private":
        welcome_text += "Send me a movie name and I'll search for it.\n\n"
    else:
        welcome_text += (
            "I'm a movie search bot!\n"
            "**How to use in group:**\n"
            "• Tag me with a movie name: `@botname movie_name`\n"
            "• Or use command: `/search movie_name`\n\n"
        )
    
    welcome_text += "⚠️ **Note:** Downloaded files are sent to your PM and auto-delete after 2 minutes."
    
    await message.reply_text(welcome_text)

@app.on_message(filters.command("help"))
async def help_command(client, message):
    user_id = message.from_user.id
    
    if not await force_sub_check(user_id):
        await send_force_sub_message(message.chat.id, user_id)
        return
    
    help_text = (
        "**📖 Help Guide:**\n\n"
        "**In Private Chat:**\n"
        "• Just send a movie name to search\n\n"
        "**In Groups:**\n"
        "• Tag me with movie name: `@botname movie_name`\n"
        "• Use command: `/search movie_name`\n\n"
        "**Features:**\n"
        "• Click on files to download\n"
        "• Use pagination buttons\n"
        "• Files are sent to your PM\n\n"
        "⏰ **Auto-delete:** Files auto-delete after 2 minutes\n\n"
        "Made with ❤️ by Movie Search Bot"
    )
    
    await message.reply_text(help_text)

@app.on_message(filters.command("search") & filters.group)
async def search_in_group(client, message):
    user_id = message.from_user.id
    
    # Check force subscription
    if not await force_sub_check(user_id):
        await send_force_sub_message(message.chat.id, user_id)
        return
    
    if len(message.command) < 2:
        await message.reply_text("Please provide a movie name!\nExample: `/search Avengers`")
        return
    
    query = " ".join(message.command[1:])
    await perform_search(message, query, is_group=True)

# Handle mentions in groups
@app.on_message(filters.group & filters.mentioned)
async def handle_mention(client, message):
    user_id = message.from_user.id
    
    if not await force_sub_check(user_id):
        await send_force_sub_message(message.chat.id, user_id)
        return
    
    # Extract query from mention
    query = message.text.replace(f"@{client.me.username}", "").strip()
    
    if query:
        await perform_search(message, query, is_group=True)

# Handle text in private chat
@app.on_message(filters.text & filters.private)
async def search_in_private(client, message):
    user_id = message.from_user.id
    
    if not await force_sub_check(user_id):
        await send_force_sub_message(message.chat.id, user_id)
        return
    
    query = message.text.strip()
    await perform_search(message, query, is_group=False)

# -----------------------------------------------------------------------------
# 3. SEARCH FUNCTIONALITY
# -----------------------------------------------------------------------------
async def perform_search(message, query, is_group=False):
    search_msg = await message.reply_text("⏳ **Searching...**")
    
    try:
        ref = db.reference('files')
        snapshot = ref.get()

        if not snapshot:
            await search_msg.edit("❌ Database is empty.")
            return

        results = []
        for key, val in snapshot.items():
            f_name = val.get('file_name', '').lower().replace(".", " ")
            if query.lower() in f_name:
                results.append(val)
        
        if not results:
            await search_msg.edit(f"❌ No results found for: `{query}`")
            return

        USER_SEARCHES[message.from_user.id] = results
        
        # Send results in current chat (group or private)
        await send_results_page(message, search_msg, page=1, is_group=is_group)

    except Exception as e:
        logger.error(f"Search Error: {e}")
        await search_msg.edit("❌ Error occurred.")

# -----------------------------------------------------------------------------
# 4. PAGINATION
# -----------------------------------------------------------------------------
async def send_results_page(message, editable_msg, page=1, is_group=False):
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
        btn_name = name.replace("[", "").replace("]", "")
        if len(btn_name) > 40: btn_name = btn_name[:40] + "..."
        
        # Store user_id in callback data to send file to PM
        buttons.append([
            InlineKeyboardButton(
                f"[{size}] {btn_name}", 
                callback_data=f"dl|{file['unique_id']}|{user_id}"
            )
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}|{user_id}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}|{user_id}"))
    if nav: buttons.append(nav)

    # Add a help button for groups
    if is_group:
        buttons.append([
            InlineKeyboardButton("ℹ️ How to download?", callback_data="help_download")
        ])

    await editable_msg.edit_text(
        f"🎬 **Found {total_results} Files for: `{message.text if hasattr(message, 'text') else 'Search'}`**\n"
        f"👇 Click to download (will be sent to your PM):\n\n"
        f"⚠️ **Note:** Files auto-delete in 2 minutes",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# -----------------------------------------------------------------------------
# 5. CALLBACK HANDLERS
# -----------------------------------------------------------------------------
@app.on_callback_query()
async def callback_handler(client, cb):
    try:
        data = cb.data.split("|")
        action = data[0]

        if action == "dl":
            unique_id = data[1]
            user_id = int(data[2]) if len(data) > 2 else cb.from_user.id
            
            # Verify the user clicking is the same as stored in callback
            if cb.from_user.id != user_id:
                await cb.answer("⚠️ This button is not for you!", show_alert=True)
                return
            
            # Check force subscription
            if not await force_sub_check(user_id):
                await cb.answer("Please join the channel first!", show_alert=True)
                await send_force_sub_message(cb.message.chat.id, user_id)
                return
            
            ref = db.reference(f'files/{unique_id}')
            file_data = ref.get()

            if not file_data:
                await cb.answer("❌ File not found.", show_alert=True)
                return
            
            await cb.answer("📂 Sending to your PM...")
            
            # Send file to user's PM
            caption = f"{file_data.get('caption', '')}\n\n" \
                     f"⏰ **This file will be automatically deleted in 2 minutes**\n" \
                     f"💬 **Requested from:** {cb.message.chat.title if cb.message.chat.type != 'private' else 'Private Chat'}"
            
            try:
                # Send file to user's PM
                sent_message = await client.send_cached_media(
                    chat_id=user_id,
                    file_id=file_data['file_id'],
                    caption=caption
                )
                
                # Schedule deletion after 2 minutes
                if sent_message:
                    delete_task = asyncio.create_task(
                        delete_file_after_delay(sent_message.id, user_id, 2)
                    )
                    DELETE_TASKS[sent_message.id] = delete_task
                    
                    # Send confirmation in group/chat
                    confirmation = await cb.message.reply_text(
                        f"✅ **File sent to your PM!**\n"
                        f"📁 **File:** {file_data.get('file_name', 'Unknown')}\n"
                        f"⏰ **Note:** Will auto-delete in 2 minutes",
                        reply_to_message_id=cb.message.id
                    )
                    
                    # Delete confirmation after 30 seconds
                    asyncio.create_task(delete_message_delayed(confirmation.id, cb.message.chat.id, 30))
                    
            except Exception as e:
                logger.error(f"Error sending file to PM: {e}")
                # If user hasn't started bot in PM
                await cb.answer("❌ Please start me in PM first!", show_alert=True)
                # Send start button
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📩 Start Bot in PM", url=f"https://t.me/{client.me.username}?start=start")
                ]])
                await cb.message.reply_text(
                    "⚠️ **Please start me in PM first!**\n"
                    "Click the button below to start the bot in private chat.",
                    reply_markup=keyboard,
                    reply_to_message_id=cb.message.id
                )

        elif action == "page":
            user_id = int(data[2]) if len(data) > 2 else cb.from_user.id
            
            # Verify user
            if cb.from_user.id != user_id:
                await cb.answer("⚠️ This button is not for you!", show_alert=True)
                return
            
            # Check force subscription
            if not await force_sub_check(user_id):
                await cb.answer("Please join the channel first!", show_alert=True)
                await send_force_sub_message(cb.message.chat.id, user_id)
                return
            
            await send_results_page(cb.message, cb.message, page=int(data[1]), 
                                  is_group=(cb.message.chat.type != "private"))
        
        elif action == "checksub":
            user_id = int(data[1]) if len(data) > 1 else cb.from_user.id
            
            if await force_sub_check(user_id):
                await cb.message.delete()
                await cb.answer("✅ You can now use the bot!", show_alert=True)
            else:
                await cb.answer("❌ Please join the channel first!", show_alert=True)
        
        elif action == "help_download":
            await cb.answer(
                "📌 **How to download:**\n"
                "1. Click any file button\n"
                "2. File will be sent to your PM\n"
                "3. Download from there\n"
                "4. Files auto-delete after 2 minutes",
                show_alert=True
            )
        
        elif action == "noop":
            await cb.answer("Current Page")

    except Exception as e:
        logger.error(f"Callback Error: {e}")

# -----------------------------------------------------------------------------
# 6. ADMIN COMMANDS
# -----------------------------------------------------------------------------
@app.on_message(filters.command("stats") & filters.user(ADMIN_IDS))
async def stats_command(client, message):
    try:
        ref = db.reference('files')
        snapshot = ref.get()
        total_files = len(snapshot) if snapshot else 0
        
        await message.reply_text(
            f"📊 **Bot Statistics**\n\n"
            f"• Total files indexed: `{total_files}`\n"
            f"• Active delete tasks: `{len(DELETE_TASKS)}`\n"
            f"• Active searches: `{len(USER_SEARCHES)}`\n"
            f"• Bot uptime: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
        )
    except Exception as e:
        logger.error(f"Stats error: {e}")

@app.on_message(filters.command("broadcast") & filters.user(ADMIN_IDS))
async def broadcast_command(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/broadcast your_message`")
        return
    
    broadcast_text = " ".join(message.command[1:])
    await message.reply_text("📢 Starting broadcast...")
    
    # Note: You'll need to maintain a user database for proper broadcasting
    # For now, this just sends to the current chat
    await message.reply_text(f"📢 **Broadcast:**\n\n{broadcast_text}")

# -----------------------------------------------------------------------------
# 7. BOT MANAGEMENT
# -----------------------------------------------------------------------------
async def cancel_all_delete_tasks():
    """Cancel all pending delete tasks when bot stops"""
    logger.info("Cancelling all pending delete tasks...")
    for message_id, task in list(DELETE_TASKS.items()):
        try:
            task.cancel()
            await task
        except:
            pass
    DELETE_TASKS.clear()

def main():
    # Start HTTP server in a separate thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Start the Telegram bot
    print("🤖 Bot Started...")
    print(f"👤 Bot Username: @{app.me.username}")
    print(f"📊 Admin IDs: {ADMIN_IDS}")
    print(f"📢 Force Sub: {FORCE_SUB_CHANNEL}")
    
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        asyncio.run(cancel_all_delete_tasks())

if __name__ == "__main__":
    main()
