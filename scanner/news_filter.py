import logging
import threading
import requests
from datetime import datetime, timezone, timedelta
import config
from database.db_manager import get_news_cache, save_news_cache

log = logging.getLogger(__name__)

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

SYMBOL_CURRENCIES = {
    "XAUUSD": ["USD"],        # XAU not on FF; USD events move gold
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "GBPJPY": ["GBP", "JPY"],
    "EURJPY": ["EUR", "JPY"],
}

_cache: dict = {"events": [], "fetched_at": None}
_lock = threading.Lock()

# The weekly calendar changes slowly: a successful fetch is reused for 24h.
# A failed attempt is retried after an hour, so a transient HTTP 429 does not
# leave the news filter without data for a whole day.
CACHE_SECONDS = 24 * 3600
RETRY_SECONDS = 3600


def _parse_dt(date_str: str):
    """Parse ISO-8601 datetime string to UTC-aware datetime."""
    if not date_str:
        return None
    try:
        # Python 3.11+ handles offset natively; fallback for 3.9/3.10
        try:
            dt = datetime.fromisoformat(date_str)
        except ValueError:
            # Try replacing trailing Z
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _as_utc(value):
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None else value
    )


def fetch_calendar(force=False):
    """Fetch and persist the ForexFactory weekly calendar.

    The JSON payload is stored in the ``news_calendar`` table and reused for
    24 hours. The last attempt is persisted too, so a restart cannot hammer
    the feed. A failed fetch is retried after an hour; the previous payload
    is kept in the meantime.
    """
    with _lock:
        now = datetime.now(timezone.utc)

        try:
            state = get_news_cache()
        except Exception:
            log.exception("Could not read calendar cache from DB")
            state = {"events": None, "fetched_at": None, "last_attempt_at": None}

        events = state.get("events")
        if events is None:
            events = _cache["events"]
        fetched_at = _as_utc(state.get("fetched_at")) or _cache["fetched_at"]
        attempted_at = _as_utc(state.get("last_attempt_at"))

        if not force:
            if fetched_at is not None:
                if (now - fetched_at).total_seconds() < CACHE_SECONDS:
                    _cache["events"] = events
                    _cache["fetched_at"] = fetched_at
                    return events
            if attempted_at is not None:
                if (now - attempted_at).total_seconds() < RETRY_SECONDS:
                    _cache["events"] = events
                    _cache["fetched_at"] = fetched_at
                    return events

        try:
            r = requests.get(
                FF_CALENDAR_URL,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            r.raise_for_status()
            events = r.json()
            _cache["events"] = events
            _cache["fetched_at"] = now
            try:
                save_news_cache(events, fetched_at=now, last_attempt_at=now)
            except Exception:
                log.exception("Could not persist calendar cache")
        except Exception as e:
            log.warning("Calendar fetch failed: %s", e)
            try:
                save_news_cache(
                    events, fetched_at=fetched_at, last_attempt_at=now
                )
            except Exception:
                log.exception("Could not persist calendar fetch attempt")
        return events


def get_upcoming_events(hours=24, impacts=("High", "Medium", "Low")):
    """Return events within the next N hours, sorted by time."""
    events = fetch_calendar()
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    result = []
    for ev in events:
        dt = _parse_dt(ev.get("date", ""))
        if dt is None or not (now - timedelta(hours=1) <= dt <= cutoff):
            continue
        impact = ev.get("impact", "")
        if impact not in impacts:
            continue
        delta_min = (dt - now).total_seconds() / 60
        result.append({
            "title": ev.get("title", ""),
            "currency": ev.get("country", ""),
            "impact": impact,
            "datetime_utc": dt,
            "forecast": ev.get("forecast") or "—",
            "previous": ev.get("previous") or "—",
            "actual": ev.get("actual") or "—",
            "minutes_away": round(delta_min),
        })
    return sorted(result, key=lambda x: x["datetime_utc"])


def is_news_blackout(symbol: str):
    """
    Returns (blackout: bool, reason: str | None).
    Blocks if a HIGH-impact event for the symbol's currencies is within
    the pre/post window defined in config.
    """
    currencies = SYMBOL_CURRENCIES.get(symbol.upper(), [])
    if not currencies:
        return False, None

    events = fetch_calendar()
    now = datetime.now(timezone.utc)
    pre = config.NEWS_BLOCK_MINUTES_BEFORE
    post = config.NEWS_BLOCK_MINUTES_AFTER

    for ev in events:
        if ev.get("impact", "") != "High":
            continue
        if ev.get("country", "").upper() not in currencies:
            continue
        dt = _parse_dt(ev.get("date", ""))
        if dt is None:
            continue
        delta_min = (dt - now).total_seconds() / 60
        if -post <= delta_min <= pre:
            direction = f"in {int(delta_min)}m" if delta_min > 0 else f"{abs(int(delta_min))}m ago"
            reason = f"⚠️ {ev.get('country')} high-impact event {direction}: {ev.get('title', '')}"
            return True, reason

    return False, None


def get_next_high_impact(currencies=("USD", "EUR", "GBP", "JPY")):
    """Return the next HIGH-impact event for tracked currencies."""
    events = get_upcoming_events(hours=48, impacts=("High",))
    for ev in events:
        if ev["currency"].upper() in currencies and ev["minutes_away"] > 0:
            return ev
    return None


def get_upcoming_symbol_events(symbol, minutes=60, impacts=("High",)):
    """HIGH-impact events that move `symbol`, due within `minutes`."""
    currencies = SYMBOL_CURRENCIES.get(str(symbol).upper(), [])
    if not currencies or minutes <= 0:
        return []
    hours = max(1, (int(minutes) + 59) // 60)
    events = get_upcoming_events(hours=hours, impacts=impacts)
    return [
        ev for ev in events
        if ev["currency"].upper() in currencies
        and ev.get("impact") in impacts
        and 0 < ev["minutes_away"] <= minutes
    ]
