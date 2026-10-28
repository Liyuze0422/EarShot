# -*- coding: utf-8 -*-
"""EarShot 设置面板 —— 图形化改配置，不用手编 JSON。

为什么不塞进浮窗：
  浮窗是「面试时盯着看」的东西 —— 必须干净、不抢焦点、防截屏（WDA_EXCLUDEFROMCAPTURE）；
  设置是「面试前弄一次」的东西 —— 控件多、要能贴密钥、要能连局域网、要能截图教人。
  两个场景的手感完全冲突，硬塞一起会同时毁掉两边。所以设置面板是一个**正常窗口**，
  浮窗继续走它的隐身路线。

怎么落地：
  本地读写直接 import server/prefs.py（不依赖后端在跑），
  只有「跨设备同步」才发 HTTP。这样后端挂了也能改配置 —— 恰恰是最需要能改的时候。
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if os.path.join(_ROOT, 'server') not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, 'server'))

import prefs as P                                            # noqa: E402
import settings as S                                         # noqa: E402

# 窗口伪装：所有窗口统一中性标题，避免在"共享屏幕/Alt+Tab/会议软件的窗口列表"里报出名字。
try:
    if os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server') not in sys.path:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))
    import winmask as _winmask
except Exception:
    class _winmask(object):
        @staticmethod
        def neutral_title():
            return '设置'

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal      # noqa: E402
from PyQt6.QtGui import QFont, QGuiApplication                # noqa: E402
from PyQt6.QtWidgets import (                                 # noqa: E402
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

import urllib.request
import json as _json


def _port():
    try:
        with open(os.path.join(_ROOT, '.runtime_port'), encoding='utf-8') as f:
            return int(f.read().strip())
    except Exception:
        return 8765


def _api(path, data=None, timeout=8):
    """打自己的后端；失败抛异常，调用方决定怎么显示。"""
    url = 'http://127.0.0.1:%d%s' % (_port(), path)
    if data is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=_json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read().decode('utf-8'))


def _get_remote(url, path, timeout=10):
    with urllib.request.urlopen(url.rstrip('/') + path, timeout=timeout) as r:
        return _json.loads(r.read().decode('utf-8'))


def _post_remote(url, path, data, timeout=10):
    req = urllib.request.Request(
        url.rstrip('/') + path, data=_json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read().decode('utf-8'))


class _TestConn(QThread):
    """测接口连通性 —— 放线程里，否则网络一卡界面就假死。"""
    done = pyqtSignal(bool, str)

    def __init__(self, base_url, key, model):
        super().__init__()
        self.base_url, self.key, self.model = base_url, key, model

    def run(self):
        try:
            body = _json.dumps({'model': self.model,
                                'messages': [{'role': 'user', 'content': 'hi'}],
                                'max_tokens': 1}).encode('utf-8')
            req = urllib.request.Request(
                self.base_url.rstrip('/') + '/chat/completions', data=body,
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + self.key})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=20) as r:
                _json.loads(r.read().decode('utf-8'))
            self.done.emit(True, '✅ 接口正常，往返 %.0f 毫秒' % ((time.time() - t0) * 1000))
        except Exception as e:
            msg = str(e)
            hint = ''
            if '401' in msg or 'Unauthorized' in msg:
                hint = '（密钥不对或被拒）'
            elif '404' in msg:
                hint = '（接口地址不对，注意结尾要不要 /v1）'
            elif 'timed out' in msg.lower() or 'timeout' in msg.lower():
                hint = '（超时 —— 是不是开了代理？）'
            self.done.emit(False, '❌ %s %s' % (msg[:160], hint))


def _row(label, widget, hint=''):
    """一行：标签 + 控件（+ 说明）。返回 (标签, 控件) 由调用方塞进布局。"""
    lb = QLabel(label)
    lb.setMinimumWidth(120)
    lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if hint and hasattr(widget, 'setToolTip'):
        widget.setToolTip(hint)
    return lb, widget


class Settings(QWidget):
    def __init__(self):
        super().__init__()
        # 中性标题：设置窗口标题里的 'EarShot' 一样会出现在会议软件的窗口列表里。
        self.setWindowTitle(_winmask.neutral_title())
        self.setMinimumSize(760, 620)
        self.d = P.device_cfg()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        title = QLabel(_winmask.neutral_title() or 'EarShot')
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_llm(), '密钥与模型')
        self.tabs.addTab(self._tab_target(), '面试目标')
        self.tabs.addTab(self._tab_audio(), '音频')
        self.tabs.addTab(self._tab_display(), '显示')
        self.tabs.addTab(self._tab_sync(), '跨设备同步')
        root.addWidget(self.tabs, 1)

        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color:#0a6; font-size:12px;')
        root.addWidget(self.status)

        bar = QHBoxLayout()
        self.btn_open = QPushButton('打开文件位置')
        self.btn_open.clicked.connect(self._open_folder)
        bar.addWidget(self.btn_open)
        bar.addStretch(1)
        b_cancel = QPushButton('关闭')
        b_cancel.clicked.connect(self.close)
        bar.addWidget(b_cancel)
        b_save = QPushButton('保存')
        b_save.clicked.connect(lambda: self._save(restart=False))
        bar.addWidget(b_save)
        b_save2 = QPushButton('保存并重启后端')
        b_save2.setDefault(True)
        b_save2.clicked.connect(lambda: self._save(restart=True))
        bar.addWidget(b_save2)
        root.addLayout(bar)

        self._load()

    # ── 各页 ────────────────────────────────────────────────────────
    def _tab_llm(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(14, 16, 14, 14)
        g.setVerticalSpacing(10)

        # 密钥
        box = QHBoxLayout()
        self.ed_key = QLineEdit(P.read_key())
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_key.setPlaceholderText('sk-...（留空 = 不配置，快答线会退回本地题库）')
        box.addWidget(self.ed_key, 1)
        self.btn_eye = QPushButton('显示')
        self.btn_eye.setCheckable(True)
        self.btn_eye.setFixedWidth(56)
        self.btn_eye.toggled.connect(
            lambda on: (self.ed_key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
                self.btn_eye.setText('隐藏' if on else '显示')))
        box.addWidget(self.btn_eye)
        lb, _ = _row('API 密钥', self.ed_key, '申请：platform.deepseek.com')
        g.addWidget(lb, 0, 0)
        g.addLayout(box, 0, 1)

        self.ed_base = QLineEdit(P.read_settings().get('base_url', ''))
        self.ed_base.setPlaceholderText('https://api.deepseek.com  或你的中转地址（含 /v1）')
        lb, _ = _row('接口地址', self.ed_base)
        g.addWidget(lb, 1, 0)
        g.addWidget(self.ed_base, 1, 1)

        self.ed_model = QLineEdit(P.read_settings().get('llm_model', ''))
        self.ed_model.setPlaceholderText('deepseek-v4-flash')
        lb, _ = _row('模型名', self.ed_model, '别填思考模型，首字会慢到 6 秒以上')
        g.addWidget(lb, 2, 0)
        g.addWidget(self.ed_model, 2, 1)

        self.btn_test = QPushButton('测试接口连通性')
        self.btn_test.clicked.connect(self._test)
        g.addWidget(self.btn_test, 3, 1)

        self.lb_key_src = QLabel('')
        self.lb_key_src.setStyleSheet('color:#666; font-size:12px;')
        g.addWidget(self.lb_key_src, 4, 1)

        self.ed_relay = QLineEdit(P.read_settings().get('relay_base_url', ''))
        self.ed_relay.setPlaceholderText('https://api.example.com/v1（留空关闭中转）')
        g.addWidget(QLabel('中转站地址'), 5, 0)
        g.addWidget(self.ed_relay, 5, 1)
        self.ed_relay_key = QLineEdit(S.relay_key())
        self.ed_relay_key.setEchoMode(QLineEdit.EchoMode.Password)
        g.addWidget(QLabel('中转令牌（Windows 加密保存）'), 6, 0)
        g.addWidget(self.ed_relay_key, 6, 1)
        self.cb_relay_tier = QComboBox()
        self.cb_relay_tier.addItem('快', 'fast')
        self.cb_relay_tier.addItem('准', 'smart')
        self.cb_relay_tier.setCurrentIndex(1 if S.get('relay_tier') == 'smart' else 0)
        g.addWidget(QLabel('默认档位'), 7, 0)
        g.addWidget(self.cb_relay_tier, 7, 1)
        g.setRowStretch(8, 1)
        g.setColumnStretch(1, 1)
        return w

    def _tab_target(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(14, 16, 14, 14)
        g.setVerticalSpacing(10)

        self.cb_profile = QComboBox()
        self.cb_profile.setEditable(True)
        self.cb_profile.addItem('（不限定，检索整个 knowledge/）', '')
        try:
            kd = os.path.join(_ROOT, 'knowledge')
            for name in sorted(os.listdir(kd)):
                if name.startswith('_') or name.startswith('.'):
                    continue
                if os.path.isdir(os.path.join(kd, name)):
                    n = len([x for x in os.listdir(os.path.join(kd, name))
                             if x.endswith(('.md', '.txt')) and not x.startswith('_')])
                    self.cb_profile.addItem('%s  （%d 份材料）' % (name, n), name)
        except Exception:
            pass
        cur = P.read_settings().get('corpus_profile', '')
        idx = self.cb_profile.findData(cur)
        if idx >= 0:
            self.cb_profile.setCurrentIndex(idx)
        elif cur:
            self.cb_profile.setEditText(cur)
        lb, _ = _row('资料包', self.cb_profile,
                     '每家公司一个包：JD、公司介绍、面经。面试哪家切哪家。')
        g.addWidget(lb, 0, 0)
        g.addWidget(self.cb_profile, 0, 1)

        tip = QLabel(
            '想加新公司：在 knowledge/ 下建一个文件夹（例如 knowledge/腾讯/），'
            '把 JD、公司介绍、面经（.md 或 .txt）丢进去，回到这里刷新就能选。\n'
            '　· 文件名以 _ 开头的材料不参与检索（拿来放自己的笔记）。\n'
            '　· 个人通用材料放 knowledge/_base/，所有资料包都会带上它。')
        tip.setWordWrap(True)
        tip.setStyleSheet('color:#555; font-size:12px;')
        g.addWidget(tip, 1, 0, 1, 2)

        self.btn_refresh = QPushButton('刷新资料包列表')
        self.btn_refresh.clicked.connect(self._reload_profiles)
        g.addWidget(self.btn_refresh, 2, 1)
        g.setRowStretch(3, 1)
        g.setColumnStretch(1, 1)
        return w

    def _tab_audio(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(14, 16, 14, 14)
        g.setVerticalSpacing(10)
        s = P.read_settings()

        self.cb_audio = QComboBox()
        for v, t in P.FIELDS['audio_mode']['choices']:
            self.cb_audio.addItem(t, v)
        i = self.cb_audio.findData(s.get('audio_mode', 'loopback'))
        self.cb_audio.setCurrentIndex(max(0, i))
        lb, _ = _row('采集方式', self.cb_audio,
                     '电脑面试选「只听电脑播放」：面试官的声音从扬声器出来被回环抓到，'
                     '麦克风彻底不听，避免把室友说话当题目。')
        g.addWidget(lb, 0, 0)
        g.addWidget(self.cb_audio, 0, 1)

        self.sp_thresh = QDoubleSpinBox()
        self.sp_thresh.setDecimals(3)
        self.sp_thresh.setSingleStep(0.001)
        self.sp_thresh.setRange(0.000, 0.200)
        self.sp_thresh.setValue(float(s.get('vad_thresh', 0.006)))
        lb, _ = _row('拾音灵敏度', self.sp_thresh, '越小越灵敏。听不到就调小，误触发就调大。')
        g.addWidget(lb, 1, 0)
        g.addWidget(self.sp_thresh, 1, 1)

        self.sp_end = QSpinBox()
        self.sp_end.setRange(120, 2000)
        self.sp_end.setSingleStep(20)
        self.sp_end.setSuffix(' 毫秒')
        self.sp_end.setValue(int(s.get('vad_end_ms', 320)))
        lb, _ = _row('判停', self.sp_end, '静音超过这么久算一句话说完。320 快 / 600 稳。')
        g.addWidget(lb, 2, 0)
        g.addWidget(self.sp_end, 2, 1)

        self.sp_merge = QSpinBox()
        self.sp_merge.setRange(0, 2000)
        self.sp_merge.setSingleStep(20)
        self.sp_merge.setSuffix(' 毫秒')
        self.sp_merge.setValue(int(s.get('vad_merge_ms', 220)))
        lb, _ = _row('合并窗口', self.sp_merge, '判停后再等这么久，接上面试官的下半句。')
        g.addWidget(lb, 3, 0)
        g.addWidget(self.sp_merge, 3, 1)

        self.sp_hold = QSpinBox()
        self.sp_hold.setRange(0, 300)
        self.sp_hold.setSuffix(' 秒')
        self.sp_hold.setValue(int(s.get('answer_holdoff_s', 25)))
        lb, _ = _row('答案保护', self.sp_hold, '刚出答案的这几秒内，听到的话不当新题。')
        g.addWidget(lb, 4, 0)
        g.addWidget(self.sp_hold, 4, 1)

        hint = QLabel('⚠ 改这几项要重启后端才生效。')
        hint.setStyleSheet('color:#b60; font-size:12px;')
        g.addWidget(hint, 5, 1)
        g.setRowStretch(6, 1)
        g.setColumnStretch(1, 1)
        return w

    def _tab_display(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(14, 16, 14, 14)
        g.setVerticalSpacing(10)
        s = P.read_settings()

        self.sp_font = QSpinBox()
        self.sp_font.setRange(12, 48)
        self.sp_font.setSuffix(' 像素')
        self.sp_font.setValue(int(s.get('core_font_px', 20)))
        lb, _ = _row('基准字号', self.sp_font, '核心大字的字号。屏幕小就调小，视力吃紧就调大。')
        g.addWidget(lb, 0, 0)
        g.addWidget(self.sp_font, 0, 1)

        self.sp_chars = QSpinBox()
        self.sp_chars.setRange(10, 120)
        self.sp_chars.setSuffix(' 字')
        self.sp_chars.setValue(int(s.get('core_max_chars', 40)))
        lb, _ = _row('大字上限', self.sp_chars, '核心句超这个字数就缩字号标黄。')
        g.addWidget(lb, 1, 0)
        g.addWidget(self.sp_chars, 1, 1)

        keys = QLabel(
            '场上热键（全局生效，不用切窗口）：\n'
            '　Ctrl+Shift+C　切换字色（黄 → 绿 → 白 → 黑，黑字适合对方共享白底文档）\n'
            '　Ctrl+Shift+↑ / ↓　字的不透明度\n'
            '　Ctrl+Shift+H　隐藏 / 显示浮窗\n'
            '　Ctrl+Shift+E　展开 / 收起长答案\n'
            '　Ctrl+Shift+P　暂停 / 继续　　Ctrl+Shift+D　要更深的答案\n'
            '　Ctrl+Shift+← / →　翻上一题 / 下一题\n'
            '　Ctrl+Shift+M　精简模式（只留核心大字）')
        keys.setWordWrap(True)
        box = QGroupBox('热键')
        bl = QVBoxLayout(box)
        bl.addWidget(keys)
        g.addWidget(box, 2, 0, 1, 2)
        g.setRowStretch(3, 1)
        g.setColumnStretch(1, 1)
        return w

    def _tab_sync(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(14, 16, 14, 14)
        g.setVerticalSpacing(10)
        d = self.d

        self.cb_lan = QCheckBox('允许局域网访问（另一台设备才连得上这台）')
        self.cb_lan.setChecked(bool(P.read_settings().get('lan_access')))
        g.addWidget(self.cb_lan, 0, 0, 1, 2)

        self.ed_token = QLineEdit(d.get('hub_token', ''))
        self.ed_token.setPlaceholderText('随便设一串，两台设备填一样')
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Password)
        lb, _ = _row('本机口令', self.ed_token,
                     '别的设备来取密钥时要对上这串。不设的话只有本机能取密钥。')
        g.addWidget(lb, 1, 0)
        g.addWidget(self.ed_token, 1, 1)

        self.lb_url = QLabel('…')
        self.lb_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lb_url.setStyleSheet('color:#06c; font-size:13px; font-weight:bold;')
        lb, _ = _row('本机地址', self.lb_url, '把这个地址 + 口令填到另一台设备上')
        g.addWidget(lb, 2, 0)
        g.addWidget(self.lb_url, 2, 1)

        self.btn_refresh_url = QPushButton('刷新本机地址')
        self.btn_refresh_url.clicked.connect(self._refresh_url)
        g.addWidget(self.btn_refresh_url, 3, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        g.addWidget(sep, 4, 0, 1, 2)

        self.ed_hub = QLineEdit(d.get('hub_url', ''))
        self.ed_hub.setPlaceholderText('http://192.168.0.27:8765　（另一台设备的地址）')
        lb, _ = _row('配置中心', self.ed_hub)
        g.addWidget(lb, 5, 0)
        g.addWidget(self.ed_hub, 5, 1)

        self.ed_hubtoken = QLineEdit(d.get('hub_token_saved') or d.get('hub_token', ''))
        self.ed_hubtoken.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_hubtoken.setPlaceholderText('那台设备的「本机口令」')
        lb, _ = _row('对方口令', self.ed_hubtoken)
        g.addWidget(lb, 6, 0)
        g.addWidget(self.ed_hubtoken, 6, 1)

        bar = QHBoxLayout()
        b_pull = QPushButton('⬇ 从配置中心拉取')
        b_pull.clicked.connect(self._pull)
        bar.addWidget(b_pull)
        b_push = QPushButton('⬆ 推送到配置中心')
        b_push.clicked.connect(self._push)
        bar.addWidget(b_push)
        g.addLayout(bar, 7, 0, 1, 2)

        self.txt_sync = QTextEdit()
        self.txt_sync.setReadOnly(True)
        self.txt_sync.setFixedHeight(96)
        self.txt_sync.setStyleSheet('font-size:12px;')
        g.addWidget(self.txt_sync, 8, 0, 1, 2)

        note = QLabel(
            '怎么用（两台设备互相同步，谁先设谁当中心）：\n'
            '　1. 两台都打开「允许局域网访问」，各设一串**相同**的口令，各自保存并重启后端。\n'
            '　2. 在 A 上把密钥填好、保存。\n'
            '　3. 到 B 上：「配置中心」填 A 的本机地址、「对方口令」填 A 的口令 → 点「拉取」。\n'
            '　4. B 就拿到了 A 的密钥和全部配置。反过来推也一样。')
        note.setWordWrap(True)
        note.setStyleSheet('color:#555; font-size:12px;')
        g.addWidget(note, 9, 0, 1, 2)
        g.setRowStretch(10, 1)
        g.setColumnStretch(1, 1)

        QTimer.singleShot(200, self._refresh_url)
        return w

    # ── 动作 ────────────────────────────────────────────────────────
    def _refresh_url(self):
        try:
            info = _api('/cfg/info')
            u = info.get('this_url') or ''
        except Exception:
            u = 'http://127.0.0.1:%d  （后端没在跑，局域网地址拿不到）' % _port()
        self.lb_url.setText(u)

    def _reload_profiles(self):
        cur = self.cb_profile.currentData() or self.cb_profile.currentText().strip()
        self.cb_profile.clear()
        self.cb_profile.addItem('（不限定，检索整个 knowledge/）', '')
        try:
            kd = os.path.join(_ROOT, 'knowledge')
            for name in sorted(os.listdir(kd)):
                if name.startswith(('_', '.')):
                    continue
                p = os.path.join(kd, name)
                if os.path.isdir(p):
                    n = len([x for x in os.listdir(p)
                             if x.endswith(('.md', '.txt')) and not x.startswith('_')])
                    self.cb_profile.addItem('%s  （%d 份材料）' % (name, n), name)
        except Exception as e:
            self.status.setText('刷新失败: %s' % e)
        i = self.cb_profile.findData(cur)
        if i >= 0:
            self.cb_profile.setCurrentIndex(i)
        self.status.setText('资料包列表已刷新，共 %d 个包' % (self.cb_profile.count() - 1))

    def _test(self):
        k = self.ed_key.text().strip()
        if not k:
            self.status.setText('⚠ 先填 API 密钥')
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText('测试中…')
        self._t = _TestConn(self.ed_base.text().strip(), k, self.ed_model.text().strip())
        self._t.done.connect(self._tested)
        self._t.start()

    def _tested(self, ok, msg):
        self.btn_test.setEnabled(True)
        self.btn_test.setText('测试接口连通性')
        self.status.setText(msg)

    def _load(self):
        self.lb_key_src.setText('密钥来源：%s' % P.key_source())

    def _collect(self):
        patch = {
            'relay_enabled': bool(self.ed_relay.text().strip()) and not bool(os.environ.get('TP_BASE_URL')),
            'relay_base_url': self.ed_relay.text().strip(),
            'relay_tier': self.cb_relay_tier.currentData(),
            'base_url': self.ed_base.text().strip(),
            'llm_model': self.ed_model.text().strip(),
            'corpus_profile': (self.cb_profile.currentData()
                               or self.cb_profile.currentText().strip()),
            'audio_mode': self.cb_audio.currentData(),
            'vad_thresh': round(self.sp_thresh.value(), 4),
            'vad_end_ms': self.sp_end.value(),
            'vad_merge_ms': self.sp_merge.value(),
            'answer_holdoff_s': self.sp_hold.value(),
            'core_font_px': self.sp_font.value(),
            'core_max_chars': self.sp_chars.value(),
            'lan_access': self.cb_lan.isChecked(),
        }
        return patch

    def _save(self, restart=False):
        try:
            old_key = P.read_key()
            patch = self._collect()
            S.save_relay_key(self.ed_relay_key.text().strip())
            P.write_settings(patch)
            new_key = self.ed_key.text().strip()
            key_changed = (new_key != old_key)
            if key_changed:
                P.write_key(new_key)
            P.save_device_cfg({'hub_url': self.ed_hub.text().strip(),
                               'hub_token': self.ed_token.text().strip(),
                               'hub_token_saved': self.ed_hubtoken.text().strip()})
            msg = '✅ 已保存（%d 项）' % len(patch)
            if key_changed:
                msg += '；密钥已更新'
            if restart:
                msg += '；' + self._restart()
            else:
                msg += '。改了接口/模型/资料包/采集方式/密钥的话，需要重启后端才生效。'
            self.status.setText(msg)
        except Exception as e:
            QMessageBox.critical(self, '保存失败', '%s: %s' % (type(e).__name__, e))

    def _restart(self):
        """让启动器把后端重启一遍 —— 复用 tools/launch.py 的 stop + 启动。

        ⚠ launch.py 没有 --restart（只有 --check/--stop/--no-ui/--no-library），
        所以要自己分两步：先 --stop，再不带参数启动。用 --no-library 避免弹选库框 ——
        资料包在这里刚改过，再弹一个「选知识库」只会让人怀疑自己刚才改的没生效。
        """
        try:
            import subprocess
            py = os.path.join(_ROOT, '.venv', 'Scripts', 'python.exe')
            if not os.path.exists(py):
                py = sys.executable
            lp = os.path.join(_ROOT, 'tools', 'launch.py')

            def call(args, timeout):
                return subprocess.run([py, '-X', 'utf8', lp] + args, cwd=_ROOT,
                                      capture_output=True, text=True,
                                      encoding='utf-8', errors='ignore', timeout=timeout)

            call(['--stop'], 120)
            time.sleep(1.5)
            r = call(['--no-library'], 300)
            if r.returncode == 0:
                return '后端已重启并就绪'
            return '后端重启返回 %d：%s' % (
                r.returncode, (r.stdout or r.stderr or '')[-200:])
        except Exception as e:
            return '后端重启失败：%s（可以手动重新双击启动）' % e

    def _pull(self):
        hub = self.ed_hub.text().strip()
        tok = self.ed_hubtoken.text().strip()
        if not hub:
            self.status.setText('⚠ 先填「配置中心」地址')
            return
        try:
            r = _get_remote(hub, '/cfg/snapshot?key=1&token=' + tok)
            if not r.get('ok'):
                self.txt_sync.setPlainText('拉取失败：%s' % r.get('error'))
                return
            sn = r['snapshot']
            changed, key_ok = P.apply_snapshot(sn, take_key=True)
            self.txt_sync.setPlainText(
                '✅ 已从 %s 拉取\n设备：%s\n改了 %d 项：%s\n密钥：%s\n'
                '请点「保存并重启后端」让它生效。'
                % (hub, sn.get('_from', {}).get('device_name', '?'), len(changed),
                   ', '.join(changed), '已更新（%s）' % P.mask_key() if key_ok else '未包含'))
            self.status.setText('✅ 拉取成功 —— 点「保存并重启后端」生效')
            self._reload_all()
        except Exception as e:
            self.txt_sync.setPlainText('拉取失败：%s\n（对方的「允许局域网访问」开了吗？'
                                       '地址端口对吗？口令对吗？）' % e)

    def _push(self):
        hub = self.ed_hub.text().strip()
        tok = self.ed_hubtoken.text().strip()
        if not hub:
            self.status.setText('⚠ 先填「配置中心」地址')
            return
        try:
            sn = P.snapshot(with_key=True)
            r = _post_remote(hub, '/cfg/apply?token=' + tok, {'snapshot': sn})
            self.txt_sync.setPlainText(
                ('✅ 已推送到 %s\n改动：%s\n密钥：%s\n对方需重启后端生效。'
                 % (hub, ', '.join(r.get('changed') or []),
                    '已更新' if r.get('key_updated') else '未包含'))
                if r.get('ok') else '推送失败：%s' % r.get('error'))
            self.status.setText('✅ 推送完成' if r.get('ok') else '推送失败')
        except Exception as e:
            self.txt_sync.setPlainText('推送失败：%s' % e)

    def _reload_all(self):
        s = P.read_settings()
        S.load(reload=True)
        self.ed_relay.setText(s.get('relay_base_url', ''))
        self.ed_relay_key.setText(S.relay_key())
        self.cb_relay_tier.setCurrentIndex(1 if S.get('relay_tier') == 'smart' else 0)
        self.ed_key.setText(P.read_key())
        self.ed_base.setText(s.get('base_url', ''))
        self.ed_model.setText(s.get('llm_model', ''))
        self.cb_lan.setChecked(bool(s.get('lan_access')))
        i = self.cb_audio.findData(s.get('audio_mode', 'loopback'))
        if i >= 0:
            self.cb_audio.setCurrentIndex(i)
        i = self.cb_profile.findData(s.get('corpus_profile', ''))
        if i >= 0:
            self.cb_profile.setCurrentIndex(i)
        self.sp_font.setValue(int(s.get('core_font_px', 20)))
        self.sp_chars.setValue(int(s.get('core_max_chars', 40)))
        self._load()

    def _open_folder(self):
        try:
            os.startfile(os.path.join(_ROOT, 'config'))
        except Exception as e:
            self.status.setText('打不开目录：%s' % e)


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)
    app = QApplication(sys.argv)
    w = Settings()
    # 给一个偏宽的初始尺寸：跨设备同步那页有长说明和文本区，用 QWidget 的自然尺寸
    # 只有 774 宽，说明文字会折得很难看。这里先给足，再用下面的夹取逻辑保证不出屏。
    w.resize(1000, 800)
    w.show()

    # 定位和限高都必须放在 show() 之后：在这之前 width()/height() 拿到的还是默认值，
    # 实测会让窗口按「还没布局完」的尺寸去居中，结果底部按钮落到可用区外被切掉
    # （1536x912 的屏上窗口高 775、顶到 y=158，底边 933 > 912）。
    scr = QGuiApplication.primaryScreen().availableGeometry()
    h = min(w.height(), scr.height() - 40)
    w.resize(min(w.width(), scr.width() - 20), h)
    w.move(scr.left() + (scr.width() - w.width()) // 2,
           scr.top() + max(10, (scr.height() - h) // 4))
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
