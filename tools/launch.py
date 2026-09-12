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
import os, sys, time, json, argparse, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENV_PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
VENV_PYW = os.path.join(ROOT, '.venv', 'Scripts', 'pythonw.exe')
SERVER = os.path.join(ROOT, 'server', 'main.py')
UI = os.path.join(ROOT, 'ui', 'app.py')
PORT_FILE = os.path.join(ROOT, '.runtime_port')
UI_PID_FILE = os.path.join(ROOT, '.ui_pid')
LOG_DIR = os.path.join(ROOT, 'logs')
LAUNCH_LOG = os.path.join(LOG_DIR, 'launch.log')
PORTS = [8765, 8766, 8767, 8768, 8769]
DETACHED = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW


def say(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
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
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
             "Where-Object { $_.CommandLine -like '*server*main.py*' } | "
             "Select-Object -ExpandProperty ProcessId"],
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
    exe, _, extra = interpreters()
    env = dict(os.environ); env.update(extra)
    say('用解释器: %s%s' % (exe, ('（+PYTHONPATH=%s）' % extra['PYTHONPATH']) if extra else ''))
    p = subprocess.Popen([exe, '-X', 'utf8', SERVER],
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
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*ui*app.py*' } | "
             "Select-Object -ExpandProperty ProcessId"],
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
    _, exe, extra = interpreters()
    env = dict(os.environ); env.update(extra)
    return subprocess.Popen([exe, '-X', 'utf8', UI], cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT,
                            creationflags=DETACHED, close_fds=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只看状态，不启动')
    ap.add_argument('--no-ui', action='store_true', help='只起后端')
    ap.add_argument('--stop', action='store_true', help='停掉所有提词器进程（后端 + 浮窗）')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')

    if a.stop:
        # 注意排除自己：launch.py 自己的命令行里也有 main.py 字样，不排除会把自杀当收工
        # （实测：进程把自己 kill 了，一行输出都没有，退出码 -1）
        # 匹配方式与 backend_processes() 保持一致：认脚本名，不认目录名 ——
        # 仓库可以叫 EarShot、可以放在任何路径下，按目录名匹配会一个进程都停不掉。
        ps = ('Get-CimInstance Win32_Process -Filter "Name=\'python.exe\' or Name=\'pythonw.exe\'" | '
              "Where-Object { $_.ProcessId -ne %d -and $_.CommandLine -notlike '*launch.py*' "
              "-and ($_.CommandLine -like '*server*main.py*' -or $_.CommandLine -like '*ui*app.py*') } | "
              "ForEach-Object { Write-Output $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }"
              % os.getpid())
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
