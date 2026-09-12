# -*- coding: utf-8 -*-
"""P0+P1 冒烟测试：WASAPI 回环 -> 能量VAD 切句 -> SenseVoice ONNX 识别 -> 打印文本。

用法:
  python tools/e2e_smoke.py             # 一直跑，Ctrl+C 停
  python tools/e2e_smoke.py --seconds 30

衡量指标: 端到端延迟(说完->出字)、单句识别耗时、进程内存峰值。
"""
import os, sys, time, argparse, ctypes
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, '.pylibs'))

SR = 16000
BLOCK_MS = 20
BLOCK = SR * BLOCK_MS // 1000


def rss_mb():
    class PMC(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                    ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
    p = PMC(); p.cb = ctypes.sizeof(PMC)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return p.WorkingSetSize / 2**20, p.PeakWorkingSetSize / 2**20


class EnergyVAD:
    """极简能量 VAD：够 P0 用，后续可换 silero。"""
    def __init__(self, thresh=0.006, start_ms=160, end_ms=600, max_ms=15000):
        self.thresh = thresh
        self.start_need = start_ms // BLOCK_MS
        self.end_need = end_ms // BLOCK_MS
        self.max_blocks = max_ms // BLOCK_MS
        self.buf, self.speech, self.sil, self.voiced = [], False, 0, 0

    def feed(self, block):
        """返回 (状态, 完整语句 audio | None)。状态: 'idle' / 'speech' / 'end'"""
        rms = float(np.sqrt((block.astype(np.float64) ** 2).mean()))
        loud = rms > self.thresh
        if not self.speech:
            if loud:
                self.voiced += 1
                self.buf.append(block)
                if self.voiced >= self.start_need:
                    self.speech = True
                    self.sil = 0
                    return 'speech', None
            else:
                self.voiced = 0
                self.buf = []
            return 'idle', None
        self.buf.append(block)
        self.sil = 0 if loud else self.sil + 1
        if self.sil >= self.end_need or len(self.buf) >= self.max_blocks:
            audio = np.concatenate(self.buf)
            self.buf, self.speech, self.sil, self.voiced = [], False, 0, 0
            return 'end', audio
        return 'speech', None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=0, help='0 = 一直跑')
    ap.add_argument('--model', default=os.path.join(ROOT, 'models', 'SenseVoiceSmall-onnx'))
    ap.add_argument('--no-asr', action='store_true', help='只测音频+VAD')
    args = ap.parse_args()

    import soundcard as sc
    spk = sc.default_speaker()
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    print(f'[音频] 回环设备: {mic.name}  @{SR}Hz')

    asr = None
    if not args.no_asr:
        t0 = time.time()
        from funasr_onnx import SenseVoiceSmall
        asr = SenseVoiceSmall(args.model, batch_size=1, quantize=True)
        print(f'[ASR ] 模型加载完成 {time.time()-t0:.1f}s  RSS={rss_mb()[0]:.0f}MB')

    vad = EnergyVAD()
    wav_dir = os.path.join(ROOT, '.tmp', 'wav')
    os.makedirs(wav_dir, exist_ok=True)
    n_utt = 0
    t_start = time.time()
    seg_start = 0.0

    print('[就绪] 播放一段人声(会议/视频)开始测试。Ctrl+C 停止。')
    try:
        with mic.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
            while args.seconds <= 0 or time.time() - t_start < args.seconds:
                data = rec.record(numframes=BLOCK)
                mono = data[:, 0] if data.ndim > 1 else data
                st, audio = vad.feed(mono)
                if st == 'speech' and seg_start == 0.0:
                    seg_start = time.time()
                    print('  · 检测到说话…')
                if st == 'end':
                    n_utt += 1
                    dur = len(audio) / SR
                    seg_start = 0.0
                    if asr is None:
                        print(f'  [{n_utt}] {dur:.2f}s 语音 (未启用 ASR)')
                        continue
                    import soundfile as sf
                    wav = os.path.join(wav_dir, f'utt_{n_utt:03d}.wav')
                    sf.write(wav, audio.astype(np.float32), SR)
                    ta = time.time()
                    try:
                        res = asr([wav])
                        text = res[0] if isinstance(res, list) else str(res)
                    except Exception as e:
                        text = f'<ASR失败 {type(e).__name__}: {e}>'
                    dt = time.time() - ta
                    cur, peak = rss_mb()
                    print(f'  [{n_utt}] {dur:.2f}s音频  识别{dt*1000:.0f}ms  RTF={dt/max(dur,0.01):.2f}  RSS={cur:.0f}MB(峰{peak:.0f}MB)')
                    print(f'       >> {text}')
    except KeyboardInterrupt:
        print()
        print('[停止]')
    cur, peak = rss_mb()
    print(f'[汇总] 识别 {n_utt} 句  当前RSS={cur:.0f}MB  峰值RSS={peak:.0f}MB')


if __name__ == '__main__':
    main()