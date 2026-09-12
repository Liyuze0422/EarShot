# server/ —— 后端（模块地图）

采集线程抓到面试官一句话，走完「判停 → 识别 → 检索 → 生成」，所有事件经 WebSocket 推给浮窗。
入口 `python server/main.py`：端口从 8765 起、被占顺延，实际端口写仓库根的 `.runtime_port`。

- `main.py`：FastAPI + WS 服务、端口顺延、采集线程自愈、会话落盘 `logs/session_*.jsonl`
- `settings.py`：统一配置（环境变量 > `config/settings.json` > 默认值）：model_dir / api_key / corpus_globs / ports / dsh_paths
- `asr_engine.py`：回环采集、能量 VAD、SenseVoiceSmall ONNX 识别；`extract_q.py`：从整段识别文本里抠出真提问
- `router.py`：问题分类（经历/设计/行为/动机/反问/不答）；`answer.py`：检索 → 提示词 → 流式生成 → 答案自检
- `knowledge.py`：切块 + jieba + 词级 BM25；`bank.py`：会前题库（补充材料 + 断网兜底）；`deep.py`：可选深答线
