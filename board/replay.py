# -*- coding: utf-8 -*-
"""board/replay.py — 棋类统一复盘（规划文档 058 项）

现状（2026-11-01 盘点）
-----------------------
六款棋的对局历史记录能力参差不齐：

| 棋种 | 历史字段 | 能否复盘 |
|---|---|---|
| 中国象棋 xq | `uci_moves`（空格分隔 UCI） | ✅ `_xq_replay` 直接给每步棋盘 |
| 国际象棋 chess | `uci_moves` | ✅ 逐步重放 |
| 翻转棋 reversi | `move_hist`（`b:d3,w:c5`） | ✅ 按翻转规则重放 |
| 五子棋 gomoku | `uci_moves`（2026-11-01 起记录） | ✅ 按落子重放 |
| 军棋 jq | `uci_moves` | ⚠️ 有着法但没重放器（有暗子，复盘规则待定） |
| 围棋 go | `uci_moves`（2026-11-01 起记录） | ⚠️ 需要提子/劫争逻辑，未实现 |

统一 API
--------
    GET /api/board/replay/<game>/<rid>

返回：
    {"ok": true, "game": "xq", "rid": 12, "moves": ["h2e2", ...],
     "states": ["<初始棋盘>", ...], "supported": true,
     "status": "finished", "winner_id": 5}

    states 长度 = moves 长度 + 1（第一个是初始局面），前端按索引播放即可。
    不支持的棋种返回 supported=false + reason，不报错。

moves 格式按棋种区分（前端按 game 处理）：
    xq / chess / jq / gomoku / go  →  ['h2e2', '7,7', ...]
    reversi                        →  ['b:d3', 'w:c5', ...]
"""
from .deps import D


# game -> (表名, 历史字段名)
REPLAY_SOURCE = {
    "xq": ("xq_rooms", "uci_moves"),
    "chess": ("chess_rooms", "uci_moves"),
    "jq": ("jq_rooms", "uci_moves"),
    "reversi": ("reversi_rooms", "move_hist"),
    "gomoku": ("gomoku_rooms", "uci_moves"),
    "go": ("go_rooms", "uci_moves"),
}


def _parse_moves(game, raw):
    """把库里的历史串拆成统一的字符串列表。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    if game == "reversi":
        # 'b:d3,w:c5' → ['b:d3', 'w:c5']
        return [t.strip() for t in raw.split(",") if t.strip()]
    return raw.split()


def _states_xq(moves, room=None):
    """中国象棋：_xq_replay 直接返回每步棋盘列表。"""
    from .xq import _xq_replay
    boards, _checks = _xq_replay(" ".join(moves))
    return boards


def _states_chess(moves, room=None):
    """国际象棋：逐步重放（O(n²)，棋局步数不多，可接受）。"""
    from .chess import _cs_replay_state
    out = [_cs_replay_state("")]
    for i in range(len(moves)):
        out.append(_cs_replay_state(" ".join(moves[:i + 1])))
    return out


def _states_reversi(moves, room=None):
    """翻转棋：按 b/w 颜色 + GTP 顶点逐步落子。"""
    from .reversi import _rv_init, _rv_from_vertex, _rv_apply, RV_BLACK, RV_WHITE
    board = _rv_init()
    states = [board]
    for tok in moves:
        if ":" not in tok:
            break
        color, v = tok.split(":", 1)
        xy = _rv_from_vertex(v)
        if xy is None:
            break
        player = RV_BLACK if color.strip().lower() == "b" else RV_WHITE
        board = _rv_apply(board, xy[0], xy[1], player)
        states.append(board)
    return states


def _states_gomoku(moves, room=None):
    """五子棋：从空盘按黑白交替落子（'x,y'）。"""
    from .gomoku import GOMOKU_N, GOMOKU_EMPTY_BOARD, _gomoku_set
    board = GOMOKU_EMPTY_BOARD
    states = [board]
    player = "1"                      # 黑先
    for tok in moves:
        if "," not in tok:
            break
        try:
            x, y = [int(v) for v in tok.split(",", 1)]
        except (TypeError, ValueError):
            break
        if not (0 <= x < GOMOKU_N and 0 <= y < GOMOKU_N):
            break
        board = _gomoku_set(board, x, y, player)
        states.append(board)
        player = "2" if player == "1" else "1"
    return states


def _states_jq(moves, room=None):
    """军棋：用 truth（真实棋盘）重放 —— 对局已结束，复盘就该全明牌。

    display 是「己方视角」（对手棋子是背面），复盘看那个没意义。
    """
    import jieqi_game as J
    # 揭棋开局是随机打乱的，必须用建局时存下的 init_board 作起点；
    # 老房间没这个字段（2026-11-01 之前建的），重放不了。
    init = ""
    try:
        init = (room["init_board"] if room is not None else "") or ""
    except Exception:
        init = ""
    if not init:
        return []
    display, truth = J._jq_parse(init)
    states = [truth]
    for tok in moves:
        if len(tok) < 4:
            break
        xy1 = J._jq_uci_to_xy(tok[:2])
        xy2 = J._jq_uci_to_xy(tok[2:4])
        if xy1 is None or xy2 is None:
            break
        r = J._jq_make_move(display, truth, xy1[0], xy1[1], xy2[0], xy2[1])
        if not r:
            break
        display, truth = r[0], r[1]
        states.append(truth)
    return states


def _states_go(moves, room=None):
    """围棋：按 _go_apply 重放（带提子）。pass 不改变棋盘但要切换手数。"""
    from .go import _go_empty_board, _go_apply, GO_P1, GO_P2
    n = 19
    try:
        n = int(room["board_size"]) or 19
    except Exception:
        pass
    board = _go_empty_board(n)
    states = [board]
    player = GO_P1
    for tok in moves:
        if tok == "pass":
            states.append(board)
            player = GO_P2 if player == GO_P1 else GO_P1
            continue
        if "," not in tok:
            break
        try:
            x, y = [int(v) for v in tok.split(",", 1)]
        except (TypeError, ValueError):
            break
        if not (0 <= x < n and 0 <= y < n):
            break
        b2, _cap = _go_apply(board, x, y, player, n)
        if b2 is None:
            break
        board = b2
        states.append(board)
        player = GO_P2 if player == GO_P1 else GO_P1
    return states


STATE_BUILDERS = {
    "xq": _states_xq,
    "chess": _states_chess,
    "reversi": _states_reversi,
    "gomoku": _states_gomoku,
    "jq": _states_jq,
    "go": _states_go,
}

# 有历史但还没重放器的棋种 → 明确原因
NO_BUILDER_REASON = {
}


def load(game, rid):
    """取一局复盘。返回 dict（永远带 ok 字段）。"""
    game = (game or "").strip().lower()
    src = REPLAY_SOURCE.get(game)
    if not src:
        return {"ok": False, "msg": "未知棋种: %s" % game,
                "known": sorted(REPLAY_SOURCE.keys())}
    table, field = src

    db = D['get_db']()
    try:
        row = db.execute("SELECT * FROM %s WHERE id=?" % table, (rid,)).fetchone()
    except Exception as e:
        return {"ok": False, "msg": "查询失败: %s" % e}
    if not row:
        return {"ok": False, "msg": "房间不存在"}

    keys = row.keys()
    raw = (row[field] if field in keys else "") or ""
    moves = _parse_moves(game, raw)

    base = {
        "ok": True,
        "game": game,
        "rid": rid,
        "moves": moves,
        "status": (row["status"] if "status" in keys else "") or "",
        "winner_id": (row["winner_id"] if "winner_id" in keys else None),
        "owner_id": (row["owner_id"] if "owner_id" in keys else None),
        "guest_id": (row["guest_id"] if "guest_id" in keys else None),
    }

    if not moves:
        base.update({
            "supported": False,
            "states": [],
            "reason": "这局没有记录着法历史",
        })
        return base

    builder = STATE_BUILDERS.get(game)
    if builder is None:
        base.update({
            "supported": False,
            "states": [],
            "reason": NO_BUILDER_REASON.get(game, "%s 暂不支持重放" % game),
        })
        return base

    try:
        states = builder(moves, row)
    except Exception as e:
        base.update({"supported": False, "states": [],
                     "reason": "重放失败: %s" % e})
        return base

    if not states:
        base.update({"supported": False, "states": [],
                     "reason": "这局缺少开局记录（2026-11-01 之前建的房间），无法重放"})
        return base

    base.update({"supported": True, "states": states})
    return base


def api_board_replay(game, rid):
    """HTTP 处理（由 board/__init__.py 注册路由）。"""
    from flask import jsonify
    me = D['current_user']()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    res = load(game, rid)
    if not res.get("ok"):
        return jsonify(res), 404 if "不存在" in (res.get("msg") or "") else 400
    return jsonify(res)
