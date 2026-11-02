# MONO 社区 · 云端镜像（Zeabur / 任意支持 Dockerfile 的平台通用）
#
# 两个关键点：
#   1. 依赖用 requirements-render.txt —— 不是给学校 Python 3.7 用的 requirements.txt
#      （后者没锁 Werkzeug 版本，新 Python 上会装 Werkzeug 3.x 把 flask 2.2.5 弄崩）
#   2. 端口固定 8080 —— Zeabur 默认探测端口；wsgi.py 会读 PORT 环境变量
  
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    MONO_SNAPSHOT_INTERVAL=120

WORKDIR /app

# 依赖单独一层，改代码时不必重装
COPY requirements-render.txt /app/requirements-render.txt
RUN pip install --no-cache-dir -r /app/requirements-render.txt

# 业务代码（.dockerignore 已排除 400MB+ 的 Windows 引擎与本地数据文件）
COPY . /app

EXPOSE 8080

# wsgi.py 做的事：读 PORT → 本地无库时从 Neon 恢复 → waitress 起服务 → 后台定期快照
CMD ["python", "wsgi.py"]
