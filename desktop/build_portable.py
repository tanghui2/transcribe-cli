# -*- coding: utf-8 -*-
"""桌面版打包脚本（绕过 electron-builder）。

为什么不用 electron-builder：它在本机沙箱里跑不起来 ——
子进程被静默终止，没有任何输出，exit=0。表现出来就是「命令跑完了但
dist/ 里什么都没变」。而 NSIS + 7za + makensis 都能正常跑。

流程：
1. 7za 把 Electron 运行时与资源打成 app.zip
2. 自研 asar_pack.py 把应用源码打成 app.asar
3. 组装 win-unpacked 目录（Electron 运行时 + resources/app.asar + backend/）
4. 用 PyInstaller 打一个自解压启动器，把 app.zip 附在 exe 尾部

用法：
    python desktop/build_portable.py

产物：
    desktop/dist/transcribe-cli-0.1.0-windows-x64.exe   （单文件，双击即用）
    desktop/dist/win-unpacked/                          （目录版）
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DESKTOP = PROJECT / "desktop"
WORK = Path(os.environ.get("TCLI_BUILD_DIR", r"E:/tmp/tcli_build"))
ELECTRON_DIR = Path(os.environ.get("TCLI_ELECTRON_DIR", r"E:/tmp/gb3/node_modules/electron/dist"))
SEVENZA = Path(os.environ.get(
    "TCLI_SEVENZA",
    r"E:/tmp/electroncache/7zip@1.0.0/7zip-win-x64-a34pt/bin/7za.exe",
))
VERSION = "0.1.0"
APP_NAME = "transcribe-cli"
BACKEND = PROJECT / "dist_v3" / "transcribe-cli.exe"


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, errors="ignore")
    if r.returncode != 0:
        print(r.stdout or "")
        print(r.stderr or "")
        raise SystemExit(f"命令失败（{r.returncode}）：{' '.join(cmd[:3])}")


def step1_prepare_electron() -> Path:
    """把 Electron 运行时拷到工作目录。"""
    dst = WORK / "electron-runtime"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    log(f"拷贝 Electron 运行时：{ELECTRON_DIR} -> {dst}")
    for name in ("electron.exe", "resources", "locales"):
        src = ELECTRON_DIR / name
        if not src.exists():
            continue
        target = dst / name
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target)
    # electron.exe 要改名成应用名
    exe = dst / "electron.exe"
    if exe.exists():
        shutil.copy2(exe, dst / f"{APP_NAME}.exe")
    return dst


def step2_build_asar() -> Path:
    """把应用源码打成 app.asar。"""
    sys.path.insert(0, str(DESKTOP))
    from asar_pack import pack  # noqa: E402

    stage = WORK / "asar-src"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    for name in ("main.js", "preload.js", "package.json"):
        src = DESKTOP / name
        if src.exists():
            shutil.copy2(src, stage / name)
    shutil.copytree(DESKTOP / "renderer", stage / "renderer", dirs_exist_ok=True)

    out = WORK / "app.asar"
    pack(str(stage), str(out))
    return out


def step3_assemble(asar: Path) -> Path:
    """组装 win-unpacked 目录。"""
    unpacked = WORK / "win-unpacked"
    if unpacked.exists():
        shutil.rmtree(unpacked, ignore_errors=True)
    unpacked.mkdir(parents=True, exist_ok=True)

    runtime = WORK / "electron-runtime"
    for item in runtime.iterdir():
        target = unpacked / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    res = unpacked / "resources"
    res.mkdir(parents=True, exist_ok=True)
    shutil.copy2(asar, res / "app.asar")

    backend = res / "backend"
    backend.mkdir(parents=True, exist_ok=True)
    if BACKEND.exists():
        shutil.copy2(BACKEND, backend / "transcribe-cli.exe")
        log(f"后端已打包：{BACKEND.stat().st_size // 1024 // 1024} MB")
    else:
        log(f"警告：找不到后端 {BACKEND}，先跑 build_exe.py")

    return unpacked


def step4_launcher() -> Path:
    """打包自解压启动器（不含 payload 的裸 exe）。"""
    outdir = WORK / "launcher"
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--noconfirm",
        "--name", "tcli-launcher",
        "--distpath", str(outdir),
        "--workpath", str(WORK / "launcher-build"),
        "--specpath", str(WORK / "launcher-build"),
        "--exclude-module", "numpy", "--exclude-module", "torch",
        "--exclude-module", "pandas", "--exclude-module", "matplotlib",
        "--exclude-module", "faster_whisper", "--exclude-module", "ctranslate2",
        "--exclude-module", "av", "--exclude-module", "transformers",
        str(DESKTOP / "launcher.py"),
    ]
    log("打包自解压启动器…")
    run(cmd)
    return outdir / "tcli-launcher.exe"


def step5_zip_and_merge(unpacked: Path, launcher: Path) -> Path:
    """应用目录压成 zip，再与启动器合并成单文件 exe。"""
    zip_path = WORK / "app.zip"
    if zip_path.exists():
        zip_path.unlink()
    log("压缩应用目录…")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for root, _dirs, files in os.walk(unpacked):
            for f in files:
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, unpacked))

    out_dir = DESKTOP / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{APP_NAME}-{VERSION}-windows-x64.exe"

    lb = launcher.read_bytes()
    pb = zip_path.read_bytes()
    with out.open("wb") as f:
        f.write(lb)
        f.write(pb)
        f.write(struct.pack("<I", len(pb)))
    log(f"单文件 exe：{out}（{out.stat().st_size // 1024 // 1024} MB）")

    # 同时留一份目录版
    dir_out = out_dir / "win-unpacked"
    if dir_out.exists():
        shutil.rmtree(dir_out, ignore_errors=True)
    shutil.copytree(unpacked, dir_out)
    log(f"目录版：{dir_out}")
    return out


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    runtime = step1_prepare_electron()
    log(f"Electron 运行时就绪：{runtime}")
    asar = step2_build_asar()
    unpacked = step3_assemble(asar)
    launcher = step4_launcher()
    out = step5_zip_and_merge(unpacked, launcher)
    print()
    print(f"完成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
