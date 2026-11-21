# -*- coding: utf-8 -*-
"""PyInstaller 单文件入口。

直接打包 `transcribe_cli/__main__.py` 会因为包内相对导入
（`from . import core`）在单文件模式下失效，报：
    ImportError: attempted relative import with no known parent package

因此单独提供本文件作为打包入口，用绝对导入绕开相对导入。
源码运行仍推荐 `python -m transcribe_cli`。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 兼容两种运行方式：
# 1. 源码：transcribe_cli 作为包被导入
# 2. PyInstaller：脚本所在目录被加入 sys.path，包以顶层模块形式存在
try:
    from transcribe_cli.__main__ import main
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from transcribe_cli.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
