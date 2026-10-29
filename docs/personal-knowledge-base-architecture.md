# 个人知识体系底座 — 架构分析（v0.1）

- 日期：2026-08-17
- 背景：用户希望建立"个人知识体系"，不局限于导入途径；长期目标是**本地 AI 直接接管隐私/涉密信息的处理**；当前阶段要做**尽量全面的信息获取底座**。
- 原则：**一切数据本地落地，涉密处理本地 AI 独占，本地 AI 变强即可无缝接管更多层级**。

---

## 1. 现状盘点：系统已有什么

### 1.1 信息获取途径（16+ 个，远超聊天导入）

| # | 途径 | 实现 | 状态 |
|---|---|---|---|
| 1 | 聊天记录导入（ChatGPT/Claude/Kimi/Markdown/微信） | `integrations/chat_importer.py` | ✅ 已有（微信 M1 完成） |
| 2 | Kimi 自动同步 | `integrations/kimi_sync.py` | ✅ 已有 |
| 3 | 浏览器扩展桥接（网页内容捕获） | `browser/` + `bridge_content.py` | ✅ 已有 |
| 4 | URL/文件学习 | `url_learning.learn` | ✅ 已有（SSRF/路径遍历防护） |
| 5 | OCR（图片→文字） | `ocr.extract_text` / `extract_base64` | ✅ 已有 |
| 6 | 语音转写 | `transcribe.transcribe_file/base64` | ✅ 已有（faster-whisper） |
| 7 | 收件箱随手记 | `inbox.add` | ✅ 已有 |
| 8 | 资讯雷达（RSS/资讯） | `info_radar` + `info.brief` | ✅ 已有 |
| 9 | 阅读文章 | `reading.push` / `save_article` | ✅ 已有 |
| 10 | 日记 | `daily.append` | ✅ 已有 |
| 11 | 翻译历史 | `translations.save` | ✅ 已有 |
| 12 | 行为日志（文件/浏览器/App） | `BehaviorLogger` → `behavior_extractor` | ✅ 已有 |
| 13 | 剪贴板捕获 | clipboard（proactive 触发） | ✅ 已有 |
| 14 | **外部内容导入：Readwise Reader/Highlights、Kindle 剪贴、浏览器书签** | `_external_content.py` | ✅ 已有（易被忽略） |
| 15 | 外部适配器（todoist、youdao_note） | `adapters/` | ✅ 已有 |
| 16 | 生词提取 | `word_extract` | ✅ 已有 |

### 1.2 消化与检索能力

| 能力 | 实现 | 说明 |
|---|---|---|
| 记忆提取 + 语义搜索 | `memory/index.py` + `memory/search/_chroma.py` | **ChromaDB + sentence-transformers，完全本地向量** |
| 知识图谱 | `knowledge/graph_store.py` + `graph_extractor.py` | 实体关系，本地 |
| 个人画像 | `assistant/user_profile.py` → **UserProfileService** | 四层模型（long_term/context/permission/output），已注入 context，对话自动注入 prompt |
| 习惯学习 | `services/habit_learner.py` | 发现模式并写回画像 |
| 跨源时间线 | `timeline.*`（7 类数据源） | 聚合呈现 |
| RAG 检索 | `knowledge/retrieval/` | 本地管道 |
| 统一知识查询 | `knowledge.unified_query` | 跨源 |

**结论：消化/检索底座已相当完整，且核心（向量+图谱+画像）全部本地。**

### 1.3 隐私现状与缺口

| 项 | 现状 | 缺口 |
|---|---|---|
| AI 路由 | `provider.routing` 按**查询类型**路由（reasoning/coding/…） | **没有按数据敏感度分流** |
| Judge 路由 | 短翻译等有 local 执行器（`ROUTE_EXECUTOR_LOCAL`） | 仅少量任务本地，主链路 DeepSeek 云端 |
| 云同步 | `sync/channels.py`（notes/tasks/vocab/settings/memory_sync） | **无敏感度过滤**——涉密数据若同步会出本机 |
| 数据存储 | SQLite + local_storage | 无"涉密"级标记/加密分层 |
| 本地 AI | Ollama qwen3-4b（弱） | 用户计划升级，需架构可迁移 |

---

## 2. 底座架构设计（五层）

```
L1 信息获取层（全面捕获，16+ 途径，已有）
   │  所有输入统一为「原始条目 raw item」：内容 + 来源 + 时间 + 敏感度标签
   ▼
L2 标准化层（★ 新增：底座核心）
   raw_items 存储（统一表）：
     id / source(wechat,chatgpt,browser,clipboard,ocr,voice,reading,inbox,url...) 
     / kind(text,image,audio,link,file...) / sensitivity(personal|sensitive)
     / raw_content / meta(json) / created_at
   - 内容哈希去重
   - 敏感度初始判定（来源规则 + 关键词 + 用户手动）
   ▼
L3 消化层（已有 + 扩展）
   记忆提取(memory_index·本地向量) / 知识图谱 / 画像更新(UserProfileService)
   / 生词 / 阅读 / 翻译
   ★ sensitive 内容的消化强制本地 AI（Ollama），禁止云端
   ▼
L4 检索层（已有 + 增强）
   memory.search / knowledge.unified_query / RAG
   ★ 按当前上下文权限过滤敏感度（默认不浮现涉密内容，显式查询才出）
   ▼
L5 呈现层
   时间线 / 画像 / 回顾(M3) / 个人工作台(M4) / 对话引用
```

## 3. 数据分级与本地 AI 接管（核心机制）

```
敏感度      处理 AI              存储          云同步      示例
─────────────────────────────────────────────────────────────
sensitive   仅本地 Ollama       本地+加密      ❌ 禁止    微信私聊/体检/财务/日记
personal    本地优先+云端兜底    本地          ✅ 可选    阅读/笔记/一般聊天
public      正常路由            本地+云端     ✅         资讯/网页
```

- **实现点**：LLM 调用链增加"敏感度上下文"（sensitivity 参数）→ Judge/AI 客户端强制 `local` 执行器
- **无缝升级**：未来本地 AI 变强（qwen-14b/32b 或更大）→ 把 personal 也切本地；**sensitive 从第一天起就是本地的，不需要迁移**
- **同步护栏**：cloud sync channels 过滤 sensitivity=sensitive 的条目（防涉密出本机）

## 4. 需要新建的底座组件（优先级）

| 优先级 | 组件 | 说明 |
|---|---|---|
| **P0** | raw items 存储层 | 统一原始信息条目 repository（SQLite 表 + repository 类） |
| **P0** | 敏感度判定 | 来源规则（微信→personal/sensitive）+ 关键词命中 + 用户手动覆盖 |
| **P0** | 本地优先路由扩展 | AI 调用链 sensitivity 参数 → 强制 local 执行器（核心隐私机制） |
| **P0** | 统一导入管道 | 各途径 → raw items → 消化（chat_importer 先接入） |
| P1 | 云同步敏感度过滤 | sync channels 跳过 sensitive |
| P1 | 检索敏感度过滤 | memory.search / unified_query 按上下文过滤 |
| P1 | 画像提取（M2） | 对话/条目 → 偏好/关系/事件 → UserProfileService |
| P2 | 本地加密存储 | SecureKeyStorage 类扩展（敏感条目值加密落盘） |
| P2 | 涉密隔离模式 | 显式"涉密工作区"：仅本地 AI + 不记录审计明细 |

## 5. 与现有规划的衔接

- **M1 微信导入**（已完成）→ 接入 L2 raw items 的 source=wechat 通道
- **M2 画像提取** → 写回现成的 UserProfileService 四层模型
- **M3 个人回顾** → L5 呈现
- **M4 前端工作台** → L1 导入引导 + L5 呈现（含微信导出指南）
- **本地 AI 升级** → 换 `OLLAMA_MODELS` 配置即可，架构零改动

## 6. 待确认决策

1. **敏感度默认策略**：微信/私聊/日记默认 `sensitive`（保守）还是 `personal`（用户手动标）？
2. **本地 AI 升级目标**：qwen-14b/32b？显存/硬件情况？→ 决定"本地接管"的边界与时机
3. **云同步现状**：cloud sync 是否启用过？是否有涉密数据已在同步（需要安全审计）？
4. **底座起步范围**：先做 L2 raw items 存储层（信息底座），还是先做敏感度路由（隐私机制）？
