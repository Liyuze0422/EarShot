# -*- coding: utf-8 -*-
"""一键启动：后端 + 浮窗（幂等、有健康检查、有日志）。

旧版 启动提词器.bat 的三个毛病：
  1. 不管后端是不是已经在跑，照样再起一个 —— 两个抢同一个端口，新的那个静默退出
  2. `timeout /t 12` 死等 12 秒，机器冷的时候模型还没加载完就起了浮窗
  3. pythonw 启动的浮窗崩了没有任何日志，屏幕上只剩"后端未连接"

用法:
  python tools/launch.py            # 起后端 + 浮窗
  python tools/launch.py --check    # 只看现在什么状态，不起来
  python tools/launch.py --no-ui    # 只起后端（调试用）
"""
import os
import sys
import time
import json
import argparse
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, 'frozen', False))


def _code_root():
    """代码在哪。打包成 exe 之后源码作为数据文件解到 sys._MEIPASS。"""
    if FROZEN:
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(HERE)


def _data_root():
    """数据（config / knowledge / logs / 知识库）在哪。

    打包后代码在 _MEIPASS（临时解压目录），数据必须留在磁盘上一个稳定位置。
    这里**从 exe 所在目录往上找 config/settings.json**，找到哪层算哪层 ——
    因为 onedir 的布局是 dist/EarShot/EarShot.exe，数据和它不在同一层，
    往上走几层才是仓库根。找不到就退回 exe 目录（那时数据得自己放旁边）。

    找到的结果会通过 TP_DATA_ROOT 传给所有子进程（后端 / 浮窗 / 选库窗口），
    它们优先读这个变量 —— 省得四个文件各写一份推断逻辑、还可能推出不同结果。
    """
    if not FROZEN:
        return os.path.dirname(HERE)
    d = os.path.dirname(sys.executable)
    for _ in range(4):
        if os.path.exists(os.path.join(d, 'config', 'settings.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.dirname(sys.executable)


CODE = _code_root()
ROOT = _data_root()
# 同一个进程里跑的脚本（--run tools/download_model.py 这种）也要看到数据根：
# 它们按 __file__ 推会推到 _MEIPASS（代码目录），而数据在磁盘上那一层才作数。
os.environ['TP_DATA_ROOT'] = ROOT
VENV_PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
VENV_PYW = os.path.join(ROOT, '.venv', 'Scripts', 'pythonw.exe')
SERVER = os.path.join(CODE, 'server', 'main.py')
UI = os.path.join(CODE, 'ui', 'app.py')
LIBRARY = os.path.join(CODE, 'ui', 'library.py')       # 启动前的选库窗口
ROLE_SCRIPTS = {'server': SERVER, 'ui': UI, 'library': LIBRARY}
PROFILE_FILE = os.path.join(ROOT, '.runtime_profile')  # 选库窗口把结果写在这
SETTINGS_FILE = os.path.join(ROOT, 'config', 'settings.json')
PORT_FILE = os.path.join(ROOT, '.runtime_port')
UI_PID_FILE = os.path.join(ROOT, '.ui_pid')
LOG_DIR = os.path.join(ROOT, 'logs')
LAUNCH_LOG = os.path.join(LOG_DIR, 'launch.log')
# 打包成"无控制台"exe 之后 sys.stdout/stderr 是 None，任何 print 都会抛 AttributeError
# （实测：一抛就整个启动器静默退出，看起来像"双击了没反应"）。这里统一兜到日志文件，
# 诊断信息照样留得下。普通运行（有控制台）不受影响。
try:
    if sys.stdout is None or sys.stderr is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        _fb = open(os.path.join(LOG_DIR, 'stdout.log'), 'a', encoding='utf-8', errors='replace')
        if sys.stdout is None:
            sys.stdout = _fb
        if sys.stderr is None:
            sys.stderr = _fb
except Exception:
    pass

PORTS = [8765, 8766, 8767, 8768, 8769]
DETACHED = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW


def dispatch_argv():
    """这次是被"叫起来干活"，还是自己当启动器？

    打包后没有独立的 .py 可以起，改成再起一次自己 + 分发参数：
      EarShot.exe --role server          起后端
      EarShot.exe --role ui              起浮窗
      EarShot.exe --role library         起选库窗口
      EarShot.exe --run tools/kb_expand.py [args]   跑任意项目脚本
    源码是作为数据文件打进 _MEIPASS 的，所以 runpy 按路径跑和"直接运行那个脚本"完全等价。
    返回 (目标脚本路径, 剩下的参数)；不是在干活时返回 (None, None)。
    """
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a in ('--role', '--run') and i + 1 < len(argv):
            target = argv[i + 1]
            if a == '--role':
                target = ROLE_SCRIPTS.get(target, '')
            elif not os.path.isabs(target):
                # --run 给相对路径时按**代码目录**解析，不看当前工作目录：
                # 打包后 tools/ 在 _internal\ 里，而用户是在 exe 那一层开命令行的。
                # 实测：在 exe 目录里敲 "EarShot.exe --run tools/download_model.py"
                # 会报「找不到要跑的脚本」—— 同一句话必须在 _internal 里敲才对。
                target = os.path.join(CODE, target)
            return target, argv[:i] + argv[i + 2:]
    return None, None


def child_argv(role, windowless=False):
    """起一个角色子进程要用的 (命令行, 额外环境变量)。"""
    extra = {'TP_DATA_ROOT': ROOT}      # 让子进程和这里算出同一个数据目录
    if FROZEN:
        return [sys.executable, '--role', role], extra
    py, pyw, extra2 = interpreters()
    extra.update(extra2)
    return [(pyw if windowless else py), '-X', 'utf8', ROLE_SCRIPTS[role]], extra


def clean_env():
    """起子进程用的环境：必须把 PyInstaller 塞进来的那几个变量删掉。

    打包后子进程还是这个 exe。PyInstaller 会往环境里塞 _PYI_* / _MEIPASS2，
    子进程的 bootloader 看到它们会以为自己是"被父进程解包出来的临时进程"，
    于是**直接退出** —— 症状是后端/浮窗起不来、控制台日志里一行 Python 输出都没有、
    进程列表里一闪就没（实测踩过：手动用 PowerShell 起同一个 exe 却完全正常，
    差异就在这几个环境变量）。
    """
    env = dict(os.environ)
    for k in list(env):
        if k.startswith('_PYI_') or k == '_MEIPASS2':
            env.pop(k, None)
    # ★ 还要清掉 PYTHONHOME / PYTHONPATH。PyInstaller 会给每个冻结进程设置
    #   PYTHONHOME=<exe 旁边的 _internal>，子进程（还是这个 exe）继承之后，
    #   它自己的 CPython 会拿这个值去找标准库，而 PyInstaller 的布局里根本没有
    #   lib/python3.12 这个目录 → 解释器初始化失败（stderr 里只有一段
    #   "Python path configuration: ... stdlib dir = ''"，连一行正常输出都来不及写）→
    #   后端/浮窗**静默死掉**。手动用 PowerShell 起同一个 exe 却完全正常，
    #   差异就在这几个继承来的环境变量上。
    # 清掉之后子进程的 bootloader 会自己重新设置正确的值。
    if FROZEN:
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
    return env


def proc_names():
    """按进程名过滤时要认哪些名字。

    打包后跑的是 EarShot.exe，不再是 python.exe/pythonw.exe ——
    只认 python 名字会一个进程都找不到（表现为"停不掉"和"开出两个浮窗"）。

    2026-09-20 修正：EarShot.exe 必须**无条件**认，不能只在 frozen 时认。
    实测踩到的场景：用户双击的是打包版 exe（命令行 EarShot.exe --role server），
    后来用源码版 run.ps1 --stop 想关掉 —— 那时 FROZEN=False、名字表里只有 python，
    于是报"已停掉 0 个提词器进程"却仍删掉了 .runtime_port：进程和采集都还在，
    端口文件却没了，下次启动会误判"没在跑"而再起一个后端（端口漂到 8766）。
    命令行那层匹配本来就认 --role server/--role ui，名字这层不该把它挡在外面。
    """
    names = ['python.exe', 'pythonw.exe', 'EarShot.exe']
    if FROZEN:
        me = os.path.basename(sys.executable)
        if me.lower() not in [n.lower() for n in names]:
            names.append(me)
    return " or ".join("Name='%s'" % n for n in names)


def say(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LAUNCH_LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def is_teleprompter(port, timeout=2.0):
    """这个端口上跑的是不是提词器后端。"""
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/' % port, timeout=timeout) as r:
            body = r.read(800).decode('utf-8', 'replace')
        return r.status == 200 and ('提词器' in body or 'teleprompter' in body.lower())
    except Exception:
        return False


def backend_processes():
    """正在跑（或正在启动）的后端进程 —— 只看端口会漏掉"还在加载模型"的那十几秒。"""
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-CimInstance Win32_Process -Filter "%s" | '
             "Where-Object { $_.CommandLine -like '*server*main.py*' "
             "-or $_.CommandLine -like '*--role server*' } | "
             'Select-Object -ExpandProperty ProcessId' % proc_names()],
            capture_output=True, text=True, timeout=15).stdout or ''
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def find_running():
    """已经在跑的提词器后端端口（先看端口文件，再扫一遍候选端口）。"""
    order = []
    try:
        p = int(open(PORT_FILE, encoding='utf-8').read().strip())
        order.append(p)
    except Exception:
        pass
    order += [p for p in PORTS if p not in order]
    for p in order:
        if is_teleprompter(p):
            return p
    return None


def interpreters():
    r"""启动用哪个解释器 —— 这里有个必须绕开的坑。

    实测：Windows 上 .venv 的 python.exe 有时是**重定向器**，用它启动会额外派生一个
    基础解释器子进程（venv 的 home 里那个 python.exe）。于是一次启动变成两个进程：
      · 两个后端互相把对方当"重复实例"，双双退出（表现为"启动失败"）
      · 启动器盯着父进程、真正的服务跑在子进程里
    绕法：直接读 .venv/pyvenv.cfg 拿到 home，用基础解释器 + PYTHONPATH 指到 venv 的
    site-packages（实测 funasr_onnx / soundcard / PyQt6 全都 import 得到）。
    返回 (python_exe, pythonw_exe, env_extra)
    """
    venv = os.path.join(ROOT, '.venv')
    home = ''
    cfg = os.path.join(venv, 'pyvenv.cfg')
    try:
        for line in open(cfg, encoding='utf-8', errors='replace'):
            if line.strip().lower().startswith('home'):
                home = line.split('=', 1)[1].strip()
    except Exception:
        home = ''
    sp = os.path.join(venv, 'Lib', 'site-packages')
    base = os.path.join(home, 'python.exe') if home else ''
    basew = os.path.join(home, 'pythonw.exe') if home else ''
    if base and os.path.exists(base) and os.path.isdir(sp):
        old = os.environ.get('PYTHONPATH', '')
        return base, (basew if os.path.exists(basew) else base), {'PYTHONPATH': sp + (os.pathsep + old if old else '')}
    return VENV_PY, VENV_PYW, {}


def start_backend():
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, 'backend_console.log'), 'a', encoding='utf-8', errors='replace')
    log.write('\n===== %s 启动后端 =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
    log.flush()
    cmd, extra = child_argv('server')
    env = clean_env(); env.update(extra)
    # 用 .get()：打包后 extra 里只有 TP_DATA_ROOT、没有 PYTHONPATH，
    # 直接取下标会 KeyError，主进程在起后端之前就崩（而且 windowed 模式下异常是
    # 被吞进一个 Error 对话框里的，看上去就像"双击没反应"）。
    say('用解释器: %s%s' % (cmd[0], ('（+PYTHONPATH=%s）' % extra['PYTHONPATH']) if extra.get('PYTHONPATH') else ''))
    p = subprocess.Popen(cmd,
                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=DETACHED, close_fds=True)
    return p


def model_ready(port, timeout=1.5):
    """后端模型真的加载完了吗（/healthz）。首页能打开只说明 uvicorn 起来了。"""
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/healthz' % port, timeout=timeout) as r:
            return bool(json.loads(r.read(200).decode('utf-8', 'replace')).get('ready'))
    except Exception:
        return False


def wait_ready(seconds=60, proc=None):
    """等后端**模型加载完**再起浮窗（不是死等固定秒数，也不是"端口通了就算"）。

    proc 传进来时会同时盯着它：自己起的进程一旦退出就立刻返回，不白等满 60 秒
    （实测踩过：子进程 1 秒就因"已有后端在跑"退出，这边却干等了 64 秒才报错）。
    """
    t0 = time.time()
    port = None
    while time.time() - t0 < seconds:
        if proc is not None and proc.poll() is not None:
            return None, time.time() - t0
        if port is None:
            port = find_running()
        elif model_ready(port):
            return port, time.time() - t0
        time.sleep(0.5)
    return (port if port and model_ready(port) else None), time.time() - t0


def ui_processes():
    """按命令行找浮窗进程（PID 文件可能过期，或者浮窗是用别的解释器起的）。"""
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-CimInstance Win32_Process -Filter "%s" | '
             "Where-Object { $_.CommandLine -like '*ui*app.py*' "
             "-or $_.CommandLine -like '*--role ui*' } | "
             'Select-Object -ExpandProperty ProcessId' % proc_names()],
            capture_output=True, text=True, timeout=10).stdout or ''
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def ui_alive():
    r"""浮窗是不是已经开着了。

    两道判据：① 自己的 PID 文件还活着；② 命令行里有 ui/app.py 的任何 python 进程
    （实测：浮窗可能是用另一个解释器（比如另一个 venv 的 pythonw.exe）起的，
      只看 PID 文件会漏，结果同时开出两个浮窗）。
    """
    try:
        pid = int(open(UI_PID_FILE, encoding='utf-8').read().strip())
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return bool(ui_processes())


def start_ui():
    log = open(os.path.join(LOG_DIR, 'ui_console.log'), 'a', encoding='utf-8', errors='replace')
    log.write('\n===== %s 启动浮窗 =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
    log.flush()
    cmd, extra = child_argv('ui', windowless=True)
    env = clean_env(); env.update(extra)
    return subprocess.Popen(cmd, cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT,
                            creationflags=DETACHED, close_fds=True)


def read_profile():
    """config/settings.json 里的 corpus_profile（选库窗口会改它）。"""
    try:
        return str(json.load(open(SETTINGS_FILE, encoding='utf-8-sig')).get('corpus_profile') or '')
    except Exception:
        return ''


def stop_all():
    """停掉后端 + 浮窗（排除自己）。换库时必须走这一步：后端装的是旧库的索引。

    注意排除自己：launch.py 自己的命令行里也有 main.py 字样，不排除会把自杀当收工
    （实测：进程把自己 kill 了，一行输出都没有，退出码 -1）。
    匹配方式与 backend_processes() 保持一致：认脚本名，不认目录名 ——
    仓库可以叫 EarShot、可以放在任何路径下，按目录名匹配会一个进程都停不掉。
    """
    # frozen 下没有 launch.py 这个命令行可以排除自己，靠 ProcessId -ne 自己兜底。
    ps = ('Get-CimInstance Win32_Process -Filter "%s" | '
          "Where-Object { $_.ProcessId -ne %d -and $_.CommandLine -notlike '*launch.py*' "
          "-and ($_.CommandLine -like '*server*main.py*' -or $_.CommandLine -like '*ui*app.py*' "
          "-or $_.CommandLine -like '*--role server*' -or $_.CommandLine -like '*--role ui*') } | "
          "ForEach-Object { Write-Output $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }"
          % (proc_names(), os.getpid()))
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                             capture_output=True, text=True, timeout=30).stdout or ''
        n = len([x for x in out.split() if x.strip().isdigit()])
    except Exception as e:
        say('停不干净: %s' % e)
        return 1
    for f in (PORT_FILE, UI_PID_FILE):
        try:
            os.remove(f)
        except Exception:
            pass
    say('已停掉 %d 个提词器进程（后端 + 浮窗），端口文件也清了' % n)
    return 0


def run_library():
    """起选库窗口，等它关掉。返回 (要不要继续启动, 选中的库名)。

    单独进程 + pythonw：不弹控制台黑框，崩了也有日志（logs/library_console.log）。
    结果走 .runtime_profile —— 起之前先清成空对象，免得读上一次的残留。
    """
    if not os.path.exists(LIBRARY):
        say('没有 ui/library.py，跳过选库直接启动')
        return True, read_profile()
    try:
        with open(PROFILE_FILE, 'w', encoding='utf-8') as f:
            f.write('{}')
    except Exception:
        pass
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, 'library_console.log'), 'a', encoding='utf-8', errors='replace')
    log.write('\n===== %s 选库窗口 =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
    log.flush()
    cmd, extra = child_argv('library', windowless=True)
    env = clean_env(); env.update(extra)
    try:
        p = subprocess.Popen(cmd, cwd=ROOT, env=env,
                             stdout=log, stderr=subprocess.STDOUT)
        p.wait()
    except Exception as e:
        say('选库窗口起不来（%s），跳过选库直接启动' % e)
        return True, read_profile()
    try:
        d = json.load(open(PROFILE_FILE, encoding='utf-8'))
    except Exception:
        d = {}
    if d.get('action') != 'start':
        return False, ''
    return True, d.get('profile', '')


def apply_pending_update():
    """把上一次界面上点过的更新装上。装了返回 True（调用方应当直接退出）。

    **只在启动的最前面调**：这一刻程序还没起来，exe / dll 都没被占用，批处理才换得动文件。
    失败一律返回 False 走正常启动 —— 一次装不上的更新，绝不该让用户连提词器都开不了。
    """
    try:
        sys.path.insert(0, os.path.join(CODE, 'server'))
        import update as U
        pend = U.read_pending()
        if not pend:
            return False
        say('待应用的更新 %s，正在安装…' % (pend.get('version') or ''))
        U.launch_apply(pend['bat'], pend['args'])
        # 记号先清掉再退出：批处理等一下会替换文件，但**不会**去动 logs 里的记号。
        # 不清的话下次启动又会装一遍，成了循环。
        U.clear_pending()
        return True
    except Exception as e:
        try:
            say('应用更新失败（不影响使用）：%s' % e)
        except Exception:
            pass
        return False


def main():
    # 打包后同一个 exe 分饰多角，--role / --run 由 child_argv() 传进来（必须最先判断）
    target, rest = dispatch_argv()
    if target:
        if not os.path.exists(target):
            sys.stderr.write('[launch] 找不到要跑的脚本: %s\n' % target)
            return 2
        import runpy
        sys.argv = [target] + rest
        runpy.run_path(target, run_name='__main__')
        return 0
    if '--version' in sys.argv[1:]:
        # 装成 exe 之后用户看不到 git log，命令行也要能问出版本
        try:
            sys.path.insert(0, os.path.join(CODE, 'server'))
            from version import __version__
            print('EarShot %s' % __version__)
        except Exception:
            print('EarShot (版本号读不到)')
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只看状态，不启动')
    ap.add_argument('--version', action='store_true', help='打印版本号后退出')
    ap.add_argument('--no-ui', action='store_true', help='只起后端')
    ap.add_argument('--no-library', action='store_true', help='跳过选库窗口，直接用当前库启动')
    ap.add_argument('--stop', action='store_true', help='停掉所有提词器进程（后端 + 浮窗）')
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass          # 无控制台的 exe 里 stdout 是 None

    if a.stop:
        return stop_all()

    # 上次在界面上点过「立即更新」：文件已经备在 logs/update_staging 里，
    # 现在程序还没起来，正是换文件的时机 —— 交给那个批处理，然后本进程退出，
    # 由批处理把新版本拉起来。用户看到的只是「重新双击了一下，版本就变了」。
    if not os.environ.get('TP_SKIP_UPDATE') and apply_pending_update():
        return 0

    # 启动前先选库：双击「启动提词器.bat」就是走这条路。
    # --no-library / TP_NO_LIBRARY=1 跳过（tools/switch.py 重启后端时用，不能弹窗打断）。
    before = read_profile()
    if not (a.check or a.no_ui or a.no_library) and not os.environ.get('TP_NO_LIBRARY'):
        go, prof = run_library()
        if not go:
            say('没选知识库，已退出（后端没有启动）')
            return 0
        if read_profile() != before:
            # 换了库：正在跑的后端装的是上一个库的索引和题库，复用它会答错材料。
            say('知识库从「%s」换成「%s」' % (before or '(全量)', read_profile()))
            if find_running() or backend_processes() or ui_alive():
                say('先把装了旧库的后端停掉再起')
                stop_all()
                time.sleep(1.5)
        elif prof:
            say('继续用『%s』' % prof)

    port = find_running()
    procs = backend_processes()
    if procs and not port and not a.check:
        say('已经有一个后端进程（PID %s）在启动中，等它就好 —— 不再起第二个' % ', '.join(map(str, procs)))
        port, dt = wait_ready(60)
        if not port:
            say('那个后端 60 秒还没就绪，看 logs/backend_console.log')
            return 1
    elif port and not a.check:
        say('后端已经在跑（127.0.0.1:%d），跳过启动 —— 不会再抢端口' % port)
    elif a.check:
        say('后端%s' % ('在跑：127.0.0.1:%d' % port if port else '没在跑'))
        if not port:
            return 1
    else:
        say('启动后端（加载模型 + 建索引，约 12 秒）…')
        proc = start_backend()
        port, dt = wait_ready(60, proc)
        if proc.poll() is not None and port is None:
            # 我们自己起的那个进程已经退出了。exit=3 是"已经有后端在跑"——
            # 那不是失败，是它守规矩没起第二个：等/找那个已经在跑的后端就行。
            if proc.returncode == 3:
                say('已有一个后端在跑（我没起第二个），等它就绪…')
                port, dt = wait_ready(60)
                if not port:
                    say('那个后端 60 秒还没就绪，看 logs/backend_console.log')
                    return 1
            else:
                say('后端进程自己退出了（exit=%s），看下面的日志尾巴' % proc.returncode)
                try:
                    tail = open(os.path.join(LOG_DIR, 'backend_console.log'), encoding='utf-8',
                                errors='replace').read()[-900:]
                    print(tail)
                except Exception:
                    pass
                return 1
        if not port:
            say('后端 60 秒内没起来（模型还在加载？），看 logs/backend_console.log')
            try:
                tail = open(os.path.join(LOG_DIR, 'backend_console.log'), encoding='utf-8',
                            errors='replace').read()[-800:]
                print(tail)
            except Exception:
                pass
            return 1
        say('后端就绪：127.0.0.1:%d（等了 %.1fs）' % (port, dt))

    if a.check or a.no_ui:
        return 0
    if ui_alive():
        say('浮窗已经在跑，跳过启动 —— 不会开出第二个窗口')
    else:
        start_ui()
        say('浮窗已启动。出问题看 logs/ui_console.log；崩溃日志在 logs/backend_error.log')
    say('『 就绪 』后端 127.0.0.1:%d + 浮窗 —— 这个窗口可以直接关掉' % (port or 0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
