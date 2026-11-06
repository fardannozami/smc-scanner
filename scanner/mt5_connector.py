import pandas as pd
import sys

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

import config

TF_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
    "W1": 32769,
}

if MT5_AVAILABLE:
    TF_MAP = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
    }


def connect():
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 package not installed. Install on Windows: pip install MetaTrader5")
    if not mt5.initialize(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER,
    ):
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    return True


def disconnect():
    if MT5_AVAILABLE:
        mt5.shutdown()


def get_bars(symbol, timeframe, count=200, include_current=False):
    """Fetch OHLCV bars.

    By default the still-forming candle is dropped: signals must only be
    evaluated on closed candles, otherwise conditions flicker intrabar and
    paper trades are opened at prices that later disappear. Pass
    ``include_current=True`` for live SL/TP monitoring.
    """
    if not MT5_AVAILABLE:
        return None
    tf = TF_MAP.get(timeframe, mt5.TIMEFRAME_H1)
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    except Exception:
        return None
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.rename(columns={
        "open": "Open", "high": "High",
        "low": "Low", "close": "Close",
        "tick_volume": "Volume",
    }, inplace=True)
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    if not include_current and len(df) > 1:
        df = df.iloc[:-1]
    return df


def get_account_info():
    if not MT5_AVAILABLE:
        return {}
    try:
        info = mt5.account_info()
    except Exception:
        return {}
    if info is None:
        return {}
    return {
        "balance": info.balance,
        "equity": info.equity,
        "profit": info.profit,
        "margin": info.margin,
        "free_margin": info.margin_free,
        "leverage": info.leverage,
        "currency": info.currency,
        "login": info.login,
        "server": info.server,
    }


def get_symbol_info(symbol):
    if not MT5_AVAILABLE:
        return {}
    try:
        info = mt5.symbol_info(symbol)
    except Exception:
        return {}
    if info is None:
        return {}
    return {
        "digits": info.digits,
        "point": info.point,
        "trade_contract_size": info.trade_contract_size,
        "trade_tick_size": info.trade_tick_size,
        "trade_tick_value": info.trade_tick_value,
        "currency_profit": info.currency_profit,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
    }


_CONTRACT_FALLBACKS = {
    "XAU": 100.0,
    "XAG": 5000.0,
}


def default_contract_size(symbol):
    """Fallback contract size when the broker does not report one."""
    sym = str(symbol).upper()
    for prefix, size in _CONTRACT_FALLBACKS.items():
        if prefix in sym:
            return size
    return 100000.0


def calc_profit(symbol, direction, lot, price_open, price_close):
    """Profit in account currency for a hypothetical position (no order sent).

    Uses MT5's own calculator when the terminal is connected, but validates it
    against ``price_diff * contract_size * lot`` when the profit currency equals
    the account currency. Some brokers publish inconsistent symbol metadata
    (e.g. XAUUSD with a tick value thousands of times too large), which makes
    ``order_calc_profit`` return absurd PnL figures.
    """
    lot = float(lot)
    diff = (price_close - price_open) if direction == "buy" else (price_open - price_close)

    symbol_info = get_symbol_info(symbol)
    contract = symbol_info.get("trade_contract_size") or default_contract_size(symbol)
    approx = diff * contract * lot

    if MT5_AVAILABLE:
        try:
            order_type = (
                mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
            )
            profit = mt5.order_calc_profit(
                order_type, symbol, lot, float(price_open), float(price_close),
            )
            if profit is not None:
                profit = float(profit)
                account = get_account_info()
                profit_currency = symbol_info.get("currency_profit")
                account_currency = account.get("currency")
                consistent = bool(
                    profit_currency and account_currency
                    and profit_currency == account_currency
                )
                if consistent and approx != 0:
                    # Allow for broker rounding, but reject broken metadata
                    # (e.g. a tick value thousands of times too large).
                    if 0.5 <= abs(profit / approx) <= 2.0:
                        return round(profit, 2)
                elif not consistent:
                    return round(profit, 2)
        except Exception:
            pass

    return round(approx, 2)
