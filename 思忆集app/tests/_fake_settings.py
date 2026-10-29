"""测试用「类型正确的假设置仓储」——**一处定义，全测试复用**（2026-09-18）。

## 为什么要建这个（治本动机，不是图省事）

`test_handlers_chat.py` 等 7 个文件长期用这种写法伪造设置仓储：

```python
self.settings_repository = MagicMock()
self.settings_repository.get_setting.return_value = []
```

**这是"类型坍缩"的假件**：它让 `get_setting(任何 key, 任何 default)` 一律返回 `[]`。
于是「字符串型配置」（如 `llm_api_key`）被读成空列表，调用方 `str(x or "").strip()`
恰好也得到 `""` —— 表面能跑，实际把"取默认值"这条路径彻底跳过。

后果在 2026-09-18 实测爆发：`chat_paths/helpers.py` 的**预检**
（无 API Key 时快速发 `not_configured` 分型）走 `load_providers(settings_repo)`，
而 blanket mock 使 `get_setting(KEY_PROVIDER_CONFIGS, [])` 与
`get_setting(KEY_LLM_API_KEY, "")` **返回同一个 `[]`**，预检判定"未配置" →
`chat.send` 零帧返回 → **13 条用例红**，且报错文案是"AI 服务尚未配置"，
与测试的真实意图（验证流式链路）**毫不相干**，排查成本极高。

同一 bug 形态（`MagicMock` blanket return_value 掩盖类型）在本仓已出现多例，
故按"同一形态 ≥2 次建基元"的约定收敛为本模块。

## 设计

* 默认值**回落真源** `core.settings_keys.SETTINGS_DEFAULTS`，不在测试里手写第二份
  —— 生产新增默认值时测试自动跟上，不会出现"测试默认值与生产漂移"。
* 仍支持显式 overrides（测试按需声明"已配置 API Key"之类的场景）。
* 深拷贝返回，**避免测试间共享可变默认值**（dict/list 型配置被某个用例改脏
  会污染同进程后续用例——这类串味极难查）。

## 门禁

`思忆集app/tests/test_fake_settings_contract_20260918.py` 对
`get_setting.return_value = []` 这类 blanket mock 做**棘轮**（当前基线 0，
新增即红），防止这个形态再次扩散。
"""

from __future__ import annotations

import copy
from typing import Any

from core.settings_keys import SETTINGS_DEFAULTS


class FakeSettingsRepository:
    """回落 `SETTINGS_DEFAULTS` 的假设置仓储。

    Args:
        overrides: 显式覆盖（键为 settings_keys 里的常量）。

    用法::

        repo = FakeSettingsRepository({KEY_LLM_API_KEY: "test-key"})
        repo.get_setting(KEY_LLM_API_KEY, "")      # -> "test-key"
        repo.get_setting(KEY_WEATHER_CITY, "")     # -> "上海"（来自真源默认值）
        repo.get_setting("unknown_key", 42)        # -> 42（回落调用方给出的 default）
    """

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self._values: dict[str, Any] = {}
        if overrides:
            self._values.update(copy.deepcopy(dict(overrides)))

    # ── 读 ──
    def get_setting(self, key: str, default: Any = None) -> Any:
        if key in self._values:
            return copy.deepcopy(self._values[key])
        if key in SETTINGS_DEFAULTS:
            return copy.deepcopy(SETTINGS_DEFAULTS[key])
        return default

    # ── 写 ──
    def set_setting(self, key: str, value: Any) -> None:
        self._values[key] = copy.deepcopy(value)

    # ── 测试辅助（非生产仓储 API） ──
    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._values)

    def reset(self) -> None:
        self._values.clear()


def configured_settings_repository(**overrides: Any) -> FakeSettingsRepository:
    """构造一个「已配置好 AI 服务」的假仓储——供 chat / AI 链路测试使用。

    显式声明 `llm_api_key` 非空，让 `chat_paths/helpers.py` 的预检判定"已配置"
    而继续正常路由。测试意图是验证**流式/分型链路本身**，不是验证"未配置"分支
    （后者由专门的预检用例覆盖）。
    """
    from core.settings_keys import KEY_LLM_API_KEY

    base: dict[str, Any] = {KEY_LLM_API_KEY: "test-llm-key-0000"}
    base.update(overrides)
    return FakeSettingsRepository(base)
