# -*- coding: utf-8 -*-
"""会前题库：口语问法 -> 可直接照念的答案。

真实一面回放实测的结论：口头追问极短、极口语、经常没有关键词
（"那个推荐系统是你自己搭的吗""数据量有多大呢"），44 条里 6 条至今检索不到。
题库把"开放检索"变成"闭集匹配"——会前有无限时间，可以把面试官可能的问法穷举出来，
索引建在**问法**上而不是知识点上。

两处用途：
  1. 命中后作为高精度材料喂给快答线（口语问法 + 一条已经写好的答案）
  2. 快答线失败/断网时直接铺答案，不调模型（离线兜底）
"""
import os
import json
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from knowledge import BM25, detect_topic

BANK_FILE = os.path.join(HERE, '..', '知识库', '题库.json')
# 注意：不按"分数阈值"自动切到直接铺答案。
# 实测 39 条真实提问的分数分布是重叠的（未命中组最高 8.11 > 命中组最低 4.89），
# 阈值分不开，硬切会把大量不相关的问题也直接铺出去。
# 题库只做两件事：当补充材料喂给模型；快答线挂了时兜底铺一条（宁可相关性弱也不要只剩报错）。
_cache = {}


def load():
    if 'items' in _cache:
        return _cache['items'], _cache['idx']
    path = os.path.abspath(BANK_FILE)
    if not os.path.exists(path):
        _cache['items'], _cache['idx'] = [], None
        return _cache['items'], _cache['idx']
    data = json.load(open(path, encoding='utf-8'))
    items = data.get('items', data if isinstance(data, list) else [])
    items = [x for x in items if x.get('q') and x.get('a')]
    idx = BM25([(x.get('src', ''), x['q']) for x in items]) if items else None
    _cache['items'], _cache['idx'] = items, idx
    return items, idx


def search(question, topk=2, boost_src=None):
    """返回 [{'score','q','a','src','topic'}]，按分数降序。"""
    items, idx = load()
    if not idx:
        return []
    hits = idx.search(question, topk=topk, boost_src=boost_src)
    out = []
    for score, src, q in hits:
        for it in items:
            if it['q'] == q and it.get('src', '') == src:
                out.append({'score': round(score, 2), 'q': q, 'a': it['a'],
                            'src': src, 'topic': it.get('topic', '')})
                break
    return out


def lookup(question, topk=2, history=None):
    """线上入口：用当前句 + 话题词加权（和主检索一致的口径）。"""
    return search(question, topk=topk, boost_src=detect_topic(question))


def followups(question, topk=3, exclude=1):
    """追问预案（P5 的廉价替代）。

    原计划的 DSH 深答线实测 31.6s，面试里用不上。但题库是按材料章节生成的，
    同一个章节里的问法天然就是"面试官接下来可能接着问什么"。
    所以追问预算是本地零成本、零延迟地算出来的——不调模型、不花时间。

    exclude: 跳过前 N 条（前几条就是当前这个问题本身命中的那几个）。
    """
    hits = lookup(question, topk=topk + exclude)
    out = hits[exclude:]
    return [{'q': h['q'], 'a': h['a'], 'src': h['src']} for h in out[:topk]]


def material(question, topk=2):
    """把命中的题库条目拼成喂给模型的材料块。"""
    hits = lookup(question, topk=topk)
    if not hits:
        return '', hits
    parts = ['<!-- 题库命中：这是会前为类似问法准备好的答案，请以它为准，' 
             '按当前提问的实际问法调整措辞，不要照抄 -->']
    for h in hits:
        parts.append('问法：%s\n准备好的答案：%s' % (h['q'], h['a']))
    return '\n\n'.join(parts), hits


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    items, idx = load()
    print('题库条目: %d' % len(items))
    for q in sys.argv[1:] or ['那个推荐系统是你自己搭的吗？', '数据量有多大呢？']:
        print('--- %s' % q)
        for h in lookup(q, topk=3):
            print('   %.2f  %s' % (h['score'], h['q']))
            print('         %s' % h['a'][:90])
