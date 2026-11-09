"""Expiry policy shared by new review records and read-time projections."""
from datetime import date, datetime, time, timedelta, timezone
import re


def instant(value, end_of_day=False):
    if not isinstance(value, str) or not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.combine(date.fromisoformat(value), time.max if end_of_day else time.min, timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def expiry_window(field):
    name = re.sub(r"[^a-z0-9]", "", field.casefold())
    if any(key in name for key in ("weather", "forecast", "transport", "transit", "schedule", "timetable", "lasttrain", "lastbus", "lastservice", "traffic", "flight", "connection")):
        return timedelta(days=3)
    if any(key in name for key in ("open", "clos", "reserv", "book", "ticket", "availab", "price", "fee", "fare")):
        return timedelta(days=7)
    return None


def with_freshness(record, *, as_of=None, strict=False):
    result = dict(record)
    checked = instant(result.get("checkedAt"))
    explicit = instant(result.get("validUntil"), end_of_day=True)
    if strict and result.get("validUntil") and explicit is None:
        raise ValueError("validUntil must be an ISO date or timezone-aware timestamp")
    if strict and result.get("status") == "verified" and checked is None:
        raise ValueError("verified facts need an ISO checkedAt date or timezone-aware timestamp")
    if strict and checked and explicit and explicit < checked:
        raise ValueError("validUntil cannot precede checkedAt")
    if result.get("status") != "verified":
        return result
    window = expiry_window(result.get("field") or result.get("type") or "")
    default = checked + window if checked and window else None
    if default and (explicit is None or default < explicit):
        result["validUntil"] = default.isoformat(timespec="seconds").replace("+00:00", "Z")
    expires = min((d for d in (explicit, default) if d is not None), default=None)
    now = as_of or datetime.now(timezone.utc)
    if (expires is not None and expires < now) or (result.get("validUntil") and expires is None) or (window and checked is None):
        result["status"] = "stale"
        result["nextAction"] = result.get("nextAction") or "Recheck before departure / 出发前重新核对"
    return result
