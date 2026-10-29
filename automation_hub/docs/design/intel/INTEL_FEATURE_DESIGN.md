# 资讯爬取与分析（日报推送 + 仪表盘）整体设计

> 目标：打破信息壁垒、用信息更好理解“当前环境形势”。
>
> 核心原则：**多源汇聚 → 统一口径 → 证据链 → 可比对指标框架 → 可反驳结论**。

## 1. 背景与问题定义

信息壁垒通常不是“信息不够”，而是：

- 来源单一（只看一种叙事）
- 口径不一致（同一指标/事件各说各话）
- 叙事先行（观点>证据）
- 不可追溯（结论找不到出处/口径）

本功能要提供一个研究型工作流：

1) 自动汇聚多源信息（RSS/公告/媒体/智库等）
2) 对新闻与指标做结构化与去重
3) 产出可追溯的日报（10分钟读完）
4) 在仪表盘中形成稳定“形势面板”，可对照、可复盘

## 2. 范围（MVP 与后续）

### 2.1 MVP（优先）

- RSS 多源抓取（配置化）
- 入库 + 去重（canonical id）
- 基础分析：来源分布、关键词、事件簇（规则合并）
- 日报：Top 事件簇 + 指标面板变化（先支持手工/半自动）+ 不确定性与反例
- 推送：复用现有 `notifications.py` 渠道（Webhook/Telegram/SMTP）
- 仪表盘：增加资讯面板（来源分布、事件簇、日报浏览、条目检索）

### 2.2 V2（可选增强）

- embedding 聚类（更强的事件合并）
- claim-evidence 抽取（观点/事实分离）
- 反向检索（自动找反例/不同口径）
- 指标自动拉取（行情/统计/公告爬取）

## 3. 目录与产物约定

### 3.1 版本控制（配置与口径）

放在 `workspace/intel/`（可提交到 Git）：

- `workspace/intel/feeds/feeds.yaml`：信息源（入口）
- `workspace/intel/indicators/panel_12grid.yaml`：12宫格面板定义（口径护城河）
- `workspace/intel/topics/topics.yaml`：专题/事件簇规则

### 3.2 运行时数据（落盘但不提交 Git）

放在 `data/`（由 `.gitignore` 忽略）：

- `data/rss/`：抓取原始 JSON/MD（证据留底）
- `data/news_pool/`：当天聚合后的“新闻池”（结构化 JSON）
- `data/topics/`：事件簇结果（JSON）
- `data/indicators/`：指标快照/缓存
- `data/reports/`：日报输出（MD/HTML）

## 4. 数据模型（SQLite）

现状：仓库已有 `tools/tool_runs/approval/audit` 等表。

建议新增（MVP最小集合）：

### 4.1 rss_sources

- `id`：主键（stable string）
- `name`：展示名
- `url`：RSS URL
- `tags_json`：标签
- `is_enabled`：开关
- `created_at/updated_at`

### 4.2 rss_items

- `id`：主键（uuid）
- `canonical_id`：去重键（sha256）
- `source_id`：来源
- `title/link/published_at/summary`
- `raw_json`：原始条目（保留证据）
- `fetched_at`

索引：`canonical_id UNIQUE`，`published_at`，`source_id`

### 4.3 daily_briefs

- `date`：日报日期（YYYY-MM-DD）
- `md_path`：输出路径
- `stats_json`：统计（来源分布、条目数、事件簇数…）
- `created_at`

> 注：事件簇表（`event_clusters`）可在 V2 上；MVP 先规则合并并直接写 `data/topics/` 也可。

## 5. 处理管线（Pipeline）

### 5.1 Ingest（抓取）

输入：`feeds.yaml` 中启用源

输出：

- 落盘 `data/rss/rss-YYYYMMDD-HHMMSS.json|md`
- 入库 `rss_items`（带去重）

### 5.2 Normalize & Dedup（统一口径 + 去重）

- 规范化 link（去 tracking 参数）
- 规范化时间（ISO8601）
- `canonical_id = sha256(normalized_link or title+published_at)`

### 5.3 Analyze（分析）

MVP 分析输出：

- 来源分布（A/B/C/D 分层可后续引入）
- 关键词趋势（词频）
- 事件簇（规则：标题相似/关键词命中/实体映射）

### 5.4 Brief（日报生成）

日报固定回答 5 个问题：

1) What：发生了什么（事件簇 Top 5）
2) Who：谁在说（来源分布 + 同一事件差异）
3) Evidence：证据链（链接列表）
4) Numbers：指标怎么变（12宫格变动摘要）
5) Uncertainty：不确定性与反例（1~2条）

### 5.5 Push（推送）

- 生成日报后调用通知服务发送摘要（标题 + Top事件 + 链接 + 报告路径）

### 5.6 Dashboard（仪表盘）

Streamlit 增加：

- 今日新闻池：按来源/主题筛选
- 事件簇：时间线 + 多来源差异
- 日报列表：可查看历史
- 指标面板：12宫格（先展示配置 + 最近快照）

## 6. 接口建议（API / UI）

MVP 可先脚本直连 SQLite；后续建议补 API：

- `GET /intel/rss/items?date=...&source=...`
- `GET /intel/briefs?date=...`
- `POST /intel/run`（触发一次抓取+分析+日报）

## 7. 可靠性与可追溯（护城河）

- 每条结论必须能回溯到：`rss_items`（链接/发布时间/原始字段）
- 口径文件版本化：`workspace/intel/*`
- 抓取与日报生成写 `audit_events`（可观测）

## 8. 迭代顺序（建议）

1) 修复通知配置引用与 DB 口径不一致（否则无法稳定推送/展示）
2) RSS 入库 + 去重（让数据可检索）
3) 信息日报（事件簇Top + 来源分布 + 证据链）
4) Streamlit 仪表盘资讯面板
5) 再做事件簇增强与指标自动拉取
