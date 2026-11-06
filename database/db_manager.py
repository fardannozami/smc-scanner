import sqlite3
import json
from datetime import date, datetime, timedelta, timezone
import config


def utc_timestamp():
    """UTC timestamp string in the same format used by SQLite CURRENT_TIMESTAMP."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def get_connection():
    # timeout/busy_timeout: the scanner and the dashboard write from separate
    # processes, so wait for the lock instead of failing with "database is locked".
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    conn = get_connection()
    # WAL lets the dashboard read while the scanner writes.
    conn.execute("PRAGMA journal_mode = WAL")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bias TEXT,
            grade TEXT,
            score INTEGER,
            conditions TEXT,
            entry REAL,
            sl REAL,
            tp1 REAL,
            tp2 REAL,
            session TEXT,
            chart_path TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT,
            setup_id INTEGER,
            opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP,
            session TEXT,
            grade TEXT,
            score INTEGER,
            entry REAL,
            sl REAL,
            tp REAL,
            exit_price REAL,
            outcome TEXT,
            rr_achieved REAL,
            notes TEXT,
            chart_path TEXT,
            FOREIGN KEY (setup_id) REFERENCES setups(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            entry_date DATE DEFAULT CURRENT_DATE,
            followed_plan TEXT,
            outcome TEXT,
            moved_stop TEXT,
            better_entry TEXT,
            emotions TEXT,
            notes TEXT,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS risk_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            balance REAL,
            equity REAL,
            daily_dd REAL,
            weekly_dd REAL,
            consecutive_losses INTEGER,
            trading_allowed INTEGER
        )
    """)

    # Virtual (paper) positions opened from scanner signals.
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            setup_id INTEGER,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL,
            grade TEXT,
            score INTEGER,
            conditions TEXT,
            session TEXT,
            timeframe TEXT,
            lot REAL NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            risk REAL,
            open_bar_time TEXT,
            opened_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'open',
            exit_price REAL,
            exit_reason TEXT,
            closed_at TIMESTAMP,
            r_multiple REAL,
            pnl REAL,
            FOREIGN KEY (setup_id) REFERENCES setups(id)
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_paper_status
        ON paper_trades(status)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_paper_strategy
        ON paper_trades(strategy)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_paper_symbol_status
        ON paper_trades(symbol, status)
    """)

    # Running win-rate statistics per strategy (grade tier).
    c.execute("""
        CREATE TABLE IF NOT EXISTS strategy_stats (
            strategy TEXT PRIMARY KEY,
            signals INTEGER DEFAULT 0,
            open_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            breakeven INTEGER DEFAULT 0,
            expired INTEGER DEFAULT 0,
            closed_trades INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            avg_r REAL DEFAULT 0,
            total_r REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            updated_at TIMESTAMP
        )
    """)

    # Last alert time per symbol/grade. Persisted so restarting the scanner
    # does not re-send alerts that are still inside their cooldown window.
    c.execute("""
        CREATE TABLE IF NOT EXISTS alert_cooldowns (
            key TEXT PRIMARY KEY,
            last_alert_at TIMESTAMP NOT NULL
        )
    """)

    # Persisted ForexFactory calendar payload. One row only; refreshed at most
    # once per day so restarting the scanner does not hammer the feed (429).
    c.execute("""
        CREATE TABLE IF NOT EXISTS news_calendar (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload TEXT NOT NULL,
            fetched_at TIMESTAMP,
            last_attempt_at TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def save_setup(symbol, timeframe, analysis, scoring, chart_path=None):
    conn = get_connection()
    c = conn.cursor()
    levels = analysis.get("levels") or {}
    c.execute("""
        INSERT INTO setups (symbol, timeframe, bias, grade, score, conditions, entry, sl, tp1, tp2, session, chart_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol, timeframe,
        analysis.get("bias"),
        scoring.get("grade"),
        scoring.get("score"),
        json.dumps(analysis.get("conditions", [])),
        levels.get("entry"),
        levels.get("sl"),
        levels.get("tp1"),
        levels.get("tp2"),
        analysis.get("session"),
        chart_path,
    ))
    setup_id = c.lastrowid
    conn.commit()
    conn.close()
    return setup_id


def log_trade(symbol, direction, entry, sl, tp, grade=None, score=None, session=None, setup_id=None, notes=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (symbol, direction, setup_id, session, grade, score, entry, sl, tp, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol, direction.lower() if direction else direction, setup_id,
        session, grade, score, entry, sl, tp, notes,
    ))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def close_trade(trade_id, exit_price, outcome, rr_achieved=None, notes=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE trades
        SET exit_price=?, outcome=?, rr_achieved=?, notes=?, closed_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (exit_price, outcome, rr_achieved, notes, trade_id))
    conn.commit()
    conn.close()


def save_journal_entry(trade_id, followed_plan, outcome, moved_stop, better_entry, emotions, notes):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO journal_entries (trade_id, followed_plan, outcome, moved_stop, better_entry, emotions, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (trade_id, followed_plan, outcome, moved_stop, better_entry,
          json.dumps(emotions) if isinstance(emotions, list) else emotions, notes))
    conn.commit()
    conn.close()


def log_risk(balance, equity, risk_status):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO risk_log (balance, equity, daily_dd, weekly_dd, consecutive_losses, trading_allowed)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        balance, equity,
        risk_status.get("daily_drawdown"),
        risk_status.get("weekly_drawdown"),
        risk_status.get("consecutive_losses"),
        1 if risk_status.get("allowed") else 0,
    ))
    conn.commit()
    conn.close()


def get_recent_losses(n=5):
    """Return the most recent losses, stopping at the first non-loss.

    Reads both manual `trades` and the virtual `paper_trades` table: the
    scanner never writes to `trades`, so relying on it alone kept the
    consecutive-loss rule from ever triggering.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT status FROM (
            SELECT outcome AS status, closed_at, id
            FROM trades
            WHERE outcome IS NOT NULL
            UNION ALL
            SELECT status, closed_at, id
            FROM paper_trades
            WHERE status IS NOT NULL AND status != 'open'
        )
        ORDER BY closed_at DESC, id DESC
        LIMIT ?
    """, (n,))
    rows = c.fetchall()
    conn.close()
    losses = []
    for row in rows:
        if row["status"] == "loss":
            losses.append("loss")
        else:
            break
    return losses


def get_equity_baseline(since):
    """Earliest equity recorded at/after `since` (UTC string).

    Used as the daily/weekly drawdown baseline so restarting the scanner does
    not reset (and therefore bypass) the risk limits.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT equity FROM risk_log
        WHERE logged_at >= ? AND equity IS NOT NULL
        ORDER BY logged_at ASC, id ASC
        LIMIT 1
    """, (since,))
    row = c.fetchone()
    conn.close()
    return float(row["equity"]) if row else None


def get_alert_cooldown(key):
    """Last alert datetime (naive UTC) for `key`, or None."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT last_alert_at FROM alert_cooldowns WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if not row or not row["last_alert_at"]:
        return None
    try:
        return datetime.fromisoformat(str(row["last_alert_at"]))
    except (TypeError, ValueError):
        return None


def set_alert_cooldown(key, when):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO alert_cooldowns (key, last_alert_at)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET last_alert_at = excluded.last_alert_at
    """, (key, when.isoformat(sep=" ", timespec="seconds")))
    conn.commit()
    conn.close()


def _parse_db_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _format_db_datetime(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(sep=" ", timespec="seconds")


def get_news_cache():
    """Return the persisted calendar cache.

    Result: {"events": list|None, "fetched_at": datetime|None,
    "last_attempt_at": datetime|None}. Datetimes are naive UTC.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT payload, fetched_at, last_attempt_at "
            "FROM news_calendar WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"events": None, "fetched_at": None, "last_attempt_at": None}
    try:
        events = json.loads(row["payload"]) if row["payload"] else []
    except (TypeError, ValueError):
        events = []
    return {
        "events": events,
        "fetched_at": _parse_db_datetime(row["fetched_at"]),
        "last_attempt_at": _parse_db_datetime(row["last_attempt_at"]),
    }


def save_news_cache(events, fetched_at=None, last_attempt_at=None):
    """Persist the calendar payload and the last fetch attempt time."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO news_calendar (id, payload, fetched_at, last_attempt_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            payload = excluded.payload,
            fetched_at = excluded.fetched_at,
            last_attempt_at = excluded.last_attempt_at
    """, (
        json.dumps(events or []),
        _format_db_datetime(fetched_at),
        _format_db_datetime(last_attempt_at) or utc_timestamp(),
    ))
    conn.commit()
    conn.close()


def get_daily_stats(target_date=None):
    target = (target_date or date.today()).isoformat()
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses,
               AVG(rr_achieved) as avg_rr
        FROM trades WHERE DATE(opened_at) = ?
    """, (target,))
    row = dict(c.fetchone())
    conn.close()
    row["avg_rr"] = round(row["avg_rr"] or 0, 2)
    return row


def get_weekly_stats():
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses,
               AVG(rr_achieved) as avg_rr
        FROM trades WHERE DATE(opened_at) >= ?
    """, (week_ago,))
    row = dict(c.fetchone())
    conn.close()
    row["avg_rr"] = round(row["avg_rr"] or 0, 2)
    return row


def get_all_trades():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM trades ORDER BY opened_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_active_setups(limit=20):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM setups ORDER BY detected_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_journal_entries():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM journal_entries ORDER BY entry_date DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_risk_log(limit=100):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM risk_log ORDER BY logged_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ─────────────────────────────────────────────
# Paper trades (virtual 0.01-lot positions)
# ─────────────────────────────────────────────

def create_paper_trade(symbol, strategy, direction, grade, score, conditions,
                       session, timeframe, lot, entry, sl, tp1, tp2, risk,
                       open_bar_time, setup_id=None, signal_key=None):
    """Insert a virtual position. Returns the row, or None if it already exists."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO paper_trades (
            signal_key, setup_id, symbol, strategy, direction, grade, score,
            conditions, session, timeframe, lot, entry, sl, tp1, tp2, risk,
            open_bar_time, opened_at, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
    """, (
        signal_key, setup_id, symbol, strategy, direction, grade, score,
        json.dumps(conditions or []), session, timeframe, lot, entry, sl,
        tp1, tp2, risk, open_bar_time, utc_timestamp(),
    ))
    inserted = c.rowcount > 0
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    if not inserted:
        return None
    return get_paper_trade(trade_id)


def get_paper_trade(trade_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_open_paper_trades():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM paper_trades WHERE status = 'open' ORDER BY opened_at ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_paper_trades(limit=200, status=None):
    conn = get_connection()
    c = conn.cursor()
    if status:
        c.execute(
            "SELECT * FROM paper_trades WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        )
    else:
        c.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def has_open_paper_trade(symbol, strategy=None):
    """Is there an open virtual position for this symbol (and strategy)?"""
    conn = get_connection()
    c = conn.cursor()
    if strategy:
        c.execute(
            "SELECT 1 FROM paper_trades WHERE symbol = ? AND strategy = ? AND status = 'open' LIMIT 1",
            (symbol, strategy),
        )
    else:
        c.execute(
            "SELECT 1 FROM paper_trades WHERE symbol = ? AND status = 'open' LIMIT 1",
            (symbol,),
        )
    exists = c.fetchone() is not None
    conn.close()
    return exists


def close_paper_trade(trade_id, exit_price, exit_reason, status,
                      r_multiple, pnl):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE paper_trades
        SET exit_price = ?, exit_reason = ?, status = ?, r_multiple = ?,
            pnl = ?, closed_at = ?
        WHERE id = ? AND status = 'open'
    """, (
        exit_price, exit_reason, status, r_multiple, pnl,
        utc_timestamp(), trade_id,
    ))
    updated = c.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def recompute_strategy_stats(strategy=None):
    """Rebuild the cached win-rate table from paper_trades.

    Win rate = wins / (wins + losses) * 100. Breakeven and expired trades are
    excluded from the win-rate denominator but still counted in total R.
    """
    conn = get_connection()
    c = conn.cursor()
    where = "WHERE strategy = ?" if strategy else ""
    params = (strategy,) if strategy else ()
    c.execute(f"""
        SELECT strategy,
               COUNT(*) AS signals,
               SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_trades,
               SUM(CASE WHEN status = 'win' THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN status = 'loss' THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN status = 'breakeven' THEN 1 ELSE 0 END) AS breakeven,
               SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired,
               SUM(CASE WHEN status NOT IN ('open', 'invalid') THEN 1 ELSE 0 END) AS closed_trades,
               SUM(CASE WHEN status NOT IN ('open', 'invalid') THEN COALESCE(r_multiple, 0) ELSE 0 END) AS total_r,
               AVG(CASE WHEN status NOT IN ('open', 'invalid') THEN r_multiple END) AS avg_r,
               SUM(CASE WHEN r_multiple > 0 THEN r_multiple ELSE 0 END) AS gross_profit_r,
               SUM(CASE WHEN r_multiple < 0 THEN -r_multiple ELSE 0 END) AS gross_loss_r
        FROM paper_trades
        {where}
        GROUP BY strategy
    """, params)
    rows = [dict(r) for r in c.fetchall()]
    now = utc_timestamp()

    for row in rows:
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        decided = wins + losses
        win_rate = round(wins / decided * 100, 2) if decided else 0.0
        gross_profit = row["gross_profit_r"] or 0.0
        gross_loss = row["gross_loss_r"] or 0.0
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (
            round(gross_profit, 2) if gross_profit > 0 else 0.0
        )
        row["win_rate"] = win_rate
        row["profit_factor"] = profit_factor
        row["avg_r"] = round(row["avg_r"] or 0.0, 2)
        row["total_r"] = round(row["total_r"] or 0.0, 2)
        c.execute("""
            INSERT INTO strategy_stats (
                strategy, signals, open_trades, wins, losses, breakeven, expired,
                closed_trades, win_rate, avg_r, total_r, profit_factor, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy) DO UPDATE SET
                signals = excluded.signals,
                open_trades = excluded.open_trades,
                wins = excluded.wins,
                losses = excluded.losses,
                breakeven = excluded.breakeven,
                expired = excluded.expired,
                closed_trades = excluded.closed_trades,
                win_rate = excluded.win_rate,
                avg_r = excluded.avg_r,
                total_r = excluded.total_r,
                profit_factor = excluded.profit_factor,
                updated_at = excluded.updated_at
        """, (
            row["strategy"], row["signals"] or 0, row["open_trades"] or 0,
            wins, losses, row["breakeven"] or 0, row["expired"] or 0,
            row["closed_trades"] or 0, win_rate,
            round(row["avg_r"] or 0.0, 2), round(row["total_r"] or 0.0, 2),
            profit_factor, now,
        ))

    conn.commit()
    conn.close()
    return rows


def get_strategy_stats():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM strategy_stats ORDER BY strategy ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_strategy_stats_row(strategy):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM strategy_stats WHERE strategy = ?", (strategy,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_paper_overall_stats():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_trades,
               SUM(CASE WHEN status = 'win' THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN status = 'loss' THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN status = 'breakeven' THEN 1 ELSE 0 END) AS breakeven,
               SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired,
               SUM(CASE WHEN status NOT IN ('open', 'invalid') THEN COALESCE(r_multiple, 0) ELSE 0 END) AS total_r,
               SUM(CASE WHEN status NOT IN ('open', 'invalid') THEN COALESCE(pnl, 0) ELSE 0 END) AS total_pnl
        FROM paper_trades
    """)
    row = dict(c.fetchone())
    conn.close()
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    decided = wins + losses
    row["win_rate"] = round(wins / decided * 100, 2) if decided else 0.0
    for key in ("total", "open_trades", "wins", "losses", "breakeven", "expired"):
        row[key] = row[key] or 0
    row["total_r"] = round(row["total_r"] or 0.0, 2)
    row["total_pnl"] = round(row["total_pnl"] or 0.0, 2)
    return row


def get_paper_period_stats(since_date=None):
    """Aggregated paper-trade stats for positions opened on/after a date."""
    since = (since_date or date.today()).isoformat()
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_trades,
               SUM(CASE WHEN status = 'win' THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN status = 'loss' THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN status = 'breakeven' THEN 1 ELSE 0 END) AS breakeven,
               SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired,
               SUM(CASE WHEN status NOT IN ('open', 'invalid') THEN COALESCE(r_multiple, 0) ELSE 0 END) AS total_r,
               SUM(CASE WHEN status NOT IN ('open', 'invalid') THEN COALESCE(pnl, 0) ELSE 0 END) AS total_pnl
        FROM paper_trades
        WHERE DATE(opened_at) >= ?
    """, (since,))
    row = dict(c.fetchone())
    conn.close()
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    decided = wins + losses
    row["win_rate"] = round(wins / decided * 100, 2) if decided else 0.0
    for key in ("total", "open_trades", "wins", "losses", "breakeven", "expired"):
        row[key] = row[key] or 0
    row["total_r"] = round(row["total_r"] or 0.0, 2)
    row["total_pnl"] = round(row["total_pnl"] or 0.0, 2)
    return row
