# -*- coding: utf-8 -*-
"""知识库体检：把"会让检索掉分"的结构问题找出来。

为什么需要它：retrieval 是词级 BM25 + 文件名加权 + 话题加权，对材料**结构**极敏感。
实测掺 25% 同题材材料会把 hit@1 从 89.7% 砸到 41.0%，而这份报告就是为了
在面试前把这类问题找出来。

跑：python tools/kb_audit.py [--all] [--profile 名字]
"""
import argparse
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import knowledge   # noqa: E402

CHUNK_MIN = 60     # 低于这个字数会被 chunk() 丢弃（硬编码在 knowledge.chunk 里）
CHUNK_MAX = knowledge.CHUNK_MAX


def _toks(t):
    return {w for w in knowledge.tok(t, drop_stop=True) if len(w) >= 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default=None, help='指定资料包（默认用当前配置）')
    ap.add_argument('--all', action='store_true', help='连公司包一起看')
    args = ap.parse_args()

    if args.profile is not None:
        knowledge.set_profile(args.profile)

    idx, chunks = knowledge.build()
    print('=' * 74)
    print('知识库体检 · 资料包 = %s' % (knowledge.active_profile() or '(未启用分包，扫整个 knowledge/)'))
    print('=' * 74)
    if not chunks:
        print('  语料是空的。检查 config/settings.json 的 corpus_globs，或 knowledge/ 下有没有 .md')
        return 0

    names = defaultdict(list)
    for src, txt in chunks:
        names[src].append(txt)
    print('  文件 %d 个，切块 %d 块' % (len(names), len(chunks)))

    issues = 0

    # 1) 块大小分布 —— >700 会被硬切，<60 会被丢掉
    sizes = sorted(len(t) for _, t in chunks)
    big = [1 for _, t in chunks if len(t) > CHUNK_MAX]
    small = [1 for _, t in chunks if len(t) < CHUNK_MIN]
    print()
    print('【1】块大小（切块规则：按 ## 标题切，60~700 字）')
    print('      中位 %d 字，最长 %d 字' % (sizes[len(sizes) // 2], sizes[-1]))
    print('      超长块（>%d，会被硬切 → 语义破碎）: %d 块' % (CHUNK_MAX, len(big)))
    print('      过短块（<%d，会被丢弃）: %d 块' % (CHUNK_MIN, len(small)))
    issues += len(big) + len(small)

    # 2) 文件名有没有带上话题词 —— 文件名在词袋里占 4 倍权重
    topic_keys = list(knowledge.TOPIC_TERMS.keys())
    generic = []
    for src in names:
        if topic_keys and not any(k in src for k in topic_keys):
            generic.append(src)
    print()
    print('【2】文件名（在检索词袋里重复 %d 次，权重最高）' % knowledge.TITLE_WEIGHT)
    if not topic_keys:
        print('      config/topic_terms.json 是空的 → 话题加权没生效（实测值 3 倍权重）')
        print('      建议至少给每个项目配一组：键=文件名里的片段，值=面试官口语里的词')
    elif generic:
        print('      文件名里不含任何话题词的 %d 个（话题加权对它们永远不生效）:' % len(generic))
        for s in generic[:12]:
            print('        · %s' % s)
        issues += len(generic)
    else:
        print('      全部文件名都含话题词 ✓')

    # 3) 同题材重复 —— 这是 89.7%→41.0% 那个坑的直接机制
    print()
    print('【3】同题材竞争（最大的杀手：两个块讲同一件事会互相抢排名）')
    sets = [(src, _toks(t)) for src, t in chunks]
    dup = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            a, b = sets[i][1], sets[j][1]
            if len(a) < 5 or len(b) < 5:
                continue
            inter = len(a & b)
            jac = inter / len(a | b)
            if jac >= 0.45:
                dup.append((jac, sets[i][0], sets[j][0]))
    dup.sort(reverse=True)
    if dup:
        print('      高度重合的块对 %d 组（前 8）:' % len(dup))
        for jac, a, b in dup[:8]:
            tag = ' ←同一文件内' if a == b else ''
            print('        %.0f%%  %s  ×  %s%s' % (jac * 100, a[:30], b[:30], tag))
        print('      同一文件内的高度重合只是小节冗余；**不同文件之间的才是抢排名**。')
        issues += sum(1 for j, a, b in dup if a != b)
    else:
        print('      没有发现高度重合的块 ✓')

    # 4) 块里有没有项目归属
    print()
    print('【4】块的归属（单独取出时，模型要知道在说哪个项目）')
    # 只查"项目类"材料：HR/话术/手册本来就不属于任何项目，报出来是噪音。
    _NON_PROJECT = ('hr', '面试', '话术', '手册', '教程', '准备', '卡片', '反问', '薪资', '简历')
    proj_files = {s for s in names
                  if any(k in s for k in topic_keys)
                  or ('项目' in s and not any(w in s.lower() for w in _NON_PROJECT))}
    if topic_keys and proj_files:
        orphan = [src for src, t in chunks
                  if src in proj_files and not any(k in t or k in src for k in topic_keys)]
        if orphan:
            print('      项目类材料里，无法判断归属的块 %d 个（前 6）:' % len(orphan))
            for s in sorted(set(orphan))[:6]:
                print('        · %s' % s)
            issues += len(orphan)
        else:
            print('      项目类材料每块都能判断归属 ✓')
        print('      （HR/话术/手册类不计入 —— 它们本来就不属于某个项目）')
    else:
        print('      （没配话题词，跳过）')

    print()
    print('=' * 74)
    print('  合计待改进项: %d' % issues)
    if issues == 0:
        print('  结构上没发现会让检索掉分的问题。')
    print('  详细规范见 docs/知识库整理清单.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
