"""自解压启动器：把 Electron 应用目录压进 exe，双击后解压运行。

为什么不用 electron-builder 的 portable：它在本机沙箱里跑不起来
（子进程被静默终止，无输出，exit=0）。而 NSIS 手工做 portable 时，
Electron 主进程从 NSIS 启动会「启动即退出」——
exe 存在、asar 正常、Exec 之后进程就没了，原因未查明。
自己写启动器更可控：启动逻辑是普通 Python，行为完全可预期。

原理：
1. 打包时把应用目录压成 app.zip，附在这个 exe 末尾
2. 运行时定位自身路径，读取末尾的 zip 数据
3. 解压到 %TEMP%\\transcribe-cli-<随机>，运行主 exe
4. 等主 exe 退出后清理临时目录

追加数据格式（文件尾部）：
    [4 字节 payload 长度][payload]
payload 是 zip 文件的原始字节。
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile


def _self_exe_path() -> str:
    """拿到「用户双击的那个 exe」的路径。

    PyInstaller 的 onefile 模式会把程序解包到 _MEIPASS 临时目录再运行，
    此时 sys.executable 指向临时副本（没有附加的 payload），
    必须用 sys.argv[0] —— 它保留的是用户实际双击的原始 exe 路径。
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and os.path.isfile(argv0):
        return os.path.abspath(argv0)
    # 退化情形：sys.executable 的同级目录里找带 payload 的文件
    exe = os.path.abspath(sys.executable)
    return exe


def _payload_len(exe_path: str) -> int:
    """读取 exe 末尾记录的自解压数据长度。"""
    with open(exe_path, "rb") as f:
        f.seek(-4, os.SEEK_END)
        return struct.unpack("<I", f.read(4))[0]


def _extract(zip_bytes: bytes, dest: str) -> None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(dest)


LOG_PATH = r"E:	mp\launcher-debug.txt"


def _log(msg: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "
")
    except Exception:
        pass


def main() -> int:
    _log("=== main start ===")
    exe_path = _self_exe_path()
    _log(f"exe_path={exe_path}")

    try:
        payload_len = _payload_len(exe_path)
        with open(exe_path, "rb") as f:
            f.seek(-4 - payload_len, os.SEEK_END)
            zip_bytes = f.read(payload_len)
    except Exception as e:  # noqa: BLE001
        print(f"读取自解压数据失败（{exe_path}）：{e}", file=sys.stderr)
        return 1

    _log(f"payload_len={payload_len}")
    tmp_root = os.path.join(tempfile.gettempdir(), "transcribe-cli")
    os.makedirs(tmp_root, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix="run-", dir=tmp_root)

    try:
        _log(f"work_dir={work_dir}")
        _extract(zip_bytes, work_dir)
        _log(f"extracted count={sum(len(fs) for _,_,fs in os.walk(work_dir))}")
        app_exe = os.path.join(work_dir, "transcribe-cli.exe")
        if not os.path.exists(app_exe):
            print(f"解压后找不到主程序（目录：{work_dir}）", file=sys.stderr)
            return 1

        # 关键：工作目录必须是解压目录，Electron 用相对路径找 resources/
        os.chdir(work_dir)

        # 清理会影响 Electron 启动的环境变量：
        #   NODE_OPTIONS 带 --use-system-ca 时，Electron 直接拒绝启动
        #     （报错 "--use-system-ca is not allowed in NODE_OPTIONS"）
        #   ELECTRON_RUN_AS_NODE=1 会让 Electron 当纯 Node 跑，二进制不加载
        #   PyInstaller 的 _MEIPASS 会让子进程误以为自己是打包程序
        env = dict(os.environ)
        for k in ("NODE_OPTIONS", "ELECTRON_RUN_AS_NODE",
                  "_MEIPASS", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
            env.pop(k, None)

        _log(f"launching {app_exe}")
        proc = subprocess.Popen([app_exe], cwd=work_dir, env=env)
        proc.wait()
        _log(f"child exited code={proc.returncode}")
        return proc.returncode
    finally:
        # 出错时保留临时目录便于排查，正常退出才清理
        if os.environ.get("TCLI_KEEP_TMP"):
            print(f"[launcher] 临时目录保留在：{work_dir}", file=sys.stderr)
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
