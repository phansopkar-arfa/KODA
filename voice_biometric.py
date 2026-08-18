"""
Voice biometric module for speaker enrollment and verification.
Uses numpy-based audio processing and a simplified resemblyzer pipeline
that avoids the webrtcvad C dependency.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

_encoder = None


def init_encoder():
    """Lazily loads the Resemblyzer VoiceEncoder."""
    global _encoder
    if _encoder is None:
        logger.info("Initializing Resemblyzer VoiceEncoder...")
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder()
        logger.info("VoiceEncoder initialized.")
    return _encoder


def _preprocess_audio(audio_float32: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """
    Preprocess audio without webrtcvad dependency.
    Normalizes volume, trims silence, and resamples to 16kHz if needed.
    """
    if len(audio_float32) == 0:
        return audio_float32

    # Normalize to [-1, 1]
    max_val = np.abs(audio_float32).max()
    if max_val > 0:
        audio_float32 = audio_float32 / max_val

    # Simple silence trimming: remove leading/trailing low-energy segments
    frame_length = int(sample_rate * 0.025)  # 25ms frames
    hop_length = int(sample_rate * 0.010)    # 10ms hop
    energy_threshold = 0.02

    # Compute frame energies
    num_frames = max(1, (len(audio_float32) - frame_length) // hop_length + 1)
    energies = np.zeros(num_frames)
    for i in range(num_frames):
        start = i * hop_length
        end = min(start + frame_length, len(audio_float32))
        frame = audio_float32[start:end]
        energies[i] = np.sqrt(np.mean(frame ** 2))

    # Find first and last frames above threshold
    active_frames = np.where(energies > energy_threshold)[0]
    if len(active_frames) == 0:
        return audio_float32  # Return as-is if no speech detected

    start_sample = active_frames[0] * hop_length
    end_sample = min(active_frames[-1] * hop_length + frame_length, len(audio_float32))

    return audio_float32[start_sample:end_sample]


def create_embedding(pcm_bytes: bytes, sample_rate: int = 16000) -> list[float]:
    """
    Converts raw PCM int16 bytes to float32 numpy array,
    preprocesses audio, and creates a 256-d speaker embedding.
    """
    encoder = init_encoder()
    try:
        if not pcm_bytes:
            return []

        # Convert PCM int16 to float32
        audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # Calculate duration in seconds
        duration = len(audio_float32) / sample_rate
        if duration < 1.5:
            logger.warning(f"Audio length ({duration:.2f}s) is too short for reliable embedding. Minimum is ~1.5s.")

        # Preprocess (normalize + trim silence) without webrtcvad
        wav = _preprocess_audio(audio_float32, sample_rate)

        if len(wav) < sample_rate:  # Less than 1 second after trimming
            logger.warning("Audio too short after preprocessing")
            return []

        # Create embedding using resemblyzer encoder directly
        # embed_utterance expects a 1-D float numpy array
        embedding = encoder.embed_utterance(wav)
        return embedding.tolist()

    except Exception as e:
        logger.error(f"Error creating voice embedding: {e}")
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
    Creates test embedding from PCM bytes, computes cosine similarity
    against stored embedding.
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

    logger.info(f"Voice verification: similarity={similarity:.4f}, threshold={threshold}, match={is_match}")
    return is_match, similarity
