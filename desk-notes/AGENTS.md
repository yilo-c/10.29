# 思忆便签 (desk-notes)

思忆便签 = 轻量级浮动便签 + 待办管理。Monorepo 结构（shared + desktop + mobile）。

> **交互语言：中文** — 与本项目的所有交流默认使用中文，除非用户明确要求英文。

## 快速命令

```bash
npm run dev              # 桌面端 Vite 开发服务器（端口 5190）
npm run electron:dev     # 完整 Electron 开发（Vite + Electron）
npm run build            # 桌面端生产构建
npm run electron:build   # 构建 + electron-builder 打包
npm run test             # 运行所有测试
npm run test:watch       # 测试监听模式
npm run typecheck        # 全部包类型检查
npm run dev:mobile       # 手机端开发服务器（端口 5173）
```

## 启动流程

1. `npm run dev` 启动桌面端 Vite（纯浏览器模式，无 Electron 功能）
2. `npm run electron:dev` 启动完整桌面应用（先启动 Vite，再启动 Electron）
3. Electron 启动后通过 `packages/desktop/electron/main.cjs` 初始化

## 项目结构

```
desk-notes/
├── package.json                     # npm workspaces root
├── tsconfig.base.json               # 共享 TS 配置
├── packages/
│   ├── shared/                      # 纯逻辑包 — 两端共用
│   │   ├── src/
│   │   │   ├── types/               # 数据模型
│   │   │   ├── store/               # Zustand 核心状态（无平台依赖）
│   │   │   ├── utils/               # 工具函数（ai/, astrology/, helpers 等）
│   │   │   ├── hooks/               # 通用 hooks（useCompass）
│   │   │   └── i18n/                # 国际化
│   │   └── index.ts                 # 统一导出
│   │
│   ├── desktop/                     # 桌面端（Electron + React）
│   │   ├── electron/
│   │   │   ├── main.cjs             # Electron 主进程
│   │   │   └── preload.cjs          # contextBridge
│   │   ├── src/
│   │   │   ├── App.tsx              # 桌面入口
│   │   │   ├── components/          # 桌面 UI 组件
│   │   │   └── ...
│   │   ├── vite.config.ts
│   │   └── tailwind.config.js
│   │
│   └── mobile/                      # 手机端（PWA，全新 UI）
│       ├── src/
│       │   ├── App.tsx              # 手机端入口
│       │   └── ...
│       └── vite.config.ts
```

## 数据架构

```
UI 组件 → Zustand store → persist 中间件
  ├── 浏览器模式 → localStorage
  └── Electron 模式
        ├── store.json          ← todos, categories, 设置
        ├── notes/{id}.md       ← 每条便签独立文件
        ├── trash.json          ← 回收站
        └── assets/*            ← 嵌入图片
```

### Platform Adapter 模式

跨平台状态持久化通过注入模式实现：

```typescript
// shared/store/storage.ts  — 默认 localStorage，各端可覆盖
setPlatformStorage(customAdapter)

// shared/store/useStore.ts  — 可注入的 trash 处理
setTrashHandler((item) => { /* 桌面端写入 trash.json */ })
```

## 开发守则

1. **先看再改**：修改前先读目标文件，理解上下文
2. **纯逻辑放 shared**：types, utils, store 尽量放入 `packages/shared/`
3. **UI 层不跨端复用**：desktop 和 mobile 各自维护组件
4. **CSS**：桌面端 TailwindCSS，手机端纯 CSS（暂定）
5. **国际化**：`packages/shared/src/i18n/`
6. **图标**：修改 `packages/desktop/public/icon.svg` 后 `npm run generate-icons`
