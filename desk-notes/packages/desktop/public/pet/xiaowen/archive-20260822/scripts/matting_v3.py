# -*- coding: utf-8 -*-
"""matting v3：trimap 融合
- definite BG = flood 局部生长到达 且 u2<0.3（防漏：漏入区 u2 高 → 不算背景）
- definite FG = u2>0.7（稳定，救漏）
- unknown 窄带 = u2 插值（自然 AA，halo 窄）
- 边缘颜色 unmix 去污"""
import os
from collections import deque
import numpy as np
from PIL import Image
from scipy import ndimage

SRC = r'D:\h\3.25\tmp_pet_test\idle_v2'
OUT = r'D:\h\3.25\tmp_pet_test\idle_v3\raw'
os.makedirs(OUT, exist_ok=True)

_u2 = None


def u2_soft(rgb):
    global _u2
    if _u2 is None:
        from rembg import new_session
        _u2 = new_session('u2net')
    from rembg import remove
    m = remove(Image.fromarray(rgb.astype(np.uint8)), session=_u2, only_mask=True)
    return np.asarray(m).astype(np.float32) / 255.0


def flood_bg(rgb, local_tol=40):
    rgb = ndimage.median_filter(rgb, size=(3, 3, 1))
    h, w, _ = rgb.shape
    arr = rgb.astype(np.float32)
    visited = np.zeros((h, w), bool)
    q = deque()
    for j in range(w):
        for i in (0, h - 1):
            visited[i, j] = True; q.append((i, j))
    for i in range(h):
        for j in (0, w - 1):
            visited[i, j] = True; q.append((i, j))
    while q:
        i, j = q.popleft()
        c = arr[i, j]
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                ni, nj = i + di, j + dj
                if 0 <= ni < h and 0 <= nj < w and not visited[ni, nj]:
                    if np.sqrt(((arr[ni, nj] - c) ** 2).sum()) < local_tol:
                        visited[ni, nj] = True; q.append((ni, nj))
    return visited


def process(fname):
    im0 = Image.open(os.path.join(SRC, fname)).convert('RGB')
    # 速度优化：最终输出 256，matting 在 512 长边做即可（BFS/u2net 均 4x 快）
    im0.thumbnail((512, 512), Image.LANCZOS)
    im = np.asarray(im0)
    rgb = im.astype(np.float32)
    u2 = u2_soft(im)
    fb = flood_bg(im)

    def_bg = fb & (u2 < 0.3)
    def_fg = (u2 > 0.7)
    unknown = ~(def_bg | def_fg)

    alpha = np.zeros(rgb.shape[:2], np.float32)
    alpha[def_fg] = 1.0
    alpha[unknown] = np.clip((u2[unknown] - 0.3) / 0.4, 0.0, 1.0)  # 0.3→0, 0.7→1
    # unknown 里被 flood 到达的（背景侧）压更低，未到达的（角色侧）抬高
    reached = unknown & fb
    alpha[reached] = np.minimum(alpha[reached], 0.25)

    # 去散点：主体连通域保留
    fg_hard = alpha > 0.15
    lab, n = ndimage.label(fg_hard)
    if n > 1:
        sizes = ndimage.sum(fg_hard, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes > 500
        valid = keep[lab]
        alpha[~valid] = 0

    # 颜色 unmix 去污（半透明像素扣除背景混色）
    bg_color = rgb[def_bg].mean(axis=0) if def_bg.any() else np.array([223, 223, 221])
    a3 = alpha[:, :, None]
    safe = np.clip(a3, 0.12, 1.0)
    rgb_clean = np.clip((rgb - bg_color * (1 - a3)) / safe, 0, 255)
    rgb_out = np.where(a3 > 0, rgb_clean, rgb)

    rgba = np.dstack([rgb_out.astype(np.uint8), (alpha * 255).astype(np.uint8)])
    out = Image.fromarray(rgba, 'RGBA')
    bbox = out.getchannel('A').getbbox()
    if bbox:
        out = out.crop(bbox)
    out.save(os.path.join(OUT, fname))

    a = np.asarray(out.getchannel('A'))
    tot = np.sum(a > 0)
    semi = np.sum((a > 0) & (a < 255))
    inv = a == 0
    lab2, n2 = ndimage.label(inv)
    border = set(lab2[0]) | set(lab2[-1]) | set(lab2[:, 0]) | set(lab2[:, -1])
    holes = sum(int(np.sum(lab2 == l)) for l in range(1, n2 + 1) if l not in border)
    print(f"{fname}: size={out.size} halo={semi}/{tot}={semi / max(tot, 1):.4f} holes={holes}")


if __name__ == '__main__':
    for f in sorted(os.listdir(SRC)):
        if f.endswith('.png') and f.startswith('idle_0') and 'small' not in f:
            process(f)
