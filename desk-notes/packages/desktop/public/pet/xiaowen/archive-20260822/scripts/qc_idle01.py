# -*- coding: utf-8 -*-
import base64, json, urllib.request
KEY = 'sk-sp-H.DIREYL.B8ko.MEYCIQCTsbAPAGZ61CnvwOdKJnjB6X0c3ruwZ4c1w4Gl4FvEfgIhAOut3WziQ734sRNLRDiAwlzm6GKvLkoW-foW4SvQj_d0'
def b64(p):
    return base64.b64encode(open(p,'rb').read()).decode()
img = b64(r'D:\h\3.25\tmp_pet_test\idle_v2\idle_01.png')
body = {'model':'qwen3.8-max','messages':[{'role':'user','content':[
 {'type':'image_url','image_url':{'url':'data:image/png;base64,'+img}},
 {'type':'text','text':'用中文简体回：这是一张桌面宠物精灵图。1)角色外观（画风/发型/服饰/手持物/姿势）2)背景是什么颜色、是否干净 3)有无文字水印 4)打分1-10这帧待机帧的质量，以及帧间生成时最容易漂移的地方'}]}],'max_tokens':700}
req = urllib.request.Request('https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions',
 data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+KEY,'Content-Type':'application/json'})
j=json.loads(urllib.request.urlopen(req,timeout=180).read())
open(r'D:\h\3.25\tmp_pet_test\qc_idle01.txt','w',encoding='utf-8').write(j['choices'][0]['message']['content'])
print('done')
