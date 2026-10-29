"""Generate and validate the minimal XiaoWen pet frame set.

This intentionally derives missing MVP frames from existing transparent XiaoWen
frames instead of introducing a new art style. It also enforces alpha hygiene so
pet frames do not rely on white backgrounds.
"""

from __future__ import annotations

import json
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageEnhance


ROOT = Path(__file__).resolve().parents[1]
PET_ROOT = ROOT / "public" / "pet" / "xiaowen"
FRAMES = PET_ROOT / "frames"
QA_DIR = PET_ROOT / "qa"


@dataclass(frozen=True)
class FrameSpec:
    action: str
    name: str
    source: Path
    angle: float = 0
    scale: float = 1
    offset: tuple[int, int] = (0, 0)
    brightness: float = 1
    contrast: float = 1
    flip: bool = False


def open_rgba(path: Path) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGBA").copy()


def clean_alpha(img: Image.Image) -> Image.Image:
    """Spatial-aware alpha cleaning.

    Three-layer strategy (per project spec memory):
    1. alpha <= 3  → fully transparent (remove noise)
    2. alpha < 80, white-ish, outside character → remove (background residue)
    3. alpha < 200, white-ish, outside character edge band → reduce alpha (white halo)
    White pixels INSIDE the character region (clothing) are always preserved.
    """
    img = img.convert("RGBA")
    pixels = img.load()
    width, height = img.size

    # ── Build opaque mask (alpha > 200) ──
    opaque = [[False] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            if pixels[x, y][3] > 200:
                opaque[y][x] = True

    # ── Character bounding box ──
    min_x, min_y, max_x, max_y = width, height, 0, 0
    for y in range(height):
        for x in range(width):
            if opaque[y][x]:
                if x < min_x: min_x = x
                if y < min_y: min_y = y
                if x > max_x: max_x = x
                if y > max_y: max_y = y

    if max_x < min_x:
        return img  # no opaque content

    # ── Edge band: 2px dilation of character area ──
    edge_band = [[False] * width for _ in range(height)]
    for y in range(max(0, min_y - 2), min(height, max_y + 3)):
        for x in range(max(0, min_x - 2), min(width, max_x + 3)):
            if opaque[y][x]:
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < height and 0 <= nx < width:
                            edge_band[ny][nx] = True

    # ── Clean ──
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                # Layer 1: zero RGB for fully transparent pixels
                if r or g or b:
                    pixels[x, y] = (0, 0, 0, 0)
                continue

            cmin, cmax = min(r, g, b), max(r, g, b)
            is_whiteish = cmin > 210 and (cmax - cmin) < 30
            if not is_whiteish:
                continue

            if a <= 3:
                # Layer 1: near-transparent → remove
                pixels[x, y] = (0, 0, 0, 0)
            elif not opaque[y][x] and not edge_band[y][x]:
                # Outside character + edge band → background residue
                if a < 80:
                    pixels[x, y] = (0, 0, 0, 0)
                else:
                    pixels[x, y] = (r, g, b, max(0, a - 60))
            elif not opaque[y][x] and edge_band[y][x]:
                # In edge band (anti-aliasing zone) → gentle cleanup
                if a < 40:
                    pixels[x, y] = (0, 0, 0, 0)
                elif a < 120:
                    pixels[x, y] = (r, g, b, max(0, a - 20))

    normalized = Image.new("RGBA", img.size, (0, 0, 0, 0))
    normalized.alpha_composite(img)
    return normalized


def normalize_png(path: Path) -> bool:
    try:
        clean_alpha(open_rgba(path)).save(path)
        return True
    except PermissionError:
        return False


def fit_canvas(img: Image.Image, scale: float, angle: float, offset: tuple[int, int], flip: bool) -> Image.Image:
    base = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else img
    if scale != 1:
        size = (max(1, round(base.width * scale)), max(1, round(base.height * scale)))
        base = base.resize(size, Image.Resampling.LANCZOS)
    if angle:
        base = base.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    x = (256 - base.width) // 2 + offset[0]
    y = (256 - base.height) // 2 + offset[1]
    canvas.alpha_composite(base, (x, y))
    return clean_alpha(canvas)


def derive(spec: FrameSpec, force: bool) -> None:
    target_dir = FRAMES / spec.action
    target = target_dir / spec.name
    if target.exists() and not force:
        return
    img = open_rgba(spec.source)
    if spec.brightness != 1:
        img = ImageEnhance.Brightness(img).enhance(spec.brightness)
    if spec.contrast != 1:
        img = ImageEnhance.Contrast(img).enhance(spec.contrast)
    out = fit_canvas(img, spec.scale, spec.angle, spec.offset, spec.flip)
    target_dir.mkdir(parents=True, exist_ok=True)
    out.save(target)


def source(action: str, name: str) -> Path:
    return FRAMES / action / name


def specs() -> list[FrameSpec]:
    # All MVP frames are now produced from the storyboard images by
    # xiaowen_storyboard_extract.py; geometric derivation is deprecated.
    return []


def alpha_stats(path: Path) -> dict[str, object]:
    img = open_rgba(path)
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    transparent_bad = 0
    weak_white = 0
    opaque_white_border = 0
    pixels = img.load()
    width, height = img.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0 and (r or g or b):
                transparent_bad += 1
            if 0 < a < 96 and r > 235 and g > 235 and b > 235:
                weak_white += 1
            if (x < 2 or y < 2 or x >= width - 2 or y >= height - 2) and a > 10 and r > 245 and g > 245 and b > 245:
                opaque_white_border += 1
    return {
        "path": str(path.relative_to(ROOT)),
        "size": img.size,
        "bbox": bbox,
        "transparent_rgb_residue": transparent_bad,
        "semi_transparent_white_pixels": weak_white,
        "opaque_white_border_pixels": opaque_white_border,
        "ok": bool(bbox) and transparent_bad == 0 and opaque_white_border == 0,
    }


def validate(paths: Iterable[Path]) -> list[dict[str, object]]:
    return [alpha_stats(path) for path in sorted(paths)]


def contact_sheet(paths: list[Path], output: Path) -> None:
    cells = []
    for path in paths:
        img = open_rgba(path).resize((128, 128), Image.Resampling.LANCZOS)
        bg = Image.new("RGBA", (128, 128), (35, 42, 52, 255))
        check = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        for y in range(0, 128, 16):
            for x in range(0, 128, 16):
                c = (48, 56, 68, 255) if (x // 16 + y // 16) % 2 else (30, 36, 45, 255)
                Image.Image.paste(check, Image.new("RGBA", (16, 16), c), (x, y))
        bg = Image.alpha_composite(bg, check)
        bg.alpha_composite(img, (0, 0))
        cells.append(bg.convert("RGB"))
    if not cells:
        return
    cols = 6
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 128, rows * 128), (20, 24, 32))
    for i, cell in enumerate(cells):
        sheet.paste(cell, ((i % cols) * 128, (i // cols) * 128))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true", help="generate missing MVP frames before validating")
    parser.add_argument("--force", action="store_true", help="overwrite existing generated MVP frames")
    parser.add_argument("--write-report", action="store_true", help="write alpha-report.json and contact sheet")
    args = parser.parse_args()

    skipped_normalize: list[str] = []
    if args.generate:
        for path in FRAMES.glob("*/*.png"):
            if not normalize_png(path):
                skipped_normalize.append(str(path.relative_to(ROOT)))

        for spec in specs():
            if not spec.source.exists():
                raise FileNotFoundError(spec.source)
            derive(spec, force=args.force)

    all_png = list((FRAMES).glob("*/*.png"))
    report = validate(all_png)
    generated_paths = {
        str((FRAMES / spec.action / spec.name).relative_to(ROOT))
        for spec in specs()
    }
    if args.generate or args.write_report:
        QA_DIR.mkdir(parents=True, exist_ok=True)
        (QA_DIR / "alpha-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        contact_sheet(all_png, QA_DIR / "minimal-mvp-contact-sheet.png")

    bad = [item for item in report if not item["ok"] and item["path"] in generated_paths]
    if bad:
        print(f"Generated frames, but alpha QA found {len(bad)} generated-frame issue(s). See {QA_DIR / 'alpha-report.json'}")
        return 1
    warnings = [item for item in report if not item["ok"] and item["path"] not in generated_paths]
    if args.generate:
        print(f"Generated {len(specs())} MVP frame specs. Alpha QA passed for generated frames.")
    else:
        print("Alpha QA passed for generated MVP frames.")
    if skipped_normalize:
        print(f"Skipped alpha normalization for {len(skipped_normalize)} locked existing frame(s).")
    if warnings:
        print(f"Existing-frame alpha warnings: {len(warnings)}. See {QA_DIR / 'alpha-report.json'}")
    if args.generate or args.write_report:
        print(f"Contact sheet: {QA_DIR / 'minimal-mvp-contact-sheet.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
