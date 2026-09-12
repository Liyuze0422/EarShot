# -*- coding: utf-8 -*-
"""私有词表：config/ 里的真词要能盖过 knowledge.py 里的内置示例。

背景：最早的写法把语料路径和项目词表硬编码在 server/knowledge.py 里，
公开仓库那份就只能放示例占位词 —— 于是私人副本和仓库版成了两个真相。
现在真词放 config/（.gitignore 挡着），算法留在仓库版。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import knowledge   # noqa: E402
import settings    # noqa: E402


@pytest.fixture
def restore():
    """_apply_private_terms 会改模块级全局，跑完必须还原。"""
    tm, dw = knowledge.TOPIC_TERMS, knowledge.DOMAIN_WORDS
    yield
    knowledge.TOPIC_TERMS, knowledge.DOMAIN_WORDS = tm, dw


def test_domain_words_txt_overrides_builtin(tmp_path, monkeypatch, restore):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'domain_words.txt').write_text(
        '# 注释行要跳过\n测试词甲\n\n测试词乙\n', encoding='utf-8')
    monkeypatch.setattr(settings, 'REPO_ROOT', str(tmp_path))
    _, words = knowledge._apply_private_terms()
    assert words == ['测试词甲', '测试词乙']


def test_topic_terms_json_overrides_builtin(tmp_path, monkeypatch, restore):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'topic_terms.json').write_text(
        json.dumps({'甲项目': ['甲乙', '甲丙']}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(settings, 'REPO_ROOT', str(tmp_path))
    terms, _ = knowledge._apply_private_terms()
    assert terms == {'甲项目': ['甲乙', '甲丙']}


def test_missing_config_keeps_builtin(tmp_path, monkeypatch, restore):
    """没有 config 文件时不能把内置示例清空 —— 那会让仓库版一上来就检索不到东西。"""
    monkeypatch.setattr(settings, 'REPO_ROOT', str(tmp_path))
    before = dict(knowledge.TOPIC_TERMS)
    terms, words = knowledge._apply_private_terms()
    assert terms == before and len(words) > 0


def test_broken_json_does_not_crash(tmp_path, monkeypatch, restore, capsys):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'topic_terms.json').write_text('{不是合法 json', encoding='utf-8')
    monkeypatch.setattr(settings, 'REPO_ROOT', str(tmp_path))
    terms, _ = knowledge._apply_private_terms()   # 不能抛
    assert terms


def test_config_files_are_gitignored():
    """这三份包含你的真实项目名，绝不能提交。"""
    gi = open(os.path.join(ROOT, '.gitignore'), encoding='utf-8').read()
    for f in ('config/topic_terms.json', 'config/domain_words.txt', 'config/settings.json'):
        assert f in gi, '%s 必须在 .gitignore 里' % f
