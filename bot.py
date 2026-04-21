"""
bot.py – IHG Scheduling Bot entry point.

Run:  python bot.py
"""

import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import BOT_TOKEN
from database import init_db
from scheduler import setup_scheduler
import handlers.user  as user
import handlers.admin as admin

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _set_commands(application: Application) -> None:
    """Register the bot's command list shown in the Telegram menu."""
    await application.bot.set_my_commands([
        BotCommand("start",           "Welcome & intro"),
        BotCommand("help",            "Show all commands"),
        BotCommand("schedule",        "Fixtures for a sport — /schedule Badminton"),
        BotCommand("nextmatch",       "Next match for a hall — /nextmatch Eusoff"),
        BotCommand("venue",           "Matches at a venue — /venue MPSH 1"),
        BotCommand("upcoming",        "All matches in the next 7 days"),
        BotCommand("sports",          "List sports with fixtures"),
        BotCommand("halls",           "List all halls"),
        BotCommand("venues",          "List common NUS venues"),
        BotCommand("subscribe",       "Get reminders — /subscribe hall Eusoff"),
        BotCommand("unsubscribe",     "Remove a subscription"),
        BotCommand("dayschedule",    "All fixtures on a day — /dayschedule 2025-02-05"),
        BotCommand("freeslots",      "Free windows at a venue — /freeslots MPSH 1 | date | 2h"),
        BotCommand("mysubscriptions", "Your active subscriptions"),
        BotCommand("addschedule",      "Admin: add schedule + clash check"),
        BotCommand("changeschedule",   "Admin: change schedule + clash check"),
        BotCommand("overallschedule",  "Admin: view overall upcoming schedule"),
    ])


def main() -> None:
    # Initialise database (creates tables on first run)
    init_db()
    logger.info("Database ready.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_set_commands)
        .build()
    )

    # ── User commands ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",           user.start))
    app.add_handler(CommandHandler("help",            user.help_cmd))
    app.add_handler(CommandHandler("schedule",        user.schedule))
    app.add_handler(CommandHandler("nextmatch",       user.nextmatch))
    app.add_handler(CommandHandler("venue",           user.venue))
    app.add_handler(CommandHandler("upcoming",        user.upcoming))
    app.add_handler(CommandHandler("sports",          user.sports))
    app.add_handler(CommandHandler("halls",           user.halls))
    app.add_handler(CommandHandler("venues",          user.venues_cmd))
    app.add_handler(CommandHandler("subscribe",       user.subscribe))
    app.add_handler(CommandHandler("unsubscribe",     user.unsubscribe))
    app.add_handler(CommandHandler("mysubscriptions", user.my_subscriptions))
    app.add_handler(CommandHandler("dayschedule",     user.dayschedule))
    app.add_handler(CommandHandler("freeslots",       user.freeslots))

    # ── Admin commands ───────────────────────────────────────────────────────
    app.add_handler(CommandHandler("addfixture",    admin.add_fixture))
    app.add_handler(CommandHandler("addschedule",   admin.add_schedule))
    app.add_handler(CommandHandler("removefixture", admin.remove_fixture))
    app.add_handler(CommandHandler("postpone",      admin.postpone))
    app.add_handler(CommandHandler("cancelfix",     admin.cancel_fixture))
    app.add_handler(CommandHandler("reschedule",    admin.reschedule))
    app.add_handler(CommandHandler("changeschedule", admin.change_schedule))
    app.add_handler(CommandHandler("updatevenue",   admin.update_venue))
    app.add_handler(CommandHandler("listfixtures",  admin.list_fixtures))
    app.add_handler(CommandHandler("overallschedule", admin.overall_schedule))
    app.add_handler(CommandHandler("checkclashes",  admin.check_clashes))
    app.add_handler(CommandHandler("announce",      admin.announce))
    app.add_handler(CommandHandler("addadmin",      admin.add_admin))
    app.add_handler(CommandHandler("importcsv",     admin.import_csv))

    # Also handle /importcsv sent as a caption on a file upload
    app.add_handler(
        MessageHandler(filters.Document.ALL & filters.CaptionRegex(r"^/importcsv"), admin.import_csv)
    )

    # ── Reminder scheduler ───────────────────────────────────────────────────
    setup_scheduler(app)

    logger.info("Bot starting — polling for updates…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
