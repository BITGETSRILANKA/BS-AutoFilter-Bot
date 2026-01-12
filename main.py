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
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))  # Channel where files are indexed
MOVIE_CHANNEL = os.environ.get("MOVIE_CHANNEL", "")  # Channel where movies will be posted
MOVIE_CHANNEL_LINK = os.environ.get("MOVIE_CHANNEL_LINK", "https://t.me/your_channel")  # Your channel link
JOIN_CHANNEL = os.environ.get("JOIN_CHANNEL", "https://t.me/your_join_channel")  # Channel to join
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# --- SETUP LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BSAutoFilterBot")

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
            self.wfile.write(b'BS Auto Filter Bot is running')
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
app = Client("BSAutoFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- GLOBAL STORAGE ---
USER_SEARCHES = {}
RESULTS_PER_PAGE = 10
# Store scheduled delete tasks
DELETE_TASKS = {}
# Store movie posts in channel
MOVIE_POSTS = {}

# --- HELPER: Size ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

# --- AUTO DELETE FUNCTION ---
async def delete_message_after_delay(message_id, chat_id, delay_minutes=2, message_type="file"):
    """Delete a message after specified delay"""
    try:
        logger.info(f"⏰ Scheduled deletion for {message_type} message {message_id} in {delay_minutes} minutes")
        await asyncio.sleep(delay_minutes * 60)  # Convert minutes to seconds
        
        # Try to delete the message
        try:
            await app.delete_messages(chat_id, message_id)
            logger.info(f"🗑️ Deleted {message_type} message {message_id}")
        except MessageDeleteForbidden:
            logger.warning(f"⚠️ Cannot delete {message_type} message {message_id} - forbidden")
        except Exception as e:
            logger.error(f"❌ Error deleting {message_type} message {message_id}: {e}")
        
        # Remove task from tracking
        task_key = f"{message_id}_{chat_id}"
        if task_key in DELETE_TASKS:
            del DELETE_TASKS[task_key]
            
    except asyncio.CancelledError:
        logger.info(f"⏹️ Deletion cancelled for message {message_id}")
    except Exception as e:
        logger.error(f"❌ Error in delete_message_after_delay: {e}")

# --- CHECK USER IN CHANNEL ---
async def check_user_in_channel(user_id):
    """Check if user is member of required channel"""
    try:
        if not JOIN_CHANNEL or "t.me" not in JOIN_CHANNEL:
            return True  # Skip check if no channel set
            
        # Extract channel username from link
        channel_username = JOIN_CHANNEL.split("/")[-1]
        if not channel_username:
            return True
            
        # Check if user is member
        try:
            user = await app.get_chat_member(channel_username, user_id)
            return user.status in ["member", "administrator", "creator"]
        except UserNotParticipant:
            return False
        except Exception as e:
            logger.error(f"Error checking channel membership: {e}")
            return True  # Allow access on error
    except Exception as e:
        logger.error(f"Error in check_user_in_channel: {e}")
        return True

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
            "message_id": message.id,
            "channel_id": CHANNEL_ID,
            "caption": message.caption or filename, # Use filename as caption if caption is empty
            "timestamp": datetime.now().isoformat()
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
    start_text = f"👋 **Hey {message.from_user.first_name}!**\n" \
                 f"Welcome to **BS Auto Filter Bot** 🎬\n\n" \
                 f"Send me a movie name and I'll search for it.\n\n" \
                 f"⚠️ **Auto-Delete Rules:**\n" \
                 f"• Movie posts auto-delete in **2 minutes** ⏰\n" \
                 f"• Search results auto-delete in **10 minutes** ⏰\n\n"
    
    # Add channel join requirement if set
    if JOIN_CHANNEL and "t.me" in JOIN_CHANNEL:
        start_text += f"📢 **Required:** Join our channel to access movies!\n"
    
    await message.reply_text(start_text)

@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    help_text = "**📖 BS Auto Filter Bot Help Guide:**\n\n" \
                "• Send me a movie name to search\n" \
                "• Click on files to get them\n" \
                "• Use pagination buttons to navigate\n\n" \
                "⏰ **Auto-delete Rules:**\n" \
                "• Movie posts auto-delete in **2 minutes**\n" \
                "• Search results auto-delete in **10 minutes**\n\n" \
                "🔗 **Buttons:**\n" \
                "• **Join Channel** - Join our official channel\n" \
                "• **Movie Post** - Get the movie from channel\n\n" \
                "Made with ❤️ by **BS Auto Filter Bot**"
    
    await message.reply_text(help_text)

@app.on_message(filters.text & filters.private)
async def search_handler(client, message):
    query = message.text.strip().lower()
    
    # Skip commands
    if query.startswith('/'):
        return
    
    # Check if user is in required channel
    if JOIN_CHANNEL and "t.me" in JOIN_CHANNEL:
        is_member = await check_user_in_channel(message.from_user.id)
        if not is_member:
            buttons = [[InlineKeyboardButton("🔗 Join Channel", url=JOIN_CHANNEL)]]
            await message.reply_text(
                "⚠️ **Access Restricted!**\n\n"
                "You need to join our channel to use this bot.\n"
                "Please join the channel below and try again.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return
    
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
        
        # Send results and schedule deletion in 10 minutes
        await send_results_page(message, msg, page=1)
        
        # Schedule deletion of search results message in 10 minutes
        if msg:
            task_key = f"{msg.id}_{msg.chat.id}"
            delete_task = asyncio.create_task(
                delete_message_after_delay(msg.id, msg.chat.id, 10, "search_results")
            )
            DELETE_TASKS[task_key] = delete_task

    except Exception as e:
        logger.error(f"Search Error: {e}")
        await msg.edit("❌ Error occurred.")

# -----------------------------------------------------------------------------
# 3. PAGINATION WITH AUTO-DELETE SCHEDULING
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
        
        buttons.append([InlineKeyboardButton(f"[{size}] {btn_name}", callback_data=f"get|{file['unique_id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav: buttons.append(nav)
    
    # Add auto-delete info in message
    current_time = datetime.now().strftime("%H:%M")
    
    text = f"**Found {total_results} Files** 🎬\n" \
           f"Click to get movie link:\n\n" \
           f"⏰ **Auto-Delete:**\n" \
           f"• Movie posts: 2 minutes\n" \
           f"• This list: 10 minutes ({current_time})\n\n" \
           f"*Search: {message.text}*"

    await editable_msg.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# -----------------------------------------------------------------------------
# 4. POST MOVIE TO CHANNEL AND SEND LINK TO USER
# -----------------------------------------------------------------------------
async def post_movie_to_channel(file_data, user_id):
    """Post movie to channel and return message link"""
    try:
        if not MOVIE_CHANNEL:
            logger.error("❌ MOVIE_CHANNEL not configured")
            return None
        
        # Prepare caption for channel post
        file_name = file_data.get('file_name', 'Unknown')
        file_size = get_size(file_data.get('file_size', 0))
        
        caption = f"**🎬 {file_name}**\n\n" \
                 f"📦 **Size:** {file_size}\n" \
                 f"⏰ **Auto-deletes in 2 minutes**\n\n" \
                 f"#BSAutoFilterBot"
        
        # Convert MOVIE_CHANNEL to appropriate format
        channel_id = MOVIE_CHANNEL
        if isinstance(MOVIE_CHANNEL, str):
            if MOVIE_CHANNEL.startswith('@'):
                channel_id = MOVIE_CHANNEL
            elif MOVIE_CHANNEL.lstrip('-').isdigit():
                channel_id = int(MOVIE_CHANNEL)
        
        # Post to movie channel
        message = await app.send_cached_media(
            chat_id=channel_id,
            file_id=file_data['file_id'],
            caption=caption
        )
        
        # Generate message link
        if isinstance(channel_id, str) and channel_id.startswith('@'):
            channel_username = channel_id.lstrip('@')
            message_link = f"https://t.me/{channel_username}/{message.id}"
        else:
            # For private channels
            channel_str = str(channel_id).replace('-100', '')
            message_link = f"https://t.me/c/{channel_str}/{message.id}"
        
        # Store message info for auto-delete
        MOVIE_POSTS[message.id] = {
            'channel_id': channel_id,
            'timestamp': datetime.now()
        }
        
        # Schedule auto-delete in 2 minutes
        task_key = f"{message.id}_{channel_id}"
        delete_task = asyncio.create_task(
            delete_message_after_delay(message.id, channel_id, 2, "channel_movie")
        )
        DELETE_TASKS[task_key] = delete_task
        
        return message_link
        
    except Exception as e:
        logger.error(f"❌ Error posting to channel: {e}")
        return None

# -----------------------------------------------------------------------------
# 5. CALLBACKS - POST MOVIE TO CHANNEL AND SEND BUTTONS
# -----------------------------------------------------------------------------
@app.on_callback_query()
async def callback_handler(client, cb):
    try:
        data = cb.data.split("|")
        action = data[0]

        if action == "get":
            unique_id = data[1]
            ref = db.reference(f'files/{unique_id}')
            file_data = ref.get()

            if not file_data:
                await cb.answer("❌ File not found.", show_alert=True)
                return
            
            await cb.answer("📤 Posting to channel...")
            
            # Post movie to channel
            message_link = await post_movie_to_channel(file_data, cb.from_user.id)
            
            if not message_link:
                await cb.answer("❌ Failed to post movie.", show_alert=True)
                return
            
            # Prepare buttons
            buttons = []
            
            # Join Channel Button (if set)
            if JOIN_CHANNEL and "t.me" in JOIN_CHANNEL:
                buttons.append([InlineKeyboardButton("🔗 Join Channel", url=JOIN_CHANNEL)])
            
            # Movie Post Link Button
            buttons.append([InlineKeyboardButton("🎬 Movie Post Link", url=message_link)])
            
            # Send message with buttons to user
            file_name = file_data.get('file_name', 'Unknown')
            file_size = get_size(file_data.get('file_size', 0))
            
            user_msg = await cb.message.reply_text(
                f"✅ **Movie Posted Successfully!**\n\n"
                f"🎬 **Title:** {file_name}\n"
                f"📦 **Size:** {file_size}\n"
                f"⏰ **Auto-deletes in 2 minutes**\n\n"
                f"Click the button below to get the movie:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
            # Schedule deletion of user message in 5 minutes
            task_key = f"{user_msg.id}_{user_msg.chat.id}"
            delete_task = asyncio.create_task(
                delete_message_after_delay(user_msg.id, user_msg.chat.id, 5, "user_notification")
            )
            DELETE_TASKS[task_key] = delete_task

        elif action == "page":
            # When user navigates to different page, update the message
            # but keep the 10-minute deletion schedule
            user_id = cb.from_user.id
            results = USER_SEARCHES.get(user_id)
            
            if not results:
                await cb.answer("⚠️ Session expired. Search again.")
                return
                
            await send_results_page(cb, cb.message, page=int(data[1]))
            await cb.answer(f"Page {data[1]}")
            
        elif action == "noop":
            await cb.answer(f"Page {data[1] if len(data) > 1 else 'Current'}")

    except Exception as e:
        logger.error(f"Callback Error: {e}")

# -----------------------------------------------------------------------------
# 6. CANCEL ALL PENDING DELETE TASKS ON STOP
# -----------------------------------------------------------------------------
async def cancel_all_delete_tasks():
    """Cancel all pending delete tasks when bot stops"""
    logger.info("Cancelling all pending delete tasks...")
    for task_key, task in list(DELETE_TASKS.items()):
        try:
            task.cancel()
            await task
        except:
            pass
    DELETE_TASKS.clear()

# -----------------------------------------------------------------------------
# 7. BOT STARTUP AND SHUTDOWN HANDLERS
# -----------------------------------------------------------------------------
@app.on_raw_update()
async def handle_raw_update(client, update, users, chats):
    # This handles bot startup/shutdown
    pass

def main():
    # Start HTTP server in a separate thread for Koyeb health checks
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Start the Telegram bot
    print("BS Auto Filter Bot Started...")
    
    # Setup signal handlers for clean shutdown
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        # Cancel all pending tasks on exit
        asyncio.run(cancel_all_delete_tasks())

if __name__ == "__main__":
    main()
