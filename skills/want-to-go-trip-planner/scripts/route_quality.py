"""Numeric route checks. Unknown inputs stay unknown, including legacy text."""
from datetime import date, timedelta
import math
import re
from urllib.parse import urlsplit

from fact_freshness import with_freshness


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
BLOCKS = {"morning": ("06:00", "12:00"), "midday": ("11:00", "14:00"),
          "afternoon": ("12:00", "18:00"), "evening": ("17:00", "24:00")}
DAY_NOTES = ("lunch", "rest", "backup", "dropIfDelayed", "tradeoff")


def normalize_area(value):
    if not isinstance(value, str) or len(value.strip()) > 80:
        raise ValueError("area must be text of at most 80 characters")
    return value.strip()


def normalize_budget(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("budget must be an array")
    result = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("budget item must be an object")
        item, amount, currency, status = (entry.get(k) for k in ("item", "amount", "currency", "status"))
        if (not isinstance(item, str) or not item.strip()
            or type(amount) not in (int, float) or not 0 <= amount <= 1e12 or not math.isfinite(amount)
            or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
            or status not in ("verified", "estimate")):
            raise ValueError("budget needs item, numeric amount, three-letter currency, and verified/estimate status")
        result.append({"item": item.strip()[:200], "amount": amount, "currency": currency, "status": status})
    return result


def bounded_int(data, key, maximum):
    value = data.get(key)
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{key} must be an integer between 0 and {maximum}")
    return value


def clock(value, allow_midnight=False):
    return isinstance(value, str) and (
        bool(re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value))
        or (allow_midnight and value == "24:00")
    )


def web_url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == "https" and bool(url.hostname) and url.username is None
    except ValueError:
        return False


def normalize_timing(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("timing must be an object")
    start, end, zone = (value.get(k) for k in ("start", "end", "timeZone"))
    if not clock(start) or not clock(end) or end <= start:
        raise ValueError("timing needs same-day HH:MM start and end in order")
    if not isinstance(zone, str) or not re.fullmatch(r"[A-Za-z0-9_+/-]{1,100}", zone) or "//" in zone:
        raise ValueError("timing.timeZone must be an IANA zone")
    status = value.get("status", "unverified")
    source = value.get("sourceUrl")
    if status not in ("verified", "unverified") or (source is not None and not web_url(source)):
        raise ValueError("invalid timing status or HTTPS source URL")
    if status == "verified" and not source:
        raise ValueError("verified timing needs a sourceUrl")
    result = {"start": start, "end": end, "timeZone": zone, "status": status}
    if source:
        result["sourceUrl"] = source
    return result


def normalize_opening_hours(value):
    """Keep historical text and unstructured objects readable."""
    if not isinstance(value, dict):
        return value
    if not set(value).intersection({"weeklyHours", "closedDays", "lastEntry", "reservationLeadDays"}):
        return value
    weekly = value.get("weeklyHours", {})
    closed = value.get("closedDays", [])
    if not isinstance(weekly, dict) or set(weekly) - set(WEEKDAYS):
        raise ValueError("weeklyHours keys must be mon through sun")
    if not isinstance(closed, list) or any(day not in WEEKDAYS for day in closed):
        raise ValueError("closedDays must contain weekday keys")
    for windows in weekly.values():
        if not isinstance(windows, list) or any(
            not isinstance(w, dict) or not clock(w.get("open")) or not clock(w.get("close"), True)
            or w["open"] >= w["close"] for w in windows
        ):
            raise ValueError("weeklyHours needs same-day open/close windows; split overnight hours")
    if "lastEntry" in value and not clock(value["lastEntry"]):
        raise ValueError("lastEntry must be HH:MM")
    bounded_int(value, "reservationLeadDays", 365)
    return value


def hours_conflict(hours, weekday, stop):
    if not isinstance(hours, dict) or weekday is None:
        return False
    if weekday in (hours.get("closedDays") or []):
        return True
    windows = (hours.get("weeklyHours") or {}).get(weekday)
    if windows is None:
        return False
    if not windows:
        return True
    timing = stop.get("timing")
    if timing:
        return not any(
            w["open"] <= timing["start"] < timing["end"] <= w["close"]
            and timing["start"] <= hours.get("lastEntry", "24:00") for w in windows
        )
    block = BLOCKS.get(stop.get("timeBlock"))
    return bool(block and not any(
        max(block[0], w["open"]) < min(block[1], w["close"])
        and max(block[0], w["open"]) <= hours.get("lastEntry", "24:00") for w in windows
    ))


def connection_status(connection):
    if connection.get("checkedAt") or connection.get("validUntil"):
        return with_freshness({**connection, "field": "transport"})["status"]
    return connection.get("status", "unverified")


def lint_route(plan, places):
    profile = plan["tripProfile"]
    reduced = profile.get("reducedMobility") or profile.get("pace", "").casefold() in {"slow", "relaxed", "慢", "轻松"}
    stop_limit = profile.get("maxMajorStops") or (2 if reduced else 3)
    leg_limit = profile.get("maxTransferMinutes") or 60
    daily_limit = profile.get("maxDailyTransferMinutes") or (90 if reduced else 120)
    walking_limit = profile.get("walkingLimitMeters")
    start = date.fromisoformat(profile["startDate"]) if profile.get("startDate") else None
    must_go = set(profile.get("mustGoPlaceIds", []))
    warnings, metrics = [], []
    for day in plan["days"]:
        number = day["day"]
        weekday = WEEKDAYS[(start + timedelta(days=number - 1)).weekday()] if start else None

        def warn(code, **details):
            warnings.append({"code": code, "day": number, **details})

        stops, connections = day["stops"], day["connections"]
        major_stops = sum(stop.get("major", True) for stop in stops)
        if major_stops > stop_limit:
            warn("too_many_major_stops", count=major_stops, limit=stop_limit)
        for key in DAY_NOTES[:-1]:
            if not day.get(key):
                warn("missing_" + key)
        for stop in stops:
            place = places.get(stop["placeId"], {})
            try:
                hours = normalize_opening_hours(place.get("openingHours"))
            except ValueError:
                hours = None
                warn("invalid_opening_hours", placeId=stop["placeId"])
            if weekday is None and isinstance(hours, dict) and (hours.get("weeklyHours") or hours.get("closedDays")):
                warn("opening_check_needs_date", placeId=stop["placeId"])
            if hours_conflict(hours, weekday, stop):
                warn("opening_hours_conflict", placeId=stop["placeId"])
            needs_reservation = stop.get("reservationRequired") or (
                isinstance(hours, dict) and hours.get("reservationLeadDays") is not None
            )
            if needs_reservation and stop.get("reservationStatus") != "confirmed":
                warn("reservation_unconfirmed", placeId=stop["placeId"],
                     status=stop.get("reservationStatus", "unknown"))
        durations = [leg["durationMinutes"] for leg in connections if "durationMinutes" in leg]
        expected_pairs = {(a["placeId"], b["placeId"]) for a, b in zip(stops, stops[1:])}
        actual_pairs = {(leg.get("fromPlaceId"), leg.get("toPlaceId")) for leg in connections}
        missing = len(expected_pairs - actual_pairs)
        unknown = len(connections) - len(durations)
        unverified = sum(connection_status(leg) != "verified" for leg in connections) + missing
        total = sum(durations)
        connection_count = len(connections) + missing
        if connection_count and unverified * 3 > connection_count:
            warn("too_many_unverified_connections", count=unverified, total=connection_count)
        if missing or unknown:
            warn("incomplete_commute_minutes", missingConnections=missing, unknownDurations=unknown)
        if total > daily_limit:
            warn("daily_commute_over_limit", minutes=total, limit=daily_limit)
        for leg in connections:
            minutes = leg.get("durationMinutes", 0)
            if minutes > leg_limit and not (leg.get("tradeoff") or day.get("tradeoff")):
                warn("long_transfer_needs_tradeoff", minutes=minutes, limit=leg_limit)
        daily_must_go = must_go.intersection(stop["placeId"] for stop in stops)
        if len(daily_must_go) > 1 and not day.get("tradeoff"):
            # Use the recorded path, including stops between the first and last must-go.
            indices = [i for i, stop in enumerate(stops) if stop["placeId"] in daily_must_go]
            span = {(a["placeId"], b["placeId"]) for a, b in zip(stops[indices[0]:indices[-1]], stops[indices[0]+1:indices[-1]+1])}
            relevant = [leg for leg in connections if (leg.get("fromPlaceId"), leg.get("toPlaceId")) in span]
            minutes = sum(leg.get("durationMinutes", 0) for leg in relevant)
            if minutes > leg_limit and not any(leg.get("tradeoff") for leg in relevant):
                warn("distant_must_go_needs_tradeoff", minutes=minutes, limit=leg_limit)
        walk_legs = [leg for leg in connections if leg["mode"] == "walk"]
        walking = sum(leg.get("distanceMeters", 0) for leg in walk_legs) + day.get("onsiteWalkingMeters", 0)
        if walking_limit is not None:
            if walking > walking_limit:
                warn("walking_over_limit", meters=walking, limit=walking_limit)
            if missing or any("distanceMeters" not in leg for leg in walk_legs) or "onsiteWalkingMeters" not in day:
                warn("incomplete_walking_distance")
        metrics.append({"day": number, "majorStops": major_stops, "knownCommuteMinutes": total,
                        "commuteComplete": not (missing or unknown), "knownWalkingMeters": walking})
    return {"warnings": warnings, "metrics": metrics}
