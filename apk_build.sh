#!/bin/bash
# apk_build.sh — APK 构建辅助（规划文档 073 简化版）
#
# 解决什么
# --------
# 文档 073 列的三个坑，今天全踩了一遍：
#   1. 手工改域名要改 5 个文件，容易漏（今天改了 index.html / index.xhtml /
#      unpackage 缓存 2 个 / apk/xqmaster，漏一个就是白屏）
#   2. 备份文件留在项目目录会被打进 APK（今天生成 .bak_ 差点打进去）
#   3. 版本号要跟 base.html 页脚一致，改一处忘另一处
#
# 本脚本自动做 1/2/3，然后提示你手工用 HBuilderX 打包（GUI 没法自动化）。
#
# 用法
# ----
#   bash apk_build.sh            # 检查 + 修复 + 提示
#   bash apk_build.sh --check    # 只检查不改（CI 用）

set -u
APK_DIR="/d/MONO/MONO"
BACKUP_DIR="/d/MONO/_apk_backup"
SITE_DIR="$(cd "$(dirname "$0")" && pwd)"
DOMAIN="monocraft.top"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }

if [ ! -d "$APK_DIR" ]; then
    echo "找不到 APK 项目目录: $APK_DIR"
    exit 1
fi

echo "=== 1. 壳页域名（应为 $DOMAIN）==="
SHELLS=(
    "$APK_DIR/index.html"
    "$APK_DIR/index.xhtml"
    "$APK_DIR/unpackage/cache/wgt/H590B4101/index.html"
    "$APK_DIR/unpackage/cache/wgt/H590B4101/index.xhtml"
    "$SITE_DIR/apk/xqmaster/index.html"
)
NEED_FIX=0
for f in "${SHELLS[@]}"; do
    [ -f "$f" ] || continue
    short="${f#$APK_DIR/}"
    if grep -q "de5\.net" "$f" 2>/dev/null; then
        if [ "$CHECK_ONLY" = "1" ]; then
            bad "$short 还有 de5.net"
            NEED_FIX=1
        else
            sed -i 's/mono-craft\.de5\.net/'"$DOMAIN"'/g' "$f"
            ok "$short 已换成 $DOMAIN"
        fi
    else
        ok "$short"
    fi
done

echo
echo "=== 2. 备份文件（不能留在项目里）==="
STRAY=$(find "$APK_DIR" -maxdepth 3 \( -name "*.bak*" -o -name "*.old" -o -name "*~" -o -name "*.orig" \) 2>/dev/null | grep -v "/unpackage/")
if [ -n "$STRAY" ]; then
    echo "$STRAY" | while read -r f; do
        if [ "$CHECK_ONLY" = "1" ]; then
            bad "$(basename "$f") 会被打进 APK"
        else
            mkdir -p "$BACKUP_DIR"
            mv "$f" "$BACKUP_DIR/" && ok "$(basename "$f") 已挪到 _apk_backup/"
        fi
    done
    NEED_FIX=1
else
    ok "项目目录干净"
fi

echo
echo "=== 3. 版本号一致性 ==="
APK_VER=$(grep -oE '"name"\s*:\s*"[0-9.]+"' "$APK_DIR/manifest.json" 2>/dev/null | head -1 | grep -oE '[0-9.]+')
SITE_VER=$(grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' "$SITE_DIR/templates/base.html" 2>/dev/null | head -1 | sed 's/^v//')
echo "  APK manifest : ${APK_VER:-未读到}"
echo "  站点 base.html: ${SITE_VER:-未读到}"
if [ -n "$APK_VER" ] && [ -n "$SITE_VER" ]; then
    if [ "$APK_VER" = "$SITE_VER" ]; then
        ok "一致"
    else
        warn "不一致（APK 是 $APK_VER，站点是 $SITE_VER）—— 打包前确认要不要跟"
    fi
else
    warn "有一边没读到，手工核对一下"
fi

echo
echo "=== 4. 下一步（必须手工）==="
cat <<EOF
  1. 用 HBuilderX 打开：$APK_DIR
  2. 发行 → 原生App-云打包 → 选择 Android
  3. ⚠️ 签名证书必须跟上次一致，否则用户要卸载旧版才能装
  4. 打包完成后，把 apk 复制到 $SITE_DIR/static/MONO-<版本>.apk
  5. 同步更新 base.html 页脚的下载链接
EOF

if [ "$CHECK_ONLY" = "1" ] && [ "$NEED_FIX" = "1" ]; then
    echo
    bad "检查未通过，去掉 --check 跑一次即可自动修复"
    exit 1
fi
exit 0
