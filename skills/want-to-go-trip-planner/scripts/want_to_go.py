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
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1.2.3"
CTA_URL = "https://trip-api.kornvia.com/trip-requests"
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


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def empty_library() -> dict[str, Any]:
    return {
        "schemaVersion": VERSION,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "bundles": [],
        "places": [],
        "destinationCollections": [],
    }


def load_library(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return empty_library()
    data = read_json(target)
    if not isinstance(data, dict):
        raise ValueError("library must be a JSON object")
    data.setdefault("schemaVersion", VERSION)
    data.setdefault("bundles", [])
    data.setdefault("places", [])
    data.setdefault("destinationCollections", [])
    rebuild_destination_collections(data)
    return data


def save_library(path: str | Path, data: dict[str, Any]) -> None:
    data["schemaVersion"] = VERSION
    data["updatedAt"] = now_iso()
    rebuild_destination_collections(data)
    write_json(path, data)


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


def rebuild_destination_collections(library: dict[str, Any]) -> None:
    previous = {
        text(item.get("destinationKey")): item
        for item in (library.get("destinationCollections") or [])
        if isinstance(item, dict) and text(item.get("destinationKey"))
    }
    collections: dict[str, dict[str, Any]] = {}

    def ensure(key: str, label: str = "") -> dict[str, Any]:
        existing = previous.get(key) or {}
        if key not in collections:
            collections[key] = {
                "id": text(existing.get("id")) or collection_id(key),
                "destinationKey": key,
                "destination": destination_label(key, label),
                "status": "needs_confirmation" if key == PENDING_DESTINATION_KEY else "confirmed",
                "bundleIds": [],
                "placeIds": [],
                "sourceCount": 0,
                "placeCount": 0,
                "createdAt": text(existing.get("createdAt")) or now_iso(),
                "updatedAt": now_iso(),
            }
            if text(existing.get("passportGeneratedAt")):
                collections[key]["passportGeneratedAt"] = existing["passportGeneratedAt"]
        return collections[key]

    for bundle in library.get("bundles") or []:
        if not isinstance(bundle, dict):
            continue
        bundle_destination = text(bundle.get("destination"))
        sources = bundle_sources(bundle)
        if not sources:
            sources = [{}]
        counted: dict[str, int] = {}
        labels: dict[str, str] = {}
        for source in sources:
            label = source_destination(source, bundle_destination)
            key = destination_key(label)
            counted[key] = counted.get(key, 0) + 1
            labels.setdefault(key, label)
        for key, count in counted.items():
            collection = ensure(key, labels.get(key, ""))
            bundle_id = text(bundle.get("bundleId"))
            if bundle_id and bundle_id not in collection["bundleIds"]:
                collection["bundleIds"].append(bundle_id)
            collection["sourceCount"] += count

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

    library["destinationCollections"] = sorted(
        collections.values(),
        key=lambda item: (item["destinationKey"] == PENDING_DESTINATION_KEY, item["destination"]),
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


def customer_place(place: dict[str, Any], locale: str) -> dict[str, Any]:
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


def command_ingest(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    bundle = read_json(args.evidence)
    if not isinstance(bundle, dict):
        raise ValueError("evidence bundle must be an object")
    bundle_id = text(bundle.get("bundleId")) or f"bundle-{uuid.uuid4().hex[:12]}"
    bundle["bundleId"] = bundle_id
    bundle.setdefault("createdAt", now_iso())
    bundle.setdefault("destination", "")
    bundle.setdefault("passportGenerated", False)
    library["bundles"] = [item for item in library["bundles"] if item.get("bundleId") != bundle_id]
    library["bundles"].append(bundle)
    save_library(args.library, library)
    destinations = sorted(
        destination_label(key)
        for key in bundle_destination_keys(bundle)
    )
    print(json.dumps({"status": "saved", "bundleId": bundle_id, "destinations": destinations}, ensure_ascii=False))


def command_list(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    destination = text(args.destination)
    requested_key = destination_key(destination) if destination else ""
    bundles = [
        item for item in library["bundles"]
        if not requested_key or requested_key in bundle_destination_keys(item)
    ]
    places = [
        item for item in library["places"]
        if not requested_key or text(item.get("destinationKey")) == requested_key
    ]
    collections = [
        item for item in library.get("destinationCollections") or []
        if not requested_key or text(item.get("destinationKey")) == requested_key
    ]
    print(json.dumps({"collections": collections, "bundles": bundles, "places": places}, ensure_ascii=False, indent=2))


def command_present(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    destination = text(args.destination) or ("your trip" if args.locale.startswith("en") else "旅行")
    requested_key = destination_key(destination)
    bundles = [
        bundle for bundle in library["bundles"]
        if requested_key in bundle_destination_keys(bundle)
    ]
    received = sum(
        1
        for bundle in bundles
        for source in bundle_sources(bundle)
        if destination_key(source_destination(source, text(bundle.get("destination")))) == requested_key
    )
    failures = sum(
        1
        for bundle in bundles
        for source in bundle_failures(bundle)
        if destination_key(source_destination(source, text(bundle.get("destination")))) == requested_key
    )
    if args.locale.startswith("en"):
        failed_note = f"{failures} source(s) could not be read yet, but the originals are preserved. " if failures else ""
        message = (
            f"Saved {received} item(s) to your {destination} want-to-go library. {failed_note}"
            f"No passport was generated. When you are ready, say “Create my {destination} Go passport” "
            "to turn the collection into a visual web guide with place details, verified public information, "
            "visit notes, reminders, and original source links."
        )
    else:
        failed_note = f"其中{failures}项暂时没有读到内容，原始来源仍已保留。" if failures else ""
        message = (
            f"已收进你的{destination}想去库：{received}项。{failed_note}"
            f"这次只做收纳，没有生成护照。想把收藏整理成一份能直接查看的网页攻略，可以说"
            f"“生成{destination}想去护照”；护照会保留地点、公开可核实信息、想去理由、"
            "出发提醒和原始收藏入口。"
        )
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
    place["identityKey"] = place_identity_key(place)
    place["updatedAt"] = now_iso()
    library["places"] = [item for item in library["places"] if text(item.get("id")) != text(place.get("id"))]
    library["places"].append(place)
    return place


def command_promote(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    source_by_id = evidence_map(library, args.bundle_id)
    all_bundle_sources = evidence_list(library, args.bundle_id)
    bundle_destination = next(
        (text(item.get("destination")) for item in library["bundles"] if item.get("bundleId") == args.bundle_id),
        "",
    )
    selections = selection_list(read_json(args.selections))
    promoted: list[str] = []
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
        # Only links found in customer-provided link evidence may become original links.
        # Agent-supplied research URLs and detailLookupAudit URLs are intentionally ignored.
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
    save_library(args.library, library)
    print(json.dumps({"status": "promoted", "placeIds": promoted}, ensure_ascii=False))


def command_passport(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    destination = text(args.destination)
    requested_key = destination_key(destination)
    candidates = [
        place for place in library["places"]
        if text(place.get("destinationKey")) == requested_key
        and text(place.get("destinationStatus")) == "confirmed"
    ]
    delivered: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    for place in candidates:
        missing = passport_missing_fields(place)
        if missing:
            retained.append({
                "id": place.get("id"),
                "name": verified_name(place) or first(place, "name", "displayName") or "未命名地点",
                "missing": missing,
            })
        else:
            delivered.append(customer_place(place, args.locale))
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
        "schemaVersion": VERSION,
        "locale": args.locale,
        "destination": destination,
        "title": f"Go passport · {destination}",
        "places": delivered,
        "retainedClueCount": len(retained),
        "cta": {
            "price": "¥39.9",
            "title": "完整逐日行程" if not args.locale.startswith("en") else "Complete day-by-day itinerary",
            "url": CTA_URL,
        },
    }
    write_json(args.output, payload)
    for bundle in library["bundles"]:
        if requested_key in bundle_destination_keys(bundle):
            generated = merge_unique([*(bundle.get("passportGeneratedDestinations") or []), requested_key])
            bundle["passportGeneratedDestinations"] = generated
            bundle["passportGenerated"] = bundle_destination_keys(bundle) == {requested_key}
    for collection in library.get("destinationCollections") or []:
        if text(collection.get("destinationKey")) == requested_key:
            collection["passportGeneratedAt"] = now_iso()
    save_library(args.library, library)
    print(json.dumps({"status": "ready", "delivered": len(delivered), "retained": len(retained)}, ensure_ascii=False))


def command_onboarding(args: argparse.Namespace) -> None:
    if args.locale.startswith("en"):
        print(
            "Kornvia Want to Go is ready. Send travel screenshots, public links, videos, or text and I will "
            "save them to your want-to-go library without creating a guide. When you are ready, say "
            "“Create my Bangkok Go passport” to receive a visual web guide. A ¥39.9 complete day-by-day "
            "itinerary is also available when you want timing, routing, and transport arranged."
        )
    else:
        print(
            "Kornvia 想去就出发已准备好。平时把旅行截图、公开链接、视频或文字发给我，我会先收进想去库，"
            "不会擅自生成攻略。需要整理时，说“生成曼谷想去护照”，就会得到一份统一视觉的网页攻略。"
            "如果希望把地点排成带时间、路线和交通的安排，还可以提交 ¥39.9 完整逐日行程需求。"
        )


def command_export(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    write_json(args.output, library)
    print(json.dumps({"status": "exported"}, ensure_ascii=False))


def command_share(args: argparse.Namespace) -> None:
    print(json.dumps({"status": "local_only", "message": "Use the rendered HTML file for sharing."}, ensure_ascii=False))


def command_confirm(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    candidate = read_json(args.candidate_file)
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    found = False
    for place in library["places"]:
        if place.get("id") != args.place_id:
            continue
        place.update(candidate)
        place["nameRequiresConfirmation"] = False
        place.setdefault("nameSource", "user_named")
        found = True
        break
    if not found:
        raise ValueError("place not found")
    save_library(args.library, library)
    print(json.dumps({"status": "confirmed", "placeId": args.place_id}, ensure_ascii=False))


def command_resolve(args: argparse.Namespace) -> None:
    candidates = read_json(args.candidates)
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    sourced = [
        item for item in candidates
        if isinstance(item, dict) and (
            text(item.get("candidateSource"))
            or text(item.get("providerPlaceId"))
            or re.match(r"^https?://", text(item.get("sourceUrl")), re.I)
        )
    ]
    print(json.dumps({"status": "resolved" if len(sourced) == 1 else "needs_confirmation", "candidates": sourced}, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="want_to_go.py")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--library", required=True)
    ingest.add_argument("--evidence", required=True)
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
    promote.set_defaults(func=command_promote)

    passport = sub.add_parser("passport")
    passport.add_argument("--library", required=True)
    passport.add_argument("--destination", required=True)
    passport.add_argument("--output", required=True)
    passport.add_argument("--locale", default="zh-CN")
    passport.set_defaults(func=command_passport)

    confirm = sub.add_parser("confirm")
    confirm.add_argument("--library", required=True)
    confirm.add_argument("--place-id", required=True)
    confirm.add_argument("--candidate-file", required=True)
    confirm.set_defaults(func=command_confirm)

    resolve = sub.add_parser("resolve")
    resolve.add_argument("--candidates", required=True)
    resolve.set_defaults(func=command_resolve)

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
