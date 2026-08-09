#!/usr/bin/env python3
"""Local want-to-go library and passport preparation.

Public edition: no login, payment, platform bypass, directory scanning, or
private service dependency.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Union

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "product.json"
CONTRACT_PATH = Path(__file__).resolve().parent.parent / "references" / "shared-data-contract-v2.schema.json"


def load_product_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = {"version", "contractVersion", "sourcePolicyVersion", "form", "offers", "copy", "paymentWorkflow"}
    missing = sorted(required - set(config))
    if missing:
        raise RuntimeError(f"product config missing keys: {', '.join(missing)}")
    return config


PRODUCT_CONFIG = load_product_config()
VERSION = str(PRODUCT_CONFIG["version"])
CONTRACT_VERSION = str(PRODUCT_CONFIG["contractVersion"])
SOURCE_POLICY_VERSION = str(PRODUCT_CONFIG["sourcePolicyVersion"])
CTA_URL = str(PRODUCT_CONFIG["form"]["requestUrl"])
PUBLIC_OFFER_ID = str(PRODUCT_CONFIG["form"]["publicOfferId"])
REQUEST_WORKFLOW_STAGES = list(PRODUCT_CONFIG["paymentWorkflow"]["stages"])
REQUEST_STAGE_REQUIREMENTS = {
    "scope_schedule_confirmed": ("agreedReviewDate", "deliveryDueAt", "scopeConfirmedAt"),
    "customer_confirmed": ("customerConfirmedAt",),
    "payment_instructions_sent": ("paymentInstructionsSentAt",),
    "payment_recorded": ("paymentRecordedAt",),
    "in_delivery": ("deliveryStartedAt",),
}
PENDING_DESTINATION_KEY = "pending"
DESTINATION_ALIASES = {
    "上海": "shanghai",
    "上海市": "shanghai",
    "shanghai": "shanghai",
    "曼谷": "bangkok",
    "bangkok": "bangkok",
    "กรุงเทพ": "bangkok",
    "กรุงเทพมหานคร": "bangkok",
}
DESTINATION_LABELS_ZH = {
    "shanghai": "上海",
    "bangkok": "曼谷",
    PENDING_DESTINATION_KEY: "待确认目的地",
}
BUSINESS_HOURS_FALLBACK_ZH = "营业时间请以出发当天商户公开信息为准"
BUSINESS_HOURS_FALLBACK_EN = "Check the merchant's public information again on the day of your visit."
PUBLIC_SPACE_HOURS_ZH = "公共空间无统一营业时间，场内商户各自安排"
PUBLIC_SPACE_HOURS_EN = "This public space has no single opening schedule; individual venues set their own hours."
MISSING_FIELD_LABELS_ZH = {
    "verifiedName": "已核实名称",
    "signature": "想去理由",
    "visitTip": "出发提醒",
    "address": "地址",
    "openingHoursText": "营业或开放时间",
    "position": "位置描述",
    "route": "路线起终点或途经点",
    "suggestedDuration": "建议时长",
}
MISSING_FIELD_LABELS_EN = {
    "verifiedName": "verified name",
    "signature": "why go",
    "visitTip": "before-you-go reminder",
    "address": "address",
    "openingHoursText": "opening hours",
    "position": "position",
    "route": "route start/end or waypoints",
    "suggestedDuration": "suggested duration",
}

EDITABLE_PLACE_FIELDS = {
    "name", "verifiedName", "localName", "placeType", "category", "categoryLabel",
    "branch", "branchName", "address", "addressText", "positionText", "openingHoursText",
    "openingHours", "signature", "reason", "whyGo", "highlight", "visitTip", "reminder",
    "departureReminder", "departureTip", "routeStart", "routeEnd", "waypoints",
    "suggestedDuration", "durationText", "destination", "nameSource",
    "nameRequiresConfirmation", "detailLookupAudit", "displayMediaId",
}
UNDOABLE_EVENT_TYPES = {"place.update", "place.delete", "place.restore", "place.reorder"}
SECRET_PATTERN = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk|rk|pk)_[A-Za-z0-9]{20,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|cookie)\s*[:=]\s*[^\s,;]{8,})",
    re.IGNORECASE,
)
CUSTOMER_INTERNAL_PATTERN = re.compile(
    r"(?:\.workbuddy|\.claude|/Users/|/mnt/|localhost|127\.0\.0\.1|"
    r"sourceIds?|mediaIds?|confidenceScore|detailLookupAudit|nameSource|hostChecks|"
    r"platformAccess|platformItemId|platformEngagement|failureCode|"
    r"schemaVersion|operationId|tombstones?|promptInjection|OCR|模型|宿主诊断)",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Union[str, Path]) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Union[str, Path], value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(str(temporary), str(target))
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


write_json = atomic_write_json


@contextmanager
def file_lock(path: Union[str, Path], timeout: float = 10.0) -> Iterator[None]:
    """Use one adjacent lock file with POSIX flock or Windows msvcrt locking."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f"{target.name}.lock")
    handle = lock_path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + max(0.1, timeout)
    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"library lock timed out: {lock_path}")
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def empty_library() -> dict[str, Any]:
    created = now_iso()
    return {
        "schemaVersion": CONTRACT_VERSION,
        "id": f"library-{uuid.uuid4().hex[:12]}",
        "revision": 0,
        "createdAt": created,
        "updatedAt": created,
        "destinations": [],
        "places": [],
        "sources": [],
        "media": [],
        "verificationSnapshots": [],
        "tripRequests": [],
        "events": [],
        "tombstones": [],
        "ingestBatches": [],
        "bundles": [],
    }


def migrate_library_data(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    data = copy.deepcopy(raw)
    changes: list[str] = []
    old_version = text(data.get("schemaVersion")) or "unversioned"
    if old_version != CONTRACT_VERSION:
        changes.append(f"schemaVersion:{old_version}->{CONTRACT_VERSION}")
    data["schemaVersion"] = CONTRACT_VERSION
    data.setdefault("id", f"library-{uuid.uuid4().hex[:12]}")
    data.setdefault("revision", 0)
    data.setdefault("createdAt", now_iso())
    data.setdefault("updatedAt", data["createdAt"])
    if "destinations" not in data:
        data["destinations"] = copy.deepcopy(data.get("destinationCollections") or [])
        if data.get("destinationCollections"):
            changes.append("destinationCollections->destinations")
    data.pop("destinationCollections", None)
    for key in (
        "destinations", "places", "sources", "media", "verificationSnapshots",
        "tripRequests", "events", "tombstones", "ingestBatches", "bundles",
    ):
        if not isinstance(data.get(key), list):
            data[key] = []
            changes.append(f"repair:{key}")
    if not data["ingestBatches"] and data["bundles"]:
        for bundle in data["bundles"]:
            if not isinstance(bundle, dict):
                continue
            batch_id = text(bundle.get("bundleId")) or f"batch-{uuid.uuid4().hex[:12]}"
            source_ids = [
                text(item.get("sourceId") or item.get("id"))
                for item in bundle_sources(bundle)
                if isinstance(item, dict) and text(item.get("sourceId") or item.get("id"))
            ]
            data["ingestBatches"].append({
                "id": batch_id,
                "destination": text(bundle.get("destination")),
                "sourceIds": merge_unique(source_ids),
                "createdAt": text(bundle.get("createdAt")) or now_iso(),
                "passportGeneratedDestinations": merge_unique(bundle.get("passportGeneratedDestinations") or []),
            })
        changes.append("bundles->ingestBatches")
    for index, place in enumerate(data["places"]):
        if not isinstance(place, dict):
            continue
        place.setdefault("id", f"place-{uuid.uuid4().hex[:12]}")
        place.setdefault("sourceIds", [])
        place.setdefault("mediaIds", [])
        place.setdefault("sortOrder", index)
        place.setdefault("createdAt", now_iso())
        place.setdefault("updatedAt", place["createdAt"])
        source_name = text(place.get("nameSource"))
        name_aliases = {
            "user": "user_named",
            "structured_data": "public_page",
            "page_title": "public_page",
            "ocr_or_text_heuristic": "material_ocr",
        }
        if source_name in name_aliases:
            place["nameSource"] = name_aliases[source_name]
    for snapshot in data["verificationSnapshots"]:
        if not isinstance(snapshot, dict):
            continue
        if text(snapshot.get("sourcePolicyVersion")) != SOURCE_POLICY_VERSION:
            snapshot["sourcePolicyVersion"] = SOURCE_POLICY_VERSION
            changes.append(f"verification_source_policy:{text(snapshot.get('id')) or 'unknown'}")
        if text(snapshot.get("trigger")) == "pre_trip_on_demand":
            snapshot["trigger"] = "pre_trip_on_demand_legacy"
            snapshot["legacyImported"] = True
            changes.append(f"legacy_verification_snapshot:{text(snapshot.get('id')) or 'unknown'}")
    manual_limits = PRODUCT_CONFIG["offers"]["manualItineraryBeta"]["limits"]
    current_statuses = {"draft", *REQUEST_WORKFLOW_STAGES, "declined", "cancelled"}
    for request in data["tripRequests"]:
        if not isinstance(request, dict):
            continue
        request.setdefault("updatedAt", text(request.get("createdAt")) or now_iso())
        old_status = text(request.get("status"))
        if old_status and old_status not in current_statuses:
            request["legacyStatus"] = old_status
            request["status"] = "submitted" if old_status == "accepted" else "draft"
            request["legacyImported"] = True
            changes.append(f"legacy_trip_request_status:{text(request.get('id')) or 'unknown'}:{old_status}")
        if text(request.get("offerId")) == PUBLIC_OFFER_ID:
            days = int(request.get("days", 0) or 0)
            count = len(request.get("placeIds") or [])
            if (
                (days and not manual_limits["daysMin"] <= days <= manual_limits["daysMax"])
                or (count and not manual_limits["placesMin"] <= count <= manual_limits["placesMax"])
            ):
                was_legacy = request.get("legacyImported") is True
                request["legacyImported"] = True
                if not was_legacy:
                    changes.append(f"legacy_trip_request_scope:{text(request.get('id')) or 'unknown'}")
        status = text(request.get("status"))
        ordered_statuses = ["draft", *REQUEST_WORKFLOW_STAGES]
        if status in ordered_statuses:
            missing_stage_fields = [
                field
                for stage, fields in REQUEST_STAGE_REQUIREMENTS.items()
                if ordered_statuses.index(status) >= ordered_statuses.index(stage)
                for field in fields
                if not text(request.get(field))
            ]
            if missing_stage_fields:
                was_legacy = request.get("legacyImported") is True
                request["legacyImported"] = True
                if not was_legacy:
                    changes.append(f"legacy_trip_request_stage:{text(request.get('id')) or 'unknown'}")
    rebuild_source_ledger(data)
    rebuild_media_ledger(data)
    rebuild_destinations(data)
    return data, changes


def load_library(path: Union[str, Path]) -> dict[str, Any]:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return empty_library()
    data = read_json(target)
    if not isinstance(data, dict):
        raise ValueError("library must be a JSON object")
    migrated, _changes = migrate_library_data(data)
    return migrated


def _save_library_unlocked(path: Union[str, Path], data: dict[str, Any]) -> None:
    migrated, _changes = migrate_library_data(data)
    migrated["schemaVersion"] = CONTRACT_VERSION
    migrated["updatedAt"] = now_iso()
    data.clear()
    data.update(migrated)
    atomic_write_json(path, data)


def save_library(path: Union[str, Path], data: dict[str, Any]) -> None:
    with file_lock(path):
        _save_library_unlocked(path, data)


@contextmanager
def library_transaction(path: Union[str, Path]) -> Iterator[dict[str, Any]]:
    with file_lock(path):
        library = load_library(path)
        yield library
        _save_library_unlocked(path, library)


def text(value: Any) -> str:
    return str(value or "").strip()


def normalized_token(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", text(value)).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def destination_key(value: Any) -> str:
    raw = text(value)
    if not raw:
        return PENDING_DESTINATION_KEY
    return DESTINATION_ALIASES.get(raw.casefold(), normalized_token(raw) or PENDING_DESTINATION_KEY)


def destination_label(key: str, fallback: str = "") -> str:
    return DESTINATION_LABELS_ZH.get(key) or text(fallback) or key


def source_destination(source: dict[str, Any], bundle_destination: str = "") -> str:
    return first(source, "destination", "city") or text(bundle_destination)


def bundle_destination_keys(bundle: dict[str, Any]) -> set[str]:
    bundle_destination = text(bundle.get("destination"))
    sources = bundle_sources(bundle)
    if not sources:
        return {destination_key(bundle_destination)}
    return {
        destination_key(source_destination(source, bundle_destination))
        for source in sources
    }


def collection_id(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"destination-{digest}"


def rebuild_destinations(library: dict[str, Any]) -> None:
    previous = {
        text(item.get("key") or item.get("destinationKey")): item
        for item in (library.get("destinations") or [])
        if isinstance(item, dict) and text(item.get("key") or item.get("destinationKey"))
    }
    collections: dict[str, dict[str, Any]] = {}

    def ensure(key: str, label: str = "") -> dict[str, Any]:
        existing = previous.get(key) or {}
        if key not in collections:
            collections[key] = {
                "id": text(existing.get("id")) or collection_id(key),
                "key": key,
                "name": destination_label(key, label),
                "status": "needs_confirmation" if key == PENDING_DESTINATION_KEY else "confirmed",
                "sourceIds": [],
                "placeIds": [],
                "sortOrder": int(existing.get("sortOrder", len(collections))),
                "createdAt": text(existing.get("createdAt")) or now_iso(),
                "updatedAt": now_iso(),
            }
            if text(existing.get("passportGeneratedAt")):
                collections[key]["passportGeneratedAt"] = existing["passportGeneratedAt"]
        return collections[key]

    for source in library.get("sources") or []:
        if not isinstance(source, dict):
            continue
        label = text(source.get("destination"))
        key = text(source.get("destinationKey")) or destination_key(label)
        collection = ensure(key, label)
        source_id = text(source.get("id"))
        if source_id and source_id not in collection["sourceIds"]:
            collection["sourceIds"].append(source_id)

    for place in library.get("places") or []:
        if not isinstance(place, dict):
            continue
        key = text(place.get("destinationKey")) or destination_key(place.get("destination"))
        place["destinationKey"] = key
        place["destinationStatus"] = "needs_confirmation" if key == PENDING_DESTINATION_KEY else "confirmed"
        collection = ensure(key, text(place.get("destination")))
        place_id = text(place.get("id"))
        if place_id and place_id not in collection["placeIds"]:
            collection["placeIds"].append(place_id)
        collection["placeCount"] = len(collection["placeIds"])

    library["destinations"] = sorted(
        collections.values(),
        key=lambda item: (item["key"] == PENDING_DESTINATION_KEY, item["sortOrder"], item["name"]),
    )


def first(place: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = text(place.get(key))
        if value:
            return value
    return ""


def normalized_photo(value: Any) -> dict[str, Any]:
    """Keep photo metadata structured so the locked renderer can embed it."""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        return {"dataUrl": raw} if raw.startswith("data:image/") else {"path": raw}
    if not isinstance(value, dict):
        return {}
    data_url = text(value.get("dataUrl"))
    path_value = text(
        value.get("path")
        or value.get("displayPath")
        or value.get("displayImagePath")
        or value.get("preparedImagePath")
    )
    result: dict[str, Any] = {}
    if data_url.startswith("data:image/"):
        result["dataUrl"] = data_url
    elif path_value:
        result["path"] = path_value
    focal = value.get("focalPoint")
    if isinstance(focal, dict):
        try:
            result["focalPoint"] = {
                "x": max(0.0, min(1.0, float(focal.get("x", 0.5)))),
                "y": max(0.0, min(1.0, float(focal.get("y", 0.5)))),
            }
        except (TypeError, ValueError):
            pass
    return result


def source_links(place: dict[str, Any]) -> list[dict[str, str]]:
    raw = place.get("originalSourceLinks") or place.get("sourceLinks") or place.get("sources") or []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            url, label = item.strip(), ""
        elif isinstance(item, dict):
            url = text(item.get("url") or item.get("href") or item.get("sourceUrl"))
            label = text(item.get("label") or item.get("title"))
        else:
            continue
        if not re.match(r"^https?://", url, flags=re.I) or url in seen:
            continue
        seen.add(url)
        result.append({"url": url, "label": label or "打开原始收藏链接"})
    return result


def audit_allows_hours_fallback(place: dict[str, Any]) -> bool:
    for item in place.get("detailLookupAudit") or []:
        if not isinstance(item, dict):
            continue
        if item.get("field") != "openingHoursText" or item.get("status") != "not_found":
            continue
        if not text(item.get("checkedAt")):
            continue
        urls = item.get("checkedUrls")
        if isinstance(urls, list) and any(re.match(r"^https?://", text(url), re.I) for url in urls):
            return True
    return False


def verified_name(place: dict[str, Any]) -> str:
    if place.get("nameRequiresConfirmation") is True:
        return ""
    if place.get("nameSource") == "material_ocr":
        return ""
    return first(place, "verifiedName", "name", "displayName")


def normalized_place_type(place: dict[str, Any]) -> str:
    raw = first(place, "placeType", "type", "category").lower()
    aliases = {
        "merchant": "business",
        "restaurant": "business",
        "cafe": "business",
        "shop": "business",
        "museum": "venue",
        "gallery": "venue",
        "attraction": "venue",
        "park": "public_space",
        "neighborhood": "public_space",
        "citywalk": "route",
        "walk": "route",
    }
    return aliases.get(raw, raw if raw in {"business", "venue", "public_space", "route"} else "business")


def passport_missing_fields(place: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    place_type = normalized_place_type(place)
    if not verified_name(place):
        missing.append("verifiedName")

    signature = first(place, "signature", "reason", "whyGo", "highlight")
    visit_tip = first(place, "visitTip", "reminder", "departureReminder", "departureTip")
    if not signature:
        missing.append("signature")
    if not visit_tip:
        missing.append("visitTip")

    if place_type == "business":
        if not first(place, "address", "addressText"):
            missing.append("address")
        if not first(place, "openingHoursText", "openingHours") and not audit_allows_hours_fallback(place):
            missing.append("openingHoursText")
    elif place_type == "venue":
        if not first(place, "address", "addressText"):
            missing.append("address")
        if not first(place, "openingHoursText", "openingHours"):
            missing.append("openingHoursText")
    elif place_type == "public_space":
        if not first(place, "address", "addressText", "positionText"):
            missing.append("position")
    elif place_type == "route":
        waypoints = place.get("waypoints")
        route_ready = bool(first(place, "routeStart") and first(place, "routeEnd"))
        if isinstance(waypoints, list) and len([p for p in waypoints if text(p)]) >= 2:
            route_ready = True
        if not route_ready:
            missing.append("route")
        if not first(place, "suggestedDuration", "durationText"):
            missing.append("suggestedDuration")
    return missing


def customer_place(place: dict[str, Any], locale: str, content_depth: str = "standard") -> dict[str, Any]:
    place_type = normalized_place_type(place)
    hours = first(place, "openingHoursText", "openingHours")
    if not hours and place_type == "business" and audit_allows_hours_fallback(place):
        hours = BUSINESS_HOURS_FALLBACK_EN if locale.startswith("en") else BUSINESS_HOURS_FALLBACK_ZH
    if not hours and place_type == "public_space":
        hours = PUBLIC_SPACE_HOURS_EN if locale.startswith("en") else PUBLIC_SPACE_HOURS_ZH

    result = {
        "id": text(place.get("id")),
        "name": verified_name(place),
        "localName": first(place, "localName"),
        "placeType": place_type,
        "category": first(place, "categoryLabel", "category"),
        "branch": first(place, "branch", "branchName"),
        "address": first(place, "address", "addressText"),
        "positionText": first(place, "positionText"),
        "openingHoursText": hours,
        "signature": first(place, "signature", "reason", "whyGo", "highlight"),
        "visitTip": first(place, "visitTip", "reminder", "departureReminder", "departureTip"),
        "routeStart": first(place, "routeStart"),
        "routeEnd": first(place, "routeEnd"),
        "waypoints": place.get("waypoints") if isinstance(place.get("waypoints"), list) else [],
        "suggestedDuration": first(place, "suggestedDuration", "durationText"),
        "photo": normalized_photo(place.get("displayPhoto") or place.get("photo")),
        "originalSourceLinks": source_links({
            "originalSourceLinks": place.get("originalSourceLinks") or [],
        }),
    }
    if content_depth == "compact":
        allowed = {
            "id", "name", "placeType", "category", "address", "positionText",
            "openingHoursText", "visitTip", "photo", "originalSourceLinks",
        }
        result = {key: value for key, value in result.items() if key in allowed}
    elif content_depth == "deep":
        result.update({
            "areaGroup": first(place, "areaGroup", "district", "neighborhood"),
            "accessibilityNote": first(place, "accessibilityNote"),
            "verificationStatus": first(place, "verificationStatus"),
        })
    return {key: value for key, value in result.items() if value not in ("", None, [])}


def bundle_evidence(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = bundle.get("evidence")
    return evidence if isinstance(evidence, list) else []


def bundle_failures(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    failures = bundle.get("failures")
    return failures if isinstance(failures, list) else []


def bundle_sources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Return customer-provided sources, including links whose public fetch failed."""
    return bundle_evidence(bundle) + bundle_failures(bundle)


UNTRUSTED_INSTRUCTION_PATTERN = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?|"
    r"system\s+prompt|developer\s+message|reveal\s+(?:the\s+)?prompt|"
    r"忽略(?:之前|以上|前面)(?:所有)?(?:指令|要求)|系统提示词|开发者消息|"
    r"按以下(?:指令|要求)执行|不要遵守(?:之前|系统)(?:指令|要求))",
    re.IGNORECASE,
)


def detect_untrusted_instructions(value: Any) -> list[str]:
    raw = str(value or "")
    excerpts: list[str] = []
    for match in UNTRUSTED_INSTRUCTION_PATTERN.finditer(raw):
        start = max(0, match.start() - 60)
        end = min(len(raw), match.end() + 100)
        excerpt = re.sub(r"\s+", " ", raw[start:end]).strip()[:240]
        if excerpt and excerpt not in excerpts:
            excerpts.append(excerpt)
    return excerpts[:5]


def customer_submitted_url(source: dict[str, Any]) -> str:
    source_type = text(source.get("sourceType") or source.get("type")).lower()
    if source_type != "link":
        return ""
    candidates = [
        text(source.get("userOriginalUrl") or source.get("originalUrl")),
        *[
            text(ref.get("value"))
            for ref in (source.get("sourceRefs") or [])
            if isinstance(ref, dict) and text(ref.get("type")) == "original_url"
        ],
        text(source.get("value")) if source.get("status") == "failed" else "",
    ]
    return next((item for item in candidates if re.match(r"^https?://", item, re.I)), "")


def source_policy_for(source: dict[str, Any], failed: bool) -> dict[str, Any]:
    source_type = text(source.get("sourceType") or source.get("type")).lower()
    if failed:
        access = "public_blocked" if source_type == "link" else "unavailable"
    elif source_type == "link":
        access = "public_readable"
    elif source_type in {"screenshot", "video", "saved_html", "html"}:
        access = "local_only"
    else:
        access = "submitted"
    can_support: list[str] = []
    if source_type == "link" and customer_submitted_url(source):
        can_support.append("original_url_submitted")
    if not failed and source_type == "link":
        can_support.append("page_content_observed")
    if not failed and source_type == "screenshot":
        can_support.append("original_image_preserved")
        if text(source.get("ocrStatus")) == "completed":
            can_support.append("ocr_text_observed")
    if not failed and source_type == "video":
        can_support.append("local_video_evidence")
    if not failed and source_type == "text":
        can_support.append("customer_submitted_text")
    content = "\n".join(
        text(source.get(key))
        for key in ("ocrText", "originalText", "combinedText", "sourceTitle")
        if text(source.get(key))
    )
    excerpts = detect_untrusted_instructions(content)
    supplied_policy = source.get("sourcePolicy") if isinstance(source.get("sourcePolicy"), dict) else {}
    cannot_prove = [
        "current_opening_status_without_fresh_public_check",
        "booking_or_ticket_availability",
        "future_accessibility_or_queue_conditions",
    ]
    if failed:
        cannot_prove.append("blocked_source_page_contents")
    if excerpts:
        cannot_prove.append("instructions_inside_external_content_are_authoritative")
    return {
        "version": SOURCE_POLICY_VERSION,
        "accessLevel": text(supplied_policy.get("accessLevel")) or access,
        "canSupport": merge_unique([*can_support, *(supplied_policy.get("canSupport") or [])]),
        "cannotProve": merge_unique([*cannot_prove, *(supplied_policy.get("cannotProve") or [])]),
        "untrustedInstructionsDetected": bool(excerpts or supplied_policy.get("untrustedInstructionsDetected")),
        **({
            "untrustedInstructionExcerpts": merge_unique([
                *excerpts, *(supplied_policy.get("untrustedInstructionExcerpts") or []),
            ])[:5]
        } if excerpts or supplied_policy.get("untrustedInstructionExcerpts") else {}),
    }


def source_fingerprint(source: dict[str, Any]) -> str:
    stable = copy.deepcopy(source)
    stable.pop("ledgerVersion", None)
    stable.pop("submittedAt", None)
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def rebuild_source_ledger(library: dict[str, Any]) -> None:
    bundles = [item for item in (library.get("bundles") or []) if isinstance(item, dict)]
    if not bundles:
        preserved = []
        for item in library.get("sources") or []:
            if not isinstance(item, dict) or not text(item.get("id")):
                continue
            source = copy.deepcopy(item)
            source.setdefault("ledgerVersion", 1)
            source.setdefault("mediaIds", [])
            source.setdefault("submittedAt", now_iso())
            source.setdefault("sourcePolicy", {
                "version": SOURCE_POLICY_VERSION,
                "accessLevel": "submitted",
                "canSupport": [],
                "cannotProve": [],
                "untrustedInstructionsDetected": False,
            })
            if isinstance(source.get("sourcePolicy"), dict):
                source["sourcePolicy"]["version"] = SOURCE_POLICY_VERSION
            preserved.append(source)
        library["sources"] = preserved
        return
    previous = {
        text(item.get("id")): item
        for item in (library.get("sources") or [])
        if isinstance(item, dict) and text(item.get("id"))
    }
    ledger: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue
        batch_id = text(bundle.get("bundleId")) or f"batch-{uuid.uuid4().hex[:12]}"
        bundle_destination = text(bundle.get("destination"))
        for index, raw in enumerate(bundle_sources(bundle)):
            if not isinstance(raw, dict):
                continue
            base_id = text(raw.get("sourceId") or raw.get("id")) or f"source-{index + 1}"
            source_id = base_id if base_id not in seen_ids else f"{batch_id}-{base_id}"
            seen_ids.add(source_id)
            failed = raw in bundle_failures(bundle) or text(raw.get("status")) == "failed"
            source_type = text(raw.get("sourceType") or raw.get("type")).lower()
            if source_type == "html":
                source_type = "saved_html"
            destination = source_destination(raw, bundle_destination)
            entity: dict[str, Any] = {
                "id": source_id,
                "ledgerVersion": 1,
                "batchId": batch_id,
                "group": text(raw.get("collectionGroup") or raw.get("group")) or "default",
                "type": source_type or "text",
                "status": "failed" if failed else "captured",
                "destinationKey": destination_key(destination),
                "destination": destination,
                "submittedAt": text(raw.get("extractedAt") or bundle.get("createdAt")) or now_iso(),
                "sourcePolicy": source_policy_for(raw, failed),
                "mediaIds": [],
                "evidence": copy.deepcopy(raw),
            }
            submitted_url = customer_submitted_url(raw)
            if submitted_url:
                entity["submittedUrl"] = submitted_url
            if text(raw.get("platform")):
                entity["platform"] = text(raw.get("platform"))
            if text(raw.get("platformItemId")):
                entity["platformItemId"] = text(raw.get("platformItemId"))
            if isinstance(raw.get("platformAccess"), dict):
                entity["platformAccess"] = copy.deepcopy(raw["platformAccess"])
            if failed:
                entity["failureReason"] = text(raw.get("reason"))
                if text(raw.get("failureCode")):
                    entity["failureCode"] = text(raw.get("failureCode"))
            old = previous.get(source_id)
            if old:
                old_without_version = copy.deepcopy(old)
                new_without_version = copy.deepcopy(entity)
                old_version = int(old_without_version.pop("ledgerVersion", 1) or 1)
                old_without_version.pop("submittedAt", None)
                old_without_version.pop("mediaIds", None)
                new_without_version.pop("ledgerVersion", None)
                new_without_version.pop("submittedAt", None)
                new_without_version.pop("mediaIds", None)
                entity["ledgerVersion"] = old_version + (old_without_version != new_without_version)
            ledger.append(entity)
    library["sources"] = ledger


def sha256_if_file(path_value: Any) -> str:
    path = Path(text(path_value))
    if not text(path_value) or not path.is_file() or path.is_symlink():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def media_id(source_id: str, role: str, path_value: str) -> str:
    raw = f"{source_id}|{role}|{path_value}"
    return f"media-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def rebuild_media_ledger(library: dict[str, Any]) -> None:
    previous_media = [
        copy.deepcopy(item) for item in (library.get("media") or [])
        if isinstance(item, dict) and text(item.get("id"))
    ]
    media: list[dict[str, Any]] = []
    for source in library.get("sources") or []:
        if not isinstance(source, dict):
            continue
        evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
        source_id = text(source.get("id"))
        source_media_ids: list[str] = []
        original_path = text(evidence.get("localPath"))
        source_type = text(source.get("type"))
        original_id = ""
        if original_path and source_type in {"screenshot", "video"}:
            original_id = media_id(source_id, "original", original_path)
            original_sha = text(evidence.get("originalSha256") or evidence.get("fileSha256")) or sha256_if_file(original_path)
            if original_sha:
                media.append({
                    "id": original_id,
                    "sourceId": source_id,
                    "kind": "video" if source_type == "video" else "image",
                    "role": "original",
                    "path": original_path,
                    "sha256": original_sha,
                    "immutableOriginal": True,
                    "createdAt": text(source.get("submittedAt")) or now_iso(),
                })
                source_media_ids.append(original_id)
        for platform_file in evidence.get("platformMediaFiles") or []:
            if not isinstance(platform_file, dict):
                continue
            platform_path = text(platform_file.get("path"))
            platform_sha = text(platform_file.get("sha256")) or sha256_if_file(platform_path)
            if not platform_path or not platform_sha:
                continue
            platform_id = media_id(source_id, "original", platform_path)
            mime_type = text(platform_file.get("mimeType"))
            media.append({
                "id": platform_id,
                "sourceId": source_id,
                "kind": "video" if mime_type.startswith("video/") else "image",
                "role": "original",
                "path": platform_path,
                "sha256": platform_sha,
                "immutableOriginal": True,
                "mimeType": mime_type,
                "createdAt": text(source.get("submittedAt")) or now_iso(),
            })
            source_media_ids.append(platform_id)
            platform_display_path = text(platform_file.get("displayPath"))
            platform_display_sha = text(platform_file.get("displaySha256")) or sha256_if_file(platform_display_path)
            if (
                platform_file.get("displayPhotoEligible") is True
                and platform_display_path
                and platform_display_sha
            ):
                display_id = media_id(source_id, "display_crop", platform_display_path)
                media.append({
                    "id": display_id,
                    "sourceId": source_id,
                    "kind": "image",
                    "role": "display_crop",
                    "path": platform_display_path,
                    "sha256": platform_display_sha,
                    "immutableOriginal": False,
                    "derivedFromMediaId": platform_id,
                    "displayPhotoScore": float(platform_file.get("displayPhotoScore", 0.0) or 0.0),
                    "displayPhotoEligible": True,
                    "crop": copy.deepcopy(platform_file.get("displayCrop") or {}),
                    "createdAt": text(source.get("submittedAt")) or now_iso(),
                })
                source_media_ids.append(display_id)
        display_photo = evidence.get("displayPhoto") if isinstance(evidence.get("displayPhoto"), dict) else {}
        display_path = text(display_photo.get("path"))
        if display_path and display_path != original_path:
            display_sha = sha256_if_file(display_path)
            if display_sha:
                display_id = media_id(source_id, "display_crop", display_path)
                item: dict[str, Any] = {
                    "id": display_id,
                    "sourceId": source_id,
                    "kind": "image",
                    "role": "display_crop",
                    "path": display_path,
                    "sha256": display_sha,
                    "immutableOriginal": False,
                    "displayPhotoScore": float(evidence.get("displayPhotoScore", display_photo.get("qualityScore", 0.0)) or 0.0),
                    "displayPhotoEligible": bool(evidence.get("displayPhotoEligible", True)),
                    "crop": copy.deepcopy(evidence.get("displayCrop") or {}),
                    "createdAt": text(source.get("submittedAt")) or now_iso(),
                }
                if original_id:
                    item["derivedFromMediaId"] = original_id
                media.append(item)
                source_media_ids.append(display_id)
        source["mediaIds"] = source_media_ids
    generated_ids = {text(item.get("id")) for item in media}
    active_source_ids = {text(item.get("id")) for item in library.get("sources") or []}
    media.extend(
        item for item in previous_media
        if text(item.get("id")) not in generated_ids
        and text(item.get("sourceId")) in active_source_ids
    )
    library["media"] = media
    media_by_source: dict[str, list[dict[str, Any]]] = {}
    for item in media:
        media_by_source.setdefault(text(item.get("sourceId")), []).append(item)
    for source in library.get("sources") or []:
        if isinstance(source, dict):
            source["mediaIds"] = merge_unique(
                [item.get("id") for item in media_by_source.get(text(source.get("id")), [])]
            )
    for place in library.get("places") or []:
        if not isinstance(place, dict):
            continue
        linked_media = [
            item
            for source_id in (place.get("sourceIds") or [])
            for item in media_by_source.get(text(source_id), [])
        ]
        place["mediaIds"] = merge_unique([item.get("id") for item in linked_media])
        eligible = [
            item for item in linked_media
            if item.get("role") == "display_crop" and item.get("displayPhotoEligible") is not False
        ]
        if eligible:
            best = sorted(eligible, key=lambda item: float(item.get("displayPhotoScore", 0.0)), reverse=True)[0]
            place["displayMediaId"] = best["id"]


def command_ingest(args: argparse.Namespace) -> None:
    bundle = read_json(args.evidence)
    if not isinstance(bundle, dict):
        raise ValueError("evidence bundle must be an object")
    bundle_id = text(bundle.get("bundleId")) or f"bundle-{uuid.uuid4().hex[:12]}"
    bundle["bundleId"] = bundle_id
    bundle.setdefault("createdAt", now_iso())
    bundle.setdefault("destination", "")
    bundle.setdefault("passportGenerated", False)
    digest = hashlib.sha256(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    operation_id = get_operation_id(args, f"ingest:{bundle_id}:{digest}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id):
            print(json.dumps({"status": "already_applied", "bundleId": bundle_id}, ensure_ascii=False))
            return
        library["bundles"] = [item for item in library["bundles"] if item.get("bundleId") != bundle_id]
        library["bundles"].append(bundle)
        source_ids = [
            text(item.get("sourceId") or item.get("id"))
            for item in bundle_sources(bundle)
            if isinstance(item, dict) and text(item.get("sourceId") or item.get("id"))
        ]
        batch = {
            "id": bundle_id,
            "destination": text(bundle.get("destination")),
            "sourceIds": merge_unique(source_ids),
            "createdAt": text(bundle.get("createdAt")) or now_iso(),
            "passportGeneratedDestinations": merge_unique(bundle.get("passportGeneratedDestinations") or []),
        }
        library["ingestBatches"] = [item for item in library["ingestBatches"] if item.get("id") != bundle_id]
        library["ingestBatches"].append(batch)
        append_event(library, operation_id, "source.ingest", bundle_id, after={"sourceIds": source_ids})
    destinations = sorted(
        destination_label(key)
        for key in bundle_destination_keys(bundle)
    )
    print(json.dumps({"status": "saved", "bundleId": bundle_id, "destinations": destinations}, ensure_ascii=False))


def command_list(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    destination = text(args.destination)
    requested_key = destination_key(destination) if destination else ""
    sources = [
        item for item in library["sources"]
        if not requested_key or text(item.get("destinationKey")) == requested_key
    ]
    places = [
        item for item in library["places"]
        if not requested_key or text(item.get("destinationKey")) == requested_key
    ]
    destinations = [
        item for item in library.get("destinations") or []
        if not requested_key or text(item.get("key")) == requested_key
    ]
    print(json.dumps({"destinations": destinations, "sources": sources, "places": places}, ensure_ascii=False, indent=2))


def command_present(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    destination = text(args.destination) or ("your trip" if args.locale.startswith("en") else "旅行")
    requested_key = destination_key(destination)
    sources = [
        source for source in library["sources"]
        if text(source.get("destinationKey")) == requested_key
    ]
    received = len(sources)
    failures = sum(1 for source in sources if text(source.get("status")) == "failed")
    if args.locale.startswith("en"):
        failed_note = f"{failures} source(s) could not be read yet, but the originals are preserved. " if failures else ""
        next_step = PRODUCT_CONFIG["copy"]["savedNextStepEn"].replace("{destination}", destination)
        message = f"Saved {received} item(s) to your {destination} want-to-go library. {failed_note}No passport was generated. {next_step}"
    else:
        failed_note = f"其中{failures}项暂时没有读到内容，原始来源仍已保留。" if failures else ""
        next_step = PRODUCT_CONFIG["copy"]["savedNextStepZh"].replace("{destination}", destination)
        message = f"已收进你的{destination}想去库：{received}项。{failed_note}这次只做收纳，没有生成护照。{next_step}"
    print(message)


def evidence_map(library: dict[str, Any], bundle_id: str) -> dict[str, dict[str, Any]]:
    for bundle in library["bundles"]:
        if text(bundle.get("bundleId")) != bundle_id:
            continue
        result: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(bundle_sources(bundle)):
            source_id = text(item.get("sourceId") or item.get("id")) or f"source-{index + 1}"
            result[source_id] = item
        return result
    raise ValueError("bundle not found")


def evidence_list(library: dict[str, Any], bundle_id: str) -> list[dict[str, Any]]:
    for bundle in library["bundles"]:
        if text(bundle.get("bundleId")) == bundle_id:
            return bundle_sources(bundle)
    raise ValueError("bundle not found")


def sources_for_selection(
    selection: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
    all_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only evidence explicitly linked to this place or sharing its non-default group."""
    raw_ids = selection.get("sourceIds") or []
    source_ids = [text(item) for item in raw_ids if text(item)] if isinstance(raw_ids, list) else []
    linked = [source_by_id[item] for item in source_ids if item in source_by_id]
    groups = {
        text(selection.get("sourceGroup") or selection.get("group")),
        *(
            text(item.get("collectionGroup") or item.get("group"))
            for item in linked
        ),
    }
    groups.discard("")
    groups.discard("default")
    linked_ids = {
        text(item.get("sourceId") or item.get("id"))
        for item in linked
    }
    for item in all_sources:
        group = text(item.get("collectionGroup") or item.get("group"))
        source_id = text(item.get("sourceId") or item.get("id"))
        if group in groups and source_id not in linked_ids:
            linked.append(item)
            linked_ids.add(source_id)
    return linked


def links_from_evidence(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for source in items:
        source_type = text(source.get("sourceType") or source.get("type")).lower()
        if source_type != "link":
            continue
        candidates = [
            text(source.get("userOriginalUrl") or source.get("originalUrl")),
            *[
                text(ref.get("value"))
                for ref in (source.get("sourceRefs") or [])
                if isinstance(ref, dict) and text(ref.get("type")) == "original_url"
            ],
            text(source.get("value")) if source.get("status") == "failed" else "",
        ]
        url = next((item for item in candidates if re.match(r"^https?://", item, re.I)), "")
        if url:
            links.append({"url": url, "label": "打开原始收藏链接"})
    return source_links({"originalSourceLinks": links})


def display_photo_from_sources(selection: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    selected = normalized_photo(selection.get("displayPhoto") or selection.get("photo"))
    if selected:
        return selected
    candidates: list[tuple[float, dict[str, Any]]] = []
    for source in items:
        if source.get("displayPhotoEligible") is False:
            continue
        for key in ("displayPhoto", "photo"):
            raw_photo = source.get(key)
            candidate = normalized_photo(raw_photo)
            if candidate:
                raw_score = (
                    raw_photo.get("qualityScore", source.get("displayPhotoScore", 0.5))
                    if isinstance(raw_photo, dict)
                    else source.get("displayPhotoScore", 0.5)
                )
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    score = 0.5
                candidates.append((score, candidate))
        for key in (
            "displayImagePath", "preparedImagePath", "photoPath", "imagePath",
            "localPath", "inputPath", "path",
        ):
            candidate = text(source.get(key))
            if candidate and re.search(r"\.(?:png|jpe?g|webp)$", candidate, re.I):
                focal = source.get("focalPoint") if isinstance(source.get("focalPoint"), dict) else None
                result: dict[str, Any] = {"path": candidate}
                if focal:
                    result["focalPoint"] = focal
                candidates.append((0.25, normalized_photo(result)))
                break
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def selection_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        raw = data.get("selections") or data.get("places") or []
        return [item for item in raw if isinstance(item, dict)]
    raise ValueError("selections must be a list or object")


def place_identity_key(place: dict[str, Any]) -> str:
    key = text(place.get("destinationKey")) or destination_key(place.get("destination"))
    name = normalized_token(verified_name(place) or first(place, "name", "displayName"))
    location = normalized_token(first(place, "address", "addressText", "branch", "positionText"))
    if normalized_place_type(place) == "route":
        location = normalized_token(
            "|".join(
                [
                    first(place, "routeStart"),
                    *[text(item) for item in (place.get("waypoints") or [])],
                    first(place, "routeEnd"),
                ]
            )
        )
    if key == PENDING_DESTINATION_KEY or not name or not location:
        return ""
    raw = f"{key}|{name}|{location}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def merge_unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = text(value)
        if item and item not in result:
            result.append(item)
    return result


def get_operation_id(args: argparse.Namespace, fallback: str) -> str:
    return text(getattr(args, "operation_id", "")) or fallback


def find_event_by_operation(library: dict[str, Any], operation_id: str) -> Optional[dict[str, Any]]:
    return next(
        (
            event for event in library.get("events") or []
            if isinstance(event, dict) and text(event.get("operationId")) == operation_id
        ),
        None,
    )


def append_event(
    library: dict[str, Any],
    operation_id: str,
    event_type: str,
    entity_id: str = "",
    before: Any = None,
    after: Any = None,
    undoable: bool = False,
) -> dict[str, Any]:
    existing = find_event_by_operation(library, operation_id)
    if existing:
        return existing
    event = {
        "id": f"event-{uuid.uuid4().hex[:12]}",
        "operationId": operation_id,
        "type": event_type,
        "createdAt": now_iso(),
        "undoable": bool(undoable),
    }
    if entity_id:
        event["entityId"] = entity_id
    if before is not None:
        event["before"] = copy.deepcopy(before)
    if after is not None:
        event["after"] = copy.deepcopy(after)
    library.setdefault("events", []).append(event)
    library["revision"] = int(library.get("revision", 0) or 0) + 1
    return event


def upsert_place(library: dict[str, Any], place: dict[str, Any], bundle_id: str) -> dict[str, Any]:
    identity = place_identity_key(place)
    place_id = text(place.get("id"))
    existing = next(
        (
            item
            for item in library["places"]
            if (place_id and text(item.get("id")) == place_id)
            or (identity and text(item.get("identityKey")) == identity)
        ),
        None,
    )
    if existing:
        merged = copy.deepcopy(existing)
        merged.update(place)
        merged["id"] = text(existing.get("id")) or place_id
        merged["createdAt"] = text(existing.get("createdAt")) or text(place.get("createdAt")) or now_iso()
        merged["sourceIds"] = merge_unique(
            [*(existing.get("sourceIds") or []), *(place.get("sourceIds") or [])]
        )
        merged["bundleIds"] = merge_unique(
            [
                *(existing.get("bundleIds") or []),
                existing.get("bundleId"),
                *(place.get("bundleIds") or []),
                bundle_id,
            ]
        )
        merged["originalSourceLinks"] = source_links(
            {
                "originalSourceLinks": [
                    *(existing.get("originalSourceLinks") or []),
                    *(place.get("originalSourceLinks") or []),
                ]
            }
        )
        place = merged
    else:
        place["bundleIds"] = merge_unique([*(place.get("bundleIds") or []), bundle_id])
        place.setdefault("createdAt", now_iso())
        place.setdefault("sortOrder", len(library["places"]))
    place.setdefault("mediaIds", [])
    place["identityKey"] = place_identity_key(place)
    place["updatedAt"] = now_iso()
    library["places"] = [item for item in library["places"] if text(item.get("id")) != text(place.get("id"))]
    library["places"].append(place)
    return place


def command_promote(args: argparse.Namespace) -> None:
    selections = selection_list(read_json(args.selections))
    digest = hashlib.sha256(
        json.dumps(selections, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    operation_id = get_operation_id(args, f"promote:{args.bundle_id}:{digest}")
    promoted: list[str] = []
    with library_transaction(args.library) as library:
        existing_event = find_event_by_operation(library, operation_id)
        if existing_event:
            prior = existing_event.get("after") if isinstance(existing_event.get("after"), dict) else {}
            print(json.dumps({"status": "already_applied", "placeIds": prior.get("placeIds", [])}, ensure_ascii=False))
            return
        source_by_id = evidence_map(library, args.bundle_id)
        all_bundle_sources = evidence_list(library, args.bundle_id)
        bundle_destination = next(
            (text(item.get("destination")) for item in library["bundles"] if item.get("bundleId") == args.bundle_id),
            "",
        )
        for selection in selections:
            name = first(selection, "verifiedName", "name", "displayName")
            if not name:
                raise ValueError("every selection needs a name")
            source_ids = selection.get("sourceIds") or []
            linked_sources = sources_for_selection(selection, source_by_id, all_bundle_sources)
            links = links_from_evidence(linked_sources)
            place = copy.deepcopy(selection)
            place["id"] = text(place.get("id")) or f"place-{uuid.uuid4().hex[:12]}"
            place["name"] = name
            place["bundleId"] = args.bundle_id
            explicit_destination = first(place, "destination")
            linked_destinations = merge_unique(
                [source_destination(source, bundle_destination) for source in linked_sources]
            )
            linked_keys = {
                destination_key(item)
                for item in linked_destinations
                if destination_key(item) != PENDING_DESTINATION_KEY
            }
            if len(linked_keys) > 1:
                raise ValueError(f"同一地点来源包含多个目的地：{', '.join(linked_destinations)}")
            explicit_key = destination_key(explicit_destination) if explicit_destination else ""
            if explicit_key and linked_keys and explicit_key not in linked_keys:
                raise ValueError(
                    f"地点“{name}”填写的目的地与来源不一致：{explicit_destination} / {', '.join(linked_destinations)}"
                )
            selected_destination = explicit_destination or next(iter(linked_destinations), "") or bundle_destination
            selected_key = destination_key(selected_destination)
            place["destination"] = "" if selected_key == PENDING_DESTINATION_KEY else destination_label(selected_key, selected_destination)
            place["destinationKey"] = selected_key
            place["destinationStatus"] = "needs_confirmation" if selected_key == PENDING_DESTINATION_KEY else "confirmed"
            place["placeType"] = normalized_place_type(place)
            place["sourceIds"] = source_ids
            place.pop("sourceLinks", None)
            place.pop("sources", None)
            place["originalSourceLinks"] = links
            display_photo = display_photo_from_sources(place, linked_sources)
            if display_photo:
                place["displayPhoto"] = display_photo
            place.setdefault("nameSource", "user_named")
            place.setdefault("nameRequiresConfirmation", place.get("nameSource") == "material_ocr")
            place = upsert_place(library, place, args.bundle_id)
            promoted.append(place["id"])
        append_event(library, operation_id, "place.promote", args.bundle_id, after={"placeIds": promoted})
    print(json.dumps({"status": "promoted", "placeIds": promoted}, ensure_ascii=False))


def command_passport(args: argparse.Namespace) -> None:
    destination = text(args.destination)
    requested_key = destination_key(destination)
    content_depth = text(getattr(args, "content_depth", "standard")) or "standard"
    if content_depth not in {"deep", "standard", "compact"}:
        raise ValueError("content depth must be deep, standard, or compact")
    visitor_mode = bool(getattr(args, "visitor_mode", False))
    delivered: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    operation_id = get_operation_id(args, f"passport:{requested_key}:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        candidates = sorted(
            [
                place for place in library["places"]
                if text(place.get("destinationKey")) == requested_key
                and text(place.get("destinationStatus")) == "confirmed"
            ],
            key=lambda place: (int(place.get("sortOrder", 0) or 0), text(place.get("name"))),
        )
        for place in candidates:
            missing = passport_missing_fields(place)
            if missing:
                retained.append({
                    "id": place.get("id"),
                    "name": verified_name(place) or first(place, "name", "displayName") or "未命名地点",
                    "missing": missing,
                })
            else:
                delivered.append(customer_place(place, args.locale, content_depth))
        if not delivered:
            labels = MISSING_FIELD_LABELS_EN if args.locale.startswith("en") else MISSING_FIELD_LABELS_ZH
            details = "; ".join(
                f"{item['name']} ({', '.join(labels.get(field, field) for field in item['missing'])})"
                for item in retained[:12]
            )
            if args.locale.startswith("en"):
                raise ValueError(
                    f"No deliverable places. Add the missing fields: {details}"
                    if details else
                    f"No places were found for destination: {destination or 'unspecified'}"
                )
            raise ValueError(
                f"没有可交付地点，请补全缺失字段：{details}"
                if details else
                f"没有找到目的地为“{destination or '未指定'}”的地点"
            )
        payload = {
            "schemaVersion": CONTRACT_VERSION,
            "locale": args.locale,
            "destination": destination,
            "title": f"Go passport · {destination}",
            "presentation": {
                "visitorMode": visitor_mode,
                "contentDepth": content_depth,
            },
            "places": delivered,
            "retainedClueCount": len(retained),
            "offers": {
                "free": PRODUCT_CONFIG["offers"]["free"],
                "manualItineraryBeta": PRODUCT_CONFIG["offers"]["manualItineraryBeta"],
            },
            "requestForm": {
                "method": PRODUCT_CONFIG["form"]["method"],
                "url": CTA_URL,
            },
        }
        write_json(args.output, payload)
        for bundle in library["bundles"]:
            if requested_key in bundle_destination_keys(bundle):
                generated = merge_unique([*(bundle.get("passportGeneratedDestinations") or []), requested_key])
                bundle["passportGeneratedDestinations"] = generated
                bundle["passportGenerated"] = bundle_destination_keys(bundle) == {requested_key}
        for batch in library.get("ingestBatches") or []:
            if text(batch.get("id")) in {
                text(bundle.get("bundleId")) for bundle in library["bundles"]
                if requested_key in bundle_destination_keys(bundle)
            }:
                batch["passportGeneratedDestinations"] = merge_unique(
                    [*(batch.get("passportGeneratedDestinations") or []), requested_key]
                )
        for collection in library.get("destinations") or []:
            if text(collection.get("key")) == requested_key:
                collection["passportGeneratedAt"] = now_iso()
        append_event(
            library, operation_id, "passport.generate", requested_key,
            after={"output": Path(args.output).name, "contentDepth": content_depth, "visitorMode": visitor_mode},
        )
    print(json.dumps({"status": "ready", "delivered": len(delivered), "retained": len(retained)}, ensure_ascii=False))


def command_onboarding(args: argparse.Namespace) -> None:
    key = "onboardingEn" if args.locale.startswith("en") else "onboardingZh"
    print(PRODUCT_CONFIG["copy"][key])


def read_patch(path: str) -> dict[str, Any]:
    patch = read_json(path)
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    unknown = sorted(set(patch) - EDITABLE_PLACE_FIELDS)
    if unknown:
        raise ValueError(f"patch contains non-editable fields: {', '.join(unknown)}")
    return patch


def replace_place(library: dict[str, Any], replacement: dict[str, Any]) -> None:
    place_id = text(replacement.get("id"))
    library["places"] = [
        copy.deepcopy(replacement) if text(item.get("id")) == place_id else item
        for item in library["places"]
    ]


def command_edit(args: argparse.Namespace) -> None:
    patch = read_patch(args.patch)
    operation_id = get_operation_id(args, f"edit:{args.place_id}:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        existing_event = find_event_by_operation(library, operation_id)
        if existing_event:
            print(json.dumps({"status": "already_applied", "placeId": args.place_id}, ensure_ascii=False))
            return
        place = next((item for item in library["places"] if text(item.get("id")) == args.place_id), None)
        if not place:
            raise ValueError("place not found")
        before = copy.deepcopy(place)
        place.update(copy.deepcopy(patch))
        if "destination" in patch:
            key = destination_key(place.get("destination"))
            place["destinationKey"] = key
            place["destinationStatus"] = "needs_confirmation" if key == PENDING_DESTINATION_KEY else "confirmed"
        place["placeType"] = normalized_place_type(place)
        place["identityKey"] = place_identity_key(place)
        place["updatedAt"] = now_iso()
        append_event(library, operation_id, "place.update", args.place_id, before, place, True)
    print(json.dumps({"status": "updated", "placeId": args.place_id}, ensure_ascii=False))


def command_delete(args: argparse.Namespace) -> None:
    operation_id = get_operation_id(args, f"delete:{args.place_id}:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id):
            print(json.dumps({"status": "already_applied", "placeId": args.place_id}, ensure_ascii=False))
            return
        place = next((item for item in library["places"] if text(item.get("id")) == args.place_id), None)
        if not place:
            raise ValueError("place not found")
        library["places"] = [item for item in library["places"] if text(item.get("id")) != args.place_id]
        tombstone = {
            "entityType": "place",
            "entityId": args.place_id,
            "deletedAt": now_iso(),
            "entity": copy.deepcopy(place),
        }
        library["tombstones"] = [
            item for item in library["tombstones"]
            if not (item.get("entityType") == "place" and text(item.get("entityId")) == args.place_id)
        ]
        library["tombstones"].append(tombstone)
        append_event(library, operation_id, "place.delete", args.place_id, place, None, True)
    print(json.dumps({"status": "deleted", "placeId": args.place_id, "recoverable": True}, ensure_ascii=False))


def command_restore(args: argparse.Namespace) -> None:
    operation_id = get_operation_id(args, f"restore:{args.place_id}:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id):
            print(json.dumps({"status": "already_applied", "placeId": args.place_id}, ensure_ascii=False))
            return
        if any(text(item.get("id")) == args.place_id for item in library["places"]):
            raise ValueError("place is already active")
        tombstone = next(
            (
                item for item in reversed(library["tombstones"])
                if item.get("entityType") == "place" and text(item.get("entityId")) == args.place_id
            ),
            None,
        )
        if not tombstone or not isinstance(tombstone.get("entity"), dict):
            raise ValueError("deleted place not found")
        restored = copy.deepcopy(tombstone["entity"])
        restored["updatedAt"] = now_iso()
        library["places"].append(restored)
        library["tombstones"].remove(tombstone)
        append_event(library, operation_id, "place.restore", args.place_id, tombstone, restored, True)
    print(json.dumps({"status": "restored", "placeId": args.place_id}, ensure_ascii=False))


def command_reorder(args: argparse.Namespace) -> None:
    raw = read_json(args.order)
    place_ids = raw.get("placeIds") if isinstance(raw, dict) else raw
    if not isinstance(place_ids, list) or not place_ids or any(not text(item) for item in place_ids):
        raise ValueError("order must be a non-empty placeIds array")
    ordered_ids = [text(item) for item in place_ids]
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError("placeIds must be unique")
    requested_key = destination_key(args.destination)
    operation_id = get_operation_id(args, f"reorder:{requested_key}:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id):
            print(json.dumps({"status": "already_applied", "destinationKey": requested_key}, ensure_ascii=False))
            return
        matching = [
            item for item in library["places"]
            if text(item.get("destinationKey")) == requested_key
        ]
        matching_ids = {text(item.get("id")) for item in matching}
        if set(ordered_ids) != matching_ids:
            raise ValueError("placeIds must include every active place in the destination exactly once")
        before = {text(item.get("id")): int(item.get("sortOrder", 0) or 0) for item in matching}
        for index, place_id in enumerate(ordered_ids):
            place = next(item for item in matching if text(item.get("id")) == place_id)
            place["sortOrder"] = index
            place["updatedAt"] = now_iso()
        after = {place_id: index for index, place_id in enumerate(ordered_ids)}
        append_event(library, operation_id, "place.reorder", requested_key, before, after, True)
    print(json.dumps({"status": "reordered", "destinationKey": requested_key, "placeIds": ordered_ids}, ensure_ascii=False))


def command_undo(args: argparse.Namespace) -> None:
    operation_id = get_operation_id(args, f"undo:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id):
            print(json.dumps({"status": "already_applied"}, ensure_ascii=False))
            return
        requested_event = text(getattr(args, "event_id", ""))
        event = next(
            (
                item for item in reversed(library["events"])
                if item.get("undoable") is True
                and not text(item.get("undoneAt"))
                and (not requested_event or text(item.get("id")) == requested_event)
            ),
            None,
        )
        if not event:
            raise ValueError("no undoable event found")
        event_type = text(event.get("type"))
        entity_id = text(event.get("entityId"))
        if event_type == "place.update":
            if not isinstance(event.get("before"), dict):
                raise ValueError("undo data is missing")
            replace_place(library, event["before"])
        elif event_type == "place.delete":
            if not isinstance(event.get("before"), dict):
                raise ValueError("undo data is missing")
            library["places"].append(copy.deepcopy(event["before"]))
            library["tombstones"] = [
                item for item in library["tombstones"]
                if not (item.get("entityType") == "place" and text(item.get("entityId")) == entity_id)
            ]
        elif event_type == "place.restore":
            library["places"] = [item for item in library["places"] if text(item.get("id")) != entity_id]
            if isinstance(event.get("before"), dict):
                library["tombstones"].append(copy.deepcopy(event["before"]))
        elif event_type == "place.reorder":
            before = event.get("before") if isinstance(event.get("before"), dict) else {}
            for place in library["places"]:
                place_id = text(place.get("id"))
                if place_id in before:
                    place["sortOrder"] = int(before[place_id])
        else:
            raise ValueError("event type cannot be undone")
        event["undoneAt"] = now_iso()
        append_event(library, operation_id, "event.undo", text(event.get("id")), after={"undid": event.get("id")})
    print(json.dumps({"status": "undone", "eventId": event.get("id"), "eventType": event_type}, ensure_ascii=False))


def command_migrate(args: argparse.Namespace) -> None:
    target = Path(args.library)
    raw = read_json(target) if target.exists() and target.stat().st_size else empty_library()
    if not isinstance(raw, dict):
        raise ValueError("library must be a JSON object")
    migrated, changes = migrate_library_data(raw)
    report = {
        "status": "migration_needed" if changes else "current",
        "fromVersion": text(raw.get("schemaVersion")) or "unversioned",
        "toVersion": CONTRACT_VERSION,
        "changes": changes,
        "dryRun": bool(args.dry_run),
    }
    if not args.dry_run:
        output = Path(args.output) if text(args.output) else target
        append_event(
            migrated,
            get_operation_id(args, f"migrate:{report['fromVersion']}->{CONTRACT_VERSION}"),
            "library.migrate",
            text(migrated.get("id")),
            after={"changes": changes},
        )
        if output == target:
            save_library(output, migrated)
        else:
            atomic_write_json(output, migrated)
        report["output"] = str(output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def repair_library(library: dict[str, Any]) -> tuple[list[str], list[str]]:
    fixes: list[str] = []
    blockers: list[str] = []
    for item in library.get("media") or []:
        if not isinstance(item, dict) or item.get("role") != "original":
            continue
        current = sha256_if_file(item.get("path"))
        if current and current != text(item.get("sha256")):
            blockers.append(f"original_media_hash_mismatch:{item.get('id')}")
    before_destinations = json.dumps(library.get("destinations") or [], ensure_ascii=False, sort_keys=True)
    before_sources = json.dumps(library.get("sources") or [], ensure_ascii=False, sort_keys=True)
    rebuild_source_ledger(library)
    rebuild_media_ledger(library)
    rebuild_destinations(library)
    if before_sources != json.dumps(library.get("sources") or [], ensure_ascii=False, sort_keys=True):
        fixes.append("rebuilt_source_and_media_ledgers")
    if before_destinations != json.dumps(library.get("destinations") or [], ensure_ascii=False, sort_keys=True):
        fixes.append("rebuilt_destination_index")
    active_ids = {text(item.get("id")) for item in library.get("places") or []}
    duplicate_ids = sorted({item for item in active_ids if sum(text(place.get("id")) == item for place in library["places"]) > 1})
    blockers.extend(f"duplicate_place_id:{item}" for item in duplicate_ids)
    return fixes, blockers


def command_repair(args: argparse.Namespace) -> None:
    if args.dry_run:
        library = load_library(args.library)
        fixes, blockers = repair_library(library)
    else:
        with library_transaction(args.library) as library:
            fixes, blockers = repair_library(library)
            if blockers:
                raise ValueError("repair blocked: " + ", ".join(blockers))
            append_event(
                library,
                get_operation_id(args, f"repair:{uuid.uuid4().hex[:12]}"),
                "library.repair",
                text(library.get("id")),
                after={"fixes": fixes},
            )
    print(json.dumps({"status": "blocked" if blockers else "ready", "dryRun": bool(args.dry_run), "fixes": fixes, "blockers": blockers}, ensure_ascii=False))


def validate_library_contract(library: dict[str, Any]) -> list[str]:
    schema = read_json(CONTRACT_PATH)
    issues: list[str] = []
    required = set(schema.get("required") or [])
    allowed = set((schema.get("properties") or {}).keys())
    missing = sorted(required - set(library))
    unknown = sorted(set(library) - allowed)
    issues.extend(f"missing_root_field:{item}" for item in missing)
    issues.extend(f"unknown_root_field:{item}" for item in unknown)
    if text(library.get("schemaVersion")) != CONTRACT_VERSION:
        issues.append("wrong_schema_version")
    ids_by_kind: dict[str, set[str]] = {}
    for kind in (
        "destinations", "places", "sources", "media", "verificationSnapshots",
        "tripRequests", "events",
    ):
        values = library.get(kind)
        if not isinstance(values, list):
            issues.append(f"not_array:{kind}")
            continue
        ids = [text(item.get("id")) for item in values if isinstance(item, dict)]
        if any(not item for item in ids) or len(ids) != len(values):
            issues.append(f"missing_entity_id:{kind}")
        if len(ids) != len(set(ids)):
            issues.append(f"duplicate_entity_id:{kind}")
        ids_by_kind[kind] = set(ids)
    source_ids = ids_by_kind.get("sources", set())
    media_ids = ids_by_kind.get("media", set())
    place_ids = ids_by_kind.get("places", set())
    for place in library.get("places") or []:
        if not isinstance(place, dict):
            issues.append("invalid_place")
            continue
        for source_id in place.get("sourceIds") or []:
            if text(source_id) not in source_ids:
                issues.append(f"dangling_place_source:{place.get('id')}:{source_id}")
        for item in place.get("mediaIds") or []:
            if text(item) not in media_ids:
                issues.append(f"dangling_place_media:{place.get('id')}:{item}")
    for source in library.get("sources") or []:
        if not isinstance(source, dict):
            issues.append("invalid_source")
            continue
        if text(source.get("type")) == "link" and not re.match(r"^https?://", text(source.get("submittedUrl")), re.I):
            issues.append(f"link_missing_submitted_url:{source.get('id')}")
        for item in source.get("mediaIds") or []:
            if text(item) not in media_ids:
                issues.append(f"dangling_source_media:{source.get('id')}:{item}")
    for media_item in library.get("media") or []:
        if isinstance(media_item, dict) and text(media_item.get("sourceId")) not in source_ids:
            issues.append(f"dangling_media_source:{media_item.get('id')}")
    for request in library.get("tripRequests") or []:
        if not isinstance(request, dict):
            issues.append("invalid_trip_request")
            continue
        request_id = text(request.get("id"))
        offer_id = text(request.get("offerId"))
        if offer_id not in {"pre-trip-review", PUBLIC_OFFER_ID}:
            issues.append(f"invalid_trip_request_offer:{request_id}")
        if offer_id == "pre-trip-review" and request.get("legacyImported") is not True:
            issues.append(f"public_standalone_review_request:{request_id}")
        allowed_statuses = {"draft", *REQUEST_WORKFLOW_STAGES, "declined", "cancelled"}
        request_status = text(request.get("status"))
        if request_status not in allowed_statuses:
            issues.append(f"invalid_trip_request_status:{request_id}")
        if offer_id == PUBLIC_OFFER_ID and request.get("legacyImported") is not True:
            limits = PRODUCT_CONFIG["offers"]["manualItineraryBeta"]["limits"]
            if request.get("days") not in (None, ""):
                days = int(request.get("days", 0) or 0)
                if not limits["daysMin"] <= days <= limits["daysMax"]:
                    issues.append(f"invalid_trip_request_days:{request_id}")
            if request.get("placeIds"):
                count = len(request.get("placeIds") or [])
                if not limits["placesMin"] <= count <= limits["placesMax"]:
                    issues.append(f"invalid_trip_request_places:{request_id}")
        ordered_statuses = ["draft", *REQUEST_WORKFLOW_STAGES]
        if request.get("legacyImported") is not True and request_status in ordered_statuses:
            for stage, fields in REQUEST_STAGE_REQUIREMENTS.items():
                if ordered_statuses.index(request_status) >= ordered_statuses.index(stage):
                    for field in fields:
                        if not text(request.get(field)):
                            issues.append(f"missing_trip_request_stage_field:{request_id}:{field}")
        for place_id in request.get("placeIds") or []:
            if text(place_id) not in place_ids:
                issues.append(f"dangling_trip_request_place:{request.get('id')}:{place_id}")
    trip_request_ids = ids_by_kind.get("tripRequests", set())
    for snapshot in library.get("verificationSnapshots") or []:
        if not isinstance(snapshot, dict):
            issues.append("invalid_verification_snapshot")
            continue
        snapshot_id = text(snapshot.get("id"))
        trigger = text(snapshot.get("trigger"))
        if trigger not in {"agreed_date_once", "pre_trip_on_demand_legacy"}:
            issues.append(f"invalid_review_trigger:{snapshot_id}")
        if trigger == "pre_trip_on_demand_legacy":
            if snapshot.get("legacyImported") is not True:
                issues.append(f"legacy_review_not_marked:{snapshot_id}")
        else:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text(snapshot.get("agreedReviewDate"))):
                issues.append(f"missing_agreed_review_date:{snapshot_id}")
            if snapshot.get("agreementConfirmed") is not True:
                issues.append(f"review_agreement_not_confirmed:{snapshot_id}")
            service_context = text(snapshot.get("serviceContext"))
            if service_context not in {"manual_itinerary_beta", "standalone_non_public"}:
                issues.append(f"invalid_review_service_context:{snapshot_id}")
            request_id = text(snapshot.get("tripRequestId"))
            if service_context == "manual_itinerary_beta" and request_id not in trip_request_ids:
                issues.append(f"dangling_review_trip_request:{snapshot_id}:{request_id}")
    return sorted(set(issues))


def command_validate(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    issues = validate_library_contract(library)
    if issues:
        raise ValueError("contract validation failed: " + ", ".join(issues))
    print(json.dumps({"status": "valid", "contractVersion": CONTRACT_VERSION, "revision": library["revision"]}, ensure_ascii=False))


def review_diff(previous: Optional[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old_by_place = {
        text(item.get("placeId")): item
        for item in ((previous or {}).get("items") or [])
        if isinstance(item, dict)
    }
    changes: list[dict[str, Any]] = []
    for item in items:
        place_id = text(item.get("placeId"))
        old_facts = old_by_place.get(place_id, {}).get("facts") or {}
        new_facts = item.get("facts") or {}
        if not isinstance(old_facts, dict) or not isinstance(new_facts, dict):
            continue
        for field in sorted(set(old_facts) | set(new_facts)):
            if old_facts.get(field) != new_facts.get(field):
                changes.append({"placeId": place_id, "field": field, "before": old_facts.get(field), "after": new_facts.get(field)})
    return changes


def command_review(args: argparse.Namespace) -> None:
    raw = read_json(args.checks)
    if not isinstance(raw, dict):
        raise ValueError("checks must be an object with agreed review details")
    agreed_review_date = text(raw.get("agreedReviewDate"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", agreed_review_date):
        raise ValueError("checks need agreedReviewDate in YYYY-MM-DD format")
    if raw.get("agreementConfirmed") is not True:
        raise ValueError("checks need agreementConfirmed: true")
    service_context = text(raw.get("serviceContext"))
    if service_context not in {"manual_itinerary_beta", "standalone_non_public"}:
        raise ValueError("checks need a supported non-monitoring serviceContext")
    trip_request_id = text(raw.get("tripRequestId"))
    if service_context == "manual_itinerary_beta" and not trip_request_id:
        raise ValueError("manual itinerary review needs tripRequestId")
    checked_at = text(raw.get("checkedAt"))
    if not checked_at:
        raise ValueError("checks need checkedAt for the agreed review date")
    if checked_at[:10] != agreed_review_date:
        raise ValueError("checkedAt must be on agreedReviewDate")
    items = raw.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("checks must contain an items array")
    requested_key = destination_key(args.destination)
    for item in items:
        if not text(item.get("placeId")):
            raise ValueError("every review item needs placeId")
        item.setdefault("accessLevel", "public_readable")
        item.setdefault("canSupport", [])
        item.setdefault("cannotProve", [])
        item.setdefault("facts", {})
    snapshot: dict[str, Any] = {}
    with library_transaction(args.library) as library:
        if service_context == "manual_itinerary_beta":
            request = next(
                (item for item in library["tripRequests"] if text(item.get("id")) == trip_request_id),
                None,
            )
            if not request or text(request.get("offerId")) != PUBLIC_OFFER_ID:
                raise ValueError("tripRequestId must reference the manual itinerary beta")
            if text(request.get("destinationKey")) != requested_key:
                raise ValueError("tripRequestId belongs to another destination")
            if text(request.get("status")) not in {"payment_recorded", "in_delivery", "completed"}:
                raise ValueError("the manual service must reach payment_recorded before review")
            if any(text(item.get("tripRequestId")) == trip_request_id for item in library["verificationSnapshots"]):
                raise ValueError("the included agreed-date review has already been recorded")
        valid_place_ids = {
            text(place.get("id")) for place in library["places"]
            if text(place.get("destinationKey")) == requested_key
        }
        unknown = sorted({text(item.get("placeId")) for item in items} - valid_place_ids)
        if unknown:
            raise ValueError("review contains places outside the destination: " + ", ".join(unknown))
        previous = next(
            (
                item for item in reversed(library["verificationSnapshots"])
                if text(item.get("destinationKey")) == requested_key
            ),
            None,
        )
        changes = review_diff(previous, items)
        snapshot = {
            "id": f"verification-{uuid.uuid4().hex[:12]}",
            "destinationKey": requested_key,
            "trigger": "agreed_date_once",
            "agreedReviewDate": agreed_review_date,
            "agreementConfirmed": True,
            "serviceContext": service_context,
            "checkedAt": checked_at,
            "sourcePolicyVersion": SOURCE_POLICY_VERSION,
            "items": copy.deepcopy(items),
            "changes": changes,
            "summary": text(raw.get("summary")),
        }
        if trip_request_id:
            snapshot["tripRequestId"] = trip_request_id
        library["verificationSnapshots"].append(snapshot)
        append_event(
            library,
            get_operation_id(args, f"review:{snapshot['id']}"),
            "verification.capture",
            snapshot["id"],
            after={"destinationKey": requested_key, "changeCount": len(changes)},
        )
    if text(getattr(args, "output", "")):
        atomic_write_json(args.output, snapshot)
    print(json.dumps({"status": "reviewed", "snapshotId": snapshot["id"], "changes": snapshot["changes"]}, ensure_ascii=False, indent=2))


def command_trip_request(args: argparse.Namespace) -> None:
    request = read_json(args.request)
    if not isinstance(request, dict):
        raise ValueError("trip request must be an object")
    offer_id = text(request.get("offerId"))
    if offer_id != PUBLIC_OFFER_ID:
        raise ValueError("standalone pre-trip review is not a public request option")
    place_ids = merge_unique(request.get("placeIds") or [])
    destination = text(request.get("destination"))
    destination_key_value = text(request.get("destinationKey")) or destination_key(destination)
    limits = PRODUCT_CONFIG["offers"]["manualItineraryBeta"]["limits"]
    days = int(request.get("days", 0) or 0)
    if not limits["daysMin"] <= days <= limits["daysMax"]:
        raise ValueError(
            f"manual itinerary beta requires {limits['daysMin']}–{limits['daysMax']} days"
        )
    if not limits["placesMin"] <= len(place_ids) <= limits["placesMax"]:
        raise ValueError(
            f"manual itinerary beta requires {limits['placesMin']}–{limits['placesMax']} places"
        )
    status = text(request.get("status")) or "draft"
    allowed_statuses = {"draft", *REQUEST_WORKFLOW_STAGES, "declined", "cancelled"}
    if status not in allowed_statuses:
        raise ValueError("unsupported trip request status")
    entity = {
        "id": text(request.get("id")) or f"trip-request-{uuid.uuid4().hex[:12]}",
        "offerId": offer_id,
        "destinationKey": destination_key_value,
        "status": status,
        "createdAt": text(request.get("createdAt")) or now_iso(),
        "updatedAt": now_iso(),
    }
    for key in (
        "startDate", "days", "agreedReviewDate", "deliveryDueAt", "scopeConfirmedAt",
        "customerConfirmedAt", "paymentInstructionsSentAt", "paymentRecordedAt",
        "deliveryStartedAt", "contact", "notes",
    ):
        if request.get(key) not in (None, ""):
            entity[key] = request[key]
    if place_ids:
        entity["placeIds"] = place_ids
    operation_id = get_operation_id(args, f"trip-request:{entity['id']}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id):
            print(json.dumps({"status": "already_applied", "tripRequestId": entity["id"]}, ensure_ascii=False))
            return
        if place_ids:
            known = {text(place.get("id")) for place in library["places"] if text(place.get("destinationKey")) == destination_key_value}
            if set(place_ids) - known:
                raise ValueError("trip request contains unknown or cross-destination places")
        existing = next(
            (item for item in library["tripRequests"] if text(item.get("id")) == entity["id"]),
            None,
        )
        if existing:
            merged_entity = copy.deepcopy(existing)
            merged_entity.update(entity)
            merged_entity["createdAt"] = text(existing.get("createdAt")) or entity["createdAt"]
            entity = merged_entity
            previous_status = text(existing.get("status"))
            if status not in {previous_status, "declined", "cancelled"}:
                ordered = ["draft", *REQUEST_WORKFLOW_STAGES]
                if previous_status not in ordered or status not in ordered:
                    raise ValueError("unsupported trip request status transition")
                if ordered.index(status) != ordered.index(previous_status) + 1:
                    raise ValueError("trip request stages cannot be skipped or reversed")
        elif status not in {"draft", "submitted"}:
            raise ValueError("a new trip request must start as draft or submitted")
        ordered = ["draft", *REQUEST_WORKFLOW_STAGES]
        if status in ordered:
            for stage, fields in REQUEST_STAGE_REQUIREMENTS.items():
                if ordered.index(status) >= ordered.index(stage):
                    missing = [field for field in fields if not text(entity.get(field))]
                    if missing:
                        raise ValueError(f"{stage} requires: {', '.join(missing)}")
        library["tripRequests"] = [item for item in library["tripRequests"] if text(item.get("id")) != entity["id"]]
        library["tripRequests"].append(entity)
        append_event(library, operation_id, "trip_request.save", entity["id"], after={"offerId": offer_id, "status": entity["status"]})
    print(json.dumps({"status": "saved", "tripRequestId": entity["id"], "externalPostPerformed": False}, ensure_ascii=False))


def iter_text_payloads(path: Path) -> Iterator[tuple[str, str]]:
    allowed = {".md", ".json", ".py", ".mjs", ".js", ".html", ".yaml", ".yml", ".ps1", ".swift", ".txt"}
    if path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_file() and not item.is_symlink() and item.suffix.lower() in allowed:
                yield str(item), item.read_text(encoding="utf-8", errors="replace")
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if Path(name).suffix.lower() in allowed:
                    yield name, archive.read(name).decode("utf-8", errors="replace")
    elif path.suffix.lower() in allowed:
        yield str(path), path.read_text(encoding="utf-8", errors="replace")


def command_scan(args: argparse.Namespace) -> None:
    target = Path(args.path)
    if not target.exists():
        raise ValueError("scan path does not exist")
    issues: list[dict[str, str]] = []
    bad_name_pattern = re.compile(r"(?:^|/)(?:\.DS_Store|__pycache__|[^/]+\.(?:tmp|temp|lock))$")
    if target.is_dir():
        for item in target.rglob("*"):
            relative = item.relative_to(target).as_posix()
            if bad_name_pattern.search(relative):
                issues.append({"file": relative, "type": "forbidden_artifact"})
    elif target.suffix.lower() == ".zip":
        with zipfile.ZipFile(target) as archive:
            for name in archive.namelist():
                if bad_name_pattern.search(name):
                    issues.append({"file": name, "type": "forbidden_artifact"})
    for name, content in iter_text_payloads(target):
        scan_content = re.sub(
            r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+",
            "[embedded-image]",
            content,
        )
        if SECRET_PATTERN.search(scan_content):
            issues.append({"file": name, "type": "possible_secret"})
        if args.mode == "customer" and CUSTOMER_INTERNAL_PATTERN.search(scan_content):
            issues.append({"file": name, "type": "customer_internal_leak"})
    if issues:
        raise ValueError("scan failed: " + json.dumps(issues, ensure_ascii=False))
    print(json.dumps({"status": "clean", "mode": args.mode, "path": str(target)}, ensure_ascii=False))


def command_export(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    write_json(args.output, library)
    print(json.dumps({"status": "exported"}, ensure_ascii=False))


def command_share(args: argparse.Namespace) -> None:
    print(json.dumps({"status": "local_only", "message": "Use the rendered HTML file for sharing."}, ensure_ascii=False))


def command_confirm(args: argparse.Namespace) -> None:
    candidate = read_json(args.candidate_file)
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    forbidden = sorted(set(candidate) - EDITABLE_PLACE_FIELDS)
    if forbidden:
        raise ValueError(f"candidate contains non-editable fields: {', '.join(forbidden)}")
    operation_id = get_operation_id(args, f"confirm:{args.place_id}:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id):
            print(json.dumps({"status": "already_applied", "placeId": args.place_id}, ensure_ascii=False))
            return
        place = next((item for item in library["places"] if text(item.get("id")) == args.place_id), None)
        if not place:
            raise ValueError("place not found")
        before = copy.deepcopy(place)
        place.update(candidate)
        place["nameRequiresConfirmation"] = False
        place.setdefault("nameSource", "user_named")
        place["identityKey"] = place_identity_key(place)
        place["updatedAt"] = now_iso()
        append_event(library, operation_id, "place.update", args.place_id, before, place, True)
    print(json.dumps({"status": "confirmed", "placeId": args.place_id}, ensure_ascii=False))


def command_resolve(args: argparse.Namespace) -> None:
    candidates = read_json(args.candidates)
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    sourced = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        evidence = bool(
            text(item.get("candidateSource"))
            or text(item.get("providerPlaceId"))
            or re.match(r"^https?://", text(item.get("sourceUrl")), re.I)
        )
        if not evidence:
            continue
        candidate = copy.deepcopy(item)
        candidate["disambiguationKey"] = normalized_token(
            "|".join(
                text(candidate.get(key))
                for key in ("name", "branch", "address", "providerPlaceId")
            )
        )
        candidate["canSupport"] = merge_unique(candidate.get("canSupport") or ["candidate_identity"])
        candidate["cannotProve"] = merge_unique(
            candidate.get("cannotProve") or ["same_name_means_same_branch"]
        )
        sourced.append(candidate)
    unique_keys = {text(item.get("disambiguationKey")) for item in sourced if text(item.get("disambiguationKey"))}
    status = "resolved" if len(sourced) == 1 and len(unique_keys) == 1 else "needs_confirmation"
    print(json.dumps({"status": status, "candidates": sourced}, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="want_to_go.py")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--library", required=True)
    ingest.add_argument("--evidence", required=True)
    ingest.add_argument("--operation-id", default="")
    ingest.set_defaults(func=command_ingest)

    listing = sub.add_parser("list")
    listing.add_argument("--library", required=True)
    listing.add_argument("--destination", default="")
    listing.set_defaults(func=command_list)

    present = sub.add_parser("present")
    present.add_argument("--library", required=True)
    present.add_argument("--destination", required=True)
    present.add_argument("--locale", default="zh-CN")
    present.set_defaults(func=command_present)

    promote = sub.add_parser("promote")
    promote.add_argument("--library", required=True)
    promote.add_argument("--bundle-id", required=True)
    promote.add_argument("--selections", required=True)
    promote.add_argument("--operation-id", default="")
    promote.set_defaults(func=command_promote)

    passport = sub.add_parser("passport")
    passport.add_argument("--library", required=True)
    passport.add_argument("--destination", required=True)
    passport.add_argument("--output", required=True)
    passport.add_argument("--locale", default="zh-CN")
    passport.add_argument("--content-depth", choices=("deep", "standard", "compact"), default="standard")
    passport.add_argument("--visitor-mode", action="store_true")
    passport.add_argument("--operation-id", default="")
    passport.set_defaults(func=command_passport)

    confirm = sub.add_parser("confirm")
    confirm.add_argument("--library", required=True)
    confirm.add_argument("--place-id", required=True)
    confirm.add_argument("--candidate-file", required=True)
    confirm.add_argument("--operation-id", default="")
    confirm.set_defaults(func=command_confirm)

    edit = sub.add_parser("edit")
    edit.add_argument("--library", required=True)
    edit.add_argument("--place-id", required=True)
    edit.add_argument("--patch", required=True)
    edit.add_argument("--operation-id", default="")
    edit.set_defaults(func=command_edit)

    delete = sub.add_parser("delete")
    delete.add_argument("--library", required=True)
    delete.add_argument("--place-id", required=True)
    delete.add_argument("--operation-id", default="")
    delete.set_defaults(func=command_delete)

    restore = sub.add_parser("restore")
    restore.add_argument("--library", required=True)
    restore.add_argument("--place-id", required=True)
    restore.add_argument("--operation-id", default="")
    restore.set_defaults(func=command_restore)

    reorder = sub.add_parser("reorder")
    reorder.add_argument("--library", required=True)
    reorder.add_argument("--destination", required=True)
    reorder.add_argument("--order", required=True)
    reorder.add_argument("--operation-id", default="")
    reorder.set_defaults(func=command_reorder)

    undo = sub.add_parser("undo")
    undo.add_argument("--library", required=True)
    undo.add_argument("--event-id", default="")
    undo.add_argument("--operation-id", default="")
    undo.set_defaults(func=command_undo)

    resolve = sub.add_parser("resolve")
    resolve.add_argument("--candidates", required=True)
    resolve.set_defaults(func=command_resolve)

    migrate = sub.add_parser("migrate")
    migrate.add_argument("--library", required=True)
    migrate.add_argument("--output", default="")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--operation-id", default="")
    migrate.set_defaults(func=command_migrate)

    repair = sub.add_parser("repair")
    repair.add_argument("--library", required=True)
    repair.add_argument("--dry-run", action="store_true")
    repair.add_argument("--operation-id", default="")
    repair.set_defaults(func=command_repair)

    validate = sub.add_parser("validate")
    validate.add_argument("--library", required=True)
    validate.set_defaults(func=command_validate)

    review = sub.add_parser("review")
    review.add_argument("--library", required=True)
    review.add_argument("--destination", required=True)
    review.add_argument("--checks", required=True)
    review.add_argument("--output", default="")
    review.add_argument("--operation-id", default="")
    review.set_defaults(func=command_review)

    trip_request = sub.add_parser("trip-request")
    trip_request.add_argument("--library", required=True)
    trip_request.add_argument("--request", required=True)
    trip_request.add_argument("--operation-id", default="")
    trip_request.set_defaults(func=command_trip_request)

    scan = sub.add_parser("scan")
    scan.add_argument("--path", required=True)
    scan.add_argument("--mode", choices=("package", "customer"), default="package")
    scan.set_defaults(func=command_scan)

    export = sub.add_parser("export")
    export.add_argument("--library", required=True)
    export.add_argument("--output", required=True)
    export.set_defaults(func=command_export)

    share = sub.add_parser("share")
    share.set_defaults(func=command_share)

    onboarding = sub.add_parser("onboarding")
    onboarding.add_argument("--locale", default="zh-CN")
    onboarding.set_defaults(func=command_onboarding)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"code": "ERROR", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
