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
import cloudscraper
from bs4 import BeautifulSoup

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

SEPARATE_STORY_CHANNEL = int(STORY_BACKUP_CHANNEL_ID) != int(STORAGE_CHANNEL_ID)

COLLECTION_INCLUDE_CAPTIONED = (
    os.environ.get("COLLECTION_INCLUDE_CAPTIONED", "0").strip() == "1"
)
PRECACHE_MEDIA = os.environ.get("PRECACHE_MEDIA", "0").strip() == "1"
STREAM_REQUEST_SIZE = 512 * 1024  # Telethon download chunk (512 KB)
MSG_CACHE_TTL = 600  # সেকেন্ড
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

telethon_loop = asyncio.new_event_loop()
telethon_loop_ready = threading.Event()
telethon_thread = threading.Thread(
    target=lambda: _run_telethon_loop(),
    name="telethon-background-loop",
    daemon=True,
)

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
scan_lock = threading.Lock()

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
# SQLite Database Initialization
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
            CREATE TABLE IF NOT EXISTS site_counters (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
        """)
    # স্ক্র্যাপ করা অ্যালবামের ডুপ্লিকেট রোধ করার লগ টেবিল
    conn.execute("""
            CREATE TABLE IF NOT EXISTS scraped_slugs (
                slug TEXT PRIMARY KEY
            )
        """)
    conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_created_at
            ON chat_messages(created_at)
        """)
    conn.commit()


init_db()


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


def find_media_row(message_id, kind=""):
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
# Telegram Bot API & Scraping Helpers
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


# mmsdesi.me অটোমেটিক স্ক্র্যাপার লজিক (৫ থেকে ৫০টি মিডিয়া সহ অ্যালবাম ফেচ করা)
def fetch_mmsdesi_albums(target_count=40):
  scraper = cloudscraper.create_scraper(
      browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
  )
  base_url = 'https://www.mmsdesi.me'
  category_urls = [
      'https://www.mmsdesi.me/',
      'https://www.mmsdesi.me/trending',
      'https://www.mmsdesi.me/desi-new',
      'https://www.mmsdesi.me/desi-old-videos',
      'https://www.mmsdesi.me/live-stream'
  ]

  with db_connect() as conn:
    posted_slugs = {
        row['slug'] for row in conn.execute("SELECT slug FROM scraped_slugs").fetchall()
    }

  albums = []
  seen_slugs = set()

  for url in category_urls:
    if len(albums) >= target_count:
      break
    try:
      res = scraper.get(url, timeout=20)
      soup = BeautifulSoup(res.text, 'html.parser')

      for card in soup.select('div.group.relative.bg-zinc-900'):
        link_tag = card.find('a', href=re.compile(r'^/watch/'))
        if not link_tag:
          continue

        href = link_tag.get('href')
        slug = href.split('/watch/')[-1].strip('/')
        if slug in seen_slugs or slug in posted_slugs:
          continue

        h3_tag = card.find('h3')
        caption = h3_tag.get_text(strip=True) if h3_tag else (link_tag.get('aria-label') or slug)

        img_tag = link_tag.find('img')
        thumb_url = img_tag.get('src') if img_tag else ''

        seen_slugs.add(slug)
        albums.append({
            'slug': slug,
            'url': f"{base_url}/watch/{slug}",
            'caption': caption,
            'thumbnail': thumb_url
        })
        if len(albums) >= target_count:
          break
    except Exception as e:
      print(f"[Scraper Error] {url}: {e}")

  return albums


def extract_album_media(album_url, fallback_thumb):
  scraper = cloudscraper.create_scraper(
      browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
  )
  videos = []
  photos = []
  try:
    res = scraper.get(album_url, timeout=20)
    html_text = res.text

    found_videos = re.findall(r'https://cdn\.mmsdesi\.me/videos/[^\s"\'\\<>]+\.mp4', html_text)
    for v in found_videos:
      if v not in videos:
        videos.append(v)

    found_photos = re.findall(r'https://cdn\.mmsdesi\.me/photos/[^\s"\'\\<>]+\.(?:jpg|jpeg|png|webp)', html_text)
    for p in found_photos:
      if p not in photos:
        photos.append(p)

    if not photos and fallback_thumb:
      photos.append(fallback_thumb)
  except Exception as e:
    print(f"[Inner Page Error] {album_url}: {e}")

  return videos, photos


# ============================================================
# Telethon Helpers & Channel Streaming
# ============================================================

_msg_cache = {}
_photo_cache = {}


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
    return False
  entity = _entity_for_kind(kind)
  if entity is None:
    return False

  try:
    msgs = await telethon_reader_client.get_messages(entity, ids=message_id)
    msg = msgs[0] if isinstance(msgs, (list, tuple)) else msgs
    if not msg or not getattr(msg, "media", None):
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
    return bool(downloaded_path)
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
# Channel Scanning & Syncing
# ============================================================


def _classify_media_for_scan(msg):
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
  if not telethon_reader_client or not telethon_channel_entity:
    raise RuntimeError("Telethon reader is not connected.")
  if mode in ("story", "all") and SEPARATE_STORY_CHANNEL and (
      telethon_story_entity is None
  ):
    raise RuntimeError("Story channel is not accessible.")
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


async def sync_posts_from_channel():
  if not telethon_reader_client or not telethon_channel_entity:
    return
  try:
    await scan_channel("all")
  except Exception:
    pass


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
      temp_client_sessions[phone] = client
      return {"status": "success", "phone_code_hash": sent.phone_code_hash}
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
    return jsonify({"status": "error", "message": "Session expired."}), 400

  async def async_verify():
    try:
      if not client.is_connected():
        await client.connect()
      await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
      global total_logged_in_users
      total_logged_in_users += 1
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
# Telethon Reader Initialization
# ============================================================


async def start_telethon_reader():
  global telethon_reader_client, telethon_channel_entity, telethon_story_entity
  if not TELETHON_SESSION_STRING:
    return False

  for attempt in range(1, 6):
    telethon_status["attempts"] = attempt
    telethon_status["state"] = "starting"
    client = TelegramClient(StringSession(TELETHON_SESSION_STRING), API_ID, API_HASH)
    try:
      await client.connect()
      if not await client.is_user_authorized():
        await client.disconnect()
        await asyncio.sleep(3)
        continue

      channel_entity = await client.get_entity(STORAGE_CHANNEL_ID)
      if not channel_entity:
        channel_entity = await _find_entity_in_dialogs(client, STORAGE_CHANNEL_ID)

      if not channel_entity:
        await client.disconnect()
        await asyncio.sleep(3)
        continue

      telethon_reader_client = client
      telethon_channel_entity = channel_entity
      telethon_story_entity = channel_entity
      telethon_status["state"] = "connected"
      return True
    except Exception as e:
      if client.is_connected():
        try:
          await client.disconnect()
        except Exception:
          pass
      await asyncio.sleep(5)
  telethon_status["state"] = "failed"
  return False


# ============================================================
# API Endpoints (Stories, Collection, Media, etc.)
# ============================================================


@app.route("/api/stories", methods=["GET"])
def get_stories_api():
  try:
    with db_connect() as conn:
      rows = conn.execute("SELECT * FROM stories ORDER BY ts DESC").fetchall()
    stories_list = []
    for row in rows:
      mid = row["message_id"]
      m_type = "photo" if row["media_type"] == "photo" else "video"
      media_url = f"/api/channel/media/{mid}"
      thumb_url = media_url if m_type == "photo" else f"/api/stories/thumb/{mid}"
      stories_list.append({
          "id": str(mid),
          "type": m_type,
          "thumb": thumb_url,
          "src": media_url,
          "ts": int(row["ts"] or 0),
      })
    return jsonify(stories_list)
  except Exception:
    return jsonify([]), 200


@app.route("/api/stories/thumb/<int:message_id>", methods=["GET"])
def get_story_thumb(message_id):
  try:
    thumb_cache = CACHE_DIR / f"thumb_{message_id}.jpg"
    if thumb_cache.is_file():
      return send_file(thumb_cache, mimetype="image/jpeg", conditional=True)
    content = run_async(get_telethon_thumb_bytes(message_id, "story"), timeout=60)
    if not content:
      return redirect(DEFAULT_STORY_THUMB)
    try:
      with open(thumb_cache, "wb") as f:
        f.write(content)
    except Exception:
      pass
    return Response(content, status=200, mimetype="image/jpeg")
  except Exception:
    return redirect(DEFAULT_STORY_THUMB)


@app.route("/api/channel/posts", methods=["GET"])
@app.route("/api/get_videos", methods=["GET"])
def get_channel_posts_api():
  try:
    limit = request.args.get("limit", 1000, type=int) or 1000
    with db_connect() as conn:
      rows = conn.execute(
          "SELECT * FROM (SELECT * FROM channel_posts ORDER BY message_id DESC LIMIT ?) ORDER BY message_id ASC",
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
    videos = [p["media_url"] for p in posts if p["media_type"] in ("video", "animation")]
    return jsonify({"ok": True, "status": "success", "count": len(posts), "posts": posts, "videos": videos})
  except Exception:
    return jsonify({"ok": False, "status": "error", "posts": [], "videos": []}), 502


@app.route("/api/collection/counts", methods=["GET"])
def collection_counts_api():
  try:
    with db_connect() as conn:
      videos = conn.execute("SELECT COUNT(*) FROM channel_posts WHERE media_type IN ('video', 'animation')").fetchone()[0]
      photos = conn.execute("SELECT COUNT(*) FROM channel_posts WHERE media_type NOT IN ('video', 'animation')").fetchone()[0]
    return jsonify({"ok": True, "videos": int(videos or 0), "photos": int(photos or 0)})
  except Exception:
    return jsonify({"ok": False, "videos": 0, "photos": 0}), 500


@app.route("/api/collection/media", methods=["GET"])
def collection_media_api():
  try:
    with db_connect() as conn:
      rows = conn.execute("SELECT message_id, media_type FROM channel_posts ORDER BY message_id DESC LIMIT 500").fetchall()
    items = []
    for row in rows:
      mid = row["message_id"]
      items.append({
          "id": str(mid),
          "type": "video" if row["media_type"] in ("video", "animation") else "photo",
          "thumb": f"/api/channel/thumb/{mid}",
          "media": f"/api/channel/media/{mid}",
      })
    return jsonify({"ok": True, "items": items})
  except Exception:
    return jsonify({"ok": False, "items": []}), 500


@app.route("/api/site/views", methods=["GET", "POST"])
def site_views_api():
  try:
    with db_connect() as conn:
      if request.method == "POST":
        conn.execute("INSERT INTO site_counters (name, value) VALUES ('site_views', 1) ON CONFLICT(name) DO UPDATE SET value = value + 1")
        conn.commit()
      row = conn.execute("SELECT value FROM site_counters WHERE name = 'site_views'").fetchone()
    return jsonify({"ok": True, "total": int(row[0]) if row else 0})
  except Exception:
    return jsonify({"ok": False, "total": 0}), 500


def _parse_range_header(range_header, size):
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
    start = max(0, size - length)
    end = size - 1
  else:
    start = int(a)
    end = int(b) if b else size - 1
    end = min(end, size - 1)
  if start >= size or start > end:
    raise ValueError("unsatisfiable")
  return start, end


def stream_telegram_media(message_id, kind, size, mime):
  try:
    rng = _parse_range_header(request.headers.get("Range"), size)
  except ValueError:
    return Response(status=416, headers={"Content-Range": f"bytes */{size}"})

  start, end = (rng if rng else (0, size - 1))
  status = 206 if rng else 200
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
        telegram_stream_producer(q, stop, message_id, kind, aligned, n_chunks, size),
        telethon_loop,
    )
    remaining = length
    to_skip = skip
    try:
      while remaining > 0:
        try:
          chunk = q.get(timeout=120)
        except queue.Empty:
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

  return Response(generate(), status=status, mimetype=mime, headers=headers, direct_passthrough=True)


@app.route("/api/channel/thumb/<int:message_id>", methods=["GET"])
def get_channel_thumb(message_id):
  try:
    thumb_cache = CACHE_DIR / f"pthumb_{message_id}.jpg"
    if thumb_cache.is_file():
      return send_file(thumb_cache, mimetype="image/jpeg", conditional=True)
    row, row_kind = find_media_row(message_id, request.args.get("kind", ""))
    if not row:
      return "", 404
    content = run_async(get_post_thumb_bytes(message_id, row_kind), timeout=60)
    if not content:
      return "", 404
    try:
      with open(thumb_cache, "wb") as f:
        f.write(content)
    except Exception:
      pass
    return Response(content, status=200, mimetype="image/jpeg")
  except Exception:
    return "", 404


@app.route("/api/channel/media/<int:message_id>", methods=["GET", "HEAD"])
def get_channel_media(message_id):
  try:
    row, row_kind = find_media_row(message_id, request.args.get("kind", ""))
    if not row:
      return jsonify({"ok": False, "error": "Media not found."}), 404

    media_type = (row["media_type"] or "photo").lower()
    default_mime = "video/mp4" if media_type in ("video", "animation") else "image/jpeg"

    cached_path = find_cached_media(message_id)
    if cached_path and cached_path.is_file():
      return send_file(cached_path, mimetype=default_mime, conditional=True, as_attachment=False)

    info = run_async(get_media_info(message_id, row_kind), timeout=60)
    if info:
      if info["is_photo"]:
        photo_data = run_async(get_photo_bytes(message_id, row_kind), timeout=120)
        if photo_data:
          return Response(photo_data, mimetype=info["mime"] or default_mime)
      elif info["size"] > 0:
        return stream_telegram_media(message_id, row_kind, info["size"], info["mime"] or default_mime)

    return jsonify({"ok": False, "error": "Media unavailable."}), 404
  except Exception:
    return jsonify({"ok": False, "error": "Streaming error."}), 502


# ============================================================
# Website Frontend Routes (100% Same)
# ============================================================


@app.route("/", methods=["GET"])
def home():
  index_path = BASE_DIR / "index.html"
  if index_path.is_file():
    return send_file(index_path)
  return "Alvee Community Server is Running!", 200


@app.route("/verify.html", methods=["GET"])
def verify_page():
  return send_file(BASE_DIR / "verify.html") if (BASE_DIR / "verify.html").is_file() else ("verify.html not found", 404)


@app.route("/dashboard.html", methods=["GET"])
def dashboard_page():
  return send_file(BASE_DIR / "dashboard.html") if (BASE_DIR / "dashboard.html").is_file() else ("dashboard.html not found", 404)


@app.route("/oc-collection.html", methods=["GET"])
def oc_collection_page():
  return send_file(BASE_DIR / "oc-collection.html") if (BASE_DIR / "oc-collection.html").is_file() else ("oc-collection.html not found", 404)


@app.route("/all-collection", methods=["GET"])
@app.route("/all-collection.html", methods=["GET"])
def all_collection_page():
  return send_file(BASE_DIR / "all-collection.html") if (BASE_DIR / "all-collection.html").is_file() else ("all-collection.html not found", 404)


@app.route("/get-random-background", methods=["GET"])
def get_random_background():
  BG_DIR.mkdir(parents=True, exist_ok=True)
  files = [name for name in os.listdir(BG_DIR) if (BG_DIR / name).is_file()]
  if not files:
    return jsonify({
        "status": "success",
        "url": "https://carder.top/imagens/1792298157180-542085424.jpg",
        "type": "image",
        "blur": "3px",
    })
  chosen = random.choice(files)
  return jsonify({
      "status": "success",
      "url": f"/static/backgrounds/{chosen}?v={int(time.time())}",
      "type": "video" if chosen.endswith((".mp4", ".mov")) else "image",
      "blur": "3px",
  })


@app.route("/oc-channel.js", methods=["GET"])
def oc_channel_script():
  return send_file(BASE_DIR / "oc-channel.js", mimetype="text/javascript") if (BASE_DIR / "oc-channel.js").is_file() else ("oc-channel.js not found", 404)


@app.route("/style.css", methods=["GET"])
def style_css_file():
  return send_file(BASE_DIR / "style.css", mimetype="text/css") if (BASE_DIR / "style.css").is_file() else ("style.css not found", 404)


@app.route("/health", methods=["GET"])
def health_check():
  return "ok", 200


# ============================================================
# Pyrogram Bot & Scanning Button Setup
# ============================================================


def build_main_keyboard():
  return InlineKeyboardMarkup([
      [InlineKeyboardButton("📡 Scanning Website Videos", callback_data="scan_media_channel")],
      [InlineKeyboardButton("📤 Upload Media", callback_data="start_upload")],
      [InlineKeyboardButton("➕ Add Story", callback_data="add_story")],
  ])


async def _safe_edit(message, text, reply_markup=None):
  try:
    await message.edit_text(text, reply_markup=reply_markup)
  except Exception:
    pass


def _download_bytes(url, name, max_mb=50):
  scraper = cloudscraper.create_scraper(
      browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
  )
  res = scraper.get(
      url, timeout=60, stream=True,
      headers={'Referer': 'https://www.mmsdesi.me/'},
  )
  res.raise_for_status()
  buf = BytesIO()
  for part in res.iter_content(256 * 1024):
    buf.write(part)
    if buf.tell() > max_mb * 1024 * 1024:
      raise ValueError("file too large")
  buf.seek(0)
  buf.name = name
  return buf


async def _send_video_safe(chat_id, url, caption):
  try:
    return await pyrogram_client.send_video(chat_id, video=url, caption=caption)
  except Exception:
    try:
      buf = await asyncio.to_thread(_download_bytes, url, "video.mp4", 50)
      return await pyrogram_client.send_video(
          chat_id, video=buf, caption=caption, supports_streaming=True
      )
    except Exception as e:
      print(f"[Upload] video failed {url}: {e}")
      return None


async def _send_photos_safe(chat_id, photos, caption):
  sent = []
  for i in range(0, len(photos), 10):
    chunk = photos[i:i + 10]
    cap = caption if i == 0 else ""
    for use_download in (False, True):
      try:
        items = chunk
        if use_download:
          items = []
          for n, p in enumerate(chunk):
            items.append(await asyncio.to_thread(_download_bytes, p, f"photo{n}.jpg", 10))
        if len(items) > 1:
          media = [
              InputMediaPhoto(m, caption=cap if idx == 0 else "")
              for idx, m in enumerate(items)
          ]
          msgs = await pyrogram_client.send_media_group(chat_id, media=media)
          sent.extend(msgs)
        else:
          sent.append(
              await pyrogram_client.send_photo(chat_id, photo=items[0], caption=cap)
          )
        break
      except Exception as e:
        print(f"[Upload] photos failed (download={use_download}): {e}")
    await asyncio.sleep(2)
  return sent


async def _handle_scan_callback(callback_query, mode="collection"):
  if callback_query.from_user.id != ADMIN_ID:
    await callback_query.answer("Unauthorized!", show_alert=True)
    return

  if scan_lock.locked():
    await callback_query.answer("⏳ A scan is already running.", show_alert=True)
    return

  message = callback_query.message
  await callback_query.answer("Scanning website albums...")
  await _safe_edit(message, "🔄 Scraping albums from mmsdesi.me and storing to Telegram...")

  try:
    albums = await asyncio.to_thread(fetch_mmsdesi_albums, 45)
    if not albums:
      await _safe_edit(message, "ℹ️ No new albums found.", reply_markup=build_main_keyboard())
      return

    added_count = 0
    target_chat = await pyrogram_client.get_chat(STORAGE_CHANNEL_ID)

    for album in albums:
      videos, photos = await asyncio.to_thread(
          extract_album_media, album['url'], album['thumbnail']
      )
      if not videos and not photos:
        continue

      caption = album['caption']
      sent_msgs = []

      for v in videos:
        msg = await _send_video_safe(target_chat.id, v, caption)
        if msg:
          sent_msgs.append(msg)
        await asyncio.sleep(2)

      if photos:
        sent_msgs.extend(await _send_photos_safe(target_chat.id, photos, caption))

      # কিছুই আপলোড না হলে slug সেভ করা হবে না, যাতে পরে আবার চেষ্টা হয়
      if not sent_msgs:
        continue

      with db_connect() as conn:
        for s_msg in sent_msgs:
          text_content = s_msg.caption or s_msg.text or caption
          m_type = "video" if s_msg.video else "photo"
          mime = s_msg.video.mime_type if s_msg.video else "image/jpeg"
          f_id = (
              s_msg.video.file_id if s_msg.video
              else (s_msg.photo.file_id if s_msg.photo else None)
          )
          conn.execute(
              "INSERT OR REPLACE INTO channel_posts (message_id, text, media_type, mime_type, telegram_file_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (s_msg.id, text_content, m_type, mime, f_id, datetime.now(timezone.utc).isoformat()),
          )
        conn.execute("INSERT OR IGNORE INTO scraped_slugs (slug) VALUES (?)", (album['slug'],))
        conn.commit()

      added_count += 1
      await asyncio.sleep(2)

    # Telethon ক্লায়েন্ট তার নিজের লুপেই চালাতে হবে
    if telethon_reader_client:
      try:
        fut = asyncio.run_coroutine_threadsafe(scan_channel("all"), telethon_loop)
        await asyncio.wrap_future(fut)
      except Exception as e:
        print("Post-upload scan error:", e)

    await _safe_edit(
        message,
        f"✅ {added_count} new albums uploaded & website synced! ✔️",
        reply_markup=build_main_keyboard(),
    )
  except Exception as e:
    print("Scan error:", traceback.format_exc())
    await _safe_edit(message, f"❌ Scan failed: {str(e)}", reply_markup=build_main_keyboard())


async def start_pyrogram_monitor():
  global pyrogram_client
  if Client is None or not BOT_TOKEN or not API_ID or not API_HASH:
    return False

  try:
    client = Client(
        "bg_channel_monitor_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    )
    await client.start()
    pyrogram_client = client

    @client.on_message(filters.command("start") & filters.private)
    async def start_cmd(client, message):
      if message.from_user.id != ADMIN_ID:
        return
      await message.reply(
          "👋 Welcome Admin!\n\nClick below to scan and fetch website videos into your channel and website:",
          reply_markup=build_main_keyboard(),
      )

    @client.on_callback_query(filters.regex("^scan_media_channel$"))
    async def scan_media_channel_callback(client, callback_query):
      await _handle_scan_callback(callback_query, "collection")

    return True
  except Exception:
    print("Pyrogram startup error:", traceback.format_exc())
    return False


# ============================================================
# Background Services Startup
# ============================================================


def _init_background_services_worker():
  if Client and BOT_TOKEN and API_ID and API_HASH:
    if not pyrogram_thread.is_alive():
      pyrogram_thread.start()
    if pyrogram_loop_ready.wait(timeout=10):
      try:
        run_pyrogram_async(start_pyrogram_monitor(), timeout=120)
      except Exception:
        pass

  if TELETHON_SESSION_STRING and API_ID and API_HASH:
    if TELETHON_START_DELAY > 0:
      time.sleep(TELETHON_START_DELAY)
    if not telethon_thread.is_alive():
      telethon_thread.start()
    if telethon_loop_ready.wait(timeout=10):
      try:
        reader_ok = run_async(start_telethon_reader(), timeout=180)
      except Exception:
        reader_ok = False
      if reader_ok:
        try:
          asyncio.run_coroutine_threadsafe(sync_posts_from_channel(), telethon_loop)
        except Exception:
          pass


def start_background_services():
  global _services_started
  with _services_lock:
    if _services_started:
      return
    _services_started = True

  startup_thread = threading.Thread(
      target=_init_background_services_worker,
      name="background-services-initializer",
      daemon=True,
  )
  startup_thread.start()


start_background_services()

if __name__ == "__main__":
  port = int(os.environ.com.get("PORT", "10000")) if hasattr(os.environ, "com") else int(os.environ.get("PORT", "10000"))
  app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
