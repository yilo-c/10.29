# -*- coding: utf-8 -*-
"""对比审查：img2img 结果 vs 参考立绘，检查身份一致性"""
import base64, json, urllib.request, io

KEY = 'sk-sp-H.DIREYL.B8ko.MEYCIQCTsbAPAGZ61CnvwOdKJnjB6X0c3ruwZ4c1w4Gl4FvEfgIhAOut3WziQ734sRNLRDiAwlzm6GKvLkoW-foW4SvQj_d0'

def b64(path):
    return base64.b64encode(open(path, 'rb').read()).decode()

ref = b64(r'D:\h\3.25\tmp_pet_test\ref_fullbody.png')
gen = b64(r'D:\h\3.25\tmp_pet_test\img2img_idle_01.png')

prompt = (
    '第一张图是角色参考立绘（小问：灰蓝高马尾、浅蓝白华山派服饰、手持黑色长剑、Q版）。'
    '第二张图是基于第一张图生成的“待机动作”精灵帧。'
    '请严格对比两张图，回答：'
    '1) 身份一致性：发型、发色、服饰款式与配色、黑剑、脸型、头身比是否一致？哪些地方变了？'
    '2) 背景/透明情况 3) 有无文字水印编号 4) 作为连续动作帧序列的第一帧，质量是否可用？给出 1-10 一致性评分。'
    '用中文简体回复。'
)

msg = {
    'model': 'qwen3.8-max',
    'messages': [{
        'role': 'user',
        'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + ref}},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + gen}},
            {'type': 'text', 'text': prompt},
        ],
    }],
    'max_tokens': 900,
}
req = urllib.request.Request(
    'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions',
    data=json.dumps(msg).encode(),
    headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
try:
    r = urllib.request.urlopen(req, timeout=180)
    j = json.loads(r.read())
    open(r'D:\h\3.25\tmp_pet_test\consistency_review.txt', 'w', encoding='utf-8').write(
        j['choices'][0]['message']['content'])
    print('done, see consistency_review.txt')
except Exception as e:
    print('ERR:', repr(e))