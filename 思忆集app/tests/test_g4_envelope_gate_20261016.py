"""B1 止血门禁：except 块内裸 return（错误信封统一 · 2026-10-16 用户拍板方案 B）。

背景（错误信封三形态量化）：dict 信封 success:false 452 处为主流；
最危险的是**异常处理器内裸 return []/{} /None**（64 处实测）——错误被
吞成"没数据"，前端无法区分空与失败，reminder.list 恒空（D1）即此土壤。

用户裁决：不全量迁移（方案 A 否），双层收敛——
  B1（本门禁）：except 内裸 return **禁止新增**，按文件计数 ratchet
     （行号漂移免疫；历史治理后下调基线）；
  B2（后续按域分批）：64 处历史逐点 triage——失败路径改
     {"success": False, "error": ...}，真·空数据加 ENVELOPE-OK 注释豁免，
     每点核对前端消费方（帧守卫批次同款纪律）。

合法形态（不计数）：return 带键 dict（success/error 信封）、raise、
返回非空容器/变量/调用、行内 ENVELOPE-OK 注释（逐点显式豁免）。
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ("backend/handlers", "services")

# 2026-10-16 实测基线（111 处；修正 return None=Constant(None) 漏抓后口径）。
# B2 历史治理每清一处，对应文件计数下调。新增任何一处裸 return → 超基线 → 红。
#
# B2 triage 结论（2026-10-16，handlers 域 40 处全量人工分类）：
#   全部为 best-effort 降级的正确模式，0 处需行为修改——
#   ① 解析 helper（_to_ms/_ts/fromisoformat/JSON）失败 return None = helper 语义；
#   ② timeline ×7「单源失败跳过该源」= 聚合视图正确设计（info log 完整）；
#   ③ info_proxy ×5 热榜/白名单解析失败 → []（源降级，log 完整）；
#   ④ inbox_knowledge ×4 反查 None = 「无详情」语义；⑤ chat_paths 流式 best-effort；
#   ⑥ 其余为 lazy-init 工厂/best-effort 增强。
#   已知残留风险（登记）：reminder.list 失败→[] = 用户闹钟静默消失（裸 list
#   契约锁死，升级需前端配合，暂以 warning log 可见）。
#   services 域 71 处同法待后续批次（预期结论相同：内部 best-effort）。
BASELINE: dict[str, int] = {
    "backend/handlers/__init__.py": 1,
    "backend/handlers/_ai_translate.py": 1,
    "backend/handlers/_chat_memory.py": 3,
    "backend/handlers/_external_content.py": 1,
    "backend/handlers/_feedback.py": 1,
    "backend/handlers/_inbox_crud.py": 2,
    "backend/handlers/_inbox_knowledge.py": 4,
    "backend/handlers/_info_proxy.py": 5,
    "backend/handlers/_narrative.py": 1,
    "backend/handlers/_notifications.py": 1,
    "backend/handlers/_reminder.py": 2,
    "backend/handlers/_timeline.py": 8,
    "backend/handlers/_transfer.py": 1,
    "backend/handlers/_word_extract.py": 1,
    "backend/handlers/_wordbook.py": 1,
    "backend/handlers/chat_paths/cross_session_recall.py": 1,
    "backend/handlers/chat_paths/gateway_path.py": 2,
    "backend/handlers/chat_paths/pydantic_agent_path.py": 3,
    "backend/handlers/chat_paths/simple_stream_path.py": 1,
    "services/_info_sources.py": 6,
    "services/agent_gateway/adapters.py": 1,
    "services/agent_gateway/observation.py": 2,
    "services/agent_gateway/routing.py": 1,
    "services/backup_service.py": 1,
    "services/clipboard_context.py": 2,
    "services/confirmation_flow.py": 1,
    "services/confirmation_store.py": 1,
    "services/cron_actions.py": 1,
    "services/habit_learner.py": 1,
    "services/inbox_capture_service.py": 1,
    "services/info_fetchers/mixin.py": 4,
    "services/info_radar.py": 1,
    "services/info_source_registry.py": 1,
    "services/job_hunter/analyzer.py": 2,
    "services/job_hunter/matcher.py": 3,
    "services/job_hunter/scheduler.py": 4,
    "services/job_hunter/scrapers/boss.py": 2,
    "services/job_hunter/scrapers/job51.py": 2,
    "services/job_hunter/scrapers/lagou.py": 2,
    "services/job_hunter/scrapers/liepin.py": 2,
    "services/job_hunter/scrapers/zhilian.py": 2,
    "services/knowledge_synthesis.py": 1,
    "services/notification_dispatcher.py": 1,
    "services/ocr_service.py": 3,
    "services/personal_profile_extractor.py": 1,
    "services/proactive_triggers.py": 4,
    "services/rss_subscriber.py": 1,
    "services/screenshot_service.py": 1,
    "services/self_maintenance.py": 1,
    "services/skin_service.py": 1,
    "services/subagent_service.py": 2,
    "services/suggestion_engine.py": 5,
    "services/tooling/generic_tool_bridge.py": 1,
    "services/transcription_service.py": 3,
    "services/voice_input_service.py": 1,
    "services/weather_service.py": 2,
}


def _is_bare_return(node: ast.Return) -> bool:
    """裸 = return None / 空列表 / 空元组 / 空 dict（字面形态）。

    注意：``return None`` 的 AST value 是 Constant(None)，不是 None——
    首版漏抓此形态（测试当场抓出），已修。
    """
    v = node.value
    if v is None:
        return True
    if isinstance(v, ast.Constant) and v.value is None:
        return True
    if isinstance(v, (ast.List, ast.Tuple)):
        return not v.elts
    if isinstance(v, ast.Dict):
        return not v.keys
    return False


def _line_has_envelope_ok(src_lines: list[str], lineno: int) -> bool:
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(src_lines) and "ENVELOPE-OK" in src_lines[idx]:
            return True
    return False


def scan_bare_returns_in_except() -> dict[str, list[tuple[int, str]]]:
    """返回 {相对路径: [(行号, return 形态)]}——仅 except 块内裸 return。"""
    out: dict[str, list[tuple[int, str]]] = {}
    for d in _SCAN_DIRS:
        base = _ROOT / d
        for py in base.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(_ROOT).as_posix()
            src = py.read_text(encoding="utf-8")
            src_lines = src.splitlines()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            found: list[tuple[int, str]] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and _is_bare_return(sub):
                        kind = "None" if sub.value is None else type(sub.value).__name__
                        if _line_has_envelope_ok(src_lines, sub.lineno):
                            continue
                        found.append((sub.lineno, kind))
            if found:
                out[rel] = found
    return out


class TestEnvelopeRatchet:
    def test_no_new_bare_returns_per_file(self):
        current = {k: len(v) for k, v in scan_bare_returns_in_except().items()}
        regressions = []
        for f, n in current.items():
            base = BASELINE.get(f)
            if base is None:
                regressions.append(f"新文件出现裸 return：{f} x{n}")
            elif n > base:
                regressions.append(f"{f}: {base} -> {n}（新增 {n - base} 处）")
        assert not regressions, "except 内裸 return 只减不增（B1 止血）：\n" + "\n".join(regressions)

    def test_total_not_growing(self):
        total = sum(len(v) for v in scan_bare_returns_in_except().values())
        assert total <= sum(BASELINE.values()), f"裸 return 总数 {total} 超基线 {sum(BASELINE.values())}"

    def test_baseline_files_still_exist(self):
        """基线里的文件不得凭空消失（防扫描范围缩水）。"""
        for f in BASELINE:
            assert (_ROOT / f).exists(), f"基线文件缺失：{f}"


class TestGateSelfProof:
    def _scan_code(self, code: str) -> int:
        import tempfile

        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "demo.py"
            tmp.write_text(code, encoding="utf-8")
            with mock.patch("tests.test_g4_envelope_gate_20261016._SCAN_DIRS", [td]):
                # _ROOT 在模块级绑定，直接临时改模块属性
                import tests.test_g4_envelope_gate_20261016 as g

                old_root = g._ROOT
                g._ROOT = Path(td)
                try:
                    return sum(len(v) for v in g.scan_bare_returns_in_except().values())
                finally:
                    g._ROOT = old_root

    def test_violation_caught(self):
        code = "def f():\n    try:\n        pass\n    except Exception:\n        return []\n"
        assert self._scan_code(code) == 1

    def test_none_and_empty_dict_caught(self):
        code = (
            "def f():\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        return None\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        return {}\n"
        )
        assert self._scan_code(code) == 2

    def test_envelope_dict_ok(self):
        code = "def f():\n    try:\n        pass\n    except Exception as exc:\n        return {'success': False, 'error': str(exc)}\n"
        assert self._scan_code(code) == 0

    def test_envelope_ok_comment_exempt(self):
        code = "def f():\n    try:\n        pass\n    except Exception:\n        # ENVELOPE-OK: 可选数据，缺失即无\n        return None\n"
        assert self._scan_code(code) == 0

    def test_bare_return_outside_except_ok(self):
        code = "def f(items):\n    if not items:\n        return []\n    return items\n"
        assert self._scan_code(code) == 0

    def test_return_value_call_ok(self):
        code = "def f():\n    try:\n        pass\n    except Exception:\n        return degrade('x', 'y')\n"
        assert self._scan_code(code) == 0
