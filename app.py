import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import queue
import random
import re
import sqlite3
import threading
import time
import traceback
from typing import Optional, Tuple
import uuid

# Python 3.12+ Event Loop Fix (Fixes "There is no current event loop in thread 'MainThread'")
try:
  asyncio.get_event_loop()
except RuntimeError:
  _main_loop = asyncio.new_event_loop()
  asyncio.set_event_loop(_main_loop)

try:
  import fcntl  # Linux (Render) — একই সার্ভারে একাধিক worker হলে সেশন ডুপ্লিকেট রোধ
except ImportError:
  fcntl = None

import requests
from flask import Flask, request, jsonify, send_file, abort, session, redirect, url_for, render_template_string, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# Telethon imports (Used for Login & Channel Reading)
from telethon import TelegramClient, utils, events as telethon_events
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeAnimated,
    MessageMediaDocument,
    MessageMediaPhoto,
)

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
# (Secrets এখন কোডে নেই — Render > Environment এ সেট করুন)
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

def _env_int(name):
  raw = os.environ.get(name, "").strip()
  if not raw:
    return 0
  try:
    return int(raw)
  except ValueError:
    print(f"[Config] WARNING: {name} must be a number")
    return 0


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
API_ID = _env_int("API_ID")
API_HASH = os.environ.get("API_HASH", "").strip()
ADMIN_ID = _env_int("ADMIN_ID")

# Telethon user session for channel reader
TELETHON_SESSION_STRING = os.environ.get("TELETHON_SESSION_STRING", "").strip()

# Storage channel ID normalization (-1004322205565)
_raw_channel_id = os.environ.get("STORAGE_CHANNEL_ID", "-1004322205565").strip()

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

STORY_BACKUP_CHANNEL_ID = int(
    os.environ.get("STORY_BACKUP_CHANNEL_ID", "-1004322205565")
)
STORY_MARKER = "#STORY"
DEFAULT_STORY_THUMB = "https://carder.top/imagens/1792065174675-662575987.jpg"

# Story চ্যানেল আলাদা হলে True (ডিফল্টে দুটোই একই চ্যানেল)
SEPARATE_STORY_CHANNEL = int(STORY_BACKUP_CHANNEL_ID) != int(STORAGE_CHANNEL_ID)

# Scan সেটিংস
# COLLECTION_INCLUDE_CAPTIONED=1 দিলে ক্যাপশনওয়ালা (#STORY ছাড়া) মিডিয়াও oc-collection এ আসবে।
# ডিফল্ট (0): শুধু ক্যাপশনহীন মিডিয়া → oc-collection
COLLECTION_INCLUDE_CAPTIONED = (
    os.environ.get("COLLECTION_INCLUDE_CAPTIONED", "0").strip() == "1"
)
# PRECACHE_MEDIA=1 দিলে স্টার্টআপে সব মিডিয়া ডিস্কে ক্যাশ হবে (ভারী — সাধারণত দরকার নেই, কারণ এখন সরাসরি স্ট্রিম হয়)
PRECACHE_MEDIA = os.environ.get("PRECACHE_MEDIA", "0").strip() == "1"
STREAM_REQUEST_SIZE = 512 * 1024  # Telethon download chunk (512 KB)
MSG_CACHE_TTL = 600  # সেকেন্ড
# নতুন ডিপ্লয়ের সময় পুরনো ও নতুন ইনস্ট্যান্স কিছুক্ষণ একসাথে চলে; একই Telethon সেশন
# দুই জায়গায় একসাথে চললে Telegram সেশন বাতিল করে দেয় (AuthKeyDuplicatedError)।
# তাই Telethon চালুর আগে এই সময় (সেকেন্ড) অপেক্ষা করা হয়।
TELETHON_START_DELAY = float(os.environ.get("TELETHON_START_DELAY", "40") or 0)

print("OC & Story Storage Channel ID:", STORAGE_CHANNEL_ID)
for _name, _val in (
    ("BOT_TOKEN", BOT_TOKEN),
    ("API_ID", API_ID),
    ("API_HASH", API_HASH),
    ("ADMIN_ID", ADMIN_ID),
    ("TELETHON_SESSION_STRING", TELETHON_SESSION_STRING),
):
  if not _val:
    print(f"[Config] WARNING: environment variable {_name} is not set!")

WEBHOOK_SECRET = os.environ.get(
    "TELEGRAM_WEBHOOK_SECRET", "my_secure_webhook_secret_123"
).strip()

MAX_UPLOAD_MB = max(1, int(os.environ.get("MAX_UPLOAD_MB", "50")))
MAX_CHAT_TEXT_CHARS = 3000
CHAT_HISTORY_LIMIT_DEFAULT = 100
CHAT_BACKUP_MARKER = "[CHAT_BACKUP]"
CHAT_BACKUP_TEXT_MARKER = "[CHAT_BACKUP_TEXT]"

DB_PATH = Path(os.environ.get("CHAT_DB_PATH", str(BASE_DIR / "chat_messages.db")))
MEDIA_DIR = Path(
    os.environ.get("CHAT_MEDIA_DIR", str(BASE_DIR / "static" / "chat_media"))
)
BG_DIR = Path(
    os.environ.get("BACKGROUND_DIR", str(BASE_DIR / "static" / "backgrounds"))
)
CACHE_DIR = Path(
    os.environ.get(
        "TELETHON_CACHE_DIR", str(BASE_DIR / "static" / "telethon_cache")
    )
)

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
BG_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or hashlib.sha256(
    ("alvee-secret-" + (BOT_TOKEN or "dev")).encode("utf-8")
).hexdigest()
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                "http://localhost:8080",
                "https://carder.top",
                "https://alveecommunity.com",
                "http://alveecommunity.com",
                "*",
            ]
        }
    },
)

total_logged_in_users = 0

# ============================================================
# TELEGRAM CLIENT GLOBALS & EVENT LOOPS
# ============================================================

telethon_reader_client = None
telethon_channel_entity = None
telethon_story_entity = None
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

# Scan লক (একসাথে একটাই স্ক্যান চলবে)
scan_lock = threading.Lock()

# Telethon সংযোগের অবস্থা (ডায়াগনস্টিকের জন্য, কোনো গোপন তথ্য নেই)
telethon_status = {
    "state": "starting" if TELETHON_SESSION_STRING else "not_configured",
    "last_error": (
        ""
        if TELETHON_SESSION_STRING
        else "TELETHON_SESSION_STRING environment variable is empty or not set"
    ),
    "attempts": 0,
    "channel_id": STORAGE_CHANNEL_ID,
}


class ScanBusyError(RuntimeError):
  pass


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
    coro.close()
    raise RuntimeError("Telethon background loop is not running.")
  if not telethon_loop_ready.wait(timeout=5):
    coro.close()
    raise RuntimeError("Telethon background loop did not start.")
  future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
  return future.result(timeout=timeout)


def run_pyrogram_async(coro, timeout=180):
  if not pyrogram_thread.is_alive():
    coro.close()
    raise RuntimeError("Pyrogram background loop is not running.")
  if not pyrogram_loop_ready.wait(timeout=5):
    coro.close()
    raise RuntimeError("Pyrogram background loop did not start.")
  future = asyncio.run_coroutine_threadsafe(coro, pyrogram_loop)
  return future.result(timeout=timeout)


# ============================================================
# SQLite chat, channel posts & stories storage
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
            CREATE TABLE IF NOT EXISTS stories (
                message_id INTEGER PRIMARY KEY,
                media_type TEXT,
                mime_type TEXT,
                telegram_file_id TEXT,
                thumb_file_id TEXT,
                ts INTEGER,
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
        "SELECT * FROM chat_messages WHERE id = ?", (message_id,)
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


def find_media_row(message_id, kind=""):
  """channel_posts / stories টেবিল থেকে মিডিয়া রো খুঁজে বের করে।
  returns (row, 'post' | 'story') অথবা (None, None)"""
  if kind == "story":
    order = (("stories", "story"), ("channel_posts", "post"))
  else:
    order = (("channel_posts", "post"), ("stories", "story"))
  with db_connect() as conn:
    for table, row_kind in order:
      row = conn.execute(
          f"SELECT * FROM {table} WHERE message_id = ?", (message_id,)
      ).fetchone()
      if row:
        return row, row_kind
  return None, None


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
    response = requests.post(
        url, data=data or {}, files=files, timeout=timeout
    )
    try:
      payload = response.json()
    except ValueError:
      payload = {}
    if response.status_code >= 400 or not payload.get("ok"):
      description = (
          payload.get("description")
          or f"Telegram API returned HTTP {response.status_code}"
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
# Telethon helpers (entity, message cache, disk cache)
# ============================================================

_msg_cache = {}  # (kind, message_id) -> (timestamp, message)   [শুধু telethon loop থেকে ব্যবহার]
_photo_cache = {}  # (kind, message_id) -> bytes                  [শুধু telethon loop থেকে ব্যবহার]


def _entity_for_kind(kind):
  if kind == "story":
    return telethon_story_entity
  return telethon_channel_entity


async def _find_entity_in_dialogs(client, channel_id):
  async for dialog in client.iter_dialogs(limit=None):
    try:
      resolved_id = utils.get_peer_id(dialog.entity)
      if int(resolved_id) == int(channel_id):
        return dialog.entity
    except (TypeError, ValueError):
      continue
  return None


async def get_cached_message(message_id, kind="post", fresh=False):
  """চ্যানেল থেকে মেসেজ আনে (১০ মিনিট ক্যাশ)।"""
  if not telethon_reader_client:
    return None
  entity = _entity_for_kind(kind)
  if entity is None:
    return None

  key = (kind, int(message_id))
  now = time.time()
  hit = _msg_cache.get(key)
  if hit and not fresh and now - hit[0] < MSG_CACHE_TTL:
    return hit[1]

  msgs = await telethon_reader_client.get_messages(entity, ids=int(message_id))
  msg = msgs[0] if isinstance(msgs, (list, tuple)) else msgs
  if not msg:
    return None

  _msg_cache[key] = (now, msg)
  if len(_msg_cache) > 200:
    oldest = min(_msg_cache, key=lambda k: _msg_cache[k][0])
    _msg_cache.pop(oldest, None)
  return msg


async def get_media_info(message_id, kind="post"):
  """size / mime / photo কিনা — স্ট্রিমিং শুরুর আগে দরকার।"""
  msg = await get_cached_message(message_id, kind)
  if not msg or not getattr(msg, "media", None):
    return None
  is_photo = bool(getattr(msg, "photo", None))
  file_obj = getattr(msg, "file", None)
  size = int(getattr(file_obj, "size", 0) or 0)
  mime = getattr(file_obj, "mime_type", None) or (
      "image/jpeg" if is_photo else "video/mp4"
  )
  return {"size": size, "mime": mime, "is_photo": is_photo}


async def get_photo_bytes(message_id, kind="post"):
  key = (kind, int(message_id))
  data = _photo_cache.get(key)
  if data:
    return data

  data = None
  for attempt in (0, 1):
    try:
      msg = await get_cached_message(message_id, kind, fresh=attempt > 0)
      if not msg or not getattr(msg, "media", None):
        return None
      data = await telethon_reader_client.download_media(msg, file=bytes)
      if data:
        break
    except Exception as exc:
      print(f"[Photo] attempt {attempt} failed for {message_id}: {exc}")

  if data:
    _photo_cache[key] = data
    while len(_photo_cache) > 40:
      _photo_cache.pop(next(iter(_photo_cache)), None)
  return data


async def get_telethon_thumb_bytes(message_id, kind="story"):
  try:
    msg = await get_cached_message(message_id, kind)
    if not msg or not getattr(msg, "media", None):
      return None
    data = await telethon_reader_client.download_media(
        msg, file=bytes, thumb=-1
    )
    return data or None
  except Exception as exc:
    print(f"[Thumb] Telethon thumb failed for {message_id}: {exc}")
    return None


async def get_post_thumb_bytes(message_id, kind="post"):
  try:
    msg = await get_cached_message(message_id, kind)
    if not msg or not getattr(msg, "media", None):
      return None
    is_photo = bool(getattr(msg, "photo", None))
    for t in ((1, 0, -1) if is_photo else (-1,)):
      try:
        data = await telethon_reader_client.download_media(
            msg, file=bytes, thumb=t
        )
      except Exception:
        data = None
      if data:
        return data
    return None
  except Exception as exc:
    print(f"[Thumb] post thumb failed for {message_id}: {exc}")
    return None


async def telegram_stream_producer(
    q, stop, message_id, kind, aligned_offset, n_chunks, file_size
):
  """Telegram থেকে চাঙ্ক ডাউনলোড করে queue তে রাখে (Flask thread এটা পড়ে)।"""
  loop = asyncio.get_running_loop()

  def put(item):
    while not stop.is_set():
      try:
        q.put(item, timeout=0.5)
        return True
      except queue.Full:
        continue
    return False

  sent = 0
  try:
    for attempt in (0, 1):
      msg = await get_cached_message(message_id, kind, fresh=attempt > 0)
      if not msg or not getattr(msg, "media", None):
        break
      try:
        async for chunk in telethon_reader_client.iter_download(
            msg.media,
            offset=aligned_offset,
            limit=n_chunks,
            chunk_size=STREAM_REQUEST_SIZE,
            request_size=STREAM_REQUEST_SIZE,
            file_size=file_size,
        ):
          if stop.is_set():
            return
          data = bytes(chunk)
          ok = await loop.run_in_executor(None, put, data)
          if not ok:
            return
          sent += len(data)
        break
      except Exception:
        if sent or attempt:
          raise
        print(f"[Stream] retrying message {message_id} with fresh reference")
  except Exception:
    print("[Stream] producer error:", traceback.format_exc())
  finally:
    try:
      await loop.run_in_executor(None, put, None)
    except Exception:
      pass


async def cache_channel_media_telethon(message_id: int, kind: str = "post") -> bool:
  if not telethon_reader_client:
    print(f"[Cache] Telethon not ready for message {message_id}")
    return False

  entity = _entity_for_kind(kind)
  if entity is None:
    print(f"[Cache] Telethon entity not ready for message {message_id}")
    return False

  try:
    msgs = await telethon_reader_client.get_messages(entity, ids=message_id)
    msg = msgs[0] if isinstance(msgs, (list, tuple)) else msgs

    if not msg or not getattr(msg, "media", None):
      print(f"[Cache] No media found for message {message_id}")
      return False

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for old in CACHE_DIR.glob(f"{message_id}.*"):
      try:
        old.unlink(missing_ok=True)
      except Exception:
        pass

    target = CACHE_DIR / str(message_id)
    downloaded_path = await telethon_reader_client.download_media(
        msg, file=str(target)
    )

    if downloaded_path:
      print(
          f"[Cache] Successfully cached message {message_id} ->"
          f" {downloaded_path}"
      )
      return True

    return False

  except Exception as e:
    print(f"[Cache] Telethon error for message {message_id}: {e}")
    return False


def find_cached_media(message_id: int) -> Optional[Path]:
  if not CACHE_DIR.exists():
    return None
  matches = [
      p
      for p in CACHE_DIR.glob(f"{message_id}*")
      if p.is_file() and (p.stem == str(message_id) or p.name == str(message_id))
  ]
  if not matches:
    return None
  return max(matches, key=lambda p: p.stat().st_size)


# ============================================================
# Channel scanning (Posts / OC Collection + Stories)
#   - Story      : ক্যাপশনে #STORY আছে       -> stories টেবিল  -> dashboard.html
#   - Collection : ক্যাপশন নেই (ভ্লগ ভিডিও)   -> channel_posts   -> oc-collection.html
# ============================================================


def _classify_media_for_scan(msg):
  """(media_type, mime_type) অথবা None"""
  media = getattr(msg, "media", None)
  if not isinstance(media, (MessageMediaPhoto, MessageMediaDocument)):
    return None

  if getattr(msg, "photo", None):
    return "photo", "image/jpeg"
  if getattr(msg, "video", None):
    return "video", getattr(msg.video, "mime_type", None) or "video/mp4"
  if getattr(msg, "document", None):
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
      return None
    return media_type, doc_mime or "application/octet-stream"
  return None


async def _scan_one_channel(entity, progress=None):
  records = []
  all_ids = set()
  total = 0
  found = 0

  async for msg in telethon_reader_client.iter_messages(
      entity, limit=None, wait_time=0.5
  ):
    total += 1
    all_ids.add(msg.id)

    info = _classify_media_for_scan(msg)
    if info:
      found += 1
      dt = msg.date or datetime.now(timezone.utc)
      records.append({
          "id": msg.id,
          "media_type": info[0],
          "mime": info[1],
          "text": msg.message or "",
          "group": getattr(msg, "grouped_id", None),
          "ts": int(dt.timestamp()),
          "created_at": dt.isoformat(),
      })

    if progress and total % 200 == 0:
      try:
        progress(total, found)
      except Exception:
        pass

  return records, all_ids, total


def _split_records(records):
  """রেকর্ডগুলোকে stories / posts এ ভাগ করে। Album এর ক্যাপশন সব আইটেমে প্রযোজ্য।"""
  group_caps = {}
  for r in records:
    if r["group"] and (r["text"] or "").strip() and r["group"] not in group_caps:
      group_caps[r["group"]] = r["text"]

  stories, posts, story_ids = [], [], set()
  for r in records:
    text = r["text"] or ""
    if not text.strip() and r["group"]:
      text = group_caps.get(r["group"], "")
    stripped = text.strip()

    if stripped.startswith(CHAT_BACKUP_MARKER) or stripped.startswith(
        CHAT_BACKUP_TEXT_MARKER
    ):
      continue

    item = dict(r, text=text)
    if STORY_MARKER.upper() in text.upper():
      story_ids.add(r["id"])
      stories.append(item)
    elif not stripped or COLLECTION_INCLUDE_CAPTIONED:
      posts.append(item)

  return stories, posts, story_ids


def save_scan_results(stories, posts, all_ids, story_ids, do_story, do_posts):
  stats = {
      "stories_added": 0,
      "stories_updated": 0,
      "posts_added": 0,
      "posts_updated": 0,
      "removed": 0,
  }

  with db_connect() as conn:
    if do_story:
      existing = {
          row["message_id"]
          for row in conn.execute("SELECT message_id FROM stories").fetchall()
      }
      for s in stories:
        story_type = "photo" if s["media_type"] == "photo" else "video"
        if s["id"] in existing:
          conn.execute(
              "UPDATE stories SET media_type = ?, mime_type = ? WHERE message_id = ?",
              (story_type, s["mime"], s["id"]),
          )
          stats["stories_updated"] += 1
        else:
          conn.execute(
              """
              INSERT OR IGNORE INTO stories (
                  message_id, media_type, mime_type, telegram_file_id, thumb_file_id, ts, created_at
              ) VALUES (?, ?, ?, ?, ?, ?, ?)
              """,
              (s["id"], story_type, s["mime"], None, None, s["ts"], s["created_at"]),
          )
          stats["stories_added"] += 1

      if all_ids:
        keep = {s["id"] for s in stories}
        stale = [(mid,) for mid in existing if mid not in keep]
        if stale:
          conn.executemany("DELETE FROM stories WHERE message_id = ?", stale)
          stats["removed"] += len(stale)

    if do_posts:
      existing = {
          row["message_id"]
          for row in conn.execute(
              "SELECT message_id FROM channel_posts"
          ).fetchall()
      }
      for p in posts:
        if p["id"] in existing:
          conn.execute(
              "UPDATE channel_posts SET text = ?, media_type = ?, mime_type = ? WHERE message_id = ?",
              (p["text"], p["media_type"], p["mime"], p["id"]),
          )
          stats["posts_updated"] += 1
        else:
          conn.execute(
              """
              INSERT OR IGNORE INTO channel_posts (
                  message_id, text, media_type, mime_type, telegram_file_id, created_at
              ) VALUES (?, ?, ?, ?, ?, ?)
              """,
              (p["id"], p["text"], p["media_type"], p["mime"], None, p["created_at"]),
          )
          stats["posts_added"] += 1

      if all_ids:
        stale = [
            (mid,)
            for mid in existing
            if (mid not in all_ids) or (mid in story_ids)
        ]
        if stale:
          conn.executemany(
              "DELETE FROM channel_posts WHERE message_id = ?", stale
          )
          stats["removed"] += len(stale)

    conn.commit()

  return stats


async def scan_channel(mode="all", progress=None):
  """mode: 'story' | 'collection' | 'all'"""
  if not telethon_reader_client or not telethon_channel_entity:
    raise RuntimeError(
        "Telethon reader is not connected (check TELETHON_SESSION_STRING)."
    )
  if mode in ("story", "all") and SEPARATE_STORY_CHANNEL and (
      telethon_story_entity is None
  ):
    raise RuntimeError(
        "Story channel is not accessible by the Telethon session."
    )
  if not scan_lock.acquire(blocking=False):
    raise ScanBusyError("A scan is already running.")

  try:
    plan = []
    if not SEPARATE_STORY_CHANNEL:
      plan.append((
          telethon_channel_entity,
          mode in ("story", "all"),
          mode in ("collection", "all"),
      ))
    else:
      if mode in ("story", "all"):
        plan.append((telethon_story_entity, True, False))
      if mode in ("collection", "all"):
        plan.append((telethon_channel_entity, False, True))

    summary = {
        "checked": 0,
        "stories_found": 0,
        "posts_found": 0,
        "stories_added": 0,
        "stories_updated": 0,
        "posts_added": 0,
        "posts_updated": 0,
        "removed": 0,
    }

    for entity, do_story, do_posts in plan:
      records, all_ids, total = await _scan_one_channel(entity, progress)
      stories, posts, story_ids = _split_records(records)
      stats = await asyncio.to_thread(
          save_scan_results,
          stories if do_story else [],
          posts if do_posts else [],
          all_ids,
          story_ids,
          do_story,
          do_posts,
      )
      summary["checked"] += total
      if do_story:
        summary["stories_found"] += len(stories)
      if do_posts:
        summary["posts_found"] += len(posts)
      for key in (
          "stories_added",
          "stories_updated",
          "posts_added",
          "posts_updated",
          "removed",
      ):
        summary[key] += stats[key]

    return summary
  finally:
    scan_lock.release()


async def recache_missing_media(items):
  for mid, kind in items:
    try:
      await cache_channel_media_telethon(mid, kind)
    except Exception as e:
      print(f"[Sync] Failed to re-cache message {mid}: {e}")
    await asyncio.sleep(0.5)


async def sync_posts_from_channel():
  """স্টার্টআপে ডাটাবেস রিস্টোর (ডিপ্লয়ের পর SQLite খালি হলে চ্যানেল থেকে ফিরিয়ে আনে)।"""
  if not telethon_reader_client or not telethon_channel_entity:
    return

  try:
    result = await scan_channel("all")
    print("[Sync] Channel restore complete:", result)

    if PRECACHE_MEDIA:
      with db_connect() as conn:
        post_ids = [
            r["message_id"]
            for r in conn.execute("SELECT message_id FROM channel_posts")
        ]
        story_ids = [
            r["message_id"] for r in conn.execute("SELECT message_id FROM stories")
        ]
      to_cache = [(m, "post") for m in post_ids if not find_cached_media(m)]
      to_cache += [(m, "story") for m in story_ids if not find_cached_media(m)]
      if to_cache:
        asyncio.create_task(recache_missing_media(to_cache))
  except ScanBusyError:
    pass
  except Exception:
    print("[Sync] Channel sync failed:", traceback.format_exc())


# ============================================================
# Telegram Login Endpoints
# ============================================================


@app.route("/send-code", methods=["POST"])
def send_code_api():
  req_data = request.get_json(silent=True) or {}
  phone = req_data.get("phone", "").strip()

  if not phone:
    return (
        jsonify({"status": "error", "message": "Phone number is required."}),
        400,
    )

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
    return (
        jsonify({
            "status": "error",
            "message": (
                "Session expired or phone missing. Please request code again."
            ),
        }),
        400,
    )

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
        send_telegram_message(
            ADMIN_ID,
            f"🎉 Successful Telegram Login (OTP Verified)!\n📱 Phone: {phone}",
        )

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
    return (
        jsonify({
            "status": "error",
            "message": "Telegram authentication is not configured.",
        }),
        400,
    )

  try:
    auth_date = int(payload.get("auth_date", 0))
    telegram_id = int(payload.get("id", 0))
  except (TypeError, ValueError):
    return (
        jsonify({"status": "error", "message": "Invalid Telegram login data."}),
        400,
    )

  now = int(time.time())
  if telegram_id <= 0 or auth_date <= 0:
    return (
        jsonify({
            "status": "error",
            "message": "Missing Telegram user ID or login time.",
        }),
        400,
    )

  if auth_date > now + 60 or now - auth_date > 86400:
    return (
        jsonify({
            "status": "error",
            "message": "Telegram login data has expired.",
        }),
        401,
    )

  data_check_string = "\n".join(
      f"{key}={payload[key]}" for key in sorted(payload)
  )
  secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
  expected_hash = hmac.new(
      secret_key, data_check_string.encode("utf-8"), hashlib.sha256
  ).hexdigest()

  if not hmac.compare_digest(received_hash, expected_hash):
    return (
        jsonify(
            {"status": "error", "message": "Telegram authentication failed."}
        ),
        401,
    )

  first_name = str(payload.get("first_name", "")).strip()
  last_name = str(payload.get("last_name", "")).strip()
  username = str(payload.get("username", "")).strip()
  photo = str(payload.get("photo_url", "")).strip()

  if not photo.startswith("https://"):
    photo = ""

  session["telegram_profile"] = {
      "id": telegram_id,
      "name": (
          " ".join(part for part in (first_name, last_name) if part)
          or "Telegram User"
      ),
      "username": username,
      "photo": photo,
      "phone": "",
      "bio": "",
  }
  session["telegram_auth_date"] = auth_date
  return jsonify({"status": "success"})


@app.route("/get-user-profile", methods=["POST"])
def get_user_profile_api():
  profile = session.get("telegram_profile")
  auth_date = session.get("telegram_auth_date", 0)

  if not isinstance(profile, dict):
    return (
        jsonify({
            "status": "error",
            "message": "Please sign in using Telegram Login.",
        }),
        401,
    )

  try:
    if int(time.time()) - int(auth_date) > 86400:
      session.pop("telegram_profile", None)
      session.pop("telegram_auth_date", None)
      return (
          jsonify({
              "status": "error",
              "message": "Your login has expired. Please sign in again.",
          }),
          401,
      )
  except (TypeError, ValueError):
    return (
        jsonify({"status": "error", "message": "Invalid login session."}),
        401,
    )

  return jsonify({
      "status": "success",
      "profile": {
          "id": profile.get("id"),
          "name": profile.get("name", ""),
          "username": profile.get("username", ""),
          "phone": profile.get("phone", ""),
          "bio": profile.get("bio", ""),
          "photo": profile.get("photo", ""),
      },
  })


# ============================================================
# TELETHON READER INITIALIZATION
# ============================================================


async def start_telethon_reader():
  global telethon_reader_client, telethon_channel_entity, telethon_story_entity

  if not TELETHON_SESSION_STRING:
    return False

  max_retries = 5
  for attempt in range(1, max_retries + 1):
    telethon_status["attempts"] = attempt
    telethon_status["state"] = "starting"
    client = TelegramClient(
        StringSession(TELETHON_SESSION_STRING), API_ID, API_HASH
    )
    try:
      await client.connect()
      if not await client.is_user_authorized():
        telethon_status["last_error"] = (
            "Session is not authorized (string invalid, revoked, or used from"
            " another server at the same time)"
        )
        print("[Telethon]", telethon_status["last_error"])
        await client.disconnect()
        await asyncio.sleep(3)
        continue

      channel_entity = None
      try:
        channel_entity = await client.get_entity(STORAGE_CHANNEL_ID)
      except Exception:
        channel_entity = await _find_entity_in_dialogs(client, STORAGE_CHANNEL_ID)

      if channel_entity is None:
        telethon_status["last_error"] = (
            f"Account cannot access channel {STORAGE_CHANNEL_ID} (is this"
            " Telethon account a member of the channel?)"
        )
        print("[Telethon]", telethon_status["last_error"])
        await client.disconnect()
        await asyncio.sleep(3)
        continue

      # Story চ্যানেল (ডিফল্টে storage চ্যানেলের মতোই)
      story_entity = channel_entity
      if SEPARATE_STORY_CHANNEL:
        story_entity = None
        try:
          story_entity = await client.get_entity(STORY_BACKUP_CHANNEL_ID)
        except Exception:
          try:
            story_entity = await _find_entity_in_dialogs(
                client, STORY_BACKUP_CHANNEL_ID
            )
          except Exception:
            story_entity = None
        if story_entity is None:
          print(
              "[Telethon] WARNING: story backup channel could not be resolved."
          )

      telethon_reader_client = client
      telethon_channel_entity = channel_entity
      telethon_story_entity = story_entity
      telethon_status["state"] = "connected"
      telethon_status["last_error"] = ""
      print(
          "Telethon reader connected and channel entity resolved successfully."
      )
      return True

    except Exception as e:
      telethon_status["last_error"] = f"{type(e).__name__}: {e}"[:300]
      print(f"[Telethon] start attempt {attempt} failed: {e}")
      if client.is_connected():
        try:
          await client.disconnect()
        except Exception:
          pass
      if type(e).__name__ == "AuthKeyDuplicatedError":
        telethon_status["last_error"] = (
            "AuthKeyDuplicatedError: Telegram invalidated this session because"
            " it was used from two places at once. Generate a NEW"
            " TELETHON_SESSION_STRING and use it only on this server."
        )
        telethon_status["state"] = "failed"
        return False
      await asyncio.sleep(5)

  telethon_status["state"] = "failed"
  return False


# ============================================================
# API: STORIES ENDPOINTS (FOR DASHBOARD.HTML - UNLIMITED)
# ============================================================


@app.route("/api/stories", methods=["GET"])
def get_stories_api():
  try:
    with db_connect() as conn:
      rows = conn.execute(
          "SELECT * FROM stories ORDER BY ts DESC"
      ).fetchall()

    stories_list = []
    for row in rows:
      mid = row["message_id"]
      m_type = "photo" if row["media_type"] == "photo" else "video"
      media_url = f"/api/channel/media/{mid}"
      if SEPARATE_STORY_CHANNEL:
        media_url += "?kind=story"

      if m_type == "photo":
        thumb_url = media_url
      else:
        thumb_url = f"/api/stories/thumb/{mid}"

      stories_list.append({
          "id": str(mid),
          "type": m_type,
          "thumb": thumb_url,
          "src": media_url,
          "ts": int(row["ts"] or 0),
      })

    return jsonify(stories_list)
  except Exception:
    app.logger.exception("Stories fetch failed")
    return jsonify([]), 200


@app.route("/api/stories/thumb/<int:message_id>", methods=["GET"])
def get_story_thumb(message_id):
  try:
    thumb_cache = CACHE_DIR / f"thumb_{message_id}.jpg"
    if thumb_cache.is_file():
      return send_file(thumb_cache, mimetype="image/jpeg", conditional=True)

    with db_connect() as conn:
      row = conn.execute(
          "SELECT thumb_file_id FROM stories WHERE message_id = ?",
          (message_id,),
      ).fetchone()

    if not row:
      return redirect(DEFAULT_STORY_THUMB)

    content = None
    if row["thumb_file_id"]:
      content, _ = download_telegram_file(row["thumb_file_id"])

    # স্ক্যান করে আনা স্টোরির থাম্বনেইল Telethon দিয়ে আনা হয়
    if not content and telethon_reader_client is not None:
      try:
        content = run_async(
            get_telethon_thumb_bytes(message_id, "story"), timeout=60
        )
      except Exception:
        content = None

    if not content:
      return redirect(DEFAULT_STORY_THUMB)

    try:
      CACHE_DIR.mkdir(parents=True, exist_ok=True)
      with open(thumb_cache, "wb") as f:
        f.write(content)
    except Exception:
      pass

    return Response(
        content,
        status=200,
        mimetype="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
  except Exception:
    return redirect(DEFAULT_STORY_THUMB)


# ============================================================
# API: GET TELEGRAM CHANNEL POSTS FROM SQLITE
# ============================================================


@app.route("/api/channel/posts", methods=["GET"])
@app.route("/api/get_videos", methods=["GET"])
def get_channel_posts_api():
  try:
    limit = request.args.get("limit", 1000, type=int) or 1000
    limit = max(1, min(limit, 5000))
    with db_connect() as conn:
      rows = conn.execute(
          """
          SELECT * FROM (
              SELECT * FROM channel_posts ORDER BY message_id DESC LIMIT ?
          ) ORDER BY message_id ASC
          """,
          (limit,),
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
          "has_media": True,
          "thumbnail_url": f"/api/channel/thumb/{row['message_id']}",
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
        "videos": videos,
    })
  except Exception:
    error_id = uuid.uuid4().hex[:8]
    app.logger.exception("Channel posts fetch failed. Error ID: %s", error_id)
    return (
        jsonify({
            "ok": False,
            "status": "error",
            "error": "Unable to read channel history.",
            "error_id": error_id,
        }),
        502,
    )


# ============================================================
# API: STREAM TELEGRAM MEDIA (সরাসরি Telegram থেকে, Range সাপোর্টসহ)
# ============================================================


def _parse_range_header(range_header, size):
  """(start, end) অথবা None; অগ্রহণযোগ্য রেঞ্জে ValueError।"""
  if not range_header or not size:
    return None
  match = re.match(r"^\s*bytes=(\d*)-(\d*)\s*$", range_header)
  if not match:
    return None
  a, b = match.group(1), match.group(2)
  if a == "" and b == "":
    return None
  if a == "":
    length = int(b)
    if length <= 0:
      raise ValueError("unsatisfiable")
    start = max(0, size - length)
    end = size - 1
  else:
    start = int(a)
    end = int(b) if b else size - 1
    end = min(end, size - 1)
  if start >= size or start > end:
    raise ValueError("unsatisfiable")
  return start, end


def _ranged_bytes_response(content, mime_type):
  file_size = len(content)
  range_header = request.headers.get("Range", None)
  if range_header:
    match = re.search(r"bytes=(\d+)-(\d*)", range_header)
    if match:
      start = int(match.group(1))
      end = int(match.group(2)) if match.group(2) else file_size - 1
      if start >= file_size:
        return Response(
            status=416, headers={"Content-Range": f"bytes */{file_size}"}
        )
      end = min(end, file_size - 1)
      length = end - start + 1
      data = content[start : end + 1]
      rv = Response(data, 206, mimetype=mime_type, direct_passthrough=True)
      rv.headers.add("Content-Range", f"bytes {start}-{end}/{file_size}")
      rv.headers.add("Accept-Ranges", "bytes")
      rv.headers.add("Content-Length", str(length))
      rv.headers.add("Cache-Control", "public, max-age=3600")
      return rv

  rv = Response(content, 200, mimetype=mime_type, direct_passthrough=True)
  rv.headers.add("Accept-Ranges", "bytes")
  rv.headers.add("Content-Length", str(file_size))
  rv.headers.add("Cache-Control", "public, max-age=3600")
  return rv


def stream_telegram_media(message_id, kind, size, mime):
  try:
    rng = _parse_range_header(request.headers.get("Range"), size)
  except ValueError:
    return Response(status=416, headers={"Content-Range": f"bytes */{size}"})

  if rng:
    start, end = rng
    status = 206
  else:
    start, end = 0, size - 1
    status = 200
  length = end - start + 1

  headers = {
      "Accept-Ranges": "bytes",
      "Content-Length": str(length),
      "Cache-Control": "public, max-age=3600",
  }
  if status == 206:
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"

  if request.method == "HEAD":
    return Response(status=status, mimetype=mime, headers=headers)

  aligned = (start // STREAM_REQUEST_SIZE) * STREAM_REQUEST_SIZE
  skip = start - aligned
  n_chunks = (end + 1 - aligned + STREAM_REQUEST_SIZE - 1) // STREAM_REQUEST_SIZE

  def generate():
    q = queue.Queue(maxsize=6)
    stop = threading.Event()
    asyncio.run_coroutine_threadsafe(
        telegram_stream_producer(
            q, stop, message_id, kind, aligned, n_chunks, size
        ),
        telethon_loop,
    )
    remaining = length
    to_skip = skip
    try:
      while remaining > 0:
        try:
          chunk = q.get(timeout=120)
        except queue.Empty:
          print(f"[Stream] timeout waiting for data (message {message_id})")
          break
        if chunk is None:
          break
        if to_skip:
          if len(chunk) <= to_skip:
            to_skip -= len(chunk)
            continue
          chunk = chunk[to_skip:]
          to_skip = 0
        if len(chunk) > remaining:
          chunk = chunk[:remaining]
        remaining -= len(chunk)
        yield chunk
    finally:
      stop.set()

  return Response(
      generate(),
      status=status,
      mimetype=mime,
      headers=headers,
      direct_passthrough=True,
  )


@app.route("/api/channel/thumb/<int:message_id>", methods=["GET"])
def get_channel_thumb(message_id):
  try:
    thumb_cache = CACHE_DIR / f"pthumb_{message_id}.jpg"
    if thumb_cache.is_file():
      return send_file(thumb_cache, mimetype="image/jpeg", conditional=True)

    row, row_kind = find_media_row(
        message_id, (request.args.get("kind") or "").strip().lower()
    )
    if not row:
      return "", 404

    content = None
    if telethon_reader_client is not None:
      try:
        content = run_async(
            get_post_thumb_bytes(message_id, row_kind), timeout=60
        )
      except Exception:
        content = None
    if not content:
      return "", 404

    try:
      with open(thumb_cache, "wb") as f:
        f.write(content)
    except Exception:
      pass

    return Response(
        content,
        status=200,
        mimetype="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
  except Exception:
    return "", 404


@app.route("/api/channel/media/<int:message_id>", methods=["GET", "HEAD"])
def get_channel_media(message_id):
  try:
    kind_hint = (request.args.get("kind") or "").strip().lower()
    row, row_kind = find_media_row(message_id, kind_hint)

    if not row:
      return jsonify({"ok": False, "error": "Media not found."}), 404

    media_type = (row["media_type"] or "photo").lower()
    if media_type in ("video", "animation"):
      default_mime = "video/mp4"
    else:
      default_mime = "image/jpeg"

    # 1) লোকাল ক্যাশ থাকলে সেটাই দেওয়া হবে (দ্রুত)
    cached_path = find_cached_media(message_id)
    if cached_path and cached_path.is_file():
      mime_type = mimetypes.guess_type(str(cached_path))[0] or default_mime
      return send_file(
          cached_path, mimetype=mime_type, conditional=True, as_attachment=False
      )

    # 2) সরাসরি Telegram থেকে স্ট্রিম (ডিপ্লয়ের পরও ফাইল হারায় না, বড় ভিডিওও চলে)
    info = None
    if telethon_reader_client is not None:
      try:
        info = run_async(get_media_info(message_id, row_kind), timeout=60)
      except Exception:
        info = None

    if info:
      if info["is_photo"]:
        photo_data = None
        try:
          photo_data = run_async(
              get_photo_bytes(message_id, row_kind), timeout=120
          )
        except Exception:
          photo_data = None
        if photo_data:
          return _ranged_bytes_response(photo_data, info["mime"] or default_mime)
      elif info["size"] > 0:
        return stream_telegram_media(
            message_id, row_kind, info["size"], info["mime"] or default_mime
        )

    # 3) ফলব্যাক: পুরনো পদ্ধতি (Telethon ক্যাশ ডাউনলোড → Bot API)
    try:
      cached_ok = run_async(
          cache_channel_media_telethon(message_id, row_kind), timeout=120
      )
    except Exception:
      cached_ok = False

    if cached_ok:
      cached_path = find_cached_media(message_id)
      if cached_path and cached_path.is_file():
        mime_type = mimetypes.guess_type(str(cached_path))[0] or default_mime
        return send_file(
            cached_path,
            mimetype=mime_type,
            conditional=True,
            as_attachment=False,
        )

    file_id = row["telegram_file_id"]
    if not file_id:
      return jsonify({"ok": False, "error": "Media not found."}), 404

    content, telegram_path = download_telegram_file(file_id)
    if content is None:
      return (
          jsonify({"ok": False, "error": "Telegram media download failed."}),
          404,
      )

    try:
      CACHE_DIR.mkdir(parents=True, exist_ok=True)
      ext = Path(telegram_path or "").suffix or (
          ".mp4" if media_type in ("video", "animation") else ".jpg"
      )
      cache_file = CACHE_DIR / f"{message_id}{ext}"
      with open(cache_file, "wb") as f:
        f.write(content)
    except Exception:
      pass

    mime_type = mimetypes.guess_type(telegram_path or "")[0] or default_mime
    return _ranged_bytes_response(content, mime_type)

  except Exception:
    error_id = uuid.uuid4().hex[:8]
    app.logger.exception("Media streaming failed. Error ID: %s", error_id)
    return (
        jsonify(
            {"ok": False, "error": "Streaming error.", "error_id": error_id}
        ),
        502,
    )


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
  return render_template_string(
      ADMIN_TEMPLATE, logged_in=logged_in, stats=stats, error=error
  )


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
      return (
          jsonify({"ok": False, "error": "Unable to load chat history."}),
          500,
      )

  text = (request.form.get("text") or "").strip()
  client_id = (request.form.get("client_id") or "").strip()[:120]
  client_message_id = (
      request.form.get("client_message_id") or uuid.uuid4().hex
  ).strip()[:120]
  sender_name = "Anonymous User"

  if not client_id:
    client_id = uuid.uuid4().hex

  if len(text) > MAX_CHAT_TEXT_CHARS:
    return (
        jsonify({
            "ok": False,
            "error": (
                f"Message text must be {MAX_CHAT_TEXT_CHARS} characters or"
                " fewer."
            ),
        }),
        400,
    )

  uploaded_file = request.files.get("media")
  if not text and (uploaded_file is None or not uploaded_file.filename):
    return (
        jsonify({"ok": False, "error": "Write a message or attach a file."}),
        400,
    )

  if not BOT_TOKEN:
    return (
        jsonify({
            "ok": False,
            "error": (
                "Chat storage is not configured. Set BOT_TOKEN on the server."
            ),
        }),
        503,
    )

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
      return (
          jsonify({
              "ok": False,
              "error": "Only image and video uploads are allowed.",
          }),
          400,
      )

    suffix = Path(original_filename).suffix[:12]
    media_filename = f"{message_id}{suffix}"
    media_path = MEDIA_DIR / media_filename
    try:
      uploaded_file.save(media_path)
    except Exception:
      return (
          jsonify({"ok": False, "error": "Unable to save uploaded media."}),
          500,
      )

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
    return (
        jsonify({
            "ok": True,
            "message": row_to_message(row),
            "telegram_saved": bool(telegram_saved),
        }),
        201,
    )

  except TelegramNotConfiguredError as exc:
    if media_path and media_path.exists():
      media_path.unlink(missing_ok=True)
    return jsonify({"ok": False, "error": str(exc)}), 503

  except TelegramAPIError as exc:
    if media_path and media_path.exists():
      media_path.unlink(missing_ok=True)
    return (
        jsonify({
            "ok": False,
            "error": "Telegram storage failed. Please try again.",
        }),
        502,
    )

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
    return jsonify({"ok": False, "error": "Unable to save chat message."}), 500

  except Exception:
    return (
        jsonify({
            "ok": False,
            "error": "Unexpected server error while saving the message.",
        }),
        500,
    )


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
          local_path, mimetype=media_type, conditional=True, as_attachment=False
      )

  file_id = row["telegram_file_id"]
  content, telegram_path = download_telegram_file(file_id)
  if content is None:
    return (
        jsonify(
            {"ok": False, "error": "Media is not available on this server."}
        ),
        404,
    )

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
  return jsonify({"total_users_logged_in": total_logged_in_users, **stats})


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(_error):
  return (
      jsonify({
          "ok": False,
          "error": (
              "Upload is too large. Maximum upload size is"
              f" {MAX_UPLOAD_MB} MB."
          ),
      }),
      413,
  )


# ============================================================
# Website pages and background media
# ============================================================


@app.route("/", methods=["GET"])
def home():
  index_path = BASE_DIR / "index.html"
  if index_path.is_file():
    return send_file(index_path)
  return "Alvee Community Server is Running! (index.html not found)", 200


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


@app.route("/all-collection", methods=["GET"])
@app.route("/all-collection.html", methods=["GET"])
def all_collection_page():
  page_path = BASE_DIR / "all-collection.html"
  if page_path.is_file():
    return send_file(page_path)
  return "all-collection.html not found", 404


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
      name
      for name in os.listdir(BG_DIR)
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


@app.route("/oc-channel.js", methods=["GET"])
def oc_channel_script():
  page_path = BASE_DIR / "oc-channel.js"
  if page_path.is_file():
    return send_file(page_path, mimetype="text/javascript")
  return "oc-channel.js not found", 404


@app.route("/style.css", methods=["GET"])
def style_css_file():
  page_path = BASE_DIR / "style.css"
  if page_path.is_file():
    return send_file(page_path, mimetype="text/css")
  return "style.css not found", 404


@app.route("/health", methods=["GET"])
def health_check():
  return "ok", 200


@app.route("/api/telegram-status", methods=["GET"])
def telegram_status_api():
  with db_connect() as conn:
    posts = conn.execute("SELECT COUNT(*) FROM channel_posts").fetchone()[0]
    stories = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
  return jsonify({
      "telethon": dict(
          telethon_status,
          thread_alive=telethon_thread.is_alive(),
          reader_connected=telethon_reader_client is not None
          and telethon_channel_entity is not None,
      ),
      "bot_started": pyrogram_client is not None,
      "env": {
          "BOT_TOKEN": bool(BOT_TOKEN),
          "API_HASH": bool(API_HASH),
          "TELETHON_SESSION_STRING": bool(TELETHON_SESSION_STRING),
      },
      "db": {"channel_posts": posts, "stories": stories},
  })


# ============================================================
# Pyrogram Bot & Media / Story Uploader Initialization
# ============================================================


def build_main_keyboard():
  return InlineKeyboardMarkup([
      [
          InlineKeyboardButton(
              "📤 Upload Media (Photo/Video)",
              callback_data="start_upload",
          )
      ],
      [InlineKeyboardButton("➕ Add Story", callback_data="add_story")],
      [
          InlineKeyboardButton(
              "📡 Scan Channel Media", callback_data="scan_media_channel"
          )
      ],
      [
          InlineKeyboardButton(
              "🎬 Story Scan Media", callback_data="story_scan_media"
          )
      ],
  ])


async def _safe_edit(message, text, reply_markup=None):
  try:
    await message.edit_text(text, reply_markup=reply_markup)
  except Exception as exc:
    print("[Bot] edit_text skipped:", exc)


def _scan_error_hint(exc):
  text = str(exc)
  low = text.lower()
  if "not connected" in low or "telethon" in low:
    return (
        "Telethon session connect হয়নি। TELETHON_SESSION_STRING ঠিক আছে কিনা"
        " এবং ওই অ্যাকাউন্ট চ্যানেলের মেম্বার কিনা দেখুন।"
    )
  if "story channel" in low:
    return "Story চ্যানেলে Telethon অ্যাকাউন্টের অ্যাক্সেস নেই।"
  if "flood" in low:
    return "Telegram সাময়িকভাবে সীমা দিয়েছে, কিছুক্ষণ পরে আবার চেষ্টা করুন।"
  return text[:300]


async def _handle_scan_callback(callback_query, mode):
  if callback_query.from_user.id != ADMIN_ID:
    await callback_query.answer("Unauthorized!", show_alert=True)
    return

  if scan_lock.locked():
    await callback_query.answer(
        "⏳ A scan is already running. Please wait.", show_alert=True
    )
    return

  label = (
      "Story Media (#STORY)"
      if mode == "story"
      else "Channel Media (OC Collection)"
  )
  message = callback_query.message

  try:
    await callback_query.answer("Scan started...")
  except Exception:
    pass

  if (
      not telethon_thread.is_alive()
      or telethon_reader_client is None
      or telethon_channel_entity is None
  ):
    reason = telethon_status.get("last_error") or "still starting, try again"
    await _safe_edit(
        message,
        "❌ Scan failed: Telethon reader is not connected.\n\n"
        f"State: {telethon_status.get('state')}\n"
        f"Reason: {reason}\n\n"
        + _scan_error_hint(RuntimeError("Telethon reader is not connected")),
        reply_markup=build_main_keyboard(),
    )
    return

  await _safe_edit(message, f"⏳ Scanning {label}...\nPlease wait.")

  pg_loop = asyncio.get_running_loop()
  last_update = {"t": 0.0}

  def on_progress(total, found):
    now = time.time()
    if now - last_update["t"] < 5:
      return
    last_update["t"] = now
    asyncio.run_coroutine_threadsafe(
        _safe_edit(
            message,
            f"⏳ Scanning {label}...\nChecked: {total} | Media found: {found}",
        ),
        pg_loop,
    )

  try:
    fut = asyncio.run_coroutine_threadsafe(
        scan_channel(mode, on_progress), telethon_loop
    )
    result = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=3600)

    if mode == "story":
      body = (
          f"🎬 Stories found: {result['stories_found']}\n"
          f"➕ New: {result['stories_added']} | ♻️ Updated:"
          f" {result['stories_updated']}"
      )
    else:
      body = (
          f"📡 Media found: {result['posts_found']}\n"
          f"➕ New: {result['posts_added']} | ♻️ Updated:"
          f" {result['posts_updated']}"
      )
    text = (
        f"✅ Scan complete — {label}\n\n"
        f"📨 Messages checked: {result['checked']}\n"
        f"{body}\n"
        f"🧹 Removed (deleted/changed): {result['removed']}\n\n"
        "Website now updated. ✔️"
    )
  except ScanBusyError:
    text = "⏳ A scan is already running. Please wait."
  except Exception as exc:
    print("[Scan] failed:", traceback.format_exc())
    text = f"❌ Scan failed: {type(exc).__name__}\n\n{_scan_error_hint(exc)}"

  await asyncio.sleep(1)
  await _safe_edit(message, text, reply_markup=build_main_keyboard())


# একসাথে অনেক আইটেম পাঠালে Telegram FLOOD_WAIT দেয়, তাই প্রতিটির মাঝে বিরতি ও অটো-রিট্রাই
UPLOAD_DELAY = float(os.environ.get("UPLOAD_DELAY", "3"))


async def _flood_retry(func, *args, **kwargs):
  """FloodWait এলে নির্দিষ্ট সময় অপেক্ষা করে আবার চেষ্টা করে (সর্বোচ্চ ৬ বার)।"""
  for attempt in range(6):
    try:
      return await func(*args, **kwargs)
    except Exception as exc:
      wait = getattr(exc, "value", None) if type(exc).__name__ == "FloodWait" else None
      if wait is None or attempt == 5:
        raise
      print(f"[Upload] FloodWait {wait}s — waiting then retrying...")
      await asyncio.sleep(int(wait) + 1)


async def start_pyrogram_monitor():
  global pyrogram_client

  if Client is None or filters is None:
    return False

  if not BOT_TOKEN or not API_ID or not API_HASH:
    return False

  try:
    if BOT_TOKEN:
      client = Client(
          "bg_channel_monitor_session",
          api_id=API_ID,
          api_hash=API_HASH,
          bot_token=BOT_TOKEN,
          in_memory=True,
          sleep_threshold=60,
      )
      await client.start()
      pyrogram_client = client
      print("Pyrogram bot started successfully.")

      @client.on_message(filters.command("start") & filters.private)
      async def start_cmd(client, message):
        if message.from_user.id != ADMIN_ID:
          await message.reply("⛔ You are not authorized to use this bot.")
          return

        await message.reply(
            "👋 Welcome Admin!\n\nChoose an option below to upload channel"
            " media, add a new story, or scan the channel to sync the website:",
            reply_markup=build_main_keyboard(),
        )

      @client.on_callback_query(filters.regex("^scan_media_channel$"))
      async def scan_media_channel_callback(client, callback_query):
        await _handle_scan_callback(callback_query, "collection")

      @client.on_callback_query(filters.regex("^story_scan_media$"))
      async def story_scan_media_callback(client, callback_query):
        await _handle_scan_callback(callback_query, "story")

      @client.on_callback_query(filters.regex("^start_upload$"))
      async def start_upload_callback(client, callback_query):
        if callback_query.from_user.id != ADMIN_ID:
          await callback_query.answer("Unauthorized!", show_alert=True)
          return

        pending_uploads[ADMIN_ID] = {
            "mode": "post",
            "waiting_for_media": True,
            "media_group": [],
            "published": False,
        }

        await callback_query.message.edit_text(
            "📥 Please send your photo(s), video(s), or document(s) now.\n\nYou"
            " can send single files sequentially or an album at once. Once"
            " finished, click 'Confirm & Upload'."
        )
        await callback_query.answer()

      @client.on_callback_query(filters.regex("^add_story$"))
      async def add_story_callback(client, callback_query):
        if callback_query.from_user.id != ADMIN_ID:
          await callback_query.answer("Unauthorized!", show_alert=True)
          return

        pending_uploads[ADMIN_ID] = {
            "mode": "story",
            "waiting_for_media": True,
            "media_group": [],
            "published": False,
        }

        await callback_query.message.edit_text(
            "📸 **Add Story Mode**\n\nPlease send the photo(s) or video(s) you"
            " want to add to the website's Story Line.\nOnce finished, click"
            " 'Confirm & Upload'."
        )
        await callback_query.answer()

      @client.on_message(
          filters.private & (filters.photo | filters.video | filters.document)
      )
      async def handle_media_upload(client, message):
        if message.from_user.id != ADMIN_ID:
          return

        user_state = pending_uploads.get(ADMIN_ID, {})
        if not user_state.get("waiting_for_media") or user_state.get(
            "published"
        ):
          return

        user_state["media_group"].append(message)
        pending_uploads[ADMIN_ID] = user_state

        count = len(user_state["media_group"])
        mode_label = (
            "Story" if user_state.get("mode") == "story" else "Channel Media"
        )
        text = (
            f"👍 {mode_label} received ({count} item(s) queued). Send more or"
            " click confirm below."
        )

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Confirm & Upload", callback_data="confirm_upload"
            )
        ]])

        await message.reply(text, reply_markup=keyboard)

      @client.on_callback_query(filters.regex("^confirm_upload$"))
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
            await callback_query.answer(
                "No media found to upload!", show_alert=True
            )
            return

          mode = state.get("mode", "post")
          state["published"] = True
          pending_uploads[ADMIN_ID] = state

        await callback_query.message.edit_text(
            "⏳ Uploading to backup channel... Please wait."
        )

        try:
          target_channel_id = (
              STORY_BACKUP_CHANNEL_ID
              if mode == "story"
              else STORAGE_CHANNEL_ID
          )
          target_chat = await client.get_chat(target_channel_id)
          sent_msgs = []

          if mode == "story":
            total_items = len(media_list)
            for item_no, msg in enumerate(media_list, start=1):
              orig_cap = msg.caption or ""
              story_cap = (
                  f"{STORY_MARKER}\n{orig_cap}".strip()
                  if STORY_MARKER not in orig_cap
                  else orig_cap
              )
              copied_msg = await _flood_retry(
                  msg.copy, target_chat.id, caption=story_cap
              )
              if copied_msg:
                sent_msgs.append(copied_msg)
              if item_no % 5 == 0 and item_no < total_items:
                await _safe_edit(
                    callback_query.message,
                    f"⏳ Uploading stories... {item_no}/{total_items}",
                )
              if item_no < total_items:
                await asyncio.sleep(UPLOAD_DELAY)

            with db_connect() as conn:
              for idx, s_msg in enumerate(sent_msgs):
                m_id = s_msg.id
                m_type = "photo"
                mime = "image/jpeg"
                f_id = None
                thumb_id = None

                if s_msg.photo:
                  m_type = "photo"
                  mime = "image/jpeg"
                  f_id = s_msg.photo.file_id
                elif s_msg.video:
                  m_type = "video"
                  mime = s_msg.video.mime_type or "video/mp4"
                  f_id = s_msg.video.file_id
                  if s_msg.video.thumbs:
                    thumb_id = s_msg.video.thumbs[0].file_id
                elif s_msg.document:
                  doc_mime = (s_msg.document.mime_type or "").lower()
                  if "video" in doc_mime or "animation" in doc_mime:
                    m_type = "video"
                    mime = doc_mime or "video/mp4"
                  else:
                    m_type = "photo"
                    mime = doc_mime or "image/jpeg"
                  f_id = s_msg.document.file_id
                  if getattr(s_msg.document, "thumbs", None):
                    thumb_id = s_msg.document.thumbs[0].file_id

                ts_val = int(time.time()) + idx
                created_at = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )
                conn.execute(
                    """
                                    INSERT OR REPLACE INTO stories (
                                        message_id, media_type, mime_type, telegram_file_id, thumb_file_id, ts, created_at
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """,
                    (m_id, m_type, mime, f_id, thumb_id, ts_val, created_at),
                )
              conn.commit()

            for s_msg in sent_msgs:
              try:
                run_async(
                    cache_channel_media_telethon(s_msg.id, "story"),
                    timeout=600,
                )
              except Exception as cache_err:
                print(
                    f"[Cache] Failed to cache story message {s_msg.id}:"
                    f" {cache_err}"
                )

            pending_uploads.pop(ADMIN_ID, None)
            await callback_query.message.edit_text(
                f"🎉 Successfully added {len(sent_msgs)} story(s) to Backup"
                f" Channel ({STORY_BACKUP_CHANNEL_ID}) and live on Website"
                " Dashboard!",
                reply_markup=build_main_keyboard(),
            )

          else:
            if len(media_list) > 1:
              input_media = []
              for msg in media_list:
                caption = msg.caption or ""
                if msg.photo:
                  input_media.append(
                      InputMediaPhoto(msg.photo.file_id, caption=caption)
                  )
                elif msg.video:
                  input_media.append(
                      InputMediaVideo(msg.video.file_id, caption=caption)
                  )
                elif msg.document:
                  input_media.append(
                      InputMediaDocument(msg.document.file_id, caption=caption)
                  )

              if input_media:
                for i in range(0, len(input_media), 10):
                  chunk = input_media[i : i + 10]
                  res_group = await _flood_retry(
                      client.send_media_group,
                      chat_id=target_chat.id,
                      media=chunk,
                  )
                  if res_group:
                    sent_msgs.extend(res_group)
                  await asyncio.sleep(UPLOAD_DELAY)
            else:
              single_msg = await _flood_retry(
                  media_list[0].copy, target_chat.id
              )
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

                created_at = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )
                conn.execute(
                    """
                                    INSERT OR REPLACE INTO channel_posts (
                                        message_id, text, media_type, mime_type, telegram_file_id, created_at
                                    ) VALUES (?, ?, ?, ?, ?, ?)
                                    """,
                    (m_id, text_content, m_type, mime, f_id, created_at),
                )
              conn.commit()

            for s_msg in sent_msgs:
              try:
                run_async(
                    cache_channel_media_telethon(s_msg.id, "post"),
                    timeout=600,
                )
              except Exception as cache_err:
                print(
                    f"[Cache] Failed to cache message {s_msg.id}: {cache_err}"
                )

            pending_uploads.pop(ADMIN_ID, None)

            await callback_query.message.edit_text(
                f"🎉 Successfully published {len(media_list)} file(s) to the"
                " Telegram channel and synced with the website!\n(Media also"
                " cached locally via Telethon)",
                reply_markup=build_main_keyboard(),
            )
        except Exception as e:
          with upload_lock:
            if ADMIN_ID in pending_uploads:
              pending_uploads[ADMIN_ID]["published"] = False
          await callback_query.message.edit_text(
              f"❌ Upload failed: {str(e)}",
              reply_markup=build_main_keyboard(),
          )

        await callback_query.answer()

    return True

  except Exception:
    print("Pyrogram startup error:", traceback.format_exc())
    return False


# ============================================================
# Background Services Startup (Non-Blocking for Render)
# ============================================================


def _init_background_services_worker():
  # 1) Pyrogram বট (আগে চালু হয়, যাতে ডিপ্লয়ের পরই বট সাড়া দেয়)
  if Client and BOT_TOKEN and API_ID and API_HASH:
    if not pyrogram_thread.is_alive():
      pyrogram_thread.start()
    if pyrogram_loop_ready.wait(timeout=10):
      try:
        run_pyrogram_async(start_pyrogram_monitor(), timeout=120)
      except Exception:
        print("Could not start Pyrogram monitor:", traceback.format_exc())

  # 2) Telethon রিডার (ডুপ্লিকেট সেশন এড়াতে সামান্য অপেক্ষার পর)
  if TELETHON_SESSION_STRING and API_ID and API_HASH:
    if TELETHON_START_DELAY > 0:
      telethon_status["state"] = "waiting"
      print(
          f"[Telethon] waiting {TELETHON_START_DELAY:.0f}s before connecting"
          " (avoids duplicate-session errors during deploy)..."
      )
      time.sleep(TELETHON_START_DELAY)
      telethon_status["state"] = "starting"
    if not telethon_thread.is_alive():
      telethon_thread.start()
    if telethon_loop_ready.wait(timeout=10):
      try:
        reader_ok = run_async(start_telethon_reader(), timeout=180)
      except Exception:
        reader_ok = False
        print("Could not start Telethon reader:", traceback.format_exc())

      if reader_ok:
        try:
          asyncio.run_coroutine_threadsafe(
              sync_posts_from_channel(), telethon_loop
          )
        except Exception:
          pass


_instance_lock_file = None


def _acquire_single_instance_lock():
  """একই সার্ভারে একাধিক worker থাকলে শুধু একটি Telegram সার্ভিস চালাবে।"""
  global _instance_lock_file
  if fcntl is None:
    return True
  try:
    lock_file = open("/tmp/alvee_telegram_services.lock", "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _instance_lock_file = lock_file
    return True
  except Exception:
    return False


def start_background_services():
  global _services_started
  with _services_lock:
    if _services_started:
      return
    _services_started = True

  if not _acquire_single_instance_lock():
    print("[Startup] Another worker already runs Telegram services; skipping.")
    return

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
  app.run(
      host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False
  )
