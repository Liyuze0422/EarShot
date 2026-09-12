<div align="center">

# EarShot

**A real-time interview teleprompter for Windows.** The interviewer finishes a question,
and 1.3–1.5 seconds later a ready-to-speak answer appears on a floating window.

Local ASR + local retrieval + LLM rewriting — everything runs on your own machine.

[![CI](https://github.com/Liyuze0422/EarShot/actions/workflows/ci.yml/badge.svg)](https://github.com/Liyuze0422/EarShot/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

<img src="docs/images/preview.png" width="700" alt="EarShot floating window" />

</div>

---

## What it does

Online technical interviews are rarely lost on knowledge. They are lost on **delivery**:
you know the material, but you have three seconds to turn it into a crisp, on-topic answer.

EarShot does not answer for you. It puts **the material you prepared** in front of you at the right moment.

| Stage | How | Measured |
|---|---|---|
| Listen | System loopback capture (speaker output only) — the interviewer's voice, never yours | 20 ms frames, 16 kHz mono |
| Segment | Energy VAD endpointing | 600 ms (300 ms aggressive mode) |
| Transcribe | SenseVoiceSmall ONNX int8, fully offline | 80–115 ms, RTF 0.025–0.03 |
| Retrieve | BM25 + jieba over your own material | 0–1 ms |
| Rewrite | DeepSeek `deepseek-flash` (thinking disabled), streamed | first token 281–831 ms |
| Display | PyQt6 always-on-top window, capture-excluded, global hotkeys | core sentence only |

**End to end: question ends → first line on screen ≈ 1.3–1.5 s.**

## What it does *not* do

- It will **not invent experience**. For terms that are not in your material it falls back to an honest "I haven't worked with that directly" script.
- It only knows what you put in `knowledge/`.
- ASR mangles proper nouns; keep `config/known_terms.md` up to date.
- Topic carry-over on contentless follow-ups ("and what about that?") is a known weakness.
- **Windows only** (WASAPI loopback, RegisterHotKey, SetWindowDisplayAffinity).
- The first transcription takes 21.7 s (numba JIT warm-up); afterwards it is ~100 ms.

## Quick start

> Windows 10/11 x64, Python 3.11/3.12. **Clone into an ASCII-only path** (e.g. `C:\dev\EarShot`) —
> a non-ASCII path makes sentencepiece fail inside third-party C++ code.

```powershell
git clone https://github.com/<you>/EarShot.git C:\dev\EarShot
cd C:\dev\EarShot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python tools\download_model.py
"sk-your-key" | Out-File -Encoding utf8 config\api_key.txt
Copy-Item examples\knowledge\* knowledge\ -Force
python tools\preflight.py
python tools\launch.py
```

Or run the one-shot scripts: `scripts\setup.ps1` then `scripts\run.ps1`.

## Hotkeys

| Hotkey | Action |
|---|---|
| `Ctrl+Shift+H` | Hide / show the window |
| `Ctrl+Shift+P` | Pause / resume transcription |
| `Ctrl+Shift+D` | Deep answer (falls back to another key if taken) |
| `Ctrl+Shift+E` | Expand / collapse the full answer |

## Documentation

All documentation is currently in Chinese — the primary audience.

| Doc | Content |
|---|---|
| [docs/安装教程.md](docs/安装教程.md) | Installation, from zero to running |
| [docs/使用说明书.md](docs/使用说明书.md) | Before / during / after the interview |
| [docs/配置手册.md](docs/配置手册.md) | settings.json, env vars, config files |
| [docs/架构与原理.md](docs/架构与原理.md) | Architecture and design trade-offs |
| [docs/性能实测.md](docs/性能实测.md) | Benchmarks and what has *not* been verified |
| [docs/故障排查.md](docs/故障排查.md) | Troubleshooting |
| [docs/隐私与安全.md](docs/隐私与安全.md) | Data flow, privacy, ethics |

## Privacy

Audio, ASR and retrieval stay on your machine. Only the question text plus the retrieved
material excerpts are sent to the DeepSeek API while generating an answer — point
`base_url` at any OpenAI-compatible endpoint to keep it fully local. Session logs live in
`logs/` (git-ignored).

## License

[MIT](LICENSE). Use it to explain what you actually know — not to fake what you don't.
