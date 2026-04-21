"""
handlers/admin.py – Commands restricted to Scheduling ICs / admins.

Real-world workflow reminder:
  1. Book venue with NUS SUU (submit booking form ≥2 weeks before the match).
  2. Wait for SUU confirmation email.
  3. Only then use /addfixture to enter the confirmed slot here.
  4. Use /checkclashes to verify no IHG internal conflicts.
  5. If NUS later bumps your slot, use /reschedule or /postpone, then /announce.

Commands:
  /addfixture   <sport> | <hall_a> | <hall_b> | <venue> | <YYYY-MM-DD HH:MM>
  /addschedule  guided wizard (or one-line format)
  /removefixture <id>
  /postpone     <id> [reason]
  /cancelfix    <id> [reason]
  /reschedule   <id> | <YYYY-MM-DD HH:MM> [| <new_venue>]
  /changeschedule <id> | <YYYY-MM-DD HH:MM> [| <new_venue>]
  /overallschedule [days]
  /updatevenue  <id> | <new venue>
  /listfixtures [sport|hall|all]
  /checkclashes
  /announce     <message>
  /addadmin     <user_id>
  /importcsv    (paste CSV in next message — see /importhelp)
"""

import io
import csv
import calendar
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.constants import ParseMode

import database as db
from config import HALLS, VENUES, canonicalize_sport, sport_options
from handlers.utils import fmt_fixture, chunk


# ── Auth guard ────────────────────────────────────────────────────────────────

def admin_only(fn):
    """Decorator: reject non-admins with a clear message."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not db.is_admin(update.effective_chat.id):
            await update.message.reply_text(
                "🔒 This command is for Scheduling ICs only.\n"
                "Contact an existing admin to be added."
            )
            return
        return await fn(update, ctx)
    return wrapper


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_dt(raw: str) -> str:
    """Parse 'YYYY-MM-DD HH:MM' or 'DD/MM/YYYY HH:MM' → 'YYYY-MM-DD HH:MM'."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H%M", "%Y-%m-%d %H.%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: '{raw}'. Use YYYY-MM-DD HH:MM")


ADD_SPORT, ADD_HALL_A, ADD_HALL_B, ADD_VENUE, ADD_DATE_PICK, ADD_TIME_PICK = range(6)
CH_SPORT, CH_FIXTURE, CH_DATE_PICK, CH_TIME_PICK, CH_VENUE = range(20, 25)
RM_PICK, RM_CONFIRM_ALL = range(30, 32)
OV_MODE = 40


def _calendar_markup(year: int, month: int) -> InlineKeyboardMarkup:
    cal = calendar.monthcalendar(year, month)
    rows = [[InlineKeyboardButton(f"{calendar.month_name[month]} {year}", callback_data="adcal:noop")]]
    rows.append(
        [
            InlineKeyboardButton("Mo", callback_data="adcal:noop"),
            InlineKeyboardButton("Tu", callback_data="adcal:noop"),
            InlineKeyboardButton("We", callback_data="adcal:noop"),
            InlineKeyboardButton("Th", callback_data="adcal:noop"),
            InlineKeyboardButton("Fr", callback_data="adcal:noop"),
            InlineKeyboardButton("Sa", callback_data="adcal:noop"),
            InlineKeyboardButton("Su", callback_data="adcal:noop"),
        ]
    )
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="adcal:noop"))
            else:
                row.append(
                    InlineKeyboardButton(str(day), callback_data=f"adcal:day:{year:04d}-{month:02d}-{day:02d}")
                )
        rows.append(row)

    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year
    rows.append(
        [
            InlineKeyboardButton("◀", callback_data=f"adcal:nav:{prev_year}:{prev_month}"),
            InlineKeyboardButton("Cancel", callback_data="adcal:cancel"),
            InlineKeyboardButton("▶", callback_data=f"adcal:nav:{next_year}:{next_month}"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _sport_keyboard() -> ReplyKeyboardMarkup:
    opts = sport_options()
    rows = []
    for i in range(0, len(opts), 2):
        rows.append(opts[i:i + 2])
    rows.append(["Cancel"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def _time_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        ["08:00", "09:00", "10:00"],
        ["11:00", "12:00", "13:00"],
        ["14:00", "15:00", "16:00"],
        ["17:00", "18:00", "19:00"],
        ["20:00", "21:00", "Cancel"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def _fixture_short_label(fixture) -> str:
    return f"#{fixture['id']} {fixture['sport']} | {fixture['match_dt']}"


def _calendar_text(fixtures: list, days: int) -> str:
    by_date = {}
    for f in fixtures:
        d, t = f["match_dt"].split(" ")
        by_date.setdefault(d, []).append((t, f))
    lines = [f"🗓 *Overall Schedule Calendar — next {days} day(s)*"]
    for d in sorted(by_date.keys()):
        lines.append(f"\n*{d}*")
        for t, f in sorted(by_date[d], key=lambda x: x[0]):
            lines.append(f"`{t}`  {f['sport']}  ({f['hall_a']} vs {f['hall_b']})")
    return "\n".join(lines)


def _save_fixture_and_report(
    *,
    sport: str,
    hall_a: str,
    hall_b: str,
    venue: str,
    match_dt: str,
) -> str:
    fixture_id = db.add_fixture(sport, hall_a, hall_b, venue, match_dt)
    fixture = db.get_fixture(fixture_id)
    venue_clashes, hall_clashes = db.get_fixture_clashes(fixture_id)
    clash_text = _format_fixture_clashes(fixture_id, venue_clashes, hall_clashes)
    return f"✅ Schedule added.\n\n{fmt_fixture(fixture, show_id=True)}\n\n{clash_text}"


# ── /addfixture ───────────────────────────────────────────────────────────────

@admin_only
async def add_fixture(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /addfixture <sport> | <hall_a> | <hall_b> | <venue> | <YYYY-MM-DD HH:MM>

    Example:
      /addfixture Badminton (M) | Eusoff | Kent Ridge | MPSH 1 | 2025-02-05 19:00

    ⚠️  Only enter a fixture AFTER receiving SUU venue confirmation.
    """
    text = " ".join(ctx.args) if ctx.args else ""
    if not text.strip():
        await update.message.reply_text("Opening guided add flow...")
        await add_schedule(update, ctx)
        return
    parts = [p.strip() for p in text.split("|")]

    if len(parts) != 5:
        await update.message.reply_text(
            "❌ Wrong format. Use:\n"
            "`/addfixture <sport> | <hall_a> | <hall_b> | <venue> | <YYYY-MM-DD HH:MM>`\n\n"
            "Example:\n"
            "`/addfixture Badminton | Eusoff | Kent Ridge | MPSH 1 | 2025-02-05 19:00`\n\n"
            "⚠️ Only enter after receiving SUU email confirmation for the venue slot.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw_sport, hall_a, hall_b, venue, raw_dt = parts
    sport = canonicalize_sport(raw_sport)
    if not sport:
        await update.message.reply_text(
            "❌ Invalid sport. Use canonical names like `Badminton (M)`, `Badminton (F)`, or `Softball`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        match_dt = _parse_dt(raw_dt)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    await update.message.reply_text(
        _save_fixture_and_report(sport=sport, hall_a=hall_a, hall_b=hall_b, venue=venue, match_dt=match_dt),
        parse_mode=ParseMode.MARKDOWN,
    )


def _format_fixture_clashes(fixture_id: int, venue_clashes: list, hall_clashes: list) -> str:
    """Render clash details for a specific fixture."""
    if not venue_clashes and not hall_clashes:
        return "✅ No clash detected for this schedule."

    lines = [f"⚠️ *Clash check for fixture #{fixture_id}:*"]
    if venue_clashes:
        lines.append("*🔴 Venue clashes* (same venue, too close):")
        for c in venue_clashes:
            lines.append(
                f"  • with #{c['id_other']} — {c['venue_other']} @ {c['dt_other']} "
                f"({c['sport_other']}: {c['other_ha']} vs {c['other_hb']})"
            )
    if hall_clashes:
        lines.append("*🟡 Hall clashes* (same hall, too close):")
        for c in hall_clashes:
            lines.append(
                f"  • with #{c['id_other']} @ {c['dt_other']} "
                f"({c['sport_other']}: {c['other_ha']} vs {c['other_hb']})"
            )
    return "\n".join(lines)


@admin_only
async def add_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage:
      /addschedule
      /addschedule <sport> | <hall_a> | <hall_b> | <venue> | <YYYY-MM-DD HH:MM>
      (sport must be canonical, e.g. Badminton (M)/(F), Softball)

    Adds a fixture and immediately runs clash checks for that fixture.
    """
    text = " ".join(ctx.args) if ctx.args else ""
    if not text.strip():
        ctx.user_data["addschedule"] = {}
        await update.message.reply_text(
            "Let's add a schedule step-by-step.\n\nStep 1/5: Select the *sport*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_sport_keyboard(),
        )
        return ADD_SPORT

    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 5:
        await update.message.reply_text(
            "❌ Wrong format. Use:\n"
            "`/addschedule <sport> | <hall_a> | <hall_b> | <venue> | <YYYY-MM-DD HH:MM>`\n\n"
            "Or simply type `/addschedule` to use the guided mode.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    raw_sport, hall_a, hall_b, venue, raw_dt = parts
    sport = canonicalize_sport(raw_sport)
    if not sport:
        await update.message.reply_text(
            "❌ Invalid sport. Use canonical names like `Badminton (M)`, `Badminton (F)`, or `Softball`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END
    try:
        match_dt = _parse_dt(raw_dt)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return ConversationHandler.END

    await update.message.reply_text(
        _save_fixture_and_report(sport=sport, hall_a=hall_a, hall_b=hall_b, venue=venue, match_dt=match_dt),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


@admin_only
async def add_schedule_sport(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw_sport = (update.message.text or "").strip()
    if raw_sport.lower() == "cancel":
        return await add_schedule_cancel(update, ctx)
    sport = canonicalize_sport(raw_sport)
    if not sport:
        await update.message.reply_text("Please choose a valid sport from buttons.", reply_markup=_sport_keyboard())
        return ADD_SPORT

    ctx.user_data.setdefault("addschedule", {})["sport"] = sport
    hall_buttons = [[h] for h in HALLS] + [["Cancel"]]
    await update.message.reply_text(
        "Step 2/5: Select *Hall A*.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(hall_buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return ADD_HALL_A


@admin_only
async def add_schedule_hall_a(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    hall_a = (update.message.text or "").strip()
    if hall_a.lower() == "cancel":
        return await add_schedule_cancel(update, ctx)
    if hall_a not in HALLS:
        await update.message.reply_text("Please choose Hall A from the list buttons.")
        return ADD_HALL_A

    ctx.user_data.setdefault("addschedule", {})["hall_a"] = hall_a
    hall_buttons = [[h] for h in HALLS if h != hall_a] + [["Cancel"]]
    await update.message.reply_text(
        "Step 3/5: Select *Hall B*.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(hall_buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return ADD_HALL_B


@admin_only
async def add_schedule_hall_b(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    hall_b = (update.message.text or "").strip()
    if hall_b.lower() == "cancel":
        return await add_schedule_cancel(update, ctx)

    data = ctx.user_data.setdefault("addschedule", {})
    hall_a = data.get("hall_a", "")
    if hall_b not in HALLS or hall_b == hall_a:
        await update.message.reply_text("Please choose a different Hall B from the list buttons.")
        return ADD_HALL_B

    data["hall_b"] = hall_b
    venue_buttons = [[v] for v in VENUES] + [["Cancel"]]
    await update.message.reply_text(
        "Step 4/5: Select venue (or type a custom venue name).",
        reply_markup=ReplyKeyboardMarkup(venue_buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return ADD_VENUE


@admin_only
async def add_schedule_venue(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    venue = (update.message.text or "").strip()
    if venue.lower() == "cancel":
        return await add_schedule_cancel(update, ctx)
    if not venue:
        await update.message.reply_text("Please select or type a venue.")
        return ADD_VENUE

    ctx.user_data.setdefault("addschedule", {})["venue"] = venue
    now = datetime.now()
    await update.message.reply_text(
        "Step 5/5: Pick the *date* from the calendar.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text("📅 Select match date:", reply_markup=_calendar_markup(now.year, now.month))
    return ADD_DATE_PICK


async def add_schedule_date_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer(cache_time=2)
    data = query.data or ""

    if data == "adcal:noop":
        return ADD_DATE_PICK

    if data == "adcal:cancel":
        ctx.user_data.pop("addschedule", None)
        await query.edit_message_text("Add schedule cancelled.")
        return ConversationHandler.END

    if data.startswith("adcal:nav:"):
        _, _, y, m = data.split(":")
        await query.edit_message_reply_markup(reply_markup=_calendar_markup(int(y), int(m)))
        return ADD_DATE_PICK

    if data.startswith("adcal:day:"):
        selected_date = data.split(":", 2)[2]
        ctx.user_data.setdefault("addschedule", {})["date"] = selected_date
        await query.edit_message_text(f"📅 Date selected: {selected_date}")
        await query.message.reply_text(
            "Now pick a *time* (or type `HH:MM`, e.g. `19:30`):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_time_keyboard(),
        )
        return ADD_TIME_PICK

    return ADD_DATE_PICK


async def add_schedule_time_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw_time = (update.message.text or "").strip()
    if raw_time.lower() == "cancel":
        return await add_schedule_cancel(update, ctx)

    try:
        parsed_time = datetime.strptime(raw_time, "%H:%M").strftime("%H:%M")
    except ValueError:
        await update.message.reply_text("Please send time in `HH:MM` format, e.g. `19:00`.", parse_mode=ParseMode.MARKDOWN)
        return ADD_TIME_PICK

    selected_date = ctx.user_data.get("addschedule", {}).get("date")
    if not selected_date:
        await update.message.reply_text("Date not found. Please run /addschedule again.")
        return ConversationHandler.END

    match_dt = f"{selected_date} {parsed_time}"
    data = ctx.user_data.get("addschedule", {})
    sport = data.get("sport", "")
    hall_a = data.get("hall_a", "")
    hall_b = data.get("hall_b", "")
    venue = data.get("venue", "")

    fixture_id = db.add_fixture(sport, hall_a, hall_b, venue, match_dt)
    fixture = db.get_fixture(fixture_id)
    venue_clashes, hall_clashes = db.get_fixture_clashes(fixture_id)
    clash_text = _format_fixture_clashes(fixture_id, venue_clashes, hall_clashes)
    await update.message.reply_text(
        f"✅ Schedule added.\n\n{fmt_fixture(fixture, show_id=True)}\n\n{clash_text}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    ctx.user_data.pop("addschedule", None)
    return ConversationHandler.END


@admin_only
async def add_schedule_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("addschedule", None)
    await update.message.reply_text("Add schedule cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def addschedule_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("addschedule", add_schedule)],
        states={
            ADD_SPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_schedule_sport)],
            ADD_HALL_A: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_schedule_hall_a)],
            ADD_HALL_B: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_schedule_hall_b)],
            ADD_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_schedule_venue)],
            ADD_DATE_PICK: [CallbackQueryHandler(add_schedule_date_pick, pattern=r"^adcal:")],
            ADD_TIME_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_schedule_time_text)],
        },
        fallbacks=[CommandHandler("cancel", add_schedule_cancel)],
        name="addschedule_flow",
        persistent=False,
    )


@admin_only
async def change_schedule_pick_fixture(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text.lower() == "cancel":
        return await change_schedule_cancel(update, ctx)

    fid_txt = text.split(" ", 1)[0].lstrip("#")
    if not fid_txt.isdigit():
        await update.message.reply_text("Please choose a fixture button.")
        return CH_FIXTURE

    fid = int(fid_txt)
    fixture = db.get_fixture(fid)
    if not fixture:
        await update.message.reply_text("Fixture not found, choose again.")
        return CH_FIXTURE

    flow = ctx.user_data.setdefault("changeschedule", {})
    flow["fixture_id"] = fid
    flow["sport"] = fixture["sport"]
    now = datetime.now()
    await update.message.reply_text("Step 3/5: Pick new *date* from calendar.", parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text("📅 Select new date:", reply_markup=_calendar_markup(now.year, now.month))
    return CH_DATE_PICK


async def change_schedule_date_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer(cache_time=2)
    data = query.data or ""

    if data == "adcal:noop":
        return CH_DATE_PICK
    if data == "adcal:cancel":
        ctx.user_data.pop("changeschedule", None)
        await query.edit_message_text("Change schedule cancelled.")
        return ConversationHandler.END
    if data.startswith("adcal:nav:"):
        _, _, y, m = data.split(":")
        await query.edit_message_reply_markup(reply_markup=_calendar_markup(int(y), int(m)))
        return CH_DATE_PICK
    if data.startswith("adcal:day:"):
        selected_date = data.split(":", 2)[2]
        ctx.user_data.setdefault("changeschedule", {})["date"] = selected_date
        await query.edit_message_text(f"📅 New date selected: {selected_date}")
        await query.message.reply_text(
            "Step 4/5: Select new *time* (or type `HH:MM`).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_time_keyboard(),
        )
        return CH_TIME_PICK
    return CH_DATE_PICK


@admin_only
async def change_schedule_time_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw_time = (update.message.text or "").strip()
    if raw_time.lower() == "cancel":
        return await change_schedule_cancel(update, ctx)
    try:
        parsed_time = datetime.strptime(raw_time, "%H:%M").strftime("%H:%M")
    except ValueError:
        await update.message.reply_text("Please send time in `HH:MM` format, e.g. `19:00`.", parse_mode=ParseMode.MARKDOWN)
        return CH_TIME_PICK

    flow = ctx.user_data.setdefault("changeschedule", {})
    flow["time"] = parsed_time
    venue_buttons = [["Keep current venue"]] + [[v] for v in VENUES] + [["Cancel"]]
    await update.message.reply_text(
        "Step 5/5: Select venue option.",
        reply_markup=ReplyKeyboardMarkup(venue_buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return CH_VENUE


@admin_only
async def change_schedule_pick_venue(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    choice = (update.message.text or "").strip()
    if choice.lower() == "cancel":
        return await change_schedule_cancel(update, ctx)

    flow = ctx.user_data.get("changeschedule", {})
    fid = flow.get("fixture_id")
    selected_date = flow.get("date")
    selected_time = flow.get("time")
    if not fid or not selected_date or not selected_time:
        await update.message.reply_text("Session expired. Please run /changeschedule again.", reply_markup=ReplyKeyboardRemove())
        ctx.user_data.pop("changeschedule", None)
        return ConversationHandler.END

    existing = db.get_fixture(fid)
    if not existing:
        await update.message.reply_text("Fixture not found.", reply_markup=ReplyKeyboardRemove())
        ctx.user_data.pop("changeschedule", None)
        return ConversationHandler.END

    new_venue = None if choice == "Keep current venue" else choice
    new_dt = f"{selected_date} {selected_time}"
    db.reschedule_fixture(fid, new_dt, new_venue)
    updated = db.get_fixture(fid)
    venue_clashes, hall_clashes = db.get_fixture_clashes(fid)
    clash_text = _format_fixture_clashes(fid, venue_clashes, hall_clashes)
    await update.message.reply_text(
        f"✅ Schedule updated.\n\n{fmt_fixture(updated, show_id=True)}\n\n{clash_text}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    ctx.user_data.pop("changeschedule", None)
    return ConversationHandler.END


@admin_only
async def change_schedule_pick_sport(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    sport = (update.message.text or "").strip()
    if sport.lower() == "cancel":
        return await change_schedule_cancel(update, ctx)
    fixtures = db.get_fixtures_by_sport(sport)
    if not fixtures:
        await update.message.reply_text("No upcoming fixtures for that sport. Pick another sport.")
        return CH_SPORT

    ctx.user_data.setdefault("changeschedule", {})["sport"] = sport
    preview = [f"📋 *Current schedule — {sport}*"]
    for f in fixtures[:10]:
        preview.append(f"• #{f['id']} {f['match_dt']} — {f['hall_a']} vs {f['hall_b']} @ {f['venue']}")
    await update.message.reply_text("\n".join(preview), parse_mode=ParseMode.MARKDOWN)

    rows = [[_fixture_short_label(f)] for f in fixtures[:12]] + [["Cancel"]]
    await update.message.reply_text(
        "Step 2/5: Select the fixture to change.",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True),
    )
    return CH_FIXTURE


@admin_only
async def change_schedule_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("changeschedule", None)
    await update.message.reply_text("Change schedule cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def changeschedule_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("changeschedule", change_schedule)],
        states={
            CH_SPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_schedule_pick_sport)],
            CH_FIXTURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_schedule_pick_fixture)],
            CH_DATE_PICK: [CallbackQueryHandler(change_schedule_date_pick, pattern=r"^adcal:")],
            CH_TIME_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_schedule_time_pick)],
            CH_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_schedule_pick_venue)],
        },
        fallbacks=[CommandHandler("cancel", change_schedule_cancel)],
        name="changeschedule_flow",
        persistent=False,
    )


# ── /removefixture ────────────────────────────────────────────────────────────

@admin_only
async def remove_fixture(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /removefixture <id>"""
    if not ctx.args:
        fixtures = db.get_upcoming_fixtures(days=45)
        if not fixtures:
            await update.message.reply_text("No fixtures available to remove.")
            return ConversationHandler.END
        rows = [[_fixture_short_label(f)] for f in fixtures[:12]]
        rows.append(["🧨 Remove ALL schedules"])
        rows.append(["Cancel"])
        await update.message.reply_text(
            "Select a fixture to remove, or choose remove all schedules.",
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True),
        )
        return RM_PICK
    if not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: `/removefixture <id>`", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    fid = int(ctx.args[0])
    f = db.get_fixture(fid)
    if not f:
        await update.message.reply_text(f"❌ No fixture with ID {fid}.")
        return ConversationHandler.END
    db.delete_fixture(fid)
    await update.message.reply_text(f"🗑 Fixture {fid} ({f['sport']}: {f['hall_a']} vs {f['hall_b']}) deleted.")
    return ConversationHandler.END


@admin_only
async def remove_fixture_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text.lower() == "cancel":
        return await remove_fixture_cancel(update, ctx)
    if text == "🧨 Remove ALL schedules":
        await update.message.reply_text(
            "Confirm remove all schedules?",
            reply_markup=ReplyKeyboardMarkup([["YES remove all"], ["Cancel"]], resize_keyboard=True, one_time_keyboard=True),
        )
        return RM_CONFIRM_ALL

    fid_txt = text.split(" ", 1)[0].lstrip("#")
    if not fid_txt.isdigit():
        await update.message.reply_text("Please choose a fixture button.")
        return RM_PICK
    fid = int(fid_txt)
    f = db.get_fixture(fid)
    if not f:
        await update.message.reply_text("Fixture not found, choose again.")
        return RM_PICK
    db.delete_fixture(fid)
    await update.message.reply_text(
        f"🗑 Fixture {fid} ({f['sport']}: {f['hall_a']} vs {f['hall_b']}) deleted.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


@admin_only
async def remove_fixture_confirm_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text.lower() == "cancel":
        return await remove_fixture_cancel(update, ctx)
    if text != "YES remove all":
        await update.message.reply_text("Please confirm with `YES remove all` or Cancel.", parse_mode=ParseMode.MARKDOWN)
        return RM_CONFIRM_ALL
    deleted = db.delete_all_fixtures()
    await update.message.reply_text(f"🧨 Removed all schedules. Deleted {deleted} fixture(s).", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


@admin_only
async def remove_fixture_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Remove fixture cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def removefixture_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("removefixture", remove_fixture)],
        states={
            RM_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_fixture_pick)],
            RM_CONFIRM_ALL: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_fixture_confirm_all)],
        },
        fallbacks=[CommandHandler("cancel", remove_fixture_cancel)],
        name="removefixture_flow",
        persistent=False,
    )


# ── /postpone ─────────────────────────────────────────────────────────────────

@admin_only
async def postpone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /postpone <id> [reason]

    Marks the fixture as postponed. Use /reschedule once a new slot is confirmed.
    Common reason: 'Venue rebooked by varsity team — pending new SUU slot'
    """
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: `/postpone <id> [reason]`", parse_mode=ParseMode.MARKDOWN)
        return
    fid    = int(ctx.args[0])
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""
    f = db.get_fixture(fid)
    if not f:
        await update.message.reply_text(f"❌ No fixture with ID {fid}.")
        return

    db.update_status(fid, "postponed", reason)

    # Notify subscribers
    notice = (
        f"🟡 *POSTPONED — {f['sport']}*\n\n"
        f"   {f['hall_a']} vs {f['hall_b']}\n"
        f"   Originally: {f['match_dt']} @ {f['venue']}\n"
        + (f"   Reason: {reason}\n" if reason else "")
        + "\nA new schedule will be announced once the venue is confirmed with SUU."
    )
    await _broadcast_to_fixture_subscribers(ctx, f, notice)
    await update.message.reply_text(f"✅ Fixture {fid} marked as postponed.\n{notice}", parse_mode=ParseMode.MARKDOWN)


# ── /cancelfix ────────────────────────────────────────────────────────────────

@admin_only
async def cancel_fixture(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /cancelfix <id> [reason]"""
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: `/cancelfix <id> [reason]`", parse_mode=ParseMode.MARKDOWN)
        return
    fid    = int(ctx.args[0])
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""
    f = db.get_fixture(fid)
    if not f:
        await update.message.reply_text(f"❌ No fixture with ID {fid}.")
        return

    db.update_status(fid, "cancelled", reason)

    notice = (
        f"🔴 *CANCELLED — {f['sport']}*\n\n"
        f"   {f['hall_a']} vs {f['hall_b']}\n"
        f"   Was: {f['match_dt']} @ {f['venue']}\n"
        + (f"   Reason: {reason}" if reason else "")
    )
    await _broadcast_to_fixture_subscribers(ctx, f, notice)
    await update.message.reply_text(f"✅ Fixture {fid} cancelled.\n{notice}", parse_mode=ParseMode.MARKDOWN)


# ── /reschedule ───────────────────────────────────────────────────────────────

@admin_only
async def reschedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /reschedule <id> | <YYYY-MM-DD HH:MM> [| <new venue>]

    Only use after receiving the new SUU venue confirmation.

    Example:
      /reschedule 7 | 2025-02-12 19:00
      /reschedule 7 | 2025-02-12 19:00 | MPSH 2
    """
    text = " ".join(ctx.args) if ctx.args else ""
    parts = [p.strip() for p in text.split("|")]

    if len(parts) < 2 or not parts[0].isdigit():
        await update.message.reply_text(
            "Usage: `/reschedule <id> | <YYYY-MM-DD HH:MM> [| <new venue>]`\n\n"
            "Example:\n`/reschedule 7 | 2025-02-12 19:00 | MPSH 2`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    fid       = int(parts[0])
    new_venue = parts[2] if len(parts) >= 3 else None
    try:
        new_dt = _parse_dt(parts[1])
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    f = db.get_fixture(fid)
    if not f:
        await update.message.reply_text(f"❌ No fixture with ID {fid}.")
        return

    db.reschedule_fixture(fid, new_dt, new_venue)

    updated = db.get_fixture(fid)
    notice  = (
        f"🔄 *RESCHEDULED — {f['sport']}*\n\n"
        f"   {f['hall_a']} vs {f['hall_b']}\n"
        f"   ✅ New time: {new_dt}\n"
        f"   📍 Venue: {updated['venue']}"
    )
    await _broadcast_to_fixture_subscribers(ctx, f, notice)
    await update.message.reply_text(f"✅ Fixture {fid} rescheduled.\n\n{notice}", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def change_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /changeschedule <id> | <YYYY-MM-DD HH:MM> [| <new venue>]

    Updates schedule time/venue and immediately runs clash checks.
    """
    text = " ".join(ctx.args) if ctx.args else ""
    if not text.strip():
        sports = db.get_distinct_sports()
        if not sports:
            await update.message.reply_text("No upcoming fixtures to change.")
            return ConversationHandler.END
        rows = [[s] for s in sports[:20]]
        rows.append(["Cancel"])
        ctx.user_data["changeschedule"] = {}
        await update.message.reply_text(
            "Step 1/5: Select a sport first.",
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True),
        )
        return CH_SPORT

    parts = [p.strip() for p in text.split("|")]

    if len(parts) < 2 or not parts[0].isdigit():
        await update.message.reply_text(
            "Usage: `/changeschedule <id> | <YYYY-MM-DD HH:MM> [| <new venue>]`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    fid = int(parts[0])
    new_venue = parts[2] if len(parts) >= 3 else None
    try:
        new_dt = _parse_dt(parts[1])
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return ConversationHandler.END

    existing = db.get_fixture(fid)
    if not existing:
        await update.message.reply_text(f"❌ No fixture with ID {fid}.")
        return

    db.reschedule_fixture(fid, new_dt, new_venue)
    updated = db.get_fixture(fid)
    venue_clashes, hall_clashes = db.get_fixture_clashes(fid)
    clash_text = _format_fixture_clashes(fid, venue_clashes, hall_clashes)
    await update.message.reply_text(
        f"✅ Schedule updated.\n\n{fmt_fixture(updated, show_id=True)}\n\n{clash_text}",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# ── /updatevenue ─────────────────────────────────────────────────────────────

@admin_only
async def update_venue(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /updatevenue <id> | <new venue>

    Use this when NUS assigns a different hall/court after you submitted the
    SUU booking but before the match.

    Example: /updatevenue 5 | MPSH 2
    """
    text = " ".join(ctx.args) if ctx.args else ""
    parts = [p.strip() for p in text.split("|")]

    if len(parts) != 2 or not parts[0].isdigit():
        await update.message.reply_text(
            "Usage: `/updatevenue <id> | <new venue>`\nExample: `/updatevenue 5 | MPSH 2`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    fid       = int(parts[0])
    new_venue = parts[1]
    f = db.get_fixture(fid)
    if not f:
        await update.message.reply_text(f"❌ No fixture with ID {fid}.")
        return

    db.update_venue(fid, new_venue)
    notice = (
        f"📍 *VENUE CHANGE — {f['sport']}*\n\n"
        f"   {f['hall_a']} vs {f['hall_b']}\n"
        f"   {f['match_dt']}\n"
        f"   Old venue: {f['venue']}\n"
        f"   ✅ New venue: {new_venue}"
    )
    await _broadcast_to_fixture_subscribers(ctx, f, notice)
    await update.message.reply_text(f"✅ Venue updated.\n\n{notice}", parse_mode=ParseMode.MARKDOWN)


# ── /listfixtures ─────────────────────────────────────────────────────────────

@admin_only
async def list_fixtures(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage:
      /listfixtures           — most recent 30 fixtures
      /listfixtures sport Badminton
      /listfixtures hall Eusoff
    """
    arg = ctx.args[0].lower() if ctx.args else ""

    if arg == "sport" and len(ctx.args) >= 2:
        sport    = " ".join(ctx.args[1:])
        fixtures = db.get_fixtures_by_sport(sport)
        title    = f"Fixtures — {sport}"
    elif arg == "hall" and len(ctx.args) >= 2:
        hall     = " ".join(ctx.args[1:])
        fixtures = db.get_all_fixtures_for_hall(hall)
        title    = f"Fixtures — {hall}"
    else:
        fixtures = db.get_all_fixtures_admin(30)
        title    = "All Fixtures (latest 30)"

    if not fixtures:
        await update.message.reply_text("No fixtures found.")
        return

    lines = [f"📋 *{title}*\n"]
    for f in fixtures:
        lines.append(fmt_fixture(f, show_id=True))

    full = "\n\n".join(lines)
    if len(full) <= 4000:
        await update.message.reply_text(full, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"📋 *{title}*", parse_mode=ParseMode.MARKDOWN)
        for batch in chunk(fixtures, 8):
            page = "\n\n".join(fmt_fixture(f, show_id=True) for f in batch)
            await update.message.reply_text(page, parse_mode=ParseMode.MARKDOWN)


@admin_only
async def overall_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /overallschedule [days]
    Example: /overallschedule 14
    """
    if not ctx.args:
        await update.message.reply_text(
            "Choose overall schedule view:",
            reply_markup=ReplyKeyboardMarkup(
                [["Calendar (30 days)"], ["List (14 days)"], ["Cancel"]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return OV_MODE

    days = 14
    calendar_mode = False
    if ctx.args:
        if ctx.args[0].lower() == "calendar":
            calendar_mode = True
            if len(ctx.args) >= 2 and ctx.args[1].isdigit():
                days = max(1, min(60, int(ctx.args[1])))
            else:
                days = 30
        elif ctx.args[0].isdigit():
            days = max(1, min(60, int(ctx.args[0])))
        else:
            await update.message.reply_text("Usage: `/overallschedule [days]` or `/overallschedule calendar [days]`", parse_mode=ParseMode.MARKDOWN)
            return ConversationHandler.END

    fixtures = db.get_upcoming_fixtures(days)
    if not fixtures:
        await update.message.reply_text(f"No scheduled fixtures in the next {days} day(s).")
        return ConversationHandler.END

    if calendar_mode:
        text = _calendar_text(fixtures, days)
        if len(text) <= 4000:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return ConversationHandler.END
        await update.message.reply_text(f"🗓 *Overall Schedule Calendar — next {days} day(s)*", parse_mode=ParseMode.MARKDOWN)
        for batch in chunk(fixtures, 20):
            page = _calendar_text(batch, days)
            await update.message.reply_text(page, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    lines = [f"🗓 *Overall Schedule — next {days} day(s)*"]
    current_date = None
    for f in fixtures:
        date_part, time_part = f["match_dt"].split(" ")
        if date_part != current_date:
            current_date = date_part
            lines.append(f"\n*{current_date}*")
        lines.append(
            f"  • {time_part} — {f['sport']}: {f['hall_a']} vs {f['hall_b']} @ {f['venue']} (#{f['id']})"
        )

    message = "\n".join(lines)
    if len(message) <= 4000:
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    await update.message.reply_text(f"🗓 *Overall Schedule — next {days} day(s)*", parse_mode=ParseMode.MARKDOWN)
    for batch in chunk(fixtures, 12):
        page_lines = []
        page_date = None
        for f in batch:
            date_part, time_part = f["match_dt"].split(" ")
            if date_part != page_date:
                page_date = date_part
                page_lines.append(f"\n*{date_part}*")
            page_lines.append(
                f"  • {time_part} — {f['sport']}: {f['hall_a']} vs {f['hall_b']} @ {f['venue']} (#{f['id']})"
            )
        await update.message.reply_text("\n".join(page_lines), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


@admin_only
async def overall_schedule_pick_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    choice = (update.message.text or "").strip()
    if choice.lower() == "cancel":
        await update.message.reply_text("Overall schedule view cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if choice == "Calendar (30 days)":
        ctx.args = ["calendar", "30"]
    elif choice == "List (14 days)":
        ctx.args = ["14"]
    else:
        await update.message.reply_text("Please choose one of the provided options.")
        return OV_MODE

    await overall_schedule(update, ctx)
    return ConversationHandler.END


@admin_only
async def overall_schedule_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Overall schedule view cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def overallschedule_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("overallschedule", overall_schedule)],
        states={OV_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, overall_schedule_pick_mode)]},
        fallbacks=[CommandHandler("cancel", overall_schedule_cancel)],
        name="overallschedule_flow",
        persistent=False,
    )


# ── /checkclashes ─────────────────────────────────────────────────────────────

@admin_only
async def check_clashes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Checks for:
      1. Venue double-booking within IHG schedule (< 90 min apart, same venue)
      2. Same hall playing two matches within 2 hours

    ⚠️  This only detects clashes WITHIN the fixtures you've entered.
    It cannot detect conflicts with NUS varsity bookings or external events.
    Always cross-check with your SUU confirmation emails.
    """
    venue_clashes = db.check_venue_clashes()
    hall_clashes  = db.check_hall_clashes()

    if not venue_clashes and not hall_clashes:
        await update.message.reply_text(
            "✅ *No clashes detected* in the current IHG schedule.\n\n"
            "Remember: this only checks within entered fixtures. "
            "External NUS venue conflicts must be verified with SUU.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = ["⚠️ *Clash Report*\n"]

    if venue_clashes:
        lines.append("*🔴 Venue double-bookings (< 90 min apart):*")
        for c in venue_clashes:
            lines.append(
                f"  IDs {c['id_a']} & {c['id_b']} — {c['venue']}\n"
                f"    #{c['id_a']}: {c['sport_a']} ({c['a_ha']} vs {c['a_hb']}) @ {c['dt_a']}\n"
                f"    #{c['id_b']}: {c['sport_b']} ({c['b_ha']} vs {c['b_hb']}) @ {c['dt_b']}"
            )

    if hall_clashes:
        lines.append("\n*🟡 Same hall, back-to-back matches (< 2 h apart):*")
        for c in hall_clashes:
            lines.append(
                f"  IDs {c['id_a']} & {c['id_b']}\n"
                f"    #{c['id_a']}: {c['sport_a']} ({c['a_ha']} vs {c['a_hb']}) @ {c['dt_a']}\n"
                f"    #{c['id_b']}: {c['sport_b']} ({c['b_ha']} vs {c['b_hb']}) @ {c['dt_b']}"
            )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── /announce ────────────────────────────────────────────────────────────────

@admin_only
async def announce(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /announce <message>

    Broadcasts a message to everyone who has at least one subscription.
    Useful for weather delays, general reminders, or ceremony info.
    """
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/announce <your message>`\n\n"
            "Broadcasts to all subscribers.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    message    = "📢 *IHG Announcement*\n\n" + " ".join(ctx.args)
    recipients = db.get_all_subscriber_ids()

    if not recipients:
        await update.message.reply_text("No subscribers found yet.")
        return

    sent = failed = 0
    for chat_id in recipients:
        try:
            await ctx.bot.send_message(chat_id, message, parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Announcement sent to {sent} subscriber(s). ({failed} failed)"
    )


# ── /addadmin ────────────────────────────────────────────────────────────────

@admin_only
async def add_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /addadmin <telegram_user_id>

    The user ID can be obtained from @userinfobot.
    """
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text(
            "Usage: `/addadmin <telegram_user_id>`\n\n"
            "The person can get their ID from @userinfobot on Telegram.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    new_admin_id = int(ctx.args[0])
    db.add_admin(new_admin_id)
    await update.message.reply_text(f"✅ User {new_admin_id} added as admin.")


# ── /importcsv ───────────────────────────────────────────────────────────────

@admin_only
async def import_csv(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Bulk import fixtures from a CSV attached as a file message.

    CSV format (no header row needed, one fixture per line):
      sport,hall_a,hall_b,venue,YYYY-MM-DD HH:MM

    Example:
      Badminton (M),Eusoff,Kent Ridge,MPSH 1,2025-02-05 19:00
      Football,Sheares,Raffles,University Field,2025-02-06 17:00

    To use: send a .csv file with this bot command as the caption.
    """
    document = update.message.document
    if not document or not document.file_name.endswith(".csv"):
        await update.message.reply_text(
            "Please attach a `.csv` file and use `/importcsv` as the caption.\n\n"
            "CSV format:\n"
            "`sport,hall_a,hall_b,venue,YYYY-MM-DD HH:MM`\n"
            "One fixture per line, no header row.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    file  = await document.get_file()
    data  = await file.download_as_bytearray()
    text  = data.decode("utf-8-sig")  # handle BOM from Excel

    reader  = csv.reader(io.StringIO(text))
    added   = []
    errors  = []

    for i, row in enumerate(reader, start=1):
        if len(row) < 5:
            errors.append(f"Row {i}: too few columns ({len(row)})")
            continue
        raw_sport, hall_a, hall_b, venue, raw_dt = [c.strip() for c in row[:5]]
        sport = canonicalize_sport(raw_sport)
        if not sport:
            errors.append(f"Row {i}: invalid sport '{raw_sport}'")
            continue
        try:
            match_dt = _parse_dt(raw_dt)
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
            continue
        fid = db.add_fixture(sport, hall_a, hall_b, venue, match_dt)
        added.append(f"  #{fid} — {sport}: {hall_a} vs {hall_b} @ {match_dt}")

    summary = f"✅ Imported {len(added)} fixture(s)."
    if added:
        summary += "\n" + "\n".join(added)
    if errors:
        summary += f"\n\n❌ {len(errors)} error(s):\n" + "\n".join(errors)

    await update.message.reply_text(summary)


# ── Internal broadcast helper ─────────────────────────────────────────────────

async def _broadcast_to_fixture_subscribers(ctx, fixture, message: str) -> None:
    """Send a message to all subscribers of a fixture's halls and sport."""
    recipients = db.get_subscribers_for_fixture(fixture)
    for chat_id in recipients:
        try:
            await ctx.bot.send_message(chat_id, message, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass  # User may have blocked the bot; silently skip
