import logging
import requests
from html import escape

import config

log = logging.getLogger(__name__)

STAGE_HEADERS = {
    "setup_developing": "🔍 SETUP DEVELOPING",
    "entry_zone": "⚡ ENTRY ZONE APPROACHING",
    "m5_confirmation": "✅ 5-MIN CONFIRMATION",
    "m15_confirmation": "✅ 15-MIN CONFIRMATION",
    "high_confidence": "🎯 HIGH-CONFIDENCE SETUP",
    "risk_violation": "🚨 RISK ALERT",
}

BIAS_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}


def _post(method, **kwargs):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, timeout=15, **kwargs)
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("[Telegram] %s error: %s", method, e)
        return False


def send_message(text):
    return _post("sendMessage", json={
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    })


def send_photo(photo_path, caption=""):
    with open(photo_path, "rb") as f:
        return _post("sendPhoto", data={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "caption": caption[:1024],
            "parse_mode": "HTML",
        }, files={"photo": f})


def format_alert(symbol, analysis, scoring, alert_stage="setup_developing",
                 paper_trade=None):
    from scanner.scoring_engine import GRADE_EMOJI
    bias = analysis.get("bias", "neutral")
    levels = analysis.get("levels") or {}
    conditions = analysis.get("conditions", [])
    session = str(analysis.get("session") or "unknown").replace("_", " ").title()
    grade = scoring.get("grade", "Standard")
    score = scoring.get("score", 0)
    max_score = scoring.get("max_score", 14)
    breakdown = scoring.get("breakdown", [])

    header = STAGE_HEADERS.get(alert_stage, "📊 SIGNAL ALERT")
    grade_emoji = GRADE_EMOJI.get(grade, "📊")
    bias_emoji = BIAS_EMOJI.get(bias, "⚪")
    symbol = escape(str(symbol))
    session = escape(str(session))
    grade = escape(str(grade))

    progress_filled = int((score / max_score) * 10)
    progress_bar = "█" * progress_filled + "░" * (10 - progress_filled)

    msg = f"{header}\n\n"
    msg += f"{grade_emoji} <b>{symbol}</b> — <b>{grade} Setup</b>\n"
    msg += f"Score: [{progress_bar}] {score}/{max_score}\n"
    msg += f"Bias: {bias_emoji} <b>{escape(str(bias).upper())}</b> | Session: {session}\n\n"

    if conditions:
        msg += "<b>Conditions Met:</b>\n"
        for c in conditions:
            msg += f"  ✓ {escape(str(c))}\n"
        msg += "\n"

    if levels:
        msg += "<b>Trade Levels:</b>\n"
        msg += f"  📍 Entry:  <code>{levels.get('entry', '—')}</code>\n"
        msg += f"  🛑 Stop:   <code>{levels.get('sl', '—')}</code>\n"
        msg += f"  🎯 TP1:    <code>{levels.get('tp1', '—')}</code>  (2R)\n"
        msg += f"  🎯 TP2:    <code>{levels.get('tp2', '—')}</code>  (3R)\n\n"

    if paper_trade:
        msg += (
            f"📝 <b>Paper trade opened</b>: "
            f"{escape(str(paper_trade['direction']).upper())} {paper_trade['lot']:.2f} lot "
            f"@ <code>{paper_trade['entry']}</code>  (#{paper_trade['id']})\n"
        )
        stats = paper_trade.get("strategy_stats") or {}
        if stats:
            msg += (
                f"📊 Strategy <b>{escape(str(paper_trade.get('strategy', '?')))}</b> win rate: "
                f"<b>{(stats.get('win_rate') or 0):.1f}%</b> "
                f"({stats.get('wins') or 0}W / {stats.get('losses') or 0}L)\n\n"
            )
        else:
            msg += "\n"

    msg += "<i>Decision support only — manual execution required.</i>"
    return msg


def send_alert(symbol, analysis, scoring, chart_path=None,
               alert_stage="setup_developing", paper_trade=None):
    msg = format_alert(symbol, analysis, scoring, alert_stage, paper_trade)
    if chart_path:
        return send_photo(chart_path, caption=msg)
    return send_message(msg)


def send_paper_close(event):
    """Notify that a virtual position hit SL/TP or expired."""
    trade = event.get("trade") or {}
    stats = event.get("stats") or {}

    reason = trade.get("exit_reason")
    if reason == "tp":
        header = "✅ PAPER TRADE CLOSED — TP HIT"
    elif reason == "sl":
        header = "🛑 PAPER TRADE CLOSED — SL HIT"
    elif reason == "expired":
        header = "⏳ PAPER TRADE CLOSED — EXPIRED"
    else:
        header = "📄 PAPER TRADE CLOSED"

    r_multiple = trade.get("r_multiple") or 0.0
    pnl = trade.get("pnl") or 0.0
    pnl_icon = "🟢" if pnl >= 0 else "🔴"
    result = "WIN" if reason == "tp" else "LOSS" if reason == "sl" else "EXPIRED"

    msg = f"{header}\n\n"
    msg += (
        f"<b>{escape(str(trade.get('symbol', '?')))}</b> "
        f"{escape(str(trade.get('direction', '')).upper())} "
        f"{trade.get('lot', 0):.2f} lot · <b>{result}</b>\n"
    )
    msg += (
        f"Entry <code>{trade.get('entry')}</code> → "
        f"Exit <code>{trade.get('exit_price')}</code>\n"
    )
    msg += f"Result: <b>{r_multiple:+.2f}R</b> | {pnl_icon} <b>{pnl:+.2f}</b>\n"

    if stats:
        msg += (
            f"\n📊 Strategy <b>{escape(str(trade.get('strategy', '?')))}</b> win rate: "
            f"<b>{(stats.get('win_rate') or 0):.1f}%</b> "
            f"({stats.get('wins') or 0}W / {stats.get('losses') or 0}L, "
            f"{stats.get('closed_trades') or 0} closed)\n"
        )
        msg += (
            f"Total: <b>{(stats.get('total_r') or 0):+.2f}R</b> · "
            f"Profit factor <b>{(stats.get('profit_factor') or 0):.2f}</b>"
        )

    return send_message(msg)


def send_risk_alert(violations, warnings=None):
    msg = "🚨 <b>RISK CONSTITUTION ALERT</b>\n\n"
    for v in violations:
        msg += f"❌ {v}\n"
    if warnings:
        msg += "\n⚠️ <b>Warnings:</b>\n"
        for w in warnings:
            msg += f"  {w}\n"
    msg += "\n<b>Trading suspended per your constitution rules.</b>"
    return send_message(msg)


def send_startup_message(symbols):
    msg = (
        "🚀 <b>Trading Scanner Online</b>\n\n"
        f"Monitoring: <code>{', '.join(symbols)}</code>\n"
        "Timeframes: H4 bias → H1 structure → M15 entry\n\n"
        "Strategy: HTF Bias · BOS · Liquidity Sweeps · FVG · Retest · Rejection\n"
        "Alerts: Standard / A / A+ / 💎 Elite"
    )
    if config.PAPER_TRADING_ENABLED:
        msg += (
            f"\n\n📝 Paper trading: {config.PAPER_LOT:.2f} lot per signal "
            f"· TP target {config.PAPER_TP_LEVEL.upper()}"
        )
    return send_message(msg)
