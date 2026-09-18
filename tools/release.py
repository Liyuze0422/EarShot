# -*- coding: utf-8 -*-
r"""一条命令发一个版本。

    python tools\release.py --version 0.9.20              # 只做检查，不打包（默认）
    python tools\release.py --version 0.9.20 --build      # 门禁 + 打包 + 生成资产
    python tools\release.py --version 0.9.20 --build --publish   # 再发到 GitHub

**为什么不做成 CI**：打包要装 funasr / onnxruntime / PyQt6，加起来上 GB，Windows runner
上很不稳；而且模型和配置都在本机，本地环境本来就是唯一验证过的打包环境。所以这条路
是「本机一条命令」，CI 版的 workflow 另有一份，哪天真跑通了再切过去。

**发布物**（Release 里就这三样）：
    EarShot-v<ver>-win64-full.zip          全量包，486MB，第一次装的人下这个
    EarShot-v<ver>-patch-from-v<old>.zip   差分包，通常几十 MB，老用户更新只下这个
    manifest-v<ver>.json                   文件清单，差分的依据

**差分包的上一版清单从哪来**：优先用 --prev 指定；不给就在产物目录里找版本号
最接近的那份 manifest；都没有就只出全量包（新用户能装，老用户走全量）。
"""

import argparse
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OUT = os.path.join(ROOT, 'build', 'release')
DIST = os.path.join(ROOT, 'dist', 'EarShot')


def say(msg):
    print(msg, flush=True)


def run(cmd, cwd=ROOT, check=True, env=None):
    say('  $ ' + ' '.join(cmd))
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(cmd, cwd=cwd, env=e)
    if check and r.returncode:
        say('  ✗ 退出码 %d' % r.returncode)
        sys.exit(r.returncode)
    return r.returncode


def gate():
    """提交前那三条，CI 就是这么跑的。过不了就别打包 —— 一个坏包发出去收不回来。"""
    say('[1/4] 门禁')
    run([PY, '-X', 'utf8', '-m', 'ruff', 'check', '.'])
    run([PY, '-X', 'utf8', '-m', 'pytest', '-o', 'addopts=', '-q'])
    run([PY, '-X', 'utf8', '-m', 'pytest', '-o', 'addopts=', '-q', 'tests/test_repo_hygiene.py'])


def check_version(ver):
    """版本号必须是单一事实来源里那个 —— 打包前对一遍，免得发出去一个名不副实的包。"""
    p = os.path.join(ROOT, 'server', 'version.py')
    m = re.search(r"__version__\s*=\s*'([^']+)'", open(p, encoding='utf-8').read())
    got = m.group(1) if m else '?'
    if got != ver:
        say('✗ 版本号对不上：server/version.py 是 %s，你给的是 %s' % (got, ver))
        sys.exit(2)
    chg = open(os.path.join(ROOT, 'CHANGELOG.md'), encoding='utf-8').read()
    if not re.search(r'^##\s+%s\b' % re.escape(ver), chg, re.M):
        say('✗ CHANGELOG.md 里没有 ## %s 一节 —— 用户看不到这一版改了什么' % ver)
        sys.exit(2)
    say('  版本号一致：%s，CHANGELOG 有对应一节' % ver)


def build():
    say('[2/4] PyInstaller 打包（几分钟）')
    run([PY, '-X', 'utf8', '-m', 'PyInstaller', '--noconfirm', 'EarShot.spec'])
    if not os.path.isdir(DIST):
        say('✗ 没找到 %s' % DIST)
        sys.exit(3)
    n = sum(len(f) for _r, _d, f in os.walk(DIST))
    say('  产物：%s（%d 个文件）' % (DIST, n))


def find_prev(out_dir, ver):
    """在产物目录里挑一份最合适的「上一版清单」。挑不到就返回 None。"""
    best = None
    for p in glob.glob(os.path.join(out_dir, 'manifest-v*.json')):
        v = os.path.basename(p)[len('manifest-v'):-len('.json')]
        if v == ver:
            continue
        if best is None or _key(v) > _key(best[0]):
            best = (v, p)
    return best


def _key(v):
    try:
        return tuple(int(x) for x in v.split('.'))
    except ValueError:
        return (0,)


def assets(ver, prev):
    say('[3/4] 生成清单 + 包')
    os.makedirs(OUT, exist_ok=True)
    run([PY, '-X', 'utf8', os.path.join('tools', 'make_release.py'),
         '--dir', DIST, '--version', ver, '--out', OUT, '--full', '--embed']
        + (['--prev', prev[1]] if prev else []))
    if not prev:
        say('  ! 没有上一版清单，这次只有全量包 —— 老用户更新会走全量下载')
    return sorted(glob.glob(os.path.join(OUT, '*v%s*.zip' % ver))
                  + glob.glob(os.path.join(OUT, 'manifest-v%s.json' % ver)))


def notes(ver):
    """从 CHANGELOG 里切出这一节，当 Release 的正文。"""
    txt = open(os.path.join(ROOT, 'CHANGELOG.md'), encoding='utf-8').read()
    m = re.search(r'^##\s+%s\b.*?(?=^##\s|\Z)' % re.escape(ver), txt, re.M | re.S)
    body = (m.group(0).strip() if m else '')
    p = os.path.join(OUT, 'notes-v%s.md' % ver)
    os.makedirs(OUT, exist_ok=True)
    open(p, 'w', encoding='utf-8').write(body)
    return p


def publish(ver, files):
    say('[4/4] 发到 GitHub Release')
    title = 'v%s' % ver
    np = notes(ver)
    cmd = ['gh', 'release', 'create', 'v%s' % ver, '--title', title,
           '--notes-file', np] + files
    say('  $ ' + ' '.join(cmd))
    say('  提示：gh 需要联网（本机如走代理，先设 $env:HTTPS_PROXY）')
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        say('  ✗ 发布失败（资产已经在 %s，可以手动传）' % OUT)
        return r.returncode
    say('  发布完成：https://github.com/Liyuze0422/EarShot/releases/tag/v%s' % ver)
    return 0


def main():
    ap = argparse.ArgumentParser(description='发一个 EarShot 版本')
    ap.add_argument('--version', required=True)
    ap.add_argument('--build', action='store_true', help='真的打包（默认只做检查）')
    ap.add_argument('--publish', action='store_true', help='发到 GitHub（需要 gh 已登录）')
    ap.add_argument('--prev', help='上一版的 manifest-v*.json')
    a = ap.parse_args()

    ver = a.version.lstrip('vV')
    gate()
    check_version(ver)
    if not a.build:
        say('\n只做检查，没有打包。加 --build 才会真的产出安装包。')
        return 0

    build()
    prev = (os.path.basename(a.prev), a.prev) if a.prev else find_prev(OUT, ver)
    if prev:
        say('  上一版清单：%s' % os.path.basename(prev[1]))
    files = assets(ver, prev)
    say('\n产物：')
    for f in files:
        say('  %-52s %s' % (os.path.basename(f), _h(os.path.getsize(f))))
    if a.publish:
        return publish(ver, files)
    say('\n没有发布。确认无误后加 --publish（或手动把上面这些文件传到 Release）。')
    return 0


def _h(n):
    return '%.1f MB' % (n / 1048576.0) if n >= 1048576 else '%.1f KB' % (n / 1024.0)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
