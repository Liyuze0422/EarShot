# -*- coding: utf-8 -*-
r"""把打包好的目录变成可以发布的资产（清单 / 全量包 / 差分包）。

    python tools\make_release.py --dir dist\EarShot --version 0.9.20 --out build\release
    python tools\make_release.py --dir dist\EarShot --version 0.9.20 --out build\release ^
        --prev build\release\manifest-v0.9.19.json     # 额外生成差分包
    python tools\make_release.py --dir dist\EarShot --version 0.9.20 --out build\release --full

**为什么要有它**：Release 里放一个 486MB 的 zip，用户每次更新都得重下 486MB —— 而其中
**会变的只有自己的代码**（dist 里 1581 个文件，自有代码只有 52 个，约 1.3MB）。
这个脚本算出「哪些文件跟上一版不一样」，只把它们打进差分包，把每次更新的下载量
从 486MB 降到几十 MB。

**manifest 的形状**（客户端按它决定要下哪些文件）：

    {
      "version": "0.9.20",
      "created": "2026-09-19T09:00:00",
      "total_bytes": 509000000,
      "files": {"EarShot.exe": ["<sha256>", 34721524], ...}
    }

**清单里不包含 manifest 自己**（否则它的哈希无从定义）。客户端读本地 manifest 时
按同样的约定忽略自己那一项 —— 两边约定一致就不会漂。

**差分包里的结构**：

    manifest.json     新版本的完整清单
    files/...         需要覆盖的文件（保持相对路径）
    deletes.json      需要删除的相对路径列表（老版本有、新版本没有的）
"""

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile

MANIFEST_NAME = 'release_manifest.json'      # 打进包里的清单文件名（在 _internal/ 下）


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def rel(p):
    """统一成正斜杠的相对路径 —— Windows 上写 zip 必须这样，否则解压出来是怪路径。"""
    return p.replace('\\', '/')


def scan(root, skip=()):
    """扫目录 -> {'相对路径': [sha256, 字节数]}。skip 里的相对路径不算进去。"""
    files = {}
    skip = {rel(s) for s in skip}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            r = rel(os.path.relpath(full, root))
            if r in skip:
                continue
            try:
                files[r] = [sha256(full), os.path.getsize(full)]
            except OSError as e:
                print('  ! 跳过 %s (%s)' % (r, e))
    return files


def write_manifest(files, version, out_path):
    manifest = {
        'version': version,
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'total_bytes': sum(v[1] for v in files.values()),
        'files': dict(sorted(files.items())),
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(',', ':'))
    return manifest


def diff(prev, cur):
    """比两份清单 -> (要覆盖的文件, 要删除的文件)。"""
    changed = [p for p, v in cur.items() if prev.get(p, [None])[0] != v[0]]
    deleted = [p for p in prev if p not in cur]
    return sorted(changed), sorted(deleted)


def zip_files(root, rel_paths, out_zip, extra_entries=(), level=6):
    """把 rel_paths 里列的文件按相对路径打进 zip；extra_entries 是 (arcname, 内容 bytes)。"""
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for r in rel_paths:
            z.write(os.path.join(root, r.replace('/', os.sep)), r)
        for arcname, data in extra_entries:
            z.writestr(arcname, data)


def human(n):
    return '%.1f MB' % (n / 1048576.0) if n >= 1048576 else '%.1f KB' % (n / 1024.0)


def main():
    ap = argparse.ArgumentParser(description='生成发布资产（清单 / 全量包 / 差分包）')
    ap.add_argument('--dir', required=True, help='打包好的目录，例如 dist\\EarShot')
    ap.add_argument('--version', required=True, help='版本号，例如 0.9.20')
    ap.add_argument('--out', required=True, help='产物输出目录')
    ap.add_argument('--prev', help='上一版的 manifest.json；给了它才会做差分包')
    ap.add_argument('--full', action='store_true', help='同时打全量包（486MB，比较慢）')
    ap.add_argument('--embed', action='store_true',
                    help='把清单写进包目录（%s），让程序知道自己是哪一版' % MANIFEST_NAME)
    a = ap.parse_args()

    root = os.path.abspath(a.dir)
    out = os.path.abspath(a.out)
    if not os.path.isdir(root):
        print('目录不存在：%s' % root)
        return 2
    os.makedirs(out, exist_ok=True)

    # 包里的清单永远排除它自己
    skip = (os.path.join('_internal', MANIFEST_NAME),) if a.embed else ()
    print('扫描 %s …' % root)
    t0 = time.time()
    files = scan(root, skip=skip)
    total = sum(v[1] for v in files.values())
    print('  %d 个文件，%s，用时 %.1fs' % (len(files), human(total), time.time() - t0))

    mp = os.path.join(out, 'manifest-v%s.json' % a.version)
    write_manifest(files, a.version, mp)
    print('清单 -> %s (%s)' % (os.path.basename(mp), human(os.path.getsize(mp))))

    if a.embed:
        dest = os.path.join(root, '_internal', MANIFEST_NAME)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w', encoding='utf-8') as f:
            json.dump({'version': a.version, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
                       'total_bytes': total, 'files': dict(sorted(files.items()))},
                      f, ensure_ascii=False, separators=(',', ':'))
        print('包内清单 -> _internal/%s' % MANIFEST_NAME)

    if a.prev:
        with open(a.prev, encoding='utf-8') as f:
            prev = json.load(f)['files']
        changed, deleted = diff(prev, files)
        prev_ver = json.load(open(a.prev, encoding='utf-8')).get('version', 'prev')
        pz = os.path.join(out, 'EarShot-v%s-patch-from-v%s.zip' % (a.version, prev_ver))
        body = json.dumps({'version': a.version, 'files': dict(sorted(files.items()))},
                          ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        dels = json.dumps({'deletes': deleted}, ensure_ascii=False).encode('utf-8')
        zip_files(root, changed, pz, extra_entries=[('manifest.json', body), ('deletes.json', dels)])
        patch_bytes = sum(files[p][1] for p in changed)
        print('差分包 -> %s' % os.path.basename(pz))
        print('  覆盖 %d 个文件（%s）· 删除 %d 个 · 包体 %s'
              % (len(changed), human(patch_bytes), len(deleted), human(os.path.getsize(pz))))
        if patch_bytes > total * 0.5:
            print('  ⚠ 差分包超过全量的 50% —— 这次多半动了依赖，用户会下很多')

    if a.full:
        fz = os.path.join(out, 'EarShot-v%s-win64-full.zip' % a.version)
        print('全量包（%s，慢）…' % human(total))
        t0 = time.time()
        zip_files(root, sorted(files), fz, level=1)
        print('  全量包 -> %s (%s, 用时 %.0fs)'
              % (os.path.basename(fz), human(os.path.getsize(fz)), time.time() - t0))

    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
