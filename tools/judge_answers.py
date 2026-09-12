# -*- coding: utf-8 -*-
"""答案质量评分（LLM-as-judge）。

之前只能测"格式/延迟/数字幻觉"，测不了"答得好不好"——所以 P4/P5 做完了也不知道是不是真变好了。
这个脚本给每条答案打一个可追踪的分数，作为质量基线。

用法: python tools/judge_answers.py [结果文件]   默认 tests/e2e_replay_result.json
"""
import sys
import os
import re
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))
import answer as A

FENCE = chr(96) * 3
# 一律用 flash + 关思考：v4-pro 是思考模型，推理会吃掉 max_tokens 导致空回复，
# 反而制造一堆"判分失败"。flash 判分足够稳定，且快得多。
JUDGE_MODEL = A.MODEL
FALLBACK_MODEL = 'deepseek-chat'

RUBRIC = """你是资深面试教练。下面给你一场真实面试里，面试官的一句提问、它的上文、以及一个提词器给出的答案。

请按这三条打分（宁严勿宽）：

1. **切题**：这个答案有没有正面回答面试官问的那件事？注意很多提问是接着上文问的，
   要结合上文判断。如果答案在自说自话、答的是别的问题，直接判 1 分。
2. **可照念**：这句话能不能抬头直接念给面试官听？
   口语、不绕、没有 markdown、没有"我判断面试官想问的是…"这类分析对方动机的元描述。
   注意：【思路】【展开】【数字】这类方括号标记是提词器 UI 用来分区的脚手架，**显示时会被剥掉**，
   不要因为标记本身扣分——请把它们去掉之后再判断正文能不能照念。
   另外要判断长度跟问题分量搭不搭：面试官只问一句"是今年毕业的是吗？"，
   回一整段 150 字的项目细节就算不可照念。完全不能念判 1 分。
3. **不空**：有没有具体信息（做法、权衡、事实），还是全是"要看情况""要结合业务"这种空话？
   全是空话判 1 分。但**对短确认题**（面试官只问一句是/不是），
   只要给了明确的是/不是加一句依据，就算不空——不要因为它没展开而扣分。

综合成 1~5 分（整数）。只输出一个 JSON 对象，不要任何解释文字、不要代码块围栏：
{"score": 4, "offtopic": false, "unreadable": false, "reason": "一句话说清扣分点"}"""


def parse_judge(txt):
    """模型经常在 JSON 里夹未转义的引号/换行，别指望 json.loads 一次成功。"""
    t = (txt or '').strip().replace(FENCE + 'json', '').replace(FENCE, '').strip()
    i, jx = t.find('{'), t.rfind('}')
    if i >= 0 and jx > i:
        try:
            return json.loads(t[i:jx + 1])
        except Exception:
            pass
    d = {}
    m = re.search(r'"?score"?\s*[:：]\s*(\d)', t)
    if m:
        d['score'] = int(m.group(1))
    for key in ('offtopic', 'unreadable'):
        m = re.search(r'"?%s"?\s*[:：]\s*(true|false|是|否)' % key, t)
        d[key] = (m.group(1) in ('true', '是')) if m else None
    m = re.search(r'"?reason"?\s*[:：]\s*"?([^"\n]{4,120})', t)
    d['reason'] = m.group(1).strip() if m else t.replace('\n', ' ')[:90]
    return d if d.get('score') else None


def judge(rec, prev):
    from openai import OpenAI
    cli = OpenAI(api_key=A.load_key(), base_url=A.BASE_URL)
    ctx = ('上文（面试官前几句）：\n%s\n\n' % '\n'.join(prev)) if prev else ''
    user = '%s面试官这句问的是：%s\n\n提词器给的答案：\n%s' % (ctx, rec['q'], rec.get('answer', '')[:900])
    err = '未知'
    for model in (JUDGE_MODEL, FALLBACK_MODEL):
        try:
            r = cli.chat.completions.create(
                model=model, temperature=0.0, max_tokens=3000,   # v4-pro 是思考模型，700 会被推理吃光导致空回复
                messages=[{'role': 'system', 'content': RUBRIC}, {'role': 'user', 'content': user}])
            d = parse_judge(r.choices[0].message.content or '')
            if d is None:
                raise ValueError('判分输出解析失败')
            d['judge_model'] = model
            return d
        except Exception as e:
            err = str(e)[:80]
    return {'score': 0, 'offtopic': None, 'unreadable': None, 'reason': 'judge失败:' + err, 'judge_model': ''}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('src', nargs='?', default=os.path.join(ROOT, 'tests', 'e2e_replay_result.json'))
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()

    rows = json.load(open(args.src, encoding='utf-8'))
    rows = [r for r in rows if r.get('answer')]
    print('待评答案 %d 条，%d 并发，判分模型 %s' % (len(rows), args.workers, JUDGE_MODEL), flush=True)
    t0 = time.time()
    out = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for k, r in enumerate(rows):
            prev = [x['q'] for x in rows[max(0, k - 2):k]]
            futs[ex.submit(judge, r, prev)] = r
        for f in as_completed(futs):
            r = futs[f]
            d = f.result()
            r2 = dict(r); r2.update(d)
            out.append(r2)
            print('  #%-3s %d分 %-34s %s' % (r['i'], d['score'], (d['reason'] or '')[:32], r['q'][:26]), flush=True)

    out.sort(key=lambda x: str(x['i']))
    dst = os.path.join(ROOT, 'tests', 'judge_result.json')
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    sc = [x['score'] for x in out if x['score']]
    import statistics
    print()
    print('=' * 68)
    print('答案质量评分   n=%d   用时 %.0f 秒' % (len(out), time.time() - t0))
    print('=' * 68)
    if sc:
        print('平均分        : %.2f / 5   (中位 %d)' % (statistics.mean(sc), statistics.median(sc)))
        for s in (5, 4, 3, 2, 1):
            c = sum(1 for x in sc if x == s)
            print('  %d 分: %2d 条  %s' % (s, c, '#' * c))
    print('答非所问      : %d 条' % sum(1 for x in out if x.get('offtopic')))
    print('不能照念      : %d 条' % sum(1 for x in out if x.get('unreadable')))
    print()
    print('--- 最差的 6 条（优先修这些）---')
    for x in sorted(out, key=lambda y: y['score'])[:6]:
        print('#%s  %d分  %s' % (x['i'], x['score'], x['q'][:40]))
        print('     %s' % (x['reason'] or '')[:78])
        print('     答案: %s' % (x.get('core') or x['answer'][:60])[:70])
    print('已写: %s' % dst)
