#!/usr/bin/env bash
# ============================================================
# MONO 社区 · VPS 一键部署脚本
# 适用：Ubuntu 22.04 / 20.04、Debian 11 / 12（全新系统）
# 用法：scp 传到服务器后执行 → sudo bash deploy_vps.sh
#
# 做完的事：装依赖 → 配 GitHub 密钥 → 拉代码 → 装 Python 依赖
#          → 写 systemd 服务（开机自启+崩溃自动重启）→ 启动
# ============================================================
set -euo pipefail

APP_DIR=/opt/mono/app
VENV_DIR=/opt/mono/venv
REPO="git@github.com:wsy2026-ops/test6.git"
SERVICE_FILE=/etc/systemd/system/mono.service
# 对外端口（Nginx 监听）与应用端口（waitress 监听，仅回环）
# 架构：访客 → Nginx :80 → waitress 127.0.0.1:8080
PORT="${MONO_PORT:-80}"
APP_PORT="${MONO_APP_PORT:-8080}"
NGINX_CONF=/etc/nginx/sites-available/mono

say() { printf '\n\033[1;36m====> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m[注意] %s\033[0m\n' "$1"; }
die() { printf '\n\033[1;31m[失败] %s\033[0m\n' "$1"; exit 1; }

[ "$(id -u)" = "0" ] || die "请用 root 运行：sudo bash deploy_vps.sh"

# ------------------------------------------------------------
say "1/7 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git curl ca-certificates

# ------------------------------------------------------------
say "2/7 准备 GitHub 部署密钥"
mkdir -p /root/.ssh && chmod 700 /root/.ssh
if [ ! -f /root/.ssh/id_ed25519 ]; then
    ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519 -C "mono-vps" >/dev/null
    echo "  已生成新密钥"
else
    echo "  复用已有密钥"
fi
if [ ! -f /root/.ssh/config ] || ! grep -q "ssh.github.com" /root/.ssh/config; then
    cat > /root/.ssh/config <<'SSHCFG'
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  StrictHostKeyChecking no
SSHCFG
    chmod 600 /root/.ssh/config
fi

PUB_KEY="$(cat /root/.ssh/id_ed25519.pub)"
cat <<EOF

================================================================
 请把下面这行公钥加到 GitHub（只需一次）
 打开：https://github.com/settings/ssh/new
 Title 随便填（如 mono-vps），Key 粘贴下面这行，点 Add SSH key
================================================================

$PUB_KEY

================================================================
EOF
read -rp " 加好了就按回车继续（Ctrl+C 可中止）..." _

# ------------------------------------------------------------
say "3/7 拉取代码"
mkdir -p /opt/mono
if [ -d "$APP_DIR/.git" ]; then
    echo "  目录已存在，执行 git pull"
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO" "$APP_DIR"
fi

# ------------------------------------------------------------
say "4/7 创建虚拟环境并安装依赖（清华源，约 1-2 分钟）"
[ -x "$VENV_DIR/bin/python" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r "$APP_DIR/requirements-render.txt"
echo "  依赖安装完成"

# ------------------------------------------------------------
say "5/7 配置环境变量"
read -rp " Neon 连接串（用于异地快照备份，直接回车=不启用）: " NEON_DSN || true
SECRET_KEY="$(python3 -c 'import os;print(os.urandom(32).hex())')"

ENV_NEON=""
if [ -n "${NEON_DSN:-}" ]; then
    ENV_NEON="Environment=MONO_NEON_DSN=${NEON_DSN}"
    echo "  已启用异地快照备份"
else
    echo "  未启用异地快照（VPS 本地磁盘本身就是持久的，够用）"
fi

# ------------------------------------------------------------
say "6/7 写入 systemd 服务（开机自启 + 崩溃自动重启）"
{
    echo "[Unit]"
    echo "Description=MONO Community"
    echo "After=network-online.target"
    echo "Wants=network-online.target"
    echo ""
    echo "[Service]"
    echo "Type=simple"
    echo "WorkingDirectory=$APP_DIR"
    echo "Environment=PORT=$APP_PORT"
    # 只绑定回环：外部无法绕过 Nginx 直连应用端口
    echo "Environment=MONO_BIND=127.0.0.1"
    echo "Environment=MONO_SECRET_KEY=$SECRET_KEY"
    echo "Environment=MONO_SNAPSHOT_INTERVAL=120"
    [ -n "$ENV_NEON" ] && echo "$ENV_NEON"
    echo "ExecStart=$VENV_DIR/bin/python wsgi.py"
    echo "Restart=always"
    echo "RestartSec=5"
    echo "StandardOutput=append:$APP_DIR/server.log"
    echo "StandardError=append:$APP_DIR/server.log"
    echo ""
    echo "[Install]"
    echo "WantedBy=multi-user.target"
} > "$SERVICE_FILE"

# 放行端口（若启用了 ufw）
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "${PORT}/tcp" >/dev/null 2>&1 || true
    echo "  已放行 ufw 端口 $PORT"
fi

systemctl daemon-reload
systemctl enable mono >/dev/null 2>&1
systemctl restart mono

# ------------------------------------------------------------
say "6.5/7 配置 Nginx 反向代理（对外 $PORT → 应用 $APP_PORT）"
if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx >/dev/null 2>&1; then
    echo "  nginx 已安装"
else
    warn "nginx 安装失败，站点将只能通过 ${APP_PORT} 端口访问"
fi
if [ -f "$APP_DIR/nginx-mono.conf" ]; then
    cp "$APP_DIR/nginx-mono.conf" "$NGINX_CONF"
    rm -f /etc/nginx/sites-enabled/default
    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/mono
    if nginx -t >/dev/null 2>&1; then
        systemctl enable nginx >/dev/null 2>&1
        systemctl restart nginx
        echo "  nginx 已启动（80 → 127.0.0.1:${APP_PORT}）"
        # Nginx 前置后必须把代理层数设为 1，否则全站共用一个限流桶
        "$VENV_DIR/bin/python" "$APP_DIR/guard_config.py" proxy 1 >/dev/null 2>&1 || true
        systemctl restart mono
    else
        warn "nginx 配置校验失败，已跳过（应用仍在 ${APP_PORT} 端口）"
        nginx -t 2>&1 | tail -3
    fi
else
    warn "找不到 $APP_DIR/nginx-mono.conf，跳过 Nginx 配置"
fi

# ------------------------------------------------------------
say "7/7 等待服务启动并自检"
for i in $(seq 1 20); do
    sleep 2
    if curl -s -o /dev/null --max-time 4 "http://127.0.0.1:${PORT}/login"; then
        echo "  本地自检通过（第 $i 次尝试）"
        break
    fi
    if [ "$i" = "20" ]; then
        warn "本地自检未通过，最近日志："
        tail -25 "$APP_DIR/server.log" || true
        die "服务未起来，把上面的日志发出来即可定位"
    fi
done

PUBLIC_IP="$(curl -s --max-time 6 ifconfig.me || true)"
[ -n "$PUBLIC_IP" ] || PUBLIC_IP="<你的服务器IP>"

cat <<EOF

================================================================
 ✅ 部署完成
================================================================
 访问地址：http://${PUBLIC_IP}$([ "$PORT" = "80" ] && echo "" || echo ":${PORT}")

 常用命令：
   查看日志    systemctl status mono -l --no-pager
   实时日志    tail -f ${APP_DIR}/server.log
   重启服务    systemctl restart mono
   停止服务    systemctl stop mono
   更新代码    cd ${APP_DIR} && git pull && systemctl restart mono

 ⚠️ 若外网打不开，去云服务商控制台把 ${PORT} 端口加入安全组/防火墙放行
================================================================
EOF
