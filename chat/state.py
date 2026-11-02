# -*- coding: utf-8 -*-
"""chat/state.py — 051 消息可靠性核心。

三件事：
  1. **离线补推**：用户 SocketIO connect 时，把「发给他的未读私信」补推一遍。
     消息一直是发送即入库的（不丢），缺的只是对方离线时没人告诉他 ——
     这正是文档说的「断线重连后错过的消息永久丢失」的解法。
  2. **幂等键**：idem_key 由客户端生成（发送时 UUID），重连重发/双击
     同键只入库一次（private.py 里查重）。
  3. **送达回执**：客户端收到私信 emit pm_ack → delivered_at → 推给发送方。
     状态机：sent（入库）→ delivered（对方客户端确认）→ read（read_at，015 已有）
            → recalled（is_deleted，034 已有）。

设计：**补推只发未读私信**（read_at=0），前端按消息 id 去重 ——
已读过的不会再推，未读的不论在不在线都至少会收到一次。
"""
import time

from .deps import D


class State:
    def __init__(self, sio):
        self.sio = sio

    def backfill_private(self, uid):
        """把发给 uid 的未读私信补推给 uid（连接建立后调用）。"""
        try:
            db = D["get_db"]()
            rows = db.execute("""
                SELECT m.*, u.avatar, u.is_admin, u.has_effect, u.effect_type,
                       u.badge_text, u.badge_effect, u.mall_title, u.mall_badge, u.bubble_style
                FROM messages m LEFT JOIN users u ON m.user_id = u.id
                WHERE m.is_private = 1 AND m.receiver_id = ? AND m.read_at = 0
                      AND m.is_deleted = 0
                ORDER BY m.id ASC LIMIT 50
            """, (uid,)).fetchall()
            if not rows:
                return
            for r in rows:
                payload = dict(r)
                payload["avatar"] = D["get_avatar_url"](r)
                payload["backfill"] = True     # 前端标记：这是补推，不弹 toast
                self.sio.emit("private_message",
                              {"from": r["user_id"], "msg": payload},
                              room="user_%d" % uid)
        except Exception:
            pass
