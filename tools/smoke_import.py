# -*- coding: utf-8 -*-
"""冒烟：把后端模块整体 import 一遍 —— 确认依赖齐全、路由分类器能用。

不加载 ASR 模型、不打开音频设备、不连网络，跑一次一两秒。
用法（仓库根目录下）：python tools/smoke_import.py
"""
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import main                     # noqa: E402  —— 后端入口（导入即校验 fastapi/uvicorn/soundcard/numpy）
from router import classify     # noqa: E402

print('后端模块导入 OK')
print('  候选端口  :', main.PORTS)
print('  模型目录  :', main.MODEL_DIR)
print('  语料 glob :', __import__('knowledge').CORPUS_GLOBS)
print('跳过不该答的:', classify('稍等，我把声音放大，听得到吗？'))
print('该答的      :', classify('那个推荐系统是你自己搭的吗？'))
print('recent 上下文:', main.STATE.get('recent'))
print('浏览器版界面 app/ 存在 :', os.path.isdir(main.UI_DIR), '(可选，浮窗不需要)')
