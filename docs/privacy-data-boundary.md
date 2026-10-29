# 思忆集隐私与数据边界

> **与 complete-architecture-design.md 的关系：**
> 本文档是 [complete-architecture-design.md](complete-architecture-design.md)（v3.2，架构唯一真源）在隐私与数据边界维度的展开。
> 隐私分级已从旧版两级模型（default/private）升级为四级数据分类（仅本地/可同步/可分享/可送模型）。
> 隐私等级属于数据元属性，必须作为对象元数据持久化，不能只靠 UI 提示。

## 核心原则

**所有用户数据物理上不出用户个人电脑。** 同步链路中的每一跳都在用户自己的设备之间完成。

## 数据物理流

```
用户个人电脑 (Windows)
┌───────────────────────────────────────────────────────────────┐
│                                                                │
│  思忆集桌面端 ←→ 自动化枢纽 (automation-hub)                     │
│  (PyQt6 应用)     (FastAPI 本地服务，纯机械转发，零 AI)            │
│       │                                                       │
│  ┌────┴──────────────────────────────────────────────────┐     │
│  │  本地 AI (Ollama)                                      │     │
│  │  http://127.0.0.1:11434 ← 本地回环，物理上不出网卡       │     │
│  │  推理 100% 在本地 GPU/CPU 完成，数据不离开本机             │     │
│  │  模型权重一次下载后，无任何数据回传给厂商                   │     │
│  └─────────────────────────────────────────────────────────┘     │
│                          │                                       │
│                   同步管道 (加密)                                  │
│                          │                                       │
└──────────────────────┬───┘─────────────────────────────────────┘
                       │
             ┌─────────▼─────────┐
             │  用户手机 (Android) │
             │  (只读消费端)       │
             └───────────────────┘


云端 AI (DeepSeek / Moonshot)
  https://api.deepseek.com/v1
  https://api.moonshot.cn/v1
  ↑ 数据经互联网到达厂商服务器
  ↑ 仅限非隐私的复杂任务
```

- 自动化枢纽与思忆集桌面端运行在**同一台个人电脑**上
- 手机端通过局域网或用户自建通道拉取数据
- **没有任何用户数据经过第三方服务器或云端 API**

## 本地 AI 为什么安全（关键）

Ollama 在本地运行时，你的数据**物理上不会离开你的电脑**：

```
你发出请求 → http://127.0.0.1:11434
                    ↓
             本地回环地址
         (loopback, 127.0.0.0/8)
                    ↓
          数据包不经过任何网关、
          路由器或物理网卡
                    ↓
          Ollama 进程在内存中完成推理
                    ↓
         结果原路返回 → 全程在单机内
```

**不会泄露的证明：**
1. 请求地址 `127.0.0.1` 是本地回环——数据不出网卡
2. Ollama 没有"回传 prompt 给模型厂商"的机制
3. 唯一的网络流量是**初次拉模型时**从 registry 下载权重文件（一次性）
4. 思忆集调用 Ollama 不走互联网

**结论：本地 AI 可以安全地处理所有数据，包括 private 内容。**

## 隐私分类

### 四级数据分类

| 级别 | 标记 | 含义 | 示例 | 存储 | 传输 |
|------|------|------|------|------|------|
| **仅本地** | `local_only` | 不离开设备 | API 密钥、私密笔记、设备私钥、生物特征 | 本地加密 | 不传输 |
| **可同步** | `syncable` | 设备间加密同步 | 笔记、任务、单词本、非敏感记忆 | 本地 + 同步目录 | 加密传输 |
| **可分享** | `shareable` | 可在显式操作下分享或导出 | 公开笔记、项目文档 | 本地/服务端 | HTTPS |
| **可送模型** | `model_egress_allowed` | 可作为 LLM 上下文发送 | 偏好设置、脱敏摘要、非敏感查询 | 本地/服务端 | 厂商 API（仅限显式授权） |

### 隐私标记规则

1. 系统默认所有数据为 `syncable` 级别
2. 用户可手动将内容标记为 `local_only`（笔记级/文件夹级）
3. 以下内容自动标记为 `local_only`：
   - 位于标记为"私密"的文件夹/笔记本中的内容
   - 包含特定关键字的内容（可配置，如"日记"、"密码"等）
   - 用户通过设置指定的文件类型（如照片等）
4. 隐私等级是数据元属性，须作为对象元数据持久化在每个实体上
5. `syncable` 不等于 `model_egress_allowed`；未标记为 `model_egress_allowed` 的内容不允许默认送第三方模型

## AI 参与边界

### 本地 AI（Ollama）参与同步

| 环节 | 可参与 | 限制 |
|------|--------|------|
| 同步前分类整理 | ✅ | 所有级别均可 |
| 识别哪些内容需要同步 | ✅ | 所有级别均可，仅给出建议 |
| 内容摘要、打标签 | ✅ | 所有级别均可 |
| 冲突时辅助合并 | ✅ | 所有级别均可，需用户确认 |

### 云端 AI（DeepSeek/Moonshot）参与同步

| 场景 | 可参与 | 限制 |
|------|--------|------|
| 处理 `local_only` / `syncable` 内容 | ❌ 禁止 | 未获显式授权的内容不得经互联网送到第三方模型厂商 |
| 处理 `model_egress_allowed` 内容的复杂任务 | ⚠️ 可用但一般不需要 | 仅在本地 AI 判断自身能力不足时，升级到 Judge 走云端 |
| 同步管道本身 | ❌ 禁止 | 自动化枢纽纯机械转发，不调用任何云端 AI |

### 自动化枢纽的 AI 中立义务

自动化枢纽（automation-hub）在同步过程中**不调用任何 AI 模型**，只做：
- 数据存储与转发
- 机械冲突检测（版本号比对）
- 设备认证与授权
- 审计日志记录

## 隐私标记的实现指引

### 数据模型

```python
from enum import Enum

class PrivacyLevel(str, Enum):
    LOCAL_ONLY = "local_only"           # 仅本地，不同步，不外发
    SYNCABLE = "syncable"               # 可同步，不默认分享，不默认外发
    SHAREABLE = "shareable"             # 可在显式操作下分享
    MODEL_EGRESS_ALLOWED = "model_egress_allowed"  # 允许送第三方模型

@dataclass
class SyncEnvelope:
    # ... 现有字段 ...
    privacy_level: PrivacyLevel = PrivacyLevel.SYNCABLE  # 默认可同步

### 调用云端 AI 前的检查

```python
def _may_call_cloud_ai(envelope: SyncEnvelope) -> bool:
    """检查该数据是否可以发送到云端 AI。"""
    if envelope.privacy_level in (PrivacyLevel.LOCAL_ONLY, PrivacyLevel.SYNCABLE):
        return False  # 仅本地和可同步内容默认不上云
    if envelope.privacy_level == PrivacyLevel.SHAREABLE:
        return False  # 可分享不等于可送模型，需要显式授权
    # 只有 model_egress_allowed 才允许送第三方模型
    return envelope.privacy_level == PrivacyLevel.MODEL_EGRESS_ALLOWED
```

## 与现有文档的关系

- **安全模型总览**：本文档是 `security-model-overview.md` 在数据隐私维度的补充
- **跨设备同步规范**：本文档对 `cross-device-sync-spec.md` 增加了 AI 参与约束
- **同步协议设计**：本文档约束了同步管道的 AI 中立性

## 用户可见的隐私指示器

1. 设置页面显示"数据本地化"状态指示器（绿色 = 全部在本地）
2. 每个数据对象在 UI 上标注其隐私等级（local_only / syncable / shareable / model_egress_allowed）
3. 同步记录中标注哪些内容被 AI 处理过、哪些因隐私等级跳过
4. 每次 AI 参与同步操作时，在 UI 上显示"本地 AI 处理"标签
5. 调用云端 AI 时显示"数据将发送到 {厂商}"的提示
