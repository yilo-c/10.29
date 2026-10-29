# 思忆便签 — Monorepo 重构计划

> 目标：将现有单项目拆分为 monorepo（`shared` + `desktop` + `mobile`），
> 实现桌面端（Electron）和手机端（PWA）独立开发、共享核心逻辑。

---

## 1. 目标结构

```
desk-notes/
├── package.json                # npm workspaces root
├── tsconfig.base.json          # 共享 TS 配置
├── packages/
│   ├── shared/                 # 纯逻辑包 — 两端共用
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── src/
│   │       ├── types/          # 数据模型（FloatingNote, TodoItem 等）
│   │       ├── store/          # Zustand store（纯状态层，无 UI）
│   │       ├── utils/          # 工具函数
│   │       │   ├── helpers.ts
│   │       │   ├── ai/         # AIProvider 接口 + LocalAIProvider
│   │       │   ├── astrology/  # 八字/奇门/风水
│   │       │   ├── fileStore.ts
│   │       │   ├── backup.ts
│   │       │   ├── export.ts
│   │       │   ├── trash.ts
│   │       │   └── ...
│   │       ├── hooks/          # 通用 hooks（useCompass 等）
│   │       └── i18n/           # 国际化文案
│   │
│   ├── desktop/                # 现有桌面端（Electron + React）
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── electron/           # Electron 主进程
│   │   ├── vite.config.ts
│   │   └── src/
│   │       ├── App.tsx         # 桌面入口
│   │       ├── main.tsx
│   │       ├── components/     # 桌面 UI 组件（几乎全部重写为 shared 引用）
│   │       │   ├── AcrylicPanel/
│   │       │   ├── FloatingNote/
│   │       │   ├── Todo/
│   │       │   ├── Common/
│   │       │   └── ...
│   │       └── index.css
│   │
│   └── mobile/                 # 手机端（PWA，新建项目）
│       ├── package.json
│       ├── tsconfig.json
│       ├── vite.config.ts      # 含 VitePWA 配置
│       ├── index.html
│       └── src/
│           ├── App.tsx         # 手机端入口（全新设计）
│           ├── main.tsx
│           ├── components/     # 手机 UI 组件
│           │   ├── Layout/     # 底部 Tab 栏、布局壳
│           │   ├── Todo/       # 待办视图
│           │   ├── Notes/      # 笔记视图
│           │   ├── Astrology/  # 命理视图（含 CompassWidget）
│           │   └── Common/     # 通用 UI
│           └── index.css
```

---

## 2. shared 包范围

### 2.1 全部移入 shared（100% 复用）

```
packages/shared/src/
├── types/
│   ├── index.ts              # FloatingNote, TodoItem, AIConfig 等
│   └── electron.ts           # ElectronAPI 类型声明（仅供 desktop 引用）
├── i18n/
│   ├── index.ts              # useTranslation hook（纯逻辑，无 UI）
│   ├── zh-CN.ts
│   └── en.ts
├── utils/
│   ├── helpers.ts            # uid, stripHtml, formatRelativeTime 等
│   ├── ai/
│   │   ├── types.ts          # AIProvider interface, AIAction
│   │   ├── index.ts          # getAIProvider(), streamAI()
│   │   ├── local.ts          # LocalAIProvider
│   │   └── rules.ts          # RuleAIProvider（手机端离线降级）
│   ├── astrology/
│   │   ├── index.ts
│   │   ├── types.ts
│   │   ├── bazi.ts
│   │   ├── huangli.ts
│   │   ├── fengshui.ts
│   │   ├── guidance.ts
│   │   ├── qimenSuggest.ts
│   │   ├── weather.ts
│   │   └── ai-guidance.ts
│   ├── wikiLinks.ts
│   ├── customTheme.ts
│   ├── pluginSystem.ts
│   └── ...其他纯逻辑 util
├── store/
│   ├── useStore.ts           # Zustand store（不含 persist 适配器，两端各自配置）
│   ├── undoManager.ts
│   ├── contentEditTracker.ts
│   └── helpers.ts            # getDescendantFolderIds 等
├── hooks/
│   └── useCompass.ts         # 手机端专用，但桌面端可安全返回 null
└── index.ts                  # 统一导出
```

### 2.2 留在各端独有

| 文件 | 归属 | 原因 |
|------|------|------|
| `electron/main.cjs` | desktop | Electron 主进程 |
| `electron/preload.cjs` | desktop | contextBridge |
| `utils/fileStore.ts` | **desktop** | 依赖 electronAPI |
| `utils/backup.ts` | **desktop** | 依赖文件系统 |
| `utils/export.ts` | **desktop** | 依赖文件系统 |
| `utils/importer.ts` | **desktop** | 依赖文件系统 |
| `utils/trash.ts` | **desktop** | 依赖文件系统 |
| `utils/imageStore.ts` | **desktop** | 依赖文件系统 |
| `utils/reminder.ts` | **desktop** | 依赖 Electron Notification |
| `utils/noteHistory.ts` | **shared** | 纯逻辑，可复用 |
| `utils/templates.ts` | **shared** | 纯数据，可复用 |
| `components/` 全部 | 各自 | UI 完全不同 |

### 2.3 store 拆分策略

Zustand store 需要拆分为两层：

```
packages/shared/src/store/
├── useStore.ts          # 核心状态 + action（两端共用）
├── undoManager.ts       # 撤销栈
├── contentEditTracker.ts # 内容变更追踪
└── types.ts             # StoreState 接口

desktop: 加 persist 中间件 + electron fileStore 适配器
mobile:  加 persist 中间件 + localStorage 适配器
```

核心 store 中与 Electron 耦合的部分（如 `loadNotesFromDisk`）需抽取为 platform adapter：

```typescript
// shared/store/types.ts
export interface PlatformAdapter {
  loadNotes(): Promise<FloatingNote[]>
  saveNote(note: FloatingNote): Promise<void>
  deleteNote(noteId: string): Promise<void>
}
```

---

## 3. 迁移步骤

### Phase 1 — 搭建 monorepo 骨架（预计 1 天）

```
[ ] 1. 根目录 package.json → npm workspaces
[ ] 2. tsconfig.base.json → 共享 TS 配置
[ ] 3. packages/shared/ 初始化
[ ] 4. packages/desktop/ 初始化（迁移现有代码）
[ ] 5. packages/mobile/ 初始化（从零搭建 Vite + React + PWA）
[ ] 6. 验证：桌面端 dev/build 正常
```

### Phase 2 — 抽取 shared 包（预计 2 天）

```
[ ] 1. 移入 types/ → 更新所有 import 路径
[ ] 2. 移入 i18n/  → 更新 import 路径
[ ] 3. 移入 utils/（纯逻辑部分）→ 更新 import
[ ] 4. 移入 store/（核心状态）→ 拆分平台适配器
[ ] 5. 移入 hooks/useCompass.ts
[ ] 6. 桌面端引用 shared 包，验证构建通过
[ ] 7. 测试全部通过
```

### Phase 3 — 手机端开发（独立进行，不影响桌面端）

```
见 docs/mobile-guide.md → 重新基于 monorepo 结构
```

---

## 4. 构建配置

### 根目录 package.json

```json
{
  "name": "desk-notes",
  "private": true,
  "workspaces": ["packages/*"],
  "scripts": {
    "dev:desktop": "npm run dev -w packages/desktop",
    "dev:mobile": "npm run dev -w packages/mobile",
    "build:shared": "npm run build -w packages/shared",
    "build:desktop": "npm run build -w packages/desktop",
    "build:mobile": "npm run build -w packages/mobile",
    "test": "npm run test -w packages/shared"
  }
}
```

### shared 包构建

shared 包编译为 CommonJS + ESM 双格式，使用 `tsup` 或 `tsc`：

```json
{
  "name": "@desk-notes/shared",
  "main": "dist/index.js",
  "module": "dist/index.mjs",
  "types": "dist/index.d.ts",
  "scripts": {
    "build": "tsup src/index.ts --format cjs,esm --dts"
  }
}
```

### desktop 端引用 shared

```json
{
  "name": "@desk-notes/desktop",
  "dependencies": {
    "@desk-notes/shared": "*"
  }
}
```

desktop 的 Vite 配置中，shared 包通过 workspace 协议直接引用源码（无需每次 build shared）：

```typescript
// packages/desktop/vite.config.ts
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@shared': path.resolve(__dirname, '../shared/src'),
    },
  },
})
```

---

## 5. 共享的 store — 平台适配器模式

当前 `useStore.ts` 中与平台强相关的操作：

| Action | 桌面实现 | 手机实现 |
|--------|---------|---------|
| `loadNotesFromDisk` | 读取 `.md` 文件 | 从 localStorage 读取 |
| `addFloatingNote` | 写入 `.md` 文件 | 写入 localStorage |
| `removeFloatingNote` | 删除 `.md` 文件 | 从 localStorage 删除 |

抽取为 adapter 后，store 不再直接依赖平台 API：

```typescript
// shared/store/types.ts
export interface StorageAdapter {
  readAllNotes(): Promise<Record<string, unknown>[]>
  writeNote(id: string, data: Record<string, unknown>): Promise<void>
  deleteNote(id: string): Promise<void>
  readAllTodos(): Promise<Record<string, unknown>[]>
  writeTodos(data: Record<string, unknown>[]): Promise<void>
}

// shared/store/useStore.ts
// action 通过 adapter 操作持久化
loadNotes: async () => {
  const adapter = getStorageAdapter()  // 由各端在初始化时注册
  const rawNotes = await adapter.readAllNotes()
  // ... 反序列化 + setState
}
```

**桌面端注册：**

```typescript
// packages/desktop/src/main.tsx
import { registerStorageAdapter } from '@desk-notes/shared'
import { ElectronStorageAdapter } from './adapters/electronStorage'

registerStorageAdapter(new ElectronStorageAdapter())
```

**手机端注册：**

```typescript
// packages/mobile/src/main.tsx
import { registerStorageAdapter } from '@desk-notes/shared'
import { LocalStorageAdapter } from './adapters/localStorage'

registerStorageAdapter(new LocalStorageAdapter())
```

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 桌面端构建中断 | 高 | Phase 1-2 每一步都验证 `npm run build:desktop` |
| shared 包抽离引入循环依赖 | 中 | shared 不引用 desktop/mobile 任何代码；用 interface 而非具体类型 |
| store persist 配置在两端不同 | 低 | persist 中间件在各端入口文件配置，不在 shared 中 |
| 手机端开发周期长 | — | 手机端独立迭代，不影响桌面端日常使用 |
| 桌面端已有功能退化 | 高 | 所有迁移步骤伴随测试验证（`npm run test`） |

---

## 7. 时间估算

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 1 | monorepo 骨架 | 1 天 |
| Phase 2 | shared 包抽取 | 2 天 |
| Phase 3 | 手机端开发 | 独立进行 |
| 总计 | 拆分完成可开始手机端开发 | 3 天 |

> 注：Phase 1-2 期间桌面端功能不受影响，可以正常使用。
> 手机端开发从 Phase 3 开始，与桌面端完全独立迭代。
