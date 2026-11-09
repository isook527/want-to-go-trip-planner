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
from pathlib import Path
from xml.sax.saxutils import escape

SHEETS = {
    "en-US": ("Itinerary", "Places", "Transfers", "Trip notes"),
    "zh-CN": ("行程", "地点", "交通", "出发准备"),
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
    for index, day in enumerate(days):
        when = day.get("date")
        if not when and start:
            when = (date.fromisoformat(start) + timedelta(days=index)).isoformat()
        visits = day.get("stops", day.get("visits", []))
        yield day.get("title") or f"{index+1}", when, visits, day.get("connections", [])


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

    title = data.get("title") or data.get("destination") or data.get("city") or "Want to Go"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Want to Go//Itinerary//EN", "CALSCALE:GREGORIAN"]
    events = 0
    for index, (label, when, visits, _) in enumerate(itinerary(data)):
        if not when:
            continue
        start = date.fromisoformat(when)
        end = start + timedelta(days=1)
        uid = hashlib.sha256(f"{title}\0{when}\0{index}".encode("utf-8")).hexdigest()
        description = "\n".join(
            " — ".join(str(part) for part in (v.get("timeLabel") or v.get("period"), v.get("name") or v.get("place") or v.get("title")) if part)
            for v in visits
        )
        lines.extend([
            "BEGIN:VEVENT", f"UID:{uid}@want-to-go", f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}",
            f"SUMMARY:{field(title)} · {field(label)}", f"DESCRIPTION:{field(description)}", "END:VEVENT",
        ])
        events += 1
    if not events:
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
    ]
    for label, when, visits, connections in itinerary(data):
        rows[0].extend((label, when, v.get("timeLabel") or v.get("period"), v.get("name") or v.get("place") or v.get("title"), v.get("note") or v.get("detail")) for v in visits)
        rows[2].extend((label, v.get("from"), v.get("to"), v.get("modeLabel"), v.get("durationText"), v.get("statusLabel"), v.get("note")) for v in connections)
    rows[1].extend((v.get("name"), v.get("address") or v.get("positionText"), v.get("openingHoursText") or v.get("hours"), v.get("visitTip") or v.get("note")) for v in data["places"])
    for key, labels in NOTES.items():
        notes = (data.get("trip_notes") or {}).get(key)
        if isinstance(notes, list):
            rows[3].extend((labels[int(en)], note) for note in notes if isinstance(note, str))
    for item in (data.get("routePlan") or {}).get("unassigned") or []:
        rows[3].append((NOTES["backups"][int(en)], " — ".join(str(x) for x in (item.get("name"), item.get("reason")) if x)))

    result = io.BytesIO()
    indices = range(1, len(tabs)+1)
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", f'<Types xmlns="{CONTENT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in indices) + "</Types>")
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>' + "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(tabs, 1)) + "</sheets></workbook>")
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{PKG_REL}">' + "".join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in indices) + "</Relationships>")
        for i, sheet in enumerate(rows, 1):
            cells = []
            for row_number, row in enumerate(sheet, 1):
                # inlineStr prevents spreadsheet formula evaluation of user text.
                cells.append(f'<row r="{row_number}">' + "".join(
                    f'<c r="{chr(65+col)}{row_number}" t="inlineStr"><is><t xml:space="preserve">{xml(value)}</t></is></c>'
                    for col, value in enumerate(row)
                ) + "</row>")
            archive.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{MAIN}"><sheetData>' + "".join(cells) + "</sheetData></worksheet>")
    return result.getvalue()


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
