# -*- coding: utf-8 -*-
"""board/socket.py — 棋盘对战的 SocketIO  lobby/join 壳事件（从 app.py 搬迁）。"""

from flask import request
from flask_socketio import join_room

from .deps import D

def gomoku_lobby_join():
    """五子棋大厅实时更新订阅。"""
    user = D['current_user']()
    if user:
        join_room('gmk_lobby', request.sid)




def go_lobby_join():
    """围棋大厅实时更新订阅。"""
    user = D['current_user']()
    if user:
        join_room('go_lobby', request.sid)




def xq_lobby_join():
    """象棋大厅实时更新订阅。"""
    user = D['current_user']()
    if user:
        join_room('xq_lobby', request.sid)




def gomoku_socket_join(data):
    """五子棋房间实时推送订阅。"""
    user = D['current_user']()
    if not user:
        return
    rid = data.get('rid')
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return
    room = D['get_db']().execute("SELECT * FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return
    is_owner = room['owner_id'] == user['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == user['id']
    if not (is_owner or is_guest) and room['status'] != 'waiting':
        return
    join_room('gmk_%d' % rid, request.sid)




def reversi_socket_join(data=None):
    D['_game_socket_join']('reversi_rooms', 'rv', data)




def chess_socket_join(data=None):
    D['_game_socket_join']('chess_rooms', 'cs', data)




def reversi_lobby_join():
    user = D['current_user']()
    if user:
        join_room('rv_lobby', request.sid)




def chess_lobby_join():
    user = D['current_user']()
    if user:
        join_room('cs_lobby', request.sid)




def register(sio, D):
    """注册 SocketIO 事件（事件名与搬迁前一致）。"""
    from .deps import D as _D
    _D.update(D)
    for ev, fn in [
        ("join_gomoku_lobby", gomoku_lobby_join),
        ("join_go_lobby", go_lobby_join),
        ("join_xq_lobby", xq_lobby_join),
        ("join_gomoku", gomoku_socket_join),
        ("join_reversi", reversi_socket_join),
        ("join_chess", chess_socket_join),
        ("join_reversi_lobby", reversi_lobby_join),
        ("join_chess_lobby", chess_lobby_join),
    ]:
        sio.on_event(ev, fn)
