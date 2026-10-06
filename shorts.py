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

# Font: put font.ttf (e.g. Press Start 2P) in the repo root. Falls back to DejaVu Bold.
CUSTOM_FONT = pathlib.Path("font.ttf")
if CUSTOM_FONT.exists():
    FONT, FONT_SIZE, WRAP = str(CUSTOM_FONT.resolve()), 54, 15
else:
    FONT, FONT_SIZE, WRAP = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 66, 15
# Layout for YouTube Shorts UI (frame 1080x1920): like/comment buttons are on the right (~15%),
# channel name + caption at the bottom (~22%). Keep text left and above the bottom zone.
TEXT_X = int(os.getenv("TEXT_X", "60"))            # left edge of the text block, px
TEXT_BOTTOM = float(os.getenv("TEXT_BOTTOM", "0.76"))  # bottom edge of text block, fraction of height
MUSIC = pathlib.Path("music.mp3")  # optional background music in the repo root
TEXT_COLOR = os.getenv("TEXT_COLOR", "white")
TITLE_COLOR = os.getenv("TITLE_COLOR", "0xf2c14e")


def gen_script():
    prompt = (
        "You write scripts for a YouTube Shorts channel set in the Minecraft universe, "
        "read by a male narrator. "
        f"Topics: {THEMES}. Pick one new topic, do not repeat these: {used[-60:]}. "
        "Write the script in English: 6-8 short sentences, 90-110 words total, "
        "first sentence is a hook, last sentence is a surprising ending. "
        'Return JSON: {"title": "up to 70 characters", '
        '"sentences": [{"text": "sentence", "q": "2-3 English words for stock video search"}], '
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


async def tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate="+5%").save(str(path))


def probe_duration(path):
    try:
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        return 0.0


def get_clip(q, i):
    if CLIPS:
        src = CLIPS[i % len(CLIPS)].resolve()
        dur = probe_duration(src)
        return str(src), round(random.uniform(0, max(0, dur - 12)), 2)
    for query in (q, "pixel art landscape", "nature"):
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
    (W / f"c{i}.mp4").write_bytes(requests.get(url, timeout=180).content)
    return str((W / f"c{i}.mp4").resolve()), 0


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, cwd=W)


def build_segment(i, src, ss, text, dur, title=None):
    (W / f"t{i}.txt").write_text(textwrap.fill(text, WRAP), encoding="utf-8")
    d = f"{dur:.2f}"
    # slow pan: alternate direction each segment; scale 10% larger than frame
    if i % 2 == 0:
        px = f"(in_w-1080)*t/{d}"
    else:
        px = f"(in_w-1080)*(1-t/{d})"
    vf = (
        "scale=1188:2112:force_original_aspect_ratio=increase,crop=1188:2112,fps=30,"
        f"crop=1080:1920:x='{px}':y='(in_h-1920)/2',"
        "eq=contrast=1.08:saturation=1.18,vignette=PI/6,"
        f"drawtext=fontfile={FONT}:textfile=t{i}.txt:fontsize={FONT_SIZE}:fontcolor={TEXT_COLOR}:"
        "borderw=5:bordercolor=black:shadowx=4:shadowy=4:shadowcolor=black@0.7:line_spacing=14:"
        "box=1:boxcolor=black@0.45:boxborderw=26:"
        f"x={TEXT_X}:y=h*{TEXT_BOTTOM}-text_h:alpha='min(1,t/0.18)'"
    )
    if title and i == 0:
        (W / "title.txt").write_text(textwrap.fill(title, 18), encoding="utf-8")
        vf += (
            f",drawtext=fontfile={FONT}:textfile=title.txt:fontsize={int(FONT_SIZE * 0.8)}:"
            f"fontcolor={TITLE_COLOR}:borderw=5:bordercolor=black:line_spacing=12:"
            f"x={TEXT_X}:y=h*0.12:enable='lt(t,3.2)'"
        )
    ff("-ss", str(ss), "-stream_loop", "-1", "-i", src, "-i", f"s{i}.mp3",
       "-vf", vf, "-map", "0:v", "-map", "1:a", "-t", d,
       "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-ar", "44100", "-ac", "2", f"seg{i}.mp4")


def make_video(sentences, title):
    segs = []
    for i, s in enumerate(sentences):
        asyncio.run(tts(s["text"], W / f"s{i}.mp3"))
        src, ss = get_clip(s["q"], i)
        dur = max(1.5, probe_duration(W / f"s{i}.mp3") + 0.15)
        build_segment(i, src, ss, s["text"], dur, title)
        segs.append(f"file 'seg{i}.mp4'")
    (W / "list.txt").write_text("\n".join(segs), encoding="utf-8")
    ff("-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "out.mp4")
    if MUSIC.exists():
        ff("-i", "out.mp4", "-stream_loop", "-1", "-i", str(MUSIC.resolve()),
           "-filter_complex", "[1:a]volume=0.10[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
           "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", "final.mp4")
    else:
        (W / "final.mp4").write_bytes((W / "out.mp4").read_bytes())


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
    make_video(data["sentences"], data["title"])
    upload(data["title"], data.get("tags", []))
    used.append(data["title"])
    USED.write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
