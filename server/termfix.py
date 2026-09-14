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


def correct(text):
    """对一段转写做保守纠错。返回 (新文本, [(听成的, 应该是的)])。"""
    if not text:
        return text, []
    tgt = targets()
    if not tgt:
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

    if not edits:
        return text, []
    out = text
    for s, e, rep in sorted(edits, key=lambda x: -x[0]):
        out = out[:s] + rep + out[e:]
    # 合并 token 会留下空位（"tensor r t"→"tensorrt  "），把多余空格收掉
    out = re.sub(r'[ \t]{2,}', ' ', out).strip()
    return out, fixes
