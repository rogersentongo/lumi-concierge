"""Lumi server — speech-to-text via faster-whisper (CTranslate2, GPU).

Lazy-loaded singleton so the app boots even before the model is downloaded.
"""
import functools
import os

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")     # tiny|base|small|medium|large-v3
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "float16")


@functools.lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)


def transcribe(wav_path: str) -> str:
    try:
        segments, _info = _model().transcribe(wav_path, beam_size=1, vad_filter=True, language="en")
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception:
        return ""


def stt_ok() -> bool:
    try:
        _model()
        return True
    except Exception:
        return False
