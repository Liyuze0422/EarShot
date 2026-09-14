# -*- coding: utf-8 -*-
"""把「知识库整理规则 + 你的简历 + 目标 JD」拼成一段可以直接发给 AI 的提示词。

为什么要这么做：同一份简历、同一个岗位，不同人整理出来的知识库命中率能差一倍。
差别不在文笔，而在**结构是否符合检索机制**（文件名占 4 倍权重、块要 60~700 字、
每个块要自带项目名……）。这些规则人容易忘，写进提示词让 AI 照着做最省事。

跑：
    python tools/kb_prompt.py --resume 简历.md --jd 岗位JD.md
    python tools/kb_prompt.py --resume 简历.md --jd JD.md --out 提示词.txt
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import knowledge   # noqa: E402

_RULES = r'''你是我的面试知识库整理助手。请按下面的规范，把我提供的简历和岗位 JD
整理成可以直接放进检索系统的材料。

# 背景：为什么规范这么细

这套材料会被一个本地检索系统（词级 BM25 + 文件名加权 + 话题加权）索引，
面试时实时匹配面试官的问题。它对材料**结构**极其敏感，实测数据：

- 文件名在检索词袋里会重复 4 次 —— 权重远高于正文任何词
- 按 ## 标题切块，每块 60~700 字；超过 700 会被**硬切**，可能从句子中间断
- 掺入 25% 「讲同一批话题」的材料，命中率从 89.7% 掉到 **41.0%**

所以下面每条规则都不是风格偏好，是要么命中要么不命中的硬约束。

# 硬性要求

## 1. 文件命名
格式：<项目名>_<主题>_<类型>.md
- 必须包含项目名，且这个项目名要和话题词表里的键**完全一致**
- 必须包含能区分这个项目的主题词
- 反例：面试材料1.md　　正例：项目3_LoRA微调_深度分析.md

## 2. 正文结构
- 每个 ## 小节控制在 60~700 字。超了就再开一个 ##
- **每块必须自带主语**（项目名），因为它会被单独检索出来
  - 反例：## 技术选型 / 用了 Milvus，因为需要混合检索。
  - 正例：## 项目3_LoRA微调 · 技术选型 / 这个项目里向量库选了 Milvus……

## 3. 每个项目必须覆盖这七件事
背景（这是什么项目）/ 我的角色（你负责哪块，用"我"不用"我们"）/
选型与理由（为什么用 X 不用 Y，含备选和淘汰原因）/ 关键数字（带具体数字）/
踩过的坑（具体故障 + 怎么定位 + 怎么修）/ 可追问点（第二三层答案）/
不会的（"这块我没深入"的体面说法）

## 4. 绝对不要编造
- **只能使用我提供的信息。** 缺什么就问，不要自己填。
- 我提供的简历里没有的数字、技术、经历，一律标注 【待补充：……】
- 宁可留空让我填，也不要写一个我答不上来的细节 —— 面试追问三层就露馅，
  这比"材料少"危害大得多。

## 5. 最后额外产出两份配置
（这两份决定检索能不能命中，很重要）

### A. 话题词表（JSON）
键 = 文件命名里的项目片段；值 = 面试官口语里会说的词
示例：{ "<项目片段>": ["<面试官可能说的词>"] }

### B. 领域词表（一行一个词）
会被分词器切碎的专业词：产品名、技术栈缩写、内部黑话。
比如"工作流"会被切成"工作/流"，需要钉住。

# 输出格式

按文件分别输出，每个文件用这样的分隔：

===== FILE: 项目X_主题_深度分析.md =====
（文件全文）

全部文件输出完，再输出两份配置（话题词表 / 领域词表）。
'''

_BRIEF = '''
# 我的情况

## 简历
{r}

'''

_JD = '''
## 目标岗位 JD
{j}

'''

_TAIL = '''
---
请开始整理。记住：**只能用我给你的信息，缺的标【待补充】，不要编。**
'''


def _read(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser(description='生成知识库整理提示词')
    ap.add_argument('--resume', required=True, help='简历文件（.md/.txt）')
    ap.add_argument('--jd', default='', help='岗位 JD 文件（可选）')
    ap.add_argument('--out', default='', help='写到文件（默认打印到屏幕）')
    ap.add_argument('--extra', default='',
                    help='额外说明，比如"重点准备项目3，面试官是搜索团队"')
    args = ap.parse_args()

    if not os.path.exists(args.resume):
        print('找不到简历文件: %s' % args.resume)
        return 2

    parts = [_RULES, _BRIEF.format(r=_read(args.resume).strip())]
    if args.jd:
        if not os.path.exists(args.jd):
            print('找不到 JD 文件: %s' % args.jd)
            return 2
        parts.append(_JD.format(j=_read(args.jd).strip()))

    # 把当前已有的配置带上，让 AI 沿用而不是另起一套
    try:
        tk = json.dumps(knowledge.TOPIC_TERMS, ensure_ascii=False, indent=2)
        parts.append('\n# 我当前已有的话题词表（请沿用这些键，需要时补充）\n%s\n' % tk)
    except Exception:
        pass
    try:
        dw = os.path.join(ROOT, 'config', 'domain_words.txt')
        if os.path.exists(dw):
            words = [w.strip() for w in _read(dw).splitlines()
                     if w.strip() and not w.strip().startswith('#')]
            if words:
                parts.append('\n# 我当前的领域词表（%d 个，可继续补充）\n%s\n'
                             % (len(words), ' '.join(words[:200])))
    except Exception:
        pass

    if args.extra:
        parts.append('\n# 额外要求\n%s\n' % args.extra)

    parts.append(_TAIL)
    text = ''.join(parts)

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('已写出: %s（%d 字）' % (args.out, len(text)))
        print('把它整段发给 AI 即可。产出后请跑 python tools/kb_audit.py 自检。')
    else:
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
