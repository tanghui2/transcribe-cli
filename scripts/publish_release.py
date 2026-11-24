# -*- coding: utf-8 -*-
"""一键发布到 GitHub：推源码 + 建 tag + 上传 Release 物料。

前置条件（只需做一次）：
    gh auth login

用法：
    python scripts/publish_release.py            # 推 main + 打 tag + 建 Release
    python scripts/publish_release.py --dry-run  # 只检查，不做任何写操作

脚本做的事：
1. 检查工作区干净、gh 已登录
2. 推送 main 到 origin
3. 创建并推送 tag（默认 v<版本号>，版本号取自 desktop/package.json）
4. 用 gh 创建 Release，附上 release-assets/ 里的 zip + 说明文本
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT / "release-assets"
NOTES_CANDIDATES = ["release-notes-v0.2.0.md", "release-notes.md"]


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(
        cmd,
        cwd=str(PROJECT),
        capture_output=capture,
        text=True,
        errors="ignore",
    )
    if capture and r.stdout:
        print(r.stdout.rstrip())
    if check and r.returncode != 0:
        if capture:
            print((r.stderr or "").rstrip())
        raise SystemExit(f"命令失败（{r.returncode}）：{' '.join(cmd[:3])}")
    return r


def read_version() -> str:
    pkg = json.loads((PROJECT / "desktop" / "package.json").read_text(encoding="utf-8"))
    return pkg["version"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只检查，不做任何写操作")
    ap.add_argument("--tag", default=None, help="tag 名，默认 v<版本号>")
    args = ap.parse_args()

    version = read_version()
    tag = args.tag or f"v{version}"
    print(f"版本 {version}，tag {tag}\n")

    # ---------- 检查 ----------
    print("[1/5] 检查工作区状态")
    st = run(["git", "status", "--porcelain"], capture=True)
    dirty = [l for l in (st.stdout or "").splitlines() if l.strip()]
    if dirty:
        print("  工作区有未提交的改动：")
        for l in dirty[:10]:
            print(f"    {l}")
        raise SystemExit("  请先提交或 stash 这些改动")
    print("  ✓ 工作区干净")

    print("\n[2/5] 检查 gh 登录")
    r = run(["gh", "auth", "status"], check=False, capture=True)
    if r.returncode != 0:
        raise SystemExit("  gh 未登录，请先执行：gh auth login")
    print("  ✓ gh 已登录")

    print("\n[3/5] 检查 Release 物料")
    assets = sorted(ASSETS_DIR.glob("*.zip")) if ASSETS_DIR.exists() else []
    if not assets:
        raise SystemExit(f"  {ASSETS_DIR} 里没有 zip 文件")
    for a in assets:
        print(f"  {a.name}  {a.stat().st_size / 1024 / 1024:.1f} MB")
    # 校验文件也一起传，方便用户验证下载完整性
    sums = ASSETS_DIR / "SHA256SUMS.txt"
    if sums.exists():
        assets.append(sums)
        print(f"  {sums.name}（校验值）")
    notes = None
    for cand in NOTES_CANDIDATES:
        p = PROJECT / cand
        if p.exists():
            notes = p
            break
    if notes:
        print(f"  说明文本：{notes.name}")
    else:
        print("  （没有说明文本，Release 正文将自动生成）")

    if args.dry_run:
        print("\n[dry-run] 检查通过，未做任何写操作")
        return 0

    # ---------- 执行 ----------
    print("\n[4/5] 推送源码与 tag")
    run(["git", "push", "origin", "main"])
    # tag 已存在就跳过
    exists = run(["git", "ls-remote", "--tags", "origin", tag], check=False, capture=True)
    if tag in (exists.stdout or ""):
        print(f"  tag {tag} 已存在，跳过")
    else:
        run(["git", "tag", "-a", tag, "-m", f"发布 {tag}"])
        run(["git", "push", "origin", tag])

    print("\n[5/5] 创建 Release")
    cmd = ["gh", "release", "create", tag, "--title", f"transcribe-cli {tag}", "--notes-file", str(notes)] if notes else ["gh", "release", "create", tag, "--title", f"transcribe-cli {tag}"]
    cmd += [str(a) for a in assets]
    run(cmd)

    print(f"\n完成。Release 地址：")
    print(f"  https://github.com/tanghui2/transcribe-cli/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
