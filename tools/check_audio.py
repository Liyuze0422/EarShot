# -*- coding: utf-8 -*-
r"""面试提词器 · 音频设备自检（只读，不录音、不改任何文件）

    python -X utf8 tools/check_audio.py

回答一个问题：**提词器现在到底在听哪个设备？是扬声器回环，还是你自己的麦克风？**

为什么要单独有这个脚本（0.9.22）：
用户反馈「第一次装好，提词器只听得见我自己说话，面试官说什么它不知道」。
根因是 soundcard 按**设备名**找设备时会被同名设备顶掉：
Windows 上蓝牙耳机的播放端点和录音端点友好名经常一模一样，
而 `all_microphones(include_loopback=True)` 是「先全部回环、再全部真麦克风」，
真麦克风会把同名的回环设备覆盖掉 —— 于是按名字查到的是麦克风。
本脚本把两种选法的结果并排打出来，一眼能看出中没中招。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))

import soundcard as sc            # noqa: E402
from asr_engine import pick_loopback   # noqa: E402


def main():
    print('=' * 78)
    print('  面试提词器 · 音频设备自检')
    print('=' * 78)

    try:
        spk = sc.default_speaker()
    except Exception as e:
        print('✗ 拿不到默认播放设备: %s: %s' % (type(e).__name__, e))
        print('  → 耳机/音箱插好了吗？「设置 → 系统 → 声音」里选一个默认输出。')
        return 1
    print('默认播放设备: %s' % spk.name)
    print('  它的 WASAPI id: %s' % spk.id)
    print()

    devices = sc.all_microphones(include_loopback=True)
    print('系统里的采集设备（%d 个）:' % len(devices))
    for m in devices:
        print('  %-6s %-52s %s' % ('回环' if m.isloopback else '真麦', m.name, m.id))
    print()

    # ── 旧写法：直接按名字查（0.9.21 及以前用的就是它）──────────────────
    print('--- 旧写法 sc.get_microphone(id=设备名, include_loopback=True) ---')
    bug_hit = False
    try:
        old = sc.get_microphone(id=str(spk.name), include_loopback=True)
        print('  拿到: %s   isloopback=%s' % (old.name, old.isloopback))
        if not old.isloopback:
            bug_hit = True
            print('  ✗ **它拿到的是真麦克风** —— 这就是那个 bug：提词器会去听你自己说话。')
    except Exception as e:
        print('  ✗ 失败: %s: %s' % (type(e).__name__, e))
    print()

    # ── 新写法：只在回环设备里挑，且优先按 id 匹配 ──────────────────────
    print('--- 现在用的 pick_loopback()（0.9.22 起）---')
    got = pick_loopback(devices, speaker_id=spk.id, speaker_name=spk.name)
    if got is None:
        print('  ✗ 没找到「%s」对应的回环设备' % spk.name)
        print('  → 这台机器上这个播放设备没有回环端点，提词器启动时会直接报错，'
              '不会偷偷去录麦克风。换个默认输出设备再试。')
        return 1
    print('  拿到: %s   isloopback=%s' % (got.name, got.isloopback))
    print()

    print('=' * 78)
    if bug_hit:
        print('  结论: 这台机器**会**踩到撞名 bug（旧版选错设备）。')
        print('        升级到 0.9.22 即可 —— 新版按 id 匹配，不会再选错。')
        print('        （现在源码跑的已经是新版，直接启动提词器即可。）')
    else:
        print('  结论: ✓ 选中的是回环设备「%s」，提词器只会听到系统播放的声音（=面试官）。' % got.name)
    print('=' * 78)
    return 0


if __name__ == '__main__':
    sys.exit(main())
