#!/usr/bin/env python3
"""Lumi — ElevenLabs expressive TTS (emotion -> voice_settings).

Zero external dependencies (stdlib only) so you can verify your key immediately:

    cp .env.example .env        # then paste your key after ELEVENLABS_API_KEY=
    python3 app/eleven_tts.py   # synthesizes the same line in two emotions

Reads ELEVENLABS_* from the repo-root .env (gitignored). NEVER hardcode the key.

API verified against https://elevenlabs.io/docs/api-reference/text-to-speech/convert
  POST /v1/text-to-speech/{voice_id}   header: xi-api-key   body: text, model_id, voice_settings
"""
import json
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.elevenlabs.io"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "out"

# Lumi's voice settings, keyed by the guest emotion Lumi is RESPONDING to.
# stability down = more emotional range; style/speed tuned per state. (Flash v2.5)
EMOTION_VOICE = {
    "calm":       {"stability": 0.42, "similarity_boost": 0.75, "style": 0.32, "use_speaker_boost": True, "speed": 1.00},
    "neutral":    {"stability": 0.45, "similarity_boost": 0.75, "style": 0.30, "use_speaker_boost": True, "speed": 1.00},
    "anxious":    {"stability": 0.45, "similarity_boost": 0.80, "style": 0.35, "use_speaker_boost": True, "speed": 0.96},
    "expectant":  {"stability": 0.45, "similarity_boost": 0.75, "style": 0.30, "use_speaker_boost": True, "speed": 1.00},
    "frustrated": {"stability": 0.30, "similarity_boost": 0.80, "style": 0.50, "use_speaker_boost": True, "speed": 1.00},
    "angry":      {"stability": 0.40, "similarity_boost": 0.80, "style": 0.42, "use_speaker_boost": True, "speed": 0.95},
    "eager":      {"stability": 0.30, "similarity_boost": 0.75, "style": 0.55, "use_speaker_boost": True, "speed": 1.05},
    "happy":      {"stability": 0.30, "similarity_boost": 0.75, "style": 0.55, "use_speaker_boost": True, "speed": 1.04},
    "relieved":   {"stability": 0.30, "similarity_boost": 0.75, "style": 0.55, "use_speaker_boost": True, "speed": 1.03},
    "distress":   {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30, "use_speaker_boost": True, "speed": 0.92},
}
DEFAULT_VOICE_SETTINGS = EMOTION_VOICE["neutral"]


def load_dotenv(path: pathlib.Path) -> None:
    """Minimal .env loader (no dependency). Real env vars win over .env."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if "  #" in val:                      # strip inline "  # comment"
            val = val.split("  #")[0].strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


# macOS python.org builds often ship without CA certs → HTTPS verify fails.
# Use certifi's bundle when available (it's in the app venv via httpx); else system default.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # certifi missing (zero-dep run) → system defaults
    _SSL_CTX = ssl.create_default_context()


def _request(method: str, url: str, api_key: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("xi-api-key", api_key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=60, context=_SSL_CTX)


def resolve_voice_id(api_key: str) -> str:
    """Use ELEVENLABS_VOICE_ID if set, else fall back to the first account voice."""
    vid = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    if vid:
        return vid
    with _request("GET", f"{API_BASE}/v1/voices", api_key) as r:
        voices = json.loads(r.read()).get("voices", [])
    if not voices:
        raise RuntimeError("No voices on this account and ELEVENLABS_VOICE_ID is empty.")
    v = voices[0]
    print(f"  (no ELEVENLABS_VOICE_ID set — using '{v.get('name')}' = {v['voice_id']})")
    return v["voice_id"]


def synthesize(text: str, emotion: str = "neutral", *,
               api_key: str = None, voice_id: str = None, model_id: str = None) -> bytes:
    """Return MP3 bytes for `text` spoken with `emotion`-tuned voice settings."""
    api_key = api_key or os.environ["ELEVENLABS_API_KEY"]
    voice_id = voice_id or resolve_voice_id(api_key)
    model_id = model_id or os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    settings = EMOTION_VOICE.get(emotion.lower(), DEFAULT_VOICE_SETTINGS)
    body = {"text": text, "model_id": model_id, "voice_settings": settings}
    url = f"{API_BASE}/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    with _request("POST", url, api_key, body) as r:
        return r.read()


def _smoke() -> None:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        print("✗ ELEVENLABS_API_KEY is empty.")
        print("  1) cp .env.example .env")
        print("  2) paste your key after  ELEVENLABS_API_KEY=  (no quotes)")
        print("  3) re-run:  python3 app/eleven_tts.py")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    voice_id = resolve_voice_id(key)
    model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    print(f"→ voice={voice_id}  model={model_id}")

    # Same words, two emotions — so you can HEAR the voice_settings effect.
    line = "Let me take care of that for you right now."
    for emotion in ("calm", "frustrated"):
        try:
            audio = synthesize(line, emotion, api_key=key, voice_id=voice_id, model_id=model_id)
        except urllib.error.HTTPError as e:
            print(f"✗ {emotion}: HTTP {e.code} — {e.read().decode(errors='replace')[:300]}")
            sys.exit(1)
        out = OUT / f"lumi_{emotion}.mp3"
        out.write_bytes(audio)
        print(f"✓ {emotion:11s} {len(audio):>7,d} bytes → {out.relative_to(ROOT)}")

    print("\nDone — same words, audibly different per emotion. Play them:")
    print(f"  open {(OUT / 'lumi_calm.mp3').relative_to(ROOT)} {(OUT / 'lumi_frustrated.mp3').relative_to(ROOT)}")


if __name__ == "__main__":
    _smoke()
