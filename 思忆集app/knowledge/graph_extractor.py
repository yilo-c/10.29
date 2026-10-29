"""知识图谱实体提取 — 从文本中提取实体与关系。

两条通道，**互不回落**（2026-09-16 批3 X1 修复后）：
1. LLM 提取 ``_extract_with_llm``：复杂文本主通道，**也是默认通道**
   （KG-CH，2026-09-21）——``use_llm`` 缺省为 True，省略参数即走 LLM；
2. 模式匹配 ``_extract_with_patterns``：**仅当调用方显式**传 ``use_llm=False``
   时使用（结构化短文本，如 ``info.collect`` 的资讯摘要）。

为什么默认值就是契约（KG-CH）：X1 只堵住了「LLM 失败静默回落正则」，却没动
默认参数，于是所有省略 ``use_llm`` 的调用点（生产上即记忆 auto_write 回灌
``memory/extractor/_core.py``）仍把聊天文本喂给正则，产出句子碎片噪声实体——
正是本模块明令禁止的行为。默认 ``True`` 后，无 AI 内核时按 X1 契约返回
``via="skipped"`` 留痕：**宁缺毋滥，不产噪声**。

出处契约（批3 W1 修复后）：来源不建容器实体、不建 mentioned_in 边，
统一写实体 ``properties.sources``；relations 表只存语义关系。

命名空间契约（P4-KG-ORIGIN，2026-09-26）：出处一律带域前缀。历史上各通道
写的是裸 entry_id（实测 445 条），导致「这条实体从哪来」无法判定、级联
清理认不出归属、详情面板无法判断能不能跳转。调用方可传 ``source_kind``
（note/task/inbox/info/memory/md/vocab/reading_article/deepseek/kimi…），
或由 ``store.add_entity_source`` 从已带前缀的串里自行解析。

提取结果写入 KnowledgeGraphStore。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from .graph_store import (
    ENTITY_TYPE_DATE,
    ENTITY_TYPE_LOCATION,
    ENTITY_TYPE_ORGANIZATION,
    ENTITY_TYPE_PERSON,
    ENTITY_TYPE_PROJECT,
    ENTITY_TYPE_UNKNOWN,
    REL_APPLY_TO,
    REL_MENTIONED_IN,
    REL_OPPOSE,
    REL_PART_OF,
    REL_RELATED_TO,
    REL_SUPPORT,
    KnowledgeGraphStore,
)

logger = logging.getLogger("siyi.knowledge_graph.extractor")

# ── 中文实体正则模式 ──────────────────────────────────────

# 人名：2-4 个中文字符（排除常见非人名词）
_PERSON_PATTERN = re.compile(
    r"(?<![a-zA-Z])"  # 不在英文单词中间
    r"([一-鿿]{2,4})"
    r"(?=说|表示|提到|认为|指出|告诉|问|回答|补充|强调|建议|觉得|想)",
)
# 地点：带地点后缀。
# PD-5c 修复（2026-09-18）：旧模式 `[一-鿿]{2,6}` 既无左边界约束、量词又贪婪，
# 于是「李四说要去上海市」被整段吞成地名（实测入库 "四说要去上海市"）——引擎
# 从位置 1 起贪 6 个字，恰好凑出「四说要去上海」+「市」。
#
# 中文没有词边界，用 (?<![一-鿿]) 这类硬边界会把「去上海市」这种最常见写法
# 一起收掉（那是更严重的漏提，反例实测「下周去上海市」也只剩这一种写法）。
# 故"收紧左边界"落在**名字部分的逐位置断言**上：名字内部不得出现动词/助词/
# 代词等功能字，量词便无法跨过它们把动词前缀卷进来（`(?![...])` 在量词内部
# 是零宽断言，等价于给每个字符位置加一道左边界）。
#
# 取舍：含功能字的少数地名（如来宾市/从化区）不再命中。相对"把整句动词吞成
# 一个假地名"的污染，宁缺毋滥是本提取器既有的明确契约（见 _is_noise_entity）。
_LOCATION_NAME_STOP_CHARS = "要去说想会能了吗呢吧啊我你他她它们这那不没把被让使给跟对是就也到在"
_LOCATION_PATTERN = re.compile(
    rf"((?:(?![{_LOCATION_NAME_STOP_CHARS}])[一-鿿]){{2,6}}"
    r"(?:省|市|区|县|镇|村|路|街|大道|大厦|大楼|广场|酒店|公司|大学|学院|医院))"
)
# 日期时间
# Bug 1 修复：原模式第一个 alternative 所有部分都是可选的（每个 (?:...)?），
# 导致其可以匹配空字符串，进而吞并整个匹配过程，使第三 alternative
# （昨天|今天|明天|前天|后天）永不被尝试。修复方式：把第三 alternative 提前，
# 并强制第一 alternative 至少匹配一个成分（用内嵌非可选 group）。
# 同时新增 (?:今年|去年|明年|前年) 作为独立 alternative，让相对年份可单独被捕获。
_DATE_PATTERN = re.compile(
    r"((?:昨天|今天|明天|前天|后天)"
    r"|(?:今年|去年|明年|前年)"
    r"|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?"
    r"|(?:今年|去年|明年|前年)?"
    r"(?:上个?|下个?|这个?)?"
    r"(?:周[一二三四五六日天]|星期[一二三四五六日天])"
    r"(?:[一二三四五六七八九十]+月)?"
    r"(?:[一二三四五六七八九十]+[号日])?"
    r"|(?:今年|去年|明年|前年)?"
    r"(?:上个?|下个?|这个?)?"
    r"(?:[一二三四五六七八九十]+月)"
    r"(?:[一二三四五六七八九十]+[号日])?"
    r")"
)
# 项目名：以"项目"结尾或带特定前缀
_PROJECT_PATTERN = re.compile(r"([一-鿿A-Za-z0-9]{2,20}(?:项目|工程|计划|任务|课题|活动|方案|系统|平台|工具))")
# 组织名
_ORG_PATTERN = re.compile(r"([一-鿿]{2,10}(?:公司|集团|组织|团队|部门|组|委员会|协会|俱乐部|社|群))")


def _generate_entity_id(prefix: str = "ent") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── LLM 提取提示词 ─────────────────────────────────────────

_EXTRACT_ENTITIES_PROMPT = (
    "你是一个严谨的知识图谱实体提取器。从下面的文本中提取【具体的命名实体】和它们之间的明确关系。\n\n"
    "基本要求：\n"
    "- 只提取那种像【专有名词】一样、可被唯一识别的实体：人物(姓名/称号)、真实地点、项目/产品名、组织/公司名、关键技术/概念名。\n"
    "- 绝对不要提取句子、半个句子、从句或描述性短语（例如「使用第三方平台」「请尝试备用方案」「这些平台」）。\n"
    "- 绝对不要提取通用词/抽象概括语（例如「开源项目」「两种方案」「你的项目」）。\n"
    "- 长度限制：优先 2~20 个字；包含 3 个以上空格/标点的长描述一律丢弃。\n"
    "- 宁缺毋滥：若不明确，宁可少提取，也不要产出噪声实体。\n"
    "返回格式为 JSON，不要包含任何其他内容：\n"
    "{{\n"
    '  "entities": [\n'
    '    {{"name": "具体名称", "type": "person|location|project|event|organization|concept", "aliases": [], '
    '"detail": "这个实体是什么的1-2句中文描述，从原文归纳，不编造；原文没有可归纳的信息时给空字符串"}}\n'
    "  ],\n"
    '  "relations": [\n'
    '    {{"source": "实体A名称", "target": "实体B名称", '
    '"relation": "support|oppose|part_of|apply_to|related_to"}}\n'
    "  ]\n"
    "}}\n\n"
    "detail 字段要求（P4-KG-DETAIL，2026-10-15）：\n"
    "- 只写原文确实支持的内容：这个实体在原文里是什么、做了什么、和什么有关；\n"
    "- 禁止百科式扩写、禁止把实体类型复读一遍（如「某是一种概念」这类无信息句）；\n"
    "- 原文对这个实体只有名字、没有任何可归纳信息时，detail 给空字符串（宁缺毋滥）。\n\n"
    "关系类型含义：\n"
    "- support：实体 A 支持/赞成/推荐实体 B（如「A 推荐用 B」「A 认可 B 方案」）\n"
    "- oppose：实体 A 反对/不推荐实体 B（如「A 排除了 B」「A 对 B 有顾虑」）\n"
    "- part_of：实体 A 是实体 B 的组成部分/成员/子类（如「B 包含 A」「A 属于 B」）\n"
    "- apply_to：实体 A 适用于/应用于实体 B（如「A 方案用在 B 项目」「A 技术用于 B」）\n"
    "- related_to：其他一般关联（默认）\n"
    "只输出 JSON 数组中的对象，relation 字段必须取自上述 5 个值。\n\n"
    "文本：\n{text}\n"
)


class KnowledgeGraphExtractor:
    """知识图谱实体提取器。

    用法:
        extractor = KnowledgeGraphExtractor(store, ai_client=None)
        extractor.extract_from_text("我上周和 小明 讨论了 思忆集重构项目 的方案")
        stats = extractor.get_extraction_stats()
    """

    def __init__(
        self,
        graph_store: KnowledgeGraphStore,
        ai_client: Any = None,
    ):
        self.store = graph_store
        self.ai_client = ai_client
        self._stats: dict[str, int] = {
            "pattern_extractions": 0,
            "llm_extractions": 0,
            "total_entities": 0,
            "total_relations": 0,
        }

    # ── 公开接口 ──────────────────────────────────────────────

    def extract_from_text(
        self,
        text: str,
        source_id: str = "",
        use_llm: bool = True,
        source_kind: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        """从文本中提取实体和关系并存入图谱。

        Args:
            text: 输入文本。
            source_id: 来源 ID，记入实体 ``properties.sources``/``origins``（W1 出处契约）。
            use_llm: 提取通道。**默认 True = LLM 通道**（KG-CH，2026-09-21）；
                仅结构化短文本显式传 ``False`` 才走正则。无 AI 内核且
                ``use_llm=True`` 时返回 ``via="skipped"``，不回落正则。
            source_kind: 出处所属域（P4-KG-ORIGIN）。不传则从 ``source_id`` 前缀
                自行解析；两者都没有时归 ``unknown``（仍优于静默丢信源）。

        Returns:
            {"entities": [...], "relations": [...], "via": ...}
        """
        if not text or not text.strip():
            return {"entities": [], "relations": [], "via": "empty"}

        if use_llm:
            if self.ai_client is None:
                # X1 修复（2026-09-16 批3）：无内核 = 跳过，不静默回落正则。
                # 判定基准与 md_parser.py:178-180 对齐——正则通道对聊天文本会
                # 制造大量句子碎片噪声实体，产垃圾比产出空更有害。
                logger.warning("graph_extractor: use_llm=True 但无 ai_client，跳过本轮提取（不回落正则）")
                return {
                    "entities": [],
                    "relations": [],
                    "via": "skipped",
                    "error": "no_ai_client",
                }
            return self._extract_with_llm(text, source_id, source_kind=source_kind)

        return self._extract_with_patterns(text, source_id, source_kind=source_kind)

    def extract_from_memory(
        self,
        memory_entry: dict[str, Any],
        use_llm: bool = True,
        source_kind: str = "memory",
    ) -> dict[str, list[dict[str, Any]]]:
        """从记忆条目中提取（自动拼接 content 字段）。

        ``use_llm`` 默认 True：记忆条目是聊天/叙述体文本，正是正则通道的
        噪声源（KG-CH，2026-09-21）。要走低精度但零成本的通道须显式传 False。

        ``source_kind`` 默认 ``memory``：本通道产出的出处实体永远来自结构化记忆
        条目，域是确定的，因此写死而不是让调用方每次传（P4-KG-ORIGIN：统一转化
        落在单点，而不是散在 6 个调用方）。
        """
        content_parts = []
        for field in ("content", "summary", "title", "text", "description"):
            val = memory_entry.get(field)
            if val and isinstance(val, str):
                content_parts.append(val)
        if not content_parts:
            return {"entities": [], "relations": []}

        text = " | ".join(content_parts)
        # G4-OK: 主键透传，extract_from_text 显式带 source_kind 域声明（出处=(kind,id) 对）
        source_id = memory_entry.get("entry_id") or memory_entry.get("id", "")
        return self.extract_from_text(text, source_id=source_id, use_llm=use_llm, source_kind=source_kind)

    # ── 模式提取 ──────────────────────────────────────────────

    def _extract_with_patterns(
        self,
        text: str,
        source_id: str = "",
        source_kind: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        """使用正则模式快速提取实体。"""
        entities_found: dict[str, dict[str, Any]] = {}
        relations: list[dict[str, Any]] = []

        # 人名提取
        for match in _PERSON_PATTERN.finditer(text):
            name = match.group(1).strip()
            if len(name) >= 2 and not self._is_common_word(name):
                eid = self._add_entity_if_new(name, ENTITY_TYPE_PERSON)
                if eid:
                    entities_found[eid] = self.store.get_entity(eid) or {}

        # 地点提取
        for match in _LOCATION_PATTERN.finditer(text):
            name = match.group(1).strip()
            eid = self._add_entity_if_new(name, ENTITY_TYPE_LOCATION)
            if eid:
                entities_found[eid] = self.store.get_entity(eid) or {}

        # 日期提取
        for match in _DATE_PATTERN.finditer(text):
            name = match.group(1).strip()
            if len(name) >= 2:
                eid = self._add_entity_if_new(name, ENTITY_TYPE_DATE)
                if eid:
                    entities_found[eid] = self.store.get_entity(eid) or {}

        # 项目名提取
        for match in _PROJECT_PATTERN.finditer(text):
            name = match.group(1).strip()
            eid = self._add_entity_if_new(name, ENTITY_TYPE_PROJECT)
            if eid:
                entities_found[eid] = self.store.get_entity(eid) or {}

        # 组织名提取
        for match in _ORG_PATTERN.finditer(text):
            name = match.group(1).strip()
            eid = self._add_entity_if_new(name, ENTITY_TYPE_ORGANIZATION)
            if eid:
                entities_found[eid] = self.store.get_entity(eid) or {}

        # W1 修复（2026-09-16 批3）：出处是元数据，不是语义关系。
        # 旧实现在这里为每个来源建 `mem_*` 容器实体 + N 条 mentioned_in 边，
        # 导致 86% 的"关系"根本不是关系、图的形状由容器决定（审计 W1）。
        # 现在出处写进实体自身 properties.sources，不再造边、不再造容器节点。
        if source_id and entities_found:
            for eid in entities_found:
                if self._record_provenance(eid, source_id, source_kind):
                    self._stats["provenance_recorded"] = self._stats.get("provenance_recorded", 0) + 1

        self.store.save()
        self._stats["pattern_extractions"] += 1
        self._stats["total_entities"] = len(self.store.get_all_entities())
        self._stats["total_relations"] = self._count_semantic_relations()

        return {
            "entities": list(entities_found.values()),
            "relations": relations,
            "via": "patterns",
        }

    # ── LLM 提取 ──────────────────────────────────────────────

    @staticmethod
    def _is_noise_entity(name: str) -> bool:
        """启发式去噪：过滤 LLM 可能产出的句子片段/通用短语。

        判断依据（分层）：
        - 长度 < 2 或超过 30
        - 含句读/从句标点（句号、逗号、分号、冒号、问号、感叹号、顿号）
        - 含 2 个以上空白（多词句子）
        - 命中常见通用/代词起始模式（这些、这个、你/我/他 + 短语）
        - 动词性短语（以"编辑/定义/获取/使用…"等动作开头）
        - 疑问/语气碎片（含"什么/怎么/为什么/吗/呢/是不是…"）
        - 描述性短语（含"的…""之一/的时候/的方式"等指示结构）
        """
        if not name:
            return True
        text = name.strip()
        if len(text) < 2 or len(text) > 30:
            return True
        if re.search(r"[，。！？；：、,;:!?]", text):
            return True
        if re.search(r"\s{2,}", text):
            return True
        # 疑问/语气碎片：几乎不可能是命名实体
        if re.search(r"(?:什么|怎么|为什么|吗|呢|吧|嘛|是不是|有没有|可不可以|会不会|好不好)", text):
            return True
        # 描述性指示结构：短语而非命名实体
        if re.search(r"(?:之一|的时候|的地方|的方式|的方法|的问题|的过程|的情况|的东西|的原因|的话|的意思是)", text):
            return True
        # 片段性/动词性/引导语特征（描述动作或过程，不像命名实体）。长度 > 20 时
        # 已超过命名实体上限，直接丢弃，因此这里只对 ≤20 字做引导语命中判定。
        if len(text) <= 20 and re.search(
            r"(?:"
            r"^[的了而在和或及经因而自将把被让对]|"  # 虚词/介词/连词开头
            r"^(?:以及|并且|或者|还有|还有着)|"  # 连词/承接词开头
            r"^(?:这里|那里|里面|外面|这一|那一|你|我|他|她|它)|"
            r"^请|^这[个种两几]|^那[个种两几]|^一种|^其中|^以下|^比如|^例如|^通过|^进行|^一个|^这条|^这边|^那里|^获取|^使用|^表示|^是指|^关于|^指的|^我们|^大家|^其它|^其他|"
            r"这个Key|"
            r"(?:有用|可以|用来|用于|尝试|引导|相关|这个|这些|那些|聊到|提到|关于|目标是|是指|相当于|其实就是)"
            r")",
            text,
        ):
            return True
        # 动词性短语：以常见动作开头且为中文短语（排除英文/数字技术名）
        if (
            re.search(
                r"^(?:编辑|定义|获取|使用|安装|运行|创建|实现|修复|升级|优化|支持|提供|进行|选择|确认|提交|部署|发布|更新|增加|减少|打开|关闭|下载|上传|启动|停止|处理|管理|解决|完成|开始|继续|调整|配置|接入|集成|编译|打包|调试|测试|评估|验证|搭建|设计|开发|建设|制作|生成|输出|输入|复制|粘贴|删除|清理|合并|拆分|导出|导入|转换|迁移|替换|覆盖|启用|禁用|设置|读取|写入|检索|搜索|查看|展示|显示|输出|收到|遇到|实现|需要|想要|打算|计划|准备|尝试|希望|建议|推荐|要求|允许|禁止|确保|保证|控制|限制|决定|判断|认为|觉得|发现|看到|听到|知道|理解|明白|记住|忘记)",
                text,
            )
            and len(text) <= 20
        ):
            return True
        # 含"的"字的描述性中文短语（排除含空格/连字符的技术名、品牌名常见后缀）
        if (
            "的" in text and len(text) <= 20 and not re.search(r"[a-zA-Z0-9_/\-\.:]", text)  # 技术路径/版本名不受影响
        ):
            return True
        # ── 增强：句子级碎片（实测大量实体是聊天句子片段）──
        # 1. 句子残片：长度 <= 10 且（含 >=2 个句法助词 或 以「的/了/在/是/也/很」等强句末词结尾）
        #    要求含 >=2 个不同助词或强句末词，避免误伤「成都」「云南」等地名/品牌名
        #    注意「都」不作为句末强词（成都/江都等地名常见）
        if len(text) <= 10 and (len(re.findall(r"[的了是在有能要会就也]", text)) >= 2 or re.search(r"[的了在也是很]$", text)):
            return True
        # 2. 以「等等/之类/这些/那些/等网站/等平台/等社区」结尾的列举残片
        if re.search(r"(?:等等|等网站|等平台|之类|等社区|这些|那些|这些内容|这些工具|这些平台)$", text):
            return True
        # 3. 明显残缺：以「的/是/了/在/这/那/从/以/其/某」开头的无主短语（<=12 字）
        if len(text) <= 12 and re.match(r"^(?:的|是|了|在|这|那|从|以|其|某)", text):
            return True
        # 4. 以「获取/访问/使用/实现/支持/提供/安装/运行/创建」等动词开头的纯中文动作短语（<=16 字）
        #    说明是动作描述而非命名实体；技术产品名通常短且不以这些动作词开头
        if (
            len(text) <= 16
            and not re.search(r"[a-zA-Z0-9_/\-\.:]", text)
            and re.match(r"^(?:获取|访问|使用|实现|支持|提供|安装|运行|创建|实现|构建|部署|编写|开发)", text)
        ):
            return True
        # 5. 口语动词短语：以「给/帮/请/求/总结/写/做/分析/推荐/说/问/查/搜/找」等动词开头
        #    且为纯中文短语（<=24 字），多为聊天里的指令/请求残片而非命名实体
        if len(text) <= 24 and re.match(
            r"^(?:给(?:我|你|大家)?|帮(?:我|你)?|请(?:问|求)?|总结(?:一下|来)?|写(?:个|一下)?|"
            r"做(?:个|一下|好)?|分析(?:一下|一下下)?|推荐|建议|麻烦|试(?:试|一下)?|"
            r"说(?:说|下)?|问(?:问)?|查(?:查|询)?|搜(?:搜)?|找(?:找)?|弄(?:个|一下)?)",
            text,
        ):
            return True
        # 6. 以「来/一下/一下下/看看/试试/出(来)」等口语词结尾的短语残片（<=12 字）
        if len(text) <= 12 and re.search(r"(?:来|一下|一下下|看看|试试|出来?|下去?)$", text):
            return True
        # 6b. 2-4 字短语以「帮我/给我/你/更/访/据/再」等口语/连接词结尾的残片
        #    （不用「理/路/里/来」——阿里/重庆路/逻辑等地名品牌名会误伤）
        if 2 <= len(text) <= 4 and not re.search(r"[a-zA-Z0-9_/\-\.:]", text) and re.search(r"(?:帮我|给我|你|更|访|据|再|给|说)$", text):
            return True
        # 7. 超长纯中文短语（>20 字）：真正的命名实体极少超过 20 字，
        #    多为从句子中间截取的描述性残片（如「还有各种专注于特定领域或采用新型模式的平台」）
        if len(text) > 20 and not re.search(r"[a-zA-Z0-9_/\-\.:]", text):
            return True
        # 8. 常见抽象残片黑名单：双字抽象词/连接残片（不误伤地名/品牌名/技术名）
        if text in {
            "当被",
            "月提",
            "太理",
            "听我",
            "立刻",
            "问或",
            "四海",
            "以剑",
            "很有",
            "是小",
            "还有",
            "根据",
            "基于",
            "针对",
            "通过",
            "作为",
            "进入",
            "执行",
            "快速",
            "精准",
            "加一",
            "就是",
            "不是",
            "这是",
            "那是",
            "这样",
            "那样",
            "一些",
            "很多",
            "有些",
            "别的",
            "这里",
            "那里",
            "上面",
            "下面",
            "后面",
            "前面",
            "然后",
            "并且",
            "或者",
            "但是",
            "因为",
            "所以",
            "如果",
            "虽然",
            "最好",
            "更好",
            "最理",
            "更别",
            "同样",
            "长期",
            "补充",
            "任何",
            "诚恳",
            "迅速",
            "值得",
            "实操",
            "进来",
            "进去",
            "进店",
            "进去",
            "吃住",
            "法院",
            "老子",
            "老丈",
            "集谛",
            "更别",
            "但我",
            "恰恰",
            "想请",
        }:
            return True
        return False

    @staticmethod
    def _normalize_relation(rel: str) -> str | None:
        """把 LLM 返回的关系谓词归一化为准入的 relation_type，未命中返回 None。

        W2 修复（2026-09-16 批3）：旧实现在末行 ``return REL_RELATED_TO`` 把一切
        认不出的谓词无条件退化为 related_to——真动词被降级塞进 description，
        制造"伪关系边"（实测 55 条 = 5.3%）。现在：
        - LLM 显式给出的 ``related_to``（含中文同义词「相关/关联」）仍然合法；
        - 未识别谓词返回 ``None`` → 调用方丢弃该边并计入 ``relation_dropped``，
          宁缺毋滥，与 §1.1 关系准入标准（C1-C4）一致。
        """
        rel = (rel or "").strip().lower()
        if rel in (REL_SUPPORT, "support", "支持", "赞成", "推荐", "认可", "赞同", "建议用", "选择"):
            return REL_SUPPORT
        if rel in (REL_OPPOSE, "oppose", "反对", "不推荐", "排除", "否定", "有顾虑", "弃用"):
            return REL_OPPOSE
        if rel in (REL_PART_OF, "part_of", "属于", "包含于", "组成部分", "成员", "子类", "隶属于"):
            return REL_PART_OF
        if rel in (REL_APPLY_TO, "apply_to", "应用于", "用于", "适用", "应用到", "方案用于"):
            return REL_APPLY_TO
        if rel in (REL_RELATED_TO, "related_to", "相关", "关联"):
            return REL_RELATED_TO
        return None

    def _extract_with_llm(
        self,
        text: str,
        source_id: str = "",
        source_kind: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        """使用 LLM 提取实体和关系。

        X1 修复（2026-09-16 批3）：LLM 调用失败 / 返回不可解析时**不再静默回落
        正则通道**（旧行为会把聊天句子切成噪声实体入库，且上层看到的是一次
        "成功"提取）。现在失败返回 ``via="skipped"`` + error，空手而归但留痕。
        """
        if not self.ai_client:
            return {
                "entities": [],
                "relations": [],
                "via": "skipped",
                "error": "no_ai_client",
            }

        prompt = _EXTRACT_ENTITIES_PROMPT.format(text=text[:2000])
        try:
            if not hasattr(self.ai_client, "complete_text"):
                logger.warning("AI client 不支持 complete_text，跳过本轮图谱提取（不回落正则）")
                return {
                    "entities": [],
                    "relations": [],
                    "via": "skipped",
                    "error": "ai_client_no_complete_text",
                }
            reply = self.ai_client.complete_text(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
            )

            import json

            data = json.loads(reply.strip().removeprefix("```json").removesuffix("```").strip())
            if not isinstance(data, dict):
                logger.warning(
                    "graph_extractor: LLM 返回非对象 JSON(%s)，跳过本轮（不回落正则）",
                    type(data).__name__,
                )
                return {
                    "entities": [],
                    "relations": [],
                    "via": "skipped",
                    "error": f"non_object_json:{type(data).__name__}",
                }
            llm_entities = data.get("entities", [])
            llm_relations = data.get("relations", [])

            entity_id_map: dict[str, str] = {}
            for ent in llm_entities:
                name = ent.get("name", "").strip()
                etype = ent.get("type", ENTITY_TYPE_UNKNOWN)
                aliases = ent.get("aliases", [])
                # P4-KG-DETAIL（2026-10-15）：detail 是实体详情（这个实体是什么），
                # 与 name/type/aliases 同级的一次性提取产物；空则不下发（宁缺毋滥）。
                detail = str(ent.get("detail") or "").strip()[:300]
                if not name or self._is_noise_entity(name):
                    continue
                existing = self.store.find_by_name(name)
                if existing:
                    eid = existing[0].get("id", "")
                    entity_id_map[name] = eid
                    # 补充别名 / 详情（properties 走 update 合并，不触碰 name/type）
                    if aliases or detail:
                        self.store.add_entity(
                            eid,
                            name,
                            etype,
                            aliases=aliases or None,
                            properties={"detail": detail} if detail else None,
                        )
                else:
                    eid = self.store.add_entity(
                        _generate_entity_id(),
                        name,
                        etype,
                        aliases=aliases,
                        properties={"detail": detail} if detail else None,
                    )
                    entity_id_map[name] = eid

            accepted_relations: list[dict[str, Any]] = []
            for rel in llm_relations:
                src_name = rel.get("source", "").strip()
                tgt_name = rel.get("target", "").strip()
                rel_desc = rel.get("relation", REL_RELATED_TO)
                if src_name not in entity_id_map or tgt_name not in entity_id_map:
                    continue
                normalized = self._normalize_relation(rel_desc)
                if normalized is None:
                    # W2：未准入谓词丢弃并留痕，不再伪造成 related_to
                    self._stats["relation_dropped"] = self._stats.get("relation_dropped", 0) + 1
                    logger.info(
                        "graph_extractor: 丢弃未准入谓词 %r（%s → %s）",
                        rel_desc,
                        src_name,
                        tgt_name,
                    )
                    continue
                self.store.add_relation(
                    entity_id_map[src_name],
                    entity_id_map[tgt_name],
                    normalized,
                    {"description": rel_desc},
                )
                accepted_relations.append({"source": entity_id_map[src_name], "target": entity_id_map[tgt_name], "type": normalized})

            # W1 修复：出处写 properties.sources，不再建容器实体 + mentioned_in 边
            if source_id and entity_id_map:
                added = 0
                for eid in entity_id_map.values():
                    if self._record_provenance(eid, source_id, source_kind):
                        added += 1
                # 增量迁移：该实体上遗留的旧版 mentioned_in 出处边（含容器节点）就地迁出
                added += self._purge_stale_provenance_edges(entity_id_map.values())
                self._stats["provenance_recorded"] = self._stats.get("provenance_recorded", 0) + added

            self.store.save()
            self._stats["llm_extractions"] += 1
            self._stats["total_entities"] = len(self.store.get_all_entities())

            data["relations"] = accepted_relations
            data["via"] = "llm"
            return data

        except Exception as exc:
            logger.warning("LLM 图谱提取失败，跳过本轮（不回落正则）: %s", exc, exc_info=True)
            return {
                "entities": [],
                "relations": [],
                "via": "skipped",
                "error": f"{type(exc).__name__}: {exc}",
            }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _add_entity_if_new(self, name: str, entity_type: str) -> str | None:
        """添加实体，已存在时复用。入库前统一做噪声过滤（源头拦截）。"""
        name = (name or "").strip()
        if not name:
            return None
        # 源头拦截：所有通道（正则/LLM）入库前都过噪声判定
        if self._is_noise_entity(name):
            return None
        existing = self.store.find_by_name(name)
        if existing:
            return existing[0].get("id")
        eid = _generate_entity_id(f"ent_{entity_type[:3]}")
        self.store.add_entity(eid, name, entity_type)
        return eid

    @staticmethod
    def _is_common_word(name: str) -> bool:
        """判断是否常见非实体词（过滤误匹配）。"""
        COMMON_WORDS = {
            "什么",
            "怎么",
            "这么",
            "那么",
            "这个",
            "那个",
            "这些",
            "那些",
            "没有",
            "可以",
            "可能",
            "应该",
            "需要",
            "知道",
            "觉得",
            "看到",
            "如果",
            "虽然",
            "因为",
            "所以",
            "而且",
            "但是",
            "然后",
            "最后",
            "开始",
            "继续",
            "完成",
            "还有",
            "已经",
            "正在",
            "一直",
            "一起",
            "工作",
            "时间",
            "问题",
            "结果",
            "原因",
            "情况",
            "时候",
            "东西",
            "项目",
            "方式",
            "方案",
            "事情",
            "方面",
            "方法",
            "数据",
        }
        return name.lower() in COMMON_WORDS or len(name) > 12

    # ── 出处层（W1，2026-09-16 批3）────────────────────────────
    #
    # 旧版 ``_ensure_memory_entity`` 已删除：它把每条来源对话/笔记建成
    # ``mem_*`` 容器实体并用 mentioned_in 边相连，出处元数据因此冒充语义
    # 关系（实测占关系表 86%），且容器成为度数最高的"枢纽节点"，图的形状
    # 由容器而非知识决定。新契约：出处 = 实体 properties.sources。

    def _record_provenance(self, entity_id: str, source_id: str, kind: str = "") -> bool:
        """把 source_id 记为实体的出处（不建边、不建容器）。

        ``kind`` 非空时走带命名空间的写法（P4-KG-ORIGIN）；旧 store/测试桩
        没有 ``kind`` 形参时退回两参调用，不因接不上而丢出处。
        """
        fn = getattr(self.store, "add_entity_source", None)
        if fn is None:  # 测试桩/旧 store 无出处层时静默跳过（不降级为建边）
            return False
        try:
            if kind:
                return bool(fn(entity_id, source_id, kind=kind))
            return bool(fn(entity_id, source_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("记录图谱出处失败 %s ← %s: %s", entity_id, source_id, exc)
            return False

    def _purge_stale_provenance_edges(self, entity_ids) -> int:
        """增量迁移：把实体上遗留的旧版 mentioned_in 出处边迁到 properties.sources。

        让 W1 修复对历史数据逐实体生效（碰到谁迁谁），不必等待全库清洗脚本。
        """
        fn = getattr(self.store, "migrate_provenance_edges", None)
        if fn is None:
            return 0
        try:
            return int(fn(entity_ids) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("迁移遗留出处边失败: %s", exc)
            return 0

    def _count_semantic_relations(self) -> int:
        """语义关系计数——显式排除 mentioned_in 出处边（含未迁移的遗留边）。

        统计层早已不把 mentioned_in 当关系（旧 :245-247 即证据），拆层后保持
        同一口径：relations 表里的真关系数。
        """
        try:
            return len([r for r in self.store.get_all_relations() if r.get("relation_type") != REL_MENTIONED_IN])
        except Exception:  # noqa: BLE001
            return self._stats.get("total_relations", 0)

    # A2（审计 §5.1）：注入提示词的边必须是强语义谓词。
    # mentioned_in（出处）与 related_to（弱兜底）不进 prompt——否则对话里
    # 会反复出现"AI mentioned_in 记忆:deepseek:xxx"这类零信息行，污染上下文。
    _PROMPT_RELATION_WHITELIST = frozenset({REL_PART_OF, REL_APPLY_TO, REL_SUPPORT, REL_OPPOSE})

    def query_for_prompt_context(self, text: str) -> str:
        """生成提示词上下文片段：文本中涉及的实体信息。

        被 PromptContext 调用，注入到系统提示词中。
        """
        entities = set()
        # 从文本中提取候选名称
        # PD-5b（2026-09-18）：本处是**召回**场景（滑窗切词后逐词找相关实体，
        # 「量子」应能带出「量子计算」），故显式开启子串折叠——与身份解析
        # （_add_entity_if_new 走精确）区分开。
        words = re.findall(r"[一-鿿]{2,6}", text)
        for word in words:
            matches = self.store.find_by_name(word, allow_substring=True)
            for m in matches:
                entities.add((m.get("name", ""), m.get("type", "")))

        if not entities:
            return ""

        lines: list[str] = []
        for name, etype in sorted(entities):
            lines.append(f"  - {name} ({etype})")
            # 获取关联实体（A2：只注入白名单内的强语义关系）
            eid = self.store.find_by_name(name)[0].get("id", "")
            relations = self.store.get_relations(eid)
            semantic = [r for r in relations if r.get("relation_type") in self._PROMPT_RELATION_WHITELIST]
            for rel in semantic[:3]:  # 最多 3 条关联
                src = self.store.get_entity(rel.get("source_id", ""))
                tgt = self.store.get_entity(rel.get("target_id", ""))
                other = tgt if src and src.get("id") == eid else src
                if other and other.get("id") != eid:
                    lines.append(f"    └─ {rel.get('relation_type')} → {other.get('name', '?')}")

        if not lines:
            return ""

        result = "\n[知识图谱上下文 — 当前提及的实体]\n" + "\n".join(lines) + "\n"
        return result

    def get_extraction_stats(self) -> dict[str, int]:
        """获取提取统计。"""
        return dict(self._stats)
