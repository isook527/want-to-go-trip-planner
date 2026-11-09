"""Behavior regressions for evidence planning, calendar revision and safe export."""
import copy
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from test_public_skill import SKILL, W2G, complete_business
from export_itinerary import make_calendar, make_xlsx
from fact_freshness import with_freshness


class ItineraryQualityTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.library = self.root / "library.json"
        library = W2G.empty_library()
        library["places"] = [
            complete_business(id=key, name=name, verifiedName=name, destinationKey="bangkok",
                              destinationStatus="confirmed", **extra)
            for key, name, extra in (
                ("museum", "Museum", {"openingHours": {"closedDays": ["fri"], "reservationLeadDays": 2},
                                      "mapRegion": "mainland"}),
                ("garden", "Garden", {"mapRegion": "outside_mainland"}),
                ("park", "Park", {"mapRegion": "unknown"}),
            )
        ]
        W2G.save_library(self.library, library)
        self.route = {
            "title": "Trip",
            "tripProfile": {"days": 1, "startDate": "2030-05-10", "reducedMobility": True,
                            "walkingLimitMeters": 4000, "mustGoPlaceIds": ["museum", "garden"]},
            "days": [{
                "day": 1, "title": "Day out",
                "stops": [{"placeId": key, "timeBlock": "afternoon"} for key in ("museum", "garden", "park")],
                "connections": [
                    {"fromPlaceId": "museum", "toPlaceId": "garden", "mode": "walk",
                     "durationMinutes": 100, "distanceMeters": 5400, "status": "verified", "checkedAt": "2025-05-01"},
                    {"mode": "flight", "fromLabel": "Bangkok airport", "toLabel": "Next city airport",
                     "durationMinutes": 90, "status": "unverified", "note": "Airport buffers still need checking."},
                ],
                "onsiteWalkingMeters": 400,
            }],
        }

    def cli(self, command, *args, check=True):
        return subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "want_to_go.py"), command,
             "--library", str(self.library), "--destination", "曼谷", *(str(a) for a in args)],
            check=check, capture_output=True, text=True, encoding="utf-8",
        )

    def write(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def save_route(self, route, name="route.json"):
        return json.loads(self.cli("route-plan", "--plan", self.write(name, route)).stdout)

    def passport(self, name="passport.json"):
        path = self.root / name
        self.cli("passport", "--output", path)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_lint_is_read_only_and_flags_overloaded_day(self):
        source = self.write("route.json", self.route)
        before = self.library.read_bytes()
        lint = json.loads(self.cli("lint-route", "--plan", source).stdout)
        self.assertEqual(before, self.library.read_bytes())
        self.assertEqual(lint["metrics"][0]["knownCommuteMinutes"], 190)
        self.assertFalse(lint["metrics"][0]["commuteComplete"])
        codes = {warning["code"] for warning in lint["warnings"]}
        self.assertTrue({
            "too_many_major_stops", "opening_hours_conflict", "reservation_unconfirmed",
            "too_many_unverified_connections", "daily_commute_over_limit", "distant_must_go_needs_tradeoff",
            "walking_over_limit", "missing_lunch", "missing_rest", "missing_backup", "missing_dropIfDelayed",
        } <= codes)
        saved = self.save_route(self.route)
        self.assertEqual(saved["warnings"], lint["warnings"])
        self.assertEqual(saved["metrics"], lint["metrics"])

    def test_mixed_region_links_and_intercity_flight_render(self):
        self.save_route(self.route)
        passport = self.passport()
        flight = passport["routePlan"]["days"][0]["connections"][-1]
        self.assertEqual((flight["mode"], flight["from"], flight["to"]),
                         ("flight", "Bangkok airport", "Next city airport"))
        self.assertEqual(passport["routePlan"]["days"][0]["connections"][0]["statusLabel"], W2G.FACT_STATUS_LABELS_ZH["stale"])
        html_path = self.root / "passport.html"
        subprocess.run(["node", str(SKILL / "renderer" / "render_report.mjs"),
                        str(self.root / "passport.json"), str(html_path)], check=True, capture_output=True)
        html = html_path.read_text(encoding="utf-8")
        self.assertEqual(html.count("uri.amap.com/search"), 1)
        self.assertEqual(html.count("www.google.com/maps/search"), 1)
        self.assertIn("飞机", html)

    def test_revision_and_undo_keep_calendar_identity(self):
        self.save_route(self.route)
        original = self.passport()
        repeat = self.passport("repeat.json")
        self.assertEqual(original["routePlan"]["calendarSequence"], repeat["routePlan"]["calendarSequence"])
        revised = copy.deepcopy(self.route)
        revised["title"] = "Renamed"
        revised["tripProfile"]["startDate"] = "2030-05-11"
        self.cli("route-plan", "--operation-id", "revision-test", "--plan", self.write("rev.json", revised))
        changed = self.passport("revised-passport.json")
        subprocess.run([sys.executable, str(SKILL / "scripts" / "want_to_go.py"), "undo",
                        "--library", str(self.library), "--operation-id", "undo-revision-test"],
                       check=True, capture_output=True)
        undone = self.passport("undone-passport.json")
        calendars = [make_calendar(data) for data in (original, changed, undone)]
        self.assertEqual(len({re.search(rb"UID:([^\r]+)", data)[1] for data in calendars}), 1)
        sequences = [int(re.search(rb"SEQUENCE:(\d+)", data)[1]) for data in calendars]
        self.assertLess(sequences[0], sequences[1])
        self.assertLess(sequences[1], sequences[2])
        self.assertIn(b"DTSTART;VALUE=DATE:20300511", calendars[1])
        self.assertIn(b"SUMMARY:Trip", calendars[0])
        self.assertIn(b"SUMMARY:Renamed", calendars[1])
        self.assertIn(b"SUMMARY:Trip", calendars[2])

    def test_nodes_compare_previous_review_and_keep_earlier_audits(self):
        route = copy.deepcopy(self.route)
        route["tripProfile"]["startDate"] = "2025-05-10"
        self.save_route(route)
        schedule = json.loads(self.cli("review-schedule").stdout)["nodes"]
        self.assertEqual([n["agreedReviewDate"] for n in schedule], ["2025-05-03", "2025-05-07", "2025-05-09"])

        def review(node, when, facts):
            checks = {
                "node": node, "agreedReviewDate": when, "checkedAt": when + "T12:00:00+07:00",
                "agreementConfirmed": True,
                "items": [{"placeId": "museum", "facts": facts, "factAudits": [
                    {"field": field, "status": "verified",
                     "sources": [{"url": "https://example.com/notices", "kind": "official"}]}
                    for field in facts
                ]}],
            }
            return json.loads(self.cli("review", "--checks", self.write("checks.json", checks)).stdout)

        review("T-7", "2025-05-03", {"openingStatus": "open", "price": 50})
        closure = review("T-3", "2025-05-07", {"openingStatus": "closed"})
        self.assertEqual(closure["changeGroups"]["blocking"][0]["before"], "open")
        self.assertEqual(len(closure["departureChecklist"]), 4)
        self.assertEqual(review("T-3", "2025-05-07", {"openingStatus": "open"})["status"], "already_recorded")
        final = review("T-1", "2025-05-09", {"price": 50})
        self.assertEqual(final["changes"], [])
        summaries = self.passport()["places"][0]["verificationSummary"]["items"]
        self.assertEqual({item["field"]: item["status"] for item in summaries}, {"openingStatus": "stale", "price": "stale"})

    def test_freshness_clamps_to_the_field_window_and_honors_earlier_source_expiry(self):
        checked = "2030-05-01T08:00:00+08:00"
        now = datetime.fromisoformat("2030-05-04T00:00:01+00:00")
        base = {"status": "verified", "checkedAt": checked, "validUntil": "2030-05-30"}
        weather = with_freshness({**base, "field": "weather"}, as_of=now)
        hours = with_freshness({**base, "field": "openingHours"}, as_of=now)
        earlier = with_freshness({**base, "field": "openingHours", "validUntil": "2030-05-02"}, as_of=now)
        self.assertEqual((weather["status"], weather["validUntil"]), ("stale", "2030-05-04T00:00:00Z"))
        self.assertEqual((hours["status"], hours["validUntil"]), ("verified", "2030-05-08T00:00:00Z"))
        self.assertEqual((earlier["status"], earlier["validUntil"]), ("stale", "2030-05-02"))

    def test_verified_timing_can_export_tzid_but_unverified_timing_stays_all_day(self):
        try:
            ZoneInfo("Asia/Shanghai")
        except ZoneInfoNotFoundError:
            self.skipTest("IANA timezone data unavailable")
        data = {"locale": "zh-CN", "city": "杭州", "calendarId": "hangzhou-trip", "map_region": "mainland",
                "places": [{"name": "Museum", "address": "Main St"}],
                "days": [{"day": 1, "date": "2030-05-10", "visits": [{
                    "place": "Museum", "timing": {"start": "10:00", "end": "11:00", "timeZone": "Asia/Shanghai",
                                                "status": "verified", "sourceUrl": "https://example.com/booking"},
                }]}]}
        timed = make_calendar(data)
        self.assertIn(b"BEGIN:VTIMEZONE", timed)
        self.assertIn(b"DTSTART;TZID=Asia/Shanghai:20300510T100000", timed)
        self.assertIn(b"LOCATION:Museum\\, Main St", timed)
        self.assertIn(b"URL:https://uri.amap.com/search?", timed)
        data["days"][0]["visits"][0]["timing"]["status"] = "unverified"
        self.assertIn(b"DTSTART;VALUE=DATE:20300510", make_calendar(data))

    def test_spreadsheet_styles_and_typed_cells(self):
        data = {"locale": "zh-CN", "places": [{"name": "Museum"}],
                "days": [{"date": "2030-05-10", "visits": [{"place": "Museum"}]}],
                "budget": [{"item": "=1+1", "amount": 0, "currency": "CNY", "status": "verified"}]}
        with zipfile.ZipFile(io.BytesIO(make_xlsx(data))) as workbook:
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            styles = ElementTree.fromstring(workbook.read("xl/styles.xml"))
            trip = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            budget = ElementTree.fromstring(workbook.read("xl/worksheets/sheet5.xml"))
            self.assertIsNotNone(styles.find(".//x:font/x:b", ns))
            self.assertEqual(trip.find(".//x:pane", ns).get("state"), "frozen")
            self.assertEqual(trip.find(".//x:c[@r='B2']", ns).get("s"), "3")
            self.assertEqual(budget.find(".//x:c[@r='B2']/x:v", ns).text, "0")
            self.assertEqual(budget.find(".//x:c[@r='A2']", ns).get("t"), "inlineStr")
            self.assertIsNone(budget.find(".//x:f", ns))

    def test_lightweight_renderer_rejects_a_leak_before_replacing_html(self):
        source = self.write("local.json", {"places": [{"name": "Museum", "note": "/Users/demo/private/booking.txt"}]})
        target = self.root / "local.html"
        target.write_text("earlier export", encoding="utf-8")
        result = subprocess.run([sys.executable, str(SKILL / "scripts" / "render.py"), str(source), str(target)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("customer_internal_leak", result.stderr)
        self.assertNotIn("booking.txt", result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "earlier export")
