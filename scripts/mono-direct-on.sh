#!/bin/bash
# MONO · Cloudflare 灰云（直连）切换脚本
# ============================================================
# 前置：DNS 已在 Cloudflare 后台切成灰云（A 记录指向 64.90.12.106）
#
# 干三件事：
#   1. 放行 ufw 的 443（全网，之前只放行 CF 网段）
#   2. 申请 Let's Encrypt 证书（HTTP-01，走 80 端口）
#   3. 换 nginx 证书 + reload
#
# 回滚：
#   bash /root/mono-direct-on.sh rollback
# ============================================================
set -e

CERT_DIR=/etc/nginx/ssl
LE_DIR=/etc/letsencrypt   # certbot 默认安装位置，不是 /etc/nginx/ssl
DOMAIN=mono-craft.de5.net
SERVER_IP=64.90.12.106

rollback() {
    echo "[回滚] 恢复 Cloudflare Origin CA 证书"
    sed -i "s|ssl_certificate .*|ssl_certificate     $CERT_DIR/mono-origin.crt;|" /etc/nginx/sites-available/mono
    sed -i "s|ssl_certificate_key .*|ssl_certificate_key $CERT_DIR/mono-origin.key;|" /etc/nginx/sites-available/mono
    nginx -t && systemctl reload nginx
    echo "[回滚] 完成。DNS 切回橙云后即为原状态。"
    exit 0
}

if [ "$1" = "rollback" ]; then
    rollback
fi

echo "=== 0. 前置检查 ==="
echo -n "  DNS 是否已指向本机: "
CURRENT=$(getent hosts $DOMAIN | head -1 | awk '{print $1}')
if [ "$CURRENT" != "$SERVER_IP" ]; then
    echo "否（当前解析到 $CURRENT，应为 $SERVER_IP）"
    echo "  → 请先在 Cloudflare 后台把 $DOMAIN 切成灰云 DNS only"
    exit 1
fi
echo "是（$CURRENT）"

echo
echo "=== 1. 放行 443 公网 ==="
ufw allow 443/tcp comment 'MONO direct (grey cloud)' 2>/dev/null || true
ufw status | grep -E "^443/tcp" | head -3

echo
echo "=== 2. 申请 Let's Encrypt 证书 ==="
mkdir -p $LE_DIR /var/www/certbot
if [ -f "$LE_DIR/privkey.pem" ]; then
    echo "  已存在证书，先续期"
    certbot certonly --webroot -w /var/www/certbot -d $DOMAIN -d www.$DOMAIN \
        --cert-name mono-direct --non-interactive --agree-tos \
        --register-unsafely-without-email --keep-until-expiring 2>&1 | tail -3
else
    echo "  第一次申请"
    certbot certonly --webroot -w /var/www/certbot -d $DOMAIN -d www.$DOMAIN \
        --cert-name mono-direct --non-interactive --agree-tos \
        --register-unsafely-without-email 2>&1 | tail -5
fi

if [ ! -f "$LE_DIR/live/mono-direct/privkey.pem" ]; then
    echo "  ✗ 证书申请失败，未改动 nginx"
    exit 1
fi
echo "  证书就绪：$LE_DIR/live/mono-direct/"

echo
echo "=== 3. 换 nginx 证书 ==="
# 保留 CF Origin CA 证书文件，橙云时还能切回来
cp -n $CERT_DIR/mono-origin.crt $LE_DIR/mono-origin.crt 2>/dev/null || true
cp -n $CERT_DIR/mono-origin.key $LE_DIR/mono-origin.key 2>/dev/null || true

# certbot 每次续期会更新 live/ 下的软链，指向真实文件
# 用 realpath 拿到真实路径写进配置，避免软链断裂导致 nginx 起不来
# ⚠️ 必须写 live 软链，不能用 realpath 展开成 archive/.../certN.pem ——
# 续期后 archive 号会变（cert1 → cert2），nginx 就指向不存在的文件了。
sed -i "s|ssl_certificate .*|ssl_certificate     $LE_DIR/live/mono-direct/cert.pem;|" /etc/nginx/sites-available/mono
sed -i "s|ssl_certificate_key .*|ssl_certificate_key $LE_DIR/live/mono-direct/privkey.pem;|" /etc/nginx/sites-available/mono
grep -E "ssl_certificate" /etc/nginx/sites-available/mono

echo
echo "=== 4. 校验并 reload ==="
nginx -t || { echo "  ✗ 配置有错，未 reload"; exit 1; }
systemctl reload nginx
sleep 1

echo
echo "=== 5. 验证 ==="
echo -n "  本机 HTTPS: "
curl -s -o /dev/null -w "%{http_code}  %{time_total}s\n" \
    --resolve $DOMAIN:443:127.0.0.1 https://$DOMAIN/ --max-time 15

echo -n "  公网 HTTPS: "
curl -s -o /dev/null -w "%{http_code}  首字节=%{time_starttransfer}s  总=%{time_total}s\n" \
    https://$DOMAIN/ --max-time 20

echo -n "  证书签发者: "
echo | openssl s_client -connect 127.0.0.1:443 -servername $DOMAIN 2>/dev/null \
    | openssl x509 -noout -issuer 2>/dev/null | sed 's/.*CN *= *//'

echo
echo "=== 6. 代理深度（IP 解析）==="
# 灰云直连时 X-Forwarded-For 只有访客一个地址，深度必须是 1。
# 设 0 会让所有请求记成 127.0.0.1 → 同设备互邀防护、IP 记录全部失效。
if ! grep -q MONO_PROXY_DEPTH /etc/systemd/system/mono.service; then
    sed -i '/^Environment=/a Environment=MONO_PROXY_DEPTH=1' /etc/systemd/system/mono.service
    systemctl daemon-reload
    echo "  已写入 systemd"
else
    echo "  systemd 已有配置"
fi
grep MONO_PROXY_DEPTH /etc/systemd/system/mono.service
systemctl restart mono
sleep 6
echo -n "  重启后服务: "
systemctl is-active mono

echo
echo "=== 7. 自动续期 ==="
systemctl is-enabled certbot.timer 2>/dev/null || systemctl enable certbot.timer 2>&1 | tail -1
systemctl list-timers 2>/dev/null | grep -i certbot | head -1

echo
echo "完成。现在可以用 https://$DOMAIN 直连了。"
echo "验证：打开首页登录，看日志里的 ip 是不是你的真实公网 IP（不是 127.0.0.1）"
echo "回滚命令：bash /root/mono-direct-on.sh rollback"
