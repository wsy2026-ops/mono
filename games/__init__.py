# -*- coding: utf-8 -*-
"""games/ — 小游戏包（app.py 拆包第一批）。

内容：扫雷/汉诺塔/华容道（中/德）/24点/数独/色盲派对/跳一跳/闲弈/
游戏中心/成绩提交/回放/top3/排行榜/游戏管理。
棋盘对战（gomoku/go/xq/chess/reversi/jq）在 board/ 包（下一批）。

endpoint 名与函数名一致，url_for 零改动。
"""
from . import deps as _deps

D = _deps.D


def init(app, deps=None):
    if deps:
        D.update(deps)
    from . import mini
    _register(app, D, mini)


def _register(app, D, mini):
    """按原装饰器语义注册（login/super_admin 从备份逐一核对）。"""
    from .mini import (game_leaderboard, game_rank_page, guling_page,
                       games_center, api_game_replay_save, api_game_top3,
                       api_game_replay_get, admin_game_reset, api_admin_game_reset,
                       api_admin_game_set, game_24, submit_24_score, game_sudoku,
                       game_2048, submit_2048_score, api_game_ach_flag,
                       game_tetris, submit_tetris_score, game_evolve, game_kubi,
                       game_kubition,
                       submit_sudoku_score, game_color_blind, submit_color_blind_score,
                       game_jump, submit_jump_score, game_xianyi, submit_xianyi_score,
                       game_hanoi, submit_hanoi_score, game_klotski, get_klotski_rank,
                       submit_klotski_time, game_klotski_cn, get_klotski_cn_rank,
                       submit_klotski_cn_time, game_mine, api_mine_rank,
                       submit_mine_time, api_game_submit)
    from .saves import (api_game_save, api_game_save_history,
                        api_game_save_restore)

    LR, SA = D["login_required"], D["super_admin_required"]
    R = [
        ("/game/leaderboard", "game_leaderboard", None, game_leaderboard, LR),
        ("/game-rank/<field>", "game_rank_page", None, game_rank_page, None),
        ("/game/guling", "guling_page", None, guling_page, LR),
        ("/games", "games_center", None, games_center, None),
        # 075：统一存档槽位（适配器门面，底层仍读写各游戏自己的表）
        ("/api/game/save/<game>", "api_game_save", ["GET", "POST"], api_game_save, LR),
        ("/api/game/save/<game>/history", "api_game_save_history", None, api_game_save_history, LR),
        ("/api/game/save/<game>/restore", "api_game_save_restore", ["POST"], api_game_save_restore, LR),
        # 075：统一成绩提交入口（纯分数类游戏走这里，特殊逻辑的返回 400 指向旧接口）
        ("/api/game/submit/<game>", "api_game_submit", ["POST"], api_game_submit, LR),
        ("/api/game/replay", "api_game_replay_save", ["POST"], api_game_replay_save, LR),
        ("/api/game/top3/<game>", "api_game_top3", None, api_game_top3, LR),
        ("/api/game/replay/<int:rid>", "api_game_replay_get", None, api_game_replay_get, LR),
        ("/admin/game/reset", "admin_game_reset", ["POST"], admin_game_reset, SA),
        ("/api/admin/game/reset", "api_admin_game_reset", ["POST"], api_admin_game_reset, SA),
        ("/api/admin/game/set", "api_admin_game_set", ["POST"], api_admin_game_set, SA),
        ("/game/24", "game_24", None, game_24, LR),
        ("/api/submit_24_score", "submit_24_score", ["POST"], submit_24_score, LR),
        ("/game/2048", "game_2048", None, game_2048, LR),
        ("/api/submit_2048_score", "submit_2048_score", ["POST"], submit_2048_score, LR),
        ("/api/game/ach_flag", "api_game_ach_flag", ["POST"], api_game_ach_flag, LR),
        ("/game/tetris", "game_tetris", None, game_tetris, LR),
        ("/game/evolve", "game_evolve", None, game_evolve, LR),
        ("/game/kubi", "game_kubi", None, game_kubi, LR),
        ("/game/kubition", "game_kubition", None, game_kubition, LR),
        ("/api/submit_tetris_score", "submit_tetris_score", ["POST"], submit_tetris_score, LR),
        ("/game/sudoku", "game_sudoku", None, game_sudoku, LR),
        ("/api/submit_sudoku_score", "submit_sudoku_score", ["POST"], submit_sudoku_score, LR),
        ("/game/color_blind", "game_color_blind", None, game_color_blind, LR),
        ("/api/submit_color_blind_score", "submit_color_blind_score", ["POST"], submit_color_blind_score, LR),
        ("/game/jump", "game_jump", None, game_jump, LR),
        ("/api/submit_jump_score", "submit_jump_score", ["POST"], submit_jump_score, LR),
        ("/game/xianyi", "game_xianyi", None, game_xianyi, LR),
        ("/api/submit_xianyi_score", "submit_xianyi_score", ["POST"], submit_xianyi_score, LR),
        ("/game/hanoi", "game_hanoi", None, game_hanoi, LR),
        ("/api/submit_hanoi_score", "submit_hanoi_score", ["POST"], submit_hanoi_score, LR),
        ("/game/klotski", "game_klotski", None, game_klotski, LR),
        ("/api/klotski_rank/<int:size>", "get_klotski_rank", None, get_klotski_rank, LR),
        ("/api/submit_klotski_time", "submit_klotski_time", ["POST"], submit_klotski_time, LR),
        ("/game/klotski_cn", "game_klotski_cn", None, game_klotski_cn, LR),
        ("/api/klotski_cn_rank/<int:layout_id>", "get_klotski_cn_rank", None, get_klotski_cn_rank, LR),
        ("/api/submit_klotski_cn_time", "submit_klotski_cn_time", ["POST"], submit_klotski_cn_time, LR),
        ("/game/mine", "game_mine", None, game_mine, LR),
        ("/api/mine_rank/<mode>", "api_mine_rank", None, api_mine_rank, LR),
        ("/api/submit_mine_time", "submit_mine_time", ["POST"], submit_mine_time, LR),
    ]
    for rule, ep, methods, fn, guard in R:
        view = guard(fn) if guard else fn
        app.add_url_rule(rule, ep, view, methods=methods)
