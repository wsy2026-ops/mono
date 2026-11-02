#!/bin/bash
# 把 infra/ 里的配置下发到服务器 —— 104：配置即代码，改动可追溯、可回滚。
#
# 用法：
#   bash infra/apply.sh            # 下发并校验
#   bash infra/apply.sh --check    # 只对比差异，不落盘
#
# 设计原则：
#   · 每份配置改动前先备份到 /root/infra-backup-<时间戳>/（可回滚）
#   · nginx 先 `nginx -t` 再 reload；测试不过就停，绝不把站搞挂
#   · **systemd 单元不下发** —— 里面是脱敏占位符，下发会用占位符覆盖真密钥
set -u
VPS="${MONO_VPS:-mono-vps}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TS=$(date '+%Y%m%d_%H%M%S')
BK="/root/infra-backup-$TS"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say(){ printf '%s\n' "$*"; }
die(){ printf '  \033[31m✗ %s\033[0m\n' "$*"; exit 1; }

say "=============================================="
say "  MONO 配置下发  $TS  →  $VPS"
say "=============================================="

# ---------- 1) 对比差异 ----------
say "[1] 对比差异"
DIFF_FOUND=0
for pair in "logrotate/mono:/etc/logrotate.d/mono" \
            "nginx/sites-available-mono:/etc/nginx/sites-available/mono" \
            "nginx/snippets-mono-locations.conf:/etc/nginx/snippets/mono-locations.conf"; do
    LOCAL="$HERE/${pair%%:*}"
    REMOTE="${pair##*:}"
    [ -f "$LOCAL" ] || { say "  ! 本地缺 $LOCAL，跳过"; continue; }
    if ssh -o ConnectTimeout=15 "$VPS" "cat $REMOTE 2>/dev/null" | diff -q "$LOCAL" - >/dev/null 2>&1; then
        say "  = $(basename "$REMOTE") 一致"
    else
        say "  ~ $(basename "$REMOTE") 有差异"
        DIFF_FOUND=1
    fi
done
[ "$CHECK_ONLY" = "1" ] && { say ""; say "  (--check 模式，不落盘)"; exit 0; }

# ---------- 2) 备份 ----------
say "[2] 备份线上配置 → $BK"
ssh -o ConnectTimeout=15 "$VPS" "mkdir -p $BK && \
  cp -a /etc/logrotate.d/mono $BK/ 2>/dev/null; \
  cp -a /etc/nginx/sites-available/mono $BK/ 2>/dev/null; \
  cp -a /etc/nginx/snippets/mono-locations.conf $BK/ 2>/dev/null; \
  cp -a /etc/systemd/system/mono.service $BK/ 2>/dev/null; \
  ls -1 $BK | wc -l" | xargs -I{} say "  已备份 {} 个文件"

# ---------- 3) logrotate ----------
say "[3] 下发 logrotate"
scp -q -o ConnectTimeout=20 "$HERE/logrotate/mono" "$VPS:/tmp/_lr_mono" || die "上传失败"
ssh -o ConnectTimeout=20 "$VPS" "cp /tmp/_lr_mono /etc/logrotate.d/mono && chmod 644 /etc/logrotate.d/mono && rm -f /tmp/_lr_mono && \
  E=\$(logrotate -d /etc/logrotate.conf 2>&1 | grep -c '^error'); \
  if [ \"\$E\" = '0' ]; then echo '  ✓ logrotate 校验通过'; else echo \"  ✗ logrotate 有 \$E 条错误\"; exit 1; fi"

# ---------- 4) nginx ----------
say "[4] 下发 nginx（先测试再 reload）"
scp -q -o ConnectTimeout=20 "$HERE/nginx/sites-available-mono" "$VPS:/tmp/_ng_site" || die "上传失败"
ssh -o ConnectTimeout=20 "$VPS" "cp /tmp/_ng_site /etc/nginx/sites-available/mono && rm -f /tmp/_ng_site"
if [ -f "$HERE/nginx/snippets-mono-locations.conf" ]; then
    scp -q -o ConnectTimeout=20 "$HERE/nginx/snippets-mono-locations.conf" "$VPS:/tmp/_ng_snip"
    ssh -o ConnectTimeout=20 "$VPS" "mkdir -p /etc/nginx/snippets && cp /tmp/_ng_snip /etc/nginx/snippets/mono-locations.conf && rm -f /tmp/_ng_snip"
fi
ssh -o ConnectTimeout=20 "$VPS" "nginx -t 2>&1 | tail -2 && systemctl reload nginx && echo '  ✓ nginx 已 reload'"

# ---------- 5) systemd（只提示，不下发） ----------
say "[5] systemd 单元 —— 故意不下发"
say "  ! infra/systemd/mono.service 是脱敏快照，下发会用占位符覆盖真密钥"
say "  ! 改服务配置请直接改服务器上的 /etc/systemd/system/mono.service，再跑 snapshot.sh 更新快照"

# ---------- 6) 校验 ----------
say "[6] 部署后校验"
ssh -o ConnectTimeout=30 "$VPS" "bash -s" < "$HERE/verify.sh" || die "校验失败，检查上面的 ✗ 项（配置已备份在 $BK）"

say ""
say "  回滚：ssh $VPS \"cp -a $BK/* /etc/logrotate.d/ 2>/dev/null; systemctl reload nginx\""
