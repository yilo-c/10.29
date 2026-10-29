"""G4 完整版门禁：引用口径全量扫描（2026-10-16）。

背景：今日 5 个缺陷（deepseek 孤儿指针 / inbox.update / tasks.delete /
id 冲突 / 映射静默降级）全部是 id 口径与映射同族。G4 尖兵（test_id_silent_
return_gate_20261015 的 deepseek 断言）只盖单点；本门禁把"出处引用写入
必须显式声明口径"推广到全量写入侧。

规则：任何向 ``source_id`` 写入"裸 id"（裸变量 / 裸字段 / 裸调用结果，
无命名空间前缀、无域并行声明、无透传、无显式豁免）都判违规。合法形态：
  R1  f-string / 字符串字面量（生成式或带命名空间前缀，如 f"deepseek:{id}"）
  R2  透传（``x.source_id`` / ``x.get("source_id")``——继承既有口径，不新增）
  R3  同一调用含 ``source_kind=`` / ``source_type=``（域并行显式声明，
      含 dict 字面量内 "source"/"source_type" 键并存，如 PD-3 设计）
  R4  行内 ``G4-OK:`` 注释（逐点显式豁免 + 理由，评审可见）

门禁自带自证（合成违规必被捕获 + 各合法形态不误报），对仓库写入侧
（services/ backend/handlers/ ai/ knowledge/）断言违规为空。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ("services", "backend/handlers", "ai", "knowledge")
_TARGET = "source_id"
_DOMAIN_FLAGS = ("source_kind", "source_type", "source", "kind")
# R5：图谱关系端点豁免——graph_store.add_relation 的 source_id/target_id 是
# 图谱**边端点**（实体 id 空间，实体 id 自带类型前缀如 external_content_xxx），
# 与"域出处引用"同名词不同义，不适用本门禁。
_ENDPOINT_FUNCS = ("add_relation",)


@dataclass
class _Finding:
    file: str
    line: int
    snippet: str
    rule: str  # 违规原因

    def as_line(self) -> str:
        return f"{self.file}:{self.line} {self.rule} | {self.snippet}"


def _is_passthrough(node: ast.AST) -> bool:
    """R2：透传既有口径。

    覆盖：x.source_id / x.get('source_id', ...) / d['source_id'] /
    str(...)/int(...) 包装 / BoolOp（a.get('source_id') or b.get('id')）
    / 同名 Name 转发（source_id=source_id，值来自同函数内已判定的赋值）。
    """
    if isinstance(node, ast.Name) and node.id == _TARGET:
        return True
    if isinstance(node, ast.Attribute) and node.attr == _TARGET:
        return True
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value == _TARGET:
            return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == _TARGET
    ):
        return True
    # 包装：str()/int() 首参递归；无参字符串清洗方法（strip/lower 等）递归接收者；
    # BoolOp 任一腿透传即透传
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("str", "int") and node.args:
        return _is_passthrough(node.args[0])
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and not node.args
        and node.func.attr in ("strip", "lstrip", "rstrip", "lower", "upper", "title")
    ):
        return _is_passthrough(node.func.value)
    if isinstance(node, ast.BoolOp):
        return any(_is_passthrough(v) for v in node.values)
    return False


def _value_is_literalish(node: ast.AST) -> bool:
    """R1：f-string / 字符串字面量（生成式或带前缀）。"""
    return isinstance(node, (ast.JoinedStr, ast.Constant))


def _host_has_domain_decl(host: ast.AST) -> bool:
    """R3：宿主节点（Call/Dict）内并行声明了域。"""
    if isinstance(host, ast.Call):
        return any(kw.arg in _DOMAIN_FLAGS and kw.value is not None for kw in host.keywords if kw.arg)
    if isinstance(host, ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value in _DOMAIN_FLAGS for k in host.keys)
    return False


def _line_has_g4_ok(src_lines: list[str], lineno: int) -> bool:
    """R4：命中行或其上一行有 G4-OK 豁免注释。"""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(src_lines) and "G4-OK" in src_lines[idx]:
            return True
    return False


def _check_value(
    value: ast.AST,
    host: ast.AST,
    src_lines: list[str],
    lineno: int,
) -> str | None:
    """返回违规原因，合法返回 None。"""
    if _value_is_literalish(value):
        return None
    if _is_passthrough(value):
        return None
    if _host_has_domain_decl(host):
        return None
    if _line_has_g4_ok(src_lines, lineno):
        return None
    try:
        seg = ast.get_source_segment("".join(l + "\n" for l in src_lines), value) or ""
    except Exception:
        seg = ""
    return f"裸 id 写入 source_id（无前缀/域声明/透传/G4-OK）：{seg.strip()[:60]}"


def _scan_source(src: str, filename: str) -> list[_Finding]:
    """扫描单文件源码，返回违规清单。"""
    src_lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    findings: list[_Finding] = []

    def _emit(value: ast.AST, host: ast.AST, lineno: int) -> None:
        # R5：图谱边端点调用整体豁免（同名词不同义，见 _ENDPOINT_FUNCS 注释）
        if isinstance(host, ast.Call) and isinstance(host.func, ast.Attribute):
            if host.func.attr in _ENDPOINT_FUNCS:
                return
        reason = _check_value(value, host, src_lines, lineno)
        if reason:
            findings.append(
                _Finding(
                    file=filename,
                    line=lineno,
                    snippet=src_lines[lineno - 1].strip()[:80] if lineno - 1 < len(src_lines) else "",
                    rule=reason,
                )
            )

    for node in ast.walk(tree):
        # 形态 1：关键字参数 source_id=...
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == _TARGET:
                    _emit(kw.value, node, kw.value.lineno)
        # 形态 2：dict 字面量 "source_id": ...
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == _TARGET:
                    _emit(v, node, v.lineno)
        # 形态 3：赋值 source_id = ...
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == _TARGET for t in node.targets):
                _emit(node.value, node, node.lineno)
    return findings


def _scan_repo() -> list[_Finding]:
    findings: list[_Finding] = []
    for d in _SCAN_DIRS:
        base = _ROOT / d
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            findings.extend(_scan_source(py.read_text(encoding="utf-8"), str(py.relative_to(_ROOT))))
    return findings


# ── 自证 ─────────────────────────────────────────────────────


class TestGateSelfProof:
    def test_synthetic_violation_caught(self):
        code = "def f(note):\n    entry = build(source_id=note.get('id'))\n"
        out = _scan_source(code, "synthetic.py")
        assert len(out) == 1
        assert "synthetic.py" in out[0].as_line()

    def test_bare_name_violation_caught(self):
        code = "article_id = 'x'\nprops = {'source_id': article_id}\n"
        out = _scan_source(code, "synthetic.py")
        assert len(out) == 1

    def test_fstring_literal_ok(self):
        code = "entry = build(source_id=f'deepseek:{sid}')\nlit = {'source_id': 'fixed_key'}\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_passthrough_ok(self):
        code = "a = build(source_id=req.source_id)\nb = build(source_id=item.get('source_id', ''))\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_domain_parallel_ok(self):
        code = (
            "e = build(source_id=note.get('id'), source_type='note')\n"
            "d = {'source_id': article_id, 'source': source}\n"
            "k = extract(source_id=item_id, source_kind='inbox')\n"
        )
        assert _scan_source(code, "synthetic.py") == []

    def test_g4_ok_comment_ok(self):
        code = "# G4-OK: 既有口径，级联清理依赖裸 id\nprops = {'source_id': article_id}\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_unrelated_dict_key_not_flagged(self):
        code = "d = {'other_id': raw}\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_name_forward_ok(self):
        # source_id = f"..."（R1 已合法）后转发 keyword → 放行
        code = "source_id = f'deepseek:{sid}'\nbuild(source_id=source_id)\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_boolop_and_str_wrapper_ok(self):
        code = "sid = m.get('entry_id') or m.get('id', '')\nd = {'source_id': str((p or {}).get('source_id') or '').strip()}\n"
        # sid 本身是 .get 透传 BoolOp → 合法；str 包装透传 → 合法
        assert _scan_source(code, "synthetic.py") == []

    def test_subscript_ok(self):
        code = "d = {'source_id': parsed['source_id']}\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_kind_parallel_ok(self):
        # 解析归一层：kind 与 source_id 并行输出
        code = "d = {'kind': kind, 'source_id': ident}\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_add_relation_endpoint_exempt(self):
        # R5：图谱边端点（同名词不同义）
        code = "gs.add_relation(\n    source_id=article_entity_id,\n    target_id=src_entity,\n    relation_type='sourced_from',\n)\n"
        assert _scan_source(code, "synthetic.py") == []

    def test_non_forward_name_still_violation(self):
        # 不同名裸变量依旧违规（转发豁免不扩大化）
        code = "note_id = 123\nbuild(source_id=note_id)\n"
        out = _scan_source(code, "synthetic.py")
        assert len(out) == 1


# ── 仓库实扫 ─────────────────────────────────────────────────


class TestRepoClean:
    def test_no_bare_reference_writes(self):
        findings = _scan_repo()
        assert not findings, "发现裸 id 引用写入（须加命名空间/域声明/透传或 G4-OK 豁免）：\n" + "\n".join(f.as_line() for f in findings)

    def test_known_good_sites_present(self):
        """既有合法写入点仍在（防门禁范围意外缩水）。"""
        src = (_ROOT / "services" / "ai_chat_sync.py").read_text(encoding="utf-8")
        assert 'source_id = f"deepseek:' in src
