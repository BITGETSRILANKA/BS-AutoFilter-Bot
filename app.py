import os
import logging
import requests
import json
from flask import Flask, render_template_string, request, jsonify
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIG ---
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# --- FIREBASE INIT (Safe Check) ---
if not firebase_admin._apps:
    try:
        if FIREBASE_KEY:
            cred = credentials.Certificate(json.loads(FIREBASE_KEY))
            firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
    except Exception as e:
        print(f"Firebase Error in App: {e}")

app_web = Flask(__name__)

# --- THE PREMIUM UI (HTML/CSS/JS) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>MovieClub</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root { --bg: #0f1014; --card: #1e1f24; --accent: #e50914; --text: #ffffff; --subtext: #a1a1a1; }
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        
        body { background-color: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; padding-bottom: 80px; }
        
        /* HEADER */
        .header { position: sticky; top: 0; background: rgba(15, 16, 20, 0.95); backdrop-filter: blur(10px); padding: 15px; z-index: 100; border-bottom: 1px solid #333; }
        .search-box { position: relative; width: 100%; }
        .search-box input {
            width: 100%; background: #26272b; border: none; padding: 12px 45px 12px 15px;
            border-radius: 12px; color: white; font-size: 16px; outline: none; transition: 0.3s;
        }
        .search-box input:focus { background: #333; box-shadow: 0 0 0 2px var(--accent); }
        .search-box i { position: absolute; right: 15px; top: 50%; transform: translateY(-50%); color: var(--subtext); }

        /* MOVIE CARDS */
        .container { padding: 15px; }
        .movie-card { display: flex; background: var(--card); border-radius: 12px; margin-bottom: 15px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s; }
        .movie-card:active { transform: scale(0.98); }
        
        .poster { width: 100px; height: 150px; object-fit: cover; flex-shrink: 0; }
        .info { padding: 12px; flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between; }
        
        .title { font-size: 15px; font-weight: 700; line-height: 1.2; margin-bottom: 5px; }
        .meta { font-size: 12px; color: var(--subtext); display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .rating { color: #ffb400; font-weight: 600; display: flex; align-items: center; gap: 4px; }
        
        /* FILE LIST (Hidden by default) */
        .file-list { display: none; background: #15161a; padding: 10px; border-top: 1px solid #333; }
        .file-list.show { display: block; animation: slideDown 0.3s ease; }
        
        .file-btn {
            background: #2a2b30; color: white; padding: 12px; margin-top: 8px; border-radius: 8px;
            display: flex; justify-content: space-between; align-items: center; font-size: 13px; cursor: pointer;
        }
        .file-btn:active { background: var(--accent); }
        .file-info { display: flex; flex-direction: column; gap: 2px; }
        .file-size { font-size: 11px; color: var(--subtext); }
        .file-btn:active .file-size { color: rgba(255,255,255,0.8); }

        .btn-expand {
            background: rgba(255,255,255,0.1); border: none; color: white; padding: 6px 12px; 
            border-radius: 6px; font-size: 12px; width: fit-content; margin-top: auto;
        }

        /* UTILS */
        .hidden { display: none; }
        .loader { text-align: center; padding: 20px; color: var(--accent); }
        @keyframes slideDown { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>

    <div class="header">
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search movies, series...">
            <i class="fas fa-search"></i>
        </div>
    </div>

    <div id="loading" class="loader hidden"><i class="fas fa-spinner fa-spin fa-2x"></i></div>
    <div class="container" id="contentArea">
        <div style="text-align:center; color: #555; margin-top: 50px;">
            <i class="fas fa-film fa-3x"></i>
            <p style="margin-top:10px;">Search for a movie to begin</p>
        </div>
    </div>

<script>
    const tg = window.Telegram.WebApp;
    tg.ready();
    tg.expand();
    
    // Theme matching
    if (tg.colorScheme === 'light') {
        document.documentElement.style.setProperty('--bg', '#ffffff');
        document.documentElement.style.setProperty('--card', '#f0f2f5');
        document.documentElement.style.setProperty('--text', '#000000');
    }

    const tmdbKey = "{{ tmdb_key }}";
    let searchTimeout;

    document.getElementById('searchInput').addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        const query = e.target.value.trim();
        if (query.length > 2) {
            document.getElementById('loading').classList.remove('hidden');
            document.getElementById('contentArea').innerHTML = '';
            searchTimeout = setTimeout(() => performSearch(query), 800);
        }
    });

    async function performSearch(query) {
        try {
            // 1. Parallel Fetch (TMDB + Internal DB)
            const [tmdbRes, dbRes] = await Promise.all([
                fetch(`https://api.themoviedb.org/3/search/multi?api_key=${tmdbKey}&query=${query}`),
                fetch(`/api/search_db?query=${query}`)
            ]);

            const tmdbData = await tmdbRes.json();
            const dbFiles = await dbRes.json();
            
            document.getElementById('loading').classList.add('hidden');
            const content = document.getElementById('contentArea');

            if (!tmdbData.results || tmdbData.results.length === 0) {
                content.innerHTML = '<p style="text-align:center; margin-top:20px;">No results found.</p>';
                return;
            }

            tmdbData.results.forEach(item => {
                if (item.media_type !== 'movie' && item.media_type !== 'tv') return;

                // Match Files
                const title = item.title || item.name;
                const cleanTitle = title.toLowerCase().replace(/[^a-z0-9]/g, '');
                
                // Flexible matching logic
                const matchedFiles = dbFiles.filter(f => {
                    const fName = f.file_name.toLowerCase().replace(/[^a-z0-9]/g, '');
                    return fName.includes(cleanTitle.substring(0, 5)); // Simple match
                });

                // Only show if files exist (Optional: remove this if you want to show all movies)
                if (matchedFiles.length === 0) return;

                const poster = item.poster_path ? `https://image.tmdb.org/t/p/w200${item.poster_path}` : 'https://via.placeholder.com/100x150?text=No+Img';
                const year = (item.release_date || item.first_air_date || 'N/A').split('-')[0];
                const rating = item.vote_average ? item.vote_average.toFixed(1) : '0.0';
                
                // Build Card HTML
                const card = document.createElement('div');
                card.className = 'movie-card-wrapper'; // Wrapper for animation
                card.innerHTML = `
                    <div class="movie-card">
                        <img src="${poster}" class="poster">
                        <div class="info">
                            <div>
                                <div class="title">${title}</div>
                                <div class="meta">
                                    <span>${year}</span>
                                    <span class="rating"><i class="fas fa-star"></i> ${rating}</span>
                                    <span style="background:rgba(255,255,255,0.1); padding:2px 6px; border-radius:4px;">${item.media_type.toUpperCase()}</span>
                                </div>
                            </div>
                            <button class="btn-expand" onclick="toggleFiles(this)">
                                ${matchedFiles.length} Files Available <i class="fas fa-chevron-down"></i>
                            </button>
                        </div>
                    </div>
                    <div class="file-list">
                        ${matchedFiles.map(f => {
                            let size = (f.file_size / (1024*1024)).toFixed(2) + ' MB';
                            if (f.file_size > 1024*1024*1024) size = (f.file_size / (1024*1024*1024)).toFixed(2) + ' GB';
                            return `
                            <div class="file-btn" onclick="sendToBot('${f.unique_id}')">
                                <div class="file-info">
                                    <span>${f.file_name}</span>
                                    <span class="file-size">${size}</span>
                                </div>
                                <i class="fas fa-download"></i>
                            </div>`;
                        }).join('')}
                    </div>
                `;
                content.appendChild(card);
            });

        } catch (e) {
            console.error(e);
        }
    }

    window.toggleFiles = function(btn) {
        const list = btn.closest('.movie-card-wrapper').querySelector('.file-list');
        const icon = btn.querySelector('i');
        if (list.style.display === 'block') {
            list.style.display = 'none';
            icon.className = 'fas fa-chevron-down';
        } else {
            list.style.display = 'block';
            icon.className = 'fas fa-chevron-up';
        }
    }

    window.sendToBot = function(id) {
        tg.sendData(id);
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
    return "OK", 200

@app_web.route('/api/search_db')
def search_db():
    query = request.args.get('query', '').lower().strip()
    if not query: return jsonify([])
    
    ref = db.reference('files')
    snapshot = ref.get()
    
    results = []
    if snapshot:
        for key, val in snapshot.items():
            # Basic fuzzy search
            if query in val.get('file_name', '').lower().replace(".", " "):
                results.append(val)
    
    return jsonify(results[:50])

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    app_web.run(host='0.0.0.0', port=port)
