# -*- coding: utf-8 -*-
"""启动前的选库窗口：挑一个知识库（或新建一个），点「开始」进提词器。

为什么要有它
------------
知识库本来是"按公司分包"的（见 docs/配置手册 的「资料包」一节），但换包一直是命令行的事
（tools/switch.py）。这个窗口把两件事搬到台面上：

  1. **看得见**：有哪些库、每个库多少材料、题库准备好了没有；
  2. **新建库 = 选文档 + 起名**，不用手工往 knowledge/ 里拷文件。

点「开始」之后：写 corpus_profile -> 备好题库与换说法扩展词（已有的库是秒级，
新建的第一次约 1~4 分钟）-> 窗口自己关掉 -> tools/launch.py 接着起后端和浮窗。
结果落在 .runtime_profile（JSON），供 launch.py 读。

自检（供 tools/preflight.py 与人工验证）:
    python ui/library.py --selftest        # 渲染一遍、截自己的图、退出码 0
"""
import html as _html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo_root(start):
    """往上找仓库根。

    这个文件会被 tools/preflight.py 复制到 .tmp/ 下跑自检 —— 那时 __file__ 的父目录
    不是仓库根，直接 dirname 上去就 import 不到 server/ 下的模块
    （实测：体检里报 ModuleNotFoundError: No module named 'settings'）。
    """
    d = start
    for _ in range(8):
        if os.path.isdir(os.path.join(d, 'server')) and os.path.isdir(os.path.join(d, 'ui')):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.dirname(start)


def _code_root():
    """代码目录：打包成 exe 后是 sys._MEIPASS，平时和数据目录是同一个。"""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', _repo_root(HERE))
    return _repo_root(HERE)


def _data_root():
    """数据目录：打包后代码在 _MEIPASS，但 knowledge / config / 知识库 都留在磁盘上。

    具体在哪由启动器算好（它从 exe 往上找 config/settings.json）并通过 TP_DATA_ROOT 传下来；
    自己算只作兜底。**必须和启动器一致**，否则写出来的 .runtime_profile 它读不到。
    """
    if getattr(sys, 'frozen', False):
        if os.environ.get('TP_DATA_ROOT'):
            return os.environ['TP_DATA_ROOT']
        d = os.path.dirname(sys.executable)          # 兜底：自己往上找一遍，和 settings 保持一致
        for _ in range(4):
            if os.path.exists(os.path.join(d, 'config', 'settings.json')):
                return d
            up = os.path.dirname(d)
            if up == d:
                break
            d = up
        return os.path.dirname(sys.executable)
    return _repo_root(HERE)


ROOT = _data_root()
sys.path.insert(0, os.path.join(_code_root(), 'server'))

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
                             QPushButton, QTextEdit, QVBoxLayout, QWidget)

import settings                       # noqa: E402
from version import __version__ as _VERSION   # noqa: E402  版本号只此一处
import knowledge                      # noqa: E402

SELFTEST = '--selftest' in sys.argv
# TP_LIBRARY_AUTO=<库名>：窗口照常显示，但起来后自动选中该库并按下「开始」。
# 走的是和鼠标点击**完全相同的代码路径**（on_go -> Prepare -> 写结果 -> 关窗），只是触发源不同，
# 这样 tools/launch.py 的整条启动链路可以无人值守地端到端验证。
AUTO = os.environ.get('TP_LIBRARY_AUTO', '').strip()
RESULT_FILE = os.path.join(ROOT, '.runtime_profile')
BANK_DIR = os.path.join(ROOT, '知识库')
DOC_EXT = ('.md', '.txt', '.markdown', '.docx')

QSS = '''
QWidget { background:#1b1c20; color:#d7dae0; font-family:"Microsoft YaHei"; font-size:13px; }
QLabel#title { font-size:17px; font-weight:600; color:#ffffff; }
QLabel#sub   { color:#7b818c; font-size:12px; }
QLabel#stat  { color:#8f96a3; font-size:12px; }
QLabel#sign  { color:#4c515a; font-size:11px; }
QLabel#err   { color:#ff9a9a; font-size:12px; }
QListWidget  { background:#232429; border:1px solid #303239; border-radius:8px; padding:5px; outline:0; }
QListWidget::item { padding:9px 10px; border-radius:6px; color:#c9cdd6; }
QListWidget::item:selected { background:#2f4f8f; color:#ffffff; }
QListWidget::item:hover:!selected { background:#2a2c33; }
QPushButton { background:#2a2c33; border:1px solid #3a3d45; border-radius:6px;
              color:#d7dae0; padding:7px 14px; }
QPushButton:hover { background:#33363e; }
QPushButton:disabled { color:#5f646e; }
QPushButton#go { background:#2f6fd0; border:0; color:#ffffff; font-size:14px;
                 font-weight:600; padding:9px 28px; }
QPushButton#go:hover { background:#3a7ee0; }
QPushButton#go:disabled { background:#3a3d45; color:#7b818c; }
QLineEdit, QTextEdit { background:#232429; border:1px solid #34363d; border-radius:6px;
                       padding:6px 8px; color:#e4e7ec; }
QProgressBar { background:#232429; border:0; border-radius:4px; height:6px; }
QProgressBar::chunk { background:#2f6fd0; border-radius:4px; }
QScrollBar:vertical { background:transparent; width:8px; }
QScrollBar::handle:vertical { background:#3a3d45; border-radius:4px; }
'''


# ── 知识库的读写 ──────────────────────────────────────────────────────
def count_docs(d):
    if not os.path.isdir(d):
        return 0
    n = 0
    for _dp, _dn, fs in os.walk(d):
        n += len([f for f in fs if f.lower().endswith(('.md', '.txt', '.markdown'))])
    return n


def lib_names():
    root = settings.knowledge_root()
    if not os.path.isdir(root):
        return []
    out = []
    for n in sorted(os.listdir(root)):
        if n.startswith('_') or n.startswith('.'):
            continue
        if os.path.isdir(os.path.join(root, n)):
            out.append(n)
    return out


def lib_core_docs(name):
    """这个库自己的材料（不含 _base）。"""
    return count_docs(os.path.join(settings.knowledge_root(), name))


def lib_all_docs(name):
    return count_docs(os.path.join(settings.knowledge_root(), '_base')) + lib_core_docs(name)


def bank_info(name):
    """(有没有题库, 条数)。"""
    p = os.path.join(BANK_DIR, '题库_%s.json' % name)
    if not os.path.exists(p):
        return False, 0
    try:
        d = json.load(open(p, encoding='utf-8'))
        return True, len(d.get('items', d if isinstance(d, list) else []))
    except Exception:
        return True, 0


def write_result(obj):
    try:
        with open(RESULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


def set_profile(name):
    """写 config/settings.json 的 corpus_profile（原文件留 .bak）。"""
    p = os.path.join(ROOT, 'config', 'settings.json')
    cfg = {}
    if os.path.exists(p):
        try:
            cfg = json.load(open(p, encoding='utf-8-sig'))
        except Exception:
            cfg = {}
    if cfg.get('corpus_profile') == name:
        return
    if os.path.exists(p):
        shutil.copy2(p, p + '.bak')
    cfg['corpus_profile'] = name
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write('\n')


def docx_to_text(path):
    """把 .docx 抠成纯文本 —— 标准库就够（docx 就是个 zip + xml），不装 python-docx。"""
    with zipfile.ZipFile(path) as z:
        cand = [n for n in z.namelist() if n == 'word/document.xml']
        if not cand:
            raise ValueError('不是标准 .docx（里面没有 word/document.xml）')
        xml = z.read(cand[0]).decode('utf-8', 'ignore')
    xml = xml.replace('</w:p>', '\n').replace('<w:br/>', '\n').replace('<w:br />', '\n')
    xml = xml.replace('</w:tr>', '\n')
    xml = re.sub(r'<w:tab[^>]*/>', '\t', xml)
    text = _html.unescape(re.sub(r'<[^>]+>', '', xml))
    out, blank = [], 0
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(line)
    return '\n'.join(out).strip() + '\n'


def _free_name(dest_dir, stem, ext):
    """"报告.md" 已存在就换成 "报告-2.md" —— 建库时选到同名文件不能把原来的覆盖掉。"""
    dst = os.path.join(dest_dir, stem + ext)
    i = 2
    while os.path.exists(dst):
        dst = os.path.join(dest_dir, '%s-%d%s' % (stem, i, ext))
        i += 1
    return dst


def save_docs(paths, dest_dir, log=None):
    """把选中的文档放进 dest_dir。.docx 转成 .md；重名自动加 -2、-3。返回 (成功, 跳过)。"""
    def note(s):
        if log:
            log(s)
    os.makedirs(dest_dir, exist_ok=True)
    ok, skip = [], []
    for src in paths:
        try:
            base = os.path.basename(src)
            stem, ext = os.path.splitext(base)
            ext = ext.lower()
            if ext in ('.md', '.markdown', '.txt'):
                dst = os.path.join(dest_dir, base)
                if os.path.abspath(src) == os.path.abspath(dst):
                    ok.append(base)
                    continue
                dst = _free_name(dest_dir, stem, ext)
                shutil.copy2(src, dst)
                ok.append(os.path.basename(dst))
            elif ext == '.docx':
                dst = _free_name(dest_dir, stem, '.md')
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write('# %s\n\n' % stem)
                    f.write(docx_to_text(src))
                ok.append(stem + '.md')
            else:
                skip.append(base)
                continue
            note('%s  ->  %s' % (base, os.path.basename(dst)))
        except Exception as e:
            skip.append('%s (%s)' % (os.path.basename(src), e))
    # 重名兜底：同名文件会被 copy2 覆盖，这里只报告结果，不做改名（用户看得见列表）
    return ok, skip


def pick_files(parent, title):
    files, _ = QFileDialog.getOpenFileNames(
        parent, title, '', '文档 (*.md *.markdown *.txt *.docx);;全部文件 (*)')
    return files


def collect_dir(d):
    out = []
    for dp, _dn, fs in os.walk(d):
        for f in fs:
            if f.lower().endswith(DOC_EXT):
                out.append(os.path.join(dp, f))
    return out


# ── 后台准备：配置 + 题库 + 扩展词 ────────────────────────────────────
class Prepare(QThread):
    """点「开始」之后在后台干的活。窗口不能被卡住，所以放线程里。"""
    progress = pyqtSignal(str)

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.error = ''

    def _tool(self, script, *args):
        """跑一个项目脚本，把它的输出当进度回报（题库生成要 40+ 秒）。"""
        if getattr(sys, 'frozen', False):
            # 打包后没有独立的 python 也没有 .py 文件：让 exe 自己再跑一次，用 --run 分发
            cmd = [sys.executable, '--run',
                   os.path.join(_code_root(), 'tools', script)] + list(args)
        else:
            py = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
            if not os.path.exists(py):
                py = sys.executable
            cmd = [py, '-X', 'utf8', os.path.join(ROOT, 'tools', script)] + list(args)
        # 冻结进程必须清掉 PYTHONHOME / PYTHONPATH 再起子进程，否则子进程的
        # CPython 会拿父进程的 PYTHONHOME 去找标准库、初始化失败、静默死掉。
        # 详见 tools/launch.py 的 clean_env()。
        env = dict(os.environ)
        if getattr(sys, 'frozen', False):
            for k in list(env):
                if k.startswith('_PYI_') or k in ('_MEIPASS2', 'PYTHONHOME', 'PYTHONPATH'):
                    env.pop(k, None)
        p = subprocess.Popen(cmd, cwd=ROOT, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8', errors='replace')
        for line in p.stdout or []:
            line = line.strip()
            if not line:
                continue
            if line.startswith('  [') or '块' in line or '生成 ' in line or '合并' in line:
                self.progress.emit(line[:80])
        p.wait()
        return p.returncode

    def run(self):
        try:
            old = knowledge.active_profile()
            self.progress.emit('写入资料包配置…')
            set_profile(self.name)
            knowledge.set_profile(self.name)

            have, n = bank_info(self.name)
            bank_path = os.path.join(BANK_DIR, '题库_%s.json' % self.name)
            stale = False
            if have:
                newest = 0.0
                for sub in ('_base', self.name):
                    d = os.path.join(settings.knowledge_root(), sub)
                    for dp, _dn, fs in os.walk(d):
                        for f in fs:
                            try:
                                newest = max(newest, os.path.getmtime(os.path.join(dp, f)))
                            except OSError:
                                pass
                stale = os.path.getmtime(bank_path) < newest - 60
            if not have:
                self.progress.emit('首次使用这个库：生成题库（约 1 分钟）…')
                rc = self._tool('build_bank.py', '--merge', '--force')
                if rc != 0:
                    self.error = '题库生成失败（exit %s），看 logs/' % rc
            elif stale:
                self.progress.emit('材料更新过，重做题库（约 1 分钟）…')
                self._tool('build_bank.py', '--merge', '--force')
            else:
                self.progress.emit('题库已就绪（%d 条）' % n)

            self.progress.emit('检查换说法扩展词…')
            self._tool('kb_expand.py')
            self.progress.emit('就绪')
            knowledge.set_profile(old or None)
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, e)


# ── 新建库 ────────────────────────────────────────────────────────────
class NewLibDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('新建知识库')
        self.setStyleSheet(QSS)
        # 尺寸要克制：1080p 屏去掉任务栏只剩一千出头，太高会被裁掉底部的「创建」。
        self.resize(560, 620)
        self.core, self.extra = [], []

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(9)

        v.addWidget(QLabel('库名', objectName='sub'))
        self.name = QLineEdit()
        self.name.setPlaceholderText('比如：公司简称')
        v.addWidget(self.name)

        v.addWidget(QLabel('专属文档 —— 这家公司/这个岗位的材料', objectName='sub'))
        row = QHBoxLayout()
        b1 = QPushButton('选择文件…')
        b1.clicked.connect(self._pick_core)
        b2 = QPushButton('选择文件夹…')
        b2.clicked.connect(self._pick_core_dir)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        v.addLayout(row)
        self.core_list = QListWidget()
        self.core_list.setFixedHeight(96)
        v.addWidget(self.core_list)

        v.addWidget(QLabel('其他 / 补充文档 —— 所有库共用的通用材料（简历、项目报告等）', objectName='sub'))
        row2 = QHBoxLayout()
        b3 = QPushButton('选择文件…')
        b3.clicked.connect(self._pick_extra)
        b4 = QPushButton('选择文件夹…')
        b4.clicked.connect(self._pick_extra_dir)
        row2.addWidget(b3)
        row2.addWidget(b4)
        row2.addStretch(1)
        v.addLayout(row2)
        self.extra_list = QListWidget()
        self.extra_list.setFixedHeight(96)
        v.addWidget(self.extra_list)

        v.addWidget(QLabel('公司背景（可留空，之后也能补）', objectName='sub'))
        self.company = QTextEdit()
        self.company.setPlaceholderText('这家公司做什么、这个岗位要什么 —— 每题都会带进上下文')
        self.company.setFixedHeight(62)
        v.addWidget(self.company)

        self.err = QLabel('', objectName='err')
        v.addWidget(self.err)

        row3 = QHBoxLayout()
        row3.addStretch(1)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        mk = QPushButton('创建')
        mk.setObjectName('go')
        mk.clicked.connect(self._create)
        row3.addWidget(cancel)
        row3.addWidget(mk)
        v.addLayout(row3)

    def _add(self, widget, paths):
        have = {widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())}
        for p in paths:
            if p not in have and p.lower().endswith(DOC_EXT):
                it = QListWidgetItem(os.path.basename(p))
                it.setData(Qt.ItemDataRole.UserRole, p)
                it.setToolTip(p)
                widget.addItem(it)

    def _pick_core(self):
        self._add(self.core_list, pick_files(self, '选择专属文档'))

    def _pick_extra(self):
        self._add(self.extra_list, pick_files(self, '选择补充文档'))

    def _pick_core_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择文件夹（递归收集 .md/.txt/.docx）')
        if d:
            self._add(self.core_list, collect_dir(d))

    def _pick_extra_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择文件夹（递归收集 .md/.txt/.docx）')
        if d:
            self._add(self.extra_list, collect_dir(d))

    def _all(self, widget):
        return [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())]

    def _create(self):
        name = self.name.text().strip()
        if not name:
            self.err.setText('先给这个库起个名字')
            return
        if re.search(r'[\\/:*?"<>|]', name):
            self.err.setText('库名里不能有 / : * ? " < > | 或反斜杠')
            return
        root = settings.knowledge_root()
        dest = os.path.join(root, name)
        if os.path.isdir(dest) and any(os.scandir(dest)):
            self.err.setText('已经有一个叫「%s」的库了，换个名字' % name)
            return
        core, extra = self._all(self.core_list), self._all(self.extra_list)
        if not core and not extra:
            self.err.setText('至少选一份文档')
            return
        try:
            ok1, sk1 = save_docs(core, dest)
            ok2, sk2 = save_docs(extra, os.path.join(root, '_base'))
        except Exception as e:
            self.err.setText('写文件失败：%s' % e)
            return
        text = self.company.toPlainText().strip()
        if text:
            with open(os.path.join(dest, 'company.md'), 'w', encoding='utf-8') as f:
                f.write('# 目标公司背景（每题都会带进上下文）\n\n')
                f.write('> 以 # 或 > 开头的行只当注释，不会注入。\n\n')
                f.write(text + '\n')
        self.created = name
        self.summary = '专属 %d 份，补充 %d 份' % (len(ok1), len(ok2))
        if sk1 or sk2:
            self.summary += '；跳过 ' + '、'.join((sk1 + sk2)[:3])
        self.accept()


# ── 主窗口 ────────────────────────────────────────────────────────────
class LibraryWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('顺风耳 · 选择知识库')
        self.setStyleSheet(QSS)
        # 置顶 + 居中：双击「启动提词器.bat」之后这个窗口必须立刻看得见。
        # 实测踩过：不置顶时它被别的窗口（比如浏览器、IDE）压在后面，看起来像"什么都没发生"。
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(575, 455)
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.move(g.center().x() - 287, g.center().y() - 227)
        self.worker = None

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        v.addWidget(QLabel('选择知识库', objectName='title'))
        v.addWidget(QLabel('选一个直接开始；没有就新建一个（把材料交给它，它会准备好检索用的题库）',
                           objectName='sub'))

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_pick)
        self.list.itemDoubleClicked.connect(lambda _i: self.on_go())
        v.addWidget(self.list, 1)

        row = QHBoxLayout()
        add = QPushButton('＋ 新建知识库')
        add.clicked.connect(self.on_new)
        ref = QPushButton('刷新')
        ref.clicked.connect(self.reload)
        row.addWidget(add)
        row.addWidget(ref)
        row.addStretch(1)
        v.addLayout(row)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        v.addWidget(self.bar)

        self.stat = QLabel('', objectName='stat')
        v.addWidget(self.stat)

        foot = QHBoxLayout()
        hint = QLabel('选好后点右下角「开始」，这个窗口会关掉、浮窗接管', objectName='sub')
        foot.addWidget(hint)
        foot.addStretch(1)
        self.go = QPushButton('开始 ▶')
        self.go.setObjectName('go')
        self.go.setEnabled(False)
        self.go.clicked.connect(self.on_go)
        foot.addWidget(self.go)
        v.addLayout(foot)

        v.addWidget(QLabel('Liyuze0422 制作    v%s' % _VERSION, objectName='sign'),
                    0, Qt.AlignmentFlag.AlignRight)

        self.reload()

    def reload(self):
        self.list.clear()
        for n in lib_names():
            have, cnt = bank_info(n)
            core = lib_core_docs(n)
            mark = '·  题库 %d 条' % cnt if have else '·  首次进入要备题库'
            it = QListWidgetItem('%s     %d 份材料  %s' % (n, lib_all_docs(n), mark))
            it.setData(Qt.ItemDataRole.UserRole, n)
            it.setToolTip('本库专属 %d 份 + 共用 %d 份' % (
                core, lib_all_docs(n) - core))
            self.list.addItem(it)
        cur = knowledge.active_profile()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == cur:
                self.list.setCurrentRow(i)
                break
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._on_pick()
        if not self.list.count():
            self.stat.setText('还没有任何知识库 —— 点「＋ 新建知识库」开始')

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else ''

    def _on_pick(self):
        n = self.current()
        self.go.setEnabled(bool(n) and self.worker is None)
        if n:
            core = lib_core_docs(n)
            self.stat.setText('『%s』本库专属 %d 份 + 共用材料 %d 份；开始后先备题库，之后就快了'
                              % (n, core, lib_all_docs(n) - core))

    def on_new(self):
        dlg = NewLibDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload()
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == dlg.created:
                    self.list.setCurrentRow(i)
                    break
            self.stat.setText('已创建「%s」（%s）。点右下角「开始」进提词器。'
                              % (dlg.created, getattr(dlg, 'summary', '')))

    def on_go(self):
        if self.worker is not None:
            return
        name = self.current()
        if not name:
            return
        self.go.setEnabled(False)
        self.list.setEnabled(False)
        self.bar.show()
        self.stat.setText('正在准备『%s』…' % name)
        self.worker = Prepare(name)
        self.worker.progress.connect(lambda s: self.stat.setText(s))
        self.worker.finished.connect(lambda: self._finish(name))
        self.worker.start()

    def _finish(self, name):
        w = self.worker
        if w is not None:
            w.wait(3000)          # finished 之后线程基本已退出，这里只是兜底
        err = w.error if w is not None else '内部错误'
        if err:
            self.worker = None
            self.bar.hide()
            self.list.setEnabled(True)
            self.go.setEnabled(True)
            self.stat.setText('准备失败：%s' % err)
            self.stat.setObjectName('err')
            self.stat.setStyleSheet('color:#ff9a9a;')
            return
        write_result({'action': 'start', 'profile': name, 'ts': time.time()})
        self.close()


def run_selftest(app, w):
    print('[selftest] 库 %d 个: %s' % (w.list.count(), [w.list.item(i).text().split()[0]
                                                     for i in range(w.list.count())]))
    print('[selftest] 当前选中 =', w.current())
    print('[selftest] 开始按钮可用 =', w.go.isEnabled())

    def later():
        dlg = NewLibDialog(w)
        dlg.name.setText('测试库')
        it = QListWidgetItem('demo.md')
        it.setData(Qt.ItemDataRole.UserRole, os.path.join(HERE, 'library.py'))
        dlg.core_list.addItem(it)
        dlg.company.setPlainText('演示用的一行公司背景。')
        dlg.show()
        app.processEvents()
        QTimer.singleShot(260, lambda: shot(app, w, dlg))

    def shot(app_, w_, dlg_):
        out = os.path.join(HERE, '.selftest_library.png')
        w_.grab().save(out)
        print('[selftest] 主窗口截图', out)
        out2 = os.path.join(HERE, '.selftest_newlib.png')
        dlg_.grab().save(out2)
        print('[selftest] 新建框截图', out2)
        print('[selftest] 结束')
        dlg_.close()
        QTimer.singleShot(60, app_.quit)

    QTimer.singleShot(500, later)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei', 10))
    w = LibraryWindow()
    w.show()
    if AUTO:
        for i in range(w.list.count()):
            if w.list.item(i).data(Qt.ItemDataRole.UserRole) == AUTO:
                w.list.setCurrentRow(i)
                break
        else:
            print('[auto] 没有叫「%s」的库' % AUTO)        # 不静默：名字写错要能看出来
        QTimer.singleShot(400, w.on_go)
    if SELFTEST:
        run_selftest(app, w)
    app.exec()
    return 0


if __name__ == '__main__':
    sys.exit(main())
