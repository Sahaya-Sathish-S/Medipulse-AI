import base64
import io
import re
from datetime import datetime, timedelta  
from flask import Flask, render_template, request, jsonify
import requests
from pypdf import PdfReader
from flask_cors import CORS
import smtplib 
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler
import os
from dotenv import load_dotenv

app = Flask(__name__)
CORS(app)

# Load configuration values securely from local file or cloud environment settings
load_dotenv()

# OPENROUTER SECURITY MANAGEMENT
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GMAIL_USER = "sahayasathish60@gmail.com"          
GMAIL_APP_PASSWORD = "kqqg dldi gyce jcdi" 

# Official Universal Free Tier Routing Identifier
FREE_MODEL_ROUTER = "openrouter/free"

# Background Scheduler Engine Setup
scheduler = BackgroundScheduler(daemon=True)
scheduler.start()


def send_email(to_email, subject, body):
    """
    Standard secure outbound automated SMTP mail delivery track.
    """
    try:
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Secure Connection Establishment (TLS Connection Framework)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())
        server.quit()
        print(f"--> [EMAIL SUCCESS] Automated dispatch sent to: {to_email}")
    except Exception as e:
        print(f"--> [EMAIL CRITICAL ERROR] Pipeline failed: {str(e)}")


def send_initial_reminder(data):
    """
    Task 1: Fires at the exact time matching the user's prescription schedule.
    """
    subject = f"⚠️ MediPulse Reminder: Time to take {data['medicine_name']}"
    body = (
        f"Hello {data['username']},\n\n"
        f"This is a notification that your scheduled dosage of {data['medicine_name']} "
        f"({data['dosage']}) is due now.\n\n"
        f"Please access your dashboard and mark it as taken immediately."
    )
    send_email(data['user_email'], subject, body)

    # Arm secondary safety escalation job for exactly 30 minutes later
    escalation_time = datetime.now() + timedelta(minutes=30)
    escalation_job_id = f"escalate_{data['medicine_id']}"

    if scheduler.get_job(escalation_job_id):
        scheduler.remove_job(escalation_job_id)

    # Core scheduling allocation routine
    scheduler.add_job(
        id=escalation_job_id,
        func=send_family_escalation,
        trigger='date',
        run_date=escalation_time,
        args=[data]
    )
    print(f"--> [ALERT ARMED] Family escalation track armed for 30 minutes from now.")


def send_family_escalation(data):
    """
    Task 2: Fires 30 minutes after the initial check if the user missed it.
    """
    subject = f"🚨 URGENT: MediPulse Missed Medication Alert for {data['username']}"
    body = (
        f"Attention {data['family_name']},\n\n"
        f"This is an automated safety alert from MediPulse Hub.\n\n"
        f"{data['username']} was scheduled to take their medication: {data['medicine_name']} "
        f"({data['dosage']}) 30 minutes ago.\n\n"
        f"They have not marked it as taken on their active dashboard tracking panel. "
        f"Please check in on them immediately."
    )
    send_email(data['family_email'], subject, body)
    print(f"--> [ESCALATED] Family alert dispatched for missed medicine ID: {data['medicine_id']}")

    
@app.route('/api/schedule_reminder', methods=['POST'])
def schedule_reminder():
    try:
        data = request.get_json()
        medicine_id = data.get("medicine_id")
        time_string = data.get("medicine_time") 

        if not medicine_id or not time_string:
            return jsonify({"status": "error", "message": "Missing necessary execution tokens"}), 400

        hour, minute = map(int, time_string.split(':')[:2])
        core_job_id = f"reminder_{medicine_id}"

        if scheduler.get_job(core_job_id):
            scheduler.remove_job(core_job_id)

        scheduler.add_job(
            id=core_job_id,
            func=send_initial_reminder,
            trigger='cron',
            hour=hour,
            minute=minute,
            args=[data]
        )

        print(f"--> [CRON SUCCESS] Daily tracking programmed for {time_string} (Med ID: {medicine_id})")
        return jsonify({"status": "success", "message": f"Daily track armed online for {time_string}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/medication_taken', methods=['POST'])
def medication_taken():
    data = request.get_json()
    medicine_id = data.get("medicine_id")
    escalation_job_id = f"escalate_{medicine_id}"

    if scheduler.get_job(escalation_job_id):
        scheduler.remove_job(escalation_job_id)
        print(f"--> [DEFUSED] Patient confirmed intake. Family alert canceled for Med ID: {medicine_id}")
        return jsonify({"status": "success", "message": "Emergency escalation disarmed safely."}), 200

    return jsonify({"status": "success", "message": "State confirmed, no open countdown found."}), 200

  
def extract_text_from_pdf(base64_data):
    """
    Decodes a base64 PDF stream and extracts raw structural text from pages.
    """
    try:
        if "," in base64_data:
            base64_data = base64_data.split(",")[1]
            
        pdf_bytes = base64.b64decode(base64_data)
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PdfReader(pdf_file)
        
        extracted_text = ""
        for page_num, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                extracted_text += f"\n--- Page {page_num + 1} ---\n{page_text}"
                
        return extracted_text.strip() if extracted_text else "Empty PDF Document Content."
    except Exception as e:
        print(f"Error reading PDF data payload: {e}")
        return f"[Error mining structural text from attached document: {str(e)}]"


# =========================================
# EMERGENCY BLOOD DONOR MAIL DISPATCH
# =========================================
@app.route('/api/send_emergency_email', methods=['POST'])
def api_send_emergency_email():
    try:
        data = request.get_json()
        
        to_email = data.get("to_email")
        donor_name = data.get("donor_name")
        blood_group = data.get("blood_group")
        
        if not to_email or not donor_name or not blood_group:
            return jsonify({"status": "error", "message": "Missing required dispatch parameters"}), 400

        subject = f"🚨 URGENT: Emergency Blood Donation Request ({blood_group})"
        body = (
            f"Dear {donor_name},\n\n"
            f"This is an urgent medical notification from the MediPulse Hospital network.\n\n"
            f"There is a critical demand for blood group type ({blood_group}) right now, "
            f"and your profile matches our immediate criteria.\n\n"
            f"If you are healthy, available, and willing to assist, please reach out to our "
            f"medical unit or review your active application dashboard as soon as possible.\n\n"
            f"Thank you for your life-saving support!\n\n"
            f"— MediPulse Emergency Hub Operations Team"
        )

        send_email(to_email, subject, body)
        return jsonify({"status": "success", "message": f"Alert successfully transmitted to {donor_name}"}), 200

    except Exception as e:
        print(f"--> [BLOOD MAIL SYSTEM ERROR]: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================================
# FLASK INTERFACE RENDER COMPONENT MAPPINGS
# =========================================
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


# =========================================
# AI EYE SCANNER ROUTE (AUTO-ROUTED FREE TIER)
# =========================================
@app.route("/analyze_eye_scan", methods=["POST"])
def analyze_eye_scan():
    try:
        data = request.get_json()
        file_content = data.get("file_content", "")  
        file_type = data.get("file_type", "image/jpeg")

        if not file_content:
            return jsonify({"reply": "Please capture or upload an eye scan image."}), 400

        base64_clean = file_content
        if "," in base64_clean:
            base64_clean = base64_clean.split(",")[1]

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:5000",  
            "X-Title": "MediPulse AI Hub"
        }

        prompt = """
        Analyze this image of a human eye for preliminary screening purposes.
        Evaluate visible structures (Cornea, Iris, Sclera, Pupil). Check for severe redness, cloudiness, or yellowing.
        Provide Observations, Indicators, Insights, and Recommended Next Steps. Include a clinical disclaimer.
        """

        messages = [
            {"role": "system", "content": "You are MediPulse Ophthalmology AI, an expert visual processing utility."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{file_type};base64,{base64_clean}"}}
                ]
            }
        ]

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={
                "model": FREE_MODEL_ROUTER, # Dynamically uses an active free-tier vision model
                "messages": messages,
                "max_tokens": 800  
            },
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({"reply": f"OpenRouter Free-Core Error Code: {response.status_code}"}), response.status_code

        ai_reply = response.json()["choices"][0]["message"]["content"]
        return jsonify({"reply": ai_reply})

    except Exception as e:
        return jsonify({"reply": f"Scanner Error: {str(e)}"}), 500


# =========================================
# AI CHAT ROUTE (AUTO-ROUTED FREE TIER)
# =========================================
@app.route("/chat", methods=["POST"])
def chat():
    try:
        payload = request.json
        user_message = payload.get("message", "").strip()
        chat_history = payload.get("history", [])

        messages = [
            {
                "role": "system",
                "content": (
                    "You are MediPulse AI, a smart healthcare assistant created by Sahaya Sathish S. "
                    "Provide short, professional, and clear answers. Support English, Tamil, and Hindi."
                )
            }
        ]

        for msg in chat_history:
            content_element = msg.get("content", "")
            if isinstance(content_element, list):
                content_element = str(content_element)
            messages.append({
                "role": msg["role"],
                "content": str(content_element)
            })

        messages.append({"role": "user", "content": user_message})

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5000",
                "X-Title": "MediPulse AI"
            },
            json={
                "model": FREE_MODEL_ROUTER, # Dynamically matches an open, active free-tier model
                "messages": messages,
                "max_tokens": 800  
            },
            timeout=30
        )
        
        if response.status_code != 200:
            return jsonify({"reply": f"OpenRouter System Line Anomaly: {response.status_code}. Route failure on zero-cost balancing pool."})

        result = response.json()
        ai_reply = result["choices"][0]["message"]["content"]
        return jsonify({"reply": ai_reply})
            
    except Exception as e:
        return jsonify({"reply": f"Core Network Exception Matrix: {str(e)}"})


# =========================================
# AI TITLE GENERATION ROUTE
# =========================================
@app.route("/generate_title", methods=["POST"])
def generate_title():
    user_message = request.json.get("message", "New Conversation")
    title_prompt = [
        {"role": "system", "content": "Generate a concise 2 to 4 word summary title. Return ONLY text."},
        {"role": "user", "content": user_message}
    ]
    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": FREE_MODEL_ROUTER, "messages": title_prompt, "max_tokens": 50}
        )
        generated_title = res.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        generated_title = "New Conversation"

    return jsonify({"title": generated_title})


# =========================================
# EMERGENCY MEDICINE AI ROUTE
# =========================================
@app.route("/medicine_ai", methods=["POST"])
def medicine_ai():
    try:
        data = request.get_json()
        user_message = data.get("message", "").strip()
        language = data.get("language", "english")
        medicine_database_text = data.get("medicine_database_text", "")

        if user_message == "":
            return jsonify({"reply": "Please enter symptoms"})

        prompt = f"Language: {language}\nDatabase:\n{medicine_database_text}\nSymptoms:\n{user_message}"

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5000",
                "X-Title": "MediPulse AI Hub"
            },
            json={
                "model": FREE_MODEL_ROUTER,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": "You are a professional clinical pharmacy assistant. Output strict structured definitions."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 600  
            },
            timeout=25
        )

        if response.status_code != 200:
            return jsonify({"reply": f"System Core Error (Status {response.status_code})."})

        ai_reply = response.json()["choices"][0]["message"]["content"]
        return jsonify({"reply": ai_reply})

    except Exception as e:
        return jsonify({"reply": f"Server Core Error: {str(e)}"})

        
# =========================================
# MAP NAVIGATION AI ROUTE
# =========================================
@app.route("/map_ai", methods=["POST"])
def map_ai():
    try:
        data = request.json
        user_message = data.get("message", "")
        prompt = f"Live Navigation Data Matrix Request. Directive context string: {user_message}"

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5000",  
                "X-Title": "MediPulse AI Hub"
            },
            json={
                "model": FREE_MODEL_ROUTER,
                "messages": [
                    {"role": "system", "content": "You are MediPulse Medical Navigation AI voice operator."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 400 
            },
            timeout=25
        )

        if response.status_code != 200:
            return jsonify({"reply": f"OpenRouter Core Navigation Error (Status {response.status_code})."})

        reply = response.json()["choices"][0]["message"]["content"]
        return jsonify({"reply": reply})

    except Exception as e:
        return jsonify({"reply": "Navigation service encountered an unexpected processing error."})


# =========================================
# BUSINESS ANALYTICS AI ROUTE
# =========================================
@app.route("/analyze_ai", methods=["POST"])
def analyze_ai():
    try:
        data = request.get_json()
        purchases = data.get("purchases", [])

        if len(purchases) == 0:
            return jsonify({"reply": "No purchase historical data records found."})

        prompt = f"Analyze metrics for analytics layer processing. Stream input elements: {str(purchases)}"

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5000",
                "X-Title": "MediPulse Analytics AI"
            },
            json={
                "model": FREE_MODEL_ROUTER,
                "messages": [
                    {"role": "system", "content": "You are a retail pharmacy operations logistics analyst."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 800,  
                "temperature": 0.3   
            },
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({"reply": f"OpenRouter Analytics Exception (Status {response.status_code})."})

        ai_reply = response.json()["choices"][0]["message"]["content"]
        return jsonify({"reply": ai_reply})

    except Exception as e:
        return jsonify({"reply": f"Analytics Server Pipeline Error: {str(e)}"})


# =========================================
# PRESCRIPTION AI ROUTE
# =========================================
@app.route("/prescription_ai", methods=["POST"])
def prescription_ai():
    try:
        data = request.get_json()
        file_content = data.get("file_content", "")
        file_type = data.get("file_type", "")

        if not file_content:
            return jsonify({"reply": "Please upload a valid prescription item payload."})

        user_content = [{"type": "text", "text": "Parse the medical documentation structural entries systematically."}]

        if "image" in file_type:
            base64_clean = file_content.split(",")[1] if "," in file_content else file_content
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{file_type};base64,{base64_clean}"}
            })
        elif file_type == "application/pdf":
            user_content.append({"type": "text", "text": f"PDF context extraction stream: {extract_text_from_pdf(file_content)}"})
        else:
            return jsonify({"reply": "Unsupported file layout target data map."})

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5000",
                "X-Title": "MediPulse Prescription AI"
            },
            json={
                "model": FREE_MODEL_ROUTER,
                "messages": [
                    {"role": "system", "content": "You are MediPulse Prescription Parsing Assistant."},
                    {"role": "user", "content": user_content}
                ],
                "max_tokens": 800, 
                "temperature": 0.2
            },
            timeout=35
        )

        if response.status_code != 200:
            return jsonify({"reply": f"OpenRouter Script Blocker Pipeline Error: {response.status_code}"})

        ai_reply = response.json()["choices"][0]["message"]["content"]
        return jsonify({"reply": ai_reply})

    except Exception as e:
        return jsonify({"reply": f"Prescription Service Failure Vector: {str(e)}"})


if __name__ == "__main__":
    app.run(debug=True)
