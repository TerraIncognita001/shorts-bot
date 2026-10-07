import array, asyncio, html, json, math, os, pathlib, random, re, subprocess, time, wave
import requests

W = pathlib.Path("work")
W.mkdir(exist_ok=True)
USED = pathlib.Path("used.json")
used = json.loads(USED.read_text(encoding="utf-8")) if USED.exists() else []
env = os.getenv

# ---- Categories: label, accent, kind, playlist, topic, story, tags (rotate each run; force with CATEGORY) ----
CATEGORIES = {
    "facts": ("MINECRAFT FACTS", "0x7CB342", "single", "Minecraft Facts",
              "a surprising, lesser-known fact about a Minecraft block, item, structure, biome or dimension",
              "Explain what it is, where it is found, and the surprising details most players never notice.",
              ["Minecraft facts", "Minecraft tips"]),
    "mobs": ("MOB FILES", "0xE53935", "single", "Mob Files",
             "a Minecraft mob (hostile, neutral or passive, including bosses and rare variants)",
             "Give its key stats (health, damage, spawn conditions, drops), unique behaviors and one rare detail.",
             ["Minecraft mobs", "Minecraft mob facts"]),
    "secrets": ("HIDDEN MECHANICS", "0xBA68C8", "single", "Hidden Mechanics",
                "a hidden or little-known Minecraft game mechanic (spawning rules, enchanting, villager trading, "
                "redstone behavior, damage, light levels, world generation)",
                "Explain exactly how the mechanic works with numbers, and how a player can use it.",
                ["Minecraft secrets", "Minecraft mechanics"]),
    "lore": ("LORE & LEGENDS", "0x26C6DA", "single", "Lore & Legends",
             "Minecraft lore, a legend, a removed feature, an unused feature, the history of a famous update, "
             "the Far Lands, ancient structures or the End Poem",
             "Tell the story: when it was added or removed, what players believed, and what is actually true.",
             ["Minecraft lore", "Minecraft history"]),
    "versus": ("VERSUS", "0x29B6F6", "versus", "Minecraft Versus",
               "two Minecraft rivals of the same type that are often compared (mob vs mob, weapon vs weapon, "
               "armor vs armor, block vs block)",
               "Compare them on 3 aspects with exact numbers from the text (e.g. health, damage, durability, speed), "
               "alternating between the two, then give a fair verdict based on the text.",
               ["Minecraft versus", "Minecraft comparison"]),
    "quiz": ("GUESS THE MOB", "0xFFB300", "quiz", "Guess the Mob",
             "a visually recognizable Minecraft mob",
             "The headline asks viewers to name it (e.g. 'Can you name this mob?'). Give clues (behavior, "
             "drops, spawn place) WITHOUT the name. Only the LAST line reveals it: \"It's the [r]Name[/r]!\". "
             "Nothing before the last line may reveal the name.",
             ["Minecraft quiz", "guess the mob"]),
    "myth": ("MYTH VS TRUTH", "0xFF7043", "single", "Minecraft Myth vs Truth",
             "a Minecraft subject surrounded by a popular myth or misconception among players",
             "Line 1 states the popular myth starting with 'Myth:'. Then 'Truth:' and the real facts from the text, "
             "with numbers. Only use a myth the text itself contradicts.",
             ["Minecraft myths", "Minecraft myth busting"]),
}
CATEGORY = env("CATEGORY") or list(CATEGORIES)[len(used) % len(CATEGORIES)]
LABEL, ACCENT, KIND, PLAYLIST, TOPIC, STORY, CAT_TAGS = CATEGORIES[CATEGORY]

# ---- Settings (override in the workflow env) ----------------------------------
CLIPS = sorted(pathlib.Path("clips").glob("*.mp4"))     # your own gameplay clips
random.shuffle(CLIPS)
IMAGES = env("IMAGES", "1") == "1"                      # 0 = clips only, no wiki pictures
MUSIC = pathlib.Path("music.mp3")                       # background track, repo root
MUSIC_START = float(env("MUSIC_START", "0"))
CHANNEL = env("CHANNEL", "TERRA INCOGNITA")                   # shown in every frame
FILE_NO = len(used) + 1                                 # running number shown on screen
NARRATION = env("NARRATION", "1") == "1"
VOICE = env("VOICE", "en-US-GuyNeural")
MUSIC_VOL = float(env("MUSIC_VOL", "0.08" if NARRATION else "0.18"))
SFX = env("SFX", "1") == "1"
SFX_VOL = float(env("SFX_VOL", "1.0"))                   # 0.5 = quieter, 2 = louder
LANGS = [l.strip() for l in env("LANGS", "es,pt,de,fr,it,pl,uk,tr").split(",") if l.strip()]
MISTAKE = env("MISTAKE", "1") == "1"                    # one tiny harmless mistake for comments
FUNNY = float(env("FUNNY", "0.35"))                     # chance of one silly sound (myth/versus)
MIN_SEC, MAX_SEC, SHOT_SEC, MAX_IMAGES = 12.0, 30.0, 3.0, 6
TEASE_SEC = 1.3                                         # frozen "wait for it" opening, paid off at the end
UA = {"User-Agent": "TerraIncognitaShortsBot/1.0 (https://terraincognita001.github.io/terra-incognita/)"}
MCWIKI = "https://minecraft.wiki/api.php"
DISCLAIMER = "Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft."

# ---- Layout 1080x1920: black bar | picture | black bar ------------------------
TOP_BAR, MID_H = 380, 810                                # picture in the eye-line zone
BOT_Y = TOP_BAR + MID_H
WHITE, RED, YELLOW = "&H00FFFFFF&", "&H003539E5&", "&H0000D7FF&"   # ASS colours (BGR)
ACC_ASS = f"&H00{ACCENT[6:8]}{ACCENT[4:6]}{ACCENT[2:4]}&"
FONT = pathlib.Path("font.ttf")                         # e.g. Press Start 2P
FONTS_DIR = str(FONT.resolve().parent) if FONT.exists() else "/usr/share/fonts/truetype/dejavu"
SKIP_IMG = re.compile(r"sprite|invicon|icon|logo|grid|slot|button|achievement|advancement|recipe|crafting|"
                      r"atlas|gui|hud|pattern|banner|edition|screenshot of|\.gif$|\.svg$", re.I)


def x264(crf):
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, cwd=W)


def probe(path, entries="format=duration"):
    try:
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries,
                               "-of", "default=nw=1:nk=1", str(path)],
                              capture_output=True, text=True, check=True).stdout.split()
    except Exception:
        return []


def probe_duration(path):
    out = probe(path)
    return float(out[0]) if out else 0.0


def font_family():
    try:
        out = subprocess.run(["fc-scan", "--format", "%{family}", str(FONT)],
                             capture_output=True, text=True, check=True).stdout
        return out.split(",")[0].strip() or "DejaVu Sans"
    except Exception:
        return "DejaVu Sans"


FAMILY = font_family()
FSCALE = 0.62 if "press start" in FAMILY.lower() else 1.0     # pixel fonts are much wider


# ---- Gemini ------------------------------------------------------------------
def gemini(prompt, ok, rounds=6):
    models = [env("GEMINI_MODEL", "gemini-3.5-flash"), "gemini-flash-latest", "gemini-3.1-flash-lite"]
    for _ in range(rounds):
        for model in models:
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": os.environ["GEMINI_API_KEY"]}, timeout=(15, 90),
                                  json={"contents": [{"parts": [{"text": prompt}]}],
                                        "generationConfig": {"responseMimeType": "application/json"}})
                if not r.ok:
                    print(model, r.status_code)
                    continue
                data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                if ok(data):
                    return data
                print(model, "incomplete JSON")
            except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
                print(model, "error:", type(e).__name__)
        time.sleep(60)
    raise SystemExit("Gemini unavailable, try later")


# ---- Minecraft Wiki: text and pictures ---------------------------------------
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


def wiki_page(title):
    """(title, plain text, image names) of a Minecraft Wiki page, or (None, None, [])."""
    d = wiki_get(action="parse", page=title, prop="text|images", disabletoc=1).get("parse")
    if not d:
        return None, None, []
    t = re.sub(r"(?s)<(style|script|sup)[^>]*>.*?</\1>", " ", d.get("text", ""))
    t = re.sub(r"<br\s*/?>|</(p|li|tr|h\d|div)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()
    t = re.split(r"\n\s*(?:References|Navigation|External links|See also|Gallery)\s*(?:\[edit\])?\s*\n", t)[0]
    return d.get("title", title), t[:12000], d.get("images", [])


def wiki_images(title, names=None, limit=MAX_IMAGES):
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
    p = W / name
    try:
        r = requests.get(url, headers=UA, timeout=120)
        r.raise_for_status()
        p.write_bytes(r.content)
        if not probe(p, "stream=width"):
            raise ValueError("not an image")
        return p.resolve()
    except Exception as e:
        print("skip image:", e)


def get_images(article, names, related, prefix, need):
    """Pictures of the main page first, then of related pages, no duplicates."""
    items, seen = wiki_images(article, names), set()
    for t in related[:4]:
        items += wiki_images(t, limit=2)
    imgs = []
    for url, _ in items:
        key = url.split("/")[-1].lower()
        if key in seen or len(imgs) >= need:
            continue
        seen.add(key)
        p = download(url, f"{prefix}{len(imgs)}{pathlib.Path(url.split('?')[0]).suffix.lower() or '.png'}")
        if p:
            imgs.append(p)
    return imgs


# ---- Topic & script ----------------------------------------------------------
CATS = {   # Minecraft Wiki categories the topic is drawn from (missing ones are skipped)
    "facts": ["Category:Blocks", "Category:Items", "Category:Generated structures", "Category:Biomes"],
    "mobs": ["Category:Mobs", "Category:Hostile mobs", "Category:Passive mobs", "Category:Neutral mobs"],
    "secrets": ["Category:Gameplay", "Category:Game mechanics", "Category:Redstone", "Category:Enchantments"],
    "lore": ["Category:Removed features", "Category:Unused features", "Category:Joke features",
             "Category:Generated structures"],
    "versus": ["Category:Mobs", "Category:Weapons", "Category:Armor", "Category:Tools"],
    "quiz": ["Category:Mobs", "Category:Hostile mobs", "Category:Passive mobs"],
    "myth": ["Category:Mobs", "Category:Blocks", "Category:Items", "Category:Game mechanics"],
}


def candidates(limit=40):
    """Random, less-known subjects from the category and a few of its subcategories, not used yet."""
    def members(cat):
        try:
            return wiki_get(action="query", list="categorymembers", cmtitle=cat, cmtype="page|subcat",
                            cmlimit=500).get("query", {}).get("categorymembers", [])
        except Exception:
            return []
    titles = set()
    for cat in CATS.get(CATEGORY, []):
        got = members(cat)
        titles |= {m["title"] for m in got if m.get("ns") == 0}
        subs = [m["title"] for m in got if m.get("ns") == 14]
        for sub in random.sample(subs, min(3, len(subs))):
            titles |= {m["title"] for m in members(sub) if m.get("ns") == 0}
    seen = " | ".join(used[-400:])
    titles = [t for t in titles if t not in seen and not t.startswith(("List of", "Category"))]
    return random.sample(titles, min(limit, len(titles)))


def pick_topic():
    keys = ["article", "article2"] if KIND == "versus" else ["article"]
    if KIND == "versus":
        ask = f"Suggest ONE pair for a Minecraft comparison Short: {TOPIC}. Each must have its own Minecraft Wiki page. "
        fmt = ('{"article": "exact Minecraft Wiki (minecraft.wiki) page title of the first", '
               '"article2": "exact Minecraft Wiki page title of the second"}')
    else:
        ask = f"Suggest ONE specific Minecraft subject for a YouTube Short. Category: {TOPIC}. "
        fmt = '{"article": "exact Minecraft Wiki (minecraft.wiki) page title"}'
    cands = candidates()
    if len(cands) >= 8:
        ask += (f"Choose ONLY from this list of exact page titles: {cands}. Prefer a lesser-known subject with a "
                "surprising story; avoid the most famous, overexposed ones that every channel already covers. ")
    for _ in range(5):
        t = gemini(ask + f"Do NOT use any of these: {used[-150:]}. Return JSON: {fmt}",
                   lambda d: isinstance(d, dict) and all(d.get(k) for k in keys))
        if not WIKI_OK:                               # wiki blocked: script from Gemini's own knowledge
            arts = [str(t[k]) for k in keys]
            if " vs ".join(arts) not in used:
                return {"articles": arts, "key": " vs ".join(arts), "text": None, "images": [[]] * len(arts)}
            continue
        got = [wiki_page(t[k]) for k in keys]
        arts = [a for a, _, _ in got]
        if all(arts) and len(set(arts)) == len(keys) and " vs ".join(arts) not in used \
                and all(len(x) > 1200 for _, x, _ in got):
            text = got[0][1] if len(got) == 1 else "\n\n".join(f"=== {a} ===\n{x[:6500]}" for a, x, _ in got)
            return {"articles": arts, "key": " vs ".join(arts), "text": text, "images": [g[2] for g in got]}
        if not WIKI_OK:
            continue
        print("topic rejected:", t)
    raise SystemExit("No usable topic")


HOOKS = [   # Blair Warren angles, one per video in turn
    "justify their failures (e.g. 'It's not your fault you keep dying to Creepers')",
    "confirm their suspicions (e.g. 'Villagers really ARE scamming you')",
    "allay their fears (e.g. 'The Warden is easier to beat than you think')",
    "encourage their dreams (e.g. 'This trick makes you rich in one day')",
    "throw rocks at a common enemy (e.g. 'Mojang never told you this')",
]


def packaging_rules():
    """Hook angle, honest packaging, peak before the end, sometimes a rhyme."""
    r = ("- title, headline and the first spoken words are about the same subject with the same key words, and "
         "they open a question: they must NEVER reveal the answer, twist or payoff.\n")
    if KIND == "quiz":
        r += "- The line before the reveal is the strongest clue.\n"
    else:
        r += (f"- headline hook angle: {HOOKS[len(used) % len(HOOKS)]}.\n"
              "- The second-to-last line is the peak: the single most surprising fact.\n")
    if random.random() < 0.25:
        r += "- Make the title a short catchy rhyme.\n"
    r += ("- Information gain: skip the most famous, widely repeated fact about this subject. Connect 2-3 facts "
          "from the text into ONE new conclusion none of them states alone (e.g. '...which means...').\n")
    return r


def gen_script(topic):
    source = (f"Use ONLY facts stated in this Minecraft Wiki text, keep names and numbers exact:\n<<<\n"
              f"{topic['text']}\n>>>\n" if topic["text"] else
              "Use only accurate, well-established facts about the current Java Edition; leave out anything "
              "you are not sure about.\n")
    loop = ("- The headline is spoken FIRST as the hook. The LAST line must be an unfinished sentence with no "
            "final punctuation that flows straight into the headline when the video loops, e.g. last line "
            "'And that is exactly why' + headline 'Creepers run from cats'.\n" if KIND != "quiz" else "")
    extra = "- Exactly one line is a bold but defensible opinion fans will argue with, starting with 'Honestly,' " \
            "or 'Hot take:' (e.g. 'Hot take: the Warden is the most useless mob').\n"
    if MISTAKE:
        extra += ("- Exactly one other line contains ONE small, harmless mistake in a minor number or detail "
                  "(e.g. a slightly wrong drop count) so fans correct it in the comments. Never in the headline, "
                  "never about safety. Put that line in the field \"mistake\".\n")
    return gemini(
        "You write a 15-25 second YouTube Short about Minecraft, voiced by an energetic male narrator over "
        f'gameplay, about "{" vs ".join(topic["articles"])}". {source}Format: {STORY}\nRules:\n'
        "- headline: the hook, max 7 words, spoken in the first 2 seconds and shown on screen the whole time: "
        "a shocking number, a question or a contradiction. Never start with 'Did you know' or 'In Minecraft'.\n"
        "- Zero filler: only the 2-3 strongest concrete details (numbers, versions, blocks, items).\n"
        "- Friendly, exciting tone for gamers.\n" + extra + packaging_rules() + loop +
        "- lines: 3-5 narration lines, each max 10 words, total 35-55 words.\n"
        "- In headline and each line wrap the single most important detail (name or number) in [r]...[/r] "
        "and at most one secondary detail in [y]...[/y].\n"
        "- question: one short question to viewers that invites comments (max 10 words, no markers), "
        "e.g. 'Which mob scares you the most?'.\n"
        "- title: up to 70 characters with a number or intrigue, honest to the content, no markers"
        + (", must not reveal the answer" if KIND == "quiz" else "") + ".\n"
        "- related: 2-4 exact Minecraft Wiki page titles of mobs, blocks, items or structures named in your "
        "lines (not the main subject), to show their pictures.\n"
        'Return JSON: {"title": "...", "headline": "...", "lines": ["...", "..."], "question": "...", '
        '"related": ["..."], "tags": ["5-8 tags"]}',
        lambda d: isinstance(d, dict) and d.get("headline") and len(d.get("lines", [])) >= 3)


def plain(t):
    return re.sub(r"\[/?[ry]\]", "", str(t)).strip()


def to_ass(t):
    t = str(t).replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")
    for tag, col in (("r", RED), ("y", YELLOW)):
        t = re.sub(rf"\[{tag}\](.*?)\[/{tag}\]", lambda m: f"{{\\c{col}}}{m.group(1)}{{\\c{WHITE}}}", t)
    return plain(t)


def all_lines(data):
    """Spoken lines; the viewer question lives in the description and comment, so the end can loop."""
    return [l for l in data["lines"] if plain(l)]


# ---- Shots: gameplay clips + wiki pictures -----------------------------------
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


class ClipPool:
    """Gives a different gameplay clip for every shot (cycles if there are few)."""
    def __init__(self, clips):
        self.clips, self.i = [c.resolve() for c in clips], 0

    def next(self):
        if not self.clips:
            return None
        c = self.clips[self.i % len(self.clips)]
        self.i += 1
        return c


def plan_shots(total, timed, a, b, pool):
    """Shots: freeze (frozen gameplay 'wait for it'), clip (gameplay), image (picture card over blurred gameplay),
    tease (blurred picture), pixel (quiz, sharper each shot), split (versus). The opening promise is paid off
    in the last shot: the frozen clip continues, the blurred/pixel picture becomes sharp."""
    shot = lambda kind, imgs, dur, **kw: {"kind": kind, "imgs": imgs, "dur": dur, "clip": pool.next(), **kw}
    if KIND == "versus" and a and b:
        head, tail = min(3.0, total * 0.18), min(3.0, total * 0.18)
        mid = total - head - tail
        n = max(2, round(mid / SHOT_SEC))
        side = lambda i: (a, b)[(i // 2) % 2]
        mids = [shot("image", [side(i)[(i // 4 + 1) % len(side(i))]], mid / n) if i % 2 else
                shot("clip", [], mid / n) for i in range(n)]
        return [shot("split", [a[0], b[0]], head)] + mids + [shot("split", [a[0], b[0]], tail)]
    pay = min(3.0, max(2.0, total * 0.18))
    if KIND == "quiz" and a:                           # pixelated mob gets sharper, answer in the last shot
        n = max(2, round((total - pay) / SHOT_SEC))
        return ([shot("pixel", [a[0]], (total - pay) / n, px=int(10 * 4 ** (i / max(1, n - 1)))) for i in range(n)]
                + [shot("image", [a[0]], pay)])
    if pool.clips:
        first = shot("freeze", [], TEASE_SEC)
        first["start"] = round(random.uniform(0, max(0, probe_duration(first["clip"]) - pay - 0.3)), 2)
        last = {**first, "kind": "clip", "dur": pay}   # same clip, continues from the frozen moment
        mid_imgs = a
    elif a:
        first, last, mid_imgs = shot("tease", [a[0]], TEASE_SEC), shot("image", [a[0]], pay), a[1:] or a
    else:
        raise SystemExit("No clips and no pictures")
    rest = total - TEASE_SEC - pay
    n = max(1, min(12, round(rest / SHOT_SEC)))
    out, k = [], 0
    for i in range(n):                                 # picture, clip, picture... while pictures last
        if mid_imgs and (not pool.clips or (i % 2 == 0 and k < len(mid_imgs))):
            out.append(shot("image", [mid_imgs[k % len(mid_imgs)]], rest / n))
            k += 1
        else:
            out.append(shot("clip", [], rest / n))
    return [first] + out + [last]


def render_shot(i, sh):
    fr = int(sh["dur"] * 30) + 2
    tail = f"setsar=1,format=yuv420p,pad=1080:1920:0:{TOP_BAR}:black[v]"
    fill = f"scale=1080:{MID_H}:force_original_aspect_ratio=increase,crop=1080:{MID_H},fps=30,setsar=1"
    if sh["kind"] == "freeze":                         # one frame of the clip, held with a slow push-in
        ff("-ss", str(sh["start"]), "-i", str(sh["clip"]), "-frames:v", "1", f"freeze{i}.png")
        inputs = ["-loop", "1", "-framerate", "30", "-i", f"freeze{i}.png"]
        fc = (f"[0:v]scale=2160:{MID_H * 2}:force_original_aspect_ratio=increase,crop=2160:{MID_H * 2},"
              f"eq=saturation=0.6:brightness=-0.08,zoompan=z='1+0.06*on/{fr}':x='iw/2-(iw/zoom/2)':"
              f"y='ih/2-(ih/zoom/2)':d={fr}:s=1080x{MID_H}:fps=30,")
    else:
        inputs, bg = [], "[0:v]"
        if sh["clip"] and sh["kind"] != "tease":
            start = sh.get("start")
            if start is None:
                start = round(random.uniform(0, max(0, probe_duration(sh["clip"]) - sh["dur"] - 0.2)), 2)
            inputs += ["-ss", str(start), "-stream_loop", "-1", "-i", str(sh["clip"])]
        else:
            inputs += ["-loop", "1", "-framerate", "30", "-i", str(sh["imgs"][0])]
        for p in sh["imgs"]:
            inputs += ["-loop", "1", "-framerate", "30", "-i", str(p)]
        if sh["kind"] == "clip":
            fc = f"{bg}{fill},eq=contrast=1.06:saturation=1.15,"
        elif sh["kind"] == "tease":                    # heavily blurred picture: what is it?
            fc = f"{bg}{fill},boxblur=40:4,eq=brightness=-0.2,"
        else:
            fc = f"{bg}{fill},boxblur=18:2,eq=brightness=-0.3:saturation=0.8[bg];"
            if sh["kind"] == "split":
                for k in range(2):
                    fc += (f"[{k + 1}:v]format=rgba,scale=500:640:force_original_aspect_ratio=decrease:"
                           f"flags={pix_flags(sh['imgs'][k])}[f{k}];")
                fc += ("[bg][f0]overlay=270-w/2:(H-h)/2:shortest=1[t];[t][f1]overlay=810-w/2:(H-h)/2:shortest=1,"
                       f"drawbox=x=537:y=0:w=6:h={MID_H}:color={ACCENT}@1:t=fill,")
            elif sh["kind"] == "pixel":
                px = sh.get("px", 24)
                fc += (f"[1:v]format=rgba,scale={px}:{px}:force_original_aspect_ratio=decrease,"
                       "scale=880:640:force_original_aspect_ratio=decrease:flags=neighbor[f];"
                       "[bg][f]overlay=(W-w)/2:(H-h)/2:shortest=1,")
            else:                                      # picture card with slow zoom
                z = f"1+0.10*on/{fr}" if i % 2 == 0 else f"1.10-0.10*on/{fr}"
                fc += (f"[1:v]format=rgba,scale=900:680:force_original_aspect_ratio=decrease:"
                       f"flags={pix_flags(sh['imgs'][0])}[f];[bg][f]overlay=(W-w)/2:(H-h)/2:shortest=1,"
                       f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x{MID_H}:fps=30,")
    ff(*inputs, "-filter_complex", fc + tail, "-map", "[v]", "-an", "-t", f"{sh['dur']:.3f}", *x264(20),
       f"shot{i}.mp4")
    return f"shot{i}.mp4"


def pix_flags(img):
    """Small pixel-art renders are enlarged without blur."""
    size = [int(x) for x in probe(img, "stream=width,height") if x.isdigit()]
    return "neighbor" if size and max(size) < 600 else "lanczos"


def build_background(shots):
    parts = [render_shot(i, s) for i, s in enumerate(shots)]
    (W / "bg.txt").write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
    ff("-f", "concat", "-safe", "0", "-i", "bg.txt", "-c", "copy", "bg.mp4")


# ---- Captions ----------------------------------------------------------------
def ass_time(sec):
    cs = int(round(max(0, sec) * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def line_times(data, words, total):
    body = all_lines(data)
    counts = [len(plain(l).split()) for l in body]
    c = head = len(plain(data["headline"]).split())
    starts = []
    for k in counts:
        if words and len(words) > head + 3:          # synced to the voice
            starts.append(words[min(c, len(words) - 1)][1])
        else:                                        # estimated by word count
            starts.append(0.4 + (total - 0.6) * c / (head + sum(counts)))
        c += k
    return list(zip(body, starts, starts[1:] + [total]))


def build_ass(data, timed, shots, total):
    sz = lambda s: int(s * FSCALE)
    style = lambda name, size, col, sp, out, sh, al, ml, mr, mv, back="&H00000000&": (
        f"Style: {name},{FAMILY},{sz(size)},{col},{col},&H00000000&,{back},-1,0,0,0,100,100,{sp},0,1,{out},{sh},"
        f"{al},{ml},{mr},{mv},1")
    ev = lambda layer, s, e, st, txt: f"Dialogue: {layer},{ass_time(s)},{ass_time(e)},{st},,0,0,0,,{txt}"
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding",
             style("Label", 40, ACC_ASS, 6, 0, 0, 8, 60, 60, 125),
             style("Head", 76, WHITE, 1, 0, 0, 8, 60, 60, 185),
             style("Body", 58, WHITE, 0, 0, 0, 8, 170, 170, BOT_Y + 35),   # centered, above channel name, clear of buttons
             style("Big", 150, WHITE, 4, 9, 4, 5, 0, 0, 0, "&H64000000&"),
             style("Wait", 96, ACC_ASS, 6, 8, 4, 5, 0, 0, 0, "&H64000000&"),
             style("Brand", 30, "&H50FFFFFF&", 4, 3, 0, 7, 28, 0, TOP_BAR + 18),
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
             ev(0, 0, total, "Label", f"{LABEL}  ·  #{FILE_NO}"),
             ev(3, 0, total, "Brand", CHANNEL),
             ev(0, 0, total, "Head", "{\\fad(200,0)}" + to_ass(data["headline"]))]
    lines += [ev(1, s, e, "Body", "{\\fad(120,60)}" + to_ass(text)) for text, s, e in timed]
    t, cy = 0.0, TOP_BAR + MID_H // 2
    for sh in shots:
        if sh["kind"] == "split":
            lines.append(ev(2, t, t + sh["dur"], "Big",
                            f"{{\\pos(540,{cy})\\c{ACC_ASS}\\fscx40\\fscy40\\t(0,180,\\fscx100\\fscy100)}}VS"))
        elif sh["kind"] == "pixel":
            lines.append(ev(2, t, t + sh["dur"], "Big",
                            f"{{\\pos(540,{cy})\\c{ACC_ASS}\\t(0,{int(sh['dur'] * 1000)},\\fscx125\\fscy125)}}?"))
        elif sh["kind"] in ("freeze", "tease"):
            lines.append(ev(2, t, t + sh["dur"], "Wait",
                            f"{{\\pos(540,{cy})\\fscx70\\fscy70\\t(0,200,\\fscx100\\fscy100)"
                            f"\\t(600,1300,\\alpha&H40&)}}WAIT FOR IT"))
        t += sh["dur"]
    path = W / "subs.ass"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---- Audio -------------------------------------------------------------------
async def _tts(text, path):
    import edge_tts
    try:
        comm = edge_tts.Communicate(text, VOICE, rate="+8%", boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, VOICE, rate="+8%")
    words = []
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append((chunk["text"], chunk["offset"] / 1e7))
    return words


def tighten(path, words):
    """Cuts every pause longer than 0.25 s down to ~0.12 s and shifts the word timings to match."""
    log = subprocess.run(["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-40dB:d=0.25", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    cuts = [(a + 0.06, b - 0.06) for a, b in zip(starts, ends) if b - a > 0.25]
    if not cuts:
        return words
    keep, prev = [], 0.0
    for a, b in cuts:
        keep.append(f"between(t,{prev:.3f},{a:.3f})")
        prev = b
    keep.append(f"gte(t,{prev:.3f})")
    tmp = path.with_name("voice_tight.mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-af",
                    f"aselect='{'+'.join(keep)}',asetpts=N/SR/TB", str(tmp)], check=True)
    tmp.replace(path)
    shift = lambda t: t - sum(b - a for a, b in cuts if b <= t + 0.001)
    return [(w[0], max(0.0, shift(w[1])), *w[2:]) for w in words]


def build_sfx(total, timed, cuts, shots, path):
    """Synthesized sounds. Discrete effects never closer than 1 s (the most important wins); under them a low
    tension bed that keeps rising and cuts off on the last frame, so the loop restarts the tension."""
    sr = 22050
    buf = [0.0] * int(sr * (total + 0.05))
    rnd = random.Random(len(timed) * 31 + int(total * 10))

    def add(start, samples, gain=1.0):
        i0 = int(start * sr)
        for k, v in enumerate(samples[:max(0, len(buf) - i0)]):
            buf[i0 + k] += v * gain

    def noise(n, alpha):                              # low-passed noise
        y, out = 0.0, []
        for _ in range(n):
            y += alpha * (rnd.uniform(-1, 1) - y)
            out.append(y)
        return out

    def tap():
        n = int(0.05 * sr)
        nz = noise(n, 0.18)
        return [nz[k] * math.exp(-k / (0.008 * sr)) * 0.9 +
                0.25 * math.sin(2 * math.pi * 180 * k / sr) * math.exp(-k / (0.012 * sr)) for k in range(n)]

    def whoosh(d=0.5):
        n, y, out = int(d * sr), 0.0, []
        for k in range(n):
            p = k / n
            y += (0.03 + 0.2 * math.sin(math.pi * p)) * (rnd.uniform(-1, 1) - y)
            out.append(y * math.sin(math.pi * p) ** 2)
        return out

    def boom(f0=55, d=1.4, nmix=0.3):
        n, ph, out = int(d * sr), 0.0, []
        nz = noise(n, 0.05)
        for k in range(n):
            t = k / sr
            ph += 2 * math.pi * f0 * (1 - 0.35 * t / d) / sr
            out.append((math.sin(ph) + nmix * nz[k] * 3) * math.exp(-t / (d * 0.3)))
        return out

    def blip(f, d=0.09):
        n = int(d * sr)
        return [0.35 * math.copysign(1, math.sin(2 * math.pi * f * k / sr)) * math.exp(-k / (0.03 * sr))
                for k in range(n)]

    def funny():                                       # a silly sound that does not match the picture
        kind, n, ph, out = rnd.choice(["boing", "fart", "slide"]), int(0.6 * sr), 0.0, []
        for k in range(n):
            t = k / sr
            if kind == "boing":
                f = 180 + 120 * math.sin(2 * math.pi * 9 * t) * math.exp(-t * 4)
                v = math.sin(ph) * math.exp(-t * 4)
            elif kind == "fart":
                f = 70 + 25 * rnd.uniform(-1, 1) - 30 * t
                v = (2 * (ph / (2 * math.pi) % 1) - 1) * min(1, t * 30) * math.exp(-t * 3)
            else:
                f = 400 + 900 * t
                v = math.sin(ph) * min(1, t * 20, (0.6 - t) * 10)
            ph += 2 * math.pi * f / sr
            out.append(0.8 * v)
        return out

    events = []                                        # (time, priority, samples, gain)
    events.append((0.0, 9, boom(60, 1.0, 0.3), 0.8))
    for idx, (text, s, _) in enumerate(timed):
        events.append((s, 5 if "[r]" in text else 2, boom(80, 0.5, 0.15) if "[r]" in text else tap(),
                       0.3 if "[r]" in text else 0.55))
    for t in cuts:
        events.append((max(0, t - 0.2), 3, whoosh(0.45), 0.45))
    t = 0.0
    for sh in shots:
        if sh["kind"] == "split":
            events.append((t + 0.05, 7, boom(60, 0.9, 0.35), 0.6))
        t += sh["dur"]
    events.append((max(0, total - shots[-1]["dur"]), 8, boom(70, 0.8, 0.3), 0.7))   # payoff hit
    if CATEGORY == "secrets":
        events.append((0.5, 6, sum((blip(f) + [0.0] * int(0.02 * sr) for f in (523, 659, 784, 1046)), []), 0.25))
    if KIND != "quiz" and CATEGORY in ("myth", "versus") and len(timed) > 2 and rnd.random() < FUNNY:
        events.append((timed[1][1] + 0.3, 8, funny(), 0.6))
    taken = []
    for t, prio, smp, g in sorted(events, key=lambda e: -e[1]):
        if all(abs(t - u) >= 1.0 for u in taken):      # max 1 effect per second
            taken.append(t)
            add(t, smp, g)
    bed = noise(len(buf), 0.006)                       # rising tension bed + slow low drone, hard cut at end
    ph = 0.0
    for k in range(len(buf)):
        p = k / len(buf)
        ph += 2 * math.pi * (42 + 14 * p) / sr
        buf[k] += (bed[k] * 1.8 + 0.12 * math.sin(ph)) * (0.25 + 1.6 * p * p) * min(1, k / (0.3 * sr))
    peak = max(1e-6, max(abs(v) for v in buf))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(array.array("h", (int(v / peak * 26000) for v in buf)).tobytes())


def render(ass_path, total):
    args = ["-i", "bg.mp4"]
    fc = [f"[0:v]drawbox=x=0:y={TOP_BAR - 6}:w=1080:h=6:color={ACCENT}@1:t=fill,drawbox=x=0:y={BOT_Y}:w=1080:"
          f"h=6:color={ACCENT}@1:t=fill,ass={ass_path.resolve()}:fontsdir={FONTS_DIR}[v]"]
    srcs = []                                        # (input args, filter) for each audio layer
    if NARRATION:
        srcs.append((["-i", "voice.mp3"], "highpass=f=90,lowpass=f=8500,deesser=i=0.6"))
    if MUSIC.exists():
        srcs.append((["-ss", str(MUSIC_START), "-stream_loop", "-1", "-i", str(MUSIC.resolve())],
                     f"volume={MUSIC_VOL},afade=t=in:d=0.3"))
    if SFX and (W / "sfx.wav").exists():
        srcs.append((["-i", "sfx.wav"], f"lowpass=f=6000,volume={0.12 * SFX_VOL:.3f}"))
    if not srcs:
        srcs.append((["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"], "anull"))
    for k, (a, f) in enumerate(srcs, 1):
        args += a
        fc.append(f"[{k}:a]aresample=44100,{f}[a{k}]")
    fc.append("".join(f"[a{k}]" for k in range(1, len(srcs) + 1))
              + f"amix=inputs={len(srcs)}:duration=longest:normalize=0[a]")
    ff(*args, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", *x264(21),
       "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", "final.mp4")


def make_video(data, topic):
    lines, words = all_lines(data), []
    spoken = plain(data["headline"]) + ". " + " ".join(plain(l) for l in lines)
    total = min(MAX_SEC, max(MIN_SEC, 1 + len(spoken.split()) * 0.38))
    if NARRATION:
        words = tighten(W / "voice.mp3", asyncio.run(_tts(spoken, W / "voice.mp3")))
        total = min(MAX_SEC, max(MIN_SEC, probe_duration(W / "voice.mp3") + 0.15))   # no tail: seamless loop
    arts, related = topic["articles"], data.get("related", [])
    if len(arts) > 1:                                # versus: related pictures split between sides
        imgs_a = get_images(arts[0], topic["images"][0], related[0::2], "a", 4)
        imgs_b = get_images(arts[1], topic["images"][1], related[1::2], "b", 4)
    else:
        imgs_a, imgs_b = get_images(arts[0], topic["images"][0], related, "a", MAX_IMAGES), []
    pool = ClipPool(CLIPS)
    if not pool.clips and not imgs_a:
        fb = get_fallback_clip()
        if not fb:
            raise SystemExit("No clips and no pictures")
        pool = ClipPool([fb])
    print("clips:", len(pool.clips), "| pictures:", len(imgs_a) + len(imgs_b))
    timed = line_times(data, words, total)
    shots = plan_shots(total, timed, imgs_a, imgs_b, pool)
    build_background(shots)
    if SFX:
        cuts = [sum(s["dur"] for s in shots[:k + 1]) for k in range(len(shots) - 1)]
        build_sfx(total, timed, cuts, shots, W / "sfx.wav")
    render(build_ass(data, timed, shots, total), total)
    return bool(imgs_a or imgs_b)


# ---- YouTube: upload, localization, playlist, comment ------------------------
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


def upload(yt, data, topic, has_images):
    from googleapiclient.http import MediaFileUpload
    title, q = plain(data["title"]), plain(data.get("question", ""))
    tags = list(dict.fromkeys(["Minecraft", "Shorts"] + CAT_TAGS + data.get("tags", [])))
    story = plain(data["headline"]) + "\n" + "\n".join(plain(l) for l in data["lines"])
    sources = "; ".join(f'"{a}"' for a in topic["articles"])
    tail = ("\n\n#Shorts #Minecraft " + " ".join("#" + t.replace(" ", "") for t in tags[2:7])
            + (f"\n\nFacts: Minecraft Wiki (minecraft.wiki), {sources}." if topic["text"] else "")
            + ("\nGame images: Minecraft Wiki, Minecraft © Mojang." if has_images else "")
            + "\n\n" + DISCLAIMER)
    body = {"snippet": {"title": f"{title[:85]} #Shorts", "tags": tags[:15], "categoryId": "20",
                        "description": ((f"{q} Tell me in the comments!\n\n" if q else "") + story + tail)[:4900],
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": env("PRIVACY", "public"), "selfDeclaredMadeForKids": False}}
    loc = {l: {"title": f"{plain(t['title'])[:85]} #Shorts",
               "description": ((t["question"] + "\n\n" if t.get("question") else "") + t.get("story", "") + tail)[:4900]}
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


def add_to_playlist(yt, vid):
    try:
        items = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
        pl = next((i["id"] for i in items if i["snippet"]["title"] == PLAYLIST), None) or \
            yt.playlists().insert(part="snippet,status", body={
                "snippet": {"title": PLAYLIST, "description": f"{PLAYLIST}: Minecraft Shorts."},
                "status": {"privacyStatus": "public"}}).execute()["id"]
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
        print("playlist:", PLAYLIST)
    except Exception as e:
        print("playlist skipped (re-run auth.py with new scopes):", str(e)[:200])


def post_comment(yt, vid, data):
    q = plain(data.get("question", ""))
    try:
        if q:
            yt.commentThreads().insert(part="snippet", body={"snippet": {"videoId": vid, "topLevelComment": {
                "snippet": {"textOriginal": f"{q} 👇"}}}}).execute()
            print("comment posted")
    except Exception as e:
        print("comment skipped (re-run auth.py with new scopes):", str(e)[:200])


if __name__ == "__main__":
    topic = pick_topic()
    print("category:", CATEGORY, "| topic:", topic["key"], "| wiki:", WIKI_OK)
    data = gen_script(topic)
    print(json.dumps(data, ensure_ascii=False))
    has_images = make_video(data, topic)
    yt = youtube()
    vid = upload(yt, data, topic, has_images)
    used.append(topic["key"])
    USED.write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
    add_to_playlist(yt, vid)
    post_comment(yt, vid, data)
