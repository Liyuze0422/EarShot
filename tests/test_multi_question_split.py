# -*- coding: utf-8 -*-
"""多问拆分（extract_q.split_questions）与组内串行派发（main.ask_group）。

背景（2026-11-21 P0）：面试官语速快、句间停顿 <1 秒时，VAD + 合并窗口会把两三个
提问粘成一次识别；整段只走一次 ask，而答案固定"思路 40 字 + 展开 150 字"，
只够答其中一问，后面的问题就被吞了（36 场真实日志里 7.1%~9.4% 的答题一次 ≥2 问）。

split_questions 是纯函数：只按句末标点切、拆不出两问就原样返回，
调用方只在 len(结果) >= 2 时才改变行为 —— 这两条边界必须钉住。

另外钉住一条容易踩的并发坑：拆出的多问**不能连着 ask**。
ask() 每题分配新 qid，而快答线线程一启动就先判 stale(qid)，连着发会让前一题的
答案线程直接判过期退出（实测两连问只出最后一条答案事件）。main.ask_group 的
做法是等前一题 answer_done 再提交下一题，这里用假 ask 验证"串行"这个约束。
"""
import os
import sys
import threading
import time

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import extract_q  # noqa: E402
import main       # noqa: E402


# ── split_questions：拆分规则 ──────────────────────────────────────────────
def test_two_independent_questions_are_split():
    """两个独立问句 → 拆成 2 题（真实日志里最常见的"一问被吞"形态）。"""
    t = '那你为什么刚毕业就要换一家公司？你做过检索相关的项目吗？'
    qs = extract_q.split_questions(t)
    assert len(qs) == 2
    assert qs[0] == '那你为什么刚毕业就要换一家公司？'
    assert qs[1] == '你做过检索相关的项目吗？'


def test_single_question_is_returned_unchanged():
    """单句 → 原样返回 1 条（绝不改变现有单题行为）。"""
    t = '你觉得这个方案怎么样？'
    assert extract_q.split_questions(t) == [t]


def test_clause_with_connector_merges_into_previous_question():
    """「然后…」开头的从句并回上一题，不另起一新题。"""
    t = '你做过检索吗？然后我们这边用的是双塔。那数据量多大？'
    qs = extract_q.split_questions(t)
    assert len(qs) == 2
    assert '然后我们这边用的是双塔' in qs[0]
    assert qs[1] == '那数据量多大？'


def test_premise_sentence_goes_with_its_question():
    """前提句（如果…）跟着它后面那一问走，不被丢掉。"""
    t = '如果数据量涨十倍。你会怎么设计？你做过类似的吗？'
    qs = extract_q.split_questions(t)
    assert len(qs) == 2
    assert qs[0] == '如果数据量涨十倍。你会怎么设计？'
    assert qs[1] == '你做过类似的吗？'


def test_more_than_max_n_merges_the_tail():
    """超过 max_n：前 max_n-1 条各自成题，其余并进最后一条（后半截不丢）。"""
    t = '第一个问题是什么？第二个问题是什么？第三个问题是什么？第四个问题是什么？'
    qs = extract_q.split_questions(t, max_n=2)
    assert len(qs) == 2
    assert qs[0] == '第一个问题是什么？'
    assert qs[1] == '第二个问题是什么？第三个问题是什么？第四个问题是什么？'


def test_no_sentence_punctuation_returns_original():
    """没标点（例如 asr_textnorm 回退成 woitn）→ 拆不出来，原样 1 条。"""
    t = '你觉得这个方案怎么样'
    assert extract_q.split_questions(t) == [t]


def test_empty_and_none_are_safe():
    assert extract_q.split_questions('') == []
    assert extract_q.split_questions(None) == []
    assert extract_q.split_questions('   ') == []


# ── split_questions：宁可少拆（2026-11-21 收紧的边界）────────────────────
def test_particle_ending_statement_is_not_a_question():
    """「…嘛。」「…呢。」这种语气词收尾的陈述不是独立提问。

    收紧前 _askish 把「嘛/呢」也算疑问形式，这两条陈述会被拆成一道题、多走一次
    ask（虽然 router 多半会 skip，但多一条误答风险）。作为内容并回前文/原样返回。
    """
    for t in [
        '但你学的这个专业并不是人工智能专业嘛。您之前做过类似的项目吗？',
        '我们这边主要做企业服务呢。您之前做过类似的项目吗？',
    ]:
        assert extract_q.split_questions(t) == [t], t


def test_self_introduction_is_not_a_question():
    """面试官开场自报家门（我先介绍一下…）是铺垫，不是独立提问。"""
    t = '我先介绍一下我们公司，我们主要做企业服务。您之前做过类似的项目吗？'
    assert extract_q.split_questions(t) == [t]


def test_strong_question_word_after_particle_still_splits():
    """语气词/开场词不能把真提问一起压掉：句内有强疑问词（介绍/多少…）仍然独立成题。"""
    t = '你介绍一下吧。那数据量多大？'
    qs = extract_q.split_questions(t)
    assert len(qs) == 2
    assert qs[0] == '你介绍一下吧。'


def test_max_n_one_disables_splitting():
    """max_n <= 1 = 不拆（原样返回）。"""
    t = '第一个问题是什么？第二个问题是什么？'
    assert extract_q.split_questions(t, max_n=1) == [t]


def test_fast_enough():
    """设计约束：纯规则、单条 <1ms（真实性能约 0.01ms）。"""
    t = '你做过检索吗？然后我们这边用的是双塔。那数据量多大？'
    t0 = time.perf_counter()
    for _ in range(300):
        extract_q.split_questions(t)
    assert (time.perf_counter() - t0) / 300 < 0.001


# ── main.ask_group：拆出的多问必须串行提交 ────────────────────────────────
def _reset_main_state():
    main.STATE['qid'] = 0
    main.STATE['ans_done'].clear()
    main.STATE['recent'].clear()


def _wait_until(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_ask_group_submits_one_at_a_time(monkeypatch):
    """前一题答案线程收尾（ans_done set）之后，才提交下一题。

    如果连着 ask，后一题的 qid 会把前一题顶掉 —— run_fast_answer 开头的
    stale(qid) 检查直接让前一题答案线程退出，用户那边还是"只答一问"。
    """
    _reset_main_state()
    calls = []

    def fake_ask(q, with_deep=False, force=False):
        with main.STATE['lock']:
            main.STATE['qid'] += 1
            qid = main.STATE['qid']
        ev = threading.Event()
        main.STATE['ans_done'][qid] = ev
        calls.append(('ask', q))

        def finish():
            time.sleep(0.02)
            calls.append(('done', q))
            ev.set()

        threading.Thread(target=finish, daemon=True).start()
        return qid

    monkeypatch.setattr(main, 'ask', fake_ask)
    main.ask_group(['问题甲是什么？', '问题乙是什么？'])
    assert _wait_until(lambda: [c[0] for c in calls].count('done') == 2)
    assert calls == [('ask', '问题甲是什么？'), ('done', '问题甲是什么？'),
                     ('ask', '问题乙是什么？'), ('done', '问题乙是什么？')]


def test_ask_group_stops_when_a_newer_question_arrives(monkeypatch):
    """组内等待期间面试官又开口（qid 前进）→ 本组剩下的作废，新题优先。"""
    _reset_main_state()
    asked, dropped = [], []

    def fake_ask(q, with_deep=False, force=False):
        with main.STATE['lock']:
            main.STATE['qid'] += 1
            qid = main.STATE['qid']
        ev = threading.Event()
        main.STATE['ans_done'][qid] = ev
        asked.append(q)
        if len(asked) == 1:                     # 模拟更晚的一句新提问把 qid 推进
            with main.STATE['lock']:
                main.STATE['qid'] += 1
            ev.set()
        return qid

    monkeypatch.setattr(main, 'ask', fake_ask)
    monkeypatch.setattr(main, 'log_rec', lambda rec: dropped.append(rec))
    main.ask_group(['问题甲是什么？', '问题乙是什么？'])
    assert _wait_until(lambda: bool(dropped))
    assert asked == ['问题甲是什么？']
    assert dropped[0]['event'] == 'question_group_dropped'
    assert dropped[0]['questions'] == ['问题乙是什么？']


def test_run_fast_answer_always_wakes_dispatcher(monkeypatch):
    """run_fast_answer 无论怎么退出都要 set ans_done —— 否则 ask_group 白等 30 秒。"""
    main.STATE['ans_done'].clear()
    ev = threading.Event()
    main.STATE['ans_done'][7] = ev
    monkeypatch.setattr(main, '_run_fast_answer', lambda q, qid: None)
    main.run_fast_answer('问题', 7)
    assert ev.is_set()
    assert 7 not in main.STATE['ans_done']


def test_ask_assigns_qid_and_returns_it(monkeypatch):
    """ask() 照旧每题 +1 qid、进 recent，并把 qid 返回给派发器。"""
    _reset_main_state()
    monkeypatch.setattr(main, 'run_fast_answer', lambda q, qid: None)
    monkeypatch.setattr(main, 'log_rec', lambda rec: None)
    qid = main.ask('你做过检索吗？')
    assert qid == 1
    assert qid in main.STATE['ans_done']
    assert main.STATE['recent'] == ['你做过检索吗？']
    assert main.ask('好的') is None       # 非提问：不占 qid
    assert main.STATE['qid'] == 1
    while not main.EVENTS.empty():        # 清掉 emit 的事件，别影响别的测试
        main.EVENTS.get()
    _reset_main_state()

# ── capture_once 接入点：asr 事件带 split 数 + 逐题提交 ─────────────────────
def test_capture_once_asks_each_split_question(monkeypatch):
    """capture_once 里拆出 2 问时：asr 事件带 split=2、落盘一条 asr 记录、
    再把两题交给派发器（ask_group）。"""
    class FakeVAD:
        def __init__(self, **kw):
            self.n = 0

        def feed(self, block):
            self.n += 1
            if self.n == 1:
                return 'end', np.ones(16000, dtype='float32')
            return 'idle', None

        def reset(self):
            pass

    class FakeCap:
        device_name = 'fake-speaker'
        device_id = 'fake-id'

        def frames(self):
            yield np.zeros(320, dtype='float32')
            raise RuntimeError('stop')      # 跑完一轮就退出采集循环

    class FakeASR:
        def transcribe(self, audio):
            return '问题一是什么？问题二是什么？', 0.01

    emitted, logs, asks = [], [], []
    monkeypatch.setattr(main, 'EnergyVAD', FakeVAD)
    monkeypatch.setattr(main, 'LoopbackCapture', FakeCap)
    monkeypatch.setattr(main, 'log_rec', lambda rec: logs.append(rec))
    monkeypatch.setattr(main, 'emit', lambda ev, qid=None: emitted.append(ev))
    monkeypatch.setattr(main, 'ask_group', lambda qs: asks.extend(qs))
    monkeypatch.setattr(main, 'ask', lambda q: asks.append(q))
    monkeypatch.setitem(main.STATE, 'asr', FakeASR())
    monkeypatch.setattr(main.settings, 'get', lambda k, d=None: {
        'vad_end_ms': 0, 'vad_merge_ms': 0, 'hold_incomplete': False,
        'split_multi_q': True, 'split_multi_q_max': 3}.get(k, d))
    with pytest.raises(RuntimeError, match='stop'):
        main.capture_once(0.006)
    asr_evs = [e for e in emitted if e.get('type') == 'asr']
    assert len(asr_evs) == 1
    assert asr_evs[0]['text'] == '问题一是什么？问题二是什么？'
    assert asr_evs[0]['split'] == 2
    asr_logs = [r for r in logs if r.get('event') == 'asr']
    assert len(asr_logs) == 1 and asr_logs[0]['split'] == 2
    assert asks == ['问题一是什么？', '问题二是什么？']
