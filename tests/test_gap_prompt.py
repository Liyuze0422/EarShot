# -*- coding: utf-8 -*-
"""缺口题（gap）不许答成"我不知道"。

用户反馈（2026-09-14 实机演练后）："每次面试官一问道多模态相关的知识问题，
就回复了不知道不清楚，我感觉这样也是不对的。"

旧 prompt 第一段就写着"直接承认没接触过…例：这块我确实没有实际用过，不装懂" ——
开口就是认输。面试官问技术，想看的是知识面和判断力，不是听人认输。

现在的四段：没落到项目里（一句带过，不许停在自我否定上）→ 我了解的原理 →
我现在怎么做、为什么这么选 → 什么条件下该把它加进来 → 反问对齐术语。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import answer  # noqa: E402


def test_gap_prompt_does_not_surrender():
    s = answer.S_GAP
    for mark in ('【实话】', '【对比】', '【什么时候用】', '【反问】'):
        assert mark in s, mark
    assert '【坦诚】' not in s, '旧标记名已经弃用'
    assert '这块我确实没有实际用过，不装懂。' not in s, '旧的认输示例句还在'


def test_gap_prompt_bans_surrender_words():
    """明令禁止"我不懂/我不会/不清楚"这类话 —— 用户实测最刺眼的就是这个。"""
    s = answer.S_GAP
    assert '不许写"我不懂"' in s
    assert '没落到项目里' in s
    # 也不许拿"没做过平行方案"这种空话把【对比】糊过去
    assert '绝对不许写"我没做过平行方案"' in s


def test_gap_anchor_query_is_defined():
    """gap 题会额外检索一次，把"缺口题怎么答 / 技术选型"的材料固定召回来。"""
    assert answer.GAP_ANCHOR_Q.strip()
