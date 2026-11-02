"""MONO 社区 · 本地版本化备份

为什么需要：
    cloud_snapshot.py 把数据存到 Neon，但那是「覆盖式」的单一副本——
    一旦某次上传的是异常状态的数据，好数据会被直接盖掉，没有回退余地。
    再加上 VPS 上的 media（uploads / 头像）整体不在代码包内，
    所以必须在服务器本地留一份带时间戳、可回溯的备份。

做法：
    1. 用 SQLite 在线备份 API 产出「一致性快照」，再写成文件
       （不锁库、不阻塞写入，且天然包含 WAL 里未 checkpoint 的内容）
    2. 每天一份，保留最近 KEEP_DAILY 份
    3. 每月 1 号额外留一份长期档，保留 KEEP_MONTHLY 份
    4. 全部过程写结构化日志，失败不会影响业务

用法：
    python backup_db.py                 # 备份到 <数据目录>/backups/
    python backup_db.py --data-dir /opt/mono/app
    python backup_db.py --list          # 只看现有备份
    python backup_db.py --keep-daily 14 # 自定义保留份数

配合 cron（服务器上每天 4:00 执行）：
    0 4 * * * cd /opt/mono/app && /opt/mono/venv/bin/python backup_db.py >> /opt/mono/app/logs/backup.log 2>&1
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

KEEP_DAILY = 7
# 用户 2026-10-21 要求：只保留近 7 天快照，不堆月度长期档。
KEEP_MONTHLY = 0
BACKUP_DIR_NAME = "backups"

_DAILY_RE = "mono-"


def _log(msg, level="info"):
    """优先走 mono_log（拿到结构化日志），拿不到就退回 print。"""
    try:
        import mono_log as _lm
        getattr(_lm, level, _lm.info)("[备份] %s" % msg)
    except Exception:
        try:
            print("[备份] " + str(msg), flush=True)
        except Exception:
            pass


def _safe_remove(path):
    """在保留策略里删除旧备份。只删自己产出的、符合命名规则的文件。"""
    try:
        os.remove(path)
        return True
    except Exception as e:
        _log("删除失败 %s: %s" % (path, e), "warn")
        return False


def make_backup(db_path, backup_dir, keep_daily=KEEP_DAILY, keep_monthly=KEEP_MONTHLY,
                tag=None):
    """产出一份备份并执行保留策略。返回备份文件路径，失败返回 None。

    用 SQLite 在线备份 API（conn.backup）而不是直接复制文件：
    直接拷贝在 WAL 模式下会漏掉未合并的 -.wal 内容，产出损坏/过期副本。
    """
    if not os.path.exists(db_path):
        _log("源数据库不存在，跳过：%s" % db_path, "warn")
        return None
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except Exception as e:
        _log("无法创建备份目录 %s: %s" % (backup_dir, e), "error")
        return None

    now = datetime.now()
    stamp = tag or now.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, "mono-%s.db" % stamp)
    tmp = dest + ".part"

    for p in (tmp, tmp + "-wal", tmp + "-shm"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    src = None
    dst = None
    try:
        src = sqlite3.connect(db_path, timeout=20)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.close()
        dst = None
        src.close()
        src = None

        if os.path.exists(dest):
            os.remove(dest)
        os.rename(tmp, dest)
        size = os.path.getsize(dest)
        _log("备份完成 %s（%.1f KB）" % (os.path.basename(dest), size / 1024.0))
    except Exception as e:
        _log("备份失败: %s" % e, "error")
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            _safe_remove(p)
        return None
    finally:
        for c in (dst, src):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass

    if now.day == 1 and tag is None:
        month_dest = os.path.join(backup_dir, "mono-%s.db" % now.strftime("%Y%m"))
        try:
            if not os.path.exists(month_dest):
                import shutil
                shutil.copy2(dest, month_dest)
                _log("已留存月度档 %s" % os.path.basename(month_dest))
        except Exception as e:
            _log("月档留存失败: %s" % e, "warn")

    _prune(backup_dir, keep_daily, keep_monthly)
    return dest


def _prune(backup_dir, keep_daily, keep_monthly):
    """保留策略：日常档留最近 N 份，月档留最近 M 份。"""
    try:
        files = [f for f in os.listdir(backup_dir) if f.startswith(_DAILY_RE) and f.endswith(".db")]
    except Exception:
        return
    daily, monthly = [], []
    for f in files:
        stem = f[len(_DAILY_RE):-3]
        (monthly if len(stem) == 6 and stem.isdigit() else daily).append(f)

    def _by_mtime_desc(names):
        return sorted(names, key=lambda n: os.path.getmtime(os.path.join(backup_dir, n)), reverse=True)

    removed = 0
    for excess in (_by_mtime_desc(daily)[keep_daily:], _by_mtime_desc(monthly)[keep_monthly:]):
        for name in excess:
            if _safe_remove(os.path.join(backup_dir, name)):
                removed += 1
    if removed:
        _log("保留策略清理 %d 个旧备份" % removed)


def list_backups(backup_dir):
    """打印现有备份，便于人工确认。"""
    if not os.path.isdir(backup_dir):
        print("（备份目录不存在：%s）" % backup_dir)
        return
    rows = []
    for f in os.listdir(backup_dir):
        p = os.path.join(backup_dir, f)
        if os.path.isfile(p):
            rows.append((os.path.getmtime(p), f, os.path.getsize(p)))
    if not rows:
        print("（暂无备份）")
        return
    rows.sort(reverse=True)
    total = sum(r[2] for r in rows)
    print("备份目录：%s" % backup_dir)
    print("%-28s %12s  %s" % ("文件名", "大小", "时间"))
    for mtime, name, size in rows:
        print("%-28s %10.1fKB  %s" % (name, size / 1024.0,
                                      datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")))
    print("共 %d 个，合计 %.1f KB" % (len(rows), total / 1024.0))


def main():
    ap = argparse.ArgumentParser(description="MONO 社区 · 本地版本化备份")
    ap.add_argument("--data-dir", default=None,
                    help="数据目录（含 data.db），默认取 MONO_DATA_DIR 或脚本所在目录")
    ap.add_argument("--backup-dir", default=None,
                    help="备份输出目录，默认 <数据目录>/%s" % BACKUP_DIR_NAME)
    ap.add_argument("--keep-daily", type=int, default=KEEP_DAILY)
    ap.add_argument("--keep-monthly", type=int, default=KEEP_MONTHLY)
    ap.add_argument("--list", action="store_true", help="只列出已有备份")
    ap.add_argument("--tag", default=None, help="自定义时间戳后缀（手动备份用）")
    args = ap.parse_args()

    code_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.abspath(args.data_dir or os.environ.get("MONO_DATA_DIR") or code_dir)
    backup_dir = os.path.abspath(args.backup_dir or os.path.join(data_dir, BACKUP_DIR_NAME))

    try:
        import mono_log as _lm
        _lm.init_logging(data_dir)
    except Exception:
        pass

    if args.list:
        list_backups(backup_dir)
        return 0

    db_path = os.path.join(data_dir, "data.db")
    t0 = time.time()
    dest = make_backup(db_path, backup_dir, args.keep_daily, args.keep_monthly, args.tag)
    if dest:
        print("✓ 备份成功：%s（耗时 %.2fs）" % (dest, time.time() - t0))
        return 0
    print("✗ 备份失败，详见日志")
    return 1


if __name__ == "__main__":
    sys.exit(main())
