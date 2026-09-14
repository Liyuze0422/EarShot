# -*- coding: utf-8 -*-
"""话题栈（跨轮指代）。

背景（用户反馈）："面试官用了一个具体名词之后，接着在这个名词的基础上追问，
但不会重复那个词，导致匹配不上。"

0.9.2 已经做了"追问继承上一题材料"，但只记**一个**锚点。话题栈保留最近几个
在聊的东西，由近及远分别融合、越远权重越低，超过 TTL 轮没再命中就忘掉。

⚠️ 诚实边界：在 tests/real_questions.json 那份基准上做了 A/B ——
单锚点 vs 全栈，**44/44 逐题 top1 完全相同**。因为真实面试官会连着十几轮聊
同一个项目，多出来的锚点基本指向同一个文件。这个栈只在"聊完 A、插两句别的、
再回 A"时才有用，而那份基准是连续问答，没有这种回绕。
所以这里只测**机制**，端到端收益未经验证（也未见伤害）。
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

answer = importlib.import_module('answer')


@pytest.fixture(autouse=True)
def _clean():
    answer.reset_topic_stack()
    yield
    answer.reset_topic_stack()


def test_push_and_read_back():
    answer._stack_push('项目A.md', '你在项目里用了什么检索方案')
    got = answer._stack_anchors(['你在项目里用了什么检索方案'])
    assert got == [('项目A.md', 1.0)]


def test_recent_topic_has_higher_weight_than_older():
    answer._stack_push('项目A.md', '第一题')
    answer._stack_push('项目B.md', '第二题')
    got = answer._stack_anchors(['第一题', '第二题'])
    assert got[0][0] == '项目B.md'          # 最近的排前面
    assert got[0][1] > got[1][1]            # 权重衰减
    assert got[1][0] == '项目A.md'


def test_same_topic_repeated_does_not_grow_stack():
    for i in range(5):
        answer._stack_push('项目A.md', '第%d题' % i)
    assert len(answer._TOPIC_STACK) == 1


def test_stack_is_capped():
    for i in range(10):
        answer._stack_push('项目%d.md' % i, '第%d题' % i)
    assert len(answer._TOPIC_STACK) == answer.TOPIC_STACK_MAX


def test_stale_topic_is_forgotten():
    answer._stack_push('项目A.md', '第一题')
    for i in range(answer.TOPIC_STACK_TTL + 2):
        answer._stack_push('项目B.md' if i % 2 else None, '后来%d' % i)
    got = [s for s, _ in answer._stack_anchors(['第一题', '后来1'])]
    assert '项目A.md' not in got


def test_topic_not_in_recent_history_is_skipped():
    """乱序/重放/用户往回翻时，栈里可能留着不相干的旧话题。"""
    answer._stack_push('项目A.md', '很久以前的题')
    answer._stack_push('项目B.md', '当前的题')
    got = [s for s, _ in answer._stack_anchors(['当前的题'])]
    assert got == ['项目B.md']


def test_hr_question_does_not_propagate():
    """HR 题不向后传递 —— 否则会把后面的技术题带进 HR 材料。"""
    answer._stack_push('HR初面准备.md', '你期望的薪资是多少')
    answer._stack_push('项目A.md', '项目里用了什么')
    got = [s for s, _ in answer._stack_anchors(['你期望的薪资是多少', '项目里用了什么'])]
    assert 'HR初面准备.md' not in got


def test_empty_src_is_ignored():
    answer._stack_push(None, '没检索到东西的题')
    assert answer._TOPIC_STACK == []


def test_reset_clears_everything():
    answer._stack_push('项目A.md', '第一题')
    answer.reset_topic_stack()
    assert answer._TOPIC_STACK == []
    assert answer._TURN[0] == 0


def test_merge_many_is_single_pass():
    """多路必须一次融合。串行融合会让靠后的路被重复计入。"""
    a = [(1.0, 's1', '文本1')]
    b = [(1.0, 's2', '文本2')]
    c = [(1.0, 's3', '文本3')]
    out = answer._rrf_merge(a, [(b, 1.0), (c, 1.0)])
    assert len(out) == 3
    # 三路平权，主路 rank0 应该仍是第一
    assert out[0][2] == '文本1'
