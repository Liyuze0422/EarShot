# -*- coding: utf-8 -*-
"""回归门禁：改任何东西之前/之后跑这一个脚本。

2026-09-11 这一轮之所以能挖出 5 个真实缺陷，就是因为第一次有了真实语料 + 可量化的跑法。
没有基线就没有优化——这个脚本把基线固定下来，不达标就退出码非 0。

这个脚本吃的是**你自己的测试集**，它们不随仓库发布（里面有你的真实面试问题与答案）：
  tests/real_questions.json          真实提问（i / text / label）
  tests/synthetic_questions.json     合成提问
  tests/regress_expect.json          期望命中的材料名片段（模板见 regress_expect.example.json）
  tools/test_gap_fp.py / test_answer_check.py / test_faults.py / test_hybrid.py
缺哪一份就跳过哪一项，并明确打印「跳过」——不伪装成通过，也不直接抛 traceback。
一份数据都没有时退出码 2（= 没有数据可判，不等于通过）。

用法:
  python tools/regress.py           # 快（约 20 秒）
  python tools/regress.py --e2e     # 再加端到端全量回放（约 80 秒，会真调模型）
"""
import sys, os, json, argparse, subprocess
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))

# 回归期望集：键 = 问题编号（对应 tests/real_questions.json 里每条问题的 i），
# 值 = 期望命中的材料文件名片段列表。它跟你的私人测试集一一对应，所以不随仓库发布：
# 放在 tests/regress_expect.json（已 .gitignore），仓库里只留虚构示例 regress_expect.example.json。
EXPECT_FILE = os.path.join(ROOT, 'tests', 'regress_expect.json')
EXPECT_EXAMPLE = os.path.join(ROOT, 'tests', 'regress_expect.example.json')


def load_expect():
    """读回归期望集。没有这个文件不是错误，只是没得测。"""
    if not os.path.exists(EXPECT_FILE):
        print('没有找到 tests/regress_expect.json —— 这是你自己的一份回归期望集'
              '（键=问题编号，值=期望命中的材料文件名片段列表）。')
        print('参考 %s（直接复制过去改就行）' % os.path.relpath(EXPECT_EXAMPLE, ROOT))
        return {}
    with open(EXPECT_FILE, encoding='utf-8') as f:
        raw = json.load(f)
    # 示例文件里带 "_说明" 这类非数字键，跳过它们，方便直接把示例复制成自己的期望集
    return {int(k): list(v) for k, v in raw.items() if str(k).strip().lstrip('-').isdigit()}


GOLD = load_expect()
# 阈值 = 当前基线往下留一点余量，掉下去就说明引入了回归
# 基线（2026-09-11 真实一面回放，39 条有明确材料）：
#   检索 hit@1 84.6% / hit@3 92.3%   题库并集 hit@3 会更高
#   阈值 = 基线往下留余量，掉下去就是引入了回归
LIMITS = {
    '路由准确率(真实)': 95.0, '路由准确率(合成)': 95.0, '非提问拦截': 95.0,
    '检索 hit@1': 78.0, '检索 hit@3': 86.0, '题库并集 hit@3': 88.0,
    'gap 判据(误报0/硬案例全中)': 100.0,
    '答案后校验(数字/经历)': 100.0,
    '故障注入(串题/看门狗/自愈/端口)': 100.0,
    # 下面两项只在 --quality 时出现（真调模型，默认不跑）
    '答案质量均分(1~5)': 76.0,          # = 3.8/5，基线 4.09
    '低分答案比例(≤2分, 越低越好)': None,   # 单独判，见 main
}
MAX_LOW_RATIO = 20.0      # 判分 ≤2 分的比例上限（基线 0~11%）

_MISSING = set()


def load(f):
    """读 tests/ 下的一份问题集。缺文件不算崩溃，只是「这份私测数据没随仓库发布」。"""
    p = os.path.join(ROOT, 'tests', f)
    if not os.path.exists(p):
        if f not in _MISSING:
            _MISSING.add(f)
            print('跳过 %s：没有这份数据（它是你自己的测试集，不随仓库发布）。' % f)
        return []
    try:
        with open(p, encoding='utf-8') as fp:
            return json.load(fp)['questions']
    except Exception as e:
        print('读取 %s 失败：%s' % (f, e))
        return []


def check_router():
    from router import classify
    out = {}
    for tag, fn in (('真实', 'real_questions.json'), ('合成', 'synthetic_questions.json')):
        qs = load(fn)
        if not qs:                    # 没数据就跳过，别把 0/0 算成 0% 再判成回归
            continue
        ok = tot = sp = sr = 0
        for q in qs:
            got = classify(q['text']); exp = q['label']
            if exp == 'skip':
                sr += 1; sp += (got == 'skip'); continue
            tot += 1; ok += (got == exp)
        out['路由准确率(%s)' % tag] = ok * 100.0 / max(tot, 1)
        if tag == '真实':
            out['非提问拦截'] = sp * 100.0 / max(sr, 1)
    return out


def check_retrieval():
    if not GOLD:                      # 没有期望集就没什么可判（原因见 load_expect 的提示）
        return {}
    from knowledge import build
    import answer as A
    try:
        import bank as B
    except Exception:
        B = None
    qs = load('real_questions.json')
    h1 = h3 = u3 = n = 0
    asked = []
    for q in qs:
        gold = GOLD.get(q['i'])
        if not gold:
            asked.append(q['text']); continue
        n += 1
        hist = asked[-8:]          # 和线上 main.py 一致：检索用后2句，话题持久化看后8句
        asked.append(q['text'])
        _, hits = A.retrieve(q['text'], history=hist)
        msrc = [s for _, s, _ in hits]
        g = lambda ss: any(any(x in s for x in gold) for s in ss)
        h1 += g(msrc[:1]); h3 += g(msrc[:3])
        ok3 = g(msrc[:3])
        if not ok3 and B:
            ok3 = g([h['src'] for h in B.lookup(q['text'], topk=3)])
        u3 += ok3
    return {'检索 hit@1': h1 * 100.0 / n, '检索 hit@3': h3 * 100.0 / n, '题库并集 hit@3': u3 * 100.0 / n}


def check_sub(name, script):
    """跑一个离线判据脚本当门禁项（秒级、不调模型）。脚本不在仓库里就跳过。"""
    if not os.path.exists(os.path.join(HERE, script)):
        print('跳过 %s：仓库里没有 tools/%s（它依赖你自己的测试数据）。' % (name, script))
        return {}
    r = subprocess.run([sys.executable, '-X', 'utf8', os.path.join(HERE, script)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print(r.stdout[-1200:])
    return {name: 100.0 if r.returncode == 0 else 0.0}


def check_quality(sample=10, workers=4):
    """抽 N 条真实提问真跑一遍并判分 —— 提示词改坏了要能看出来。

    默认不跑（要调 2N 次模型，约 30 秒）；面试前一天跑一次：
        python tools/regress.py --quality 10
    """
    if not GOLD:                      # 没有期望集就没什么可判（原因见 load_expect 的提示）
        return {}
    import answer as A
    try:
        import judge_answers as J
    except Exception as e:
        print('判分器不可用: %s' % e)
        return {}
    qs = [q for q in load('real_questions.json') if GOLD.get(q['i'])][:sample]
    rows = []
    for k, q in enumerate(qs):
        prev = [x['q'] for x in rows[max(0, k - 2):k]]
        try:
            text, first, total, qt, hits = A.answer_stream(q['text'], history=prev)
        except Exception as e:
            text = '<失败: %s>' % e
        rows.append({'i': q['i'], 'q': q['text'], 'answer': text, 'qtype': qt if 'qt' in dir() else ''})
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for k, r_ in enumerate(rows):
            prev = [x['q'] for x in rows[max(0, k - 2):k]]
            futs[ex.submit(J.judge, r_, prev)] = r_
        for f in as_completed(futs):
            futs[f].update(f.result())
    scores = [r_['score'] for r_ in rows if r_['score']]
    if not scores:
        return {}
    avg = sum(scores) / float(len(scores))
    low = sum(1 for s in scores if s <= 2) * 100.0 / len(scores)
    print('  抽样 %d 条：均分 %.2f · ≤2 分 %d 条' % (len(scores), avg, sum(1 for s in scores if s <= 2)))
    for r_ in sorted(rows, key=lambda x: x.get('score') or 9)[:3]:
        print('    #%-3s %s分 %s' % (r_['i'], r_.get('score'), (r_.get('reason') or '')[:40]))
    json.dump(rows, open(os.path.join(ROOT, 'tests', 'quality_sample.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    return {'答案质量均分(1~5)': avg * 20.0, '低分答案比例(≤2分, 越低越好)': low}


def check_gap():
    """材料外名词判据（离线，秒级）：见 tools/test_gap_fp.py。

    两个方向都要 100%：误报 = 对着我会的东西说"我没接触过"（最难看的失败）；
    漏报 = 该走"坦诚"却退回旧行为，模型自己补一段"我用过"。
    """
    if not os.path.exists(os.path.join(HERE, 'test_gap_fp.py')):
        print('跳过 gap 判据：仓库里没有 tools/test_gap_fp.py（它依赖你自己的材料外名词样本）。')
        return {}
    r = subprocess.run([sys.executable, '-X', 'utf8', os.path.join(HERE, 'test_gap_fp.py')],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print(r.stdout[-1500:])
    return {'gap 判据(误报0/硬案例全中)': 100.0 if r.returncode == 0 else 0.0}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--e2e', action='store_true')
    ap.add_argument('--quality', type=int, default=0, metavar='N',
                    help='抽 N 条真实提问真跑 + 判分（默认 0=不跑）')
    args = ap.parse_args()

    res = {}
    res.update(check_router())
    res.update(check_retrieval())
    res.update(check_gap())
    res.update(check_sub('答案后校验(数字/经历)', 'test_answer_check.py'))
    res.update(check_sub('故障注入(串题/看门狗/自愈/端口)', 'test_faults.py'))
    if args.quality:
        print('\n抽样真跑 + 判分（%d 条，约 30 秒）…\n' % args.quality)
        res.update(check_quality(args.quality))

    if not res:
        print()
        print('=' * 66)
        print('没有可判的项：仓库里没有任何你的测试集与期望集。')
        print('这不叫「通过」，而是「没数据可判」。先按文件头部的说明准备')
        print('tests/real_questions.json 与 tests/regress_expect.json')
        print('（模板：tests/regress_expect.example.json）。')
        print('=' * 66)
        sys.exit(2)

    print('=' * 66)
    print('%-26s %8s %10s   %s' % ('指标', '实测', '阈值', '结果'))
    print('-' * 66)
    bad = 0
    for k, lim in LIMITS.items():
        v = res.get(k)
        if v is None:
            continue
        if lim is None:                     # 越低越好的指标
            ok = v <= MAX_LOW_RATIO
            bad += (not ok)
            print('%-26s %6.1f%% %8s%%   %s' % (k, v, '≤%g' % MAX_LOW_RATIO, 'PASS' if ok else 'FAIL ← 回归'))
            continue
        ok = v >= lim
        bad += (not ok)
        print('%-26s %7.1f%% %9.1f%%   %s' % (k, v, lim, 'PASS' if ok else 'FAIL ← 回归'))
    print('=' * 66)

    if args.e2e:
        print('\n端到端全量回放（真调模型，约 80 秒）…\n')
        subprocess.run([sys.executable, '-X', 'utf8', os.path.join(HERE, 'test_e2e_replay.py')])

    print()
    print('回归门禁: %s' % ('全部通过' if not bad else '%d 项不达标' % bad))
    sys.exit(1 if bad else 0)