# -*- coding: utf-8 -*-
"""把一场面试变成下一场的准备：答砸的题 + 被误拦的提问。

为什么要有它：session_report.py 的报告里写着「这些提问原文可以直接追加进
tests/real_questions.json」，但那要人一条条手抄；更要紧的是**它完全没看被过滤器
拦掉的那部分** —— 而一场真实面试里被拦下的往往有几百条，真正被漏放的面试官提问
就埋在里面，那是复盘里最值钱的一块。

这个脚本一次给出两样：
  1. 该复查的题（判据复用 session_report.verdict，不另立一套标准）；
  2. 拦下的句子里**像面试官在提问**的那些，按像的程度排序（漏放候选）。

输出两份文件到 logs/：一份给人看的 markdown，一份能直接合并进
tests/real_questions.json 的 JSON 片段。

**它不改任何东西** —— 不动作题库、不动材料、不动回归集。
要不要吸收，看了清单你自己决定。

用法:
  python tools/absorb_session.py              # 最近一场
  python tools/absorb_session.py --list       # 列出所有会话
  python tools/absorb_session.py --top 40     # 漏放候选最多列 40 条
"""
import os
import re
import sys
import json
import glob
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import session_report as SR          # noqa: E402  判据只有一套，复用

LOG_DIR = os.path.join(ROOT, 'logs')

# 疑问标记：面试官提问几乎必带一个
_Q = re.compile(r'[？?]|吗|呢|什么|怎么|为什么|哪|多少|几个|如何|是不是|有没有|对不对|可以吗|好吗')
_YOU = re.compile(r'你|您')
_ME = re.compile(r'我')


def question_score(s):
    """这句话有多像面试官在提问。分数只用来排序，不下判决。

    判据都是可复核的表面特征，没有任何模型参与 —— 所以你可以逐条反驳它。
    """
    score = 0
    if _Q.search(s):
        score += 3
    n_you = len(_YOU.findall(s))
    n_me = len(_ME.findall(s))
    if n_you > n_me:
        score += 2                      # 满篇「你/您」是面试官
    elif n_you and n_me:
        score += 1
    if s.rstrip().endswith(('？', '?')):
        score += 1
    if 6 <= len(s) <= 40:
        score += 1                      # 太长的多半是候选人在陈述
    return score


def leaked_questions(recs, top=30, min_score=4):
    """被拦下、但看起来像真问题的句子。"""
    seen = set()
    out = []
    for r in recs:
        if r.get('event') != 'filtered':
            continue
        q = (r.get('q') or '').strip()
        if len(q) < 4 or q in seen:
            continue
        seen.add(q)
        sc = question_score(q)
        if sc >= min_score:
            out.append((sc, q, r.get('ts', '')))
    out.sort(key=lambda x: (-x[0], -len(x[1])))
    return out[:top]


def to_regress_items(pairs, start=1):
    """整理成能直接合并进 tests/real_questions.json 的形状（label 让 router 猜）。"""
    items = []
    try:
        sys.path.insert(0, os.path.join(ROOT, 'server'))
        from router import classify
    except Exception:
        classify = None
    for n, (sc, q, ts) in enumerate(pairs, start):
        label = 'unknown'
        if classify:
            try:
                label = classify(q)
            except Exception:
                pass
        items.append({'i': n, 'text': q, 'label': label, '_score': sc, '_ts': ts})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--top', type=int, default=30, help='漏放候选最多列几条')
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    files = sorted(glob.glob(os.path.join(LOG_DIR, 'session_*.jsonl')))
    if a.list or not files:
        print('会话日志（%s）:' % LOG_DIR)
        for f in files:
            print('  %-46s %.1f KB' % (os.path.basename(f), os.path.getsize(f) / 1024.0))
        if not files:
            print('  还没有。')
        return 0
    path = a.file or files[-1]
    sid = os.path.basename(path).replace('session_', '').replace('.jsonl', '')
    recs = SR.load(path)
    rows, filtered = SR.analyze(recs)
    bad = [(r, SR.verdict(r)) for r in rows]
    nbad = [x for x in bad if x[1]]
    leaks = leaked_questions(recs, top=a.top)

    L = []
    L.append('# 面试吸收清单 · %s' % sid)
    L.append('')
    L.append('问题 %d 条 · 有答案 %d 条 · 被拦下 %d 条 · 需复查 %d 条 · 漏放候选 %d 条'
             % (len(rows), sum(1 for r in rows if r['firstMs']), filtered, len(nbad), len(leaks)))
    L.append('')
    L.append('## 一、该复查的题')
    L.append('')
    if nbad:
        L.append('| # | 提问 | 复查点 |')
        L.append('|---|---|---|')
        for r, v in nbad:
            L.append('| %s | %s | %s |' % (r['qid'], (r['q'] or '')[:46].replace('|', '/'), '、'.join(v)))
    else:
        L.append('（这一场没有需要复查的题）')
    L.append('')
    L.append('## 二、被拦下但像真问题的（漏放候选）')
    L.append('')
    L.append('这些句子进了过滤器被丢掉了。**逐条自己看** —— 判据只是「带疑问词 / 用你您 / 长度合适」，')
    L.append('它会误报。确认是面试官提问的，就是真正的漏放。')
    L.append('')
    if leaks:
        L.append('| 分 | 时间 | 句子 |')
        L.append('|---|---|---|')
        for sc, q, ts in leaks:
            L.append('| %d | %s | %s |' % (sc, (ts or '')[-8:], q[:60].replace('|', '/')))
    else:
        L.append('（没有够像的 —— 说明这一场几乎没漏放）')
    L.append('')
    L.append('## 三、下一场可以直接用的回归集片段')
    L.append('')
    L.append('`%s` 里的 questions 数组可以直接追加进 `tests/real_questions.json`。' % ('logs/absorb_%s.json' % sid))
    L.append('`_score` / `_ts` 是给你判断用的，合并前删掉；`label` 是 router 猜的，也请核一遍。')
    md = os.path.join(LOG_DIR, 'absorb_%s.md' % sid)
    js = os.path.join(LOG_DIR, 'absorb_%s.json' % sid)
    open(md, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    json.dump({'questions': to_regress_items(leaks)},
              open(js, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    print('\n'.join(L[:10]))
    print()
    print('清单: %s' % md)
    print('片段: %s' % js)
    print()
    print('注意：本脚本不改任何东西。要不要吸收，看完清单再决定。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
