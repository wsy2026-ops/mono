#!/usr/bin/env bash
# =============================================================
# MONO 一键推送 + 服务器同步
# 用法：双击「更新线上.cmd」，或在 Git Bash 里运行 bash sync_server.sh
# 流程：提交本地改动 -> 推送 GitHub -> 打包 -> 上传 VPS -> 重启 -> 健康检查
# 依赖：ssh/scp（Windows 自带）、密钥 mono-vps（已配置免密）
# =============================================================
set -u
cd "$(dirname "$0")" || exit 1

VPS_ALIAS="mono-vps"          # ~/.ssh/config 中的主机别名
APP_DIR="/opt/mono/app"
# 健康检查走域名（经 Cloudflare）。
# ⚠️ 不要改回 IP：源站已限制为只接受 Cloudflare 流量，直连 IP 会被防火墙挡掉。
# 2026-11-01：主域名换成 monocraft.top（旧域名 mono-craft.de5.net 只留跳转页，
# 拿它做健康检查等于「旧域名还活着」而不是「新站正常」）。
SITE_URL="https://monocraft.top"

echo "=============================================="
echo "  MONO 一键更新  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ---- 0) 版本号自动进位（可用 MONO_SKIP_BUMP=1 跳过） ----
if [ "${MONO_SKIP_BUMP:-0}" != "1" ]; then
    NEW_VER="$(python bump_version.py 2>/dev/null || python3 bump_version.py 2>/dev/null)"
    [ -n "$NEW_VER" ] && echo "  版本号进位: v$NEW_VER"
fi

# ---- 1) 提交本地改动（有变化才提交） ----
echo "[1/5] 检查本地改动..."
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    git add -A
    git commit -q -m "sync: 站点更新 $(date '+%Y-%m-%d %H:%M')" && echo "  已提交: $(git log --oneline -1)"
else
    echo "  无未提交改动"
fi

# ---- 2) 推送 GitHub（失败不阻塞同步） ----
echo "[2/5] 推送 GitHub..."
if GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15" git push origin main 2>&1 | tail -1; then
    echo "  GitHub 推送 OK"
else
    echo "  (!) GitHub 推送失败（网络波动），继续同步服务器"
fi

# ---- 3) 打包代码（排除引擎/数据/密钥） ----
echo "[3/5] 打包代码..."
TAR_FILE="$(mktemp -u)/../mono_sync_$$.tar"
TAR_FILE="C:/Users/weiha/AppData/Local/Temp/mono_sync_$$.tar"
git archive -o "$TAR_FILE" HEAD -- . \
    ':(exclude)egaroucid' ':(exclude)gnugo' ':(exclude)katago' \
    ':(exclude)pikafish' ':(exclude)rapfi' ':(exclude)stockfish' \
    ':(exclude)data.db' ':(exclude)neon_dsn.txt' ':(exclude)data_backups' \
    ':(exclude)backups' ':(exclude)logs' \
    ':(exclude)uploads' ':(exclude)music' ':(exclude)drive' ':(exclude)oj_data' ':(exclude)static/avatars' ':(exclude)static/feedback' ':(exclude)_old_guns' ':(exclude)_trash_wip' ':(exclude)server.log' \
    ':(exclude)*.log' ':(exclude)__pycache__' >/dev/null 2>&1
if [ ! -f "$TAR_FILE" ]; then
    echo "  (!) git archive 失败，改用工作区打包"
    tar -cf "$TAR_FILE" --exclude='.git' --exclude='egaroucid' --exclude='gnugo' \
        --exclude='katago' --exclude='pikafish' --exclude='rapfi' --exclude='stockfish' \
        --exclude='data.db' --exclude='neon_dsn.txt' --exclude='data_backups' \
        --exclude='backups' --exclude='logs' \
        --exclude='uploads' --exclude='music' --exclude='drive' --exclude='oj_data' --exclude='static/avatars' --exclude='static/feedback' --exclude='_old_guns' --exclude='_trash_wip' --exclude='*.log' --exclude='__pycache__' .
fi
echo "  包大小: $(du -h "$TAR_FILE" | cut -f1)"

# ---- 4) 上传 VPS 并应用 + 重启 ----
# 这台机器的线路很抖，scp/ssh 会间歇性报 Connection closed，一律重试
echo "[4/5] 上传服务器并重启..."
UP_OK=0
for try in 1 2 3 4 5; do
    if scp -q -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$TAR_FILE" "$VPS_ALIAS:/tmp/mono_sync.tar" 2>/dev/null; then
        UP_OK=1
        [ "$try" -gt 1 ] && echo "  第 $try 次上传成功"
        break
    fi
    echo "  第 $try 次上传失败，3 秒后重试…"
    sleep 3
done
if [ "$UP_OK" != "1" ]; then
    echo "  (!) 上传连续失败 5 次，放弃"
    echo "      手动执行: scp \"$TAR_FILE\" $VPS_ALIAS:/tmp/ && ssh $VPS_ALIAS 'tar -xf /tmp/mono_sync.tar -C $APP_DIR && systemctl restart mono'"
    rm -f "$TAR_FILE"; exit 1
fi

STATE=""
for try in 1 2 3 4 5; do
    STATE=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$VPS_ALIAS" \
        "tar -xf /tmp/mono_sync.tar -C $APP_DIR && rm -f /tmp/mono_sync.tar && cd $APP_DIR && bash precompress.sh > /tmp/mono_gz.log 2>&1; \
         mkdir -p /opt/mono/oj; [ -e /opt/mono/oj/problems ] || ln -s $APP_DIR/ojweb/oj_data/problems /opt/mono/oj/problems; [ -L /opt/mono/oj/problems ] && ln -sfn $APP_DIR/ojweb/oj_data/problems /opt/mono/oj/problems; \
         echo \"[oj] \$(ls /opt/mono/oj/problems 2>/dev/null | wc -l) problems visible to judge\"; \
         systemctl restart mono && sleep 4 && systemctl is-active mono" 2>&1 | tail -1)
    if [ "$STATE" = "active" ]; then break; fi
    echo "  第 $try 次应用失败（$STATE），3 秒后重试…"
    sleep 3
done
if [ "$STATE" = "active" ]; then
    echo "  服务器已更新并重启（mono active）"
else
    echo "  (!) 服务状态异常: $STATE"
    echo "      查日志: ssh $VPS_ALIAS tail -30 $APP_DIR/server.log"
    rm -f "$TAR_FILE"; exit 1
fi
rm -f "$TAR_FILE"

# ---- 5) 健康检查 ----
echo "[5/5] 健康检查..."
for i in 1 2 3 4 5; do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --noproxy '*' "$SITE_URL/login" 2>/dev/null)
    if [ "$CODE" = "200" ]; then
        echo "=============================================="
        echo "  ✓ 更新完成，线上正常: $SITE_URL"
        echo "=============================================="
        exit 0
    fi
    sleep 3
done
echo "  (!) 健康检查未通过 (HTTP $CODE)，查日志: ssh $VPS_ALIAS tail -30 $APP_DIR/server.log"
exit 1
