# -*- coding: utf-8 -*-
"""离线生成「换个说法」扩展词：让面试官用自己的话问也能命中材料。

为什么需要（2026-09-14 实测，19 条换说法基准）
------------------------------------------------
面试官分析完简历后，常用**材料里根本没出现的措辞**指代同一件事：
材料写"LangGraph 九节点有向图工作流"，他问"你那套任务编排是怎么设计的"；
材料写"自然语言解析三级降级链"，他问"你说的那套语义理解具体指什么"。

词级 BM25 靠字面重合，一换说法就归零：

    原题     hit@1 61.5%   hit@3 74.4%
    换说法   hit@1 15.8%   hit@3 42.1%     <- 崩了 3/4

做法（doc2query）
-----------------
离线让模型读每一块材料，生成"面试官可能怎么问这块"的**通用说法**，
编进该块自己的词袋。运行时不发任何请求，延迟和花费都不变。

存 `config/doc_expand.json`，按**内容 hash** 索引（不是块序号）——
材料改一个字，hash 变了这块就没有扩展词，自动降级，不会张冠李戴。

用法
----
    python tools/kb_expand.py                 # 增量生成（只补没做过的块）
    python tools/kb_expand.py --dry-run       # 只报还差多少块，不调模型
    python tools/kb_expand.py --limit 12      # 先跑 12 块看质量
    python tools/kb_expand.py --redo          # 全部重做
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))

import knowledge                                     # noqa: E402
import settings                                      # noqa: E402
import answer                                        # noqa: E402

OUT = os.path.join(ROOT, 'config', 'doc_expand.json')
BATCH = 6               # 每次请求塞几块材料
PER_CHUNK = 5           # 每块生成几个问法
WORKERS = 4

PROMPT = '''你在帮一个中文面试提词器做检索索引。下面有 {{n}} 块材料，每块带编号。

请对**每一块**写出 {{k}} 个面试官**可能用来提问这块内容**的中文问句。

硬性要求：
1. 尽量**不用材料里的原词**。面试官不会照抄你的措辞，他会用自己的通用说法。
   例：材料写"LangGraph 九节点有向图工作流"，该生成"你那套任务编排是怎么设计的"、
   "这个流程分成几个环节"；材料写"自然语言解析三级降级链"，该生成
   "你说的语义理解具体指什么"、"用户说法不规范的时候怎么办"。
2. 每条 8~25 字，口语化，像真人面试官在问。
3. 要能**唯一指向这一块**。禁止"介绍一下你的项目""这个怎么实现的"这类放之四海的空话。
4. 保留必要的专有名词（如果这块的核心就是某个技术名），但其余部分换成通用说法。

只输出 JSON，形如 {{"1": ["问句", "问句"], "2": ["问句"]}}，不要任何解释、不要代码块围栏。

{{chunks}}'''


def chunk_key(text):
    """按内容 hash 索引，材料改了这块扩展词自动作废。"""
    return hashlib.sha1(re.sub(r'\s+', '', text or '').encode('utf-8')).hexdigest()[:16]


def load_out():
    if os.path.exists(OUT):
        try:
            return json.loads(open(OUT, encoding='utf-8').read())
        except Exception:
            print('警告：%s 解析失败，当作空表重来' % OUT)
    return {}


def save_out(d):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)


def _parse(txt):
    t = (txt or '').strip()
    t = re.sub(r'^\s*```(?:json)?\s*', '', t)
    t = re.sub(r'\s*```\s*$', '', t)
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


def gen_batch(batch, client):
    """batch = [(idx, text)]，返回 {idx: [问法]}。"""
    body = []
    for n, (_, txt) in enumerate(batch, 1):
        body.append('【%d】%s' % (n, txt[:900]))
    prompt = PROMPT.replace('{{n}}', str(len(batch))).replace('{{k}}', str(PER_CHUNK)) \
                   .replace('{{chunks}}', '\n\n'.join(body))
    r = client.chat.completions.create(
        model=settings.get('llm_model'),
        messages=[{'role': 'user', 'content': prompt}],
        temperature=0.3,
        timeout=180,
    )
    obj = _parse(r.choices[0].message.content)
    out = {}
    for n, (idx, _) in enumerate(batch, 1):
        v = obj.get(str(n)) or obj.get(n) or []
        if isinstance(v, str):
            v = [v]
        out[idx] = [str(x).strip() for x in v if str(x).strip()][:PER_CHUNK + 2]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--redo', action='store_true')
    args = ap.parse_args()

    chunks = knowledge.build()[1]
    print('语料 %d 块（%d 个文件）' % (len(chunks), len({s for s, _ in chunks})))

    table = {} if args.redo else load_out()
    todo = []
    for i, (src, txt) in enumerate(chunks):
        k = chunk_key(txt)
        if k in table and table[k]:
            continue
        todo.append((i, txt))
    print('已有扩展词 %d 块 | 待生成 %d 块' % (len(table), len(todo)))

    if args.dry_run or not todo:
        if args.dry_run:
            print('  --dry-run：没有调模型。去掉这个参数开始生成。')
        return
    if args.limit:
        todo = todo[:args.limit]
        print('  --limit 生效，本次只做 %d 块' % len(todo))

    from openai import OpenAI
    client = OpenAI(api_key=answer.load_key(), base_url=answer.BASE_URL, max_retries=2)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    print('分成 %d 批，%d 并发' % (len(batches), WORKERS))

    done = [0]
    fails = [0]
    t0 = time.time()

    logf = open(os.path.join(ROOT, 'config', 'doc_expand.log'), 'a', encoding='utf-8')

    def log(s):
        logf.write('%s %s\n' % (time.strftime('%H:%M:%S'), s))
        logf.flush()

    def work(b):
        t = time.time()
        try:
            r = gen_batch(b, client)
            log('OK   %d 块 %.1fs' % (len(b), time.time() - t))
            return b, r, None
        except Exception as e:
            log('FAIL %d 块 %.1fs %s: %s' % (len(b), time.time() - t, type(e).__name__, str(e)[:200]))
            return b, None, e

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for b, got, err in ex.map(work, batches):
            if err:
                print('  某批失败（跳过，下次重跑会补）：%s' % str(err)[:120])
                fails[0] += 1
                continue
            for idx, _ in b:
                table[chunk_key(chunks[idx][1])] = got.get(idx, [])
            done[0] += len(b)
            el = time.time() - t0
            print('  %3d/%d 块  已用 %.0fs  预计还要 %.0fs'
                  % (done[0], len(todo), el, el / max(done[0], 1) * (len(todo) - done[0])))
            save_out(table)

    save_out(table)
    logf.close()
    if fails[0]:
        print('  本次有 %d 批失败（再跑一次会自动补）' % fails[0])
    n_with = sum(1 for c in chunks if table.get(chunk_key(c[1])))
    print()
    print('完成：%d/%d 块有扩展词 -> %s' % (n_with, len(chunks), OUT))
    print('把 config/doc_expand.json 留着即可，重新建索引会自动用上。')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
