# -*- coding: utf-8 -*-
r"""面试提词器 · 面试前 30 秒全链路自检 (preflight)

一条命令跑完 10 项，逐项输出 PASS/FAIL/WARN + 耗时；有 FAIL 则退出码非 0。

    python -X utf8 tools/preflight.py          # 全量（约 20-40 秒）
    python -X utf8 tools/preflight.py --fast   # 快速(跳过最慢两项)

用装了依赖的那个解释器即可（例如 .venv\Scripts\python.exe）；在仓库根目录下运行。

退出码: 有 FAIL -> 1；WARN/SKIP 不影响退出码（但面试前看到 WARN 也该心里有数）。

本脚本的硬约束 —— 只新建、不改任何已有文件：
  · 浮窗自检把 ui/app.py 复制到 .tmp/preflight/ 再跑，截图落在 .tmp 里，不碰 ui/ 目录
    （ui/app.py 的 --selftest 会把截图写到自己所在目录，直接跑就会覆盖 ui/.selftest.png）。
  · ASR 识别把 SenseVoiceASR 的临时输出目录指到 .tmp/preflight/asr_out/：
    transcribe() 按内部计数器写 live_0000N.wav，不重定向就会覆盖 .tmp 里的真录音。
  · 其余检查全是只读（打开设备、建内存索引、发一次请求、读文件）。
"""
import os
import sys
import re
import time
import glob
import socket
import shutil
import struct
import ctypes
import subprocess
import threading
import unicodedata

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_DIR = os.path.join(ROOT, 'server')
UI_DIR = os.path.join(ROOT, 'ui')
TMP_DIR = os.path.join(ROOT, '.tmp')
WORK_DIR = os.path.join(TMP_DIR, 'preflight')          # 本脚本自己的临时目录
sys.path.insert(0, SERVER_DIR)

import settings                    # 统一配置：环境变量 > config/settings.json > 默认值

FAST = '--fast' in sys.argv[1:]

PORT = settings.port_list()[0]     # 默认 8765；候选端口列表见 config/settings.json 的 ports
# 模型目录不再在这儿留一份字面量：统一由 _model_dir() -> settings.model_dir() 取
VAD_THRESH = 0.006                 # 与 server/asr_engine.py EnergyVAD 默认阈值保持一致
CAP_SECONDS = 1.0                  # 第 2 项采样时长
DS_TIMEOUT = 12.0 if FAST else 45.0
UI_TIMEOUT = 90.0
PROBE_QUESTION = '你在那个推荐系统里怎么做召回和粗排的？'   # 有材料可依，省 token
KNOWN_QUERY = '项目A_推荐系统'
KNOWN_EXPECT = '项目A'
BASELINE_HINT = ('基线(2026-09-11 真实一面回放): 路由 100% / 检索 hit@1 84.6% / '
                 'hit@3 92.3% / 题库并集 hit@3 94.9% / 首字中位 582ms')

CTX = {}
ROWS = []


# ── 输出小工具 ────────────────────────────────────────────────────────
def _dw(s):
    """中文按 2 列算的显示宽度，用来对齐表格。"""
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)


def _pad(s, n):
    s = str(s)
    return s + ' ' * max(0, n - _dw(s))


def _clip(s, n):
    if _dw(s) <= n:
        return s
    out, w = '', 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if w + cw > n - 1:
            break
        out += ch
        w += cw
    return out + '…'


COL = (5, 26, 7, 9)        # # / 检查项 / 结果 / 耗时


def row(idx, name, status, cost, detail):
    head = '  %s%s%s%s' % (
        _pad(str(idx), COL[0]), _pad(name, COL[1]), _pad(status, COL[2]), _pad('%.2fs' % cost, COL[3]))
    indent = ' ' * (2 + sum(COL[:3]))
    lines = str(detail).split('\n')
    print(head + lines[0], flush=True)
    for ln in lines[1:]:
        print(indent + ln, flush=True)


def hr(ch='-'):
    print(ch * 86, flush=True)


def banner():
    hr('=')
    print('  面试提词器 · 面试前全链路自检 (preflight)', flush=True)
    mode = '快速 --fast（跳过 ASR 识别 / 浮窗自检，目标 20 秒内）' if FAST else '全量（含 ASR 识别与浮窗自检，约 20-40 秒）'
    print('  模式: %s' % mode, flush=True)
    print('  时间: %s   venv: %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), sys.executable), flush=True)
    hr('=')
    print('  %s%s%s%s' % (_pad('#', COL[0]), _pad('检查项', COL[1]), _pad('结果', COL[2]), _pad('耗时', COL[3]) + '说明'),
          flush=True)
    hr()


# ── 1. 回环采集设备 ───────────────────────────────────────────────────
def check_loopback_device():
    from asr_engine import LoopbackCapture
    try:
        cap = LoopbackCapture()
    except Exception as e:
        return 'FAIL', '拿不到回环设备（耳机/扬声器插好了吗？）: %s: %s' % (type(e).__name__, e)
    CTX['cap'] = cap
    return 'PASS', '默认回环设备: %s（isloopback=%s）  →  只会听到系统播放的声音' % (
        cap.device_name or '?', getattr(cap.mic, 'isloopback', None))


# ── 2. 当前有没有声音在放 ─────────────────────────────────────────────
def check_sound_playing():
    cap = CTX.get('cap')
    if cap is None:
        return 'SKIP', '第 1 项没拿到回环设备，无法录音'
    import numpy as np
    from asr_engine import SR, BLOCK
    need = int(SR * CAP_SECONDS)
    blocks, got = [], 0
    with cap.mic.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
        while got < need:
            d = rec.record(numframes=BLOCK)
            d = d[:, 0] if getattr(d, 'ndim', 1) > 1 else d
            blocks.append(d)
            got += len(d)
    audio = np.concatenate(blocks)[:need].astype('float32')
    rms = float(np.sqrt((audio.astype('float64') ** 2).mean()))
    peak = float(np.abs(audio).max())
    if rms >= VAD_THRESH:
        return 'PASS', '采样 %.1fs  RMS=%.4f  峰值=%.4f（≥VAD 阈值 %.3f，回环链路通）' % (
            len(audio) / SR, rms, peak, VAD_THRESH)
    return 'WARN', '采样 %.1fs  RMS=%.4f  峰值=%.4f  < VAD 阈值 %.3f：采集正常，但当前没有声音在放。\n' % (
        len(audio) / SR, rms, peak, VAD_THRESH) + \
        '面试前先放段音频（任意会议录音/视频）确认能量条会动，否则面试官说话也可能被 VAD 当静音丢掉'


# ── 3. ASR 模型目录 + 真识别 ──────────────────────────────────────────
def _model_dir():
    """模型目录：统一走 server/settings.py（环境变量 > config/settings.json > 默认值）。

    以前是从 server/main.py 里正则抠 MODEL_DIR 字面量；现在那边也只是转调 settings，
    再抠字符串就等于抄一份配置，还会在报错信息里指错地方。
    """
    return settings.model_dir()


def _candidate_wavs():
    """.tmp 下的样本音频，按 RMS 从大到小排（越响越像真人说话）。"""
    import numpy as np
    import soundfile as sf
    files = sorted(set(glob.glob(os.path.join(TMP_DIR, 'live_*.wav')) +
                       glob.glob(os.path.join(TMP_DIR, '*.wav'))))
    out = []
    for fp in files:
        try:
            a, sr = sf.read(fp, dtype='float32')
            if a.ndim > 1:
                a = a[:, 0]
            out.append((float(np.sqrt((a.astype('float64') ** 2).mean())), a.shape[0] / float(sr), fp))
        except Exception:
            continue
    # live_*.wav 是回环真录音，优先拿它们验证；没有才退到其它样本(旧片段/TTS)
    out.sort(key=lambda t: (0 if os.path.basename(t[2]).startswith('live_') else 1, -t[0]))
    return out


def check_asr():
    if FAST:
        return 'SKIP', '--fast 跳过（加载 ONNX + 首次识别含 numba JIT，约 10 秒，是最慢的两项之一）'
    model_dir = _model_dir()
    if not os.path.isdir(model_dir):
        return 'FAIL', '模型目录不存在: %s\n跑 tools/download_model.py 下载（约 230MB），'\
            '或改 config/settings.json 的 model_dir / 设环境变量 TP_MODEL_DIR 指到已有模型目录' % model_dir
    onnx = glob.glob(os.path.join(model_dir, '*.onnx'))
    if not onnx:
        return 'FAIL', '模型目录在但没有 .onnx 权重: %s' % model_dir

    cands = _candidate_wavs()
    import numpy as np
    import soundfile as sf
    from asr_engine import SenseVoiceASR, SR
    t0 = time.time()
    asr = SenseVoiceASR(model_dir)
    load = time.time() - t0

    # 关键:把输出目录指到自己的 .tmp，别让 transcribe() 覆盖 .tmp/live_0000N.wav 那几段真录音
    asr._tmp = os.path.join(WORK_DIR, 'asr_out')
    os.makedirs(asr._tmp, exist_ok=True)
    asr._n = 90000

    if cands:
        used = cands[:3]
        src_note = '%s（.tmp 真录音，RMS %.4f）' % (os.path.basename(used[0][2]), used[0][0])
    else:
        # 机器上没样本：造一段静音，只验证"模型能跑通"，识别内容自然为空
        used = [(0.0, 1.0, None)]
        src_note = '机器上没找到样本 wav，改用 1 秒静音（只能验证模型能跑，验证不了识别质量）'

    best = None
    tried = []
    for rms, dur, fp in used:
        if fp is None:
            audio = (np.random.randn(int(SR * dur)) * 1e-4).astype('float32')
            sr = SR
        else:
            audio, sr = sf.read(fp, dtype='float32')
            if audio.ndim > 1:
                audio = audio[:, 0]
        t1 = time.time()
        txt, _ = asr.transcribe(audio, sr)
        dt = time.time() - t1
        tried.append('%s(%.2fs)' % (os.path.basename(fp) if fp else '静音', dt))
        if txt.strip():
            best = (fp, txt.strip(), dt, dur)
            break

    if best:
        fp, txt, dt, dur = best
        nm = os.path.basename(fp) if fp else '静音'
        return 'PASS', '模型加载 %.1fs · 识别 %.2fs（%.1fs 音频）· 样本 %s · %s\n识别结果：「%s」' % (
            load, dt, dur, nm, src_note, _clip(txt, 60))
    return 'WARN', '模型加载正常（%.1fs），但 %d 段样本都识别成空串（试了 %s）。\n' % (
        load, len(tried), ' '.join(tried)) + \
        '%s\n静音/噪声段返回空串是正常的；若换真人说话的 wav 仍为空，再查模型。' % src_note


# ── 4. 检索索引 ───────────────────────────────────────────────────────
def check_retrieval():
    from knowledge import build
    t0 = time.time()
    idx, chunks = build()
    build_s = time.time() - t0
    if not chunks:
        return 'FAIL', '索引建起来了但语料 0 块：把材料放进 %s（.md / .txt），'\
            '或改 config/settings.json 的 corpus_globs（当前：%s）' % (
                os.path.join(ROOT, 'knowledge'), '; '.join(settings.corpus_globs()))

    # 基准问题：语料里还有那份自带基准材料时，按"问它能不能命中基准"验；
    # 换成你自己的材料后基准自然不在，退化成"拿第一个块的文件名当查询，看能不能查回它自己"
    # —— 文件名在词袋里加权 4 倍，这个自检对任何语料都成立，不会因为换了材料就误报 FAIL。
    if any(KNOWN_EXPECT in src for src, _ in chunks):
        probe_q, expect = KNOWN_QUERY, KNOWN_EXPECT
    else:
        probe_q, expect = os.path.splitext(chunks[0][0])[0], chunks[0][0]

    t1 = time.time()
    hits = idx.search(probe_q, topk=4)
    q_ms = (time.time() - t1) * 1000
    srcs = [s for _, s, _ in hits]
    if not srcs:
        return 'FAIL', '切块 %d 但查询「%s」零命中，BM25 词袋可能坏了' % (len(chunks), probe_q)
    if expect in srcs[0]:
        return 'PASS', '切块 %d · 建索引 %.2fs · 查询 %.1fms · 「%s」→ top1 %s' % (
            len(chunks), build_s, q_ms, _clip(probe_q, 24), _clip(srcs[0], 40))
    if any(expect in s for s in srcs):
        return 'WARN', '切块 %d · 建索引 %.2fs · 查询 %.1fms · 「%s」top1 是 %s，%s 掉到了第 %d 位（能答，但不准）' % (
            len(chunks), build_s, q_ms, _clip(probe_q, 24), _clip(srcs[0], 30), expect,
            next(i for i, s in enumerate(srcs, 1) if expect in s))
    return 'FAIL', '切块 %d · 建索引 %.2fs · 查询 %.1fms · 「%s」top%d 里没有 %s: %s' % (
        len(chunks), build_s, q_ms, _clip(probe_q, 24), len(srcs), expect,
        ', '.join(_clip(s, 24) for s in srcs))


# ── 5. DeepSeek 快答线 ────────────────────────────────────────────────
def check_deepseek():
    import answer as A
    key_path = settings.api_key_path()
    env_key = (os.environ.get(settings.API_KEY_ENV) or '').strip()
    try:
        key = A.load_key()          # 环境变量 TP_API_KEY 优先，其次密钥文件
    except Exception as e:
        return 'FAIL', '%s\n（密钥文件: %s；也可以直接用环境变量 %s）' % (
            _clip(str(e).replace('\n', ' '), 200), key_path, settings.API_KEY_ENV)
    if len(key) < 10:
        return 'FAIL', '密钥只有 %d 个字符，八成是空的: %s' % (
            len(key), ('环境变量 %s' % settings.API_KEY_ENV) if env_key else key_path)

    box = {}

    def work():
        try:
            t0 = time.time()
            text, first, total, qt, hits = A.answer_stream(PROBE_QUESTION)
            box['r'] = (text, first, total, qt, time.time() - t0)
        except Exception as e:
            box['err'] = '%s: %s' % (type(e).__name__, e)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(DS_TIMEOUT)
    if 'err' in box:
        return 'FAIL', '真调一次失败（密钥/网络/额度）: %s' % _clip(box['err'], 120)
    if 'r' not in box:
        return 'FAIL', '%.0f 秒没等到首字（网络慢或额度卡住）；换网/换密钥再试' % DS_TIMEOUT
    text, first, total, qt, wall = box['r']
    if not text.strip():
        return 'FAIL', '请求通了但返回空内容'
    if first is None:
        return 'FAIL', '流里一个 content 增量都没有（可能是 thinking 没关掉），总耗时 %.1fs' % total
    fm = first * 1000.0
    detail = '真调一次「%s」：首字 %.0fms · 总 %.2fs · 分类 %s · %d 字（%s）' % (
        _clip(PROBE_QUESTION, 18), fm, total, qt, len(text), BASELINE_HINT)
    if fm <= 3000:
        return 'PASS', detail
    return 'WARN', detail.replace('：', '：首字偏慢（>3s），面试里会明显卡顿。', 1)


# ── 6. 端口 8765 ──────────────────────────────────────────────────────
def _port_owner():
    try:
        out = subprocess.run(['netstat', '-ano'], capture_output=True, encoding='utf-8',
                             errors='replace', timeout=8).stdout or ''
    except Exception:
        return ''
    for ln in out.splitlines():
        if ':%d ' % PORT in ln and 'LISTENING' in ln.upper():
            return ln.split()[-1]
    return ''


def check_port():
    s = socket.socket()
    s.settimeout(0.8)
    rc = s.connect_ex(('127.0.0.1', PORT))
    s.close()
    if rc != 0:
        return 'PASS', '127.0.0.1:%d 空闲，可以启动后端（server/main.py）' % PORT
    import urllib.request
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/' % PORT, timeout=3) as r:
            body = r.read(600).decode('utf-8', 'replace')
            server = r.headers.get('server', '')
        if '提词器' in body or 'teleprompter' in body.lower():
            return 'PASS', '%d 已被本机提词器后端占用（%s），说明后端已在跑，别再启动第二个' % (PORT, server or 'uvicorn')
        return 'FAIL', '%d 被别的网页服务占了（%s），但不是提词器后端：%s' % (
            PORT, server or '?', _clip(body[:60].replace('\n', ' '), 50))
    except Exception as e:
        pid = _port_owner()
        return 'FAIL', '%d 被非 HTTP 进程占用%s，后端起不来（%s: %s）' % (
            PORT, '（PID %s）' % pid if pid else '', type(e).__name__, _clip(str(e), 40))


# ── 6.5 词表体检 ─────────────────────────────────────────────────────
def check_lexicon():
    """known_terms / never_used 是人手改的 —— 脏行会静默变成词条。

    实测踩过：文件开头的说明文字被当成词条吃进去（"tools/build_terms.py" 一度
    成了"我其实没做过"的一个词）。这里提前喊一声，别等面试当天才发现。
    """
    import answer as A
    kn = sorted(A.known_terms()); nv = sorted(A.never_used())
    bad = []
    for tag, ws in (('known_terms', kn), ('never_used', nv)):
        for w in ws:
            if len(w) > 20 or any(ch in w for ch in '：。（）【】；、'):
                bad.append('%s 里疑似混进说明文字: %s' % (tag, w[:26]))
    conflict = sorted(set(kn) & set(nv))
    if conflict:
        bad.append('同一个词同时在两张表里（会按"没做过"走）: %s' % '、'.join(conflict[:6]))
    detail = '会 %d 个 · 没做过 %d 个' % (len(kn), len(nv))
    if bad:
        return 'WARN', detail + '\n' + '；\n'.join(bad[:6]) + \
            '\n改 config/known_terms.md（我会的）/ config/never_used.md（我没做过的）'
    return 'PASS', detail


# ── 6.5 换说法扩展词覆盖率 ────────────────────────────────────────────
def check_expand():
    """扩展词是**构建产物**：材料改了它不会自己更新，得重跑 tools/kb_expand.py。

    不查的话症状很隐蔽 —— 检索照常工作、只是"面试官换个说法就问不出来"，
    而这正是最难自己察觉的那类退化。
    """
    import knowledge as K
    try:
        _, chunks = K.build()
    except Exception as e:
        return 'WARN', '读语料失败：%s' % str(e)[:80]
    table = K.load_expand()
    n = len(chunks)
    have = sum(1 for _, t in chunks if table.get(K._chunk_key(t)))
    detail = '扩展词覆盖 %d/%d 块' % (have, n)
    if not table:
        return 'WARN', detail + '\n全都没有 —— 面试官用自己的说法提问时会检索不到。' \
            '\n跑 python tools/kb_expand.py 生成（离线一次，运行时不花钱）'
    if have < n:
        return 'WARN', detail + '\n有 %d 块没覆盖（多半是材料改过）。' \
            '\n跑 python tools/kb_expand.py 补上（增量，只做缺的）' % (n - have)
    return 'PASS', detail


# ── 6.6 题库新鲜度 ────────────────────────────────────────────────────
def check_bank_fresh():
    """题库比材料旧 = 材料更新了没重建，会一直用旧问法答新问题。"""
    import json as _json
    import bank as _bank                       # 题库存哪由 bank.py 决定，别在这儿再抄一份路径
    bank = _bank.bank_path()                   # 按资料包分文件：题库_<包名>.json 优先
    files = []
    for g in settings.corpus_globs():
        files += glob.glob(g, recursive=True)
    newest, newest_f = 0.0, ''
    for f in files:
        try:
            m = os.path.getmtime(f)
        except OSError:
            continue
        if m > newest:
            newest, newest_f = m, os.path.basename(f)
    if not os.path.exists(bank):
        return 'WARN', '题库还没建过：跑 tools/build_bank.py（约 30 秒，出 380+ 条问法）'
    bm = os.path.getmtime(bank)
    try:
        data = _json.load(open(bank, encoding='utf-8'))
        n = len(data.get('items', data if isinstance(data, list) else []))
    except Exception:
        n = 0
    if newest and bm < newest - 60:
        return 'WARN', '题库（%s，%d 条）比最新材料旧：%s 是 %s 改的\n材料更新过就重建: ' \
            'tools/build_bank.py --workers 8 --followup --merge' % (
                time.strftime('%m-%d %H:%M', time.localtime(bm)), n,
                newest_f, time.strftime('%m-%d %H:%M', time.localtime(newest)))
    return 'PASS', '题库 %s（%d 条），不落后于材料（最新材料 %s）' % (
        time.strftime('%m-%d %H:%M', time.localtime(bm)), n, newest_f or '?')


# ── 7. 浮窗起来 + 截图 ────────────────────────────────────────────────
def _png_size(fp):
    try:
        with open(fp, 'rb') as f:
            head = f.read(24)
        if head[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack('>II', head[16:24])
            return w, h
    except Exception:
        pass
    return 0, 0


def check_ui():
    if FAST:
        return 'SKIP', '--fast 跳过（起 Qt 进程约 4-6 秒，是最慢的两项之一）'
    app_py = os.path.join(UI_DIR, 'app.py')
    if not os.path.exists(app_py):
        return 'SKIP', 'ui/app.py 不存在，没有自检入口'
    src = open(app_py, encoding='utf-8', errors='replace').read()
    if '--selftest' not in src:
        return 'SKIP', 'ui/app.py 里没有 --selftest 自检入口，外部无法验证浮窗，不瞎猜'

    work = os.path.join(WORK_DIR, 'ui_selftest')
    os.makedirs(work, exist_ok=True)
    copy = os.path.join(work, 'ui_app_selftest.py')
    shutil.copy2(app_py, copy)          # 跑副本：截图落在 .tmp，不覆盖 ui/.selftest.png
    png = os.path.join(work, '.selftest.png')
    for old in glob.glob(os.path.join(work, '*.png')):      # 清掉上一轮截图，防止拿旧的冒充
        os.remove(old)
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['TP_NO_WS'] = '1'     # ui/app.py 自带的自检开关：不连后端，截图不被真实事件干扰
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, '-X', 'utf8', copy, '--selftest'],
                           cwd=ROOT, env=env, capture_output=True, timeout=UI_TIMEOUT,
                           encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        return 'FAIL', '浮窗进程 %.0f 秒没退出（Qt 卡死/没桌面会话？）' % UI_TIMEOUT
    dt = time.time() - t0
    out = (p.stdout or '') + (p.stderr or '')
    CTX['ui_out'] = out                    # 第 8 项的热键结论也从这里读
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if p.returncode != 0:
        return 'FAIL', '浮窗自检退出码 %s: %s' % (p.returncode, _clip(' | '.join(lines[-2:]), 110))
    if not os.path.exists(png) or os.path.getsize(png) < 5000:
        return 'FAIL', '进程正常退出（%.1fs）但没生成截图，自检路径可能变了' % dt
    w, h = _png_size(png)
    m = re.search(r'\[selftest\] \S+\.png\s+(\d+x\d+) \| 窗口 (\d+x\d+) \| 展开=(\S+)', out)
    detail = '浮窗渲染 + 截图 OK：%s %dx%d  %.1fKB  %.1fs' % (
        os.path.basename(png), w, h, os.path.getsize(png) / 1024.0, dt)
    if m:
        detail += '\n自检窗口 %s 展开=%s（截图落在 %s，没覆盖 ui/.selftest.png）' % (
            m.group(2), m.group(3), os.path.relpath(os.path.dirname(png), ROOT))
    else:
        detail += '\n跑的是 ui/app.py 的临时副本，截图在 %s（没覆盖 ui/.selftest.png）' % os.path.relpath(png, ROOT)
    return 'PASS', detail


def check_library():
    """启动前的选库窗口（ui/library.py）。

    双击「启动提词器.bat」第一眼看到的就是它 —— 它崩了，用户看到的是"什么都没发生"
    （旧流程会直接起后端，新流程卡在选库这一步），所以单独钉一条。
    """
    if FAST:
        return 'SKIP', '--fast 跳过（起 Qt 进程约 4-6 秒，和浮窗自检同一量级）'
    lib_py = os.path.join(UI_DIR, 'library.py')
    if not os.path.exists(lib_py):
        return 'SKIP', 'ui/library.py 不存在（启动会退回「直接用当前库」的老流程）'
    if '--selftest' not in open(lib_py, encoding='utf-8', errors='replace').read():
        return 'SKIP', 'ui/library.py 里没有 --selftest 入口，外部没法验证，不瞎猜'

    work = os.path.join(WORK_DIR, 'library_selftest')
    os.makedirs(work, exist_ok=True)
    copy = os.path.join(work, 'library_selftest.py')
    shutil.copy2(lib_py, copy)        # 跑副本：截图落在 .tmp，不覆盖 ui/.selftest_*.png
    # 截图名以点开头（和 ui/app.py 的惯例一致，免得被 git 追踪）——
    # glob 的 * 匹配不到前导点，这里必须点名删，否则会拿上一轮的旧图冒充。
    shots = [os.path.join(work, '.selftest_library.png'),
             os.path.join(work, '.selftest_newlib.png')]
    for old in shots:
        try:
            os.remove(old)
        except OSError:
            pass
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, '-X', 'utf8', copy, '--selftest'],
                           cwd=ROOT, env=env, capture_output=True, timeout=UI_TIMEOUT,
                           encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        return 'FAIL', '选库窗口 %.0f 秒没退出（Qt 卡死/没有桌面会话？）' % UI_TIMEOUT
    dt = time.time() - t0
    out = (p.stdout or '') + (p.stderr or '')
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if p.returncode != 0:
        return 'FAIL', '选库窗口自检退出码 %s: %s' % (p.returncode, _clip(' | '.join(lines[-2:]), 110))
    pngs = [p for p in shots if os.path.exists(p)]
    if not pngs:
        return 'FAIL', '进程正常退出（%.1fs）但没生成截图，自检路径可能变了' % dt
    m = re.search(r'\[selftest\] 库 (\d+) 个', out)
    return 'PASS', '选库窗口渲染 OK：列出 %s 个知识库，截图 %s（%.1fs）\n截图在 %s' % (
        m.group(1) if m else '?', ', '.join(os.path.basename(x) for x in pngs), dt,
        os.path.relpath(work, ROOT))


# ── 8. 全局快捷键 ─────────────────────────────────────────────────────
def check_hotkey():
    app_py = os.path.join(UI_DIR, 'app.py')
    if not os.path.exists(app_py):
        return 'SKIP', 'ui/app.py 不存在，无从判断'
    src = open(app_py, encoding='utf-8', errors='replace').read()
    # 只认真正的调用（半角括号），注释里提一句 RegisterHotKey 不算
    if not re.search(r'RegisterHotKey\s*\(', src):
        return 'SKIP', 'ui/app.py 里找不到 RegisterHotKey( ) 调用，全局热键有没有注册上无从判断，也不瞎猜。\n' + \
            '（哪天 ui/app.py 真做了全局热键注册，本项会自动从「跳过」变成实测结论）'
    out = CTX.get('ui_out') or ''
    regs = re.findall(r'\[selftest\] 全局注册\s+(\S+)\s*=\s*(\S+)', out)
    taps = re.findall(r'\[selftest\] (Ctrl\+Shift\+\w) -> (\S+) = (\S+)', out)
    if regs:
        bad = [n for n, v in regs if v != 'True']
        if not bad:
            detail = 'ui/app.py 自己注册的 %d 个全局热键全部成功：%s（浮窗自检实测）' % (
                len(regs), '  '.join(n for n, _ in regs))
            problems = _tap_problems(taps)
            if problems:
                return 'WARN', detail + '\n但热键派发不对: ' + '；'.join(problems)
            seq = _tap_summary(taps)
            return 'PASS', detail + ('\nWM_HOTKEY 派发也通: %s' % seq if seq else '')
        # app.py 自己会打 [hotkey] xxx 注册失败→…(winerr=N)，直接拿它的错误码定性
        werrs = re.findall(r'\[hotkey\] (\S+)\s+.*?winerr=(-?\d+)', out)
        occ = [n for n, e in werrs if e == '1409']
        others = _other_ui_running()
        who = ('检测到另有浮窗进程在跑（PID %s）' % ','.join(others)) if others else \
            '没抓到别的提词器进程（占用者可能是别的软件，也可能只是某个浮窗刚好在这几秒里开着）'
        if occ:
            return 'WARN', '%d/%d 个热键没注册上：%s（winerr=1409=已被占用；%s）\n' % (
                len(occ), len(regs), ' '.join(occ), who) + \
                '没注册上的会退化成窗口内快捷键（得先点一下浮窗才管用），面试里基本等于没有。\n' + \
                '面试前先关掉占着它的程序，或把 ui/app.py 里 HOTKEYS 表的那一个键换成别的组合'
        free, err = _try_register()
        if not free and err == 1409:
            return 'WARN', '热键 %s 注册失败，本进程复测也是 1409 已被占用——%s' % (' '.join(bad), who)
        return 'FAIL', '全局热键注册失败: %s\n本进程复测这组键是空的，说明是 app.py 的注册环节本身有问题，热键按了不会有反应' % \
            ' '.join('%s=%s' % t for t in regs)

    # --fast 没跑浮窗自检，读不到 app.py 自己的注册结果，只能测"这组键在系统里能不能注册"
    free, err = _try_register()
    if free:
        return 'SKIP', 'ui/app.py 确实调了 RegisterHotKey，但 --fast 跳过了浮窗自检，读不到它的注册结果，不瞎猜。\n' + \
            '本进程实测 Ctrl+Shift+P 可以注册（没被别的软件占）；要确证跑一次不带 --fast 的默认模式'
    if err == 1409:
        return 'WARN', 'Ctrl+Shift+P 已被占用（1409）——多半是提词器浮窗自己已经开着了；若没开浮窗就是被别的软件抢了'
    return 'FAIL', 'Ctrl+Shift+P 注册失败，GetLastError=%d' % err


def _try_register():
    """本进程试注册一次 Ctrl+Shift+P，返回 (是否成功, GetLastError)。成功会立刻注销。"""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, VK_P = 0x0002, 0x0004, 0x4000, 0x50
    HK_ID = 0x0B0B
    if user32.RegisterHotKey(None, HK_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_P):
        user32.UnregisterHotKey(None, HK_ID)
        return True, 0
    return False, ctypes.get_last_error()


def _other_ui_running():
    """有没有别的 python 进程正在跑提词器浮窗——热键被占用的头号原因。"""
    try:
        cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'app[.]py' } | "
               "Select-Object -First 5 -ExpandProperty ProcessId")
        out = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True,
                             encoding='utf-8', errors='replace', timeout=10).stdout or ''
        return [x for x in out.split() if x.isdigit() and int(x) != os.getpid()]
    except Exception:
        return []


def _tap_problems(taps):
    """自检里直接投递 WM_HOTKEY 跑了一遍真派发路径，核对切换结果对不对。"""
    seq = {}
    for name, field, val in taps:
        seq.setdefault(name, []).append(val)
    bad = []
    h = seq.get('Ctrl+Shift+H')
    if h and h[:2] != ['False', 'True']:
        bad.append('Ctrl+Shift+H 切换显隐 = %s（应为 False→True）' % h[:2])
    p = seq.get('Ctrl+Shift+P')
    if p and p[:2] != ['True', 'False']:
        bad.append('Ctrl+Shift+P 切换暂停 = %s（应为 True→False）' % p[:2])
    d = seq.get('Ctrl+Shift+D')
    if d and d[0] != 'True':
        bad.append('Ctrl+Shift+D 深答区没弹出来 = %s' % d[0])
    return bad


def _tap_summary(taps):
    return ' '.join('%s%s=%s' % (n.replace('Ctrl+Shift+', ''), f, v) for n, f, v in taps[:6])


CHECKS = [
    ('回环采集设备存在', check_loopback_device),
    ('当前有声音在放', check_sound_playing),
    ('ASR 模型加载+识别', check_asr),
    ('BM25 检索索引', check_retrieval),
    ('专名词表体检', check_lexicon),
    ('换说法扩展词', check_expand),
    ('题库新鲜度', check_bank_fresh),
    ('DeepSeek 快答线', check_deepseek),
    ('端口 8765 占用', check_port),
    ('浮窗启动 + 截图', check_ui),
    ('选库窗口 + 截图', check_library),
    ('全局快捷键注册', check_hotkey),
]


def main():
    if '--help' in sys.argv[1:] or '-h' in sys.argv[1:]:
        print(__doc__)
        return 0
    os.makedirs(WORK_DIR, exist_ok=True)
    t_start = time.time()
    banner()
    for i, (name, fn) in enumerate(CHECKS, 1):
        t0 = time.time()
        try:
            status, detail = fn()
        except Exception as e:
            status, detail = 'FAIL', '这一项自己抛异常了: %s: %s' % (type(e).__name__, e)
            import traceback
            CTX.setdefault('tb', []).append(traceback.format_exc())
        cost = time.time() - t0
        ROWS.append((i, name, status, cost, detail))
        row(i, name, status, cost, detail)

    total = time.time() - t_start
    hr('=')
    n_pass = sum(1 for r in ROWS if r[2] == 'PASS')
    n_fail = sum(1 for r in ROWS if r[2] == 'FAIL')
    n_warn = sum(1 for r in ROWS if r[2] == 'WARN')
    n_skip = sum(1 for r in ROWS if r[2] == 'SKIP')
    print('  合计 %d 项: PASS %d   FAIL %d   WARN %d   SKIP %d     总耗时 %.1fs' % (
        len(ROWS), n_pass, n_fail, n_warn, n_skip, total), flush=True)
    if FAST:
        print('  --fast 目标 ≤20s，本次 %.1fs %s' % (total, '（达标）' if total <= 20 else '（超了，机器被占满？）'), flush=True)
    print('  面试前基准: %s' % BASELINE_HINT, flush=True)
    for i, name, status, cost, detail in ROWS:
        if status in ('FAIL', 'WARN', 'SKIP'):
            print('  [%s] %s: %s' % (status, name, detail.split('\n')[0]), flush=True)
    if n_fail:
        print('  >>> 有 %d 项 FAIL，先修好再上面试。' % n_fail, flush=True)
    elif n_warn:
        print('  >>> 没有 FAIL，但有 %d 项 WARN，扫一眼上面的说明。' % n_warn, flush=True)
    else:
        print('  >>> 全绿，可以放心上面试。', flush=True)
    hr('=')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
