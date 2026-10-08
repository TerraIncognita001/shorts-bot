import array, asyncio, bisect, html, json, math, os, pathlib, random, re, subprocess, time, wave
import requests

W = pathlib.Path("work")
W.mkdir(exist_ok=True)
USED = pathlib.Path("used.json")
env = os.getenv


def load_used():
    """used.json is a list; old entries are plain strings, new ones are dicts (key, fmt, hook, vid, ts ...)."""
    try:
        raw = json.loads(USED.read_text(encoding="utf-8")) if USED.exists() else []
    except ValueError:
        raw = []
    return [e if isinstance(e, dict) else {"key": str(e)} for e in raw]


used = load_used()


def titles_of(e):
    return e.get("arts") or str(e.get("key", "")).split(" vs ")


def recent_titles(n):
    return {t for e in used[-n:] for t in titles_of(e)}


# ---- Formats: each one is a different STRUCTURE (hook, pacing, visuals), not just a topic bucket ----------------
#  kind: top (countdown) | versus | quiz | single.  The bot learns which formats get views (see learn()).
FORMATS = {
    "top": dict(
        label="TOP 5", accent="0xFFB300", kind="top", items=5, playlist="Minecraft Top Lists",
        pool="a fun theme for a countdown of 5 Minecraft mobs, blocks, items or structures players know "
             "(e.g. 'mobs that are secretly dangerous', 'most useless items', 'blocks you should never break')",
        tags=["Minecraft top 5", "Minecraft ranking"],
        cats=["Category:Mobs", "Category:Blocks", "Category:Items", "Category:Generated structures"]),
    "secrets": dict(
        label="SECRETS", accent="0xBA68C8", kind="single", playlist="Minecraft Secrets",
        pool="a hidden, misunderstood or removed side of a famous Minecraft mob, block, item, structure or mechanic "
             "(secret behavior, hidden numbers, removed or unused features, lore)",
        tags=["Minecraft secrets", "Minecraft facts"],
        cats=["Category:Game mechanics", "Category:Removed features", "Category:Unused features", "Category:Mobs",
              "Category:Blocks", "Category:Redstone"]),
    "myth": dict(
        label="MYTH vs TRUTH", accent="0xFF7043", kind="single", playlist="Minecraft Myth vs Truth",
        pool="a famous Minecraft subject surrounded by a popular myth or misconception among players",
        tags=["Minecraft myths", "Minecraft myth busting"],
        cats=["Category:Mobs", "Category:Blocks", "Category:Items", "Category:Game mechanics"]),
    "versus": dict(
        label="VERSUS", accent="0x29B6F6", kind="versus", playlist="Minecraft Versus",
        pool="two famous Minecraft rivals of the same type that players love to compare (mob vs mob, weapon vs "
             "weapon, armor vs armor, block vs block)",
        tags=["Minecraft versus", "Minecraft who wins"],
        cats=["Category:Mobs", "Category:Weapons", "Category:Armor", "Category:Tools"]),
    "quiz": dict(
        label="GUESS IT", accent="0x9CCC65", kind="quiz", playlist="Minecraft Guess It",
        pool="a visually recognizable Minecraft mob that players should be able to guess from clues",
        tags=["Minecraft quiz", "guess the mob"],
        cats=["Category:Mobs", "Category:Hostile mobs", "Category:Passive mobs"]),
    "pov": dict(
        label="MOB TALK", accent="0x26C6DA", kind="single", playlist="Minecraft Mob Talk",
        pool="a famous Minecraft mob that has a funny, tragic or dramatic 'personality' based on its real behavior",
        tags=["Minecraft POV", "Minecraft funny"],
        cats=["Category:Mobs", "Category:Hostile mobs", "Category:Passive mobs", "Category:Neutral mobs"]),
    "news": dict(
        label="NEWS", accent="0xFF1744", kind="single", playlist="Minecraft News",
        pool="the newest Minecraft update", tags=["Minecraft update", "Minecraft news"], cats=[]),
}

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
]

# ---- Settings (override in the workflow env) ----------------------------------
CLIPS = sorted(pathlib.Path("clips").glob("*.mp4"))     # your own gameplay clips
random.shuffle(CLIPS)
IMAGES = env("IMAGES", "1") == "1"                      # 0 = clips only, no wiki pictures
MUSIC_DIR = pathlib.Path("music")                       # your tracks: music/<format>/*.mp3 or music/*.mp3
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
MUSIC_ALIAS = {"top": ["facts"], "secrets": ["lore", "secrets"], "pov": ["mobs"], "news": ["facts"]}   # old folders still work
MUSIC_START = env("MUSIC_START", "auto")                # seconds, or "auto" = varies per video
CHANNEL = env("CHANNEL", "TERRA INCOGNITA")             # small brand in every frame
FILE_NO = len(used) + 1                                 # running number shown on screen
NARRATION = env("NARRATION", "1") == "1"
VOICE = env("VOICE", "en-US-GuyNeural")
RATE = env("RATE", "+12%")                              # faster speech = more energy
MUSIC_VOL = float(env("MUSIC_VOL", "0.08" if NARRATION else "0.18"))
SFX = env("SFX", "1") == "1"
SFX_VOL = float(env("SFX_VOL", "1.0"))                  # 0.5 = quieter, 2 = louder
LANGS = [l.strip() for l in env("LANGS", "es,pt,de,fr,it,pl,uk,tr").split(",") if l.strip()]
MISTAKE = env("MISTAKE", "0") == "1"                    # off: deliberate errors cost trust (set 1 to bring back)
PROGRESS = env("PROGRESS", "1") == "1"                  # thin progress bar: viewers see the end coming
LEARN = env("LEARN", "1") == "1"                        # pick formats/hooks by real view counts
MIN_SEC, MAX_SEC = 12.0, 32.0
COOLDOWN = int(env("COOLDOWN", "45"))                   # a famous subject can come back after this many videos
UA = {"User-Agent": "TerraIncognitaShortsBot/1.0 (https://terraincognita001.github.io/terra-incognita/)"}
MCWIKI = "https://minecraft.wiki/api.php"
DISCLAIMER = "Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft."

# ---- Layout 1080x1920 (YouTube Shorts UI covers: top ~8%, right ~15%, bottom ~22%) ----
HOOK_Y = 345                       # centre of the hook text (two lines)
CARD_X, CARD_CY = 500, 860         # centre of the picture card (left of the like/comment buttons)
SPLIT_X = (240, 690)               # versus: centres of the two cards
WIN_Y, WIN_H = 350, 920            # sharp gameplay window (a blurred copy fills the rest)
CAP_Y = 1345                       # centre of the word-by-word captions (one line)
BRAND_Y, BAR_Y = 1432, 1468        # brand and progress bar sit just above the bottom UI zone
WHITE, RED, YELLOW = "&H00FFFFFF&", "&H003539E5&", "&H0000D7FF&"   # ASS colours (BGR)
FONT = pathlib.Path("font.ttf")                         # optional own font; default: Anton (downloaded once)
ANTON_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf"
SKIP_IMG = re.compile(r"sprite|invicon|icon|logo|grid|slot|button|achievement|advancement|recipe|crafting|"
                      r"atlas|gui|hud|pattern|banner|edition|screenshot of|\.gif$|\.svg$", re.I)

FORMAT = F = LABEL = ACCENT = ACC_ASS = KIND = PLAYLIST = None


def use_format(name):
    global FORMAT, F, LABEL, ACCENT, ACC_ASS, KIND, PLAYLIST
    FORMAT, F = name, FORMATS[name]
    LABEL, ACCENT, KIND, PLAYLIST = F["label"], F["accent"], F["kind"], F["playlist"]
    ACC_ASS = "&H00" + ACCENT[6:8] + ACCENT[4:6] + ACCENT[2:4] + "&"


use_format(env("CATEGORY") or env("FORMAT") or "secrets")


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


def setup_font():
    """(fonts dir, family): your font.ttf, else Anton (bold condensed, the classic Shorts caption font) downloaded
    once from GitHub, else DejaVu."""
    path = FONT if FONT.exists() else None
    if not path:
        p = W / "fonts" / "Anton-Regular.ttf"
        if not p.exists():
            try:
                if env("FONT_DOWNLOAD", "1") != "1":
                    raise RuntimeError("download disabled")
                p.parent.mkdir(exist_ok=True)
                r = requests.get(ANTON_URL, timeout=60)
                r.raise_for_status()
                p.write_bytes(r.content)
            except Exception as e:
                print("font download failed, using DejaVu:", type(e).__name__)
        path = p if p.exists() and p.stat().st_size > 10000 else None
    if path:
        try:
            out = subprocess.run(["fc-scan", "--format", "%{family}", str(path)],
                                 capture_output=True, text=True, check=True).stdout
            if out.split(",")[0].strip():
                return str(path.resolve().parent), out.split(",")[0].strip()
        except Exception:
            pass
    return "/usr/share/fonts/truetype/dejavu", "DejaVu Sans"


FONTS_DIR, FAMILY = setup_font()
FAM = FAMILY.lower()
ANTON = "anton" in FAM
CHAR_W = 0.31 if ANTON else 1.0 if "press start" in FAM else 0.66      # glyph width per letter / ASS font size
TOP_CAP, TOP_HOOK = (190, 165) if ANTON else (118, 108)                # biggest caption / hook font sizes
BOLD = 0 if ANTON else -1


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
    p = W / name
    try:
        r = requests.get(url, headers=UA, timeout=120)
        r.raise_for_status()
        p.write_bytes(r.content)
        if not probe(p, "stream=width"):
            raise ValueError("not an image")
        png = p.with_name(p.stem + "_ok.png")                  # must really decode (some webp files do not)
        t = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p), "-frames:v", "1", str(png)],
                           capture_output=True)
        if t.returncode != 0 or not png.exists():
            try:
                from PIL import Image
                Image.open(p).convert("RGBA").save(png)
            except Exception:
                raise ValueError("cannot decode image")
        p.unlink(missing_ok=True)
        return png.resolve()
    except Exception as e:
        print("skip image:", e)


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


# ---- Topic: famous subject + fresh angle (or the newest update) ----------------------
CATS_ALL = lambda: F.get("cats", [])


def candidates(limit=12):
    """Random wiki subjects from the format's categories (and a few subcategories), not used yet."""
    def members(cat):
        try:
            return wiki_get(action="query", list="categorymembers", cmtitle=cat, cmtype="page|subcat",
                            cmlimit=500).get("query", {}).get("categorymembers", [])
        except Exception:
            return []
    titles = set()
    for cat in CATS_ALL():
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


def pick_topic():
    if FORMAT == "news":
        t = news_topic()
        if t:
            return t
        print("no fresh update found, switching to secrets")
        use_format("secrets")
    n = 2 if KIND == "versus" else F.get("items", 5) if KIND == "top" else 1
    heads = [e["hl"] for e in used[-30:] if e.get("hl")]
    wiki_c, pop = candidates(40), popular(40)
    if KIND == "versus":
        fmt = ('{"article": "exact page title of the first", "article2": "exact page title of the second", '
               '"angle": "one sentence: why this matchup is interesting"}')
    elif KIND == "top":
        fmt = (f'{{"theme": "the countdown theme in a few words", "articles": ["{n} exact page titles, '
               f'5th place first, 1st place last"], "angle": "one sentence"}}')
    else:
        fmt = '{"article": "exact page title", "angle": "one sentence: the specific surprising angle"}'
    for _ in range(5):
        cands = random.sample(pop, min(len(pop), 24 if KIND == "top" else 16)) + \
            random.sample(wiki_c, min(len(wiki_c), 10))
        random.shuffle(cands)
        ask = (f"Pick the subject of ONE viral Minecraft YouTube Short. Format: {F['pool']}. "
               "Pick subjects EVERY player recognizes at first sight (famous mobs, blocks, items, bosses, "
               "structures): viewers only stop for things they know. Freshness must come from the ANGLE (a "
               "surprising, specific, little-known side of it), never from obscurity. ")
        if len(cands) >= 8:
            ask += f"Choose ONLY from this list of exact Minecraft Wiki page titles: {cands}. "
        if heads:
            ask += f"Do NOT repeat the angle of these recent videos: {heads}. "
        ask += f"Return JSON: {fmt}"
        keys = ["article", "article2"][:n] if KIND != "top" else ["articles"]
        t = gemini(ask, lambda d: isinstance(d, dict) and all(d.get(k) for k in keys)
                    and (KIND != "top" or len(d["articles"]) >= n))
        want = [str(t[k]) for k in keys] if KIND != "top" else [str(x) for x in t["articles"]][:n]
        angle = str(t.get("angle") or t.get("theme") or "")
        if len(set(want)) != n:
            continue
        if not WIKI_OK:                               # wiki blocked: script from Gemini's own knowledge
            return {"articles": want, "key": " vs ".join(want), "text": None, "images": [[]] * n, "angle": angle,
                    "theme": t.get("theme", "")}
        got = [wiki_page(w, cap=6500 if KIND == "versus" else 2400 if KIND == "top" else 12000) for w in want]
        arts = [a for a, _, _ in got]
        need = 500 if KIND == "top" else 1200
        if WIKI_OK and all(arts) and len(set(arts)) == n and all(len(x) > need for _, x, _ in got):
            if KIND == "single":
                text = got[0][1]
            else:
                text = "\n\n".join(f"=== {a} ===\n{x}" for a, x, _ in got)
            key = " vs ".join(arts) if KIND != "top" else "Top: " + ", ".join(arts)
            return {"articles": arts, "key": key, "text": text, "images": [g[2] for g in got], "angle": angle,
                    "theme": t.get("theme", "")}
        if not WIKI_OK:
            continue
        print("topic rejected:", want)
    raise SystemExit("No usable topic")


# ---- Script: hook system ------------------------------------------------------
HOOK_TYPES = {   # style templates, never facts
    "warning": "STAKES: a danger or a costly mistake. Template: 'Never [do X] near [familiar thing].'",
    "contradiction": "CONTRADICTION: a famous thing that is NOT what players think. Template: '[Familiar thing] "
                     "isn't what you think.'",
    "superlative": "EXTREME: the rarest / deadliest / most useless thing. Template: 'The [most X] [thing] in "
                   "Minecraft.'",
    "insider": "INSIDER SECRET: something Mojang hid or players missed. Template: 'Mojang hid [something] in "
               "[familiar thing].'",
    "you": "YOU: you have been doing it wrong. Template: 'You've been [doing X] wrong.'",
    "number": "SHOCK NUMBER: open with a surprising number plus a familiar thing. Template: '[Number] [unit] — "
              "and [familiar thing] still [odd verb].'",
    "scenario": "WHAT-IF: a vivid, specific scenario. Template: 'What happens if you [specific action]?'",
}
HOOKED = ("secrets", "myth", "pov")          # formats whose hook type is chosen (and learned); the rest have their own

STRUCT = {
    "secrets": (
        "FORMAT = SECRET: something most players never notice, get wrong, or that Mojang hid or removed.\n"
        "- 6-8 lines, 3-9 words each. Line 1 re-hooks inside 3 seconds by raising the stakes (what the player "
        "loses or gains) with a concrete detail. Then 3-4 reveal beats, each a NEW concrete fact (a number, a name, "
        "a condition). The second-to-last line is the PEAK: the single most shocking fact. At least one line is "
        "something the player can try tonight.\n"
        "- About a third and two thirds in, one line opens a new mini-question that the next line answers.\n"),
    "myth": (
        "FORMAT = MYTH vs TRUTH: bust a belief most players hold.\n"
        "- Line 1 starts 'Myth:' (tag \"myth\"): the belief in a player's own words. Line 2 starts 'Truth:' (tag "
        "\"truth\"): the correction with an exact number or condition from the text. Then 2-3 lines of proof or "
        "consequences, each a new fact; the second-to-last line is the PEAK.\n"
        "- Only use a myth that the text itself contradicts.\n"),
    "versus": (
        "FORMAT = VERSUS, who wins? Compare the two on 3 rounds with exact numbers from the text (health, damage, "
        "speed, durability...).\n"
        "- Headline: 'X vs Y' plus a hook; it never reveals the winner.\n"
        "- Lines: for each round one line per side with its number ('Warden: 500 health.'), alternating sides; "
        "every such line has show = that side's exact page title. Then ONE verdict line (tag \"winner\", show = the "
        "winner's page title) that names the winner and why in one sentence: it is the PEAK.\n"),
    "quiz": (
        "FORMAT = GUESS IT: the viewer must name the mob before the reveal.\n"
        "- Headline = a challenge that dares the viewer ('99% of players can't name this mob').\n"
        "- 3-4 clue lines from vague to obvious (behavior, sound, drops, where it spawns) WITHOUT the name; the "
        "last clue is the strongest.\n"
        "- The LAST line is the reveal: \"It's the [r]Name[/r]!\" Nothing earlier may contain the name. "
        "There is NO loop line in this format.\n"),
    "pov": (
        "FORMAT = MOB TALK: the subject speaks in FIRST PERSON as a funny, dramatic character (sarcastic, "
        "self-pitying or arrogant) who complains or brags.\n"
        "- Headline: 'POV: you're a [subject]' or a first-person hook.\n"
        "- 6-8 short lines; EVERY line carries one TRUE fact from the text (numbers, drops, behavior, spawn "
        "rules) wrapped in attitude. The humor comes from the personality, never from false facts. The peak is the "
        "most absurd true detail.\n"),
    "news": (
        "FORMAT = BREAKING UPDATE ({version}): pick the single most exciting player-facing NEW addition or change "
        "in the changelog (a mob, block, biome, item, mechanic), never engine or technical changes. If nothing "
        "player-facing exists return {{\"skip\": true}}.\n"
        "- Headline: 'Minecraft just added [X]' style.\n"
        "- 6-8 lines: what it is, what it does with exact numbers from the text, what players will try first, one "
        "consequence; mention the version once.\n"),
    "top": (
        "FORMAT = COUNTDOWN of {n}. Theme: {theme}.\n"
        "- Headline: '{n} Minecraft [things] that [hook]' (max 8 words).\n"
        "- Exactly {n} item lines counting down from {n} to 1, the most shocking item last. Each starts with the "
        "spoken number word ('Five.', 'Four.' ...), then the item and ONE killer fact with a number, max 13 words. "
        "Set n = its number and show = its exact page title (from: {titles}).\n"),
}


def gen_script(topic):
    arts = topic["articles"]
    subject = " vs ".join(arts) if KIND != "top" else ", ".join(arts)
    source = (f"Use ONLY facts stated in this Minecraft Wiki text; keep names and numbers exact:\n<<<\n"
              f"{topic['text']}\n>>>\n" if topic["text"] else
              "Use only accurate, well-established facts about the current Java Edition; leave out anything you are "
              "not sure about.\n")
    hook = ""
    if FORMAT in HOOKED:
        hook = f"- Hook type for this video: {HOOK_TYPES[topic['hook']]}\n"
    struct = STRUCT[FORMAT].format(version=topic.get("version", ""), n=F.get("items", 5),
                                   theme=topic.get("theme", ""), titles=arts)
    loop = ("" if KIND == "quiz" else
            "- The LAST line is an unfinished sentence with no final punctuation that flows straight into the "
            "headline when the video loops (e.g. last line 'And that is exactly why' + headline 'Creepers run from "
            "cats').\n")
    extra = ""
    if FORMAT in ("secrets", "myth", "news"):
        extra += ("- Exactly one line is a bold but defensible opinion fans will argue about, starting with "
                  "'Honestly,' or 'Hot take:'.\n")
    if MISTAKE:
        extra += ("- Exactly one other line contains ONE small harmless mistake in a minor number so fans correct "
                  "it in the comments (never in the headline, never about safety); put that line in \"mistake\".\n")
    recent = [e["hl"] for e in used[-25:] if e.get("hl")]
    prompt = (
        "You write 15-25 second YouTube Shorts for a Minecraft channel. Viewers decide in ONE second whether to "
        "swipe, so every word must earn the next one.\n"
        "VOICE: a funny, energetic friend who plays Minecraft every day, speaking to ONE viewer. Say 'you'. "
        "Contractions. Short punchy sentences. No greetings, no 'welcome', no 'in this video', never start with "
        "'Did you know' or 'In Minecraft'.\n"
        f'SUBJECT: "{subject}". Angle: {topic.get("angle", "")}\n{source}{struct}'
        "HEADLINE (the hook): spoken first and shown from the first frame.\n"
        f"{hook}"
        "- Max 7 words. Its first 3 words are something every player instantly recognizes (a mob, block, item) or a "
        "number. It opens a question the viewer NEEDS answered and NEVER reveals the answer, twist or payoff.\n"
        "RULES:\n"
        "- Zero filler: every line holds one NEW concrete detail (number, name, condition). Spoken text 45-65 words "
        "in total (countdown: 65-80).\n"
        f"{extra}{loop}"
        "- show: for each line, the exact Minecraft Wiki page title of the mob/block/item/structure THAT LINE talks "
        "about (it is pictured on screen while the line is spoken); \"\" when the line has no clear visual. Never "
        "invent titles.\n"
        "- In headline and lines wrap the single most important word or number in [r]...[/r] and at most one more "
        "in [y]...[/y].\n"
        "- question: one short comment-bait question that asks for a CHOICE or an opinion (not yes/no), max 10 "
        "words, e.g. 'Which mob is the most annoying?'\n"
        "- title: up to 60 characters, the subject's name first, curiosity, honest to the content, no markers"
        + (", must not reveal the answer" if KIND == "quiz" else "") + ".\n"
        "- related: 0-3 more exact page titles worth showing.\n"
        + (f"- Do not reuse the hooks of these recent videos: {recent}\n" if recent else "")
        + 'Return JSON: {"title": "...", "headline": "...", "lines": [{"t": "spoken line", "show": "page title or '
          'empty", "tag": "myth|truth|winner or empty", "n": 0}], "question": "...", "related": ["..."], '
          '"tags": ["5-8 tags"]' + (', "mistake": "..."' if MISTAKE else "") + "}")
    min_lines = F.get("items", 5) + 1 if KIND == "top" else 4
    data = gemini(prompt, lambda d: isinstance(d, dict) and (d.get("skip") or (
        d.get("headline") and isinstance(d.get("lines"), list) and len(d["lines"]) >= min_lines)))
    if data.get("skip"):
        return None
    return normalize(data)


def normalize(data):
    """Lines become dicts {t, show, tag, n}; empty lines are dropped."""
    out = []
    for l in data.get("lines", []):
        if isinstance(l, str):
            l = {"t": l}
        if isinstance(l, dict) and plain(l.get("t", "")):
            n = l.get("n")
            out.append({"t": str(l["t"]).strip(), "show": str(l.get("show") or "").strip(),
                        "tag": str(l.get("tag") or "").strip().lower(),
                        "n": int(n) if isinstance(n, (int, float)) and n else 0})
    data["lines"] = out
    data["headline"] = str(data["headline"]).strip()
    data["title"] = str(data.get("title") or plain(data["headline"])).strip()
    data["question"] = str(data.get("question") or "").strip()
    return data


def plain(t):
    return re.sub(r"\[/?[ry]\]", "", str(t)).strip()


def all_lines(data):
    return [l for l in data["lines"] if plain(l["t"])]


# ---- Timing: tokens, word alignment, beats --------------------------------------
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


def spoken_tokens(data):
    """[(word, flag, line index)]; the headline has line index -1."""
    toks = [(w, f, -1) for w, f in parse_marked(data["headline"])]
    for i, l in enumerate(all_lines(data)):
        toks += [(w, f, i) for w, f in parse_marked(l["t"])]
    return toks


def spoken_text(data):
    h = plain(data["headline"])
    if not re.search(r"[.!?:]$", h):
        h += "."
    return h + " " + " ".join(plain(l["t"]) for l in all_lines(data))


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def align(tokens, words, total):
    """(start, end) of every spoken token: from the TTS word timings, else spread evenly."""
    n = len(tokens)
    if n == 0:
        return []
    if words and len(words) >= max(3, n // 2):
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
        starts = [0.25 + (total - 0.5) * k / n for k in range(n)]
    starts[0] = 0.0
    for k in range(1, n):
        starts[k] = min(max(starts[k], starts[k - 1] + 0.02), total - 0.05)
    return [(s, starts[k + 1] if k + 1 < n else total) for k, s in enumerate(starts)]


def make_beats(data, tokens, times, total):
    """[(start, end, line or None)]: the headline, then one beat per line. Cuts land 0.08 s before the first word."""
    lines = all_lines(data)
    first = {}
    for k, (_, _, li) in enumerate(tokens):
        first.setdefault(li, k)
    bounds = [0.0] + [max(0.6, times[first[i]][0] - 0.08) for i in range(len(lines))] + [total]
    for k in range(len(bounds) - 2, 0, -1):             # every beat stays at least 0.3 s and inside the video
        bounds[k] = min(bounds[k], bounds[k + 1] - 0.3)
    for k in range(1, len(bounds) - 1):
        bounds[k] = max(bounds[k], bounds[k - 1] + 0.3)
    bounds[-1] = total
    return [(bounds[k], bounds[k + 1], None if k == 0 else lines[k - 1]) for k in range(len(bounds) - 1)]


# ---- Shots --------------------------------------------------------------------
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


MAX_SHOT = 2.9


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


def plan_shots(spans, topic, pics, pool):
    """One shot per narration beat (long beats are cut in two), so the picture always matches the words.
    First and last shot show the same thing: the loop restarts on the same image."""
    arts, last = topic["articles"], len(spans) - 1
    hero = pics.get(arts[0], advance=False)
    if FORMAT == "news":                                 # the new thing, not the version page
        hero = next((pics.get(l["show"], advance=False) for _, _, l in spans if l and pics.has(l["show"])), hero)
    anypic = hero or next((v[0] for v in pics.imgs.values() if v), None)
    shots = []

    def add(kind, im, s, e, **kw):
        im, clip = [x for x in im if x], pool.next()
        if kind != "clip" and not im:
            kind = "clip"
        if kind == "clip" and not clip:                  # no gameplay clips at all: pictures carry the video
            if not anypic:
                raise SystemExit("No clips and no pictures")
            kind, im = "image", [anypic]
        shots.append({"kind": kind, "imgs": im, "dur": e - s, "t0": s, "clip": clip, **kw})

    def pictures_in_a_row():
        k = 0
        for sh in reversed(shots):
            if sh["kind"] == "clip":
                break
            k += 1
        return k

    for i, (s, e, line) in enumerate(spans):
        n = 1 if e - s <= MAX_SHOT else math.ceil((e - s) / 2.4)
        show = (line or {}).get("show", "")
        for part in range(n):
            a, b = s + (e - s) * part / n, s + (e - s) * (part + 1) / n
            meta = {"line": i - 1, "tag": (line or {}).get("tag", ""), "num": (line or {}).get("n", 0),
                    "first": part == 0}
            if KIND == "versus":
                A = pics.get(arts[0], False)
                B = pics.get(arts[1], False) if len(arts) > 1 else None
                if i in (0, last):
                    add("split" if (A and B) else "clip", [A, B], a, b, **meta)
                    continue
                side = 0 if norm(show) == norm(arts[0]) else 1 if norm(show) == norm(arts[-1]) else i % 2
                meta["side"] = arts[side]
                p = pics.get(arts[side]) if part == 0 else None
                add("image" if p else "clip", [p], a, b, **meta)
            elif KIND == "quiz":
                if i < last:
                    px = int(6 * (48 / 6) ** (i / max(1, last - 1)))
                    add("pixel", [hero], a, b, px=px, **meta)
                else:
                    add("image", [hero], a, b, **meta)
            elif KIND == "top":
                p = pics.get(show) if (meta["num"] and pics.has(show) and part == 0) else None
                add("image" if p else "clip", [p], a, b, **meta)
            else:
                if i == last or (i == 0 and part == 0):  # hook and loop line: the hero picture
                    add("image", [hero], a, b, **meta)
                    continue
                if part > 0:                             # long beat: picture <-> gameplay
                    p = pics.get(arts[0]) if shots[-1]["kind"] == "clip" else None
                else:
                    p = pics.get(show) if (show and pics.has(show)) else None
                    if p and pictures_in_a_row() >= 2:   # never more than two pictures in a row
                        p = None
                add("image" if p else "clip", [p], a, b, **meta)
    return shots


def card_png(img, name, w, h, px=None, angle=0.0):
    """Trading-card look: dark panel, the picture, white frame, soft shadow, slight tilt."""
    pad, inner_w, inner_h = 14, w - 28 - 40, h - 28 - 40
    pre = f"scale={px}:{px}:force_original_aspect_ratio=decrease," if px else ""
    flags = "neighbor" if px else pix_flags(img)
    fc = (f"[0:v]format=rgba,{pre}scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease:flags={flags},"
          f"format=rgba[im];color=c=0x172033:s={w - 2 * pad}x{h - 2 * pad},format=rgba[pn];"
          f"[pn][im]overlay=(W-w)/2:(H-h)/2:format=auto,format=rgba,"
          f"pad=iw+{2 * pad}:ih+{2 * pad}:{pad}:{pad}:color=white,format=rgba,"
          f"pad=iw+80:ih+80:40:40:color=black@0,split[a][b];"
          f"[b]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.55,boxblur=16:2[sh];"
          f"[sh][a]overlay=x=6:y=14:format=auto,format=rgba,"
          f"rotate={angle:.4f}:ow=rotw({angle:.4f}):oh=roth({angle:.4f}):c=black@0[o]")
    ff("-i", str(img), "-filter_complex", fc, "-map", "[o]", "-frames:v", "1", name)
    return name


def pix_flags(img):
    """Small pixel-art renders are enlarged without blur."""
    size = [int(x) for x in probe(img, "stream=width,height") if x.isdigit()]
    return "neighbor" if size and max(size) < 600 else "lanczos"


BLUR = ("scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=7:2,scale=1080:1920:flags=bilinear,"
        "eq=brightness=-0.10:saturation=1.3")


def render_shot(i, sh):
    dur = sh["dur"]
    fr = int(dur * 30) + 2
    zoom = f"1+0.045*on/{fr}+0.09*max(0,1-on/7)"            # slow push-in + a punch at every cut
    zp = f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30,format=yuv420p"
    ang = (-0.04, 0.04)[i % 2]
    inputs, cards = [], []
    if sh["clip"]:
        start = sh.get("start")
        if start is None:
            start = round(random.uniform(0, max(0, probe_duration(sh["clip"]) - dur - 0.2)), 2)
        inputs += ["-ss", str(start), "-stream_loop", "-1", "-i", str(sh["clip"])]
    else:
        inputs += ["-loop", "1", "-framerate", "30", "-i", str(sh["imgs"][0])]
    if sh["kind"] == "clip":
        pan = f"t/{dur:.3f}" if i % 2 == 0 else f"1-t/{dur:.3f}"
        fc = (f"[0:v]fps=30,setsar=1,split[a][b];[a]{BLUR}[bg];"
              f"[b]scale=1080:{int(WIN_H * 1.06)}:force_original_aspect_ratio=increase,"
              f"crop=1080:{WIN_H}:x='max(0,min(iw-1080,(iw-1080)/2+70*(2*({pan})-1)))':y='(ih-{WIN_H})/2',"
              f"eq=contrast=1.06:saturation=1.15[w];"
              f"[bg][w]overlay=0:{WIN_Y},drawbox=x=0:y={WIN_Y - 6}:w=1080:h=6:color={ACCENT}@1:t=fill,"
              f"drawbox=x=0:y={WIN_Y + WIN_H}:w=1080:h=6:color={ACCENT}@1:t=fill,{zp}[v]")
    else:
        if sh["kind"] == "split":
            sizes = [(420, 700)] * 2
        else:
            sizes = [(860, 800)]
        for k, im in enumerate(sh["imgs"][:len(sizes)]):
            cards.append(card_png(im, f"card{i}_{k}.png", *sizes[k], px=sh.get("px"),
                                  angle=ang if k == 0 else -ang))
        for c in cards:
            inputs += ["-loop", "1", "-framerate", "30", "-i", c]
        fc = f"[0:v]fps=30,setsar=1,{BLUR}[bg];"
        if sh["kind"] == "split":
            fc += (f"[bg][1:v]overlay={SPLIT_X[0]}-w/2:{CARD_CY}-h/2:format=auto[t];"
                   f"[t][2:v]overlay={SPLIT_X[1]}-w/2:{CARD_CY}-h/2:format=auto,{zp}[v]")
        else:
            fc += f"[bg][1:v]overlay={CARD_X}-w/2:{CARD_CY}-h/2:format=auto,{zp}[v]"
    ff(*inputs, "-filter_complex", fc, "-map", "[v]", "-an", "-t", f"{dur:.3f}", *x264(20), f"shot{i}.mp4")
    return f"shot{i}.mp4"


def build_background(shots):
    parts = [render_shot(i, s) for i, s in enumerate(shots)]
    (W / "bg.txt").write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
    ff("-f", "concat", "-safe", "0", "-i", "bg.txt", "-c", "copy", "bg.mp4")


# ---- Captions (ASS): hook, word-by-word captions, stamps, progress bar ---------------
def ass_time(sec):
    cs = int(round(max(0, sec) * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def esc(t):
    return str(t).replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")


def ass_words(words, limit=14):
    """words: [(word, colour)] -> (ASS text in 1-2 upper-case lines, longest line in letters)."""
    words = [(esc(w).upper(), c) for w, c in words]
    total = sum(len(w) for w, _ in words) + len(words) - 1
    brk = None
    if total > limit and len(words) > 1:
        acc, best = 0, None
        for k, (w, _) in enumerate(words[:-1]):
            acc += len(w) + (1 if k else 0)
            if best is None or abs(acc - total / 2) < best[0]:
                best = (abs(acc - total / 2), k)
        brk = best[1]
    out, lens, cur = [], [], 0
    for k, (w, c) in enumerate(words):
        out.append(f"{{\\c{c}}}{w}")
        cur += len(w) + (1 if cur else 0)
        if k == brk:
            out.append("\\N")
            lens.append(cur)
            cur = 0
        elif k < len(words) - 1:
            out.append(" ")
    lens.append(cur)
    return "".join(out).replace(" \\N ", "\\N").replace(" \\N", "\\N"), max(lens)


def fit(longest, maxw=860, top=190, low=60):
    return int(max(low, min(top, maxw / (max(1, longest) * CHAR_W))))


def chunks(tokens, idxs):
    """Caption chunks of 1-3 words (max 18 letters), never across a line, broken after punctuation."""
    out, cur = [], []
    for k in idxs:
        if cur and tokens[k][2] != tokens[cur[0]][2]:
            out.append(cur)
            cur = []
        cur.append(k)
        w = tokens[k][0]
        letters = sum(len(tokens[j][0]) for j in cur) + len(cur) - 1
        nxt = len(tokens[k + 1][0]) if k + 1 < len(tokens) and tokens[k + 1][2] == tokens[k][2] else 99
        if len(cur) >= 3 or letters + 1 + nxt > 13 or re.search(r"[.,!?;:]$", w):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    merged = []                                         # no one-word orphans like "A" or "IT"
    for c in out:
        if merged and len(c) == 1 and len(tokens[c[0]][0]) <= 3 and tokens[merged[-1][0]][2] == tokens[c[0]][2] \
                and len(merged[-1]) < 3 and not re.search(r"[.!?]$", tokens[merged[-1][-1]][0]) \
                and sum(len(tokens[j][0]) + 1 for j in merged[-1] + c) <= 14:
            merged[-1] += c
        else:
            merged.append(c)
    return merged


def build_ass(data, tokens, times, spans, shots, total, topic):
    style = lambda name, size, col, sp, out, sh, al=5, ml=0, mr=0, mv=0, bs=1, back="&H64000000&", oc="&H00000000&": (
        f"Style: {name},{FAMILY},{size},{col},{col},{oc},{back},{BOLD},0,0,0,100,100,{sp},0,{bs},{out},{sh},"
        f"{al},{ml},{mr},{mv},1")
    ev = lambda layer, s, e, st, txt: f"Dialogue: {layer},{ass_time(s)},{ass_time(e)},{st},,0,0,0,,{txt}"
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding",
             style("Cap", 100, WHITE, 1, 10, 3),
             style("Hook", 100, WHITE, 1, 11, 4),
             style("Chip", 38, "&H00000000&", 3, 12, 0, 7, 0, 0, 0, bs=3, back="&H00000000&", oc=ACC_ASS),
             style("Big", 150, WHITE, 3, 10, 4, 5, 0, 0, 0),
             style("Brand", 30, "&H60FFFFFF&", 4, 3, 0, 7, 0, 0, 0),
             style("Bar", 20, WHITE, 0, 0, 0, 7, 0, 0, 0), "",
             "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    hook_end = min(spans[0][1] + 0.35, total)
    # chips: format + running number, then the subject once the hook text is gone
    lines.append(ev(2, 0, total, "Chip", f"{{\\an7\\pos(40,172)}} {esc(LABEL)}  #{FILE_NO} "))
    subject = {"top": topic.get("theme") or f"TOP {F.get('items', 5)}",
               "versus": " VS ".join(topic["articles"])}.get(KIND, topic["articles"][0])
    if hook_end < total - 0.5:
        lines.append(ev(2, hook_end, total, "Chip", f"{{\\an7\\pos(40,244)}} {esc(subject.upper())[:34]} "))
    # hook text
    hw = [(w, {"r": RED, "y": YELLOW}.get(f, WHITE)) for w, f in parse_marked(data["headline"])]
    txt, longest = ass_words(hw)
    pop = "\\fscx70\\fscy70\\t(0,130,\\fscx108\\fscy108)\\t(130,240,\\fscx100\\fscy100)"
    lines.append(ev(3, 0, hook_end, "Hook", f"{{\\an5\\pos(510,{HOOK_Y})\\fs{fit(longest, 880, TOP_HOOK)}{pop}}}{txt}"))
    # word-by-word captions (the headline is the hook text, so captions start with the first line)
    body = [k for k, t in enumerate(tokens) if t[2] >= 0]
    for ch in chunks(tokens, body):
        words = [tokens[k] for k in ch]
        e0 = times[ch[-1]][1]
        if e0 <= hook_end - 0.05:
            continue
        _, longest = ass_words([(w, WHITE) for w, _, _ in words], 14)
        fs = fit(longest, 860, TOP_CAP, 60)
        for pos, k in enumerate(ch):
            cols = [RED if f == "r" else ACC_ASS if f == "y" else (YELLOW if j == pos else WHITE)
                    for j, (_, f, _) in enumerate(words)]
            txt, _ = ass_words([(w, c) for (w, _, _), c in zip(words, cols)], 14)
            a, b = max(times[k][0], hook_end - 0.05) if pos == 0 else times[k][0], times[k][1]
            anim = "\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)" if pos == 0 else ""
            lines.append(ev(1, a, b, "Cap", f"{{\\an5\\pos(510,{CAP_Y})\\fs{fs}{anim}}}{txt}"))
    # overlays tied to a shot
    t, cy = 0.0, CARD_CY
    groups = {}                                         # line index -> start, end, first shot (a line may span 2 shots)
    for i, sh in enumerate(shots):
        a, b = t, t + sh["dur"]
        g = groups.setdefault(sh["line"], {"a": a, "sh": sh})
        g["b"] = b
        if sh["kind"] == "split" and sh.get("first"):
            lines.append(ev(2, a, min(b, a + 1.6), "Big",
                            f"{{\\pos({sum(SPLIT_X) // 2},{cy})\\c{ACC_ASS}\\fscx40\\fscy40\\t(0,180,\\fscx105\\fscy105)"
                            f"\\t(180,280,\\fscx100\\fscy100)}}VS"))
        if sh["kind"] == "pixel":
            lines.append(ev(2, a, b, "Big", f"{{\\pos({CARD_X},{cy})\\c{ACC_ASS}\\bord12\\t(0,{int(sh['dur'] * 1000)},"
                                            f"\\fscx118\\fscy118)}}?"))
        if KIND == "quiz" and sh["kind"] == "clip" and i < len(shots) - 1:
            lines.append(ev(2, a, b, "Big", f"{{\\pos({CARD_X},{cy})\\c{ACC_ASS}}}?"))
        t = b
    for li, g in groups.items():
        sh, a, b = g["sh"], g["a"], g["b"]
        if KIND == "top" and sh.get("num"):
            lines.append(ev(2, a, b, "Big", f"{{\\an7\\pos(50,420)\\fs230\\c{ACC_ASS}\\bord12\\fscx60\\fscy60\\t(0,130,"
                                            f"\\fscx115\\fscy115)\\t(130,230,\\fscx100\\fscy100)}}#{sh['num']}"))
        if KIND == "versus" and sh.get("side"):
            lines.append(ev(2, a, b, "Big", f"{{\\an8\\pos({CARD_X},350)\\fs{fit(len(sh['side']), 760, 100)}\\c{ACC_ASS}"
                                            f"\\bord9}}{esc(sh['side']).upper()}"))
        tag = sh.get("tag", "")
        if tag in ("myth", "truth", "winner"):
            word, col = {"myth": ("MYTH", RED), "truth": ("TRUTH", "&H0050AF4C&"),
                         "winner": ("WINNER", "&H0050AF4C&")}[tag]
            lines.append(ev(3, a + 0.1, b, "Big",
                            f"{{\\an5\\pos({CARD_X},{CARD_CY - 330})\\fs150\\c{col}\\3c&H00FFFFFF&\\bord12\\frz-7\\fscx260\\fscy260"
                            f"\\t(0,150,\\fscx100\\fscy100)\\t(150,220,\\frz-10)\\t(220,290,\\frz-7)}}{word}"))
    if KIND == "quiz" and len(spans) > 2:                   # the answer, big, with the reveal line
        name = esc(topic["articles"][0]).upper()
        lines.append(ev(3, spans[-1][0] + 0.1, total, "Big",
                        f"{{\\an5\\pos({CARD_X},{CARD_CY - 330})\\fs{fit(len(name), 860, 150)}\\c{ACC_ASS}\\3c&H00FFFFFF&"
                        f"\\bord12\\frz-5\\fscx250\\fscy250\\t(0,150,\\fscx100\\fscy100)}}{name}"))
    if KIND == "quiz" and len(spans) > 2:                  # countdown before the reveal
        rev = spans[-1][0]
        for k in (3, 2, 1):
            if rev - k > hook_end:
                lines.append(ev(3, rev - k, rev - k + 1, "Big",
                                f"{{\\an5\\pos(900,{HOOK_Y + 60})\\fs170\\c{ACC_ASS}\\fscx130\\fscy130\\t(0,150,"
                                f"\\fscx100\\fscy100)}}{k}"))
    lines.append(ev(2, 0, total, "Brand", f"{{\\an7\\pos(40,{BRAND_Y})}}{esc(CHANNEL)}"))
    if PROGRESS:
        lines.append(ev(4, 0, total, "Bar", f"{{\\an7\\pos(0,{BAR_Y})\\1c&H00FFFFFF&\\1a&HC8&\\bord0\\shad0\\p1}}"
                                           f"m 0 0 l 1080 0 1080 10 0 10{{\\p0}}"))
        lines.append(ev(5, 0, total, "Bar", f"{{\\an7\\pos(0,{BAR_Y})\\1c{ACC_ASS}\\bord0\\shad0\\fscx0\\p1"
                                           f"\\t(0,{int(total * 1000)},\\fscx100)}}m 0 0 l 1080 0 1080 10 0 10{{\\p0}}"))
    path = W / "subs.ass"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---- Audio -------------------------------------------------------------------
async def _tts(text, path):
    import edge_tts
    pitch = "-6Hz" if FORMAT == "pov" else "+0Hz"          # mob talk: a slightly deeper character voice
    try:
        comm = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=pitch, boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, VOICE, rate=RATE)
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


def build_sfx(total, shots, tokens, times, spans, path):
    """Synthesized sounds. Discrete effects never closer than ~1 s (the most important wins); under them a low
    tension bed that keeps rising and cuts off on the last frame, so the loop restarts the tension."""
    sr = 22050
    buf = [0.0] * int(sr * (total + 0.05))
    rnd = random.Random(len(tokens) * 31 + int(total * 10))

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

    events = []                                        # (time, priority, samples, gain)
    events.append((0.0, 9, boom(60, 1.0, 0.3), 0.8))   # hit on the very first frame
    t = 0.0
    for sh in shots:
        if t > 0:
            events.append((max(0, t - 0.2), 3, whoosh(0.45), 0.45))
        if sh["kind"] == "split" and sh.get("first"):
            events.append((t + 0.05, 7, boom(60, 0.9, 0.35), 0.6))
        if sh.get("tag") in ("myth", "truth", "winner") and sh.get("first"):
            events.append((t + 0.1, 8, boom(70, 0.9, 0.4), 0.75))
        if sh.get("num") and sh.get("first"):
            events.append((t, 6, tap(), 0.8))
        t += sh["dur"]
    for k, (_, flag, li) in enumerate(tokens):
        if flag == "r" and li >= 0:
            events.append((times[k][0], 5, boom(80, 0.5, 0.15), 0.3))
    if KIND == "quiz" and len(spans) > 2:
        rev = spans[-1][0]
        events += [(rev - k, 7, blip(880), 0.5) for k in (3, 2, 1) if rev - k > 0.5]
        events.append((rev, 9, sum((blip(f, 0.14) + [0.0] * int(0.02 * sr) for f in (523, 659, 784, 1046)), []), 0.5))
    elif KIND != "quiz":
        events.append((max(0, total - shots[-1]["dur"]), 8, boom(70, 0.8, 0.3), 0.7))   # payoff hit
    if FORMAT == "secrets":
        events.append((0.5, 6, sum((blip(f) + [0.0] * int(0.02 * sr) for f in (523, 659, 784, 1046)), []), 0.25))
    taken = []
    for tm, prio, smp, g in sorted(events, key=lambda e: -e[1]):
        if all(abs(tm - u) >= 0.95 for u in taken):    # max 1 effect per second
            taken.append(tm)
            add(tm, smp, g)
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


def pick_music(total):
    """Track for this video: music/<format>/ first (old folder names still work), then music/ root, then the old
    music.mp3. Rotates through the pool by run number, so the same track never plays twice in a row."""
    def tracks(d):
        return sorted(f for f in d.glob("*") if f.suffix.lower() in MUSIC_EXT) if d.is_dir() else []
    pool = []
    for name in [FORMAT] + MUSIC_ALIAS.get(FORMAT, []):
        pool = tracks(MUSIC_DIR / name)
        if pool:
            break
    pool = pool or tracks(MUSIC_DIR)
    if not pool and pathlib.Path("music.mp3").exists():
        pool = [pathlib.Path("music.mp3")]
    if not pool:
        return None, 0.0
    track = pool[len(used) % len(pool)]
    if MUSIC_START != "auto":
        start = float(MUSIC_START)
    else:
        spare = max(0.0, probe_duration(track) - total - 1)
        start = round(random.uniform(0, spare), 1) if spare > 1 else 0.0
    print("music:", track, "| start:", start)
    return track, start


def render(ass_path, total):
    args = ["-i", "bg.mp4"]
    fc = [f"[0:v]ass={ass_path.resolve()}:fontsdir={FONTS_DIR}[v]"]
    srcs = []                                        # (input args, filter) for each audio layer
    if NARRATION:
        srcs.append((["-i", "voice.mp3"], "highpass=f=90,lowpass=f=8500,deesser=i=0.6"))
    track, start = pick_music(total)
    if track:
        srcs.append((["-ss", str(start), "-stream_loop", "-1", "-i", str(track.resolve())],
                     f"volume={MUSIC_VOL},afade=t=in:d=0.3"))
    if SFX and (W / "sfx.wav").exists():
        srcs.append((["-i", "sfx.wav"], f"lowpass=f=6000,volume={0.12 * SFX_VOL:.3f}"))
    if not srcs:
        srcs.append((["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"], "anull"))
    for k, (a, f) in enumerate(srcs, 1):
        args += a
        fc.append(f"[{k}:a]aresample=44100,{f}[a{k}]")
    fc.append("".join(f"[a{k}]" for k in range(1, len(srcs) + 1))
              + f"amix=inputs={len(srcs)}:duration=longest:normalize=0,loudnorm=I=-15:TP=-1.5:LRA=9[a]")
    ff(*args, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", *x264(21),
       "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", "final.mp4")


def collect_images(data, topic):
    """{page title: [downloaded pictures]} for the subjects and for every page the lines point at."""
    out, arts = {}, topic["articles"]
    for k, a in enumerate(arts):
        names = topic["images"][k] if k < len(topic.get("images", [])) else None
        out[a] = fetch_images(a, names, f"m{k}_", 2 if FORMAT == "news" else 4 if len(arts) <= 2 else 2)
    have = {norm(a) for a in out}
    extra = [l["show"] for l in all_lines(data) if l["show"]] + list(data.get("related", []))[:3]
    for t in dict.fromkeys(str(x) for x in extra):
        if norm(t) in have or len(out) >= 10 or not t:
            continue
        have.add(norm(t))
        out[t] = fetch_images(t, None, f"x{len(out)}_", 2)
    return out


def make_video(data, topic):
    tokens = spoken_tokens(data)
    words = []
    total = min(MAX_SEC, max(MIN_SEC, 1 + len(tokens) * 0.33))
    if NARRATION:
        words = tighten(W / "voice.mp3", asyncio.run(_tts(spoken_text(data), W / "voice.mp3")))
        total = min(MAX_SEC, max(MIN_SEC, probe_duration(W / "voice.mp3") + 0.15))   # no tail: seamless loop
    times = align(tokens, words, total)
    spans = make_beats(data, tokens, times, total)
    imgs = collect_images(data, topic)
    pool = ClipPool(CLIPS)
    if not pool.clips and not any(imgs.values()):
        fb = get_fallback_clip()
        if not fb:
            raise SystemExit("No clips and no pictures")
        pool = ClipPool([fb])
    print("clips:", len(pool.clips), "| pictured pages:", sum(bool(v) for v in imgs.values()), "| shots:", len(spans))
    shots = plan_shots(spans, topic, Pics(imgs), pool)
    build_background(shots)
    if SFX:
        build_sfx(total, shots, tokens, times, spans, W / "sfx.wav")
    render(build_ass(data, tokens, times, spans, shots, total, topic), total)
    return any(imgs.values())


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
    tags = list(dict.fromkeys(["Minecraft", "Shorts"] + F["tags"] + [str(t) for t in data.get("tags", [])]))
    texts = [plain(l["t"]) for l in all_lines(data)]
    if KIND != "quiz" and texts and not re.search(r"[.!?]$", texts[-1]):      # the loop line is unfinished by design
        texts = texts[:-1]
    story = plain(data["headline"]) + "\n" + "\n".join(texts)
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


# ---- Learning: the bot looks at its own results and leans toward what gets views --------
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
        print("view counts unavailable, rotating formats:", str(e)[:150])
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
    for field in ("fmt", "hook"):
        by = {}
        for e, v in rows:
            if e.get(field):
                by.setdefault(e[field], []).append(v)
        if by:
            print(field + ":", " | ".join(f"{k} {sum(v) // len(v)} avg views (n={len(v)})"
                                          for k, v in sorted(by.items(), key=lambda x: -sum(x[1]) / len(x[1]))))


def new_script(topic, rows):
    topic["hook"] = bandit(list(HOOK_TYPES), "hook", rows) if FORMAT in HOOKED else FORMAT
    return gen_script(topic)


def main():
    yt = youtube()
    rows = performance(yt) if LEARN else []
    report(rows)
    forced = env("CATEGORY") or env("FORMAT")
    use_format(forced or (bandit(list(FORMATS), "fmt", rows) if LEARN else list(FORMATS)[len(used) % len(FORMATS)]))
    topic = pick_topic()                                  # may switch to "secrets" when there is no fresh update
    data = new_script(topic, rows)
    if data is None:                                      # news: the changelog has nothing for players
        use_format("secrets")
        topic = pick_topic()
        data = new_script(topic, rows)
    print("format:", FORMAT, "| hook:", topic["hook"], "| topic:", topic["key"], "| wiki:", WIKI_OK)
    print(json.dumps(data, ensure_ascii=False))
    has_images = make_video(data, topic)
    vid = upload(yt, data, topic, has_images)
    used.append({"key": topic["key"], "arts": topic["articles"], "fmt": FORMAT, "hook": topic["hook"],
                 "hl": plain(data["headline"]), "vid": vid, "ts": int(time.time())})
    USED.write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
    add_to_playlist(yt, vid)
    post_comment(yt, vid, data)


if __name__ == "__main__":
    main()
