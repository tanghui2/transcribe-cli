// 预加载脚本：向渲染进程暴露有限的白名单 API
// 渲染进程不接触 Node，只能通过这里的方法与主进程通信
//
// 注意：Electron 32 起即使 sandbox:false，preload 也无法 require('fs')，
// 因此文件读取一律走 IPC 到主进程执行，渲染层只拿结果。

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // ---- 文件选择 ----
  openFiles: () => ipcRenderer.invoke('dialog:openFiles'),
  // 注意：选目录叫 pickDir。早期版本这里叫 openDir，
  // 和下面「用系统默认程序打开目录」的 openDir 重名 —— 后者覆盖了前者，
  // 结果点「更改」不弹出选择框，而是去打开目录。
  pickDir: () => ipcRenderer.invoke('dialog:openDir'),

  // ---- 转写任务 ----
  run: (payload) => ipcRenderer.invoke('transcribe:run', payload),
  cancel: () => ipcRenderer.invoke('transcribe:cancel'),
  check: () => ipcRenderer.invoke('transcribe:check'),
  gpu: () => ipcRenderer.invoke('transcribe:gpu'),

  // ---- 进度事件 ----
  onProgress: (cb) => {
    const handler = (_e, obj) => cb(obj);
    ipcRenderer.on('transcribe:progress', handler);
    return () => ipcRenderer.removeListener('transcribe:progress', handler);
  },
  onLog: (cb) => {
    const handler = (_e, line) => cb(line);
    ipcRenderer.on('transcribe:log', handler);
    return () => ipcRenderer.removeListener('transcribe:log', handler);
  },

  // ---- 路径与系统 ----
  paths: () => ipcRenderer.invoke('app:paths'),
  openDir: (dir) => ipcRenderer.invoke('shell:openDir', dir),
  openFile: (file) => ipcRenderer.invoke('shell:openFile', file),

  // ---- 窗口控制 ----
  minimize: () => ipcRenderer.send('win:minimize'),
  maximize: () => ipcRenderer.send('win:maximize'),
  close: () => ipcRenderer.send('win:close'),
  isMaximized: () => ipcRenderer.invoke('win:isMaximized'),

  // ---- 读取输出文件（Electron 32+ 的 preload 不能用 fs，改走 IPC）----
  fs: {
    statSync: (p) => ipcRenderer.invoke('fs:stat', p),
    readFile: (p, enc) => ipcRenderer.invoke('fs:read', p, enc),
    existsSync: (p) => ipcRenderer.invoke('fs:exists', p),
  },
});
