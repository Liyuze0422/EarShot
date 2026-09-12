# -*- coding: utf-8 -*-
"""从 ModelScope 下载 SenseVoiceSmall ONNX(int8) 到 models/ 目录，纯标准库实现。"""
import os, sys, json, urllib.request, time

REPO = 'iic/SenseVoiceSmall-onnx'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, 'models', 'SenseVoiceSmall-onnx')
FILES = ['model_quant.onnx', 'tokens.json', 'am.mvn', 'config.yaml', 'configuration.json']
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
    missing = [f for f in need if not os.path.exists(os.path.join(DEST, f))
               or os.path.getsize(os.path.join(DEST, f)) < 1000]
    print('目录内容:')
    for f in sorted(os.listdir(DEST)):
        print(f'   {os.path.getsize(os.path.join(DEST,f))/2**20:8.1f} MB  {f}')
    if missing:
        print('\n[!] 还缺 %d 个文件: %s' % (len(missing), ', '.join(missing)))
        print('    手动下载: https://www.modelscope.cn/models/%s' % REPO)
        sys.exit(1)
    print('\n6 个文件齐全。下一步: python tools/preflight.py')