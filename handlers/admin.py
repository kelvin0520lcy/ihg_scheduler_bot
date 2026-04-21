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
  /removefixture <id>
  /postpone     <id> [reason]
  /cancelfix    <id> [reason]
  /reschedule   <id> | <YYYY-MM-DD HH:MM> [| <new_venue>]
  /updatevenue  <id> | <new venue>
  /listfixtures [sport|hall|all]
  /checkclashes
  /announce     <message>
  /addadmin     <user_id>
  /importcsv    (paste CSV in next message — see /importhelp)
"""

import io
import csv
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
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
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: '{raw}'. Use YYYY-MM-DD HH:MM")


# ── /addfixture ───────────────────────────────────────────────────────────────

@admin_only
async def add_fixture(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /addfixture <sport> | <hall_a> | <hall_b> | <venue> | <YYYY-MM-DD HH:MM>

    Example:
      /addfixture Badminton | Eusoff | Kent Ridge | MPSH 1 | 2025-02-05 19:00

    ⚠️  Only enter a fixture AFTER receiving SUU venue confirmation.
    """
    text = " ".join(ctx.args) if ctx.args else ""
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

    sport, hall_a, hall_b, venue, raw_dt = parts
    try:
        match_dt = _parse_dt(raw_dt)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    fixture_id = db.add_fixture(sport, hall_a, hall_b, venue, match_dt)
    await update.message.reply_text(
        f"✅ Fixture added (ID: {fixture_id})\n\n"
        f"{fmt_fixture(db.get_fixture(fixture_id), show_id=True)}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /removefixture ────────────────────────────────────────────────────────────

@admin_only
async def remove_fixture(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /removefixture <id>"""
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: `/removefixture <id>`", parse_mode=ParseMode.MARKDOWN)
        return
    fid = int(ctx.args[0])
    f = db.get_fixture(fid)
    if not f:
        await update.message.reply_text(f"❌ No fixture with ID {fid}.")
        return
    db.delete_fixture(fid)
    await update.message.reply_text(f"🗑 Fixture {fid} ({f['sport']}: {f['hall_a']} vs {f['hall_b']}) deleted.")


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
      Badminton,Eusoff,Kent Ridge,MPSH 1,2025-02-05 19:00
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
        sport, hall_a, hall_b, venue, raw_dt = [c.strip() for c in row[:5]]
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
