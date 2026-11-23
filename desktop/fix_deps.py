# -*- coding: utf-8 -*-
"""绕过 npm 补装缺失的依赖包。

背景：这台机器上 npm 在 Windows 上解包大依赖树时会解包不完整——
部分包的 dist/ 目录是空的，主入口文件缺失，导致 require 报
"Cannot find module ... verify that the package.json has a valid main entry"。

npm 自己也会因为 package-lock 认为"已安装"而拒绝重装。
本脚本直接走 registry tarball，逐个下载解压，绕开 arborist 的依赖解析。

用法：python fix_deps.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.request
import zipfile

PROJECT = os.path.dirname(os.path.abspath(__file__))
NODE_MODULES = os.path.join(PROJECT, "node_modules")
REGISTRY = "https://registry.npmjs.org"
MIRROR = "https://registry.npmmirror.com"



def resolve_entry(pkg_dir: str, meta: dict) -> str | None:
    """解析包的入口文件路径。

    现代包可能不写 main，只用 exports 字段（ESM 风格）：
      {"main": "./dist/index.js"}
      {"exports": {"default": "./dist/index.js"}}
      {"exports": {".": {"require": "./dist/index.cjs", "default": "./dist/index.js"}}}
    这里把三种写法都试一遍，任一命中即认为包完整。
    """
    candidates: list[str] = []

    main = meta.get("main")
    if main:
        candidates.append(main)

    exports = meta.get("exports")
    if isinstance(exports, str):
        candidates.append(exports)
    elif isinstance(exports, dict):
        def collect(node):
            if isinstance(node, str):
                candidates.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    collect(v)
        collect(exports)

    # 补充常见默认位置
    candidates.extend(["index.js", "./index.js", "dist/index.js", "./dist/index.js"])

    seen = set()
    for raw in candidates:
        if not isinstance(raw, str) or raw in seen:
            continue
        seen.add(raw)
        rel = raw.replace("\\", "/")
        # 跳过通配与条件导出（无法静态判定）
        if "*" in rel or rel.startswith("."):
            continue
        rel = rel.lstrip("./").lstrip("/")
        if not rel:
            continue
        variants = [rel]
        if not rel.endswith(".js"):
            variants.append(rel + ".js")
            variants.append(rel + "/index.js")
        for c in variants:
            p = os.path.join(pkg_dir, c.replace("/", os.sep))
            if os.path.isfile(p):
                return p
    return None


def find_missing() -> list[str]:
    """扫描 node_modules，找出主入口文件缺失的包。"""
    missing = []
    for name in sorted(os.listdir(NODE_MODULES)):
        if name.startswith("."):
            continue
        scope_dir = os.path.join(NODE_MODULES, name)
        if not os.path.isdir(scope_dir):
            continue
        if name.startswith("@"):
            packages = [f"{name}/{s}" for s in sorted(os.listdir(scope_dir))]
        else:
            packages = [name]
        for pkg in packages:
            pkg_dir = os.path.join(NODE_MODULES, pkg.replace("/", os.sep))
            manifest = os.path.join(pkg_dir, "package.json")
            if not os.path.exists(manifest):
                continue
            try:
                meta = json.load(open(manifest, encoding="utf-8"))
            except Exception:
                continue
            if resolve_entry(pkg_dir, meta) is None:
                missing.append(pkg)
    return missing


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_tarball(meta: dict) -> str | None:
    """从 registry 元数据里挑一个 tarball URL（优先官方源）。"""
    versions = meta.get("versions", {})
    if not versions:
        return None
    # 取最新版本
    latest = meta.get("dist-tags", {}).get("latest")
    if latest not in versions:
        latest = sorted(versions.keys())[-1]
    dist = versions[latest].get("dist", {})
    tarball = dist.get("tarball")
    if not tarball:
        return None
    # 把官方源换成镜像源（更快更稳）
    return tarball.replace(REGISTRY, MIRROR)


def extract_tarball(url: str, dest: str) -> None:
    """下载 tarball 并解压到目标目录（剥掉 package/ 前缀）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.name.startswith("package/"):
                continue
            rel = member.name[len("package/"):]
            if not rel:
                continue
            target = os.path.join(dest, rel.replace("/", os.sep))
            # 防目录穿越
            if not os.path.abspath(target).startswith(os.path.abspath(dest)):
                continue
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with open(target, "wb") as out:
                out.write(src.read())


def install_one(pkg: str) -> bool:
    """补装单个包。返回是否成功。"""
    dest = os.path.join(NODE_MODULES, pkg.replace("/", os.sep))
    try:
        meta = fetch_json(f"{REGISTRY}/{pkg.replace('/', '%2f')}")
    except Exception as exc:
        print(f"  [FAIL] {pkg}: 取元数据失败 {exc}")
        return False
    tarball = pick_tarball(meta)
    if not tarball:
        print(f"  [FAIL] {pkg}: 没有 tarball")
        return False
    try:
        extract_tarball(tarball, dest)
    except Exception as exc:
        print(f"  [FAIL] {pkg}: 解压失败 {exc}")
        return False
    # 校验主入口
    manifest = os.path.join(dest, "package.json")
    try:
        meta_local = json.load(open(manifest, encoding="utf-8"))
    except Exception:
        print(f"  [FAIL] {pkg}: 解压后没有 package.json")
        return False
    if resolve_entry(dest, meta_local) is None:
        print(f"  [FAIL] {pkg}: 入口仍缺失")
        return False
    print(f"  [OK] {pkg}")
    return True


def main() -> int:
    print("扫描缺失包…")
    missing = find_missing()
    if not missing:
        print("没有缺失的包，无需修复")
        return 0
    print(f"发现 {len(missing)} 个包主入口缺失：")
    for p in missing:
        print("  -", p)

    ok = 0
    failed = []
    for i, pkg in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {pkg}")
        if install_one(pkg):
            ok += 1
        else:
            failed.append(pkg)
        time.sleep(0.3)

    print()
    print(f"成功 {ok} / {len(missing)}")
    if failed:
        print("失败：", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
