# -*- coding: utf-8 -*-
"""端到端自测 v2：TTS 朗读已知文本 -> 回环采集 -> VAD -> SenseVoice 识别（ASCII 模型路径）"""
import os, sys, time, subprocess, threading, re, wave
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import soundcard as sc
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import settings                        # noqa: E402  统一配置：环境变量 > config/settings.json > 默认值
MODEL = settings.ensure_ascii(settings.model_dir(), 'ASR 模型目录（model_dir）')
SR, BLOCK_MS = 16000, 20
BLOCK = SR * BLOCK_MS // 1000
TEXT = '我们一共用了十二个工具，通过路由缓存把延迟从八百毫秒降到了二百六十毫秒。'

def speak(text):
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.SelectVoice('Microsoft Huihui Desktop'); $s.Rate = 0; $s.Volume = 100; "
          "$s.Speak('" + text + "');")
    subprocess.run(['powershell','-NoProfile','-Command',ps], capture_output=True)

print('='*66)
print('步骤 1  回环采集 + TTS 朗读')
print('='*66)
spk = sc.default_speaker()
mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
print('  回环设备:', mic.name)
print('  朗读文本:', TEXT)
frames = []
t = threading.Thread(target=speak, args=(TEXT,), daemon=True)
with mic.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
    t.start(); t0 = time.time()
    while time.time() - t0 < 9.0:
        d = rec.record(numframes=BLOCK)
        frames.append((d[:,0] if d.ndim>1 else d).copy())
Audio = np.concatenate(frames)
print(f'  录到 {len(Audio)/SR:.1f}s  RMS={np.sqrt((Audio.astype(float)**2).mean()):.4f}  峰值={np.abs(Audio).max():.3f}')

print()
print('='*66)
print('步骤 2  能量 VAD 切句')
print('='*66)
segs, cur, sil = [], [], 0
for i in range(0, len(Audio)-BLOCK, BLOCK):
    b = Audio[i:i+BLOCK]
    loud = float(np.sqrt((b.astype(float)**2).mean())) > 0.006
    if loud: cur.append(b); sil = 0
    elif cur:
        cur.append(b); sil += 1
        if sil >= 30: segs.append(np.concatenate(cur)); cur=[]; sil=0
if cur: segs.append(np.concatenate(cur))
segs = [s for s in segs if len(s)/SR > 0.3]
print(f'  切出 {len(segs)} 段:', [f'{len(s)/SR:.2f}s' for s in segs])

print()
print('='*66)
print('步骤 3  加载 SenseVoice ONNX')
print('='*66)
from funasr_onnx import SenseVoiceSmall
t0 = time.time()
m = SenseVoiceSmall(MODEL, batch_size=1, quantize=True)
print(f'  加载耗时 {time.time()-t0:.1f}s')
print(f'  当前进程 RSS = {os.popen("powershell -NoProfile -Command \"(Get-Process -Id " + str(os.getpid()) + ").WorkingSet64/1MB\"").read().strip()} MB')

print()
print('='*66)
print('步骤 4  识别')
print('='*66)
def clean(t): return re.sub(r'<\|[^|]*\|>', '', t).strip()
times = []
for i, s in enumerate(segs, 1):
    w = os.path.join(ROOT, '.tmp', f'seg{i}.wav')
    sf.write(w, s.astype(np.float32), SR)
    ta = time.time()
    try:
        res = m([w]); txt = clean(res[0] if isinstance(res,list) else str(res))
    except Exception as e:
        txt = f'<失败 {type(e).__name__}: {e}>'
    dt = time.time()-ta; dur = len(s)/SR; times.append(dt)
    print(f'  [{i}] {dur:.2f}s 音频 -> {dt*1000:5.0f} ms  RTF={dt/dur:.3f}')
    print(f'      识别: {txt}')
print()
print(f'  识别平均耗时 {np.mean(times)*1000:.0f} ms')

print()
print('='*66)
print('步骤 5  二次识别（模型已在内存，测真实推理速度）')
print('='*66)
w = os.path.join(ROOT, '.tmp', 'seg1.wav')
if os.path.exists(w):
    for k in range(3):
        ta = time.time(); m([w]); dt = time.time()-ta
        dur = len(segs[0])/SR if segs else 1
        print(f'  第{k+1}次: {dt*1000:5.0f} ms  RTF={dt/dur:.3f}')