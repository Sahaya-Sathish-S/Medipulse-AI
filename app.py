import base64
import io
import os
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

import requests

from pypdf import PdfReader

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from apscheduler.schedulers.background import BackgroundScheduler

from dotenv import load_dotenv


# =========================================================
# LOAD ENV
# =========================================================

load_dotenv()


# =========================================================
# FLASK APP
# =========================================================

app = Flask(__name__)

CORS(app)


# =========================================================
# API CONFIG
# =========================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# =========================================================
# SAFE WORKING MODELS
# =========================================================

MODELS = [

    "openai/gpt-4o-mini",

    "meta-llama/llama-3.1-8b-instruct",

    "qwen/qwen-2.5-7b-instruct",

    "microsoft/phi-3-mini-128k-instruct"

]


# =========================================================
# EMAIL CONFIG
# =========================================================

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# =========================================================
# BACKGROUND SCHEDULER
# =========================================================

scheduler = BackgroundScheduler(daemon=True)

scheduler.start()


# =========================================================
# UNIVERSAL AI FUNCTION
# =========================================================

def ask_ai(messages, temperature=0.4, max_tokens=1000):

    if not OPENROUTER_API_KEY:

        return "❌ OPENROUTER_API_KEY missing in .env"

    headers = {

        "Authorization": f"Bearer {OPENROUTER_API_KEY}",

        "Content-Type": "application/json",

        "HTTP-Referer": "http://localhost:5000",

        "X-Title": "MediPulse AI"

    }

    for model in MODELS:

        try:

            print(f"\n===== TRYING MODEL: {model} =====")

            payload = {

                "model": model,

                "messages": messages,

                "temperature": temperature,

                "max_tokens": max_tokens

            }

            response = requests.post(

                OPENROUTER_URL,

                headers=headers,

                json=payload,

                timeout=60

            )

            print("STATUS:", response.status_code)

            if response.status_code == 200:

                result = response.json()

                reply = result["choices"][0]["message"]["content"]

                print("SUCCESS:", model)

                return reply

            else:

                print("FAILED MODEL:", model)

                print(response.text)

        except Exception as e:

            print("MODEL ERROR:", str(e))

    return (
        "⚠️ AI service unavailable temporarily. "
        "Please try again later."
    )


# =========================================================
# EMAIL FUNCTION
# =========================================================

def send_email(to_email, subject, body):

    try:

        msg = MIMEMultipart()

        msg["From"] = GMAIL_USER

        msg["To"] = to_email

        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587)

        server.starttls()

        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)

        server.sendmail(

            GMAIL_USER,

            to_email,

            msg.as_string()

        )

        server.quit()

        print("EMAIL SENT")

        return True

    except Exception as e:

        print("EMAIL ERROR:", str(e))

        return False


# =========================================================
# REMINDER FUNCTIONS
# =========================================================

def send_initial_reminder(data):

    subject = f"⚠️ Medicine Reminder: {data['medicine_name']}"

    body = f"""
Hello {data['username']},

Time to take your medicine.

Medicine: {data['medicine_name']}
Dosage: {data['dosage']}

Please mark it as taken.
"""

    send_email(data["user_email"], subject, body)

    escalation_time = datetime.now() + timedelta(minutes=30)

    escalation_job_id = f"escalate_{data['medicine_id']}"

    if scheduler.get_job(escalation_job_id):

        scheduler.remove_job(escalation_job_id)

    scheduler.add_job(

        id=escalation_job_id,

        func=send_family_escalation,

        trigger="date",

        run_date=escalation_time,

        args=[data]

    )


def send_family_escalation(data):

    subject = "🚨 Missed Medication Alert"

    body = f"""
{data['username']} has not taken:

{data['medicine_name']}

Please check immediately.
"""

    send_email(data["family_email"], subject, body)


# =========================================================
# SCHEDULE REMINDER
# =========================================================

@app.route("/api/schedule_reminder", methods=["POST"])
def schedule_reminder():

    try:

        data = request.get_json()

        medicine_id = data.get("medicine_id")

        medicine_time = data.get("medicine_time")

        hour, minute = map(int, medicine_time.split(":"))

        job_id = f"reminder_{medicine_id}"

        if scheduler.get_job(job_id):

            scheduler.remove_job(job_id)

        scheduler.add_job(

            id=job_id,

            func=send_initial_reminder,

            trigger="cron",

            hour=hour,

            minute=minute,

            args=[data]

        )

        return jsonify({

            "status": "success",

            "message": "Reminder scheduled"

        })

    except Exception as e:

        return jsonify({

            "status": "error",

            "message": str(e)

        })


# =========================================================
# MEDICATION TAKEN
# =========================================================

@app.route("/api/medication_taken", methods=["POST"])
def medication_taken():

    try:

        data = request.get_json()

        medicine_id = data.get("medicine_id")

        escalation_job_id = f"escalate_{medicine_id}"

        if scheduler.get_job(escalation_job_id):

            scheduler.remove_job(escalation_job_id)

        return jsonify({

            "status": "success"

        })

    except Exception as e:

        return jsonify({

            "status": "error",

            "message": str(e)

        })


# =========================================================
# EMERGENCY EMAIL
# =========================================================

@app.route("/api/send_emergency_email", methods=["POST"])
def send_emergency_email():

    try:

        data = request.get_json()

        subject = "🚨 Emergency Blood Requirement"

        body = f"""
Dear {data.get('donor_name')},

Emergency blood needed.

Blood Group: {data.get('blood_group')}

Please help if possible.

- MediPulse
"""

        send_email(

            data.get("to_email"),

            subject,

            body

        )

        return jsonify({

            "status": "success"

        })

    except Exception as e:

        return jsonify({

            "status": "error",

            "message": str(e)

        })


# =========================================================
# PDF TEXT EXTRACTOR
# =========================================================

def extract_text_from_pdf(base64_data):

    try:

        if "," in base64_data:

            base64_data = base64_data.split(",")[1]

        pdf_bytes = base64.b64decode(base64_data)

        pdf_file = io.BytesIO(pdf_bytes)

        reader = PdfReader(pdf_file)

        extracted_text = ""

        for page in reader.pages:

            text = page.extract_text()

            if text:

                extracted_text += text + "\n"

        return extracted_text

    except Exception as e:

        return f"PDF Extraction Error: {str(e)}"


# =========================================================
# MAIN CHATBOT
# =========================================================

@app.route("/chat", methods=["POST"])
def chat():

    try:

        data = request.get_json()

        user_message = data.get("message", "")

        history = data.get("history", [])

        messages = [

            {

                "role": "system",

                "content": """
You are MediPulse AI.

You are:
- Professional
- Friendly
- Helpful
- Short and clear

Languages:
- English
- Tamil
- Hindi
"""

            }

        ]

        for msg in history:

            role = msg.get("role", "user")

            content = msg.get("content", "")

            messages.append({

                "role": role,

                "content": content

            })

        messages.append({

            "role": "user",

            "content": user_message

        })

        ai_reply = ask_ai(messages)

        return jsonify({

            "reply": ai_reply

        })

    except Exception as e:

        print("CHAT ERROR:", str(e))

        return jsonify({

            "reply": "⚠️ Chatbot unavailable."

        })


# =========================================================
# MEDICINE AI
# =========================================================

@app.route("/medicine_ai", methods=["POST"])
def medicine_ai():

    try:

        data = request.get_json()

        symptoms = data.get("message", "")

        prompt = f"""
User symptoms:

{symptoms}

Suggest:
- OTC medicine
- Dosage
- Advice
- Warning

Keep response short.
"""

        messages = [

            {

                "role": "system",

                "content": "You are a pharmacy assistant."

            },

            {

                "role": "user",

                "content": prompt

            }

        ]

        reply = ask_ai(messages)

        return jsonify({

            "reply": reply

        })

    except Exception as e:

        return jsonify({

            "reply": str(e)

        })


# =========================================================
# PRESCRIPTION AI
# =========================================================

@app.route("/prescription_ai", methods=["POST"])
def prescription_ai():

    try:

        data = request.get_json()

        file_content = data.get("file_content", "")
        file_type = data.get("file_type", "")

        if not file_content:

            return jsonify({

                "reply": "Please upload a prescription."

            })

        headers = {

            "Authorization": f"Bearer {OPENROUTER_API_KEY}",

            "Content-Type": "application/json",

            "HTTP-Referer": "http://localhost:5000",

            "X-Title": "MediPulse AI"

        }

        # =====================================================
        # PDF SUPPORT
        # =====================================================

        if file_type == "application/pdf":

            extracted_text = extract_text_from_pdf(file_content)

            prompt = f"""
Analyze this medical prescription.

Extract:
- Medicine names
- Dosage
- Timing
- Instructions

TEXT:
{extracted_text}

Keep response clean and short.
"""

            payload = {

                "model": "openai/gpt-3.5-turbo",

                "messages": [

                    {
                        "role": "system",

                        "content": "You are a prescription analysis AI."
                    },

                    {
                        "role": "user",

                        "content": prompt
                    }

                ],

                "max_tokens": 700

            }

        # =====================================================
        # IMAGE SUPPORT
        # =====================================================

        elif "image" in file_type:

            base64_clean = (
                file_content.split(",")[1]
                if "," in file_content
                else file_content
            )

            prompt = """
Analyze this prescription image carefully.

Extract:
- Medicine names
- Dosage
- Timing
- Instructions

If handwriting is unclear,
mention it politely.

Keep response professional.
"""

            payload = {

                "model": "openai/gpt-4o-mini",

                "messages": [

                    {

                        "role": "user",

                        "content": [

                            {
                                "type": "text",
                                "text": prompt
                            },

                            {
                                "type": "image_url",

                                "image_url": {

                                    "url": f"data:{file_type};base64,{base64_clean}"

                                }
                            }

                        ]

                    }

                ],

                "max_tokens": 700

            }

        else:

            return jsonify({

                "reply": "Unsupported file format."

            })

        # =====================================================
        # SEND REQUEST
        # =====================================================

        response = requests.post(

            OPENROUTER_URL,

            headers=headers,

            json=payload,

            timeout=60

        )

        print("PRESCRIPTION STATUS:", response.status_code)
        print(response.text)

        if response.status_code != 200:

            return jsonify({

                "reply": f"Prescription AI Error: {response.status_code}"

            })

        result = response.json()

        ai_reply = result["choices"][0]["message"]["content"]

        return jsonify({

            "reply": ai_reply

        })

    except Exception as e:

        print("PRESCRIPTION ERROR:", str(e))

        return jsonify({

            "reply": f"Prescription processing error: {str(e)}"

        })

# =========================================================
# ANALYTICS AI
# =========================================================

@app.route("/analyze_ai", methods=["POST"])
def analyze_ai():

    try:

        data = request.get_json()

        purchases = data.get("purchases", [])

        total = 0

        for item in purchases:

            total += float(item.get("total_price", 0))

        prompt = f"""
Total pharmacy sales: ₹{total}

Give:
- Business insights
- Stock ideas
- Marketing ideas
"""

        messages = [

            {

                "role": "system",

                "content": "You are a business analyst."

            },

            {

                "role": "user",

                "content": prompt

            }

        ]

        reply = ask_ai(messages)

        return jsonify({

            "reply": reply

        })

    except Exception as e:

        return jsonify({

            "reply": str(e)

        })


# =========================================================
# MAP AI
# =========================================================

@app.route("/map_ai", methods=["POST"])
def map_ai():

    try:

        data = request.get_json()

        prompt = f"""
Destination: {data.get('destination')}

Distance: {data.get('distance')}

Duration: {data.get('duration')}

Message:
{data.get('message')}

Give navigation help.
"""

        messages = [

            {

                "role": "system",

                "content": "You are a navigation AI."

            },

            {

                "role": "user",

                "content": prompt

            }

        ]

        reply = ask_ai(messages)

        return jsonify({

            "reply": reply

        })

    except Exception as e:

        return jsonify({

            "reply": str(e)

        })


# =========================================================
# IMAGE ANALYSIS AI
# =========================================================

@app.route("/image_ai", methods=["POST"])
def image_ai():

    try:

        data = request.get_json()

        image_text = data.get("image_text", "")

        prompt = f"""
Analyze this medical image description:

{image_text}

Give short medical explanation.
"""

        messages = [

            {

                "role": "system",

                "content": "You are a medical image assistant."

            },

            {

                "role": "user",

                "content": prompt

            }

        ]

        reply = ask_ai(messages)

        return jsonify({

            "reply": reply

        })

    except Exception as e:

        return jsonify({

            "reply": str(e)

        })


# =========================================================
# TITLE GENERATOR
# =========================================================

@app.route("/generate_title", methods=["POST"])
def generate_title():

    try:

        user_message = request.json.get("message", "")

        messages = [

            {

                "role": "system",

                "content": "Generate short title only."

            },

            {

                "role": "user",

                "content": user_message

            }

        ]

        reply = ask_ai(

            messages,

            temperature=0.2,

            max_tokens=20

        )

        return jsonify({

            "title": reply.strip()

        })

    except:

        return jsonify({

            "title": "New Chat"

        })

# =========================================================
# AI EYE SCAN ANALYSIS
# =========================================================

@app.route("/analyze_eye_scan", methods=["POST"])
def analyze_eye_scan():

    try:

        data = request.get_json()

        file_content = data.get("file_content", "")
        file_type = data.get("file_type", "image/jpeg")

        if not file_content:
            return jsonify({
                "reply": "Please upload an eye image."
            })

        # Remove base64 prefix
        base64_clean = (
            file_content.split(",")[1]
            if "," in file_content
            else file_content
        )

        headers = {

            "Authorization": f"Bearer {OPENROUTER_API_KEY}",

            "Content-Type": "application/json",

            "HTTP-Referer": "http://localhost:5000",

            "X-Title": "MediPulse AI"

        }

        prompt = """
You are a professional ophthalmology AI assistant.

Analyze this eye image carefully.

Check:
- Redness
- Cataract signs
- Yellowing
- Swelling
- Pupil abnormalities

Give:
1. Observations
2. Possible indicators
3. Advice
4. Safety warning

Keep response short and professional.
"""

        payload = {

            "model": "openai/gpt-4o-mini",

            "messages": [

                {
                    "role": "user",

                    "content": [

                        {
                            "type": "text",
                            "text": prompt
                        },

                        {
                            "type": "image_url",

                            "image_url": {
                                "url": f"data:{file_type};base64,{base64_clean}"
                            }
                        }

                    ]
                }

            ],

            "max_tokens": 700

        }

        response = requests.post(

            OPENROUTER_URL,

            headers=headers,

            json=payload,

            timeout=60

        )

        print("VISION STATUS:", response.status_code)
        print(response.text)

        if response.status_code != 200:

            return jsonify({

                "reply": f"Vision AI Error: {response.status_code}"

            })

        result = response.json()

        ai_reply = result["choices"][0]["message"]["content"]

        return jsonify({

            "reply": ai_reply

        })

    except Exception as e:

        print("VISION ERROR:", str(e))

        return jsonify({

            "reply": f"Diagnostic processing error: {str(e)}"

        })
# =========================================================
# PAGE ROUTES
# =========================================================
@app.route("/")
def home(): return render_template("home.html")

@app.route("/medicine-search")
def medicine_search(): return render_template("ai_medicine.html")

@app.route("/checker")
def checker(): return render_template("checker.html")

@app.route("/setup")
def setup(): return render_template("setup.html")

@app.route("/hospital")
def hospital(): return render_template("hospital.html")

@app.route("/donation")
def donation(): return render_template("donation.html")

@app.route("/register")
def register(): return render_template("register.html")

@app.route("/emergency")
def emergency(): return render_template("Emergency.html")

@app.route("/emergency-medicine")
def emergency_medicine(): return render_template("emergency_medicine_ai.html")

@app.route("/scanner")
def scanner(): return render_template("scanner.html")

@app.route("/free-map")
def free_map(): return render_template("medical_map.html")

@app.route("/chatbot")
def chatbot(): return render_template("chatbot.html")

@app.route("/login")
def login(): return render_template("login.html")

@app.route("/signup")
def signup(): return render_template("signup.html")

@app.route("/profile")
def profile(): return render_template("profile.html")

@app.route("/medical_analytics")
def medical_analytics(): return render_template("analytics.html")

@app.route("/prescription-scanner")
def prescription_scanner(): return render_template("prescription.html")
    
# =========================================================
# MAIN
# =========================================================

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    app.run(debug=True)