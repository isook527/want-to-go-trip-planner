#!/usr/bin/env python3
"""Extract local place evidence from public links, screenshots, or text."""

import argparse
import hashlib
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
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
import urllib.request


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 250 * 1024 * 1024
MAX_VIDEO_SCENES = 24
EVIDENCE_SCHEMA_VERSION = "1.2.3"
MIN_PYTHON_VERSION = (3, 9)
MIN_NODE_MAJOR = 18
PLATFORM_MEDIA_HOSTS = {
    "xiaohongshu.com",
    "xhslink.com",
    "xhslink.cn",
    "douyin.com",
    "iesdouyin.com",
    "tiktok.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
}
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
LATIN_PLACE_PATTERN = re.compile(
    r"\b[A-ZÀ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9&'’.-]*"
    r"(?:\s+(?:(?:[A-ZÀ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9&'’.-]*)|(?:of|the|and|&)|(?:\d+))){1,5}\b"
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


def host_matches(hostname, domains):
    hostname = clean_text(hostname).casefold()
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in domains
    )


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
    video_urls = unique(
        [
            parser.meta.get("og:video"),
            parser.meta.get("og:video:url"),
            parser.meta.get("og:video:secure_url"),
            parser.meta.get("twitter:player:stream"),
        ]
    )
    og_type = clean_text(parser.meta.get("og:type"))
    return {
        "title": title,
        "description": description,
        "structuredNames": unique(structured_names),
        "structuredAddresses": unique(structured_addresses),
        "originalText": original[:12000],
        "mediaType": (
            "video"
            if video_urls or "video" in og_type.casefold()
            else "page"
        ),
        "publicVideoUrls": video_urls,
        "thumbnailUrl": clean_text(
            parser.meta.get("og:image")
            or parser.meta.get("twitter:image")
        ),
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


def inferred_plain_text_name(lines):
    for line in lines:
        match = LATIN_PLACE_PATTERN.search(line)
        if match:
            return clean_text(match.group(0))
    return ""


def build_evidence(args, text, source_type, source_value, extra=None):
    extra = extra or {}
    lines = candidate_lines(text)
    supplied_name = clean_text(args.name)
    structured_names = extra.get("structuredNames") or []
    title = extra.get("title") or ""
    plain_text_name = inferred_plain_text_name(lines) if source_type == "text" else ""
    name_candidates = unique(
        [supplied_name, *structured_names, title, plain_text_name, *lines[:8]]
    )
    media_type = extra.get("mediaType") or (
        "video" if source_type == "video" else "image"
        if source_type == "screenshot"
        else "text"
    )
    source_title = title or (name_candidates[0] if name_candidates else "")
    if media_type == "video" and not supplied_name and not structured_names:
        name = ""
    else:
        name = name_candidates[0] if name_candidates else ""
    if not name and media_type != "video":
        raise ValueError(
            "未能从内容中识别地点名称，请填写 source.name 或使用 --name"
            if args.output_locale != "en-US" else
            "No place name could be extracted; add source.name or use --name"
        )
    mall = first_matching(lines, MALL_PATTERN)
    branch = first_matching(lines, BRANCH_PATTERN)
    address = (
        (extra.get("structuredAddresses") or [""])[0]
        or first_matching(lines, ADDRESS_PATTERN)
    )
    floor = first_matching(lines, FLOOR_PATTERN)
    times = unique(TIME_PATTERN.findall(text or ""))[:20]
    result = {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "extractedAt": now(),
        "sourceLanguage": args.source_language,
        "outputLocale": args.output_locale,
        "sourceType": source_type,
        "sourceRefs": [{"type": source_type, "value": source_value}],
        "mediaType": media_type,
        "sourceTitle": source_title,
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
        "countryCode": clean_text(args.country_code).upper(),
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
        "extractionWarnings": [],
    }
    if source_type == "link":
        result["userOriginalUrl"] = source_value
    if not supplied_name:
        if args.output_locale == "en-US":
            warning = (
                "The video may contain multiple places. Confirm each place "
                "from timestamped evidence before creating a Place Passport."
                if media_type == "video"
                else "The place name was inferred from the page or OCR and "
                "must be confirmed against map candidates."
            )
        else:
            warning = (
                "视频可能包含多个地点，必须按带时间戳的证据逐个确认后才能生成地点护照"
                if media_type == "video"
                else "店名由页面或 OCR 自动推断，必须在地图候选阶段由用户确认"
            )
        result["extractionWarnings"].append(warning)
    if source_type == "screenshot":
        result["ocrText"] = (text or "")[:12000]
        result["ocrEngine"] = extra.get("ocrEngine")
    else:
        result["originalText"] = (text or "")[:12000]
        if extra.get("finalUrl"):
            result["finalUrl"] = extra["finalUrl"]
            result["sourceRefs"] = [
                {"type": "original_url", "value": source_value},
                {
                    "type": "canonical_url",
                    "value": extra["finalUrl"],
                },
            ]
        platform_media = bool(extra.get("platformMedia"))
        if (
            media_type == "video"
            or extra.get("publicVideoUrls")
            or platform_media
        ):
            result["videoEvidence"] = {
                "binaryStatus": (
                    "public_stream_detected"
                    if extra.get("publicVideoUrls")
                    else "not_provided_by_platform"
                ),
                "publicVideoUrlsDetected": len(
                    extra.get("publicVideoUrls") or []
                ),
                "sceneAnalysis": "not_run",
                "nextAction": (
                    "Upload the original video or timestamped video screenshots."
                    if args.output_locale == "en-US"
                    else "请上传原视频，或按时间顺序上传视频截图。"
                ),
            }
        if extra.get("thumbnailUrl"):
            result["thumbnailEvidence"] = {
                "sourceUrl": extra["thumbnailUrl"],
                "copiedIntoPassport": False,
            }
    return result


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe_video(path):
    executable = shutil.which("ffprobe")
    if not executable:
        raise ValueError(
            "ffprobe is unavailable; upload timestamped video screenshots instead"
        )
    result = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed: {clean_text(result.stderr)[:300]}")
    payload = json.loads(result.stdout)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    streams = payload.get("streams") or []
    return {
        "durationSeconds": round(duration, 3),
        "hasVideo": any(item.get("codec_type") == "video" for item in streams),
        "hasAudio": any(item.get("codec_type") == "audio" for item in streams),
        "streams": streams,
    }


def extract_video_scenes(path, target_folder, threshold=0.28):
    executable = shutil.which("ffmpeg")
    if not executable:
        raise ValueError(
            "ffmpeg is unavailable; upload timestamped video screenshots instead"
        )
    os.makedirs(target_folder, exist_ok=True)
    output_pattern = os.path.join(target_folder, "scene-%03d.jpg")
    filter_value = (
        f"select='eq(n,0)+gt(scene,{threshold})',"
        "showinfo,scale='min(1280,iw)':-2"
    )
    result = subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            path,
            "-vf",
            filter_value,
            "-fps_mode",
            "vfr",
            "-frames:v",
            str(MAX_VIDEO_SCENES),
            "-q:v",
            "3",
            output_pattern,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"ffmpeg scene extraction failed: {clean_text(result.stderr)[:300]}")
    timestamps = [
        float(value)
        for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", result.stderr)
    ]
    frames = sorted(
        os.path.join(target_folder, name)
        for name in os.listdir(target_folder)
        if re.fullmatch(r"scene-\d{3}\.jpg", name)
    )
    return [
        {
            "index": index,
            "timestampSeconds": round(
                timestamps[index - 1] if index - 1 < len(timestamps) else 0.0,
                3,
            ),
            "framePath": frame,
        }
        for index, frame in enumerate(frames, start=1)
    ]


def load_transcript(path):
    if not path:
        return []
    if not os.path.isfile(path):
        raise ValueError("transcript file does not exist")
    if os.path.getsize(path) > MAX_HTML_BYTES:
        raise ValueError("transcript file exceeds 2 MB")
    raw = open(path, "r", encoding="utf-8").read()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [{"startSeconds": None, "endSeconds": None, "text": clean_text(raw)}]
    if not isinstance(parsed, list):
        raise ValueError("transcript JSON must be an array")
    result = []
    for item in parsed:
        if not isinstance(item, dict) or not clean_text(item.get("text")):
            continue
        result.append(
            {
                "startSeconds": item.get("startSeconds", item.get("start")),
                "endSeconds": item.get("endSeconds", item.get("end")),
                "text": clean_text(item.get("text")),
            }
        )
    return result


def analyze_video(path, args, transcript_file=""):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError("video file does not exist")
    if os.path.getsize(path) > MAX_VIDEO_BYTES:
        raise ValueError("video exceeds 250 MB")
    metadata = ffprobe_video(path)
    if not metadata["hasVideo"]:
        raise ValueError("file does not contain a video stream")
    transcript = load_transcript(transcript_file)
    with tempfile.TemporaryDirectory(prefix="want-to-go-video-") as folder:
        scenes = extract_video_scenes(path, folder)
        evidence_scenes = []
        for scene in scenes:
            ocr_text = ""
            ocr_engine = ""
            ocr_error = ""
            try:
                ocr = screenshot_ocr(
                    scene["framePath"],
                    args.ocr_engine,
                    args.languages,
                )
                ocr_text = clean_text(ocr["text"])
                ocr_engine = ocr["engine"]
            except ValueError as error:
                ocr_error = str(error)
            evidence_scenes.append(
                {
                    "index": scene["index"],
                    "timestampSeconds": scene["timestampSeconds"],
                    "frameSha256": file_sha256(scene["framePath"]),
                    "ocrText": ocr_text,
                    "ocrEngine": ocr_engine,
                    "ocrError": ocr_error,
                }
            )
    combined_text = "\n".join(
        unique(
            [
                *(item.get("ocrText") for item in evidence_scenes),
                *(item.get("text") for item in transcript),
            ]
        )
    )
    return {
        "metadata": metadata,
        "videoSha256": file_sha256(path),
        "scenes": evidence_scenes,
        "transcript": transcript,
        "asrStatus": (
            "provided"
            if transcript
            else "requires_host_asr"
            if metadata["hasAudio"]
            else "not_applicable"
        ),
        "combinedText": combined_text,
    }


def validate_common_args(args):
    if args.country_code and not re.fullmatch(
        r"[A-Za-z]{2}", args.country_code.strip()
    ):
        raise ValueError("--country-code must be an ISO 3166-1 alpha-2 code")
    if args.source_language != "auto" and not re.fullmatch(
        r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*",
        args.source_language,
    ):
        raise ValueError("--source-language must be auto or a valid language tag")


def extract_one(args, source):
    source_type = clean_text(source.get("type")).casefold()
    value = source.get("value") or source.get("path") or ""
    if source_type == "screenshot":
        if not os.path.isfile(value):
            raise ValueError("截图文件不存在" if args.output_locale != "en-US" else "Screenshot file does not exist")
        if os.path.getsize(value) > MAX_IMAGE_BYTES:
            raise ValueError("截图超过 25 MB" if args.output_locale != "en-US" else "Screenshot exceeds 25 MB")
        try:
            ocr = screenshot_ocr(value, args.ocr_engine, args.languages)
            result = build_evidence(
                args,
                ocr["text"],
                "screenshot",
                os.path.basename(value),
                {"ocrEngine": ocr["engine"]},
            )
            result["ocrStatus"] = "completed"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            supplied_name = clean_text(args.name)
            result = {
                "schemaVersion": EVIDENCE_SCHEMA_VERSION,
                "extractedAt": now(),
                "sourceLanguage": args.source_language,
                "outputLocale": args.output_locale,
                "sourceType": "screenshot",
                "sourceRefs": [{"type": "screenshot", "value": os.path.basename(value)}],
                "mediaType": "image",
                "sourceTitle": supplied_name or os.path.basename(value),
                "name": supplied_name,
                "nameSource": "user" if supplied_name else "unresolved",
                "nameRequiresConfirmation": not bool(supplied_name),
                "aliases": [],
                "city": clean_text(args.city),
                "countryCode": clean_text(args.country_code).upper(),
                "destination": clean_text(args.destination),
                "branch": "",
                "mall": "",
                "floor": "",
                "addressHint": "",
                "openingHoursText": [],
                "signals": {
                    "reservationMentioned": False,
                    "queueMentioned": False,
                    "closureMentioned": False,
                    "weatherSensitive": False,
                },
                "ocrStatus": "unavailable",
                "extractionWarnings": [
                    "OCR unavailable; the original image was preserved. Confirm the place name before creating a passport."
                    if args.output_locale == "en-US"
                    else "OCR 暂不可用，原图已保留；生成护照前需确认地点名称。"
                ],
                "ocrFailure": clean_text(error),
            }
        result["localPath"] = os.path.abspath(value)
        result["displayPhoto"] = {"path": result["localPath"]}
    elif source_type == "video":
        analysis = analyze_video(
            value,
            args,
            source.get("transcriptFile") or "",
        )
        result = build_evidence(
            args,
            analysis["combinedText"],
            "video",
            os.path.basename(value),
            {"mediaType": "video"},
        )
        result["videoEvidence"] = {
            "binaryStatus": "local_original_analyzed",
            "sceneAnalysis": "completed",
            **analysis,
        }
    elif source_type == "link":
        document, final_url = fetch_public_link(value, args.timeout)
        parsed = parse_html_evidence(document)
        parsed["platformMedia"] = host_matches(
            urlparse(final_url).hostname,
            PLATFORM_MEDIA_HOSTS,
        )
        result = build_evidence(
            args,
            parsed["originalText"],
            "link",
            value,
            {**parsed, "finalUrl": final_url},
        )
    elif source_type in {"html", "saved_html"}:
        if not os.path.isfile(value):
            raise ValueError("HTML file does not exist")
        with open(value, "rb") as handle:
            raw_document = handle.read(MAX_HTML_BYTES + 1)
        if len(raw_document) > MAX_HTML_BYTES:
            raise ValueError("HTML file exceeds 2 MB")
        parsed = parse_html_evidence(
            raw_document.decode("utf-8", errors="replace")
        )
        result = build_evidence(
            args,
            parsed["originalText"],
            "saved_html",
            os.path.basename(value),
            parsed,
        )
    elif source_type == "text":
        result = build_evidence(args, value, "text", "user-provided-text")
    else:
        raise ValueError(
            "source type must be screenshot, video, link, html, or text"
        )
    result["sourceId"] = clean_text(source.get("id")) or str(uuid.uuid4())
    result["collectionGroup"] = clean_text(source.get("group")) or "default"
    return result


def safe_asset_token(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", clean_text(value)).strip("-.")
    return cleaned[:80] or uuid.uuid4().hex[:12]


def preserve_screenshot_asset(evidence, source, output_path, bundle_id, storage_mode):
    original = os.path.abspath(source.get("path") or source.get("value") or "")
    if evidence.get("sourceType") != "screenshot" or not os.path.isfile(original):
        return evidence
    target_path = original
    asset_root = ""
    if output_path:
        output = os.path.abspath(output_path)
        asset_root = os.path.join(
            os.path.dirname(output),
            f"{os.path.splitext(os.path.basename(output))[0]}-assets",
            safe_asset_token(bundle_id),
        )
        os.makedirs(asset_root, exist_ok=True)
    if storage_mode == "durable" and asset_root:
        extension = os.path.splitext(original)[1].lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
            extension = ".png"
        target_path = os.path.join(
            asset_root,
            f"{safe_asset_token(source.get('id') or evidence.get('sourceId'))}{extension}",
        )
        if os.path.realpath(original) != os.path.realpath(target_path):
            shutil.copy2(original, target_path)
    evidence["localPath"] = target_path
    evidence["assetStorage"] = "durable_copy" if target_path != original else "source_path"
    display_stem = safe_asset_token(source.get("id") or evidence.get("sourceId"))
    display_path = (
        os.path.join(asset_root, f"{display_stem}.display.jpg")
        if asset_root
        else os.path.splitext(target_path)[0] + ".display.jpg"
    )
    crop_script = os.path.join(os.path.dirname(__file__), "prepare_display_image.py")
    try:
        prepared = subprocess.run(
            [
                sys.executable,
                crop_script,
                target_path,
                display_path,
                "--target-aspect",
                "4:3",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        metadata = json.loads(prepared.stdout)
        score = float(metadata.get("photoScore", 0.0))
        eligible = bool(metadata.get("photoRich"))
        evidence["displayPhotoScore"] = round(score, 4)
        evidence["displayPhotoEligible"] = eligible
        evidence["displayCrop"] = {
            "strategy": clean_text(metadata.get("strategy")),
            "targetAspect": metadata.get("targetAspect"),
            "cropBox": metadata.get("cropBox"),
        }
        if eligible:
            evidence["displayPhoto"] = {
                "path": display_path,
                "qualityScore": round(score, 4),
            }
            evidence["displayPhotoStatus"] = "prepared"
        else:
            evidence.pop("displayPhoto", None)
            evidence["displayPhotoStatus"] = "text_dominant"
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        evidence.pop("displayPhoto", None)
        evidence["displayPhotoEligible"] = False
        evidence["displayPhotoStatus"] = "preparation_unavailable"
        evidence["displayPhotoWarning"] = clean_text(error)
    return evidence


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
    validate_common_args(args)
    if args.screenshot:
        source = {"type": "screenshot", "path": args.screenshot}
    elif args.video:
        source = {
            "type": "video",
            "path": args.video,
            "transcriptFile": args.transcript_file,
        }
    elif args.link:
        source = {"type": "link", "value": args.link}
    elif args.html_file:
        source = {"type": "html", "path": args.html_file}
    else:
        source = {"type": "text", "value": args.text}
    result = extract_one(args, source)
    write_result(result, args.output)


def batch_args(root_args, manifest, source):
    values = vars(root_args).copy()
    for key, default in (
        ("name", ""),
        ("city", ""),
        ("country_code", ""),
        ("destination", ""),
        ("source_language", "auto"),
        ("output_locale", "zh-CN"),
        ("ocr_engine", "auto"),
        ("languages", ""),
        ("timeout", 15),
    ):
        manifest_key = "".join(
            [key.split("_")[0]]
            + [part.title() for part in key.split("_")[1:]]
        )
        values[key] = source.get(
            manifest_key,
            manifest.get(manifest_key, values.get(key, default)),
        )
    return argparse.Namespace(**values)


def source_public_ref(source):
    value = source.get("value") or source.get("path") or ""
    return {
        "sourceId": source.get("id"),
        "type": source.get("type"),
        "value": (
            value
            if source.get("type") == "link"
            else os.path.basename(value)
        ),
        "group": source.get("group") or "default",
        "destination": clean_text(source.get("destination")),
    }


def batch_extract(args):
    if not os.path.isfile(args.manifest):
        raise ValueError(
            "manifest 文件不存在"
            if args.output_locale != "en-US" else
            "Manifest file does not exist"
        )
    with open(args.manifest, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("sources"), list
    ):
        raise ValueError(
            "manifest 必须包含 sources 数组"
            if args.output_locale != "en-US" else
            "Manifest must contain a sources array"
        )
    storage_mode = manifest.get("storageMode", "durable")
    if storage_mode not in {"durable", "session_only"}:
        raise ValueError("storageMode must be durable or session_only")
    bundle_id = clean_text(manifest.get("bundleId")) or str(uuid.uuid4())
    sources = []
    for item in manifest["sources"]:
        if not isinstance(item, dict):
            raise ValueError("every source must be an object")
        normalized = item.copy()
        normalized["id"] = clean_text(item.get("id")) or str(uuid.uuid4())
        normalized["group"] = clean_text(item.get("group")) or "default"
        sources.append(normalized)
    received_by_type = {}
    for source in sources:
        source_type = clean_text(source.get("type")).casefold() or "unknown"
        received_by_type[source_type] = received_by_type.get(source_type, 0) + 1
    evidence = []
    failures = []
    for source in sources:
        current_args = batch_args(args, manifest, source)
        try:
            validate_common_args(current_args)
            item = extract_one(current_args, source)
            evidence.append(
                preserve_screenshot_asset(
                    item,
                    source,
                    args.output,
                    bundle_id,
                    storage_mode,
                )
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as error:
            failures.append(
                {
                    **source_public_ref(source),
                    "status": "failed",
                    "reason": clean_text(error),
                    "mustRemainVisible": True,
                }
            )
    locale = manifest.get("outputLocale", args.output_locale)
    if storage_mode == "session_only":
        storage_notice = (
            "Only stored for this conversation. Export the bundle before leaving."
            if locale == "en-US"
            else "仅在当前会话暂存，离开前请先导出收纳包。"
        )
    else:
        storage_notice = (
            "Saved to the durable local Want-to-go Library."
            if locale == "en-US"
            else "已保存到可持续使用的本地想去库。"
        )
    result = {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "bundleId": bundle_id,
        "mode": "library_capture",
        "createdAt": now(),
        "storageMode": storage_mode,
        "storageNotice": storage_notice,
        "destination": clean_text(manifest.get("destination")),
        "inputManifest": {
            "receivedTotal": len(sources),
            "receivedByType": received_by_type,
            "processedTotal": len(evidence),
            "failedTotal": len(failures),
            "allSourcesAccountedFor": (
                len(evidence) + len(failures) == len(sources)
            ),
        },
        "sameMessageGrouping": (
            "Sources with the same group belong to one collection bundle and "
            "must not be counted as separate places."
        ),
        "evidence": evidence,
        "failures": failures,
        "reportGenerated": False,
        "passportGenerated": False,
        "nextAction": (
            "Keep collecting. Generate a passport only when the user explicitly asks."
            if locale == "en-US"
            else "继续收纳即可；只有用户明确提出时才生成想去护照。"
        ),
    }
    write_result(result, args.output)


def command_probe(name, version_arguments=("--version",)):
    executable = shutil.which(name)
    if not executable:
        return {"installed": False, "version": "", "ready": False}
    try:
        result = subprocess.run(
            [executable, *version_arguments],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"installed": True, "version": "", "ready": False}
    output = clean_text(result.stdout or result.stderr)
    return {
        "installed": True,
        "version": output.splitlines()[0][:120] if output else "",
        "ready": result.returncode == 0,
    }


def pillow_probe():
    try:
        import PIL
    except ImportError:
        return {"installed": False, "version": "", "ready": False}
    return {
        "installed": True,
        "version": clean_text(getattr(PIL, "__version__", "unknown")),
        "ready": True,
    }


def tesseract_probe():
    base = command_probe("tesseract")
    languages = []
    if base["installed"]:
        executable = shutil.which("tesseract")
        try:
            result = subprocess.run(
                [executable, "--list-langs"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.returncode == 0:
                languages = sorted(
                    line.strip()
                    for line in result.stdout.splitlines()
                    if re.fullmatch(r"[A-Za-z0-9_\-]+", line.strip())
                )
        except (OSError, subprocess.SubprocessError):
            languages = []
    chinese_ready = "chi_sim" in languages or "chi_tra" in languages
    base.update(
        {
            "languages": languages,
            "englishReady": "eng" in languages,
            "chineseReady": chinese_ready,
            "ready": bool(base["ready"] and "eng" in languages and chinese_ready),
        }
    )
    return base


def node_probe():
    result = command_probe("node")
    match = re.search(r"(?:^|\s)v?(\d+)", result.get("version", ""))
    major = int(match.group(1)) if match else 0
    result.update(
        {
            "major": major,
            "minimumMajor": MIN_NODE_MAJOR,
            "ready": bool(result["ready"] and major >= MIN_NODE_MAJOR),
        }
    )
    return result


def doctor_report(locale="zh-CN"):
    is_english = locale == "en-US"
    is_windows = sys.platform.startswith("win")
    is_macos = sys.platform == "darwin"
    platform_name = "Windows" if is_windows else "macOS" if is_macos else sys.platform
    platform_supported = is_windows or is_macos
    python_version = tuple(sys.version_info[:3])
    python_ready = python_version >= MIN_PYTHON_VERSION
    python = {
        "installed": True,
        "version": ".".join(str(value) for value in python_version),
        "minimum": ".".join(str(value) for value in MIN_PYTHON_VERSION),
        "ready": python_ready,
    }
    pillow = pillow_probe()
    node = node_probe()
    tesseract = tesseract_probe()
    swift = command_probe("swift") if is_macos else {
        "installed": False,
        "version": "",
        "ready": False,
    }
    macos_vision_ready = bool(is_macos and swift["ready"])
    ocr_provider = (
        "macos_vision"
        if macos_vision_ready
        else "tesseract"
        if tesseract["ready"]
        else "none"
    )
    ocr_ready = ocr_provider != "none"
    ffmpeg = command_probe("ffmpeg", ("-version",))
    ffprobe = command_probe("ffprobe", ("-version",))
    video_ready = bool(ffmpeg["ready"] and ffprobe["ready"])
    core_ready = bool(platform_supported and python_ready and pillow["ready"] and node["ready"])
    full_ready = bool(core_ready and ocr_ready)
    status = "ready" if full_ready else "limited" if core_ready else "blocked"

    blocking_issues = []
    feature_warnings = []
    install_commands = []
    if not platform_supported:
        blocking_issues.append(
            "当前正式支持 macOS 和 Windows。"
            if not is_english else
            "The supported desktop platforms are macOS and Windows."
        )
    if not python_ready:
        blocking_issues.append(
            "需要 Python 3.9 或更高版本。"
            if not is_english else
            "Python 3.9 or newer is required."
        )
    if not pillow["ready"]:
        blocking_issues.append(
            "缺少 Pillow，无法生成地点展示图。"
            if not is_english else
            "Pillow is missing, so display images cannot be prepared."
        )
        install_commands.append(
            "py -m pip install Pillow" if is_windows else "python3 -m pip install Pillow"
        )
    if not node["ready"]:
        blocking_issues.append(
            "需要 Node.js 18 或更高版本来生成想去护照 HTML。"
            if not is_english else
            "Node.js 18 or newer is required to render passport HTML."
        )
        install_commands.append(
            "winget install --id OpenJS.NodeJS.LTS -e"
            if is_windows else
            "brew install node"
        )
    if not ocr_ready:
        feature_warnings.append(
            "截图仍会保存，但缺少可用 OCR；Windows 请安装 Tesseract、eng 及 chi_sim 或 chi_tra 语言包。"
            if not is_english else
            "Screenshots will still be saved, but OCR is unavailable; on Windows install Tesseract with eng and chi_sim or chi_tra."
        )
        if is_windows:
            install_commands.append("winget install --id UB-Mannheim.TesseractOCR -e")
    if not video_ready:
        feature_warnings.append(
            "缺少 FFmpeg/FFprobe，视频需改用关键截图或文字。"
            if not is_english else
            "FFmpeg/FFprobe is unavailable; use key screenshots or text for videos."
        )
        install_commands.append(
            "winget install --id Gyan.FFmpeg -e"
            if is_windows else
            "brew install ffmpeg"
        )

    return {
        "schemaVersion": "kornvia-install-doctor-1.2.3",
        "platform": {
            "name": platform_name,
            "supported": platform_supported,
        },
        "status": status,
        "coreReady": core_ready,
        "fullReady": full_ready,
        "features": {
            "libraryCapture": bool(platform_supported and python_ready),
            "displayImagePreparation": bool(python_ready and pillow["ready"]),
            "htmlPassport": bool(python_ready and node["ready"]),
            "screenshotOcr": ocr_ready,
            "videoBinaryAnalysis": video_ready,
            "publicLinkExtraction": True,
        },
        "dependencies": {
            "python": python,
            "pillow": pillow,
            "node": node,
            "macosVision": {
                "available": macos_vision_ready,
                "swift": swift,
            },
            "tesseract": tesseract,
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
        },
        "ocrProvider": ocr_provider,
        "blockingIssues": blocking_issues,
        "featureWarnings": feature_warnings,
        "installCommands": unique(install_commands),
        "videoSceneLimit": MAX_VIDEO_SCENES,
        "privacy": {
            "uploadsOriginalScreenshot": False,
            "usesBrowserCookies": False,
            "acceptsPrivateCollectionCredentials": False,
        },
    }


def doctor(args):
    print(json.dumps(doctor_report(args.output_locale), ensure_ascii=False, indent=2))


def add_common_arguments(target):
    target.add_argument("--name", default="")
    target.add_argument("--city", default="")
    target.add_argument("--country-code", default="")
    target.add_argument("--destination", default="")
    target.add_argument("--source-language", default="auto")
    target.add_argument(
        "--output-locale",
        choices=["zh-CN", "en-US"],
        default="zh-CN",
    )
    target.add_argument(
        "--ocr-engine",
        choices=["auto", "macos_vision", "tesseract"],
        default="auto",
    )
    target.add_argument("--languages", default="")
    target.add_argument("--timeout", type=int, default=15)
    target.add_argument("--output")


def parser():
    root = argparse.ArgumentParser(
        description="Extract local place evidence from a screenshot, public link, or text"
    )
    sub = root.add_subparsers(dest="command", required=True)
    p_extract = sub.add_parser("extract")
    source = p_extract.add_mutually_exclusive_group(required=True)
    source.add_argument("--screenshot")
    source.add_argument("--video")
    source.add_argument("--link")
    source.add_argument("--html-file")
    source.add_argument("--text")
    p_extract.add_argument("--transcript-file", default="")
    add_common_arguments(p_extract)
    p_extract.set_defaults(func=extract)
    p_batch = sub.add_parser("batch")
    p_batch.add_argument("--manifest", required=True)
    add_common_arguments(p_batch)
    p_batch.set_defaults(func=batch_extract)
    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument(
        "--output-locale",
        "--locale",
        dest="output_locale",
        choices=["zh-CN", "en-US"],
        default="zh-CN",
    )
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
