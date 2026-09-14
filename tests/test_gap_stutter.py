# -*- coding: utf-8 -*-
"""ASR 结巴造出的假名词，不许被判成"我没接触过"。

背景（2026-09-14 的真实误判，用户反馈"漏字"）：
面试官说"自然语言调度，这个调度具体是怎么实现的"，转写成了
"…讲一下调度具具体指的是一层"。pseg 把"调度具具"切成 调度(n) + 具具(v)，
而"具具"是 jieba 不认识的叠字 —— 于是 unknown_cjk 把"调度具具"当成了
面试官嘴里的生造技术名词，判 gap，工具回答：

    【坦诚】调度具这个词我确实没接触过，不装懂，我材料里也没有对应的东西。

**而检索其实命中了正确材料 5 条。** 用户看到的就是"没识别到、直接不给答案"。

试过并否掉的方案：加"检索覆盖率够高就不判 gap"的守卫。覆盖率算的是
"问题的内容词在材料里出现多少"，真正该报 gap 的题覆盖率本来就低，
这次误判的题覆盖率只有 0.25 —— 该报的被压掉、该拦的没拦住，
test_gap_fp 从 10/10 掉到 6/10。正确修法是在 unknown_cjk 里过滤假名词。
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

answer = importlib.import_module('answer')

GARBLED = ('嗯，描述就用了自然语言调度这个说法，那我麻烦你讲一下调度具具体指的是一层？，'
           '调度调度的具体是哪一层是吗？对嗯嗯呃，我的意思是说是指是这样任务优先级的一些排序资源分配路径规划')


@pytest.fixture(autouse=True)
def _no_known_terms(monkeypatch):
    """隔离私有词表，让结果只取决于叠字规则。"""
    monkeypatch.setattr(answer, 'known_terms', lambda: set())
    monkeypatch.setattr(answer, '_vocab', lambda: set())


def test_stutter_token_is_not_a_gap_term():
    """核心回归：这条真实转写不许再产出"调度具具"。"""
    got = answer.unknown_cjk(GARBLED)
    assert '调度具具' not in got


def test_real_terms_still_detected():
    """反向：真生造词不能被这条规则误杀。"""
    got = answer.unknown_cjk('你了解湖仓一体吗')
    assert '湖仓一体' in got


def test_cjk_repeat_regex():
    assert answer._CJK_REPEAT.search('具具')
    assert answer._CJK_REPEAT.search('调度具具')
    assert not answer._CJK_REPEAT.search('湖仓')
    assert not answer._CJK_REPEAT.search('具身智能')
    assert not answer._CJK_REPEAT.search('存算分离')
