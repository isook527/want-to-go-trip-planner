#!/usr/bin/env python3
"""Export the traveler-facing passport JSON to calendar and spreadsheet files."""
import argparse
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from route_quality import normalize_budget, normalize_timing
from pathlib import Path
from xml.sax.saxutils import escape

SHEETS = {
    "en-US": ("Itinerary", "Places", "Transfers", "Trip notes", "Budget"),
    "zh-CN": ("行程", "地点", "交通", "出发准备", "预算"),
}
NOTES = {
    "budget": ("预算", "Budget"), "lodging": ("住宿", "Lodging"),
    "reservations": ("预约", "Reservations"), "backups": ("备选", "Backups"),
    "checklist": ("清单", "Checklist"),
}
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"


def xml(value):
    return escape(re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF]", "", str(value or "")))


def itinerary(data):
    if not isinstance(data, dict) or not isinstance(data.get("places"), list):
        raise ValueError("Use a passport JSON with a places array, not a private library")
    route = data.get("routePlan") or {}
    start = (route.get("tripProfile") or {}).get("startDate")
    days = route.get("days") or data.get("days") or []
    for index, day in enumerate(days, 1):
        number = day.get("day") or index
        if type(number) is not int or number < 1:
            raise ValueError("day must be a positive integer")
        when = day.get("date")
        if not when and start:
            when = (date.fromisoformat(start) + timedelta(days=number - 1)).isoformat()
        visits = day.get("stops", day.get("visits", []))
        yield number, day.get("title") or str(number), when, visits, day.get("connections", [])


def day_timing(day, visits):
    if not visits:
        return None
    try:
        timings = [normalize_timing(v.get("timing")) for v in visits]
        if any(not t or t["status"] != "verified" for t in timings):
            return None
        zones = {t["timeZone"] for t in timings}
        if len(zones) != 1:
            return None
        zone = ZoneInfo(zones.pop())
        begins = [datetime.fromisoformat(f"{day}T{t['start']}") for t in timings]
        ends = [datetime.fromisoformat(f"{day}T{t['end']}") for t in timings]
        if any(t.replace(tzinfo=zone, fold=0).utcoffset() != t.replace(tzinfo=zone, fold=1).utcoffset()
               for t in begins + ends):
            return None  # Ambiguous or nonexistent local time.
        return zone, min(begins), max(ends)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def zone_component(zone, start, end):
    def offset(value):
        seconds = int(value.total_seconds())
        hours, remainder = divmod(abs(seconds), 3600)
        minutes, seconds_part = divmod(remainder, 60)
        return ("-" if seconds < 0 else "+") + f"{hours:02}{minutes:02}" + (f"{seconds_part:02}" if seconds_part else "")

    def observation(at, previous, local):
        kind = "DAYLIGHT" if local.dst() else "STANDARD"
        return [f"BEGIN:{kind}", f"DTSTART:{at:%Y%m%dT%H%M%S}", f"TZOFFSETFROM:{offset(previous)}",
                f"TZOFFSETTO:{offset(local.utcoffset())}", f"END:{kind}"]

    cursor = start.replace(tzinfo=timezone.utc) - timedelta(days=2)
    until = end.replace(tzinfo=timezone.utc) + timedelta(days=2)
    local = cursor.astimezone(zone)
    lines = ["BEGIN:VTIMEZONE", f"TZID:{zone.key}"]
    lines += observation(local, local.utcoffset(), local)
    while cursor < until:
        following = min(cursor + timedelta(days=1), until)
        old_offset = cursor.astimezone(zone).utcoffset()
        next_local = following.astimezone(zone)
        if old_offset != next_local.utcoffset():
            lower, upper = cursor, following
            while (upper - lower).total_seconds() > 1:
                middle = lower + timedelta(seconds=int((upper - lower).total_seconds()) // 2)
                if middle.astimezone(zone).utcoffset() == old_offset:
                    lower = middle
                else:
                    upper = middle
            lines += observation(upper.replace(tzinfo=None) + old_offset, old_offset, upper.astimezone(zone))
        cursor = following
    return [*lines, "END:VTIMEZONE"]


def calendar_location(data, visits):
    places = {p.get("name"): p for p in data["places"] if isinstance(p, dict)}
    for visit in visits:
        name = visit.get("name") or visit.get("place")
        if not name:
            continue
        place = places.get(name, {})
        address = visit.get("address") or place.get("address")
        location = ", ".join(str(v) for v in (name, address) if v)
        region = place.get("mapRegion") or place.get("map_region") or data.get("mapRegion") or data.get("map_region")
        query = " ".join(str(v) for v in (name, address, place.get("city") or data.get("destination") or data.get("city")) if v)
        if region == "mainland":
            return location, "https://uri.amap.com/search?" + urlencode({"keyword": query})
        if region == "outside_mainland":
            return location, "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": query})
        return location, ""
    return "", ""


def make_calendar(data):
    def field(value):
        return str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")

    def fold(line):
        rows, chunk = [], ""
        for char in line:
            if len(chunk.encode("utf-8")) + len(char.encode("utf-8")) > 75:
                rows.append(chunk)
                chunk = " "
            chunk += char
        return "\r\n".join([*rows, chunk])

    route = data.get("routePlan") or {}
    title = route.get("title") or data.get("title") or data.get("destination") or data.get("city") or "Want to Go"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Want to Go//Itinerary//EN", "CALSCALE:GREGORIAN"]
    entries = list(itinerary(data))
    series = route.get("calendarId") or route.get("routePlanId") or data.get("calendarId")
    if not series:
        dated = sorted(when for _, _, when, _, _ in entries if when)
        series = f"{data.get('destination') or data.get('city') or 'trip'}\0{dated[0] if dated else ''}"
    sequence = route.get("calendarSequence", data.get("calendarSequence", 0))
    if type(sequence) is not int or not 0 <= sequence <= 2147483647:
        raise ValueError("calendarSequence must be a nonnegative integer")
    event_lines, zones = [], {}
    seen_days = set()
    for number, label, when, visits, connections in entries:
        if not when:
            continue
        if number in seen_days:
            raise ValueError("dated itinerary day numbers must be unique")
        seen_days.add(number)
        start = date.fromisoformat(when)
        end = start + timedelta(days=1)
        uid = hashlib.sha256(f"{series}\0{number}".encode("utf-8")).hexdigest()
        description = "\n".join(
            " — ".join(str(part) for part in (v.get("timeLabel") or v.get("period"), v.get("name") or v.get("place") or v.get("title")) if part)
            for v in visits
        )
        timing = day_timing(start, visits)
        if timing:
            zone, begins, ends = timing
            prior = zones.get(zone.key, (zone, begins, ends))
            zones[zone.key] = (zone, min(prior[1], begins), max(prior[2], ends))
            dates = [f"DTSTART;TZID={zone.key}:{begins:%Y%m%dT%H%M%S}", f"DTEND;TZID={zone.key}:{ends:%Y%m%dT%H%M%S}"]
        else:
            dates = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
        location, url = calendar_location(data, visits)
        if not location and connections:
            first_leg = connections[0]
            location = " → ".join(str(first_leg[key]) for key in ("from", "to") if first_leg.get(key))
        event_lines.extend([
            "BEGIN:VEVENT", f"UID:{uid}@want-to-go", f"DTSTAMP:{stamp}", f"SEQUENCE:{sequence}", *dates,
            f"SUMMARY:{field(title)} · {field(label)}", f"DESCRIPTION:{field(description)}",
        ])
        if location:
            event_lines.append(f"LOCATION:{field(location)}")
        if url:
            event_lines.append(f"URL:{url}")
        event_lines.append("END:VEVENT")
    for zone, begins, ends in zones.values():
        if (ends - begins).days > 366:
            raise ValueError("timed calendar events for a zone must be within a year")
        lines.extend(zone_component(zone, begins, ends))
    lines.extend(event_lines)
    if not seen_days:
        chinese = data.get("locale") == "zh-CN"
        if "routePlan" in data:
            if not (data.get("routePlan") or {}).get("days"):
                raise ValueError(
                    "导出 ICS 前请用 route-plan 保存带日期的路线，再重新生成护照 JSON。"
                    if chinese else "Save a dated route with route-plan and regenerate the passport JSON before exporting ICS."
                )
            raise ValueError(
                "导出 ICS 需要在路线 JSON 的 tripProfile.startDate 填写 YYYY-MM-DD，再保存路线并重新生成护照。"
                if chinese else "For ICS, set tripProfile.startDate (YYYY-MM-DD) in the route JSON, save the route and regenerate the passport."
            )
        raise ValueError(
            "导出 ICS 需要在轻量行程 JSON 的 days[].date 填写至少一个 YYYY-MM-DD 日期。"
            if chinese else "For ICS, set at least one days[].date (YYYY-MM-DD) in the lightweight itinerary JSON."
        )
    return ("\r\n".join(fold(line) for line in [*lines, "END:VCALENDAR"]) + "\r\n").encode("utf-8")


def make_xlsx(data):
    en = data.get("locale") != "zh-CN"
    tabs = SHEETS["en-US" if en else "zh-CN"]
    rows = [
        [("Day", "Date", "Period", "Stop", "Notes") if en else ("天", "日期", "时段", "地点或活动", "备注")],
        [("Place", "Address", "Hours", "Tip") if en else ("地点", "地址", "营业时间", "出发提示")],
        [("Day", "From", "To", "Mode", "Duration", "Status", "Notes") if en else ("天", "起点", "终点", "方式", "时长", "状态", "备注")],
        [("Category", "Item") if en else ("类别", "事项")],
        [("Item", "Amount", "Currency", "Status") if en else ("项目", "金额", "币种", "状态")],
    ]
    for _, label, when, visits, connections in itinerary(data):
        rows[0].extend((label, date.fromisoformat(when) if when else None, v.get("timeLabel") or v.get("period"), v.get("name") or v.get("place") or v.get("title"), v.get("note") or v.get("detail")) for v in visits)
        rows[2].extend((label, v.get("from"), v.get("to"), v.get("modeLabel"), v.get("durationText"), v.get("statusLabel"), v.get("note")) for v in connections)
    rows[1].extend((v.get("name"), v.get("address") or v.get("positionText"), v.get("openingHoursText") or v.get("hours"), v.get("visitTip") or v.get("note")) for v in data["places"])
    for key, labels in NOTES.items():
        notes = (data.get("trip_notes") or {}).get(key)
        if isinstance(notes, list):
            rows[3].extend((labels[int(en)], note) for note in notes if isinstance(note, str))
    for item in (data.get("routePlan") or {}).get("unassigned") or []:
        rows[3].append((NOTES["backups"][int(en)], " — ".join(str(x) for x in (item.get("name"), item.get("reason")) if x)))

    budget = (data.get("routePlan") or {}).get("budget") or data.get("budget")
    for item in normalize_budget(budget):
        status = ("Verified" if en else "已核实") if item["status"] == "verified" else ("Estimate" if en else "估算")
        rows[4].append((item["item"], item["amount"], item["currency"], status))

    result = io.BytesIO()
    indices = range(1, len(tabs)+1)
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", f'<Types xmlns="{CONTENT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in indices) + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><bookViews><workbookView/></bookViews><sheets>' + "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(tabs, 1)) + "</sheets></workbook>")
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{PKG_REL}">' + "".join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in indices) + f'<Relationship Id="rId{len(tabs)+1}" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", workbook_styles())
        widths = ((18, 15, 16, 28, 50), (28, 32, 26, 50), (18, 26, 26, 18, 18, 18, 45), (18, 60), (32, 15, 12, 15))
        for i, sheet in enumerate(rows, 1):
            cells = []
            for row_number, row in enumerate(sheet, 1):
                cells.append(f'<row r="{row_number}">' + "".join(
                    spreadsheet_cell(f"{chr(65+col)}{row_number}", value, row_number == 1)
                    for col, value in enumerate(row)
                ) + "</row>")
            cols = "".join(f'<col min="{col}" max="{col}" width="{width}" customWidth="1"/>' for col, width in enumerate(widths[i-1], 1))
            archive.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{MAIN}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>{cols}</cols><sheetData>' + "".join(cells) + "</sheetData></worksheet>")
    return result.getvalue()


def spreadsheet_cell(reference, value, heading):
    if isinstance(value, date):
        serial = (value - date(1899, 12, 31)).days + int(value >= date(1900, 3, 1))
        return f'<c r="{reference}" s="3"><v>{serial}</v></c>'
    if type(value) in (int, float):
        return f'<c r="{reference}" s="2"><v>{value}</v></c>'
    style = 1 if heading else 2
    # inlineStr prevents spreadsheet formula evaluation of user text.
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml(value)}</t></is></c>'


def workbook_styles():
    return (
        f'<styleSheet xmlns="{MAIN}">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("passport", type=Path)
    parser.add_argument("--xlsx", type=Path)
    parser.add_argument("--ics", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.passport.read_text(encoding="utf-8"))
        list(itinerary(data))
        files = []
        for target, suffix, writer in ((args.xlsx, ".xlsx", make_xlsx), (args.ics, ".ics", make_calendar)):
            if target is None:
                continue
            if target.suffix.lower() != suffix or target.resolve() == args.passport.resolve() or target.is_symlink():
                raise ValueError(f"Use a separate {suffix} output path")
            files.append((target, writer(data)))
        if not files:
            parser.error("specify --xlsx, --ics, or both")
        for target, data_bytes in files:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
                try:
                    tmp.write(data_bytes)
                    tmp.close()
                    os.replace(tmp.name, target)
                finally:
                    Path(tmp.name).unlink(missing_ok=True)
            print(target)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
