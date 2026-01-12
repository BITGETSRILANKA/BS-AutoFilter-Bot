# Telegram Movie Search Bot

A Python bot that indexes files from a Channel to Firebase and allows users to search for them via DM with pagination.

## 🚀 Deployment on Koyeb

1. **Fork/Upload** this repo to GitHub.
2. Create a new App on **Koyeb**.
3. Select **GitHub** as source and choose this repo.
4. Set **Builder** to `Buildpack`.
5. Add the following **Environment Variables**:

| Variable Name | Description |
| :--- | :--- |
| `API_ID` | Your Telegram API ID (from my.telegram.org) |
| `API_HASH` | Your Telegram API Hash (from my.telegram.org) |
| `BOT_TOKEN` | Your Bot Token (from @BotFather) |
| `CHANNEL_ID` | Your Channel ID (Start with -100 e.g., -100123456789) |
| `DB_URL` | Your Firebase Database URL (e.g., https://xxx.firebaseio.com/) |
| `FIREBASE_KEY` | Open your firebase `.json` file, copy ALL text, paste here. |

## 🛠 Usage

1. **Add Bot to Channel:** Add the bot to your channel as an **Administrator**.
2. **Index Files:** Upload any `.mkv`, `.mp4` or `.avi` file to the channel. The bot will automatically save it to the database.
3. **Search:** Send the movie name to the bot in private chat.

## 📋 Features

- **Auto Indexing:** Saves file info immediately when posted in channel.
- **Search Engine:** Finds files by name.
- **Pagination:** Handles results > 10 with Next/Prev buttons.
- **Size Display:** Shows human-readable file size (GB, MB) on buttons.
