"""Lumi server — qwen3 via the box's localhost Ollama (READ-ONLY).

Two jobs:
  - lexical_read(transcript): a lexical emotion + safety classifier (JSON)
  - answer(messages, emotion): the tone-adapted concierge reply

We never reconfigure Ollama; we only POST to /api/chat. Model + URL come from env.
"""
import json
import os
import re

import httpx

import hotel

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")

CONCIERGE_SYSTEM = """You are Lumi, the warm, attentive voice concierge at The Lumen Hotel, a luxury hotel. Always refer to it as "The Lumen Hotel".
You are on a live voice call, so speak naturally and BRIEFLY — like a real concierge, never robotic, never a wall of text. Keep replies to 1-3 short sentences.

You are told the guest's detected emotion each turn. Adapt to it:
- Upset/angry: acknowledge the feeling FIRST, take ownership, then give one concrete next step. Do not get defensive.
- Anxious/distress: be calm and grounding; reassure; prioritize safety.
- Happy/eager: be warm and upbeat; keep momentum.
- Neutral/calm: be warm and helpful.

Rules:
- Be concrete about what you'll do next; avoid empty "I understand" filler.
- If you must decline something (e.g., releasing security footage requires a police report/court order), do it empathetically and immediately offer an alternative (send staff to check, lost & found, file a report).
- If the guest may be in danger or in crisis, prioritize their safety, offer to connect them to a human / hotel security, and never dismiss it.
- Never invent facts about a specific guest's reservation; ask if you don't know.

Tone for this reply: {tone}."""

TONE_BY_EMOTION = {
    "angry": "de-escalating, accountable, action-first",
    "frustrated": "empathetic, concise, action-first",
    "anxious": "warm, reassuring, calm",
    "distress": "calm, grounding, safety-first",
    "sad": "gentle, warm, unhurried",
    "happy": "upbeat, warm, brief",
    "eager": "upbeat, helpful",
    "relieved": "warm, affirming",
    "expectant": "informative, honest",
    "calm": "warm, helpful",
    "neutral": "warm, helpful",
}

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _chat(messages, system=None, fmt=None, temperature=0.6, timeout=120) -> str:
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    payload = {"model": OLLAMA_MODEL, "messages": msgs, "stream": False,
               "options": {"temperature": temperature}}
    if fmt:
        payload["format"] = fmt
    r = httpx.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    return _strip_think(r.json()["message"]["content"])


def lexical_read(transcript: str) -> dict:
    """Lexical emotion + crisis read from the words alone (the 0.25 fusion term)."""
    transcript = (transcript or "").strip()
    if not transcript:
        return {"emotion": "neutral", "intensity": 0.0, "distress": 0.0, "evidence": ""}
    system = "You are an emotion and safety classifier for a hotel concierge. Return ONLY JSON, no prose."
    prompt = (
        f'Classify this guest message.\nMessage: "{transcript}"\n\n'
        'Return JSON exactly: {"emotion": one of '
        '["neutral","happy","sad","angry","frustrated","anxious","distress","eager"], '
        '"intensity": 0.0-1.0, "distress": 0.0-1.0 (1.0 = clear crisis, safety risk, '
        'self-harm, or someone in danger), "evidence": "<=8 word quote"}'
    )
    try:
        out = _chat([{"role": "user", "content": prompt}], system=system, fmt="json", temperature=0.0)
        data = json.loads(out)
        return {
            "emotion": str(data.get("emotion", "neutral")).lower(),
            "intensity": float(data.get("intensity", 0.0)),
            "distress": float(data.get("distress", 0.0)),
            "evidence": str(data.get("evidence", ""))[:120],
        }
    except Exception:
        return {"emotion": "neutral", "intensity": 0.0, "distress": 0.0, "evidence": ""}


def answer(messages, emotion: str, memory: str = "") -> dict:
    """Tone-adapted concierge reply + chosen media. Returns {reply, media:[ids|phrases]}.

    `memory` is an optional long-term context block (returning guest, past complaints).
    media is the LLM's choice of images to show. Empty for distress turns. {reply:None} on fail.
    """
    emo = (emotion or "neutral").lower()
    tone = TONE_BY_EMOTION.get(emo, "warm, helpful")
    show_media = emo not in ("distress",)  # never show images during a crisis
    media_rule = (
        'Set "media" to [] — the guest is in distress; show NO images.' if not show_media else
        '"media": 0-3 items that genuinely help the guest SEE what they asked about (a dish '
        "they're choosing, a venue they're curious about). Prefer the catalog ids above; if "
        "nothing fits but an image clearly helps, use a short search phrase. Use [] for "
        "emotional/complaint/safety turns or when an image adds nothing."
    )
    parts = [CONCIERGE_SYSTEM.format(tone=tone), hotel.HOTEL_PROFILE, hotel.catalog_hint()]
    if memory:
        parts.append("BACKGROUND from past conversations (do NOT quote or repeat this — use it ONLY to be proactively helpful):\n" + memory)
    parts += [
        f"The guest currently sounds: {emo}.",
        "Answer the guest's LATEST message in your OWN fresh words; never copy the background text. "
        "If they ask for an 'update' and there's an open issue above, proactively address that issue.",
        'Return ONLY JSON: {"reply": "<spoken reply, 1-3 sentences>", "media": [<ids or phrases>]}.',
        media_rule,
    ]
    system = "\n\n".join(parts)
    try:
        data = json.loads(_chat(messages, system=system, fmt="json", temperature=0.5))
        reply = (data.get("reply") or "").strip() or None
        media = data.get("media") or []
        if not isinstance(media, list):
            media = []
        media = [str(m).strip() for m in media if str(m).strip()][:3]
        if not show_media:
            media = []
        return {"reply": reply, "media": media}
    except Exception:
        try:  # fallback: plain reply, no media
            sys_min = CONCIERGE_SYSTEM.format(tone=tone) + "\n\n" + hotel.HOTEL_PROFILE
            return {"reply": (_chat(messages, system=sys_min, temperature=0.5) or None), "media": []}
        except Exception:
            return {"reply": None, "media": []}


def ollama_ok() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False
