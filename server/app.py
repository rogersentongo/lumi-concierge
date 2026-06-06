#!/usr/bin/env python3
"""Lumi — GPU inference server (FastAPI, port 8100, runs ON the box).

  GET  /healthz   liveness (+ ollama reachability)
  POST /analyze   audio -> faster-whisper STT + emotion2vec+ + prosody + qwen3 lexical
                  -> fused emotion + V-A-D + guardrail signals
  POST /answer    {messages, emotion} -> qwen3 concierge reply (tone-adapted)

Isolation: pin to GPU 1 (CUDA_VISIBLE_DEVICES=1), expose only this port, auth via a
bearer token. Calls the box's localhost Ollama READ-ONLY. See README + ../CLAUDE.local.md.
"""
import asyncio
import os
import subprocess
import tempfile
import threading

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import curate_media
import db
import emotion
import llm
import memory
import stt

AUTH_TOKEN = os.environ.get("INFERENCE_AUTH_TOKEN", "").strip()

app = FastAPI(title="Lumi — GPU inference server")


@app.on_event("startup")
def _startup():
    db.init_schema()


def _check_auth(authorization):
    if not AUTH_TOKEN:
        return  # dev mode: no token configured
    if authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="bad or missing auth token")


def _to_wav(src_bytes: bytes):
    """Persist the upload and transcode to 16k mono WAV via ffmpeg. Returns (raw, wav)."""
    raw = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    raw.write(src_bytes)
    raw.flush()
    raw.close()
    wav = raw.name + ".wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw.name, "-ar", "16000", "-ac", "1", wav],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return raw.name, wav


def _cleanup(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


class AnswerReq(BaseModel):
    messages: list
    emotion: str = "neutral"
    guest_token: str | None = None
    session_id: str | None = None
    vad: dict | None = None
    prosody: dict | None = None
    guard: dict | None = None


class ClaimReq(BaseModel):
    guest_token: str
    room: str = ""
    name: str = ""


@app.get("/healthz")
def healthz():
    # model loads are lazy + heavy; only probe the cheap checks here.
    return {"ok": True, "ollama": llm.ollama_ok(), "db": db.db_ok()}


@app.post("/guest")
def guest(authorization: str = Header(default=None)):
    """Issue a device token (identity) for a brand-new guest."""
    _check_auth(authorization)
    token, gid = auth.issue_token()
    try:
        memory.ensure_guest(gid)
    except Exception:
        pass
    return {"guest_token": token, "guest_id": gid}


@app.post("/claim")
def claim(req: ClaimReq, authorization: str = Header(default=None)):
    """Bind a name/room to the device (optional 'real hotel' identity)."""
    _check_auth(authorization)
    gid = auth.verify_token(req.guest_token)
    if not gid:
        return JSONResponse({"error": "bad_token"}, status_code=401)
    try:
        memory.claim(gid, req.room, req.name)
    except Exception:
        pass
    return {"ok": True}


@app.get("/stats")
def stats(authorization: str = Header(default=None)):
    """Live analytics over the chat DB for the operator Console."""
    _check_auth(authorization)
    try:
        with db.pg() as c:
            totals = {
                "guests": c.execute("SELECT count(*) FROM guests").fetchone()[0],
                "sessions": c.execute("SELECT count(*) FROM sessions").fetchone()[0],
                "turns": c.execute("SELECT count(*) FROM turns").fetchone()[0],
                "complaints": c.execute("SELECT count(*) FROM memories WHERE status='open'").fetchone()[0],
            }
            by_emotion = c.execute(
                "SELECT coalesce(emotion,'?'), count(*) FROM turns GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
            guests = c.execute(
                "SELECT g.guest_id, coalesce(g.name,''), coalesce(g.room,''), count(t.id), max(t.ts), "
                "array_remove(array_agg(DISTINCT t.emotion), NULL) "
                "FROM guests g LEFT JOIN turns t USING (guest_id) "
                "GROUP BY 1,2,3 ORDER BY max(t.ts) DESC NULLS LAST LIMIT 12"
            ).fetchall()
            complaints = c.execute(
                "SELECT guest_id, text, status, ts FROM memories WHERE kind='complaint' ORDER BY ts DESC LIMIT 15"
            ).fetchall()
            recent = c.execute(
                "SELECT ts, guest_id, emotion, left(coalesce(user_text,''),60), left(coalesce(reply,''),60) "
                "FROM turns ORDER BY ts DESC LIMIT 15"
            ).fetchall()
        return {
            "totals": totals,
            "by_emotion": [{"emotion": e, "count": n} for e, n in by_emotion],
            "guests": [{"id": str(g)[:8], "name": nm, "room": rm, "turns": tc,
                        "last": str(lt) if lt else None, "emotions": list(em or [])}
                       for g, nm, rm, tc, lt, em in guests],
            "complaints": [{"guest": str(g)[:8], "text": tx, "status": st, "ts": str(ts)}
                           for g, tx, st, ts in complaints],
            "recent": [{"ts": str(ts), "guest": str(g)[:8], "emotion": em, "said": sd, "reply": rp}
                       for ts, g, em, sd, rp in recent],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)[:160]}, status_code=500)


# Serialize GPU inference (one model pass at a time) — the models aren't thread-safe
# and there's one GPU anyway. The endpoint runs in a worker thread so the event loop
# (and /healthz, /answer) stay responsive while a turn is being analyzed.
_GPU_LOCK = threading.Lock()


def _run_analyze(data: bytes) -> dict:
    raw_path, wav_path = _to_wav(data)
    try:
        with _GPU_LOCK:
            transcript = stt.transcribe(wav_path)
            acoustic = emotion.acoustic_emotion(wav_path)
            pros = emotion.prosody(wav_path)
        lexical = llm.lexical_read(transcript)   # Ollama (network) — no GPU lock needed
        fused = emotion.fuse(acoustic, lexical, pros)
        return {
            "transcript": transcript,
            "emotion": fused["emotion"],
            "vad": fused["vad"],
            "prosody": pros,
            "guard": fused["guard"],
            "disagree": fused["disagree"],
            "confidence": fused["confidence"],
            "sources": fused["sources"],
            "lexical": lexical,
        }
    finally:
        _cleanup(raw_path, wav_path)


@app.post("/analyze")
async def analyze(audio: UploadFile = File(...), authorization: str = Header(default=None)):
    _check_auth(authorization)
    data = await audio.read()
    try:
        return await asyncio.to_thread(_run_analyze, data)
    except Exception:
        return JSONResponse({"error": "analyze_failed", "hint": "decode/model error (ffmpeg + models present?)"}, status_code=400)


@app.post("/answer")
def answer(req: AnswerReq, authorization: str = Header(default=None)):
    _check_auth(authorization)
    guest_id = auth.verify_token(req.guest_token) if req.guest_token else None
    mem_block, returning = "", False
    if guest_id:
        try:
            memory.ensure_guest(guest_id)
            memory.ensure_session(req.session_id, guest_id)
            mem_block = memory.memory_block(memory.load_memory(guest_id))
            returning = bool(mem_block)
        except Exception:
            pass

    result = llm.answer(req.messages, req.emotion, memory=mem_block)
    if not result.get("reply"):
        return JSONResponse({"error": "llm_failed"}, status_code=502)
    reply, media = result["reply"], result.get("media", [])

    if guest_id:
        try:
            user_text = next((m.get("content", "") for m in reversed(req.messages) if m.get("role") == "user"), "")
            memory.record_turn(req.session_id, guest_id, user_text, req.emotion,
                               req.vad, req.prosody, req.guard, reply, media)
        except Exception:
            pass

    return {"reply": reply, "emotion": req.emotion, "media": media, "returning": returning}


@app.get("/media/fetch")
def media_fetch(q: str, authorization: str = Header(default=None)):
    """Self-expansion: fetch one image for a fresh query via SearXNG -> jpg bytes.
    The Mac app caches the result, growing the local image DB."""
    _check_auth(authorization)
    data = curate_media.fetch_one(q)
    if not data:
        return JSONResponse({"error": "no_image"}, status_code=404)
    return Response(content=data, media_type="image/jpeg")
