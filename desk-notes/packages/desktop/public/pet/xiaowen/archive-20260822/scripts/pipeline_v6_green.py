# -*- coding: utf-8 -*-
"""pipeline v6：绿幕生成 + chroma key（确定性抠图，无分割模型/无 flood 猜参）
1) pro 生成 6 帧纯绿背景（3 参考图 + 绿幕首帧锁一致性）
2) chroma key：greenness 距离 → alpha，边缘 spill 去除
3) compose：眼区微小移植 + 1px 呼吸
4) deploy + sheet"""
import os, io, json, base64, time
import urllib.request
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

KEY = 'sk-sp-H.DIREYL.B8ko.MEYCIQCTsbAPAGZ61CnvwOdKJnjB6X0c3ruwZ4c1w4Gl4FvEfgIhAOut3WziQ734sRNLRDiAwlzm6GKvLkoW-foW4SvQj_d0'
BASE = 'https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
DIR = r'D:\h\3.25\tmp_pet_test'
GREEN = os.path.join(DIR, 'green')
OUT = os.path.join(DIR, 'idle_v6_final')
os.makedirs(GREEN, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

IDENTITY = ('角色与所有参考图完全一致（灰蓝高马尾金铃铛发饰、浅蓝白华山派道袍、'
            '深灰裙白过膝袜黑鞋、黑剑蓝穗、Q版2-2.5头身）。')
GREENBG = ('背景必须是纯绿色#00FF00单色，无渐变无光影无地面无阴影。'
           '单角色完整身体居中，脚底贴近画面下缘。禁止文字水印编号。')
ACTIONS = {
    'idle_01': '怀中抱剑于胸前，站姿端正，正视前方，平静待机。',
    'idle_02': '怀中抱剑于胸前，站姿与基准完全一致，正在眨眼瞬间——双眼完全闭合仅留细线。',
    'idle_03': '怀中抱剑于胸前，站姿与基准完全一致，单眼wink，另一眼睁着，嘴小笑。',
    'idle_04': '怀中抱剑于胸前，站姿与基准完全一致，双眼睁开，嘴巴张开微笑。',
    'idle_05': '怀中抱剑于胸前，站姿与基准完全一致，仅身体极轻微下沉配合呼气。',
    'idle_06': '怀中抱剑于胸前，站姿与基准完全一致，恢复正视平静，与首帧几乎相同。',
}
NAMES = list(ACTIONS)
TARGET_H, FOOT_Y = 204, 244


def b64ref(p, side=1024):
    im = Image.open(p).convert('RGB')
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def gen(name, refs):
    body = {'model': 'wan2.7-image-pro', 'input': {'messages': [{'role': 'user', 'content': refs + [
        {'text': IDENTITY + '动作：' + ACTIONS[name] + GREENBG}]}]},
        'parameters': {'size': '1024*1024', 'n': 1,
                       'negative_prompt': '渐变背景,白底,黑底,灰底,阴影,地面,文字,水印,换装,变形'}}
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
    j = json.loads(urllib.request.urlopen(req, timeout=240).read())
    url = [c['image'] for c in j['output']['choices'][0]['message']['content'] if c.get('type') == 'image'][0]
    urllib.request.urlretrieve(url, os.path.join(GREEN, name + '.png'))


def chroma_key(rgb):
    """greenness → alpha；确定性，无猜参"""
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    greenness = g - (r + b) / 2          # 纯绿≈216，角色各处≈0-40
    alpha = 1 - np.clip((greenness - 60) / 80, 0, 1)   # 60以下实，140以上透
    # spill 去除：半透明边像素压低绿色通道
    a3 = alpha[:, :, None]
    spill = np.clip(greenness, 0, 200)[:, :, None] / 200
    rgb_clean = rgb.astype(np.float32) * (1 - spill * (1 - a3) * 0.8)
    out = np.dstack([np.clip(rgb_clean, 0, 255).astype(np.uint8),
                     (alpha * 255).astype(np.uint8)])
    return out


def normalize(frames):
    heights = []
    for a in frames:
        bb = np.argwhere(a[:, :, 3] > 128)
        heights.append(bb[:, 0].max() - bb[:, 0].min() + 1)
    scale = TARGET_H / max(heights)
    out = []
    for a in frames:
        bb = np.argwhere(a[:, :, 3] > 128)
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
    ys = np.argwhere(base[:, :, 3] > 128)[:, 0]
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
    import shutil
    # 01 已有 greentest.png
    if not os.path.exists(os.path.join(GREEN, 'idle_01.png')):
        shutil.copy2(os.path.join(DIR, 'greentest.png'), os.path.join(GREEN, 'idle_01.png'))
    refs = [ {'image': b64ref(os.path.join(DIR, 'refs_new', 'ref%d.png' % i))} for i in (1, 2, 3) ]
    refs.append({'image': b64ref(os.path.join(GREEN, 'idle_01.png'), 512)})
    for n in NAMES[1:]:
        t0 = time.time()
        gen(n, refs)
        print(f'gen {n} {time.time() - t0:.0f}s')

    keyed = [chroma_key(np.asarray(Image.open(os.path.join(GREEN, n + '.png')).convert('RGB')))
             for n in NAMES]
    for n, k in zip(NAMES, keyed):
        a = k[:, :, 3]
        tot, semi = int((a > 0).sum()), int(((a > 8) & (a < 247)).sum())
        print(f'{n}: halo_ratio={semi / max(tot, 1):.3f}')

    frames = normalize(keyed)
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
    for t in [r'D:\h\3.25\思忆集\desk-notes\packages\desktop\public\pet\xiaowen\frames\idle',
              r'D:\h\3.25\思忆集\思忆集app\web_dist\pet\xiaowen\frames\idle']:
        for n in NAMES:
            shutil.copy2(os.path.join(OUT, n + '.png'), os.path.join(t, n + '.png'))
    print('deployed')
    sheet = Image.new('RGBA', (6 * 268 + 12, 280), (38, 38, 46, 255))
    for i, f in enumerate(final):
        sheet.paste(Image.fromarray(f), (12 + i * 268, 12), Image.fromarray(f))
    sheet.convert('RGB').save(os.path.join(DIR, 'idle_v6_final', 'contact_v7.jpg'), quality=92)
    print('sheet saved')


if __name__ == '__main__':
    main()