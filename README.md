# KODA V2 - Zero Latency Voice Companion

This directory contains **KODA V2**, built with a brand new AI stack and the **Filler Word Latency Masking Trick** for zero perceived response delay.

## 🚀 Stack

- **VAD**: Silero VAD (Speech vs Silence segmentation)
- **SST**: Parakeet ASR Model
- **LLM**: Groq Llama 3 8B (`llama3-8b-8192`)
- **TTS**: Cartesia Sonic (`sonic-english`)
- **Biometric Voice Lock**: Resemblyzer speaker verification

## ⚡ Zero-Latency "Filler Word" Trick
1. The LLM system prompt mandates that every initial response starts with a natural filler word/phrase (e.g. `Hmm...`, `Let's see...`, `Oh!`).
2. The server regex-detects the first filler word chunk as it streams from Groq and dispatches it **immediately** to Cartesia Sonic TTS.
3. The filler word audio plays back to the child within ~100-200ms of speaking, masking the remaining 1-2 seconds of LLM generation time.

## 🛠️ How to Run

1. Open `.env` and fill in your keys:
   ```env
   GROQ_API_KEY=your_groq_api_key
   CARTESIA_API_KEY=your_cartesia_api_key
   PARAKEET_API_URL=https://api.nvidia.com/v1/audio/parakeet
   PARAKEET_API_KEY=your_parakeet_key
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the server:
   ```bash
   python server.py
   ```
   Or:
   ```bash
   uvicorn server:app --host 0.0.0.0 --port 8000
   ```

4. Open `http://localhost:8000` in your browser.
