# -*- coding: utf-8 -*-
"""对着**正在运行的后端**跑一次真实问答（WebSocket 层，和浮窗走同一条路）。

用途：改完后端事件协议（qid / slow / answer_warn / preplan）之后，确认真实链路也对，
而不是只在单测里对。跑完顺手看一眼 logs/session_*.jsonl 有没有落下记录。

用法: python tools/e2e_live.py ["问题"]      # 默认问一个材料外的名词
"""
import sys, os, json, time, asyncio, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))


def ws_url():
    try:
        p = int(open(os.path.join(ROOT, '.runtime_port'), encoding='utf-8').read().strip())
        return 'ws://127.0.0.1:%d/ws' % p
    except Exception:
        return 'ws://127.0.0.1:8765/ws'


async def run(question, seconds=25):
    import websockets
    got = []
    first_delta = [None]
    t0 = time.time()
    async with websockets.connect(ws_url(), ping_interval=20) as ws:
        hello = json.loads(await ws.recv())
        print('后端握手: ready=%s port=%s qid=%s' % (hello.get('ready'), hello.get('port'), hello.get('qid')))
        await ws.send(json.dumps({'cmd': 'ask', 'text': question}, ensure_ascii=False))
        while time.time() - t0 < seconds:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=seconds - (time.time() - t0))
            except asyncio.TimeoutError:
                break
            ev = json.loads(raw)
            t = ev.get('type')
            if t == 'answer_delta':
                if first_delta[0] is None:
                    first_delta[0] = time.time() - t0       # 从发问到第一个字上屏的真实墙钟时间
                continue
            got.append(ev)
            tag = '%s%s' % (t, ('#%s' % ev['qid']) if ev.get('qid') is not None else '')
            if t == 'answer_done':
                print('  %-14s 出字(内部) %sms 出字(墙钟) %sms 总 %.1fs 告警 %s' % (
                    tag, ev.get('firstMs'),
                    int(first_delta[0] * 1000) if first_delta[0] else -1,
                    (ev.get('totalMs') or 0) / 1000.0, ev.get('warns')))
                print('  答案: %s' % (ev.get('text') or '').replace('\n', ' ')[:200])
                break
            if t == 'preplan':
                print('  %-14s %s' % (tag, ' ／ '.join(i.get('q', '')[:18] for i in ev.get('items', []))))
            elif t in ('slow', 'answer_warn', 'filtered'):
                print('  %-14s %s' % (tag, json.dumps(ev, ensure_ascii=False)[:150]))
            elif t == 'question':
                print('  %-14s %s' % (tag, ev.get('qtypeLabel')))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('q', nargs='?', default='你用过 eBPF 做可观测性吗？')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    events = asyncio.run(run(a.q))
    ok = any(e.get('type') == 'answer_done' for e in events)
    print()
    print('链路: %s' % ('PASS' if ok else 'FAIL（没等到 answer_done）'))
    files = sorted([f for f in os.listdir(os.path.join(ROOT, 'logs')) if f.startswith('session_')]) if os.path.isdir(os.path.join(ROOT, 'logs')) else []
    if files:
        p = os.path.join(ROOT, 'logs', files[-1])
        recs = [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
        last = [r for r in recs if r.get('event') == 'answer'][-1:]
        print('会话日志: %s（%d 条），最后一条 answer: %s' % (
            os.path.basename(p), len(recs), (last[0].get('q', '')[:30] if last else '无')))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
