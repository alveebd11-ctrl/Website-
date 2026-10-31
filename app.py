import os
import re
import uuid
import time
import json
import asyncio
import random
import sqlite3
import threading
import traceback
import mimetypes
import hashlib
import hmac
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import requests
from flask import Flask, request, jsonify, send_file, abort, session, redirect, url_for, render_template_string, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# Telethon imports (Used for Login & Channel Reading)
from telethon import TelegramClient, utils, events as telethon_events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeAnimated

# Pyrogram imports (Used exclusively for Bot & Media Upload Handling)
try:
    from pyrogram import Client, filters
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo, InputMediaDocument
except ImportError:
    Client = None
    filters = None
    InlineKeyboardMarkup = None
    InlineKeyboardButton = None
    InputMediaPhoto = None
    InputMediaVideo = None
    InputMediaDocument = None


# ============================================================
# TELEGRAM CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8613982412:AAGEdvZdzjK3Eli_vIYoEtXnsxZgwodqnVE").strip()
API_ID = int(os.environ.get("API_ID", "32875278") or "32875278")
API_HASH = os.environ.get("API_HASH", "55d7b29aa48e8e160abe9e3cb91ba347").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6034439625") or "6034439625")

# Telethon user session for channel reader
TELETHON_SESSION_STRING = os.environ.get(
    "TELETHON_SESSION_STRING", "1BVtsOIgBu8Ld797Gm7tiQ9pMT34lKAGTW2wGgJXXC9kay-DU8hToIe6GugESCFCwGU2auCYj3cnflF5FvXQJGhsR2HRUfewLrlAyQJPPxLAxlc9ZCiBE1AJTPifPqgsKB395haHgTQKhggQyR-VeVG4LQeP0WlitlYALDA3gvhz4KAQHAm7sVZ1S9OK6rYyfvX0Qgh138P3n97duv52qIKuxUST2x6avkDEu2Y9VDATeAorW15QaGb6tmHdAPbzppCQtgI3fvY160HhfnZVW7RrrfM3StFrOAPkcWRp8tQnKMVq6xa5ZWEbkz2IeFIC1wJc37iHqM3cqpdufYZSSmqqxaomZsTg="
).strip()

# Storage channel ID normalization
_raw_channel_id = os.environ.get(
    "STORAGE_CHANNEL_ID", "4322205565"
).strip()

if not _raw_channel_id:
    STORAGE_CHANNEL_ID = 0
else:
    channel_digits = _raw_channel_id.lstrip("-")
    if not channel_digits.isdigit():
        raise ValueError("Invalid STORAGE_CHANNEL_ID")
    if channel_digits.startswith("100"):
        STORAGE_CHANNEL_ID = -int(channel_digits)
    else:
        STORAGE_CHANNEL_ID = -int("100" + channel_digits)

print("OC Storage Channel ID:", STORAGE_CHANNEL_ID)

WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "my_secure_webhook_secret_123").strip()

MAX_UPLOAD_MB = max(1, int(os.environ.get("MAX_UPLOAD_MB", "50")))
MAX_CHAT_TEXT_CHARS = 3000
CHAT_HISTORY_LIMIT_DEFAULT = 100
CHAT_BACKUP_MARKER = "[CHAT_BACKUP]"
CHAT_BACKUP_TEXT_MARKER = "[CHAT_BACKUP_TEXT]"

DB_PATH = Path(os.environ.get("CHAT_DB_PATH", str(BASE_DIR / "chat_messages.db")))
MEDIA_DIR = Path(os.environ.get("CHAT_MEDIA_DIR", str(BASE_DIR / "static" / "chat_media")))
BG_DIR = Path(os.environ.get("BACKGROUND_DIR", str(BASE_DIR / "static" / "backgrounds")))
CACHE_DIR = Path(os.environ.get("TELETHON_CACHE_DIR", str(BASE_DIR / "static" / "telethon_cache")))

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
BG_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "alvee_super_secret_key_12345")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(app, resources={r"/*": {"origins": ["http://localhost:8080", "https://carder.top", "https://alveecommunity.com", "http://alveecommunity.com", "*"]}})

total_logged_in_users = 0

# ============================================================
# TELEGRAM CLIENT GLOBALS & EVENT LOOPS
# ============================================================

telethon_reader_client = None
telethon_channel_entity = None
pyrogram_client = None
_services_started = False
_services_lock = threading.Lock()

# Telethon Background Loop
telethon_loop = asyncio.new_event_loop()
telethon_loop_ready = threading.Event()
telethon_thread = threading.Thread(
    target=lambda: _run_telethon_loop(),
    name="telethon-background-loop",
    daemon=True,
)

# Pyrogram Background Loop (For Bot Handling)
pyrogram_loop = asyncio.new_event_loop()
pyrogram_loop_ready = threading.Event()
pyrogram_thread = threading.Thread(
    target=lambda: _run_pyrogram_loop(),
    name="pyrogram-background-loop",
    daemon=True,
)

pending_uploads = {}
temp_client_sessions = {}
upload_lock = threading.Lock()


def _run_telethon_loop():
    asyncio.set_event_loop(telethon_loop)
    telethon_loop_ready.set()
    telethon_loop.run_forever()


def _run_pyrogram_loop():
    asyncio.set_event_loop(pyrogram_loop)
    pyrogram_loop_ready.set()
    pyrogram_loop.run_forever()


def run_async(coro, timeout=180):
    if not telethon_thread.is_alive():
        raise RuntimeError("Telethon background loop is not running.")
    if not telethon_loop_ready.wait(timeout=5):
        raise RuntimeError("Telethon background loop did not start.")
    future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
    return future.result(timeout=timeout)


def run_pyrogram_async(coro, timeout=180):
    if not pyrogram_thread.is_alive():
        raise RuntimeError("Pyrogram background loop is not running.")
    if not pyrogram_loop_ready.wait(timeout=5):
        raise RuntimeError("Pyrogram background loop did not start.")
    future = asyncio.run_coroutine_threadsafe(coro, pyrogram_loop)
    return future.result(timeout=timeout)


# ============================================================
# SQLite chat & channel posts storage
# ============================================================

def db_connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                client_message_id TEXT UNIQUE,
                client_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                media_filename TEXT,
                media_type TEXT,
                telegram_file_id TEXT,
                telegram_message_id INTEGER,
                telegram_chat_id TEXT,
                telegram_saved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_posts (
                message_id INTEGER PRIMARY KEY,
                text TEXT,
                media_type TEXT,
                mime_type TEXT,
                telegram_file_id TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_created_at
            ON chat_messages(created_at)
        """)
        conn.commit()


def row_to_message(row):
    if row is None:
        return None
    has_media = bool(row["media_filename"] or row["telegram_file_id"])
    return {
        "id": row["id"],
        "client_message_id": row["client_message_id"],
        "client_id": row["client_id"],
        "sender_name": row["sender_name"],
        "text": row["text"] or "",
        "media_url": f"/api/chat/media/{row['id']}" if has_media else "",
        "media_type": row["media_type"] or "",
        "created_at": row["created_at"],
        "telegram_saved": bool(row["telegram_saved"]),
    }


def get_message_by_id(message_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM chat_messages WHERE id = ?",
            (message_id,)
        ).fetchone()
    return row


def get_chat_stats():
    with db_connect() as conn:
        total_messages = conn.execute(
            "SELECT COUNT(*) FROM chat_messages"
        ).fetchone()[0]
        total_clients = conn.execute(
            "SELECT COUNT(DISTINCT client_id) FROM chat_messages"
        ).fetchone()[0]
    return {
        "total_chat_messages": int(total_messages or 0),
        "total_chat_clients": int(total_clients or 0),
    }


init_db()


# ============================================================
# Telegram Bot API helpers
# ============================================================

class TelegramAPIError(RuntimeError):
    pass


class TelegramNotConfiguredError(RuntimeError):
    pass


def telegram_api(method, data=None, files=None, timeout=(10, 180)):
    if not BOT_TOKEN:
        raise TelegramNotConfiguredError("BOT_TOKEN is not configured.")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, data=data or {}, files=files, timeout=timeout)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or not payload.get("ok"):
            description = payload.get("description") or f"Telegram API returned HTTP {response.status_code}"
            raise TelegramAPIError(description)
        return payload.get("result", {})
    except requests.RequestException as exc:
        raise TelegramAPIError(f"Telegram network error: {exc}") from exc


def send_telegram_message(chat_id, text, reply_markup=None, parse_mode=None):
    try:
        payload = {"chat_id": str(chat_id), "text": str(text)}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        if parse_mode:
            payload["parse_mode"] = parse_mode
        telegram_api("sendMessage", data=payload, timeout=(10, 60))
        return True
    except Exception as exc:
        print("Telegram sendMessage error:", exc)
        return False


def get_telegram_file_id(result):
    if not isinstance(result, dict):
        return None
    if result.get("photo"):
        photos = result["photo"]
        if photos:
            return photos[-1].get("file_id")
    for key in ("video", "document", "animation"):
        item = result.get(key)
        if isinstance(item, dict):
            return item.get("file_id")
    return None


def send_chat_archive_to_telegram(
    message_id: str,
    sender_name: str,
    text: str,
    created_at: str,
    client_id: str,
    media_path: Optional[Path],
    media_type: str,
    original_filename: str,
) -> Tuple[Optional[int], Optional[str], bool]:
    header = (
        f"{CHAT_BACKUP_MARKER}\n"
        f"ID: {message_id}\n"
        f"Sender: {sender_name}\n"
        f"Time: {created_at}\n"
    )
    if not media_path:
        archive_text = header + "\n" + (text or "(No text)")
        result = telegram_api(
            "sendMessage",
            data={
                "chat_id": str(STORAGE_CHANNEL_ID),
                "text": archive_text,
                "disable_web_page_preview": "true",
            },
        )
        return result.get("message_id"), None, True

    caption_limit = 950
    available_text = max(0, caption_limit - len(header) - 2)
    caption_text = (text or "")[:available_text]
    caption = header + (("\n" + caption_text) if caption_text else "")

    mime = (media_type or "").lower()
    if mime.startswith("video/"):
        method = "sendVideo"
        upload_field = "video"
    elif mime in ("image/jpeg", "image/png"):
        method = "sendPhoto"
        upload_field = "photo"
    else:
        method = "sendDocument"
        upload_field = "document"

    with open(media_path, "rb") as file_handle:
        result = telegram_api(
            method,
            data={
                "chat_id": str(STORAGE_CHANNEL_ID),
                "caption": caption,
            },
            files={
                upload_field: (
                    original_filename or media_path.name,
                    file_handle,
                    mime or "application/octet-stream",
                )
            },
        )
    return result.get("message_id"), get_telegram_file_id(result), True


def download_telegram_file(file_id):
    if not BOT_TOKEN or not file_id:
        return None, None
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=60,
        )
        payload = response.json()
        if not response.ok or not payload.get("ok"):
            return None, None
        file_path = payload["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        file_response = requests.get(download_url, timeout=120)
        if not file_response.ok:
            return None, None
        return file_response.content, file_path
    except Exception as exc:
        print("Telegram file download error:", exc)
        return None, None


# ============================================================
# Telethon Media Cache Helper (NEW)
# ============================================================

async def cache_channel_media_telethon(message_id: int) -> bool:
    """Download media from channel using Telethon and save to CACHE_DIR.
    Supports files far larger than 20 MB (up to ~2GB / 4GB).
    """
    global telethon_reader_client, telethon_channel_entity

    if not telethon_reader_client or not telethon_channel_entity:
        print(f"[Cache] Telethon not ready for message {message_id}")
        return False

    try:
        msgs = await telethon_reader_client.get_messages(telethon_channel_entity, ids=message_id)
        msg = msgs[0] if isinstance(msgs, (list, tuple)) else msgs

        if not msg or not getattr(msg, "media", None):
            print(f"[Cache] No media found for message {message_id}")
            return False

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Clean any previous partial files for this message_id
        for old in CACHE_DIR.glob(f"{message_id}*"):
            try:
                old.unlink(missing_ok=True)
            except Exception:
                pass

        target = CACHE_DIR / str(message_id)
        downloaded_path = await telethon_reader_client.download_media(msg, file=str(target))

        if downloaded_path:
            print(f"[Cache] Successfully cached message {message_id} → {downloaded_path}")
            return True

        print(f"[Cache] download_media returned None for {message_id}")
        return False

    except Exception as e:
        print(f"[Cache] Telethon error for message {message_id}: {e}")
        traceback.print_exc()
        return False


def find_cached_media(message_id: int) -> Optional[Path]:
    """Find the cached file for a given message_id (handles any extension)."""
    if not CACHE_DIR.exists():
        return None
    matches = list(CACHE_DIR.glob(f"{message_id}*"))
    # Prefer the largest file if multiple (rare)
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_size if p.is_file() else 0)


# ============================================================
# Channel History Auto-Recovery (NEW)
# ============================================================
# সার্ভার রিস্টার্ট বা কোড আপডেটের ফলে chat_messages.db এবং telethon_cache
# ফোল্ডার মুছে গেলেও, এই ফাংশনগুলো সার্ভার চালু হওয়ার সময় টেলিগ্রাম
# স্টোরেজ চ্যানেলের সম্পূর্ণ হিস্ট্রি আবার পড়ে channel_posts টেবিল এবং
# মিডিয়া ক্যাশ পুনরায় তৈরি করে দেয়। ছবি/ভিডিও কখনো "উধাও" হবে না।

async def sync_posts_from_channel():
    """টেলিগ্রাম স্টোরেজ চ্যানেলের সব পোস্ট আবার স্ক্যান করে channel_posts
    টেবিলে যা নেই তা যোগ করে দেয়, এবং ক্যাশে নেই এমন মিডিয়ার তালিকা তৈরি করে।"""
    global telethon_reader_client, telethon_channel_entity

    if not telethon_reader_client or not telethon_channel_entity:
        print("[Sync] Telethon reader not ready; skipping channel sync.")
        return

    try:
        with db_connect() as conn:
            existing_ids = {
                row["message_id"]
                for row in conn.execute("SELECT message_id FROM channel_posts").fetchall()
            }

        synced_count = 0
        to_cache = []

        async for msg in telethon_reader_client.iter_messages(telethon_channel_entity, limit=None):
            if not getattr(msg, "media", None):
                continue

            media_type = None
            mime_type = None

            if getattr(msg, "photo", None):
                media_type = "photo"
                mime_type = "image/jpeg"
            elif getattr(msg, "video", None):
                media_type = "video"
                mime_type = getattr(msg.video, "mime_type", None) or "video/mp4"
            elif getattr(msg, "document", None):
                doc_mime = (msg.document.mime_type or "").lower()
                is_animated = any(
                    isinstance(a, DocumentAttributeAnimated)
                    for a in (msg.document.attributes or [])
                )
                if doc_mime.startswith("video/"):
                    media_type = "video"
                elif is_animated:
                    media_type = "animation"
                elif doc_mime.startswith("image/"):
                    media_type = "photo"
                else:
                    # অন্য ধরনের ডকুমেন্ট (যেমন pdf) হলে এড়িয়ে যান
                    continue
                mime_type = doc_mime or "application/octet-stream"
            else:
                continue

            if msg.id not in existing_ids:
                created_at = (msg.date or datetime.now(timezone.utc)).isoformat()
                try:
                    with db_connect() as conn:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO channel_posts (
                                message_id, text, media_type, mime_type, telegram_file_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (msg.id, msg.message or "", media_type, mime_type, None, created_at)
                        )
                        conn.commit()
                    existing_ids.add(msg.id)
                    synced_count += 1
                except Exception as insert_err:
                    print(f"[Sync] Could not insert message {msg.id}: {insert_err}")

            if not find_cached_media(msg.id):
                to_cache.append(msg.id)

        if synced_count:
            print(f"[Sync] Restored {synced_count} post(s) from Telegram channel history.")
        else:
            print("[Sync] channel_posts table already up to date.")

        if to_cache:
            print(f"[Sync] {len(to_cache)} media file(s) need re-caching; starting background download...")
            asyncio.create_task(recache_missing_media(to_cache))

    except Exception:
        print("[Sync] Channel sync failed:", traceback.format_exc())


async def recache_missing_media(message_ids):
    """ক্যাশে নেই এমন মিডিয়া ব্যাকগ্রাউন্ডে ধীরে ধীরে আবার ডাউনলোড করে,
    যাতে টেলিগ্রাম রেট-লিমিটে না পড়তে হয়।"""
    for mid in message_ids:
        try:
            await cache_channel_media_telethon(mid)
        except Exception as e:
            print(f"[Sync] Failed to re-cache message {mid}: {e}")
        await asyncio.sleep(0.5)
    print(f"[Sync] Background re-caching finished for {len(message_ids)} item(s).")


# ============================================================
# Telegram Login Endpoints
# ============================================================

@app.route("/send-code", methods=["POST"])
def send_code_api():
    req_data = request.get_json(silent=True) or {}
    phone = req_data.get("phone", "").strip()
    
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required."}), 400

    async def async_send():
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
            phone_code_hash = sent.phone_code_hash
            temp_client_sessions[phone] = client
            return {"status": "success", "phone_code_hash": phone_code_hash}
        except Exception as e:
            if client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return {"status": "error", "message": str(e)}

    try:
        res = run_async(async_send(), timeout=180)
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/verify-code", methods=["POST"])
def verify_code_api():
    req_data = request.get_json(silent=True) or {}
    phone = req_data.get("phone", "").strip()
    code = req_data.get("code", "").strip()
    phone_code_hash = req_data.get("phone_code_hash", "").strip()

    client = None
    if phone and phone in temp_client_sessions:
        client = temp_client_sessions.get(phone)
    elif temp_client_sessions:
        phone = list(temp_client_sessions.keys())[0]
        client = temp_client_sessions.get(phone)

    if not client:
        return jsonify({"status": "error", "message": "Session expired or phone missing. Please request code again."}), 400

    async def async_verify():
        try:
            if not client.is_connected():
                await client.connect()

            if not phone_code_hash or not code:
                return {"status": "error", "message": "Missing code or hash."}
            
            try:
                await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            except Exception as sign_in_err:
                err_str = str(sign_in_err).lower()
                if "session password needed" not in err_str:
                    raise sign_in_err
            
            global total_logged_in_users
            total_logged_in_users += 1
            if ADMIN_ID:
                send_telegram_message(ADMIN_ID, f"🎉 Successful Telegram Login (OTP Verified)!\n📱 Phone: {phone}")
            
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            if phone:
                temp_client_sessions.pop(phone, None)
            if client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass

    try:
        res = run_async(async_verify(), timeout=180)
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================================================
# Secure Telegram Profile API
# ============================================================

@app.route("/auth/telegram", methods=["POST"])
def telegram_login_auth():
    payload = request.get_json(silent=True) or {}
    received_hash = str(payload.pop("hash", "")).strip()

    if not BOT_TOKEN or not received_hash:
        return jsonify({"status": "error", "message": "Telegram authentication is not configured."}), 400

    try:
        auth_date = int(payload.get("auth_date", 0))
        telegram_id = int(payload.get("id", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid Telegram login data."}), 400

    now = int(time.time())
    if telegram_id <= 0 or auth_date <= 0:
        return jsonify({"status": "error", "message": "Missing Telegram user ID or login time."}), 400

    if auth_date > now + 60 or now - auth_date > 86400:
        return jsonify({"status": "error", "message": "Telegram login data has expired."}), 401

    data_check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(received_hash, expected_hash):
        return jsonify({"status": "error", "message": "Telegram authentication failed."}), 401

    first_name = str(payload.get("first_name", "")).strip()
    last_name = str(payload.get("last_name", "")).strip()
    username = str(payload.get("username", "")).strip()
    photo = str(payload.get("photo_url", "")).strip()

    if not photo.startswith("https://"):
        photo = ""

    session["telegram_profile"] = {
        "id": telegram_id,
        "name": " ".join(part for part in (first_name, last_name) if part) or "Telegram User",
        "username": username,
        "photo": photo,
        "phone": "",
        "bio": ""
    }
    session["telegram_auth_date"] = auth_date
    return jsonify({"status": "success"})


@app.route("/get-user-profile", methods=["POST"])
def get_user_profile_api():
    profile = session.get("telegram_profile")
    auth_date = session.get("telegram_auth_date", 0)

    if not isinstance(profile, dict):
        return jsonify({"status": "error", "message": "Please sign in using Telegram Login."}), 401

    try:
        if int(time.time()) - int(auth_date) > 86400:
            session.pop("telegram_profile", None)
            session.pop("telegram_auth_date", None)
            return jsonify({"status": "error", "message": "Your login has expired. Please sign in again."}), 401
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid login session."}), 401

    return jsonify({
        "status": "success",
        "profile": {
            "id": profile.get("id"),
            "name": profile.get("name", ""),
            "username": profile.get("username", ""),
            "phone": profile.get("phone", ""),
            "bio": profile.get("bio", ""),
            "photo": profile.get("photo", "")
        }
    })


# ============================================================
# TELETHON READER INITIALIZATION
# ============================================================

async def start_telethon_reader():
    global telethon_reader_client, telethon_channel_entity

    if not TELETHON_SESSION_STRING:
        print("TELETHON_SESSION_STRING is missing.")
        return False

    max_retries = 5
    for attempt in range(1, max_retries + 1):
        client = TelegramClient(StringSession(TELETHON_SESSION_STRING), API_ID, API_HASH)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"Telethon attempt {attempt}: Session is not authorized.")
                await client.disconnect()
                await asyncio.sleep(3)
                continue

            channel_entity = None
            try:
                channel_entity = await client.get_entity(STORAGE_CHANNEL_ID)
            except Exception:
                async for dialog in client.iter_dialogs(limit=None):
                    try:
                        resolved_id = utils.get_peer_id(dialog.entity)
                        if int(resolved_id) == int(STORAGE_CHANNEL_ID):
                            channel_entity = dialog.entity
                            break
                    except (TypeError, ValueError):
                        continue

            if channel_entity is None:
                print(f"Telethon attempt {attempt}: Storage channel not found.")
                await client.disconnect()
                await asyncio.sleep(3)
                continue

            telethon_reader_client = client
            telethon_channel_entity = channel_entity
            print("Telethon reader connected and channel entity resolved successfully.")
            return True

        except Exception as e:
            print(f"Telethon connection attempt {attempt} failed: {e}")
            if client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(5)

    print("All Telethon reader connection attempts failed.")
    return False


# ============================================================
# API: GET TELEGRAM CHANNEL POSTS FROM SQLITE (Fixed Sequencing)
# ============================================================

@app.route("/api/channel/posts", methods=["GET"])
@app.route("/api/get_videos", methods=["GET"])
def get_channel_posts_api():
    try:
        with db_connect() as conn:
            # সঠিক সিরিয়াল মেইনটেইন করার জন্য ASC (পুরোনো থেকে নতুন) অথবা DESC সাজিয়ে পাঠানো
            rows = conn.execute(
                "SELECT * FROM channel_posts ORDER BY message_id ASC LIMIT 100"
            ).fetchall()

        posts = []
        for row in rows:
            posts.append({
                "id": row["message_id"],
                "message_id": row["message_id"],
                "text": row["text"] or "",
                "media_type": row["media_type"] or "photo",
                "mime_type": row["mime_type"] or "image/jpeg",
                "media_url": f"/api/channel/media/{row['message_id']}",
                "date": row["created_at"] or "",
                "has_media": True
            })

        videos = [
            post["media_url"]
            for post in posts
            if post["media_type"] in ("video", "animation")
        ]

        return jsonify({
            "ok": True,
            "status": "success",
            "count": len(posts),
            "posts": posts,
            "videos": videos
        })
    except Exception:
        error_id = uuid.uuid4().hex[:8]
        app.logger.exception("Channel posts fetch failed. Error ID: %s", error_id)
        return jsonify({
            "ok": False,
            "status": "error",
            "error": "Unable to read channel history.",
            "error_id": error_id
        }), 502


# ============================================================
# API: STREAM TELEGRAM MEDIA VIA CACHE / BOT API (UPDATED)
# ============================================================

@app.route("/api/channel/media/<int:message_id>", methods=["GET"])
def get_channel_media(message_id):
    try:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_posts WHERE message_id = ?",
                (message_id,)
            ).fetchone()

        if not row:
            return jsonify({"ok": False, "error": "Media not found."}), 404

        media_type = (row["media_type"] or "photo").lower()
        if media_type in ("video", "animation"):
            default_mime = "video/mp4"
        else:
            default_mime = "image/jpeg"

        # ---------- 1. Prefer local Telethon cache ----------
        cached_path = find_cached_media(message_id)
        if cached_path and cached_path.is_file():
            mime_type = mimetypes.guess_type(str(cached_path))[0] or default_mime
            return send_file(
                cached_path,
                mimetype=mime_type,
                conditional=True,
                as_attachment=False
            )

        # ---------- 1b. Cache missing: try Telethon download on-demand ----------
        try:
            cached_ok = run_async(cache_channel_media_telethon(message_id), timeout=120)
        except Exception as e:
            cached_ok = False
            print(f"[Media] On-demand Telethon cache failed for {message_id}: {e}")

        if cached_ok:
            cached_path = find_cached_media(message_id)
            if cached_path and cached_path.is_file():
                mime_type = mimetypes.guess_type(str(cached_path))[0] or default_mime
                return send_file(
                    cached_path,
                    mimetype=mime_type,
                    conditional=True,
                    as_attachment=False
                )

        # ---------- 2. Fallback: Bot API download + cache it ----------
        file_id = row["telegram_file_id"]
        if not file_id:
            return jsonify({"ok": False, "error": "Media not found."}), 404

        content, telegram_path = download_telegram_file(file_id)
        if content is None:
            return jsonify({"ok": False, "error": "Telegram media download failed."}), 404

        # Save to cache for next time (even if it came from Bot API)
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            # Try to keep a sensible extension
            ext = Path(telegram_path or "").suffix or (".mp4" if media_type in ("video", "animation") else ".jpg")
            cache_file = CACHE_DIR / f"{message_id}{ext}"
            with open(cache_file, "wb") as f:
                f.write(content)
            print(f"[Cache] Saved Bot-API fallback for {message_id} → {cache_file}")
        except Exception as e:
            print(f"[Cache] Failed to write fallback cache: {e}")

        mime_type = mimetypes.guess_type(telegram_path or "")[0] or default_mime
        file_size = len(content)

        range_header = request.headers.get('Range', None)
        if range_header:
            match = re.search(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                if start >= file_size:
                    abort(416)
                end = min(end, file_size - 1)
                length = end - start + 1
                data = content[start:end + 1]
                rv = Response(data, 206, mimetype=mime_type, direct_passthrough=True)
                rv.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
                rv.headers.add('Accept-Ranges', 'bytes')
                rv.headers.add('Content-Length', str(length))
                return rv

        rv = Response(content, 200, mimetype=mime_type, direct_passthrough=True)
        rv.headers.add('Accept-Ranges', 'bytes')
        rv.headers.add('Content-Length', str(file_size))
        return rv

    except Exception:
        error_id = uuid.uuid4().hex[:8]
        app.logger.exception("Media streaming failed. Error ID: %s", error_id)
        return jsonify({"ok": False, "error": "Streaming error.", "error_id": error_id}), 502


# ============================================================
# Admin Panel Routes
# ============================================================

ADMIN_USER = os.environ.get("ADMIN_USER", "@alvee")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "@alvee")

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Website Admin Panel</title>
    <style>
        body { font-family: Arial, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .container { width: 100%; max-width: 400px; margin: 20px; background: #1e293b; padding: 25px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); box-sizing: border-box; }
        h1 { color: #38bdf8; text-align: center; font-size: 22px; }
        .stats-box { display: flex; justify-content: space-around; margin: 20px 0; }
        .stat-card { background: #334155; padding: 15px; border-radius: 8px; text-align: center; width: 45%; }
        .stat-card h3 { margin: 0 0 8px; color: #cbd5e1; font-size: 14px; }
        .stat-card p { font-size: 20px; margin: 0; color: #38bdf8; font-weight: bold; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-size: 14px; color: #cbd5e1; }
        input { width: 100%; padding: 12px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #fff; box-sizing: border-box; font-size: 16px; }
        button { background: #0284c7; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; width: 100%; font-size: 16px; font-weight: bold; }
        button:hover { background: #0369a1; }
        .logout { text-align: right; margin-bottom: 15px; }
        .logout a { color: #f43f5e; text-decoration: none; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        {% if logged_in %}
            <div class="logout"><a href="/admin/logout">Logout 🚪</a></div>
            <h1>⚡ Website Admin Dashboard</h1>
            <div class="stats-box">
                <div class="stat-card">
                    <h3>Total Chat Messages</h3>
                    <p>{{ stats.total_chat_messages }}</p>
                </div>
                <div class="stat-card">
                    <h3>Logged-in Users</h3>
                    <p>{{ stats.total_chat_clients }}</p>
                </div>
            </div>
            <hr style="border-color: #334155; margin: 20px 0;">
            <h3>System Info</h3>
            <p><strong>Bot Status:</strong> Active ✅</p>
            <p><strong>Storage Channel:</strong> Connected ✅</p>
        {% else %}
            <h1>🔒 Admin Login</h1>
            {% if error %}<p style="color: #f43f5e; text-align: center;">{{ error }}</p>{% endif %}
            <form method="POST" action="/admin">
                <div class="form-group">
                    <label>Username:</label>
                    <input type="text" name="username" required>
                </div>
                <div class="form-group">
                    <label>Password:</label>
                    <input type="password" name="password" required>
                </div>
                <button type="submit">Login</button>
            </form>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == ADMIN_USER and password == ADMIN_PASS:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        else:
            error = "Invalid username or password!"

    logged_in = session.get("admin_logged_in", False)
    stats = get_chat_stats() if logged_in else {}
    return render_template_string(ADMIN_TEMPLATE, logged_in=logged_in, stats=stats, error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_panel"))


# ============================================================
# Chat API
# ============================================================

@app.route("/api/chat/messages", methods=["GET", "POST"])
def chat_messages_api():
    if request.method == "GET":
        try:
            limit = request.args.get("limit", CHAT_HISTORY_LIMIT_DEFAULT, type=int)
            limit = max(1, min(limit or CHAT_HISTORY_LIMIT_DEFAULT, 200))
            with db_connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            messages = [row_to_message(row) for row in reversed(rows)]
            return jsonify({"ok": True, "messages": messages})
        except Exception:
            print("Chat history error:", traceback.format_exc())
            return jsonify({"ok": False, "error": "Unable to load chat history."}), 500

    text = (request.form.get("text") or "").strip()
    client_id = (request.form.get("client_id") or "").strip()[:120]
    client_message_id = (request.form.get("client_message_id") or uuid.uuid4().hex).strip()[:120]
    sender_name = "Anonymous User"

    if not client_id:
        client_id = uuid.uuid4().hex

    if len(text) > MAX_CHAT_TEXT_CHARS:
        return jsonify({
            "ok": False,
            "error": f"Message text must be {MAX_CHAT_TEXT_CHARS} characters or fewer.",
        }), 400

    uploaded_file = request.files.get("media")
    if not text and (uploaded_file is None or not uploaded_file.filename):
        return jsonify({"ok": False, "error": "Write a message or attach a file."}), 400

    if not BOT_TOKEN:
        return jsonify({
            "ok": False,
            "error": "Chat storage is not configured. Set BOT_TOKEN on the server.",
        }), 503

    with db_connect() as conn:
        existing = conn.execute(
            "SELECT * FROM chat_messages WHERE client_message_id = ?",
            (client_message_id,),
        ).fetchone()

    if existing:
        message = row_to_message(existing)
        return jsonify({
            "ok": True,
            "message": message,
            "telegram_saved": bool(existing["telegram_saved"]),
            "duplicate": True,
        })

    message_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    media_filename = None
    media_type = ""
    original_filename = ""
    media_path = None

    if uploaded_file and uploaded_file.filename:
        original_filename = secure_filename(uploaded_file.filename) or "upload"
        media_type = (uploaded_file.mimetype or mimetypes.guess_type(original_filename)[0] or "").lower()
        if not (media_type.startswith("image/") or media_type.startswith("video/")):
            return jsonify({"ok": False, "error": "Only image and video uploads are allowed."}), 400

        suffix = Path(original_filename).suffix[:12]
        media_filename = f"{message_id}{suffix}"
        media_path = MEDIA_DIR / media_filename
        try:
            uploaded_file.save(media_path)
        except Exception:
            print("Media save error:", traceback.format_exc())
            return jsonify({"ok": False, "error": "Unable to save uploaded media."}), 500

    try:
        telegram_message_id, telegram_file_id, telegram_saved = send_chat_archive_to_telegram(
            message_id=message_id,
            sender_name=sender_name,
            text=text,
            created_at=created_at,
            client_id=client_id,
            media_path=media_path,
            media_type=media_type,
            original_filename=original_filename,
        )

        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    id, client_message_id, client_id, sender_name, text,
                    media_filename, media_type, telegram_file_id,
                    telegram_message_id, telegram_chat_id, telegram_saved,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    client_message_id,
                    client_id,
                    sender_name,
                    text,
                    media_filename,
                    media_type,
                    telegram_file_id,
                    telegram_message_id,
                    str(STORAGE_CHANNEL_ID),
                    1 if telegram_saved else 0,
                    created_at,
                ),
            )
            conn.commit()

        row = get_message_by_id(message_id)
        return jsonify({
            "ok": True,
            "message": row_to_message(row),
            "telegram_saved": bool(telegram_saved),
        }), 201

    except TelegramNotConfiguredError as exc:
        if media_path and media_path.exists():
            media_path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(exc)}), 503

    except TelegramAPIError as exc:
        if media_path and media_path.exists():
            media_path.unlink(missing_ok=True)
        print("Telegram chat backup failed:", exc)
        return jsonify({"ok": False, "error": "Telegram storage failed. Please try again."}), 502

    except sqlite3.IntegrityError:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT * FROM chat_messages WHERE client_message_id = ?",
                (client_message_id,),
            ).fetchone()
        if row:
            return jsonify({
                "ok": True,
                "message": row_to_message(row),
                "telegram_saved": bool(row["telegram_saved"]),
                "duplicate": True,
            })
        print("Chat database integrity error:", traceback.format_exc())
        return jsonify({"ok": False, "error": "Unable to save chat message."}), 500

    except Exception:
        print("Chat send error:", traceback.format_exc())
        return jsonify({"ok": False, "error": "Unexpected server error while saving the message."}), 500


@app.route("/api/chat/media/<message_id>", methods=["GET"])
def get_chat_media(message_id):
    row = get_message_by_id(message_id)
    if row is None:
        return jsonify({"ok": False, "error": "Media not found."}), 404

    media_type = row["media_type"] or "application/octet-stream"
    media_filename = row["media_filename"]

    if media_filename:
        local_path = MEDIA_DIR / os.path.basename(media_filename)
        if local_path.is_file():
            return send_file(local_path, mimetype=media_type, conditional=True, as_attachment=False)

    file_id = row["telegram_file_id"]
    content, telegram_path = download_telegram_file(file_id)
    if content is None:
        return jsonify({"ok": False, "error": "Media is not available on this server."}), 404

    guessed_type = mimetypes.guess_type(telegram_path or "")[0] or media_type
    filename = Path(telegram_path or "media").name
    return send_file(BytesIO(content), mimetype=guessed_type, download_name=filename, as_attachment=False)


@app.route("/stats", methods=["GET"])
def get_stats():
    stats = get_chat_stats()
    return jsonify({"total_users_logged_in": total_logged_in_users, **stats})


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(_error):
    return jsonify({"ok": False, "error": f"Upload is too large. Maximum upload size is {MAX_UPLOAD_MB} MB."}), 413


# ============================================================
# Website pages and background media
# ============================================================

@app.route("/", methods=["GET"])
def home():
    index_path = BASE_DIR / "index.html"
    if index_path.is_file():
        return send_file(index_path)
    return "Alvee Community Server is Running! (index.html not found)", 200


@app.route("/naughty.html", methods=["GET"])
def naughty_page():
    page_path = BASE_DIR / "naughty.html"
    if page_path.is_file():
        return send_file(page_path)
    return "naughty.html not found", 404


@app.route("/verify.html", methods=["GET"])
def verify_page():
    page_path = BASE_DIR / "verify.html"
    if page_path.is_file():
        return send_file(page_path)
    return "verify.html not found", 404


@app.route("/dashboard.html", methods=["GET"])
def dashboard_page():
    page_path = BASE_DIR / "dashboard.html"
    if page_path.is_file():
        return send_file(page_path)
    return "dashboard.html not found", 404


@app.route("/oc-collection.html", methods=["GET"])
def oc_collection_page():
    page_path = BASE_DIR / "oc-collection.html"
    if page_path.is_file():
        return send_file(page_path)
    return "oc-collection.html not found", 404


def background_file_type(filename):
    ext = Path(filename).suffix.lower()
    if ext in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        return "video"
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    return None


@app.route("/get-random-background", methods=["GET"])
def get_random_background():
    BG_DIR.mkdir(parents=True, exist_ok=True)
    files = [name for name in os.listdir(BG_DIR) if (BG_DIR / name).is_file() and background_file_type(name)]
    blur_val = app.config.get("CURRENT_BLUR", "3px")
    if not files:
        return jsonify({
            "status": "success",
            "url": "https://carder.top/imagens/1792298157180-542085424.jpg",
            "type": "image",
            "blur": blur_val,
        })
    chosen_file = random.choice(files)
    return jsonify({
        "status": "success",
        "url": f"/static/backgrounds/{chosen_file}?v={int(time.time())}",
        "type": background_file_type(chosen_file),
        "blur": blur_val,
    })


@app.route("/oc-channel.js", methods=["GET"])
def oc_channel_script():
    page_path = BASE_DIR / "oc-channel.js"
    if page_path.is_file():
        return send_file(page_path, mimetype="text/javascript")
    return "oc-channel.js not found", 404


# ============================================================
# Pyrogram Bot & Media Uploader Initialization
# ============================================================

async def start_pyrogram_monitor():
    global pyrogram_client

    if Client is None or filters is None:
        print("Pyrogram is not installed; background bot uploader is disabled.")
        return False

    if not BOT_TOKEN or not API_ID or not API_HASH:
        print("API_ID/API_HASH/BOT_TOKEN missing; Pyrogram bot uploader is disabled.")
        return False

    try:
        if BOT_TOKEN:
            client = Client(
                "bg_channel_monitor_session",
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=BOT_TOKEN,
                in_memory=True,
            )
            await client.start()
            pyrogram_client = client
            print("Pyrogram bot started successfully.")

            @client.on_message(filters.command("start") & filters.private)
            async def start_cmd(client, message):
                if message.from_user.id != ADMIN_ID:
                    await message.reply("⛔ You are not authorized to use this bot.")
                    return
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload Media (Photo/Video)", callback_data="start_upload")]
                ])
                await message.reply(
                    "👋 Welcome Admin!\n\nClick the button below to upload media. You can send photos, videos, documents, or multiple media files grouped as an album.",
                    reply_markup=keyboard
                )

            @client.on_callback_query(filters.regex("start_upload"))
            async def start_upload_callback(client, callback_query):
                if callback_query.from_user.id != ADMIN_ID:
                    await callback_query.answer("Unauthorized!", show_alert=True)
                    return
                
                pending_uploads[ADMIN_ID] = {
                    "waiting_for_media": True,
                    "media_group": [],
                    "published": False
                }
                
                await callback_query.message.edit_text(
                    "📥 Please send your photo(s), video(s), or document(s) now.\n\n"
                    "You can send single files sequentially or an album at once. Once finished, click 'Confirm & Upload'."
                )
                await callback_query.answer()

            @client.on_message(filters.private & (filters.photo | filters.video | filters.document))
            async def handle_media_upload(client, message):
                if message.from_user.id != ADMIN_ID:
                    return
                
                user_state = pending_uploads.get(ADMIN_ID, {})
                if not user_state.get("waiting_for_media") or user_state.get("published"):
                    return

                user_state["media_group"].append(message)
                pending_uploads[ADMIN_ID] = user_state

                count = len(user_state["media_group"])
                text = f"👍 Media received ({count} item(s) queued). Send more or click confirm below."

                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Confirm & Upload", callback_data="confirm_upload")]
                ])
                
                await message.reply(text, reply_markup=keyboard)

            @client.on_callback_query(filters.regex("confirm_upload"))
            async def confirm_upload_callback(client, callback_query):
                if callback_query.from_user.id != ADMIN_ID:
                    return

                with upload_lock:
                    state = pending_uploads.get(ADMIN_ID, {})
                    if state.get("published"):
                        await callback_query.answer("Already published!", show_alert=True)
                        return
                    
                    media_list = state.get("media_group", [])
                    if not media_list:
                        await callback_query.answer("No media found to upload!", show_alert=True)
                        return
                    
                    state["published"] = True
                    pending_uploads[ADMIN_ID] = state

                await callback_query.message.edit_text("⏳ Uploading to channel... Please wait.")

                try:
                    target_chat = await client.get_chat(STORAGE_CHANNEL_ID)
                    sent_msgs = []

                    if len(media_list) > 1:
                        input_media = []
                        for msg in media_list:
                            caption = msg.caption or ""
                            if msg.photo:
                                input_media.append(InputMediaPhoto(msg.photo.file_id, caption=caption))
                            elif msg.video:
                                input_media.append(InputMediaVideo(msg.video.file_id, caption=caption))
                            elif msg.document:
                                input_media.append(InputMediaDocument(msg.document.file_id, caption=caption))

                        if input_media:
                            for i in range(0, len(input_media), 10):
                                chunk = input_media[i:i+10]
                                res_group = await client.send_media_group(chat_id=target_chat.id, media=chunk)
                                if res_group:
                                    sent_msgs.extend(res_group)
                    else:
                        single_msg = await media_list[0].copy(target_chat.id)
                        if single_msg:
                            sent_msgs.append(single_msg)

                    with db_connect() as conn:
                        for s_msg in sent_msgs:
                            m_id = s_msg.id
                            text_content = s_msg.caption or s_msg.text or ""
                            m_type = "photo"
                            mime = "image/jpeg"
                            f_id = None

                            if s_msg.photo:
                                m_type = "photo"
                                mime = "image/jpeg"
                                f_id = s_msg.photo.file_id
                            elif s_msg.video:
                                m_type = "video"
                                mime = s_msg.video.mime_type or "video/mp4"
                                f_id = s_msg.video.file_id
                            elif s_msg.document:
                                doc_mime = (s_msg.document.mime_type or "").lower()
                                if "video" in doc_mime:
                                    m_type = "video"
                                elif "animation" in doc_mime:
                                    m_type = "animation"
                                else:
                                    m_type = "photo"
                                mime = doc_mime or "application/octet-stream"
                                f_id = s_msg.document.file_id

                            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO channel_posts (
                                    message_id, text, media_type, mime_type, telegram_file_id, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (m_id, text_content, m_type, mime, f_id, created_at)
                            )
                        conn.commit()

                    # ---------- NEW: Cache every uploaded media via Telethon ----------
                    for s_msg in sent_msgs:
                        try:
                            # Large timeout for big files
                            run_async(cache_channel_media_telethon(s_msg.id), timeout=600)
                        except Exception as cache_err:
                            print(f"[Cache] Failed to cache message {s_msg.id}: {cache_err}")

                    pending_uploads.pop(ADMIN_ID, None)
                    
                    await callback_query.message.edit_text(
                        f"🎉 Successfully published {len(media_list)} file(s) to the Telegram channel and synced with the website!\n"
                        f"(Media also cached locally via Telethon)"
                    )
                except Exception as e:
                    with upload_lock:
                        pending_uploads[ADMIN_ID]["published"] = False
                    await callback_query.message.edit_text(f"❌ Upload failed: {str(e)}")
                
                await callback_query.answer()

        return True

    except Exception:
        print("Pyrogram startup error:", traceback.format_exc())
        return False


# ============================================================
# Background Services Startup (Non-Blocking for Render Port Scan)
# ============================================================

def _init_background_services_worker():
    """ব্যাকগ্রাউন্ড থ্রেডে টেলিথন ও পাইরোগ্রাম চালু করে, যাতে ফ্লাস্ক সার্ভারের পোর্ট
    ওপেন হতে এক সেকেন্ডও দেরি না হয় এবং Render-এ Port Scan Timeout না আসে।"""
    if TELETHON_SESSION_STRING and API_ID and API_HASH:
        if not telethon_thread.is_alive():
            telethon_thread.start()
        if telethon_loop_ready.wait(timeout=10):
            try:
                reader_ok = run_async(start_telethon_reader(), timeout=120)
            except Exception:
                reader_ok = False
                print("Could not start Telethon reader:", traceback.format_exc())

            # ---------- সার্ভার চালু হলে টেলিগ্রাম চ্যানেলের পুরো হিস্ট্রি
            # ব্যাকগ্রাউন্ডে স্ক্যান করে channel_posts টেবিল ও মিডিয়া ক্যাশ পুনরুদ্ধার করা ----------
            if reader_ok:
                try:
                    asyncio.run_coroutine_threadsafe(sync_posts_from_channel(), telethon_loop)
                except Exception:
                    print("Channel history sync schedule failed:", traceback.format_exc())

    if Client and BOT_TOKEN and API_ID and API_HASH:
        if not pyrogram_thread.is_alive():
            pyrogram_thread.start()
        if pyrogram_loop_ready.wait(timeout=10):
            try:
                run_pyrogram_async(start_pyrogram_monitor(), timeout=120)
            except Exception:
                print("Could not start Pyrogram monitor:", traceback.format_exc())


def start_background_services():
    global _services_started
    with _services_lock:
        if _services_started:
            return
        _services_started = True

    # আলাদা ডায়মন থ্রেডে সার্ভিসগুলো স্টার্ট করা হচ্ছে যেন ওয়েব সার্ভার সাথে সাথে চালু হয়
    startup_thread = threading.Thread(
        target=_init_background_services_worker,
        name="background-services-initializer",
        daemon=True,
    )
    startup_thread.start()


start_background_services()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"Starting Flask Web Server on 0.0.0.0:{port}...")
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
