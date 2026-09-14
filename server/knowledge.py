# -*- coding: utf-8 -*-
"""本地知识库：切块 + jieba 分词 + 词级 BM25 + 字级 bigram BM25，RRF 融合。语料 = 我自己的面试材料。

2026-09-11 加了字级 bigram 通道（第二路 BM25 + RRF），**实测是负收益，默认关闭**。
  动机：词级 BM25 靠实词，担心"整句没有实词"的口语追问（#12"首先听得懂这一块是要听懂什么？
  有哪些。"）会检索不到。做法：把去掉空白标点后的串切相邻两字（"先听/听得/得懂/听懂..."）
  单独建一路 BM25，两路各排名后 RRF 融合，score = Σ w/(k+rank)。
  结论（权威口径 tools/test_retrieval_real.py，39 条有明确材料的真实提问，走
  answer.retrieve 的"上文2句 + 话题词x3"）：
        配置                  hit@1   hit@3
        纯词级 BM25 (wb=0)     69.2%   84.6%   ← 基线
        bigram 权重 0.2        66.7%   82.1%
        bigram 权重 0.3        69.2%   79.5%
        bigram 权重 0.5        66.7%   79.5%
        bigram 权重 0.7        66.7%   76.9%
        bigram 权重 1.0        64.1%   76.9%
  单路对比（同样 39 条、同样窗口）：词级 27/39 hit@1、33/39 hit@3；
  bigram 单路 24/39、27/39——本身就比词级弱，且两路 top3 取并集一条都没多救回来。
  原因：剩下 6 条未命中（#12/#14/#16/#26/#38/#39）全是"话题续接"失败——gold 材料由
  好几轮之前的话题（推荐系统项目）决定，句子本身既没有实词也没有话题词，
  gold chunk 在词级排 rank 5/7/16/5/54/43、在 bigram 排 47/7/27/22/20/9，两路都够不到 top3。
  这类问题不是字面匹配能救的（要话题持久化，那是 answer.py 侧的事）。
  所以：bigram 通道代码保留、默认关（RRF_W_BIGRAM=0.0，此时排序与改造前逐条一致），
  只在词级通道几乎没结果时当兜底用（BIGRAM_FALLBACK_*）。
  验收见 tools/test_hybrid.py（A/B + 权重扫描 + 兜底触发统计）。

2026-09-11 用真实一面（50 段面试官原话）做回归测试发现旧版三个硬伤：
  1) 不过滤虚词 —— "那个推荐系统是自己搭的吗" 里 "是/的/自己" 把 HR 通用稿顶到了第一，
     真正讲推荐系统的 项目A 报告排到第 6。真实口语提问短、虚词占比一半以上，必须去掉。
  2) jieba 默认词典把专业词切碎 —— "工作流"→工作/流、"有向图工作流"→有向图/工作/流，
     术语对不上。用自定义词典钉住领域词。
  3) 文件名没参与打分 —— "推荐系统那个项目" 明明就是 项目A_推荐系统.md，
     但正文里没有"项目A"这个完整串。把文件名也塞进词袋并加权。
实测：真实提问 hit@1 15.4%→（见 tools/test_retrieval_real.py），hit@3 41%→。
"""
import os
import re
import math
import glob
import json
from collections import Counter
import jieba

import settings

# 语料位置：默认 <仓库根>/knowledge 下的 *.md / *.txt。
# 改法：编辑 config/settings.json 的 corpus_globs，或设环境变量
# TP_CORPUS_GLOBS="D:\材料\**\*.md;D:\材料\**\*.txt"（分号分隔）。
#
# ── 资料包（corpus_profile）──────────────────────────────────────────────
# 实测（_realdata/_work/bench_dilute*.py）：往知识库里掺「讲同一批话题」的文本会**直接抢排名**——
# 掺 25% 本人面试录音，hit@1 从 89.7% 塌到 41.0%；而掺 2 倍**异题材**文档只掉 2.5。
# 结论：个人经历材料千万不能和别人的经历混在一起，按面试分包是唯一干净的办法。
# 约定：
#   knowledge/_base/      所有面试通用的个人材料
#   knowledge/<包名>/      某家公司/某轮专用（JD、公司介绍、面经），面试哪家切哪家
#   corpus_profile 留空     = 老行为（扫整个 knowledge/），不做任何改变
_UNSET = object()
_runtime_profile = _UNSET


def active_profile():
    """当前生效的资料包名（运行时覆盖 > 配置/环境变量）。空 = 未启用分包。"""
    p = settings.corpus_profile() if _runtime_profile is _UNSET else str(_runtime_profile)
    return (p or '').strip()


def set_profile(name):
    """切换资料包并清掉索引缓存（下次 build() 自动重建）。

    set_profile('字节跳动') -> 只扫 _base + 该包
    set_profile('')         -> 显式退回"扫整个 knowledge/"
    set_profile()           -> 撤销运行时覆盖，回到 config/settings.json / TP_CORPUS_PROFILE
    """
    global _runtime_profile
    _runtime_profile = _UNSET if name is None else str(name).strip()
    _cache.clear()
    # 词表/纠错缓存也要清：它们是从语料派生的，不清就会拿着上个资料包的词
    # 做术语纠错和缺口判定。
    try:
        import answer
        answer.reset_cache()
    except Exception:
        pass
    try:
        import termfix
        termfix.reset_cache()
    except Exception:
        pass
    return active_profile()


def list_profiles():
    """knowledge/ 下可用的资料包名（下划线开头的是内部目录，不算）。"""
    root = settings.knowledge_root()
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if not d.startswith('_') and os.path.isdir(os.path.join(root, d)))


def corpus_globs():
    """当前生效的语料 glob（启用分包时 = _base/ + 该包/）。"""
    prof = active_profile()
    if not prof:
        return settings.corpus_globs()
    root = settings.knowledge_root()
    out = []
    for d in (os.path.join(root, '_base'), os.path.join(root, prof)):
        out += [os.path.join(d, '**', '*.md'), os.path.join(d, '**', '*.txt')]
    return out
CHUNK_MIN, CHUNK_MAX = 300, 700
TITLE_WEIGHT = 4          # 文件名在词袋里重复几次
# 「换个说法」扩展词在词袋里重复几次。它们由 tools/kb_expand.py 离线生成，
# 存的是一句句"面试官可能怎么问这块"。1 次就够 —— 这些是提问句式，
# 权重给大了会把材料原文的内容词压掉。
EXPAND_WEIGHT = 1
_EXPAND_FILE = 'doc_expand.json'
_expand_cache = {}


def _chunk_key(text):
    import hashlib
    return hashlib.sha1(re.sub(r'\s+', '', text or '').encode('utf-8')).hexdigest()[:16]


def load_expand(reload=False):
    """读 config/doc_expand.json（按内容 hash 索引）。

    为什么按 hash 不按块序号：材料改一个字，hash 就变了，这一块自动没有
    扩展词、退回纯原文检索 —— 比"序号对不上导致张冠李戴"安全得多。
    """
    if _expand_cache and not reload:
        return _expand_cache.get('t')
    p = os.path.join(settings.REPO_ROOT, 'config', _EXPAND_FILE)
    t = {}
    try:
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                t = json.load(f) or {}
    except Exception as e:
        print('[知识库] %s 读取失败（忽略扩展词继续）：%s' % (_EXPAND_FILE, e))
        t = {}
    _expand_cache['t'] = t
    return t

# ── 领域词典：钉住会被 jieba 切碎的专业词 ──────────────────────────────────
DOMAIN_WORDS = [
    # 下面是示例条目：换成你自己项目里会被 jieba 切碎的专有词（产品名、技术栈缩写）。
    '推荐系统', '双塔', '粗排', '精排', '冷启动', '协同过滤', '特征工程', '排序模型',
    '离线评估', '线上指标', '点击率', '转化率', '私有化部署', '内网',
    '质检', '工艺规范', '缺陷', '工控机', '页码', '图纸',
    '大模型', '智能体', '工作流', '有向图', '节点', '子图', '状态机', '行为树',
    '提示词', '上下文', '工具调用', '函数调用', '任务分解', '任务分配', '反思', '记忆',
    '检索增强', '向量库', '向量数据库', '知识库', '切块', '召回', '重排', '混合检索',
    '微调', '低秩适配', '量化', '显存', '训练集', '标注', '评估集', '基座模型', '推理',
    '语音识别', '意图识别', '意图澄清', '置信度', '降级策略', '兜底', '超时', '重试',
    '行业情报', '开源情报', '前置工作', '占比', '插件化', '嵌入', '界面', '前端后端',
    '面试官', '自我介绍', '离职', '职业规划', '反问', '薪资',
]
for _w in DOMAIN_WORDS:
    jieba.add_word(_w)

# ── 停用词：虚词 + 面试口水的填充语。真实 ASR 句子里这些占一半以上 ──────────
STOP = set('''
的 了 是 在 我 你 他 她 它 咱 我们 你们 他们 这 那 这个 那个 这些 那些 这样 那样 这边 那边
一个 一下 一些 一点 有点 有的 有 没有 没 就 都 也 还 又 很 更 最 太 挺 和 与 跟 同 对 把 被
给 让 从 到 向 往 为 因为 所以 但是 但 而且 而 然后 如果 就是 还是 或者 以及 即使 虽然
什么 怎么 怎样 咋 为什么 为啥 哪些 哪个 哪 多少 多大 几个 几种 吗 嘛 呢 吧 啊 嗯 哦 呀 哈
请问 请 的话 时候 时 之间 之后 之前 自己 本身 一般 主要 应该 可能 可以 能够 能 会 要 想
说 做 用 去 来 上 下 中 里 个 条 点 种 台 层 次 个 关于 对于 相当于 比如说 比如 其实 反正
大概 差不多 是不是 有没有 现在 目前 当时 以后 之前 里面 外面 知道 觉得 感觉 意思 情况
一下 一样 怎么着 这种 那种 什 么 一下 呃 唉 哎 对对 好的 行
'''.split())


# ── 强话题词 → 材料归属 ────────────────────────────────────────────────
# 真实面试会切话题（推荐系统项目 → 微调项目），这时候"上文2句"反而是干扰：
# 实测 #34"就是做了微调是吗？"被窗口拉回推荐系统材料，答成了"不是微调"。
# 当前提问里只要出现强话题词，就把对应项目的材料加权（x3），
# 实测 hit@1 51.3%→69.2%、hit@3 76.9%→84.6%（tools/test_retrieval_topic.py）。
# ⚠️ 下面是示例条目：请替换成你自己项目的话题词 ——
# 键 = 材料文件名里能区分项目的片段；值 = 口语提问里常出现的项目名/技术词
# （用来判断"这句话在问哪个项目"，命中的材料会被加权 TOPIC_BOOST 倍）。
TOPIC_TERMS = {
    '项目A': ['推荐', '召回', '双塔', '粗排', '冷启动', '向量'],
    '项目B': ['质检', '规范', '页码', '工控机'],
    '项目C': ['微调', 'LoRA', 'QLoRA', '量化', '显存', '语料', '训练集', '数据集', '标注'],
}
TOPIC_BOOST = 3.0


# ── 私有词表：用 config/ 里的真词覆盖上面的示例 ────────────────────────────
# 上面两个表是**示例**（公开仓库不能带你的项目名）。真正要用的词放 config/ 下，
# 那两份被 .gitignore 挡着，不会被推到 GitHub：
#   config/domain_words.txt    一行一个词（# 开头是注释）—— 钉住会被 jieba 切碎的专有词
#   config/topic_terms.json    {"项目1": ["无人机", "地面站", …], "项目2": [...]}
# 为什么做成文件而不是让你直接改代码：以前只能改 knowledge.py，一改就和上游分叉，
# 公开仓库那份就永远是示例。真实配置和算法混在一起，两份代码迟早漂。
# 移过来的命令：python tools/migrate_private.py --from <私人副本/server>
def _apply_private_terms():
    global TOPIC_TERMS, DOMAIN_WORDS
    cfg_dir = os.path.join(settings.REPO_ROOT, 'config')

    p = os.path.join(cfg_dir, 'topic_terms.json')
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding='utf-8'))
            d = {str(k): [str(x) for x in v]
                 for k, v in d.items() if isinstance(v, list) and v}
            if d:
                TOPIC_TERMS = d
        except Exception as e:
            print('[知识库] config/topic_terms.json 读不了，继续用内置示例：%s' % e)

    p = os.path.join(cfg_dir, 'domain_words.txt')
    if os.path.exists(p):
        words = []
        for line in open(p, encoding='utf-8'):
            w = line.strip()
            if w and not w.startswith('#'):
                words.append(w)
        if words:
            DOMAIN_WORDS = words
            for w in words:
                jieba.add_word(w)
    return TOPIC_TERMS, DOMAIN_WORDS


_apply_private_terms()


def detect_topic(text):
    """返回唯一命中话题对应的材料文件关键字；同时命中多个话题则不锁定。"""
    hit = [k for k, terms in TOPIC_TERMS.items()
           if any(t.lower() in (text or '').lower() for t in terms)]
    return hit[0] if len(hit) == 1 else None


def load_docs():
    files = []
    for g in corpus_globs():
        # recursive=True：默认 glob 里的 "**" 才算"任意层子目录"，
        # 否则 knowledge/**/*.md 匹配不到直接放在 knowledge/ 下的文件。
        files += glob.glob(g, recursive=True)
    docs = []
    for f in sorted(set(files)):
        # 名字以 _ 开头的文件不参与检索：留个"往包里放笔记/待办"的口子，
        # 否则随手写的备忘会被当成材料检索出来（还会稀释排名）。
        if os.path.basename(f).startswith('_'):
            continue
        try:
            raw = open(f, 'r', encoding='utf-8', errors='ignore').read()
        except Exception:
            continue
        if f.lower().endswith('.html'):
            raw = re.sub(r'<script[\s\S]*?</script>', ' ', raw)
            raw = re.sub(r'<style[\s\S]*?</style>', ' ', raw)
            raw = re.sub(r'<[^>]+>', '\n', raw)
            import html as _h
            raw = _h.unescape(raw)
        docs.append((os.path.basename(f), raw))
    return docs


def chunk(text, src):
    out = []
    parts = re.split(r'\n(?=#{1,4}\s)', text)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= CHUNK_MAX:
            if len(p) >= 60:
                out.append((src, p))
        else:
            for i in range(0, len(p), CHUNK_MAX):
                seg = p[i:i + CHUNK_MAX].strip()
                if len(seg) >= 60:
                    out.append((src, seg))
    return out


# ── 标识符压平：max_len ↔ maxlen ────────────────────────────────────────
# ASR 听不见下划线、点号、连字符。面试官看着你的代码问"max_len 是怎么定的"，
# 转写出来很可能是"maxlen"，而材料里写的是 max_len。
# 不压平的话两边分词成 {max,len} vs {maxlen}，**一个词都对不上**，这题就白丢了。
# 实测材料里有 1094 种带分隔符的标识符（max_tokens / adapter_config.json / eval.py / tf-idf…），
# 全是这个坑。驼峰（LangGraph -> langgraph）jieba 本来就整块留着，不受影响。
# 只加不删：原有的分词结果一个不动，额外把压平形式也塞进词袋。
_IDENT_RUN = re.compile(r'[A-Za-z][A-Za-z0-9]*(?:[._\-][A-Za-z0-9]+)+')
_IDENT_MIN = 4


def _flat_idents(s):
    """把 s 里带分隔符的 ASCII 标识符压平：max_len -> maxlen。"""
    out = []
    for m in _IDENT_RUN.findall(s or ''):
        flat = re.sub(r'[^a-z0-9]', '', m.lower())
        if len(flat) >= _IDENT_MIN:
            out.append(flat)
    return out


def tok(s, drop_stop=True):
    ws = []
    for w in jieba.lcut(s):
        w = w.strip().lower()
        if not w or re.fullmatch(r'[\s\W_]+', w):
            continue
        if drop_stop and w in STOP:
            continue
        ws.append(w)
    ws += _flat_idents(s)
    return ws


# ── 字级 bigram 通道 + RRF 融合（默认关，只当兜底）────────────────────
RRF_K = 60                 # RRF 平滑常数：score = Σ w / (RRF_K + rank)
RRF_W_WORD = 1.0           # 词级 BM25 通道权重
# 字级 bigram 通道权重。**默认 0.0 = 关闭**，因为实测是负收益（改大有改大的坏处）：
#     wb=0.0  69.2% / 84.6%（基线）    wb=0.2  66.7% / 82.1%
#     wb=0.3  69.2% / 79.5%            wb=0.5  66.7% / 79.5%
#     wb=0.7  66.7% / 76.9%            wb=1.0  64.1% / 76.9%   (hit@1 / hit@3)
# 口径：tools/test_retrieval_real.py（39 条有明确材料的真实提问）。详见文件头。
RRF_W_BIGRAM = 0.0
# 「换说法」扩展词走**独立通道**，不混进材料词袋。
# 混进去实测会掉 hit@3（词袋变大 → BM25 长度归一化惩罚所有真实词）：
#   混入词袋：换说法 hit@1 15.8→31.6 但原题 hit@3 94.9→92.3、换说法带历史 hit@3 84.2→78.9
# 独立通道只影响"排得上名的块"，不动其余块的分数，两边都不吃亏。
RRF_W_EXPAND = 0.0
# 词袋法里，扩展词算不算进 BM25 的长度归一化（dl）。
# True = 只算原文长度（不掉 hit@3，但增益也小）；False = 一起算（增益大，原题 hit@3 -2.6）。
DL_EXCLUDE_EXPAND = True
# 兜底模式：词级通道候选 chunk 数 < BIGRAM_FALLBACK_MIN_HITS 时，让 bigram 通道顶上排序。
# 正常提问（哪怕很口语）词级通道都远不止 2 个候选，所以这条兜底在真实回归集上不触发，
# 只防"整句虚词/极短"到词级一路全军覆没的极端情况。
BIGRAM_FALLBACK = True
BIGRAM_FALLBACK_MIN_HITS = 2
BIGRAM_FALLBACK_W = 1.0    # 兜底时 bigram 通道的 RRF 权重
RRF_SCORE_SCALE = float(RRF_K)   # 返回分数放大倍数：某一路排第 1 得 RRF_W，两路都排第 1 得两路权重和
BIGRAM_TITLE_WEIGHT = 1    # 文件名进 bigram 词袋重复几次（词级那边是 TITLE_WEIGHT=4）
_PUNCT_RE = re.compile(r'[\s\W_]+', re.UNICODE)

# 两字都是虚词的 bigram（"的话""这个""就要"）对区分材料没有信息量，直接丢掉；
# 只要有一个字是实词就保留（"听得""懂这"留着有用）。
BIGRAM_STOP_CHARS = set(
    '的了着过是在有没木我你他她它咱我们你们他们这那哪谁什怎咋么吗嘛呢吧啊嗯哦呀哈呃唉哎'
    '就都也还又很更最太挺和与跟同对把被给让从到向往为因所但而如或及以并则等之其此该'
    '一个条点种台层次上下中里内外面间时多少几可会要想说做用来去回再又还各种些位件些'
)


def bigram_tok(s):
    """字级 bigram 切词器：去掉空白和标点 → 相邻两字成 token。

    注意是先删标点再切，所以 bigram 会跨句读边界（"问对，还是" → "对还"），
    这是有意的：ASR 出来的断句本来就不可靠。
    """
    t = _PUNCT_RE.sub('', (s or '')).lower()
    if len(t) < 2:
        return [t] if t else []
    out = []
    for i in range(len(t) - 1):
        if t[i] in BIGRAM_STOP_CHARS and t[i + 1] in BIGRAM_STOP_CHARS:
            continue
        out.append(t[i:i + 2])
    return out


class BM25:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        # 文件名（含项目名）加权进词袋：问"推荐系统那个项目"时先命中 项目A_推荐系统.md
        exp = load_expand()
        # 每块材料附上"面试官可能怎么问这块"的通用说法（离线生成）。
        # 这是换说法检索的唯一手段：材料写"LangGraph 九节点有向图工作流"，
        # 面试官问"你那套任务编排怎么设计的"，字面零重合，只有预生成的
        # 问法能把两者连起来。实测见 tools/kb_expand.py 顶部注释。
        self.exp = [' '.join(exp.get(_chunk_key(c[1]), [])) for c in chunks]
        base = [tok(c[1]) + tok(os.path.splitext(c[0])[0]) * TITLE_WEIGHT for c in chunks]
        self.docs = [base[i] + tok(self.exp[i]) * EXPAND_WEIGHT for i in range(len(chunks))]
        self.tf = [Counter(d) for d in self.docs]
        # 长度只算**原文**，扩展词不进 dl —— 这是词袋法能不掉 hit@3 的关键。
        # 把扩展词也算进 dl 的话，这一块的 dl/avgdl 一起变大，
        # BM25 的长度归一化会把**该块所有真实词**的分数压下去，得不偿失（实测 hit@3 -2.6）。
        # IDF 仍按含扩展词的 docs 统计，否则扩展词自己拿不到 IDF 分（恒 0，白加）。
        self.dl = [len(d) for d in (base if DL_EXCLUDE_EXPAND else self.docs)]
        self.avgdl = sum(self.dl) / max(len(self.dl), 1)
        self.N = len(self.docs)
        self.idf = self._idf(self.docs)
        # 第二路索引：字级 bigram。文件名也给一点权重（"项目A_推荐系统" → 项目/目A/推荐…）
        self.bdocs = [bigram_tok(c[1]) + bigram_tok(os.path.splitext(c[0])[0]) * BIGRAM_TITLE_WEIGHT
                      for c in chunks]
        self.btf = [Counter(d) for d in self.bdocs]
        self.bdl = [len(d) for d in self.bdocs]
        self.bavgdl = sum(self.bdl) / max(len(self.bdl), 1)
        self.bidf = self._idf(self.bdocs)
        # 第三路（可选，默认关）：只用「面试官可能怎么问这块」的扩展词建索引。
        # 实测弱于词袋法（见 RRF_W_EXPAND 注释），默认 RRF_W_EXPAND=0 时
        # **不建这份索引**，省内存也省建库时间。
        self.has_exp = RRF_W_EXPAND > 0 and any(self.exp)
        if self.has_exp:
            self.edocs = [tok(self.exp[i]) * EXPAND_WEIGHT for i in range(len(chunks))]
            self.etf = [Counter(d) for d in self.edocs]
            self.edl = [len(d) for d in self.edocs]
            self.eavgdl = sum(self.edl) / max(len(self.edl), 1)
            self.eidf = self._idf(self.edocs)

    def _idf(self, docs):
        df = Counter()
        for d in docs:
            for w in set(d):
                df[w] += 1
        return {w: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for w, n in df.items()}

    def _bm25_scores(self, q, tf, idf, dl, avgdl, boost_src=None, boost=None):
        """一路 BM25 的原始打分，返回 [(score, chunk_index)]（按分数降序，只留 score>0）。"""
        k1, b = self.k1, self.b
        base = k1 * (1 - b)
        out = []
        for i in range(self.N):
            ti = tf[i]
            denom_add = base + k1 * b * dl[i] / avgdl
            s = 0.0
            for w in q:
                f = ti.get(w)
                if not f:
                    continue
                s += idf.get(w, 0.0) * f * (k1 + 1) / (f + denom_add)
            if s > 0:
                if boost_src and boost_src in self.chunks[i][0]:
                    s *= (TOPIC_BOOST if boost is None else boost)
                out.append((s, i))
        out.sort(key=lambda x: (-x[0], x[1]))     # 同分按块序，结果可复现
        return out

    def search(self, query, topk=4, boost_src=None, boost=None):
        """词级 BM25 与字级 bigram BM25 各自排名 → RRF 融合。

        返回 [(score, src, text)]，score = RRF_SCORE_SCALE * Σ 通道权重/(RRF_K + rank)。
        量纲：某一路上排第 1 得对应通道权重，两路都排第 1 得权重之和。

        默认 RRF_W_BIGRAM=0.0（bigram 实测负收益，见文件头），此时：
          · 词级候选 >= BIGRAM_FALLBACK_MIN_HITS → 纯词级 BM25，排序与改造前逐条一致；
          · 词级候选更少 → 走兜底，用 bigram 通道补位（BIGRAM_FALLBACK*）。
        想复现两路融合就把 RRF_W_BIGRAM 调大（会掉点，别调）。
        """
        qw = tok(query)
        if not qw:                     # 全是虚词（"嗯，好的"）→ 退回原始分词
            qw = tok(query, drop_stop=False)
        word = self._bm25_scores(qw, self.tf, self.idf, self.dl, self.avgdl, boost_src, boost)
        if RRF_W_BIGRAM > 0:                                  # 显式开启两路融合
            w_big = RRF_W_BIGRAM
        elif BIGRAM_FALLBACK and len(word) < BIGRAM_FALLBACK_MIN_HITS:
            w_big = BIGRAM_FALLBACK_W                         # 兜底：词级没货，bigram 顶上
        else:
            w_big = 0.0
        big = (self._bm25_scores(bigram_tok(query), self.btf, self.bidf, self.bdl, self.bavgdl,
                                 boost_src, boost)
               if w_big > 0 else [])
        fused = {}
        for rank, (sc, i) in enumerate(word, 1):
            e = fused.setdefault(i, [0.0, 0.0, 0.0])
            e[0] += RRF_W_WORD / (RRF_K + rank)
            e[1] = sc
        for rank, (sc, i) in enumerate(big, 1):
            e = fused.setdefault(i, [0.0, 0.0, 0.0])
            e[0] += w_big / (RRF_K + rank)
            e[2] = sc
        if self.has_exp and RRF_W_EXPAND > 0:
            for rank, (sc, i) in enumerate(
                    self._bm25_scores(qw, self.etf, self.eidf, self.edl, self.eavgdl,
                                      boost_src, boost), 1):
                fused.setdefault(i, [0.0, 0.0, 0.0])[0] += RRF_W_EXPAND / (RRF_K + rank)
        # 注：独立通道实测**弱于词袋法**（单元测试 70.5% vs 100%）。RRF 是排名制，
        # 一个通道排第 1 只给 weight/(K+1)，量级比不过直接进 TF-IDF 的贡献。
        # 保留代码是为了以后能再试权重，默认 RRF_W_EXPAND=0 不生效。
        # 排序：融合分 → 词级原始分 → bigram 原始分 → 块序（全确定性，便于回归复现）
        order = sorted(fused.items(), key=lambda kv: (-kv[1][0], -kv[1][1], -kv[1][2], kv[0]))
        return [(e[0] * RRF_SCORE_SCALE, self.chunks[i][0], self.chunks[i][1])
                for i, e in order[:topk]]


_cache = {}


def _warn_profile():
    """包名打错时别静默降级 —— 面试当天才发现只剩几份通用材料就晚了。"""
    prof = active_profile()
    if not prof:
        return
    root = settings.knowledge_root()
    pack = os.path.join(root, prof)
    if not os.path.isdir(pack):
        cand = list_profiles()
        print('[知识库] 资料包【%s】不存在：%s\n'
              '  现在只会用到 knowledge/_base/ 里的通用材料。\n'
              '  建包：python tools/profiles.py --new %s\n'
              '  已有的包：%s' % (prof, pack, prof, '、'.join(cand) or '（无）'))
    elif not os.path.isdir(os.path.join(root, '_base')):
        print('[知识库] 启用了资料包【%s】但还没有 knowledge/_base/ —— '
              '通用个人材料（项目报告、简历…）会被漏掉。' % prof)


def build():
    if 'idx' in _cache:
        return _cache['idx'], _cache['chunks']
    _warn_profile()
    docs = load_docs()
    chunks = []
    for name, text in docs:
        chunks += chunk(text, name)
    if not chunks:
        # 空语料不报错也能"跑起来"，但快答线会退化成让模型凭印象编 —— 面试当天才发现就晚了。
        print('[知识库] 语料 0 块：检索不到任何材料，答案会没有事实依据。\n'
              '  把面试材料（.md / .txt）放进 %s，或改 config/settings.json 的 corpus_globs。\n'
              '  当前 glob: %s' % (os.path.join(settings.REPO_ROOT, 'knowledge'), corpus_globs()))
    idx = BM25(chunks)
    _cache['idx'], _cache['chunks'] = idx, chunks
    return idx, chunks
