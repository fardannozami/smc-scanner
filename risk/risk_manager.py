from datetime import datetime, timedelta, timezone

import config
from database.db_manager import get_equity_baseline, get_recent_losses
from scanner.mt5_connector import default_contract_size, get_symbol_info


def _period_starts():
    """Return (day_start, week_start) as UTC timestamp strings."""
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    fmt = "%Y-%m-%d %H:%M:%S"
    return day_start.strftime(fmt), week_start.strftime(fmt)


class RiskManager:
    """Enforce the trading constitution.

    Daily/weekly baselines are read from the ``risk_log`` table instead of
    being kept only in memory, so restarting the scanner neither resets the
    drawdown (bypass) nor carries a stale weekly baseline forever.
    """

    def __init__(self):
        self._daily_key = None
        self._weekly_key = None
        self._daily_start = None
        self._weekly_start = None

    def _refresh_baselines(self, equity):
        day_key, week_key = _period_starts()
        if self._daily_key != day_key:
            self._daily_key = day_key
            self._daily_start = get_equity_baseline(day_key)
        if self._weekly_key != week_key:
            self._weekly_key = week_key
            self._weekly_start = get_equity_baseline(week_key)
        # First check of a new day/week: current equity is the baseline.
        if self._daily_start is None:
            self._daily_start = equity
        if self._weekly_start is None:
            self._weekly_start = equity

    def check_risk(self, balance, equity):
        self._refresh_baselines(equity)

        violations = []
        warnings = []

        daily_dd = (
            (self._daily_start - equity) / self._daily_start * 100
            if self._daily_start else 0.0
        )
        weekly_dd = (
            (self._weekly_start - equity) / self._weekly_start * 100
            if self._weekly_start else 0.0
        )

        if daily_dd >= config.MAX_DAILY_DRAWDOWN:
            violations.append(
                f"Daily drawdown {daily_dd:.2f}% exceeds limit ({config.MAX_DAILY_DRAWDOWN}%)"
            )
        elif daily_dd >= config.MAX_DAILY_DRAWDOWN * 0.75:
            warnings.append(f"Daily drawdown at {daily_dd:.2f}% — approaching limit")

        if weekly_dd >= config.MAX_WEEKLY_DRAWDOWN:
            violations.append(
                f"Weekly drawdown {weekly_dd:.2f}% exceeds limit ({config.MAX_WEEKLY_DRAWDOWN}%)"
            )

        consec_losses = len(get_recent_losses(config.MAX_CONSECUTIVE_LOSSES))
        if consec_losses >= config.MAX_CONSECUTIVE_LOSSES:
            violations.append(
                f"{consec_losses} consecutive losses — trading suspended per constitution"
            )

        return {
            "allowed": len(violations) == 0,
            "violations": violations,
            "warnings": warnings,
            "daily_drawdown": round(daily_dd, 2),
            "weekly_drawdown": round(weekly_dd, 2),
            "consecutive_losses": consec_losses,
            "daily_start": self._daily_start,
            "weekly_start": self._weekly_start,
        }

    def calculate_position_size(self, account_balance, entry, sl, symbol=None,
                                contract_size=None):
        """Return lot size based on MAX_RISK_PER_TRADE%.

        Sizing uses ``price_risk * contract_size`` so it works for gold,
        forex and CFDs instead of assuming a 5-digit forex point.
        """
        risk_amount = account_balance * (config.MAX_RISK_PER_TRADE / 100)
        price_risk = abs(float(entry) - float(sl))
        if price_risk <= 0 or risk_amount <= 0:
            return 0.01

        info = get_symbol_info(symbol) if symbol else {}
        if contract_size is None:
            contract_size = (
                info.get("trade_contract_size")
                or default_contract_size(symbol)
            )

        value_per_lot = price_risk * float(contract_size)
        if value_per_lot <= 0:
            return 0.01

        lot_size = risk_amount / value_per_lot
        return round(max(0.01, min(lot_size, 100.0)), 2)

    def is_elite_trade_allowed(self, scoring, risk_status):
        """Elite trades require ELITE grade + no violations."""
        return (
            scoring.get("grade") == "ELITE"
            and risk_status.get("allowed")
            and risk_status.get("daily_drawdown", 99) < config.MAX_DAILY_DRAWDOWN * 0.5
        )
