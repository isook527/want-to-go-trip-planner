#!/usr/bin/env python3
"""Render a local, self-contained travel guide; map links are search URLs."""
import html
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

from want_to_go import text_scan_issues

def esc(value):
    return html.escape(str(value or ""), quote=True)


def render(data):
    if not isinstance(data, dict) or not isinstance(data.get("places"), list):
        raise ValueError("输入须包含 places 数组")
    locale = data.get("locale", "zh-CN")
    if locale not in ("zh-CN", "en-US"):
        raise ValueError("locale must be zh-CN or en-US")
    en = locale == "en-US"
    def t(zh, english):
        return english if en else zh
    places = data["places"]
    names = set()
    for item in places:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ValueError("每个地点都需要名称")
        name = str(item["name"]).strip()
        if name in names:
            raise ValueError("地点名称重复，请加上分店或区域以区分")
        names.add(name)
    days = data.get("days", [])
    if not isinstance(days, list):
        raise ValueError("days 必须是数组")
    cards = []
    for p in places:
        name = str(p["name"]).strip()
        region = p.get("map_region", data.get("map_region", "unknown"))
        if region not in ("mainland", "outside_mainland", "unknown"):
            raise ValueError("地图区域必须是 mainland、outside_mainland 或 unknown")
        query = " ".join(str(x).strip() for x in (name, p.get("address"), p.get("city") or data.get("city")) if x and str(x).strip())
        google = "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": query})
        amap = "https://uri.amap.com/search?" + urlencode({"keyword": query})
        if region == "mainland":
            map_link = f'<a href="{esc(amap)}">{t("高德地图搜索", "Search Amap")}</a>'
        elif region == "outside_mainland":
            map_link = f'<a href="{esc(google)}">{t("Google 地图搜索", "Search Google Maps")}</a>'
        else:
            map_link = f'<span>{t("确认目的地后提供地图入口", "Confirm the destination to see a map link")}</span>'
        details = "".join(f'<div><dt>{label}</dt><dd>{esc(p.get(key) or t("待确认", "To be confirmed"))}</dd></div>' for key, label in (("address", t("地址", "Address")), ("hours", t("营业", "Hours"))))
        note = f'<p>{esc(p["note"])}</p>' if p.get("note") else ""
        cards.append(f'<article><h3>{esc(name)}</h3><dl>{details}</dl>{note}<nav>{map_link}</nav></article>')
    daycards = []
    for day in days:
        if not isinstance(day, dict) or not isinstance(day.get("visits", []), list):
            raise ValueError("每天须包含 visits 数组")
        visits = []
        for visit in day.get("visits", []):
            if not isinstance(visit, dict):
                raise ValueError('行程项类型或名称无效；地点游览须对应地点清单。')
            kind = visit.get("type", "place")
            if kind == "activity":
                label = visit.get("title")
                if not isinstance(label, str) or not label.strip() or visit.get("place"):
                    raise ValueError('行程项类型或名称无效；地点游览须对应地点清单。')
            elif kind == "place" and visit.get("place") in names:
                label = visit["place"]
            else:
                raise ValueError('行程项类型或名称无效；地点游览须对应地点清单。')
            visits.append(f'<li><span>{esc(visit.get("period", ""))}</span><strong>{esc(label)}</strong><p>{esc(visit.get("detail", ""))}</p></li>')
        daycards.append(f'<article><h3>{esc(day.get("title") or t("行程", "Itinerary"))}</h3><ol>{"".join(visits)}</ol></article>')
    route = f'<section><h2>{t("逐日安排", "Day-by-day itinerary")}</h2><div class="grid">' + "".join(daycards) + '</div></section>' if daycards else ""
    title = esc(data.get("title") or t("想去地点护照", "Places Passport"))
    return f'''<!doctype html><html lang="{locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f5f3ed;color:#21332d;font:16px/1.65 -apple-system,BlinkMacSystemFont,"Noto Sans",sans-serif}}main{{max-width:1050px;margin:auto;padding:42px 22px}}header{{padding:30px;border-radius:24px;background:#214d3e;color:#fff}}.brand{{font-size:12px;letter-spacing:.17em;text-transform:uppercase}}h1{{font-size:clamp(30px,6vw,56px);line-height:1.15;margin:18px 0 8px}}h2{{margin:42px 0 18px}}h3{{margin:0 0 14px;font-size:21px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:16px}}article{{background:white;border:1px solid #e2e7df;border-radius:18px;padding:22px}}dl{{margin:0}}dl div{{display:flex;gap:12px;margin:8px 0}}dt{{color:#6b7d72;min-width:3em}}dd{{margin:0}}nav{{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}}a{{color:#175a43;background:#edf5ee;padding:8px 12px;border-radius:9px;text-decoration:none}}ol{{padding-left:20px}}li{{padding:6px 0}}li span{{display:block;color:#6b7d72}}li p{{margin:4px 0}}footer{{margin-top:36px;color:#60756a;font-size:13px}}@media print{{body{{background:white}}main{{padding:0}}article{{break-inside:avoid}}}}
    </style></head><body><main><header><div class="brand">Kornvia · Go Passport</div><h1>{title}</h1><div>{esc(data.get("city", ""))}</div></header>{route}<section><h2>{t("想去的地方", "Saved places")}</h2><div class="grid">{"".join(cards)}</div></section><footer>{t("地图链接用于搜索地点，出发前请确认分店、营业时间和交通信息。", "Map links open a search. Confirm the branch, hours and transport before leaving.")}</footer></main></body></html>'''


def main():
    if len(sys.argv) != 3:
        raise ValueError("用法：python3 render.py input.json output.html")
    source, target = map(Path, sys.argv[1:])
    if target.suffix.lower() not in (".html", ".htm"):
        raise ValueError("Output must be an HTML file")
    if source.resolve() == target.resolve():
        raise ValueError("输入和输出不能是同一文件")
    with source.open(encoding="utf-8") as file:
        data = json.load(file)
    output = render(data)
    issues = text_scan_issues(output, "customer")
    if issues:
        raise ValueError("customer scan failed: " + ", ".join(issues))
    target.write_text(output, encoding="utf-8")
    print(f"已生成：{target}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"生成失败：{error}", file=sys.stderr)
        sys.exit(1)
