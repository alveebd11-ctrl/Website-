import os
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient
import requests

app = Flask(__name__)
CORS(app)

MONGO_URI = "mongodb+srv://alveebd11_db_user:pInrzFCilPUxVIMy@cluster0.w8dmhdd.mongodb.net/?appName=Cluster0"
BOT_TOKEN = "7918291159:AAF3qmf-nbamXmbQU77lltmeCbU5uHIUAKE" 
GROUP_ID = "-5527916101"

client = MongoClient(MONGO_URI)
db = client['telegram_auth']
users_col = db['users']

# --- হোমপেজ (index.html) ---
@app.route('/', methods=['GET'])
def home():
    try:
        return send_file('index.html')
    except Exception:
        return "index.html not found", 404

# --- ভেরিফিকেশন পেজ (verify.html) ---
@app.route('/verify', methods=['GET'])
def verify_page():
    try:
        return send_file('verify.html')
    except Exception:
        return "verify.html not found", 404

# --- ডেটা রিসিভ করা ও টেলিগ্রাম গ্রুপে নোটিফিকেশন পাঠানো ---
@app.route('/auth', methods=['POST'])
def auth_user():
    user_data = request.json
    if not user_data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    user_id = user_data.get('id')
    first_name = user_data.get('first_name')
    username = user_data.get('username', 'No Username')

    if not users_col.find_one({'id': user_id}):
        users_col.insert_one(user_data)
        
        # টেলিগ্রাম এপিআই ব্যবহার করে সরাসরি গ্রুপে মেসেজ পাঠানো (কোনো পোলিং ঝামেলা ছাড়াই)
        msg = f"🟢 𝗡𝗲𝘄 𝗨𝘀𝗲𝗿 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱!\n\n👤 𝗡𝗮𝗺𝗲: {first_name}\n🆔 𝗜𝗗: {user_id}\n🔗 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲: @{username}"
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": GROUP_ID, "text": msg})
        except Exception as e:
            print(f"Telegram API Error: {e}")
            
    return jsonify({"status": "success", "message": "User verified!"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
