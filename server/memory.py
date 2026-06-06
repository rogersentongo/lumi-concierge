"""Lumi — sessions + long-term per-guest memory (Postgres-backed).

  ensure_guest / ensure_session   upserts on each turn
  load_memory / memory_block      what Lumi recalls -> injected into qwen3
  record_turn                     log the full turn (+ capture complaints heuristically)
  claim                           bind a name/room to the device (the /claim flow)
"""
import db
from psycopg.types.json import Jsonb

NEG = {"angry", "frustrated", "sad", "distress", "anxious"}


def ensure_guest(guest_id, device_hash=None):
    with db.pg() as c:
        c.execute(
            "INSERT INTO guests (guest_id, device_hash) VALUES (%s,%s) "
            "ON CONFLICT (guest_id) DO UPDATE SET last_seen=now()",
            (guest_id, device_hash),
        )


def ensure_session(session_id, guest_id):
    if not session_id:
        return
    with db.pg() as c:
        c.execute(
            "INSERT INTO sessions (session_id, guest_id) VALUES (%s,%s) "
            "ON CONFLICT (session_id) DO UPDATE SET last_at=now()",
            (session_id, guest_id),
        )


def is_returning(guest_id) -> bool:
    with db.pg() as c:
        n = c.execute("SELECT count(*) FROM turns WHERE guest_id=%s", (guest_id,)).fetchone()[0]
    return n > 0


def load_memory(guest_id) -> dict:
    with db.pg() as c:
        g = c.execute("SELECT name, room, profile_summary FROM guests WHERE guest_id=%s", (guest_id,)).fetchone()
        recent = c.execute(
            "SELECT user_text, emotion, reply FROM turns WHERE guest_id=%s ORDER BY ts DESC LIMIT 6", (guest_id,)
        ).fetchall()
        complaints = c.execute(
            "SELECT text FROM memories WHERE guest_id=%s AND status='open' AND kind='complaint' ORDER BY ts DESC LIMIT 5",
            (guest_id,),
        ).fetchall()
    return {
        "name": g[0] if g else None,
        "room": g[1] if g else None,
        "summary": (g[2] if g else "") or "",
        "recent": list(reversed(recent)),  # chronological
        "complaints": [x[0] for x in complaints],
    }


def memory_block(mem: dict) -> str:
    """Compact context for the prompt. Empty for a brand-new guest."""
    if not mem["recent"] and not mem["complaints"] and not mem["name"]:
        return ""
    lines = ["RETURNING GUEST — you've spoken before. Use this naturally; do not recite it back."]
    if mem["name"]:
        lines.append(f"Guest: {mem['name']}." + (f" Room {mem['room']}." if mem["room"] else ""))
    if mem["complaints"]:
        lines.append("Open issues to follow up on: " + "; ".join(mem["complaints"]))
    if mem["recent"]:
        convo = " ".join(
            f'[guest({t[1]}): "{t[0]}" → you: "{(t[2] or "")[:80]}"]' for t in mem["recent"] if t[0]
        )
        if convo:
            lines.append("Earlier in your conversations: " + convo)
    return "\n".join(lines)


def record_turn(session_id, guest_id, user_text, emotion, vad, prosody, guard, reply, media, latency_ms=None):
    with db.pg() as c:
        c.execute(
            "INSERT INTO turns (session_id, guest_id, user_text, emotion, vad, prosody, guard, reply, media, latency_ms)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (session_id, guest_id, user_text, emotion,
             Jsonb(vad) if vad else None, Jsonb(prosody) if prosody else None,
             Jsonb(guard) if guard else None, reply, Jsonb(media) if media else None, latency_ms),
        )
        c.execute("UPDATE guests SET last_seen=now() WHERE guest_id=%s", (guest_id,))
        if emotion in NEG and user_text and len(user_text) > 8:
            dup = c.execute(
                "SELECT 1 FROM memories WHERE guest_id=%s AND kind='complaint' AND status='open' AND text=%s",
                (guest_id, user_text[:200]),
            ).fetchone()
            if not dup:
                c.execute("INSERT INTO memories (guest_id, kind, text) VALUES (%s,'complaint',%s)",
                          (guest_id, user_text[:200]))


def claim(guest_id, room, name):
    with db.pg() as c:
        c.execute("UPDATE guests SET name=%s, room=%s WHERE guest_id=%s", (name, room, guest_id))
