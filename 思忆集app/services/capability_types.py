"""统一能力注册表读模型。

所有能力（内建、插件、MCP provider）统一在此注册。
planner 和 UI 从此读取，不再依赖硬编码列表。

capability_descriptor 是全平台能力合同，包含完整的安全/隐私/审计元数据。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── 枚举类型 ──────────────────────────────────────────────


class RiskLevel(str, Enum):
    """风险分级。"""

    READ = "read"  # R1 读取 — 搜索、读取、获取上下文
    WRITE = "write"  # R2 写入 — 新建、添加、创建
    MODIFY = "modify"  # R3 修改 — 改原内容、移动、批量
    DESTRUCTIVE = "destructive"  # R4 不可逆 — 删除、覆盖、发消息、付款、提权


class PrivacyEgressPolicy(str, Enum):
    """隐私外发策略。"""

    LOCAL_ONLY = "local_only"  # 仅本地处理，禁止送模型/联网/同步
    LOCAL_MODEL = "local_model"  # 可送本地模型，不送云端
    MODEL_WITH_CONSENT = "model_with_consent"  # 送云端模型需用户同意
    SYNC_ALLOWED = "sync_allowed"  # 可跨端同步
    EGRESS_ALLOWED = "egress_allowed"  # 可分享/外发（需确认）


class ConfirmationLevel(str, Enum):
    """确认级别。"""

    AUTO = "auto"  # 自动执行
    FIRST_TIME = "first_time"  # 首次确认，同一会话可信任
    EVERY_TIME = "every_time"  # 每次确认
    STRONG = "strong"  # 强确认（密码/生物）
    DEFAULT_BLOCK = "default_block"  # 默认阻断


class ExecutionMode(str, Enum):
    """执行模式。"""

    LOCAL_SYNC = "local_sync"  # 本地同步执行
    LOCAL_ASYNC = "local_async"  # 本地异步执行
    FOREGROUND_VISIBLE = "foreground_visible"  # 前台可见执行
    REMOTE_AGENT = "remote_agent"  # 远程代理执行
    MANUAL_TAKEOVER = "manual_takeover"  # 需要用户手动接管


class IdempotencyClass(str, Enum):
    """幂等性分类。"""

    IDEMPOTENT = "idempotent"  # 可重复执行多次，结果一致
    NON_IDEMPOTENT = "non_idempotent"  # 不可重复，每次可能不同
    APPEND = "append"  # 追加型，重复=再追加
    DESTRUCTIVE = "destructive"  # 破坏型，重复会造成更大破坏


class RollbackPolicy(str, Enum):
    """回滚策略。"""

    NONE = "none"  # 无需回滚（纯查询）
    PRE_SNAPSHOT = "pre_snapshot"  # 写前快照，可完整恢复
    REVERSIBLE = "reversible"  # 有逆操作，可逐条撤销
    MANUAL_ONLY = "manual_only"  # 只能人工恢复


class AuditLevel(str, Enum):
    """审计级别。"""

    NONE = "none"  # 不记录内容
    SUMMARY = "summary"  # 记录摘要
    FULL_PARAMS = "full_params"  # 记录完整参数
    FILE_SNAPSHOT = "file_snapshot"  # 记录文件快照


class FallbackPolicy(str, Enum):
    """失败回退策略。"""

    RETRY = "retry"  # 重试（可配次数）
    DEGRADE = "degrade"  # 降级（降低风险模式）
    TO_MANUAL = "to_manual"  # 转人工
    TO_OTHER_DEVICE = "to_other_device"  # 转其他设备
    TERMINATE = "terminate"  # 终止


# ── 核心数据类 ────────────────────────────────────────────


def _normalize_parameters_schema(parameters: Any) -> dict[str, Any]:
    """把 payload_schema 归一化为合法的 OpenAI JSON Schema。

    CAPABILITY_CATALOG 中 payload_schema 历史上写成 ``{参数名: 中文描述字符串}``
    而非真正的 JSON Schema。直接把它当作 ``parameters`` 传给 OpenAI tool calling
    会导致 ``type: null`` / ``{"type":"","properties":null}`` 这类 400 错误，
    使整个 chat 链路（DeepSeek 主 + Ollama 本地）全部失败。

    本函数：
    - 若 parameters 已是合法 JSON Schema（``type`` 是已知 JSON 类型 且带 ``properties``），
      深拷贝返回。
    - 否则视为 ``{name: description_string}``，转成
      ``{"type":"object","properties":{name:{"type":"string","description":...}}}``。
    - 描述含"数量"/"条数"等整数量词或常见整数参数名时，type 标为 integer。

    注意：不能仅靠 ``"type" in parameters`` 判断是否已是 schema —— catalog 里
    ``file.find`` 历史上有个参数恰好叫 ``type``（值为 ``"file|dir(可选)"``），这种情况
    会被误判，导致顶层 ``type`` 变成中文描述字符串（Ollama 实际报过的错）。
    """
    if not parameters:
        return {"type": "object", "properties": {}}

    if not isinstance(parameters, dict):
        # 兜底：非字典（不应发生）也包成 object，绝不把非法结构漏给模型
        return {"type": "object", "properties": {}}

    # 已是 JSON Schema？需同时满足：顶层 type 是已知 JSON 类型 且 有 properties 字典。
    # 仅靠 key 存在性判断会误判 catalog 里名为 type 的参数。
    top_type = parameters.get("type")
    known_types = {"object", "string", "integer", "number", "boolean", "array", "null"}
    if isinstance(top_type, str) and top_type in known_types and isinstance(parameters.get("properties"), dict):
        return copy.deepcopy(parameters)

    integer_hints = {"limit", "num_results", "top_n", "top_k", "max_items", "max_results", "timeout", "offset", "depth"}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, value in parameters.items():
        if isinstance(value, dict):
            # 已经是单属性 schema（含 type）则原样用
            prop = dict(value)
            if "type" not in prop:
                prop["type"] = "string"
            properties[str(key)] = prop
            continue

        desc = str(value)
        vtype: str
        if key in integer_hints or "数量" in desc or "条数" in desc or "前N" in desc or "(1-" in desc or "（1-" in desc:
            vtype = "integer"
        elif "true/false" in desc.lower() or "是否" in desc:
            vtype = "boolean"
        else:
            vtype = "string"

        if "必填" in desc or "required" in desc.lower():
            required.append(str(key))
            desc = desc.replace("(必填)", "").replace("（必填）", "").replace("(required)", "").replace("（required）", "").strip()

        properties[str(key)] = {"type": vtype, "description": desc}

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


@dataclass
class ToolDef:
    """工具定义 — LLM 可消费的完整工具描述（OpenAI tool calling 兼容）。"""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema 格式的参数描述
    returns: dict[str, Any] = field(default_factory=dict)  # 返回值 schema
    risk_level: str = "low"
    group: str = "builtin"
    timeout_default: int = 20
    allow_user_approval_skip: bool = False

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name.replace(".", "_"),
                "description": self.description,
                "parameters": _normalize_parameters_schema(self.parameters),
            },
        }


@dataclass
class CapabilityDescriptor:
    """capability_descriptor — 全平台能力合同。

    所有工具、App、AI 能力的注册描述，包含安全/隐私/审计元数据。
    新能力如果不能描述这些字段，不能进入小问的自动执行链。
    """

    # ── 基础标识 ──
    capability_id: str  # 全局唯一，例如 file.read
    display_name: str = ""  # 给用户看的名称
    description: str = ""  # 能力描述
    category: str = "builtin"  # internal_domain / system_bridge / external_app_adapter / ai_tool_provider / hub_proxy / plugin
    group: str = "builtin"
    source: str = "builtin"
    enabled: bool = True

    # ── 数据 Schema ──
    payload_schema: dict[str, Any] = field(default_factory=dict)  # 参数 schema（capability_descriptor 中称 input_schema）
    output_schema: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)

    # ── 风险与安全 ──
    risk_level: str = RiskLevel.READ.value  # read / write / modify / destructive
    privacy_egress_policy: str = PrivacyEgressPolicy.LOCAL_MODEL.value
    confirmation_level: str = ConfirmationLevel.AUTO.value

    # ── 执行控制 ──
    execution_mode: str = ExecutionMode.LOCAL_SYNC.value
    idempotency_class: str = IdempotencyClass.IDEMPOTENT.value
    timeout_default: int = 20
    allow_user_approval_skip: bool = False

    # ── 治理 ──
    rollback_policy: str = RollbackPolicy.NONE.value
    audit_level: str = AuditLevel.SUMMARY.value
    fallback_policy: str = FallbackPolicy.TERMINATE.value

    # ── 转换方法 ──

    def to_catalog_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "action_id": self.capability_id,
            "display_name": self.display_name or self.capability_id,
            "description": self.description,
            "risk": self.risk_level,
            "category": self.category,
            "confirmation_level": self.confirmation_level,
            "privacy_egress_policy": self.privacy_egress_policy,
            "execution_mode": self.execution_mode,
            "payload_schema": dict(self.payload_schema),
            "input_schema": dict(self.payload_schema),
            "source": self.source,
        }
        if self.output_schema:
            entry["output_schema"] = dict(self.output_schema)
        if self.depends_on:
            entry["depends_on"] = list(self.depends_on)
        if self.rollback_policy:
            entry["rollback_policy"] = self.rollback_policy
        if self.audit_level:
            entry["audit_level"] = self.audit_level
        if self.fallback_policy:
            entry["fallback_policy"] = self.fallback_policy
        entry["idempotency_class"] = self.idempotency_class
        entry["timeout_default"] = self.timeout_default
        return entry

    def to_tool_def(self) -> ToolDef:
        """转换为 LLM 可消费的 ToolDef。"""
        return ToolDef(
            name=self.capability_id,
            description=self.description,
            parameters=self.payload_schema or {},
            returns=self.output_schema or {},
            risk_level=self.risk_level,
            group=self.group,
            timeout_default=self.timeout_default,
            allow_user_approval_skip=self.allow_user_approval_skip,
        )


# 向后兼容别名
CapabilitySpec = CapabilityDescriptor


@dataclass
class CapabilityInvocation:
    """capability_invocation — 一次能力调用的请求/响应记录。

    保留完整的输入输出和状态追踪，连接 capability_descriptor 和 execution_receipt。
    """

    invocation_id: str
    capability_id: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending / running / completed / failed / blocked / cancelled
    source: str = "assistant"
    risk_level: str = RiskLevel.READ.value
    confirmation: str = ""  # auto / first_time / every_time / strong / default_block
    execution_receipt_id: str = ""  # 关联的 execution_receipt
    error: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "capability_id": self.capability_id,
            "input": dict(self.input),
            "output": dict(self.output),
            "status": self.status,
            "source": self.source,
            "risk_level": self.risk_level,
            "confirmation": self.confirmation,
            "execution_receipt_id": self.execution_receipt_id,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class CapabilityGroup:
    """能力组：一组相关能力的集合。"""

    group_id: str
    name: str
    description: str = ""
    enabled: bool = True


class CapabilityRegistry:
    """统一能力注册表 — 读模型。

    planner 和 UI 从此读取能力目录，不直接依赖硬编码列表。
    关闭某能力组后，planner 不再选择该组内的能力。
    新 provider 注入后立即可见（自动发现）。
    """

    def __init__(self):
        self._capabilities: dict[str, CapabilitySpec] = {}
        self._groups: dict[str, CapabilityGroup] = {}

    # ── 组管理 ─────────────────────────────────────────────

    def register_group(self, group: CapabilityGroup) -> None:
        self._groups[group.group_id] = group

    def get_group(self, group_id: str) -> CapabilityGroup | None:
        return self._groups.get(group_id)

    def list_groups(self) -> list[CapabilityGroup]:
        return list(self._groups.values())

    def enable_group(self, group_id: str, enabled: bool) -> None:
        if group_id in self._groups:
            self._groups[group_id].enabled = enabled

    def is_group_enabled(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        return group is not None and group.enabled

    # ── 能力管理 ───────────────────────────────────────────

    def register_capability(self, spec: CapabilitySpec) -> None:
        self._capabilities[spec.capability_id] = spec

    def get_capability(self, capability_id: str) -> CapabilitySpec | None:
        return self._capabilities.get(capability_id)

    def enable_capability(self, capability_id: str, enabled: bool) -> None:
        cap = self._capabilities.get(capability_id)
        if cap is not None:
            cap.enabled = enabled

    def list_capabilities(
        self,
        include_disabled: bool = False,
    ) -> list[CapabilitySpec]:
        if include_disabled:
            return list(self._capabilities.values())
        return [c for c in self._capabilities.values() if c.enabled and self._is_group_enabled_for(c)]

    def _is_group_enabled_for(self, spec: CapabilitySpec) -> bool:
        group_id = spec.group
        if group_id and group_id in self._groups:
            return self._groups[group_id].enabled
        return True

    # ── 注册 provider 能力集合 ────────────────────────────

    def register_provider_capabilities(
        self,
        source: str,
        capabilities: list[CapabilitySpec],
    ) -> None:
        for spec in capabilities:
            spec.source = source
            self.register_capability(spec)

    # ── 为 planner 构建能力目录 ───────────────────────────

    def build_capability_catalog(
        self,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        return [cap.to_catalog_entry() for cap in self.list_capabilities(include_disabled=include_disabled)]

    def get_valid_action_ids(self, include_disabled: bool = False) -> set[str]:
        return {cap.capability_id for cap in self.list_capabilities(include_disabled=include_disabled)}

    # ── ToolDef 生成（LLM 工具调用格式） ──────────────────

    def list_tool_defs(self, include_disabled: bool = False) -> list[ToolDef]:
        """返回所有启用能力的 ToolDef 列表，供 LLM 消费。"""
        return [cap.to_tool_def() for cap in self.list_capabilities(include_disabled=include_disabled)]

    def build_openai_tools(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        """返回 OpenAI tool calling 格式的工具列表。"""
        return [td.to_openai_tool() for td in self.list_tool_defs(include_disabled=include_disabled)]

    # ── 内置组注册 ────────────────────────────────────────

    @staticmethod
    def create_default_groups() -> list[CapabilityGroup]:
        return [
            CapabilityGroup(group_id="news", name="新闻资讯", description="新闻检索、简报生成"),
            CapabilityGroup(group_id="journal", name="日志草稿", description="日志/复盘/周报草稿"),
            CapabilityGroup(group_id="notes", name="笔记管理", description="笔记同步、整理"),
            CapabilityGroup(group_id="system", name="系统管理", description="系统信息、进程管理"),
            CapabilityGroup(group_id="files", name="文件操作", description="文件读写、目录浏览"),
            CapabilityGroup(group_id="clipboard", name="剪贴板", description="系统剪贴板读写"),
            CapabilityGroup(group_id="browser", name="浏览器", description="浏览器打开、Web搜索"),
            CapabilityGroup(group_id="app", name="应用启动", description="启动本地应用"),
            CapabilityGroup(group_id="shell", name="命令执行", description="Shell命令执行"),
            CapabilityGroup(group_id="web", name="网络工具", description="Web下载、搜索、抓取"),
            CapabilityGroup(group_id="cron", name="定时任务", description="定时任务管理"),
            CapabilityGroup(group_id="mcp", name="MCP工具", description="MCP协议工具调用"),
            CapabilityGroup(group_id="subagent", name="子代理", description="子代理任务管理"),
            CapabilityGroup(group_id="memory", name="记忆检索", description="本地统一记忆索引检索"),
            CapabilityGroup(group_id="upgrade", name="升级管理", description="升级检查与建议"),
            CapabilityGroup(group_id="usage", name="使用监控", description="运行软件快照与使用统计"),
            CapabilityGroup(group_id="code", name="代码执行", description="通过CCBridge执行复杂编码任务"),
        ]

    @staticmethod
    def _categorize_action(action_id: str) -> str:
        """根据 action_id 推断所属能力组。"""
        category_map = {
            "news": "news",
            "journal": "journal",
            "notes": "notes",
            "usage": "usage",
            "memory": "memory",
            "upgrade": "upgrade",
            "browser": "browser",
            "file": "files",
            "clipboard": "clipboard",
            "app": "app",
            "shell": "shell",
            "web": "web",
            "process": "system",
            "system": "system",
            "cron": "cron",
            "mcp": "mcp",
            "subagent": "subagent",
            "code": "code",
        }
        prefix = action_id.split(".")[0]
        return category_map.get(prefix, "builtin")
