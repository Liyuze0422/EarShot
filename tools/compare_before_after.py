# -*- coding: utf-8 -*-
"""前后对比：核心句元描述率 / 长度 / 延迟。"""
import sys
import os
import json
import re
import statistics
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = re.compile(r'^我(先)?(判断|觉得|理解|猜|认为)|面试官(想|要)问|他问的是|对方想(听|问)|考的是我|我得先|先(分|搞)清')
def load(f):
    return json.load(open(os.path.join(ROOT, 'tests', f), encoding='utf-8'))
def stat(rows, tag):
    q = [r for r in rows if r['qtype'] != 'reverse']
    meta = [r for r in q if META.search(r['core'])]
    short = [r for r in q if 0 < len(r['core']) <= 40]
    f = [r['firstMs'] for r in rows]; t = [r['totalMs'] for r in rows]
    print('%-14s 元描述核心 %2d/%d=%3.0f%%   核心<=40字 %2d/%d=%3.0f%%   首字中位 %4dms  总中位 %4dms'
          % (tag, len(meta), len(q), len(meta)*100.0/len(q), len(short), len(q), len(short)*100.0/len(q),
             statistics.median(f), statistics.median(t)))
    return meta
print('=' * 92)
b = load('e2e_replay_before.json'); a = load('e2e_replay_result.json')
stat(b, '改提示词前'); stat(a, '改提示词后')
print('=' * 92)
mb = {r['i']: r for r in b}; 
print()
print('--- 同一题前后对照（抽 6 条）---')
for i in (4, 8, 16, 20, 24, 34):
    rb = [r for r in b if r['i'] == i][0]; ra = [r for r in a if r['i'] == i][0]
    print('#%s %s' % (i, rb['q'][:40]))
    print('   前: %s' % rb['core'][:64])
    print('   后: %s' % ra['core'][:64])
json.dump({'meta_before': len([r for r in b if r['qtype']!='reverse' and META.search(r['core'])]),
           'meta_after': len([r for r in a if r['qtype']!='reverse' and META.search(r['core'])])},
          open(os.path.join(ROOT, 'tests', 'prompt_fix_compare.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
