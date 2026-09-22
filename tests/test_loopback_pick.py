# -*- coding: utf-8 -*-
"""回环设备选择：蓝牙耳机撞名时，绝不能选到真麦克风。

背景（2026-09-22 用户反馈）："第一次装好，提词器只听得见我自己说话，面试官说什么它不知道"。
根因既不在采集也不在 ASR，在「挑设备」这一步：

    sc.get_microphone(id=spk.name, include_loopback=True)

soundcard 的 _match_device()（mediafoundation.py:165）先按 id 精确匹配，匹配不上就按**名字**匹配；
而 all_microphones(include_loopback=True) 返回的是「先全部回环、再全部真麦克风」，
它用名字做字典键 —— **后写入的同名真麦克风会把回环设备覆盖掉**。
Windows 上蓝牙耳机（Hands-Free）的播放端点和录音端点友好名经常一模一样，于是默认播放设备按名字一查，
命中的是它同名的**真麦克风**。

所以现在的 pick_loopback() 只在 isloopback=True 的设备里挑，且优先按 WASAPI id 匹配。
这里把 soundcard 的匹配算法逐行复刻一份，用来证明「旧写法确实会选错」——不是推测。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import asr_engine                                   # noqa: E402
from asr_engine import pick_loopback                # noqa: E402

BT_NAME = '耳机 (WH-1000XM4 Hands-Free AG Audio)'


class Dev:
    """冒充 soundcard 的设备对象，只用到 name / id / isloopback 三个属性。"""
    def __init__(self, name, dev_id, isloopback=False):
        self.name = name
        self.id = dev_id
        self.isloopback = isloopback


def match_device_like_soundcard(dev_id, devices):
    """soundcard._match_device 的复刻（只保留 id / 名字两条分支）。"""
    by_id = {d.id: d for d in devices}
    by_name = {d.name: d for d in devices}      # ← 同名的：后写入的覆盖先写入的
    if dev_id in by_id:
        return by_id[dev_id]
    for name, device in by_name.items():
        if dev_id in name:
            return device
    raise IndexError('no device with id %s' % dev_id)


def all_mics_like_soundcard(loop_names, mic_names):
    """soundcard 的枚举顺序：**先全部回环，再全部真麦克风**。"""
    devs = [Dev(n, 'id-loop-%d' % i, isloopback=True) for i, n in enumerate(loop_names)]
    devs += [Dev(n, 'id-mic-%d' % i) for i, n in enumerate(mic_names)]
    return devs


def bt_devices():
    """蓝牙耳机：播放端点与录音端点同名，回环在前、真麦在后。"""
    loop = Dev(BT_NAME, 'id-loop-bt', isloopback=True)
    mic = Dev(BT_NAME, 'id-mic-bt')
    return loop, mic, [loop, mic]


# ── 先证明旧写法确实是错的（否则这个测试就是自说自话）─────────────────
def test_old_name_lookup_would_pick_the_microphone():
    """旧写法（按名字查）会拿到真麦克风 —— 这就是用户踩到的那个 bug。"""
    loop, mic, devs = bt_devices()
    got = match_device_like_soundcard(BT_NAME, devs)
    assert got is mic and not got.isloopback


def test_pick_loopback_returns_loopback_not_microphone():
    """新写法在同样的设备列表里必须拿到回环设备。"""
    loop, mic, devs = bt_devices()
    got = pick_loopback(devs, speaker_id=loop.id, speaker_name=BT_NAME)
    assert got is loop and got.isloopback


# ── 各种退化情形 ──────────────────────────────────────────────────────
def test_id_wins_over_name():
    """名字一样时以 id 为准（两个蓝牙端点同名，id 才是唯一身份）。"""
    loop, mic, devs = bt_devices()
    assert pick_loopback(devs, speaker_id=loop.id, speaker_name=BT_NAME) is loop


def test_falls_back_to_exact_name_when_id_unknown():
    loop, mic, devs = bt_devices()
    assert pick_loopback(devs, speaker_id=None, speaker_name=BT_NAME) is loop


def test_name_rewritten_by_windows_still_matches():
    """Windows 会给设备名加序号（"2- XXX"），包含匹配要兜住，但只在回环里兜。"""
    devs = all_mics_like_soundcard(['耳机 (2- WH-1000XM4 Hands-Free AG Audio)'], [BT_NAME])
    got = pick_loopback(devs, speaker_id="id-nope", speaker_name=BT_NAME)
    assert got is devs[0] and got.isloopback


def test_no_loopback_returns_none_rather_than_a_microphone():
    """只有真麦克风时返回 None —— 让上层报错，绝不退而求其次去录用户自己。"""
    devs = all_mics_like_soundcard([], [BT_NAME])
    assert pick_loopback(devs, speaker_id='id-loop-bt', speaker_name=BT_NAME) is None


def test_empty_device_list():
    assert pick_loopback([], speaker_id='x', speaker_name='y') is None


def test_other_speakers_loopback_is_not_picked():
    """默认播放设备换成另一个之后，不能还拿旧的：id 对不上、名字也对不上就返回 None。"""
    devs = all_mics_like_soundcard(['扬声器 (Realtek(R) Audio)'], [])
    assert pick_loopback(devs, speaker_id='id-loop-bt', speaker_name=BT_NAME) is None


# ── LoopbackCapture 的兜底：宁可报错，也不要静默录麦克风 ────────────────
class _FakeSC:
    """假的 soundcard 模块：默认播放设备给一个没有回环的蓝牙端点。"""
    def __init__(self, spk, devs):
        self._spk = spk
        self._devs = devs

    def default_speaker(self):
        return self._spk

    def all_microphones(self, include_loopback=False):
        return self._devs


def test_capture_raises_when_no_loopback(monkeypatch):
    """拿不到回环就抛错（上层会显示音频丢失+原因），而不是偷偷录麦克风。"""
    spk = Dev(BT_NAME, "id-loop-bt")
    fake = _FakeSC(spk, [Dev(BT_NAME, "id-mic-bt")])
    monkeypatch.setattr(asr_engine, 'sc', fake)
    with pytest.raises(RuntimeError):
        asr_engine.LoopbackCapture()


def test_capture_picks_loopback_on_normal_machine(monkeypatch):
    """名字不撞名的普通机器：照常拿到回环设备。"""
    spk = Dev('耳机 (Realtek(R) Audio)', 'id-loop-rt')
    loop = Dev('耳机 (Realtek(R) Audio)', 'id-loop-rt', isloopback=True)
    mic = Dev('麦克风 (Realtek(R) Audio)', 'id-mic-rt')
    fake = _FakeSC(spk, [loop, mic])
    monkeypatch.setattr(asr_engine, 'sc', fake)
    cap = asr_engine.LoopbackCapture()
    assert cap.mic is loop
    assert cap.device_id == 'id-loop-rt'
