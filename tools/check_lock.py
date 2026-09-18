
# -*- coding: utf-8 -*-
"""门禁：检查 requirements.lock.txt 与 requirements.txt 是否一致、哈希是否齐全。

放进 CI 后，任何人改了 requirements.txt 却忘了重生成锁文件，push 就会红。
用法:  python tools/check_lock.py     （退出码 0 = 通过）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, 'requirements.txt')
LOCK = os.path.join(ROOT, 'requirements.lock.txt')

def norm(n):
    return re.sub(r'[-_.]+', '-', n).lower()

def direct_requirements(path):
    """requirements.txt 里的直接依赖名（去掉注释、-r、选项行）。"""
    names = []
    for line in open(path, encoding='utf-8'):
        line = line.split('#')[0].strip()
        if not line or line.startswith('-'):
            continue
        m = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)', line)
        if m:
            names.append(norm(m.group(1)))
    return names

def locked_packages(path):
    out = {}
    for line in open(path, encoding='utf-8'):
        m = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)', line.strip())
        if m:
            out[norm(m.group(1))] = m.group(2)
    return out

problems = []

if not os.path.isfile(LOCK):
    print('FAIL 找不到 requirements.lock.txt')
    sys.exit(1)

lock_text = open(LOCK, encoding='utf-8').read()
if '--require-hashes' not in lock_text:
    problems.append('锁文件里没有 --require-hashes，等于没有强制校验')

locked = locked_packages(LOCK)
print('锁文件包数: %d' % len(locked))

# 1. 每个包都必须带哈希
blocks = re.split(r'\n(?=[A-Za-z0-9][A-Za-z0-9._-]*==)', lock_text)
for b in blocks:
    m = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==', b)
    if m and '--hash=sha256:' not in b:
        problems.append('%s 没有哈希' % m.group(1))

# 2. requirements.txt 的直接依赖必须都在锁文件里
missing = [n for n in direct_requirements(REQ) if n not in locked]
if missing:
    problems.append('requirements.txt 里有、锁文件里没有: %s（是不是改了依赖忘了重生成锁文件？）' % missing)

# 3. 锁定的版本必须满足 requirements.txt 的版本下限
for line in open(REQ, encoding='utf-8'):
    line = line.split('#')[0].strip()
    m = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)\s*>=\s*([0-9][0-9A-Za-z.]*)', line)
    if not m:
        continue
    name, low = norm(m.group(1)), m.group(2)
    got = locked.get(name)
    if got is None:
        continue
    try:
        def tup(s):
            return tuple(int(x) for x in re.findall(r'\d+', s)[:3])
        if tup(got) < tup(low):
            problems.append('%s 锁的是 %s，低于 requirements 要求的 >=%s' % (name, got, low))
    except Exception:
        pass

print('检查项: 哈希齐全 / 直接依赖覆盖 / 版本下限')
if problems:
    print('FAIL —— %d 个问题:' % len(problems))
    for p in problems:
        print('   !! ' + p)
    sys.exit(1)
print('PASS —— 锁文件与 requirements.txt 一致，且每个包都有哈希。')
