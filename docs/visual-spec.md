# 思忆集视觉规范

> 目标：统一桌面端 (PyQt6) 悬浮气泡 + Web UI (React/Vite) 的视觉语言。
> 来源：`ui/theme/tokens.py` 设计 token + `ui/theme/functions.py` 应用函数 + 思忆便签最佳实践。

---

## 1. 设计 Token

### 1.1 色板

| Token | 浅色 | 暗色 | 用途 |
|-------|------|------|------|
| `BG` | `#eaf1fa` | `#1a1d23` | 页面背景 |
| `SURFACE` | `#ffffff` | `#1e2128` | 卡片/面板表面 |
| `SURFACE_HERO` | `#ffffff` | `#1e2128` | 英雄区表面 |
| `ACCENT` | `#f0f5ff` | `#252a32` | 次级表面/悬浮态 |
| `BORDER` | `#d0dcec` | `#2d323c` | 默认边框 |
| `BORDER_INPUT` | `#c8d4e4` | `#353a45` | 输入框边框 |
| `BLUE` | `#5b9cf6` | `#5b9cf6` | 主色/链接/用户色 |
| `BLUE_HOVER` | `#4a8be5` | `#7bb3ff` | 主色悬浮态 |
| `TEXT_PRIMARY` | `#1a1a2e` | `#e8edf5` | 正文 |
| `TEXT_SECONDARY` | `#64748b` | `#9aaec9` | 辅助文字 |
| `TEXT_MUTED` | `#8a9bb5` | `#5a6e89` | 弱化文字/占位符 |
| `SCROLLBAR` | `#bcc8d8` | `#3d4250` | 滚动条 |
| `GREEN` | `#22c55e` | `#22c55e` | AI 角色色/成功 |

**Web CSS 映射：**

```css
:root {
  --bg: #eaf1fa;
  --surface: #ffffff;
  --surface-hero: #ffffff;
  --accent: #f0f5ff;
  --border: #d0dcec;
  --border-input: #c8d4e4;
  --blue: #5b9cf6;
  --blue-hover: #4a8be5;
  --text-primary: #1a1a2e;
  --text-secondary: #64748b;
  --text-muted: #8a9bb5;
  --scrollbar: #bcc8d8;
  --green: #22c55e;
}
[data-theme="dark"] {
  --bg: #1a1d23;
  --surface: #1e2128;
  --surface-hero: #1e2128;
  --accent: #252a32;
  --border: #2d323c;
  --border-input: #353a45;
  --blue: #5b9cf6;
  --blue-hover: #7bb3ff;
  --text-primary: #e8edf5;
  --text-secondary: #9aaec9;
  --text-muted: #5a6e89;
  --scrollbar: #3d4250;
  --green: #22c55e;
}
```

**禁止行为：**
- ❌ 在 PyQt6 代码中硬编码 `#4CAF50`、`#ff0000` 等非 token 颜色
- ❌ 在 Web CSS 中硬编码色值（必须用 CSS 变量）
- ✅ 新增颜色必须先在 `tokens.py` 注册，然后同步到 Web

### 1.2 阴影层级

```
Level 0: 无阴影 — Surface 卡片本身不带阴影
Level 1: blur=12, dy=2, opacity=8%  — 普通卡片、消息气泡
Level 2: blur=24, dy=4, opacity=12% — 弹窗、下拉菜单、选中工具栏
Level 3: blur=40, dy=8, opacity=16% — 模态对话框
```

- PyQt6: 用 `apply_shadow(widget, level)`
- Web: `box-shadow: 0 {dy}px {blur}px rgba(0,0,0,{opacity})`

### 1.3 圆角

```
sm:   8px   — 小元素（标签、图标）
md:  14px   — 卡片、气泡、输入框（默认）
lg:  20px   — 英雄区、大卡片
xl:  24px   — 对话框
pill: 50%   — 头像、圆点
```

### 1.4 间距体系

间距使用 4px 基准的线性 scale：

```
space-1:   4px    — 紧凑元素间
space-2:   8px    — 按钮间、图标间
space-3:  12px    — 容器内边距（默认）
space-4:  16px    — 段落间
space-5:  20px    — 标题与内容间
space-6:  28px    — 区块间
space-7:  40px    — 大区块间隔
space-8:  56px    — 页面边距
```

**规则：** 不允许使用非 scale 上的间距值。不允许出现 margin/padding 为 7px、9px、15px 等。

### 1.5 字号

```
10px — 眼眉/标签/徽标数字
11px — 辅助文字/时间戳
12px — 按钮文字/章节标题
13px — 正文（默认）
14px — 加重大/对话标题
18px — 小号指标值
20px — 中号指标值/对话框标题
22px — 大号指标值
24px — 页面标题
32px — 英雄区指标值
```

---

## 2. 表面层级

每块 UI 由一个或多个"表面"构成，表面是带背景色和圆角的容器。

### 2.1 表面类型

| 类型 | 背景 | 边框 | 圆角 | 阴影 | 典型用途 |
|------|------|------|------|------|---------|
| `page` | BG | 无 | 0 | 0 | 页面根容器 |
| `surface` | SURFACE | BORDER 1px | md | L1 | 普通卡片、面板 |
| `hero` | SURFACE_HERO | BORDER 1px | lg | L2 | 英雄区 |
| `accent` | ACCENT | BORDER 1px | md | 0 | 次级面板 |
| `chat-shell` | ACCENT | BORDER 1px | md | 0 | 聊天容器 |
| `modal` | SURFACE | BORDER 1px | xl | L3 | 对话框 |
| `bubble-user` | BLUE 18% | BLUE 30% 1px | md | 0 | 用户消息 |
| `bubble-ai` | SURFACE | BORDER 1px | md | L1 | AI 消息 |

### 2.2 布局模板（通用）

所有 panel 和 dialog 遵循以下结构：

```
┌─ [可选] 英雄区 ────────────────── surface=hero, lg, L2 ─┐
│  标题 (24px bold)     指标 1    指标 2    指标 3        │
└─────────────────────────────────────────────────────────┘
┌─ 内容区 ──────────────────────── surface=page ─────────┐
│  ┌─ 左侧栏 ─────────┐ ┌─ 右侧主区 ───── surface ──┐  │
│  │ 导航/列表         │ │ 内容/编辑器                │  │
│  └──────────────────┘ └────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 组件规范

### 3.1 按钮

```
┌──────────────────┬──────────┬─────────┬────────┬──────────┐
│ 角色             │ 背景      │ 文字色  │ 圆角   │ 阴影     │
├──────────────────┼──────────┼─────────┼────────┼──────────┤
│ primary (主)     │ BLUE     │ white   │ pill   │ 0        │
│ secondary (次)   │ SURFACE  │ PRIMARY │ md     │ 0        │
│ tertiary (文字)  │ 透明     │ MUTED   │ 0      │ 0        │
│ danger (危险)    │ #ef4444  │ white   │ pill   │ 0        │
│ ghost (幽灵)     │ 透明     │ PRIMARY │ md     │ 0        │
│                      (hover: ACCENT)                   │
└──────────────────┴──────────┴─────────┴────────┴──────────┘
```

**尺寸：**
- 大：h=36px, padding=0 16px, font=13px
- 中：h=32px, padding=0 12px, font=12px（默认）
- 小：h=24px, padding=0 8px, font=11px（气泡内复制按钮）
- 图标：h=28px, w=28px, font=14px（圆形或 md 圆角）

**悬浮态：** 所有非透明按钮 opacity hover 变深 10%（或 blue_hover）
**点击态：** 无位移

### 3.2 输入框

```
┌──────────────────────────┐
│ 背景: SURFACE            │
│ 边框: BORDER_INPUT 1px   │
│ 圆角: md (14px)          │
│ 内边距: 4px 12px          │
│ 字号: 13px               │
│ focus 态: border-color → BLUE │
│ 占位符色: TEXT_MUTED      │
│ 最小高度: 32px            │
└──────────────────────────┘
```

**多行输入：** 同规则，max-height 128px 后滚动。

**浮动气泡中的输入框：**
- 水平内边距 8px（比标准 12px 紧凑）
- 背景用 `__INPUT_BG__` token（不是 SURFACE），因为气泡有半透明背景

### 3.3 头像（Avatar）

```
圆形, 固定尺寸:
  sm: 16px — 列表项
  md: 22px — 消息气泡 header（默认）
  lg: 32px — 对话列表

文字头像:
  背景色: 用户=BLUE, AI=GREEN
  文字色: white
  字号: 11px (md), 14px (lg)
  加粗
```

### 3.4 标签（Caption / Eyebrow）

```
眼眉 (eyebrow):
  10px, 700 weight, TEXT_MUTED, letter-spacing 0.8px
  全大写（CSS text-transform）或首字母大写
  用途: 区块标题前的分类标签

辅助文字 (caption/meta):
  11px, TEXT_MUTED2
  用途: 时间戳、统计数字

章节标题 (section-title):
  12px, 700 weight, TEXT_SECONDARY
  用途: 面板内小标题
```

### 3.5 消息气泡（核心组件）

```
┌──────────────────────────────────────┐
│  ○ 小问            [复制]            │ ← header row
├──────────────────────────────────────┤
│  AI 回复内容...                       │
│  支持 Markdown 渲染                   │
│  - 列表                                │
│  `code`                               │
├──────────────────────────────────────┤
│                              14:30   │ ← timestamp
└──────────────────────────────────────┘

最大宽度: 78% 容器宽度 (capped 200-400px)
用户气泡: 右对齐, 蓝色背景 (BLUE 18%), 蓝色边框 (BLUE 30% 1px)
AI 气泡:   左对齐, 白色背景, BORDER 1px, L1 阴影
间距: 8px 垂直间距
```

### 3.6 卡片（Web UI 专用）

```
┌─ surface ────────────────────────────┐
│ 标题 (14px bold)                     │
│ 内容 (13px, TEXT_SECONDARY)          │
│ 标签组 (sm 圆角胶囊)                  │
│ 底部: 时间戳 (11px, TEXT_MUTED2)     │
└──────────────────────────────────────┘

hover: 轻微上移 (transform: translateY(-2px))
圆角: md (14px)
阴影: L1
```

### 3.7 滚动条

```
Web:
  ::-webkit-scrollbar: 4px wide
  track: transparent
  thumb: --scrollbar, 2px radius
  thumb:hover: darken 10%

PyQt6:
  QScrollBar:vertical: 4px
  background: transparent
  handle: SCROLLBAR, 2px radius
  handle:hover: SCROLLBAR_HOVER
```

---

## 4. 浮动气泡重构清单

### 4.1 布局

```
高度方向: header(32px) | sep(1px) | 消息区(stretch) | 输入区(~44px)
宽度方向: 可拖拽调整, 默认 300px, 放大 500px, 最小 240px

header 结构:
  ● (8px 圆点)  "小问"  "· 就绪"  Stretch  ⋮(更多)

"更多"菜单 (替代当前 放大/置顶/锁定 三个按钮):
  ┌ 放大窗口 ┐
  ├ 置顶     ├
  ├ 锁定     ├
  └──────────┘
```

### 4.2 header 改造 (当前 L657-729 → 精简)

- 移除三个独占按钮（放大/置顶/锁定）
- 改为一个 `⋮` 更多按钮，点击弹出 QMenu
- header 高度从 ~40px 降到 ~32px
- 状态文字改为在「设置/更多」菜单中可配置

### 4.3 输入区改造 (当前 L741-769)

- 输入框和发送按钮水平排列，高度一致 32px
- 输入框圆角 14px（和消息气泡一致）
- 发送按钮用 BLUE 色，圆角 14px
- placeholder 精简为"问小问..."

### 4.4 消息区改造

- 用户气泡右对齐（已实现 A2）
- AI 气泡左对齐（已实现 A2）
- 头像 22px 圆形带首字（已实现 A2）
- 复制按钮 hover 显示（已实现）
- 打字指示器改为动画三点"..."而非纯文字（当前 L1075-1077）
- 选中工具栏跟随气泡背景颜色自适应

### 4.5 窗口行为

- 跟随桌宠移动（B3 待实现）
- 消息持久化存 HTML（B1 待实现）
- 快捷键 Ctrl+Enter 也发送（当前只有 Enter）

---

## 5. Web UI 规范

### 5.1 全局样式基准

```css
/* 基于 CSS 变量 */
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text-primary);
  font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
  font-size: 13px;
  -webkit-font-smoothing: antialiased;
}
```

### 5.2 页面结构

```
┌─ Header ───────────────────────────────────────────────┐
│  [logo] 思忆集                             暗黑模式切换│
├─ Content ──────────────────────────────────────────────┤
│                                                       │
│  [页面内容]                                            │
│                                                       │
└────────────────────────────────────────────────────────┘
```

Header: h=48px, surface BG, bottom-border BORDER 1px
Content: padding = space-6 (28px), max-width 1200px

### 5.3 卡片网格

```
Grid: grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))
Gap: space-5 (20px)
卡片: surface 表面, md 圆角, L1 阴影, padding space-3
卡片 hover: translateY(-2px) + L2 阴影过渡 200ms
```

### 5.4 与桌面端桥接

Web UI 通过 `siyi-bridge.ts` 调用桌面 API。所有 UI 数据通过桥接获取，Web 端只负责渲染。

**初始加载流程：**
1. `SiYiBridge.initialize()` 建立 QWebChannel
2. 页面展示 loading 态
3. 桥接连接后获取初始数据并渲染
4. 桥接断开/错误时展示异常页面

**错误处理：**
- 无数据 → 空状态插画 + "暂无内容" + 操作建议
- 加载中 → 骨架屏 (skeleton loading)
- 错误 → 错误卡片 + 重试按钮

---

## 6. 图标使用规则

### 6.1 分层

| 层级 | 图标源 | 用途 | 示例 |
|------|--------|------|------|
| L1 | 纯 SVG 线性图标 | 导航、操作按钮、全局 UI | `icons.py` 中 19 个图标 |
| L2 | emoji | 内容情感标记、状态提示 | ✅⚠️💡 |
| L3 | 手绘/自定义 | 桌宠、吉祥物 | 独立资源 |

### 6.2 规则

- 导航栏按钮 → L1 SVG 图标
- 操作按钮（发送、保存） → 纯文字，不加图标
- 消息气泡选中工具栏 → 允许 emoji（💡 解释、🌐 翻译）
- 通知/状态标记 → emoji 或彩点
- **禁止同一界面混用 L1 SVG 和 emoji 作按钮图标**

### 6.3 Web UI 图标

- 初期用 emoji 占位（避免引入图标库依赖）
- 后期统一用同一套 SVG sprite
- 不可同时用 FontAwesome + emoji + 自绘 SVG

---

## 7. 暗黑模式

### 7.1 规则

- 所有界面必须同时支持 light 和 dark
- 色值同步自 `tokens.py` 中的 `_DARK_*` 变量
- Web UI 使用 `data-theme` 属性切换
- 桌面端通过 `set_app_theme()` 切换

### 7.2 Web 切换

```typescript
// 简单实现 — 切换 <html> 的 data-theme 属性
function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('siyi-theme', next);
}

// 初始化
const saved = localStorage.getItem('siyi-theme') || 'light';
document.documentElement.dataset.theme = saved;
```

---

## 8. 实现优先级

### P0 — 立刻做（浮动气泡）

- [x] A1 AI 上下文累积
- [x] A2 用户/AI 气泡左右对齐 + 头像
- [x] A3 链接可点击
- [x] A4 发送按钮主题化
- [ ] B1 消息持久化存 HTML
- [ ] B2 header 精简（三个按钮 → ⋮ 菜单）
- [ ] B3 窗口跟随 orb

### P1 — Web UI 基础

- [ ] W1 CSS 变量主题系统（light/dark）
- [ ] W2 页面结构（header + content + 卡片网格）
- [ ] W3 桥接初始化 + loading/empty/error 三态
- [ ] W4 卡片组件（适配桌面端数据）

### P2 — 视觉统一

- [ ] V1 检查所有 PyQt6 panel 的间距对齐到 spacing scale
- [ ] V2 统一所有面板 header 风格（眼眉 + 标题 + 操作）
- [ ] V3 清理 emoji/text/SVG 混用问题
- [ ] V4 打字指示器动画三点
