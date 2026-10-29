#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复本沙箱 git 分支引用写入异常（commit 成功但 ref 不落盘）。

背景（2026-08-24 发现，2026-08-29 复现）：
    在本环境 `git commit` / `git update-ref` 对分支 ref 的更新会静默丢失：
    提交对象本身创建成功（可从 SHA 直接 `git log`），但 `refs/heads/<branch>`
    仍停留在 `packed-refs` 中的旧值，导致 `git rev-parse HEAD` 回退到旧提交。
    疑似沙箱内并发 repack 与 packed-refs 写竞争所致，根因未完全查明。

用法：
    python scripts/git_ref_repair.py <new_commit_sha> [<branch>]

    不带参数时，脚本会尝试自动推断：取 `git log --all --oneline` 中最新但未被
    任何 ref 覆盖的提交（即刚 commit 却未挂到分支上的那个）。

行为：
    1. 校验 SHA 为存在的 commit 对象；
    2. 写入 loose ref `.git/refs/heads/<branch>`（loose 优先于 packed-refs，
       后续 commit 走 loose 更新路径更稳）；
    3. 同步改写 `.git/packed-refs` 中该分支那一行（loose 若再次丢失仍有正确值）；
    4. 复验 `git rev-parse HEAD`，输出 OK / FAIL。

退出码：0 成功；1 参数或对象错误；2 修复后复验仍不一致。
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 审计日志（2026-09-18 P0 事故后新增）
#
# 动机：本脚本是**全仓唯一直接写 ref 的地方**（loose ref + packed-refs 双通道），
# 因此是 2026-09-18「.git 对象库被清空」事故的头号根因嫌疑——但事故发生时，
# **没有任何记录**能说明本脚本在什么时刻、对哪些 ref、写了什么值。排查只能靠猜。
#
# 落点选择（重要）：写到**仓库外**（默认 `REPO.parent / git-ref-repair.log`，
# 即与 bundle 同级的 D:\h\3.25\），**不是** `.git/` 内。理由：本次事故把 `.git`
# 整个清空了，写在 `.git/` 里的日志会随事故一起消失——恰好在最需要它的场景失效。
#
# 环境变量名刻意**不用 `SIYI_` 前缀**：`scripts/gen_inventory.py` 会以
# `SIYI_[A-Z0-9_]+` 正则对 `思忆集app/**/*.py` 做**文本级**扫描（连注释都入账），
# 虽然当前不扫 `scripts/`，但项目已两次因"某处写出 SIYI_ 字面量"造成清单污染
# （见 .workbuddy/memory/2026-09-18.md「入库时踩到并修掉的真红」），故不冒这个险。
# ---------------------------------------------------------------------------
AUDIT_LOG = Path(os.environ.get("GIT_REF_REPAIR_LOG") or (REPO.parent / "git-ref-repair.log"))


def _audit(branch: str, old: str, new: str, note: str = "") -> None:
    """追加一行审计记录。**永不抛出**——审计通道自身不得成为故障源。"""
    try:
        ts = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        argv = " ".join(sys.argv[1:])
        line = f"{ts}\tref=refs/heads/{branch}\told={old or '(none)'}\tnew={new}\targv=[{argv}]"
        if note:
            line += f"\tnote={note}"
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.strip()


def _object_type(sha: str) -> str:
    return _git("cat-file", "-t", sha)


def current_branch() -> str:
    branch = _git("symbolic-ref", "--short", "HEAD")
    if not branch:
        raise SystemExit("HEAD 处于 detached 状态，请显式指定分支名")
    return branch


def _reachable_from_any_ref(sha: str) -> bool:
    """判断 sha 是否已可从任意 ref 到达（是则不是"孤立提交"）。

    注意：不能只比对 ref 尖端（`show-ref --hash`），那样任何祖先提交都会被
    误判为孤立（2026-08-29 踩坑：脚本因此把 HEAD 从最新提交回退了一格）。
    """
    refs = [line.split()[0] for line in _git("show-ref", "--hash").splitlines() if line.strip()]
    for ref in refs:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, ref],
            cwd=str(REPO),
            capture_output=True,
        )
        if proc.returncode == 0:
            return True
    return False


def resolve_orphan_commit() -> str:
    """推断刚 commit 但未挂到任何 ref 上的提交。

    只取 reflog 中最近的 commit 记录，且必须确实无法从任何 ref 到达；
    否则说明分支引用正常，无需修复（避免把 HEAD 往回改）。
    """
    for line in _git("reflog", "--format=%H %gs").splitlines():
        sha, _, subject = line.partition(" ")
        if not sha or "commit" not in subject:
            continue
        if _object_type(sha) != "commit":
            continue
        if _reachable_from_any_ref(sha):
            print(f"[SKIP] {sha[:8]} 已可从现有 ref 到达，分支引用正常，无需修复")
            return ""
        return sha
    raise SystemExit("未能自动推断孤立提交，请显式传入 SHA")


def repair(sha: str, branch: str) -> bool:
    git_dir = REPO / ".git"
    if not git_dir.is_dir():
        git_dir = Path(_git("rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = REPO / git_dir

    full_sha = _git("rev-parse", sha)
    old_sha = _git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")

    # 1) loose ref（优先于 packed-refs）
    # 注意：必须按字节写 LF，Windows 文本模式会把 \n 转成 \r\n，
    # git 无法解析 CRLF 的 packed-refs（会报 unexpected line）。
    loose = git_dir / "refs" / "heads" / branch
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_bytes((full_sha + "\n").encode("ascii"))

    # 2) packed-refs 同步改写（同样强制 LF，并顺带把既有 CRLF 归一化）
    packed = git_dir / "packed-refs"
    if packed.exists():
        raw = packed.read_bytes().replace(b"\r\n", b"\n")
        text = raw.decode("utf-8", errors="replace")
        pattern = re.compile(rf"^[0-9a-f]{{40}}[ \t]+refs/heads/{re.escape(branch)}$", re.M)
        if pattern.search(text):
            text = pattern.sub(f"{full_sha} refs/heads/{branch}", text)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += f"{full_sha} refs/heads/{branch}\n"
        packed.write_bytes(text.encode("utf-8"))

    # 3) 复验
    ok = _git("rev-parse", "HEAD") == full_sha
    # 注意：复验基准是 HEAD。恢复**非当前分支**时此处必然 False（写操作本身已成功），
    # 2026-09-18 恢复 refs/heads/main 时就踩过这一点 —— 故如实记进审计日志。
    _audit(branch, old_sha, full_sha, note="repair" if ok else "repair (HEAD 复验不一致：非当前分支属预期)")
    return ok


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    branch = current_branch()

    if len(args) >= 2:
        sha, branch = args[0], args[1]
    elif len(args) == 1:
        sha = args[0]
    else:
        sha = resolve_orphan_commit()

    if not sha:
        # 自动推断判定"分支引用正常，无需修复"
        _audit(branch, _git("rev-parse", "HEAD"), "", note="skip: 分支引用正常，无需修复")
        return 0

    if _object_type(sha) != "commit":
        print(f"[FAIL] {sha} 不是有效的 commit 对象")
        _audit(branch, "", sha, note="fail: 目标不是 commit 对象")
        return 1

    head_before = _git("rev-parse", "HEAD")
    if head_before.startswith(sha):
        print(f"[OK] HEAD 已指向 {sha[:8]}，无需修复")
        _audit(branch, head_before, sha, note="no-op: HEAD 已指向目标")
        return 0

    ok = repair(sha, branch)
    head_after = _git("rev-parse", "HEAD")
    if ok:
        print(f"[OK] 已修复：{branch} {head_before[:8]} -> {head_after[:8]}（loose ref + packed-refs 均已写入）")
        return 0
    print(f"[FAIL] 修复后仍不一致：HEAD={head_after[:8]} 期望={sha[:8]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
