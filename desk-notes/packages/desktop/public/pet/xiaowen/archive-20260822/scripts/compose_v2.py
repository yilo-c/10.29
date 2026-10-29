# -*- coding: utf-8 -*-
"""漂移修复：静态基帧 + 面部区域移植
- 统一 scale（全帧最大高度→204px）+ 脚底锚点 → 消除平移/缩放漂移
- base=帧1；帧2/3/4 仅移植面部差异区（羽化），静态部分像素级不变
- 帧5=base 下沉1px（呼吸），帧6=base（循环回中点）"""
import os
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

RAW = r'D:\h\3.25\tmp_pet_test\idle_v3\raw'
OUT = r'D:\h\3.25\tmp_pet_test\idle_v3\final'
os.makedirs(OUT, exist_ok=True)
NAMES = ['idle_01', 'idle_02', 'idle_03', 'idle_04', 'idle_05', 'idle_06']
TARGET_H = 204   # 角色视觉高度（与旧版一致）
FOOT_Y = 244     # 脚底基线


def load_all():
    ims = []
    for n in NAMES:
        im = Image.open(os.path.join(RAW, n + '.png')).convert('RGBA')
        ims.append(np.asarray(im))
    return ims


def normalize_all(ims):
    """统一 scale + 脚底锚点，输出 256 canvas 数组列表"""
    heights = []
    for a in ims:
        bbox = np.argwhere(a[:, :, 3] > 0)
        heights.append(bbox[:, 0].max() - bbox[:, 0].min() + 1)
    scale = TARGET_H / max(heights)
    out = []
    for a in ims:
        bbox = np.argwhere(a[:, :, 3] > 0)
        y0, x0 = bbox[:, 0].min(), bbox[:, 1].min()
        y1, x1 = bbox[:, 0].max(), bbox[:, 1].max()
        crop = Image.fromarray(a[y0:y1 + 1, x0:x1 + 1])
        nw, nh = max(1, int(crop.width * scale)), max(1, int(crop.height * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)
        canvas = np.zeros((256, 256, 4), np.uint8)
        x = (256 - nw) // 2
        y = FOOT_Y - nh + 1
        canvas[y:y + nh, x:x + nw] = np.asarray(crop)
        out.append(canvas)
    return out


def face_diff_mask(base, other):
    """面部差异区：rgb/alpha 差异 → 限上半 → 膨胀+羽化"""
    d = np.abs(base[:, :, :3].astype(int) - other[:, :, :3].astype(int)).sum(2)
    da = np.abs(base[:, :, 3].astype(int) - other[:, :, 3].astype(int))
    diff = (d > 60) | (da > 80)
    # 限角色上半（脸/刘海区）
    ys = np.argwhere(base[:, :, 3] > 0)[:, 0]
    top, bot = ys.min(), ys.max()
    face = np.zeros_like(diff)
    face[top:top + int((bot - top) * 0.52), :] = True
    m = diff & face
    m = ndimage.binary_dilation(m, iterations=3)
    return m


def feather_mask(m, px=2):
    a = (m * 255).astype(np.uint8)
    a = Image.fromarray(a).filter(ImageFilter.GaussianBlur(px))
    return np.asarray(a).astype(np.float32) / 255.0


def composite(base, other, w):
    """alpha-aware 移植：out = other*w + base*(1-w)"""
    bf = base.astype(np.float32) / 255.0
    of = other.astype(np.float32) / 255.0
    w3 = w[:, :, None]
    # premultiplied blend
    ba, oa = bf[:, :, 3:4], of[:, :, 3:4]
    out_a = oa * w3 + ba * (1 - w3)
    safe = np.clip(out_a, 1e-5, 1)
    out_rgb = (of[:, :, :3] * oa * w3 + bf[:, :, :3] * ba * (1 - w3)) / safe
    out = np.dstack([out_rgb, out_a]) * 255
    return np.clip(out, 0, 255).astype(np.uint8)


def main():
    ims = load_all()
    frames = normalize_all(ims)
    base = frames[0]
    final = [base]
    for i in (1, 2, 3):
        m = face_diff_mask(base, frames[i])
        w = feather_mask(m)
        final.append(composite(base, frames[i], w))
        print(f'{NAMES[i]}: face mask px={int(m.sum())}')
    # 帧5：base 下沉 1px 呼吸
    sunk = np.zeros_like(base)
    sunk[1:, :, :] = base[:-1, :, :]
    final.append(sunk)
    # 帧6：base
    final.append(base)

    for n, f in zip(NAMES, final):
        Image.fromarray(f).save(os.path.join(OUT, n + '.png'))

    # IoU 验证（静态一致性）
    ba = base[:, :, 3] > 0
    print('\nvs base IoU:')
    for n, f in zip(NAMES, final):
        fa = f[:, :, 3] > 0
        iou = np.logical_and(fa, ba).sum() / max(np.logical_or(fa, ba).sum(), 1)
        print(f'  {n}: {iou:.4f}')


if __name__ == '__main__':
    main()