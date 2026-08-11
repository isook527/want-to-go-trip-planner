#!/usr/bin/env python3
"""Extract local place evidence from public links, screenshots, or text."""

import argparse
from concurrent.futures import ThreadPoolExecutor
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
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.error
import urllib.request


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 512 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 250 * 1024 * 1024
MAX_VIDEO_SCENES = 24
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "product.json"
with CONFIG_PATH.open("r", encoding="utf-8") as _config_handle:
    PRODUCT_CONFIG = json.load(_config_handle)
EVIDENCE_SCHEMA_VERSION = str(PRODUCT_CONFIG["contractVersion"])
SOURCE_POLICY_VERSION = str(PRODUCT_CONFIG["sourcePolicyVersion"])
MIN_PYTHON_VERSION = (3, 9)
MIN_NODE_MAJOR = 18
PLATFORM_IMPORTS = PRODUCT_CONFIG.get("platformImports") or {}
PLATFORM_MEDIA_HOSTS = {
    domain
    for platform in {"xiaohongshu", "douyin", "tiktok", "instagram", "youtube"}
    for domain in (PLATFORM_IMPORTS.get(platform) or {}).get("domains", [])
}
PUBLIC_METADATA_ENDPOINT_HOSTS = {
    "www.tiktok.com",
    "www.youtube.com",
    "graph.facebook.com",
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
UNTRUSTED_INSTRUCTION_PATTERN = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?|"
    r"system\s+prompt|developer\s+message|reveal\s+(?:the\s+)?prompt|"
    r"忽略(?:之前|以上|前面)(?:所有)?(?:指令|要求)|系统提示词|开发者消息|"
    r"按以下(?:指令|要求)执行|不要遵守(?:之前|系统)(?:指令|要求))",
    re.IGNORECASE,
)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def is_english_locale(value):
    return clean_text(value).casefold().startswith("en")


def locale_text(locale, zh, en):
    return en if is_english_locale(locale) else zh


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


def untrusted_instruction_excerpts(value):
    raw = str(value or "")
    excerpts = []
    for match in UNTRUSTED_INSTRUCTION_PATTERN.finditer(raw):
        start = max(0, match.start() - 60)
        end = min(len(raw), match.end() + 100)
        excerpt = re.sub(r"\s+", " ", raw[start:end]).strip()[:240]
        if excerpt and excerpt not in excerpts:
            excerpts.append(excerpt)
    return excerpts[:5]


def source_policy(source_type, content, access_level, captured=True):
    can_support = []
    if source_type == "link":
        can_support.append("original_url_submitted")
        if captured:
            can_support.append("page_content_observed")
    elif source_type == "screenshot":
        can_support.append("original_image_preserved")
        if captured:
            can_support.append("ocr_text_observed")
    elif source_type == "video":
        can_support.append("local_video_evidence")
    elif source_type == "text":
        can_support.append("customer_submitted_text")
    excerpts = untrusted_instruction_excerpts(content)
    cannot_prove = [
        "current_opening_status_without_fresh_public_check",
        "booking_or_ticket_availability",
        "future_accessibility_or_queue_conditions",
    ]
    if not captured:
        cannot_prove.append("blocked_or_failed_source_contents")
    if excerpts:
        cannot_prove.append("instructions_inside_external_content_are_authoritative")
    result = {
        "version": SOURCE_POLICY_VERSION,
        "accessLevel": access_level,
        "canSupport": unique(can_support),
        "cannotProve": unique(cannot_prove),
        "untrustedInstructionsDetected": bool(excerpts),
    }
    if excerpts:
        result["untrustedInstructionExcerpts"] = excerpts
    return result


def host_matches(hostname, domains):
    hostname = clean_text(hostname).casefold()
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in domains
    )


def classify_link_platform(url):
    hostname = urlparse(clean_text(url)).hostname or ""
    for platform, settings in PLATFORM_IMPORTS.items():
        if host_matches(hostname, settings.get("domains") or []):
            return platform
    return "generic_web"


def standard_http_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("link must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("link must not contain embedded credentials")
    allowed_port = 443 if parsed.scheme == "https" else 80
    if parsed.port not in {None, allowed_port}:
        raise ValueError("link must use the standard HTTP or HTTPS port")
    return parsed.geturl()


def opencli_executable():
    executable = shutil.which("opencli")
    if not executable:
        raise ValueError(
            "PLATFORM_ADAPTER_UNAVAILABLE: opencli is required for this platform URL"
        )
    return executable


def run_opencli(arguments, timeout=60, expect_json=False):
    result = subprocess.run(
        [opencli_executable(), *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        message = clean_text(result.stderr or result.stdout)[:500]
        raise ValueError(f"PLATFORM_READ_FAILED: {message or 'opencli returned an error'}")
    output = result.stdout.strip()
    if expect_json:
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise ValueError("PLATFORM_READ_FAILED: platform returned invalid JSON") from error
    return output


def mime_from_bytes(path):
    with open(path, "rb") as handle:
        prefix = handle.read(16)
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"\x00\x00\x00") and b"ftyp" in prefix:
        return "video/mp4"
    return "application/octet-stream"


def platform_media_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    result = []
    for path in sorted(Path(folder).rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name.endswith(".display.jpg"):
            continue
        mime = mime_from_bytes(path)
        if not mime.startswith(("image/", "video/")):
            continue
        result.append({
            "path": str(path.resolve()),
            "sha256": file_sha256(path),
            "mimeType": mime,
            "immutableOriginal": True,
        })
    return result


def prepare_platform_media_assets(files):
    crop_script = os.path.join(os.path.dirname(__file__), "prepare_display_image.py")
    def prepare_one(item):
        prepared = item.copy()
        if not clean_text(item.get("mimeType")).startswith("image/"):
            return prepared
        source_path = clean_text(item.get("path"))
        display_path = str(Path(source_path).with_name(f"{Path(source_path).stem}.display.jpg"))
        try:
            result = subprocess.run(
                [
                    sys.executable, crop_script, source_path, display_path,
                    "--target-aspect", "4:3", "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            metadata = json.loads(result.stdout)
            score = round(float(metadata.get("photoScore", 0.0)), 4)
            eligible = bool(metadata.get("photoRich"))
            prepared["displayPhotoScore"] = score
            prepared["displayPhotoEligible"] = eligible
            prepared["displayCrop"] = {
                "strategy": clean_text(metadata.get("strategy")),
                "targetAspect": metadata.get("targetAspect"),
                "cropBox": metadata.get("cropBox"),
            }
            if eligible and os.path.isfile(display_path):
                prepared["displayPath"] = display_path
                prepared["displaySha256"] = file_sha256(display_path)
            elif os.path.isfile(display_path):
                os.unlink(display_path)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
            prepared["displayPhotoEligible"] = False
            prepared["displayPreparationWarning"] = clean_text(error)
        return prepared
    if not files:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(files))) as executor:
        return list(executor.map(prepare_one, files))


def platform_engagement(payload):
    aliases = {
        "likes": ("likes", "likeCount", "点赞", "点赞数"),
        "collects": ("collects", "collectCount", "收藏", "收藏数"),
        "comments": ("comments", "commentCount", "评论", "评论数"),
    }
    result = {}
    for target, keys in aliases.items():
        value = next((payload.get(key) for key in keys if payload.get(key) is not None), None)
        if value is not None and clean_text(value):
            result[target] = clean_text(value)
    return result


def multi_place_document(value):
    raw = str(value or "")
    markers = re.findall(
        r"(?:^|\n)\s*(?:Day\s*\d+|\d+[\.、)]|[1-9][️⃣⃣]|\*\*\s*\d+\\?\.)",
        raw,
        re.IGNORECASE,
    )
    named_sections = re.findall(
        r"(?:^|\n)\s*\*\*[A-ZÀ-Þ][^*\n]{2,60}\*\*",
        raw,
    )
    return len(markers) >= 2 or len(named_sections) >= 3


def xiaohongshu_evidence(url, source, timeout, output_locale="zh-CN"):
    parsed_url = urlparse(url)
    if not host_matches(parsed_url.hostname, ["xiaohongshu.com"]):
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_INPUT_INCOMPLETE: 小红书短链已保留；请补充含 xsec_token 的完整笔记链接或截图",
            "PLATFORM_INPUT_INCOMPLETE: The Xiaohongshu short link was retained. Provide a full note URL with xsec_token or a screenshot.",
        ))
    query = parsed_url.query
    if "xsec_token=" not in query:
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_INPUT_INCOMPLETE: 小红书链接已保留；读取正文需要含 xsec_token 的完整笔记链接或截图",
            "PLATFORM_INPUT_INCOMPLETE: The Xiaohongshu URL was retained. Reading the note requires a full URL with xsec_token or a screenshot.",
        ))
    payload = run_opencli(
        ["xiaohongshu", "note", url, "-f", "json"],
        timeout=max(timeout, 60),
        expect_json=True,
    )
    if isinstance(payload, list):
        mapped = {
            clean_text(item.get("field")): item.get("value")
            for item in payload if isinstance(item, dict) and item.get("field")
        }
        payload = mapped or (payload[0] if payload and isinstance(payload[0], dict) else {})
    if not isinstance(payload, dict):
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_READ_FAILED: 小红书笔记返回格式不可识别",
            "PLATFORM_READ_FAILED: The Xiaohongshu note returned an unsupported format.",
        ))
    title = clean_text(payload.get("title") or payload.get("标题") or source.get("name"))
    content = clean_text(
        payload.get("content") or payload.get("desc") or payload.get("正文")
    )
    author = clean_text(payload.get("author") or payload.get("作者"))
    engagement = platform_engagement(payload)
    text_value = "\n".join(item for item in (title, author, content) if item)
    if not clean_text(source.get("name")) and multi_place_document(text_value):
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_MULTIPLE_PLACES: 小红书笔记包含多个地点；原链接已保留，请指定要收纳的地点名或拆成多条来源",
            "PLATFORM_MULTIPLE_PLACES: The Xiaohongshu note contains multiple places. The original URL was retained; name the target place or split it into separate sources.",
        ))
    media_folder = clean_text(source.get("platformMediaDirectory"))
    media = []
    if source.get("downloadMedia") and media_folder:
        os.makedirs(media_folder, exist_ok=True)
        run_opencli(
            ["xiaohongshu", "download", url, "--output", media_folder],
            timeout=max(timeout, 180),
        )
        media = prepare_platform_media_assets(platform_media_files(media_folder))
    return {
        "title": title,
        "originalText": text_value,
        "platform": "xiaohongshu",
        "platformItemId": clean_text(payload.get("noteId") or payload.get("id")),
        "platformAccess": {
            "mode": "signed_note_url",
            "status": "readable",
            "checkedAt": now(),
            "capabilities": [
                "note_text_observed",
                *(["engagement_counts_observed"] if engagement else []),
                *(["platform_media_downloaded"] if media else []),
            ],
            "limitations": ["current_place_status_not_proven"],
        },
        "platformMediaFiles": media,
        **({"platformEngagement": engagement} if engagement else {}),
    }


def ctrip_place_id(url):
    match = re.search(r"/(\d+)\.html(?:$|[?#])", clean_text(url))
    return match.group(1) if match else ""


def ctrip_evidence(url, source, args):
    output_locale = getattr(args, "output_locale", "zh-CN")
    query = clean_text(source.get("name") or args.name)
    if not query:
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_INPUT_INCOMPLETE: 携程原链接已保留；请同时提供地点名或地点截图",
            "PLATFORM_INPUT_INCOMPLETE: The Ctrip URL was retained. Also provide the place name or a place screenshot.",
        ))
    payload = run_opencli(
        ["ctrip", "search", query, "--limit", "10", "-f", "json"],
        timeout=max(args.timeout, 60),
        expect_json=True,
    )
    candidates = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
    wanted_id = ctrip_place_id(url)
    destination = clean_text(source.get("destination") or args.destination or args.city)
    if wanted_id:
        id_matches = [item for item in candidates if clean_text(item.get("id")) == wanted_id]
        if id_matches:
            candidates = id_matches
    if destination and len(candidates) > 1:
        destination_matches = [
            item for item in candidates
            if destination.casefold() in clean_text(item.get("cityName")).casefold()
            or destination.casefold() in clean_text(item.get("name")).casefold()
        ]
        if destination_matches:
            candidates = destination_matches
    if not candidates:
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_NO_MATCH: 携程未找到与地点名、目的地或链接 ID 一致的候选",
            "PLATFORM_NO_MATCH: Ctrip returned no candidate matching the place name, destination or submitted URL ID.",
        ))
    if len(candidates) != 1:
        labels = "；".join(clean_text(item.get("name")) for item in candidates[:5])
        raise ValueError(locale_text(
            output_locale,
            f"PLATFORM_AMBIGUOUS: 携程存在多个地点或分店候选：{labels}",
            f"PLATFORM_AMBIGUOUS: Ctrip returned multiple place or branch candidates: {labels}",
        ))
    item = candidates[0]
    title = clean_text(item.get("name") or query)
    original_text = "\n".join(
        clean_text(value) for value in (
            title, item.get("eName"), item.get("cityName"),
            item.get("provinceName"), item.get("countryName"),
        ) if clean_text(value)
    )
    return {
        "title": title,
        "originalText": original_text,
        "structuredNames": unique([title, item.get("eName")]),
        "platform": "ctrip",
        "platformItemId": clean_text(item.get("id")),
        "platformPlace": {
            "name": title,
            "englishName": clean_text(item.get("eName")),
            "city": clean_text(item.get("cityName")),
            "country": clean_text(item.get("countryName")),
            "latitude": item.get("lat"),
            "longitude": item.get("lon"),
        },
        "platformAccess": {
            "mode": "name_search_with_url_preservation",
            "status": "readable",
            "checkedAt": now(),
            "capabilities": [
                "provider_place_id_observed", "place_name_observed",
                "city_country_observed", "coordinates_observed",
            ],
            "limitations": ["submitted_detail_page_body_not_observed"],
        },
        "pageContentObserved": False,
    }


def parse_wechat_markdown(markdown):
    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = clean_text(title_match.group(1) if title_match else "")
    images = unique(re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)", markdown))
    return {
        "title": title,
        "originalText": markdown[:12000],
        "publicImageUrls": images,
    }


def wechat_evidence(url, source, args):
    output_locale = getattr(args, "output_locale", "zh-CN")
    markdown = run_opencli(
        ["web", "read", "--url", url, "--stdout", "true", "--download-images", "false"],
        timeout=max(args.timeout, 120),
    )
    lower = markdown.casefold()
    if any(marker in lower for marker in ("安全检测", "验证码", "verification code", "访问过于频繁")):
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_SECURITY_CHECK: 公众号文章触发安全检测，原链接已保留",
            "PLATFORM_SECURITY_CHECK: The WeChat article triggered a security check. The original URL was retained.",
        ))
    parsed = parse_wechat_markdown(markdown)
    if not parsed["title"] or len(clean_text(parsed["originalText"])) < 80:
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_READ_FAILED: 未读取到完整公众号文章正文",
            "PLATFORM_READ_FAILED: The complete WeChat article body could not be read.",
        ))
    if not clean_text(source.get("name") or args.name) and multi_place_document(markdown):
        raise ValueError(locale_text(
            output_locale,
            "PLATFORM_MULTIPLE_PLACES: 公众号文章包含多个地点；原链接已保留，请指定要收纳的地点名或拆成多条来源",
            "PLATFORM_MULTIPLE_PLACES: The WeChat article contains multiple places. The original URL was retained; name the target place or split it into separate sources.",
        ))
    return {
        **parsed,
        "platform": "wechat_official",
        "platformAccess": {
            "mode": "public_article_browser_read",
            "status": "readable",
            "checkedAt": now(),
            "capabilities": [
                "article_title_observed", "article_body_observed",
                *(["article_image_urls_observed"] if parsed["publicImageUrls"] else []),
            ],
            "limitations": ["article_image_files_not_copied"],
        },
    }


def fetch_platform_link(url, source, args):
    platform = classify_link_platform(url)
    if platform == "xiaohongshu":
        return xiaohongshu_evidence(url, source, args.timeout, getattr(args, "output_locale", "zh-CN"))
    if platform == "ctrip":
        return ctrip_evidence(url, source, args)
    if platform == "wechat_official":
        return wechat_evidence(url, source, args)
    if platform == "mafengwo":
        raise ValueError(locale_text(
            getattr(args, "output_locale", "zh-CN"),
            "PLATFORM_SECURITY_CHECK: 马蜂窝公开正文触发安全检测；原链接已保留，请补截图、保存网页或粘贴文字",
            "PLATFORM_SECURITY_CHECK: Mafengwo blocked the public article with a security check. The original URL was retained; provide screenshots, a saved page or pasted text.",
        ))
    if platform == "douyin":
        return douyin_evidence(url, source, args)
    if platform == "tiktok":
        return oembed_evidence(
            url, source, args, "tiktok", "https://www.tiktok.com/oembed",
            "official_public_oembed",
        )
    if platform == "instagram":
        return instagram_evidence(url, source, args)
    if platform == "youtube":
        return oembed_evidence(
            url, source, args, "youtube", "https://www.youtube.com/oembed",
            "public_oembed_metadata",
        )
    document, final_url = fetch_public_link(url, args.timeout)
    parsed = parse_html_evidence(document)
    parsed["platform"] = platform
    parsed["finalUrl"] = final_url
    parsed["platformMedia"] = host_matches(
        urlparse(final_url).hostname, PLATFORM_MEDIA_HOSTS,
    )
    return parsed


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
    parsed = urlparse(standard_http_url(value))
    allowed_port = 443 if parsed.scheme == "https" else 80
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
    global_addresses = {
        address for address in addresses if ipaddress.ip_address(address).is_global
    }
    if not global_addresses:
        raise ValueError("link host resolves to a non-public address")
    if parsed.hostname.casefold() not in PUBLIC_METADATA_ENDPOINT_HOSTS:
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                raise ValueError("link host resolves to a non-public address")
    return parsed.geturl()


def validate_connected_peer(response, request_url=""):
    file_pointer = getattr(response, "fp", None)
    raw = getattr(file_pointer, "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is None:
        return
    address = sock.getpeername()[0]
    peer_ip = ipaddress.ip_address(address)
    if peer_ip.is_global:
        return
    request_scheme = urlparse(clean_text(request_url)).scheme
    proxy_url = clean_text(urllib.request.getproxies().get(request_scheme))
    proxy_host = urlparse(proxy_url).hostname if proxy_url else ""
    proxy_addresses = set()
    if proxy_host:
        try:
            proxy_addresses = {
                item[4][0]
                for item in socket.getaddrinfo(proxy_host, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror:
            proxy_addresses = set()
    if address in proxy_addresses:
        return
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
            "User-Agent": f"WantToGoTripPlanner/{PRODUCT_CONFIG['version']} (+public-evidence-only)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    opener = urllib.request.build_opener(PublicRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        public_http_url(response.geturl())
        validate_connected_peer(response, safe_url)
        content_type = str(response.headers.get("content-type", "")).lower()
        if "html" not in content_type:
            raise ValueError("link did not return an HTML page")
        raw = response.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raise ValueError("link page exceeds 2 MB")
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), response.geturl()


def fetch_public_json(url, timeout):
    safe_url = public_http_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": f"WantToGoTripPlanner/{PRODUCT_CONFIG['version']} (+public-evidence-only)",
            "Accept": "application/json",
        },
    )
    opener = urllib.request.build_opener(PublicRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        public_http_url(response.geturl())
        validate_connected_peer(response, safe_url)
        raw = response.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("public metadata response exceeds 512 KB")
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            payload = json.loads(raw.decode(charset, errors="replace"))
        except json.JSONDecodeError as error:
            raise ValueError("public metadata endpoint returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("public metadata endpoint returned an unsupported result")
        return payload, response.geturl()


def social_item_id(url, platform):
    parsed = urlparse(clean_text(url))
    path = parsed.path or ""
    if platform == "tiktok":
        match = re.search(r"/@[^/]+/video/(\d+)", path)
    elif platform == "instagram":
        match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", path)
    elif platform == "youtube":
        if host_matches(parsed.hostname, ["youtu.be"]):
            match = re.match(r"/([A-Za-z0-9_-]{6,})", path)
        else:
            query_id = clean_text((parse_qs(parsed.query).get("v") or [""])[0])
            match = re.search(r"/(?:shorts|embed)/([A-Za-z0-9_-]{6,})", path)
            if query_id:
                return query_id
    else:
        match = re.search(r"/video/(\d+)", path)
    return clean_text(match.group(1)) if match else ""


def oembed_evidence(url, source, args, platform, endpoint, mode):
    locale = getattr(args, "output_locale", "zh-CN")
    item_id = social_item_id(url, platform)
    parsed_url = urlparse(url)
    if platform == "tiktok" and not item_id and not host_matches(
        parsed_url.hostname, ["vm.tiktok.com", "vt.tiktok.com"],
    ):
        raise ValueError(locale_text(
            locale,
            "PLATFORM_INPUT_INCOMPLETE: TikTok 原链接已保留；请提供公开视频链接，或补原视频、截图或文字",
            "PLATFORM_INPUT_INCOMPLETE: The TikTok URL was retained. Provide a public video URL, or add the original video, screenshots or text.",
        ))
    if platform == "youtube" and not item_id:
        raise ValueError(locale_text(
            locale,
            "PLATFORM_INPUT_INCOMPLETE: YouTube 原链接已保留；请提供公开视频或 Shorts 链接",
            "PLATFORM_INPUT_INCOMPLETE: The YouTube URL was retained. Provide a public video or Shorts URL.",
        ))
    metadata_url = f"{endpoint}?{urlencode({'url': url})}"
    if platform == "youtube":
        metadata_url += "&format=json"
    try:
        payload, _ = fetch_public_json(metadata_url, args.timeout)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise ValueError(locale_text(
            locale,
            f"PLATFORM_CONTENT_UNAVAILABLE: {platform} 公开元数据不可用；原链接已保留，请补原视频、截图或文字",
            f"PLATFORM_CONTENT_UNAVAILABLE: {platform} public metadata is unavailable. The original URL was retained; provide the original video, screenshots or text.",
        )) from error
    title = clean_text(payload.get("title"))
    author = clean_text(payload.get("author_name"))
    provider = clean_text(payload.get("provider_name"))
    if not title and not clean_text(source.get("name") or getattr(args, "name", "")):
        raise ValueError(locale_text(
            locale,
            "PLATFORM_INPUT_INCOMPLETE: Instagram 公开嵌入已确认，但未返回可用的地点文字；原链接已保留，请补地点名、截图、原视频或文字",
            "PLATFORM_INPUT_INCOMPLETE: The public Instagram embed was confirmed but returned no usable place text. The original URL was retained; add the place name, screenshots, original video or text.",
        ))
    original = "\n".join(unique([title, author, provider]))
    capabilities = ["public_embed_confirmed"]
    if title:
        capabilities.append("oembed_title_observed")
    if author:
        capabilities.append("oembed_author_observed")
    thumbnail = clean_text(payload.get("thumbnail_url"))
    if thumbnail:
        capabilities.append("oembed_thumbnail_url_observed")
    return {
        "title": title,
        "description": "",
        "structuredNames": [],
        "structuredAddresses": [],
        "originalText": original,
        "mediaType": "video" if platform in {"tiktok", "youtube"} else "page",
        "publicVideoUrls": [],
        "thumbnailUrl": thumbnail,
        "platform": platform,
        "platformItemId": item_id or clean_text(
            payload.get("embed_product_id") or payload.get("author_unique_id")
        ),
        "pageContentObserved": False,
        "platformMedia": True,
        "platformAccess": {
            "mode": mode,
            "status": "readable",
            "checkedAt": now(),
            "capabilities": capabilities,
            "limitations": [
                "full_post_body_not_observed",
                "video_binary_not_downloaded",
                "captions_or_spoken_words_not_observed",
                "place_identity_not_proven_by_platform_metadata",
            ],
        },
    }


def instagram_evidence(url, source, args):
    parsed = urlparse(url)
    if re.search(r"/stories/", parsed.path or "", re.IGNORECASE):
        raise ValueError(locale_text(
            getattr(args, "output_locale", "zh-CN"),
            "PLATFORM_INPUT_INCOMPLETE: Instagram Story 不支持自动读取；原链接已保留，请补截图、原视频或文字",
            "PLATFORM_INPUT_INCOMPLETE: Instagram Stories are not supported for automatic reading. The original URL was retained; provide screenshots, the original video or text.",
        ))
    if not social_item_id(url, "instagram"):
        raise ValueError(locale_text(
            getattr(args, "output_locale", "zh-CN"),
            "PLATFORM_INPUT_INCOMPLETE: Instagram 原链接已保留；请提供公开帖子或 Reel 链接",
            "PLATFORM_INPUT_INCOMPLETE: The Instagram URL was retained. Provide a public post or Reel URL.",
        ))
    return oembed_evidence(
        url, source, args, "instagram",
        "https://graph.facebook.com/v25.0/instagram_oembed",
        "official_tokenless_oembed_with_local_fallback",
    )


def douyin_evidence(url, source, args):
    locale = getattr(args, "output_locale", "zh-CN")
    parsed_url = urlparse(url)
    if not social_item_id(url, "douyin") and not host_matches(
        parsed_url.hostname, ["v.douyin.com"],
    ):
        raise ValueError(locale_text(
            locale,
            "PLATFORM_INPUT_INCOMPLETE: 抖音原链接已保留；请提供具体公开作品链接或分享短链",
            "PLATFORM_INPUT_INCOMPLETE: The Douyin URL was retained. Provide a specific public video URL or share short link.",
        ))
    try:
        document, final_url = fetch_public_link(url, args.timeout)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise ValueError(locale_text(
            locale,
            "PLATFORM_SECURITY_CHECK: 抖音公开页本轮无法读取；原链接已保留，请补原视频、截图或文字",
            "PLATFORM_SECURITY_CHECK: The public Douyin page could not be read in this run. The original URL was retained; provide the original video, screenshots or text.",
        )) from error
    parsed = parse_html_evidence(document)
    if not parsed["originalText"]:
        raise ValueError(locale_text(
            locale,
            "PLATFORM_CONTENT_UNAVAILABLE: 抖音页面未返回可用文字；原链接已保留，请补原视频、截图或文字",
            "PLATFORM_CONTENT_UNAVAILABLE: The Douyin page returned no usable text. The original URL was retained; provide the original video, screenshots or text.",
        ))
    parsed.update({
        "platform": "douyin",
        "platformItemId": social_item_id(final_url, "douyin") or social_item_id(url, "douyin"),
        "finalUrl": final_url,
        "platformMedia": True,
        "platformAccess": {
            "mode": "public_share_page_with_local_fallback",
            "status": "readable",
            "checkedAt": now(),
            "capabilities": ["public_page_metadata_observed"],
            "limitations": [
                "official_richer_video_api_requires_authorization",
                "video_binary_not_downloaded",
                "captions_or_spoken_words_not_proven_complete",
                "place_identity_not_proven_by_platform_metadata",
            ],
        },
    })
    return parsed


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
            and not UNTRUSTED_INSTRUCTION_PATTERN.search(line)
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
        "city": clean_text(args.city or (extra.get("platformPlace") or {}).get("city")),
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
        "sourcePolicy": source_policy(
            source_type,
            text,
            "public_readable" if source_type == "link"
            else "local_only" if source_type in {"screenshot", "video", "saved_html"}
            else "submitted",
        ),
    }
    if result["sourcePolicy"]["untrustedInstructionsDetected"]:
        result["extractionWarnings"].append(
            "External content contained instruction-like text. It was retained only as untrusted evidence and was not executed."
            if args.output_locale == "en-US"
            else "外部内容含指令式文本；仅作为不可信证据保留，不执行其中要求。"
        )
    platform = clean_text(extra.get("platform"))
    if platform:
        result["platform"] = platform
    if clean_text(extra.get("platformItemId")):
        result["platformItemId"] = clean_text(extra.get("platformItemId"))
    if isinstance(extra.get("platformAccess"), dict):
        result["platformAccess"] = extra["platformAccess"]
        result["sourcePolicy"]["canSupport"] = unique([
            *result["sourcePolicy"]["canSupport"],
            *(extra["platformAccess"].get("capabilities") or []),
        ])
        result["sourcePolicy"]["cannotProve"] = unique([
            *result["sourcePolicy"]["cannotProve"],
            *(extra["platformAccess"].get("limitations") or []),
        ])
    if extra.get("pageContentObserved") is False:
        result["sourcePolicy"]["canSupport"] = [
            item for item in result["sourcePolicy"]["canSupport"]
            if item != "page_content_observed"
        ]
    if isinstance(extra.get("platformPlace"), dict):
        result["platformPlace"] = extra["platformPlace"]
    if isinstance(extra.get("platformEngagement"), dict):
        result["platformEngagement"] = extra["platformEngagement"]
    if isinstance(extra.get("platformMediaFiles"), list):
        result["platformMediaFiles"] = extra["platformMediaFiles"]
    if isinstance(extra.get("publicImageUrls"), list):
        result["publicImageUrls"] = extra["publicImageUrls"]
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
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
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
    source = source.copy()
    source.setdefault("name", clean_text(args.name))
    source.setdefault("destination", clean_text(args.destination))
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
                "sourcePolicy": source_policy(
                    "screenshot", "", "local_only", captured=False
                ),
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
        platform = classify_link_platform(value)
        if platform == "generic_web":
            public_http_url(value)
        else:
            standard_http_url(value)
        parsed = fetch_platform_link(value, source, args)
        result = build_evidence(
            args,
            parsed["originalText"],
            "link",
            value,
            parsed,
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
    evidence["originalSha256"] = file_sha256(target_path)
    evidence["originalImmutable"] = True
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
            if (
                clean_text(source.get("type")).casefold() == "link"
                and classify_link_platform(source.get("value") or "") == "xiaohongshu"
                and storage_mode == "durable"
                and args.output
            ):
                source.setdefault("downloadMedia", True)
                source.setdefault(
                    "platformMediaDirectory",
                    os.path.join(
                        os.path.dirname(os.path.abspath(args.output)),
                        f"{os.path.splitext(os.path.basename(args.output))[0]}-assets",
                        safe_asset_token(bundle_id),
                        safe_asset_token(source.get("id")),
                        "platform-originals",
                    ),
                )
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
            reason = clean_text(error)
            source_type = clean_text(source.get("type")).casefold()
            platform = (
                classify_link_platform(source.get("value") or "")
                if source_type == "link" else ""
            )
            failure_code = reason.split(":", 1)[0] if reason.startswith("PLATFORM_") else "EXTRACTION_FAILED"
            platform_status = (
                "security_check_required" if failure_code == "PLATFORM_SECURITY_CHECK"
                else "input_incomplete" if failure_code in {"PLATFORM_INPUT_INCOMPLETE", "PLATFORM_MULTIPLE_PLACES", "PLATFORM_AMBIGUOUS"}
                else "unavailable"
            )
            failures.append(
                {
                    **source_public_ref(source),
                    "status": "failed",
                    "reason": reason,
                    "failureCode": failure_code,
                    **({"platform": platform} if platform else {}),
                    **({
                        "platformAccess": {
                            "mode": clean_text((PLATFORM_IMPORTS.get(platform) or {}).get("mode")) or "generic_public_web",
                            "status": platform_status,
                            "checkedAt": now(),
                            "capabilities": ["original_url_submitted"],
                            "limitations": ["blocked_or_incomplete_source_contents"],
                        }
                    } if platform else {}),
                    "mustRemainVisible": True,
                    "sourcePolicy": source_policy(
                        source_type,
                        clean_text(source.get("value")),
                        "public_blocked" if source_type == "link" else "unavailable",
                        captured=False,
                    ),
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
    opencli = command_probe("opencli")
    video_ready = bool(ffmpeg["ready"] and ffprobe["ready"])
    core_ready = bool(platform_supported and python_ready and pillow["ready"] and node["ready"])
    full_ready = bool(core_ready and ocr_ready and opencli["ready"])
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
    if not opencli["ready"]:
        feature_warnings.append(
            "缺少 opencli：小红书、携程和公众号链接仍会原样保留，但自动读取受限；请从受信来源安装后重新运行 doctor。"
            if not is_english else
            "opencli is unavailable: Xiaohongshu, Ctrip, and WeChat URLs are still preserved, but automatic reading is limited. Install it from a trusted source and rerun doctor."
        )

    return {
        "schemaVersion": PRODUCT_CONFIG["installDoctorMarker"],
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
            "platformLinkImports": {
                "xiaohongshu": bool(opencli["ready"]),
                "ctrip": bool(opencli["ready"]),
                "wechatOfficial": bool(opencli["ready"]),
                "mafengwo": "local_evidence_fallback",
                "douyin": "public_page_with_local_fallback",
                "tiktok": "official_public_oembed",
                "instagram": "official_oembed_with_local_fallback",
                "youtube": "public_oembed_metadata",
            },
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
            "opencli": opencli,
        },
        "ocrProvider": ocr_provider,
        "blockingIssues": blocking_issues,
        "featureWarnings": feature_warnings,
        "installCommands": unique(install_commands),
        "videoSceneLimit": MAX_VIDEO_SCENES,
        "privacy": {
            "uploadsOriginalScreenshot": False,
            "usesBrowserCookies": bool(opencli["ready"]),
            "browserCookieBoundary": (
                "opencli_may_reuse_local_read_only_browser_session_never_exported"
                if opencli["ready"] else
                "not_used"
            ),
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
