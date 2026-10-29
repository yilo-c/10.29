# -*- coding: utf-8 -*-
"""视频抽帧管线：下载 → imageio 抽帧 → chroma key → 锚点归一 → 循环接缝检测 → sheet
用法: extract_qc.py <video_url_or_path> <out_dir> <n_frames>"""
import os, sys
import numpy as np
import imageio.v3 as iio
from PIL import Image, ImageFilter
from scipy import ndimage

TARGET_H, FOOT_Y = 204, 244


def chroma_key(rgb):
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    greenness = g - (r + b) / 2
    alpha = 1 - np.clip((greenness - 60) / 80, 0, 1)
    a3 = alpha[:, :, None]
    spill = np.clip(greenness, 0, 200)[:, :, None] / 200
    rgb_clean = rgb.astype(np.float32) * (1 - spill * (1 - a3) * 0.8)
    return np.dstack([np.clip(rgb_clean, 0, 255).astype(np.uint8), (alpha * 255).astype(np.uint8)])


def find_loop_span(keyed_raw, min_span=24):
    """在抽 key 帧前，找首尾最相似的区间 [i,j]（j-i>=min_span）作为无缝循环段"""
    small = [np.asarray(Image.fromarray(np.asarray(f).astype(np.uint8)).resize((64, 64))).astype(np.int16)
             for f in keyed_raw]
    T = len(small)
    best = None
    for i in range(0, T - min_span):
        for j in range(i + min_span, T):
            d = np.abs(small[i] - small[j]).mean()
            if best is None or d < best[0]:
                best = (d, i, j)
    return best


def main():
    src, outdir, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    os.makedirs(outdir, exist_ok=True)
    if src.startswith('http'):
        import urllib.request
        local = os.path.join(outdir, '_video.mp4')
        urllib.request.urlretrieve(src, local)
        src = local
    import imageio
    reader = imageio.get_reader(src)
    frames = [f for f in reader]
    reader.close()
    d, li, lj = find_loop_span(frames)
    print(f'loop span: [{li},{lj}] of {len(frames)} diff={d:.1f}')
    seg = frames[li:lj + 1]
    idx = np.linspace(0, len(seg) - 1, n).round().astype(int)
    keyed = [chroma_key(np.asarray(seg[i]).astype(np.float32)) for i in idx]

    # 统一 scale 归一（全序列最大高度）
    heights = []
    for a in keyed:
        bb = np.argwhere(a[:, :, 3] > 128)
        heights.append(bb[:, 0].max() - bb[:, 0].min() + 1 if len(bb) else 1)
    scale = TARGET_H / max(heights)
    norm = []
    for a in keyed:
        bb = np.argwhere(a[:, :, 3] > 128)
        crop = Image.fromarray(a[bb[:, 0].min():bb[:, 0].max() + 1, bb[:, 1].min():bb[:, 1].max() + 1])
        nw, nh = max(1, int(crop.width * scale)), max(1, int(crop.height * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)
        c = np.zeros((256, 256, 4), np.uint8)
        c[(FOOT_Y - nh + 1):(FOOT_Y + 1), (256 - nw) // 2:(256 - nw) // 2 + nw] = np.asarray(crop)
        norm.append(c)

    for i, a in enumerate(norm):
        Image.fromarray(a).save(os.path.join(outdir, 'frame_%02d.png' % i))

    # 循环接缝：首尾 alpha+rgb 差
    d = np.abs(norm[0].astype(int) - norm[-1].astype(int))
    seam = (d[:, :, :3].sum(2) > 60).mean()
    # 帧间运动量序列（看动作是否连续）
    motion = [round(float((np.abs(norm[i].astype(int) - norm[i + 1].astype(int))[:, :, :3].sum(2) > 60).mean()), 4)
              for i in range(len(norm) - 1)]
    print('seam(first-last diff ratio): %.4f' % seam)
    print('motion per step:', motion)

    cols = min(n, 8)
    rows = (n + cols - 1) // cols
    sheet = Image.new('RGBA', (cols * 140 + 12, rows * 140 + 12), (38, 38, 46, 255))
    for i, a in enumerate(norm):
        im = Image.fromarray(a).resize((140, 140))
        sheet.paste(im, (12 + (i % cols) * 140, 12 + (i // cols) * 140), im)
    sheet.convert('RGB').save(os.path.join(outdir, 'contact.jpg'), quality=90)
    # 循环 GIF 预览（叠深色底）
    gif_frames = []
    for a in norm:
        im = Image.fromarray(a).resize((192, 192))
        bg = Image.new('RGBA', (192, 192), (26, 26, 36, 255))
        bg.alpha_composite(im)
        gif_frames.append(bg.convert('RGB').convert('P', palette=Image.ADAPTIVE))
    gif_frames[0].save(os.path.join(outdir, 'loop.gif'), save_all=True,
                       append_images=gif_frames[1:], duration=120, loop=0)
    print('saved %d frames + contact.jpg + loop.gif' % n)


if __name__ == '__main__':
    main()