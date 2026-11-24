"""Which publish slot does this run fill? Prints KEY=value lines for $GITHUB_ENV (stdlib only).

GitHub starts only about 2 of the 5 scheduled runs a day, hours late. So the videos are made ahead and uploaded
private with publishAt: YouTube publishes them on the minute. A slot run starts the next one itself while a slot
is free, so any one scheduled run fills the whole day.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SLOTS = [(6, 7), (7, 7), (23, 7), (0, 7), (1, 7)]   # UTC
AHEAD_H = 24          # fill the slots of the next 24 hours
MARGIN_MIN = 45       # longer than the job timeout (40 min): the upload is always before the slot
MAX_UPLOADS = 5       # per YouTube quota day (Pacific time)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def gate(now=None, videos=None):
    now = now or datetime.now(timezone.utc)
    if videos is None:
        with open("used.json", encoding="utf-8") as f:   # unreadable state = the run fails, it never guesses
            videos = json.load(f)["videos"]
    taken = {v.get("slot") for v in videos}
    quota_day = now.astimezone(ZoneInfo("America/Los_Angeles")).replace(hour=0, minute=0, second=0, microsecond=0)
    uploads = sum(v.get("up", v.get("ts", 0)) >= quota_day.timestamp() for v in videos)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = sorted(base + timedelta(days=d, hours=h, minutes=m) for d in (0, 1) for h, m in SLOTS)
    free = [t for t in slots if now + timedelta(minutes=MARGIN_MIN) < t <= now + timedelta(hours=AHEAD_H)
            and iso(t) not in taken]
    if free and uploads < MAX_UPLOADS:
        return {"GO": "1", "SLOT": iso(free[0])}
    due = any(v.get("comment_q") and v.get("ts", 0) < now.timestamp() for v in videos[-40:])
    return {"GO": "0", "COMMENTS": "1" if due else "0"}


if __name__ == "__main__":
    out = gate()
    print(out.get("SLOT", "") if "--slot" in sys.argv else "\n".join(f"{k}={v}" for k, v in out.items()))
