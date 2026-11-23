# transcribe-cli

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/tanghui2/transcribe-cli)](https://github.com/tanghui2/transcribe-cli/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](DEPLOY.md)

**本地音视频转写 · 摘要 · 翻译 · 字幕生成**

全程离线运行，零 API 成本，素材不离开你的设备。

```bash
python -m transcribe_cli lecture.mp4 --language zh --summarize --translate English
```

> 175 分钟中文课程视频 → **27 分钟**跑完全流程（6.4 倍实时），产出 SRT / VTT / TXT / JSON / 中文纪要 / 英文译文 6 份文件；加 `--bilingual-srt` 则第 7 份（时间轴对齐的双语字幕）一并产出。

Windows 用户可直接下载 [Releases](https://github.com/tanghui2/transcribe-cli/releases) 里的单文件 exe（93 MB），无需安装 Python。模型需另行下载（约 3 GB）。

> **桌面版**：不想碰命令行的话，`desktop/` 目录里有完整的 Electron 图形界面 —— 拖文件进去、实时看进度、五个视图切换（转写 / 摘要 / 双语字幕 / 导出 / 设置）。详见[桌面版](#桌面版electron)。

---

## 目录

- [为什么做这个](#为什么做这个)
- [特性](#特性)
- [安装](#安装)
- [桌面版（Electron）](#桌面版electron)
- [使用](#使用)
- [输出](#输出)
- [命令行参数](#命令行参数)
- [性能](#性能)
- [工作原理](#工作原理)
- [限制](#限制)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

---

## 为什么做这个

字幕与转写工具的现状是两头不讨好：

| | 云端方案 | 现状 | 本项目 |
|---|---|---|---|
| 成本 | 按分钟计费，用得越多越贵 | Happy Scribe €17/月起、Maestra $39/月起 | **0** |
| 隐私 | 素材必须上传 | 课程、客户会议、未发布内容都得传出去 | **不出一台机器** |
| 可用性 | 依赖网络 | 内网、涉密环境、飞机上直接歇业 | **照常工作** |
| 长期形态 | 越用越贵，越做越像生意 | 规模越大越被推理成本拖住 | **做完即用** |

开源侧的工具（Subtitle Edit）功能其实很全，缺的是**开箱即用的工作流**——用户要自己配引擎、调参数、拼命令。

本项目把「本地推理」包装成一条开箱即用的命令行流水线。

## 特性

| 能力 | 说明 |
|---|---|
| **转写** | faster-whisper large-v3 + CUDA，VAD 静音过滤，词级时间戳可选 |
| **摘要** | 本地 LLM 产出「概要 / 要点 / 待办」三段式中文纪要 |
| **翻译** | 整段通顺译文，或逐段对齐的双语字幕 |
| **字幕导出** | SRT（标准）、VTT（网页）、TXT、JSON（含词级时间戳） |
| **双语字幕** | 原文在上译文在下，时间轴与原文**严格对齐**，可直接用于压制 |
| **环境自检** | `--check` 一键确认 CUDA、模型、Ollama 是否就绪 |
| **零依赖成本** | 全部推理在本机完成，不需要 API key，不产生调用费用 |

## 安装

### 方式一：从源码运行

```bash
git clone https://github.com/tanghui2/transcribe-cli.git
cd transcribe-cli

# Windows
python -m venv .venv && .\.venv\Scripts\activate

# macOS / Linux
python -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt
```

### 方式二：下载单文件 exe（Windows）

从 [Releases](https://github.com/tanghui2/transcribe-cli/releases) 下载 `transcribe-cli-windows-x64.exe`，无需安装 Python。

### 下载模型

模型约 3 GB，**不含在仓库与 Release 中**，需自行下载：

```bash
pip install huggingface_hub
python get_model.py                          # large-v3（约 3 GB）
python get_model.py --model large-v3-turbo   # 约 1.6 GB，速度更快
python get_model.py --model medium           # 约 1.5 GB
python get_model.py --model small            # 约 500 MB
```

### 配置 Ollama（可选）

摘要与翻译需要本地 LLM：

```bash
ollama pull qwen3:8b
```

不装 Ollama 也能用——转写功能不依赖它，只是摘要和翻译会被跳过。

### 验证

```bash
python -m transcribe_cli --check -v
```

```
=== 运行时 ===
  ctranslate2: 4.8.3
  cuda_device_count: 1
  ollama 可用: True
  faster_whisper: 1.2.1
```

更多部署形态（Docker、Web 服务、CI）见 **[DEPLOY.md](DEPLOY.md)**。

## 桌面版（Electron）

`desktop/` 是一个完整的桌面图形界面，Electron 外壳 + 同一个 Python 后端，功能与命令行版一致。

界面采用「状态带驱动」布局：顶部一条贯穿全宽的深色横带，既是五阶段导航（解码 → 识别 → 纪要 → 翻译 → 导出）也是整体进度条；下方左右分栏，左边是文件队列与参数，右边是五个结果视图。

### 下载运行

去 [Releases](https://github.com/tanghui2/transcribe-cli/releases) 下载 `transcribe-cli-0.1.0-windows-x64.exe`（169 MB，单文件 portable 版），双击即用。

### 从源码构建

```bash
# 1. 先构建 Python 后端 exe
python build_exe.py

# 2. 再打桌面版
cd desktop && npm install && npm run dist
```

产物在 `desktop/dist/`。

### 三个必须知道的技术约束

**显存独占，第二个实例会静默崩溃。**
`large-v3` 占用 12–16 GB 显存。同时启动两个转写进程时，第二个会在模型加载阶段**直接退出**——不产生 Python 异常、日志文件 0 字节、exit=1。
桌面版在开始转写前会检查显存占用，剩余不足 4 GB 时弹窗告知，而不是让进程悄无声息地消失。

**多任务实际是串行的。**
显存装不下两个 `large-v3`，所以队列里的文件是依次处理的，不是并行。界面上的「排队」状态是准确的。

**取消任务要杀整个进程树。**
只杀父进程的话 Python 子进程会残留并继续占用显存，所以用的是 `taskkill /PID <pid> /T /F`。

### 结构化进度事件（CLI 侧的改动）

桌面版需要知道「转写到第几段、百分比、实时倍率」，所以 CLI 加了 `--events` 开关：

```bash
python -m transcribe_cli lecture.mp4 --events
```

```json
{"__event": true, "event": "stage", "stage": "asr", "state": "now", "detail": "cuda · float16"}
{"__event": true, "event": "segments", "pct": 39.15, "speed": 16.35, "new": {...}, "count": 1}
{"__event": true, "event": "summary", "data": {...}}
{"__event": true, "event": "bilingual", "data": [...]}
{"__event": true, "event": "exports", "data": [...]}
```

事件类型：`stage`（阶段切换）、`segments`（增量段落）、`summary`、`bilingual`、`exports`。

> `segments` 事件**只推新增的那一段**（字段 `new`），不重发整个数组 —— 否则 5221 段会产生 O(n²) 的数据传输量。

### 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows x64（macOS / Linux 未打包） |
| 显卡 | NVIDIA GPU + CUDA（无 GPU 时自动降级到 CPU，速度约为十分之一） |
| 模型 | faster-whisper 模型，需自行下载（large-v3 约 3 GB） |
| 摘要 / 翻译 | 需要本地 [Ollama](https://ollama.com/) 运行 `qwen3:8b`（可选，缺失时跳过这两步） |

模型目录优先级：`TRANSCRIBE_MODEL_DIR` 环境变量 → `E:/whisper_model` → 用户数据目录。

### 开发踩坑记录

给后来者省点时间，这几条都是实测踩到的：

- **Electron 32+ 起，即使 `sandbox: false`，preload 也无法 `require('fs')`**。文件读取必须走 IPC 到主进程执行。
- **`ELECTRON_RUN_AS_NODE=1` 会让 Electron 二进制永远不下载**（npm 装完但 `dist/` 是空的）。启动前必须 `env -u ELECTRON_RUN_AS_NODE`。
- **`NODE_OPTIONS` 里带 `--use-system-ca` 会让 Electron 拒绝启动**。同样要 `env -u NODE_OPTIONS`。
- **依赖完整性不要自己写检测脚本**。现代 npm 包不写 `main` 字段，只用 ESM 的 `exports`，自己拼路径判断会连环误判。让实际消费者报错，或用 Node 的 `require.resolve`。
- **CTranslate2 的 `TranscriptionInfo` 没有 `elapsed` 字段**。在回调里直接访问会抛 `AttributeError` 且被静默吞掉，表现为「进度事件一条都不发」。耗时要在自己的函数里算好再传。

---

## 使用

```bash
# 只转写
python -m transcribe_cli lecture.mp4

# 指定语种（中文语料建议显式指定，更准更快）
python -m transcribe_cli lecture.mp4 --language zh

# 加中文纪要
python -m transcribe_cli lecture.mp4 --language zh --summarize

# 翻译成英文
python -m transcribe_cli lecture.mp4 --translate English

# 生成时间轴对齐的双语字幕
python -m transcribe_cli lecture.mp4 --translate English --bilingual-srt

# 词级时间戳
python -m transcribe_cli lecture.mp4 --words
```

用 `--prompt` 引导专有名词识别：

```bash
python -m transcribe_cli lecture.mp4 --language zh \
  --prompt "以下是关于 RAG 与 Agent 框架的课程"
```

## 输出

```
out/
├── lecture.srt              # 标准字幕，直接挂播放器
├── lecture.vtt              # WebVTT，网页播放器用
├── lecture.txt              # 纯文本
├── lecture.json             # 结构化，含词级时间戳
├── lecture.summary.md       # 中文纪要（概要 / 要点 / 待办）
├── lecture.English.md       # 整段通顺译文（适合阅读）
└── lecture.English.srt      # 双语字幕，时间轴严格对齐（适合直接压制）
```

翻译有两种模式，按用途选：

- **默认**：整段翻译成连续文本，读起来自然，适合快速浏览
- **`--bilingual-srt`**：逐段翻译后写回 SRT，时间轴与原文严格对齐，适合直接做成字幕

## 命令行参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `media` | 必填 | 音视频文件路径 |
| `-m, --model-dir` | `E:/whisper_model` | 模型目录，需为 faster-whisper 格式（`model.bin` + `config.json`） |
| `--device` | `cuda` | `cuda` / `cpu` |
| `--compute-type` | 自动 | cuda 用 `float16`，cpu 用 `int8` |
| `--language` | 自动检测 | `zh` / `en` / …，中文语料建议显式指定 |
| `--beam-size` | `5` | 越大越准越慢 |
| `--no-vad` | 关 | 关闭静音过滤；嘈杂环境可试 |
| `--prompt` | 无 | 引导专有名词识别 |
| `--summarize` | 关 | 生成中文纪要 |
| `--translate LANG` | 关 | 翻译到指定语言 |
| `--bilingual-srt` | 关 | 与 `--translate` 配合，生成时间轴对齐的双语字幕 |
| `--words` | 关 | 输出词级时间戳 |
| `-o, --out-dir` | `./out` | 输出目录 |
| `--stem` | 取输入文件名 | 输出文件名主干 |
| `--check` | — | 只做环境自检并退出 |
| `-v, --verbose` | 关 | 打印调试信息 |

## 性能

本机实测（单卡 16 GB + faster-whisper large-v3 + qwen3:8b）：

| 场景 | 耗时 | 倍率 |
|---|---|---|
| 120 秒中文音频 | 9.1 秒 | 13x 实时 |
| 180 秒中文音频 | 35.5 秒 | 5.1x 实时 |
| **175 分钟课程视频** | **1632.9 秒（27 分钟）** | **6.4x 实时，5221 段** |

3 小时课程视频完整产出全部 7 份文件（含双语字幕），全程离线，总耗时约 35 分钟。

> ⚠️ **GPU 是独占的。** large-v3 加载后占 12–16 GB 显存，16 GB 卡基本占满。同一台机器不能并行跑两个转写进程，第二个会在模型加载阶段直接崩掉（Windows 下表现为进程无输出退出、不产生 Python 异常、日志为空）。要并行时给其中一个加 `--device cpu`。
>
> ```bash
> nvidia-smi --query-gpu=memory.used,memory.total --format=csv
> ```

## 工作原理

这部分是真正花时间的地方，抄走可以直接省几小时。

### 架构

```
媒体文件
   │
   ├─ ffmpeg 抽音频 ────► 16kHz 单声道 wav
   │                        （无 ffmpeg 时回退 PyAV 解码）
   ▼
faster-whisper large-v3 ──► 段落 + 词级时间戳
   │                        （ctranslate2 + CUDA）
   ├──────────────┬──────────────┬─────────────┐
   ▼              ▼              ▼             ▼
  SRT            VTT           TXT           JSON
                                    │
                                    ├─► Ollama 摘要（概要/要点/待办）
                                    └─► Ollama 翻译（整段 / 逐段对齐）
```

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

Whisper 默认 `condition_on_previous_text=True`，在长音频上会自我强化、编出重复内容。3 小时素材这一项是必须的。

### 关闭 think 模式

Ollama 的 `think` 默认开启思维链，对摘要这类抽取式任务是纯浪费：

| | 耗时 |
|---|---|
| `think=true`（默认） | 7.2s |
| `think=false` | **2.5s** |

约 2.9 倍。另外实测 **qwen3:1.7b 反而比 8b 慢**（14.2s vs 8.3s），小模型不等于快。

### 双语字幕必须逐段翻译

整段翻译会把段落边界合并，时间轴对不上。正确做法是逐段调用 LLM，再按原时间轴写回 SRT。

### 降级而非失败

Ollama 不可用时，`summarize()` / `translate()` 返回 `None`，CLI 打印「跳过」，**转写结果照常输出**。不会因为缺一个可选依赖让整条链路崩掉。

### 项目结构

```
transcribe_cli/
├── cuda_setup.py    # CUDA DLL 注入 + 运行时自检
├── media.py         # ffmpeg 抽音频 / ffprobe 探测 / PyAV 回退
├── core.py          # 转写 + 字幕导出 + 摘要/翻译/双语 SRT
└── __main__.py      # CLI 入口与参数
```

839 行，零第三方依赖（除 faster-whisper / av），只依赖标准库 + 本地 Ollama HTTP 接口。

## 限制

- 词级时间戳在 VAD 过滤开启时，可能与段落边界有微小偏差
- 专有名词识别依赖 `--prompt`，不指定时专有词可能识别不准
- 摘要长文本会分段处理（每段 12000 字符）再合并，单次塞太多质量会下降
- 只支持单文件，暂不支持批量目录
- 需要 NVIDIA GPU 才有理想速度；CPU 模式可用但慢

## 参与贡献

欢迎提 issue 和 PR。

```bash
# 本地跑测试
python -m unittest discover tests -v

# 打包单文件 exe
pip install pyinstaller
python build_exe.py
```

提交前请确认：

- 注释与文档使用中文，UTF-8 编码
- 新增功能同步更新 README 与 `DEPLOY.md`
- 不引入需要商业 API key 的依赖

## 许可证

[MIT](LICENSE)

第三方组件遵循各自许可：faster-whisper（MIT）、CTranslate2（MIT）、PyAV（BSD-3）、tokenizers（Apache-2.0）。
本项目不分发任何模型权重，模型需自行下载并遵循其仓库许可。
