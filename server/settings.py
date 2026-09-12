# -*- coding: utf-8 -*-
"""统一配置：环境变量 > config/settings.json > 内置默认值。

三种来源，优先级从高到低：

  1. 环境变量      TP_MODEL_DIR / TP_API_KEY / TP_KEY_FILE / TP_BASE_URL / TP_LLM_MODEL /
                   TP_CORPUS_GLOBS / TP_PORTS / TP_DSH_NODE / TP_DSH_BIN / TP_DSH_HOME
  2. 配置文件      <仓库根>/config/settings.json（可选；模板见 config/settings.example.json）
  3. 内置默认值    见下面 _DEFAULTS

键的含义（与 config/settings.example.json 一一对应）：

  model_dir      ASR 模型目录，默认 <仓库根>/models/SenseVoiceSmall-onnx（**必须纯 ASCII**，
                 见 ensure_ascii() 里的解释）
  api_key_file   DeepSeek 密钥文件，默认 <仓库根>/config/api_key.txt
  base_url       https://api.deepseek.com
  llm_model      deepseek-flash
  corpus_globs   知识库语料 glob 数组，默认 <仓库根>/knowledge 下的 *.md / *.txt
  corpus_profile 资料包名（可选）。留空 = 老行为（扫整个 knowledge/）；
                 填了名字 P = 只扫 knowledge/_base/ 和 knowledge/P/。
                 用途：个人材料放 _base，JD/公司介绍按公司各放一个包，面试哪家切哪家。
                 可用包名工具看：python tools/profiles.py --list
  ports          [8765, 8766, 8767, 8768, 8769]，被占自动顺延，实际端口写 .runtime_port
  dsh_node / dsh_bin / dsh_home
                 深答线（可选，三项都填才生效；留空＝关闭深答线）

约定：配置里的相对路径按**仓库根**解析，不按当前工作目录 —— 从哪个目录启动结果都一样。
"""
import json
import os

# 仓库根 = 本文件所在目录（server/）的上一级
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(REPO_ROOT, 'config', 'settings.json')
SETTINGS_EXAMPLE_PATH = os.path.join(REPO_ROOT, 'config', 'settings.example.json')

_DEFAULTS = {
    'model_dir': os.path.join(REPO_ROOT, 'models', 'SenseVoiceSmall-onnx'),
    'api_key_file': os.path.join(REPO_ROOT, 'config', 'api_key.txt'),
    'base_url': 'https://api.deepseek.com',
    'llm_model': 'deepseek-flash',
    'corpus_globs': [os.path.join(REPO_ROOT, 'knowledge', '**', '*.md'),
                     os.path.join(REPO_ROOT, 'knowledge', '**', '*.txt')],
    # 资料包（空 = 老行为）。见文件头「corpus_profile」。
    'corpus_profile': '',
    'ports': [8765, 8766, 8767, 8768, 8769],
    'dsh_node': '',
    'dsh_bin': '',
    'dsh_home': '',
    # ── 手感参数（都有安全默认值；不写就是当前手感，改小改大按自己的语速来）──────
    # 判停：静音超过这么久就认为「这句话说完了」。600 稳；调到 350~400 能早 200ms 出答案，
    # 但语速慢、爱停顿的人会被切句（一句变两句，检索质量下降）。
    'vad_end_ms': 600,
    # 核心句超过这个字数，一秒内念不完 → 标黄 + 缩字号（实测这类合规率只有 79%）
    'core_max_chars': 40,
    # 核心大字基准字号
    'core_font_px': 20,
}

# 配置键 -> 环境变量名
_ENV_KEYS = {
    'model_dir': 'TP_MODEL_DIR',
    'api_key_file': 'TP_KEY_FILE',
    'base_url': 'TP_BASE_URL',
    'llm_model': 'TP_LLM_MODEL',
    'corpus_globs': 'TP_CORPUS_GLOBS',
    'corpus_profile': 'TP_CORPUS_PROFILE',
    'ports': 'TP_PORTS',
    'dsh_node': 'TP_DSH_NODE',
    'dsh_bin': 'TP_DSH_BIN',
    'dsh_home': 'TP_DSH_HOME',
}
# TP_API_KEY 不是配置键（密钥不该写进 settings.json），但 load_key() 会读它
API_KEY_ENV = 'TP_API_KEY'

# 这些键是路径：相对路径按仓库根解析
_PATH_KEYS = ('model_dir', 'api_key_file', 'dsh_node', 'dsh_bin', 'dsh_home')

_CACHE = None


def _as_list(v):
    """逗号/分号分隔的字符串或 JSON 数组 -> 去空白的字符串列表。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        items = [str(x).strip() for x in v]
    else:
        items = [x.strip() for x in str(v).replace(';', ',').split(',')]
    return [x for x in items if x]


def _as_ports(v, fallback):
    out = []
    for x in _as_list(v):
        try:
            p = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= p <= 65535:
            out.append(p)
    return out or list(fallback)


def _paths(v):
    """单个路径：展开 ~、相对路径按仓库根解析、去空白。"""
    s = os.path.expanduser(str(v or '').strip())
    if not s:
        return ''
    return s if os.path.isabs(s) else os.path.abspath(os.path.join(REPO_ROOT, s))


def _globs(v, fallback):
    """glob 模式列表：同样按仓库根解析（不能直接把整串 abspath，通配符要留着）。"""
    out = [_paths(p) for p in _as_list(v)]
    return [p for p in out if p] or list(fallback)


def load(reload=False):
    """读出生效的配置（dict）。结果缓存，改了就 reload=True。"""
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE

    cfg = dict(_DEFAULTS)
    cfg['corpus_globs'] = list(_DEFAULTS['corpus_globs'])
    cfg['ports'] = list(_DEFAULTS['ports'])

    raw = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding='utf-8-sig') as f:
                raw = json.load(f)
        except Exception as e:
            raise RuntimeError(
                '配置文件解析失败：%s\n  %s: %s\n'
                '改成一个合法的 JSON 对象，或直接删掉这个文件（默认值也能跑）。\n'
                '模板见 config/settings.example.json。' % (SETTINGS_PATH, type(e).__name__, e))
        if not isinstance(raw, dict):
            raise RuntimeError('配置文件最外层必须是一个 JSON 对象：%s' % SETTINGS_PATH)
        for k, v in raw.items():
            if k.startswith('_'):
                continue
            if v is not None and v != '':
                cfg[k] = v

    for key, env in _ENV_KEYS.items():
        v = os.environ.get(env)
        if v is not None and v.strip() != '':
            cfg[key] = v

    for k in _PATH_KEYS:
        cfg[k] = _paths(cfg.get(k))
    cfg['corpus_globs'] = _globs(cfg.get('corpus_globs'), _DEFAULTS['corpus_globs'])
    cfg['ports'] = _as_ports(cfg.get('ports'), _DEFAULTS['ports'])
    cfg['base_url'] = str(cfg.get('base_url') or '').strip().rstrip('/') or _DEFAULTS['base_url']
    cfg['llm_model'] = str(cfg.get('llm_model') or '').strip() or _DEFAULTS['llm_model']

    _CACHE = cfg
    return cfg


def get(key, default=None):
    """取一个配置项；没这个键就返回 default。"""
    return load().get(key, default)


def model_dir():
    """ASR 模型目录（SenseVoiceSmall ONNX）。必须纯 ASCII，见 ensure_ascii()。"""
    return load()['model_dir']


def api_key_path():
    """DeepSeek 密钥文件路径（默认 <仓库根>/config/api_key.txt）。"""
    return load()['api_key_file']


def corpus_globs():
    """知识库语料 glob 列表（未启用资料包时的"全量"模式）。"""
    return list(load()['corpus_globs'])


def corpus_profile():
    """当前资料包名；空字符串 = 未启用（扫整个 knowledge/）。"""
    return str(load().get('corpus_profile') or '').strip()


def knowledge_root():
    """知识库根目录（<仓库根>/knowledge）。"""
    return os.path.join(REPO_ROOT, 'knowledge')


def port_list():
    """候选端口列表，按顺序取第一个空闲的。"""
    return list(load()['ports'])


def dsh_paths():
    """深答线三件套 (node, dsh_bin, dsh_home)；没配全就返回 None（＝关闭深答线）。"""
    cfg = load()
    node, bin_, home = cfg.get('dsh_node', ''), cfg.get('dsh_bin', ''), cfg.get('dsh_home', '')
    if not (node and bin_ and home):
        return None
    return node, bin_, home


_KEY_HINT = (
    '没找到 DeepSeek 密钥。任选一条配置好再用：\n'
    '  1. 把密钥写进文件 %s（一行，只放密钥本身，不要引号和分号）\n'
    '  2. 设环境变量 %s=sk-xxxx（临时用）\n'
    '  3. 在 config/settings.json 里把 api_key_file 指到别处的密钥文件\n'
    '密钥申请：https://platform.deepseek.com  →  API keys\n'
    '（没有密钥也能启动：快答线会退回本地题库，只是不再现生成答案。）'
)


def api_key():
    """DeepSeek 密钥：环境变量 TP_API_KEY 优先，其次 api_key_file 指向的文件。

    两处都没有就抛 RuntimeError，文案里带修复步骤（启动阶段直接告诉用户怎么弄）。
    """
    env = (os.environ.get(API_KEY_ENV) or '').strip()
    if env:
        return env
    path = api_key_path()
    if not os.path.exists(path):
        raise RuntimeError(_KEY_HINT % (path, API_KEY_ENV))
    try:
        # utf-8-sig：Windows 上用记事本存出来的 txt 常带 BOM，不去掉会拼进 Authorization 头
        text = open(path, encoding='utf-8-sig').read()
    except Exception as e:
        raise RuntimeError('密钥文件读不出来：%s\n  %s: %s' % (path, type(e).__name__, e))
    for line in text.splitlines():
        s = line.strip().strip('"').strip("'")
        if s and not s.startswith('#'):
            return s
    raise RuntimeError('密钥文件是空的：%s\n%s' % (path, _KEY_HINT % (path, API_KEY_ENV)))


def ensure_ascii(path, what='路径'):
    """确认路径是纯 ASCII；不是就抛 RuntimeError，并把修法写清楚。

    为什么要卡这一条：SenseVoice 模型里的 sentencepiece 是 C++ 实现，路径含中文时
    它在 C++ 层直接报 NOT_FOUND，而 Python 这一侧 os.path.exists() 认为文件在 ——
    报错完全指不到真正的原因（实测踩过）。所以宁可在启动时就明确拒绝。
    """
    s = str(path)
    try:
        s.encode('ascii')
        return s
    except UnicodeEncodeError:
        bad = ''.join(dict.fromkeys(c for c in s if ord(c) > 127))[:12]   # 按出现顺序去重
        raise RuntimeError(
            '%s里含非 ASCII 字符（%s），ASR 模型加载会失败。\n'
            '  当前值：%s\n'
            '原因：模型自带的 sentencepiece 是 C++ 实现，遇中文路径报 NOT_FOUND，\n'
            '      而 Python 侧 os.path.exists() 却认为文件存在 —— 报错信息指不到真正的原因。\n'
            '怎么改（任选一条）：\n'
            '  1. 把整个仓库放到纯英文路径下，例如 D:\\EarShot\\（最省事）\n'
            '  2. 设环境变量指向纯英文目录：TP_MODEL_DIR=D:\\models\\SenseVoiceSmall-onnx\n'
            '  3. 在 config/settings.json 里把 model_dir 改成纯英文路径'
            % (what, ''.join(bad), s))


def describe():
    """生效配置 + 每项来源，启动或排障时打印用。"""
    env_hit = [k for k, e in _ENV_KEYS.items() if (os.environ.get(e) or '').strip()]
    from_file = []
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding='utf-8-sig') as f:
                from_file = [k for k in json.load(f) if not str(k).startswith('_')]
        except Exception:
            from_file = ['<配置文件解析失败>']
    cfg = load()
    lines = ['配置文件: %s%s' % (SETTINGS_PATH, '' if os.path.exists(SETTINGS_PATH) else '（不存在，用默认值）'),
             '优先级  : 环境变量(%s) > config/settings.json(%s) > 默认值' % (
                 ','.join(env_hit) or '无', ','.join(from_file) or '无')]
    for k in ('model_dir', 'api_key_file', 'base_url', 'llm_model', 'ports'):
        lines.append('/%s = %s' % (k, cfg[k]))
    lines.append('/corpus_globs = %s' % cfg['corpus_globs'])
    lines.append('/corpus_profile = %s' % (cfg.get('corpus_profile') or '（未启用）'))
    lines.append('/dsh_paths = %s' % (dsh_paths() or '未配置（深答线关闭）',))
    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    print(describe())
