# 单词功能升级 — 技术文档

> 创建日期：2026-06-15
> 状态：Sprint 1-3B 已完成（8/8），P1/P2 后续待推进

---

## 一、功能总览

本次升级围绕 3 个核心目标：

1. **小问 × 单词联动** — 让小问从"独立功能"变成"学习伴侣"
2. **上下文捕获链路** — 把"看到才学"从手动变成半自动
3. **算法 + 模式升级** — SM-2 → FSRS 双写，翻转 + 听写双模式

---

## 二、Sprint 完成记录

### Sprint 1A — 复习情绪联动（vocab → mood 事件）

| 项目 | 详情 |
|------|------|
| 新增文件 | `backend/vocab_mood.py`（224 行） |
| 修改文件 | `backend/handlers/_wordbook.py`（word.review 内 emit `vocabulary.review.recorded`） |
| 修改文件 | `backend/headless_main.py`（启动/关闭 vocab_mood_coordinator） |
| 测试 | `tests/test_vocab_mood.py`（6/6 ✅） |

**设计决策：**
- 复用现有 PetMood 类型（`alert` / `happy`），通过 `reason` 字段区分语义场景
- 连续 ≥3 次 forget(quality≤2) → `mood.changed=alert`
- 一次 session ≥5 词全对 → `mood.changed=happy`
- Session idle ≥90s → `vocabulary.session.summary`

### Sprint 1B — 主动复习提醒（vocabulary.due_reminder）

| 项目 | 详情 |
|------|------|
| 新增文件 | `backend/due_reminder.py`（150 行） |
| 修改文件 | `backend/headless_main.py`（启动/关闭 scheduler） |
| 修改文件 | `FloatingChatApp.tsx`（接收 due_reminder → 小问主动开口） |
| 修改文件 | `useBackendEvents.ts`（加 3 个事件类型） |
| 测试 | `tests/test_due_reminder.py`（4/4 ✅） |

**设计决策：**
- 每 5 分钟扫一次，同日最多 1 次（状态写 `vocab_reminder.json`）
- 启动延迟 30s，等前端 WebSocket 连上
- 前端用 `lastDueReminderRef` 去重，避免重复注入

### Sprint 1C — ReAct 工具 vocab.quiz_in_chat / vocab.review_status

| 项目 | 详情 |
|------|------|
| 修改文件 | `backend/react_executor.py`（2 个工具 + catalog + executor） |
| 测试 | `tests/test_vocab_react_tools.py`（6/6 ✅） |

**工具说明：**
- `vocab.quiz_in_chat` — 从词库随机抽 due 词出小问答（中文释义 + 例句留空），prefer_due=true 优先抽到期词
- `vocab.review_status` — 返回 total/due/reviewed_today，小问可回答"我还有多少词没复习"

### Sprint 2A — 剪贴板加入生词本

| 项目 | 详情 |
|------|------|
| 修改文件 | `backend/handlers/_wordbook.py`（word.add 新增 source 参数 + emit `vocabulary.word_added`） |
| 修改文件 | `ClipboardPopupApp.tsx`（抽出 ensureDefaultWordbookId、source 标记、UX 改进） |
| 测试 | `tests/test_word_add_source.py`（3/3 ✅） |

**设计决策：**
- source 字段持久化到词条，区分 `clipboard` / `clipboard_translate` / `browser_extension` / `manual`
- 消除 addToWordbook 和 add_word action 的重复代码

### Sprint 2B — reading 联动生词提取

| 项目 | 详情 |
|------|------|
| 新增文件 | `backend/handlers/_word_extract.py`（171 行） |
| 修改文件 | `backend/handlers/_reading.py`（save_article 后异步触发生词提议） |
| 修改文件 | `backend/handlers/__init__.py`（注册 _word_extract） |
| 修改文件 | `useBackendEvents.ts`（加 `reading.words_proposed` + `vocabulary.word_added`） |
| 修改文件 | `FloatingChatApp.tsx`（接收 words_proposed → 小问注入候选词列表） |
| 测试 | `tests/test_word_extract.py`（9/9 ✅） |

**设计决策：**
- 双层过滤：本地 stopword/长度初筛 → AI 二筛挑 B1+ 难度词
- 已收词排除：避免重复推荐
- AI 失败降级：返回前 5 频次词
- RPC `translate.extract_words` 供前端/其他模块直接调用

### Sprint 2C — 浏览器扩展划词入库

| 项目 | 详情 |
|------|------|
| 修改文件 | `browser/assistant_bridge.py`（DI 注入 vocabulary_repository + add-word 路由） |
| 修改文件 | `browser/bridge_content.py`（新增 add_word_from_selection 86 行） |
| 修改文件 | `app_runtime/app_context.py`（注入 vocabulary_repository 到 bridge） |
| 修改文件 | `browser-extension/siyi-browser-assistant/manifest.json`（v0.4.0 + contextMenus + notifications） |
| 修改文件 | `browser-extension/siyi-browser-assistant/background.js`（右键菜单 + bridge 调用 + 桌面通知） |
| 修改文件 | `browser-extension/siyi-browser-assistant/README.md`（更新功能说明 + 测试指引） |
| 新增测试 | `tests/test_browser_bridge_add_word.py`（8/8 ✅） + `tests/background.add-word.test.js`（3/3 ✅） |

**设计决策：**
- 右键菜单格式：「加入思忆集生词本：%s」
- source 标记 `browser_extension`，附带 `source_url` 和 `source_title`
- 走现有 token 鉴权链路

### Sprint 3A — FSRS 双写算法

| 项目 | 详情 |
|------|------|
| 新增文件 | `repositories/_fsrs_scheduler.py`（237 行） |
| 修改文件 | `repositories/_vocab.py`（record_review 改双写 + 降级路径 _sm2_only_step） |
| 修改文件 | `backend/handlers/_wordbook.py`（新增 word.review_fsrs + vocab.rollback_fsrs） |
| 修改文件 | `requirements-desktop.txt`（加 fsrs==6.3.1） |
| 测试 | `tests/test_fsrs_scheduler.py`（12/12）+ `tests/test_vocab_record_review_fsrs.py`（8/8）+ `tests/test_wordbook_fsrs_rpc.py`（5/5）= 25/25 ✅ |

**双写策略：**
- 旧 SM-2 字段（repetitions/efactor/interval/next_review/last_review）保持原样可读
- 新增 FSRS 字段（fsrs_state/fsrs_stability/fsrs_difficulty/fsrs_step/fsrs_due/fsrs_last_review）
- next_review 取两套调度较早者（"宁紧勿松"）
- 旧词条首次评分时从 SM-2 状态推算 FSRS 初值
- FSRS 失败自动降级 SM-2（`_sm2_only_step`）
- 接口零破坏：`record_review` 仍返回 4-tuple

**评分映射：**

| 用户 5 档 | FSRS 4 档 | quality 值 |
|-----------|-----------|------------|
| 1 (忘) | 1 (Again) | 1 |
| 2 (难) | 2 (Hard) | 3 |
| 3 (慢) | 3 (Good) | 4 |
| 4 (对) | 3 (Good) | 4 |
| 5 (熟) | 4 (Easy) | 5 |

**回滚方案：**
- RPC `vocab.rollback_fsrs(confirm=true)` 清除所有 fsrs_* 字段
- 删除 `fsrs` 依赖即可彻底回到纯 SM-2

### Sprint 3B — 听写复习模式

| 项目 | 详情 |
|------|------|
| 新增文件 | `backend/handlers/_dictation.py`（184 行） |
| 新增文件 | `components/Wordbank/WordDictation.tsx`（252 行） |
| 修改文件 | `backend/handlers/__init__.py`（注册 _dictation） |
| 修改文件 | `components/Wordbank/WordbankModule.tsx`（模式切换 + 听写集成 + handleDictationRate） |
| 修改文件 | `useBackendEvents.ts`（加 `vocabulary.dictation.checked`） |
| 测试 | `tests/test_dictation.py`（21/21 ✅） |

**交互流程：**
1. 显示中文释义 + 音标 → 自动播放 TTS（0.85 速率）
2. 例句中目标词自动替换为 `____`（挖空提示）
3. 用户在输入框拼写 → 按 Enter 提交
4. 后端 `word.dictation_check` 返回字符级 diff + 建议评分
5. 前端展示 diff（🟢正确 / 🔴漏打 / ❌多打）
6. 用户点击"下一个"或按 Enter 推进

**防双写：**
- `word.dictation_check` 的 `auto_record=true` 已走 `record_review`
- 前端 `handleDictationRate` 只更新统计，不调 `word.review` RPC
- `onAdvance` 由 WordDictation 组件内部控制（先看 diff 再推进）

---

## 三、新增 RPC 方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `word.review_fsrs` | `word_id`, `rating` (1-4) | FSRS 4 档评分入口 |
| `word.dictation_check` | `word_id`, `actual`, `auto_record` | 听写检查 + 字符级 diff |
| `vocab.rollback_fsrs` | `confirm` (bool) | 紧急回滚 FSRS 字段 |
| `translate.extract_words` | `text` | 从文本提取生词候选 |

---

## 四、新增 EventBus 事件

| 事件类型 | 触发条件 | Payload 关键字段 |
|----------|----------|------------------|
| `vocabulary.review.recorded` | word.review 完成后 | word_id, word, quality, efactor, interval |
| `vocabulary.word_added` | word.add 完成后 | word_id, word, wordbook_id, source |
| `vocabulary.due_reminder` | 每日首次启动有 due 词 | due_count, today, message |
| `vocabulary.session.summary` | 复习 session idle ≥90s | total, good, forgot, duration_seconds |
| `vocabulary.dictation.checked` | word.dictation_check 完成 | word_id, word, correct, exact, suggested_quality |
| `reading.words_proposed` | save_article 后 AI 提取生词 | article_id, title, words[] |

---

## 五、后续路线图（P1/P2 待推进）

### P1 — 重大缺失

| # | 任务 | 估时 | 说明 |
|---|------|------|------|
| 4 | 更多复习模式（选择题/匹配/填空/连连看） | 3-5 天 | 选择题 ROI 最高：当前词+3干扰词释义洗牌 |
| 5 | 打卡/streak/成就系统 | 2-3 天 | 复用 reminder + task 基础设施 |
| 6 | 手机端离线模式 | 2 天 | 无配对时读 localStorage 缓存最近 200 词 |
| 7 | JSON → SQLite 迁移 | 3 天 | FTS5 + 复合索引 + 双写过渡 + 迁移脚本 |
| 8 | 程序员词库预设 | 0.5 天 | 替换/补充现有 50 词高频词（~200 个技术术语） |

### P2 — 体验槽点

| # | 任务 | 估时 | 说明 |
|---|------|------|------|
| 9 | 遗忘曲线/记忆地图可视化 | 1-2 天 | 按 stability 分桶 + 困难词 Top 10 |
| 10 | 真人 TTS 接入 | 1 天 | edge-tts 免费，缓存到 data/tts_cache/ |
| 11 | 双向复习（英→中 + 中→英 随机切换） | 0.5 天 | 翻转模式已有，加 direction toggle |
| 12 | 困难词自动入册 | 0.5 天 | lapses ≥ 3 自动进系统词本「⚠️ 重点关注」 |
| 13 | 手机端 WordbookView 拆分 | 1 天 | 700+ 行 → 5 文件（参考桌面端） |
| 14 | AI 补全 prompt 拆分 + schema 校验 | 0.5 天 | 拆成 2 个 prompt + Zod/Pydantic 校验 |

### P3 — 长期机会

| # | 任务 | 估时 | 说明 |
|---|------|------|------|
| 15 | 按主题/场景词包 | 1 天 | 旅游/面试/医疗等 |
| 16 | 今日生词 → 笔记自动整合 | 1 天 | 写日记/总结时引用 |
| 17 | OCR 拍照取词入库 | 0.5 天 | 已有 ocr.extract_text RPC |
| 18 | 阅读时点词加入 | 1 天 | 和 reading 模块联动 |
| 19 | 学习时长统计 + 复习节奏建议 | 1 天 | 仪表盘扩展 |
| 20 | 多语言支持（日语/韩语） | 3 天 | 算法通用，UI 需适配 |

---

## 六、测试清单

| 测试文件 | 覆盖范围 | 用例数 |
|----------|----------|--------|
| `tests/test_vocab_mood.py` | 情绪联动（连续 forget → alert、全对 → happy、session idle → summary） | 6 |
| `tests/test_due_reminder.py` | 到期提醒调度（首次启动、跨午夜、节流、消息体裁） | 4 |
| `tests/test_vocab_react_tools.py` | ReAct 工具（quiz_in_chat 空词库/due 优先/review_status） | 6 |
| `tests/test_word_add_source.py` | source 参数持久化 + word_added 事件 | 3 |
| `tests/test_word_extract.py` | 生词提取（stopword 过滤、AI 降级、已收词排除、reading 联动） | 9 |
| `tests/test_browser_bridge_add_word.py` | 划词入库（无 repo、空文本、自动建词本、source 标记） | 8 |
| `tests/test_fsrs_scheduler.py` | FSRS 算法层（评分映射、双写字段、回滚、SM-2 推算初值） | 12 |
| `tests/test_vocab_record_review_fsrs.py` | Repository 双写集成（双写+回滚+兼容性） | 8 |
| `tests/test_wordbook_fsrs_rpc.py` | FSRS RPC（4 档评分、rollback、兼容旧接口） | 5 |
| `tests/test_dictation.py` | 听写检查（diff 算法、评分建议、auto_record、事件发射） | 21 |
| **合计** | | **82** |

---

*最后更新：2026-06-15*
