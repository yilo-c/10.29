# -*- coding: utf-8 -*-
"""pipeline v5：纯几何 matting（无 u2net）
alpha = ~flood_bg；封闭背景色彩仲裁；描边天然保护；1px AA
compose：眼区微小移植 + 1px 呼吸"""
import os
from collections import deque
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

SRC = r'D:\h\3.25\tmp_pet_test\idle_v2'
OUT = r'D:\h\3.25\tmp_pet_test\idle_v5\final'
os.makedirs(OUT, exist_ok=True)
NAMES = ['idle_01', 'idle_02', 'idle_03', 'idle_04', 'idle_05', 'idle_06']
TARGET_H, FOOT_Y = 204, 244


def flood_bg(rgb, local_tol=45, var_gate=2000):
    """局部生长 + 平滑度门控（无需 median，1024 保 2px 描边）
    - 色差 < tol：背景渐变可穿越
    - 局部方差 < gate：噪声(±20, var~500)放行；描边(±200, var~10000)屏障"""
    h, w, _ = rgb.shape
    arr = rgb.astype(np.float32)
    gray = arr.mean(2)
    varmap = ndimage.uniform_filter(gray ** 2, 3) - ndimage.uniform_filter(gray, 3) ** 2
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
                    if varmap[ni, nj] < var_gate and \
                            np.sqrt(((arr[ni, nj] - c) ** 2).sum()) < local_tol:
                        visited[ni, nj] = True; q.append((ni, nj))
    return visited


def classify_enclosed(fb, rgb):
    """封闭小区域：颜色近邻背景且平滑 → 背景；否则保留（角色部件）"""
    comp = ~fb
    lab, n = ndimage.label(comp)
    if n == 0:
        return fb, []
    sizes = ndimage.sum(comp, lab, range(1, n + 1))
    confirmed = fb.copy()
    removed = []
    for l in range(1, n + 1):
        sz = int(sizes[l - 1])
        if sz > 3000 or sz < 4:
            continue
        R = lab == l
        ring = ndimage.binary_dilation(R, iterations=4) & fb
        if ring.sum() < 8:
            continue
        d = float(np.sqrt(((rgb[R].mean(0) - rgb[ring].mean(0)) ** 2).sum()))
        varR = float(rgb[R].var(0).mean())
        if d < 45 and varR < 800:
            confirmed |= R
            removed.append((sz, round(d), round(varR)))
    return confirmed, removed


def matte(im):
    rgb = im.astype(np.float32)
    fb = flood_bg(rgb, 45)
    if fb.mean() < 0.2:          # 陡渐变/噪声背景扩展不足 → 放宽重试
        fb = flood_bg(rgb, 60, var_gate=4000)
    confirmed, removed = classify_enclosed(fb, rgb)

    alpha = np.where(confirmed, 0, 255).astype(np.uint8)
    # 1px AA：高斯 0.8
    alpha = np.array(Image.fromarray(alpha).filter(ImageFilter.GaussianBlur(0.8)), copy=True)

    # 连通域去散点
    hard = alpha > 64
    lab, n = ndimage.label(hard)
    if n > 1:
        sizes = ndimage.sum(hard, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes > 300
        alpha[~keep[lab]] = 0

    # 边缘颜色 unmix 去污
    a3 = (alpha.astype(np.float32) / 255)[:, :, None]
    bgc = rgb[confirmed].mean(0) if confirmed.any() else np.array([223, 223, 221])
    safe = np.clip(a3, 0.12, 1)
    rgb_clean = np.clip((rgb - bgc * (1 - a3)) / safe, 0, 255)
    rgb_out = np.where(a3 > 0, rgb_clean, rgb)
    return np.dstack([rgb_out.astype(np.uint8), alpha]), removed


# ── compose 部分（同 v4）─────────────────────────────────────────────
def normalize(frames):
    heights = []
    for a in frames:
        bb = np.argwhere(a[:, :, 3] > 0)
        heights.append(bb[:, 0].max() - bb[:, 0].min() + 1)
    scale = TARGET_H / max(heights)
    out = []
    for a in frames:
        bb = np.argwhere(a[:, :, 3] > 0)
        crop = Image.fromarray(a[bb[:, 0].min():bb[:, 0].max() + 1, bb[:, 1].min():bb[:, 1].max() + 1])
        nw, nh = max(1, int(crop.width * scale)), max(1, int(crop.height * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)
        c = np.zeros((256, 256, 4), np.uint8)
        c[(FOOT_Y - nh + 1):(FOOT_Y + 1), (256 - nw) // 2:(256 - nw) // 2 + nw] = np.asarray(crop)
        out.append(c)
    return out


def tiny_face_mask(base, other):
    d = np.abs(base[:, :, :3].astype(int) - other[:, :, :3].astype(int)).sum(2)
    da = np.abs(base[:, :, 3].astype(int) - other[:, :, 3].astype(int))
    diff = (d > 60) | (da > 80)
    ys = np.argwhere(base[:, :, 3] > 0)[:, 0]
    face = np.zeros_like(diff)
    face[ys.min():ys.min() + int((ys.max() - ys.min()) * 0.55), :] = True
    lab, n = ndimage.label(diff & face)
    keep = np.zeros_like(diff)
    for l in range(1, n + 1):
        sz = int((lab == l).sum())
        if 15 < sz < 700:
            keep |= (lab == l)
    return ndimage.binary_dilation(keep, iterations=2)


def composite(base, other, w):
    bf, of = base.astype(np.float32) / 255, other.astype(np.float32) / 255
    w3 = w[:, :, None]
    ba, oa = bf[:, :, 3:4], of[:, :, 3:4]
    out_a = oa * w3 + ba * (1 - w3)
    safe = np.clip(out_a, 1e-5, 1)
    rgb = (of[:, :, :3] * oa * w3 + bf[:, :, :3] * ba * (1 - w3)) / safe
    return np.clip(np.dstack([rgb, out_a]) * 255, 0, 255).astype(np.uint8)


def main():
    matted = []
    for n in NAMES:
        im0 = Image.open(os.path.join(SRC, n + '.png')).convert('RGB')
        rgba, removed = matte(np.asarray(im0))   # 1024 全尺寸，保 2px 描边
        a = rgba[:, :, 3]
        tot, semi = int((a > 0).sum()), int(((a > 0) & (a < 255)).sum())
        print(f'{n}: halo={semi}/{tot}={semi / max(tot, 1):.3f} enclosed_removed={len(removed)}')
        matted.append(rgba)

    frames = normalize(matted)
    base = frames[0]
    final = [base]
    for i in (1, 2, 3):
        m = tiny_face_mask(base, frames[i])
        w = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(1))).astype(np.float32) / 255
        final.append(composite(base, frames[i], w))
    sunk = np.zeros_like(base)
    sunk[1:] = base[:-1]
    final += [sunk, base]

    for n, f in zip(NAMES, final):
        Image.fromarray(f).save(os.path.join(OUT, n + '.png'))

    import shutil
    for t in [r'D:\h\3.25\思忆集\desk-notes\packages\desktop\public\pet\xiaowen\frames\idle',
              r'D:\h\3.25\思忆集\思忆集app\web_dist\pet\xiaowen\frames\idle']:
        for n in NAMES:
            shutil.copy2(os.path.join(OUT, n + '.png'), os.path.join(t, n + '.png'))
    print('deployed')

    sheet = Image.new('RGBA', (6 * 268 + 12, 280), (38, 38, 46, 255))
    for i, f in enumerate(final):
        sheet.paste(Image.fromarray(f), (12 + i * 268, 12), Image.fromarray(f))
    sheet.convert('RGB').save(r'D:\h\3.25\tmp_pet_test\idle_v5\contact_v6.jpg', quality=92)
    print('sheet saved')


if __name__ == '__main__':
    main()