# transcribe-cli

> 本地音视频转写 · 摘要 · 翻译 · 字幕生成
> **全程离线，零 API 成本，素材不离开你的设备。**

[![CI](https://github.com/tanghui2/transcribe-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/tanghui2/transcribe-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

把任意音视频文件丢进去，出来一套可直接使用的字幕、纪要和译文。

```bash
python -m transcribe_cli 课程录像.mp4 --language zh --summarize --translate English
```

**175 分钟中文课程视频 → 27 分钟跑完全流程（6.4 倍实时）**，产出 SRT / VTT / TXT / JSON / 中文纪要 / 英文译文共 6 份文件；加 `--bilingual-srt` 则第 7 份（时间轴对齐的双语字幕）一并产出。

📖 **[部署指南](DEPLOY.md)** · 🧾 [项目介绍](项目介绍.md) · 📝 [多场合文案](介绍文案-多场合.md)

---

## 为什么做这个

字幕工具的现状是两头不讨好：

| | 云端方案 | 现状 | 本项目 |
|---|---|---|---|
| 成本 | 按分钟计费，用得越多越贵 | Happy Scribe €17/月起、Maestra $39/月起 | **0** |
| 隐私 | 素材必须上传 | 课程、客户会议、未发布内容都得传出去 | **不出一台机器** |
| 断网 | 完全不可用 | 飞机上、涉密环境、内网里直接歇业 | **照常工作** |
| 形态 | 越用越贵，越做越像生意 | 独立开发者做云端 AI，规模越大越被成本拖住 | **做完即用** |

我不是为了做产品而做产品。是因为发现一件事：**很多工具的"长期运营负担"不是来自功能，而是来自架构选型。** 一旦把推理放进云端，成本曲线就永远压在你头上；一旦需要内容供给，就永远有做不完的活。

这个项目是按"做完即用"倒推出来的架构：本地推理 + 无内容依赖 + 无账号体系。

## 核心能力

| 能力 | 说明 |
|---|---|
| **转写** | faster-whisper large-v3 + CUDA，VAD 静音过滤，词级时间戳可选 |
| **摘要** | 本地 LLM 产出「概要 / 要点 / 待办」三段式中文纪要 |
| **翻译** | 整段通顺译文，或逐段对齐的双语字幕 |
| **字幕导出** | SRT（标准）、VTT（网页）、TXT、JSON（含词级时间戳） |
| **双语字幕** | 原文在上译文在下，时间轴与原文**严格对齐**，可直接用于压制 |
| **环境自检** | `--check` 一键确认 CUDA、模型、Ollama 是否就绪 |

## 快速开始

```bash
pip install -r requirements.txt

python -m transcribe_cli --check -v        # 先自检
python -m transcribe_cli 录播.mp4           # 只转写
python -m transcribe_cli 录播.mp4 --summarize
python -m transcribe_cli 录播.mp4 --translate English --bilingual-srt
```

**环境要求**

- Python 3.11+（已在 3.13 验证）
- [Ollama](https://ollama.com) 运行中，已拉取 `ollama pull qwen3:8b`
- faster-whisper 模型目录（默认 `E:/whisper_model`，用 `-m` 指定）
- Windows + NVIDIA GPU：需 CUDA 运行库，默认在 `E:/cuda_dlls`，可用 `TRANSCRIBE_CUDA_DIR` 覆盖
- 无 GPU 也能跑：`--device cpu`（用 int8 精度）

## 输出

```
out/
├── 录播.srt              # 标准字幕，直接挂播放器
├── 录播.vtt              # WebVTT，网页播放器用
├── 录播.txt              # 纯文本
├── 录播.json             # 结构化，含词级时间戳
├── 录播.summary.md       # 中文纪要（概要 / 要点 / 待办）
├── 录播.English.md       # 整段通顺译文（适合阅读）
└── 录播.English.srt      # 双语字幕，时间轴严格对齐（适合直接压制）
```

翻译有两种模式，按用途选：

- **默认**：整段翻译成连续文本，读起来自然，适合快速浏览
- **`--bilingual-srt`**：逐段翻译后写回 SRT，时间轴与原文严格对齐，适合直接做成字幕

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `-m, --model-dir` | `E:/whisper_model` | 模型目录，需为 faster-whisper 格式（`model.bin` + `config.json`） |
| `--device` | `cuda` | `cuda` / `cpu` |
| `--compute-type` | 自动 | cuda 用 `float16`，cpu 用 `int8` |
| `--language` | 自动检测 | 中文语料**建议显式指定 `zh`**，更准更快 |
| `--beam-size` | `5` | 越大越准越慢 |
| `--no-vad` | 关 | 关闭静音过滤；嘈杂环境可试 |
| `--prompt` | 无 | 引导专有名词识别，如「以下是关于 RAG 与 Agent 框架的课程」 |
| `--summarize` | 关 | 生成中文纪要 |
| `--translate LANG` | 关 | 翻译到指定语言 |
| `--bilingual-srt` | 关 | 与 `--translate` 配合，生成时间轴对齐的双语字幕 |
| `--words` | 关 | 输出词级时间戳 |
| `-o, --out-dir` | `./out` | 输出目录 |

## 实测性能

本机（RTX 单卡 16GB + faster-whisper large-v3 + qwen3:8b）：

| 场景 | 耗时 | 效果 |
|---|---|---|
| 120 秒中文音频 | 9.1 秒 | 13 倍实时 |
| 180 秒中文音频 | 35.5 秒 | 5.1 倍实时 |
| **175 分钟课程视频** | **1632.9 秒（27 分钟）** | **6.4 倍实时，5221 段** |

3 小时课程视频完整产出全部 7 份文件（含 `--bilingual-srt` 的双语字幕），全程离线，总耗时约 35 分钟。

### 注意：GPU 是独占的

large-v3 加载后占 12–16 GB 显存，16 GB 卡基本占满。**同一台机器不能并行跑两个转写进程**，第二个会在模型加载阶段直接崩掉——Windows 下表现为进程无输出退出、不产生 Python 异常、日志为空。

要并行时给其中一个加 `--device cpu`。判断谁占着显存：

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

## 实现要点

这部分是这个项目真正花时间的地方，抄走可以直接省几小时。

### CUDA DLL 必须在 import 之前注入

cTranslate2 依赖 cublas / cudnn，Windows 上不在 PATH 里会在 import 阶段直接报
`OSError: Library cudnn_ops_infer.so.8 not found`。

```python
from transcribe_cli import cuda_setup
cuda_setup.setup_cuda()                     # 先注入 PATH
from faster_whisper import WhisperModel     # 之后才能 import
```

`cublas` 必须排在 `cuda_runtime` 前面，否则可能加载到不匹配的版本。

### 关闭跨段幻觉

`condition_on_previous_text=False`——长音频上 Whisper 容易自我强化、编出重复内容，关掉后文本干净度明显提升。3 小时素材这一项是必须的。

### Ollama 关闭 think 模式

`/api/generate` 的 payload 加 `"think": false`。摘要这类抽取式任务不需要思维链：

| | 耗时 |
|---|---|
| `think=true`（默认） | 7.2s |
| `think=false` | **2.5s** |

约 2.9 倍。另外实测 **qwen3:1.7b 反而比 8b 慢**（14.2s vs 8.3s），小模型不等于快。

### 双语字幕必须逐段翻译

整段翻译会把段落边界合并，时间轴对不上。正确做法是逐段调用 LLM，再按原时间轴写回 SRT。

### 降级而非失败

Ollama 不可用时，`summarize()` / `translate()` 返回 `None`，CLI 打印"跳过"，**转写结果照常输出**。不会因为缺一个可选依赖让整条链路崩掉。

## 已知限制

- 词级时间戳在 VAD 过滤开启时，可能与段落边界有微小偏差
- 专有名词识别依赖 `--prompt`，不指定时专有词可能识别不准
- 摘要长文本会分段处理（每段 12000 字符）再合并，单次塞太多质量会下降
- 只支持单文件，暂不支持批量目录

## 适用场景

- 课程 / 播客 / 访谈录音 → 字幕 + 纪要
- 影视 / 短视频 → 双语字幕
- 会议录音 → 概要与待办（素材不出本机，合规友好）
- 批量整理素材库（配 shell 脚本循环）

## 许可

MIT
