# -*- coding: utf-8 -*-
"""提词器后端:回环采集 -> VAD -> ASR -> 快答线 -> (可选)深答线,全部通过 WebSocket 推给 UI。

启动:  python server/main.py
UI:    ws://127.0.0.1:<端口>/ws    端口写在 .runtime_port（8765 被占会自动往后顺延）

这一轮补的健壮性(都是"面试当场会咬人"的):
  1. 每个事件带 qid —— 面试官连问时,上一题的流不会再混进这一题
  2. 快答线独占超时 + 首字看门狗 —— 网络卡了会明说,而不是永远停在半句
  3. 采集循环可自愈 —— 插拔耳机/切默认设备后自动重连,不再"整场静默"
  4. 8765 被占自动顺延 —— 不再"后端静默退出、浮窗永远未连接"
  5. 会话落盘 logs/session_*.jsonl —— 面试后复盘和新测试集都从这来
"""
import os
import re
import sys
import json
import time
# 尽可能早地记下进程启动时刻：启动耗时的真相要从这里算起。
# 之前只从 boot() 开算，漏掉了 import 与 uvicorn 绑定端口的那一段。
_T_PROC = time.time()
import glob
import asyncio
import socket
import threading
import queue
import traceback
import subprocess

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings            # 统一配置：环境变量 > config/settings.json > 默认值
from version import __version__   # 版本号只此一处（server/version.py）

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from asr_engine import (LoopbackCapture, EnergyVAD, SenseVoiceASR, SR,
                        calibrate_threshold, default_speaker_name)

# 分段计时第 1 段：解释器启动 + 全部依赖 import（下一段是 uvicorn 起来之前）。
try:
    print('[boot] import 完成     %5.1fs' % (time.time() - _T_PROC), flush=True)
except Exception:
    pass

ROOT = settings.REPO_ROOT
MODEL_DIR = settings.model_dir()     # 默认 <仓库根>/models/SenseVoiceSmall-onnx，**必须纯 ASCII**
HOST = '127.0.0.1'
PORTS = settings.port_list()         # 默认 8765 起，被占自动顺延（见 __main__ 里的绑定逻辑）
PORT_FILE = os.path.join(ROOT, '.runtime_port')    # UI 读它拿端口
LOG_DIR = os.path.join(ROOT, 'logs')
TMP_DIR = os.path.join(ROOT, '.tmp')
DEVICE_CHECK_S = 5.0        # 每 5 秒看一眼默认播放设备有没有变
MAX_MERGE_S = 30.0          # 拼接后的音频上限：一句话再长也不该拖过 30 秒

# ── 半句判定 ────────────────────────────────────────────────────────────
# 面试官常这样说："那你这个项目 …… 对吧，但是我想知道的是" —— 中间换气就够判停。
# 旧版拿前半句直接发答案，第二段来了再顶掉一次，屏幕上的答案就在乱闪。
# 宁可少判（判错只是晚一轮），也别把完整问题扣住不发。
_HALF_TAIL = re.compile(
    r'(然后|但是|但|因为|所以|如果|假如|而且|不过|就是|那个|这个|以及|还有|或者|还是'
    r'|不像|关于|对于|为了|按照|根据|我们|你们|他们|我|你|他|她|的|是|和|跟|对|把|在'
    r'|有|会|能|要|想|说|讲|做|用|给|让|被|从|到|就|都|也|还|再|很|挺|比较)$')
_Q_MARK = re.compile(
    r'[？?]|吗|呢|什么|怎么|为啥|为什么|如何|哪|多少|几(个|种|台|层|条|套|步|次|年|家)'
    r'|是不是|有没有|会不会|能不能|对不对|介绍一下|讲讲|说说|聊一聊|谈一谈|做过|用过|接触过')


class MergeGate:
    """把 VAD 切出的一段段音频，按"面试官到底说完了没"合并成一次识别。

    为什么需要它：面试官句内换气、想一下再说下半句，常常超过 vad_end_ms。
    旧版一判停就发答案，第二段来了又发一次并把上一个顶掉 —— 用户看到的就是
    "答案乱变"。这里让判停后先攒着，窗口内只要又出声就取消待发，等它说完
    拼成一句再送。

    抽成纯逻辑类是为了能单测：采集循环要真实音频设备，测不了。
    """

    def __init__(self, merge_s=0.6, max_s=MAX_MERGE_S, sr=SR):
        self.merge_s = max(0.0, merge_s)
        self.max_s = max_s
        self.sr = sr
        self.pending = None
        self.deadline = None
        self.n_merged = 0

    def feed(self, st, audio, now):
        """喂一个 VAD 事件。返回"该送识别"的音频（numpy 数组），否则 None。

        st: 'end' = 判停；'speech' = 正在说；'idle' = 静音/没人说话。
        """
        if st == 'speech' and self.pending is not None:
            # 又出声了 → 上句没说完。用 inf 而不是 None：None 表示"没有窗口，
            # 立刻发"，会正好触发我们想避免的行为（单测抓到过）。
            self.deadline = float('inf')
        elif st == 'end':
            if self.pending is None:
                self.pending = audio
            else:
                gap = np.zeros(int(self.sr * 0.2), dtype='float32')
                self.pending = np.concatenate([self.pending, gap, audio])
                self.n_merged += 1
            self.deadline = (now + self.merge_s) if self.merge_s > 0 else None

        if self.pending is None:
            return None
        if (self.deadline is not None and now < self.deadline
                and len(self.pending) / self.sr < self.max_s):
            return None
        out, self.pending, self.deadline = self.pending, None, None
        return out

    def reset(self):
        self.pending, self.deadline = None, None


# 语气词：句尾挂在这些字上，多半是"话没说完先顿一下"（"…这些嘛""…不理想吧"）。
# "呢"不列进来 —— 它在 _Q_MARK 里，句尾的"呢"是问完了。
_SOFT_TAIL = re.compile(
    r'(然后|但是|但|因为|所以|如果|假如|而且|不过|就是|那个|这个|以及|还有|或者|还是'
    r'|不像|关于|对于|为了|按照|根据|我们|你们|他们|我|你|他|她|的|是|和|跟|对|把|在'
    r'|有|会|能|要|想|说|讲|做|用|给|让|被|从|到|就|都|也|还|再|很|挺|比较'
    r'|嘛|吧|啊|呀|哦)$')
_TAIL_WIN = 15


def _is_half_sentence(txt):
    """这句看着说完了吗？

    旧判据是"够长（≥40 字）就当说完，别让用户干等"。2026-09-14 第一次实机演练
    （真实 HR 面全场回放）证明它会**把场景题的前半段当成完整问题**发出去：
    HR 说"我们脚本给到你之前是一个 A 的版本……然后就做算法调度这些嘛"，停一下
    再补"但是你的代码已经写死了，你会怎么去解决这个问题呢" —— 前半段够长，
    被立刻发出并答掉，真正的问法来的时候主题已经被占了。

    新判据只看**尾巴**：
      · 最后 15 个字里有疑问信号（怎么/吗/哪…）→ 说完了，立刻发
      · 剥掉尾部标点后短于 12 字 → 半句
      · 剥掉尾部标点后结尾挂在连词/助词/语气词上 → 半句
    判成半句 → 先进 hold，最多再等 max_hold_s 秒；面试官接着说就拼起来一起送。
    代价：少数"以语气词收尾的完整问题"会多等几秒（实测 42 条里 14 条翻转，
    其中约一半确实是半句）。
    """
    t = (txt or '').strip()
    if not t:
        return True
    core = t.rstrip('。！？!?；;…，,、 ')
    if _Q_MARK.search(core[-_TAIL_WIN:]):   # 尾巴上已经问到点子上了
        return False
    if len(core) < 12:
        return True
    return bool(_SOFT_TAIL.search(core))
KEEP_WAV = 20               # .tmp 里只留最近 20 段录音
DEEP_TIMEOUT = 90           # 原 240s:面试里没人等得起 4 分钟
CAPTURE_BACKOFF_INIT = 1.0  # 采集重连退避起点（秒）—— 故障注入测试会把它调小

app = FastAPI()
CLIENTS = set()
EVENTS = queue.Queue()          # 采集线程 -> 事件循环
STATE = {'paused': False, 'listening': False, 'asr': None, 'ready': False,
         'recent': [], 'qid': 0, 'first_seen': {}, 'lock': threading.Lock(),
         'port': None, 'device': '', 'audio_restarts': 0, 'vad': 0.006,
         'session': time.strftime('%Y%m%d_%H%M%S')}


def emit(ev, qid=None):
    """所有事件都带 qid —— UI 只认当前 qid,过期的事件直接丢。"""
    if qid is not None:
        ev['qid'] = qid
    EVENTS.put(ev)


def stale(qid):
    return qid != STATE['qid']


def crash(tag, exc):
    """线程里抛的异常不会打到控制台,写文件,面试后能查。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, 'backend_error.log'), 'a', encoding='utf-8') as f:
            f.write('[%s] %s: %s\n%s\n' % (time.strftime('%H:%M:%S'), tag, exc, traceback.format_exc()))
    except Exception:
        pass


def log_rec(rec):
    """会话落盘:面试后 `tools/session_report.py` 直接读它出复盘。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        rec['ts'] = time.strftime('%Y-%m-%d %H:%M:%S')
        rec['session'] = STATE['session']
        with open(os.path.join(LOG_DIR, 'session_%s.jsonl' % STATE['session']), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass


def cleanup_tmp():
    """live_*.wav 每段约 180KB 且只增不减 —— 面试官的声音不该一直躺在磁盘上。"""
    try:
        os.makedirs(TMP_DIR, exist_ok=True)
        fs = sorted(glob.glob(os.path.join(TMP_DIR, 'live_*.wav')), key=os.path.getmtime, reverse=True)
        for f in fs[KEEP_WAV:]:
            os.remove(f)
    except Exception:
        pass


def bind_port(port):
    """真的把端口绑下来。绑不上返回 None。

    注意**不要**设 SO_REUSEADDR：Windows 上设了之后两个进程能同时 bind 同一个端口，
    两边都以为自己是唯一的（实测就出过两个后端抢 8765、UI 随机连到其中一个的事故）。
    不设就是默认的排他语义：第二个 bind 直接 OSError。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, port))
    except OSError:
        s.close()
        return None
    s.listen(64)
    return s


def free_port():
    """8765 被别的程序占了就顺延 —— 旧版直接 uvicorn.run(8765) 抛异常退出。"""
    for p in PORTS:
        s = bind_port(p)
        if s:
            s.close()
            return p
    return None


def teleprompter_on(port, timeout=1.5):
    """这个端口上是不是**已经有一个提词器后端**在跑。"""
    import urllib.request
    try:
        with urllib.request.urlopen('http://%s:%d/' % (HOST, port), timeout=timeout) as r:
            body = r.read(600).decode('utf-8', 'replace')
        return r.status == 200 and ('提词器' in body or 'teleprompter' in body.lower())
    except Exception:
        return False


# netstat 的 LISTENING 行，例如：  TCP    127.0.0.1:8765    0.0.0.0:0    LISTENING
_LISTEN_RE = re.compile(r'^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING', re.I)


def _listening_ports():
    """一次 netstat 拿到本机所有 LISTENING 端口；拿不到返回 None（调用方退回逐个探测）。

    为什么需要它：Windows 上连一个**没人监听**的本地端口不会立刻被拒绝，而是等到超时
    —— 实测裸 socket.connect 也要等满 1.5s（是防火墙丢包，不是 RST）。
    候选端口有 5 个（8765~8769），running_backend() 每个都发一次 HTTP，
    光白等就是 7.5 秒 —— 这正是「后端就绪 21.7 秒」里最大的一块：
    实测拆解 = import 1.0 + 空等 7.5 + 模型 4.0 + 预热识别 3.8 + 建索引 1.0 + 答题预热 3.7。
    netstat -an 只要 0.04 秒，比这划算得多。
    """
    try:
        out = subprocess.run(['netstat', '-an'], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=5).stdout or ''
    except Exception:
        return None                      # 拿不到就按老办法逐个探，行为不变
    live = set()
    for line in out.splitlines():
        m = _LISTEN_RE.match(line)
        if m:
            live.add(int(m.group(1)))
    return live


def running_backend():
    """扫一遍候选端口，返回已经在跑的提词器后端端口（没有则 None）。

    先用 netstat 过滤：只对真的有人在监听的端口发 HTTP。空端口连上去要等满超时，
    5 个候选端口就是 7.5 秒白等（见 _listening_ports）。
    netstat 拿不到时退回原来的逐个探测 —— 慢，但不会漏判。
    """
    live = _listening_ports()
    cand = PORTS if live is None else [p for p in PORTS if p in live]
    for p in cand:
        if teleprompter_on(p):
            return p
    return None


def _ps_pairs():
    """命令行像"提词器后端"的 python 进程：[(pid, ppid)]。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
          "Where-Object { $_.CommandLine -like '*server*main.py*' } | "
          "ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }")
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                             capture_output=True, text=True, timeout=15).stdout or ''
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        a = line.split()
        if len(a) == 2 and a[0].isdigit() and a[1].isdigit():
            rows.append((int(a[0]), int(a[1])))
    return rows


def other_backend_pids():
    r"""命令行里带 server\main.py 的其它后端进程 —— 包括**正在启动、还没开始监听**的。

    为什么不能只看端口：后端从进程起来到开始应答 HTTP 要十几秒（加载 ASR 模型）。
    两个启动器同时点的时候，这个窗口里彼此都看不见对方，于是双双起来抢回环音频
    （一句话识别两遍、屏幕上出两份答案）。实测就是这么翻的车。
    """
    me = os.getpid()
    rows = _ps_pairs()
    ppid = next((p for pid, p in rows if pid == me), 0) or os.getppid()
    kids = {pid for pid, p in rows if p == me}
    # 自己和自己的父/子进程都要放过。为什么：这个 .venv 的 python.exe 是**重定向器**，
    # 用它启动一次会得到"父进程=重定向器、子进程=真解释器"两个命令行一模一样的进程，
    # 不放过的话父子俩会互相把对方当成"第二个后端"，双双退出（实测就是这么翻的车）。
    return [pid for pid, _ in rows if pid not in ({me, ppid} | kids)]


async def broadcast(ev):
    dead = []
    data = json.dumps(ev, ensure_ascii=False)
    for ws in list(CLIENTS):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)


# ── 快答线（在线程里跑，避免阻塞事件循环）────────────────────────────
def _first_token_watch(qid):
    """首字看门狗:到点还没吐第一个字就明说,别让人干等一个卡住的流。"""
    import answer as A
    time.sleep(A.FIRST_TOKEN_WARN)
    if not stale(qid) and not STATE['first_seen'].get(qid):
        emit({'type': 'slow', 'seconds': A.FIRST_TOKEN_WARN,
              'message': '快答线还没出字（网络慢）：这题先自己讲，出字了会自动上屏'}, qid)


def _history():
    with STATE['lock']:
        return list(STATE['recent'][-8:])


def run_fast_answer(question, qid):
    import answer as A
    from router import LABEL
    qt = A.decide_qtype(question)
    if stale(qid):
        return
    emit({'type': 'answer_start', 'qtype': qt, 'qtypeLabel': LABEL[qt]}, qid)
    threading.Thread(target=_first_token_watch, args=(qid,), daemon=True).start()
    hist = _history()[:-1] or []          # 上文不含当前这句
    buf = []
    t0 = time.time()

    def on_delta(p):
        STATE['first_seen'][qid] = True
        if stale(qid):
            return
        buf.append(p)
        emit({'type': 'answer_delta', 'text': p}, qid)

    try:
        text, first, total, qt, hits = A.answer_stream(
            question, on_delta=on_delta, history=hist, should_stop=lambda: stale(qid))
    except Exception as e:
        crash('fast_answer', e)
        if stale(qid):
            return
        ans, src = A.offline_answer(question)      # 断网/接口挂了:题库里有就直接铺
        out = ('【离线题库】' + ans) if ans else ('<快答线失败: %s>' % e)
        emit({'type': 'answer_done', 'text': out, 'firstMs': 0, 'totalMs': round((time.time() - t0) * 1000),
              'sources': [src] if src else [], 'offline': True}, qid)
        log_rec({'event': 'answer', 'qid': qid, 'q': question, 'qtype': qt, 'answer': out,
                 'firstMs': 0, 'totalMs': round((time.time() - t0) * 1000), 'sources': [src] if src else [],
                 'warns': [], 'offline': True, 'error': str(e)})
        return

    if stale(qid):          # 已经问到下一题了,这段答案不上屏(也不写进这一题的记录)
        log_rec({'event': 'answer_dropped', 'qid': qid, 'q': question, 'qtype': qt,
                 'answer': text[:200], 'reason': 'stale'})
        return

    text = ''.join(buf) or text
    gap = A.is_unknown_gap(question) if qt == 'gap' else []
    warns = A.check_answer(text, hits=hits, history=hist, question=question, gap_terms=gap)
    srcs = [s for _, s, _ in hits]
    emit({'type': 'answer_done', 'text': text, 'firstMs': round((first or 0) * 1000),
          'totalMs': round(total * 1000), 'sources': srcs, 'warns': warns}, qid)
    if warns:
        emit({'type': 'answer_warn', 'items': warns}, qid)
    log_rec({'event': 'answer', 'qid': qid, 'q': question, 'qtype': qt, 'answer': text,
             'firstMs': round((first or 0) * 1000), 'totalMs': round(total * 1000),
             'sources': srcs, 'warns': warns})
    # 追问预案:题库里同一个章节的问法 ≈ 面试官接下来可能接着问什么。本地算,不花钱
    try:
        import bank as B
        pre = B.followups(question)
        if pre and not stale(qid):
            emit({'type': 'preplan', 'items': pre}, qid)
    except Exception as e:
        crash('preplan', e)


def run_deep_answer(question, qid):
    import deep as D
    emit({'type': 'deep_start'}, qid)
    try:
        text, dt, code = D.ask_deep(question, timeout=DEEP_TIMEOUT)
        emit({'type': 'deep_done', 'text': text, 'seconds': round(dt, 1)}, qid)
    except Exception as e:
        crash('deep_answer', e)
        emit({'type': 'deep_done', 'text': '<深答线失败: %s>' % e, 'seconds': 0}, qid)


def ask(question, with_deep=False, force=False):
    """收到一个提问:立刻跑快答线,可选同时跑深答线。

    force=False 用于 ASR 自动来的句子:先过 router 的"非提问"过滤
    (真实一面 50 段面试官发言里有 6 段是调试设备/自言自语,
     旧版本会照样发一次大模型请求,白花钱还往屏幕上刷垃圾答案)。
    手动打字提问走 force=True,用户既然敲了就当他是真问。
    """
    from router import classify
    qt = classify(question)
    if qt == 'skip' and not force:
        emit({'type': 'filtered', 'text': question, 'ts': time.time()})
        log_rec({'event': 'filtered', 'q': question})
        return
    with STATE['lock']:
        STATE['qid'] += 1
        qid = STATE['qid']
        STATE['recent'].append(question)
        del STATE['recent'][:-20]          # 只留最近 20 句，别无限涨
    emit({'type': 'question', 'text': question, 'qtype': qt, 'ts': time.time()}, qid)
    threading.Thread(target=run_fast_answer, args=(question, qid), daemon=True).start()
    if with_deep:
        threading.Thread(target=run_deep_answer, args=(question, qid), daemon=True).start()


# ── 采集线程（可自愈）─────────────────────────────────────────────────
def capture_once(vad_thresh):
    """跑一次采集循环。设备掉了/换设备会抛异常，交给 capture_forever 重连。"""
    asr = STATE['asr']
    vad = EnergyVAD(thresh=vad_thresh, end_ms=int(settings.get('vad_end_ms', 600) or 600))
    cap = LoopbackCapture()
    STATE['device'] = cap.device_name
    STATE['listening'] = True
    emit({'type': 'status', 'stage': 'listening', 'device': cap.device_name,
          'vadThresh': round(vad_thresh, 4), 'restarts': STATE['audio_restarts']})
    # ── 合并窗口 ─────────────────────────────────────────────────────────
    # 面试官句内换气、想一下再说下半句，常常超过 vad_end_ms。旧版一判停就发答案，
    # 第二段来了又发一次并把上一个顶掉 —— 用户看到的就是"答案乱变"。
    # 现在：判停后先攒着，merge_s 内只要又出声就取消待发，等它说完拼成一句再送。
    merge_s = max(0.0, int(settings.get('vad_merge_ms', 600) or 0) / 1000.0)
    hold_on = bool(settings.get('hold_incomplete', True))
    max_hold_s = float(settings.get('max_hold_s', 4.0) or 4.0)
    if merge_s or hold_on:
        emit({'type': 'status', 'stage': 'merge_window',
              'mergeMs': int(merge_s * 1000), 'holdIncomplete': hold_on})
    gate = MergeGate(merge_s=merge_s)
    hold = {'text': '', 'at': 0.0}   # 文本层的"半句"暂存

    last_level = 0.0
    last_dev = time.time()
    for block in cap.frames():
        now = time.time()
        if now - last_dev > DEVICE_CHECK_S:
            last_dev = now
            cur = default_speaker_name()
            if cur and cap.device_name and cur != cap.device_name:
                raise RuntimeError('默认播放设备变成 %s（原 %s）' % (cur, cap.device_name))
        if now - last_level > 0.1:
            rms = float(np.sqrt((block.astype('float64') ** 2).mean()))
            emit({'type': 'level', 'rms': round(rms, 4)})
            last_level = now
        if STATE['paused']:
            vad.reset()
            gate.reset()
            continue

        st, audio = vad.feed(block)
        audio = gate.feed(st, audio, now)      # 合并窗口：没说完就返回 None

        # 半句存太久了：别再等，直接发（否则用户会觉得"我说了它没反应"）
        if hold['text'] and now - hold['at'] > max_hold_s:
            t, hold['text'] = hold['text'], ''
            emit({'type': 'asr', 'text': t, 'held': False, 'forced': True})
            ask(t)

        if audio is None:
            continue
        dur = len(audio) / SR
        t0 = time.time()
        txt, dt = asr.transcribe(audio)
        if not txt.strip():
            continue
        # 术语纠错要放在 extract() 之前 —— 纠正后的文本才该进上屏、检索和
        # "没准备过"的判定。实测英文术语识别率 35.7% -> 54.3%（35 术语 × 2 臂）。
        try:
            from termfix import correct as _fix_terms
            txt, _fx = _fix_terms(txt)
            if _fx:
                emit({'type': 'termfix', 'fixes': [[a, b] for a, b in _fx]})
        except Exception:
            pass
        try:
            from extract_q import extract
            txt = extract(txt)
        except Exception:
            pass

        if hold['text']:
            # 上一段是半句 → 拼起来当一句
            txt = hold['text'] + txt
        if hold_on and _is_half_sentence(txt):
            hold['text'] = txt
            if not hold.get('at'):
                hold['at'] = now
            emit({'type': 'asr', 'text': txt, 'dur': round(dur, 2),
                  'asrMs': round(dt * 1000), 'held': True, 'merged': gate.n_merged,
                  'latencyMs': round((time.time() - t0 + dt) * 1000)})
            continue
        hold['text'], hold['at'] = '', 0.0
        emit({'type': 'asr', 'text': txt, 'dur': round(dur, 2),
              'asrMs': round(dt * 1000), 'held': False, 'merged': gate.n_merged,
              'latencyMs': round((time.time() - t0 + dt) * 1000)})
        ask(txt)


def capture_forever():
    """采集永不"死掉就完事"：设备掉了自动重连，并把状态明说给 UI。

    旧版 capture_loop 抛一次异常就退出线程 —— 面试中插一下耳机，后半场就是静默的。
    """
    thresh = 0.006
    try:
        thresh, floor = calibrate_threshold(LoopbackCapture())
        STATE['vad'] = thresh
        emit({'type': 'status', 'stage': 'vad_calibrated', 'thresh': round(thresh, 4), 'floor': round(floor, 5)})
    except Exception as e:
        crash('vad_calibrate', e)
    backoff = CAPTURE_BACKOFF_INIT
    while True:
        t_start = time.time()
        try:
            capture_once(thresh)
            return                      # 正常退出（目前只有 stop 才会走到这）
        except Exception as e:
            STATE['listening'] = False
            STATE['audio_restarts'] += 1
            crash('capture', e)
            emit({'type': 'status', 'stage': 'audio_lost',
                  'message': '%s: %s' % (type(e).__name__, e),
                  'restarts': STATE['audio_restarts']})
            log_rec({'event': 'audio_lost', 'error': str(e), 'restarts': STATE['audio_restarts']})
            if time.time() - t_start > 5:
                backoff = CAPTURE_BACKOFF_INIT      # 连上过一会儿了，退避重置
            time.sleep(backoff)
            backoff = min(backoff * 2, 15.0)
            emit({'type': 'status', 'stage': 'audio_reconnecting', 'restarts': STATE['audio_restarts']})


def boot():
    emit({'type': 'status', 'stage': 'loading_model'})

    # 分段计时：启动慢的时候不用猜是哪一段。只打日志，不影响任何流程。
    # 加这个是因为「后端就绪 21.6 秒」长期拆不出来 —— 已知模型 4.6s、预热 4.5s、
    # 建索引 1s，剩下的十几秒不知道在哪。
    _t0 = _T_PROC
    def _boot(label):
        try:
            print('[boot] %-12s %5.1fs' % (label, time.time() - _t0), flush=True)
        except Exception:
            pass                      # 无控制台的 exe 里 stdout 是 None

    _boot('import+uvicorn')      # 这一行之前的都算解释器启动与模块导入
    cleanup_tmp()
    _boot('cleanup')
    asr = SenseVoiceASR(MODEL_DIR)
    _boot('模型加载')
    emit({'type': 'status', 'stage': 'warming_up'})
    w = asr.warmup()
    _boot('预热识别')
    STATE['asr'] = asr
    emit({'type': 'status', 'stage': 'indexing'})
    from knowledge import build
    _, chunks = build()
    _boot('建索引')
    # 顺手把答题链路的冷启动开销挪到启动阶段：词表（jieba posseg）和题库 BM25
    # 都是首次调用才加载。不预热的话，本场第一题要多花约 1 秒 —— 实测会让
    # 首字看门狗误报一次"网络慢"。
    emit({'type': 'status', 'stage': 'warming_answer'})
    try:
        import answer as A
        A.is_unknown_gap('预热一句')
        A.decide_qtype('预热一句')
        import bank as B
        B.lookup('预热一句')
        A.warmup()                      # DNS + TLS 握手也挪到启动阶段（首题省 2~3 秒）
    except Exception as e:
        crash('warm_answer', e)
    _boot('答题链路预热')
    STATE['ready'] = True
    _boot('★ 就绪（/healthz 开始报 ready）')
    emit({'type': 'status', 'stage': 'ready', 'loadS': round(asr.load_s, 1),
          'warmupS': round(w, 1), 'chunks': len(chunks), 'port': STATE['port']})
    capture_forever()


async def pump():
    while True:
        try:
            ev = EVENTS.get_nowait()
            await broadcast(ev)
        except queue.Empty:
            await asyncio.sleep(0.02)


@app.on_event('startup')
async def on_start():
    asyncio.create_task(pump())
    threading.Thread(target=boot, daemon=True).start()


# 浏览器版界面（可选）：目录不存在时不要 mount —— StaticFiles 会在 import 时就抛
# "Directory ... does not exist"，后端连启动都谈不上，而桌面浮窗走的是 WebSocket，不需要它。
UI_DIR = os.path.join(ROOT, 'app')
if os.path.isdir(UI_DIR):
    app.mount('/static', StaticFiles(directory=UI_DIR), name='static')


@app.get('/')
async def ui_index():
    index = os.path.join(UI_DIR, 'index.html')
    if not os.path.isfile(index):
        # 这条 HTTP 应答还兼着"这个端口上是不是提词器后端"的探针功能（启动器/自检都靠它），
        # 所以缺界面时也要返回 200 + 含"提词器"的正文。
        return HTMLResponse(
            '<h3>提词器后端在跑</h3>'
            '<p>没找到浏览器版界面 %s（这个界面是可选的）。桌面浮窗直接连 '
            'ws://127.0.0.1:%s/ws。</p>' % (index, STATE['port']))
    return FileResponse(index)


@app.get('/healthz')
async def healthz():
    """启动器靠它判断"真的能用了" —— HTTP 能应答 ≠ 模型加载完了。

    实测：启动器原来只看首页能不能打开就宣布就绪，结果第一题是在后端还在
    预热（加载 ASR + 建索引）的时候问的，墙钟出字时间从 1.2 秒涨到 3.8 秒。
    """
    return {'ready': bool(STATE['ready']), 'port': STATE['port'],
            'version': __version__,      # 启动器/浮窗/自检都靠它报版本
            'device': STATE.get('device', ''), 'audio_restarts': STATE['audio_restarts']}


@app.websocket('/ws')
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    CLIENTS.add(ws)
    await ws.send_text(json.dumps({'type': 'hello', 'ready': STATE['ready'],
                                   'port': STATE['port'], 'qid': STATE['qid']}, ensure_ascii=False))
    # 补发当前状态(UI 可能在后端就绪之后才连上来)
    if STATE['ready']:
        await ws.send_text(json.dumps(
            {'type': 'status', 'stage': 'paused' if STATE['paused'] else 'listening',
             'device': STATE.get('device', ''), 'vadThresh': round(STATE.get('vad', 0.006), 4)},
            ensure_ascii=False))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            cmd = msg.get('cmd')
            if cmd == 'pause':
                STATE['paused'] = True
                await broadcast({'type': 'status', 'stage': 'paused'})
            elif cmd == 'resume':
                STATE['paused'] = False
                await broadcast({'type': 'status', 'stage': 'listening'})
            elif cmd == 'ask':
                q = (msg.get('text') or '').strip()
                if q:
                    ask(q, with_deep=bool(msg.get('deep')), force=True)
            elif cmd == 'deep':
                q = (msg.get('text') or '').strip()
                if q:
                    with STATE['lock']:
                        qid = STATE['qid']
                    threading.Thread(target=run_deep_answer, args=(q, qid), daemon=True).start()
    except WebSocketDisconnect:
        pass
    finally:
        CLIENTS.discard(ws)


def _stop_hint():
    return ('要停掉它：Get-CimInstance Win32_Process | Where-Object '
            '{ $_.CommandLine -like "*server\\main.py*" } | ForEach-Object '
            '{ Stop-Process -Id $_.ProcessId -Force }')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')

    # ⓪ 模型目录先做 ASCII 校验 + 存在性检查。
    #    这两件事出问题时，boot() 是在后台线程里抛异常，只留下日志里一行 traceback，
    #    浮窗那头看到的是"永远连不上"，面试当天根本来不及查 —— 所以宁可在启动前直接拒绝。
    try:
        settings.ensure_ascii(MODEL_DIR, 'ASR 模型目录（model_dir）')
    except RuntimeError as e:
        print(e)
        log_rec({'event': 'startup_fail', 'error': str(e)})
        sys.exit(2)
    if not os.path.isdir(MODEL_DIR):
        print('ASR 模型目录不存在：%s' % MODEL_DIR)
        print('先下载模型（约 230MB，纯标准库实现）：python tools/download_model.py')
        print('或改 config/settings.json 的 model_dir，或设环境变量 TP_MODEL_DIR 指到已有的模型目录。')
        sys.exit(2)

    # ① 已经有一个在跑就别起第二个。两个后端会同时抢回环音频（同一句话识别两遍、
    #    屏幕上出两份答案），还会互相覆盖端口文件 —— 实测发生过。
    others = other_backend_pids()
    if others:
        print('已经有一个提词器后端进程在跑或正在启动（PID %s），不要起第二个。'
              % ', '.join(str(p) for p in others))
        print('（两个后端会同时录同一路音频、互相覆盖端口文件）')
        print(_stop_hint())
        sys.exit(3)
    old = running_backend()
    if old:
        print('127.0.0.1:%d 上已经有一个提词器后端在应答，不要起第二个。' % old)
        print(_stop_hint())
        sys.exit(3)

    # ② 先把端口真的绑下来，再写端口文件 —— 抢不到端口的进程不会覆盖它
    port = None
    for p in PORTS:
        sock = bind_port(p)
        if sock:
            port = p
            break
    if port is None:
        print('%d~%d 全被占用，先关掉占用的程序再启动。' % (PORTS[0], PORTS[-1]))
        sys.exit(2)

    # ③ 并发启动时：别人刚抢到更靠前的端口 = 我是多余的那个，主动退出
    for p in PORTS:
        if p >= port:
            break
        if teleprompter_on(p):
            print('检测到已有后端在 127.0.0.1:%d，我这个实例（%d）退出，避免两个实例并存。'
                  % (p, port))
            sock.close()
            sys.exit(3)

    STATE['port'] = port
    try:
        open(PORT_FILE, 'w', encoding='utf-8').write(str(port))
    except Exception as e:
        print('端口文件写不了（UI 会退回默认 8765）: %s' % e)
        log_rec({'event': 'port_file_error', 'error': str(e)})
    print('提词器后端启动: http://%s:%d   (WS: /ws)   端口已写入 %s' % (HOST, port, PORT_FILE))
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
    server.run(sockets=[sock])
