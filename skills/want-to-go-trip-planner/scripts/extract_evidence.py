#!/usr/bin/env python3
"""Extract local place evidence from public links, screenshots, or text."""

import argparse
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse
import urllib.request


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
TIME_PATTERN = re.compile(
    r"(?:[01]?\d|2[0-3])[:：][0-5]\d\s*(?:-|–|—|至|~)\s*"
    r"(?:[01]?\d|2[0-3])[:：][0-5]\d"
)
FLOOR_PATTERN = re.compile(
    r"(?:\bB?\d{1,2}(?:F|层|楼)\b|\bL\d{1,2}\b|负一层|地下一层)",
    re.IGNORECASE,
)
ADDRESS_PATTERN = re.compile(
    r"(?:\d+\s*(?:号|弄|巷)|(?:路|街|道|区|县|镇|村|大道|"
    r"Road|Rd\.?|Street|St\.?|Avenue|Ave\.?|Soi)\b)",
    re.IGNORECASE,
)
MALL_PATTERN = re.compile(
    r"(?:商场|购物中心|广场|百货|大厦|天地|里巷|Mall|Plaza|"
    r"Shopping\s+Center|Department\s+Store)",
    re.IGNORECASE,
)
BRANCH_PATTERN = re.compile(
    r"(?:[\w\u3400-\u9fff·&'\- ]{1,36}(?:店|分店|旗舰店)|"
    r"\b(?:branch|outlet)\b)",
    re.IGNORECASE,
)
RESERVATION_PATTERN = re.compile(
    r"(?:预约|预订|订位|reservation|booking|required\s+booking)",
    re.IGNORECASE,
)
QUEUE_PATTERN = re.compile(
    r"(?:排队|等位|取号|queue|waiting\s+time)",
    re.IGNORECASE,
)
CLOSURE_PATTERN = re.compile(
    r"(?:闭店|停业|歇业|暂停营业|永久关闭|临时关闭|closed|temporarily\s+closed)",
    re.IGNORECASE,
)
WEATHER_PATTERN = re.compile(
    r"(?:户外|露天|天台|屋顶|海滩|登山|徒步|天气|下雨|雨天|"
    r"outdoor|rooftop|beach|hiking|weather|rain)",
    re.IGNORECASE,
)
NOISE_PATTERN = re.compile(
    r"(?:小红书|抖音|大众点评|点赞|收藏|评论|分享|关注|登录|打开App|"
    r"REDnote|TikTok|Dianping|copyright|隐私政策)",
    re.IGNORECASE,
)
GENERIC_NAME_PATTERN = re.compile(
    r"^(?:优先|优先级|问题|必须怎么改|详情|首页|发现|附近|发布|"
    r"更多|笔记|图片|视频|PO|P0|P1|P2)$",
    re.IGNORECASE,
)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def unique(values):
    seen = set()
    result = []
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


class PageEvidenceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.text_parts = []
        self.meta = {}
        self.json_ld = []
        self._in_title = False
        self._in_ignored = 0
        self._in_json_ld = False
        self._json_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value for key, value in attrs if key}
        lower = tag.lower()
        if lower in {"script", "style", "noscript", "svg"}:
            self._in_ignored += 1
        if lower == "title":
            self._in_title = True
        if lower == "meta":
            key = (
                attrs.get("property")
                or attrs.get("name")
                or attrs.get("itemprop")
                or ""
            ).lower()
            content = attrs.get("content")
            if key and content:
                self.meta[key] = content
        if (
            lower == "script"
            and str(attrs.get("type", "")).lower() == "application/ld+json"
        ):
            self._in_json_ld = True
            self._json_parts = []

    def handle_endtag(self, tag):
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
        if lower == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld.append("".join(self._json_parts))
            self._json_parts = []
        if lower in {"script", "style", "noscript", "svg"}:
            self._in_ignored = max(0, self._in_ignored - 1)

    def handle_data(self, data):
        if self._in_json_ld:
            self._json_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)
        if self._in_ignored == 0:
            cleaned = clean_text(data)
            if cleaned:
                self.text_parts.append(cleaned)


def public_http_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("link must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("link must not contain embedded credentials")
    allowed_port = 443 if parsed.scheme == "https" else 80
    if parsed.port not in {None, allowed_port}:
        raise ValueError("link must use the standard HTTP or HTTPS port")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or allowed_port,
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as error:
        raise ValueError(f"cannot resolve link host: {error}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("link host resolves to a non-public address")
    return parsed.geturl()


def validate_connected_peer(response):
    file_pointer = getattr(response, "fp", None)
    raw = getattr(file_pointer, "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is None:
        return
    address = sock.getpeername()[0]
    if not ipaddress.ip_address(address).is_global:
        raise ValueError("link connected to a non-public address")


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        safe_url = public_http_url(new_url)
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            safe_url,
        )


def flatten_json_ld(value):
    items = []
    if isinstance(value, list):
        for child in value:
            items.extend(flatten_json_ld(child))
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if graph:
            items.extend(flatten_json_ld(graph))
        type_value = value.get("@type")
        types = type_value if isinstance(type_value, list) else [type_value]
        if any(
            item in {
                "Place",
                "LocalBusiness",
                "Restaurant",
                "CafeOrCoffeeShop",
                "TouristAttraction",
                "Store",
                "LodgingBusiness",
            }
            for item in types
        ):
            items.append(value)
    return items


def fetch_public_link(url, timeout):
    safe_url = public_http_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": "WantToGoTripPlanner/0.3 (+public-evidence-only)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    opener = urllib.request.build_opener(PublicRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        public_http_url(response.geturl())
        validate_connected_peer(response)
        content_type = str(response.headers.get("content-type", "")).lower()
        if "html" not in content_type:
            raise ValueError("link did not return an HTML page")
        raw = response.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raise ValueError("link page exceeds 2 MB")
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), response.geturl()


def parse_html_evidence(document):
    parser = PageEvidenceParser()
    parser.feed(document)
    title = clean_text(
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or " ".join(parser.title_parts)
    )
    description = clean_text(
        parser.meta.get("og:description")
        or parser.meta.get("description")
        or parser.meta.get("twitter:description")
    )
    structured = []
    for raw in parser.json_ld:
        try:
            structured.extend(flatten_json_ld(json.loads(raw)))
        except (json.JSONDecodeError, TypeError):
            continue
    structured_names = [
        item.get("name") for item in structured if item.get("name")
    ]
    structured_addresses = []
    for item in structured:
        address = item.get("address")
        if isinstance(address, dict):
            address = " ".join(
                str(address.get(key) or "")
                for key in (
                    "addressCountry",
                    "addressRegion",
                    "addressLocality",
                    "streetAddress",
                )
            )
        if address:
            structured_addresses.append(address)
    body = "\n".join(unique(parser.text_parts[:250]))
    original = "\n".join(
        unique([title, description, *structured_names, *structured_addresses, body])
    )
    return {
        "title": title,
        "description": description,
        "structuredNames": unique(structured_names),
        "structuredAddresses": unique(structured_addresses),
        "originalText": original[:12000],
    }


def tesseract_ocr(image_path, languages):
    executable = shutil.which("tesseract")
    if not executable:
        return None
    requested = languages or "chi_sim+chi_tra+eng"
    command = [executable, image_path, "stdout", "-l", requested]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 and requested != "eng":
        result = subprocess.run(
            [executable, image_path, "stdout", "-l", "eng"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    if result.returncode != 0:
        raise ValueError(f"Tesseract OCR failed: {clean_text(result.stderr)[:300]}")
    return {"engine": "tesseract", "text": result.stdout}


def macos_vision_ocr(image_path):
    swift_executable = shutil.which("swift")
    if sys.platform != "darwin" or not swift_executable:
        return None
    script = os.path.join(os.path.dirname(__file__), "ocr_macos.swift")
    environment = dict(os.environ)
    module_cache = os.path.join(tempfile.gettempdir(), "want-to-go-swift-cache")
    os.makedirs(module_cache, exist_ok=True)
    environment["CLANG_MODULE_CACHE_PATH"] = module_cache
    environment["SWIFT_MODULECACHE_PATH"] = module_cache
    result = subprocess.run(
        [swift_executable, script, image_path],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise ValueError(f"macOS Vision OCR failed: {clean_text(result.stderr)[:300]}")
    return {"engine": "macos_vision", "text": result.stdout}


def screenshot_ocr(image_path, engine, languages):
    image_path = os.path.abspath(image_path)
    if not os.path.isfile(image_path):
        raise ValueError("screenshot file does not exist")
    if os.path.getsize(image_path) > MAX_IMAGE_BYTES:
        raise ValueError("screenshot exceeds 25 MB")
    attempts = []
    engines = (
        [engine]
        if engine != "auto"
        else ["macos_vision", "tesseract"]
    )
    for selected in engines:
        try:
            result = (
                macos_vision_ocr(image_path)
                if selected == "macos_vision"
                else tesseract_ocr(image_path, languages)
            )
            if result and clean_text(result["text"]):
                return result
            attempts.append(f"{selected}: unavailable or empty")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            attempts.append(f"{selected}: {error}")
    raise ValueError(
        "no OCR engine succeeded; install Tesseract or use a macOS host with Vision. "
        + "; ".join(attempts)
    )


def candidate_lines(text):
    lines = unique(re.split(r"[\r\n|•]+", text or ""))
    candidates = []
    for line in lines:
        if (
            2 <= len(line) <= 80
            and not NOISE_PATTERN.search(line)
            and not GENERIC_NAME_PATTERN.search(line)
            and not line.startswith(("http://", "https://"))
            and re.search(r"[A-Za-z\u3400-\u9fff\u0e00-\u0e7f]", line)
        ):
            candidates.append(line)
    return candidates


def first_matching(lines, pattern):
    return next((line for line in lines if pattern.search(line)), "")


def build_evidence(args, text, source_type, source_value, extra=None):
    extra = extra or {}
    lines = candidate_lines(text)
    supplied_name = clean_text(args.name)
    structured_names = extra.get("structuredNames") or []
    title = extra.get("title") or ""
    name_candidates = unique(
        [supplied_name, *structured_names, title, *lines[:8]]
    )
    name = name_candidates[0] if name_candidates else ""
    if not name:
        raise ValueError("no place name could be extracted; provide --name")
    mall = first_matching(lines, MALL_PATTERN)
    branch = first_matching(lines, BRANCH_PATTERN)
    address = (
        (extra.get("structuredAddresses") or [""])[0]
        or first_matching(lines, ADDRESS_PATTERN)
    )
    floor = first_matching(lines, FLOOR_PATTERN)
    times = unique(TIME_PATTERN.findall(text or ""))[:20]
    result = {
        "schemaVersion": "1.0",
        "extractedAt": now(),
        "sourceType": source_type,
        "sourceRefs": [{"type": source_type, "value": source_value}],
        "name": name,
        "nameSource": (
            "user"
            if supplied_name
            else "structured_data"
            if structured_names
            else "page_title"
            if title
            else "ocr_or_text_heuristic"
        ),
        "nameRequiresConfirmation": not bool(supplied_name),
        "aliases": [item for item in name_candidates[1:8] if item != name],
        "city": clean_text(args.city),
        "destination": clean_text(args.destination),
        "branch": branch,
        "mall": mall,
        "floor": floor,
        "addressHint": address,
        "openingHoursText": times,
        "signals": {
            "reservationMentioned": bool(RESERVATION_PATTERN.search(text or "")),
            "queueMentioned": bool(QUEUE_PATTERN.search(text or "")),
            "closureMentioned": bool(CLOSURE_PATTERN.search(text or "")),
            "weatherSensitive": bool(WEATHER_PATTERN.search(text or "")),
        },
        "extractionWarnings": (
            []
            if supplied_name
            else ["店名由页面或 OCR 自动推断，必须在地图候选阶段由用户确认"]
        ),
    }
    if source_type == "screenshot":
        result["ocrText"] = (text or "")[:12000]
        result["ocrEngine"] = extra.get("ocrEngine")
    else:
        result["originalText"] = (text or "")[:12000]
        if extra.get("finalUrl"):
            result["finalUrl"] = extra["finalUrl"]
    return result


def write_result(value, output):
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        parent = os.path.dirname(os.path.abspath(output))
        os.makedirs(parent, exist_ok=True)
        file_descriptor, temporary = tempfile.mkstemp(
            prefix=".want-to-go-evidence-",
            dir=parent,
            text=True,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(serialized)
            os.chmod(temporary, 0o600)
            os.replace(temporary, output)
        except Exception:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise
    else:
        print(serialized, end="")


def extract(args):
    if args.screenshot:
        ocr = screenshot_ocr(args.screenshot, args.ocr_engine, args.languages)
        result = build_evidence(
            args,
            ocr["text"],
            "screenshot",
            os.path.basename(args.screenshot),
            {"ocrEngine": ocr["engine"]},
        )
    elif args.link:
        document, final_url = fetch_public_link(args.link, args.timeout)
        parsed = parse_html_evidence(document)
        result = build_evidence(
            args,
            parsed["originalText"],
            "link",
            args.link,
            {**parsed, "finalUrl": final_url},
        )
    elif args.html_file:
        if not os.path.isfile(args.html_file):
            raise ValueError("HTML file does not exist")
        with open(args.html_file, "rb") as handle:
            raw_document = handle.read(MAX_HTML_BYTES + 1)
        if len(raw_document) > MAX_HTML_BYTES:
            raise ValueError("HTML file exceeds 2 MB")
        parsed = parse_html_evidence(raw_document.decode("utf-8", errors="replace"))
        result = build_evidence(
            args,
            parsed["originalText"],
            "saved_html",
            os.path.basename(args.html_file),
            parsed,
        )
    else:
        result = build_evidence(args, args.text, "text", "user-provided-text")
    write_result(result, args.output)


def doctor(_args):
    result = {
        "macosVision": sys.platform == "darwin" and bool(shutil.which("swift")),
        "tesseract": bool(shutil.which("tesseract")),
        "dedicatedOcrAvailable": (
            sys.platform == "darwin" and bool(shutil.which("swift"))
        )
        or bool(shutil.which("tesseract")),
        "publicLinkExtraction": True,
        "privacy": {
            "uploadsOriginalScreenshot": False,
            "usesBrowserCookies": False,
            "acceptsPrivateCollectionCredentials": False,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def parser():
    root = argparse.ArgumentParser(
        description="Extract local place evidence from a screenshot, public link, or text"
    )
    sub = root.add_subparsers(dest="command", required=True)
    p_extract = sub.add_parser("extract")
    source = p_extract.add_mutually_exclusive_group(required=True)
    source.add_argument("--screenshot")
    source.add_argument("--link")
    source.add_argument("--html-file")
    source.add_argument("--text")
    p_extract.add_argument("--name", default="")
    p_extract.add_argument("--city", default="")
    p_extract.add_argument("--destination", default="")
    p_extract.add_argument(
        "--ocr-engine",
        choices=["auto", "macos_vision", "tesseract"],
        default="auto",
    )
    p_extract.add_argument("--languages", default="")
    p_extract.add_argument("--timeout", type=int, default=15)
    p_extract.add_argument("--output")
    p_extract.set_defaults(func=extract)
    p_doctor = sub.add_parser("doctor")
    p_doctor.set_defaults(func=doctor)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    try:
        arguments.func(arguments)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit(str(error))
