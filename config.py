import os
import re
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set. Copy .env.example → .env and fill it in.")

DB_PATH: str = os.getenv("DB_PATH", "ihg.db")
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Singapore")

# Parse comma-separated admin IDs from .env
_raw_admins = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _raw_admins.split(",") if x.strip().isdigit()]

# How many hours before a match to send reminder notifications.
# 24h = day-before heads-up; 2h = "leave now" nudge.
REMINDER_INTERVALS: list[int] = [24, 2]

# ── NUS IHG halls (adjust if PGP status changes) ────────────────────────────
HALLS: list[str] = [
    "Eusoff", "Kent Ridge", "King Edward VII",
    "Raffles", "Sheares", "Temasek",
]

# ── Common NUS IHG venues ────────────────────────────────────────────────────
# These are just for the /venues helper list.  Venue names in the DB are free-text
# so ICs can type exactly what SUU confirmed (e.g. "MPSH 1" or "USC Sports Hall").
VENUES: list[str] = [
    "MPSH 1", "MPSH 2", "MPSH 3", "MPSH 4", "MPSH 5", "MPSH 6",
    "USC Sports Hall", "USC Swimming Pool", "USC Squash Courts",
    "UTown Sports Hall 1", "UTown Sports Hall 2",
    "Field 1", "Field 2", "Field 3",
]

# ── Sport canonicalization ─────────────────────────────────────────────────────
SPORTS_WITH_GENDER: list[str] = [
    "Badminton",
    "Basketball",
    "Floorball",
    "Table Tennis",
    "Tennis",
    "Volleyball",
]
UNISEX_SPORTS: list[str] = [
    "Softball",
]


def sport_options() -> list[str]:
    tagged = [f"{s} (M)" for s in SPORTS_WITH_GENDER] + [f"{s} (F)" for s in SPORTS_WITH_GENDER]
    return sorted(tagged + UNISEX_SPORTS)


def canonicalize_sport(raw: str) -> str | None:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if not text:
        return None

    opts = {o.lower(): o for o in sport_options()}
    if text.lower() in opts:
        return opts[text.lower()]

    m = re.match(r"^(.*?)(?:\s*[\(\[]?\s*([mfMF])\s*[\)\]]?)$", text)
    if m:
        base = m.group(1).strip()
        tag = m.group(2).upper()
        candidate = f"{base} ({tag})"
        return opts.get(candidate.lower())

    for sport in UNISEX_SPORTS:
        if text.lower() == sport.lower():
            return sport
    return None
