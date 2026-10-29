# 思忆便签 — 开发路线图

> 当前版本：v1.2.0
> 基于现有功能分析和竞品定位，规划后续迭代方向。

---

## 产品定位

**思忆便签 = 轻量级浮动便签 + 待办管理**

核心差异化：浮动即贴的桌面体验（类似便利贴物理感，但数字化），极低启动成本，不干扰主工作流。

---

## 当前完成状态总览

```
✅ v1.1 — 数据安全    （文件落地存储 + 提醒通知 + 撤销重做）— 已完成
✅ v1.2 — 笔记能力    （Markdown 预览 + 图片嵌入 + 全局搜索 + 导入）— 已完成
✅ v1.3 — 用户扩展    （日历视图 + 自动更新 + 国际化 + 自定义主题）— 已完成
🟡 v1.4 — 高级组织    （看板 + 番茄钟 + 模板 + 画布模式）— 已实现，待打磨
⬜ v2.0 — 护城河      （双向链接 + AI 深度集成 + 插件系统）— 部分开始
    移动端已从 PWA 切换到 Capacitor 新项目，不在本仓库中开发
```

---

## v1.1 — 数据安全与基础体验 ✅ 已完成

### 1.1 文件落地存储 ✅

**方案实现**：
- Electron 主进程将每条浮动便签存为独立 `.md` 文件（YAML frontmatter + Markdown）
- 待办/分类/设置存为 `store.json`
- 前端通过 IPC 读写文件，localStorage 作为 fallback
- 500ms 防抖自动同步（`useStore.subscribe` → `file-store:write-note` IPC）
- 纯浏览器模式回退 localStorage

**关键文件**：
- `src/utils/fileStore.ts` — Zustand 持久化适配器 + 笔记级读写
- `electron/main.cjs` — IPC handlers（`file-store:*`）
- `src/store/useStore.ts` — subscribe 500ms 防抖同步

---

### 1.2 提醒与通知 ✅

**方案实现**：
- Electron 主进程 30s 轮询所有待办 dueDate
- 到期通过 Electron Notification API 弹系统通知（窗口隐藏时也可触发）
- 通知已提醒记录持久化（`.notified.json`），避免重复通知
- 前端 `reminder.ts` 订阅 store 变化，通过 IPC 同步待办到主进程

**关键文件**：
- `src/utils/reminder.ts` — 订阅 store → IPC `reminder:update`
- `electron/main.cjs`（lines ~923-939）— startReminderScheduler / pollReminders

---

### 1.3 撤销 / 重做 ✅

**方案实现**：
- UndoManager 类，50 步操作历史栈
- 每个操作记录 `{ type, before, after, timestamp }`
- contentEditTracker 2s 去抖合并连续内容编辑
- Ctrl+Z / Ctrl+Shift+Z 全局快捷键
- 浮动撤销提示条

**关键文件**：
- `src/store/undoManager.ts` — 50 步撤销栈
- `src/store/contentEditTracker.ts` — 2s 去抖内容追踪
- `src/components/Common/UndoToast.tsx` — 撤销提示 UI

---

## v1.2 — 笔记能力与效率提升 ✅ 已完成

### 2.1 Markdown 实时预览 ✅

**方案实现**：
- `react-markdown` + `remark-gfm` 渲染 Markdown
- GFM 扩展支持：表格、任务列表、删除线
- 代码块语法高亮（`react-syntax-highlighter`）
- 工具栏切换按钮：编辑 / 预览模式

**关键文件**：
- `src/components/FloatingNote/MdPreview.tsx`
- `package.json` — react-markdown, react-syntax-highlighter, remark-gfm

---

### 2.2 图片粘贴/嵌入 ✅

**方案实现**：
- 从剪贴板读取图片（Electron 剪贴板 API / 浏览器 paste 事件）
- 图片存入用户数据目录 `assets/` 文件夹
- 笔记内以 `<img src="assets://...">` 显示
- Electron 自定义协议 `assets://` 映射到本地图片路径

**关键文件**：
- `src/utils/imageStore.ts`
- `electron/main.cjs` — assets:// 协议注册

---

### 2.3 全局快速搜索 (Ctrl+K) ✅

**方案实现**：
- 全局唤出搜索弹窗（类 Raycast 命令面板）
- Ctrl+K / Ctrl+Shift+F 触发
- 同时搜索：笔记标题、笔记内容、待办文本
- 搜索结果分组显示（笔记 / 待办）
- 选中直达聚焦笔记
- `fuse.js` 模糊匹配

**关键文件**：
- `src/components/Search/QuickSearchModal.tsx`
- `package.json` — fuse.js

---

### 2.4 导入功能 ✅

**方案实现**：
- 支持导入格式：Markdown（.md）、JSON（本应用导出格式）、纯文本（.txt）
- 导入时自动识别标题 + 内容，创建为便签
- 文件选择对话框（Electron 或浏览器 `<input type="file">`）

**关键文件**：
- `src/utils/importer.ts`
- `src/components/Common/ImportModal.tsx`
- `src/utils/__tests__/importer.test.ts`

---

## v1.3 — 用户群扩展 ✅ 已完成

### 3.1 日历视图 ✅

**方案实现**：
- 月视图日历，显示该日截止的待办数量徽标
- 点击日期展开当日待办列表
- 与 Kanban / 列表视图切换

**关键文件**：
- `src/components/Todo/CalendarView.tsx`

---

### 3.2 自动更新 ✅

**方案实现**：
- 集成 `electron-updater`
- 配合 GitHub Releases 发布
- 启动时后台检查更新 → 有新版时提示 → 下载 → 重启安装
- 下载进度显示

**关键文件**：
- `src/components/Common/UpdateNotifier.tsx`
- `electron/main.cjs` — autoUpdater 配置
- `package.json` — electron-updater, build.publish 配置

---

### 3.3 国际化多语言 ✅

**方案实现**：
- 轻量自研 i18n 方案（非 react-i18next）
- 提取 UI 字符串为 key-value 映射
- 提供 en / zh-CN 两套翻译
- 手动可切换，设置持久化

**关键文件**：
- `src/i18n/index.ts` — i18n 引擎
- `src/i18n/zh-CN.ts`
- `src/i18n/en.ts`
- `src/utils/__tests__/i18n.test.ts`

---

### 3.4 自定义主题 ✅

**方案实现**：
- 色盘自定义：主色、背景色、毛玻璃透明度、圆角
- 预设主题包：默认暗黑 / 明亮 / 极光 / 日系 / 赛博朋克
- 主题 CSS 变量化，覆盖 Tailwind 配置
- 主题持久化

**关键文件**：
- `src/components/Common/CustomThemeEditor.tsx`
- `src/utils/customTheme.ts`
- `src/utils/__tests__/customTheme.test.ts`

---

## v1.4 — 高级组织能力 🟡 已实现，待打磨

以下功能已实现基础版本，需持续完善用户体验和稳定性。

### 4.1 看板视图 🟡

便签 / 待办的看板视图，支持列管理、拖拽排序。

- `src/components/Todo/KanbanBoard.tsx`

### 4.2 番茄钟 🟡

内置番茄钟计时器，支持专注 / 休息切换。

- `src/components/Todo/PomodoroTimer.tsx`

### 4.3 模板系统 🟡

便签模板选择器，可从预设模板快速创建笔记。

- `src/components/Common/TemplatePicker.tsx`
- `src/utils/templates.ts`

### 4.4 画布模式 🟡

浮动便签的画布管理模式，支持平移、缩放、自由排列。

- `src/components/FloatingNote/CanvasMode.tsx`

### 4.5 数据导出 🟡

支持导出为 Markdown / JSON 格式。

- `src/utils/export.ts`
- `src/utils/__tests__/export.test.ts`

### 4.6 自动备份 🟡

定时自动备份数据文件。

- `src/utils/backup.ts`
- `src/utils/__tests__/backup.test.ts`

### 4.7 AI 工具栏 🟡

选中文本后的 AI 操作菜单（续写、总结、翻译、润色）。

- `src/components/FloatingNote/AIToolbar.tsx`
- `src/utils/ai.ts`

---

## v2.0 — 护城河与生态 ⬜ 未开始

### 5.1 笔记双向链接 + 知识图谱

- 支持 `[[笔记标题]]` 语法创建笔记间链接
- 每篇笔记底部显示"反向链接"列表
- 可选：力导向图可视化展示笔记网络

### 5.2 AI 深度集成

- **自然语言创建**：在搜索框输入"明天下午3点开会提醒我"→ 自动创建待办 + 设提醒
- **智能标签**：保存笔记时 AI 自动推荐标签
- **笔记问答 (RAG)**：对整个笔记库提问，AI 检索相关内容后回答

### 5.3 移动端配套（已迁移到新项目）

PWA 方案已废弃，移动端已使用 Capacitor 新建项目。本节仅供历史参考。
- 或 Flutter / React Native 重写核心

### 5.4 插件系统

- 定义插件 API 规范：生命周期钩子（onCreate, onSave, onDelete…）
- 插件从本地文件夹加载

---

## 测试覆盖现状

| 文件 | 状态 |
|------|------|
| `src/utils/__tests__/trash.test.ts` | ✅ 已存在 |
| `src/utils/__tests__/export.test.ts` | ✅ 已存在 |
| `src/utils/__tests__/backup.test.ts` | ✅ 已存在 |
| `src/utils/__tests__/importer.test.ts` | ✅ 已存在 |
| `src/utils/__tests__/reminder.test.ts` | ✅ 已存在 |
| `src/utils/__tests__/helpers.test.ts` | ✅ 已存在 |
| `src/utils/__tests__/i18n.test.ts` | ✅ 已存在 |
| `src/utils/__tests__/customTheme.test.ts` | ✅ 已存在 |

共计 **8 个测试文件**，覆盖核心工具函数。仍需补充：fileStore、undoManager、组件测试。

---

## 技术债务与质量改进

| 项目 | 说明 | 优先级 | 状态 |
|------|------|--------|------|
| 测试覆盖率 | 当前约 20%，核心模块（fileStore, undoManager）缺测试 | P1 | 🟡 进行中 |
| 类型完善 | 部分 `any` / `unknown` 可收窄，Electron API 类型声明 | P2 | ⬜ |
| 错误处理 | 文件读写、存储满、AI 请求失败的统一错误提示 | P1 | 🟡 部分完成 |
| 组件测试 | 当前全为 utils 测试，无组件级测试 | P2 | ⬜ |
| E2E 测试 | Playwright 框架已就绪，尚无 E2E 测试 | P2 | ⬜ |
| 图标已替换 | SVG 源图标 + 多尺寸 PNG 已通过 `generate-icons` 生成 | ✅ | 已完成 |
| package.json 字段 | description、author 已补充 | ✅ | 已完成 |

---

## 竞品定位参考

| 竞品 | 思忆便签的差异优势 | 思忆便签的劣势 |
|------|-------------------|---------------|
| **Obsidian** | 启动快、浮动窗口即贴感、集成待办 | 无插件生态、无图谱、无社区 |
| **Notion** | 轻量百倍、无需网络、内存占用低 | 无数据库、无协作、无模板市场 |
| **Apple Notes** | Windows 可用、浮动便签更灵活、开源可控 | 无系统集成、无 iCloud 同步 |
| **Microsoft To Do** | 便签 + 待办一体化、看板视图、番茄钟 | 无跨平台同步、无团队功能 |
| **TickTick** | 无订阅制、本地优先、隐私友好 | 无习惯追踪 |

---

## 下一步建议

1. **补充测试**：fileStore、undoManager、组件级测试 → 提升覆盖率至 60%+
2. **v1.4 功能打磨**：看板、番茄钟、模板、画布模式的体验完善
3. **E2E 测试**：Playwright 接入关键用户流程
4. **v2.0 预研**：双向链接 + AI 深度集成的方案设计

---

*最后更新：2026-05-11*
*对应版本：v1.2.0*
