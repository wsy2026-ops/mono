#!/bin/bash
# MONO · 灰云切换状态检查（随时可跑，不改任何东西）
# 用法：bash /root/mono-direct-status.sh
echo "═══════════════════════════════════════════"
echo " MONO 直连状态检查  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════"
DOMAIN="${MONO_DOMAIN:?请设置 MONO_DOMAIN}"
IP="${MONO_SERVER_IP:?请设置 MONO_SERVER_IP}"

echo
echo "【1】DNS 解析"
RESOLVED=$(getent hosts $DOMAIN | awk '{print $1}' | head -3)
echo "$RESOLVED" | sed 's/^/    /'
if echo "$RESOLVED" | grep -q "^$IP"; then
    echo "    → 状态：✅ 已指向本机（灰云）"
else
    echo "    → 状态：⏳ 还在 Cloudflare（橙云），未切换"
fi

echo
echo "【2】防火墙 443"
if ufw status | grep -E "^443/tcp\s+ALLOW\s+Anywhere" >/dev/null 2>&1; then
    echo "    → 状态：✅ 已放行全网 443"
else
    echo "    → 状态：❌ 只放行 Cloudflare 段 —— 直连会被挡住！"
    echo "       修复：ufw allow 443/tcp comment 'MONO direct'"
fi

echo
echo "【3】nginx 配置"
nginx -t 2>&1 | grep -q successful && echo "    语法：✅ ok" || echo "    语法：❌ 有错"
CERT=$(grep -oP 'ssl_certificate\s+\K\S+' /etc/nginx/sites-available/mono 2>/dev/null | head -1)
if echo "$CERT" | grep -q letsencrypt; then
    echo "    证书：✅ Let's Encrypt（浏览器可信）"
    echo "         $CERT"
else
    echo "    证书：⚠️ Cloudflare Origin CA（浏览器不信任，直连会报错）"
    echo "         $CERT"
fi

echo
echo "【4】证书有效期"
# certbot 默认装在 /etc/letsencrypt，不是 /etc/nginx/ssl
if [ -f /etc/letsencrypt/live/mono-direct/cert.pem ]; then
    openssl x509 -enddate -noout \
        -in /etc/letsencrypt/live/mono-direct/cert.pem 2>/dev/null \
        | sed 's/^/    /'
    if systemctl is-enabled certbot.timer >/dev/null 2>&1; then
        echo "    自动续期: ✅ 已启用"
    else
        echo "    自动续期: ❌ timer 没启用"
    fi
else
    echo "    还没申请 Let's Encrypt 证书"
fi

echo
echo "【5】代理深度（IP 解析）"
_d=$(grep -oP 'MONO_PROXY_DEPTH=\K[0-9]+' /etc/systemd/system/mono.service 2>/dev/null | head -1)
if [ -n "$_d" ]; then
    echo "    systemd：已配置 $_d"
else
    echo "    systemd：未显式配置（代码默认 1，也对）"
fi

echo
echo "【6】连通性实测"
if [ "$RESOLVED" = "$(echo "$RESOLVED" | grep $IP)" ] || echo "$RESOLVED" | grep -q "^$IP"; then
    # 用 --resolve 强制指本机：DNS 已指向本机，正常解析会绕到公网 IP 再回来
    echo -n "    本机 HTTPS: "
    curl -s -o /dev/null -w "%{http_code}  首字节=%{time_starttransfer}s  总=%{time_total}s\n" \
        --resolve "$DOMAIN:443:$IP" "https://$DOMAIN/" --max-time 15 2>/dev/null || echo "失败"
    echo -n "    证书签发者: "
    echo | openssl s_client -connect $IP:443 -servername $DOMAIN 2>/dev/null \
        | openssl x509 -noout -issuer 2>/dev/null | sed 's/.*CN *= *//' \
        | tr -d '\n'; echo
else
    echo "    （还没切 DNS，跳过）"
fi

echo
echo "【7】应用最近记录的 IP（应看到真实公网 IP，不是 127.0.0.1）"
tail -20 /opt/mono/app/logs/mono.log 2>/dev/null \
    | grep -oP 'REQ \S+ \S+ -> \d+ \d+ms ip=\S+' | tail -5 \
    | sed 's/^/    /' || echo "    日志没读到"

echo
echo "═══════════════════════════════════════════"
