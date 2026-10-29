# -*- coding: utf-8 -*-
"""小问图生图测试：用 xiaowen_fullbody.png 锁身份 → 生成 idle 待机动作帧"""
import base64, json, urllib.request, sys, io

KEY = 'sk-sp-H.DIREYL.B8ko.MEYCIQCTsbAPAGZ61CnvwOdKJnjB6X0c3ruwZ4c1w4Gl4FvEfgIhAOut3WziQ734sRNLRDiAwlzm6GKvLkoW-foW4SvQj_d0'
BASE = 'https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'

ref_b64 = base64.b64encode(open(r'D:\h\3.25\tmp_pet_test\ref_fullbody.png', 'rb').read()).decode()

prompt = (
    '保持参考图中角色完全一致（灰蓝高马尾、浅蓝白华山派服饰、黑色长剑、'
    '冰蓝剑意、脸型与发饰、配色、头身比例），仅改变动作：'
    '生成她的“待机”动作帧——怀中抱剑，轻微呼吸起伏，头发和衣摆微动。'
    'Q版Q头身，透明背景，单角色完整身体，禁止文字、编号、对白框、背景、阴影地面。'
    '适合256x256桌宠sprite。'
)

body = {
    'model': 'wan2.7-image',
    'input': {
        'messages': [{
            'role': 'user',
            'content': [
                {'image': 'data:image/png;base64,' + ref_b64},
                {'text': prompt},
            ],
        }]
    },
    'parameters': {'size': '1024*1024', 'n': 1, 'negative_prompt': '文字, 水印, 编号, 对白框, 黑底, 白底, 网格, 模糊'},
}

req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
                             headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
try:
    r = urllib.request.urlopen(req, timeout=180)
    j = json.loads(r.read())
    out = j['output']
    msg = out['choices'][0]['message']['content']
    print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
    for c in msg:
        if c.get('type') == 'image':
            url = c['image']
            print('IMAGE_URL:', url)
            urllib.request.urlretrieve(url, r'D:\h\3.25\tmp_pet_test\img2img_idle_01.png')
            print('saved img2img_idle_01.png')
except Exception as e:
    print('ERR:', repr(e))
    try:
        print('body:', e.read().decode() if hasattr(e, 'read') else '')
    except Exception:
        pass