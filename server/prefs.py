# -*- coding: utf-8 -*-
"""用户配置层：把「用户能改的东西」收成一个口子。

为什么要有这一层 —— 原来要改配置得手编 config/settings.json（里面还混着一堆
_comment_* 说明键）、密钥要自己拿记事本建 config/api_key.txt、跨设备同步更没有。
这个模块把三件事收在一起：

  1. 读写 settings.json —— **保留 _comment_* 注释键**。用户手编这个文件时靠注释认字段，
     程序一保存就把注释全删掉的话，下次他自己都看不懂了。
  2. 读写 API 密钥 —— 统一走 api_key.txt，并支持打码显示（截图/演示时不至于泄露）。
  3. 设备身份 + 配置中心 —— config/device.json，给「另一台设备填了密钥，这台也能拿到」用。

设计取舍：不引入新依赖、不建数据库。全是 JSON / 纯文本，用户随时能拿记事本打开看。
跨设备同步走 HTTP，由后端提供 /cfg/* 端点（见 main.py），任何一台跑着 EarShot 的机器
都能当配置中心。
"""
import io
import json
import os
import socket
import threading
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

SETTINGS_PATH = os.path.join(ROOT, 'config', 'settings.json')
KEY_PATH = os.path.join(ROOT, 'config', 'api_key.txt')
DEVICE_PATH = os.path.join(ROOT, 'config', 'device.json')

_LOCK = threading.Lock()

# 允许用户在界面上改的键。不在这个白名单里的（model_dir / corpus_globs / dsh_* 等）
# 属于高级项，界面不碰 —— 乱改这些会把后端弄得起不来，用户又不知道哪儿错了。
EDITABLE = (
    'lan_access',
    'base_url',
    'llm_model',
    'corpus_profile',
    'audio_mode',
    'mic_device',
    'vad_thresh',
    'vad_end_ms',
    'vad_merge_ms',
    'answer_holdoff_s',
    'core_max_chars',
    'core_font_px',
    'ports',
)

# 各项的中文名 + 校验范围，界面直接拿来用（免得界面和后端各写一份、对不上）
FIELDS = {
    'api_key':       {'label': 'API 密钥',        'type': 'secret', 'hint': 'sk- 开头，存在 config/api_key.txt'},
    'lan_access':    {'label': '允许局域网访问',    'type': 'bool',
                      'hint': '打开后别的设备才连得上这台（用于同步密钥）。有口令保护。'},
    'base_url':      {'label': '接口地址',         'type': 'text',   'hint': 'OpenAI 兼容，例如 https://api.deepseek.com'},
    'llm_model':     {'label': '模型名',           'type': 'text',   'hint': '别用思考模型，首字会慢到 6 秒以上'},
    'corpus_profile': {'label': '资料包（公司/岗位）', 'type': 'text', 'hint': '例如「汉得」；留空=检索整个 knowledge/'},
    'audio_mode':    {'label': '采集方式',         'type': 'choice', 'hint': '电脑面试选 dual 最稳；macOS 上只能选 mic',
                      'choices': [('dual', '回环+麦克风自动切换（推荐）'),
                                  ('loopback', '只听电脑播放的声音（Windows）'),
                                  ('mic', '只听麦克风（手机面试／macOS）')]},
    # 加进界面是因为：它原本只能改 config/settings.json，而**macOS 上它是抓系统声音的唯一正路**
    # （soundcard 在 CoreAudio 后端没有回环 API），让用户去手改 JSON 不现实。
    'mic_device':    {'label': '指定麦克风',       'type': 'text',
                      'hint': '留空=系统默认麦克风。macOS 上填 BlackHole 2ch 就能抓系统播放的声音；'
                              'Windows 上可用来锁定某个麦克风。填名字、名字的一部分、或完整设备 id。'},
    'vad_thresh':    {'label': '拾音灵敏度',       'type': 'float',  'hint': '越小越灵敏；0.002~0.01，噪音大就调大'},
    'vad_end_ms':    {'label': '判停（毫秒）',     'type': 'int',    'hint': '静音超过这么久就算一句话说完；320 快 / 600 稳'},
    'vad_merge_ms':  {'label': '合并窗口（毫秒）',  'type': 'int',    'hint': '判停后再等这么久，接上下半句；220 比较跟手'},
    'answer_holdoff_s': {'label': '答案保护（秒）', 'type': 'int',    'hint': '刚出答案的这几秒内，麦克风听到的话不当新题'},
    'core_max_chars': {'label': '大字上限（字）',  'type': 'int',    'hint': '核心句超过就缩字号标黄'},
    'core_font_px':  {'label': '基准字号（像素）',  'type': 'int',    'hint': '16~28'},
    'ports':         {'label': '后端端口',         'type': 'text',   'hint': '被占自动顺延；多端口用逗号分隔'},
}


# ── 基础读写 ──────────────────────────────────────────────────────────
def _read_json(path, default):
    try:
        with io.open(path, encoding='utf-8-sig') as f:
            v = json.load(f)
        return v if isinstance(v, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)     # 原子替换：写到一半断电也不会把原文件毁掉


def read_settings():
    """原始 settings.json（含 _comment_* 注释键）。"""
    return _read_json(SETTINGS_PATH, {})


def write_settings(patch):
    """把 patch 合并进 settings.json，**保留原有注释键和未知键**。

    先读后合并是有意的：这个文件用户会手编，直接覆盖等于把他自己加的字段删了。
    """
    with _LOCK:
        cur = read_settings()
        for k, v in (patch or {}).items():
            if str(k).startswith('_'):
                continue
            if v is None or v == '':
                cur.pop(k, None)      # 空值 = 恢复默认（而不是写个空字符串进去）
            else:
                cur[k] = v
        _write_json(SETTINGS_PATH, cur)
        return cur


# ── API 密钥 ──────────────────────────────────────────────────────────
def read_key():
    """读密钥；没有就返回空字符串（不抛异常 —— 界面要靠它判断「填没填」）。"""
    env = (os.environ.get('TP_API_KEY') or '').strip()
    if env:
        return env
    if not os.path.exists(KEY_PATH):
        return ''
    try:
        with io.open(KEY_PATH, encoding='utf-8-sig') as f:
            for line in f:
                s = line.strip().strip('"').strip("'")
                if s and not s.startswith('#'):
                    return s
    except Exception:
        return ''
    return ''


def write_key(key):
    """写密钥。空字符串 = 删掉密钥文件。"""
    with _LOCK:
        k = (key or '').strip().strip('"').strip("'")
        if not k:
            try:
                os.remove(KEY_PATH)
            except FileNotFoundError:
                pass
            return ''
        os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
        with io.open(KEY_PATH, 'w', encoding='utf-8') as f:
            f.write(k + '\n')
        return k


def mask_key(k=None):
    """打码：sk-mFIsIabcd…wxyz → sk-mFI…wxyz。太短就整体打掉。"""
    k = read_key() if k is None else k
    if not k:
        return ''
    if len(k) <= 12:
        return k[:3] + '*' * max(0, len(k) - 3)
    return '%s…%s' % (k[:6], k[-4:])


def key_source():
    """密钥是从哪儿来的 —— 界面要显示，免得用户改了文件却不生效还以为没生效。"""
    if (os.environ.get('TP_API_KEY') or '').strip():
        return '环境变量 TP_API_KEY'
    if os.path.exists(KEY_PATH):
        return KEY_PATH
    return '未配置'


# ── 设备身份 + 配置中心 ────────────────────────────────────────────────
def _default_device():
    host = ''
    try:
        host = socket.gethostname()
    except Exception:
        pass
    return {
        'device_id': uuid.uuid4().hex[:12],
        'device_name': host or '这台设备',
        'hub_url': '',        # 配置中心地址，例如 http://192.168.1.5:8765
        'hub_token': '',      # 共享口令，防止同网段别人乱拉你的密钥
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


def device_cfg():
    """设备身份 + 配置中心设置；文件不存在就生成一份稳定的（device_id 只生成一次）。"""
    d = _read_json(DEVICE_PATH, {})
    base = _default_device()
    changed = False
    for k, v in base.items():
        if not d.get(k):
            d[k] = v
            changed = True
    if changed:
        try:
            _write_json(DEVICE_PATH, d)
        except Exception:
            pass
    return d


def save_device_cfg(patch):
    with _LOCK:
        d = device_cfg()
        for k, v in (patch or {}).items():
            if k in d:
                d[k] = v
        _write_json(DEVICE_PATH, d)
        return d


# ── 快照：导出 / 导入 / 跨设备同步的载体 ────────────────────────────────
def snapshot(with_key=False):
    """当前配置快照。with_key=True 时才带明文密钥（局域网同步用）。"""
    s = read_settings()
    out = {}
    for k in EDITABLE:
        if k in s:
            out[k] = s[k]
    # 缺失的可编辑键补成空串，别让界面拿到 None 去渲染成字面量 "None"。
    # 什么时候会缺：`write_settings()` 的语义是"空值 = 恢复默认"，所以用户一旦把
    # 某个键清空（例如 mic_device 从「BlackHole 2ch」清回默认麦克风），
    # 那个键会被**整个从 settings.json 里删掉** —— 之后快照里它就是 None。
    # 对界面来说"没这个键"和"这个键是空的"是同一件事，这里统一成空串。
    for k in EDITABLE:
        out.setdefault(k, '')
    out['corpus_profile'] = s.get('corpus_profile', '')
    out['audio_mode'] = s.get('audio_mode', 'loopback')
    if with_key:
        out['api_key'] = read_key()
    else:
        out['api_key_masked'] = mask_key()
        out['api_key_set'] = bool(read_key())
    d = device_cfg()
    out['_from'] = {'device_id': d['device_id'], 'device_name': d['device_name']}
    out['_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return out


def apply_snapshot(data, take_key=True):
    """把一份快照落到本机。返回 (改了哪些键, 密钥是否更新)。"""
    if not isinstance(data, dict):
        raise ValueError('快照必须是一个 JSON 对象')
    patch = {}
    for k in EDITABLE:
        if k not in data:
            continue
        # 空值一律跳过（"没填"不等于"要改成空"），但 mic_device 例外：
        # 它是一个**能被有意义地清空**的键 —— 用户先填了「BlackHole 2ch」，
        # 后来想回到"系统默认麦克风"，界面上必须能清。
        # settings.load() 会忽略空字符串（看作"没配"），所以写 '' 的语义正是"恢复默认"。
        if data[k] in (None, '') and k != 'mic_device':
            continue
        v = data[k]
        if k == 'ports':
            # 端口可能是 "8765,8766" 字符串或数组，统一成 int 数组。
            # ⚠ 这里必须把转换结果写回 patch —— 早期版本写的是原始的 data[k]，
            # 结果 settings.json 里 ports 变成字符串 "9001,9002"，后端
            # settings._as_ports 虽然也能容错解析，但文件本身已经不符合它的 schema 了。
            if isinstance(v, str):
                v = [x.strip() for x in v.replace(';', ',').split(',') if x.strip()]
            try:
                v = [int(x) for x in v]
            except Exception:
                continue
            if not v:
                continue
        patch[k] = v
    changed = write_settings(patch)
    key_updated = False
    if take_key and (data.get('api_key') or '').strip():
        write_key(data['api_key'])
        key_updated = True
    return sorted(patch.keys()), key_updated


def effective():
    """生效值（把 settings.json + 默认值合起来），给界面显示用。"""
    try:
        import sys
        if _HERE not in sys.path:
            sys.path.insert(0, _HERE)
        import settings as S
        cfg = S.load(reload=True)
    except Exception:
        cfg = dict(read_settings())
    out = {k: cfg.get(k) for k in EDITABLE if k in cfg}
    out['api_key_masked'] = mask_key()
    out['api_key_set'] = bool(read_key())
    out['api_key_source'] = key_source()
    return out


if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    print('配置文件 :', SETTINGS_PATH)
    print('密钥文件 :', KEY_PATH)
    print('设备文件 :', DEVICE_PATH)
    print('密钥      :', mask_key() or '（未配置）', ' 来源:', key_source())
    print('设备      :', json.dumps(device_cfg(), ensure_ascii=False))
    print('生效值    :', json.dumps(effective(), ensure_ascii=False, indent=2))
