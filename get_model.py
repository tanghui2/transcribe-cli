# -*- coding: utf-8 -*-
"""下载 faster-whisper 模型到本地目录。

用法：
    python get_model.py                       # 下载 large-v3 到默认目录
    python get_model.py --model medium        # 下载 medium（体积更小）
    python get_model.py --dir D:/models/large-v3

说明：
- 模型体积：large-v3 约 3GB，medium 约 1.5GB，small 约 500MB
- 需要已安装 huggingface_hub：pip install huggingface_hub
- 走公开网络下载不需要登录；若下载被限流，可设置 HF_TOKEN 环境变量
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 可选模型：名字 -> (仓库 ID, 大致体积)
MODELS = {
    "large-v3": ("Systran/faster-whisper-large-v3", "约 3.0 GB"),
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "约 1.6 GB"),
    "medium": ("Systran/faster-whisper-medium", "约 1.5 GB"),
    "small": ("Systran/faster-whisper-small", "约 500 MB"),
}

DEFAULT_DIR = os.environ.get("TRANSCRIBE_MODEL_DIR", r"E:/whisper_model")

# 只下必需文件；*.txt 是分词器词表
ALLOW_PATTERNS = ["*.json", "vocabulary.txt", "preprocessor_config.json"]


def main() -> int:
    p = argparse.ArgumentParser(description="下载 faster-whisper 模型")
    p.add_argument("--model", default="large-v3", choices=list(MODELS.keys()))
    p.add_argument("--dir", default=DEFAULT_DIR, help="下载目标目录")
    p.add_argument("--force", action="store_true", help="目录已存在时重新下载")
    args = p.parse_args()

    repo_id, size_hint = MODELS[args.model]
    target = Path(args.dir).resolve()

    # 已存在且文件完整就不重复下载
    if target.exists() and not args.force:
        if (target / "model.bin").exists() and (target / "config.json").exists():
            print(f"模型已存在，跳过：{target}")
            print("如需重新下载，加 --force")
            print()
            print(f"使用：python -m transcribe_cli -m {target} 你的文件.mp4")
            return 0
        print(f"目录存在但文件不完整，将重新下载：{target}")
        print("（删除该目录或加 --force）")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("缺少依赖，请先执行：pip install huggingface_hub", file=sys.stderr)
        return 1

    print(f"下载 {args.model}（{size_hint}）→ {target}")
    print(f"仓库：{repo_id}")
    print()

    snapshot_download(
        repo_id,
        local_dir=str(target),
        allow_patterns=ALLOW_PATTERNS,
        max_workers=4,
    )

    print()
    print("下载完成。文件清单：")
    for f in sorted(target.iterdir()):
        if f.is_file():
            mb = f.stat().st_size / 1024 / 1024
            print(f"  {f.name:<28} {mb:8.1f} MB")

    print()
    print("使用：")
    print(f"  python -m transcribe_cli -m {target} 你的文件.mp4")
    print()
    print("自检：")
    print(f"  python -m transcribe_cli --check -v -m {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
