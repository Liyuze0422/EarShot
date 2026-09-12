# -*- coding: utf-8 -*-
"""快答线冒烟测试：只在 examples/knowledge 的示例语料上验证切块与 BM25 检索。

不需要 ASR 模型、不需要音频设备、不需要网络与 API Key。
"""
import os
import sys

import pytest

pytest.importorskip('jieba', reason='检索依赖 jieba，未安装时跳过')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
os.environ['TP_CORPUS_GLOBS'] = os.path.join(ROOT, 'examples', 'knowledge', '*.md')

import knowledge  # noqa: E402


def test_build_and_search_example_corpus():
    index, chunks = knowledge.build()
    assert chunks, '示例语料没有切出任何块：examples/knowledge/ 下的文件还在吗？'
    hits = index.search('推荐系统的双塔召回是怎么做的', topk=3)
    assert hits, '在示例语料上检索不到任何结果'
    joined = ' '.join(str(h) for h in hits)
    assert '推荐' in joined or '双塔' in joined or '召回' in joined, \
        '检索命中的内容与问题无关: ' + joined[:200]


def test_chunk_returns_src_and_body_pairs():
    long_text = '## 小标题\n' + '这是一段用来验证切块逻辑的中文文本，长度需要超过单个块的上限。' * 60
    parts = knowledge.chunk(long_text, 'unit-test.md')
    assert len(parts) > 1, '超过块上限的长文本没有被切成多块'
    for src, body in parts:
        assert src == 'unit-test.md'
        assert body.strip(), '切出了空块'
        assert len(body) <= knowledge.CHUNK_MAX, '切出的块超过了 CHUNK_MAX'


def test_tok_drops_stopwords_and_keeps_terms():
    toks = knowledge.tok('这是他的项目，主要做了双塔召回')
    assert '的' not in toks, '停用词没有被过滤掉'
    assert any('召回' in t for t in toks), '实词被过滤掉了'