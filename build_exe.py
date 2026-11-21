# -*- coding: utf-8 -*-
"""把 transcribe-cli 打包成 Windows 单文件 exe。

用法（在任意目录执行均可）：
    python build_exe.py

产物：dist/transcribe-cli.exe

打包要点（都是本项目实测踩出来的）：
1. faster-whisper 依赖 ctranslate2，其 DLL 由 onnxruntime / cudnn 动态加载，
   PyInstaller 静态分析抓不到，必须用 --collect-all 显式带上。
2. Whisper 模型（large-v3 约 3GB）**不内置**：模型目录由用户通过 -m 指定，
   打包体积必须可控。--check 参数会提示模型路径。
3. 不内置 torch / transformers：faster-whisper 用不到，打进去会让 exe 膨胀数 GB。
4. add-data 的源路径必须写绝对路径，否则会按 spec 目录解析（坑过一次）。
"""
import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--name", "transcribe-cli",
    "--onefile",
    "--console",
    "--noconfirm",
    "--paths", PROJECT_DIR,
    # 入口用 exe_entry.py：直接打包 __main__.py 会因包内相对导入而失败
    os.path.join(PROJECT_DIR, "exe_entry.py"),
    # faster-whisper / ctranslate2 的全部运行时资源
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    # PyAV 的编解码插件
    "--collect-all", "av",
    # tokenizers / huggingface_hub 的部分资源
    "--collect-all", "tokenizers",
    # 体积大且用不到的推理栈
    "--exclude-module", "torch",
    "--exclude-module", "transformers",
    "--exclude-module", "sentence_transformers",
    "--exclude-module", "numpy.distutils",
    "--exclude-module", "matplotlib",
    "--exclude-module", "PIL",
    "--exclude-module", "pandas",
    "--distpath", os.path.join(PROJECT_DIR, "dist"),
    "--workpath", os.path.join(PROJECT_DIR, "build"),
    "--specpath", os.path.join(PROJECT_DIR, "build"),
]

print("执行：")
print("  " + " ".join(cmd[:6]) + " ...")
print()

proc = subprocess.run(cmd)
if proc.returncode != 0:
    print("\n打包失败，请检查上方输出", file=sys.stderr)
    sys.exit(proc.returncode)

exe_path = os.path.join(PROJECT_DIR, "dist", "transcribe-cli.exe")
if os.path.exists(exe_path):
    size_mb = os.path.getsize(exe_path) / 1024 / 1024
    print(f"\n打包完成：{exe_path}")
    print(f"体积：{size_mb:.1f} MB")
    print()
    print("注意：Whisper 模型不内置。首次使用请先下载模型，然后：")
    print("  transcribe-cli.exe --check -v")
    print("  transcribe-cli.exe -m <模型目录> 你的文件.mp4")
else:
    print("\n未找到产物，检查打包日志", file=sys.stderr)
    sys.exit(1)
