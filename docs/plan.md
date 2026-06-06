# Lumi — Build Plan (standalone)

Focused, public-safe plan for the AI Tinkerers Emotionally-Intelligent-Voice hackathon (Sat Jun 6). Server access + isolation specifics are in `../CLAUDE.local.md` (gitignored).

> **Lumi** is the emotionally-intelligent **voice concierge** for a (fictional) luxury hotel, **The Lumen**. It hears *how* a guest speaks — not just the words — and adapts its tone, pacing, word choice, what it shows on screen, and its escalation strategy. Hospitality is dense with situations where the *same request* carries very different emotion, which is exactly where an emotion layer earns its keep.

## Goal & scope
One strong core (emotion-aware concierge) + two bolt-ons (guardrail, eval) → eligible for 1st **and** all three bonus prizes. The judged thing is the **emotion layer**; the brain/UI/media are scaffolding in service of it.

## Why hospitality
A hotel gives us the full emotional spread the judges asked for ("handle an angry caller differently than a confused one"):
- **Dining / indecision** — "what should I get for breakfast?" → hungry, indecisive → upbeat suggestions + appetizing food imagery.
- **Lost belongings** — a full *emotional arc in one conversation* (anxious → angry → relieved → happy). The hero demo.
- **Amenities** — pool, rooftop, lounge, spa → curious/excited → warm, vivid, with venue imagery.
- **Safety / medical** — a guest who feels unsafe or unwell → the **guardrail**, now in-domain (route to security / medical / front-desk human + emergency services).

## Architecture
`Browser (mic + UI) → Mac app (logic, ElevenLabs key, local fallback) → GPU infer service :8100 (emotion + qwen3) → ElevenLabs TTS`. See `../CLAUDE.md` §4. The technical spine is unchanged by the hospitality framing — only the persona, scenarios, media, and copy are hospitality-specific.

## Emotion engine (the heart — built for reliability)
- **Acoustic (primary):** `emotion2vec+ large` via FunASR — one call, 9 labeled emotions, sub-second on GPU. *(Alt one-box: SenseVoice-Small = emotion + audio-events + ASR.)*
- **Continuous:** `audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim` → valence/arousal/dominance (powers the on-screen V-A-D dot + the per-conversation trajectory). *(CC-BY-NC — demo only.)*
- **Lexical:** qwen3 on the transcript → JSON `{emotion, intensity, distress, evidence}`.
- **Fusion (acoustic-dominant):** `0.6·acoustic + 0.25·lexical + 0.15·heuristic`; if acoustic & lexical disagree (calm words, angry tone) surface both and let acoustic win — the strongest "it heard *how* I said it" moment.
- **Fallback ladder (cannot fail):** model → **openSMILE/librosa prosody heuristic** (pitch+energy+rate vs. a 5-sec calibrated baseline; pure math, self-explaining) → **manual emotion toggle** → if ElevenLabs down, **local TTS** → if network/server down, **pre-recorded backup video**.
- ⚠️ Avoid **Hume** (Expression Measurement API sunsets Jun 14). ElevenLabs **Scribe has no emotion field** — emotion is our layer.

## Conversation modality
- **Turn-based push-to-talk is the reliable core** (clean, segmented utterances → strong acoustic emotion read).
- **"Call mode" (stretch):** VAD/auto-endpointing (silero-vad / webrtcvad / browser silence detection) for hands-free, call-like back-and-forth — still turn-based under the hood, so the emotion read stays clean; **PTT remains the fallback**. Defer barge-in.
- **Multi-turn is the spine**, not a bolt-on: conversation memory (qwen3 `messages` array) + one stored emotion read per turn (→ trajectory + escalation). The guardrail's escalation tracking *requires* this.
- ⚠️ Don't hand the whole audio loop to ElevenLabs Conversational AI — it would sideline our emotion pipeline (the differentiator). Use ElevenLabs for expressive **TTS** only; own the emotion read.

## Response adaptation
- **Text:** inject a whitelisted tone directive (`warm | upbeat | empathetic | de-escalating | informative`) into the qwen3 concierge system prompt. Word choice + pacing + escalation strategy shift with emotion.
- **Voice (spoken inline):** every agent reply is **spoken in the chat** — each bubble has inline play + waveform; **ElevenLabs Flash v2.5** `voice_settings` chosen by detected emotion (`stability` **down** ~0.25–0.35 for range; `style`/`speed` per state). In call mode replies auto-play (browser autoplay unlocks after the guest's first tap/hold gesture). Make the Lumi voice via **Voice Design** (text prompt → voice, ~1 min).
- **Media (emotion-gated):** the concierge is screen + voice (in-room tablet / lobby kiosk / app), so food and venue images *belong*. Emotion gates them: hungry/indecisive → appetizing food tiles; distressed/angry → **suppress media**, stay calm and action-focused. Media is always subordinate to the emotion layer.
  - **Reliability:** pre-stage a small curated asset pack (The Lumen's lounge, rooftop, pool, a few dishes) so the demo is deterministic and safe on a projector. Keep **SearXNG image search** as an optional live flourish on a *vetted* query only — never a dependency.

## Bonus modules
- **Guardrail (best guardrail):** OR-gate — acoustic distress OR lexical crisis OR prosody spike → Red support mode; route to **hotel security / medical / front-desk human** + emergency services + persistent "AI assist — not a substitute for emergency services" disclaimer. High-recall, human-in-the-loop. **Note:** service-anger (e.g., the AirPods escalation) is handled by *de-escalation + service recovery*, NOT the crisis guardrail — distinguishing anger from distress is itself a strong "we get emotion" signal.
- **Evaluation (best eval pipeline):** CREMA-D (same sentences × 6 emotions) + self-recorded hospitality lines → per-clip predictions → the headline chart: **text-only classifier flatlines while the acoustic model tracks the true emotion** + a confusion matrix + a latency table. That one chart is the whole anti-wrapper argument, measured.
- **Creative (best creative use):** emotion-gated media curation + the expressive voice that warms/calms/celebrates with the guest.
  - **Stretch (Tavus avatar):** only after the core is bulletproof. Use Tavus as an **emotion-driven output face** (ideally pre-rendered clips: warm greeting / calm de-escalation / safety-concern) whose expression is set by **our** acoustic read — "emotion you can *see*." Do **not** use Tavus CVI as the conversation loop (it would own STT/turn-taking/LLM/TTS → wrapper + a competing visual emotion signal + live-video reliability risk on the venue network). Verify first whether a Tavus avatar can lip-sync to external ElevenLabs audio with an affect param; if not, stick to pre-rendered clips or skip.

## Demo (5 min) — 2 surfaces: the live concierge + the eval dashboard
1. **Hero (the AirPods arc, one conversation):** guest lost AirPods in the Conservatory lounge.
   - *anxious* → Lumi reassures, gathers details, shows the lounge.
   - asks for **security footage** → Lumi declines *empathetically* (policy: footage released only after a police report/court order) and offers an alternative (dispatch staff + check lost & found).
   - *angry* ("that's not good enough") → Lumi **de-escalates**, validates, commits to the action + a timeline. (Guardrail visibly *watching but correctly NOT firing* — anger ≠ crisis.)
   - *relieved → happy* — staff found them at the front desk → warm, celebratory, offers room delivery.
   The V-A-D trajectory drifts to anger and recovers; emotion timeline + inline spoken replies throughout.
2. **Dining (media beat):** "what's good for breakfast?" → upbeat suggestions + appetizing food tiles (emotion-gated).
3. **Guardrail (safety beat):** a guest feels unsafe / unwell → Red support mode → security/medical/human + disclaimer.
4. **Proof (eval):** the text-flatlines-vs-acoustic-tracks chart + a latency line + "$0 per inference, on our own GPUs."

**Distribution:** the concierge is a **domain-free PWA** served over a free HTTPS tunnel (cloudflared/ngrok) — judges scan a **QR**, open it in the browser, optionally Add to Home Screen (no app store, no sign-in). Full mechanics in [`implementation.md`](implementation.md).

## Today (hacking 10:30→18:00)
| Time | Focus |
|---|---|
| 10:30–12:00 | `server/` on the box: `/analyze` (emotion fusion) + `/answer` (qwen3 concierge, tone-adapted, **messages array**), pinned to GPU 1. Validate with curl. |
| 12:00–13:30 | Mac `app/`: mic capture + call server + **ElevenLabs TTS** with per-emotion voice_settings. Hear the same text two ways. |
| 13:30–15:30 | Web UI: hold-to-talk, V-A-D dot + trajectory + prosody bars, transcript with **inline spoken replies**, emotion-gated **media tiles**. |
| 15:30–16:30 | Bonus modules: guardrail (distress → Red + security/medical/human) and the eval scorecard chart. |
| 16:30–17:15 | Reliability: heuristic + manual-toggle + local-TTS fallbacks; pre-warm; curate demo asset pack; **record the YouTube/backup video**. |
| 17:15–18:00 | Secret-scan + push the public repo; upload video; rehearse the beats; submit links. Due 18:00. |

## Risks
- **Venue→server network** — primary risk; Tailscale usually fine, but laptop-local fallback (heuristic + manual toggle + local TTS) means the demo survives a drop. Keep a backup video.
- **Acoustic model on natural speech** — fine for scripted, over-acted beats; heuristic + manual toggle backstop.
- **Media on a projector** — use the curated asset pack, not live web image search, for the demo path.
- **ElevenLabs credits/latency** — Flash v2.5 (cheap, fast); cache audio by (text,emotion); local-TTS fallback.
- **Scope creep** — food/venue imagery and "call mode" are seductive; the emotion read is where the prizes are. Don't trade emotion-layer polish for UI polish.
- **Isolation** — see `../CLAUDE.local.md`: separate folder/venv/port, GPU-1 pin, read-only Ollama/SearXNG, expose only :8100.
