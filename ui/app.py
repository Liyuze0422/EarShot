# -*- coding: utf-8 -*-
"""面试提词器浮窗（PyQt6 原生）

为什么不用 Electron：本机 Electron 44 的 GPU/渲染子进程会崩(STATUS_BREAKPOINT)，
软件渲染也不行。PyQt 同进程渲染，最稳，且能自我截图验证。

要点：
  · 置顶：WindowStaysOnTopHint + Tool，并定时 SetWindowPos 保活（压过全屏会议窗口）
  · 防共享：SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE=0x11)
  · 全局热键：RegisterHotKey（不依赖窗口焦点），统一在 HOTKEYS 表里注册
  · 显示模式：默认「只看核心句」= 20px 核心大字 + 状态栏，展开区/深答区折叠。
    真实面试里注视时间是秒级的，多显示一段就是负担；Ctrl+Shift+E 才展开。
  · 核心句超过 MAX_CORE_CHARS(40) 字：左侧黄线 + 黄色角标 + 字号自适应缩小，
    一眼就知道「这行太长，别照念」。
"""
import os, sys, json, ctypes, asyncio, threading, time
from ctypes import wintypes

from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QTimer, QPoint,
                          QAbstractNativeEventFilter)
from PyQt6.QtGui import (QFont, QColor, QPainter, QPainterPath, QFontMetrics,
                         QKeySequence, QShortcut)
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QTextEdit, QLineEdit, QPushButton, QFrame)

WS_URL = 'ws://127.0.0.1:8765/ws'
# 后端 8765 被占时会自动顺延，并把实际端口写在这里。UI 每次重连都重新读一遍，
# 后端换端口重启也能自动跟上（旧版写死 8765，端口一冲突就永远"未连接"）。
PORT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.runtime_port')
# 浮窗自己的 PID：启动器靠它判断"是不是已经开了一个" —— 两个浮窗同时在跑
# 会出现两份一样的画面，还会莫名其妙抢焦点。
PID_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.ui_pid')


def write_pid():
    try:
        open(PID_FILE, 'w', encoding='utf-8').write(str(os.getpid()))
    except Exception:
        pass


def clear_pid():
    try:
        if os.path.exists(PID_FILE) and open(PID_FILE, encoding='utf-8').read().strip() == str(os.getpid()):
            os.remove(PID_FILE)
    except Exception:
        pass
SELFTEST = '--selftest' in sys.argv


def ws_url():
    try:
        p = int(open(PORT_FILE, encoding='utf-8').read().strip())
        if 1 < p < 65536:
            return 'ws://127.0.0.1:%d/ws' % p
    except Exception:
        pass
    return WS_URL

user32 = ctypes.windll.user32
# 单独再拿一个带 last-error 的句柄：热键注册失败时要能说出原因（1409=已被别的程序占用）
try:
    user32_x = ctypes.WinDLL('user32', use_last_error=True)
except Exception:
    user32_x = user32
WDA_EXCLUDEFROMCAPTURE = 0x11
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010

# ── 全局热键（RegisterHotKey，不依赖窗口焦点）────────────────────────
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
HK_MODS = MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT

# 唯一的注册入口：(id, 虚拟键, 显示名, 处理函数名, 说明)
HOTKEYS = [
    (1, ord('H'), 'Ctrl+Shift+H', 'toggle_visibility', '隐藏 / 显示浮窗'),
    (2, ord('P'), 'Ctrl+Shift+P', 'toggle_pause',      '暂停 / 恢复转写'),
    (3, ord('D'), 'Ctrl+Shift+D', 'run_deep',          '深度回答'),
    (4, ord('E'), 'Ctrl+Shift+E', 'toggle_expand',     '展开 / 收起'),
]

# 首选键被别的软件占用时自动换下一个。
# 实测本机 Ctrl+Shift+D 被占（RegisterHotKey 返回 winerr=1409），
# 而全局热键一旦退化成"窗口内快捷键"，面试时（浮窗没有焦点）就等于没有。
# 所以这里给每个热键排一串候选，注册到哪个用哪个，并把实际生效的键显示在底部提示行。
HOTKEY_ALTS = {
    3: [('Ctrl+Shift+J', ord('J')), ('Ctrl+Alt+D', ord('D')), ('Ctrl+Shift+F10', 0x79)],
    4: [('Ctrl+Shift+K', ord('K')), ('Ctrl+Shift+F9', 0x78)],
    1: [('Ctrl+Shift+U', ord('U'))],
    2: [('Ctrl+Shift+L', ord('L'))],
}

MAX_CORE_CHARS = 40   # 核心句超过这个字数，一秒内念不完（实测这类合规率只有 79%）
CORE_FONT_PX = 20     # 核心大字基准字号：只看核心句模式下唯一的大字
CORE_FONT_MIN = 12    # 超长时字号缩小的下限


class HotkeyFilter(QAbstractNativeEventFilter):
    """RegisterHotKey 注册的热键不走 Qt 按键事件，只能在原生消息里捞 WM_HOTKEY。

    注意：同一条 WM_HOTKEY，Qt 会往原生事件过滤器里投两次（实测 message.time
    完全一样）。不去重的话按一下等于按两下，toggle 类热键会"看起来没反应"。
    """

    def __init__(self, on_hotkey):
        super().__init__()
        self._on_hotkey = on_hotkey
        self._last = None

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType == b'windows_generic_MSG':
                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY:
                    key = (int(msg.wParam), int(msg.time), int(msg.lParam))
                    if key != self._last:
                        self._last = key
                        self._on_hotkey(int(msg.wParam))
        except Exception:
            pass
        return False, 0


# ── WebSocket 客户端（在独立线程跑 asyncio）──────────────────────────
class WSClient(QThread):
    event = pyqtSignal(dict)
    status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.loop = None
        self.queue = asyncio.Queue() if False else None
        self._outbox = []
        self._stop = False

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        import websockets
        self.loop = asyncio.get_running_loop()
        while not self._stop:
            try:
                async with websockets.connect(ws_url(), ping_interval=20) as ws:
                    self.status.emit('connected')
                    sender = asyncio.create_task(self._sender(ws))
                    async for raw in ws:
                        try:
                            self.event.emit(json.loads(raw))
                        except Exception:
                            pass
                    sender.cancel()
            except Exception as e:
                self.status.emit('disconnected')
                await asyncio.sleep(1.5)

    async def _sender(self, ws):
        while True:
            if self._outbox:
                msg = self._outbox.pop(0)
                try:
                    await ws.send(json.dumps(msg, ensure_ascii=False))
                except Exception:
                    pass
            await asyncio.sleep(0.05)

    def send(self, obj):
        self._outbox.append(obj)


# ── 浮窗 ────────────────────────────────────────────────────────────
class Teleprompter(QWidget):
    def __init__(self):
        super().__init__()
        self.paused = False
        self.cur_q = ''
        self.cur_qid = None           # 当前题目号：只认这一题的事件（防串题）
        self.buf = ''
        self._drag = None
        # 显示模式：False = 只看核心句（默认）
        self.expanded = False
        self._deep_ready = False      # 深答区有内容，展开时才重新露出来
        self._full_h = 660            # 展开时的窗口高度
        self._mini_h = 200            # 折叠时的窗口高度
        self._core_px = CORE_FONT_PX
        self._core_text = ''
        self._core_long = False
        self._hotkey_ok = {}
        self._hotkey_last = {}
        self._fallback = []
        self._in_resize = False

        self.setWindowTitle('面试提词器')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.resize(580, 660)
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - 610, scr.top() + 50)
        self._build_ui()
        self._apply_style()
        self._set_core('—')
        self.set_expanded(False)      # 启动即「只看核心句」

        # 防共享：屏幕共享/录屏里看不见
        QTimer.singleShot(300, self._protect)
        # 保活置顶（压过全屏会议窗口）
        self._top_timer = QTimer(self)
        self._top_timer.timeout.connect(self._keep_top)
        self._top_timer.start(2000)

        # 全局热键：统一注册；注册不到的热键退化成窗口内快捷键，功能不丢
        self._hk_filter = HotkeyFilter(self._on_hotkey)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._hk_filter)
        QTimer.singleShot(0, self._register_hotkeys)

        self.ws = None
        if not os.environ.get('TP_NO_WS'):   # 自检用：不连服务端，截图不受真实事件干扰
            self.ws = WSClient()
            self.ws.event.connect(self.on_event)
            self.ws.status.connect(self.on_status)
            self.ws.start()

    # ── UI ──
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        bar = QHBoxLayout(); bar.setSpacing(8)
        self.dot = QLabel('●'); self.dot.setObjectName('dot')
        self.stage = QLabel('启动中…'); self.stage.setObjectName('stage')
        self.stats = QLabel(''); self.stats.setObjectName('stats')
        bar.addWidget(self.dot); bar.addWidget(self.stage); bar.addStretch(); bar.addWidget(self.stats)

        self.btn_expand = QPushButton('▸'); self.btn_expand.setObjectName('icon')
        self.btn_expand.setToolTip('展开/收起 (Ctrl+Shift+E)')
        self.btn_expand.setFixedSize(26, 24); self.btn_expand.clicked.connect(self.toggle_expand)
        bar.addWidget(self.btn_expand)
        for txt, tip, fn in [('⏸', '暂停/恢复 (Ctrl+Shift+P)', self.toggle_pause),
                             ('⟳', '深度回答 (Ctrl+Shift+D)', self.run_deep),
                             ('—', '最小化', self.showMinimized),
                             ('×', '退出', QApplication.quit)]:
            b = QPushButton(txt); b.setObjectName('icon'); b.setToolTip(tip)
            b.setFixedSize(26, 24); b.clicked.connect(fn); bar.addWidget(b)
        v.addLayout(bar)

        self.meter = QFrame(); self.meter.setObjectName('meter'); self.meter.setFixedHeight(3)
        v.addWidget(self.meter)

        self.q = QLabel('等待面试官提问…'); self.q.setObjectName('question'); self.q.setWordWrap(True)
        self.qbox = self._box('面试官', self.q)
        v.addWidget(self.qbox)

        self.core = QLabel('—'); self.core.setObjectName('core'); self.core.setWordWrap(True)
        self.qtype = QLabel(''); self.qtype.setObjectName('tag')
        self.overflow = QLabel(''); self.overflow.setObjectName('overflow'); self.overflow.hide()
        # 数字/经历对不上时贴一个红标：照念之前先看一眼
        self.warn = QLabel(''); self.warn.setObjectName('warn'); self.warn.hide()
        self.corebox = self._box('核心', self.core, (self.qtype, self.warn, self.overflow))
        v.addWidget(self.corebox)

        # 追问预案：贴在核心框下面，折叠状态下也看得见（面试官组织下一个问题时扫一眼）
        self.preplan = QLabel(''); self.preplan.setObjectName('plan')
        self.preplan.setWordWrap(True); self.preplan.hide()
        v.addWidget(self.preplan)

        self.expand = QTextEdit(); self.expand.setObjectName('expand'); self.expand.setReadOnly(True)
        self.expandbox = self._box('展开', self.expand)
        v.addWidget(self.expandbox, 1)

        self.deep = QTextEdit(); self.deep.setObjectName('deep'); self.deep.setReadOnly(True)
        self.deepbox = self._box('⟳ 深度补充', self.deep)
        self.deepbox.hide(); v.addWidget(self.deepbox, 1)

        self.manualrow = QWidget()
        mrow = QHBoxLayout(self.manualrow); mrow.setContentsMargins(0, 0, 0, 0); mrow.setSpacing(6)
        self.manual = QLineEdit(); self.manual.setPlaceholderText('手动提问，回车发送…')
        self.manual.returnPressed.connect(self.ask_manual)
        ask = QPushButton('问'); ask.setObjectName('ask'); ask.clicked.connect(self.ask_manual)
        mrow.addWidget(self.manual, 1); mrow.addWidget(ask)
        v.addWidget(self.manualrow)

        self.hint = QLabel(''); self.hint.setObjectName('hint')
        self.hint.setToolTip('Ctrl+Shift+E 展开/收起\nCtrl+Shift+H 隐藏/显示\n'
                             'Ctrl+Shift+P 暂停/恢复\nCtrl+Shift+D 深度回答')
        v.addWidget(self.hint)

    def _box(self, title, widget, tags=()):
        f = QFrame(); f.setObjectName('box')
        l = QVBoxLayout(f); l.setContentsMargins(10, 8, 10, 8); l.setSpacing(4)
        h = QHBoxLayout(); h.setSpacing(6)
        t = QLabel(title); t.setObjectName('label'); h.addWidget(t)
        for tag in tags: h.addWidget(tag)
        h.addStretch(); l.addLayout(h); l.addWidget(widget)
        return f

    def _apply_style(self):
        self.setStyleSheet('''
        QLabel#label   { color:#6d727c; font-size:11px; }
        QLabel#stage   { color:#9aa0aa; font-size:12px; }
        QLabel#stats   { color:#6d727c; font-size:11px; }
        QLabel#dot     { color:#666; font-size:13px; }
        QLabel#question{ color:#a8adb8; font-size:13px; }
        QLabel#tag     { color:#9dbaff; font-size:10px; background:rgba(90,140,255,0.22);
                         border-radius:8px; padding:1px 6px; }
        QLabel#overflow{ color:#f2c14e; font-size:10px; background:rgba(240,192,64,0.20);
                         border-radius:8px; padding:1px 6px; }
        QLabel#warn    { color:#ff9a9a; font-size:10px; background:rgba(224,85,85,0.20);
                         border-radius:8px; padding:1px 6px; }
        QLabel#plan    { color:#8f96a3; font-size:11px; }
        QLabel#hint    { color:#5f646e; font-size:10px; }
        QFrame#box     { background:rgba(255,255,255,0.045); border-radius:8px; }
        QFrame#meter   { background:rgba(255,255,255,0.08); max-height:3px; }
        QTextEdit#expand,QTextEdit#deep { background:transparent; border:0; color:#c9cdd6; font-size:14px; }
        QTextEdit#deep { color:#b9c6e8; font-size:13px; }
        QPushButton#icon { background:transparent; border:0; color:#9aa0aa; font-size:14px; }
        QPushButton#icon:hover { background:rgba(255,255,255,0.12); border-radius:5px; color:#fff; }
        QPushButton#ask  { background:rgba(90,140,255,0.75); border:0; color:#fff;
                           border-radius:6px; padding:6px 14px; }
        QLineEdit { background:rgba(255,255,255,0.07); border:1px solid rgba(255,255,255,0.12);
                    border-radius:6px; color:#e8e8ec; padding:6px 9px; font-size:13px; }
        ''')
        # 核心大字的字号随长度变，不能用固定 QSS，统一走 _apply_core_style()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(); path.addRoundedRect(0, 0, self.width(), self.height(), 12, 12)
        p.fillPath(path, QColor(18, 18, 22, 242))
        p.setPen(QColor(255, 255, 255, 28)); p.drawPath(path)

    # ── 置顶 / 防共享 ──
    def _protect(self):
        if os.environ.get('TP_NO_PROTECT'):
            return
        try:
            user32.SetWindowDisplayAffinity(wintypes.HWND(int(self.winId())), WDA_EXCLUDEFROMCAPTURE)
        except Exception as e:
            print('内容保护失败:', e)

    def _keep_top(self):
        try:
            user32.SetWindowPos(wintypes.HWND(int(self.winId())), wintypes.HWND(HWND_TOPMOST),
                                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    # ── 拖动 ──
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._in_resize:
            return
        self._in_resize = True
        try:
            self._render_question()
        finally:
            self._in_resize = False

    # ── 核心大字（默认唯一显示的内容）──
    def _set_core(self, text):
        """核心句 = 抬头一秒要念完的话。超过 MAX_CORE_CHARS 字：黄线 + 角标 + 缩字号。"""
        t = (text or '').strip() or '…'
        n = len(t)
        long_ = n > MAX_CORE_CHARS
        self._core_text = t
        self._core_long = long_
        self._core_px = self._core_font_px(n)
        self.core.setText(t)
        self._apply_core_style(self._core_px, long_)
        if long_:
            self.overflow.setText('⚠ 超长 %d 字 · 别照念' % n)
            self.overflow.show()
        else:
            self.overflow.hide()
        if not self.expanded:
            self._apply_window_height()   # 核心句换行后行数变了，窗口要跟着长高

    @staticmethod
    def _core_font_px(n):
        if n <= MAX_CORE_CHARS:
            return CORE_FONT_PX
        px = int(round(CORE_FONT_PX * ((float(MAX_CORE_CHARS) / n) ** 0.5)))
        return max(CORE_FONT_MIN, min(CORE_FONT_PX, px))

    def _apply_core_style(self, px, long_):
        # 左边框常驻（透明），超长才点亮成黄色，这样文字左边缘不会跳
        css = ('color:#ffffff; font-weight:600; font-size:%dpx; padding-left:8px; '
               'border-left:3px solid %s;'
               % (px, '#f0c040' if long_ else 'transparent'))
        if long_:
            css += ' background:rgba(240,192,64,0.10);'
        self.core.setStyleSheet(css)

    # ── 显示模式：只看核心句 / 展开 ──
    def set_expanded(self, on):
        on = bool(on)
        self.expanded = on
        self.expandbox.setVisible(on)
        self.deepbox.setVisible(on and self._deep_ready)
        self.manualrow.setVisible(on)
        if self.btn_expand is not None:
            self.btn_expand.setText('▾' if on else '▸')
        self._render_question()
        self._sync_hint()
        self._apply_window_height()

    def toggle_expand(self):
        if self.expanded:
            self._full_h = max(self.height(), 320)   # 收起前记住展开时的高度
        self.set_expanded(not self.expanded)

    def _apply_window_height(self):
        """折叠时窗口只留「核心大字 + 状态栏」的高度；核心句行数一变就重算，
        否则 QLabel 会被压扁（文字重叠、被裁掉半行）。"""
        lay = self.layout()
        if lay is None:
            return
        lay.invalidate(); lay.activate()
        need = lay.totalSizeHint().height() + 6      # QLabel 换行的 sizeHint 偏保守，留点余量
        if self.expanded:
            # 展开态维持原高度，只有内容真塞不下才长高
            floor_h = lay.minimumSize().height()
            self.resize(self.width(), max(320, self._full_h, min(900, floor_h)))
        else:
            h = max(150, min(420, need))
            self._mini_h = h
            self.resize(self.width(), h)

    def _sync_hint(self):
        hk = getattr(self, '_hotkey_name', {}) or {}
        short = lambda n: n.replace('Ctrl+Shift+', '').replace('Ctrl+Alt+', 'C-A+')
        self.hint.setText('%s %s · %s 隐藏 · %s 暂停 · %s 深答'
                          % (short(hk.get(4, 'Ctrl+Shift+E')),
                             '收起' if self.expanded else '展开',
                             short(hk.get(1, 'Ctrl+Shift+H')),
                             short(hk.get(2, 'Ctrl+Shift+P')),
                             short(hk.get(3, 'Ctrl+Shift+D'))))

    def _render_question(self):
        """折叠时面试官原话压成一行（省略号截断），展开时才全文换行。"""
        txt = self.cur_q or '等待面试官提问…'
        if self.expanded:
            self.q.setWordWrap(True)
            self.q.setText(txt)
            return
        self.q.setWordWrap(False)
        w = self.qbox.width() if self.qbox is not None else 0
        if w < 120:
            w = max(160, self.width() - 40)
        fm = QFontMetrics(self.q.font())
        self.q.setText(fm.elidedText(txt, Qt.TextElideMode.ElideRight, int(w - 24)))

    # ── 后端事件 ──
    def on_status(self, s):
        if s == 'connected':
            self._ws_fails = 0
            self.dot.setStyleSheet('color:#37c871;')
        else:
            self._ws_fails = getattr(self, '_ws_fails', 0) + 1
            self.dot.setStyleSheet('color:#e05555;')
            if self._ws_fails >= 5:
                # 一直连不上 = 后端没起来。别只写"重试中…"，直接告诉人该干什么
                self.stage.setText('后端没起来：双击 启动提词器.bat（这个窗口不用关，会自动连上）')
            else:
                self.stage.setText('后端未连接，重试中…')

    def on_event(self, ev):
        t = ev.get('type')
        q = ev.get('qid')
        # 只认"当前这一题"的事件：面试官连问时，上一题迟到的片段不会再糊到这一题上
        # （那是"照念错内容"级别的错，比不显示更坏）。question 本身要放行。
        if q is not None and self.cur_qid is not None and q != self.cur_qid and t != 'question':
            return
        if t == 'status':
            st = ev.get('stage')
            if st in ('ready', 'listening'):
                self.dot.setStyleSheet('color:#37c871;')
                self.stage.setText('在听 · ' + (ev.get('device', '') or ''))
            elif st == 'paused':
                self.dot.setStyleSheet('color:#d9a441;'); self.stage.setText('已暂停')
            elif st == 'error':
                self.dot.setStyleSheet('color:#e05555;'); self.stage.setText('出错: ' + str(ev.get('message', '')))
            elif st == 'audio_lost':
                # 拔耳机/切默认设备：后端会自动重连，这里必须让人看见，否则就是"整场静默"
                self.dot.setStyleSheet('color:#e05555;')
                self.stage.setText('⚠ 音频掉了，正在重连（第 %s 次）' % ev.get('restarts', '?'))
            elif st == 'audio_reconnecting':
                self.dot.setStyleSheet('color:#d9a441;'); self.stage.setText('重连音频设备中…')
            elif st == 'vad_calibrated':
                self.stats.setText('VAD 阈值 %.4f' % (ev.get('thresh') or 0))
            else:
                self.stage.setText({'loading_model': '加载模型…', 'warming_up': '预热中…',
                                    'indexing': '建索引…'}.get(st, str(st)))
        elif t == 'level':
            w = max(2, min(self.width() - 30, int(ev.get('rms', 0) * 900 * (self.width() - 30))))
            self.meter.setFixedWidth(w)
        elif t in ('asr', 'question'):
            txt = ev.get('text', '')
            self.cur_q = txt
            self._render_question()
            if t == 'question':
                self.cur_qid = ev.get('qid')      # 换题：从这一刻起只认新题的事件
                self.reset_answer()
            if ev.get('asrMs'):
                self.stats.setText('ASR %sms' % ev['asrMs'])
        elif t == 'filtered':
            # 面试官在调设备/自言自语，不调用模型。显示出来是为了让你能发现"误杀真问题"
            self.stats.setText('已过滤(非提问) · %s' % ev.get('text', '')[:20])
        elif t == 'answer_start':
            # gap = 面试官问的是一块我材料里没有的东西。标签变琥珀色 + 加⚠，
            # 一眼就能看出这段答案是"坦诚 + 不编经历"，不是平常那套经历稿。
            gap = ev.get('qtype') == 'gap'
            self.qtype.setText(('⚠ ' + ev.get('qtypeLabel', '')) if gap else ev.get('qtypeLabel', ''))
            self.qtype.setStyleSheet('color:#f2c14e; font-size:10px; background:rgba(240,192,64,0.22);'
                                     'border-radius:8px; padding:1px 6px;' if gap else '')
        elif t == 'answer_delta':
            self.buf += ev.get('text', '')
            self.render_answer()
        elif t == 'answer_done':
            self.stats.setText('首字 %sms · 总 %.1fs%s' % (
                ev.get('firstMs', 0), ev.get('totalMs', 0) / 1000,
                ' · 离线题库' if ev.get('offline') else ''))
        elif t == 'slow':
            self.dot.setStyleSheet('color:#d9a441;')
            self.stage.setText('⚠ ' + str(ev.get('message', '快答线还没出字')))
        elif t == 'answer_warn':
            # 数字/经历对不上：照念前先看一眼。只提示，不拦。
            parts = []
            for w in (ev.get('items') or [])[:2]:
                if w.get('kind') == 'number':
                    parts.append('数字 %s 不在本题材料里' % w.get('text'))
                else:
                    parts.append('"%s" 的经历不在材料里' % w.get('text'))
            if parts:
                self.warn.setText('⚠ ' + '；'.join(parts))
                self.warn.show()
        elif t == 'preplan':
            # 追问预案：面试官会接着问什么（本地题库算的）。贴在核心框下面，折叠也看得见。
            items = [i.get('q', '') for i in (ev.get('items') or []) if i.get('q')]
            if items:
                self.preplan.setText('追问预案：' + ' ／ '.join(q[:24] for q in items[:3]))
                self.preplan.show()
        elif t == 'deep_start':
            self._deep_ready = True
            self.set_expanded(True)      # 深答内容得看得见，自动展开
            self.deep.setPlainText('深度思考中…（约 30 秒）')
        elif t == 'deep_done':
            self._deep_ready = True
            self.set_expanded(True)
            self.deep.setPlainText(ev.get('text', ''))

    def render_answer(self):
        """通用分段渲染：模型可能输出【思路】【展开】【数字】或【思路】【拆解】【落点】，
        不硬编码段名。第一个出现的段 = 核心大字，其余进展开区。"""
        import re
        secs = re.findall(r'【([^】]+)】([\s\S]*?)(?=【|$)', self.buf)
        if not secs:
            # 模型偶尔不带【】标记（gap 策略实测出现过一次）。旧版直接全塞进展开区，
            # 核心大字停在"…"—— 面试时那一栏才是要照念的。退回"第一句当核心"。
            t = self.buf.strip()
            m = re.match(r'^([^。！？!?\n]{1,60}[。！？!?]?)', t)
            head = (m.group(1).strip() if m else t[:40]).strip()
            self._set_core(head or '…')
            self.expand.setPlainText(t[len(head):].strip())
            return
        self._set_core(secs[0][1].strip() or '…')
        rest = []
        for name, body in secs[1:]:
            bd = body.strip()
            if not bd or (name == '数字' and bd == '无'):
                continue
            rest.append('【%s】%s' % (name, bd))
        self.expand.setPlainText('\n\n'.join(rest))

    def reset_answer(self):
        self.buf = ''
        self._set_core('…'); self.expand.setPlainText('')
        self.deep.setPlainText(''); self._deep_ready = False
        if not self.expanded:
            self.deepbox.hide()
        self.qtype.setText('')
        self.qtype.setStyleSheet('')      # 清掉上一题可能留下的 gap 琥珀色
        self.warn.setText(''); self.warn.hide()
        self.preplan.setText(''); self.preplan.hide()

    # ── 全局热键 ──
    def _register_hotkeys(self):
        """所有全局热键只在这里注册一次。失败的不静默丢弃，退化成窗口内快捷键。"""
        hwnd = wintypes.HWND(int(self.winId()))
        self._hotkey_ok = {}
        self._hotkey_name = {}
        for hid, vk, name, action, desc in HOTKEYS:
            cands = [(name, vk)] + [(n, k) for n, k in HOTKEY_ALTS.get(hid, [])]
            ok, err, used = False, 0, name
            for cname, cvk in cands:
                try:
                    ok = bool(user32_x.RegisterHotKey(hwnd, hid, HK_MODS, cvk))
                    err = 0 if ok else ctypes.get_last_error()
                except Exception as e:
                    ok, err = False, -1
                    print('[hotkey] %s 注册异常: %s' % (cname, e))
                if ok:
                    used = cname
                    break
            self._hotkey_ok[hid] = ok
            self._hotkey_name[hid] = used
            note = ('已注册' if used == name else '首选 %s 被占，自动改用 %s' % (name, used)) if ok \
                else '注册失败→改用窗口内快捷键(winerr=%d)' % err
            print('[hotkey] %-12s %s  %s' % (used, note, desc))
        for hid, vk, name, action, desc in HOTKEYS:
            if self._hotkey_ok.get(hid):
                continue
            sc = QShortcut(QKeySequence(name), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            fn = getattr(self, action, None)
            if fn is not None:
                sc.activated.connect(fn)
            self._fallback.append(sc)
        # 注册完之后必须刷一次提示行：否则底部还显示首选键，
        # 而实际生效的可能是自动换过的备用键（本机 Ctrl+Shift+D → Ctrl+Shift+J）。
        self._sync_hint()

    def _on_hotkey(self, hid):
        now = time.monotonic()
        if now - self._hotkey_last.get(hid, 0.0) < 0.05:   # 50ms 内重复的一律当噪声
            return
        self._hotkey_last[hid] = now
        for i, vk, name, action, desc in HOTKEYS:
            if i == hid:
                fn = getattr(self, action, None)
                if fn is not None:
                    fn()
                return

    def _unregister_hotkeys(self):
        hwnd = wintypes.HWND(int(self.winId()))
        for hid, vk, name, action, desc in HOTKEYS:
            if self._hotkey_ok.get(hid):
                try:
                    user32.UnregisterHotKey(hwnd, hid)
                except Exception:
                    pass

    def closeEvent(self, e):
        self._unregister_hotkeys()
        super().closeEvent(e)

    # ── 操作 ──
    def toggle_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal(); self.show(); self.raise_(); self._keep_top()

    def toggle_pause(self):
        self.paused = not self.paused
        if self.ws is not None:
            self.ws.send({'cmd': 'pause' if self.paused else 'resume'})

    def run_deep(self):
        if self.cur_q:
            self._deep_ready = True
            self.set_expanded(True)
            if self.ws is not None:
                self.ws.send({'cmd': 'deep', 'text': self.cur_q})

    def ask_manual(self):
        v = self.manual.text().strip()
        if not v:
            return
        self.manual.clear()
        self.cur_q = v; self._render_question(); self.reset_answer()
        if self.ws is not None:
            self.ws.send({'cmd': 'ask', 'text': v})


# ── 自检（--selftest）：注入假数据 → 截图 → 真跑一遍 WM_HOTKEY 路径 ─────
def run_selftest(app, w):
    UI = os.path.dirname(os.path.abspath(__file__))
    # 自检用的是一段**虚构**面试场景（推荐系统项目），截图可直接对外展示；
    # 请勿把这里换成自己的真实材料 —— 自检截图经常被拿去当文档配图。
    LONG_Q = ('你在那个推荐系统项目里，为什么后来改成双塔召回加粗排重排？'
              '直接上深度模型不是更简单吗，数据量再涨十倍这套还撑得住吗？')
    LONG_CORE = ('双塔把算力从二十万商品收到五百个候选，精排只花在最值得的商品上，'
                 '冷启动也终于有了曝光。')
    SHORT_CORE = '双塔负责从二十万商品里召回五百个候选，精排只算最值得的那些。'

    def settle(ms=260):
        t0 = time.time()
        while (time.time() - t0) * 1000 < ms:
            QApplication.processEvents()
            time.sleep(0.01)

    def shot(tag):
        pix = w.grab()
        out = os.path.join(UI, tag)
        pix.save(out)
        print('[selftest] %s  %dx%d | 窗口 %dx%d | 展开=%s | 核心 %d 字 %dpx 超长标记=%s'
              % (tag, pix.width(), pix.height(), w.width(), w.height(),
                 w.expanded, len(w._core_text), w._core_px, w._core_long))
        return out

    def tap(hid):
        # 直接投递 WM_HOTKEY，走的就是 RegisterHotKey → 原生事件过滤器 → 处理函数这条真路径
        user32.PostMessageW(wintypes.HWND(int(w.winId())), WM_HOTKEY, hid, 0)
        settle(120)

    settle(200)
    for hid, vk, name, action, desc in HOTKEYS:
        used = (getattr(w, '_hotkey_name', {}) or {}).get(hid, name)
        print('[selftest] 全局注册 %-12s = %s%s'
              % (name, w._hotkey_ok.get(hid),
                 '' if used == name else '  (首选被占，已改用 %s)' % used))

    # 场景 1：默认「只看核心句」 + 核心句超长(>40 字)
    w.stage.setText('在听 · 耳机 (Realtek(R) Audio)')
    w.stats.setText('首字 620ms · 总 1.4s')
    w.cur_q = LONG_Q; w.qtype.setText('经历深挖题'); w._render_question()
    w.expand.setPlainText('我权衡过，直接上深度模型的问题是精排算力要花在全部商品上，单次推理的 P99 顶到 40ms 以上。\n'
                          '拆成召回加粗排之后，精排只处理 60 个候选，P99 回到 9ms 左右。\n'
                          '冷启动那条路我用的是内容向量兜底，新商品曝光占比从 3.1% 提到 11.7%。\n\n'
                          '数字：召回 500，粗排 60，A/B 14 天')
    w.deep.setPlainText('【思路】关键不是模型多深，是算力要花在最值得的商品上。\n'
                        '【拆解】我会先定死召回率下限，再决定粗排砍到多少…\n【落点】先把冷启动通路做出来。')
    w._deep_ready = True          # 有内容，但默认折叠着，不显示
    w._set_core(LONG_CORE)
    settle()
    shot('.selftest.png')

    # 场景 2：Ctrl+Shift+E 展开
    tap(4)
    print('[selftest] Ctrl+Shift+E -> 展开 =', w.expanded)
    settle()
    shot('.selftest_expand.png')

    # 场景 3：再按一次收起 + 合规核心句(≤40 字)
    tap(4)
    print('[selftest] Ctrl+Shift+E -> 展开 =', w.expanded)
    w.cur_q = LONG_Q; w._render_question(); w._set_core(SHORT_CORE)
    settle()
    shot('.selftest_ok.png')

    # 场景 4：四个热键各按两下，确认都没坏
    tap(1); print('[selftest] Ctrl+Shift+H -> 可见 =', w.isVisible())
    tap(1); print('[selftest] Ctrl+Shift+H -> 可见 =', w.isVisible())
    tap(2); print('[selftest] Ctrl+Shift+P -> 暂停 =', w.paused)
    tap(2); print('[selftest] Ctrl+Shift+P -> 暂停 =', w.paused)
    tap(3); print('[selftest] Ctrl+Shift+D -> 展开 =', w.expanded, '深答区可见 =', w.deepbox.isVisible())

    # 场景 5：gap（面试官甩出材料外的名词）—— 标签变琥珀色；
    # 而且模型有一次把四段【】标记全丢了，那时核心大字不能空着（那栏才是要照念的）
    w.reset_answer()
    w.on_event({'type': 'answer_start', 'qtype': 'gap', 'qtypeLabel': '没准备过的名词'})
    gap_tag = w.qtype.text(); gap_hl = bool(w.qtype.styleSheet())
    for piece in ['没有，', 'eBPF 我确实没实际用过，不装懂。', '我理解它是内核里挂探针做观测。']:
        w.on_event({'type': 'answer_delta', 'text': piece})
    w.stats.setText('首字 1526ms · 总 1.9s')
    settle(150)
    core = w._core_text
    print('[selftest] gap 标签=%r 高亮=%s | 无标记答案 -> 核心大字=%r 展开区=%d 字 -> %s'
          % (gap_tag, gap_hl, core, len(w.expand.toPlainText()),
             'PASS' if (gap_tag.startswith('⚠') and gap_hl and core and core != '…') else 'FAIL'))
    shot('.selftest_gap.png')
    w.on_event({'type': 'answer_start', 'qtype': 'experience', 'qtypeLabel': '经历深挖题'})
    print('[selftest] 切回普通题 -> 标签=%r 高亮已清=%s'
          % (w.qtype.text(), not w.qtype.styleSheet()))

    # 场景 6：面试官连问 —— 上一题迟到的事件必须被丢掉，不能糊到这一题上；
    # 同时验证追问预案上屏 + 数字告警标签（这两个在这一轮之前根本没接线/根本不存在）
    w.reset_answer(); w.cur_qid = None
    w.on_event({'type': 'question', 'text': '第一题：你那个推荐系统项目怎么分工的？', 'qid': 1})
    w.on_event({'type': 'answer_start', 'qtype': 'experience', 'qtypeLabel': '经历深挖题', 'qid': 1})
    w.on_event({'type': 'answer_delta', 'text': '【思路】第一题的答案。', 'qid': 1})
    w.on_event({'type': 'question', 'text': '第二题：你的语料规模多大？', 'qid': 2})       # 连问
    w.on_event({'type': 'answer_start', 'qtype': 'experience', 'qtypeLabel': '经历深挖题', 'qid': 2})
    w.on_event({'type': 'answer_delta', 'text': '【思路】第二题的答案：20 万商品、8 万日活。', 'qid': 2})
    w.on_event({'type': 'answer_delta', 'text': '（第一题迟到碎片）', 'qid': 1})            # 必须丢
    w.on_event({'type': 'preplan', 'qid': 2,
                'items': [{'q': '那为什么不用协同过滤？'}, {'q': '召回和粗排怎么切分？'}]})
    w.on_event({'type': 'answer_warn', 'qid': 2, 'items': [{'kind': 'number', 'text': '777'}]})
    settle(150)
    body = w._core_text + w.expand.toPlainText()
    ok6 = ('迟到碎片' not in body and '第一题' not in body and '第二题' in w._core_text
           and '追问预案' in w.preplan.text() and '777' in w.warn.text())
    print('[selftest] 连问防串题 核心=%r | 预案=%r | 告警=%r -> %s'
          % (w._core_text, w.preplan.text()[:28], w.warn.text(), 'PASS' if ok6 else 'FAIL'))
    shot('.selftest_qid.png')

    # 全局注册不到的热键（本机 Ctrl+Shift+D 被别的程序占了，winerr=1409）必须真的能兜底
    for sc in w._fallback:
        print('[selftest] 兜底快捷键 =', sc.key().toString())
    if w._fallback:
        try:
            from PyQt6.QtTest import QTest
            w.set_expanded(False); w.show(); w.activateWindow(); settle(300)
            before = w.expanded
            QTest.keyClick(w, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier |
                                          Qt.KeyboardModifier.ShiftModifier)
            settle(200)
            print('[selftest] 实按 Ctrl+Shift+D 兜底 -> 展开 %s => %s' % (before, w.expanded))
        except Exception as e:
            print('[selftest] 兜底实按自测跳过:', repr(e))
    print('[selftest] 结束')
    QTimer.singleShot(60, app.quit)   # 让 exec() 正常跑起来再退出，保证退出码 0


def main():
    write_pid()
    import atexit
    atexit.register(clear_pid)
    app = QApplication(sys.argv)
    app.aboutToQuit.connect(clear_pid)
    app.setFont(QFont('Microsoft YaHei', 10))
    w = Teleprompter()
    w.show()
    shot_after = os.environ.get('TP_SHOT_AFTER')
    if shot_after:
        def _auto():
            pix = w.grab()
            out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.live.png')
            pix.save(out)
            print('[auto-shot]', out)
        QTimer.singleShot(int(float(shot_after) * 1000), _auto)
    if SELFTEST:
        # 注入假数据渲染，截自己的图（不依赖系统截屏，内容保护下也可用）
        run_selftest(app, w)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
