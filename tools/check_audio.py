# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import soundcard as sc
print('=== 默认扬声器 ===')
spk = sc.default_speaker()
print(' ', spk.name)
print()
print('=== 回环采集设备 ===')
try:
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    print('  拿到:', mic.name, ' isloopback=', mic.isloopback)
except Exception as e:
    print('  ✗ 取回环设备失败:', type(e).__name__, e)
print()
print('=== 全部含 loopback 的采集设备 ===')
n = 0
for m in sc.all_microphones(include_loopback=True):
    if m.isloopback:
        print('  -', m.name)
        n += 1
print('  共', n, '个回环设备')