"""ReAct 工具目录与无依赖纯函数 — 共享层（P0-12）。

**波次② C-1（2026-09-23）后本模块是全仓工具 / 能力的唯一声明源**：
``REACT_TOOL_CATALOG``（115 条）已吸收原 ``assistant/agents/planner.py`` 的
``CAPABILITY_CATALOG``，每条自带 ``risk`` / ``confirmation``（及可选
rollback / audit / privacy / idempotency / timeout_default）；
capability_router 的注册主循环、planner 的兼容视图、butler 的工具暴露面
（``BUTLER_DECLARED_TOOL_IDS``）全部从此处取数；仍读旧 risk 词表
（low/medium/high）的消费者经 ``to_step_risk`` 回落。

历史上 ``REACT_TOOL_CATALOG`` 和 ``extract_tool_calls_from_text`` 等纯函数
位于 ``backend/react_executor.py``，导致 ``assistant/multi_agent_orchestrator``
反向 import backend，破坏分层（assistant → backend）；该编排器的执行链已于
波次② 步骤 5 下线，本模块的分层职责不变。

本模块下沉这些**无 AppContext 依赖**的纯数据 / 函数，使：
- ``services`` 在层级图中位于 ``backend`` 之下，
- ``assistant`` 与 ``backend`` 都从 services 引用，
- ``REACT_TOOL_CATALOG`` / ``SAFE_TOOL_IDS_BASE`` 等同名符号现在仅在本模块与
  ``services.react_executor`` 中定义，所有调用方（agent_loop / 测试）
  均直接引用 ``services.*``，不再经过 ``backend.react_executor`` 兼容层。

说明：历史上这些符号曾通过 ``backend/react_executor.py`` 的 re-export 别名暴露，
该兼容层已于 2026-08-23 删除（见 T1.4 收口）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("siyi.services.react_tool_catalog")


# ── 安全工具白名单（无 AppContext 依赖） ─────────────────────────────────────

# 统一工具白名单 — ReActEngine 与 AgentOrchestrator 的唯一来源。
# P0 安全约束：高危工具（app.launch / browser.automate / browser.close 等）
# 不在 LLM 可见白名单中 —— LLM 不应自主启动应用或关闭浏览器；
# 这些能力仍保留在 REACT_TOOL_CATALOG 中，由 ActionBroker 在 user/explicit
# source 下经审批后执行（defense in depth）。
SAFE_TOOL_IDS_BASE: frozenset[str] = frozenset({
    "file.read", "file.write", "file.find", "file.copy", "file.grep",
    "web.search", "web.fetch",
    "memory.recall",
    "clipboard.read",
    "system.info",
    "translate.text",
    "vocab.quiz_in_chat",
    "vocab.review_status",
    # ── 本地资源管理（笔记/任务/收件箱）──
    "notes.list", "notes.search", "notes.create", "notes.get", "notes.update", "notes.tag", "notes.delete", "notes.batch_delete",
    "tasks.list", "tasks.search", "tasks.create", "tasks.get", "tasks.update", "tasks.delete", "tasks.toggle",
    "inbox.add", "inbox.list",
    # ── 思忆集定制能力 ──
    "info.brief", "knowledge.query", "transfer.send",
    # ── 浏览器只读能力（LLM 可见，写入/关闭仍由 ActionGuard 拦截）──
    "browser.navigate", "browser.extract", "browser.screenshot", "browser.scroll",
    "browser.get_tabs", "browser.switch_tab", "browser.wait",
})


# ── 工具目录（无 AppContext 依赖） ─────────────────────────────────────────

REACT_TOOL_CATALOG: list[dict[str, Any]] = [
    {"action_id": "file.read", "description": "读取本地文件、列出目录内容、支持 glob 模式匹配和目录预览", "payload_schema": {"path": "string (必填) 文件或目录路径，Windows 格式如 C:\\Users\\用户名\\Desktop", "encoding": "utf-8", "recursive": "是否递归列出(true/false)", "depth": "递归深度(1-5)", "preview": "是否预览小文件内容(true/false)"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "file.write", "description": "创建或覆盖写入文件（文本内容）", "payload_schema": {"path": "string (必填) 文件完整路径", "content": "string (必填) 要写入的文本内容", "mode": "overwrite|append"}, "risk": "destructive", "confirmation": "every_time", "rollback": "pre_snapshot", "audit": "full_params"},
    {"action_id": "file.find", "description": "按名称/类型模式搜索文件（支持 glob 模糊匹配）", "payload_schema": {"pattern": "string (必填) 搜索模式，如 *.txt", "root": "string (可选, 默认.) 搜索根目录", "max_results": "最大结果数(默认50)", "entry_type": "类型过滤(file/dir,可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "file.copy", "description": "复制或移动文件到目标位置", "payload_schema": {"source": "string (必填) 源路径", "dest": "string (必填) 目标路径", "move": "是否移动(true/false,默认false)", "overwrite": "是否覆盖(true/false,默认false)"}, "risk": "destructive", "confirmation": "first_time", "rollback": "pre_snapshot"},
    {"action_id": "web.search", "description": "搜索互联网获取实时信息", "payload_schema": {"query": "string (必填) 搜索关键词", "num_results": "结果数量(1-10)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "web.fetch", "description": "抓取网页正文内容", "payload_schema": {"url": "string (必填) 网页完整 URL"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "memory.recall", "description": "检索本地统一记忆索引（笔记/任务/收件箱/执行记录）", "payload_schema": {"query": "string (必填) 搜索关键词", "top_k": "结果条数(1-20)", "source_filters": "来源过滤(note/task/inbox/decision/execution_run)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "system.info", "description": "获取系统信息（CPU/内存/磁盘/网络）", "payload_schema": {"category": "cpu|memory|disk|network|all"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "translate.text", "description": "翻译文本（支持多语言互译，默认自动检测源语言翻译为中文）", "payload_schema": {"text": "string (必填) 待翻译内容", "from": "源语言代码(auto/zh-CHS/en/ja/ko/fr/...)", "to": "目标语言代码(zh-CHS/en/...)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "clipboard.read", "description": "读取系统剪贴板当前内容", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "app.launch", "description": "启动系统桌面应用程序（如微信、浏览器、编辑器等）", "payload_schema": {"command": "string (必填) 应用名称或路径，如 wechat, chrome, code", "args": "参数列表(可选)"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "vocab.quiz_in_chat", "description": "从用户的单词本随机抽 1 个待复习的词出小问答（中文释义+例句留空），用户答对/答错后会自动记录到 SM-2", "payload_schema": {"prefer_due": "bool (可选, 默认 true) 优先抽到期复习的词", "wordbook_id": "number (可选) 限定词本"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "vocab.review_status", "description": "查询用户的单词复习状态（待复习数 / 总词数 / 今日已复习）。用于聊天里回答\"我今天还有多少词没复习\"这类问题", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    # ── 本地资源管理（笔记/任务/收件箱）──
    {"action_id": "notes.list", "description": "列出最近的笔记。用于查看已有笔记、查找特定笔记。用户说\"帮我看看有哪些笔记\"\"列一下笔记\"时使用。", "payload_schema": {"limit": "number (可选, 默认 20) 返回数量上限"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "notes.search", "description": "按关键词搜索笔记（全文搜索标题/内容/标签/分类）。用于用户说\"帮我找关于 AI 的笔记\"\"搜索包含某个词的笔记\"等场景。", "payload_schema": {"query": "string (必填) 搜索关键词", "max_results": "number (可选, 默认 20) 最多返回条数"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "notes.create", "description": "创建一条新笔记。用于用户说\"帮我记笔记/创建笔记/写一篇笔记\"等场景。", "payload_schema": {"title": "string (必填) 笔记标题", "content": "string (可选) 笔记内容（Markdown）", "tags": "string (可选) 逗号分隔的标签，如 '工作,周报'"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "notes.get", "description": "获取单条笔记的完整内容。用于查看笔记详情。", "payload_schema": {"note_id": "number/string (必填) 笔记 ID"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "notes.update", "description": "更新笔记字段（标题/内容/标签等）。用于修改已有笔记。", "payload_schema": {"note_id": "number/string (必填) 笔记 ID", "title": "string (可选) 新标题", "content": "string (可选) 新内容", "tags": "string (可选) 逗号分隔的新标签"}, "risk": "modify", "confirmation": "auto"},
    {"action_id": "notes.tag", "description": "批量给笔记添加或移除标签。适合整理时说\"给这些笔记打上工作标签\"\"取消某标签\"等场景。", "payload_schema": {"note_ids": "string (必填) 逗号分隔的笔记ID列表，例如 \"1,2,3\"", "tags": "string (必填) 逗号分隔的标签，例如 \"工作,重要\"", "remove": "bool (可选, 默认 false) 设为 true 表示移除标签而不是添加"}, "risk": "modify", "confirmation": "auto"},
    {"action_id": "notes.delete", "description": "删除单条笔记（软删除，可恢复）。用于用户说\"删掉这条笔记\"等场景。如果要删除多条，请优先使用 notes.batch_delete。", "payload_schema": {"note_id": "number/string (必填) 笔记 ID"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "notes.batch_delete", "description": "批量删除笔记（软删除）。适合用户说\"帮我整理删除一些笔记\"\"清理空笔记\"\"批量删除这些笔记\"等场景。用法：先调 notes.list 或 notes.search 获取列表，识别出要删除的笔记ID后，用 note_ids 传入逗号分隔的ID列表；或设 empty_only=true 自动删除所有空笔记。", "payload_schema": {"note_ids": "string (可选) 逗号分隔的笔记ID列表，例如 \"1,2,3\"", "empty_only": "bool (可选, 默认 false) 设为 true 时自动删除所有内容为空（或仅含空白）的笔记"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "tasks.list", "description": "列出待办事项。用于查看任务、回答\"我有哪些待办\"等问题。", "payload_schema": {"limit": "number (可选, 默认 30) 返回数量上限"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "tasks.search", "description": "按关键词搜索待办任务（全文搜索标题/备注/标签）。用于用户说\"帮我找一下买菜的待办\"\"搜索和项目相关的任务\"等场景。", "payload_schema": {"query": "string (必填) 搜索关键词", "max_results": "number (可选, 默认 20) 最多返回条数"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "tasks.create", "description": "创建一条新待办任务。用于用户说\"帮我加个任务/提醒我做某事\"等场景。", "payload_schema": {"name": "string (必填) 任务标题", "description": "string (可选) 详细描述", "due_date": "string (可选) ISO 格式截止日期"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "tasks.get", "description": "获取单条任务的完整信息。", "payload_schema": {"task_id": "number/string (必填) 任务 ID"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "tasks.update", "description": "更新任务字段（标题/描述/截止日期/优先级等）。", "payload_schema": {"task_id": "number/string (必填) 任务 ID", "name": "string (可选) 新标题", "description": "string (可选) 新描述", "due_date": "string (可选) 新截止日期", "priority": "string (可选) 优先级 high/medium/low"}, "risk": "modify", "confirmation": "auto"},
    {"action_id": "tasks.delete", "description": "删除任务（软删除）。用于用户说\"删掉这个任务\"等场景。", "payload_schema": {"task_id": "number/string (必填) 任务 ID"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "tasks.toggle", "description": "切换任务完成状态。", "payload_schema": {"task_id": "number/string (必填) 任务 ID", "completed": "bool (必填) 是否完成"}, "risk": "modify", "confirmation": "auto"},
    {"action_id": "inbox.add", "description": "向收件箱投递一条临时信息（想法、稍后整理的内容）。", "payload_schema": {"content": "string (必填) 内容主体", "title": "string (可选) 标题", "source": "string (可选, 默认 'agent') 来源标识"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "inbox.list", "description": "列出收件箱条目（分页返回，默认按录入顺序取第一页；如需最新条目请用 offset 翻到末页）。", "payload_schema": {"include_archived": "bool (可选, 默认 false) 是否包含已归档", "limit": "number (可选, 默认 50, 上限 200) 本页返回条数", "offset": "number (可选, 默认 0) 跳过条数，用于翻页"}, "risk": "read", "confirmation": "auto"},
    # ── 思忆集定制能力 ──
    {"action_id": "info.brief", "description": "获取最近一次科技资讯简报（含抓取条目和 AI 生成的中文简报）。用于用户说'今天有什么科技新闻''看看资讯简报'等场景。当前返回缓存结果；如需最新抓取请去信息模块手动刷新。", "payload_schema": {"limit": "number (可选, 默认 20) 返回条目数上限"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "knowledge.query", "description": "统一查询个人知识库：同时搜索记忆、知识图谱、笔记、词库和历史资讯，并做融合排序。用于用户问'我之前记过关于 XX 的什么''我有哪些关于 XX 的知识'等场景。", "payload_schema": {"query": "string (必填) 查询内容", "limit": "number (可选, 默认 15) 最多返回条数"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "transfer.send", "description": "向已配对设备（手机/桌面）发送一段文本或链接。用于'把这句话发到手机上''传个链接到手机'等场景。", "payload_schema": {"text": "string (必填) 要发送的文本或链接", "target": "string (可选, 默认 mobile) 目标设备: mobile 或 desktop"}, "risk": "write", "confirmation": "auto"},
    # ── 浏览器全自动化（Playwright）──
    {"action_id": "browser.navigate", "description": "打开一个网页 URL（会启动可见的浏览器窗口）。用于浏览网页、查看在线文档、打开搜索页等。", "payload_schema": {"url": "string (必填) 完整的网页地址，如 https://www.bing.com"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "browser.click", "description": "点击页面上的元素。可以通过 CSS 选择器或元素文本定位。用于点击按钮、链接、菜单项等。", "payload_schema": {"selector": "string (可选) CSS 选择器，如 '#submit-btn' 或 '.nav-link'", "text": "string (可选) 元素显示的文本，如 '搜索' 或 'Login'。如果提供 text 会优先使用"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "browser.type", "description": "在输入框中输入文本。用于填写搜索框、表单输入、登录框等。", "payload_schema": {"selector": "string (必填) CSS 选择器定位输入框，如 'input[name=q]' 或 '#search-box'", "text": "string (必填) 要输入的文本内容", "clear": "bool (可选, 默认 true) 输入前是否清空原内容"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "browser.extract", "description": "提取页面内容。可提取整页文本、HTML、或通过 CSS 选择器提取特定元素。用于获取搜索结果、文章内容、数据列表等。", "payload_schema": {"selector": "string (可选) CSS 选择器，指定要提取的元素。不填则提取整页", "type": "string (可选, 默认 text) 提取类型: text/html/attr", "attr": "string (可选, type=attr 时使用) 要获取的属性名，如 href"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "browser.screenshot", "description": "截取当前页面的截图并保存为 PNG 文件。用于记录页面状态、让用户查看当前页面、供 AI 视觉分析等。", "payload_schema": {"path": "string (可选) 保存路径。不填则自动保存到 agent_outputs 目录", "full_page": "bool (可选, 默认 true) 是否截取完整页面（包括滚动区域）"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "browser.scroll", "description": "滚动页面。用于查看长页面的后续内容、触发懒加载等。", "payload_schema": {"direction": "string (可选, 默认 down) 滚动方向: down/up/top/bottom", "amount": "number (可选, 默认 500) 滚动像素数（direction=down/up 时有效）"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "browser.get_tabs", "description": "获取当前浏览器中所有打开的标签页列表。用于多标签页任务管理。", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "browser.switch_tab", "description": "切换到指定索引的标签页。索引从 0 开始。", "payload_schema": {"index": "number (必填) 标签页索引"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "browser.wait", "description": "等待一段时间或等待元素出现。用于等待页面加载、动画完成、AJAX 请求等。", "payload_schema": {"ms": "number (可选, 默认 1000) 等待毫秒数", "selector": "string (可选) 等待此 CSS 选择器对应的元素出现"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "browser.close", "description": "关闭浏览器会话，释放资源。任务完成后调用以清理。", "payload_schema": {}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "browser.automate", "description": "一站式浏览器自动化：接受一个多步骤脚本，依次执行 navigate/click/type/extract 等原子操作。用于复杂的多步网页交互任务。", "payload_schema": {"steps": "array (必填) 操作步骤列表，每项形如 {action: 'navigate|click|type|extract|screenshot|scroll', ...对应参数}"}, "risk": "destructive", "confirmation": "every_time"},
    # ── 高级文件操作（对标 Codex 工具集）──
    {"action_id": "file.grep", "description": "在文件内容中按正则搜索（类似 ripgrep）。用于查找代码/文本内容，如'哪个文件里包含 XX'。返回命中行及行号。", "payload_schema": {"pattern": "string (必填) 正则表达式", "path": "string (可选) 单个文件或搜索根目录，默认工作区", "glob": "string (可选) 文件名过滤，如 *.py", "max_results": "number (可选, 默认 50) 最多命中条数"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "file.edit", "description": "对已有文件做局部替换编辑（比 file.write 整文件覆盖更安全）。old_text 默认必须全文唯一；若不唯一会报错，此时应提供更多上下文或设 replace_all。", "payload_schema": {"path": "string (必填) 文件路径", "old_text": "string (必填) 要被替换的原文（需与文件内容完全一致）", "new_text": "string (必填) 替换后的新文本，可为空表示删除", "replace_all": "bool (可选, 默认 false) 为 true 时替换全部匹配"}, "risk": "destructive", "confirmation": "first_time", "rollback": "pre_snapshot", "audit": "full_params"},
    {"action_id": "desktop.screenshot", "description": "截取整个桌面并保存为图片，尝试用视觉模型描述画面内容。用于用户说'看看我的桌面/屏幕''截个屏'等场景。返回截图路径与内容描述，截图会以图片卡片展示给用户。", "payload_schema": {"describe": "bool (可选, 默认 true) 是否尝试用视觉模型描述截图内容"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "shell.run", "description": "在系统中执行一条命令行命令（会先请求用户确认）。仅在没有专用工具可用时使用，如整理/移动/重命名大量文件、运行脚本、查看系统信息等。禁止用于删除系统文件、修改注册表等危险操作。", "payload_schema": {"command": "string (必填) 要执行的命令，如 powershell -Command \"...\" 或 dir /b", "working_dir": "string (可选) 工作目录", "timeout": "number (可选, 默认 15) 超时秒数，上限 60"}, "risk": "destructive", "confirmation": "every_time", "audit": "full_params"},
    {"action_id": "confirmation.status", "description": "查询之前发起的用户确认请求的结果（approved/rejected/pending）。在发起确认请求后需要知道结果时使用。", "payload_schema": {"confirmation_id": "string (必填) 确认请求 ID（来自 pending_confirmation 返回）"}, "risk": "read", "confirmation": "auto"},

    # ── 原 planner CAPABILITY_CATALOG 独有条目（审计波次② C-1 步骤 4 并入）──
    # 本目录自此为**唯一声明源**：assistant/capability_router 的 registry 注册、
    # butler 工具面（按 BUTLER_DECLARED_TOOL_IDS 取子集）、planner 的兼容视图
    # CAPABILITY_CATALOG 全部从这里出。新增动作只改这里。
    {"action_id": "news.lookup", "description": "检索新闻资讯（支持多源聚合、分类筛选）", "payload_schema": {"query": "搜索关键词", "limit": "数量(1-20)", "source": "来源名称(all/央视新闻/36氪/...)", "category": "分类(AI/互联网/政策/国际/科技/...)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "news.briefing", "description": "生成 AI 新闻简报摘要（聚合多源后用 LLM 总结）", "payload_schema": {"topic": "主题关键词", "max_items": "聚合条数(默认10)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "journal.daily_draft", "description": "生成日志/复盘/周报草稿", "payload_schema": {"title": "标题"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "notes.sync", "description": "笔记双向同步", "payload_schema": {"mode": "bidirectional"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "notes.organize", "description": "笔记分类统计", "payload_schema": {"max_items": "数量"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "usage.snapshot", "description": "当前运行软件快照", "payload_schema": {"top_n": "显示前N个进程"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "upgrade.check", "description": "检查升级建议", "payload_schema": {"channel": "stable|beta"}, "risk": "destructive", "confirmation": "first_time"},
    {"action_id": "browser.open", "description": "打开浏览器访问网址或搜索", "payload_schema": {"url": "网址或留空", "search": "搜索关键词或留空"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "clipboard.write", "description": "向系统剪贴板写入内容", "payload_schema": {"text": "要写入的文本"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "web.download", "description": "下载网页文件到本地", "payload_schema": {"url": "下载地址", "save_path": "保存路径(可选)"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "process.list", "description": "列出当前运行的进程", "payload_schema": {"filter": "进程名过滤(可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "process.kill", "description": "结束指定进程", "payload_schema": {"name": "进程名或PID"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "cron.list", "description": "列出所有定时任务", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "cron.add", "description": "添加定时任务", "payload_schema": {"name": "任务名称", "action_id": "要执行的能力ID", "schedule": "cron表达式(如 0 8 * * *)", "repeat": "none|daily|weekly|monthly|cron"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "cron.remove", "description": "删除定时任务", "payload_schema": {"job_id": "任务ID"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "mcp.tools.list", "description": "列出已接入的 MCP 工具目录", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "mcp.tool.call", "description": "调用第三方 MCP 工具", "payload_schema": {"server": "MCP 服务名", "tool": "工具名", "arguments": "工具参数对象"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "youdao.note.list", "description": "列出有道云笔记（支持分页）", "payload_schema": {"limit": "数量(默认20)", "offset": "偏移(默认0)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "youdao.note.create", "description": "在有道云笔记中创建新笔记", "payload_schema": {"title": "笔记标题", "content": "笔记内容", "folder": "笔记文件夹(可选)"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "youdao.note.get", "description": "查看有道云笔记详情", "payload_schema": {"note_id": "笔记ID"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "youdao.note.update", "description": "更新有道云笔记内容", "payload_schema": {"note_id": "笔记ID", "title": "新标题(可选)", "content": "新内容(可选)"}, "risk": "destructive", "confirmation": "auto"},
    {"action_id": "browser.context.get", "description": "获取当前浏览器页面的上下文（标题、URL、内容、选中文本）", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "browser.summarize", "description": "对当前浏览器页面进行 AI 总结", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "todoist.task.list", "description": "列出 Todoist 任务", "payload_schema": {"project_id": "项目ID(可选)", "limit": "数量(默认20)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "todoist.task.create", "description": "在 Todoist 创建新任务", "payload_schema": {"content": "任务内容", "description": "任务描述(可选)", "priority": "优先级1-4(默认1)", "due_string": "截止时间(如'today', 'tomorrow')(可选)"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "todoist.project.list", "description": "列出 Todoist 项目", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "subagent.spawn", "description": "启动后台子代理任务，可串行或并行执行步骤", "payload_schema": {"workflow": "weekly_notes_digest|weekly_tech_digest|execute_steps", "label": "任务标签", "steps": "步骤数组", "parallel": "是否并行"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "subagent.list", "description": "查看后台子代理任务列表", "payload_schema": {"limit": "返回条数"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "subagent.get", "description": "查看单个后台子代理任务详情", "payload_schema": {"run_id": "后台任务ID"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "system.capabilities", "description": "列出助手所有可执行的能力清单（含插件和 MCP 工具）", "payload_schema": {"include_disabled": "是否包含已禁用的能力(true/false)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "system.environment", "description": "扫描当前本地环境：系统信息、CPU/内存/磁盘、运行进程、已安装应用", "payload_schema": {"category": "all|cpu|memory|disk|network|processes|apps"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "system.scan", "description": "全面扫描：同时检查系统状态、运行进程和应用可用性", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "vision.analyze", "description": "分析图片内容——接收 base64 编码的图片，返回 AI 描述", "payload_schema": {"image_base64": "图片的 base64 编码", "image_format": "图片格式(png/jpeg/webp)", "prompt": "分析提示文字(可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "project.health_check", "description": "扫描项目代码与文档的实现差距，生成健康报告（代码-文档对齐度、后端API完整性）", "payload_schema": {"format": "summary|json|analyze"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "project.auto_repair", "description": "根据健康检查结果自动修复代码与文档的差距（创建缺失后端路由、更新文档地图、清理过时文件）", "payload_schema": {"scope": "all|backend|doc|nav"}, "risk": "destructive", "confirmation": "every_time", "rollback": "pre_snapshot", "audit": "full_params"},
    {"action_id": "dashboard.show", "description": "在 AI 工具覆盖层中显示工作台仪表盘", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    # ── 以下为 Phase 2.3 补齐的 handler ──
    {"action_id": "code.execute", "description": "通过 CC Bridge 执行代码（沙箱隔离，支持 Python/JS/Shell）", "payload_schema": {"language": "python|javascript|shell", "code": "要执行的代码", "timeout": "超时秒数(默认30)"}, "risk": "destructive", "confirmation": "every_time", "rollback": "pre_snapshot", "audit": "full_params"},
    {"action_id": "evolution.daily_scan", "description": "每日进化扫描：收集今日活动数据、技术雷达更新", "payload_schema": {"date": "扫描日期(YYYY-MM-DD)", "full_scan": "是否全量扫描(true/false)"}, "risk": "write", "confirmation": "auto"},
    {"action_id": "tech_watch.collect", "description": "收集技术资讯：扫描 RSS 和技术博客获取最新动态", "payload_schema": {"topics": "主题列表(逗号分隔)", "max_items": "最大收集条数(默认20)", "source": "来源过滤(可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "tech_watch.judge", "description": "技术资讯智能判断：评估技术文章的相关性和价值", "payload_schema": {"items": "待判断的文章列表", "threshold": "相关性阈值(0.0-1.0,默认0.6)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "workflow.execute", "description": "执行自动化工作流（由 WorkflowEngine 执行预设流程）", "payload_schema": {"workflow_id": "工作流ID", "inputs": "输入参数字典", "mode": "sync|async(默认sync)"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "decision.compare_paths", "description": "对比两种决策路径的效果和风险", "payload_schema": {"path_a": "路径A的描述", "path_b": "路径B的描述", "criteria": "评估标准(可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "info.collect", "description": "采集多源技术资讯（HN/Dev.to/GitHub/Reddit），可选 AI 生成简报", "payload_schema": {"topics": "兴趣话题列表(逗号分隔)", "generate_brief": "是否生成AI简报(true/false,默认true)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "image.generate", "description": "AI 生成配图（DALL-E 3），返回 base64 图片数据", "payload_schema": {"prompt": "图片描述", "size": "尺寸(1024x1024/1792x1024/1024x1792,默认1024x1024)", "n": "生成数量(1-4,默认1)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "info.publish", "description": "发布文章到外部平台（WordPress/Medium）", "payload_schema": {"platform": "目标平台(wordpress/medium)", "content": {"title": "标题", "body": "正文(markdown)", "image_urls": "图片列表(可选)", "tags": "标签列表(可选)", "status": "draft/publish(默认draft)"}}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "workflow.publish_article", "description": "全自动发布文章工作流：采集资讯 → AI写文 → 配图 → 发布到平台", "payload_schema": {"topics": "话题列表", "platform": "发布平台(wordpress/medium)", "status": "draft/publish"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "cross_device.route", "description": "路由请求到其他设备执行（跨设备能力调用）", "payload_schema": {"action_id": "要执行的能力ID", "payload": "能力参数字典", "target_device": "目标设备ID(可选，空则自动选择)"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "cross_device.save_session", "description": "保存当前会话到云端，供其他设备恢复", "payload_schema": {"session_data": "会话数据字典", "device_id": "当前设备ID"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "cross_device.upload_attachment", "description": "上传附件到云端，供其他设备访问", "payload_schema": {"file_path": "本地文件路径", "description": "文件描述(可选)"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "proactive.tick", "description": "主动内容推送：根据上下文自动推送相关提醒、建议或信息", "payload_schema": {"context": "当前上下文描述", "max_items": "最大推送条数(默认3)"}, "risk": "read", "confirmation": "auto"},
    # ── 桌面自动化（Phase 7） ──
    {"action_id": "window.find", "description": "搜索桌面窗口（按标题或类名），返回窗口句柄和位置", "payload_schema": {"title": "窗口标题关键词", "class_name": "窗口类名(可选)", "max_results": "最大返回数(默认20)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "window.activate", "description": "将桌面窗口激活到前台（恢复如果最小化，然后置顶）", "payload_schema": {"hwnd": "窗口句柄(来自window.find)", "title": "窗口标题(可选，与hwnd二选一)"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "screen.read", "description": "截取当前屏幕截图，可选的 OCR 文字识别", "payload_schema": {"ocr": "是否执行OCR文字识别(true/false,默认false)", "return_image": "是否返回base64图片(true/false,默认false)", "region": "截取区域{left,top,width,height}(可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "mouse.click", "description": "模拟鼠标在指定屏幕坐标点击", "payload_schema": {"x": "屏幕X坐标(整数)", "y": "屏幕Y坐标(整数)", "button": "left|right(默认left)", "clicks": "点击次数1-3(默认1)"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "mouse.click_text", "description": "通过 OCR 文字识别找到指定文本并点击", "payload_schema": {"text": "要点击的文本内容(支持模糊匹配)", "button": "left|right(默认left)", "clicks": "点击次数1-3(默认1)"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "keyboard.type", "description": "模拟键盘输入文本到当前焦点窗口", "payload_schema": {"text": "要输入的文本", "enter": "是否在输入后按回车(true/false,默认false)", "delay": "按键间隔秒数(默认0.01)"}, "risk": "destructive", "confirmation": "every_time"},
    {"action_id": "keyboard.press", "description": "模拟按键组合（如 ctrl+c, alt+tab 等）", "payload_schema": {"keys": "按键名列表，如 [\"ctrl\", \"c\"]", "key": "单键(与keys二选一)"}, "risk": "destructive", "confirmation": "every_time"},
    # ── Agent 编排（Phase 1） ──
    {"action_id": "agent.directory", "description": "查看所有可用 AI 客户端及其能力", "payload_schema": {"capability": "按能力过滤(可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "agent.ask", "description": "向指定 AI 客户端提问或咨询，获取建议或分析", "payload_schema": {"agent_id": "目标 agent 标识(claude_code/openclaw/hermes/codex)", "question": "问题描述"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "agent.delegate", "description": "委派任务给指定 AI 客户端执行（Claude Code / OpenClaw / Hermes / Codex / Trae）", "payload_schema": {"agent_id": "目标 agent 标识", "task": "任务描述", "timeout": "超时秒数(默认300)"}, "risk": "write", "confirmation": "first_time"},
    # ── Trae IDE 控制 ──
    {"action_id": "trae.open", "description": "用 Trae IDE 打开项目/文件夹", "payload_schema": {"path": "项目/文件夹路径(必填)", "new_window": "是否新窗口打开(true/false,默认false)"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "trae.open_file", "description": "用 Trae IDE 打开文件，可跳转指定行/列", "payload_schema": {"file_path": "文件路径(必填)", "line": "行号(可选,从1开始)", "column": "列号(可选,从1开始)", "new_window": "是否新窗口打开(可选)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "trae.diff", "description": "在 Trae IDE 中比较两个文件", "payload_schema": {"file1": "第一个文件路径(必填)", "file2": "第二个文件路径(必填)"}, "risk": "read", "confirmation": "auto"},
    {"action_id": "trae.list_extensions", "description": "列出 Trae IDE 已安装的扩展", "payload_schema": {}, "risk": "read", "confirmation": "auto"},
    {"action_id": "trae.install_extension", "description": "为 Trae IDE 安装扩展", "payload_schema": {"ext_id": "扩展ID(如 ms-python.python)或VSIX路径(必填)"}, "risk": "write", "confirmation": "first_time"},
    {"action_id": "trae.uninstall_extension", "description": "卸载 Trae IDE 扩展", "payload_schema": {"ext_id": "扩展ID(必填)"}, "risk": "write", "confirmation": "first_time"},
]


# ── butler（PydanticAI 管家）声明池 ─────────────────────────────────────
# 合并 planner 目录前，REACT_TOOL_CATALOG 的 49 条恰好就是 butler 向模型声明的
# 工具池（assistant/agents/butler_agent.py::_build_tools 遍历目录 + is_tool_allowed
# 过滤）。目录扩为唯一声明源（含 planner 独有动作）后，若不钉住，模型面会凭空多出
# 11 个工具（mcp.tool.call / code.execute / workflow.execute / process.kill /
# subagent.spawn ...）——那是 LLM 暴露面的扩张，必须显式决定而非静默发生。
# 新增 want 给管家用时，把 action_id 加进本池并保持 is_tool_allowed 判定一致。
BUTLER_DECLARED_TOOL_IDS: frozenset[str] = frozenset({"file.read", "file.write", "file.find", "file.copy", "web.search", "web.fetch", "memory.recall", "system.info", "translate.text", "clipboard.read", "app.launch", "vocab.quiz_in_chat", "vocab.review_status", "notes.list", "notes.search", "notes.create", "notes.get", "notes.update", "notes.tag", "notes.delete", "notes.batch_delete", "tasks.list", "tasks.search", "tasks.create", "tasks.get", "tasks.update", "tasks.delete", "tasks.toggle", "inbox.add", "inbox.list", "info.brief", "knowledge.query", "transfer.send", "browser.navigate", "browser.click", "browser.type", "browser.extract", "browser.screenshot", "browser.scroll", "browser.get_tabs", "browser.switch_tab", "browser.wait", "browser.close", "browser.automate", "file.grep", "file.edit", "desktop.screenshot", "shell.run", "confirmation.status"})


# ── risk 词表回落（审计波次② C-1 步骤 4）────────────────────────────────
# 本目录的 risk 自合并起统一到 registry 现行词表 read/write/modify/destructive。
# 但旧词表 low/medium/high 仍有真实判定消费者（PlannedStep.risk_level 下游的
# security_layer 高风险强确认、execution 的 degrade_to_readonly、judge 的 ⚠️
# 标注、character_runtime/prompt_context 的 requires_confirmation）。
# 那些位置必须经 to_step_risk() 回落，不能让词表变化静默穿过安全判定。
_STEP_RISK_BY_LEVEL = {"read": "low", "write": "medium", "modify": "medium", "destructive": "high"}


def to_step_risk(risk: Any) -> str:
    """统一 risk 词表（read/write/modify/destructive）→ 旧 low/medium/high。

    已经是旧词表的值（如动态工具 ToolSpec 自己写的 "medium"）原样透传，
    空值回落 "low" —— 本函数对两种词表都幂等，调用方无需知道条目来源。
    """
    key = str(risk or "").strip().lower()
    if not key:
        return "low"
    return _STEP_RISK_BY_LEVEL.get(key, key)


# ── 文本兜底：从 LLM 文本输出中提取工具调用 ──────────────────────────────────
#
# 当 LLM 不支持原生 tool calling（如部分本地 Ollama 模型），或原生调用失败时，
# 模型可能在回复文本中嵌入 JSON 格式的工具调用：
#   {"action_id": "file.read", "path": "C:\\Users\\..."}
# 本函数解析这种格式，让本地 AI 也能触发工具调用。

_TOOL_BLOCK_RE = re.compile(r'\{\s*"(?:tool|action|action_id)"\s*:', re.DOTALL)


def extract_tool_calls_from_text(text: str) -> list[dict[str, Any]] | None:
    """从 LLM 文本输出中提取工具调用（原生 tool calling 失败时兜底）。

    匹配 JSON 块格式: {"action_id": "file.read", "path": "/tmp"}

    Returns:
        list[{"action_id": str, "args": dict}] 或 None（无匹配）
    """
    results: list[dict[str, Any]] = []

    for match in _TOOL_BLOCK_RE.finditer(text):
        start = match.start()
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        aid = (obj.pop("action_id", None) or
                               obj.pop("tool", None) or
                               obj.pop("action", None))
                        if aid and isinstance(aid, str) and aid.strip():
                            results.append({"action_id": aid.strip(), "args": obj})
                    except json.JSONDecodeError:
                        pass
                    break

    return results if results else None


def text_tool_calls_to_openai_format(
    text_calls: list[dict[str, Any]],
    tool_name_map: dict[str, str],
) -> list[dict[str, Any]]:
    """将文本解析出的工具调用转换为 OpenAI tool_calls 格式。

    Args:
        text_calls: extract_tool_calls_from_text() 的返回值
        tool_name_map: OpenAI 安全名称 → 原始 action_id 映射（build_tool_name_map 的返回值）

    Returns:
        OpenAI 格式的 tool_calls 列表，可直接注入 assistant 消息
    """
    # 反转映射：action_id → OpenAI 安全名称
    id_to_safe = {v: k for k, v in tool_name_map.items()}

    openai_calls: list[dict[str, Any]] = []
    for i, tc in enumerate(text_calls):
        action_id = tc.get("action_id", "")
        args = tc.get("args", {})
        safe_name = id_to_safe.get(action_id, action_id.replace(".", "_"))
        openai_calls.append({
            "id": f"text_call_{i}",
            "type": "function",
            "function": {
                "name": safe_name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
    return openai_calls


# ── OpenAI tool 定义转换 ─────────────────────────────────────────────────────


def react_catalog_to_openai_tools(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert REACT_TOOL_CATALOG entries to OpenAI function-calling format.

    Dots in action_id (e.g. ``file.read``) are replaced with underscores
    because OpenAI/DeepSeek require ``^[a-zA-Z0-9_-]+$``.
    """
    tools: list[dict[str, Any]] = []
    for entry in catalog:
        safe_name = entry["action_id"].replace(".", "_")
        schema_desc = entry.get("payload_schema", {})
        properties: dict[str, dict[str, Any]] = {}
        required: list[str] = []
        if isinstance(schema_desc, dict):
            for key, desc in schema_desc.items():
                desc_str = str(desc)
                # 检测必填标记。catalog 在本模块中写为 "(必填)"，
                # 历史上 _chat_memory.py 曾因 mojibake 编辑器导致检测字符串变为乱码，
                # 使所有 OpenAI tool 定义的 required:[] 永远为空、模型频繁省略
                # 关键参数。这里同时容忍正确编码和历史乱码。
                if "(必填)" in desc_str or "必填" in desc_str:
                    required.append(key)
                properties[key] = {
                    "type": "string",
                    "description": desc_str,
                }
        tools.append({
            "type": "function",
            "function": {
                "name": safe_name,
                "description": entry.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return tools


def build_tool_name_map(catalog: list[dict[str, Any]]) -> dict[str, str]:
    """OpenAI-safe name → original action_id 映射。"""
    return {e["action_id"].replace(".", "_"): e["action_id"] for e in catalog}


# ── 内联模型解析（P3：从 backend/react_executor.py 下沉）────────────────────

_INLINE_MODEL_RE = re.compile(r"^/model:(\S+)\s*(.*)", re.DOTALL)


def parse_inline_model(message: str) -> tuple[str, str | None, str | None]:
    """解析 '/model:deepseek-chat 你好' → ('你好', 'deepseek', 'deepseek-chat')"""
    m = _INLINE_MODEL_RE.match(message.strip())
    if m:
        raw_model = m.group(1).strip()
        remaining = m.group(2).strip()

        if raw_model == "auto":
            return (remaining, None, None)
        if raw_model == "local":
            return (remaining, "local", None)

        if ":" in raw_model:
            parts = raw_model.split(":", 1)
            return (remaining, parts[0], parts[1])
        return (remaining, None, raw_model)

    return (message, None, None)


__all__ = [
    "SAFE_TOOL_IDS_BASE",
    "REACT_TOOL_CATALOG",
    "BUTLER_DECLARED_TOOL_IDS",
    "to_step_risk",
    "extract_tool_calls_from_text",
    "text_tool_calls_to_openai_format",
    "react_catalog_to_openai_tools",
    "build_tool_name_map",
    "parse_inline_model",
]
