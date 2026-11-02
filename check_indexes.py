# -*- coding: utf-8 -*-
"""check_indexes.py — 049 离线索引审查。

对核心查询跑 EXPLAIN QUERY PLAN，报告 SCAN（全表扫）的查询，
提示该补什么索引。用法：
    python check_indexes.py [db_path]
零写入，只读报告，可随时跑。
"""
import os
import sqlite3
import sys

# 核心查询清单（表 → 代表性 SQL，覆盖榜单/帖子列表/用户/流水/通知）
CHECKS = [
    ("榜单", "SELECT username, high_score_24 FROM users ORDER BY high_score_24 DESC LIMIT 10"),
    ("帖子列表(版块+时间)",
     "SELECT id, title FROM posts WHERE section_id=? AND is_deleted=0 ORDER BY created_at DESC LIMIT 20"),
    ("帖子列表(主题)", "SELECT id, title FROM posts WHERE thread_id=? ORDER BY created_at DESC LIMIT 20"),
    ("我的帖子", "SELECT id, title FROM posts WHERE user_id=? ORDER BY created_at DESC LIMIT 20"),
    ("评论列表", "SELECT id, content FROM post_comments WHERE post_id=? ORDER BY created_at LIMIT 50"),
    ("楼中楼", "SELECT id, content FROM post_comments WHERE parent_id=? ORDER BY created_at"),
    ("私信记录", "SELECT id, content FROM messages WHERE is_private=1 AND receiver_id=? ORDER BY created_at DESC LIMIT 50"),
    ("积分流水", "SELECT * FROM points_log WHERE user_id=? ORDER BY created_at DESC LIMIT 50"),
    ("通知未读", "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0"),
    ("收藏", "SELECT post_id FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT 20"),
    ("标签帖", "SELECT p.id FROM posts_tags pt JOIN posts p ON p.id=pt.post_id WHERE pt.tag_id=? ORDER BY p.created_at DESC LIMIT 20"),
    ("主题订阅", "SELECT thread_id FROM thread_subs WHERE user_id=?"),
    ("FTS 私聊", "SELECT msg.id FROM messages_fts f JOIN messages msg ON msg.id=f.rowid WHERE messages_fts MATCH '链接' LIMIT 20"),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data.db"
    if not os.path.exists(path):
        print("数据库不存在:", path)
        return 1
    db = sqlite3.connect(path)
    bad = 0
    print("=" * 72)
    for name, sql in CHECKS:
        try:
            if "MATCH" in sql:
                # FTS 查询无占位符，不能传参
                plan = db.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            else:
                plan = db.execute("EXPLAIN QUERY PLAN " + sql, (1,)).fetchall()
        except Exception as e:
            print("  [跳过] %s：%s" % (name, str(e)[:60]))
            continue
        detail = " ; ".join(r[3] for r in plan)
        # FTS5 虚拟表的 EXPLAIN 恒显示 SCAN（MATCH 走 FTS 内部索引），不算问题
        scan = ("SCAN" in detail and "USING INDEX" not in detail
                and "USING COVERING INDEX" not in detail and "MATCH" not in sql)
        flag = "⚠️ 全表扫" if scan else "✓ 走索引"
        if scan:
            bad += 1
        print("%-18s %s" % (name, flag))
        print("    %s" % detail[:150])
    print("=" * 72)
    print("结论: %d/%d 条查询全表扫%s" % (bad, len(CHECKS),
          "" if bad == 0 else "，见上方明细补索引"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
