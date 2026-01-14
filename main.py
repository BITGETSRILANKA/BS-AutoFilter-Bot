import os
import json
import math
import logging
import asyncio
import threading
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageDeleteForbidden, UserNotParticipant, ChannelInvalid, ChannelPrivate
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# Add these new configurations
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(','))) if os.environ.get("ADMIN_IDS") else []
RESULTS_PER_PAGE = 10

# Store channel info
CHANNEL_INFO = {}
BOT_STARTED = False

# --- SETUP LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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
app = Client(
    "MnSearchBot", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    bot_token=BOT_TOKEN,
    in_memory=True  # This helps with Koyeb deployments
)

# --- GLOBAL STORAGE ---
USER_SEARCHES = {}
DELETE_TASKS = {}
# Track group messages to prevent spam
GROUP_SEARCH_COOLDOWN = {}

# --- HELPER FUNCTIONS ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

async def get_channel_info(channel_id):
    """Get channel info and cache it"""
    if channel_id not in CHANNEL_INFO:
        try:
            chat = await app.get_chat(channel_id)
            CHANNEL_INFO[channel_id] = {
                'title': chat.title,
                'username': chat.username,
                'id': chat.id
            }
            logger.info(f"✅ Got channel info for {chat.title}")
            return True
        except (ChannelInvalid, ChannelPrivate):
            logger.error(f"❌ Bot is not a member of channel {channel_id} or channel doesn't exist")
            CHANNEL_INFO[channel_id] = {
                'title': f"Channel {channel_id}",
                'username': None,
                'id': channel_id
            }
            return False
        except Exception as e:
            logger.error(f"❌ Error getting channel info: {e}")
            CHANNEL_INFO[channel_id] = {
                'title': f"Channel {channel_id}",
                'username': None,
                'id': channel_id
            }
            return False
    return True

async def delete_message_delayed(message_id, chat_id, delay_seconds=120):
    """Delete a message after specified delay"""
    await asyncio.sleep(delay_seconds)
    try:
        await app.delete_messages(chat_id, message_id)
    except:
        pass

def is_valid_movie_query(text):
    """
    Check if text looks like a movie name query
    Filters out short texts, commands, and common non-movie messages
    """
    # Remove extra spaces and normalize
    text = text.strip()
    
    # Check minimum length (at least 3 characters)
    if len(text) < 3:
        return False
    
    # Check maximum length (movies rarely have names longer than 50 chars)
    if len(text) > 50:
        return False
    
    # Ignore if it's a command
    if text.startswith('/'):
        return False
    
    # Ignore if it's a mention
    if '@' in text and any(word in text.lower() for word in ['@admin', '@admins', '@mod', '@moderator']):
        return False
    
    # Ignore common chat phrases (case insensitive)
    ignore_phrases = [
        'ok', 'okay', 'yes', 'no', 'thanks', 'thank you', 'hi', 'hello', 'hey',
        'bye', 'good morning', 'good night', 'lol', 'haha', 'wow', 'omg',
        'please', 'sorry', 'welcome', 'how are you', 'whats up', 'sup',
        'good', 'bad', 'nice', 'cool', 'awesome', 'great', 'perfect'
    ]
    
    if text.lower() in ignore_phrases:
        return False
    
    # Check if it looks like a typical movie name (contains at least one letter)
    if not any(c.isalpha() for c in text):
        return False
    
    return True

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
# 1. FILE INDEXING (Only if bot has access to channel)
# -----------------------------------------------------------------------------
@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    try:
        # Check if bot has access to this channel
        if not await get_channel_info(CHANNEL_ID):
            return
        
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
            "channel_id": CHANNEL_ID,
            "timestamp": datetime.now().isoformat()
        }

        ref = db.reference(f'files/{media.file_unique_id}')
        ref.set(file_data)
        
        logger.info(f"✅ Indexed: {filename} (ID: {message.id})")

    except Exception as e:
        logger.error(f"❌ Error indexing file: {e}")

# -----------------------------------------------------------------------------
# 2. BOT STARTUP - GET CHANNEL INFO
# -----------------------------------------------------------------------------
@app.on_raw_update()
async def handle_raw_update(client, update, users, chats):
    pass

# -----------------------------------------------------------------------------
# 3. COMMANDS - WORK IN BOTH PRIVATE AND GROUPS
# -----------------------------------------------------------------------------
@app.on_message(filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    chat_type = message.chat.type
    
    welcome_text = f"👋 **Hey {message.from_user.first_name}!**\n"
    
    if chat_type == "private":
        welcome_text += "Send me a movie name and I'll search for it.\n\n"
    else:
        welcome_text += (
            "I'm a movie search bot!\n"
            "**How to use:**\n"
            "• Just send a movie name (e.g., `Men In Black`)\n"
            "• Or use command: `/search movie_name`\n\n"
            "**Auto-search is enabled!** Just type movie names normally.\n\n"
        )
    
    welcome_text += "⚠️ **Note:** Downloaded files are sent to your PM and auto-delete after 2 minutes."
    
    await message.reply_text(welcome_text)

@app.on_message(filters.command("help"))
async def help_command(client, message):
    help_text = (
        "**📖 Help Guide:**\n\n"
        "**In Private Chat:**\n"
        "• Just send a movie name to search\n\n"
        "**In Groups:**\n"
        "• Send movie name directly (e.g., `Avengers Endgame`)\n"
        "• Or use command: `/search movie_name`\n"
        "• Or mention: `@botname movie_name`\n\n"
        "**Auto-Search Feature:**\n"
        "In groups, just type movie names normally. The bot will automatically search!\n\n"
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
    
    if len(message.command) < 2:
        await message.reply_text("Please provide a movie name!\nExample: `/search Avengers`")
        return
    
    query = " ".join(message.command[1:])
    await perform_search(message, query, is_group=True)

# -----------------------------------------------------------------------------
# 4. MAIN FEATURE: AUTO-SEARCH ON MOVIE NAMES IN GROUPS
# -----------------------------------------------------------------------------
@app.on_message(filters.group & filters.text)
async def auto_search_movie(client, message):
    """
    Automatically search when someone sends a movie name in group
    """
    # Skip if message is from bot
    if message.from_user and message.from_user.is_bot:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if it's a valid movie query
    if not is_valid_movie_query(message.text):
        return
    
    # Cooldown check to prevent spam (5 seconds per user per group)
    cooldown_key = f"{chat_id}_{user_id}"
    current_time = time.time()
    if cooldown_key in GROUP_SEARCH_COOLDOWN:
        if current_time - GROUP_SEARCH_COOLDOWN[cooldown_key] < 5:
            return
    
    # Update cooldown
    GROUP_SEARCH_COOLDOWN[cooldown_key] = current_time
    
    # Perform search
    query = message.text.strip()
    await perform_search(message, query, is_group=True)

# Handle mentions in groups (still works as before)
@app.on_message(filters.group & filters.mentioned)
async def handle_mention(client, message):
    user_id = message.from_user.id
    
    # Extract query from mention
    me = await app.get_me()
    query = message.text.replace(f"@{me.username}", "").strip()
    
    if query and is_valid_movie_query(query):
        await perform_search(message, query, is_group=True)

# Handle text in private chat
@app.on_message(filters.text & filters.private)
async def search_in_private(client, message):
    user_id = message.from_user.id
    
    query = message.text.strip()
    if is_valid_movie_query(query):
        await perform_search(message, query, is_group=False)
    else:
        # If it's not a movie query, send help
        await message.reply_text(
            "Please send a movie name to search.\n"
            "Example: `Men In Black`, `Avengers Endgame`, `John Wick 4`\n\n"
            "Or use /help for more information."
        )

# -----------------------------------------------------------------------------
# 5. SEARCH FUNCTIONALITY
# -----------------------------------------------------------------------------
async def perform_search(message, query, is_group=False):
    search_msg = await message.reply_text(f"🔍 **Searching for:** `{query}`")
    
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
# 6. PAGINATION
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
        f"🎬 **Found {total_results} Files for:** `{query}`\n"
        f"👇 Click to download (will be sent to your PM):\n\n"
        f"⚠️ **Note:** Files auto-delete in 2 minutes",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# -----------------------------------------------------------------------------
# 7. CALLBACK HANDLERS
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
                me = await app.get_me()
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📩 Start Bot in PM", url=f"https://t.me/{me.username}?start=start")
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
            
            await send_results_page(cb.message, cb.message, page=int(data[1]), 
                                  is_group=(cb.message.chat.type != "private"))
        
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
# 8. ADMIN COMMANDS
# -----------------------------------------------------------------------------
@app.on_message(filters.command("stats") & filters.user(ADMIN_IDS))
async def stats_command(client, message):
    try:
        ref = db.reference('files')
        snapshot = ref.get()
        total_files = len(snapshot) if snapshot else 0
        
        # Check channel access
        channel_access = await get_channel_info(CHANNEL_ID)
        
        await message.reply_text(
            f"📊 **Bot Statistics**\n\n"
            f"• Total files indexed: `{total_files}`\n"
            f"• Active delete tasks: `{len(DELETE_TASKS)}`\n"
            f"• Active searches: `{len(USER_SEARCHES)}`\n"
            f"• Channel access: `{'✅' if channel_access else '❌'}`\n"
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

@app.on_message(filters.command("checkchannel") & filters.user(ADMIN_IDS))
async def check_channel_command(client, message):
    """Check if bot has access to the channel"""
    try:
        channel_access = await get_channel_info(CHANNEL_ID)
        
        if channel_access:
            channel_info = CHANNEL_INFO[CHANNEL_ID]
            await message.reply_text(
                f"✅ **Bot has access to channel!**\n\n"
                f"**Title:** {channel_info['title']}\n"
                f"**Username:** @{channel_info['username'] if channel_info['username'] else 'No Username'}\n"
                f"**ID:** `{channel_info['id']}`"
            )
        else:
            await message.reply_text(
                f"❌ **Bot does NOT have access to channel!**\n\n"
                f"**Channel ID:** `{CHANNEL_ID}`\n\n"
                f"**To fix:**\n"
                f"1. Add @{message._client.me.username} to your channel\n"
                f"2. Make sure bot has admin rights\n"
                f"3. Restart the bot"
            )
    except Exception as e:
        logger.error(f"Check channel error: {e}")
        await message.reply_text(f"❌ Error: {e}")

# -----------------------------------------------------------------------------
# 9. BOT STARTUP HANDLER
# -----------------------------------------------------------------------------
@app.on_message(filters.command("restart") & filters.user(ADMIN_IDS))
async def restart_bot(client, message):
    await message.reply_text("🔄 Restarting bot...")
    # This will trigger the bot to restart
    os._exit(1)

# -----------------------------------------------------------------------------
# 10. BOT MANAGEMENT
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

async def main_async():
    """Main async function to run the bot"""
    global BOT_STARTED
    
    try:
        # Start the bot
        await app.start()
        
        # Get bot info
        me = await app.get_me()
        print(f"🤖 Bot Started Successfully: @{me.username}")
        print(f"📊 Admin IDs: {ADMIN_IDS}")
        print(f"🎬 **Auto-search is ENABLED!**")
        print("Users can just type movie names in groups!")
        
        # Try to get channel info
        if CHANNEL_ID:
            channel_access = await get_channel_info(CHANNEL_ID)
            if channel_access:
                print(f"✅ Bot has access to channel: {CHANNEL_INFO[CHANNEL_ID]['title']}")
            else:
                print(f"⚠️ Bot does NOT have access to channel ID: {CHANNEL_ID}")
                print("Please add bot to the channel as admin!")
        else:
            print("⚠️ CHANNEL_ID not configured. File indexing disabled.")
        
        BOT_STARTED = True
        
        # Keep the bot running
        await idle()
        
    except Exception as e:
        logger.error(f"❌ Bot failed to start: {e}")
        print(f"❌ Bot failed to start: {e}")
    finally:
        # Stop the bot gracefully
        BOT_STARTED = False
        await cancel_all_delete_tasks()
        await app.stop()
        print("👋 Bot stopped")

def main():
    # Start HTTP server in a separate thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Run the bot
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
