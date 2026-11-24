# -*- coding: utf-8 -*-
"""手工打包 Electron 的 app.asar。

⚠️ 优先用官方工具：`node node_modules/@electron/asar/bin/asar.js pack <src> <out>`
   —— 它会带上 integrity 校验字段，Electron 官方支持度最好。

本文件保留是因为本机沙箱有时会静默终止批量子进程（asar CLI 跑起来
直接没输出就退出），需要一个纯 Python 的兜底实现。

**格式必须与官方逐字节一致**，否则 Electron 报
"Failed to parse header from app.asar"，然后回退到 default_app.asar，
打印 Electron 用法说明后退出 —— 表现就是「双击没反应」。

用官方 @electron/asar 3.4.1 的产物反推出的规则：
    offset 0  = 4             pickle 长度基准
    offset 4  = json_len + 8  header 总长
    offset 8  = json_len + 4  对齐后的长度
    offset 12 = json_len      JSON 实际字节数
    offset 16 起 = JSON，紧接着就是文件内容

**内容起点 = 16 + json_len，不要额外加填充字节。**

我在这里踩了两次：
    ① v4 写成 json_len + 9（应为 + 8）
    ② JSON 后面多插了 pad 个 0x00（官方不插）
两次都会让 Electron 读不了这个 asar。

**教训：自研格式实现必须用官方工具的产物逐字节对照，
不能只做自洽的 round-trip（自己解自己永远是对的）。**

目录 JSON 结构：
    {"files": {"name": {"size": 123, "offset": "0", "files": {...}}}}
    目录节点没有 size/offset，只有 files。
"""

from __future__ import annotations

import json
import os
import struct
import sys


def _build_tree(root: str) -> dict:
    """构造 asar 的目录树。返回 {name: node}，node 是 dict。"""
    files: dict = {}
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if os.path.isdir(full):
            files[name] = {"files": _build_tree(full)}
        else:
            files[name] = {"size": os.path.getsize(full)}
    return files


def _assign_offsets(tree: dict, root: str, buf: bytearray) -> None:
    """递归给文件分配 offset，并把内容写进 buf。"""
    for name, node in tree.items():
        if "files" in node:
            _assign_offsets(node["files"], os.path.join(root, name), buf)
        else:
            node["offset"] = str(len(buf))
            with open(os.path.join(root, name), "rb") as f:
                buf.extend(f.read())


def pack(src_dir: str, out_path: str) -> None:
    src_dir = os.path.abspath(src_dir)
    tree = {"files": _build_tree(src_dir)}

    buf = bytearray()
    _assign_offsets(tree["files"], src_dir, buf)

    header_json = json.dumps(tree, separators=(",", ":")).encode("utf-8")

    # Electron 的 asar 头部规则（用 @electron/asar 3.4.1 的真实产物逐字节验证）：
    #   offset 0  = 4             pickle 长度基准
    #   offset 4  = json_len + 8  header 总长
    #   offset 8  = json_len + 4  对齐后的长度
    #   offset 12 = json_len      JSON 实际字节数
    #   offset 16 起 = JSON，紧接着就是文件内容
    #
    # **内容起点 = 16 + json_len，不要额外加填充字节。**
    # 官方产物实测：json_len=1288 → v0=4 v4=1296 v8=1292 v12=1288，
    # 内容起点 1304 = 16 + 1288。
    #
    # 写错的表现：Electron 报 "Failed to parse header from app.asar"，
    # 回退到 default_app.asar，打印用法说明后退出 —— 用户看到「双击没反应」。
    json_len = len(header_json)

    out = bytearray()
    out += struct.pack("<I", 4)
    out += struct.pack("<I", json_len + 8)
    out += struct.pack("<I", json_len + 4)
    out += struct.pack("<I", json_len)
    out += header_json
    out += buf

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(out)

    n = sum(1 for _, _, fs in os.walk(src_dir) for _ in fs)
    print(f"已打包 {n} 个文件 -> {out_path} ({len(out) / 1024:.1f} KB)")


def extract(asar_path: str, out_dir: str) -> None:
    """解包（用于验证）。"""
    with open(asar_path, "rb") as f:
        data = f.read()
    # offset 12 是 JSON 实际字节数；内容起点 = 16 + json_len（不额外加填充）
    json_len = struct.unpack("<I", data[12:16])[0]
    base = 16 + json_len
    tree = json.loads(data[16:16 + json_len].decode("utf-8"))

    def walk(node: dict, prefix: str) -> None:
        for name, child in node.get("files", {}).items():
            path = os.path.join(prefix, name)
            if "files" in child:
                os.makedirs(path, exist_ok=True)
                walk(child, path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                off = int(child.get("offset", "0"))
                size = child.get("size", 0)
                with open(path, "wb") as f:
                    f.write(data[base + off: base + off + size])

    os.makedirs(out_dir, exist_ok=True)
    walk(tree, out_dir)
    print(f"已解包 -> {out_dir}")


if __name__ == "__main__":
    if sys.argv[1] == "pack":
        pack(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "extract":
        extract(sys.argv[2], sys.argv[3])
    else:
        print("用法: asar_pack.py pack <src> <out> | extract <asar> <out>")
