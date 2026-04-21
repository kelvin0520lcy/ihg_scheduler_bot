import os
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
    "MPSH 1", "MPSH 2", "MPSH 4", "MPSH 5", "MPSH 6",
    "USC Sports Hall", "USC Swimming Pool", "USC Squash Courts",
    "UTown Sports Hall 1", "UTown Sports Hall 2",
    "University Field", "Track (UTown)",
]
