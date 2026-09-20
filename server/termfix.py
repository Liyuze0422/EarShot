# -*- coding: utf-8 -*-
"""ASR 术语后处理纠错。

问题（用户反馈 + 实测）：SenseVoice 对英文技术词识别很差 ——
35 个真实术语、中文口音读出来只有 **20%** 原样识别，英文口音也只有 40%。
典型错误：Redis→read、Nginx→links、Kubernetes→kubbernet、Milvus→mivis、
Ollama→o、FastAPI→fast api、ONNX→o n n x。

做法：拿**用户自己的术语表**（config/known_terms.md）+ 语料里出现过的英文词
当目标集，对转写结果做保守的模糊纠错。

## 为什么要分级信任

实测：语料词表有 7000+ 个词，里面混着 read / phase / links 这类**普通英文词**，
它们是假阳性的主要来源（把 "please" 纠成 "phase"）。所以分两级：

  · known_terms（用户手写的 114 个词）—— 高信任，允许完整编辑距离
  · 语料词表 —— 低信任，**只允许距离 ≤ 1**

另外：太短的 token 不纠（o / v / ra 纠了只会更错）、并列候选放弃、常见英文词不动。

用法::

    from termfix import correct
    correct('你在项目里用过 mivis 吗')    # -> ('你在项目里用过 milvus 吗', [('mivis','milvus')])
"""
import re

__all__ = ['correct', 'targets', 'reset_cache']

_ASCII = re.compile(r'[A-Za-z][A-Za-z0-9._\-]*')
_CJK = re.compile(r'[\u4e00-\u9fff]+')

# 语气词：以它们开头的两字串是「音节 + 残字」（「呃场」），不是被听错的术语
_PARTICLE = set('呃嗯啊哦哎诶呀咳唉')

# 长度 < 这个值的 token 一律不动 —— "o"(Ollama)、"v"(Vue/Faiss)、"ra"(RAG)
# 这类短 token 纠错的假阳性远高于收益。
MIN_LEN = 4

# 合并 token 时最多并几个（ONNX 会被念成 "o n n x" = 4 个 token）
MAX_JOIN = 4

# 常见英文词 / 语气词：它们"本来就是英文单词"，纠了就是错
_STOP = {
    'the', 'and', 'for', 'you', 'your', 'that', 'this', 'with', 'have', 'has',
    'what', 'when', 'how', 'why', 'can', 'could', 'would', 'should', 'about',
    'there', 'their', 'them', 'then', 'than', 'from', 'into', 'over', 'some',
    'just', 'like', 'make', 'made', 'take', 'time', 'team', 'work', 'well',
    'good', 'code', 'data', 'test', 'page', 'user', 'name', 'type', 'list',
    'read', 'write', 'file', 'line', 'size', 'rate', 'case', 'base', 'main',
    'http', 'https', 'www', 'com', 'org', 'yes', 'okay', 'sorry', 'thanks',
    'please', 'phase', 'links', 'wins', 'readies', 'cuba', 'inter', 'strand',
    'pros', 'graph', 'web', 'land', 'chain', 'fast', 'api', 'sql', 'postgre',
    'my', 'la', 'vo', 'ra', 'o', 'v', 'c', 'n', 'x',
}


_CACHE = {}


def _build():
    """构建目标集。known_terms 高信任，语料词表低信任。"""
    import answer
    kn = set()
    try:
        kn = {str(w).strip().lower() for w in answer.known_terms()}
    except Exception:
        pass
    v = set()
    try:
        v = {str(w).lower() for w in answer._vocab()}
    except Exception:
        pass
    # 中文词单独留一份**过滤前**的：下面那道 MIN_LEN=4 是给英文 token 设的
    # （"o"/"ra" 这类短 token 纠了只会更错），但「飞书」「字节」这种两字中文词
    # 会被它一起挡掉 —— 而中文恰恰只有 2~4 字。
    _kn_raw = set(kn)
    kn = {w for w in kn if len(w) >= MIN_LEN and w not in _STOP}
    v = {w for w in v - kn if len(w) >= MIN_LEN and w not in _STOP}
    by = {}
    for w, trust in [(w, 2) for w in kn] + [(w, 1) for w in v]:
        by.setdefault((w[0], len(w)), []).append((w, trust))
    for k in by:
        by[k] = sorted(set(by[k]), key=lambda x: (-x[1], x[0]))
    _CACHE['index'] = by
    _CACHE['known'] = kn
    _CACHE['all'] = kn | v
    # 中文目标词：ASR 会把「飞书」听成「飞猪」—— 同长度、单字替换（编辑距离 1）。
    # 英文那套按 token 纠，完全够不到中文（2026-09-20 真实面试里这条错误直接进了答案文本）。
    # 只收**用户手写词表**（trust 2），不收语料词表：语料里「飞」打头的词有一串
    # （飞机/飞过/起飞…），只按编辑距离会并列成一堆候选、只能放弃；而手写词表是用户
    # 亲口指定的目标词，命中它才敢下判断。所以这一层的能力边界 = 用户词表里写了什么。
    cn = {}
    for w in _kn_raw:
        if 2 <= len(w) <= 4 and _CJK.fullmatch(w):
            cn.setdefault(len(w), []).append(w)
    _CACHE['cn'] = cn


def reset_cache():
    """语料/词表变了（换资料包）之后要清一次。"""
    _CACHE.clear()


def targets():
    """返回全部目标词。首次调用会构建索引。"""
    if 'all' not in _CACHE:
        _build()
    return _CACHE['all']


def _cap(word, trust=2):
    """允许的编辑距离。`read`(4)→`redis` 差 2，所以 ≤4 只给 1，避免把真词纠歪。

    假阳性防线不靠"压低阈值"，靠 _STOP（源词本身是正常英文词就跳过）——
    压低阈值会把 mivis→milvus、uicorn→uvicorn 这些真错误一起挡掉。
    """
    n = len(word)
    if n <= 4:
        return 1
    return 2 if n <= 9 else 3


def _ed(a, b, cap):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[len(b)]


def _best(word):
    """找最接近的目标词。

    并列时优先用户手写词表（trust 高）；只有**同等级**并列才放弃 ——
    宁可不错，也别纠错。
    """
    by = _CACHE['index']
    cap = _cap(word)
    best = None          # ((距离, -trust), 词)
    tie = False
    for ln in range(len(word) - cap, len(word) + cap + 1):
        for cand, trust in by.get((word[0], ln), ()):
            # 语料词表是低信任：只允许距离 ≤1。
            # 实测挡掉的假阳性：GraphqL（本来就对）→ graphpy（语料里的词，差 2）。
            if trust < 2 and cap > 1:
                d = _ed(word, cand, 1)
                if d > 1:
                    continue
            else:
                d = _ed(word, cand, cap)
                if d > cap:
                    continue
            key = (d, -trust)
            if best is None or key < best[0]:
                best, tie = (key, cand), False
            elif key == best[0] and cand != best[1]:
                tie = True
    return None if (best is None or tie) else best[1]


def _cn_edits(text):
    """中文同长度单字替换纠错，返回 [(start, end, 正确词, 听成的词)]。

    判据（三个都满足才纠，宁可不纠）：
      1. **起点落在 jieba 的词边界上**。跨边界的假子串不可能是 ASR 错词 ——
         「其实我我理解」里切出来的「实我」会把 jieba 分词边界当成窗口起点试，
         一试就撞上「实施」。只从边界起步，这类假子串根本不会被尝试。
      2. **源词 jieba 词典里查不到（词频 0）**。这是最关键的防线：
         「飞猪/自节/豆报」的词频都是 0，而「公司(45604)/工作(66367)/实习(1023)」
         这些正常词都有词频。先前用「不在 known ∪ 语料词表」当判据是错的 ——
         语料词表也被 MIN_LEN=4 砍过，两字常用词根本不在里面，实测 127 条误纠 35 条
         （公司→公式、简历→日历、客户→门户）。
      3. **目标词在用户手写词表里、且 jieba 认识它**（词频 > 0）。

    为什么不按首字分桶：错字可能连首字都是错的（「字节」被听成「自节」），
    按首字查会直接漏掉。known_terms 只有一两百条，按长度全量扫足够快。
    """
    cn = _CACHE.get('cn') or {}
    if not cn:
        return []
    import jieba
    FREQ = jieba.dt.FREQ
    bounds, pos = [], 0
    for t in jieba.lcut(text):
        bounds.append(pos)
        pos += len(t)
    out = []
    for i in bounds:
        hit = None
        # 只试 2 字窗口。3/4 字窗口在真实数据上是净亏的：「工作是」=「工作」+「是」
        # 这种跨词拼接会整片撞上「工作台」（实测 127 条里 4 条误纠全是它）。
        # 而 2 字错词（飞猪/自节/豆报）才是中文 ASR 的主战场。
        for ln in (2,):
            sub = text[i:i + ln]
            if len(sub) < ln or not _CJK.fullmatch(sub):
                continue
            if FREQ.get(sub, 0):
                continue                         # 正常词 → 不试
            if sub[0] in _PARTICLE:
                continue                         # 语气词开头（「呃场」）不可能是术语错字
            cands = [w for w in cn.get(ln, ())
                     if w != sub and FREQ.get(w, 0) and _ed(sub, w, 1) == 1]
            if len(cands) == 1:                  # 候选唯一才敢纠
                hit = (sub, cands[0])
                break
        if hit:
            out.append((i, i + len(hit[0]), hit[1], hit[0]))
    return out


def correct(text):
    """对一段转写做保守纠错。返回 (新文本, [(听成的, 应该是的)])。"""
    if not text:
        return text, []
    tgt = targets()
    if not tgt and not (_CACHE.get('cn') or {}):
        return text, []
    spans = [(m.start(), m.end(), m.group(0)) for m in _ASCII.finditer(text)]
    n = len(spans)
    used = set()
    edits = []      # (start, end, 替换成的文本)
    fixes = []

    # 第一步：相邻 token 合并（FastAPI 被念成 "fast api"、ONNX 被念成 "o n n x"）。
    # 从长的窗口开始贪心。注意：只替换**各个 token 自己**，
    # 不能把"第一个 token 起点到最后一个 token 终点"整段替换 —— 中间夹着汉字
    # （"mongo d b 和 g r p c" 曾把"和"一起吃掉了）。
    def _join(i, size, hit):
        edits.append((spans[i][0], spans[i][1], hit))
        for j in range(i + 1, i + size):
            edits.append((spans[j][0], spans[j][1], ''))
        fixes.append((' '.join(spans[j][2] for j in range(i, i + size)), hit))
        used.update(range(i, i + size))

    # 第一步 A：**精确**合并，长的优先。
    # 精确匹配没有误纠风险，所以可以贪心吃掉多个 token（ONNX = "o n n x"）。
    for size in range(MAX_JOIN, 1, -1):
        for i in range(n - size + 1):
            if any(j in used for j in range(i, i + size)):
                continue
            joined = ''.join(spans[j][2] for j in range(i, i + size))
            if len(joined.strip('.-_+')) < MIN_LEN:
                continue
            if joined.strip('.-_+').lower() in tgt:
                _join(i, size, joined.strip('.-_+').lower())

    # 第一步 B：**模糊**合并，只允许两个词、且距离 ≤1。
    # 放宽会出事：贪心 + 模糊会让 "mongo d b g" 整段匹配到 mongodb，
    # 把后面 grpc 的 g 一起吞掉；"read the" 也会被模糊成 readme。
    for i in range(n - 1):
        if i in used or (i + 1) in used:
            continue
        joined = (spans[i][2] + spans[i + 1][2]).strip('.-_+').lower()
        if len(joined) < 6:
            continue
        hit = _best(joined)
        if hit and _ed(joined, hit, 1) <= 1:
            _join(i, 2, hit)

    # 第二步：单个 token 的近音纠错
    for i, (s, e, w) in enumerate(spans):
        if i in used:
            continue
        lw = w.strip('.-_+').lower()
        if len(lw) < MIN_LEN or not lw.isalpha() or lw in tgt or lw in _STOP:
            continue
        hit = _best(lw)
        if hit:
            edits.append((s, e, hit))
            fixes.append((w, hit))

    # 第三步：中文单字替换纠错（「飞书」被听成「飞猪」）。
    # 和英文改动做重叠检查 —— 两边都改同一段会互相覆盖。
    for s, e, rep, heard in _cn_edits(text):
        if any(s < x[1] and x[0] < e for x in edits):
            continue
        edits.append((s, e, rep))
        fixes.append((heard, rep))

    if not edits:
        return text, []
    out = text
    for s, e, rep in sorted(edits, key=lambda x: -x[0]):
        out = out[:s] + rep + out[e:]
    # 合并 token 会留下空位（"tensor r t"→"tensorrt  "），把多余空格收掉
    out = re.sub(r'[ \t]{2,}', ' ', out).strip()
    return out, fixes
