# 🎬 BS AutoFilter Bot

A smart and advanced Telegram AutoFilter bot that indexes files from a channel and allows users to search for them via Private Messages, Groups, or Inline Mode.

Powered by **Python (Pyrogram)** and **Firebase**, featuring auto-deletion of files and deep linking to prevent copyright strikes.

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&repository=github.com/YOUR_USERNAME/YOUR_REPO_NAME&branch=main&run_command=python+main.py)

---

## ✨ Features

*   **📂 Automatic Indexing:** Just upload a file to your channel, and the bot indexes it instantly.
*   **🔎 Smart Search:** Ignores dots, underscores, and hyphens (e.g., `spider.man` matches `Spider Man`).
*   **⚡ Inline Search:** Search for files in any chat by typing `@YourBotName query`.
*   **🔗 Deep Linking:**
    *   **In Groups:** Buttons redirect to the Bot's PM to download.
    *   **In PM:** Buttons download the file directly.
*   **⏲️ Auto-Delete System:**
    *   Downloaded files are deleted automatically after **2 minutes**.
    *   Search result messages are deleted automatically after **10 minutes**.
*   **☁️ Firebase Backend:** Fast and persistent storage.
*   **❤️ Health Check:** Built-in HTTP server to keep the bot running on cloud platforms (Koyeb, Render, etc.).

---

## 🛠️ Prerequisites

Before deploying, make sure you have the following:

1.  **Telegram Bot Token:** Get it from [@BotFather](https://t.me/BotFather).
2.  **API ID & API Hash:** Get them from [my.telegram.org](https://my.telegram.org).
3.  **Firebase Database:**
    *   Create a project at [firebase.google.com](https://console.firebase.google.com/).
    *   Create a **Realtime Database**.
    *   Generate a **Service Account Key** (JSON file) from *Project Settings > Service Accounts*.

---

## ⚙️ Environment Variables

You need to set these variables in your deployment environment (Koyeb, Heroku, .env file):

| Variable | Description | Example |
| :--- | :--- | :--- |
| `API_ID` | Your Telegram API ID | `1234567` |
| `API_HASH` | Your Telegram API Hash | `abcd123...` |
| `BOT_TOKEN` | Your Telegram Bot Token | `123456:ABC-DEF...` |
| `CHANNEL_ID` | The ID of the channel to index files from (Must start with -100) | `-1001234567890` |
| `DB_URL` | Your Firebase Database URL | `https://your-project.firebaseio.com` |
| `FIREBASE_KEY` | The **content** of your `serviceAccountKey.json` file. Copy the whole JSON text and paste it here. | `{"type": "service_account", ...}` |
| `PORT` | (Optional) Port for the health check server | `8080` |

---

## 🚀 How to Deploy on Koyeb

1.  Fork this repository.
2.  Click the **Deploy to Koyeb** button at the top of this README.
3.  In the Koyeb configuration page, add the **Environment Variables** listed above.
4.  **Important for `FIREBASE_KEY`:** Paste the *entire content* of the JSON file into the value field.
5.  Click **Deploy**.

---

## 🤖 Bot Settings (Required)

For the bot to work correctly, perform these steps in **@BotFather**:

1.  **Enable Inline Mode:**
    *   Send `/mybots` > Select your bot.
    *   Go to **Bot Settings** > **Inline Mode** > **Turn On**.
    *   (Optional) **Edit Inline Placeholder** > Set it to "Search movies..."

2.  **Add to Channel:**
    *   Add the bot to your indexing channel as an **Administrator** (so it can read messages).

---

## 📝 Commands

*   `/start` - Check if the bot is alive.
*   `/help` - Get help instructions.
*   **(In Group)** - Just type the movie name to search.
*   **(Inline)** - Type `@YourBotUsername movie_name` in any chat.

---

## ⚖️ License

This project is open-source. Feel free to modify and distribute.
