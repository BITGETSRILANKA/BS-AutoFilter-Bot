import psutil
import os
from pyrogram import enums
from pyrogram.errors import UserNotParticipant
from config import FSUB_CHANNEL_ID, logger

# --- SIZE FORMATTER ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

# --- FORCE SUB CHECK (Improvement 3) ---
async def get_fsub(client, message):
    if not FSUB_CHANNEL_ID: return True # If no ID set, skip check
    
    user_id = message.from_user.id
    try:
        member = await client.get_chat_member(FSUB_CHANNEL_ID, user_id)
        if member.status in [enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.LEFT]:
            return False
        return True
    except UserNotParticipant:
        return False
    except Exception as e:
        # If bot isn't admin or other error, let user pass to avoid blocking
        logger.warning(f"FSub Error: {e}") 
        return True

# --- SYSTEM STATS (Improvement 4) ---
def get_system_stats():
    process = psutil.Process(os.getpid())
    ram_usage = get_size(process.memory_info().rss)
    return ram_usage
