#!/bin/bash
# 从服务器拉配置 → infra/（自动脱敏）—— 104：先把现状纳入版本管理，再谈自动化。
#
# 用法：bash infra/snapshot.sh
#
# 敏感处理：systemd 单元里的 SECRET_KEY / RESEND_KEY / SMTP_PASS / NEON_DSN
# 一律替换成占位符，**真值永远不落进仓库**。
set -u
VPS="${MONO_VPS:-mono-vps}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/nginx" "$HERE/systemd" "$HERE/logrotate"

echo "拉取 $VPS 的配置…"

scp -q -o ConnectTimeout=20 "$VPS:/etc/nginx/sites-available/mono" "$HERE/nginx/sites-available-mono" \
    && echo "  ✓ nginx/sites-available-mono"
scp -q -o ConnectTimeout=20 "$VPS:/etc/nginx/snippets/mono-locations.conf" \
    "$HERE/nginx/snippets-mono-locations.conf" 2>/dev/null \
    && echo "  ✓ nginx/snippets-mono-locations.conf"
scp -q -o ConnectTimeout=20 "$VPS:/etc/logrotate.d/mono" "$HERE/logrotate/mono" \
    && echo "  ✓ logrotate/mono"

# systemd 单元：拉下来立刻脱敏，中间文件删掉
TMP="$(mktemp)"
scp -q -o ConnectTimeout=20 "$VPS:/etc/systemd/system/mono.service" "$TMP" && \
python3 - "$TMP" "$HERE/systemd/mono.service" <<'PYEOF'
import io, re, sys
src, dst = sys.argv[1], sys.argv[2]
SECRET = ('MONO_SECRET_KEY', 'MONO_RESEND_KEY', 'MONO_SMTP_PASS', 'MONO_NEON_DSN',
          'MONO_SMTP_USER', 'MONO_SMTP_HOST', 'MONO_SMTP_PORT', 'MONO_SMTP_FROM')
out = []
for line in io.open(src, encoding='utf-8').read().splitlines():
    m = re.match(r'^Environment=([A-Z0-9_]+)=', line)
    if m and m.group(1) in SECRET:
        out.append('Environment=%s=<脱敏：真值只在服务器上>' % m.group(1))
    else:
        out.append(line)
head = ("# MONO systemd 单元 · **脱敏快照**（apply.sh 不会下发它）\n"
        "# 只用于对比「线上配置有没有被手改」。真值请直接看服务器。\n\n")
io.open(dst, 'w', encoding='utf-8', newline='\n').write(head + '\n'.join(out) + '\n')
print("  ✓ systemd/mono.service（已脱敏）")
PYEOF
rm -f "$TMP"

echo "完成。检查差异：git diff infra/"
