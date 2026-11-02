# -*- coding: utf-8 -*-
"""xqm/ — 象棋大师包（app.py 拆包）。

对局管理/匹配/赛季/钱包/抽签/API + SocketIO。规则引擎是 xqmaster.py。
endpoint 名与函数名一致，url_for 零改动。
"""
from . import deps as _deps

D = _deps.D


def init(app, deps=None):
    if deps:
        D.update(deps)
    from . import core
    _register(app, D, core)


def _register(app, D, core):
    from .core import (
        xqmaster_page, xqmaster_match_page, xqmaster_rank_page,
        xqmaster_codex_page, xqmaster_lineup_page, xqmaster_settings_page,
        api_xqm_season, xqmaster_lottery_page, api_xqm_replay,
        xqmaster_replay_page, xqmaster_battle_page, xqmaster_result_page,
        api_xqm_lineup, api_xqm_me, api_xqm_grow, api_xqm_lottery,
        api_xqm_grow_upgrade, api_xqm_queue, api_xqm_queue_leave,
        api_xqm_current, api_xqm_game, api_xqm_move, api_xqm_pass,
        api_xqm_draw, api_xqm_draw_accept, api_xqm_draw_decline,
        api_xqm_resign, api_xqm_rank, xqm_socket_join)
    LR = D["login_required"]
    R = [
        ("/game/xqmaster", "xqmaster_page", None, xqmaster_page),
        ("/game/xqmaster/match", "xqmaster_match_page", None, xqmaster_match_page),
        ("/game/xqmaster/rank", "xqmaster_rank_page", None, xqmaster_rank_page),
        ("/game/xqmaster/codex", "xqmaster_codex_page", None, xqmaster_codex_page),
        ("/game/xqmaster/lineup", "xqmaster_lineup_page", None, xqmaster_lineup_page),
        ("/game/xqmaster/settings", "xqmaster_settings_page", None, xqmaster_settings_page),
        ("/api/xqm/season", "api_xqm_season", None, api_xqm_season),
        ("/game/xqmaster/lottery", "xqmaster_lottery_page", None, xqmaster_lottery_page),
        ("/api/xqm/game/<int:gid>/replay", "api_xqm_replay", None, api_xqm_replay),
        ("/game/xqmaster/replay/<int:gid>", "xqmaster_replay_page", None, xqmaster_replay_page),
        ("/game/xqmaster/battle/<int:gid>", "xqmaster_battle_page", None, xqmaster_battle_page),
        ("/game/xqmaster/result/<int:gid>", "xqmaster_result_page", None, xqmaster_result_page),
        ("/api/xqm/lineup", "api_xqm_lineup", ["GET", "POST"], api_xqm_lineup),
        ("/api/xqm/me", "api_xqm_me", None, api_xqm_me),
        ("/api/xqm/grow", "api_xqm_grow", None, api_xqm_grow),
        ("/api/xqm/lottery", "api_xqm_lottery", ["POST"], api_xqm_lottery),
        ("/api/xqm/grow/upgrade", "api_xqm_grow_upgrade", ["POST"], api_xqm_grow_upgrade),
        ("/api/xqm/queue", "api_xqm_queue", ["GET", "POST"], api_xqm_queue),
        ("/api/xqm/queue/leave", "api_xqm_queue_leave", ["POST"], api_xqm_queue_leave),
        ("/api/xqm/current", "api_xqm_current", None, api_xqm_current),
        ("/api/xqm/game/<int:gid>", "api_xqm_game", None, api_xqm_game),
        ("/api/xqm/game/<int:gid>/move", "api_xqm_move", ["POST"], api_xqm_move),
        ("/api/xqm/game/<int:gid>/pass", "api_xqm_pass", ["POST"], api_xqm_pass),
        ("/api/xqm/game/<int:gid>/draw", "api_xqm_draw", ["POST"], api_xqm_draw),
        ("/api/xqm/game/<int:gid>/draw/accept", "api_xqm_draw_accept", ["POST"], api_xqm_draw_accept),
        ("/api/xqm/game/<int:gid>/draw/decline", "api_xqm_draw_decline", ["POST"], api_xqm_draw_decline),
        ("/api/xqm/game/<int:gid>/resign", "api_xqm_resign", ["POST"], api_xqm_resign),
        ("/api/xqm/rank", "api_xqm_rank", None, api_xqm_rank),
    ]
    for rule, ep, methods, fn in R:
        app.add_url_rule(rule, ep, LR(fn), methods=methods)
    D["socketio"].on_event("join_xqm", xqm_socket_join)
