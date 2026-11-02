# -*- coding: utf-8 -*-
"""board/jq.py — 军棋（房间对战，规则内嵌于 move/legal）。

从 app.py 搬迁。helper 一律走 deps.D 注入（不 import app）。
"""
import time

import json

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_socketio import join_room

from .deps import D

import jieqi_game as _jq
from games.mini import _my_ai_survival, _board_wins_leaderboard

def _jq_payload(room):
    combined = room['board_state'] or _jq._jq_init()
    display = combined.split('|')[0]
    lm = None
    if room['last_move']:
        try:
            parts = room['last_move'].split(',')
            lm = [int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])]
        except Exception:
            lm = None
    to_move_red = room['turn_user_id'] == room['owner_id']
    in_check = bool(_jq._jq_in_check(display, to_move_red)) if room['status'] == 'playing' else False
    mt = room['move_text'] or '[]'
    try:
        move_text = json.loads(mt) if isinstance(mt, str) else mt
    except Exception:
        move_text = []
    return {
        "board": display,
        "status": room['status'],
        "winner_id": room['winner_id'],
        "turn_user_id": room['turn_user_id'],
        "last_move": lm,
        "uci_moves": room['uci_moves'] or '',
        "in_check": in_check,
        "move_text": move_text,
    }


def _jq_emit(rid):
    room = D['get_db']().execute("SELECT * FROM jq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return {"ok": False}
    payload = _jq_payload(room)
    try:
        D['socketio'].emit('jq_move', payload, room='jq_%d' % rid)
    except Exception:
        pass
    D['_award_game_stats']('jq', rid)
    return dict(payload, ok=True)


def _jq_lobby_broadcast():
    try:
        D['socketio'].emit('jq_lobby_update', {}, room='jq_lobby')
    except Exception:
        pass


def _jq_room_broadcast(rid, guest_id=None):
    try:
        D['socketio'].emit('jq_room_update', {'rid': rid, 'guest_id': guest_id}, room='jq_%d' % rid)
    except Exception:
        pass




def jq_socket_join(data):
    user = D['current_user']()
    if not user:
        return
    rid = data.get('rid')
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return
    room = D['get_db']().execute("SELECT * FROM jq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return
    is_owner = room['owner_id'] == user['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == user['id']
    if not (is_owner or is_guest) and room['status'] != 'waiting':
        return
    join_room('jq_%d' % rid, request.sid)




def jq_lobby_join():
    user = D['current_user']()
    if user:
        join_room('jq_lobby', request.sid)






def jq_list():
    db = D['get_db']()
    me = D['current_user']()
    public_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM jq_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.is_public=1 AND r.status='waiting' AND r.mode=0 AND r.owner_id != ? "
        "ORDER BY r.id DESC LIMIT 20",
        (me['id'],)).fetchall()
    my_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM jq_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.owner_id=? OR r.guest_id=? ORDER BY r.id DESC LIMIT 20",
        (me['id'], me['id'])).fetchall()
    return render_template("jq_list.html",
                           public_rooms=public_rooms, my_rooms=my_rooms, user=me,
                           ai_lb=D['_ai_survival_leaderboard'](('xq','jq')),
                           my_ai=_my_ai_survival(('xq','jq')),
                           blb_rows=_board_wins_leaderboard('jq'))






# ── 复盘：记录开局局面（058）──
# 揭棋 _jq_init() 是随机打乱的，不存开局就没法重放。
_JQ_INIT_READY = [False]


def _jq_ensure_init(db):
    if _JQ_INIT_READY[0]:
        return
    try:
        db.execute("ALTER TABLE jq_rooms ADD COLUMN init_board TEXT NOT NULL DEFAULT ''")
        db.commit()
    except Exception:
        pass
    _JQ_INIT_READY[0] = True


def jq_create():
    """揭棋建房间。揭棋大厅只有「加入」没有「创建」以外的入口，
    有人先建才能有人来 —— 别的棋都走匹配了，揭棋保留自建。"""
    me = D['current_user']()
    name = (request.form.get("name") or "揭棋房间").strip()[:30] or "揭棋房间"
    is_public = 1 if request.form.get("is_public") else 0
    db = D['get_db']()
    if not D['repeat_guard']("room_create", ["jq", name, is_public], window=5.0, uid=me['id']):
        return redirect(url_for("jq_list"))
    init = _jq._jq_init()
    _jq_ensure_init(db)
    cur = db.execute(
        "INSERT INTO jq_rooms (name, owner_id, mode, is_public, status, board_state, init_board, "
        "created_at, updated_at, owner_fp, owner_ip) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (name, me['id'], 0, is_public, 'waiting', init, init, time.time(), time.time(),
         D['_mm_fp'](), D['_mm_ip']()))
    rid = cur.lastrowid
    db.commit()
    _jq_lobby_broadcast()
    return redirect(url_for("jq_room", rid=rid))






def jq_join(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM jq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['mode'] == 1:
        return jsonify({"ok": False, "msg": "人机房间无需加入"})
    if room['status'] != 'waiting':
        return jsonify({"ok": False, "msg": "对局已开始"})
    if room['owner_id'] == me['id']:
        return jsonify({"ok": False, "msg": "这是你的房间"})
    if room['guest_id'] is not None:
        return jsonify({"ok": False, "msg": "房间已满"})
    my_fp, my_ip = D['_mm_fp'](), D['_mm_ip']()
    ofp = (room['owner_fp'] or '')[:24]
    if my_fp and ofp and my_fp == ofp:
        return jsonify({"ok": False, "msg": "同一台设备上的账号不能对战"})
    oip = (room['owner_ip'] or '')[:64]
    if my_ip and oip and my_ip == oip and D['_mm_is_new'](me['id'], time.time()):
        return jsonify({"ok": False, "msg": "同一网络下已有账号在对局"})
    db.execute(
        "UPDATE jq_rooms SET guest_id=?, status='playing', turn_user_id=?, updated_at=?, "
        "guest_fp=?, guest_ip=? WHERE id=?",
        (me['id'], room['owner_id'], time.time(), my_fp, my_ip, rid))
    db.commit()
    _jq_emit(rid)
    _jq_room_broadcast(rid, guest_id=me['id'])
    _jq_lobby_broadcast()
    return jsonify({"ok": True})






def jq_random_join():
    me = D['current_user']()
    db = D['get_db']()
    rows = db.execute(
        "SELECT id FROM jq_rooms WHERE is_public=1 AND status='waiting' AND mode=0 AND owner_id != ? "
        "AND guest_id IS NULL ORDER BY RANDOM() LIMIT 5",
        (me['id'],)).fetchall()
    for row in rows:
        r = db.execute(
            "UPDATE jq_rooms SET guest_id=?, status='playing', turn_user_id=owner_id, updated_at=? "
            "WHERE id=? AND status='waiting' AND guest_id IS NULL",
            (me['id'], time.time(), row['id']))
        if r.rowcount:
            db.commit()
            _jq_emit(row['id'])
            _jq_room_broadcast(row['id'], guest_id=me['id'])
            _jq_lobby_broadcast()
            return jsonify({"ok": True, "rid": row['id']})
    return jsonify({"ok": False, "msg": "暂无可加入的公开房间"})






def jq_room(rid):
    me = D['current_user']()
    room = D['get_db']().execute(
        "SELECT r.*, ou.username as owner_name, gu.username as guest_name "
        "FROM jq_rooms r "
        "LEFT JOIN users ou ON ou.id=r.owner_id "
        "LEFT JOIN users gu ON gu.id=r.guest_id "
        "WHERE r.id=?", (rid,)
    ).fetchone()
    if not room:
        abort(404)
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    is_super = bool(me['is_super_admin'])
    is_spectator = not (is_owner or is_guest)   # 观战只读（超管也是只读）
    return render_template("jq_room.html", room=room, user=me,
                           is_owner=is_owner, is_guest=is_guest, is_super=is_super)






def jq_legal(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM jq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"}), 404
    if room['status'] != 'playing':
        return jsonify({"ok": True, "moves": []})
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    if not (is_owner or is_guest):
        return jsonify({"ok": False, "msg": "无权访问"}), 403
    if room['turn_user_id'] != me['id']:
        return jsonify({"ok": True, "moves": []})
    display, truth = _jq._jq_parse(room['board_state'] or _jq._jq_init())
    red = is_owner
    moves = _jq._jq_legal_moves(display, truth, red)
    return jsonify({"ok": True, "moves": [list(m) for m in moves]})






def jq_state(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM jq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"}), 404
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    # 观战：任何人可读
    return jsonify(dict(_jq_payload(room), ok=True))






def jq_move(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM jq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['status'] == 'waiting':
        return jsonify({"ok": False, "msg": "对局还没开始"})
    if room['status'] != 'playing':
        return jsonify({"ok": False, "msg": "对局已结束"})
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    is_super = bool(me['is_super_admin'])
    if not (is_owner or is_guest or is_super):
        return jsonify({"ok": False, "msg": "你不是本局玩家"})
    if is_super and not (is_owner or is_guest):
        return jsonify({"ok": False, "msg": "超管仅可观战，不可操纵"}), 403
    player_red = is_owner
    if room['turn_user_id'] != me['id']:
        return jsonify({"ok": False, "msg": "还没轮到你"})

    if request.form.get("resign") == "1":
        win_id = room['guest_id'] if player_red else room['owner_id']
        db.execute(
            "UPDATE jq_rooms SET status='finished', winner_id=?, updated_at=? WHERE id=?",
            (win_id, time.time(), rid))
        db.commit()
        return _jq_emit(rid)

    try:
        x1 = int(request.form.get("x1", "-1"))
        y1 = int(request.form.get("y1", "-1"))
        x2 = int(request.form.get("x2", "-1"))
        y2 = int(request.form.get("y2", "-1"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数错误"})
    if not (0 <= x1 < 9 and 0 <= y1 < 10 and 0 <= x2 < 9 and 0 <= y2 < 10):
        return jsonify({"ok": False, "msg": "坐标越界"})
    display, truth = _jq._jq_parse(room['board_state'] or _jq._jq_init())
    p = _jq._jq_at(display, x1, y1)
    if p == '.' or p.isupper() != player_red:
        return jsonify({"ok": False, "msg": "请选择己方棋子"})
    r = _jq._jq_make_move(display, truth, x1, y1, x2, y2)
    if r is None:
        return jsonify({"ok": False, "msg": _jq._jq_why_illegal(display, truth, x1, y1, x2, y2, player_red)})

    nd, nt, captured, check, end = r
    uci = _jq._jq_xy_to_uci(x1, y1) + _jq._jq_xy_to_uci(x2, y2)
    base_moves = (room['uci_moves'] or '').strip()
    idx = len(base_moves.split()) if base_moves else 0
    text = _jq._jq_move_text(display, truth, x1, y1, x2, y2, idx)
    move_text = room['move_text'] or '[]'
    try:
        mt_list = json.loads(move_text) if isinstance(move_text, str) else []
    except Exception:
        mt_list = []
    mt_list.append(text)

    if end:
        db.execute(
            "UPDATE jq_rooms SET board_state=?, prev_board=?, status='finished', winner_id=?, last_move=?, uci_moves=?, move_text=?, updated_at=? WHERE id=?",
            (nd + '|' + nt, room['board_state'] or '', me['id'], "%d,%d,%d,%d" % (x1, y1, x2, y2),
             (base_moves + ' ' + uci).strip(), json.dumps(mt_list, ensure_ascii=False), time.time(), rid))
        db.commit()
        return _jq_emit(rid)

    next_turn = room['guest_id'] if is_owner else room['owner_id']
    db.execute(
        "UPDATE jq_rooms SET board_state=?, prev_board=?, last_move=?, uci_moves=?, move_text=?, turn_user_id=?, updated_at=? WHERE id=?",
        (nd + '|' + nt, room['board_state'] or '', "%d,%d,%d,%d" % (x1, y1, x2, y2),
         (base_moves + ' ' + uci).strip(), json.dumps(mt_list, ensure_ascii=False), next_turn, time.time(), rid))
    db.commit()
    return _jq_emit(rid)






def jq_restart(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM jq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id']:
        return jsonify({"ok": False, "msg": "仅房主可重开"})
    db.execute(
        "UPDATE jq_rooms SET board_state=?, status='playing', winner_id=NULL, last_move=NULL, "
        "uci_moves='', move_text='[]', turn_user_id=owner_id, updated_at=? WHERE id=?",
        (_jq._jq_init(), time.time(), rid))
    db.commit()
    return jsonify({"ok": True})




