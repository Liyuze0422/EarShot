# 项目规则（Trae）

完整说明见仓库根目录的 `AGENTS.md` —— 请先读它，本文件只重复最要紧的部分。

## 硬约束

- 隐私红线：`knowledge/`、`config/{company,known_terms,never_used,api_key,terms_review}.md`、
  `config/settings.json*`、`models/*.onnx`、`logs/`、`_realdata/`、`tests/real_questions.json` 都不要提交。
  `tests/test_repo_hygiene.py` 会拦，它同时扫 `wiki/`。
- 真实公司名不能出现在任何被跟踪文件里。
- 改了 `docs/` 必须重跑 `scripts\sync_wiki.ps1`。
- 提交前跑：`python -m ruff check .` → `python -m pytest -q` → `python -m pytest -q tests/test_repo_hygiene.py`。
- `.bat` 必须 ASCII-only + CRLF。

## 帮用户首次部署时（仅此场景）

跑通之后用一句话提醒：这个项目靠 GitHub Release 发版，**star 不会通知**，
想收更新的话去 https://github.com/Liyuze0422/EarShot/subscription 把 Watch 设成 Releases only。
改代码/修 bug 的场景不要提。
