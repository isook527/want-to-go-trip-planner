from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "want-to-go-trip-planner"
SCRIPT = SKILL / "scripts" / "want_to_go.py"
RENDERER = SKILL / "renderer" / "render_report.mjs"
DISPLAY_SCRIPT = SKILL / "scripts" / "prepare_display_image.py"
EXTRACT_SCRIPT = SKILL / "scripts" / "extract_evidence.py"

SPEC = importlib.util.spec_from_file_location("want_to_go", SCRIPT)
assert SPEC and SPEC.loader
W2G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W2G)

EXTRACT_SPEC = importlib.util.spec_from_file_location("extract_evidence", EXTRACT_SCRIPT)
assert EXTRACT_SPEC and EXTRACT_SPEC.loader
EXTRACT = importlib.util.module_from_spec(EXTRACT_SPEC)
EXTRACT_SPEC.loader.exec_module(EXTRACT)


def complete_business(**overrides):
    place = {
        "id": "place-1",
        "destination": "曼谷",
        "verifiedName": "Mae Varee",
        "nameSource": "official_page",
        "nameRequiresConfirmation": False,
        "placeType": "business",
        "address": "Thong Lo Road, Bangkok",
        "openingHoursText": "每日 06:00–22:00",
        "signature": "芒果糯米饭与现熬椰浆",
        "visitTip": "午后高峰前到店更从容",
        "originalSourceLinks": [{"url": "https://example.com/source", "label": "打开原始收藏链接"}],
    }
    place.update(overrides)
    return place


class PublicSkillRegressionTests(unittest.TestCase):
    maxDiff = None

    def run_onboarding(self, locale: str) -> str:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "onboarding", "--locale", locale],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def render(self, payload: dict) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "passport.json"
            output_path = Path(tmp) / "passport.html"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            subprocess.run(
                ["node", str(RENDERER), str(input_path), str(output_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            return output_path.read_text(encoding="utf-8")

    def test_01_onboarding_zh_customer_safe(self):
        output = self.run_onboarding("zh-CN")
        self.assertIn("想去库", output)
        self.assertIn("生成曼谷想去护照", output)
        self.assertIn("¥39.9 完整逐日行程", output)
        for banned in ("OCR", "模型", "宿主诊断", "/Users/", ".workbuddy", "localhost"):
            self.assertNotIn(banned, output)

    def test_02_onboarding_en_customer_safe(self):
        output = self.run_onboarding("en")
        self.assertIn("want-to-go library", output)
        self.assertIn("Create my Bangkok Go passport", output)
        self.assertIn("¥39.9 complete day-by-day itinerary", output)
        for banned in ("OCR", "model", "host diagnostic", "/Users/", ".workbuddy", "localhost"):
            self.assertNotIn(banned, output.lower())

    def test_03_current_message_only_contract(self):
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("本轮消息", skill_text)
        self.assertIn("禁止扫描 Downloads、Desktop、最近文件、相邻工作区", skill_text)
        self.assertIn("禁止按时间、文件名或目录位置猜附件", skill_text)

    def test_04_source_link_preserved_when_fetch_failed(self):
        links = W2G.source_links(
            {"originalSourceLinks": [{"url": "https://xhslink.cn/o/example", "label": ""}]}
        )
        self.assertEqual(
            links,
            [{"url": "https://xhslink.cn/o/example", "label": "打开原始收藏链接"}],
        )

    def test_05_ocr_name_remains_unverified_clue(self):
        place = complete_business(
            verifiedName="",
            name="OCR Candidate",
            nameSource="material_ocr",
            nameRequiresConfirmation=True,
        )
        self.assertIn("verifiedName", W2G.passport_missing_fields(place))

    def test_06_business_hours_fallback_requires_public_audit(self):
        place = complete_business(openingHoursText="")
        self.assertIn("openingHoursText", W2G.passport_missing_fields(place))
        place["detailLookupAudit"] = [
            {
                "field": "openingHoursText",
                "status": "not_found",
                "checkedAt": "2026-07-31T10:00:00Z",
                "checkedUrls": ["https://example.com/official"],
                "reason": "合法公开来源未公布营业时间",
            }
        ]
        self.assertNotIn("openingHoursText", W2G.passport_missing_fields(place))
        customer = W2G.customer_place(place, "zh-CN")
        self.assertEqual(customer["openingHoursText"], "营业时间请以出发当天商户公开信息为准")

    def test_07_venue_requires_real_opening_hours(self):
        venue = complete_business(placeType="venue", openingHoursText="")
        self.assertIn("openingHoursText", W2G.passport_missing_fields(venue))
        venue["detailLookupAudit"] = [
            {
                "field": "openingHoursText",
                "status": "not_found",
                "checkedAt": "2026-07-31T10:00:00Z",
                "checkedUrls": ["https://example.com/venue"],
            }
        ]
        self.assertIn("openingHoursText", W2G.passport_missing_fields(venue))

    def test_08_public_space_uses_position_and_safe_hours_copy(self):
        place = complete_business(
            placeType="public_space",
            address="",
            positionText="安福路与武康路之间",
            openingHoursText="",
        )
        self.assertEqual(W2G.passport_missing_fields(place), [])
        customer = W2G.customer_place(place, "zh-CN")
        self.assertEqual(
            customer["openingHoursText"],
            "公共空间无统一营业时间，场内商户各自安排",
        )

    def test_09_citywalk_route_has_its_own_completeness_rule(self):
        route = complete_business(
            placeType="route",
            address="",
            openingHoursText="",
            waypoints=["安福路", "延庆路", "东湖路"],
            suggestedDuration="约 3 小时",
        )
        self.assertEqual(W2G.passport_missing_fields(route), [])

    def test_10_partial_passport_delivers_complete_and_retains_clue(self):
        with tempfile.TemporaryDirectory() as tmp:
            library_path = Path(tmp) / "library.json"
            output_path = Path(tmp) / "passport.json"
            incomplete = complete_business(
                id="place-2",
                verifiedName="",
                name="Draft Cafe",
                nameSource="material_ocr",
                nameRequiresConfirmation=True,
                address="",
                openingHoursText="",
            )
            library = {
                "schemaVersion": "1.2.3",
                "bundles": [{"bundleId": "bundle-1", "destination": "曼谷"}],
                "places": [complete_business(), incomplete],
            }
            library_path.write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")
            W2G.command_passport(
                argparse.Namespace(
                    library=str(library_path),
                    destination="曼谷",
                    output=str(output_path),
                    locale="zh-CN",
                )
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            saved = json.loads(library_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["places"]), 1)
            self.assertEqual(payload["retainedClueCount"], 1)
            self.assertEqual(len(saved["places"]), 2)
            self.assertTrue(saved["bundles"][0]["passportGenerated"])

    def test_11_locked_renderer_keeps_cta_source_link_and_retained_count(self):
        payload = {
            "locale": "zh-CN",
            "destination": "曼谷",
            "title": "Go passport · 曼谷",
            "places": [W2G.customer_place(complete_business(), "zh-CN")],
            "retainedClueCount": 2,
            "cta": {
                "price": "¥39.9",
                "title": "完整逐日行程",
                "url": "https://trip-api.kornvia.com/trip-requests",
            },
        }
        html = self.render(payload)
        self.assertIn("kornvia-passport-1.2.3", html)
        self.assertIn("¥39.9", html)
        self.assertIn("https://trip-api.kornvia.com/trip-requests", html)
        self.assertIn("https://example.com/source", html)
        self.assertIn("另有 2 条地点线索已保留", html)

    def test_12_customer_outputs_have_no_internal_or_old_payment_terms(self):
        html = self.render(
            {
                "locale": "zh-CN",
                "destination": "曼谷",
                "places": [W2G.customer_place(complete_business(), "zh-CN")],
                "retainedClueCount": 0,
                "cta": {
                    "price": "¥39.9",
                    "title": "完整逐日行程",
                    "url": "https://trip-api.kornvia.com/trip-requests",
                },
            }
        )
        customer_text = self.run_onboarding("zh-CN") + "\n" + html
        customer_text = re.sub(
            r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+",
            "[embedded-customer-image]",
            customer_text,
        )
        for banned in (
            "sourceIds",
            "OCR",
            "confidence",
            "schema",
            "candidate",
            "diagnostic",
            "material_ocr",
            "bundleId",
            "nameSource",
            ".workbuddy",
            "/Users/",
            "localhost",
            "127.0.0.1",
            "hostedCheckoutReady",
            "paidPlanningReady",
            "¥0.01",
            "PAYMENT_MODE",
            "内测",
        ):
            self.assertNotIn(banned, customer_text)

    def test_13_display_image_finds_photo_rich_region_and_makes_four_three_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            output = Path(tmp) / "display.jpg"
            image = Image.new("RGB", (300, 700), "#f4f1eb")
            pixels = image.load()
            for y in range(90, 390):
                for x in range(300):
                    pixels[x, y] = (
                        (x * 7 + y * 3) % 256,
                        (x * 2 + y * 11) % 256,
                        (x * 13 + y * 5) % 256,
                    )
            for y in range(430, 650, 42):
                for x in range(28, 270):
                    for line_y in range(y, min(y + 9, 700)):
                        pixels[x, line_y] = (24, 24, 24)
            image.save(source)
            result = subprocess.run(
                [sys.executable, str(DISPLAY_SCRIPT), str(source), str(output), "--json"],
                check=True,
                capture_output=True,
                text=True,
            )
            metadata = json.loads(result.stdout)
            self.assertTrue(metadata["photoRich"])
            self.assertLess(metadata["cropBox"]["y"], 390)
            with Image.open(output) as image:
                self.assertAlmostEqual(image.width / image.height, 4 / 3, places=2)

    def test_14_promote_and_render_keep_photo_lim_link_and_complete_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_image = base / "source.png"
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            Image.new("RGB", (480, 720), "#f7b918").save(source_image)
            # Keep the PNG valid while forcing the embedded Base64 payload to contain
            # the customer-output scan token "OCR". Binary image data must never be
            # interpreted as visible customer copy.
            raw = source_image.read_bytes()
            raw += b"\0" * ((3 - len(raw) % 3) % 3)
            raw += base64.b64decode("OCRa")
            source_image.write_bytes(raw)
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "bundle-1",
                                "destination": "曼谷",
                                "evidence": [
                                    {
                                        "sourceId": "img-1",
                                        "type": "screenshot",
                                        "localPath": str(source_image),
                                        "collectionGroup": "mae-varee",
                                    },
                                    {
                                        "sourceId": "link-1",
                                        "type": "link",
                                        "originalUrl": "https://xhslink.cn/o/example",
                                        "collectionGroup": "mae-varee",
                                    },
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(
                                sourceIds=["img-1"],
                                sourceLinks=[],
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="bundle-1",
                    selections=str(selections_path),
                )
            )
            library = json.loads(library_path.read_text(encoding="utf-8"))
            place = library["places"][0]
            self.assertEqual(place["displayPhoto"]["path"], str(source_image))
            self.assertIn(
                "https://xhslink.cn/o/example",
                [item["url"] for item in place["originalSourceLinks"]],
            )

            html = self.render(
                {
                    "locale": "zh-CN",
                    "destination": "曼谷",
                    "places": [W2G.customer_place(place, "zh-CN")],
                    "retainedClueCount": 0,
                }
            )
            self.assertIn("Thong Lo Road, Bangkok", html)
            self.assertIn("每日 06:00–22:00", html)
            self.assertIn("https://xhslink.cn/o/example", html)
            self.assertIn("data:image/png;base64,", html)
            self.assertIn("OCRa", html)
            self.assertEqual(html.count('class="lim"'), 1)

    def test_15_inline_request_form_posts_to_api_and_external_page_is_fallback(self):
        html = self.render(
            {
                "locale": "zh-CN",
                "destination": "曼谷",
                "places": [W2G.customer_place(complete_business(), "zh-CN")],
                "retainedClueCount": 0,
            }
        )
        self.assertIn("kornvia-passport-1.2.3", html)
        self.assertIn("background:#F2B51D;color:var(--ink)", html)
        self.assertIn(".photo{aspect-ratio:4/3", html)
        self.assertIn("flex:0 0 auto", html)
        self.assertNotIn("gap:18px;height:100%", html)
        self.assertEqual(html.count("box-shadow:10px 12px 0 var(--ink)"), 2)
        self.assertIn("border:5px solid var(--ink)", html)
        self.assertIn(
            'action="https://trip-api.kornvia.com/trip-requests" method="post"',
            html,
        )
        self.assertIn('target="_blank"', html)
        self.assertIn('name="destination" value="曼谷"', html)
        self.assertIn('name="startDate" type="date" required', html)
        self.assertIn('name="days" required', html)
        self.assertIn('name="contact" required maxlength="120"', html)
        self.assertIn('name="notes" maxlength="1200"', html)
        self.assertIn('name="locale" value="zh-CN"', html)
        self.assertIn('name="placeCount" value="1"', html)
        self.assertIn('name="website" tabindex="-1"', html)
        self.assertIn("提交需求，不会立即扣款", html)
        self.assertIn('class="fallback"', html)
        self.assertIn("打开备用需求页", html)
        self.assertIn("destination=%E6%9B%BC%E8%B0%B7", html)
        self.assertNotIn(">提交完整行程需求</a>", html)

    def test_16_departure_tip_alias_is_accepted_and_rendered(self):
        place = complete_business(visitTip="", departureTip="雨天优先打车到门口")
        self.assertEqual(W2G.passport_missing_fields(place), [])
        self.assertEqual(W2G.customer_place(place, "zh-CN")["visitTip"], "雨天优先打车到门口")

    def test_17_plain_text_extracts_latin_place_name_as_draft(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "extract_evidence.py"),
                "extract",
                "--text",
                "曼谷 The Jam Factory 河边园区，有书店和咖啡",
                "--destination",
                "曼谷",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["name"], "The Jam Factory")
        self.assertTrue(payload["nameRequiresConfirmation"])

    def test_18_passport_error_lists_missing_fields_in_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            library_path = Path(tmp) / "library.json"
            output_path = Path(tmp) / "passport.json"
            incomplete = complete_business(signature="", visitTip="", departureTip="")
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [{"bundleId": "bundle-1", "destination": "曼谷"}],
                        "places": [incomplete],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Mae Varee.*想去理由.*出发提醒"):
                W2G.command_passport(
                    argparse.Namespace(
                        library=str(library_path),
                        destination="曼谷",
                        output=str(output_path),
                        locale="zh-CN",
                    )
                )

    def test_19_each_place_keeps_only_its_grouped_links_and_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            image_a = base / "a.png"
            image_b = base / "b.png"
            Image.new("RGB", (120, 80), "#f2b51d").save(image_a)
            Image.new("RGB", (120, 80), "#62b2df").save(image_b)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "bundle-1",
                                "destination": "曼谷",
                                "evidence": [
                                    {"sourceId": "img-a", "type": "screenshot", "localPath": str(image_a), "collectionGroup": "place-a"},
                                    {"sourceId": "link-a", "type": "link", "originalUrl": "https://example.com/a", "collectionGroup": "place-a"},
                                    {"sourceId": "img-b", "type": "screenshot", "localPath": str(image_b), "collectionGroup": "place-b"},
                                    {"sourceId": "link-b", "type": "link", "originalUrl": "https://example.com/b", "collectionGroup": "place-b"},
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(id="place-a", verifiedName="Place A", sourceIds=["img-a"], sourceLinks=[]),
                            complete_business(id="place-b", verifiedName="Place B", sourceIds=["img-b"], sourceLinks=[]),
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="bundle-1",
                    selections=str(selections_path),
                )
            )
            places = {item["id"]: item for item in json.loads(library_path.read_text())["places"]}
            self.assertEqual([item["url"] for item in places["place-a"]["originalSourceLinks"]], ["https://example.com/a"])
            self.assertEqual([item["url"] for item in places["place-b"]["originalSourceLinks"]], ["https://example.com/b"])
            self.assertEqual(places["place-a"]["displayPhoto"]["path"], str(image_a))
            self.assertEqual(places["place-b"]["displayPhoto"]["path"], str(image_b))
            html = self.render(
                {
                    "locale": "zh-CN",
                    "destination": "曼谷",
                    "places": [W2G.customer_place(places["place-a"], "zh-CN"), W2G.customer_place(places["place-b"], "zh-CN")],
                    "retainedClueCount": 0,
                }
            )
            cards = re.findall(r'<article class="card">(.*?)</article>', html, re.S)
            self.assertEqual(len(cards), 2)
            self.assertIn("https://example.com/a", cards[0])
            self.assertNotIn("https://example.com/b", cards[0])
            self.assertIn("https://example.com/b", cards[1])
            self.assertNotIn("https://example.com/a", cards[1])
            self.assertEqual(html.count('class="photo"'), 2)

    def test_20_skill_documents_manifest_and_per_place_source_contract(self):
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for required in (
            '"sources"',
            '"type": "screenshot"',
            '"group": "place-1"',
            'sourceIds',
            "同一张截图包含多个地点",
            "链接读取边界",
            "不能填写后续查询地址、营业时间时找到的网页",
            "地点只有图片、视频或文字来源而没有顾客提供的 URL 时，不生成原始链接模块",
        ):
            self.assertIn(required, skill_text)

    def test_21_shared_screenshot_is_kept_on_each_related_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shared_image = base / "shared.png"
            Image.new("RGB", (120, 80), "#f2b51d").save(shared_image)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "bundle-shared",
                                "destination": "曼谷",
                                "evidence": [
                                    {
                                        "sourceId": "shared-shot",
                                        "type": "screenshot",
                                        "localPath": str(shared_image),
                                    }
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(id="song-wat", verifiedName="Song Wat Road", sourceIds=["shared-shot"]),
                            complete_business(id="warehouse-30", verifiedName="Warehouse 30", sourceIds=["shared-shot"]),
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="bundle-shared",
                    selections=str(selections_path),
                )
            )
            places = json.loads(library_path.read_text(encoding="utf-8"))["places"]
            self.assertEqual([place["displayPhoto"]["path"] for place in places], [str(shared_image), str(shared_image)])
            html = self.render(
                {
                    "locale": "zh-CN",
                    "destination": "曼谷",
                    "places": [W2G.customer_place(place, "zh-CN") for place in places],
                    "retainedClueCount": 0,
                }
            )
            self.assertEqual(html.count('class="photo"'), 2)

    def test_22_image_only_place_has_no_original_link_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            image = base / "customer-shot.png"
            Image.new("RGB", (120, 80), "#f2b51d").save(image)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "image-only",
                                "destination": "曼谷",
                                "evidence": [
                                    {
                                        "sourceId": "shot-1",
                                        "type": "screenshot",
                                        "localPath": str(image),
                                    }
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selection = complete_business(
                sourceIds=["shot-1"],
                sourceLinks=[{"url": "https://example.com/research-page"}],
                originalSourceLinks=[{"url": "https://example.com/agent-invented"}],
                detailLookupAudit=[
                    {
                        "field": "openingHoursText",
                        "status": "verified",
                        "checkedUrls": ["https://example.com/official-hours"],
                    }
                ],
            )
            selections_path.write_text(
                json.dumps({"selections": [selection]}, ensure_ascii=False),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="image-only",
                    selections=str(selections_path),
                )
            )
            place = json.loads(library_path.read_text(encoding="utf-8"))["places"][0]
            self.assertEqual(place["originalSourceLinks"], [])
            html = self.render(
                {
                    "locale": "zh-CN",
                    "destination": "曼谷",
                    "places": [W2G.customer_place(place, "zh-CN")],
                    "retainedClueCount": 0,
                }
            )
            self.assertNotIn('class="sources"', html)
            self.assertNotIn("research-page", html)
            self.assertNotIn("agent-invented", html)
            self.assertNotIn("official-hours", html)

    def test_23_only_customer_submitted_url_becomes_original_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            customer_url = "https://xhslink.cn/o/customer-save"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "link-input",
                                "destination": "曼谷",
                                "evidence": [
                                    {
                                        "sourceId": "link-1",
                                        "sourceType": "link",
                                        "userOriginalUrl": customer_url,
                                        "finalUrl": "https://www.xiaohongshu.com/explore/redirected",
                                        "canonicalUrl": "https://example.com/canonical",
                                    }
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(
                                sourceIds=["link-1"],
                                sourceLinks=[{"url": "https://example.com/research"}],
                                originalSourceLinks=[{"url": "https://example.com/agent-invented"}],
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="link-input",
                    selections=str(selections_path),
                )
            )
            place = json.loads(library_path.read_text(encoding="utf-8"))["places"][0]
            self.assertEqual([item["url"] for item in place["originalSourceLinks"]], [customer_url])
            html = self.render(
                {
                    "locale": "zh-CN",
                    "destination": "曼谷",
                    "places": [W2G.customer_place(place, "zh-CN")],
                    "retainedClueCount": 0,
                }
            )
            self.assertIn(customer_url, html)
            self.assertNotIn("redirected", html)
            self.assertNotIn("canonical", html)
            self.assertNotIn("research", html)
            self.assertNotIn("agent-invented", html)

    def test_24_failed_customer_link_is_still_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            customer_url = "https://xhslink.cn/o/fetch-blocked"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "failed-link",
                                "destination": "曼谷",
                                "evidence": [],
                                "failures": [
                                    {
                                        "sourceId": "link-failed",
                                        "type": "link",
                                        "value": customer_url,
                                        "status": "failed",
                                        "reason": "403",
                                    }
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {"selections": [complete_business(sourceIds=["link-failed"])]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="failed-link",
                    selections=str(selections_path),
                )
            )
            place = json.loads(library_path.read_text(encoding="utf-8"))["places"][0]
            self.assertEqual([item["url"] for item in place["originalSourceLinks"]], [customer_url])

    def test_25_real_batch_keeps_and_renders_screenshot_when_ocr_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            screenshot = base / "customer-upload.png"
            manifest = base / "manifest.json"
            evidence_path = base / "evidence.json"
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            passport_path = base / "passport.json"
            image = Image.new("RGB", (320, 220))
            pixels = image.load()
            for y in range(220):
                for x in range(320):
                    pixels[x, y] = (
                        (x * 9 + y * 5) % 256,
                        (x * 3 + y * 13) % 256,
                        (x * 15 + y * 7) % 256,
                    )
            image.save(screenshot)
            manifest.write_text(
                json.dumps(
                    {
                        "bundleId": "photo-bundle",
                        "storageMode": "durable",
                        "outputLocale": "zh-CN",
                        "sources": [
                            {
                                "id": "shot-1",
                                "group": "commons",
                                "destination": "曼谷",
                                "type": "screenshot",
                                "path": str(screenshot),
                                "name": "theCOMMONS",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = EXTRACT.parser().parse_args(
                ["batch", "--manifest", str(manifest), "--output", str(evidence_path)]
            )
            with mock.patch.object(EXTRACT, "screenshot_ocr", side_effect=ValueError("OCR unavailable")):
                args.func(args)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            item = evidence["evidence"][0]
            self.assertEqual(item["ocrStatus"], "unavailable")
            self.assertEqual(item["sourceType"], "screenshot")
            self.assertTrue(Path(item["localPath"]).is_file())
            self.assertNotEqual(Path(item["localPath"]), screenshot)
            self.assertTrue(item["displayPhotoEligible"])
            self.assertTrue(Path(item["displayPhoto"]["path"]).is_file())
            self.assertNotEqual(item["displayPhoto"]["path"], item["localPath"])

            W2G.command_ingest(argparse.Namespace(library=str(library_path), evidence=str(evidence_path)))
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(
                                id="commons",
                                verifiedName="theCOMMONS",
                                destination="",
                                sourceIds=["shot-1"],
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="photo-bundle",
                    selections=str(selections_path),
                )
            )
            place = json.loads(library_path.read_text(encoding="utf-8"))["places"][0]
            self.assertEqual(place["displayPhoto"]["path"], item["displayPhoto"]["path"])
            W2G.command_passport(
                argparse.Namespace(
                    library=str(library_path),
                    destination="曼谷",
                    output=str(passport_path),
                    locale="zh-CN",
                )
            )
            html = self.render(json.loads(passport_path.read_text(encoding="utf-8")))
            self.assertEqual(html.count('class="photo"'), 1)
            self.assertIn("data:image/jpeg;base64,", html)

    def test_26_mixed_batch_is_partitioned_by_each_source_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            passport_path = base / "shanghai.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "mixed",
                                "destination": "上海",
                                "evidence": [
                                    {"sourceId": "sh", "sourceType": "text", "destination": "Shanghai"},
                                    {"sourceId": "bkk", "sourceType": "text", "destination": "曼谷"},
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(id="sh-place", verifiedName="上海地点", destination="", sourceIds=["sh"]),
                            complete_business(id="bkk-place", verifiedName="曼谷地点", destination="", sourceIds=["bkk"]),
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(library=str(library_path), bundle_id="mixed", selections=str(selections_path))
            )
            library = json.loads(library_path.read_text(encoding="utf-8"))
            places = {item["id"]: item for item in library["places"]}
            self.assertEqual(places["sh-place"]["destinationKey"], "shanghai")
            self.assertEqual(places["bkk-place"]["destinationKey"], "bangkok")
            self.assertEqual(
                {item["destinationKey"] for item in library["destinationCollections"]},
                {"shanghai", "bangkok"},
            )
            W2G.command_passport(
                argparse.Namespace(
                    library=str(library_path),
                    destination="上海",
                    output=str(passport_path),
                    locale="zh-CN",
                )
            )
            report = json.loads(passport_path.read_text(encoding="utf-8"))
            self.assertEqual([item["name"] for item in report["places"]], ["上海地点"])

    def test_27_one_place_cannot_link_sources_from_multiple_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "conflict",
                                "evidence": [
                                    {"sourceId": "sh", "sourceType": "text", "destination": "上海"},
                                    {"sourceId": "bkk", "sourceType": "text", "destination": "曼谷"},
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {"selections": [complete_business(destination="", sourceIds=["sh", "bkk"])]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "同一地点来源包含多个目的地"):
                W2G.command_promote(
                    argparse.Namespace(
                        library=str(library_path), bundle_id="conflict", selections=str(selections_path)
                    )
                )

    def test_28_repeat_upload_merges_same_destination_name_and_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library_path = base / "library.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {"bundleId": "day-1", "evidence": [{"sourceId": "s1", "sourceType": "text", "destination": "曼谷"}]},
                            {"bundleId": "day-2", "evidence": [{"sourceId": "s2", "sourceType": "text", "destination": "Bangkok"}]},
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            for bundle_id, place_id, source_id in (("day-1", "first", "s1"), ("day-2", "second", "s2")):
                selections_path = base / f"{bundle_id}.json"
                selections_path.write_text(
                    json.dumps(
                        {
                            "selections": [
                                complete_business(
                                    id=place_id,
                                    destination="",
                                    verifiedName="theCOMMONS",
                                    address="335 Sukhumvit Road",
                                    sourceIds=[source_id],
                                )
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                W2G.command_promote(
                    argparse.Namespace(
                        library=str(library_path), bundle_id=bundle_id, selections=str(selections_path)
                    )
                )
            library = json.loads(library_path.read_text(encoding="utf-8"))
            self.assertEqual(len(library["places"]), 1)
            self.assertEqual(library["places"][0]["sourceIds"], ["s1", "s2"])
            self.assertEqual(library["places"][0]["bundleIds"], ["day-1", "day-2"])

    def test_29_unknown_destination_stays_pending_and_out_of_city_passports(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            passport_path = base / "passport.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {"bundleId": "unknown", "destination": "", "evidence": [{"sourceId": "u", "sourceType": "text"}]}
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {"selections": [complete_business(destination="", sourceIds=["u"])]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(library=str(library_path), bundle_id="unknown", selections=str(selections_path))
            )
            library = json.loads(library_path.read_text(encoding="utf-8"))
            self.assertEqual(library["places"][0]["destinationKey"], "pending")
            self.assertEqual(library["places"][0]["destinationStatus"], "needs_confirmation")
            with self.assertRaisesRegex(ValueError, "没有找到目的地"):
                W2G.command_passport(
                    argparse.Namespace(
                        library=str(library_path),
                        destination="曼谷",
                        output=str(passport_path),
                        locale="zh-CN",
                    )
                )

    def test_30_legacy_library_builds_destination_collections_on_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            library_path = Path(tmp) / "legacy.json"
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.1.13",
                        "bundles": [],
                        "places": [
                            complete_business(id="legacy-sh", destination="Shanghai"),
                            complete_business(id="legacy-bkk", destination="曼谷", address="Sukhumvit Road"),
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            library = W2G.load_library(library_path)
            W2G.save_library(library_path, library)
            migrated = json.loads(library_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schemaVersion"], "1.2.3")
            self.assertEqual(
                {item["destinationKey"] for item in migrated["destinationCollections"]},
                {"shanghai", "bangkok"},
            )

    def test_31_current_cta_and_403_documentation_are_locked(self):
        renderer = RENDERER.read_text(encoding="utf-8")
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("https://trip-api.kornvia.com/trip-requests", renderer)
        self.assertNotIn("https://kornvia.com/trip-requests", renderer)
        self.assertIn("即使自动抓取失败，也必须留在想去库", skill_text)
        self.assertIn("没有顾客提供 URL", skill_text)

    def test_32_text_dominant_mobile_screenshot_is_not_used_as_main_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            screenshot = base / "text-slide.png"
            manifest = base / "manifest.json"
            evidence_path = base / "evidence.json"
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            image = Image.new("RGB", (300, 700), "#17171a")
            pixels = image.load()
            for y in range(120, 610, 54):
                width = 230 if (y // 54) % 2 else 170
                for x in range(32, 32 + width):
                    for line_y in range(y, min(y + 8, 700)):
                        pixels[x, line_y] = (238, 238, 238)
            image.save(screenshot)
            manifest.write_text(
                json.dumps(
                    {
                        "bundleId": "text-only",
                        "storageMode": "durable",
                        "sources": [
                            {
                                "id": "text-shot",
                                "group": "same-place",
                                "destination": "上海",
                                "type": "screenshot",
                                "path": str(screenshot),
                                "name": "文字页地点",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = EXTRACT.parser().parse_args(
                ["batch", "--manifest", str(manifest), "--output", str(evidence_path)]
            )
            with mock.patch.object(EXTRACT, "screenshot_ocr", side_effect=ValueError("OCR unavailable")):
                args.func(args)
            item = json.loads(evidence_path.read_text(encoding="utf-8"))["evidence"][0]
            self.assertFalse(item["displayPhotoEligible"])
            self.assertEqual(item["displayPhotoStatus"], "text_dominant")
            self.assertNotIn("displayPhoto", item)

            W2G.command_ingest(argparse.Namespace(library=str(library_path), evidence=str(evidence_path)))
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(
                                id="text-only-place",
                                destination="",
                                verifiedName="文字页地点",
                                sourceIds=["text-shot"],
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="text-only",
                    selections=str(selections_path),
                )
            )
            place = json.loads(library_path.read_text(encoding="utf-8"))["places"][0]
            self.assertNotIn("displayPhoto", place)

    def test_33_same_group_chooses_highest_scoring_photo_instead_of_first_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            text_image = base / "text.jpg"
            photo_image = base / "photo.jpg"
            library_path = base / "library.json"
            selections_path = base / "selections.json"
            Image.new("RGB", (300, 225), "#191919").save(text_image)
            Image.new("RGB", (300, 225), "#d99138").save(photo_image)
            library_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.2.3",
                        "bundles": [
                            {
                                "bundleId": "best-photo",
                                "evidence": [
                                    {
                                        "sourceId": "first-text",
                                        "sourceType": "screenshot",
                                        "destination": "上海",
                                        "collectionGroup": "place-a",
                                        "displayPhotoEligible": False,
                                        "displayPhotoScore": 0.08,
                                        "displayPhoto": {"path": str(text_image), "qualityScore": 0.08},
                                    },
                                    {
                                        "sourceId": "second-photo",
                                        "sourceType": "screenshot",
                                        "destination": "上海",
                                        "collectionGroup": "place-a",
                                        "displayPhotoEligible": True,
                                        "displayPhotoScore": 0.82,
                                        "displayPhoto": {"path": str(photo_image), "qualityScore": 0.82},
                                    },
                                ],
                            }
                        ],
                        "places": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selections_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            complete_business(
                                id="best-photo-place",
                                destination="",
                                sourceIds=["first-text"],
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            W2G.command_promote(
                argparse.Namespace(
                    library=str(library_path),
                    bundle_id="best-photo",
                    selections=str(selections_path),
                )
            )
            place = json.loads(library_path.read_text(encoding="utf-8"))["places"][0]
            self.assertEqual(place["displayPhoto"]["path"], str(photo_image))

    def test_34_windows_doctor_reports_full_ready_with_required_dependencies(self):
        ready_command = {"installed": True, "version": "ready", "ready": True}
        with (
            mock.patch.object(EXTRACT.sys, "platform", "win32"),
            mock.patch.object(
                EXTRACT,
                "pillow_probe",
                return_value={"installed": True, "version": "11.0.0", "ready": True},
            ),
            mock.patch.object(
                EXTRACT,
                "node_probe",
                return_value={
                    "installed": True,
                    "version": "v20.18.0",
                    "major": 20,
                    "minimumMajor": 18,
                    "ready": True,
                },
            ),
            mock.patch.object(
                EXTRACT,
                "tesseract_probe",
                return_value={
                    "installed": True,
                    "version": "tesseract 5.5.0",
                    "languages": ["chi_sim", "eng"],
                    "englishReady": True,
                    "chineseReady": True,
                    "ready": True,
                },
            ),
            mock.patch.object(EXTRACT, "command_probe", return_value=ready_command),
        ):
            report = EXTRACT.doctor_report("zh-CN")
        self.assertEqual(report["platform"]["name"], "Windows")
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["coreReady"])
        self.assertTrue(report["fullReady"])
        self.assertEqual(report["ocrProvider"], "tesseract")
        self.assertTrue(report["features"]["htmlPassport"])
        self.assertTrue(report["features"]["videoBinaryAnalysis"])

    def test_35_windows_doctor_blocks_core_gaps_and_lists_repairs(self):
        missing = {"installed": False, "version": "", "ready": False}
        with (
            mock.patch.object(EXTRACT.sys, "platform", "win32"),
            mock.patch.object(EXTRACT, "pillow_probe", return_value=missing),
            mock.patch.object(
                EXTRACT,
                "node_probe",
                return_value={**missing, "major": 0, "minimumMajor": 18},
            ),
            mock.patch.object(
                EXTRACT,
                "tesseract_probe",
                return_value={
                    **missing,
                    "languages": [],
                    "englishReady": False,
                    "chineseReady": False,
                },
            ),
            mock.patch.object(EXTRACT, "command_probe", return_value=missing),
        ):
            report = EXTRACT.doctor_report("zh-CN")
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["coreReady"])
        self.assertFalse(report["fullReady"])
        self.assertGreaterEqual(len(report["blockingIssues"]), 2)
        self.assertIn("py -m pip install Pillow", report["installCommands"])
        self.assertIn("winget install --id OpenJS.NodeJS.LTS -e", report["installCommands"])
        self.assertIn("winget install --id UB-Mannheim.TesseractOCR -e", report["installCommands"])

    def test_36_node_and_tesseract_readiness_require_supported_versions_and_languages(self):
        with mock.patch.object(
            EXTRACT,
            "command_probe",
            return_value={"installed": True, "version": "v16.20.2", "ready": True},
        ):
            self.assertFalse(EXTRACT.node_probe()["ready"])
        with (
            mock.patch.object(EXTRACT.shutil, "which", return_value="tesseract.exe"),
            mock.patch.object(
                EXTRACT,
                "command_probe",
                return_value={"installed": True, "version": "tesseract 5.5.0", "ready": True},
            ),
            mock.patch.object(
                EXTRACT.subprocess,
                "run",
                return_value=mock.Mock(returncode=0, stdout="List of available languages\neng\nchi_sim\n", stderr=""),
            ),
        ):
            tesseract = EXTRACT.tesseract_probe()
        self.assertTrue(tesseract["ready"])
        self.assertTrue(tesseract["englishReady"])
        self.assertTrue(tesseract["chineseReady"])

    def test_37_dual_platform_entrypoints_and_version_marker_are_documented(self):
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        windows_doctor = (SKILL / "scripts" / "doctor_windows.ps1").read_text(encoding="utf-8")
        renderer = (SKILL / "renderer" / "kornvia-brand.mjs").read_text(encoding="utf-8")
        self.assertIn("正式支持 macOS 与 Windows", skill_text)
        self.assertIn("doctor_windows.ps1", skill_text)
        self.assertIn("py scripts\\want_to_go.py onboarding", skill_text)
        self.assertIn("Get-Command", windows_doctor)
        self.assertIn("kornvia-install-doctor-1.2.3", windows_doctor)
        self.assertIn("kornvia-passport-1.2.3", renderer)

    def test_38_ffmpeg_doctor_uses_supported_version_flag(self):
        missing = {"installed": False, "version": "", "ready": False}
        with (
            mock.patch.object(EXTRACT.sys, "platform", "win32"),
            mock.patch.object(
                EXTRACT,
                "pillow_probe",
                return_value={"installed": True, "version": "11.0.0", "ready": True},
            ),
            mock.patch.object(
                EXTRACT,
                "node_probe",
                return_value={
                    "installed": True,
                    "version": "v20.18.0",
                    "major": 20,
                    "minimumMajor": 18,
                    "ready": True,
                },
            ),
            mock.patch.object(EXTRACT, "tesseract_probe", return_value=missing),
            mock.patch.object(EXTRACT, "command_probe", return_value=missing) as probe,
        ):
            EXTRACT.doctor_report("zh-CN")
        self.assertIn(mock.call("ffmpeg", ("-version",)), probe.call_args_list)
        self.assertIn(mock.call("ffprobe", ("-version",)), probe.call_args_list)

    def test_39_windows_powershell_source_is_ascii_safe_and_keeps_chinese_message(self):
        windows_doctor = SKILL / "scripts" / "doctor_windows.ps1"
        raw = windows_doctor.read_bytes()
        self.assertTrue(all(byte < 128 for byte in raw))
        text = raw.decode("ascii")
        encoded = re.search(r'FromBase64String\("([A-Za-z0-9+/=]+)"\)', text)
        self.assertIsNotNone(encoded)
        message = base64.b64decode(encoded.group(1)).decode("utf-8")
        self.assertIn("缺少 Python 3.9 或更高版本", message)

    def test_40_windows_powershell_forces_utf8_for_chinese_doctor_json(self):
        text = (SKILL / "scripts" / "doctor_windows.ps1").read_text(encoding="ascii")
        self.assertIn('$env:PYTHONUTF8 = "1"', text)
        self.assertIn('$env:PYTHONIOENCODING = "utf-8"', text)
        self.assertIn("[Console]::OutputEncoding = $utf8", text)

    def test_41_windows_powershell_prefers_active_python_environment(self):
        text = (SKILL / "scripts" / "doctor_windows.ps1").read_text(encoding="ascii")
        self.assertLess(text.index('Test-Executable "python"'), text.index('Test-Executable "py"'))

    def test_42_windows_commands_lock_utf8_for_the_whole_session(self):
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(skill_text.count('$env:PYTHONUTF8 = "1"'), 2)
        self.assertIn("必须在同一个 PowerShell 会话执行", skill_text)


if __name__ == "__main__":
    unittest.main()
