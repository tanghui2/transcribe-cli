// Electron 主进程：窗口管理、Python 后端调度、IPC 桥接
// 设计要点：
// 1. 渲染进程禁用 Node 集成，只通过 preload 暴露白名单 API
// 2. Python 后端以子进程方式调用，stdout 用 NDJSON 逐行回传进度
// 3. 冻结后的路径一律走 resourcesPath，不用 __dirname 拼路径

const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, execFileSync } = require('child_process');

// ---------------------------------------------------------------- 路径解析

// 打包后 Python 后端与资源目录的位置
function resolveBackendDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend');
  }
  // 开发态：指向同级目录的 transcribe-cli
  return path.join(__dirname, '..', 'transcribe-cli');
}

function resolvePythonCommand() {
  if (app.isPackaged) {
    // 打包后后端是 exe
    return path.join(process.resourcesPath, 'backend', 'transcribe-cli.exe');
  }
  // 开发态：优先用托管 venv 的解释器，其次系统 Python
  const candidates = [
    'C:/Users/a/.workbuddy/binaries/python/envs/default/Scripts/python.exe',
    'E:/python3.11/python.exe',
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return 'python';
}

function resolveModelDir() {
  // 模型目录优先级：环境变量 > 已存在的常见位置 > 用户数据目录
  if (process.env.TRANSCRIBE_MODEL_DIR) return process.env.TRANSCRIBE_MODEL_DIR;
  const candidates = [
    'E:/whisper_model',
    path.join(app.getPath('userData'), 'models', 'faster-whisper-large-v3'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return candidates[0];
}

/**
 * 输出目录：主进程是唯一权威。
 *
 * 之前这里有个真实的 bug：outDir 为空时 CLI 用自己的默认 "./out"，
 * 那个路径相对后端 exe 所在目录（打包后是 resources/backend/out），
 * 而渲染层却去 Documents	ranscribe-cli 输出 找文件——
 * 转写其实成功了，界面却显示不出任何结果。
 *
 * 现在统一：主进程永远显式传 --out-dir，渲染层只读这里的值。
 */
function resolveOutputDir() {
  if (process.env.TRANSCRIBE_OUTPUT_DIR) return process.env.TRANSCRIBE_OUTPUT_DIR;
  return path.join(app.getPath('documents'), 'transcribe-cli 输出');
}

/** 模型目录里是否真的有模型文件（区分"路径存在"和"模型就位"） */
function modelReady(dir) {
  try {
    return fs.existsSync(path.join(dir, 'model.bin'))
        && fs.existsSync(path.join(dir, 'config.json'));
  } catch (_) {
    return false;
  }
}

// ---------------------------------------------------------------- 窗口

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1180,
    minHeight: 720,
    frame: false,           // 自定义标题栏，与设计稿一致
    backgroundColor: '#F4F5F7',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());

  // 加载失败时窗口永远不会 show，用户只看到「双击没反应」。
  // 这里把失败原因写进日志文件并弹窗，让问题可诊断。
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    const msg = `界面加载失败：${desc}（${code}）\n${url}`;
    writeStartupLog(`did-fail-load ${code} ${desc} ${url}`);
    if (app.isPackaged) {
      dialog.showErrorBox('transcribe-cli 启动失败', msg);
    }
    console.error('[startup]', msg);
  });

  mainWindow.webContents.on('preload-error', (_e, file, err) => {
    writeStartupLog(`preload-error ${file} ${err && err.message}`);
    console.error('[startup] preload 出错：', file, err);
  });

  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    writeStartupLog(`render-process-gone ${JSON.stringify(details)}`);
  });

  // 外链交给系统浏览器，不在应用内开新窗
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // F5 刷新在生产环境禁用，避免误触丢状态
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.key === 'F5' && app.isPackaged) event.preventDefault();
  });
}

/**
 * 写启动日志。
 *
 * 启动失败时用户唯一的反馈是「双击没反应」，没有任何线索。
 * 这里把关键事件写进 userData 下的 startup.log，便于事后排查。
 */
function writeStartupLog(line) {
  try {
    const logPath = path.join(app.getPath('userData'), 'startup.log');
    fs.appendFileSync(logPath, `${new Date().toISOString()} ${line}\n`, 'utf-8');
  } catch (_) { /* 写不进去就算了 */ }
}

/**
 * GPU 进程起不来时（无 GPU 访问权、驱动异常、虚拟机环境），
 * Electron 默认会 FATAL 直接退出，整个应用打不开。
 *
 * 这里做三件事：
 * 1. 提前关掉 GPU 合成，减少 GPU 进程崩溃的概率
 * 2. 监听 GPU 进程退出：写崩溃标记，并立即用软件渲染重建窗口
 * 3. 下次启动读到标记就直接软件渲染启动
 *
 * 注意：界面用软件渲染只影响观感，转写走的是 CUDA 计算，与此无关。
 */
const GPU_CRASH_MARK = path.join(app.getPath('userData'), 'gpu-crash.flag');

// GPU 进程崩溃几次后放弃硬扛，改用软件渲染
let gpuCrashCount = 0;
let gpuFallbackApplied = false;

function readGpuCrashFlag() {
  try {
    return fs.existsSync(GPU_CRASH_MARK);
  } catch (_) {
    return false;
  }
}

function writeGpuCrashFlag() {
  try {
    fs.writeFileSync(GPU_CRASH_MARK, new Date().toISOString());
  } catch (_) { /* 写不进去就算了 */ }
}

function clearGpuCrashFlag() {
  try {
    if (fs.existsSync(GPU_CRASH_MARK)) fs.unlinkSync(GPU_CRASH_MARK);
  } catch (_) { /* 忽略 */ }
}

/**
 * 强制切换到软件渲染。
 *
 * `--disable-gpu` 这个命令行开关**只在进程启动时生效**，运行时追加无效，
 * 所以真正要切换渲染方式，只能重启进程。
 *
 * 这里用 spawn 启动自己（带 --disable-gpu），而不是 app.relaunch() ——
 * 后者在某些环境（服务、容器、无桌面会话）里不生效，
 * 结果就是父进程退了、新进程没起来，应用等于「双击没反应」。
 *
 * 注意：不能用 `disable-software-rasterizer` —— 它的含义是「禁用软件光栅化」，
 * 加上它等于禁止用软件渲染兜底，结果 GPU 一崩应用照样 FATAL 退出（踩过一次）。
 */
let relaunching = false;

function forceSoftwareRendering() {
  if (gpuFallbackApplied || relaunching) return;
  gpuFallbackApplied = true;
  writeGpuCrashFlag();
  writeStartupLog('GPU 反复崩溃，重启进程并切到软件渲染');

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.destroy();
  }
  mainWindow = null;

  relaunching = true;
  // 延迟一下再重启，让当前崩溃的子进程先收干净
  setTimeout(() => {
    try {
      const child = spawn(process.execPath, ['--disable-gpu'], {
        detached: true,
        stdio: 'ignore',
        cwd: app.getPath('documents'),
      });
      child.unref();
      writeStartupLog('已用 --disable-gpu 启动新进程');
    } catch (e) {
      writeStartupLog(`重启失败: ${e && e.message}`);
    }
    app.exit(0);
  }, 800);
}

/**
 * 探测本机是否有可用的 NVIDIA 显卡。
 *
 * Electron 的 GPU 进程在没有显卡访问权时（虚拟机、远程桌面、驱动异常）
 * 会反复崩溃，最终整个应用 FATAL 退出。
 *
 * 注意：nvidia-smi 能跑通不代表 GPU 进程能用 ——
 * 容器里常见「nvidia-smi 正常但 Chromium GPU 进程起不来」。
 * 所以这里只把 nvidia-smi 当作第一层判断，真正的兜底在 GPU 崩溃处理里。
 *
 * 这个探测与转写的 CUDA 无关 —— 没显卡时转写会退到 CPU，只是慢。
 */
function hasNvidiaGpu() {
  try {
    const r = execFileSync('nvidia-smi', ['--query-gpu=name', '--format=csv,noheader'], {
      encoding: 'utf-8',
      timeout: 5000,
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    return r.trim().length > 0;
  } catch (_) {
    // nvidia-smi 不存在或执行失败 —— 视为没有可用显卡
    return false;
  }
}

// 上次启动崩过 GPU，或本机根本没有显卡 → 一开始就软件渲染启动。
//
// 关键：`--disable-gpu` 必须在进程启动前生效，运行时追加无效。
// 所以这里只能用「启动时判断」，崩溃后的兜底走「自动重启进程」那条路。
if (readGpuCrashFlag()) {
  app.commandLine.appendSwitch('disable-gpu');
  gpuFallbackApplied = true;
  console.log('[gpu] 检测到上次 GPU 崩溃，本次以软件渲染启动');
} else if (!hasNvidiaGpu()) {
  app.commandLine.appendSwitch('disable-gpu');
  gpuFallbackApplied = true;
  console.log('[gpu] 未检测到可用显卡，本次以软件渲染启动');
} else {
  // 有显卡：正常走 GPU 渲染，但关掉 GPU 合成降低崩溃概率
  app.commandLine.appendSwitch('disable-gpu-compositing');
}

app.whenReady().then(() => {
  writeStartupLog('app ready');
  Menu.setApplicationMenu(null);   // 菜单栏隐藏，界面自足
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// GPU 崩溃不再拖垮整个应用：记下标记，并立即用软件渲染重建窗口
app.on('child-process-gone', (event, details) => {
  if (details.type !== 'GPU') return;
  gpuCrashCount += 1;
  console.warn(`[gpu] GPU 进程退出（第 ${gpuCrashCount} 次）`);
  writeStartupLog(`gpu process gone (${gpuCrashCount})`);

  // 第一次崩溃先写标记（下次启动会自动软件渲染）；
  // 反复崩溃说明硬扛没用，立即切软件渲染重建窗口。
  if (gpuCrashCount >= 2) {
    forceSoftwareRendering();
  } else {
    writeGpuCrashFlag();
  }
});

// 窗口正常渲染出来就清掉标志（说明这次 GPU 没问题）
app.on('browser-window-created', (_e, win) => {
  win.webContents.once('did-finish-load', () => {
    setTimeout(() => {
      if (!win.isDestroyed() && win.webContents.isLoading() === false) {
        clearGpuCrashFlag();
      }
    }, 3000);
  });
});

app.on('window-all-closed', () => {
  // 有任务在跑时不允许直接退出，避免留下半个进程
  if (activeJob) {
    dialog.showMessageBoxSync({
      type: 'warning',
      buttons: ['取消退出', '强制退出'],
      defaultId: 0,
      cancelId: 0,
      title: '还有任务在运行',
      message: '转写任务尚未完成，强制退出会丢失当前进度。',
    });
    if (activeJob) return;
  }
  app.quit();
});

// ---------------------------------------------------------------- Python 后端调度

let activeJob = null;   // 当前运行的子进程

/** 调用 Python 后端执行转写，进度以 NDJSON 逐行回传 */
ipcMain.handle('transcribe:run', async (event, payload) => {
  if (activeJob) return { ok: false, error: '已有任务在运行' };

  const {
    files = [],
    language = 'zh',
    modelDir = '',
    summarize = false,
    proofread = false,
    translate = '',
    bilingualSrt = false,
    outDir = '',
    device = 'cuda',
    computeType = 'float16',
    beamSize = 5,
    vad = true,
    words = false,
    prompt = '',
  } = payload || {};

  const py = resolvePythonCommand();
  const model = modelDir || resolveModelDir();

  const args = ['-m', 'transcribe_cli'];
  if (files.length === 1) args.push(files[0]);
  args.push('--language', String(language));
  args.push('--model-dir', model);
  args.push('--device', device);
  args.push('--compute-type', computeType);
  args.push('--beam-size', String(beamSize));
  if (vad === false) args.push('--no-vad');   // CLI 用的是反向开关
  if (words) args.push('--words');
  if (prompt) args.push('--prompt', prompt);
  if (summarize) args.push('--summarize');
  if (proofread) args.push('--proofread');
  if (translate) args.push('--translate', translate);
  if (bilingualSrt) args.push('--bilingual-srt');
  // 输出目录始终显式传入，避免 CLI 落到它自己的默认相对路径
  args.push('--out-dir', outDir || resolveOutputDir());
  args.push('--events');                      // 结构化进度事件，供 GUI 消费

  const cwd = resolveBackendDir();
  const env = { ...process.env };
  // 打包后的 exe 不需要注入 DLL；开发态由 cuda_setup 处理
  if (!app.isPackaged) {
    env.PYTHONPATH = cwd;
    env.PYTHONIOENCODING = 'utf-8';
  }

  const child = spawn(py, args, {
    cwd,
    env,
    windowsHide: true,
  });
  activeJob = child;

  // stdout 逐行解析：后端以 NDJSON 输出进度，混有普通文本
  let buf = '';
  child.stdout.setEncoding('utf-8');
  child.stdout.on('data', (chunk) => {
    buf += chunk;
    const lines = buf.split('\n');
    buf = lines.pop() ?? '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (trimmed.startsWith('{')) {
        try {
          const obj = JSON.parse(trimmed);
          if (obj.__event) {
            event.sender.send('transcribe:progress', obj);
            continue;
          }
        } catch (_) { /* 不是 JSON 就按普通输出处理 */ }
      }
      event.sender.send('transcribe:log', trimmed);
    }
  });

  let stderr = '';
  child.stderr.setEncoding('utf-8');
  child.stderr.on('data', (d) => { stderr += d; });

  const exitCode = await new Promise((resolve) => {
    child.on('error', (e) => {
      event.sender.send('transcribe:log', `启动失败：${e.message}`);
      resolve(-1);
    });
    child.on('close', (code) => resolve(code));
  });

  activeJob = null;

  if (exitCode !== 0) {
    const tail = stderr.split('\n').filter(Boolean).slice(-14).join('\n');
    return { ok: false, error: tail || `进程退出码 ${exitCode}` };
  }
  return { ok: true, stderr };
});

/** 取消当前任务 */
ipcMain.handle('transcribe:cancel', () => {
  if (!activeJob) return { ok: false, error: '没有运行中的任务' };
  // Windows 下需要 kill 整个进程树，否则 Python 子进程会残留
  const { execSync } = require('child_process');
  try {
    execSync(`taskkill /PID ${activeJob.pid} /T /F`, { stdio: 'ignore' });
  } catch (_) {
    activeJob.kill('SIGKILL');
  }
  activeJob = null;
  return { ok: true };
});

/** 环境自检：跑 CLI 的 --check，把输出返回渲染层 */
ipcMain.handle('transcribe:check', async () => {
  const py = resolvePythonCommand();
  const cwd = resolveBackendDir();
  return new Promise((resolve) => {
    const env = { ...process.env };
    if (!app.isPackaged) {
      env.PYTHONPATH = cwd;
      env.PYTHONIOENCODING = 'utf-8';
    }
    const child = spawn(py, ['-m', 'transcribe_cli', '--check', '-v'], {
      cwd, env, windowsHide: true,
    });
    let out = '';
    child.stdout.setEncoding('utf-8');
    child.stderr.setEncoding('utf-8');
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { out += d; });
    child.on('close', () => resolve({ ok: true, output: out }));
    child.on('error', (e) => resolve({ ok: false, output: e.message }));
  });
});

/** GPU 显存占用检测：避免第二个进程静默崩溃 */
ipcMain.handle('transcribe:gpu', async () => {
  const { execFile } = require('child_process');
  return new Promise((resolve) => {
    execFile('nvidia-smi', [
      '--query-gpu=memory.used,memory.total',
      '--format=csv,noheader,nounits',
    ], { windowsHide: true }, (err, stdout) => {
      if (err) { resolve({ ok: false, message: '未检测到 NVIDIA 显卡' }); return; }
      const line = (stdout || '').trim().split('\n')[0];
      if (!line) { resolve({ ok: false, message: '无法读取显存信息' }); return; }
      // nounits 输出的单位是 MiB，不是 GB
      const [usedMiB, totalMiB] = line.split(',').map((x) => parseInt(x.trim(), 10));
      resolve({
        ok: true,
        usedMiB, totalMiB,
        used: usedMiB / 1024,
        total: totalMiB / 1024,
        free: (totalMiB - usedMiB) / 1024,
        ratio: totalMiB ? usedMiB / totalMiB : 0,
      });
    });
  });
});

/** 选择文件对话框 */
ipcMain.handle('dialog:openFiles', async () => {
  const res = await dialog.showOpenDialog(mainWindow, {
    title: '选择要转写的音视频文件',
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: '音视频', extensions: ['mp4', 'mov', 'mkv', 'avi', 'mp3', 'wav', 'm4a', 'flac', 'ogg', 'aac'] },
      { name: '全部文件', extensions: ['*'] },
    ],
  });
  if (res.canceled) return [];
  return res.filePaths;
});

/** 选择输出目录 */
ipcMain.handle('dialog:openDir', async () => {
  const res = await dialog.showOpenDialog(mainWindow, {
    title: '选择输出目录',
    properties: ['openDirectory', 'createDirectory'],
  });
  if (res.canceled) return '';
  return res.filePaths[0] || '';
});

/** 打开目录 */
ipcMain.handle('shell:openDir', async (_e, dir) => {
  if (dir && fs.existsSync(dir)) shell.openPath(dir);
});

/** 打开文件 */
ipcMain.handle('shell:openFile', async (_e, file) => {
  if (file && fs.existsSync(file)) shell.openPath(file);
});

/** 获取路径信息 */
ipcMain.handle('app:paths', () => {
  const modelDir = resolveModelDir();
  return {
    modelDir,
    modelReady: modelReady(modelDir),
    backendDir: resolveBackendDir(),
    backendExe: app.isPackaged
      ? path.join(process.resourcesPath, 'backend', 'transcribe-cli.exe')
      : resolvePythonCommand(),
    python: resolvePythonCommand(),
    isPackaged: app.isPackaged,
    version: app.getVersion(),
    outputDir: resolveOutputDir(),
    outputs: resolveOutputDir(),
    exePath: app.getPath('exe'),
  };
});

/** 读取输出文件：渲染层需要读回转写结果填充视图 */
ipcMain.handle('fs:stat', (_e, p) => {
  try {
    const st = fs.statSync(p);
    return { ok: true, size: st.size, mtime: st.mtimeMs };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
});

ipcMain.handle('fs:read', (_e, p, enc) => {
  try {
    return { ok: true, data: fs.readFileSync(p, enc || 'utf-8') };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
});

ipcMain.handle('fs:exists', (_e, p) => fs.existsSync(p));

// 窗口控制（自定义标题栏用）
ipcMain.on('win:minimize', () => mainWindow?.minimize());
ipcMain.on('win:maximize', () => {
  if (!mainWindow) return;
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});
ipcMain.on('win:close', () => mainWindow?.close());
ipcMain.handle('win:isMaximized', () => mainWindow?.isMaximized() ?? false);
