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
import sys


def _find_data_root():
    """从 exe 所在目录往上找 config/settings.json —— 打包后的固定套路。

    打包布局是 dist/EarShot/EarShot.exe，而 config / knowledge / logs / 知识库 都在仓库根，
    往上走两层才是。**必须和 tools/launch.py 的推断完全一致** —— 不一致的后果实测过：
    启动器读到了真配置，后端却退回默认值，模型路径变成 dist/EarShot/models/… ，
    而且因为仓库路径带中文，ASR 直接加载失败（报的却是「模型目录含非 ASCII 字符」，
    看起来像路径配置问题，其实是数据根推错了）。
    找不到就退回 exe 目录（那时数据得自己放旁边）。
    """
    d = os.path.dirname(sys.executable)
    for _ in range(4):
        if os.path.exists(os.path.join(d, 'config', 'settings.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.dirname(sys.executable)


def _split_roots():
    """把「代码在哪」和「数据在哪」分开 —— 打包成 exe 之后这两件事不再是一回事。

    普通运行：两者都是仓库根。
    打包运行：代码被解到 sys._MEIPASS（一个临时解压目录），而 config / knowledge /
              logs 必须在 **exe 旁边** —— 用户要能改配置、要能往里加材料、日志要留得住。
              写进 _MEIPASS 的话下次启动就没了，而且用户根本找不到那个目录。
    返回 (代码根, 数据根)。
    """
    if getattr(sys, 'frozen', False):
        # 数据目录由启动器算好并通过环境变量传下来（它从 exe 往上找 config/settings.json），
        # 这里只是兜底 —— 单独跑 exe 时也能起来。
        data = os.environ.get('TP_DATA_ROOT') or _find_data_root()
        return getattr(sys, '_MEIPASS', data), data
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    return root, root


BUNDLE_ROOT, REPO_ROOT = _split_roots()
SETTINGS_PATH = os.path.join(REPO_ROOT, 'config', 'settings.json')
SETTINGS_EXAMPLE_PATH = os.path.join(REPO_ROOT, 'config', 'settings.example.json')

_DEFAULTS = {
    'model_dir': os.path.join(REPO_ROOT, 'models', 'SenseVoiceSmall-onnx'),
    'api_key_file': os.path.join(REPO_ROOT, 'config', 'api_key.txt'),
    'base_url': 'https://api.deepseek.com',
    'llm_model': 'deepseek-flash',
    'relay_enabled': True,
    'relay_base_url': 'http://127.0.0.1:3000/v1',
    'relay_api_key_ref': 'TP_RELAY_API_KEY',
    'relay_model_fast': 'earshot-fast',
    'relay_model_smart': 'earshot-smart',
    'relay_tier': 'fast',
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
    # 合并窗口（2026-09-13 新增）。面试官句内换气、想一下再说下半句，常常超过
    # vad_end_ms —— 旧版一判停就发答案，第二段来了又把答案顶掉，用户看到的就是
    # "答案乱变"。这里让判停后再等这么久，期间只要又有语音就把两段拼成一句。
    # 0 = 关掉（回到旧行为）。代价：出答案晚这么多毫秒。
    'vad_merge_ms': 600,
    # 半句判定：抽出来的问题若明显没说完（以连接词/助词收尾，或既无疑问标志又很短），
    # 不发答案，先存着，等下一段拼起来再发。超过 max_hold_s 秒强制发，避免卡住。
    'hold_incomplete': True,
    'max_hold_s': 4.0,
    # ASR 文本归一化：woitn=不归一化（旧行为，实测真实录音产出 0 个标点，
    # 导致 extract_q 完全失效）；withitn=带标点+数字归一化。回退就改回 woitn。
    'asr_textnorm': 'withitn',
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
    'relay_enabled': 'TP_RELAY_ENABLED',
    'relay_base_url': 'TP_RELAY_BASE_URL',
    'relay_api_key_ref': 'TP_RELAY_API_KEY_REF',
    'relay_model_fast': 'TP_RELAY_MODEL_FAST',
    'relay_model_smart': 'TP_RELAY_MODEL_SMART',
    'relay_tier': 'TP_RELAY_TIER',
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


_ENV_SEEN = None


def _env_snapshot():
    """`_ENV_KEYS` 里所有环境变量当前值的快照 —— 用来发现"环境变量被改了"。"""
    return tuple((os.environ.get(e) or '').strip() for e in _ENV_KEYS.values())


def load(reload=False):
    """读出生效的配置（dict）。结果缓存，改了就 reload=True。

    ⚠ **缓存会把"之后才设置的环境变量"吃掉 —— 0.9.23 修。**

    原实现是 `if _CACHE is not None and not reload: return _CACHE`，
    于是环境变量只在**第一次 load() 之前**设置才有效。这直接违背了本文件
    顶部声明的优先级「环境变量 > config/settings.json > 默认值」。

    本机踩到的实例：`tests/test_knowledge_search.py` 在模块顶层设
    `TP_CORPUS_GLOBS=examples/knowledge/*.md`，但 pytest **收集阶段**先导入了
    别的测试模块、那里已经触发过 load()，缓存里存的是 config/settings.json
    的值。结果那个环境变量完全没生效，测试扫的还是本机私人资料包，
    报出「检索命中的内容与问题无关」——**单跑那个文件是绿的**。

    修法：把 `_ENV_KEYS` 里那些环境变量做一次快照，`load()` 每轮比对，
    变了就自动重读。代价是每次多 11 次 `os.environ.get`（纳秒级），
    换来的是"环境变量优先级最高"这句话真的成立，而不是要求每个调用方
    都记得在改完环境后补一句 `load(reload=True)` —— 那种要求一定会有人忘。
    """
    global _CACHE, _ENV_SEEN
    snap = _env_snapshot()
    if _CACHE is not None and not reload and snap == _ENV_SEEN:
        return _CACHE
    _ENV_SEEN = snap

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
    cfg['relay_base_url'] = str(cfg.get('relay_base_url') or '').strip().rstrip('/') or _DEFAULTS['relay_base_url']
    cfg['relay_api_key_ref'] = str(cfg.get('relay_api_key_ref') or '').strip() or _DEFAULTS['relay_api_key_ref']
    cfg['relay_model_fast'] = str(cfg.get('relay_model_fast') or '').strip() or _DEFAULTS['relay_model_fast']
    cfg['relay_model_smart'] = str(cfg.get('relay_model_smart') or '').strip() or _DEFAULTS['relay_model_smart']
    raw_enabled = cfg.get('relay_enabled', _DEFAULTS['relay_enabled'])
    cfg['relay_enabled'] = str(raw_enabled).strip().lower() not in ('0', 'false', 'no', 'off')
    if raw and 'relay_enabled' not in raw and not os.environ.get('TP_RELAY_ENABLED'):
        cfg['relay_enabled'] = False
    cfg['relay_tier'] = 'smart' if cfg.get('relay_tier') == 'smart' else 'fast'
    # Existing TP_BASE_URL users explicitly selected direct mode. Remove that
    # legacy variable before selecting a relay in the settings window.
    if (os.environ.get('TP_BASE_URL') or '').strip():
        cfg['relay_enabled'] = False

    _CACHE = cfg
    return cfg


def get(key, default=None):
    """取一个配置项；没这个键就返回 default。"""
    return load().get(key, default)


def _relay_secret_path():
    import hashlib
    ref = get('relay_api_key_ref', 'TP_RELAY_API_KEY')
    name = hashlib.sha256(ref.encode('utf-8')).hexdigest() + '.bin'
    return os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                        'EarShot', 'credentials', name)


def _dpapi(data, decrypt=False):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

    if sys.platform != 'win32':
        raise OSError('Relay credential storage requires Windows DPAPI')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), 'EarShot relay', None, None, None, 1,
            ctypes.byref(result))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def relay_key():
    value = (os.environ.get(get('relay_api_key_ref')) or '').strip()
    if value:
        return value
    path = _relay_secret_path()
    if not os.path.exists(path):
        return ''
    with open(path, 'rb') as f:
        return _dpapi(f.read(), decrypt=True).decode('utf-8')


def save_relay_key(value):
    path = _relay_secret_path()
    if not value:
        if os.path.exists(path):
            os.remove(path)
        return
    encrypted = _dpapi(value.strip().encode('utf-8'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'wb') as f:
        f.write(encrypted)
    os.replace(path + '.tmp', path)


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
    """当前资料包名；空字符串 = 未启用（扫整个 knowledge/）。

    ⚠ **`TP_CORPUS_GLOBS` 一旦显式设置，资料包就让位**（0.9.23 修）。

    为什么：这两个是同一件事的两种说法 —— "扫这些文件"和"只扫这个包"。
    原来的写法是只要 `corpus_profile` 非空就走分包逻辑、**完全忽略** `corpus_globs`，
    于是 `TP_CORPUS_GLOBS=... python -m pytest tests/test_knowledge_search.py`
    里那个环境变量**一点作用都没有**：测试想指向 `examples/knowledge/`，
    实际扫的还是配置里那个私人资料包 —— 本机实测就是这条把测试跑红的。

    按本项目写在 settings.json 顶部、也是所有人默认预期的优先级
    「环境变量 > config/settings.json > 默认值」，同属一个语义的环境变量
    没理由被配置文件里的另一个键压住。而且从意图上看：临时在命令行导出
    `TP_CORPUS_GLOBS` 的人，要的就是"就扫这些文件"，不是"顺便还套个包"。
    """
    if (os.environ.get('TP_CORPUS_GLOBS') or '').strip():
        return ''
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
