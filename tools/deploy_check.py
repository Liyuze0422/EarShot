# -*- coding: utf-8 -*-
"""经历/部署味道自检 —— 材料改完一键跑，看提词器答的是"做过的事"还是"道理"。

为什么需要它：材料里只有技术方案、没有现场动作时，提词器会退化成
"我会先定边界""一般我会先搭骨架"这类**没做过事也能说的话**。
实测（2026-09-18）：四条经历味的问题，四条答案的【数字】栏全是"无"。

这个脚本用固定几条"经历味"的问题真跑一遍快答线，逐条检查四件事：
  1. 有没有具体数字（【数字】栏不是"无"）
  2. 有没有具体动作（抓字节/加了一层/灰度/跑通…）
  3. 有没有环境或对手方（客户内网/厂商/硬件/现场…）
  4. 有没有空话句式（"我会先…""一般我会…""要分情况看"）—— 出现即该条不合格

用法:
    python tools\\deploy_check.py                      # 跑内置问题，不达标退出码 1
    python tools\\deploy_check.py --show               # 顺带打印每条答案全文
    python tools\\deploy_check.py --json               # 机器可读（给 CI / 别的脚本）
    python tools\\deploy_check.py --questions q.json   # 自定义问题（{"questions": [...]}）
    python tools\\deploy_check.py --min-num 0.75      # 要求含数字的条目占比（默认 0.5）

注意：它会**真的调一次大模型**（每条一次），所以不进 pytest/CI —— 这是材料改完后
手动跑的自检，和 tools/regress.py 一样属于"要花钱的门禁"。
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import answer as A  # noqa: E402

# 默认问题：覆盖"部署 / 对接 / 工期 / 交付"四个最容易被答成理论的场景。
# 前四条是 2026-09-18 实测的那四条，后两条是补充的现场味问法。
DEFAULT_QUESTIONS = [
    '你在客户现场部署过吗？',
    '跟硬件对接的时候你遇到过什么问题？',
    '你怎么评估一个项目的开发周期？',
    '你们的系统最后是怎么交付上线的？',
    '现场出过一次什么问题，你是怎么定位的？',
    '没真机 / 没真实环境的时候，你怎么验证能用？',
]

EMPTY_NUM = re.compile(r'^[\s无]*$')
# 具体动作：能让人照着复现的那种动词/名词
ACTION = re.compile(r'(抓|比对|复现|加了一层|加了|写了一|改成|装了|接了|拉了|灰度|联调|握手|心跳|'
                    r'定位到|逐字节|实测|跑通|排查|拆成|留了|拆出|代理层|最小复现)')
# 环境 / 对手方：没有它，动作就悬空
CONTEXT = re.compile(r'(客户|甲方|厂商|硬件|平台方|现场|内网|私有化|仿真|SITL|飞控|地面站|实测|联调|真实)')
# 空话句式：没做过事也能说的话，出现即不合格
FILLER = re.compile(r'(我会先|一般我会|我一般会|要分情况|看具体情况|具体要看|看具体场景|'
                    r'我理解最重要的是|理论上|原则上|首先我会)')


def parse(text):
    """拆出【数字】栏和正文（正文不含数字栏，避免数字栏自己把"有没有数字"刷成 true）。"""
    m = re.search(r'【数字】\s*(.*)', text, re.S)
    if not m:
        return text, ''
    return text[:m.start()], m.group(1).strip()


def judge(text):
    body, nums = parse(text)
    has_num = bool(nums) and not EMPTY_NUM.match(nums)
    actions = sorted(set(ACTION.findall(body)))
    contexts = sorted(set(CONTEXT.findall(body)))
    fillers = sorted(set(FILLER.findall(body)))
    ok = (not fillers) and bool(actions or contexts)
    return {
        'has_num': has_num, 'nums': nums,
        'actions': actions, 'contexts': contexts, 'fillers': fillers,
        'ok': ok, 'chars': len(text),
    }


def load_questions(path):
    d = json.load(open(path, encoding='utf-8'))
    items = d.get('questions', d) if isinstance(d, dict) else d
    out = []
    for it in items:
        out.append(it if isinstance(it, str) else it.get('text') or it.get('q'))
    return [q for q in out if q]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--questions', help='自定义问题 JSON（{"questions": [...]}）')
    ap.add_argument('--json', action='store_true', help='输出机器可读结果')
    ap.add_argument('--show', action='store_true', help='打印每条答案全文')
    ap.add_argument('--min-num', type=float, default=0.5, help='含具体数字的条目占比下限（默认 0.5）')
    args = ap.parse_args()

    if not A.load_key():
        print('没有配置 API Key，快答线跑不起来（离线兜底答案不适用本检查）。')
        print('先填 config/api_key.txt，或设置环境变量后再跑。')
        return 2

    questions = load_questions(args.questions) if args.questions else DEFAULT_QUESTIONS
    rows = []
    for q in questions:
        try:
            text, first, total, _qt, _hits = A.answer_stream(q)
        except Exception as e:  # 网络/超时都不该让整轮自检作废
            rows.append({'q': q, 'error': str(e)[:200]})
            continue
        r = judge(text or '')
        r['q'] = q
        r['text'] = text or ''
        r['first_ms'] = int(first * 1000) if first else None
        rows.append(r)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print('== 经历/部署味道自检（%d 条）==' % len(rows))
        for r in rows:
            if r.get('error'):
                print('[ERR ] %s\n       %s' % (r['q'], r['error']))
                continue
            print('[%s] %s' % ('PASS' if r['ok'] else 'FAIL', r['q']))
            print('       数字: %s | 动作: %s | 环境: %s | 空话: %s' % (
                ('、'.join(r['nums'].split(','))[:60] if r['has_num'] else '无'),
                '、'.join(r['actions'][:5]) or '—',
                '、'.join(r['contexts'][:5]) or '—',
                '、'.join(r['fillers']) or '0',
            ))
            if args.show:
                print('       ' + (r['text'] or '').replace('\n', '\n       '))

    ok_rows = [r for r in rows if not r.get('error')]
    n_ok = sum(1 for r in ok_rows if r['ok'])
    n_num = sum(1 for r in ok_rows if r['has_num'])
    ratio = (n_num / len(ok_rows)) if ok_rows else 0.0
    passed = bool(ok_rows) and n_ok == len(ok_rows) and ratio >= args.min_num

    if not args.json:
        print('-' * 60)
        print('逐条通过 %d/%d · 含具体数字 %d/%d（%.0f%%，下限 %.0f%%）· 报错 %d 条' % (
            n_ok, len(ok_rows), n_num, len(ok_rows), ratio * 100, args.min_num * 100,
            len(rows) - len(ok_rows)))
        if passed:
            print('结论: 通过 —— 答案落在"我做过的事"上，材料够用。')
        else:
            print('结论: 不通过 —— 材料里的现场经历还不够，答案退回了道理。')
            print('      补 knowledge/_base/现场部署与交付_软件侧实战.md（见 docs/知识库整理清单.md 第九节）。')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
