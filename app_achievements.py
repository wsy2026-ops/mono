# -*- coding: utf-8 -*-
"""统一排行榜与成就系统（站点功能规划第 027 项）。

设计：
  - ach_defs  成就定义（code/title/desc/icon/分组/奖励积分/条件 JSON），种子幂等；
  - ach_user  用户解锁记录（user_id + def_id 唯一）。
  - 条件 JSON: {"k": "score"/"time"/"games"/"coins", "f": 字段, "op": ">"/">="/"<"/"==", "v": 值}
      score/time 读 users 行字段；games = 有成绩的游戏款数；coins = 用户 coins。
  - 判定入口 _ach_check(uid)：扫全部未解锁定义，命中即授予（积分走 award() 流水，
    通知走 add_notification）。
  - 与旧的 game_achievements 表（静默 Shipping 小游戏遗留，只读统计）**互不相干**，
    不要动那张表的数据。
"""
import json
import sqlite3
import time

# ---------------- 成就定义种子 ----------------
# (code, title, desc, icon, group, coins, cond)
ACH_DEFS = [
    # —— 游戏进阶（每款 500 币）——
    ("g24_first", "24点达人", "24 点最高分达到 100", "🎯", "游戏进阶", 500,
     {"k": "score", "f": "high_score_24", "op": ">=", "v": 100}),
    ("gsudoku_first", "数独好手", "数独最高分达到 3000", "🧩", "游戏进阶", 500,
     {"k": "score", "f": "high_score_sudoku", "op": ">=", "v": 3000}),
    ("gjump_first", "跳跳好手", "跳一跳最高分达到 100", "🐰", "游戏进阶", 500,
     {"k": "score", "f": "high_score_jump", "op": ">=", "v": 100}),
    ("gcb_first", "色彩敏锐", "色盲派对最高分达到 20", "🎨", "游戏进阶", 500,
     {"k": "score", "f": "high_score_color_blind", "op": ">=", "v": 20}),
    ("gxy_first", "闲弈好手", "闲弈破阵最高分达到 500", "♟️", "游戏进阶", 500,
     {"k": "score", "f": "high_score_xianyi", "op": ">=", "v": 500}),
    ("ghanoi_first", "九阶登塔", "汉诺塔通关九阶", "🗼", "游戏进阶", 500,
     {"k": "score", "f": "high_score_hanoi", "op": ">=", "v": 9}),
    ("gmine_first", "扫雷好手", "通关扫雷高级", "💣", "游戏进阶", 500,
     {"k": "time", "f": "mine_best_ms_expert", "op": ">", "v": 0}),
    ("gklotski_first", "华容道好手", "通关 6×6 数字华容道", "🀄", "游戏进阶", 500,
     {"k": "time", "f": "best_time_klotski_6", "op": ">", "v": 0}),
    ("gcn_first", "三国华容道好手", "三国华容道四个布局全部通关", "⚔️", "游戏进阶", 500,
     {"k": "all", "fields": ["best_time_klotski_cn_1", "best_time_klotski_cn_2",
                             "best_time_klotski_cn_3", "best_time_klotski_cn_4"],
      "op": ">", "v": 0}),
    ("g2048_2048", "2048 达成", "单局合成 2048 方块", "🔢", "游戏进阶", 500,
     {"k": "score", "f": "max_tile_2048", "op": ">=", "v": 2048}),

    # —— 游戏精通（每款 2000 币）——
    ("g24_master", "24点心算王", "24 点最高分达到 200", "🏆", "游戏精通", 2000,
     {"k": "score", "f": "high_score_24", "op": ">=", "v": 200}),
    ("gsudoku_master", "数独无瑕", "数独最高分 3000 且全程没有出错", "🏆", "游戏精通", 2000,
     {"k": "combo", "conds": [
         {"k": "score", "f": "high_score_sudoku", "op": ">=", "v": 3000},
         {"k": "flag", "f": "sudoku_perfect"}]}),
    ("gjump_master", "中心之王", "跳一跳连续 20 次落在平台中心", "🏆", "游戏精通", 2000,
     {"k": "flag", "f": "jump_center_streak"}),
    ("gcb_master", "色感超人", "色盲派对最高分达到 50", "🏆", "游戏精通", 2000,
     {"k": "score", "f": "high_score_color_blind", "op": ">=", "v": 50}),
    ("gxy_master", "闲弈宗师", "闲弈破阵最高分达到 1000", "🏆", "游戏精通", 2000,
     {"k": "score", "f": "high_score_xianyi", "op": ">=", "v": 1000}),
    ("ghanoi_master", "通天无瑕", "九阶通关 + 理论最少步 + 3 分钟内", "🏆", "游戏精通", 2000,
     {"k": "combo", "conds": [
         {"k": "score", "f": "high_score_hanoi", "op": ">=", "v": 9},
         {"k": "score", "f": "hanoi_best_moves", "op": "==", "v": 511},
         {"k": "time", "f": "hanoi_best_ms", "op": "<=", "v": 180000}]}),
    ("gmine_master", "扫雷无痕", "通关扫雷高级：2 分钟内 或 全程不插旗", "🏆", "游戏精通", 2000,
     {"k": "any", "conds": [
         {"k": "time", "f": "mine_best_ms_expert", "op": "<=", "v": 120000},
         {"k": "flag", "f": "mine_noflag_expert"}]}),
    ("gklotski_master", "华容道宗师", "通关 6×6 数字华容道且 60 秒内", "🏆", "游戏精通", 2000,
     {"k": "time", "f": "best_time_klotski_6", "op": "<=", "v": 60000}),
    ("gcn_master", "三国华容道宗师", "四个布局全通关且每个 30 秒内", "🏆", "游戏精通", 2000,
     {"k": "all", "fields": ["best_time_klotski_cn_1", "best_time_klotski_cn_2",
                             "best_time_klotski_cn_3", "best_time_klotski_cn_4"],
      "op": "<=", "v": 30000}),
    ("g2048_65536", "65536 传说", "单局合成 65536 方块", "👑", "游戏精通", 2000,
     {"k": "score", "f": "max_tile_2048", "op": ">=", "v": 65536}),

    ("gspeed", "秒速狂人", "任一竞速成绩进入 60 秒内", "⚡", "游戏精通", 30,
     {"k": "time_any", "fields": ["best_time_klotski_4", "best_time_klotski_5",
                                  "best_time_klotski_6", "hanoi_best_ms"],
      "op": "<", "v": 60000}),
    # —— 跨游戏 ——
    ("games3", "全能萌芽", "玩过 3 款不同游戏", "🎮", "跨游戏", 30,
     {"k": "games", "op": ">=", "v": 3}),
    ("games5", "半壁江山", "玩过 5 款不同游戏", "🎮", "跨游戏", 80,
     {"k": "games", "op": ">=", "v": 5}),
    ("games_all", "九款全通", "玩过全部 9 款游戏", "👑", "跨游戏", 200,
     {"k": "games", "op": ">=", "v": 9}),
    ("coin_1000", "小富豪", "持有积分达到 1000", "💰", "跨游戏", 50,
     {"k": "coins", "op": ">=", "v": 1000}),
    ("coin_5000", "富甲一方", "持有积分达到 5000", "💰", "跨游戏", 100,
     {"k": "coins", "op": ">=", "v": 5000}),
]

ACH_GROUPS = ["游戏进阶", "游戏精通", "跨游戏"]

_SCHEMA_READY = False


def ensure_schema(db):
    """幂等建表 + 种子。每个进程首次请求时跑一次。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS ach_defs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            desc TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT '🏅',
            group_name TEXT NOT NULL DEFAULT '其它',
            coins INTEGER NOT NULL DEFAULT 0,
            cond_json TEXT NOT NULL DEFAULT '{}',
            sort INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ach_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            def_id INTEGER NOT NULL,
            unlocked_at REAL NOT NULL,
            UNIQUE(user_id, def_id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ach_user_uid ON ach_user(user_id)")
    # 成就判定需要的 users 字段（懒建，失败忽略 = 已存在）
    for _col, _ddl in [
        ("sudoku_perfect", "ALTER TABLE users ADD COLUMN sudoku_perfect INTEGER NOT NULL DEFAULT 0"),
        ("jump_center_streak", "ALTER TABLE users ADD COLUMN jump_center_streak INTEGER NOT NULL DEFAULT 0"),
        ("mine_noflag_expert", "ALTER TABLE users ADD COLUMN mine_noflag_expert INTEGER NOT NULL DEFAULT 0"),
    ]:
        try:
            db.execute(_ddl)
        except Exception:
            pass
    # ⚠️ 2026-10-26：原来是 INSERT OR IGNORE —— 已存在的 code 永远不更新，
    # 改了 ACH_DEFS 的阈值/文案后线上还是旧的。改成 UPSERT（同步全部字段）。
    for i, (code, title, desc, icon, grp, coins, cond) in enumerate(ACH_DEFS):
        db.execute(
            "INSERT INTO ach_defs "
            "(code, title, desc, icon, group_name, coins, cond_json, sort) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET "
            "title=excluded.title, desc=excluded.desc, icon=excluded.icon, "
            "group_name=excluded.group_name, coins=excluded.coins, "
            "cond_json=excluded.cond_json, sort=excluded.sort",
            (code, title, desc, icon, grp, coins, json.dumps(cond, ensure_ascii=False), i))
    db.commit()
    _SCHEMA_READY = True


# ---------------- 判定引擎 ----------------

def _cmp(a, op, b):
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == "==":
        return a == b
    return False


def _user_field(row, key, default=0):
    """sqlite3.Row 安全取值：列不存在返回 default（老行可能缺列）。"""
    try:
        v = row[key]
    except (IndexError, KeyError):
        return default
    return default if v is None else v


def _eval_cond(row, cond):
    k = cond.get("k")
    if k == "time":
        # ★ 修 bug：以前 _user_field 把 None 转 0，导致 time 类条件
        #   （如 best_time_klotski_6 <= 60000）变成 0 <= 60000 恒真，
        #   所有没玩过的人都误解锁了「华容道宗师」「扫雷无痕」。
        #   现在：没有记录（None 或 <=0）一律视为不满足。
        raw = None
        try:
            raw = row[cond["f"]]
        except (IndexError, KeyError):
            raw = None
        if raw is None:
            return False
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return False
        if v <= 0:
            return False
        return _cmp(v, cond["op"], cond["v"])
    if k == "score":
        return _cmp(float(_user_field(row, cond["f"])), cond["op"], cond["v"])
    if k == "time_any":
        for f in cond["fields"]:
            v = float(_user_field(row, f))
            if v > 0 and _cmp(v, cond["op"], cond["v"]):
                return True
        return False
    if k == "all":
        # 所有字段都必须「有记录且满足条件」（三国华容道四个全通关靠它）
        for f in cond["fields"]:
            v = float(_user_field(row, f))
            if not (v > 0 and _cmp(v, cond["op"], cond["v"])):
                return False
        return True
    if k == "flag":
        # 布尔/计数型标记（>=1 视为达成）
        return float(_user_field(row, cond["f"])) >= 1
    if k == "combo":
        # 多条件 AND（汉诺塔「九阶+最少步+3分钟」、数独「3000分+无错」）
        for c in cond["conds"]:
            if not _eval_cond(row, c):
                return False
        return True
    if k == "any":
        # 多条件 OR（扫雷精通：2 分钟内 或 全程不插旗）
        for c in cond["conds"]:
            if _eval_cond(row, c):
                return True
        return False
    if k == "games":
        n = 0
        for f in _GAME_FIELDS_ALL():
            if float(_user_field(row, f)) > 0:
                n += 1
        return _cmp(n, cond["op"], cond["v"])
    if k == "coins":
        return _cmp(float(_user_field(row, "coins")), cond["op"], cond["v"])
    return False


def _GAME_FIELDS_ALL():
    """九款游戏的成绩字段集合（调用方注入，避免循环 import）。"""
    from app import _GAME_SCORE_ROWS, _GAME_TIME_ROWS
    return [k for k, _ in _GAME_SCORE_ROWS] + [k for k, _ in _GAME_TIME_ROWS]


def check(uid, award_fn, notif_fn=None):
    """扫描该用户全部未解锁成就，命中即授予。返回新解锁的 def 行 list。"""
    db = _DB()
    ensure_schema(db)
    row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return []
    done = {r["def_id"] for r in
            db.execute("SELECT def_id FROM ach_user WHERE user_id=?", (uid,)).fetchall()}
    granted = []
    for d in db.execute("SELECT * FROM ach_defs ORDER BY sort, id").fetchall():
        if d["id"] in done:
            continue
        try:
            cond = json.loads(d["cond_json"])
        except Exception:
            continue
        if not _eval_cond(row, cond):
            continue
        try:
            db.execute("INSERT OR IGNORE INTO ach_user (user_id, def_id, unlocked_at) "
                       "VALUES (?,?,?)", (uid, d["id"], time.time()))
            db.commit()
        except sqlite3.Error:
            continue
        if d["coins"]:
            try:
                award_fn(uid, d["coins"], "achievement",
                         "解锁成就「%s」" % d["title"], ref_id=d["id"])
            except Exception:
                pass
        if notif_fn:
            try:
                notif_fn(uid, "achievement",
                         "🏅 解锁成就「%s」%s" % (d["title"],
                                                 ("，+%d 积分" % d["coins"]) if d["coins"] else ""),
                         "/achievements")
            except Exception:
                pass
        granted.append(d)
    return granted


def _groups_for(uid=None):
    """按分组产出图鉴数据。uid=None 时全部 locked（访客视图）。"""
    db = _DB()
    ensure_schema(db)
    mine = {}
    if uid is not None:
        mine = {r["def_id"]: r["unlocked_at"] for r in
                db.execute("SELECT def_id, unlocked_at FROM ach_user WHERE user_id=?",
                           (uid,)).fetchall()}
    counts = unlock_counts()
    groups = []
    for g in ACH_GROUPS:
        items = []
        got = 0
        for d in db.execute("SELECT * FROM ach_defs WHERE group_name=? ORDER BY sort, id",
                            (g,)).fetchall():
            unlocked = d["id"] in mine
            got += 1 if unlocked else 0
            items.append({"id": d["id"], "icon": d["icon"], "title": d["title"],
                          "desc": d["desc"], "coins": d["coins"],
                          "unlocked": unlocked, "unlocked_count": counts.get(d["id"], 0)})
        groups.append({"name": g, "list": items, "got": got, "total": len(items)})
    return groups


def user_summary(uid):
    """个人主页/图鉴用：已解锁数、总分、按分组明细（items 已扁平化）。"""
    groups = _groups_for(uid)
    total = sum(g["total"] for g in groups)
    got = sum(g["got"] for g in groups)
    coins = sum(it["coins"] for g in groups for it in g["list"] if it["unlocked"])
    return {"groups": groups, "got": got, "total": total, "coins": coins,
            "pct": int(got * 100 / total) if total else 0}


def all_groups_public():
    """访客图鉴：全部定义 + 全站解锁数，全部标记未解锁。"""
    return _groups_for(None)


def ach_score_rank(limit=20):
    """成就总分榜：每用户 Σ已解锁成就积分。"""
    db = _DB()
    ensure_schema(db)
    return db.execute("""
        SELECT u.id AS uid, u.username, COALESCE(SUM(d.coins),0) AS score,
               COUNT(*) AS cnt
        FROM ach_user a JOIN ach_defs d ON d.id = a.def_id
        JOIN users u ON u.id = a.user_id
        GROUP BY a.user_id
        ORDER BY score DESC, cnt DESC
        LIMIT ?
    """, (limit,)).fetchall()


def unlock_counts():
    """图鉴墙：每个成就的全站解锁人数。"""
    db = _DB()
    ensure_schema(db)
    return {r["def_id"]: r["c"] for r in db.execute(
        "SELECT def_id, COUNT(*) c FROM ach_user GROUP BY def_id").fetchall()}


def title_for(score):
    """成就总分 → 称号（全局榜展示用）。"""
    if score >= 300:
        return "传奇玩家"
    if score >= 200:
        return "大师"
    if score >= 120:
        return "精英"
    if score >= 60:
        return "熟手"
    if score >= 10:
        return "新手"
    return "无名"


# ---------------- db 句柄 ----------------
# 由 app.py 注入（避免循环 import）
_DB = None


def bind_db(get_db):
    global _DB
    _DB = get_db
