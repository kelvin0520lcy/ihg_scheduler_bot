# IHG Scheduling Telegram Bot

A scheduling assistant for NUS Inter Hall Games (IHG) committee members and players.

---

## What the bot does (and what it does NOT do)

### ✅ What it does
- Lets users look up fixtures by sport, hall, or venue
- Sends automated match reminders 24 h and 2 h before kick-off to subscribers
- Lets Scheduling ICs update venues, postpone/cancel/reschedule matches, and broadcast announcements
- Detects scheduling clashes *within the IHG schedule you enter*
- Supports bulk CSV import for the start of the season

### ❌ What it does NOT do
- **It cannot check NUS REBOKS or the SUU booking calendar.**
  There is no public API for this. Venue availability must be confirmed manually
  by submitting the SUU Facilities Booking Form (at least 2 weeks before the event)
  and waiting for a confirmation email.
- It does not know about NUS varsity team priority bookings, which can bump IHG slots.
- It does not book venues for you.

### Real-world workflow for Scheduling ICs
```
1. Identify tentative slot for a fixture.
2. Submit NUS SUU Facilities Booking Form (≥2 weeks before event).
   → For MPSH / USC / UTown halls, go to uci.nus.edu.sg → Facilities Booking
3. Wait for SUU confirmation email.
4. Enter the confirmed fixture in the bot:
      /addfixture Badminton | Eusoff | Kent Ridge | MPSH 1 | 2025-02-05 19:00
5. Run /checkclashes to verify no IHG-internal conflicts.
6. If NUS later reassigns your slot (varsity priority), use:
      /postpone <id>   or   /reschedule <id> | new_dt | new_venue
   The bot will notify all subscribers automatically.
```

---

## Setup

### Prerequisites
- Python 3.11+
- A Telegram account

### Step 1 — Create a bot on Telegram
1. Open Telegram, search for **@BotFather**.
2. Send `/newbot`, follow the prompts.
3. Copy the **HTTP API token** you receive.

### Step 2 — Get your Telegram user ID
1. Open Telegram, search for **@userinfobot**.
2. Send it any message — it will reply with your user ID.
3. This becomes your `ADMIN_IDS` value.

### Step 3 — Install dependencies
```bash
cd ihg_bot
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 4 — Configure environment
```bash
cp .env.example .env
# Edit .env and fill in BOT_TOKEN and ADMIN_IDS
```

### Step 5 — Run the bot
```bash
python bot.py
```

The bot will create `ihg.db` (SQLite) on first run.

---

## Deployment (running 24/7 on a server)

For the bot to send reminders reliably, it needs to run continuously.
The simplest free options:

### Option A — Railway (recommended for beginners)
1. Create a free account at [railway.app](https://railway.app).
2. Push the `ihg_bot/` folder to a GitHub repository.
3. In Railway: New Project → Deploy from GitHub repo.
4. Add environment variables (`BOT_TOKEN`, `ADMIN_IDS`) in the Railway dashboard.
5. Railway auto-restarts the bot if it crashes.

### Option B — Render (free tier)
1. Push to GitHub.
2. Go to [render.com](https://render.com) → New → Web Service.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Add environment variables in the Render dashboard.

### Option C — Your own Linux VPS (e.g. AWS Free Tier / DigitalOcean)
```bash
# On the server:
git clone <your-repo>
cd ihg_bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env   # fill in values

# Run with systemd so it restarts automatically
sudo nano /etc/systemd/system/ihgbot.service
```

Paste into the service file:
```ini
[Unit]
Description=IHG Scheduling Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/ihg_bot
ExecStart=/home/ubuntu/ihg_bot/venv/bin/python bot.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/ihg_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ihgbot
sudo systemctl start ihgbot
sudo systemctl status ihgbot   # check it's running
```

---

## Command Reference

### For everyone
| Command | Description | Example |
|---------|-------------|---------|
| `/start` | Welcome message | |
| `/help` | List all commands | |
| `/schedule <sport>` | Upcoming fixtures for a sport | `/schedule Badminton` |
| `/nextmatch <hall>` | Next match for a hall | `/nextmatch Eusoff` |
| `/venue <name>` | Matches at a venue | `/venue MPSH 1` |
| `/upcoming` | All matches in the next 7 days | |
| `/sports` | Sports with fixtures | |
| `/halls` | List all halls | |
| `/venues` | List common NUS venues | |
| `/subscribe hall <hall>` | Get reminders for a hall | `/subscribe hall Eusoff` |
| `/subscribe sport <sport>` | Get reminders for a sport | `/subscribe sport Football` |
| `/unsubscribe hall <hall>` | Remove subscription | `/unsubscribe hall Sheares` |
| `/mysubscriptions` | See your active subscriptions | |

### For Scheduling ICs (admin only)
| Command | Description | Example |
|---------|-------------|---------|
| `/addfixture` | Add a confirmed fixture | `/addfixture Badminton \| Eusoff \| Kent Ridge \| MPSH 1 \| 2025-02-05 19:00` |
| `/removefixture <id>` | Delete a fixture | `/removefixture 3` |
| `/postpone <id> [reason]` | Mark as postponed | `/postpone 3 Venue taken by varsity` |
| `/cancelfix <id> [reason]` | Mark as cancelled | `/cancelfix 3 Weather` |
| `/reschedule <id> \| <dt> [\| venue]` | Reschedule to new slot | `/reschedule 3 \| 2025-02-12 19:00 \| MPSH 2` |
| `/updatevenue <id> \| <venue>` | Update venue only | `/updatevenue 3 \| MPSH 2` |
| `/listfixtures` | List all fixtures (with IDs) | `/listfixtures hall Eusoff` |
| `/checkclashes` | Detect IHG-internal conflicts | |
| `/announce <message>` | Broadcast to all subscribers | `/announce IHG Opening Ceremony is this Saturday!` |
| `/addadmin <user_id>` | Add another IC as admin | `/addadmin 987654321` |
| `/importcsv` | Bulk import (send CSV as file) | See below |

### Bulk CSV import
Prepare a `.csv` file (no header row):
```
sport,hall_a,hall_b,venue,YYYY-MM-DD HH:MM
Badminton,Eusoff,Kent Ridge,MPSH 1,2025-02-05 19:00
Football,Sheares,Raffles,University Field,2025-02-06 17:00
```
Then in Telegram, attach the file and type `/importcsv` as the caption.

---

## How reminders work

1. Every 5 minutes the bot checks whether any fixture is exactly 24 h or 2 h away (±5 min).
2. If yes, it sends a reminder to everyone subscribed to that fixture's hall(s) or sport.
3. Each reminder is only sent once (tracked in the database).
4. If a fixture is rescheduled, old reminder flags are cleared so new ones fire at the correct time.

---

## Notes on venue availability

The bot has **no connection to NUS REBOKS or SUU**. This is intentional:

- NUS does not provide a public API for venue availability.
- Venue bookings require SUU form submission + staff advisor sign-off.
- Varsity teams have priority; IHG slots can be bumped without warning.

The correct mental model:
> The bot reflects your confirmed IHG schedule. Venue availability lives in your email inbox (SUU confirmations), not in the bot.

---

## File structure
```
ihg_bot/
├── bot.py              Main entry point
├── config.py           Env vars + constants
├── database.py         SQLite operations
├── scheduler.py        APScheduler reminder jobs
├── handlers/
│   ├── user.py         Public commands
│   ├── admin.py        IC-only commands
│   └── utils.py        Shared formatting helpers
├── requirements.txt
├── .env.example
└── README.md           (this file)
```
