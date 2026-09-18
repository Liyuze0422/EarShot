# -*- coding: utf-8 -*-
"""版本检查 + 应用内更新。

**为什么要这个东西**：装了 exe 的用户看不到 git log。现在 README 教他用
EarShot.exe --version 拿本地版本，再自己去 Releases 页面对数字 —— 那是应用商店
出现之前的做法。这个模块让程序自己完成「查 → 比 → 下 → 装」。

**只读的一半**（check）：只发一个 GET，不改任何文件。失败静默 —— 网络不通是国内
访问 GitHub 的常态，一个提词器不该因为查更新失败而打扰用户。

**会写盘的一半**（stage / apply）：只在用户点了「现在更新」之后才动。写两类地方：
    logs/update_staging/   下载解压 + 逐文件校验的临时区（logs 整体在 .gitignore 里）
    安装目录本身            由 apply_update.bat 在主程序退出后替换

**为什么要差分**：安装目录 486MB / 1581 个文件，其中自有代码只有 54 个文件 0.85MB。
每次发版真正变的通常只有几十 MB（主要是 EarShot.exe 自己）。差分包只装变化的文件 ——
这是「像应用商店一样」的全部秘密，不是 UI 做得多漂亮。

**清单约定**：release_manifest.json 里 **不包含它自己**。打包端（tools/make_release.py）
和这里都按同样的约定处理，两边一致就不会漂。
"""

import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile

from settings import BUNDLE_ROOT

MANIFEST_NAME = 'release_manifest.json'
LOCAL_MANIFEST = os.path.join(BUNDLE_ROOT, MANIFEST_NAME)

_INSTALL_ROOT = None


def install_root():
    """要替换文件的目录 = 清单里相对路径的基准（打包布局是 dist/EarShot/）。

    ⚠️ **源码模式返回 None，调用方必须拒绝继续。**

    这条不是洁癖。源码模式下 BUNDLE_ROOT 是仓库根，往上一层就是**工作区的父目录** ——
    一个算错的路径 + 一个真跑起来的替换脚本，后果是把文件复制到工作区根上。
    这个事故在写这一版的当天就真的发生过一次（测试脚本没挡住），所以这里做成硬约束：
    源码版要更新用 git pull，不该走这条路。
    """
    global _INSTALL_ROOT
    if _INSTALL_ROOT is None:
        _INSTALL_ROOT = os.path.dirname(BUNDLE_ROOT) if getattr(sys, 'frozen', False) else ''
    return _INSTALL_ROOT or None

REPO = 'Liyuze0422/EarShot'
API = 'https://api.github.com/repos/%s/releases/latest' % REPO
RELEASES_PAGE = 'https://github.com/%s/releases' % REPO
TIMEOUT = 6.0             # 查版本
DOWNLOAD_TIMEOUT = 600.0  # 下差分包
CACHE_TTL = 6 * 3600      # 6 小时。启动频繁也不会打爆 GitHub 的 60 次/小时/IP
FAIL_TTL = 600            # 失败也缓存 10 分钟，避免每次启动都干等一个超时

STAGING = None            # 由 settings 的数据根决定的临时区，见 _staging()


def _staging():
    """更新临时区。放在数据根的 logs 下 —— 那里整体被 .gitignore 挡住，不会外泄。"""
    global STAGING
    if STAGING is None:
        try:
            from settings import REPO_ROOT
            STAGING = os.path.join(REPO_ROOT, 'logs', 'update_staging')
        except Exception:
            STAGING = os.path.join(BUNDLE_ROOT, 'logs', 'update_staging')
    return STAGING


def current_version():
    """本地版本。版本号只此一处（server/version.py）。"""
    from version import __version__
    return __version__


# ---------------------------------------------------------------- 版本比较

def _parse(v):
    """v0.9.19 -> (0, 9, 19)。非数字段当 0，不抛异常。"""
    out = []
    for part in str(v).strip().lstrip('vV').split('.')[:4]:
        num = ''
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(latest, current):
    """latest 是否比 current 新。用元组比较，0.10.0 > 0.9.99 才对（字符串比会错）。"""
    return _parse(latest) > _parse(current)


# ---------------------------------------------------------------- 查版本

CACHE = None


def _cache_path():
    global CACHE
    if CACHE is None:
        CACHE = os.path.join(os.path.dirname(_staging()), 'update_cache.json')
    return CACHE


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass          # 写不进去不影响功能


def _fetch():
    req = urllib.request.Request(API, headers={
        'User-Agent': 'EarShot-update-check',
        'Accept': 'application/vnd.github+json',
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    assets = {}
    for a in data.get('assets') or []:
        assets[a.get('name', '')] = {
            'url': a.get('browser_download_url', ''),
            'size': a.get('size', 0),
        }
    return {
        'ok': True,
        'latest': (data.get('tag_name') or '').lstrip('vV'),
        'name': data.get('name') or '',
        'notes': (data.get('body') or '').strip()[:4000],
        'url': data.get('html_url') or RELEASES_PAGE,
        'published': (data.get('published_at') or '')[:10],
        'assets': assets,
        'ts': time.time(),
    }


def check(force=False):
    """查有没有新版本。**永不抛异常**。

    返回 dict：ok / current / latest / has_update / notes / url / published /
    assets / cached / error，另加两个与「能不能自己装」有关的：
        local_manifest  本地清单（源码模式为 None）
        can_self_update 能不能在程序里直接更新（要有本地清单，且远端有对得上的包）
    """
    cur = current_version()
    out = {'ok': False, 'current': cur, 'latest': '', 'has_update': False,
           'notes': '', 'url': RELEASES_PAGE, 'published': '', 'assets': {},
           'cached': False, 'error': ''}

    cached = _read_json(_cache_path())
    if cached and not force:
        age = time.time() - (cached.get('ts') or 0)
        ttl = CACHE_TTL if cached.get('ok') else FAIL_TTL
        if age < ttl:
            out.update(cached)
            out['cached'] = True

    if not out.get('ok') or force:
        try:
            fresh = _fetch()
        except Exception as e:
            # 网络不通是常态，不是错误。连异常类型都不细分，只留一句话给人看。
            fresh = {'ok': False, 'latest': '', 'name': '', 'notes': '', 'assets': {},
                     'url': RELEASES_PAGE, 'published': '', 'ts': time.time(),
                     'error': '%s: %s' % (type(e).__name__, e)}
        _write_json(_cache_path(), fresh)
        out.update(fresh)

    out['current'] = cur
    out['has_update'] = bool(out.get('ok')) and is_newer(out.get('latest', ''), cur)
    out['local_manifest'] = local_manifest()
    out['can_self_update'] = bool(out['has_update'] and out['local_manifest']
                                  and pick_patch(out) is not None)
    return out


def pick_patch(r):
    """从 release 资产里挑一个能用的包：优先差分包，退而求其次全量包。

    返回 (资产名, url, bytes, 是不是差分)。
    """
    assets = r.get('assets') or {}
    cur = r.get('current') or ''
    # 差分包名形如 EarShot-v0.9.20-patch-from-v0.9.19.zip —— 起点版本必须对得上，
    # 否则覆盖上去的是「从别的版本跳过来」的文件，比不更新还危险。
    for name, a in sorted(assets.items()):
        if '-patch-from-v%s.zip' % cur in name:
            return (name, a['url'], a['size'], True)
    for name, a in sorted(assets.items()):
        if name.endswith('-win64-full.zip'):
            return (name, a['url'], a['size'], False)
    return None


def summary(r):
    """一行话，给日志和命令行用。"""
    if not r.get('ok'):
        return '查不到新版本（%s）—— 不影响使用' % (r.get('error') or '网络不通')
    if r.get('has_update'):
        return '有新版本 %s（当前 %s，%s 发布）' % (r['latest'], r['current'], r.get('published') or '?')
    return '已是最新版本 %s' % r['current']


# ---------------------------------------------------------------- 本地清单 / 差集

def local_manifest():
    """读包内清单。**源码运行没有这个文件** —— 那种情况下只提示、不自动更新。"""
    return _read_json(LOCAL_MANIFEST)


def diff_plan(local, remote):
    """比两份清单 -> (要覆盖的路径, 要删除的路径)。

    以**清单里的哈希**为准，不是以磁盘现状为准：用户手动改过的文件在清单里仍是原始
    哈希，会被下一次更新覆盖回去 —— 这是刻意的，更新包只该装官方文件。
    """
    lf = (local or {}).get('files') or {}
    rf = (remote or {}).get('files') or {}
    changed = sorted(p for p, v in rf.items() if (lf.get(p) or [None])[0] != v[0])
    deleted = sorted(p for p in lf if p not in rf)
    return changed, deleted


# ---------------------------------------------------------------- 下载 + 暂存

def _download(url, dest, on_progress=None, timeout=DOWNLOAD_TIMEOUT):
    req = urllib.request.Request(url, headers={'User-Agent': 'EarShot-updater',
                                               'Accept': 'application/octet-stream'})
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get('Content-Length') or 0)
        with open(dest, 'wb') as f:
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if on_progress:
                    on_progress(got, total)
    return got


def stage(url, on_progress=None, on_step=None):
    """下载并解压到一个干净的临时区，逐文件校验哈希。

    返回 dict：ok / dir / files / deletes / bytes / error。
    **校验不过就整个作废** —— 半个更新包装上去比不更新更糟。
    """
    st = _staging()
    shutil.rmtree(st, ignore_errors=True)
    os.makedirs(st, exist_ok=True)
    zpath = os.path.join(st, 'patch.zip')

    def step(msg):
        if on_step:
            on_step(msg)

    step('下载中…')
    try:
        got = _download(url, zpath, on_progress=on_progress)
    except Exception as e:
        return {'ok': False, 'error': '下载失败：%s: %s' % (type(e).__name__, e)}

    step('校验中…')
    try:
        with zipfile.ZipFile(zpath) as z:
            manifest = json.loads(z.read('manifest.json').decode('utf-8'))
            files = manifest.get('files') or {}
            try:
                deletes = json.loads(z.read('deletes.json').decode('utf-8')).get('deletes') or []
            except KeyError:
                deletes = []
            names = [n for n in z.namelist() if n != 'manifest.json' and n != 'deletes.json']
            z.extractall(os.path.join(st, 'files'))
    except Exception as e:
        return {'ok': False, 'error': '解压失败：%s: %s' % (type(e).__name__, e)}

    bad = []
    for n in names:
        want = (files.get(n) or [None])[0]
        p = os.path.join(st, 'files', n.replace('/', os.sep))
        if not os.path.exists(p):
            bad.append(n + '（缺文件）')
            continue
        if want and _sha256(p) != want:
            bad.append(n + '（哈希不符）')
    if bad:
        return {'ok': False, 'error': '校验不过，已作废：%s' % '，'.join(bad[:3])}

    os.remove(zpath)
    return {'ok': True, 'dir': os.path.join(st, 'files'), 'files': names,
            'deletes': deletes, 'bytes': got, 'version': manifest.get('version', '')}


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- 应用更新

def write_apply_script(staged, version):
    """生成 apply_update.bat 的参数文件，返回 (bat 路径, 命令行参数)。

    替换动作**不能由本进程做**：exe / dll 正在被自己占用，Windows 上覆盖会失败。
    所以交给一个独立的小批处理：它等本进程退出 → 覆盖 → 删多余文件 → 重新启动。
    """
    root = install_root()
    if not root:
        raise RuntimeError('源码模式不支持应用内更新 —— 更新代码请用 git pull')
    if not os.path.isdir(os.path.join(root, '_internal')):
        raise RuntimeError('安装目录看起来不对（没有 _internal/），拒绝替换：%s' % root)

    st = staged['dir']                  # .../update_staging/files
    parent = os.path.dirname(st)
    lst = os.path.join(parent, 'deletes.txt')
    with open(lst, 'w', encoding='utf-8') as f:
        # cmd 的 del 只认反斜杠，清单里是正斜杠
        f.write('\n'.join(p.replace('/', '\\') for p in (staged.get('deletes') or [])))
    with open(os.path.join(parent, 'target_version.txt'), 'w', encoding='utf-8') as f:
        f.write(str(version))
    # **把 bat 复制到临时区再运行**：它自己也躺在安装目录里，从那里运行会在覆盖
    # 自己的时候出问题（cmd 一边读这个文件一边被替换）。临时区不在覆盖范围内。
    src = os.path.join(BUNDLE_ROOT, 'tools', 'apply_update.bat')
    bat = os.path.join(parent, 'apply_update.bat')
    shutil.copy2(src, bat)
    return bat, [root, st, lst]


PENDING = 'pending.json'


def write_pending(bat, args, version):
    """记下「已经准备好一份更新，下次启动时装」。

    为什么要绕这一下：替换发生在**程序没在跑**的时候才做得成。界面上点完更新时
    程序正开着，所以只能把文件备好、留个记号，等下次启动的**第一件事**把它装上。
    这样用户的动作就是「点更新 → 重启」，跟应用商店一样。
    """
    _write_json(os.path.join(_staging(), PENDING),
                {'bat': bat, 'args': [str(a) for a in args],
                 'version': str(version), 'ts': time.time()})


def read_pending():
    """读记号。脚本或参数文件不在了就当没有（用户可能清理过 logs）。"""
    d = _read_json(os.path.join(_staging(), PENDING))
    if not d or not d.get('bat') or not os.path.exists(d['bat']):
        return None
    if not all(os.path.exists(a) for a in d.get('args') or []):
        return None
    return d


def clear_pending():
    try:
        os.remove(os.path.join(_staging(), PENDING))
    except Exception:
        pass


def launch_apply(bat, args):
    """启动替换脚本（它就是最后一步，启动后本进程应当退出）。"""
    import subprocess
    flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(['cmd.exe', '/c', bat] + [str(a) for a in args],
                     creationflags=flags, close_fds=True)
    return True


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    r = check(force='--force' in sys.argv)
    print(summary(r))
    print('  本地清单:', '有' if r.get('local_manifest') else '无（源码模式）')
    print('  可程序内更新:', r.get('can_self_update'))
    if r.get('has_update'):
        p = pick_patch(r)
        print('  可用的包:', p[0] if p else '无', '(%.1f MB)' % (p[2] / 1048576) if p else '')
        print('-' * 60)
        print(r.get('notes', '')[:800])
        print('-' * 60)
        print(r['url'])
