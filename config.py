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
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "") # <--- NEW

# Channels & Admins
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
FSUB_CHANNEL_ID = int(os.environ.get("FSUB_CHANNEL_ID", 0)) 
FSUB_LINK = os.environ.get("FSUB_LINK", "")

# Database
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# Settings
PORT = int(os.environ.get('PORT', 8080))
DELETE_DELAY = 120
