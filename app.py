import os
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import telebot

app = Flask(__name__)
CORS(app) # ফ্রন্টএন্ড থেকে ডেটা রিসিভ করার পারমিশন

# --- আপনার দেওয়া ইনফরমেশন ---
MONGO_URI = "mongodb+srv://alveebd11_db_user:pInrzFCilPUxVIMy@cluster0.w8dmhdd.mongodb.net/?appName=Cluster0"
BOT_TOKEN = "8617170555:AAEd6jdZQ3DRPZNtQW7UwgGujqwiQBJR05Q"
GROUP_ID = "-5527916101"

# --- ডাটাবেস ও বট কানেকশন ---
client = MongoClient(MONGO_URI)
db = client['telegram_auth']
users_col = db['users']

bot = telebot.TeleBot(BOT_TOKEN)

# --- ১. ওয়েবসাইট থেকে ডেটা রিসিভ করা ও গ্রুপে পাঠানো ---
@app.route('/auth', methods=['POST'])
def auth_user():
    user_data = request.json
    user_id = user_data.get('id')
    first_name = user_data.get('first_name')
    username = user_data.get('username', 'No Username')

    # চেক করা হচ্ছে এই ইউজার আগে লগইন করেছে কি না
    if not users_col.find_one({'id': user_id}):
        users_col.insert_one(user_data) # MongoDB তে সেভ করা
        
        # আপনার ডেটাবেস গ্রুপে মেসেজ পাঠানো
        msg = f"🟢 𝗡𝗲𝘄 𝗨𝘀𝗲𝗿 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱!\n\n👤 𝗡𝗮𝗺𝗲: {first_name}\n🆔 𝗜𝗗: {user_id}\n🔗 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲: @{username}"
        try:
            bot.send_message(GROUP_ID, msg)
        except Exception as e:
            pass
            
    return jsonify({"status": "success", "message": "User verified!"})

# --- ২. অ্যাডমিন বটের কমান্ড (কতজন ইউজার আছে দেখার জন্য) ---
@bot.message_handler(commands=['stats', 'users'])
def send_stats(message):
    count = users_col.count_documents({})
    bot.reply_to(message, f"📊 𝗧𝗼𝘁𝗮𝗹 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱 𝗨𝘀𝗲𝗿𝘀: {count}")

# বটকে ব্যাকগ্রাউন্ডে চালু রাখা
def run_bot():
    bot.infinity_polling()

if __name__ == '__main__':
    threading.Thread(target=run_bot).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
