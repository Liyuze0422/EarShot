# -*- coding: utf-8 -*-
"""测:材料块大小 -> 首字延迟。用不同问题避免缓存命中，测真实冷启动。"""
import sys
import os
import time
import json
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
from openai import OpenAI
import answer as A
from knowledge import build

client = OpenAI(api_key=A.load_key(), base_url=A.BASE_URL)
idx, _ = build()

QS = [
    '推荐系统里双塔召回和粗排是怎么分工的？',
    '你那个推荐系统解决了什么问题？',
    '6GB 显存怎么把 1.5B 模型跑起来的？',
]

def build_user(q, topk, cut):
    hits = idx.search(q, topk=topk)
    mats = []
    for sc, src, txt in hits:
        mats.append(f'<!-- {src} -->\n{txt[:cut]}')
    return '<材料>\n' + '\n\n---\n\n'.join(mats) + f'\n</材料>\n\n面试官提问：{q}', hits

def probe(q, topk, cut):
    user, hits = build_user(q, topk, cut)
    t0 = time.time(); first = None; n = 0
    st = client.chat.completions.create(model=A.MODEL,
        messages=[{'role':'system','content':A.STRATEGIES['experience']},{'role':'user','content':user}],
        stream=True, temperature=0.2, max_tokens=400, extra_body=A.EXTRA_BODY)
    for ch in st:
        if ch.choices and getattr(ch.choices[0].delta, 'content', None):
            if first is None: first = time.time() - t0
            n += len(ch.choices[0].delta.content)
    return first, time.time()-t0, len(user), n

print('配置 (topk, 每块截断字符) -> 首字 / 总耗时 / 材料字数 / 答案字数')
print('-'*78)
configs = [(4, 10000), (4, 400), (3, 350), (3, 250), (2, 350)]
for i, q in enumerate(QS):
    print(f'\n问: {q}')
    for topk, cut in configs:
        try:
            first, total, ulen, alen = probe(q, topk, cut)
            print(f'  topk={topk} cut={cut:5d}  首字 {first*1000:6.0f} ms  总 {total:5.2f}s  材料 {ulen:5d} 字  答案 {alen:4d} 字')
        except Exception as e:
            print(f'  topk={topk} cut={cut:5d}  失败 {type(e).__name__} {str(e)[:60]}')
