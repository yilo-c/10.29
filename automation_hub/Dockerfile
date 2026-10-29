# 模块说明：应用镜像构建文件。
# 多阶段构建 - 构建阶段
FROM python:3.11-slim AS builder

# 安装构建所需的依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制并安装依赖，利用Docker构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# 多阶段构建 - 运行阶段
FROM python:3.11-slim

# 安装运行时所需的基本工具（保持精简）
# 说明：工具执行隔离（DockerExecutor）需要容器内存在 docker CLI。
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    docker-cli \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 创建专用用户和应用目录
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -m -s /bin/bash appuser && \
    mkdir -p /app && \
    chown appuser:appuser /app

# 从构建阶段复制已安装的Python包到全局运行时目录
COPY --from=builder /install /usr/local

# 复制应用代码到 /app/automation_hub/ 包目录
COPY . /app/automation_hub/
RUN chown -R appuser:appuser /app

# 设置 Python 模块搜索路径，使 automation_hub 包可导入
ENV PYTHONPATH=/app

# 创建数据目录并设置权限
RUN mkdir -p /data /data/runs /data/backups /data/reports /data/rss /data/logs /workspace && \
    chown -R appuser:appuser /data

# 设置工作目录为包根目录（alembic.ini 在此目录中）
WORKDIR /app/automation_hub

# 切换到非root用户运行
USER appuser

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 启动时通过 Alembic 初始化数据库，然后启动应用
CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn automation_hub.api.main:app --host 0.0.0.0 --port 8000"]