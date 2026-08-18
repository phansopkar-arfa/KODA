// DOM Elements
const ui = {
    orb: document.getElementById('voice-orb'),
    status: document.getElementById('status-label'),
    startBtn: document.getElementById('start-btn'),
    volBar: document.getElementById('vol-bar'),
    barStt: document.getElementById('bar-stt'),
    barLlm: document.getElementById('bar-llm'),
    barTts: document.getElementById('bar-tts'),
    barTotal: document.getElementById('bar-total'),
    valStt: document.getElementById('val-stt'),
    valLlm: document.getElementById('val-llm'),
    valTts: document.getElementById('val-tts'),
    valTotal: document.getElementById('val-total'),
    chatLog: document.getElementById('chat-log'),
    thinkingArea: document.getElementById('thinking-area'),
    interimText: document.getElementById('interim-text'),
};

// State
let ws = null;
let audioContext = null;
let mediaStream = null;
let scriptProcessor = null;
let isListening = false;
let isPlaying = false;
let currentSource = null;
let audioQueue = [];
let nextPlayTime = 0;
let silenceFrames = 0;
let isSpeakingLocal = false;
const ENERGY_THRESHOLD = 0.03;

// --- Orb State ---
function setOrbState(state, label) {
    ui.orb.className = `orb ${state}`;
    const labels = {
        idle: 'Ready',
        waking: '💤 Waiting for "Hi Koda"...',
        listening: '🎤 Listening...',
        thinking: '🧠 Thinking...',
        speaking: '🔊 Speaking...',
        unrecognized: '⚠️ Voice not recognized. Only the registered child can talk to KODA.'
    };
    ui.status.textContent = label || labels[state] || state;

    // After unrecognized, revert to waking after 3 seconds
    if (state === 'unrecognized') {
        setTimeout(() => setOrbState('waking'), 3000);
    }
}

// --- Chat ---
function addChatMessage(role, text) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    div.innerHTML = `${text} <span class="timestamp">${time}</span>`;
    ui.chatLog.appendChild(div);
    ui.chatLog.scrollTop = ui.chatLog.scrollHeight;
}

// --- Latency ---
function updateLatency(data) {
    const maxMs = 2000;
    const pct = (ms) => Math.min((ms / maxMs) * 100, 100) + '%';

    if (data.stt_ms !== undefined) {
        ui.barStt.style.width = pct(data.stt_ms);
        ui.valStt.textContent = `${data.stt_ms}ms`;
    }
    if (data.llm_ttft_ms !== undefined) {
        ui.barLlm.style.width = pct(data.llm_ttft_ms);
        ui.valLlm.textContent = `${data.llm_ttft_ms}ms`;
    }
    if (data.tts_ttfa_ms !== undefined) {
        ui.barTts.style.width = pct(data.tts_ttfa_ms);
        ui.valTts.textContent = `${data.tts_ttfa_ms}ms`;
    }
    if (data.total_ms !== undefined) {
        ui.barTotal.style.width = pct(data.total_ms);
        ui.valTotal.textContent = `${data.total_ms}ms`;
    }
}

// --- Mic Capture ---
async function startMic() {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    mediaStream = await navigator.mediaDevices.getUserMedia({ 
        audio: { 
            channelCount: 1, 
            sampleRate: 16000,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
        } 
    });

    const source = audioContext.createMediaStreamSource(mediaStream);
    scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);
    source.connect(scriptProcessor);
    scriptProcessor.connect(audioContext.destination);

    scriptProcessor.onaudioprocess = (e) => {
        if (!isListening || !ws || ws.readyState !== WebSocket.OPEN) return;

        const input = e.inputBuffer.getChannelData(0);

        // RMS energy for VAD + volume bar
        let sum = 0;
        for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
        const rms = Math.sqrt(sum / input.length);
        ui.volBar.style.width = `${Math.min(rms * 500, 100)}%`;

        // VAD: detect speech and interruptions
        if (rms > ENERGY_THRESHOLD) {
            if (!isSpeakingLocal) {
                isSpeakingLocal = true;
                if (isPlaying) handleInterruption();
            }
            silenceFrames = 0;
        } else if (isSpeakingLocal) {
            silenceFrames++;
            if (silenceFrames > 3) {
                isSpeakingLocal = false;
                ui.volBar.style.width = '0%';
            }
        }

        // Convert Float32 → Int16 PCM and send
        const pcm16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        ws.send(pcm16.buffer);
    };
}

// --- Audio Playback ---
function stopPlayback() {
    if (currentSource) { try { currentSource.stop(); } catch(e){} currentSource = null; }
    audioQueue = [];
    isPlaying = false;
    nextPlayTime = 0;
}

function handleInterruption() {
    stopPlayback();
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'interrupt' }));
        setOrbState('listening', '🎤 Interrupted. Listening...');
    }
}

async function playAudioChunk(arrayBuffer) {
    if (!audioContext) return;
    try {
        // Decode raw linear16 (Int16) PCM chunks from WebSocket TTS
        const int16Data = new Int16Array(arrayBuffer);
        const float32Data = new Float32Array(int16Data.length);
        for (let i = 0; i < int16Data.length; i++) {
            float32Data[i] = int16Data[i] / 32768.0;
        }
        
        const audioBuffer = audioContext.createBuffer(1, float32Data.length, 16000);
        audioBuffer.getChannelData(0).set(float32Data);
        
        audioQueue.push(audioBuffer);
        schedulePlayback();
    } catch (e) {
        console.error('Audio decode error', e);
    }
}

function schedulePlayback() {
    if (audioQueue.length === 0 || !audioContext) return;
    if (!isPlaying) { isPlaying = true; nextPlayTime = audioContext.currentTime; }

    while (audioQueue.length > 0) {
        const buffer = audioQueue.shift();
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);
        const playTime = Math.max(audioContext.currentTime, nextPlayTime);
        source.start(playTime);
        nextPlayTime = playTime + buffer.duration;
        source.onended = () => {
            if (audioContext.currentTime >= nextPlayTime - 0.05) isPlaying = false;
        };
        currentSource = source;
    }
}

// --- WebSocket ---
function connectWebSocket() {
    return new Promise((resolve, reject) => {
        ws = new WebSocket('ws://localhost:8000/ws/conversation');
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => resolve();
        ws.onerror = (e) => reject(e);
        ws.onclose = () => stopSession();

        ws.onmessage = (e) => {
            if (typeof e.data === 'string') {
                const msg = JSON.parse(e.data);
                switch (msg.type) {
                    case 'status':
                        setOrbState(msg.state);
                        if (msg.state !== 'thinking') {
                            ui.thinkingArea.classList.add('hidden');
                            ui.interimText.textContent = '';
                        }
                        break;
                    case 'transcript':
                        if (msg.is_final) {
                            ui.thinkingArea.classList.add('hidden');
                            ui.interimText.textContent = '';
                        } else {
                            ui.thinkingArea.classList.remove('hidden');
                            ui.interimText.textContent = msg.text;
                        }
                        break;
                    case 'llm_text':
                        if (msg.done) {
                            ui.thinkingArea.classList.add('hidden');
                            ui.interimText.textContent = '';
                        } else {
                            ui.thinkingArea.classList.remove('hidden');
                            ui.interimText.textContent += msg.text;
                        }
                        break;
                    case 'chat':
                        addChatMessage(msg.role, msg.text);
                        break;
                    case 'latency':
                        updateLatency(msg);
                        break;
                }
            } else {
                playAudioChunk(e.data);
            }
        };
    });
}

// --- Session Control ---
async function startSession() {
    try {
        setOrbState('idle', 'Connecting...');
        await connectWebSocket();
        ws.send(JSON.stringify({ type: 'start' }));
        await startMic();
        isListening = true;
        ui.startBtn.classList.add('hidden');
        setOrbState('waking');
    } catch (err) {
        console.error(err);
        setOrbState('idle', '❌ Connection Failed. Is the server running?');
    }
}

function stopSession() {
    isListening = false;
    stopPlayback();
    if (ws) {
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'stop' }));
            ws.close();
        }
        ws = null;
    }
    if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
    if (scriptProcessor) { scriptProcessor.disconnect(); scriptProcessor = null; }
    ui.startBtn.classList.remove('hidden');
    setOrbState('idle', 'Stopped');
}

// Events
ui.startBtn.addEventListener('click', startSession);
