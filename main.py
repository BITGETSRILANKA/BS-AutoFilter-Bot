import os
import json
import math
import logging
import asyncio
import threading
import random
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
API_ID = os.environ.get("API_ID", "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "0")
MOVIE_CHANNEL = os.environ.get("MOVIE_CHANNEL", "")
JOIN_CHANNEL = os.environ.get("JOIN_CHANNEL", "")
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# Debug print all env vars (hide sensitive values)
print("=" * 50)
print("ENVIRONMENT VARIABLES CHECK:")
print(f"API_ID: {'Set' if API_ID and API_ID != '0' else 'NOT SET'}")
print(f"API_HASH: {'Set' if API_HASH else 'NOT SET'}")
print(f"BOT_TOKEN: {'Set' if BOT_TOKEN else 'NOT SET'}")
print(f"CHANNEL_ID: {'Set' if CHANNEL_ID and CHANNEL_ID != '0' else 'NOT SET'}")
print(f"MOVIE_CHANNEL: {'Set' if MOVIE_CHANNEL else 'NOT SET'}")
print(f"JOIN_CHANNEL: {'Set' if JOIN_CHANNEL else 'NOT SET'}")
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
                "bot_status": "starting"
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

# --- CREATE BOT CLIENT ---
def create_bot():
    try:
        # Convert string IDs to integers
        api_id = int(API_ID) if API_ID and API_ID != "0" else None
        channel_id = int(CHANNEL_ID) if CHANNEL_ID and CHANNEL_ID != "0" else None
        
        if not api_id or not API_HASH or not BOT_TOKEN:
            print("❌ Missing API credentials")
            return None
            
        print("🤖 Creating bot client...")
        bot = Client(
            name="bs_auto_filter_bot",
            api_id=api_id,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=2,
            sleep_threshold=30
        )
        print("✅ Bot client created")
        return bot
    except Exception as e:
        print(f"❌ Error creating bot: {e}")
        return None

# --- BOT COMMAND HANDLERS ---
def setup_handlers(bot):
    """Setup all bot command handlers"""
    
    @bot.on_message(filters.command("start") & filters.private)
    async def start_command(client, message):
        print(f"📨 Received /start from {message.from_user.id}")
        await message.reply_text(
            f"👋 **Hey {message.from_user.first_name}!**\n"
            f"Welcome to **BS Auto Filter Bot** 🎬\n\n"
            f"Send me a movie name and I'll search for it."
        )

    @bot.on_message(filters.command("ping") & filters.private)
    async def ping_command(client, message):
        print(f"🏓 Received /ping from {message.from_user.id}")
        start_time = time.time()
        msg = await message.reply_text("🏓 Pong!")
        end_time = time.time()
        await msg.edit_text(f"🏓 Pong! `{round((end_time - start_time) * 1000, 2)}ms`")

    @bot.on_message(filters.command("status") & filters.private)
    async def status_command(client, message):
        print(f"📊 Received /status from {message.from_user.id}")
        await message.reply_text(
            "**🤖 Bot Status:**\n"
            "✅ Online and running\n"
            f"👤 User: {message.from_user.first_name}\n"
            f"🆔 ID: {message.from_user.id}\n"
            f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}"
        )

    @bot.on_message(filters.command("id") & filters.private)
    async def id_command(client, message):
        print(f"🆔 Received /id from {message.from_user.id}")
        await message.reply_text(
            f"**Your Info:**\n"
            f"👤 Name: {message.from_user.first_name}\n"
            f"🆔 ID: `{message.from_user.id}`\n"
            f"📝 Chat ID: `{message.chat.id}`"
        )

    @bot.on_message(filters.text & filters.private)
    async def text_handler(client, message):
        if message.text.startswith('/'):
            return
            
        print(f"📝 Received text from {message.from_user.id}: {message.text[:50]}...")
        await message.reply_text(
            f"🔍 You sent: `{message.text}`\n\n"
            f"✅ Bot is working! Search functionality will be added soon."
        )

    print("✅ Bot handlers setup complete")
    return bot

# --- START BOT WITH RETRY ---
async def start_bot():
    max_retries = 10
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            print(f"\n🚀 Starting bot (Attempt {attempt + 1}/{max_retries})...")
            
            # Create bot instance
            bot = create_bot()
            if not bot:
                print("❌ Failed to create bot instance")
                return None
            
            # Setup handlers
            bot = setup_handlers(bot)
            
            # Start the bot
            await bot.start()
            print("✅ Bot started successfully!")
            
            # Get bot info
            me = await bot.get_me()
            print(f"🤖 Bot Info:")
            print(f"   Name: {me.first_name}")
            print(f"   Username: @{me.username}")
            print(f"   ID: {me.id}")
            
            # Send startup notification
            try:
                await bot.send_message(
                    chat_id=me.id,
                    text=f"🤖 **Bot Started Successfully!**\n\n"
                         f"Name: {me.first_name}\n"
                         f"Username: @{me.username}\n"
                         f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                         f"✅ Ready to receive commands!"
                )
            except Exception as e:
                print(f"⚠️ Could not send startup message: {e}")
            
            return bot
            
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
                retry_delay *= 2  # Exponential backoff
            else:
                print(f"❌ Max retries reached.")
                return None
    
    return None

async def run_bot():
    """Main bot runner"""
    print("\n" + "="*50)
    print("BS AUTO FILTER BOT - STARTING")
    print("="*50)
    
    # Setup Firebase
    firebase_setup = setup_firebase()
    
    # Start bot
    bot = await start_bot()
    
    if bot:
        print("\n" + "="*50)
        print("✅ BOT IS RUNNING SUCCESSFULLY!")
        print("="*50)
        print("\n📱 Available Commands:")
        print("   /start - Start the bot")
        print("   /ping - Test bot response")
        print("   /status - Check bot status")
        print("   /id - Get your user ID")
        print("\n🌐 Health check available at:")
        print("   http://localhost:8080/")
        print("   http://localhost:8080/health")
        print("   http://localhost:8080/status")
        print("\n⏰ Time:", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        print("="*50)
        
        # Keep the bot running
        try:
            # Run forever
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            print("\n🛑 Received shutdown signal...")
        except Exception as e:
            print(f"\n⚠️ Error in main loop: {e}")
        finally:
            # Clean shutdown
            if bot:
                print("🛑 Stopping bot...")
                await bot.stop()
                print("✅ Bot stopped cleanly")
    else:
        print("\n❌ FAILED TO START BOT")
        print("="*50)
        print("Please check:")
        print("1. ✅ Environment variables are set")
        print("2. 🔑 Bot token is valid (check with @BotFather)")
        print("3. 🌐 Internet connectivity")
        print("4. ⏰ Wait if there's FloodWait restriction")
        print("5. 🔄 Restart the app after fixing issues")
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
        # Create event loop and run bot
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the bot
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
    # Check if we should run in simple mode (without full features)
    if not all([API_ID, API_HASH, BOT_TOKEN]):
        print("⚠️ WARNING: Missing required environment variables!")
        print("Running in minimal mode with only HTTP server...")
        
        # Just run HTTP server
        run_http_server()
    else:
        main()
