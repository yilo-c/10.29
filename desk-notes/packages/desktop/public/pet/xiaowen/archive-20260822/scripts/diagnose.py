# -*- coding: utf-8 -*-
"""量化诊断：① 抠图边缘残留 ② 帧间部位漂移"""
import os, glob, json
import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.signal import fftconvolve

FINAL = r'D:\h\3.25\tmp_pet_test\idle_final'


def load(f):
    return np.asarray(Image.open(f).convert('RGBA'))


def edge_stats(rgba):
    """抠图质量：半透明 halo 宽度 + 白边（亮色残留）比例"""
    a = rgba[:, :, 3]
    rgb = rgba[:, :, :3].astype(np.int16)
    mask = a > 0
    # 半透明像素（非 0 非 255）= halo
    semi = np.sum((a > 0) & (a < 255))
    # 外环 3px：mask 膨胀3 - mask，检查其中 a>0 的亮色残留（白边）
    dil = ndimage.binary_dilation(mask, iterations=3) & ~mask
    fringe_px = np.sum(dil & (a > 0))
    # 内环 2px 里接近白/浅灰的像素（白袍误判不管，只看"边缘亮残留"）：
    erode = ndimage.binary_erosion(mask, iterations=2)
    ring = mask & ~erode
    bright = np.sum(ring & (rgb.mean(axis=2) > 210))
    total = np.sum(mask)
    return {
        'semi_halo_px': int(semi),
        'outer_fringe_px': int(fringe_px),
        'inner_bright_edge_px': int(bright),
        'solid_px': int(total),
        'halo_ratio': round(semi / max(total, 1), 4),
    }


def drift_stats(frames):
    """漂移：以帧1为基准，FFT 相位相关求平移 → 对齐后残差比例 + 分区残差"""
    base = frames[0]
    ba = (base[:, :, 3] > 0).astype(np.float32)
    out = []
    for i, f in enumerate(frames):
        fa = (f[:, :, 3] > 0).astype(np.float32)
        # 相位相关求平移
        corr = fftconvolve(fa, ba[::-1, ::-1], mode='full')
        dy, dx = np.unravel_index(np.argmax(corr), corr.shape)
        dy -= base.shape[0] - 1
        dx -= base.shape[1] - 1
        shifted = np.roll(fa, (int(dy), int(dx)), axis=(0, 1))
        # 残差 = 对称差 / 并集
        inter = np.logical_and(shifted > 0, ba > 0).sum()
        union = np.logical_or(shifted > 0, ba > 0).sum()
        iou = inter / max(union, 1)
        # 分区：上半（头/马尾）下半（身/腿）
        h = base.shape[0] // 2
        top_iou = np.logical_and(shifted[:h] > 0, ba[:h] > 0).sum() / max(np.logical_or(shifted[:h] > 0, ba[:h] > 0).sum(), 1)
        bot_iou = np.logical_and(shifted[h:] > 0, ba[h:] > 0).sum() / max(np.logical_or(shifted[h:] > 0, ba[h:] > 0).sum(), 1)
        out.append({'frame': i + 1, 'shift': [int(dx), int(dy)], 'iou': round(float(iou), 4),
                    'top_iou': round(float(top_iou), 4), 'bot_iou': round(float(bot_iou), 4)})
    return out


def main():
    files = sorted(glob.glob(os.path.join(FINAL, 'idle_0?.png')))
    frames = [load(f) for f in files]
    report = {'edges': [], 'drift': drift_stats(frames)}
    for f, fr in zip(files, frames):
        e = edge_stats(fr)
        e['frame'] = os.path.basename(f)
        report['edges'].append(e)
        print(e['frame'], 'halo:', e['semi_halo_px'], 'fringe:', e['outer_fringe_px'],
              'bright_edge:', e['inner_bright_edge_px'], 'halo_ratio:', e['halo_ratio'])
    print()
    for d in report['drift']:
        print(f"frame{d['frame']} shift={d['shift']} iou={d['iou']} top={d['top_iou']} bot={d['bot_iou']}")
    json.dump(report, open(os.path.join(FINAL, '_diagnose.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()