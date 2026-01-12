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
if not firebase_admin._apps and FIREBASE_KEY:
    try:
        cred = credentials.Certificate(json.loads(FIREBASE_KEY))
        firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})
    except Exception as e:
        print(f"Firebase Error: {e}")

app_web = Flask(__name__)

# --- UI TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>MovieClub</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Urbanist:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg: #0f1014;
            --surface: #1b1c21;
            --text-main: #ffffff;
            --text-sec: #a0a0a0;
            --accent: #e50914; /* Netflix Red */
            --badge-720: #ff9f1c;
            --badge-1080: #2ec4b6;
            --badge-4k: #d90429;
        }

        body {
            background-color: var(--bg);
            color: var(--text-main);
            font-family: 'Urbanist', sans-serif;
            margin: 0; padding: 0;
            padding-bottom: 50px;
            -webkit-font-smoothing: antialiased;
        }

        /* --- HEADER --- */
        .header {
            padding: 15px 20px;
            background: rgba(15, 16, 20, 0.95);
            backdrop-filter: blur(10px);
            position: sticky; top: 0; z-index: 100;
            display: flex; align-items: center; justify-content: space-between;
        }
        .brand { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; color: #fff; }
        .brand span { color: var(--accent); }

        /* --- SEARCH --- */
        .search-container { padding: 0 20px 10px 20px; position: sticky; top: 60px; z-index: 99; background: var(--bg); }
        .search-box {
            background: var(--surface);
            border-radius: 12px;
            display: flex; align-items: center;
            padding: 0 15px;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .search-box input {
            background: transparent; border: none; color: white;
            padding: 14px 10px; font-size: 15px; width: 100%; outline: none;
            font-family: 'Urbanist', sans-serif;
        }
        .search-box i { color: var(--text-sec); }

        /* --- HERO CARD --- */
        .hero-wrapper { padding: 10px 20px; }
        .hero-card {
            height: 400px;
            border-radius: 20px;
            background-size: cover; background-position: center;
            position: relative; overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            display: flex; align-items: flex-end;
            cursor: pointer;
        }
        .hero-overlay {
            background: linear-gradient(to top, var(--bg) 5%, transparent 100%);
            width: 100%; padding: 25px;
            display: flex; flex-direction: column; gap: 8px;
        }
        .trending-badge {
            background: var(--accent); color: white;
            font-size: 10px; font-weight: 700; text-transform: uppercase;
            padding: 4px 8px; border-radius: 6px; width: fit-content;
        }
        .hero-title { font-size: 28px; font-weight: 800; line-height: 1.1; text-shadow: 0 2px 10px rgba(0,0,0,0.5); }
        .hero-meta { font-size: 13px; color: rgba(255,255,255,0.8); display: flex; gap: 10px; align-items: center; }

        /* --- HORIZONTAL SECTIONS --- */
        .section-header {
            padding: 25px 20px 15px 20px;
            font-size: 18px; font-weight: 700;
            display: flex; align-items: center; gap: 10px;
        }
        .section-header i { color: var(--accent); font-size: 16px; }
        
        .scroll-container {
            display: flex; overflow-x: auto; gap: 15px; padding: 0 20px;
            scrollbar-width: none; /* Firefox */
        }
        .scroll-container::-webkit-scrollbar { display: none; } /* Chrome */

        .poster-card {
            min-width: 130px; width: 130px;
            display: flex; flex-direction: column; gap: 8px; cursor: pointer;
        }
        .poster-img {
            width: 100%; height: 195px; border-radius: 12px;
            object-fit: cover; background: var(--surface);
        }
        .poster-title {
            font-size: 13px; font-weight: 600;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            color: rgba(255,255,255,0.9);
        }

        /* --- SEARCH RESULTS --- */
        #searchResults { display: none; padding: 10px 20px; }
        .result-item {
            display: flex; gap: 15px; margin-bottom: 15px;
            background: var(--surface); padding: 10px; border-radius: 12px;
            align-items: center; cursor: pointer;
        }
        .result-img { width: 50px; height: 75px; border-radius: 8px; object-fit: cover; }
        .result-info { display: flex; flex-direction: column; gap: 4px; }
        .result-title { font-size: 15px; font-weight: 700; }
        .result-year { font-size: 12px; color: var(--text-sec); }

        /* --- DETAILS MODAL --- */
        #detailsPage {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: var(--bg); z-index: 200; overflow-y: auto;
            transform: translateY(100%); transition: transform 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
        }
        #detailsPage.active { transform: translateY(0); }
        
        .back-btn {
            position: absolute; top: 15px; left: 15px; z-index: 201;
            background: rgba(0,0,0,0.5); backdrop-filter: blur(5px);
            width: 40px; height: 40px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            color: white; border: 1px solid rgba(255,255,255,0.1);
        }
        
        .backdrop-container { position: relative; width: 100%; height: 350px; }
        .backdrop-img { width: 100%; height: 100%; object-fit: cover; }
        .backdrop-fade {
            position: absolute; bottom: 0; left: 0; width: 100%; height: 100%;
            background: linear-gradient(to bottom, transparent 40%, var(--bg) 100%);
        }

        .content-body { padding: 0 20px 40px 20px; position: relative; margin-top: -60px; }
        .movie-title-lg { font-size: 32px; font-weight: 800; line-height: 1.1; margin-bottom: 10px; }
        
        .meta-row { display: flex; gap: 15px; font-size: 14px; color: var(--text-sec); margin-bottom: 20px; align-items: center; }
        .rating-box { color: #ffd700; font-weight: 700; display: flex; align-items: center; gap: 5px; }
        
        .overview { font-size: 14px; line-height: 1.6; color: #d0d0d0; margin-bottom: 30px; }

        /* --- FILE LIST --- */
        .file-list-header { font-size: 16px; font-weight: 700; margin-bottom: 15px; border-left: 3px solid var(--accent); padding-left: 10px; }
        
        .file-card {
            background: var(--surface); padding: 15px; border-radius: 12px;
            display: flex; align-items: center; gap: 15px; margin-bottom: 10px;
            border: 1px solid rgba(255,255,255,0.05); cursor: pointer;
            transition: 0.2s;
        }
        .file-card:active { transform: scale(0.98); background: #25262c; }
        
        .file-icon {
            width: 40px; height: 40px; background: rgba(255,255,255,0.05);
            border-radius: 50%; display: flex; align-items: center; justify-content: center;
            color: var(--text-sec); font-size: 18px;
        }
        
        .file-info { flex: 1; overflow: hidden; }
        .file-name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #fff; }
        .file-size { font-size: 12px; color: var(--text-sec); margin-top: 3px; }
        
        .quality-badge {
            font-size: 10px; font-weight: 800; padding: 4px 8px; border-radius: 4px; color: #000;
        }
        .q-720 { background: var(--badge-720); }
        .q-1080 { background: var(--badge-1080); }
        .q-4k { background: var(--badge-4k); color: white; }

        .loader { text-align: center; padding: 30px; color: var(--text-sec); }
    </style>
</head>
<body>

    <!-- HOME PAGE -->
    <div id="homeView">
        <div class="header">
            <div class="brand">Movie<span>Club</span></div>
            <div style="font-size: 20px;"><i class="fas fa-user-circle"></i></div>
        </div>

        <div class="search-container">
            <div class="search-box">
                <i class="fas fa-search"></i>
                <input type="text" id="searchInput" placeholder="Search movies, series...">
            </div>
        </div>

        <!-- MAIN CONTENT SCROLL -->
        <div id="mainContent">
            
            <!-- HERO SECTION -->
            <div class="hero-wrapper" id="heroSection"></div>

            <!-- 1. NEWLY RELEASED -->
            <div class="section-header"><i class="fas fa-sparkles"></i> Newly Released</div>
            <div class="scroll-container" id="newReleases"></div>

            <!-- 2. THRILLER -->
            <div class="section-header"><i class="fas fa-user-secret"></i> Thriller Picks</div>
            <div class="scroll-container" id="thrillerSection"></div>

            <!-- 3. HORROR -->
            <div class="section-header"><i class="fas fa-ghost"></i> Horror Hits</div>
            <div class="scroll-container" id="horrorSection"></div>

        </div>

        <!-- SEARCH RESULTS LAYOUT -->
        <div id="searchResults"></div>
    </div>

    <!-- DETAILS MODAL -->
    <div id="detailsPage">
        <div class="back-btn" onclick="closeDetails()"><i class="fas fa-chevron-down"></i></div>
        
        <div class="backdrop-container">
            <img id="dBackdrop" class="backdrop-img" src="">
            <div class="backdrop-fade"></div>
        </div>

        <div class="content-body">
            <div class="movie-title-lg" id="dTitle"></div>
            
            <div class="meta-row">
                <span id="dYear"></span>
                <span id="dGenres"></span>
                <div class="rating-box"><i class="fas fa-star"></i> <span id="dRating"></span></div>
            </div>

            <div class="overview" id="dOverview"></div>

            <div class="file-list-header">Available Downloads</div>
            <div id="fileListContainer"></div>
        </div>
    </div>

<script>
    const tg = window.Telegram.WebApp;
    tg.ready();
    tg.expand();
    tg.setHeaderColor('#0f1014'); // Match bg color

    const tmdbKey = "{{ tmdb_key }}";
    
    // Initialize
    loadHomePage();

    // --- FETCH DATA LOGIC ---
    async function loadHomePage() {
        // 1. Trending for Hero
        fetchTrending();
        // 2. Newly Released (Now Playing)
        fetchSection(`https://api.themoviedb.org/3/movie/now_playing?api_key=${tmdbKey}`, 'newReleases');
        // 3. Thriller (Genre 53)
        fetchSection(`https://api.themoviedb.org/3/discover/movie?api_key=${tmdbKey}&with_genres=53&sort_by=popularity.desc`, 'thrillerSection');
        // 4. Horror (Genre 27)
        fetchSection(`https://api.themoviedb.org/3/discover/movie?api_key=${tmdbKey}&with_genres=27&sort_by=popularity.desc`, 'horrorSection');
    }

    async function fetchTrending() {
        try {
            const res = await fetch(`https://api.themoviedb.org/3/trending/movie/week?api_key=${tmdbKey}`);
            const data = await res.json();
            if (data.results && data.results.length > 0) {
                const m = data.results[0];
                const year = (m.release_date || '').split('-')[0];
                document.getElementById('heroSection').innerHTML = `
                    <div class="hero-card" onclick='openDetails(${JSON.stringify(m)})' 
                         style="background-image: url('https://image.tmdb.org/t/p/w780${m.poster_path}')">
                        <div class="hero-overlay">
                            <div class="trending-badge">#1 Trending</div>
                            <div class="hero-title">${m.title}</div>
                            <div class="hero-meta">
                                <span>${year}</span> • <span>${m.vote_average.toFixed(1)} <i class="fas fa-star" style="color:#ffd700"></i></span>
                            </div>
                        </div>
                    </div>
                `;
            }
        } catch(e) { console.error(e); }
    }

    async function fetchSection(url, containerId) {
        try {
            const res = await fetch(url);
            const data = await res.json();
            const container = document.getElementById(containerId);
            
            data.results.forEach(m => {
                if(!m.poster_path) return;
                const div = document.createElement('div');
                div.className = 'poster-card';
                div.onclick = () => openDetails(m);
                div.innerHTML = `
                    <img class="poster-img" src="https://image.tmdb.org/t/p/w300${m.poster_path}">
                    <div class="poster-title">${m.title}</div>
                `;
                container.appendChild(div);
            });
        } catch(e) { console.error(e); }
    }

    // --- SEARCH LOGIC ---
    let searchTimeout;
    const searchInput = document.getElementById('searchInput');
    const mainContent = document.getElementById('mainContent');
    const searchResults = document.getElementById('searchResults');

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        clearTimeout(searchTimeout);

        if(query.length > 1) {
            mainContent.style.display = 'none';
            searchResults.style.display = 'block';
            searchResults.innerHTML = '<div class="loader"><i class="fas fa-circle-notch fa-spin"></i></div>';
            searchTimeout = setTimeout(() => performSearch(query), 600);
        } else {
            mainContent.style.display = 'block';
            searchResults.style.display = 'none';
        }
    });

    async function performSearch(query) {
        const res = await fetch(`https://api.themoviedb.org/3/search/multi?api_key=${tmdbKey}&query=${query}`);
        const data = await res.json();
        searchResults.innerHTML = '';
        
        if(!data.results.length) {
            searchResults.innerHTML = '<div class="loader">No results found</div>';
            return;
        }

        data.results.forEach(item => {
            if(item.media_type !== 'movie' && item.media_type !== 'tv') return;
            const title = item.title || item.name;
            const year = (item.release_date || item.first_air_date || '').split('-')[0];
            const img = item.poster_path ? `https://image.tmdb.org/t/p/w200${item.poster_path}` : 'https://via.placeholder.com/50x75';
            
            const div = document.createElement('div');
            div.className = 'result-item';
            div.onclick = () => openDetails(item);
            div.innerHTML = `
                <img class="result-img" src="${img}">
                <div class="result-info">
                    <div class="result-title">${title}</div>
                    <div class="result-year">${item.media_type.toUpperCase()} • ${year}</div>
                </div>
            `;
            searchResults.appendChild(div);
        });
    }

    // --- DETAILS PAGE LOGIC ---
    function openDetails(item) {
        document.getElementById('dBackdrop').src = item.backdrop_path 
            ? `https://image.tmdb.org/t/p/w780${item.backdrop_path}` 
            : (item.poster_path ? `https://image.tmdb.org/t/p/w500${item.poster_path}` : '');
            
        document.getElementById('dTitle').innerText = item.title || item.name;
        document.getElementById('dYear').innerText = (item.release_date || item.first_air_date || 'N/A').split('-')[0];
        document.getElementById('dRating').innerText = item.vote_average ? item.vote_average.toFixed(1) : 'N/A';
        document.getElementById('dOverview').innerText = item.overview || "No synopsis available.";
        
        // Simple Genres
        // (In a real app, map IDs to names. For now, hardcode "Movie" or pass genres)
        document.getElementById('dGenres').innerText = item.media_type === 'tv' ? 'TV Series' : 'Movie';

        findFiles(item.title || item.name);
        document.getElementById('detailsPage').classList.add('active');
    }

    function closeDetails() {
        document.getElementById('detailsPage').classList.remove('active');
    }

    async function findFiles(query) {
        const listDiv = document.getElementById('fileListContainer');
        listDiv.innerHTML = '<div class="loader"><i class="fas fa-circle-notch fa-spin"></i> Checking Library...</div>';

        try {
            const res = await fetch(`/api/search_db?query=${encodeURIComponent(query)}`);
            const files = await res.json();
            listDiv.innerHTML = '';

            if (files.length === 0) {
                listDiv.innerHTML = '<div style="text-align:center; color: #555; padding: 20px;">No files available yet. <br> Request this movie!</div>';
                return;
            }

            files.forEach(f => {
                let badgeClass = 'q-720'; 
                let badgeText = '720p';
                const name = f.file_name.toLowerCase();
                if (name.includes('1080p')) { badgeClass = 'q-1080'; badgeText = '1080p'; }
                if (name.includes('2160p') || name.includes('4k')) { badgeClass = 'q-4k'; badgeText = '4K'; }
                
                let size = (f.file_size / (1024*1024)).toFixed(0) + ' MB';
                if (f.file_size > 1024*1024*1024) size = (f.file_size / (1024*1024*1024)).toFixed(2) + ' GB';

                const div = document.createElement('div');
                div.className = 'file-card';
                div.onclick = () => tg.sendData(f.unique_id);
                div.innerHTML = `
                    <div class="file-icon"><i class="fas fa-play"></i></div>
                    <div class="file-info">
                        <div class="file-name">${f.file_name}</div>
                        <div class="file-size">${size}</div>
                    </div>
                    <div class="quality-badge ${badgeClass}">${badgeText}</div>
                `;
                listDiv.appendChild(div);
            });

        } catch(e) {
            listDiv.innerHTML = '<div class="loader">Error loading files.</div>';
        }
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
    
    # Simple cleaner
    clean_q = "".join(e for e in query if e.isalnum()).lower()[:15]

    ref = db.reference('files')
    snapshot = ref.get()
    
    results = []
    if snapshot:
        for key, val in snapshot.items():
            f_name = val.get('file_name', '').lower().replace(".", " ")
            if query in f_name:
                results.append(val)
    
    # Return max 50 to prevent lag
    return jsonify(results[:50])

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    app_web.run(host='0.0.0.0', port=port)
