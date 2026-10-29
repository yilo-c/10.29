# 手机端 APK 打包指南

> 2026-10-29

浏览器能看 UI 效果，装了会连不上。先满足 `127.0.0.1:5173` 这个比较常见的开发模式；仓库里 `capacitor.config.ts` 的 `androidScheme: 'http'` 我**没有**改动，只在必要处补最小兼容。

---

## 一、为什么不能在手机本机构建

| 依赖 | 手机 proot 环境 | 是否可补 |
|---|---|---|
| Java JDK 17+ | 无，apt 源里也没有 openjdk | 可解压二进制约 180 MB |
| Android SDK (compileSdk 36) | 无 | 平台包 100–200 MB，整套 1–2 GB |
| Gradle | 无 | 需下载发行版 |
| 内存峰值 | 仅剩 2.5 GB 可用 | **不可解**，Gradle+AGP 8.13 峰值 3–4 GB |
| 存储 | 已用 91% | 勉强够但很紧张 |

**结论：在电脑上构建。** 手机内存不足是硬约束，装什么工具都绕不过去。

---

## 二、电脑打包步骤

### 前置检查

```bash
java -version     # 需要 17 或 21（工程 kotlinOptions.jvmTarget = "21"）
```

没有 JDK 就先装 [Temurin 17](https://adoptium.net/)，或直接装 Android Studio（自带 JDK + SDK）。

### 构建

```bash
cd 思忆集手机端app

# 1. 装依赖
npm install

# 2. 前端构建（产物进 dist/）
npm run build

# 3. 同步到 Android 工程（把 dist/ 拷进 android/app/src/main/assets/public）
npx cap sync android

# 4. 打 debug 包
cd android
./gradlew assembleDebug
```

**产物**：`android/app/build/outputs/apk/debug/app-debug.apk`

传到手机点击安装即可。debug 包用默认 debug key 签名，不需要额外配置 keystore。

### 第一次构建可能遇到的问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `SDK location not found` | 没设 SDK 路径 | 在 `android/local.properties` 写 `sdk.dir=/path/to/Android/Sdk` |
| 卡在 `Downloading gradle-8.x` | 网络慢 | 用国内镜像，改 `android/gradle/wrapper/gradle-wrapper.properties` 的 `distributionUrl` |
| 依赖下载失败 | `google()` / `mavenCentral()` 慢 | 在 `build.gradle` 的 repositories 前加阿里云镜像 |
| `compileSdk 36` 找不到 | SDK 未装该版本 | Android Studio → SDK Manager → 勾选 API 36 |

---

## 三、只验证 UI 的话，不用打 APK

手机浏览器直接开：

```
http://127.0.0.1:5173
```

前提是在手机上跑 `npx vite --host 127.0.0.1 --port 5173`（proot 与宿主共享网络命名空间，宿主侧已验证可连通该端口）。

**能验证**：布局、导航栏高度、AI 中央按钮、工具页、聊天隐私提示
**不能验证**：无障碍读屏、悬浮球、本地通知、生物识别等原生能力（浏览器里没有对应 API）

---

## 四、改动清单（相对 GitHub 上的版本）

| 文件 | 性质 |
|---|---|
| `src/styles/layout.css` | 新增，布局层 |
| `src/App.tsx` | 重写骨架 + 导航栏 |
| `src/components/features/tools/ToolsView.tsx` | 新增，原缺失 |
| `src/components/ui/ErrorBoundary.tsx` | 新增，原缺失 |
| `src/lib/bridges/{info,cron,agent,audit}Bridge.ts` | 新增，原缺失 |
| `src/lib/bridges/index.ts` | 命名空间导出，避同名冲突 |
| `src/lib/chatOrchestrator.ts` | 新增，AI 编排 |
| `src/lib/aiRouter.ts` | 修正 SYNCABLE 语义 |
| `src/store/mobileStore.ts` | sendMessage 接入编排器 |
| `src/components/features/chat/ChatView.tsx` | 实时隐私提示 |
| `src/index.css` | 引入 layout.css |
| `desk-notes/packages/shared/src/types/index.ts` | 补 editMode 字段 |

验证记录：`tsc --noEmit` 0 错误、`vitest run` 137/137 通过、`vite build` 成功。
