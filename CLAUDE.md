# Lumi — Emotionally Intelligent Voice Concierge

> **Hackathon project for the AI Tinkerers "Emotionally Intelligent AI Hackathon" (ElevenLabs + Tavus), NYC — Sat June 6, 2026.**
> **Lumi** is the emotion-aware voice **concierge** for a fictional luxury hotel, **The Lumen**. It hears *how* a guest speaks and adapts tone, pacing, word choice, on-screen media, and escalation strategy.
> This is a **standalone** project. It does **NOT** use or import any CityPulse app/backend code. It *borrows* the home GPU server's already-running models (Ollama qwen3 + SearXNG) as a convenience — strictly read-only and isolated. See **`CLAUDE.local.md`** (gitignored) for server access + the hard isolation rules.

---

## 1. Goal

Build and demo **Lumi**, a hotel voice concierge that **hears *how* a guest speaks — not just the words — and responds differently because of it.** Win a prize; specifically target **1st** + the **bonus prizes** (best guardrail / best evaluation / best creative use of emotion).

The judged thing is the **emotional layer**, so that's where ~all effort goes. Everything else (the LLM "brain", web UI, food/venue media) is scaffolding in service of the emotion layer. **Hospitality is the vehicle**, chosen because a hotel is dense with situations where the *same request* carries very different emotion (hungry guest deciding on breakfast; a guest whose AirPods were taken from the lounge swinging from anxiety → anger → relief; a guest who feels unsafe).

## 2. The hackathon mandate (what judges reward)

- **"Make it hear something. Make it respond differently because of what it heard."** → the hero demo beat: *same words, different emotion → different response.*
- **"Reliability wins. Bring a demo that works end to end."** → turn-based (push-to-talk), tiered fallbacks that **cannot fail** (model → DSP heuristic → manual toggle), pre-recorded backup video.
- **"No chatbot wrappers. No 'plug in an LLM and call it an agent.' Not TTS demos."** → emotion is a **first-class signal**: a real acoustic pipeline drives tone, pacing, word choice, and safety routing.
- **Submission:** public **GitHub repo** + **YouTube demo video** + **live demo** (5 min). Judging 7pm. Prizes: DJI drone / AirPods / ElevenLabs credits + the 3 bonus prizes.
- Schedule: hacking 10:30am→6:00pm; submissions due 6:00pm; demos 6:30pm.

## 3. Concept

A real-time, **multi-turn**, emotion-aware **hotel concierge (Lumi)**. The guest holds-to-talk (or uses hands-free "call mode"); Lumi transcribes, reads the **acoustic emotion**, fuses it with a lexical read, generates a **tone-adapted** reply, **speaks it back inline** with **ElevenLabs expressive TTS** (voice settings chosen by the detected emotion), and — when it helps — surfaces **emotion-gated media** (food/venue tiles). Conversation memory + a per-turn emotion read drive a V-A-D **trajectory** across the conversation. Two bolt-on modules make us eligible for the bonus prizes:
- **Guardrail** — detect genuine distress/escalation in the voice and route in-domain (**hotel security / medical / front-desk human** + emergency services + disclaimer). Service-anger (e.g., the AirPods escalation) is handled by **de-escalation + service recovery**, NOT the crisis guardrail — distinguishing anger from distress is itself a strong "we get emotion" signal.
- **Evaluation** — a scorecard proving the system reacts to *tone, not words* (the "text-flatlines-vs-acoustic-tracks" chart on CREMA-D / self-recorded hospitality clips).

See [`docs/plan.md`](docs/plan.md) for the full plan (emotion engine, fusion, fallback ladder, modality, media, ElevenLabs specifics, demo beats, hour-by-hour).

## 4. Architecture

```
Browser  (mic capture + UI)
  │ local
Mac "app" service  ── owns demo logic, holds the ElevenLabs key, has the LOCAL FALLBACK
  │ HTTPS over Tailscale (or a dedicated tunnel) → GPU inference server :8100
GPU "infer" service  (port 8100, emotion model pinned to the spare GPU)
  ├─ POST /analyze : audio → acoustic emotion (emotion2vec+) + prosody (openSMILE) + lexical (qwen3) → fused emotion + V-A-D
  └─ POST /answer  : (messages[], emotion) → qwen3 (Ollama, read-only) → tone-adapted reply (streamed)
        + optional emotion-gated media (curated assets; SearXNG image search optional, never a dependency)
        └─ calls the server's localhost Ollama + SearXNG (READ-ONLY)
ElevenLabs (cloud) ← called by the Mac app for expressive TTS, spoken inline (voice_settings driven by emotion)
```

**Why this split:** the GPU server does the heavy/quality inference (leverages real GPUs + models already loaded, ~$0/inference — a genuine differentiator); the Mac app keeps the ElevenLabs key off the exposed server and owns the **local fallback** so a network/server hiccup can't kill the demo.

## 5. Self-hosted vs. sponsor (deliberate)

| Capability | Choice | Notes |
|---|---|---|
| STT | self-host (faster-whisper or Scribe) | raw audio needed for acoustic emotion |
| **Emotion read** | **self-host** (emotion2vec+ / openSMILE / qwen3 lexical) | the theme; no good sponsor API (Hume sunset Jun 14) |
| LLM "brain" | self-host (Ollama **qwen3:4b-instruct**) | already on the server |
| **Expressive TTS** | **ElevenLabs** (Flash v2.5 + Voice Design) | the one thing worth buying; spoken **inline** per reply; `stability` ↓ = more emotional range |
| Media (emotion-gated) | curated asset pack (+ optional SearXNG image search) | food/venue tiles; gated by emotion; pre-staged for the demo, never a dependency |
| Avatar (stretch) | Tavus | only if core is bulletproof; use as an **emotion-driven output face** (pre-rendered clips) whose expression is set by OUR acoustic read — NOT as the conversation loop (CVI owning STT/turn-taking/LLM/TTS would make us a wrapper + add a competing visual emotion detector + network-video reliability risk) |
| Cache (optional) | Redis | sponsor; on the server — use a **dedicated DB index**, not CityPulse's |

## 6. 🔒 Isolation principles (DO NOT disrupt CityPulse; DO NOT expose anything)

This project shares a physical server with CityPulse. Hard rules (specifics + host details in `CLAUDE.local.md`):
- **Separate everything** — own folder, own venv, own port (8100), own `.env`. Never edit/import CityPulse repos, venvs, services, DBs.
- **Read-only** use of the shared Ollama + SearXNG. Never remove/modify models; only additive `ollama pull` if ever needed.
- **Pin the new GPU process to the spare GPU** so it never competes with CityPulse.
- **Never expose internal services publicly** — not Ollama (no auth!), not SearXNG, not SSH, not any CityPulse port. If judges need remote access, tunnel **only** port 8100, with its own auth token.
- **Secrets** live in `.env` (gitignored) — never reused from CityPulse, never committed.

## 7. Repo hygiene (this becomes a PUBLIC repo)

- `.env` and **`CLAUDE.local.md` are gitignored** — they hold keys + the server host/IP/ports. Keep them out of git.
- No server IPs, SSH commands, internal hostnames/ports, or keys in any committed file (this `CLAUDE.md`, `README.md`, `docs/`).
- Before pushing: run a **secret scan** (`gitleaks detect` / `detect-secrets scan`) and grep for the server host/IP + any internal identifiers (scan list is in `CLAUDE.local.md`).

## 8. Build / run

1. `cp .env.example .env` and fill in (ElevenLabs key, server URL + auth — see `CLAUDE.local.md`).
2. `server/` runs on the **GPU box** (see `server/README` / `CLAUDE.local.md` for the isolated launch + GPU pinning).
3. `app/` runs on the **Mac** (serves the web UI, proxies to the server, calls ElevenLabs, owns the fallback).
4. `git init` here and push to a **new, empty public repo** for submission (do NOT fork/clone any CityPulse repo — fresh history only).

## 9. The demo (5 min) + reliability ladder

Two surfaces: the **live concierge** (one screen — multi-turn, adapts, speaks inline, shows media, flips to Red) + the **eval dashboard**.

1. **Hero (the AirPods arc, one conversation):** a guest's AirPods were taken from the Conservatory lounge — *anxious* → Lumi reassures + shows the lounge; guest asks for **security footage** → Lumi declines empathetically (policy: footage only after a police report/court order) + offers an alternative; *angry* → Lumi **de-escalates** + commits to action (guardrail visibly watching but **correctly not firing** — anger ≠ crisis); *relieved → happy* → staff found them, offers room delivery. V-A-D trajectory drifts to anger and recovers; replies spoken inline.
2. **Dining (media beat):** "what's good for breakfast?" → upbeat suggestions + appetizing food tiles (emotion-gated).
3. **Guardrail (safety beat):** a guest feels unsafe / unwell → UI goes Red → routes to **hotel security / medical / front-desk human** + emergency services + disclaimer.
4. **Proof (eval):** the "text-flatlines-vs-acoustic-tracks" chart + a latency line + the $0/inference note.

**Fallback ladder (say it out loud):** emotion model → **DSP heuristic** (pure math, can't fail) → **manual emotion toggle** → if ElevenLabs down, **local TTS** → if the network/server is down, the **pre-recorded backup video**. (Media uses the curated asset pack, not live search, on the demo path.)
