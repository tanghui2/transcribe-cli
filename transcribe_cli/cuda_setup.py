# -*- coding: utf-8 -*-
"""CUDA 运行时加载：把 NVIDIA DLL 目录注入 PATH。

cTranslate2（faster-whisper 的推理后端）依赖 cublas / cudnn 等动态库，
在 Windows 上如果不在 PATH 里，import 阶段就会报
`OSError: Library cudnn_ops_infer.so.8 ... not found`。

本模块必须在 `import faster_whisper` 之前调用 `setup_cuda()`。
DLL 目录可���过环境变量 TRANSCRIBE_CUDA_DIR 覆盖，默认 E:/cuda_dlls。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_CUDA_DIR = Path(r"E:/cuda_dlls")

# 需要注入 PATH 的子目录。cublas 必须在 cuda_runtime 之前，
# 否则可能加载到不匹配的版本。
_DLL_SUBDIRS = ("nvidia/cublas/bin", "nvidia/cuda_runtime/bin", "nvidia/cuda_nvrtc/bin")


def setup_cuda(cuda_dir: str | os.PathLike | None = None, verbose: bool = False) -> list[str]:
    """把 CUDA DLL 目录插入 PATH 最前面，返回实际生效的目录列表。

    已存在于 PATH 的目录不会重复添加。重复调用是安全的。
    """
    root = Path(cuda_dir) if cuda_dir else Path(
        os.environ.get("TRANSCRIBE_CUDA_DIR", DEFAULT_CUDA_DIR)
    )
    if not root.exists():
        if verbose:
            print(f"[cuda] 目录不存在，跳过：{root}", file=sys.stderr)
        return []

    added: list[str] = []
    for sub in _DLL_SUBDIRS:
        d = root / sub
        if not d.exists():
            continue
        # 统一成 Windows 风格路径，避免 Git Bash 下的正斜杠差异
        win = str(d).replace("/", "\\")
        parts = os.environ.get("PATH", "").split(os.pathsep)
        if win.lower() in {p.lower() for p in parts}:
            continue
        os.environ["PATH"] = win + os.pathsep + os.environ.get("PATH", "")
        added.append(win)

    if verbose:
        if added:
            print(f"[cuda] 已注入 {len(added)} 个目录", file=sys.stderr)
            for a in added:
                print(f"       {a}", file=sys.stderr)
        else:
            print("[cuda] 无需注入（目录已在 PATH 或不存在）", file=sys.stderr)
    return added


def report_runtime() -> dict:
    """返回当前推理运行时信息，用于 --check 自检。"""
    import ctranslate2

    info = {
        "ctranslate2": ctranslate2.__version__,
        "cuda_device_count": ctranslate2.get_cuda_device_count(),
        "supported_compute_types": ["auto", "int8", "float16", "float32"],
    }
    try:
        import torch  # 可选，仅用于打印更多信息

        info["torch"] = torch.__version__
        info["torch_cuda"] = torch.cuda.is_available()
    except Exception:
        pass
    return info


if __name__ == "__main__":
    setup_cuda(verbose=True)
    print(report_runtime())
