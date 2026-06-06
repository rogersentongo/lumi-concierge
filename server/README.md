# Lumi — GPU inference server (`server/`)

FastAPI service on **port 8100**, run **on the GPU box**, pinned to **GPU 1**. It does the heavy, quality inference and calls the box's localhost Ollama **read-only**. Host/SSH details are in the repo-root `../CLAUDE.local.md` (gitignored) — never put IPs/hostnames in this file.

## Endpoints
- `GET /healthz` → `{ ok, ollama }`
- `POST /analyze` (multipart `audio`) → `{ transcript, emotion, vad, prosody, guard, disagree, confidence, sources, lexical }`
- `POST /answer` `{ messages, emotion }` → `{ reply, emotion }`

Both POSTs require `Authorization: Bearer <INFERENCE_AUTH_TOKEN>` when that env var is set.

## Prereqs on the box
- **ffmpeg** (`ffmpeg -version`) — used to transcode the browser's webm/opus to 16k WAV.
- **CUDA** GPUs; GPU 1 (the spare) free — we pin to it.
- **Ollama** already running locally with `qwen3:4b-instruct` (read-only; do not reconfigure).
- **uv** at `/home/rogersentongo/.local/bin/uv` (see `../CLAUDE.local.md`).

## Install (isolated venv, in this folder)
```bash
export PATH=/home/rogersentongo/.local/bin:$PATH
cd ~/Dev/voice-emotion-hack/server
uv venv
# 1) install torch built for the box's CUDA FIRST (funasr needs it). Example for CUDA 12.1:
uv pip install torch --index-url https://download.pytorch.org/whl/cu121
# 2) then the rest
uv pip install -r requirements.txt
```

## Run (pinned to GPU 1, with an auth token)
```bash
export PATH=/home/rogersentongo/.local/bin:$PATH
cd ~/Dev/voice-emotion-hack/server
CUDA_VISIBLE_DEVICES=1 \
INFERENCE_AUTH_TOKEN="choose-a-long-random-token" \
OLLAMA_MODEL=qwen3:4b-instruct \
uv run uvicorn app:app --host 0.0.0.0 --port 8100
```
First `/analyze` downloads the emotion2vec+ and Whisper models (additive — does not touch CityPulse). Keep `CUDA_VISIBLE_DEVICES=1` so we never compete on GPU 0.

## Env vars
| var | default | note |
|---|---|---|
| `INFERENCE_AUTH_TOKEN` | (unset = open) | bearer token; set it in prod/demo |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | localhost only |
| `OLLAMA_MODEL` | `qwen3:4b-instruct` | read-only |
| `WHISPER_MODEL` | `small` | tiny/base/small/medium/large-v3 |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE` | `cuda` / `float16` | |
| `EMOTION_CUDA_DEVICE` | `1` | informational; pin via `CUDA_VISIBLE_DEVICES=1` |

## Smoke test
```bash
TOKEN="the-token-you-set"
curl -s localhost:8100/healthz

curl -s -X POST localhost:8100/answer \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"My AirPods are gone from the lounge."}],"emotion":"anxious"}'

# analyze needs a real audio file (wav/webm/m4a all fine — ffmpeg transcodes):
curl -s -X POST localhost:8100/analyze \
  -H "Authorization: Bearer $TOKEN" -F audio=@sample.wav
```

## Wire the Mac app to it
In the repo-root `.env` (on the Mac): set `EMOTION_SERVER_URL` to the box's Tailscale URL on **:8100** and `INFERENCE_AUTH_TOKEN` to the same token. The Mac `app/` then proxies `/api/analyze` + `/api/answer` here; until then it uses the local fallback. **Expose only :8100** (Tailscale or one dedicated tunnel) — never Ollama/SearXNG/SSH.
