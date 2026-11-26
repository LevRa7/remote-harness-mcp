"""Builds the Reddit / X preview assets from docs/screenshots.

    python docs/reddit/make_preview.py

Outputs (docs/reddit/), English and Russian:
    demo-preview.gif / demo-preview-ru.gif     1080x1080, 5 frames
    demo-preview.mp4 / demo-preview-ru.mp4     same cut, better text (Reddit/X prefer it)
    cover-1600x900.png / cover-ru-1600x900.png link preview

The screenshots in docs/screenshots are never modified: captions are drawn around them.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
OUT = ROOT / "docs" / "reddit"

W = H = 1080
BG = (11, 13, 16)
BORDER = (38, 44, 54)
TEXT = (238, 241, 245)
MUTED = (136, 146, 160)
DIM = (104, 113, 126)
ACCENT = (118, 219, 150)
STAR = (255, 199, 0)

F_BOLD = "C:/Windows/Fonts/arialbd.ttf"
F_REG = "C:/Windows/Fonts/arial.ttf"

EN = {
    "hook_eyebrow": "LAST THREE WEEKS IN GOOGLE LAND",
    "hook_lines": [("New models.", MUTED), ("New locks.", ACCENT), ("Old excuses.", TEXT)],
    "hook_sub": "So I gave the Gemini app hands.",
    "hook_note1": "Google: \u201cnot available in your location.\u201d",
    "hook_note2": "Me: the same machine, driven from a chat window.",
    "hook_footer": "MIT \u00b7 free tier \u00b7 no VPN \u00b7 no Antigravity",
    "frames": [
        ("\u201cGive me the specs\u201d \u2192 the laptop answers itself.",
         "system_info + system_vitals, executed on the node"),
        ("The phone reports its own battery, LTE and temperature.",
         "the same node, installed in Termux, no root"),
        ("\u201cScreenshot both monitors and tell me what is running.\u201d",
         "8086x2880 desktop, published and described"),
    ],
    "frame_footer": "commands ran on the machine - not in the cloud",
    "end_title": "Remote Harness MCP",
    "end_sub": "one link into Gemini \u2192 Connectors",
    "end_items": ["shell, git, builds", "files, grep, glob", "background jobs, cron",
                  "screenshots, vitals", "Android phone as a node"],
    "end_star": "star it if it saves you one SSH session",
    "end_meta": "MIT \u00b7 Windows, Linux, macOS, Android \u00b7 free Gemini tier",
    "cover_eyebrow": "REMOTE HARNESS MCP \u00b7 OPEN SOURCE \u00b7 MIT",
    "cover_lines": [("Your phone already", TEXT), ("has Gemini.", TEXT), ("Now it has your PC.", ACCENT)],
    "cover_sub": ["MCP server for the Gemini app and web: shell, files, jobs,",
                  "screenshots, an Android phone as a node. No Antigravity, no region check."],
    "cover_star": "free \u00b7 works on the free Gemini tier \u00b7 star if useful",
    "cover_caption": "preview shows three real tool calls: laptop specs, phone vitals, desktop screenshot",
}

RU = {
    "hook_eyebrow": "\u041f\u041e\u0421\u041b\u0415\u0414\u041d\u0418\u0415 \u0422\u0420\u0418 \u041d\u0415\u0414\u0415\u041b\u0418 \u0412 \u041c\u0418\u0420\u0415 GOOGLE",
    "hook_lines": [("\u041d\u043e\u0432\u044b\u0435 \u043c\u043e\u0434\u0435\u043b\u0438.", MUTED), ("\u041d\u043e\u0432\u044b\u0435 \u0437\u0430\u043c\u043a\u0438.", ACCENT), ("\u0421\u0442\u0430\u0440\u044b\u0435 \u043e\u0442\u043c\u0430\u0437\u043a\u0438.", TEXT)],
    "hook_sub": "\u0418 \u044f \u0432\u044b\u0434\u0430\u043b \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u044e Gemini \u0440\u0443\u043a\u0438.",
    "hook_note1": "Google: \u00ab\u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e \u0432 \u0432\u0430\u0448\u0435\u043c \u0440\u0435\u0433\u0438\u043e\u043d\u0435\u00bb.",
    "hook_note2": "\u042f: \u0442\u0430 \u0436\u0435 \u043c\u0430\u0448\u0438\u043d\u0430, \u043d\u043e \u0438\u0437 \u043e\u043a\u043d\u0430 \u0447\u0430\u0442\u0430.",
    "hook_footer": "MIT \u00b7 \u0431\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u044b\u0439 \u0442\u0430\u0440\u0438\u0444 \u00b7 \u0431\u0435\u0437 VPN \u00b7 \u0431\u0435\u0437 Antigravity",
    "frames": [
        ("\u00ab\u0414\u0430\u0439 \u0445\u0430\u0440\u0430\u043a\u0442\u0435\u0440\u0438\u0441\u0442\u0438\u043a\u0438\u00bb \u2192 \u043d\u043e\u0443\u0442\u0431\u0443\u043a \u043e\u0442\u0432\u0435\u0447\u0430\u0435\u0442 \u0441\u0430\u043c.",
         "system_info + system_vitals, \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435 \u043d\u0430 \u0443\u0437\u043b\u0435"),
        ("\u0422\u0435\u043b\u0435\u0444\u043e\u043d \u0441\u0430\u043c \u0440\u0430\u0441\u0441\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u043f\u0440\u043e \u0431\u0430\u0442\u0430\u0440\u0435\u044e, LTE \u0438 \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0443.",
         "\u0442\u043e\u0442 \u0436\u0435 \u0443\u0437\u0435\u043b, Termux, \u0431\u0435\u0437 root"),
        ("\u00ab\u0421\u0434\u0435\u043b\u0430\u0439 \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442 \u0434\u0432\u0443\u0445 \u043c\u043e\u043d\u0438\u0442\u043e\u0440\u043e\u0432 \u0438 \u043e\u043f\u0438\u0448\u0438, \u0447\u0442\u043e \u0437\u0430\u043f\u0443\u0449\u0435\u043d\u043e\u00bb.",
         "\u0434\u0435\u0441\u043a\u0442\u043e\u043f 8086x2880, \u043e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d \u0438 \u043e\u043f\u0438\u0441\u0430\u043d"),
    ],
    "frame_footer": "\u043a\u043e\u043c\u0430\u043d\u0434\u044b \u0432\u044b\u043f\u043e\u043b\u043d\u044f\u043b\u0438\u0441\u044c \u043d\u0430 \u043c\u0430\u0448\u0438\u043d\u0435, \u0430 \u043d\u0435 \u0432 \u043e\u0431\u043b\u0430\u043a\u0435",
    "end_title": "Remote Harness MCP",
    "end_sub": "\u043e\u0434\u043d\u0430 \u0441\u0441\u044b\u043b\u043a\u0430 \u0432 Gemini \u2192 \u041a\u043e\u043d\u043d\u0435\u043a\u0442\u043e\u0440\u044b",
    "end_items": ["shell, git, \u0441\u0431\u043e\u0440\u043a\u0438", "\u0444\u0430\u0439\u043b\u044b, grep, glob", "\u0444\u043e\u043d\u043e\u0432\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438, cron",
                  "\u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u044b, \u043c\u0435\u0442\u0440\u0438\u043a\u0438", "Android-\u0442\u0435\u043b\u0435\u0444\u043e\u043d \u043a\u0430\u043a \u0443\u0437\u0435\u043b"],
    "end_star": "\u043f\u043e\u0441\u0442\u0430\u0432\u044c \u0437\u0432\u0435\u0437\u0434\u0443, \u0435\u0441\u043b\u0438 \u0441\u044d\u043a\u043e\u043d\u043e\u043c\u0438\u0442 \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u043d\u0443 SSH-\u0441\u0435\u0441\u0441\u0438\u044e",
    "end_meta": "MIT \u00b7 Windows, Linux, macOS, Android \u00b7 \u0431\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u044b\u0439 \u0442\u0430\u0440\u0438\u0444",
    "cover_eyebrow": "REMOTE HARNESS MCP \u00b7 OPEN SOURCE \u00b7 MIT",
    "cover_lines": [("\u0423 \u0432\u0430\u0448\u0435\u0433\u043e \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430", TEXT), ("\u0443\u0436\u0435 \u0435\u0441\u0442\u044c Gemini.", TEXT), ("\u0422\u0435\u043f\u0435\u0440\u044c \u0438 \u0432\u0430\u0448 \u041f\u041a.", ACCENT)],
    "cover_sub": ["MCP-\u0441\u0435\u0440\u0432\u0435\u0440 \u0434\u043b\u044f Gemini app/web: shell, \u0444\u0430\u0439\u043b\u044b, \u0437\u0430\u0434\u0430\u0447\u0438,",
                  "\u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u044b, Android-\u0442\u0435\u043b\u0435\u0444\u043e\u043d \u043a\u0430\u043a \u0443\u0437\u0435\u043b. \u0411\u0435\u0437 Antigravity \u0438 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 \u0440\u0435\u0433\u0438\u043e\u043d\u0430."],
    "cover_star": "\u0431\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u043e \u00b7 \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442 \u043d\u0430 free-\u0442\u0430\u0440\u0438\u0444\u0435 \u00b7 \u0437\u0432\u0435\u0437\u0434\u0430, \u0435\u0441\u043b\u0438 \u043f\u0440\u0438\u0433\u043e\u0434\u0438\u0442\u0441\u044f",
    "cover_caption": "\u043d\u0430 \u043f\u0440\u0435\u0432\u044c\u044e \u0442\u0440\u0438 \u0440\u0435\u0430\u043b\u044c\u043d\u044b\u0445 \u0432\u044b\u0437\u043e\u0432\u0430: \u0445\u0430\u0440\u0430\u043a\u0442\u0435\u0440\u0438\u0441\u0442\u0438\u043a\u0438 \u043d\u043e\u0443\u0442\u0431\u0443\u043a\u0430, \u043c\u0435\u0442\u0440\u0438\u043a\u0438 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430, \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442 \u0434\u0435\u0441\u043a\u0442\u043e\u043f\u0430",
}


def font(path, size):
    return ImageFont.truetype(path, size)


def wrap(draw, text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=f) <= maxw:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def spaced(draw, xy, text, f, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f) + spacing


def star(draw, cx, cy, r, fill):
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.42
        pts.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
    draw.polygon(pts, fill=fill)


def base_frame(step, total=5):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    spaced(d, (72, 44), "REMOTE HARNESS MCP", font(F_REG, 21), MUTED, 3)
    r = 5
    x0 = W - 72 - (total * (2 * r + 14) - 14)
    for i in range(total):
        c = ACCENT if i == step else (52, 59, 70)
        d.ellipse([x0 + i * (2 * r + 14), 51, x0 + i * (2 * r + 14) + 2 * r, 51 + 2 * r], fill=c)
    d.line([(72, 88), (W - 72, 88)], fill=(28, 33, 40), width=1)
    return im, d


def caption(d, text, sub):
    for size, lh in ((40, 48), (36, 44), (32, 40)):
        f = font(F_BOLD, size)
        lines = wrap(d, text, f, W - 144)
        if len(lines) <= 2:
            break
    y = 118 + (48 - lh) // 2
    for ln in lines:
        d.text((72, y), ln, font=f, fill=TEXT)
        y += lh
    fs = font(F_REG, 25)
    for ln in wrap(d, sub, fs, W - 144):
        d.text((72, y + 6), ln, font=fs, fill=MUTED)
        y += 32


def place_shot(im, path, box=(72, 262, W - 72, 962)):
    shot = Image.open(path).convert("RGB")
    bw, bh = box[2] - box[0], box[3] - box[1]
    k = min(bw / shot.width, bh / shot.height)
    size = (max(1, int(shot.width * k)), max(1, int(shot.height * k)))
    shot = shot.resize(size, Image.LANCZOS)
    x = box[0] + (bw - size[0]) // 2
    y = box[1] + (bh - size[1]) // 2
    im.paste(shot, (x, y))
    d = ImageDraw.Draw(im)
    d.rectangle([x - 1, y - 1, x + size[0], y + size[1]], outline=BORDER, width=1)


def footer(d, text):
    f = font(F_REG, 22)
    d.text(((W - d.textlength(text, font=f)) / 2, 1002), text, font=f, fill=DIM)


def hook(T):
    im, d = base_frame(0)
    spaced(d, (72, 300), T["hook_eyebrow"], font(F_REG, 24), MUTED, 2)
    y = 360
    for txt, col in T["hook_lines"]:
        d.text((72, y), txt, font=font(F_BOLD, 96), fill=col)
        y += 112
    d.text((72, 726), T["hook_sub"], font=font(F_BOLD, 46), fill=TEXT)
    d.text((72, 800), T["hook_note1"], font=font(F_REG, 28), fill=MUTED)
    d.text((72, 840), T["hook_note2"], font=font(F_REG, 28), fill=MUTED)
    footer(d, T["hook_footer"])
    return im


def shot_frame(step, path, cap, sub, T):
    im, d = base_frame(step)
    caption(d, cap, sub)
    place_shot(im, path)
    footer(d, T["frame_footer"])
    return im


def endcard(T):
    im, d = base_frame(4)
    d.text((72, 300), T["end_title"], font=font(F_BOLD, 76), fill=TEXT)
    sub_f = font(F_REG, 34)
    d.text((72, 396), T["end_sub"], font=sub_f, fill=MUTED)
    y = 480
    for it in T["end_items"]:
        d.ellipse([72, y + 12, 84, y + 24], fill=ACCENT)
        d.text((100, y), it, font=font(F_REG, 34), fill=TEXT)
        y += 54
    d.text((72, 786), "github.com/LevRa7/remote-harness-mcp", font=font(F_BOLD, 40), fill=ACCENT)
    star(d, 84, 884, 20, STAR)
    d.text((118, 862), T["end_star"], font=font(F_REG, 30), fill=TEXT)
    d.text((72, 928), T["end_meta"], font=font(F_REG, 26), fill=MUTED)
    return im


def cover(T):
    cw, ch = 1600, 900
    im = Image.new("RGB", (cw, ch), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 6, ch], fill=ACCENT)
    spaced(d, (80, 64), T["cover_eyebrow"], font(F_REG, 24), MUTED, 3)
    y = 168
    for i, (ln, col) in enumerate(T["cover_lines"]):
        d.text((80, y), ln, font=font(F_BOLD, 62 if i < 2 else 66), fill=col)
        y += 84
    y = 470
    for ln in T["cover_sub"]:
        d.text((80, y), ln, font=font(F_REG, 30), fill=MUTED)
        y += 44
    d.text((80, 590), "github.com/LevRa7/remote-harness-mcp", font=font(F_BOLD, 40), fill=ACCENT)
    star(d, 96, 720, 20, STAR)
    d.text((132, 696), T["cover_star"], font=font(F_REG, 30), fill=TEXT)
    d.text((80, 806), T["cover_caption"], font=font(F_REG, 24), fill=DIM)
    return im


def build(lang, T, suffix, cover_suffix):
    frames_dir = OUT / "frames" / lang
    frames_dir.mkdir(parents=True, exist_ok=True)
    shots = ["01-hardware.png", "02-phone.png", "03-screen-capture.png"]
    frames = [(hook(T), 2.2, "00-hook")]
    for i, (shot, (cap, sub)) in enumerate(zip(shots, T["frames"]), start=1):
        frames.append((shot_frame(i, SHOTS / shot, cap, sub, T), 2.8, shot.replace(".png", "")))
    frames.append((endcard(T), 3.4, "04-endcard"))

    paths = []
    for im, _dur, name in frames:
        p = frames_dir / (name + ".png")
        im.save(p)
        paths.append(p)

    gif = [f[0].quantize(colors=160, method=Image.MEDIANCUT, dither=Image.NONE) for f in frames]
    gif[0].save(OUT / ("demo-preview" + suffix + ".gif"), save_all=True, append_images=gif[1:],
                duration=[int(f[1] * 1000) for f in frames], loop=0, optimize=True, disposal=2)
    cover(T).save(OUT / ("cover" + cover_suffix + "-1600x900.png"))

    total = sum(f[1] for f in frames)
    lst = OUT / ("frames-" + lang + ".txt")
    with lst.open("w", encoding="utf-8") as fh:
        for p, (_, secs, _n) in zip(paths, frames):
            fh.write("file \u0027" + p.as_posix() + "\u0027\n")
            fh.write("duration " + format(secs, ".2f") + "\n")
        fh.write("file \u0027" + paths[-1].as_posix() + "\u0027\n")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-t", format(total, ".2f"), "-vf", "fps=30,format=yuv420p", "-c:v", "libx264",
           "-preset", "slow", "-crf", "20", "-movflags", "+faststart",
           str(OUT / ("demo-preview" + suffix + ".mp4"))]
    subprocess.run(cmd, check=True)
    lst.unlink(missing_ok=True)
    for name in ("demo-preview" + suffix + ".gif", "demo-preview" + suffix + ".mp4",
                 "cover" + cover_suffix + "-1600x900.png"):
        print(format((OUT / name).stat().st_size, ">8"), name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    build("en", EN, "", "")
    build("ru", RU, "-ru", "-ru")
    return 0


if __name__ == "__main__":
    sys.exit(main())
