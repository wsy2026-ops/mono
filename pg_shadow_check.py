# -*- coding: utf-8 -*-
"""pg_shadow_check.py — 052 影子库每日比对（cron 4:30，避开 4 点备份）。

行数一致 → 静默；不一致 → 写 mono.log 事件（admin 事件页可见）+ 退出码 1。
用法：MONO_PG_SHADOW=1 python pg_shadow_check.py
"""
import os
import sys
import time

os.environ.setdefault("MONO_PG_SHADOW", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pg_shadow  # noqa: E402


def main():
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")
    bad = []
    lines = []
    for t in ("points_log", "messages"):
        lite, pg, ok = pg_shadow.verify(t, db)
        # PG 落后就当场补齐 —— 镜像写入是静默失败的，不修的话缺口会一直挂着，
        # 而 sync_full() 只补「前缀」，中间有洞补不上。
        if not ok and pg >= 0 and pg < lite:
            n = pg_shadow.repair_missing(t, db)
            if n:
                lite2, pg2, ok2 = pg_shadow.verify(t, db)
                lines.append("%s sqlite=%d pg=%d → 自动补 %d 条 → %s"
                             % (t, lite, pg, n, "OK" if ok2 else "仍有差异 pg=%d" % pg2))
                lite, pg, ok = lite2, pg2, ok2
                if not ok2:
                    bad.append(t)
                continue
        lines.append("%s sqlite=%d pg=%d %s" % (t, lite, pg, "OK" if ok else "MISMATCH"))
        if not ok:
            bad.append(t)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if bad:
        # 记进 mono.log（_log_mod 在 app 上下文外不可用，直接写事件表太绕——
        # cron 环境用 print 到 stdout，由 cron 重定向到日志文件）
        print("%s [SHADOW-MISMATCH] %s" % (stamp, "; ".join(lines)))
        return 1
    print("%s [SHADOW-OK] %s" % (stamp, "; ".join(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
