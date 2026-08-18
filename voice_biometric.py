"""
Voice biometric module for speaker enrollment and verification.
Uses NVIDIA TitaNet (or PyTorch speaker verification fallback) for speaker embeddings
and continuous voice verification.
"""

import logging
import numpy as np
import torch
import io
import soundfile as sf

logger = logging.getLogger(__name__)

_titanet_model = None


def init_encoder():
    """
    Lazily loads the NVIDIA TitaNet speaker verification model.
    Attempts to load NeMo TitaNet Large model; falls back to PyTorch Hub if NeMo is not installed.
    """
    global _titanet_model
    if _titanet_model is not None:
        return _titanet_model

    logger.info("Initializing TitaNet Speaker Verification Model...")

    # Strategy 1: Try NVIDIA NeMoEncDecSpeakerLabelModel (Official TitaNet)
    try:
        import nemo.collections.asr as nemo_asr
        _titanet_model = nemo_asr.models.EncDecSpeakerLabelModel.from_pretrained(
            "nvidia/speakerverification_en_titanet_large"
        )
        _titanet_model.eval()
        logger.info("NVIDIA TitaNet Large (NeMo) loaded successfully.")
        return _titanet_model
    except Exception as e1:
        logger.warning(f"Could not load NeMo TitaNet: {e1}. Trying SpeechBrain / PyTorch Hub fallback...")

    # Strategy 2: SpeechBrain ECAPA-TDNN / TitaNet via PyTorch Hub
    try:
        from speechbrain.inference.speaker import EncoderClassifier
        _titanet_model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir="tmp_model"
        )
        logger.info("SpeechBrain Speaker Encoder loaded successfully as fallback.")
        return _titanet_model
    except Exception as e2:
        logger.warning(f"Could not load SpeechBrain model: {e2}. Using lightweight MFCC spectral embedding fallback.")

    # Strategy 3: Lightweight Spectrogram / MFCC Embedding fallback (Pure PyTorch + NumPy, zero C++ build requirements)
    _titanet_model = "SPECTRAL_FALLBACK"
    logger.info("Spectral Voice Biometric fallback initialized.")
    return _titanet_model


def _preprocess_audio(audio_float32: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Normalizes volume and trims silence."""
    if len(audio_float32) == 0:
        return audio_float32

    # Peak normalization
    max_val = np.abs(audio_float32).max()
    if max_val > 0:
        audio_float32 = audio_float32 / max_val

    # Energy-based silence trimming
    frame_length = int(sample_rate * 0.025)
    hop_length = int(sample_rate * 0.010)
    energy_threshold = 0.02

    num_frames = max(1, (len(audio_float32) - frame_length) // hop_length + 1)
    energies = np.zeros(num_frames)
    for i in range(num_frames):
        start = i * hop_length
        end = min(start + frame_length, len(audio_float32))
        frame = audio_float32[start:end]
        energies[i] = np.sqrt(np.mean(frame ** 2))

    active_frames = np.where(energies > energy_threshold)[0]
    if len(active_frames) == 0:
        return audio_float32

    start_sample = active_frames[0] * hop_length
    end_sample = min(active_frames[-1] * hop_length + frame_length, len(audio_float32))
    return audio_float32[start_sample:end_sample]


def _extract_spectral_embedding(wav: np.ndarray, sample_rate: int = 16000) -> list[float]:
    """Generates a 128-d spectral feature vector using FFT & Mel scale when neural models are offline."""
    # Compute STFT magnitude spectrogram
    tensor_wav = torch.from_numpy(wav).float()
    window = torch.hann_window(512)
    stft = torch.stft(tensor_wav, n_fft=512, hop_length=160, win_length=512, window=window, return_complex=True)
    spectrogram = torch.abs(stft)  # (freq_bins, time_steps)

    # Average across time & standard deviation across time to form fixed-length vector
    mean_spec = torch.mean(spectrogram, dim=1)
    std_spec = torch.std(spectrogram, dim=1)
    vector = torch.cat([mean_spec, std_spec], dim=0)

    # Normalize vector
    vector = vector / (torch.norm(vector) + 1e-8)
    return vector.numpy().tolist()


def create_embedding(pcm_bytes: bytes, sample_rate: int = 16000) -> list[float]:
    """
    Converts raw PCM int16 bytes to float32 numpy array,
    preprocesses audio, and extracts a TitaNet / Speaker embedding.
    """
    if not pcm_bytes:
        return []

    try:
        model = init_encoder()

        # Convert PCM int16 to float32
        audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        wav = _preprocess_audio(audio_float32, sample_rate)

        if len(wav) < int(sample_rate * 0.4):  # Require at least 0.4s
            logger.warning("Audio too short for biometric embedding")
            return []

        # 1. NeMo TitaNet Model
        if hasattr(model, "get_embedding"):
            # Write temp wav buffer for NeMo
            with io.BytesIO() as bio:
                sf.write(bio, wav, sample_rate, format='WAV', subtype='PCM_16')
                bio.seek(0)
                embedding = model.get_embedding(bio)
                if isinstance(embedding, torch.Tensor):
                    embedding = embedding.detach().cpu().numpy().squeeze()
                return embedding.tolist()

        # 2. SpeechBrain Model
        elif hasattr(model, "encode_batch"):
            tensor_wav = torch.from_numpy(wav).unsqueeze(0)
            embeddings = model.encode_batch(tensor_wav)
            return embeddings.squeeze().detach().cpu().numpy().tolist()

        # 3. Spectral Fallback
        else:
            return _extract_spectral_embedding(wav, sample_rate)

    except Exception as e:
        logger.error(f"Error creating TitaNet voice embedding: {e}")
        return []


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Calculates cosine similarity between two numpy arrays."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def verify_speaker(
    test_pcm_bytes: bytes,
    stored_embedding: list[float],
    threshold: float = 0.65,
    sample_rate: int = 16000
) -> tuple[bool, float]:
    """
    Creates test embedding from PCM bytes and computes cosine similarity
    against the registered child profile embedding.
    Returns (is_match, similarity_score).
    """
    if not stored_embedding:
        logger.warning("No stored embedding provided for verification.")
        return False, 0.0

    test_embedding_list = create_embedding(test_pcm_bytes, sample_rate)
    if not test_embedding_list:
        return False, 0.0

    test_emb = np.array(test_embedding_list)
    stored_emb = np.array(stored_embedding)

    similarity = cosine_similarity(test_emb, stored_emb)
    is_match = similarity >= threshold

    logger.info(f"TitaNet Voice Verification: similarity={similarity:.4f}, threshold={threshold}, match={is_match}")
    return is_match, similarity
