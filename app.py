import base64
import io
import re
import os
from datetime import datetime, timedelta  
from flask import Flask, render_template, request, jsonify
import requests
from pypdf import PdfReader
from flask_cors import CORS
import smtplib 
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

app = Flask(__name__)
CORS(app)

# Load environment configuration securely
load_dotenv()

# =========================================
# NATIVE GEMINI & EMAIL GATEWAY SECURE CONFIG
# =========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    GEMINI_API_KEY = GEMINI_API_KEY.strip().replace('"', '').replace("'", "")

# Centralized Google Gemini API Gateway Endpoint
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

GMAIL_USER = "sahayasathish60@gmail.com"          
GMAIL_APP_PASSWORD = "kqqg dldi gyce jcdi" 

# Background Scheduler Initialize
scheduler = BackgroundScheduler(daemon=True)
scheduler.start()


# =========================================
# CORE SMTP EMAIL PIPELINE (PORT 587 TLS)
# =========================================
def send_email(to_email, subject, body):
    """
    Uses Secure Connection Establishment over Port 587 (TLS) 
    for cross-platform cloud hosting reliability.
    """
    try:
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())
        server.quit()
        print(f"--> [EMAIL SUCCESS] Automated dispatch sent to: {to_email}")
    except Exception as e:
        print(f"--> [EMAIL CRITICAL ERROR] Pipeline failed: {str(e)}")


# =========================================
# MEDICINE ALERTS & ESCALATION SCHEDULER
# =========================================
def send_initial_reminder(data):
    """Fires at the exact time matching the user's prescription schedule."""
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

    scheduler.add_job(
        id=escalation_job_id,
        func=send_family_escalation,
        trigger='date',
        run_date=escalation_time,
        args=[data]
    )
    print(f"--> [ALERT ARMED] Family escalation track armed for 30 minutes from now.")


def send_family_escalation(data):
    """Fires 30 minutes after the initial check if the user missed it."""
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
# UTILITY HELPER: PDF DATA EXTRACTOR
# =========================================
def extract_text_from_pdf(base64_data):
    """Decodes a base64 PDF stream and extracts raw structural text from pages."""
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
# 1. AI EYE SCANNER ROUTE (GEMINI NATIVE)
# =========================================
@app.route("/analyze_eye_scan", methods=["POST"])
def analyze_eye_scan():
    try:
        data = request.get_json()
        file_content = data.get("file_content", "")  
        file_type = data.get("file_type", "image/jpeg")

        if not file_content:
            return jsonify({"reply": "Please capture or upload an eye scan image."}), 400

        base64_clean = file_content.split(",")[1] if "," in file_content else file_content

        prompt = """
        You are MediPulse Ophthalmology AI, an expert clinical screening assistant capable of performing structural visual analysis on eye photographs.
        Analyze this image of a human eye for preliminary screening purposes.
        
        Tasks:
        1. Evaluate visible structures (Cornea, Iris, Sclera, Pupil).
        2. Check for obvious anomalies: severe redness (conjunctivitis), cloudiness (cataracts), yellowing (jaundice), or abnormalities in pupil shape.
        3. Provide a structured, easy-to-read summary.
        
        STRICT RESPONSE FORMAT:
        - Preliminary Observations: [What is visible in this specific photo]
        - Potential Indicators: [Normal / Detected signs of specific issues, or write "None detected visually"]
        - Educational Insights: [Brief explanation of what those signs typically mean]
        - Recommended Next Steps: [e.g., Visit an Optometrist/Ophthalmologist for physical testing]
        
        CRITICAL SAFETY WARNING: Always append a clear legal disclaimer stating that this AI tool does not replace a professional clinical diagnosis or an automated refraction exam.
        """

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": file_type,
                            "data": base64_clean
                        }
                    }
                ]
            }]
        }

        response = requests.post(GEMINI_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=30)

        if response.status_code != 200:
            return jsonify({"reply": f"Gemini API returned error code: {response.status_code}"}), response.status_code

        ai_reply = response.json()["contents"][0]["parts"][0]["text"] if "contents" in response.json() else response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": ai_reply})
    except Exception as e:
        return jsonify({"reply": f"Scanner Error: {str(e)}"}), 500


# ==============================================================
# 2. MULTI-MODAL AI CHAT ROUTE (GEMINI NATIVE)
# ==============================================================
@app.route("/chat", methods=["POST"])
def chat():
    try:
        payload = request.json
        user_message = payload.get("message", "").strip()
        chat_history = payload.get("history", [])
        file_content = payload.get("file_content", "")
        file_type = payload.get("file_type", "image/jpeg")

        system_context = (
            "You are MediPulse AI, a smart healthcare assistant. Provide clean, short, conversational responses. "
            "Support English, Tamil, and Hindi. You can see images and document contents attached by the user. "
            "You are made by Sahaya Sathish S, an aspiring Computer Science Engineering "
            "Student who is passionate about coding and actively participates in symposiums and technical events to develop his skills. "
            "He is also developing some projects like Busy AI (a smart business assistant that creates smart professional business "
            "posters, reels, logos, websites, business analytics, and a chatbot to get business advice and improve), EcoSort AI "
            "(a smart device and software to maintain and keep the environment clean by a smart IoT powered dustbin which scans waste items "
            "and directs them to the right compartment, monitors the fill level, addresses the dustbin when filled, alerts waste collectors, "
            "and uses sensors/webcams to find the type of waste), and CodeForge AI (an AI powered smart gaming platform so that students can "
            "learn coding through quiz games, programming battles, and debugging events). He has won prizes in various technical events."
        )

        gemini_contents = []
        
        # Hydrate text history context safely
        if chat_history:
            for msg in chat_history:
                role = "user" if msg.get("role") == "user" else "model"
                content_element = msg.get("content", "")
                if isinstance(content_element, list):
                    content_element = str(content_element)
                
                gemini_contents.append({
                    "role": role,
                    "parts": [{"text": str(content_element)}]
                })

        current_user_parts = []
        
        # Image attachment evaluation block
        if file_content and "image" in file_type:
            base64_clean = file_content.split(",")[1] if "," in file_content else file_content
            current_user_parts.append({
                "inlineData": {
                    "mimeType": file_type,
                    "data": base64_clean
                }
            })
        # PDF document verification block
        elif file_content and file_type == "application/pdf":
            extracted_text = extract_text_from_pdf(file_content)
            user_message = f"[Attached PDF Document Content:\n{extracted_text}]\n\nUser Question: {user_message}"

        current_user_parts.append({"text": user_message if user_message else "Analyze this attachment."})

        gemini_contents.append({
            "role": "user",
            "parts": current_user_parts
        })

        payload = {
            "contents": gemini_contents,
            "systemInstruction": {
                "parts": [{"text": system_context}]
            }
        }

        response = requests.post(GEMINI_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        
        if response.status_code != 200:
            return jsonify({"reply": f"Gemini Chat Link Anomaly Code: {response.status_code}."})

        ai_reply = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": ai_reply})
    except Exception as e:
        print(f"--> [CHAT EXCEPTION RUNTIME ERROR]: {str(e)}")
        return jsonify({"reply": "I'm having trouble connecting to my brain right now. Please try again."})


# =========================================
# 3. AI TITLE GENERATION ROUTE (GEMINI NATIVE)
# =========================================
@app.route("/generate_title", methods=["POST"])
def generate_title():
    user_message = request.json.get("message", "New Conversation")
    
    payload = {
        "contents": [{
            "parts": [{"text": f"Generate a concise 2 to 4 word summary title for a conversation based on this initial user message: '{user_message}'. Respond with ONLY the title. No quotes, no markdown, no punctuation."}]
        }]
    }

    try:
        res = requests.post(GEMINI_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        generated_title = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        generated_title = "New Conversation"

    return jsonify({"title": generated_title})


# =========================================
# 4. EMERGENCY MEDICINE AI ROUTE (GEMINI NATIVE)
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

        prompt = f"""
        You are MediPulse Emergency Medicine AI, a professional pharmacy assistant.
        Available Language: {language}

        IMPORTANT RULES:
        1. Analyze symptoms carefully and recommend ONLY suitable OTC medicines.
        2. Medicine names must be written in ENGLISH characters.
        3. Keep answers concise, highly readable, and cleanly structured.
        4. If medicine exists in the database text block below, state its specific block number.
        5. If medicine is NOT available in the text blocks below, write exactly: "Medicine not available in blocks"
        6. If symptoms point to severe or life-threatening status, sound an urgent warning to visit a hospital immediately.

        STRICT RESPONSE FORMAT:
        Medicine Recommended: [Medicine Name]
        Dosage: [Short Dosage instruction]
        Available Block: [Block Number OR "Medicine not available in blocks"]
        Advice: [Short advice]
        Warning: [Short warning]

        AVAILABLE MEDICINE DATABASE:
        {medicine_database_text}

        USER SYMPTOMS:
        {user_message}
        """

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }

        response = requests.post(GEMINI_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=25)

        if response.status_code != 200:
            return jsonify({"reply": f"System Core Error (Status {response.status_code})."})

        ai_reply = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": ai_reply})
    except Exception as e:
        return jsonify({"reply": f"Server Core Error: {str(e)}"})


# =========================================
# 5. MAP NAVIGATION AI ROUTE (GEMINI NATIVE)
# =========================================
@app.route("/map_ai", methods=["POST"])
def map_ai():
    try:
        data = request.json
        user_message = data.get("message", "")
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        destination = data.get("destination", "")
        distance = data.get("distance", "")
        duration = data.get("duration", "")
        current_speed = data.get("speed", "")
        current_time = data.get("current_time", "")

        prompt = f"""
        You are MediPulse Live Navigation AI system.

        Live User Details:
        Current Latitude: {latitude} | Current Longitude: {longitude}
        Destination: {destination}
        Remaining Distance: {distance} | Estimated Arrival Time: {duration}
        Current Speed: {current_speed} KM/H | Current Time: {current_time}

        User Message: {user_message}

        Instructions:
        - Act like a professional live navigation AI assistant.
        - Give very clear road directions and explain routes simply.
        - Keep responses short and direct like a maps voice assistant.
        """

        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }

        response = requests.post(GEMINI_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=25)

        if response.status_code != 200:
            return jsonify({"reply": f"Gemini Navigation Error (Status {response.status_code})."})

        reply = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": "Navigation service encountered an unexpected error."})


# =========================================
# 6. BUSINESS ANALYTICS AI ROUTE (GEMINI NATIVE)
# =========================================
@app.route("/analyze_ai", methods=["POST"])
def analyze_ai():
    try:
        print("\n===== ANALYTICS AI START =====")

        data = request.get_json()
        purchases = data.get("purchases", [])

        # =====================================
        # VALIDATION
        # =====================================
        if len(purchases) == 0:
            return jsonify({
                "reply": "No purchase historical data records found inside dashboard telemetry panel."
            })

        # =====================================
        # LOCAL ANALYTICS CALCULATION
        # =====================================
        total_profit = 0
        total_quantity = 0
        medicine_counts = {}

        # LIMIT RECORDS TO PREVENT GEMINI OVERLOAD
        limited_purchases = purchases[:20]

        for item in limited_purchases:
            medicine_name = item.get("medicine_name", "Unknown")
            quantity = int(item.get("quantity", 0))
            total_price = float(item.get("total_price", 0))

            total_profit += total_price
            total_quantity += quantity

            if medicine_name in medicine_counts:
                medicine_counts[medicine_name] += quantity
            else:
                medicine_counts[medicine_name] = quantity

        # =====================================
        # FIND TOP SELLING MEDICINES
        # =====================================
        sorted_medicines = sorted(
            medicine_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        top_medicines = sorted_medicines[:5]

        top_medicine_text = ""
        for med, qty in top_medicines:
            top_medicine_text += f"- {med}: {qty} units sold\n"

        trending_medicine = top_medicines[0][0] if top_medicines else "Unknown"

        # =====================================
        # OPTIMIZED GEMINI PROMPT
        # =====================================
        prompt = f"""
        You are MediPulse Pharmacy Business Analytics AI.

        Analyze this pharmacy sales summary and provide short business improvement advice.

        BUSINESS SUMMARY:
        - Total Profit: ₹{total_profit}
        - Total Medicines Sold: {total_quantity}
        - Top Performing Medicine: {trending_medicine}

        TOP SELLING MEDICINES:
        {top_medicine_text}

        TASKS:
        1. Evaluate current pharmacy performance.
        2. Identify weak areas or missed opportunities.
        3. Suggest marketing or stock improvement ideas.
        4. Keep response concise and professional.

        RESPONSE STYLE:
        - Use bullet points
        - Keep it short
        - Make it practical
        """

        # =====================================
        # GEMINI PAYLOAD
        # =====================================
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 500
            }
        }

        # =====================================
        # GEMINI API REQUEST
        # =====================================
        response = requests.post(
            GEMINI_URL,
            json=payload,
            headers={
                "Content-Type": "application/json"
            },
            timeout=30
        )

        print("\n===== GEMINI RESPONSE =====")
        print(response.text)

        # =====================================
        # STATUS CHECK
        # =====================================
        if response.status_code != 200:
            return jsonify({
                "reply": f"Gemini Analytics Failure (Status {response.status_code})."
            })

        result = response.json()

        # =====================================
        # SAFE RESPONSE EXTRACTION
        # =====================================
        if (
            "candidates" in result and
            len(result["candidates"]) > 0 and
            "content" in result["candidates"][0] and
            "parts" in result["candidates"][0]["content"] and
            len(result["candidates"][0]["content"]["parts"]) > 0
        ):

            ai_reply = result["candidates"][0]["content"]["parts"][0]["text"]

        else:
            print("\n===== INVALID GEMINI RESPONSE =====")
            print(result)

            ai_reply = (
                "Analytics AI could not generate a valid response. "
                "The response structure returned from Gemini was incomplete."
            )

        print("\n===== ANALYTICS AI SUCCESS =====")

        return jsonify({
            "reply": ai_reply
        })

    except Exception as e:
        print(f"\n===== ANALYTICS AI SYSTEM CRASH =====")
        print(str(e))

        return jsonify({
            "reply": f"Analytics Server Pipeline Error: {str(e)}"
        })
# =========================================
# 7. AI PRESCRIPTION ROUTE (GEMINI NATIVE)
# =========================================
@app.route("/prescription_ai", methods=["POST"])
def prescription_ai():
    try:
        data = request.get_json()
        file_content = data.get("file_content", "")
        file_name = data.get("file_name", "")
        file_type = data.get("file_type", "")

        if not file_content:
            return jsonify({"reply": "Please upload a valid prescription image or PDF document."})

        # =========================================
        # IMAGE HANDLING
        # =========================================
        if "image" in file_type:
            base64_clean = file_content.split(",")[1] if "," in file_content else file_content
            mime_match = re.search(r"data:([^;]+);base64,", file_content)
            final_mime = mime_match.group(1) if mime_match else file_type

            prompt = """
            Analyze this medical prescription image carefully.
            
            Tasks:
            - Read doctor handwriting systematically.
            - Extract medicine names, precise dosage, and timing frequencies.
            - Detail clear target instructions or medical purposes.

            STRICT RESPONSE FORMAT:
            Medicine Name: [Name]
            Dosage: [e.g., 500mg]
            Timing: [e.g., Once daily after meals]
            Purpose: [Brief use description]
            
            If any handwriting string remains unparseable, append: "Some prescription text was unclear."
            """

            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": final_mime,
                                "data": base64_clean
                            }
                        }
                    ]
                }]
            }

        # =========================================
        # PDF HANDLING
        # =========================================
        elif file_type == "application/pdf":
            extracted_pdf_text = extract_text_from_pdf(file_content)

            prompt = f"""
            You are MediPulse Prescription AI, an expert specialized in parsing clinical digital medical charts.
            This is an electronic prescription PDF document text stream. Extract medicine structures completely.

            Prescription Raw Text Content:
            {extracted_pdf_text}

            Provide clean bullet marks containing:
            - Medicine names
            - Dosage metrics
            - Intended timings
            - Special Instructions
            """

            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }
        else:
            return jsonify({"reply": "Unsupported file format upload target detected."})

        response = requests.post(GEMINI_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=35)

        if response.status_code != 200:
            return jsonify({"reply": f"Gemini API Transmission Blocked (Status {response.status_code})."})

        ai_reply = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": ai_reply})
    except Exception as e:
        return jsonify({"reply": f"Prescription Service System Defect: {str(e)}"})


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


if __name__ == "__main__":
    app.run(debug=True)
