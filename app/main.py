#!/usr/bin/env python3
"""Lumi — Mac-side app (FastAPI).

Serves the mobile-first PWA, holds the ElevenLabs key, and owns the local
fallback. Proxies emotion analysis + answer generation to the GPU `server/`
when it's configured (EMOTION_SERVER_URL), otherwise degrades gracefully so
the laptop demo works on its own.

Run from the repo root:
    python3 -m venv app/.venv && source app/.venv/bin/activate
    pip install -r app/requirements.txt
    uvicorn app.main:app --reload --port 8000
    # open http://localhost:8000   (mic needs HTTPS on other devices — use a tunnel)
"""
import asyncio
import base64
import io
import json
import os
import pathlib
import re

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.eleven_tts import EMOTION_VOICE, load_dotenv, synthesize

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "static"
load_dotenv(ROOT / ".env")

EMOTION_SERVER_URL = os.environ.get("EMOTION_SERVER_URL", "").strip().rstrip("/")
INFERENCE_AUTH_TOKEN = os.environ.get("INFERENCE_AUTH_TOKEN", "").strip()
ALLOW_LOCAL_FALLBACK = os.environ.get("ALLOW_LOCAL_FALLBACK", "true").lower() == "true"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip()


def has_eleven() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def server_ready() -> bool:
    # treat the .env.example placeholder ("http://HOST:8100") as not-configured
    return bool(EMOTION_SERVER_URL) and "HOST" not in EMOTION_SERVER_URL


def _auth() -> dict:
    return {"Authorization": f"Bearer {INFERENCE_AUTH_TOKEN}"} if INFERENCE_AUTH_TOKEN else {}


# Placeholder concierge replies — used only until server/ /answer (qwen3) is live.
FALLBACK_REPLIES = {
    "calm":       "Of course — happy to help. Let me take care of that for you right away.",
    "neutral":    "Of course — let me take care of that for you right away.",
    "anxious":    "Don't worry — I'm on it right now. Tell me a little more and we'll sort this out together.",
    "expectant":  "Good question — let me look into that for you right now.",
    "frustrated": "You're right to be frustrated, and I'm sorry. Here's exactly what I'm doing to make it right.",
    "angry":      "I hear you, and I'm sorry this happened. Let me fix it for you right now — here's my plan.",
    "eager":      "Great choice! Let me get that started for you straight away.",
    "happy":      "Wonderful — I'm so glad! Is there anything else I can take care of for you?",
    "relieved":   "I'm so glad that worked out. Anything else I can do to make your stay better?",
    "distress":   "I hear you, and your safety comes first. I'm getting help to you right now — I'm staying with you.",
}

# ---- media: catalog (shared with the box) + resolver (cached-first, self-expanding) ----
MEDIA_DIR = STATIC / "media"
try:
    CATALOG = json.loads((ROOT / "server" / "catalog.json").read_text())["items"]
except Exception:
    CATALOG = []


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def _match_catalog(phrase: str):
    p = phrase.lower().strip()
    ps = _slug(p)
    for it in CATALOG:
        if it["id"] in (ps, p) or it["id"].replace("_", " ") in p or p in it["title"].lower():
            return it
    for it in CATALOG:  # loose: any title word appears in the phrase
        if any(w in p for w in it["title"].lower().split() if len(w) > 3):
            return it
    return None


async def resolve_media(media_list, emotion):
    """Map the LLM's media picks -> renderable tiles. Cached catalog first; on a miss,
    live-fetch via the box and SAVE it locally (self-expanding image DB). Emotion-gated."""
    if not media_list or emotion == "distress":
        return []
    out, seen = [], set()
    for m in media_list:
        item = _match_catalog(m)
        if item and (MEDIA_DIR / f"{item['id']}.jpg").exists():
            url = f"/static/media/{item['id']}.jpg"
            if url not in seen:
                seen.add(url)
                out.append({"url": url, "title": item["title"], "caption": item.get("caption", "")})
            continue
        slug = _slug(m)
        f = MEDIA_DIR / f"{slug}.jpg"
        if not f.exists() and server_ready():
            try:
                async with httpx.AsyncClient(timeout=25) as c:
                    rr = await c.get(f"{EMOTION_SERVER_URL}/media/fetch", params={"q": m}, headers=_auth())
                if rr.status_code == 200 and len(rr.content) > 4000:
                    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(rr.content)
            except Exception:
                pass
        if f.exists():
            url = f"/static/media/{slug}.jpg"
            if url not in seen:
                seen.add(url)
                out.append({"url": url, "title": m.title(), "caption": ""})
    return out[:4]


app = FastAPI(title="Lumi — concierge app")


class TTSReq(BaseModel):
    text: str
    emotion: str = "neutral"


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "elevenlabs": has_eleven(),
        "emotion_server": server_ready(),
        "local_fallback": ALLOW_LOCAL_FALLBACK,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/sw.js")
def service_worker():
    return Response((STATIC / "sw.js").read_text(), media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return Response((STATIC / "manifest.webmanifest").read_text(),
                    media_type="application/manifest+json")


@app.post("/api/tts")
def api_tts(req: TTSReq):
    """Synthesize one line with emotion-tuned voice settings. Key stays server-side."""
    if not has_eleven():
        return JSONResponse({"error": "no_elevenlabs_key"}, status_code=503)
    try:
        audio = synthesize(req.text, req.emotion)
    except Exception as e:
        return JSONResponse({"error": "tts_failed", "detail": str(e)[:300]}, status_code=502)
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/api/guest")
async def api_guest():
    """Issue a device token via the box (keeps the inference auth token server-side)."""
    if not server_ready():
        return JSONResponse({"guest_token": None})
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{EMOTION_SERVER_URL}/guest", headers=_auth())
            r.raise_for_status()
            return r.json()
    except Exception:
        return JSONResponse({"guest_token": None})


@app.post("/api/turn")
async def api_turn(
    audio: UploadFile = File(None),
    forced_emotion: str = Form(None),
    user_text: str = Form(None),
    messages_json: str = Form("[]"),
    guest_token: str = Form(None),
    session_id: str = Form(None),
):
    """One conversational turn: analyze -> answer -> speak, with fallbacks at each step."""
    messages = json.loads(messages_json or "[]")
    transcript = (user_text or "").strip()
    emotion = (forced_emotion or "").lower().strip()
    vad = prosody = guard = None

    # 1) ANALYZE (acoustic emotion). Proxy to the GPU server; else fall back.
    if server_ready() and audio is not None:
        try:
            data = await audio.read()
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(f"{EMOTION_SERVER_URL}/analyze", headers=_auth(),
                                 files={"audio": (audio.filename or "turn.webm", data,
                                                  audio.content_type or "application/octet-stream")})
                r.raise_for_status()
                a = r.json()
            transcript = a.get("transcript", transcript) or transcript
            emotion = emotion or a.get("emotion", "")
            vad, prosody, guard = a.get("vad"), a.get("prosody"), a.get("guard")
        except Exception:
            if not ALLOW_LOCAL_FALLBACK:
                raise
    emotion = emotion or "neutral"  # tier-3 manual toggle / safe default

    if transcript:
        messages = messages + [{"role": "user", "content": transcript}]

    # 2) ANSWER (tone-adapted) + LLM media picks. Proxy to qwen3; else templated reply.
    reply, box_media, returning = None, [], False
    if server_ready():
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(f"{EMOTION_SERVER_URL}/answer", headers=_auth(),
                                 json={"messages": messages, "emotion": emotion,
                                       "guest_token": guest_token, "session_id": session_id,
                                       "vad": vad, "prosody": prosody, "guard": guard})
                r.raise_for_status()
                resp = r.json()
                reply = resp.get("reply")
                box_media = resp.get("media", []) or []
                returning = bool(resp.get("returning"))
        except Exception:
            if not ALLOW_LOCAL_FALLBACK:
                raise
    if not reply:
        reply = FALLBACK_REPLIES.get(emotion, FALLBACK_REPLIES["neutral"])

    media_items = await resolve_media(box_media, emotion)

    # 3) SPEAK (ElevenLabs). If no key, client falls back to browser speechSynthesis.
    audio_b64 = None
    if has_eleven():
        try:
            audio_bytes = await asyncio.to_thread(synthesize, reply, emotion)
            audio_b64 = base64.b64encode(audio_bytes).decode()
        except Exception:
            audio_b64 = None

    messages = messages + [{"role": "assistant", "content": reply}]
    return {
        "transcript": transcript,
        "emotion": emotion,
        "vad": vad,
        "prosody": prosody,
        "guard": guard,
        "reply": reply,
        "media": media_items,
        "returning": returning,
        "voice": EMOTION_VOICE.get(emotion, EMOTION_VOICE["neutral"]),
        "audio_b64": audio_b64,
        "messages": messages,
        "source": "server" if server_ready() else "local-fallback",
    }


@app.get("/share", response_class=HTMLResponse)
def share():
    """Render a QR code of the public tunnel URL for one-tap judge access."""
    if not PUBLIC_URL:
        return HTMLResponse(
            "<body style='font-family:system-ui;background:#07080a;color:#eaf8a1;"
            "display:grid;place-items:center;height:100vh;text-align:center'>"
            "<div><h2>Set <code>PUBLIC_URL</code> to your tunnel URL</h2>"
            "<p style='color:#888'>e.g. <code>PUBLIC_URL=https://xyz.trycloudflare.com</code> in .env, then reload.</p></div></body>")
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(PUBLIC_URL)
        qr.make(fit=True)
        m = qr.get_matrix()
        n = len(m)
        cell = 9
        size = n * cell
        rects = "".join(
            f'<rect x="{x*cell}" y="{y*cell}" width="{cell}" height="{cell}"/>'
            for y, row in enumerate(m) for x, val in enumerate(row) if val
        )
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
               f'viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
               f'<rect width="{size}" height="{size}" fill="#fff"/><g fill="#000">{rects}</g></svg>')
    except Exception as e:
        svg = f"<p style='color:#c33'>QR unavailable: {str(e)[:140]}</p>"
    return HTMLResponse(
        f"<body style='font-family:system-ui;background:#07080a;color:#f4f6ef;"
        f"display:grid;place-items:center;height:100vh;text-align:center'>"
        f"<div><h2 style='color:#eaf8a1'>Scan to open Lumi</h2>"
        f"<div style='background:#fff;padding:18px;border-radius:14px;display:inline-block'>{svg}</div>"
        f"<p style='font-family:monospace;color:#888;margin-top:14px'>{PUBLIC_URL}</p></div></body>")


@app.get("/console", response_class=HTMLResponse)
def console():
    """Operator Console — Live Sessions (chat DB) + Proof (eval benchmark)."""
    return (STATIC / "console.html").read_text()


@app.get("/api/stats")
async def api_stats():
    if not server_ready():
        return JSONResponse({"error": "no_server"})
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{EMOTION_SERVER_URL}/stats", headers=_auth())
            return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)[:120]})


@app.get("/api/eval")
def api_eval():
    p = ROOT / "eval_results.json"
    return JSONResponse(json.loads(p.read_text()) if p.exists() else {"available": False})


# Static assets (styles.css, app.js, icon.svg, ...)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
