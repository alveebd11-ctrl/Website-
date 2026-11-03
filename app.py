import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import html as html_lib
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
from urllib.parse import urljoin, quote
import uuid
import cloudscraper
from bs4 import BeautifulSoup

# Cloudflare 403 বাইপাসের জন্য curl_cffi (Chrome TLS Impersonation)
try:
  from curl_cffi import requests as cf_requests
except ImportError:
  cf_requests = None

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

# ওয়েবসাইট থেকে আনা সব মিডিয়া চিহ্নিত করার ক্যাপশন ট্যাগ
WEBSITE_MARKER = "#website"

# চ্যানেলে আপলোড-সম্পূর্ণ মেসেজে এই কী দিয়ে অ্যালবামের আইডি লেখা থাকে (ডুপ্লিকেট রোধে ব্যবহৃত)
ALBUM_REF_KEY = "albumref"

SEPARATE_STORY_CHANNEL = int(STORY_BACKUP_CHANNEL_ID) != int(STORAGE_CHANNEL_ID)

# ওয়েবসাইট থেকে আনা (#website) পোস্ট আপনার সাইটে দেখানো হবে কি না।
# 0 (ডিফল্ট) = শুধু টেলিগ্রাম চ্যানেলে আপলোড হবে, সাইটে দেখাবে না
# 1 = আগের মতো সাইটেও দেখাবে
SYNC_WEBSITE_POSTS = os.environ.get("SYNC_WEBSITE_POSTS", "0").strip() == "1"

COLLECTION_INCLUDE_CAPTIONED = (
    os.environ.get("COLLECTION_INCLUDE_CAPTIONED", "0").strip() == "1"
)

# OC কালেকশনে (oc-collection.html) ওয়েবসাইটের ফটো/ভিডিও আসবে না।
# ওয়েবসাইট-আপলোডগুলোর ক্যাপশন থাকে, আর হাতে আপলোড করা OC মিডিয়ার ক্যাপশন খালি।
if COLLECTION_INCLUDE_CAPTIONED:
  OC_WHERE = "LOWER(COALESCE(text, '')) NOT LIKE '%" + WEBSITE_MARKER.lower() + "%'"
else:
  OC_WHERE = "TRIM(COALESCE(text, '')) = ''"
PRECACHE_MEDIA = os.environ.get("PRECACHE_MEDIA", "0").strip() == "1"
STREAM_REQUEST_SIZE = 512 * 1024  # Telethon download chunk (512 KB)
MSG_CACHE_TTL = 600  # সেকেন্ড
TELETHON_START_DELAY = float(os.environ.get("TELETHON_START_DELAY", "40") or 0)

# ---- Scraper configuration ----
# TARGET_SITE_URL         : যে ওয়েবসাইট থেকে অ্যালবাম স্ক্র্যাপ হবে (যেমন https://mmsdesi.me)
# TARGET_CATEGORY_PATHS   : কমা দিয়ে আলাদা পেজ পাথ, ডিফল্ট "/,/trending,/new"
# SCRAPER_PROXY           : (ঐচ্ছিক) Cloudflare ব্লক করলে প্রক্সি, যেমন http://user:pass@host:port
# SCRAPER_PAGES           : প্রতিটি ক্যাটাগরির কয়টা পেজ দেখবে (ডিফল্ট 2)
# ALBUM_SCAN_LIMIT        : এক স্ক্যানে সর্বোচ্চ কতটি অ্যালবাম আনবে (ডিফল্ট 50)
# ALBUM_CHUNK_SIZE        : টেলিগ্রামে এক অ্যালবাম পোস্টে কয়টি করে মিডিয়া (ডিফল্ট 10, টেলিগ্রামের সর্বোচ্চ সীমা 10)
# VIDEO_MAX_MB / PHOTO_MAX_MB : ডাউনলোড ফলব্যাকের সর্বোচ্চ ফাইল সাইজ
TARGET_SITE_URL = os.environ.get("TARGET_SITE_URL", "https://mmsdesi.me").strip().rstrip("/")
SCRAPER_PROXY = os.environ.get("SCRAPER_PROXY", "").strip()
SCRAPER_PAGES = max(1, int(os.environ.get("SCRAPER_PAGES", "2") or 2))
TARGET_CATEGORY_PATHS = [
    p.strip()
    for p in os.environ.get("TARGET_CATEGORY_PATHS", "/,/trending,/new").split(",")
    if p.strip()
]
ALBUM_SCAN_LIMIT = max(1, int(os.environ.get("ALBUM_SCAN_LIMIT", "50") or 50))
ALBUM_CHUNK_SIZE = min(10, max(2, int(os.environ.get("ALBUM_CHUNK_SIZE", "10") or 10)))
VIDEO_MAX_MB = max(5, int(os.environ.get("VIDEO_MAX_MB", "150") or 150))
# এক গ্রুপ (১০টি ফাইল) আপলোডে সর্বোচ্চ কত সেকেন্ড অপেক্ষা; আটকে গেলে পরের গ্রুপে চলে যাবে
CHUNK_TIMEOUT = max(60, int(os.environ.get("CHUNK_TIMEOUT_SEC", "900") or 900))
# 1 দিলে ভিডিও-সহ গ্রুপেও আগে টেলিগ্রামকে URL থেকে আনার চেষ্টা করাবে (ধীর/অনির্ভরযোগ্য, তাই ডিফল্ট বন্ধ)
TRY_URL_FIRST = os.environ.get("TRY_URL_FIRST", "0").strip() == "1"
PHOTO_MAX_MB = max(1, int(os.environ.get("PHOTO_MAX_MB", "10") or 10))

print("OC & Story Storage Channel ID:", STORAGE_CHANNEL_ID)
for _name, _val in (
    ("BOT_TOKEN", BOT_TOKEN),
    ("API_ID", API_ID),
    ("API_HASH", API_HASH),
    ("ADMIN_ID", ADMIN_ID),
    ("TELETHON_SESSION_STRING", TELETHON_SESSION_STRING),
    ("TARGET_SITE_URL", TARGET_SITE_URL),
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
scrape_lock = threading.Lock()  # দুইবার চাপ পড়লে ডাবল স্ক্র্যাপ রোধ

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
    # ওয়েবসাইট-অ্যালবাম সিস্টেম: একটি অ্যালবাম + তার ভেতরের সব ফটো/ভিডিও
    conn.execute("""
            CREATE TABLE IF NOT EXISTS website_albums (
                slug TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT
            )
        """)
    conn.execute("""
            CREATE TABLE IF NOT EXISTS website_album_items (
                message_id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL,
                media_type TEXT,
                mime_type TEXT,
                pos INTEGER NOT NULL DEFAULT 0
            )
        """)
    conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_album_items_slug
            ON website_album_items(slug)
        """)
    # পুরনো ডাটাবেসে নতুন কলাম যোগ (ক্রম ও কভারের জন্য)
    for _col, _ddl in (
        ("batch", "INTEGER DEFAULT 0"),
        ("site_order", "INTEGER DEFAULT 0"),
        ("cover_id", "INTEGER"),
    ):
      try:
        conn.execute(f"ALTER TABLE website_albums ADD COLUMN {_col} {_ddl}")
      except sqlite3.OperationalError:
        pass
    conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_created_at
            ON chat_messages(created_at)
        """)
    conn.commit()


init_db()


def _cleanup_website_posts():
  """সিঙ্ক বন্ধ থাকলে আগে সেভ হওয়া #website পোস্ট ডাটাবেস থেকে সরিয়ে দেয় (চ্যানেলের পোস্ট অক্ষত থাকে)।"""
  if SYNC_WEBSITE_POSTS:
    return
  try:
    with db_connect() as conn:
      conn.execute(
          "DELETE FROM channel_posts WHERE LOWER(COALESCE(text, '')) LIKE ?",
          ("%" + WEBSITE_MARKER.lower() + "%",),
      )
      conn.commit()
  except Exception as e:
    print("[Cleanup] website posts cleanup failed:", e)


_cleanup_website_posts()


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
    for table, row_kind in order + (("website_album_items", "post"),):
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


# ------------------------------------------------------------
# অটোমেটিক স্ক্র্যাপার লজিক (Cloudflare Bypass + Fallback)
# ------------------------------------------------------------

scrape_diag = {}

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# অ্যালবামের ভেতরের ফটো খোঁজার সময় এই শব্দগুলো থাকলে সেটা সাইটের লোগো/আইকন ধরে বাদ দেওয়া হবে
JUNK_IMAGE_HINTS = (
    "logo", "favicon", "icon", "avatar", "sprite", "banner", "placeholder",
    "loading", "spinner", "emoji",
)


def _get_base_url():
  return os.environ.get("TARGET_SITE_URL", TARGET_SITE_URL).strip().rstrip("/")


def _make_scraper():
  if cf_requests is not None:
    session = cf_requests.Session(impersonate="chrome")
    session.headers.update(BROWSER_HEADERS)
    if SCRAPER_PROXY:
      session.proxies = {"http": SCRAPER_PROXY, "https": SCRAPER_PROXY}
    return session

  scraper = cloudscraper.create_scraper(
      browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
  )
  scraper.headers.update(BROWSER_HEADERS)
  if SCRAPER_PROXY:
    scraper.proxies = {"http": SCRAPER_PROXY, "https": SCRAPER_PROXY}
  return scraper


def _is_cf_blocked(status_code, body):
  if status_code in (401, 403, 429, 503):
    return True
  head = (body or "")[:5000]
  return "Just a moment" in head or "cf-browser-verification" in head or "Attention Required! | Cloudflare" in head


def _fetch_html(scraper, url, timeout=25):
  """প্রথমে সরাসরি (curl_cffi/cloudscraper), ব্লক হলে ফ্রি ওয়েব প্রক্সি ফলব্যাক দিয়ে চেষ্টা করে।"""
  last_status = 0
  last_body = ""

  # ধাপ ১: সরাসরি বা কনফিগার করা প্রক্সি দিয়ে রিকোয়েস্ট
  try:
    res = scraper.get(url, timeout=timeout)
    last_status = res.status_code
    last_body = res.text or ""
    if last_status < 400 and not _is_cf_blocked(last_status, last_body):
      return last_status, last_body
  except Exception as e:
    print(f"[Fetch Direct Error] {url}: {e}")

  # ধাপ ২: curl_cffi আলাদা ব্রাউজার ফিঙ্গারপ্রিন্ট পাল্টে চেষ্টা করবে
  if cf_requests is not None and not SCRAPER_PROXY:
    for browser_ver in ("chrome120", "chrome116", "safari17_0"):
      try:
        res = cf_requests.get(
            url, impersonate=browser_ver, headers=BROWSER_HEADERS, timeout=timeout
        )
        if res.status_code < 400 and not _is_cf_blocked(res.status_code, res.text):
          return res.status_code, res.text or ""
      except Exception:
        pass

  # ধাপ ৩: ফ্রি প্রক্সি গেটওয়ে ফলব্যাক (ডেটাসেন্টার আইপি ব্লক এড়াতে)
  fallback_gateways = [
      f"https://api.allorigins.win/raw?url={quote(url, safe='')}",
      f"https://api.codetabs.com/v1/proxy?quest={quote(url, safe='')}",
  ]
  for gw in fallback_gateways:
    try:
      r = requests.get(gw, timeout=timeout)
      if r.status_code == 200 and r.text and not _is_cf_blocked(200, r.text):
        if "<a " in r.text or "<html" in r.text.lower():
          return 200, r.text
    except Exception:
      continue

  return last_status or 403, last_body


def _text_of(tag):
  return tag.get_text(" ", strip=True) if tag else ""


def _find_caption_and_thumb(link_tag, base_url, slug):
  """লিংকের ভেতরে, না পেলে কয়েক লেভেল উপরের কার্ডে h3 / img খোঁজে।"""
  caption = ""
  thumb = ""
  node = link_tag
  for _ in range(4):
    if node is None:
      break
    if not caption:
      h = node.find(['h3', 'h2', 'h4'])
      caption = _text_of(h)
    if not thumb:
      img = node.find('img')
      if img:
        thumb = (
            img.get('src') or img.get('data-src')
            or img.get('data-lazy-src') or ''
        )
        if not thumb and img.get('srcset'):
          thumb = img.get('srcset').split()[0]
    if caption and thumb:
      break
    node = node.parent
  if not caption:
    caption = link_tag.get('aria-label') or link_tag.get('title') or _text_of(link_tag) or slug
  if thumb:
    thumb = urljoin(base_url + "/", thumb)
  return caption.strip(), thumb


def fetch_target_albums(target_count=ALBUM_SCAN_LIMIT):
  global scrape_diag
  base_url = _get_base_url()
  diag = {
      "base_url": base_url,
      "pages_ok": 0,
      "pages_failed": [],
      "links_found": 0,
      "skipped_posted": 0,
      "skipped_dup": 0,
      "blocked": False,
      "error": "",
  }
  scrape_diag = diag

  if not base_url:
    diag["error"] = "TARGET_SITE_URL is not set (add it in Render → Environment)"
    print("[Scraper] TARGET_SITE_URL is not set!")
    return []

  scraper = _make_scraper()

  # ক্যাটাগরি URL + পেজ নম্বর
  category_urls = []
  for path in TARGET_CATEGORY_PATHS:
    full = base_url if path == "/" else f"{base_url}{path if path.startswith('/') else '/' + path}"
    category_urls.append(full)
    for page in range(2, SCRAPER_PAGES + 1):
      sep = "&" if "?" in full else "?"
      category_urls.append(f"{full}{sep}page={page}")

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
      status_code, body = _fetch_html(scraper, url, timeout=25)
      if status_code >= 400 or _is_cf_blocked(status_code, body):
        diag["pages_failed"].append(f"{url} → HTTP {status_code}")
        if _is_cf_blocked(status_code, body):
          diag["blocked"] = True
        continue

      diag["pages_ok"] += 1
      soup = BeautifulSoup(body, 'html.parser')

      # ধাপ ১: পুরনো কার্ড সিলেক্টর
      link_pairs = []
      for card in soup.select('div.group.relative.bg-zinc-900'):
        link_tag = card.find('a', href=re.compile(r'/watch/'))
        if link_tag:
          link_pairs.append((link_tag, card))

      # ধাপ ২: ফলব্যাক — যেকোনো /watch/ লিংক (সাইটের ক্লাস বদলে গেলেও কাজ করবে)
      if not link_pairs:
        for link_tag in soup.find_all('a', href=re.compile(r'/watch/')):
          link_pairs.append((link_tag, link_tag))

      for link_tag, card in link_pairs:
        href = link_tag.get('href') or ''
        slug = href.split('/watch/')[-1].split('?')[0].split('#')[0].strip('/')
        if not slug:
          continue
        diag["links_found"] += 1
        if slug in seen_slugs:
          diag["skipped_dup"] += 1
          continue
        if slug in posted_slugs:
          diag["skipped_posted"] += 1
          continue

        if card is link_tag:
          caption, thumb_url = _find_caption_and_thumb(link_tag, base_url, slug)
        else:
          h3_tag = card.find('h3')
          caption = h3_tag.get_text(strip=True) if h3_tag else (link_tag.get('aria-label') or slug)
          img_tag = link_tag.find('img') or card.find('img')
          thumb_url = ''
          if img_tag:
            thumb_url = img_tag.get('src') or img_tag.get('data-src') or ''
            if thumb_url:
              thumb_url = urljoin(base_url + "/", thumb_url)

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
      diag["pages_failed"].append(f"{url} → {type(e).__name__}: {e}")
      print(f"[Scraper Error] {url}: {e}")

  print(
      f"[Scraper] albums={len(albums)} links={diag['links_found']} "
      f"posted_skip={diag['skipped_posted']} ok_pages={diag['pages_ok']} "
      f"failed={diag['pages_failed']}"
  )
  return albums


def describe_scrape_diag():
  d = scrape_diag or {}
  if not d:
    return ""
  lines = []
  if d.get("error"):
    lines.append(f"❌ Error Code 503\nReason: {d['error']}")
  else:
    lines.append(f"🌐 Site: {d.get('base_url')}")
    lines.append(f"📄 Pages loaded: {d.get('pages_ok', 0)} | Failed: {len(d.get('pages_failed', []))}")
    lines.append(
        f"🔗 Album links found: {d.get('links_found', 0)} "
        f"(already uploaded: {d.get('skipped_posted', 0)})"
    )
    if d.get("blocked"):
      lines.append("❌ Error Code 403\nReason: Site is blocking the bot (Cloudflare). Set SCRAPER_PROXY.")
    for f in (d.get("pages_failed") or [])[:3]:
      lines.append(f"• {f}")
    if d.get("links_found", 0) == 0 and not d.get("blocked") and d.get("pages_ok", 0) > 0:
      lines.append("❌ Error Code 404\nReason: No /watch/ links found on the page.")
    if d.get("links_found", 0) > 0 and d.get("skipped_posted", 0) >= d.get("links_found", 0):
      lines.append("ℹ️ All albums are already logged as uploaded. Send /resetslugs to clear the log.")
  return "\n".join(lines)


def _extract_urls(text, exts):
  pattern = r'https?://[^\s"\'\\<>]+?\.(?:' + exts + r')(?:\?[^\s"\'\\<>]*)?'
  return re.findall(pattern, text, flags=re.IGNORECASE)


def _is_junk_image(url):
  low = url.lower()
  return any(hint in low for hint in JUNK_IMAGE_HINTS)


def extract_album_media(album_url, fallback_thumb):
  """একটি অ্যালবাম পেজের ভেতরের সবগুলো ভিডিও ও ফটো বের করে (সংখ্যার কোনো সীমা নেই)।"""
  scraper = _make_scraper()
  base_url = _get_base_url()
  videos = []
  photos = []
  try:
    status_code, raw_html = _fetch_html(scraper, album_url, timeout=25)
    # JSON-এস্কেপ করা (https:\/\/...) ও &amp; URL ঠিক করা
    html_text = html_lib.unescape((raw_html or "").replace('\\/', '/').replace('\\u0026', '&'))

    for v in _extract_urls(html_text, "mp4"):
      if v not in videos:
        videos.append(v)

    for p in _extract_urls(html_text, "jpg|jpeg|png|webp"):
      if p not in photos and not _is_junk_image(p):
        photos.append(p)

    # <video>/<source>/og:video ট্যাগ থেকেও খোঁজা
    try:
      soup = BeautifulSoup(raw_html or "", 'html.parser')
      for tag in soup.find_all(['video', 'source']):
        src = tag.get('src')
        if src and '.mp4' in src.lower():
          full = urljoin(album_url, src)
          if full not in videos:
            videos.append(full)
      for meta in soup.find_all('meta', attrs={'property': re.compile(r'^og:video')}):
        c = meta.get('content')
        if c and '.mp4' in c.lower():
          full = urljoin(album_url, c)
          if full not in videos:
            videos.append(full)
    except Exception:
      pass

    # অ্যালবামে ভিডিও/ফটো কিছুই না পেলে তবেই থাম্বনেইল ফলব্যাক হিসেবে নেওয়া হবে
    # পেজে যে ক্রমে আছে সেই ক্রমে সাজানো (ওয়েবসাইটের সাথে হুবহু মেলাতে)
    videos.sort(key=lambda u: html_text.find(u) if html_text.find(u) >= 0 else 10**9)
    photos.sort(key=lambda u: html_text.find(u) if html_text.find(u) >= 0 else 10**9)

    if not photos and not videos and fallback_thumb:
      photos.append(fallback_thumb)
  except Exception as e:
    print(f"[Inner Page Error] {album_url}: {e}")
    if fallback_thumb and not videos and not photos:
      photos.append(fallback_thumb)

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
    for t in ((-1, 1, 0) if is_photo else (-1,)):
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
    is_website_post = WEBSITE_MARKER.lower() in text.lower()
    if is_website_post and not SYNC_WEBSITE_POSTS:
      continue  # শুধু চ্যানেলে থাকবে, ওয়েবসাইটে আসবে না
    if STORY_MARKER.upper() in text.upper():
      story_ids.add(r["id"])
      stories.append(item)
    elif (
        not stripped
        or COLLECTION_INCLUDE_CAPTIONED
        or is_website_post
    ):
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


async def fetch_channel_album_refs():
  """চ্যানেলের কনফার্মেশন মেসেজ থেকে আগে আপলোড হওয়া সব অ্যালবামের আইডি (slug) বের করে।"""
  if not telethon_reader_client or not telethon_channel_entity:
    return None
  refs = set()
  pattern = re.compile(re.escape(ALBUM_REF_KEY) + r":\s*(\S+)")
  async for msg in telethon_reader_client.iter_messages(
      telethon_channel_entity, search=ALBUM_REF_KEY, limit=None
  ):
    for m in pattern.finditer(msg.message or ""):
      refs.add(m.group(1))
  return refs


def _merge_slugs(slugs):
  with db_connect() as conn:
    conn.executemany(
        "INSERT OR IGNORE INTO scraped_slugs (slug) VALUES (?)",
        [(x,) for x in slugs],
    )
    conn.commit()


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
          f"SELECT * FROM (SELECT * FROM channel_posts WHERE {OC_WHERE} ORDER BY message_id DESC LIMIT ?) ORDER BY message_id ASC",
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
    scope = request.args.get("scope", "")
    with db_connect() as conn:
      if scope == "all":
        # সব মিলিয়ে: OC + ওয়েবসাইট-অ্যালবাম (কভার ছবি বাদে)
        videos = conn.execute(
            "SELECT (SELECT COUNT(*) FROM channel_posts WHERE media_type IN ('video', 'animation')) + "
            "(SELECT COUNT(*) FROM website_album_items WHERE media_type = 'video' AND slug NOT LIKE 'cover:%')"
        ).fetchone()[0]
        photos = conn.execute(
            "SELECT (SELECT COUNT(*) FROM channel_posts WHERE media_type NOT IN ('video', 'animation')) + "
            "(SELECT COUNT(*) FROM website_album_items WHERE media_type != 'video' AND slug NOT LIKE 'cover:%')"
        ).fetchone()[0]
      else:
        # শুধু OC কালেকশন (ওয়েবসাইটের মিডিয়া বাদ)
        videos = conn.execute(
            f"SELECT COUNT(*) FROM channel_posts WHERE {OC_WHERE} AND media_type IN ('video', 'animation')"
        ).fetchone()[0]
        photos = conn.execute(
            f"SELECT COUNT(*) FROM channel_posts WHERE {OC_WHERE} AND media_type NOT IN ('video', 'animation')"
        ).fetchone()[0]
    return jsonify({"ok": True, "videos": int(videos or 0), "photos": int(photos or 0)})
  except Exception:
    return jsonify({"ok": False, "videos": 0, "photos": 0}), 500


@app.route("/api/collection/media", methods=["GET"])
def collection_media_api():
  try:
    with db_connect() as conn:
      rows = conn.execute(f"SELECT message_id, media_type FROM channel_posts WHERE {OC_WHERE} ORDER BY message_id DESC LIMIT 500").fetchall()
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


def _clean_title(text):
  return re.sub(re.escape(WEBSITE_MARKER), "", text or "", flags=re.IGNORECASE).strip()


def _item_json(mid, media_type):
  return {
      "id": str(mid),
      "type": "video" if media_type in ("video", "animation") else "photo",
      "thumb": f"/api/channel/thumb/{mid}",
      "media": f"/api/channel/media/{mid}",
  }


def _legacy_albums():
  """আগে সেভ হওয়া ক্যাপশনওয়ালা পোস্টগুলোকে একই ক্যাপশন ধরে অ্যালবামে গ্রুপ করে।"""
  groups = {}
  with db_connect() as conn:
    rows = conn.execute(
        "SELECT message_id, text, media_type FROM channel_posts "
        "WHERE TRIM(COALESCE(text, '')) != '' ORDER BY message_id ASC"
    ).fetchall()
  for r in rows:
    title = _clean_title(r["text"])
    if not title:
      continue
    slug = "legacy-" + hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
    g = groups.setdefault(slug, {"title": title, "items": []})
    g["items"].append((r["message_id"], r["media_type"]))
  return groups


@app.route("/api/albums", methods=["GET"])
def albums_api():
  try:
    albums = []
    with db_connect() as conn:
      rows = conn.execute(
          """
          SELECT a.slug, a.title, COUNT(i.message_id) AS n,
                 SUM(CASE WHEN i.media_type = 'video' THEN 1 ELSE 0 END) AS v,
                 MIN(i.message_id) AS first_id, MAX(i.message_id) AS last_id,
                 a.cover_id AS cover_id, a.batch AS batch, a.site_order AS site_order
          FROM website_albums a JOIN website_album_items i ON i.slug = a.slug
          GROUP BY a.slug
          """
      ).fetchall()
    for r in rows:
      n, v = int(r["n"] or 0), int(r["v"] or 0)
      albums.append({
          "slug": r["slug"],
          "title": _clean_title(r["title"]) or r["slug"],
          "count": n, "videos": v, "photos": n - v,
          "cover": f"/api/channel/thumb/{r['cover_id'] or r['first_id']}",
          "_key": (-int(r["last_id"] or 0),),
      })
    for slug, g in _legacy_albums().items():
      items = g["items"]
      v = sum(1 for _, t in items if t in ("video", "animation"))
      albums.append({
          "slug": slug, "title": g["title"],
          "count": len(items), "videos": v, "photos": len(items) - v,
          "cover": f"/api/channel/thumb/{items[0][0]}",
          "_key": (-max(m for m, _ in items),),
      })
    albums.sort(key=lambda a: a["_key"])
    for a in albums:
      a.pop("_key", None)
    return jsonify({
        "ok": True,
        "albums": albums,
        "syncing": bool(album_sync_state["running"] or album_sync_state["restoring"]),
    })
  except Exception:
    return jsonify({"ok": False, "albums": []}), 500


@app.route("/api/albums/<slug>", methods=["GET"])
def album_items_api(slug):
  try:
    with db_connect() as conn:
      head = conn.execute("SELECT title, cover_id FROM website_albums WHERE slug = ?", (slug,)).fetchone()
      rows = conn.execute(
          "SELECT message_id, media_type FROM website_album_items WHERE slug = ? ORDER BY pos ASC, message_id ASC",
          (slug,),
      ).fetchall()
    if head and rows:
      return jsonify({
          "ok": True,
          "title": _clean_title(head["title"]) or slug,
          "cover": f"/api/channel/thumb/{head['cover_id'] or rows[0]['message_id']}",
          "items": [_item_json(r["message_id"], r["media_type"]) for r in rows],
      })
    g = _legacy_albums().get(slug)
    if g:
      return jsonify({
          "ok": True, "title": g["title"],
          "cover": f"/api/channel/thumb/{g['items'][0][0]}",
          "items": [_item_json(m, t) for m, t in g["items"]],
      })
    return jsonify({"ok": False, "items": []}), 404
  except Exception:
    return jsonify({"ok": False, "items": []}), 500


# ------------------------------------------------------------
# টেলিগ্রাম চ্যানেল → ওয়েবসাইট সিঙ্ক (অ্যাডমিন-প্রোটেক্টেড বাটন)
# ------------------------------------------------------------

album_sync_state = {
    "running": False, "done": False, "checked": 0, "found": 0,
    "added": 0, "error": "",
    # ডিপ্লয়ের পর অ্যালবাম চ্যানেল থেকে ফিরিয়ে আনা পর্যন্ত true থাকে
    "restoring": bool(TELETHON_SESSION_STRING and API_ID and API_HASH),
}
album_sync_lock = threading.Lock()


def _admin_key_status():
  """None = ADMIN_SYNC_KEY সেট করা নেই, True/False = কী মিলেছে কি না।"""
  expected = os.environ.get("ADMIN_SYNC_KEY", "").strip()
  if not expected:
    return None
  given = (request.headers.get("X-Admin-Key") or "").strip()
  return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _save_synced_album(slug, title, cover_id, items):
  """চ্যানেল থেকে পাওয়া একটি সম্পূর্ণ অ্যালবাম ডাটাবেসে সেভ করে। নতুন হলে True।"""
  now = datetime.now(timezone.utc).isoformat()
  with db_connect() as conn:
    existed = conn.execute(
        "SELECT 1 FROM website_albums WHERE slug = ?", (slug,)
    ).fetchone() is not None
    conn.execute(
        "INSERT OR IGNORE INTO website_albums (slug, title, created_at, batch, site_order, cover_id) VALUES (?, ?, ?, 0, 0, ?)",
        (slug, title, now, cover_id),
    )
    if cover_id:
      conn.execute("UPDATE website_albums SET cover_id = ? WHERE slug = ?", (cover_id, slug))
      conn.execute(
          "INSERT OR REPLACE INTO website_album_items (message_id, slug, media_type, mime_type, pos) VALUES (?, ?, 'photo', 'image/jpeg', -1)",
          (cover_id, "cover:" + slug),
      )
    for pos, (mid, mtype, mime) in enumerate(items):
      conn.execute(
          "INSERT OR REPLACE INTO website_album_items (message_id, slug, media_type, mime_type, pos) VALUES (?, ?, ?, ?, ?)",
          (mid, slug, mtype, mime, pos),
      )
    conn.execute("INSERT OR IGNORE INTO scraped_slugs (slug) VALUES (?)", (slug,))
    conn.commit()
  return not existed


async def sync_albums_from_channel():
  """
  চ্যানেল পুরনো → নতুন ক্রমে পড়ে অ্যালবাম বাই অ্যালবাম ওয়েবসাইটে তোলে।
  একটি অ্যালবামের গঠন: [কভার ছবি] + [১০টি করে মিডিয়া গ্রুপ] + [কনফার্মেশন মেসেজ]।
  কনফার্মেশন মেসেজ পেলেই অ্যালবাম সম্পূর্ণ ধরা হয় (অসম্পূর্ণ অ্যালবাম বাদ)।
  """
  st = album_sync_state
  client = telethon_reader_client
  entity = telethon_channel_entity
  if not client or not entity:
    raise RuntimeError("Telethon reader is not connected.")

  ref_re = re.compile(re.escape(ALBUM_REF_KEY) + r":\s*(\S+)")
  group_caps = {}
  pending = []
  pending_title = ""

  async for msg in client.iter_messages(entity, reverse=True, limit=None, wait_time=0.5):
    st["checked"] += 1
    text = msg.message or ""
    info = _classify_media_for_scan(msg)

    if info:
      gid = getattr(msg, "grouped_id", None)
      if gid and text.strip():
        group_caps[gid] = text
      eff = text if text.strip() else (group_caps.get(gid, "") if gid else "")
      if WEBSITE_MARKER.lower() not in eff.lower():
        continue
      title = _clean_title(eff)
      if pending and title != pending_title:
        pending = []  # আগের অসম্পূর্ণ অ্যালবামের অবশিষ্ট বাদ
      pending_title = title
      pending.append({
          "id": msg.id,
          "type": "video" if info[0] in ("video", "animation") else "photo",
          "mime": info[1],
          "group": gid,
      })
      continue

    # New English text + old Bengali text (so previously uploaded albums still restore)
    is_confirm = (
        WEBSITE_MARKER.lower() in text.lower()
        and (
            ALBUM_REF_KEY in text
            or "Album upload complete" in text
            or "অ্যালবাম আপলোড সম্পূর্ণ" in text
        )
    )
    if not is_confirm:
      continue

    if pending:
      ref = ref_re.search(text)
      slug = ref.group(1) if ref else f"tg-{msg.id}"
      title = pending_title
      if not title:
        m_title = re.search(r"📁\s*(.+)", text)
        title = m_title.group(1).strip() if m_title else slug

      cover_id = None
      items_src = pending
      # কভার = সবার আগের একক (গ্রুপ-ছাড়া) ছবি
      if len(pending) >= 2 and pending[0]["group"] is None and pending[0]["type"] == "photo":
        cover_id = pending[0]["id"]
        items_src = pending[1:]
      items = [(x["id"], x["type"], x["mime"]) for x in items_src]

      is_new = await asyncio.to_thread(_save_synced_album, slug, title, cover_id, items)
      st["found"] += 1
      if is_new:
        st["added"] += 1

    pending = []
    pending_title = ""


async def _album_sync_job():
  st = album_sync_state
  try:
    for attempt in range(3):
      try:
        st["error"] = ""
        await sync_albums_from_channel()
        break
      except Exception as e:
        print("Album sync error:", traceback.format_exc())
        st["error"] = str(e) or type(e).__name__
        if attempt < 2:
          await asyncio.sleep(20)  # নেটওয়ার্ক/FloodWait সমস্যায় আবার চেষ্টা
  finally:
    st["running"] = False
    st["done"] = True
    st["restoring"] = False


def _start_album_sync():
  """সিঙ্ক শুরু করে। আগে থেকে চললে False ফেরত দেয়।"""
  with album_sync_lock:
    if album_sync_state["running"]:
      return False
    album_sync_state.update(
        running=True, done=False, checked=0, found=0, added=0, error=""
    )
  asyncio.run_coroutine_threadsafe(_album_sync_job(), telethon_loop)
  return True


@app.route("/api/albums/sync", methods=["POST"])
def album_sync_start():
  ok = _admin_key_status()
  if ok is None:
    return jsonify({"ok": False, "code": 503, "error": "ADMIN_SYNC_KEY is not set on the server"}), 503
  if not ok:
    return jsonify({"ok": False, "code": 401, "error": "Invalid admin key"}), 401
  if not telethon_reader_client or not telethon_channel_entity:
    return jsonify({"ok": False, "code": 503, "error": "Telethon not connected, try again shortly"}), 503

  _start_album_sync()
  return jsonify({"ok": True, "running": True})


@app.route("/api/albums/sync/status", methods=["GET"])
def album_sync_status():
  return jsonify(dict(album_sync_state))


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
    thumb_cache = CACHE_DIR / f"pthumb_hd_{message_id}.jpg"
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


def _clock():
  """বাংলাদেশ সময় (GMT+6) — প্রোগ্রেস মেসেজে শেষ আপডেটের সময় দেখাতে।"""
  return datetime.fromtimestamp(time.time() + 6 * 3600, timezone.utc).strftime("%H:%M:%S")


# ------------------------------------------------------------
# Loading bar + Render-log style error helpers
# ------------------------------------------------------------


def make_bar(pct, length=10):
  """[■■■■□□□□□□] style loading bar. pct = 0..100"""
  pct = max(0, min(100, int(pct)))
  filled = round(length * pct / 100)
  return "[" + "■" * filled + "□" * (length - filled) + "]"


def error_info(exc):
  """Exception -> (HTTP-like error code, short reason)."""
  name = type(exc).__name__
  msg = (str(exc) or name).replace("\n", " ").strip()
  low = msg.lower()
  m = re.search(r"\b([45]\d\d)\b", msg)

  if isinstance(exc, ScanBusyError):
    code = 409
  elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in low:
    code = 504
  elif isinstance(exc, TelegramNotConfiguredError):
    code = 503
  elif m:
    code = int(m.group(1))
  elif "flood" in low or "flood" in name.lower():
    code = 429
  elif "peer id invalid" in low or "not found" in low or "chat_id_invalid" in low:
    code = 404
  elif "forbidden" in low or "unauthorized" in low or "not_enough_rights" in low:
    code = 403
  elif "too large" in low:
    code = 413
  else:
    code = 500
  return code, msg[:120]


def render_error(code_or_exc, reason=None):
  """Short Render-log style error: code + reason only."""
  if isinstance(code_or_exc, BaseException):
    code, reason = error_info(code_or_exc)
  else:
    code, reason = int(code_or_exc), (reason or "Unknown error")
  return f"❌ Error Code {code}\nReason: {reason}"


async def _get_target_chat(chat_id):
  """
  সার্ভার রিস্টার্টের পর বটের পিয়ার-ক্যাশ খালি থাকে, তখন get_chat 'Peer id invalid' দেয়।
  সেক্ষেত্রে Telethon-এর কাছে থাকা চ্যানেলের access_hash বটের স্টোরেজে বসিয়ে আবার চেষ্টা করে।
  """
  try:
    return await pyrogram_client.get_chat(chat_id)
  except Exception as first_err:
    ent = telethon_channel_entity
    access_hash = getattr(ent, "access_hash", None) if ent is not None else None
    if access_hash is None or int(chat_id) not in (int(STORAGE_CHANNEL_ID), int(STORY_BACKUP_CHANNEL_ID)):
      raise first_err
    for peer in (
        (int(chat_id), int(access_hash), "channel", None, None),
        (int(chat_id), int(access_hash), "channel", [], None),
    ):
      try:
        await pyrogram_client.storage.update_peers([peer])
        return await pyrogram_client.get_chat(chat_id)
      except Exception:
        continue
    raise first_err


def build_main_keyboard():
  return InlineKeyboardMarkup([
      [InlineKeyboardButton("📡 Scanning Website Videos", callback_data="scan_media_channel")],
      [InlineKeyboardButton("📤 Upload Media", callback_data="start_upload")],
      [InlineKeyboardButton("➕ Add Story", callback_data="add_story")],
  ])


# ------------------------------------------------------------
# Animated loading bar: white boxes turn black, then black turn white again
#   [□□□□□□□□□□] → [■■■■■■■■■■] → [□□□□□□□□□□] → ... (loops until the task ends)
# ------------------------------------------------------------

LOADING_BAR_LEN = 10
LOADING_FRAME_DELAY = 0.8  # seconds between frames (lower = faster; too low can hit Telegram FloodWait)
LOADING_STEP = 2            # boxes changed per frame (higher = faster looking animation)
_animators = {}


def _msg_key(message):
  return (getattr(getattr(message, "chat", None), "id", 0), getattr(message, "id", id(message)))


class _LoadingAnimator:
  def __init__(self, message):
    self.message = message
    self.title = ""
    self.detail = ""
    self.pct = 0
    self.tick = 0
    self.delay = LOADING_FRAME_DELAY
    self.task = asyncio.ensure_future(self._run())

  def _frame(self):
    n = LOADING_BAR_LEN
    cycle = n * 2
    pos = (self.tick * LOADING_STEP) % cycle
    filled = pos if pos <= n else cycle - pos  # fills up, then empties
    return "[" + "■" * filled + "□" * (n - filled) + "]"

  def _text(self):
    lines = [self.title, f"{self._frame()} {int(self.pct)}%"]
    if self.detail:
      lines.append(self.detail)
    lines.append(f"🕒 {_clock()}")
    return "\n".join(lines)

  async def _run(self):
    try:
      while True:
        try:
          await self.message.edit_text(self._text())
        except asyncio.CancelledError:
          raise
        except Exception as e:
          # FloodWait: automatically slow down a little, keep animating
          if "flood" in str(e).lower() or "wait" in str(e).lower():
            self.delay = min(self.delay + 0.4, 3.0)
            await asyncio.sleep(3)
        self.tick += 1
        await asyncio.sleep(self.delay)
    except asyncio.CancelledError:
      pass

  async def stop(self):
    self.task.cancel()
    try:
      await self.task
    except BaseException:
      pass


async def _stop_loading(message):
  anim = _animators.pop(_msg_key(message), None)
  if anim:
    await anim.stop()


async def _loading(message, title, pct, detail=""):
  """Start/update the animated loading bar on this message."""
  key = _msg_key(message)
  anim = _animators.get(key)
  if anim is None:
    anim = _LoadingAnimator(message)
    _animators[key] = anim
  anim.title, anim.pct, anim.detail = title, pct, detail


async def _safe_edit(message, text, reply_markup=None):
  await _stop_loading(message)  # final/static text: stop the animation first
  try:
    await message.edit_text(text, reply_markup=reply_markup)
  except Exception:
    pass


def _download_bytes(url, name, max_mb=50):
  scraper = _make_scraper()
  base_url = _get_base_url()
  headers = {'Referer': f"{base_url}/"} if base_url else {}
  res = scraper.get(
      url, timeout=60, stream=True,
      headers=headers,
  )
  res.raise_for_status()
  buf = BytesIO()
  if hasattr(res, "iter_content"):
    for part in res.iter_content(256 * 1024):
      if not part:
        continue
      buf.write(part)
      if buf.tell() > max_mb * 1024 * 1024:
        raise ValueError("file too large")
  else:
    content = res.content
    if len(content) > max_mb * 1024 * 1024:
      raise ValueError("file too large")
    buf.write(content)
  buf.seek(0)
  buf.name = name
  return buf


# ------------------------------------------------------------
# অ্যালবাম আপলোড: ১০টি করে মিডিয়া গ্রুপ → সব শেষে কনফার্মেশন মেসেজ
# ------------------------------------------------------------


def build_caption(title):
  """ওয়েবসাইটের ক্যাপশনের নিচে #website ট্যাগ যোগ করে (টেলিগ্রাম ক্যাপশন লিমিট 1024)।"""
  title = (title or "").strip()
  tag = WEBSITE_MARKER
  if not title:
    return tag
  max_title = 1024 - len(tag) - 2
  if len(title) > max_title:
    title = title[:max_title - 1] + "…"
  return f"{title}\n{tag}"


def _build_input_media(items, caption):
  """items = [(kind, source)] → Pyrogram InputMedia তালিকা (ক্যাপশন শুধু প্রথমটিতে)।"""
  media = []
  for idx, (kind, src) in enumerate(items):
    cap = caption if idx == 0 else ""
    if kind == "video":
      media.append(InputMediaVideo(src, caption=cap, supports_streaming=True))
    else:
      media.append(InputMediaPhoto(src, caption=cap))
  return media


async def _send_items(chat_id, items, caption):
  """১টি হলে সিঙ্গেল পোস্ট, ২-১০টি হলে মিডিয়া গ্রুপ (অ্যালবাম) হিসেবে পাঠায়।"""
  if len(items) == 1:
    kind, src = items[0]
    if kind == "video":
      msg = await pyrogram_client.send_video(
          chat_id, video=src, caption=caption, supports_streaming=True
      )
    else:
      msg = await pyrogram_client.send_photo(chat_id, photo=src, caption=caption)
    return [msg]
  return await pyrogram_client.send_media_group(
      chat_id, media=_build_input_media(items, caption)
  )


def _download_to_file(url, path, max_mb=50):
  """ফাইল ডিস্কে নামায় (RAM-এ না), যাতে ১০টি বড় ভিডিওতে মেমরি শেষ হয়ে সার্ভার রিস্টার্ট না হয়।"""
  scraper = _make_scraper()
  base_url = _get_base_url()
  headers = {'Referer': f"{base_url}/"} if base_url else {}
  res = scraper.get(url, timeout=(15, 600), stream=True, headers=headers)
  res.raise_for_status()
  limit = max_mb * 1024 * 1024
  total = 0
  with open(path, "wb") as f:
    if hasattr(res, "iter_content"):
      for part in res.iter_content(256 * 1024):
        if not part:
          continue
        total += len(part)
        if total > limit:
          raise ValueError("file too large")
        f.write(part)
    else:
      content = res.content
      if len(content) > limit:
        raise ValueError("file too large")
      f.write(content)
  return path


async def _send_chunk_safe(chat_id, chunk, caption):
  """
  chunk = সর্বোচ্চ ১০টি (kind, url)।
  ধাপ ১: (শুধু ফটোর গ্রুপে) URL দিয়ে সরাসরি অ্যালবাম পোস্ট — ১২০ সেকেন্ডের টাইমআউটসহ।
  ধাপ ২: ফাইলগুলো ডিস্কে নামিয়ে (একসাথে ৩টি) অ্যালবাম পোস্ট।
  ধাপ ৩: তাতেও ফেইল করলে একটা একটা করে পোস্ট।
  যতগুলো মেসেজ সফলভাবে পাঠানো হলো তার তালিকা ফেরত দেয়।
  """
  import shutil
  import tempfile

  has_video = any(k == "video" for k, _ in chunk)

  # ধাপ ১: ভিডিও থাকলে এটা বাদ, কারণ টেলিগ্রাম বড় ভিডিও URL থেকে ধীরে আনে বা আটকে থাকে
  if not has_video or TRY_URL_FIRST:
    try:
      return await asyncio.wait_for(_send_items(chat_id, chunk, caption), timeout=120)
    except Exception as e:
      print(f"[Upload] URL group failed ({len(chunk)} items): {type(e).__name__}: {e}")

  tmpdir = tempfile.mkdtemp(prefix="album_")
  try:
    sem = asyncio.Semaphore(3)

    async def fetch(n, kind, url):
      ext = ".mp4" if kind == "video" else ".jpg"
      path = os.path.join(tmpdir, f"{n:03d}{ext}")
      async with sem:
        try:
          await asyncio.to_thread(
              _download_to_file, url, path,
              VIDEO_MAX_MB if kind == "video" else PHOTO_MAX_MB,
          )
          return (kind, path)
        except Exception as e:
          print(f"[Upload] download failed {url}: {e}")
          return None

    results = await asyncio.gather(*(fetch(n, k, u) for n, (k, u) in enumerate(chunk)))
    downloaded = [r for r in results if r]
    if not downloaded:
      return []

    try:
      return await _send_items(chat_id, downloaded, caption)
    except Exception as e:
      print(f"[Upload] downloaded group failed: {type(e).__name__}: {e}")

    # ধাপ ৩: একটা একটা করে
    sent = []
    for kind, path in downloaded:
      try:
        msgs = await _send_items(chat_id, [(kind, path)], caption if not sent else "")
        sent.extend(msgs)
      except Exception as e:
        print(f"[Upload] single send failed: {e}")
      await asyncio.sleep(2)
    return sent
  finally:
    shutil.rmtree(tmpdir, ignore_errors=True)


def _save_sent_messages(sent_msgs, fallback_caption):
  """সফলভাবে চ্যানেলে যাওয়া মেসেজগুলো channel_posts টেবিলে সেভ করে।"""
  with db_connect() as conn:
    for s_msg in sent_msgs:
      text_content = s_msg.caption or s_msg.text or fallback_caption
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
    conn.commit()


def _save_album_items(slug, title, sent_msgs, start_pos=0, batch=0, site_order=0):
  """চ্যানেলে যাওয়া মেসেজগুলো অ্যালবামের আইটেম হিসেবে সেভ করে (OC কালেকশনের সাথে মেশে না)।"""
  now = datetime.now(timezone.utc).isoformat()
  with db_connect() as conn:
    conn.execute(
        "INSERT OR IGNORE INTO website_albums (slug, title, created_at, batch, site_order) VALUES (?, ?, ?, ?, ?)",
        (slug, (title or slug).strip(), now, int(batch), int(site_order)),
    )
    for i, m in enumerate(sent_msgs):
      is_video = bool(getattr(m, "video", None))
      mime = "video/mp4" if is_video else "image/jpeg"
      if is_video and getattr(m.video, "mime_type", None):
        mime = m.video.mime_type
      conn.execute(
          "INSERT OR REPLACE INTO website_album_items (message_id, slug, media_type, mime_type, pos) VALUES (?, ?, ?, ?, ?)",
          (m.id, slug, "video" if is_video else "photo", mime, start_pos + i),
      )
    conn.commit()


def _save_cover(slug, title, msg, batch=0, site_order=0):
  """অ্যালবামের কভার ছবি সেভ করে। এটা অ্যালবামের আইটেম গোনায় ধরা হয় না ("cover:" স্লাগ)।"""
  now = datetime.now(timezone.utc).isoformat()
  with db_connect() as conn:
    conn.execute(
        "INSERT OR IGNORE INTO website_albums (slug, title, created_at, batch, site_order) VALUES (?, ?, ?, ?, ?)",
        (slug, (title or slug).strip(), now, int(batch), int(site_order)),
    )
    conn.execute(
        "INSERT OR REPLACE INTO website_album_items (message_id, slug, media_type, mime_type, pos) VALUES (?, ?, ?, ?, ?)",
        (msg.id, "cover:" + slug, "photo", "image/jpeg", -1),
    )
    conn.execute("UPDATE website_albums SET cover_id = ? WHERE slug = ?", (msg.id, slug))
    conn.commit()


def _mark_slug_done(slug):
  with db_connect() as conn:
    conn.execute("INSERT OR IGNORE INTO scraped_slugs (slug) VALUES (?)", (slug,))
    conn.commit()


async def _upload_album_to_channel(chat_id, album, videos, photos, progress=None):
  """
  একটি ওয়েবসাইট-অ্যালবামের সব ভিডিও/ফটো ১০টি করে গ্রুপে চ্যানেলে আপলোড করে।
  সব শেষ হলে চ্যানেলে একটি কনফার্মেশন মেসেজ পাঠায়।
  ফেরত দেয়: (sent_count, total_count)
  """
  items = [("video", u) for u in videos] + [("photo", p) for p in photos]
  total = len(items)
  caption = build_caption(album['caption'])
  chunks = [items[i:i + ALBUM_CHUNK_SIZE] for i in range(0, total, ALBUM_CHUNK_SIZE)]

  batch = int(album.get('batch') or 0)
  site_order = int(album.get('site_order') or 0)

  # অ্যালবামের শুরুতে কভার ছবি (ওয়েবসাইটের অ্যালবাম থাম্বনেইল) চ্যানেলে পোস্ট হবে
  cover_src = album.get('thumbnail')
  if cover_src:
    try:
      cover_msgs = await asyncio.wait_for(
          _send_chunk_safe(chat_id, [("photo", cover_src)], caption), timeout=300
      )
      if cover_msgs:
        await asyncio.to_thread(_save_cover, album['slug'], album['caption'], cover_msgs[0], batch, site_order)
      await asyncio.sleep(2)
    except Exception as e:
      print(f"[Upload] cover failed: {e}")

  all_sent = []
  for ci, chunk in enumerate(chunks, 1):
    if progress:
      await progress(ci, len(chunks), len(all_sent), total)

    try:
      sent = await asyncio.wait_for(_send_chunk_safe(chat_id, chunk, caption), timeout=CHUNK_TIMEOUT)
    except asyncio.TimeoutError:
      print(f"[Upload] group {ci}/{len(chunks)} timed out for {album['slug']}")
      sent = []
    if sent:
      all_sent.extend(sent)
      await asyncio.to_thread(
          _save_album_items, album['slug'], album['caption'], sent,
          len(all_sent) - len(sent), batch, site_order,
      )
      if SYNC_WEBSITE_POSTS:
        await asyncio.to_thread(_save_sent_messages, sent, caption)
    await asyncio.sleep(3)  # টেলিগ্রাম FloodWait এড়াতে

  sent_videos = sum(1 for m in all_sent if getattr(m, "video", None))
  sent_photos = len(all_sent) - sent_videos
  failed = total - len(all_sent)

  if all_sent:
    await asyncio.to_thread(_mark_slug_done, album['slug'])

    # সব আপলোড শেষ → চ্যানেলে কনফার্মেশন মেসেজ
    status_line = "✅ Album upload complete" if failed == 0 else "⚠️ Album upload complete (some files skipped)"
    confirm = (
        f"{status_line}\n"
        f"📁 {album['caption'][:200]}\n"
        f"🎞 Videos: {sent_videos} | 🖼 Photos: {sent_photos} | Total: {len(all_sent)}/{total}\n"
        f"🔖 {ALBUM_REF_KEY}: {album['slug']}\n"
        f"{WEBSITE_MARKER}"
    )
    # এই মেসেজটাই "অ্যালবাম চ্যানেলে আছে" তার প্রমাণ, তাই না গেলে কয়েকবার চেষ্টা
    for _try in range(3):
      try:
        await pyrogram_client.send_message(chat_id, confirm)
        break
      except Exception as e:
        print(f"[Upload] confirmation message failed (try {_try + 1}): {e}")
        await asyncio.sleep(3)

  return len(all_sent), total


async def _handle_scan_callback(callback_query, mode="collection"):
  if callback_query.from_user.id != ADMIN_ID:
    await callback_query.answer("Error Code 403: Unauthorized", show_alert=True)
    return

  if scan_lock.locked():
    await callback_query.answer("Error Code 409: A scan is already running", show_alert=True)
    return

  if not scrape_lock.acquire(blocking=False):
    await callback_query.answer("Error Code 409: Scraping is already running", show_alert=True)
    return

  try:
    message = callback_query.message
    await callback_query.answer("Starting scan...")
    await _loading(message, "🔄 Starting scan", 0, f"Looking for albums (max {ALBUM_SCAN_LIMIT})")

    try:
      # ধাপ ০: চ্যানেলে যে অ্যালবামগুলো আগেই আপলোড করা আছে সেগুলো খুঁজে বাদ দেওয়া হবে
      channel_check_note = ""
      if telethon_reader_client:
        await _loading(message, "🔎 Checking channel", 5, "Looking for previously uploaded albums")
        try:
          fut = asyncio.run_coroutine_threadsafe(fetch_channel_album_refs(), telethon_loop)
          refs = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=300)
          if refs:
            await asyncio.to_thread(_merge_slugs, refs)
        except Exception as e:
          print("Channel album check error:", e)
          code, reason = error_info(e)
          channel_check_note = f"\n\n⚠️ Error Code {code}\nReason: Channel check failed ({reason})"
      else:
        channel_check_note = "\n\n⚠️ Error Code 503\nReason: Telethon not connected, channel check skipped"

      await _loading(message, "🌐 Fetching website", 10, "Reading album list")
      albums = await asyncio.to_thread(fetch_target_albums, ALBUM_SCAN_LIMIT)
      if not albums:
        report = describe_scrape_diag()
        text = "ℹ️ No new albums found." + channel_check_note
        if report:
          text += "\n\n" + report
        await _safe_edit(message, text, reply_markup=build_main_keyboard())
        return

      # সাইটের উপরের অ্যালবাম সবশেষে আপলোড হবে, ফলে আপনার সাইটে ওয়েবসাইটের ক্রম ঠিক থেকে সবচেয়ে নতুন সবার উপরে থাকবে
      albums = list(reversed(albums))
      added_count = 0
      failed_count = 0
      total_files = 0
      last_err = None
      target_chat = await _get_target_chat(STORAGE_CHANNEL_ID)
      album_total = len(albums)

      def overall(a_idx, frac=0.0):
        # 15% .. 100% spread across all albums
        return 15 + 85 * ((a_idx - 1) + frac) / album_total

      batch_ts = int(time.time())
      for a_idx, album in enumerate(albums, 1):
        album['batch'] = batch_ts       # একই স্ক্যানের অ্যালবাম একসাথে থাকবে
        album['site_order'] = a_idx     # ওয়েবসাইটে যে ক্রমে ছিল, সেই ক্রম
        title_short = (album['caption'] or album['slug'])[:40]
        await _loading(
            message, f"🔍 Album {a_idx}/{album_total}", overall(a_idx, 0),
            f"📁 {title_short}\nScanning photos/videos...",
        )

        videos, photos = await asyncio.to_thread(
            extract_album_media, album['url'], album['thumbnail']
        )
        if not videos and not photos:
          failed_count += 1
          last_err = (404, "No media found in album")
          continue

        async def progress(ci, cn, done, total, _i=a_idx, _t=title_short):
          await _loading(
              message, f"📤 Album {_i}/{album_total}", overall(_i, (ci - 1) / max(1, cn)),
              f"📁 {_t}\nGroup {ci}/{cn} | {done}/{total} files",
          )

        try:
          sent_count, total_count = await _upload_album_to_channel(
              target_chat.id, album, videos, photos, progress
          )
        except Exception as e:
          print("Album upload error:", traceback.format_exc())
          last_err = error_info(e)
          sent_count, total_count = 0, len(videos) + len(photos)

        if sent_count == 0:
          failed_count += 1
          if not last_err:
            last_err = (502, "Telegram upload failed")
        else:
          added_count += 1
          total_files += sent_count

        await asyncio.sleep(2)

      if SYNC_WEBSITE_POSTS and telethon_reader_client:
        await _loading(message, "🔄 Syncing website", 98, "Scanning channel")
        try:
          fut = asyncio.run_coroutine_threadsafe(scan_channel("all"), telethon_loop)
          await asyncio.wrap_future(fut)
        except Exception as e:
          print("Post-upload scan error:", e)

      done_text = (
          f"{make_bar(100)} 100%\n"
          f"✅ {added_count} new album(s) uploaded to Telegram channel ({total_files} files)"
      )
      if SYNC_WEBSITE_POSTS:
        done_text += "\n🌐 Website synced."
      done_text += channel_check_note
      if failed_count:
        code, reason = last_err or (500, "Unknown error")
        done_text += f"\n\n❌ Error Code {code}\nReason: {reason} ({failed_count} album(s) failed)"
      await _safe_edit(message, done_text, reply_markup=build_main_keyboard())
    except Exception as e:
      print("Scan error:", traceback.format_exc())
      await _safe_edit(message, render_error(e), reply_markup=build_main_keyboard())
  finally:
    scrape_lock.release()


def _classify_pyro_media(m):
  if getattr(m, "photo", None):
    return "photo", "image/jpeg"
  if getattr(m, "video", None):
    return "video", m.video.mime_type or "video/mp4"
  if getattr(m, "animation", None):
    return "animation", m.animation.mime_type or "video/mp4"
  if getattr(m, "document", None):
    mime = (m.document.mime_type or "").lower()
    if mime.startswith("video/"):
      return "video", mime
    if mime.startswith("image/"):
      return "photo", mime
  return None


def _save_uploaded_media(msg_id, kind, media_type, mime, caption):
  now = datetime.now(timezone.utc)
  with db_connect() as conn:
    if kind == "story":
      conn.execute(
          "INSERT OR REPLACE INTO stories (message_id, media_type, mime_type, telegram_file_id, thumb_file_id, ts, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
          (msg_id, "photo" if media_type == "photo" else "video", mime, None, None, int(now.timestamp()), now.isoformat()),
      )
    else:
      conn.execute(
          "INSERT OR REPLACE INTO channel_posts (message_id, text, media_type, mime_type, telegram_file_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
          (msg_id, caption, media_type, mime, None, now.isoformat()),
      )
    conn.commit()


def build_done_keyboard():
  return InlineKeyboardMarkup([
      [InlineKeyboardButton("✅ Done", callback_data="upload_done")],
  ])


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
          "👋 Welcome Admin!\n\nTap a button below to scan website albums, upload media, or add a story:",
          reply_markup=build_main_keyboard(),
      )

    @client.on_message(filters.command("resetslugs") & filters.private)
    async def reset_slugs_cmd(client, message):
      # আপলোড-করা-অ্যালবাম লগ মুছে ফেলে, যাতে সব অ্যালবাম আবার স্ক্র্যাপ হয়
      if not message.from_user or message.from_user.id != ADMIN_ID:
        return
      status = await message.reply(f"♻️ Clearing log...\n{make_bar(0)} 0%")
      try:
        def _clear():
          with db_connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM scraped_slugs").fetchone()[0]
            conn.execute("DELETE FROM scraped_slugs")
            conn.commit()
          return n
        n = await asyncio.to_thread(_clear)
        await _safe_edit(
            status,
            f"{make_bar(100)} 100%\n♻️ {n} log entries cleared. You can scan again now.",
            reply_markup=build_main_keyboard(),
        )
      except Exception as e:
        await _safe_edit(status, render_error(e))

    @client.on_callback_query(filters.regex("^scan_media_channel$"))
    async def scan_media_channel_callback(client, callback_query):
      await _handle_scan_callback(callback_query, "collection")

    @client.on_callback_query(filters.regex("^(start_upload|add_story)$"))
    async def start_upload_callback(client, callback_query):
      if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Error Code 403: Unauthorized", show_alert=True)
        return
      mode = "story" if callback_query.data == "add_story" else "upload"
      with upload_lock:
        pending_uploads[ADMIN_ID] = mode
      await callback_query.answer("Ready!")
      label = "Story" if mode == "story" else "Media"
      await _safe_edit(
          callback_query.message,
          f"📤 {label} mode on.\n\nSend photos/videos now (you can send many). Press ✅ Done when finished, or send /cancel to stop.",
          reply_markup=build_done_keyboard(),
      )

    @client.on_callback_query(filters.regex("^upload_done$"))
    async def upload_done_callback(client, callback_query):
      if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Error Code 403: Unauthorized", show_alert=True)
        return
      with upload_lock:
        pending_uploads.pop(ADMIN_ID, None)
      await callback_query.answer("Syncing website...")
      message = callback_query.message
      await _loading(message, "🔄 Syncing website", 10, "Starting channel scan")
      if telethon_reader_client:
        try:
          fut = asyncio.run_coroutine_threadsafe(scan_channel("all"), telethon_loop)
          await asyncio.wrap_future(fut)
        except Exception as e:
          print("Upload-done scan error:", e)
          await _safe_edit(message, render_error(e), reply_markup=build_main_keyboard())
          return
      else:
        await _safe_edit(
            message, render_error(503, "Telethon not connected"),
            reply_markup=build_main_keyboard(),
        )
        return
      await _safe_edit(
          message,
          f"{make_bar(100)} 100%\n✅ Uploaded & website synced!",
          reply_markup=build_main_keyboard(),
      )

    @client.on_message(filters.command("cancel") & filters.private)
    async def cancel_cmd(client, message):
      if not message.from_user or message.from_user.id != ADMIN_ID:
        return
      with upload_lock:
        pending_uploads.pop(ADMIN_ID, None)
      await message.reply("❌ Cancelled.", reply_markup=build_main_keyboard())

    @client.on_message(
        filters.private
        & (filters.photo | filters.video | filters.animation | filters.document)
    )
    async def upload_media_handler(client, message):
      if not message.from_user or message.from_user.id != ADMIN_ID:
        return
      mode = pending_uploads.get(ADMIN_ID)
      if not mode:
        return
      info = _classify_pyro_media(message)
      if not info:
        await message.reply(render_error(415, "Only photo or video files are supported"))
        return
      media_type, mime = info
      target_id = STORY_BACKUP_CHANNEL_ID if mode == "story" else STORAGE_CHANNEL_ID
      caption = STORY_MARKER if mode == "story" else ""
      status = await message.reply("📤 Uploading...")
      await _loading(status, "📤 Uploading", 30, "Connecting to channel")
      try:
        target_chat = await _get_target_chat(target_id)
        await _loading(status, "📤 Uploading", 60, "Sending file")
        copied = await message.copy(target_chat.id, caption=caption)
        await asyncio.to_thread(
            _save_uploaded_media, copied.id, mode, media_type, mime, caption
        )
        await _safe_edit(
            status,
            f"{make_bar(100)} 100%\n✅ Added. Send more or press Done.",
            reply_markup=build_done_keyboard(),
        )
      except Exception as e:
        print("Upload error:", traceback.format_exc())
        await _safe_edit(status, render_error(e), reply_markup=build_done_keyboard())

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
        # ডিপ্লয়/রিস্টার্টে ডাটাবেস মুছে গেলেও অ্যালবাম চ্যানেল থেকে স্বয়ংক্রিয়ভাবে ফিরে আসবে
        try:
          _start_album_sync()
        except Exception:
          album_sync_state["restoring"] = False

  if not album_sync_state["running"]:
    album_sync_state["restoring"] = False


def _keepalive_loop():
  """Render ফ্রি সার্ভিস ১৫ মিনিট কোনো HTTP রিকোয়েস্ট না পেলে ঘুমিয়ে পড়ে (স্ক্যান মাঝপথে থেমে যায়)।
  তাই প্রতি ৪ মিনিটে নিজের /health-এ রিকোয়েস্ট পাঠিয়ে জাগিয়ে রাখা হয়।"""
  if os.environ.get("KEEPALIVE", "1").strip() == "0":
    return
  url = (os.environ.get("KEEPALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
  if not url:
    print("[KeepAlive] RENDER_EXTERNAL_URL/KEEPALIVE_URL is not set, disabled")
    return
  print(f"[KeepAlive] pinging {url}/health every 4 min")
  while True:
    time.sleep(240)
    try:
      requests.get(url + "/health", timeout=20)
    except Exception:
      pass


def start_background_services():
  global _services_started
  with _services_lock:
    if _services_started:
      return
    _services_started = Tr
