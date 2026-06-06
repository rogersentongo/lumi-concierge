#!/usr/bin/env python3
"""Eval: run a CREMA-D subset through /analyze and compare ACOUSTIC vs TEXT-only emotion.

CREMA-D actors say the SAME emotionally-neutral sentences in 6 emotions, so a text
classifier can't tell them apart (it flatlines) while the acoustic model tracks the
truth — exactly the bonus-prize argument. Labels come from the filenames.

Run on the box (where /analyze is localhost):
    INFERENCE_AUTH_TOKEN=... python3 eval_run.py [N]
Writes eval_results.json (rsync it to the Mac so /api/eval serves the Proof tab).
"""
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.request

ANALYZE = os.environ.get("ANALYZE_URL", "http://127.0.0.1:8100/analyze")
TOKEN = os.environ.get("INFERENCE_AUTH_TOKEN", "")
# CREMA-D wavs are Git LFS — the media host serves the real audio (raw returns a pointer).
RAW = "https://media.githubusercontent.com/media/CheyneyComputerScience/CREMA-D/master/AudioWAV/"
NOVERIFY = ssl._create_unverified_context()
UA = "Mozilla/5.0"

MAP = {"ANG": "angry", "DIS": "frustrated", "FEA": "anxious", "HAP": "happy", "NEU": "neutral", "SAD": "sad"}
LABELS = ["ANG", "DIS", "FEA", "HAP", "NEU", "SAD"]


def clip_names(n_actors=20):
    out = []
    for a in range(1001, 1001 + n_actors):
        for emo in LABELS:
            lvl = "XX" if emo == "NEU" else "HI"
            out.append((f"{a}_IEO_{emo}_{lvl}.wav", emo))
    return out


def download(name):
    req = urllib.request.Request(RAW + name, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20, context=NOVERIFY) as r:
        data = r.read()
    if len(data) < 3000:
        return None
    p = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    with open(p, "wb") as f:
        f.write(data)
    return p


def analyze(path):
    boundary = "----lumiEval"
    with open(path, "rb") as f:
        data = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; "
            f"filename=\"c.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(ANALYZE, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    t = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()), int((time.time() - t) * 1000)


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 48
    rows, lat = [], []
    for name, code in clip_names():
        if len(rows) >= target:
            break
        try:
            p = download(name)
            if not p:
                continue
            j, ms = analyze(p)
            os.remove(p)
            ac = (j.get("sources") or {}).get("acoustic")
            tx = (j.get("lexical") or {}).get("emotion")
            rows.append({"true": MAP[code], "acoustic": ac, "text": tx})
            lat.append(ms)
            print(f"  {name}  true={MAP[code]:10s} acoustic={str(ac):10s} text={str(tx):10s} {ms}ms")
        except Exception as e:
            print("  skip", name, repr(e)[:60])
    if not rows:
        sys.exit("no clips analyzed — is the server up on :8100?")

    acc_ac = sum(r["acoustic"] == r["true"] for r in rows) / len(rows)
    acc_tx = sum(r["text"] == r["true"] for r in rows) / len(rows)
    per = []
    for code in LABELS:
        tr = MAP[code]
        sub = [r for r in rows if r["true"] == tr]
        if sub:
            per.append({"label": code,
                        "acoustic": round(sum(r["acoustic"] == tr for r in sub) / len(sub), 3),
                        "text": round(sum(r["text"] == tr for r in sub) / len(sub), 3)})
    lat.sort()
    res = {
        "available": True,
        "dataset": "CREMA-D (IEO, same words)",
        "n": len(rows),
        "acoustic_accuracy": round(acc_ac, 3),
        "text_accuracy": round(acc_tx, 3),
        "per_emotion": per,
        "latency_ms": {"p50": lat[len(lat) // 2], "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))]},
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_results.json"), "w") as f:
        json.dump(res, f, indent=2)
    print("\n" + json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
