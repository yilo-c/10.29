# -*- coding: utf-8 -*-
"""compose v3：只移植眼/嘴微小区域，全身静态像素=base（上下身协调）
+ median 斑点去杂质"""
import os
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

RAW = r'D:\h\3.25\tmp_pet_test\idle_v3\raw'
OUT = r'D:\h\3.25\tmp_pet_test\idle_v3\final'
os.makedirs(OUT, exist_ok=True)
NAMES = ['idle_01', 'idle_02', 'idle_03', 'idle_04', 'idle_05', 'idle_06']
TARGET_H = 204
FOOT_Y = 244


def load_all():
    return [np.asarray(Image.open(os.path.join(RAW, n + '.png')).convert('RGBA')) for n in NAMES]


def normalize_all(ims):
    heights = []
    for a in ims:
        bb = np.argwhere(a[:, :, 3] > 0)
        heights.append(bb[:, 0].max() - bb[:, 0].min() + 1)
    scale = TARGET_H / max(heights)
    out = []
    for a in ims:
        bb = np.argwhere(a[:, :, 3] > 0)
        crop = Image.fromarray(a[bb[:, 0].min():bb[:, 0].max() + 1, bb[:, 1].min():bb[:, 1].max() + 1])
        nw, nh = max(1, int(crop.width * scale)), max(1, int(crop.height * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)
        canvas = np.zeros((256, 256, 4), np.uint8)
        canvas[(FOOT_Y - nh + 1):(FOOT_Y + 1), (256 - nw) // 2:(256 - nw) // 2 + nw] = np.asarray(crop)
        out.append(canvas)
    return out


def denoise(a):
    """median 去斑点杂质（保边）+ 去小连通域"""
    rgb = ndimage.median_filter(a[:, :, :3], size=(3, 3, 1))
    alpha = a[:, :, 3]
    lab, n = ndimage.label(alpha > 0)
    if n > 1:
        sizes = ndimage.sum(alpha > 0, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes > 60
        alpha = np.where(keep[lab], alpha, 0).astype(np.uint8)
    return np.dstack([rgb.astype(np.uint8), alpha])


def tiny_face_mask(base, other):
    """只保留眼/嘴级别的小连通差异块，丢弃上半身大漂移块"""
    d = np.abs(base[:, :, :3].astype(int) - other[:, :, :3].astype(int)).sum(2)
    da = np.abs(base[:, :, 3].astype(int) - other[:, :, 3].astype(int))
    diff = (d > 60) | (da > 80)
    ys = np.argwhere(base[:, :, 3] > 0)[:, 0]
    top, bot = ys.min(), ys.max()
    face = np.zeros_like(diff)
    face[top:top + int((bot - top) * 0.55), :] = True
    m = diff & face
    lab, n = ndimage.label(m)
    keep = np.zeros_like(m)
    kept_px = 0
    for l in range(1, n + 1):
        sz = int((lab == l).sum())
        if 15 < sz < 700:          # 眼/嘴小块；大漂移块丢弃
            keep |= (lab == l)
            kept_px += sz
    keep = ndimage.binary_dilation(keep, iterations=2)
    return keep, kept_px


def feather(m, px=1):
    a = (m * 255).astype(np.uint8)
    return np.asarray(Image.fromarray(a).filter(ImageFilter.GaussianBlur(px))).astype(np.float32) / 255.0


def composite(base, other, w):
    bf = base.astype(np.float32) / 255.0
    of = other.astype(np.float32) / 255.0
    w3 = w[:, :, None]
    ba, oa = bf[:, :, 3:4], of[:, :, 3:4]
    out_a = oa * w3 + ba * (1 - w3)
    safe = np.clip(out_a, 1e-5, 1)
    out_rgb = (of[:, :, :3] * oa * w3 + bf[:, :, :3] * ba * (1 - w3)) / safe
    return np.clip(np.dstack([out_rgb, out_a]) * 255, 0, 255).astype(np.uint8)


def main():
    frames = normalize_all(load_all())
    frames = [denoise(f) for f in frames]
    base = frames[0]
    final = [base]
    for i in (1, 2, 3):
        m, px = tiny_face_mask(base, frames[i])
        final.append(composite(base, frames[i], feather(m)))
        print(f'{NAMES[i]}: tiny mask px={px} (dilated={int(m.sum())})')
    sunk = np.zeros_like(base)
    sunk[1:] = base[:-1]
    final.append(sunk)
    final.append(base)
    for n, f in zip(NAMES, final):
        Image.fromarray(f).save(os.path.join(OUT, n + '.png'))
    ba = base[:, :, 3] > 0
    print('vs base IoU:')
    for n, f in zip(NAMES, final):
        fa = f[:, :, 3] > 0
        print(f'  {n}: {np.logical_and(fa, ba).sum() / max(np.logical_or(fa, ba).sum(), 1):.4f}')


if __name__ == '__main__':
    main()