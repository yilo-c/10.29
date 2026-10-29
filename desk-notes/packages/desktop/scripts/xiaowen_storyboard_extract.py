"""Extract XiaoWen pet frames from the two ChatGPT storyboard images.

Storyboard layout (verified 2026-08-07):
  ChatGPT Image ...04_23_24.png:
    band1 = 站立(待机) 4 帧 + 眨眼 4 帧，同一行共 8 格
    band4 = 挥剑 7 帧
    band6 = 轻跳 6 帧
  ChatGPT Image ...04_59_20.png:
    band1 = 受惊拔剑(单击) 6 帧 + 对白气泡
    band4 = 害羞捂脸(双击) 6 帧 + 对白气泡
    band7 = 御剑飞行(拖拽) 6 帧，取前 4 帧作为 dragFly

Mapping to clips:
  idle(4) / notice(4) ← 0423 band1 前 4 格 / 后 4 格
  combo3(7)           ← 0423 band4 挥剑
  land(6)             ← 0423 band6 轻跳
  click_defend(6)     ← 0459 band1 受惊拔剑
  shy(6)              ← 0459 band4 害羞捂脸
  drag_fly(4)         ← 0459 band7 御剑飞行 ①-④

Pipeline per panel:
1. Locate row bands via row projection, then panels via connected components.
2. Keep the largest connected blob per panel (character), dropping circled
   frame numbers, labels, and speech bubbles.
3. Luminance-based matte to remove the black storyboard background.
4. Dark-fringe cleanup (prevents "black dots" on transparent desktop windows).
5. Normalize onto a 256x256 canvas with a PER-CLIP uniform scale (median
   character height → 196px), horizontally centered, bottom (feet/base)
   anchored at y=224 — consistent with the xiaowen frame spec
   (256x256 canvas, safePadding 16, 180-220px character height).

Usage:
    python xiaowen_storyboard_extract.py [--dry-run]
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
PET_DIR = ROOT / "public" / "pet"
FRAMES = PET_DIR / "xiaowen" / "frames"

BOARD_IDLE = PET_DIR / "ChatGPT Image 2026年5月18日 04_23_24.png"
BOARD_INTERACT = PET_DIR / "ChatGPT Image 2026年5月18日 04_59_20.png"

CANVAS = 256
TARGET_CHAR_HEIGHT = 196  # spec: 180-220px visual height
ANCHOR_BOTTOM = 224       # feet/base anchor row (256 - 32 safe margin)
MAX_CHAR_WIDTH = CANVAS - 32

# Matting thresholds (luminance 0-255 on black background)
CONTENT_LUM_MIN = 22   # above background noise floor → silhouette content
EDGE_SOFT_FLOOR = 8.0  # edge-band luminance below this → transparent
EDGE_SOFT_CEIL = 95.0  # edge-band luminance above this → opaque

# Content mask threshold: channel sum (>~23 avg) keeps navy/black outfit
# and sword parts while excluding pure-black background and light noise.
CONTENT_SUM_MIN = 70


@dataclass(frozen=True)
class ClipPlan:
    board: Path
    band_index: int
    take: slice            # which panels of the band to use
    clip: str              # frames/ subdirectory
    prefix: str            # frame file prefix


PLANS: list[ClipPlan] = [
    ClipPlan(BOARD_IDLE, 1, slice(0, 4), "idle", "idle"),
    ClipPlan(BOARD_IDLE, 1, slice(4, 8), "notice", "notice"),
    ClipPlan(BOARD_IDLE, 4, slice(0, 7), "combo3", "combo3"),
    ClipPlan(BOARD_IDLE, 6, slice(0, 6), "land", "land"),
    ClipPlan(BOARD_INTERACT, 1, slice(0, 6), "click_defend", "click_defend"),
    ClipPlan(BOARD_INTERACT, 4, slice(0, 6), "shy", "shy"),
    ClipPlan(BOARD_INTERACT, 7, slice(0, 4), "drag_fly", "drag_fly"),
]


def find_row_bands(mask: np.ndarray, min_width: int = 40, min_height: int = 12) -> list[tuple[int, int]]:
    rows = mask.sum(axis=1)
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(rows):
        if v > min_width and start is None:
            start = i
        elif v <= min_width and start is not None:
            if i - start >= min_height:
                bands.append((start, i))
            start = None
    if start is not None and len(rows) - start >= min_height:
        bands.append((start, len(rows)))
    return bands


def find_panels(mask: np.ndarray, band: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """Return panel bboxes (x0, y0, x1, y1) inside a row band, sorted by x.

    Connected components on the dilated content mask handle single-row bands;
    small blobs (circled numbers, stray punctuation) are filtered by size.
    """
    y0, y1 = band
    py0, py1 = max(0, y0 - 12), min(mask.shape[0], y1 + 12)
    strip_mask = mask[py0:py1, :]

    labels, n = ndimage.label(ndimage.binary_dilation(strip_mask, iterations=2))
    boxes: list[tuple[int, int, int, int]] = []
    for lab in range(1, n + 1):
        ys, xs = np.where(labels == lab)
        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if w < 60 or h < 60:
            continue
        boxes.append((int(xs.min()), int(ys.min()) + py0, int(xs.max()) + 1, int(ys.max()) + 1 + py0))
    boxes.sort(key=lambda b: b[0])
    return boxes


def fill_small_holes(blob: np.ndarray, rgb: np.ndarray, max_area: int = 400):
    """Inpaint tiny enclosed gaps (hair-strand separations) with the mean
    color of their neighboring pixels.

    Leaving them transparent renders as dark dots on dark desktops;
    filling with the raw black background would be worse, so we sample
    the surrounding character color. Large openings (real limb gaps)
    stay untouched.
    """
    filled = ndimage.binary_fill_holes(blob)
    holes = filled & ~blob
    if not holes.any():
        return blob, rgb
    labels, n = ndimage.label(holes)
    rgb_out = rgb.astype(np.float64).copy()
    new_blob = blob.copy()
    for i in range(1, n + 1):
        m = labels == i
        if m.sum() > max_area:
            continue
        ring = ndimage.binary_dilation(m, iterations=4) & blob
        if not ring.any():
            continue
        rgb_out[m] = rgb[ring].astype(np.float64).mean(axis=0)
        new_blob |= m
    return new_blob, rgb_out


def matte_from_black(rgb: np.ndarray) -> np.ndarray:
    """Silhouette-based matte that preserves dark character parts.

    A plain luminance matte destroys dark-but-opaque regions (navy skirt,
    black sword, pupils) because they look like the black background.
    Instead: build the silhouette from a connected-component content mask,
    keep the interior fully opaque, and only derive soft alpha on the
    2px edge band (un-premultiplied to avoid dark halos).
    """
    lum = rgb.astype(np.float64).mean(axis=2)
    content = lum > CONTENT_LUM_MIN
    labels, n = ndimage.label(ndimage.binary_dilation(content, iterations=1))
    if n == 0:
        out = np.zeros((*rgb.shape[:2], 4), dtype=np.uint8)
        return out
    sizes = ndimage.sum(np.ones_like(labels), labels, range(1, n + 1))
    blob = labels == (int(np.argmax(sizes)) + 1)
    blob, rgb = fill_small_holes(blob, rgb)

    interior = ndimage.binary_erosion(blob, iterations=2)
    edge = blob & ~interior

    alpha = np.zeros(rgb.shape[:2], dtype=np.float64)
    alpha[interior] = 255.0
    # soft anti-aliased edge from luminance (background is pure black)
    soft = np.clip((lum - EDGE_SOFT_FLOOR) / (EDGE_SOFT_CEIL - EDGE_SOFT_FLOOR), 0.0, 1.0) ** 0.7
    alpha[edge] = soft[edge] * 255.0

    # un-premultiply edge colors: observed = F * a on black background,
    # so recover F to prevent dark fringing on transparent desktops
    rgb_out = rgb.astype(np.float64).copy()
    a_norm = np.clip(alpha / 255.0, 0.12, 1.0)
    rgb_out[edge] = rgb_out[edge] / a_norm[edge, None]

    rgba = np.dstack([
        np.clip(rgb_out, 0, 255).astype(np.uint8),
        alpha.astype(np.uint8),
    ])
    # outside the silhouette: fully transparent and clean
    outside = ~blob
    rgba[outside] = (0, 0, 0, 0)
    return rgba


def clean_noise(rgba: np.ndarray) -> np.ndarray:
    """Light cleanup: drop near-invisible noise, zero RGB where transparent."""
    out = rgba.copy()
    a = out[:, :, 3].astype(np.int32)
    out[a <= 6] = (0, 0, 0, 0)
    out[out[:, :, 3] == 0, :3] = 0
    return np.clip(out, 0, 255).astype(np.uint8)


def strip_small_parts(rgba: np.ndarray, keep_ratio: float = 0.15) -> np.ndarray:
    """Drop small disconnected parts merged into the main blob.

    Targets yellow exclamation marks / sparkle stars that touch the
    character via anti-aliased pixels. Sword, sword aura and body-sized
    parts are kept (they exceed keep_ratio of the main blob area).
    """
    alpha = rgba[:, :, 3] > 8
    labels, n = ndimage.label(alpha)
    if n <= 1:
        return rgba
    sizes = ndimage.sum(np.ones_like(labels), labels, range(1, n + 1))
    main_size = float(max(sizes))
    out = rgba.copy()
    for lab in range(1, n + 1):
        if sizes[lab - 1] < main_size * keep_ratio:
            out[labels == lab] = (0, 0, 0, 0)
    return out


def extract_character(img: Image.Image, box: tuple[int, int, int, int]) -> Image.Image | None:
    """Crop panel, keep the character blob, matte + clean, return RGBA crop."""
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
    x1, y1 = min(img.width, x1 + 2), min(img.height, y1 + 2)
    panel = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.int32)
    content = panel.sum(axis=2) > CONTENT_SUM_MIN

    # gentle dilation (1px) bridges anti-aliasing gaps without merging
    # nearby separate blobs such as circled frame numbers or speech bubbles
    labels, n = ndimage.label(ndimage.binary_dilation(content, iterations=1))
    if n == 0:
        return None
    sizes = ndimage.sum(np.ones_like(labels), labels, range(1, n + 1))
    main_lab = int(np.argmax(sizes)) + 1
    blob_mask = labels == main_lab

    ys, xs = np.where(blob_mask)
    bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
    crop_rgb = panel[by0:by1 + 1, bx0:bx1 + 1]
    rgba = strip_small_parts(clean_noise(matte_from_black(crop_rgb)))

    alpha_mask = rgba[:, :, 3] > 8
    if not alpha_mask.any():
        return None
    ys, xs = np.where(alpha_mask)
    rgba = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return Image.fromarray(rgba, "RGBA")


def place_on_canvas(char: Image.Image, scale: float) -> Image.Image:
    """Resize with a shared clip scale, center, anchor bottom at ANCHOR_BOTTOM."""
    new_w = max(1, round(char.width * scale))
    new_h = max(1, round(char.height * scale))
    if new_w > MAX_CHAR_WIDTH:  # guard: never overflow canvas width
        shrink = MAX_CHAR_WIDTH / new_w
        new_w = MAX_CHAR_WIDTH
        new_h = max(1, round(new_h * shrink))
    resized = char.resize((new_w, new_h), Image.Resampling.LANCZOS)
    # drop resampling noise only (dark interior pixels are legitimate now)
    resized = Image.fromarray(clean_noise(np.asarray(resized)), "RGBA")

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    dx = (CANVAS - new_w) // 2
    dy = max(0, ANCHOR_BOTTOM - new_h)
    canvas.alpha_composite(resized, (dx, dy))
    return canvas


def run(dry_run: bool) -> int:
    cache: dict[Path, Image.Image] = {}

    def board(p: Path) -> Image.Image:
        if p not in cache:
            if not p.exists():
                raise FileNotFoundError(p)
            cache[p] = Image.open(p).convert("RGB")
        return cache[p]

    total = 0
    for plan in PLANS:
        img = board(plan.board)
        mask = np.asarray(img).sum(axis=2) > 220
        bands = find_row_bands(mask)
        if plan.band_index >= len(bands):
            print(f"[FAIL] {plan.clip}: band {plan.band_index} missing ({len(bands)} bands)")
            return 1
        panels = find_panels(mask, bands[plan.band_index])
        expected = plan.take.stop - plan.take.start
        if len(panels) < plan.take.stop:
            print(f"[FAIL] {plan.clip}: band {plan.band_index} has {len(panels)} panels, need {plan.take.stop}")
            for i, b in enumerate(panels):
                print(f"    panel{i}: {b}")
            return 1
        chosen = panels[plan.take]

        chars = []
        for box in chosen:
            char = extract_character(img, box)
            if char is None:
                print(f"[FAIL] {plan.clip}: empty extraction at {box}")
                return 1
            chars.append(char)

        # per-clip uniform scale → no size jitter between frames;
        # cap so the tallest frame never overshoots the bottom anchor
        max_h = max(c.height for c in chars)
        scale = min(
            TARGET_CHAR_HEIGHT / statistics.median(c.height for c in chars),
            (ANCHOR_BOTTOM - 4) / max_h,
        )
        print(f"[{plan.clip}] panels={len(panels)} used={len(chosen)} "
              f"char_heights={[c.height for c in chars]} scale={scale:.3f}")

        out_dir = FRAMES / plan.clip
        # purge stale frames from previous generations first
        if not dry_run and out_dir.exists():
            for stale in out_dir.glob("*.png"):
                stale.unlink()
        for i, char in enumerate(chars, start=1):
            frame = place_on_canvas(char, scale)
            target = out_dir / f"{plan.prefix}_{i:02d}.png"
            total += 1
            if dry_run:
                print(f"  would write {target.relative_to(ROOT)}  char={char.size}")
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                frame.save(target)
                print(f"  wrote {target.relative_to(ROOT)}  char={char.size}")
    print(f"\nDone: {total} frames across {len(PLANS)} clips.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="locate panels only, do not write")
    args = parser.parse_args()
    raise SystemExit(run(args.dry_run))
