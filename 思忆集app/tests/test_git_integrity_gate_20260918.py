"""批2.5「事故防复发」—— git 仓库完整性门禁 + bundle 兜底脚本的契约测试。

## 为什么有这批测试

2026-09-18 主仓 `.git` 对象库被静默清空，**批1 的 7 个原子提交对象永久丢失**；
同一次事故还静默清掉了 `refs/heads/main`。两次都是**靠偶然**才发现的：
前者靠一条恰好失败的 `git stash`，后者靠"新 bundle 与历史 bundle 的 ref 清单不一致"
的交叉比对（`git status` 干净、`git log` 正常，什么都看不出来）。

→ 对象库/ref 完整性此前**零机器门禁**。本文件把"**真出事时门禁必须红**"固化为断言
（AGENTS.md 守则 #7：修复侧优先把结论固化为机器断言 / 先红后绿 PoC）。

**所有用例都在临时仓库上制造事故形态，绝不碰真实仓库的 `.git`。**

## 刻意避免的坑（项目已有先例）

路径一律用 `Path(__file__)` 反推**绝对路径**，**不用相对路径** —— 本仓测试曾因
`Path("backend/...")` 这类相对写法而对 cwd 产生隐含依赖（从仓库根跑就挂、从
`思忆集app/` 跑才过），已由 2026-09-18 的独立复核捕获（`clean-review-batch2-2026-09-18.md`）。
**测试必须与 cwd 无关。**

另：删除文件一律走 `_unlink()`，它以**盘面**为判据（文件是否真的没了），因为本环境
有平台级"安全删除 shim"会在文件已实际移走后仍抛 `OSError`。
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# 思忆集app/tests/<本文件> → parents[0]=tests, [1]=思忆集app, [2]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check_git_integrity.py"
BUNDLE_TOOL = REPO_ROOT / "scripts" / "make_bundle.py"
REF_REPAIR = REPO_ROOT / "scripts" / "git_ref_repair.py"

# 2026-09-18 事故现场的真实 fsck 输出（删节版，保留每一种错误形态各至少一条）。
# 分类器必须把它正确分桶 —— 这是"门禁红了到底红在哪"的唯一依据。
SAMPLE_FSCK = """
dangling tree 51d393466e1db86dcdd1f24201e86a7d50041ee6
error: HEAD: invalid reflog entry f771c5e550984d75f2cd325013cf296ac218fe9a
error: HEAD: invalid reflog entry 4be9de92fd81ecb550b2c2e9b7602d3c44f5f1c4
error: refs/heads/fix/p0-2026-06-18: invalid reflog entry f771c5e550984d75f2cd325013cf296ac218fe9a
error: refs/heads/fix/p0-2026-06-18: invalid reflog entry 4be9de92fd81ecb550b2c2e9b7602d3c44f5f1c4
missing blob 1c44243beb7771f4ba48580ad924544bb4535964
missing commit 1505ddfa479780f647c8502284a36af6e365ff95
error: refs/heads/main: invalid sha1 pointer 99801562366ba21b55ce7a18eac2a838a4efb002
"""


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _make_repo(tmp_path: Path) -> Path:
    """造一个最小临时 git 仓库（1 个提交，分支 main）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0, "git init 失败"
    # 显式钉默认分支名：不同 git 版本默认是 master / main，用 symbolic-ref 直接写，
    # 不依赖 `git init -b`（旧版不支持）。
    assert _git(repo, "symbolic-ref", "HEAD", "refs/heads/main").returncode == 0
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    assert _git(repo, "add", "a.txt").returncode == 0
    # 显式关签名：全局 gitconfig 可能开了 gpgsign，会让临时仓库的 commit 失败
    assert _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init").returncode == 0
    return repo


def _unlink(path: Path) -> None:
    """删除文件并以**盘面**为判据（平台安全删除 shim 会在文件已移走后仍抛 OSError）。

    注意：git 的 loose 对象在 Windows 上是**只读**的（mode 444），直接 unlink 会
    PermissionError 且**文件仍在** —— 故先 chmod 加写权限。这是本用例实测踩到的坑。
    """
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass
    try:
        path.unlink()
    except OSError:
        pass
    assert not path.exists(), f"预期文件已被删除但仍在：{path}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"无法加载模块：{path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 一、fsck 输出分类器（把"红在哪"钉死）
# ---------------------------------------------------------------------------


def test_scan_fsck_classifies_incident_shapes() -> None:
    """分类器必须把 2026-09-18 事故的真实 fsck 输出正确分桶，否则门禁红了也说不清原因。"""
    mod = _load_module(GATE, "_cgi_under_test")
    counts = mod._scan_fsck(SAMPLE_FSCK)
    assert counts["invalid_reflog_entries"] == 4, counts
    assert counts["missing"] == 2, counts
    assert counts["invalid_pointer"] == 1, counts
    assert counts["dangling"] == 1, counts


def test_scan_fsck_ignores_dangling_only() -> None:
    """只有 dangling（等待 gc 的悬空对象）时，阻断计数必须为 0 —— 否则正常仓库会假红。"""
    mod = _load_module(GATE, "_cgi_under_test")
    counts = mod._scan_fsck("dangling tree " + "a" * 40 + "\n")
    assert counts["missing"] == 0
    assert counts["invalid_pointer"] == 0
    assert counts["invalid_reflog_entries"] == 0


# ---------------------------------------------------------------------------
# 二、门禁在临时仓库上的红/绿（先红后绿 PoC）
# ---------------------------------------------------------------------------


def test_gate_green_on_healthy_repo(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    assert _run(GATE, "--repo", str(repo), "--baseline", str(baseline), "--update").returncode == 0
    proc = _run(GATE, "--repo", str(repo), "--baseline", str(baseline))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "通过" in proc.stdout


def test_gate_red_when_required_ref_deleted(tmp_path: Path) -> None:
    """★ 事故形态②：`refs/heads/main` 被静默清除。

    这正是 2026-09-18 的第二处损失 —— `git status` 干净、`git log` 正常，
    **只有专门对账 ref 才能发现**。本用例就是那次的回归防线。
    """
    repo = _make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    _run(GATE, "--repo", str(repo), "--baseline", str(baseline), "--update")

    _unlink(repo / ".git" / "refs" / "heads" / "main")

    proc = _run(GATE, "--repo", str(repo), "--baseline", str(baseline))
    assert proc.returncode == 1, "ref 被清除后门禁必须红（这正是当初漏掉的那一步）"
    assert "refs/heads/main" in proc.stdout


def test_gate_red_when_ref_points_to_missing_object(tmp_path: Path) -> None:
    """★ 事故形态①：ref 指向已消失的对象（原事故里 refs 就是这个样子）。"""
    repo = _make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    _run(GATE, "--repo", str(repo), "--baseline", str(baseline), "--update")

    (repo / ".git" / "refs" / "heads" / "main").write_text("0" * 40 + "\n", encoding="ascii")

    proc = _run(GATE, "--repo", str(repo), "--baseline", str(baseline))
    assert proc.returncode == 1
    assert "阻断" in proc.stdout


def test_gate_red_when_object_missing(tmp_path: Path) -> None:
    """★ 事故形态③：对象库真的缺对象（`missing <type> <sha>`）。"""
    repo = _make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    _run(GATE, "--repo", str(repo), "--baseline", str(baseline), "--update")

    loose = [p for p in (repo / ".git" / "objects").rglob("*") if p.is_file() and len(p.parent.name) == 2 and len(p.name) == 38]
    assert loose, "临时仓库应当有 loose 对象"
    _unlink(loose[0])

    proc = _run(GATE, "--repo", str(repo), "--baseline", str(baseline))
    assert proc.returncode == 1
    assert "missing" in proc.stdout or "阻断" in proc.stdout


def test_gate_red_when_new_invalid_reflog_exceeds_baseline(tmp_path: Path) -> None:
    """棘轮语义：失效 reflog 条目**新增**即失败（存量已登记，只允许下降）。"""
    repo = _make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    _run(GATE, "--repo", str(repo), "--baseline", str(baseline), "--update")

    # 伪造一条指向**不存在对象**的 reflog 记录 → 新增失效条目。
    # 必须用**非零**伪 SHA：全零是 git 的"空 OID"，在 reflog 里是合法的首次创建标记，
    # fsck 不会报错（本用例首版就用了全零，实测红不了，故记于此）。
    logs = repo / ".git" / "logs" / "refs" / "heads" / "main"
    logs.parent.mkdir(parents=True, exist_ok=True)
    bogus = "deadbeef" * 5
    logs.write_text(
        f"{bogus} {bogus} t <t@example.com> 1700000000 +0800\tcommit: bogus\n",
        encoding="utf-8",
    )

    proc = _run(GATE, "--repo", str(repo), "--baseline", str(baseline))
    assert proc.returncode == 1
    assert "reflog" in proc.stdout


def test_gate_update_writes_baseline(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    assert _run(GATE, "--repo", str(repo), "--baseline", str(baseline), "--update").returncode == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["blocking"] == 0
    assert "refs/heads/main" in data["required_refs"]


def test_gate_skips_outside_git_repo(tmp_path: Path) -> None:
    """非 git 目录 → 跳过而非假红。

    门禁一旦在无 git 环境常红，就会被当成"环境性欠债"忽略掉，反而削弱它在本机的信号价值
    （本项目已有先例：ruff/mypy 分居两解释器导致必然红一道，多个会话把假红上报成欠债）。
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    proc = _run(GATE, "--repo", str(plain), "--baseline", str(tmp_path / "b.json"))
    assert proc.returncode == 0
    assert "跳过" in proc.stdout


def test_gate_passes_on_this_repo() -> None:
    """本仓自身必须健康。

    **本用例红了就说明真出事了**（对象库缺对象 / ref 丢失），而不是测试写错 —— 这正是它存在的意义。
    """
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("本环境无 git 可用")
    proc = _run(GATE, "--baseline", str(REPO_ROOT / "scripts" / "git_integrity_baseline.json"))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# 三、bundle 兜底脚本：ref 清单交叉对账
# ---------------------------------------------------------------------------


def test_make_bundle_creates_and_reconciles(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    out = tmp_path / "out"
    proc = _run(BUNDLE_TOOL, "--repo", str(repo), "--out-dir", str(out), "--tag", "t")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ref 对账通过" in proc.stdout
    assert list(out.glob("*.bundle")), "应当产出 bundle 文件"


def test_audit_bundle_detects_lost_ref(tmp_path: Path) -> None:
    """★ 事故形态②的**检出方式**：拿历史 bundle 对账当前仓库，找出"曾经有、现在没了"的 ref。

    这正是 2026-09-18 发现 `refs/heads/main` 丢失的那个动作。
    """
    repo = _make_repo(tmp_path)
    out = tmp_path / "out"
    assert _run(BUNDLE_TOOL, "--repo", str(repo), "--out-dir", str(out), "--tag", "before").returncode == 0
    bundle = next(out.glob("*.bundle"))

    ok = _run(BUNDLE_TOOL, "--repo", str(repo), "--audit-bundle", str(bundle))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "对账通过" in ok.stdout

    _unlink(repo / ".git" / "refs" / "heads" / "main")

    bad = _run(BUNDLE_TOOL, "--repo", str(repo), "--audit-bundle", str(bundle))
    assert bad.returncode == 1, "历史 bundle 里有的 ref 在仓库里没了 → 必须报 LOST"
    assert "ref 丢失" in bad.stdout
    assert "refs/heads/main" in bad.stdout


def test_audit_bundle_reports_forward_movement_without_failing(tmp_path: Path) -> None:
    """分支正常前进**不是故障** → 只报不红（避免把业务推进变成噪声）。"""
    repo = _make_repo(tmp_path)
    out = tmp_path / "out"
    assert _run(BUNDLE_TOOL, "--repo", str(repo), "--out-dir", str(out), "--tag", "before").returncode == 0
    bundle = next(out.glob("*.bundle"))

    (repo / "b.txt").write_text("x\n", encoding="utf-8")
    assert _git(repo, "add", "b.txt").returncode == 0
    assert _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "second").returncode == 0

    proc = _run(BUNDLE_TOOL, "--repo", str(repo), "--audit-bundle", str(bundle))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "已前进" in proc.stdout


def test_prune_report_never_deletes(tmp_path: Path) -> None:
    """`--prune-keep` **只报告不删除** —— 存量 bundle 是唯一历史兜底，删除必须人工确认。"""
    repo = _make_repo(tmp_path)
    out = tmp_path / "out"
    for tag in ("a", "b", "c"):
        assert _run(BUNDLE_TOOL, "--repo", str(repo), "--out-dir", str(out), "--tag", tag).returncode == 0
    before = sorted(p.name for p in out.glob("*.bundle"))

    proc = _run(BUNDLE_TOOL, "--out-dir", str(out), "--prune-keep", "1")
    assert proc.returncode == 0
    assert "仅报告，未删除任何文件" in proc.stdout

    after = sorted(p.name for p in out.glob("*.bundle"))
    assert after == before, "报告模式绝不允许删除任何 bundle"


# ---------------------------------------------------------------------------
# 四、git_ref_repair 审计日志（全仓唯一直接写 ref 的地方）
# ---------------------------------------------------------------------------


def test_ref_repair_audit_line_format(tmp_path: Path) -> None:
    """审计日志必须记全 ref / 旧 SHA / 新 SHA / argv。

    动机：事故发生时**没有任何记录**能说明该脚本在什么时刻对哪些 ref 写了什么值，
    排查只能靠猜 —— 而它是全仓唯一直接写 ref 的地方，也是头号根因嫌疑。
    """
    log = tmp_path / "audit.log"
    mod = _load_module(REF_REPAIR, "_grr_under_test")
    mod.AUDIT_LOG = log

    mod._audit("fix/p0-2026-06-18", "b4eaeef5" + "0" * 32, "32c819df" + "0" * 32, note="repair")

    text = log.read_text(encoding="utf-8")
    assert "ref=refs/heads/fix/p0-2026-06-18" in text
    assert "old=b4eaeef5" in text
    assert "new=32c819df" in text
    assert "note=repair" in text


def test_ref_repair_audit_never_raises(tmp_path: Path) -> None:
    """审计通道自身不得成为故障源 —— 写不进去也必须静默放过。

    （同 `思忆集app/core/integrity.py::record` 的设计取舍：观测不得引入新的故障面。）
    """
    mod = _load_module(REF_REPAIR, "_grr_under_test2")
    mod.AUDIT_LOG = tmp_path  # 指向一个**目录**：任何平台下 open(..., "a") 都会抛 OSError
    mod._audit("main", "a", "b")  # 不应抛异常
