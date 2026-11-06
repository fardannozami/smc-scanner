import pandas as pd
from datetime import datetime, timezone

# A gap/break only counts as a live signal while it is still fresh. Without
# these limits an FVG or BOS stays "active" for the whole dataframe, so the
# score never drops and every scan produces the same alert.
FVG_MAX_AGE_BARS = 24          # M15: last 6 hours
BOS_MAX_EXTENSION_ATR = 1.5    # price must still be near the broken level


def get_htf_bias(df):
    if len(df) < 50:
        return "neutral"
    ema20 = df["Close"].ewm(span=20).mean()
    ema50 = df["Close"].ewm(span=50).mean()
    last_close = df["Close"].iloc[-1]
    if last_close > ema20.iloc[-1] > ema50.iloc[-1]:
        return "bullish"
    if last_close < ema20.iloc[-1] < ema50.iloc[-1]:
        return "bearish"
    return "neutral"


def detect_swing_points(df, lookback=5):
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)
    swing_highs = []
    swing_lows = []
    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[i - j] for j in range(1, lookback + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, lookback + 1)):
            swing_highs.append((i, highs[i]))
        if all(lows[i] <= lows[i - j] for j in range(1, lookback + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, lookback + 1)):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def detect_bos(df, swing_highs, swing_lows, bias):
    """Break of Structure: price closes beyond last confirmed swing level."""
    if not swing_highs and not swing_lows:
        return None
    last_close = df["Close"].iloc[-1]
    atr = calculate_atr(df)
    if pd.isna(atr) or atr <= 0:
        atr = float((df["High"] - df["Low"]).mean())
    max_extension = atr * BOS_MAX_EXTENSION_ATR if atr > 0 else float("inf")

    if bias == "bullish" and swing_highs:
        _, prev_high = swing_highs[-1]
        if prev_high < last_close <= prev_high + max_extension:
            return {"type": "bullish_bos", "level": round(prev_high, 5)}
    if bias == "bearish" and swing_lows:
        _, prev_low = swing_lows[-1]
        if prev_low - max_extension <= last_close < prev_low:
            return {"type": "bearish_bos", "level": round(prev_low, 5)}
    return None


def detect_liquidity_sweep(df, swing_highs, swing_lows):
    """Liquidity sweep: wick past a structural level, body closes back inside."""
    if len(df) < 3:
        return None
    last = df.iloc[-1]
    if swing_lows:
        _, low_level = swing_lows[-1]
        if last["Low"] < low_level and last["Close"] > low_level:
            return {"type": "bullish_sweep", "level": round(low_level, 5)}
    if swing_highs:
        _, high_level = swing_highs[-1]
        if last["High"] > high_level and last["Close"] < high_level:
            return {"type": "bearish_sweep", "level": round(high_level, 5)}
    return None


def detect_fvg(df):
    """Fair Value Gap (3-candle imbalance): gap between candle[i-2] and candle[i]."""
    fvgs = []
    for i in range(2, len(df)):
        c0 = df.iloc[i - 2]
        c2 = df.iloc[i]
        # Bullish FVG: entire candle i is above candle i-2
        if c2["Low"] > c0["High"]:
            fvgs.append({
                "type": "bullish_fvg",
                "top": round(c2["Low"], 5),
                "bottom": round(c0["High"], 5),
                "size": round(c2["Low"] - c0["High"], 5),
                "index": i,
                "time": df.index[i],
                "filled": False,
            })
        # Bearish FVG: entire candle i is below candle i-2
        if c2["High"] < c0["Low"]:
            fvgs.append({
                "type": "bearish_fvg",
                "top": round(c0["Low"], 5),
                "bottom": round(c2["High"], 5),
                "size": round(c0["Low"] - c2["High"], 5),
                "index": i,
                "time": df.index[i],
                "filled": False,
            })

    last_close = df["Close"].iloc[-1]
    last_index = len(df) - 1
    for fvg in fvgs:
        if fvg["type"] == "bullish_fvg":
            fvg["filled"] = last_close < fvg["bottom"]
        else:
            fvg["filled"] = last_close > fvg["top"]
        fvg["age"] = last_index - fvg["index"]
        fvg["active"] = (not fvg["filled"]) and fvg["age"] <= FVG_MAX_AGE_BARS
    return fvgs


def detect_retest(df, fvgs, swing_highs, swing_lows, bias):
    """Check if current price is retesting an FVG or broken structure level."""
    if len(df) == 0:
        return None
    last = df.iloc[-1]
    tolerance = (df["High"].max() - df["Low"].min()) * 0.003

    for fvg in fvgs[-6:]:
        if fvg["filled"]:
            continue
        if fvg["type"] == "bullish_fvg" and bias == "bullish":
            if fvg["bottom"] - tolerance <= last["Low"] <= fvg["top"] + tolerance:
                return {"type": "fvg_retest", "level": fvg["bottom"], "fvg_type": "bullish"}
        elif fvg["type"] == "bearish_fvg" and bias == "bearish":
            if fvg["bottom"] - tolerance <= last["High"] <= fvg["top"] + tolerance:
                return {"type": "fvg_retest", "level": fvg["top"], "fvg_type": "bearish"}

    if bias == "bullish" and swing_highs:
        _, bos_level = swing_highs[-1]
        if abs(last["Close"] - bos_level) < tolerance * 2:
            return {"type": "structure_retest", "level": round(bos_level, 5)}
    if bias == "bearish" and swing_lows:
        _, bos_level = swing_lows[-1]
        if abs(last["Close"] - bos_level) < tolerance * 2:
            return {"type": "structure_retest", "level": round(bos_level, 5)}
    return None


def detect_rejection_candle(df, bias):
    """Detect confirmation candle: hammer, shooting star, or engulfing."""
    if len(df) < 2:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    total_range = last["High"] - last["Low"]
    if total_range < 1e-8:
        return None
    body = abs(last["Close"] - last["Open"])
    upper_wick = last["High"] - max(last["Close"], last["Open"])
    lower_wick = min(last["Close"], last["Open"]) - last["Low"]

    if bias == "bullish":
        if lower_wick > total_range * 0.6 and last["Close"] >= last["Open"]:
            return {"type": "hammer", "strength": round(lower_wick / total_range, 2)}
        if (last["Close"] > last["Open"] and prev["Close"] < prev["Open"]
                and last["Open"] <= prev["Close"] and last["Close"] >= prev["Open"]):
            return {"type": "bullish_engulfing", "strength": 1.0}

    if bias == "bearish":
        if upper_wick > total_range * 0.6 and last["Close"] <= last["Open"]:
            return {"type": "shooting_star", "strength": round(upper_wick / total_range, 2)}
        if (last["Close"] < last["Open"] and prev["Close"] > prev["Open"]
                and last["Open"] >= prev["Close"] and last["Close"] <= prev["Open"]):
            return {"type": "bearish_engulfing", "strength": 1.0}
    return None


def get_session(utc_hour):
    if 8 <= utc_hour < 17:
        if 13 <= utc_hour < 17:
            return "london_ny_overlap"
        return "london"
    if 13 <= utc_hour < 22:
        return "new_york"
    if 0 <= utc_hour < 8:
        return "asian"
    return "off_hours"


def calculate_atr(df, period=14):
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]



def _infer_digits(price):
    """Best-effort fallback when the broker digits are not supplied."""
    price = abs(float(price))
    if price >= 1000:
        return 2
    if price >= 100:
        return 3
    if price >= 10:
        return 4
    return 5


def calculate_entry_levels(df, bias, swing_highs, swing_lows, digits=None):
    entry = float(df["Close"].iloc[-1])
    atr = calculate_atr(df)

    if pd.isna(atr) or atr <= 0:
        return None

    if digits is None:
        digits = _infer_digits(entry)

    buffer = atr * 0.1
    min_risk = atr * 0.5

    if bias == "bullish":

        # Ambil swing low yang berada di bawah entry
        valid_lows = [
            price for _, price in swing_lows
            if price < entry
        ]

        if valid_lows:
            candidate_sl = max(valid_lows) - buffer
        else:
            candidate_sl = entry - atr

        # Pastikan SL berada di bawah entry
        risk = max(entry - candidate_sl, min_risk)
        sl = entry - risk

        tp1 = entry + risk * 2
        tp2 = entry + risk * 3

    elif bias == "bearish":

        # Ambil swing high yang berada di atas entry
        valid_highs = [
            price for _, price in swing_highs
            if price > entry
        ]

        if valid_highs:
            candidate_sl = min(valid_highs) + buffer
        else:
            candidate_sl = entry + atr

        # Pastikan SL berada di atas entry
        risk = max(candidate_sl - entry, min_risk)
        sl = entry + risk

        tp1 = entry - risk * 2
        tp2 = entry - risk * 3

    else:
        return None

    levels = {
        "entry": round(entry, digits),
        "sl": round(sl, digits),
        "tp1": round(tp1, digits),
        "tp2": round(tp2, digits),
        "risk": round(risk, digits),
        "rr": 2.0,
    }

    # Rounding must not collapse the levels (e.g. 5-digit pairs were being
    # rounded to 2 decimals before, which made SL == entry).
    if levels["sl"] == levels["entry"] or levels["tp1"] == levels["entry"]:
        return None

    return levels


def analyze_symbol(htf_df, mtf_df, ltf_df, symbol, digits=None):
    result = {
        "symbol": symbol,
        "bias": "neutral",
        "bos": None,
        "liquidity_sweep": None,
        "fvgs": [],
        "retest": None,
        "rejection": None,
        "session": None,
        "levels": None,
        "conditions": [],
    }

    bias = get_htf_bias(htf_df)
    result["bias"] = bias
    if bias == "neutral":
        result["session"] = get_session(datetime.now(timezone.utc).hour)
        return result

    swing_highs_mtf, swing_lows_mtf = detect_swing_points(mtf_df)

    bos = detect_bos(mtf_df, swing_highs_mtf, swing_lows_mtf, bias)
    result["bos"] = bos
    if bos:
        result["conditions"].append("BOS")

    sweep = detect_liquidity_sweep(mtf_df, swing_highs_mtf, swing_lows_mtf)
    result["liquidity_sweep"] = sweep
    if sweep:
        result["conditions"].append("Liquidity Sweep")

    fvgs = detect_fvg(ltf_df)
    result["fvgs"] = fvgs
    active_fvgs = [f for f in fvgs if f.get("active", not f["filled"])]
    if active_fvgs:
        result["conditions"].append("FVG")

    ltf_highs, ltf_lows = detect_swing_points(ltf_df)

    retest = detect_retest(ltf_df, active_fvgs, ltf_highs, ltf_lows, bias)
    result["retest"] = retest
    if retest:
        result["conditions"].append("Retest")

    rejection = detect_rejection_candle(ltf_df, bias)
    result["rejection"] = rejection
    if rejection:
        result["conditions"].append("Rejection Candle")

    utc_hour = datetime.now(timezone.utc).hour
    result["session"] = get_session(utc_hour)

    result["levels"] = calculate_entry_levels(
        ltf_df, bias, ltf_highs, ltf_lows, digits=digits
    )

    return result
