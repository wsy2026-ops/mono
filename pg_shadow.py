# -*- coding: utf-8 -*-
"""pg_shadow.py — PostgreSQL 影子库（052 渐进迁移 · 阶段 1：points_log / messages 双写）。

背景：SQLite 单写锁是并发硬顶（见规划 052）。渐进迁移三步走：
  阶段 1（本模块）：增长最快的两张表双写 —— 写主库（SQLite）的同时镜像到
    PostgreSQL 影子库，读仍走 SQLite。影子失败**静默**（只记事件），
    绝不影响主站写入。
  阶段 2：影子读比对稳定后，这两张表的读切 PG。
  阶段 3：逐批迁移其余表，最终全量切换（076）。

设计约束：
  - 零侵入接线：每个写入点只需加一行 pg_shadow.mirror('表', sql, params)
  - SQL 方言转换只做最小集：? → %s（这些 INSERT 无字符串字面量占位问题，
    且用引号感知的替换，字面量里的 ? 不动）
  - 开关：环境变量 MONO_PG_SHADOW=1 开启；关掉后所有调用变成 no-op
  - DSN：环境变量 MONO_PG_DSN，默认连本机 mono_shadow 库

行数比对：verify(table) 返回 (sqlite_n, pg_n, ok)，供每日 cron 影子校验。
"""
import os
import re
import sqlite3
import threading
import time

import psycopg2
import psycopg2.extras

# ---------------- 配置 ----------------
DSN = os.environ.get("MONO_PG_DSN") or "dbname=mono_shadow user=postgres password=mono_shadow_2026 host=127.0.0.1 port=5432"
ENABLED = os.environ.get("MONO_PG_SHADOW") == "1"

_local = threading.local()

# 已做过全量同步的表（本进程内记住，避免每次写入都试同步）
_synced = set()
_sync_lock = threading.Lock()


def enabled():
    return ENABLED


def _conn():
    """线程内复用连接。失败返回 None（调用方静默）。"""
    c = getattr(_local, "pg", None)
    if c is not None:
        try:
            c.cursor().execute("SELECT 1")
            return c
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            _local.pg = None
    try:
        c = psycopg2.connect(DSN, connect_timeout=3)
        c.autocommit = True
        _local.pg = c
        return c
    except Exception:
        return None


def _qmarks_to_percents(sql):
    """? 占位符 → %s。只在引号外替换（字符串字面量里的 ? 保持不动）。"""
    out = []
    in_str = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_INSERT_RE = re.compile(r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES", re.I)


def mirror(table, sql, params, row_id=None):
    """把一条 SQLite INSERT 镜像到 PG 影子库。任何失败都静默（记 log 事件）。

    row_id：SQLite 的自增 id（写入后拿 lastrowid 传进来）—— 影子库保持同 id，
    后续行数/逐行比对才对得上。幂等靠 ON CONFLICT (id) DO NOTHING。
    """
    if not ENABLED:
        return True
    try:
        m = _INSERT_RE.search(sql)
        if not m:
            return False  # 不是标准 INSERT 形态，不猜
        cols = [c.strip() for c in m.group(2).split(",")]
        vals = list(params)
        if row_id is not None and "id" not in cols:
            cols.append("id")
            vals.append(row_id)
        pg_sql = "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT (id) DO NOTHING" % (
            table, ",".join(cols), ",".join(["%s"] * len(cols)))
        conn = _conn()
        if conn is None:
            return False
        with conn.cursor() as cur:
            cur.execute(pg_sql, tuple(vals))
        return True
    except Exception as e:
        try:
            from app import _log_mod
            _log_mod.event("pg_shadow_mirror_fail", table=table, err=str(e)[:120])
        except Exception:
            pass
        return False


def sync_full(table, db_path, batch=500):
    """全量同步一张表（SQLite → PG）。用于初次对齐或修复漂移。

    返回 (copied, skipped_reason)。已同步过且 PG 行数 >= SQLite 行数则跳过。
    """
    if not ENABLED:
        return 0, "disabled"
    conn = _conn()
    if conn is None:
        return 0, "pg_unreachable"
    try:
        lite = sqlite3.connect(db_path)
        lite.row_factory = sqlite3.Row
        n_lite = lite.execute("SELECT COUNT(*) c FROM %s" % table).fetchone()["c"]
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM %s" % table)
            n_pg = cur.fetchone()[0]
        if n_pg >= n_lite:
            return 0, "already_synced(pg=%d,lite=%d)" % (n_pg, n_lite)
        # 按 id 增量搬（PG 已有前 n_pg 行的假设：全量同步从空开始，幂等靠
        # ON CONFLICT (id) DO NOTHING）
        # 主键列不一定是 id（email_codes / board_game_stats 等都是复合主键）
        pk_col = None
        try:
            info = lite.execute("PRAGMA table_info(%s)" % table).fetchall()
            pks = [c[1] for c in info if c[5]]
            if len(pks) == 1:
                pk_col = pks[0]
        except Exception:
            pk_col = None

        order_sql = (' ORDER BY "%s"' % pk_col) if pk_col else ""
        rows = lite.execute("SELECT * FROM %s%s" % (table, order_sql)).fetchall()
        cols = rows[0].keys() if rows else []
        if not cols:
            return 0, "empty"
        ph = ",".join(["%s"] * len(cols))
        # 列名必须加引号：mall_items 有 desc 列，PG 里是保留字
        col_sql = ",".join('"%s"' % c for c in cols)
        conflict = (' ON CONFLICT ("%s") DO NOTHING' % pk_col) if pk_col else ""
        pg_sql = 'INSERT INTO %s (%s) VALUES (%s)%s' % (table, col_sql, ph, conflict)
        n = 0
        with conn.cursor() as cur:
            for i in range(0, len(rows), batch):
                chunk = [tuple(r) for r in rows[i:i + batch]]
                cur.executemany(pg_sql, chunk)
                n += len(chunk)
        # 修序列，避免后续 INSERT 撞 id（表名走字符串拼接 —— 参数化会被加引号变非法 SQL）
        with conn.cursor() as cur:
            if pk_col:
                cur.execute("SELECT setval(pg_get_serial_sequence('%s', '%s'), "
                            'GREATEST((SELECT COALESCE(MAX("%s"), 0) FROM %s), 1))' % (
                                table, pk_col, pk_col, table))
        lite.close()
        with _sync_lock:
            _synced.add(table)
        return n, "ok"
    except Exception as e:
        try:
            from app import _log_mod
            _log_mod.event("pg_shadow_sync_fail", table=table, err=str(e)[:120])
        except Exception:
            pass
        return 0, "error:%s" % str(e)[:80]


def verify(table, db_path):
    """行数比对（影子校验）。返回 (sqlite_n, pg_n, ok)。"""
    try:
        lite = sqlite3.connect(db_path)
        n_lite = lite.execute("SELECT COUNT(*) c FROM %s" % table).fetchone()[0]
        lite.close()
        conn = _conn()
        if conn is None:
            return n_lite, -1, False
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM %s" % table)
            n_pg = cur.fetchone()[0]
        return n_lite, n_pg, n_lite == n_pg
    except Exception:
        return -1, -1, False


def repair_missing(table, db_path, limit=5000):
    """把「SQLite 有、PG 没有」的行按主键补进去，返回补入条数。

    为什么需要这个：mirror() 是**静默失败**的（PG 抖一下、连接超时，这条就永久丢了），
    而 sync_full() 假设「PG 一定是 SQLite 的前缀」，中间一旦有洞就永远补不上。
    2026-11-02 实测：11-01 20:27~21:06 有 56 条积分流水没镜像进去，
    缺口一直挂到第二天才发现 —— 靠人肉对账太晚，让每日校验自己修。
    """
    if not ENABLED:
        return 0
    conn = _conn()
    if conn is None:
        return 0
    lite = sqlite3.connect(db_path)
    lite.row_factory = sqlite3.Row
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM %s" % table)
            pids = {r[0] for r in cur.fetchall()}
        lids = {r[0] for r in lite.execute("SELECT id FROM %s" % table)}
        missing = sorted(lids - pids)[:limit]
        n = 0
        for pid in missing:
            row = lite.execute("SELECT * FROM %s WHERE id=?" % table, (pid,)).fetchone()
            if row is None:
                continue
            d = dict(row)
            cols = list(d.keys())
            sql = "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT (id) DO NOTHING" % (
                table, ",".join('"%s"' % c for c in cols), ",".join(["%s"] * len(cols)))
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(d[c] for c in cols))
                n += 1
            except Exception:
                # 类型/约束问题：别硬撑，剩下的留给人工，上层会继续报 mismatch
                break
        return n
    finally:
        lite.close()


def ensure_synced(table, db_path):
    """进程内首次对该表做全量同步（双写上线时对齐历史数据）。"""
    if not ENABLED:
        return
    with _sync_lock:
        if table in _synced:
            return
    sync_full(table, db_path)
    with _sync_lock:
        _synced.add(table)

# ---------------- 阶段 2：读路径 ----------------
READ_ENABLED = os.environ.get("MONO_PG_READ") == "1"

# 命中统计（进程内；admin 面板/日志消费）
STATS = {"pg_ok": 0, "pg_fail": 0, "lite_fb": 0}
_stats_lock = threading.Lock()


def read_enabled():
    return ENABLED and READ_ENABLED


def _dialect(sql):
    """SQLite → PG 读查询方言最小集。"""
    s = sql
    # IFNULL(a,b) → COALESCE(a,b)（admin 积分审计在用）
    s = re.sub(r"\bIFNULL\s*\(", "COALESCE(", s, flags=re.I)
    # datetime(x,'unixepoch') → to_timestamp(x)
    # （datetime(x,'unixepoch','localtime') 形态暂不支持，遇到会 PG 报错走回落）
    s = re.sub(r"datetime\s*\(\s*([^,]+?)\s*,\s*'unixepoch'\s*\)",
               r"to_timestamp(\1)", s, flags=re.I)
    return s


def pg_read(sql, params=()):
    """在 PG 影子库上执行一条**读**查询，返回 list[dict]。

    方言适配 + ? → %s。任何失败都抛异常（调用方负责回落 SQLite）。
    """
    if not read_enabled():
        raise RuntimeError("pg_read disabled")
    conn = _conn()
    if conn is None:
        raise RuntimeError("pg unreachable")
    pg_sql = _qmarks_to_percents(_dialect(sql))
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(pg_sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def read_rows(db, sql, params=()):
    """阶段 2 统一读封装：PG 影子库优先，任何失败回落 SQLite。

    返回 list[dict]（与 sqlite3.Row 的 ['col'] 访问兼容）。
    统计写 STATS（pg_ok / pg_fail / lite_fb）。
    """
    if read_enabled():
        try:
            rows = pg_read(sql, params)
            with _stats_lock:
                STATS["pg_ok"] += 1
            return rows
        except Exception:
            with _stats_lock:
                STATS["pg_fail"] += 1
    with _stats_lock:
        STATS["lite_fb"] += 1
    return [dict(r) for r in db.execute(sql, params).fetchall()]
