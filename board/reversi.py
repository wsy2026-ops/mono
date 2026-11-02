# -*- coding: utf-8 -*-
"""board/reversi.py — 翻转棋（Egaroucid 引擎 + 房间对战）。

从 app.py 搬迁。helper 一律走 deps.D 注入（不 import app）。
"""
import os
import time
import queue as _queue_mod
import subprocess as _subprocess_mod
import threading as _threading_mod

from flask import abort, jsonify, render_template, request

from .deps import D
from blocking import run_blocking
from games.mini import _board_wins_leaderboard

def _scan_engine_dir(d):
    """在目录里挑一个可执行的引擎二进制（*console* 优先，其次 .exe）。"""
    try:
        files = sorted(os.listdir(d))
    except Exception:
        return None
    first_exe = None
    for f in files:
        low = f.lower()
        p = os.path.join(d, f)
        if not os.path.isfile(p):
            continue
        if low.endswith(('.dll', '.c', '.cc', '.cpp', '.h', '.hpp', '.o', '.a',
                         '.txt', '.md', '.json', '.toml', '.yml', '.py')):
            continue
        if not os.access(p, os.X_OK):
            continue
        if 'console' in low:
            return p
        if low.endswith('.exe') and first_exe is None:
            first_exe = p
    return first_exe


def _egaroucid_detect():
    """找黑白棋引擎二进制。

    ⚠️ 2026-10-26 修：原来只在 `board/egaroucid/` 下找，但线上引擎实际放在
    `/opt/mono/app/egaroucid/`（里面是 edax-console）——路径差一级，一直返回 None，
    黑白棋全程降级走内置弱 AI，用户反馈「疑似没有 AI」。
    现在按顺序找：board/egaroucid → app_root/egaroucid。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, 'egaroucid'),                  # board/egaroucid
        os.path.join(os.path.dirname(here), 'egaroucid'),  # 项目根/egaroucid
    ]
    for d in candidates:
        exe = _scan_engine_dir(d)
        if exe:
            return exe
    return None


_EGAROUCID_EXE = _egaroucid_detect()


_EGAROUCID_AVAILABLE = False


_EGAROUCID_ENGINES = {}


# ⚠️ 2026-10-26：原来是 set，失败一次该房间就永久走内置弱 AI（重开也没用）。
# 改成「rid -> 失败时间戳 + 冷却」，和象棋 _PIKAFISH_FAILED 一个路子。
_EGAROUCID_FAILED = {}
_EGAROUCID_FAIL_COOLDOWN = 180   # 秒；失败后这么久再给一次机会


_EGAROUCID_LOCK = _threading_mod.Lock()


_EGAROUCID_MAX = 2 if D['_LOW_MEM'] else 4


def _egaroucid_version_ok(exe):
    """真实启动握手验证（GTP name 命令）。"""
    try:
        p = _subprocess_mod.Popen([exe, '-gtp'], stdin=_subprocess_mod.PIPE,
                                  stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.DEVNULL,
                                  cwd=os.path.dirname(exe))
        p.stdin.write(b'name\n')
        p.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < 30:
            line = p.stdout.readline()
            if not line:
                break
            if line.strip().startswith(b'='):
                try:
                    p.stdin.write(b'quit\n'); p.stdin.flush()
                except Exception:
                    pass
                D['_proc_stop'](p, grace=3)
                return True
        D['_proc_stop'](p, grace=3)
    except Exception:
        return False
    return False


if _EGAROUCID_EXE:
    try:
        _EGAROUCID_AVAILABLE = _egaroucid_version_ok(_EGAROUCID_EXE)
    except Exception:
        _EGAROUCID_AVAILABLE = False


class _EgaroucidEngine(object):
    """黑白棋 GTP 引擎封装。每手都从标准开局重放历史，天然无状态、重启免疫。"""

    def __init__(self, exe, level=0, movetime=0):
        self.exe = exe
        self.level = int(level or 0)        # Edax 搜索深度（0 = 引擎默认）
        self.movetime = int(movetime or 0)  # 每手限时毫秒（0 = 不限）
        self.proc = None
        self.q = None

    def start(self):
        # ⚠️ 2026-10-26：Edax 的强度靠**启动参数**控制，GTP 里没有 level 命令：
        #   -l <n>          限制搜索深度
        #   -move-time <ms> 限制每手思考时间
        # 之前只传 -gtp，引擎用默认（很浅），黑白棋 AI 一直很弱。
        args = [self.exe, '-gtp']
        if self.level > 0:
            args += ['-l', str(self.level)]
        if self.movetime > 0:
            args += ['-move-time', str(self.movetime)]
        self.proc = _subprocess_mod.Popen(args, stdin=_subprocess_mod.PIPE,
                                          stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.DEVNULL,
                                          cwd=os.path.dirname(self.exe))
        self.q = _queue_mod.Queue()
        t = _threading_mod.Thread(target=self._read, daemon=True)
        t.start()
        if not self._cmd('name', timeout=45):
            raise RuntimeError('egaroucid handshake timeout')

    def _read(self):
        try:
            for raw in iter(self.proc.stdout.readline, b''):
                self.q.put(raw.decode('utf-8', errors='replace').rstrip('\r\n'))
        except Exception:
            pass
        # ⚠️ 2026-10-17：子进程退出后如果没人 wait()，会变成僵尸 <defunct> 累积，
        #    长时间运行会耗尽 PID。这里兜底回收一次（close() 里已有 wait，
        #    但进程自己崩掉 / 异常路径不一定走到 close）。
        try:
            if self.proc is not None:
                self.proc.wait(timeout=1)
        except Exception:
            pass
        try:
            self.q.put(None)
        except Exception:
            pass

    def _cmd(self, line, timeout=90):
        """发一条 GTP 命令，返回响应行列表（不含空行）。"""
        try:
            while True:
                self.q.get_nowait()
        except _queue_mod.Empty:
            pass
        self.proc.stdin.write((line + '\n').encode())
        self.proc.stdin.flush()
        out = []
        end = time.time() + timeout
        while time.time() < end:
            try:
                s = self.q.get(timeout=0.25)
            except _queue_mod.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError('engine closed')
                continue
            if s is None:
                raise RuntimeError('engine closed')
            if s == '':
                if out:
                    break
                continue
            out.append(s)
            if s.startswith('=') or s.startswith('?'):
                break
        return out

    def newgame(self):
        self._cmd('boardsize 8', timeout=20)
        self._cmd('clear_board', timeout=20)

    def play(self, color, vertex):
        """vertex 形如 'd3'；非法时返回 False（引擎回 '?'）。"""
        out = self._cmd('play %s %s' % (color, vertex), timeout=20)
        return bool(out) and out[0].startswith('=')

    def genmove(self, color, timeout=60, depth=None):
        """求一手。

        ⚠️ 2026-10-26：Edax 4.6 的 GTP 支持 `genmove <color> depth <n>` 扩展
        （`list_commands` 没列出来，但实测有效）。原来只发 `genmove <color>`，
        引擎用自己的默认深度——很低，所以黑白棋 AI 一直很弱。
        """
        cmd = 'genmove %s' % color
        if depth:
            cmd += ' depth %d' % int(depth)
        out = self._cmd(cmd, timeout=timeout)
        for s in out:
            if s.startswith('='):
                v = s[1:].strip()
                return v if v else None
        return None

    def close(self):
        try:
            if self.proc is not None:
                try:
                    self.proc.stdin.write(b'quit\n')
                    self.proc.stdin.flush()
                except Exception:
                    pass
                D['_proc_stop'](self.proc)
        except Exception:
            pass
        self.proc = None


# AI 等级（1~20）→ Edax 的 `-l` 搜索深度。
# 2026-10-26 实测（开局局面，单线程）：
#   -l 2 → 0ms | 8 → 4ms | 14 → 72ms | 20 → 543ms | 24 → 1151ms | 28 → 7.6s | 30 → 10.6s
# 中后期空格少会快很多，所以 20 级给 24 层（开局约 1.1 秒）是安全上限。
# ⚠️ `-move-time` 实测不生效（50ms/2500ms 都跑 ~500ms），所以不用它，只靠深度。
_RV_LEVEL_TABLE = {
    1: 2, 2: 4, 3: 5, 4: 6, 5: 8,
    6: 9, 7: 10, 8: 12, 9: 14, 10: 16,
    11: 17, 12: 18, 13: 19, 14: 20, 15: 21,
    16: 22, 17: 22, 18: 23, 19: 24, 20: 24,
}


def _rv_strength(lv):
    """等级 → (depth, movetime_ms)。老房间（等级 0）按 20 级给满强度。"""
    try:
        lv = int(lv or 0)
    except (TypeError, ValueError):
        lv = 0
    if lv <= 0:
        lv = 20
    lv = max(1, min(20, lv))
    return _RV_LEVEL_TABLE[lv], 0


def _egaroucid_get(rid):
    if not _EGAROUCID_AVAILABLE:
        return None
    with _EGAROUCID_LOCK:
        e = _EGAROUCID_ENGINES.get(rid)
        if e is not None:
            return e
        _bad = _EGAROUCID_FAILED.get(rid)
        if _bad is not None:
            if time.time() - _bad < _EGAROUCID_FAIL_COOLDOWN:
                return None
            _EGAROUCID_FAILED.pop(rid, None)
        if len(_EGAROUCID_ENGINES) >= _EGAROUCID_MAX:
            return None
        # 按房间的 AI 等级决定 Edax 的深度上限和每手限时
        _lv = 0
        try:
            _lvf = D.get('_ai_level_of_room')
            _lv = _lvf('reversi_rooms', rid) if _lvf else 0
        except Exception:
            _lv = 0
        _level, _mt = _rv_strength(_lv)
        e = _EgaroucidEngine(_EGAROUCID_EXE, level=_level, movetime=_mt)
        try:
            e.start()
        except Exception:
            _EGAROUCID_FAILED[rid] = time.time()
            return None
        _EGAROUCID_ENGINES[rid] = e
        return e


def _rv_vertex(x, y):
    """棋盘坐标 (x,y)（0 起，y=0 为底行）→ GTP 顶点，如 (3,2) -> 'd3'。"""
    return chr(ord('a') + x) + str(y + 1)


def _rv_from_vertex(v):
    """GTP 顶点 → (x,y)；PASS/异常返回 None。"""
    try:
        v = v.strip().lower()
        if len(v) < 2:
            return None
        x = ord(v[0]) - ord('a')
        y = int(v[1:]) - 1
        if 0 <= x < RV_N and 0 <= y < RV_N:
            return (x, y)
    except Exception:
        pass
    return None


def _egaroucid_move(b, rid, hist):
    """用引擎求白方（AI）一手。hist 为本局行棋顶点列表（含黑白）。

    每次从标准开局重放全部历史再 genmove，引擎无状态；重放失败时返回 None 走内置 AI。
    """
    eng = _egaroucid_get(rid)
    if eng is None:
        return None
    try:
        eng.newgame()
        for col, v in hist:
            if not eng.play(col, v):
                return None
        _dep = D.get('_ai_depth_for')
        _depth = _dep('reversi', rid) if _dep else 20
        v = eng.genmove('w', timeout=max(20, _depth * 3), depth=_depth)
        if not v:
            return None
        mv = _rv_from_vertex(v)
        if mv is None:
            return None
        if _rv_flips(b, mv[0], mv[1], RV_WHITE) is None:
            return None
        return mv
    except Exception as _e:
        try:
            print(" [Egaroucid] 调用失败，本手降级内置 AI：%s" % _e)
        except Exception:
            pass
        try:
            eng.close()
        except Exception:
            pass
        with _EGAROUCID_LOCK:
            _EGAROUCID_ENGINES.pop(rid, None)
        return None


_RV_HIST_SEP = ','.join


def _rv_hist_join(hist):
    """行棋历史 → 'b:d3,w:c5,...' 存库格式。"""
    return ','.join('%s:%s' % (c, v) for (c, v) in hist)


def _rv_hist_split(hist):
    """行棋记录 → [('b','d3'), ...]；坏数据直接丢弃（引擎重放失败会自动降级）。"""
    out = []
    try:
        for tok in (hist or '').split(','):
            tok = tok.strip()
            if not tok or ':' not in tok:
                continue
            c, v = tok.split(':', 1)
            if c in ('b', 'w') and v:
                out.append((c, v))
    except Exception:
        return []
    return out


def _rv_next_side(b, player):
    """player 刚落子后的局面走向：返回 (next_player_or_None, over)（不修改局面）。"""
    opp = RV_WHITE if player == RV_BLACK else RV_BLACK
    if '.' not in b:
        return None, True
    if _rv_legal_moves(b, opp):
        return opp, False
    if _rv_legal_moves(b, player):
        return player, False
    return None, True


def _rv_room_payload(room, skipped=False):
    b = _rv_parse(room['board_state'])
    bc, wc = _rv_counts(b)
    lm = None
    if room['last_move']:
        try:
            p = room['last_move'].split(',')
            lm = [int(p[0]), int(p[1])]
        except Exception:
            lm = None
    return {
        "board": b,
        "status": room['status'],
        "winner_id": room['winner_id'],
        "turn_user_id": room['turn_user_id'],
        "last_move": lm,
        "skipped": skipped,
        "black": bc,
        "white": wc,
    }


def _rv_result(winner, me_id):
    """把胜者 id 转成走子者视角的结果（win/lose/draw）。

    winner: 0=平局；-1=PvAI 的 AI 胜；其它为用户 id。
    """
    if winner == me_id:
        return 'win'
    if winner == 0:
        return 'draw'
    return 'lose'


def _rv_finish(rid, db, room, msg):
    """终局结算：按子数定胜负并计分。内部重新读取最新房间行。"""
    room = db.execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
    b = _rv_parse(room['board_state'])
    bc, wc = _rv_counts(b)
    if bc > wc:
        winner_id = room['owner_id']
    elif wc > bc:
        winner_id = room['guest_id'] if room['guest_id'] is not None else -1
    else:
        winner_id = 0
    db.execute(
        "UPDATE reversi_rooms SET status='finished', winner_id=?, updated_at=? WHERE id=?",
        (winner_id, time.time(), rid))
    db.commit()
    D['_award_game_stats']('reversi', rid)
    return {"winner": winner_id, "black": bc, "white": wc, "msg": msg}






def reversi_list():
    me = D['current_user']()
    db = D['get_db']()
    public = db.execute(
        "SELECT r.*, u.username owner_name FROM reversi_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.is_public=1 AND r.status='waiting' AND r.owner_id != ? ORDER BY r.id DESC LIMIT 20",
        (me['id'],)).fetchall()
    mine = db.execute(
        "SELECT r.*, u.username owner_name FROM reversi_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.owner_id=? OR r.guest_id=? ORDER BY r.id DESC LIMIT 20",
        (me['id'], me['id'])).fetchall()
    return render_template("reversi_list.html", user=me, public=public, mine=mine,
                           blb_rows=_board_wins_leaderboard('reversi'))






def reversi_join(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
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
    db.execute(
        "UPDATE reversi_rooms SET guest_id=?, status='playing', turn_user_id=?, updated_at=? WHERE id=?",
        (me['id'], room['owner_id'], time.time(), rid))
    db.commit()
    try:
        fresh = db.execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
        D['socketio'].emit('rv_update', dict({"ok": True}, **_rv_room_payload(fresh)),
                      room='rv_%d' % rid)
        D['socketio'].emit('rv_lobby_update', {}, room='rv_lobby')
    except Exception:
        pass
    return jsonify({"ok": True})






def reversi_random_join():
    me = D['current_user']()
    db = D['get_db']()
    rows = db.execute(
        "SELECT id FROM reversi_rooms WHERE is_public=1 AND status='waiting' AND mode=0 AND owner_id != ? "
        "AND guest_id IS NULL ORDER BY RANDOM() LIMIT 5",
        (me['id'],)).fetchall()
    for row in rows:
        r = db.execute(
            "UPDATE reversi_rooms SET guest_id=?, status='playing', turn_user_id=owner_id, updated_at=? "
            "WHERE id=? AND status='waiting' AND guest_id IS NULL",
            (me['id'], time.time(), row['id']))
        if r.rowcount:
            db.commit()
            return jsonify({"ok": True, "rid": row['id']})
    return jsonify({"ok": False, "msg": "暂无可加入的公开房间"})






def reversi_room(rid):
    me = D['current_user']()
    room = D['get_db']().execute(
        "SELECT r.*, ou.username owner_name, gu.username guest_name FROM reversi_rooms r "
        "LEFT JOIN users ou ON ou.id=r.owner_id "
        "LEFT JOIN users gu ON gu.id=r.guest_id WHERE r.id=?", (rid,)
    ).fetchone()
    if not room:
        abort(404)
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    is_spectator = not (is_owner or is_guest)   # 观战只读
    return render_template("reversi_room.html", user=me, room=room,
                           is_owner=is_owner, is_guest=is_guest, ai_builtin=not _EGAROUCID_AVAILABLE)






def reversi_move(rid):

    def _out(payload):
        try:
            D['socketio'].emit('rv_update', payload, room='rv_%d' % rid)
        except Exception:
            pass
        return jsonify(payload)
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return _out({"ok": False, "msg": "房间不存在"})
    if room['status'] != 'playing':
        return _out({"ok": False, "msg": "对局已结束"})
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    if not (is_owner or is_guest):
        return _out({"ok": False, "msg": "你不是本局玩家"})
    if request.form.get("resign") == "1":
        winner_id = room['guest_id'] if is_owner else room['owner_id']
        if room['mode'] == 1 and is_owner:
            winner_id = -1
        db.execute("UPDATE reversi_rooms SET status='finished', winner_id=?, updated_at=? WHERE id=?",
                   (winner_id, time.time(), rid))
        db.commit()
        D['_award_game_stats']('reversi', rid)
        return _out({"ok": True, "status": "finished", "winner_id": winner_id})
    if room['turn_user_id'] != me['id']:
        return _out({"ok": False, "msg": "还没轮到你"})
    if room['mode'] == 1 and not is_owner:
        return _out({"ok": False, "msg": "人机房间仅房主可操作"})
    player = RV_BLACK if is_owner else RV_WHITE
    try:
        x = int(request.form.get("x", "-1"))
        y = int(request.form.get("y", "-1"))
    except (TypeError, ValueError):
        return _out({"ok": False, "msg": "参数错误"})
    b = _rv_parse(room['board_state'])
    flips = _rv_flips(b, x, y, player)
    if flips is None:
        return _out({"ok": False, "msg": "该位置不能落子（必须夹住并翻转对方棋子）"})
    nb = _rv_apply(b, x, y, player)
    opp = RV_WHITE if player == RV_BLACK else RV_BLACK
    if room['mode'] == 1 and is_owner:
        hist = _rv_hist_split(room['move_hist'])
        hist.append(('b', _rv_vertex(x, y)))
        nb_cur, last2, ai_played = nb, (x, y), False
        cur = RV_BLACK
        for _ in range(40):
            nxt, over = _rv_next_side(nb_cur, cur)
            if over:
                db.execute("UPDATE reversi_rooms SET board_state=?, last_move=?, move_hist=?, updated_at=? WHERE id=?",
                           (nb_cur, "%d,%d" % last2, _rv_hist_join(hist), time.time(), rid))
                db.commit()
                fin = _rv_finish(rid, db, room, "棋终")
                return _out(dict({"ok": True, "over": True, "ai_played": ai_played,
                                     "result": _rv_result(fin["winner"], me['id'])},
                                    **_rv_room_payload({**room, "board_state": nb_cur, "status": "finished",
                                                        "winner_id": fin["winner"],
                                                        "last_move": "%d,%d" % last2}),
                                    msg="棋终，" + fin["msg"]))
            if nxt == RV_BLACK:
                db.execute("UPDATE reversi_rooms SET board_state=?, turn_user_id=?, last_move=?, move_hist=?, updated_at=? "
                           "WHERE id=?",
                           (nb_cur, me['id'], "%d,%d" % last2, _rv_hist_join(hist), time.time(), rid))
                db.commit()
                return _out(dict({"ok": True, "ai_played": ai_played, "skipped": not ai_played},
                                    **_rv_room_payload({**room, "board_state": nb_cur, "turn_user_id": me['id'],
                                                        "last_move": "%d,%d" % last2},
                                                        skipped=not ai_played),
                                    msg=("" if ai_played else "对方无子可下，跳过回合，请继续走棋")))
            ai = run_blocking(_egaroucid_move, nb_cur, rid, hist)   # 引擎最多 5 秒
            if ai is None:
                ai = _rv_ai_move(nb_cur, RV_WHITE)
            if ai is None:
                break
            hist.append(('w', _rv_vertex(ai[0], ai[1])))
            nb_cur = _rv_apply(nb_cur, ai[0], ai[1], RV_WHITE)
            last2, ai_played, cur = ai, True, RV_WHITE
        db.execute("UPDATE reversi_rooms SET board_state=?, turn_user_id=?, last_move=?, move_hist=?, updated_at=? WHERE id=?",
                   (nb_cur, me['id'], "%d,%d" % last2, _rv_hist_join(hist), time.time(), rid))
        db.commit()
        return _out(dict({"ok": True, "ai_played": ai_played, "skipped": False},
                            **_rv_room_payload({**room, "board_state": nb_cur, "turn_user_id": me['id'],
                                                "last_move": "%d,%d" % last2}, skipped=False)))
    hist = _rv_hist_split(room['move_hist'])
    hist.append((('b' if is_owner else 'w'), _rv_vertex(x, y)))
    opp_uid = room['guest_id'] if is_owner else room['owner_id']
    if '.' not in nb:
        db.execute("UPDATE reversi_rooms SET board_state=?, last_move=?, move_hist=?, updated_at=? WHERE id=?",
                   (nb, "%d,%d" % (x, y), _rv_hist_join(hist), time.time(), rid))
        db.commit()
        fin = _rv_finish(rid, db, room, "棋盘已满")
        return _out(dict({"ok": True, "over": True, "result": _rv_result(fin["winner"], me['id'])},
                            **_rv_room_payload({**room, "board_state": nb, "status": "finished",
                                                "winner_id": fin["winner"], "last_move": "%d,%d" % (x, y)}),
                            msg="棋终，" + fin["msg"]))
    if _rv_legal_moves(nb, opp):
        db.execute("UPDATE reversi_rooms SET board_state=?, turn_user_id=?, last_move=?, move_hist=?, updated_at=? WHERE id=?",
                   (nb, opp_uid, "%d,%d" % (x, y), _rv_hist_join(hist), time.time(), rid))
        db.commit()
        return _out(dict({"ok": True, "skipped": False},
                            **_rv_room_payload({**room, "board_state": nb, "turn_user_id": opp_uid,
                                                "last_move": "%d,%d" % (x, y)})))
    if not _rv_legal_moves(nb, player):
        db.execute("UPDATE reversi_rooms SET board_state=?, last_move=?, move_hist=?, updated_at=? WHERE id=?",
                   (nb, "%d,%d" % (x, y), _rv_hist_join(hist), time.time(), rid))
        db.commit()
        fin = _rv_finish(rid, db, room, "双方均无子可下")
        return _out(dict({"ok": True, "over": True, "result": _rv_result(fin["winner"], me['id'])},
                            **_rv_room_payload({**room, "board_state": nb, "status": "finished",
                                                "winner_id": fin["winner"], "last_move": "%d,%d" % (x, y)}),
                            msg="棋终，" + fin["msg"]))
    db.execute("UPDATE reversi_rooms SET board_state=?, turn_user_id=?, last_move=?, move_hist=?, updated_at=? WHERE id=?",
               (nb, me['id'], "%d,%d" % (x, y), _rv_hist_join(hist), time.time(), rid))
    db.commit()
    return _out(dict({"ok": True, "skipped": True, "msg": "对方无子可下，跳过回合，请继续走棋"},
                        **_rv_room_payload({**room, "board_state": nb, "turn_user_id": me['id'],
                                            "last_move": "%d,%d" % (x, y)}, skipped=True)))






def reversi_state(rid):
    me = D['current_user']()
    room = D['get_db']().execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    # 观战：任何人可读
    return jsonify(dict({"ok": True, "my_color": RV_BLACK if is_owner else RV_WHITE},
                        **_rv_room_payload(room)))






def reversi_restart(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id']:
        return jsonify({"ok": False, "msg": "仅房主可重开"})
    if room['mode'] == 0 and room['guest_id'] is None:
        return jsonify({"ok": False, "msg": "暂无对手"})
    db.execute(
        "UPDATE reversi_rooms SET board_state=?, status='playing', turn_user_id=?, winner_id=NULL, "
        "last_move=NULL, move_hist='', stats_awarded=0, updated_at=? WHERE id=?",
        (_rv_init(), room['owner_id'], time.time(), rid))
    db.commit()
    try:
        fresh = db.execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
        D['socketio'].emit('rv_update', dict({"ok": True}, **_rv_room_payload(fresh)),
                      room='rv_%d' % rid)
        D['socketio'].emit('rv_lobby_update', {}, room='rv_lobby')
    except Exception:
        pass
    return jsonify({"ok": True})



RV_N = 8


RV_BLACK = '1'


RV_WHITE = '2'


def _rv_init():
    """标准开局：中心 2x2 交叉布子，黑（1）先。"""
    b = list(RV_EMPTY)
    b[3 * RV_N + 3] = RV_WHITE
    b[4 * RV_N + 4] = RV_WHITE
    b[3 * RV_N + 4] = RV_BLACK
    b[4 * RV_N + 3] = RV_BLACK
    return ''.join(b)


def _rv_parse(b):
    return b if b else _rv_init()


def _rv_at(b, x, y):
    return b[y * RV_N + x]


def _rv_flips(b, x, y, player):
    """若 (x,y) 落 player 子合法，返回将被翻转的格子列表；否则 None。"""
    if not (0 <= x < RV_N and 0 <= y < RV_N):
        return None
    if _rv_at(b, x, y) != '.':
        return None
    opp = RV_WHITE if player == RV_BLACK else RV_BLACK
    flips = []
    for dx, dy in RV_DIRS:
        line = []
        cx, cy = x + dx, y + dy
        while 0 <= cx < RV_N and 0 <= cy < RV_N:
            c = _rv_at(b, cx, cy)
            if c == '.':
                break
            if c == player:
                flips.extend(line)
                break
            line.append((cx, cy))
            cx += dx
            cy += dy
    return flips or None


def _rv_legal_moves(b, player):
    """返回当前方可下的所有合法点 [(x,y),...]。"""
    out = []
    for y in range(RV_N):
        for x in range(RV_N):
            if _rv_flips(b, x, y, player) is not None:
                out.append((x, y))
    return out


def _rv_apply(b, x, y, player):
    arr = list(b)
    arr[y * RV_N + x] = player
    for (fx, fy) in (_rv_flips(b, x, y, player) or []):
        arr[fy * RV_N + fx] = player
    return ''.join(arr)


def _rv_counts(b):
    return (b.count(RV_BLACK), b.count(RV_WHITE))


def _rv_ai_move(b, color):
    """启发式 AI：权重和 + 翻转数 + 随机微扰，返回 (x,y)。"""
    best = None
    best_s = None
    for (x, y) in _rv_legal_moves(b, color):
        nb = _rv_apply(b, x, y, color)
        flips = len(_rv_flips(b, x, y, color))
        score = flips * 3 + 0.6 * len(_rv_legal_moves(nb, RV_WHITE if color == RV_BLACK else RV_BLACK))
        for yy in range(8):
            for xx in range(8):
                c = nb[yy * 8 + xx]
                if c != '.':
                    score += _RV_W[yy][xx] * (1 if c == color else -0.9)
        score += (x + y) % 3 * 0.1
        if best_s is None or score > best_s:
            best_s = score
            best = (x, y)
    return best



RV_EMPTY = '.' * (RV_N * RV_N)


RV_DIRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


_RV_W = (
    (100, -10, 8, 6, 6, 8, -10, 100),
    (-10, -20, -4, -4, -4, -4, -20, -10),
    (8, -4, 2, 1, 1, 2, -4, 8),
    (6, -4, 1, 0, 0, 1, -4, 6),
    (6, -4, 1, 0, 0, 1, -4, 6),
    (8, -4, 2, 1, 1, 2, -4, 8),
    (-10, -20, -4, -4, -4, -4, -20, -10),
    (100, -10, 8, 6, 6, 8, -10, 100),
)







def reversi_delete(rid):
    """删除房间（房主或超管）。

    2026-11-01 修：拆包时漏了依赖注入 —— current_user / get_db / socketio
    都是裸名字，NameError。这个接口从拆包起就是 500。
    """
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM reversi_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id'] and not me['is_super_admin']:
        return jsonify({"ok": False, "msg": "仅房主或超管可删除"})
    db.execute("DELETE FROM reversi_rooms WHERE id=?", (rid,))
    db.commit()
    try:
        D['socketio'].emit('rv_update', {"closed": True}, room='rv_%d' % rid)
        D['socketio'].emit('rv_lobby_update', {}, room='rv_lobby')
    except Exception:
        pass
    return jsonify({"ok": True})


