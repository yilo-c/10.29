# 个人记忆系统设计

> 本文档定义思忆集「个人记忆层」的类型模型、提取策略、衰减/强化机制和检索优先级。
> 记忆层是思忆集区别于通用 AI 助手的核心差异化能力——它让助手"记住用户是谁、做过什么、在乎什么"。

## 参考对标

| 产品 | 借鉴点 |
|------|--------|
| **ChatGPT Memory** | AI 自动提取关键信息、跨会话持久化、用户可管理记忆条目 |
| **Mem.ai** | AI 自动组织、无文件夹设计、自然语言检索 |
| **Reflect.app** | 间隔重复记忆强化、每日摘要回顾 |
| **Dot/New Computer** | 从对话中自动提取结构化记忆、时间线回顾 |

---

## 1. 记忆类型模型

三种记忆类型，各自独立存储但统一检索：

### 1.1 语义记忆（Semantic Memory）

用户的事实性信息，长期稳定：

| 子类型 | 示例 | 提取方式 | 更新频率 |
|--------|------|---------|---------|
| 身份信息 | 姓名、职业、所在地 | 用户显式告知 + AI 确认 | 极少 |
| 关系信息 | 家人、同事、重要联系人 | 对话中提取 + 用户确认 | 低频 |
| 知识偏好 | 技术栈偏好、学习方向 | 观察长期行为模式 | 中频 |
| 事实声明 | "我的 NAS 地址是…""我在学 Rust" | AI 主动提取 | 中频 |

### 1.2 情景记忆（Episodic Memory）

用户经历过的事件，带时间上下文：

| 子类型 | 示例 | 保留策略 |
|--------|------|---------|
| 对话摘要 | "上次讨论了数据库迁移方案" | 最近 30 天高权重，之后衰减 |
| 任务历史 | "周三完成了 Docker 部署验收" | 归档后保留摘要 |
| 决策记录 | "选择了 PostgreSQL 替代 SQLite" | 长期保留，作为偏好推理依据 |
| 阅读/浏览 | "上周收藏了 3 篇关于 CRDT 的文章" | 关联到知识库条目 |

### 1.3 程序记忆（Procedural Memory）

用户的行为模式和偏好：

| 子类型 | 示例 | 更新方式 |
|--------|------|---------|
| 工作习惯 | 上午 9 点查新闻，下午 4 点整理笔记 | 时间序列分析 |
| 交互偏好 | 偏好简洁回复、不喜欢语音输出 | 显式设置 + 行为推断 |
| 工具偏好 | 翻译用有道、笔记用 Markdown | 使用频率统计 |
| 通知偏好 | 新闻提醒开、任务提醒开、翻译提醒关 | 设置面板管理 |

---

## 2. 记忆提取策略

### 2.1 自动提取（AI 主动）

触发条件：
- 对话中用户明确声明事实性信息（"我最近在…""我的…是…"）
- 用户完成一个完整任务闭环（创建 → 整理 → 完成）
- 用户连续 3 次以上表现出相同行为模式
- 用户对某条信息的查询频率显著高于基线

提取流程：
```
对话/行为 → MemoryExtractor(AI) → 候选记忆 → 置信度评分
                                              ├─ >0.8: 自动写入，通知用户
                                              ├─ 0.5-0.8: 追加到"待确认记忆"，下次交互时自然确认
                                              └─ <0.5: 丢弃
```

### 2.2 用户显式记录

入口：
- 悬浮助手"记住这个"快捷入口
- 对话框中 `/remember` 命令
- 笔记/任务创建时勾选"加入记忆"

### 2.3 定期摘要生成

- 每周自动生成"本周关键记忆"摘要，用户确认后入库
- 每月生成"本月习惯回顾"，作为偏好更新的输入

---

## 3. 记忆衰减与强化

### 3.1 衰减曲线

```
初始权重: 1.0
7 天未访问: ×0.85
30 天未访问: ×0.60
90 天未访问: ×0.30
180 天未访问: 归档（不删除，但默认不参与检索）
```

### 3.2 强化信号

| 信号 | 权重加成 |
|------|---------|
| 用户主动查询该记忆 | +0.15 |
| 该记忆被用于回答问题 | +0.10 |
| 用户显式确认/修正该记忆 | +0.20（重置为 1.0） |
| 相关主题的对话再次发生 | +0.05 |

### 3.3 冲突解决

当新信息与已有记忆冲突时：
1. 标记冲突，不静默覆盖
2. 下次交互时自然确认（"你之前提到 X，现在变成 Y 了吗？"）
3. 保留旧记忆为历史版本

---

## 4. 记忆检索优先级

多级检索链路，逐级降级：

```
用户查询
  ├─ 1. 本地记忆层（SQLite + ChromaDB 向量索引）
  │     ├─ 精确匹配：关键词/时间/类型过滤
  │     ├─ 语义匹配：向量相似度 > 0.75
  │     └─ 关联遍历：通过 cos 关系找出间接关联记忆
  │
  ├─ 2. 本地知识库（用户文档、笔记、收藏）
  │     ├─ 全文检索
  │     └─ 向量检索
  │
  ├─ 3. 用户笔记/任务（结构化查询）
  │     └─ SQL 查询 + 关键词
  │
  └─ 4. 联网搜索（外部补充）
        └─ 最多 3 轮，结果缓存 24h
```

每条检索结果附带来源标签：`[记忆]` `[知识库]` `[笔记]` `[网页]`，让用户知道信息出处。

---

## 5. 数据模型

```python
class MemoryEntry:
    id: str                    # UUID
    type: MemoryType           # semantic | episodic | procedural
    content: str               # 记忆正文
    source: MemorySource       # auto_extract | user_explicit | periodic_summary
    confidence: float          # 0.0-1.0
    weight: float              # 当前权重（衰减后）
    tags: list[str]            # 自动 + 手动标签
    related_memory_ids: list[str]  # 关联记忆
    related_note_ids: list[str]    # 关联笔记
    created_at: datetime
    last_accessed_at: datetime
    last_reinforced_at: datetime
    access_count: int
    version: int               # 冲突解决版本号
    device_id: str             # 创建设备
    sync_state: SyncState      # local_only | syncing | synced | conflict
```

---

## 6. 与知识库的边界

| 维度 | 记忆层 | 知识库 |
|------|--------|--------|
| 内容来源 | 用户行为/对话 | 外部文档/网页 |
| 数据结构 | 结构化 `MemoryEntry` | 文档块 + 向量 |
| 检索方式 | 语义 + 关键词 + 关联遍历 | 向量相似度 + 全文 |
| 生命周期 | 衰减、强化、归档 | 版本管理、删除 |
| 所有权 | 纯个人，不出设备 | 可选择性同步 |
| 更新者 | AI 提取 + 用户确认 | 用户导入 + 爬取 |

---

## 7. 隐私控制

每项记忆标记隐私等级：
- **仅本地**：不离开当前设备（敏感个人信息）
- **可同步**：允许在用户设备间同步
- **可送模型**：可作为 LLM 上下文发送（默认等级）

用户可在设置中按类型批量调整隐私等级，也可逐条标记例外。

---

## 8. MemoryExtractor：自动记忆提取

### 8.1 设计目标

每次对话后自动运行，从对话中提取候选记忆。对标 ChatGPT Memory 的零摩擦记忆写入。

### 8.2 提取流程

```
对话结束
  ↓
MemoryExtractor.extract(conversation) → 候选记忆列表
  ↓
对每条候选记忆:
  ├─ 置信度 > 0.8 → 自动写入记忆库，通知用户
  ├─ 置信度 0.5-0.8 → 加入"待确认"队列，下次对话自然确认
  └─ 置信度 < 0.5 → 丢弃
  ↓
reconcile(候选记忆, 已有记忆) → 冲突检测
  ├─ 无冲突 → 直接保存
  └─ 有冲突 → 标记冲突，下次对话时确认
```

### 8.3 提取类型

| 提取类型 | 触发模式 | 示例 |
|---------|---------|------|
| 事实声明 | "我的…是…""我在学…" | "我的 NAS 地址是 192.168.1.100" |
| 偏好变化 | "我现在更喜欢…""我决定放弃…" | "我现在更喜欢用 Rust 而不是 Go" |
| 关系信息 | "我同事…""我家人…" | "我同事张工负责后端" |
| 计划意图 | "我打算…""我计划…" | "我打算下个月学 Kubernetes" |
| 经验总结 | "上次…""经验是…" | "上次用 SQLite 遇到并发瓶颈" |

### 8.4 数据结构

```python
class MemoryCandidate:
    content: str               # 候选记忆内容
    type: MemoryType           # semantic | episodic | procedural
    confidence: float          # 0.0-1.0
    source_text: str           # 原文片段
    source_conversation_id: str
    conflicting_memory_ids: list[str]  # 冲突的已有记忆

class MemoryExtractor:
    def extract(self, conversation: list[Message]) -> list[MemoryCandidate]: ...
    def reconcile(self, candidate: MemoryCandidate, existing: list[MemoryEntry]) -> ReconciliationResult: ...
```

---

## 9. MemoryScheduler：间隔重复记忆回顾

### 9.1 设计目标

对标 Reflect.app，基于 SM-2 算法（简化版）决定每条记忆的回访间隔，通过定期回顾强化长期记忆。

### 9.2 SM-2 简化算法

```
初次学习: interval = 1 天
第一次复习（评分 ≥ 3）: interval = 6 天
后续复习（评分 ≥ 3）: interval = previous_interval × EF
  EF（难易度因子）默认 2.5，根据评分微调：
    评分 5: EF += 0.1
    评分 4: EF 不变
    评分 3: EF -= 0.15
    评分 < 3: 重置 interval = 1，EF 不变

评分标准：
  5 = 完全记得，轻松
  4 = 记得，稍有犹豫
  3 = 勉强记得，有困难
  2 = 不记得，但看到后能想起来
  1 = 完全不记得
```

### 9.3 回顾调度

| 时段 | 回顾内容 | 触发方式 |
|------|---------|---------|
| 每天早上 8:00 | 昨日对话摘要 + 本周关键记忆 | 通知推送 |
| 按 SM-2 间隔 | 到期记忆回顾 | 收件箱卡片 |
| 每周日 | 本周记忆总结 + 下周预览 | 通知推送 |
| 每月 1 日 | 本月习惯回顾 | 通知推送 |

### 9.4 数据结构

```python
class MemoryReviewSchedule:
    memory_id: str
    interval_days: int         # 当前间隔
    ease_factor: float         # 难易度因子（默认 2.5）
    repetitions: int           # 复习次数
    next_review_at: datetime   # 下次复习日期
    last_review_at: datetime
    last_rating: int           # 上次评分 (1-5)

class MemoryScheduler:
    def schedule_review(self, memory: MemoryEntry) -> MemoryReviewSchedule: ...
    def get_due_reviews(self, user_id: str) -> list[MemoryEntry]: ...
    def record_review(self, memory_id: str, rating: int) -> MemoryReviewSchedule: ...
    def generate_daily_brief(self, user_id: str) -> DailyMemoryBrief: ...
```

---

## 10. MemoryLinker：自动记忆关联

### 10.1 设计目标

对标 Mem.ai 的 AI 自动组织。对每条新记忆，计算与已有记忆的语义相似度，自动建立关联。

### 10.2 关联流程

```
新记忆写入
  ↓
MemoryLinker.link(new_memory, existing_memories)
  ↓
计算语义相似度（通过 ChromaDB 向量检索 top-10）
  ↓
对每条相似记忆:
  ├─ 相似度 > 0.85 → 强关联（可能是同一件事的不同表述）→ 合并建议
  ├─ 相似度 0.70-0.85 → 关联（相关话题）→ 自动建立 related_memory_ids
  └─ 相似度 < 0.70 → 不关联
  ↓
更新记忆图谱（力导向图数据）
```

### 10.3 关联类型

| 关联类型 | 示例 | 处理方式 |
|---------|------|---------|
| 主题关联 | "在学 Rust" ↔ "Rust 所有权机制笔记" | 自动关联 |
| 时间关联 | "周三讨论了数据库" ↔ "周四做了数据库迁移" | 自动关联（相邻时间） |
| 人物关联 | "张工负责后端" ↔ "张工推荐了 PostgreSQL" | 自动关联 |
| 因果关联 | "SQLite 并发瓶颈" → "切换到 PostgreSQL" | AI 识别 + 确认 |
| 重复/冲突 | 两次不同的 NAS 地址 | 标记冲突，用户确认 |

### 10.4 数据结构

```python
class MemoryLink:
    source_id: str
    target_id: str
    link_type: str            # topic | temporal | person | causal | duplicate
    similarity_score: float
    created_at: datetime

class MemoryLinker:
    def link(self, new_memory: MemoryEntry, existing: list[MemoryEntry]) -> list[MemoryLink]: ...
    def find_duplicates(self, content: str, threshold: float = 0.85) -> list[MemoryEntry]: ...
    def build_graph(self, user_id: str) -> MemoryGraph: ...
    def suggest_merge(self, memory_a: str, memory_b: str) -> MergeSuggestion: ...
```

---

## 11. 每日回顾内容结构

```python
class DailyMemoryBrief:
    date: date
    yesterday_summary: str         # 昨日对话/操作摘要
    key_memories_this_week: list   # 本周关键记忆（top 5）
    due_reviews: list              # 到期需要回顾的记忆
    forgotten_risks: list          # 即将衰减到归档阈值的记忆
    new_insights: list             # AI 发现的新关联/模式
    tech_radar_updates: list       # TechRadar 技术动态（自升级相关）
```

---

## 12. 当前实现状态

| 组件 | 状态 | 现有文件 |
|------|------|---------|
| 记忆存储 | 🟡 原型 | `assistant_memory_index.py`（证据层） |
| 用户画像 | 🟡 原型 | `assistant_user_profile.py`（偏好真源） |
| 自动提取 | ⬜ 待实现 | 需新建 `assistant_memory_extractor.py` |
| 衰减/强化 | ⬜ 待实现 | 需在 memory_index 中增加权重更新 |
| 向量检索 | 🟡 ChromaDB 已集成 | `requirements-desktop.txt` 中已包含 |
| 多级检索 | ⬜ 待实现 | — |
| 间隔重复 | ⬜ 待实现 | 需新建 `assistant_memory_scheduler.py` |
| 自动关联 | ⬜ 待实现 | 需新建 `assistant_memory_linker.py` |
| 每日回顾 | ⬜ 待实现 | 集成到 cron_service + DailyReviewWorker |
