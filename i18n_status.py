#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""i18n_status.py — 094 国际化覆盖率报告。

用法：
    python i18n_status.py            # 按模板列出还剩多少中文文案
    python i18n_status.py --top 20   # 只看最脏的 20 个

只统计**界面文案**（文本节点 + placeholder/title/aria-label/alt），
不含 <script>/<style>/Jinja 注释，也不含数据库里的内容 ——
所以这个数字下降 = 模板里的中文在减少，跟用户发的中文帖子无关。
"""
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(BASE_DIR, "templates")
CN = re.compile(r"[\u4e00-\u9fa5]")
ATTR = re.compile(r'\b(placeholder|title|aria-label|alt)="([^"]*)"')


def scan(path):
    try:
        s = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    s = re.sub(r"<script[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"\{#[\s\S]*?#\}", "", s)
    out = []
    for m in re.finditer(r">([^<>{}]+)<", s):
        txt = m.group(1).strip()
        if txt and CN.search(txt):
            out.append(txt)
    for m in ATTR.finditer(s):
        val = m.group(2).strip()
        if val and CN.search(val) and "{{" not in val:
            out.append(val)
    return out


def main():
    top = 25
    if "--top" in sys.argv:
        try:
            top = int(sys.argv[sys.argv.index("--top") + 1])
        except (IndexError, ValueError):
            pass
    rows = []
    total = 0
    for fn in sorted(os.listdir(TPL_DIR)):
        if not fn.endswith(".html"):
            continue
        items = scan(os.path.join(TPL_DIR, fn))
        if items:
            rows.append((len(items), fn, items))
            total += len(items)
    rows.sort(reverse=True)
    print("=" * 56)
    print("  模板中文文案剩余：%d 处 / %d 个模板" % (total, len(rows)))
    print("=" * 56)
    for n, fn, items in rows[:top]:
        print("%-4d %s" % (n, fn))
    if len(rows) > top:
        print("... 还有 %d 个模板" % (len(rows) - top))
    print()
    print("已接 i18n 的模板：%d 个" % sum(
        1 for fn in os.listdir(TPL_DIR)
        if fn.endswith(".html") and "{{ tr('" in open(
            os.path.join(TPL_DIR, fn), encoding="utf-8", errors="ignore").read()))


if __name__ == "__main__":
    main()
