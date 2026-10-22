#!/usr/bin/env python3
"""Terra Incognita Shorts bot v5 — INSTANT format.

Radical change:
- 12–18 second videos
- Almost no continuous voice (1–3 punchy lines)
- Kinetic text that zooms, shakes, flies in
- Rapid clip cuts (0.7–1.3 s)
- No progress bar, no lock, no case-file look
- Modern CapCut / viral TikTok style

Same tools: Gemini, edge-tts, Minecraft Wiki, clips/, music/, PIL, ffmpeg, YouTube API.
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

VOICE = env("VOICE", "en-US-GuyNeural")
RATE = int(env("RATE", "18"))
MUSIC_VOL = float(env("MUSIC_VOL", "0.18"))
SFX = env("SFX", "1") == "1"
SFX_VOL = float(env("SFX_VOL", "1.15"))
LANGS = [l.strip() for l in env("LANGS", "es,pt,de,fr,it,pl,uk,tr").split(",") if l.strip()]
LEARN = env("LEARN", "1") == "1"
IMAGES = env("IMAGES", "1") == "1"
COOLDOWN = int(env("COOLDOWN", "35"))
TAG_PER_RUN = int(env("TAG_PER_RUN", "10"))
DRY_RUN = env("DRY_RUN", "0") == "1"
FPS = 30
UA = {"User-Agent": "TerraIncognitaShortsBot/5.0"}
MCWIKI = "https://minecraft.wiki/api.php"
DISCLAIMER = "NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT."
MUSIC_DIR = pathlib.Path("music")
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
SKIP_IMG = re.compile(r"sprite|invicon|icon|logo|grid|slot|button|achievement|advancement|recipe|crafting|"
                      r"atlas|gui|hud|pattern|banner|edition|screenshot of|\.gif$|\.svg$", re.I)


def load_state():
    try:
        raw = json.loads(USED.read_text(encoding="utf-8")) if USED.exists() else {}
    except ValueError:
        raw = {}
    if isinstance(raw, list):
        raw = {"videos": raw}
    vids = [e if isinstance(e, dict) else {"key": str(e)} for e in (raw.get("videos") if isinstance(raw.get("videos"), list) else [])]
    clips = raw.get("clips") if isinstance(raw.get("clips"), dict) else {}
    return {"v": 5, "videos": vids, "clips": clips}

STATE = load_state()
used = STATE["videos"]


def save_state():
    d = pathlib.Path("clips")
    STATE["clips"] = {k: v for k, v in STATE["clips"].items() if (d / k).exists()}
    def block(items):
        return "[\n" + ",\n".join(" " + json.dumps(i, ensure_ascii=False) for i in items) + "\n]"
    clips = "{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}"
                               for k, v in sorted(STATE["clips"].items())) + "\n}"
    USED.write_text('{\n"v": 5,\n"videos": ' + block(used) + ',\n"clips": ' + clips + "\n}\n", encoding="utf-8")


def titles_of(e):
    return e.get("arts") or [str(e.get("key", ""))]

AVOID = set()


def recent_titles(n):
    return {t for e in used[-n:] for t in titles_of(e)} | AVOID


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


FONT_URLS = {
    "anton": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "pixel": "https://raw.githubusercontent.com/google/fonts/main/ofl/pressstart2p/PressStart2P-Regular.ttf"}
FONT_PATHS = {}
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def ensure_fonts():
    own = pathlib.Path("font.ttf")
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


CW, CH = 1080, 1920
GREEN, GOLD, RED = (88, 209, 59), (255, 205, 30), (255, 69, 58)
CYAN, PURPLE, ORANGE = (51, 224, 224), (181, 101, 255), (255, 138, 31)
WHITE = (255, 255, 255)


def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def ease_out_back(t, s=1.7):
    t = clamp(t) - 1
    return t * t * ((s + 1) * t + s) + 1


def ease_out_cubic(t):
    return 1 - (1 - clamp(t)) ** 3


_FONTS = {}
def font(kind, size):
    key = (kind, int(size))
    if key not in _FONTS:
        path = FONT_PATHS.get(kind) or FONT_PATHS["anton"]
        try:
            _FONTS[key] = ImageFont.truetype(str(path), int(size))
        except Exception:
            _FONTS[key] = ImageFont.load_default()
    return _FONTS[key]


_TILES = {}
def text_tile(txt, size, fill=WHITE, kind="anton", stroke=None, shadow=True):
    key = (txt, int(size), fill, kind, stroke, shadow)
    if key in _TILES:
        return _TILES[key]
    f = font(kind, size)
    sw = (round(size * 0.09) if kind == "anton" else max(2, round(size * 0.13))) if stroke is None else stroke
    pad = sw + round(size * 0.08) + 4
    l, t, r, b = f.getbbox(txt, stroke_width=sw)
    _, rt, _, rb = f.getbbox("Hg", stroke_width=sw)
    t, b = min(t, rt), max(b, rb)
    im = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shadow:
        off = max(2, round(size * 0.05))
        d.text((pad - l + off, pad - t + off), txt, font=f, fill=(0, 0, 0, 160), stroke_width=sw, stroke_fill=(0, 0, 0, 160))
    d.text((pad - l, pad - t), txt, font=f, fill=fill + (255,), stroke_width=sw, stroke_fill=(8, 8, 10, 255))
    if len(_TILES) > 300:
        _TILES.clear()
    _TILES[key] = im
    return im


def text_w(txt, size, kind="anton"):
    return font(kind, size).getlength(txt)


def split_lines(words, max_w, sizes, max_lines=4, max_words=3):
    """Fit words into lines: max `max_words` per line, never wider than max_w."""
    n = len(words)
    if n == 0:
        return sizes[-1], []
    for size in sizes:
        sp = size * 0.24
        ws = [text_w(w, size) for w in words]
        if max(ws) > max_w:
            continue
        lines, cur, cur_w = [], [], 0
        for i, w in enumerate(ws):
            need = w + (sp if cur else 0)
            # hard limit: never more than max_words on one line
            if cur and (len(cur) >= max_words or cur_w + need > max_w):
                lines.append(cur)
                cur, cur_w = [], 0
                need = w
            cur.append(i)
            cur_w += need
        if cur:
            lines.append(cur)
        if len(lines) <= max_lines:
            return size, lines
    # last resort: smaller size + groups of max_words
    size = sizes[-1]
    longest = max(text_w(w, size) for w in words) if words else 0
    if longest > max_w and longest > 0:
        size = max(40, int(size * max_w / longest))
    lines = [list(range(i, min(i + max_words, n))) for i in range(0, n, max_words)]
    return size, lines


# Safe margins so text never gets cropped on 9:16 (1080x1920)
SAFE_X = 36
SAFE_Y = 48


def blit(base, tile, cx, cy, scale=1.0, angle=0.0, alpha=1.0):
    """Paste tile centered at (cx, cy), clamped so it stays inside the safe 9:16 frame."""
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
    # Clamp so the whole tile stays inside the frame with safe margins
    half_w, half_h = t.width / 2, t.height / 2
    cx = max(SAFE_X + half_w, min(CW - SAFE_X - half_w, cx))
    cy = max(SAFE_Y + half_h, min(CH - SAFE_Y - half_h, cy))
    # If tile is still wider/taller than safe area, shrink it
    max_w = CW - 2 * SAFE_X
    max_h = CH - 2 * SAFE_Y
    if t.width > max_w or t.height > max_h:
        s = min(max_w / t.width, max_h / t.height)
        t = t.resize((max(1, int(t.width * s)), max(1, int(t.height * s))), Image.BILINEAR)
        half_w, half_h = t.width / 2, t.height / 2
        cx = max(SAFE_X + half_w, min(CW - SAFE_X - half_w, cx))
        cy = max(SAFE_Y + half_h, min(CH - SAFE_Y - half_h, cy))
    base.paste(t, (int(cx - half_w), int(cy - half_h)), t)


def make_sticker(img, height, outline=14):
    im = img.convert("RGBA")
    a = np.asarray(im.getchannel("A"))
    cut = (a < 245).mean() > 0.04
    if cut:
        bb = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        if bb:
            im = im.crop(bb)
    sc = height / max(1, im.height)
    w = max(1, int(im.width * sc))
    if not cut:
        w = min(w, 780)
        sc = w / max(1, im.width)
        height = int(im.height * sc)
    im = im.resize((w, max(1, int(height))), Image.LANCZOS)
    pad = outline + 20
    canvas = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    if cut:
        canvas.alpha_composite(im, (pad, pad))
        alpha = canvas.getchannel("A")
        grown = alpha.filter(ImageFilter.GaussianBlur(outline * 0.5)).point(lambda v: 255 if v > 18 else 0)
        edge = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
        edge.putalpha(grown)
        body = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        body.alpha_composite(edge)
        body.alpha_composite(canvas)
        shape = grown
    else:
        card = Image.new("RGBA", (im.width + 2 * outline, im.height + 2 * outline), (255, 255, 255, 255))
        card.alpha_composite(im, (outline, outline))
        m = Image.new("L", card.size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, card.width - 1, card.height - 1], radius=20, fill=255)
        card.putalpha(m)
        body = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        body.alpha_composite(card, ((canvas.width - card.width) // 2, (canvas.height - card.height) // 2))
        shape = body.getchannel("A")
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.putalpha(shape.filter(ImageFilter.GaussianBlur(11)).point(lambda v: int(v * 0.5)))
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 10))
    out.alpha_composite(body)
    return out


def fit_tile(tile, max_w, max_h):
    s = min(1.0, max_w / tile.width, max_h / tile.height)
    return tile if s >= 0.999 else tile.resize((max(1, int(tile.width * s)), max(1, int(tile.height * s))), Image.LANCZOS)


def shade_layer():
    y = np.arange(CH, dtype=np.float32)[:, None]
    x = np.arange(CW, dtype=np.float32)[None, :]
    top = np.clip(1 - y / 450, 0, 1) ** 1.3 * 0.42
    bot = np.clip((y - 1250) / 420, 0, 1) ** 1.2 * 0.5
    vx, vy = (x - CW / 2) / (CW / 2), (y - CH / 2) / (CH / 2)
    vig = np.clip(vx * vx * 0.4 + vy * vy * 0.3 - 0.22, 0, 1) * 0.45
    a = 1 - (1 - 0.08) * (1 - top) * (1 - bot) * (1 - vig)
    arr = np.zeros((CH, CW, 4), np.uint8)
    arr[..., 3] = np.clip(a * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


ASP = CW / CH
GRADE = "eq=contrast=1.15:saturation=1.35:gamma=0.95,unsharp=5:5:1.1:5:5:0.0"


def shot_region(sw, sh, margin=1.25):
    if sw <= 0 or sh <= 0:
        return None
    if sw / sh >= ASP:
        rh, rw = sh, min(sw, int(sh * ASP * margin))
    else:
        rw, rh = sw, min(sh, int(sw / ASP * margin))
    rw, rh = rw - rw % 2, rh - rh % 2
    return (sw - rw) // 2, (sh - rh) // 2, rw, rh


class ShotReader:
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
             "-t", f"{dur + 0.2:.3f}", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self.rw * self.rh * 3 * 2)
        self.size = self.rw * self.rh * 3

    def read(self):
        if self.proc:
            buf = self.proc.stdout.read(self.size)
            if len(buf) == self.size:
                self.last = Image.frombytes("RGB", (self.rw, self.rh), buf)
        if self.last is None:
            self.last = Image.new("RGB", (self.rw, self.rh), (30, 35, 42))
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
                self.proc.wait(timeout=3)
            except Exception:
                pass
            self.proc = None


def kinetic_state(t, t0, t1, style="slam"):
    """Return (scale, alpha, angle, dx, dy). Scales are capped so text never overflows 9:16."""
    u = t - t0
    life = t1 - t0
    if u < 0 or u > life + 0.12:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    if style == "slam":
        if u < 0.16:
            # mild overshoot only (max ~1.08)
            s = 0.35 + 0.65 * ease_out_back(u / 0.16, 1.35)
            a = clamp(u / 0.06)
            ang = (1 - ease_out_cubic(u / 0.16)) * 5
            return min(s, 1.12), a, ang, 0, 0
        if u > life - 0.1:
            return 1.0, clamp((life - u) / 0.1), 0, 0, 0
        return 1.0 + 0.02 * math.sin(u * 5.5), 1.0, 0, 0, 2 * math.sin(u * 3)
    if style == "zoom":
        if u < 0.18:
            # start slightly large, settle to 1.0 — max scale 1.22
            s = 1.22 - 0.22 * ease_out_cubic(u / 0.18)
            a = clamp(u / 0.05)
            return s, a, 0, 0, 0
        if u > life - 0.08:
            return 1.0, clamp((life - u) / 0.08), 0, 0, 0
        return 1.0, 1.0, 0, 0, 0
    if style == "slide":
        if u < 0.14:
            s = 0.92 + 0.08 * ease_out_cubic(u / 0.14)
            a = clamp(u / 0.05)
            # small slide so text stays on screen after clamp
            return s, a, 0, (1 - ease_out_cubic(u / 0.14)) * 70, 0
        if u > life - 0.08:
            return 1.0, clamp((life - u) / 0.08), 0, 0, 0
        return 1.0, 1.0, 0, 0, 0
    return 1.0, 1.0, 0, 0, 0


class Renderer:
    def __init__(self, plan):
        self.p = plan
        self.shade = shade_layer()
        self.white = Image.new("RGB", (CW, CH), (255, 255, 255))
        self.reader, self.si = None, -1

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
            if e["kind"] == "shake" and u < 0.35:
                a = e["amp"] * math.exp(-u / 0.08)
                sx += a * math.sin(u * 80)
                sy += a * math.cos(u * 66)
            elif e["kind"] == "flash" and u < 0.2:
                fl = max(fl, e["amp"] * (1 - u / 0.2) ** 2)
        return sx, sy, fl

    def background(self, t, sx, sy):
        s = self.shot_at(t)
        u, dur = t - s["t0"], max(0.08, s["t1"] - s["t0"])
        z = (1 + 0.14 * u / dur) * (1 + s.get("punch", 0.25) * (1 - min(1.0, u / 0.16)) ** 2)
        return self.reader.frame(z, s.get("pan", 0.0) * (2 * u / dur - 1), sx * 0.85, sy * 0.85)

    def draw_texts(self, base, t, sx, sy):
        for item in self.p["texts"]:
            if not (item["t0"] <= t < item["t1"] + 0.1):
                continue
            sc, al, ang, dx, dy = kinetic_state(t, item["t0"], item["t1"], item.get("style", "slam"))
            if al < 0.02:
                continue
            tile = text_tile(item["txt"], item["size"], item["color"])
            blit(base, tile, item["x"] + dx + sx * 0.35, item["y"] + dy + sy * 0.35, sc, ang, al)

    def draw_stickers(self, base, t, sx, sy):
        for st in self.p.get("stickers", []):
            if not (st["t0"] <= t < st["t1"]):
                continue
            u = t - st["t0"]
            left = st["t1"] - t
            out = 1.0 - 0.35 * clamp(1 - left / 0.09)
            s = (0.35 + 0.65 * ease_out_back(u / 0.2)) * out
            ang = 5 * math.sin(u * 3.8) if st.get("wiggle") else 0
            blit(base, st["tile"], st["x"] + sx, st["y"] + sy + 5 * math.sin(u * 2.2), s, ang)

    def frame(self, t):
        sx, sy, fl = self.fx_state(t)
        base = self.background(t, sx, sy)
        base.paste(self.shade, (0, 0), self.shade)
        self.draw_stickers(base, t, sx, sy)
        self.draw_texts(base, t, sx, sy)
        if fl > 0.02:
            base = Image.blend(base, self.white, min(1.0, fl))
        return base

    def close(self):
        if self.reader:
            self.reader.close()
            self.reader = None


def encode(plan, audio, out, progress=True):
    r = Renderer(plan)
    total = plan["total"]
    n = int(round(total * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{CW}x{CH}",
           "-r", str(FPS), "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac",
           "-b:a", "192k", "-ar", "44100", "-ac", "2", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for i in range(n):
            enc.stdin.write(r.frame(i / FPS).tobytes())
            if progress and i % 100 == 99:
                print(f"  frames {i+1}/{n} ({time.time()-t0:.0f}s)")
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("ffmpeg encode failed")
    finally:
        r.close()
        if enc.poll() is None:
            enc.kill()
    return out


# Gemini
GEM = "https://generativelanguage.googleapis.com/v1beta"
GEM_FALLBACK = ["gemini-2.0-flash", "gemini-flash-latest", "gemini-1.5-flash"]
_MODELS, _COOL = None, {}
_SKIP_MODEL = re.compile(r"image|tts|audio|live|embed|thinking|robotics|computer|vision|learnlm|gemma|exp|aqa|imagen|veo|customtools", re.I)


def gemini_models():
    global _MODELS
    if _MODELS is not None:
        return _MODELS
    chosen = [m.strip() for m in env("GEMINI_MODEL", "").split(",") if m.strip()]
    found = []
    try:
        r = requests.get(f"{GEM}/models", params={"key": os.environ["GEMINI_API_KEY"], "pageSize": 200}, timeout=25)
        r.raise_for_status()
        for m in r.json().get("models", []):
            name = m.get("name", "").split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []) or _SKIP_MODEL.search(name):
                continue
            mt = re.match(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?", name)
            if mt:
                score = float(mt.group(1)) * 10 + (0 if mt.group(2) else 4)
                found.append((score, name))
    except Exception as e:
        print("gemini models list fail:", type(e).__name__)
    ranked = [n for _, n in sorted(found, reverse=True)]
    _MODELS = list(dict.fromkeys(chosen + ranked[:3] + GEM_FALLBACK))
    print("gemini models:", ", ".join(_MODELS[:4]))
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


def gemini(prompt, ok, rounds=4, temperature=None):
    key = os.environ["GEMINI_API_KEY"]
    for _ in range(rounds):
        for model in gemini_models():
            if _COOL.get(model, 0) > time.time():
                continue
            try:
                cfg = {"responseMimeType": "application/json"}
                if temperature is not None:
                    cfg["temperature"] = temperature
                r = requests.post(f"{GEM}/models/{model}:generateContent", params={"key": key}, timeout=(12, 100),
                                  json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg})
                if r.status_code in (429, 500, 502, 503, 504):
                    _COOL[model] = time.time() + (60 if r.status_code == 429 else 18)
                    print(model, r.status_code)
                    continue
                if not r.ok:
                    _COOL[model] = time.time() + 500
                    print(model, r.status_code)
                    continue
                cand = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in cand if not p.get("thought"))
                data = _json_from(text)
                if ok(data):
                    return data
                print(model, "bad JSON")
            except Exception as e:
                print(model, type(e).__name__)
        time.sleep(8)
    raise SystemExit("Gemini unavailable")


QTYPES = {
    "trick": dict(label="TRICK", accent=CYAN, playlist="Minecraft Instant Tricks",
                  ask="one extremely fast or surprising mechanic / build / interaction that feels broken",
                  tags=["Minecraft trick", "Minecraft hack", "Minecraft secret"]),
    "op": dict(label="OP", accent=GOLD, playlist="Minecraft OP Moments",
               ask="something that gives a massive advantage or looks overpowered in seconds",
               tags=["Minecraft OP", "Minecraft broken", "Minecraft farm"]),
    "secret": dict(label="SECRET", accent=PURPLE, playlist="Minecraft Secrets",
                   ask="a little-known fact or hidden behaviour most players never notice",
                   tags=["Minecraft secret", "Minecraft fact", "Minecraft hidden"]),
    "glitch": dict(label="GLITCH", accent=ORANGE, playlist="Minecraft Glitches",
                   ask="a real unintended behaviour or viral claim that actually works",
                   tags=["Minecraft glitch", "Minecraft myth", "Minecraft bug"]),
}

SERIES = "Terra Incognita Instant"
SCENES = ["plains", "forest", "desert", "snow", "ocean", "mountain", "cave", "nether", "end", "village", "build",
          "farm", "fight", "night", "redstone", "any"]
POPULAR = [
    "Creeper", "Enderman", "Warden", "Piglin", "Ghast", "Villager", "Iron Golem", "Axolotl", "Allay", "Sniffer",
    "Breeze", "Diamond", "Netherite", "Elytra", "Totem of Undying", "Trident", "Mace", "Hopper", "Observer",
    "Piston", "Honey Block", "Slime Block", "Sculk Sensor", "Trial Chamber", "Ancient City", "Beacon",
]


WIKI_OK = True


def wiki_get(**p):
    global WIKI_OK
    if not WIKI_OK:
        return {}
    try:
        r = requests.get(MCWIKI, params={**p, "format": "json", "formatversion": 2, "redirects": 1},
                         headers=UA, timeout=45)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("wiki fail:", type(e).__name__)
        WIKI_OK = False
        return {}


def wiki_page(title, cap=8000):
    d = wiki_get(action="parse", page=title, prop="text|images", disabletoc=1).get("parse")
    if not d:
        return None, None, []
    t = re.sub(r"(?s)<(style|script|sup)[^>]*>.*?</\1>", " ", d.get("text", ""))
    t = re.sub(r"<br\s*/?>|</(p|li|tr|h\d|div)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()
    t = re.split(r"\n\s*(?:References|Navigation|External links|See also|Gallery)\s*(?:\[edit\])?\s*\n", t)[0]
    return d.get("title", title), t[:cap], d.get("images", [])


def wiki_images(title, names=None, limit=3):
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
    pages = wiki_get(action="query", prop="imageinfo", iiprop="url|size", iiurlwidth=1000,
                     titles="|".join("File:" + n for n in names[:25])).get("query", {}).get("pages", [])
    info = {p["title"][5:].replace(" ", "_"): (p.get("imageinfo") or [{}])[0] for p in pages}
    out = []
    for n in names:
        ii = info.get(n.replace(" ", "_"), {})
        if min(ii.get("width", 0), ii.get("height", 0)) >= 130 and (ii.get("thumburl") or ii.get("url")):
            out.append((ii.get("thumburl") or ii.get("url"), n))
        if len(out) >= limit:
            break
    return out


def download(url, name):
    p = W / name
    try:
        r = requests.get(url, headers=UA, timeout=80)
        r.raise_for_status()
        p.write_bytes(r.content)
        png = p.with_name(p.stem + "_ok.png")
        try:
            Image.open(p).convert("RGBA").save(png)
        except Exception:
            t = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p), "-frames:v", "1", str(png)],
                               capture_output=True)
            if t.returncode != 0 or not png.exists():
                raise ValueError("decode")
        p.unlink(missing_ok=True)
        return png.resolve()
    except Exception as e:
        print("skip img:", str(e)[:60])


def fetch_images(title, names, prefix, need):
    imgs, seen = [], set()
    for url, _ in wiki_images(title, names, limit=need + 1):
        key = url.split("/")[-1].lower()
        if key in seen or len(imgs) >= need:
            continue
        seen.add(key)
        p = download(url, f"{prefix}{len(imgs)}.png")
        if p:
            imgs.append(p)
    return imgs


def candidates(limit=8):
    titles = set()
    for cat in random.sample(["Category:Mobs", "Category:Blocks", "Category:Items", "Category:Game mechanics"], 3):
        try:
            got = wiki_get(action="query", list="categorymembers", cmtitle=cat, cmtype="page", cmlimit=300).get("query", {}).get("categorymembers", [])
            titles |= {m["title"] for m in got if m.get("ns") == 0}
        except Exception:
            pass
    seen = recent_titles(250)
    titles = [t for t in titles if t not in seen and not t.startswith(("List of", "Category"))]
    return random.sample(titles, min(limit, len(titles)))


def popular(limit=12):
    seen = recent_titles(COOLDOWN)
    pool = [t for t in POPULAR if t not in seen]
    return random.sample(pool, min(limit, len(pool)))


def pick_topic(qtype):
    qt = QTYPES[qtype]
    heads = [e.get("q") for e in used[-15:] if e.get("q")]
    wiki_c, pop = candidates(25), popular(25)
    for _ in range(4):
        cands = random.sample(pop, min(len(pop), 16)) + random.sample(wiki_c, min(len(wiki_c), 6))
        random.shuffle(cands)
        ask = (f"Pick ONE subject for a 15-second viral Minecraft Short about {qt['ask']}. "
               "Must be something every player instantly recognizes. Angle must feel surprising or broken. ")
        if len(cands) >= 5:
            ask += f"ONLY from: {cands}. "
        if heads:
            ask += f"Avoid: {heads}. "
        ask += 'JSON: {"article": "exact title", "angle": "one sentence crazy moment"}'
        t = gemini(ask, lambda d: isinstance(d, dict) and d.get("article"))
        want = str(t["article"])
        angle = str(t.get("angle") or "")
        if not WIKI_OK:
            return {"articles": [want], "key": want, "text": None, "images": [[]], "angle": angle}
        got, text, names = wiki_page(want)
        if got and text and len(text) > 700:
            return {"articles": [got], "key": got, "text": text, "images": [names], "angle": angle}
        print("reject:", want)
    raise SystemExit("No topic")


def plain(t):
    return re.sub(r"\[/?[ry]\]", "", str(t)).strip()


def parse_marked(text):
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


BANNED = re.compile(r"did you know|in this video|welcome|hey guys|subscribe|like and|let'?s dive|today we|"
                    r"you won'?t believe|stay tuned|don'?t forget", re.I)


def normalize_script(d):
    lines = []
    for L in d.get("lines", []):
        if isinstance(L, str):
            L = {"t": L}
        if isinstance(L, dict) and plain(L.get("t", "")):
            lines.append({"t": str(L["t"]).strip(), "scene": str(L.get("scene") or "any").lower(),
                          "show": str(L.get("show") or "").strip()})
    hook = str(d.get("hook", "")).strip().upper()
    payoff = str(d.get("payoff", "")).strip().upper()
    if not hook.endswith(("!", "?")):
        hook += "!"
    tags = [re.sub(r"[#\s]+", " ", str(t)).strip() for t in d.get("tags", []) if str(t).strip()][:7]
    return {"hook": hook, "lines": lines[:3], "payoff": payoff[:26], "comment_q": plain(d.get("comment_q", "")),
            "tags": tags}


def check_script(s):
    errs = []
    h = plain(s["hook"])
    if not 3 <= len(h.split()) <= 8:
        errs.append(f"hook words {len(h.split())}")
    if BANNED.search(h):
        errs.append("banned filler")
    if not 1 <= len(s["lines"]) <= 3:
        errs.append("need 1-3 lines")
    if not 2 <= len(s["payoff"]) <= 24:
        errs.append("payoff length")
    total = len(h.split()) + sum(len(plain(L["t"]).split()) for L in s["lines"])
    if not 10 <= total <= 28:
        errs.append(f"total words {total}")
    return errs


def script_prompt(topic, qtype, recent, errors):
    qt = QTYPES[qtype]
    subject = topic["articles"][0]
    source = (f"SOURCE:\n<<<\n{topic['text']}\n>>>\n" if topic.get("text") else "Accurate Java Edition facts only.\n")
    fix = ("\nFIX:\n- " + "\n- ".join(errors) + "\n" if errors else "")
    return (
        "Write a 12-16 second viral Minecraft Short. INSTANT format.\n"
        "GOAL: stop the scroll in 1 second.\n"
        "1. HOOK (huge on screen from frame 0): dramatic result statement. "
        "Examples: 'SECRET ROOM IN 6 BLOCKS', 'THIS FARM IS BROKEN', 'SLEEP IN THE NETHER'. 4-7 words + !\n"
        "2. LINES (1-3 spoken, 4-8 words each): key steps or reason. Ultra punchy.\n"
        "3. PAYOFF: 2-4 words huge at end (e.g. 'IT WORKS', '6 BLOCKS').\n"
        "Voice: excited friend. No greetings.\n"
        f'SUBJECT: "{subject}". Angle: {topic.get("angle","")}\nTYPE: {qt["ask"]}\n{source}'
        "Mark most important hook word with [r]...[/r].\n"
        "Each line needs scene (gameplay tag) and optional show (wiki title).\n"
        "Total spoken words: 12-26.\n"
        "comment_q max 7 words. tags 4-6.\n"
        + (f"Avoid recent: {recent}\n" if recent else "")
        + fix
        + 'JSON: {"hook": "SECRET ROOM IN 6 BLOCKS!", "lines": [{"t": "Place two hoppers facing each other", "scene": "build", "show": "Hopper"}], '
          '"payoff": "6 BLOCKS", "comment_q": "Did you try this?", "tags": ["..."]}')


class ScriptError(Exception):
    pass


def gen_script(topic, qtype):
    recent = [e.get("q") for e in used[-15:] if e.get("q")]
    errors, best = [], None
    for attempt in range(3):
        raw = gemini(script_prompt(topic, qtype, recent, errors),
                     lambda d: isinstance(d, dict) and d.get("hook") and isinstance(d.get("lines"), list))
        s = normalize_script(raw)
        errors = check_script(s)
        print(f"script {attempt+1}:", "ok" if not errors else "; ".join(errors)[:180])
        if not errors:
            return s
        if best is None or len(errors) < len(best[1]):
            best = (s, errors)
    if best and len(best[1]) <= 3:
        print("using closest")
        return best[0]
    raise ScriptError("; ".join(best[1])[:140])


# Audio
SR = 44100
PROSODY = {"hook": (10, "+6Hz", "+10%"), "line": (4, "+2Hz", "+4%")}


async def _tts_line(text, path, kind):
    import edge_tts
    rate, pitch, vol = PROSODY.get(kind, PROSODY["line"])
    rate = f"{RATE + int(rate):+d}%"
    kw = dict(rate=rate, pitch=pitch, volume=vol)
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
    async def go():
        out = []
        for i, (kind, text) in enumerate(lines):
            for attempt in range(3):
                try:
                    p = W / f"line{i}.mp3"
                    words = await _tts_line(text, p, kind)
                    if p.stat().st_size < 500:
                        raise RuntimeError("empty")
                    out.append((p, words))
                    break
                except Exception as e:
                    print("tts retry", i, type(e).__name__)
                    await asyncio.sleep(1.2 + attempt)
            else:
                raise SystemExit("TTS failed")
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


def voice_line(path, words, target_rms=0.145):
    x = decode(path)
    if len(x) < SR // 14:
        raise SystemExit("TTS short")
    envv = np.convolve(np.abs(x), np.ones(360, dtype=np.float32) / 360, mode="same")
    thr = max(0.0035, 0.05 * float(envv.max()))
    idx = np.nonzero(envv > thr)[0]
    a = max(0, int(idx[0]) - int(0.018 * SR))
    b = min(len(x), int(idx[-1]) + int(0.04 * SR))
    y = x[a:b].copy()
    rms = float(np.sqrt(np.mean(y * y))) or 1.0
    y = np.clip(y * min(3.6, target_rms / rms), -0.95, 0.95)
    f, g = int(0.003 * SR), int(0.022 * SR)
    y[:f] *= np.linspace(0, 1, f, dtype=np.float32)
    y[-g:] *= np.linspace(1, 0, g, dtype=np.float32)
    shift = a / SR
    return y, [(w, max(0.0, s - shift), d) for w, s, d in words]


# SFX
_rng = np.random.default_rng(9)


def _t(d):
    return np.arange(int(d * SR), dtype=np.float32) / SR


def _lp(x, n):
    return np.convolve(x, np.ones(n, dtype=np.float32) / n, mode="same")


def sfx_boom(d=0.65):
    t = _t(d)
    ph = 2 * np.pi * np.cumsum(42 + 70 * np.exp(-t / 0.09)) / SR
    return (np.sin(ph) * np.exp(-t / (d * 0.22)) + 0.35 * _lp(_rng.standard_normal(len(t)).astype(np.float32), 25) * 3 * np.exp(-t / 0.06)).astype(np.float32)


def sfx_whoosh(d=0.32):
    t = _t(d)
    n = _rng.standard_normal(len(t)).astype(np.float32)
    p = t / d
    y = _lp(n, 60) * 3.8 * (1 - p) + _lp(n, 5) * 1.1 * p
    return (y * np.sin(np.pi * p) ** 2).astype(np.float32)


def sfx_pop():
    t = _t(0.07)
    ph = 2 * np.pi * np.cumsum(400 + 650 * np.minimum(1, t / 0.035)) / SR
    return (np.sin(ph) * np.exp(-t / 0.025)).astype(np.float32)


def sfx_slam():
    b = sfx_boom(0.5)
    t = _t(0.5)
    return (b + 0.5 * _lp(_rng.standard_normal(len(t)).astype(np.float32), 4) * 2.2 * np.exp(-t / 0.035)).astype(np.float32)


def sfx_riser(d=0.4):
    t = _t(d)
    p = t / d
    ph = 2 * np.pi * np.cumsum(220 + 1400 * p ** 2) / SR
    n = _lp(_rng.standard_normal(len(t)).astype(np.float32), 4) * 1.8
    return ((0.4 * np.sin(ph) + n) * p ** 2).astype(np.float32)


def sfx_chime():
    out = np.zeros(int(0.5 * SR), dtype=np.float32)
    for k, f in enumerate((523.25, 659.25, 783.99)):
        t = _t(0.3)
        s = (0.65 * np.sin(2 * np.pi * f * t) * np.exp(-t / 0.03)).astype(np.float32)
        i = int(k * 0.065 * SR)
        out[i:i + len(s)] += s[:len(out) - i]
    return out


SFX_FN = {"boom": sfx_boom, "whoosh": sfx_whoosh, "pop": sfx_pop, "slam": sfx_slam, "riser": sfx_riser, "chime": sfx_chime}


def build_sfx(events, total):
    buf = np.zeros(int((total + 0.3) * SR), dtype=np.float32)
    for ev in events:
        t, name, gain = ev[:3]
        fn = SFX_FN.get(name)
        i = int(max(0.0, t) * SR)
        if fn is None or i >= len(buf):
            continue
        s = fn()
        s = s / (float(np.abs(s).max()) or 1.0) * gain
        buf[i:i + len(s)] += s[:len(buf) - i]
    return buf


def pick_music(total):
    pool = sorted(f for f in MUSIC_DIR.rglob("*") if f.is_file() and f.suffix.lower() in MUSIC_EXT) if MUSIC_DIR.is_dir() else []
    if not pool and pathlib.Path("music.mp3").exists():
        pool = [pathlib.Path("music.mp3")]
    if not pool:
        return None, 0.0
    track = pool[len(used) % len(pool)]
    spare = max(0.0, probe_duration(track) - total - 0.4)
    start = round(random.uniform(0, spare), 1) if spare > 0.6 else 0.0
    print("music:", track.name)
    return track, start


def duck_curve(voice):
    e = _lp(np.abs(voice), int(0.045 * SR))
    e = np.clip(e / 0.085, 0, 1)
    return 1.0 - 0.5 * _lp(e, int(0.09 * SR))


def loudnorm(src, dst):
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                            "loudnorm=I=-14.5:TP=-1.4:LRA=8:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
        st = json.loads(m.group())
        af = (f"loudnorm=I=-14.5:TP=-1.4:LRA=8:measured_I={st['input_i']}:measured_TP={st['input_tp']}:"
              f"measured_LRA={st['input_lra']}:measured_thresh={st['input_thresh']}:offset={st['target_offset']}:linear=true")
        ffmpeg("-i", src, "-af", af, "-ar", str(SR), "-ac", "1", dst)
    except Exception:
        pathlib.Path(dst).write_bytes(pathlib.Path(src).read_bytes())
    return dst


def mix_audio(voice_parts, sfx_events, total, out):
    n = int((total + 0.2) * SR)
    voice = np.zeros(n, dtype=np.float32)
    for t, y in voice_parts:
        i = int(t * SR)
        voice[i:i + len(y)] += y[:max(0, n - i)]
    mix = voice.copy()
    if SFX and sfx_events:
        mix += build_sfx(sfx_events, total)[:n] * 0.45 * SFX_VOL
    track, start = pick_music(total)
    if track:
        try:
            m = decode(track, start, total + 0.2, loop=True)[:n]
            m = np.pad(m, (0, n - len(m)))
            fade = np.minimum(1.0, np.minimum(np.arange(n) / (0.25 * SR), (n - np.arange(n)) / (0.4 * SR))).astype(np.float32)
            mix += m * MUSIC_VOL * duck_curve(voice) * fade
        except Exception:
            pass
    mix = np.tanh(mix * 0.93) / np.tanh(0.93)
    raw = W / "mix_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(mix, -1, 1) * 32000).astype("<i2").tobytes())
    return loudnorm(raw, out)


VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def list_clips():
    d = pathlib.Path("clips")
    return sorted(p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXT) if d.is_dir() else []


def get_fallback_clip():
    for query in ("pixel game", "parkour"):
        try:
            r = requests.get("https://pixabay.com/api/videos/", timeout=40, params={
                "key": os.environ["PIXABAY_API_KEY"], "q": query, "per_page": 12, "safesearch": "true"}).json()
            if r.get("hits"):
                vids = random.choice(r["hits"])["videos"]
                url = next(vids[k]["url"] for k in ("large", "medium", "small") if vids.get(k, {}).get("url"))
                p = W / "fallback.mp4"
                p.write_bytes(requests.get(url, timeout=120).content)
                return p.resolve()
        except Exception:
            pass


class ClipLib:
    def __init__(self, files):
        self.files, self.meta, self.recent = list(files), STATE["clips"], []

    def info(self, p):
        m = self.meta.setdefault(p.name, {})
        if "w" not in m:
            w, h = probe_size(p)
            m.update({"w": w, "h": h, "d": round(probe_duration(p), 2)})
        return m

    def pick(self, scene, need, hero=False):
        pool = random.sample(self.files, min(30, len(self.files)))
        weights, cands = [], []
        for p in pool:
            m = self.info(p)
            if m.get("w", 0) <= 0 or m.get("d", 0) < 0.7:
                continue
            tags = m.get("tags") or []
            w = 1.0
            if scene != "any" and tags:
                w *= 4.5 if scene in tags else 0.4
            if m["h"] < 480:
                w *= 0.25
            if p in self.recent[-5:]:
                w *= 0.06
            if m["d"] < need + 0.3:
                w *= 0.35
            if hero:
                w *= 1.5
            cands.append(p)
            weights.append(w)
        if not cands:
            return None, 0.0
        p = random.choices(cands, weights)[0]
        self.recent.append(p)
        d = self.meta[p.name]["d"]
        return p, round(random.uniform(0.08, max(0.08, d - need - 0.15)), 2)


def build_plan(s, qtype, topic, voice, pics, lib, case_no):
    qt = QTYPES[qtype]
    for L in voice:
        L["dur"] = len(L["audio"]) / SR

    texts = []
    stickers = []
    fx = []
    sfx = []
    voice_parts = []

    # Safe content width (leaves room for stroke + kinetic scale up to ~1.12)
    SAFE_W = 900

    # HOOK text — top third of screen, never overflows
    hook_txt = plain(s["hook"]).upper()
    size, lines = split_lines(hook_txt.split(), SAFE_W, [170, 150, 130, 110, 95], max_lines=3, max_words=3)
    # vertical placement: keep block well inside safe area
    line_h = size * 1.08
    block_h = line_h * len(lines)
    y0 = 320 - block_h / 2 + line_h / 2
    y0 = max(SAFE_Y + size * 0.6, min(y0, 520))
    marked = parse_marked(s["hook"])
    for li, idxs in enumerate(lines):
        row = " ".join(hook_txt.split()[i] for i in idxs)
        # final safety: shrink size if this single row is still too wide
        while text_w(row, size) > SAFE_W and size > 70:
            size -= 4
        color = RED if any(i < len(marked) and marked[i][1] == "r" for i in idxs) else GOLD
        texts.append({"t0": 0.0, "t1": 2.2, "txt": row, "size": size, "color": color,
                      "x": 540, "y": y0 + li * line_h, "style": "slam"})
    sfx.append((0.01, "boom", 1.15))
    fx.append({"t": 0.0, "kind": "flash", "amp": 0.6})
    fx.append({"t": 0.04, "kind": "shake", "amp": 14})

    t = 0.3
    for L in voice:
        L["t0"] = t
        L["t1"] = t + L["dur"]
        voice_parts.append((t, L["audio"]))
        words = plain(L["text"]).upper().split()
        if words:
            size2, line_idxs = split_lines(words, SAFE_W - 40, [88, 78, 68, 58], max_lines=3, max_words=3)
            # multi-line captions: stack them
            for li, idxs in enumerate(line_idxs):
                row = " ".join(words[i] for i in idxs)
                while text_w(row, size2) > SAFE_W - 40 and size2 > 48:
                    size2 -= 3
                texts.append({"t0": t, "t1": L["t1"] + 0.3, "txt": row, "size": size2, "color": WHITE,
                              "x": 540, "y": 1280 + li * size2 * 1.1, "style": "slide"})
        sfx.append((t - 0.015, "whoosh", 0.5))
        t = L["t1"] + 0.15

    # PAYOFF — auto-size so it always fits
    payoff = s["payoff"] or "IT WORKS"
    p_size = 190
    while text_w(payoff, p_size) > SAFE_W and p_size > 80:
        p_size -= 6
    texts.append({"t0": t, "t1": t + 1.5, "txt": payoff, "size": p_size, "color": GOLD,
                  "x": 540, "y": 900, "style": "zoom"})
    sfx.append((t - 0.12, "riser", 0.85))
    sfx.append((t, "slam", 1.05))
    sfx.append((t + 0.04, "chime", 0.75))
    fx.append({"t": t, "kind": "flash", "amp": 0.95})
    fx.append({"t": t, "kind": "shake", "amp": 16})

    total = t + 1.55

    if pics and topic["articles"]:
        p = pics.get(topic["articles"][0])
        if p:
            tile = fit_tile(make_sticker(Image.open(p), 360), 680, 440)
            stickers.append({"t0": 0.7, "t1": total - 0.25, "tile": tile, "x": 540, "y": 720, "wiggle": True})

    shots = []
    n_shots = max(4, int(total / 1.05))
    scene = s["lines"][0]["scene"] if s["lines"] else "any"
    pan = 1
    for i in range(n_shots):
        t0 = total * i / n_shots
        t1 = total * (i + 1) / n_shots
        clip, start = lib.pick(scene if i > 0 else "any", t1 - t0 + 0.15, hero=(i == 0))
        if clip is None:
            raise SystemExit("No clips")
        shots.append({"t0": t0, "t1": t1, "clip": clip, "start": start, "pan": pan,
                      "punch": 0.3 if i == 0 else 0.16})
        pan = -pan
        if i > 0:
            fx.append({"t": t0, "kind": "flash", "amp": 0.2})
            sfx.append((t0 - 0.015, "whoosh", 0.32))

    plan = {"total": total, "accent": qt["accent"], "label": qt["label"], "case_no": case_no,
            "texts": texts, "stickers": stickers, "shots": shots, "fx": fx}
    return plan, voice_parts, sfx


def make_video(s, qtype, topic, case_no):
    spoken = [("line", plain(L["t"])) for L in s["lines"]]
    if not spoken:
        spoken = [("line", plain(s["hook"]))]
    takes = tts_all(spoken)
    voice = []
    for (kind, text), (p, words) in zip(spoken, takes):
        y, w = voice_line(p, words)
        voice.append({"kind": kind, "text": text, "audio": y, "words": w})
    print("voice:", " | ".join(f"{len(v['audio'])/SR:.1f}s" for v in voice))

    imgs = {}
    for a in topic["articles"]:
        names = topic["images"][0] if topic.get("images") else None
        imgs[a] = fetch_images(a, names, "m0_", 2)

    class Pics:
        def __init__(self, d):
            self.d = {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in d.items() if v}
        def get(self, title):
            lst = self.d.get(re.sub(r"[^a-z0-9]", "", title.lower()), [])
            return lst[0] if lst else None
    pics = Pics(imgs)

    files = list_clips()
    if not files:
        fb = get_fallback_clip()
        if not fb:
            raise SystemExit("No clips")
        files = [fb]
    lib = ClipLib(files)
    print("clips:", len(files))

    plan, parts, sfx = build_plan(s, qtype, topic, voice, pics, lib, case_no)
    print(f"timeline: {plan['total']:.1f}s | shots: {len(plan['shots'])} | texts: {len(plan['texts'])}")
    wav = mix_audio(parts, sfx, plan["total"], W / "final.wav")
    encode(plan, wav, W / "final.mp4")
    return bool(imgs)


def youtube():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=Credentials(
        None, refresh_token=os.environ["YT_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"]))


def video_title(s):
    h = plain(s["hook"])
    return (h if "minecraft" in h.lower() else f"{h} Minecraft")[:78]


def upload(yt, s, qtype, topic, has_images):
    from googleapiclient.http import MediaFileUpload
    qt = QTYPES[qtype]
    title = video_title(s)
    tags = list(dict.fromkeys(["Minecraft", "Shorts", "MinecraftHack"] + qt["tags"] + s.get("tags", [])))[:13]
    desc = f"{plain(s['hook'])}\n\n{s.get('comment_q', '')}\n\n#Shorts #Minecraft #MinecraftTricks\n\n{DISCLAIMER}"[:4700]
    body = {"snippet": {"title": f"{title} #Shorts", "tags": tags, "categoryId": "20",
                        "description": desc, "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": env("PRIVACY", "public"), "selfDeclaredMadeForKids": False}}
    vid = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(W / "final.mp4"), chunksize=-1, resumable=True)).execute()["id"]
    print("uploaded:", vid)
    return vid


def add_to_playlists(yt, vid, qtype):
    try:
        items = yt.playlists().list(part="snippet", mine=True, maxResults=35).execute().get("items", [])
        for name in (SERIES, QTYPES[qtype]["playlist"]):
            pl = next((i["id"] for i in items if i["snippet"]["title"] == name), None) or \
                yt.playlists().insert(part="snippet,status", body={
                    "snippet": {"title": name, "description": f"{name} — instant Minecraft tricks."},
                    "status": {"privacyStatus": "public"}}).execute()["id"]
            yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
            print("playlist:", name)
    except Exception as e:
        print("playlist skip:", str(e)[:120])


def post_comment(yt, vid, s):
    q = s.get("comment_q", "")
    try:
        if q:
            yt.commentThreads().insert(part="snippet", body={"snippet": {"videoId": vid, "topLevelComment": {
                "snippet": {"textOriginal": f"{q} 👇"}}}}).execute()
            print("comment ok")
    except Exception as e:
        print("comment skip:", str(e)[:120])


def performance(yt):
    now = time.time()
    rows = [e for e in used[-90:] if e.get("vid") and now - e.get("ts", now) > 86400]
    views = {}
    try:
        for i in range(0, len(rows), 50):
            r = yt.videos().list(part="statistics", id=",".join(e["vid"] for e in rows[i:i+50])).execute()
            for it in r.get("items", []):
                views[it["id"]] = int(it.get("statistics", {}).get("viewCount", 0))
    except Exception:
        return []
    return [(e, views[e["vid"]]) for e in rows if e["vid"] in views]


def bandit(options, field, rows, floor=2):
    trials = {o: sum(1 for e in used if e.get(field) == o) for o in options}
    low = [o for o in options if trials[o] < floor]
    if low:
        return random.choice(low)
    score = {o: [math.log1p(v) for e, v in rows if e.get(field) == o] for o in options}
    allv = [x for s in score.values() for x in s]
    if not allv:
        return random.choice(options)
    g = sum(allv) / len(allv)
    mean = {o: (sum(score[o]) + 1.5 * g) / (len(score[o]) + 1.5) for o in options}
    top = max(mean.values())
    return random.choices(options, [math.exp((mean[o] - top) / 0.4) + 0.12 for o in options])[0]


def main():
    ensure_fonts()
    yt = None if DRY_RUN else youtube()
    rows = performance(yt) if (LEARN and yt) else []
    forced = env("QTYPE")
    qtype = forced if forced in QTYPES else (bandit(list(QTYPES), "qtype", rows) if LEARN else list(QTYPES)[len(used) % len(QTYPES)])
    s = topic = None
    for attempt in range(3):
        try:
            topic = pick_topic(qtype)
            s = gen_script(topic, qtype)
            break
        except Exception as e:
            print(f"retry {attempt+1}:", e)
            if topic:
                AVOID.update(topic["articles"])
    if s is None:
        raise SystemExit("No script")
    print("type:", qtype, "| topic:", topic["key"])
    print(json.dumps(s, ensure_ascii=False))
    has_images = make_video(s, qtype, topic, len(used) + 1)
    if DRY_RUN:
        print("DRY_RUN →", W / "final.mp4")
        return
    vid = upload(yt, s, qtype, topic, has_images)
    used.append({"key": topic["key"], "arts": topic["articles"], "qtype": qtype, "q": plain(s["hook"]),
                 "vid": vid, "ts": int(time.time())})
    save_state()
    add_to_playlists(yt, vid, qtype)
    post_comment(yt, vid, s)


if __name__ == "__main__":
    main()
