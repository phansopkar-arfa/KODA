import os
import json
import logging
from datetime import datetime
from cryptography.fernet import Fernet
from dotenv import load_dotenv, set_key

load_dotenv()

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "profiles")
PROFILE_PATH = os.path.join(PROFILE_DIR, "child_profile.enc")
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

logger = logging.getLogger(__name__)

def _get_or_create_key() -> bytes:
    """Reads PROFILE_ENCRYPTION_KEY from env; if empty, generates a new Fernet key, writes it back."""
    key = os.environ.get("PROFILE_ENCRYPTION_KEY", "")
    if not key:
        key = Fernet.generate_key().decode('utf-8')
        os.environ["PROFILE_ENCRYPTION_KEY"] = key
        try:
            set_key(ENV_PATH, "PROFILE_ENCRYPTION_KEY", key)
        except Exception as e:
            logger.error(f"Failed to save encryption key to .env: {e}")
    return key.encode('utf-8')

def encrypt_data(data: dict) -> bytes:
    """JSON serialize then Fernet encrypt."""
    key = _get_or_create_key()
    f = Fernet(key)
    json_data = json.dumps(data).encode('utf-8')
    return f.encrypt(json_data)

def decrypt_data(encrypted: bytes) -> dict:
    """Fernet decrypt then JSON parse."""
    key = _get_or_create_key()
    f = Fernet(key)
    decrypted_data = f.decrypt(encrypted)
    return json.loads(decrypted_data.decode('utf-8'))

import re

def _get_profile_path(device_id: str = "default") -> str:
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(device_id))
    if not safe_id:
        safe_id = "default"
    return os.path.join(PROFILE_DIR, f"profile_{safe_id}.enc")

def save_profile(profile: dict, device_id: str = "default") -> None:
    """Encrypts and saves to profiles/profile_{device_id}.enc."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    encrypted = encrypt_data(profile)
    path = _get_profile_path(device_id)
    with open(path, 'wb') as f:
        f.write(encrypted)
    logger.info(f"Profile saved successfully for device: {device_id}")

def load_profile(device_id: str = "default") -> dict | None:
    """Loads and decrypts the profile for device_id. Falls back to default if device file missing."""
    path = _get_profile_path(device_id)
    if not os.path.exists(path):
        default_path = os.path.join(PROFILE_DIR, "child_profile.enc")
        if os.path.exists(default_path):
            path = default_path
        else:
            return None
    try:
        with open(path, 'rb') as f:
            encrypted = f.read()
        return decrypt_data(encrypted)
    except Exception as e:
        logger.error(f"Failed to load profile for {device_id}: {e}")
        return None

def delete_profile(device_id: str = "default") -> bool:
    """Deletes the profile file for device_id."""
    path = _get_profile_path(device_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False

def has_profile(device_id: str = "default") -> bool:
    """Checks if a profile exists for device_id."""
    path = _get_profile_path(device_id)
    return os.path.exists(path) or os.path.exists(os.path.join(PROFILE_DIR, "child_profile.enc"))

def calculate_age(dob_str: str) -> int:
    """Calculates age from YYYY-MM-DD date string."""
    try:
        dob = datetime.strptime(dob_str, "%Y-%m-%d")
        today = datetime.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return age
    except Exception:
        return 0

def build_system_prompt(profile: dict) -> str:
    """Builds a personalized KODA system prompt."""
    personal = profile.get("personal", {})
    personality = profile.get("personality", {})
    health = profile.get("health_routine", {})

    name = personal.get("name", "Friend")
    dob = personal.get("date_of_birth")
    
    age = calculate_age(dob) if dob else "young"
    pronouns = personal.get("gender_pronouns", "they/them")

    traits = ", ".join(personality.get("traits", []))
    interests = ", ".join(personality.get("likes_interests", []))
    sibling_info = personality.get("sibling_info", "")
    neurodiversity = personality.get("neurodiversity", "")
    speech_goals = personality.get("speech_goals", "")
    pronunciation_focus = ", ".join(personality.get("pronunciation_focus", []))
    allergies = health.get("allergies_medical", "")
    routines = health.get("daily_routines", "")

    prompt_parts = []
    
    prompt_parts.append(f"Your name is KODA, a friendly and playful AI companion.")
    prompt_parts.append(f"You are talking to {name}, a {age}-year-old child ({pronouns}).\n")

    if any([traits, interests, sibling_info, neurodiversity]):
        prompt_parts.append("PERSONALITY CONTEXT:")
        if traits:
            prompt_parts.append(f"- {name} is {traits}")
        if interests:
            prompt_parts.append(f"- Loves: {interests}")
        if sibling_info:
            prompt_parts.append(f"- Sibling info: {sibling_info}")
        if neurodiversity:
            prompt_parts.append(f"- Neurodiversity: {neurodiversity}")
        prompt_parts.append("")

    if speech_goals or pronunciation_focus:
        prompt_parts.append("SPEECH THERAPY GOALS:")
        if pronunciation_focus:
            prompt_parts.append(f"- Focus areas: {pronunciation_focus}")
        if speech_goals:
            prompt_parts.append(f"- Goals: {speech_goals}")
        prompt_parts.append("- Gently encourage correct pronunciation when relevant, but never be pushy.\n")

    if allergies:
        prompt_parts.append("HEALTH AWARENESS:")
        prompt_parts.append(f"- Medical context: {allergies}")
        prompt_parts.append("- Never suggest foods or activities that conflict with known allergies or medical conditions.\n")

    if routines:
        prompt_parts.append("ROUTINE AWARENESS:")
        prompt_parts.append(f"- {routines}\n")

    prompt_parts.append("RULES:")
    prompt_parts.append("- MANDATORY FILLER WORD: ALWAYS begin your very first response sentence with a natural filler word or expression (e.g. 'Hmm...', 'Let's see...', 'Oh!', 'Aha!', 'Well...', 'Ooh!').")
    prompt_parts.append("- Speak in short, simple, enthusiastic sentences (max 2-3 sentences).")
    prompt_parts.append(f"- Use simple words appropriate for a {age}-year-old.")
    prompt_parts.append("- Be warm, encouraging, and playful.")
    prompt_parts.append("- Never say anything inappropriate.")
    prompt_parts.append(f"- If {name} seems upset, be comforting.")
    prompt_parts.append(f"- Reference {name}'s interests naturally in conversation.")

    return "\n".join(prompt_parts)
