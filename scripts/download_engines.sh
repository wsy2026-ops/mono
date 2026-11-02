#!/usr/bin/env bash
# =============================================================
# scripts/download_engines.sh —— 棋类 AI 引擎的获取与检查
#
# 为什么不在仓库里直接带引擎：
#   五个引擎加起来 ~400MB，其中三个是 GPL 软件（Stockfish / Pikafish / GNU Go）。
#   把二进制塞进 git 会让 clone 变成灾难，也把别人的许可证义务捆到你身上。
#
# 用法：
#   bash scripts/download_engines.sh check     # 只看缺什么（默认）
#   bash scripts/download_engines.sh info      # 每个引擎的来源与许可证
#   bash scripts/download_engines.sh fetch     # 尝试自动下载能自动下的
#
# 放哪：引擎目录放在**项目根**（和 app.py 同级），不是 board/ 下面。
#   <repo>/pikafish/pikafish(.exe)
#   <repo>/katago/katago(.exe) + katago/*.bin.gz
#   <repo>/stockfish/stockfish*（Linux 无扩展名 / Windows *.exe）
#   <repo>/rapfi/engine/...
#   <repo>/egaroucid/...
#   <repo>/gnugo/gnugo(.exe)
#
# 没有引擎也能跑：代码会探测可执行文件，探测不到就降级到内置 AI（棋力弱一些）。
# =============================================================
set -u
cd "$(dirname "$0")/.." || exit 1

MODE="${1:-check}"

say() { printf '%s\n' "$*"; }

# 引擎清单：名字 | 目录 | 需要的文件（glob）| 许可证 | 来源
check_one() {
    local name="$1" dir="$2" glob="$3" lic="$4" url="$5"
    local found=""
    if [ -d "$dir" ]; then
        found=$(find "$dir" -maxdepth 2 -type f -name "$glob" 2>/dev/null | head -1)
    fi
    if [ -n "$found" ]; then
        printf '  \033[32m✓\033[0m %-12s %s\n' "$name" "$found"
    else
        printf '  \033[33m·\033[0m %-12s 未安装（降级到内置 AI）\n' "$name"
    fi
}

engines() {
    check_one "Pikafish"  "pikafish"  "pikafish*"   "GPL-3.0" "https://github.com/official-pikafish/Pikafish/releases"
    check_one "Stockfish" "stockfish" "stockfish*"  "GPL-3.0" "https://github.com/official-stockfish/Stockfish/releases"
    check_one "KataGo"    "katago"    "katago*"     "MIT"     "https://github.com/lightvector/KataGo/releases"
    check_one "RAPFI"     "rapfi"     "rapfi*"      "GPL-3.0" "https://github.com/dhbloo/rapfi/releases"
    check_one "Egaroucid" "egaroucid" "egaroucid*"  "AGPL-3.0" "https://github.com/Nyanyan/Egaroucid/releases"
    check_one "GNU Go"    "gnugo"     "gnugo*"      "GPL-3.0" "https://www.gnu.org/software/gnugo/"
}

case "$MODE" in
  check)
    say "引擎检查（项目根：$(pwd)）"
    engines
    say ""
    say "要装的话：bash scripts/download_engines.sh info"
    ;;
  info)
    say "引擎来源与许可证"
    say ""
    say "  Pikafish   中国象棋   GPL-3.0    https://github.com/official-pikafish/Pikafish/releases"
    say "  Stockfish  国际象棋   GPL-3.0    https://github.com/official-stockfish/Stockfish/releases"
    say "  KataGo     围棋       MIT        https://github.com/lightvector/KataGo/releases"
    say "  RAPFI      五子棋     GPL-3.0    https://github.com/dhbloo/rapfi/releases"
    say "  Egaroucid  翻转棋     AGPL-3.0   https://github.com/Nyanyan/Egaroucid/releases"
    say "  GNU Go     围棋兜底   GPL-3.0    https://www.gnu.org/software/gnugo/"
    say ""
    say "  ⚠️ 这些都是**别人的软件**，本仓库的 MIT 许可证不覆盖它们。"
    say "     自己下载、自己遵守各自的协议；本项目只通过子进程调用它们，不链接、不修改。"
    say ""
    say "  下载后按上面的目录结构放好，重启服务即可自动探测。"
    say "  KataGo 还需要一个权重文件（*.bin.gz），放在 katago/ 下，"
    say "  从 https://katagotraining.org/networks/ 选一个下载。"
    ;;
  fetch)
    say "自动下载：只处理命名稳定的 Stockfish，其余请手动下载（见 info）"
    mkdir -p stockfish
    OS=$(uname -s)
    if [ "$OS" = "Linux" ]; then
        ASSET="stockfish-ubuntu-x86-64-avx2.tar"
    else
        ASSET="stockfish-windows-x86-64-avx2.zip"
    fi
    URL="https://github.com/official-stockfish/Stockfish/releases/latest/download/$ASSET"
    say "  → $URL"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 2 -o "/tmp/$ASSET" "$URL" || {
            say "  ✗ 下载失败，请到 releases 页面手动下载"; exit 1; }
        if [ "${ASSET##*.}" = "zip" ]; then
            unzip -oq "/tmp/$ASSET" -d stockfish/ && rm -f "/tmp/$ASSET"
        else
            tar -xf "/tmp/$ASSET" -C stockfish/ && rm -f "/tmp/$ASSET"
        fi
        say "  ✓ 已解压到 stockfish/"
    else
        say "  ✗ 没有 curl"
    fi
    ;;
  *)
    say "用法：bash scripts/download_engines.sh [check|info|fetch]"
    exit 1
    ;;
esac
