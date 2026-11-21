# 部署指南

三种部署方式，按你的场景选：

| 方式 | 适合 | 耗时 | 产物 |
|---|---|---|---|
| [A. 直接跑源码](#a-直接跑源码推荐) | 开发、试用、贡献者 | 3 分钟 | 无 |
| [B. 打包单文件 exe](#b-打包单文件-exe) | 分发给不装 Python 的用户 | 10–20 分钟 | `transcribe-cli.exe` |
| [C. 做成 Web 服务](#c-做成-web-服务) | 多人共享、接入自动化流程 | 30 分钟 | FastAPI 服务 |

---

## A. 直接跑源码（推荐）

### 1. 前置条件

| 组件 | 要求 | 检查命令 |
|---|---|---|
| Python | 3.11+ | `python --version` |
| Ollama | 已安装并运行 | `ollama list` |
| Whisper 模型 | large-v3 或任意 faster-whisper 格式 | 见下方「获取模型」 |
| GPU（可选） | NVIDIA + CUDA 运行库 | `nvidia-smi` |
| ffmpeg（可选） | 没有会自动回退到 PyAV 解码 | `ffmpeg -version` |

### 2. 安装

```bash
git clone https://github.com/tanghui2/transcribe-cli.git
cd transcribe-cli
python -m venv .venv
```

**Windows（PowerShell）**
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 获取模型

模型约 3GB，**不入库**（仓库已用 `.gitignore` 排除）。

```bash
# 用自带的下载脚本（推荐）
pip install huggingface_hub
python get_model.py                          # 下载 large-v3 到 E:/whisper_model
python get_model.py --model medium           # 体积更小
python get_model.py --dir D:/models/large-v3 # 指定目录
```

脚本会自动跳过已下载完整的目录，不会重复拉 3GB。

下载后目录结构应为：
```
faster-whisper-large-v3/
├── config.json
├── model.bin
├── tokenizer.json
├── vocabulary.json
└── preprocessor_config.json
```

**手动下载**：从 Hugging Face 仓库 `Systran/faster-whisper-large-v3` 下载全部文件到同一目录。
可选模型还有 `large-v3-turbo`（约 1.6GB，速度快精度略降）、`medium`（约 1.5GB）、`small`（约 500MB）。

> 必须是 `model.bin` + `config.json` 这种格式。OpenAI 原始 Whisper 的 `.pt` 权重不能直接用，需先用 `ct2-transformers-converter` 转换。

### 4. 配置 Ollama

```bash
ollama pull qwen3:8b
```

不装 Ollama 也能用——转写功能不依赖它，只是摘要和翻译会被跳过。

### 5. 验证

```bash
python -m transcribe_cli --check -v
```

预期输出：
```
=== 运行时 ===
  ctranslate2: 4.8.3
  cuda_device_count: 1
  supported_compute_types: ['auto', 'int8', 'float16', 'float32']
  ollama 可用: True
  faster_whisper: 1.2.1
```

如果 `cuda_device_count: 0`，说明没识别到 GPU。检查 CUDA DLL 是否在 `E:/cuda_dlls`（可用 `TRANSCRIBE_CUDA_DIR` 覆盖），或加 `--device cpu` 用 CPU 跑。

### 6. 使用

```bash
# 默认模型路径是 E:/whisper_model，Linux/macOS 或其他位置要显式指定
python -m transcribe_cli -m ~/models/faster-whisper-large-v3 音频.mp4

# 完整流程
python -m transcribe_cli -m ~/models/faster-whisper-large-v3 视频.mp4 \
  --language zh --summarize --translate English --bilingual-srt
```

### 常见问题

**`OSError: Library cudnn_ops_infer.so.8 not found`**
CUDA DLL 不在 PATH。Windows 下把 `E:/cuda_dlls/nvidia/{cublas,cuda_runtime,cuda_nvrtc}/bin` 加进系统 PATH，或设 `TRANSCRIBE_CUDA_DIR` 环境变量指向含 `nvidia/` 子目录的根路径。

**进程无输出直接退出，exit code 1**
显存不足。`nvidia-smi --query-gpu=memory.used,memory.total --format=csv` 确认占用，给其中一个任务加 `--device cpu`。

**转写内容重复啰嗦**
Whisper 的跨段幻觉。用 `--prompt` 引导，或检查是否漏了 `condition_on_previous_text=False`（本项目已默认关闭）。

**中文专有名词识别不准**
加 `--prompt "以下是关于 RAG 与 Agent 框架的课程"` 这类提示词。

**摘要特别慢或内容不全**
摘要长文本会按 12000 字符分段，超过 12 万字符时每段都要调一次 LLM，属正常耗时。

---

## B. 打包单文件 exe

给不装 Python 的用户分发。

```bash
pip install pyinstaller
python build_exe.py
```

产物：`dist/transcribe-cli.exe`（**实测 93.6 MB**，不含模型）

```bash
dist\transcribe-cli.exe --check -v
dist\transcribe-cli.exe -m D:\models\faster-whisper-large-v3 视频.mp4 --summarize
```

**已实测验证**：打包后的 exe 能正常加载 CUDA、识别 GPU、真实转写通过
（90 秒音频 5.1 秒完成，17.6 倍实时），产出 SRT/VTT/TXT/JSON 四份文件。

### 打包脚本里的关键决策

| 决策 | 原因 |
|---|---|
| 入口用 `exe_entry.py` 而非 `__main__.py` | 直接打包 `__main__.py` 会因包内相对导入（`from . import core`）在单文件模式下失效，报 `ImportError: attempted relative import with no known parent package` |
| 模型**不内置**（3GB） | 打包体积必须可控；模型由用户用 `-m` 指定 |
| `--collect-all ctranslate2` | 其 DLL 由 onnxruntime 动态加载，PyInstaller 静态分析抓不到 |
| `--collect-all av` | PyAV 的编解码插件需要显式带上 |
| `--exclude-module torch/transformers` | faster-whisper 用不到，打进去 exe 会膨胀数 GB |

> 重新打包时如果旧的 `dist/` `build/` 目录阻塞，直接重跑即可——`--noconfirm` 会覆盖。
> 若需清理，Windows 上可能遇到回收站不可用导致删除失败，这是环境限制不是脚本问题。

### 分发建议

只发 exe 不够，用户还需要模型。建议二选一：

1. **启动时引导下载**——加一段首次运行检测，模型缺失时打印 `python get_model.py` 提示
2. **随包附下载脚本**——把 `get_model.py` 一起分发，用户一条命令搞定

---

## C. 做成 Web 服务

多人共享或接入自动化流水线时用。

### 1. 加一层 FastAPI 包装

```python
# -*- coding: utf-8 -*-
"""转写服务：上传媒体 → 后台转写 → 下载产物。"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from transcribe_cli import core

app = FastAPI(title="transcribe-cli service")
WORK = Path("./workspace")
WORK.mkdir(exist_ok=True)

MODEL_DIR = r"E:/whisper_model"


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = None,
    summarize: bool = False,
):
    """上传媒体，转写并返回任务 id。"""
    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    src = job_dir / (file.filename or "input")
    with src.open("wb") as f:
        f.write(await file.read())

    transcript = core.transcribe(
        src, model_dir=MODEL_DIR, language=language, device="cuda"
    )

    core.write_srt(transcript, job_dir / "out.srt")
    core.write_vtt(transcript, job_dir / "out.vtt")
    (job_dir / "out.txt").write_text(transcript.text, encoding="utf-8")

    if summarize:
        s = core.summarize(transcript)
        if s:
            (job_dir / "summary.md").write_text(s, encoding="utf-8")

    return {"job_id": job_id, "segments": len(transcript.segments)}


@app.get("/result/{job_id}/{filename}")
async def result(job_id: str, filename: str):
    """按 job_id 下载产物。"""
    path = (WORK / job_id / filename).resolve()
    if not str(path).startswith(str(WORK.resolve())) or not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path)
```

### 2. 启动

```bash
pip install fastapi uvicorn python-multipart
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
```

> **必须 `--workers 1`**。faster-whisper 加载后占 12–16 GB 显存，多 worker 会直接 OOM。
> 这是本项目实测踩过的坑，不是保守建议。

### 3. 调用

```bash
curl -X POST "http://localhost:8000/transcribe?language=zh&summarize=true" \
  -F "file=@课程录像.mp4"

curl -OJ "http://localhost:8000/result/<job_id>/out.srt"
```

### 4. 生产注意事项

| 项 | 说明 |
|---|---|
| workers 必须为 1 | 显存独占 |
| 上传大小限制 | 长视频 + 多人并发会撑爆磁盘，需设配额与清理策略 |
| 任务队列 | 同步处理会阻塞，长任务建议丢 Celery / RQ |
| 磁盘清理 | 转写产物应及时清理，JSON 产物体积约为音频的 2–3 倍 |
| 认证 | 公开部署必须加鉴权，否则等于送算力 |
| 容器化 | CUDA 容器用 `nvidia/cuda:12.x` 基础镜像，`--gpus all` 启动 |

### 5. Docker（可选）

```dockerfile
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

WORKDIR /app
RUN apt-get update && apt-get install -y python3 python3-pip ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

启动：
```bash
docker build -t transcribe-cli .
docker run --gpus all -p 8000:8000 -v /path/to/models:/models transcribe-cli
```

---

## 持续集成

`.github/workflows/ci.yml` 已包含：

- **多版本测试**：Python 3.11 / 3.12 / 3.13
- **语法检查**：`python -m compileall -q transcribe_cli`
- **单元测试**：18 个用例，用标准库 `unittest`（零额外依赖），跑 `python -m unittest discover tests -v`
- **CLI 可用性检查**：`--help` 与参数解析断言
- **打包验证**：Linux 单文件产物上传为 artifact

> CI 里**不跑真实转写**：large-v3 模型 3GB，且 CI 无 GPU。真实转写靠本地验证。

单元测试覆盖的是那些容易静默出错的纯逻辑：

| 测试 | 防的是什么 |
|---|---|
| 时间戳转换（含负数夹取、99 小时边界） | 非法时间戳导致播放器不认 |
| SRT 用逗号 / VTT 用点 | 格式写错，字幕不显示 |
| 双语字幕译文缺失 | 段落错位或崩溃 |
| Ollama 不可用返回 `None` | 可选依赖挂了导致整条链路崩 |
| 非法 `--device` 被拒 | 参数校验失效 |
| 无 ffmpeg 时返回 `None` | 缺少可选依赖导致崩溃 |

---

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `TRANSCRIBE_CUDA_DIR` | CUDA DLL 根目录（含 `nvidia/` 子目录） | `E:/cuda_dlls` |
| `OLLAMA_BASE_URL` | Ollama 服务地址 | `http://localhost:11434` |

---

## 依赖清单

```
faster-whisper==1.2.1     # 转写（ctranslate2 后端）
av==16.1.0                # PyAV 音频解码（锁版本）
```

Ollama 通过 HTTP 调用，无需 Python 包。**整个工具链零商业 API 依赖，无 API key。**
