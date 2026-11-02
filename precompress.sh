#!/bin/bash
# 预压缩静态资源：生成 .gz 供 nginx gzip_static 直接回源，省掉实时压缩的 CPU。
# 2 核机器上实时 gzip 是纯浪费 —— 压缩一次吃几百毫秒 CPU，预压缩只吃一次。
set -u
cd "$(dirname "$0")" || exit 1

MIN_BYTES=1024
GENERATED=0
SKIPPED=0

mapfile -t FILES < <(
  find static -type f \
    \( -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.json' \
       -o -name '*.html' -o -name '*.map' -o -name '*.woff' -o -name '*.woff2' \
       -o -name '*.xml' -o -name '*.txt' \) \
    ! -name '*.gz' 2>/dev/null
)

for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  size=$(stat -c%s "$f" 2>/dev/null || echo 0)
  if [ "$size" -lt "$MIN_BYTES" ]; then
    SKIPPED=$((SKIPPED+1))
    continue
  fi
  if gzip -9 -c "$f" > "$f.gz" 2>/dev/null && [ -s "$f.gz" ]; then
    gz=$(stat -c%s "$f.gz" 2>/dev/null || echo 0)
    if [ "$gz" -ge "$size" ]; then
      rm -f "$f.gz"
      SKIPPED=$((SKIPPED+1))
    else
      GENERATED=$((GENERATED+1))
    fi
  else
    rm -f "$f.gz"
    SKIPPED=$((SKIPPED+1))
  fi
done

find static -name '*.gz' -type f 2>/dev/null | while read -r gz; do
  src="${gz%.gz}"
  [ -f "$src" ] || rm -f "$gz"
done

RAW=$(find static -type f ! -name '*.gz' -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s/1048576}')
GZ=$(find static -name '*.gz' -type f -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s/1048576}')
echo "预压缩: ${GENERATED} 个 .gz，跳过 ${SKIPPED} 个；static 原始 ${RAW}M，.gz 合计 ${GZ}M"
