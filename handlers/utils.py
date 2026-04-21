"""Shared formatting helpers used by both user and admin handlers."""

from datetime import datetime
import sqlite3


STATUS_EMOJI = {
    "scheduled":  "🟢",
    "postponed":  "🟡",
    "cancelled":  "🔴",
}


def fmt_fixture(f: sqlite3.Row, show_id: bool = False) -> str:
    """Return a nicely formatted fixture string for Telegram (MarkdownV2-safe plain text)."""
    try:
        dt = datetime.strptime(f["match_dt"], "%Y-%m-%d %H:%M")
        date_str = dt.strftime("%a, %-d %b %Y")   # e.g. "Wed, 5 Feb 2025"
        time_str = dt.strftime("%-I:%M %p")        # e.g. "7:00 PM"
    except ValueError:
        date_str = f["match_dt"]
        time_str = ""

    emoji  = STATUS_EMOJI.get(f["status"], "⚪")
    id_tag = f"  [ID: {f['id']}]" if show_id else ""
    notes  = f"\n   📝 {f['notes']}" if f.get("notes") else ""

    status_label = f"  ({f['status'].upper()})" if f["status"] != "scheduled" else ""

    return (
        f"{emoji} *{f['sport']}*{status_label}{id_tag}\n"
        f"   {f['hall_a']} vs {f['hall_b']}\n"
        f"   📅 {date_str}  {time_str}\n"
        f"   📍 {f['venue']}"
        f"{notes}"
    )


def chunk(lst: list, size: int):
    """Split list into chunks of `size`."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
