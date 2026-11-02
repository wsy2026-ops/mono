# -*- coding: utf-8 -*-
"""chat/private.py — 051 私聊线（从 app.py 原样搬迁，逻辑零改写）。

endpoint 名与搬迁前一致（private_chat / send_private / api_private_send /
api_private_conversations / update_private_perm），全站 url_for 零改动。
"""
import os
import time
import uuid

from flask import (request, jsonify, render_template, abort, flash,
                   redirect, url_for)

from .deps import D


def _app():
    return D["app"]


def _login_required(f):
    return D["login_required"](f)


@_login_required
def update_private_perm():
    """私信权限设置（谁可以给我发私信）。"""
    me = D["current_user"]()
    db = D["get_db"]()
    mode = (request.form.get("mode") or "all").strip()
    if mode not in ("all", "friends", "none"):
        mode = "all"
    db.execute("UPDATE users SET private_perm=? WHERE id=?", (mode, me["id"]))
    db.commit()
    flash("私信权限已更新", "ok")
    return redirect(request.referrer or url_for("settings"))


def private_chat(user_id):
    """私聊页：历史消息 + 已读回执推送给对方。"""
    me = D["current_user"]()
    target = D["get_user_by_id"](user_id)
    if not target:
        abort(404)
    if me["id"] == user_id:
        return redirect(url_for("chat"))

    if D["has_blocked"](user_id, me["id"]) and not me["is_admin"]:
        flash("对方暂不接受私信", "warn")
        return redirect(url_for("friends"))

    is_friend = D["are_friends"](me["id"], user_id)
    can_send, deny_reason = D["can_private_chat"](me, target)

    db = D["get_db"]()
    cur = db.execute("UPDATE messages SET read_at=? "
                     "WHERE is_private=1 AND receiver_id=? AND user_id=? AND read_at=0",
                     (time.time(), me["id"], user_id))
    db.commit()
    # 已读回执推给对方，让发送方不用猜对方看没看
    if cur.rowcount:
        try:
            last = db.execute(
                "SELECT MAX(id) AS mid FROM messages WHERE is_private=1 "
                "AND user_id=? AND receiver_id=? AND read_at>0",
                (me["id"], user_id)).fetchone()
            if last and last["mid"]:
                D["socketio"].emit("dm_read_receipt", {
                    "from": me["id"], "by": me["username"],
                    "upto_id": last["mid"], "read_at": time.time(),
                }, room="user_%d" % user_id)
        except Exception:
            pass
    rows = db.execute("""
        SELECT m.*, u.avatar, u.is_admin, u.has_effect, u.effect_type, u.badge_text, u.badge_effect, u.mall_title, u.mall_badge, u.bubble_style FROM messages m 
        LEFT JOIN users u ON m.user_id = u.id
        WHERE m.is_private = 1 AND
              ((m.user_id = ? AND m.receiver_id = ?) OR (m.user_id = ? AND m.receiver_id = ?))
        ORDER BY m.id DESC LIMIT 200
    """, (me["id"], user_id, user_id, me["id"])).fetchall()
    rows = list(reversed(rows))

    today = int((time.time() + 28800) // 86400)
    messages = []
    prev = None
    for r in rows:
        d = dict(r)
        dk = int((d["created_at"] + 28800) // 86400)
        prev_dk = int((prev["created_at"] + 28800) // 86400) if prev else None
        if prev_dk == dk:
            d["day_label"] = ""
        elif dk == today:
            d["day_label"] = "今天"
        elif dk == today - 1:
            d["day_label"] = "昨天"
        else:
            d["day_label"] = D["fmt_cn"](d["created_at"], "%m月%d日")
        d["cont"] = bool(prev and not prev["is_deleted"] and not d["is_deleted"]
                         and prev["user_id"] == d["user_id"]
                         and (d["created_at"] - prev["created_at"]) < 300)
        d["last_mine"] = False
        messages.append(d)
        prev = d
    for d in reversed(messages):
        if d["user_id"] == me["id"] and not d["is_deleted"]:
            d["last_mine"] = True
            break

    i_blocked_target = D["has_blocked"](me["id"], user_id)
    return render_template("private_chat.html", target=target, messages=messages,
                          is_friend=is_friend, can_send=can_send, sent_count=0,
                          deny_reason=deny_reason, i_blocked_target=i_blocked_target)


def _private_send_core(me, target, content, file_storage=None, idem_key=None):
    """私信发送的公共逻辑（表单和接口共用）。返回 (ok, err, payload)。

    051：支持幂等键 idem_key（重连重发不重复入库）。
    """
    user_id = target["id"]
    is_friend = D["are_friends"](me["id"], user_id)
    db = D["get_db"]()
    content = (content or "").strip()
    file_url = file_name = file_type = None
    img_meta = ""

    if file_storage is not None and file_storage.filename:
        if D["allowed_file"](file_storage.filename):
            ext = file_storage.filename.rsplit(".", 1)[1].lower()
            fn = "%s.%s" % (uuid.uuid4().hex, ext)
            fp = os.path.join(_app().config["UPLOAD_FOLDER"], fn)
            file_storage.save(fp)
            file_name = D["sanitize_display_name"](file_storage.filename)
            if D["is_image"](file_storage.filename):
                file_type = "image"
                fn, img_meta = D["_save_upload_image"](fp, fn)
            elif D["is_video"](file_storage.filename):
                file_type = "video"
                nd = D["_compress_video"](fp)
                if nd != fp:
                    fn = os.path.basename(nd)
            else:
                file_type = "file"
            file_url = url_for("uploaded_file", filename=fn)

    if not content and not file_url:
        return False, "消息不能为空", None

    can_send, deny_reason = D["can_private_chat"](me, target)
    if not can_send:
        D["_log_warning"](me, "private_blocked", "-> %s: %s" % (target["username"], (content or "")[:100]))
        return False, (deny_reason or "无法发送私信"), None

    filtered, block_err = D["filter_and_block"](content, me, "private")
    if block_err:
        return False, block_err, None
    # 051 幂等：同 idem_key 已入库 → 直接返回原消息（重连重发/双击防重）。
    # 必须放在 repeat_guard 之前 —— 否则重连重发同内容会被 8 秒防重拦掉，
    # 幂等键根本走不到（实测：同键第二次发送返回「刚刚已经发出去了」）。
    if idem_key:
        old = db.execute(
            "SELECT * FROM messages WHERE user_id=? AND idem_key=? AND is_private=1",
            (me["id"], idem_key)).fetchone()
        if old:
            payload = dict(old)
            payload["dup"] = True
            return True, "", payload
    if not D["repeat_guard"]("send_private", [user_id, filtered, file_url or ""], window=8.0, uid=me["id"]):
        return False, "这条私信刚刚已经发出去了", None


    now = time.time()
    cursor = db.execute(
        "INSERT INTO messages (user_id, username, content, file_url, file_name, file_type, is_private, receiver_id, img_meta, created_at, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (me["id"], me["username"], filtered, file_url, file_name, file_type, 1, user_id, img_meta, now, idem_key),
    )
    msg_id = cursor.lastrowid
    db.commit()
    try:
        import pg_shadow
        pg_shadow.mirror('messages',
            "INSERT INTO messages (user_id, username, content, file_url, file_name, file_type, is_private, receiver_id, img_meta, created_at, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (me["id"], me["username"], filtered, file_url, file_name, file_type, 1, user_id, img_meta, now, idem_key),
            msg_id)
    except Exception:
        pass
    D["task_inc"](me["id"], "daily_dm")
    D["behavior_points"](me["id"], "dm")

    D["add_notification"](
        user_id,
        "private_message",
        f'{me["username"]} 给你发了一条私信',
        url_for("private_chat", user_id=me["id"])
    )

    payload = {
        "id": msg_id, "user_id": me["id"], "username": me["username"],
        # 统一走 get_avatar_url()：和模板/服务端渲染同一个来源，
        # 免得 JS 用另一套兜底、两边长得不一样（2026-10-10 头像破图就是这个引起的）
        "avatar": D["get_avatar_url"](me),
        "content": filtered, "file_url": file_url, "file_name": file_name,
        "file_type": file_type, "created_at": now,
        "img_meta": img_meta,
        # 气泡特效：前端按这个值套 .bubble-XXX（与聊天室一致）
        "bubble_style": D["_row_get"](me, "bubble_style", "") or "",
    }
    try:
        D["socketio"].emit("private_message", {"from": me["id"], "msg": payload},
                           room="user_%d" % user_id)
        D["socketio"].emit("private_message", {"from": me["id"], "msg": payload, "self": True},
                           room="user_%d" % me["id"])
    except Exception:
        pass
    return True, "", payload


def send_private(user_id):
    me = D["current_user"]()
    target = D["get_user_by_id"](user_id)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("friends"))

    ok, err, _ = _private_send_core(me, target, request.form.get("content"),
                                    request.files.get("file"))
    if not ok:
        flash(err, "ok" if err == "这条私信刚刚已经发出去了" else "error")
        if D["has_blocked"](user_id, me["id"]):
            return redirect(url_for("friends"))
    return redirect(url_for("private_chat", user_id=user_id))


def api_private_send():
    me = D["current_user"]()
    if request.files or request.form:
        data = request.form
        fstore = request.files.get("file")
    else:
        data = request.get_json(silent=True) or {}
        fstore = None
    try:
        to = int(data.get("to"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数不合法"}), 400
    target = D["get_user_by_id"](to)
    if not target:
        return jsonify({"ok": False, "msg": "用户不存在"}), 404
    # 051 幂等键：重连重发/双击时同键只入库一次
    idem_key = (data.get("idem_key") or "").strip()[:80] or None
    ok, err, payload = _private_send_core(me, target, data.get("content"), fstore,
                                          idem_key=idem_key)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 400
    return jsonify({"ok": True, "msg": payload})


def api_private_conversations():
    me = D["current_user"]()
    db = D["get_db"]()
    uid = me["id"]
    rows = db.execute("""
        SELECT CASE WHEN user_id = ? THEN receiver_id ELSE user_id END AS pid,
               MAX(id) AS last_id,
               SUM(CASE WHEN receiver_id = ? AND read_at = 0 THEN 1 ELSE 0 END) AS unread
        FROM messages
        WHERE is_private = 1 AND is_deleted = 0 AND (user_id = ? OR receiver_id = ?)
        GROUP BY pid
        ORDER BY last_id DESC
        LIMIT 80
    """, (uid, uid, uid, uid)).fetchall()
    out = []
    for r in rows:
        pid = r["pid"]
        if not pid:
            continue
        pu = db.execute("SELECT id, username, avatar FROM users WHERE id=?", (pid,)).fetchone()
        if not pu:
            continue
        last = db.execute("SELECT * FROM messages WHERE id=?", (r["last_id"],)).fetchone()
        if not last:
            continue
        if last["file_type"] == "image":
            preview = "[图片]"
        elif last["file_type"] == "video":
            preview = "[视频]"
        elif last["file_url"]:
            preview = "[文件] " + (last["file_name"] or "")
        else:
            preview = last["content"] or ""
        out.append({
            "id": pid,
            "name": pu["username"],
            "avatar": D["get_avatar_url"](pu),
            "preview": preview[:60],
            "mine": last["user_id"] == uid,
            "time": last["created_at"],
            "unread": r["unread"] or 0,
        })
    return jsonify({"ok": True, "list": out, "unread": sum(x["unread"] for x in out)})


def register(app, D):
    """注册私聊线路由。endpoint 名与搬迁前完全一致（url_for 零改动）。"""
    app.add_url_rule("/update-private-perm", "update_private_perm",
                     update_private_perm, methods=["POST"])
    app.add_url_rule("/private-chat/<int:user_id>", "private_chat",
                     D["login_required"](private_chat))
    app.add_url_rule("/send-private/<int:user_id>", "send_private",
                     D["login_required"](send_private), methods=["POST"])
    app.add_url_rule("/api/private/send", "api_private_send",
                     D["login_required"](api_private_send), methods=["POST"])
    app.add_url_rule("/api/private/conversations", "api_private_conversations",
                     D["login_required"](api_private_conversations))
