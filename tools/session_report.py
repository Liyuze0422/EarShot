# -*- coding: utf-8 -*-
"""面试后复盘：读 logs/session_*.jsonl，出一份"哪几题答砸了"的报告。

为什么要有：一场面试真正的收获是那些答砸的题。旧流程要人肉把录音稿转文本、
按 tests/real_questions.json 的格式标注意图、再跑回放 —— 现在这些都从会话日志里
自动生成，下一场的测试集和题库就是这么长出来的。

用法:
  python tools/session_report.py                # 最近一场
  python tools/session_report.py --file logs/session_20260911_231349.jsonl
  python tools/session_report.py --list         # 列出所有会话
"""
import os, sys, json, glob, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG_DIR = os.path.join(ROOT, 'logs')
FIRST_SLOW_MS = 2000
TOTAL_SLOW_MS = 4000


def load(path):
    recs = []
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    return recs


def analyze(recs):
    """把逐条记录整理成"每题一行"。"""
    rows = {}
    order = []
    filtered = 0
    for r in recs:
        ev = r.get('event')
        if ev == 'filtered':
            filtered += 1
            continue
        qid = r.get('qid')
        if qid is None:
            continue
        if qid not in rows:
            rows[qid] = {'qid': qid, 'q': r.get('q', ''), 'qtype': r.get('qtype', ''),
                         'answer': '', 'firstMs': None, 'totalMs': None, 'warns': [],
                         'dropped': False, 'offline': False, 'sources': []}
            order.append(qid)
        row = rows[qid]
        if r.get('q'):
            row['q'] = r['q']
        if r.get('qtype'):
            row['qtype'] = r['qtype']
        if ev == 'answer':
            row['answer'] = r.get('answer', '')
            row['firstMs'] = r.get('firstMs')
            row['totalMs'] = r.get('totalMs')
            row['warns'] = r.get('warns') or []
            row['offline'] = bool(r.get('offline'))
            row['sources'] = r.get('sources') or []
        elif ev == 'answer_dropped':
            row['dropped'] = True          # 面试官连问，这题被切掉了
        elif ev == 'audio_lost':
            row.setdefault('audio', []).append(r.get('error', ''))
    return [rows[q] for q in order], filtered


def verdict(row):
    """这一题算不算"答砸了"。判据都是可复核的，不靠感觉。"""
    bad = []
    if row['dropped']:
        bad.append('被下一题切掉（没答完）')
    if row['offline']:
        bad.append('走了离线题库（当时接口挂了）')
    if row['warns']:
        bad.append('告警: ' + '、'.join(w.get('text', '') for w in row['warns']))
    if row['firstMs'] and row['firstMs'] > FIRST_SLOW_MS:
        bad.append('首字 %dms 偏慢' % row['firstMs'])
    if row['totalMs'] and row['totalMs'] > TOTAL_SLOW_MS:
        bad.append('总耗时 %.1fs 偏慢' % (row['totalMs'] / 1000.0))
    if row['answer'] and row['answer'].startswith('<快答线失败'):
        bad.append('快答线失败')
    if not row['answer'] and not row['dropped']:
        bad.append('没有答案记录')
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')

    files = sorted(glob.glob(os.path.join(LOG_DIR, 'session_*.jsonl')))
    if a.list or not files:
        print('会话日志（%s）:' % LOG_DIR)
        for f in files:
            print('  %-46s %.1f KB' % (os.path.basename(f), os.path.getsize(f) / 1024.0))
        if not files:
            print('  还没有。跑一场（或 tools/test_faults.py）之后再看。')
        return 0
    path = a.file or files[-1]
    recs = load(path)
    rows, filtered = analyze(recs)
    bad = [(r, verdict(r)) for r in rows]
    answered = [r for r in rows if r['firstMs']]
    L = []
    L.append('# 面试复盘 · %s' % os.path.basename(path).replace('session_', '').replace('.jsonl', ''))
    L.append('')
    L.append('问题 %d 条 · 被过滤（非提问）%d 条 · 有答案 %d 条' % (len(rows), filtered, len(answered)))
    if answered:
        firsts = sorted(r['firstMs'] for r in answered)
        totals = sorted(r['totalMs'] for r in answered if r['totalMs'])
        L.append('首字中位 %dms / 最慢 %dms · 总耗时中位 %.1fs' % (
            firsts[len(firsts) // 2], firsts[-1], (totals[len(totals) // 2] / 1000.0) if totals else 0))
    L.append('')
    L.append('| # | 题型 | 首字 | 总 | 面试官提问 | 复查点 |')
    L.append('|---|---|---|---|---|---|')
    for r, v in bad:
        L.append('| %s | %s | %s | %s | %s | %s |' % (
            r['qid'], r['qtype'] or '-',
            ('%dms' % r['firstMs']) if r['firstMs'] else '-',
            ('%.1fs' % (r['totalMs'] / 1000.0)) if r['totalMs'] else '-',
            (r['q'] or '')[:44].replace('|', '/'),
            '、'.join(v) if v else 'OK'))
    L.append('')
    L.append('## 需要改的东西（按上面复查点）')
    L.append('')
    L.append('1. 有告警的题 → 把正确数字/说法补进材料或题库')
    L.append('2. 被切掉的题 → 面试官连问很快，考虑把 VAD 判停调到 300ms 激进模式')
    L.append('3. 走离线题库的题 → 查那段时间的网络/接口')
    L.append('4. 这些提问原文可以直接追加进 tests/real_questions.json 当下一场的回归集')
    out = os.path.join(LOG_DIR, 'report_%s.md' % os.path.basename(path).replace('session_', '').replace('.jsonl', ''))
    open(out, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L[:14]))
    print()
    print('完整报告: %s' % out)
    nbad = sum(1 for _, v in bad if v)
    print('需要复查: %d 题' % nbad)
    return 0


if __name__ == '__main__':
    sys.exit(main())
