# 思忆集云部署指南

> 目标：将 Python 后端部署到云服务器，让手机端可以脱离桌面端独立运行。
> 手机端当前 41 个 RPC 中有 30 个需要后端 → 部署后全部可用。

---

## 一、架构

```
手机端 (Capacitor) ──WSS──▶ Cloud Server (VPS)
     │                          │
     │ WebSocket JSON-RPC       │ Python 后端
     │ port 443 (via Caddy)     │   headless_main.py
     │                          │   ws_server.py
     │                          │   ai/ (DeepSeek API)
     │                          │   memory/ (ChromaDB)
     │                          │   data/ (JSON 文件)
```

### 最低配置

| 配置 | 规格 |
|------|------|
| CPU | 1 核 (x86_64) |
| RAM | 1 GB |
| 硬盘 | 10 GB |
| 带宽 | 1 Mbps |
| 系统 | Ubuntu 22.04 / Debian 12 |
| 费用 | ~$5-10/月 |

### 推荐配置（含本地 AI）

| 配置 | 规格 |
|------|------|
| CPU | 4 核 |
| RAM | 8 GB |
| 硬盘 | 20 GB |
| GPU | 可选（本地跑 Ollama） |

---

## 二、部署步骤

### 2.1 购买服务器

推荐服务商：

| 服务商 | 最低月费 | 特点 |
|--------|---------|------|
| 阿里云 ECS | ~¥34 | 国内速度快 |
| 腾讯云 Lighthouse | ~¥30 | 国内速度快 |
| Vultr | $6 | 海外，无需备案 |
| Hetzner | €4 | 性价比最高 |

> **国内服务器**需域名备案。如果不方便备案，选海外服务器。

### 2.2 服务器初始化

```bash
# SSH 登录
ssh root@你的服务器IP

# 更新系统
apt update && apt upgrade -y

# 安装基础依赖
apt install -y python3.11 python3.11-venv python3.11-dev git curl

# 安装 Caddy（自动 HTTPS 反向代理）
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

### 2.3 部署后端代码

```bash
# 创建应用目录
mkdir -p /opt/siyi
cd /opt/siyi

# 克隆仓库（或手动上传思忆集app/ 目录）
git clone https://你的仓库地址.git
# 或者用 scp 从本地传输：
# scp -r 思忆集app root@服务器IP:/opt/siyi/

cd 思忆集app

# 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 安装核心依赖
pip install -r requirements-desktop.txt
```

### 2.4 配置环境变量

```bash
cat > /opt/siyi/思忆集app/.env << 'EOF'
# 必须配置
DEEPSEEK_API_KEY=sk-你的DeepSeekKey
SIYI_WS_HOST=127.0.0.1
SIYI_WS_PORT=18765
SIYI_DATA_DIR=/opt/siyi/data

# 可选配置
SIYI_DEBUG=0
SIYI_MAX_FILE_SIZE=52428800
EOF
```

### 2.5 配置反向代理（Caddy → WebSocket WSS）

```bash
cat > /etc/caddy/Caddyfile << 'EOF'
siyi.你的域名.com {
    # 自动 HTTPS（Caddy 自动申请证书）
    reverse_proxy 127.0.0.1:18765 {
        # WebSocket 支持
        header_up Upgrade {http.request.header.Upgrade}
        header_up Connection {http.request.header.Connection}
    }
}
EOF

# 重载 Caddy
systemctl reload caddy
```

> 如果没有域名，也可以用 IP + 自签名证书。但手机端 WebView 对自签名证书比较严格，**建议用域名**。

### 2.6 配置 systemd 服务（自动启动 + 崩溃恢复）

```bash
cat > /etc/systemd/system/siyi-backend.service << 'EOF'
[Unit]
Description=思忆集 Backend
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/siyi/思忆集app
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/siyi/思忆集app/.venv/bin/python -m backend.headless_main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 启用并启动
systemctl daemon-reload
systemctl enable siyi-backend
systemctl start siyi-backend

# 查看状态
systemctl status siyi-backend

# 查看日志
journalctl -u siyi-backend -f
```

### 2.7 验证部署

```bash
# 检查后端是否运行
curl http://127.0.0.1:18765/health
# 应返回: {"status": "ok"}

# 测试 WebSocket（需要 wscat）
# npm install -g wscat
# wscat -c ws://127.0.0.1:18765/ws
```

---

## 三、手机端连接配置

### 方案 A：编译时硬编码（推荐）

修改 `思忆集手机端app/src/lib/noteAttachment.ts` 中的连接地址：

```typescript
// 在 uploadNoteAttachment 和 downloadAttachment 中
const WS_URL = 'wss://siyi.你的域名.com/ws'
```

或者在 Capacitor 配置中添加：

```typescript
// capacitor.config.ts
server: {
  url: 'https://siyi.你的域名.com',  // 手机端加载时连接到此地址
  androidScheme: 'https',
  allowNavigation: ['你的域名.com'],
}
```

### 方案 B：运行时切换（更灵活）

在手机端设置页增加"后端地址"输入框，用户可自由切换 LAN/Cloud：

```typescript
// 在设置页保存后端地址
localStorage.setItem('siyi-backend-url', 'wss://siyi.你的域名.com/ws')

// 在 WebSocket 客户端读取
const url = localStorage.getItem('siyi-backend-url') || 'ws://192.168.1.5:18765/ws'
```

---

## 四、数据迁移

### 从本地桌面端迁移到云

```bash
# 1. 在桌面端运行的后端数据目录找到数据
# 桌面端: 思忆集app/data/

# 2. 压缩数据
cd /d/h/3.25/思忆集/思忆集app
tar czf siyi-data-backup.tar.gz data/

# 3. 上传到云服务器
scp siyi-data-backup.tar.gz root@服务器IP:/opt/siyi/

# 4. 在服务器上解压
cd /opt/siyi
tar xzf siyi-data-backup.tar.gz -C /opt/siyi/思忆集app/
```

### 定期备份

```bash
# 每日凌晨 3 点备份
crontab -e
# 添加:
0 3 * * * cd /opt/siyi && tar czf /backups/siyi-$(date +\%Y\%m\%d).tar.gz data/
```

---

## 五、安全注意事项

| 项目 | 说明 |
|------|------|
| **HTTPS/WSS** | Caddy 自动管理证书，手机端必须通过 WSS 连接 |
| **API Key** | `DEEPSEEK_API_KEY` 存在服务端 .env，手机端不持有 |
| **认证令牌** | 设置 `SIYI_TRANSFER_KEY` 防止未授权访问 |
| **防火墙** | 只放行 443 (HTTPS) 和 22 (SSH) 端口 |
| **SSRF 防护** | 已完成 ✅ |
| **文件上传** | 已限流/防穿越 ✅ |

```bash
# 防火墙配置
ufw allow 22/tcp
ufw allow 443/tcp
ufw deny 18765  # 后端端口不对外暴露，只走 Caddy 代理
ufw enable
```

---

## 六、各服务商实测费用

| 服务商 | 配置 | 月费 | 延迟 (国内) | 适合 |
|--------|------|------|-----------|------|
| 阿里云 ECS | 2C2G | ¥34 | 低 | 主力 |
| 腾讯云 Lighthouse | 2C2G | ¥30 | 低 | 主力 |
| Vultr | 1C1G | $6 | 中 | 轻量 |
| Hetzner CX22 | 2C4G | €4.5 | 高 | 性价比 |
| Oracle Cloud (免费) | 4C24G | 免费 | 中 | ❌ 注册困难 |

### 推荐

**国内用户 → 阿里云 ECS 2C2G (¥34/月)**  
**海外用户 → Hetzner CX22 (€4.5/月)** 或 Vultr ($6/月)

---

## 七、手机端独立后的能力变化

| 功能 | 之前（依赖桌面端） | 之后（云部署） |
|------|:--------------:|:------------:|
| AI 对话 | 桌面端后端 | ✅ 云端 |
| OCR 识别 | 桌面端后端 | ✅ 云端 |
| 词库/单词 | 桌面端后端 | ✅ 云端 |
| 附件存储 | 桌面端后端 | ✅ 云端 |
| 设备同步 | LAN + 桌面端 | ❌ 仍需设计 |
| 桌面端 UI | 可用 | 可用（同为客户端） |

**部署后，手机端和桌面端都成为云端后端的客户端。两者可以同时使用，数据共享，互不依赖。**
