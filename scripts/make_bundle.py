#!/usr/bin/env python3
"""打 git bundle 兜底快照，并**交叉校验 ref 清单**。

## 为什么需要它（2026-09-18 P0 事故的直接产物）

事故中主仓 `.git` 对象库被清空，**唯一**救命手段就是 bundle —— 恢复靠的是
`siyiji-local-backup-20260917-r2f.bundle`。但：

- **批1 的 7 个原子提交之所以永久丢失，恰恰因为它的提交诞生于最后一次 bundle 之后。**
  → 结论：bundle 必须"**入库即打**"，不能等下一个自然节点。
- 恢复时又发现第二处损失：`refs/heads/main` 也被清除了，而 `git status` 是干净的、
  `git log` 也正常，**看不出来**。它是靠"新打的 bundle 的 `list-heads`"与
  "历史 bundle 的 `list-heads`"**交叉比对**才暴露的。
  → 结论：打包时**必须顺带对账 ref 清单**，把"少了一个 ref"变成当场可见。

本脚本把这两条结论固化成一次操作。

## 用法

  python scripts/make_bundle.py                      # 打包到默认位置，命名含日期+时分
  python scripts/make_bundle.py --tag postb3         # 自定义标签
  python scripts/make_bundle.py --out-dir <dir>      # 自定义输出目录
  python scripts/make_bundle.py --list               # 只列出既有 bundle 与体积（不打包）
  python scripts/make_bundle.py --prune-keep 5       # **仅报告**超出保留窗口的旧 bundle（不删）
  python scripts/make_bundle.py --audit-bundle <b>   # 拿历史 bundle 对账当前仓库，找"曾经有、现在没了"的 ref
  python scripts/make_bundle.py --repo <dir> ...     # 指向别的仓库（仅供测试/PoC）

  返回值：0 = 成功且 ref 对账通过；1 = 失败（打包/校验不过 / ref 不一致 / ref 丢失）

## 刻意不做的事

**不自动删除旧 bundle。** 存量 bundle 是唯一的历史兜底（含本次事故里
"最新 bundle 之后"那段历史的最后副本），删除属不可逆操作，必须由人显式确认。
`--prune-keep` 只做**只读报告**，把"该清理哪些"摆出来，动手与否交给人。
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO.parent

# 可被 `--repo` 覆盖。唯一目的是让**ref 对账**这段核心逻辑能在临时仓库上做
# "先红后绿"PoC（在真实仓库上制造 ref 丢失既危险又不可逆）。默认永远指向本仓。
_REPO = REPO


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _refs_in_repo() -> dict[str, str]:
    """仓库当前全部 ref（`git show-ref`），返回 {ref: sha}。"""
    out = _git("show-ref").stdout
    refs: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            refs[parts[1].strip()] = parts[0]
    return refs


def _refs_in_bundle(path: Path) -> dict[str, str]:
    """bundle 内记录的 ref（`git bundle list-heads`），返回 {ref: sha}。

    注意：`list-heads` 会额外回一行 `HEAD`，它不是 `refs/` 下的引用，
    对账时要排除，否则会与 `show-ref` 产生结构性假差异。
    """
    out = _git("bundle", "list-heads", str(path)).stdout
    refs: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            ref = parts[1].strip()
            if ref.startswith("refs/"):
                refs[ref] = parts[0]
    return refs


def list_bundles(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("*.bundle"), key=lambda p: p.stat().st_mtime, reverse=True)


def cmd_list(out_dir: Path) -> int:
    items = list_bundles(out_dir)
    total = 0
    print(f"[bundle] 目录 {out_dir} 下共 {len(items)} 个 bundle：")
    for p in items:
        size = p.stat().st_size
        total += size
        ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {ts}  {size / 1048576:8.1f} MB  {p.name}")
    print(f"[bundle] 合计 {total / 1073741824:.2f} GB")
    return 0


def cmd_prune_report(out_dir: Path, keep: int) -> int:
    items = list_bundles(out_dir)
    stale = items[keep:]
    if not stale:
        print(f"[bundle] 无超出保留窗口（keep={keep}）的 bundle")
        return 0
    freed = sum(p.stat().st_size for p in stale)
    print(f"[bundle] 超出保留窗口（keep={keep} 最新）的 bundle 共 {len(stale)} 个，可释放 {freed / 1073741824:.2f} GB：")
    for p in stale:
        ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {ts}  {p.stat().st_size / 1048576:8.1f} MB  {p.name}")
    print("[bundle] **仅报告，未删除任何文件。** 删除属不可逆操作，请人工确认后再手动处理。")
    return 0


def cmd_audit_bundle(path: Path) -> int:
    """拿一个**既有 bundle** 的 ref 清单去对账**当前仓库**，找出"曾经有、现在没了"的 ref。

    这就是 2026-09-18 事故里发现第二处损失（`refs/heads/main` 被静默清除）的那个动作：
    `git status` 干净、`git log` 正常，什么都看不出来；只有"历史 bundle 里明明有这个 ref，
    仓库里却没了"这一交叉比对才能暴露。故把它固化成一条命令。
    """
    if not path.is_file():
        print(f"[bundle] 找不到 bundle：{path}")
        return 1
    repo_refs = _refs_in_repo()
    bundle_refs = _refs_in_bundle(path)
    if not bundle_refs:
        print(f"[bundle] 该 bundle 未记录任何 refs/ 引用（或读取失败）：{path}")
        return 1

    lost = {r: s for r, s in bundle_refs.items() if r not in repo_refs}
    diverged = {r: (bundle_refs[r], repo_refs[r]) for r in bundle_refs if r in repo_refs and bundle_refs[r] != repo_refs[r]}

    if lost:
        print(f"[bundle] **ref 丢失**：以下 ref 在 bundle 中存在、但仓库里已消失（{len(lost)} 个）：")
        for ref, sha in sorted(lost.items()):
            print(f"  [LOST] {ref}（bundle 记录 {sha[:12]}）")
        print("  处置：对象若尚存则用 scripts/git_ref_repair.py 双通道恢复；")
        print("  详见 deliverables/incident-2026-09-18-git-object-wipe.md（恢复第 10 步）。")
        return 1

    print(f"[bundle] ref 对账通过：bundle 记录的 {len(bundle_refs)} 个 ref 在仓库中均存在")
    if diverged:
        # 分支前进是正常业务，不是故障 —— 只报不红，避免把正常推进变成噪声。
        print(f"  （{len(diverged)} 个 ref 已前进/分叉，属正常，仅供参考：")
        for ref, (bun_sha, repo_sha) in sorted(diverged.items()):
            print(f"    {ref}: bundle {bun_sha[:12]} -> 仓库 {repo_sha[:12]}")
        print("  ）")
    return 0


def main() -> int:
    global _REPO

    argv = sys.argv[1:]
    if "--repo" in argv:
        _REPO = Path(argv[argv.index("--repo") + 1])
    if "--audit-bundle" in argv:
        return cmd_audit_bundle(Path(argv[argv.index("--audit-bundle") + 1]))
    if "--list" in argv:
        out_dir = Path(argv[argv.index("--out-dir") + 1]) if "--out-dir" in argv else DEFAULT_OUT_DIR
        return cmd_list(out_dir)

    out_dir = Path(argv[argv.index("--out-dir") + 1]) if "--out-dir" in argv else DEFAULT_OUT_DIR
    if "--prune-keep" in argv:
        keep = int(argv[argv.index("--prune-keep") + 1])
        return cmd_prune_report(out_dir, keep)

    tag = argv[argv.index("--tag") + 1] if "--tag" in argv else datetime.datetime.now().strftime("%H%M")
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"siyiji-local-backup-{stamp}-{tag}.bundle"

    # 1) 打包（--all = refs/ 下全部引用）
    proc = _git("bundle", "create", str(target), "--all")
    if proc.returncode != 0:
        print(f"[bundle] 打包失败：\n{(proc.stdout or '') + (proc.stderr or '')}")
        return 1

    # 2) verify（bundle 自身是否自洽）
    verify = _git("bundle", "verify", str(target))
    verify_out = (verify.stdout or "") + (verify.stderr or "")
    if verify.returncode != 0 or "is okay" not in verify_out:
        print(f"[bundle] verify 失败：\n{verify_out}")
        return 1

    # 3) ref 清单对账（本脚本存在的核心理由）
    #    "少了一个 ref" 在 git status 里完全看不见，只有这里能当场抓到。
    repo_refs = _refs_in_repo()
    bundle_refs = _refs_in_bundle(target)
    missing = {r: s for r, s in repo_refs.items() if r not in bundle_refs}
    mismatched = {r: (s, bundle_refs[r]) for r, s in repo_refs.items() if r in bundle_refs and bundle_refs[r] != s}

    size_mb = target.stat().st_size / 1048576
    print(f"[bundle] 已生成 {target}（{size_mb:.1f} MB，{len(bundle_refs)} 个 ref）")
    for ref, sha in sorted(bundle_refs.items()):
        print(f"  {sha[:12]}  {ref}")

    if missing or mismatched:
        print("[bundle] **ref 清单对账失败** —— bundle 未完整覆盖仓库引用：")
        for ref, sha in sorted(missing.items()):
            print(f"  [MISSING]  {ref}（仓库 {sha[:12]}，bundle 中无此 ref）")
        for ref, (repo_sha, bun_sha) in sorted(mismatched.items()):
            print(f"  [MISMATCH] {ref}（仓库 {repo_sha[:12]} vs bundle {bun_sha[:12]}）")
        print("  处置：这通常意味着 ref 在打包瞬间被并发改动；若持续复现，检查是否有")
        print("  外部进程在写 ref（本仓唯一直接写 ref 的脚本是 scripts/git_ref_repair.py，")
        print("  其审计日志见仓库同级目录 git-ref-repair.log）。")
        return 1

    print(f"[bundle] ref 对账通过：仓库 {len(repo_refs)} 个 ref 全部含于 bundle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
