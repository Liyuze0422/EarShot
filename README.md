<div align="center">

# EarShot · 顺风耳

**实时面试提词器** —— 面试官问出口，1.3~1.5 秒后屏幕上出现一段能直接照念的回答

本地语音识别 + 本地检索 + LLM 改写，全部跑在你自己的 Windows 机器上

[![CI](https://github.com/Liyuze0422/EarShot/actions/workflows/ci.yml/badge.svg)](https://github.com/Liyuze0422/EarShot/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
![Status](https://img.shields.io/badge/status-beta-orange.svg)

<img src="docs/images/preview.png" width="700" alt="EarShot 浮窗：默认只看核心句，核心句超过 40 字会给黄色警示" />

<sub>↑ 真实的浮窗截图，内容由 `python ui/app.py --selftest` 自动生成（虚构示例，不含任何真实面试内容）</sub>

</div>

---

## 它解决什么问题

线上技术面试里最难的不是「不会」，而是**知道的东西说不出来**：问题听着耳熟、脑子里有料，
却要在三秒内组织成一段有观点、有数字、不跑题的话。

EarShot 不替你回答问题，它做的是**在正确的时刻，把你准备好的材料推到你眼前**：

| 环节 | 做法 | 实测 |
|---|---|---|
| 听 | 只抓**系统回环**（扬声器输出）＝ 面试官的声音，天然区分说话人，你说话不会被误识别 | 20ms 一帧 / 16000Hz |
| 断句 | 能量 VAD 判停 | 600ms（可调至 300ms 激进模式） |
| 识别 | SenseVoiceSmall ONNX int8，**全离线**，音频不出本机 | 80~115ms / 次，RTF 0.025~0.03 |
| 检索 | BM25 + jieba 在你的材料里找相关片段 | 0~1ms |
| 改写 | DeepSeek `deepseek-flash`（关闭思考模式）流式生成「可以照念」的口语回答 | 首字 281~831ms |
| 显示 | PyQt6 置顶浮窗 + 防共享 + 全局热键 | 默认只显示核心句 |

**端到端：面试官说完 → 第一行答案上屏 ≈ 1.3 ~ 1.5 秒。**

## 先看这段：它不做什么

这一节比后面的功能列表重要。这些是实测出来的边界，不是免责声明：

- ❌ **不会替你编答案。** 材料里查不到的名词，它会给一套「坦诚」话术（我确实没实际用过 / 我理解它是……），而不是编一个项目经历骗过面试官。
- ❌ **不是全知。** 它只能检索你放进 `knowledge/` 的材料。知识库空着，屏幕上就只有空话。
- ⚠️ **ASR 会把专有名词写坏。** 面试官的口语转写稿本身就有错字，所以「材料里查无此词」并不等于你真没准备过 —— 用 `config/known_terms.md` 把你会的词钉住。
- ⚠️ **话题续接是已知弱点。** 面试官用「那这个呢」这类没有实词的追问时，检索可能还停留在上一个话题上（实测 39 条真实提问里 6 条属于此类）。
- ⚠️ **只在 Windows 上跑。** 用到 WASAPI 回环采集（soundcard）、RegisterHotKey 全局热键、SetWindowDisplayAffinity 防共享，这些都是 Windows API。
- ⚠️ **第一次识别要等 21.7 秒**（numba JIT 编译），之后才是 ~100ms。所以一定要先预热再进会议室。

## 它长什么样

<div align="center">
<img src="docs/images/preview-expanded.png" width="560" alt="展开后的样子：核心句 + 完整回答 + 深答补充" />
</div>

- **核心句（大字）**：只显示最该照念的那一句，默认就是全部内容 —— 真实面试里你的注视时间是秒级的。
- **黄色警示**：核心句超过 40 字会自动变黄并标「超长 43 字 · 别照念」，防止你一紧张开始念长句。
- **展开区**：`Ctrl+Shift+E` 展开完整回答、追问预案、数字告警。
- **深答区**：`Ctrl+Shift+D`（被占用时自动换键，启动日志里会写明）让一个外部 agent 带工具重新回答一遍，用于设计题。
- **状态栏**：在听的设备、首字耗时、总耗时。

### 全局热键（不依赖窗口焦点）

| 热键 | 作用 |
|---|---|
| `Ctrl+Shift+H` | 隐藏 / 显示浮窗（共享屏幕前来一下） |
| `Ctrl+Shift+P` | 暂停 / 恢复转写（该你自己讲的时候） |
| `Ctrl+Shift+D` | 深度回答（设计题、开放题） |
| `Ctrl+Shift+E` | 展开 / 收起完整内容 |

## 快速开始

> Windows 10/11 x64 · Python 3.11 或 3.12 · 需要联网（下载模型、调用 DeepSeek API）

**第 0 步很重要：把仓库放在纯英文路径下**（比如 `C:\dev\EarShot`）。
中文路径会让 sentencepiece 的 C++ 层报 `NOT_FOUND`，而 Python 侧却认为文件好好地在着 —— 这个坑很难查。详见 [安装教程](docs/安装教程.md)。

> 没装 git 也没关系：打开 https://github.com/Liyuze0422/EarShot ，点绿色的 **Code → Download ZIP**，
> 解压到 `C:\dev\EarShot` 效果一样（**路径必须是纯英文**，原因见下面第 0 步）。

```powershell
git clone https://github.com/Liyuze0422/EarShot.git C:\dev\EarShot
cd C:\dev\EarShot

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # 国内可加 -i https://pypi.tuna.tsinghua.edu.cn/simple

python tools\download_model.py           # 下载 SenseVoiceSmall ONNX（约 230MB）

# 填 DeepSeek 密钥（申请：https://platform.deepseek.com ）
"sk-你的密钥" | Out-File -Encoding utf8 config\api_key.txt

# 放一份示例材料试跑（替换成你自己的简历与项目材料）
Copy-Item examples\knowledge\* knowledge\ -Force

python tools\preflight.py                # 自检：模型 / 密钥 / 音频 / 检索 / 网络逐项体检
python tools\launch.py                   # 起后端 + 浮窗
```

**一键脚本（推荐给不想一条条敲的人）**：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # 建环境 → 装依赖 → 下模型 → 自检
powershell -ExecutionPolicy Bypass -File scripts\run.ps1     # 启动
```

> 第一次识别会慢（21.7 秒的 JIT 预热），建议进会议室前先跑一次让它热起来。

## 实测数据

| 指标 | 实测值 |
|---|---|
| 单次识别（2.4~4.1s 音频） | **80~115 ms** |
| RTF（实时率） | **0.025~0.03**（比实时快约 38 倍） |
| 进程内存 | **491 MB** |
| 模型加载（含预热） | 4.0s + 3.8s |
| 检索（BM25 + jieba） | 索引 1.0s 建好，单次 **0~1 ms** |
| 答案首字延迟 | **281~831 ms** |
| 答案总耗时 | **0.91~1.58 s**，150~300 字 |
| **端到端（说完 → 上屏）** | **约 1.3 ~ 1.5 秒** |

完整数据、换算过程与**未验证的部分**见 [性能实测](docs/性能实测.md)。

## 工作原理

```
   ┌──────────────┐   20ms/帧   ┌────────┐  判停 600ms  ┌──────────────────┐
   │ 系统回环采集  │ ─────────→ │ 能量VAD │ ───────────→ │ SenseVoiceSmall  │
   │ (面试官的声音)│  16kHz 单声道│        │              │ ONNX int8 · 本机 │
   └──────────────┘            └────────┘              └────────┬─────────┘
                                                                 │ 80~115ms
                                                                 ▼
   ┌──────────────┐   命中片段  ┌──────────────────────┐  ┌──────────────┐
   │ 你的材料      │ ─────────→ │ BM25 + jieba 检索     │← │ 提问判定/合并 │
   │ knowledge/   │  0~1ms     │ (领域词典 + 文件名加权)│  │ extract_q.py │
   └──────────────┘            └──────────┬───────────┘  └──────────────┘
                                          │ 问题 + 材料片段
                                          ▼
                          ┌───────────────────────────────┐
                          │ 快答线：DeepSeek flash 流式改写 │  首字 281~831ms
                          │ 深答线（可选）：外部 agent 带工具 │  设计题用
                          └───────────────┬───────────────┘
                                          │ WebSocket (127.0.0.1:8765+)
                                          ▼
                          ┌───────────────────────────────┐
                          │ PyQt6 浮窗：核心句 / 展开 / 深答 │  置顶 · 防共享 · 全局热键
                          └───────────────────────────────┘
```

架构取舍、为什么不用 Silero-VAD / 为什么字级 bigram 通道实测是负收益、健壮性是怎么设计的 —— 见 [架构与原理](docs/架构与原理.md)。

## 目录结构

```
EarShot/
├── server/              后端：音频 → VAD → ASR → 检索 → 快答
│   ├── main.py          FastAPI + WebSocket 入口（端口 8765 起，被占自动顺延）
│   ├── asr_engine.py    回环采集 LoopbackCapture / EnergyVAD / SenseVoiceASR
│   ├── knowledge.py     BM25 检索（切块 + jieba + 领域词典 + 文件名加权）
│   ├── answer.py        快答线：提问分类 → 检索 → DeepSeek 流式生成
│   ├── extract_q.py     提问判定与追问合并       ├── router.py    问题分类
│   ├── bank.py          题库兜底（网络挂了也能给答案）  └── deep.py  深答线（可选）
├── ui/app.py            PyQt6 浮窗：置顶 / 防共享 / 全局热键 / 核心句
├── tools/               启动、自检、下载模型、建库、复盘、评测脚本
├── knowledge/           你的面试材料放这里（已 gitignore，不会被提交）
├── examples/knowledge/  虚构的示例材料，用来试跑
├── config/              公司背景 / 词表 / 配置模板（真实内容已 gitignore）
├── docs/                安装教程、使用说明书、架构、性能、故障排查、FAQ
├── wiki/                可直接推送到 GitHub Wiki 的页面
└── tests/               不需要模型与音频设备的冒烟测试（CI 跑这个）
```

## 配置

三份「你的信息」放进 `config/`（默认都被 gitignore，不会上传）：

| 文件 | 作用 |
|---|---|
| `config/company.md` | 目标公司背景（业务、JD、技术栈、面试轮次）。填得越具体，设计题答得越贴业务 |
| `config/known_terms.md` | 我会、但材料里没写的技术。防止提词器让我对这些词说「我没接触过」 |
| `config/never_used.md` | 材料里出现过、但我其实没做过的。命中就走「坦诚」那套，绝不装懂 |

其余开关放 `config/settings.json`（模板见 `config/settings.example.json`）：模型目录、语料 glob、端口、模型名、超时、深答线命令等。环境变量优先级更高（`TP_MODEL_DIR` / `TP_API_KEY` / `TP_CORPUS_GLOBS` …）。详见 [配置手册](docs/配置手册.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [安装教程](docs/安装教程.md) | 从零到跑起来，含踩坑与自检 |
| [使用说明书](docs/使用说明书.md) | 会前 30 分钟 → 面试中 → 赛后 10 分钟复盘 |
| [配置手册](docs/配置手册.md) | 三个 md、settings.json、环境变量全表 |
| [架构与原理](docs/架构与原理.md) | 每个模块的设计取舍 |
| [性能实测](docs/性能实测.md) | 实测数据与未验证的部分 |
| [故障排查](docs/故障排查.md) | 症状 → 原因 → 处理 |
| [常见问题](docs/常见问题FAQ.md) | 联网、费用、隐私、合规、平台 |
| [隐私与安全](docs/隐私与安全.md) | 数据流、密钥、防共享边界、使用伦理 |
| [发布到 GitHub](docs/发布到GitHub.md) | 把本仓库推到你自己的账号 + 开 Wiki |

## 隐私

- 音频、识别、检索**全部在本机**完成，音频文件不离开你的电脑。
- 只有生成答案时，**「问题文本 + 检索命中的材料片段」**会发到 DeepSeek API。
  不想发就换成本地模型：改 `config/settings.json` 的 `base_url` 指向任何 OpenAI 兼容端点。
- 会话记录落在 `logs/session_*.jsonl`（含转写与答案），`logs/` 已 gitignore，赛后用 `python tools/session_report.py` 复盘。
- 「防共享」用的是 Windows 的 `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`：能挡住常见会议软件的屏幕共享与录屏，但**挡不住对着屏幕拍的手机**，也不保证对所有采集方式有效。

## 使用伦理

这个工具是用来**帮你把已经会的东西说清楚**的，不是用来伪造经历。
面试方若明确禁止使用辅助工具，请遵守他们的规则；对自己的简历与项目如实陈述，
是这个项目所有设计取舍（宁可不答也不编造）的前提。**使用者对使用方式负责。**

## 已知问题 / 路线图

- [ ] 话题续接过弱：无实词的「那这个呢」类追问会跟着上文跑偏
- [ ] 多屏与高 DPI 缩放的适配还不够细
- [ ] 首次预热 21.7 秒可以提前到安装阶段做掉
- [ ] macOS / Linux 端（需要换掉回环采集与窗口 API）

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。提 PR 前请跑：

```powershell
python -m compileall -q server ui tools
ruff check .
pytest -q
```

## 许可

[MIT](LICENSE)。用之前请读一遍上面的[使用伦理](#使用伦理)与[隐私](#隐私)。
