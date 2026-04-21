"""
database.py – SQLite persistence layer for the IHG Scheduling Bot.

Design note on venue availability:
  The bot does NOT connect to NUS REBOKS or the SUU booking system.
  Venue availability is always determined manually:
    1. Scheduling IC submits SUU Facilities Booking Form (≥2 weeks before event).
    2. SUU emails a confirmation.
    3. IC enters the confirmed fixture into the bot via /addfixture.
  The bot's "clash detection" only flags conflicts WITHIN IHG's own schedule.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional
import pytz

from config import DB_PATH, TIMEZONE

TZ = pytz.timezone(TIMEZONE)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    c.row_factory = sqlite3.Row
    return c


def now_sgt() -> datetime:
    return datetime.now(TZ)


def now_str() -> str:
    return now_sgt().strftime("%Y-%m-%d %H:%M")


# ── Schema ───────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't exist.  Safe to call on every startup."""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fixtures (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sport       TEXT    NOT NULL,
                hall_a      TEXT    NOT NULL,
                hall_b      TEXT    NOT NULL,
                venue       TEXT    NOT NULL,
                match_dt    TEXT    NOT NULL,   -- "YYYY-MM-DD HH:MM" in SGT
                status      TEXT    NOT NULL DEFAULT 'scheduled',
                -- status values: scheduled | postponed | cancelled
                notes       TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                sub_type    TEXT    NOT NULL,   -- 'hall' or 'sport'
                sub_value   TEXT    NOT NULL,   -- lowercase hall/sport name
                UNIQUE(chat_id, sub_type, sub_value)
            );

            CREATE TABLE IF NOT EXISTS reminders_sent (
                fixture_id  INTEGER NOT NULL,
                hours_before INTEGER NOT NULL,
                sent_at     TEXT    NOT NULL,
                PRIMARY KEY (fixture_id, hours_before)
            );

            CREATE TABLE IF NOT EXISTS admins (
                chat_id     INTEGER PRIMARY KEY,
                username    TEXT    DEFAULT '',
                added_at    TEXT    NOT NULL
            );
        """)


# ── Fixture CRUD ─────────────────────────────────────────────────────────────

def add_fixture(sport: str, hall_a: str, hall_b: str, venue: str, match_dt: str) -> int:
    ts = now_str()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO fixtures (sport, hall_a, hall_b, venue, match_dt, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (sport, hall_a, hall_b, venue, match_dt, ts, ts),
        )
        return cur.lastrowid


def get_fixture(fixture_id: int) -> Optional[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute("SELECT * FROM fixtures WHERE id=?", (fixture_id,)).fetchone()


def get_fixtures_by_sport(sport: str) -> list:
    now = now_str()
    base = sport.strip()
    if base.endswith(" (M)") or base.endswith(" (F)"):
        with _conn() as conn:
            return conn.execute(
                "SELECT * FROM fixtures"
                " WHERE lower(sport)=lower(?) AND status='scheduled' AND match_dt>=?"
                " ORDER BY match_dt",
                (sport, now),
            ).fetchall()

    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM fixtures WHERE status='scheduled' AND match_dt>=? ORDER BY match_dt",
            (now,),
        ).fetchall()
    return [r for r in rows if r["sport"] == base or r["sport"].startswith(f"{base} (")]


def get_next_match(hall: str) -> Optional[sqlite3.Row]:
    now = now_str()
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM fixtures"
            " WHERE (lower(hall_a)=lower(?) OR lower(hall_b)=lower(?))"
            "   AND status='scheduled' AND match_dt>=?"
            " ORDER BY match_dt LIMIT 1",
            (hall, hall, now),
        ).fetchone()


def get_fixtures_by_venue(venue: str) -> list:
    now = now_str()
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM fixtures"
            " WHERE lower(venue) LIKE lower(?) AND status='scheduled' AND match_dt>=?"
            " ORDER BY match_dt",
            (f"%{venue}%", now),
        ).fetchall()


def get_upcoming_fixtures(days: int = 7) -> list:
    now = now_sgt()
    end = now + timedelta(days=days)
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM fixtures"
            " WHERE status='scheduled' AND match_dt>=? AND match_dt<=?"
            " ORDER BY match_dt",
            (now.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")),
        ).fetchall()


def get_fixtures_on_date(date_str: str) -> list:
    """
    All scheduled fixtures on a given date (YYYY-MM-DD), sorted by time.
    Includes postponed/cancelled so the day view is complete.
    """
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM fixtures"
            " WHERE substr(match_dt, 1, 10) = ?"
            " ORDER BY match_dt",
            (date_str,),
        ).fetchall()


def get_venue_fixtures_on_date(venue: str, date_str: str) -> list:
    """
    All *scheduled* fixtures at a given venue on a given date.
    Used to compute free slot windows.
    """
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM fixtures"
            " WHERE lower(venue) LIKE lower(?)"
            "   AND substr(match_dt, 1, 10) = ?"
            "   AND status = 'scheduled'"
            " ORDER BY match_dt",
            (f"%{venue}%", date_str),
        ).fetchall()


def get_all_fixtures_for_hall(hall: str) -> list:
    """All fixtures (past + future, all statuses) for admin review."""
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM fixtures"
            " WHERE lower(hall_a)=lower(?) OR lower(hall_b)=lower(?)"
            " ORDER BY match_dt DESC",
            (hall, hall),
        ).fetchall()


def get_all_fixtures_admin(limit: int = 30) -> list:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM fixtures ORDER BY match_dt DESC LIMIT ?", (limit,)
        ).fetchall()


def update_status(fixture_id: int, status: str, notes: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE fixtures SET status=?, notes=?, updated_at=? WHERE id=?",
            (status, notes, now_str(), fixture_id),
        )


def reschedule_fixture(fixture_id: int, new_dt: str, new_venue: Optional[str] = None) -> None:
    with _conn() as conn:
        if new_venue:
            conn.execute(
                "UPDATE fixtures SET match_dt=?, venue=?, status='scheduled',"
                " notes='', updated_at=? WHERE id=?",
                (new_dt, new_venue, now_str(), fixture_id),
            )
        else:
            conn.execute(
                "UPDATE fixtures SET match_dt=?, status='scheduled',"
                " notes='', updated_at=? WHERE id=?",
                (new_dt, now_str(), fixture_id),
            )
        # Clear old reminders so new ones fire for the rescheduled time
        conn.execute("DELETE FROM reminders_sent WHERE fixture_id=?", (fixture_id,))


def update_venue(fixture_id: int, new_venue: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE fixtures SET venue=?, updated_at=? WHERE id=?",
            (new_venue, now_str(), fixture_id),
        )


def delete_fixture(fixture_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM fixtures WHERE id=?", (fixture_id,))
        conn.execute("DELETE FROM reminders_sent WHERE fixture_id=?", (fixture_id,))


def delete_all_fixtures() -> int:
    """Delete all fixtures and reminder markers. Returns number of deleted fixtures."""
    with _conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
        conn.execute("DELETE FROM reminders_sent")
        conn.execute("DELETE FROM fixtures")
    return count


def get_distinct_sports() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT sport FROM fixtures ORDER BY sport"
        ).fetchall()
    return [r["sport"] for r in rows]


# ── Clash Detection ──────────────────────────────────────────────────────────
# NOTE: This only detects clashes WITHIN the IHG schedule you've entered.
# It cannot know about NUS varsity bookings, other events at the same venue,
# or any external calendar.  Always verify with SUU before finalising a slot.

VENUE_BUFFER_MINUTES = 90   # Flag if two IHG matches at same venue < 90 min apart
HALL_BUFFER_MINUTES  = 120  # Flag if same hall has two matches < 2 h apart


def check_venue_clashes() -> list:
    with _conn() as conn:
        return conn.execute(
            f"""
            SELECT a.id id_a, b.id id_b,
                   a.sport sport_a, b.sport sport_b,
                   a.hall_a a_ha, a.hall_b a_hb,
                   b.hall_a b_ha, b.hall_b b_hb,
                   a.venue, a.match_dt dt_a, b.match_dt dt_b
            FROM fixtures a
            JOIN fixtures b
              ON lower(a.venue) = lower(b.venue)
             AND a.id < b.id
             AND a.status = 'scheduled'
             AND b.status = 'scheduled'
             AND ABS(
                   (strftime('%s', a.match_dt) - strftime('%s', b.match_dt))
                 ) < {VENUE_BUFFER_MINUTES * 60}
            """
        ).fetchall()


def check_hall_clashes() -> list:
    with _conn() as conn:
        return conn.execute(
            f"""
            SELECT a.id id_a, b.id id_b,
                   a.sport sport_a, b.sport sport_b,
                   a.hall_a a_ha, a.hall_b a_hb,
                   b.hall_a b_ha, b.hall_b b_hb,
                   a.match_dt dt_a, b.match_dt dt_b
            FROM fixtures a
            JOIN fixtures b
              ON a.id < b.id
             AND a.status = 'scheduled'
             AND b.status = 'scheduled'
             AND (lower(a.hall_a) IN (lower(b.hall_a), lower(b.hall_b))
               OR lower(a.hall_b) IN (lower(b.hall_a), lower(b.hall_b)))
             AND (strftime('%s', b.match_dt) - strftime('%s', a.match_dt))
                 BETWEEN 0 AND {HALL_BUFFER_MINUTES * 60}
            """
        ).fetchall()


def get_fixture_clashes(fixture_id: int) -> tuple[list, list]:
    """
    Return (venue_clashes, hall_clashes) for one fixture against all other
    scheduled fixtures.
    """
    with _conn() as conn:
        venue_clashes = conn.execute(
            f"""
            SELECT f.id id_target, o.id id_other,
                   f.sport sport_target, o.sport sport_other,
                   f.hall_a target_ha, f.hall_b target_hb,
                   o.hall_a other_ha, o.hall_b other_hb,
                   f.venue venue_target, o.venue venue_other,
                   f.match_dt dt_target, o.match_dt dt_other
            FROM fixtures f
            JOIN fixtures o
              ON f.id = ?
             AND o.id != f.id
             AND f.status = 'scheduled'
             AND o.status = 'scheduled'
             AND lower(f.venue) = lower(o.venue)
             AND ABS(strftime('%s', f.match_dt) - strftime('%s', o.match_dt)) < ?
            ORDER BY o.match_dt
            """,
            (fixture_id, VENUE_BUFFER_MINUTES * 60),
        ).fetchall()

        hall_clashes = conn.execute(
            f"""
            SELECT f.id id_target, o.id id_other,
                   f.sport sport_target, o.sport sport_other,
                   f.hall_a target_ha, f.hall_b target_hb,
                   o.hall_a other_ha, o.hall_b other_hb,
                   f.match_dt dt_target, o.match_dt dt_other
            FROM fixtures f
            JOIN fixtures o
              ON f.id = ?
             AND o.id != f.id
             AND f.status = 'scheduled'
             AND o.status = 'scheduled'
             AND (
                   lower(f.hall_a) IN (lower(o.hall_a), lower(o.hall_b))
                OR lower(f.hall_b) IN (lower(o.hall_a), lower(o.hall_b))
             )
             AND ABS(strftime('%s', f.match_dt) - strftime('%s', o.match_dt)) < ?
            ORDER BY o.match_dt
            """,
            (fixture_id, HALL_BUFFER_MINUTES * 60),
        ).fetchall()

    return venue_clashes, hall_clashes


# ── Subscriptions ─────────────────────────────────────────────────────────────

def subscribe(chat_id: int, sub_type: str, value: str) -> bool:
    """Return True if newly added, False if already existed."""
    with _conn() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE chat_id=? AND sub_type=? AND sub_value=?",
            (chat_id, sub_type, value.lower()),
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions (chat_id, sub_type, sub_value) VALUES (?,?,?)",
            (chat_id, sub_type, value.lower()),
        )
        return before == 0


def unsubscribe(chat_id: int, sub_type: str, value: str) -> bool:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM subscriptions WHERE chat_id=? AND sub_type=? AND sub_value=?",
            (chat_id, sub_type, value.lower()),
        )
        return conn.total_changes > 0


def get_subscriptions(chat_id: int) -> list:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM subscriptions WHERE chat_id=? ORDER BY sub_type, sub_value",
            (chat_id,),
        ).fetchall()


def get_subscribers_for_fixture(fixture: sqlite3.Row) -> list[int]:
    """All chat_ids subscribed to either hall or the sport in this fixture."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT chat_id FROM subscriptions
            WHERE (sub_type='hall'  AND lower(sub_value) IN (lower(?), lower(?)))
               OR (sub_type='sport' AND lower(sub_value)  = lower(?))
            """,
            (fixture["hall_a"], fixture["hall_b"], fixture["sport"]),
        ).fetchall()
    return [r["chat_id"] for r in rows]


def get_all_subscriber_ids() -> list[int]:
    with _conn() as conn:
        rows = conn.execute("SELECT DISTINCT chat_id FROM subscriptions").fetchall()
    return [r["chat_id"] for r in rows]


# ── Reminders ─────────────────────────────────────────────────────────────────

def get_fixtures_needing_reminder(hours_before: int) -> list:
    """
    Return scheduled fixtures whose match_dt is (hours_before ± 5 min) from now
    and for which this reminder has not yet been sent.
    """
    low  = hours_before * 3600 - 300   # 5-minute window start
    high = hours_before * 3600 + 300   # 5-minute window end
    now_epoch = int(now_sgt().timestamp())
    with _conn() as conn:
        return conn.execute(
            """
            SELECT f.* FROM fixtures f
            WHERE f.status = 'scheduled'
              AND (strftime('%s', f.match_dt) - ?) BETWEEN ? AND ?
              AND f.id NOT IN (
                  SELECT fixture_id FROM reminders_sent WHERE hours_before=?
              )
            """,
            (now_epoch, low, high, hours_before),
        ).fetchall()


def mark_reminder_sent(fixture_id: int, hours_before: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO reminders_sent (fixture_id, hours_before, sent_at) VALUES (?,?,?)",
            (fixture_id, hours_before, now_str()),
        )


# ── Admins ────────────────────────────────────────────────────────────────────

def is_admin(chat_id: int) -> bool:
    from config import ADMIN_IDS
    if chat_id in ADMIN_IDS:
        return True
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM admins WHERE chat_id=?", (chat_id,)
        ).fetchone() is not None


def add_admin(chat_id: int, username: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (chat_id, username, added_at) VALUES (?,?,?)",
            (chat_id, username, now_str()),
        )
