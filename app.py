"""
Telegram-backed media server with Bot & Telethon Sync
- মিডিয়া ফাইল সার্ভারে সেভ হয় না; শুধু মেটাডাটা DB-তে থাকে, ভিডিও/ফটো সরাসরি Telegram থেকে স্ট্রিম হয়।
- Pyrogram Bot দিয়ে বাটন: Upload Media, Add Story, Scan Channel Media এবং Back বাটন।
- /scan কমান্ড অথবা বটের বাটনের মাধ্যমে টেলিগ্রাম চ্যানেল থেকে অটোমেটিক পোস্ট ও স্টোরি স্ক্যান ও সিঙ্ক হবে।
"""
import os, re, time, math, asyncio, logging, threading, hashlib, hmac, sqlite3, traceback, mimetypes, random, uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
from typing import Optional, Tuple

# Python 3.12+ Event Loop Fix
try:
    asyncio.get_event_loop()
except RuntimeError:
    _main_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_main_loop)

import requests
from flask import Flask, jsonify, request, Response, abort, send_file, redirect, url_for, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

from telethon import TelegramClient, utils
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeAnimated

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

# ───────────────────────── Config (Environment Variables) ─────────────────────────
def need(name):
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Environment variable {name} সেট করা নেই")
    return v

API_ID = int(need("API_ID"))
API_HASH = need("API_HASH")
SESSION_STRING = need("SESSION_STRING")      # User account session (Pyrogram/Telethon string)
BOT_TOKEN = need("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6034439625") or "6034439625")

_ch = need("CHANNEL_ID")                     # যেমন -1004322205565 অথবা @username
CHANNEL = int(_ch) if re.fullmatch(r"-?\d+", _ch) else _ch.lstrip("@")
STORAGE_CHANNEL_ID = CHANNEL
STORY_BACKUP_CHANNEL_ID = CHANNEL

ADMIN_KEYS = {int(x) for x in os.environ.get("ADMIN_IDS", f"{ADMIN_ID}").replace(" ", "").split(",") if x}
STORY_MARKER = "#story"
DEFAULT_STORY_THUMB = "https://carder.top/imagens/1792065174675-662575987.jpg"

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///media.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("sqlite"):
    log.warning("SQLite ব্যবহার হচ্ছে — ডিপ্লয়ে ডাটা মুছে যেতে পারে! DATABASE_URL এ Postgres দিন।")

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
CACHE_DIR = Path(os.environ.get("TELETHON_CACHE_DIR", str(BASE_DIR / "static" / "telethon_cache")))
MEDIA_DIR = Path(os.environ.get("CHAT_MEDIA_DIR", str(BASE_DIR / "static" / "chat_media")))
BG_DIR = Path(os.environ.get("BACKGROUND_DIR", str(BASE_DIR / "static" / "backgrounds")))

CACHE_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
BG_DIR.mkdir(parents=True, exist_ok=True)

CHUNK = 1024 * 1024  # 1 MiB

# ───────────────────────── Database Setup ─────────────────────────
DB_PATH = Path(os.environ.get("CHAT_DB_PATH", str(BASE_DIR / "chat_messages.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def db_connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id BIGINT UNIQUE NOT NULL,
                kind VARCHAR(16),
                media_type VARCHAR(8),
                caption TEXT DEFAULT '',
                duration INTEGER DEFAULT 0,
                file_size BIGINT DEFAULT 0,
                mime VARCHAR(64),
                posted_ts BIGINT,
                telegram_file_id TEXT,
                thumb_file_id TEXT
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
        conn.commit()

init_db()

def classify(msg):
    if msg is None or msg.empty:
        return None
    if getattr(msg, "video", None):
        m, mt = msg.video, "video"
    elif getattr(msg, "photo", None):
        m, mt = msg.photo, "photo"
    elif getattr(msg, "document", None) and (msg.document.mime_type or "").startswith("video/"):
        m, mt = msg.document, "video"
    else:
        return None
    cap = (msg.caption or "").strip()
    if STORY_MARKER in cap.lower():
        kind = "story"
    elif not cap:
        kind = "collection"
    else:
        return None
    
    file_id = None
    thumb_id = None
    if mt == "photo" and msg.photo:
        file_id = msg.photo.file_id
    elif mt == "video" and msg.video:
        file_id = msg.video.file_id
        if msg.video.thumbs:
            thumb_id = msg.video.thumbs[0].file_id

    return dict(
        message_id=msg.id, kind=kind, media_type=mt, caption=cap,
        duration=int(getattr(m, "duration", 0) or 0),
        file_size=int(getattr(m, "file_size", 0) or 0),
        mime=getattr(m, "mime_type", None) or ("image/jpeg" if mt == "photo" else "video/mp4"),
        posted_ts=int(msg.date.timestamp()) if msg.date else 0,
        telegram_file_id=file_id,
        thumb_file_id=thumb_id
    )

def save_scan(infos, seen_ids):
    added = updated = removed = 0
    with db_connect() as conn:
        cursor = conn.cursor()
        existing = {row["message_id"]: row for row in cursor.execute("SELECT message_id, kind FROM media").fetchall()}
        for info in infos:
            mid = info["message_id"]
            if mid in existing:
                cursor.execute("""
                    UPDATE media SET kind=?, media_type=?, caption=?, duration=?, file_size=?, mime=?, posted_ts=?, telegram_file_id=?, thumb_file_id=?
                    WHERE message_id=?
                """, (info["kind"], info["media_type"], info["caption"], info["duration"], info["file_size"], info["mime"], info["posted_ts"], info["telegram_file_id"], info["thumb_file_id"], mid))
                updated += 1
            else:
                cursor.execute("""
                    INSERT INTO media (message_id, kind, media_type, caption, duration, file_size, mime, posted_ts, telegram_file_id, thumb_file_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mid, info["kind"], info["media_type"], info["caption"], info["duration"], info["file_size"], info["mime"], info["posted_ts"], info["telegram_file_id"], info["thumb_file_id"]))
                added += 1
        if seen_ids:
            all_mids = [row["message_id"] for row in cursor.execute("SELECT message_id FROM media").fetchall()]
            for mid in all_mids:
                if mid not in seen_ids:
                    cursor.execute("DELETE FROM media WHERE message_id=?", (mid,))
                    removed += 1
        conn.commit()
    return added, updated, removed

# ───────────────────────── Telegram Runtime (Async Loop) ─────────────────────────
LOOP = asyncio.new_event_loop()
USER = BOT = None
STATE = {"user": False, "bot": False, "channel": None, "error": None}
SCAN_LOCK = asyncio.Lock()
_started = False
pending_uploads = {}
upload_lock = threading.Lock()

def run(coro, timeout=60):
    if USER is None:
        try:
            coro.close()
        except Exception:
            pass
        abort(503, "Telegram এখনো প্রস্তুত নয়")
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)

async def scan_channel_history(progress=None):
    infos, seen, total = [], set(), 0
    async for msg in USER.get_chat_history(CHANNEL):
        total += 1
        info = classify(msg)
        if info:
            infos.append(info)
            seen.add(msg.id)
            with db_connect() as conn:
                if info["kind"] == "story":
                    conn.execute("INSERT OR REPLACE INTO stories (message_id, media_type, mime_type, telegram_file_id, thumb_file_id, ts, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (msg.id, info["media_type"], info["mime"], info["telegram_file_id"], info["thumb_file_id"], info["posted_ts"], datetime.now(timezone.utc).isoformat()))
                else:
                    conn.execute("INSERT OR REPLACE INTO channel_posts (message_id, text, media_type, mime_type, telegram_file_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                 (msg.id, info["caption"], info["media_type"], info["mime"], info["telegram_file_id"], datetime.now(timezone.utc).isoformat()))
                conn.commit()
        if progress and total % 300 == 0:
            await progress(total, len(infos))
    added, updated, removed = await asyncio.to_thread(save_scan, infos, seen if total else set())
    return dict(total=total, found=len(infos), added=added, updated=updated, removed=removed)

def is_admin(uid):
    return (not ADMIN_KEYS) or uid in ADMIN_KEYS

def get_main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload Media (Photo/Video)", callback_data="start_upload")],
        [InlineKeyboardButton("➕ Add Story", callback_data="add_story")],
        [InlineKeyboardButton("🔄 Scan Channel Media", callback_data="run_scan")]
    ])

async def on_start(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply_text("⛔ অনুমতি নেই")
    pending_uploads.pop(m.from_user.id, None)
    await m.reply_text("👋 Welcome Admin!\n\nChoose an option below to upload channel media, add a new story, or scan channel media:", reply_markup=get_main_menu_markup())

async def on_cb(c, q):
    if not is_admin(q.from_user.id):
        return await q.answer("⛔ অনুমতি নেই", show_alert=True)
    
    data = q.data
    uid = q.from_user.id

    if data == "main_menu":
        pending_uploads.pop(uid, None)
        await q.message.edit_text("👋 Welcome Admin!\n\nChoose an option below to upload channel media, add a new story, or scan channel media:", reply_markup=get_main_menu_markup())
        await q.answer()

    elif data == "run_scan":
        if SCAN_LOCK.locked():
            return await q.answer("একটি স্ক্যান ইতিমধ্যে চলছে, অপেক্ষা করুন", show_alert=True)
        await q.message.edit_text("🔍 Scanning channel media and stories... Please wait.")
        async with SCAN_LOCK:
            try:
                async def progress(total, found):
                    try:
                        await q.message.edit_text(f"⏳ স্ক্যান চলছে...\nচেক: {total} | পাওয়া মিডিয়া: {found}")
                    except Exception:
                        pass
                await scan_channel_history(progress)
                back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Back to Menu", callback_data="main_menu")]])
                await q.message.edit_text("✅ Scan & Synchronization completed successfully! All photos and videos from the channel are now live on your website.", reply_markup=back_markup)
            except Exception as e:
                log.exception("scan failed")
                back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Back to Menu", callback_data="main_menu")]])
                await q.message.edit_text(f"❌ Scan failed: {str(e)}", reply_markup=back_markup)
        await q.answer()

    elif data == "start_upload":
        pending_uploads[uid] = {"mode": "post", "waiting_for_media": True, "media_group": [], "published": False}
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]])
        await q.message.edit_text("📥 Please send your photo(s), video(s), or document(s) now.\n\nYou can send single files sequentially or an album at once. Once finished, click 'Confirm & Upload'.", reply_markup=back_markup)
        await q.answer()

    elif data == "add_story":
        pending_uploads[uid] = {"mode": "story", "waiting_for_media": True, "media_group": [], "published": False}
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]])
        await q.message.edit_text("📸 **Add Story Mode**\n\nPlease send the photo(s) or video(s) you want to add to the website's Story Line.\nOnce finished, click 'Confirm & Upload'.", reply_markup=back_markup)
        await q.answer()

    elif data == "confirm_upload":
        with upload_lock:
            state = pending_uploads.get(uid, {})
            if state.get("published"):
                await q.answer("Already published!", show_alert=True)
                return
            media_list = state.get("media_group", [])
            if not media_list:
                await q.answer("No media found to upload!", show_alert=True)
                return
            mode = state.get("mode", "post")
            state["published"] = True
            pending_uploads[uid] = state

        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")]])
        await q.message.edit_text("⏳ Uploading to channel... Please wait.", reply_markup=back_markup)

        try:
            target_chat = await c.get_chat(STORAGE_CHANNEL_ID)
            sent_msgs = []
            for msg in media_list:
                orig_cap = msg.caption or ""
                if mode == "story" and STORY_MARKER not in orig_cap.lower():
                    final_cap = f"{STORY_MARKER}\n{orig_cap}".strip()
                else:
                    final_cap = orig_cap
                
                copied = await msg.copy(target_chat.id, caption=final_cap)
                if copied:
                    sent_msgs.append(copied)

            for s_msg in sent_msgs:
                info = classify(s_msg)
                if info:
                    with db_connect() as conn:
                        if info["kind"] == "story":
                            conn.execute("INSERT OR REPLACE INTO stories (message_id, media_type, mime_type, telegram_file_id, thumb_file_id, ts, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                         (s_msg.id, info["media_type"], info["mime"], info["telegram_file_id"], info["thumb_file_id"], info["posted_ts"], datetime.now(timezone.utc).isoformat()))
                        else:
                            conn.execute("INSERT OR REPLACE INTO channel_posts (message_id, text, media_type, mime_type, telegram_file_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                         (s_msg.id, info["caption"], info["media_type"], info["mime"], info["telegram_file_id"], datetime.now(timezone.utc).isoformat()))
                        conn.commit()

            pending_uploads.pop(uid, None)
            await q.message.edit_text(f"🎉 Successfully published {len(sent_msgs)} item(s) and synced with website!", reply_markup=back_markup)
        except Exception as e:
            with upload_lock:
                if uid in pending_uploads:
                    pending_uploads[uid]["published"] = False
            await q.message.edit_text(f"❌ Upload failed: {str(e)}", reply_markup=back_markup)
        await q.answer()

async def on_user_media(c, message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    state = pending_uploads.get(uid, {})
    if not state.get("waiting_for_media") or state.get("published"):
        return
    state["media_group"].append(message)
    pending_uploads[uid] = state
    count = len(state["media_group"])
    mode_label = "Story" if state.get("mode") == "story" else "Channel Media"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Upload", callback_data="confirm_upload")],
        [InlineKeyboardButton("« Back", callback_data="main_menu")]
    ])
    await message.reply(f"👍 {mode_label} received ({count} item(s) queued). Send more or click confirm below.", reply_markup=keyboard)

async def scan_cmd(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ অনুমতি নেই")
        return
    status_msg = await message.reply("🔍 Scanning channel media using Telethon/User session... Please wait.")
    try:
        await scan_channel_history()
        await status_msg.edit_text("✅ Scan completed successfully! All channel media and stories have been synchronized with the website.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Scan failed: {str(e)}")

async def _start_telegram():
    global USER, BOT
    try:
        u = Client("user", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING,
                   in_memory=True, sleep_threshold=60)
        await u.start()
        async for _ in u.get_dialogs():
            pass
        chat = await u.get_chat(CHANNEL)
        STATE.update(user=True, channel=getattr(chat, "title", str(CHANNEL)), error=None)
        USER = u
        log.info("User client ready: %s", STATE["channel"])
    except Exception as e:
        STATE["error"] = f"{type(e).__name__}: {e}"
        log.exception("User client start failed")
    
    try:
        from pyrogram.handlers import MessageHandler, CallbackQueryHandler
        b = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
        b.add_handler(MessageHandler(on_start, filters.command(["start", "menu"]) & filters.private))
        b.add_handler(MessageHandler(scan_cmd, filters.command(["scan"]) & filters.private))
        b.add_handler(MessageHandler(on_user_media, filters.private & (filters.photo | filters.video | filters.document)))
        b.add_handler(CallbackQueryHandler(on_cb))
        await b.start()
        BOT = b
        STATE["bot"] = True
        log.info("Bot ready")
    except Exception as e:
        STATE["error"] = (STATE["error"] or "") + f" | BOT {type(e).__name__}: {e}"
        log.exception("Bot start failed")

def _runner():
    asyncio.set_event_loop(LOOP)
    LOOP.run_until_complete(_start_telegram())
    LOOP.run_forever()

def start_telegram():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_runner, daemon=True, name="telegram").start()

# ───────────────────────── Message / Stream Cache ─────────────────────────
_lock = threading.Lock()
MSG_CACHE = OrderedDict()

async def _get_message(mid):
    return await USER.get_messages(CHANNEL, mid)

def get_msg(mid, fresh=False):
    now = time.time()
    with _lock:
        hit = MSG_CACHE.get(mid)
        if hit and not fresh and now - hit[0] < 600:
            return hit[1]
    msg = run(_get_message(mid))
    if not msg or msg.empty:
        return None
    with _lock:
        MSG_CACHE[mid] = (now, msg)
        while len(MSG_CACHE) > 200:
            MSG_CACHE.popitem(last=False)
    return msg

async def _open_stream(msg, limit, offset):
    return USER.stream_media(msg, limit=limit, offset=offset)

async def _anext(agen):
    try:
        return await agen.__anext__()
    except StopAsyncIteration:
        return None

async def _aclose(agen):
    await agen.aclose()

async def _download_thumb(file_id):
    return await USER.download_media(file_id, in_memory=True)

def stream_range(mid, start, end):
    first_chunk = start // CHUNK
    skip = start - first_chunk * CHUNK
    n_chunks = end // CHUNK - first_chunk + 1
    remaining = end - start + 1
    sent = False
    for attempt in (0, 1):
        msg = get_msg(mid, fresh=attempt > 0)
        if msg is None:
            return
        agen = run(_open_stream(msg, n_chunks, first_chunk))
        first = True
        try:
            while remaining > 0:
                chunk = run(_anext(agen), 120)
                if chunk is None:
                    return
                if first:
                    chunk, first = chunk[skip:], False
                chunk = chunk[:remaining]
                remaining -= len(chunk)
                sent = True
                yield bytes(chunk)
            return
        except Exception:
            if sent or attempt:
                log.exception("stream error")
                return
            log.warning("stream retry (fresh message)")
        finally:
            try:
                run(_aclose(agen), 10)
            except Exception:
                pass

# ───────────────────────── Flask Web App ─────────────────────────
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "alvee_super_secret_key_12345")

def clean_caption(c):
    return re.sub(re.escape(STORY_MARKER), "", c or "", flags=re.I).strip()

def ser(r):
    return {
        "id": r.message_id,
        "type": r.media_type,
        "kind": r.kind,
        "caption": clean_caption(r.caption),
        "duration": r.duration or 0,
        "date": r.posted_ts,
        "url": f"/media/{r.message_id}",
        "thumb": f"/thumb/{r.message_id}"
    }

@app.get("/api/stories")
def api_stories():
    hours = request.args.get("hours", 0, type=int)
    with db_connect() as conn:
        query = "SELECT * FROM media WHERE kind = 'story'"
        params = []
        if hours > 0:
            query += " AND posted_ts >= ?"
            params.append(int(time.time()) - hours * 3600)
        query += " ORDER BY posted_ts DESC LIMIT 100"
        rows = conn.execute(query, params).fetchall()
    items = []
    for row in rows:
        class R: pass
        r = R()
        r.message_id = row["message_id"]
        r.media_type = row["media_type"]
        r.kind = row["kind"]
        r.caption = row["caption"]
        r.duration = row["duration"]
        r.posted_ts = row["posted_ts"]
        items.append(ser(r))
    return jsonify(items)

@app.get("/api/collection")
def api_collection():
    page = max(1, request.args.get("page", 1, type=int))
    per = min(60, max(1, request.args.get("per_page", 24, type=int)))
    mtype = request.args.get("type")
    with db_connect() as conn:
        q_base = "FROM media WHERE kind = 'collection'"
        params = []
        if mtype in ("video", "photo"):
            q_base += " AND media_type = ?"
            params.append(mtype)
        total = conn.execute(f"SELECT COUNT(*) {q_base}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * {q_base} ORDER BY posted_ts DESC LIMIT ? OFFSET ?", params + [per, (page - 1) * per]).fetchall()
    items = []
    for row in rows:
        class R: pass
        r = R()
        r.message_id = row["message_id"]
        r.media_type = row["media_type"]
        r.kind = row["kind"]
        r.caption = row["caption"]
        r.duration = row["duration"]
        r.posted_ts = row["posted_ts"]
        items.append(ser(r))
    return jsonify({"items": items, "page": page, "pages": max(1, math.ceil(total / per)), "total": total})

@app.get("/health")
def health():
    return "ok", 200

@app.get("/api/status")
def api_status():
    with db_connect() as conn:
        counts = dict(conn.execute("SELECT kind, COUNT(*) FROM media GROUP BY kind").fetchall())
    return jsonify({**STATE, "db": DATABASE_URL.split(":")[0], "counts": counts})

@app.route("/media/<int:mid>", methods=["GET", "HEAD"])
def media(mid):
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM media WHERE message_id = ?", (mid,)).fetchone()
    if not row:
        abort(404)
    size = int(row["file_size"] or 0)
    start, end, status = 0, max(size - 1, 0), 200
    rng = request.headers.get("Range")
    if rng and size:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            a, b = m.groups()
            if a == "" and b:
                start, end = max(0, size - int(b)), size - 1
            else:
                start = int(a or 0)
                end = min(int(b) if b else size - 1, size - 1)
            if start > end or start >= size:
                return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600",
               "Content-Type": row["mime"] or "application/octet-stream"}
    if size:
        headers["Content-Length"] = str(end - start + 1)
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        return Response(status=status, headers=headers)
    if not size:
        abort(500, "file size অজানা — আবার স্ক্যান করুন")
    return Response(stream_range(mid, start, end), status=status, headers=headers, direct_passthrough=True)

@app.get("/thumb/<int:mid>")
def thumb(mid):
    thumb_cache = CACHE_DIR / f"thumb_{mid}.jpg"
    if thumb_cache.is_file():
        return send_file(thumb_cache, mimetype="image/jpeg", conditional=True)
    
    msg = get_msg(mid)
    if msg is None:
        abort(404)
    obj = msg.video or msg.photo or msg.document
    thumbs = getattr(obj, "thumbs", None) if obj else None
    if not thumbs:
        abort(404)
    buf = run(_download_thumb(thumbs[-1].file_id))
    data = buf.getvalue() if buf else None
    if not data:
        abort(404)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(thumb_cache, "wb") as f:
            f.write(data)
    except Exception:
        pass
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

@app.get("/")
def index():
    for name in ("index.html", "dashboard.html"):
        if os.path.exists(os.path.join(PUBLIC_DIR, name)):
            return send_from_directory(PUBLIC_DIR, name)
    return "Alvee Community Server is Running!", 200

@app.get("/<path:name>")
def pages(name):
    target = PUBLIC_DIR / name
    if target.is_file():
        return send_file(target)
    for page_name in ["naughty.html", "verify.html", "dashboard.html", "oc-collection.html", "oc-channel.js"]:
        if name == page_name and (PUBLIC_DIR / page_name).is_file():
            return send_file(PUBLIC_DIR / page_name)
    abort(404)

start_telegram()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
