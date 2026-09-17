# 打包成 exe

把顺风耳打成一个不用装 Python 的独立程序。产出在 `dist/EarShot/`：

```
dist/EarShot/
  EarShot.exe      ← 双击这个
  _internal/       ← 约 486MB 的运行时，必须和 exe 在一起
```

## 一条命令

```powershell
.venv\Scripts\pyinstaller.exe --noconfirm EarShot.spec
```

约 4~6 分钟。改过 `server/` `ui/` `tools/` 里任何代码之后都要重跑。
（`--clean` 会连分析缓存一起清掉，改过 spec 再考虑加。）

## 分发

**整个 `dist/EarShot/` 文件夹一起拷**，不能只拷 exe —— 它是 onedir 模式，
`_internal/` 里放着 Python 运行时和全部依赖。

目标机器**不需要**装 Python、不需要 `.venv`、不需要任何环境变量。
模型文件（`SenseVoiceSmall-onnx`）本来就在外部目录，也不在包里，
所以要保证那台机器上 `config/settings.json` 指的模型路径也存在。

## 数据在哪

**exe 旁边**。准确说：从 exe 所在目录**往上找，找到第一个含 `config/settings.json` 的目录**，
那就是数据根。

这样布局的好处是 exe 放在 `仓库/dist/EarShot/` 时，数据仍然是仓库根的 `config/`、`knowledge/`、
`logs/`、`知识库/` —— 和源码模式共用一份，换库、加材料都不用在两个地方各来一遍。
代码在哪（`_MEIPASS` 临时解压目录）和数据在哪是两件事，见 `server/settings.py` 的 `_split_roots()`。

想让 exe 独立携带数据，就把 `config/` 和 `knowledge/` 一起拷到 exe 那一层。

## 想改图标

图标由 `tools/make_icon.py` 生成（Pillow 画 1024px 再缩到多尺寸）：

```powershell
.venv\Scripts\python.exe -X utf8 tools\make_icon.py
```

输出 `assets/icon.png`（512px）和 `assets/icon.ico`（16/24/32/48/64/128/256）。
改完**要重新打包**才会进 exe（`EarShot.spec` 里的 `icon=` 指向 ico）。

## 打包时容易踩的四个坑

完整记录见 `CHANGELOG.md` 的 0.9.15，这里只列最要命的一句：

1. **入口脚本不能同时出现在 `datas` 里** —— 会被当成数据文件，exe 起来什么都不做。
2. **`a.scripts` 每项是 `(dest名, 源路径, 类型)`，dest 名不带 `.py`** ——
   挑入口时用错了下标就是空列表，生成一个没有入口的 exe。spec 里有 `assert` 兜底。
3. **`console=False`（windowed）打出来起不来**，报 `Failed to start embedded python interpreter`。
   用 `console=True` + `hide_console='hide-early'`。
4. **冻结进程起子进程要清 `PYTHONHOME`/`PYTHONPATH`/`_PYI_*`**，否则子进程静默死。
   见 `tools/launch.py` 的 `clean_env()`。

## 出问题怎么查

**第一步永远是：把 `EarShot.spec` 里的 `console` 改成 `True` 重新打一个包。**

无控制台的版本会把异常塞进一个标题为 `Error` 的对话框，
双击时看起来只是「没反应」。有了控制台就能直接看到 traceback。

（那个对话框的内容也可以用截图 + OCR 读出来，窗口标题是 `Error`。）

查完记得改回 `console=True` + `hide_console='hide-early'` —— 也就是保持原样，
这两项是配套的，改回 `False` 会重新触发坑 3。

## 诚实边界

- **启动比源码模式慢约 16 秒**（40 秒 vs 24 秒），是 onedir 首次读盘的固定成本。
- **没有代码签名**，首次运行 SmartScreen 可能报「未知发布者」。
- **只在 Windows 上验证过**；换系统要重新打包。
- 包体 486MB 已经排掉 `torch` 等 577MB，但 `transformers`/`pandas`/`numba` 保留了 ——
  想再压必须用真实录音跑一遍，别只看体积。
- 回滚：删掉 `dist/` 就回到源码模式，`启动提词器.bat` 一直是可用的那条路。
