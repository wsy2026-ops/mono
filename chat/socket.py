# -*- coding: utf-8 -*-
"""chat/socket.py — 051 SocketIO 事件（从 app.py 搬迁 + 051 新增）。

搬迁：private_typing / send_message / recall_message / delete_message。
新增（051 消息可靠性）：
  pm_ack        客户端收到私信 → 回执 → 标记 delivered 并通知发送方
  pm_resend     重连后请求补推（ connect 时也会自动补推一次）
"""
import re
import time

from flask import request
from flask_socketio import emit

from .deps import D


def _private_typing(data):
    """私聊「正在输入」：只做瞬时广播，不落库、不记日志。"""
    me = D["current_user"]()
    if not me:
        return
    try:
        to = int(data.get("to") or 0)
    except (ValueError, TypeError):
        return
    if not to or to == me["id"]:
        return
    try:
        if D["has_blocked"](to, me["id"]) or D["has_blocked"](me["id"], to):
            return
    except Exception:
        pass
    D["socketio"].emit("private_typing", {
        "from": me["id"],
        "name": me["username"],
    }, room="user_%d" % to)


def _send_message(data):
    def _fail(m):
        emit("error", {"msg": m, "from": "send_message"})
        return {"ok": False, "error": m}

    user = D["current_user"]()
    if not user:
        return _fail("请先登录后再发言")
    room = D["get_room"](data.get("room_id", 1)) or D["get_room"](1)
    if not room:
        return _fail("房间不存在")
    rid = room["id"]
    if room["is_closed"] and not user["is_super_admin"]:
        return _fail("该房间已关闭")
    if D["is_active_restriction"](user["banned_until"]):
        return _fail("你已被封禁，无法发言")
    if D["is_active_restriction"](user["muted_until"]):
        return _fail("你已被禁言：" + D["status_text"](user["muted_until"]))
    if room["all_muted"] and not user["is_admin"]:
        return _fail("房间已开启全员禁言（维护中）")
    content = (data.get("content") or "").strip()
    file_url = data.get("file_url") or None
    file_name = data.get("file_name") or None
    file_type = data.get("file_type") or None
    quote_id = data.get("quote_id") or None
    if not room["file_upload"] and file_url:
        file_url = file_name = file_type = None
    if not content and not file_url:
        return _fail("消息不能为空")
    if not user["is_admin"]:
        if content and re.fullmatch(r"[\s\W_]+", content):
            return _fail("请勿发送无意义内容")
        last = D["get_db"]().execute(
            "SELECT content FROM messages WHERE room_id=? AND user_id=? AND is_private=0 ORDER BY id DESC LIMIT 1",
            (rid, user["id"])).fetchone()
        if last and last["content"] == content:
            return _fail("请勿重复发送相同内容")
        if not D["rate_allow"](user["id"]):
            D["_log_warning"](user, "chat_spam", content[:200])
            return _fail("发言过于频繁，请稍后再试")
    final = content[:1000] if content else ""
    if final:
        final, block_err = D["filter_and_block"](final, user, "chat")
        if block_err:
            return _fail(block_err)
    quote = None
    if quote_id:
        q = D["get_db"]().execute(
            "SELECT id,user_id,username,content,is_deleted FROM messages WHERE id=? AND room_id=?",
            (quote_id, rid)).fetchone()
        if q:
            if q["is_deleted"]:
                quote = {"id": q["id"], "deleted": True}
            else:
                quote = {"id": q["id"], "username": q["username"], "snippet": (q["content"] or "")[:80]}
    db = D["get_db"]()
    cur = db.execute(
        "INSERT INTO messages (user_id,username,content,file_url,file_name,file_type,is_private,room_id,quote_id,created_at,is_deleted) "
        "VALUES (?,?,?,?,?,?,0,?,?,?,0)",
        (user["id"], user["username"], final, file_url, file_name, file_type, rid, quote_id, time.time()))
    mid = cur.lastrowid
    db.commit()
    try:
        import pg_shadow
        pg_shadow.mirror('messages',
            "INSERT INTO messages (user_id,username,content,file_url,file_name,file_type,is_private,room_id,quote_id,created_at,is_deleted) "
            "VALUES (?,?,?,?,?,?,0,?,?,?,0)",
            (user["id"], user["username"], final, file_url, file_name, file_type, rid, quote_id, time.time()),
            mid)
    except Exception:
        pass
    msg = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    mentioned = []
    for nick in set(D["parse_mentions"](final)):
        tu = D["get_user_by_name"](nick)
        if not tu:
            continue
        blk = db.execute("SELECT 1 FROM blacklist WHERE user_id=? AND blocked_id=?",
                         (tu["id"], user["id"])).fetchone()
        if blk:
            continue
        mentioned.append(tu["username"])
        D["add_notification"](tu["id"], "mention",
                        "%s 在「%s」@了你" % (user["username"], room["name"]),
                        D["url_for"]("chat") + ("?room=%d#msg-%d" % (rid, mid)))
    db.commit()
    payload = D["serialize_msg"](msg, quote=quote, mentions=mentioned)
    D["add_exp"](user["id"], 1)
    D["task_inc"](user["id"], "daily_chat")
    D["behavior_points"](user["id"], "chat")
    D["socketio"].emit("new_message", payload, room=rid)
    return {"ok": True, "msg": payload}


def _recall(data):
    user = D["current_user"]()
    if not user:
        return
    mid = data.get("message_id")
    msg = D["get_db"]().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not msg or not D["can_recall"](msg):
        emit("error", {"msg": "该消息不可撤回"})
        return
    db = D["get_db"]()
    db.execute("UPDATE messages SET is_deleted=1, content='[该消息已被撤回]' WHERE id=?", (mid,))
    db.execute("INSERT INTO recall_logs (operator_id,operator_name,message_id,scene,content,recalled_at) "
               "VALUES (?,?,?,?,?,?)",
               (user["id"], user["username"], mid, "chat", msg["content"], time.time()))
    db.commit()
    D["socketio"].emit("message_recalled", {"message_id": mid, "room_id": msg["room_id"]}, room=msg["room_id"])


def _delete(data):
    user = D["current_user"]()
    if not user or not user["is_admin"]:
        emit("error", {"msg": "无权限"})
        return
    mid = data.get("message_id")
    msg = D["get_db"]().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not msg:
        return
    db = D["get_db"]()
    db.execute("UPDATE messages SET is_deleted=1, content='[该消息已被管理员删除]' WHERE id=?", (mid,))
    db.commit()
    D["add_admin_log"](user, "chat_delete", mid, msg["username"])
    D["socketio"].emit("message_deleted", {"message_id": mid, "room_id": msg["room_id"]}, room=msg["room_id"])


def _pm_ack(data):
    """051：客户端确认收到私信 → 标记 delivered + 通知发送方（✓✓ 状态）。"""
    me = D["current_user"]()
    if not me:
        return
    try:
        mid = int(data.get("id") or 0)
    except (TypeError, ValueError):
        return
    if not mid:
        return
    db = D["get_db"]()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not msg or int(msg["receiver_id"] or 0) != int(me["id"]):
        return
    if msg["delivered_at"]:
        return
    now = time.time()
    db.execute("UPDATE messages SET delivered_at=? WHERE id=?", (now, mid))
    db.commit()
    # 只推给发送方一个轻量回执（不带内容，隐私最小化）
    try:
        D["socketio"].emit("pm_delivered", {"id": mid, "at": now},
                           room="user_%d" % int(msg["user_id"]))
    except Exception:
        pass


def _pm_resend(data):
    """051：客户端重连后主动请求补推（connect 时已自动补推一次，这是手动兜底）。"""
    me = D["current_user"]()
    if not me:
        return
    D["state_backfill"](me["id"])


def register(sio, D, state):
    """注册 SocketIO 事件。事件名与搬迁前一致。"""
    sio.on_event("private_typing", _private_typing)
    sio.on_event("send_message", _send_message)
    sio.on_event("recall_message", _recall)
    sio.on_event("delete_message", _delete)
    # 051 新增
    sio.on_event("pm_ack", _pm_ack)
    sio.on_event("pm_resend", _pm_resend)
    D["state_backfill"] = state.backfill_private
