#!/usr/bin/env python3
"""Local want-to-go library and passport preparation.

Public edition: no login, payment, platform bypass, directory scanning, or
private service dependency.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
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
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
NAME_SOURCE_ALIASES = {
    "user": "user_named",
    "structured_data": "public_page",
    "page_title": "public_page",
    "ocr_or_text_heuristic": "material_ocr",
}
NAME_SOURCE_VALUES = {
    "user_named", "public_page", "material_ocr", "visual_review", "unresolved",
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
FACT_AUDIT_STATUSES = {"unverified", "verified", "conflict", "not_found", "stale"}
FACT_AUDIT_PRIORITY = {
    "verified": 0,
    "unverified": 1,
    "not_found": 2,
    "stale": 3,
    "conflict": 4,
}
FACT_STATUS_LABELS_ZH = {
    "unverified": "尚未人工复核",
    "verified": "来源一致",
    "conflict": "来源存在冲突",
    "not_found": "公开来源未查到",
    "stale": "信息已过适用期",
}
FACT_STATUS_LABELS_EN = {
    "unverified": "Not manually reviewed",
    "verified": "Sources agree",
    "conflict": "Sources conflict",
    "not_found": "Not found in public sources",
    "stale": "Information is out of date",
}
FACT_FIELD_LABELS_ZH = {
    "verifiedName": "地点名称",
    "address": "地址",
    "positionText": "位置",
    "openingHoursText": "营业时间",
    "reservationPolicy": "预约政策",
    "temporaryClosure": "临时关闭",
    "routeConnection": "交通衔接",
}
FACT_FIELD_LABELS_EN = {
    "verifiedName": "Place name",
    "address": "Address",
    "positionText": "Location",
    "openingHoursText": "Opening hours",
    "reservationPolicy": "Reservation policy",
    "temporaryClosure": "Temporary closure",
    "routeConnection": "Transport connection",
}
EXECUTION_RISK_TYPES = {
    "last_mile", "reservation_ticket", "weather_sensitive",
    "temporary_closure", "transfer_buffer",
}
EXECUTION_RISK_SCOPES = {"place", "route", "trip"}
EXECUTION_RISK_LABELS_ZH = {
    "last_mile": "最后一公里",
    "reservation_ticket": "预约或购票",
    "weather_sensitive": "天气敏感",
    "temporary_closure": "临时关闭或节假日调整",
    "transfer_buffer": "换乘与时间缓冲",
}
EXECUTION_RISK_LABELS_EN = {
    "last_mile": "Last-mile access",
    "reservation_ticket": "Reservation or ticket",
    "weather_sensitive": "Weather-sensitive",
    "temporary_closure": "Temporary closure or holiday change",
    "transfer_buffer": "Transfer and time buffer",
}
EXECUTION_SCOPE_LABELS_ZH = {"place": "地点", "route": "路线", "trip": "行程"}
EXECUTION_SCOPE_LABELS_EN = {"place": "Place", "route": "Route", "trip": "Trip"}
AUDIT_SOURCE_KINDS = {"official", "reliable_public"}

EDITABLE_PLACE_FIELDS = {
    "name", "verifiedName", "localName", "placeType", "category", "categoryLabel",
    "branch", "branchName", "address", "addressText", "positionText", "openingHoursText",
    "openingHours", "signature", "reason", "whyGo", "highlight", "visitTip", "reminder",
    "departureReminder", "departureTip", "routeStart", "routeEnd", "waypoints",
    "suggestedDuration", "durationText", "destination", "displayMediaId",
}
CONFIRM_EVIDENCE_FIELDS = {
    "candidateSource", "providerPlaceId", "sourceUrl", "confirmedBy", "confirmationQuote",
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
    r"(?:\.workbuddy|\.claude|/Users/|/mnt/|[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]|"
    r"%USERPROFILE%|https?://(?:localhost|127\.0\.0\.1)(?::\d+)?|"
    r"sourceIds?|mediaIds?|confidenceScore|detailLookupAudit|nameSource|hostChecks|"
    r"platformAccess|platformItemId|platformEngagement|failureCode|"
    r"schemaVersion|operationId|tombstones?|promptInjection|宿主诊断)",
    re.IGNORECASE,
)
SENSITIVE_QUERY_KEYS = {
    "xsec_token", "xsec_source", "access_token", "refresh_token", "auth_token",
    "authorization", "api_key", "apikey", "signature", "sig",
}
MAX_SCAN_ENTRY_BYTES = 32 * 1024 * 1024
MAX_SCAN_TOTAL_BYTES = 128 * 1024 * 1024
MAX_SCAN_ARCHIVE_ENTRIES = 10_000
MAX_CUSTOMER_PHOTO_BYTES = 16 * 1024 * 1024
RELEASE_ROOT = "want-to-go-trip-planner"
RELEASE_MANIFEST_NAME = f"{RELEASE_ROOT}/release-manifest.json"
RELEASE_MANIFEST_SCHEMA = "kornvia-skill-release-manifest-1"

CLI_ERROR_MESSAGES = {
    "zh-CN": {
        "ARGUMENT_ERROR": "命令参数不完整或格式不正确，请按帮助中的参数重新执行。",
        "FILE_ERROR": "无法读取或写入指定文件，请检查路径、权限和文件状态。",
        "JSON_ERROR": "JSON 文件格式不正确，请修正后重试。",
        "CONTRACT_VALIDATION_FAILED": "数据未通过共享契约校验，请先修正缺失或越界字段。",
        "PACKAGE_SCAN_FAILED": "发布包或客户输出未通过安全扫描，请查看审计代码并修正后重试。",
        "PLACE_NOT_FOUND": "没有找到指定地点或目的地，请核对 ID 和所属分库。",
        "OPERATION_CONFLICT": "操作状态或幂等标识冲突，请刷新当前库后使用新的操作标识重试。",
        "INVALID_INPUT": "输入内容不符合要求，请核对字段类型、允许值和必填项。",
        "ERROR": "操作未完成，请检查输入和当前库状态后重试。",
    },
    "en-US": {
        "ARGUMENT_ERROR": "Command arguments are missing or invalid. Check the help text and try again.",
        "FILE_ERROR": "The requested file could not be read or written. Check its path, permissions and state.",
        "JSON_ERROR": "The JSON file is invalid. Correct it and try again.",
        "CONTRACT_VALIDATION_FAILED": "The data failed the shared contract. Correct missing or out-of-range fields first.",
        "PACKAGE_SCAN_FAILED": "The release package or customer output failed its security scan. Review the audit code and correct it before retrying.",
        "PLACE_NOT_FOUND": "The requested place or destination was not found. Check its ID and destination library.",
        "OPERATION_CONFLICT": "The operation state or idempotency identifier conflicts with current data. Reload the library and retry with a new operation ID.",
        "INVALID_INPUT": "The input is invalid. Check field types, allowed values and required fields.",
        "ERROR": "The operation did not complete. Check the input and current library state, then try again.",
    },
}


def normalized_locale(value: Any) -> str:
    return "en-US" if text(value).lower().startswith("en") else "zh-CN"


def cli_error_code(error: BaseException) -> str:
    message = str(error)
    prefix = re.match(r"^([A-Z][A-Z0-9_]{2,}):", message)
    if prefix:
        return prefix.group(1)
    lowered = message.casefold()
    if isinstance(error, json.JSONDecodeError):
        return "JSON_ERROR"
    if isinstance(error, OSError):
        return "FILE_ERROR"
    if "contract validation failed" in lowered or "schema_" in lowered:
        return "CONTRACT_VALIDATION_FAILED"
    if "scan failed" in lowered or "archive" in lowered or "release manifest" in lowered:
        return "PACKAGE_SCAN_FAILED"
    if "not found" in lowered or "不存在" in message:
        return "PLACE_NOT_FOUND"
    if "operation-id" in lowered or "operation id" in lowered or "collision" in lowered or "undo" in lowered:
        return "OPERATION_CONFLICT"
    if any(token in lowered for token in ("must ", "needs ", "required", "invalid", "unsupported", "cannot be empty")):
        return "INVALID_INPUT"
    return "ERROR"


def localized_cli_error(error: BaseException, locale: Any) -> dict[str, str]:
    normalized = normalized_locale(locale)
    code = cli_error_code(error)
    messages = CLI_ERROR_MESSAGES[normalized]
    return {"code": code, "message": messages.get(code, messages["ERROR"])}


def requested_cli_locale(argv: list[str]) -> str:
    for index, value in enumerate(argv):
        if value == "--locale" and index + 1 < len(argv):
            return normalized_locale(argv[index + 1])
        if value.startswith("--locale="):
            return normalized_locale(value.split("=", 1)[1])
    return "zh-CN"


class LocalizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        locale = requested_cli_locale(sys.argv[1:])
        payload = {
            "code": "ARGUMENT_ERROR",
            "message": CLI_ERROR_MESSAGES[locale]["ARGUMENT_ERROR"],
        }
        self.exit(2, json.dumps(payload, ensure_ascii=False) + "\n")


def normalized_name_source(value: Any) -> str:
    candidate = NAME_SOURCE_ALIASES.get(text(value), text(value))
    return candidate if candidate in NAME_SOURCE_VALUES else "unresolved"


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
        fsync_directory(target.parent)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


write_json = atomic_write_json


def fsync_directory(path: Union[str, Path]) -> None:
    """Persist a completed rename where the platform supports directory fsync."""
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


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
        place.setdefault(
            "name",
            text(place.get("verifiedName") or place.get("displayName") or place.get("localName")),
        )
        place.setdefault("sourceIds", [])
        place.setdefault("mediaIds", [])
        place.setdefault("placeType", normalized_place_type(place))
        place.setdefault("sortOrder", index)
        place.setdefault("createdAt", now_iso())
        place.setdefault("updatedAt", place["createdAt"])
        if "nameSource" in place:
            place["nameSource"] = normalized_name_source(place.get("nameSource"))
        legacy_audits = place.get("detailLookupAudit")
        if isinstance(legacy_audits, list):
            normalized_audits = [
                normalized or legacy_fact_audit(item)
                for item in legacy_audits
                if (normalized := normalize_fact_audit(item))
                or legacy_fact_audit(item)
            ]
            if normalized_audits != legacy_audits:
                place["detailLookupAudit"] = normalized_audits
                changes.append(f"normalized_fact_audits:{text(place.get('id'))}")
        legacy_risks = place.get("executionRisks")
        if isinstance(legacy_risks, list):
            normalized_risks = [
                normalized
                for item in legacy_risks
                if (normalized := normalize_execution_risk(item))
            ]
            if normalized_risks != legacy_risks:
                place["executionRisks"] = normalized_risks
                changes.append(f"normalized_execution_risks:{text(place.get('id'))}")
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
        snapshot.setdefault("tripRisks", [])
        snapshot.setdefault("changes", [])
        normalized_trip_risks = [
            normalized
            for item in snapshot.get("tripRisks") or []
            if (normalized := normalize_execution_risk(item, text(snapshot.get("checkedAt"))))
        ]
        if normalized_trip_risks != snapshot.get("tripRisks"):
            snapshot["tripRisks"] = normalized_trip_risks
            changes.append(f"normalized_trip_risks:{text(snapshot.get('id')) or 'unknown'}")
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict):
                continue
            checked_default = text(snapshot.get("checkedAt"))
            supplied_audits = item.get("factAudits") or item.get("detailLookupAudit") or []
            normalized_audits = [
                normalized or legacy_fact_audit(audit, checked_default)
                for audit in supplied_audits
                if (normalized := normalize_fact_audit(audit, checked_default))
                or legacy_fact_audit(audit, checked_default)
            ]
            if not normalized_audits and isinstance(item.get("facts"), dict):
                normalized_audits = [
                    {
                        "field": field,
                        "status": "unverified",
                        "sources": [],
                        "cannotProve": merge_unique(item.get("cannotProve") or []),
                        "nextAction": "需要补充公开来源后再确认",
                    }
                    for field in item["facts"]
                ]
            item["factAudits"] = normalized_audits
            item.pop("detailLookupAudit", None)
            item.setdefault("accessLevel", "submitted")
            item.setdefault("canSupport", [])
            item.setdefault("cannotProve", ["legacy_review_evidence_incomplete"])
            item.setdefault("facts", {})
            item["executionRisks"] = [
                normalized
                for risk in item.get("executionRisks") or []
                if (normalized := normalize_execution_risk(risk, checked_default))
            ]
    manual_limits = PRODUCT_CONFIG["offers"]["manualItineraryBeta"]["limits"]
    current_statuses = {"draft", *REQUEST_WORKFLOW_STAGES, "declined", "cancelled"}
    for request in data["tripRequests"]:
        if not isinstance(request, dict):
            continue
        request.setdefault("id", f"trip-request-{uuid.uuid4().hex[:12]}")
        request.setdefault("offerId", PUBLIC_OFFER_ID)
        request.setdefault("destinationKey", destination_key(request.get("destination")))
        request.setdefault("status", "draft")
        request.setdefault("createdAt", now_iso())
        request.setdefault("updatedAt", text(request.get("createdAt")) or now_iso())
        if old_version != CONTRACT_VERSION:
            request.setdefault("legacyImported", True)
        if text(request.get("offerId")) == "pre-trip-review" and request.get("legacyImported") is not True:
            request["legacyImported"] = True
            changes.append(f"legacy_trip_request_offer:{text(request.get('id')) or 'unknown'}")
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
    source_ids_by_bundle: dict[str, dict[str, str]] = {}
    for bundle, _raw, base_id, canonical_id in canonical_source_records(data.get("bundles") or []):
        source_ids_by_bundle.setdefault(text(bundle.get("bundleId")), {})[base_id] = canonical_id
    rebuild_source_ledger(data)
    active_source_ids = {text(item.get("id")) for item in data.get("sources") or []}
    for place in data.get("places") or []:
        if not isinstance(place, dict):
            continue
        bundle_ids = merge_unique([*(place.get("bundleIds") or []), place.get("bundleId")])
        remapped: list[str] = []
        for source_id in merge_unique(place.get("sourceIds") or []):
            candidates = [
                source_ids_by_bundle[bundle_id][source_id]
                for bundle_id in bundle_ids
                if source_id in source_ids_by_bundle.get(bundle_id, {})
            ]
            remapped.extend(candidates or ([source_id] if source_id in active_source_ids else [source_id]))
        place["sourceIds"] = merge_unique(remapped)
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


def destination_key_for_library(library: dict[str, Any], value: Any) -> str:
    raw = text(value)
    normalized = normalized_token(raw)
    if not normalized:
        return PENDING_DESTINATION_KEY
    for destination in library.get("destinations") or []:
        if not isinstance(destination, dict):
            continue
        aliases = [
            destination.get("key"), destination.get("name"),
            *(destination.get("aliases") or []),
        ]
        if any(normalized_token(alias) == normalized for alias in aliases):
            return text(destination.get("key")) or destination_key(raw)
    return destination_key(raw)


def destination_label(key: str, fallback: str = "") -> str:
    return text(fallback) or DESTINATION_LABELS_ZH.get(key) or key


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


def bundle_destination_labels(bundle: dict[str, Any]) -> dict[str, str]:
    bundle_destination = text(bundle.get("destination"))
    labels: dict[str, str] = {}
    for source in bundle_sources(bundle):
        label = source_destination(source, bundle_destination)
        key = destination_key(label)
        if key not in labels and label:
            labels[key] = label
    if not labels and bundle_destination:
        labels[destination_key(bundle_destination)] = bundle_destination
    return labels


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
            aliases = merge_unique(existing.get("aliases") or [])
            if aliases:
                collections[key]["aliases"] = aliases
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


def customer_safe_url(value: Any) -> str:
    """Keep a submitted public URL while removing credential-like query fields."""
    raw = text(value)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in SENSITIVE_QUERY_KEYS
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), parsed.fragment))


def customer_source_links(value: Any) -> list[dict[str, str]]:
    links = source_links({"originalSourceLinks": value if isinstance(value, list) else []})
    result: list[dict[str, str]] = []
    for item in links:
        safe_url = customer_safe_url(item.get("url"))
        if safe_url:
            result.append({**item, "url": safe_url})
    return result


def audit_sources(raw: Any, checked_urls: Any = None) -> list[dict[str, str]]:
    candidates = raw if isinstance(raw, list) else []
    if not candidates and isinstance(checked_urls, list):
        candidates = [{"url": item, "kind": "reliable_public"} for item in checked_urls]
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, str):
            url, label, kind = text(item), "", "reliable_public"
        elif isinstance(item, dict):
            url = text(item.get("url") or item.get("href"))
            label = text(item.get("label") or item.get("title"))
            kind = text(item.get("kind")) or "reliable_public"
        else:
            continue
        if not re.match(r"^https?://", url, flags=re.I) or url in seen:
            continue
        if kind not in AUDIT_SOURCE_KINDS:
            continue
        seen.add(url)
        result.append({
            "url": url,
            "label": label or ("官方公开来源" if kind == "official" else "可信公开来源"),
            "kind": kind,
        })
    return result


def normalize_fact_audit(raw: Any, checked_at_default: str = "", strict: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("fact audit must be an object")
        return {}
    field = text(raw.get("field"))
    status = text(raw.get("status")) or "unverified"
    checked_at = text(raw.get("checkedAt")) or checked_at_default
    valid_until = text(raw.get("validUntil"))
    sources = audit_sources(raw.get("sources"), raw.get("checkedUrls"))
    cannot_prove = merge_unique(raw.get("cannotProve") or [])
    next_action = text(raw.get("nextAction"))
    if not field or status not in FACT_AUDIT_STATUSES:
        if strict:
            raise ValueError("fact audit needs field and supported status")
        return {}
    if status != "unverified" and (not checked_at or not sources):
        if strict:
            raise ValueError("reviewed fact audit needs checkedAt and an official or reliable public source")
        return {}
    result: dict[str, Any] = {
        "field": field,
        "status": status,
        "sources": sources,
        "cannotProve": cannot_prove,
        "nextAction": next_action,
    }
    if checked_at:
        result["checkedAt"] = checked_at
    if valid_until:
        result["validUntil"] = valid_until
    return result


def legacy_fact_audit(raw: Any, checked_at_default: str = "") -> dict[str, Any]:
    """Losslessly downgrade a recognizable legacy audit instead of deleting it."""
    if not isinstance(raw, dict) or not text(raw.get("field")):
        return {}
    legacy_status = text(raw.get("status")) or "missing"
    cannot_prove = merge_unique([
        *(raw.get("cannotProve") or []),
        f"legacy_audit_status:{legacy_status}",
    ])
    result: dict[str, Any] = {
        "field": text(raw.get("field")),
        "status": "unverified",
        "sources": audit_sources(raw.get("sources"), raw.get("checkedUrls")),
        "cannotProve": cannot_prove,
        "nextAction": text(raw.get("nextAction")) or "Recheck this legacy fact against a current public source.",
    }
    checked_at = text(raw.get("checkedAt")) or checked_at_default
    if checked_at:
        result["checkedAt"] = checked_at
    if text(raw.get("validUntil")):
        result["validUntil"] = text(raw.get("validUntil"))
    return result


def normalize_execution_risk(raw: Any, checked_at_default: str = "", strict: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("execution risk must be an object")
        return {}
    risk_type = text(raw.get("type"))
    scope = text(raw.get("scope"))
    status = text(raw.get("status")) or "unverified"
    checked_at = text(raw.get("checkedAt")) or checked_at_default
    valid_until = text(raw.get("validUntil"))
    sources = audit_sources(raw.get("sources"), raw.get("checkedUrls"))
    cannot_prove = merge_unique(raw.get("cannotProve") or [])
    next_action = text(raw.get("nextAction"))
    summary = text(raw.get("summary"))
    if risk_type not in EXECUTION_RISK_TYPES or scope not in EXECUTION_RISK_SCOPES or status not in FACT_AUDIT_STATUSES:
        if strict:
            raise ValueError("execution risk needs supported type, scope and status")
        return {}
    if status != "unverified" and (not checked_at or not sources):
        if strict:
            raise ValueError("reviewed execution risk needs checkedAt and an official or reliable public source")
        return {}
    result: dict[str, Any] = {
        "type": risk_type,
        "scope": scope,
        "status": status,
        "summary": summary,
        "sources": sources,
        "cannotProve": cannot_prove,
        "nextAction": next_action,
    }
    if checked_at:
        result["checkedAt"] = checked_at
    if valid_until:
        result["validUntil"] = valid_until
    return result


def customer_verification_summary(place: dict[str, Any], locale: str) -> dict[str, Any]:
    en = locale.startswith("en")
    labels = FACT_FIELD_LABELS_EN if en else FACT_FIELD_LABELS_ZH
    status_labels = FACT_STATUS_LABELS_EN if en else FACT_STATUS_LABELS_ZH
    audits = [
        normalized
        for item in (place.get("detailLookupAudit") or [])
        if (normalized := normalize_fact_audit(item))
    ]
    if not audits:
        return {
            "status": "unverified",
            "statusLabel": status_labels["unverified"],
            "sources": [],
            "pending": ["Public-source or manual review has not been completed." if en else "尚未完成人工或公开来源复核"],
            "nextActions": ["Check hours, reservations and transport before departure." if en else "出发前核对营业、预约和交通信息"],
            "items": [],
        }
    status = max((item["status"] for item in audits), key=lambda item: FACT_AUDIT_PRIORITY[item])
    checked_values = sorted(item.get("checkedAt", "") for item in audits if item.get("checkedAt"))
    valid_values = sorted(item.get("validUntil", "") for item in audits if item.get("validUntil"))
    sources: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    for audit in audits:
        for source in audit["sources"]:
            if source["url"] not in seen_sources:
                seen_sources.add(source["url"])
                sources.append(source)
    pending = merge_unique([item for audit in audits for item in audit["cannotProve"]])
    next_actions = merge_unique([audit["nextAction"] for audit in audits if audit["nextAction"]])
    items = []
    for audit in audits:
        item = {
            "label": labels.get(audit["field"], audit["field"]),
            "status": audit["status"],
            "statusLabel": status_labels[audit["status"]],
            "sources": audit["sources"],
            "pending": audit["cannotProve"],
            "nextAction": audit["nextAction"],
        }
        if audit.get("checkedAt"):
            item["checkedAt"] = audit["checkedAt"]
        if audit.get("validUntil"):
            item["validUntil"] = audit["validUntil"]
        items.append(item)
    result: dict[str, Any] = {
        "status": status,
        "statusLabel": status_labels[status],
        "sources": sources,
        "pending": pending,
        "nextActions": next_actions,
        "items": items,
    }
    if checked_values:
        result["checkedAt"] = checked_values[-1]
    if valid_values:
        result["validUntil"] = valid_values[0]
    return result


def customer_execution_risks(raw: Any, locale: str) -> list[dict[str, Any]]:
    en = locale.startswith("en")
    type_labels = EXECUTION_RISK_LABELS_EN if en else EXECUTION_RISK_LABELS_ZH
    scope_labels = EXECUTION_SCOPE_LABELS_EN if en else EXECUTION_SCOPE_LABELS_ZH
    status_labels = FACT_STATUS_LABELS_EN if en else FACT_STATUS_LABELS_ZH
    result: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        normalized = normalize_execution_risk(item)
        if not normalized:
            continue
        customer = {
            **normalized,
            "label": type_labels[normalized["type"]],
            "scopeLabel": scope_labels[normalized["scope"]],
            "statusLabel": status_labels[normalized["status"]],
        }
        result.append(customer)
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


def customer_photo(
    place: dict[str, Any],
    media_by_id: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    existing = normalized_photo(place.get("displayPhoto") or place.get("photo"))
    if existing:
        return existing
    media_id_value = text(place.get("displayMediaId"))
    media = (media_by_id or {}).get(media_id_value)
    if not isinstance(media, dict) or text(media.get("role")) != "display_crop":
        return {}
    path_value = text(media.get("path"))
    if not path_value:
        return {}
    return normalized_photo({"path": path_value})


def verified_image_bytes(path_value: Any) -> tuple[bytes, str]:
    path = Path(text(path_value))
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_CUSTOMER_PHOTO_BYTES:
        return b"", ""
    data = path.read_bytes()
    suffix = path.suffix.lower()
    valid = (
        suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")
        or suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
        or suffix == ".webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    )
    return (data, suffix) if valid else (b"", "")


def stage_customer_photos(places: list[dict[str, Any]], output_path: Union[str, Path]) -> None:
    """Copy verified display images beside the passport so the renderer stays sandboxed."""
    output = Path(output_path).resolve()
    asset_root = output.parent / f"{output.stem}-assets"
    for place in places:
        photo = place.get("photo")
        if not isinstance(photo, dict) or not text(photo.get("path")):
            continue
        data, suffix = verified_image_bytes(photo.get("path"))
        if not data:
            place.pop("photo", None)
            continue
        asset_root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        target = asset_root / f"{digest[:24]}{suffix}"
        if not target.exists():
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=str(asset_root)
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
                fsync_directory(asset_root)
            except Exception:
                if temporary.exists():
                    temporary.unlink()
                raise
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("passport asset hash collision")
        photo["path"] = os.path.relpath(target, output.parent)


def customer_place(
    place: dict[str, Any],
    locale: str,
    content_depth: str = "standard",
    media_by_id: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
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
        "photo": customer_photo(place, media_by_id),
        "originalSourceLinks": customer_source_links(place.get("originalSourceLinks") or []),
        "verificationSummary": customer_verification_summary(place, locale),
        "executionRisks": customer_execution_risks(place.get("executionRisks"), locale),
    }
    if content_depth == "compact":
        allowed = {
            "id", "name", "placeType", "category", "address", "positionText",
            "openingHoursText", "visitTip", "photo", "originalSourceLinks",
            "verificationSummary", "executionRisks",
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
    current_sources = bundle_evidence(bundle) + bundle_failures(bundle)
    if current_sources:
        return current_sources
    legacy = bundle.get("sources")
    return legacy if isinstance(legacy, list) else []


UNTRUSTED_INSTRUCTION_PATTERN = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?|"
    r"disregard\s+(?:all\s+)?(?:previous|prior|earlier)\s+(?:instructions?|guidance)|"
    r"override\s+(?:the\s+)?(?:system|developer|previous)\s+(?:prompt|message|instructions?)|"
    r"(?:new\s+task|act\s+as|developer\s+mode)\s*[:：]?|"
    r"system\s+prompt|developer\s+message|reveal\s+(?:the\s+)?prompt|"
    r"忽略(?:之前|以上|前面)(?:所有)?(?:指令|要求)|系统提示词|开发者消息|"
    r"(?:新任务|请执行|开发者模式)\s*[:：]?|"
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


def canonical_source_records(
    bundles: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], dict[str, Any], str, str]]:
    """Yield stable ledger IDs while preserving legacy IDs that were globally unique."""
    seen_ids: set[str] = set()
    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue
        batch_id = text(bundle.get("bundleId")) or f"batch-{uuid.uuid4().hex[:12]}"
        for index, raw in enumerate(bundle_sources(bundle)):
            if not isinstance(raw, dict):
                continue
            base_id = text(raw.get("sourceId") or raw.get("id")) or f"source-{index + 1}"
            candidate = base_id if base_id not in seen_ids else f"{batch_id}-{base_id}"
            suffix = 2
            while candidate in seen_ids:
                candidate = f"{batch_id}-{base_id}-{suffix}"
                suffix += 1
            seen_ids.add(candidate)
            yield bundle, raw, base_id, candidate


def canonical_source_id_map(library: dict[str, Any], bundle_id: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for bundle, _raw, base_id, canonical_id in canonical_source_records(library.get("bundles") or []):
        if text(bundle.get("bundleId")) != bundle_id:
            continue
        if base_id in result:
            raise ValueError(f"bundle contains duplicate sourceId: {base_id}")
        result[base_id] = canonical_id
    if not result:
        raise ValueError("bundle not found or contains no sources")
    return result


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
    for bundle, raw, _base_id, source_id in canonical_source_records(bundles):
        batch_id = text(bundle.get("bundleId")) or f"batch-{uuid.uuid4().hex[:12]}"
        bundle_destination = text(bundle.get("destination"))
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
            "destinationKey": destination_key_for_library(library, destination),
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
        if find_event_by_operation(library, operation_id, "source.ingest", bundle_id):
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
    destination_labels = bundle_destination_labels(bundle)
    destinations = sorted(
        destination_label(key, destination_labels.get(key, ""))
        for key in bundle_destination_keys(bundle)
    )
    print(json.dumps({"status": "saved", "bundleId": bundle_id, "destinations": destinations}, ensure_ascii=False))


def command_list(args: argparse.Namespace) -> None:
    library = load_library(args.library)
    destination = text(args.destination)
    requested_key = destination_key_for_library(library, destination) if destination else ""
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
    requested_key = destination_key_for_library(library, destination)
    sources = [
        source for source in library["sources"]
        if text(source.get("destinationKey")) == requested_key
    ]
    received = len(sources)
    failures = sum(1 for source in sources if text(source.get("status")) == "failed")
    if args.locale.startswith("en"):
        failed_note = f"{failures} source(s) could not be read yet, but the originals are preserved. " if failures else ""
        next_step = PRODUCT_CONFIG["copy"]["savedNextStepEn"].replace("{destination}", destination)
        message = f"Your {destination} want-to-go library now contains {received} item(s). {failed_note}No passport was generated. {next_step}"
    else:
        failed_note = f"其中{failures}项暂时没有读到内容，原始来源仍已保留。" if failures else ""
        next_step = PRODUCT_CONFIG["copy"]["savedNextStepZh"].replace("{destination}", destination)
        message = f"你的{destination}想去库现有{received}项。{failed_note}这次只做收纳，没有生成护照。{next_step}"
    print(message)


def command_destination_alias(args: argparse.Namespace) -> None:
    alias = text(args.alias)
    if not alias:
        raise ValueError("destination alias cannot be empty")
    operation_id = get_operation_id(
        args, f"destination-alias:{normalized_token(args.destination)}:{normalized_token(alias)}",
    )
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id, "destination.alias"):
            print(json.dumps({"status": "already_applied"}, ensure_ascii=False))
            return
        canonical_key = destination_key_for_library(library, args.destination)
        canonical = next(
            (item for item in library["destinations"] if text(item.get("key")) == canonical_key),
            None,
        )
        if not canonical:
            raise ValueError("canonical destination not found; ingest it before registering aliases")
        alias_key = destination_key_for_library(library, alias)
        merged = next(
            (
                item for item in library["destinations"]
                if text(item.get("key")) == alias_key and alias_key != canonical_key
            ),
            None,
        )
        alias_values = [alias]
        if merged:
            alias_values.extend([merged.get("name"), *(merged.get("aliases") or [])])
            for place in library["places"]:
                if text(place.get("destinationKey")) == alias_key:
                    place["destinationKey"] = canonical_key
                    place["destination"] = text(canonical.get("name")) or text(args.destination)
                    place["updatedAt"] = now_iso()
        canonical["aliases"] = merge_unique([*(canonical.get("aliases") or []), *alias_values])
        canonical["updatedAt"] = now_iso()
        append_event(
            library, operation_id, "destination.alias", canonical_key,
            after={"alias": alias, "mergedKey": alias_key if merged else ""},
        )
    print(json.dumps({"status": "registered", "destinationKey": canonical_key, "alias": alias}, ensure_ascii=False))


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


def find_event_by_operation(
    library: dict[str, Any],
    operation_id: str,
    expected_type: str = "",
    entity_id: str = "",
) -> Optional[dict[str, Any]]:
    event = next(
        (
            event for event in library.get("events") or []
            if isinstance(event, dict) and text(event.get("operationId")) == operation_id
        ),
        None,
    )
    if event and (
        (expected_type and text(event.get("type")) != expected_type)
        or (entity_id and text(event.get("entityId")) != entity_id)
    ):
        raise ValueError(
            "operation-id collision: the ID is already used by another command or entity"
        )
    return event


def append_event(
    library: dict[str, Any],
    operation_id: str,
    event_type: str,
    entity_id: str = "",
    before: Any = None,
    after: Any = None,
    undoable: bool = False,
) -> dict[str, Any]:
    existing = find_event_by_operation(library, operation_id, event_type, entity_id)
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
        existing_event = find_event_by_operation(library, operation_id, "place.promote", args.bundle_id)
        if existing_event:
            prior = existing_event.get("after") if isinstance(existing_event.get("after"), dict) else {}
            print(json.dumps({"status": "already_applied", "placeIds": prior.get("placeIds", [])}, ensure_ascii=False))
            return
        source_by_id = evidence_map(library, args.bundle_id)
        source_id_map = canonical_source_id_map(library, args.bundle_id)
        all_bundle_sources = evidence_list(library, args.bundle_id)
        bundle_destination = next(
            (text(item.get("destination")) for item in library["bundles"] if item.get("bundleId") == args.bundle_id),
            "",
        )
        for selection in selections:
            name = first(selection, "verifiedName", "name", "displayName")
            if not name:
                raise ValueError("every selection needs a name")
            raw_source_ids = selection.get("sourceIds") or []
            if not isinstance(raw_source_ids, list):
                raise ValueError(f"地点“{name}”的 sourceIds 必须是数组")
            requested_source_ids = merge_unique(raw_source_ids)
            unknown_source_ids = [item for item in requested_source_ids if item not in source_by_id]
            if unknown_source_ids:
                raise ValueError(
                    f"地点“{name}”引用了本批次不存在的来源：{', '.join(unknown_source_ids)}"
                )
            linked_sources = sources_for_selection(selection, source_by_id, all_bundle_sources)
            linked_raw_ids = merge_unique(
                [text(item.get("sourceId") or item.get("id")) for item in linked_sources]
            )
            source_ids = [source_id_map[item] for item in linked_raw_ids if item in source_id_map]
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
                destination_key_for_library(library, item)
                for item in linked_destinations
                if destination_key_for_library(library, item) != PENDING_DESTINATION_KEY
            }
            if len(linked_keys) > 1:
                raise ValueError(f"同一地点来源包含多个目的地：{', '.join(linked_destinations)}")
            explicit_key = destination_key_for_library(library, explicit_destination) if explicit_destination else ""
            if explicit_key and linked_keys and explicit_key not in linked_keys:
                raise ValueError(
                    f"地点“{name}”填写的目的地与来源不一致：{explicit_destination} / {', '.join(linked_destinations)}"
                )
            selected_destination = explicit_destination or next(iter(linked_destinations), "") or bundle_destination
            selected_key = destination_key_for_library(library, selected_destination)
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
            evidence_sources = [
                normalized_name_source(item.get("nameSource"))
                for item in linked_sources
                if text(item.get("nameSource"))
            ]
            explicit_name_source = normalized_name_source(selection.get("nameSource"))
            if text(selection.get("nameSource")):
                name_source = explicit_name_source
            elif evidence_sources:
                name_source = next(
                    (item for item in evidence_sources if item in {"material_ocr", "unresolved"}),
                    evidence_sources[0],
                )
            else:
                name_source = "unresolved"
            evidence_requires_confirmation = any(
                item.get("nameRequiresConfirmation") is True for item in linked_sources
            ) or any(item in {"material_ocr", "unresolved"} for item in evidence_sources)
            place["nameSource"] = name_source
            place["nameRequiresConfirmation"] = bool(
                selection.get("nameRequiresConfirmation") is True
                or evidence_requires_confirmation
                or name_source in {"material_ocr", "unresolved"}
            )
            place = upsert_place(library, place, args.bundle_id)
            promoted.append(place["id"])
        append_event(library, operation_id, "place.promote", args.bundle_id, after={"placeIds": promoted})
    print(json.dumps({"status": "promoted", "placeIds": promoted}, ensure_ascii=False))


def command_passport(args: argparse.Namespace) -> None:
    destination = text(args.destination)
    content_depth = text(getattr(args, "content_depth", "standard")) or "standard"
    if content_depth not in {"deep", "standard", "compact"}:
        raise ValueError("content depth must be deep, standard, or compact")
    visitor_mode = bool(getattr(args, "visitor_mode", False))
    delivered: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    with library_transaction(args.library) as library:
        media_by_id = {
            text(item.get("id")): item
            for item in (library.get("media") or [])
            if isinstance(item, dict) and text(item.get("id"))
        }
        requested_key = destination_key_for_library(library, destination)
        operation_id = get_operation_id(args, f"passport:{requested_key}:{uuid.uuid4().hex[:12]}")
        candidates = sorted(
            [
                place for place in library["places"]
                if text(place.get("destinationKey")) == requested_key
                and text(place.get("destinationStatus")) == "confirmed"
            ],
            key=lambda place: (int(place.get("sortOrder", 0) or 0), text(place.get("name"))),
        )
        latest_snapshot = next(
            (
                snapshot for snapshot in reversed(library.get("verificationSnapshots") or [])
                if isinstance(snapshot, dict)
                and text(snapshot.get("destinationKey")) == requested_key
            ),
            None,
        )
        snapshot_items = {
            text(item.get("placeId")): item
            for item in ((latest_snapshot or {}).get("items") or [])
            if isinstance(item, dict) and text(item.get("placeId"))
        }
        for place in candidates:
            missing = passport_missing_fields(place)
            if missing:
                retained.append({
                    "id": place.get("id"),
                    "name": verified_name(place) or first(place, "name", "displayName") or "未命名地点",
                    "missing": missing,
                })
            else:
                customer_input = copy.deepcopy(place)
                reviewed = snapshot_items.get(text(place.get("id")))
                if reviewed:
                    customer_input["detailLookupAudit"] = copy.deepcopy(reviewed.get("factAudits") or [])
                    customer_input["executionRisks"] = copy.deepcopy(reviewed.get("executionRisks") or [])
                delivered.append(
                    customer_place(customer_input, args.locale, content_depth, media_by_id)
                )
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
            "tripRisks": customer_execution_risks((latest_snapshot or {}).get("tripRisks"), args.locale),
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
        stage_customer_photos(payload["places"], args.output)
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
    for field, value in patch.items():
        if field == "waypoints":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("waypoints must be an array of strings")
        elif field == "openingHours":
            if not isinstance(value, (str, dict)):
                raise ValueError("openingHours must be text or an object")
        elif not isinstance(value, str):
            raise ValueError(f"{field} must be text")
    if "placeType" in patch and patch["placeType"] not in {"business", "venue", "public_space", "route"}:
        raise ValueError("placeType must be business, venue, public_space, or route")
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
        existing_event = find_event_by_operation(library, operation_id, "place.update", args.place_id)
        if existing_event:
            print(json.dumps({"status": "already_applied", "placeId": args.place_id}, ensure_ascii=False))
            return
        place = next((item for item in library["places"] if text(item.get("id")) == args.place_id), None)
        if not place:
            raise ValueError("place not found")
        if text(patch.get("displayMediaId")):
            allowed_media = set(place.get("mediaIds") or [])
            if text(patch["displayMediaId"]) not in allowed_media:
                raise ValueError("displayMediaId must belong to this place")
        before = copy.deepcopy(place)
        place.update(copy.deepcopy(patch))
        if "destination" in patch:
            key = destination_key_for_library(library, place.get("destination"))
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
        if find_event_by_operation(library, operation_id, "place.delete", args.place_id):
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
        if find_event_by_operation(library, operation_id, "place.restore", args.place_id):
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
    with library_transaction(args.library) as library:
        requested_key = destination_key_for_library(library, args.destination)
        operation_id = get_operation_id(args, f"reorder:{requested_key}:{uuid.uuid4().hex[:12]}")
        if find_event_by_operation(library, operation_id, "place.reorder", requested_key):
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
    operation_id = text(getattr(args, "operation_id", ""))
    if not operation_id:
        raise ValueError("operation-id is required for undo so retries cannot undo another event")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id, "event.undo"):
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
        later_for_entity = next(
            (
                item for item in reversed(library["events"])
                if item.get("undoable") is True
                and not text(item.get("undoneAt"))
                and text(item.get("entityId")) == entity_id
            ),
            None,
        )
        if later_for_entity is not event:
            raise ValueError("undo the latest active event for this place or destination first")
        if event_type == "place.update":
            if not isinstance(event.get("before"), dict):
                raise ValueError("undo data is missing")
            if sum(text(item.get("id")) == entity_id for item in library["places"]) != 1:
                raise ValueError("place is not in the expected active state")
            replace_place(library, event["before"])
        elif event_type == "place.delete":
            if not isinstance(event.get("before"), dict):
                raise ValueError("undo data is missing")
            if any(text(item.get("id")) == entity_id for item in library["places"]):
                raise ValueError("place is already active; undo the later event first")
            library["places"].append(copy.deepcopy(event["before"]))
            library["tombstones"] = [
                item for item in library["tombstones"]
                if not (item.get("entityType") == "place" and text(item.get("entityId")) == entity_id)
            ]
        elif event_type == "place.restore":
            if sum(text(item.get("id")) == entity_id for item in library["places"]) != 1:
                raise ValueError("restored place is not in the expected active state")
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


def command_migrate(args: argparse.Namespace) -> int:
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
    validation_issues = validate_library_contract(migrated)
    report["validationIssues"] = validation_issues
    if validation_issues:
        report["status"] = "blocked"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
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
    return 0


def repair_library(library: dict[str, Any]) -> tuple[list[str], list[str]]:
    fixes: list[str] = []
    blockers: list[str] = []
    for item in library.get("media") or []:
        if not isinstance(item, dict) or item.get("role") != "original":
            continue
        current = sha256_if_file(item.get("path"))
        if current and current != text(item.get("sha256")):
            blockers.append(f"original_media_hash_mismatch:{item.get('id')}")
    grouped_places: dict[str, list[dict[str, Any]]] = {}
    place_order: list[str] = []
    for place in library.get("places") or []:
        if not isinstance(place, dict):
            continue
        place_id = text(place.get("id"))
        if place_id not in grouped_places:
            place_order.append(place_id)
        grouped_places.setdefault(place_id, []).append(place)
    repaired_places: list[dict[str, Any]] = []
    for place_id in place_order:
        candidates = grouped_places[place_id]
        winner = copy.deepcopy(max(
            candidates,
            key=lambda item: (text(item.get("updatedAt")), text(item.get("createdAt"))),
        ))
        if len(candidates) > 1:
            for candidate in candidates:
                for key, value in candidate.items():
                    if key not in winner or winner.get(key) in (None, "", []):
                        winner[key] = copy.deepcopy(value)
                for key in ("sourceIds", "mediaIds", "bundleIds"):
                    winner[key] = merge_unique([
                        *(winner.get(key) or []), *(candidate.get(key) or []),
                    ])
                winner["originalSourceLinks"] = source_links({
                    "originalSourceLinks": [
                        *(winner.get("originalSourceLinks") or []),
                        *(candidate.get("originalSourceLinks") or []),
                    ]
                })
            fixes.append(f"merged_duplicate_place:{place_id}")
        repaired_places.append(winner)
    library["places"] = repaired_places

    before_destinations = json.dumps(library.get("destinations") or [], ensure_ascii=False, sort_keys=True)
    before_sources = json.dumps(library.get("sources") or [], ensure_ascii=False, sort_keys=True)
    rebuild_source_ledger(library)
    rebuild_media_ledger(library)
    rebuild_destinations(library)
    if before_sources != json.dumps(library.get("sources") or [], ensure_ascii=False, sort_keys=True):
        fixes.append("rebuilt_source_and_media_ledgers")
    if before_destinations != json.dumps(library.get("destinations") or [], ensure_ascii=False, sort_keys=True):
        fixes.append("rebuilt_destination_index")
    source_ids = {text(item.get("id")) for item in library.get("sources") or []}
    for place in library.get("places") or []:
        for source_id in merge_unique(place.get("sourceIds") or []):
            if source_id not in source_ids:
                blockers.append(f"dangling_place_source:{text(place.get('id'))}:{source_id}")
    return fixes, blockers


def command_repair(args: argparse.Namespace) -> int:
    if args.dry_run:
        library = load_library(args.library)
        fixes, blockers = repair_library(library)
    else:
        with file_lock(args.library):
            library = load_library(args.library)
            fixes, blockers = repair_library(library)
            if not blockers:
                append_event(
                    library,
                    get_operation_id(args, f"repair:{uuid.uuid4().hex[:12]}"),
                    "library.repair",
                    text(library.get("id")),
                    after={"fixes": fixes},
                )
                _save_library_unlocked(args.library, library)
    print(json.dumps({"status": "blocked" if blockers else "ready", "dryRun": bool(args.dry_run), "fixes": fixes, "blockers": blockers}, ensure_ascii=False))
    return 1 if blockers else 0


def json_schema_issues(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the contract subset used by the bundled draft-2020-12 schema."""
    if "$ref" in schema:
        reference = text(schema["$ref"])
        if not reference.startswith("#/"):
            return [f"schema_external_ref:{path}"]
        resolved: Any = root
        for token in reference[2:].split("/"):
            resolved = resolved.get(token.replace("~1", "/").replace("~0", "~")) if isinstance(resolved, dict) else None
        if not isinstance(resolved, dict):
            return [f"schema_missing_ref:{path}:{reference}"]
        return json_schema_issues(value, resolved, root, path)

    issues: list[str] = []
    for child in schema.get("allOf") or []:
        if isinstance(child, dict):
            issues.extend(json_schema_issues(value, child, root, path))
    condition = schema.get("if")
    if isinstance(condition, dict) and not json_schema_issues(value, condition, root, path):
        consequence = schema.get("then")
        if isinstance(consequence, dict):
            issues.extend(json_schema_issues(value, consequence, root, path))
    prohibited = schema.get("not")
    if isinstance(prohibited, dict) and not json_schema_issues(value, prohibited, root, path):
        issues.append(f"schema_not:{path}")

    expected_type = schema.get("type")
    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected_type, True)
    if expected_type and not type_ok:
        return [*issues, f"schema_type:{path}:{expected_type}"]
    if "const" in schema and value != schema["const"]:
        issues.append(f"schema_const:{path}")
    if "enum" in schema and value not in schema["enum"]:
        issues.append(f"schema_enum:{path}")

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0) or 0):
            issues.append(f"schema_min_length:{path}")
        if schema.get("maxLength") is not None and len(value) > int(schema["maxLength"]):
            issues.append(f"schema_max_length:{path}")
        if text(schema.get("pattern")) and not re.search(text(schema["pattern"]), value):
            issues.append(f"schema_pattern:{path}")
        if schema.get("format") == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            issues.append(f"schema_date:{path}")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                issues.append(f"schema_datetime:{path}")
        if schema.get("format") == "uri" and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
            issues.append(f"schema_uri:{path}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None and value < schema["minimum"]:
            issues.append(f"schema_minimum:{path}")
        if schema.get("maximum") is not None and value > schema["maximum"]:
            issues.append(f"schema_maximum:{path}")
    if isinstance(value, list):
        if schema.get("minItems") is not None and len(value) < int(schema["minItems"]):
            issues.append(f"schema_min_items:{path}")
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            issues.append(f"schema_max_items:{path}")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                issues.append(f"schema_unique_items:{path}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(json_schema_issues(item, item_schema, root, f"{path}[{index}]"))
    if isinstance(value, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                issues.append(f"schema_required:{path}.{key}")
        properties = schema.get("properties") or {}
        for key, child_value in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                issues.extend(json_schema_issues(child_value, child_schema, root, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                issues.append(f"schema_additional_property:{path}.{key}")
    return issues


def validate_library_contract(library: dict[str, Any]) -> list[str]:
    schema = read_json(CONTRACT_PATH)
    issues: list[str] = json_schema_issues(library, schema, schema)
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
    requested_key = destination_key_for_library(load_library(args.library), args.destination)
    for item in items:
        if not text(item.get("placeId")):
            raise ValueError("every review item needs placeId")
        item.setdefault("accessLevel", "public_readable")
        item.setdefault("canSupport", [])
        item.setdefault("cannotProve", [])
        item.setdefault("facts", {})
        supplied_audits = item.get("factAudits") or item.get("detailLookupAudit") or []
        if not isinstance(supplied_audits, list):
            raise ValueError("factAudits must be an array")
        fact_audits = [
            normalize_fact_audit(audit, checked_at, strict=True)
            for audit in supplied_audits
        ]
        if not fact_audits and isinstance(item.get("facts"), dict):
            fact_audits = [
                {
                    "field": field,
                    "status": "unverified",
                    "sources": [],
                    "cannotProve": merge_unique(item.get("cannotProve") or []),
                    "nextAction": "需要补充公开来源后再确认",
                }
                for field in item["facts"]
            ]
        item["factAudits"] = fact_audits
        item.pop("detailLookupAudit", None)
        supplied_risks = item.get("executionRisks") or []
        if not isinstance(supplied_risks, list):
            raise ValueError("executionRisks must be an array")
        item["executionRisks"] = [
            normalize_execution_risk(risk, checked_at, strict=True)
            for risk in supplied_risks
        ]
        if any(risk["scope"] == "trip" for risk in item["executionRisks"]):
            raise ValueError("trip-scoped risks belong in tripRisks")
    supplied_trip_risks = raw.get("tripRisks") or []
    if not isinstance(supplied_trip_risks, list):
        raise ValueError("tripRisks must be an array")
    trip_risks = [
        normalize_execution_risk(risk, checked_at, strict=True)
        for risk in supplied_trip_risks
    ]
    if any(risk["scope"] != "trip" for risk in trip_risks):
        raise ValueError("tripRisks only accepts trip-scoped risks")
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
            "tripRisks": copy.deepcopy(trip_risks),
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
    destination_key_value = text(request.get("destinationKey")) or destination_key_for_library(
        load_library(args.library), destination,
    )
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
        if find_event_by_operation(library, operation_id, "trip_request.save", entity["id"]):
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
                if item.stat().st_size > MAX_SCAN_ENTRY_BYTES:
                    raise ValueError(f"scan entry too large: {item}")
                yield str(item), item.read_text(encoding="utf-8", errors="replace")
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_SCAN_ARCHIVE_ENTRIES:
                raise ValueError("archive contains too many entries")
            total = 0
            for info in sorted(infos, key=lambda item: item.filename):
                name = info.filename.replace("\\", "/")
                if unsafe_archive_name(name):
                    raise ValueError(f"unsafe_archive_entry:{name}")
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise ValueError(f"encrypted_archive_entry:{name}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"symlink_archive_entry:{name}")
                if info.file_size > MAX_SCAN_ENTRY_BYTES:
                    raise ValueError(f"scan entry too large: {name}")
                total += info.file_size
                if total > MAX_SCAN_TOTAL_BYTES:
                    raise ValueError("archive expanded content is too large")
                if PurePosixPath(name).suffix.lower() in allowed:
                    yield name, archive.read(info).decode("utf-8", errors="replace")
    elif path.suffix.lower() in allowed:
        if path.stat().st_size > MAX_SCAN_ENTRY_BYTES:
            raise ValueError(f"scan entry too large: {path}")
        yield str(path), path.read_text(encoding="utf-8", errors="replace")


def unsafe_archive_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    return (
        not normalized
        or normalized.startswith("/")
        or bool(re.match(r"^[A-Za-z]:/", normalized))
        or ".." in parts
        or "\x00" in normalized
    )


def forbidden_package_name(name: str) -> bool:
    normalized = name.replace("\\", "/").rstrip("/")
    base = PurePosixPath(normalized).name.casefold()
    if base in {".ds_store", "__pycache__", ".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        return True
    if base.startswith(".env."):
        return True
    return bool(re.search(r"\.(?:tmp|temp|lock|pem|key|p12|pfx)$", base, re.I))


def release_manifest_issues(archive: zipfile.ZipFile) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    names = [info.filename.replace("\\", "/") for info in archive.infolist()]
    if len(names) != len(set(names)):
        issues.append({"file": str(archive.filename), "type": "duplicate_archive_entry"})
    roots = {PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}
    if roots != {RELEASE_ROOT}:
        issues.append({"file": str(archive.filename), "type": "unexpected_release_root"})
    if RELEASE_MANIFEST_NAME not in names:
        issues.append({"file": RELEASE_MANIFEST_NAME, "type": "missing_release_manifest"})
        return issues
    try:
        manifest = json.loads(archive.read(RELEASE_MANIFEST_NAME))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        issues.append({"file": RELEASE_MANIFEST_NAME, "type": "invalid_release_manifest"})
        return issues
    if not isinstance(manifest, dict):
        issues.append({"file": RELEASE_MANIFEST_NAME, "type": "invalid_release_manifest"})
        return issues
    expected_markers = {
        "schemaVersion": RELEASE_MANIFEST_SCHEMA,
        "packageName": RELEASE_ROOT,
        "packageVersion": VERSION,
        "contractVersion": CONTRACT_VERSION,
        "rootDirectory": RELEASE_ROOT,
    }
    for field, expected in expected_markers.items():
        if manifest.get(field) != expected:
            issues.append({"file": RELEASE_MANIFEST_NAME, "type": f"release_manifest_{field}_mismatch"})
    listed = manifest.get("files")
    if not isinstance(listed, list) or manifest.get("fileCount") != len(listed):
        issues.append({"file": RELEASE_MANIFEST_NAME, "type": "release_manifest_file_count_mismatch"})
        return issues
    expected_files = {
        name.removeprefix(f"{RELEASE_ROOT}/")
        for name in names
        if not name.endswith("/") and name != RELEASE_MANIFEST_NAME
    }
    listed_files: dict[str, dict[str, Any]] = {}
    for item in listed:
        if not isinstance(item, dict) or not text(item.get("path")):
            issues.append({"file": RELEASE_MANIFEST_NAME, "type": "invalid_release_manifest_file"})
            continue
        path = text(item["path"])
        if unsafe_archive_name(path) or path in listed_files:
            issues.append({"file": path, "type": "invalid_release_manifest_path"})
            continue
        listed_files[path] = item
    if set(listed_files) != expected_files:
        issues.append({"file": RELEASE_MANIFEST_NAME, "type": "release_manifest_file_set_mismatch"})
    for path, item in listed_files.items():
        archive_name = f"{RELEASE_ROOT}/{path}"
        if archive_name not in names:
            continue
        data = archive.read(archive_name)
        if item.get("size") != len(data):
            issues.append({"file": path, "type": "release_manifest_size_mismatch"})
        if text(item.get("sha256")) != hashlib.sha256(data).hexdigest():
            issues.append({"file": path, "type": "release_manifest_hash_mismatch"})
    return issues


def customer_url_issues(content: str) -> list[str]:
    issues: list[str] = []
    attributes = re.findall(r"\b(?:href|src|action)\s*=\s*['\"]([^'\"]+)['\"]", content, re.I)
    for value in attributes:
        if re.match(r"^data:image/(?:png|jpeg|webp);base64,", value, re.I):
            continue
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            issues.append("unsafe_url_protocol")
            continue
        hostname = parsed.hostname.casefold()
        if hostname == "localhost":
            issues.append("unsafe_url_host")
            continue
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and not address.is_global:
            issues.append("unsafe_url_host")
        if any(key.casefold() in SENSITIVE_QUERY_KEYS for key, _item in parse_qsl(parsed.query, keep_blank_values=True)):
            issues.append("sensitive_url_query")
    return sorted(set(issues))


def customer_text_for_scan(content: str) -> str:
    without_images = re.sub(
        r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+",
        "[embedded-image]",
        content,
    )
    without_attributes = re.sub(
        r"\b(href|src|action)\s*=\s*(['\"])[^'\"]*\2",
        lambda match: f'{match.group(1)}="[customer-url]"',
        without_images,
        flags=re.I,
    )
    return re.sub(r"https?://[^\s\"'<>]+", "[customer-url]", without_attributes, flags=re.I)


def command_scan(args: argparse.Namespace) -> None:
    target = Path(args.path)
    if not target.exists():
        raise ValueError("scan path does not exist")
    issues: list[dict[str, str]] = []
    if target.is_dir():
        for item in target.rglob("*"):
            relative = item.relative_to(target).as_posix()
            if item.is_symlink():
                issues.append({"file": relative, "type": "forbidden_symlink"})
            if forbidden_package_name(relative):
                issues.append({"file": relative, "type": "forbidden_artifact"})
    elif target.suffix.lower() == ".zip":
        with zipfile.ZipFile(target) as archive:
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                if unsafe_archive_name(name):
                    issues.append({"file": name, "type": "unsafe_archive_entry"})
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    issues.append({"file": name, "type": "forbidden_symlink"})
                if forbidden_package_name(name):
                    issues.append({"file": name, "type": "forbidden_artifact"})
            if args.mode == "package":
                issues.extend(release_manifest_issues(archive))
    for name, content in iter_text_payloads(target):
        secret_scan_content = re.sub(
            r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+",
            "[embedded-image]",
            content,
        )
        scan_content = customer_text_for_scan(content)
        if SECRET_PATTERN.search(secret_scan_content):
            issues.append({"file": name, "type": "possible_secret"})
        if args.mode == "customer":
            if CUSTOMER_INTERNAL_PATTERN.search(scan_content):
                issues.append({"file": name, "type": "customer_internal_leak"})
            for issue in customer_url_issues(content):
                issues.append({"file": name, "type": issue})
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
    forbidden = sorted(set(candidate) - EDITABLE_PLACE_FIELDS - CONFIRM_EVIDENCE_FIELDS)
    if forbidden:
        raise ValueError(f"candidate contains non-editable fields: {', '.join(forbidden)}")
    source_url = text(candidate.get("sourceUrl"))
    candidate_source = text(candidate.get("candidateSource"))
    provider_place_id = text(candidate.get("providerPlaceId"))
    confirmed_by = text(candidate.get("confirmedBy"))
    confirmation_quote = text(candidate.get("confirmationQuote"))
    if candidate_source == "user_confirmation" and (
        confirmed_by != "user" or not confirmation_quote
    ):
        raise ValueError(
            "user_confirmation requires confirmedBy=user and a confirmationQuote from the current user turn"
        )
    has_evidence = bool(
        provider_place_id
        or re.match(r"^https?://", source_url, re.I)
        or candidate_source in {"user_confirmation", "public_page", "visual_review"}
    )
    if not has_evidence:
        raise ValueError("confirmation requires a public URL, provider place ID, or explicit user/visual confirmation")
    update = {key: copy.deepcopy(value) for key, value in candidate.items() if key in EDITABLE_PLACE_FIELDS}
    if not update:
        raise ValueError("confirmation candidate contains no place fields")
    for field, value in update.items():
        if field == "waypoints":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("waypoints must be an array of strings")
        elif field == "openingHours":
            if not isinstance(value, (str, dict)):
                raise ValueError("openingHours must be text or an object")
        elif not isinstance(value, str):
            raise ValueError(f"{field} must be text")
    operation_id = get_operation_id(args, f"confirm:{args.place_id}:{uuid.uuid4().hex[:12]}")
    with library_transaction(args.library) as library:
        if find_event_by_operation(library, operation_id, "place.update", args.place_id):
            print(json.dumps({"status": "already_applied", "placeId": args.place_id}, ensure_ascii=False))
            return
        place = next((item for item in library["places"] if text(item.get("id")) == args.place_id), None)
        if not place:
            raise ValueError("place not found")
        before = copy.deepcopy(place)
        place.update(update)
        place["nameRequiresConfirmation"] = False
        if candidate_source == "visual_review":
            place["nameSource"] = "visual_review"
        elif candidate_source == "user_confirmation":
            place["nameSource"] = "user_named"
        else:
            place["nameSource"] = "public_page"
        place["identityKey"] = place_identity_key(place)
        place["updatedAt"] = now_iso()
        append_event(
            library, operation_id, "place.update", args.place_id, before, place, True,
        )
        library["events"][-1]["after"] = {
            **copy.deepcopy(place),
            "confirmationEvidence": {
                **({"candidateSource": candidate_source} if candidate_source else {}),
                **({"providerPlaceId": provider_place_id} if provider_place_id else {}),
                **({"sourceUrl": source_url} if source_url else {}),
                **({"confirmedBy": confirmed_by} if confirmed_by else {}),
                **({"confirmationQuote": confirmation_quote[:240]} if confirmation_quote else {}),
            },
        }
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
    parser = LocalizedArgumentParser(prog="want_to_go.py")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=LocalizedArgumentParser)

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
    present.add_argument("--locale", choices=("zh-CN", "en-US", "en"), default="zh-CN")
    present.set_defaults(func=command_present)

    destination_alias = sub.add_parser("destination-alias")
    destination_alias.add_argument("--library", required=True)
    destination_alias.add_argument("--destination", required=True)
    destination_alias.add_argument("--alias", required=True)
    destination_alias.add_argument("--operation-id", default="")
    destination_alias.set_defaults(func=command_destination_alias)

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
    passport.add_argument("--locale", choices=("zh-CN", "en-US", "en"), default="zh-CN")
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
    undo.add_argument("--operation-id", required=True)
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
    onboarding.add_argument("--locale", choices=("zh-CN", "en-US", "en"), default="zh-CN")
    onboarding.set_defaults(func=command_onboarding)
    for command_parser in sub.choices.values():
        if not any("--locale" in action.option_strings for action in command_parser._actions):
            command_parser.add_argument("--locale", choices=("zh-CN", "en-US", "en"), default="zh-CN")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
        return int(result or 0)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps(localized_cli_error(exc, getattr(args, "locale", "zh-CN")), ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
