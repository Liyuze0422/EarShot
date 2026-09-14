# -*- coding: utf-8 -*-
"""快答线:问题分类 -> 本地检索 -> DeepSeek 流式生成"可照念"的答案。

核心设计(按用户要求):
  1. 说【思路】而不是【具体做法】—— 先讲判断依据/权衡/排除,再讲落地
  2. 按问题类型分策略 —— 经历题严格依据材料;设计题结合对方公司具体分析
  3. 设计题没有材料可依 -> 禁止编造,改为给方法论框架
"""
import os
import sys
import time
import re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings
from knowledge import build, detect_topic, tok
import knowledge          # 只为取 knowledge.RRF_K（RRF 融合常数）
from router import classify, LABEL

# 密钥：环境变量 TP_API_KEY 优先，其次 KEY_FILE 指向的文件（默认 <仓库根>/config/api_key.txt）。
# 路径与 base_url / model 都来自 server/settings.py（环境变量 > config/settings.json > 默认值）。
KEY_FILE = settings.api_key_path()
BASE_URL = settings.get('base_url')
# 默认模型 deepseek-flash。官方支持的模型名只有 deepseek-flash 和 deepseek-v4-pro（API 报错信息里明确列出）。
# deepseek-v4-flash 是能用的别名，但交替顺序 A/B（n=15）实测 deepseek-flash 更快：
#   首字中位 546ms vs 694ms（tools/compare_model_id.py）。
# 全项目统一走 flash，不用 pro（思考模型，首字 6690ms，面试场景用不上）。
# 换模型：改 config/settings.json 的 llm_model，或设环境变量 TP_LLM_MODEL。
MODEL = settings.get('llm_model')
EXTRA_BODY = {'thinking': {'type': 'disabled'}}
# 超时：SDK 默认 read timeout 是 600 秒、默认重试 2 次 —— 面试时网络一抖，
# 屏幕上就永远停在半句话，不报错也不兜底。8 秒够用（正常首字 0.3~1.5s），重试 1 次，
# 再失败就走题库兜底。
ANSWER_TIMEOUT = float(os.environ.get('TP_TIMEOUT', '8'))
ANSWER_RETRIES = int(os.environ.get('TP_RETRIES', '1'))
# 首字看门狗：超过这个秒数还没吐第一个字，后端发一条 slow 事件，UI 提示"这题自己先讲"
# 实测：题面组装（检索 + 题库 + 名词判定）+ 网络往返，正常也要 1.5~2.5 秒才出第一个字。
# 阈值定 3 秒会在好网络下误报，定 5 秒才既能兜住真卡住、又不吓人。
FIRST_TOKEN_WARN = float(os.environ.get('TP_FIRST_WARN', '5'))
DEFAULT_TOPK = 3
DEFAULT_CUT = 400


def _config_file(name):
    """定位 config/ 下的用户文件：优先你自己的，没有就退回随仓库发布的 .example 模板。

    company.md / known_terms.md / never_used.md 三份都被 .gitignore 忽略（属于你本机），
    公开仓库里只有对应的 .example.md —— 这样 clone 下来什么都不改也能跑，
    而你一旦自己建了同名文件，用的就是你的。
    """
    p = os.path.join(settings.REPO_ROOT, 'config', name)
    if os.path.exists(p):
        return p
    # company.md -> company.example.md（注意 .example 插在扩展名之前，不是直接接在末尾）
    stem, ext = os.path.splitext(name)
    ex = os.path.join(settings.REPO_ROOT, 'config', stem + '.example' + ext)
    return ex if os.path.exists(ex) else p


COMPANY_FILE = _config_file('company.md')

# ── 所有策略共享的铁律 ────────────────────────────────────────────────
COMMON = """你是我的线上面试实时提词器。面试官刚问了一个问题,我要一份能立刻照念的口语化答案。

铁律:
1. 重点是【思路】不是【做法】:先讲我怎么判断这个问题、权衡了什么、排除了什么方案,
   再简要说做法。宁可少讲实现细节,也要把判断讲清楚。
   注意:【思路】那一句是要抬头看屏幕直接念出来的,所以它本身必须就是结论/判断
   (例:"这块我做的是插件层,不碰他原有系统")。
   不要写成"我判断面试官想问的是…""他问的是X不是Y""我得先分清…"这种描述性开场——
   面试官不需要听我分析他的提问动机。真实一面回放实测:不写这条时 35% 的核心句
   都在描述面试官的意图,照念很尴尬。
2. 事实和数字只能来自 <材料> 或 <上文>(上文里已经确认过的数字可以直接用)。
   这两处都没有的数字一个字都不许编;没有就写"无"。
   设计题/假设题没有材料可依时,可以讲思路框架,但绝不能编造对方的业务数据或我自己的经历。
3. 口语化,像人在讲自己的判断。不要 markdown 标题、不要分点符号、不要堆技术名词。
4. 直接说"我",不要"该候选人"。"""

# ── 分类型策略 ────────────────────────────────────────────────────────
S_EXPERIENCE = COMMON + """

这是一道【经历深挖题】。严格按下面三段输出,不要任何额外文字:
【思路】一句结论性的话,能直接念给面试官听,40 字以内。不要描述他为什么这么问。
【展开】3 到 4 句。先说"为什么这么做"(权衡过什么、排除了什么),再一句带过做法。150 字以内。
【数字】材料里可引用的具体数字,逗号分隔;没有就只写:无"""

S_DESIGN = COMMON + """

这是一道【开放式设计题】,没有标准答案,考的是我搭思路的能力。
注意:<材料> 里可能没有直接对应的内容,这是正常的,不要硬套我的项目经历。

严格按下面三段输出,不要任何额外文字:
【思路】一句结论:这类问题的核心矛盾在哪,或者我会先定死哪个前提。40 字以内,能直接念。
【拆解】3 到 4 句。讲我按什么维度拆、每步怎么取舍、为什么这么取舍。
         如果 <公司背景> 里有对方业务信息,必须结合他们的实际情况说,不要讲通用套话。150 字以内。
【落点】一两句:如果真做,我第一步先动什么、先验证什么。"""

S_BEHAVIOR = COMMON + """

这是一道【行为面试题】。严格按下面三段输出,不要任何额外文字:
【思路】一句结论:这件事的定性 + 我的处理原则。40 字以内,能直接念。
【展开】压缩版 STAR:情境一句、我的判断一句、动作一句、结果一句(有数字就带上)。150 字以内。
【数字】材料里可引用的具体数字,没有就只写:无"""

S_MOTIVATION = COMMON + """

这是一道【动机/意向题】。严格按下面三段输出,不要任何额外文字:
【思路】一句结论:我和对方最核心的匹配点。40 字以内,能直接念。
【展开】3 到 4 句。结合 <公司背景> 说为什么匹配,用我材料里的具体经历做证据,不喊口号。150 字以内。
【数字】材料里可引用的具体数字,没有就只写:无"""

S_REVERSE = """你是我的线上面试提词器。面试官把提问权交给我了,这是加分机会。

要求:
1. 给 2 到 3 个能体现我认真研究过对方、且有水平的问题。
2. 不问薪资福利,不问"公司文化怎么样"这种空问题。
3. 每个问题后面用一句括号说明我想从这个问题里了解什么。

结合 <公司背景> 写。直接输出问题本身,不要额外解释。"""

S_GENERAL = COMMON + """

严格按下面三段输出,不要任何额外文字:
【思路】一句结论,能直接念,40 字以内。
【展开】3 到 4 句,150 字以内。
【数字】材料里可引用的具体数字,没有就只写:无"""

S_GAP = """你是我的线上面试实时提词器。面试官问的这个词/这块技术，**我的材料里完全没有准备过**。

这种情况下，编造经历是最致命的：面试官只要追问两层就穿帮，而且会直接判我不诚实。
所以这题必须走"坦诚 + 展示推理和学习能力"的路子，严格按下面四段输出：

【坦诚】一句话，直接承认没接触过。不要绕，不要用"了解一点""接触过一些"这种模糊话。
        例：这块我确实没有实际用过，不装懂。
【我的理解】我知道的部分 + 我按原理推它大概是干什么的（要明确是推导，不是我的经历）。
        80 字以内。
【迁移与路径】我哪块经验能接上、为什么能接上；如果要用，我第一步会从哪个点入手去学或验证。
        80 字以内。只许引用材料里明确写过的经历（RAG / Agent 编排 / 检索排序 / LoRA 微调 这类），
        **不许给它换名字或升格**——不要写"研究链路""从 0 到 1""全链路"这种材料里没有的措辞。
【反问】一句能让我快速对齐的问题。**首选用来对齐术语**（"你说的 X 是指……吗？"）——
        万一是语音转写把我没听过的词写歪了，面试官会当场纠正，比我照着错词答强。

铁律（违反即无效）：
0. **四段必须各自以【坦诚】【我的理解】【迁移与路径】【反问】开头。**
   没有标记我看不出该念哪一句（实测有一次模型把标记全丢了，屏幕上大字只剩省略号）。
1. **绝对不许出现"我用过""我做过""我拿它""我在项目里用过"这类第一人称经历句
   —— 尤其不许把它安在面试官问的那个名词上。**
   我没有这块经历，一个字都不许编。
2. 不许编造这块钱的具体细节（工具名、参数、指标、踩过的坑）。
3. 口语、能直接念，不要 markdown，不要分点符号。"""


STRATEGIES = {
    'gap': S_GAP,
    'experience': S_EXPERIENCE,
    'design': S_DESIGN,
    'behavior': S_BEHAVIOR,
    'motivation': S_MOTIVATION,
    'reverse': S_REVERSE,
    'unknown': S_GENERAL,
}


# ── 短问题（确认类）要短答 ──────────────────────────────────────────────
# 实测（tools/judge_answers.py，判分模型给 2 分及以下的 8 条里有 4 条是这个毛病）：
# 面试官只问"是今年毕业的是吗？""就是做了微调是吗？"，答案却回了 150 字项目自证。
# 问题有多重，答案就该有多长。
_BRIEF_STOP = re.compile(r'为什么|怎么|如何|哪些|哪些方面|讲讲|介绍|举例|详细|具体|展开')


def is_brief(q):
    """这句是不是"只要个是/不是 + 一句说明"的短确认。"""
    q = (q or '').strip()
    if not q or len(q) > 30:
        return False
    if _BRIEF_STOP.search(q):
        return False
    return bool(re.search(r'吗|嘛|是吧|对吧|是吗|对不对|是不是|有没有|[？?]$', q))


# 列举型问题（"还做过哪些业务""调研哪些方面""多少人一个小组""占比多少"）。
# 实测这几个是判分最低的：#33/#37/#39/#42 全部答成"怎么做的"，一个清单都没给出来。
_ENUM = re.compile(r'哪些|哪几|有几种|多少人|几个(人|角色|节点|模块)|占比|几成|分别(是|做了)|覆盖哪些|还有(什么|哪些)')

ENUM_HINT = ('\n\n⚠️ 注意：面试官在让你**列举**。第一句就把清单给出来'
             '（2~4 条，每条一个短词组，例如"法规问答、路径规划、故障诊断这三块"），'
             '之后最多用一句话补充。不要在清单之前讲实现过程，不要报代码常量、文件名、变量名。')

BRIEF_HINT = ('\n\n⚠️ 注意：面试官这句很短，只是要个确认。'
              '整段答案压到 60 字以内——第一句直接给"是/不是"或结论，再补一句最必要的说明，'
              '不要展开项目细节，不要报一串数字。')


def load_key():
    """DeepSeek 密钥：环境变量 TP_API_KEY 优先，其次 config/api_key.txt。

    两处都没有时抛 RuntimeError，文案里带修复步骤（见 settings.api_key()）。
    """
    return settings.api_key()


def _company_path():
    """公司背景文件：config/company.md 优先，其次是当前资料包里的 company.md。

    这样"按公司分包"时，公司介绍可以跟着包走（knowledge/<包名>/company.md），
    不用每换一家都去手改 config/company.md。config/company.md 仍然是最高的显式覆盖。
    """
    if os.path.exists(COMPANY_FILE):
        return COMPANY_FILE
    prof = knowledge.active_profile()
    if prof:
        p = os.path.join(settings.knowledge_root(), prof, 'company.md')
        if os.path.exists(p):
            return p
    return ''


def load_company():
    """读目标公司背景,过滤掉模板占位行。"""
    path = _company_path()
    if not path:
        return ''
    out = []
    for line in open(path, 'r', encoding='utf-8'):
        s = line.strip()
        if not s or s.startswith('#') or s.startswith('>') or s.startswith('（例'):
            continue
        out.append(s)
    return '\n'.join(out).strip()


# 话题持久化：真实面试官会连着十几轮追同一个项目，中间夹杂大量既没实词、
# 也没话题词的短追问（"还有哪些角色呢？""你做的占比多少？"）。
# 这类题只靠本句检索必然跑偏——实测 39 条里有 6 条是这么丢的，
# 它们的正确材料在词级通道排到 rank 5/7/16/5/54/43。
# 做法：本句检测不到话题词时，回看上文最多 8 句，沿用最近一次明确出现过的话题。
# 实测 hit@1 69.2% → 84.6%，hit@3 保持 84.6%（工具：tools/sweep_sticky.py）。
# ⚠️ 风险：这场面试 90% 时间在聊同一个项目，所以"记忆话题"收益很大；
# 换一场如果面试官在几个项目间横跳、而新话题又不在 TOPIC_TERMS 里，这个加权可能帮倒忙。
# 好在只有一个常量，掉分了就把它调小或设 0。
STICKY_LOOKBACK = 8
STICKY_BOOST = 3.0


# HR/履历类问题不该继承项目话题：实测不加这条守卫时，"你做的占比多少""都是同一家公司对吧"
# 这几条会继承到上一条项目问答，把真正该看的 HR 材料挤掉（hit@3 因此掉了 3 条）。
HR_HINT = re.compile(r'毕业|离职|换一家|同一家公司|团队|小组|角色|占比|简历|薪资|待遇|职业规划|为什么.{0,4}(换|跳|离开)')

# 2026-09-13 修正：命中后原来 return None（= 完全放弃加权），实测 hit@1 82.1%。
# 改成"把加权目标换成 HR 材料"后 87.2%，hit@3/并集同步 +5.1 ——
# 守卫的**意图**是对的（HR 题别继承项目话题），**动作**错了（该换目标，不是放弃加权）。
# 空列表 = 退回旧行为。
# HR/履历材料的**文件名关键字**（子串匹配，任一命中即可）。
# ⚠️ 这是示例值：换成你自己 HR/简历材料的文件名片段。一个都没匹配上时退回"不加权"
#    （等价于旧行为），所以填错了不会把检索搞坏，只是没收益。
# 空列表 = 明确关闭这条。
HR_FILES = ['简历', 'HR', '履历']


def _pick_hr(sources=None):
    """从语料文件名里挑一个**真正存在**的 HR 关键字，避免写死私人文件名。"""
    if not HR_FILES:
        return None
    for key in HR_FILES:
        if sources and any(key in s for s in sources):
            return key
    return HR_FILES[0]


def detect_topic_sticky(question, history, lookback=STICKY_LOOKBACK, sources=None):
    t = detect_topic(question)
    if t:
        return t
    if HR_HINT.search(question or ''):
        return _pick_hr(sources)
    for h in reversed(list(history or [])[-lookback:]):
        t = detect_topic(h)
        if t:
            return t
    return None


# ── 材料外问题（面试官甩出一个我没准备过的名词）────────────────────────
# 实测事故：问"你用过 eBPF 做可观测性吗？"——语料里 eBPF 出现 0 次，
# 模型却编出"我用它做过节点级观测，挂 kprobe 抓系统调用、用 map 吐指标"。
# 问 Temporal 同样编了"用过，但我的主力是 LangGraph"。
# 这种答案听起来最专业，但面试官追问两层就崩，比直接说"没接触过"危险得多。
# 判定：问题里出现了语料里根本不存在的技术专名，且是在问我"了不了解/用没用过"。
_PROBE = re.compile(r'了解|用过|用吗|会用|用过吗|熟吗|熟悉|接触过|接触|会不会|听说过|听说|懂吗|懂不|'
                    r'掌握|有没有用|搞过|写过|上手|精通|知道|经验|水平|研究过|了解过|玩过|干过|做过|'
                    r'讲讲|讲一下|介绍一下|说说|聊聊|怎么用|怎么看')
_ASCII = re.compile(r'[A-Za-z][A-Za-z0-9.+#_-]{1,20}')
_VER = re.compile(r'^[A-Za-z]?\d+(\.\d+)*$')          # v2.1 / 3.0 / 1.2.3 是版本号，不是技术专名
_STOP = {'ok', 'ppt', 'ai', 'app', 'demo', 'yes', 'no', 'id'}
_VOCAB = None
_FREQ = None

# config/known_terms.md：材料里没写、但我确实会的东西（用户自己维护）。
# 起因实测：语料里没有 onnx/k8s/kubernetes，问"你用过 ONNX 吗"会被判成"没准备过"，
# 我就要对着自己天天在用的东西说"我没接触过" —— 这是最难看的一类误报。
KNOWN_FILE = _config_file('known_terms.md')
# config/never_used.md：材料里出现过、但我其实没做过的东西。
# 词表只能回答"我材料里有没有"，回答不了"材料里写了但我到底做没做过"——
# 一个词在报告里被提到过一次，不代表我会。这张表就是补这个洞：
# 命中即走"坦诚"，不管材料里写了多少。
NEVER_FILE = _config_file('never_used.md')
_KNOWN = None
_NEVER = None


def _load_list(path):
    """读一份词表：一行一个（空格/逗号/顿号也能分隔）。

    跳过 # 注释和 markdown 的 > |-* 说明行 —— 否则文件开头的说明文字会被
    当成词条吃进来（实测：never_used.md 的说明行把 "tools/build_terms.py"
    变成了"我其实没做过"的一个词）。
    """
    s = set()
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            line = line.split('#')[0].strip()
            if not line or line[0] in '>-*|':
                continue
            for w in re.split(r'[\s,，、/|*`]+', line):
                w = w.strip().lower()
                if len(w) >= 2:
                    s.add(w)
    return s


def never_used():
    global _NEVER
    if _NEVER is None:
        _NEVER = _load_list(NEVER_FILE)
    return _NEVER


def never_hit(question):
    """问题里出现了"材料里有、但我其实没做过"的词。ASCII 按词边界，中文按子串。"""
    q = normalize_terms(question).lower()
    out = []
    for t in never_used():
        if t.isascii():
            if re.search(r'(?<![a-z0-9])%s(?![a-z0-9])' % re.escape(t), q):
                out.append(t)
        elif t in q:
            out.append(t)
    return out


def known_terms():
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = _load_list(KNOWN_FILE)
    return _KNOWN


def reset_cache():
    """清掉随语料变化的缓存。

    切资料包必须调 —— _VOCAB / _FUZZ 是模块级 memo，不清的话换了 knowledge/
    内容之后还会拿旧词表做纠错和缺口判定（termfix 也依赖它们）。
    """
    global _VOCAB, _FREQ, _FUZZ
    _VOCAB = _FREQ = _FUZZ = None


def _vocab():
    global _VOCAB, _FREQ
    if _VOCAB is None:
        idx, chunks = build()
        s = set(); c = {}
        for _, txt in chunks:
            for t in tok(txt, drop_stop=False):
                tl = t.lower()
                s.add(tl)
                c[tl] = c.get(tl, 0) + 1
        _VOCAB, _FREQ = s, c
    return _VOCAB


# ── ASR 写坏的专名要还原 ──────────────────────────────────────────────
# 面试官的话和我的材料**都要过一遍 ASR**，专有名词经常被写坏：
#   "r a g"（散开）·"Loar"（拼错）·"GRAPHRAG"（全大写）·"ONN X"（拆开）
# 实测（tools/test_gap_fp.py 第 2-B 节）：不还原时两头都错 ——
#   "E B P F" 漏判（该走"坦诚"却走了普通策略，等于放它去编）
#   "Loar"    误判（我天天在用的 LoRA 被判成"没准备过"）
# 所以判定前先归一化，再对已知词表做一次模糊匹配。
_SPACED = re.compile(r'(?<![A-Za-z0-9])(?:[A-Za-z][\s.\-_]+){1,}[A-Za-z](?:[\s.\-_]*\d+)?(?![A-Za-z0-9])')


def normalize_terms(text):
    """把 ASR 写坏的英文专名拼回去：r a g → rag、l l m → llm、ONN X 不动。"""
    return _SPACED.sub(lambda m: re.sub(r'[\s.\-_]', '', m.group(0)), text or '')


def _ed(a, b, cap=3):
    """编辑距离（含相邻换位），超过 cap 早停 —— 只用来判断"像不像"。"""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    d = [list(range(len(b) + 1))]
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(d[i - 1][j] + 1, cur[j - 1] + 1, d[i - 1][j - 1] + (ca != cb)))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:   # Loar ↔ LoRA
                cur[j] = min(cur[j], d[i - 2][j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        d.append(cur)
    return d[-1][-1]


_FUZZ = None


def _freq():
    _vocab()
    return _FREQ


def _fuzz_index():
    """按（首字母, 长度）分桶：(词, 是否在人工词典里, 语料里出现几次)。

    分桶是为了别把每个词都跟整个词表算一遍编辑距离；带频次和词典标记是为了
    **挑对候选** —— 第一版没有排序，"Loar" 被捞成了 "load"、"ONN" 被捞成了 "one"，
    判定没出错，但提示词会告诉模型"他说的是 load"，那比不提示更糟。
    """
    global _FUZZ
    if _FUZZ is None:
        kn = known_terms(); fr = _freq()
        d = {}
        for w in _vocab():
            if w.isascii() and w.isalpha() and len(w) >= 3:
                d.setdefault((w[0], len(w)), []).append((w, w in kn, fr.get(w, 0)))
        for w in kn:                      # 我会、但材料里没写的词也得进候选池
            if w.isascii() and w.isalpha() and len(w) >= 3 and w not in _vocab():
                d.setdefault((w[0], len(w)), []).append((w, True, 0))
        _FUZZ = d
    return _FUZZ


def fuzzy_known(w):
    """这个词是不是某个我认识的词被写坏了？是就返回正确的那个。

    判据（防止瞎猜）：编辑距离 ≤ cap，且候选要么在我的人工词典里、要么语料里
    至少出现过 2 次 —— 只出现过一次的词很可能本身就是转写噪音，不足为凭。
    """
    lw = (w or '').lower()
    if len(lw) < 3 or not lw.isalpha():
        return None
    cap = 1 if len(lw) <= 5 else 2        # Loar→lora 换位算 1；graprag→graphrag 多一个字母算 1
    idx = _fuzz_index()
    best = None
    for ln in range(len(lw) - cap, len(lw) + cap + 1):
        for cand, in_kn, freq in idx.get((lw[0], ln), ()):
            d = _ed(lw, cand, cap)
            if d > cap or not (in_kn or freq >= 2):
                continue
            key = (d, 0 if in_kn else 1, -freq, cand)   # 距离最短 > 人工词典优先 > 语料常见 > 字典序
            if best is None or key < best[0]:
                best = (key, cand)
    return best[1] if best else None


def alias_terms(question):
    """他说的词像是被转写写坏了 —— 返回 [(听成的, 应该是的)]，用来提示模型按后者答。"""
    v = _vocab(); kn = known_terms()
    out = []
    for raw in _ASCII.findall(normalize_terms(question)):
        w = raw.strip('.-_+'); lw = w.lower()
        if len(lw) < 3 or lw in v or lw in kn or not lw.isalpha():
            continue
        hit = fuzzy_known(lw)
        if hit:
            out.append((w, hit))
    return out


def unknown_terms(question):
    """返回问题里语料中查无此词的技术专名（判断真正的"没准备过"）。"""
    v = _vocab(); kn = known_terms()
    out = []
    for raw in _ASCII.findall(normalize_terms(question)):
        w = raw.strip('.-_+')
        lw = w.lower()
        if len(lw) < 2 or lw in v or lw in kn or lw in _STOP:
            continue
        if len(re.findall(r'[A-Za-z]', lw)) < 2:   # "v2.1""3D" 这种一个字母带数字的，不是专名
            continue
        if _VER.match(w):
            continue
        if fuzzy_known(lw):        # 像是我认识的词被转写写坏了（Loar→lora），不算没准备过
            continue
        out.append(w)
    return out


def is_unknown_gap(question, topic_ok=None):
    """面试官问的是一块我材料里没有的东西 —— 必须走"坦诚 + 不编经历"策略。"""
    q = question or ''
    miss = unknown_terms(q) + unknown_cjk(q) + never_hit(q)
    if not miss:
        return []
    if not _PROBE.search(q):
        return []
    return miss


_CJK = re.compile(r'[\u4e00-\u9fff]{3,12}')
_JIEBA_FREQ = None
# 只要成分里出现虚词，拼出来的东西就一律不算"专名"（否则"双塔你""这块怎么"都能中）。
# 用黑名单而不是白名单：白名单会误杀"存算分离"(分离/v)、"具身智能"(具身/vn) 这类真专名。
_POS_BAD = ('r', 'u', 'p', 'c', 'd', 'm', 'q', 't', 'f', 'y', 'e', 'o', 'w', 'h', 'k')
# 提问本身带进来的动词，会跟真名词粘在一起（"了解湖仓一体""具身智能有"）。
# 只在两头剥这些词，不能按词性剥 —— "存算分离"两个成分都是动词，剥完就没了。
_GLUE = set('了解 用过 用 做过 做 有 是 会 知道 接触 听说 听 讲 说 介绍 研究 学 熟悉 掌握 '
            '写过 搞过 上过 玩过 干过 了解过 记得 见过 试过 搞 弄 整'.split())


def _trim_glue(part):
    while len(part) > 1 and part[0][0] in _GLUE:
        part = part[1:]
    while len(part) > 1 and part[-1][0] in _GLUE:
        part = part[:-1]
    return part


def _jieba_freq():
    global _JIEBA_FREQ
    if _JIEBA_FREQ is None:
        import jieba
        jieba.initialize()
        _JIEBA_FREQ = set(jieba.dt.FREQ)
    return _JIEBA_FREQ


def unknown_cjk(question, max_len=8):
    """中文侧：材料里没有、而且不是日常词的"专业/生造名词"。

    只用"语料查无此词"判中文会大面积误报 —— 真实 44 题里就有 68 个这种词
    （一些/比如说/对于/起点/自信…），它们只是我的材料不会写。
    所以再加两道闸（宁可漏报，不可误报）：
      1) 组成成分里至少有一个连 jieba 通用词典都不认识（生造词）：
         湖仓一体→湖仓、存算分离→存算、具身智能→具身；
         而 知识图谱/强化学习/联邦学习 每个成分 jieba 都认识，就不判。
      2) 成分必须全是名词性成分，否则"双塔你""这块怎么"也会被拼成"新名词"。
    """
    import jieba.posseg as pseg
    v = _vocab(); jf = _jieba_freq(); kn = known_terms()
    out = []
    for run in _CJK.findall(normalize_terms(question)):
        toks = [(w, f) for w, f in pseg.cut(run) if w.strip()]
        for n in (1, 2, 3):
            for i in range(len(toks) - n + 1):
                part = _trim_glue(toks[i:i + n])
                cand = ''.join(w for w, _ in part)
                if not part or not (3 <= len(cand) <= max_len) or cand in v or cand in jf:
                    continue
                if cand.lower() in kn:
                    continue
                if any(f[0] in _POS_BAD for _, f in part):
                    continue
                if any(len(w) >= 2 and w not in jf for w, _ in part):
                    out.append(cand)
    keep = []
    for c in sorted(set(out), key=len, reverse=True):   # 长的优先，短的是它的子串就丢掉
        if not any(c != k and c in k for k in keep):
            keep.append(c)
    return keep


def decide_qtype(question, qtype=None):
    """最终生效的策略名 —— 后端打标签和真正用哪套提示词必须是同一个口径。

    之前 build_messages 内部偷偷把 qtype 换成 gap，可 main.py 早就用 classify()
    的结果把标签发出去了：屏幕顶上是"经历深挖题"，答案走的却是"坦诚"那套。
    """
    qtype = qtype or classify(question)
    if qtype not in STRATEGIES:      # 手动提问时用户可能敲了句非提问，别让它 KeyError
        qtype = 'unknown'
    return 'gap' if is_unknown_gap(question) else qtype


def unbacked_terms(question):
    """我会、但材料里完全没写的词（白名单命中 + 语料零出现）—— 这类题不许编细节。"""
    v = _vocab(); kn = known_terms()
    out = []
    for raw in _ASCII.findall(question or ''):
        w = raw.strip('.-_+'); lw = w.lower()
        if lw in kn and lw not in v and not _VER.match(w) and len(re.findall(r'[A-Za-z]', lw)) >= 2:
            out.append(w)
    return out


def retrieve_bank(question, topk=2):
    """会前题库通道（口语问法 -> 已经写好的答案）。

    实测（tools/test_bank_union.py，39 条有明确材料的真实提问）：
      BM25 主检索 hit@1 64.1% / hit@3 76.9%
      会前题库     hit@1 43.6% / hit@3 61.5%
      **并集      hit@1 71.8% / hit@3 87.2%**
    题库补上了主检索漏掉的 4 条，全是"整句没实词"的口语追问。
    注意题库的补漏里也有噪音（问法像但话题不同），所以它只当**补充材料**，
    不单独决定答案。
    """
    try:
        import bank as B
        text, hits = B.material(question, topk=topk)
        return text, hits
    except Exception:
        return '', []


def offline_answer(question):
    """断网/快答线失败时的兜底：题库里有现成答案就直接铺，不调模型。"""
    try:
        import bank as B
        hits = B.lookup(question, topk=1)
        return (hits[0]['a'], hits[0]['src']) if hits else ('', '')
    except Exception:
        return '', ''


# ── 追问的"材料继承" ────────────────────────────────────────────────────────
# 最难的一类是【指代型追问】："还有哪些功能吗""你做的占比估计有多少"。
# 它们有内容词，但全是泛词（功能/角色/占比），与目标材料**零词面交集**——
# 这时任何 boost 都是乘在一个 0 分的 chunk 上，救不回来。实测 miss 的题全是这一类。
# 做法：把【上一题命中的那块材料】也检索一遍，和本句检索做 RRF 融合（不是替换，
# 主路权重仍是 1.0，所以原本排第 1 的不会因为这条路被踢出去）。
#
# 实测（39 条人工标注，20 多组参数扫过一遍；工具 _realdata/_work/verify.py）：
#   线上窗口 7 句：hit@1 82.1%→87.2%  hit@3 89.7%→94.9%  题库并集 hit@3 92.3%→97.4%
#   回归门窗口 8 句：hit@1 84.6%→89.7%  hit@3 92.3%→94.9%  题库并集 94.9%→97.4%
#   三项指标、两个窗口同时 +2.6~+5.1，没有一项掉。
# 参数落在一片平台上（回看 7~14 句、上一题最小长度 0~12 结果完全一致），不是刀尖调参。
#
# 开火率（实测，别以为它只对"短追问"生效）：
#   50 条人工标注真题里 42% 会开；28% 有话题词、12% 是 HR、18% 都不。
#   41 场真实录音捞出的 1924 条短追问候选里 95.9% 会开——但那个池子是混轨录音挖的，
#   大半是 ASR 碎片，只能当上界看。
# 它之所以安全：真实面试官会连着十几轮聊同一个项目，所以"上一题的材料"通常**本来就是**
# 这一题该看的材料（融合进去等于没影响）；真正起作用的少数几次才是话题被中断的追问。
#
# 回滚：INHERIT_ENABLE = False 一行退回旧行为；掉分了就把 INHERIT_WEIGHT 调小。
INHERIT_ENABLE = True
INHERIT_WEIGHT = 1.0        # 继承路在 RRF 里的权重（主路恒为 1.0）
INHERIT_BOOST = 3.0         # 继承路内部给"上一题那块材料"的加权
INHERIT_GATE_CHARS = 40     # 当前句短于这个字数才当追问
# True = 只在"上一题自己也抓到了话题"时才继承。更保守、hit@1 略高（89.7%），
# 但 hit@3 在 7 句窗口只有 92.3%（不像 False 那样两个窗口都稳在 94.9%），所以默认关。
INHERIT_NEED_PREV_ANCHOR = False

# 上一题"真正答过的"那次检索结果，只记一条。
# 为什么不用 history 反推：main.py 只把 recent[-8:][:-1] 共 7 句传进来，
# 而上一题当时看到的窗口里还有第 8 句——反推出来的锚点会丢（实测 #26 就是这么丢的）。
# 直接在每题检索完时记下来，和 history 怎么截断无关。
# 用 q 校验是否真的是上一题，防止乱序/重放时张冠李戴。
_LAST_MAT = {'q': None, 'src': None, 'anchor': None}

# ── 话题栈（2026-09-13）───────────────────────────────────────────────
# 单锚点不够用：面试官会连着十几轮聊同一个项目，中间插两句别的（公司、团队规模）
# 再绕回来，那时"上一题的材料"已经不是该项目了，单锚点就跟丢。
# 所以改成保留最近几个"在聊的东西"，由近及远分别融合；越远权重越低（衰减），
# 且超过 TTL 轮没再命中就忘掉 —— 否则一个早就不聊的项目会被反复拉进来。
TOPIC_STACK_MAX = 3         # 最多记几个话题
TOPIC_STACK_TTL = 8         # 超过这么多轮没再命中就出栈
INHERIT_DECAY = 0.6         # 每远一格，权重乘以这个数

_TOPIC_STACK = []           # [{'src': 文件名, 'at': 轮次, 'q': 那题原文}]
_TURN = [0]


def reset_topic_stack():
    """开新的一场（或切资料包）时清空。"""
    _TOPIC_STACK[:] = []
    _TURN[0] = 0
    _LAST_MAT.update({'q': None, 'src': None, 'anchor': None})


def _stack_push(src, question):
    _TURN[0] += 1
    if not src:
        return
    st = _TOPIC_STACK
    if st and st[-1]['src'] == src:
        st[-1]['at'], st[-1]['q'] = _TURN[0], question
    else:
        st.append({'src': src, 'at': _TURN[0], 'q': question})
        del st[:-TOPIC_STACK_MAX]
    while st and _TURN[0] - st[0]['at'] > TOPIC_STACK_TTL:
        st.pop(0)


def _stack_anchors(history):
    """由近及远返回 [(文件名, 权重)]，只保留还在最近上下文里的话题。

    逐个用 history 校验：不在最近这几句里的就别继承 —— 乱序、重放、
    或者用户手动往回翻的时候，栈里可能留着不相干的旧话题。
    """
    hist = {h.strip() for h in (history or []) if h}
    out = []
    for i, e in enumerate(reversed(_TOPIC_STACK)):
        if HR_HINT.search(e['q'] or ''):
            continue                    # HR 题不向后传递，免得把后面的题带进 HR 材料
        if hist and (e['q'] or '').strip() not in hist:
            continue
        out.append((e['src'], INHERIT_DECAY ** i))
    return out


def _rrf_merge(a, b, w_b=None, k=None):
    """两路 [(score, src, text)] 按 RRF 融合，a 是主路（权重 1.0）。

    b 也可以传 [(列表, 权重), ...] 一次融合多路（话题栈要用）——
    逐个串行融合会让靠后的路被重复计入，所以必须一次算完。
    """
    if b and isinstance(b[0], tuple):
        return _rrf_merge_many(a, b, k=k)
    w_b = INHERIT_WEIGHT if w_b is None else w_b
    k = getattr(knowledge, 'RRF_K', 60) if k is None else k
    agg = {}
    for w, lst in ((1.0, a), (w_b, b)):
        for rank, item in enumerate(lst):
            e = agg.setdefault(item[2], [0.0, item[1]])
            e[0] += w / (k + rank)
    return [(v[0], v[1], key) for key, v in sorted(agg.items(), key=lambda kv: -kv[1][0])]


def _rrf_merge_many(a, others, k=None):
    """主路 a（权重 1.0）+ 若干旁路 [(列表, 权重)] 一次性 RRF 融合。"""
    k = getattr(knowledge, 'RRF_K', 60) if k is None else k
    agg = {}
    for w, lst in [(1.0, a)] + [(float(w), lst) for lst, w in others]:
        for rank, item in enumerate(lst):
            e = agg.setdefault(item[2], [0.0, item[1]])
            e[0] += w / (k + rank)
    return [(v[0], v[1], key) for key, v in sorted(agg.items(), key=lambda kv: -kv[1][0])]


def _prev_material(idx, question, history):
    """上一题命中材料的 top1 文件名——追问继承的锚点。

    上一题是 HR 题就不向后传递：否则会把后面的题也带进 HR 材料。
    """
    hist = list(history or [])
    if not hist:
        return None
    prev = hist[-1]
    if HR_HINT.search(prev or ''):
        return None
    if _LAST_MAT['q'] == prev:
        if INHERIT_NEED_PREV_ANCHOR and not _LAST_MAT['anchor']:
            return None
        return _LAST_MAT['src']
    # 兜底：没有记忆时按老办法反推（窗口少一句，只在冷启动/重放时走到）
    t = detect_topic_sticky(prev, hist[:-1])
    if not t:
        return None
    q = normalize_terms(' '.join(hist[:-1][-2:] + [prev]))
    hits = idx.search(q, topk=1, boost_src=t, boost=STICKY_BOOST)
    return hits[0][1] if hits else None


def retrieve(question, topk=DEFAULT_TOPK, history=None):
    """检索用"上文2句 + 当前句"当查询词。

    真实一面实测：面试官的追问几乎都是"那你这个…""它还有一个…"，
    单句检索 hit@1 只有 46%，加上上文 2 句提到 51%，hit@3 69%→77%
    （tools/test_retrieval_window.py）。
    """
    idx, _ = build()
    sources = [c[0] for c in idx.chunks]
    hist = list(history or [])
    q = normalize_terms(' '.join(hist[-2:] + [question]))
    topic = detect_topic_sticky(question, hist, sources=sources)
    hits = idx.search(q, topk=topk, boost_src=topic, boost=STICKY_BOOST if topic else None)

    # 指代型追问：本句没有话题词、又短、又不是 HR 题 -> 把最近聊过的材料一起拉进来
    if (INHERIT_ENABLE and detect_topic(question) is None
            and not HR_HINT.search(question or '')
            and len(question or '') <= INHERIT_GATE_CHARS and hist):
        anchors = _stack_anchors(hist)
        if not anchors:                      # 冷启动/重放：栈还空着，退回单锚点反推
            anc = _prev_material(idx, question, hist)
            anchors = [(anc, 1.0)] if anc else []
        if anchors:
            others = [(idx.search(q, topk=topk, boost_src=src, boost=INHERIT_BOOST),
                       w * INHERIT_WEIGHT) for src, w in anchors]
            hits = _rrf_merge(hits, others)[:topk]

    _LAST_MAT['q'], _LAST_MAT['anchor'] = question, topic
    _LAST_MAT['src'] = hits[0][1] if hits else None
    _stack_push(_LAST_MAT['src'], question)

    mats = []
    for sc, src, txt in hits:
        mats.append('<!-- 来源: %s -->\n%s' % (src, txt[:DEFAULT_CUT]))
    return '\n\n---\n\n'.join(mats), hits


def build_messages(question, qtype=None, company=None, topk=DEFAULT_TOPK, history=None):
    qtype = decide_qtype(question, qtype)   # 材料外名词会被翻成 gap，标签和策略同一个口径
    gap = is_unknown_gap(question) if qtype == 'gap' else []
    company = company if company is not None else load_company()
    material, hits = retrieve(question, topk, history)
    bank_mat, bank_hits = retrieve_bank(question)
    parts = []
    if company:
        parts.append('<公司背景>\n%s\n</公司背景>' % company)
    if bank_mat:
        parts.append('<题库参考>\n%s\n</题库参考>' % bank_mat)
    parts.append('<材料>\n%s\n</材料>' % material)
    prev = [h for h in (history or []) if h.strip()][-2:]
    if prev:
        parts.append('<上文>\n%s\n</上文>\n(上面是面试官前几句,他现在的提问可能是接着这些说的,' 
                     '只用来理解他在指什么,不要回答上文的问题)' % '\n'.join(prev))
    if gap:
        parts.append('⚠️ 注意：%s 这些词在我的材料里查无此词，我没有任何相关经历。'
                     '按【坦诚】开头，严禁编造"我用过/我做过"。' % '、'.join(gap))
    else:
        alias = alias_terms(question)
        if alias:
            parts.append('⚠️ 注意：%s。语音转写常把技术名词写坏，就按后面那个正确写法回答。'
                         % '、'.join('%s 应该是 %s' % (a, b) for a, b in alias))
        back = unbacked_terms(question)
        if back:
            parts.append('⚠️ 注意：%s 这些词我材料里没有细节，但这块我是会的。'
                         '可以讲我的真实理解，但不许编造参数、指标、工具名和踩过的坑。' % '、'.join(back))
    parts.append('面试官提问:%s' % question)
    if is_brief(question):
        parts.append(BRIEF_HINT)
    elif _ENUM.search(question):
        parts.append(ENUM_HINT)
    user = '\n\n'.join(parts)
    # 题库条目带**答案正文**：它和检索到的材料一样，是"数字可以来自这里"的依据
    return [{'role': 'system', 'content': STRATEGIES[qtype]},
            {'role': 'user', 'content': user}], qtype, hits + [('bank', h['src'], h['a']) for h in bank_hits]


_CLIENT = None


def _client(timeout):
    """复用一个 client：每次新建都要重做 DNS + TLS 握手，实测每题多花 0.2~0.5 秒。"""
    global _CLIENT
    if _CLIENT is None or getattr(_CLIENT, '_tp_timeout', None) != timeout:
        from openai import OpenAI
        _CLIENT = OpenAI(api_key=load_key(), base_url=BASE_URL,
                         timeout=timeout, max_retries=ANSWER_RETRIES)
        _CLIENT._tp_timeout = timeout
    return _CLIENT


def warmup():
    """把快答线的 DNS + TLS 握手挪到启动阶段。

    实测：不预热时本场**第一题**的墙钟出字时间是 4.0 秒（同一句话后面几题只要 0.9 秒），
    差的这 2~3 秒全在"第一次连 api.deepseek.com"上。第一题往往是自我介绍，最不该慢。
    只发 1 个 token，成本可忽略；顺带确认 key 是好的。
    """
    c = _client(ANSWER_TIMEOUT)
    c.chat.completions.create(model=MODEL, messages=[{'role': 'user', 'content': 'ok'}],
                              max_tokens=1, extra_body=EXTRA_BODY)
    return True


def answer_stream(question, on_delta=None, qtype=None, company=None, topk=DEFAULT_TOPK, history=None,
                  should_stop=None, timeout=ANSWER_TIMEOUT):
    """跑一次快答线。

    should_stop：面试官已经问到下一题了，这条流就没必要再收 —— 既不浪费 token，
    也不会把上一题的答案混进下一题（那是"照念错内容"级别的错）。
    """
    msgs, qtype, hits = build_messages(question, qtype, company, topk, history)
    client = _client(timeout)
    t0 = time.time(); first = None; buf = []
    stream = client.chat.completions.create(
        model=MODEL, messages=msgs, stream=True,
        temperature=0.2, max_tokens=400, extra_body=EXTRA_BODY)
    for ch in stream:
        if should_stop and should_stop():
            break
        if not ch.choices:
            continue
        piece = getattr(ch.choices[0].delta, 'content', None)
        if piece:
            if first is None:
                first = time.time() - t0
            buf.append(piece)
            if on_delta:
                on_delta(piece)
    return ''.join(buf), first, time.time() - t0, qtype, hits


# ── 答案后校验（线上拦一道，而不是只在离线评测里测）────────────────────
# 规则写在提示词里 ≠ 模型一定遵守。照念场景下，念出一个材料里没有的数字、
# 或者把"我用过"安在一个没准备过的名词上，是全场最致命的两种错。
# 这一步在流式输出结束后跑（几十微秒），只**提示**不拦截 —— 屏幕上标黄让人自己看一眼。
_NUM = re.compile(r'\d[\d,]*(?:\.\d+)?%?')
_YEAR = re.compile(r'^(?:19|20)\d\d$')
_CLAIM = re.compile(r'(我用过|我做过|我拿|我用它|我实际用过|我搞过|我实现过|我们用的就是|我写过)')


def _nums_in(text):
    """抽出"要紧的数字"：>=3 位数字或带小数/百分号。- 1~2 位的小数字不查，
    分辨率太低（"三个模块""两个服务"），误报一条警告比不报还烦。"""
    out = set()
    for m in _NUM.findall(text or ''):
        t = m.replace(',', '').replace('%', '').strip().rstrip('.')
        if not t or _YEAR.match(t):
            continue
        if len(t.replace('.', '')) >= 3:
            out.add(t)
    return out


def _num_backed(n, backed):
    """答案里的这个数字算不算"有出处"。

    要放过的几种写法（都是人在口语里会说的，不能报警）：
      90.30 == 90.3（尾零）· 90.3 ← 90.35（截断）· 90.4 ← 90.35（四舍五入）
    但要拦住：50000 说成 500 这种位数都不对的（只有长度差 ≤1 才认前缀）。
    """
    if n in backed:
        return True
    if n.rstrip('0').rstrip('.') in {b.rstrip('0').rstrip('.') for b in backed}:
        return True
    for b in backed:
        if '.' in n:
            if b.startswith(n):
                return True
            try:
                d = len(n.split('.')[1])
                if abs(round(float(b), d) - float(n)) < 1e-9:
                    return True
            except Exception:
                pass
        elif b.startswith(n) and len(b) - len(n) <= 1:
            return True
    return False


def check_answer(text, hits=(), history=(), question='', gap_terms=()):
    """答案里有没有"材料/上文/提问里都找不到"的数字，或者把经历安在 gap 词上。

    返回 [{'kind': 'number'|'claim', 'text': ...}]，空列表 = 没发现问题。
    只对着**本题检索到的材料**比，不做全文搜索：宁可标一条"没在本题材料里出现"，
    也不要放过一个凭空冒出来的指标。
    """
    warn = []
    backed = set()
    for item in hits or ():
        backed |= _nums_in(item[-1] if isinstance(item, (tuple, list)) else item)
    for h in history or ():
        backed |= _nums_in(h)
    backed |= _nums_in(question)
    for n in sorted(_nums_in(text), key=lambda s: (-len(s), s)):
        if not _num_backed(n, backed):
            warn.append({'kind': 'number', 'text': n})
    for t in gap_terms or ():
        if not t:
            continue
        for m in _CLAIM.finditer(text or ''):
            seg = (text or '')[m.start():m.end() + 14]
            if t.lower() in seg.lower():
                warn.append({'kind': 'claim', 'text': t})
                break
    return warn


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    q = sys.argv[1] if len(sys.argv) > 1 else '你在那个推荐系统里怎么做召回和粗排的？'
    qt = classify(q)
    print('面试官提问: %s' % q)
    print('问题类型: %s (%s)' % (LABEL[qt], qt))
    print('-' * 64)
    text, first, total, qt, hits = answer_stream(
        q, on_delta=lambda p: (sys.stdout.write(p), sys.stdout.flush()))
    print()
    print('-' * 64)
    print('首字 %.0f ms   总 %.2f s   字数 %d' % (first * 1000, total, len(text)))
    print('依据材料: ' + (', '.join('%s(%.1f)' % (s, sc) for sc, s, _ in hits) or '无'))
