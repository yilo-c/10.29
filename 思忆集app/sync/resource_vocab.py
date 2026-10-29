"""内部资源词汇单源映射（波次④包B · 蓝本 §1a-1）。

背景：同一业务实体在三套词表里名字打架——任务实体在 A 编排栈叫 ``tasks``
（``api/sync_orchestrator.py`` RESOURCE_SPECS），在 B 云栈 / Cloudflare Worker /
手机端 wire 上叫 ``todos``，在 C 声明层（``core/sync_protocol.py`` RESOURCE_TYPES）
叫 ``personal_task``（蓝本 §1a「三名同实」活例）。

本模块把这一词汇差异**固化为机器可读的单一映射表**（守则第 7 条），仅供后续
波次接线收敛时作为唯一查表入口——**本包不接线、不改任何调用点**。

边界（蓝本 §4 铁律）：
- wire 词汇冻结：不触碰 ``cloudflare/worker/src/sync.ts`` ALLOWED_RESOURCES、
  手机端 ``cloudSync.ts`` payload 键；本表只是**内部编排层**的翻译登记，
  方向为「内部名 → wire/他栈名」，与 Worker 契约测试正交。
- 反方向（wire→内部）由各消费栈现有映射承担，本表不定义（单向锚定，
  见 tests/test_wave4b_sync_state_ssot_20260924.py::test_resource_name_translation_tasks_todos）。
"""
from __future__ import annotations

#: 内部编排层资源名 → 目标栈（B/Worker/手机 wire）资源名 的单向别名表。
#: 仅登记**有分歧**的词汇；未登记词项视为两侧同名（透传）。
#: 新增分歧必须先登记此表并被锚测试覆盖，禁止在调用点散落 if/字典硬翻译。
RESOURCE_ALIASES: dict[str, str] = {
    "tasks": "todos",          # A 栈内部名 → B/Worker/手机 wire 名（§1a-1 三名同实）
    "personal_task": "todos",  # C 声明层单数名 → wire 名（信封协议词汇，仅供映射参考，未接线）
    "note": "notes",           # C 声明层单数名 → wire/A 栈复数名（同上，未接线）
}


def translate_resource(name: str) -> str:
    """把内部资源名翻译为 wire/他栈资源名；未登记词项原样透传。

    本包（波次④包B）只提供单一查表入口并锁住已知分歧，**不改任何调用点**；
    接线收敛归后续波次（届时 A/B 栈的散落映射改调此函数）。
    """
    return RESOURCE_ALIASES.get(name, name)
