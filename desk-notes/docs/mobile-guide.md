# 思忆便签 — 手机端开发指南

> 基于 monorepo 架构（`shared` + `desktop` + `mobile`），面向手机端独立开发的指导文档。
> 详细的重构计划见 [monorepo-restructure.md](monorepo-restructure.md)。
>
> **当前状态：** PWA 方案已废弃，手机端已使用 Capacitor 新项目重写。
> 本文档及 `packages/mobile/` 代码仅作架构参考，不再维护。

---

## 目录

1. [现状与策略](#1-现状与策略)
2. [AI 在手机端](#2-ai-在手机端)
3. [指南针集成](#3-指南针集成)
4. [轻量养生（中医）](#4-轻量养生中医)
5. [PWA 适配清单](#5-pwa-适配清单)
6. [响应式 UI 规范](#6-响应式-ui-规范)
7. [实现路线图](#7-实现路线图)
8. [Monorepo 结构](#8-monorepo-结构)

---

## 1. 现状与策略

### 1.1 交付形态

| 形态 | 支持度 | 说明 |
|------|--------|------|
| PWA（浏览器安装） | ✅ 独立项目 | `packages/mobile/` 使用 VitePWA |
| iOS Safari | ⚠️ 待验证 | 需单独测试 |
| Android Chrome | ✅ 已验证 | 标准 PWA 支持 |
| 原生 App | ❌ 不采用 | — |

### 1.2 架构变更：从单体到 Monorepo

**决策背景：** 桌面端 UI（浮动毛玻璃面板、拖拽便签画布）与手机端（全屏触控、底部 Tab 导航）交互模型完全不同。66 个 UI 组件中约 90% 需要重写。与其在同一项目里塞条件分支，不如拆为独立项目。

**新架构：**

```
desk-notes/
├── packages/
│   ├── shared/     → types, store, utils, i18n（两端共用）
│   ├── desktop/    → 现有 Electron 桌面端
│   └── mobile/     → 新建 PWA 手机端（本文档主体）
```

**开发命令：**

```bash
npm run dev:desktop      # 桌面端开发
npm run dev:mobile       # 手机端开发
npm run build:shared     # 共享包构建
```

### 1.4 桌面 vs 手机差异

| 维度 | 桌面 (Electron) | 手机 (PWA) |
|------|----------------|-------------|
| 窗口 | 多窗口 / 浮动 | 单页全屏 |
| 文件系统 | Node.js fs 全套 | 无 — 仅 localStorage / IndexedDB |
| AI | Ollama localhost | 仅云端 API |
| 传感器 | 无 | DeviceOrientation, Geolocation, Touch |
| 通知 | Electron Notification | Push API + Service Worker |
| 离线 | 本地文件始终可用 | 需 SW cache 策略 |

### 1.5 数据存储适配

在 monorepo 架构下，各端使用 shared store + 各自注册的 platform adapter：

```
shared/store/         ← 核心状态 + action（两端共用）
desktop: ElectronStorageAdapter → store.json + notes/{id}.md
mobile:  LocalStorageAdapter    → localStorage（键: desk-notes-storage）
```

手机端只需确保 `localStorage` 路径覆盖所有写操作即可。store 逻辑复用。

---

## 2. AI 在手机端

### 2.1 问题

当前 AI 架构默认连接 `http://localhost:11434`（Ollama），手机端无法使用：

- 手机不运行 Ollama
- PWA 不能启动本地进程
- 跨网络访问桌面 Ollama 需要用户手动配置 IP

### 2.2 架构现状

```
src/utils/ai/
├── types.ts    → AIProvider 接口定义、AIAction 枚举
├── index.ts    → getAIProvider() / streamAI() 入口
└── local.ts    → LocalAIProvider（Ollama + OpenAI 协议）
```

`AIProvider` 接口已抽象到位，新增 provider 不需改消费者代码。

### 2.3 手机端方案

#### 路径 A：OpenAI-compatible 云端 API（推荐）

`LocalAIProvider` 已有 `protocol: 'openai'` 分支，只需：

```typescript
// 现有代码已支持，在 AIConfig 中配置：
{
  enabled: true,
  protocol: 'openai',
  apiUrl: 'https://api.deepseek.com',  // 或其他兼容端点
  apiKey: 'sk-xxx',
  model: 'deepseek-chat',
}
```

**手机端需要新增：**

1. **AI 设置页** — 在 `AcrylicPanel` 的设置区域加一段表单：

   ```
   ┌─────────────────────┐
   │ AI 设置              │
   │ ─────────────────── │
   │ API 地址 [__________]│
   │ API Key  [__________]│
   │ 模型名   [__________]│
   │ [✓] 启用自动标签     │
   │ [测试连接]           │
   └─────────────────────┘
   ```

   - 数据通过 `useStore` 的 `aiConfig` 字段 persist 到 localStorage
   - `apiKey` 存储明文（PWA 无更安全方案）。可加提示："API Key 仅存本地"
   - 常见服务商提供一键填入按钮：DeepSeek / Moonshot / SiliconFlow / OpenAI

2. **首次使用引导**

   当 `aiConfig.enabled === false` 且检测到非 Electron 环境时，在 AI 功能触发时弹轻提示：

   ```
   "AI 功能需要配置 API Key，是否前往设置？"
   ```

#### 路径 B：局域网 Ollama 中继（高级用户）

用户桌面已跑 Ollama 时，手机可通过 WiFi 连接：

- 桌面跑一个轻量 CORS relay（5 行 Express）
- 手机填入 `http://192.168.x.x:11434`
- 不额外产生 API 费用
- 数据不出局域网

当前代码的 `apiUrl` 已支持自定义，不需要改动。

#### 路径 C：混合策略（推荐）

将 AI 能力按复杂度拆层，简单任务走内置规则引擎，复杂任务走云端 API，中间加自动降级层。

##### 分层设计

```
┌──────────────────────────────────────────────┐
│              消费者（组件层）                    │
│  AIAction → processText()                     │
│  suggestTags() / parseIntent() / ask()         │
└──────────────┬───────────────────────────────┘
               │ 通过 AIProvider 接口调用
               ▼
┌──────────────────────────────────────────────┐
│          ProviderRouter（自动路由层）            │
│                                              │
│  可用 + 在线 ──────→ LocalAIProvider（云端）    │
│  可用 + 离线 ──────→ RuleAIProvider（规则引擎）  │
│  未配置         ──────→ RuleAIProvider（规则引擎）│
└──────────────────────────────────────────────┘
```

##### 1. RuleAIProvider（离线规则引擎）

`src/utils/ai/rules.ts` — 纯规则实现 `AIProvider` 接口，零依赖零请求：

```typescript
// src/utils/ai/rules.ts（新增）

// 所有 processText 操作降级为简单文本处理
const ACTION_RULES: Record<AIAction, (text: string) => string> = {
  continue:      (t) => t,                          // 无法续写，原文返回
  summarize:     (t) => t.length > 50 ? t.slice(0, 50) + '…' : t,
  translate_zh:  (t) => `[离线模式] ${t}`,          // 无法翻译
  translate_en:  (t) => `[Offline] ${t}`,
  polish:        (t) => t,                          // 无法润色
  organize:      (t) => t,                          // 无法整理
}

// 标签推荐用关键词映射表
const TAG_RULES: [RegExp, string][] = [
  [/会议|讨论|sync|review|standup|agenda/, '工作'],
  [/买菜|做饭|晚餐|午饭|食谱/, '生活'],
  [/读书|笔记|学习|课程|作业/, '学习'],
  [/bug|修复|重构|部署|上线/, '开发'],
  [/设计|UI|交互|视觉/, '设计'],
  [/预算|发票|报销|财务/, '财务'],
  [/旅行|机票|酒店|行程/, '旅行'],
  [/健身|跑步|游泳|运动/, '健康'],
  [/生日|礼物|聚会/, '社交'],
  [/idea|灵感|TODO|备忘/, '灵感'],
]

class RuleAIProvider implements AIProvider {
  async processText(action: AIAction, text: string): Promise<string> {
    const rule = ACTION_RULES[action]
    return rule ? rule(text) : text
  }

  async suggestTags(content: string, existingTags: string[]): Promise<string[]> {
    const matched = new Set<string>()
    for (const [pattern, tag] of TAG_RULES) {
      if (pattern.test(content)) matched.add(tag)
    }
    return [...matched].slice(0, 5)
  }

  async parseIntent(input: string): Promise<{ type: 'unknown' }> {
    return { type: 'unknown' }
  }

  async ask(_question: string, _context: string[]): Promise<string> {
    return '离线模式下无法回答，请连接后重试。'
  }
}
```

##### 2. 网络状态感知 + 自动降级

```typescript
// src/utils/ai/network.ts（新增）
function isOnline(): boolean {
  return navigator.onLine
}

function listenNetworkChanges(onOnline: () => void, onOffline: () => void): () => void {
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)
  return () => {
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
  }
}
```

##### 3. ProviderRouter 自动路由

```typescript
// src/utils/ai/index.ts — 增强 getAIProvider()

let _cachedRuleProvider: RuleAIProvider | null = null

export function getAIProvider(config?: AIConfig): AIProvider {
  const cfg = config ?? getDefaultConfig()
  const isMobile = !window.electronAPI

  // 手机端 Ollama 不可用 → 降级
  if (isMobile && cfg.protocol === 'ollama') {
    return getCachedRuleProvider()
  }

  // 已配置云端 API 但在离线状态 → 降级
  if (cfg.enabled && !navigator.onLine) {
    return getCachedRuleProvider()
  }

  // 未启用 → 规则引擎兜底
  if (!cfg.enabled) {
    return getCachedRuleProvider()
  }

  return new LocalAIProvider(cfg)
}

function getCachedRuleProvider(): RuleAIProvider {
  if (!_cachedRuleProvider) {
    _cachedRuleProvider = new RuleAIProvider()
  }
  return _cachedRuleProvider
}
```

##### 4. UI 反馈

当使用规则引擎时，AI 输出应有清晰标识避免误导：

| 场景 | 显示 |
|------|------|
| 标签推荐走规则 | 正常展示标签，不额外提示 |
| 续写/翻译走规则 | 输出前加 `[离线模式]` 前缀 |
| 网络恢复 | 自动切换到云端 provider |
| 完整功能提示 | "AI 配置后可获得完整功能"（仅首次使用规则引擎时） |

##### 5. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/utils/ai/rules.ts` | 新增 | RuleAIProvider 实现 |
| `src/utils/ai/network.ts` | 新增 | 网络状态监听 |
| `src/utils/ai/index.ts` | 修改 | provider 路由逻辑 |
| `src/utils/ai/types.ts` | 可能修改 | 如需要导出 RuleAIProvider |

##### 路径对比总结

| 维度 | 路径 A（纯云端） | 路径 B（局域网 relay） | 路径 C（混合） |
|------|----------------|---------------------|--------------|
| 实现成本 | 低（UI 配置页） | 中（需 relay 服务） | 中（规则引擎 + 路由） |
| 离线可用 | ❌ | ❌ | ✅ 基础功能 |
| 标签准确率 | 高 | 高（LLM） | 中（关键词匹配） |
| 翻译/润色 | ✅ 完整 | ✅ 完整 | ⚠️ 降级无此能力 |
| 长期维护 | AI Key + 费用 | 桌面服务 | 规则表需维护 |

### 2.4 Provider 自动选择逻辑（概要）

> 详细实现在上方 [路径 C 第 3 节](#_3-providerrouter-自动路由)。

核心逻辑：

```
getAIProvider(config)
    │
    ├─ 手机端且 protocol=ollama ──→ RuleAIProvider（规则引擎降级）
    ├─ 已配置但离线 ──────────────→ RuleAIProvider（规则引擎降级）
    ├─ 未启用 AI ────────────────→ RuleAIProvider（规则引擎兜底）
    └─ 已配置且在线 ────────────→ LocalAIProvider（云端 API 正常调用）
```

当网络状态变化时，已创建的 provider 不自动切换。应用通过以下方式保证一致性：

- 每次调用 AI 功能时通过 `getAIProvider()` 获取（非单例缓存）
- 网络变化时在下一次调用自动使用新的 provider
- `processText` / `suggestTags` 等接口签名不变，消费者无感知

---

## 3. 指南针集成

### 3.1 目标

在奇门遁甲结果页，用手机罗盘实时显示用户的朝向，并与奇门推算的「吉利方位」做视觉对比。

### 3.2 浏览器 API

```typescript
// DeviceOrientationEvent — 返回 alpha/beta/gamma
window.addEventListener('deviceorientation', (event) => {
  const heading = event.alpha  // 0-360, 指向磁北
})

// iOS 13+ 需用户手势触发权限请求
DeviceOrientationEvent.requestPermission()
  .then(state => {
    if (state === 'granted') {
      window.addEventListener('deviceorientation', ...)
    }
  })
```

**关键事实：**

| 平台 | 需权限请求 | 返回绝对方位 | 备注 |
|------|-----------|-------------|------|
| iOS Safari | ✅ `requestPermission()` | `webkitCompassHeading` 是真北 | 非 HTTPS 不可用 |
| Android Chrome | ❌ 自动授权 | `event.alpha` 是磁北 | 需校准 |
| 桌面浏览器 | 不支持 | — | 降级为纯文字 |

### 3.3 模块设计

#### `src/hooks/useCompass.ts`（新增）

```typescript
interface CompassState {
  heading: number | null       // 当前朝向角度（0=北, 90=东）
  headingLabel: string | null  // 方位文字（"正北"）
  permission: 'prompt' | 'granted' | 'denied' | 'unsupported'
  error: string | null
}

function useCompass(): CompassState & {
  requestPermission: () => Promise<void>
}
```

- 内部用 `useEffect` 注册/清理 `deviceorientation` 事件
- 角度 → 方位文字用已有映射（`fengshui.ts` 中有方向列表）
- 在 `compasschange` 事件中触发重渲染（考虑节流到 100ms）

#### `src/components/Todo/CompassWidget.tsx`（新增）

效果示意：

```
        N
        ↑
    NW  |  NE
  ←─── 吉 ───→   ← 绿色箭头指向吉方
    SW  |  SE
        ↓
        S

  你当前朝向: 正北 (0°)
  吉方: 正东 (90°) → 向右转 90°
```

**视觉设计：**

- 圆形罗盘，直径约 200px（手机端适配 fill width）
- 8 方位标记（N/NE/E/SE/S/SW/W/NW — 中文）
- 当前朝向：红色指针/三角，固定指上，罗盘旋转对齐角度
- 吉方：绿色高亮标记（半圆或外圈标记）
- 角度差文字提示（"左转 45°" / "右转 90°"）
- 不含磁偏角校正（消费级精度足够）

**状态处理：**

| 状态 | 显示 |
|------|------|
| 权限未授予 | "点击开启指南针" 按钮（调用 requestPermission） |
| 权限被拒绝 | 显示纯文字方位 + "请在系统设置中开启" 提示 |
| 不支持 | 隐藏罗盘，只留文字 |
| 正常 | 罗盘 + 方位文字 + 吉方标注 |

**降级策略：**

`CompassWidget` 必须通过 `heading: number | null` 属性接收数据，而不是直接依赖 useCompass hook。这样桌面端可以传 null 显示静默降级（无罗盘 UI），手机端透传 useCompass 的数据。

### 3.4 集成到 QimenPanel

在 QimenPanel 结果页，在「吉利方位」大字下方条件渲染：

```
┌────────────────────┐
│   吉利方位 正东      │  ← 现有
│   ─────────────     │
│   [CompassWidget]   │  ← 新增（移动端显示）
└────────────────────┘
```

判断条件：

```typescript
const isMobile = !window.electronAPI
// 或从 props / context 获取
```

### 3.5 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/hooks/useCompass.ts` | 新增 | 罗盘数据 hook |
| `src/components/Todo/CompassWidget.tsx` | 新增 | 罗盘 UI 组件 |
| `src/components/Todo/QimenPanel.tsx` | 修改 | 条件渲染 CompassWidget |
| `src/i18n/zh-CN.ts` | 修改 | 指南针相关文案 |
| `src/i18n/en.ts` | 修改 | 英文文案 |

---

## 4. 轻量养生（中医）— 已决策：暂缓实施

> **决策：当前阶段不加入手机端。** 以下保留完整技术规划供未来参考，但不纳入当前或下一阶段开发计划。

### 决策依据

1. **核心闭环未完成。** 八字-黄历-奇门 → 日常行动（建待办、排时间、定方位）的主链路尚未缝合。在"知"尚未贯通到"行"之前，加一条对内养生支线会分散焦点，推迟核心价值验证。

2. **模糊产品定位。** 八字/奇门解决"我该不该做、何时做、朝哪走"（对外行动辅助），中医解决"我身体需要什么"（对内状态关照）。把两者揉在一个待办/笔记界面里，用户会困惑——这到底是决策军师还是健康管家？当前阶段需要保持"决策操作系统"的锋利心智，而不是用"体质调养"的温和需求稀释它。

3. **监管风险。** 八字、黄历归为"传统文化/决策参考"，安全。但养生建议一旦涉及"吃啥、不吃啥、按哪里"，即使只是对照表，在实践中也容易被推过红线。文档中规划的 ❌ 体质辨识/穴位按压/AI 建议在实际维护中很难守住。不做是最干净的。

4. **精力约束。** 中医理论的复杂性与命理相当，要做就必须做到同样深度，否则是鸡肋。核心闭环尚在建设中，精力应集中投放。

### 未来接入方式（非当前计划）

中医数据未来不应作为独立功能出现，而应作为**决策系统的一个深层参数**——当 App 积累了足够的时空和个人状态数据后，将身体状态维度融入每次建议：

> "今日奇门开在西南，但根据过往记录，你此时节肝火较旺，建议在下午吉时前小憩十五分钟再赴约。"

那时，用户不会觉得 App 在教他养生，而是觉得"这个 App 怎么这么懂我当下的状态"。前提是当前的决策系统先跑通、被信任。

### 4.1 范围限定（技术参考，实施前需重新评估）

只做 **纯对照表**，不做诊断/推荐/处方：

| 模块 | 内容 | 风险 |
|------|------|------|
| ✅ 时辰养生 | 12 时辰 → 经络 → 生活贴士 | 极低，纯知识展示 |
| ✅ 节气养生 | 24 节气 → 饮食/作息提示 | 极低，纯知识展示 |
| ✅ 五行食养 | 五行 → 季节 → 食物建议 | 极低，纯文化参考 |
| ❌ 体质辨识 | 问卷 → 9 种体质判定 | ⚠️ 类诊断行为 |
| ❌ 穴位按压 | 症状 → 穴位推荐 | ⚠️ 医疗行为 |
| ❌ AI 养生建议 | 用户描述 → AI 生成建议 | ⚠️ 输出不可控 |

### 4.2 数据模块

#### `src/utils/astrology/wellness.ts`（新增）

```typescript
// ── 时辰养生 ──

interface ShiChenWellness {
  shichen: string       // "子时"
  timeRange: string     // "23:00-01:00"
  meridian: string      // "足少阳胆经"
  organ: string         // "胆"
  tip: string           // 生活建议
  avoid: string         // 避免事项
}

const SHICHEN_WELLNESS: ShiChenWellness[] = [
  { shichen: "子时", timeRange: "23:00-01:00", meridian: "足少阳胆经", organ: "胆",
    tip: "宜入睡养胆，忌熬夜", avoid: "熬夜、进食" },
  { shichen: "丑时", timeRange: "01:00-03:00", meridian: "足厥阴肝经", organ: "肝",
    tip: "熟睡养肝血", avoid: "饮酒、愤怒" },
  // ... 12 条
]

// ── 节气养生 ──

interface JieQiWellness {
  jieqi: string         // "立春"
  date: string          // "2月3-5日"
  element: string       // "木"
  organ: string         // "肝"
  dietTip: string       // 饮食建议
  lifestyleTip: string  // 起居建议
}

const JIEQI_WELLNESS: JieQiWellness[] = [
  { jieqi: "立春", date: "2月3-5日", element: "木", organ: "肝",
    dietTip: "宜辛甘发散（韭菜、香菜、葱蒜）",
    lifestyleTip: "夜卧早起，舒缓形体" },
  // ... 24 条
]

// ── 五行食养（与 Bazi 联动） ──

interface WuxingDietAdvice {
  element: string       // "金" | "木" | "水" | "火" | "土"
  season: string        // "秋"（对应季节）
  organ: string         // "肺"（对应脏腑）
  foods: string[]       // 推荐食物
  flavor: string        // "辛"（五味）
}

const WUXING_DIET: WuxingDietAdvice[] = [
  { element: "金", season: "秋", organ: "肺", foods: ["白萝卜", "梨", "银耳", "百合"], flavor: "辛" },
  // ... 5 条
]
```

总数据量约 120 行，无外部依赖。

#### 导出函数

```typescript
function getCurrentShichen(): ShiChenWellness | null      // 根据当前时间返回时辰养生
function getShichenByHour(hour: number): ShiChenWellness   // 根据指定小时返回
function getJieqiWellness(jieqi: string): JieQiWellness | null
function getSeasonalDiet(element: string): WuxingDietAdvice | null  // 根据八字五行
```

### 4.3 UI 组件

#### `src/components/Todo/WellnessPanel.tsx`（新增）

- 轻量面板，类似 QimenPanel 的 select/result 模式
- 默认显示当日时辰 + 节气信息
- 可展开查看五行饮食建议（与八字数据联动，如果用户有八字档案）
- 纯信息展示，无需加载状态

#### 集成方式

已有 `GuidanceDashboard` 的 `adviceView` 是 'advice' | 'bazi' | 'qimen' 三 tab。建议新增：

```typescript
type AdviceView = 'advice' | 'bazi' | 'qimen' | 'wellness'
```

在 dashboard 第四 tab：

```
[ 指引 | 八字 | 奇门 | 养生 ]
                        ^^^^ 新增
```

### 4.4 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/utils/astrology/wellness.ts` | 新增 | 养生数据对照表 |
| `src/utils/astrology/__tests__/wellness.test.ts` | 新增 | 数据正确性 + 边界测试 |
| `src/components/Todo/WellnessPanel.tsx` | 新增 | 养生面板 |
| `src/components/Todo/GuidanceDashboard.tsx` | 修改 | 添加第4个 tab |
| `src/types/index.ts` | 修改 | 更新 AdviceView 类型 |
| `src/i18n/zh-CN.ts` | 修改 | 养生相关文案 |
| `src/i18n/en.ts` | 修改 | 英文文案 |

---

## 5. PWA 适配清单

### 5.1 Service Worker

当前（`vite-plugin-pwa` 配置）：

```typescript
// vite.config.ts 中已配置：
injectRegister: 'auto',        // 自动注册
registerType: 'autoUpdate',    // SW 自动更新
workbox: {
  globPatterns: ['**/*.{js,css,html,png,jpg,webp,svg,json}'],
  runtimeCaching: [
    // Google Fonts → CacheFirst
    // CDN assets → StaleWhileRevalidate
  ],
}
```

**验证清单：**

- [ ] SW 注册成功（打开 DevTools → Application → Service Workers）
- [ ] 离线后页面能加载
- [ ] offline.html 存在且可用（如果配置了）
- [ ] 新版本发布后 SW 自动更新生效

### 5.2 安装体验

| 项 | 方案 |
|------|------|
| Android 安装提示 | 浏览器自动弹出（有 `beforeinstallprompt` 事件）|
| iOS 添加到主屏幕 | 需手动「分享 → 添加到主屏幕」|
| 启动屏 | 在 `public/` 放启动图（iOS 用 `<link rel="apple-touch-startup-image">`）|
| 状态栏 | `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">` |

**优化建议：** 监听 `beforeinstallprompt` 事件，在 app 内展示安装提示 banner（可关闭），而不是完全依赖浏览器默认弹窗。

```typescript
// 在 App.tsx 中
const [installPrompt, setInstallPrompt] =
  useState<BeforeInstallPromptEvent | null>(null)

useEffect(() => {
  const handler = (e: Event) => {
    e.preventDefault()
    setInstallPrompt(e as BeforeInstallPromptEvent)
  }
  window.addEventListener('beforeinstallprompt', handler)
  return () => window.removeEventListener('beforeinstallprompt', handler)
}, [])
```

### 5.3 iOS 特有注意

| 问题 | 说明 | 对策 |
|------|------|------|
| SW 注册延迟 | iOS Safari SW 注册时机不稳定 | SW 脚本体积尽量小 |
| 无 Push API | iOS 不支持 Web Push | 无解，放弃推送 |
| 传感器权限 | 需 `requestPermission()` | 流程化的权限请求 UI |
| 屏幕 notch | Safe Area 适配 | `env(safe-area-inset-*)` |
| 键盘弹出 | 键盘会遮挡输入 | 输入框用 `visualViewport` 适配 |
| 100vh 问题 | Safari 底部工具栏导致 | `100dvh` 代替 `100vh` |

**CSS 适配：**

```css
/* Safe area 适配 */
.panel {
  padding-top: env(safe-area-inset-top);
  padding-bottom: env(safe-area-inset-bottom);
  padding-left: env(safe-area-inset-left);
  padding-right: env(safe-area-inset-right);
}

/* 动态视口高度 */
.fullscreen {
  height: 100dvh;  /* 不是 100vh */
}
```

### 5.4 离线策略

| 资源类型 | 策略 | 说明 |
|---------|------|------|
| JS/CSS 构建产物 | `precache` | 构建时生成 hash，离线可用 |
| 字体（Google Fonts） | `CacheFirst` | 首次加载后永久缓存 |
| 外部图片 | `StaleWhileRevalidate` | 优先展示缓存，后台更新 |
| 便签数据 | `localStorage` | 非 SW 管理，由 app JS 直接读写 |

**关键约定：** PWA 下便签内容只存 `localStorage`。用户删除浏览器数据 = 丢失笔记。应在 UI 中醒目标注。

### 5.5 性能参考

| 指标 | 目标 | 工具 |
|------|------|------|
| FCP | < 1.5s | Lighthouse |
| LCP | < 2.5s | Chrome DevTools |
| TTI | < 3s | Web Vitals |
| 首屏 JS | < 100KB (gzip) | `vite-plugin-imagemin` + 代码分割 |
| 缓存命中 | > 90% | Workbox log |

---

## 6. 响应式 UI 规范

### 6.1 断点

```
手机:  < 640px
平板:  640 - 1024px
桌面:  > 1024px
```

现有代码使用 TailwindCSS。手机端优先用 `sm:` / `md:` 前缀适配。

### 6.2 布局原则

| 场景 | 桌面 | 手机 |
|------|------|------|
| 主面板 | 浮动毛玻璃窗 | 全屏，从底部弹出 |
| 侧边栏 | 左侧固定 | 从左侧滑入 overlay |
| 奇门结果 | 内嵌卡片 | 全宽卡片 + 全屏罗盘 |
| 导航 | Tab Bar | 底部 Tab Bar（iOS 风格） |

### 6.3 触控交互

| 交互 | 方案 |
|------|------|
| 滑动删除 | 待办列表支持左滑显示删除 |
| 下拉刷新 | `pull-to-refresh` 手势触发同步 |
| 长按菜单 | `contextmenu` 事件（移动端用 `touchstart` 计时器模拟）|
| 底部 Sheet | 用 `@radix-ui/react-dialog` 或在 `AcrylicPanel` 上加 transform 动画 |

### 6.4 字体与触摸目标

- 最小触摸目标：`44x44px`（Apple HIG）
- 最小字号：`11px`（实际 UI 中 9px-10px 的标签可接受，但可点击元素不小于 44px）
- 现有组件字号偏小（8-10px），桌面端因视距更远，手机端可适当放大到 12-14px

---

## 7. 实现路线图

### Phase 0 — Monorepo 拆分（3 天）

```
[ ] 根目录 npm workspaces + tsconfig.base.json
[ ] packages/shared/ 搭建（types, store, utils, i18n, hooks）
[ ] packages/desktop/ 搭建（迁移现有代码，依赖 shared）
[ ] packages/mobile/ 搭建（新建 Vite + React + PWA 项目）
[ ] 验证：桌面端 dev/build 正常、shared 包构建正常
```

### Phase 1 — PWA 基础（1 天，mobile 项目）

```
[✓] VitePWA 配置（vite-plugin-pwa + manifest + SW）
[✓] SW devOptions 启用
[✓] beforeinstallprompt 安装引导组件
[✓] iOS safe-area CSS + meta tags
[✓] 100dvh 替换（已有，mobile 新建时直接使用）
[ ] 响应式断点 + 触摸目标基线
```

### Phase 2 — 指南针（已完成，代码位于 desktop 项目）

```
[✓] useCompass hook（含 iOS 权限处理）
[✓] CompassWidget 组件（罗盘 UI + 降级逻辑）
[✓] 集成到 QimenPanel 条件渲染
[✓] 文案 + i18n
[ ] 可复用代码移入 shared → 两端共用
```

### Phase 3 — 养生模块（已决策：暂缓实施）

见 [第 4 章](#4-轻量养生中医) 的决策说明。

### Phase 4 — AI 混合策略（3-4 天）

```
[ ] RuleAIProvider 实现（移入 shared/utils/ai/rules.ts）
[ ] network.ts 网络状态感知（移入 shared/utils/ai/network.ts）
[ ] getAIProvider() 路由逻辑改造（shared）
[ ] mobile: AI 设置页 UI
[ ] mobile: 首次使用引导弹窗
[ ] 规则引擎输出标识
[ ] 测试连接功能
[ ] 单元测试
```

### Phase 5 — 手机端 UI 开发（独立进行）

```
[ ] MobileLayout 壳子（底部 Tab 栏 + 页面路由）
[ ] TodoList 移动端视图
[ ] NotesBrowser 移动端视图
[ ] NoteEditor 全屏模式
[ ] 侧边栏滑入 overlay
[ ] 触控交互（滑动删除、下拉刷新）
[ ] 设置页
[ ] 命理视图（含 CompassWidget）
```

---

## 附录

### A. 传感器 API 兼容性

参考：https://caniuse.com/deviceorientation

| API | Chrome Android | iOS Safari | 桌面 Chrome |
|-----|---------------|------------|-------------|
| `DeviceOrientationEvent` | ✅ 自动 | ✅ 需 permission | ❌ |
| `requestPermission()` | ❌ 不需 | ✅ iOS 13+ 必须 | ❌ |
| `webkitCompassHeading` | ❌ | ✅ iOS 独占 | ❌ |

### B. 判断环境的方法

```typescript
// 判断是否为 Electron
const isElectron = !!window.electronAPI

// 判断是否为 PWA / standalone mode
const isStandalone = window.matchMedia('(display-mode: standalone)').matches
  || (window.navigator as any).standalone === true

// 判断是否为移动端
const isMobile = !isElectron && (
  /iPhone|iPad|iPod|Android/i.test(navigator.userAgent)
  || (window.innerWidth < 640)
)
```

### C. 与后端无关

此项目无服务器端。所有数据存本地：

- `localStorage` → 便签 + 待办 + 设置
- `IndexedDB` → 可用但不必须（大数据场景如离线图片）
- 无用户账户、无同步、无后端 API

### D. 参考文档

- [PWA 清单 (web.dev)](https://web.dev/learn/pwa/)
- [DeviceOrientationEvent - MDN](https://developer.mozilla.org/en-US/docs/Web/API/DeviceOrientationEvent)
- [iOS Safari 特有行为](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)
- [vite-plugin-pwa 文档](https://vite-pwa-org.netlify.app/)
- [Monorepo 重构计划](monorepo-restructure.md)

---

## 8. Monorepo 结构

详细的重构步骤见 [monorepo-restructure.md](monorepo-restructure.md)。

### 目录树

```
desk-notes/
├── package.json                     # npm workspaces root
├── tsconfig.base.json
├── packages/
│   ├── shared/                      # 纯逻辑包
│   │   ├── src/
│   │   │   ├── types/               # 数据模型
│   │   │   ├── store/               # Zustand 状态（无 UI）
│   │   │   │   ├── useStore.ts
│   │   │   │   ├── undoManager.ts
│   │   │   │   └── types.ts         # PlatformAdapter 接口
│   │   │   ├── utils/               # 工具函数
│   │   │   │   ├── helpers.ts
│   │   │   │   ├── ai/              # AIProvider
│   │   │   │   │   ├── types.ts
│   │   │   │   │   ├── index.ts
│   │   │   │   │   ├── local.ts
│   │   │   │   │   └── rules.ts     # RuleAIProvider
│   │   │   │   ├── astrology/       # 命理
│   │   │   │   └── ...
│   │   │   ├── hooks/
│   │   │   │   └── useCompass.ts
│   │   │   └── i18n/
│   │   └── package.json
│   │
│   ├── desktop/                     # Electron 桌面端
│   │   ├── package.json
│   │   ├── vite.config.ts
│   │   ├── electron/
│   │   │   ├── main.cjs
│   │   │   └── preload.cjs
│   │   └── src/
│   │       ├── App.tsx
│   │       ├── main.tsx
│   │       ├── components/          # 桌面 UI（完全重写为 shared 引用）
│   │       │   ├── AcrylicPanel/
│   │       │   ├── FloatingNote/
│   │       │   ├── Todo/
│   │       │   └── Common/
│   │       └── adapters/            # 平台适配器
│   │           └── electronStorage.ts
│   │
│   └── mobile/                      # 手机端 PWA
│       ├── package.json
│       ├── vite.config.ts           # VitePWA 配置
│       ├── index.html               # 含 iOS meta tags
│       └── src/
│           ├── App.tsx              # MobileLayout 入口
│           ├── main.tsx
│           ├── components/
│           │   ├── Layout/          # 底部 Tab 栏 + 壳子
│           │   ├── Todo/
│           │   ├── Notes/
│           │   ├── Astrology/
│           │   └── Common/
│           └── adapters/
│               └── localStorage.ts
```

### 核心原则

| 原则 | 说明 |
|------|------|
| shared 无 UI | 不包含任何 React 组件代码，只含 hooks |
| shared 无平台 API | 不引用 `electronAPI`、`fs`、`localStorage`，通过 adapter 接口注入 |
| 桌面端不动 | 拆分期间桌面端保持可构建、可运行 |
| 手机端独立迭代 | 不依赖桌面端进度，独立构建部署 |
