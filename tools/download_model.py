# -*- coding: utf-8 -*-
"""从 ModelScope 下载 SenseVoiceSmall ONNX(int8) 到 models/ 目录，纯标准库实现。"""
import hashlib
import os
import sys
import json
import urllib.request
import time

REPO = 'iic/SenseVoiceSmall-onnx'


def _data_root():
    r"""模型下到哪 —— 必须和 server/settings.py 找模型的目录是同一个。

    源码运行时它就是仓库根（__file__ 往上两级），没问题。
    打包成 exe 之后两者**不是一回事**：__file__ 在 _internal 里（onedir 的 _MEIPASS，
    那是**代码**目录），而 server/settings.py 的 _split_roots() 找模型看的是**数据**目录
    （exe 所在那一层）。照 __file__ 推就会把 230MB 下进 _internal\models\，
    后端永远不会去看那儿 —— 第一次装的人看到的是「模型目录不存在」，而他明明刚下完。

    启动器用 TP_DATA_ROOT 把数据根传给所有子进程（后端 / 浮窗 / 选库窗口用的是同一个值），
    这里跟着用同一个，就不会再漂。
    """
    env = os.environ.get('TP_DATA_ROOT')
    if env:
        return env
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _data_root()
DEST = os.path.join(ROOT, 'models', 'SenseVoiceSmall-onnx')
FILES = ['model_quant.onnx', 'tokens.json', 'am.mvn', 'config.yaml', 'configuration.json']
# 每个文件的最小合理字节数 —— 收尾核对用。
# ⚠️ 这里**绝不能**用一个统一阈值：早先用的是"小于 1000 字节就算没下全"，
# 而 configuration.json 本身就只有 56 字节（{"framework":"Pytorch",...}），
# 于是**每个下载成功的用户都会看到「还缺 1 个文件: configuration.json」并拿到退出码 1**
# —— 文件明明是好的。（实测：model_quant.onnx 230MB / tokens.json 352KB / am.mvn 11KB /
# config.yaml 1855B / configuration.json 56B / bpe.model 377KB。）
# 阈值按各文件实测值打对折，既能挡住"下到一半断了"，又不会误报。
MIN_BYTES = {
    'model_quant.onnx': 100 << 20,
    'tokens.json': 100 << 10,
    'am.mvn': 4 << 10,
    'config.yaml': 500,
    'configuration.json': 10,
}
# 每个文件的 sha256 —— 2026-09-18 从本机已经跑通的模型目录实测得到。
# 有了它：上游仓库被换包、镜像被投毒、中间人替换，都会在下载完的那一刻被拦下并删掉半个文件，
# 而不是把"能加载但内容不对"的权重喂给 ASR。换模型版本时这几个值必须一起更新。
SHA256 = {
    'model_quant.onnx': '21dc965f689a78d1604717bf561e40d5a236087c85a95584567835750549e822',
    'tokens.json': 'a2594fc1474e78973149cba8cd1f603ebed8c39c7decb470631f66e70ce58e97',
    'am.mvn': '29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5',
    'config.yaml': 'f71e239ba36705564b5bf2d2ffd07eece07b8e3f2bbf6d2c99d8df856339ac19',
    'configuration.json': 'c57f6a580d63f7465c6a22ba95847aee05a1ae1181f5abddffb943d9febda061',
    # 这个从另一个仓库（iic/SenseVoiceSmall）取，同样是可被换包的入口，一起锁
    'chn_jpn_yue_eng_ko_spectok.bpe.model':
        'aa87f86064c3730d799ddf7af3c04659151102cba548bce325cf06ba4da4e6a8',
}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fp:
        while True:
            block = fp.read(1 << 20)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify(path, name):
    """核对 sha256；对不上就删掉文件并抛错（宁可装不上，也别用被换过的权重）。"""
    want = SHA256.get(name)
    if not want:
        return
    got = sha256_of(path)
    if got != want:
        try:
            os.remove(path)
        except OSError:
            pass
        raise RuntimeError(
            '%s 的 sha256 对不上，文件已删除。\n  期望 %s\n  实际 %s\n'
            '上游仓库可能被替换过 —— 先别用，去 ModelScope 官方页面核对。'
            % (name, want, got))


# 第 6 个文件是分词模型，它不在 onnx 仓库里，少了这个文件 funasr_onnx 建模型时会直接失败，
# 而 Python 侧 os.path.exists 看上去一切正常 —— 所以单独从一个仓库补。
BPE_REPO = 'iic/SenseVoiceSmall'
BPE_FILE = 'chn_jpn_yue_eng_ko_spectok.bpe.model'
BASE = 'https://www.modelscope.cn/api/v1/models/' + REPO + '/repo?Revision=master&FilePath='

def download(name, repo=None):
    base = ('https://www.modelscope.cn/api/v1/models/%s/repo?Revision=master&FilePath=' % repo) if repo else BASE
    out = os.path.join(DEST, name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    req = urllib.request.Request(base + urllib.parse.quote(name), headers={'User-Agent': 'Mozilla/5.0'})
    t0, done, total = time.time(), 0, 0
    with urllib.request.urlopen(req, timeout=60) as r, open(out, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk: break
            f.write(chunk); done += len(chunk)
            if total:
                pct = done * 100 / total
                sys.stdout.write(f'\r  {name}: {done/2**20:6.1f}/{total/2**20:.1f} MB  {pct:5.1f}%')
            else:
                sys.stdout.write(f'\r  {name}: {done/2**20:6.1f} MB')
            sys.stdout.flush()
    dt = time.time() - t0
    print(f'\r  {name}: {done/2**20:.1f} MB in {dt:.1f}s  ({done/2**20/max(dt,0.01):.1f} MB/s)      ')
    verify(out, name)
    return out

if __name__ == '__main__':
    import urllib.parse
    os.makedirs(DEST, exist_ok=True)
    print('下载到:', DEST)
    for f in FILES:
        try: download(f)
        except Exception as e: print('  FAIL', f, type(e).__name__, str(e)[:120])
    try:
        download(BPE_FILE, repo=BPE_REPO)
    except Exception as e: print('  FAIL', BPE_FILE, type(e).__name__, str(e)[:120])

    # 收尾必须核对 6 个文件都在 —— 少一个都会在"加载模型"那一步才炸，那时人已经坐在会议室里了
    need = FILES + [BPE_FILE]
    lo = dict(MIN_BYTES)
    lo[BPE_FILE] = 100 << 10
    missing = [f for f in need if not os.path.exists(os.path.join(DEST, f))
               or os.path.getsize(os.path.join(DEST, f)) < lo.get(f, 1)]
    print('目录内容:')
    for f in sorted(os.listdir(DEST)):
        print(f'   {os.path.getsize(os.path.join(DEST,f))/2**20:8.1f} MB  {f}')
    if missing:
        print('\n[!] 还缺 %d 个文件: %s' % (len(missing), ', '.join(missing)))
        print('    手动下载: https://www.modelscope.cn/models/%s' % REPO)
        sys.exit(1)
    print('\n6 个文件齐全。下一步: python tools/preflight.py')
