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
        .theme-toggle { background: #f0f0f0; border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; border: none; }

        /* SEARCH BAR */
        .search-container { padding: 0 20px 10px 20px; position:relative; z-index: 101; }
        .search-box {
            background: #fff; border: 1px solid #e0e0e0; border-radius: 50px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.03); display: flex; align-items: center;
        }
        .search-box input {
            border: none; background: transparent; padding: 12px 20px; font-size: 15px; width: 100%; outline: none; border-radius: 50px;
        }
        .search-btn {
            background: var(--primary); color: white; border: none;
            width: 38px; height: 38px; border-radius: 50%;
            margin-right: 5px; display: flex; align-items: center; justify-content: center;
        }

        /* HERO CARD (TRENDING) */
        .hero-card {
            margin: 10px 20px 20px 20px;
            height: 420px;
            border-radius: 20px;
            background-size: cover; background-position: center;
            position: relative; overflow: hidden;
            box-shadow: 0 10px 20px rgba(0,0,0,0.2);
            display: flex; align-items: flex-end;
            cursor: pointer;
        }
        .hero-overlay {
            background: linear-gradient(to top, rgba(0,0,0,0.95), transparent 90%);
            width: 100%; padding: 25px; color: white;
            display: flex; flex-direction: column; gap: 10px;
        }
        .popular-pill {
            background: rgba(80, 80, 80, 0.6); backdrop-filter: blur(10px);
            padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 600;
            width: fit-content; display: flex; align-items: center; gap: 6px;
            position: absolute; top: 20px; left: 20px;
        }
        .hero-title { font-size: 28px; font-weight: 800; line-height: 1.1; margin-top: 20px; }
        .hero-desc { font-size: 13px; opacity: 0.8; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        
        .genre-tags { display: flex; gap: 8px; margin-top: 5px; }
        .genre-tag {
            background: rgba(255,255,255,0.2); backdrop-filter: blur(5px);
            padding: 5px 12px; border-radius: 15px; font-size: 12px; font-weight: 500;
        }

        /* SEARCH RESULTS LIST (LIKE PHOTO 1) */
        #searchResults {
            display: none; padding: 0 20px; margin-top: 10px;
        }
        .result-item {
            display: flex; gap: 15px; margin-bottom: 15px; cursor: pointer;
        }
        .result-img {
            width: 50px; height: 75px; border-radius: 8px; object-fit: cover; flex-shrink: 0; background: #eee;
        }
        .result-info {
            display: flex; flex-direction: column; justify-content: center; border-bottom: 1px solid #f0f0f0; flex-grow: 1; padding-bottom: 15px;
        }
        .result-title { font-size: 15px; font-weight: 600; color: #333; margin-bottom: 4px; }
        .result-meta { font-size: 13px; color: #888; }
        .na-img { display: flex; align-items: center; justify-content: center; font-size: 10px; color: #aaa; border: 1px solid #eee; }

        /* DETAILS PAGE */
        #detailsPage {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: #fff; z-index: 200; overflow-y: auto;
            transform: translateX(100%); transition: transform 0.25s ease;
            display: none;
        }
        #detailsPage.active { transform: translateX(0); display: block; }
        .back-btn { position: absolute; top: 15px; left: 15px; z-index: 10; background: #fff; border-radius: 50%; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .backdrop { width: 100%; height: 260px; object-fit: cover; mask-image: linear-gradient(to bottom, black 80%, transparent 100%); }
        .info-container { padding: 0 20px; margin-top: -30px; position: relative; }
        .btn-play { background: #ff0000; color: white; border: none; width: 100%; padding: 14px; border-radius: 12px; font-weight: 600; font-size: 15px; display: flex; align-items: center; justify-content: center; gap: 8px; margin-bottom: 20px; }
        
        /* FILE LIST */
        .file-card { display: flex; align-items: center; background: #fff; border: 1px solid #eee; border-radius: 12px; padding: 12px; margin-bottom: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.03); cursor: pointer; }
        .file-icon { width: 45px; height: 45px; background: #f1f3f5; border-radius: 8px; display: flex; align-items: center; justify-content: center; margin-right: 12px; color: #555; font-size: 20px; }
        .res-badge { font-size: 11px; font-weight: 700; color: white; padding: 4px 8px; border-radius: 6px; }
        .res-720 { background-color: var(--badge-720); } .res-1080 { background-color: var(--badge-1080); } .res-4k { background-color: var(--badge-2160); }
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

        <!-- HERO SECTION (Only visible when not searching) -->
        <div id="heroSection"></div>

        <!-- SEARCH RESULTS (Only visible when searching) -->
        <div id="searchResults"></div>

    </div>

    <!-- DETAILS VIEW -->
    <div id="detailsPage">
        <div class="back-btn" onclick="closeDetails()"><i class="fas fa-arrow-left"></i></div>
        <img id="dBackdrop" class="backdrop" src="">
        <div class="info-container">
            <h1 id="dTitle" style="font-size: 26px; font-weight: 800; margin-bottom: 5px;"></h1>
            <div style="display:flex; gap:10px; font-size:13px; color:#666; margin-bottom:15px;">
                <span style="border:1px solid #ccc; padding:0 4px; border-radius:3px; font-weight:700; color:#333;">PG-13</span>
                <span id="dGenres"></span> • <span id="dYear"></span>
            </div>

            <button class="btn-play"><i class="fas fa-play"></i> Play Trailer</button>
            
            <div style="font-size:14px; font-weight:bold; color:#f5c518; margin-bottom:15px;">
                <i class="fas fa-star"></i> <span id="dRating"></span> IMDb
            </div>

            <h3 style="font-size:18px; font-weight:700; margin-bottom:10px;">Overview</h3>
            <p id="dOverview" style="font-size:14px; color:#555; line-height:1.6; margin-bottom:20px;"></p>

            <h3 style="font-size:18px; font-weight:700; margin-bottom:10px;">Available Files</h3>
            <div id="fileListContainer"></div>
        </div>
    </div>

<script>
    const tg = window.Telegram.WebApp;
    tg.ready();
    tg.expand();

    const tmdbKey = "{{ tmdb_key }}";
    
    // Genre Map for Tags
    const genres = { 
        28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 
        80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family", 
        14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music", 
        9648: "Mystery", 10749: "Romance", 878: "Science Fiction", 
        10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western" 
    };

    // Load Trending on Start
    fetchTrending();

    // Search Logic
    let searchTimeout;
    const searchInput = document.getElementById('searchInput');
    const heroSection = document.getElementById('heroSection');
    const searchResults = document.getElementById('searchResults');

    searchInput.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        const query = e.target.value.trim();

        if (query.length > 0) {
            heroSection.style.display = 'none';
            searchResults.style.display = 'block';
            searchResults.innerHTML = '<div style="text-align:center; padding:20px; color:#888;"><i class="fas fa-spinner fa-spin"></i></div>';
            
            searchTimeout = setTimeout(() => performSearch(query), 500);
        } else {
            heroSection.style.display = 'block';
            searchResults.style.display = 'none';
            searchResults.innerHTML = '';
        }
    });

    async function fetchTrending() {
        try {
            const res = await fetch(`https://api.themoviedb.org/3/trending/movie/week?api_key=${tmdbKey}`);
            const data = await res.json();
            if (data.results && data.results.length > 0) {
                renderHero(data.results[0]);
            }
        } catch (e) { console.error(e); }
    }

    function renderHero(movie) {
        // Map Genre IDs to Names (Max 3)
        const genreNames = movie.genre_ids.slice(0, 3).map(id => genres[id] || "").filter(Boolean);
        const tagsHtml = genreNames.map(g => `<span class="genre-tag">${g}</span>`).join('');

        const html = `
            <div class="hero-card" onclick='openDetails(${JSON.stringify(movie)})' style="background-image: url('https://image.tmdb.org/t/p/w500${movie.poster_path}');">
                <div class="popular-pill"><i class="fas fa-fire" style="color:#ffa500;"></i> Now Popular</div>
                <div class="hero-overlay">
                    <div class="genre-tags">${tagsHtml}</div>
                    <div class="hero-title">${movie.title}</div>
                    <div class="hero-desc">${movie.overview}</div>
                </div>
            </div>
        `;
        heroSection.innerHTML = html;
    }

    async function performSearch(query) {
        try {
            const res = await fetch(`https://api.themoviedb.org/3/search/multi?api_key=${tmdbKey}&query=${query}`);
            const data = await res.json();
            
            searchResults.innerHTML = '';
            
            if (!data.results || data.results.length === 0) {
                searchResults.innerHTML = '<div style="padding:20px; text-align:center; color:#888;">No results found</div>';
                return;
            }

            data.results.forEach(item => {
                if (item.media_type !== 'movie' && item.media_type !== 'tv') return;
                
                const title = item.title || item.name;
                const year = (item.release_date || item.first_air_date || '').split('-')[0];
                const imgUrl = item.poster_path ? `https://image.tmdb.org/t/p/w200${item.poster_path}` : null;
                const imgHtml = imgUrl ? `<img src="${imgUrl}" class="result-img">` : `<div class="result-img na-img">N/A</div>`;
                const type = item.media_type === 'tv' ? 'TV Show' : 'Movie';

                const div = document.createElement('div');
                div.className = 'result-item';
                // Pass full object safely
                div.onclick = () => openDetails(item);
                div.innerHTML = `
                    ${imgHtml}
                    <div class="result-info">
                        <div class="result-title">${title}</div>
                        <div class="result-meta">${type} (${year})</div>
                    </div>
                `;
                searchResults.appendChild(div);
            });
        } catch (e) { console.error(e); }
    }

    // --- DETAILS & FILE LOGIC ---
    function openDetails(item) {
        document.getElementById('dBackdrop').src = item.backdrop_path ? `https://image.tmdb.org/t/p/w780${item.backdrop_path}` : '';
        document.getElementById('dTitle').innerText = item.title || item.name;
        document.getElementById('dYear').innerText = (item.release_date || item.first_air_date || 'N/A').split('-')[0];
        document.getElementById('dOverview').innerText = item.overview || "No description available.";
        document.getElementById('dRating').innerText = item.vote_average ? item.vote_average.toFixed(1) : "N/A";
        
        // Genres string
        const gList = item.genre_ids ? item.genre_ids.map(id => genres[id]).filter(Boolean).slice(0, 3).join(", ") : "Movie";
        document.getElementById('dGenres').innerText = gList;

        findFiles(item.title || item.name);
        document.getElementById('detailsPage').classList.add('active');
    }

    function closeDetails() {
        document.getElementById('detailsPage').classList.remove('active');
    }

    async function findFiles(query)
