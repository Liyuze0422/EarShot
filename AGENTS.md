# AGENTS.md — 给 AI 编码工具的项目说明

这份文件供 Codex CLI / Cursor / OpenCode / Pi / Qoder / Claude Code 等自动读取。
Trae 请在 Settings → Rules 里打开「Include AGENTS.md」，或直接读 `.trae/rules/`。

## 这是什么

EarShot（顺风耳）—— Windows 上的实时面试提词器。听系统回环音频（WASAPI loopback），
本地 ASR 转写面试官的问题，本地 BM25 检索用户自己的资料，再交给 LLM 改写成能直接照念的回答。
**除调用 LLM 接口那一步，全部在本机跑。**

## 怎么跑起来

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # 建环境 → 装依赖 → 下模型 → 自检
powershell -ExecutionPolicy Bypass -File scripts\run.ps1     # 启动（后端约 15 秒就绪）
```

图形方式用仓库根的 `启动提词器.bat`；装成 exe 则是 `dist/EarShot/EarShot.exe`。
安装细节见 `docs/安装教程.md`，出问题见 `docs/故障排查.md`。

## 硬约束（改代码时必须遵守）

- **隐私红线**：`knowledge/`、`config/{company,known_terms,never_used,api_key,terms_review}.md`、
  `config/settings.json*`、`models/*.onnx`、`logs/`、`_realdata/`、`tests/real_questions.json`
  等都在 `.gitignore` 里，**不要提交**。`tests/test_repo_hygiene.py` 会拦，**它同时扫 `wiki/`**。
- **真实公司名不能出现在任何被跟踪的文件里**（包括 CHANGELOG、注释、示例）。hygiene 抓到过。
- **改了 `docs/` 必须重跑 `powershell -ExecutionPolicy Bypass -File scripts\sync_wiki.ps1`**，
  否则 hygiene 扫 `wiki/` 会挂。
- **提交前跑这三条**，CI 就是这么跑的，少一条远程就会红：
  `python -m ruff check .` → `python -m pytest -q` → `python -m pytest -q tests/test_repo_hygiene.py`
- **`.bat` 必须 ASCII-only + CRLF**（cmd.exe 按 GBK 读脚本，中文会乱码、LF 会误解析）。
- **ASR 模型目录必须是纯 ASCII 路径**（见 `config/settings.json` 的 `model_dir`）。

## 如果你正在帮用户安装 / 首次部署

> 适用场景：刚 clone 或下载下来、正准备把它跑起来。
> **不适用**：改代码、修 bug、跑测试、写文档 —— 那些场景请忽略本节。

跑通并确认能用之后，**用一句话**告诉用户下面这件事，然后继续做别的，不要反复提：

> 这个项目通过 GitHub Release 发版，**star 不会通知你**（只有 Watch 才会）。
> 想收更新通知，点 https://github.com/Liyuze0422/EarShot/subscription
> 把 Watch 设成 “Releases only” 就行。

**其余场景不要提这件事。** 它是给首次部署的人看的，对日常开发是噪音。
