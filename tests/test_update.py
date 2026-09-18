# -*- coding: utf-8 -*-
"""更新机制的纯逻辑测试。

这里只测**不需要网络、不需要 PyQt** 的部分：版本比较、清单比对、记号读写。
真正替换文件那条路（批处理 + 文件系统）没法在单测里跑 —— 它要等进程退出，
由 tests 之外的手工验收覆盖（见 docs 里的更新说明）。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))

import update  # noqa: E402


# ---------------------------------------------------------------- 版本比较

@pytest.mark.parametrize('latest,cur,want', [
    ('0.9.20', '0.9.19', True),
    ('0.9.19', '0.9.19', False),
    ('0.9.18', '0.9.19', False),
    ('0.10.0', '0.9.99', True),      # 字符串比较会判反，必须按段比
    ('1.0.0', '0.99.99', True),
    ('v0.9.20', '0.9.19', True),     # tag 带 v
    ('0.9.20-beta', '0.9.19', True),  # 后缀不参与比较
    ('', '0.9.19', False),
])
def test_is_newer(latest, cur, want):
    assert update.is_newer(latest, cur) is want


def test_parse_handles_garbage():
    assert update._parse('v0.9.19') == (0, 9, 19)
    assert update._parse('1.2') == (1, 2, 0)
    assert update._parse('') == (0, 0, 0)
    assert update._parse('abc') == (0, 0, 0)


# ---------------------------------------------------------------- 清单比对

def _mf(pairs):
    return {'files': {p: [h, 1] for p, h in pairs}}


def test_diff_plan_basic():
    local = _mf([('a.py', 'h1'), ('b.py', 'h2'), ('gone.py', 'h3')])
    remote = _mf([('a.py', 'h1'), ('b.py', 'h2new'), ('new.py', 'h4')])
    changed, deleted = update.diff_plan(local, remote)
    assert changed == ['b.py', 'new.py']
    assert deleted == ['gone.py']


def test_diff_plan_same_content_is_not_changed():
    """哈希一样就不该进差分包 —— 这正是「只下几十 MB」的原因。"""
    m = _mf([('a.py', 'h1'), ('b.py', 'h2')])
    changed, deleted = update.diff_plan(m, _mf([('a.py', 'h1'), ('b.py', 'h2')]))
    assert changed == [] and deleted == []


def test_diff_plan_without_local_manifest():
    """源码模式没有清单：全部算新增，但调用方会先拒绝（install_root 为 None）。"""
    changed, deleted = update.diff_plan(None, _mf([('a.py', 'h1')]))
    assert changed == ['a.py'] and deleted == []


# ---------------------------------------------------------------- 记号

def test_pending_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(update, '_staging', lambda: str(tmp_path))
    assert update.read_pending() is None
    bat = tmp_path / 'apply.bat'
    lst = tmp_path / 'deletes.txt'
    bat.write_text('x', encoding='utf-8')
    lst.write_text('', encoding='utf-8')
    update.write_pending(str(bat), [str(tmp_path), str(tmp_path), str(lst)], '9.9.9')
    got = update.read_pending()
    assert got and got['version'] == '9.9.9'
    update.clear_pending()
    assert update.read_pending() is None


def test_pending_rejects_missing_script(tmp_path, monkeypatch):
    """批处理或参数文件被清理掉了就当没有 —— 否则启动时会去跑一个不存在的文件。"""
    monkeypatch.setattr(update, '_staging', lambda: str(tmp_path))
    update.write_pending(str(tmp_path / 'nope.bat'), [str(tmp_path)], '1.0')
    assert update.read_pending() is None


# ---------------------------------------------------------------- 安全约束

def test_source_mode_refuses_to_write_apply_script():
    """源码模式必须拒绝 —— 那时 install_root 会算成仓库的父目录。

    这条不是假设：写这版的时候测试脚本没拦住，真的把文件复制到了工作区根上。
    """
    if update.install_root() is not None:
        pytest.skip('这是打包环境，源码模式的断言不适用')
    with pytest.raises(RuntimeError):
        update.write_apply_script({'dir': 'x', 'deletes': []}, '1.0')


def test_pick_patch_prefers_diff_of_same_origin():
    r = {'current': '0.9.19', 'assets': {
        'EarShot-v0.9.20-win64-full.zip': {'url': 'F', 'size': 500},
        'EarShot-v0.9.20-patch-from-v0.9.19.zip': {'url': 'P', 'size': 30},
        'EarShot-v0.9.20-patch-from-v0.9.10.zip': {'url': 'OLD', 'size': 30},
    }}
    name, url, size, is_diff = update.pick_patch(r)
    assert url == 'P' and is_diff is True
    # 起点版本对不上的差分包不能用：那是「从别的版本跳过来」的文件，比不更新还危险
    assert url != 'OLD'


def test_pick_patch_falls_back_to_full():
    r = {'current': '0.9.19', 'assets': {'EarShot-v1.0.0-win64-full.zip': {'url': 'F', 'size': 500}}}
    name, url, size, is_diff = update.pick_patch(r)
    assert url == 'F' and is_diff is False


def test_pick_patch_none_when_no_assets():
    assert update.pick_patch({'current': '0.9.19', 'assets': {}}) is None
