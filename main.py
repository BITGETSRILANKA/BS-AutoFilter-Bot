import os
import json
import logging
import asyncio
import threading
import requests
from flask import Flask, render_template_string, request, jsonify
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    WebAppInfo
)
from pyrogram.errors import MessageDeleteForbidden
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# NEW VARIABLES
URL = os.environ.get("URL", "") # Your Koyeb URL (e.g., https://app-name.koyeb.app)
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "") 

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
            logger.info("✅ Firebase Initialized")
        else:
            logger.error("❌ FIREBASE_KEY missing")
    except Exception as e:
        logger.error(f"❌ Firebase Error: {e}")

# --- FLASK WEB APP (THE UI) ---
app_web = Flask(__name__)

# HTML TEMPLATE 
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Movie Club</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        body { background-color: #1a1a1a; color: #fff; font-family: sans-serif; padding-bottom: 50px; }
        .search-container { position: sticky; top: 0; z-index: 1000; background: #1a1a1a; padding: 15px; }
        .movie-card { background: #2c2c2c; border: none; border-radius: 10px; margin-bottom: 15px; overflow: hidden; }
        .movie-poster { width: 100%; height: auto; border-radius: 10px 10px 0 0; }
        .file-item { background: #383838; padding: 10px; border-radius: 8px; margin-top: 5px; cursor: pointer; display: flex; align-items: center; justify-content: space-between; }
        .file-item:active { background: #505050; }
        .badge-res { font-size: 0.7rem; padding: 4px 6px; border-radius: 4px; }
        .hidden { display: none; }
        #loading { text-align: center; margin-top: 20px; }
    </style>
</head>
<body>

<div class="search-container">
    <div class="input-group">
        <span class="input-group-text bg-dark border-0 text-white"><i class="fas fa-search"></i></span>
        <input type="text" id="searchInput" class="form-control bg-dark border-0 text-white" placeholder="Search movies...">
    </div>
</div>

<div class="container" id="contentArea"></div>
<div id="loading" class="hidden"><div class="spinner-border text-light"></div></div>

<script>
    const tg = window.Telegram.WebApp;
    tg.ready();
    tg.expand();

    const tmdbKey = "{{ tmdb_key }}";
    let searchTimeout;

    document.getElementById('searchInput').addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        const query = e.target.value.trim();
        if (query.length > 2) {
            document.getElementById('loading').classList.remove('hidden');
            searchTimeout = setTimeout(() => performSearch(query), 800);
        }
    });

    async function performSearch(query) {
        const content = document.getElementById('contentArea');
        content.innerHTML = '';
        
        try {
            const tmdbRes = await fetch(`https://api.themoviedb.org/3/search/multi?api_key=${tmdbKey}&query=${query}`);
            const tmdbData = await tmdbRes.json();
            
            const dbRes = await fetch(`/api/search_db?query=${query}`);
            const dbData = await dbRes.json();

            document.getElementById('loading').classList.add('hidden');

            if (tmdbData.results) {
                tmdbData.results.forEach(item => {
                    if (item.media_type !== 'movie' && item.media_type !== 'tv') return;
                    
                    const poster = item.poster_path ? `https://image.tmdb.org/t/p/w500${item.poster_path}` : 'https://via.placeholder.com/500x750?text=No+Image';
                    const title = item.title || item.name;
                    const year = (item.release_date || item.first_air_date || '').split('-')[0];
                    const overview = item.overview ? item.overview.substring(0, 100) + '...' : 'No description.';

                    const cleanTitle = title.toLowerCase().replace(/[^a-z0-9]/g, '');
                    const matchingFiles = dbData.filter(f => f.file_name.toLowerCase().replace(/[^a-z0-9]/g, '').includes(cleanTitle.substring(0, 5)));

                    const card = document.createElement('div');
                    card.className = 'movie-card p-3';
                    
                    let filesHtml = '';
                    if (matchingFiles.length > 0) {
                        filesHtml = `<h6 class="mt-3 text-warning">Available Files (${matchingFiles.length})</h6>`;
                        matchingFiles.forEach(f => {
                            let size = (f.file_size / (1024*1024)).toFixed(2) + ' MB';
                            if (f.file_size > 1024*1024*1024) size = (f.file_size / (1024*1024*1024)).toFixed(2) + ' GB';
                            
                            filesHtml += `
                            <div class="file-item" onclick="sendToBot('${f.unique_id}')">
                                <div>
                                    <i class="fas fa-file-video text-info me-2"></i>
                                    <small>${f.file_name.substring(0, 30)}...</small>
                                </div>
                                <span class="badge bg-secondary badge-res">${size}</span>
                            </div>`;
                        });
                    } else {
                        filesHtml = `<p class="text-muted mt-2 small">No files currently available.</p>`;
                    }

                    card.innerHTML = `
                        <div class="d-flex">
                            <img src="${poster}" style="width: 80px; height: 120px; object-fit: cover; border-radius: 5px;">
                            <div class="ms-3">
                                <h5>${title} <span class="text-muted small">(${year})</span></h5>
                                <p class="small text-white-50">${overview}</p>
                            </div>
                        </div>
                        ${filesHtml}
                    `;
                    content.appendChild(card);
                });
            }
        } catch (e) {
            console.error(e);
        }
    }

    function sendToBot(unique_id) {
        tg.sendData(unique_id);
    }
</script>
</body>
</html>
"""

@app_web.route('/')
def home():
    return render_template_string(HTML_TEMPLATE, tmdb_key=TMDB_API_KEY)

@app_web.route('/health')
def health():
    return "Alive", 200

@app_web.route('/api/search_db')
def search_db():
    query = request.args.get('query', '').lower().strip()
    if not query: return jsonify([])
    
    ref = db.reference('files')
    snapshot = ref.get()
    
    results = []
    if snapshot:
        for key, val in snapshot.items():
            if query in val.get('file_name', '').lower().replace(".", " "):
                results.append(val)
    return jsonify(results[:50])

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app_web.run(host='0.0.0.0', port=port)

# --- TELEGRAM BOT ---
app = Client("BSAutoFilterBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
DELETE_TASKS = {}

async def delete_after(message, delay=120):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass

# --- 1. Custom Filter for Web App Data (FIXED) ---
# This manual filter works on all versions and prevents the crash
def web_data_filter(_, __, message):
    return bool(message.web_app_data)

web_data = filters.create(web_data_filter)

# --- 2. Start Command ---
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    web_app_url = URL  
    if not web_app_url.startswith("http"):
        await message.reply("⚠️ Error: `URL` Variable is missing or invalid in Koyeb.")
        return

    await message.reply_text(
        f"👋 **Hey {message.from_user.first_name}!**\n\n"
        "Click the button below to open the **Movie Club App**! 🎬\n",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Open Movie App", web_app=WebAppInfo(url=web_app_url))],
            [InlineKeyboardButton("🔍 Inline Search", switch_inline_query_current_chat="")]
        ])
    )

# --- 3. Handle Web App Data (FIXED HANDLER) ---
@app.on_message(web_data)
async def web_app_data_handler(client, message):
    try:
        unique_id = message.web_app_data.data
        
        ref = db.reference(f'files/{unique_id}')
        file_data = ref.get()
        
        if not file_data:
            await message.reply("❌ File no longer exists.", quote=True)
            return
            
        await message.reply(f"📂 **Retrieving:** `{file_data['file_name']}`...", quote=True)
        
        caption = f"🎬 **{file_data['file_name']}**\n\n⚠️ Auto-delete in 2 mins."
        sent_msg = await client.send_cached_media(
            chat_id=message.chat.id,
            file_id=file_data['file_id'],
            caption=caption
        )
        
        asyncio.create_task(delete_after(sent_msg, 120))
        
        await asyncio.sleep(1) 
        await start(client, message)
        
    except Exception as e:
        logger.error(f"Web App Error: {e}")

# --- 4. File Indexing ---
@app.on_message(filters.chat(CHANNEL_ID) & (filters.document | filters.video))
async def index_files(client, message):
    try:
        media = message.document or message.video
        if not media: return
        
        filename = getattr(media, "file_name", None) or f"Video_{message.id}.mp4"
        if message.caption: filename = message.caption.split("\n")[0].strip()

        file_data = {
            "file_name": filename,
            "file_size": media.file_size,
            "file_id": media.file_id,
            "unique_id": media.file_unique_id,
        }
        db.reference(f'files/{media.file_unique_id}').set(file_data)
        logger.info(f"Indexed: {filename}")
    except Exception as e:
        logger.error(e)

# --- MAIN ENTRY POINT ---
if __name__ == "__main__":
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    
    print("Bot & Web App Started...")
    app.run()
