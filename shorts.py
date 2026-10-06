import asyncio, json, os, pathlib, random, subprocess, textwrap, time
import edge_tts
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

W = pathlib.Path("work")
W.mkdir(exist_ok=True)
USED = pathlib.Path("used.json")
used = json.loads(USED.read_text(encoding="utf-8")) if USED.exists() else []
VOICE = os.getenv("VOICE", "en-US-GuyNeural")
THEMES = "Minecraft lore, mob facts, hidden mechanics, what-if scenarios, survival stories, mysteries and legends of the game world"
CLIPS = sorted(pathlib.Path("clips").glob("*.mp4"))
random.shuffle(CLIPS)
MUSIC = pathlib.Path("music.mp3")  # optional background music in the repo root

# ---- Design: "Parkour + Fact" -------------------------------------------------
# Frame 1080x1920. YouTube Shorts UI: buttons on the right (~15%), channel name/caption
# at the bottom (~22%), search/camera at the top (~8%). Captions stay left, above the bottom zone.
CUSTOM_FONT = pathlib.Path("font.ttf")  # e.g. Press Start 2P; falls back to DejaVu Sans Bold
if CUSTOM_FONT.exists():
    FONTS_DIR = str(CUSTOM_FONT.resolve().parent)
    FONT_SIZE, MAX_CHARS, BOLD = 54, 14, 0
else:
    FONTS_DIR = "/usr/share/fonts/truetype/dejavu"
    FONT_SIZE, MAX_CHARS, BOLD = 70, 14, -1
TEXT_X = int(os.getenv("TEXT_X", "60"))                 # left margin, px
TEXT_BOTTOM = float(os.getenv("TEXT_BOTTOM", "0.76"))   # bottom edge of captions (fraction of height)
RIGHT_MARGIN = 170
HILITE = os.getenv("HILITE", "&H004EC1F2&")  # gold (BGR) for the current word
WHITE = "&H00FFFFFF&"
SEGMENT_SECONDS = (6.0, 10.0)  # length range of each parkour scene before a cut


def font_family():
    if CUSTOM_FONT.exists():
        try:
            out = subprocess.run(["fc-scan", "--format", "%{family}", str(CUSTOM_FONT)],
                                 capture_output=True, text=True, check=True).stdout
            fam = out.split(",")[0].strip()
            if fam:
                return fam
        except Exception:
            pass
        return "Press Start 2P"
    return "DejaVu Sans"


def gen_script():
    prompt = (
        "You write scripts for a YouTube Shorts channel about Minecraft facts, "
        "read by a male narrator over parkour gameplay. "
        f"Topics: {THEMES}. Pick one new topic, do not repeat these: {used[-60:]}. "
        "Write the script in English: 6-8 short sentences, 90-110 words total, "
        "first sentence is a hook, last sentence is a surprising ending. "
        'Return JSON: {"title": "up to 70 characters", '
        '"sentences": ["sentence 1", "sentence 2"], '
        '"tags": ["tag1", "tag2"]}'
    )
    models = [os.getenv("GEMINI_MODEL", "gemini-3.5-flash"), "gemini-flash-latest", "gemini-3.1-flash-lite"]
    for attempt in range(4):
        for model in models:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": os.environ["GEMINI_API_KEY"]},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=120,
            )
            if r.ok:
                return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            print(model, r.status_code)
        time.sleep(20)
    r.raise_for_status()


def narration_text(data):
    parts = []
    for s in data["sentences"]:
        parts.append(s["text"] if isinstance(s, dict) else str(s))
    return " ".join(p.strip() for p in parts if p.strip())


async def _tts(text, path):
    try:
        comm = edge_tts.Communicate(text, VOICE, rate="+5%", boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, VOICE, rate="+5%")
    words = []
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append((chunk["text"], chunk["offset"] / 1e7, chunk["duration"] / 1e7))
    return words


def probe_duration(path):
    try:
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        return 0.0


def estimate_words(text, total):
    toks = text.split()
    weights = [len(t) + 2 for t in toks]
    scale = total / sum(weights)
    out, t = [], 0.0
    for tok, w in zip(toks, weights):
        out.append((tok, t, w * scale))
        t += w * scale
    return out


def ass_time(sec):
    sec = max(0, sec)
    cs = int(round(sec * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def clean(word):
    return word.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ").strip()


def group_words(words):
    groups, cur, chars = [], [], 0
    for w in words:
        wl = len(clean(w[0]))
        if cur and (len(cur) >= 3 or chars + 1 + wl > MAX_CHARS):
            groups.append(cur)
            cur, chars = [], 0
        cur.append(w)
        chars += wl + (1 if len(cur) > 1 else 0)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words, title, fact_no, total):
    family = font_family()
    margin_v = int(round(1920 * (1 - TEXT_BOTTOM)))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{family},{FONT_SIZE},{WHITE},{WHITE},&H00000000&,&H80000000&,{BOLD},0,0,0,100,100,0,0,1,6,4,1,{TEXT_X},{RIGHT_MARGIN},{margin_v},1",
        f"Style: Title,{family},{int(FONT_SIZE * 0.8)},{HILITE},{HILITE},&H00000000&,&H80000000&,{BOLD},0,0,0,100,100,0,0,1,5,3,7,{TEXT_X},{RIGHT_MARGIN},215,1",
        f"Style: Tag,{family},{int(FONT_SIZE * 0.6)},{WHITE},{WHITE},&H00000000&,&H80000000&,{BOLD},0,0,0,100,100,0,0,1,4,2,7,{TEXT_X},{RIGHT_MARGIN},165,1",
        "Style: Bar,Arial,20,&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    # persistent fact tag
    lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Tag,,0,0,0,,FACT #{fact_no}")
    # title for the first seconds
    if title:
        wrapped = "\\N".join(textwrap.wrap(clean(title), 18))
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},Title,,0,0,0,,{{\\fad(0,250)}}{wrapped}")
    # progress bar: thin gold line growing left to right
    ms = int(total * 1000)
    lines.append(
        f"Dialogue: 2,{ass_time(0)},{ass_time(total)},Bar,,0,0,0,,"
        f"{{\\an7\\pos(0,1884)\\fscx1\\1c&H004EC1F2&\\bord0\\shad0\\p1\\t(0,{ms},\\fscx100)}}"
        "m 0 0 l 1080 0 1080 10 0 10{\\p0}"
    )
    # karaoke captions: group shown continuously, current word gold + pop
    groups = group_words(words)
    for gi, grp in enumerate(groups):
        group_end = grp[-1][1] + grp[-1][2] + 0.12
        if gi + 1 < len(groups):
            group_end = min(group_end, groups[gi + 1][0][1])
        for wi, w in enumerate(grp):
            start = w[1]
            end = grp[wi + 1][1] if wi + 1 < len(grp) else group_end
            if end <= start:
                end = start + 0.05
            parts = []
            for wj, ww in enumerate(grp):
                txt = clean(ww[0])
                if wj == wi:
                    parts.append(f"{{\\c{HILITE}\\fscx114\\fscy114\\t(0,90,\\fscx100\\fscy100)}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            lines.append(f"Dialogue: 3,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}")
    path = W / "subs.ass"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def get_fallback_clip():
    for query in ("parkour", "pixel game", "nature"):
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": os.environ["PIXABAY_API_KEY"], "q": query, "per_page": 20, "safesearch": "true"},
            timeout=60,
        ).json()
        if r.get("hits"):
            break
    hit = random.choice(r["hits"])
    vids = hit["videos"]
    url = next(vids[k]["url"] for k in ("large", "medium", "small", "tiny") if vids.get(k, {}).get("url"))
    p = W / "fallback.mp4"
    p.write_bytes(requests.get(url, timeout=180).content)
    return p.resolve()


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, cwd=W)


def build_background(total):
    pool = [c.resolve() for c in CLIPS] or [get_fallback_clip()]
    parts, covered, i = [], 0.0, 0
    while covered < total - 0.05:
        src = pool[i % len(pool)]
        i += 1
        length = min(total - covered, random.uniform(*SEGMENT_SECONDS))
        if total - covered - length < 2.5:  # avoid a tiny last scene
            length = total - covered
        dur = probe_duration(src)
        start = round(random.uniform(0, max(0, dur - length)), 2)
        out = f"bg{len(parts)}.mp4"
        ff("-ss", str(start), "-stream_loop", "-1", "-i", str(src), "-t", f"{length:.3f}", "-an",
           "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", out)
        parts.append(out)
        covered += length
    (W / "bg.txt").write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
    ff("-f", "concat", "-safe", "0", "-i", "bg.txt", "-c", "copy", "bg.mp4")


def render(ass_path, total):
    vf = (f"eq=contrast=1.06:saturation=1.15,"
          f"ass={ass_path.resolve()}:fontsdir={FONTS_DIR}")
    args = ["-i", "bg.mp4", "-i", "voice.mp3"]
    if MUSIC.exists():
        args += ["-stream_loop", "-1", "-i", str(MUSIC.resolve()),
                 "-filter_complex",
                 f"[0:v]{vf}[v];[2:a]volume=0.10[m];[1:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
                 "-map", "[v]", "-map", "[a]"]
    else:
        args += ["-vf", vf, "-map", "0:v", "-map", "1:a"]
    ff(*args, "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", "final.mp4")


def make_video(data):
    text = narration_text(data)
    words = asyncio.run(_tts(text, W / "voice.mp3"))
    audio = probe_duration(W / "voice.mp3")
    if not words:
        words = estimate_words(text, audio)
    total = audio + 0.5
    build_background(total)
    ass = build_ass(words, data["title"], len(used) + 1, total)
    render(ass, total)


def upload(title, tags):
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
    )
    yt = build("youtube", "v3", credentials=creds)
    tags = list(dict.fromkeys(["Minecraft"] + tags))
    body = {
        "snippet": {
            "title": f"{title[:85]} #Shorts",
            "description": f"{title}\n\n#Shorts #Minecraft "
            + " ".join("#" + t.replace(" ", "") for t in tags[1:5])
            + "\n\nNot an official Minecraft product. Not approved by or associated with Mojang or Microsoft.",
            "tags": tags[:15],
            "categoryId": "20",
        },
        "status": {
            "privacyStatus": os.getenv("PRIVACY", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(W / "final.mp4"), chunksize=-1, resumable=True)
    res = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    print("uploaded:", res["id"])


if __name__ == "__main__":
    data = gen_script()
    make_video(data)
    upload(data["title"], data.get("tags", []))
    used.append(data["title"])
    USED.write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
