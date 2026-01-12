import os
import logging
import json
from flask import Flask, render_template_string, request, jsonify
import firebase_admin
from firebase_admin import credentials, db

# --- CONFIG ---
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
DB_URL = os.environ.get("DB_URL", "")
FIREBASE_KEY = os.environ.get("FIREBASE_KEY", "")

# --- FIREBASE INIT ---
# We check if firebase is already initialized to avoid conflicts with main.py
if not firebase_admin._apps and FIREBASE_KEY:
    try:
        cred = credentials.Certificate(json.loads(FIREBASE_KEY))
        firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
    except Exception as e:
        print(f"Firebase Error in App: {e}")

app_web = Flask(__name__)

# --- THE EXACT UI REPLICA ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>MovieClubFamily</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg: #ffffff;
            --text-main: #1a1a1a;
            --text-sec: #6c757d;
            --primary: #0088cc;
            --card-bg: #f8f9fa;
            --border: #e9ecef;
            --badge-720: #fd7e14;
            --badge-1080: #0d6efd;
            --badge-2160: #198754;
        }

        body {
            background-color: var(--bg);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            margin: 0; padding: 0;
            padding-bottom: 40px;
        }

        /* HEADER */
        .header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 15px 20px; background: #fff; position: sticky; top: 0; z-index: 100;
        }
        .brand { font-weight: 700; font-size: 18px; display: flex; align-items: center; gap: 10px; }
        .brand i { font-size: 24px; color: #333; }
        .theme-toggle { background: #f0f0f0; border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; border: none; cursor: pointer; }

        /* SEARCH BAR */
        .search-container { padding: 0 20px 20px 20px; }
        .search-box {
            position: relative;
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 50px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
            display: flex; align-items: center;
        }
        .search-box input {
            border: none; background: transparent; padding: 14px 20px; font-size: 16px; width: 100%; outline: none; border-radius: 50px;
        }
        .search-btn {
            background: var(--primary); color: white; border: none;
            width: 40px; height: 40px; border-radius: 50%;
            margin-right: 6px; display: flex; align-items: center; justify-content: center;
        }

        /* HOME CONTENT */
        .section-title { padding: 10px 20px; font-weight: 700; font-size: 18px; }
        
        .hero-card {
            margin: 20px;
            height: 400px;
            border-radius: 20px;
            background-size: cover; background-position: center;
            position: relative;
            overflow: hidden;
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            display: flex; align-items: flex-end;
            cursor: pointer;
        }
        .hero-overlay {
            background: linear-gradient(to top, rgba(0,0,0,0.9), transparent);
            width: 100%; padding: 20px; color: white;
        }
        .popular-badge {
            background: rgba(255,255,255,0.2); backdrop-filter: blur(5px);
            padding: 5px 12px; border-radius: 20px; font-size: 12px;
            display: inline-flex; align-items: center; gap: 5px; margin-bottom: 10px;
        }

        /* DETAILS PAGE */
        #detailsPage {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: #fff; z-index: 200; overflow-y: auto;
            transform: translateX(100%); transition: transform 0.3s ease;
            display: none;
        }
        #detailsPage.active { transform: translateX(0); display: block; }
        
        .back-btn {
            position: absolute; top: 15px; left: 15px; z-index: 10;
            background: rgba(255,255,255,0.8); border-radius: 50%;
            width: 40px; height: 40px; display: flex; align-items: center; justify-content: center;
        }

        .backdrop {
            width: 100%; height: 250px; object-fit: cover;
            mask-image: linear-gradient(to bottom, black 80%, transparent 100%);
        }
        
        .info-container { padding: 0 20px; margin-top: -30px; position: relative; }
        .movie-title { font-size: 24px; font-weight: 800; margin-bottom: 5px; }
        .meta-tags { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #666; margin-bottom: 15px; }
        .pg-badge { border: 1px solid #ccc; padding: 1px 4px; border-radius: 3px; font-size: 11px; font-weight: 700; color: #333; }
        
        .btn-play {
            background: #ff0000; color: white; border: none;
            width: 100%; padding: 12px; border-radius: 12px;
            font-weight: 600; font-size: 15px; display: flex; align-items: center; justify-content: center; gap: 8px;
            margin-bottom: 15px;
        }

        .section-header { font-size: 18px; font-weight: 700; margin: 20px 0 10px 0; }
        
        /* CAST */
        .cast-scroll { display: flex; gap: 15px; overflow-x: auto; padding-bottom: 10px; }
        .cast-item { min-width: 80px; text-align: center; }
        .cast-img { width: 70px; height: 70px; border-radius: 15px; object-fit: cover; margin-bottom: 5px; }
        .cast-name { font-size: 11px; font-weight: 700; }

        /* EXACT FILE LIST UI */
        .file-card {
            display: flex; align-items: center;
            background: #fff; border: 1px solid #eee;
            border-radius: 10px; padding: 10px; margin-bottom: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.03);
            cursor: pointer;
        }
        .file-card:active { background-color: #f5f5f5; }
        
        .file-icon {
            width: 45px; height: 45px; background: #eee;
            border-radius: 8px; display: flex; align-items: center; justify-content: center;
            margin-right: 12px; color: #555; font-size: 20px;
        }
        .file-details { flex: 1; overflow: hidden; }
        .file-name { font-size: 13px; font-weight: 600; color: #222; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .file-meta { font-size: 11px; color: #888; margin-top: 3px; display: flex; align-items: center; gap: 8px; }
        
        .res-badge {
            font-size: 11px; font-weight: 700; color: white;
            padding: 3px 8px; border-radius: 6px;
        }
        .res-720 { background-color: var(--badge-720); }
        .res-1080 { background-color: var(--badge-1080); }
        .res-4k { background-color: var(--badge-2160); }
        .res-sd { background-color: #6c757d; }

        .hidden { display: none; }
        .loader { text-align: center; margin-top: 20px; color: var(--primary); }

        /* Horizontal Scroll for Posters */
        .h-scroll { display: flex; overflow-x: auto; gap: 10px; padding: 0 20px 20px 20px; }
        .poster-card { min-width: 140px; border-radius: 10px; overflow: hidden; }
        .poster-card img { width: 100%; height: 210px; object-fit: cover; }
    </style>
</head>
<body>

    <!-- HOME VIEW -->
    <div id="homeView">
        <div class="header">
            <div class="brand"><i class="fas fa-lion"></i> MovieClubFamily</div>
            <button class="theme-toggle"><i class="fas fa-sun"></i></button>
        </div>

        <div class="search-container">
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="Search movies & TV series...">
                <button class="search-btn"><i class="fas fa-search"></i></button>
            </div>
        </div>

        <div id="homeContent">
            <!-- Dynamic Content -->
             <div class="loader" id="mainLoader"><i class="fas fa-spinner fa-spin"></i> Loading...</div>
        </div>
    </div>

    <!-- DETAILS VIEW (MODAL) -->
    <div id="detailsPage">
        <div class="back-btn" onclick="closeDetails()"><i class="fas fa-arrow-left"></i></div>
        <img id="dBackdrop" class="backdrop" src="">
        
        <div class="info-container">
            <h1 id="dTitle" class="movie-title"></h1>
            <div class="meta-tags">
                <span class="pg-badge">PG-13</span>
                <span id="dGenres">Action</span> • 
                <span id="dYear">2024</span>
            </div>

            <button class="btn-play"><i class="fas fa-play"></i> Play Trailer</button>
            
            <div style="font-size:14px; font-weight:bold; color:#f5c518; margin-bottom:10px;">
                <i class="fas fa-star"></i> <span id="dRating"></span> IMDb
            </div>

            <h3 class="section-header">Overview</h3>
            <p id="dOverview" style="font-size:13px; color:#555; line-height:1.5;"></p>

            <h3 class="section-header">Cast</h3>
            <div id="dCast" class="cast-scroll"></div>

            <h3 class="section-header">Available Files <span id="fileCount" style="font-size:14px; color:#888;"></span></h3>
            
            <!-- FILE LIST CONTAINER -->
            <div id="fileListContainer">
                <div class="loader"><i class="fas fa-spinner fa-spin"></i> Checking database...</div>
            </div>
        </div>
    </div>

<script>
    const tg = window.Telegram.WebApp;
    tg.ready();
    tg.expand();

    const tmdbKey = "{{ tmdb_key }}";
    
    // Init Home
    fetchPopular();

    // Search Listener
    let searchTimeout;
    document.getElementById('searchInput').addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        const query = e.target.value.trim();
        if (query.length > 2) {
            searchTimeout = setTimeout(() => performSearch(query), 800);
        } else if (query.length === 0) {
            fetchPopular(); // Reset to home
        }
    });

    async function fetchPopular() {
        const res = await fetch(`https://api.themoviedb.org/3/trending/movie/week?api_key=${tmdbKey}`);
        const data = await res.json();
        renderHome(data.results);
    }

    async function performSearch(query) {
        document.getElementById('homeContent').innerHTML = '<div class="loader"><i class="fas fa-spinner fa-spin"></i> Searching...</div>';
        const res = await fetch(`https://api.themoviedb.org/3/search/multi?api_key=${tmdbKey}&query=${query}`);
        const data = await res.json();
        renderHome(data.results);
    }

    function renderHome(items) {
        const container = document.getElementById('homeContent');
        container.innerHTML = '';

        if (!items || items.length === 0) {
            container.innerHTML = '<p style="text-align:center; color:#999;">No results found.</p>';
            return;
        }

        // Hero Item (First Result)
        const hero = items[0];
        if (hero.backdrop_path) {
            const heroEl = document.createElement('div');
            heroEl.className = 'hero-card';
            heroEl.style.backgroundImage = `url(https://image.tmdb.org/t/p/w500${hero.poster_path})`;
            heroEl.onclick = () => openDetails(hero);
            heroEl.innerHTML = `
                <div class="hero-overlay">
                    <div class="popular-badge"><i class="fas fa-fire"></i> Top Result</div>
                    <div style="font-size:24px; font-weight:800;">${hero.title || hero.name}</div>
                    <div style="font-size:13px; opacity:0.8;">${(hero.release_date || hero.first_air_date || '').split('-')[0]}</div>
                </div>
            `;
            container.appendChild(heroEl);
        }

        // Horizontal List for others
        const title = document.createElement('div');
        title.className = 'section-title';
        title.innerText = 'More Results';
        container.appendChild(title);

        const scroll = document.createElement('div');
        scroll.className = 'h-scroll';
        
        items.slice(1).forEach(item => {
            if (!item.poster_path) return;
            const card = document.createElement('div');
            card.className = 'poster-card';
            card.onclick = () => openDetails(item);
            card.innerHTML = `<img src="https://image.tmdb.org/t/p/w200${item.poster_path}">`;
            scroll.appendChild(card);
        });
        container.appendChild(scroll);
    }

    // --- DETAILS LOGIC ---
    async function openDetails(item) {
        // Populate UI
        document.getElementById('dBackdrop').src = item.backdrop_path ? `https://image.tmdb.org/t/p/w780${item.backdrop_path}` : '';
        document.getElementById('dTitle').innerText = item.title || item.name;
        document.getElementById('dYear').innerText = (item.release_date || item.first_air_date || 'N/A').split('-')[0];
        document.getElementById('dOverview').innerText = item.overview;
        document.getElementById('dRating').innerText = item.vote_average.toFixed(1);

        // Fetch Cast
        fetchCast(item.id, item.media_type || 'movie');
        
        // Find Files
        findFiles(item.title || item.name);

        // Show Page
        document.getElementById('detailsPage').classList.add('active');
    }

    function closeDetails() {
        document.getElementById('detailsPage').classList.remove('active');
    }

    async function fetchCast(id, type) {
        const res = await fetch(`https://api.themoviedb.org/3/${type}/${id}/credits?api_key=${tmdbKey}`);
        const data = await res.json();
        const castDiv = document.getElementById('dCast');
        castDiv.innerHTML = '';
        
        data.cast.slice(0, 10).forEach(c => {
            if(!c.profile_path) return;
            castDiv.innerHTML += `
                <div class="cast-item">
                    <img class="cast-img" src="https://image.tmdb.org/t/p/w200${c.profile_path}">
                    <div class="cast-name">${c.name}</div>
                </div>
            `;
        });
    }

    async function findFiles(query) {
        const listDiv = document.getElementById('fileListContainer');
        listDiv.innerHTML = '<div class="loader"><i class="fas fa-spinner fa-spin"></i> Finding files...</div>';
        
        const res = await fetch(`/api/search_db?query=${encodeURIComponent(query)}`);
        const files = await res.json();
        
        document.getElementById('fileCount').innerText = `(${files.length})`;
        listDiv.innerHTML = '';

        if (files.length === 0) {
            listDiv.innerHTML = '<p style="text-align:center; color:#999; margin-top:20px;">No files available yet.</p>';
            return;
        }

        files.forEach(f => {
            // Quality Detection for Badge
            let badgeClass = 'res-sd';
            let badgeText = 'SD';
            const name = f.file_name.toLowerCase();
            
            if (name.includes('2160p') || name.includes('4k')) { badgeClass = 'res-4k'; badgeText = '2160p'; }
            else if (name.includes('1080p')) { badgeClass = 'res-1080'; badgeText = '1080p'; }
            else if (name.includes('720p')) { badgeClass = 'res-720'; badgeText = '720p'; }

            // Size Formatting
            let size = (f.file_size / (1024*1024)).toFixed(0) + ' MB';
            if (f.file_size > 1024*1024*1024) size = (f.file_size / (1024*1024*1024)).toFixed(2) + ' GB';

            const div = document.createElement('div');
            div.className = 'file-card';
            div.onclick = () => tg.sendData(f.unique_id);
            div.innerHTML = `
                <div class="file-icon"><i class="fas fa-film"></i></div>
                <div class="file-details">
                    <div class="file-name">${f.file_name}</div>
                    <div class="file-meta">
                        <span>Size: ${size}</span>
                        <span><i class="fas fa-download"></i> 0</span>
                    </div>
                </div>
                <div class="res-badge ${badgeClass}">${badgeText}</div>
            `;
            listDiv.appendChild(div);
        });
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
    
    # Simplify query for better matching (remove year, symbols)
    simple_query = "".join(e for e in query if e.isalnum()).lower()[:10]

    ref = db.reference('files')
    snapshot = ref.get()
    
    results = []
    if snapshot:
        for key, val in snapshot.items():
            file_n = val.get('file_name', '').lower().replace(".", " ")
            if query in file_n:
                results.append(val)
    
    return jsonify(results[:50])

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    app_web.run(host='0.0.0.0', port=port)
