"""Prepare layers for the procedural XiaoWen idle avatar (v2).

Inputs (public/pet/xiaowen/references/):
  xiaowen_layer_base.png      — D1 idle base (eyes open)
  xiaowen_idle_closedeyes.png — D2 closed-eye variant (may carry a small
                                global offset due to GPT redraw drift)

Outputs (public/pet/xiaowen/layers/):
  body.png            — full character silhouette layer
  hair_back.png       — head-band hair region minus face ellipse (sway layer)
  eyes_closed_L.png   — closed-eye patch aligned to D1 coords (feathered)
  eyes_closed_R.png
  xiaowen_layers.json — anchors, eyes, sparkle zone, physics params

Eye anchors are one-time calibrated (template-match verified) and hardcoded;
the D2→D1 alignment offset is computed automatically via NCC each run.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.signal import fftconvolve

ROOT = Path(__file__).resolve().parents[1]
PET = ROOT / "public" / "pet" / "xiaowen"
REF = PET / "references"
OUT = PET / "layers"

BASE = REF / "xiaowen_layer_base.png"
CLOSED = REF / "xiaowen_idle_closedeyes.png"

BG_DIST_MIN = 45

# ── one-time calibration for D1 (xiaowen_layer_base.png, 1122×1402) ──
# eyes: left iris cluster + right eye via NCC template match (NCC 0.475)
CAL_EYES = [
    {"cx": 379, "cy": 351, "w": 118, "h": 152},
    {"cx": 655, "cy": 416, "w": 118, "h": 152},
]
CAL_FACE = {"cx": 520, "cy": 430, "rx": 190, "ry": 205}
CAL_HAIR_PIVOT = [520, 130]
EYE_PATCH_MARGIN = 16
EYE_FEATHER = 14


def silhouette(rgb: np.ndarray) -> np.ndarray:
    bg = rgb[0:24, 0:24].mean(axis=(0, 1))
    char = np.linalg.norm(rgb - bg, axis=2) > BG_DIST_MIN
    char = ndimage.binary_fill_holes(char)
    char = ndimage.binary_opening(char, iterations=1)
    # keep only the largest blob (character), drop stray marks
    labels, n = ndimage.label(char)
    if n > 1:
        sizes = ndimage.sum(np.ones_like(labels), labels, range(1, n + 1))
        char = labels == (int(np.argmax(sizes)) + 1)
    return char


def layer_from_mask(rgb_u8: np.ndarray, mask: np.ndarray) -> Image.Image:
    rgba = np.zeros((*rgb_u8.shape[:2], 4), dtype=np.uint8)
    rgba[:, :, :3] = rgb_u8
    rgba[:, :, 3] = (mask * 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def find_sword_zone(char: np.ndarray, lum: np.ndarray) -> dict | None:
    """Tallest, thin dark blob = the black sword (character's right hand
    is on the viewer's left, so no side assumption is made)."""
    dark = char & (lum < 70)
    labels, n = ndimage.label(dark)
    best = None
    for i in range(1, n + 1):
        m = labels == i
        ys, xs = np.where(m)
        bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if bh < 400 or bh < bw * 3.0:   # must be tall AND thin
            continue
        score = bh
        if best is None or score > best[0]:
            best = (score, (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
    if not best:
        return None
    x0, y0, x1, y1 = best[1]
    # sparkle segment: blade body, excluding grip (top) and ground tip (bottom)
    return {"x0": x0, "y0": y0 + 80, "x1": x1, "y1": y1 - 60}


def align_closed_eye(base_gray: np.ndarray, closed_gray: np.ndarray) -> tuple[int, int]:
    """Global (dx, dy) so that closed[p + (dx,dy)] == base[p].

    NCC search over ±50px using the head/upper-torso region as template.
    """
    ty0, ty1, tx0, tx1 = 250, 600, 330, 720
    tmpl = base_gray[ty0:ty1, tx0:tx1]
    tmpl = tmpl - tmpl.mean()
    # search window in D2 (template anchor can shift ±50)
    sy0, sx0 = ty0 - 50, tx0 - 50
    sy1, sx1 = ty1 + 50, tx1 + 50
    region = closed_gray[sy0:sy1, sx0:sx1]
    region = region - region.mean()
    corr = fftconvolve(region, tmpl[::-1, ::-1], mode="valid")
    energy = fftconvolve(region ** 2, np.ones_like(tmpl), mode="valid")
    ncc = corr / (np.sqrt(energy) * np.sqrt((tmpl ** 2).sum()) + 1e-9)
    iy, ix = np.unravel_index(np.argmax(ncc), ncc.shape)
    score = float(ncc[iy, ix])
    # feature at base position p lives at p + offset in D2:
    # match anchor in region coords → offset = (sy0+iy-ty0, sx0+ix-tx0)
    dy, dx = sy0 + iy - ty0, sx0 + ix - tx0
    print(f"D2→D1 alignment offset: dx={dx} dy={dy} (NCC={score:.3f})")
    if abs(dx) > 40 or abs(dy) > 40 or score < 0.4:
        print("WARN: alignment out of expected range")
    return int(dx), int(dy)


def refine_eye_local(closed_gray: np.ndarray, tmpl_center: tuple[int, int],
                     search: tuple[int, int, int, int]) -> tuple[int, int] | None:
    """Local NCC refinement: locate a matching eye region in D2.

    GPT redraw drift is not always a pure global shift (heads get
    rescaled/rotated slightly), so each eye patch position is refined
    with a ±search window around its globally predicted location.
    """
    tcx, tcy = tmpl_center
    th, tw = 160, 130
    ty0, tx0 = tcy - th // 2, tcx - tw // 2
    tmpl = closed_gray[ty0:ty0 + th, tx0:tx0 + tw]
    if tmpl.shape[0] < th or tmpl.shape[1] < tw:
        return None
    tmpl = tmpl - tmpl.mean()
    sy0, sy1, sx0, sx1 = search
    region = closed_gray[sy0:sy1, sx0:sx1]
    if region.shape[0] <= th or region.shape[1] <= tw:
        return None
    region = region - region.mean()
    corr = fftconvolve(region, tmpl[::-1, ::-1], mode="valid")
    energy = fftconvolve(region ** 2, np.ones_like(tmpl), mode="valid")
    ncc = corr / (np.sqrt(energy) * np.sqrt((tmpl ** 2).sum()) + 1e-9)
    iy, ix = np.unravel_index(np.argmax(ncc), ncc.shape)
    score = float(ncc[iy, ix])
    cx, cy = sx0 + ix + tw // 2, sy0 + iy + th // 2
    print(f"  local refine -> ({cx},{cy}) NCC={score:.3f}")
    if score < 0.3:
        return None
    return cx, cy


def feather_patch(patch: np.ndarray, margin: int) -> np.ndarray:
    """Cosine alpha ramp on the patch border so edges blend invisibly."""
    ph, pw = patch.shape[:2]
    ramp = np.ones((ph, pw), dtype=np.float64)
    for i in range(margin):
        t = 0.5 - 0.5 * np.cos(np.pi * (i + 0.5) / margin)  # 0→1
        ramp[i, :] *= t
        ramp[ph - 1 - i, :] *= t
        ramp[:, i] *= t
        ramp[:, pw - 1 - i] *= t
    out = patch.copy()
    out[:, :, 3] = (out[:, :, 3] * ramp).astype(np.uint8)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.open(BASE).convert("RGB")
    rgb = np.asarray(img, dtype=np.float64)
    rgb_u8 = rgb.astype(np.uint8)
    h, w, _ = rgb.shape
    lum = rgb.mean(axis=2)

    char = silhouette(rgb)
    ys, xs = np.where(char)
    cx0, cy0, cx1, cy1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    print(f"char bbox=({cx0},{cy0})-({cx1},{cy1})")

    # ── hair_back: head band minus face ellipse ──
    fcx, fcy = CAL_FACE["cx"], CAL_FACE["cy"]
    frx, fry = CAL_FACE["rx"], CAL_FACE["ry"]
    yy, xx = np.mgrid[0:h, 0:w]
    face_ell = ((xx - fcx) / frx) ** 2 + ((yy - fcy) / fry) ** 2 <= 1.0
    head_bottom = cy0 + int((cy1 - cy0) * 0.44)
    head_band = np.zeros((h, w), dtype=bool)
    head_band[cy0:head_bottom, :] = True
    hair_back = char & head_band & ~face_ell
    labels, n = ndimage.label(hair_back)
    if n:
        top_seed = np.zeros_like(hair_back)
        top_seed[cy0:cy0 + int((cy1 - cy0) * 0.08), cx0:cx1 + 1] = True
        keep = set(np.unique(labels[top_seed & (labels > 0)])) - {0}
        hair_back = np.isin(labels, list(keep)) if keep else hair_back
    print(f"hair_back={int(hair_back.sum())} px")

    # ── sword zone (sparkle anchor) ──
    sword = find_sword_zone(char, lum)
    print(f"sword zone: {sword}")

    # ── eye validation ──
    eyes = []
    for e in CAL_EYES:
        patch = lum[e["cy"] - 20:e["cy"] + 20, e["cx"] - 20:e["cx"] + 20]
        ok = bool((patch < 160).any())
        eyes.append(dict(e, valid=ok))
        if not ok:
            print(f"WARN: eye anchor {e} not on eye-like pixels")
    print(f"eyes: {eyes}")

    # ── closed-eye patches (aligned from D2) ──
    closed_img = Image.open(CLOSED).convert("RGB")
    closed_gray = np.asarray(closed_img, dtype=np.float64).mean(axis=2)
    base_gray = lum
    dx, dy = align_closed_eye(base_gray, closed_gray)
    closed_full = np.asarray(closed_img.convert("RGBA"), dtype=np.uint8)
    patches = []
    # predicted D2 position per eye (global offset), then local refinement:
    # right eye refines itself; left eye matches against the right-eye crop
    # (closed eyes look alike, and D2 drift is not a pure global shift)
    pred = [(e["cx"] + dx, e["cy"] + dy) for e in CAL_EYES]
    r_pred = pred[1]
    refined = [None, None]
    lpos = refine_eye_local(closed_gray, r_pred, (250, 560, 300, 640))
    refined[0] = lpos if lpos else pred[0]
    rpos = refine_eye_local(closed_gray, r_pred, (r_pred[1] - 90, r_pred[1] + 90,
                                                  r_pred[0] - 90, r_pred[0] + 90))
    refined[1] = rpos if rpos else pred[1]
    for idx, e in enumerate(CAL_EYES):
        m = EYE_PATCH_MARGIN
        ecx, ecy = refined[idx]
        x0 = ecx - e["w"] // 2 - m
        y0 = ecy - e["h"] // 2 - m
        pw = e["w"] + 2 * m
        ph = e["h"] + 2 * m
        if x0 < 0 or y0 < 0 or x0 + pw > w or y0 + ph > closed_full.shape[0]:
            print(f"WARN: eye {idx} patch out of D2 bounds, skipping")
            continue
        patch = closed_full[y0:y0 + ph, x0:x0 + pw].copy()
        patch = feather_patch(patch, EYE_FEATHER)
        name = f"eyes_closed_{'L' if idx == 0 else 'R'}.png"
        Image.fromarray(patch, "RGBA").save(OUT / name)
        patches.append({"file": f"layers/{name}",
                        "x": e["cx"] - e["w"] // 2 - m,   # D1-space position
                        "y": e["cy"] - e["h"] // 2 - m,
                        "w": pw, "h": ph})
    print(f"closed-eye patches: {len(patches)}")

    # ── save main layers ──
    layer_from_mask(rgb_u8, char).save(OUT / "body.png")
    layer_from_mask(rgb_u8, hair_back).save(OUT / "hair_back.png")

    # ── manifest ──
    manifest = {
        "base": "references/xiaowen_layer_base.png",
        "sourceSize": [w, h],
        "charBBox": [cx0, cy0, cx1, cy1],
        "drawOrder": ["hair_back", "body"],
        "hairPivot": CAL_HAIR_PIVOT,
        "faceEllipse": CAL_FACE,
        "eyes": eyes,
        "eyesClosed": patches,
        "swordZone": sword,
        "physics": {
            "breath": {"amplitudePx": 7, "periodSec": 3.2},
            "hairSway": {"stiffness": 0.06, "damping": 0.92, "maxAngleRad": 0.05},
            "blink": {"intervalMs": [2200, 5500], "closeDurationMs": 130,
                      "doubleBlinkChance": 0.15},
            "sway": {"maxAngleRad": 0.009, "periodSec": [6, 9]},
            "droplets": {
                "count": 8,
                "orbitPeriodSec": [6, 9],
                "fadeInMs": 1000,
                "holdMs": [8000, 12000],
                "fadeOutMs": 1500,
                "restMs": [18000, 30000],
            },
            "sparkle": {"intervalMs": [8000, 15000], "perBurst": [2, 3], "lifeMs": 800},
        },
    }
    (OUT / "xiaowen_layers.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("layers + manifest saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
