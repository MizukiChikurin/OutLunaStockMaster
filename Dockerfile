# 多阶段构建，减小最终镜像体积
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装编译依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY outluna ./outluna

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[all]"

# 运行阶段
FROM python:3.11-slim

WORKDIR /app

# 复制已安装的 Python 包和项目代码
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

# 创建数据目录
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV OUTLUNA_CACHE_DIR=/app/data/cache
ENV OUTLUNA_REPORT_DIR=/app/data/reports
ENV OUTLUNA_DB_PATH=/app/data/outluna.db

EXPOSE 8080

CMD ["python", "-m", "outluna.cli"]
