# -*- coding: utf-8 -*-
"""ASR 术语后处理纠错。

背景（2026-09-13）：SenseVoice 对英文技术词识别很差 —— 35 个真实术语、
中文口音读出来只有 20% 原样识别。termfix 拿用户自己的术语表做保守纠错，
实测把端到端识别率从 35.7% 提到 54.3%，且假阳性为 0。

这里不依赖真实语料（那是私有的），直接注入假词表。
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

termfix = importlib.import_module('termfix')

# 假语料：高信任 = 用户手写的词表；低信任 = 语料里出现过的词
KNOWN = {'kafka', 'milvus', 'kubernetes', 'docker', 'tensorflow', 'pytorch',
         'tensorrt', 'grafana', 'mongodb', 'langgraph', 'fastapi', 'redis',
         'mysql', 'postgresql', 'nginx', 'onnx', 'faiss', 'rag'}
VOCAB = {'pydantic', 'graphpy', 'linux', 'long', 'types', 'phase', 'read',
         'please', 'readme'}


@pytest.fixture(autouse=True)
def _fake_targets(monkeypatch):
    """注入假词表，避开真实（私有）语料。"""
    index = {}
    for w in KNOWN:
        index.setdefault((w[0], len(w)), []).append((w, 2))
    for w in VOCAB - KNOWN:
        index.setdefault((w[0], len(w)), []).append((w, 1))
    for k in index:
        index[k] = sorted(set(index[k]), key=lambda x: (-x[1], x[0]))
    monkeypatch.setitem(termfix._CACHE, 'index', index)
    monkeypatch.setitem(termfix._CACHE, 'known', set(KNOWN))
    monkeypatch.setitem(termfix._CACHE, 'all', set(KNOWN) | set(VOCAB))
    # 中文目标词单独一路（按长度分桶），同样用假词表避开私有语料
    monkeypatch.setitem(termfix._CACHE, 'cn', {2: ['飞书', '字节', '豆包']})


@pytest.mark.parametrize('heard, want', [
    ('你在项目里用过 kfka 吗', '你在项目里用过 kafka 吗'),
    ('用过 mivis 和 tensor r t', '用过 milvus 和 tensorrt'),
    ('有 mongo d b 和 fast api 的经验', '有 mongodb 和 fastapi 的经验'),
    ('见过 Kubbernets 吗', '见过 kubernetes 吗'),
    ('用过 o n n x 推理', '用过 onnx 推理'),
])
def test_corrects_typical_asr_errors(heard, want):
    got, fixes = termfix.correct(heard)
    assert got == want
    assert fixes


def test_joins_do_not_swallow_intervening_chinese():
    """曾经的真 bug：把"第一个 token 起点到最后一个终点"整段替换，
    中间的"和"被一起吃掉（mongo d b 和 g... -> mongodb）。"""
    got, _ = termfix.correct('有 mongo d b 和 g r p c 的经验')
    assert '和' in got
    assert 'mongodb' in got


def test_short_tokens_are_left_alone():
    """o / v / ra 这类太短，纠了只会更错。"""
    for t in ['用过 o 和 v 吗', 'ra 是什么']:
        got, fixes = termfix.correct(t)
        assert got == t
        assert not fixes


def test_common_english_words_are_not_touched():
    """read / please / phase 这些本来就是英文词，纠了就是假阳性。"""
    for t in ['read the file please', 'the phase is fine']:
        got, fixes = termfix.correct(t)
        assert got == t


def test_low_trust_targets_need_distance_one():
    """语料词表是低信任。曾经的真假阳性：GraphqL（本来就对）被纠成 graphpy（差 2）。"""
    got, fixes = termfix.correct('你在项目里用过 GraphqL 吗')
    assert got == '你在项目里用过 GraphqL 吗'
    assert not fixes


def test_preserves_surrounding_text():
    got, _ = termfix.correct('前面的话 kfka 后面的话')
    assert got == '前面的话 kafka 后面的话'


def test_empty_and_plain_text():
    assert termfix.correct('') == ('', [])
    assert termfix.correct('这段话里没有英文') == ('这段话里没有英文', [])


def test_cap_scales_with_length():
    assert termfix._cap('read') == 1        # 短词保守：read 不该被纠成 redis
    assert termfix._cap('kubbernet') == 2
    assert termfix._cap('kubernetesx') == 3


# ── 中文错字（2026-09-20 真实面试）───────────────────────────────────────
# 那场里面试官说的是「飞书」，ASR 全听成了「飞猪」，而且错字直接进了答案文本
# （屏幕上写的是「没去动飞猪那套基座」）—— 照念出来是会尴尬的。
# 原来的 termfix 只按 _ASCII 找 token，中文这条路完全没接。


def test_chinese_typo_is_corrected():
    got, fixes = termfix.correct('用的是飞猪那套聊天软件')
    assert ('飞猪', '飞书') in fixes
    assert '飞书' in got and '飞猪' not in got


def test_chinese_typo_with_different_first_char():
    """错字连首字都错（「字节」听成「自节」）—— 所以不能按首字分桶查。"""
    got, fixes = termfix.correct('围绕自节的生态做交付')
    assert ('自节', '字节') in fixes


def test_chinese_common_words_are_never_touched():
    """判据必须是「jieba 词典里查不到」，不能是「不在 known_terms」。

    known_terms 只装术语，常用词本来就不在里面。踩过：拿它当判据时，
    同一批 127 条真实转写里有 35 条被误纠（公司→公式、简历→日历、
    客户→门户、交流→交付）—— 命中率看着漂亮，内容全毁了。
    """
    for t in ['那你过去的工作是相对实习喽', '我找一下简历', '上家公司做乙方服务',
              '最后一公里的部署', '不交社保', '一月份开始实习', '过程中的实际问题']:
        got, fixes = termfix.correct(t)
        assert not fixes, (t, fixes)
        assert got == t
