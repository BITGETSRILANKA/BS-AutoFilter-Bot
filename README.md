# 🎬 BS AutoFilter Bot (Advanced)

A high-performance Telegram AutoFilter bot built with **Python (Pyrogram)** and **Firebase**. It features In-Memory caching for speed, persistent auto-deletion, and a force subscribe system.

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&repository=github.com/BITGETSRILANKA/BS-AutoFilter-Bot&branch=main&run_command=python+main.py)

---

## ✨ Key Features

*   **⚡ Zero-Latency Search:** Uses RAM caching to search thousands of files instantly.
*   **📢 Force Subscribe:** Users must join your update channel to use the bot.
*   **🔒 Content Protection:** Files cannot be forwarded or saved (Restricted Content).
*   **⏲️ Persistent Auto-Delete:**
    *   Files sent to users are auto-deleted after **2 minutes**.
    *   Tasks are saved to the database, so deletion works **even if the bot restarts**.
*   **📊 Admin Stats:** View live RAM usage, total users, and file counts via `/stats`.
*   **🔗 Deep Linking:** Smart links for groups and private chats.
*   **❤️ Health Check:** Built-in HTTP server to prevent sleeping on cloud platforms.

---

## 🛠️ Prerequisites

1.  **Telegram Bot Token:** From [@BotFather](https://t.me/BotFather).
2.  **API ID & Hash:** From [my.telegram.org](https://my.telegram.org).
3.  **Firebase Database:**
    *   Create a **Realtime Database** at [firebase.google.com](https://console.firebase.google.com/).
    *   Go to **Project Settings** > **Service Accounts**.
    *   Generate a **New Private Key** (JSON file). Open this file and copy the **entire text content**.

---

## ⚙️ Environment Variables

Set these in your VPS `config.py` or Cloud Dashboard (Koyeb/Heroku/Render).

| Variable | Description | Example |
| :--- | :--- | :--- |
| `API_ID` | Telegram API ID | `1234567` |
| `API_HASH` | Telegram API Hash | `abc1234...` |
| `BOT_TOKEN` | Bot Token from BotFather | `1234:AbCdEf...` |
| `CHANNEL_ID` | Channel to index files from | `-100123456789` |
| `ADMIN_ID` | **(NEW)** Your Telegram User ID for stats | `987654321` |
| `FSUB_CHANNEL_ID` | **(NEW)** Channel ID users must join | `-100987654321` |
| `FSUB_LINK` | **(NEW)** Invite link for that channel | `https://t.me/MyUpdates` |
| `DB_URL` | Firebase Database URL | `https://project.firebaseio.com` |
| `FIREBASE_KEY` | **Content** of serviceAccountKey.json | `{"type": "service...}` |
| `PORT` | (Optional) HTTP Port | `8080` |

> **⚠️ Note on FIREBASE_KEY:** Do not paste the file path. Paste the actual JSON code (curly braces and all).

---

## 🚀 Deployment

### Option 1: Koyeb (Recommended)
1.  Fork this repo.
2.  Click the **Deploy to Koyeb** button above.
3.  Fill in the Environment Variables.
4.  For `Run Command`, ensure it is: `python main.py`

### Option 2: VPS / Local
1.  Clone the repo:
    ```bash
    git clone https://github.com/YourUser/YourRepo.git
    cd YourRepo
    ```
2.  Install requirements:
    ```bash
    pip install -r requirements.txt
    ```
3.  Run:
    ```bash
    python3 main.py
    ```

---

## 🤖 Bot Setup (Crucial)

1.  **Inline Mode:** Go to **@BotFather** -> Bot Settings -> Inline Mode -> **Turn On**.
2.  **Admin Rights:**
    *   Add the bot to your **Indexing Channel** (`CHANNEL_ID`) as Admin.
    *   Add the bot to your **Force Sub Channel** (`FSUB_CHANNEL_ID`) as Admin.

---

## 📝 Commands

*   `/start` - Start the bot / Check subscription.
*   `/stats` - **(Admin Only)** Check RAM usage and database count.
*   **Search:** Type movie name in Group or PM.
*   **Inline:** Type `@YourBotName query` in any chat.

---

## ⚖️ License
This project is open-source.
