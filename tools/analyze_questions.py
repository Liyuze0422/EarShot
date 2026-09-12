# -*- coding: utf-8 -*-
r"""从面试记录里提取面试官的提问，归纳题型。

用法：
    python tools/analyze_questions.py [面试记录.md]

不给参数时读 knowledge/面试记录.md。原文按"发言人1 / 发言人2"开头的行分段
（转写工具常见的格式），第 2 个发言人被当作面试官。
"""
import sys
import os
import re
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
from router import classify, LABEL     # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'knowledge', '面试记录.md')
if not os.path.exists(src):
    print('找不到面试记录: %s' % src)
    print('用法: python tools/analyze_questions.py <面试记录.md>（默认 knowledge/面试记录.md）')
    sys.exit(1)
print('输入:', src)
lines = open(src, encoding='utf-8').read().split('\n')
cur = None
said = []
for l in lines:
    m = re.match(r'^发言人(\d)\s', l.strip())
    if m:
        cur = m.group(1)
        continue
    if cur == '2' and l.strip():
        said.append(l.strip())

print('面试官发言段数:', len(said))
print()

# 疑问句筛选
QHINT = re.compile(r'[?？]|吗|呢|什么|为什么|怎么|如何|多少|哪|有没有|是不是|能不能|会不会|介绍|说说|讲讲|聊|评价|举例|判断|觉得')
qs = [s for s in said if QHINT.search(s)]
print('疑似提问:', len(qs))
print('=' * 72)
types = {}
for i, q in enumerate(qs, 1):
    t = classify(q)
    types.setdefault(t, []).append(q)
    print('%2d [%s] %s' % (i, LABEL[t], q[:96]))
print('=' * 72)
print()
print('=== 题型分布 ===')
for t, arr in sorted(types.items(), key=lambda x: -len(x[1])):
    print('  %-12s %2d 条  (%.0f%%)' % (LABEL[t], len(arr), len(arr) * 100.0 / len(qs)))
