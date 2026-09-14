# -*- coding: utf-8 -*-
"""tests 公共夹具。

这里只处理一件事：`server/asr_engine.py` 在**模块顶层** `import soundcard`，
而 soundcard 在 Linux 上**导入时就会去连接 PulseAudio 守护进程**
（`soundcard/pulseaudio.py` 里 `_PulseAudio()` 断言 context ready）。
GitHub Actions 的 ubuntu runner 没有 PulseAudio 服务，于是凡是 `import main` 的测试
（tests/test_merge_gate.py）在**收集期**就 AssertionError —— 音频后端不存在，
不该拖垮"压根不碰音频"的测试。

兜底只在该模块导入失败时生效：

- Windows（真实使用环境）：soundcard 正常导入，用的一直是真模块，行为不变；
- CI 的 Linux：塞一个空模块占位，测试照常收集与执行，真去录音才会暴露 AttributeError。
"""
import sys
import types

try:                       # 有真模块就用真的
    import soundcard  # noqa: F401
except Exception:          # 没有可用音频后端（CI 上的 Linux runner）：占位，别让收集期挂掉
    sys.modules['soundcard'] = types.ModuleType('soundcard')
