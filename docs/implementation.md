# Lumi — Implementation Plan (the "how")

Complements [`plan.md`](plan.md) (the *what/why*). This is the build order, where code runs, the secrets gate, and the demo-day runbook. Server host specifics are in `../CLAUDE.local.md` (gitignored).

## Decision log
- **Client = mobile-first web app (PWA).** Served over HTTPS, shared by **URL + QR code**. Mic via `getUserMedia` (works in iOS Safari 14.3+ over HTTPS). "Add to Home Screen" → full-screen, app-like, **no Apple ID / store / invite**. Runs on the demo laptop *and* judges' phones.
  - **Why not native iOS / TestFlight today:** TestFlight needs an Apple ID (sign-in), the TestFlight app, an invite/public link, a paid Developer account, and build processing + Beta App Review — none reliable before the 6pm deadline, and it's a from-scratch UI rewrite. A PWA is *closer* to "download it, no sign-in" than TestFlight is.
  - **Native iOS = post-hackathon fast-follow.** The backend is client-agnostic, so a future Swift/Expo app just calls the same endpoints.
- **Backend-first build order.**
- **ElevenLabs = expressive TTS (core); Tavus = stretch only.** The ElevenLabs key lives on the Mac (`app/`), never on the exposed GPU box.

## Where code runs
| Service | Runs on | Responsibility | Exposure |
|---|---|---|---|
| **`server/`** | **GPU box** | `/analyze` (STT + emotion2vec+ + prosody + qwen3 lexical → fused V·A·D + guard signals) and `/answer` (qwen3 concierge, streamed, multi-turn) | **private** — reached from the Mac over **Tailscale**; `:8100` + auth token. No public tunnel needed for the demo. |
| **`app/`** | **the Mac** | serves the PWA UI, holds the ElevenLabs key, `/api/tts`, proxies `/api/analyze` + `/api/answer`, owns conversation memory + the **local fallback** | free **cloudflared/ngrok** tunnel → free HTTPS URL (**no domain**) + auth, for judges' phones |

`app/` reaches `server/` privately over Tailscale. Localhost Ollama + SearXNG stay on the box, read-only, never exposed. **The only publicly reachable surface is the `app/` tunnel.**

## Hosting & distribution (no domain needed)
A PWA is distributed as a **URL**, not an app-store binary. The only hard requirement is **HTTPS** (for mic `getUserMedia` + the service worker), which we get for free from a tunnel — **no domain, no DNS, no deploy**:
- run `app/` locally: `uvicorn app.main:app --port 8000`
- expose it: `cloudflared tunnel --url http://localhost:8000` → a free `https://<random>.trycloudflare.com` URL (or ngrok)
- show a **QR code** of that URL (the `/share` page renders it) → judges scan → PWA opens in their browser → optional **Add to Home Screen**. No app store, no sign-in, no invite.

Notes:
- Free tunnel URLs are **ephemeral** (change per run, mild rate limits) → generate the URL/QR **right before demoing**.
- `localhost` is a secure context, so on the Mac itself `http://localhost:8000` works for dev without a tunnel.
- Optional later (not for the demo): a custom domain + named Cloudflare tunnel, or deploy the PWA to Vercel/Netlify with the API behind a tunnel.

## Turn flow (end to end)
1. Browser/PWA records a turn (push-to-talk; optional VAD "call mode") → POST audio to `app/ /api/turn`.
2. `app/` → `server/ /analyze` → `{ transcript, emotion, vad, prosody, guard_signals }`.
3. `app/` → `server/ /answer` with `{ messages[], emotion }` → tone-adapted reply (streamed) + a media decision (emotion-gated).
4. `app/` → ElevenLabs `/tts` with `{ reply_text, emotion }` → audio (voice_settings per emotion).
5. `app/` returns `{ transcript, emotion, vad, reply, audio, media }`; UI updates (V·A·D dot + trajectory, prosody, timeline, guardrail) and plays the reply inline.
6. **Fallbacks at each step** (see ladder).

## Build order
- **Phase 0 ✅** — ElevenLabs TTS smoke test (`app/eleven_tts.py`): emotion → `voice_settings`, verified.
- **Phase 1 — `app/` (Mac), FastAPI.** Serve the mobile-first PWA (from the mockups), `/api/tts` (ElevenLabs, key server-side), and `/api/analyze` + `/api/answer` as proxies to `server/` **with a local DSP-heuristic fallback** so the laptop demo works even if the box is down. Hold session `messages[]` + per-turn emotion history (trajectory). → *a working end-to-end demo on the laptop alone.*
- **Phase 2 — `server/` (GPU box), FastAPI :8100.** Real `/analyze` (faster-whisper STT + emotion2vec+ via FunASR + openSMILE/librosa prosody + qwen3 lexical → fused V·A·D + guard signals) and `/answer` (qwen3 via Ollama, tone-adapted, streamed). `CUDA_VISIBLE_DEVICES=1`. Isolated venv/folder/port.
- **Phase 3 — integrate + polish.** Wire the PWA to live endpoints; design **Lumi's voice** (Voice Design → `ELEVENLABS_VOICE_ID`); emotion-gated media (curated asset pack); reliability pass; **record the backup video**; secret-scan + push.

## `app/` (Mac) — FastAPI
- `GET /` → the PWA (mobile-first; reuse the mockup UI + `styles.css`); `manifest.webmanifest` + service worker for Add-to-Home-Screen.
- `POST /api/tts` `{text, emotion}` → ElevenLabs (reuses `eleven_tts.synthesize`), returns audio. Optional cache by `(text, emotion)`.
- `POST /api/analyze` (audio) → proxy to `server/`; on failure → **local DSP heuristic** (librosa pitch/energy/rate vs. a calibrated baseline).
- `POST /api/answer` `{messages, emotion}` → proxy to `server/` (streamed); on failure → templated tone-adapted reply.
- `POST /api/turn` → orchestrates analyze → answer → tts in one call (demo convenience).
- `GET /share` → a page that renders a **QR code** of the public tunnel URL (passed via `PUBLIC_URL` env) for one-tap judge access.
- `GET /healthz` → liveness for the tunnel/demo check.
- Config from root `.env` (`ELEVENLABS_*`, `EMOTION_SERVER_URL`, `INFERENCE_AUTH_TOKEN`, `ALLOW_LOCAL_FALLBACK`, `PUBLIC_URL`).

## `server/` (GPU box) — FastAPI :8100
- `POST /analyze` → STT + acoustic emotion + prosody + lexical → fused emotion + V·A·D + guard signals.
- `POST /answer` → qwen3 (Ollama, read-only) concierge system prompt + whitelisted tone directive + `messages[]` → streamed reply; optional SearXNG.
- Launch per `CLAUDE.local.md`: separate folder + `uv` venv, `CUDA_VISIBLE_DEVICES=1`, `--port 8100`. Auth token on every request.

## Fallback ladder (reliability)
emotion model → **DSP heuristic** (local, pure math) → **manual emotion toggle** → ElevenLabs down → **local TTS** (macOS `say` / pyttsx3) → network/server down → **pre-recorded backup video**. Media uses the curated asset pack on the demo path, not live search.

## 🔒 Secrets / GitHub gate — run BEFORE every push
- `.env`, `.env.*`, `CLAUDE.local.md`, `app/out/`, `*.mp3` are gitignored (verified). Repo is **not** git-init'd yet → nothing has leaked.
- `git init` → **review `git status`**: confirm `.env`, `CLAUDE.local.md`, audio output are NOT staged.
- **Secret scan**: `gitleaks detect` (or `detect-secrets scan`) + **grep for the server IP / hostname / ports** (scan list in `CLAUDE.local.md`).
- Never tunnel/commit Ollama, SearXNG, SSH, or CityPulse identifiers. Only `:8100` (server) and the `app/` tunnel are exposed, each behind auth.
- Push to a **fresh public repo** (no fork/clone of any other project).

## Demo-day runbook
- **Primary:** present the PWA on the **laptop** (big screen, reliable). Walk the 3 beats: AirPods arc → safety guardrail → eval.
- **Bonus:** show a **QR code** so judges open the PWA on their own phones.
- If the box/network drops: laptop-local fallback (heuristic + manual toggle + local TTS). If all else fails: the backup video.

## Data layer: sessions + long-term memory + quick auth (Redis + Postgres)

**Isolation decision (important):** CityPulse owns Postgres `:5432/:5433` and Redis `:6379` (db0/db1) — off-limits per `../CLAUDE.local.md`. Docker IS available on the box, so we run **our own** isolated instances and never touch CityPulse's:
- **`lumi-postgres`** — Postgres container, host port **5434**, db `lumi`, user `lumi` (durable record + memory).
- **`lumi-redis`** — Redis container, host port **6380** (sessions cache, TTS/answer cache, trajectory, rate-limit).
- Defined in `infra/docker-compose.yml`; both bind `127.0.0.1` only. Credentials live in `.env`/`CLAUDE.local.md` (gitignored).

**Where it runs:** the **box `server/`** owns the data layer (localhost access to both containers). The Mac `app/` stays thin and passes `guest_token` + `session_id` through. When the box is down, the Mac fallback runs memory-less (degraded, by design).

**Postgres schema (created on startup if absent):**
- `guests(guest_id uuid pk, device_hash text unique, name text, room text, created_at, last_seen, profile_summary text)`
- `sessions(session_id uuid pk, guest_id fk, started_at, last_at, channel)`
- `turns(id bigserial pk, session_id fk, guest_id fk, ts, user_text, emotion, vad jsonb, prosody jsonb, guard jsonb, reply, model, latency_ms)` ← full observability + eval material
- `memories(id bigserial pk, guest_id fk, ts, kind text['complaint'|'preference'|'fact'], text, status text['open'|'resolved'])`

**Redis (separate instance) usage:** `sess:{session_id}` → messages JSON (TTL 6h); `cache:tts:{hash(text,emotion)}`; `traj:{session_id}` → recent V·A·D; simple rate-limit keys.

**Per-turn memory flow (in box `/answer`):** resolve guest → load (recent turns + open complaints + `profile_summary`) → inject a short **"guest memory"** block into the qwen3 system prompt → generate reply → persist the turn → extract/update memory (a small qwen3 "what should I remember?" pass, or heuristic) → cache session in Redis. This makes *"any update on my luggage?"* work across sessions and lets Lumi greet a returning guest.

**Quick auth (no passwords/OAuth):**
- **Device token** = identity. On first visit the app issues `guest_token = guest_uuid + "." + HMAC(LUMI_AUTH_SECRET, guest_uuid)`, stored in `localStorage`, sent as `X-Guest-Token`. The box verifies the HMAC → `guest_id`. Possession-based, instant, un-forgeable.
- **Optional "claim"** (more hotel-real): `POST /claim {room, surname}` checks a seeded `guests` list and binds the device to a named guest. Enriches memory; not required to use Lumi.

**Implementation order (fast):**
1. `infra/docker-compose.yml` → `docker compose up -d` (lumi-postgres :5434, lumi-redis :6380).
2. `server/db.py` (psycopg pool + redis client; schema init), `server/memory.py` (load/record/update), `server/auth.py` (issue/verify token, `/claim`).
3. Wire box `/answer` to use guest+session+memory; add `/guest` (issue token) + `/claim`.
4. Mac `app/`: store `guest_token` in `localStorage`, send it + a `session_id`; surface "returning guest" in the UI.
5. Add `psycopg[binary]`, `redis` to `server/requirements.txt`; run the secret-scan before any push.
