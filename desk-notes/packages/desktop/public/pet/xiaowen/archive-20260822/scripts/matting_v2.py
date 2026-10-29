# -*- coding: utf-8 -*-
"""matting v2：几何 flood-fill 抠图（黑描边屏障）+ 1px AA + 边缘颜色去污
替代 u2net（halo 0.98 不可用）。输出到 idle_v3/raw/"""
import os
from collections import deque
import numpy as np
from PIL import Image
from scipy import ndimage

SRC = r'D:\h\3.25\tmp_pet_test\idle_v2'
OUT = r'D:\h\3.25\tmp_pet_test\idle_v3\raw'
os.makedirs(OUT, exist_ok=True)

TOL = 60          # 背景色容差
FEATHER_ALPHA = 170  # 边界 1px AA 的 alpha


def flood_matte(rgb: np.ndarray, local_tol: int = 40):
    """返回背景 mask。局部生长 flood-fill：
    新像素与当前已访问像素颜色差 < local_tol 才算背景。
    平滑渐变背景可穿越；黑描边色差 ~200 形成屏障。
    先 median 滤波去 dither 噪声（否则噪声差 > tol 卡死生长）。"""
    rgb = ndimage.median_filter(rgb, size=(3, 3, 1))
    h, w, _ = rgb.shape
    arr = rgb.astype(np.float32)  # int16 会平方溢出致 sqrt(NAN)
    visited = np.zeros((h, w), bool)
    q = deque()
    # 种子：最外圈 1px（必为背景）
    for j in range(w):
        for i in (0, h - 1):
            if not visited[i, j]:
                visited[i, j] = True; q.append((i, j))
    for i in range(h):
        for j in (0, w - 1):
            if not visited[i, j]:
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


def decontaminate(rgb: np.ndarray, fg: np.ndarray, ring: np.ndarray):
    """边界 1px 颜色去污：用向内侵蚀色替换（去除浅灰混色）"""
    inner = fg & ~ring
    # 对 ring 像素取最近 inner 像素颜色（distance transform indices）
    _, (iy, ix) = ndimage.distance_transform_edt(~inner, return_indices=True)
    out = rgb.copy()
    out[ring] = rgb[iy[ring], ix[ring]]
    return out


_u2_session = None


def u2_mask(rgb: np.ndarray) -> np.ndarray:
    """u2net 概率掩码（0..1），仅作漏填检测器"""
    global _u2_session
    if _u2_session is None:
        from rembg import new_session
        _u2_session = new_session('u2net')
    from rembg import remove
    out = remove(Image.fromarray(rgb.astype(np.uint8)), session=_u2_session, only_mask=True)
    return np.asarray(out).astype(np.float32) / 255.0


def process(fname: str):
    im = Image.open(os.path.join(SRC, fname)).convert('RGB')
    rgb = np.asarray(im)
    bgmask = flood_matte(rgb)
    u2 = u2_mask(rgb)
    # 漏填修补：flood 说是背景、但 u2net 说是角色(>0.5)的区域 = 描边断口漏入，填回
    leak = bgmask & (u2 > 0.5)
    fg = (~bgmask) | leak
    # 去散点噪声：只保留 >500px 的连通域（主体+剑等大件）
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes > 500
        fg = keep[lab]
    # 1px 边界环
    eroded = ndimage.binary_erosion(fg, iterations=1)
    ring = fg & ~eroded
    # alpha：实心 255，环 AA
    alpha = np.where(fg, 255, 0).astype(np.uint8)
    alpha[ring] = FEATHER_ALPHA
    # 去污
    rgb_clean = decontaminate(rgb, fg, ring)
    rgba = np.dstack([rgb_clean, alpha])
    out = Image.fromarray(rgba, 'RGBA')
    bbox = out.getchannel('A').getbbox()
    if bbox:
        out = out.crop(bbox)
    out.save(os.path.join(OUT, fname))
    # 质检
    a = np.asarray(out.getchannel('A'))
    tot = np.sum(a > 0)
    semi = np.sum((a > 0) & (a < 255))
    # 漏填孔洞
    inv = a == 0
    lab, n = ndimage.label(inv)
    border = set(lab[0]) | set(lab[-1]) | set(lab[:, 0]) | set(lab[:, -1])
    holes = sum(int(np.sum(lab == l)) for l in range(1, n + 1) if l not in border)
    print(f"{fname}: size={out.size} halo={semi}/{tot}={semi / max(tot, 1):.4f} holes={holes}")
    return out


if __name__ == '__main__':
    for f in sorted(os.listdir(SRC)):
        if f.endswith('.png') and f.startswith('idle_0') and 'small' not in f:
            process(f)