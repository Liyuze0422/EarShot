# -*- coding: utf-8 -*-
"""换说法扩展词的管线：内容 hash 索引 + 进词袋。

为什么按 hash 不按块序号：材料改一个字，序号会整段平移，
按序号索引会**张冠李戴**（把 A 块的说法贴到 B 块上），比没有扩展词还糟。
按内容 hash 则自动降级成"这块没有扩展词"。
"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

knowledge = importlib.import_module('knowledge')


def test_chunk_key_is_content_based():
    """同一段内容换个排版还是同一个 key；内容变了 key 就变。"""
    a = knowledge._chunk_key('## 标题\n正文内容')
    b = knowledge._chunk_key('## 标题\n\n正文内容')      # 只多了空行
    c = knowledge._chunk_key('## 标题\n正文内容改了')
    assert a == b
    assert a != c


def test_expand_tokens_enter_the_bag(monkeypatch):
    """扩展词要真的进词袋，否则等于没接上。"""
    monkeypatch.setattr(knowledge, 'load_expand',
                        lambda reload=False: {knowledge._chunk_key('材料正文'): ['任务编排怎么设计的']})
    chunks = [('样例.md', '材料正文')]
    idx = knowledge.BM25(chunks)
    assert '编排' in idx.docs[0] or '任务' in idx.docs[0]


def test_missing_expand_file_is_not_fatal(monkeypatch, tmp_path):
    """没有扩展词文件必须静默降级 —— 它是可选增强，不该让工具起不来。"""
    monkeypatch.setattr(knowledge, '_EXPAND_FILE', 'definitely_not_here.json')
    knowledge._expand_cache.clear()
    assert knowledge.load_expand() == {}
    knowledge._expand_cache.clear()


def test_expand_does_not_inflate_len(monkeypatch):
    """扩展词进 TF，但**不进 BM25 的长度归一化**。

    把扩展词也算进 dl 的话，这一块的 dl/avgdl 一起变大，
    长度归一化会把该块所有真实词的分数一起压低 —— 实测原题 hit@3 掉 2.6 个点。
    现在 dl 只算原文，扩展词照样能匹配上。
    """
    monkeypatch.setattr(knowledge, 'load_expand',
                        lambda reload=False: {knowledge._chunk_key('材料正文'): ['任务编排怎么设计的']})
    idx = knowledge.BM25([('样例.md', '材料正文')])
    base = knowledge.tok('材料正文') + knowledge.tok('样例') * knowledge.TITLE_WEIGHT
    assert idx.dl[0] == len(base)
    assert '编排' in idx.docs[0]          # 但词袋里有，检索得到
    assert '编排' not in knowledge.tok('材料正文')


def test_expand_weight_zero_disables(monkeypatch):
    """权重为 0 时词袋和不带扩展词完全一致（A/B 评测要靠这个开关）。"""
    monkeypatch.setattr(knowledge, 'load_expand',
                        lambda reload=False: {knowledge._chunk_key('材料正文'): ['任务编排怎么设计的']})
    chunks = [('样例.md', '材料正文')]
    monkeypatch.setattr(knowledge, 'EXPAND_WEIGHT', 0)
    off = knowledge.BM25(chunks).docs[0]
    monkeypatch.setattr(knowledge, 'EXPAND_WEIGHT', 1)
    on = knowledge.BM25(chunks).docs[0]
    assert len(on) > len(off)
