import psutil
import os
import requests
from pyrogram import enums
from config import FSUB_CHANNEL_ID, TMDB_API_KEY, logger

# --- SIZE FORMATTER ---
def get_size(size):
    if not size: return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

# --- FORCE SUB CHECK ---
async def get_fsub(client, message):
    # (Existing Fsub code remains here...)
    return True # Since you removed FSub, keep this returning True or paste your old code

# --- SYSTEM STATS ---
def get_system_stats():
    process = psutil.Process(os.getpid())
    ram_usage = get_size(process.memory_info().rss)
    return ram_usage

# --- TMDB SEARCH (NEW) ---
def search_tmdb(query):
    if not TMDB_API_KEY: return None
    
    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}&page=1"
        response = requests.get(url).json()
        
        if response.get('results'):
            # Get the first result
            top_result = response['results'][0]
            
            # Extract details
            title = top_result.get('title') or top_result.get('name') or "Unknown"
            overview = top_result.get('overview', 'No description available.')
            rating = top_result.get('vote_average', 0)
            poster = top_result.get('poster_path')
            
            if poster:
                poster_url = f"https://image.tmdb.org/t/p/w500{poster}"
            else:
                poster_url = None
                
            return {
                "title": title,
                "overview": overview[:500] + "...", # Truncate long desc
                "rating": rating,
                "poster": poster_url
            }
            
    except Exception as e:
        logger.error(f"TMDB Error: {e}")
        
    return None
