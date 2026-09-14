# -*- coding: utf-8 -*-
"""合并窗口与半句判定。

背景（2026-09-13）：用户反馈"面试官一停顿再说话，显示的答案就变了"。
根因是 VAD 判停（默认 600ms）后**立刻**发答案，第二段来了又发一次并把上一个顶掉。

回归门禁喂的是文本不是音频，覆盖不到这条链路，所以这里单独测。
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import main   # noqa: E402

SR = 16000


def _audio(sec):
    """一段假音频。不用真波形 —— 合并逻辑只关心长度和拼接顺序。"""
    return np.full(int(SR * sec), 0.5, dtype='float32')


# ── MergeGate ──────────────────────────────────────────────────────────────
def test_single_segment_flushes_after_window():
    g = main.MergeGate(merge_s=0.6)
    assert g.feed('end', _audio(1.0), now=10.0) is None        # 刚判停，还要等
    assert g.feed('idle', None, now=10.3) is None              # 窗口内
    out = g.feed('idle', None, now=10.7)                       # 窗口过了
    assert out is not None and abs(len(out) / SR - 1.0) < 0.01


def test_pause_then_continue_is_merged_into_one():
    """核心场景：面试官说半句、停一下、接着说 —— 必须合成一句发一次。"""
    g = main.MergeGate(merge_s=0.6)
    assert g.feed('end', _audio(2.0), now=100.0) is None
    assert g.feed('speech', None, now=100.3) is None           # 又出声了 → 取消待发
    assert g.feed('speech', None, now=100.9) is None
    assert g.feed('end', _audio(1.5), now=101.5) is None       # 第二段说完，重新计时
    assert g.feed('idle', None, now=101.9) is None
    out = g.feed('idle', None, now=102.2)
    assert out is not None
    # 2.0 + 0.2 静音垫 + 1.5 = 3.7 秒
    assert abs(len(out) / SR - 3.7) < 0.02
    assert g.n_merged == 1


def test_without_speech_cancel_it_would_flush_early():
    """反证：如果不取消待发，第二段就变成独立的一次 —— 这正是旧行为。"""
    g = main.MergeGate(merge_s=0.6)
    g.feed('end', _audio(2.0), now=100.0)
    out = g.feed('idle', None, now=100.7)      # 没有 speech 事件
    assert out is not None and abs(len(out) / SR - 2.0) < 0.01


def test_merge_disabled_is_immediate():
    """vad_merge_ms=0 → 回到旧行为，判停即发。"""
    g = main.MergeGate(merge_s=0)
    out = g.feed('end', _audio(1.0), now=5.0)
    assert out is not None and abs(len(out) / SR - 1.0) < 0.01


def test_long_audio_does_not_wait_for_deadline():
    """一句话攒到上限就别再等了，否则用户干等。"""
    g = main.MergeGate(merge_s=5.0, max_s=3.0)
    assert g.feed('end', _audio(4.0), now=0.0) is not None


def test_reset_drops_pending():
    g = main.MergeGate(merge_s=0.6)
    g.feed('end', _audio(1.0), now=0.0)
    g.reset()
    assert g.pending is None
    assert g.feed('idle', None, now=99.0) is None


# ── 半句判定 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize('t, half', [
    ('那你这个项目', True),            # 以"目"收尾，但短且无疑问标志
    ('我用了三个月', True),
    ('嗯', True),
    ('', True),
    ('那你这个项目里边那个', True),      # 以"个"收尾
    ('这个项目你是怎么做的？', False),   # 疑问句 → 说完了
    ('介绍一下你们的团队分工', False),    # 40 字以内但有"介绍一下"
    ('我们团队一共五个人我负责检索模块', False),   # ≥12 字 → 当说完
])
def test_is_half_sentence(t, half):
    assert main._is_half_sentence(t) is half
