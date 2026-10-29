# -*- coding: utf-8 -*-
"""批量动作管线：绿幕首帧 → happyhorse i2v → 轮询 → 抽帧 QC → 存帧
批次：idle_i1 + click_defend/shy/combo3/drag_fly/land"""
import os, io, sys, json, time, base64
import urllib.request, urllib.error
import numpy as np
import imageio
from PIL import Image
from scipy import ndimage

sys.path.insert(0, r'D:\h\3.25\tmp_pet_test')
from extract_qc import chroma_key, find_loop_span

KEY = 'sk-sp-H.DIREYL.B8ko.MEYCIQCTsbAPAGZ61CnvwOdKJnjB6X0c3ruwZ4c1w4Gl4FvEfgIhAOut3WziQ734sRNLRDiAwlzm6GKvLkoW-foW4SvQj_d0'
M = 'https://token-plan.cn-beijing.maas.aliyuncs.com'
DIR = r'D:\h\3.25\tmp_pet_test'
OUT = os.path.join(DIR, 'idle_v7')
os.makedirs(OUT, exist_ok=True)

KEEP = ('角色与首帧图完全一致（灰蓝高马尾金铃铛发饰、浅蓝白华山派道袍、深灰裙白过膝袜、'
        '黑剑蓝穗、Q版2.5头身），全程不变脸不换装。背景保持纯绿色#00FF00不变。镜头完全固定。')

ACTIONS = [
    # (name, pose, motion, n_frames, loop)
    ('idle_i1', 'idle', '动作：只眨一次眼（双眼闭合再睁开），其余身体完全静止。', 4, True),
    ('click_defend', 'idle', '动作：突然受惊轻微收肩，反手握紧剑柄警觉一瞬，看清后表情放松。', 8, False),
    ('shy', 'idle', '动作：用宽袖遮住半张脸只露一只眼，脸颊微红，黑剑仍在身侧。', 8, False),
    ('combo3', 'idle', '动作：横剑于胸前摆出剑势，冰蓝剑意沿剑身小爆发，随即收势抱剑。', 10, False),
    ('drag_fly', 'fly', '动作：踩剑向前飞行，马尾衣摆被风吹动，贴身少量蓝白拖尾，飞行姿态循环。', 8, True),
    ('land', 'fly', '动作：从剑上跳下，落地轻微踉跄一下随即站稳抱剑，表情有点迷糊。', 8, False),
]


def b64ref(p, side=768):
    im = Image.open(p).convert('RGB')
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def post(url, body, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wan_green_url(prompt):
    """wan2.7-image-pro 绿幕生图，返回 OSS URL"""
    refs = [b64ref(os.path.join(DIR, 'refs_new', 'ref%d.png' % i)) for i in (1, 2, 3)]
    body = {'model': 'wan2.7-image-pro', 'input': {'messages': [{'role': 'user', 'content':
            [{'image': r} for r in refs] + [{'text': prompt}]}]},
            'parameters': {'size': '1024*1024', 'n': 1}}
    j = post(M + '/api/v1/services/aigc/multimodal-generation/generation', body,
             {'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}, 240)
    return [c['image'] for c in j['output']['choices'][0]['message']['content']
            if c.get('type') == 'image'][0]


def submit_i2v(img_url, motion):
    body = {'model': 'happyhorse-1.1-i2v',
            'input': {'prompt': KEEP + motion + '动作舒缓克制幅度小。无文字无水印。',
                      'media': [{'type': 'first_frame', 'url': img_url}]},
            'parameters': {'resolution': '720P', 'duration': 3}}
    j = post(M + '/api/v1/services/aigc/video-generation/video-synthesis', body,
             {'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json',
              'X-DashScope-Async': 'enable'})
    return j['output']['task_id']


def poll(tid, timeout_s=560):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        req = urllib.request.Request(M + '/api/v1/tasks/' + tid,
                                     headers={'Authorization': 'Bearer ' + KEY})
        j = json.loads(urllib.request.urlopen(req, timeout=60).read())
        st = j.get('output', {}).get('task_status')
        if st == 'SUCCEEDED':
            return j['output']['video_url']
        if st in ('FAILED', 'CANCELED'):
            raise RuntimeError('%s: %s' % (st, j['output'].get('message', '')))
        time.sleep(10)
    raise RuntimeError('timeout ' + tid)


def extract(video_url, outdir, n, loop):
    os.makedirs(outdir, exist_ok=True)
    local = os.path.join(outdir, '_video.mp4')
    urllib.request.urlretrieve(video_url, local)
    reader = imageio.get_reader(local)
    frames = [f for f in reader]
    reader.close()
    if loop:
        _, li, lj = find_loop_span(frames)
        seg = frames[li:lj + 1]
    else:
        seg = frames
    idx = np.linspace(0, len(seg) - 1, n).round().astype(int)
    keyed = [chroma_key(np.asarray(seg[i]).astype(np.float32)) for i in idx]
    # 统一 scale 归一
    heights = []
    for a in keyed:
        bb = np.argwhere(a[:, :, 3] > 128)
        heights.append(bb[:, 0].max() - bb[:, 0].min() + 1 if len(bb) else 1)
    scale = 204 / max(heights)
    norm = []
    for a in keyed:
        bb = np.argwhere(a[:, :, 3] > 128)
        crop = Image.fromarray(a[bb[:, 0].min():bb[:, 0].max() + 1, bb[:, 1].min():bb[:, 1].max() + 1])
        nw, nh = max(1, int(crop.width * scale)), max(1, int(crop.height * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)
        c = np.zeros((256, 256, 4), np.uint8)
        c[(244 - nh + 1):245, (256 - nw) // 2:(256 - nw) // 2 + nw] = np.asarray(crop)
        norm.append(c)
    for i, a in enumerate(norm):
        Image.fromarray(a).save(os.path.join(outdir, '%s_%02d.png' % (os.path.basename(outdir), i + 1)))
    # contact + gif
    cols = min(n, 8)
    rows = (n + cols - 1) // cols
    sheet = Image.new('RGBA', (cols * 140 + 12, rows * 140 + 12), (38, 38, 46, 255))
    gif_frames = []
    for i, a in enumerate(norm):
        im140 = Image.fromarray(a).resize((140, 140))
        sheet.paste(im140, (12 + (i % cols) * 140, 12 + (i // cols) * 140), im140)
        im192 = Image.fromarray(a).resize((192, 192))
        bg = Image.new('RGBA', (192, 192), (26, 26, 36, 255))
        bg.alpha_composite(im192)
        gif_frames.append(bg.convert('RGB').convert('P', palette=Image.ADAPTIVE))
    sheet.convert('RGB').save(os.path.join(outdir, 'contact.jpg'), quality=90)
    gif_frames[0].save(os.path.join(outdir, 'anim.gif'), save_all=True,
                       append_images=gif_frames[1:], duration=120, loop=0)
    return len(norm)


def main():
    # pose URLs
    urls = {'idle': open(os.path.join(DIR, 'green_url.txt')).read().strip()}
    urls['fly'] = wan_green_url(
        '角色与参考图完全一致。踩在黑色长剑上御剑飞行的姿势，身体微前倾，灰蓝马尾和衣摆被风吹起，'
        '少量蓝白剑气拖尾。背景纯绿色#00FF00单色无渐变。单角色完整身体。禁止文字水印。')
    print('pose urls ready', flush=True)

    tasks = {}
    for name, pose, motion, n, loop in ACTIONS:
        tid = submit_i2v(urls[pose], motion)
        tasks[name] = (tid, n, loop)
        print('submitted', name, tid, flush=True)

    summary = {}
    for name, (tid, n, loop) in tasks.items():
        try:
            vurl = poll(tid)
            cnt = extract(vurl, os.path.join(OUT, name), n, loop)
            summary[name] = 'ok %d frames' % cnt
            print('DONE', name, cnt, 'frames', flush=True)
        except Exception as e:
            summary[name] = 'FAIL %s' % e
            print('FAIL', name, str(e)[:200], flush=True)
    json.dump(summary, open(os.path.join(OUT, '_summary.json'), 'w', encoding='utf-8'), indent=1)
    print('SUMMARY', json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()