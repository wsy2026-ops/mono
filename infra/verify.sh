#!/bin/bash
# MONO 部署后校验 —— 104：部署完自动验证，不靠人眼看。
#
# 用法：
#   bash infra/verify.sh              # 在服务器上跑
#   ssh mono-vps 'bash -s' < infra/verify.sh   # 从本地跑
#
# 退出码：0 = 全部通过；1 = 有失败项（可以直接拿来当 CI 门槛）
set -u
VPS_OK=1
FAIL=0

ok(){   printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad(){  printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }

echo "=============================================="
echo "  MONO 部署校验  $(date '+%F %T')"
echo "=============================================="

# 1) 关键接口探活（走本机，不依赖 Cloudflare）
echo "[1] 接口探活"
CODE=$(curl -s -o /tmp/_hz.json -w '%{http_code}' --max-time 10 http://127.0.0.1:8080/healthz 2>/dev/null)
if [ "$CODE" = "200" ]; then
    if grep -q '"db":true' /tmp/_hz.json 2>/dev/null; then ok "healthz 200 · db=true"; else bad "healthz 200 但 db 异常"; fi
else
    bad "healthz 返回 $CODE"
fi
rm -f /tmp/_hz.json

# 2) 服务与端口
echo "[2] 服务状态"
[ "$(systemctl is-active mono)" = "active" ] && ok "mono active" || bad "mono 不是 active"
ss -tln 2>/dev/null | grep -q '127.0.0.1:8080' && ok "端口 8080 已监听" || bad "端口 8080 没监听"

# 3) 数据一致性：库能读 + 关键表非空
echo "[3] 数据一致性"
PY=/opt/mono/venv/bin/python
if [ -x "$PY" ]; then
    OUT=$($PY - <<'PYEOF' 2>&1
import sqlite3, os
os.chdir('/opt/mono/app')
c = sqlite3.connect('data.db', timeout=5)
t = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
u = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
p = c.execute("SELECT COUNT(*) FROM posts WHERE COALESCE(is_deleted,0)=0").fetchone()[0]
print("%d %d %d" % (t, u, p))
PYEOF
)
    TABLES=$(echo "$OUT" | awk '{print $1}')
    USERS=$(echo "$OUT" | awk '{print $2}')
    POSTS=$(echo "$OUT" | awk '{print $3}')
    if [ -n "${TABLES:-}" ] && [ "$TABLES" -gt 100 ] 2>/dev/null; then ok "库可读 · $TABLES 张表"; else bad "库读取异常: $OUT"; fi
    [ "${USERS:-0}" -gt 0 ] 2>/dev/null && ok "users=$USERS" || bad "users 为 0（数据可能丢了）"
    [ "${POSTS:-0}" -ge 0 ] 2>/dev/null && ok "可见帖子=$POSTS" || bad "posts 查询失败"
else
    warn "找不到 $PY，跳过数据校验"
fi

# 4) 资源占用
echo "[4] 资源占用"
DISK=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
[ "$DISK" -lt 85 ] && ok "磁盘 ${DISK}%" || bad "磁盘 ${DISK}%（超过 85%）"
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
[ "$AVAIL" -gt 100 ] && ok "可用内存 ${AVAIL}MB" || bad "可用内存只有 ${AVAIL}MB"
LOAD=$(awk '{print $1}' /proc/loadavg)
NCPU=$(nproc)
ok "负载 ${LOAD} / ${NCPU} 核"

# 5) 日志轮转配置（2026-11-02 那次的教训：配置错会导致每天 00:00 重启）
echo "[5] 日志轮转"
ERRS=$(logrotate -d /etc/logrotate.conf 2>&1 | grep -c '^error' || true)
[ "${ERRS:-0}" = "0" ] && ok "logrotate 无配置错误" || bad "logrotate 有 $ERRS 条错误（配置错会跳文件，甚至杀服务）"
if grep -v '^[[:space:]]*#' /etc/logrotate.d/mono 2>/dev/null | grep -q 'kill -s HUP mono'; then
    bad "logrotate 里还有 'systemctl kill -s HUP mono' —— 每天 00:00 会把服务杀掉"
else
    ok "logrotate 没有 HUP mono（不会午夜自杀）"
fi

# 6) 定时任务
echo "[6] 定时任务"
CRON=$(crontab -l 2>/dev/null)
for k in backup_db watchdog.py daily_report.py; do
    echo "$CRON" | grep -q "$k" && ok "crontab: $k" || warn "crontab 缺 $k"
done

# cron 的 `>> 日志` 如果目录不存在，重定向直接失败 —— 整条命令一行都不执行，
# 而且只在 syslog 里留一句 "No such file or directory"，任务看起来"配了"其实从没跑过。
# 2026-11-02 就是栽在这：/opt/mono/logs 不存在，巡检上线后一次没执行。
# 注意 /etc/cron.d/* 是**另一套**调度，`crontab -l` 看不到，必须一起扫。
CRON_ALL="$CRON
$(cat /etc/cron.d/* 2>/dev/null)"
CDIRS=$(echo "$CRON_ALL" | grep -v '^[[:space:]]*#' | grep -o '>> *[^ ]*' | sed 's/>> *//' | while read -r f; do dirname "$f"; done | sort -u)
if [ -n "$CDIRS" ]; then
    BADDIR=0
    for d in $CDIRS; do
        if [ ! -d "$d" ]; then bad "cron 日志目录不存在：$d（任务其实从未执行）"; BADDIR=1; fi
    done
    [ "$BADDIR" = "0" ] && ok "cron 日志目录都存在（$(echo "$CDIRS" | wc -l | tr -d ' ') 个）"
fi

# 7) 引擎僵尸进程
echo "[7] 引擎进程"
Z=$(pgrep -c -f 'pikafish|katago|pbrain|egaroucid' 2>/dev/null | head -1)
Z=${Z:-0}
[ "$Z" -le 8 ] && ok "引擎进程 ${Z} 个" || warn "引擎进程 ${Z} 个，可能有僵尸"

echo "=============================================="
if [ "$FAIL" = "0" ]; then
    echo "  ✓ 全部通过"
    exit 0
else
    echo "  ✗ $FAIL 项失败"
    exit 1
fi
