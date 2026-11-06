"""Virtual (paper) position tracking.

Every scanner signal opens a virtual 0.01-lot position. Signals are generated
from closed candles only, so the entry is the signal bar close and the position
is tracked from the next candle until it hits SL, hits TP, or expires. Results
are stored in SQLite and aggregated per strategy so the win rate keeps updating.
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from database.db_manager import (
    close_paper_trade,
    create_paper_trade,
    get_paper_trade,
    get_open_paper_trades,
    get_strategy_stats_row,
    has_open_paper_trade,
    recompute_strategy_stats,
)
from scanner.mt5_connector import calc_profit, get_bars

log = logging.getLogger(__name__)

_TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080,
}


def _bars_needed(timeframe):
    """How many candles cover the maximum holding period."""
    minutes = _TF_MINUTES.get(str(timeframe).upper(), 15)
    hours = config.PAPER_MAX_HOLD_HOURS
    if hours <= 0:
        return 1000
    needed = int((hours * 60) / minutes) + 100
    return max(300, min(needed, 5000))


# Strategy label used for grouping win-rate statistics. The scanner's grade
# tiers (Standard / A / A+ / ELITE) are the natural strategy dimension; the
# full condition list is stored per trade for deeper analysis.
def strategy_key(analysis, scoring):
    return scoring.get("grade") or "Standard"


def _direction(analysis):
    bias = analysis.get("bias")
    if bias == "bullish":
        return "buy"
    if bias == "bearish":
        return "sell"
    return None


def _target_price(levels):
    if config.PAPER_TP_LEVEL == "tp2" and levels.get("tp2"):
        return levels["tp2"]
    return levels.get("tp1")


def open_from_signal(symbol, analysis, scoring, setup_id=None,
                     timeframe=None, df=None):
    """Open a virtual position for a signal.

    Returns the new paper-trade row, or None when the signal was skipped.
    Statistics are refreshed on every signal, even when no trade is opened.
    """
    if not config.PAPER_TRADING_ENABLED:
        return None

    levels = analysis.get("levels") or {}
    direction = _direction(analysis)
    if not direction or not levels:
        return None

    entry = levels.get("entry")
    sl = levels.get("sl")
    tp1 = levels.get("tp1")
    tp2 = levels.get("tp2")
    if entry is None or sl is None or tp1 is None:
        return None

    # Validate entry, SL and TP before opening position
    target = _target_price(levels)

    if target is None:
        log.warning(
            "Paper trade skipped for %s: TP target missing", symbol
        )
        return None

    if not validate_trade(direction, entry, sl, target):
        log.warning(
            "Paper trade rejected for %s: invalid levels "
            "direction=%s entry=%s sl=%s tp=%s",
            symbol, direction, entry, sl, target,
        )
        return None


    risk = abs(float(entry) - float(sl))
    if risk <= 0:
        log.warning("Paper trade skipped for %s: invalid risk", symbol)
        return None

    strategy = strategy_key(analysis, scoring)

    # No stacking: one virtual position per symbol at a time. Grade is part of
    # the strategy label, so checking (symbol, grade) allowed up to four
    # correlated trades on the same instrument.
    if not config.PAPER_ALLOW_STACKING and has_open_paper_trade(symbol):
        log.info(
            "Paper trade skipped for %s: position already open "
            "(stacking disabled)", symbol,
        )
        recompute_strategy_stats(strategy)
        return None

    open_bar_time = None
    if df is not None and len(df):
        open_bar_time = str(df.index[-1])

    signal_key = "|".join([
        symbol,
        strategy,
        direction,
        f"{float(entry):.5f}",
        f"{float(sl):.5f}",
        open_bar_time or "",
    ])

    trade = create_paper_trade(
        symbol=symbol,
        strategy=strategy,
        direction=direction,
        grade=scoring.get("grade"),
        score=scoring.get("score"),
        conditions=analysis.get("conditions", []),
        session=analysis.get("session"),
        timeframe=timeframe or config.LTF,
        lot=config.PAPER_LOT,
        entry=float(entry),
        sl=float(sl),
        tp1=float(tp1),
        tp2=float(tp2) if tp2 is not None else float(tp1),
        risk=round(risk, 5),
        open_bar_time=open_bar_time,
        setup_id=setup_id,
        signal_key=signal_key,
    )

    if trade is None:
        log.info("Paper trade duplicate ignored for %s (%s)", symbol, signal_key)
    else:
        log.info(
            "Paper trade #%s opened: %s %s %.2f lot @ %s (SL %s, TP %s)",
            trade["id"], symbol, direction.upper(), trade["lot"],
            trade["entry"], trade["sl"], trade["tp1"],
        )

    # Keep the per-strategy record current whenever this strategy fires.
    recompute_strategy_stats(strategy)
    if trade is not None:
        trade["strategy_stats"] = get_strategy_stats_row(strategy)
    return trade


def _is_expired(trade):
    hours = config.PAPER_MAX_HOLD_HOURS
    if hours <= 0 or not trade.get("opened_at"):
        return False
    try:
        opened = datetime.fromisoformat(str(trade["opened_at"]))
    except (TypeError, ValueError):
        return False
    if opened.tzinfo is not None:
        opened = opened.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now - opened >= timedelta(hours=hours)


def _evaluate_trade(trade, df):
    """Check a single open trade against candles. Returns a close event or None."""
    if df is None or df.empty or not trade.get("open_bar_time"):
        return None

    try:
        open_bar = pd.Timestamp(trade["open_bar_time"])
    except (TypeError, ValueError):
        return None

    # Entry is the close of the signal bar, so evaluation starts on the next
    # candle. The candle itself is never partly replayed.
    bars = df[df.index > open_bar]
    direction = trade["direction"]
    entry = float(trade["entry"])
    sl = float(trade["sl"])
    tp = _target_price({"tp1": trade["tp1"], "tp2": trade["tp2"]})
    tp = float(tp)
    risk = abs(float(trade.get("risk") or (entry - sl)))

    # Protect paper monitor from invalid stored positions. Close them instead
    # of leaving them open forever (which would block new trades forever once
    # per-symbol stacking is enforced).
    if not validate_trade(direction, entry, sl, tp):
        log.error(
            "Paper trade #%s has INVALID stored levels. "
            "direction=%s entry=%s sl=%s tp=%s. Closing as invalid.",
            trade["id"], direction, entry, sl, tp,
        )
        last_close = float(df["Close"].iloc[-1]) if not df.empty else entry
        close_paper_trade(
            trade["id"], last_close, "invalid", "invalid", 0.0, 0.0
        )
        return None


    exit_price = None
    exit_reason = None

    if not bars.empty:
        for _, bar in bars.iterrows():
            high = float(bar["High"])
            low = float(bar["Low"])
            bar_open = float(bar["Open"])

            if direction == "buy":
                # SL is checked first: when a candle touches both levels we
                # assume the pessimistic outcome (stop hit before target).
                if low <= sl:
                    exit_price = min(bar_open, sl)  # gap-through protection
                    exit_reason = "sl"
                    break
                if high >= tp:
                    exit_price = max(bar_open, tp)
                    exit_reason = "tp"
                    break
            else:
                if high >= sl:
                    exit_price = max(bar_open, sl)
                    exit_reason = "sl"
                    break
                if low <= tp:
                    exit_price = min(bar_open, tp)
                    exit_reason = "tp"
                    break

    if exit_reason is None:
        if not _is_expired(trade):
            return None
        exit_price = float(df["Close"].iloc[-1])
        exit_reason = "expired"

    if risk <= 0:
        r_multiple = 0.0
    elif direction == "buy":
        r_multiple = (exit_price - entry) / risk
    else:
        r_multiple = (entry - exit_price) / risk
    r_multiple = round(r_multiple, 2)

    if exit_reason == "tp":
        status = "win"
    elif exit_reason == "sl":
        status = "loss"
    else:
        status = "expired"

    pnl = calc_profit(trade["symbol"], direction, trade["lot"], entry, exit_price)

    if not close_paper_trade(
        trade["id"], exit_price, exit_reason, status, r_multiple, pnl
    ):
        return None

    closed = get_paper_trade(trade["id"])
    log.info(
        "Paper trade #%s closed: %s %s %s @ %s (%+.2fR, %+.2f)",
        trade["id"], trade["symbol"], direction.upper(), exit_reason,
        exit_price, r_multiple, pnl,
    )
    return closed


def monitor_open_trades():
    """Replay candles for every open paper trade and close those that hit.

    Returns a list of events: {"trade": <closed row>, "stats": <strategy row>}.
    """
    trades = get_open_paper_trades()
    if not trades:
        return []

    bars_cache = {}
    closed_trades = []

    for trade in trades:
        timeframe = trade.get("timeframe") or config.LTF
        cache_key = (trade["symbol"], timeframe)
        if cache_key not in bars_cache:
            bars_cache[cache_key] = get_bars(
                trade["symbol"], timeframe, _bars_needed(timeframe),
                include_current=True,
            )
        df = bars_cache[cache_key]

        if df is None:
            log.warning(
                "Paper monitor: no bars for %s (%s)", trade["symbol"], timeframe
            )
            continue

        try:
            closed = _evaluate_trade(trade, df)
        except Exception:
            log.exception("Paper monitor failed for trade #%s", trade["id"])
            continue

        if closed:
            closed_trades.append(closed)

    events = []
    strategies = {t["strategy"] for t in trades}
    strategies.update(t["strategy"] for t in closed_trades)
    for strategy in strategies:
        recompute_strategy_stats(strategy)

    for closed in closed_trades:
        events.append({
            "trade": closed,
            "stats": get_strategy_stats_row(closed["strategy"]),
        })
    return events




def validate_trade(direction, entry, sl, tp):
    """Validate price levels before opening a paper trade."""

    try:
        entry = float(entry)
        sl = float(sl)
        tp = float(tp)
    except (TypeError, ValueError):
        return False

    if not all(map(
        lambda x: pd.notna(x) and abs(x) != float("inf"),
        [entry, sl, tp]
    )):
        return False

    direction = direction.lower()

    if direction == "buy":
        return sl < entry < tp

    elif direction == "sell":
        return tp < entry < sl

    return False
