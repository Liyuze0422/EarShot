# 发布到 GitHub（含 Wiki）

这份文档把「本仓库推到你自己的账号」和「把 wiki/ 推到 GitHub Wiki」两步都写清楚。
全程只需要 Git；有 gh CLI 的话更省事（第 4 节）。

---

## 0. 发布前的 60 秒自查（别跳过）

公开仓库最容易出的不是代码问题，是**把私人信息推上去了**。先跑一遍：

```powershell
cd C:\dev\EarShot
pytest -q tests\test_repo_hygiene.py     # 检查个人路径 / 密钥 / 大文件
git status --short                        # 确认没有 config\api_key.txt、knowledge\ 里的材料
```

再确认这三件事：

1. `knowledge/` 里只有 `README.md` 和 `examples/`（你自己的材料默认被 .gitignore 挡着）；
2. `config/api_key.txt`、`config/company.md`、`config/known_terms.md`、`config/never_used.md` 都没有被 `git add`；
3. `models/` 里没有 230MB 的 onnx（模型让用户自己跑 `tools/download_model.py`）。

顺手把 `LICENSE` 里的 `Copyright (c) 2026 EarShot Contributors` 换成你的名字或 GitHub 用户名。

---

## 1. 本地初始化仓库

```powershell
cd C:\dev\EarShot
git init -b main
git add .
git status --short          # 再看一眼列表里没有私人文件
git commit -m "feat: EarShot 0.9.0 —— 实时面试提词器首个公开版本"
```

> 如果你的 Git 还没配身份：`git config --global user.name "你的名字"` 和 `git config --global user.email "你的邮箱"`。

---

## 2. 在 GitHub 上建仓库

网页路径：右上角 **+ → New repository**

| 字段 | 填什么 |
|---|---|
| Repository name | `EarShot` |
| Description | `实时面试提词器：本地 ASR + BM25 检索 + LLM 快答，说完 1.3 秒出答案（Windows）` |
| Public / Private | **Public** |
| Add a README / .gitignore / license | **三个都不要勾**（仓库里已经有了，勾了会冲突） |

建好后把远程地址接上并推送：

```powershell
git remote add origin https://github.com/<你的用户名>/EarShot.git
git push -u origin main
```

如果推送时要求登录：GitHub 从 2021 年起不再接受账号密码，
用 **Personal Access Token**（Settings → Developer settings → Personal access tokens → Fine-grained tokens，勾 `Contents: Read and write`）当密码，
或者先在 Windows 上装一次 Git Credential Manager 走浏览器授权。

---

## 3. 仓库页面的收尾（30 秒，但很值）

- **About**（右上角齿轮）：Description 用上面那句；Website 留空；**Topics** 建议填 `interview` `teleprompter` `asr` `sensevoice` `onnx` `bm25` `realtime` `windows` `pyqt6`
- **Releases** → Draft a new release → Tag `v0.9.0` → 标题 `EarShot 0.9.0` → 描述直接复制 `CHANGELOG.md` 里 `## 0.9.0` 那一节
- 命令行等价写法：

```powershell
git tag -a v0.9.0 -m "EarShot 0.9.0 首个公开版本"
git push origin v0.9.0
```

---

## 4. 用 gh CLI 一条命令搞定（可选）

```powershell
winget install --id GitHub.cli
gh auth login                                   # 选 GitHub.com → HTTPS → 浏览器授权
gh repo create EarShot --public --source=. --remote=origin --push \
  --description "实时面试提词器：本地 ASR + BM25 + LLM 快答，说完 1.3 秒出答案（Windows）"
```

---

## 5. 发布 Wiki

GitHub 的 Wiki 是**独立的 git 仓库**（`<repo>.wiki.git`），和主仓库分开推送。
本仓库的 `wiki/` 目录已经按 GitHub Wiki 的格式写好了（`Home.md`、`_Sidebar.md`、`_Footer.md` + 各页面）。

**第 1 步：先让 GitHub 把 Wiki 建出来**

打开仓库页面 → 顶部 **Wiki** 标签 → **Create the first page** → 标题随便写（比如 `Home`）→ Save。
（这一步必须做：Wiki 仓库在你创建第一页之前是不存在的，直接 clone 会报 `repository not found`。）

**第 2 步：把本仓库的 wiki/ 推上去**

```powershell
git clone https://github.com/<你的用户名>/EarShot.wiki.git
cd EarShot.wiki
Copy-Item ..\EarShot\wiki\* . -Force
git add .
git commit -m "docs: 初始 Wiki（安装 / 使用 / 配置 / 原理 / 排障 / FAQ）"
git push
```

> **两个踩过的坑**（第一次推 Wiki 必踩）：
>
> 1. **Wiki 是独立仓库，clone 出来没有你的 git 身份** —— `git commit` 会直接报
>    `Author identity unknown`。先在这个 clone 里配一次（只对它生效，不动全局）：
>
>    ```powershell
>    git config user.name "你的名字"
>    git config user.email "你的邮箱"
>    ```
>
> 2. **Wiki 仓库的默认分支是 `master`，不是 `main`** —— 推的时候写 `git push origin master`。
>    不确定就先跑 `git rev-parse --abbrev-ref HEAD` 看当前分支名。
>
> 推完核对：`git log --oneline -1`，再回仓库 Wiki 标签刷新看一眼。

**第 3 步：检查**

回到仓库 → Wiki 标签，应该能看到带侧边栏的多页文档。
以后主仓库的 `docs/` 更新了，重新执行第 2 步的 Copy-Item + commit + push 即可。

> 想省事也可以不推 Wiki：`wiki/` 里的内容本来就是从 `docs/` 整理来的，仓库内直接读 `docs/` 一样完整。

---

## 6. 以后怎么持续更新

```powershell
git add .
git commit -m "fix: 描述你改了什么"
git push

# Wiki 同步（改过 docs/ 或 wiki/ 之后）
cd ..\EarShot.wiki ; Copy-Item ..\EarShot\wiki\* . -Force ; git add . ; git commit -m "docs: 同步" ; git push
```

---

## 7. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `git push` 报 `remote: Support for password authentication was removed` | 用 PAT 当密码，或走 Git Credential Manager 浏览器授权 |
| clone `EarShot.wiki.git` 报 `repository not found` | Wiki 还没启用，先在网页上创建第一页（见第 5 节第 1 步） |
| 推上去发现带了大文件 | `git rm --cached` 掉，补进 `.gitignore`；已经 push 过的还要用 `git filter-repo` 清历史 |
| 不小心把 `config/api_key.txt` 推上去了 | **立刻去 DeepSeek 后台吊销这把密钥**，然后 `git rm --cached` + 重写历史 + 强制推送 |
| CI 红了（`.github/workflows/ci.yml`） | 本地先跑 `ruff check .` 与 `pytest -q` 复现；hygiene job 专门查个人路径与密钥 |