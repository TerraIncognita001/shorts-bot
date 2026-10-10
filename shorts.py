#!/usr/bin/env python3
"""Terra Incognita Shorts bot, v3: the "CASE FILE" format.

Every video is ONE question (the hook, spoken and shown in the first second) that the video answers at the end:
the progress bar carries a lock that opens exactly when the answer is revealed.  Frames are composed in Python
(PIL + numpy) and piped to ffmpeg; the voice is edge-tts, the script is Gemini, the facts are the Minecraft Wiki,
the background is your own gameplay (clips/), the music is yours (music/).  Everything is free.
"""
import asyncio, base64, bisect, html, json, math, os, pathlib, random, re, subprocess, sys, time, wave
import requests


def _need(mod, pip_name):
    try:
        return __import__(mod)
    except ImportError:
        for extra in ([], ["--break-system-packages"]):
            if subprocess.run([sys.executable, "-m", "pip", "install", "-q", pip_name, *extra]).returncode == 0:
                break
        return __import__(mod)


np = _need("numpy", "numpy")
_need("PIL", "Pillow")
from PIL import Image, ImageDraw, ImageFilter, ImageFont

env = os.getenv
W = pathlib.Path(env("WORK_DIR", "work"))
W.mkdir(parents=True, exist_ok=True)
USED = pathlib.Path(env("STATE_FILE", "used.json"))

# ---- Settings (override in the workflow env) -----------------------------------------------------------------------
VOICE = env("VOICE", "en-US-GuyNeural")
RATE = int(env("RATE", "10"))                             # base speech speed, % faster than normal
MUSIC_VOL = float(env("MUSIC_VOL", "0.10"))
SFX = env("SFX", "1") == "1"
SFX_VOL = float(env("SFX_VOL", "1.0"))
LANGS = [l.strip() for l in env("LANGS", "es,pt,de,fr,it,pl,uk,tr").split(",") if l.strip()]
LEARN = env("LEARN", "1") == "1"                          # pick question types by real view counts
IMAGES = env("IMAGES", "1") == "1"                        # 0 = no wiki pictures
COOLDOWN = int(env("COOLDOWN", "45"))                     # a famous subject can come back after this many videos
TAG_PER_RUN = int(env("TAG_PER_RUN", "12"))               # gameplay clips described by Gemini per run
DRY_RUN = env("DRY_RUN", "0") == "1"                      # make the video, skip YouTube and the state file
FPS = 30
UA = {"User-Agent": "TerraIncognitaShortsBot/3.0 (https://terraincognita001.github.io/terra-incognita/)"}
MCWIKI = "https://minecraft.wiki/api.php"
DISCLAIMER = "NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT."
MUSIC_DIR = pathlib.Path("music")
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
SKIP_IMG = re.compile(r"sprite|invicon|icon|logo|grid|slot|button|achievement|advancement|recipe|crafting|"
                      r"atlas|gui|hud|pattern|banner|edition|screenshot of|\.gif$|\.svg$", re.I)


# ---- State: used.json = {"videos": [...], "clips": {...}}; the old plain list is migrated ---------------------------
def load_state():
    try:
        raw = json.loads(USED.read_text(encoding="utf-8")) if USED.exists() else {}
    except ValueError:
        raw = {}
    if isinstance(raw, list):
        raw = {"videos": raw}
    vids = [e if isinstance(e, dict) else {"key": str(e)} for e in (raw.get("videos") if isinstance(raw.get("videos"), list) else [])]
    clips = raw.get("clips") if isinstance(raw.get("clips"), dict) else {}
    return {"v": 3, "videos": vids, "clips": clips}


STATE = load_state()
used = STATE["videos"]


def save_state():
    d = pathlib.Path("clips")
    STATE["clips"] = {k: v for k, v in STATE["clips"].items() if (d / k).exists()}   # forget deleted / fallback clips

    def block(items):
        return "[\n" + ",\n".join(" " + json.dumps(i, ensure_ascii=False) for i in items) + "\n]"
    clips = "{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}"
                               for k, v in sorted(STATE["clips"].items())) + "\n}"
    USED.write_text('{\n"v": 3,\n"videos": ' + block(used) + ',\n"clips": ' + clips + "\n}\n", encoding="utf-8")


def titles_of(e):
    return e.get("arts") or str(e.get("key", "")).split(" vs ")


AVOID = set()                                              # subjects whose script failed during this run


def recent_titles(n):
    return {t for e in used[-n:] for t in titles_of(e)} | AVOID


# ---- ffmpeg helpers ------------------------------------------------------------------------------------------------
def ffmpeg(*args, **kw):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True, **kw)


def probe(path, entries="format=duration", stream="v:0"):
    try:
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", entries,
                               "-of", "default=nw=1:nk=1", str(path)],
                              capture_output=True, text=True, check=True).stdout.split()
    except Exception:
        return []


def probe_duration(path):
    out = probe(path)
    if not out:
        out = probe(path, "format=duration", "a:0")
    try:
        return float(out[0])
    except (IndexError, ValueError):
        return 0.0


def probe_size(path):
    out = probe(path, "stream=width,height")
    try:
        return int(out[0]), int(out[1])
    except (IndexError, ValueError):
        return 0, 0


# ---- Fonts: Anton (captions) + Press Start 2P (labels), both OFL, downloaded once -----------------------------------
FONT_URLS = {
    "anton": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "pixel": "https://raw.githubusercontent.com/google/fonts/main/ofl/pressstart2p/PressStart2P-Regular.ttf"}
FONT_PATHS = {}
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def ensure_fonts():
    own = pathlib.Path("font.ttf")                         # optional: your own caption font
    for kind, url in FONT_URLS.items():
        p = own if (kind == "anton" and own.exists()) else W / "fonts" / url.rsplit("/", 1)[1]
        if not p.exists() and env("FONT_DOWNLOAD", "1") == "1":
            try:
                p.parent.mkdir(exist_ok=True)
                r = requests.get(url, headers=UA, timeout=60)
                r.raise_for_status()
                p.write_bytes(r.content)
            except Exception as e:
                print("font download failed:", kind, type(e).__name__)
        ok = p.exists() and p.stat().st_size > 10000
        FONT_PATHS[kind] = str(p) if ok else DEJAVU
    return FONT_PATHS


# ---- Drawing toolkit: text tiles, pixel panels, stickers, widgets, easing --------------------------------------
CW, CH = 1080, 1920                                         # canvas
GREEN, GOLD, RED = (88, 209, 59), (255, 205, 30), (255, 69, 58)
CYAN, PURPLE, ORANGE = (51, 224, 224), (181, 101, 255), (255, 138, 31)
WHITE, INK = (255, 255, 255), (14, 16, 20)


def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def ease_out_back(t, s=1.9):
    t = clamp(t) - 1
    return t * t * ((s + 1) * t + s) + 1


def ease_out_cubic(t):
    return 1 - (1 - clamp(t)) ** 3


def ease_in_out(t):
    t = clamp(t)
    return 3 * t * t - 2 * t * t * t


_FONTS = {}


def font(kind, size):
    """kind: 'anton' (captions) or 'pixel' (labels)."""
    key = (kind, int(size))
    if key not in _FONTS:
        path = FONT_PATHS.get(kind) or FONT_PATHS["anton"]
        try:
            _FONTS[key] = ImageFont.truetype(str(path), int(size))
        except Exception:
            _FONTS[key] = ImageFont.load_default()
    return _FONTS[key]


_TILES = {}


def text_tile(txt, size, fill=WHITE, kind="anton", stroke=None, shadow=True, pad=None):
    """RGBA tile with the text, a thick black outline and a soft drop shadow (cached). All tiles of one font size
    share the same top/bottom, so words of one line sit on the same baseline."""
    key = (txt, int(size), fill, kind, stroke, shadow)
    if key in _TILES:
        return _TILES[key]
    f = font(kind, size)
    sw = (round(size * 0.085) if kind == "anton" else max(2, round(size * 0.12))) if stroke is None else stroke
    pad = (sw + round(size * 0.07) + 4) if pad is None else pad
    l, t, r, b = f.getbbox(txt, stroke_width=sw)
    _, rt, _, rb = f.getbbox("Hg", stroke_width=sw)
    t, b = min(t, rt), max(b, rb)
    im = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shadow:
        off = max(2, round(size * 0.045))
        d.text((pad - l + off, pad - t + off), txt, font=f, fill=(0, 0, 0, 150), stroke_width=sw, stroke_fill=(0, 0, 0, 150))
    d.text((pad - l, pad - t), txt, font=f, fill=fill + (255,), stroke_width=sw, stroke_fill=(10, 10, 12, 255))
    if len(_TILES) > 400:
        _TILES.clear()
    _TILES[key] = im
    return im


def text_w(txt, size, kind="anton"):
    return font(kind, size).getlength(txt)


def multi_tile(words, size, colors, kind="anton", gap=0.26):
    """One tile with several words, each in its own colour (cached through text_tile)."""
    tiles = [text_tile(w, size, c, kind) for w, c in zip(words, colors)]
    sp = int(size * gap)
    w = sum(t.width for t in tiles) + sp * (len(tiles) - 1) - 2 * 0
    h = max(t.height for t in tiles)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = 0
    for t in tiles:
        im.alpha_composite(t, (x, (h - t.height) // 2))
        x += t.width + sp
    return im


def _cuts(n, k):
    """All ways to cut n words into k consecutive non-empty groups, as [(start, end), ...]."""
    def rec(start, left):
        if left == 1:
            yield [(start, n)]
            return
        for cut in range(start + 1, n - left + 2):
            for rest in rec(cut, left - 1):
                yield [(start, cut)] + rest
    return rec(0, k)


def split_lines(words, max_w, sizes, kind="anton", max_lines=3, max_h=None, lh=1.0):
    """(size, [[word indexes per line]]): the biggest font where the words fit into <= max_lines balanced lines
    and the block is at most max_h tall; if nothing fits, the smallest font shrunk until the widest line fits."""
    n = len(words)
    for size in sizes:
        sp = size * 0.26
        ws = [text_w(w, size, kind) for w in words]
        for k in range(1, min(max_lines, n) + 1):
            if max_h is not None and k * size * lh > max_h:
                break
            best = None
            for parts in _cuts(n, k):
                m = max(sum(ws[a:b]) + sp * (b - a - 1) for a, b in parts)
                if m <= max_w and (best is None or m < best[0]):
                    best = (m, parts)
            if best:
                return size, [list(range(a, b)) for a, b in best[1]]
    size = sizes[-1]
    sp = size * 0.26
    ws = [text_w(w, size, kind) for w in words]
    k = max(1, min(max_lines, n))
    m, parts = min(((max(sum(ws[a:b]) + sp * (b - a - 1) for a, b in parts), parts) for parts in _cuts(n, k)),
                   key=lambda x: x[0])
    if m > max_w:
        size = max(8, int(size * max_w / m))
    return size, [list(range(a, b)) for a, b in parts]


def panel(w, h, fill=(14, 18, 24, 232), border=GREEN, bw=6, notch=None):
    """Pixel-style panel: chunky border, notched corners, a lighter top edge."""
    nt = bw if notch is None else notch
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def body(x0, y0, x1, y1, col):
        d.rectangle([x0 + nt, y0, x1 - nt, y1], fill=col)
        d.rectangle([x0, y0 + nt, x1, y1 - nt], fill=col)

    body(0, 0, w - 1, h - 1, border + (255,))
    body(bw, bw, w - 1 - bw, h - 1 - bw, fill)
    hi = tuple(min(255, c + 70) for c in border) + (255,)
    d.rectangle([nt, 0, w - 1 - nt, max(1, bw // 3)], fill=hi)
    return im


def gradient(w, h, top_a, bottom_a, color=(0, 0, 0)):
    a = np.linspace(top_a, bottom_a, h, dtype=np.float32)[:, None].repeat(w, 1)
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[..., :3] = color
    arr[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def blit(base, tile, cx, cy, scale=1.0, angle=0.0, alpha=1.0):
    """Paste an RGBA tile centred at (cx, cy) with optional scale / rotation / opacity."""
    if tile is None or alpha <= 0.01 or scale <= 0.01:
        return
    t = tile
    if abs(scale - 1) > 0.004:
        t = t.resize((max(1, int(t.width * scale)), max(1, int(t.height * scale))),
                     Image.BILINEAR if scale < 1 else Image.BICUBIC)
    if angle:
        t = t.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 0.99:
        t = t.copy()
        t.putalpha(t.getchannel("A").point(lambda v: int(v * alpha)))
    base.paste(t, (int(cx - t.width / 2), int(cy - t.height / 2)), t)


def make_sticker(img, height, outline=16, color=WHITE):
    """Cut-out sticker: transparent pictures keep their shape (white outline + shadow); opaque pictures become a
    framed photo card with rounded corners."""
    im = img.convert("RGBA")
    a = np.asarray(im.getchannel("A"))
    cut = (a < 245).mean() > 0.04                          # has real transparency -> cut-out
    if cut:
        bb = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        if bb:
            im = im.crop(bb)
    pixel = min(im.size) <= 64
    sc = height / im.height
    w = max(1, int(im.width * sc))
    if not cut:
        w = min(w, 860)
        sc = w / im.width
        height = int(im.height * sc)
    im = im.resize((w, int(height)), Image.NEAREST if pixel else Image.LANCZOS)
    pad = outline + 26
    canvas = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    if cut:
        canvas.alpha_composite(im, (pad, pad))
        alpha = canvas.getchannel("A")
        grown = alpha.filter(ImageFilter.GaussianBlur(outline * 0.55)).point(lambda v: 255 if v > 24 else 0)
        edge = Image.new("RGBA", canvas.size, color + (255,))
        edge.putalpha(grown)
        body = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        body.alpha_composite(edge)
        body.alpha_composite(canvas)
        shape = grown
    else:
        card = Image.new("RGBA", (im.width + 2 * outline, im.height + 2 * outline), color + (255,))
        card.alpha_composite(im, (outline, outline))
        m = Image.new("L", card.size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, card.width - 1, card.height - 1], radius=26, fill=255)
        card.putalpha(m)
        body = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        body.alpha_composite(card, ((canvas.width - card.width) // 2, (canvas.height - card.height) // 2))
        shape = body.getchannel("A")
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.putalpha(shape.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 14))
    out.alpha_composite(body)
    return out


# ---- pixel icons ------------------------------------------------------------------------------------------------
HEART = ["..XX...XX..", ".XXXX.XXXX.", "XXXXXXXXXXX", "XXXXXXXXXXX", ".XXXXXXXXX.", "..XXXXXXX..", "...XXXXX...", "....XXX....", ".....X....."]
LOCK = ["...XXXXX...", "..XX...XX..", "..XX...XX..", "..XX...XX..", ".XXXXXXXXX.", ".XXXXXXXXX.", ".XXXXkXXXX.", ".XXXXkXXXX.", ".XXXXXXXXX.", ".XXXXXXXXX."]
LOCK_OPEN = ["........XXX.", ".......XX.XX", ".......XX.XX", ".......XX.XX", ".XXXXXXXXX..", ".XXXXXXXXX..", ".XXXXkXXXX..", ".XXXXkXXXX..", ".XXXXXXXXX..", ".XXXXXXXXX.."]
_ICONS = {}


def icon(rows, px, fill, outline=(20, 8, 8), hi=None, key=None):
    key = key or (tuple(rows), px, fill, outline, hi)
    if key in _ICONS:
        return _ICONS[key]
    h, w = len(rows), len(rows[0])
    im = Image.new("RGBA", ((w + 2) * px, (h + 2) * px), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cells = {(x, y) for y, r in enumerate(rows) for x, c in enumerate(r) if c != "."}
    for (x, y) in cells:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (x + dx, y + dy) not in cells:
                    d.rectangle([(x + 1 + dx) * px, (y + 1 + dy) * px, (x + 2 + dx) * px - 1, (y + 2 + dy) * px - 1], fill=outline + (255,))
    for y, r in enumerate(rows):
        for x, c in enumerate(r):
            if c == "X":
                col = fill
                if hi and (x, y) in hi:
                    col = (255, 190, 190)
                d.rectangle([(x + 1) * px, (y + 1) * px, (x + 2) * px - 1, (y + 2) * px - 1], fill=col + (255,))
            elif c == "k":
                d.rectangle([(x + 1) * px, (y + 1) * px, (x + 2) * px - 1, (y + 2) * px - 1], fill=outline + (255,))
    _ICONS[key] = im
    return im


def heart(px):
    return icon(HEART, px, (232, 32, 44), hi={(2, 1), (3, 1), (1, 2), (2, 2), (1, 3)}, key=("heart", px))


def lock_icon(px, opened=False):
    return icon(LOCK_OPEN if opened else LOCK, px, (255, 205, 30) if opened else (200, 200, 205), (30, 24, 8), key=("lock", px, opened))


# ---- widgets: explain the number instead of only saying it -------------------------------------------------------
def fnum(v):
    v = float(v)
    return str(int(v)) if v == int(v) else f"{v:g}"


def widget_card(w, h, title, accent):
    im = panel(w, h, border=accent)
    tt = text_tile(title, 26, accent, "pixel", stroke=3, shadow=False)
    im.alpha_composite(tt, (28, 18))
    return im


def viz_number(spec, t, accent):
    """Giant number / short phrase that slams in."""
    txt = str(spec.get("text", "")).upper()[:14]
    size, lines = split_lines(txt.split() or [txt], 880, [330, 290, 250, 210, 180, 150, 120], max_lines=2)
    words = txt.split()
    rows = [" ".join(words[i] for i in ln) for ln in lines]
    tiles = [text_tile(r, size, GOLD if k == 0 else WHITE) for k, r in enumerate(rows)]
    h = sum(x.height for x in tiles) - int(size * 0.18) * (len(tiles) - 1)
    w = max(x.width for x in tiles)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = 0
    for x in tiles:
        im.alpha_composite(x, ((w - x.width) // 2, y))
        y += x.height - int(size * 0.18)
    s = 0.35 + 0.65 * ease_out_back(t / 0.28)
    return im, s, 0.0


def viz_hearts(spec, t, accent):
    hp = max(1.0, float(spec.get("value", 20)))
    n = hp / 2.0
    shown = int(min(20, math.ceil(n)))
    per_row, px = (shown, 13) if shown <= 5 else (5, 11) if shown <= 10 else (10, 6)
    hw = (len(HEART[0]) + 2) * px
    step = hw + (10 if px > 6 else 6)
    rows = (shown + per_row - 1) // per_row
    w, h = 960, 150 + rows * (hw + 8) + (50 if n > 20 else 14)
    im = widget_card(w, h, str(spec.get("label", "HEALTH")).upper()[:18], RED)
    d = ImageDraw.Draw(im)
    big = text_tile(f"{fnum(hp)} HP", 104, WHITE)
    im.alpha_composite(big, (w - big.width - 24, 4))
    k = int(shown * ease_out_cubic(t / 0.9))
    ht = heart(px)
    x0 = (w - (per_row * step - (step - hw))) // 2
    for i in range(shown):
        x = x0 + (i % per_row) * step
        y = 126 + (i // per_row) * (hw + 8)
        if i < k:
            im.alpha_composite(ht, (x, y))
        else:
            d.rectangle([x + px * 2, y + px * 2, x + hw - px * 2, y + hw - px * 2], outline=(70, 70, 78), width=3)
    if n > 20:
        mt = text_tile(f"+{fnum(math.ceil(n - 20))} MORE HEARTS", 44, GOLD)
        im.alpha_composite(mt, (40, h - mt.height - 6))
    return im, 1.0, 0.0


def viz_grid(spec, t, accent):
    n = int(clamp(float(spec.get("n", 10)), 2, 100))
    hit = int(clamp(float(spec.get("hit", 1)), 1, max(1, n // 2)))
    cols = 10 if n > 20 else 5 if n > 5 else n
    rows = (n + cols - 1) // cols
    cell = int(max(22, min(120, (800 // cols) - 8, 330 // rows - 8)))
    lab = text_tile(f"{hit} IN {n}", 84, GOLD)
    w, h = max(cols * (cell + 8) + 80, 640), 84 + rows * (cell + 8) + lab.height - 2
    im = widget_card(w, h, str(spec.get("label", "THE ODDS")).upper()[:18], accent)
    d = ImageDraw.Draw(im)
    lit = int(n * ease_out_cubic(t / 0.7))
    show_hit = t > 0.8
    x0 = (w - (cols * (cell + 8) - 8)) // 2
    for i in range(n):
        x, y = x0 + (i % cols) * (cell + 8), 84 + (i // cols) * (cell + 8)
        isHit = i < hit and show_hit
        col = (255, 205, 30) if isHit else (60, 66, 76) if i >= lit else (110, 120, 134)
        d.rectangle([x, y, x + cell, y + cell], fill=col, outline=(10, 12, 14), width=3)
        if isHit:
            d.rectangle([x + 6, y + 6, x + cell - 6, y + 6 + max(6, cell // 8)], fill=(255, 245, 170))
    im.alpha_composite(lab, ((w - lab.width) // 2, h - lab.height - 2))
    return im, 1.0, 0.0


def viz_bars(spec, t, accent):
    items = [(str(a)[:16].upper(), float(b)) for a, b in spec.get("items", [])][:4]
    top = max([v for _, v in items] + [1e-9])
    row = 132
    w, h = 960, 96 + row * len(items)
    im = widget_card(w, h, str(spec.get("label", "COMPARE")).upper()[:18], accent)
    d = ImageDraw.Draw(im)
    for i, (name, v) in enumerate(items):
        y = 84 + i * row
        nt = text_tile(name, 58, WHITE)
        im.alpha_composite(nt, (24, y - 8))
        bx, bw_max = 24, w - 48
        frac = v / top * ease_out_cubic((t - 0.08 * i) / 0.7)
        d.rectangle([bx, y + 62, bx + bw_max, y + 108], fill=(40, 44, 52), outline=(10, 12, 14), width=3)
        if frac > 0:
            col = accent if v == top else (130, 140, 154)
            d.rectangle([bx + 3, y + 65, bx + 3 + int((bw_max - 6) * frac), y + 105], fill=col)
        vt = text_tile(fnum(v), 58, GOLD if v == top else WHITE)
        im.alpha_composite(vt, (w - vt.width - 24, y - 8))
    return im, 1.0, 0.0


VIZ = {"number": viz_number, "hearts": viz_hearts, "grid": viz_grid, "bars": viz_bars}


def valid_viz(spec):
    """Only specs the widgets can really draw."""
    if not isinstance(spec, dict):
        return None
    k = str(spec.get("type", "")).lower()
    try:
        if k == "number" and str(spec.get("text", "")).strip():
            return {"type": k, "text": str(spec["text"]).strip()[:14]}
        if k == "hearts" and 1 <= float(spec.get("value", 0)) <= 5000:
            return {"type": k, "value": float(spec["value"]), "label": str(spec.get("label", "HEALTH"))[:18]}
        if k == "grid" and 2 <= float(spec.get("n", 0)) <= 100 and 1 <= float(spec.get("hit", 1)) <= float(spec["n"]) / 2:
            return {"type": k, "n": int(float(spec["n"])), "hit": int(float(spec.get("hit", 1))),
                    "label": str(spec.get("label", "THE ODDS"))[:18]}
        if k == "bars":
            items = [(str(a), float(b)) for a, b in spec.get("items", []) if float(b) > 0][:4]
            if len(items) >= 2:
                return {"type": k, "items": items, "label": str(spec.get("label", "COMPARE"))[:18]}
    except (TypeError, ValueError):
        return None
    return None


def mystery_block(size=470):
    """Generic pixel '?' block: the picture when a subject has no usable image."""
    n, px = 10, size // 10
    im = Image.new("RGBA", (n * px + 40, n * px + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ox = oy = 20
    d.rectangle([ox, oy, ox + n * px - 1, oy + n * px - 1], fill=(20, 14, 6, 255))
    d.rectangle([ox + px // 2, oy + px // 2, ox + n * px - px // 2 - 1, oy + n * px - px // 2 - 1], fill=(222, 156, 26, 255))
    d.rectangle([ox + px // 2, oy + px // 2, ox + n * px - px // 2 - 1, oy + px // 2 + px // 3], fill=(255, 214, 90, 255))
    d.rectangle([ox + px // 2, oy + px // 2, ox + px // 2 + px // 3, oy + n * px - px // 2 - 1], fill=(255, 214, 90, 255))
    d.rectangle([ox + n * px - px // 2 - px // 3, oy + px // 2, ox + n * px - px // 2 - 1, oy + n * px - px // 2 - 1], fill=(160, 100, 12, 255))
    d.rectangle([ox + px // 2, oy + n * px - px // 2 - px // 3, ox + n * px - px // 2 - 1, oy + n * px - px // 2 - 1], fill=(160, 100, 12, 255))
    q = text_tile("?", int(size * 0.62), WHITE, "pixel", stroke=int(size * 0.035), shadow=True)
    im.alpha_composite(q, ((im.width - q.width) // 2 + 6, (im.height - q.height) // 2 + 2))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(im.getchannel("A").filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 14))
    out.alpha_composite(im)
    return out


def fit_tile(tile, max_w, max_h):
    s = min(1.0, max_w / tile.width, max_h / tile.height)
    return tile if s >= 0.999 else tile.resize((max(1, int(tile.width * s)), max(1, int(tile.height * s))), Image.LANCZOS)


# ---- Scene: the per-frame compositor ---------------------------------------------------------------------------
# Layout (1080x1920; Shorts UI covers the top ~170 px, the right column x > 918 below y ~ 1000, the bottom ~ 420 px):
#   tab chip y~196 | question panel y 240-400 | visual zone y 440-1060 | captions y~1245 | bar y 1452
ASP = CW / CH
HOOK_CX, HOOK_CY, HERO_CY = 540, 1140, 555
VIS_CY, CARD_CY = 775, 790
CAP_CX, CAP_CY = 500, 1245
BAR_X0, BAR_X1, BAR_Y, BAR_H = 60, 796, 1452, 30
LOCK_CX = 876
GHOST = (206, 210, 220)
HOOK_SIZES = [230, 210, 190, 172, 156, 140, 124, 108]
HEAD_SIZES = [76, 70, 64, 58, 52, 46]
CAP_SIZES = [124, 112, 100, 90, 80, 70]
GRADE = "eq=contrast=1.10:saturation=1.28:gamma=0.97,unsharp=5:5:0.9:5:5:0.0"


def lerp(a, b, e):
    return a + (b - a) * e


def shade_layer():
    """One RGBA layer: dark top and bottom gradients + vignette, so text always reads on any gameplay."""
    y = np.arange(CH, dtype=np.float32)[:, None]
    x = np.arange(CW, dtype=np.float32)[None, :]
    top = np.clip(1 - y / 600, 0, 1) ** 1.5 * 0.62
    bot = np.clip((y - 980) / 540, 0, 1) ** 1.25 * 0.70
    vx, vy = (x - CW / 2) / (CW / 2), (y - CH / 2) / (CH / 2)
    vig = np.clip(vx * vx * 0.55 + vy * vy * 0.45 - 0.30, 0, 1) * 0.60
    a = 1 - (1 - 0.14) * (1 - top) * (1 - bot) * (1 - vig)
    arr = np.zeros((CH, CW, 4), np.uint8)
    arr[..., 3] = np.clip(a * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def layout_words(words, colors, sizes, max_w, cx, cy, max_lines, kind="anton", lh_mul=1.02, max_h=None):
    """Balanced lines of words with the biggest font that fits: (size, [item dicts with centre x/y], block height)."""
    size, lines = split_lines(words, max_w, sizes, kind, max_lines, max_h, lh_mul)
    sp, lh = size * 0.26, int(size * lh_mul)
    block = lh * len(lines)
    y0 = cy - block / 2 + lh / 2
    items = []
    for li, idxs in enumerate(lines):
        ws = [text_w(words[i], size, kind) for i in idxs]
        x = cx - (sum(ws) + sp * (len(idxs) - 1)) / 2
        for i, wd in zip(idxs, ws):
            items.append({"i": i, "x": x + wd / 2, "y": y0 + li * lh, "color": colors[i], "w": wd})
            x += wd + sp
    return size, items, block


def chip(txt, fg, bg, size=22, pad=14):
    """Small pixel label: dark text on a coloured plate."""
    tt = text_tile(txt, size, fg, "pixel", stroke=0, shadow=False, pad=0)
    w, h = tt.width + 2 * pad, tt.height + 2 * pad - 4
    im = panel(w, h, fill=bg + (255,), border=tuple(max(0, c - 70) for c in bg), bw=4)
    im.alpha_composite(tt, (pad, (h - tt.height) // 2))
    return im


def shot_region(sw, sh, margin=1.3):
    """(x0, y0, w, h) of the part of a source frame that is decoded: a 9:16 window plus room to pan and zoom."""
    if sw <= 0 or sh <= 0:
        return None
    if sw / sh >= ASP:
        rh, rw = sh, min(sw, int(sh * ASP * margin))
    else:
        rw, rh = sw, min(sh, int(sw / ASP * margin))
    rw, rh = rw - rw % 2, rh - rh % 2
    return (sw - rw) // 2, (sh - rh) // 2, rw, rh


class ShotReader:
    """Streams one background shot from ffmpeg (rawvideo) and cuts a zoomed / panned 9:16 window out of every frame."""

    def __init__(self, path, start, dur):
        self.path = path
        sw, sh = probe_size(path)
        reg = shot_region(sw, sh)
        self.last = None
        self.proc = None
        if not reg:
            self.rw, self.rh = 360, 640
            return
        self.x0, self.y0, self.rw, self.rh = reg
        vf = f"crop={self.rw}:{self.rh}:{self.x0}:{self.y0},fps={FPS},{GRADE}"
        self.proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.3f}", "-stream_loop", "-1", "-i", str(path),
             "-t", f"{dur + 0.3:.3f}", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self.rw * self.rh * 3 * 3)
        self.size = self.rw * self.rh * 3

    def read(self):
        if self.proc:
            buf = self.proc.stdout.read(self.size)
            if len(buf) == self.size:
                self.last = Image.frombytes("RGB", (self.rw, self.rh), buf)
        if self.last is None:
            self.last = Image.new("RGB", (self.rw, self.rh), (40, 44, 52))
        return self.last

    def frame(self, z, pan, dx=0.0, dy=0.0):
        im = self.read()
        cw = min(self.rw, self.rh * ASP)
        ch = cw / ASP
        bw, bh = cw / z, ch / z
        cx = self.rw / 2 + pan * max(0.0, self.rw - bw) / 2 + dx * bw / CW
        cy = self.rh / 2 + dy * bh / CH
        x0 = min(max(cx - bw / 2, 0), self.rw - bw)
        y0 = min(max(cy - bh / 2, 0), self.rh - bh)
        return im.resize((CW, CH), Image.BICUBIC, box=(x0, y0, x0 + bw, y0 + bh))

    def close(self):
        if self.proc:
            try:
                self.proc.stdout.close()
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None


ORB = ["..GGGG..", ".GYYYYG.", "GYYWWYYG", "GYWYYYYG", "GYYYYYYG", "GYYYYYYG", ".GYYYYG.", "..GGGG.."]
ORB_COL = {"G": (86, 190, 40, 255), "Y": (196, 238, 56, 255), "W": (250, 255, 176, 255)}


def orb_tile(px=4):
    im = Image.new("RGBA", (8 * px + 20, 8 * px + 20), (0, 0, 0, 0))
    glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([2, 2, im.width - 3, im.height - 3], fill=(170, 255, 60, 90))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5)))
    d = ImageDraw.Draw(im)
    for y, row in enumerate(ORB):
        for x, c in enumerate(row):
            if c in ORB_COL:
                d.rectangle([10 + x * px, 10 + y * px, 10 + (x + 1) * px - 1, 10 + (y + 1) * px - 1], fill=ORB_COL[c])
    return im


class Renderer:
    """plan -> frames.  Everything that moves is a pure function of time t, so the same code draws test frames."""

    def __init__(self, plan):
        self.p = plan
        self.acc = plan["accent"]
        self.shade = shade_layer()
        self.white = Image.new("RGB", (CW, CH), (255, 255, 255))
        self.reader, self.si = None, -1
        self.memo = {}
        p = self.p
        qw = [w.upper() for w, _ in p["q_words"]]
        qcol = [RED if f == "r" else self.acc if f == "y" else WHITE for _, f in p["q_words"]]
        self.q_words, self.q_col = qw, qcol
        self.hook_size, self.hook_pos, _ = layout_words(qw, qcol, HOOK_SIZES, 920, HOOK_CX, HOOK_CY, 3, max_h=600)
        self.head_size, hp, hh = layout_words(qw, qcol, HEAD_SIZES, 880, 540, 0, 2, max_h=160)
        self.panel_y0, self.panel_h = 244, int(hh + 44)
        dy = self.panel_y0 + self.panel_h / 2
        self.head_pos = [dict(h, y=h["y"] + dy) for h in hp]
        self.panel = panel(1000, self.panel_h, fill=(12, 16, 22, 242), border=self.acc, bw=6)
        self.tab = self._tab()
        self.vis_top = self.panel_y0 + self.panel_h + 30
        self.card = self._card()
        self.stamp = self._stamp()
        self.orb = orb_tile()
        self.orbs = self._orbs()
        self.lock = {False: lock_icon(6, False), True: lock_icon(6, True)}

    # ---- static pieces ----
    def _tab(self):
        p = self.p
        a = chip(f"CASE #{p['case_no']:03d}", INK, self.acc, 28, 14)
        b = chip(p["label"], self.acc, (14, 18, 24), 28, 14)
        im = Image.new("RGBA", (a.width + b.width + 8, a.height), (0, 0, 0, 0))
        im.alpha_composite(a, (0, 0))
        im.alpha_composite(b, (a.width + 8, 0))
        return im

    def _card(self):
        short = str(self.p["reveal"]["short"]).upper()
        words = short.split() or [short]
        size, lines = split_lines(words, 840, [230, 200, 176, 156, 140, 126, 112, 100, 90], max_lines=2, max_h=350, lh=0.93)
        rows = [" ".join(words[i] for i in ln) for ln in lines]
        tiles = [text_tile(r, size, GOLD if k == 0 else WHITE) for k, r in enumerate(rows)]
        th = sum(t.height for t in tiles) - int(size * 0.2) * (len(tiles) - 1)
        w = min(960, max(max(t.width for t in tiles) + 90, 700))
        h = th + 150
        im = panel(w, h, fill=(10, 14, 20, 252), border=GREEN, bw=8)
        lab = text_tile("THE ANSWER", 32, GREEN, "pixel", stroke=4, shadow=False)
        im.alpha_composite(lab, ((w - lab.width) // 2, 26))
        y = 88
        for t in tiles:
            im.alpha_composite(t, ((w - t.width) // 2, y))
            y += t.height - int(size * 0.2)
        return im

    def _stamp(self):
        tt = text_tile("SOLVED", 62, RED, "pixel", stroke=0, shadow=False, pad=0)
        w, h = tt.width + 56, tt.height + 44
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, w - 1, h - 1], outline=RED + (255,), width=8, fill=(255, 255, 255, 235))
        d.rectangle([14, 14, w - 15, h - 15], outline=RED + (255,), width=3)
        im.alpha_composite(tt, (28, 22))
        return im.rotate(8, resample=Image.BICUBIC, expand=True)

    def _orbs(self):
        rnd = random.Random(11)
        out = []
        for _ in range(20):
            ang = rnd.uniform(-math.pi * 0.98, -math.pi * 0.02)
            sp = rnd.uniform(260, 620)
            out.append({"vx": math.cos(ang) * sp, "vy": math.sin(ang) * sp, "delay": rnd.uniform(0, 0.16),
                        "size": rnd.choice([0.8, 1.0, 1.0, 1.25]), "fly": rnd.uniform(0.34, 0.52)})
        return out

    # ---- timeline helpers ----
    def shot_at(self, t):
        shots = self.p["shots"]
        while self.si < len(shots) - 1 and (self.si < 0 or t >= shots[self.si]["t1"] - 1e-6):
            if self.reader:
                self.reader.close()
            self.si += 1
            s = shots[self.si]
            self.reader = ShotReader(s["clip"], s["start"], s["t1"] - s["t0"])
        return shots[self.si]

    def fx_state(self, t):
        sx = sy = fl = 0.0
        for e in self.p["fx"]:
            u = t - e["t"]
            if u < 0:
                continue
            if e["kind"] == "shake" and u < 0.5:
                a = e["amp"] * math.exp(-u / 0.11)
                sx += a * math.sin(u * 74)
                sy += a * math.cos(u * 63)
            elif e["kind"] == "flash" and u < 0.3:
                fl = max(fl, e["amp"] * (1 - u / 0.3) ** 2)
        return sx, sy, fl

    # ---- layers ----
    def background(self, t, sx, sy):
        s = self.shot_at(t)
        u, dur = t - s["t0"], max(0.1, s["t1"] - s["t0"])
        z = (1 + 0.07 * u / dur) * (1 + s.get("punch", 0.13) * (1 - min(1.0, u / 0.26)) ** 2)
        return self.reader.frame(z, s.get("pan", 0.0) * (2 * u / dur - 1), sx * 0.7, sy * 0.7)

    def draw_hero(self, base, t, sx, sy):
        p = self.p
        heroes = p.get("heroes") or []
        if not heroes or t > p["dock0"] + 0.4:
            return
        s = 0.90 + 0.10 * ease_out_back(t / 0.30, 2.4)
        if t > p["dock0"]:
            s *= max(0.0, 1 - ease_in_out((t - p["dock0"]) / 0.3))
        n = len(heroes)
        for k, tile in enumerate(heroes):
            x = 540 if n == 1 else (300 if k == 0 else 780)
            y = HERO_CY + 9 * math.sin(t * 3.0 + k * 1.7)
            blit(base, tile, x + sx, y + sy, s)
        if n == 2:
            vs = text_tile("VS", 112, GOLD)
            blit(base, vs, 540 + sx, HERO_CY + sy, s * (1 + 0.06 * math.sin(t * 6)))

    def draw_header(self, base, t):
        p = self.p
        d0, d1 = p["dock0"], p["dock1"]
        e = ease_in_out((t - d0) / max(0.05, d1 - d0))
        blit(base, self.tab, 40 + self.tab.width / 2, 204)
        if e > 0.01:
            blit(base, self.panel, 540, self.panel_y0 + self.panel_h / 2, 1.0, 0.0, e)
        docked = t >= d1
        n = max(1, len(self.hook_pos))
        for rank, (hp, hd) in enumerate(zip(self.hook_pos, self.head_pos)):
            i = hp["i"]
            ts = p["q_t"][i]
            if docked:
                blit(base, text_tile(self.q_words[i], self.head_size, self.q_col[i]), hd["x"], hd["y"])
                continue
            spoken = t >= ts - 0.02
            tile = text_tile(self.q_words[i], self.hook_size, self.q_col[i] if spoken else GHOST)
            pop = 1 + 0.22 * (1 - clamp((t - ts) / 0.17)) ** 2 if spoken else 1.0
            ei = ease_in_out((t - d0 - 0.18 * (d1 - d0) * rank / n) / max(0.05, 0.82 * (d1 - d0)))
            sc = lerp(1.0, self.head_size / self.hook_size, ei) * pop
            blit(base, tile, lerp(hp["x"], hd["x"], ei), lerp(hp["y"], hd["y"], ei) + 0.06 * self.hook_size * (1 - ei), sc)

    def visual_at(self, t):
        for v in self.p["visuals"]:
            if v["t0"] <= t < v["t1"]:
                return v
        return None

    def draw_visual(self, base, t, sx, sy):
        v = self.visual_at(t)
        if v is None:
            return
        u = t - v["t0"]
        left = v["t1"] - t
        out = 1.0 - 0.4 * clamp(1 - left / 0.10)
        if v["kind"] == "sticker":
            s = (0.5 + 0.5 * ease_out_back(u / 0.30)) * out
            blit(base, v["tile"], 540 + v.get("dx", 0) + sx, VIS_CY + 10 * math.sin(u * 3.1) + sy, s)
        elif v["kind"] == "viz":
            uu = u - v.get("delay", 0.0)
            if uu < 0:
                return
            key = (id(v), int(min(uu, 1.3) * FPS))
            if key not in self.memo:
                if len(self.memo) > 80:
                    self.memo.clear()
                self.memo[key] = VIZ[v["spec"]["type"]](v["spec"], min(uu, 1.3), self.acc)
            tile, vs, _ = self.memo[key]
            fit = min(1.15, 580 / tile.height, 960 / tile.width)
            pop = 0.55 + 0.45 * ease_out_back(uu / 0.26)
            blit(base, tile, 540 + sx, VIS_CY + sy, fit * vs * pop * out)
        elif v["kind"] == "mystery":
            k = 1 - clamp(left / 1.4)                          # 0 -> 1 while the reveal comes closer
            s = (0.4 + 0.6 * ease_out_back(u / 0.34, 2.2)) * (1 + 0.05 * math.sin(u * 8) + 0.10 * k * k)
            ang = (3.0 + 7.0 * k) * math.sin(u * (7 + 10 * k))
            blit(base, v["tile"], 540 + sx, VIS_CY + 8 * math.sin(u * 3.0) + sy, s * out, ang)

    def draw_reveal(self, base, t, sx, sy):
        r = self.p["reveal"]
        u = t - r["t"]
        if u < 0:
            return
        s = 0.45 + 0.55 * ease_out_back(u / 0.34, 2.4)
        blit(base, self.card, 540 + sx, CARD_CY + sy, s)
        us = u - 0.5
        if us >= 0:
            blit(base, self.stamp, 735 + sx, CARD_CY + self.card.height / 2 - 42 + sy, 1 + 1.7 * (1 - ease_out_cubic(us / 0.2)),
                 0.0, clamp(us / 0.07))
        sxo, syo = 540, CARD_CY + self.card.height / 2 - 30
        tx, ty = LOCK_CX, BAR_Y + BAR_H / 2
        for o in self.orbs:
            v = u - o["delay"] - 0.05
            if v < 0:
                continue
            if v < o["fly"]:
                x = sxo + o["vx"] * v * (1 - 0.8 * v)
                y = syo + o["vy"] * v + 520 * v * v
            else:
                v0 = o["fly"]
                x0 = sxo + o["vx"] * v0 * (1 - 0.8 * v0)
                y0 = syo + o["vy"] * v0 + 520 * v0 * v0
                w = (v - v0) / 0.6
                if w >= 1:
                    continue
                x, y = lerp(x0, tx, w * w), lerp(y0, ty, w * w)
            blit(base, self.orb, x, y, o["size"])

    def draw_captions(self, base, t):
        ch = None
        for c in self.p["caps"]:
            if c["t0"] <= t < c["t1"]:
                ch = c
                break
        if ch is None:
            return
        if "pos" not in ch:
            words = [w.upper() for w, _, _, _ in ch["words"]]
            size, pos, _ = layout_words(words, [WHITE] * len(words), CAP_SIZES, 840, CAP_CX, CAP_CY, 2, max_h=250)
            ch["pos"], ch["size"], ch["txt"] = pos, size, words
        act = 0
        for k, (_, _, s0, _) in enumerate(ch["words"]):
            if t >= s0 - 0.01:
                act = k
        for it in ch["pos"]:
            i = it["i"]
            _, flag, s0, _ = ch["words"][i]
            col = RED if flag == "r" else self.acc if flag == "y" else GOLD if i == act else WHITE
            sc = 1.0
            if i == act:
                sc = 1 + 0.2 * (1 - clamp((t - s0) / 0.12)) ** 2
            blit(base, text_tile(ch["txt"][i], ch["size"], col), it["x"], it["y"] + 0.05 * ch["size"], sc)

    def draw_bar(self, base, t):
        p = self.p
        r = p["reveal"]["t"]
        d = ImageDraw.Draw(base)
        prog = clamp(t / r)
        done = t >= r
        d.rectangle([BAR_X0 - 5, BAR_Y - 5, BAR_X1 + 5, BAR_Y + BAR_H + 5], fill=(8, 10, 12))
        d.rectangle([BAR_X0, BAR_Y, BAR_X1, BAR_Y + BAR_H], fill=(50, 55, 64))
        fw = int((BAR_X1 - BAR_X0) * prog)
        if fw > 0:
            col, hi = ((255, 205, 30), (255, 240, 150)) if done else ((88, 209, 59), (160, 245, 120))
            d.rectangle([BAR_X0, BAR_Y, BAR_X0 + fw, BAR_Y + BAR_H], fill=col)
            d.rectangle([BAR_X0, BAR_Y, BAR_X0 + fw, BAR_Y + 8], fill=hi)
        for k in range(1, 10):
            x = BAR_X0 + (BAR_X1 - BAR_X0) * k // 10
            d.rectangle([x - 1, BAR_Y, x + 1, BAR_Y + BAR_H], fill=(8, 10, 12))
        lk = self.lock[done]
        sc = 1.0 + (0.7 * (1 - ease_out_cubic((t - r) / 0.35)) if done and t - r < 0.35 else 0.0)
        blit(base, lk, LOCK_CX, BAR_Y + BAR_H / 2, sc)
        cl = p.get("cliff")
        if done:
            txt, col = "SOLVED!", GOLD
        elif cl and cl[0] <= t < cl[1] and int(t * 4) % 2 == 0:
            txt, col = "WAIT FOR IT!", ORANGE
        else:
            txt, col = f"ANSWER IN {max(1, math.ceil(r - t))}S", (210, 214, 222)
        tt = text_tile(txt, 32, col, "pixel", stroke=4, shadow=False)
        base.paste(tt, (BAR_X0 - 6, BAR_Y - 8 - tt.height), tt)

    # ---- one frame ----
    def frame(self, t):
        sx, sy, fl = self.fx_state(t)
        base = self.background(t, sx, sy)
        base.paste(self.shade, (0, 0), self.shade)
        self.draw_hero(base, t, sx, sy)
        self.draw_visual(base, t, sx, sy)
        self.draw_reveal(base, t, sx, sy)
        self.draw_header(base, t)
        self.draw_captions(base, t)
        self.draw_bar(base, t)
        if fl > 0.02:
            base = Image.blend(base, self.white, min(1.0, fl))
        return base

    def close(self):
        if self.reader:
            self.reader.close()
            self.reader = None


def encode(plan, audio, out, progress=True):
    """Pipe every composed frame into ffmpeg together with the finished audio track."""
    r = Renderer(plan)
    total = plan["total"]
    n = int(round(total * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{CW}x{CH}",
           "-r", str(FPS), "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac",
           "-b:a", "192k", "-ar", "44100", "-ac", "2", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for i in range(n):
            enc.stdin.write(r.frame(i / FPS).tobytes())
            if progress and i % 150 == 149:
                print(f"  frames {i + 1}/{n} ({time.time() - t0:.0f}s)")
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("ffmpeg encode failed")
    finally:
        r.close()
        if enc.poll() is None:
            enc.kill()
    return out


# ---- Gemini (free tier: Flash text models; the list is discovered, nothing is hard-coded) ---------------------------
GEM = "https://generativelanguage.googleapis.com/v1beta"
GEM_FALLBACK = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
_MODELS, _COOL = None, {}
_SKIP_MODEL = re.compile(r"image|tts|audio|live|embed|thinking|robotics|computer|vision|learnlm|gemma|exp|aqa|imagen|veo|"
                         r"customtools", re.I)


def gemini_models():
    """Flash models this key can call, best first: newest version, full Flash before Lite, stable before preview."""
    global _MODELS
    if _MODELS is not None:
        return _MODELS
    chosen = [m.strip() for m in env("GEMINI_MODEL", "").split(",") if m.strip()]
    found = []
    try:
        r = requests.get(f"{GEM}/models", params={"key": os.environ["GEMINI_API_KEY"], "pageSize": 200}, timeout=30)
        r.raise_for_status()
        for m in r.json().get("models", []):
            name = m.get("name", "").split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []) or _SKIP_MODEL.search(name):
                continue
            mt = re.match(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?", name)
            if mt:
                score = float(mt.group(1)) * 10 + (0 if mt.group(2) else 4) - (2 if "preview" in name else 0)
                found.append((score, name))
    except Exception as e:
        print("gemini model list unavailable:", type(e).__name__)
    ranked = [n for _, n in sorted(found, reverse=True)]
    _MODELS = list(dict.fromkeys(chosen + ranked[:4] + GEM_FALLBACK))
    print("gemini models:", ", ".join(_MODELS[:6]))
    return _MODELS


def _json_from(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


def gemini(prompt, ok, rounds=5, parts=None, temperature=None):
    """JSON from Gemini that passes ok(data). Models rotate on errors and rate limits (429 cools a model down)."""
    key = os.environ["GEMINI_API_KEY"]
    for _ in range(rounds):
        for model in gemini_models():
            if _COOL.get(model, 0) > time.time():
                continue
            try:
                cfg = {"responseMimeType": "application/json"}
                if temperature is not None:
                    cfg["temperature"] = temperature
                r = requests.post(f"{GEM}/models/{model}:generateContent", params={"key": key}, timeout=(15, 150),
                                  json={"contents": [{"parts": parts or [{"text": prompt}]}], "generationConfig": cfg})
                if r.status_code in (429, 500, 502, 503, 504):
                    _COOL[model] = time.time() + (75 if r.status_code == 429 else 25)
                    print(model, r.status_code)
                    continue
                if not r.ok:
                    _COOL[model] = time.time() + 900
                    print(model, r.status_code, r.text[:150].replace("\n", " "))
                    continue
                cand = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in cand if not p.get("thought"))
                data = _json_from(text)
                if ok(data):
                    return data
                print(model, "unusable JSON")
            except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
                print(model, "error:", type(e).__name__)
        wait = [t - time.time() for t in _COOL.values() if t > time.time()]
        time.sleep(min(80, max(8, min(wait))) if len(wait) >= len(gemini_models()) else 12)
    raise SystemExit("Gemini unavailable, try later")


# ---- Question types: every video is ONE question; the type is what the bot learns about ---------------------------
QTYPES = {
    "can": dict(label="CAN IT?", accent=CYAN, subjects=1, playlist="Can It? Minecraft Case Files",
                ask="a capability test: 'Can [familiar thing] [do something surprising]?' The answer is YES or NO, with a "
                    "twist the viewer did not expect.",
                tags=["Minecraft can it", "Minecraft tricks"]),
    "howmany": dict(label="HOW MANY?", accent=GOLD, subjects=1, playlist="How Many? Minecraft Case Files",
                    ask="a number question: 'How many [units] does it take to ...?' The answer is ONE exact number "
                        "from the source text.",
                    tags=["Minecraft how many", "Minecraft numbers"]),
    "what_if": dict(label="WHAT IF?", accent=ORANGE, subjects=1, playlist="What If? Minecraft Case Files",
                    ask="a consequence question: 'What happens if you [specific action]?' The answer is the exact, "
                        "surprising result.",
                    tags=["Minecraft what if", "Minecraft experiments"]),
    "why": dict(label="WHY?", accent=PURPLE, subjects=1, playlist="Why? Minecraft Case Files",
                ask="a mystery question: 'Why does [familiar thing] [do something odd]?' The answer is the real in-game "
                    "reason.",
                tags=["Minecraft why", "Minecraft mysteries"]),
    "myth": dict(label="TRUE OR FALSE?", accent=RED, subjects=1, playlist="Myth or Fact? Minecraft Case Files",
                 ask="a myth check: 'Is it true that [popular belief players have]?' The answer confirms or busts it "
                     "with the exact truth.",
                 tags=["Minecraft myths", "Minecraft true or false"]),
    "which": dict(label="WHICH WINS?", accent=GREEN, subjects=2, playlist="Which Wins? Minecraft Case Files",
                  ask="a matchup of two famous rivals of the same kind: 'Which is [stronger/faster/rarer]: A or B?' "
                      "The answer is decided by numbers from the source text.",
                  tags=["Minecraft versus", "Minecraft who wins"]),
    "new": dict(label="NEW UPDATE", accent=(110, 190, 255), subjects=1, playlist="New Update Minecraft Case Files",
                ask="a question about the newest update: 'What is the best new thing in [version]?' or 'Is [new "
                    "thing] actually useful?' The answer names the new thing and why it matters.",
                tags=["Minecraft update", "Minecraft news"]),
}
SERIES = "Terra Incognita: Minecraft Case Files"
SCENES = ["plains", "forest", "desert", "snow", "ocean", "mountain", "cave", "nether", "end", "village", "build",
          "farm", "fight", "night", "redstone", "any"]
CATS = ["Category:Mobs", "Category:Blocks", "Category:Items", "Category:Generated structures",
        "Category:Game mechanics", "Category:Removed features", "Category:Unused features"]
POPULAR = [   # subjects every player knows: the angle is what has to be fresh
    "Creeper", "Enderman", "Zombie", "Skeleton", "Spider", "Cave Spider", "Villager", "Iron Golem", "Snow Golem",
    "Warden", "Ender Dragon", "Wither", "Herobrine", "Piglin", "Zombified Piglin", "Ghast", "Blaze", "Slime",
    "Witch", "Pillager", "Ravager", "Evoker", "Vindicator", "Phantom", "Drowned", "Husk", "Stray", "Silverfish",
    "Endermite", "Guardian", "Elder Guardian", "Shulker", "Wolf", "Cat", "Axolotl", "Bee", "Fox", "Panda", "Parrot",
    "Turtle", "Llama", "Horse", "Pig", "Cow", "Chicken", "Sheep", "Rabbit", "Squid", "Dolphin", "Allay", "Sniffer",
    "Camel", "Armadillo", "Breeze", "Wandering Trader", "Hoglin", "Strider", "Mooshroom", "Magma Cube", "Vex",
    "Diamond", "Netherite", "Obsidian", "TNT", "Bedrock", "Elytra", "Totem of Undying", "Trident", "Enchanting Table",
    "Anvil", "Beacon", "Nether Portal", "End Portal", "Sculk Sensor", "Redstone Dust", "Ender Pearl", "Golden Apple",
    "Enchanted Golden Apple", "Shield", "Bed", "Lava", "Cobweb", "Cactus", "Soul Sand", "Ancient Debris", "Netherrack",
    "End Stone", "Dragon Egg", "Command Block", "Spawner", "Crying Obsidian", "Respawn Anchor", "Lightning Rod",
    "Ancient City", "Stronghold", "End City", "Bastion Remnant", "Nether Fortress", "Woodland Mansion",
    "Ocean Monument", "Mineshaft", "Trial Chamber", "Desert Temple", "Jungle Temple", "Village", "Pillager Outpost",
    "The Nether", "The End", "Deep Dark", "Mushroom Fields", "Mace", "Crossbow", "Bow", "Fishing Rod", "Compass",
    "Clock", "Map", "Cake", "Pumpkin", "Honey Block", "Slime Block", "Piston", "Hopper", "Observer", "Dispenser",
    "Steve", "Alex", "Notch", "Far Lands", "Entity 303", "Null", "Water", "Fire", "Gravity", "Sleeping", "Hunger",
]


# ---- Minecraft Wiki: text and pictures -------------------------------------------------------------------------------
WIKI_OK = True


def wiki_get(**p):
    global WIKI_OK
    if not WIKI_OK:
        return {}
    try:
        r = requests.get(MCWIKI, params={**p, "format": "json", "formatversion": 2, "redirects": 1},
                         headers=UA, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("minecraft.wiki unavailable, using Gemini knowledge + clips only:", type(e).__name__)
        WIKI_OK = False
        return {}


def wiki_page(title, cap=12000):
    """(title, plain text, image names) of a Minecraft Wiki page, or (None, None, [])."""
    d = wiki_get(action="parse", page=title, prop="text|images", disabletoc=1).get("parse")
    if not d:
        return None, None, []
    t = re.sub(r"(?s)<(style|script|sup)[^>]*>.*?</\1>", " ", d.get("text", ""))
    t = re.sub(r"<br\s*/?>|</(p|li|tr|h\d|div)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()
    t = re.split(r"\n\s*(?:References|Navigation|External links|See also|Gallery)\s*(?:\[edit\])?\s*\n", t)[0]
    return d.get("title", title), t[:cap], d.get("images", [])


def wiki_images(title, names=None, limit=4):
    """(url, credit) for pictures of a page: lead image first, no icons/sprites."""
    if not IMAGES:
        return []
    if names is None:
        names = wiki_get(action="parse", page=title, prop="images").get("parse", {}).get("images", [])
    lead = (wiki_get(action="query", prop="pageimages", piprop="name", titles=title)
            .get("query", {}).get("pages", [{}]) or [{}])[0].get("pageimage")
    names = [n for n in names if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and not SKIP_IMG.search(n)]
    if lead in names:
        names.remove(lead)
        names.insert(0, lead)
    if not names:
        return []
    pages = wiki_get(action="query", prop="imageinfo", iiprop="url|size", iiurlwidth=1200,
                     titles="|".join("File:" + n for n in names[:40])).get("query", {}).get("pages", [])
    info = {p["title"][5:].replace(" ", "_"): (p.get("imageinfo") or [{}])[0] for p in pages}
    out = []
    for n in names:
        ii = info.get(n.replace(" ", "_"), {})
        if min(ii.get("width", 0), ii.get("height", 0)) >= 150 and (ii.get("thumburl") or ii.get("url")):
            out.append((ii.get("thumburl") or ii.get("url"), n.rsplit(".", 1)[0].replace("_", " ")))
        if len(out) >= limit:
            break
    return out


def download(url, name):
    """Downloads a picture and returns a PNG that really decodes (some webp files do not), or None."""
    p = W / name
    try:
        r = requests.get(url, headers=UA, timeout=120)
        r.raise_for_status()
        p.write_bytes(r.content)
        png = p.with_name(p.stem + "_ok.png")
        try:
            Image.open(p).convert("RGBA").save(png)
        except Exception:
            t = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p), "-frames:v", "1", str(png)],
                               capture_output=True)
            if t.returncode != 0 or not png.exists():
                raise ValueError("cannot decode image")
        p.unlink(missing_ok=True)
        return png.resolve()
    except Exception as e:
        print("skip image:", str(e)[:80])


def fetch_images(title, names, prefix, need):
    """Downloaded pictures of one wiki page (lead picture first)."""
    imgs, seen = [], set()
    for url, _ in wiki_images(title, names, limit=need + 2):
        key = url.split("/")[-1].lower()
        if key in seen or len(imgs) >= need:
            continue
        seen.add(key)
        p = download(url, f"{prefix}{len(imgs)}{pathlib.Path(url.split('?')[0]).suffix.lower() or '.png'}")
        if p:
            imgs.append(p)
    return imgs


# ---- Topic: a famous subject + a question it can answer (or the newest update) --------------------------------------
def candidates(limit=12):
    """Random wiki subjects from the categories (and a few subcategories), not used lately."""
    def members(cat):
        try:
            return wiki_get(action="query", list="categorymembers", cmtitle=cat, cmtype="page|subcat",
                            cmlimit=500).get("query", {}).get("categorymembers", [])
        except Exception:
            return []
    titles = set()
    for cat in random.sample(CATS, 4):
        got = members(cat)
        titles |= {m["title"] for m in got if m.get("ns") == 0}
        subs = [m["title"] for m in got if m.get("ns") == 14]
        for sub in random.sample(subs, min(2, len(subs))):
            titles |= {m["title"] for m in members(sub) if m.get("ns") == 0}
    seen = recent_titles(400)
    titles = [t for t in titles if t not in seen and not t.startswith(("List of", "Category"))]
    return random.sample(titles, min(limit, len(titles)))


def popular(limit=16):
    seen = recent_titles(COOLDOWN)
    pool = [t for t in POPULAR if t not in seen]
    return random.sample(pool, min(limit, len(pool)))


VER = re.compile(r"^Java Edition (\d{2})\.(\d+)(?:\.(\d+))?(?:-(snapshot|pre|rc)-?(\d+))?$", re.I)
STAGE = {"snapshot": 0, "pre": 1, "rc": 2, "": 3}


def newest_versions():
    """[(page title, sort key)] of Java Edition versions of this and last year, oldest first."""
    yy = time.gmtime().tm_year % 100
    found = []
    for y in (yy, yy - 1):
        r = wiki_get(action="query", list="allpages", apprefix="Java Edition 2", apfrom=f"Java Edition {y}",
                     aplimit=500, apnamespace=0)
        for p in r.get("query", {}).get("allpages", []):
            m = VER.match(p["title"])
            if m:
                found.append((p["title"], (int(m[1]), int(m[2]), int(m[3] or 0), STAGE[(m[4] or "").lower()],
                                           int(m[5] or 0))))
        if found:
            break
    return sorted(found, key=lambda x: x[1])


def news_topic():
    """The newest release (or the newest snapshot of the next one) as a topic; None if the wiki has nothing."""
    try:
        vers = newest_versions()
        if not vers:
            return None
        rel = [v for v in vers if v[1][3] == 3]
        cands = [v for v in ((rel[-1] if rel else None), vers[-1]) if v]
        cands = list(dict.fromkeys(c[0] for c in cands))
        count = lambda t: sum(t in titles_of(e) for e in used[-30:])
        cands.sort(key=count)                              # the one we covered less lately
        for title in cands:
            got, text, names = wiki_page(title, cap=14000)
            if got and text and len(text) > 1500:
                ver = re.sub(r"^Java Edition ", "", got)
                return {"articles": [got], "key": got, "text": text, "images": [names], "angle": "newest update",
                        "version": ver}
    except Exception as e:
        print("news lookup failed:", type(e).__name__, str(e)[:100])
    return None


def pick_topic(qtype):
    """{articles, key, text, images, angle, ...} for the question type; raises SystemExit if nothing usable."""
    qt = QTYPES[qtype]
    if qtype == "new":
        t = news_topic()
        if t:
            return t
        raise LookupError("no fresh update found")
    n = qt["subjects"]
    heads = [e["q"] for e in used[-30:] if e.get("q")]
    wiki_c, pop = candidates(40), popular(40)
    for _ in range(5):
        cands = random.sample(pop, min(len(pop), 24)) + random.sample(wiki_c, min(len(wiki_c), 10))
        random.shuffle(cands)
        fmt = ('{"article": "exact page title", "angle": "one sentence: the specific surprising question to answer"}'
               if n == 1 else
               '{"article": "exact page title of the first", "article2": "exact page title of the second", '
               '"angle": "one sentence: why this matchup is interesting"}')
        ask = (f"Pick the subject of ONE viral Minecraft YouTube Short built on {qt['ask']} "
               "Pick subjects EVERY player recognizes at first sight (famous mobs, blocks, items, bosses, "
               "structures, dimensions, or famous myths): viewers only stop for things they know. Freshness must come "
               "from the ANGLE (a surprising, specific, little-known side of it), never from obscurity. ")
        if len(cands) >= 8:
            ask += f"Choose ONLY from this list of exact Minecraft Wiki page titles: {cands}. "
        if heads:
            ask += f"Do NOT repeat the question or angle of these recent videos: {heads}. "
        ask += f"Return JSON: {fmt}"
        keys = ["article", "article2"][:n]
        t = gemini(ask, lambda d: isinstance(d, dict) and all(d.get(k) for k in keys))
        want = [str(t[k]) for k in keys]
        angle = str(t.get("angle") or "")
        if len(set(want)) != n:
            continue
        if not WIKI_OK:                               # wiki blocked: script from Gemini's own knowledge
            return {"articles": want, "key": " vs ".join(want), "text": None, "images": [[]] * n, "angle": angle}
        got = [wiki_page(w, cap=6500 if n == 2 else 12000) for w in want]
        arts = [a for a, _, _ in got]
        if WIKI_OK and all(arts) and len(set(arts)) == n and all(len(x) > 1200 for _, x, _ in got):
            text = got[0][1] if n == 1 else "\n\n".join(f"=== {a} ===\n{x}" for a, x, _ in got)
            return {"articles": arts, "key": " vs ".join(arts), "text": text, "images": [g[2] for g in got],
                    "angle": angle}
        if not WIKI_OK:
            continue
        print("topic rejected:", want)
    raise SystemExit("No usable topic")


# ---- Script: ONE question, 3-4 beats of evidence, ONE answer ----------------------------------------------------------
def plain(t):
    return re.sub(r"\[/?[ry]\]", "", str(t)).strip()


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def toks(t):
    return re.findall(r"[a-z0-9]+", plain(t).lower().replace("'", "").replace("\u2019", "").replace("\u2018", ""))


def parse_marked(text):
    """[(word, flag)] from text with [r]..[/r] / [y]..[/y] markers; flag is 'r', 'y' or ''."""
    out, flag = [], ""
    for raw in str(text).split():
        w, f0 = "", flag
        for part in re.split(r"(\[/?[ry]\])", raw):
            m = re.fullmatch(r"\[(/?)([ry])\]", part)
            if m:
                flag = "" if m.group(1) else m.group(2)
            elif part:
                if not w:
                    f0 = flag
                w += part
        if w:
            out.append((w, f0))
    return out


NUMW = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "hundred": 100, "thousand": 1000, "half": 0.5, "double": 2, "triple": 3}
BANNED = re.compile(r"did you know|in this video|welcome|hey guys|subscribe|like and|let'?s dive|today we|have you ever|"
                    r"you won'?t believe|stay tuned|don'?t forget|smash that|link in", re.I)
HEDGE = re.compile(r"\b(it depends|maybe|might be|not sure|unclear|we don'?t know|nobody knows)\b", re.I)


def numbers_in(text):
    nums = {float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", str(text)) if x.strip(",.")}
    low = str(text).lower()
    nums |= {float(v) for w, v in NUMW.items() if re.search(rf"\b{w}\b", low)}
    return nums


def grounded_number(n, pool):
    """A number is fine when the source has it, or its double/half (HP <-> hearts) or a round percent."""
    return any(abs(n - p) < 1e-6 or abs(n * 2 - p) < 1e-6 or abs(n / 2 - p) < 1e-6 for p in pool)


def quote_ok(src, text_words):
    w = toks(src)
    return len(w) >= 3 and sum(x in text_words for x in w) / len(w) >= 0.8


def viz_numbers(v):
    if v["type"] == "number":
        return numbers_in(v["text"])
    if v["type"] == "hearts":
        return {v["value"]}
    if v["type"] == "grid":
        return {float(v["n"])}
    return {b for _, b in v["items"]}


def normalize_script(d):
    """Typed, trimmed copy of the model's JSON: {question, beats:[{t,scene,show,viz,src}], answer, comment_q, tags}."""
    beats = []
    for b in d.get("beats", []):
        if isinstance(b, str):
            b = {"t": b}
        if not isinstance(b, dict) or not plain(b.get("t", "")):
            continue
        scene = str(b.get("scene") or "any").strip().lower()
        beats.append({"t": str(b["t"]).strip(), "scene": scene if scene in SCENES else "any",
                      "show": str(b.get("show") or "").strip(), "viz": valid_viz(b.get("viz")),
                      "src": str(b.get("src") or "").strip()})
    a = d.get("answer") if isinstance(d.get("answer"), dict) else {}
    scene = str(a.get("scene") or "any").strip().lower()
    answer = {"short": re.sub(r"\s+", " ", plain(a.get("short", ""))).strip().upper(), "t": str(a.get("t", "")).strip(),
              "scene": scene if scene in SCENES else "any", "show": str(a.get("show") or "").strip(),
              "src": str(a.get("src") or "").strip()}
    q = str(d.get("question", "")).strip()
    if q and not plain(q).endswith("?"):
        q = q + "?"
    tags = [re.sub(r"[#\s]+", " ", str(t)).strip() for t in d.get("tags", []) if str(t).strip()][:10]
    return {"question": q, "beats": beats, "answer": answer, "comment_q": plain(d.get("comment_q", "")), "tags": tags,
            "related": [str(x) for x in d.get("related", []) if str(x).strip()][:3]}


def check_script(s, text):
    """List of problems; empty = the script can be filmed. `text` is the wiki text (None = no grounding possible)."""
    errs = []
    q = plain(s["question"])
    n = len(q.split())
    if not 4 <= n <= 11:
        errs.append(f"the question has {n} words; it must have 5-10")
    if BANNED.search(q) or re.match(r"(?i)(in minecraft|did you|have you)", q):
        errs.append("the question starts like a cliche; open with the familiar thing or a question word")
    if not 3 <= len(s["beats"]) <= 4:
        errs.append(f"{len(s['beats'])} beats; exactly 3 or 4 are needed")
    a = s["answer"]
    if not 2 <= len(a["short"]) <= 24:
        errs.append("answer.short must be 2-24 characters (shown big on screen)")
    if not 6 <= len(a["t"].split()) <= 20:
        errs.append("answer.t must be 6-20 words")
    if HEDGE.search(a["t"]) or HEDGE.search(a["short"]):
        errs.append("the answer hedges; it must be definite")
    words_total = n + len(a["t"].split())
    for i, b in enumerate(s["beats"], 1):
        k = len(plain(b["t"]).split())
        words_total += k
        if not 4 <= k <= 14:
            errs.append(f"beat {i} has {k} words; it must have 5-12")
    if not 38 <= words_total <= 72:
        errs.append(f"{words_total} spoken words in total; aim for 42-62 (never below 38 or above 72)")
    alltext = " ".join([q, a["t"]] + [b["t"] for b in s["beats"]])
    if BANNED.search(alltext):
        errs.append("a filler phrase (did you know / welcome / subscribe ...) is not allowed")
    if len({norm(b["t"]) for b in s["beats"]}) < len(s["beats"]):
        errs.append("two beats are the same")
    if text:
        tw = set(toks(text))
        pool = numbers_in(text)
        for label, line in [(f"beat {i}", b) for i, b in enumerate(s["beats"], 1)] + [("the answer", a)]:
            if not quote_ok(line["src"], tw):
                errs.append(f"{label}: src must be an exact quote copied from the source text")
            for x in numbers_in(plain(line["t"])) - numbers_in(line["src"]):
                if not grounded_number(x, pool):
                    errs.append(f"{label}: the number {x:g} is not in the source text")
    return errs


def script_prompt(topic, qtype, recent, errors):
    qt = QTYPES[qtype]
    subject = " vs ".join(topic["articles"])
    if topic.get("text"):
        source = ("SOURCE (Minecraft Wiki). Use ONLY facts stated here; keep names and numbers exact:\n<<<\n"
                  f"{topic['text']}\n>>>\n")
    else:
        source = ("No source text is available: use only accurate, well-established facts about the current Java "
                  "Edition and leave out anything you are not sure about. For every src write \"\".\n")
    extra = {"new": f"The update is {topic.get('version', '')}: choose the single most exciting PLAYER-FACING addition "
                    "or change (a mob, block, biome, item, mechanic), never engine or technical changes.\n",
             "which": "Both subjects appear in the source. The question names both; the answer names the winner.\n"
             }.get(qtype, "")
    fix = ("\nYOUR PREVIOUS ATTEMPT WAS REJECTED. Fix every one of these problems:\n- " + "\n- ".join(errors) + "\n"
           if errors else "")
    return (
        "You write ONE 16-22 second YouTube Short for a Minecraft channel. Format: a CASE FILE. The first second asks "
        "ONE question; the video answers it at the very end, so the viewer has to stay. Viewers decide in ONE second "
        "whether to swipe, so every word must earn the next one.\n"
        "VOICE: a funny, energetic friend who plays Minecraft every day, speaking to ONE viewer. Say 'you'. "
        "Contractions. Short punchy sentences. No greetings, no 'welcome', no 'in this video', no 'did you know'.\n"
        f'SUBJECT: "{subject}". Angle: {topic.get("angle", "")}\n'
        f"QUESTION TYPE: {qt['ask']}\n{extra}{source}"
        "Use behavior, numbers, drops, spawning, mechanics and trivia from the source; ignore version-history lists.\n"
        "THE QUESTION (spoken first, shown on screen from the first frame):\n"
        "- 5-10 words, ends with '?'. Its first 3 words are the familiar thing (a mob, block, item) or a number.\n"
        "- It must be answerable from the source and it NEVER contains or hints at the answer.\n"
        "- Choose a question whose true answer is SURPRISING or goes against what players assume. Skip questions "
        "whose answer every player already knows.\n"
        "- Wrap the single most important word in [r]...[/r] and at most one more word in [y]...[/y].\n"
        "THE BEATS (3 or 4 lines spoken after the question; they are the evidence, NOT the answer):\n"
        "- 5-12 words each. Every beat gives ONE new concrete detail from the source (a number, a name, a condition), "
        "so the viewer feels the answer getting closer. No filler, no repeating the question.\n"
        "- Beat 1 is not a definition: it drops the viewer into a vivid, specific situation or detail, in second person "
        "where it fits. The middle beats raise the stakes with a surprising detail. The LAST beat is the pivot that "
        "makes the viewer wait for the answer (a fresh-worded 'so what really happens?') and never says the answer.\n"
        "THE ANSWER (spoken right after a dramatic pause):\n"
        "- answer.t: 8-18 words. It starts with the answer in 1-3 words and then gives the reason with one number or "
        "condition from the source. Definite, never 'it depends'.\n"
        "- answer.short: the answer in at most 4 words / 22 characters, shown huge on screen, built from the real "
        "answer (e.g. 'YES, BUT <twist>', 'ONLY <N> HITS', '<NAME> WINS', 'NOPE, IT'S <truth>').\n"
        "PER LINE (every beat and the answer):\n"
        "- src: a short exact quote (max 25 words) copied from the SOURCE that proves the line. Every number in the "
        "line must appear in it.\n"
        f"- scene: the gameplay that should play behind the line, one of {SCENES}.\n"
        "- show: the exact Minecraft Wiki page title of the mob/block/item/structure the line is about (its picture is "
        "shown on screen), or \"\" if none. Never invent titles.\n"
        "- viz (optional, at most 2 in the whole video, only when the line states that number): null or one of "
        '{"type":"number","text":"3 HITS"} (a huge phrase of max 14 characters), '
        '{"type":"hearts","value":20,"label":"WARDEN HP"} (health in HP, 2 HP = 1 heart), '
        '{"type":"grid","n":100,"hit":1,"label":"DROP CHANCE"} (odds: hit of n squares, n up to 100), '
        '{"type":"bars","items":[["Netherite",9],["Diamond",7]],"label":"DAMAGE"} (2-4 values compared).\n'
        "TOTAL spoken words (question + beats + answer): 42-62.\n"
        "- comment_q: one short question that asks viewers to CHOOSE or share an experience (not yes/no), max 10 words.\n"
        "- tags: 5-8 search tags. related: 0-3 more exact wiki page titles worth picturing.\n"
        + (f"- Do not reuse the questions of these recent videos: {recent}\n" if recent else "")
        + fix
        + 'Return JSON: {"question": "...", "beats": [{"t": "...", "scene": "cave", "show": "Creeper", "viz": null, '
          '"src": "..."}], "answer": {"t": "...", "short": "...", "scene": "any", "show": "", "src": "..."}, '
          '"comment_q": "...", "tags": ["..."], "related": ["..."]}')


class ScriptError(Exception):
    """The model could not write a script that passes the checks (the bot then tries another subject)."""


SOFT = ("spoken words in total", "words; it must have")      # length only: such a script is still filmable


def gen_script(topic, qtype):
    """The validated script for the topic; the model is asked again with the list of problems if it breaks a rule.
    After 4 attempts the closest script is used only when it breaks nothing but length limits; unproven facts and
    other content problems raise ScriptError (the caller picks another subject)."""
    recent = [e["q"] for e in used[-25:] if e.get("q")]
    errors, best = [], None
    for attempt in range(4):
        raw = gemini(script_prompt(topic, qtype, recent, errors),
                     lambda d: isinstance(d, dict) and d.get("question") and isinstance(d.get("beats"), list)
                     and isinstance(d.get("answer"), dict))
        s = normalize_script(raw)
        errors = check_script(s, topic.get("text"))
        print(f"script attempt {attempt + 1}:", "ok" if not errors else "; ".join(errors)[:300])
        if not errors:
            return s
        rank = (sum(not any(x in e for x in SOFT) for e in errors), len(errors))
        if best is None or rank < best[2]:
            best = (s, errors, rank)
    s, errs, rank = best
    spoken = len(plain(s["question"]).split()) + len(plain(s["answer"]["t"]).split()) + sum(
        len(plain(b["t"]).split()) for b in s["beats"])
    if rank[0] == 0 and len(errs) <= 5 and 30 <= spoken <= 90 and len(s["beats"]) in (3, 4):
        print("using the closest script (only length limits are broken)")
        return s
    raise ScriptError("; ".join(errs)[:200])


def clean_viz(s):
    """Drops graphics whose numbers the line does not say."""
    for b in s["beats"]:
        v = b.get("viz")
        if v and not any(grounded_number(x, numbers_in(plain(b["t"]) + " " + b["src"])) for x in viz_numbers(v)):
            b["viz"] = None
    seen = 0
    for b in s["beats"]:
        if b.get("viz"):
            seen += 1
            if seen > 2:
                b["viz"] = None
    return s


def spoken_lines(s):
    """[(kind, text with [r]/[y] markers)] in the order they are spoken."""
    out = [("hook", s["question"])]
    for i, b in enumerate(s["beats"]):
        out.append(("cliff" if i == len(s["beats"]) - 1 else "beat", b["t"]))
    out.append(("answer", s["answer"]["t"]))
    return out


# ---- Audio: one TTS take per line (own prosody), numpy SFX, music with ducking, 2-pass loudness ----------------------
SR = 44100
PROSODY = {"hook": (4, "+3Hz", "+0%"), "beat": (0, "+0Hz", "+0%"), "cliff": (-4, "-3Hz", "+0%"),
           "answer": (-3, "+0Hz", "+8%")}                  # (rate offset %, pitch, volume) per kind of line


WOBBLE = [(0, 0), (3, 2), (-2, -2), (2, 1), (-1, 2)]      # small rate/pitch drift between lines: less robotic


async def _tts_line(text, path, kind, idx=0):
    import edge_tts
    d_rate, pitch, vol = PROSODY.get(kind, PROSODY["beat"])
    if kind == "beat":
        wr, wp = WOBBLE[idx % len(WOBBLE)]
        d_rate, pitch = d_rate + wr, f"{int(pitch[:-2]) + wp:+d}Hz"
    kw = dict(rate=f"{RATE + d_rate:+d}%", pitch=pitch, volume=vol)
    try:
        comm = edge_tts.Communicate(text, VOICE, boundary="WordBoundary", **kw)
    except TypeError:
        comm = edge_tts.Communicate(text, VOICE, **kw)
    words = []
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append((chunk["text"], chunk["offset"] / 1e7, chunk.get("duration", 0) / 1e7))
    return words


def tts_all(lines):
    """[(kind, text)] -> [(mp3 path, [(word, start s, duration s)])]; every line is its own take, retried on failure."""
    async def go():
        out = []
        for i, (kind, text) in enumerate(lines):
            for attempt in range(4):
                try:
                    p = W / f"line{i}.mp3"
                    words = await _tts_line(text, p, kind, i)
                    if p.stat().st_size < 800:
                        raise RuntimeError("empty audio")
                    out.append((p, words))
                    break
                except Exception as e:
                    print("tts retry", i, type(e).__name__, str(e)[:80])
                    await asyncio.sleep(2 + 2 * attempt)
            else:
                raise SystemExit("Text-to-speech failed")
        return out
    return asyncio.run(go())


def decode(path, start=0.0, dur=None, loop=False):
    cmd = ["ffmpeg", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def voice_line(path, words, target_rms=0.13):
    """Decoded line without leading/trailing silence, level-matched; the word times shift to match the cut."""
    x = decode(path)
    if len(x) < SR // 10:
        raise SystemExit("Text-to-speech returned almost nothing")
    env = np.convolve(np.abs(x), np.ones(441, dtype=np.float32) / 441, mode="same")
    thr = max(0.004, 0.06 * float(env.max()))
    idx = np.nonzero(env > thr)[0]
    a = max(0, int(idx[0]) - int(0.025 * SR))
    b = min(len(x), int(idx[-1]) + int(0.06 * SR))
    y = x[a:b].copy()
    rms = float(np.sqrt(np.mean(y * y))) or 1.0
    y = np.clip(y * min(4.0, target_rms / rms), -0.97, 0.97)
    f, g = int(0.004 * SR), int(0.03 * SR)
    y[:f] *= np.linspace(0, 1, f, dtype=np.float32)
    y[-g:] *= np.linspace(1, 0, g, dtype=np.float32)
    shift = a / SR
    return y, [(w, max(0.0, s - shift), d) for w, s, d in words]


def align(tokens, words, total):
    """(start, end) of every spoken token within its line: from the TTS word timings, else spread evenly."""
    n = len(tokens)
    if n == 0:
        return []
    if words and len(words) >= max(2, n // 2):
        if len(words) == n:
            starts = [w[1] for w in words]
        else:                                          # the TTS split or merged words: map by letter position
            tc, wc = [0], [0]
            for t in tokens:
                tc.append(tc[-1] + max(1, len(norm(t[0]))))
            for w in words:
                wc.append(wc[-1] + max(1, len(norm(w[0]))))
            starts = []
            for k in range(n):
                j = bisect.bisect_right(wc, tc[k] / tc[-1] * wc[-1]) - 1
                starts.append(words[min(len(words) - 1, max(0, j))][1])
    else:
        starts = [0.05 + (total - 0.1) * k / n for k in range(n)]
    starts[0] = 0.0
    for k in range(1, n):
        starts[k] = min(max(starts[k], starts[k - 1] + 0.03), total - 0.05)
    return [(s, starts[k + 1] if k + 1 < n else total) for k, s in enumerate(starts)]


# ---- Synthesized sounds (no samples, nothing to license) ----------------------------------------------------------
_rng = np.random.default_rng(5)


def _t(d):
    return np.arange(int(d * SR), dtype=np.float32) / SR


def _lp(x, n):
    return np.convolve(x, np.ones(n, dtype=np.float32) / n, mode="same")


def sfx_boom(d=1.0, f0=95.0, f1=38.0, noise=0.35):
    t = _t(d)
    ph = 2 * np.pi * np.cumsum(f1 + (f0 - f1) * np.exp(-t / 0.12)) / SR
    return (np.sin(ph) * np.exp(-t / (d * 0.28)) + noise * _lp(_rng.standard_normal(len(t)).astype(np.float32), 40)
            * 4 * np.exp(-t / 0.09)).astype(np.float32)


def sfx_whoosh(d=0.42):
    t = _t(d)
    n = _rng.standard_normal(len(t)).astype(np.float32)
    p = t / d
    y = _lp(n, 90) * 5 * (1 - p) + _lp(n, 8) * 1.4 * p
    return (y * np.sin(np.pi * p) ** 2).astype(np.float32)


def sfx_pop(f0=420.0, f1=980.0, d=0.09):
    t = _t(d)
    ph = 2 * np.pi * np.cumsum(f0 + (f1 - f0) * np.minimum(1, t / 0.05)) / SR
    return (np.sin(ph) * np.exp(-t / 0.035)).astype(np.float32)


def sfx_tick(d=0.05):
    t = _t(d)
    return (0.6 * np.sin(2 * np.pi * 1700 * t) * np.exp(-t / 0.008) +
            0.5 * _rng.standard_normal(len(t)).astype(np.float32) * np.exp(-t / 0.003)).astype(np.float32)


def sfx_slam():
    b = sfx_boom(0.7, 110, 45, 0.6)
    t = _t(0.7)
    return (b + 0.5 * _lp(_rng.standard_normal(len(t)).astype(np.float32), 6) * np.exp(-t / 0.05) * 3).astype(np.float32)


def sfx_riser(d=0.6):
    t = _t(d)
    p = t / d
    ph = 2 * np.pi * np.cumsum(180 + 1500 * p ** 2) / SR
    n = _lp(_rng.standard_normal(len(t)).astype(np.float32), 5) * 2.2
    return ((0.5 * np.sin(ph) + n) * p ** 2.2).astype(np.float32)


def sfx_ping(f=1400.0, d=0.12):
    t = _t(d)
    return (0.7 * np.sin(2 * np.pi * f * t) * np.exp(-t / 0.04) + 0.25 * np.sin(2 * np.pi * f * 2 * t) * np.exp(-t / 0.02)
            ).astype(np.float32)


def sfx_chime():
    out = np.zeros(int(0.7 * SR), dtype=np.float32)
    for k, f in enumerate((523.25, 659.25, 783.99, 1046.5)):
        s = sfx_ping(f, 0.4)
        i = int(k * 0.085 * SR)
        out[i:i + len(s)] += s[:len(out) - i]
    return out


def sfx_thud():
    t = _t(0.3)
    return (np.sin(2 * np.pi * 75 * t) * np.exp(-t / 0.08) + 0.6 * _lp(_rng.standard_normal(len(t)).astype(np.float32), 25)
            * 3 * np.exp(-t / 0.05)).astype(np.float32)


SFX_FN = {"boom": sfx_boom, "whoosh": sfx_whoosh, "pop": sfx_pop, "tick": sfx_tick, "slam": sfx_slam,
          "riser": sfx_riser, "chime": sfx_chime, "thud": sfx_thud, "ping": sfx_ping}


def build_sfx(events, total):
    """events: [(time, name, gain[, kwargs])] -> mono float32 track of `total` seconds."""
    buf = np.zeros(int((total + 0.5) * SR), dtype=np.float32)
    for ev in events:
        t, name, gain = ev[:3]
        fn = SFX_FN.get(name)
        i = int(max(0.0, t) * SR)
        if fn is None or i >= len(buf):
            continue
        s = fn(**(ev[3] if len(ev) > 3 else {}))
        s = s / (float(np.abs(s).max()) or 1.0) * gain
        buf[i:i + len(s)] += s[:len(buf) - i]
    return buf


def pick_music(total):
    """(track, start) for this video: any file under music/ (rotates by run number), else music.mp3, else none."""
    pool = sorted(f for f in MUSIC_DIR.rglob("*") if f.is_file() and f.suffix.lower() in MUSIC_EXT) if MUSIC_DIR.is_dir() else []
    if not pool and pathlib.Path("music.mp3").exists():
        pool = [pathlib.Path("music.mp3")]
    if not pool:
        return None, 0.0
    track = pool[len(used) % len(pool)]
    spare = max(0.0, probe_duration(track) - total - 1)
    start = round(random.uniform(0, spare), 1) if spare > 1 else 0.0
    print("music:", track, "| start:", start)
    return track, start


def duck_curve(voice):
    """1.0 where the voice is silent, ~0.4 under speech (smooth), so music never fights the narration."""
    e = _lp(np.abs(voice), int(0.06 * SR))
    e = np.clip(e / 0.10, 0, 1)
    return 1.0 - 0.6 * _lp(e, int(0.12 * SR))


def loudnorm(src, dst, I=-15.0, TP=-1.5, LRA=9.0):
    """Two-pass EBU R128 normalisation (accurate on short clips); falls back to a plain copy."""
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                            f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
        st = json.loads(m.group())
        af = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:measured_I={st['input_i']}:measured_TP={st['input_tp']}:"
              f"measured_LRA={st['input_lra']}:measured_thresh={st['input_thresh']}:offset={st['target_offset']}:linear=true")
        ffmpeg("-i", src, "-af", af, "-ar", str(SR), "-ac", "1", dst)
    except Exception as e:
        print("loudnorm skipped:", type(e).__name__)
        pathlib.Path(dst).write_bytes(pathlib.Path(src).read_bytes())
    return dst


def mix_audio(voice_parts, sfx_events, total, out):
    """voice_parts: [(start s, samples)]; writes the finished, loudness-normalised wav and returns its path."""
    n = int((total + 0.3) * SR)
    voice = np.zeros(n, dtype=np.float32)
    for t, y in voice_parts:
        i = int(t * SR)
        voice[i:i + len(y)] += y[:max(0, n - i)]
    mix = voice.copy()
    if SFX and sfx_events:
        mix += build_sfx(sfx_events, total)[:n] * 0.34 * SFX_VOL
    track, start = pick_music(total)
    if track:
        try:
            m = decode(track, start, total + 0.3, loop=True)[:n]
            m = np.pad(m, (0, n - len(m)))
            fade = np.minimum(1.0, np.minimum(np.arange(n) / (0.4 * SR), (n - np.arange(n)) / (0.6 * SR))).astype(np.float32)
            mix += m * MUSIC_VOL * duck_curve(voice) * fade
        except Exception as e:
            print("music skipped:", type(e).__name__)
    mix = np.tanh(mix * 0.9) / np.tanh(0.9)
    raw = W / "mix_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(mix, -1, 1) * 32000).astype("<i2").tobytes())
    return loudnorm(raw, out)


# ---- Gameplay clips: probe, describe (Gemini, a few per run), pick by scene -------------------------------------------
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def list_clips():
    d = pathlib.Path("clips")
    return sorted(p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXT) if d.is_dir() else []


def get_fallback_clip():
    for query in ("pixel game", "parkour", "nature"):
        try:
            r = requests.get("https://pixabay.com/api/videos/", timeout=60, params={
                "key": os.environ["PIXABAY_API_KEY"], "q": query, "per_page": 20, "safesearch": "true"}).json()
            if r.get("hits"):
                vids = random.choice(r["hits"])["videos"]
                url = next(vids[k]["url"] for k in ("large", "medium", "small", "tiny") if vids.get(k, {}).get("url"))
                p = W / "fallback.mp4"
                p.write_bytes(requests.get(url, timeout=180).content)
                return p.resolve()
        except Exception as e:
            print("pixabay:", type(e).__name__)


def clip_features(path, w, h, d):
    """(brightness 0-1, motion 0-1) from a 3 s sample at 6 fps; None if it cannot be decoded."""
    try:
        hh = max(2, int(round(96 * h / max(1, w) / 2)) * 2)
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{max(0.0, min(d * 0.4, d - 3.2)):.2f}", "-i",
                              str(path), "-t", "3", "-vf", "fps=6,scale=96:-2,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, timeout=60).stdout
        fr = np.frombuffer(raw, dtype=np.uint8)
        k = len(fr) // (96 * hh)
        if k < 3:
            return None
        fr = fr[:k * 96 * hh].reshape(k, hh, 96).astype(np.float32) / 255
        return round(float(fr.mean()), 3), round(float(np.abs(np.diff(fr, axis=0)).mean()), 4)
    except Exception:
        return None


def clip_frames(path, d, n=3):
    """n small JPEG frames (bytes) spread over the clip."""
    out = []
    for k in range(n):
        p = W / f"tag{k}.jpg"
        t = max(0.0, d * (0.15 + 0.35 * k))
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=384:-2", "-q:v", "5", str(p)], capture_output=True)
        if r.returncode == 0 and p.exists():
            out.append(p.read_bytes())
    return out


class ClipLib:
    """Picks a gameplay clip and a start time for every shot; what Gemini has seen in a clip is remembered in used.json."""

    def __init__(self, files):
        self.files, self.meta, self.recent = list(files), STATE["clips"], []

    def info(self, p):
        m = self.meta.setdefault(p.name, {})
        if "w" not in m:
            w, h = probe_size(p)
            m.update({"w": w, "h": h, "d": round(probe_duration(p), 2)})
        if "mo" not in m and m["d"] > 0:
            f = clip_features(p, m["w"], m["h"], m["d"])
            if f:
                m["br"], m["mo"] = f
        return m

    def pick(self, scene, need, hero=False):
        pool = random.sample(self.files, min(40, len(self.files)))
        weights, cands = [], []
        for p in pool:
            m = self.info(p)
            if m.get("ok") is False or m.get("w", 0) <= 0 or m.get("d", 0) < 1:
                continue
            tags = m.get("tags") or []
            w = 1.0
            if scene != "any" and tags:
                w *= 6.0 if scene in tags else 0.5
            if m["h"] < 540:
                w *= 0.35
            if m.get("mo", 0.02) < 0.006:
                w *= 0.3
            if m.get("br", 0.4) < 0.08:
                w *= 0.3
            if hero:
                w *= 1 + min(4.0, m.get("mo", 0.02) * 60)
            if p in self.recent[-8:]:
                w *= 0.04
            if m["d"] < need + 0.5:
                w *= 0.5
            cands.append(p)
            weights.append(w)
        if not cands:
            return None, 0.0
        p = random.choices(cands, weights)[0]
        self.recent.append(p)
        d = self.meta[p.name]["d"]
        return p, round(random.uniform(0.15, max(0.15, d - need - 0.3)), 2)


def tag_new_clips(lib, limit):
    """Describes up to `limit` clips that Gemini has not seen yet (one multi-image call) and stores tags and quality."""
    todo = [p for p in lib.files if not lib.meta.get(p.name, {}).get("tagged")]
    random.shuffle(todo)
    parts, ids = [], []
    for p in todo[:limit]:
        m = lib.info(p)
        if m.get("w", 0) <= 0 or m.get("d", 0) < 1:
            m.update({"ok": False, "tagged": True})
            continue
        frames = clip_frames(p, m["d"])
        if not frames:
            continue
        ids.append(p)
        parts.append({"text": f"CLIP {len(ids) - 1}:"})
        parts += [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(f).decode()}} for f in frames]
    if not ids:
        return
    tags = [s for s in SCENES if s != "any"]
    prompt = ("You label Minecraft gameplay clips for a video editor. Each clip is shown as 3 frames in time order. For "
              f"every clip return 1-3 tags from {tags} (the environment or action visible) and ok=false only if the clip "
              "is a menu, loading screen, black or blank screen, a chat window, or too blurry to see the game. "
              'Return JSON: {"clips": [{"id": 0, "tags": ["cave"], "ok": true}]}')
    try:
        res = gemini(prompt, lambda d: isinstance(d, dict) and isinstance(d.get("clips"), list) and d["clips"],
                     rounds=2, parts=[{"text": prompt}] + parts)
    except SystemExit:
        print("clip tagging skipped (Gemini busy)")
        return
    done = 0
    for c in res["clips"]:
        try:
            p = ids[int(c["id"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        t = [str(x).lower() for x in c.get("tags", []) if str(x).lower() in tags][:3]
        lib.meta[p.name].update({"tags": t, "ok": bool(c.get("ok", True)), "tagged": True})
        done += 1
    print(f"clips described: {done}/{len(ids)} | library: {len(lib.files)} clips, "
          f"{sum(1 for p in lib.files if lib.meta.get(p.name, {}).get('tagged'))} described")


class Pics:
    """Pictures by wiki page title; hands out a different picture of the same page each time."""

    def __init__(self, imgs):
        self.imgs, self.n = {norm(k): v for k, v in imgs.items() if v}, {}

    def has(self, title):
        return bool(self.imgs.get(norm(title)))

    def get(self, title, advance=True):
        lst = self.imgs.get(norm(title), [])
        if not lst:
            return None
        k = self.n.get(norm(title), 0)
        if advance:
            self.n[norm(title)] = k + 1
        return lst[k % len(lst)]

    def any(self):
        return next((v[0] for v in self.imgs.values() if v), None)


# ---- Plan: script + voice + pictures + clips  ->  one timeline that the renderer draws ---------------------------------
DOCK_GAP, BEAT_GAP, ANSWER_GAP, HOLD, MIN_HOOK = 0.5, 0.16, 0.62, 1.0, 1.95
MAX_SHOT = 2.9


def chunk_idx(tokens):
    """Caption chunks of 1-3 words (about 14 letters), broken after punctuation, no one-word orphans."""
    out, cur = [], []
    for k, (w, _) in enumerate(tokens):
        cur.append(k)
        letters = sum(len(tokens[j][0]) for j in cur) + len(cur) - 1
        nxt = len(tokens[k + 1][0]) if k + 1 < len(tokens) else 99
        if len(cur) >= 3 or letters + 1 + nxt > 14 or re.search(r"[.,!?;:]$", w):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    merged = []
    for c in out:
        if (merged and len(c) == 1 and len(tokens[c[0]][0]) <= 3 and len(merged[-1]) < 3
                and not re.search(r"[.!?]$", tokens[merged[-1][-1]][0])
                and sum(len(tokens[j][0]) + 1 for j in merged[-1] + c) <= 15):
            merged[-1] += c
        else:
            merged.append(c)
    return merged


def auto_flags(tokens):
    """Numbers are always shown red in captions."""
    return [(w, f or ("r" if re.search(r"\d", w) or w.lower().strip(".,!?") in NUMW else "")) for w, f in tokens]


def collect_images(s, topic):
    """{page title: [downloaded pictures]} for the subjects and for every page the lines point at."""
    out, arts = {}, topic["articles"]
    for k, a in enumerate(arts):
        names = topic["images"][k] if k < len(topic.get("images", [])) else None
        out[a] = fetch_images(a, names, f"m{k}_", 3 if len(arts) == 1 else 2)
    have = {norm(a) for a in out}
    extra = [b["show"] for b in s["beats"] if b["show"]] + [s["answer"]["show"]] + s.get("related", [])
    for t in dict.fromkeys(str(x) for x in extra if x):
        if norm(t) in have or len(out) >= 9:
            continue
        have.add(norm(t))
        out[t] = fetch_images(t, None, f"x{len(out)}_", 2)
    return out


def sticker_for(path, height, tilt=0.0, max_w=900):
    tile = fit_tile(make_sticker(Image.open(path), height), max_w, 620)
    return tile.rotate(tilt, resample=Image.BICUBIC, expand=True) if tilt else tile


def build_plan(s, qtype, topic, voice, pics, lib, case_no):
    """(plan for the Renderer, [(start, samples)] voice parts, [(t, sfx name, gain)])."""
    qt = QTYPES[qtype]
    lines = voice
    # ---- when every line is spoken
    hook = lines[0]
    hook["dur"] = len(hook["audio"]) / SR
    hook["t0"], hook["t1"] = 0.0, hook["dur"]
    prev = hook["t1"]
    for i, L in enumerate(lines[1:], 1):
        gap = (max(DOCK_GAP, MIN_HOOK + 0.42 - hook["t1"]) if i == 1 else
               ANSWER_GAP if L["kind"] == "answer" else BEAT_GAP)
        L["dur"] = len(L["audio"]) / SR
        L["t0"] = prev + gap
        L["t1"] = L["t0"] + L["dur"]
        prev = L["t1"]
    beats, ans = lines[1:-1], lines[-1]
    total = ans["t1"] + HOLD
    reveal_t = ans["t0"] - 0.10
    dock0 = hook["t1"] + 0.04
    dock1 = dock0 + 0.38

    # ---- word times
    qtok = parse_marked(s["question"])
    qtimes = align(qtok, hook["words"], hook["dur"])
    q_t = [hook["t0"] + a for a, _ in qtimes]
    caps = []
    for L in lines[1:]:
        toks_ = auto_flags(parse_marked(L["marked"]))
        times = align(toks_, L["words"], L["dur"])
        idx = chunk_idx(toks_)
        for ci, ch in enumerate(idx):
            t0 = L["t0"] + times[ch[0]][0]
            t1 = L["t0"] + times[idx[ci + 1][0]][0] if ci + 1 < len(idx) else L["t1"] + (0.3 if L["kind"] == "answer" else 0.12)
            caps.append({"t0": t0, "t1": t1, "words": [(toks_[k][0], toks_[k][1], L["t0"] + times[k][0], L["t0"] + times[k][1])
                                                    for k in ch]})
        L["times"] = [(L["t0"] + a, L["t0"] + b) for a, b in times]
        L["toks"] = toks_

    # ---- pictures: heroes in the hook, stickers / graphics on the beats
    arts = topic["articles"]
    heroes = []
    for k, a in enumerate(arts[:2]):
        p = pics.get(a, advance=False)
        if p:
            heroes.append(sticker_for(p, 520, max_w=900) if len(arts) == 1 else sticker_for(p, 380, max_w=390))
    if not heroes:
        p = pics.any()
        heroes = [sticker_for(p, 520)] if p else [mystery_block()]
    visuals, last_pic, k_side = [], None, 0
    bounds = [b["t0"] - 0.06 for b in beats] + [reveal_t]
    fx, sfx = [], []
    for i, (b, L) in enumerate(zip(s["beats"], beats)):
        t0, t1 = bounds[i], bounds[i + 1]
        viz = b.get("viz")
        if viz:
            delay = 0.0
            for (w, _), (a, _) in zip(L["toks"], L["times"]):
                if re.search(r"\d", w) or w.lower().strip(".,!?") in NUMW:
                    delay = max(0.0, min(1.0, a - t0 - 0.12))
                    break
            if delay >= 0.3 and b["show"] and pics.has(b["show"]):          # the picture waits for the graphic
                k_side += 1
                visuals.append({"t0": t0, "t1": t0 + delay, "kind": "sticker", "dx": (-26, 26)[k_side % 2],
                                "tile": sticker_for(pics.get(b["show"]), 500, tilt=(-4, 4)[k_side % 2])})
                sfx.append((t0 + 0.02, "pop", 0.45))
            visuals.append({"t0": t0, "t1": t1, "kind": "viz", "spec": viz, "delay": delay})
            if viz["type"] == "number":
                fx.append({"t": t0 + delay, "kind": "shake", "amp": 9})
                sfx.append((t0 + delay, "slam", 0.8))
            else:
                sfx.append((t0 + delay, "pop", 0.55))
            last_pic = None
        elif b["show"] and pics.has(b["show"]) and norm(b["show"]) != last_pic:
            p = pics.get(b["show"])
            k_side += 1
            tile = sticker_for(p, 500, tilt=(-4, 4)[k_side % 2])
            visuals.append({"t0": t0, "t1": t1, "kind": "sticker", "tile": tile, "dx": (-26, 26)[k_side % 2]})
            sfx.append((t0 + 0.02, "pop", 0.5))
            last_pic = norm(b["show"])
        else:
            last_pic = None
        if i == len(beats) - 1 and not (visuals and visuals[-1]["t0"] == t0):
            visuals.append({"t0": t0, "t1": t1, "kind": "mystery", "tile": mystery_block(420)})
            sfx.append((t0 + 0.02, "pop", 0.5))

    for a, nxt in zip(visuals, visuals[1:]):                  # a picture stays until the next graphic really appears
        end = nxt["t0"] + nxt.get("delay", 0.0)
        if 0 < end - a["t1"] <= 1.2:
            a["t1"] = end

    # ---- background shots: hook shot, one per beat (long beats are cut again), the answer shot
    cuts = [0.0] + bounds + [total]
    scenes = [s["beats"][0]["scene"]] + [b["scene"] for b in s["beats"]] + [s["answer"]["scene"]]
    shots, pan = [], 1
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        n = 1 if b - a <= MAX_SHOT + 0.2 else math.ceil((b - a) / 2.5)
        for part in range(n):
            t0, t1 = a + (b - a) * part / n, a + (b - a) * (part + 1) / n
            clip, start = lib.pick(scenes[i], t1 - t0 + 0.3, hero=(i == 0))
            if clip is None:
                raise SystemExit("No gameplay clips available")
            shots.append({"t0": t0, "t1": t1, "clip": clip, "start": start, "pan": pan,
                          "punch": 0.30 if i == 0 else 0.20 if i == len(cuts) - 2 else 0.13})
            pan = -pan
            if t0 > 0:
                fx.append({"t": t0, "kind": "flash", "amp": 0.30 if part == 0 else 0.20})
                if abs(t0 - dock0) > 0.3:
                    sfx.append((t0 - 0.04, "whoosh", 0.35))

    # ---- the reveal
    fx += [{"t": reveal_t, "kind": "flash", "amp": 0.85}, {"t": reveal_t, "kind": "shake", "amp": 18},
           {"t": reveal_t + 0.5, "kind": "shake", "amp": 10}]
    sfx += [(0.02, "boom", 0.95), (dock0, "whoosh", 0.5), (reveal_t - 0.62, "riser", 0.7), (reveal_t, "boom", 1.0),
            (reveal_t + 0.03, "chime", 0.55), (reveal_t + 0.5, "thud", 0.8)]
    sfx += [(t, "tick", 0.35) for i, t in enumerate(q_t) if i > 0 and t < dock0]
    sfx += [(reveal_t + 0.55 + 0.075 * k, "ping", 0.35, {"f": 1000 + 150 * k}) for k in range(8)]
    plan = {"total": total, "accent": qt["accent"], "label": qt["label"], "case_no": case_no,
            "q_words": qtok, "q_t": q_t, "dock0": dock0, "dock1": dock1, "heroes": heroes, "caps": caps,
            "visuals": visuals, "shots": shots, "fx": fx,
            "reveal": {"t": reveal_t, "short": s["answer"]["short"]}, "cliff": (bounds[-2], reveal_t)}
    return plan, [(L["t0"], L["audio"]) for L in lines], sfx


def make_video(s, qtype, topic, case_no):
    """Voice, pictures, clips, plan, audio mix, frames -> work/final.mp4 (returns True if wiki pictures were used)."""
    spoken = spoken_lines(s)
    takes = tts_all([(k, plain(m)) for k, m in spoken])
    voice = []
    for (kind, marked), (p, words) in zip(spoken, takes):
        y, w = voice_line(p, words)
        voice.append({"kind": kind, "text": plain(marked), "marked": marked, "audio": y, "words": w})
    print("voice:", " | ".join(f"{len(v['audio']) / SR:.1f}s" for v in voice))
    imgs = collect_images(s, topic)
    pics = Pics(imgs)
    files = list_clips()
    if not files:
        fb = get_fallback_clip()
        if not fb:
            raise SystemExit("No gameplay clips and no fallback clip")
        files = [fb]
    lib = ClipLib(files)
    if TAG_PER_RUN > 0 and len(files) > 1:
        tag_new_clips(lib, TAG_PER_RUN)
    print("clips:", len(files), "| pictured pages:", sum(bool(v) for v in imgs.values()))
    plan, parts, sfx = build_plan(s, qtype, topic, voice, pics, lib, case_no)
    print(f"timeline: {plan['total']:.1f}s | shots: {len(plan['shots'])} | reveal at {plan['reveal']['t']:.1f}s")
    wav = mix_audio(parts, sfx, plan["total"], W / "final.wav")
    encode(plan, wav, W / "final.mp4")
    return any(imgs.values())


# ---- YouTube: upload, localization, playlists, comment ---------------------------------------------------------------
def youtube():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=Credentials(
        None, refresh_token=os.environ["YT_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"]))


def translate(title, question, story):
    """Localized title/description so YouTube shows them in the viewer's language."""
    if not LANGS:
        return {}
    try:
        tr = gemini(
            f"Translate this YouTube Short metadata about Minecraft into these languages: {', '.join(LANGS)} "
            "(ISO 639-1 codes). Natural, catchy wording for gamers in each language, keep Minecraft names as the "
            "official localized game names and numbers exact, title max 80 characters, no hashtags.\n"
            f"TITLE: {title}\nQUESTION: {question}\nSTORY:\n{story}\n"
            'Return JSON: {"<code>": {"title": "...", "question": "...", "story": "..."}, ...}',
            lambda d: isinstance(d, dict) and sum(isinstance(d.get(l), dict) and bool(d[l].get("title"))
                                                  for l in LANGS) >= len(LANGS) // 2, rounds=2)
    except SystemExit:
        print("translation skipped")
        return {}
    return {l: v for l, v in tr.items() if l in LANGS and isinstance(v, dict) and v.get("title")}


def video_title(s):
    q = plain(s["question"])
    return (q if "minecraft" in q.lower() else f"{q} | Minecraft")[:85]


def upload(yt, s, qtype, topic, has_images):
    from googleapiclient.http import MediaFileUpload
    qt = QTYPES[qtype]
    title, q = video_title(s), plain(s["question"])
    a = s["answer"]
    story = f"Answer: {plain(a['t'])}"
    tags = list(dict.fromkeys(["Minecraft", "Shorts"] + qt["tags"] + [str(t) for t in s.get("tags", [])]))
    sources = "; ".join(f'"{x}"' for x in topic["articles"])
    tail = ("\n\n#Shorts #Minecraft " + " ".join("#" + t.replace(" ", "") for t in tags[2:7])
            + (f"\n\nFacts: Minecraft Wiki (minecraft.wiki), {sources}." if topic.get("text") else "")
            + ("\nGame images: Minecraft Wiki, Minecraft © Mojang." if has_images else "")
            + "\n\n" + DISCLAIMER)
    ask = f"{s['comment_q']} Tell me in the comments!\n\n" if s.get("comment_q") else ""
    body = {"snippet": {"title": f"{title} #Shorts", "tags": tags[:15], "categoryId": "20",
                        "description": (f"{q}\n{story}\n\n{ask}".rstrip() + tail)[:4900],
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": env("PRIVACY", "public"), "selfDeclaredMadeForKids": False}}
    loc = {l: {"title": f"{plain(t['title'])[:85]} #Shorts",
               "description": ((t.get("question", "") + "\n" + t.get("story", "") + "\n\n").strip() + tail)[:4900]}
           for l, t in translate(title, q, story).items()}
    if loc:
        body["localizations"] = loc
        print("localized:", ", ".join(loc))
    send = lambda part: yt.videos().insert(part=part, body=body, media_body=MediaFileUpload(
        str(W / "final.mp4"), chunksize=-1, resumable=True)).execute()["id"]
    try:
        vid = send("snippet,status" + (",localizations" if loc else ""))
    except Exception as e:
        if not loc or "uploadLimitExceeded" in str(e) or "quota" in str(e).lower():
            raise
        print("localizations rejected, uploading without them:", str(e)[:200])
        body.pop("localizations")
        vid = send("snippet,status")
    print("uploaded:", vid)
    return vid


def add_to_playlists(yt, vid, qtype):
    try:
        items = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
        for name in (SERIES, QTYPES[qtype]["playlist"]):
            pl = next((i["id"] for i in items if i["snippet"]["title"] == name), None) or \
                yt.playlists().insert(part="snippet,status", body={
                    "snippet": {"title": name, "description": f"{name}: one question, one answer, Minecraft Shorts."},
                    "status": {"privacyStatus": "public"}}).execute()["id"]
            yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
            print("playlist:", name)
    except Exception as e:
        print("playlist skipped (re-run auth.py with new scopes):", str(e)[:200])


def post_comment(yt, vid, s):
    q = s.get("comment_q", "")
    try:
        if q:
            yt.commentThreads().insert(part="snippet", body={"snippet": {"videoId": vid, "topLevelComment": {
                "snippet": {"textOriginal": f"{q} 👇"}}}}).execute()
            print("comment posted")
    except Exception as e:
        print("comment skipped (re-run auth.py with new scopes):", str(e)[:200])


# ---- Learning: the bot looks at its own results and leans toward the question types that get views ------------------
def performance(yt):
    """[(entry, views)] for our own videos older than 24 h (newest 120)."""
    now = time.time()
    rows = [e for e in used[-120:] if e.get("vid") and now - e.get("ts", now) > 86400]
    views = {}
    try:
        for i in range(0, len(rows), 50):
            r = yt.videos().list(part="statistics", id=",".join(e["vid"] for e in rows[i:i + 50])).execute()
            for it in r.get("items", []):
                views[it["id"]] = int(it.get("statistics", {}).get("viewCount", 0))
    except Exception as e:
        print("view counts unavailable, rotating question types:", str(e)[:150])
        return []
    return [(e, views[e["vid"]]) for e in rows if e["vid"] in views]


def bandit(options, field, rows, floor=3):
    """Every option gets `floor` tries first (least tried goes next); after that options are drawn by their
    average log-views (shrunk toward the global average), and every option keeps a small chance."""
    trials = {o: sum(1 for e in used if e.get(field) == o) for o in options}
    low = [o for o in options if trials[o] < floor]
    if low:
        m = min(trials[o] for o in low)
        return random.choice([o for o in low if trials[o] == m])
    score = {o: [math.log1p(v) for e, v in rows if e.get(field) == o] for o in options}
    allv = [x for s in score.values() for x in s]
    if not allv:
        return random.choice(options)
    g = sum(allv) / len(allv)
    mean = {o: (sum(score[o]) + 2 * g) / (len(score[o]) + 2) for o in options}
    top = max(mean.values())
    return random.choices(options, [math.exp((mean[o] - top) / 0.5) + 0.1 for o in options])[0]


def report(rows):
    by = {}
    for e, v in rows:
        if e.get("qtype"):
            by.setdefault(e["qtype"], []).append(v)
    if by:
        print("question types:", " | ".join(f"{k} {sum(v) // len(v)} avg views (n={len(v)})"
                                            for k, v in sorted(by.items(), key=lambda x: -sum(x[1]) / len(x[1]))))


def main():
    ensure_fonts()
    yt = None if DRY_RUN else youtube()
    rows = performance(yt) if (LEARN and yt) else []
    report(rows)
    forced = env("QTYPE")
    qtype = forced if forced in QTYPES else (bandit(list(QTYPES), "qtype", rows) if LEARN else list(QTYPES)[len(used) % len(QTYPES)])
    s = topic = None
    for attempt in range(3):                               # a subject the model cannot ground is replaced by another
        try:
            topic = pick_topic(qtype)
        except LookupError:
            print("no fresh update found, switching question type")
            qtype = random.choice([k for k in QTYPES if k != "new"])
            topic = pick_topic(qtype)
        try:
            s = clean_viz(gen_script(topic, qtype))
            break
        except ScriptError as e:
            print(f"no valid script for {topic['key']} ({e}); trying another subject")
            AVOID.update(topic["articles"])
            if qtype == "new":
                qtype = random.choice([k for k in QTYPES if k != "new"])
    if s is None:
        raise SystemExit("No valid script after 3 subjects")
    print("type:", qtype, "| topic:", topic["key"], "| wiki:", WIKI_OK)
    print(json.dumps(s, ensure_ascii=False))
    has_images = make_video(s, qtype, topic, len(used) + 1)
    if DRY_RUN:
        print("DRY_RUN: video is", W / "final.mp4")
        return
    vid = upload(yt, s, qtype, topic, has_images)
    used.append({"key": topic["key"], "arts": topic["articles"], "qtype": qtype, "q": plain(s["question"]),
                 "vid": vid, "ts": int(time.time())})
    save_state()
    add_to_playlists(yt, vid, qtype)
    post_comment(yt, vid, s)


if __name__ == "__main__":
    main()
