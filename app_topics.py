# -*- coding: utf-8 -*-
"""app_topics.py — 站点功能规划 029（标签体系与话题热榜）+ 030（版块化与主题订阅）数据层。

版块层**不重造轮子**：boards = 现有 forum_sections（5 个分区现成，/forum?section=
已支持过滤）。本模块新增的是「主题」层（threads，挂在版块下）与「标签」层（tags）。

表（全部幂等 ensure，不走 init_db）：
    tags / posts_tags / tag_subs          029 标签体系
    threads / thread_subs                 030 主题层
    tag_hot                               029 每日热榜聚合落库
    posts.thread_id 列                    030（_add_column 补）

调用方（app.py）在 before_request 里 ensure_topics() 一次。
"""
import json
import re
import sqlite3
import time

_SCHEMA_READY = False
_DB = None

MAX_TAGS_PER_POST = 5
_TAG_RE = re.compile(r"[#,，]?\s*([^\s#,，、/]{1,20})")


def bind_db(get_db):
    global _DB
    _DB = get_db


# ---------------- 建表 + 迁移 ----------------

def ensure_schema(db):
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            use_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS posts_tags (
            post_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (post_id, tag_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_subs (
            user_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, tag_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            post_count INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS thread_subs (
            user_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, thread_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_hot (
            day TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            post_cnt INTEGER NOT NULL DEFAULT 0,
            interact_cnt INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (day, tag_id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_posts_tags_tag ON posts_tags(tag_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_threads_board ON threads(board_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_thread_subs_t ON thread_subs(thread_id)")
    _add_column(db, "posts", "thread_id", "INTEGER NOT NULL DEFAULT 0")
    db.commit()
    _migrate_threads(db)
    _SCHEMA_READY = True


def _add_column(db, table, col, ddl):
    try:
        db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ddl))
        db.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在


def _migrate_threads(db):
    """030 迁移：每个版块建一个默认主题；存量帖子按 section 归入默认主题。

    幂等：默认主题按 slug 唯一（s{board}-default）；UPDATE 只碰 thread_id=0 的行。
    """
    for sec in db.execute("SELECT id, name FROM forum_sections").fetchall():
        slug = "s%d-default" % sec["id"]
        db.execute(
            "INSERT OR IGNORE INTO threads (board_id, name, slug, description, is_default, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (sec["id"], "综合", slug, "%s的默认主题" % sec["name"], 1, time.time()))
        t = db.execute("SELECT id FROM threads WHERE slug=?", (slug,)).fetchone()
        db.execute("UPDATE posts SET thread_id=? WHERE thread_id=0 AND section_id=?",
                   (t["id"], sec["id"]))
    # section_id 指向不存在分区的孤儿帖（防御）
    t0 = db.execute("SELECT id FROM threads WHERE slug='s1-default'").fetchone()
    if t0:
        db.execute("UPDATE posts SET thread_id=? WHERE thread_id=0", (t0["id"],))
    db.commit()
    _refresh_thread_counts(db)


def _refresh_thread_counts(db):
    db.execute(
        "UPDATE threads SET post_count = "
        "(SELECT COUNT(*) FROM posts p WHERE p.thread_id = threads.id "
        " AND p.is_deleted=0 AND p.recycle=0)")
    db.commit()


# ---------------- 029 标签 ----------------

def parse_tags(raw, limit=MAX_TAGS_PER_POST):
    """从用户输入提取标签列表：支持逗号/空格/井号分隔，去重保序，限长。"""
    if not raw:
        return []
    out = []
    for m in _TAG_RE.finditer(str(raw)):
        name = m.group(1).strip(".,!?;:'\"")
        if not name or name in out:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


_STOP = set("的了是在我有和就不人这那一部被把对于与及或也很会要还能都从上下到关于")


def auto_tags(title, limit=3):
    """029「自动提取」：标题分词取标签（用户不选时的兜底，冷启动让标签体系有数据）。

    规则：按标点/空格切 → 去停用词 → 长度 2-12 → 最多 limit 个。
    中文按连续段切（无分词器，够用且不会造出怪词）。
    """
    if not title:
        return []
    parts = re.split(r"[\s,，、。？?！!；;：:\-—…·/\\|()（）\[\]【】]+", str(title))
    out = []
    for p in parts:
        p = p.strip(".,!?;:'\"#~")
        # 剥离开头的停用字（"关于本站点" → "本站点"）
        while p and p[0] in _STOP:
            p = p[1:]
        p = p.strip(".,!?;:'\"#~")
        if not (2 <= len(p) <= 12):
            continue
        if p in _STOP or all(c in _STOP for c in p):
            continue
        if p.isdigit():
            continue
        if p in out:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def tag_ids_for(names):
    """按名字取/建 tag，返回 id 列表。"""
    db = _DB()
    ids = []
    now = time.time()
    for n in names:
        row = db.execute("SELECT id FROM tags WHERE name=?", (n,)).fetchone()
        if row:
            ids.append(row["id"])
        else:
            cur = db.execute("INSERT INTO tags (name, use_count, created_at) VALUES (?,0,?)",
                             (n, now))
            ids.append(cur.lastrowid)
    return ids


def set_post_tags(post_id, names):
    """全量重设帖子标签（新建/删除差量），维护 use_count。"""
    db = _DB()
    old = {r["tag_id"] for r in
           db.execute("SELECT tag_id FROM posts_tags WHERE post_id=?", (post_id,)).fetchall()}
    new = set(tag_ids_for(names)) if names else set()
    for tid in old - new:
        db.execute("DELETE FROM posts_tags WHERE post_id=? AND tag_id=?", (post_id, tid))
        db.execute("UPDATE tags SET use_count=MAX(0, use_count-1) WHERE id=?", (tid,))
    for tid in new - old:
        db.execute("INSERT OR IGNORE INTO posts_tags (post_id, tag_id) VALUES (?,?)",
                   (post_id, tid))
        db.execute("UPDATE tags SET use_count=use_count+1 WHERE id=?", (tid,))
    db.commit()


def post_tags(post_id):
    db = _DB()
    return db.execute(
        "SELECT t.id, t.name FROM posts_tags pt JOIN tags t ON t.id=pt.tag_id "
        "WHERE pt.post_id=? ORDER BY t.name", (post_id,)).fetchall()


def tags_map(post_ids):
    """批量：{post_id: [tag名...]}，列表页避免 N+1。"""
    db = _DB()
    out = {pid: [] for pid in post_ids}
    if not post_ids:
        return out
    qs = ",".join("?" * len(post_ids))
    for r in db.execute(
            "SELECT pt.post_id, t.name FROM posts_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.post_id IN (%s) ORDER BY t.name" % qs, list(post_ids)).fetchall():
        out[r["post_id"]].append(r["name"])
    return out


def tag_page(name, page=1, per=20, user_id=None):
    """标签页：帖子列表（分页）+ 订阅数。"""
    db = _DB()
    tag = db.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
    if not tag:
        return None, [], 0, False
    total = db.execute(
        "SELECT COUNT(*) c FROM posts_tags pt JOIN posts p ON p.id=pt.post_id "
        "WHERE pt.tag_id=? AND p.is_deleted=0 AND p.recycle=0", (tag["id"],)).fetchone()["c"]
    rows = db.execute(
        "SELECT p.id, p.title, p.username, p.created_at, p.comment_count, p.likes, p.section_id "
        "FROM posts_tags pt JOIN posts p ON p.id=pt.post_id "
        "WHERE pt.tag_id=? AND p.is_deleted=0 AND p.recycle=0 "
        "ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
        (tag["id"], per, (page - 1) * per)).fetchall()
    subbed = False
    if user_id:
        subbed = bool(db.execute(
            "SELECT 1 FROM tag_subs WHERE user_id=? AND tag_id=?",
            (user_id, tag["id"])).fetchone())
    return tag, rows, total, subbed


def toggle_tag_sub(user_id, tag_id):
    db = _DB()
    row = db.execute("SELECT 1 FROM tag_subs WHERE user_id=? AND tag_id=?",
                     (user_id, tag_id)).fetchone()
    if row:
        db.execute("DELETE FROM tag_subs WHERE user_id=? AND tag_id=?", (user_id, tag_id))
        db.commit()
        return False
    db.execute("INSERT OR IGNORE INTO tag_subs (user_id, tag_id, created_at) VALUES (?,?,?)",
               (user_id, tag_id, time.time()))
    db.commit()
    return True


def related_posts(post_id, limit=6):
    """详情页：同标签的相关帖（排除自己；同标签数多的优先）。"""
    db = _DB()
    return db.execute("""
        SELECT p.id, p.title, p.username, p.created_at
        FROM posts_tags pt1
        JOIN posts_tags pt2 ON pt2.tag_id = pt1.tag_id AND pt2.post_id != ?
        JOIN posts p ON p.id = pt2.post_id
        WHERE pt1.post_id = ? AND p.is_deleted=0 AND p.recycle=0
        GROUP BY p.id
        ORDER BY COUNT(*) DESC, p.created_at DESC
        LIMIT ?
    """, (post_id, post_id, limit)).fetchall()


# ---------------- 029 热榜聚合 ----------------

def aggregate_hot(days=7, top=20):
    """聚合近 N 天标签的帖子量与互动量 → tag_hot（按天落库，可回溯）。

    score = 帖子数*10 + 评论数*2 + 点赞数*3（互动加权）。
    返回当天行数。由定时线程每天调用一次。
    """
    db = _DB()
    since = time.time() - days * 86400
    rows = db.execute("""
        SELECT pt.tag_id,
               COUNT(DISTINCT p.id) AS post_cnt,
               COALESCE(SUM(p.comment_count),0) + COALESCE(SUM(p.likes),0) AS interact_cnt
        FROM posts_tags pt
        JOIN posts p ON p.id = pt.post_id
        WHERE p.created_at > ? AND p.is_deleted=0 AND p.recycle=0
        GROUP BY pt.tag_id
        ORDER BY post_cnt DESC, interact_cnt DESC
        LIMIT ?
    """, (since, top)).fetchall()
    day = time.strftime("%Y-%m-%d")
    n = 0
    for r in rows:
        score = r["post_cnt"] * 10 + r["interact_cnt"]
        db.execute(
            "INSERT OR REPLACE INTO tag_hot (day, tag_id, post_cnt, interact_cnt, score) "
            "VALUES (?,?,?,?,?)",
            (day, r["tag_id"], r["post_cnt"], r["interact_cnt"], score))
        n += 1
    db.commit()
    return n


def hot_list(day=None, limit=15):
    """话题热榜：默认取最近一次聚合（不传 day 取最新）。"""
    db = _DB()
    if not day:
        day = db.execute("SELECT MAX(day) d FROM tag_hot").fetchone()["d"]
    if not day:
        return [], None
    rows = db.execute("""
        SELECT h.tag_id, h.post_cnt, h.interact_cnt, h.score, t.name
        FROM tag_hot h JOIN tags t ON t.id = h.tag_id
        WHERE h.day = ? ORDER BY h.score DESC, h.post_cnt DESC LIMIT ?
    """, (day, limit)).fetchall()
    return rows, day


# ---------------- 030 主题 ----------------

def boards_with_threads():
    """版块列表 + 每版块下的主题（论坛首页/侧栏用）。"""
    db = _DB()
    out = []
    for b in db.execute("SELECT * FROM forum_sections WHERE is_hidden=0 "
                        "ORDER BY sort_order ASC").fetchall():
        ts = db.execute(
            "SELECT * FROM threads WHERE board_id=? ORDER BY is_default DESC, sort_order ASC, id ASC",
            (b["id"],)).fetchall()
        out.append({"board": b, "threads": ts})
    return out


def thread_page(thread_id, page=1, per=20, user_id=None):
    db = _DB()
    t = db.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
    if not t:
        return None, [], 0, False, None
    total = t["post_count"]
    rows = db.execute(
        "SELECT id, title, username, created_at, comment_count, likes "
        "FROM posts WHERE thread_id=? AND is_deleted=0 AND recycle=0 "
        "ORDER BY is_pinned DESC, created_at DESC LIMIT ? OFFSET ?",
        (thread_id, per, (page - 1) * per)).fetchall()
    subbed = False
    if user_id:
        subbed = bool(db.execute(
            "SELECT 1 FROM thread_subs WHERE user_id=? AND thread_id=?",
            (user_id, thread_id)).fetchone())
    board = db.execute("SELECT * FROM forum_sections WHERE id=?", (t["board_id"],)).fetchone()
    return t, rows, total, subbed, board


def toggle_thread_sub(user_id, thread_id):
    db = _DB()
    row = db.execute("SELECT 1 FROM thread_subs WHERE user_id=? AND thread_id=?",
                     (user_id, thread_id)).fetchone()
    if row:
        db.execute("DELETE FROM thread_subs WHERE user_id=? AND thread_id=?",
                   (user_id, thread_id))
        db.commit()
        return False
    db.execute("INSERT OR IGNORE INTO thread_subs (user_id, thread_id, created_at) "
               "VALUES (?,?,?)", (user_id, thread_id, time.time()))
    db.commit()
    return True


def notify_thread_subs(thread_id, post_id, title, author):
    """030：主题有新帖 → 通知订阅者（未读缓存失效已由 add_notification 负责）。"""
    db = _DB()
    subs = db.execute(
        "SELECT user_id FROM thread_subs WHERE thread_id=? AND user_id!=?",
        (thread_id, author)).fetchall()
    # 延迟 import 避免环（app.py 底部 import 本模块）
    from app import add_notification, url_for
    for s in subs:
        add_notification(s["user_id"], "thread",
                         "📌 关注的主题有新帖：《%s》" % title[:30],
                         url_for("view_post", post_id=post_id))


def threads_for_post_select(board_id):
    """发帖表单：某版块下的主题下拉。"""
    db = _DB()
    return db.execute(
        "SELECT id, name, is_default FROM threads WHERE board_id=? "
        "ORDER BY is_default DESC, sort_order ASC, id ASC", (board_id,)).fetchall()


def on_post_created(post_id, thread_id):
    """发帖成功后：刷新主题帖子数（posts.thread_id 已在 INSERT 时写入）。"""
    db = _DB()
    db.execute("UPDATE threads SET post_count=post_count+1 WHERE id=?", (thread_id,))
    db.commit()


# ---------------- 029 每日热榜定时任务 ----------------

def start_hot_loop(app):
    """daemon 线程：启动 90 秒后首跑，之后每 24 小时聚合一次热榜。

    全兜异常 —— 定时任务趴窝不影响站点任何功能（热榜页取空列表而已）。
    """
    import threading

    def _loop():
        import time as _t
        _t.sleep(90)
        while True:
            try:
                with app.app_context():
                    aggregate_hot()
            except Exception:
                pass
            _t.sleep(86400)

    threading.Thread(target=_loop, name="tag-hot-daily", daemon=True).start()
