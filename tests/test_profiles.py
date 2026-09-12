# -*- coding: utf-8 -*-
"""资料包（corpus profile）：目录约定、切换、缓存失效、_ 前缀排除。

背景：实测往知识库里掺"讲同一批话题"的文本会直接抢排名（掺 25% 本人面试录音
hit@1 89.7%→41.0%），所以要能把材料按面试切开。这个测试守住切割的边界。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import knowledge   # noqa: E402
import settings    # noqa: E402


def _doc(text, n=30):
    return ('# 标题\n' + text * n)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """假的 knowledge/：_base + 两个包 + 一个平铺文件 + 一个 _ 前缀文件。"""
    root = tmp_path / 'knowledge'
    (root / '_base').mkdir(parents=True)
    (root / '甲公司').mkdir()
    (root / '乙公司').mkdir()
    (root / '_base' / '通用材料.md').write_text(_doc('通用'), encoding='utf-8')
    (root / '_base' / '_笔记.md').write_text(_doc('笔记'), encoding='utf-8')
    (root / '甲公司' / 'JD.md').write_text(_doc('甲'), encoding='utf-8')
    (root / '乙公司' / 'JD.md').write_text(_doc('乙'), encoding='utf-8')
    (root / '平铺材料.md').write_text(_doc('平铺'), encoding='utf-8')

    monkeypatch.setattr(settings, 'knowledge_root', lambda: str(root))
    monkeypatch.setattr(settings, 'corpus_globs', lambda: [str(root / '**' / '*.md')])
    monkeypatch.setattr(settings, 'corpus_profile', lambda: '')

    knowledge.set_profile(None)      # 先撤销运行时覆盖
    knowledge._cache.clear()
    yield root
    knowledge.set_profile(None)
    knowledge._cache.clear()


def names():
    return sorted(n for n, _ in knowledge.load_docs())


def test_list_profiles_ignores_base_and_files(tree):
    assert set(knowledge.list_profiles()) == {'甲公司', '乙公司'}


def test_default_is_flat_and_unchanged(tree):
    """不设包 = 老行为：扫整个 knowledge/，平铺文件也要在。"""
    assert knowledge.active_profile() == ''
    assert names() == ['JD.md', 'JD.md', '平铺材料.md', '通用材料.md']


def test_profile_scopes_corpus(tree):
    knowledge.set_profile('甲公司')
    assert knowledge.active_profile() == '甲公司'
    # 只有 _base + 甲公司；乙公司和平铺都不在
    assert len(names()) == 2
    text = '\n'.join(t for _, t in knowledge.load_docs())
    assert '甲' * 5 in text
    assert '乙' * 5 not in text
    assert '平铺' * 5 not in text


def test_underscore_files_are_skipped(tree):
    """文件名以 _ 开头的不参与检索（留个放笔记的口子）。"""
    knowledge.set_profile('')
    assert '_笔记.md' not in names()


def test_switching_invalidates_index_cache(tree):
    knowledge.set_profile('甲公司')
    idx_a, chunks_a = knowledge.build()
    n_a = len(chunks_a)
    knowledge.set_profile('乙公司')
    idx_b, chunks_b = knowledge.build()
    assert len(chunks_b) == n_a, '两个包材料数一样，但必须是重新建的索引'
    assert idx_a is not idx_b, '切包后索引对象应当被重建'


def test_set_profile_none_returns_to_settings(tree):
    knowledge.set_profile('甲公司')
    assert knowledge.active_profile() == '甲公司'
    knowledge.set_profile(None)
    assert knowledge.active_profile() == '', 'None = 撤销运行时覆盖，回到配置值'


def test_missing_profile_still_serves_base(tree):
    """包名写错/目录还没建时：_base 照样能用，但只加载 _base（不静默串到别的包）。"""
    knowledge.set_profile('不存在的公司')
    assert names() == ['通用材料.md'], '_base 照常生效，其他包不会被串进来'
