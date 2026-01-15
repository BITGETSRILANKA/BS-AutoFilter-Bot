import os
import logging

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("BSFilterBot")

# --- ENVIRONMENT VARIABLES ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Channels & Admins
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))       # Where files are uploaded
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))           # Your ID
FSUB_CHANNEL_ID = int(os.environ.get("FSUB_CHANNEL_ID", 0)) # Force Sub Channel ID
FSUB_LINK = os.environ.get("FSUB_LINK", "")             # Link to Force Sub Channel

# Database
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")       # JSON string content

# Settings
PORT = int(os.environ.get('PORT', 8080))
DELETE_DELAY = 120  # Seconds (2 Minutes)
