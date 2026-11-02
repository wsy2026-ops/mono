#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pg_backfill.py — 把 SQLite 全量补进 PostgreSQL 影子库（052 收尾）

干什么
------
1. 从 SQLite 的 `PRAGMA table_info` 生成 PG 建表语句（表不存在才建）
2. 逐表 `pg_shadow.sync_full()` 搬数据
3. `pg_shadow.verify()` 校验行数

不干什么
--------
- **不碰 SQLite**（只读）
- **不改站点配置**（不切读写，双写照旧）
- 不迁 FTS 虚拟表（`messages_fts*` 是 SQLite 专属，PG 有自己的全文方案）

用法
----
    python3 pg_backfill.py            # 建表 + 同步 + 校验
    python3 pg_backfill.py --check    # 只看现状，不动
"""
import os
import sqlite3
import subprocess
import sys

DB = "/opt/mono/app/data.db"

# SQLite 弱类型，这几张表同一列混存了数字和字符串（实测）：
#   user_actions.target_id       → 混了 'jump'
#   game_achievements.unlocked_at → 混了 "2026-07-07 08:34:02"
#   silence_*.created_at          → 混了 "2026-08-08 04:57:49"
# 影子库不需要类型严格，这些表除主键外一律建成 TEXT，避免转换失败。
FORCE_TEXT_TABLES = {
    "user_actions", "game_achievements", "silence_save", "silence_user_meta",
    "changelog", "kubition_saves_history",
}


def load_service_env():
    """cron/独立进程拿不到 systemd 的 Environment，得手动捞。"""
    try:
        import shlex
        out = subprocess.run(
            ["systemctl", "show", "mono", "-p", "Environment", "--value"],
            capture_output=True, text=True, timeout=8).stdout.strip()
        for kv in shlex.split(out):
            if "=" in kv:
                k, v = kv.split("=", 1)
                if k and v:
                    os.environ.setdefault(k, v)
    except Exception:
        pass


load_service_env()
sys.path.insert(0, "/opt/mono/app")
import psycopg2          # noqa: E402
import pg_shadow         # noqa: E402

DSN = pg_shadow.DSN


def map_type(ctype, is_pk):
    """SQLite 类型 → PG 类型。"""
    t = (ctype or "").upper()
    if "INT" in t:
        return "BIGINT"
    if any(x in t for x in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if any(x in t for x in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE PRECISION"
    if "BLOB" in t or not t:
        return "BYTEA"
    if any(x in t for x in ("NUMERIC", "DECIMAL")):
        return "NUMERIC"
    if "BOOL" in t:
        return "BOOLEAN"
    if "DATE" in t or "TIME" in t:
        return "DOUBLE PRECISION"      # 项目里时间全是 unix 时间戳 float
    return "TEXT"


def pg_ddl(lite, table):
    """从 PRAGMA table_info 生成 PG 建表语句。"""
    cols = lite.execute("PRAGMA table_info(%s)" % table).fetchall()
    if not cols:
        return None
    force_text = table in FORCE_TEXT_TABLES
    # cols: (cid, name, type, notnull, dflt_value, pk)
    pk_cols = [c for c in cols if c[5]]
    single_int_pk = (len(pk_cols) == 1 and "INT" in (pk_cols[0][2] or "").upper())

    parts = []
    for cid, name, ctype, notnull, dflt, pk in cols:
        if single_int_pk and pk == 1:
            parts.append('"%s" BIGSERIAL PRIMARY KEY' % name)
            continue
        col_type = "TEXT" if force_text else map_type(ctype, pk == 1)
        p = '"%s" %s' % (name, col_type)
        if notnull and not (single_int_pk and pk == 1):
            p += " NOT NULL"
        if dflt is not None:
            d = str(dflt).strip()
            # 布尔值 / 数字直接用，字符串加引号
            if d.upper() in ("NULL", "CURRENT_TIMESTAMP", "CURRENT_TIME", "CURRENT_DATE"):
                pass
            elif d.replace(".", "").replace("-", "").isdigit() or d.upper() in ("TRUE", "FALSE"):
                p += " DEFAULT %s" % d
            else:
                p += " DEFAULT %s" % d.replace("'", "''").join(["'", "'"])
        parts.append(p)

    if pk_cols and not single_int_pk:
        parts.append("PRIMARY KEY (%s)" % ", ".join('"%s"' % c[1] for c in pk_cols))

    return 'CREATE TABLE IF NOT EXISTS "%s" (%s)' % (table, ", ".join(parts))


def main():
    check_only = "--check" in sys.argv
    lite = sqlite3.connect(DB)
    lite.row_factory = sqlite3.Row

    tables = [r[0] for r in lite.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    normal = [t for t in tables if "fts" not in t.lower()]
    print("SQLite 普通表: %d 张（跳过 FTS 虚拟表 %d 张）" % (len(normal), len(tables) - len(normal)))
    print()

    try:
        pg = psycopg2.connect(DSN, connect_timeout=5)
        pg.autocommit = True
    except Exception as e:
        print("连不上 PG: %s" % e)
        return 1

    created = skipped = failed = 0
    for t in normal:
        ddl = pg_ddl(lite, t)
        if not ddl:
            skipped += 1
            continue
        try:
            with pg.cursor() as cur:
                cur.execute(ddl)
            created += 1
        except Exception as e:
            failed += 1
            print("  建表失败 %-26s %s" % (t, str(e)[:70]))

    print("建表: 新建/已存在 %d 张，失败 %d 张" % (created, failed))
    if check_only:
        return 0

    print()
    print("=== 同步数据 ===")
    total_rows = 0
    sync_fail = []
    for t in normal:
        try:
            n, why = pg_shadow.sync_full(t, DB, batch=500)
            if n:
                print("  %-28s +%d 行" % (t, n))
                total_rows += n
            elif why not in ("ok", "already_synced") and not why.startswith("already_synced"):
                sync_fail.append((t, why))
        except Exception as e:
            sync_fail.append((t, str(e)[:60]))

    print()
    print("共同步 %d 行" % total_rows)
    if sync_fail:
        print("同步失败/跳过 %d 张:" % len(sync_fail))
        for t, why in sync_fail[:12]:
            print("  %-28s %s" % (t, why))

    print()
    print("=== 校验（SQLite vs PG 行数）===")
    ok = diff = 0
    bad = []
    for t in normal:
        try:
            a, b, same = pg_shadow.verify(t, DB)
        except Exception:
            continue
        if same:
            ok += 1
        else:
            diff += 1
            bad.append((t, a, b))
    print("  一致 %d 张 / 不一致 %d 张" % (ok, diff))
    if bad:
        print()
        for t, a, b in sorted(bad, key=lambda x: -(x[1] if x[1] > 0 else 0))[:15]:
            print("  %-28s sqlite=%-6s pg=%-6s" % (t, a, b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
