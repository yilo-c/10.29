"""`tests/_fake_settings.py` 的契约测试 + **blanket mock 棘轮**（2026-09-18）。

## 背景（为什么要有这个门禁）

7 个测试文件长期用 `MagicMock() + get_setting.return_value = []` 伪造设置仓储。
这是**类型坍缩**的桩：任何 key 都返回 `[]`，于是字符串型配置被读成空列表，
"取默认值"这条路径被整个跳过。

2026-09-18 实测爆发：`chat_paths/helpers.py` 的预检走 `load_providers(settings_repo)`，
blanket `[]` 使 `get_setting(KEY_LLM_API_KEY, "")` 也返回 `[]` → 判定"未配置" →
`chat.send` 零帧返回 → **13 条用例红**，且报错文案与测试意图**毫不相干**。

本文件做两件事：
1. 钉住 `FakeSettingsRepository` 的契约（回落真源 / 不串味 / 显式覆盖优先）。
2. **棘轮**：`get_setting.return_value = []` 这类 blanket 桩的数量**只允许下降**
   （当前基线 0），新增即红 —— 防止这个形态再次扩散。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from _fake_settings import FakeSettingsRepository, configured_settings_repository
from core.settings_keys import (
    KEY_LLM_API_KEY,
    KEY_LLM_LOCAL_API_BASE,
    KEY_WEATHER_CITY,
    SETTINGS_DEFAULTS,
)

TESTS_DIR = Path(__file__).resolve().parent

# ── 棘轮基线：blanket list 桩的数量。当前 0（本批已清干净），新增即红。 ──
BLANKET_LIST_STUB_BASELINE = 0

# 有害形态：`get_setting.return_value = []`（一律返回列表，掩盖类型）
RE_BLANKET_LIST_STUB = re.compile(r"get_setting\.return_value\s*=\s*\[\s*\]")


# ─────────────────────────────────────────────────────────────────────────────
# 1. FakeSettingsRepository 契约
# ─────────────────────────────────────────────────────────────────────────────


def test_unknown_key_falls_back_to_caller_default() -> None:
    """未知 key 回落调用方给的 default（不凭空造值）。"""
    repo = FakeSettingsRepository()
    assert repo.get_setting("definitely_not_a_key", 42) == 42
    assert repo.get_setting("definitely_not_a_key", "x") == "x"


def test_known_key_falls_back_to_settings_defaults() -> None:
    """已知 key 回落 `SETTINGS_DEFAULTS` 真源 —— 不在测试里手写第二份默认值。"""
    repo = FakeSettingsRepository()
    assert repo.get_setting(KEY_WEATHER_CITY, "") == SETTINGS_DEFAULTS[KEY_WEATHER_CITY]
    # 关键：字符串型配置必须返回 **str**，不能是 blanket 桩的 []
    assert isinstance(repo.get_setting(KEY_LLM_API_KEY, ""), str)
    assert isinstance(repo.get_setting(KEY_LLM_LOCAL_API_BASE, ""), str)


def test_explicit_override_wins() -> None:
    repo = FakeSettingsRepository({KEY_LLM_API_KEY: "k-1"})
    assert repo.get_setting(KEY_LLM_API_KEY, "") == "k-1"


def test_set_then_get_roundtrip() -> None:
    repo = FakeSettingsRepository()
    repo.set_setting("some.key", {"a": 1})
    assert repo.get_setting("some.key") == {"a": 1}


def test_mutable_defaults_do_not_leak_between_instances() -> None:
    """★ 可变默认值必须深拷贝 —— 否则一个用例改脏 dict/list，会污染同进程后续用例。"""
    mutable_key = next(
        (k for k, v in SETTINGS_DEFAULTS.items() if isinstance(v, (dict, list))),
        None,
    )
    if mutable_key is None:  # 真源里没有可变默认值时退化为显式值测试
        r1 = FakeSettingsRepository({"k": {"x": 1}})
        got = r1.get_setting("k")
        got["x"] = 999
        assert r1.get_setting("k") == {"x": 1}
        return
    r1 = FakeSettingsRepository()
    r2 = FakeSettingsRepository()
    got = r1.get_setting(mutable_key)
    if isinstance(got, dict):
        got["__dirty__"] = True
    elif isinstance(got, list):
        got.append("__dirty__")
    assert r2.get_setting(mutable_key) == SETTINGS_DEFAULTS[mutable_key]
    assert r1.get_setting(mutable_key) == SETTINGS_DEFAULTS[mutable_key]


def test_configured_repo_declares_api_key() -> None:
    """`configured_settings_repository()` 必须让预检判定"已配置"。"""
    repo = configured_settings_repository()
    assert str(repo.get_setting(KEY_LLM_API_KEY, "") or "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# 2. 棘轮：blanket list 桩只降不升
# ─────────────────────────────────────────────────────────────────────────────


def _count_blanket_list_stubs() -> dict[str, int]:
    """用 **AST** 精确统计，而不是正则。

    正则会被两类假阳性污染：① 本文档/_fake_settings docstring 里的**示例代码**；
    ② 注释里引用的旧写法。AST 只认真正的赋值语句
    ``<...>.get_setting.return_value = []``，免疫字符串与注释。
    """
    hits: dict[str, int] = {}
    for py in TESTS_DIR.glob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue  # 语法坏的文件由别的门禁管，这里不重复报错
        n = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            # 赋值目标形如：Attribute(attr="return_value", value=Attribute(attr="get_setting"))
            for target in node.targets:
                if not isinstance(target, ast.Attribute) or target.attr != "return_value":
                    continue
                owner = target.value
                if (
                    isinstance(owner, ast.Attribute)
                    and owner.attr == "get_setting"
                    and isinstance(node.value, ast.List)
                    and not node.value.elts  # 空列表 = blanket 桩
                ):
                    n += 1
        if n:
            hits[py.name] = n
    return hits


def test_no_blanket_get_setting_list_stub() -> None:
    hits = _count_blanket_list_stubs()
    total = sum(hits.values())
    assert total <= BLANKET_LIST_STUB_BASELINE, (
        f"新增了 {total - BLANKET_LIST_STUB_BASELINE} 处 blanket 桩 "
        f"`get_setting.return_value = []`（类型坍缩，会让预检读到错误类型）。"
        f"明细：{hits}。请改用 tests/_fake_settings.py 的 FakeSettingsRepository。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. 结构性：预检读的是**类型正确**的仓储
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preflight_passes_when_api_key_configured() -> None:
    """已配置 API Key → 预检放行（返回 True），不发 not_configured 帧。"""
    from backend.handlers.chat_paths.helpers import preflight_ai_or_emit  # type: ignore[attr-defined]

    class _Ctx:
        def __init__(self, repo):
            self.settings = repo
            self.settings_repository = repo

    sent: list[tuple[str, str]] = []

    async def _send(text: str, kind: str = "delta") -> None:
        sent.append((text, kind))

    ok = await preflight_ai_or_emit(_Ctx(configured_settings_repository()), _send)  # type: ignore[arg-type]
    assert ok is True, f"已配置时预检应放行，实际发了：{sent}"
    assert not sent, f"放行时不得发任何帧：{sent}"


def test_preflight_symbol_exists() -> None:
    """防重命名漂移：预检函数必须能从 helpers 取到（上面用例依赖它）。"""
    from backend.handlers.chat_paths import helpers

    assert hasattr(helpers, "preflight_ai_or_emit"), (
        "helpers.preflight_ai_or_emit 不存在——若已改名请同步更新本文件用例，不要直接删掉这条断言（那会让门禁静默失效）。"
    )
