# 小问桌宠项目设计与实施文档

本文档描述“思忆集”桌面端小问桌宠的最终技术路线、视觉资产规格、状态机、台词系统、工程接入方式、分阶段实施计划与验收标准。

当前目标不是临时做一个能动的头像，而是把小问做成可维护、可扩展、可验收的桌宠子系统。后续任何资源生成、代码改造和交互扩展，原则上都应以本文档为准。

## 1. 项目定位

小问是思忆集桌面端的常驻轻量助手形象，服务两个层面：

1. 视觉陪伴：以桌宠方式出现在桌面，具备可爱、克制、有辨识度的存在感。
2. 功能入口：作为聊天窗口、通知、剪贴板感知、主动建议等能力的轻入口。
3. 情绪反馈：通过动作和台词表达应用状态，例如待机、思考、提醒、拖拽、打开聊天。
4. 品牌记忆点：让“思忆集的小问”区别于通用 AI 助手头像。

小问的人设定稿如下：

```text
角色名：小问
门派：一梦江湖 · 华山派
性格标签：冷飒外表 · 傲娇内核 · 剑痴 · 迷糊小师妹
视觉锚点：黑剑不离身 · 灰蓝高马尾 · 冰蓝剑意光效 · 白色云气
```

## 2. 当前工程状态

当前桌宠相关代码位于：

```text
packages/desktop/src/FloatingBubble/
```

当前桌宠资源位于：

```text
packages/desktop/public/pet/
```

现有资源：

```text
packages/desktop/public/pet/
├── xiaowen_fullbody.png
├── sprite_idle.png
├── ChatGPT Image 2026年5月18日 04_23_24.png
└── ChatGPT Image 2026年5月18日 04_59_20.png
```

现有资源含义：

| 文件 | 当前用途 | 备注 |
| --- | --- | --- |
| `xiaowen_fullbody.png` | 小问角色身份基准图 | 可作为所有后续分帧生成的 canonical reference |
| `sprite_idle.png` | 当前 fallback 小尺寸待机图 | 可临时保证桌宠不空白 |
| `ChatGPT Image 2026年5月18日 04_23_24.png` | 待机、眨眼、挥剑、轻跳分镜参考 | 不能直接作为运行素材 |
| `ChatGPT Image 2026年5月18日 04_59_20.png` | 点击、双击、拖拽飞行分镜参考 | 不能直接作为运行素材 |

当前主要组件：

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `FloatingOrbApp.tsx` | 当前桌宠窗口入口 | Electron 独立桌宠窗口渲染入口 |
| `PetXiaoWenAvatar.tsx` | 当前启用的小问渲染组件 | 负责 Canvas 绘制、点击、拖拽、粒子效果 |
| `xiaowenDialogue.ts` | 已新增台词配置 | 存放小问事件台词 |
| `usePetState.ts` | 通用 mood 状态 | 当前仅有 `idle / thinking / happy / alert` |
| `PetAvatar.tsx`、`PetBoneAvatar.tsx`、`PetCanvasAvatar.tsx`、`PetLayerAvatar.tsx` | 实验实现 | 暂不作为最终路线 |

当前核心问题：

1. `PetXiaoWenAvatar.tsx` 原本会加载 `/pet/xiaowen/idle_01.png` 等 21 张序列帧，但这些资源目前不存在。
2. 当前资源是“角色图 + 分镜说明图”，还不是桌宠运行所需的透明逐帧 PNG。
3. 代码中存在多套桌宠渲染尝试，后续需要收敛到一个正式实现。
4. 台词、人设、动作状态之间需要建立稳定映射，不能散落在组件事件里。

## 3. 最终技术路线

最终采用：

```text
透明 PNG 分帧资源
+ Canvas 2D 渲染
+ 事件驱动状态机
+ 台词气泡
+ Electron 透明悬浮窗口
```

不采用以下路线作为正式主线：

1. 不采用 Live2D：成本高，资产制作链路复杂，当前需求不需要。
2. 不采用纯程序化绘制：角色一致性和精致度较难保证。
3. 不采用多套渲染器长期并存：会增加维护成本和行为不确定性。
4. 不直接裁切 ChatGPT 分镜大图运行：黑底、编号、对白框、分组排版都会污染运行效果。

选择 PNG 分帧的原因：

1. 小问是强角色形象，最重要的是脸、发型、服饰、黑剑、比例稳定。
2. 分镜图已经明确了动作方向，适合进一步拆成逐帧资源。
3. Canvas 播放序列帧对 200-300px 桌宠足够轻量。
4. 后续新增动作时只需补资源和配置，不需要重写动画引擎。
5. 更容易做视觉验收：每个动作都可以看 contact sheet 和 GIF 预览。

## 4. 目标文件结构

建议最终资源结构如下：

```text
packages/desktop/public/pet/xiaowen/
├── manifest.json
├── references/
│   ├── xiaowen_fullbody.png
│   ├── storyboard_idle.png
│   └── storyboard_interactions.png
└── frames/
    ├── idle/
    │   ├── idle_01.png
    │   ├── idle_02.png
    │   ├── idle_03.png
    │   └── idle_04.png
    ├── click_defend/
    │   ├── click_defend_01.png
    │   └── ...
    ├── shy/
    ├── drag_fly/
    ├── land/
    ├── combo3/
    ├── combo5/
    ├── chat_open/
    ├── notice/
    ├── sleepy/
    └── startup/
```

建议最终代码结构如下：

```text
packages/desktop/src/FloatingBubble/
├── FloatingOrbApp.tsx
├── XiaoWenPetAvatar.tsx
├── useXiaoWenPetMachine.ts
├── xiaowenClips.ts
├── xiaowenDialogue.ts
├── xiaowenTypes.ts
└── xiaowenRender.ts
```

当前可以先不重命名 `PetXiaoWenAvatar.tsx`，但完整分帧版稳定后，建议把正式实现命名为 `XiaoWenPetAvatar.tsx`，并把旧实验组件移入：

```text
packages/desktop/src/FloatingBubble/experimental/
```

## 5. 资源规格

### 5.1 单帧规格

正式分帧资源必须满足：

| 项目 | 规格 |
| --- | --- |
| 文件格式 | PNG |
| 背景 | 透明 |
| 推荐画布 | `256 x 256` |
| 安全边距 | 上下左右至少 `16px` |
| 角色视觉高度 | `180-220px` |
| 锚点 | 脚底、剑尖、中心点尽量稳定 |
| 色彩 | 保持灰蓝头发、浅蓝衣袖、黑剑、冰蓝特效 |
| 阴影 | 不建议内置柔和投影；投影交给 Canvas |
| 文字 | 禁止出现文字、编号、对白框 |
| 背景 | 禁止黑底、白底、棋盘格 |

### 5.2 命名规范

资源文件名使用小写英文和下划线：

```text
<action>_<frame_number>.png
```

示例：

```text
idle_01.png
idle_02.png
click_defend_01.png
drag_fly_04.png
combo5_12.png
```

禁止：

```text
小问待机1.png
ChatGPT Image xxx.png
frame-final-new-new.png
```

### 5.3 动画动作与建议帧数

| 动作 | 建议帧数 | 是否循环 | 说明 |
| --- | ---: | --- | --- |
| `idle` | 4-6 | 是 | 抱剑、呼吸、头发/衣摆微动 |
| `clickDefend` | 5-7 | 否 | 受惊收肩，反手握剑鞘 |
| `shy` | 5-7 | 否 | 袖子遮脸，露一只眼 |
| `dragFly` | 6-8 | 是 | 踩剑飞行，蓝白拖尾 |
| `land` | 4-6 | 否 | 急停落地，轻微踉跄 |
| `combo3` | 7-9 | 否 | 横剑摆 pose，剑意小爆发 |
| `combo5` | 10-14 | 否 | 横劈、上挑、收鞘三连 |
| `chatOpen` | 4-6 | 可循环 | 盘腿悬空打坐，剑横膝上 |
| `notice` | 4-5 | 否 | 侧耳听，马尾一抖 |
| `sleepy` | 5-7 | 否 | 伸懒腰、揉眼、打哈欠 |
| `startup` | 6-8 | 否 | 云端落地，小问报到 |

### 5.4 分镜图使用方式

`ChatGPT Image 2026年5月18日 04_23_24.png` 和 `ChatGPT Image 2026年5月18日 04_59_20.png` 只作为动作参考图，不作为最终运行素材。

正确用法：

1. 参考其中角色姿态、动作节奏和表情。
2. 以 `xiaowen_fullbody.png` 锁定角色身份。
3. 重新生成或绘制独立透明帧。
4. 每个动作单独导出为透明 PNG 帧序列。

错误用法：

1. 直接把大图放进 Canvas。
2. 从黑底大图粗暴裁切导致黑边、编号、对白框残留。
3. 每个动作使用不同画风或不同角色比例。

## 6. 状态模型

正式动作状态建议定义为：

```ts
export type XiaoWenAction =
  | 'idle'
  | 'clickDefend'
  | 'shy'
  | 'dragFly'
  | 'land'
  | 'combo3'
  | 'combo5'
  | 'chatOpen'
  | 'notice'
  | 'sleepy'
  | 'startup'
```

### 6.1 状态优先级

桌宠事件可能同时发生，因此需要优先级。

建议优先级从高到低：

| 优先级 | 状态 | 说明 |
| ---: | --- | --- |
| 1 | `dragFly` | 用户正在拖拽，最高优先级 |
| 2 | `combo5` | 强交互反馈 |
| 3 | `combo3` | 强交互反馈 |
| 4 | `clickDefend` / `shy` | 点击反馈 |
| 5 | `chatOpen` | 打开聊天时触发 |
| 6 | `notice` | 通知、剪贴板、提醒 |
| 7 | `sleepy` | 夜间或长时间未交互 |
| 8 | `startup` | 应用启动时一次性播放 |
| 9 | `idle` | 默认状态 |

### 6.2 状态转换

基本转换：

```text
startup -> idle
idle + click -> clickDefend -> idle
idle + doubleClick -> shy -> idle
idle + dragStart -> dragFly
dragFly + dragEnd -> land -> idle
idle + combo3 -> combo3 -> idle
idle + combo5 -> combo5 -> idle
idle + chatOpen -> chatOpen -> idle 或保持 chatOpen
idle + notification -> notice -> idle
idle + nightMode -> sleepy -> idle
```

### 6.3 连击规则

推荐规则：

```text
900ms 内连续点击计入同一轮连击
第 1 次：clickDefend
第 2 次：可保持 clickDefend 或触发 shy
第 3 次：combo3
第 5 次：combo5
超过 900ms 未继续点击：连击计数清零
拖拽开始：连击计数清零
```

### 6.4 拖拽规则

推荐规则：

```text
mousedown 记录起点
移动距离超过 8px 视为拖拽
拖拽开始触发 dragFly
拖拽过程中循环 dragFly
mouseup 触发 land
land 播放结束回 idle
```

Electron 独立窗口拖拽目前由主进程 `dragStart / dragEnd` 配合实现。后续如果要做更细腻的“御剑飞行”，可以让 renderer 保留拖拽动作状态，main process 只负责窗口移动。

## 7. 台词系统

台词应集中存放在：

```text
packages/desktop/src/FloatingBubble/xiaowenDialogue.ts
```

禁止把台词散落在点击事件、Canvas 渲染循环或 Electron main process 中。

### 7.1 台词配置

当前建议结构：

```ts
export const XIAOWEN_DIALOGUES: Record<XiaoWenAction, string[]> = {
  idle: [
    '剑在人在……呼……',
    '此剑随我八百年……哦不是，八年。',
    '华山剑法第三式……咦，第三式是什么来着？',
  ],
  clickDefend: ['干嘛！——哦，是你啊。'],
  shy: ['戳一下两下的……当心我用剑气挠你痒痒哦！'],
  dragFly: ['喂喂，我自己会走……', '华山轻功都被你浪费了。'],
  land: ['到了——唔，落地有点歪。'],
  combo3: ['再三挑衅——华山剑法第一式·疏狂剑意！……看招！'],
  combo5: ['千山冷落凌云道——！一生疏狂——！剑并箫！……哼！'],
  chatOpen: ['你聊你的，我练我的。——诶等等，你说的那个我也有兴趣。'],
  notice: ['嗯？有风声。'],
  sleepy: ['亥时了……华山弟子也是要睡觉的……哈啊~~'],
  startup: ['小问报到——今天也是剑意满满的一天！'],
}
```

### 7.2 台词显示规则

建议：

1. 气泡默认显示 2.4-4 秒。
2. 长台词可显示 4-5 秒。
3. `idle` 台词不要过于频繁，建议 18-45 秒随机一次。
4. 点击类台词可以立即覆盖 idle 台词。
5. 拖拽中不应反复刷新台词，避免闪烁。
6. 气泡不应超出窗口边界，必要时扩大桌宠窗口或调整气泡位置。

### 7.3 台词气质约束

小问台词要遵守：

1. 偏短，适合气泡阅读。
2. 保留冷飒外表和傲娇反差。
3. 不要过度卖萌。
4. 不要大量解释功能。
5. 不要把桌宠变成说明书。
6. 古风表达要轻，不要压过现代助手身份。

## 8. 渲染设计

### 8.1 Canvas 职责

Canvas 只负责：

1. 清屏。
2. 根据当前 action 选择当前帧。
3. 绘制角色帧。
4. 添加轻量附加效果，如阴影、呼吸偏移、少量粒子。
5. 根据 devicePixelRatio 适配高清屏。

Canvas 不应负责：

1. 决定业务状态。
2. 保存台词文本。
3. 直接访问 Zustand 主 store。
4. 与 Electron 主进程通信。
5. 管理复杂业务事件。

### 8.2 图片加载

建议建立帧资源预加载器：

```ts
type LoadedFrames = Record<XiaoWenAction, HTMLImageElement[]>
```

预加载策略：

1. 启动时优先加载 `idle`、`clickDefend`、`dragFly`。
2. 后台继续加载其他动作。
3. 未加载完成时使用 `idle` 或 `xiaowen_fullbody.png` fallback。
4. 图片加载失败要记录 warning，但不让桌宠空白。

### 8.3 帧播放

每个 clip 需要配置：

```ts
interface XiaoWenClip {
  action: XiaoWenAction
  frames: string[]
  fps: number
  loop: boolean
  next?: XiaoWenAction
}
```

示例：

```ts
export const XIAOWEN_CLIPS: Record<XiaoWenAction, XiaoWenClip> = {
  idle: {
    action: 'idle',
    frames: [
      '/pet/xiaowen/frames/idle/idle_01.png',
      '/pet/xiaowen/frames/idle/idle_02.png',
      '/pet/xiaowen/frames/idle/idle_03.png',
      '/pet/xiaowen/frames/idle/idle_04.png',
    ],
    fps: 6,
    loop: true,
  },
  clickDefend: {
    action: 'clickDefend',
    frames: [
      '/pet/xiaowen/frames/click_defend/click_defend_01.png',
      '/pet/xiaowen/frames/click_defend/click_defend_02.png',
    ],
    fps: 12,
    loop: false,
    next: 'idle',
  },
}
```

### 8.4 视觉效果边界

允许：

1. 轻微呼吸上下浮动。
2. 轻微阴影。
3. 冰蓝剑意小粒子。
4. 拖拽时少量蓝白拖尾。
5. 落地时少量白色云气。

避免：

1. 大面积光晕。
2. 模糊拖影。
3. 黑底残留。
4. 大量爱心、星星等偏离华山剑客气质的效果。
5. 粒子超出桌宠窗口后被裁切得很明显。

## 9. Electron 窗口设计

当前桌宠窗口由 `FloatingOrbApp.tsx` 渲染，Electron 主进程创建独立透明 `BrowserWindow`。

推荐尺寸：

```text
最小：240 x 260
推荐：280 x 300
如果气泡在窗口内：320 x 340
如果气泡拆到单独窗口：桌宠窗口可保持 220 x 240
```

当前优先方案：

```text
桌宠 + 台词气泡放在同一个透明窗口内
```

优点：

1. 实现简单。
2. 拖拽时气泡跟随角色。
3. 不需要维护第二个气泡窗口。

缺点：

1. 气泡需要占用窗口空间。
2. 透明窗口 hitArea 需要覆盖气泡与角色。

长期可选方案：

```text
桌宠窗口 + 气泡窗口 分离
```

只有当气泡经常被裁切、遮挡或影响点击穿透时，再考虑拆分。

## 10. 工程接入边界

### 10.1 Renderer 层

Renderer 负责：

1. 播放动画。
2. 展示气泡。
3. 捕获点击、双击、拖拽。
4. 根据后端事件切换小问状态。

### 10.2 Main Process 层

Electron main process 负责：

1. 创建透明桌宠窗口。
2. always-on-top。
3. skipTaskbar。
4. 窗口拖拽。
5. hitArea / click-through。
6. 打开聊天窗口。

Main process 不负责：

1. 绘制桌宠。
2. 选择台词。
3. 播放动画帧。
4. 处理小问人设逻辑。

### 10.3 Shared 层

暂时不建议把桌宠逻辑放入 `packages/shared`。

原因：

1. 桌宠是桌面端专属 UI。
2. 分帧资源也在 desktop public 下。
3. mobile 不应复用该 UI。

只有在未来 mobile 也需要“小问助手人格台词”时，可以把纯文本台词或人格 prompt 抽到 shared。

## 11. 实施阶段

### 阶段 0：止血稳定

目标：桌宠不空白，有小问形象，有基础台词。

任务：

1. 当前 `PetXiaoWenAvatar.tsx` 缺帧时 fallback 到 `sprite_idle.png` 或 `xiaowen_fullbody.png`。
2. 加入 `xiaowenDialogue.ts`。
3. 点击、拖拽、连击时显示对应台词。
4. 桌宠窗口尺寸能容纳气泡。

验收：

1. `npm run typecheck:desktop` 通过。
2. `npm run electron:dev` 能看到小问。
3. 缺少 `/pet/xiaowen/*.png` 时不空白。
4. 单击、双击、拖拽至少有可见反馈。

### 阶段 1：资源结构落地

目标：建立正式资源目录和 manifest。

任务：

1. 创建 `packages/desktop/public/pet/xiaowen/`。
2. 移入 reference 图。
3. 创建 `frames/` 目录。
4. 创建 `manifest.json`。
5. 建立 `xiaowenClips.ts`，不要在组件里硬编码路径。

验收：

1. 资源路径统一。
2. 组件从 clip 配置加载图片。
3. 旧资源仍可 fallback。

### 阶段 2：第一批正式分帧

目标：先做最小可用高质量动作集。

优先动作：

```text
idle
clickDefend
shy
dragFly
land
```

原因：

1. 覆盖用户最常见交互。
2. 可以快速判断桌宠观感是否成立。
3. 不会一开始陷入高成本 combo 动作。

验收：

1. 每个动作有透明 PNG 帧。
2. 同一动作内比例稳定。
3. 动作切换不跳脸、不跳比例。
4. 拖拽时飞行动作可循环。

### 阶段 3：增强动作

目标：补齐人设高光和彩蛋。

动作：

```text
combo3
combo5
chatOpen
notice
sleepy
startup
```

验收：

1. combo5 有明显横劈、上挑、收鞘节奏。
2. chatOpen 能表现“盘腿悬空打坐，剑横膝上”。
3. notice 有马尾/耳朵方向的轻反应。
4. sleepy 不破坏冷飒人设，偏轻微可爱。
5. startup 只播放一次，不反复打断用户。

### 阶段 4：收敛代码

目标：移除多套实验实现带来的混乱。

任务：

1. 正式组件命名为 `XiaoWenPetAvatar.tsx`。
2. 状态机提取为 `useXiaoWenPetMachine.ts`。
3. 渲染辅助提取为 `xiaowenRender.ts`。
4. 旧组件移入 `experimental/` 或删除。
5. 保留一个清晰入口：`FloatingOrbApp.tsx -> XiaoWenPetAvatar`。

验收：

1. `rg "PetBoneAvatar|PetLayerAvatar|PetCanvasAvatar"` 不应出现在正式入口中。
2. 新人能从 `FloatingOrbApp.tsx` 顺藤摸瓜读懂桌宠系统。
3. 台词、资源、状态机、渲染职责清晰分离。

### 阶段 5：验收与打磨

目标：把桌宠从“能跑”打磨到“可长期使用”。

验收项：

1. 桌宠不遮挡主功能核心按钮。
2. 透明区域点击穿透符合预期。
3. 拖拽流畅。
4. 气泡不裁切。
5. 长时间运行无明显内存增长。
6. 所有图片加载失败都有 fallback。
7. Electron 打包后资源路径仍正确。

## 12. QA 清单

### 12.1 视觉 QA

每次新增分帧资源后检查：

1. 是否透明背景。
2. 是否有黑边、白边、残留编号。
3. 是否有文本或气泡残留。
4. 角色脸是否一致。
5. 发型是否一致。
6. 黑剑是否始终存在，除非动作明确不展示。
7. 冰蓝特效是否克制。
8. 帧间是否有大小突变。
9. 脚底或中心锚点是否稳定。
10. 动作循环首尾是否自然。

### 12.2 工程 QA

每次改动后至少运行：

```bash
npm run typecheck:desktop
```

如果改了 Electron 主进程或窗口逻辑，再运行：

```bash
npm run electron:dev
```

如果改了资源路径或构建配置，再运行：

```bash
npm run build
```

### 12.3 交互 QA

检查：

1. 启动后是否播放 startup。
2. 待机是否循环 idle。
3. 单击是否触发 clickDefend。
4. 双击是否触发 shy。
5. 三连击是否触发 combo3。
6. 五连击是否触发 combo5。
7. 拖拽是否触发 dragFly。
8. 放开拖拽是否触发 land。
9. 打开聊天是否触发 chatOpen。
10. 收到通知或剪贴板事件是否触发 notice。
11. 夜间模式是否触发 sleepy。

## 13. 资源生成提示词建议

生成分帧时，应使用简洁、状态明确、 sprite 生产导向的提示。

通用身份锁定：

```text
小问，Q版中国女剑士，灰蓝高马尾，浅蓝白华山派服饰，黑色长剑不离身，冰蓝剑意点缀，白色云气，冷飒但可爱的傲娇小师妹气质。保持与参考图一致的脸型、发型、服饰、配色、黑剑和比例。透明背景，单角色完整身体，适合 256x256 桌宠 sprite，禁止文字、编号、对白框、背景、阴影地面。
```

idle：

```text
待机动作，怀中抱剑或剑在身侧，轻微呼吸，头发和衣摆微动，偶尔眨眼。4-6 frame sprite sequence, transparent background, consistent scale and anchor.
```

clickDefend：

```text
单击受惊反应，小问收肩，反手握住剑鞘，看清来人后表情放松，傲娇但安心。5-7 frame sprite sequence, transparent background.
```

shy：

```text
双击害羞反应，小问用宽袖遮住半张脸，只露一只眼，脸颊微红，黑剑仍在身边。5-7 frame sprite sequence, transparent background.
```

dragFly：

```text
御剑飞行，小问踩在黑剑上向右前方飞行，灰蓝高马尾和衣摆被风吹起，少量贴近角色的蓝白剑气拖尾。6-8 frame looping sprite sequence, transparent background.
```

land：

```text
急停落地，小问从剑上跳下，落地略微踉跄但很快站稳，表情有一点迷糊。4-6 frame sprite sequence, transparent background.
```

combo5：

```text
三连剑招，小问横劈、上挑、收鞘，动作帅气，冰蓝剑意沿黑剑贴身出现，不要大面积光效。10-14 frame sprite sequence, transparent background.
```

## 14. 风险与注意事项

### 14.1 资源一致性风险

AI 生成多动作分帧时最容易出现：

1. 发型变了。
2. 剑变形或消失。
3. 衣服花纹变化。
4. 头身比跳变。
5. 表情风格不一致。

应对：

1. 始终使用 `xiaowen_fullbody.png` 作为身份基准。
2. 每个动作生成后做 contact sheet 检查。
3. 不合格动作单独返工，不要硬接入。

### 14.2 透明背景风险

常见问题：

1. 黑底没有去干净。
2. 白色云气和白底混在一起。
3. 半透明光效导致边缘脏。
4. 阴影残留。

应对：

1. 生成时要求透明背景或纯色可抠背景。
2. 尽量避免柔和光晕。
3. 后处理检查 alpha。
4. 不接受带背景的运行资源。

### 14.3 Electron 点击穿透风险

如果 hitArea 太大：

1. 用户点不到桌宠背后的窗口。
2. 透明区域也拦截鼠标。

如果 hitArea 太小：

1. 气泡或角色局部无法点击/拖拽。
2. 拖拽不稳定。

当前建议：

1. 阶段 0 先用较大 hitArea 保证能交互。
2. 资源稳定后再根据角色实际 bounds 缩小 hitArea。
3. 如气泡常驻，hitArea 应覆盖气泡，否则气泡可设置 `pointer-events: none`。

## 15. 最终验收定义

完整分帧资源版完成时，应满足：

1. 小问启动后能稳定显示，不依赖 fallback 单图。
2. 所有核心动作都有独立透明帧。
3. 台词与动作一一对应。
4. 点击、双击、拖拽、连击、聊天打开、通知触发都有效。
5. 桌宠窗口透明、置顶、可拖拽。
6. 不出现黑底、编号、对白框、裁切、空白帧。
7. `npm run typecheck:desktop` 通过。
8. `npm run electron:dev` 下实机体验可用。
9. 旧实验组件不再参与正式入口。
10. 后续新增动作只需要补资源和 clip 配置，不需要改主渲染逻辑。

## 16. 下一步建议

建议下一步按这个顺序执行：

1. 创建正式资源目录 `packages/desktop/public/pet/xiaowen/frames/`。
2. 把现有三张参考图移动或复制到 `references/`。
3. 写 `xiaowenClips.ts`，把图片路径从组件中抽离。
4. 先制作 `idle`、`clickDefend`、`shy`、`dragFly`、`land` 五组分帧。
5. 接入分帧播放，保留当前 fallback。
6. Electron 实机验收。
7. 再补 `combo3`、`combo5`、`chatOpen`、`notice`、`sleepy`、`startup`。

这套顺序的好处是：先把桌宠变成稳定、可测试的工程系统，再逐步提升动画资产质量，避免一开始被完整美术资源卡住。
