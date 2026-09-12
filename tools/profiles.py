# -*- coding: utf-8 -*-
"""资料包（corpus profile）管理：建包 / 看包 / 试包。

为什么要有这个：实测（_realdata/_work/bench_dilute*.py）往知识库里掺"讲同一批话题"的
文本会直接抢排名——掺 25% 本人面试录音，hit@1 从 89.7% 塌到 41.0%；而掺 2 倍**异题材**
文档只掉 2.5。所以个人经历材料要按面试分包，不能混在一起。

目录约定：
    knowledge/_base/        所有面试通用的个人材料（项目报告、简历、数字卡片…）
    knowledge/<包名>/        某家公司/某轮专用（JD、公司介绍、面经、岗位技术栈）
    knowledge/              直接平铺（不分包）时 = 老行为，扫全部

用法：
    python tools/profiles.py --list                 看有哪些包、各多少字
    python tools/profiles.py --show                 看当前生效的是哪个包、实际 glob 是什么
    python tools/profiles.py --new 字节跳动           建一个新包（含 company.md 模板）
    python tools/profiles.py --check 字节跳动         试算：切到这个包会加载多少块
    python tools/profiles.py --use 字节跳动          打印启动命令（不重启当前进程）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))

import knowledge   # noqa: E402
import settings    # noqa: E402


def _tree(root):
    """一个包里有多少份材料、多少字。"""
    n = chars = 0
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fn in filenames:
            if fn.startswith('_'):
                continue
            if not fn.lower().endswith(('.md', '.txt')):
                continue
            p = os.path.join(dirpath, fn)
            try:
                t = open(p, encoding='utf-8', errors='ignore').read()
            except Exception:
                continue
            n += 1
            chars += len(t)
            files.append(os.path.relpath(p, root))
    return n, chars, files


COMPANY_TPL = """# 目标公司背景（每换一家就改这里）

> 这个文件有两个用处：① 作为「公司背景」直接注入提示词；② 本身也会被检索。
> 以 # 或 > 开头的行不会被注入，只当注释用。

公司：<公司全名>
岗位：<岗位名 + 职级>
面试轮次：<一面 / 二面 / 终面 / HR 面>
主营与技术栈：<一句话：做什么的、主力技术栈>
产品线：<和岗位相关的产品/业务线>
我为什么想来：<写成能直接说出口的 2~3 句>
我想反问的：<准备问对方的 2~3 个问题>
"""


def cmd_list():
    root = settings.knowledge_root()
    print('知识库根目录: %s' % root)
    if not os.path.isdir(root):
        print('  （目录不存在，先建它并把材料放进去）')
        return 0
    print()
    print('  %-24s %6s %10s' % ('资料包', '份数', '字数'))
    print('  ' + '-' * 44)
    base = os.path.join(root, '_base')
    if os.path.isdir(base):
        n, c, _ = _tree(base)
        print('  %-24s %6d %10d   <- 所有面试通用' % ('_base', n, c))
    profiles = knowledge.list_profiles()
    for p in profiles:
        n, c, _ = _tree(os.path.join(root, p))
        print('  %-24s %6d %10d' % (p, n, c))
    if not profiles:
        print('  （还没有资料包。建一个：python tools/profiles.py --new 公司名）')
    flat = [f for f in os.listdir(root)
            if not f.startswith('_') and os.path.isfile(os.path.join(root, f))
            and f.lower().endswith(('.md', '.txt'))]
    if flat:
        print()
        print('  注意：knowledge/ 下还有 %d 个平铺文件（%s）。'
              % (len(flat), '、'.join(flat[:3]) + ('…' if len(flat) > 3 else '')))
        print('  启用资料包后这些**不会**被检索到 —— 想通用就移进 _base/。')
    return 0


def cmd_show():
    prof = knowledge.active_profile()
    print('当前资料包: %s' % (prof or '（未启用，扫整个 knowledge/）'))
    for line in settings.describe().splitlines():
        if 'corpus' in line:
            print('  ' + line.strip())
    print('实际 glob:')
    for g in knowledge.corpus_globs():
        print('  ' + g)
    docs = knowledge.load_docs()
    chunks = []
    for name, text in docs:
        chunks += knowledge.chunk(text, name)
    print('=> %d 份材料 / %d 块' % (len(docs), len(chunks)))
    return 0


def cmd_new(name):
    root = settings.knowledge_root()
    pack = os.path.join(root, name)
    if os.path.isdir(pack) and os.listdir(pack):
        print('包已存在且非空：%s' % pack)
        return 1
    os.makedirs(pack, exist_ok=True)
    cpath = os.path.join(pack, 'company.md')
    if not os.path.exists(cpath):
        open(cpath, 'w', encoding='utf-8').write(COMPANY_TPL)
    print('已建包: %s' % pack)
    print('  company.md   <- 填公司背景（会注入提示词，也会被检索）')
    print()
    print('接下来把该公司的资料丢进这个目录就行（.md / .txt）：')
    print('  JD.md、公司介绍.md、一面面经.md ……')
    print('  文件名以 _ 开头的不会参与检索（放笔记/待办可以这么命名）。')
    print()
    if not os.path.isdir(os.path.join(root, '_base')):
        print('提示：还没建 knowledge/_base/ —— 把你的通用个人材料（项目报告、简历、')
        print('      数字卡片…）移进去，否则切包之后它们就检索不到了。')
    return 0


def cmd_check(name):
    old = knowledge.active_profile()
    knowledge.set_profile(name)
    docs = knowledge.load_docs()
    chunks = []
    for n, text in docs:
        chunks += knowledge.chunk(text, n)
    print('切到【%s】后：%d 份材料 / %d 块' % (name or '（关掉分包）', len(docs), len(chunks)))
    for n, _ in docs:
        print('  - %s' % n)
    knowledge.set_profile(None if old == '' else old)
    return 0


def cmd_use(name):
    print('切换方式（三选一）：')
    print()
    print('  1) 临时（只对这条命令起的进程生效）：')
    print('       set TP_CORPUS_PROFILE=%s' % name)
    print('       python -m server.main        （或你平时的启动方式）')
    print()
    print('  2) 长期：编辑 config/settings.json，加一行')
    print('       "corpus_profile": "%s"' % name)
    print()
    print('  3) 运行时（需要已经在跑的进程支持，GUI 里点切换）：')
    print('       knowledge.set_profile("%s")' % name)
    print()
    print('改完要重启后端才生效（索引是启动时建的）。')
    return 0


def main():
    ap = argparse.ArgumentParser(description='资料包管理')
    ap.add_argument('--list', action='store_true', help='列出所有资料包')
    ap.add_argument('--show', action='store_true', help='显示当前生效的包和实际语料范围')
    ap.add_argument('--new', metavar='NAME', help='新建资料包')
    ap.add_argument('--check', metavar='NAME', help='试算切到某个包会加载什么')
    ap.add_argument('--use', metavar='NAME', help='打印切换命令')
    a = ap.parse_args()
    if a.new:
        return cmd_new(a.new)
    if a.check is not None:
        return cmd_check(a.check)
    if a.use:
        return cmd_use(a.use)
    if a.show:
        return cmd_show()
    return cmd_list()


if __name__ == '__main__':
    sys.exit(main())
