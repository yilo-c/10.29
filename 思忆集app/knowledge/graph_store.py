"""知识图谱存储 — 实体与关系的轻量图存储。

实体节点 + 关系边，邻接表结构，JSON 持久化。
支持实体 CRUD、关系管理、多跳遍历。

P1.4 增强：
- **原子写**：`save()` 用 temp file + `os.replace`，进程崩溃不损坏既有文件
- **反向邻接索引**：`_reverse_adjacency` 让 `get_relations` 入边查询 O(1)
- **增量索引更新**：`_add_relation_to_index` / `_remove_relation_from_index` 替代每次 `_rebuild_index`
- **并发锁**：`threading.RLock` 保护所有公共方法，多线程 to_thread 调用安全
- **事务 API**：`with store.transaction(): ...` 异常时回滚内存状态
- **方向感知遍历**：`traverse(direction="out"|"in"|"both")`
- **search 方法**：让 KG 真正参与 `UnifiedKnowledgeService` 统一查询
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from data._atomic_json import atomic_write_json

logger = logging.getLogger("siyi.knowledge_graph.store")

# ── 实体类型 ──────────────────────────────────────────────

ENTITY_TYPE_PERSON = "person"
ENTITY_TYPE_LOCATION = "location"
ENTITY_TYPE_EVENT = "event"
ENTITY_TYPE_PROJECT = "project"
ENTITY_TYPE_DATE = "date"
ENTITY_TYPE_ORGANIZATION = "organization"
ENTITY_TYPE_CONCEPT = "concept"
ENTITY_TYPE_TAG = "tag"
ENTITY_TYPE_UNKNOWN = "unknown"

# ── 关系类型 ──────────────────────────────────────────────

REL_MENTIONED_IN = "mentioned_in"
REL_RELATED_TO = "related_to"
REL_PART_OF = "part_of"
REL_LOCATED_AT = "located_at"
REL_PARTICIPATED_IN = "participated_in"
REL_BEFORE = "before"
REL_AFTER = "after"
REL_SAME_AS = "same_as"
REL_SUPPORT = "support"
REL_OPPOSE = "oppose"
REL_APPLY_TO = "apply_to"
# PD-3（2026-09-17）由 _external_content 引入，但一直游离在词表外——
# 前端 relation_type_display 拿不到它就只能显示英文原文。在此登记为正式成员。
REL_SOURCED_FROM = "sourced_from"

# 出处 kind 词表（与 knowledge/node_profile.py 保持同步；此处只做形状收敛，
# 不 import 那边避免循环依赖）。
_ORIGIN_SEPARATOR = ":"


def split_origin(raw: Any) -> tuple[str, str]:
    """把一条出处串拆成 ``(kind, source_id)``，无命名空间时 kind 归 ``unknown``。

    背景（P4-KG-ORIGIN，2026-09-26）：各通道历史上各写各的——`"note:42"`、
    `"deepseek:conv-9"`、`"kimi_1712..."`（下划线）、以及**裸 entry_id**
    （命名空间已丢）。于是级联清理认不出谁是谁、详情面板无法判定能不能跳转。
    """
    text = str(raw or "").strip()
    if not text:
        return ("unknown", "")
    if _ORIGIN_SEPARATOR in text:
        head, _, tail = text.partition(_ORIGIN_SEPARATOR)
        kind, ident = head.strip(), tail.strip()
        if kind and ident:
            return (kind, ident)
    if text.startswith("kimi_"):
        return ("kimi", text[len("kimi_") :])
    return ("unknown", text)


def format_origin(kind: str, source_id: Any) -> str:
    """`split_origin` 的逆：生成规范出处串（无 kind 时退回裸 id，不编命名空间）。"""
    ident = str(source_id or "").strip()
    if not ident:
        return ""
    kind_text = str(kind or "").strip()
    if not kind_text or kind_text == "unknown":
        return ident
    return f"{kind_text}{_ORIGIN_SEPARATOR}{ident}"


def normalize_name(name: str) -> str:
    """实体名的**规范化键**——全库唯一的"名字是否同一个"口径。

    处理：NFKC（全角→半角等兼容折叠）→ 去首尾空白并把内部连续空白压成一个
    空格 → ``casefold()``（比 ``lower()`` 更彻底）。

    为什么必须是共享函数（PD-5b，2026-09-18）：名称索引的**写入端**与**查询端**
    若各写一套规范化，就会重现本批次的同源缺陷形态——PD-5a 是"导入通道按原文
    本判重、大小写异体各建一节点"，PD-5b 是"查询端用子串折叠把新实体折叠进旧
    实体"。两者根因相同：**同一实体的名字在不同通道被当成不同的名字**。
    故这里把口径收敛成一处，四处索引维护与查找共用。
    """
    import unicodedata

    folded = unicodedata.normalize("NFKC", str(name or ""))
    return " ".join(folded.split()).casefold()


class KnowledgeGraphStore:
    """轻量知识图谱存储（线程安全 + 原子持久化）。

    用法:
        store = KnowledgeGraphStore("data/knowledge_graph.json")
        store.add_entity("proj-1", "思忆集重构", ENTITY_TYPE_PROJECT)
        store.add_entity("person-1", "小明", ENTITY_TYPE_PERSON)
        store.add_relation("person-1", "proj-1", REL_PARTICIPATED_IN)
        store.save()

        # 多步原子写：异常时自动回滚内存状态
        with store.transaction():
            store.add_entity("e1", "实体1")
            store.add_relation("e1", "proj-1", REL_RELATED_TO)
            if not ok:
                raise RuntimeError("rollback")
            store.save()
    """

    def __init__(self, path: str | os.PathLike | None = None):
        # R2-C1：默认路径优先 honor SIYI_DATA_DIR（隔离实例），
        # 缺省回落源码树 data/knowledge_graph.json（env 未设时行为不变）。
        if path is None:
            _data_dir = os.environ.get("SIYI_DATA_DIR", "").strip()
            path = os.path.join(_data_dir, "knowledge_graph.json") if _data_dir else "data/knowledge_graph.json"
        self.path = os.fspath(path)
        self._entities: dict[str, dict[str, Any]] = {}  # id -> entity
        self._relations: list[dict[str, Any]] = []  # list of relation edges
        self._adjacency: dict[str, list[dict[str, Any]]] = {}  # source_id -> [relation, ...] (出边)
        self._reverse_adjacency: dict[str, list[dict[str, Any]]] = {}  # target_id -> [relation, ...] (入边)  # P1.4
        self._name_index: dict[str, str] = {}  # normalized name -> entity_id
        self._loaded = False
        self._lock = threading.RLock()  # P1.4: 保护所有公共方法

    # ── 持久化 ──────────────────────────────────────────────

    def load(self) -> None:
        """从 JSON 文件加载图数据（线程安全、幂等）。"""
        with self._lock:
            if self._loaded:
                return
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._entities = data.get("entities", {})
                    self._relations = data.get("relations", [])
                    self._rebuild_index()
                    logger.info("知识图谱已加载: %d 实体, %d 关系", len(self._entities), len(self._relations))
                except (json.JSONDecodeError, OSError) as exc:
                    # 七维-数C3（2026-08-29）：损坏文件先隔离改名保留物证，再置空图。
                    # 此前仅 warning 后置空图，后续任何一次 save() 会用空图
                    # os.replace 掉原文件——损坏窗口被放大为确定性数据丢失。
                    quarantine = f"{self.path}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
                    try:
                        os.replace(self.path, quarantine)
                        logger.warning("知识图谱加载失败，原文件已隔离为 %s: %s", quarantine, exc)
                    except OSError:
                        logger.warning("知识图谱加载失败且隔离失败（原文件保留原地）: %s", exc)
                    self._entities = {}
                    self._relations = []
                    self._adjacency = {}
                    self._reverse_adjacency = {}
                    self._name_index = {}
            self._loaded = True

    def save(self) -> None:
        """原子保存图数据到 JSON 文件。

        P1.4：写入临时文件 + `os.replace` 原子替换，进程崩溃不会损坏既有文件。
        Windows 上 `os.replace` 也是原子操作（Python 3.3+）。
        """
        with self._lock:
            dirname = os.path.dirname(self.path) or "."
            os.makedirs(dirname, exist_ok=True)
            data = {
                "entities": self._entities,
                "relations": self._relations,
                "updated_at": datetime.now().isoformat(),
            }
            # 七维-数C3：改用统一原子写原语（PID+UUID 临时名、fsync、replace
            # 退避重试、异常清理临时文件），并连带修掉固定名 .tmp 多进程互踩。
            atomic_write_json(self.path, data)
            logger.debug("知识图谱已保存: %d 实体, %d 关系", len(self._entities), len(self._relations))

    def _rebuild_index(self) -> None:
        """重建邻接表（出边 + 入边）和名称索引。调用方需持锁。"""
        self._adjacency = {}
        self._reverse_adjacency = {}
        self._name_index = {}
        for eid, entity in self._entities.items():
            self._adjacency.setdefault(eid, [])
            self._reverse_adjacency.setdefault(eid, [])
            name = normalize_name(entity.get("name"))
            if name:
                self._name_index[name] = eid
            for alias in entity.get("aliases", []):
                alias_lower = normalize_name(alias)
                if alias_lower:
                    self._name_index[alias_lower] = eid
        for rel in self._relations:
            src = rel.get("source_id", "")
            tgt = rel.get("target_id", "")
            self._adjacency.setdefault(src, []).append(rel)
            self._reverse_adjacency.setdefault(tgt, []).append(rel)
            # 确保两端都有邻接条目
            self._adjacency.setdefault(tgt, [])
            self._reverse_adjacency.setdefault(src, [])

    def _add_relation_to_index(self, rel: dict[str, Any]) -> None:
        """增量添加关系到邻接索引 — O(1) 替代 O(n) 重建。调用方需持锁。"""
        src = rel.get("source_id", "")
        tgt = rel.get("target_id", "")
        self._adjacency.setdefault(src, []).append(rel)
        self._reverse_adjacency.setdefault(tgt, []).append(rel)
        # 确保两端都有邻接条目（即使没有出/入边）
        self._adjacency.setdefault(tgt, [])
        self._reverse_adjacency.setdefault(src, [])

    def _remove_relations_from_index(self, predicate) -> None:
        """从两个邻接索引中删除满足 predicate 的关系。调用方需持锁。"""
        for adj in self._adjacency.values():
            adj[:] = [r for r in adj if not predicate(r)]
        for radj in self._reverse_adjacency.values():
            radj[:] = [r for r in radj if not predicate(r)]

    def _add_entity_to_name_index(self, eid: str, entity: dict[str, Any]) -> None:
        """增量添加实体到名称索引。调用方需持锁。"""
        name = normalize_name(entity.get("name"))
        if name:
            self._name_index[name] = eid
        for alias in entity.get("aliases", []):
            alias_lower = normalize_name(alias)
            if alias_lower:
                self._name_index[alias_lower] = eid

    def _remove_entity_from_name_index(self, eid: str, entity: dict[str, Any]) -> None:
        """从名称索引中删除实体的所有条目。调用方需持锁。"""
        name = normalize_name(entity.get("name"))
        if name and self._name_index.get(name) == eid:
            del self._name_index[name]
        for alias in entity.get("aliases", []):
            alias_lower = normalize_name(alias)
            if alias_lower and self._name_index.get(alias_lower) == eid:
                del self._name_index[alias_lower]

    # ── 事务 ──────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """原子事务：异常时回滚内存状态。

        注意：
        - 持锁期间执行，嵌套调用安全（RLock）
        - 回滚仅恢复 entities/relations 引用 + 重建索引，不撤销已 save 到磁盘的内容
        - 调用方应在事务内部决定是否调用 save()

        用法:
            with store.transaction():
                store.add_entity("e1", "x")
                store.add_relation("e1", "e2", REL_RELATED_TO)
                if not ok:
                    raise RuntimeError("rollback")  # 自动恢复
                store.save()
        """
        with self._lock:
            # 浅拷贝容器引用 — 子操作的 in-place 修改不会影响快照
            snapshot_entities = {k: dict(v) for k, v in self._entities.items()}
            snapshot_relations = [dict(r) for r in self._relations]
            try:
                yield
            except Exception:
                logger.warning("知识图谱事务回滚")
                self._entities = snapshot_entities
                self._relations = snapshot_relations
                self._rebuild_index()
                raise

    # ── 实体操作 ──────────────────────────────────────────────

    def add_entity(
        self,
        entity_id: str,
        name: str,
        entity_type: str = ENTITY_TYPE_UNKNOWN,
        aliases: list[str] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """添加实体节点。已存在时更新信息。"""
        with self._lock:
            self.load()
            now = datetime.now().isoformat()
            if entity_id in self._entities:
                existing = self._entities[entity_id]
                # 名称索引更新前先移除旧条目
                self._remove_entity_from_name_index(entity_id, existing)
                existing["name"] = name
                existing["type"] = entity_type
                existing["updated_at"] = now
                if aliases:
                    existing["aliases"] = list(set(existing.get("aliases", []) + aliases))
                if properties:
                    existing.setdefault("properties", {}).update(properties)
                self._add_entity_to_name_index(entity_id, existing)
                return entity_id

            self._entities[entity_id] = {
                "id": entity_id,
                "name": name,
                "type": entity_type,
                "aliases": aliases or [],
                "properties": properties or {},
                "created_at": now,
                "updated_at": now,
            }
            # P1.4: 增量索引，避免 O(n) 重建
            self._adjacency.setdefault(entity_id, [])
            self._reverse_adjacency.setdefault(entity_id, [])
            self._add_entity_to_name_index(entity_id, self._entities[entity_id])
            logger.debug("图谱实体新增: %s (%s)", name, entity_type)
            return entity_id

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """获取实体。"""
        with self._lock:
            self.load()
            return self._entities.get(entity_id)

    # ── 出处（provenance）层 — W1 拆出（2026-09-16 批3）────────────
    #
    # 「哪条来源提到了这个实体」是实体的元数据，不是实体间的语义关系。
    # 旧管线为此建 `mem_*` 容器实体 + mentioned_in 边，与真关系同表同字段，
    # 实测 86% 的"关系"是出处边、图形状由容器决定。新契约：出处写
    # entity.properties.sources（字符串列表），relations 表只留语义边。

    def add_entity_source(self, entity_id: str, source_id: str, *, kind: str = "") -> bool:
        """给实体追加一条出处（去重）。返回是否有新增。

        不触碰 name/type/aliases——避免旧 ``add_entity`` 复用路径的静默改名风险。

        P4-KG-ORIGIN（2026-09-26）：同时写两处，但**以结构化 `origins` 为准**：
        * ``properties.origins = [{kind, id}]``——可判定、可跳转、可按域级联清理；
        * ``properties.sources = ["kind:id", ...]``——兼容旧读端（含本方法历史上
          写的裸串），kind 已知时顺手补上前缀，不让旧数据退化成裸 id。
        调用方可显式传 ``kind``；不传则从串里解析（无命名空间则 unknown）。
        """
        raw_source = str(source_id or "").strip()
        if not raw_source:
            return False
        parsed_kind, parsed_id = split_origin(raw_source)
        origin_kind = str(kind or "").strip() or parsed_kind
        canonical = format_origin(origin_kind, parsed_id or raw_source)
        if not canonical:
            return False
        with self._lock:
            self.load()
            entity = self._entities.get(entity_id)
            if entity is None:
                return False
            props = entity.setdefault("properties", {})

            origins = props.get("origins")
            origins = list(origins) if isinstance(origins, list) else []
            seen = {(str(o.get("kind") or "unknown"), str(o.get("id") or "")) for o in origins if isinstance(o, dict)}
            pair = (origin_kind, (parsed_id or raw_source))
            if pair in seen:
                return False
            origins.append({"kind": origin_kind, "id": pair[1]})
            props["origins"] = origins[-200:]

            # 镜像到兼容字段（上限同源，防对话批量回灌时无限膨胀）
            sources = props.get("sources")
            sources = [str(s) for s in sources] if isinstance(sources, list) else []
            if canonical not in sources:
                sources.append(canonical)
            props["sources"] = sources[-200:]
            entity["updated_at"] = datetime.now().isoformat()
            return True

    def migrate_provenance_edges(self, entity_ids) -> int:
        """把遗留的 mentioned_in 出处边就地迁出为 properties.sources。

        对每个 entity_id：找它挂的 mentioned_in 边 → 从对端容器实体
        （properties.source_id）取出真实来源 ID 写入 properties.sources →
        删除该边 → 容器若再无其他边触及则连节点一并删除。

        幂等：重复执行结果一致。返回迁移的边数。调用方负责 save()。
        """
        ids = {str(e) for e in entity_ids if e}
        if not ids:
            return 0
        with self._lock:
            self.load()
            migrated = 0
            containers: set[str] = set()
            migrated_edge_obj_ids: set[int] = set()

            def _is_provenance_edge(r: dict[str, Any]) -> bool:
                if r.get("relation_type") != REL_MENTIONED_IN:
                    return False
                src = r.get("source_id", "")
                tgt = r.get("target_id", "")
                if src not in ids and tgt not in ids:
                    return False
                # 出处语义：正常建边是 src=实体、tgt=容器；
                # 但历史数据可能存在反向建边，这里按 ids 命中侧判定双向兼容。
                entity_id, container_id = (src, tgt) if src in ids else (tgt, src)
                container = self._entities.get(container_id) or {}
                real_source = str((container.get("properties") or {}).get("source_id") or container.get("name") or container_id)
                self.add_entity_source(entity_id, real_source)
                containers.add(container_id)
                migrated_edge_obj_ids.add(id(r))
                return True

            kept = []
            for r in self._relations:
                if _is_provenance_edge(r):
                    migrated += 1
                else:
                    kept.append(r)
            if migrated:
                self._relations = kept
                # 按对象身份精确剔除已迁移边——不能用类型条件，
                # 否则会误删不经过 ids 集合的遗留 mentioned_in 边的索引
                self._remove_relations_from_index(lambda r: id(r) in migrated_edge_obj_ids)
            # 容器孤儿节点清理：仅剩"出处用途"的容器整体移除
            for cid in containers:
                still_used = any(r.get("source_id") == cid or r.get("target_id") == cid for r in self._relations)
                if not still_used and cid in self._entities:
                    entity = self._entities.pop(cid)
                    self._remove_entity_from_name_index(cid, entity)
                    self._adjacency.pop(cid, None)
                    self._reverse_adjacency.pop(cid, None)
            if migrated:
                logger.info(
                    "KG provenance migration: %d mentioned_in edges -> properties.sources, %d container nodes removed",
                    migrated,
                    len(containers),
                )
            return migrated

    def find_by_name(self, name: str, *, allow_substring: bool = False) -> list[dict[str, Any]]:
        """按名称查找实体（默认 = 规范化精确匹配）。

        **默认语义是"身份解析"**（这个名字是不是同一个实体）：规范化精确命中
        即返回，未命中返回 ``[]``。子串包含折叠（``量子`` ⊂ ``量子计算``）只在
        调用方**显式**传 ``allow_substring=True``（检索/召回场景）时启用。

        PD-5b（2026-09-18）：旧实现把子串折叠放在默认路径上，于是
        ``_add_entity_if_new("量子")`` 命中已存在的「量子计算」并复用其 id →
        新实体永远建不出来、被静默吞并（实测图谱只剩 ``['量子计算']``）。
        折叠能力本身有价值（用户搜「量子」应看到「量子计算」），故**不删除**，
        而是降级为显式选项，并按调用方语义逐个选择：身份解析走精确，检索召回
        显式开启（graph_extractor.query_for_prompt_context / memory.search /
        knowledge.search / find_path）。
        """
        with self._lock:
            self.load()
            name_lower = normalize_name(name)
            if not name_lower:
                return []

            # 精确匹配（规范化：全角/大小写/空白异体统一口径）
            if name_lower in self._name_index:
                eid = self._name_index[name_lower]
                entity = self._entities.get(eid)
                if entity:
                    return [entity]

            if not allow_substring:
                # 未显式要求折叠 → 不猜。宁可空手，也不要把别的实体当成它。
                return []

            # 模糊匹配（显式选项）：保留既有折叠能力，供检索类调用方使用
            results = []
            for eid, entity in self._entities.items():
                ename = normalize_name(entity.get("name"))
                if name_lower in ename or ename in name_lower:
                    results.append(entity)
                    continue
                for alias in entity.get("aliases", []):
                    alias_lower = normalize_name(alias)
                    if name_lower in alias_lower or alias_lower in name_lower:
                        results.append(entity)
                        break

            return results[:10]

    def find_by_type(self, entity_type: str) -> list[dict[str, Any]]:
        """按类型查找实体。"""
        with self._lock:
            self.load()
            return [e for e in self._entities.values() if e.get("type") == entity_type]

    def get_all_entities(self) -> list[dict[str, Any]]:
        """获取所有实体。"""
        with self._lock:
            self.load()
            return list(self._entities.values())

    def remove_entity(self, entity_id: str) -> bool:
        """删除实体及其所有关系（出边 + 入边都清理）。"""
        with self._lock:
            self.load()
            if entity_id not in self._entities:
                return False
            entity = self._entities.pop(entity_id)
            self._remove_entity_from_name_index(entity_id, entity)
            # 删除所有触及此实体的关系
            self._relations = [r for r in self._relations if r.get("source_id") != entity_id and r.get("target_id") != entity_id]
            # P1.4: 增量索引清理 — 从其他节点的邻接表中剔除
            self._adjacency.pop(entity_id, None)
            self._reverse_adjacency.pop(entity_id, None)
            self._remove_relations_from_index(lambda r: r.get("source_id") == entity_id or r.get("target_id") == entity_id)
            logger.debug("图谱实体删除: %s", entity_id)
            return True

    def remove_by_source(self, source_type: str, source_id: str | int) -> int:
        """P1.5: 按 source_type + source_id 级联删除图谱实体。

        P4-KG-ORIGIN（2026-09-26）三态匹配。旧实现只读 ``properties.source_id``，
        而批3 W1 之后**没有任何写入端再写这个键**（只剩 `_ensure_knowledge_entity`
        一族）——实测生产图谱 739 实体里 `source_id` 为 0，于是笔记/任务/收件箱/
        资讯四域的级联清理全部退化为永久 no-op → 幽灵实体累积。现在按三代形状都认：

        1. ``properties.origins`` —— 结构化 [{kind,id}]，现行正典；
        2. ``properties.sources`` —— 字符串列表，匹配 ``"{type}:{id}"``；
        3. ``properties.source_id`` —— 旧代（含裸 id），保留兼容。

        另：若实体完全没有任何带命名空间的出处，才允许用**裸 id** 兼容匹配
        （防止把 memory 的裸 entry_id 误当成 note id 删掉）。

        Args:
            source_type: "note" / "task" / "inbox" / "info" / "memory" / "md" /
                "reading_article" / "deepseek" / "kimi" …
            source_id: 来源 ID

        Returns:
            实际删除的实体数
        """
        sid = str(source_id).strip()
        stype = str(source_type or "").strip()
        if not stype or not sid:
            return 0
        canonical = f"{stype}:{sid}"
        with self._lock:
            self.load()
            to_remove: list[str] = []
            for eid, entity in self._entities.items():
                props = entity.get("properties") or {}

                origins = props.get("origins")
                if isinstance(origins, list) and any(
                    isinstance(o, dict) and str(o.get("kind") or "") == stype and str(o.get("id") or "") == sid for o in origins
                ):
                    to_remove.append(eid)
                    continue

                raw_sources = props.get("sources")
                source_list = [str(s) for s in raw_sources] if isinstance(raw_sources, list) else []
                if canonical in source_list or canonical in {str(props.get("source_id") or "")}:
                    to_remove.append(eid)
                    continue

                # 兜底兼容（旧代数据）：裸 source_id；或该实体所有出处都未定名
                # （全是裸串）时，才允许按裸 id 命中
                legacy_source_id = str(props.get("source_id") or "").strip()
                if legacy_source_id and legacy_source_id == sid:
                    to_remove.append(eid)
                    continue
                if source_list and sid in source_list and all(split_origin(s)[0] == "unknown" for s in source_list):
                    to_remove.append(eid)
            if not to_remove:
                return 0
            removed = 0
            for eid in to_remove:
                # remove_entity 内部会再次 acquire RLock（可重入），但为了
                # 避免重复 lock/unlock 开销，这里直接执行清理逻辑
                entity = self._entities.pop(eid, None)
                if entity is None:
                    continue
                self._remove_entity_from_name_index(eid, entity)
                self._relations = [r for r in self._relations if r.get("source_id") != eid and r.get("target_id") != eid]
                self._adjacency.pop(eid, None)
                self._reverse_adjacency.pop(eid, None)
                self._remove_relations_from_index(lambda r: r.get("source_id") == eid or r.get("target_id") == eid)
                removed += 1
            logger.info(
                "KG cascade-removed %d entities for %s:%s",
                removed,
                stype,
                sid,
            )
            return removed

    # ── 实体合并（P3-KG-MERGE，2026-09-21）────────────────────
    #
    # 图谱里同一事物常以多个节点共存（正则/LLM 两代提取、单复数、异体字），
    # 而 `find_by_name` 默认是**规范化精确匹配**（PD-5b），不会自动合并它们；
    # 此前全仓也无任何实体归并入口（只有 remove_entity 一删了之，会连带丢掉对端关系）。
    # 合并语义：source 消失、它的边/别名/出处全部转到 target。

    def merge_entities(self, source_entity_id: str, target_entity_id: str, *, keep_alias: bool = True) -> dict[str, Any]:
        """把 ``source`` 实体并入 ``target``（就地、不可逆）。返回合并明细。

        规则（与出处层同一纪律）：
        * ``target`` 的 name/type **不变**——不因合并而静默改名（同 ``add_entity_source``）；
        * ``source`` 的主名与 aliases 并入 ``target.aliases``（规范化去重，可选）；
        * ``source.properties.sources`` 并入 ``target``（去重，保留尾部 200 条上限）；
          其余 properties 仅在 ``target`` 缺同键时补，不覆盖已有值；
        * 所有触及 ``source`` 的边改指 ``target``；变成自环的边直接丢弃；
        * 边按 ``(source_id, target_id, relation_type)`` 去重，confidence 取较大值；
        * 最后删除 ``source`` 节点并重建索引（合并是低频操作，整体重建换正确性）。

        调用方负责 ``save()``。失败（任一实体不存在 / 两者同一）时**不改任何状态**。
        """
        src = str(source_entity_id or "").strip()
        dst = str(target_entity_id or "").strip()
        if not src or not dst:
            return {"merged": False, "error": "source_entity_id and target_entity_id required"}
        with self._lock:
            self.load()
            if src == dst:
                return {"merged": False, "error": "source and target are the same entity"}
            source_entity = self._entities.get(src)
            target_entity = self._entities.get(dst)
            if source_entity is None or target_entity is None:
                missing = src if source_entity is None else dst
                return {"merged": False, "error": f"entity not found: {missing}"}

            # 1) 别名与出处先算好，再一次性写盘（中途异常不留下半状态）
            target_key = normalize_name(target_entity.get("name"))
            aliases = list(target_entity.get("aliases") or [])
            moved_aliases = 0
            if keep_alias:
                candidates = [str(source_entity.get("name") or "").strip()] + [str(a).strip() for a in (source_entity.get("aliases") or [])]
                seen = {normalize_name(a) for a in aliases if str(a).strip()}
                if target_key:
                    seen.add(target_key)
                for cand in candidates:
                    key = normalize_name(cand)
                    if not cand or not key or key in seen:
                        continue
                    aliases.append(cand)
                    seen.add(key)
                    moved_aliases += 1

            src_props = dict(source_entity.get("properties") or {})
            tgt_props = dict(target_entity.get("properties") or {})
            base_sources = tgt_props.get("sources")
            base_sources = list(base_sources) if isinstance(base_sources, list) else []
            incoming_sources = src_props.get("sources")
            incoming_sources = list(incoming_sources) if isinstance(incoming_sources, list) else []
            merged_sources: list[Any] = []
            seen_src: set[str] = set()
            for item in base_sources + incoming_sources:
                key = str(item)
                if key in seen_src:
                    continue
                seen_src.add(key)
                merged_sources.append(item)
            moved_sources = len(merged_sources) - len(base_sources)
            for key, value in src_props.items():
                if key == "sources":
                    continue
                tgt_props.setdefault(key, value)
            if merged_sources:
                # 与 add_entity_source 同一上限口径（保留最近 200 条）
                tgt_props["sources"] = merged_sources[-200:]

            # 2) 转边 + 去自环 + 去重
            relations_moved = 0
            self_loops_dropped = 0
            duplicates_merged = 0
            rebuilt: list[dict[str, Any]] = []
            by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
            for rel in self._relations:
                rs = rel.get("source_id", "")
                rt = rel.get("target_id", "")
                new_rs = dst if rs == src else rs
                new_rt = dst if rt == src else rt
                if new_rs == new_rt:
                    self_loops_dropped += 1
                    continue  # 自环丢弃
                moved = new_rs != rs or new_rt != rt
                # 叫 edge_key 不叫 key：本函数前面的别名/properties 循环已把 `key`
                # 绑成 str，同名复用会被类型检查判为赋值冲突（且可读性更差）。
                edge_key: tuple[str, str, str] = (new_rs, new_rt, str(rel.get("relation_type", "")))
                if moved:
                    # G4-OK: 关系边端点重指（图谱实体 id 空间，非域出处口径）
                    rel = {**rel, "source_id": new_rs, "target_id": new_rt}
                existing = by_key.get(edge_key)
                if existing is None:
                    by_key[edge_key] = rel
                    rebuilt.append(rel)
                    relations_moved += 1 if moved else 0
                    continue
                # 重复边：confidence 取大、时间取新，不新增条目
                duplicates_merged += 1
                existing["confidence"] = max(existing.get("confidence", 0), rel.get("confidence", 0))
                existing_ts = str(existing.get("updated_at") or "")
                rel_ts = str(rel.get("updated_at") or "")
                if rel_ts > existing_ts:
                    existing["updated_at"] = rel["updated_at"]
                if rel.get("properties"):
                    merged_props = dict(existing.get("properties") or {})
                    merged_props.update(rel["properties"])
                    existing["properties"] = merged_props

            # 3) 提交：target 字段 → 删 source → 关系表 → 索引重建
            now = datetime.now().isoformat()
            target_entity["aliases"] = aliases
            target_entity["properties"] = tgt_props
            target_entity["updated_at"] = now
            self._relations = rebuilt
            self._entities.pop(src, None)
            self._adjacency.pop(src, None)
            self._reverse_adjacency.pop(src, None)
            self._rebuild_index()
            logger.info(
                "KG merge %s -> %s: %d 别名 / %d 出处 / %d 边转指 / %d 自环丢弃 / %d 重复边归一",
                src,
                dst,
                moved_aliases,
                moved_sources,
                relations_moved,
                self_loops_dropped,
                duplicates_merged,
            )
            return {
                "merged": True,
                # G4-OK: merge 报告字段（图谱实体 id 空间，非域出处口径）
                "source_id": src,
                "target_id": dst,
                "aliases_merged": moved_aliases,
                "sources_merged": moved_sources,
                "relations_repointed": relations_moved,
                "self_loops_dropped": self_loops_dropped,
                "duplicates_merged": duplicates_merged,
                "relation_count": len(rebuilt),
            }

    # ── 关系操作 ──────────────────────────────────────────────

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str = REL_RELATED_TO,
        properties: dict[str, Any] | None = None,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """添加关系边（自动去重）。"""
        with self._lock:
            self.load()
            if source_id not in self._entities:
                logger.warning("关系起点实体不存在: %s", source_id)
                return {}
            if target_id not in self._entities:
                logger.warning("关系终点实体不存在: %s", target_id)
                return {}

            # 去重：同类型同方向不重复添加
            for existing in self._relations:
                if (
                    existing.get("source_id") == source_id
                    and existing.get("target_id") == target_id
                    and existing.get("relation_type") == relation_type
                ):
                    existing["confidence"] = max(existing.get("confidence", 0), confidence)
                    existing["updated_at"] = datetime.now().isoformat()
                    if properties:
                        existing.setdefault("properties", {}).update(properties)
                    return existing

            relation = {
                # G4-OK: 关系边端点构造（图谱实体 id 空间，非域出处口径）
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "properties": properties or {},
                "confidence": confidence,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            self._relations.append(relation)
            # P1.4: 增量索引，避免 O(n) 重建
            self._add_relation_to_index(relation)
            return relation

    def get_relations(self, entity_id: str, *, direction: str = "both") -> list[dict[str, Any]]:
        """获取实体的关系。

        P1.4：使用反向邻接索引，O(1) 查询入边（替代 O(N) 全表扫描）。

        Args:
            entity_id: 实体 ID
            direction: "out" 仅出边 / "in" 仅入边 / "both" 全部（默认）

        Returns:
            去重后的关系列表
        """
        with self._lock:
            self.load()
            out = self._adjacency.get(entity_id, []) if direction in ("out", "both") else []
            incoming = self._reverse_adjacency.get(entity_id, []) if direction in ("in", "both") else []
            # 排除自环（source == target == entity_id）出现在入边列表中
            incoming = [r for r in incoming if r.get("source_id") != entity_id]
            seen = set()
            result = []
            for r in out + incoming:
                key = (r.get("source_id", ""), r.get("target_id", ""), r.get("relation_type", ""))
                if key not in seen:
                    seen.add(key)
                    result.append(r)
            return result

    def get_all_relations(self) -> list[dict[str, Any]]:
        """获取所有关系（线程安全）。"""
        with self._lock:
            self.load()
            return list(self._relations)

    # ── 图遍历 ──────────────────────────────────────────────

    def traverse(
        self,
        start_id: str,
        max_hops: int = 2,
        relation_types: list[str] | None = None,
        *,
        direction: str = "both",
    ) -> list[list[dict[str, Any]]]:
        """从起点进行 BFS 多跳遍历。

        P1.4：使用反向邻接索引支持真正的反向遍历。

        Args:
            start_id: 起点实体 ID
            max_hops: 最大遍历深度
            relation_types: 可选，只遍历指定类型的关系
            direction: "out"（仅出边）/ "in"（仅入边）/ "both"（默认，双向）

        Returns:
            路径列表，每条路径包含 [entity, relation, entity, ...]
        """
        with self._lock:
            self.load()
            if start_id not in self._entities:
                return []

            visited = {start_id}
            queue: list[tuple[str, list[dict[str, Any]]]] = [(start_id, [self._entities[start_id]])]
            results: list[list[dict[str, Any]]] = []

            for _ in range(max_hops):
                level_results = []
                next_queue: list[tuple[str, list[dict[str, Any]]]] = []
                for current_id, path in queue:
                    # P1.4: 根据 direction 取邻居
                    neighbors = self._get_neighbors(current_id, direction)
                    for rel in neighbors:
                        if relation_types and rel.get("relation_type") not in relation_types:
                            continue
                        neighbor_id = self._other_endpoint(rel, current_id)
                        if neighbor_id is None or neighbor_id in visited or neighbor_id not in self._entities:
                            continue
                        visited.add(neighbor_id)
                        neighbor_entity = self._entities[neighbor_id]
                        new_path = path + [rel, neighbor_entity]
                        level_results.append(new_path)
                        next_queue.append((neighbor_id, new_path))

                if not level_results:
                    break
                results.extend(level_results)
                queue = next_queue

            return results

    def _get_neighbors(self, entity_id: str, direction: str) -> list[dict[str, Any]]:
        """根据方向返回邻居关系列表。调用方需持锁。"""
        if direction == "out":
            return self._adjacency.get(entity_id, [])
        if direction == "in":
            return self._reverse_adjacency.get(entity_id, [])
        # both
        return self._adjacency.get(entity_id, []) + self._reverse_adjacency.get(entity_id, [])

    @staticmethod
    def _other_endpoint(rel: dict[str, Any], current_id: str) -> str | None:
        """从关系中取出 current_id 的对端实体 ID。"""
        src = rel.get("source_id", "")
        tgt = rel.get("target_id", "")
        if src == current_id:
            return tgt if tgt != current_id else None
        if tgt == current_id:
            return src if src != current_id else None
        return None

    def find_path(self, start_name: str, end_name: str, *, direction: str = "both") -> list[list[dict[str, Any]]]:
        """查找两个实体之间的路径（BFS 最短路径）。

        P1.4：支持 direction 参数。
        """
        with self._lock:
            self.load()
            start_results = self.find_by_name(start_name, allow_substring=True)
            end_results = self.find_by_name(end_name, allow_substring=True)
            if not start_results or not end_results:
                return []
            start_id = start_results[0].get("id", "")
            end_id = end_results[0].get("id", "")

            if start_id == end_id:
                return [[self._entities[start_id]]]

            from collections import deque

            visited = {start_id}
            parent_map: dict[str, tuple[str, dict[str, Any] | None, int]] = {start_id: ("", None, 0)}
            queue: deque[str] = deque([start_id])
            found = False

            while queue and not found:
                current = queue.popleft()
                depth = parent_map[current][2]
                if depth >= 5:
                    break
                for rel in self._get_neighbors(current, direction):
                    neighbor = self._other_endpoint(rel, current)
                    if neighbor is None or neighbor in visited or neighbor not in self._entities:
                        continue
                    visited.add(neighbor)
                    parent_map[neighbor] = (current, rel, depth + 1)
                    queue.append(neighbor)
                    if neighbor == end_id:
                        found = True
                        break

            if not found or end_id not in parent_map:
                return []

            path: list[dict[str, Any]] = []
            current = end_id
            while current != start_id:
                entity = self._entities.get(current)
                if entity:
                    path.append(entity)
                parent, rel, _ = parent_map.get(current, ("", None, 0))
                if rel:
                    path.append(rel)
                current = parent
            path.append(self._entities[start_id])
            path.reverse()
            return [path]

    # ── 搜索（P1.4 新增 — 让 KG 参与 UnifiedKnowledgeService） ─────────────

    def search(self, query: str, *, limit: int = 15) -> list[dict[str, Any]]:
        """按名称/别名/类型搜索实体 — 供 KnowledgeGraphAdapter 调用。

        简单的子串匹配 + 类型匹配，无语义搜索。
        复杂查询请用 find_by_name / find_by_type / traverse 组合。
        """
        with self._lock:
            self.load()
            query_lower = (query or "").strip().lower()
            if not query_lower:
                return []

            scored: list[tuple[int, dict[str, Any]]] = []
            for entity in self._entities.values():
                name = (entity.get("name") or "").lower()
                etype = (entity.get("type") or "").lower()
                aliases = [a.lower() for a in entity.get("aliases", [])]

                # 评分：名称精确匹配 > 名称子串 > 别名子串 > 类型子串
                score = 0
                if name == query_lower:
                    score = 100
                elif query_lower in name:
                    score = 80
                elif any(query_lower == a for a in aliases):
                    score = 70
                elif any(query_lower in a for a in aliases):
                    score = 50
                elif query_lower in etype:
                    score = 30

                if score > 0:
                    scored.append((score, entity))

            scored.sort(key=lambda x: -x[0])
            return [e for _, e in scored[:limit]]

    # ── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取图谱统计信息。"""
        with self._lock:
            self.load()
            type_counts: dict[str, int] = {}
            for e in self._entities.values():
                t = e.get("type", ENTITY_TYPE_UNKNOWN)
                type_counts[t] = type_counts.get(t, 0) + 1
            relation_type_counts: dict[str, int] = {}
            for r in self._relations:
                t = r.get("relation_type", REL_RELATED_TO)
                relation_type_counts[t] = relation_type_counts.get(t, 0) + 1
            return {
                "entity_count": len(self._entities),
                "relation_count": len(self._relations),
                "entity_types": type_counts,
                "relation_types": relation_type_counts,
            }

    def clear(self) -> None:
        """清空图谱。"""
        with self._lock:
            self._entities = {}
            self._relations = []
            self._adjacency = {}
            self._reverse_adjacency = {}
            self._name_index = {}
            if os.path.exists(self.path):
                try:
                    os.remove(self.path)
                except OSError as exc:
                    logger.warning("清空图谱时删除文件失败: %s", exc)
            self._loaded = True
            logger.info("知识图谱已清空")
