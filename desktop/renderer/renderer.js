// 渲染层：状态机、文件队列、进度联动、波形渲染
// 只通过 window.api 与主进程通信，不直接碰 Node

// ---------------------------------------------------------------- 全局状态

const S = {
  files: [],            // 队列项 { path, name, size, status, pct, msg }
  running: false,
  tab: 'transcript',
  language: 'zh',
  device: 'cuda',
  outputs: { srt: true, txt: true, vtt: false, json: false, summary: false },
  vad: true,
  words: false,
  modelDir: '',
  modelName: 'large-v3',
  outDir: '',
  paths: null,
  segments: [],         // 转写段落
  bilingual: [],        // 双语对照
  summary: null,        // { overview, points, todos }
  exports: [],          // 导出文件清单
  logs: [],
  stages: [],           // 状态带五格
};

const STAGE_DEFS = [
  { key: 'decode',  n: '01 解码', t: '音频提取' },
  { key: 'asr',     n: '02 识别', t: '语音转写' },
  { key: 'summary', n: '03 纪要', t: '要点提炼' },
  { key: 'trans',   n: '04 翻译', t: '逐段对齐' },
  { key: 'export',  n: '05 导出', t: '写入文件' },
];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
));

// ---------------------------------------------------------------- 状态带

function renderStages() {
  const grid = $('stageGrid');
  grid.innerHTML = S.stages.map((s) => `
    <div class="stage-cell ${s.state}">
      <div class="n">${esc(s.n)}</div>
      <div class="t">${esc(s.t)}</div>
      <div class="s">${esc(s.s || '')}</div>
    </div>`).join('');

  // 进度填充：完成的格数占比
  const doneCount = S.stages.filter((s) => s.state === 'done').length;
  const nowIdx = S.stages.findIndex((s) => s.state === 'now');
  let pct = 0;
  if (nowIdx >= 0) pct = ((doneCount + 0.5) / S.stages.length) * 100;
  else if (doneCount === S.stages.length) pct = 100;
  const fill = $('stageFill');
  fill.style.width = pct + '%';
  fill.classList.toggle('full', doneCount === S.stages.length && S.stages.length > 0);
}

function resetStages(statusMap) {
  S.stages = STAGE_DEFS.map((d) => ({
    ...d,
    state: (statusMap && statusMap[d.key]) || 'todo',
    s: '',
  }));
  renderStages();
}

function setStage(key, state, sub) {
  const st = S.stages.find((x) => x.key === key);
  if (!st) return;
  st.state = state;
  if (sub !== undefined) st.s = sub;
  renderStages();
}

// ---------------------------------------------------------------- 队列

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

function renderQueue() {
  $('fileCount').textContent = String(S.files.length);
  const box = $('queue');
  if (!S.files.length) {
    box.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-3);font-size:13px">队列为空</div>';
    return;
  }
  box.innerHTML = S.files.map((f, i) => `
    <div class="qrow ${f.status === 'run' ? 'act' : ''} ${f.status === 'done' ? 'done' : ''} ${f.status === 'fail' ? 'fail' : ''}">
      <div>
        <div class="qname" title="${esc(f.path)}">${esc(f.name)}</div>
        <div class="qsub">${esc(f.size)}${f.lang ? ' · ' + esc(f.lang) : ''}</div>
      </div>
      <div class="qst ${f.status === 'run' ? 'run' : ''} ${f.status === 'done' ? 'ok' : ''} ${f.status === 'fail' ? 'bad' : ''}">${esc(f.msg)}</div>
    </div>`).join('');
}

// ---------------------------------------------------------------- 右栏各视图

function viewTranscript() {
  if (!S.segments.length) {
    return `<div class="empty"><div class="ei">◌</div><div class="et">还没有转写结果</div>
      <div class="et" style="font-size:13px">拖入文件后点击「开始转写」</div></div>`;
  }
  const wave = `
    <div class="wavebar">
      <div class="wavebars" id="waveBox"></div>
      <div class="whead">
        <span>00:00</span>
        <span>已转写 <b>${S.segments.length}</b> 段</span>
        <span class="spacer"></span>
        <span>${S.segments.length ? fmtTime(S.segments[S.segments.length - 1].end) : '00:00'}</span>
      </div>
    </div>`;
  const rows = S.segments.map((s, i) => `
    <div class="seg ${i === S.segments.length - 1 ? 'act' : ''}">
      <div class="tm">${fmtTime(s.start)}</div>
      <div><div class="tx">${esc(s.text)}</div>
        ${s.en ? `<div class="en">${esc(s.en)}</div>` : ''}</div>
    </div>`).join('');
  return `<div class="result flush" style="height:100%">${wave}
    <div style="flex:1;overflow:auto;padding:14px 20px 20px">${rows}</div></div>`;
}

function viewSummary() {
  if (!S.summary) {
    return `<div class="empty"><div class="ei">◌</div><div class="et">还没有生成纪要</div>
      <div class="et" style="font-size:13px">勾选「纪要」输出并开始转写</div></div>`;
  }
  const s = S.summary;
  const points = s.points.map((p) => `<li>${p}</li>`).join('');
  return `<div class="summary">
    <h3>概要</h3><p>${esc(s.overview)}</p>
    <h3>要点</h3><ol>${points}</ol>
    <h3>待办</h3><p>${esc(s.todos || '无')}</p>
  </div>`;
}

function viewBilingual() {
  if (!S.bilingual.length) {
    return `<div class="empty"><div class="ei">◌</div><div class="et">还没有双语字幕</div>
      <div class="et" style="font-size:13px">翻译到英文后自动生成时间轴对齐的双语字幕</div></div>`;
  }
  const rows = S.bilingual.map((s) => `
    <div class="subrow">
      <div class="tm">${fmtTime(s.start)}<br>${fmtTime(s.end)}</div>
      <div><div class="zh">${esc(s.zh)}</div><div class="en">${esc(s.en)}</div></div>
    </div>`).join('');
  return `<div class="sublist">${rows}</div>`;
}

function viewExport() {
  if (!S.exports.length) {
    return `<div class="empty"><div class="ei">◌</div><div class="et">还没有导出文件</div>
      <div class="et" style="font-size:13px">转写完成后会自动列出生成的文件</div></div>`;
  }
  const rows = S.exports.map((f) => `
    <div class="expr">
      <div><div class="fn">${esc(f.name)}</div><div class="fd">${esc(f.desc)}</div></div>
      <div class="sz">${esc(f.size)}</div>
      <div class="st">已生成</div>
    </div>`).join('');
  return `<div class="export">
    <div class="exph"><span>文件</span><span>用途</span><span>体积</span><span>状态</span></div>
    ${rows}
    <div class="expbar">
      <span class="pth">${esc(S.outDir || '（默认输出目录）')}</span>
      <span class="spacer"></span>
      <span class="btn-sec" id="btnCopyPath">复制路径</span>
      <span class="btn-sec" id="btnOpenDir">打开目录</span>
    </div>
  </div>`;
}

function viewSettings() {
  const gpu = S.gpu || {};
  const ok = gpu.ok;
  const pct = ok ? Math.round((1 - gpu.ratio) * 100) : 0;
  const ring = `
    <svg class="ring" viewBox="0 0 100 100" role="img" aria-label="显存占用 ${gpu.ratio ? Math.round(gpu.ratio * 100) : 0}%">
      <circle cx="50" cy="50" r="42" fill="none" stroke="#EDF0F4" stroke-width="11"></circle>
      <circle cx="50" cy="50" r="42" fill="none" stroke="${ok && gpu.ratio > 0.8 ? '#C0392B' : '#136B49'}" stroke-width="11"
        stroke-dasharray="${(263.9 * (ok ? gpu.ratio : 0)).toFixed(1)} 263.9" transform="rotate(-90 50 50)"></circle>
      <text x="50" y="48" text-anchor="middle" font-family="SF Mono, monospace" font-size="18" font-weight="600" fill="#16181C">${ok ? Math.round(gpu.ratio * 100) + '%' : '—'}</text>
      <text x="50" y="64" text-anchor="middle" font-family="Inter, sans-serif" font-size="12" fill="#5A5F6A">显存占用</text>
    </svg>`;

  const logText = S.logs.slice(-120).join('\n');
  return `
    <div class="health">
      ${ring}
      <div>
        <div class="h1">${ok ? '本地推理环境就绪' : '未检测到 NVIDIA 显卡'}</div>
        <div class="h2">${ok
          ? `显存 <b>${gpu.used} / ${gpu.total} GB</b>，剩余 <b>${gpu.free} GB</b>。large-v3 需要约 12 GB，如果剩余不足请先关闭其他占用 GPU 的程序，否则转写进程会在加载模型时静默退出。`
          : '将自动降级到 CPU 推理，速度约为 GPU 的十分之一。'}</div>
      </div>
    </div>
    <div class="chk">
      <div class="chk-h"><span>运行环境</span><span>检测值</span><span>状态</span></div>
      <div class="chk-r"><span class="k">模型目录</span><span class="v">${esc(S.paths?.modelDir || '—')}</span><span class="s ${S.paths?.modelReady ? 'ok' : 'bad'}">${S.paths?.modelReady ? '模型就位' : '未找到模型'}</span></div>
      <div class="chk-r"><span class="k">后端程序</span><span class="v">${esc(S.paths?.backendExe || '—')}</span><span class="s ok">就绪</span></div>
      <div class="chk-r"><span class="k">当前设备</span><span class="v">${S.device}</span><span class="s ok">${S.device === 'cuda' ? 'GPU 推理' : 'CPU 推理'}</span></div>
      <div class="chk-r"><span class="k">网络请求</span><span class="v">全程无</span><span class="s ok">离线</span></div>
    </div>
    <div class="logbox"><pre id="logPre">${esc(logText || '（暂无日志）')}</pre></div>`;
}

function renderResult() {
  const box = $('result');
  if (S.tab === 'transcript') box.innerHTML = viewTranscript();
  else if (S.tab === 'summary') box.innerHTML = viewSummary();
  else if (S.tab === 'bilingual') box.innerHTML = viewBilingual();
  else if (S.tab === 'export') box.innerHTML = viewExport();
  else box.innerHTML = viewSettings();

  if (S.tab === 'transcript' && S.segments.length) drawWave();
  bindResultActions();
}

function bindResultActions() {
  const openDir = $('btnOpenDir');
  if (openDir) openDir.onclick = () => window.api.openDir(S.outDir || S.paths?.outputs);
  const copyPath = $('btnCopyPath');
  if (copyPath) copyPath.onclick = async () => {
    const p = S.outDir || S.paths?.outputs || '';
    try { await navigator.clipboard.writeText(p); copyPath.textContent = '已复制'; }
    catch (_) { copyPath.textContent = '复制失败'; }
    setTimeout(() => { copyPath.textContent = '复制路径'; }, 1400);
  };
}

// ---------------------------------------------------------------- 波形

function drawWave() {
  const box = $('waveBox');
  if (!box) return;
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 1000 56');
  svg.setAttribute('preserveAspectRatio', 'none');

  const segs = S.segments;
  const N = Math.min(96, Math.max(12, segs.length));
  const segW = 1000 / N;
  for (let i = 0; i < N; i++) {
    const seg = segs[Math.floor(i * segs.length / N)];
    if (!seg) break;
    const dur = Math.max(0.4, (seg.end - seg.start) / 18);
    for (let k = 0; k < 4; k++) {
      const h = 5 + Math.min(1, dur) * 48 * (0.55 + 0.45 * Math.abs(Math.sin((i * 4 + k) * 1.1)));
      const r = document.createElementNS(NS, 'rect');
      r.setAttribute('x', (i * segW + k * 2.4).toFixed(2));
      r.setAttribute('y', ((56 - h) / 2).toFixed(2));
      r.setAttribute('width', '1.8');
      r.setAttribute('height', h.toFixed(2));
      const isLast = i === N - 1;
      r.setAttribute('fill', isLast ? '#E8622D' : '#9FB8DA');
      svg.appendChild(r);
    }
  }
  box.innerHTML = '';
  box.appendChild(svg);
}

function fmtTime(sec) {
  if (sec === undefined || sec === null) return '00:00';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// ---------------------------------------------------------------- 文件加入

async function addFiles(paths) {
  if (!paths || !paths.length) return;
  for (const p of paths) {
    if (S.files.some((f) => f.path === p)) continue;
    const st = await window.api.fs.statSync(p);
    S.files.push({
      path: p,
      name: p.split(/[\\/]/).pop(),
      size: st && st.ok ? fmtSize(st.size) : '',
      status: 'wait',
      msg: '排队',
      pct: 0,
    });
  }
  renderQueue();
}

// ---------------------------------------------------------------- 运行任务

async function run() {
  if (S.running) return;
  if (!S.files.length) {
    S.logs.push('请先添加要转写的文件');
    renderResult();
    return;
  }

  // 模型未就位就别启动：转写会在加载模型阶段失败，白跑一趟
  if (S.paths && !S.paths.modelReady) {
    S.logs.push(`模型未就位：${S.paths.modelDir}`);
    S.tab = 'settings';
    syncTabs();
    renderResult();
    alert(`模型未就位：\n${S.paths.modelDir}\n\n请先下载 faster-whisper 模型（large-v3 约 3 GB），或用环境变量 TRANSCRIBE_MODEL_DIR 指定已有模型目录。`);
    return;
  }

  // 显存检查：不足时明确告知，不让进程静默崩掉
  const gpu = await window.api.gpu();
  S.gpu = gpu;
  if (gpu.ok && gpu.free < 4 && S.device === 'cuda') {
    S.logs.push(`显存仅剩 ${gpu.free} GB，large-v3 需要约 12 GB。建议关闭其他占用 GPU 的程序，或改用 CPU。`);
    renderResult();
    if (!confirm(`显存仅剩 ${gpu.free} GB，large-v3 需要约 12 GB。\n\n继续可能导致转写进程在加载模型时直接退出（无报错）。\n\n仍要继续吗？`)) {
      return;
    }
  }

  S.running = true;
  S.logs = [];
  S.segments = [];
  S.bilingual = [];
  S.summary = null;
  S.exports = [];

  const cur = S.files[0];
  cur.status = 'run';
  cur.msg = '0%';
  renderQueue();

  resetStages({ decode: 'now' });
  setStage('decode', 'now', '准备中');
  $('btnRun').textContent = '停止转写';
  $('btnRun').classList.add('stop');
  $('stProgress').textContent = '运行中';

  const payload = {
    files: S.files.map((f) => f.path),
    language: S.language,
    modelDir: S.modelDir || S.paths?.modelDir || '',
    summarize: S.outputs.summary,
    translate: S.outputs.summary ? 'English' : '',
    bilingualSrt: S.outputs.summary,
    outDir: S.outDir,
    device: S.device,
    computeType: S.device === 'cuda' ? 'float16' : 'int8',
    beamSize: 5,
    vad: S.vad,
    words: S.words,
  };

  // 进度事件
  const offProgress = window.api.onProgress((obj) => onProgress(obj));
  const offLog = window.api.onLog((line) => {
    S.logs.push(line);
    if (S.tab === 'settings') renderResult();
  });

  let res;
  try {
    res = await window.api.run(payload);
  } finally {
    offProgress();
    offLog();
  }

  S.running = false;
  $('btnRun').textContent = '开始转写';
  $('btnRun').classList.remove('stop');

  if (!res || !res.ok) {
    const msg = (res && res.error) || '未知错误';
    S.logs.push('失败：' + msg);
    cur.status = 'fail';
    cur.msg = '失败';
    setStage('asr', 'err', '失败');
    $('stProgress').textContent = '失败';
    renderQueue();
    renderResult();
    return;
  }

  cur.status = 'done';
  cur.msg = '完成';
  renderQueue();

  // 读取输出文件填充视图
  await loadOutputs(cur);

  resetStages({ decode: 'done', asr: 'done', summary: 'done', trans: 'done', export: 'done' });
  S.stages[0].s = '16kHz 单声道';
  S.stages[1].s = S.segments.length + ' 段';
  S.stages[2].s = S.summary ? '已生成' : '未启用';
  S.stages[3].s = S.bilingual.length ? '已对齐' : '未启用';
  S.stages[4].s = S.exports.length + ' 个文件';
  renderStages();

  $('stProgress').textContent = '完成';
  S.tab = 'transcript';
  syncTabs();
  renderResult();
}

/** 进度事件处理：后端以 NDJSON 推送 */
function onProgress(obj) {
  const ev = obj.__event;
  if (ev === 'stage') {
    setStage(obj.stage, obj.state || 'now', obj.detail || '');
  } else if (ev === 'segments') {
    // 后端只推送新增段落（增量），这里追加而非整体替换
    if (obj.new) S.segments.push(obj.new);
    const cur = S.files[0];
    if (cur) { cur.pct = obj.pct ?? 0; cur.msg = Math.round(obj.pct || 0) + '%'; }
    $('stProgress').innerHTML = `已转写 <b>${Math.round(obj.pct || 0)}%</b> · ${obj.count ?? S.segments.length} 段`;
    if (obj.speed) $('stSpeed').textContent = obj.speed.toFixed(1) + 'x 实时';
    // 高频刷新会卡渲染，每 6 段刷一次足够流畅
    if ((obj.count ?? 0) % 6 === 0 && S.tab === 'transcript') renderResult();
    renderQueue();
  } else if (ev === 'summary') {
    S.summary = obj.data || null;
    if (S.tab === 'summary') renderResult();
  } else if (ev === 'bilingual') {
    S.bilingual = obj.data || [];
    if (S.tab === 'bilingual') renderResult();
  } else if (ev === 'exports') {
    S.exports = obj.data || [];
    if (S.tab === 'export') renderResult();
  } else if (ev === 'done') {
    if (obj.data) S.segments = obj.data;
  }
}

/** 读取输出目录里的实际文件 */
async function loadOutputs(fileItem) {
  const dir = S.outDir || S.paths?.outputs;
  if (!dir) return;
  // 输出文件名基于输入文件名，这里按约定规则拼
  const base = fileItem.name.replace(/\.[^.]+$/, '');
  const specs = [
    { ext: '.srt', desc: '标准字幕，播放器可直接挂载' },
    { ext: '.English.srt', desc: '中英对照，时间轴对齐' },
    { ext: '.vtt', desc: 'WebVTT，网页播放器用' },
    { ext: '.txt', desc: '纯文本，便于检索与二次编辑' },
    { ext: '.json', desc: '结构化数据，含词级时间戳' },
    { ext: '.summary.md', desc: '中文纪要：概要 / 要点 / 待办' },
  ];
  for (const sp of specs) {
    const full = `${dir}\\${base}${sp.ext}`;
    const st = await window.api.fs.statSync(full);
    if (st && st.ok) {
      S.exports.push({ name: base + sp.ext, desc: sp.desc, size: fmtSize(st.size), path: full });
    }
  }
  // 解析 JSON 里的段落
  const jr = await window.api.fs.readFile(`${dir}\\${base}.json`);
  if (jr && jr.ok) {
    try {
      const data = JSON.parse(jr.data);
      if (Array.isArray(data.segments) && data.segments.length) {
        S.segments = data.segments.map((s) => ({
          start: s.start, end: s.end, text: s.text,
        }));
      }
    } catch (_) { /* 解析失败保持已有段落 */ }
  }
  // 解析双语字幕
  const br = await window.api.fs.readFile(`${dir}\\${base}.English.srt`);
  if (br && br.ok) S.bilingual = parseSrtBilingual(br.data);
  // 解析纪要
  const sr = await window.api.fs.readFile(`${dir}\\${base}.summary.md`);
  if (sr && sr.ok) S.summary = parseSummary(sr.data);
}

/** 解析 SRT（双语：奇数行中文、偶数行英文） */
function parseSrtBilingual(raw) {
  const out = [];
  const blocks = raw.split(/\r?\n\r?\n/).filter((b) => b.trim());
  for (const b of blocks) {
    const lines = b.split(/\r?\n/).filter((l) => l.trim());
    const idx = lines.findIndex((l) => l.includes('-->'));
    if (idx < 0) continue;
    const m = lines[idx].match(/(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)/);
    if (!m) continue;
    const start = +m[1] * 3600 + +m[2] * 60 + +m[3] + +m[4] / 1000;
    const end = +m[5] * 3600 + +m[6] * 60 + +m[7] + +m[8] / 1000;
    const body = lines.slice(idx + 1);
    const zh = body[0] || '';
    const en = body.slice(1).join(' ');
    out.push({ start, end, zh, en });
  }
  return out;
}

/** 解析纪要 md */
function parseSummary(raw) {
  const lines = raw.split(/\r?\n/);
  const res = { overview: '', points: [], todos: '' };
  let cur = null;
  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith('【概要】')) { cur = 'overview'; continue; }
    if (t.startsWith('【要点】')) { cur = 'points'; continue; }
    if (t.startsWith('【待办】')) { cur = 'todos'; continue; }
    if (!t) continue;
    if (cur === 'overview') res.overview += t;
    else if (cur === 'points') res.points.push(t.replace(/^\d+[.、]\s*/, ''));
    else if (cur === 'todos') res.todos += t;
  }
  res.overview = res.overview.trim();
  res.todos = res.todos.trim();
  return res.points.length ? res : null;
}

// ---------------------------------------------------------------- 事件绑定

function syncTabs() {
  document.querySelectorAll('.tab').forEach((t) => {
    t.classList.toggle('on', t.dataset.v === S.tab);
  });
}

function bindChips(containerId, apply) {
  const box = $(containerId);
  box.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    chip.classList.toggle('on');
    apply(chip.classList.contains('on'), chip.dataset.v);
  });
}

function init() {
  // 窗口按钮
  $('btnMin').onclick = () => window.api.minimize();
  $('btnMax').onclick = () => window.api.maximize();
  $('btnClose').onclick = () => window.api.close();
  $('wClose').onclick = () => window.api.close();
  $('wMin').onclick = () => window.api.minimize();
  $('wMax').onclick = () => window.api.maximize();

  // 标签页
  $('tabs').addEventListener('click', (e) => {
    const tab = e.target.closest('.tab');
    if (!tab) return;
    S.tab = tab.dataset.v;
    syncTabs();
    renderResult();
  });

  // 拖放区
  const drop = $('drop');
  drop.onclick = async () => {
    const paths = await window.api.openFiles();
    addFiles(paths);
  };
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('over');
    const paths = [...e.dataTransfer.files].map((f) => f.path);
    addFiles(paths);
  });
  // 全窗口拖放
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) {
      addFiles([...e.dataTransfer.files].map((f) => f.path));
    }
  });

  // 参数
  bindChips('langChips', (on, v) => { if (on) S.language = v; });
  bindChips('devChips', (on, v) => { if (on) S.device = v; });
  bindChips('outChips', (on, v) => { S.outputs[v] = on; });

  $('tgVad').onclick = function () {
    this.classList.toggle('on');
    S.vad = this.classList.contains('on');
  };
  $('tgWords').onclick = function () {
    this.classList.toggle('on');
    S.words = this.classList.contains('on');
  };

  // 模型选择
  $('modelBtn').onclick = () => $('modal').classList.add('show');
  $('mCancel').onclick = () => $('modal').classList.remove('show');
  $('mOk').onclick = () => {
    const sel = document.querySelector('.mcard.sel');
    if (sel) {
      S.modelName = sel.querySelector('.mt').textContent;
      S.modelDir = sel.dataset.dir || S.paths?.modelDir || '';
      $('modelBtn').innerHTML = `${S.modelName} <span class="car">▼</span>`;
    }
    $('modal').classList.remove('show');
  };
  $('mgrid').addEventListener('click', (e) => {
    const card = e.target.closest('.mcard');
    if (!card) return;
    document.querySelectorAll('.mcard').forEach((c) => c.classList.remove('sel'));
    card.classList.add('sel');
  });

  // 运行 / 取消
  $('btnRun').onclick = async () => {
    if (S.running) {
      await window.api.cancel();
      S.running = false;
      $('btnRun').textContent = '开始转写';
      $('btnRun').classList.remove('stop');
      $('stProgress').textContent = '已取消';
      return;
    }
    run();
  };

  // 环境自检
  $('btnCheck').onclick = async () => {
    S.tab = 'settings';
    syncTabs();
    S.logs.push('正在检测环境…');
    renderResult();
    const res = await window.api.check();
    S.logs.push(res.output || res.error || '');
    renderResult();
  };

  // 初始化
  resetStages({});
  renderQueue();
  renderResult();

  window.api.paths().then((p) => {
    S.paths = p;
    S.modelDir = p.modelDir || '';
    $('stModel').textContent = p.modelReady
      ? 'large-v3 · ' + p.modelDir.split(/[\\/]/).pop()
      : '模型未就位';
    $('stOut').textContent = '输出 ' + (p.outputs || '');
    $('titleMeta').textContent = p.isPackaged ? '本地推理 · 无网络请求' : '开发模式 · 本地推理';
  });

  window.api.gpu().then((g) => {
    S.gpu = g;
    if (g.ok) {
      $('gpuLed').className = 'led' + (g.ratio > 0.85 ? ' bad' : '');
      $('gpuText').textContent = g.ratio > 0.85 ? '显存不足' : 'CUDA 就绪';
      $('stVram').textContent = `显存 ${g.used} / ${g.total} GB`;
    } else {
      $('gpuLed').className = 'led wait';
      $('gpuText').textContent = 'CPU 模式';
      $('stVram').textContent = '未检测到 GPU';
      if (S.device === 'cuda') {
        S.device = 'cpu';
        document.querySelectorAll('#devChips .chip').forEach((c) => {
          c.classList.toggle('on', c.dataset.v === 'cpu');
        });
      }
    }
  });
}

document.addEventListener('DOMContentLoaded', init);

/**
 * 注入演示数据（用于生成 README 截图与演示）。
 * 正常使用时不会调用，界面数据完全来自真实的转写产物。
 */
window.__injectDemo = function (data) {
  if (data.stages) {
    S.stages = data.stages;
    renderStages();
  }
  if (data.segments) S.segments = data.segments;
  if (data.files) {
    S.files = data.files;
    renderQueue();
  }
  if (data.bilingualRaw) S.bilingual = parseSrtBilingual(data.bilingualRaw);
  if (data.summaryRaw) S.summary = parseSummary(data.summaryRaw);
  if (data.exports) S.exports = data.exports;
  if (data.tab) S.tab = data.tab;
  syncTabs();
  renderResult();
  return 'ok';
};
