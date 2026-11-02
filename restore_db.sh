#!/bin/bash
# ============================================================
# MONO 社区 · 数据库恢复脚本
#
# 为什么需要：备份一直在跑，但出事时没有现成流程 —— 那种时候最容易手忙脚乱。
#
# 用法：
#   bash restore_db.sh --list          只列出可用备份（不改动任何东西）
#   bash restore_db.sh                 用最新本地备份恢复
#   bash restore_db.sh <备份文件>      用指定备份恢复
#   bash restore_db.sh --from-neon     从 Neon 异地快照恢复
#
# 安全措施：
#   - 恢复前必须输入 yes 确认
#   - 自动把当前库另存为 data.db.pre-restore-<时间>（可回退）
#   - 恢复前对备份做 integrity_check，不通过直接中止
#   - 恢复后探活，失败时打印回退命令
# ============================================================
set -e

APP_DIR="${MONO_APP_DIR:-/opt/mono/app}"
VENV_PY="${MONO_VENV_PY:-/opt/mono/venv/bin/python}"
DB="$APP_DIR/data.db"
BACKUP_DIR="$APP_DIR/backups"
SERVICE="${MONO_SERVICE:-mono}"
SITE="${MONO_SITE_URL:-http://127.0.0.1:8080}"

c_red() { printf '\033[31m%s\033[0m\n' "$1"; }
c_grn() { printf '\033[32m%s\033[0m\n' "$1"; }
c_yel() { printf '\033[33m%s\033[0m\n' "$1"; }

[ -x "$VENV_PY" ] || { c_red "找不到 Python：$VENV_PY"; exit 1; }

# Neon DSN 通常只写在 systemd 的 Environment 里，SSH 会话里读不到 ——
# 不补这一步，--from-neon 会在真正需要它的时候报「未配置 DSN」。
if [ -z "$MONO_NEON_DSN" ]; then
  # 注意两点：
  #   1. grep 不能加 ^ —— systemd 会因为 DSN 里的 & 和 ? 给它套上双引号，
  #      输出是 "MONO_NEON_DSN=postgresql://...，开头是个引号
  #   2. 末尾的引号要用 tr -d '"' 去掉
  _dsn=$(systemctl show "$SERVICE" -p Environment 2>/dev/null \
         | tr ' ' '\n' | grep 'MONO_NEON_DSN=' | head -1 | cut -d= -f2- | tr -d '"')
  if [ -n "$_dsn" ]; then
    export MONO_NEON_DSN="$_dsn"
  fi
fi

# ---------- --list ----------
if [ "$1" = "--list" ]; then
  echo "本地备份（$BACKUP_DIR）："
  # 注意：ls -lh 的 $5 已是人类可读（如 680K），不要再除以 1024
  ls -lht "$BACKUP_DIR"/*.db 2>/dev/null \
    | awk '{printf "  %-46s %8s  %s %s %s\n", $9, $5, $6, $7, $8}'
  echo ""
  echo "Neon 异地快照："
  cd "$APP_DIR" || exit 1
  "$VENV_PY" - <<PYEOF 2>/dev/null || echo "  （查询失败）"
import sys
sys.path.insert(0, "$APP_DIR")
try:
    import cloud_snapshot as cs
    dsn = cs.get_dsn("$APP_DIR")
    if not dsn:
        print("  未配置 DSN")
    else:
        blob, ts = cs.download(cs._KEY_DB, dsn)
        if blob:
            print("  可用，大小 %.1f KB，快照时间 %s" % (len(blob) / 1024, ts))
        else:
            print("  云端没有快照")
except Exception as e:
    print("  查询失败：%s" % e)
PYEOF
  exit 0
fi

# ---------- 选源 ----------
FROM_NEON=0
SRC=""
if [ "$1" = "--from-neon" ]; then
  FROM_NEON=1
elif [ -n "$1" ]; then
  SRC="$1"
  [ -f "$SRC" ] || { c_red "备份文件不存在：$SRC"; exit 1; }
else
  SRC=$(ls -t "$BACKUP_DIR"/*.db 2>/dev/null | head -1)
  [ -n "$SRC" ] || { c_red "本地没有任何备份，可改用 --from-neon"; exit 1; }
fi

echo "=============================================="
if [ "$FROM_NEON" = "1" ]; then
  c_yel "将从 Neon 异地快照恢复"
else
  echo "将使用备份：$SRC"
  ls -lh "$SRC" | awk '{printf "  文件大小：%s   修改时间：%s %s %s\n", $5, $6, $7, $8}'
fi
echo ""
c_red "⚠️  此操作会用备份【覆盖】当前数据库：$DB"
echo "   当前库里的新数据将丢失（会先自动另存一份 .pre-restore 以便回退）"
echo "=============================================="
printf '确认恢复请输入 yes：'
read -r ans
[ "$ans" = "yes" ] || { echo "已取消，未做任何改动。"; exit 0; }

# ---------- 准备新库 ----------
TMPDB="$APP_DIR/data.db.restore-tmp"
rm -f "$TMPDB"

if [ "$FROM_NEON" = "1" ]; then
  echo "[1/6] 从 Neon 下载快照…"
  cd "$APP_DIR" || exit 1
  "$VENV_PY" - <<PYEOF
import sys
sys.path.insert(0, "$APP_DIR")
import cloud_snapshot as cs
dsn = cs.get_dsn("$APP_DIR")
if not dsn:
    print("  未配置 DSN"); sys.exit(1)
blob, ts = cs.download(cs._KEY_DB, dsn)
if not blob:
    print("  云端没有可用快照"); sys.exit(1)
open("$TMPDB", "wb").write(blob)
print("  已下载 %.1f KB，快照时间 %s" % (len(blob) / 1024, ts))
PYEOF
else
  echo "[1/6] 复制备份文件…"
  cp "$SRC" "$TMPDB"
fi

# ---------- 校验新库 ----------
echo "[2/6] 校验备份完整性…"
set +e
"$VENV_PY" - <<PYEOF
import sqlite3, sys
try:
    c = sqlite3.connect("$TMPDB")
    r = c.execute("PRAGMA integrity_check").fetchone()[0]
    if r != "ok":
        print("  integrity_check = %s" % r); sys.exit(1)
    n = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    u = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    print("  ✅ 完整性 ok，%d 张表，%d 个用户" % (n, u))
    c.close()
except Exception as e:
    print("  校验失败：%s" % e); sys.exit(1)
PYEOF
VERIFY_RC=$?
set -e
if [ "$VERIFY_RC" != "0" ]; then
  rm -f "$TMPDB"
  c_red "备份校验未通过，已中止（当前库未改动）"
  exit 1
fi

# ---------- 停服务 ----------
echo "[3/6] 停止服务 $SERVICE…"
systemctl stop "$SERVICE"
sleep 1

# ---------- 另存当前库 ----------
echo "[4/6] 备份当前库（可回退）…"
STAMP=$(date +%Y%m%d-%H%M%S)
KEEP="$DB.pre-restore-$STAMP"
if [ -f "$DB" ]; then
  cp "$DB" "$KEEP"
  echo "  已另存为：$KEEP"
else
  echo "  当前没有数据库文件，跳过"
fi
# 清理 WAL/SHM，避免旧事务日志污染新库
rm -f "$DB-wal" "$DB-shm"

# ---------- 替换 ----------
echo "[5/6] 替换数据库…"
mv "$TMPDB" "$DB"
chown --reference="$APP_DIR" "$DB" 2>/dev/null || true
chmod 644 "$DB"

# ---------- 启动并健康检查 ----------
echo "[6/6] 启动服务并检查…"
systemctl start "$SERVICE"
sleep 3
if systemctl is-active --quiet "$SERVICE"; then
  c_grn "✅ 服务已启动"
else
  c_red "❌ 服务启动失败！可用下面的命令回退："
  echo "     systemctl stop $SERVICE && cp '$KEEP' '$DB' && systemctl start $SERVICE"
  exit 1
fi

CODE=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 10 "$SITE/healthz" 2>/dev/null || echo "000")
if [ "$CODE" = "200" ]; then
  c_grn "✅ 健康检查通过（/healthz -> $CODE）"
else
  c_yel "⚠️  健康检查返回 $CODE，请手动确认"
fi

echo ""
c_grn "恢复完成。"
echo "  回退命令（若发现数据不对）："
echo "    systemctl stop $SERVICE && cp '$KEEP' '$DB' && systemctl start $SERVICE"
