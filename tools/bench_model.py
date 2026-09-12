# -*- coding: utf-8 -*-
"""测首字延迟:候选模型 x 是否开思考。"""
import sys
import os
import time
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
from openai import OpenAI
from answer import load_key, SYSTEM, build_prompt, BASE_URL

client = OpenAI(api_key=load_key(), base_url=BASE_URL)

print('=== 可用模型 ===')
try:
    for m in client.models.list().data:
        print('  -', m.id)
except Exception as e:
    print('  查询失败:', type(e).__name__, str(e)[:120])
print()

q = '你在那个推荐系统里怎么做召回和粗排的？'
user, _ = build_prompt(q, topk=4)
msgs = [{'role':'system','content':SYSTEM},{'role':'user','content':user}]

def probe(model, extra):
    t0 = time.time(); first=None; n=0
    try:
        st = client.chat.completions.create(model=model, messages=msgs, stream=True,
                                            temperature=0.2, max_tokens=400, **extra)
        for ch in st:
            if ch.choices and getattr(ch.choices[0].delta, 'content', None):
                if first is None: first = time.time()-t0
                n += 1
        return f'首字 {first*1000:6.0f} ms   总 {time.time()-t0:.2f}s   分片 {n}'
    except Exception as e:
        return f'失败: {type(e).__name__} {str(e)[:90]}'

print('=== 首字延迟对比（各跑 1 次）===')
cands = [
    ('deepseek-chat', {}),
    ('deepseek-chat', {'extra_body': {'thinking': {'type': 'disabled'}}}),
    ('deepseek-v4-flash', {}),
    ('deepseek-v4-flash', {'extra_body': {'thinking': {'type': 'disabled'}}}),
    ('deepseek-flash', {}),
    ('deepseek-flash', {'extra_body': {'thinking': {'type': 'disabled'}}}),
]
# 不再测 deepseek-v4-pro：它是思考模型（实测 6690ms 首字），面试场景用不上，全项目统一走 flash。
for model, extra in cands:
    tag = model + (' +关思考' if extra else '')
    print(f'  {tag:34s} {probe(model, extra)}')
