# -*- coding: utf-8 -*-
"""xqm/core.py — 象棋大师（对局管理 / 匹配 / 赛季 / 钱包 / 抽签 / API）。

从 D['app'].py 搬迁。规则引擎是 xqmaster.py（import xqmaster as XQM）；
helper 一律走 deps.D 注入（不 import D['app']）。
"""
import json
import random
import sqlite3
import threading
import time

from flask import (flash, jsonify, redirect, render_template, request,
                   url_for)
from flask_socketio import emit, join_room

import xqmaster as XQM

import board
import chat
import games

from .deps import D

_XQM_MATCH_LOCK = threading.Lock()


_XQM_ACTION_LOCK = threading.Lock()


_XQM_SWEEP_STARTED = False


XQM_QUEUE_TTL = 25          # 超过这么久没轮询，认为玩家已经离开


# 赛季切分参数。⚠️ 这三个常量原来在 app.py 里（0328551 那次提交加的），
# 2026-10-23 拆包搬到 xqm/ 时**漏搬了**，导致 /api/xqm/season 一直 NameError 500。
XQM_SEASON_START = (2026, 1)   # 赛季起点（年, 月）
XQM_SEASON_MONTHS = 2          # 每季几个月
XQM_SEASON_RESET = 0           # 换季时积分是否清零（0 = 清）


XQM_TURN_TIMEOUT = 150      # 单方多久不出手判负（秒）


XQM_MATCH_DEADLINE = 30     # 超过这个等待时间就不再限制分段


def _xqm_season_at(ts=None):
    """按两个月一季切分（从 2026-01 起算），返回 (赛季号, 名称, 开始时间戳, 结束时间戳)。"""
    t = time.localtime(ts if ts is not None else time.time())
    idx = ((t.tm_year - XQM_SEASON_START[0]) * 12 + (t.tm_mon - XQM_SEASON_START[1])) // XQM_SEASON_MONTHS
    if idx < 0:
        idx = 0
    base = XQM_SEASON_START[0] * 12 + (XQM_SEASON_START[1] - 1) + idx * XQM_SEASON_MONTHS
    sy, sm = base // 12, base % 12 + 1
    ebase = base + XQM_SEASON_MONTHS
    ey, em = ebase // 12, ebase % 12 + 1
    start = time.mktime((sy, sm, 1, 0, 0, 0, 0, 0, -1))
    end = time.mktime((ey, em, 1, 0, 0, 0, 0, 0, -1))
    lbase = ebase - 1
    ly, lm = lbase // 12, lbase % 12 + 1
    name = ('%d.%02d–%02d 赛季' % (sy, sm, lm)) if ly == sy else ('%d.%02d–%d.%02d 赛季' % (sy, sm, ly, lm))
    return idx + 1, name, start, end


def _xqm_ensure_season(db):
    """当前赛季不存在就开一季：先把上一季归档进历史，再清空积分。"""
    sid, name, start, end = _xqm_season_at()
    row = db.execute("SELECT * FROM xqm_season WHERE id=?", (sid,)).fetchone()
    if row:
        return row
    prev = db.execute("SELECT * FROM xqm_season ORDER BY id DESC LIMIT 1").fetchone()
    if prev and not prev['closed']:
        now = time.time()
        for i, r in enumerate(db.execute("SELECT * FROM xqm_rating ORDER BY rating DESC").fetchall()):
            db.execute(
                "INSERT INTO xqm_season_hist (season_id, season_name, user_id, rating, wins, losses, draws, rank, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (prev['id'], prev['name'], r['user_id'], r['rating'],
                 r['wins'], r['losses'], r['draws'], i + 1, now))
        db.execute("UPDATE xqm_season SET closed=1 WHERE id=?", (prev['id'],))
        db.execute("UPDATE xqm_rating SET rating=?, wins=0, losses=0, draws=0, updated_at=?",
                   (XQM_SEASON_RESET, now))
    db.execute("INSERT INTO xqm_season (id, name, start_at, end_at, closed) VALUES (?,?,?,?,0)",
               (sid, name, start, end))
    db.commit()
    return db.execute("SELECT * FROM xqm_season WHERE id=?", (sid,)).fetchone()


def _xqm_rating(db, uid):
    row = db.execute("SELECT * FROM xqm_rating WHERE user_id=?", (uid,)).fetchone()
    if row:
        return row
    db.execute("INSERT OR IGNORE INTO xqm_rating (user_id, rating) VALUES (?,1000)", (uid,))
    db.commit()
    return db.execute("SELECT * FROM xqm_rating WHERE user_id=?", (uid,)).fetchone()


def _xqm_side_of(row, uid):
    if row['red_id'] == uid:
        return 'red'
    if row['black_id'] == uid:
        return 'black'
    return None


def _xqm_load(row):
    try:
        st = json.loads(row['state'] or '{}')
    except Exception:
        st = {}
    if not st.get('board'):
        st = XQM.new_state()
    return st


def _xqm_save(db, gid, st, status, winner=None, reason=''):
    db.execute("UPDATE xqm_games SET state=?, status=?, winner=?, reason=?, updated_at=? WHERE id=?",
               (json.dumps(st, ensure_ascii=False), status, winner, reason, time.time(), gid))
    db.commit()


def _xqm_append_log(db, gid, events):
    """把这一步的事件追加进对局历史，供复盘逐手回放。"""
    if not events:
        return
    row = db.execute("SELECT log FROM xqm_games WHERE id=?", (gid,)).fetchone()
    try:
        arr = json.loads(row['log']) if row and row['log'] else []
    except Exception:
        arr = []
    if not isinstance(arr, list):
        arr = []
    arr.extend(events)
    if len(arr) > 800:
        arr = arr[-800:]
    db.execute("UPDATE xqm_games SET log=? WHERE id=?",
               (json.dumps(arr, ensure_ascii=False), gid))


def _xqm_lost_pieces(db, st, row):
    """双方各自损失了哪些棋子：用初始阵容重建开局棋盘，再和当前棋盘对比。"""
    init = XQM.new_state(red_layout=_xqm_load_layout(db, row['red_id']),
                         black_layout=_xqm_load_layout(db, row['black_id']))
    order = ['king', 'advisor', 'elephant', 'horse', 'chariot', 'cannon', 'pawn']
    out = {}
    for side in ('red', 'black'):
        alive = {}
        for r in range(XQM.ROWS):
            for c in range(XQM.COLS):
                p = st['board'][r][c]
                if p and p['s'] == side:
                    alive[p['t']] = alive.get(p['t'], 0) + 1
        gone = []
        for r in range(XQM.ROWS):
            for c in range(XQM.COLS):
                p = init['board'][r][c]
                if p and p['s'] == side:
                    t = p['t']
                    if alive.get(t, 0) > 0:
                        alive[t] -= 1
                    else:
                        gone.append(t)
        out[side] = sorted(gone, key=lambda t: order.index(t) if t in order else 99)
    return out


def _xqm_state_payload(db, row, uid):
    """给客户端的一整包状态：棋局 + 双方信息 + 我该走哪边。"""
    st = _xqm_load(row)
    my = _xqm_side_of(row, uid)
    red_u = db.execute("SELECT id, username, avatar FROM users WHERE id=?", (row['red_id'],)).fetchone()
    blk_u = db.execute("SELECT id, username, avatar FROM users WHERE id=?", (row['black_id'],)).fetchone()
    rr = _xqm_rating(db, row['red_id'])
    br = _xqm_rating(db, row['black_id'])
    red_hp, red_mx = XQM.total_hp(st, 'red')
    blk_hp, blk_mx = XQM.total_hp(st, 'black')
    raw_loot = None
    if row['loot']:
        try:
            raw_loot = json.loads(row['loot'])
        except Exception:
            raw_loot = None
    if raw_loot and 'loot' in raw_loot:
        loot_out, yizhu_out = raw_loot.get('loot'), raw_loot.get('yizhu')
    else:
        loot_out, yizhu_out = raw_loot, None
    return {
        'gid': row['id'],
        'state': st,
        'my_side': my,
        'status': row['status'],
        'winner': row['winner'],
        'reason': row['reason'],
        'turn_left': max(0, int((row['last_move_at'] or 0) + XQM_TURN_TIMEOUT - time.time())),
        'turn_timeout': XQM_TURN_TIMEOUT,
        'loot': loot_out,
        'yizhu': yizhu_out,
        'yizhu_balance': _xqm_wallet(db, uid),
        'lost': _xqm_lost_pieces(db, st, row),
        'red': {'id': row['red_id'], 'name': red_u['username'] if red_u else '?',
                'rating': rr['rating'], 'hp': red_hp, 'max': red_mx,
                'alive': XQM.alive_count(st, 'red')},
        'black': {'id': row['black_id'], 'name': blk_u['username'] if blk_u else '?',
                  'rating': br['rating'], 'hp': blk_hp, 'max': blk_mx,
                  'alive': XQM.alive_count(st, 'black')},
    }


def _xqm_my_active_game(db, uid):
    return db.execute(
        "SELECT * FROM xqm_games WHERE status='playing' AND (red_id=? OR black_id=?) "
        "ORDER BY id DESC LIMIT 1", (uid, uid)).fetchone()


def _xqm_window(wait_sec):
    """分段窗口：每等 5 秒放宽 50 分，30 秒后不再限制。"""
    if wait_sec >= XQM_MATCH_DEADLINE:
        return 999999
    return 50 + int(wait_sec // 5) * 50


def _xqm_try_match(db, uid, rating, my_joined, now, fp='', ip=''):
    """给我找一个对手。返回对手的 user_id 或 None。

    整个「挑人 + 建房 + 双方出队」必须在锁里完成，
    否则两个请求可能同时挑中同一个人，各建一局。

    同设备（fp 相同）直接排除 —— 小号在同一台机器上开两个浏览器窗口
    就能跟自己下。同 IP 只排除「注册不满几天的新号」，
    免得宿舍里老同学互相匹配不了。
    """
    cands = db.execute(
        "SELECT q.user_id, q.rating, q.joined_at, q.fp, q.ip FROM xqm_queue q "
        "WHERE q.user_id != ? "
        "  AND NOT EXISTS (SELECT 1 FROM xqm_games g WHERE g.status='playing' "
        "                  AND (g.red_id = q.user_id OR g.black_id = q.user_id)) "
        "ORDER BY ABS(q.rating - ?) ASC", (uid, rating)).fetchall()
    for c in cands:
        if fp and c['fp'] and c['fp'] == fp:
            D['_log_mod'].event("xqm_self_block", uid=uid, other=c['user_id'], why='same_device')
            continue
        if ip and c['ip'] and c['ip'] == ip and D['_mm_is_new'](c['user_id'], now):
            D['_log_mod'].event("xqm_self_block", uid=uid, other=c['user_id'], why='same_ip_new')
            continue
        other_wait = now - (c['joined_at'] or now)
        wait = max(now - my_joined, other_wait)
        win = _xqm_window(wait)
        if abs((c['rating'] or 1000) - rating) <= win:
            return c['user_id'], (c['fp'] or ''), (c['ip'] or '')
    return None, '', ''


def _xqm_load_layout(db, uid):
    row = db.execute("SELECT layout FROM xqm_lineup WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return None
    try:
        lay = json.loads(row['layout'])
    except Exception:
        return None
    ok, _ = XQM.validate_lineup(lay)
    return lay if ok else None


def _xqm_levels(db, uid):
    rows = db.execute("SELECT type, level FROM xqm_shards WHERE user_id=?", (uid,)).fetchall()
    return {r['type']: r['level'] for r in rows if r['level'] and r['level'] > 1}


def _xqm_wallet_add(db, uid, amount):
    db.execute("INSERT INTO xqm_wallet (user_id, yizhu, updated_at) VALUES (?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET yizhu=MAX(0,yizhu+?), updated_at=?",
               (uid, amount, time.time(), amount, time.time()))


XQM_START_YIZHU = 200   # 新用户初始赠送


def _xqm_wallet(db, uid):
    row = db.execute("SELECT yizhu FROM xqm_wallet WHERE user_id=?", (uid,)).fetchone()
    if row:
        return row['yizhu']
    # 首次访问即开户，并送上初始弈铢（老用户已有记录，不受影响）
    db.execute("INSERT OR IGNORE INTO xqm_wallet (user_id, yizhu) VALUES (?,?)",
               (uid, XQM_START_YIZHU))
    db.commit()
    return XQM_START_YIZHU


def _xqm_award_shards(db, uid, layout, drops):
    """从阵容棋子类型里随机抽 drops 次，每次 30 碎片，同一类型可重复中。返回 {type: 数量}。"""
    types = sorted({it[0] for it in (layout or XQM.default_layout())})
    now = time.time()
    gain = {}
    for _ in range(drops):
        t = random.choice(types)
        gain[t] = gain.get(t, 0) + XQM.SHARDS_PER_DROP
    for t, n in gain.items():
        db.execute("INSERT INTO xqm_shards (user_id, type, shards, level, updated_at) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(user_id, type) DO UPDATE SET shards=shards+?, updated_at=?",
                   (uid, t, n, 0 if XQM.is_locked_type(t) else 1, now, n, now))
    return gain


def _xqm_create_game(db, a_id, b_id, a_fp='', a_ip='', b_fp='', b_ip=''):
    """建房：积分低的执红先手（减少先手优势带来的不公平），同分则 id 小的执红。"""
    _xqm_ensure_season(db)
    ra = _xqm_rating(db, a_id)['rating']
    rb = _xqm_rating(db, b_id)['rating']
    if ra < rb:
        red_id, black_id = a_id, b_id
    elif rb < ra:
        red_id, black_id = b_id, a_id
    else:
        red_id, black_id = (a_id, b_id) if a_id < b_id else (b_id, a_id)
    fp_of = {a_id: (a_fp or '')[:24], b_id: (b_fp or '')[:24]}
    ip_of = {a_id: (a_ip or '')[:64], b_id: (b_ip or '')[:64]}
    st = XQM.new_state(red_layout=_xqm_load_layout(db, red_id),
                       black_layout=_xqm_load_layout(db, black_id),
                       red_levels=_xqm_levels(db, red_id),
                       black_levels=_xqm_levels(db, black_id))
    now = time.time()
    init_log = json.dumps([{"k": "init", "board": st['board']}], ensure_ascii=False)
    cur = db.execute(
        "INSERT INTO xqm_games (red_id, black_id, state, status, created_at, updated_at, "
        "last_move_at, log, red_fp, black_fp, red_ip, black_ip) "
        "VALUES (?,?,?,'playing',?,?,?,?,?,?,?,?)",
        (red_id, black_id, json.dumps(st, ensure_ascii=False), now, now, now, init_log,
         fp_of.get(red_id, ''), fp_of.get(black_id, ''),
         ip_of.get(red_id, ''), ip_of.get(black_id, '')))
    gid = cur.lastrowid
    db.execute("DELETE FROM xqm_queue WHERE user_id IN (?,?)", (a_id, b_id))
    db.commit()
    return gid


def _xqm_is_selfplay(db, row):
    """这局象棋是不是自己配自己拉的。返回 (是否, 原因)。"""
    red_id, black_id = row['red_id'], row['black_id']
    if not red_id or not black_id:
        return True, 'missing_user'
    if int(red_id) == int(black_id):
        return True, 'same_user'
    rfp = (row['red_fp'] or '')[:24]
    bfp = (row['black_fp'] or '')[:24]
    if rfp and bfp and rfp == bfp:
        return True, 'same_device'
    rip = (row['red_ip'] or '')[:64]
    bip = (row['black_ip'] or '')[:64]
    if rip and bip and rip == bip:
        now = time.time()
        if D['_mm_is_new'](int(red_id), now) and D['_mm_is_new'](int(black_id), now):
            return True, 'same_ip_new'
    return False, ''


def _xqm_apply_result(db, row, st):
    """对局结束时结算 Elo 并写历史。幂等：已结算过就不再算。"""
    if row['status'] != 'playing':
        return
    selfplay, why = _xqm_is_selfplay(db, row)
    if selfplay:
        D['_log_mod'].event("xqm_selfplay_blocked", gid=row['id'],
                       red=row['red_id'], black=row['black_id'], why=why)
        db.execute("UPDATE xqm_games SET status='finished', winner=?, reason=?, updated_at=? WHERE id=?",
                   (st.get('winner') or '', 'selfplay', time.time(), row['id']))
        db.execute("DELETE FROM xqm_queue WHERE user_id IN (?,?)",
                   (row['red_id'], row['black_id']))
        db.commit()
        return
    winner = st.get('winner')
    red_id, black_id = row['red_id'], row['black_id']
    rr = _xqm_rating(db, red_id)
    br = _xqm_rating(db, black_id)
    if winner == 'red':
        dr = XQM.elo_delta(rr['rating'], br['rating'], 1)
        db_ = XQM.elo_delta(br['rating'], rr['rating'], 0)
        db.execute("UPDATE xqm_rating SET rating=MAX(0,rating+?), wins=wins+1, updated_at=? WHERE user_id=?",
                   (dr, time.time(), red_id))
        db.execute("UPDATE xqm_rating SET rating=MAX(0,rating+?), losses=losses+1, updated_at=? WHERE user_id=?",
                   (db_, time.time(), black_id))
    elif winner == 'black':
        dr = XQM.elo_delta(rr['rating'], br['rating'], 0)
        db_ = XQM.elo_delta(br['rating'], rr['rating'], 1)
        db.execute("UPDATE xqm_rating SET rating=MAX(0,rating+?), losses=losses+1, updated_at=? WHERE user_id=?",
                   (dr, time.time(), red_id))
        db.execute("UPDATE xqm_rating SET rating=MAX(0,rating+?), wins=wins+1, updated_at=? WHERE user_id=?",
                   (db_, time.time(), black_id))
    else:
        dr = XQM.elo_delta(rr['rating'], br['rating'], 0.5)
        db_ = XQM.elo_delta(br['rating'], rr['rating'], 0.5)
        db.execute("UPDATE xqm_rating SET rating=MAX(0,rating+?), draws=draws+1, updated_at=? WHERE user_id=?",
                   (dr, time.time(), red_id))
        db.execute("UPDATE xqm_rating SET rating=MAX(0,rating+?), draws=draws+1, updated_at=? WHERE user_id=?",
                   (db_, time.time(), black_id))
    db.execute("UPDATE xqm_games SET status='finished', winner=?, reason=?, updated_at=? WHERE id=?",
               (winner, st.get('reason') or '', time.time(), row['id']))
    loot = {}
    yizhu = {}
    for side, uid in (("red", red_id), ("black", black_id)):
        if winner == 'draw':
            drops = XQM.DRAW_DROPS
            coin = XQM.YIZHU["draw"]
        elif winner == side:
            drops = XQM.WIN_DROPS
            coin = XQM.YIZHU["win"]
        else:
            drops = XQM.LOSE_DROPS
            coin = XQM.YIZHU["lose"]
        try:
            loot[side] = _xqm_award_shards(db, uid, _xqm_load_layout(db, uid), drops)
        except Exception:
            loot[side] = {}
        try:
            _xqm_wallet_add(db, uid, coin)
            yizhu[side] = coin
        except Exception:
            yizhu[side] = 0
    db.execute("UPDATE xqm_games SET loot=? WHERE id=?",
               (json.dumps({"loot": loot, "yizhu": yizhu}, ensure_ascii=False), row['id']))
    db.commit()


def _xqm_broadcast(db, gid, events=None):
    """把最新状态推给对局房间里的双方。"""
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return
    for uid in (row['red_id'], row['black_id']):
        try:
            payload = _xqm_state_payload(db, row, uid)
            payload['events'] = events or []
            D['socketio'].emit('xqm_update', payload, room='xqm_user_%d' % uid)
        except Exception:
            pass


def _xqm_sweep_once():
    """清理过期队列 + 判超时负。跑在独立线程里，自己开连接。"""
    try:
        db = D['_tune_conn'](sqlite3.connect(D['DB_PATH']))
        db.row_factory = sqlite3.Row
    except Exception:
        return
    try:
        now = time.time()
        db.execute("DELETE FROM xqm_queue WHERE last_poll < ?", (now - XQM_QUEUE_TTL,))
        rows = db.execute("SELECT * FROM xqm_games WHERE status='playing'").fetchall()
        for row in rows:
            if now - (row['last_move_at'] or 0) < XQM_TURN_TIMEOUT:
                continue
            st = _xqm_load(row)
            loser = st.get('turn')
            if loser not in ('red', 'black'):
                continue
            XQM._finish(st, 'black' if loser == 'red' else 'red', '对方长时间未行动，判负')
            _xqm_save(db, row['id'], st, 'playing')
            _xqm_apply_result(db, row, st)
            _xqm_broadcast(db, row['id'])
        db.commit()
    except Exception:
        pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def _xqm_sweep_loop():
    while True:
        time.sleep(10)
        try:
            _xqm_sweep_once()
        except Exception:
            pass


def _xqm_ensure_sweeper():
    global _XQM_SWEEP_STARTED
    if _XQM_SWEEP_STARTED:
        return
    _XQM_SWEEP_STARTED = True
    threading.Thread(target=_xqm_sweep_loop, daemon=True).start()




@D['login_required']


def xqmaster_page():
    return render_template("xqmaster.html", user=D['current_user'](), active="games")




@D['login_required']


def xqmaster_match_page():
    return render_template("xqmaster_match.html", user=D['current_user'](), active="games")




@D['login_required']


def xqmaster_rank_page():
    return render_template("xqmaster_rank.html", user=D['current_user'](), active="games")




@D['login_required']


def xqmaster_codex_page():
    return render_template("xqmaster_codex.html", user=D['current_user'](), active="games")




@D['login_required']


def xqmaster_lineup_page():
    return render_template("xqmaster_lineup.html", user=D['current_user'](), active="games")




@D['login_required']


def xqmaster_settings_page():
    return render_template("xqmaster_settings.html", user=D['current_user'](), active="games")




@D['login_required']


def api_xqm_season():
    me = D['current_user']()
    db = D['get_db']()
    s = _xqm_ensure_season(db)
    now = time.time()
    total = max(1.0, s['end_at'] - s['start_at'])
    rows = db.execute(
        "SELECT r.user_id, r.rating, r.wins, r.losses, r.draws, u.username "
        "FROM xqm_rating r JOIN users u ON u.id = r.user_id "
        "WHERE r.wins + r.losses + r.draws > 0 "
        "ORDER BY r.rating DESC, r.wins DESC LIMIT 50").fetchall()
    lst = [{'rank': i + 1, 'name': r['username'], 'rating': r['rating'],
            'wins': r['wins'], 'losses': r['losses'], 'draws': r['draws'],
            'me': r['user_id'] == me['id']} for i, r in enumerate(rows)]
    hist = []
    for c in db.execute("SELECT season_name, rank, rating, user_id FROM xqm_season_hist "
                        "WHERE rank = 1 ORDER BY season_id DESC LIMIT 8").fetchall():
        u = db.execute("SELECT username FROM users WHERE id=?", (c['user_id'],)).fetchone()
        hist.append({'season': c['season_name'], 'name': u['username'] if u else '?',
                     'rating': c['rating']})
    return jsonify({
        'ok': True,
        'season': {
            'id': s['id'], 'name': s['name'],
            'days_left': max(0, int((s['end_at'] - now) // 86400)),
            'days_total': int(total // 86400),
            'progress': round(max(0.0, min(1.0, (now - s['start_at']) / total)) * 100, 1),
            'reset': XQM_SEASON_RESET,
        },
        'list': lst, 'hist': hist,
    })




@D['login_required']


def xqmaster_lottery_page():
    return render_template("xqmaster_lottery.html", user=D['current_user'](), active="games")




@D['login_required']


def api_xqm_replay(gid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    side = _xqm_side_of(row, me['id'])
    if side is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    try:
        evs = json.loads(row['log']) if row['log'] else []
    except Exception:
        evs = []
    if not isinstance(evs, list):
        evs = []
    red_u = db.execute("SELECT username FROM users WHERE id=?", (row['red_id'],)).fetchone()
    blk_u = db.execute("SELECT username FROM users WHERE id=?", (row['black_id'],)).fetchone()
    return jsonify({
        'ok': True, 'gid': gid, 'events': evs, 'my_side': side,
        'red': red_u['username'] if red_u else '?',
        'black': blk_u['username'] if blk_u else '?',
        'winner': row['winner'], 'reason': row['reason'], 'status': row['status'],
    })




@D['login_required']


def xqmaster_replay_page(gid):
    return render_template("xqmaster_replay.html", user=D['current_user'](), active="games", gid=gid)




@D['login_required']


def api_xqm_lineup():
    me = D['current_user']()
    db = D['get_db']()
    if request.method == "GET":
        row = db.execute("SELECT layout FROM xqm_lineup WHERE user_id=?", (me['id'],)).fetchone()
        my = None
        if row:
            try:
                my = json.loads(row['layout'])
            except Exception:
                my = None
        if my is not None:
            ok, _ = XQM.validate_lineup(my)
            if not ok:
                my = None
        return jsonify({'ok': True, 'layout': my, 'default': XQM.default_layout(),
                        'order': list(XQM.PIECE_COUNTS.keys()), 'counts': XQM.PIECE_COUNTS})
    data = request.get_json(silent=True) or {}
    lay = data.get('layout')
    ok, msg = XQM.validate_lineup(lay)
    if not ok:
        return jsonify({'ok': False, 'msg': msg}), 400
    db.execute("INSERT INTO xqm_lineup (user_id, layout, updated_at) VALUES (?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET layout=excluded.layout, updated_at=excluded.updated_at",
               (me['id'], json.dumps(lay, ensure_ascii=False), time.time()))
    db.commit()
    return jsonify({'ok': True})




@D['login_required']


def xqmaster_battle_page(gid):
    me = D['current_user']()
    row, side = _xqm_game_for(gid, me['id'])
    if not row:
        flash("对局不存在，或者这不是你的对局", "error")
        return redirect(url_for("xqmaster_page"))
    # 已经结束的局，直接送去结算页
    if row['status'] != 'playing':
        return redirect(url_for("xqmaster_result_page", gid=gid))
    return render_template("xqmaster_battle.html", user=me, active="games", gid=gid)




@D['login_required']


def xqmaster_result_page(gid):
    me = D['current_user']()
    row, side = _xqm_game_for(gid, me['id'])
    if not row:
        flash("对局不存在，或者这不是你的对局", "error")
        return redirect(url_for("xqmaster_page"))
    return render_template("xqmaster_result.html", user=me, active="games", gid=gid)




@D['login_required']


def api_xqm_me():
    me = D['current_user']()
    db = D['get_db']()
    r = _xqm_rating(db, me['id'])
    total = r['wins'] + r['losses'] + r['draws']
    return jsonify({
        'ok': True, 'rating': r['rating'], 'wins': r['wins'],
        'losses': r['losses'], 'draws': r['draws'], 'total': total,
        'rate': round(r['wins'] * 100.0 / total) if total else 0,
        'yizhu': _xqm_wallet(db, me['id']),
    })




@D['login_required']


def api_xqm_grow():
    me = D['current_user']()
    db = D['get_db']()
    rows = db.execute("SELECT type, shards, level FROM xqm_shards WHERE user_id=?",
                      (me['id'],)).fetchall()
    have = {r['type']: r for r in rows}
    pieces = []
    for t in XQM.PIECE_COUNTS:
        r = have.get(t)
        lv = r['level'] if r else (0 if XQM.is_locked_type(t) else 1)
        sh = r['shards'] if r else 0
        g = XQM.piece_growth(t, lv)
        nt = XQM.NEW_TRAITS[t]
        base = XQM.PIECES[t]
        pieces.append({
            'type': t, 'level': lv, 'shards': sh,
            'cost': XQM.upgrade_cost(lv), 'maxed': lv >= XQM.MAX_LEVEL,
            'hp': g['mh'], 'atk': g['atk'],
            'base_hp': base['hp'], 'base_atk': base['atk'],
            'trait_bonus': round(g['tr'] * 100),
            'lv20': g['g2'], 'nt_name': nt['name'], 'nt_desc': nt['desc'],
            'locked': lv <= 0, 'unlock_cost': XQM.UNLOCK_COST,
            'base': XQM.is_locked_type(t),
        })
    return jsonify({'ok': True, 'pieces': pieces, 'order': list(XQM.PIECE_COUNTS.keys()),
                    'grades': XQM.GRADES, 'max_level': XQM.MAX_LEVEL,
                    'lottery': XQM.LOTTERY,
                    'balance': _xqm_wallet(db, me['id']), 'cost': XQM.LOTTERY["cost"]})




@D['login_required']


def api_xqm_lottery():
    me = D['current_user']()
    db = D['get_db']()
    bal = _xqm_wallet(db, me['id'])
    cost = XQM.LOTTERY["cost"]
    try:
        times = int((request.get_json(silent=True) or {}).get('times') or 1)
    except (ValueError, TypeError):
        times = 1
    times = 10 if times >= 10 else 1
    need = cost * times
    if bal < need:
        return jsonify({'ok': False, 'msg': '弈铢不足，还差 %d 枚（当前 %d 枚）' % (need - bal, bal)}), 400
    now = time.time()
    results = []
    for _ in range(times):
        grade, ptype, count = XQM.roll_lottery(random)
        db.execute("INSERT INTO xqm_shards (user_id, type, shards, level, updated_at) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(user_id, type) DO UPDATE SET shards=shards+?, updated_at=?",
                   (me['id'], ptype, count, 0 if XQM.is_locked_type(ptype) else 1, now, count, now))
        results.append({'grade': grade, 'type': ptype, 'count': count,
                        'zh': XQM.PIECES[ptype]["zh"][0], 'name': XQM.PIECES[ptype]["zh"][0]})
    _xqm_wallet_add(db, me['id'], -need)
    db.commit()
    if times == 1:
        r = results[0]
        r.update({'ok': True, 'balance': bal - cost})
        return jsonify(r)
    # 十连：额外给出最好的一发（前端拿它做舞台展示）
    order = {'mythic': 0, 'legend': 1, 'epic': 2, 'rare': 3, 'common': 4}
    best = sorted(results, key=lambda x: order.get(x['grade'], 9))[0]
    return jsonify({'ok': True, 'times': times, 'results': results, 'best': best,
                    'balance': bal - need})




@D['login_required']


def api_xqm_grow_upgrade():
    me = D['current_user']()
    t = (request.get_json(silent=True) or {}).get('type')
    if t not in XQM.PIECES:
        return jsonify({'ok': False, 'msg': '没有这种棋子'}), 400
    db = D['get_db']()
    row = db.execute("SELECT shards, level FROM xqm_shards WHERE user_id=? AND type=?",
                     (me['id'], t)).fetchone()
    lv = row['level'] if row else (0 if XQM.is_locked_type(t) else 1)
    sh = row['shards'] if row else 0
    if lv <= 0:
        if sh < XQM.UNLOCK_COST:
            return jsonify({'ok': False,
                            'msg': '还没解锁，需要 %d 个碎片（当前 %d）' % (XQM.UNLOCK_COST, sh)}), 400
        db.execute("INSERT INTO xqm_shards (user_id, type, shards, level, updated_at) VALUES (?,?,?,1,?) "
                   "ON CONFLICT(user_id, type) DO UPDATE SET shards=shards-?, level=1, updated_at=?",
                   (me['id'], t, sh - XQM.UNLOCK_COST, time.time(), XQM.UNLOCK_COST, time.time()))
        db.commit()
        g = XQM.piece_growth(t, 1)
        nt = XQM.NEW_TRAITS[t]
        return jsonify({'ok': True, 'unlocked': True,
                        'msg': '%s 已解锁' % XQM.PIECES[t]['zh'][0],
                        'level': 1, 'shards': sh - XQM.UNLOCK_COST,
                        'hp': g['mh'], 'atk': g['atk'], 'cost': XQM.upgrade_cost(1),
                        'maxed': False, 'trait_bonus': round(g['tr'] * 100),
                        'lv20': g['g2'], 'nt_name': nt['name'], 'nt_desc': nt['desc']})
    if lv >= XQM.MAX_LEVEL:
        return jsonify({'ok': False, 'msg': '已经练到顶了'}), 400
    cost = XQM.upgrade_cost(lv)
    if sh < cost:
        return jsonify({'ok': False, 'msg': '碎片不够，还差 %d 个' % (cost - sh)}), 400
    new_lv = lv + 1
    db.execute("INSERT INTO xqm_shards (user_id, type, shards, level, updated_at) VALUES (?,?,?,1,?) "
               "ON CONFLICT(user_id, type) DO UPDATE SET shards=shards-?, level=?, updated_at=?",
               (me['id'], t, sh - cost, time.time(), cost, new_lv, time.time()))
    db.commit()
    g = XQM.piece_growth(t, new_lv)
    nt = XQM.NEW_TRAITS[t]
    next_cost = XQM.upgrade_cost(new_lv) if new_lv < XQM.MAX_LEVEL else None
    return jsonify({'ok': True, 'type': t, 'level': new_lv, 'shards': sh - cost, 'cost': cost,
                    'next_cost': next_cost,
                    'hp': g['mh'], 'atk': g['atk'], 'trait_bonus': round(g['tr'] * 100),
                    'lv20': g['g2'], 'nt_name': nt['name'],
                    'just20': new_lv == 20})




@D['login_required']


def api_xqm_queue():
    """POST 入队 / GET 轮询。轮询里顺带尝试匹配，不用额外开线程。"""
    me = D['current_user']()
    db = D['get_db']()
    uid = me['id']
    now = time.time()
    _xqm_ensure_sweeper()

    # 已经在打？直接把他送回那局，别让他再排一个
    row = _xqm_my_active_game(db, uid)
    if row:
        return jsonify({'ok': True, 'status': 'matched', 'gid': row['id'], 'rejoin': True})

    if request.method == "POST":
        rating = _xqm_rating(db, uid)['rating']
        my_fp = D['_mm_fp']()
        my_ip = D['_mm_ip']()
        ex = db.execute("SELECT * FROM xqm_queue WHERE user_id=?", (uid,)).fetchone()
        if ex:
            # 重复入队不重置等待时间（否则窗口永远长不大）
            db.execute("UPDATE xqm_queue SET last_poll=?, fp=?, ip=? WHERE user_id=?",
                       (now, my_fp, my_ip, uid))
            db.commit()
            joined = ex['joined_at']
        else:
            db.execute("INSERT INTO xqm_queue (user_id, rating, joined_at, last_poll, fp, ip)"
                       " VALUES (?,?,?,?,?,?)",
                       (uid, rating, now, now, my_fp, my_ip))
            db.commit()
            joined = now
    else:
        q = db.execute("SELECT * FROM xqm_queue WHERE user_id=?", (uid,)).fetchone()
        if not q:
            return jsonify({'ok': True, 'status': 'idle'})
        db.execute("UPDATE xqm_queue SET last_poll=?, fp=?, ip=? WHERE user_id=?",
                   (now, q['fp'] or D['_mm_fp'](), q['ip'] or D['_mm_ip'](), uid))
        db.commit()
        joined = q['joined_at']
        rating = q['rating'] or 1000

    with _XQM_MATCH_LOCK:
        # 锁里重新确认一次：可能刚刚被别的请求匹配走了
        row = _xqm_my_active_game(db, uid)
        if row:
            return jsonify({'ok': True, 'status': 'matched', 'gid': row['id'], 'rejoin': True})
        my_fp, my_ip = D['_mm_fp'](), D['_mm_ip']()
        opp, opp_fp, opp_ip = _xqm_try_match(db, uid, rating, joined, now, my_fp, my_ip)
        if opp:
            gid = _xqm_create_game(db, uid, opp, my_fp, my_ip, opp_fp, opp_ip)
            D['_activity']("开始对局", "中国象棋大师 #%d" % gid)
            try:
                _xqm_broadcast(db, gid)
            except Exception:
                pass
            return jsonify({'ok': True, 'status': 'matched', 'gid': gid})

    wait = now - joined
    return jsonify({'ok': True, 'status': 'waiting', 'wait': int(wait),
                    'window': _xqm_window(wait)})




@D['login_required']


def api_xqm_queue_leave():
    """退队。注意：如果已经被匹配进对局了，这里不会让你逃掉。"""
    me = D['current_user']()
    db = D['get_db']()
    row = _xqm_my_active_game(db, me['id'])
    db.execute("DELETE FROM xqm_queue WHERE user_id=?", (me['id'],))
    db.commit()
    if row:
        return jsonify({'ok': True, 'status': 'matched', 'gid': row['id'], 'rejoin': True})
    return jsonify({'ok': True, 'status': 'idle'})




@D['login_required']


def api_xqm_current():
    """刷新页面后回来接着打。"""
    me = D['current_user']()
    db = D['get_db']()
    row = _xqm_my_active_game(db, me['id'])
    if not row:
        return jsonify({'ok': True, 'gid': None})
    return jsonify({'ok': True, 'gid': row['id']})




@D['login_required']


def api_xqm_game(gid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    if _xqm_side_of(row, me['id']) is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    return jsonify(dict(_xqm_state_payload(db, row, me['id']), ok=True))




@D['login_required']


def api_xqm_move(gid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    side = _xqm_side_of(row, me['id'])
    if side is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    if row['status'] != 'playing':
        return jsonify({'ok': False, 'msg': '对局已结束'}), 400

    data = request.get_json(silent=True) or {}
    try:
        fr, fc, tr, tc = (int(data.get(k)) for k in ('fr', 'fc', 'tr', 'tc'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': '坐标不合法'}), 400
    if not all(0 <= v < 10 for v in (fr, tr)) or not all(0 <= v < 9 for v in (fc, tc)):
        return jsonify({'ok': False, 'msg': '坐标越界'}), 400

    # 防连点：同一份操作 2 秒内只放行一次
    if not D['repeat_guard']("xqm_move", [gid, fr, fc, tr, tc], window=2.0, uid=me['id']):
        return jsonify({'ok': False, 'msg': '操作太快了'}), 400

    with _XQM_ACTION_LOCK:
        st = _xqm_load(row)
        ok, msg, events = XQM.apply_action(st, side, fr, fc, tr, tc)
        if not ok:
            D['repeat_release']("xqm_move", [gid, fr, fc, tr, tc], uid=me['id'])
            return jsonify({'ok': False, 'msg': msg}), 400

        now = time.time()
        db.execute("UPDATE xqm_games SET state=?, last_move_at=?, updated_at=? WHERE id=?",
                   (json.dumps(st, ensure_ascii=False), now, now, gid))
        _xqm_append_log(db, gid, events)
        db.commit()

        if st.get('over'):
            _xqm_apply_result(db, row, st)
            D['_activity']("对局结束", "中国象棋大师 #%d %s" % (gid, st.get('winner')))

        _xqm_broadcast(db, gid, events)
    return jsonify({'ok': True, 'events': events, 'state': st})




@D['login_required']


def api_xqm_pass(gid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    side = _xqm_side_of(row, me['id'])
    if side is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    if row['status'] != 'playing':
        return jsonify({'ok': False, 'msg': '对局已结束'}), 400

    if not D['repeat_guard']("xqm_pass", [gid], window=2.0, uid=me['id']):
        return jsonify({'ok': False, 'msg': '操作太快了'}), 400

    with _XQM_ACTION_LOCK:
        st = _xqm_load(row)
        ok, msg, events = XQM.end_turn(st, side)
        if not ok:
            D['repeat_release']("xqm_pass", [gid], uid=me['id'])
            return jsonify({'ok': False, 'msg': msg}), 400

        now = time.time()
        db.execute("UPDATE xqm_games SET state=?, last_move_at=?, updated_at=? WHERE id=?",
                   (json.dumps(st, ensure_ascii=False), now, now, gid))
        _xqm_append_log(db, gid, events)
        db.commit()

        if st.get('over'):
            _xqm_apply_result(db, row, st)

        _xqm_broadcast(db, gid, events)
    return jsonify({'ok': True, 'events': events, 'state': st})




@D['login_required']


def api_xqm_draw(gid):
    """发起或撤回求和提议。提议存 state JSON 的 draw_offer 字段（发起方 uid）。"""
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    side = _xqm_side_of(row, me['id'])
    if side is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    if row['status'] != 'playing':
        return jsonify({'ok': False, 'msg': '对局已结束'}), 400

    if not D['repeat_guard']("xqm_draw", [gid], window=0.8, uid=me["id"]):
        return jsonify({'ok': False, 'msg': '操作太快了'}), 400

    with _XQM_ACTION_LOCK:
        st = _xqm_load(row)
        if st.get('over'):
            return jsonify({'ok': False, 'msg': '对局已结束'}), 400
        offer = st.get('draw_offer')
        if offer == me['id']:
            st.pop('draw_offer', None)
            action = 'withdraw'
        elif offer:
            return jsonify({'ok': False, 'msg': '对方已发起求和，请先回应'}), 400
        else:
            st['draw_offer'] = me['id']
            action = 'offer'
        now = time.time()
        db.execute("UPDATE xqm_games SET state=?, updated_at=? WHERE id=?",
                   (json.dumps(st, ensure_ascii=False), now, gid))
        db.commit()
        _xqm_broadcast(db, gid)
    return jsonify({'ok': True, 'action': action, 'state': st})




@D['login_required']


def api_xqm_draw_accept(gid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    side = _xqm_side_of(row, me['id'])
    if side is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    if row['status'] != 'playing':
        return jsonify({'ok': False, 'msg': '对局已结束'}), 400

    if not D['repeat_guard']("xqm_draw_acc", [gid], window=0.8, uid=me["id"]):
        return jsonify({'ok': False, 'msg': '操作太快了'}), 400

    with _XQM_ACTION_LOCK:
        st = _xqm_load(row)
        offer = st.get('draw_offer')
        if not offer or offer == me['id']:
            return jsonify({'ok': False, 'msg': '没有待回应的求和'}), 400
        st.pop('draw_offer', None)
        ok, msg, events = XQM.agree_draw(st, "双方同意和棋")
        if not ok:
            return jsonify({'ok': False, 'msg': msg}), 400
        now = time.time()
        db.execute("UPDATE xqm_games SET state=?, updated_at=? WHERE id=?",
                   (json.dumps(st, ensure_ascii=False), now, gid))
        _xqm_append_log(db, gid, events)
        db.commit()
        _xqm_apply_result(db, row, st)
        _xqm_broadcast(db, gid, events)
    return jsonify({'ok': True, 'state': st})




@D['login_required']


def api_xqm_draw_decline(gid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    side = _xqm_side_of(row, me['id'])
    if side is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    if row['status'] != 'playing':
        return jsonify({'ok': False, 'msg': '对局已结束'}), 400

    if not D['repeat_guard']("xqm_draw_dec", [gid], window=0.8, uid=me["id"]):
        return jsonify({'ok': False, 'msg': '操作太快了'}), 400

    with _XQM_ACTION_LOCK:
        st = _xqm_load(row)
        offer = st.get('draw_offer')
        if not offer or offer == me['id']:
            return jsonify({'ok': False, 'msg': '没有待回应的求和'}), 400
        st.pop('draw_offer', None)
        now = time.time()
        db.execute("UPDATE xqm_games SET state=?, updated_at=? WHERE id=?",
                   (json.dumps(st, ensure_ascii=False), now, gid))
        db.commit()
        _xqm_broadcast(db, gid)
    return jsonify({'ok': True, 'state': st})




@D['login_required']


def api_xqm_resign(gid):
    me = D['current_user']()
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '对局不存在'}), 404
    side = _xqm_side_of(row, me['id'])
    if side is None:
        return jsonify({'ok': False, 'msg': '这不是你的对局'}), 403
    if row['status'] != 'playing':
        return jsonify({'ok': False, 'msg': '对局已结束'}), 400
    with _XQM_ACTION_LOCK:
        st = _xqm_load(row)
        XQM.resign(st, side)
        _xqm_save(db, gid, st, 'playing')
        _xqm_apply_result(db, row, st)
        _xqm_broadcast(db, gid)
    return jsonify({'ok': True})




@D['login_required']


def api_xqm_rank():
    """段位榜：前 30 名。"""
    db = D['get_db']()
    rows = db.execute(
        "SELECT r.user_id, r.rating, r.wins, r.losses, r.draws, u.username "
        "FROM xqm_rating r JOIN users u ON u.id = r.user_id "
        "WHERE r.wins + r.losses + r.draws > 0 "
        "ORDER BY r.rating DESC, r.wins DESC LIMIT 30").fetchall()
    me = D['current_user']()
    mine = _xqm_rating(db, me['id'])
    out = []
    for i, r in enumerate(rows):
        out.append({'rank': i + 1, 'name': r['username'], 'rating': r['rating'],
                    'wins': r['wins'], 'losses': r['losses'], 'draws': r['draws'],
                    'me': r['user_id'] == me['id']})
    return jsonify({'ok': True, 'list': out, 'my_rating': mine['rating']})




def xqm_socket_join(data):
    user = D['current_user']()
    if not user:
        return
    try:
        gid = int((data or {}).get('gid'))
    except (TypeError, ValueError):
        return
    db = D['get_db']()
    row = db.execute("SELECT * FROM xqm_games WHERE id=?", (gid,)).fetchone()
    if not row or _xqm_side_of(row, user['id']) is None:
        return
    join_room('xqm_user_%d' % user['id'], request.sid)
    emit('xqm_ready', _xqm_state_payload(db, row, user['id']))
