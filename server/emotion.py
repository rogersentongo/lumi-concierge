"""Lumi server — the emotion engine.

  acoustic_emotion()  emotion2vec+ large (FunASR) on the waveform  [the 0.60 term]
  prosody()           librosa pitch / energy / rate features        [the 0.15 heuristic]
  fuse()              acoustic-dominant fusion -> emotion + V-A-D + guardrail signals

The lexical term (0.25) is computed in llm.py and passed into fuse().
Heavy imports are lazy so the FastAPI app boots without the models present.
"""
import functools
import os

import numpy as np

# emotion2vec+ large emits 9 classes (en/zh). Map them to Lumi's vocab.
LABEL_MAP = {
    "angry": "angry", "生气": "angry",
    "happy": "happy", "开心": "happy",
    "sad": "sad", "难过": "sad",
    "neutral": "neutral", "中立": "neutral",
    "fearful": "anxious", "恐惧": "anxious",
    "disgusted": "frustrated", "厌恶": "frustrated",
    "surprised": "eager", "吃惊": "eager",
    "other": "neutral", "其他": "neutral",
    "unknown": "neutral", "<unk>": "neutral",
}

# approximate valence / arousal / dominance per emotion, each in [-1, 1]
VAD = {
    "neutral":    (0.05, -0.10,  0.00),
    "calm":       (0.25, -0.35,  0.10),
    "happy":      (0.65,  0.40,  0.30),
    "eager":      (0.45,  0.45,  0.15),
    "relieved":   (0.55,  0.05,  0.15),
    "expectant":  (0.05,  0.15,  0.10),
    "sad":        (-0.55, -0.30, -0.40),
    "anxious":    (-0.45,  0.45, -0.45),
    "frustrated": (-0.55,  0.50,  0.35),
    "angry":      (-0.55,  0.70,  0.50),
    "distress":   (-0.75,  0.35, -0.55),
}

# acoustic distress cluster (feeds the guardrail acoustic-distress signal).
# FEAR only — NOT sadness or anger. A sad or angry guest needs empathy/de-escalation,
# not the crisis/safety path; only genuine fear corroborates a safety crisis.
DISTRESS_LABELS = {"anxious"}

# Model + hub are env-configurable. ModelScope ("ms") can be very slow from the US;
# HuggingFace ("hf") is usually far faster. Smaller variants (base/seed) download quicker.
EMOTION_MODEL = os.environ.get("EMOTION_MODEL", "iic/emotion2vec_plus_large")
EMOTION_HUB = os.environ.get("EMOTION_HUB", "ms")


@functools.lru_cache(maxsize=1)
def _emo_model():
    from funasr import AutoModel
    return AutoModel(model=EMOTION_MODEL, hub=EMOTION_HUB, disable_update=True)


def _norm_label(raw) -> str:
    s = str(raw).split("/")[0].strip().lower()
    return LABEL_MAP.get(s, LABEL_MAP.get(str(raw).strip(), "neutral"))


def acoustic_emotion(wav_path: str):
    """emotion2vec+ -> {emotion, confidence, scores, distress}. None on failure."""
    try:
        res = _emo_model().generate(wav_path, granularity="utterance", extract_embedding=False)
        item = res[0]
        labels = item.get("labels", [])
        scores = list(item.get("scores", []))
        if not labels or not scores:
            return None
        agg = {}
        for lbl, sc in zip(labels, scores):
            mapped = _norm_label(lbl)
            agg[mapped] = agg.get(mapped, 0.0) + float(sc)
        top = max(agg, key=agg.get)
        distress = sum(float(sc) for lbl, sc in zip(labels, scores)
                       if _norm_label(lbl) in DISTRESS_LABELS)
        return {
            "emotion": top,
            "confidence": round(float(agg[top]), 3),
            "scores": {k: round(v, 3) for k, v in agg.items()},
            "distress": round(min(distress, 1.0), 3),
        }
    except Exception:
        return None


def prosody(wav_path: str):
    """librosa pitch / energy / rate (% vs a rough reference) + an arousal proxy."""
    try:
        import librosa
        y, sr = librosa.load(wav_path, sr=16000, mono=True)
        if y.size < int(sr * 0.2):
            return None
        rms = float(np.mean(librosa.feature.rms(y=y)))
        zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
        try:
            f0, _vflag, _vp = librosa.pyin(y, fmin=70, fmax=400, sr=sr)
            f0v = f0[~np.isnan(f0)]
            pitch_hz = float(np.mean(f0v)) if f0v.size else 0.0
            pitch_std = float(np.std(f0v)) if f0v.size else 0.0
        except Exception:
            pitch_hz = pitch_std = 0.0

        REF_PITCH, REF_RMS, REF_ZCR = 165.0, 0.04, 0.08
        clamp = lambda x: int(max(-100, min(100, round(x))))
        pitch_pct = clamp((pitch_hz / REF_PITCH - 1) * 100) if pitch_hz else 0
        energy_pct = clamp((rms / REF_RMS - 1) * 100)
        rate_pct = clamp((zcr / REF_ZCR - 1) * 100)
        arousal = max(-1.0, min(1.0, (rms / REF_RMS - 1) * 0.6 + (pitch_std / 40.0) * 0.4))
        return {"pitch": pitch_pct, "energy": energy_pct, "rate": rate_pct, "arousal": round(arousal, 3)}
    except Exception:
        return None


def _blend_vad(weighted):
    v = a = d = wsum = 0.0
    for emo, w in weighted:
        if emo not in VAD or w <= 0:
            continue
        ev, ea, ed = VAD[emo]
        v += ev * w; a += ea * w; d += ed * w; wsum += w
    if wsum == 0:
        return {"v": 0.0, "a": 0.0, "d": 0.0}
    return {"v": round(v / wsum, 2), "a": round(a / wsum, 2), "d": round(d / wsum, 2)}


def fuse(acoustic, lexical, pros):
    """Acoustic-dominant fusion: 0.60 acoustic + 0.25 lexical + 0.15 prosody heuristic."""
    ac_emo = acoustic["emotion"] if acoustic else None
    lex_emo = (lexical or {}).get("emotion", "neutral")
    final = ac_emo or lex_emo or "neutral"

    weighted = []
    if ac_emo:
        weighted.append((ac_emo, 0.60))
    weighted.append((lex_emo, 0.25))
    vad = _blend_vad(weighted)
    if pros and "arousal" in pros:
        vad["a"] = round(0.85 * vad["a"] + 0.15 * pros["arousal"], 2)

    ac_distress = float(acoustic.get("distress", 0.0)) if acoustic else 0.0
    lex_crisis = float((lexical or {}).get("distress", 0.0))
    pros_distress = 0.0
    if pros:
        hi_arousal = max(0.0, (pros.get("arousal", 0.0) + 1) / 2)      # 0..1
        flat = 1.0 if pros.get("energy", 0) < -40 else 0.0
        pros_distress = max(0.0, min(1.0, hi_arousal * 0.5 + flat * 0.5))
    # Crisis gate: lexical crisis is the primary trigger (qwen3 catches danger/self-harm
    # words). Acoustic fear only fires when corroborated by some lexical distress, so an
    # emotional-but-safe guest (sad / angry / anxious about a lost item) never trips Red.
    fired = (
        lex_crisis >= 0.55
        or (ac_distress >= 0.55 and lex_crisis >= 0.25)
        or pros_distress >= 0.75
    )

    disagree = bool(
        ac_emo and lex_emo and ac_emo != lex_emo
        and ac_emo in ("angry", "frustrated", "anxious", "distress")
        and lex_emo in ("neutral", "happy")
    )

    return {
        "emotion": final,
        "vad": vad,
        "confidence": acoustic.get("confidence") if acoustic else None,
        "guard": {
            "acoustic_distress": round(ac_distress, 3),
            "lexical_crisis": round(lex_crisis, 3),
            "prosody_distress": round(pros_distress, 3),
            "fired": fired,
        },
        "disagree": disagree,
        "sources": {"acoustic": ac_emo, "lexical": lex_emo},
    }


def emotion_ok() -> bool:
    try:
        _emo_model()
        return True
    except Exception:
        return False
