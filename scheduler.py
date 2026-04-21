"""
scheduler.py – Background reminder jobs.

The scheduler runs every 5 minutes and checks whether any fixture is
within the reminder window (±5 min of 24 h or 2 h before match start).
It then sends a notification to all subscribers of that fixture's halls/sport.

This approach is robust: even if the bot restarts, it will catch missed
reminders on the next poll cycle.
"""

import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telegram.constants import ParseMode

import database as db
from config import REMINDER_INTERVALS, TIMEZONE

logger = logging.getLogger(__name__)
TZ = pytz.timezone(TIMEZONE)


def _format_reminder(fixture, hours_before: int) -> str:
    try:
        dt = datetime.strptime(fixture["match_dt"], "%Y-%m-%d %H:%M")
        time_str = dt.strftime("%-I:%M %p, %-d %b %Y")
    except ValueError:
        time_str = fixture["match_dt"]

    if hours_before == 24:
        heading = "⏰ *Match reminder — tomorrow!*"
    elif hours_before == 2:
        heading = "🚨 *Match in 2 hours — get ready!*"
    else:
        heading = f"⏰ *Match reminder ({hours_before}h away)*"

    return (
        f"{heading}\n\n"
        f"🏆 *{fixture['sport']}*\n"
        f"   {fixture['hall_a']} vs {fixture['hall_b']}\n"
        f"   📅 {time_str}\n"
        f"   📍 {fixture['venue']}\n\n"
        "Good luck! 💪"
    )


async def _send_reminders(bot) -> None:
    """Check all reminder intervals and send any that are due."""
    for hours in REMINDER_INTERVALS:
        fixtures = db.get_fixtures_needing_reminder(hours)
        for f in fixtures:
            recipients = db.get_subscribers_for_fixture(f)
            if not recipients:
                db.mark_reminder_sent(f["id"], hours)
                continue

            message = _format_reminder(f, hours)
            sent = 0
            for chat_id in recipients:
                try:
                    await bot.send_message(chat_id, message, parse_mode=ParseMode.MARKDOWN)
                    sent += 1
                except Exception as e:
                    logger.warning("Failed to send reminder to %s: %s", chat_id, e)

            db.mark_reminder_sent(f["id"], hours)
            logger.info(
                "Reminder (%dh) for fixture %d sent to %d recipients.",
                hours, f["id"], sent,
            )


def setup_scheduler(application) -> None:
    """Attach the APScheduler to the Telegram Application's event loop."""
    scheduler = AsyncIOScheduler(timezone=TZ)

    # Store the Application on the scheduler so the job can access the bot
    scheduler.add_job(
        lambda: application.create_task(_send_reminders(application.bot)),
        trigger=IntervalTrigger(minutes=5),
        id="reminder_poll",
        replace_existing=True,
        name="IHG reminder poller",
    )

    application.job_queue  # ensure job_queue is initialised (triggers PTB's loop setup)
    scheduler.start()
    logger.info("Reminder scheduler started (polling every 5 minutes).")
