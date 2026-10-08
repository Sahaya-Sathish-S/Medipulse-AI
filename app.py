# =========================
# PART 1 — IMPORTS + CONFIG
# =========================

import os
import re
import json
import time
import uuid
import shutil
import base64
import threading
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect

load_dotenv()

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates")
)

# =========================
# API KEYS
# =========================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
FREE_AI_API_KEY = os.getenv("FREE_AI_API_KEY", "")

# =========================
# OPENROUTER
# =========================

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MODELS = [
    "openai/gpt-4o-mini",
    "meta-llama/llama-3.1-8b-instruct",
    "qwen/qwen-2.5-7b-instruct",
    "microsoft/phi-3-mini-128k-instruct"
]

# Optional: OPENROUTER_MODELS="model1,model2" in .env overrides the list
_env_models = os.getenv("OPENROUTER_MODELS", "").strip()
if _env_models:
    MODELS = [m.strip() for m in _env_models.split(",") if m.strip()]

# =========================
# FREE.AI
# =========================

FREE_AI_BASE_URL = "https://api.free.ai"

FREE_AI_IMAGE_URL = "https://api.free.ai/v1/image/generate/"

FREE_AI_TTS_URL = "https://api.free.ai/v1/tts/"

# =========================
# OUTPUT DIRECTORY
# =========================

MEDICAL_VIDEO_OUTPUT_DIR = BASE_DIR / "static" / "medical_videos"

MEDICAL_VIDEO_OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

# =========================
# VIDEO SETTINGS
# =========================

# 960x540 keeps rendering fast on Render's free CPU.
# Set VIDEO_WIDTH=1280 and VIDEO_HEIGHT=720 in .env for HD (slower).
VIDEO_W = int(os.getenv("VIDEO_WIDTH", "960"))
VIDEO_H = int(os.getenv("VIDEO_HEIGHT", "540"))
VIDEO_FPS = 25

# =========================
# LANGUAGE CODES
# =========================

MEDICAL_VIDEO_LANG_CODES = {
    "english": "en",
    "tamil": "ta",
    "hindi": "hi",
    "malayalam": "ml",
    "telugu": "te",
    "kannada": "kn"
}

MEDICAL_VIDEO_LANG_NAMES = {
    "english": "English",
    "tamil": "Tamil",
    "hindi": "Hindi",
    "malayalam": "Malayalam",
    "telugu": "Telugu",
    "kannada": "Kannada"
}

# =========================
# BACKGROUND JOBS
# =========================
# Video generation takes 1-4 minutes. A normal web request would be
# killed by Render (about 30 seconds), so the work runs in a background
# thread and the browser polls /api/medical_video_status/<job_id>.
#
# NOTE: jobs are kept in memory, so run gunicorn with ONE worker:
#   gunicorn app:app --workers 1 --threads 4 --timeout 120

JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_MAX_AGE_SECONDS = 60 * 60


def job_update(job_id, **fields):

    with JOBS_LOCK:

        job = JOBS.get(job_id)

        if job is not None:
            job.update(fields)
            job["updated"] = time.time()


def cleanup_old_jobs():

    cutoff = time.time() - JOB_MAX_AGE_SECONDS

    with JOBS_LOCK:

        old = [
            key for key, value in JOBS.items()
            if value.get("created", 0) < cutoff
        ]

        for key in old:
            JOBS.pop(key, None)


# =========================
# PART 2 — AI HELPERS
# =========================

def clean_ai_json(text):
    """
    Cleans AI response and extracts JSON.
    """

    if not text:
        return None

    text = text.strip()

    # Remove markdown code blocks
    text = re.sub(
        r"^```(?:json)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```$",
        "",
        text
    )

    text = text.strip()

    # Direct JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # Find JSON object
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        possible_json = text[start:end + 1]

        try:
            return json.loads(possible_json)
        except Exception:
            pass

    return None


def ask_ai_json(prompt):
    """
    Sends prompt to OpenRouter and returns JSON.
    """

    if not OPENROUTER_API_KEY:
        raise Exception(
            "OPENROUTER_API_KEY missing in .env"
        )

    headers = {
        "Authorization":
            f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":
            "application/json"
    }

    last_error = ""

    for model in MODELS:

        try:

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content":
                            "Return ONLY valid JSON. "
                            "Do not use markdown."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.4
            }

            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=120
            )

            if response.status_code != 200:
                last_error = (
                    f"{model}: "
                    f"{response.status_code} "
                    f"{response.text[:500]}"
                )
                continue

            data = response.json()

            content = (
                data
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            result = clean_ai_json(content)

            if result:
                return result

            last_error = (
                f"{model}: AI did not return valid JSON"
            )

        except Exception as e:
            last_error = str(e)

    raise Exception(
        f"AI JSON generation failed: {last_error}"
    )


def get_free_ai_headers():

    if not FREE_AI_API_KEY:
        raise Exception(
            "FREE_AI_API_KEY missing in .env"
        )

    return {
        "Authorization":
            f"Bearer {FREE_AI_API_KEY}",
        "Content-Type":
            "application/json"
    }


def download_file(url, output_path):

    response = requests.get(
        url,
        timeout=120
    )

    if response.status_code != 200:
        raise Exception(
            f"Download failed: "
            f"{response.status_code}"
        )

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


def extract_media_url(data, list_key):
    """
    Finds the file URL in a Free.ai JSON response.
    list_key is "images" for pictures and "audio" for voice.
    """

    if not isinstance(data, dict):
        return None

    url = (
        data.get("output_url")
        or data.get("image_url")
        or data.get("audio_url")
        or data.get("url")
    )

    if url:
        return url

    block = data.get(list_key)

    if isinstance(block, list) and block:
        block = block[0]

    if isinstance(block, str):
        return block

    if isinstance(block, dict):
        return (
            block.get("url")
            or block.get("image_url")
            or block.get("audio_url")
            or block.get("output_url")
        )

    return None


# =========================
# PART 3 — SCENE SCRIPT
# =========================

def scene_count_for_duration(duration):

    try:
        duration = int(duration)
    except Exception:
        duration = 60

    # about 12 seconds per scene -> 60s = 5, 80s = 7, 100s = 8
    return max(4, min(8, round(duration / 12)))


def generate_medical_video_content(
    topic,
    language="english",
    duration=60
):
    """
    Asks the AI for a scene-by-scene script.
    Every scene has its own narration and its own visual.
    """

    language = (language or "english").lower()

    language_name = MEDICAL_VIDEO_LANG_NAMES.get(
        language,
        "English"
    )

    scene_total = scene_count_for_duration(duration)

    seconds_per_scene = max(
        8,
        round(int(duration or 60) / scene_total)
    )

    words_per_scene = int(seconds_per_scene * 2.2)

    prompt = f"""
You are writing a script for an educational medical video
that is presented by an AI doctor.

Topic: "{topic}"

Narration language: {language_name}

Create EXACTLY {scene_total} scenes.
Each scene lasts about {seconds_per_scene} seconds.

Scene rules:

- Scene 1: the doctor introduces the topic.
  visual_type must be "doctor".
- Scene {scene_total}: the doctor gives final advice and says
  to consult a real doctor. visual_type must be "doctor".
- All middle scenes: explain ONE key point each (for example
  one symptom, cause or prevention tip).
  visual_type must be "visual".

Return ONLY valid JSON in exactly this structure:

{{
    "title": "video title in English",
    "scenes": [
        {{
            "heading_en": "max 5 words, English",
            "narration": "what the doctor says, in {language_name}, about {words_per_scene} words",
            "visual_type": "doctor or visual",
            "image_prompt": "English description of ONE clear image that matches this scene"
        }}
    ]
}}

Image prompt rules:

- Describe a realistic, clean, educational medical illustration or photo.
- Show the idea directly (example: for excessive thirst show a
  person drinking a large glass of water; for glucose testing
  show a glucometer on a table).
- No text, no letters, no numbers, no watermark in the image.
- Nothing graphic, bloody or disturbing.
- For "doctor" scenes the image_prompt may be an empty string.

Narration rules:

- Educational purpose only.
- Simple, friendly language, as a doctor talking to a patient.
- Do NOT diagnose and do NOT give dangerous medical advice.
- Plain spoken text only: no emojis, no bullet points,
  no stage directions.
"""

    content = ask_ai_json(prompt)

    return normalize_scenes(
        content,
        topic,
        scene_total
    )


def normalize_scenes(content, topic, scene_total):
    """
    Makes sure the AI result is a clean list of scenes,
    even if the AI used the old script format.
    """

    if not isinstance(content, dict):
        raise Exception("AI script was not a JSON object.")

    title = str(
        content.get("title") or topic
    ).strip()

    raw_scenes = content.get("scenes")

    scenes = []

    if isinstance(raw_scenes, list):

        for item in raw_scenes:

            if not isinstance(item, dict):
                continue

            narration = str(
                item.get("narration") or ""
            ).strip()

            if not narration:
                continue

            scenes.append({
                "heading_en": str(
                    item.get("heading_en")
                    or item.get("heading")
                    or ""
                ).strip(),
                "narration": narration,
                "visual_type": str(
                    item.get("visual_type") or "visual"
                ).strip().lower(),
                "image_prompt": str(
                    item.get("image_prompt") or ""
                ).strip()
            })

    # Old format fallback: introduction / sections / conclusion
    if not scenes:

        intro = str(content.get("introduction") or "").strip()

        if intro:
            scenes.append({
                "heading_en": "Introduction",
                "narration": intro,
                "visual_type": "doctor",
                "image_prompt": ""
            })

        for section in content.get("sections") or []:

            if not isinstance(section, dict):
                continue

            text = str(
                section.get("explanation") or ""
            ).strip()

            if not text:
                continue

            heading = str(
                section.get("heading") or ""
            ).strip()

            scenes.append({
                "heading_en": heading,
                "narration": text,
                "visual_type": "visual",
                "image_prompt":
                    f"{topic}, {heading}, medical education"
            })

        outro = str(content.get("conclusion") or "").strip()

        if outro:
            scenes.append({
                "heading_en": "Final Advice",
                "narration": outro,
                "visual_type": "doctor",
                "image_prompt": ""
            })

    if not scenes:
        raise Exception(
            "AI did not return any scenes. Please try again."
        )

    scenes = scenes[:10]

    # first and last scene are always the doctor
    scenes[0]["visual_type"] = "doctor"

    if len(scenes) > 1:
        scenes[-1]["visual_type"] = "doctor"

    for index, scene in enumerate(scenes):

        if scene["visual_type"] != "doctor":
            scene["visual_type"] = "visual"

        if not scene["heading_en"]:
            scene["heading_en"] = (
                "Introduction" if index == 0
                else f"Key Point {index}"
            )

        if (
            scene["visual_type"] == "visual"
            and not scene["image_prompt"]
        ):
            scene["image_prompt"] = (
                f"{topic}, {scene['heading_en']}, "
                "medical education illustration"
            )

    return {
        "title": title,
        "scenes": scenes,
        "narration": " ".join(
            s["narration"] for s in scenes
        )
    }


def generate_medical_presenter(content):

    title = content.get(
        "title",
        "Medical Education"
    )

    prompt = f"""
Professional AI medical doctor presenter.

A realistic Indian medical doctor in a modern
hospital environment.

The doctor should be:

- Professional
- Friendly
- Clean white medical coat
- Natural face
- Looking directly at camera
- Upper body portrait
- Studio lighting
- Medical education presentation style
- No text
- No watermark
- No extra people

Video topic:

{title}
"""

    return free_ai_generate_image(
        prompt,
        768,
        1024,
        "doctor"
    )


def free_ai_generate_image(
    prompt,
    width,
    height,
    prefix,
    save_dir=None
):
    """
    Creates one picture with Free.ai and saves it as PNG.
    Returns the file path.
    """

    headers = get_free_ai_headers()

    save_dir = Path(save_dir or MEDICAL_VIDEO_OUTPUT_DIR)

    # first try with the size, then without it
    # (in case Free.ai rejects the size)
    attempts = [
        {
            "model": "sdxl",
            "prompt": prompt,
            "width": width,
            "height": height
        },
        {
            "model": "sdxl",
            "prompt": prompt
        }
    ]

    last_error = ""

    for payload in attempts:

        try:

            response = requests.post(
                FREE_AI_IMAGE_URL,
                headers=headers,
                json=payload,
                timeout=180
            )

            if response.status_code != 200:
                last_error = (
                    "Free.ai image generation failed: "
                    f"{response.status_code} "
                    f"{response.text[:500]}"
                )
                continue

            data = response.json()

            image_url = extract_media_url(data, "images")

            if not image_url:
                last_error = (
                    "Free.ai did not return an image URL. "
                    f"Response: {str(data)[:500]}"
                )
                continue

            image_path = save_dir / (
                f"{prefix}_{uuid.uuid4().hex}.png"
            )

            download_file(image_url, image_path)

            return str(image_path)

        except Exception as e:
            last_error = str(e)

    raise Exception(last_error or "Image generation failed.")


# =========================
# PART 4 — VOICE + IMAGES
# =========================

def generate_medical_voice(
    narration,
    language="english",
    save_dir=None
):

    language = (
        language or "english"
    ).lower()

    save_dir = Path(save_dir or MEDICAL_VIDEO_OUTPUT_DIR)

    # ---------------------------------
    # ENGLISH → FREE.AI KOKORO
    # (falls back to gTTS if Free.ai fails)
    # ---------------------------------

    if language == "english":

        try:

            payload = {
                "model": "kokoro",
                "voice": "af_heart",
                "text": narration
            }

            response = requests.post(
                FREE_AI_TTS_URL,
                headers=get_free_ai_headers(),
                json=payload,
                timeout=180
            )

            if response.status_code != 200:
                raise Exception(
                    "Free.ai TTS failed: "
                    f"{response.status_code} "
                    f"{response.text[:500]}"
                )

            audio_url = extract_media_url(
                response.json(),
                "audio"
            )

            if not audio_url:
                raise Exception(
                    "Free.ai did not return audio URL."
                )

            audio_path = save_dir / (
                f"voice_{uuid.uuid4().hex}.mp3"
            )

            download_file(audio_url, audio_path)

            return str(audio_path)

        except Exception as e:
            print(
                "Free.ai voice failed, using gTTS:",
                str(e)[:300]
            )

    # ---------------------------------
    # OTHER LANGUAGES → gTTS
    # ---------------------------------

    try:

        from gtts import gTTS

        lang_code = (
            MEDICAL_VIDEO_LANG_CODES
            .get(language, "en")
        )

        audio_path = save_dir / (
            f"voice_{uuid.uuid4().hex}.mp3"
        )

        tts = gTTS(
            text=narration,
            lang=lang_code,
            slow=False
        )

        tts.save(
            str(audio_path)
        )

        return str(audio_path)

    except Exception as e:

        raise Exception(
            f"Voice generation failed: {e}"
        )


# ---------------------------------
# PILLOW HELPERS (overlays + fallback)
# ---------------------------------

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "arialbd.ttf",
    "DejaVuSans-Bold.ttf"
]


def load_font(size):

    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def fit_text(draw, text, max_width, start_size, min_size=16):
    """
    Returns a font small enough for the text to fit in max_width,
    and the text (shortened with ... if still too long).
    """

    size = start_size

    while size >= min_size:

        font = load_font(size)

        if draw.textlength(text, font=font) <= max_width:
            return font, text

        size -= 2

    font = load_font(min_size)

    while (
        len(text) > 4
        and draw.textlength(text + "...", font=font) > max_width
    ):
        text = text[:-1]

    return font, text.rstrip() + "..."


def make_pip_png(doctor_path, output_path):
    """
    Round doctor face with a white ring (picture-in-picture).
    """

    from PIL import Image, ImageDraw

    size = int(VIDEO_H * 0.37)
    ring = 5

    doctor = Image.open(doctor_path).convert("RGB")

    w, h = doctor.size

    # face is in the upper-middle part of the portrait
    crop_size = int(min(w, h * 0.62, w * 0.62))
    left = (w - crop_size) // 2
    top = int(h * 0.02)

    face = doctor.crop(
        (left, top, left + crop_size, top + crop_size)
    ).resize((size - ring * 2, size - ring * 2), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    draw = ImageDraw.Draw(canvas)

    draw.ellipse(
        (0, 0, size - 1, size - 1),
        fill=(255, 255, 255, 255)
    )

    mask = Image.new("L", face.size, 0)

    ImageDraw.Draw(mask).ellipse(
        (0, 0, face.size[0] - 1, face.size[1] - 1),
        fill=255
    )

    canvas.paste(face, (ring, ring), mask)

    canvas.save(output_path)

    return size


def make_stage_png(doctor_path, output_path):
    """
    Full-frame doctor scene: blurred background + doctor in the centre.
    """

    from PIL import Image, ImageFilter, ImageEnhance

    doctor = Image.open(doctor_path).convert("RGB")

    w, h = doctor.size

    # blurred, darker background that fills the frame
    scale = max(VIDEO_W / w, VIDEO_H / h)

    bg = doctor.resize(
        (int(w * scale) + 1, int(h * scale) + 1),
        Image.LANCZOS
    )

    left = (bg.size[0] - VIDEO_W) // 2
    top = (bg.size[1] - VIDEO_H) // 2

    bg = bg.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    bg = bg.filter(ImageFilter.GaussianBlur(22))

    bg = ImageEnhance.Brightness(bg).enhance(0.55)

    # doctor fitted to the frame height
    fit = VIDEO_H / h

    fg = doctor.resize(
        (int(w * fit), VIDEO_H),
        Image.LANCZOS
    )

    bg.paste(fg, ((VIDEO_W - fg.size[0]) // 2, 0))

    bg.save(output_path)


def make_caption_png(heading, scene_number, scene_total, output_path):
    """
    Transparent overlay: lower-third heading + top brand tag.
    """

    from PIL import Image, ImageDraw

    overlay = Image.new(
        "RGBA",
        (VIDEO_W, VIDEO_H),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(overlay)

    margin = int(VIDEO_W * 0.03)

    # ---- brand tag (top left) ----
    tag_font = load_font(max(14, int(VIDEO_H * 0.032)))

    tag_text = "MediPulse AI  |  Educational video"

    tag_w = int(draw.textlength(tag_text, font=tag_font)) + 28
    tag_h = int(VIDEO_H * 0.068)

    draw.rounded_rectangle(
        (margin, margin, margin + tag_w, margin + tag_h),
        radius=tag_h // 2,
        fill=(8, 20, 40, 190)
    )

    draw.text(
        (margin + 14, margin + tag_h // 2),
        tag_text,
        font=tag_font,
        fill=(255, 255, 255, 255),
        anchor="lm"
    )

    # ---- lower third ----
    pip_space = int(VIDEO_H * 0.37) + margin * 2

    box_x0 = margin
    box_x1 = VIDEO_W - pip_space
    box_h = int(VIDEO_H * 0.17)
    box_y1 = VIDEO_H - margin
    box_y0 = box_y1 - box_h

    draw.rounded_rectangle(
        (box_x0, box_y0, box_x1, box_y1),
        radius=14,
        fill=(8, 20, 40, 205)
    )

    draw.rounded_rectangle(
        (box_x0, box_y0, box_x0 + 8, box_y1),
        radius=4,
        fill=(34, 211, 238, 255)
    )

    text_x = box_x0 + 26

    max_text_w = (box_x1 - text_x) - 16

    head_font, head_text = fit_text(
        draw,
        heading,
        max_text_w,
        int(VIDEO_H * 0.062),
        min_size=16
    )

    draw.text(
        (text_x, box_y0 + int(box_h * 0.40)),
        head_text,
        font=head_font,
        fill=(255, 255, 255, 255),
        anchor="lm"
    )

    small_font = load_font(max(12, int(VIDEO_H * 0.03)))

    draw.text(
        (text_x, box_y0 + int(box_h * 0.76)),
        f"Scene {scene_number} of {scene_total}",
        font=small_font,
        fill=(125, 211, 252, 255),
        anchor="lm"
    )

    overlay.save(output_path)


def make_fallback_card(heading, output_path):
    """
    Used when AI image generation fails, so the video never breaks.
    """

    from PIL import Image, ImageDraw

    card = Image.new("RGB", (VIDEO_W, VIDEO_H))

    draw = ImageDraw.Draw(card)

    # vertical gradient
    top = (13, 71, 161)
    bottom = (2, 136, 209)

    for y in range(VIDEO_H):

        ratio = y / max(1, VIDEO_H - 1)

        color = tuple(
            int(top[i] + (bottom[i] - top[i]) * ratio)
            for i in range(3)
        )

        draw.line((0, y, VIDEO_W, y), fill=color)

    # medical cross
    cx = VIDEO_W // 2
    cy = int(VIDEO_H * 0.42)
    arm = int(VIDEO_H * 0.20)
    thick = int(VIDEO_H * 0.07)

    draw.rounded_rectangle(
        (cx - thick, cy - arm, cx + thick, cy + arm),
        radius=thick // 2,
        fill=(255, 255, 255)
    )

    draw.rounded_rectangle(
        (cx - arm, cy - thick, cx + arm, cy + thick),
        radius=thick // 2,
        fill=(255, 255, 255)
    )

    card.save(output_path)


def generate_scene_image(scene, index, work_dir):
    """
    Relevant medical visual for one scene.
    Falls back to a clean card if Free.ai fails.
    """

    style = (
        ", realistic educational medical illustration, "
        "clean bright lighting, no text, no letters, "
        "no numbers, no watermark, no logo, "
        "nothing graphic or disturbing"
    )

    prompt = scene["image_prompt"] + style

    last_error = ""

    for attempt in range(2):

        try:

            return free_ai_generate_image(
                prompt,
                1024,
                576,
                f"scene{index + 1}",
                save_dir=work_dir
            )

        except Exception as e:

            last_error = str(e)

            time.sleep(1.5)

    print(
        f"Scene {index + 1}: image failed, using fallback card. "
        f"{last_error[:300]}"
    )

    fallback_path = Path(work_dir) / f"scene{index + 1}_fallback.png"

    make_fallback_card(scene["heading_en"], fallback_path)

    return str(fallback_path)


# =========================
# PART 5 — VIDEO RENDERING
# =========================

def get_ffmpeg():

    try:

        import imageio_ffmpeg

    except ImportError:

        raise Exception(
            "imageio-ffmpeg is missing. "
            "Run: pip install imageio-ffmpeg"
        )

    return imageio_ffmpeg.get_ffmpeg_exe()


def get_media_duration(ffmpeg_exe, path):
    """
    Reads the duration (seconds) of an audio file.
    """

    result = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True
    )

    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        result.stderr
    )

    if not match:
        raise Exception(
            "Could not read audio length: "
            + result.stderr[-500:]
        )

    hours, minutes, seconds = match.groups()

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )


def render_scene_clip(
    ffmpeg_exe,
    scene,
    scene_index,
    scene_total,
    image_path,
    audio_path,
    doctor_path,
    pip_path,
    pip_size,
    work_dir
):
    """
    One scene = relevant visual (slow pan) + doctor picture-in-picture
    + heading caption + animated voice bar + this scene's voice.
    """

    work_dir = Path(work_dir)

    audio_seconds = get_media_duration(ffmpeg_exe, audio_path)

    clip_seconds = audio_seconds + 0.5

    is_doctor_scene = scene["visual_type"] == "doctor"

    # ---- overlays ----
    caption_path = work_dir / f"caption_{scene_index + 1}.png"

    make_caption_png(
        scene["heading_en"],
        scene_index + 1,
        scene_total,
        caption_path
    )

    if is_doctor_scene:

        stage_path = work_dir / f"stage_{scene_index + 1}.png"

        make_stage_png(doctor_path, stage_path)

        background_path = stage_path

    else:

        background_path = image_path

    clip_path = work_dir / f"clip_{scene_index + 1:02d}.mp4"

    margin = int(VIDEO_W * 0.03)

    wave_w = pip_size
    wave_h = int(VIDEO_H * 0.08)

    pip_x = VIDEO_W - pip_size - margin
    wave_x = pip_x
    wave_y = VIDEO_H - margin - wave_h
    pip_y = wave_y - 10 - pip_size

    # ---- background filter ----
    if is_doctor_scene:

        bg_filter = (
            f"[0:v]scale={VIDEO_W}:{VIDEO_H},setsar=1,"
            f"fps={VIDEO_FPS}[bg];"
        )

    else:

        big_w = int(VIDEO_W * 1.12) // 2 * 2
        big_h = int(VIDEO_H * 1.12) // 2 * 2

        bg_filter = (
            f"[0:v]scale={big_w}:{big_h}"
            f":force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H}"
            f":x='(iw-{VIDEO_W})*t/{clip_seconds:.2f}'"
            f":y='(ih-{VIDEO_H})/2',"
            f"setsar=1,fps={VIDEO_FPS}[bg];"
        )

    # ---- overlays filter ----
    filter_parts = [
        bg_filter,
        "[bg][2:v]overlay=0:0[v1];"
    ]

    if is_doctor_scene:

        filter_parts.append("[v1]null[v2];")

    else:

        filter_parts.append(
            f"[v1][1:v]overlay={pip_x}:{pip_y}[v2];"
        )

    filter_parts.extend([
        "[3:a]apad=pad_dur=0.5,asplit=2[a1][a2];",
        f"[a2]showwaves=s={wave_w}x{wave_h}:mode=cline"
        f":colors=0x22d3ee:rate={VIDEO_FPS},"
        "format=rgba,colorkey=0x000000:0.2:0.0[w];",
        f"[v2][w]overlay={wave_x}:{wave_y},format=yuv420p[v]"
    ])

    filter_complex = "".join(filter_parts)

    command = [
        ffmpeg_exe, "-y",

        "-loop", "1", "-framerate", str(VIDEO_FPS),
        "-i", str(background_path),

        "-loop", "1", "-framerate", str(VIDEO_FPS),
        "-i", str(pip_path),

        "-loop", "1", "-framerate", str(VIDEO_FPS),
        "-i", str(caption_path),

        "-i", str(audio_path),

        "-filter_complex", filter_complex,

        "-map", "[v]",
        "-map", "[a1]",

        "-t", f"{clip_seconds:.2f}",

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),

        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",

        str(clip_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600
    )

    if result.returncode != 0 or not clip_path.exists():

        raise Exception(
            f"FFmpeg failed on scene {scene_index + 1}:\n"
            + result.stderr[-2000:]
        )

    return clip_path


def join_clips(ffmpeg_exe, clip_paths, output_path, work_dir):

    list_path = Path(work_dir) / "clips.txt"

    with open(list_path, "w", encoding="utf-8") as f:

        for clip in clip_paths:

            safe = str(Path(clip).resolve()).replace("\\", "/")

            f.write(f"file '{safe}'\n")

    command = [
        ffmpeg_exe, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600
    )

    if result.returncode != 0 or not Path(output_path).exists():

        raise Exception(
            "FFmpeg could not join the scenes:\n"
            + result.stderr[-2000:]
        )

    return str(output_path)


def build_medical_video(
    job_id,
    topic,
    language,
    duration
):
    """
    Full pipeline:

    topic -> scene script -> doctor -> (visual + voice per scene)
          -> one clip per scene -> final MP4
    """

    work_dir = MEDICAL_VIDEO_OUTPUT_DIR / f"job_{job_id}"

    work_dir.mkdir(parents=True, exist_ok=True)

    try:

        ffmpeg_exe = get_ffmpeg()

        # -----------------------------
        # STEP 1 — SCENE SCRIPT
        # -----------------------------

        job_update(
            job_id,
            progress=5,
            message="Writing scene-by-scene script..."
        )

        content = generate_medical_video_content(
            topic,
            language,
            duration
        )

        scenes = content["scenes"]

        scene_total = len(scenes)

        print(f"STEP 1: {scene_total} scenes created.")

        # -----------------------------
        # STEP 2 — AI DOCTOR
        # -----------------------------

        job_update(
            job_id,
            progress=12,
            message="Creating the AI doctor..."
        )

        doctor_path = generate_medical_presenter(content)

        pip_path = work_dir / "doctor_pip.png"

        pip_size = make_pip_png(doctor_path, pip_path)

        print(f"STEP 2: AI doctor created: {doctor_path}")

        # -----------------------------
        # STEP 3 — VISUAL + VOICE PER SCENE
        # -----------------------------

        job_update(
            job_id,
            progress=18,
            message="Generating medical visuals and voice..."
        )

        finished = {"count": 0}

        finished_lock = threading.Lock()

        def make_scene_assets(index):

            scene = scenes[index]

            if scene["visual_type"] == "visual":

                image_path = generate_scene_image(
                    scene,
                    index,
                    work_dir
                )

            else:

                image_path = None

            audio_path = generate_medical_voice(
                scene["narration"],
                language,
                save_dir=work_dir
            )

            with finished_lock:

                finished["count"] += 1

                done = finished["count"]

            job_update(
                job_id,
                progress=18 + int(42 * done / scene_total),
                message=(
                    f"Scene {done} of {scene_total} "
                    "visuals and voice ready..."
                )
            )

            return image_path, audio_path

        with ThreadPoolExecutor(max_workers=3) as pool:

            assets = list(
                pool.map(make_scene_assets, range(scene_total))
            )

        print("STEP 3: scene visuals + voices created.")

        # -----------------------------
        # STEP 4 — RENDER EACH SCENE
        # -----------------------------

        clips = []

        for index, scene in enumerate(scenes):

            job_update(
                job_id,
                progress=62 + int(30 * index / scene_total),
                message=(
                    f"Rendering scene {index + 1} "
                    f"of {scene_total}..."
                )
            )

            image_path, audio_path = assets[index]

            clips.append(
                render_scene_clip(
                    ffmpeg_exe,
                    scene,
                    index,
                    scene_total,
                    image_path,
                    audio_path,
                    doctor_path,
                    pip_path,
                    pip_size,
                    work_dir
                )
            )

        # -----------------------------
        # STEP 5 — JOIN ALL SCENES
        # -----------------------------

        job_update(
            job_id,
            progress=94,
            message="Combining scenes into the final video..."
        )

        video_filename = (
            f"medical_video_{uuid.uuid4().hex}.mp4"
        )

        video_path = MEDICAL_VIDEO_OUTPUT_DIR / video_filename

        join_clips(
            ffmpeg_exe,
            clips,
            video_path,
            work_dir
        )

        print(f"STEP 5: final video created: {video_path}")

        # first narration kept as a preview of the voice
        first_audio_name = Path(assets[0][1]).name

        preview_audio = (
            MEDICAL_VIDEO_OUTPUT_DIR / first_audio_name
        )

        try:
            shutil.copy(assets[0][1], preview_audio)
            audio_url = (
                "/static/medical_videos/" + first_audio_name
            )
        except Exception:
            audio_url = ""

        total_seconds = sum(
            get_media_duration(ffmpeg_exe, a[1]) + 0.5
            for a in assets
        )

        return {

            "success": True,

            "message":
                "Medical video generated successfully.",

            "title":
                content.get("title", topic),

            "script":
                content,

            "scene_count":
                scene_total,

            "duration_seconds":
                round(total_seconds),

            "video_url":
                "/static/medical_videos/" + video_filename,

            "presenter_url":
                "/static/medical_videos/"
                + Path(doctor_path).name,

            "audio_url":
                audio_url,

            "lip_sync":
                False,

            "model":
                "OpenRouter script + Free.ai SDXL scenes + "
                "Free.ai/gTTS voice + FFmpeg"
        }

    finally:

        # remove temporary scene files, keep only the final outputs
        shutil.rmtree(work_dir, ignore_errors=True)


def run_video_job(job_id, topic, language, duration):

    try:

        result = build_medical_video(
            job_id,
            topic,
            language,
            duration
        )

        job_update(
            job_id,
            status="done",
            progress=100,
            message="Your medical video is ready!",
            result=result
        )

    except Exception as e:

        print("\nMEDICAL VIDEO GENERATION ERROR")
        print(str(e))

        job_update(
            job_id,
            status="error",
            error=str(e)
        )


# =========================
# PART 6 — ROUTES
# =========================

@app.route("/")
def home():

    return redirect(
        "/medical-video"
    )


@app.route("/medical-video")
def medical_video_page():

    return render_template(
        "medical_video.html"
    )


@app.route(
    "/api/generate_medical_video",
    methods=["POST"]
)
def api_generate_medical_video():

    """
    Starts a background job and returns its job_id immediately.
    The page then polls /api/medical_video_status/<job_id>.
    """

    try:

        data = request.get_json(
            silent=True
        ) or {}

        topic = (
            data.get("topic")
            or data.get("content")
            or ""
        ).strip()

        language = (
            data.get("language")
            or "english"
        ).strip().lower()

        try:
            duration = int(data.get("duration") or 60)
        except Exception:
            duration = 60

        if not topic:

            return jsonify({
                "success": False,
                "error":
                    "Please enter a medical topic."
            }), 400

        if not OPENROUTER_API_KEY:

            return jsonify({
                "success": False,
                "error":
                    "OPENROUTER_API_KEY missing "
                    "in .env"
            }), 500

        if not FREE_AI_API_KEY:

            return jsonify({
                "success": False,
                "error":
                    "FREE_AI_API_KEY missing "
                    "in .env"
            }), 500

        cleanup_old_jobs()

        job_id = uuid.uuid4().hex

        with JOBS_LOCK:

            JOBS[job_id] = {
                "status": "running",
                "progress": 2,
                "message": "Starting...",
                "created": time.time(),
                "updated": time.time()
            }

        thread = threading.Thread(
            target=run_video_job,
            args=(job_id, topic, language, duration),
            daemon=True
        )

        thread.start()

        return jsonify({
            "success": True,
            "job_id": job_id,
            "status": "running"
        }), 202

    except Exception as e:

        print("\nMEDICAL VIDEO START ERROR")
        print(str(e))

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/medical_video_status/<job_id>")
def api_medical_video_status(job_id):

    with JOBS_LOCK:

        job = JOBS.get(job_id)

        job = dict(job) if job else None

    if not job:

        return jsonify({
            "status": "error",
            "error":
                "This video job was not found. The server may "
                "have restarted. Please generate the video again."
        }), 404

    return jsonify({
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "error": job.get("error"),
        "result": job.get("result")
    })


# =========================
# RUN SERVER
# =========================

if __name__ == "__main__":

    print(
        "\n================================"
    )

    print(
        "MediPulse AI Medical Video Studio"
    )

    print(
        "================================"
    )

    print(
        "Open:"
    )

    print(
        "http://127.0.0.1:5000/medical-video"
    )

    # use_reloader=False so background jobs are not killed on file save
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False
    )
