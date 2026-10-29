# -*- coding: utf-8 -*-
"""小问 idle 帧后处理：rembg 抠图 → 脚底锚点对齐 → 256x256 输出"""
import os, json
from PIL import Image, ImageFilter
import numpy as np

SRC_DIR = r'D:\h\3.25\tmp_pet_test\idle_v2'
RAW_DIR = os.path.join(SRC_DIR, 'raw_crops')
OUT_DIR = r'D:\h\3.25\tmp_pet_test\idle_final'
os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

_session = None


def extract_alpha(im: Image.Image) -> Image.Image:
    """rembg U2Net 人物分割抠图"""
    global _session
    if _session is None:
        from rembg import new_session
        _session = new_session('u2net')
    from rembg import remove
    return remove(im, session=_session, post_process_mask=True)


def process(fname: str):
    src = os.path.join(SRC_DIR, fname)
    im = Image.open(src).convert('RGBA')
    im = extract_alpha(im)
    bbox = im.getchannel('A').getbbox()
    if bbox:
        im = im.crop(bbox)
        # 边缘收缩 2px 清白边
        a = im.getchannel('A').filter(ImageFilter.MinFilter(3))
        im.putalpha(a)
    im.save(os.path.join(RAW_DIR, fname))
    return im


def normalize(im: Image.Image, out_size=256):
    """缩放 + 脚底锚点对齐：内容底边固定 y=out_size-12（12px 安全边距），水平居中"""
    w, h = im.size
    scale = out_size / max(w, h) * 0.80
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGBA', (out_size, out_size), (0, 0, 0, 0))
    bbox = im.getchannel('A').getbbox()
    if not bbox:
        return canvas
    fw, fh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (out_size - fw) // 2
    y = out_size - 12 - fh
    canvas.paste(im, (x, y), im)
    return canvas


def main():
    names = sorted(f for f in os.listdir(SRC_DIR)
                   if f.endswith('.png') and not f.startswith('_') and 'small' not in f)
    stats = []
    for fn in names:
        im = process(fn)
        final = normalize(im)
        final.save(os.path.join(OUT_DIR, fn))
        bbox = final.getchannel('A').getbbox()
        stats.append({'frame': fn, 'bbox': list(bbox) if bbox else None})
        print(f'{fn}: crop={im.size}  final bbox={bbox}')
    json.dump(stats, open(os.path.join(OUT_DIR, '_anchor_stats.json'), 'w'), indent=1)
    # 锚点漂移汇总
    if stats:
        print('\nanchor spread (px):')
        xs, xw = min(s['bbox'][0] for s in stats), max(s['bbox'][2] for s in stats)
        ys, yh = min(s['bbox'][1] for s in stats), max(s['bbox'][3] for s in stats)
        print(f"  x: {xs}..{xw} (spread {xw-xs})  y: {ys}..{yh} (spread {yh-ys})")


if __name__ == '__main__':
    main()