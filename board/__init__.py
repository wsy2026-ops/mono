# -*- coding: utf-8 -*-
"""board/ — 棋盘对战包（app.py 拆包第二批）。

内容：五子棋（RAPFI 引擎）/ 围棋 / 中国象棋 / 翻转棋 / 国际象棋 / 军棋。
小游戏在 games/ 包；象棋大师（xqmaster）路由壳在 xqm/ 包（下一批）。

endpoint 名与函数名一致，url_for 零改动；SocketIO 事件名与搬迁前一致。

2026-11-01（规划文档 058 第一步）：六份 `_register_xxx()` 收成
`protocol.register()` 数据驱动。标准七条路由由 protocol 生成，
各棋只写自己多出来的路由。**rule / endpoint / methods 与改前逐条一致。**
"""
from . import deps as _deps
from . import protocol

D = _deps.D


def init(app, deps=None):
    if deps:
        D.update(deps)
    from . import socket as _sio
    from . import gomoku, go as _go, xq as _xq, reversi as _rv, chess as _cs, jq as _jq

    _sio.register(D["socketio"], D)

    # ── 五子棋：标准七条，无额外 ──
    protocol.register(app, D, "gomoku", gomoku)

    # ── 围棋：标准七条 + socket 事件 ──
    protocol.register(app, D, "go", _go,
                      socket_events={"join_go": _go.go_socket_join})

    # ── 中国象棋：标准七条 + 8 条残局路由 + socket 事件 ──
    protocol.register(
        app, D, "xq", _xq,
        extras=[
            ("/game/xq/puzzles", "xq_puzzles_list", None, _xq.xq_puzzles_list),
            ("/game/xq/puzzle/new", "xq_puzzle_new_page", ["GET"], _xq.xq_puzzle_new_page),
            ("/api/xq/puzzle/new", "xq_puzzle_create", ["POST"], _xq.xq_puzzle_create),
            ("/game/xq/puzzle/<int:pid>", "xq_puzzle_page", None, _xq.xq_puzzle_page),
            ("/api/xq/puzzle/<int:pid>/move", "xq_puzzle_move", ["POST"], _xq.xq_puzzle_move),
            ("/api/xq/puzzle/<int:pid>/hint", "xq_puzzle_hint", ["POST"], _xq.xq_puzzle_hint),
            ("/api/xq/puzzle/<int:pid>/giveup", "xq_puzzle_giveup", ["POST"], _xq.xq_puzzle_giveup),
            ("/api/xq/puzzle/<int:pid>/delete", "xq_puzzle_delete", ["POST"], _xq.xq_puzzle_delete),
        ],
        socket_events={"join_xq": _xq.xq_socket_join})

    # ── 翻转棋：标准七条（delete 历史上挂在 chess 那一块注册，见下） ──
    protocol.register(app, D, "reversi", _rv)

    # ── 国际象棋：标准七条 + legal/delete，外加两条跨模块路由 ──
    # 注意：/api/reversi/<rid>/delete 和 /api/xq/<rid>/legal 原本就写在
    # chess 的注册块里，这里原样保留，避免 endpoint 冲突或漏注册。
    protocol.register(
        app, D, "chess", _cs,
        extras=[
            ("/api/chess/<int:rid>/legal", "chess_legal", ["POST"], _cs.chess_legal),
            ("/api/chess/<int:rid>/delete", "chess_delete", ["POST"], _cs.chess_delete),
            ("/api/reversi/<int:rid>/delete", "reversi_delete", ["POST"], _rv.reversi_delete),
            ("/api/xq/<int:rid>/legal", "xq_legal", None, _xq.xq_legal),
        ])

    # ── 军棋：标准七条 + create/legal + 两个 socket 事件 ──
    protocol.register(
        app, D, "jq", _jq,
        extras=[
            ("/api/jq/create", "jq_create", ["POST"], _jq.jq_create),
            ("/api/jq/<int:rid>/legal", "jq_legal", None, _jq.jq_legal),
        ],
        socket_events={
            "join_jq": _jq.jq_socket_join,
            "join_jq_lobby": _jq.jq_lobby_join,
        })

    # ── 058：棋类统一复盘 ──
    from .replay import api_board_replay
    app.add_url_rule("/api/board/replay/<game>/<int:rid>", "api_board_replay",
                     D["login_required"](api_board_replay), methods=None)

    def _board_replay_page(game, rid):
        from flask import render_template
        return render_template("board_replay.html", game=game, rid=rid,
                               user=D["current_user"]())

    app.add_url_rule("/board/replay/<game>/<int:rid>", "board_replay_page",
                     D["login_required"](_board_replay_page), methods=None)
