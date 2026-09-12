# EarShot · 顺风耳 —— 实时面试提词器

> 面试官问出口，1.3~1.5 秒后屏幕上出现一段**可以直接照念**的回答。
> 本地 ASR + 本地检索 + LLM 改写，全部跑在你自己这台 Windows 上。

![preview](images/preview.png)

> 截图由 `python ui/app.py --selftest` 自动生成（虚构示例，不含真实面试内容）。
> 图片由 `scripts/sync_wiki.ps1` 一并拷进 `wiki/images/`，推送 Wiki 时一起带上去。

## 三分钟上手

```powershell
git clone https://github.com/<你的用户名>/EarShot.git C:\dev\EarShot   # 必须纯英文路径
cd C:\dev\EarShot
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python tools\download_model.py            # 约 230MB，下到 models\
"sk-你的密钥" | Out-File -Encoding utf8 config\api_key.txt
Copy-Item examples\knowledge\* knowledge\ -Force
python tools\preflight.py                 # 自检
python tools\launch.py                    # 起后端 + 浮窗
```

（不想一条条敲：`powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` 然后 `scripts\run.ps1`）

## 它不做什么（先读这四条，能省很多时间）

1. **不编答案**：材料里没有的名词，它给「坦诚」话术，不会编项目经历。
2. **不是全知**：只在 `knowledge/` 里检索。知识库空着，屏幕上就是空的。
3. **ASR 会写坏专有名词**：靠 `config/known_terms.md` 把你会的词钉住。
4. **只在 Windows 上跑**：回环采集、全局热键、防共享都是 Windows API。

## 导航

| 页面 | 什么时候看 |
|---|---|
| [[安装教程]] | 第一次装，或者换机器重装 |
| [[使用说明书]] | 会前 30 分钟到赛后 10 分钟，整个流程 |
| [[配置手册]] | 改模型路径、换 API 端点、调超时、写词表 |
| [[架构与原理]] | 想知道每个模块为什么这么设计 |
| [[性能实测]] | 想看实测数字与「哪些没验证过」 |
| [[故障排查]] | 抓不到声音、答案不出来、浮窗没反应 |
| [[常见问题]] | 联网、费用、隐私、合规、平台 |
| [[隐私与安全]] | 数据流、密钥、防共享的边界、使用伦理 |
| [[扩展与二次开发]] | 换 ASR、换模型、接自己的深答线 |

## 实测数据（一屏看完）

| 指标 | 实测值 |
|---|---|
| 单次识别（2.4~4.1s 音频） | 80~115 ms |
| RTF | 0.025~0.03（比实时快约 38 倍） |
| 进程内存 | 491 MB |
| 检索（BM25 + jieba） | 0~1 ms |
| 答案首字 | 281~831 ms |
| 答案总耗时 | 0.91~1.58 s |
| **端到端（说完 → 上屏）** | **约 1.3~1.5 秒** |

## 全局热键

| 热键 | 作用 |
|---|---|
| `Ctrl+Shift+H` | 隐藏 / 显示浮窗 |
| `Ctrl+Shift+P` | 暂停 / 恢复转写 |
| `Ctrl+Shift+D` | 深度回答（被占用时自动换键，启动日志会写明） |
| `Ctrl+Shift+E` | 展开 / 收起完整回答 |

## 使用伦理（请认真读）

这个工具是用来**帮你把已经会的东西说清楚**的，不是用来伪造经历。
面试方若明确禁止使用辅助工具，请遵守他们的规则。使用者对使用方式负责。