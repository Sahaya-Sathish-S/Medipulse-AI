import base64
import io
import json
import os
import socket
import re
import subprocess
import tempfile
import shutil
from pathlib import Path

from PIL import Image, ImageFilter, ImageDraw, ImageFont
from gtts import gTTS
import imageio_ffmpeg
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

import requests
import urllib3.util.connection as urllib3_cn

from pypdf import PdfReader

import smtplib


from apscheduler.schedulers.background import BackgroundScheduler

from dotenv import load_dotenv

# =========================================================
# FORCE IPv4 FOR ALL OUTBOUND REQUESTS
# =========================================================
# Some hosts (Render/Railway free tiers, etc.) advertise IPv6 support in
# DNS resolution but don't actually have a working IPv6 route out of the
# container. Python/urllib3 tries IPv6 first by default, which then fails
# instantly with "Network is unreachable" (errno 101) before ever trying
# the IPv4 address that would have worked. Forcing IPv4-only here fixes
# that for every outbound request this app makes (Overpass, OpenRouter,
# Resend, etc.) - not just the Overpass proxy below.


def _allowed_gai_family():
    return socket.AF_INET


urllib3_cn.allowed_gai_family = _allowed_gai_family


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
# NEARBY SEARCH (Overpass / OpenStreetMap - free, no signup)
# =========================================================
# Back to free OSM/Overpass, since a signup-required provider isn't what
# was wanted. The real bug here (not a data gap): when the query was
# broadened to nwr + 8 tag filters + 30km radius with only a 9s server-side
# timeout, Overpass was very likely running out of time mid-query. When
# that happens, Overpass returns HTTP 200 with a "remark" field (e.g.
# "runtime error: Query timed out") and no/partial "elements" - which the
# old code silently treated as "genuinely found nothing," when it had
# actually failed to finish. Fixed here by:
#   1. Checking for "remark" and treating it as a real failure, not a
#      valid empty result.
#   2. A cheap, fast first pass (fewer tags, "node" only, short timeout)
#      that mirrors what worked originally.
#   3. A heavier "nwr" pass only as a fallback if the cheap pass is truly
#      empty (not timed out), to still catch way/relation-tagged places
#      without paying that cost on every single search.

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

OVERPASS_HEADERS = {
    "User-Agent": "MediPulseAI/1.0 (nearby medical facility search; contact: sahayasathish60@gmail.com)"
}


def run_overpass_query(query, timeout_seconds):
    """Try each mirror in turn. Returns (elements, errors).
    elements is None if every mirror failed or timed out server-side -
    that's the signal to try the next fallback step, not 'zero results'."""
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = requests.post(endpoint, data={"data": query}, headers=OVERPASS_HEADERS, timeout=timeout_seconds)
            resp.raise_for_status()
            result = resp.json()
            if result.get("remark"):
                # Overpass ran but hit an internal error/timeout - NOT a
                # valid "nothing found" result. Try the next mirror.
                errors.append(f"{endpoint} -> Overpass remark: {result['remark']}")
                continue
            return result.get("elements", []), errors
        except Exception as e:
            errors.append(f"{endpoint} -> {type(e).__name__}: {e}")
    return None, errors


@app.route("/nearby_search", methods=["GET"])
def nearby_search():
    try:
        place_type = request.args.get("type")
        raw_lat = request.args.get("lat")
        raw_lng = request.args.get("lng")

        if not raw_lat or not raw_lng:
            return jsonify({"error": "lat and lng query params are required"}), 400

        try:
            lat = float(raw_lat)
            lng = float(raw_lng)
        except ValueError:
            return jsonify({"error": f"lat/lng must be numeric"}), 400

        if place_type not in ("hospital", "pharmacy"):
            return jsonify({"error": "type must be 'hospital' or 'pharmacy'"}), 400

        # High-performance single-pass regex query
        def build_optimized_query(r):
            if place_type == "pharmacy":
                return f"""
                [out:json][timeout:20];
                (
                  node["amenity"~"pharmacy|chemist"](around:{r},{lat},{lng});
                  way["amenity"~"pharmacy|chemist"](around:{r},{lat},{lng});
                  node["healthcare"="pharmacy"](around:{r},{lat},{lng});
                );
                out center;
                """
            else:
                return f"""
                [out:json][timeout:20];
                (
                  node["amenity"~"hospital|clinic|doctors"](around:{r},{lat},{lng});
                  way["amenity"~"hospital|clinic|doctors"](around:{r},{lat},{lng});
                  node["healthcare"~"hospital|clinic|centre|doctor"](around:{r},{lat},{lng});
                  way["healthcare"~"hospital|clinic|centre|doctor"](around:{r},{lat},{lng});
                );
                out center;
                """

        headers = {
            "User-Agent": "MediPulseAI/1.0 (contact: sahayasathish60@gmail.com)"
        }

        radii_to_try = [15000, 35000]
        PER_MIRROR_TIMEOUT = 12

        # 1. Try Overpass API Mirrors
        for r in radii_to_try:
            query = build_optimized_query(r)
            for endpoint in OVERPASS_ENDPOINTS:
                try:
                    resp = requests.post(endpoint, data={"data": query}, headers=headers, timeout=PER_MIRROR_TIMEOUT)
                    if resp.status_code == 200:
                        result = resp.json()
                        elements = result.get("elements", [])
                        if elements:
                            result["_radius_used_meters"] = r
                            return jsonify(result)
                except Exception as e:
                    continue

        # 2. Fallback to OpenStreetMap Nominatim API if Overpass returns 0 results
        nominatim_query = "hospital" if place_type == "hospital" else "pharmacy"
        nom_url = f"https://nominatim.openstreetmap.org/search?format=json&q={nominatim_query}&viewbox={lng-0.35},{lat+0.35},{lng+0.35},{lat-0.35}&bounded=1&limit=25"
        
        nom_resp = requests.get(nom_url, headers=headers, timeout=10)
        if nom_resp.status_code == 200:
            nom_data = nom_resp.json()
            elements = []
            for item in nom_data:
                elements.append({
                    "id": item.get("place_id"),
                    "lat": float(item.get("lat")),
                    "lon": float(item.get("lon")),
                    "tags": {"name": item.get("display_name", "").split(",")[0]}
                })
            if elements:
                return jsonify({"elements": elements, "_radius_used_meters": 35000, "_source": "nominatim_fallback"})

        return jsonify({
            "elements": [],
            "_radius_used_meters": 35000,
            "_note": "No facilities found within search radius."
        })

    except Exception as e:
        return jsonify({"error": f"nearby_search crashed: {str(e)}"}), 500


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

GMAIL_USER = "sahayasathish60@gmail.com"
GMAIL_APP_PASSWORD = "kqqg dldi gyce jcdi"

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

# =========================================================
# EMAIL CONFIG (UPDATED FOR RENDER COMPATIBILITY VIA HTTP)
# =========================================================

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_URL = "https://api.resend.com/emails"
# Note: On Resend's free tier without a custom domain,
# your "From" email must be onboarding@resend.dev
EMAIL_FROM_ADDRESS = "onboarding@resend.dev"


# =========================================================
# FAKE MEDICINE DETECTOR
# =========================================================

FAKE_MEDICINE_SYSTEM_PROMPT = """
You are MediPulse Fake Medicine Detector AI, a pharmaceutical packaging
authenticity screening assistant. You are NOT a lab test and you know it —
your job is a careful visual screen, biased toward caution.

Analyze the uploaded image of a medicine strip, blister pack, bottle, or box.

STEP 1 — Extract, if visible:
- Medicine / brand name
- Manufacturer name
- Batch number
- Manufacturing date
- Expiry date
- Manufacturing license / registration number

STEP 2 — Check for common counterfeit packaging red flags:
- Blurry, smudged, pixelated, or low-resolution printing
- Spelling errors or inconsistent fonts/sizes in the drug or manufacturer name
- Missing batch number, mfg date, or expiry date
- Expiry date already passed (compare to today if a date is legible)
- Missing or implausible manufacturing license / registration number
- Poor-quality foil, seal, embossing, or packaging material
- Packaging design, color scheme, or logo that looks inconsistent or crudely
  reproduced

STEP 3 — Decide a verdict:
- "genuine"   -> no red flags, legible required details, consistent printing
- "suspicious"-> some missing/unclear details or minor inconsistencies
- "fake"      -> clear red flags (expired, missing critical details, obvious
                 print/spelling defects, implausible packaging)
- "unclear"   -> image too blurry/dark/cropped to make any real judgment

CRITICAL SAFETY RULE: when evidence is ambiguous, choose "suspicious" rather
than "genuine". A missed fake is far worse than an extra warning shown to a
genuine medicine. Never output "genuine" unless the packaging is clearly
legible AND shows no red flags.

Respond with STRICT JSON ONLY — no markdown, no code fences, no text outside
the JSON object — in exactly this shape:

{
  "verdict": "genuine" | "suspicious" | "fake" | "unclear",
  "confidence": <integer 0-100, your confidence in the verdict itself>,
  "medicine_name": "<string or empty>",
  "manufacturer": "<string or empty>",
  "batch_no": "<string or empty>",
  "mfg_date": "<string or empty>",
  "exp_date": "<string or empty>",
  "red_flags": ["<short phrase>", ...],
  "positive_signs": ["<short phrase>", ...],
  "summary": "<2-3 sentence plain-language explanation of the verdict>",
  "disclaimer": "<one sentence reminding the user this is a preliminary AI
                  screening, not a lab or pharmacist verification, and to
                  confirm suspicious/fake results before use>"
}

Only output the JSON object. Nothing else.
"""


@app.route("/api/detect_fake_medicine", methods=["POST"])
def detect_fake_medicine():

    try:

        data = request.get_json()

        file_content = data.get("file_content", "")
        file_type = data.get("file_type", "image/jpeg")

        if not file_content:
            return jsonify({
                "status": "error",
                "message": "Please upload a photo of the medicine packaging."
            })

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

        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": FAKE_MEDICINE_SYSTEM_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{file_type};base64,{base64_clean}"
                            }
                        }
                    ]
                }
            ],
            "temperature": 0.2,
            "max_tokens": 800
        }

        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=60
        )

        print("FAKE MEDICINE STATUS:", response.status_code)

        if response.status_code != 200:
            print(response.text)
            return jsonify({
                "status": "error",
                "message": f"Detection AI Error: {response.status_code}"
            })

        result = response.json()
        raw_reply = result["choices"][0]["message"]["content"]

        # strip accidental code fences, just in case
        cleaned = raw_reply.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
        except Exception:
            # graceful fallback so the UI never breaks on a malformed reply —
            # always err toward "suspicious", never silently pass a medicine
            parsed = {
                "verdict": "suspicious",
                "confidence": 40,
                "medicine_name": "",
                "manufacturer": "",
                "batch_no": "",
                "mfg_date": "",
                "exp_date": "",
                "red_flags": ["Could not fully parse the packaging analysis."],
                "positive_signs": [],
                "summary": "The image could not be fully analyzed. Please retake the photo in good lighting with the label fully visible, and verify this medicine manually before use.",
                "disclaimer": "This is an AI preliminary screening only, not a lab or pharmacist verification."
            }

        parsed["status"] = "success"

        return jsonify(parsed)

    except Exception as e:

        print("FAKE MEDICINE DETECTOR ERROR:", str(e))

        return jsonify({
            "status": "error",
            "message": f"Detection processing error: {str(e)}"
        })


# =========================================================
# EMAIL FUNCTION (BYPASSES SMTP BLOCKS ON CLOUD HOSTS)
# =========================================================

def send_email(to_email, subject, body):
    if not RESEND_API_KEY:
        print("EMAIL ERROR: RESEND_API_KEY missing in .env")
        return False

    try:
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "from": EMAIL_FROM_ADDRESS,
            "to": [to_email],
            "subject": subject,
            "text": body  # Sends clean plain text matching your setup
        }

        print(f"\n===== SENDING EMAIL VIA HTTP TO: {to_email} =====")
        response = requests.post(
            RESEND_URL,
            headers=headers,
            json=payload,
            timeout=15
        )

        print("EMAIL STATUS:", response.status_code)

        if response.status_code in [200, 201]:
            print("SUCCESS: Email delivered over HTTP successfully.")
            return True
        else:
            print("FAILED EMAIL REQUEST:", response.text)
            return False

    except Exception as e:
        print("EMAIL CRITICAL ERROR:", str(e))
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

@app.route("/api/medipulse_profile_deep_analyzer", methods=["POST"])
def medipulse_profile_deep_analyzer():
    try:
        data = request.get_json()
        # The JS frontend passes the object inside the 'profile' key
        profile = data.get("profile", {})

        if not profile:
            return jsonify({
                "status": "error",
                "reply": "⚠️ No profile dataset found to process. Please fill out your profile."
            })

        # Structured diagnostic prompt with strict structural rules
        prompt = f"""
You are the master clinical analyst for MediPulse AI, an advanced medical ecosystem.
Analyze the following patient profile metrics thoroughly and output an insightful, deeply supportive, clinical response that is EXACTLY around 400 words. Do not exceed or fall short significantly.

=== PATIENT COMPREHENSIVE DOSSIER ===
- Full Name: {profile.get('full_name', 'N/A')}
- Age / Gender: {profile.get('age', 'N/A')} years old | {profile.get('gender', 'N/A')}
- Blood Type: {profile.get('blood_group', 'N/A')}
- Current Profession: {profile.get('occupation', 'N/A')}
- Academic/Studies: {profile.get('studies', 'N/A')}
- Family Core Backdrop: Marital Status: {profile.get('marital_status', 'N/A')} | Family Occupation: {profile.get('family_occupation', 'N/A')}
- Reported Chronic/Acute Health Problems: {profile.get('health_problem', 'No acute problems specified.')}

=== EVALUATION OUTPUT PROTOCOL STRUCTURE ===
1. CLINICAL ASSESSMENT SUMMARY: Cross-examine age, occupation strain, and reported health complaints.
2. TAILORED ROOT-CAUSE STRATEGIES & LIFE PROTOCOLS: Actionable holistic, nutritional, ergonomic, or therapeutic guidance.
3. PREVENTATIVE CARE RISK PROFILE: Custom warnings mapped directly to their lifestyle metrics.

Maintain an empathetic, authoritative, and brilliantly sharp persona. Ensure the final text reads naturally as an integrated evaluation without code block schemas.
"""

        messages = [
            {
                "role": "system",
                "content": "You are a Chief Clinical Analyst and Medical UI Informatics Officer for MediPulse AI system."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        # Call your existing fallback model sequencer
        ai_reply = ask_ai(messages, temperature=0.5, max_tokens=1200)

        return jsonify({
            "status": "success",
            "reply": ai_reply
        })

    except Exception as e:  # Fixed syntax here from 'catch' to 'except'
        print("PROFILE ANALYSIS CRITICAL EXCEPTION ERROR:", str(e))
        return jsonify({
            "status": "error",
            "reply": f"An infrastructure anomaly occurred during diagnostic processing: {str(e)}"
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

You are made by Sahaya Sathish S he is an aspiring first year computer Science Engineering student studying in DMI Engineering College. He also has other inspiring projects like CodeForge AI(A student coding teacher), EcoSort AI(A Smart dustbin powered with AI), Busy AI(A business promoting agent with social media marketting). He is from Vadakkankulam, Tirunelveli, Tamil Nadu. Here you can assist with health related queries, navigate to the nearby hospital and the pharmacy shop, emergency medicine finder related to the problems or symptoms provided by the user, you can call the ambulance faster, you can remaind the user for taking medicine with email message sending,you can store and call the blood donators while emergency occurred and AI eye scanner for identifying the problem in the eye and you can save the complaints from the user regarding hospital management and solve with the help of human and analyze your profile also.
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
# MEDICINE AI — GUIDED SYMPTOM CHECK (3-5 QUESTIONS -> CONFIRM -> FINAL)
# =========================================================

def count_questions_asked(history):
    """Counts how many 'question' stage turns the AI has already asked."""
    count = 0
    for msg in history:
        if msg.get("role") == "assistant":
            try:
                parsed = json.loads(msg.get("content", ""))
                if parsed.get("stage") == "question":
                    count += 1
            except Exception:
                pass
    return count


def build_medicine_ai_system_prompt(question_count):

    return f"""
You are MediPulse Emergency Medicine Finder AI, a careful pharmacy/triage assistant.

Your job in this conversation:
1. The user describes a health problem or symptom.
2. Ask clarifying questions ONE AT A TIME to clearly understand the problem
   (duration, severity, other symptoms, age, allergies, existing conditions, etc).
   Ask a MINIMUM of 3 and a MAXIMUM of 5 questions total before moving on.
3. Once you have asked enough questions (at least 3, no more than 5) and understand
   the problem clearly, STOP asking questions. Instead, summarize the FULL problem
   in your own words and ask the user to confirm it is correct.
4. Once the user confirms, compile everything discussed into a final answer with
   suggested OTC medicine name(s), dosage, general advice, and safety warnings.
   Always include a disclaimer to see a doctor for anything serious or if symptoms
   persist or worsen.

You have already asked {question_count} clarifying question(s) so far in this conversation.

CRITICAL: Respond with STRICT JSON ONLY. No markdown, no code fences, no text outside
the JSON object. Use exactly one of these shapes:

Clarifying question:
{{"stage":"question","message":"<your single question>"}}

Confirmation step:
{{"stage":"confirm","message":"<summary of the problem you understood, ending by asking the user to confirm>"}}

Final answer (ONLY after the user has confirmed):
{{"stage":"final","message":"<short empathetic intro line>","medicines":[{{"name":"<medicine name>","dosage":"<dosage>","instructions":"<how/when to take>"}}],"advice":"<general advice>","warning":"<safety warning / when to seek emergency care>"}}

Rules:
- Never ask more than 5 questions total.
- Never skip the confirm stage before giving the final answer.
- If at any point the symptoms suggest a medical emergency (e.g. chest pain,
  difficulty breathing, severe bleeding, loss of consciousness, stroke signs),
  skip straight to stage "final" and urgently advise calling emergency services /
  going to the ER, with the warning field emphasizing urgency instead of OTC medicine.
- Keep each question short and directly useful for narrowing down the right medicine.
- Only output the JSON object. Nothing else.
"""


@app.route("/medicine_ai", methods=["POST"])
def medicine_ai():

    try:

        data = request.get_json()

        user_message = data.get("message", "")
        history = data.get("history", [])

        question_count = count_questions_asked(history)

        messages = [
            {
                "role": "system",
                "content": build_medicine_ai_system_prompt(question_count)
            }
        ]

        # carry forward prior turns (assistant turns are stored as JSON strings)
        for msg in history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })

        messages.append({
            "role": "user",
            "content": user_message
        })

        raw_reply = ask_ai(messages, temperature=0.3, max_tokens=700)

        # clean up in case the model wraps JSON in code fences
        cleaned = raw_reply.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
        except Exception:
            # graceful fallback so the chat doesn't break if the model
            # returns something that isn't valid JSON
            parsed = {
                "stage": "question",
                "message": raw_reply.strip() or "Could you tell me a bit more about your symptoms?"
            }

        return jsonify(parsed)

    except Exception as e:

        print("MEDICINE AI ERROR:", str(e))

        return jsonify({
            "stage": "question",
            "message": "Sorry, something went wrong. Could you describe your symptom again?"
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
You should not tell that you can't analyze this eye and all you should analyze the eye carefully and if any problem available then tell it in a positive way that it can be cured by this easily if problem available then don't hide tell it to the user immediately at last give a disclaimer that ai can make mistakes meet a optical doctor for an effective eye solution.
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
# COMPLAINT ANALYZER AI
# =========================================================

def analyze_complaint(complaint_data):

    prompt = f"""
Analyze this hospital complaint.

Problem:
{complaint_data}

Give response ONLY in JSON.

{{
  "category":"Billing/Doctor/Nurse/Staff/Cleanliness/Emergency/Medicine/Other",
  "priority":"Low/Medium/High/Critical",
  "solution":"Short admin suggestion"
}}

No explanation.
"""

    messages = [

        {
            "role":"system",
            "content":"You are a hospital complaint analyzer."
        },

        {
            "role":"user",
            "content":prompt
        }

    ]

    return ask_ai(messages)

# =========================================================
# COMPLAINT CHAT AI
# =========================================================
@app.route("/complaint_ai", methods=["POST"])
def complaint_ai():

    print("COMPLAINT AI HIT")

    try:

        data = request.get_json()

        print(data)

        history = data.get("history", [])

        messages = [
            {
                "role":"system",
                "content":"""You are MediPulse Complaint Assistant.

        Your job:

        Collect complaint details one by one.

        Ask:

        1. Hospital Name
        2. Problem
        3. Date
        4. Department
        5. People involved
        6. Evidence available
        7. Additional details

        Ask ONLY one question at a time.

        Reply in English no any other language should be used

        Keep trustful and supportive."""
            }
        ]

        messages.extend(history)

        reply = ask_ai(messages)

        print("AI REPLY:", reply)

        return jsonify({
            "reply": reply
        })

    except Exception as e:

        print("ERROR:", str(e))

        return jsonify({
            "reply": str(e)
        })
@app.route("/analyze_complaint_ai", methods=["POST"])
def analyze_complaint_ai():

    try:

        data = request.get_json()

        complaint_text = data.get("complaint", "")

        result = analyze_complaint(
            complaint_text
        )

        return jsonify({
            "reply": result
        })

    except Exception as e:

        return jsonify({
            "reply": str(e)
        })

# =========================================================
# AI MEDICAL VIDEO STUDIO
# =========================================================

VIDEO_OUTPUT_DIR = Path(
    app.static_folder
) / "generated_videos"

VIDEO_OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


def clean_ai_json(text):
    """
    Extract JSON from AI response even if
    the model adds markdown/code fences.
    """

    text = text.strip()

    text = re.sub(
        r"```json",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```",
        "",
        text
    )

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(
            "AI did not return valid JSON."
        )

    return json.loads(
        text[start:end + 1]
    )


def generate_medical_video_content(
    topic,
    language="English"
):

    prompt = f"""
You are MediPulse AI Medical Education
Content Generator.

Create a very short public medical awareness
video about:

{topic}

Language:
{language}

The video will be approximately 15-20 seconds.

Create exactly 5 scenes.

The content must be:
- Simple
- Educational
- Suitable for the general public
- Medically responsible
- Easy to understand
- Not a diagnosis
- Not a prescription
- Not personalized medical advice

For each scene provide:
1. title
2. narration
3. image_search

The total narration should be short enough
for approximately 15-20 seconds.

Use simple English.

IMPORTANT:
Return ONLY valid JSON.

Format:

{{
    "title": "Short video title",
    "scenes": [
        {{
            "title": "Scene title",
            "narration": "Short narration",
            "image_search": "medical image search keywords"
        }},
        {{
            "title": "Scene title",
            "narration": "Short narration",
            "image_search": "medical image search keywords"
        }},
        {{
            "title": "Scene title",
            "narration": "Short narration",
            "image_search": "medical image search keywords"
        }},
        {{
            "title": "Scene title",
            "narration": "Short narration",
            "image_search": "medical image search keywords"
        }},
        {{
            "title": "Scene title",
            "narration": "Short narration",
            "image_search": "medical image search keywords"
        }}
    ]
}}
"""

    response = ask_ai(
        [
            {
                "role": "system",
                "content":
                    "You are a safe medical education AI."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2,
        max_tokens=1000
    )

    return clean_ai_json(response)


# =========================================================
# WIKIMEDIA COMMONS IMAGE SEARCH
# =========================================================

# =========================================================
# WIKIMEDIA COMMONS IMAGE SEARCH
# =========================================================

def search_wikimedia_image(search_term):

    url = (
        "https://commons.wikimedia.org/w/api.php"
    )

    params = {

        "action": "query",

        "generator": "search",

        "gsrsearch":
            search_term,

        "gsrnamespace": 6,

        "gsrlimit": 5,

        "prop":
            "imageinfo",

        "iiprop":
            "url",

        "iiurlwidth":
            1000,

        "format":
            "json"

    }

    headers = {

        "User-Agent":
            "MediPulseAI/1.0 "
            "(educational medical video generator)"

    }

    try:

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=8
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:

        print(
            "WIKIMEDIA SEARCH ERROR:",
            str(e)
        )

        return None

    pages = (
        data
        .get("query", {})
        .get("pages", {})
    )

    candidates = []

    for page in pages.values():

        imageinfo = page.get(
            "imageinfo",
            []
        )

        if not imageinfo:
            continue

        info = imageinfo[0]

        image_url = (
            info.get("thumburl")
            or info.get("url")
        )

        if image_url:
            candidates.append(
                image_url
            )

    if not candidates:
        return None

    return candidates[0]

# =========================================================
# DOWNLOAD IMAGE - RENDER OPTIMIZED
# =========================================================

def download_image(
    image_url,
    output_path
):

    headers = {
        "User-Agent":
            "MediPulseAI/1.0 "
            "(educational medical video generator)"
    }

    response = requests.get(
        image_url,
        headers=headers,
        timeout=10,
        stream=True
    )

    response.raise_for_status()

    # Protect Render memory by limiting image size.
    max_bytes = 8 * 1024 * 1024

    content_length = response.headers.get(
        "Content-Length"
    )

    if content_length:

        try:

            if int(content_length) > max_bytes:

                raise ValueError(
                    "Image is too large."
                )

        except ValueError as e:

            if "too large" in str(e).lower():
                raise

    total = 0

    with open(
        output_path,
        "wb"
    ) as file:

        for chunk in response.iter_content(
            chunk_size=64 * 1024
        ):

            if not chunk:
                continue

            total += len(chunk)

            if total > max_bytes:

                raise ValueError(
                    "Downloaded image exceeded "
                    "8 MB limit."
                )

            file.write(chunk)

# =========================================================
# PREPARE LANDSCAPE IMAGE
# =========================================================

def prepare_landscape_image(
    input_path,
    output_path,
    width=1280,
    height=720
):

    try:

        with Image.open(input_path) as source:

            image = source.convert("RGB")

            target_ratio = (
                width / height
            )

            image_ratio = (
                image.width /
                image.height
            )

            if image_ratio > target_ratio:

                new_height = height

                new_width = int(
                    image.width *
                    height /
                    image.height
                )

            else:

                new_width = width

                new_height = int(
                    image.height *
                    width /
                    image.width
                )

            image = image.resize(
                (
                    new_width,
                    new_height
                ),
                Image.Resampling.LANCZOS
            )

            left = max(
                0,
                (
                    image.width -
                    width
                ) // 2
            )

            top = max(
                0,
                (
                    image.height -
                    height
                ) // 2
            )

            image = image.crop(
                (
                    left,
                    top,
                    left + width,
                    top + height
                )
            )

            image.save(
                output_path,
                "JPEG",
                quality=85,
                optimize=True
            )

    except Exception as e:

        raise RuntimeError(
            "Could not prepare medical image: "
            + str(e)
        )

# =========================================================
# ADD MEDICAL VIDEO TEXT
# =========================================================

def create_scene_image(
    image_path,
    output_path,
    scene_title,
    scene_number,
    total_scenes
):

    image = Image.open(
        image_path
    ).convert("RGB")

    image = image.resize(
        (1280,720),
        Image.Resampling.LANCZOS
    )


    # Dark bottom gradient

    overlay = Image.new(
        "RGBA",
        image.size,
        (0,0,0,0)
    )

    draw = ImageDraw.Draw(
        overlay
    )


    for y in range(450,720):

        alpha = int(
            190 *
            (y - 450) /
            270
        )

        draw.line(
            [(0,y),(1280,y)],
            fill=(0,0,0,alpha)
        )


    image = Image.alpha_composite(
        image.convert("RGBA"),
        overlay
    )


    draw = ImageDraw.Draw(
        image
    )


    try:

        font_large = ImageFont.truetype(
            "DejaVuSans-Bold.ttf",
            48
        )

        font_small = ImageFont.truetype(
            "DejaVuSans.ttf",
            22
        )

    except:

        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()


    # MediPulse label

    draw.text(
        (45,40),
        "MEDIPULSE AI",
        font=font_small,
        fill=(255,255,255,230)
    )


    # Scene counter

    counter = (
        f"{scene_number:02d} / "
        f"{total_scenes:02d}"
    )

    draw.text(
        (1130,40),
        counter,
        font=font_small,
        fill=(0,220,255,230)
    )


    # Title

    draw.text(
        (50,575),
        scene_title[:55],
        font=font_large,
        fill=(255,255,255,255)
    )


    # Bottom line

    draw.rectangle(
        (50,650,350,654),
        fill=(168,85,247,255)
    )


    image.convert(
        "RGB"
    ).save(
        output_path,
        "JPEG",
        quality=94
    )


# =========================================================
# CREATE VOICE
# =========================================================

# =========================================================
# CREATE ENGLISH VOICE
# =========================================================

def create_voice(
    narration,
    output_path
):

    narration = (
        narration or ""
    ).strip()

    if not narration:

        narration = (
            "This video provides "
            "general medical awareness "
            "and educational information."
        )

    # Keep narration short for the 15–20 second video.
    words = narration.split()

    if len(words) > 65:

        narration = " ".join(
            words[:65]
        )

    print(
        "Creating English narration..."
    )

    try:

        tts = gTTS(
            text=narration,
            lang="en",
            slow=False
        )

        tts.save(
            str(output_path)
        )

    except Exception as e:

        raise RuntimeError(
            "Voice generation failed: "
            + str(e)
        )

    if not output_path.exists():

        raise RuntimeError(
            "Voice file was not created."
        )
# =========================================================
# FFmpeg VIDEO CREATION - OPTIMIZED FOR RENDER
# =========================================================

def create_video(
    scene_images,
    audio_path,
    output_path,
    duration=18
):
    """
    Create a lightweight 16:9 medical educational video.

    Optimized for Render:
    - 1280x720 landscape
    - 15 FPS instead of 30 FPS
    - ultrafast H.264 encoding
    - lower memory usage
    - black fade transition between scenes
    - narration added separately
    - explicit FFmpeg timeout
    """

    if not scene_images:
        raise ValueError("No scene images were provided.")

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    scene_count = len(scene_images)

    # Safety limit
    scene_count = min(scene_count, 5)
    scene_images = scene_images[:scene_count]

    duration = int(duration)

    if duration not in (15, 18, 20):
        duration = 18

    scene_duration = float(duration) / scene_count

    # Short black transition
    transition_duration = 0.35

    # Make sure transition does not become longer
    # than the scene itself.
    transition_duration = min(
        transition_duration,
        max(0.10, scene_duration / 3)
    )

    with tempfile.TemporaryDirectory(
        prefix="medipulse_ffmpeg_"
    ) as temp_dir:

        temp_dir = Path(temp_dir)

        silent_video = (
            temp_dir /
            "silent.mp4"
        )

        # -------------------------------------------------
        # CREATE INPUTS
        # -------------------------------------------------

        command = [
            ffmpeg,
            "-y"
        ]

        for image_path in scene_images:

            command.extend([
                "-loop",
                "1",

                "-t",
                f"{scene_duration:.3f}",

                "-i",
                str(Path(image_path).resolve())
            ])

        # -------------------------------------------------
        # BUILD FILTER
        # -------------------------------------------------

        filter_parts = []

        for index in range(scene_count):

            filter_parts.append(
                f"[{index}:v]"
                "scale=1280:720:"
                "force_original_aspect_ratio=decrease,"
                "pad=1280:720:"
                "(ow-iw)/2:"
                "(oh-ih)/2:"
                "black,"
                "format=yuv420p,"
                "setsar=1"
                f"[v{index}]"
            )

        # First scene
        current_label = "v0"

        # Join scenes using fade-to-black transitions.
        #
        # Example:
        # Scene 1 -> black -> Scene 2 -> black -> Scene 3
        #

        for index in range(1, scene_count):

            output_label = f"x{index}"

            offset = (
                scene_duration * index
                - transition_duration
            )

            filter_parts.append(
                f"[{current_label}]"
                f"[v{index}]"
                "xfade="
                "transition=fadeblack:"
                f"duration={transition_duration:.3f}:"
                f"offset={offset:.3f}"
                f"[{output_label}]"
            )

            current_label = output_label

        filter_complex = ";".join(
            filter_parts
        )

        command.extend([
            "-filter_complex",
            filter_complex,

            "-map",
            f"[{current_label}]",

            "-an",

            "-r",
            "15",

            "-t",
            str(duration),

            "-c:v",
            "libx264",

            "-preset",
            "ultrafast",

            "-crf",
            "28",

            "-pix_fmt",
            "yuv420p",

            "-movflags",
            "+faststart",

            str(silent_video)
        ])

        print(
            "Starting optimized FFmpeg video creation..."
        )

        print(
            "Video:",
            "1280x720",
            "15 FPS",
            f"{duration} seconds"
        )

        try:

            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120
            )

        except subprocess.TimeoutExpired:

            raise RuntimeError(
                "FFmpeg video creation timed out. "
                "Render could not finish the video within "
                "the allowed processing time."
            )

        if result.returncode != 0:

            error_message = (
                result.stderr[-5000:]
                if result.stderr
                else "Unknown FFmpeg error."
            )

            print(
                "FFMPEG ERROR:",
                error_message
            )

            raise RuntimeError(
                "FFmpeg failed while creating the video:\n"
                + error_message
            )

        if not silent_video.exists():

            raise RuntimeError(
                "FFmpeg completed but the silent video "
                "file was not created."
            )

        # -------------------------------------------------
        # ADD NARRATION
        # -------------------------------------------------

        command_audio = [
            ffmpeg,

            "-y",

            "-i",
            str(silent_video),

            "-i",
            str(audio_path),

            "-map",
            "0:v:0",

            "-map",
            "1:a:0",

            "-t",
            str(duration),

            "-c:v",
            "copy",

            "-c:a",
            "aac",

            "-b:a",
            "96k",

            "-ar",
            "44100",

            "-ac",
            "2",

            "-shortest",

            "-movflags",
            "+faststart",

            str(output_path)
        ]

        print(
            "Adding narration to medical video..."
        )

        try:

            result_audio = subprocess.run(
                command_audio,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60
            )

        except subprocess.TimeoutExpired:

            raise RuntimeError(
                "FFmpeg timed out while adding narration."
            )

        if result_audio.returncode != 0:

            error_message = (
                result_audio.stderr[-5000:]
                if result_audio.stderr
                else "Unknown FFmpeg audio error."
            )

            print(
                "FFMPEG AUDIO ERROR:",
                error_message
            )

            raise RuntimeError(
                "FFmpeg failed while adding narration:\n"
                + error_message
            )

        if not output_path.exists():

            raise RuntimeError(
                "Final medical video was not created."
            )

        print(
            "Medical video successfully created:",
            output_path
        )


# =========================================================
# VIDEO GENERATION API
# =========================================================

@app.route(
    "/api/generate_medical_video",
    methods=["POST"]
)
def generate_medical_video():

    work_dir = None

    try:

        data = request.get_json(
            silent=True
        ) or {}

        topic = (
            data.get("topic") or ""
        ).strip()

        language = (
            data.get("language")
            or "English"
        )

        try:

            duration = int(
                data.get(
                    "duration",
                    18
                )
            )

        except Exception:

            duration = 18

        if not topic:

            return jsonify({
                "success": False,
                "error":
                    "Please enter a medical topic."
            }), 400

        if duration not in (
            15,
            18,
            20
        ):

            duration = 18

        print(
            "\n========================================"
        )

        print(
            "MEDICAL VIDEO TOPIC:",
            topic
        )

        print(
            "LANGUAGE:",
            language
        )

        print(
            "DURATION:",
            duration
        )

        print(
            "========================================"
        )

        # -----------------------------------------
        # STEP 1: AI CONTENT
        # -----------------------------------------

        print(
            "STEP 1: Generating AI medical content..."
        )

        content = (
            generate_medical_video_content(
                topic,
                language
            )
        )

        if not isinstance(
            content,
            dict
        ):

            raise ValueError(
                "AI returned an invalid content format."
            )

        title = content.get(
            "title",
            "Medical Awareness"
        )

        scenes = content.get(
            "scenes",
            []
        )

        if not isinstance(
            scenes,
            list
        ):

            scenes = []

        scenes = scenes[:5]

        if not scenes:

            raise ValueError(
                "AI did not generate any scenes."
            )

        print(
            "AI scenes generated:",
            len(scenes)
        )

        # -----------------------------------------
        # STEP 2: TEMP WORKSPACE
        # -----------------------------------------

        work_dir = Path(
            tempfile.mkdtemp(
                prefix="medipulse_video_"
            )
        )

        scene_images = []
        narration_parts = []

        # -----------------------------------------
        # STEP 3: IMAGES
        # -----------------------------------------

        print(
            "STEP 2: Preparing medical images..."
        )

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            if not isinstance(
                scene,
                dict
            ):

                scene = {}

            search_term = (
                scene.get(
                    "image_search"
                )
                or topic
            )

            raw_image = (
                work_dir /
                f"raw_{index}.jpg"
            )

            landscape_image = (
                work_dir /
                f"landscape_{index}.jpg"
            )

            final_scene = (
                work_dir /
                f"scene_{index}.jpg"
            )

            print(
                f"Scene {index}/{len(scenes)}:",
                search_term
            )

            image_url = None

            try:

                image_url = (
                    search_wikimedia_image(
                        search_term
                    )
                )

            except Exception as e:

                print(
                    "IMAGE SEARCH ERROR:",
                    str(e)
                )

            if image_url:

                try:

                    download_image(
                        image_url,
                        raw_image
                    )

                    prepare_landscape_image(
                        raw_image,
                        landscape_image
                    )

                except Exception as image_error:

                    print(
                        "IMAGE DOWNLOAD ERROR:",
                        image_error
                    )

                    image_url = None

            # -------------------------------------
            # FALLBACK IMAGE
            # -------------------------------------

            if not image_url:

                print(
                    "Using generated fallback image."
                )

                fallback = Image.new(
                    "RGB",
                    (1280, 720),
                    (10, 5, 25)
                )

                fallback.save(
                    landscape_image,
                    "JPEG",
                    quality=85
                )

            # -------------------------------------
            # TITLE OVERLAY
            # -------------------------------------

            create_scene_image(
                landscape_image,
                final_scene,
                scene.get(
                    "title",
                    f"Medical Scene {index}"
                ),
                index,
                len(scenes)
            )

            scene_images.append(
                final_scene
            )

            narration = (
                scene.get(
                    "narration",
                    ""
                ) or ""
            ).strip()

            if narration:

                narration_parts.append(
                    narration
                )

        # -----------------------------------------
        # STEP 4: NARRATION
        # -----------------------------------------

        print(
            "STEP 3: Preparing narration..."
        )

        narration = " ".join(
            narration_parts
        ).strip()

        if not narration:

            narration = (
                f"This short video explains "
                f"{topic} for general medical "
                f"awareness and education."
            )

        audio_path = (
            work_dir /
            "narration.mp3"
        )

        create_voice(
            narration,
            audio_path
        )

        # -----------------------------------------
        # STEP 5: OUTPUT
        # -----------------------------------------

        filename = (
            "medical_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S_%f"
            )
            + ".mp4"
        )

        output_path = (
            VIDEO_OUTPUT_DIR /
            filename
        )

        print(
            "STEP 4: Creating final video..."
        )

        create_video(
            scene_images,
            audio_path,
            output_path,
            duration
        )

        # -----------------------------------------
        # SUCCESS
        # -----------------------------------------

        video_url = (
            "/static/generated_videos/"
            + filename
        )

        print(
            "========================================"
        )

        print(
            "MEDICAL VIDEO COMPLETE"
        )

        print(
            "VIDEO:",
            video_url
        )

        print(
            "========================================"
        )

        return jsonify({

            "success": True,

            "title":
                title,

            "video_url":
                video_url

        })

    except Exception as e:

        print(
            "\n========================================"
        )

        print(
            "MEDICAL VIDEO ERROR:"
        )

        print(
            type(e).__name__,
            str(e)
        )

        print(
            "========================================"
        )

        return jsonify({

            "success": False,

            "error":
                "Video generation failed: "
                + str(e)

        }), 500

    finally:

        if work_dir:

            try:

                shutil.rmtree(
                    work_dir,
                    ignore_errors=True
                )

            except Exception:

                pass

# =========================================================
# PAGE ROUTES
# =========================================================

@app.route("/medical-video")
def medical_video():
    return render_template("medical_video.html")

@app.route("/")
def loading(): return render_template("loading.html")

@app.route("/complaint")
def complaint():
    return render_template("complaint.html")

@app.route("/complaints_admin")
def complaints_admin():
    return render_template("view.html")

@app.route("/home")
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

@app.route("/fake-medicine")
def fake_medicine(): return render_template("fake_medicine.html")

@app.route("/view")
def view(): return render_template("view.html")

@app.route("/profile_analyzer_dashboard")
def profile_analyzer_dashboard():
    return render_template("analyzer.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # 0.0.0.0 (not 127.0.0.1) so Render/Railway's proxy can actually reach
    # this process, and the platform-assigned PORT rather than a hardcoded
    # 5000. debug=False for production - the Werkzeug debugger is a remote
    # code execution risk if left reachable on a public URL.
    app.run(host="0.0.0.0", port=port, debug=False)
