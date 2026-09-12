# -*- coding: utf-8 -*-
"""面试官问题抽取：从面试官一整段转写里挑出"他真正在问我的那一句"。

痛点：真实 ASR 转写里面试官经常一口气说 100+ 字，把自述、铺垫、提问混在一起：

    "我为什么这么问呢？就是前面因为你说到你用了部分的三维的这些数据。
     但我听下来可能更多的是只是做一个给人来看的，就会不会碰到楼。"

整段丢给 router.classify() 和 knowledge 检索全是噪音，所以要先把"真问题"抠出来。

设计原则（保守优先：宁可少切，也不能切掉问题）
  1) 只按"句子"切，绝不在句子内部按逗号动刀——逗号两边的成分互相依赖，
     切了容易把问题的前半截（"那你这边用三维地图"）和问句本身拆散。
  2) 锚点 = 第一句"像在提问"的话；中间一律不丢（哪怕夹着自述），
     尾部只在"最后一句问句之后剩下的全是没问句、没前提词、且≤40字的短句"
     时才裁掉——任何一条不满足就整块保留。
  3) 锚点前面的句子默认丢掉（那才是"自述/铺垫"），只有命中"前提词"
     （如果/假如/比如说……）才向前回带一句——设计题常把条件写在铺垫句里，
     丢了会让 router 从 design 掉到 experience。
  4) 面试官自述（"我为什么这么问呢""我是本次的面试官"）带疑问词也不算锚点。
  5) 一个锚点都找不到 → 原样返回原文（不猜）。
  6) 整段本来就是干净问题（第一句就在问）→ 原样返回，等价于 no-op。

纯规则（标点 + 疑问词 + 长度 + 位置），零依赖，单条 <1ms。

用法::

    from extract_q import extract, is_questionish
    extract("我为什么这么问呢？就是前面因为你说到……就会不会碰到楼。")
    # -> "但我听下来可能更多的是只是做一个给人来看的，就会不会碰到楼。"
"""
import re

__all__ = ['extract', 'is_questionish']

# ── 句子切分：到句末标点为止，标点保留在句尾 ────────────────────────────────
_SENT = re.compile(r'[^。！？!?；;…\r\n]+[。！？!?；;…]*')

# ── 疑问形式：问号，或疑问词/疑问句式 ────────────────────────────────────────
_PUNCT_ONLY = re.compile(r'[。！？!?；;…，,、\s]+')
_Q_PUNCT = re.compile(r'[?？]')
_Q_WORD = re.compile(
    r'吗|嘛|呢|'
    r'什么|啥|怎么|咋|咋样|为什么|为啥|如何|'
    r'哪里|哪个|哪些|哪儿|哪一|哪种|'
    r'多少|多长|多大|多久|'
    r'几(个|种|台|层|条|套|步|次|年|家|块)|'
    r'是不是|有没有|会不会|能不能|对不对|行不行|可不可以|'
    r'对吧|是吧|对吗|好吗|'
    r'介绍(一下|下)?|讲讲|讲一下|说说|说一下|聊一聊|谈一谈|举例|举个例子|'
    r'做过|用过|接触过|了解一下|了解过|了解吗')

# ── 前提词：命中则把锚点前一句话一起带上（设计题的条件常在这里） ─────────────
_PREMISE = re.compile(
    r'如果|假如|假设|要是|倘若|比如说|举个例子|例如|结合(我们|贵|你们)|前提')

# ── 面试官自述 / 设备闲聊：带疑问词也不算"在问我" ───────────────────────────
_META = re.compile(
    r'我为什么这么问|我这么问|我是本(次|场|轮)的面试官|'
    r'我了解了|我明白了|我知道了|'
    r'我这边(没什么|没有问题|没有|ok|OK)|'
    r'你继续|我先(说|讲|看|打开|关)|我打断一下|我补充一下|'
    r'稍等|等我一下|别紧张|不好意思|听得到吗|能听到|听得清')


def _segments(text):
    """按句末标点切成句子（句尾标点保留），去空白后返回列表。"""
    out = []
    for m in _SENT.finditer(text):
        s = m.group(0).strip()
        if s:
            out.append(s)
    return out


def _askish(seg):
    """这一句形式上是不是在提问（不管是谁在问）。"""
    return bool(_Q_PUNCT.search(seg) or _Q_WORD.search(seg))


def _tail_droppable(t):
    """尾部这一句能不能丢：够短，且要么是面试官收尾/设备闲聊，要么完全没有提问形式。"""
    if len(t) > 40:
        return False
    if _META.search(t):
        return True
    return (not _Q_PUNCT.search(t)) and (not _Q_WORD.search(t)) and (not _PREMISE.search(t))


def _askish_strong(seg):
    """去掉标点后还剩下实义词的提问形式（排除"呢？"这种只剩语气词的尾巴）。"""
    core = _PUNCT_ONLY.sub('', seg or '')
    if len(core) < 2:
        return False
    return bool(_Q_PUNCT.search(seg) or _Q_WORD.search(core))


def is_questionish(text):
    """整段（或单句）里有没有"在问我"的提问。

    与 router.is_skip 的区别：这里只看形式（标点/疑问词/自述），
    不管该用哪套回答策略，也不做设计题/行为题的细分。
    纯自述（"我为什么这么问呢？"）返回 False。
    """
    s = (text or '').strip()
    if len(s) < 2:
        return False
    if not _askish(s):
        return False
    if _META.search(s):
        # 自述里夹带的疑问词不算（"我为什么这么问呢？"只剩"呢？"）；
        # 剥掉自述后还有真问句才算
        if not _askish_strong(_META.sub('', s)):
            return False
    return True


def extract(text):
    """返回最可能"面试官在问我"的那一句或几句（尽量短，只按整句切）。

    抽不出东西时原样返回，绝不返回空串。
    """
    s = (text or '').strip()
    if not s:
        return s
    segs = _segments(s)
    if len(segs) <= 1:
        return s  # 本来就只有一句，没得切也不该切

    anchor = -1
    for i, seg in enumerate(segs):
        if _META.search(seg):
            continue  # 面试官自述，不是问我
        if _askish(seg):
            anchor = i
            break
    if anchor < 0:
        return s  # 一个问句都没有：不猜，整段返回

    # 前提句回带：紧邻锚点、且命中前提词、且自己不是自述
    while anchor > 0:
        prev = segs[anchor - 1]
        if _META.search(prev) or not _PREMISE.search(prev):
            break
        anchor -= 1

    # 尾部裁剪：最后一个问句之后如果只剩"没问句、没前提词、而且很短"的句子，
    # 说明是面试官问完之后的收尾/自述，丢掉。任何一条不满足就整块保留。
    last = anchor - 1
    for i in range(anchor, len(segs)):
        if not _META.search(segs[i]) and _askish(segs[i]):
            last = i
    if last + 1 < len(segs):
        tail = segs[last + 1:]
        if all(_tail_droppable(t) for t in tail):
            segs = segs[:last + 1]

    out = ''.join(segs[anchor:]).strip()
    return out or s
