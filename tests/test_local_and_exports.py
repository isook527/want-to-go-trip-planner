import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from html.parser import HTMLParser
from xml.etree import ElementTree
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/want-to-go-trip-planner/scripts"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.append(urlsplit(dict(attrs)["href"]))


class LocalSkillTests(unittest.TestCase):
    def run_script(self, script, *args, check=True):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", check=check,
            env={**os.environ, "PYTHONUTF8": "1"},
        )

    def test_capture_reopen_export_and_render_full_chinese_itinerary(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = work / "library"
            original = work / "source.png"
            original.write_bytes(b"synthetic source bytes")
            manifest = work / "capture.json"
            manifest.write_text(json.dumps({
                "sources": [{"type": "image", "path": str(original), "processing_status": "pending"}],
                "places": [],
            }), encoding="utf-8")
            def library(*args):
                return json.loads(self.run_script("library.py", "--root", root, "--locale", "zh-CN", *args).stdout)
            capture = library("capture", manifest)
            self.assertEqual(Path(capture["sources"][0]["saved_path"]).read_bytes(), original.read_bytes())
            self.assertEqual(library("find", "上海")["unprocessed_batches"][0]["batch_id"], capture["batch_id"])
            manifest.write_text(json.dumps([
                {"name": "示例花园", "city": "上海", "city_aliases": ["Shanghai"], "status": "identified"},
            ]), encoding="utf-8")
            library("add-places", capture["batch_id"], manifest)
            library("source-processed", capture["sources"][0]["id"])
            self.assertEqual(library("find", "Shanghai")["unprocessed_batches"], [])
            self.assertEqual(library("catalog")["source_count"], 1)
            exported = work / "passport.json"
            library("export", "上海", exported, "--map-region", "mainland")
            data = json.loads(exported.read_text(encoding="utf-8"))
            data["days"] = [{"title": "第一天", "visits": [
                {"place": "示例花园", "period": "上午"},
                {"type": "activity", "title": "午休", "period": "下午"},
            ]}]
            data["places"].append({"name": "Example Museum", "city": "London", "map_region": "outside_mainland"})
            exported.write_text(json.dumps(data), encoding="utf-8")
            html = work / "passport.html"
            self.run_script("render.py", exported, html)
            page = html.read_text(encoding="utf-8")
            self.assertIn("逐日安排", page)
            self.assertIn("午休", page)
            links = Links()
            links.feed(page)
            self.assertEqual([url.hostname for url in links.urls], ["uri.amap.com", "www.google.com"])
            self.assertEqual(parse_qs(links.urls[1].query)["query"], ["Example Museum London"])

    def test_calendar_and_xlsx_are_readable_and_keep_user_text_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source, xlsx, ics = (work / item for item in ("passport.json", "itinerary.xlsx", "itinerary.ics"))
            data = json.loads((ROOT / "examples/itinerary.en-US.json").read_text(encoding="utf-8"))
            data["title"] = "東京旅行, " * 20
            data["trip_notes"]["budget"] = ["=1+1"]
            source.write_text(json.dumps(data), encoding="utf-8")
            self.run_script("export_itinerary.py", source, "--xlsx", xlsx, "--ics", ics)
            text = ics.read_bytes()
            self.assertTrue(all(len(line) <= 75 for line in text.split(b"\r\n")))
            unfolded = text.replace(b"\r\n ", b"").decode("utf-8")
            self.assertEqual(unfolded.count("BEGIN:VEVENT"), 2)
            self.assertIn("DTSTART;VALUE=DATE:20300510\r\nDTEND;VALUE=DATE:20300511", unfolded)
            self.assertIn("東京旅行\\,", unfolded)
            with zipfile.ZipFile(xlsx) as archive:
                self.assertIsNone(archive.testzip())
                for name in archive.namelist():
                    ElementTree.fromstring(archive.read(name))
                notes = archive.read("xl/worksheets/sheet4.xml")
                self.assertIn(b't="inlineStr"', notes)
                self.assertIn(b">=1+1</t>", notes)
                self.assertNotIn(b"<f>", notes)

    def test_undated_calendar_request_leaves_existing_outputs_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source, xlsx, ics = (work / item for item in ("passport.json", "itinerary.xlsx", "itinerary.ics"))
            for trip, expected_field in (
                ({"days": [{"title": "Day 1", "visits": []}]}, "days[].date"),
                ({"routePlan": {"tripProfile": {}, "days": [{"title": "Day 1", "stops": []}]}}, "tripProfile.startDate"),
            ):
                with self.subTest(expected_field=expected_field):
                    source.write_text(json.dumps({"places": [], **trip}), encoding="utf-8")
                    xlsx.write_bytes(b"previous export")
                    result = self.run_script("export_itinerary.py", source, "--xlsx", xlsx, "--ics", ics, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected_field, result.stderr)
                    self.assertEqual(xlsx.read_bytes(), b"previous export")
                    self.assertFalse(ics.exists())


if __name__ == "__main__":
    unittest.main()
