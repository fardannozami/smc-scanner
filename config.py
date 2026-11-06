import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")

SYMBOLS = ["XAUUSD"]

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))

MAX_RISK_PER_TRADE = float(os.getenv("MAX_RISK_PER_TRADE", "1.0"))
MAX_DAILY_DRAWDOWN = float(os.getenv("MAX_DAILY_DRAWDOWN", "3.0"))
MAX_WEEKLY_DRAWDOWN = float(os.getenv("MAX_WEEKLY_DRAWDOWN", "6.0"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))

SCORE_STANDARD = 4
SCORE_A = 7
SCORE_A_PLUS = 10
SCORE_ELITE = 12

HTF = "H4"
MTF = "H1"
LTF = "M15"

DB_PATH = os.getenv("DB_PATH", "trading_scanner.db")
SCREENSHOTS_DIR = "screenshots"
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "60"))

# News filter
NEWS_FILTER_ENABLED = os.getenv("NEWS_FILTER_ENABLED", "true").lower() == "true"
NEWS_BLOCK_MINUTES_BEFORE = int(os.getenv("NEWS_BLOCK_MINUTES_BEFORE", "30"))
NEWS_BLOCK_MINUTES_AFTER = int(os.getenv("NEWS_BLOCK_MINUTES_AFTER", "15"))
# Send a Telegram heads-up this many minutes before a high-impact event that
# affects a tracked symbol. 0 disables the reminder.
NEWS_ALERT_MINUTES_BEFORE = int(os.getenv("NEWS_ALERT_MINUTES_BEFORE", "60"))

# Paper trading (virtual positions used to measure strategy win rate)
PAPER_TRADING_ENABLED = os.getenv("PAPER_TRADING_ENABLED", "true").lower() == "true"
PAPER_LOT = float(os.getenv("PAPER_LOT", "0.01"))
PAPER_TP_LEVEL = os.getenv("PAPER_TP_LEVEL", "tp1").lower()  # "tp1" or "tp2"
PAPER_MAX_HOLD_HOURS = int(os.getenv("PAPER_MAX_HOLD_HOURS", "168"))
# When false, a new signal is ignored while a position on the same symbol is
# still open (per-strategy stats are still refreshed on every signal).
PAPER_ALLOW_STACKING = os.getenv("PAPER_ALLOW_STACKING", "false").lower() == "true"
