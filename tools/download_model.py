# -*- coding: utf-8 -*-
"""从 ModelScope 下载 SenseVoiceSmall ONNX(int8) 到 models/ 目录，纯标准库实现。"""
import os
import sys
import json
import urllib.request
import time

REPO = 'iic/SenseVoiceSmall-onnx'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
