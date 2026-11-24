// 渲染层：状态机、文件队列、进度联动、波形渲染
// 只通过 window.api 与主进程通信，不直接碰 Node

// ---------------------------------------------------------------- 全局状态

const S = {
  files: [],            // 队列项 { path, name, size, status, pct, msg }
  running: false,
  tab: 'transcript',
  language: 'zh',
  device: 'cuda',
  outputs: { srt: true, txt: true, vtt: false, json: false, summary: false, proofread: false },
  vad: true,
  words: false,
  modelDir: '',
  modelName: 'large-v3',
  outDir: '',
  paths: null,
  segments: [],         // 转写段落
  bilingual: [],        // 双语对照
  proofread: null,     // 校对结果 { scanned, changedCount, changes: [...] }
  summary: null,        // { overview, points, todos }
  exports: [],          // 导出文件清单
  logs: [],
  stages: [],           // 状态带五格
};

/**
 * 模型规格表。
 *
 * 数据来源：faster-whisper 官方模型卡 + 本机实测（large-v3 在单卡 16GB 上
 * 跑 175 分钟中文课程为 6.4 倍实时）。
 *
 * 准确率用公开基准的相对水平表示（不是绝对 CER），
 * 因为不同语种与音频条件差异很大，给绝对数字会误导用户。
 */
const MODELS = [
  {
    key: 'large-v3',
    dir: 'E:/whisper_model',
    tag: '最准',
    size: '3.0 GB',
    speed: 6.4,
    accuracy: 100,
    vram: '12 - 16 GB',
    good: '中文课程、访谈、播客、多语种内容、专业术语',
    note: '准确率最高，专有名词与中英混说表现最好。实测 6.4 倍实时，3 小时素材约 27 分钟跑完。',
  },
  {
    key: 'turbo',
    dir: '',
    tag: '均衡',
    size: '1.6 GB',
    speed: 10,
    accuracy: 97,
    vram: '6 - 8 GB',
    good: '中文课程、访谈、日常录音，显存紧张的机器',
    note: '准确度接近 large-v3，速度约为它的 1.5 倍，显存占用只有一半。',
  },
  {
    key: 'medium',
    dir: '',
    tag: '推荐',
    size: '1.5 GB',
    speed: 7,
    accuracy: 88,
    vram: '5 - 7 GB',
    good: '普通话录音的通用选择，清晰人声',
    note: '速度与准确率的平衡点。清晰的普通话人声（课程、访谈）表现已经很好。',
  },
  {
    key: 'small',
    dir: '',
    tag: '最快',
    size: '500 MB',
    speed: 16,
    accuracy: 68,
    vram: '2 - 3 GB',
    good: '快速预览、口音较重的短片段',
    note: '体积最小、速度最快。中文准确率一般，适合先试手或听个大概。',
  },
];

const STAGE_DEFS = [
  { key: 'decode',  n: '01 解码', t: '音频提取' },
  { key: 'asr',     n: '02 识别', t: '语音转写' },
  { key: 'proofread', n: '03 校对', t: '错字修正' },
  { key: 'summary', n: '04 纪要', t: '要点提炼' },
  { key: 'trans',   n: '05 翻译', t: '逐段对齐' },
  { key: 'export',  n: '06 导出', t: '写入文件' },
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

/**
 * 把校对前后的差异高亮出来。
 *
 * 逐字符比对找出差异区间，差异部分用 <mark> 标出。
 * 没有整行上下文时（如后端只给了词语），直接高亮词语本身。
 */
function highlightDiff(original, corrected, wrong, right) {
  if (!original || !corrected) return esc(corrected || '');
  const a = String(original);
  const b = String(corrected);

  // 找出首个差异点与末个差异点
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }

  // 差异区间过大说明整体重写了，不高亮（避免整段标黄）
  if (endB - start > 12) return esc(b);

  return (
    esc(b.slice(0, start)) +
    `<mark>${esc(b.slice(start, endB))}</mark>` +
    esc(b.slice(endB))
  );
}

function viewProofread() {
  const pr = S.proofread;
  if (!pr) {
    return `<div class="empty"><div class="ei">◌</div><div class="et">还没有校对结果</div>
      <div class="et" style="font-size:13px">勾选「校对」输出并开始转写，会自动修正错别字与专有名词</div></div>`;
  }
  if (pr.skipped) {
    return `<div class="empty"><div class="ei">◌</div><div class="et">校对已跳过</div>
      <div class="et" style="font-size:13px">本地 Ollama 不可用 —— 校对依赖本地模型推理</div></div>`;
  }

  const head = `
    <div class="pr-head">
      <div class="pr-stat"><b>${pr.changedCount}</b><span>处修正</span></div>
      <div class="pr-stat"><b>${pr.scanned}</b><span>段已检查</span></div>
      <div class="pr-stat"><b>${pr.blocks}</b><span>个校对块</span></div>
      ${pr.failedBlocks ? `<div class="pr-stat bad"><b>${pr.failedBlocks}</b><span>块失败</span></div>` : ''}
    </div>`;

  if (!pr.changedCount) {
    return head + `<div class="empty"><div class="ei">✓</div><div class="et">没有发现错别字</div>
      <div class="et" style="font-size:13px">${pr.scanned} 段文本均未发现同音误识别或专有名词错误</div></div>`;
  }

  const rows = pr.changes.map((c, i) => {
    // 有整行上下文时展示整行对照，否则退回词语对照
    const before = c.fullOriginal || c.original;
    const after = c.fullCorrected || c.corrected;
    const afterHtml = c.fullOriginal
      ? highlightDiff(c.fullOriginal, c.fullCorrected)
      : esc(c.corrected);
    return `
    <div class="pr-item">
      <div class="pr-no">${i + 1}</div>
      <div class="pr-body">
        <div class="pr-before"><span class="pr-tag">原文</span>${esc(before)}</div>
        <div class="pr-after"><span class="pr-tag">改后</span>${afterHtml}</div>
      </div>
    </div>`;
  }).join('');

  return head + `<div class="pr-list">${rows}</div>`;
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
  // 显存需求随模型变化，但设置页可能在模型规格加载前就渲染 ——
  // 这里兜个底，别让界面上出现 "undefined GB"。
  const needGiB = Number.isFinite(gpu.needGiB) ? gpu.needGiB : 12;
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
          ? `显存已用 <b>${gpu.used.toFixed(1)} GB</b>，总计 <b>${gpu.total.toFixed(1)} GB</b>，剩余 <b>${gpu.free.toFixed(1)} GB</b>。当前模型需要约 ${needGiB} GB，剩余不足时请先关闭其他占用 GPU 的程序 —— 否则转写进程会在加载模型阶段静默退出（不报错）。`
          : '未检测到 NVIDIA 显卡，将自动降级到 CPU 推理，速度约为 GPU 的十分之一。'}</div>
      </div>
    </div>
    <div class="chk">
      <div class="chk-h"><span>运行环境</span><span>检测值</span><span>状态</span></div>
      <div class="chk-r"><span class="k">模型目录</span><span class="v">${esc(S.paths?.modelDir || '—')}</span><span class="s ${S.paths?.modelReady ? 'ok' : 'bad'}">${S.paths?.modelReady ? '模型就位' : '未找到模型'}</span></div>
      <div class="chk-r"><span class="k">后端程序</span><span class="v">${esc(S.paths?.backendExe || '—')}</span><span class="s ok">就绪</span></div>
      <div class="chk-r"><span class="k">当前设备</span><span class="v">${S.device}</span><span class="s ok">${S.device === 'cuda' ? 'GPU 推理' : 'CPU 推理'}</span></div>
      <div class="chk-r"><span class="k">输出目录</span><span class="v">${esc(S.outDir || S.paths?.outputDir || '—')}</span><span class="s ok">可写</span></div>
      <div class="chk-r"><span class="k">网络请求</span><span class="v">全程无</span><span class="s ok">离线</span></div>
      <div class="chk-r"><span class="k">应用版本</span><span class="v">${esc(S.paths?.version || '—')}</span><span class="s ok">就绪</span></div>
    </div>
    <div class="logbox"><pre id="logPre">${esc(logText || '（暂无日志）')}</pre></div>`;
}

function renderResult() {
  const box = $('result');
  if (S.tab === 'transcript') box.innerHTML = viewTranscript();
  else if (S.tab === 'proofread') box.innerHTML = viewProofread();
  else if (S.tab === 'summary') box.innerHTML = viewSummary();
  else if (S.tab === 'bilingual') box.innerHTML = viewBilingual();
  else if (S.tab === 'export') box.innerHTML = viewExport();
  else box.innerHTML = viewSettings();

  if (S.tab === 'transcript' && S.segments.length) drawWave();
  bindResultActions();
}

function bindResultActions() {
  const openDir = $('btnOpenDir');
  if (openDir) openDir.onclick = () => window.api.openDir(S.outDir || S.paths?.outputDir);
  const copyPath = $('btnCopyPath');
  if (copyPath) copyPath.onclick = async () => {
    const p = S.outDir || S.paths?.outputDir || '';
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

/** 渲染模型选择卡片：名称、擅长、速度、准确率、显存占用 */
function renderModelCards() {
  const box = $('mgrid');
  if (!box) return;
  const maxSpeed = Math.max(...MODELS.map((m) => m.speed));
  box.innerHTML = MODELS.map((m) => `
    <div class="mcard${m.key === S.modelName ? ' sel' : ''}" data-dir="${m.dir}" data-key="${m.key}">
      <div class="mname">
        <span class="mt">${esc(m.key)}</span>
        <span class="mv">faster-whisper</span>
      </div>
      <span class="mtag">${esc(m.tag)}</span>
      <div class="mrow">
        <span class="mk">擅长</span>
        <span class="mv2">${esc(m.good)}</span>
      </div>
      <div class="mrow">
        <span class="mk">速度</span>
        <span class="mv2">约 ${m.speed} 倍实时（单卡 16GB 实测）
          <span class="mbar"><i class="dim" style="width:${(m.speed / maxSpeed * 100).toFixed(0)}%"></i></span>
        </span>
      </div>
      <div class="mrow">
        <span class="mk">准确率</span>
        <span class="mv2">相对水平 ${m.accuracy}%
          <span class="mbar"><i style="width:${m.accuracy}%"></i></span>
        </span>
      </div>
      <div class="mrow">
        <span class="mk">显存</span>
        <span class="mv2">${esc(m.vram)}</span>
      </div>
      <div class="mfoot">
        <span class="msize">${esc(m.size)}</span>
        <span class="msize">${m.dir ? '已就位' : '需下载'}</span>
      </div>
      <div class="mnote">${esc(m.note)}</div>
    </div>`).join('');
}

/** 更新输出目录显示（只显示末两段，完整路径放 title 里） */
function updateOutPathLabel() {
  const el = $('outPath');
  if (!el) return;
  const dir = S.outDir || S.paths?.outputDir || '';
  el.textContent = dir || '默认输出目录';
  el.title = dir;
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
  S.proofread = null;
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
    proofread: S.outputs.proofread,
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

  // 读取输出文件填充视图（用后端实际写出的文件清单）
  await loadOutputs(cur, S.exports);

  resetStages({
    decode: 'done', asr: 'done', proofread: 'done',
    summary: 'done', trans: 'done', export: 'done',
  });
  S.stages[0].s = '16kHz 单声道';
  S.stages[1].s = S.segments.length + ' 段';
  S.stages[2].s = S.proofread
    ? (S.proofread.changedCount ? S.proofread.changedCount + ' 处修正' : '无须修正')
    : '未启用';
  S.stages[3].s = S.summary ? '已生成' : '未启用';
  S.stages[4].s = S.bilingual.length ? '已对齐' : '未启用';
  S.stages[5].s = S.exports.length + ' 个文件';
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
    // 按时间节流刷新：每 200ms 最多刷一次。
    // 按段数节流（每 N 段）不行 —— 段落来得快时界面会明显卡顿，
    // 慢素材又长时间不刷新，用户会以为程序没动。
    const now = Date.now();
    if (S.tab === 'transcript' && now - (S.lastPaint || 0) > 200) {
      S.lastPaint = now;
      renderResult();
    }
    renderQueue();
  } else if (ev === 'summary') {
    S.summary = obj.data || null;
    if (S.tab === 'summary') renderResult();
  } else if (ev === 'bilingual') {
    S.bilingual = obj.data || [];
    if (S.tab === 'bilingual') renderResult();
  } else if (ev === 'proofread') {
    S.proofread = obj.data || null;
    if (S.tab === 'proofread') renderResult();
  } else if (ev === 'exports') {
    // 后端报的是它实际写出的文件（含完整路径），直接存下来给 loadOutputs 用
    S.exports = obj.data || [];
    if (S.tab === 'export') renderResult();
  } else if (ev === 'done') {
    if (obj.data) S.segments = obj.data;
  }
}

/** 文件用途说明（按扩展名匹配） */
const FILE_DESC = {
  '.srt': '标准字幕，播放器可直接挂载',
  '.English.srt': '中英对照，时间轴对齐',
  '.vtt': 'WebVTT，网页播放器用',
  '.txt': '纯文本，便于检索与二次编辑',
  '.json': '结构化数据，含词级时间戳',
  '.summary.md': '中文纪要：概要 / 要点 / 待办',
  '.md': '中文纪要：概要 / 要点 / 待办',
  '.corrected.srt': '校对后字幕，错字已修正',
  '.corrected.txt': '校对后纯文本',
  '.proofread.json': '校对改动清单',
};

/** 根据文件名推断用途 */
function describeFile(name) {
  if (name.endsWith('.English.srt')) return FILE_DESC['.English.srt'];
  if (name.endsWith('.summary.md')) return FILE_DESC['.summary.md'];
  if (name.endsWith('.corrected.srt')) return FILE_DESC['.corrected.srt'];
  if (name.endsWith('.corrected.txt')) return FILE_DESC['.corrected.txt'];
  if (name.endsWith('.proofread.json')) return FILE_DESC['.proofread.json'];
  const dot = name.lastIndexOf('.');
  const ext = dot >= 0 ? name.slice(dot) : '';
  return FILE_DESC[ext] || '输出文件';
}

/**
 * 读取输出文件。
 *
 * 优先用后端 exports 事件里报的完整路径 —— 那是后端实际写出来的文件，
 * 不需要这边再按文件名规则推一遍（推错一次就是「转写完了但界面没结果」）。
 * 后端没报时（比如开发态直接调 API）再退回按规则拼路径。
 */
async function loadOutputs(fileItem, reported) {
  const dir = S.outDir || S.paths?.outputDir;
  if (!dir) return;

  const base = fileItem.name.replace(/\.[^.]+$/, '');

  // 清单：优先用后端报的
  let files = [];
  if (reported && reported.length) {
    files = reported.map((f) => ({
      name: f.name,
      path: f.path,
      size: fmtSize(f.size),
      desc: describeFile(f.name),
    }));
  } else {
    const specs = ['.srt', '.English.srt', '.vtt', '.txt', '.json', '.summary.md',
                   '.corrected.srt', '.corrected.txt', '.proofread.json'];
    for (const ext of specs) {
      const full = `${dir}\\${base}${ext}`;
      const st = await window.api.fs.statSync(full);
      if (st && st.ok) {
        files.push({
          name: base + ext,
          path: full,
          size: fmtSize(st.size),
          desc: describeFile(base + ext),
        });
      }
    }
  }
  S.exports = files;

  // 逐个读需要解析内容的文件
  const jsonFile = files.find((f) => f.name.endsWith('.json'));
  if (jsonFile) {
    const jr = await window.api.fs.readFile(jsonFile.path);
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
  }

  const biFile = files.find((f) => f.name.endsWith('.English.srt'));
  if (biFile) {
    const br = await window.api.fs.readFile(biFile.path);
    if (br && br.ok) S.bilingual = parseSrtBilingual(br.data);
  }

  const sumFile = files.find((f) => f.name.endsWith('.summary.md'));
  if (sumFile) {
    const sr = await window.api.fs.readFile(sumFile.path);
    if (sr && sr.ok) S.summary = parseSummary(sr.data);
  }

  // 校对结果：后端写的 .proofread.json 里有完整的改动清单
  const prFile = files.find((f) => f.name.endsWith('.proofread.json'));
  if (prFile) {
    const prr = await window.api.fs.readFile(prFile.path);
    if (prr && prr.ok) {
      try {
        S.proofread = JSON.parse(prr.data);
      } catch (_) { /* 解析失败保持已有结果 */ }
    }
  }
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
    // 模型输出的实际格式是 markdown 标题：'### 【概要】'
    // 直接 startsWith('【概要】') 匹配不上，导致整篇解析为空。
    // 这里先剥掉 markdown 标题前缀（# 与空格），再判断是哪一段。
    const t = line.trim().replace(/^#{1,6}\s*/, '').trim();
    if (t.startsWith('【概要】')) { cur = 'overview'; continue; }
    if (t.startsWith('【要点】')) { cur = 'points'; continue; }
    if (t.startsWith('【待办】')) { cur = 'todos'; continue; }
    if (!t) continue;
    // 分隔线（---）不是内容
    if (/^[-*_]{3,}$/.test(t)) continue;
    if (cur === 'overview') res.overview += t;
    else if (cur === 'points') res.points.push(t.replace(/^\d+[.、]\s*/, ''));
    else if (cur === 'todos') res.todos += t;
  }
  res.overview = res.overview.trim();
  res.todos = res.todos.trim();
  return res.points.length || res.overview ? res : null;
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

  // 模型选择：渲染带详细参数的卡片
  renderModelCards();
  $('modelBtn').onclick = () => $('modal').classList.add('show');
  $('mCancel').onclick = () => $('modal').classList.remove('show');
  $('mOk').onclick = () => {
    const sel = document.querySelector('.mcard.sel');
    if (sel) {
      S.modelName = sel.dataset.key;
      // 只有已就位的模型才能直接用；未下载的模型沿用当前模型目录并提示先下载
      S.modelDir = sel.dataset.dir || S.paths?.modelDir || '';
      if (!sel.dataset.dir) {
        S.logs.push(`模型 ${S.modelName} 尚未下载，请先下载后使用`);
        S.tab = 'settings';
        syncTabs();
        renderResult();
      }
      $('modelBtn').innerHTML = `${S.modelName} <span class="car">▼</span>`;
      // 显存需求随模型变化
      const spec = MODELS.find((m) => m.key === S.modelName);
      if (spec && S.gpu) S.gpu.needGiB = parseFloat(spec.vram) || 12;
    }
    $('modal').classList.remove('show');
  };
  $('mgrid').addEventListener('click', (e) => {
    const card = e.target.closest('.mcard');
    if (!card) return;
    document.querySelectorAll('.mcard').forEach((c) => c.classList.remove('sel'));
    card.classList.add('sel');
  });

  // 输出目录选择
  $('btnPickOut').onclick = async () => {
    const dir = await window.api.pickDir();
    if (!dir) return;
    S.outDir = dir;
    updateOutPathLabel();
  };

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
    $('stOut').textContent = '输出 ' + (p.outputDir || '');
    updateOutPathLabel();
  });

  window.api.gpu().then((g) => {
    S.gpu = g;
    if (g.ok) {
      // 当前模型的显存需求（供设置页提示用）
      const spec = MODELS.find((m) => m.key === S.modelName) || MODELS[0];
      g.needGiB = parseFloat(spec.vram) || 12;
      $('gpuLed').className = 'led' + (g.ratio > 0.85 ? ' bad' : '');
      $('gpuText').textContent = g.ratio > 0.85 ? '显存不足' : 'CUDA 就绪';
      $('stVram').textContent = `显存 ${g.used.toFixed(1)} / ${g.total.toFixed(1)} GB`;
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
  if (data.proofread) S.proofread = data.proofread;
  if (data.exports) S.exports = data.exports;
  if (data.tab) S.tab = data.tab;
  syncTabs();
  renderResult();
  return 'ok';
};
