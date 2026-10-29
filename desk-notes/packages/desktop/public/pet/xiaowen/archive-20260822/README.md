# 小问桌宠 v6 成果归档（2026-08-22）

> 百炼额度耗尽前产出的全部图片/视频成果快照。帧已抠图（绿幕 chroma key）为 256×256 透明 PNG。

## 目录

```
archive-20260822/
├── frames/             # 最终可用帧（256×256 透明）
│   ├── idle/                       v6 合成版待机（呼吸+眨眼，纯帧合成）
│   ├── idle_videoversion/          视频抽帧版待机（12帧，动作最自然，含循环段）
│   ├── idle_i1/                    眨眼插曲（4帧）
│   ├── click_defend/               单击受惊（8帧）
│   ├── shy/                        双击害羞（8帧）
│   ├── combo3/                     三连击剑势（10帧）
│   └── drag_fly/                   御剑飞行循环（8帧）
├── videos/             # happyhorse-1.1-i2v 原始视频（每动作一条，绿幕）
├── previews/           # 每个动作的 anim.gif + contact.jpg（可直接目检）
├── green_screens/       # wan2.7-image-pro 绿幕首帧
├── references/          # 角色参考图（ref1 分镜板 / ref2 立绘 / ref3 形象图）
└── scripts/             # 全部管线脚本（可复用，改 ACTIONS 表即可生成新动作）
```

## 各动作来源与状态

| 动作 | 帧数 | 生成方式 | 状态 |
|------|------|---------|------|
| idle | 6 | 绿幕逐帧合成（v6） | ✅ 已部署，用户已验收 |
| idle_videoversion | 12 | i2v 视频抽帧（自动循环段 [20,55]） | ✅ 用户评为"完美" |
| idle_i1 眨眼 | 4 | i2v | ✅ |
| click_defend | 8 | i2v | ✅ |
| shy | 8 | i2v | ✅ |
| combo3 | 10 | i2v | ✅ |
| drag_fly | 8 | i2v | ✅ |
| **land** | — | — | ❌ 批量中途额度耗尽，未产出 |
| combo5/chatOpen/notice/sleepy/startup | — | — | ⬜ 未开始（需恢复额度） |

## 待办（额度恢复后）

1. 补 land（用 drag_fly 的绿幕首帧，prompt 已备好）
2. 优化 prompt 后重出：嘴角自然度 / 细节清晰度（1080P、表情锁定、线稿锁定）
3. combo5/chatOpen/notice/sleepy/startup 四组

## 管线速记

- 绿幕首帧：`wan2.7-image-pro` + 纯绿 #00FF00 → OSS URL
- 视频：`happyhorse-1.1-i2v`（input.media 格式），720P/3s，轮询 task
- 抽帧：`extract_qc.py`（loop 动作自动找最优循环段）→ chroma key → 锚点归一 256
- 脚本入口：`scripts/batch_video.py`（改 ACTIONS 表即跑）
