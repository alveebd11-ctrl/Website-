import os
import threading
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient
import telebot

app = Flask(__name__)
CORS(app)

MONGO_URI = "mongodb+srv://alveebd11_db_user:pInrzFCilPUxVIMy@cluster0.w8dmhdd.mongodb.net/?appName=Cluster0"
BOT_TOKEN = "7918291159:AAF3qmf-nbamXmbQU77lltmeCbU5uHIUAKE" 
GROUP_ID = "-5527916101"

client = MongoClient(MONGO_URI)
db = client['telegram_auth']
users_col = db['users']

bot = telebot.TeleBot(BOT_TOKEN)

# --- ওয়েবসাইট শো করার জন্য (আপনার আসল ডিজাইন) ---
@app.route('/', methods=['GET'])
def home():
    # লিংকে ঢুকলেই আপনার verify.html পেজটি দেখাবে
    return send_file('verify.html')

@app.route('/style.css', methods=['GET'])
def style():
    # ডিজাইনের জন্য CSS ফাইলটি লোড করবে
    return send_file('style.css')

# --- ডেটা রিসিভ করা ও গ্রুপে পাঠানো ---
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
        
        msg = f"🟢 𝗡𝗲𝘄 𝗨𝘀𝗲𝗿 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱!\n\n👤 𝗡𝗮𝗺𝗲: {first_name}\n🆔 𝗜𝗗: {user_id}\n🔗 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲: @{username}"
        try:
            bot.send_message(GROUP_ID, msg)
        except Exception as e:
            print(f"Telegram Message Error: {e}")
            
    return jsonify({"status": "success", "message": "User verified!"})

@bot.message_handler(commands=['stats', 'users'])
def send_stats(message):
    count = users_col.count_documents({})
    bot.reply_to(message, f"📊 𝗧𝗼𝘁𝗮𝗹 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱 𝗨𝘀𝗲𝗿𝘀: {count}")

def run_bot():
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"Bot Polling Error: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
