# -*- coding: utf-8 -*-
"""chat/room.py — 051 聊天室线（从 app.py 原样搬迁，逻辑零改写）。

含：聊天室页与发言、消息列表 API、撤回（034 无痕卡联动）、编辑、
管理员删除、聊天文件上传。/uploads/<filename> 是公共文件服务，留 app.py。
endpoint 名与搬迁前一致，url_for 零改动。
"""
import os
import time
import uuid

from flask import (request, jsonify, render_template, flash, redirect, url_for)

from .deps import D


def _app():
    return D["app"]


def chat():
    user = D["current_user"]()
    if request.method == "POST":
        _ok, _wait = D["flood_check"]("chat", user["id"])
        if not _ok:
            flash("发言太快了，请等 %d 秒后再发" % _wait, "warn")
            return redirect(url_for("chat"))
        content = (request.form.get("content") or "").strip()
        file_url = None
        file_name = None
        file_type = None
        img_meta = ""

        try:
            rid = int(request.args.get("room") or request.form.get("room_id") or "1")
        except (ValueError, TypeError):
            rid = 1
        room = D["get_room"](rid) or D["get_room"](1)
        rid = room["id"]

        if "file" in request.files:
            file = request.files["file"]
            if file and file.filename != "" and D["allowed_file"](file.filename):
                ext = file.filename.rsplit(".", 1)[1].lower()
                filename = f"{uuid.uuid4().hex}.{ext}"
                dest = os.path.join(_app().config["UPLOAD_FOLDER"], filename)
                file.save(dest)
                file_name = D["sanitize_display_name"](file.filename)
                if D["is_image"](file.filename):
                    file_type = "image"
                    filename, img_meta = D["_save_upload_image"](dest, filename)
                elif D["is_video"](file.filename):
                    file_type = "video"
                    nd = D["_compress_video"](dest)
                    if nd != dest:
                        filename = os.path.basename(nd)
                else:
                    file_type = "file"
                file_url = url_for("uploaded_file", filename=filename)

        if D["is_active_restriction"](user["muted_until"]):
            flash("你已被禁言：%s，无法发言" % D["status_text"](user["muted_until"]), "error")
            return redirect(url_for("chat"))
        if not content and not file_url:
            flash("消息不能为空", "warn")
            return redirect(url_for("chat"))

        final_content = content[:1000] if content else ""
        if final_content:
            original = final_content
            final_content, block_err = D["filter_and_block"](final_content, user, "chat")
            if block_err:
                flash(block_err, "error")
                return redirect(url_for("chat"))
            if original != final_content:
                flash("消息包含敏感词，已被过滤", "warn")

        db = D["get_db"]()
        cursor = db.execute(
            "INSERT INTO messages (user_id, username, content, file_url, file_name, file_type, is_private, room_id, img_meta, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user["id"], user["username"], final_content, file_url, file_name, file_type, 0, rid, img_meta, time.time()),
        )
        msg_id = cursor.lastrowid
        db.commit()
        try:
            import pg_shadow
            pg_shadow.mirror('messages',
                "INSERT INTO messages (user_id, username, content, file_url, file_name, file_type, is_private, room_id, img_meta, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user["id"], user["username"], final_content, file_url, file_name, file_type, 0, rid, img_meta, time.time()),
                msg_id)
        except Exception:
            pass

        mentions = D["extract_mentions"](final_content)
        for mentioned_name in mentions:
            mentioned_user = D["get_user_by_name"](mentioned_name)
            if mentioned_user and mentioned_user["id"] != user["id"]:
                D["add_notification"](
                    mentioned_user["id"],
                    "mention",
                    f'{user["username"]} 在群聊中@了你',
                    url_for("chat") + f"#msg-{msg_id}"
                )

        return redirect(url_for("chat"))

    db = D["get_db"]()
    rid = request.args.get("room", "1")
    try:
        rid = int(rid)
    except Exception:
        rid = 1
    room = db.execute("SELECT * FROM chat_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        room = db.execute("SELECT * FROM chat_rooms WHERE id=1").fetchone()
        rid = room["id"]
    rooms = db.execute("SELECT * FROM chat_rooms ORDER BY sort_order ASC").fetchall()
    muted = D["is_active_restriction"](user["muted_until"])
    banned = D["is_active_restriction"](user["banned_until"])
    return render_template("chat.html", rooms=rooms, room=room, muted=muted, banned=banned, user=user)


def api_messages():
    rows = D["get_db"]().execute(
        "SELECT m.*, u.avatar, u.is_admin, u.has_effect, u.effect_type, u.badge_text, u.badge_effect, u.mall_title, u.mall_badge FROM messages m LEFT JOIN users u ON m.user_id = u.id WHERE m.is_private = 0 ORDER BY m.id DESC LIMIT 100"
    ).fetchall()
    data = []
    for r in reversed(rows):
        deleted = bool(r["is_deleted"])
        data.append({
            "id": r["id"],
            "user_id": r["user_id"],
            "username": r["username"],
            "content": "" if deleted else r["content"],
            "file_url": None if deleted else r["file_url"],
            "file_name": None if deleted else r["file_name"],
            "file_type": None if deleted else r["file_type"],
            "avatar": D["get_avatar_url"](r),
            "is_admin": r["is_admin"],
            "effect": D["get_user_effect"](r),
            "badge_text": r["badge_text"],
            "badge_effect": r["badge_effect"],
            "mall_title": (r["mall_title"] if "mall_title" in r.keys() else None),
            "mall_title_color": (D["TITLE_COLORS"].get(r["mall_title"]) if "mall_title" in r.keys() and r["mall_title"] else None),
            "mall_badge": (r["mall_badge"] if "mall_badge" in r.keys() else None),
            "time": time.strftime("%H:%M:%S", time.localtime(r["created_at"])),
            "can_recall": D["can_recall"](r),
            "is_deleted": deleted,
        })
    return jsonify({"messages": data})


def recall_message(msg_id):
    """撤回（034：2 分钟内自由撤，超时用无痕撤回卡，24 小时硬顶）。"""
    user = D["current_user"]()
    db = D["get_db"]()
    msg = db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if not msg:
        return jsonify({"error": "消息不存在"}), 404
    if not D["can_recall"](msg):
        if (time.time() - float(msg["created_at"] or 0)) >= 86400:
            return jsonify({"error": "超过 24 小时的消息无法撤回"}), 403
        card_count = D["get_consumable_count"](user["id"], "recall_card")
        if card_count <= 0:
            return jsonify({"error": "无法撤回该消息（已超时）"}), 403
        if int(msg["user_id"] or 0) != int(user["id"]) and not user["is_admin"]:
            return jsonify({"error": "无法撤回该消息"}), 403
        if request.form.get("confirm_card") != "1":
            return jsonify({
                "need_card": True,
                "card_name": "无痕撤回卡",
                "card_count": card_count,
                "msg_id": msg_id,
            })
        D["consume_item"](user["id"], "recall_card", 1)
        db.execute("INSERT INTO consumable_logs (user_id, item_key, detail, created_at) VALUES (?,?,?,?)",
                   (user["id"], "recall_card", "超时撤回 #%d" % msg_id, time.time()))
        db.commit()
        db.execute("UPDATE messages SET is_deleted = 1 WHERE id = ?", (msg_id,))
        db.commit()
        return jsonify({"ok": True, "username": msg["username"], "card_used": True,
                        "card_remaining": D["get_consumable_count"](user["id"], "recall_card")})

    db.execute("UPDATE messages SET is_deleted = 1 WHERE id = ?", (msg_id,))
    db.commit()
    scene = "private" if msg["is_private"] else "chat"
    db.execute(
        "INSERT INTO recall_logs (operator_id, operator_name, message_id, scene, content, recalled_at) VALUES (?,?,?,?,?,?)",
        (user["id"], user["username"], msg_id, scene, (msg["content"] or "")[:500], time.time())
    )
    db.commit()
    return jsonify({"ok": True, "username": msg["username"]})


def edit_message(msg_id):
    """编辑消息：本人（未撤回可改）或管理员/超管（任意消息）。"""
    user = D["current_user"]()
    db = D["get_db"]()
    msg = db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if not msg:
        return jsonify({"error": "消息不存在"}), 404
    if msg["is_deleted"]:
        return jsonify({"error": "该消息已被撤回/删除，无法编辑"}), 403
    if int(msg["user_id"] or 0) != int(user["id"]) and not (user["is_admin"] or user["is_super_admin"]):
        return jsonify({"error": "无权编辑该消息"}), 403
    new_content = (request.form.get("content") or "").strip()
    if not new_content:
        return jsonify({"error": "内容不能为空"}), 400
    if len(new_content) > 2000:
        return jsonify({"error": "内容过长"}), 400
    old_hist = (msg["edit_history"] or "")
    hist_parts = [p for p in old_hist.split("|||") if p][-2:]
    hist_parts.append((msg["content"] or "")[:500])
    db.execute(
        "UPDATE messages SET content=?, edited=1, edit_history=? WHERE id=?",
        (new_content, "|||".join(hist_parts), msg_id))
    db.commit()
    if (user["is_admin"] or user["is_super_admin"]) and int(msg["user_id"] or 0) != int(user["id"]):
        D["add_admin_log"](user, "edit_message", msg_id, msg["username"])
    room_id = D["_row_get"](msg, "room_id") or 1
    try:
        D["socketio"].emit("message_edited", {"message_id": msg_id, "content": new_content}, room=room_id)
    except Exception:
        pass
    return jsonify({"ok": True})


def admin_delete_message(msg_id):
    """管理员/超管删除任意消息（HTTP 兜底，socket 不可用时调用）。"""
    user = D["current_user"]()
    if not (user["is_admin"] or user["is_super_admin"]):
        return jsonify({"error": "无权限"}), 403
    db = D["get_db"]()
    msg = db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if not msg:
        return jsonify({"error": "消息不存在"}), 404
    db.execute("UPDATE messages SET is_deleted=1, content='[该消息已被管理员删除]' WHERE id=?", (msg_id,))
    db.commit()
    D["add_admin_log"](user, "chat_delete", msg_id, msg["username"])
    try:
        D["socketio"].emit("message_deleted", {"message_id": msg_id, "room_id": msg["room_id"]}, room=msg["room_id"])
    except Exception:
        pass
    return jsonify({"ok": True})


def api_chat_upload():
    user = D["current_user"]()
    if "file" not in request.files:
        return jsonify({"error": "无文件"}), 400
    file = request.files["file"]
    room_id = request.form.get("room_id", "1")
    room = D["get_room"](room_id) or D["get_room"](1)
    if room and not room["file_upload"]:
        return jsonify({"error": "本房间已关闭文件上传"}), 403
    if file and file.filename != "" and D["allowed_file"](file.filename):
        ext = file.filename.rsplit(".", 1)[1].lower()
        filename = "%s.%s" % (uuid.uuid4().hex, ext)
        dest = os.path.join(_app().config["UPLOAD_FOLDER"], filename)
        file.save(dest)
        meta = ""
        if D["is_image"](file.filename):
            ftype = "image"
            filename, meta = D["_save_upload_image"](dest, filename)
        elif D["is_video"](file.filename):
            ftype = "video"
            newdest = D["_compress_video"](dest)
            if newdest != dest:
                filename = os.path.basename(newdest)
        else:
            ftype = "file"
        return jsonify({
            "file_url": url_for("uploaded_file", filename=filename),
            "file_name": D["sanitize_display_name"](file.filename),
            "file_type": ftype,
            "img_meta": meta,
        })
    return jsonify({"error": D["ext_reject_msg"]()}), 400


def register(app, D):
    app.add_url_rule("/chat", "chat", D["login_required"](chat), methods=["GET", "POST"])
    app.add_url_rule("/api/messages", "api_messages",
                     D["login_required"](api_messages))
    app.add_url_rule("/recall-message/<int:msg_id>", "recall_message",
                     D["login_required"](recall_message), methods=["POST"])
    app.add_url_rule("/edit-message/<int:msg_id>", "edit_message",
                     D["login_required"](edit_message), methods=["POST"])
    app.add_url_rule("/admin/delete-message/<int:msg_id>", "admin_delete_message",
                     D["login_required"](admin_delete_message), methods=["POST"])
    app.add_url_rule("/api/chat/upload", "api_chat_upload",
                     D["login_required"](api_chat_upload), methods=["POST"])
