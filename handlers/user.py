"""
handlers/user.py – Commands available to all users.

Commands:
  /start              Welcome message
  /help               Command list
  /schedule <sport>   Fixtures for a sport
  /nextmatch <hall>   Next fixture for a hall
  /venue <name>       Fixtures at a venue
  /upcoming           All fixtures in the next 7 days
  /sports             List sports with fixtures
  /halls              List halls
  /venues             List known venues
  /subscribe          Subscribe to a hall or sport
  /unsubscribe        Remove a subscription
  /mysubscriptions    Show your subscriptions
"""

from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
from config import HALLS, VENUES
from handlers.utils import fmt_fixture, chunk


# ── /start ───────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to the IHG Scheduling Bot!*\n\n"
        "I help you look up Inter Hall Games fixtures, get reminders before "
        "your hall's matches, and stay updated on schedule changes.\n\n"
        "Use /help to see all available commands.\n"
        "If you are a Scheduling IC/admin, you can also use:\n"
        "  `/addschedule`, `/changeschedule`, `/overallschedule`\n\n"
        "💡 Tip: Use /subscribe to get notified before your hall's matches.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /help ────────────────────────────────────────────────────────────────────

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 *IHG Bot Commands*\n\n"
        "*Fixture Lookup*\n"
        "  /schedule `<sport>` — fixtures for a sport\n"
        "  /nextmatch `<hall>` — next match for a hall\n"
        "  /venue `<name>` — matches at a venue\n"
        "  /upcoming — all matches in the next 7 days\n"
        "  /dayschedule `<date>` — all fixtures on a specific day\n\n"
        "*Venue Planning*\n"
        "  /freeslots `<venue> | <date> | <hours>` — free time windows at a venue\n\n"
        "*Discovery*\n"
        "  /sports — list all sports with fixtures\n"
        "  /halls — list all halls\n"
        "  /venues — list common NUS venues\n\n"
        "*Notifications*\n"
        "  /subscribe — get reminders for a hall or sport\n"
        "  /unsubscribe — remove a subscription\n"
        "  /mysubscriptions — see what you're subscribed to\n\n"
        "*Admin Scheduling (ICs only)*\n"
        "  /addschedule `<sport> | <hall_a> | <hall_b> | <venue> | <YYYY-MM-DD HH:MM>`\n"
        "  /changeschedule `<id> | <YYYY-MM-DD HH:MM> [| <new_venue>]`\n"
        "  /overallschedule `[days]` — consolidated upcoming schedule (default 14)\n\n"
        "📌 Reminders are sent *24 hours* and *2 hours* before each match "
        "to all subscribers of the relevant hall or sport.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /sports ──────────────────────────────────────────────────────────────────

async def sports(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    sport_list = db.get_distinct_sports()
    if not sport_list:
        await update.message.reply_text("No fixtures have been entered yet.")
        return
    lines = "\n".join(f"  • {s}" for s in sport_list)
    await update.message.reply_text(
        f"🏅 *Sports with fixtures:*\n\n{lines}\n\n"
        "Use `/schedule <sport>` to see matches.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /halls ───────────────────────────────────────────────────────────────────

async def halls(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = "\n".join(f"  • {h}" for h in HALLS)
    await update.message.reply_text(
        f"🏠 *IHG Halls:*\n\n{lines}\n\n"
        "Use `/nextmatch <hall>` to see a hall's next fixture.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /venues ──────────────────────────────────────────────────────────────────

async def venues_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = "\n".join(f"  • {v}" for v in VENUES)
    await update.message.reply_text(
        f"📍 *Common NUS IHG Venues:*\n\n{lines}\n\n"
        "Use `/venue <name>` to see matches at a venue.\n\n"
        "⚠️ Note: Venue availability is confirmed by ICs with NUS SUU. "
        "The bot only shows slots that have been officially booked and entered.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /schedule <sport> ────────────────────────────────────────────────────────

async def schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Please specify a sport.\nExample: `/schedule Badminton`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    sport = " ".join(ctx.args)
    fixtures = db.get_fixtures_by_sport(sport)

    if not fixtures:
        await update.message.reply_text(
            f"No upcoming fixtures found for *{sport}*.\n\n"
            "Try /sports to see which sports have fixtures entered.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = [f"🗓 *Upcoming fixtures — {sport}*\n"]
    for f in fixtures:
        lines.append(fmt_fixture(f))
    await update.message.reply_text(
        "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


# ── /nextmatch <hall> ────────────────────────────────────────────────────────

async def nextmatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Please specify a hall.\nExample: `/nextmatch Eusoff`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    hall = " ".join(ctx.args)
    f = db.get_next_match(hall)

    if not f:
        await update.message.reply_text(
            f"No upcoming scheduled matches found for *{hall}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        f"⏭ *Next match for {hall}*\n\n{fmt_fixture(f)}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /venue <name> ────────────────────────────────────────────────────────────

async def venue(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Please specify a venue.\nExample: `/venue MPSH 1`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    venue_name = " ".join(ctx.args)
    fixtures = db.get_fixtures_by_venue(venue_name)

    if not fixtures:
        await update.message.reply_text(
            f"No upcoming IHG fixtures found at *{venue_name}*.\n\n"
            "Check the venue name or use /venues for a list.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = [f"📍 *Upcoming fixtures at {venue_name}*\n"]
    for f in fixtures:
        lines.append(fmt_fixture(f))

    await update.message.reply_text(
        "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


# ── /upcoming ────────────────────────────────────────────────────────────────

async def upcoming(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    fixtures = db.get_upcoming_fixtures(days=7)

    if not fixtures:
        await update.message.reply_text("No fixtures scheduled in the next 7 days.")
        return

    lines = ["📅 *Upcoming fixtures (next 7 days)*\n"]
    for f in fixtures:
        lines.append(fmt_fixture(f))

    # Telegram has a 4096-char message limit; split if needed
    full_text = "\n\n".join(lines)
    if len(full_text) <= 4000:
        await update.message.reply_text(full_text, parse_mode=ParseMode.MARKDOWN)
    else:
        # Send in pages of up to 10 fixtures
        await update.message.reply_text(lines[0], parse_mode=ParseMode.MARKDOWN)
        for batch in chunk(fixtures, 10):
            page = "\n\n".join(fmt_fixture(f) for f in batch)
            await update.message.reply_text(page, parse_mode=ParseMode.MARKDOWN)


# ── /subscribe ───────────────────────────────────────────────────────────────

async def subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage:
      /subscribe hall Eusoff
      /subscribe sport Badminton
    """
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "  `/subscribe hall <HallName>`\n"
            "  `/subscribe sport <SportName>`\n\n"
            "Examples:\n"
            "  `/subscribe hall Eusoff`\n"
            "  `/subscribe sport Badminton`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    sub_type = ctx.args[0].lower()
    value    = " ".join(ctx.args[1:])

    if sub_type not in ("hall", "sport"):
        await update.message.reply_text("Type must be `hall` or `sport`.", parse_mode=ParseMode.MARKDOWN)
        return

    chat_id = update.effective_chat.id
    added   = db.subscribe(chat_id, sub_type, value)

    if added:
        await update.message.reply_text(
            f"✅ Subscribed to *{value}* ({sub_type}) updates.\n"
            "You'll get reminders 24 h and 2 h before matches.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            f"You're already subscribed to *{value}* ({sub_type}).",
            parse_mode=ParseMode.MARKDOWN,
        )


# ── /unsubscribe ─────────────────────────────────────────────────────────────

async def unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "  `/unsubscribe hall <HallName>`\n"
            "  `/unsubscribe sport <SportName>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    sub_type = ctx.args[0].lower()
    value    = " ".join(ctx.args[1:])
    chat_id  = update.effective_chat.id

    removed = db.unsubscribe(chat_id, sub_type, value)
    if removed:
        await update.message.reply_text(f"✅ Unsubscribed from *{value}*.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(
            f"You weren't subscribed to *{value}* ({sub_type}).", parse_mode=ParseMode.MARKDOWN
        )


# ── /mysubscriptions ─────────────────────────────────────────────────────────

async def my_subscriptions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    subs = db.get_subscriptions(update.effective_chat.id)

    if not subs:
        await update.message.reply_text(
            "You have no active subscriptions.\n"
            "Use /subscribe to add one.",
        )
        return

    hall_subs  = [s["sub_value"] for s in subs if s["sub_type"] == "hall"]
    sport_subs = [s["sub_value"] for s in subs if s["sub_type"] == "sport"]

    text = "📬 *Your subscriptions*\n\n"
    if hall_subs:
        text += "*Halls:*\n" + "\n".join(f"  • {v}" for v in hall_subs) + "\n\n"
    if sport_subs:
        text += "*Sports:*\n" + "\n".join(f"  • {v}" for v in sport_subs)

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── Venue / schedule constants ────────────────────────────────────────────────

DEFAULT_MATCH_DURATION_H = 1.5   # assumed match length in hours
SETUP_BUFFER_MIN         = 30    # setup/teardown buffer either side of a match
VENUE_OPEN_HOUR          = 8     # 08:00 – NUS venues generally available from here
VENUE_CLOSE_HOUR         = 22    # 22:00


# ── Date parsing helper ───────────────────────────────────────────────────────

def _parse_date(raw: str):
    """Parse 'YYYY-MM-DD' or 'DD/MM/YYYY' → 'YYYY-MM-DD', or None on failure."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── /dayschedule <date> ───────────────────────────────────────────────────────

async def dayschedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /dayschedule <YYYY-MM-DD>  or  /dayschedule <DD/MM/YYYY>

    Shows every IHG fixture on that day, sorted by time.
    Useful for ICs checking a day's load before adding a new match.
    """
    if not ctx.args:
        await update.message.reply_text(
            "Please provide a date.\nExamples:\n"
            "  `/dayschedule 2025-02-05`\n"
            "  `/dayschedule 05/02/2025`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    date_str = _parse_date(" ".join(ctx.args))
    if not date_str:
        await update.message.reply_text(
            "❌ Couldn't parse that date. Use `YYYY-MM-DD` or `DD/MM/YYYY`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    fixtures = db.get_fixtures_on_date(date_str)

    try:
        day_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %-d %b %Y")
    except ValueError:
        day_label = date_str

    if not fixtures:
        await update.message.reply_text(
            f"📅 *{day_label}*\n\nNo IHG fixtures on this day.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = [f"📅 *{day_label}*  ({len(fixtures)} fixture(s))\n"]
    for f in fixtures:
        lines.append(fmt_fixture(f))

    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── /freeslots <venue> | <date> | <duration_hours> ───────────────────────────

async def freeslots(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /freeslots <venue> | <date> | <duration_hours>

    Shows available time windows at a venue on a given day, based purely on
    what's already entered in the IHG schedule.

    Examples:
      /freeslots MPSH 1 | 2025-02-05 | 2
      /freeslots USC Sports Hall | 05/02/2025 | 1.5

    ⚠️  Only checks IHG-internal bookings. Always verify NUS-wide
        availability via REBOKS or by contacting SUU before confirming.
    """
    text  = " ".join(ctx.args) if ctx.args else ""
    parts = [p.strip() for p in text.split("|")]

    if len(parts) != 3:
        await update.message.reply_text(
            "Usage: `/freeslots <venue> | <date> | <duration_hours>`\n\n"
            "Examples:\n"
            "  `/freeslots MPSH 1 | 2025-02-05 | 2`\n"
            "  `/freeslots USC Sports Hall | 05/02/2025 | 1.5`\n\n"
            "Duration is in hours (e.g. `2` or `1.5`).\n\n"
            "⚠️ Shows gaps in the IHG schedule only — not NUS-wide availability.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    venue_query, raw_date, raw_dur = parts

    date_str = _parse_date(raw_date)
    if not date_str:
        await update.message.reply_text(
            "❌ Couldn't parse the date. Use `YYYY-MM-DD` or `DD/MM/YYYY`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        duration_h = float(raw_dur)
        if not (0.5 <= duration_h <= 12):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Duration must be a number between 0.5 and 12.")
        return

    fixtures = db.get_venue_fixtures_on_date(venue_query, date_str)

    try:
        day_obj   = datetime.strptime(date_str, "%Y-%m-%d")
        day_label = day_obj.strftime("%A, %-d %b %Y")
    except ValueError:
        day_obj   = datetime.today()
        day_label = date_str

    # ── Build occupied blocks (match + setup buffer each side) ─────────────
    occupied = []
    for f in fixtures:
        try:
            start = datetime.strptime(f["match_dt"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        end = start + timedelta(hours=DEFAULT_MATCH_DURATION_H)
        buf = timedelta(minutes=SETUP_BUFFER_MIN)
        occupied.append((start - buf, end + buf, f))

    occupied.sort(key=lambda x: x[0])

    # ── Find free windows ─────────────────────────────────────────────────
    window_start = day_obj.replace(hour=VENUE_OPEN_HOUR,  minute=0, second=0, microsecond=0)
    window_end   = day_obj.replace(hour=VENUE_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    req_delta    = timedelta(hours=duration_h)

    free_slots = []
    cursor     = window_start

    for blk_start, blk_end, _ in occupied:
        # Clip to venue operating window
        blk_start = max(blk_start, window_start)
        blk_end   = min(blk_end,   window_end)
        if blk_start > cursor and (blk_start - cursor) >= req_delta:
            free_slots.append((cursor, blk_start))
        cursor = max(cursor, blk_end)

    if cursor < window_end and (window_end - cursor) >= req_delta:
        free_slots.append((cursor, window_end))

    # ── Format response ───────────────────────────────────────────────────
    dur_label = f"{int(duration_h)}h" if duration_h == int(duration_h) else f"{duration_h}h"
    header    = (
        f"🔍 *Free slots — {venue_query}*\n"
        f"   {day_label}  |  min {dur_label} window\n"
        f"   _(venue hours {VENUE_OPEN_HOUR}:00 – {VENUE_CLOSE_HOUR}:00, "
        f"{SETUP_BUFFER_MIN}-min buffer around each match)_\n"
    )

    # Booked block summary
    if fixtures:
        booked = "\n*IHG bookings at this venue:*\n"
        for f in fixtures:
            try:
                t = datetime.strptime(f["match_dt"], "%Y-%m-%d %H:%M").strftime("%-I:%M %p")
            except ValueError:
                t = f["match_dt"]
            booked += f"  🔴 {t}  {f['sport']}: {f['hall_a']} vs {f['hall_b']}\n"
    else:
        booked = "\n_No IHG fixtures entered for this venue on this day._\n"

    # Free windows
    if free_slots:
        free = f"\n✅ *Available windows (≥ {dur_label}):*\n"
        for s, e in free_slots:
            gap_h   = (e - s).total_seconds() / 3600
            gap_lbl = f"{int(gap_h)}h" if gap_h == int(gap_h) else f"{gap_h:.1f}h"
            free   += f"  🟢 {s.strftime('%-I:%M %p')} – {e.strftime('%-I:%M %p')}  ({gap_lbl} free)\n"
    else:
        free = f"\n❌ No {dur_label}+ window available on this day.\n"

    caveat = (
        "\n⚠️ _IHG schedule only. Verify NUS-wide availability via "
        "REBOKS or SUU before booking._"
    )

    await update.message.reply_text(
        header + booked + free + caveat,
        parse_mode=ParseMode.MARKDOWN,
    )
