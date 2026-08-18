"""
Mock for webrtcvad to avoid C++ build errors on Windows.
Resemblyzer imports this, but we bypass its usage using a custom preprocessor
in voice_biometric.py.
"""

class Vad:
    def __init__(self, mode=3):
        pass

    def set_mode(self, mode):
        pass

    def is_speech(self, frame, sample_rate):
        # We don't actually use this because we avoid resemblyzer.preprocess_wav,
        # but just in case, we return True so it doesn't fail.
        return True
