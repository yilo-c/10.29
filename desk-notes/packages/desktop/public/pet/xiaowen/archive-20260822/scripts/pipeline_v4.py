# -*- coding: utf-8 -*-
"""pipeline v4：matting(trimap+封闭背景色彩仲裁) + compose(眼区移植) + deploy
根因修复：两腿间/颈肩处封闭背景被 u2net 误判为人 → 用颜色+平滑度仲裁剔除"""
import os
from collections import deque
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

SRC = r'D:\h\3.25\tmp_pet_test\idle_v2'
OUT = r'D:\h\3.25\tmp_pet_test\idle_v4\final'
os.makedirs(OUT, exist_ok=True)
NAMES = ['idle_01', 'idle_02', 'idle_03', 'idle_04', 'idle_05', 'idle_06']
TARGET_H, FOOT_Y = 204, 244

_u2 = None


def u2_soft(im):
    global _u2
    if _u2 is None:
        from rembg import new_session
        _u2 = new_session('u2net')
    from rembg import remove
    return np.asarray(remove(Image.fromarray(im), session=_u2, only_mask=True)).astype(np.float32) / 255.0


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


def classify_enclosed(fb, rgb):
    """封闭小区域色彩仲裁：颜色近邻背景且平滑=背景剔除；否则保留"""
    comp = ~fb
    lab, n = ndimage.label(comp)
    if n == 0:
        return fb, []
    sizes = ndimage.sum(comp, lab, range(1, n + 1))
    confirmed = fb.copy()
    removed = []
    for l in range(1, n + 1):
        sz = int(sizes[l - 1])
        if sz > 3000 or sz < 4:      # 主体不算；噪点不管
            continue
        R = lab == l
        ring = ndimage.binary_dilation(R, iterations=4) & fb
        if ring.sum() < 8:
            continue
        cR = rgb[R].mean(0)
        cRing = rgb[ring].mean(0)
        varR = float(rgb[R].var(0).mean())
        d = float(np.sqrt(((cR - cRing) ** 2).sum()))
        if d < 45 and varR < 800:    # 近背景色 + 平滑无纹理 = 封闭背景
            confirmed |= R
            removed.append((sz, round(d), round(varR)))
    return confirmed, removed


def matte(im):
    rgb = im.astype(np.float32)
    u2 = u2_soft(im)
    fb = flood_bg(im)
    confirmed, removed = classify_enclosed(fb, rgb)

    def_bg = confirmed
    # 描边保护：暗色像素（黑描边/黑剑）= 确定前景，防被 u2 中间值半透明化
    # （02/04 黑角背景已被 flood 覆盖进 confirmed，不会误判）
    bright = rgb.mean(2)
    outline = (bright < 95) & ~confirmed
    def_fg = ((u2 > 0.7) & ~confirmed) | outline
    unknown = ~(def_bg | def_fg)

    alpha = np.zeros(rgb.shape[:2], np.float32)
    alpha[def_fg] = 1.0
    alpha[unknown] = np.clip((u2[unknown] - 0.3) / 0.4, 0, 1)

    fg_hard = alpha > 0.15
    lab, n = ndimage.label(fg_hard)
    if n > 1:
        sizes = ndimage.sum(fg_hard, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes > 300
        alpha[~keep[lab]] = 0

    bgc = rgb[def_bg].mean(0) if def_bg.any() else np.array([223, 223, 221])
    a3 = alpha[:, :, None]
    safe = np.clip(a3, 0.12, 1)
    rgb_clean = np.clip((rgb - bgc * (1 - a3)) / safe, 0, 255)
    rgb_out = np.where(a3 > 0, rgb_clean, rgb)
    rgba = np.dstack([rgb_out.astype(np.uint8), (alpha * 255).astype(np.uint8)])
    return rgba, removed


def denoise(a):
    rgb = ndimage.median_filter(a[:, :, :3], size=(3, 3, 1))
    alpha = a[:, :, 3]
    lab, n = ndimage.label(alpha > 0)
    if n > 1:
        sizes = ndimage.sum(alpha > 0, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes > 60
        alpha = np.where(keep[lab], alpha, 0).astype(np.uint8)
    return np.dstack([rgb.astype(np.uint8), alpha])


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
    m = diff & face
    lab, n = ndimage.label(m)
    keep = np.zeros_like(m)
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
        im0.thumbnail((512, 512), Image.LANCZOS)
        rgba, removed = matte(np.asarray(im0))
        print(f'{n}: enclosed-bg removed {len(removed)} regions {removed[:6]}')
        matted.append(rgba)

    frames = normalize([denoise(m) for m in matted])
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

    # deploy
    import shutil
    for t in [r'D:\h\3.25\思忆集\desk-notes\packages\desktop\public\pet\xiaowen\frames\idle',
              r'D:\h\3.25\思忆集\思忆集app\web_dist\pet\xiaowen\frames\idle']:
        for n in NAMES:
            shutil.copy2(os.path.join(OUT, n + '.png'), os.path.join(t, n + '.png'))
    print('deployed')

    # contact sheet
    sheet = Image.new('RGBA', (6 * 268 + 12, 280), (38, 38, 46, 255))
    for i, f in enumerate(final):
        sheet.paste(Image.fromarray(f), (12 + i * 268, 12), Image.fromarray(f))
    sheet.convert('RGB').save(r'D:\h\3.25\tmp_pet_test\idle_v4\contact_v5.jpg', quality=92)
    print('sheet saved')


if __name__ == '__main__':
    main()