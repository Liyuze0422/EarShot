# -*- coding: utf-8 -*-
"""DSH 深答线:调 `dsh --profile headless` 让 harness 本体带工具回答。"""
import os
import sys
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings

# 深答线是可选的：node / dsh bin / DSH_HOME 三个路径全部配置了才启用。
# 配置位置：config/settings.json 的 dsh_node / dsh_bin / dsh_home，
# 或环境变量 TP_DSH_NODE / TP_DSH_BIN / TP_DSH_HOME。留空＝关闭这个功能。
NOT_CONFIGURED = ('<深答线未配置：在 config/settings.json 里填 dsh_node/dsh_bin/dsh_home，'
                  '或忽略此功能>')

TASK_TMPL = '''这是一场线上面试，面试官刚问了以下问题。请给我一份可以直接照念的口语化回答。

面试官提问: {question}

{company}
要求:
1. 重点是【思路】而不是具体做法:先说这类问题的关键判断和取舍原则,再给一个落点。
2. 如果问题涉及对方公司的业务/场景,请结合他们的实际情况具体分析,不要讲通用套话。
3. 口语化,像人在讲自己的判断,不要分点符号,不要罗列技术名词。
4. 严格按三段输出,不要任何多余文字:
   【思路】一句话点出关键判断,40 字以内
   【拆解】3 到 4 句,讲我按什么维度想、怎么取舍,150 字以内
   【落点】一两句,如果真做我会先从哪一步动手
'''


def build_task(question, company_ctx=''):
    c = ''
    if company_ctx and company_ctx.strip():
        c = '<公司背景>\n' + company_ctx.strip() + '\n</公司背景>'
    return TASK_TMPL.format(question=question, company=c)


def ask_deep(question, company_ctx='', timeout=90):
    paths = settings.dsh_paths()
    if not paths:
        return NOT_CONFIGURED, 0.0, 1
    node, dsh_bin, dsh_home = paths
    task = build_task(question, company_ctx)
    env = os.environ.copy()
    env['DSH_HOME'] = dsh_home
    env['PYTHONIOENCODING'] = 'utf-8'
    t0 = time.time()
    try:
        p = subprocess.run([node, dsh_bin, '--profile', 'headless', task],
                           capture_output=True, env=env, timeout=timeout,
                           encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        return '<超时未返回>', time.time() - t0, 1
    dt = time.time() - t0
    out = (p.stdout or '').strip()
    if p.returncode != 0 and not out:
        out = '<失败 exit=%d> %s' % (p.returncode, (p.stderr or '')[-400:])
    return out, dt, p.returncode


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    q = sys.argv[1] if len(sys.argv) > 1 else '如果让你为一家跨境电商公司设计智能客服 Agent，你会怎么搭？'
    ctx = sys.argv[2] if len(sys.argv) > 2 else ''
    print('问题:', q)
    print('-' * 62)
    txt, dt, code = ask_deep(q, ctx)
    print(txt)
    print('-' * 62)
    print(f'耗时 {dt:.1f}s   exit={code}')
