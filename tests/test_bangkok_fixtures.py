"""Reproduce the Bangkok passport, lightweight layout and route acceptance cases."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "want-to-go-trip-planner"
FIXTURES = ROOT / "tests" / "fixtures"


class BangkokFixtureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.library = self.work / "library.json"
        shutil.copyfile(FIXTURES / "bangkok-evidence-library.json", self.library)
        self.route = FIXTURES / "bangkok-route.json"

    def run_cli(self, executable, path, *args, check=True):
        return subprocess.run([executable, str(path), *(str(a) for a in args)], check=check, capture_output=True,
                              text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})

    def wtg(self, command, *args, skill=SKILL, check=True):
        return self.run_cli(sys.executable, skill / "scripts" / "want_to_go.py", command, *args, check=check)

    def passport(self, locale, prefix, skill=SKILL):
        report, html = (self.work / f"{prefix}.{ext}" for ext in ("json", "html"))
        self.wtg("route-plan", "--library", self.library, "--destination", "曼谷", "--plan", self.route, skill=skill)
        self.wtg("passport", "--library", self.library, "--destination", "曼谷", "--locale", locale,
                 "--map-region", "outside_mainland", "--output", report, skill=skill)
        self.run_cli("node", skill / "renderer" / "render_report.mjs", report, html)
        return html

    def scan(self, html, check=True):
        return self.wtg("scan", "--path", html, "--mode", "customer", check=check)

    def test_old_html_is_blocked_and_new_labels_are_clean_in_both_locales(self):
        with zipfile.ZipFile(ROOT / "dist" / "want-to-go-trip-planner-skill-3.1.0.zip") as package:
            package.extractall(self.work / "baseline")
        old = self.passport("zh-CN", "old", skill=self.work / "baseline" / "want-to-go-trip-planner")
        baseline = old.read_text(encoding="utf-8")
        self.assertIn("future_queue", baseline)
        self.assertRegex(baseline, r"2026-08-09T00:00:00Z")
        self.assertNotEqual(self.scan(old, check=False).returncode, 0)

        sys.path.insert(0, str(SKILL / "scripts"))
        self.addCleanup(sys.path.remove, str(SKILL / "scripts"))
        from want_to_go import text_scan_issues
        self.assertIn("customer_internal_leak", text_scan_issues(baseline, "customer"))
        for leak in ("not_found", "weather_sensitive", "2026-08-09T00:00:00Z", "future_queue"):
            self.assertIn("customer_internal_leak", text_scan_issues(f"<p>核对{leak}状态</p>", "customer"))
        for locale, label in (("zh-CN", "8 月 9 日查询"), ("en-US", "Checked Aug 9")):
            with self.subTest(locale=locale):
                path = self.passport(locale, locale)
                html = path.read_text(encoding="utf-8")
                self.assertNotIn("future_queue", html)
                self.assertNotRegex(html, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
                self.assertIn(label, html)
                self.assertEqual(html.count('<details class="verification'), 1)
                self.assertEqual(html.count('<p class="verification-fallback"'), 3)
                self.assertNotIn('class="route-map"', html)
                self.assertIn('href="#day-01"', html)
                self.assertEqual(json.loads(self.scan(path).stdout)["status"], "clean")

    def test_manifest_uses_four_address_districts_and_hides_mostly_unknown_list(self):
        html = self.passport("zh-CN", "areas").read_text(encoding="utf-8")
        labels = set(re.findall(r'<span class="route-name">([^<]+)</span>', html))
        self.assertEqual(labels, {"Watthana", "Phra Nakhon", "Huai Khwang", "Khlong San"})
        self.assertNotIn("未分片区", html)
        report_path = self.work / "areas.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for place in report["places"][:3]:
            for key in ("area", "areaGroup", "district", "address"):
                place.pop(key, None)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        output = self.work / "unknown.html"
        self.run_cli("node", SKILL / "renderer" / "render_report.mjs", report_path, output)
        self.assertNotIn('<div class="area-route"', output.read_text(encoding="utf-8"))

    def test_lightweight_and_evidence_share_style_and_activity_remains_distinct(self):
        for locale in ("zh-CN", "en-US"):
            with self.subTest(locale=locale):
                evidence = self.passport(locale, "evidence").read_text(encoding="utf-8")
                data = json.loads((FIXTURES / "bangkok-lightweight.json").read_text(encoding="utf-8"))
                data["locale"] = locale
                source, target = self.work / "lightweight.json", self.work / "lightweight.html"
                source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self.run_cli(sys.executable, SKILL / "scripts" / "render.py", source, target)
                rendered = target.read_text(encoding="utf-8")
                self.assertEqual(re.search(r"<style>(.*?)</style>", evidence, re.S)[1],
                                 re.search(r"<style>(.*?)</style>", rendered, re.S)[1])
                self.assertIn('class="day-activity"', rendered)
                self.assertEqual(rendered.count('class="card"'), 3)
                self.assertEqual(rendered.count("www.google.com/maps/search"), 3)
                self.assertNotIn('<img ', rendered)
                self.assertNotIn('<details class="verification', rendered)
                self.scan(target)
                data.pop("days")
                source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self.run_cli(sys.executable, SKILL / "scripts" / "render.py", source, target)
                self.assertNotIn('class="journey-plan"', target.read_text(encoding="utf-8"))
                self.scan(target)

    def lint(self, locale="zh-CN"):
        return json.loads(self.wtg("lint-route", "--library", self.library, "--destination", "曼谷",
                                   "--plan", self.route, "--locale", locale).stdout)

    def test_lint_groups_six_codes_with_no_blockers(self):
        lint = self.lint()
        self.assertEqual(lint, self.lint("en-US"))
        self.assertEqual(len(lint["summary"]), 6)
        self.assertEqual(len(lint["warnings"]), 12)
        self.assertTrue(all(item["days"] == [1, 2] for item in lint["summary"]))
        self.assertEqual([item["severity"] for item in lint["summary"]], ["suggest"] * 2 + ["info"] * 4)
        self.assertIn({"code": "missing_lunch", "severity": "info", "days": [1, 2]}, lint["summary"])

    def test_lint_reports_geography_only_with_data_and_blocks_explicit_booking(self):
        library = json.loads(self.library.read_text(encoding="utf-8"))
        plan = json.loads(self.route.read_text(encoding="utf-8"))
        stops = [s for day in plan["days"] for s in day["stops"]]
        plan["tripProfile"]["days"] = 1
        plan["days"] = [{**plan["days"][0], "stops": stops}]
        stops[0]["reservationRequired"] = True
        self.route = self.work / "annotated-route.json"
        self.route.write_text(json.dumps(plan), encoding="utf-8")
        raw = {item["code"] for item in self.lint()["summary"]}
        geographies = {"day_spans_many_areas", "area_backtrack", "day_spread_wide"}
        self.assertFalse(geographies & raw)
        places = {place["id"]: place for place in library["places"]}
        for stop, area, lat in zip(stops, ("A", "B", "A", "C"), (13.74, 13.75, 13.78, 13.95)):
            places[stop["placeId"]].update(area=area, lat=lat, lng=100.50)
        self.library.write_text(json.dumps(library), encoding="utf-8")
        groups = self.lint()["summary"]
        self.assertEqual(groups[0], {"code": "reservation_unconfirmed", "severity": "blocking", "days": [1]})
        for code in geographies:
            self.assertIn({"code": code, "severity": "suggest", "days": [1]}, groups)
        plan["days"][0]["tradeoff"] = "A longer leg is acceptable for this must-go stop."
        self.route.write_text(json.dumps(plan), encoding="utf-8")
        self.assertNotIn("day_spread_wide", {item["code"] for item in self.lint()["summary"]})
