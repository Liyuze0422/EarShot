# -*- coding: utf-8 -*-
"""会前 2 分钟：把材料里的技术名词列出来，让我自己标"会 / 没做过"。

为什么需要它：提词器判"这块我准没准备过"只能看材料里有没有这个词，可是
  ① 材料（很多本身就是转写稿）里的专有名词自己就可能是错的。实测一场真实转写稿：
     34 个"材料查无此词"的英文里，13 个其实是 lara←LoRA、lmm/lim←LLM、
     rug/reg←RAG、rog←ROS、imm←IMU 这种被写坏的已知词；
  ② 反过来，材料里出现过的词也不代表我会。
所以"我到底会不会"这件事最终得由人给一次锚点 —— 这个脚本负责把候选列出来。

输出 config/terms_review.md：会的抄进 config/known_terms.md，
材料里有但我没做过的抄进 config/never_used.md。

用法: python tools/build_terms.py
"""
import sys
import os
import re
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import answer as A
from knowledge import build

OUT = os.path.join(ROOT, 'config', 'terms_review.md')
# 常见英文小词（材料里一堆 if/vs/return，人工过表时是纯噪音）
SKIP = set('''the and for with www com png jpg utf sdk if in is are was were be been
vs return base facts pp you your we our it its on at to of my me not no yes can could
will would should may might all any one two three new old use used user users get set got
run runs top row col data file files line lines text name names type types value values
true false none null none here there this that these those what when where which who how
why do does did done have has had very more most much many some such only also than then
them they he she his her out up down over under again just about into from'''.split() + ['ut'])


def corpus_chunks():
    _, chunks = build()
    return chunks


def ascii_terms(texts):
    from collections import Counter
    c = Counter()
    for t in texts:
        for w in re.findall(r'[A-Za-z][A-Za-z0-9.+#_-]{1,20}', A.normalize_terms(t)):
            w = w.strip('.-_+').lower()
            if len(w) < 2 or len(re.findall(r'[A-Za-z]', w)) < 2 or w in SKIP or A._VER.match(w):
                continue
            c[w] += 1
    return c


def cjk_terms(texts, min_freq=4):
    """材料里反复出现的名词性词（跳过通用高频词）。"""
    import jieba
    import jieba.posseg as pseg
    from collections import Counter
    jieba.initialize()
    c = Counter()
    for t in texts:
        for w, f in pseg.cut(t):
            if len(w) >= 3 and f[0] in 'nvlj' and f not in ('nr', 'ns', 'nt', 'nrfg'):
                if jieba.dt.FREQ.get(w, 0) < 800:      # 太通用的词不要
                    c[w] += 1
    return Counter({w: n for w, n in c.items() if n >= min_freq})


def main():
    chunks = corpus_chunks()
    texts = [t for _, t in chunks]
    v = A._vocab(); kn = A.known_terms()
    av = ascii_terms(texts)

    rows_in, rows_out = [], []
    for w, n in av.most_common(400):
        if w in kn:
            continue
        if w in v:
            rows_in.append((w, n, '材料里有'))
        else:
            fz = A.fuzzy_known(w)
            rows_out.append((w, n, ('疑似 %s（转写写坏了？）' % fz) if fz else '材料里没有'))

    L = ['# 会前专名词表（请人工过一遍）', '',
         '> 由 `tools/build_terms.py` 生成：%d 块材料 / %d 个英文词。' % (len(chunks), len(av)),
         '> **会的**抄进 `config/known_terms.md`；**材料里有但我没做过的**抄进 `config/never_used.md`。',
         '> 不求全 —— 只求"我会的东西不被判成没接触过，我没做过的别装会"。', '',
         '## 一、材料里出现过、需要确认"我到底会不会"（%d 个）' % len(rows_in), '',
         '| 词 | 材料出现 | 备注 | 我确认（会/听过/没做过） |', '|---|---|---|---|']
    for w, n, note in rows_in[:80]:
        L.append('| %s | %d | %s |  |' % (w, n, note))
    L += ['', '## 二、材料里没有、可能是写坏的已知词或真不认识的（%d 个）' % len(rows_out), '',
          '| 词 | 材料出现 | 备注 | 我确认（会/没做过） |', '|---|---|---|---|']
    for w, n, note in rows_out[:80]:
        L.append('| %s | %d | %s |  |' % (w, n, note))

    cj = cjk_terms(texts)
    L += ['', '## 三、材料里高频的中文名词（挑出"我其实没做过"的）', '',
          '| 词 | 材料出现 | 我确认（会/没做过） |', '|---|---|---|']
    for w, n in cj.most_common(60):
        if w not in kn:
            L.append('| %s | %d |  |' % (w, n))

    open(OUT, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('已生成 %s' % OUT)
    print('  材料里出现过、待确认: %d 个' % len(rows_in))
    print('  材料里没有、待确认:   %d 个（疑似写坏 %d 个）'
          % (len(rows_out), sum(1 for r in rows_out if r[2].startswith('疑似'))))
    print('  中文高频名词:         %d 个' % len(cj))
    print()
    print('前 20 个材料里出现过、需要我确认的:')
    print('  ' + ', '.join('%s(%d)' % (w, n) for w, n, _ in rows_in[:20]))


if __name__ == '__main__':
    main()
