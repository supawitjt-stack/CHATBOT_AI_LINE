from urllib import response
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import google.generativeai as genai
import os
import sys
from dotenv import load_dotenv

# โหลด Environment Variables
load_dotenv()

# --- 1. การเตรียมข้อมูลและ Prompt (เหมือนใน app.py) ---

# กำหนด Path ของโฟลเดอร์ CHATBOT_AI เพื่อให้สามารถดึงไฟล์ Prompt และ Cache ได้
current_dir = os.path.dirname(os.path.abspath(__file__))
chatbot_ai_dir = os.path.join(current_dir, 'LINE_his')
sys.path.append(chatbot_ai_dir)

# พยายามโหลด Prompt และเนื้อหาเอกสาร (Knowledge Base)
try:
    from prompt import PROMPT_C_Programmer
except ImportError:
    # กรณีหาไฟล์ไม่เจอ ใช้ค่า Default
    PROMPT_C_Programmer = "คุณคือผู้ช่วยสอนภาษา C ที่มีความเชี่ยวชาญ"
    print("Warning: Could not import prompt from CHATBOT_AI")

def get_knowledge_base():
    cache_path = os.path.join(chatbot_ai_dir, "extracted_content_cache.txt")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                print(f"Loaded Knowledge Base from: {cache_path}")
                return f.read()
        except Exception as e:
            print(f"Error reading cache file: {e}")
            return None
    else:
        print(f"Cache file not found at: {cache_path}")
        return None

# โหลดเนื้อหาเตรียมไว้ในหน่วยความจำ
kb_content = get_knowledge_base()

# --- 2. ตั้งค่า Flask และ LINE Bot ---

app = Flask(__name__)

# LINE Bot Config
# แนะนำ: ควรใช้ os.getenv('LINE_ACCESS_TOKEN') และ os.getenv('LINE_CHANNEL_SECRET') เพื่อความปลอดภัย
configuration = Configuration(access_token='+P1otaC6jNIjjBO1KirvxY42KZ4DrXowBfrg4+A2K4X3bn27hdI9TXsU5WQdQsRbcew46f1okyiGa0nF/Srp5x4uZGQkCn8+NXQZ9SZ0edXRfLi8qivMp4SnDVMT2NpddSQ+vfr1giompcKeZq2xDQdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('5f320d1a9b1130dce4ad530395ea099d')

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# --- 3. ตั้งค่า Gemini (ปรับให้เหมือน app.py) ---

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    print("Error: GOOGLE_API_KEY not found in environment variables.")

# ปรับ Config ให้เหมือน app.py (ลด temperature เพื่อความแม่นยำ)
generation_config = {
    "temperature": 0.1,  # ปรับจาก 1 เป็น 0.1 เพื่อให้ตอบตรงเนื้อหา
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 2048,
    "response_mime_type": "text/plain",
}

# สร้าง System Instruction ที่รวมทั้ง Persona และ Knowledge Base
# วิธีนี้ช่วยให้ Chat Session จดจำเอกสารได้ตลอดการสนทนา
system_instruction_text = PROMPT_C_Programmer

if kb_content:
    system_instruction_text += f"""

    # KNOWLEDGE BASE (ข้อมูลอ้างอิงหลัก)
    ข้อมูลที่คุณต้องใช้ในการตอบคำถามมีดังนี้ (ห้ามตอบนอกเหนือจากนี้):
    --------------------------------------------------
    {kb_content}
    --------------------------------------------------
    
    # STRICT RULES
    1. ตอบคำถามโดยอ้างอิงจาก "KNOWLEDGE BASE" ที่ให้ไว้ด้านบนเท่านั้น
    2. หากต้องเขียนโค้ดตัวอย่าง ให้ใช้ตัวอย่างที่มีในเอกสารเท่านั้น
    3. ถ้าไม่มีข้อมูลในเอกสาร ให้ตอบว่า "ขออภัยครับ ข้อมูลส่วนนี้น้อง C ยังไม่ได้เรียนรู้มาเลยครับ"
    4. ตอบตรงเนื้อหาและการยกตัวอย่างต้องครบถ้วนตรงตาม KNOWLEDGE BASE 100%
    """

# --- 4. ส่วนการทำงานของ Bot ---

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature.")
        abort(400)

    return 'OK'

user_gemini_sessions = {}

def get_or_create_chat_session(user_id):
    if user_id not in user_gemini_sessions:
        # สร้างโมเดลพร้อม System Instruction ที่ใส่เนื้อหาเอกสารไว้แล้ว
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config=generation_config,
            system_instruction=system_instruction_text
        )
        user_gemini_sessions[user_id] = model.start_chat(history=[])
    return user_gemini_sessions[user_id]

def chat_with_gemini(user_id, user_message):
    try:
        chat_session = get_or_create_chat_session(user_id)
        
        # ส่งข้อความไปถาม (Context ถูกกำหนดไว้ใน system_instruction แล้ว)
        # แต่เราสามารถย้ำคำสั่งใน prompt ได้อีกครั้งเพื่อให้มั่นใจ
        final_user_prompt = f"""
        คำถาม: {user_message}
        
        (เตือน: ตอบโดยใช้ข้อมูลจาก KNOWLEDGE BASE เท่านั้น)
        """
        
        response = chat_session.send_message(final_user_prompt)
        # 1. เอาข้อความจาก AI มาเก็บใส่ตัวแปรไว้ก่อน
        clean_text = response.text

        # 2. สั่งลบตัวหนังสือขยะทิ้ง (replace)
        # ลบ ```c (หัวข้อโค้ด)
        clean_text = clean_text.replace("```c", "") 
        # ลบ ``` (ตัวปิดท้ายโค้ด)
        clean_text = clean_text.replace("```", "")
        # (แถม) ลบคำว่า Code C Language block: ที่มักจะติดมาด้วย
        clean_text = clean_text.replace("Code C Language block:", "")
        # แสดงผลใน Terminal ดูว่าสะอาดหรือยัง
        print(f'Gemini Response for {user_id}:', clean_text)
        # 3. ส่งค่าที่สะอาดแล้วกลับไป
        return clean_text

    except Exception as e:
        print(f"Error generating response: {e}")
        return "ขออภัยครับ ระบบประมวลผลขัดข้องชั่วคราว ลองถามใหม่อีกครั้งนะครับ"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    reply_text = ""

    # ตรวจสอบคำทักทายเบื้องต้น
    if user_message.strip() in ['สวัสดี', 'นี่ใคร', 'hi', 'hello'] or user_message.startswith('สวัสดี'):
        reply_text = "สวัสดีครับ! ผมคือน้องเซียน C ผู้ช่วยสอนภาษา C ครับ มีอะไรให้ผมช่วยไหมครับ? 🤖"
    else:
        # ส่งเข้าฟังก์ชัน Gemini ที่ปรับปรุงแล้ว
        reply_text = chat_with_gemini(user_id, user_message)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    # ใช้ Port ตามสภาพแวดล้อม หรือ Default ที่ 5000
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)