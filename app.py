import base64
import io
import re
from datetime import datetime, timedelta  # <-- FIXED: Added missing imports
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

# OPENROUTER API KEY (Keep this safe!)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GMAIL_USER = "sahayasathish60@gmail.com"          
GMAIL_APP_PASSWORD = "kqqg dldi gyce jcdi" 

scheduler = BackgroundScheduler(daemon=True)
scheduler.start()

def send_email(to_email, subject, body):
    """
    FIXED: Added the missing SMTP core execution architecture
    """
    try:
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Secure Connection Establishment
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

# Keep the rest of your AI chat, PDF extractors, and routes untouched down below...
  
def extract_text_from_pdf(base64_data):
    """
    Decodes a base64 PDF stream and extracts raw structural text from pages.
    """
    try:
        # Strip away potential data URI headers if sent from frontend
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

        # Validation Check
        if not to_email or not donor_name or not blood_group:
            return jsonify({"status": "error", "message": "Missing required dispatch parameters"}), 400

        # Construct Contextual Alerts
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

        # OPTIMIZATION: Run SMTP task instantly in a background thread to prevent Render HTTP 30s timeouts
        scheduler.add_job(
            func=send_email,
            trigger='date',
            run_date=datetime.now(),
            args=[to_email, subject, body]
        )

        print(f"--> [BLOOD ALERT QUEUED] Offloaded dispatch job to background threads for {donor_name}")
        return jsonify({"status": "success", "message": f"Alert processing successfully initiated for {donor_name}"}), 200

    except Exception as e:
        print(f"--> [BLOOD MAIL SYSTEM ERROR]: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/")
def home():
    return render_template("home.html")

@app.route("/medicine-search")
def medicine_search():
    return render_template("ai_medicine.html")

@app.route("/checker")
def checker():
    return render_template("checker.html")

@app.route("/setup")
def setup():
    return render_template("setup.html")

@app.route("/hospital")
def hospital():
    return render_template("hospital.html")

@app.route("/donation")
def donation():
    return render_template("donation.html")

@app.route("/register")
def register():
    return render_template("register.html")

@app.route("/emergency")
def emergency():
    return render_template("Emergency.html")


@app.route("/emergency-medicine")
def emergency_medicine():
    return render_template("emergency_medicine_ai.html")

@app.route("/scanner")
def scanner():
    return render_template("scanner.html")


@app.route("/free-map")
def free_map():
    return render_template("medical_map.html")

@app.route("/chatbot")
def chatbot():
    return render_template("chatbot.html")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/signup")
def signup():
    return render_template("signup.html")

@app.route("/profile")
def profile():
    return render_template("profile.html")

@app.route("/medical_analytics")
def medical_analytics():
    return render_template("analytics.html")
    
# =========================================
# AI EYE SCANNER ROUTE (TOKEN BOUNDARY FIX)
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

        messages = [
            {
                "role": "system",
                "content": "You are MediPulse Ophthalmology AI, an expert clinical screening assistant capable of performing structural visual analysis on eye photographs."
            },
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
        ]

        # Payload delivery maps
        json_payload = {
            "model": "openai/gpt-4o-mini",
            "messages": messages,
            "max_tokens": 1000  # 💡 FIX: Drastically cuts cost to bypass Free Tier 402 restrictions!
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=json_payload,
            timeout=30
        )

        if response.status_code != 200:
            print(f"--> [OPENROUTER ERROR]: {response.text}")
            return jsonify({"reply": f"OpenRouter API returned error code: {response.status_code}"}), response.status_code

        result = response.json()
        ai_reply = result["choices"][0]["message"]["content"]
        
        return jsonify({"reply": ai_reply})

    except Exception as e:
        print(f"Scanner Crash Details: {e}")
        return jsonify({"reply": f"Scanner Error: {str(e)}"}), 500

# =========================================
# PRESCRIPTION SCANNER PAGE
# =========================================

@app.route("/prescription-scanner")
def prescription_scanner():
    return render_template("prescription.html")
# --- AI CHAT ROUTE WITH HISTORY CONTEXT & EXTRACTORS ---
@app.route("/chat", methods=["POST"])
def chat():
    payload = request.json
    user_message = payload.get("message", "").strip()
    chat_history = payload.get("history", [])
    file_content = payload.get("file_content", "")
    file_name = payload.get("file_name", "")
    file_type = payload.get("file_type", "")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    # Setup core custom system profile instructions
    messages = [
        {
            "role": "system",
            "content": (
                "You are MediPulse AI, a smart healthcare assistant. Provide clean, short, conversational responses. "
                "Support English, Tamil, and Hindi. You are made by Sahaya Sathish S, an aspiring Computer Science Engineering "
                "Student who is passionate about coding and actively participates in symposiums and technical events to develop his skills. "
                "He is also developing some projects like Busy AI (a smart business assistant that creates smart professional business "
                "posters, reels, logos, websites, business analytics, and a chatbot to get business advice and improve), EcoSort AI "
                "(a smart device and software to maintain and keep the environment clean by a smart IoT powered dustbin which scans waste items "
                "and directs them to the right compartment, monitors the fill level, addresses the dustbin when filled, alerts waste collectors, "
                "and uses sensors/webcams to find the type of waste), and CodeForge AI (an AI powered smart gaming platform so that students can "
                "learn coding through quiz games, programming battles, and debugging events). He has won prizes in various technical events."
            )
        }
    ]

        # Append historical conversational frames securely by converting any dictionary data objects into standard text
    for msg in chat_history:
        content_element = msg.get("content", "")
        
        # If history content was saved as a nested structure or list block, cast it cleanly to string format
        if isinstance(content_element, list):
            content_element = str(content_element)
            
        messages.append({
            "role": msg["role"],
            "content": str(content_element)
        })

    # Delivery request body data mappings
        # Delivery request body data mappings
    data = {
        "model": "openai/gpt-4o-mini",  
        "messages": messages,
        "max_tokens": 1000  # 💡 FIX: Caps output token volume to bypass Free Tier 402 sequence blockages!
    }
    # =====================================================================
    # ADD FROM HERE TO THE END OF THE ROUTE TO FIX THE CHAT SEQUENCE ERRORS
    # =====================================================================
    try:
        # Execute communication with OpenRouter API endpoints
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5000",
                "X-Title": "MediPulse AI"
            },
            json=data,
            timeout=30
        )
        
        # Safe status check on network pipeline
        if response.status_code != 200:
            print(f"--> [CHAT API ERROR]: STATUS {response.status_code} - {response.text}")
            return jsonify({"reply": f"OpenRouter Core Error: {response.status_code}. Please verify your key balances."})

        result = response.json()
        
        # Structural keys check validation before accessing indices
        if "choices" in result and len(result["choices"]) > 0 and "message" in result["choices"][0]:
            ai_reply = result["choices"][0]["message"]["content"]
        else:
            print(f"--> [UNEXPECTED PAYLOAD MAP STRUCTURE]: {result}")
            ai_reply = "I received an unparseable transmission structure sequence from the backend intelligence core."
            
    except Exception as e:
        print(f"--> [CRITICAL ROUTE EXCEPTION]: {str(e)}")
        ai_reply = "I'm having trouble connecting to my brain right now. Please try your message again."

    return jsonify({"reply": ai_reply})

    # =====================================================================
    # STOP REPLACING STEP 1 HERE
    # =====================================================================


# --- AI TITLE GENERATION ROUTE ---
@app.route("/generate_title", methods=["POST"])
def generate_title():
    user_message = request.json.get("message", "New Conversation")
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    title_prompt = [
        {"role": "system", "content": "Generate a concise 2 to 4 word summary title for a chat thread based on this initial user message or file event. Respond with ONLY the title. No quotes, no markdown, no punctuation."},
        {"role": "user", "content": user_message}
    ]

    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={"model": "openai/gpt-4o-mini", "messages": title_prompt}
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
        print("\n===== MEDICINE AI REQUEST START =====")
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

        print("\n===== SENDING TO OPENROUTER =====")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5000",
                "X-Title": "MediPulse AI Hub"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "temperature": 0.3, # Lowered for strict format consistency
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert emergency medicine recommendation assistant. You provide short, structured answers."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": 600  # 💡 FIX: Caps token generation to prevent Free Tier pipeline blocks
            },
            timeout=25
        )

        print(f"STATUS CODE: {response.status_code}")

        if response.status_code != 200:
            print(f"--> [OPENROUTER ERROR LOG]: {response.text}")
            return jsonify({"reply": f"System Core Error (Status {response.status_code}). Please try again."})

        result = response.json()

        if "choices" in result and len(result["choices"]) > 0:
            ai_reply = result["choices"][0]["message"]["content"]
        else:
            ai_reply = "Unable to process structural medicine parameters. Please attempt again."

        print("\n===== AI REPLY SUCCESS =====")
        return jsonify({"reply": ai_reply})

    except Exception as e:
        print(f"\n===== MEDICINE AI ERROR CRASH =====\n{str(e)}")
        return jsonify({"reply": f"Server Core Error: {str(e)}"})

        
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
        You are MediPulse Navigation AI.

        Live User Details:
        Current Latitude: {latitude}
        Current Longitude: {longitude}
        Destination: {destination}
        Remaining Distance: {distance}
        Estimated Arrival Time: {duration}
        Current Speed: {current_speed} KM/H
        Current Time: {current_time}

        User Message:
        {user_message}

        Instructions:
        - Act like a professional live navigation AI.
        - Give very clear road directions.
        - Explain routes simply.
        - Keep responses short and direct like a maps voice assistant.
        """

        # FIXED: Added required OpenRouter Referer and Title flags
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:5000",  
            "X-Title": "MediPulse AI Hub"
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You are MediPulse Medical Navigation AI."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 500 # Kept lightweight
            },
            timeout=25
        )

        # DEBUGGING PRINT: Check your terminal to see exactly what OpenRouter says!
        if response.status_code != 200:
            print(f"--> [MAP AI ROUTER ERROR] Status: {response.status_code} | Response: {response.text}")
            
            # Informative message based on common API structural faults
            if response.status_code == 402:
                return jsonify({"reply": "OpenRouter API limits reached or credit balance exhausted."})
            return jsonify({"reply": f"OpenRouter Core Error (Status {response.status_code}). Please check server logs."})

        result = response.json()
        
        if "choices" in result and len(result["choices"]) > 0:
            reply = result["choices"][0]["message"]["content"]
        else:
            reply = "I couldn't process the navigational payload structure successfully."

        return jsonify({"reply": reply})

    except Exception as e:
        print(f"--> [MAP AI CRITICAL FAILURE]: {str(e)}")
        return jsonify({"reply": "Navigation service encountered an unexpected error."})

# =========================================
# FIXED: BUSINESS ANALYTICS AI ROUTE
# =========================================
@app.route("/analyze_ai", methods=["POST"])
def analyze_ai():
    try:
        print("\n===== ANALYTICS AI START =====")
        data = request.get_json()
        purchases = data.get("purchases", [])

        print(f"\nTOTAL PURCHASE RECORDS: {len(purchases)}")

        if len(purchases) == 0:
            return jsonify({
                "reply": "No purchase historical data records found inside dashboard telemetry panel."
            })

        # =====================================
        # CALCULATE BUSINESS ANALYTICS
        # =====================================
        total_profit = 0
        total_quantity = 0
        medicine_counts = {}
        analytics_text = ""

        for item in purchases:
            customer_name = item.get("customer_name", "Unknown")
            medicine_name = item.get("medicine_name", "Unknown")
            quantity = int(item.get("quantity", 0))
            total_price = float(item.get("total_price", 0))
            purchase_time = item.get("purchase_time", "")

            total_profit += total_price
            total_quantity += quantity

            # MEDICINE COUNT TRACKING
            if medicine_name in medicine_counts:
                medicine_counts[medicine_name] += quantity
            else:
                medicine_counts[medicine_name] = quantity

            # BUILD ANALYTICS STRINGS DATA STRUCT
            analytics_text += f"\nCustomer: {customer_name} | Medicine: {medicine_name} | Qty: {quantity} | Price: ₹{total_price} | Time: {purchase_time}\n"

        # FIND TRENDING MEDICINE
        trending_medicine = "Unknown"
        max_sales = 0
        for medicine, qty in medicine_counts.items():
            if qty > max_sales:
                max_sales = qty
                trending_medicine = medicine

        # =====================================
        # GENERATE OPTIMIZED AI ENGINE PROMPT
        # =====================================
        prompt = f"""
        You are MediPulse Pharmacy Business Analytics AI.
        Analyze this medical shop metrics layout carefully to provide immediate optimization tactics.

        Business Dashboard Metrics:
        - Total Profit: ₹{total_profit}
        - Total Medicines Sold: {total_quantity}
        - Top Performing Variant: {trending_medicine}

        Raw Segment Data Stream:
        {analytics_text}

        Tasks:
        - Evaluate current store sales metrics performance.
        - Identify clear system vulnerabilities (e.g., low velocity items or missing complementary item combos).
        - Provide brief actionable marketing actions to increase next-quarter operations revenue.

        Rules:
        - Provide high-density professional feedback.
        - Use clean Markdown tables or highly compressed bullet matrices.
        - Keep responses concise and practical.
        """

        print("\n===== SENDING TO OPENROUTER =====")

        # FIXED: Added browser referrer variables and application title keys
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:5000",
            "X-Title": "MediPulse Analytics AI"
        }

        # Delivery mapping profile array
        json_payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional pharmacy operations analyst specialized in clinical supply chains and medical retail growth planning."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": 1000,  # 💡 FIX: Caps outbound token generation streams to stop server timeouts
            "temperature": 0.3   # Lower temperature increases analytical reliability
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=json_payload,
            timeout=30
        )

        print(f"OPENROUTER STATUS: {response.status_code}")

        # Safe status mapping validation checking
        if response.status_code != 200:
            print(f"--> [ANALYTICS API ERROR CODE]: {response.status_code} | Msg: {response.text}")
            return jsonify({"reply": f"OpenRouter Core Failed (Status {response.status_code}). Check your balance or key access."})

        result = response.json()

        # Structural map index fallback validation checks
        if "choices" in result and len(result["choices"]) > 0 and "message" in result["choices"][0]:
            ai_reply = result["choices"][0]["message"]["content"]
        else:
            print(f"\nINVALID RESPONSE MAP PAYLOAD: {result}")
            ai_reply = "Analytics parsing succeeded but target text generation arrays dropped during output assembly."

        print("\n===== AI ANALYTICS SUCCESS =====")
        return jsonify({"reply": ai_reply})

    except Exception as e:
        print(f"\n===== ANALYTICS AI SYSTEM CRASH =====\n{str(e)}")
        return jsonify({"reply": f"Analytics Server Pipeline Error: {str(e)}"})



# =========================================
# FIXED: PRESCRIPTION AI ROUTE
# =========================================

@app.route("/prescription_ai", methods=["POST"])
def prescription_ai():
    try:
        data = request.get_json()

        file_content = data.get("file_content", "")
        file_name = data.get("file_name", "")
        file_type = data.get("file_type", "")

        if not file_content:
            return jsonify({
                "reply": "Please upload a valid prescription image or PDF document."
            })

        # FIXED: Added required OpenRouter validation tracking headers
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:5000",
            "X-Title": "MediPulse Prescription AI"
        }

        user_content = []

        # =========================================
        # IMAGE HANDLING
        # =========================================
        if "image" in file_type:
            base64_clean = file_content
            if "," in base64_clean:
                base64_clean = base64_clean.split(",")[1]

            mime_match = re.search(r"data:([^;]+);base64,", file_content)
            final_mime = mime_match.group(1) if mime_match else file_type

            user_content.append({
                "type": "text",
                "text": """
                Analyze this medical prescription carefully.
                
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
            })

            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{final_mime};base64,{base64_clean}"
                }
            })

        # =========================================
        # PDF HANDLING
        # =========================================
        elif file_type == "application/pdf":
            extracted_pdf_text = extract_text_from_pdf(file_content)

            user_content.append({
                "type": "text",
                "text": f"""
                This is an electronic prescription PDF document. 
                Extract medicine structures completely.

                Prescription Raw Text Content:
                {extracted_pdf_text}

                Provide clean bullet marks containing:
                - Medicine names
                - Dosage metrics
                - Intended timings
                - Special Instructions
                """
            })
        else:
            return jsonify({"reply": "Unsupported file format upload target detected."})

        # Delivery payload construction
        json_payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": "You are MediPulse Prescription AI, an expert specialized in parsing clinical handwritten scripts and digital medical charts."
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "max_tokens": 1000, # 💡 FIX: Caps the image token limits to maintain reliable network processing profiles
            "temperature": 0.2
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=json_payload,
            timeout=35
        )

        # Checking status validity response pipeline
        if response.status_code != 200:
            print(f"--> [PRESCRIPTION ROUTE API ERROR]: Status {response.status_code} | Text: {response.text}")
            return jsonify({"reply": f"OpenRouter Transmission Blocked (Status {response.status_code}). Please verify api keys."})

        result = response.json()

        if "choices" in result and len(result["choices"]) > 0:
            ai_reply = result["choices"][0]["message"]["content"]
        else:
            ai_reply = "The data payload processed, but failed structural layout verification validation."

        return jsonify({"reply": ai_reply})

    except Exception as e:
        print(f"--> [PRESCRIPTION BACKEND BREAK]: {str(e)}")
        return jsonify({"reply": f"Prescription Service System Defect: {str(e)}"})


if __name__ == "__main__":
    app.run(debug=True)
