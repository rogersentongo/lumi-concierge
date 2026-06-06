#!/usr/bin/env python3
"""Design Lumi's voice via ElevenLabs Voice Design.

  app/.venv/bin/python app/design_voice.py design        # ~3 previews -> mp3s you can play
  app/.venv/bin/python app/design_voice.py create <N>    # create voice N -> writes ELEVENLABS_VOICE_ID to .env

Reuses the SSL/cert handling + .env loader from eleven_tts. Run with the app venv
python (it has certifi). Never prints the API key.
"""
import base64
import json
import os
import pathlib
import sys

from eleven_tts import API_BASE, _request, load_dotenv  # same-dir import

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "out"
PREVIEWS_JSON = OUT / "voice_previews.json"

# Tweak these to change Lumi's character, then re-run `design`.
DESCRIPTION = (
    "A warm, polished hotel concierge in her early thirties with a calm, reassuring, and "
    "articulate voice. Gentle and welcoming, neutral American accent, a measured unhurried "
    "pace and quiet confidence — the kind of voice that makes a stressed guest feel instantly "
    "taken care of. Natural, human, never robotic."
)
SAMPLE = (
    "Welcome to The Lumen Hotel — I'm Lumi, your concierge. I'm so sorry your AirPods went missing "
    "from the lounge; let's sort this out together right away. I've already sent someone to check, "
    "and I'll stay with you until we find them."
)
MODEL = "eleven_multilingual_ttv_v2"


def _key():
    load_dotenv(ROOT / ".env")
    k = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not k:
        sys.exit("ELEVENLABS_API_KEY missing in .env")
    return k


def design():
    key = _key()
    OUT.mkdir(parents=True, exist_ok=True)
    body = {"voice_description": DESCRIPTION, "text": SAMPLE, "model_id": MODEL}
    try:
        with _request("POST", f"{API_BASE}/v1/text-to-voice/design", key, body) as r:
            data = json.loads(r.read())
    except Exception as e:
        sys.exit(f"design failed: {repr(e)[:300]}")
    previews = data.get("previews", [])
    if not previews:
        sys.exit(f"no previews returned: {json.dumps(data)[:300]}")
    meta = []
    print(f"Generated {len(previews)} Lumi voice previews:")
    for i, p in enumerate(previews, 1):
        b64 = p.get("audio_base_64") or p.get("audio_base64") or ""
        gvid = p.get("generated_voice_id")
        path = OUT / f"lumi_preview_{i}.mp3"
        if b64:
            path.write_bytes(base64.b64decode(b64))
        meta.append({"index": i, "generated_voice_id": gvid, "file": str(path)})
        print(f"  [{i}] {path.name}")
    PREVIEWS_JSON.write_text(json.dumps(meta, indent=2))
    print(f"\nSaved to {OUT.relative_to(ROOT)}/. Listen, then run:")
    print("  app/.venv/bin/python app/design_voice.py create <N>")


def create(n):
    key = _key()
    if not PREVIEWS_JSON.exists():
        sys.exit("run `design` first")
    meta = json.loads(PREVIEWS_JSON.read_text())
    match = next((m for m in meta if m["index"] == n), None)
    if not match:
        sys.exit(f"no preview {n}; run design first")
    body = {"voice_name": "Lumi", "voice_description": DESCRIPTION,
            "generated_voice_id": match["generated_voice_id"]}
    try:
        with _request("POST", f"{API_BASE}/v1/text-to-voice", key, body) as r:
            data = json.loads(r.read())
    except Exception as e:
        sys.exit(f"create failed: {repr(e)[:300]}")
    vid = data.get("voice_id")
    if not vid:
        sys.exit(f"no voice_id in response: {json.dumps(data)[:300]}")
    print("created voice_id:", vid)
    _set_env_voice(vid)


def _set_env_voice(vid):
    env = ROOT / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    out, done = [], False
    for ln in lines:
        if ln.startswith("ELEVENLABS_VOICE_ID="):
            out.append(f"ELEVENLABS_VOICE_ID={vid}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"ELEVENLABS_VOICE_ID={vid}")
    env.write_text("\n".join(out) + "\n")
    print("✓ wrote ELEVENLABS_VOICE_ID to .env — restart the app to use Lumi's voice")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "design"
    if cmd == "design":
        design()
    elif cmd == "create" and len(sys.argv) > 2:
        create(int(sys.argv[2]))
    else:
        sys.exit("usage: design | create <N>")
