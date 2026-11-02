"""MONO 社区 · 历史攻击数据回溯

背景：
    运行监控是 2026-09-14 才上线的，只能记录上线之后的事件。
    但服务器的 /var/log/auth.log 里保留着更早的 SSH 爆破记录
    （实测 9123 次密码失败，覆盖 9/13 ~ 9/14）。
    历史数据从最早有记录的时点开始持续记录，需要把这段补进来。

做法：
    解析 auth.log 的 "Failed password ... from <IP>" 行，
    按 (小时, IP) 聚合后写入 attack_events（kind = ssh_brute）。
    按小时聚合而非逐条写入，是为了让 9000+ 条记录变成约千条，
    既保留时间分辨率，又不会把库撑大。

可重复执行：每次会先清空旧的 ssh_brute 记录再重建。

用法：
    python backfill_history.py                 # 回溯并写入
    python backfill_history.py --dry-run       # 只看统计，不写库
    python backfill_history.py --data-dir /opt/mono/app
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

AUTH_LOG = "/var/log/auth.log"
KIND = "ssh_brute"
DETAIL = "历史数据（auth.log 回溯）"

_LINE_RE = re.compile(
    r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2}).*?"
    r"Failed password.*?from\s+([0-9a-fA-F:.]+)")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def parse_auth_log(path):
    """解析 auth.log，返回 {(小时时间戳, IP): 次数} 与统计信息。"""
    buckets = {}
    total = 0
    bad = 0
    now = datetime.now()

    with open(path, "r", errors="replace") as f:
        for line in f:
            m = _LINE_RE.match(line)
            if not m:
                continue
            mon, day, hh, mm, ss, ip = m.groups()
            try:
                month = _MONTHS[mon]
                year = now.year
                if month > now.month + 1:
                    year -= 1
                dt = datetime(year, month, int(day), int(hh), int(mm), int(ss))
                hour_ts = dt.replace(minute=0, second=0, microsecond=0).timestamp()
            except Exception:
                bad += 1
                continue
            buckets[(hour_ts, ip)] = buckets.get((hour_ts, ip), 0) + 1
            total += 1
    return buckets, total, bad


def main():
    ap = argparse.ArgumentParser(description="回溯 auth.log 里的历史 SSH 爆破记录")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--auth-log", default=AUTH_LOG)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.auth_log):
        print("✗ 找不到日志文件：%s" % args.auth_log)
        return 1

    print("=" * 60)
    print(" 历史攻击数据回溯")
    print("=" * 60)
    print("日志文件：%s" % args.auth_log)
    print("文件大小：%.1f KB" % (os.path.getsize(args.auth_log) / 1024.0))
    print()

    buckets, total, bad = parse_auth_log(args.auth_log)
    if not buckets:
        print("未解析到任何记录。")
        return 1

    hours = sorted({k[0] for k in buckets})
    ips = {}
    for (ts, ip), c in buckets.items():
        ips[ip] = ips.get(ip, 0) + c

    print("解析结果：")
    print("  密码失败总次数 : %d" % total)
    print("  涉及独立 IP    : %d" % len(ips))
    print("  时间范围       : %s ~ %s" % (
        datetime.fromtimestamp(hours[0]).strftime("%Y-%m-%d %H:%M"),
        datetime.fromtimestamp(hours[-1]).strftime("%Y-%m-%d %H:%M")))
    print("  聚合后记录条数 : %d（按小时+IP）" % len(buckets))
    if bad:
        print("  解析失败行数   : %d" % bad)
    print()
    print("  攻击来源 TOP 10：")
    for ip, c in sorted(ips.items(), key=lambda x: -x[1])[:10]:
        print("    %-18s %6d 次" % (ip, c))
    print()

    if args.dry_run:
        print("（--dry-run 模式，未写入数据库）")
        return 0

    code_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.abspath(args.data_dir or os.environ.get("MONO_DATA_DIR") or code_dir)
    db_path = os.path.join(data_dir, "data.db")
    if not os.path.exists(db_path):
        print("✗ 找不到数据库：%s" % db_path)
        return 1

    try:
        import monitor as _mon
        if not _mon.init(data_dir):
            print("✗ 监控表初始化失败")
            return 1
    except Exception as e:
        print("✗ 无法导入 monitor 模块：%s" % e)
        return 1

    db = sqlite3.connect(db_path, timeout=20)
    try:
        db.execute("PRAGMA busy_timeout=10000")
        cur = db.execute("DELETE FROM attack_events WHERE kind=?", (KIND,))
        if cur.rowcount:
            print("已清除旧的回溯记录 %d 条" % cur.rowcount)

        rows = [(ts, KIND, ip, None, c, DETAIL) for (ts, ip), c in buckets.items()]
        db.executemany(
            "INSERT INTO attack_events (ts, kind, ip, path, count, detail) "
            "VALUES (?,?,?,?,?,?)", rows)
        db.commit()
        n = db.execute("SELECT COALESCE(SUM(count),0) FROM attack_events WHERE kind=?",
                       (KIND,)).fetchone()[0]
        print("✓ 已写入 %d 条聚合记录，覆盖 %d 次爆破" % (len(rows), n))
        print("  重启服务后归属地会在 5 分钟内自动补齐：systemctl restart mono")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
