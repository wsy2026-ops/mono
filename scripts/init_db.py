#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化数据库 —— 建表 + 可选创建第一个管理员。

    python scripts/init_db.py                          # 只建表
    python scripts/init_db.py --admin me 密码至少8位    # 建表 + 建超级管理员

表结构来自仓库里的 `schema.sql`（从线上库导出的完整结构，145 张表）。
**不要用 `app.init_db()`** —— 那是早期的建库函数，里面全是裸 ALTER 和非幂等 UPDATE，
在空库上都会因为缺表而中途报错，在非空库上会误改数据。函数自带护栏拒绝执行，
但它本来也不该被调用。

改表结构怎么办：自己写幂等迁移（先 `PRAGMA table_info` 判断再 ALTER），
本地和线上各跑一次；schema.sql 只在「新建库」时用。
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SCHEMA = os.path.join(ROOT, "schema.sql")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin", nargs=2, metavar=("用户名", "密码"),
                    help="顺便创建第一个超级管理员")
    ap.add_argument("--force", action="store_true", help="库里已有数据也继续（危险）")
    args = ap.parse_args()

    os.environ.setdefault("MONO_SECRET_KEY", "dev-only-not-for-production")
    from app import DB_PATH

    print("数据库：%s" % DB_PATH)
    if os.path.exists(DB_PATH):
        try:
            c = sqlite3.connect(DB_PATH)
            n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            c.close()
        except Exception:
            n = 0
        if n and not args.force:
            print("库里已经有 %d 个用户了，不动它。要重建请先删掉 data.db。" % n)
            return 0

    if not os.path.exists(SCHEMA):
        print("找不到 schema.sql")
        return 1

    db = sqlite3.connect(DB_PATH)
    with open(SCHEMA, encoding="utf-8") as f:
        db.executescript(f.read())
    db.commit()
    tables = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    print("建表完成：%d 张表" % tables)

    # 最小初始数据：没有聊天室 / 板块时，/chat 会跳到一个不存在的房间然后 500
    import time
    now = time.time()
    if not db.execute("SELECT 1 FROM chat_rooms LIMIT 1").fetchone():
        db.execute("INSERT INTO chat_rooms (name, description, sort_order, created_at) "
                   "VALUES ('综合大厅', '默认聊天室', 0, ?)", (now,))
        print("已创建默认聊天室")
    if not db.execute("SELECT 1 FROM forum_sections LIMIT 1").fetchone():
        db.execute("INSERT INTO forum_sections (name, description, sort_order, created_at) "
                   "VALUES ('综合讨论', '默认板块', 0, ?)", (now,))
        print("已创建默认板块")
    db.commit()

    if args.admin:
        from app import hash_password
        name, pwd = args.admin
        if len(pwd) < 8:
            print("密码太短，至少 8 位")
            return 1
        now = time.time()
        h, salt = hash_password(pwd)
        try:
            db.execute(
                "INSERT INTO users (username, password_hash, salt, is_admin, is_super_admin, "
                "coins, exp, level, created_at) VALUES (?,?,?,1,1,0,0,1,?)",
                (name, h, salt, now))
            db.commit()
            print("已创建超级管理员：%s" % name)
        except Exception as e:
            print("创建管理员失败：%s" % e)
            return 1
    db.close()
    print("完成。启动：python start_server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
