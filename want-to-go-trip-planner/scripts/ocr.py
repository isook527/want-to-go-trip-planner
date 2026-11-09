#!/usr/bin/env python3
"""Local OCR fallback, called only when the host cannot read a screenshot."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_IMAGE_BYTES = 25 * 1024 * 1024


LANGUAGE = "en-US"
ZH = {'Local screenshot text recognition': '本地截图文字识别', 'Image exceeds 25 MB': '图片大于 25 MB', 'Image is missing or unreadable': '图片不存在或无法读取', 'Local OCR could not read this image': '本地 OCR 无法识别此图片', 'No supported Chinese or English OCR language is available': '没有可用的中文或英文识别语言', 'Could not get available OCR languages': '无法获取 OCR 可用语言', 'macOS Vision could not read this image': 'macOS Vision 无法识别此图片'}


def tr(message):
    return ZH.get(message, message) if LANGUAGE == "zh-CN" else message


def vision(image):
    swift = shutil.which("swift")
    if sys.platform != "darwin" or not swift:
        return None
    env = os.environ.copy()
    cache = Path(tempfile.gettempdir()) / "kornvia-ocr-swift-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env["CLANG_MODULE_CACHE_PATH"] = str(cache)
    env["SWIFT_MODULECACHE_PATH"] = str(cache)
    result = subprocess.run([swift, str(Path(__file__).with_name("ocr_macos.swift")), str(image)], capture_output=True, text=True, timeout=30, env=env)
    if result.returncode:
        raise RuntimeError(tr('macOS Vision could not read this image'))
    return {"engine": "macos_vision", "text": result.stdout.strip()}


def tesseract(image):
    binary = shutil.which("tesseract")
    if not binary:
        return None
    langs = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=20)
    if langs.returncode:
        raise RuntimeError(tr('Could not get available OCR languages'))
    available = {line.strip() for line in langs.stdout.splitlines()}
    selected = [lang for lang in ("chi_sim", "chi_tra", "eng") if lang in available]
    if not selected:
        raise RuntimeError(tr('No supported Chinese or English OCR language is available'))
    result = subprocess.run([binary, str(image), "stdout", "-l", "+".join(selected)], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(tr('Local OCR could not read this image'))
    return {"engine": "tesseract", "languages": selected, "text": result.stdout.strip()}


def extract(image, engine="auto"):
    image = Path(image).expanduser().resolve()
    if not image.is_file():
        raise ValueError(tr('Image is missing or unreadable'))
    if image.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(tr('Image exceeds 25 MB'))
    methods = [("tesseract", tesseract), ("macos_vision", vision)]
    if engine != "auto":
        methods = [(name, func) for name, func in methods if name == engine]
    attempts = []
    for name, func in methods:
        try:
            result = func(image)
            if result and result.get("text", "").strip():
                return {"status": "ok", **result}
            attempts.append(name + ": unavailable_or_empty")
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            attempts.append(name + ": " + type(exc).__name__)
    return {"status": "unavailable", "text": "", "attempts": attempts}


def main():
    parser = argparse.ArgumentParser(description=tr('Local screenshot text recognition'))
    parser.add_argument("image")
    parser.add_argument("--engine", choices=("auto", "macos_vision", "tesseract"), default="auto")
    parser.add_argument("--locale", choices=("zh-CN", "en-US"), default="en-US")
    args = parser.parse_args()
    global LANGUAGE
    LANGUAGE = args.locale
    try:
        result = extract(args.image, args.engine)
    except (ValueError, OSError) as exc:
        result = {"status": "unavailable", "text": "", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
