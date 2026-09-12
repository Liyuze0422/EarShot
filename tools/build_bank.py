# -*- coding: utf-8 -*-
"""P4 会前题库生成：把面试材料跑成"口语问法 -> 可直接照念的答案"库。

为什么形态是这样（真实一面回放实测的结论）：
  - 口头追问极短、极口语、经常没有关键词（"那个推荐系统是你自己搭的吗""数据量有多大呢"），
    纯关键词检索救不回来——44 条真实提问里有 6 条至今没命中。
  - 但同一件事面试官会问 5 种方式，会前有无限时间可以把变体穷举出来。
  - 所以索引要建在**问法**上，不是建在知识点上。

用法:
  python tools/build_bank.py --smoke      # 只跑 4 个窗口，看质量
  python tools/build_bank.py              # 全量（约 40+ 次调用，6 并发）
"""
import sys, os, re, json, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))
from knowledge import build
import answer as A

FENCE = chr(96) * 3
WINDOW = 5000
PER_WINDOW = 10
WORKERS = 6
OUT = os.path.join(ROOT, '知识库', '题库.json')

SYS = """你在帮一个求职者做面试提词器。下面是他自己的面试材料片段。

请站在面试官的角度，生成他会怎么**口头**问的问题，以及求职者应该怎么回答。

硬要求：
1. 问题必须是**口语化追问**，像面试官现场随口问的：短、口语、常常没有关键词，
   经常带"那你""那这个""是吧""呢""有没有""怎么来呢"这类语气词。
   严禁写"请介绍一下XX""请阐述XX"这种标准题面。
2. 同一个知识点至少给 2 种不同的口语问法。
3. 答案要能**直接照着念**：口语、80~150 字、第一句就是结论、不要 markdown、不要分点符号。
4. 答案里的事实和数字**只能来自材料**，材料里没有的一个字都不许编。
5. 严格输出 JSON 数组，不要任何解释文字、不要代码块围栏：
   [{"q": "口语问法", "a": "可直接照念的答案"}]"""


def windows():
    idx, chunks = build()
    bysrc = {}
    for src, txt in chunks:
        bysrc.setdefault(src, []).append(txt)
    out = []
    for src, parts in bysrc.items():
        buf = ''
        for p in parts:
            if len(buf) + len(p) > WINDOW and buf:
                out.append((src, buf)); buf = ''
            buf += p + '\n'
        if len(buf) > 300:
            out.append((src, buf))
    return out


def parse_json(txt):
    txt = (txt or '').strip().replace(FENCE + 'json', '').replace(FENCE, '').strip()
    i, j = txt.find('['), txt.rfind(']')
    if i < 0 or j < 0:
        return []
    try:
        data = json.loads(txt[i:j + 1])
    except Exception:
        return []
    out = []
    for d in data:
        if isinstance(d, dict):
            q = str(d.get('q', '')).strip()
            a = str(d.get('a', '')).strip()
            if len(q) >= 4 and len(a) >= 20:
                out.append({'q': q, 'a': a})
    return out


def one(args):
    src, body = args
    from openai import OpenAI
    cli = OpenAI(api_key=A.load_key(), base_url=A.BASE_URL)
    try:
        r = cli.chat.completions.create(
            model=A.MODEL, stream=False, temperature=0.4, max_tokens=2000,
            extra_body=A.EXTRA_BODY,
            messages=[{'role': 'system', 'content': SYS},
                      {'role': 'user', 'content': '材料：\n%s\n\n生成 %d 条。' % (body, PER_WINDOW)}])  # noqa
        items = parse_json(r.choices[0].message.content)
    except Exception as e:
        return src, [], str(e)
    for it in items:
        it['src'] = src
        it['topic'] = src.split('_')[0]
    return src, items, ''


SYS_FOLLOWUP = """你在帮一个求职者做面试提词器。下面是他自己的面试材料片段。

请生成面试官在**同一个项目里连续追问**时会怎么问。这类问题的真实特征是：
  - 极短（常常 5~15 个字），大量用指代词："那这个""它""大概几个""那还有呢"
  - 几乎没有关键词，脱离上文根本不知道在问什么
  - 经常带"是吧""呢""对吧""有没有""怎么来呢""大概多少"
  - 例子："那个推荐系统是你自己搭的吗？""数据量有多大呢？""就是做了微调是吗？""大概这九个，串行的还是有并行的？"

硬要求：
1. 全部写成这种追问口吻，严禁写完整规范的题目。
2. 答案要能**直接照着念**：口语、80~150 字、第一句就是结论、不要 markdown、不要分点。
3. 事实和数字只能来自材料，材料里没有的一个字都不许编。
4. 严格输出 JSON 数组，不要解释文字、不要代码块围栏：
   [{"q": "追问", "a": "可直接照念的答案"}]"""


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--followup', action='store_true', help='生成"连续追问式"问法')
    ap.add_argument('--merge', action='store_true', help='合并进已有题库而不是覆盖')
    ap.add_argument('--workers', type=int, default=WORKERS)
    args = ap.parse_args()
    if args.followup:
        SYS = SYS_FOLLOWUP
        PER_WINDOW = 12

    ws = windows()
    if args.smoke:
        ws = ws[:4]
    print('窗口数 %d  (每窗 %d 条, %d 并发)' % (len(ws), PER_WINDOW, args.workers), flush=True)

    t0 = time.time(); allq = []; fails = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(one, w) for w in ws]
        for n, f in enumerate(as_completed(futs), 1):
            src, items, err = f.result()
            if err:
                fails += 1
            allq += items
            print('  [%2d/%d] %-44s +%2d 条 %s' % (n, len(ws), src[:42], len(items), err[:40]), flush=True)

    if args.merge and os.path.exists(OUT):
        old = json.load(open(OUT, encoding='utf-8')).get('items', [])
        print('合并已有题库 %d 条' % len(old))
        allq = old + allq
    seen = set(); uniq = []
    for it in allq:
        if it['q'] in seen:
            continue
        seen.add(it['q']); uniq.append(it)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({'source': '面试材料自动生成', 'count': len(uniq), 'items': uniq},
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print()
    print('=' * 70)
    print('生成 %d 条（去重后 %d），失败窗口 %d，用时 %.0f 秒' % (len(allq), len(uniq), fails, time.time() - t0))
    print('已写: %s' % OUT)
    from collections import Counter
    for s, n in Counter(x['src'] for x in uniq).most_common():
        print('  %3d  %s' % (n, s[:52]))
    print()
    print('--- 抽样 6 条 ---')
    import random
    for it in random.sample(uniq, min(6, len(uniq))):
        print('Q: %s' % it['q'])
        print('A: %s' % it['a'][:130])
        print()
