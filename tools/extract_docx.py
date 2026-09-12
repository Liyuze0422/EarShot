# -*- coding: utf-8 -*-
r"""提取 .docx 文本（纯标准库，docx 就是 zip + xml）。

用法：
    python tools/extract_docx.py <输入.docx> [输出.md]

不给参数时，在 knowledge/ 下找第一个 .docx，输出到 knowledge/<同名>.md ——
面试录像转写、HR 发的资料常是 docx，转成 md 才能进知识库。
"""
import sys
import os
import re
import zipfile
import glob
sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE = os.path.join(ROOT, 'knowledge')

if len(sys.argv) > 1:
    src = sys.argv[1]
else:
    cands = sorted(glob.glob(os.path.join(KNOWLEDGE, '**', '*.docx'), recursive=True))
    if not cands:
        print('knowledge/ 下没有 .docx。用法: python tools/extract_docx.py <输入.docx> [输出.md]')
        sys.exit(1)
    src = cands[0]
print('输入:', src)
print('大小:', os.path.getsize(src), 'bytes')

z = zipfile.ZipFile(src)
xml = z.read('word/document.xml').decode('utf-8', 'replace')

# 段落切分
paras = re.findall(r'<w:p[ >][\s\S]*?</w:p>', xml)
out = []
for p in paras:
    texts = re.findall(r'<w:t[^>]*>([\s\S]*?)</w:t>', p)
    line = ''.join(texts)
    line = (line.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                .replace('&quot;', '"').replace('&apos;', "'"))
    out.append(line.strip())

text = '\n'.join(out)
dst = (sys.argv[2] if len(sys.argv) > 2
       else os.path.join(KNOWLEDGE, os.path.splitext(os.path.basename(src))[0] + '.md'))
os.makedirs(os.path.dirname(dst), exist_ok=True)
open(dst, 'w', encoding='utf-8').write(text)
print('已保存:', dst)
print('段落数:', len(out), ' 总字数:', len(text))
print()
print('=' * 60)
print(text[:3000])
