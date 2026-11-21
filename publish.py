# -*- coding: utf-8 -*-
"""创建 GitHub 仓库并推送。

用法（推荐）：
    python publish.py <TOKEN>

流程：
1. 调 GitHub API 创建仓库 tanghui2/transcribe-cli（公开）
2. 设置 description 与 topics
3. 配置 remote 并推送 main 分支
4. 开启 Issues 与 Discussions

需要 classic token 且带 public_repo scope（fine-grained token 无法创建新仓库）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO_NAME = "transcribe-cli"
OWNER = "tanghui2"
DESCRIPTION = (
    "Local audio/video transcription, summarization, translation and subtitle "
    "generation — fully offline, zero API cost."
)
TOPICS = [
    "whisper", "faster-whisper", "local-llm", "ollama", "transcription",
    "subtitles", "srt", "offline", "privacy", "cuda", "cli", "chinese",
]
API = "https://api.github.com"


def api(token: str, method: str, path: str, payload: dict | None = None):
    """调用 GitHub API，返回解析后的 JSON。失败时打印详细错误并退出。"""
    url = f"{API}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "transcribe-cli-publish",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"\nAPI 失败 {method} {path}\n  HTTP {e.code}\n  {detail[:600]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"\n网络错误：{e}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    token = sys.argv[1].strip()
    if not token.startswith(("ghp_", "github_pat_")):
        print("token 格式不对，应以 ghp_ 或 github_pat_ 开头", file=sys.stderr)
        return 2

    # 1. 确认 token 有效并识别账号
    user = api(token, "GET", "/user")
    login = user.get("login")
    print(f"认证成功：{login}")
    if login.lower() != OWNER.lower():
        print(f"警告：token 属于 {login}，但目标仓库在 {OWNER} 下", file=sys.stderr)

    # 2. 创建仓库（已存在则跳过）
    print(f"\n创建仓库 {OWNER}/{REPO_NAME} …")
    try:
        repo = api(token, "POST", "/user/repos", {
            "name": REPO_NAME,
            "description": DESCRIPTION,
            "public": True,
            "auto_init": False,
            "has_issues": True,
            "has_wiki": False,
        })
        print(f"  已创建（id={repo.get('id')}）")
    except SystemExit:
        # 422 通常意味着已存在，改用查询确认
        print("  创建失败，检查是否已存在 …")
        repo = api(token, "GET", f"/repos/{OWNER}/{REPO_NAME}")
        print(f"  仓库已存在（id={repo.get('id')}）")

    # 3. 设置描述与 topics
    print("\n设置描述与 topics …")
    api(token, "PATCH", f"/repos/{OWNER}/{REPO_NAME}", {
        "description": DESCRIPTION,
        "topics": TOPICS,
        "has_issues": True,
        "has_wiki": True,
        "has_discussions": True,
    })
    print(f"  description: {DESCRIPTION}")
    print(f"  topics: {', '.join(TOPICS)}")

    # 4. 推送代码
    print("\n推送代码 …")
    project_dir = r"E:\新建文件夹\知乎AI课程录播\.workbuddy\prototype\transcribe-cli"
    remote = f"https://{token}@github.com/{OWNER}/{REPO_NAME}.git"

    subprocess.run(["git", "remote", "remove", "origin"], cwd=project_dir, check=False)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=project_dir, check=True)

    push = subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if push.returncode != 0:
        print(push.stdout)
        print(push.stderr, file=sys.stderr)
        return 1
    print("  推送成功")

    # 5. 还原干净的 remote（不带 token，避免凭据留在 .git/config）
    subprocess.run(["git", "remote", "set-url", "origin",
                    f"https://github.com/{OWNER}/{REPO_NAME}.git"], cwd=project_dir, check=False)

    # 6. 打 tag
    print("\n创建 v0.1.0 tag …")
    subprocess.run(["git", "tag", "-a", "v0.1.0", "-m", "v0.1.0 首个版本"], cwd=project_dir, check=False)
    subprocess.run(["git", "push", "origin", "v0.1.0"], cwd=project_dir, check=False,
                   capture_output=True, text=True)

    # 7. 验证
    final = api(token, "GET", f"/repos/{OWNER}/{REPO_NAME}")
    print("\n=== 完成 ===")
    print(f"  URL:    {final.get('html_url')}")
    print(f"  描述:   {final.get('description')}")
    print(f"  Topics: {', '.join(final.get('topics', []))}")
    print(f"  Issues: {'开' if final.get('has_issues') else '关'}")
    print(f"  Discussions: {'开' if final.get('has_discussions') else '关'}")
    print()
    print("后续建议（见 发布清单.md）：")
    print("  1. 打 Release：把 dist/transcribe-cli.exe 作为 v0.1.0 附件上传")
    print("  2. 补 30 秒演示录屏 + 对比图到 README 顶部")
    print("  3. 冷启动：Reddit r/CommandLine、r/selfhosted、Product Hunt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
