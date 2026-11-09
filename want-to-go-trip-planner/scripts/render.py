#!/usr/bin/env python3
"""Convert a lightweight itinerary and render it with the shared passport template."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from want_to_go import text_scan_issues


RENDERER = Path(__file__).resolve().parent.parent / "renderer" / "render_report.mjs"
MAP_REGIONS = {"mainland", "outside_mainland", "unknown"}
PLACE_FIELDS = ("localName", "branch", "address", "city", "area", "areaGroup", "district")


def map_region(value):
    if not isinstance(value, str) or value not in MAP_REGIONS:
        raise ValueError("地图区域必须是 mainland、outside_mainland 或 unknown / Invalid map region.")
    return value


def report_from_lightweight(data):
    if not isinstance(data, dict) or not isinstance(data.get("places"), list):
        raise ValueError("输入须包含 places 数组 / Input must contain a places array.")
    locale = data.get("locale", "zh-CN")
    if locale not in ("zh-CN", "en-US"):
        raise ValueError("locale must be zh-CN or en-US")
    en = locale == "en-US"
    region = map_region(data.get("map_region", "unknown"))
    places = {}
    for item in data["places"]:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ValueError("每个地点都需要名称 / Every place needs a name.")
        name = str(item["name"]).strip()
        if name in places:
            raise ValueError("地点名称重复，请加上分店或区域 / Distinguish places with the same name.")
        places[name] = {
            "name": name,
            **{key: item[key] for key in PLACE_FIELDS if isinstance(item.get(key), str)},
            "openingHoursText": item.get("hours") or ("To be confirmed" if en else "待确认"),
            "signature": item.get("note") or "",
            "mapRegion": map_region(item.get("map_region", region)),
        }

    raw_days = data.get("days", [])
    if not isinstance(raw_days, list):
        raise ValueError("days 必须是数组 / days must be an array.")
    invalid_stop = "行程项类型或名称无效；地点游览须对应地点清单。 / Invalid stop or unknown place."
    days = []
    for index, day in enumerate(raw_days, 1):
        if not isinstance(day, dict) or not isinstance(day.get("visits", []), list):
            raise ValueError("每天须包含 visits 数组 / Each day must contain a visits array.")
        stops = []
        for visit in day.get("visits", []):
            if not isinstance(visit, dict):
                raise ValueError(invalid_stop)
            kind = visit.get("type", "place")
            name = visit.get("title") if kind == "activity" else visit.get("place")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(invalid_stop)
            if kind == "activity" and not visit.get("place"):
                stop = {"name": name, "type": "activity"}
            elif kind == "place" and name in places:
                stop = {"name": name, "address": places[name].get("address", "")}
            else:
                raise ValueError(invalid_stop)
            stop.update(timeLabel=visit.get("period") or "", note=visit.get("detail") or "")
            stops.append(stop)
        days.append({
            "day": index,
            "title": day.get("title") or (f"Day {index}" if en else f"第 {index} 天"),
            "stops": stops,
        })

    route = {"title": data.get("title") or "", "days": days} if days else {}
    return {
        "locale": locale,
        "destination": data.get("city") or data.get("title") or "",
        "mapRegion": region,
        "lightweight": True,
        "placesOnly": not days,
        "places": list(places.values()),
        "routePlan": route,
    }


def title_day_count(title):
    if not isinstance(title, str):
        return None
    zh = re.search(r"(?<![0-9零〇一二两三四五六七八九十])([0-9]+|[零〇一二两三四五六七八九十]+)\s*(?:日游|天(?:游|行程)?|日行程)", title)
    if zh:
        value = zh[1].replace("两", "二").replace("〇", "零")
        if value.isascii() and value.isdecimal():
            return int(value)
        digits = {digit: i for i, digit in enumerate("零一二三四五六七八九")}
        if value in digits:
            return digits[value]
        if value.count("十") == 1:
            left, right = value.split("十")
            if (not left or left in digits) and (not right or right in digits):
                return digits.get(left, 1) * 10 + digits.get(right, 0)
    words = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    en = re.search(r"\b(\d+|" + "|".join(words) + r")(?:\s*[-–]\s*day\b|\s+days?\b)", title, re.I)
    if en:
        return int(en[1]) if en[1].isdecimal() else words.index(en[1].lower())
    return None


def main():
    if len(sys.argv) != 3:
        raise ValueError("用法 / Usage: python3 render.py input.json output.html")
    source, target = map(Path, sys.argv[1:])
    if target.suffix.lower() not in (".html", ".htm"):
        raise ValueError("Output must be an HTML file")
    if source.resolve() == target.resolve():
        raise ValueError("输入和输出不能是同一文件 / Input and output must differ.")
    data = json.loads(source.read_text(encoding="utf-8"))
    report = report_from_lightweight(data)
    serialized = json.dumps(report, ensure_ascii=False)
    issues = text_scan_issues(serialized, "customer")
    if issues:
        raise ValueError("customer scan failed: " + ", ".join(issues))
    node = shutil.which("node")
    if not node:
        raise ValueError("生成 HTML 需要 Node.js 18+ / HTML rendering requires Node.js 18+.")
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".want-to-go-render-") as temporary:
        work = Path(temporary)
        prepared, rendered = work / "report.json", work / "passport.html"
        prepared.write_text(serialized, encoding="utf-8")
        result = subprocess.run([node, str(RENDERER), str(prepared), str(rendered)], capture_output=True,
                                encoding="utf-8", errors="replace", check=False)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Passport renderer failed")
        issues = text_scan_issues(rendered.read_text(encoding="utf-8"), "customer")
        if issues:
            raise ValueError("customer scan failed: " + ", ".join(issues))
        os.replace(rendered, target)
    title_count = title_day_count(data.get("title"))
    if title_count is not None and "days" in data and title_count != len(data["days"]):
        actual = len(data["days"])
        if report["locale"] == "en-US":
            print(f"Warning: the title says {title_count} days, but the itinerary contains {actual}.", file=sys.stderr)
        else:
            print(f"提醒：标题写了 {title_count} 天，行程里有 {actual} 天。", file=sys.stderr)
    print(f"Created: {target}" if report["locale"] == "en-US" else f"已生成：{target}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"生成失败 / Render failed: {error}", file=sys.stderr)
        sys.exit(1)
