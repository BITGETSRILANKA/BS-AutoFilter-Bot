# ⚡ ＢＳ － ＡＵＴＯＦＩＬＴＥＲ ⚡

<p align="center">
  <img src="https://img.shields.io/badge/VERSION-2060.2-00f2ff?style=for-the-badge&logo=probot&logoColor=white" />
  <img src="https://img.shields.io/badge/CORE-PYTHON_3.10+-ffe600?style=for-the-badge&logo=python&logoColor=black" />
  <img src="https://img.shields.io/badge/DATABASE-FIREBASE-ffca28?style=for-the-badge&logo=firebase&logoColor=black" />
  <img src="https://img.shields.io/badge/DEPLOY-KOYEB-black?style=for-the-badge&logo=koyeb&logoColor=white" />
</p>

<p align="center">
  <em>High-performance Telegram Neural Interface. Features <strong>In-Memory Caching</strong>, <strong>Persistent Self-Destruct Protocols</strong>, and <strong>Smart Group Cleaning</strong>.</em>
</p>

---

## 🚀 ＩＭＭＥＤＩＡＴＥ _ ＤＥＰＬＯＹ

**Initialize System on Cloud Infrastructure:**

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&repository=github.com/BITGETSRILANKA/BS-AutoFilter-Bot&branch=main&run_command=python+bot.py)

---

## 🧬 ＳＹＳＴＥＭ _ ＣＡＰＡＢＩＬＩＴＩＥＳ

*   **⚡ Quantum RAM Caching**
    *   Zero-latency retrieval. Indexes thousands of files into volatile memory for instant access.
*   **📢 Neural Broadcast**
    *   Admins can transmit global signals (messages) to all active users via command.
*   **🧹 Auto-Purge Protocols**
    *   **Queries:** User inputs ("iron man") incinerated after **10 mins**.
    *   **404 Alerts:** "Not found" warnings self-destruct in **20 secs**.
    *   **File Links:** Media links dissolve after **2 mins**.
*   **🔒 DRM / Anti-Leak**
    *   Content is strictly restricted. Forwarding and saving are disabled at the protocol level.
*   **💾 Non-Volatile Task Memory**
    *   Deletion timers are stored in Firebase. If the bot reboots, it remembers what to kill.
*   **📊 Live Telemetry**
    *   Real-time monitoring of RAM usage, user count, and database load via `/stats`.
*   **🔗 Deep Link Navigation**
    *   Smart routing for private access to files.
*   **❤️ Life-Support Server**
    *   Integrated HTTP server keeps the bot awake on platforms like Koyeb/Render.

---

## 🛠️ ＩＮＩＴＩＡＬＩＺＡＴＩＯＮ _ ＲＥＱＵＩＲＥＭＥＮＴＳ

Before system launch, acquire the following credentials:

1.  **Telegram Bot Token:** via [@BotFather](https://t.me/BotFather).
2.  **API ID & Hash:** via [my.telegram.org](https://my.telegram.org).
3.  **Firebase Database:**
    *   Create a **Realtime Database** at [Firebase Console](https://console.firebase.google.com/).
    *   Navigate: **Project Settings** > **Service Accounts**.
    *   Action: **Generate New Private Key**.
    *   **CRITICAL:** Open the downloaded JSON file and copy the **entire text content**.

---

## ⚙️ ＣＯＮＦＩＧＵＲＡＴＩＯＮ _ ＭＡＴＲＩＸ

Set these variables in your **Koyeb Environment Settings** or `config.py`.

| Variable | Description | Example Data |
| :--- | :--- | :--- |
| `API_ID` | Telegram Application ID | `1234567` |
| `API_HASH` | Telegram Application Hash | `abc1234...` |
| `BOT_TOKEN` | Identity Token from BotFather | `1234:AbCdEf...` |
| `CHANNEL_ID` | Target Indexing Channel ID | `-100123456789` |
| `ADMIN_ID` | **(Required)** Your User ID | `987654321` |
| `DB_URL` | Firebase Database URL | `https://your-db.firebaseio.com` |
| `FIREBASE_KEY` | **Raw JSON Content** of Key | `{"type": "service_account"...}` |
| `PORT` | HTTP Server Port | `8080` |

> **⚠️ WARNING ON FIREBASE_KEY:** Do not paste a file path. You must paste the actual JSON code (curly braces and all) into the value field.

---

## 📡 ＤＥＰＬＯＹＭＥＮＴ _ ＶＥＣＴＯＲＳ

### 🔹 Vector A: Koyeb (Recommended)
1.  **Fork** this repository.
2.  Click the **Deploy to Koyeb** button at the top.
3.  Inject your **Environment Variables**.
4.  Ensure `Run Command` is set to:
    ```bash
    python bot.py
    ```

### 🔹 Vector B: Local / VPS
1.  **Clone Protocol:**
    ```bash
    git clone https://github.com/BITGETSRILANKA/BS-AutoFilter-Bot.git
    cd BS-AutoFilter-Bot
    ```
2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Ignition:**
    ```bash
    python3 bot.py
    ```

---

## 🤖 ＯＰＥＲＡＴＩＯＮＡＬ _ ＳＥＴＵＰ

1.  **Inline Mode Activation:**
    *   Go to **@BotFather** > Bot Settings > Inline Mode > **Turn On**.
2.  **Privilege Escalation:**
    *   Add the bot to your **Indexing Channel** (`CHANNEL_ID`) as **Admin**.
    *   Promote the bot to **Admin** in your groups to enable message deletion protocols.

---

## 🕹️ ＣＯＭＭＡＮＤ _ ＩＮＴＥＲＦＡＣＥ

| Command | Action | Permission |
| :--- | :--- | :--- |
| `/start` | Initialize Interface | User |
| `/stats` | View System Telemetry | **Admin** |
| `/broadcast` | Global Transmission | **Admin** |
| `/index [Link]`| Force Index Channel | **Admin** |
| `[Text]` | Search Movie (Group/PM) | User |
| `@BotName [Query]` | Inline Search | User |

---

<p align="center">
  <img src="https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcDdtY2J6Znh2eXJ4aXJ4aXJ4aXJ4aXJ4aXJ4/xT9IgusfDstACqHa9O/giphy.gif" width="100%">
</p>

<p align="center">
  <strong>SYSTEM ONLINE. END OF LINE.</strong>
</p>
