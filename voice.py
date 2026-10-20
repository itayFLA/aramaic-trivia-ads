# -*- coding: utf-8 -*-
"""הקראה (TTS) + כלי אודיו למחולל הפרסומות.

ספקים:
  gemini - Google Gemini TTS (דורש GEMINI_API_KEY, מסלול חינמי ב-AI Studio)
  edge   - edge-tts (בלי מפתח, שירות לא רשמי)
  mock   - לבדיקות בלבד: צליל במקום דיבור
  auto   - gemini אם יש מפתח, ואם נכשל עובר ל-edge
"""
import asyncio
import base64
import json
import os
import subprocess
import tempfile
import urllib.request

import numpy as np

SR = 44100  # קצב דגימה פנימי

GEMINI_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Orus")
GEMINI_STYLE = os.environ.get(
    "GEMINI_STYLE",
    "Read this Hebrew text aloud in an energetic, upbeat promo style, at a natural pace. "
    "Pronounce Hebrew and Aramaic words correctly. Text: ")
EDGE_VOICE = os.environ.get("EDGE_VOICE", "he-IL-AvriNeural")


# ------------------------------------------------------------------ אודיו
def load_audio(path):
    """קורא כל קובץ אודיו למערך float מונו ב-44.1kHz."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def save_wav_f32(path, arr):
    raw = np.clip(arr, -1, 1).astype(np.float32).tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                    "-i", "-", path], input=raw, check=True)


def trim_and_normalize(a, peak=0.89, margin=0.03):
    """חותך שקט בקצוות ומנרמל עוצמה."""
    idx = np.where(np.abs(a) > 0.012)[0]
    if len(idx):
        a = a[max(0, idx[0] - int(margin * SR)): idx[-1] + int(margin * SR)]
    m = np.abs(a).max() if len(a) else 0
    return a * (peak / m) if m > 0 else a


# ------------------------------------------------------------------ ספקים
def _gemini(text):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("אין GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": GEMINI_STYLE + text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}},
        },
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    pcm = base64.b64decode(part["data"])
    mime = part.get("mimeType", "")
    rate = 24000
    if "rate=" in mime:
        rate = int(mime.split("rate=")[1].split(";")[0])
    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        f.write(arr.tobytes())
        tmp = f.name
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "1",
                              "-i", tmp, "-ar", str(SR), "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
    finally:
        os.unlink(tmp)
    return np.frombuffer(raw, dtype=np.float32).copy()


def _edge(text):
    import edge_tts

    async def go(path):
        await asyncio.wait_for(edge_tts.Communicate(text, EDGE_VOICE).save(path), timeout=60)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mp3")
        asyncio.run(go(p))
        return load_audio(p)


def _mock(text):
    n = int(SR * (0.35 + 0.07 * len(text)))
    t = np.arange(n) / SR
    return (0.4 * np.sin(2 * np.pi * 220 * t) * np.hanning(n)).astype(np.float32)


def synthesize(text, provider="auto", log=print):
    """מחזיר מערך אודיו (44.1kHz מונו) מנורמל וחתוך. text = הטקסט להקראה."""
    order = {"gemini": ["gemini"], "edge": ["edge"], "mock": ["mock"],
             "auto": ["gemini", "edge"] if os.environ.get("GEMINI_API_KEY") else ["edge"]}[provider]
    last = None
    for name in order:
        for attempt in range(3):
            try:
                a = {"gemini": _gemini, "edge": _edge, "mock": _mock}[name](text)
                if len(a) < SR * 0.15:
                    raise RuntimeError("האודיו קצר מדי / ריק")
                return trim_and_normalize(a)
            except Exception as e:  # noqa
                last = e
                log(f"  [{name}] ניסיון {attempt + 1} נכשל: {type(e).__name__}: {str(e)[:120]}")
        log(f"  עובר לספק הבא אחרי כשל ב-{name}")
    raise RuntimeError(f"כל ספקי הקול נכשלו: {last}")
