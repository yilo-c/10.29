# 知识图谱功能完善方案

> 日期：2026-06-11  
> 背景：后端图谱能力完整（5 RPC + 自动提取），但前端体验不完善，标签全英文，数据以 sample 为主

---

## 一、当前状态

### 1.1 后端（已完成）

| 能力 | 位置 | 状态 |
|------|------|:---:|
| 实体 CRUD | `knowledge/graph_store.py` | ✅ |
| 关系管理 | `knowledge/graph_store.py` | ✅ |
| 中文 NLP 实体提取 | `knowledge/graph_extractor.py` | ✅ |
| BFS 多跳遍历 | `knowledge/graph_store.py:traverse()` | ✅ |
| JSON 持久化 | `knowledge/graph_store.py:save/load` | ✅ |
| 5 个搜索/查询 RPC | `_inbox_data.py` (knowledge.search/graph/relations/traverse) | ✅ |
| 统一查询 RPC | `handlers/__init__.py` (knowledge.unified_query) | ✅ |
| RAG 检索管道 | `knowledge/retrieval/` (8 模块) | ✅ |

### 1.2 前端（有但需完善）

| 能力 | 位置 | 状态 |
|------|------|:---:|
| ForceGraph 力导向图 | `desk-notes/.../Graph/ForceGraph.tsx` | ✅ |
| 图谱服务层 + sample 数据 | `desk-notes/.../Graph/graphService.ts` | ⚠️ 含 sample 兜底 |
| 图表面板 UI | `desk-notes/.../Modules/KnowledgeGraphModule.tsx` | ✅ |
| 手机端知识视图 | `思忆集手机端app/` | ❌ 完全缺失 |

### 1.3 标签语言状态

```
后端 entity_type:    "person" "project" "concept" "location" "event"      ← 英文
后端 relation_type:  "related_to" "part_of" "mentioned_in" "before"       ← 英文
前端 NODE_TYPES:     ['project','concept','person','location','event']    ← 英文
前端 type color:     { project: '#4fc3f7', concept: '#81c784' }          ← 英文 key
前端 GraphNode.type: "project"                                            ← 英文
```

**存储中所有数据都使用英文 type 值，直接改常量值会破坏已有数据。**

---

## 二、竞品对标

### 2.1 Obsidian Graph View

| 特性 | Obsidian | 思忆集 |
|------|---------|-------|
| 节点来源 | 本地 .md 文件 [[wiki-link]] | AI 自动提取 + 手动创建 |
| 边来源 | 用户手动加 [[链接]] | AI 自动关联 + 手动创建 |
| 节点发现 | 用户需要主动写链接 | **AI 自动从内容中提取** ✨ |
| 节点过滤 | 按文件夹/tag/搜索 | 按 entity_type（后端支持，UI 缺） |
| 关系深度 | 无限制（全量） | 2-hop BFS（`knowledge.traverse`） |
| 右键菜单 | 打开文件/新标签页 | 展开节点/查看关系 |
| 中文体验 | 文件名即标签 | 标签是英文 type，不直观 |
| 本地图 | 全量本地笔记 | **跨笔记/对话/待办的 AI 知识网络** ✨ |

### 2.2 思忆集的核心差异化

```
Obsidian:  笔记 → 用户手动 [[链接]] → 图谱  (被动)
思忆集:   笔记+对话+待办 → AI 自动提取实体+关系 → 图谱  (主动)
```

你不需要手动写 `[[链接]]`，AI 从你的笔记和对话中自动发现"你提到了 React 18"、"你在做思忆集项目"、"你关注 AI 自动化"——然后自动建图谱。

**这是 Obsidian 做不到的，也是思忆集知识图谱的核心价值。**

### 2.3 缺失的关键体验

| # | 问题 | 竞品对标 | 优先级 |
|---|------|---------|:---:|
| 1 | 节点标签是英文 type，用户看不懂 | 所有竞品都是中文 | 🔴 |
| 2 | 边标签是英文 "related_to"，无意义 | Obsidian 无标签（纯连线） | 🔴 |
| 3 | 默认显示 sample 数据而非用户真实数据 | 无竞品用假数据 | 🔴 |
| 4 | 无类型过滤（想看"只有项目"做不到） | Obsidian 按文件夹过滤 | 🟡 |
| 5 | 无时间线展示（哪个实体最新） | Roam Research 有时间轴 | 🟡 |
| 6 | 无实体详情面板（点击节点看关联笔记） | Obsidian 右键打开文件 | 🟡 |
| 7 | 手机端完全无知识视图 | Obsidian mobile 有 | 🟡 |
| 8 | 无自动发现提示（"发现新关系，要加入图谱吗"） | 无竞品有 | 🟢 |

---

## 三、中文标签方案

### 3.1 原则

**不改变存储的英文值** — 已有数据保持兼容。只在展示层做映射。

### 3.2 后端改造 (`knowledge/graph_store.py`)

新增类型显示名映射（不改常量值）：

```python
ENTITY_TYPE_DISPLAY: dict[str, str] = {
    ENTITY_TYPE_PERSON:       "人物",
    ENTITY_TYPE_LOCATION:     "地点",
    ENTITY_TYPE_EVENT:        "事件",
    ENTITY_TYPE_PROJECT:      "项目",
    ENTITY_TYPE_DATE:         "日期",
    ENTITY_TYPE_ORGANIZATION: "组织",
    ENTITY_TYPE_CONCEPT:      "概念",
    ENTITY_TYPE_TAG:          "标签",
    ENTITY_TYPE_UNKNOWN:      "其他",
}

RELATION_DISPLAY: dict[str, str] = {
    REL_MENTIONED_IN:     "提及于",
    REL_RELATED_TO:       "关联",
    REL_PART_OF:          "属于",
    REL_LOCATED_AT:       "位于",
    REL_PARTICIPATED_IN:  "参与",
    REL_BEFORE:           "之前",
    REL_AFTER:            "之后",
    REL_SAME_AS:          "等同于",
}
```

在 RPC 返回中附加 `type_display` 字段（`knowledge.graph` / `knowledge.search` / `knowledge.relations`）：

```python
# 当前返回:
{"entities": [{"id": "p-1", "name": "思忆集", "type": "project"}]}

# 改为:
{"entities": [{"id": "p-1", "name": "思忆集", "type": "project", "type_display": "项目"}]}
```

### 3.3 内存实体类型 (`memory/_schema_constants.py`)

同样不改常量值，加映射：

```python
ENTITY_TYPE_DISPLAY: dict[str, str] = {
    ENTITY_TYPE_PERSON:   "人物",
    ENTITY_TYPE_TASK:     "任务",
    ENTITY_TYPE_PROJECT:  "项目",
    ENTITY_TYPE_EVENT:    "事件",
    ENTITY_TYPE_NOTE:     "笔记",
    ENTITY_TYPE_CONTACT:  "联系人",
    ENTITY_TYPE_CUSTOM:   "自定义",
}

RELATION_DISPLAY: dict[str, str] = {
    RELATION_RELATES_TO:   "关联",
    RELATION_DEPENDS_ON:   "依赖",
    RELATION_REFERENCES:   "引用",
    RELATION_PARENT_OF:    "父节点",
    RELATION_CHILD_OF:     "子节点",
    RELATION_PART_OF:      "属于",
    RELATION_SIBLING_OF:   "同级",
    RELATION_FOLLOWS:      "后续",
}
```

### 3.4 前端改造

`GraphNode` 和 `GraphEdge` 类型增加 `typeLabel` 字段:

```typescript
export interface GraphNode {
  id: string
  label: string
  type: string          // 保持不变 (如 "person")
  type_display?: string // 新增 (如 "人物")
  group?: number
}

export interface GraphEdge {
  source: string
  target: string
  label?: string
  type?: string          // 保持不变
  type_display?: string  // 新增
}
```

`graphService.ts` 中的颜色配置改用 display name:

```typescript
const NODE_COLORS: Record<string, string> = {
  '人物': '#ff8a65',
  '项目': '#4fc3f7',
  '概念': '#81c784',
  '地点': '#ce93d8',
  '事件': '#ffd54f',
  '组织': '#4db6ac',
  '日期': '#90a4ae',
  '标签': '#a1887f',
}
```

`KnowledgeGraphModule.tsx` 中的 `NODE_TYPES` 从英文改为中文:

```typescript
const NODE_TYPES = ['项目', '概念', '人物', '地点', '事件', '组织', '标签']
```

---

## 四、功能完善路线

### Phase 1：中文标签 + 去 sample 化（2-3h）🔴

| 任务 | 文件 | 说明 |
|------|------|------|
| 1.1 后端加 type_display 映射 | `knowledge/graph_store.py` | ENTITY_TYPE_DISPLAY + RELATION_DISPLAY |
| 1.2 后端加 memory 映射 | `memory/_schema_constants.py` | 同上 |
| 1.3 RPC 返回加 type_display | `_inbox_data.py` (knowledge.* handlers) | 每个实体的 type_display 字段 |
| 1.4 前端类型加字段 | `shared/src/types/index.ts` | GraphNode/GraphEdge 加 type_display |
| 1.5 前端 color config 改中文 | `KnowledgeGraphModule.tsx` | NODE_COLORS + NODE_TYPES |
| 1.6 前端 graphService 去 sample | `graphService.ts` | 无后端数据时显示空状态而非 sample |

### Phase 2：类型过滤 + 详情面板（2-3h）🟡

| 任务 | 文件 | 说明 |
|------|------|------|
| 2.1 图谱顶部加 type filter chip | `KnowledgeGraphModule.tsx` | 点击"项目"只看项目节点 |
| 2.2 点击节点显示详情侧栏 | 新建 `NodeDetailPanel.tsx` | 显示实体名、类型、关联笔记列表、相关实体 |
| 2.3 "关联笔记"可点击打开 | `NodeDetailPanel.tsx` | 点击笔记标题 → 打开笔记编辑器 |

### Phase 3：AI 自动发现 + 手机端（3-4h）🟡

| 任务 | 文件 | 说明 |
|------|------|------|
| 3.1 笔记保存时自动提取实体 | `_notes.py` + `graph_extractor.py` | 保存笔记时后台触发实体提取 |
| 3.2 "发现新关系"通知 | EventBus → WebSocket → 前端 toast | |
| 3.3 手机端轻量知识视图 | 手机端新页面 | 实体搜索 + 关系列表 + 关联笔记（不做力导向图） |

### Phase 4：时间线 + 路径（后续）🟢

| 任务 | 说明 |
|------|------|
| 4.1 时间线视图 | 按实体创建时间排序，显示知识积累过程 |
| 4.2 关系路径 | A→B→C 的路径可视化（"思忆集和 ollama 是怎么关联的"） |
| 4.3 图谱导出 | 导出为 SVG/PNG |

---

## 五、实施建议

### 立即做 (Phase 1)

```
改动量: ~150 行 (后端 50 + 前端 100)
时间: 2-3h
效果: 用户看到中文标签、不再看到假数据
```

### 竞品关键差异

| 竞品 | 节点来源 | 边来源 | 思忆集的优势 |
|------|---------|-------|-----------|
| Obsidian | 手动 [[link]] | 手动 | AI 自动提取 |
| Roam Research | 手动 #tag [[]] | 手动 | AI 自动 + block ref |
| Logseq | 手动 [[]] | 手动 | AI 自动 |
| Notion | 无原生图谱 | - | 有图 |
| **思忆集** | **AI 自动** | **AI 自动** | **唯一 AI 驱动的知识图谱** |

思忆集的知识图谱不是"笔记间的链接图"（Obsidian那种），而是"你知识体系的 AI 自动建模"。这是真正的差异化。

---

## 六、实施顺序

1. **Phase 1** — 中文标签 + 去 sample（今天能做，2-3h）
2. **Phase 2** — 过滤 + 详情（明天，2-3h）
3. **Phase 3** — AI 自动发现 + 手机端（本周，3-4h）
