# -*- coding: utf-8 -*-
"""games/mini.py — 小游戏模块（051 拆包 · games 第一批）。

从 app.py 搬迁：扫雷/汉诺塔/华容道/24点/数独/色盲/跳一跳/闲弈/游戏中心/
成绩提交 API/回放/排行榜/游戏管理。helper 一律走 deps.D 注入（不 import app）。
"""
import json
import os
import time

from flask import (send_from_directory, abort, flash, jsonify, redirect,
                   render_template, request, url_for)

from .deps import D


def _board_wins_leaderboard(game, limit=20):
    db = D['get_db']()
    return db.execute(
        "SELECT gs.user_id, u.username, gs.wins, gs.draws, gs.losses, gs.games "
        "FROM board_game_stats gs JOIN users u ON u.id=gs.user_id "
        "WHERE gs.game=? AND gs.games>0 "
        "ORDER BY gs.wins DESC, gs.draws DESC, gs.losses ASC, gs.games ASC "
        "LIMIT ?", (game, limit)).fetchall()


def _my_ai_survival(games):
    """当前用户在指定游戏里的 AI 对战数据。"""
    db = D['get_db']()
    me = D['current_user']()
    if isinstance(games, str):
        games = (games,)
    if not games or not me:
        return {'ai_wins': 0, 'ai_best_steps': 0, 'ai_games': 0}
    placeholders = ','.join('?' * len(games))
    row = db.execute(
        "SELECT COALESCE(SUM(ai_wins),0) AS ai_wins, "
        "COALESCE(MAX(ai_best_steps),0) AS ai_best_steps, "
        "COALESCE(SUM(ai_games),0) AS ai_games "
        "FROM board_game_stats WHERE user_id=? AND game IN (%s)" % placeholders,
        (me['id'],) + tuple(games)).fetchone()
    return row


MINE_MODES = {
    'beginner': {'label': '初级', 'cols': 9, 'rows': 9, 'mines': 10, 'field': 'mine_best_ms_beginner'},
    'intermediate': {'label': '中级', 'cols': 16, 'rows': 16, 'mines': 40, 'field': 'mine_best_ms_intermediate'},
    'expert': {'label': '高级', 'cols': 30, 'rows': 16, 'mines': 99, 'field': 'mine_best_ms_expert'},
}


def _mine_field(mode):
    m = MINE_MODES.get(mode)
    return m['field'] if m else None


def _guling_enabled():
    return (D['get_config']("guling_enabled", "1") == "1")


def _game_score_rank(field, me):
    db = D['get_db']()
    # 028：top10 榜单按「游戏」缓存 5 分钟（提交成绩/授成就时主动失效）；
    # user_rank（我的名次）仍是实时查询 —— 用户要看自己最新排位。
    rank_list = D['cache'].remember(
        "rank:top:%s" % field, D['cache'].DEFAULT_TTL,
        lambda: [{"username": r["username"], "score": r["score"]}
                 for r in db.execute(
                     "SELECT username, %s AS score FROM users ORDER BY %s DESC LIMIT 10"
                     % (field, field)).fetchall()])
    user_rank = db.execute(
        "SELECT COUNT(*) + 1 AS r FROM users WHERE %s > ?" % field, (me[field] or 0,)).fetchone()['r']
    return rank_list, user_rank


HANOI_MIN_MS_PER_MOVE = 55.0

# 反作弊：各游戏「不可能低于」的最短用时（毫秒）。宁可宽松，也别误杀正常玩家。


GAME_MIN_MS = {
    'hanoi':       {str(i): (1 << i) - 1 for i in range(2, 10)},  # 下面再乘每步耗时
    'klotski':     {'4': 2500, '5': 4000, '6': 7000},
    'klotski_cn':  {'1': 1500, '2': 2500, '3': 3500, '4': 4500},
    'mine':        {'beginner': 2500, 'intermediate': 12000, 'expert': 35000},
    'jump':        {'': 1200},
    'xianyi':      {'': 1200},
    'color_blind': {'': 1000},
    '24':          {'': 1200},
    '2048':        {'': 3000},
    'tetris':      {'': 2000},
    'sudoku':      {'': 15000},
}


def _game_time_ok(game, sub, ms):
    """用时是不是慢到「人能做到」。没有配置的游戏一律放行。"""
    rules = GAME_MIN_MS.get(game)
    if not rules:
        return True
    floor = rules.get(str(sub))
    if floor is None:
        floor = rules.get('')
    if floor is None:
        return True
    if game == 'hanoi':
        floor = floor * HANOI_MIN_MS_PER_MOVE      # 配置里存的是步数
    return ms >= floor


_GAME_BOARDS = {
    'hanoi':       [(str(i), '%d 层' % i, 'time') for i in range(2, 10)],
    '24':          [('', '标准', 'score')],
    '2048':        [('', '标准', 'score')],
    'tetris':      [('', '标准', 'score')],
    'sudoku':      [('', '标准', 'score')],
    'color_blind': [('', '标准', 'score')],
    'jump':        [('', '标准', 'score')],
    'xianyi':      [('', '标准', 'score')],
    'klotski':     [('4', '4×4', 'time'), ('5', '5×5', 'time'), ('6', '6×6', 'time')],
    'klotski_cn':  [('1', '布局 1', 'time'), ('2', '布局 2', 'time'),
                    ('3', '布局 3', 'time'), ('4', '布局 4', 'time')],
    'mine':        [('beginner', '初级', 'time'), ('intermediate', '中级', 'time'),
                    ('expert', '高级', 'time')],
}


_GAME_SCORE_FIELDS = {
    'high_score_24': '24 点',
    'high_score_2048': '2048',
    'high_score_tetris': '俄罗斯方块',
    'high_score_sudoku': '数独',
    'high_score_klotski': '华容道',
    'high_score_color_blind': '色盲派对',
    'high_score_jump': '跳一跳',
    'high_score_xianyi': '仙弈破阵',
    'high_score_hanoi': '汉诺塔（最高阶数）',
    'best_time_klotski_4': '华容道 4×4 最佳时间',
    'best_time_klotski_5': '华容道 5×5 最佳时间',
    'best_time_klotski_6': '华容道 6×6 最佳时间',
    'best_time_klotski_cn_1': '华容道横版 1',
    'best_time_klotski_cn_2': '华容道横版 2',
    'best_time_klotski_cn_3': '华容道横版 3',
    'best_time_klotski_cn_4': '华容道横版 4',
    'hanoi_best_moves': '汉诺塔最少步数',
    'hanoi_best_ms': '汉诺塔最快用时（毫秒）',
    'mine_best_ms_beginner': '扫雷初级最佳用时（毫秒）',
    'mine_best_ms_intermediate': '扫雷中级最佳用时（毫秒）',
    'mine_best_ms_expert': '扫雷高级最佳用时（毫秒）',
}


_GAME_FIELD_IS_TIME = {
    'best_time_klotski_4', 'best_time_klotski_5', 'best_time_klotski_6',
    'best_time_klotski_cn_1', 'best_time_klotski_cn_2', 'best_time_klotski_cn_3',
    'best_time_klotski_cn_4',
    'hanoi_best_moves', 'hanoi_best_ms',
    'mine_best_ms_beginner', 'mine_best_ms_intermediate', 'mine_best_ms_expert',
}


def _game_reset_sql():
    """生成「把全部游戏成绩清零」的 SET 片段。

    由 _GAME_SCORE_FIELDS 派生 —— 新增游戏时只需改那张表，重置逻辑自动跟上。
    （此前这里是手写硬编码，曾与字段表脱节。）
    """
    return ", ".join("%s=0" % f for f in _GAME_SCORE_FIELDS)


def _hanoi_fmt_ms(ms):
    """毫秒 → '01:23.4'（榜单展示用）。"""
    try:
        ms = int(ms or 0)
    except (TypeError, ValueError):
        return '--'
    if ms <= 0:
        return '--'
    tenth = (ms // 100) % 10
    sec = ms // 1000
    h, m = sec // 3600, (sec % 3600) // 60
    s2 = sec % 60
    p2 = lambda n: ('0' if n < 10 else '') + str(n)
    return ('%d:' % h if h else '') + p2(m) + ':' + p2(s2) + '.' + str(tenth)


_HANOI_RANK_SQL = (
    "SELECT username, high_score_hanoi AS level, hanoi_best_ms AS ms, hanoi_best_moves AS moves "
    "FROM users WHERE high_score_hanoi > 0 "
    "ORDER BY high_score_hanoi DESC, hanoi_best_ms ASC, username ASC LIMIT 10")


def _klotski_field(size):
    return {4: 'best_time_klotski_4', 5: 'best_time_klotski_5', 6: 'best_time_klotski_6'}.get(int(size))


def _klotski_cn_field(layout_id):
    return {1: 'best_time_klotski_cn_1', 2: 'best_time_klotski_cn_2',
            3: 'best_time_klotski_cn_3', 4: 'best_time_klotski_cn_4'}.get(int(layout_id))







def game_leaderboard():
    """旧版棋类排行榜入口：现在已拆分进各游戏大厅，这里直接跳回游戏中心。"""
    return redirect(url_for("games_center"))




def game_rank_page(field):
    """分游戏完整榜单（027：全局榜点进来的落地页）。"""
    me = D['current_user']()
    name = None
    kind = "score"
    for f, nm in _GAME_SCORE_ROWS:
        if f == field:
            name, kind = nm, "score"
    for f, nm in _GAME_TIME_ROWS:
        if f == field:
            name, kind = nm, "time"
    if not name:
        abort(404)
    db = D['get_db']()
    if kind == "score":
        rows = db.execute("SELECT username, %s AS v FROM users WHERE %s > 0 "
                          "ORDER BY %s DESC LIMIT 20" % (field, field, field)).fetchall()
    else:
        rows = db.execute("SELECT username, %s AS v FROM users WHERE %s > 0 "
                          "ORDER BY %s ASC LIMIT 20" % (field, field, field)).fetchall()
    rows = [{"username": r['username'],
             "v": str(r['v']) if kind == "score" else _fmt_time(r['v'], field)} for r in rows]
    my_rank = None
    if me:
        my_row = db.execute("SELECT %s AS v FROM users WHERE id=?" % field,
                            (me['id'],)).fetchone()
        if my_row and (my_row['v'] or 0) > 0:
            if kind == "score":
                my_rank = db.execute(
                    "SELECT COUNT(*)+1 AS r FROM users WHERE %s > ?" % field,
                    (my_row['v'],)).fetchone()['r']
            else:
                my_rank = db.execute(
                    "SELECT COUNT(*)+1 AS r FROM users WHERE %s > 0 AND %s < ?" % (field, field),
                    (my_row['v'],)).fetchone()['r']
    return render_template("game_rank.html", name=name, kind=kind, rows=rows,
                           my_rank=my_rank, me=me)


# ==================== 029 标签体系 / 030 版块主题 ====================






def guling_page():
    me = D['current_user']()
    if not D['_oj_super'](me) or not _guling_enabled():
        abort(404)
    return render_template("guling.html", me=me, active="games")






def games_center():
    return render_template("games.html", user=D['current_user']())






def api_game_replay_save():
    me = D['current_user']()
    data = request.get_json(silent=True) or {}
    game = (data.get('game') or '').strip()[:32]
    sub = str(data.get('sub') or '').strip()[:16]
    if game not in _GAME_BOARDS:
        return jsonify({'ok': False, 'msg': '未知游戏'}), 400
    kind = 'time' if data.get('kind') == 'time' else 'score'
    try:
        score = int(data.get('score') or 0)
        duration = int(data.get('duration_ms') or 0)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': '参数不合法'}), 400
    frames = data.get('frames') or []
    if not isinstance(frames, list) or not frames:
        return jsonify({'ok': False, 'msg': '没有可回放的内容'}), 400
    if len(frames) > 4000:
        frames = frames[:4000]
    raw = json.dumps(frames, ensure_ascii=False, separators=(',', ':'))
    if len(raw) > 900 * 1024:
        return jsonify({'ok': False, 'msg': '回放数据过大'}), 400

    # 反作弊：帧的时间轴必须自洽，且成绩要和回放对得上
    prev = -1.0
    for f in frames:
        t = f.get('t') if isinstance(f, dict) else None
        if not isinstance(t, (int, float)) or t < 0 or t < prev:
            return jsonify({'ok': False, 'msg': '回放时间轴异常'}), 400
        prev = t
    if duration and prev > duration + 5000:
        return jsonify({'ok': False, 'msg': '回放时间轴与总时长不符'}), 400
    if kind == 'time':
        if abs(score - duration) > max(3000, duration * 0.3):
            return jsonify({'ok': False, 'msg': '成绩与回放时长不符'}), 400
        if not _game_time_ok(game, sub, score):
            return jsonify({'ok': False, 'msg': '这个用时不可能做到，成绩无效'}), 400
    elif score < 0:
        return jsonify({'ok': False, 'msg': '无效成绩'}), 400

    db = D['get_db']()
    db.execute(
        "INSERT INTO game_replays (game, sub, user_id, username, score, kind, duration_ms, frames, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (game, sub, me['id'], me['username'], score, kind, duration, raw, time.time()))

    order = "score ASC" if kind == 'time' else "score DESC"
    keep = [r['id'] for r in db.execute(
        "SELECT id FROM game_replays WHERE game=? AND sub=? ORDER BY %s, created_at ASC LIMIT 3" % order,
        (game, sub)).fetchall()]
    if keep:
        db.execute("DELETE FROM game_replays WHERE game=? AND sub=? AND id NOT IN (%s)"
                   % ",".join("?" * len(keep)), (game, sub) + tuple(keep))
    db.commit()

    rank = db.execute(
        "SELECT COUNT(*)+1 AS r FROM game_replays WHERE game=? AND sub=? AND %s"
        % ("score < ?" if kind == 'time' else "score > ?"),
        (game, sub, score)).fetchone()['r']
    return jsonify({'ok': True, 'rank': rank})






def api_game_top3(game):
    if game not in _GAME_BOARDS:
        return jsonify({'ok': False, 'msg': '未知游戏'}), 404
    db = D['get_db']()
    boards = []
    for sub, label, kind in _GAME_BOARDS[game]:
        order = "score ASC" if kind == 'time' else "score DESC"
        rows = db.execute(
            "SELECT id, username, score, kind, duration_ms, created_at FROM game_replays "
            "WHERE game=? AND sub=? ORDER BY %s, created_at ASC LIMIT 3" % order,
            (game, sub)).fetchall()
        boards.append({
            'sub': sub, 'label': label, 'kind': kind,
            'rows': [{'id': r['id'], 'name': r['username'], 'score': r['score'],
                      'duration_ms': r['duration_ms'], 'at': r['created_at']} for r in rows],
        })
    return jsonify({'ok': True, 'boards': boards})






def api_game_replay_get(rid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM game_replays WHERE id=?", (rid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '回放不存在'}), 404
    # 只能看前三名和自己的回放
    if row['user_id'] != me['id']:
        order = "score ASC" if row['kind'] == 'time' else "score DESC"
        top = [r['id'] for r in db.execute(
            "SELECT id FROM game_replays WHERE game=? AND sub=? ORDER BY %s, created_at ASC LIMIT 3" % order,
            (row['game'], row['sub'])).fetchall()]
        if row['id'] not in top:
            return jsonify({'ok': False, 'msg': '只能查看前三名或自己的回放'}), 403
    try:
        frames = json.loads(row['frames'] or '[]')
    except Exception:
        frames = []
    return jsonify({'ok': True, 'game': row['game'], 'sub': row['sub'],
                    'name': row['username'], 'score': row['score'], 'kind': row['kind'],
                    'duration_ms': row['duration_ms'], 'frames': frames})






def admin_game_reset():
    """超管重置指定用户的全部游戏成绩（清分）。"""
    me = D['current_user']()
    uid_raw = (request.form.get("user_id") or "").strip()
    target = None
    if uid_raw.isdigit():
        target = D['get_user_by_id'](int(uid_raw))
    if not target:
        target = D['get_user_by_name'](uid_raw)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    db = D['get_db']()
    db.execute("UPDATE users SET %s WHERE id=?" % _game_reset_sql(), (target['id'],))
    db.commit()
    D['add_admin_log'](me, "game_reset", target['username'], "重置全部游戏成绩")
    flash("已重置 %s 的全部游戏成绩" % target['username'], "ok")
    return redirect(url_for("admin"))






def api_admin_game_reset():
    me = D['current_user']()
    data = request.get_json(silent=True) or {}
    uid_raw = str(data.get('user_id') or '')
    target = None
    if uid_raw.isdigit():
        target = D['get_user_by_id'](int(uid_raw))
    if not target:
        target = D['get_user_by_name'](uid_raw)
    if not target:
        return jsonify({"ok": False, "msg": "用户不存在"}), 404
    db = D['get_db']()
    db.execute("UPDATE users SET %s WHERE id=?" % _game_reset_sql(), (target['id'],))
    db.commit()
    D['add_admin_log'](me, "game_reset", target['username'], "重置全部游戏成绩")
    return jsonify({"ok": True, "msg": "已重置 %s 的游戏成绩" % target['username']})






def api_admin_game_set():
    """超管将任意用户的任意游戏成绩改为指定值。"""
    me = D['current_user']()
    data = request.get_json(silent=True) or {}
    uid_raw = str(data.get('user_id') or '')
    field = (data.get('field') or '').strip()
    try:
        value = int(data.get('value'))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "分数非法"}), 400
    if field not in _GAME_SCORE_FIELDS:
        return jsonify({"ok": False, "msg": "游戏字段非法"}), 400
    if value < 0 or value > 9999999:
        return jsonify({"ok": False, "msg": "分数超出范围"}), 400
    target = None
    if uid_raw.isdigit():
        target = D['get_user_by_id'](int(uid_raw))
    if not target:
        target = D['get_user_by_name'](uid_raw)
    if not target:
        return jsonify({"ok": False, "msg": "用户不存在"}), 404
    db = D['get_db']()
    db.execute("UPDATE users SET %s=? WHERE id=?" % field, (value, target['id']))
    db.commit()
    D['add_admin_log'](me, "game_set", target['username'],
                  "%s 改为 %s" % (_GAME_SCORE_FIELDS[field], value))
    return jsonify({"ok": True, "msg": "已将 %s 的 [%s] 设为 %s" % (
        target['username'], _GAME_SCORE_FIELDS[field], value)})






def game_24():
    me = D['current_user']()
    rank_list, user_rank = _game_score_rank('high_score_24', me)
    return render_template("24_puzzle.html", user=me, rank_list=rank_list, user_rank=user_rank)






def submit_24_score():
    return D['_submit_score_common']('high_score_24', game='24')


# 前端可上报的成就标记字段（白名单，防止乱改用户表）
_ACH_FLAG_FIELDS = ('sudoku_perfect', 'jump_center_streak', 'mine_noflag_expert')


def api_game_ach_flag():
    """游戏前端上报成就标记。

    sudoku_perfect      : 数独完成且全程没出错 → 1
    jump_center_streak  : 跳一跳连续落在平台中心的最大次数（只增不减）
    """
    me = D['current_user']()
    if not me:
        return jsonify({"ok": False, "msg": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    field = (data.get('field') or '').strip()
    if field not in _ACH_FLAG_FIELDS:
        return jsonify({"ok": False, "msg": "非法字段"}), 400
    try:
        val = int(data.get('value') or 0)
    except (TypeError, ValueError):
        val = 0
    if val <= 0:
        return jsonify({"ok": False, "msg": "无效数值"}), 400

    db = D['get_db']()
    try:
        cur = db.execute("SELECT IFNULL(%s, 0) v FROM users WHERE id=?" % field,
                         (me['id'],)).fetchone()
        if field == 'jump_center_streak' and cur and val <= int(cur['v'] or 0):
            return jsonify({"ok": True, "noop": True})
        db.execute("UPDATE users SET %s=? WHERE id=?" % field, (val, me['id']))
        db.commit()
    except Exception as e:
        return jsonify({"ok": False, "msg": "字段不存在: %s" % e}), 500

    try:
        D['_ach_after_submit'](me['id'])
    except Exception:
        pass
    return jsonify({"ok": True})


def game_2048():
    me = D['current_user']()
    rank_list, user_rank = _game_score_rank('high_score_2048', me)
    return render_template("2048.html", user=me, rank_list=rank_list, user_rank=user_rank)


_MT2048_READY = [False]


def _ensure_max_tile(db):
    """懒建 users.max_tile_2048（2048 成就靠它判，分数不能反推方块）。"""
    if _MT2048_READY[0]:
        return
    try:
        db.execute("ALTER TABLE users ADD COLUMN max_tile_2048 INTEGER NOT NULL DEFAULT 0")
        db.commit()
    except Exception:
        pass
    _MT2048_READY[0] = True


def submit_2048_score():
    """2048 提交：先把本局最大方块写进 users.max_tile_2048，再走通用分数流程。"""
    db = D['get_db']()
    _ensure_max_tile(db)
    me = D['current_user']()
    if me:
        try:
            mt = int((request.get_json() or {}).get('max_tile') or 0)
        except (TypeError, ValueError):
            mt = 0
        if mt > 0:
            row = db.execute("SELECT IFNULL(max_tile_2048, 0) v FROM users WHERE id=?",
                             (me['id'],)).fetchone()
            if row and mt > int(row['v'] or 0):
                db.execute("UPDATE users SET max_tile_2048=? WHERE id=?", (mt, me['id']))
                db.commit()
    return D['_submit_score_common']('high_score_2048', game='2048')


def game_tetris():
    me = D['current_user']()
    rank_list, user_rank = _game_score_rank('high_score_tetris', me)
    return render_template("tetris.html", user=me, rank_list=rank_list, user_rank=user_rank)


def submit_tetris_score():
    return D['_submit_score_common']('high_score_tetris', game='tetris')


def game_evolve():
    """进化 Evolve（pmotschmann 的开源放置游戏，部署在 static/evolve）。

    原版是单机，联机改造在 P1 阶段只做「能看到其他玩家」。
    """
    import os as _os
    base = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'static', 'evolve')
    resp = send_from_directory(base, 'index.html')
    # ★ index.html 必须不缓存——否则它里面的 main.js?v=xxx 一直是旧的，
    #   导致 main.js 也永远拉旧版（用户看不到任何更新）
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


def game_kubi():
    """超苦逼冒险者（gura8390/KUBI 的复刻版，纯前端 HTML5 文字生存游戏）。

    原版在新浪云 SAE 上已经挂了（502），这个是 GitHub 上的复刻。
    """
    import os as _os
    base = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         'static', 'kubi')
    resp = send_from_directory(base, 'index.html')
    # 同 Evolve：HTML 不缓存，否则改了用户看不到
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


def game_kubition():
    """超苦逼冒险者 v0.32（原版静态副本，maouzju/kubition）。

    React 0.14 + jQuery 的老版本，作者把原版搬到了 GitHub Pages。
    依赖已全部本地化（build/ 下），零外链。
    """
    import os as _os
    base = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         'static', 'kubition')
    resp = send_from_directory(base, 'index.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp






def game_sudoku():
    me = D['current_user']()
    rank_list, user_rank = _game_score_rank('high_score_sudoku', me)
    return render_template("sudoku.html", user=me, rank_list=rank_list, user_rank=user_rank)






def submit_sudoku_score():
    return D['_submit_score_common']('high_score_sudoku', game='sudoku')






def game_color_blind():
    me = D['current_user']()
    rank_list, user_rank = _game_score_rank('high_score_color_blind', me)
    return render_template("color_blind.html", user=me, rank_list=rank_list, user_rank=user_rank)






def submit_color_blind_score():
    return D['_submit_score_common']('high_score_color_blind', game='colorblind')






def game_jump():
    me = D['current_user']()
    rank_list, user_rank = _game_score_rank('high_score_jump', me)
    return render_template("jump.html", user=me, rank_list=rank_list, user_rank=user_rank)






def submit_jump_score():
    return D['_submit_score_common']('high_score_jump', game='jump')






def game_xianyi():
    me = D['current_user']()
    rank_list, user_rank = _game_score_rank('high_score_xianyi', me)
    return render_template("xianyi.html", user=me, rank_list=rank_list, user_rank=user_rank)






def submit_xianyi_score():
    return D['_submit_score_common']('high_score_xianyi', game='xianyi')






def game_hanoi():
    me = D['current_user']()
    db = D['get_db']()
    rank_list = []
    for r in db.execute(_HANOI_RANK_SQL).fetchall():
        d = dict(r)
        d['time_str'] = _hanoi_fmt_ms(d['ms'])
        rank_list.append(d)
    lv = me['high_score_hanoi'] or 0
    ms = me['hanoi_best_ms'] or 0
    if lv > 0 and ms > 0:
        user_rank = db.execute(
            "SELECT COUNT(*) + 1 AS r FROM users WHERE high_score_hanoi > ? "
            "OR (high_score_hanoi = ? AND hanoi_best_ms > 0 AND hanoi_best_ms < ?)",
            (lv, lv, ms)).fetchone()['r']
    else:
        user_rank = db.execute(
            "SELECT COUNT(*) + 1 AS r FROM users WHERE high_score_hanoi > ?", (lv,)).fetchone()['r']
    me_dict = dict(me)
    me_dict['time_str'] = _hanoi_fmt_ms(ms)
    return render_template("hanoi.html", user=me_dict, rank_list=rank_list, user_rank=user_rank)






def submit_hanoi_score():
    """汉诺塔：按「最高通关阶数」记档，同阶比最快用时（从第一步起算）。"""
    me = D['current_user']()
    data = request.get_json() or {}
    try:
        level = int(data.get('level') or 0)
        moves = int(data.get('moves') or 0)
        ms = int(data.get('ms') or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数错误"}), 400
    if level < 2 or level > 9 or moves <= 0 or ms <= 0:
        return jsonify({"ok": False, "msg": "无效成绩"}), 400
    # 反作弊：n 阶汉诺塔理论最少 2^n-1 步，且每步至少要点一下
    min_moves = (1 << level) - 1
    if moves < min_moves:
        return jsonify({"ok": False, "msg": "步数少于理论最少（%d 步），成绩无效" % min_moves}), 400
    if ms < moves * HANOI_MIN_MS_PER_MOVE:
        return jsonify({"ok": False,
                        "msg": "用时与步数不匹配（每步至少 %.0f 毫秒），成绩无效" % HANOI_MIN_MS_PER_MOVE}), 400
    if ms > 24 * 3600 * 1000:
        return jsonify({"ok": False, "msg": "用时异常"}), 400
    g = D['_score_gate']("hanoi", data, level)
    if g:
        return g
    db = D['get_db']()
    cur_lv = me['high_score_hanoi'] or 0
    cur_ms = me['hanoi_best_ms'] or 0
    cur_moves = me['hanoi_best_moves'] or 0
    new_best = False
    # 步数纪录口径 = **历史最优步数**（任意阶数）。旧逻辑只在破最高阶时更新，
    # 导致先玩 9 阶(511步)再玩 3 阶(7步)的用户 best_moves 停在 511 ——
    # 「三阶不倒」永远解锁不了（2026-10-22 用户反馈）。
    better_moves = moves > 0 and (cur_moves == 0 or moves < cur_moves)
    if level > cur_lv or (level == cur_lv and (cur_ms == 0 or ms < cur_ms)):
        db.execute("UPDATE users SET high_score_hanoi=?, hanoi_best_ms=?, hanoi_best_moves=? WHERE id=?",
                   (level, ms, moves, me['id']))
        db.commit()
        new_best = True
    elif better_moves:
        db.execute("UPDATE users SET hanoi_best_moves=? WHERE id=?", (moves, me['id']))
        db.commit()
    if new_best or better_moves:
        D['_ach_after_submit'](me['id'])   # 027：破纪录才跑成就扫描
    ranks = [{"username": r['username'], "level": r['level'], "ms": r['ms']}
             for r in db.execute(_HANOI_RANK_SQL).fetchall()]
    return jsonify({"ok": True, "new_best": new_best, "rank_list": ranks})






def game_klotski():
    me = D['current_user']()
    field = _klotski_field(4)
    db = D['get_db']()
    rank_list = db.execute(
        "SELECT username, %s AS best_time FROM users WHERE %s IS NOT NULL AND %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field, field)).fetchall()
    user_best = me[field]
    user_rank = '暂无记录'
    if user_best is not None:
        user_rank = db.execute(
            "SELECT COUNT(*) + 1 AS r FROM users WHERE %s IS NOT NULL AND %s > 0 AND %s < ?" % (field, field, field),
            (user_best,)).fetchone()['r']
    return render_template("klotski.html", user=me, rank_list=rank_list, user_rank=user_rank)






def get_klotski_rank(size):
    field = _klotski_field(size)
    if not field:
        return jsonify({"ok": False, "msg": "无效尺寸"})
    db = D['get_db']()
    rank_list = db.execute(
        "SELECT username, %s AS best_time FROM users WHERE %s IS NOT NULL AND %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field, field)).fetchall()
    return jsonify({"ok": True, "rank_list":
                    [{"username": r['username'], "best_time": r['best_time']} for r in rank_list]})






def submit_klotski_time():
    me = D['current_user']()
    data = request.get_json() or {}
    field = _klotski_field(int(data.get('size', 4)))
    t = int(data.get('time', 0))
    if not field or t <= 0:
        return jsonify({"ok": False, "msg": "无效参数"}), 400
    g = D['_score_gate']("klotski", data, 1)
    if g:
        return g
    db = D['get_db']()
    new_best = False
    if me[field] is None or t < me[field]:
        db.execute("UPDATE users SET %s=? WHERE id=?" % field, (t, me['id']))
        db.commit()
        new_best = True
        D['_ach_after_submit'](me['id'])   # 027：破纪录才跑成就扫描
    rank_list = db.execute(
        "SELECT username, %s AS best_time FROM users WHERE %s IS NOT NULL AND %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field, field)).fetchall()
    return jsonify({"ok": True, "new_best": new_best,
                    "rank_list": [{"username": r['username'], "best_time": r['best_time']} for r in rank_list]})






def game_klotski_cn():
    me = D['current_user']()
    field = _klotski_cn_field(1)
    db = D['get_db']()
    rank_list = db.execute(
        "SELECT username, %s AS best_time FROM users WHERE %s IS NOT NULL AND %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field, field)).fetchall()
    return render_template("klotski_cn.html", user=me, rank_list=rank_list)






def get_klotski_cn_rank(layout_id):
    field = _klotski_cn_field(layout_id)
    if not field:
        return jsonify({"ok": False, "msg": "无效布局"})
    db = D['get_db']()
    rank_list = db.execute(
        "SELECT username, %s AS best_time FROM users WHERE %s IS NOT NULL AND %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field, field)).fetchall()
    return jsonify({"ok": True, "rank_list":
                    [{"username": r['username'], "best_time": r['best_time']} for r in rank_list]})






def submit_klotski_cn_time():
    me = D['current_user']()
    data = request.get_json() or {}
    field = _klotski_cn_field(int(data.get('layout_id', 1)))
    t = int(data.get('time', 0))
    if not field or t <= 0:
        return jsonify({"ok": False, "msg": "无效参数"}), 400
    g = D['_score_gate']("klotski_cn", data, 1)
    if g:
        return g
    db = D['get_db']()
    new_best = False
    if me[field] is None or t < me[field]:
        db.execute("UPDATE users SET %s=? WHERE id=?" % field, (t, me['id']))
        db.commit()
        new_best = True
        D['_ach_after_submit'](me['id'])   # 027：破纪录才跑成就扫描
    rank_list = db.execute(
        "SELECT username, %s AS best_time FROM users WHERE %s IS NOT NULL AND %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field, field)).fetchall()
    return jsonify({"ok": True, "new_best": new_best,
                    "rank_list": [{"username": r['username'], "best_time": r['best_time']} for r in rank_list]})






def game_mine():
    me = D['current_user']()
    return render_template("mine.html", user=me, modes=MINE_MODES)






def api_mine_rank(mode):
    field = _mine_field(mode)
    if not field:
        return jsonify({"ok": False, "msg": "无效模式"}), 400
    db = D['get_db']()
    rank_list = db.execute(
        "SELECT username, %s AS best_ms FROM users WHERE %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field)).fetchall()
    return jsonify({"ok": True, "rank_list":
                    [{"username": r['username'], "best_ms": r['best_ms']} for r in rank_list]})






def submit_mine_time():
    me = D['current_user']()
    data = request.get_json() or {}
    mode = str(data.get('mode') or '')
    field = _mine_field(mode)
    if not field:
        return jsonify({"ok": False, "msg": "无效模式"}), 400
    try:
        ms = int(data.get('ms') or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "无效参数"}), 400
    cfg = MINE_MODES[mode]
    floor_ms = cfg['cols'] * cfg['rows'] * 20
    if ms < floor_ms or ms > 86400000:
        return jsonify({"ok": False, "msg": "成绩不合理"}), 400
    g = D['_score_gate']("mine", data, 1)
    if g:
        return g
    db = D['get_db']()
    new_best = False
    if (me[field] or 0) <= 0 or ms < me[field]:
        db.execute("UPDATE users SET %s=? WHERE id=?" % field, (ms, me['id']))
        db.commit()
        new_best = True
        D['_ach_after_submit'](me['id'])   # 027：破纪录才跑成就扫描
    rank_list = db.execute(
        "SELECT username, %s AS best_ms FROM users WHERE %s > 0 ORDER BY %s ASC LIMIT 10"
        % (field, field, field)).fetchall()
    return jsonify({"ok": True, "new_best": new_best,
                    "rank_list": [{"username": r['username'], "best_ms": r['best_ms']} for r in rank_list]})



_GAME_SCORE_ROWS = [
    ("high_score_24", "24 点"), ("high_score_sudoku", "数独"),
    ("high_score_klotski", "华容道"), ("high_score_color_blind", "色盲测试"),
    ("high_score_jump", "跳一跳"), ("high_score_xianyi", "闲弈"), ("high_score_hanoi", "汉诺塔"),
]


_GAME_TIME_ROWS = [
    ("best_time_klotski_4", "华容道 4×4"), ("best_time_klotski_5", "华容道 5×5"),
    ("best_time_klotski_6", "华容道 6×6"), ("best_time_klotski_cn_1", "三国华容道 1"),
    ("best_time_klotski_cn_2", "三国华容道 2"), ("best_time_klotski_cn_3", "三国华容道 3"),
    ("best_time_klotski_cn_4", "三国华容道 4"),
    ("mine_best_ms_beginner", "扫雷 初级"), ("mine_best_ms_intermediate", "扫雷 中级"),
    ("mine_best_ms_expert", "扫雷 高级"), ("hanoi_best_ms", "汉诺塔 最快"),
]

# 每个时间字段的**存储单位**：华容道两族前端提交的是秒（seconds++ 累加），
# 扫雷/汉诺塔是毫秒。格式化必须按单位走，否则秒值被当毫秒除以 1000 → "0.04 秒" 这种鬼展示。


def _fmt_time(v, field):
    """按字段真实单位格式化（秒 / 毫秒两套口径）。"""
    if not v or v <= 0:
        return "--"
    if _GAME_TIME_UNIT.get(field) == "s":
        v = int(v)
        if v >= 3600:
            return "%d:%02d:%02d" % (v // 3600, (v % 3600) // 60, v % 60)
        return "%d:%02d" % (v // 60, v % 60)
    return D['_fmt_ms'](v)



_GAME_TIME_UNIT = {
    "best_time_klotski_4": "s", "best_time_klotski_5": "s", "best_time_klotski_6": "s",
    "best_time_klotski_cn_1": "s", "best_time_klotski_cn_2": "s",
    "best_time_klotski_cn_3": "s", "best_time_klotski_cn_4": "s",
    "mine_best_ms_beginner": "ms", "mine_best_ms_intermediate": "ms",
    "mine_best_ms_expert": "ms", "hanoi_best_ms": "ms",
}


# ══════════════════════════════════════════════════════════════════
# 075 · 统一成绩提交入口
# ══════════════════════════════════════════════════════════════════
def api_game_submit(game):
    """统一成绩提交入口（规划文档 075 项）。

    覆盖全部 11 款游戏：
    - 纯分数类（24/sudoku/jump/xianyi/colorblind/tetris）→ 直接转发
      `_submit_score_common`，走复算闸门 + 更新最高分 + 成就 + 埋点 + 排行榜
    - 有自定义逻辑的（2048 记最大方块 / 汉诺塔按阶数 / 华容道两族 / 扫雷三难度）
      → 按 `GameSpec.handler` 调各自的旧函数，行为与旧接口完全一致

    请求体与旧接口一致：{"score": N, "seq": {...}}，seq 交给 _score_gate 复算。
    """
    from . import protocol

    spec = protocol.get(game)
    if not spec:
        return jsonify({"ok": False, "msg": "未知游戏: %s" % game,
                        "known": list(protocol.all_keys())}), 404
    if spec.handler:
        fn = globals().get(spec.handler)
        if fn is None:
            return jsonify({"ok": False,
                            "msg": "配置错误：%s 的 handler %s 不存在"
                                   % (spec.name, spec.handler)}), 500
        return fn()
    return D['_submit_score_common'](spec.score_field, game=spec.key)
