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
