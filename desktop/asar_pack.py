# -*- coding: utf-8 -*-
"""手工打包 Electron 的 app.asar。

为什么不用 @electron/asar 的命令行：本机沙箱会静默终止批量子进程，
asar CLI 跑起来直接没输出就退出。asar 的格式其实很简单，
自己实现反而更可控。

格式：
    4 字节  uint32 = 4（header 里 pickle 的长度基准）
    4 字节  uint32 = header 总长度
    4 字节  uint32 = header 内容长度（JSON 的 UTF-8 字节数）
    4 字节  uint32 = 0（对齐填充）
    N 字节  JSON 目录
    ...     文件内容（按 offset 顺序紧密排列）

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

    # Electron 的 asar 头部规则（用真实产物反推验证过）：
    #   offset 0  = 4                pickle 长度基准
    #   offset 4  = json_len + 9     header 总长
    #   offset 8  = json_len + 5     对齐后的长度
    #   offset 12 = json_len         JSON 实际字节数（不含填充）
    #   offset 16 起 = JSON，末尾按 4 字节对齐
    #
    # 例（官方产物）：json_len=6983 → pad=1 → v4=6992 / v8=6988 / v12=6983
    # 内容起点 = 16 + json_len + pad
    json_len = len(header_json)
    pad = 4 - (json_len % 4)
    header_json += b"\x00" * pad

    out = bytearray()
    out += struct.pack("<I", 4)
    out += struct.pack("<I", json_len + 9)
    out += struct.pack("<I", json_len + 5)
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
    # offset 12 是 JSON 实际字节数；内容起点 = 16 + json_len + pad
    json_len = struct.unpack("<I", data[12:16])[0]
    pad = 4 - (json_len % 4)
    base = 16 + json_len + pad
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
