"""G1 门禁：id 映射层的「静默 return」扫描（2026-10-15 id 口径专项审计）。

背景：`IDMapper.to_uuid()` 返回 Optional[str]，7 个调用点中曾有 **5 处**在映射
缺失时静默放弃（裸 return / 无留痕新建 uuid）——用户看到"操作成功"而数据没动
（实测：tasks.delete 静默失效、toggle 完成态静默无效、frontmatter 数据分裂）。

本门禁绑定规则（本次审计确立）：
  返回 Optional 的 id 查询，调用方必须二选一——
  ① 留痕（logger.warning 等）  ② 如实返回失败（return False/None）
  **禁止裸 return**（既不留痕也不报失败）。

判据（行扫描，与代码形态解耦到"允诺集合"）：
  对每个 `if not <var>:` 分支，取其后的前 3 个非空行，若其中既无 logger.*
  也无 `return False` / `return None` / `continue` / `continue` 之类明确语义，
  即判违规。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

REPO_DIRS = [
    APP_ROOT / "data" / "repositories",
    APP_ROOT / "repositories",
]

# 触发模式：id 映射缺失判定
TRIGGER = re.compile(r"^\s*if\s+not\s+(uuid|src_uuid|target_uuid|[a-z_]*uuid)\s*:\s*$")
# 允诺信号：留痕 或 明确语义返回
ALLOWED = (
    "logger.",
    "return False",
    "return None",
    "continue",
    "raise ",
    "return {}",  # 空 dict 亦属"如实空结果"（调用方可判）
)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    lines = path.read_text(encoding="utf-8", errors="ignore").split("\n")
    for i, line in enumerate(lines):
        if not TRIGGER.match(line):
            continue
        # 收集**整个 if 块**（缩进更深/相同的续行，直到缩进回落到同级），
        # 跳过纯注释行——否则"兜底生成 3 行后再 logger"的合法形态会假红。
        base_indent = len(line) - len(line.lstrip())
        block: list[str] = []
        for ln in lines[i + 1 :]:
            if not ln.strip():
                continue
            indent = len(ln) - len(ln.lstrip())
            if indent <= base_indent:
                break
            if not ln.strip().startswith("#"):
                block.append(ln)
        body = " ".join(block)
        if not any(sig in body for sig in ALLOWED):
            violations.append((i + 1, line.strip()))
    return violations


class TestNoSilentReturnOnIdMapping:
    def test_no_silent_return_in_repositories(self) -> None:
        """所有仓储层不得在 id 映射缺失时裸 return（必须留痕或如实失败）。"""
        all_violations: list[str] = []
        for d in REPO_DIRS:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.py")):
                for lineno, snippet in _scan_file(f):
                    all_violations.append(f"{f.relative_to(APP_ROOT)}:{lineno}  {snippet}")
        assert not all_violations, "检测到 id 映射缺失时的静默 return（应加 logger.warning 或如实返回失败）:\n  " + "\n  ".join(all_violations)

    def test_scanner_selfcheck_catches_synthetic_violation(self, tmp_path) -> None:
        """自证：合成违规片段必须被扫到（防扫描器失效导致门禁假绿）。"""
        bad = tmp_path / "synthetic_bad.py"
        bad.write_text(
            "def f(x):\n    uuid = mapper.to_uuid(x)\n    if not uuid:\n        return\n    return uuid\n",
            encoding="utf-8",
        )
        hits = _scan_file(bad)
        assert hits, "扫描器未能捕获合成违规（门禁失效）"

    def test_scanner_selfcheck_accepts_fixed_shapes(self, tmp_path) -> None:
        """自证：三种合法形态（留痕 / 如实返回 / 循环跳过）都不应误报。"""
        good = tmp_path / "synthetic_good.py"
        good.write_text(
            "def a(x):\n"
            "    uuid = mapper.to_uuid(x)\n"
            "    if not uuid:\n"
            "        logger.warning('missing %s', x)\n"
            "        return\n"
            "    return uuid\n"
            "\n"
            "def b(x):\n"
            "    uuid = mapper.to_uuid(x)\n"
            "    if not uuid:\n"
            "        return False\n"
            "    return True\n"
            "\n"
            "def c(items):\n"
            "    out = []\n"
            "    for it in items:\n"
            "        uuid = mapper.to_uuid(it)\n"
            "        if not uuid:\n"
            "            continue\n"
            "        out.append(uuid)\n"
            "    return out\n",
            encoding="utf-8",
        )
        assert _scan_file(good) == [], f"误报合法形态: {_scan_file(good)}"


if __name__ == "__main__":
    for d in REPO_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            for line, sn in _scan_file(f):
                print(f"{f}:{line}  {sn}")


# ── G2 门禁：id 生成式唯一性（禁纯秒级时间戳）────────────────────────
# 背景：`siyi_task_{int(now_ms/1000)}` 秒级精度 → 同秒创建任务+提醒即撞 id
# （真实库实测两条不同条目共享同一 id，删除误伤/漏删）。已修为毫秒+随机。

_SECOND_LEVEL_ID = re.compile(
    r"f\"siyi_(?:task|note)_{int\([^)]*/\s*1000\)\}|"  # siyi_task_{int(now_ms / 1000)}
    r"siyi_(?:task|note)_{int\(time\.time\(\)\)\}"
)


class TestG2IdGeneratorsUnique:
    def test_no_second_level_id_generators(self) -> None:
        """id 生成式禁用纯秒级时间戳（同秒创建即撞 id）。"""
        violations = []
        scan_roots = [APP_ROOT / "data" / "repositories", APP_ROOT / "integrations"]
        for root in scan_roots:
            if not root.is_dir():
                continue
            for f in sorted(root.glob("*.py")):
                src = f.read_text(encoding="utf-8", errors="ignore")
                for i, ln in enumerate(src.split("\n"), 1):
                    if _SECOND_LEVEL_ID.search(ln):
                        violations.append(f"{f.relative_to(APP_ROOT)}:{i}  {ln.strip()[:90]}")
        assert not violations, "检测到秒级 id 生成式（同秒创建即撞 id，应毫秒+随机）:\n  " + "\n  ".join(violations)

    def test_scanner_selfcheck_catches_second_level(self, tmp_path) -> None:
        """自证：秒级生成式必须被扫到。"""
        bad = tmp_path / "synthetic.py"
        bad.write_text('uuid = f"siyi_task_{int(now_ms / 1000)}"\n', encoding="utf-8")
        src = bad.read_text(encoding="utf-8")
        assert _SECOND_LEVEL_ID.search(src), "扫描器未能捕获秒级生成式"


# ── G4 尖兵：deepseek 出处必须用本地会话 id（治本不回潮）──────────────


class TestG4DeepseekSourceIdentity:
    def test_extract_graph_prefers_local_session_id(self) -> None:
        """_extract_graph 的出处优先 local_session_id（治本 2026-10-15）。"""
        src = (APP_ROOT / "services" / "ai_chat_sync.py").read_text(encoding="utf-8")
        assert "local_session_id" in src, "_extract_graph 应回填/使用本地会话 id（治本）"
        assert "deepseek:{conv.get('id')" not in src.replace("local_sid if local_sid else (conv.get('id') or conv.get('title') or '')", ""), (
            "出处仍直接使用平台 conv.id（孤儿指针回潮）"
        )

    def test_importer_backfills_local_session_id(self) -> None:
        """chat_importer 两处导入路径都必须回填 local_session_id。"""
        src = (APP_ROOT / "integrations" / "chat_importer.py").read_text(encoding="utf-8")
        count = src.count('conv["local_session_id"] = session_id')
        assert count >= 2, f"chat_importer 应有 2 处回填（两导入路径），实得 {count}"


# ── G3 门禁：handler id 参数标注 vs 仓储签名一致性 ────────────────────
# 背景：inbox.update 标注 `entry_id: str` 而仓储 `get_entry(entry_id: int)`
# → 按标注传串的调用方全部静默失败（T1 实测）。本门禁扫描所有仓储 id 方法
# 的参数标注，找出 handler 层「只标注 str 但仓储要 int」的不一致点。
# 白名单：已人工核对安全/已修复的条目。

_G3_HANDLER_DIRS = ["backend/handlers"]
_G3_REPO_DIRS = ["repositories", "data/repositories"]
_ID_PARAM = re.compile(
    r"async def (\w+)\(\s*(?:self,?\s*)?(\w*id)\w*\s*:\s*(int|str|int \| str|str \| int)",
    re.IGNORECASE | re.MULTILINE,
)
# handler 里这些 id 参数名的「危险标注」（纯 str——仓储若要 int 就会静默失败）
_DANGEROUS_STR_ONLY = re.compile(r":\s*str\s*=\s*\"\"")


class TestG3HandlerRepoIdTypeConsistency:
    def test_repo_strict_int_methods_are_known(self) -> None:
        """登记：仓储层「只吃 int」的 id 方法清单（新增需评审）。"""
        repo_strict = self._collect_repo_strict_int()
        # 2026-10-15 审计基线：get_entry 是唯一严格 int 的（其余均为宽容 union）。
        # 新增条目 = 新增了一个"按 str 传参会静默失败"的契约点，需评审并
        # 在 handler 层做类型归一（参照 inbox.update 的修复）。
        known = {"get_entry"}
        unknown = repo_strict - known
        assert not unknown, f"发现新的「只吃 int」仓储方法，需评审: {sorted(unknown)}"

    def test_no_str_only_handler_id_params_for_strict_repos(self) -> None:
        """inbox 域（get_entry 只吃 int）的 handler 不得只标 `entry_id: str`。

        2026-10-15 精确化：只扫**使用 personal_inbox_repository** 的文件——
        audit/memory 域的 entry_id 本就是 uuid 字符串（str 标注正确），不误报。
        """
        violations = []
        for d in _G3_HANDLER_DIRS:
            root = APP_ROOT / d
            if not root.is_dir():
                continue
            for f in sorted(root.rglob("*.py")):
                src = f.read_text(encoding="utf-8", errors="ignore")
                if "personal_inbox_repository" not in src:
                    continue
                for m in re.finditer(r"async def (\w+)\([^)]*\bentry_id: str\b", src):
                    violations.append(f"{f.relative_to(APP_ROOT)}:{src[:m.start()].count(chr(10)) + 1}  {m.group(1)}(entry_id: str)")
        assert not violations, (
            "inbox 域 handler 的 entry_id: str 标注（仓储 get_entry 只吃 int，"
            "按标注传参会静默失败——参照 _inbox_crud.inbox_update 的归一修法）:\n  "
            + "\n  ".join(violations)
        )

    @staticmethod
    def _collect_repo_strict_int() -> set[str]:
        """收集仓储层「id 参数标注为纯 int」的方法名。"""
        strict: set[str] = set()
        for d in _G3_HANDLER_DIRS and _G3_REPO_DIRS:
            root = APP_ROOT / d
            if not root.is_dir():
                continue
            for f in sorted(root.glob("*.py")):
                src = f.read_text(encoding="utf-8", errors="ignore")
                for m in _ID_PARAM.finditer(src):
                    name, param, anno = m.group(1), m.group(2), m.group(3)
                    if anno == "int" and param.endswith("_id"):
                        strict.add(name)
        return strict
