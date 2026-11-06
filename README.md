# Trading Scanner — MT5 Signal Bot + Streamlit Dashboard

A professional **Forex & Gold trading scanner** built in Python that connects to **MetaTrader 5**, detects high-probability setups using Smart Money Concepts (SMC), scores them, and sends **progressive Telegram alerts** with chart screenshots.

Designed as Phase 1 of a full **Trading Operating System** — no auto-execution, full manual control.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-informational.svg)](https://www.metatrader5.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

---

## Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Setup](#setup)
- [Configuration Reference](#configuration-reference)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Security](#security)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## Features

### Market Scanner

- Connects to MetaTrader 5 via the official Python API
- Scans a configurable list of instruments (default: `XAUUSD`); add or remove pairs in `config.py`
- Multi-timeframe analysis: H4 bias → H1 structure → M15 entry
- Runs on a configurable interval (default 60s)
- Survives transient MT5/DB failures and keeps running across restarts

### Strategy Logic (Smart Money Concepts)

| Condition | Description |
|-----------|-------------|
| HTF Bias | Bullish/bearish trend via EMA20/EMA50 on H4 |
| Market Structure | Swing highs/lows across timeframes |
| Break of Structure (BOS) | Price closes beyond last confirmed swing (only while within 1.5 ATR) |
| Liquidity Sweep | Wick past structural level, body closes back inside |
| Fair Value Gap (FVG) | 3-candle imbalance, active while unfilled and ≤ 24 bars old |
| Retest | Price returning to FVG or broken structure |
| Rejection Candle | Hammer, shooting star, bullish/bearish engulfing |
| Session Filter | London, New York, London/NY overlap, Asian |
| Risk-to-Reward | Auto-calculated entry, SL, TP1 (2R), TP2 (3R) |
| **News Filter** | Blocks alerts 30m before / 15m after HIGH-impact events (ForexFactory) |

### Setup Scoring Engine

Each setup is dynamically scored (max **14 points**) and graded:

| Grade | Min Score | Description |
|-------|-----------|-------------|
| 🥉 Standard | 4 | Basic confluence |
| 🥈 A | 7 | Good setup |
| 🥇 A+ | 10 | High-probability |
| 💎 Elite | 12 | All conditions confirmed |

### Telegram Alerts

Progressive staged alerts sent with chart screenshots:

- 🔍 Setup Developing
- ⚡ Entry Zone Approaching
- ✅ 5-Min / 15-Min Confirmation
- 🎯 High-Confidence Setup

Each alert includes grade, score bar, bias, session, and entry/SL/TP levels.

### Paper Trading & Strategy Win Rate

Every signal automatically opens a **virtual 0.01-lot position** (no order is
sent to the broker). Signals are evaluated on **closed candles only**, so a
setup cannot flicker intrabar. The position is tracked from the next candle:

- Opens at the signal entry (close of the signal candle), with the signal's SL
  and TP.
- Closes automatically when **SL** or **TP** is touched, or after
  `PAPER_MAX_HOLD_HOURS` (default 7 days).
- Sends a Telegram notification on every close with the result in R and
  account currency.
- Records every trade in SQLite (`paper_trades`) and keeps a live
  **win-rate table per strategy** (`strategy_stats`), refreshed on every signal
  and every close.
- When a candle touches both SL and TP, the stop is assumed first (pessimistic)
  so the statistics are not inflated.

Strategy is the scanner's grade tier (`Standard`, `A`, `A+`, `ELITE`); the full
condition list is stored per trade for deeper analysis. Win rate is
`wins / (wins + losses)`; expired trades are tracked separately. By default only
one position per symbol can be open at a time (`PAPER_ALLOW_STACKING=false`),
and alert cooldowns persist across scanner restarts.

### Risk Management Constitution

Automatically suspends alerts when limits are breached:

- Max % risk per trade (used for lot size calculation)
- Daily drawdown limit
- Weekly drawdown limit
- Max consecutive losses

### Trade Journal & Database

SQLite database storing:

- Setups detected (symbol, grade, score, levels, chart path)
- Paper trades (virtual positions, SL/TP outcome, R multiple, PnL)
- Strategy stats (running win rate, average R, profit factor per grade)
- Trades (entry, SL, TP, outcome, RR achieved, notes)
- Journal entries (daily review responses)
- Risk log (equity, drawdown history)
- Alert cooldowns and the cached economic calendar

### News Filter

- Fetches the **ForexFactory weekly calendar** (public JSON endpoint, no API key needed)
- Caches the calendar in SQLite for 24 hours (one fetch per day, survives restarts)
- Maps each symbol to its currencies (e.g. GBPJPY → GBP + JPY)
- Suppresses scanner alerts during the blackout window around HIGH-impact events
- Sends a Telegram heads-up once per HIGH-impact event (default 60m before) for tracked symbols
- Sends a Telegram notification when an alert is blocked (once per symbol, with cooldown)
- Window is fully configurable via `.env`

### Streamlit Dashboard

Eight-page dashboard:

| Page | Contents |
|------|----------|
| Dashboard | Today's stats, recent setups, next high-impact event countdown, equity trend |
| **Economic Calendar** | Live ForexFactory events, countdown timer, impact filter, timeline chart, scanner blackout indicator |
| Active Setups | Filterable setup table with chart previews |
| **Paper Trades** | Virtual positions, win rate per strategy, cumulative R curve, SL/TP outcomes |
| Trade Journal | Log and close trades |
| Analytics | Win rate, RR distribution, cumulative RR curve, performance by grade/symbol/session |
| Daily Review | End-of-day reflection form (stored for future AI coaching) |
| Risk Status | Drawdown tracker, consecutive loss chart, constitution rules |

---

## Project Structure

```text
trading-scanner/
├── main.py                    # Scanner entry point (runs the loop)
├── config.py                  # All settings loaded from .env
├── scanner/
│   ├── mt5_connector.py       # MT5 connection and bar fetching
│   ├── market_analyzer.py     # Full SMC strategy logic
│   ├── scoring_engine.py      # Setup scoring and grading
│   ├── chart_generator.py     # Dark-theme candlestick charts (mplfinance)
│   └── news_filter.py         # ForexFactory calendar + blackout logic
├── alerts/
│   └── telegram_bot.py        # Telegram message and photo sending
├── paper/
│   └── paper_trader.py        # Virtual positions + SL/TP tracking
├── risk/
│   └── risk_manager.py        # Constitution rules enforcement + lot sizing
├── database/
│   └── db_manager.py          # SQLite schema + all CRUD operations
├── dashboard/
│   └── app.py                 # Streamlit 8-page dashboard
├── screenshots/               # Generated chart images (runtime output)
├── requirements.txt
└── .env.example
```

> `trading_scanner.db` (the SQLite database) and `scanner.log` are created at
> runtime and are not part of the source tree.

---

## Setup

### Requirements

- **Windows** (the `MetaTrader5` Python package is Windows-only)
- MetaTrader 5 terminal installed and logged in
- Python 3.10+
- A Telegram bot (create one via [@BotFather](https://t.me/BotFather))

### Install

```bash
git clone https://github.com/NadirAliOfficial/trading-scanner.git
cd trading-scanner
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

On macOS/Linux, activate with `source .venv/bin/activate`. Note that the scanner
itself requires Windows because of the MT5 dependency.

### Configure

```bash
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux
```

Edit `.env` with your credentials:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
MT5_LOGIN=123456
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Server
```

To scan additional instruments, edit `SYMBOLS` in `config.py`:

```python
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY"]
```

### Run the Scanner

```bash
python main.py
```

### Run the Dashboard

```bash
streamlit run dashboard/app.py
```

---

## Configuration Reference

All values are read from `.env` (see `.env.example` for a template).

| Variable | Default | Description |
|----------|---------|-------------|
| `SCAN_INTERVAL` | `60` | Seconds between scans |
| `ALERT_COOLDOWN_MINUTES` | `60` | Min minutes between repeat alerts per symbol/grade |
| `MAX_RISK_PER_TRADE` | `1.0` | % of account per trade |
| `MAX_DAILY_DRAWDOWN` | `3.0` | Daily DD % limit |
| `MAX_WEEKLY_DRAWDOWN` | `6.0` | Weekly DD % limit |
| `MAX_CONSECUTIVE_LOSSES` | `3` | Losses before scanner pauses |
| `SCORE_STANDARD` | `4` | Min score for Standard alert |
| `SCORE_A` | `7` | Min score for A alert |
| `SCORE_A_PLUS` | `10` | Min score for A+ alert |
| `SCORE_ELITE` | `12` | Min score for Elite alert |
| `NEWS_FILTER_ENABLED` | `true` | Enable/disable news blackout |
| `NEWS_BLOCK_MINUTES_BEFORE` | `30` | Minutes before event to block alerts |
| `NEWS_BLOCK_MINUTES_AFTER` | `15` | Minutes after event to resume alerts |
| `NEWS_ALERT_MINUTES_BEFORE` | `60` | Telegram heads-up before a high-impact event (0 = off) |
| `PAPER_TRADING_ENABLED` | `true` | Open virtual positions on every signal |
| `PAPER_LOT` | `0.01` | Virtual lot size per signal |
| `PAPER_TP_LEVEL` | `tp1` | Exit target: `tp1` (2R) or `tp2` (3R) |
| `PAPER_MAX_HOLD_HOURS` | `168` | Close open paper trades after N hours (0 = never) |
| `PAPER_ALLOW_STACKING` | `false` | Allow multiple open trades per symbol |
| `DB_PATH` | `trading_scanner.db` | SQLite database file path |

---

## Roadmap

- [ ] AI coaching feedback based on journal entries
- [ ] Multi-broker support
- [ ] Webhook-based Telegram bot for journal commands
- [ ] Machine learning setup quality predictor

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for code
guidelines and the pull request process.

## Security

For security concerns, please refer to [SECURITY.md](./SECURITY.md). Never commit
your `.env` file or broker credentials.

## Disclaimer

This tool is for **decision support only**. It does not place trades automatically.
All execution is manual. Past performance of any strategy does not guarantee future
results. Trade responsibly.

## License

Released under the [MIT License](./LICENSE). Copyright (c) 2026 Nadir Ali Khan.
