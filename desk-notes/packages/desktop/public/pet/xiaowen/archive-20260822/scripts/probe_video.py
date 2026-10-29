# -*- coding: utf-8 -*-
"""探测视频模型可用性：MaaS happyhorse + 火山 flf2v"""
import base64, json, io, os, urllib.request, urllib.error
from PIL import Image

# 2026-09-12 安全整改：明文密钥已外置到环境变量（原值入库=已泄露，须轮换）
KEY = os.environ.get('MAAS_TOKEN_PLAN_KEY', '')
M = 'https://token-plan.cn-beijing.maas.aliyuncs.com'
VOLC_KEY = os.environ.get('ARK_API_KEY', '')
V = 'https://ark.cn-beijing.volces.com/api/v3'
if not KEY or not VOLC_KEY:
    raise SystemExit('缺少环境变量 MAAS_TOKEN_PLAN_KEY / ARK_API_KEY（密钥已外置，勿再硬编码）')


def b64ref(p, side=768):
    im = Image.open(p).convert('RGB')
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return 'OK', json.loads(r.read())
    except urllib.error.HTTPError as e:
        return 'HTTP %d' % e.code, e.read().decode()[:500]


# 1) 绿幕图 → OSS URL
refs = [b64ref(r'D:\h\3.25\tmp_pet_test\refs_new\ref%d.png' % i) for i in (1, 2, 3)]
body = {'model': 'wan2.7-image-pro', 'input': {'messages': [{'role': 'user', 'content':
        [{'image': r} for r in refs] + [{'text': '角色与参考图完全一致。怀中抱剑于胸前，站姿端正正视前方平静待机。背景纯绿色#00FF00单色无渐变无阴影。单角色完整身体居中。禁止文字水印。'}]}]},
        'parameters': {'size': '1024*1024', 'n': 1}}
st, j = post(M + '/api/v1/services/aigc/multimodal-generation/generation', body,
             {'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
url = [c['image'] for c in j['output']['choices'][0]['message']['content'] if c.get('type') == 'image'][0]
open(r'D:\h\3.25\tmp_pet_test\green_url.txt', 'w').write(url)
print('green url ok')

H = {'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json', 'X-DashScope-Async': 'enable'}

# 2) MaaS happyhorse-1.1-i2v
st, r = post(M + '/api/v1/services/aigc/video-generation/video-synthesis',
             {'model': 'happyhorse-1.1-i2v', 'input': {'prompt': '轻微呼吸，马尾微摆，镜头固定', 'img_url': url},
              'parameters': {'resolution': '720P', 'duration': 3}}, H)
print('MaaS happyhorse-1.1-i2v:', st, str(r)[:400])

# 3) MaaS happyhorse-1.1-r2v（首尾帧?）
st, r = post(M + '/api/v1/services/aigc/video-generation/video-synthesis',
             {'model': 'happyhorse-1.1-r2v', 'input': {'prompt': '轻微呼吸，镜头固定', 'first_frame_url': url, 'last_frame_url': url},
              'parameters': {'resolution': '720P', 'duration': 3}}, H)
print('MaaS happyhorse-1.1-r2v:', st, str(r)[:400])

# 4) 火山 wan2-1-14b-flf2v（content 格式探测）
st, r = post(V + '/contents/generations/tasks',
             {'model': 'wan2-1-14b-flf2v-250417',
              'content': [{'type': 'text', 'text': '轻微呼吸，镜头固定'},
                          {'type': 'image_url', 'image_url': {'url': url}},
                          {'type': 'image_url', 'image_url': {'url': url}}],
              'resolution': '720p', 'duration': 3},
             {'Authorization': 'Bearer ' + VOLC_KEY, 'Content-Type': 'application/json'})
print('Volcano flf2v:', st, str(r)[:400])
