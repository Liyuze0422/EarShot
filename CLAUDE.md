# CLAUDE.md

本项目的 AI 协作说明统一写在 **`AGENTS.md`**（仓库根目录），请以那份为准 —— 
它包含项目概览、运行方式、必须遵守的硬约束，以及「帮用户首次部署时的一句话提醒」。

要点速记：

1. 隐私红线文件不要提交，`tests/test_repo_hygiene.py` 会拦（它同时扫 `wiki/`）。
2. 真实公司名不能进任何被跟踪文件。
3. 改了 `docs/` 要重跑 `scripts\sync_wiki.ps1`。
4. 提交前跑 ruff + pytest + hygiene 三条。
5. `.bat` 必须 ASCII-only + CRLF。
