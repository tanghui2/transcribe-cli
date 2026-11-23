# transcribe-gui · 桌面版

`transcribe-cli` 的图形界面版本。Electron 外壳 + Python 后端，全程离线，零 API 成本。

界面沿用「状态带驱动」方案：顶部一条贯穿全宽的深色横带，既是阶段导航也是进度条，
下方左右分栏（左：文件队列与参数；右：转写 / 摘要 / 双语字幕 / 导出 / 设置五个视图）。

---

## 快速开始（开发态）

```bash
cd transcribe-gui
npm install
npm start
```

需要环境：

- Node.js 18+（本机用 22.22.2）
- Python 后端可被找到：`../transcribe-cli/` 目录存在且能 `import transcribe_cli`
- 模型目录：默认 `E:/whisper_model`，也可用环境变量 `TRANSCRIBE_MODEL_DIR` 覆盖

## 打包

```bash
# 先构建 Python 后端 exe
cd ../transcribe-cli && python build_exe.py

# 再打 Electron 包
cd ../transcribe-gui && npm run dist
```

产物：`dist/transcribe-cli-0.1.0-windows-x64.exe`（portable 单文件，双击即用）

后端 exe 会被放到 `resources/backend/transcribe-cli.exe`，主进程按 `process.resourcesPath` 定位，
不依赖 `__dirname` 拼路径，冻结后不会找错位置。

---

## 架构

```
transcribe-gui/
├── main.js          主进程：窗口、IPC、Python 子进程调度、显存检测
├── preload.js       白名单 API 桥接（contextIsolation: true）
└── renderer/
    ├── index.html   界面结构与样式
    └── renderer.js  状态机、队列、进度联动、波形渲染
```

**安全模型**：渲染进程禁用 Node 集成，`contextIsolation: true`，
所有能力通过 preload 的白名单方法走 IPC 到主进程执行。

### 为什么文件读取走 IPC

Electron 32 起，即使 `sandbox: false`，preload 也无法 `require('fs')`。
因此读文件统一走 `fs:stat` / `fs:read` / `fs:exists` 三个 IPC 端点，由主进程代劳。

---

## 进度事件协议

CLI 加了 `--events` 开关：开启后以 NDJSON 向 stdout 输出结构化事件，普通文本不受影响。

```json
{"__event": true, "event": "stage", "stage": "asr", "state": "now", "detail": "cuda · float16"}
{"__event": true, "event": "segments", "pct": 39.15, "speed": 16.35, "new": {...}, "count": 1}
{"__event": true, "event": "summary", "data": {...}}
{"__event": true, "event": "bilingual", "data": [...]}
{"__event": true, "event": "exports", "data": [...]}
```

`segments` 事件只推**新增**的那一段（字段 `new`），不重发整个数组——
否则 5221 段会产生 O(n²) 的数据传输量。

主进程逐行解析 stdout，靠 `__event` 字段区分「结构化事件」与「普通日志」。

---

## 三个必须知道的技术约束

### 1. 显存独占：第二个实例会静默崩溃

`large-v3` 占用 12–16 GB 显存。同时启动两个转写进程时，第二个会在模型加载阶段直接退出，
**不产生 Python 异常、日志文件 0 字节、exit=1**。

因此 GUI 在开始转写前会调 `nvidia-smi` 检查显存占用，
剩余不足 4 GB 时弹确认框告知用户，而不是让进程悄无声息地消失。

### 2. 多任务实际是串行的

显存装不下两个 `large-v3`，所以队列里的文件是依次处理的，不是并行。
界面上「排队」状态是准确的，用户不会误以为能并行。

### 3. 长任务需要能中途取消

取消用 `taskkill /PID <pid> /T /F` 杀整个进程树——只杀父进程的话，
Python 子进程会残留并继续占用显存。

---

## 环境变量

| 变量 | 作用 |
|---|---|
| `TRANSCRIBE_MODEL_DIR` | 指定模型目录，覆盖默认的 `E:/whisper_model` |

## 已知限制

- 只打包了 Windows x64
- 模型（3 GB）不内置，需用户自行下载
- Ollama 不可用时摘要与翻译会自动跳过，转写本身不受影响
