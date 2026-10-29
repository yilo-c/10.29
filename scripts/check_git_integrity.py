#!/usr/bin/env python3
"""守卫：git 仓库完整性（对象库 + ref 清单）。

## 为什么建这道门禁（2026-09-18 P0 事故的直接产物）

事故：主仓 `.git` 的 **loose 对象全灭** + **`objects/pack/*.pack` 缺失**（只剩一个孤儿 `.idx`），
仓库从"可用"变为 `fatal: bad object HEAD`。**批1 的 7 个原子提交对象随之永久丢失**
（它们创建于最后一次 bundle 打包时刻之后，51 个 bundle 无一含之）。

更糟的是**发现方式**：不是门禁报的，而是"碰巧某条 `git stash` 命令报 `is not a valid object`"。
也就是说 —— **对象库完整性此前没有任何机器门禁**，损坏可以静默存在很久。

第二处损失更隐蔽：`refs/heads/main` 也被一并清除。`git status` 是**干净**的、工作树文件一个没少，
`git log` 也正常（HEAD 那条线还在）—— 只有把"新打的 bundle 的 `list-heads`"与
"历史 bundle 的 `list-heads`"**交叉比对**才暴露出来。

→ 故本门禁查**两件事**，缺一不可：
  1. **对象库**：`git fsck` 不得报 `missing` / `invalid sha1 pointer`；
  2. **ref 清单**：必需分支必须存在且指向合法 commit（`git status` 干净**不能**证明 ref 齐全）。

## 棘轮（ratchet）语义

`scripts/git_integrity_baseline.json` 存两类基线，**只允许下降**：
- `blocking`：阻断类错误计数（missing 对象 / 无效 sha1 指针）。目标恒为 0。
- `invalid_reflog_entries`：已知的历史失效 reflog 条目计数（指向早已丢失的
  `lost-found` 提交）。这类条目**无法在不重写 reflog 的前提下消除**，故给一个
  存量基线；一旦真实值超过基线（= 又出现了新的 ref 损坏）即失败。
- `required_refs`：必须存在的 ref 清单。

用法：
  python scripts/check_git_integrity.py            # 校验
  python scripts/check_git_integrity.py --update   # 把当前真实值写回基线（收紧/登记）
  python scripts/check_git_integrity.py --verbose  # 打印 fsck 原始输出分类明细
  python scripts/check_git_integrity.py --repo <dir> --baseline <file>   # 指向别的仓库（仅供测试/PoC）
  返回值：0 = 通过；1 = 不通过

## 已知噪声与判据（不要"优化"掉这些分支）

- `dangling <type> <sha>`：正常（等待 gc 的悬空对象），**忽略**。
  实测例：批量 `write-tree` 试错会留下 dangling tree。
- `error: <ref>: invalid reflog entry <sha>`：计入 `invalid_reflog_entries` 棘轮。
- `missing <type> <sha>`：**阻断**（对象真的没了）。
- `error: ...: invalid sha1 pointer ...`：**阻断**（ref 指向不存在的对象 = 本次事故的形态）。
- `desk-notes` 是 gitlink（独立子仓、无 `.gitmodules`），父仓 fsck 不校验其对象，**正常**。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(REPO_ROOT, "scripts", "git_integrity_baseline.json")

# 必须存在的 ref（除基线登记外，还会自动加入"当前分支"）。
# 动机：2026-09-18 事故中 refs/heads/main 被静默清除，而 git status 完全看不出来。
DEFAULT_REQUIRED_REFS = ["refs/heads/main"]

# fsck 输出分类
RE_MISSING = re.compile(r"^missing\s+\S+\s+[0-9a-f]{40}", re.M)
RE_INVALID_POINTER = re.compile(r"^error:.*invalid sha1 pointer", re.M)
RE_INVALID_REFLOG = re.compile(r"^error:.*invalid reflog entry\s+[0-9a-f]{40}", re.M)
RE_DANGLING = re.compile(r"^dangling\s+\S+\s+[0-9a-f]{40}", re.M)


# 可被 `--repo` / `--baseline` 覆盖。存在的唯一理由是**可测**：本门禁的价值全在
# "真出事时会红"，而在真实仓库上制造事故既危险又不可逆 → 必须能在临时仓库上做
# "先红后绿"PoC（AGENTS.md 守则 #7）。默认值永远指向本仓，正常调用不会偏离。
_REPO = REPO_ROOT
_BASELINE = BASELINE_PATH


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _load_baseline() -> dict:
    if not os.path.isfile(_BASELINE):
        return {}
    try:
        with open(_BASELINE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001 —— 基线损坏不该让门禁崩，按"无基线"处理并明示
        print(f"[git 完整性] 警告：基线文件无法解析（{exc}），按空基线继续")
        return {}


def _save_baseline(data: dict) -> None:
    with open(_BASELINE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _required_refs(baseline: dict) -> list[str]:
    """基线登记的必需 ref + 当前分支（当前分支永远必须有 ref，否则 HEAD 悬空）。"""
    refs = list(baseline.get("required_refs") or DEFAULT_REQUIRED_REFS)
    cur = _git("symbolic-ref", "--short", "HEAD").stdout.strip()
    if cur:
        ref = f"refs/heads/{cur}"
        if ref not in refs:
            refs.append(ref)
    return refs


def _check_refs(refs: list[str]) -> list[str]:
    """返回问题描述列表（空 = 全好）。"""
    problems: list[str] = []
    for ref in refs:
        proc = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if proc.returncode != 0 or not proc.stdout.strip():
            problems.append(f"必需 ref 缺失或未指向合法 commit：{ref}")
    # HEAD 自身也必须可解析（事故形态之一：HEAD 指向已消失的对象）
    head = _git("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if head.returncode != 0 or not head.stdout.strip():
        problems.append("HEAD 未指向合法 commit（`git rev-parse HEAD` 失败）")
    return problems


def _scan_fsck(out: str) -> dict:
    return {
        "missing": len(RE_MISSING.findall(out)),
        "invalid_pointer": len(RE_INVALID_POINTER.findall(out)),
        "invalid_reflog_entries": len(RE_INVALID_REFLOG.findall(out)),
        "dangling": len(RE_DANGLING.findall(out)),
    }


def main() -> int:
    global _REPO, _BASELINE

    argv = sys.argv[1:]
    update = "--update" in argv
    verbose = "--verbose" in argv
    if "--repo" in argv:
        _REPO = argv[argv.index("--repo") + 1]
    if "--baseline" in argv:
        _BASELINE = argv[argv.index("--baseline") + 1]

    # 无 git / 非 git 仓 → 无事可守。**不做假红**：门禁一旦在无 git 环境常红，
    # 就会被当成"环境性欠债"忽略掉，反而削弱它在本机的信号价值。
    probe = _git("rev-parse", "--git-dir")
    if probe.returncode != 0:
        print("[git 完整性] 跳过：当前目录不是 git 仓库（或无 git 可用）")
        return 0

    proc = _git("fsck", "--no-progress")
    out = (proc.stdout or "") + (proc.stderr or "")
    counts = _scan_fsck(out)

    if verbose:
        print("----- fsck 原始输出 -----")
        print(out.rstrip() or "(空)")
        print("-------------------------")

    baseline = _load_baseline()
    base_blocking = int(baseline.get("blocking", 0) or 0)
    base_reflog = int(baseline.get("invalid_reflog_entries", 0) or 0)
    refs = _required_refs(baseline)

    blocking = counts["missing"] + counts["invalid_pointer"]
    ref_problems = _check_refs(refs)

    if update:
        _save_baseline(
            {
                "blocking": blocking,
                "invalid_reflog_entries": counts["invalid_reflog_entries"],
                "required_refs": refs,
                "_comment": (
                    "git 完整性棘轮基线：blocking / invalid_reflog_entries 只允许下降；"
                    "required_refs 为必需存在的分支。由 scripts/check_git_integrity.py --update 生成。"
                ),
            }
        )
        print(f"[git 完整性] 基线已更新：blocking={blocking} invalid_reflog_entries={counts['invalid_reflog_entries']} required_refs={refs}")
        return 0

    failures: list[str] = []

    if blocking > 0:
        failures.append(
            f"对象库阻断类错误 {blocking} 处（missing={counts['missing']} "
            f"invalid sha1 pointer={counts['invalid_pointer']}）—— 这是 2026-09-18 P0 事故的形态"
        )
    if counts["invalid_reflog_entries"] > base_reflog:
        failures.append(f"失效 reflog 条目 {counts['invalid_reflog_entries']} > 基线 {base_reflog}（新增 = 又出现了指向已消失对象的 ref 记录）")
    failures.extend(ref_problems)

    if failures:
        print("[git 完整性] 失败：")
        for item in failures:
            print(f"  [FAIL] {item}")
        print()
        print("处置指南：")
        print("  1) 先做**非破坏性备份**（整份复制 .git 到仓外，不要原地操作）；")
        print("  2) 用最新 bundle 重建对象库：git bundle unbundle <最新 bundle>；")
        print("  3) 重钉分支引用：python scripts/git_ref_repair.py <sha> <branch>（commit-tree 后只跑它，勿补 update-ref）；")
        print("  4) 恢复后必须做 **ref 清单对账**（git show-ref vs 历史 bundle 的 list-heads）——git status 干净不能证明 ref 齐全；")
        print("  5) 详见 deliverables/incident-2026-09-18-git-object-wipe.md")
        return 1

    print(
        f"[git 完整性] 通过：对象库无阻断错误（blocking={blocking}≤基线 {base_blocking}，"
        f"失效 reflog={counts['invalid_reflog_entries']}≤基线 {base_reflog}，"
        f"dangling={counts['dangling']} 忽略）；{len(refs)} 个必需 ref 均在场"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
