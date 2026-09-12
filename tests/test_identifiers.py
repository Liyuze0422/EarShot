# -*- coding: utf-8 -*-
"""标识符压平：材料写 max_len，面试官的话转写成 maxlen，两边要对得上。

为什么值得一条测试：ASR 听不见下划线/点号。实测（_realdata/_work/bench_ident.py，
742 个"只属于一个文件"的真实标识符）：不压平时，问"maxlen 是怎么定的"这类问法
hit@1 = **0.0%**、hit@3 = 35.0%；压平后 73.3% / 85.0%。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import knowledge   # noqa: E402


def test_separator_identifiers_get_flat_form():
    t = knowledge.tok('max_len')
    assert 'maxlen' in t, '带下划线的标识符要额外产出压平形式'
    assert 'max' in t and 'len' in t, '原有分词不能被删掉（只加不删）'


def test_flat_form_covers_common_separators():
    for raw, flat in (('adapter_config.json', 'adapterconfigjson'),
                      ('tf-idf', 'tfidf'),
                      ('utf-8', 'utf8')):
        assert flat in knowledge.tok(raw), raw


def test_camel_case_untouched():
    """驼峰 jieba 本来就整块留着，不要多切出 lang/graph 这种泛词。"""
    t = knowledge.tok('LangGraph')
    assert 'langgraph' in t
    assert 'lang' not in t


def test_short_or_clean_tokens_emit_no_alias():
    assert knowledge._flat_idents('a-b') == [], '压平后不足 4 字符的不必加'
    assert knowledge._flat_idents('plainword') == [], '没有分隔符的不加'


def test_flat_query_finds_separator_material():
    """端到端：材料里写 max_len，用 maxlen 去检索也要命中原块。"""
    chunks = [
        ('项目1_训练脚本.md', '训练脚本里 max_len 设成 512，超过就截断，这一条是经验值。' * 4),
        ('项目2_检索模块.md', '检索模块用 BM25，召回路和精排路分开跑，召回数固定。' * 4),
        ('项目3_微调报告.md', '微调用 LoRA，显存不够就把 batch 降下来，梯度累积补上。' * 4),
    ]
    idx = knowledge.BM25(chunks)
    for q in ('maxlen', 'max_len'):
        hits = idx.search(q, topk=1)
        assert hits, '查询 %r 一个都没命中' % q
        assert hits[0][1] == '项目1_训练脚本.md', '查询 %r 命中了 %s' % (q, hits[0][1])
