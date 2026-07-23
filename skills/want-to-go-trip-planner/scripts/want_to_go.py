#!/usr/bin/env python3
"""Portable JSON want-to-go library helper. Python standard library only."""

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime, timezone
from urllib.parse import quote, urlparse


MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_CAPABILITIES_BYTES = 1024 * 1024
PROVIDER_COORDINATE_SYSTEMS = {
    "amap": "GCJ02",
    "google": "WGS84",
    "mock": "WGS84",
}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path):
    if os.path.getsize(path) > MAX_JSON_BYTES:
        raise ValueError("JSON file exceeds 10 MB")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path, value):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    file_descriptor, temp_path = tempfile.mkstemp(
        prefix=".want-to-go-",
        dir=folder,
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def load(path):
    if not os.path.exists(path):
        return {"schemaVersion": "1.0", "updatedAt": now(), "places": []}
    value = load_json(path)
    if not isinstance(value, dict) or not isinstance(value.get("places"), list):
        raise ValueError("unsupported want-to-go library schema")
    schema_version = value.get("schemaVersion")
    if schema_version is None:
        value["schemaVersion"] = "1.0"
        return value
    match = re.fullmatch(r"(\d+)\.(\d+)", str(schema_version))
    if not match:
        raise ValueError("unsupported want-to-go library schemaVersion")
    major = int(match.group(1))
    if major >= 2:
        raise ValueError(
            f"schemaVersion {schema_version!r} is newer than this tool; "
            "use a future migrate command before loading"
        )
    if major != 1:
        raise ValueError("unsupported want-to-go library schemaVersion")
    if schema_version != "1.0":
        print(
            f"warning: loading compatible schemaVersion {schema_version!r}",
            file=sys.stderr,
        )
    return value


def save(path, value):
    value["updatedAt"] = now()
    write_json_atomic(path, value)


def place_id(place):
    text = "|".join(
        str(place.get(key, "")).strip().lower()
        for key in ("name", "branch", "city", "address", "lat", "lng")
    )
    return "poi_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def provider_match(a, b):
    a_ids = a.get("providerIds") or {}
    b_ids = b.get("providerIds") or {}
    return any(value and b_ids.get(provider) == value for provider, value in a_ids.items())


def normalize_text(value):
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def similarity(a, b):
    left = normalize_text(a)
    right = normalize_text(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        if min(len(left), len(right)) < 2:
            return 0.0
        length_ratio = min(len(left), len(right)) / max(len(left), len(right))
        return 0.82 if length_ratio < 0.82 else length_ratio
    left_pairs = {left[index:index + 2] for index in range(max(1, len(left) - 1))}
    right_pairs = {right[index:index + 2] for index in range(max(1, len(right) - 1))}
    if not left_pairs or not right_pairs:
        return 0.0
    return 2 * len(left_pairs & right_pairs) / (len(left_pairs) + len(right_pairs))


def best_similarity(values, candidate):
    return max([0.0] + [similarity(value, candidate) for value in values if value])


def distance_km(a, b):
    lat1 = math.radians(float(a["lat"]))
    lat2 = math.radians(float(b["lat"]))
    delta_lat = lat2 - lat1
    delta_lng = math.radians(float(b["lng"]) - float(a["lng"]))
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return 6371 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def score_candidate(evidence, candidate):
    names = [
        evidence.get("name"),
        evidence.get("originalText"),
        evidence.get("ocrText"),
        *(evidence.get("aliases") or []),
    ]
    name_score = max(
        [best_similarity(names, candidate.get("name"))]
        + [
            best_similarity(names, alias)
            for alias in (candidate.get("aliases") or [])
        ]
    )
    city_score = best_similarity(
        [evidence.get("city"), evidence.get("destination")],
        " ".join(str(candidate.get(key) or "") for key in ("city", "address")),
    )
    branch_signals = [
        evidence.get("branch"),
        evidence.get("mall"),
        evidence.get("area"),
        evidence.get("landmark"),
    ]
    branch_score = (
        best_similarity(
            branch_signals,
            " ".join(
                str(candidate.get(key) or "")
                for key in ("branch", "mall", "area", "address", "name")
            ),
        )
        if any(branch_signals)
        else 0.5
    )
    address_score = best_similarity(
        [evidence.get("addressHint")], candidate.get("address")
    )
    distance_score = 0.5
    distance = None
    hint = evidence.get("hintLocation")
    if (
        isinstance(hint, dict)
        and all(key in hint for key in ("lat", "lng"))
        and all(key in candidate for key in ("lat", "lng"))
    ):
        distance = distance_km(hint, candidate)
        distance_score = (
            1.0
            if distance <= 0.2
            else 0.85
            if distance <= 1
            else 0.55
            if distance <= 5
            else 0.0
        )
    status_score = (
        1.0
        if candidate.get("businessStatus") == "OPERATIONAL"
        else 0.0
        if candidate.get("businessStatus") == "CLOSED_PERMANENTLY"
        else 0.5
    )
    confidence = round(
        name_score * 0.42
        + city_score * 0.16
        + branch_score * 0.2
        + address_score * 0.1
        + distance_score * 0.08
        + status_score * 0.04,
        3,
    )
    reasons = []
    if name_score >= 0.82:
        reasons.append("店名或别名高度一致")
    if city_score >= 0.8:
        reasons.append("城市一致")
    if any(branch_signals) and branch_score >= 0.75:
        reasons.append("分店、商场或地标信号一致")
    if address_score >= 0.7:
        reasons.append("地址提示一致")
    if distance is not None and distance <= 1:
        reasons.append("与分享位置接近")
    if candidate.get("businessStatus") == "OPERATIONAL":
        reasons.append("地图显示正常营业")
    return {
        **candidate,
        "confidenceScore": confidence,
        "distanceFromHintKm": None if distance is None else round(distance, 2),
        "evidenceReasons": reasons,
    }


def same_place(a, b):
    if provider_match(a, b):
        return True
    return all(
        normalize_text(a.get(key, "")) == normalize_text(b.get(key, ""))
        for key in ("name", "branch", "city")
    ) and normalize_text(a.get("name")) != ""


def normalize(place):
    result = dict(place)
    result["name"] = str(result.get("name", "")).strip()
    if not result["name"]:
        raise ValueError("place.name is required")
    result["id"] = result.get("id") or place_id(result)
    result["confidenceScore"] = float(result.get("confidenceScore", 0))
    result["confirmed"] = result.get("confirmed") is True
    result["status"] = "confirmed" if result["confirmed"] else "needs_confirmation"
    result["sourceRefs"] = result.get("sourceRefs") or []
    result["providerIds"] = result.get("providerIds") or {}
    result["updatedAt"] = now()
    return result


def ingest(args):
    library = load(args.library)
    incoming = load_json(args.input)
    incoming = incoming if isinstance(incoming, list) else [incoming]
    statuses = []
    for raw in incoming:
        place = normalize(raw)
        index = next(
            (i for i, current in enumerate(library["places"]) if same_place(current, place)),
            None,
        )
        if index is None:
            place["createdAt"] = now()
            library["places"].append(place)
            statuses.append({"id": place["id"], "status": "added"})
        else:
            current = library["places"][index]
            refs = current.get("sourceRefs", []) + place.get("sourceRefs", [])
            unique_refs = {json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in refs}
            library["places"][index] = {
                **current,
                **place,
                "sourceRefs": list(unique_refs.values()),
            }
            statuses.append({"id": place["id"], "status": "updated"})
    save(args.library, library)
    print(json.dumps(statuses, ensure_ascii=False, indent=2))


def list_places(args):
    places = load(args.library)["places"]
    if args.destination:
        places = [
            place
            for place in places
            if match_destination(place, args.destination)
        ]
    print(json.dumps(places, ensure_ascii=False, indent=2))


def confirm(args):
    library = load(args.library)
    candidate = load_json(args.candidate)
    for index, current in enumerate(library["places"]):
        if current.get("id") == args.id:
            merged = normalize(
                {
                    **current,
                    **candidate,
                    "id": args.id,
                    "confirmed": True,
                    "confidenceScore": float(
                        candidate.get(
                            "confidenceScore",
                            current.get("confidenceScore", 0),
                        )
                    ),
                }
            )
            if not all(
                key in merged and merged.get(key) not in (None, "")
                for key in ("lat", "lng", "address", "coordinateSystem")
            ):
                raise ValueError(
                    "confirmed place requires address, lat, lng, and coordinateSystem"
                )
            aliases = {
                normalize_text(value): value
                for value in [
                    *(current.get("aliases") or []),
                    *(candidate.get("aliases") or []),
                    current.get("name"),
                    candidate.get("name"),
                ]
                if value
            }
            fingerprints = {
                *((current.get("identity") or {}).get("evidenceFingerprints", [])),
                *(
                    [candidate["evidenceFingerprint"]]
                    if candidate.get("evidenceFingerprint")
                    else []
                ),
            }
            merged["aliases"] = list(aliases.values())
            identity = {
                **(current.get("identity") or {}),
                "canonicalProviderIds": merged.get("providerIds") or {},
                "evidenceFingerprints": sorted(fingerprints),
                "confirmedAt": now(),
                "confirmedBy": "user",
                "lastVerifiedAt": candidate.get("checkedAt") or now(),
            }
            if merged["confidenceScore"] < 0.6:
                identity["lowConfidenceOverride"] = True
            else:
                identity.pop("lowConfidenceOverride", None)
            merged["identity"] = identity
            library["places"][index] = merged
            save(args.library, library)
            print(json.dumps(merged, ensure_ascii=False, indent=2))
            return
    raise ValueError("place id not found")


def resolve(args):
    evidence = load_json(args.evidence)
    candidates = load_json(args.candidates)
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a JSON array")
    if not any(evidence.get(key) for key in ("name", "originalText", "ocrText")):
        raise ValueError("evidence needs name, originalText, or ocrText")
    ranked = sorted(
        [score_candidate(evidence, candidate) for candidate in candidates[:10]],
        key=lambda item: (
            -item["confidenceScore"],
            str(item.get("providerId", "")),
        ),
    )[:3]
    top = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    gap = (
        top["confidenceScore"]
        - (runner_up["confidenceScore"] if runner_up else 0)
        if top
        else 0
    )
    same_name_branches = (
        sum(
            similarity(candidate.get("name"), top.get("name")) >= 0.9
            for candidate in ranked
        )
        > 1
        if top
        else False
    )
    confirmation_required = (
        not top
        or top["confidenceScore"] < 0.9
        or gap < 0.1
        or same_name_branches
        or top.get("businessStatus") == "CLOSED_PERMANENTLY"
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "names": [
                    normalize_text(evidence.get(key))
                    for key in ("name", "originalText", "ocrText")
                ],
                "city": normalize_text(
                    evidence.get("city") or evidence.get("destination")
                ),
                "branch": normalize_text(
                    evidence.get("branch")
                    or evidence.get("mall")
                    or evidence.get("area")
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    result = {
        "schemaVersion": "1.0",
        "resolvedAt": now(),
        "evidenceFingerprint": fingerprint,
        "resolutionStatus": (
            "unresolved"
            if not top or top["confidenceScore"] < 0.6
            else "high_confidence"
            if top["confidenceScore"] >= 0.9 and not confirmation_required
            else "needs_confirmation"
        ),
        "confirmationRequired": confirmation_required,
        "candidates": ranked,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def safe_service_url(value, resolve_host=False):
    parsed = urlparse(value)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("service URL must not contain credentials, query, or fragment")
    if parsed.scheme == "http":
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("HTTP service URL is allowed only for localhost")
        return parsed.geturl().rstrip("/")
    if parsed.scheme != "https" or parsed.port not in {None, 443}:
        raise ValueError("service URL must use public HTTPS on port 443")
    if resolve_host:
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    parsed.hostname,
                    parsed.port or 443,
                    type=socket.SOCK_STREAM,
                )
            }
        except socket.gaierror as error:
            raise ValueError(f"cannot resolve service host: {error}") from error
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise ValueError("service URL resolves to a non-public address")
    return parsed.geturl().rstrip("/")


def service_headers():
    headers = {"Accept": "application/json"}
    token = os.environ.get("WANT_TO_GO_SERVICE_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def doctor(args):
    raw_service_url = (
        args.service_url or os.environ.get("WANT_TO_GO_SERVICE_URL") or ""
    ).strip()
    service_url = ""
    service_url_error = ""
    if raw_service_url:
        try:
            service_url = safe_service_url(
                raw_service_url,
                resolve_host=args.online,
            )
        except ValueError as error:
            service_url_error = str(error)
    service_url_configured = bool(service_url)
    macos_vision = sys.platform == "darwin" and bool(shutil.which("swift"))
    tesseract = bool(shutil.which("tesseract"))
    dedicated_ocr = macos_vision or tesseract
    host_checks = ["network", "weixinpay_pay"]
    if not dedicated_ocr and not args.host_vision:
        host_checks.insert(0, "vision_or_ocr")
    result = {
        "python": True,
        "localFileStorage": True,
        "dedicatedOcr": {
            "available": dedicated_ocr,
            "macosVision": macos_vision,
            "tesseract": tesseract,
        },
        "screenshotExtractionReady": dedicated_ocr or args.host_vision,
        "serviceUrlConfigured": service_url_configured,
        "serviceTokenConfigured": bool(
            os.environ.get("WANT_TO_GO_SERVICE_TOKEN", "").strip()
        ),
        "serviceReachable": None,
        "serviceCapabilities": None,
        "hostChecksStillRequired": host_checks,
    }
    if args.online and service_url_configured:
        try:
            request = urllib.request.Request(
                f"{service_url}/v1/capabilities",
                headers=service_headers(),
            )
            opener = urllib.request.build_opener(NoRedirectHandler())
            with opener.open(request, timeout=5) as response:
                raw = response.read(MAX_CAPABILITIES_BYTES + 1)
                if len(raw) > MAX_CAPABILITIES_BYTES:
                    raise ValueError("capabilities response exceeds 1 MB")
                result["serviceCapabilities"] = json.loads(
                    raw.decode("utf-8")
                )
                result["serviceReachable"] = response.status == 200
        except Exception as error:
            result["serviceReachable"] = False
            result["serviceError"] = str(error)
    elif raw_service_url and not service_url_configured:
        result["serviceError"] = service_url_error
    if result["serviceReachable"] is True:
        result["hostChecksStillRequired"] = [
            item
            for item in result["hostChecksStillRequired"]
            if item != "network"
        ]
    if args.host_weixinpay_pay:
        result["hostChecksStillRequired"] = [
            item
            for item in result["hostChecksStillRequired"]
            if item != "weixinpay_pay"
        ]
    if (
        not service_url_configured
        or result["serviceReachable"] is False
        or not result["serviceTokenConfigured"]
        or not args.host_weixinpay_pay
    ):
        result["paidPlanningReady"] = False
    elif result["serviceReachable"] is True:
        result["paidPlanningReady"] = True
    else:
        result["paidPlanningReady"] = "unverified"
    print(json.dumps(result, ensure_ascii=False, indent=2))


def require_coordinate_system(place, provider):
    expected = PROVIDER_COORDINATE_SYSTEMS.get(provider)
    if expected is None:
        raise ValueError(f"unknown provider: {provider}")
    actual = place.get("coordinateSystem")
    if actual != expected:
        raise ValueError(
            f"place {place.get('id')} coordinateSystem={actual!r}, "
            f"provider {provider} requires {expected!r}; refuse to export"
        )


def match_destination(place, needle):
    normalized_needle = normalize_text(needle)
    if not normalized_needle:
        return False
    if any(
        normalize_text(place.get(key)) == normalized_needle
        for key in ("city", "countryCode", "destination")
    ):
        return True
    return normalized_needle in normalize_text(place.get("address"))


def export(args):
    places = load(args.library)["places"]
    selected = [
        {key: value for key, value in place.items() if key != "sourceRefs"}
        for place in places
        if place.get("status") == "confirmed"
        and match_destination(place, args.destination)
    ]
    for place in selected:
        require_coordinate_system(place, args.provider)
    write_json_atomic(args.output, {"places": selected})
    print(f"exported {len(selected)} confirmed places")


def _redact_payment_codes(value, payment_codes):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.lower() in {"paymentcode", "weixinpay-required"} and item:
                payment_codes.append(str(item))
                redacted[key] = "<redacted, use weixinpay_pay>"
            else:
                redacted[key] = _redact_payment_codes(item, payment_codes)
        return redacted
    if isinstance(value, list):
        return [_redact_payment_codes(item, payment_codes) for item in value]
    return value


def _write_payment_code_file(payment_codes):
    file_descriptor, path = tempfile.mkstemp(prefix="want-to-go-payment-", suffix=".json")
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"paymentCode": payment_codes[0]},
                handle,
                ensure_ascii=False,
            )
            handle.write("\n")
        os.chmod(path, 0o600)
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise
    return path


def claim(args):
    raw_service_url = (
        args.service_url or os.environ.get("WANT_TO_GO_SERVICE_URL") or ""
    ).strip()
    if not raw_service_url:
        raise ValueError(
            "service URL is required via --service-url or WANT_TO_GO_SERVICE_URL"
        )
    service_url = safe_service_url(raw_service_url, resolve_host=True)
    query_order = quote(args.out_trade_no, safe="")
    request = urllib.request.Request(
        f"{service_url}/v1/plan/claim?out_trade_no={query_order}",
        headers=service_headers(),
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    status = 200
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(MAX_CAPABILITIES_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read(MAX_CAPABILITIES_BYTES + 1)
        status = error.code
    if len(raw) > MAX_CAPABILITIES_BYTES:
        raise ValueError("claim response exceeds 1 MB")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("claim response is not valid JSON") from error
    payment_codes = []
    output = _redact_payment_codes(body, payment_codes)
    if isinstance(output, dict):
        output["httpStatus"] = status
    if payment_codes:
        output["paymentCodeFile"] = _write_payment_code_file(payment_codes)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def parser():
    root = argparse.ArgumentParser(description="Manage a local want-to-go JSON library")
    sub = root.add_subparsers(dest="command", required=True)
    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("library")
    p_ingest.add_argument("input")
    p_ingest.set_defaults(func=ingest)
    p_list = sub.add_parser("list")
    p_list.add_argument("library")
    p_list.add_argument("destination", nargs="?")
    p_list.set_defaults(func=list_places)
    p_confirm = sub.add_parser("confirm")
    p_confirm.add_argument("library")
    p_confirm.add_argument("id")
    p_confirm.add_argument("candidate")
    p_confirm.set_defaults(func=confirm)
    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("evidence")
    p_resolve.add_argument("candidates")
    p_resolve.set_defaults(func=resolve)
    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument("--service-url")
    p_doctor.add_argument("--online", action="store_true")
    p_doctor.add_argument("--host-vision", action="store_true")
    p_doctor.add_argument("--host-weixinpay-pay", action="store_true")
    p_doctor.set_defaults(func=doctor)
    p_export = sub.add_parser("export")
    p_export.add_argument("library")
    p_export.add_argument("destination")
    p_export.add_argument("output")
    p_export.add_argument(
        "--provider",
        required=True,
        choices=sorted(PROVIDER_COORDINATE_SYSTEMS),
    )
    p_export.set_defaults(func=export)
    p_claim = sub.add_parser("claim")
    p_claim.add_argument("out_trade_no")
    p_claim.add_argument("--service-url")
    p_claim.set_defaults(func=claim)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    try:
        arguments.func(arguments)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error))
