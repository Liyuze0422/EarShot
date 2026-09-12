# -*- coding: utf-8 -*-
"""面试提词器 · 实时语音识别引擎

链路: WASAPI 回环采集(只抓系统播放的声音=面试官) -> 能量VAD切句 -> SenseVoice ONNX 识别

关键设计:
  1. 只采集回环设备,不碰麦克风 -> 天然只转写面试官,无需声纹分离
  2. 模型路径必须是纯 ASCII(中文路径下 sentencepiece 的 C++ 层打不开文件)
  3. 启动时必须 warmup() 预热,否则首次识别要 20+ 秒(numba JIT)
"""
import os
import sys
import time
import re
import threading
import queue
import numpy as np
import soundcard as sc
import soundfile as sf

SR = 16000
BLOCK_MS = 20
BLOCK = SR * BLOCK_MS // 1000


class LoopbackCapture:
    """WASAPI 回环采集:拿到的是系统正在播放的音频(会议里=面试官的声音)。"""
    def __init__(self, samplerate=SR):
        self.samplerate = samplerate
        spk = sc.default_speaker()
        self.device_name = spk.name
        self.mic = sc.get_microphone(id=str(spk.name), include_loopback=True)

    def frames(self, stop_event=None):
        with self.mic.recorder(samplerate=self.samplerate, channels=1, blocksize=BLOCK) as rec:
            while not (stop_event and stop_event.is_set()):
                d = rec.record(numframes=BLOCK)
                yield (d[:, 0] if d.ndim > 1 else d).astype(np.float32)


def default_speaker_name():
    """当前默认播放设备名 —— 面试中插拔耳机/切设备时要能发现。"""
    try:
        return sc.default_speaker().name or ''
    except Exception:
        return ''


def calibrate_threshold(cap, seconds=1.5, floor=0.004, ceil=0.02):
    """按房间底噪定 VAD 阈值。

    写死 0.006 的问题：面试官音量偏小时整句被切碎，环境吵时又把噪音当语音。
    真实一面里面试官说过"稍等，我把声音放大" —— 阈值必须跟着环境走。
    返回 (阈值, 实测底噪)。
    """
    import numpy as _np
    need = int(SR * seconds); blocks = []; got = 0
    try:
        with cap.mic.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
            while got < need:
                d = rec.record(numframes=BLOCK)
                d = d[:, 0] if getattr(d, 'ndim', 1) > 1 else d
                blocks.append(d); got += len(d)
    except Exception:
        return 0.006, 0.0
    audio = _np.concatenate(blocks)[:need]
    rms = float(_np.sqrt((audio.astype('float64') ** 2).mean()))
    return min(ceil, max(floor, rms * 3 + 0.002)), rms


class EnergyVAD:
    """能量 VAD + 端点检测。语音停 end_ms 毫秒判为一句结束。"""
    def __init__(self, thresh=0.006, start_ms=160, end_ms=600, max_ms=20000, min_ms=300):
        self.thresh = thresh
        self.start_need = max(1, start_ms // BLOCK_MS)
        self.end_need = max(1, end_ms // BLOCK_MS)
        self.max_blocks = max_ms // BLOCK_MS
        self.min_blocks = min_ms // BLOCK_MS
        self.reset()

    def reset(self):
        self.buf, self.speech, self.sil, self.voiced = [], False, 0, 0

    def feed(self, block):
        rms = float(np.sqrt((block.astype(np.float64) ** 2).mean()))
        loud = rms > self.thresh
        if not self.speech:
            if loud:
                self.voiced += 1
                self.buf.append(block)
                if self.voiced >= self.start_need:
                    self.speech, self.sil = True, 0
                    return 'speech', None
            else:
                self.voiced, self.buf = 0, []
            return 'idle', None
        self.buf.append(block)
        self.sil = 0 if loud else self.sil + 1
        if self.sil >= self.end_need:
            audio = np.concatenate(self.buf)
            self.reset()
            if len(audio) < self.min_blocks * BLOCK:
                return 'idle', None
            return 'end', audio
        if len(self.buf) >= self.max_blocks:
            return self._split_long()
        return 'speech', None

    def _split_long(self):
        """说到 max_ms 还没停：别硬切在句子中间。

        面试官一口气讲 30 秒是常事；旧的硬切会把一句话拦腰截断，后面 extract_q
        就在残句上找问题。改成在最近几秒里挑**最安静的 300ms** 切开，
        后半段留作下一句的开头（不清空缓冲）。
        """
        win = max(1, 300 // BLOCK_MS)
        tail_from = max(self.start_need, len(self.buf) - win * 8)
        seg = self.buf[tail_from:]
        best = 0
        if seg:
            energies = [float(np.sqrt((b.astype(np.float64) ** 2).mean())) for b in seg]
            best = min(range(len(energies)), key=lambda i: sum(energies[i:i + win]))
        cut = tail_from + best + win // 2
        cut = max(self.start_need + 1, min(cut, len(self.buf) - 1))
        head = np.concatenate(self.buf[:cut])
        self.buf = self.buf[cut:]        # 后半段接着算同一句的延续
        self.speech, self.sil, self.voiced = True, 0, 0
        if len(head) < self.min_blocks * BLOCK:
            return 'idle', None
        return 'end', head


class SenseVoiceASR:
    def __init__(self, model_dir, threads=8):
        from funasr_onnx import SenseVoiceSmall
        t0 = time.time()
        self.model = SenseVoiceSmall(model_dir, batch_size=1, quantize=True,
                                     intra_op_num_threads=threads)
        self.load_s = time.time() - t0
        self._tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.tmp')
        os.makedirs(self._tmp, exist_ok=True)
        self._n = 0

    @staticmethod
    def _clean(t):
        return re.sub(r'<\|[^|]*\|>', '', t).strip()

    def transcribe(self, audio, sr=SR):
        self._n += 1
        wav = os.path.abspath(os.path.join(self._tmp, f'live_{self._n:05d}.wav'))
        sf.write(wav, audio.astype(np.float32), sr)
        t0 = time.time()
        res = self.model([wav])
        dt = time.time() - t0
        txt = res[0] if isinstance(res, list) else str(res)
        return self._clean(txt), dt

    def warmup(self, seconds=2.0):
        """必须预热:首次调用含 numba JIT 编译,要 20 秒以上。"""
        t0 = time.time()
        silence = (np.random.randn(int(SR * seconds)) * 1e-4).astype(np.float32)
        self.transcribe(silence)
        return time.time() - t0


def rss_mb():
    try:
        out = os.popen('powershell -NoProfile -Command "(Get-Process -Id %d).WorkingSet64/1MB"' % os.getpid()).read().strip()
        return float(out)
    except Exception:
        return -1.0
