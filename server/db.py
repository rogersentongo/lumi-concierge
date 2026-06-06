"""Lumi data layer — our OWN isolated Postgres + Redis (NOT CityPulse's).

Connect-per-call (localhost is ~1ms) keeps it simple and thread-safe under uvicorn's
threadpool. Schema is created on startup if absent.
"""
import os

import psycopg
import redis as redislib

PG_DSN = os.environ.get("LUMI_PG_DSN", "postgresql://lumi:lumi_local_dev@127.0.0.1:5434/lumi")
REDIS_URL = os.environ.get("LUMI_REDIS_URL", "redis://127.0.0.1:6380/0")

_redis = None


def r():
    global _redis
    if _redis is None:
        _redis = redislib.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def pg():
    return psycopg.connect(PG_DSN, autocommit=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS guests (
  guest_id        uuid PRIMARY KEY,
  device_hash     text UNIQUE,
  name            text,
  room            text,
  created_at      timestamptz DEFAULT now(),
  last_seen       timestamptz DEFAULT now(),
  profile_summary text DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id  uuid PRIMARY KEY,
  guest_id    uuid REFERENCES guests(guest_id),
  started_at  timestamptz DEFAULT now(),
  last_at     timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS turns (
  id          bigserial PRIMARY KEY,
  session_id  uuid,
  guest_id    uuid,
  ts          timestamptz DEFAULT now(),
  user_text   text,
  emotion     text,
  vad         jsonb,
  prosody     jsonb,
  guard       jsonb,
  reply       text,
  media       jsonb,
  latency_ms  int
);
CREATE TABLE IF NOT EXISTS memories (
  id        bigserial PRIMARY KEY,
  guest_id  uuid,
  ts        timestamptz DEFAULT now(),
  kind      text,
  text      text,
  status    text DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS turns_guest_ts ON turns (guest_id, ts DESC);
CREATE INDEX IF NOT EXISTS mem_guest ON memories (guest_id, status);
"""


def init_schema() -> bool:
    try:
        with pg() as conn:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    conn.execute(stmt)
        return True
    except Exception as e:
        print("DB init failed:", repr(e)[:200])
        return False


def db_ok() -> bool:
    try:
        with pg() as conn:
            conn.execute("SELECT 1")
        r().ping()
        return True
    except Exception:
        return False
