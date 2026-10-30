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

# Telethon imports
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeAnimated, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telethon.tl.custom import Button

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
CORS(app, resources={r"/*": {"origins": ["http://localhost:8080", "https://carder.top"]}})

total_logged_in_users = 0

# ============================================================
# TELETHON CLIENT GLOBALS & EVENT LOOP
# ============================================================

telethon_reader_client = None
telethon_bot_client = None
telethon_channel_entity = None

telethon_loop = asyncio.new_event_loop()
telethon_loop_ready = threading.Event()
telethon_thread = threading.Thread(
    target=lambda: _run_telethon_loop(),
    name="telethon-background-loop",
    daemon=True,
)

pending_uploads = {}
temp_client_sessions = {}
upload_lock = threading.Lock()


def _run_telethon_loop():
    asyncio.set_event_loop(telethon_loop)
    telethon_loop_ready.set()
    telethon_loop.run_forever()


def run_async(coro, timeout=180):
    if not telethon_thread.is_alive():
        raise RuntimeError("Telethon background loop is not running.")
    if not telethon_loop_ready.wait(timeout=5):
        raise RuntimeError("Telethon background loop did not start.")
    future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
    return future.result(timeout=timeout)


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
# Telegram Login Endpoints (Direct OTP Verification, No 2FA Check)
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
# TELETHON READER & BOT INITIALIZATION
# ============================================================

async def start_telethon_services():
    global telethon_reader_client, telethon_bot_client, telethon_channel_entity

    if not TELETHON_SESSION_STRING or not API_ID or not API_HASH:
        print("Telethon session or API credentials missing.")
        return

    # 1. Start Channel Reader Client
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        client = TelegramClient(StringSession(TELETHON_SESSION_STRING), API_ID, API_HASH)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Telethon session is not authorized.")

            channel_entity = None
            async for dialog in client.iter_dialogs(limit=None):
                try:
                    resolved_id = utils.get_peer_id(dialog.entity)
                    if int(resolved_id) == int(STORAGE_CHANNEL_ID):
                        channel_entity = dialog.entity
                        break
                except (TypeError, ValueError):
                    continue

            if channel_entity is None:
                try:
                    channel_entity = await client.get_entity(STORAGE_CHANNEL_ID)
                except Exception:
                    raise RuntimeError("Storage channel not found.")

            telethon_reader_client = client
            telethon_channel_entity = channel_entity
            print("Telethon reader connected successfully.")
            break
        except Exception as e:
            print(f"Telethon connection attempt {attempt} failed: {e}")
            if client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
            if attempt == max_retries:
                raise

    # 2. Start Telethon Bot for Admin Media Uploads
    if BOT_TOKEN:
        try:
            bot_client = TelegramClient('telethon_bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
            telethon_bot_client = bot_client
            print("Telethon bot started successfully.")

            @bot_client.on(events.NewMessage(pattern='/start', incoming=True))
            async def start_cmd(event):
                if event.sender_id != ADMIN_ID:
                    await event.respond("⛔ You are not authorized to use this bot.")
                    return
                
                buttons = [
                    [Button.inline("📤 Upload Media (Photo/Video)", data="start_upload")]
                ]
                await event.respond(
                    "👋 Welcome Admin!\n\nClick the button below to upload media. You can send photos, videos, documents, or multiple media files grouped as an album.",
                    buttons=buttons
                )

            @bot_client.on(events.CallbackQuery(data=b'start_upload'))
            async def start_upload_callback(event):
                if event.sender_id != ADMIN_ID:
                    await event.answer("Unauthorized!", alert=True)
                    return
                
                pending_uploads[ADMIN_ID] = {
                    "waiting_for_media": True,
                    "media_group": [],
                    "published": False
                }
                
                await event.edit(
                    "📥 Please send your photo(s), video(s), or document(s) now.\n\n"
                    "You can send single files sequentially or an album at once. Once finished, click 'Confirm & Upload'."
                )
                await event.answer()

            @bot_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and (e.photo or e.video or e.document)))
            async def handle_media_upload(event):
                if event.sender_id != ADMIN_ID:
                    return
                
                user_state = pending_uploads.get(ADMIN_ID, {})
                if not user_state.get("waiting_for_media") or user_state.get("published"):
                    return

                user_state["media_group"].append(event.message)
                pending_uploads[ADMIN_ID] = user_state

                count = len(user_state["media_group"])
                text = f"👍 Media received ({count} item(s) queued). Send more or click confirm below."

                buttons = [
                    [Button.inline("✅ Confirm & Upload", data="confirm_upload")]
                ]
                await event.respond(text, buttons=buttons)

            @bot_client.on(events.CallbackQuery(data=b'confirm_upload'))
            async def confirm_upload_callback(event):
                if event.sender_id != ADMIN_ID:
                    return

                with upload_lock:
                    state = pending_uploads.get(ADMIN_ID, {})
                    if state.get("published"):
                        await event.answer("Already published!", alert=True)
                        return
                    
                    media_list = state.get("media_group", [])
                    if not media_list:
                        await event.answer("No media found to upload!", alert=True)
                        return
                    
                    state["published"] = True
                    pending_uploads[ADMIN_ID] = state

                await event.edit("⏳ Uploading to channel... Please wait.")

                try:
                    if len(media_list) > 1:
                        files_to_send = []
                        for msg in media_list:
                            caption = msg.text or ""
                            file_downloaded = await msg.download_media()
                            files_to_send.append(file_downloaded)
                        
                        await bot_client.send_file(STORAGE_CHANNEL_ID, files_to_send, caption="Uploaded via Admin Bot")
                        for f in files_to_send:
                            if os.path.exists(f):
                                os.remove(f)
                    else:
                        await bot_client.send_file(STORAGE_CHANNEL_ID, media_list[0], caption=media_list[0].text or "")

                    pending_uploads.pop(ADMIN_ID, None)
                    await event.edit(f"🎉 Successfully published {len(media_list)} file(s) to the Telegram channel and synced with the website!")
                except Exception as e:
                    with upload_lock:
                        pending_uploads[ADMIN_ID]["published"] = False
                    await event.edit(f"❌ Upload failed: {str(e)}")
                
                await event.answer()

        except Exception as e:
            print("Telethon bot startup error:", e)


def is_chat_backup_message(message):
    content = (
        getattr(message, "caption", None)
        or getattr(message, "text", None)
        or getattr(message, "message", None)
        or ""
    ).strip()
    return (
        content.startswith(CHAT_BACKUP_MARKER)
        or content.startswith(CHAT_BACKUP_TEXT_MARKER)
    )


def get_oc_telethon_media_info(message):
    if getattr(message, "photo", None):
        return "photo", "image/jpeg"

    document = getattr(message, "document", None)
    if document:
        mime_type = (getattr(document, "mime_type", None) or "application/octet-stream").lower()
        attributes = getattr(document, "attributes", []) or []
        is_animation = any(isinstance(attribute, DocumentAttributeAnimated) for attribute in attributes)

        if is_animation:
            return "animation", mime_type
        if mime_type.startswith("video/"):
            return "video", mime_type
        if mime_type.startswith("image/"):
            return "photo", mime_type
        if "video" in mime_type:
            return "video", mime_type
        if "image" in mime_type:
            return "photo", mime_type
    return None


# ============================================================
# API: GET TELEGRAM CHANNEL POSTS
# ============================================================

@app.route("/api/channel/posts", methods=["GET"])
@app.route("/api/get_videos", methods=["GET"])
def get_channel_posts_api():
    if telethon_reader_client is None or telethon_channel_entity is None:
        return jsonify({
            "ok": False,
            "status": "error",
            "error": "Telethon reader is not connected."
        }), 503

    async def fetch_posts():
        posts = []
        async for message in telethon_reader_client.iter_messages(telethon_channel_entity, limit=100):
            if is_chat_backup_message(message):
                continue
            media_info = get_oc_telethon_media_info(message)
            if not media_info:
                continue
            media_type, mime_type = media_info
            posts.append({
                "id": message.id,
                "message_id": message.id,
                "text": message.message or "",
                "media_type": media_type,
                "mime_type": mime_type,
                "media_url": f"/api/channel/media/{message.id}",
                "date": message.date.isoformat() if message.date else "",
                "media_group_id": str(message.grouped_id) if message.grouped_id else None,
                "has_media": True
            })
            if len(posts) >= 60:
                break
        posts.reverse()
        return posts

    try:
        posts = run_async(fetch_posts(), timeout=180)
        videos = [post["media_url"] for post in posts if post["media_type"] in ("video", "animation")]
        return jsonify({
            "ok": True,
            "status": "success",
            "count": len(posts),
            "posts": posts,
            "videos": videos
        })
    except Exception:
        error_id = uuid.uuid4().hex[:8]
        app.logger.exception("Telethon channel history failed. Error ID: %s", error_id)
        return jsonify({
            "ok": False,
            "status": "error",
            "error": "Unable to read Telegram channel history.",
            "error_id": error_id
        }), 502


# ============================================================
# API: STREAM TELEGRAM MEDIA
# ============================================================

@app.route("/api/channel/media/<int:message_id>", methods=["GET"])
def get_channel_media(message_id):
    if telethon_reader_client is None or telethon_channel_entity is None:
        return jsonify({"ok": False, "error": "Telethon reader is not connected."}), 503

    if message_id <= 0:
        return jsonify({"ok": False, "error": "Invalid message ID."}), 400

    cache_file = CACHE_DIR / f"msg_{message_id}"

    async def get_media_bytes():
        if cache_file.is_file():
            return cache_file.read_bytes()
        message = await telethon_reader_client.get_messages(telethon_channel_entity, ids=message_id)
        if not message or not message.media:
            return None
        content = await telethon_reader_client.download_media(message, file=bytes)
        if content:
            cache_file.write_bytes(content)
        return content

    try:
        content = run_async(get_media_bytes(), timeout=180)
        if not content:
            return jsonify({"ok": False, "error": "Telegram media not found."}), 404

        mime_type = "application/octet-stream"
        if b"ftyp" in content[:20] or b"moov" in content[:1000]:
            mime_type = "video/mp4"
        elif content.startswith(b"\xff\xd8\xff"):
            mime_type = "image/jpeg"
        elif content.startswith(b"\x89PNG"):
            mime_type = "image/png"

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
        app.logger.exception("Telethon media streaming failed. Error ID: %s", error_id)
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
# Background Services Startup (Telethon Reader & Bot)
# ============================================================

def start_background_services():
    if not TELETHON_SESSION_STRING or not API_ID or not API_HASH:
        print("Telethon session or API credentials missing; services disabled.")
        return

    if not telethon_thread.is_alive():
        telethon_thread.start()

    if not telethon_loop_ready.wait(timeout=5):
        print("Telethon loop did not become ready.")
        return

    try:
        run_async(start_telethon_services(), timeout=45)
    except Exception:
        print("Could not start Telethon services:", traceback.format_exc())


start_background_services()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
