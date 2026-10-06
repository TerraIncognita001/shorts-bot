import asyncio, json, os, pathlib, random, shutil, subprocess, textwrap, time
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
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
THEMES = "Minecraft lore, mob facts, hidden mechanics, what-if scenarios, survival stories, mysteries and legends of the game world"
CLIPS = sorted(pathlib.Path("clips").glob("*.mp4"))
random.shuffle(CLIPS)


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


def get_clip(q, i):
    if CLIPS:
        shutil.copy(CLIPS[i % len(CLIPS)], W / f"c{i}.mp4")
        return
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


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, cwd=W)


def make_video(sentences):
    segs = []
    for i, s in enumerate(sentences):
        asyncio.run(tts(s["text"], W / f"s{i}.mp3"))
        get_clip(s["q"], i)
        (W / f"t{i}.txt").write_text(textwrap.fill(s["text"], 18), encoding="utf-8")
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,"
            f"drawtext=fontfile={FONT}:textfile=t{i}.txt:fontsize=64:fontcolor=white:"
            "borderw=4:bordercolor=black:line_spacing=12:x=(w-text_w)/2:y=h*0.62"
        )
        ff("-stream_loop", "-1", "-i", f"c{i}.mp4", "-i", f"s{i}.mp3",
           "-vf", vf, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", f"seg{i}.mp4")
        segs.append(f"file 'seg{i}.mp4'")
    (W / "list.txt").write_text("\n".join(segs), encoding="utf-8")
    ff("-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "out.mp4")


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
    media = MediaFileUpload(str(W / "out.mp4"), chunksize=-1, resumable=True)
    res = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    print("uploaded:", res["id"])


if __name__ == "__main__":
    data = gen_script()
    make_video(data["sentences"])
    upload(data["title"], data.get("tags", []))
    used.append(data["title"])
    USED.write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
