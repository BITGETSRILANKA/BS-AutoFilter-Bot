import firebase_admin
from firebase_admin import credentials, db
import json
import time
from config import DB_URL, FIREBASE_KEY, logger

# --- INITIALIZATION ---
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
        logger.error(f"❌ Firebase Init Error: {e}")

# --- IN-MEMORY CACHE (Improvement 1) ---
FILES_CACHE = []

def refresh_cache():
    """Loads all files from DB to RAM at startup."""
    global FILES_CACHE
    try:
        ref = db.reference('files')
        snapshot = ref.get()
        if snapshot:
            FILES_CACHE = list(snapshot.values())
        logger.info(f"🚀 Cache Refreshed: {len(FILES_CACHE)} files in RAM")
    except Exception as e:
        logger.error(f"Cache Refresh Error: {e}")

def add_file_to_db(file_data):
    """Adds file to DB and updates Cache immediately."""
    try:
        ref = db.reference(f'files/{file_data["unique_id"]}')
        ref.set(file_data)
        FILES_CACHE.append(file_data) # Update RAM
        return True
    except Exception as e:
        logger.error(f"DB Write Error: {e}")
        return False

def get_file_by_id(unique_id):
    """Finds file in RAM first (Faster)."""
    for file in FILES_CACHE:
        if file['unique_id'] == unique_id:
            return file
    # Fallback to DB
    ref = db.reference(f'files/{unique_id}')
    return ref.get()

# --- USER MANAGEMENT ---
def add_user(user_id):
    if user_id < 0: return
    try:
        ref = db.reference(f'users/{user_id}')
        if not ref.get():
            ref.set({"active": True})
    except: pass

def get_total_users():
    try:
        ref = db.reference('users')
        snap = ref.get()
        return len(snap) if snap else 0
    except: return 0

# --- PERSISTENT AUTO DELETE (Improvement 5) ---
def add_delete_task(chat_id, message_id, delete_time):
    """Saves delete task to DB so it survives restarts."""
    try:
        task_id = f"{chat_id}_{message_id}"
        ref = db.reference(f'delete_queue/{task_id}')
        ref.set({
            "chat_id": chat_id,
            "message_id": message_id,
            "delete_time": delete_time
        })
    except Exception as e:
        logger.error(f"Add Delete Task Error: {e}")

def get_due_delete_tasks():
    """Fetches tasks that are ready to be deleted."""
    try:
        ref = db.reference('delete_queue')
        snapshot = ref.get()
        tasks = []
        now = time.time()
        
        if snapshot:
            for key, val in snapshot.items():
                if val['delete_time'] <= now:
                    val['key'] = key
                    tasks.append(val)
        return tasks
    except Exception as e:
        return []

def remove_delete_task(key):
    try:
        db.reference(f'delete_queue/{key}').delete()
    except: pass
