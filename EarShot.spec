# -*- mode: python ; coding: utf-8 -*-
"""EarShot 打包配置（PyInstaller）。

三个设计决定，都不是随手写的：

1. **数据留在 exe 旁边，不打进包** —— config / knowledge / logs / 知识库 是用户要改、要加、
   要留档的东西。打进 _MEIPASS（一个临时解压目录）的话用户根本找不着，日志重启即失。
   代码里靠 server/settings.py 的 _split_roots() 把「代码在哪」和「数据在哪」分开。

2. **源码作为数据文件带上（datas）** —— 打包后**一个 exe 分饰多角**（后端 / 浮窗 / 选库窗口 /
   项目脚本），靠 tools/launch.py 的 --role / --run 用 runpy 按路径跑。
   没有独立的 .py 可起，所以源码必须在包里、且是真实文件。

3. **四个脚本一起喂给 Analysis** —— 因为 (2) 用的是 runpy 按路径跑，PyInstaller 的静态分析
   看不见它们的依赖，fastapi / PyQt6 / funasr_onnx / soundcard 全会漏掉，
   要手工列几十条 hiddenimports 且极容易漏。所以把四个入口都放进 Analysis 让它自己扫；
   只挑第一个脚本生成 exe，其余三个只是"探针"。

产出：dist/EarShot/EarShot.exe（+ 同级 _internal/，两者必须在一起）。
"""
import os

datas = []
for d in ('server', 'ui', 'tools', 'assets'):
    if not os.path.isdir(d):
        continue
    for name in sorted(os.listdir(d)):
        if name == '__pycache__':
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p):
            # ★ launch.py 是 exe 的入口脚本，**绝不能同时出现在 datas 里** ——
            # PyInstaller 会因此把入口登记成 DATA（不是可执行入口），
            # 打出来的 exe 起来什么都不做：退出码 0、不写日志、不弹窗。
            # （实测踩过，查了半天：Analysis-00.toc 里它那行是 'DATA' 而不是 'PYSOURCE'。）
            if d == 'tools' and name == 'launch.py':
                continue
            datas.append((p, d))
        elif os.path.isdir(p):
            datas.append((p, os.path.join(d, name)))

# uvicorn 用字符串动态导入这些协议实现，静态分析看不到（老问题了）
hidden = [
    'uvicorn.logging',
    'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
]

a = Analysis(
    ['tools/launch.py', 'server/main.py', 'ui/app.py', 'ui/library.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 只排明确用不上的：排多了会在运行时才炸，而这里没有测试兜底
    # 只排明确用不上的：排多了会在运行时才炸，而这里没有测试兜底。
    # torch / torchaudio / torchvision 一共 361MB，而项目代码里没有任何 import torch
    # （ASR 走 onnxruntime，见 server/asr_engine.py）；它们是被某个包的 import 链拖进来的。
    # pyarrow 是 pandas 的存储后端，matplotlib 不画图 —— 都不需要。
    # transformers / pandas / numba 先留着：不确定 funasr_onnx 是否真用到，宁大勿崩。
    excludes=['tkinter', 'IPython', 'pytest', '_pytest',
              'torch', 'torchaudio', 'torchvision', 'pyarrow', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 挑入口脚本 —— 这里有两个坑，都踩过：
#   ① a.scripts[0] 是**单个三元组**，直接传给 EXE 会被当 TOC 解析，
#      报 "too many values to unpack (expected 3)"；
#   ② 每一项是 **(dest名, 源路径, 类型)**，dest 名**不带扩展名**（'launch'），
#      所以不能拿 s[0] 去比 'launch.py' —— 匹配结果为空 → EXE 拿到空入口列表 →
#      打出一个"没有入口"的 exe：启动约 1 秒、退出码 0、不写任何日志、什么都不做。
#      这个症状极像"权限问题"或"缺 dll"，其实是入口列表空了。
# 另外三个脚本留在 Analysis 里当"依赖探针"（它们的依赖会被收集），本身不生成 exe。
ENTRY = [s for s in a.scripts if os.path.basename(s[1]) == 'launch.py'][:1]
assert len(ENTRY) == 1, (
    '挑不到入口脚本 launch.py。a.scripts 里的候选是: %r —— '
    '注意每项是 (dest名, 源路径, 类型)，dest 名不带 .py'
    % [(x[0], x[2]) for x in a.scripts])

exe = EXE(
    pyz,
    ENTRY,
    [],
    exclude_binaries=True,
    name='EarShot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # ★ 不能写 console=False。实测（PyInstaller 6.22.3）：windowed 版打出来一启动就弹
    #   "Failed to start embedded python interpreter" —— 连主进程的 Python 都初始化不了，
    #   而同一个 spec 只把这里改成 True 就一切正常。
    #   所以用控制台版 + hide-early（bootloader 早期就把控制台窗口藏掉），
    #   效果等同于无黑窗，又不踩那个坑。stdout 仍是有效的，被启动器重定向进 logs/。
    console=True,
    hide_console='hide-early',
    disable_windowed_traceback=False,
    icon='assets/icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='EarShot',
)
