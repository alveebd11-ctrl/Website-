"""
Telegram-backed media server with OTP Login & Channel Sync
- মিডিয়া ফাইল সার্ভারে সেভ হয় না; শুধু মেটাডাটা DB-তে থাকে, ভিডিও/ফটো সরাসরি Telegram থেকে স্ট্রিম হয়।
- Pyrogram User Session (SESSION_STRING) দিয়ে চ্যানেল স্ক্যান + স্ট্রিম
- Pyrogram Bot দিয়ে বাটন: "Scan Channel Media" ও "Story Scan Media"
- Telethon & Telegram Login OTP API (/send-code, /verify-code)
- API: /api/stories, /api/collection, /media/<id>, /thumb/<id>, /api/status
"""
import os, re, time, math, asyncio, logging, threading
from collections import OrderedDict

from flask import Flask, jsonify, request, Response, abort, send_from_directory, session
from sqlalchemy import (create_engine, Column, Integer, BigInteger, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import declarative_base, sessionmaker

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, EditedMessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Telethon imports for OTP Login
from telethon import TelegramClient as TelethonClient
from telethon.sessions import StringSession

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
SESSION_STRING = need("SESSION_STRING")      # User account session (Pyrogram string)
BOT_TOKEN = need("BOT_TOKEN")
_ch = need("CHANNEL_ID")                     # যেমন -1001234567890 অথবা @username
CHANNEL = int(_ch) if re.fullmatch(r"-?\d+", _ch) else _ch.lstrip("@")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")  # ঐচ্ছিক: /api/scan/<mode> এর জন্য
STORY_TAG = os.environ.get("STORY_TAG", "#story").lower()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///media.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("sqlite"):
    log.warning("SQLite ব্যবহার হচ্ছে — ডিপ্লয়ে ডাটা মুছে যেতে পারে! DATABASE_URL এ Postgres দিন।")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")   # dashboard.html, oc-collection.html, frontend.js এখানে রাখুন
CHUNK = 1024 * 1024                             # Telegram stream chunk = 1 MiB

# ───────────────────────── Database ─────────────────────────
Base = declarative_base()

class Media(Base):
    __tablename__ = "media"
    id = Column(Integer, primary_key=True)
    message_id = Column(BigInteger, nullable=False, index=True)
    kind = Column(String(16), index=True)        # story | collection
    media_type = Column(String(8))               # video | photo
    caption = Column(Text, default="")
    duration = Column(Integer, default=0)
    file_size = Column(BigInteger, default=0)
    mime = Column(String(64))
    posted_ts = Column(BigInteger, index=True)
    __table_args__ = (UniqueConstraint("message_id", name="uq_media_message"),)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300,
                       connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
Session = sessionmaker(engine, expire_on_commit=False)
Base.metadata.create_all(engine)

def classify(msg):
    """মেসেজ থেকে মিডিয়া তথ্য বের করে। #STORY ক্যাপশন → story, ক্যাপশন নেই → collection"""
    if msg is None or msg.empty:
        return None
    if msg.video:
        m, mt = msg.video, "video"
    elif msg.photo:
        m, mt = msg.photo, "photo"
    elif msg.document and (msg.document.mime_type or "").startswith("video/"):
        m, mt = msg.document, "video"
    else:
        return None
    cap = (msg.caption or "").strip()
    if STORY_TAG in cap.lower():
        kind = "story"
    elif not cap:
        kind = "collection"
    else:
        return None
    return dict(
        message_id=msg.id, kind=kind, media_type=mt, caption=cap,
        duration=int(getattr(m, "duration", 0) or 0),
        file_size=int(getattr(m, "file_size", 0) or 0),
        mime=getattr(m, "mime_type", None) or ("image/jpeg" if mt == "photo" else "video/mp4"),
        posted_ts=int(msg.date.timestamp()) if msg.date else 0,
    )

def _apply(row, info):
    for k, v in info.items():
        setattr(row, k, v)

def save_scan(infos, mode, seen_ids):
    """upsert + (স্ক্যান সম্পূর্ণ হলে) মুছে ফেলা মেসেজ prune"""
    added = updated = removed = 0
    with Session() as s:
        existing = {r.message_id: r for r in s.query(Media).all()}
        for info in infos:
            r = existing.get(info["message_id"])
            if r:
                _apply(r, info); updated += 1
            else:
                s.add(Media(**info)); added += 1
        if seen_ids:
            for mid, r in existing.items():
                if r.kind == mode and mid not in seen_ids:
                    s.delete(r); removed += 1
        s.commit()
    return added, updated, removed

def sync_one(message_id, info):
    with Session() as s:
        r = s.query(Media).filter_by(message_id=message_id).first()
        if info:
            if r: _apply(r, info)
            else: s.add(Media(**info))
        elif r:
            s.delete(r)
        s.commit()

# ───────────────────────── Telegram runtime (আলাদা thread + event loop) ─────────────────────────
LOOP = asyncio.new_event_loop()
USER = BOT = None
STATE = {"user": False, "bot": False, "channel": None, "error": None}
SCAN_LOCK = asyncio.Lock()
_started = False
temp_client_sessions = {}

def run_telethon_async(coro, timeout=180):
    future = asyncio.run_coroutine_threadsafe(coro, LOOP)
    return future.result(timeout=timeout)

def run(coro, timeout=60):
    if USER is None:
        try:
            coro.close()
        except Exception:
            pass
        abort(503, "Telegram এখনো প্রস্তুত নয়")
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)

async def scan(mode, progress=None):
    """mode: 'story' বা 'collection'"""
    infos, seen, total = [], set(), 0
    async for msg in USER.get_chat_history(CHANNEL):
        total += 1
        info = classify(msg)
        if info and info["kind"] == mode:
            infos.append(info); seen.add(msg.id)
        if progress and total % 300 == 0:
            await progress(total, len(infos))
    added, updated, removed = await asyncio.to_thread(save_scan, infos, mode, seen if total else set())
    return dict(total=total, found=len(infos), added=added, updated=updated, removed=removed)

def is_admin(uid):
    return (not ADMIN_IDS) or uid in ADMIN_IDS

KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📡 Scan Channel Media", callback_data="scan_collection")],
    [InlineKeyboardButton("🎬 Story Scan Media", callback_data="scan_story")],
])

HINTS = {
    "PeerIdInvalid": "ইউজার অ্যাকাউন্ট চ্যানেলের মেম্বার নয়, অথবা CHANNEL_ID ভুল।",
    "ChannelInvalid": "চ্যানেল পাওয়া যায়নি — ইউজার অ্যাকাউন্টকে চ্যানেলে জয়েন করান।",
    "ChannelPrivate": "চ্যানেলটি প্রাইভেট — ইউজার অ্যাকাউন্ট জয়েন করা আছে কিনা দেখুন।",
    "AuthKeyUnregistered": "SESSION_STRING অকার্যকর — নতুন করে জেনারেট করুন।",
    "SessionRevoked": "সেশন বাতিল হয়েছে — নতুন SESSION_STRING তৈরি করুন।",
    "UserDeactivated": "ইউজার অ্যাকাউন্ট ডিঅ্যাক্টিভেটেড।",
}

async def on_start(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply_text("⛔ অনুমতি নেই")
    await m.reply_text("📂 মিডিয়া ম্যানেজার\nনিচের বাটন থেকে স্ক্যান করুন:", reply_markup=KB)

async def on_cb(c, q):
    if not is_admin(q.from_user.id):
        return await q.answer("⛔ অনুমতি নেই", show_alert=True)
    mode = q.data.split("_", 1)[1]
    label = "স্টোরি (#STORY)" if mode == "story" else "ভ্লগ কালেকশন (ক্যাপশনহীন)"
    if SCAN_LOCK.locked():
        return await q.answer("একটি স্ক্যান চলছে, অপেক্ষা করুন", show_alert=True)
    await q.answer("স্ক্যান শুরু হচ্ছে…")
    async with SCAN_LOCK:
        async def progress(total, found):
            try:
                await q.message.edit_text(f"⏳ {label} স্ক্যান চলছে…\nচেক: {total} | পাওয়া: {found}")
            except Exception:
                pass
        try:
            await q.message.edit_text(f"⏳ {label} স্ক্যান শুরু…")
            r = await scan(mode, progress)
            text = (f"✅ {label} স্ক্যান সম্পূর্ণ\n"
                    f"মোট মেসেজ চেক: {r['total']}\nপাওয়া মিডিয়া: {r['found']}\n"
                    f"নতুন: {r['added']} | আপডেট: {r['updated']} | মুছে ফেলা: {r['removed']}")
        except Exception as e:
            log.exception("scan failed")
            text = f"❌ স্ক্যান ব্যর্থ: {type(e).__name__}\n{HINTS.get(type(e).__name__, str(e)[:300])}"
        try:
            await q.message.edit_text(text, reply_markup=KB)
        except Exception:
            await q.message.reply_text(text, reply_markup=KB)

async def on_channel_msg(c, msg):
    """চ্যানেলে নতুন/এডিট পোস্ট এলে অটো সিঙ্ক"""
    try:
        await asyncio.to_thread(sync_one, msg.id, classify(msg))
    except Exception:
        log.exception("live sync failed")

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
        u.add_handler(MessageHandler(on_channel_msg, filters.chat(CHANNEL) & (filters.video | filters.photo | filters.document)))
        u.add_handler(EditedMessageHandler(on_channel_msg, filters.chat(CHANNEL)))
        USER = u
        log.info("User client ready: %s", STATE["channel"])
    except Exception as e:
        STATE["error"] = f"{type(e).__name__}: {e}"
        log.exception("User client start failed")
    try:
        b = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
        b.add_handler(MessageHandler(on_start, filters.command(["start", "menu"]) & filters.private))
        b.add_handler(CallbackQueryHandler(on_cb, filters.regex(r"^scan_(story|collection)$")))
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

# ───────────────────────── Message / Thumb cache ─────────────────────────
_lock = threading.Lock()
MSG_CACHE, THUMB_CACHE = OrderedDict(), OrderedDict()

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

# ───────────────────────── Flask ─────────────────────────
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "alvee_super_secret_key_12345")

def clean_caption(c):
    return re.sub(re.escape(STORY_TAG), "", c or "", flags=re.I).strip()

def ser(r):
    return {"id": r.message_id, "type": r.media_type, "kind": r.kind,
            "caption": clean_caption(r.caption), "duration": r.duration or 0, "date": r.posted_ts,
            "url": f"/media/{r.message_id}", "thumb": f"/thumb/{r.message_id}"}

# ───────────────────────── Telegram OTP Login Endpoints ─────────────────────────
@app.post("/send-code")
def send_code_api():
    req_data = request.get_json(silent=True) or {}
    phone = req_data.get("phone", "").strip()
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required."}), 400

    async def async_send():
        client = TelethonClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        sent = await client.send_code_request(phone)
        temp_client_sessions[phone] = client
        return {"status": "success", "phone_code_hash": sent.phone_code_hash}

    try:
        res = run_telethon_async(async_send(), timeout=180)
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.post("/verify-code")
def verify_code_api():
    req_data = request.get_json(silent=True) or {}
    phone = req_data.get("phone", "").strip()
    code = req_data.get("code", "").strip()
    phone_code_hash = req_data.get("phone_code_hash", "").strip()

    client = temp_client_sessions.get(phone) or (list(temp_client_sessions.values())[0] if temp_client_sessions else None)
    if not client:
        return jsonify({"status": "error", "message": "Session expired or phone missing."}), 400

    async def async_verify():
        if not client.is_connected():
            await client.connect()
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        return {"status": "success"}

    try:
        res = run_telethon_async(async_verify(), timeout=180)
        session["telegram_logged_in"] = True
        session["user_phone"] = phone
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if phone:
            temp_client_sessions.pop(phone, None)
        if client and client.is_connected():
            try:
                run_telethon_async(client.disconnect(), timeout=10)
            except Exception:
                pass

@app.get("/api/stories")
def api_stories():
    hours = request.args.get("hours", 0, type=int)
    with Session() as s:
        q = s.query(Media).filter(Media.kind == "story")
        if hours > 0:
            q = q.filter(Media.posted_ts >= int(time.time()) - hours * 3600)
        rows = q.order_by(Media.posted_ts.desc()).limit(100).all()
    return jsonify({"items": [ser(r) for r in rows]})

@app.get("/api/collection")
def api_collection():
    page = max(1, request.args.get("page", 1, type=int))
    per = min(60, max(1, request.args.get("per_page", 24, type=int)))
    mtype = request.args.get("type")
    with Session() as s:
        q = s.query(Media).filter(Media.kind == "collection")
        if mtype in ("video", "photo"):
            q = q.filter(Media.media_type == mtype)
        total = q.count()
        rows = q.order_by(Media.posted_ts.desc()).offset((page - 1) * per).limit(per).all()
    return jsonify({"items": [ser(r) for r in rows], "page": page,
                    "pages": max(1, math.ceil(total / per)), "total": total})

@app.get("/health")
def health():
    return "ok", 200

@app.get("/api/status")
def api_status():
    with Session() as s:
        counts = dict(s.query(Media.kind, func.count(Media.id)).group_by(Media.kind).all())
    return jsonify({**STATE, "db": DATABASE_URL.split(":")[0], "counts": counts})

@app.post("/api/scan/<mode>")
def api_scan(mode):
    if not ADMIN_KEY or request.headers.get("X-Admin-Key") != ADMIN_KEY:
        abort(403)
    if mode not in ("story", "collection"):
        abort(400)
    if USER is None:
        abort(503)
    async def job():
        async with SCAN_LOCK:
            try:
                log.info("API scan %s: %s", mode, await scan(mode))
            except Exception:
                log.exception("API scan failed")
    asyncio.run_coroutine_threadsafe(job(), LOOP)
    return jsonify({"started": mode}), 202

@app.route("/media/<int:mid>", methods=["GET", "HEAD"])
def media(mid):
    with Session() as s:
        row = s.query(Media).filter_by(message_id=mid).first()
    if not row:
        abort(404)
    size = int(row.file_size or 0)
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
               "Content-Type": row.mime or "application/octet-stream"}
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
    with _lock:
        data = THUMB_CACHE.get(mid)
    if data is None:
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
        with _lock:
            THUMB_CACHE[mid] = data
            while len(THUMB_CACHE) > 300:
                THUMB_CACHE.popitem(last=False)
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

@app.get("/")
def index():
    for name in ("index.html", "dashboard.html"):
        if os.path.exists(os.path.join(PUBLIC_DIR, name)):
            return send_from_directory(PUBLIC_DIR, name)
    abort(404)

@app.get("/<path:name>")
def pages(name):
    return send_from_directory(PUBLIC_DIR, name)

start_telegram()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
