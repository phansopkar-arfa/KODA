import asyncio
import json
import logging
import time
import os
import re
import uuid
import struct
import io
import wave
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
from dotenv import load_dotenv

import profile_manager
import voice_biometric

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Keys & Endpoints
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
CARTESIA_KEY = os.getenv("CARTESIA_API_KEY", "")
PARAKEET_URL = os.getenv("PARAKEET_API_URL", "https://api.nvidia.com/v1/audio/parakeet")
PARAKEET_KEY = os.getenv("PARAKEET_API_KEY", "")
VOICE_THRESHOLD = float(os.getenv("VOICE_SIMILARITY_THRESHOLD", "0.65"))

if not GROQ_KEY or GROQ_KEY == "your_groq_api_key_here":
    logger.warning("⚠️ GROQ_API_KEY not set in .env file!")
if not CARTESIA_KEY or CARTESIA_KEY == "your_cartesia_api_key_here":
    logger.warning("⚠️ CARTESIA_API_KEY not set in .env file!")

app = FastAPI(title="KODA V2 - Zero Latency Voice Companion")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/registration", StaticFiles(directory="registration"), name="registration")

# Default system prompt with filler word trick
DEFAULT_SYSTEM_PROMPT = (
    "Your name is KODA, a friendly and playful AI companion for children.\n"
    "RULES:\n"
    "- MANDATORY FILLER WORD: ALWAYS begin your very first response sentence with a natural filler word or expression (e.g. 'Hmm...', 'Let's see...', 'Oh!', 'Aha!', 'Well...', 'Ooh!').\n"
    "- Speak in short, simple, enthusiastic sentences (max 2-3 sentences).\n"
    "- Be warm, encouraging, and playful. Use simple words a 5-year-old can understand.\n"
    "- Never say anything inappropriate. If the child seems upset, be comforting.\n"
)

conversation_memory = []


# =====================================================================
# Silero VAD Lightweight Detector
# =====================================================================
class SileroVAD:
    """Silero VAD Helper - Processes 16kHz PCM audio frames to detect speech vs silence."""
    def __init__(self):
        self.sample_rate = 16000
        self.threshold = 0.5
        self.model = None
        self._init_model()

    def _init_model(self):
        try:
            import torch
            model, _ = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', force_reload=False, trust_repo=True)
            self.model = model
            logger.info("✅ Silero VAD loaded via Torch Hub")
        except Exception as e:
            logger.warning(f"⚠️ Could not load Silero VAD via torch hub: {e}. Falling back to energy-based VAD.")
            self.model = None

    def is_speech(self, pcm_bytes: bytes) -> bool:
        """Returns True if speech is detected in pcm_bytes frame."""
        if not pcm_bytes:
            return False
        if self.model:
            try:
                import torch
                import numpy as np
                audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
                audio_float32 = audio_int16.astype(np.float32) / 32768.0
                
                # Silero VAD strictly requires 512 samples per chunk at 16kHz
                chunk_size = 512
                probs = []
                for i in range(0, len(audio_float32), chunk_size):
                    chunk = audio_float32[i:i + chunk_size]
                    if len(chunk) < chunk_size:
                        # Pad with zeros if chunk is too small
                        chunk = np.pad(chunk, (0, chunk_size - len(chunk)), 'constant')
                    tensor = torch.from_numpy(chunk).unsqueeze(0)  # Add batch dimension if needed, Silero typically expects 1D or 2D [1, seq_len]
                    prob = self.model(tensor, self.sample_rate).item()
                    probs.append(prob)
                
                if probs:
                    return max(probs) > self.threshold
                return False
            except Exception as e:
                logger.error(f"Silero VAD inference error: {e}")
        
        # Energy fallback
        import numpy as np
        audio = np.frombuffer(pcm_bytes, dtype=np.int16)
        rms = np.sqrt(np.mean(audio.astype(np.float32)**2))
        return rms > 300


vad_detector = SileroVAD()


# =====================================================================
# Groq Whisper SST Client (Replacing Parakeet due to gRPC complexity)
# =====================================================================
async def transcribe_speech(pcm_bytes: bytes) -> str:
    """Send audio to Groq Whisper for extremely fast speech-to-text transcription."""
    if not pcm_bytes:
        return ""
    
    # Create WAV in-memory
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_bytes)
    wav_bytes = wav_io.getvalue()

    headers = {"Authorization": f"Bearer {GROQ_KEY}"}
    files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
    data = {"model": "whisper-large-v3", "response_format": "json", "language": "en"}

    url = "https://api.groq.com/openai/v1/audio/transcriptions"

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, files=files, data=data, timeout=10.0)
            if res.status_code == 200:
                result = res.json()
                return result.get("text", "").strip()
            else:
                logger.error(f"Groq Whisper SST failed ({res.status_code}): {res.text}")
    except Exception as e:
        logger.error(f"Groq Whisper SST Request Error: {e}")
    
    return ""


# =====================================================================
# Cartesia Sonic TTS Client
# =====================================================================
async def fetch_cartesia_tts(text: str) -> bytes | None:
    """Fetch TTS audio from Cartesia Sonic API for a text chunk."""
    if not text.strip():
        return None

    url = "https://api.cartesia.ai/tts/bytes"
    headers = {
        "X-API-Key": CARTESIA_KEY,
        "Cartesia-Version": "2024-06-10",
        "Content-Type": "application/json",
    }
    payload = {
        "model_id": "sonic-3.5",
        "transcript": text,
        "voice": {
            "mode": "id",
            "id": "79a125e8-cd45-4c13-8a67-188112f4dd22"  # Friendly sonic voice
        },
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": 16000
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if res.status_code == 200:
                return res.content
            else:
                logger.error(f"Cartesia TTS Error ({res.status_code}): {res.text}")
    except Exception as e:
        logger.error(f"Cartesia TTS Exception: {e}")

    return None


# =====================================================================
# HTTP Routes
# =====================================================================
@app.get("/")
async def get_index():
    return FileResponse("static/index.html")

@app.get("/register")
async def get_register():
    return FileResponse("registration/index.html")

@app.get("/api/profile")
async def get_profile():
    profile = profile_manager.load_profile()
    if not profile:
        return JSONResponse({"exists": False}, status_code=404)
    safe_profile = {k: v for k, v in profile.items() if k != "voice_embedding"}
    safe_profile["exists"] = True
    safe_profile["has_voice"] = profile.get("voice_embedding") is not None
    return JSONResponse(safe_profile)

@app.post("/api/profile")
async def create_profile(request: Request):
    try:
        data = await request.json()
        profile = profile_manager.load_profile() or {}

        profile["id"] = profile.get("id", str(uuid.uuid4()))
        profile["created_at"] = profile.get("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        profile["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        personal = data.get("personal", {})
        profile["personal"] = {
            "name": personal.get("name", ""),
            "date_of_birth": personal.get("dob", ""),
            "gender_pronouns": personal.get("pronouns", "they/them"),
        }

        neuro_chips = data.get("neurodiversity", [])
        neuro_context = data.get("neuro_context", "")
        neuro_combined = ", ".join(neuro_chips)
        if neuro_context:
            neuro_combined += f". {neuro_context}" if neuro_combined else neuro_context

        profile["personality"] = {
            "traits": data.get("personality", []),
            "likes_interests": data.get("interests", []),
            "sibling_info": data.get("sibling_info", ""),
            "neurodiversity": neuro_combined,
            "speech_goals": data.get("speech_goals", ""),
            "pronunciation_focus": data.get("pronunciation", []),
            "additional_notes": data.get("notes", ""),
        }

        health = data.get("health", {})
        profile["health_routine"] = {
            "allergies_medical": health.get("allergies", ""),
            "daily_routines": health.get("routines", ""),
        }

        if "voice_embedding" not in profile:
            profile["voice_embedding"] = None

        profile_manager.save_profile(profile)
        logger.info(f"✅ Profile saved for: {profile['personal']['name']}")
        return JSONResponse({"status": "ok", "id": profile["id"]})

    except Exception as e:
        logger.error(f"Profile save error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/voice-enroll")
async def voice_enroll(request: Request):
    try:
        pcm_bytes = await request.body()
        if len(pcm_bytes) < 16000 * 2 * 1:
            return JSONResponse({"error": "Audio too short. Need at least 1 second."}, status_code=400)

        embedding = voice_biometric.create_embedding(pcm_bytes, sample_rate=16000)
        if not embedding:
            return JSONResponse({"error": "Could not create voice embedding."}, status_code=400)

        profile = profile_manager.load_profile()
        if not profile:
            return JSONResponse({"error": "No profile found."}, status_code=404)

        profile["voice_embedding"] = embedding
        profile_manager.save_profile(profile)
        return JSONResponse({"status": "ok", "embedding_size": len(embedding)})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.delete("/api/profile")
async def delete_profile_endpoint():
    deleted = profile_manager.delete_profile()
    return JSONResponse({"deleted": deleted})

@app.get("/api/has-profile")
async def has_profile():
    return JSONResponse({"exists": profile_manager.has_profile()})


# =====================================================================
# WebSocket Voice Pipeline (Silero VAD + Parakeet SST + Groq LLM + Cartesia TTS)
# =====================================================================
AUDIO_BUFFER_SIZE = 16000 * 2 * 3

class SessionState:
    def __init__(self):
        self.mode = "WAKING"
        self.last_active_time = time.time()
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.audio_buffer = bytearray()
        self.speech_buffer = bytearray()
        self.silence_chunks = 0

@app.websocket("/ws/conversation")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_state = SessionState()
    conversation_memory.clear()

    profile = profile_manager.load_profile()
    if profile:
        session_state.system_prompt = profile_manager.build_system_prompt(profile)

    await websocket.send_json({"type": "status", "state": "waking"})

    try:
        while True:
            msg = await websocket.receive()

            if "text" in msg:
                data = json.loads(msg["text"])
                msg_type = data.get("type")

                if msg_type == "start":
                    session_state = SessionState()
                    conversation_memory.clear()
                    if profile:
                        session_state.system_prompt = profile_manager.build_system_prompt(profile)
                    await websocket.send_json({"type": "status", "state": "waking"})

                elif msg_type == "stop":
                    await websocket.send_json({"type": "status", "state": "idle"})

                elif msg_type == "interrupt":
                    if session_state.mode == "ACTIVE":
                        await websocket.send_json({"type": "status", "state": "listening"})
                    else:
                        await websocket.send_json({"type": "status", "state": "waking"})

            elif "bytes" in msg:
                audio_bytes = msg["bytes"]
                session_state.audio_buffer.extend(audio_bytes)

                if len(session_state.audio_buffer) > AUDIO_BUFFER_SIZE:
                    excess = len(session_state.audio_buffer) - AUDIO_BUFFER_SIZE
                    del session_state.audio_buffer[:excess]

                # Run Silero VAD on audio chunk
                is_speech = vad_detector.is_speech(audio_bytes)

                if is_speech:
                    session_state.speech_buffer.extend(audio_bytes)
                    session_state.silence_chunks = 0
                else:
                    if len(session_state.speech_buffer) > 0:
                        session_state.silence_chunks += 1
                        session_state.speech_buffer.extend(audio_bytes)

                        # ~0.5s of silence triggers end of utterance
                        if session_state.silence_chunks >= 5 and len(session_state.speech_buffer) >= 16000 * 2 * 0.8:
                            utterance_audio = bytes(session_state.speech_buffer)
                            session_state.speech_buffer.clear()
                            session_state.silence_chunks = 0

                            # Process complete speech utterance
                            asyncio.create_task(process_speech_utterance(utterance_audio, websocket, session_state))

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")


async def verify_voice(session_state: SessionState) -> bool:
    profile = profile_manager.load_profile()
    if not profile or not profile.get("voice_embedding"):
        logger.warning("No voice profile found.")
        return False

    audio_bytes = bytes(session_state.audio_buffer)
    if len(audio_bytes) < 16000 * 2 * 0.5:
        logger.warning(f"Audio buffer too short for verification: {len(audio_bytes)} bytes")
        return False

    is_match, similarity = voice_biometric.verify_speaker(
        audio_bytes, profile["voice_embedding"], threshold=VOICE_THRESHOLD
    )
    logger.info(f"Voice verification similarity: {similarity:.2f} (Threshold: {VOICE_THRESHOLD}) -> Match: {is_match}")
    return is_match


async def process_speech_utterance(audio_bytes: bytes, client_ws: WebSocket, session_state: SessionState):
    stt_start = time.time()
    await client_ws.send_json({"type": "status", "state": "thinking"})

    # 1. Transcribe audio using Groq Whisper SST
    transcript = await transcribe_speech(audio_bytes)
    logger.info(f"Transcribed: '{transcript}'")
    if not transcript.strip():
        await client_ws.send_json({"type": "status", "state": "listening" if session_state.mode == "ACTIVE" else "waking"})
        return

    stt_ms = int((time.time() - stt_start) * 1000)
    await client_ws.send_json({"type": "transcript", "text": transcript, "is_final": True})
    await client_ws.send_json({"type": "latency", "stt_ms": stt_ms})

    # 2. Check wake word if WAKING
    if session_state.mode == "WAKING":
        wake_match = re.search(r"\b(hi koda|hi coda|koda|coda|corda)\b", transcript.lower())
        if wake_match:
            logger.info("Wake word detected! Verifying voice...")
            is_verified = await verify_voice(session_state)
            if is_verified:
                logger.info("Voice verified! Activating Koda.")
                session_state.mode = "ACTIVE"
                session_state.last_active_time = time.time()
                await client_ws.send_json({"type": "status", "state": "listening"})

                remainder = transcript[wake_match.end():].strip()
                if len(remainder) > 2:
                    conversation_memory.append({"role": "child", "text": remainder})
                    await client_ws.send_json({"type": "chat", "role": "child", "text": remainder})
                    await run_groq_llm_and_cartesia_tts(remainder, client_ws, stt_ms, session_state)
            else:
                logger.warning("Voice NOT verified. Ignoring wake word.")
                await client_ws.send_json({"type": "status", "state": "unrecognized"})
        else:
            logger.info("No wake word found in waking mode.")
    else:
        session_state.last_active_time = time.time()
        conversation_memory.append({"role": "child", "text": transcript})
        await client_ws.send_json({"type": "chat", "role": "child", "text": transcript})
        await run_groq_llm_and_cartesia_tts(transcript, client_ws, stt_ms, session_state)


# =====================================================================
# Groq LLM (Llama 3 8B) + Cartesia Sonic TTS + Filler Word Latency Masking
# =====================================================================
async def run_groq_llm_and_cartesia_tts(text: str, client_ws: WebSocket, stt_ms: int, session_state: SessionState):
    llm_start = time.time()
    await client_ws.send_json({"type": "status", "state": "thinking"})

    # Prepare chat history for Groq API
    messages = [{"role": "system", "content": session_state.system_prompt}]
    for m in conversation_memory[-10:]:
        role = "user" if m["role"] == "child" else "assistant"
        messages.append({"role": role, "content": m["text"]})

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 150,
        "stream": True
    }

    tts_queue = asyncio.Queue()
    llm_ttft_ms = 0
    tts_ttfa_ms = 0

    # TTS Consumer Task
    async def tts_consumer():
        nonlocal tts_ttfa_ms
        await client_ws.send_json({"type": "status", "state": "speaking"})
        while True:
            chunk_text = await tts_queue.get()
            if chunk_text is None:
                break
            
            audio_bytes = await fetch_cartesia_tts(chunk_text)
            if audio_bytes:
                if not tts_ttfa_ms:
                    tts_ttfa_ms = int((time.time() - llm_start) * 1000)
                    total_ms = stt_ms + tts_ttfa_ms
                    await client_ws.send_json({
                        "type": "latency",
                        "tts_ttfa_ms": tts_ttfa_ms,
                        "total_ms": total_ms
                    })
                await client_ws.send_bytes(audio_bytes)

        await client_ws.send_json({"type": "status", "state": "listening" if session_state.mode == "ACTIVE" else "waking"})

    consumer_task = asyncio.create_task(tts_consumer())

    full_response = ""
    sentence_buffer = ""
    first_filler_sent = False

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", url, headers=headers, json=payload, timeout=30.0) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta:
                                if not llm_ttft_ms:
                                    llm_ttft_ms = int((time.time() - llm_start) * 1000)
                                    await client_ws.send_json({"type": "latency", "llm_ttft_ms": llm_ttft_ms})

                                full_response += delta
                                sentence_buffer += delta

                                await client_ws.send_json({"type": "llm_text", "text": delta, "done": False})

                                # Zero Latency Trick: Instantly dispatch first filler word/phrase to TTS!
                                if not first_filler_sent:
                                    # Match filler word ending in punctuation or space (e.g., "Hmm...", "Oh!", "Let's see...")
                                    match = re.match(r"^\s*([A-Za-z'!\.\s]+[\!\?\.,])\s*", sentence_buffer)
                                    if match:
                                        filler_text = match.group(1).strip()
                                        first_filler_sent = True
                                        sentence_buffer = sentence_buffer[match.end():]
                                        logger.info(f"⚡ FILLER WORD TRICK: Dispatched filler '{filler_text}' immediately to TTS!")
                                        await tts_queue.put(filler_text)

                                # Split remaining text by sentence boundary
                                elif any(p in sentence_buffer for p in [".", "?", "!", "\n"]):
                                    last_p_idx = max(sentence_buffer.rfind(p) for p in [".", "?", "!", "\n"])
                                    if last_p_idx != -1:
                                        to_speak = sentence_buffer[:last_p_idx+1].strip()
                                        sentence_buffer = sentence_buffer[last_p_idx+1:]
                                        if to_speak:
                                            await tts_queue.put(to_speak)

                        except json.JSONDecodeError:
                            pass

        if sentence_buffer.strip():
            await tts_queue.put(sentence_buffer.strip())

    except Exception as e:
        logger.error(f"Groq LLM Exception: {e}")

    await tts_queue.put(None)
    await consumer_task

    await client_ws.send_json({"type": "llm_text", "text": "", "done": True})

    if full_response.strip():
        conversation_memory.append({"role": "toy", "text": full_response.strip()})
        await client_ws.send_json({"type": "chat", "role": "toy", "text": full_response.strip()})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
