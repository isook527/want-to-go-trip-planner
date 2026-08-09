#!/usr/bin/env python3
"""Create a photo-first 4:3 display crop from a customer screenshot.

The original screenshot remains untouched. Candidate windows are scored for
photographic texture and colour diversity so social captions and text slides do
not become a place card's main image.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


DEFAULT_ASPECT = 4 / 3
DEFAULT_MIN_PHOTO_SCORE = 0.45


def parse_aspect(value: str) -> float:
    raw = str(value).strip()
    if ":" in raw:
        left, right = raw.split(":", 1)
        aspect = float(left) / float(right)
    else:
        aspect = float(raw)
    if not math.isfinite(aspect) or aspect <= 0:
        raise argparse.ArgumentTypeError("target aspect must be positive")
    return aspect


def evenly_spaced(limit: int, count: int = 9) -> list[int]:
    if limit <= 0:
        return [0]
    return sorted({round(limit * index / (count - 1)) for index in range(count)})


def candidate_boxes(width: int, height: int, aspect: float) -> list[tuple[int, int, int, int]]:
    max_width = min(width, round(height * aspect))
    boxes: set[tuple[int, int, int, int]] = set()
    for scale in (1.0, 0.84, 0.68, 0.55):
        crop_width = max(64, min(width, round(max_width * scale)))
        crop_height = max(48, min(height, round(crop_width / aspect)))
        crop_width = max(1, min(width, round(crop_height * aspect)))
        for left in evenly_spaced(width - crop_width, 7):
            for top in evenly_spaced(height - crop_height, 11):
                boxes.add((left, top, left + crop_width, top + crop_height))
    return sorted(boxes)


def photographic_score(image) -> float:
    from PIL import ImageFilter, ImageStat

    sample = image.convert("RGB")
    sample.thumbnail((128, 96))
    pixel_count = max(1, sample.width * sample.height)

    quantized = sample.quantize(colors=24)
    palette_counts = [
        count for count, _colour in (quantized.getcolors(pixel_count) or [])
    ]
    dominant_share = max(palette_counts, default=pixel_count) / pixel_count
    top_four_share = sum(sorted(palette_counts, reverse=True)[:4]) / pixel_count
    diversity = min(1.0, max(0.0, (1.0 - dominant_share) / 0.70))

    gray = sample.convert("L")
    entropy = min(1.0, gray.entropy() / 7.5)
    contrast = min(1.0, ImageStat.Stat(gray).stddev[0] / 64.0)
    saturation_channel = sample.convert("HSV").getchannel("S")
    saturation = min(1.0, ImageStat.Stat(saturation_channel).mean[0] / 96.0)
    edge_image = gray.filter(ImageFilter.FIND_EDGES)
    edge_strength = min(1.0, ImageStat.Stat(edge_image).mean[0] / 48.0)
    rgb_pixels = list(sample.getdata())
    neutral_share = sum(max(pixel) - min(pixel) < 12 for pixel in rgb_pixels) / pixel_count
    black_or_white_share = sum(
        max(pixel) < 45 or min(pixel) > 225 for pixel in rgb_pixels
    ) / pixel_count
    edge_pixels = list(edge_image.getdata())
    row_means = [
        sum(edge_pixels[offset:offset + sample.width]) / sample.width
        for offset in range(0, len(edge_pixels), sample.width)
    ]
    row_mean = sum(row_means) / max(1, len(row_means))
    row_variance = sum((value - row_mean) ** 2 for value in row_means) / max(1, len(row_means))
    row_edge_cv = math.sqrt(row_variance) / max(1.0, row_mean)

    score = (
        0.30 * diversity
        + 0.20 * entropy
        + 0.18 * contrast
        + 0.20 * saturation
        + 0.12 * edge_strength
    )
    if dominant_share > 0.55:
        score -= (dominant_share - 0.55) * 0.90
    if saturation < 0.04 and dominant_share > 0.45:
        score -= 0.10
    if top_four_share > 0.48:
        score -= min(0.55, (top_four_share - 0.48) / 0.52 * 0.55)
    if neutral_share > 0.58:
        neutral_penalty = (neutral_share - 0.58) / 0.42
        score -= min(0.72, neutral_penalty * min(1.0, black_or_white_share / 0.45) * 0.72)
    if row_edge_cv > 0.65:
        score -= min(0.30, (row_edge_cv - 0.65) * 0.65)
    return max(0.0, min(1.0, score))


def best_crop(source, aspect: float) -> tuple[tuple[int, int, int, int], float]:
    width, height = source.size
    best_box = (0, 0, width, height)
    best_score = -1.0
    for box in candidate_boxes(width, height, aspect):
        score = photographic_score(source.crop(box))
        # A small upper-screen preference breaks near-ties without hard-coding
        # a platform-specific y offset. Users may have scrolled before capture.
        centre_x = (box[0] + box[2]) / 2 / width
        centre_y = (box[1] + box[3]) / 2 / height
        score += max(0.0, 0.05 * (1.0 - abs(centre_x - 0.5) * 2.0))
        score += max(0.0, 0.02 * (1.0 - centre_y))
        area_ratio = ((box[2] - box[0]) * (box[3] - box[1])) / (width * height)
        score += 0.04 * math.sqrt(area_ratio)
        if score > best_score:
            best_box = box
            best_score = score
    return best_box, max(0.0, min(1.0, best_score))


def prepare_display_image(
    input_path: str,
    output_path: str,
    aspect: float = DEFAULT_ASPECT,
    min_photo_score: float = DEFAULT_MIN_PHOTO_SCORE,
) -> dict[str, object]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("Pillow is required for deterministic display-image cropping") from exc

    source = Image.open(input_path).convert("RGB")
    width, height = source.size
    box, score = best_crop(source, aspect)
    cropped = source.crop(box)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(target, "JPEG", quality=90, optimize=True)
    photo_rich = score >= min_photo_score
    return {
        "output": str(target),
        "sourceSize": {"width": width, "height": height},
        "cropBox": {
            "x": box[0],
            "y": box[1],
            "width": box[2] - box[0],
            "height": box[3] - box[1],
        },
        "targetAspect": round(aspect, 6),
        "photoScore": round(score, 4),
        "photoRich": photo_rich,
        "strategy": "photo-rich-window" if photo_rich else "text-dominant-fallback",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--target-aspect", type=parse_aspect, default=DEFAULT_ASPECT)
    parser.add_argument("--min-photo-score", type=float, default=DEFAULT_MIN_PHOTO_SCORE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    metadata = prepare_display_image(
        args.input,
        args.output,
        aspect=args.target_aspect,
        min_photo_score=max(0.0, min(1.0, args.min_photo_score)),
    )
    print(json.dumps(metadata, ensure_ascii=False) if args.json else metadata["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
