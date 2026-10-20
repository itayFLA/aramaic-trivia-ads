#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
מחולל פרסומות טריוויה ארמית
=============================
קורא את questions.txt ומפיק קובץ MP4 אחד לכל פרסומת (3 שאלות + סיום קבוע).

שימוש:
    python make_ads.py                    # כל הפרסומות מתוך questions.txt
    python make_ads.py my_file.txt        # קובץ שאלות אחר
    python make_ads.py --only 2           # רק פרסומת מספר 2
    python make_ads.py --preview          # תמונות בדיקה בלבד (מהיר, בלי וידאו)
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, features

# =====================================================================
#  הגדרות שאפשר לשנות
# =====================================================================
END_TEXT = "כמה קלעתם? כתבו בתגובות!"   # הטקסט בסיום הקבוע

# תזמון בשניות - הועתק מהפרסומת המקורית כדי שהסאונד ייצא מסונכרן.
# לכל שאלה: (התחלה, רגע הדגשת התשובה הנכונה). אחרי השאלה השלישית מתחיל הסיום.
Q_START = [0.0, 5.750, 11.317]
Q_HIGHLIGHT = [4.417, 9.750, 16.267]
END_START = 17.917
TOTAL = 23.568

FPS = 60
# =====================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
W, H = 1080, 1920

# צבעים (נמדדו מהפרסומת המקורית)
CARD_FILL = (221, 201, 149)
ROW_FILL = (210, 184, 129)
BORDER = (70, 44, 20)
TEXT = (39, 25, 10)
TEXT_HL = (140, 20, 15)
BADGE = (196, 46, 37)
LETTERS = ["א", "ב", "ג"]

# מיקומים
CARD = (80, 246, 1000, 592)
ROW_TOPS = [675, 864, 1054]
ROW_H = 145
ROW_X = (80, 1001)
BADGE_BOX = 108
BADGE_X = 876
BAR = (200, 1256, 880, 1298)
LOGO_BOX = (865, 12, 1068, 215)

USE_RAQM = features.check("raqm") and not os.environ.get("ADMAKER_FORCE_BASIC")
if not USE_RAQM:
    try:
        from bidi.algorithm import get_display
    except ImportError:
        sys.exit("חסרה החבילה python-bidi. התקן:  pip install python-bidi")

_font_cache = {}


def font(weight, size):
    key = (weight, size)
    if key not in _font_cache:
        path = os.path.join(ASSETS, "fonts", f"Heebo-{weight}.ttf")
        engine = ImageFont.Layout.RAQM if USE_RAQM else ImageFont.Layout.BASIC
        _font_cache[key] = ImageFont.truetype(path, size, layout_engine=engine)
    return _font_cache[key]


_MARKS = re.compile(r"[\u0591-\u05C7]")


def visual(text):
    """ללא raqm: הופך לסדר חזותי ומחזיר כל ניקוד לאחר האות שלו."""
    if USE_RAQM:
        return text
    fixed, pending = [], []
    for ch in get_display(text):
        if _MARKS.match(ch):
            pending.append(ch)  # אחרי ההיפוך הניקוד מופיע לפני האות
        else:
            fixed.append(ch)
            fixed.extend(pending)
            pending = []
    return "".join(fixed)


def text_width(text, f):
    return f.getlength(visual(text), **({"direction": "rtl", "language": "he"} if USE_RAQM else {}))


def draw_centered(draw, cx, cy, text, f, fill):
    kw = {"direction": "rtl", "language": "he"} if USE_RAQM else {}
    draw.text((cx, cy), visual(text), font=f, fill=fill, anchor="mm", **kw)


def wrap(text, f, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if text_width(trial, f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- ציור
_bg_cache = {}


def background():
    if "bg" not in _bg_cache:
        bg = Image.open(os.path.join(ASSETS, "background.png")).convert("RGB")
        bg = ImageOps.fit(bg, (W, H), Image.LANCZOS)
        bg = Image.eval(bg, lambda v: int(v * 0.95))
        _bg_cache["bg"] = bg
    return _bg_cache["bg"].copy().convert("RGBA")


def shadowed_round_rect(img, box, radius, fill, outline, width=4, shadow=8):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (box[0], box[1] + shadow, box[2], box[3] + shadow), radius, fill=(0, 0, 0, 110))
    layer = layer.filter(ImageFilter.GaussianBlur(7))
    img.alpha_composite(layer)
    ImageDraw.Draw(img).rounded_rectangle(box, radius, fill=fill, outline=outline, width=width)


def draw_card_text(img, text):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = CARD
    max_w, max_h = (x1 - x0) - 70, (y1 - y0) - 50
    for size in range(74, 36, -2):
        f = font(800, size)
        lines = wrap(text, f, max_w)
        lh = int(size * 1.2)
        if len(lines) <= 3 and lh * len(lines) <= max_h:
            break
    cy = (y0 + y1) / 2
    top = cy - lh * (len(lines) - 1) / 2
    for i, ln in enumerate(lines):
        draw_centered(d, (x0 + x1) / 2, top + i * lh, ln, f, TEXT)


def draw_rows(img, answers, correct=None, highlight=False):
    d = ImageDraw.Draw(img)
    for i, top in enumerate(ROW_TOPS):
        box = (ROW_X[0], top, ROW_X[1], top + ROW_H)
        shadowed_round_rect(img, box, 26, ROW_FILL, BORDER, 4, 6)
        d = ImageDraw.Draw(img)
        # תג אדום עם אות
        by = top + (ROW_H - BADGE_BOX) // 2
        d.rounded_rectangle((BADGE_X, by, BADGE_X + BADGE_BOX, by + BADGE_BOX), 18,
                            fill=BADGE, outline=BORDER, width=4)
        draw_centered(d, BADGE_X + BADGE_BOX / 2, by + BADGE_BOX / 2 - 4, LETTERS[i],
                      font(900, 78), (255, 255, 255))
        if answers is not None:
            color = TEXT_HL if (highlight and i == correct) else TEXT
            size = 56
            f = font(800, size)
            avail = BADGE_X - ROW_X[0] - 60
            while text_width(answers[i], f) > avail and size > 30:
                size -= 2
                f = font(800, size)
            draw_centered(d, (ROW_X[0] + BADGE_X) / 2, top + ROW_H / 2 - 2, answers[i], f, color)


def draw_bar(img):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (BAR[0], BAR[1] + 4, BAR[2], BAR[3] + 4), 21, fill=(0, 0, 0, 90))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(5)))
    ImageDraw.Draw(img).rounded_rectangle(BAR, 21, fill=(243, 243, 243, 255))


def draw_logo(img):
    logo = Image.open(os.path.join(ASSETS, "logo.png")).convert("RGB")
    logo = logo.crop((26, 26, 486, 486))   # חיתוך הטבעת הבהירה סביב הלוגו
    size = LOGO_BOX[2] - LOGO_BOX[0]
    logo = logo.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((LOGO_BOX[0], LOGO_BOX[1] + 5, LOGO_BOX[2], LOGO_BOX[3] + 5), fill=(0, 0, 0, 100))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    img.paste(logo, (LOGO_BOX[0], LOGO_BOX[1]), mask)


def render_frame(question=None, highlight=False, end=False):
    img = background()
    shadowed_round_rect(img, CARD, 30, CARD_FILL, BORDER, 4, 8)
    if end:
        draw_card_text(img, END_TEXT)
        draw_rows(img, None)
        draw_bar(img)
        draw_logo(img)
    else:
        draw_card_text(img, question["q"])
        draw_rows(img, question["answers"], question["correct"], highlight)
        if not highlight:      # בדוגמה הפס הלבן נעלם ברגע ההדגשה
            draw_bar(img)
    return img.convert("RGB")


# ---------------------------------------------------------------- קריאת הקובץ
def parse(path):
    """קורא קובץ שאלות. כל 3 שאלות רצופות = פרסומת אחת.
    '---' מפריד קבוצות (לא חובה). בשורה אפשר לכתוב:  טקסט-על-המסך || טקסט-להקראה"""
    groups, cur_name, cur_qs, block = [], None, [], []

    def split_say(t):
        if "||" in t:
            d, sp = t.split("||", 1)
            return d.strip(), sp.strip()
        return t.strip(), t.strip()

    def flush_block():
        nonlocal block
        if not block:
            return
        lines = [l for _, l in block]
        n0 = block[0][0]
        if len(lines) != 4:
            sys.exit(f"שורה {n0}: כל שאלה חייבת להיות 4 שורות (שאלה + 3 תשובות), נמצאו {len(lines)}.")
        answers, says, correct = [], [], None
        for j, a in enumerate(lines[1:]):
            if a.startswith("*"):
                if correct is not None:
                    sys.exit(f"שורה {n0}: יותר מתשובה נכונה אחת (כוכבית).")
                correct, a = j, a[1:].strip()
            d, sp = split_say(a)
            answers.append(d)
            says.append(sp)
        if correct is None:
            sys.exit(f"שורה {n0}: לא סומנה תשובה נכונה. הוסף * לפני התשובה הנכונה.")
        qd, qs = split_say(lines[0])
        cur_qs.append({"q": qd, "q_say": qs, "answers": answers, "ans_say": says, "correct": correct})
        block = []

    def flush_group(lineno):
        nonlocal cur_qs, cur_name
        flush_block()
        if cur_qs:
            if len(cur_qs) % 3:
                sys.exit(f"הקבוצה שמסתיימת ב-{lineno} כוללת {len(cur_qs)} שאלות, צריך כפולה של 3.")
            groups.append((cur_name, cur_qs))
        cur_qs, cur_name = [], None

    with open(path, encoding="utf-8-sig") as fh:
        for n, raw in enumerate(fh, 1):
            s = raw.rstrip("\n").rstrip("\r").strip()
            if s == "---":
                flush_group(n)
            elif s.startswith("//"):
                continue
            elif s.startswith("#") and not block:
                cur_name = s.lstrip("#").strip()
            elif s == "":
                flush_block()
            else:
                block.append((n, s))
    flush_group("הסוף")
    ads = []
    for name, qs in groups:
        chunks = [qs[i:i + 3] for i in range(0, len(qs), 3)]
        for c in chunks:
            ads.append({"name": name if len(chunks) == 1 else None, "questions": c})
    return ads


# ---------------------------------------------------------------- וידאו
def build_video(ad, out_path, tmp):
    frames = []
    for i, q in enumerate(ad["questions"]):
        for tag, hl in (("n", False), ("h", True)):
            p = os.path.join(tmp, f"q{i}{tag}.png")
            render_frame(q, hl).save(p)
            frames.append((p, hl))
    end_p = os.path.join(tmp, "end.png")
    render_frame(end=True).save(end_p)

    seq = []
    for i in range(3):
        nxt = Q_START[i + 1] if i < 2 else END_START
        seq.append((os.path.join(tmp, f"q{i}n.png"), Q_HIGHLIGHT[i] - Q_START[i]))
        seq.append((os.path.join(tmp, f"q{i}h.png"), nxt - Q_HIGHLIGHT[i]))
    seq.append((end_p, TOTAL - END_START))

    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p, d in seq:
            fh.write(f"file '{p.replace(chr(92), '/')}'\nduration {d:.4f}\n")
        fh.write(f"file '{seq[-1][0].replace(chr(92), '/')}'\n")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
           "-i", os.path.join(ASSETS, "sound.m4a"),
           "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-c:a", "aac", "-b:a", "160k", "-t", f"{TOTAL:.3f}", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)


def build_video_voice(ad, out_path, tmp, provider):
    """פרסומת עם הקראה: שאלה -> תקתוק -> תשובה נכונה, והתזמון נגזר מאורך ההקראה."""
    import numpy as np
    import voice

    SR = voice.SR
    tick = voice.load_audio(os.path.join(ASSETS, "tick.wav"))
    end_snd = voice.load_audio(os.path.join(ASSETS, "end_sound.wav"))
    PAD, GAP, TICK_LEN, HL_AFTER, ANS_AFTER, TAIL = 0.25, 0.40, 1.60, 0.07, 0.30, 0.45

    cur, events, seq = 0.0, [], []   # events: (זמן התחלה, אודיו)
    for i, q in enumerate(ad["questions"]):
        qa = voice.synthesize(q["q_say"], provider)
        aa = voice.synthesize(q["ans_say"][q["correct"]], provider)
        q_start = cur
        q_audio_at = q_start + PAD
        q_audio_end = q_audio_at + len(qa) / SR
        tick_at = q_audio_end + GAP
        tick_end = tick_at + TICK_LEN
        hl_at = tick_end + HL_AFTER
        ans_at = tick_end + ANS_AFTER
        q_end = ans_at + len(aa) / SR + TAIL
        events += [(q_audio_at, qa), (tick_at, tick[: int(TICK_LEN * SR)]), (ans_at, aa)]
        pn, ph = os.path.join(tmp, f"q{i}n.png"), os.path.join(tmp, f"q{i}h.png")
        render_frame(q, False).save(pn)
        render_frame(q, True).save(ph)
        seq += [(pn, hl_at - q_start), (ph, q_end - hl_at)]
        cur = q_end
    end_p = os.path.join(tmp, "end.png")
    render_frame(end=True).save(end_p)
    seq.append((end_p, len(end_snd) / SR))
    events.append((cur, end_snd))
    total = cur + len(end_snd) / SR

    mix = np.zeros(int(total * SR) + SR, dtype=np.float32)
    for t, a in events:
        i0 = int(t * SR)
        mix[i0:i0 + len(a)] += a[: len(mix) - i0]
    mix = np.clip(mix, -1, 1)[: int(total * SR)]
    wav = os.path.join(tmp, "mix.wav")
    voice.save_wav_f32(wav, mix)

    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p, d in seq:
            fh.write(f"file '{p.replace(chr(92), '/')}'\nduration {d:.4f}\n")
        fh.write(f"file '{seq[-1][0].replace(chr(92), '/')}'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-i", wav,
                    "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", out_path],
                   check=True)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default=os.path.join(HERE, "questions.txt"))
    ap.add_argument("--only", type=int, help="להפיק רק פרסומת מספר N")
    ap.add_argument("--preview", action="store_true", help="תמונות בדיקה בלבד, בלי וידאו")
    ap.add_argument("--out", default=os.path.join(HERE, "output"))
    ap.add_argument("--voice", default="none", choices=["none", "auto", "gemini", "edge", "mock"],
                    help="none = הסאונד המקורי הקבוע; auto/gemini/edge = הקראה אמיתית")
    ap.add_argument("--skip", type=int, default=0, help="לדלג על N פרסומות ראשונות (לבנק שאלות)")
    ap.add_argument("--count", type=int, default=0, help="להפיק רק N פרסומות (0 = כולן)")
    a = ap.parse_args()

    if not a.preview and not shutil.which("ffmpeg"):
        sys.exit("ffmpeg לא נמצא. התקן אותו (Windows:  winget install ffmpeg) ופתח את הטרמינל מחדש.")
    ads = parse(a.file)
    if not ads:
        sys.exit("לא נמצאו פרסומות בקובץ.")
    os.makedirs(a.out, exist_ok=True)
    made = 0
    for idx, ad in enumerate(ads, 1):
        if a.only and idx != a.only:
            continue
        if idx <= a.skip:
            continue
        if a.count and made >= a.count:
            break
        made += 1
        name = re.sub(r'[\\/:*?"<>|]', "", ad["name"] or "").strip()
        base = f"ad_{idx:02d}" + (f"_{name}" if name else "")
        if a.preview:
            for i, q in enumerate(ad["questions"]):
                render_frame(q, True).save(os.path.join(a.out, f"{base}_q{i + 1}.png"))
            render_frame(end=True).save(os.path.join(a.out, f"{base}_end.png"))
            print("תמונות בדיקה:", base)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                outp = os.path.join(a.out, base + ".mp4")
                if a.voice == "none":
                    build_video(ad, outp, tmp)
                    dur = TOTAL
                else:
                    dur = build_video_voice(ad, outp, tmp, a.voice)
            print(f"נוצר: {base}.mp4  ({dur:.1f} שניות)")


if __name__ == "__main__":
    main()
