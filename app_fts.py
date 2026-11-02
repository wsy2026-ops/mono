# -*- coding: utf-8 -*-
"""app_fts.py — 站点功能规划第 036 项 · 私聊全文检索。

messages 表 FTS5 索引（trigram 分词，中文可子串匹配）。
- 建表 + INSERT/DELETE 触发器同步（与消息写入同事务，强一致，微秒级成本）；
- ensure 幂等：首次回填存量消息；trigram 不可用时降级 unicode61（英文仍可用，不挂站）；
- 搜索只限私聊（is_private=1）且调用者是对话双方之一。
"""
import re
import sqlite3

_READY = False
_DB = None
_TRIGRAM = True


def bind_db(get_db):
    global _DB
    _DB = get_db


def ensure_schema(db):
    global _READY, _TRIGRAM
    if _READY:
        return True
    tok = "trigram"
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                   "USING fts5(content, tokenize='trigram')")
    except sqlite3.OperationalError:
        # 老版本 sqlite（<3.34）没有 trigram → 降级（中文检索退化，但不炸）
        tok = "unicode61"
        _TRIGRAM = False
        try:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                       "USING fts5(content, tokenize='unicode61')")
        except sqlite3.OperationalError:
            return False   # 连 FTS5 都没有 → 搜索功能关闭，站点照常
    # 触发器同步（DROP + CREATE 保证幂等刷新）
    db.execute("DROP TRIGGER IF EXISTS messages_fts_ai")
    db.execute("DROP TRIGGER IF EXISTS messages_fts_ad")
    db.execute("""
        CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END
    """)
    db.execute("""
        CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.id;
        END
    """)
    # 首次回填（行数不一致才跑，之后增量靠触发器）
    try:
        n_idx = db.execute("SELECT COUNT(*) c FROM messages_fts").fetchone()["c"]
        n_all = db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        if n_idx == 0 and n_all > 0:
            db.execute("INSERT INTO messages_fts(rowid, content) "
                       "SELECT id, content FROM messages")
    except sqlite3.Error:
        pass
    db.commit()
    _READY = True
    return True


def escape(q):
    """FTS5 查询串转义：包双引号，内部双引号翻倍。空串返回 None。"""
    q = (q or "").strip()
    if not q:
        return None
    return '"' + q.replace('"', '""') + '"'


def search_private(user_id, keyword, peer_id=0, since=0, until=0, limit=50):
    """私聊全文检索：关键词 + 对方 + 时间范围。返回消息行（含对方 id）。

    路由策略：<3 字符（trigram 下限）或含 FTS 元字符 → LIKE；
    ≥3 字符走 FTS5，OperationalError 再降级 LIKE。
    """
    db = _DB()
    if not ensure_schema(db):
        return []
    kw = (keyword or "").strip()
    if not kw:
        return []
    if len(kw) < 3 or not _TRIGRAM:
        return _search_like(user_id, kw, peer_id, since, until, limit)
    m = escape(kw)
    sql = """
        SELECT msg.id, msg.user_id, msg.receiver_id, msg.content, msg.created_at,
               msg.file_url, msg.file_name, msg.file_type
        FROM messages_fts f
        JOIN messages msg ON msg.id = f.rowid
        WHERE messages_fts MATCH ?
          AND msg.is_private = 1
          AND (msg.user_id = ? OR msg.receiver_id = ?)
    """
    params = [m, user_id, user_id]
    if peer_id:
        sql += " AND ((msg.user_id = ? AND msg.receiver_id = ?) OR (msg.user_id = ? AND msg.receiver_id = ?))"
        params += [user_id, peer_id, peer_id, user_id]
    if since:
        sql += " AND msg.created_at >= ?"
        params.append(since)
    if until:
        sql += " AND msg.created_at <= ?"
        params.append(until)
    sql += " ORDER BY msg.created_at DESC LIMIT ?"
    params.append(limit)
    try:
        return db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return _search_like(user_id, kw, peer_id, since, until, limit)


def _search_like(user_id, keyword, peer_id, since, until, limit):
    """LIKE 降级路径（短词 / 无 FTS5 环境）。"""
    db = _DB()
    like = "%" + keyword.replace("%", "").replace("_", "") + "%"
    sql = """
        SELECT msg.id, msg.user_id, msg.receiver_id, msg.content, msg.created_at,
               msg.file_url, msg.file_name, msg.file_type
        FROM messages msg
        WHERE msg.is_private = 1
          AND (msg.user_id = ? OR msg.receiver_id = ?)
          AND msg.content LIKE ?
    """
    p2 = [user_id, user_id, like]
    if peer_id:
        sql += " AND ((msg.user_id = ? AND msg.receiver_id = ?) OR (msg.user_id = ? AND msg.receiver_id = ?))"
        p2 += [user_id, peer_id, peer_id, user_id]
    if since:
        sql += " AND msg.created_at >= ?"
        p2.append(since)
    if until:
        sql += " AND msg.created_at <= ?"
        p2.append(until)
    sql += " ORDER BY msg.created_at DESC LIMIT ?"
    p2.append(limit)
    try:
        return db.execute(sql, p2).fetchall()
    except sqlite3.Error:
        return []
