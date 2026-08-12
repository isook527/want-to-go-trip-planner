#!/usr/bin/env python3
"""Build and verify a deterministic Want-to-go Skill release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


SKILL_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_CONFIG_PATH = SKILL_ROOT / "config" / "product.json"
RELEASE_MANIFEST_PATH = SKILL_ROOT / "release-manifest.json"
ROOT_DIRECTORY = "want-to-go-trip-planner"
MANIFEST_SCHEMA = "kornvia-skill-release-manifest-1"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_files(include_manifest: bool = True) -> list[Path]:
    files: list[Path] = []
    for path in SKILL_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(SKILL_ROOT)
        if path.is_symlink():
            raise ValueError(f"package source contains symlink: {relative.as_posix()}")
        if "__pycache__" in relative.parts or path.name == ".DS_Store":
            continue
        if path.suffix.lower() in {".lock", ".tmp"}:
            continue
        if not include_manifest and path == RELEASE_MANIFEST_PATH:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(SKILL_ROOT).as_posix())


def release_manifest() -> dict:
    config = json.loads(PRODUCT_CONFIG_PATH.read_text(encoding="utf-8"))
    files = []
    for path in package_files(include_manifest=False):
        data = path.read_bytes()
        files.append({
            "path": path.relative_to(SKILL_ROOT).as_posix(),
            "size": len(data),
            "sha256": sha256_bytes(data),
        })
    return {
        "schemaVersion": MANIFEST_SCHEMA,
        "packageName": ROOT_DIRECTORY,
        "packageVersion": str(config["version"]),
        "contractVersion": str(config["contractVersion"]),
        "rootDirectory": ROOT_DIRECTORY,
        "fileCount": len(files),
        "files": files,
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_manifest() -> None:
    encoded = (json.dumps(release_manifest(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(RELEASE_MANIFEST_PATH, encoded)


def zip_info(name: str, directory: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED
    info.external_attr = ((0o40755 if directory else 0o100644) << 16)
    return info


def build_archive(zip_path: Path) -> None:
    write_manifest()
    files = package_files()
    directories = {ROOT_DIRECTORY + "/"}
    for path in files:
        relative = PurePosixPath(path.relative_to(SKILL_ROOT).as_posix())
        parts = relative.parts[:-1]
        for index in range(1, len(parts) + 1):
            directories.add(f"{ROOT_DIRECTORY}/{'/'.join(parts[:index])}/")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{zip_path.name}.", suffix=".tmp", dir=str(zip_path.parent))
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for directory in sorted(directories):
                archive.writestr(zip_info(directory, directory=True), b"")
            for path in files:
                name = f"{ROOT_DIRECTORY}/{path.relative_to(SKILL_ROOT).as_posix()}"
                archive.writestr(zip_info(name), path.read_bytes())
        os.replace(temporary, zip_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    checksum = sha256_bytes(zip_path.read_bytes())
    atomic_write(zip_path.with_suffix(zip_path.suffix + ".sha256"), f"{checksum}  {zip_path.name}\n".encode("ascii"))


def verify_archive(zip_path: Path) -> dict:
    if not zip_path.is_file():
        raise ValueError(f"release archive does not exist: {zip_path}")
    expected_source = {
        path.relative_to(SKILL_ROOT).as_posix(): sha256_bytes(path.read_bytes())
        for path in package_files()
    }
    with zipfile.ZipFile(zip_path) as archive:
        archive.testzip()
        file_names = sorted(name for name in archive.namelist() if not name.endswith("/"))
        roots = {PurePosixPath(name).parts[0] for name in archive.namelist() if PurePosixPath(name).parts}
        if roots != {ROOT_DIRECTORY}:
            raise ValueError("release archive must contain one expected root directory")
        packed = {
            name.removeprefix(f"{ROOT_DIRECTORY}/"): sha256_bytes(archive.read(name))
            for name in file_names
        }
        if packed != expected_source:
            raise ValueError("release archive files do not match the source directory")
        manifest = json.loads(archive.read(f"{ROOT_DIRECTORY}/release-manifest.json"))
    expected_manifest = release_manifest()
    if manifest != expected_manifest:
        raise ValueError("release manifest does not match package source")
    checksum = sha256_bytes(zip_path.read_bytes())
    sidecar = zip_path.with_suffix(zip_path.suffix + ".sha256")
    expected_sidecar = f"{checksum}  {zip_path.name}\n"
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii") != expected_sidecar:
        raise ValueError("release checksum sidecar is missing or invalid")
    return {
        "status": "verified",
        "version": manifest["packageVersion"],
        "files": len(expected_source),
        "sha256": checksum,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify a deterministic Skill release")
    parser.add_argument("--zip", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    zip_path = Path(args.zip)
    if not args.verify:
        build_archive(zip_path)
    print(json.dumps(verify_archive(zip_path), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
