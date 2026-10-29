# -*- coding: utf-8 -*-
"""小问 idle 动作帧重新生成 — wan2.7-image-pro，三张参考图全上传"""
import base64, json, urllib.request, time, os, sys

KEY = 'sk-sp-H.DIREYL.B8ko.MEYCIQCTsbAPAGZ61CnvwOdKJnjB6X0c3ruwZ4c1w4Gl4FvEfgIhAOut3WziQ734sRNLRDiAwlzm6GKvLkoW-foW4SvQj_d0'
BASE = 'https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'

REF_DIR = r'D:\h\3.25\tmp_pet_test\refs_new'
OUT_DIR = r'D:\h\3.25\tmp_pet_test\idle_v2'
os.makedirs(OUT_DIR, exist_ok=True)

REFS = [
    ('ref1.png', '参考图1（动作分镜参考板）'),
    ('ref2.png', '参考图2（小问全身立绘）'),
    ('ref3.png', '参考图3（小问待机形象图）'),
]

def load_refs(max_side=1024):
    """加载并压缩参考图为 base64 data URL"""
    from PIL import Image
    items = []
    for fn, _ in REFS:
        p = os.path.join(REF_DIR, fn)
        im = Image.open(p).convert('RGB')
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = __import__('io').BytesIO()
        im.save(buf, 'PNG')
        items.append('data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode())
        print(f'  {fn} -> {im.size}')
    return items

# 6 帧待机循环设计（抱剑呼吸主循环 + 眨眼/小动作）
IDLE_FRAMES = [
    ('idle_01', '怀抱黑剑于胸前，站姿端正，正视前方，吸气到顶点，上身微微抬起，表情平静。'),
    ('idle_02', '怀抱黑剑于胸前，站姿与基准帧完全一致（同比例、同服饰、同白色波浪边过膝袜、同蓝穗刀鞘），正在眨眼瞬间——双眼完全闭合仅留细线，其余部位保持不动。禁止缩小，禁止换装，禁止加特效。'),
    ('idle_03', '怀抱黑剑于胸前，正面前视，正在眨眼的瞬间，眼睛半闭睁开的过渡，衣摆和马尾轻轻飘动，保持与基准帧完全相同的站姿比例。'),
    ('idle_04', '怀抱黑剑于胸前，肩膀微微下沉放松，表情柔和，剑尖稍向地面倾斜，保持与基准帧完全相同的比例和服饰细节（白色波浪边过膝袜、蓝穗刀鞘）。'),
    ('idle_05', '怀抱黑剑于胸前，站姿与基准帧完全一致（同比例、同服饰、同白色波浪边过膝袜、同蓝穗刀鞘），仅身体和头部极轻微下沉配合呼气，嘴角平缓，其余完全保持基准。禁止缩小，禁止换装，禁止加特效。'),
    ('idle_06', '怀抱黑剑于胸前，恢复正视前方，眼神清澈，站姿回到起始姿态，轻微含胸。'),
]

COMMON = (
    '角色必须与所有参考图完全一致：小问，Q版中国女剑士，约2到2.5头身，'
    '灰蓝色高马尾和头顶金色小发冠，刘海遮额，浅蓝白华山派道袍，白色盘扣内衫，'
    '深灰短裙配白袜黑靴，黑剑不离身，冰蓝剑意轻微点缀，'
    '脸型圆润、灰蓝色大眼、腮红，冷飒但可爱的傲娇气质。'
    '动作只做轻微待机变化，全身站位不变，脚底位置固定在同一水平基线，'
    '整体横向位置和画面比例保持稳定，禁止左右移动、禁止脸部变形、禁止更换服饰配饰。'
    '输出为透明背景PNG精灵帧，单角色完整身体居于画面中央，'
    '禁止文字、编号、对白框、水印、黑底、白底、阴影地面。'
)

def gen_frame(idx_name, desc, refs, out_path, base_b64=None):
    content = [{'image': r} for r in refs]
    # 身份锁定基准帧（首帧），后续帧共用，防表情/剑效/发饰漂移
    if base_b64:
        content.append({'image': base_b64})
    content.append({'text': COMMON + '动作：' + desc})
    body = {
        'model': 'wan2.7-image-pro',
        'input': {
            'messages': [{
                'role': 'user',
                'content': content,
            }]
        },
        'parameters': {'size': '1024*1024', 'n': 1,
                       'negative_prompt': '文字,水印,编号,对白框,黑底,白底,网格,模糊,变形,换装,移动,多角色'}
    }
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + KEY,
                                          'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=240)
    j = json.loads(r.read())
    msg = j['output']['choices'][0]['message']['content']
    for c in msg:
        if c.get('type') == 'image':
            url = c['image']
            urllib.request.urlretrieve(url, out_path)
            return True
    return False

def load_base():
    """idle_01 压缩为基准帧 data url"""
    from PIL import Image
    import io
    p = os.path.join(OUT_DIR, 'idle_01.png')
    if not os.path.exists(p):
        return None
    im = Image.open(p).convert('RGB')
    im.thumbnail((512, 512), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    print('  base frame ->', im.size)
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()

def main():
    which = sys.argv[1:] if len(sys.argv) > 1 else ['idle_01']
    refs = load_refs()
    base_b64 = load_base()
    for idx_name, desc in IDLE_FRAMES:
        if which and idx_name not in which:
            continue
        out = os.path.join(OUT_DIR, idx_name + '.png')
        if os.path.exists(out):
            print(f'skip {idx_name} (exists)')
            continue
        print(f'=== gen {idx_name} ===')
        t0 = time.time()
        ok = gen_frame(idx_name, desc, refs, out, base_b64)
        print(f'  done {ok} in {time.time()-t0:.1f}s -> {out}')

if __name__ == '__main__':
    main()