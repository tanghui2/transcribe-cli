# -*- coding: utf-8 -*-
"""按 Node 的真实解析规则，找出并补齐 node_modules 里不完整的包。

背景：这台机器上 npm 的解包经常大面积失败（dist/ lib/ 目录缺失），
而 npm 自己会因为 lock 文件认为「已安装」而拒绝重装。

判断标准用 Node 的 createRequire —— 与实际消费者（electron-builder）
的解析规则一致，自己拼路径判断会连环误判。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tarfile
import urllib.request

MIRROR = "https://registry.npmmirror.com"
REGISTRY = "https://registry.npmjs.org"


def node_broken(root: str) -> list[tuple[str, str]]:
    """用 Node 的 require.resolve 找出无法解析的包。

    返回 [(包名, 包目录)]，同时把检测结果写到临时 JSON 供后续使用。
    """
    script = r"""
const fs = require('fs'), path = require('path');
const { createRequire } = require('module');
const root = process.argv[1];
const bad = [];
function check(dir) {
  let entries;
  try { entries = fs.readdirSync(dir); } catch (e) { return; }
  for (const name of entries) {
    if (name.startsWith('.')) continue;
    const full = path.join(dir, name);
    let st;
    try { st = fs.statSync(full); } catch (e) { continue; }
    if (!st.isDirectory()) continue;
    if (name.startsWith('@')) {
      let subs;
      try { subs = fs.readdirSync(full); } catch (e) { continue; }
      for (const s of subs) checkPkg(path.join(full, s));
      continue;
    }
    checkPkg(full);
  }
}
function checkPkg(dir) {
  const pj = path.join(dir, 'package.json');
  if (!fs.existsSync(pj)) return;
  let m;
  try { m = JSON.parse(fs.readFileSync(pj, 'utf8')); } catch (e) { return; }
  const main = m.main || 'index.js';
  try {
    createRequire(path.join(dir, 'package.json')).resolve(main);
  } catch (e) {
    bad.push(dir);
  }
}
check(root);
console.log(JSON.stringify(bad));
"""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_check_broken.js")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

    node = "node"
    r = subprocess.run([node, script_path, os.path.abspath(root)],
                       capture_output=True, text=True)
    try:
        dirs = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        print("检测脚本输出解析失败:", r.stdout[:300], r.stderr[:300])
        return []
    return [(os.path.relpath(d, root).replace("\\", "/"), d) for d in dirs]


def fetch_and_extract(pkg: str, dest: str) -> bool:
    """下载 tarball 并用 robocopy 覆盖到 dest。"""
    encoded = pkg.replace("/", "%2f")
    try:
        req = urllib.request.Request(f"{REGISTRY}/{encoded}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"    [FAIL] 元数据 {exc}")
        return False

    versions = meta.get("versions", {})
    if not versions:
        return False
    latest = meta.get("dist-tags", {}).get("latest")
    if latest not in versions:
        latest = sorted(versions.keys())[-1]
    tarball = versions[latest].get("dist", {}).get("tarball")
    if not tarball:
        return False
    tarball = tarball.replace(REGISTRY, MIRROR)

    try:
        req = urllib.request.Request(tarball, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
    except Exception as exc:
        print(f"    [FAIL] 下载 {exc}")
        return False

    extract_dir = dest + "__x"
    os.makedirs(extract_dir, exist_ok=True)
    with tarfile.open(fileobj=__import__("io").BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.name.startswith("package/"):
                continue
            rel = member.name[len("package/"):]
            if not rel:
                continue
            target = os.path.join(extract_dir, rel.replace("/", os.sep))
            if not os.path.abspath(target).startswith(os.path.abspath(extract_dir)):
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

    for root, dirs, files in os.walk(dest):
        for f in files:
            try:
                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass
    os.makedirs(dest, exist_ok=True)
    subprocess.run(
        ["robocopy", os.path.join(extract_dir, "package"), dest,
         "/E", "/IS", "/IT", "/R:1", "/W:1",
         "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP"],
        capture_output=True,
    )
    return True


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else "node_modules"
    print("用 Node 真实解析规则检测…")
    broken = node_broken(root)
    print(f"发现 {len(broken)} 个包无法解析")
    if not broken:
        return 0

    fixed, failed = 0, []
    for i, (pkg, d) in enumerate(broken, 1):
        print(f"[{i}/{len(broken)}] {pkg}")
        if fetch_and_extract(pkg, d):
            fixed += 1
            print("    [OK]")
        else:
            failed.append(pkg)

    print(f"\n补齐 {fixed} / {len(broken)}")
    if failed:
        print("失败:", ", ".join(failed[:10]))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
