import os
import json
import math
import logging
import asyncio
import threading
import random
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, MessageDeleteForbidden, UserNotParticipant, BadRequest
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
API_ID = os.environ.get("API_ID", "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))  # Source channel for indexing (ID)
MOVIE_CHANNEL_ID = int(os.environ.get("MOVIE_CHANNEL_ID", "0"))  # Channel to post movies (ID)
JOIN_CHANNEL_ID = int(os.environ.get("JOIN_CHANNEL_ID", "0"))  # Channel users must join (ID, optional)
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# Debug print all env vars (hide sensitive values)
print("=" * 50)
print("ENVIRONMENT VARIABLES CHECK:")
print(f"API_ID: {'Set' if API_ID and API_ID != '0' else 'NOT SET'}")
print(f"API_HASH: {'Set' if API_HASH else 'NOT SET'}")
print(f"BOT_TOKEN: {'Set' if BOT_TOKEN else 'NOT SET'}")
print(f"CHANNEL_ID: {CHANNEL_ID if CHANNEL_ID != 0 else 'NOT SET'}")
print(f"MOVIE_CHANNEL_ID: {MOVIE_CHANNEL_ID if MOVIE_CHANNEL_ID != 0 else 'NOT SET'}")
print(f"JOIN_CHANNEL_ID: {JOIN_CHANNEL_ID if JOIN_CHANNEL_ID != 0 else 'NOT SET'}")
print(f"DB_URL: {'Set' if DB_URL else 'NOT SET'}")
print(f"FIREBASE_KEY: {'Set' if FIREBASE_KEY else 'NOT SET'}")
print("=" * 50)

# --- SETUP LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("BSAutoFilterBot")

# --- SIMPLE HTTP SERVER FOR KOYEB HEALTH CHECKS ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/health', '/ping', '/status']:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            status = {
                "status": "running",
                "service": "BS Auto Filter Bot",
                "timestamp": datetime.now().isoformat(),
                "features": ["search", "auto-delete", "channel-posting"]
            }
            self.wfile.write(json.dumps(status).encode())
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
    print(f"✅ HTTP Server running on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

# --- SETUP FIREBASE ---
def setup_firebase():
    try:
        if FIREBASE_KEY and DB_URL:
            cred_dict = json.loads(FIREBASE_KEY)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
            logger.info("✅ Firebase Initialized Successfully")
            print("✅ Firebase initialized")
            return True
        else:
            logger.warning("⚠️ Firebase not configured")
            print("⚠️ Firebase not configured (missing FIREBASE_KEY or DB_URL)")
            return False
    except Exception as e:
        logger.error(f"❌ Firebase Error: {e}")
        print(f"❌ Firebase Error: {e}")
        return False

# --- GLOBAL STORAGE ---
USER_SEARCHES = {}
RESULTS_PER_PAGE = 10
DELETE_TASKS = {}
MOVIE_POSTS = {}
CHANNEL_INVITE_LINKS = {}  # Store generated invite links

# --- HELPER FUNCTIONS ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

async def handle_flood_wait(e, operation="unknown"):
    """Handle FloodWait errors"""
    wait_time = e.value or 60
    logger.warning(f"⏳ FloodWait for {operation}. Waiting {wait_time}s...")
    print(f"⏳ FloodWait detected. Waiting {wait_time} seconds...")
    await asyncio.sleep(wait_time)

async def delete_message_after_delay(message_id, chat_id, delay_minutes=2, message_type="file"):
    """Delete a message after specified delay"""
    try:
        logger.info(f"⏰ Scheduled deletion for {message_type} message {message_id} in {delay_minutes} minutes")
        await asyncio.sleep(delay_minutes * 60)
        
        try:
            await bot.delete_messages(chat_id, message_id)
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

async def check_user_in_channel(user_id):
    """Check if user is member of required channel using channel ID"""
    try:
        if not JOIN_CHANNEL_ID or JOIN_CHANNEL_ID == 0:
            return True
            
        # Check membership using channel ID
        try:
            user = await bot.get_chat_member(JOIN_CHANNEL_ID, user_id)
            return user.status in ["member", "administrator", "creator"]
        except UserNotParticipant:
            return False
        except Exception as e:
            logger.error(f"Error checking channel membership: {e}")
            return True  # Allow access on error
    except Exception as e:
        logger.error(f"Error in check_user_in_channel: {e}")
        return True

async def generate_invite_link():
    """Generate an invite link for the join channel"""
    try:
        if not JOIN_CHANNEL_ID or JOIN_CHANNEL_ID == 0:
            return None
            
        # Check if we already have a link
        if JOIN_CHANNEL_ID in CHANNEL_INVITE_LINKS:
            link_info = CHANNEL_INVITE_LINKS[JOIN_CHANNEL_ID]
            # Check if link is still valid (not expired)
            if link_info['expires_at'] > datetime.now():
                return link_info['invite_link']
        
        # Create new invite link
        chat = await bot.get_chat(JOIN_CHANNEL_ID)
        
        # Try to get existing invite links first
        try:
            invite_links = await bot.get_chat_invite_links(JOIN_CHANNEL_ID, limit=1)
            if invite_links:
                return invite_links[0].invite_link
        except:
            pass
        
        # Create new invite link (expires in 7 days, unlimited uses)
        invite_link = await bot.create_chat_invite_link(
            chat_id=JOIN_CHANNEL_ID,
            name="Bot Join Link",
            creates_join_request=False,
            expire_date=int((datetime.now() + timedelta(days=7)).timestamp()),
            member_limit=None  # Unlimited
        )
        
        # Store the link
        CHANNEL_INVITE_LINKS[JOIN_CHANNEL_ID] = {
            'invite_link': invite_link.invite_link,
            'expires_at': datetime.fromtimestamp(invite_link.expire_date) if invite_link.expire_date else datetime.now() + timedelta(days=365)
        }
        
        return invite_link.invite_link
        
    except Exception as e:
        logger.error(f"Error generating invite link: {e}")
        # Fallback: Try to get chat info and generate basic link
        try:
            chat = await bot.get_chat(JOIN_CHANNEL_ID)
            if hasattr(chat, 'username') and chat.username:
                return f"https://t.me/{chat.username}"
            else:
                # For private channels, we need to create an invite
                invite = await bot.create_chat_invite_link(
                    chat_id=JOIN_CHANNEL_ID,
                    name="Temporary Bot Join Link",
                    expire_date=int((datetime.now() + timedelta(hours=1)).timestamp()),
                    member_limit=100
                )
                return invite.invite_link
        except Exception as e2:
            logger.error(f"Error in fallback invite generation: {e2}")
            return None

async def get_channel_message_link(channel_id, message_id):
    """Generate a message link for a channel post"""
    try:
        # Get chat info to check if it has username
        chat = await bot.get_chat(channel_id)
        
        if hasattr(chat, 'username') and chat.username:
            # Public channel with username
            return f"https://t.me/{chat.username}/{message_id}"
        else:
            # Private channel - generate t.me/c/ link
            # Remove -100 prefix for t.me/c/ links
            channel_id_str = str(channel_id)
            if channel_id_str.startswith('-100'):
                channel_num = channel_id_str[4:]
            else:
                channel_num = channel_id_str.lstrip('-')
            
            return f"https://t.me/c/{channel_num}/{message_id}"
            
    except Exception as e:
        logger.error(f"Error generating message link: {e}")
        return f"https://t.me/c/{str(abs(channel_id))}/{message_id}"

# --- CREATE BOT CLIENT ---
def create_bot():
    try:
        api_id = int(API_ID) if API_ID and API_ID != "0" else None
        
        if not api_id or not API_HASH or not BOT_TOKEN:
            print("❌ Missing API credentials")
            return None
            
        print("🤖 Creating bot client...")
        bot_client = Client(
            name="bs_auto_filter_bot",
            api_id=api_id,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=2,
            sleep_threshold=30,
            in_memory=True  # Use in-memory session to avoid file issues
        )
        print("✅ Bot client created")
        return bot_client
    except Exception as e:
        print(f"❌ Error creating bot: {e}")
        return None

# --- SEARCH AND FILE FUNCTIONS ---
async def send_results_page(callback, editable_msg, page=1):
    """Send search results page with pagination"""
    try:
        user_id = callback.from_user.id
        results = USER_SEARCHES.get(user_id)

        if not results:
            await editable_msg.edit("⚠️ Session expired. Please search again.")
            return

        total_results = len(results)
        total_pages = math.ceil(total_results / RESULTS_PER_PAGE)
        
        # Validate page number
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
            
        start_i = (page - 1) * RESULTS_PER_PAGE
        end_i = start_i + RESULTS_PER_PAGE
        current_files = results[start_i:end_i]

        buttons = []
        for file in current_files:
            size = get_size(file.get('file_size', 0))
            name = file.get('file_name', 'Unknown')
            btn_name = name.replace("[", "").replace("]", "")
            if len(btn_name) > 40: 
                btn_name = btn_name[:40] + "..."
            
            buttons.append([InlineKeyboardButton(f"[{size}] {btn_name}", callback_data=f"get|{file['unique_id']}")])

        # Navigation buttons
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page|{page-1}"))
        
        nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        
        if page < total_pages:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page|{page+1}"))
        
        if nav: 
            buttons.append(nav)
        
        current_time = datetime.now().strftime("%H:%M")
        
        text = f"**Found {total_results} Files** 🎬\n" \
               f"Click to get movie link:\n\n" \
               f"⏰ **Auto-Delete:**\n" \
               f"• Movie posts: 2 minutes\n" \
               f"• This list: 10 minutes ({current_time})\n\n" \
               f"Page: {page}/{total_pages}"

        await editable_msg.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.error(f"Error in send_results_page: {e}")
        await callback.answer(f"Error: {str(e)[:50]}", show_alert=True)

async def post_movie_to_channel(file_data, user_id):
    """Post movie to channel and return message link"""
    try:
        if not MOVIE_CHANNEL_ID or MOVIE_CHANNEL_ID == 0:
            logger.error("❌ MOVIE_CHANNEL_ID not configured")
            return None
        
        file_name = file_data.get('file_name', 'Unknown')
        file_size = get_size(file_data.get('file_size', 0))
        
        caption = f"**🎬 {file_name}**\n\n" \
                 f"📦 **Size:** {file_size}\n" \
                 f"⏰ **Auto-deletes in 2 minutes**\n\n" \
                 f"#BSAutoFilterBot"
        
        print(f"📤 Posting {file_name} to channel {MOVIE_CHANNEL_ID}...")
        
        # Post to movie channel
        message = await bot.send_cached_media(
            chat_id=MOVIE_CHANNEL_ID,
            file_id=file_data['file_id'],
            caption=caption
        )
        
        print(f"✅ Posted message ID: {message.id}")
        
        # Generate message link
        message_link = await get_channel_message_link(MOVIE_CHANNEL_ID, message.id)
        
        if not message_link:
            # Fallback link generation
            message_link = f"https://t.me/c/{str(abs(MOVIE_CHANNEL_ID))}/{message.id}"
        
        print(f"🔗 Generated link: {message_link}")
        
        # Schedule auto-delete in 2 minutes
        task_key = f"{message.id}_{MOVIE_CHANNEL_ID}"
        delete_task = asyncio.create_task(
            delete_message_after_delay(message.id, MOVIE_CHANNEL_ID, 2, "channel_movie")
        )
        DELETE_TASKS[task_key] = delete_task
        
        return message_link
        
    except FloodWait as e:
        print(f"⏳ FloodWait while posting: {e}")
        await handle_flood_wait(e, "post_to_channel")
        return None
    except BadRequest as e:
        print(f"❌ BadRequest while posting: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Error posting to channel: {e}")
        print(f"❌ Full error posting to channel: {e}")
        return None

# --- BOT COMMAND HANDLERS ---
def setup_handlers(bot_instance):
    """Setup all bot command handlers"""
    
    # Store bot instance globally for use in other functions
    global bot
    bot = bot_instance
    
    @bot.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video) if CHANNEL_ID != 0 else filters.none)
    async def index_files(client, message):
        try:
            media = message.document or message.video
            if not media: 
                return

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
                "message_id": message.id,
                "channel_id": CHANNEL_ID,
                "caption": message.caption or filename,
                "timestamp": datetime.now().isoformat()
            }

            ref = db.reference(f'files/{media.file_unique_id}')
            ref.set(file_data)
            
            logger.info(f"✅ Indexed: {filename} (ID: {message.id})")
            print(f"✅ Indexed file: {filename}")

        except Exception as e:
            logger.error(f"❌ Error indexing file: {e}")
    
    @bot.on_message(filters.command("start") & filters.private)
    async def start_command(client, message):
        print(f"📨 Received /start from {message.from_user.id}")
        start_text = f"👋 **Hey {message.from_user.first_name}!**\n" \
                     f"Welcome to **BS Auto Filter Bot** 🎬\n\n" \
                     f"Send me a movie name and I'll search for it.\n\n" \
                     f"⚠️ **Auto-Delete Rules:**\n" \
                     f"• Movie posts auto-delete in **2 minutes** ⏰\n" \
                     f"• Search results auto-delete in **10 minutes** ⏰\n\n"
        
        if JOIN_CHANNEL_ID and JOIN_CHANNEL_ID != 0:
            start_text += f"📢 **Required:** Join our channel to access movies!\n"
        
        await message.reply_text(start_text)
    
    @bot.on_message(filters.command("channels") & filters.private)
    async def channels_command(client, message):
        """Show information about configured channels"""
        channels_info = "**📢 Configured Channels:**\n\n"
        
        if CHANNEL_ID != 0:
            try:
                source_chat = await bot.get_chat(CHANNEL_ID)
                channels_info += f"**Source Channel:**\n"
                channels_info += f"• Name: {source_chat.title}\n"
                channels_info += f"• ID: `{CHANNEL_ID}`\n"
                if hasattr(source_chat, 'username'):
                    channels_info += f"• Username: @{source_chat.username}\n"
                channels_info += f"• Role: Indexing files\n\n"
            except Exception as e:
                channels_info += f"**Source Channel:** Error: {str(e)[:50]}...\n\n"
        
        if MOVIE_CHANNEL_ID != 0:
            try:
                movie_chat = await bot.get_chat(MOVIE_CHANNEL_ID)
                channels_info += f"**Movie Channel:**\n"
                channels_info += f"• Name: {movie_chat.title}\n"
                channels_info += f"• ID: `{MOVIE_CHANNEL_ID}`\n"
                if hasattr(movie_chat, 'username'):
                    channels_info += f"• Username: @{movie_chat.username}\n"
                channels_info += f"• Role: Posting movies (2-min auto-delete)\n\n"
            except Exception as e:
                channels_info += f"**Movie Channel:** Error: {str(e)[:50]}...\n\n"
        
        if JOIN_CHANNEL_ID != 0:
            try:
                join_chat = await bot.get_chat(JOIN_CHANNEL_ID)
                channels_info += f"**Join Channel:**\n"
                channels_info += f"• Name: {join_chat.title}\n"
                channels_info += f"• ID: `{JOIN_CHANNEL_ID}`\n"
                if hasattr(join_chat, 'username'):
                    channels_info += f"• Username: @{join_chat.username}\n"
                channels_info += f"• Role: Required for access\n\n"
            except Exception as e:
                channels_info += f"**Join Channel:** Error: {str(e)[:50]}...\n\n"
        
        if CHANNEL_ID == 0 and MOVIE_CHANNEL_ID == 0 and JOIN_CHANNEL_ID == 0:
            channels_info += "No channels configured yet.\n"
        
        await message.reply_text(channels_info)
    
    @bot.on_message(filters.text & filters.private)
    async def search_handler(client, message):
        query = message.text.strip().lower()
        
        if query.startswith('/'):
            return
        
        # Check channel membership
        if JOIN_CHANNEL_ID and JOIN_CHANNEL_ID != 0:
            is_member = await check_user_in_channel(message.from_user.id)
            if not is_member:
                # Generate invite link
                invite_link = await generate_invite_link()
                
                if invite_link:
                    buttons = [[InlineKeyboardButton("🔗 Join Channel", url=invite_link)]]
                    await message.reply_text(
                        "⚠️ **Access Restricted!**\n\n"
                        "You need to join our channel to use this bot.\n"
                        "Please join the channel using the button below and try again.",
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                else:
                    await message.reply_text(
                        "⚠️ **Access Restricted!**\n\n"
                        "You need to join our channel to use this bot.\n"
                        "Please contact admin for access."
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
    
    @bot.on_callback_query()
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
                print(f"📤 User {cb.from_user.id} requested file: {file_data.get('file_name', 'Unknown')}")
                
                # Post movie to channel
                message_link = await post_movie_to_channel(file_data, cb.from_user.id)
                
                if not message_link:
                    await cb.answer("❌ Failed to post movie. Please try again.", show_alert=True)
                    return
                
                # Prepare buttons
                buttons = []
                
                if JOIN_CHANNEL_ID and JOIN_CHANNEL_ID != 0:
                    # Generate invite link for this user
                    invite_link = await generate_invite_link()
                    if invite_link:
                        buttons.append([InlineKeyboardButton("🔗 Join Channel", url=invite_link)])
                
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
                page_num = int(data[1])
                await cb.answer(f"Loading page {page_num}...")
                print(f"📄 User {cb.from_user.id} requested page {page_num}")
                
                await send_results_page(cb, cb.message, page=page_num)
                
            elif action == "noop":
                await cb.answer()

        except Exception as e:
            logger.error(f"Callback Error: {e}")
            print(f"❌ Callback error details: {e}")
            try:
                await cb.answer("❌ Error occurred. Please try again.", show_alert=True)
            except:
                pass
    
    print("✅ All bot handlers setup complete")
    return bot_instance

# --- START BOT WITH RETRY ---
async def start_bot():
    max_retries = 10
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            print(f"\n🚀 Starting bot (Attempt {attempt + 1}/{max_retries})...")
            
            # Create bot instance
            bot_instance = create_bot()
            if not bot_instance:
                print("❌ Failed to create bot instance")
                return None
            
            # Setup handlers
            bot_instance = setup_handlers(bot_instance)
            
            # Start the bot
            await bot_instance.start()
            print("✅ Bot started successfully!")
            
            # Get bot info
            me = await bot_instance.get_me()
            print(f"🤖 Bot Info:")
            print(f"   Name: {me.first_name}")
            print(f"   Username: @{me.username}")
            print(f"   ID: {me.id}")
            
            # Check bot permissions in channels
            if CHANNEL_ID != 0:
                try:
                    chat = await bot_instance.get_chat(CHANNEL_ID)
                    print(f"📁 Source Channel: {chat.title} (ID: {CHANNEL_ID})")
                except Exception as e:
                    print(f"⚠️ Cannot access source channel: {e}")
            
            if MOVIE_CHANNEL_ID != 0:
                try:
                    chat = await bot_instance.get_chat(MOVIE_CHANNEL_ID)
                    print(f"🎬 Movie Channel: {chat.title} (ID: {MOVIE_CHANNEL_ID})")
                except Exception as e:
                    print(f"⚠️ Cannot access movie channel: {e}")
            
            if JOIN_CHANNEL_ID != 0:
                try:
                    chat = await bot_instance.get_chat(JOIN_CHANNEL_ID)
                    print(f"🔗 Join Channel: {chat.title} (ID: {JOIN_CHANNEL_ID})")
                except Exception as e:
                    print(f"⚠️ Cannot access join channel: {e}")
            
            return bot_instance
            
        except FloodWait as e:
            wait_time = e.value or 60
            print(f"⏳ FloodWait: Need to wait {wait_time} seconds...")
            if attempt < max_retries - 1:
                print(f"💤 Waiting {wait_time} seconds before retry...")
                await asyncio.sleep(wait_time)
            else:
                print(f"❌ Max retries reached. Failed due to FloodWait.")
                return None
                
        except Exception as e:
            print(f"❌ Error starting bot (Attempt {attempt + 1}): {str(e)}")
            if attempt < max_retries - 1:
                print(f"💤 Waiting {retry_delay} seconds before retry...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                print(f"❌ Max retries reached.")
                return None
    
    return None

async def cancel_all_delete_tasks():
    """Cancel all pending delete tasks"""
    print("🗑️ Cancelling all pending delete tasks...")
    for task_key, task in list(DELETE_TASKS.items()):
        try:
            task.cancel()
        except:
            pass
    DELETE_TASKS.clear()

async def run_bot():
    """Main bot runner"""
    print("\n" + "="*50)
    print("BS AUTO FILTER BOT - DEBUG MODE")
    print("="*50)
    
    # Setup Firebase
    firebase_setup = setup_firebase()
    
    # Start bot
    bot_instance = await start_bot()
    
    if bot_instance:
        print("\n" + "="*50)
        print("✅ BOT IS RUNNING WITH ALL FEATURES!")
        print("="*50)
        print("\n🎯 Features Active:")
        print(f"   • Search with pagination ✓")
        print(f"   • Movie posting to channel ✓")
        print(f"   • Auto-delete system (2min/10min) ✓")
        print(f"   • Dynamic invite links ✓")
        print(f"   • Channel membership check ✓")
        print("\n📱 Test Commands:")
        print("   /start - Welcome message")
        print("   /channels - Check channel info")
        print("   Search any movie name")
        print("\n🔧 Debug Tips:")
        print("   • Check bot is admin in all channels")
        print("   • Verify channel IDs are correct")
        print("   • Check Firebase connection")
        print("="*50)
        
        try:
            # Keep bot running
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            print("\n🛑 Received shutdown signal...")
        except Exception as e:
            print(f"\n⚠️ Error in main loop: {e}")
        finally:
            # Clean shutdown
            print("🛑 Stopping bot...")
            await cancel_all_delete_tasks()
            if bot_instance:
                await bot_instance.stop()
                print("✅ Bot stopped cleanly")
    else:
        print("\n❌ FAILED TO START BOT")
        print("="*50)
        print("Troubleshooting:")
        print("1. ✅ Check all environment variables")
        print("2. 🔑 Verify bot token with @BotFather")
        print("3. 👑 Ensure bot is ADMIN in all channels")
        print("4. 🔄 Wait 5 mins if FloodWait error")
        print("5. 📞 Contact support if issues persist")
        print("="*50)
    
    print("👋 Bot process ended")

# --- MAIN FUNCTION ---
def main():
    # Start HTTP server in background thread
    print("🚀 Starting HTTP server...")
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Give HTTP server time to start
    time.sleep(2)
    
    # Start the bot
    print("🚀 Starting Telegram bot...")
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_bot())
        
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("👋 Application ended")

if __name__ == "__main__":
    # Initialize bot variable
    bot = None
    
    # Check required environment variables
    if not all([API_ID, API_HASH, BOT_TOKEN]):
        print("⚠️ WARNING: Missing required environment variables!")
        print("Running in minimal mode with only HTTP server...")
        run_http_server()
    else:
        main()
