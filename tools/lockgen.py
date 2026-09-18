#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""由 pip 的解析报告生成带哈希的锁文件（requirements.lock.txt）。

用法（在仓库根执行，venv 已激活）：

    # 1) 让 pip 只解析、不下载，把完整依赖闭包写成报告
    python -m pip install --dry-run --ignore-installed --report closure.json -r requirements.txt

    # 2) 用报告生成锁文件（哈希取自 PyPI 官方 JSON API，覆盖所有平台）
    python tools/lockgen.py closure.json requirements.lock.txt

    # 3) 验证锁文件与 requirements.txt 是否一致
    python tools/check_lock.py

为什么需要它：requirements.txt 只写直接依赖的版本下限，pip 每次解析都可能装到
不同的东西；锁文件把整个闭包连内容哈希一起钉死，装的时候只要有包被换过就当场报错。

⚠️ 这一步必须在你自己的干净环境里做。锁文件的可信度 = 生成它的那台机器 +
PyPI 官方 API 的可信度；它保证的是「锁住之后不会再被改」，防不了「锁的那一刻
上游就已经是坏的」。
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = 'earshot-lockgen/1'

HEADER = [
    '# EarShot 依赖锁定文件（强制哈希校验版）',
    '#',
    '# 用法：  python -m pip install -r requirements.lock.txt',
    '#   文件里已内置 --require-hashes，pip 会对每个包逐个做 sha256 校验；',
    '#   任何一个字节对不上就直接拒绝安装，不会"装一个差不多的版本"糊过去。',
    '#',
    '# hash 全部取自 PyPI 官方 JSON API（含该版本所有平台的 wheel 与源码包）。',
    '# 想用国内镜像加速也可以：pip install -r requirements.lock.txt -i <镜像地址>',
    '#   镜像若给出内容不同的文件，哈希校验会直接拦下。',
    '#',
    '# 重新生成：见 tools/lockgen.py 开头的用法说明。',
    '#',
    '# 刻意不写死 --index-url：锁的是"内容"，不是"从哪下"。',
    '# 官方源和国内镜像都能用，哈希不一致时会当场报错。',
    '#',
    '# 不能强制只用轮子：jieba 等包只发布源码包，会被整包排除掉、直接装不上。',
    '# --prefer-binary = 有轮子就用轮子，没有才退回源码包（源码包的 sha256 同样锁死）。',
    '',
    '--require-hashes',
    '--prefer-binary',
    '',
]


def fetch_pypi(name, version):
    """取某个版本在 PyPI 上的全部文件与 sha256（含各平台 wheel 与源码包）。"""
    url = 'https://pypi.org/pypi/%s/%s/json' % (name, version)
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode())
            return [(u['filename'], u['digests']['sha256'], u['packagetype']) for u in data['urls']], None
        except Exception as exc:  # 网络抖动就重试，不掩盖最终失败
            last = exc
            time.sleep(1.2 + attempt)
    return [], last


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    report_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else 'requirements.lock.txt'

    with open(report_path, encoding='utf-8') as fp:
        items = json.load(fp)['install']
    print('闭包包数: %d' % len(items))

    tasks = [(it['metadata']['name'], it['metadata']['version']) for it in items]
    resolved = {}
    errors = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for (name, version), (files, err) in zip(tasks, pool.map(lambda a: fetch_pypi(*a), tasks)):
            resolved[(name, version)] = files
            if err:
                errors.append('%s==%s: %s' % (name, version, err))
            print('  %-26s %-12s %3d 个文件%s' % (name, version, len(files), '  ERR' if err else ''))

    if errors:
        print('')
        print('!! 有包没取到官方哈希，锁文件不完整，已中止:')
        for e in errors:
            print('   ', e)
        return 1

    # 交叉校验：本机解析到的那个文件，其 sha256 必须出现在官方列表里
    bad = []
    for it in items:
        meta = it['metadata']
        want = (it['download_info'].get('archive_info', {}).get('hashes', {}) or {}).get('sha256')
        have = {h for _, h, _ in resolved.get((meta['name'], meta['version']), [])}
        if want and want not in have:
            bad.append('%s==%s 本机 sha256 不在官方列表里' % (meta['name'], meta['version']))
    if bad:
        print('')
        print('!! 交叉校验失败（解析到的文件与官方不一致，先别用）:')
        for b in bad:
            print('   ', b)
        return 1
    print('')
    print('交叉校验通过：本机解析到的每个包，其 sha256 都在 PyPI 官方文件列表里。')

    lines = list(HEADER)
    total_hashes = 0
    for it in sorted(items, key=lambda x: x['metadata']['name'].lower()):
        meta = it['metadata']
        name, version = meta['name'], meta['version']
        files = resolved[(name, version)]
        hashes = sorted({h for _, h, kind in files if kind in ('bdist_wheel', 'sdist')})
        if not hashes:
            print('!! %s==%s 没有可用哈希，跳过' % (name, version))
            continue
        summary = (meta.get('summary') or '').strip()
        if summary:
            lines.append('# %s' % summary[:70])
        backslash = chr(92)   # pip 要求 --hash 用续行符绑定到同一条 requirement 上
        lines.append('%s==%s %s' % (name, version, backslash))
        for idx, digest in enumerate(hashes):
            if idx < len(hashes) - 1:
                lines.append('    --hash=sha256:%s %s' % (digest, backslash))
            else:
                lines.append('    --hash=sha256:%s' % digest)
        lines.append('')
        total_hashes += len(hashes)

    with open(out_path, 'w', encoding='utf-8', newline=chr(10)) as fp:
        fp.write(chr(10).join(lines))
    print('已写出: %s' % out_path)
    print('总包数: %d  总哈希数: %d  平台覆盖: %d' % (len(items), total_hashes, len(lines)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
