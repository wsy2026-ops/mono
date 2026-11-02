# -*- coding: utf-8 -*-
"""board/chess.py — 国际象棋（Stockfish 引擎 + 房间对战）。

从 D['app'].py 搬迁。helper 一律走 deps.D 注入（不 import D['app']）。
"""
import os
import time
import atexit as _atexit_mod
import queue as _queue_mod
import subprocess as _subprocess_mod
import threading as _threading_mod

from flask import abort, jsonify, render_template, request

from .deps import D
from blocking import run_blocking
from games.mini import _board_wins_leaderboard

CS_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


CS_UNI = {'K': '♔', 'Q': '♕', 'R': '♖', 'B': '♗', 'N': '♘', 'P': '♙',
          'k': '♚', 'q': '♛', 'r': '♜', 'b': '♝', 'n': '♞', 'p': '♟'}


CS_DIRS = {'R': ((1, 0), (-1, 0), (0, 1), (0, -1)),
           'B': ((1, 1), (1, -1), (-1, 1), (-1, -1)),
           'Q': ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)),
           'N': ((1, 2), (2, 1), (2, -1), (1, -2), (-1, 2), (-2, 1), (-2, -1), (-1, -2))}


CS_FILES = 'abcdefgh'


def _cs_parse(fen=None):
    """解析 FEN → 内部状态 dict；空则标准开局。"""
    fen = fen or CS_START_FEN
    f = fen.split()
    rows = f[0].split('/')
    board = ''
    for row in rows:
        for c in row:
            if c.isdigit():
                board += '.' * int(c)
            else:
                board += c
    return {'board': board, 'turn': f[1] if len(f) > 1 else 'w',
            'castle': f[2] if len(f) > 2 else '-',
            'ep': f[3] if len(f) > 3 and f[3] != '-' else '',
            'half': int(f[4]) if len(f) > 4 else 0}


def _cs_fen(s):
    b = s['board']
    rows = []
    for y in range(8):
        row, run = '', 0
        for x in range(8):
            c = b[y * 8 + x]
            if c == '.':
                run += 1
            else:
                if run:
                    row += str(run); run = 0
                row += c
        if run:
            row += str(run)
        rows.append(row)
    return '/'.join(rows) + ' %s %s %s %d 0' % (s['turn'], s['castle'], s['ep'] or '-', s['half'])


def _cs_at(s, x, y):
    return s['board'][y * 8 + x]


def _cs_put(s, x, y, v):
    i = y * 8 + x
    s['board'] = s['board'][:i] + v + s['board'][i + 1:]


def _cs_xy(uci):
    """'e2' → (4,6)  (y0=第8横排, 白方在底部 y6/7)"""
    if len(uci) < 2:
        return None
    x = CS_FILES.find(uci[0])
    y = 8 - int(uci[1])
    if x < 0 or not (0 <= y < 8):
        return None
    return x, y


def _cs_uci(x, y):
    return CS_FILES[x] + str(8 - y)


def _cs_attacked(s, x, y, by):
    """(x,y) 是否被 by 方棋子攻击。"""
    for ty in range(8):
        for tx in range(8):
            c = _cs_at(s, tx, ty)
            if c == '.' or (c.isupper() != (by == 'w')):
                continue
            k = c.upper()
            if k == 'P':
                d = -1 if by == 'w' else 1
                if ty + d == y and abs(tx - x) == 1:
                    return True
                continue
            if k == 'K':
                if max(abs(tx - x), abs(ty - y)) == 1:
                    return True
                continue
            if k == 'N':
                if (abs(tx - x), abs(ty - y)) in ((1, 2), (2, 1)):
                    return True
                continue
            if k == 'R':
                dirs = ((1, 0), (-1, 0), (0, 1), (0, -1))
            elif k == 'B':
                dirs = ((1, 1), (1, -1), (-1, 1), (-1, -1))
            elif k == 'Q':
                dirs = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
            else:
                continue
            for dx, dy in dirs:
                cx, cy = tx + dx, ty + dy
                while 0 <= cx < 8 and 0 <= cy < 8:
                    cur = _cs_at(s, cx, cy)
                    if cur != '.':
                        if cx == x and cy == y:
                            return True
                        break
                    cx += dx
                    cy += dy
    return False


def _cs_legal(s):
    """返回当前方全部合法着法 uci 列表（兵升变含 4 个变体）。"""
    turn = s['turn']
    out = []
    moves = []
    for y in range(8):
        for x in range(8):
            c = _cs_at(s, x, y)
            if c == '.' or (c.isupper() != (turn == 'w')):
                continue
            k = c.upper()
            cand = []
            if k == 'P':
                d = -1 if turn == 'w' else 1
                home = 6 if turn == 'w' else 1
                ny = y + d
                if 0 <= ny < 8 and _cs_at(s, x, ny) == '.':
                    cand.append((x, ny))
                    if y == home and _cs_at(s, x, y + 2 * d) == '.':
                        cand.append((x, y + 2 * d))
                for dx in (-1, 1):
                    nx = x + dx
                    if 0 <= nx < 8 and 0 <= ny < 8:
                        t = _cs_at(s, nx, ny)
                        if t != '.' and (t.isupper() != (turn == 'w')):
                            cand.append((nx, ny))
                        elif t == '.' and s['ep'] == _cs_uci(nx, ny):
                            cand.append((nx, ny))
            elif k == 'K':
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < 8 and 0 <= ny < 8:
                            t = _cs_at(s, nx, ny)
                            if t == '.' or (t.isupper() != (turn == 'w')):
                                cand.append((nx, ny))
                if turn == 'w' and y == 7:
                    if 'K' in s['castle'] and _cs_at(s, 5, 7) == '.' and _cs_at(s, 6, 7) == '.' \
                            and _cs_at(s, 7, 7) == 'R' and not _cs_attacked(s, 4, 7, 'b') \
                            and not _cs_attacked(s, 5, 7, 'b') and not _cs_attacked(s, 6, 7, 'b'):
                        cand.append((6, 7))
                    if 'Q' in s['castle'] and _cs_at(s, 3, 7) == '.' and _cs_at(s, 2, 7) == '.' \
                            and _cs_at(s, 1, 7) == '.' and _cs_at(s, 0, 7) == 'R' \
                            and not _cs_attacked(s, 4, 7, 'b') and not _cs_attacked(s, 3, 7, 'b') \
                            and not _cs_attacked(s, 2, 7, 'b'):
                        cand.append((2, 7))
                if turn == 'b' and y == 0:
                    if 'k' in s['castle'] and _cs_at(s, 5, 0) == '.' and _cs_at(s, 6, 0) == '.' \
                            and _cs_at(s, 7, 0) == 'r' and not _cs_attacked(s, 4, 0, 'w') \
                            and not _cs_attacked(s, 5, 0, 'w') and not _cs_attacked(s, 6, 0, 'w'):
                        cand.append((6, 0))
                    if 'q' in s['castle'] and _cs_at(s, 3, 0) == '.' and _cs_at(s, 2, 0) == '.' \
                            and _cs_at(s, 1, 0) == '.' and _cs_at(s, 0, 0) == 'r' \
                            and not _cs_attacked(s, 4, 0, 'w') and not _cs_attacked(s, 3, 0, 'w') \
                            and not _cs_attacked(s, 2, 0, 'w'):
                        cand.append((2, 0))
            elif k == 'N':
                for dx, dy in CS_DIRS['N']:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < 8 and 0 <= ny < 8:
                        t = _cs_at(s, nx, ny)
                        if t == '.' or (t.isupper() != (turn == 'w')):
                            cand.append((nx, ny))
            else:
                for dx, dy in CS_DIRS[k]:
                    nx, ny = x + dx, y + dy
                    while 0 <= nx < 8 and 0 <= ny < 8:
                        t = _cs_at(s, nx, ny)
                        if t == '.':
                            cand.append((nx, ny))
                        else:
                            if t.isupper() != (turn == 'w'):
                                cand.append((nx, ny))
                            break
                        nx += dx
                        ny += dy
            promo_rank = (0 if turn == 'w' else 7)
            for mx, my in cand:
                if k == 'P' and my == promo_rank:
                    for pr in (('Q', 'R', 'B', 'N') if turn == 'w' else ('q', 'r', 'b', 'n')):
                        moves.append((_cs_uci(x, y) + _cs_uci(mx, my) + pr.lower(), mx, my, pr))
                else:
                    moves.append((_cs_uci(x, y) + _cs_uci(mx, my), mx, my, None))
    enemy = 'b' if turn == 'w' else 'w'
    for uci, mx, my, promo in moves:
        s3 = _cs_clone(s)
        fr_x = CS_FILES.find(uci[0])
        fr_y = 8 - int(uci[1])
        _cs_exec(s3, fr_x, fr_y, mx, my, promo)
        kx, ky = _cs_king(s3, turn)
        if kx is not None and not _cs_attacked(s3, kx, ky, enemy):
            out.append(uci)
    return out


def _cs_clone(s):
    return {'board': s['board'], 'turn': s['turn'], 'castle': s['castle'],
            'ep': s['ep'], 'half': s['half']}


def _cs_king(s, color):
    k = 'K' if color == 'w' else 'k'
    i = s['board'].find(k)
    if i < 0:
        return None, None
    return i % 8, i // 8


def _cs_exec(s, x, y, mx, my, promo=None):
    """直接执行（前提已由合法列表验证）。更新 s。"""
    c = _cs_at(s, x, y)
    turn = s['turn']
    enemy = 'b' if turn == 'w' else 'w'
    captured = _cs_at(s, mx, my)
    if c.upper() == 'K' and abs(mx - x) == 2:
        if mx == 6:
            _cs_put(s, 5, y, _cs_at(s, 7, y)); _cs_put(s, 7, y, '.')
        else:
            _cs_put(s, 3, y, _cs_at(s, 0, y)); _cs_put(s, 0, y, '.')
    if c.upper() == 'P' and captured == '.' and mx != x:
        _cs_put(s, mx, y, '.')
    if c.upper() == 'P' or captured != '.':
        s['half'] = 0
    else:
        s['half'] += 1
    cl = s['castle']
    if c == 'K':
        cl = cl.replace('K', '').replace('Q', '')
    elif c == 'k':
        cl = cl.replace('k', '').replace('q', '')
    if c == 'R' and x == 0 and y == 7:
        cl = cl.replace('Q', '')
    if c == 'R' and x == 7 and y == 7:
        cl = cl.replace('K', '')
    if c == 'r' and x == 0 and y == 0:
        cl = cl.replace('q', '')
    if c == 'r' and x == 7 and y == 0:
        cl = cl.replace('k', '')
    if mx == 0 and my == 0:
        cl = cl.replace('q', '')
    if mx == 7 and my == 0:
        cl = cl.replace('k', '')
    if mx == 0 and my == 7:
        cl = cl.replace('Q', '')
    if mx == 7 and my == 7:
        cl = cl.replace('K', '')
    s['castle'] = ''.join(ch for ch in 'KQkq' if ch in cl)
    s['ep'] = ''
    if c.upper() == 'P' and abs(my - y) == 2:
        s['ep'] = _cs_uci(x, (y + my) // 2)
    _cs_put(s, mx, my, c if not promo else promo)
    _cs_put(s, x, y, '.')
    s['turn'] = enemy


def _cs_status(s):
    """返回 (status, detail)。playing/checkmate/stalemate/draw_xxx"""
    legal = _cs_legal(s)
    turn = s['turn']
    kx, ky = _cs_king(s, turn)
    in_chk = kx is not None and _cs_attacked(s, kx, ky, 'b' if turn == 'w' else 'w')
    if not legal:
        return ('checkmate' if in_chk else 'stalemate'), in_chk
    b = s['board'].replace('.', '')
    majors = [c for c in b if c.upper() in 'QR']
    pawns = b.count('P') + b.count('p')
    if not majors and not pawns and len(b) <= 4:
        minors = [c for c in b if c.upper() in 'NB']
        if len(b) <= 2 or (len(minors) == 1):
            return ('draw', 'insufficient')
    if in_chk:
        return ('check', True)
    return ('playing', False)


def _cs_replay_state(uci_moves):
    s = _cs_parse()
    for u in (uci_moves or '').split():
        u = u.lower()
        fr = u[:2]
        to = u[2:4]
        promo = u[4] if len(u) > 4 else None
        x, y = _cs_xy(fr)
        mx, my = _cs_xy(to)
        c = _cs_at(s, x, y)
        turn = s['turn']
        if promo:
            promo = promo.upper() if turn == 'w' else promo.lower()
        _cs_exec(s, x, y, mx, my, promo)
    return s


def _stockfish_detect():
    """Linux 版是 stockfish / stockfish-ubuntu-* 等无扩展名文件；Windows 是 *.exe。"""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stockfish')
    try:
        for f in sorted(os.listdir(d)):
            low = f.lower()
            p = os.path.join(d, f)
            if not os.path.isfile(p):
                continue
            if not (low.endswith('.exe') or low.startswith('stockfish')):
                continue
            if low.endswith(('.txt', '.md', '.json', '.py', '.sha256')):
                continue
            return p
    except Exception:
        pass
    return None


_STOCKFISH_EXE = _stockfish_detect()


_STOCKFISH_AVAILABLE = bool(_STOCKFISH_EXE)


_STOCKFISH_ENGINES = {}


_STOCKFISH_FAILED = set()


_STOCKFISH_LOCK = _threading_mod.Lock()


_STOCKFISH_MAX = 2 if D['_LOW_MEM'] else 3


def _stockfish_version_ok(exe):
    try:
        p = _subprocess_mod.Popen([exe], stdin=_subprocess_mod.PIPE, stdout=_subprocess_mod.PIPE,
                                  stderr=_subprocess_mod.DEVNULL, cwd=os.path.dirname(exe))
        p.stdin.write(b'uci\n')
        p.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < 15:
            line = p.stdout.readline()
            if not line:
                break
            if line.startswith(b'id name Stockfish'):
                p.stdin.write(b'quit\n'); p.stdin.flush()
                D['_proc_stop'](p, grace=3)
                return True
        D['_proc_stop'](p, grace=3)
    except Exception:
        return False
    return False


if _STOCKFISH_EXE and not _stockfish_version_ok(_STOCKFISH_EXE):
    _STOCKFISH_AVAILABLE = False


class _StockfishEngine(object):
    def __init__(self, exe):
        self.exe = exe
        self.proc = None
        self.q = None

    def start(self):
        self.proc = _subprocess_mod.Popen([self.exe], stdin=_subprocess_mod.PIPE,
                                          stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.DEVNULL,
                                          cwd=os.path.dirname(self.exe))
        self.q = _queue_mod.Queue()
        t = _threading_mod.Thread(target=self._read, daemon=True)
        t.start()
        if 'uciok' not in self._cmd('uci', stop='uciok', timeout=60):
            raise RuntimeError('stockfish uci handshake timeout')
        if 'readyok' not in self._cmd('isready', stop='readyok', timeout=60):
            raise RuntimeError('stockfish isready timeout')

    def _read(self):
        try:
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    break
                self.q.put(raw.decode('utf-8', 'replace').strip())
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

    def _cmd(self, line, stop=None, timeout=90):
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
            out.append(s)
            if stop and s == stop:
                break
            if s.startswith('bestmove'):
                break
        return out

    def bestmove(self, uci_moves, movetime=1000):
        mv = ' '.join(uci_moves)
        if mv:
            self._cmd('position startpos moves ' + mv, timeout=15)
        else:
            self._cmd('position startpos', timeout=15)
        out = self._cmd('go movetime %d' % movetime, timeout=movetime + 15000)
        for s in out:
            if s.startswith('bestmove'):
                p = s.split()
                if len(p) >= 2 and p[1] != '(none)':
                    return p[1].lower()
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


def _chess_get(rid):
    if not _STOCKFISH_AVAILABLE:
        return None
    with _STOCKFISH_LOCK:
        e = _STOCKFISH_ENGINES.get(rid)
        if e is not None:
            return e
        if rid in _STOCKFISH_FAILED or len(_STOCKFISH_ENGINES) >= _STOCKFISH_MAX:
            return None
        e = _StockfishEngine(_STOCKFISH_EXE)
        try:
            e.start()
        except Exception:
            _STOCKFISH_FAILED.add(rid)
            return None
        _STOCKFISH_ENGINES[rid] = e
        return e


def _chess_warm(rid):
    """建房（PvAI）后后台预热引擎：冷启动加载较慢，避免玩家首手时等待/超时。"""
    try:
        _chess_get(rid)
    except Exception:
        _STOCKFISH_FAILED.add(rid)


def _chess_close(rid):
    with _STOCKFISH_LOCK:
        e = _STOCKFISH_ENGINES.pop(rid, None)
    if e is not None:
        try:
            e.close()
        except Exception:
            pass


@_atexit_mod.register


def _stockfish_cleanup():
    with _STOCKFISH_LOCK:
        es = list(_STOCKFISH_ENGINES.values())
        _STOCKFISH_ENGINES.clear()
    for e in es:
        try:
            e.close()
        except Exception:
            pass


CS_UCI_RE = None


def _cs_room_fen(room):
    return room['board_state'] or CS_START_FEN






def chess_list():
    me = D['current_user']()
    db = D['get_db']()
    public = db.execute(
        "SELECT r.*, u.username owner_name FROM chess_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.is_public=1 AND r.status='waiting' AND r.mode=0 AND r.owner_id != ? "
        "ORDER BY r.id DESC LIMIT 20", (me['id'],)).fetchall()
    mine = db.execute(
        "SELECT r.*, u.username owner_name FROM chess_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.owner_id=? OR r.guest_id=? ORDER BY r.id DESC LIMIT 20",
        (me['id'], me['id'])).fetchall()
    return render_template("chess_list.html", user=me, public=public, mine=mine,
                           ai_ok=_STOCKFISH_AVAILABLE,
                           blb_rows=_board_wins_leaderboard('chess'))






def chess_join(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['mode'] == 1:
        return jsonify({"ok": False, "msg": "人机房间无需加入"})
    if room['status'] != 'waiting' or room['owner_id'] == me['id'] or room['guest_id'] is not None:
        return jsonify({"ok": False, "msg": "无法加入"})
    db.execute("UPDATE chess_rooms SET guest_id=?, status='playing', turn_user_id=?, updated_at=? WHERE id=?",
               (me['id'], room['owner_id'], time.time(), rid))
    db.commit()
    try:
        fresh = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
        D['socketio'].emit('cs_update', dict({"ok": True}, **_cs_room_payload(fresh)),
                      room='cs_%d' % rid)
        D['socketio'].emit('cs_lobby_update', {}, room='cs_lobby')
    except Exception:
        pass
    return jsonify({"ok": True})






def chess_random_join():
    me = D['current_user']()
    db = D['get_db']()
    for row in db.execute(
            "SELECT id FROM chess_rooms WHERE is_public=1 AND status='waiting' AND mode=0 "
            "AND owner_id != ? AND guest_id IS NULL ORDER BY RANDOM() LIMIT 5", (me['id'],)).fetchall():
        r = db.execute(
            "UPDATE chess_rooms SET guest_id=?, status='playing', turn_user_id=owner_id, updated_at=? "
            "WHERE id=? AND status='waiting' AND guest_id IS NULL",
            (me['id'], time.time(), row['id']))
        if r.rowcount:
            db.commit()
            try:
                fresh = db.execute("SELECT * FROM chess_rooms WHERE id=?", (row['id'],)).fetchone()
                D['socketio'].emit('cs_update', dict({"ok": True}, **_cs_room_payload(fresh)),
                              room='cs_%d' % row['id'])
                D['socketio'].emit('cs_lobby_update', {}, room='cs_lobby')
            except Exception:
                pass
            return jsonify({"ok": True, "rid": row['id']})
    return jsonify({"ok": False, "msg": "暂无可加入的公开房间"})






def chess_room(rid):
    me = D['current_user']()
    room = D['get_db']().execute(
        "SELECT r.*, ou.username owner_name, gu.username guest_name FROM chess_rooms r "
        "LEFT JOIN users ou ON ou.id=r.owner_id LEFT JOIN users gu ON gu.id=r.guest_id "
        "WHERE r.id=?", (rid,)).fetchone()
    if not room:
        abort(404)
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    is_spectator = not (is_owner or is_guest)   # 观战只读
    return render_template("chess_room.html", user=me, room=room,
                           is_owner=is_owner, is_guest=is_guest,
                           ai_ok=_STOCKFISH_AVAILABLE, ai_builtin=False,
                           og=D['og_context'](title="来下一盘中国象棋 · MONO",
                                         desc="好友开了个象棋房间，就等你了。",
                                         site_name="MONO 社区"))






def chess_legal(rid):
    """返回某起点的全部合法目标（uci 列表）。body: from=e2"""
    me = D['current_user']()
    room = D['get_db']().execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['status'] != 'playing' or room['turn_user_id'] != me['id']:
        return jsonify({"ok": False, "moves": []})
    fr = (request.form.get("from") or '').strip().lower()
    s = _cs_parse(_cs_room_fen(room))
    targets = [u[2:] for u in _cs_legal(s) if u[:2] == fr]
    return jsonify({"ok": True, "moves": targets})


def _chess_ai_play(rid, uci_hist):
    """后台线程：Stockfish 执黑应手（首次冷启动可能较慢），完成后更新房间并实时推送。

    玩家走子已在 HTTP 请求内落库并返回，这里只负责 AI 那一步，不阻塞前端点击。
    """
    ai = None
    try:
        eng = _chess_get(rid)
        if eng is not None:
            ai = run_blocking(eng.bestmove, uci_hist.split(),
                              movetime=D['_ai_ms_for']('chess_rooms', rid, 1200, 'chess'))
    except Exception:
        _chess_close(rid)
        ai = None
    if ai:
        try:
            s2 = _cs_replay_state(uci_hist)
            if ai not in _cs_legal(s2):
                _chess_close(rid)
                ai = None
        except Exception:
            ai = None
    try:
        with D['app'].app_context():
            db = D['get_db']()
            room = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
            if not room or room['status'] != 'playing':
                return
            if ai is None:
                db.execute("UPDATE chess_rooms SET turn_user_id=?, updated_at=? WHERE id=?",
                           (room['owner_id'], time.time(), rid))
                db.commit()
                fresh = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
                D['socketio'].emit('cs_update',
                              dict({"ok": True, "msg": "Stockfish 引擎暂不可用，已跳过本步 AI"},
                                   **_cs_room_payload(fresh)),
                              room='cs_%d' % rid)
                return
            hist2 = (uci_hist + ' ' + ai).strip()
            s3 = _cs_replay_state(hist2)
            st3, _ = _cs_status(s3)
            db.execute("UPDATE chess_rooms SET board_state=?, uci_moves=?, last_move=?, "
                       "turn_user_id=?, updated_at=? WHERE id=?",
                       (_cs_fen(s3), hist2, ai, room['owner_id'], time.time(), rid))
            db.commit()
            if st3 in ('checkmate', 'stalemate'):
                _cs_finish_room(rid, db, '')
            fresh = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
            D['socketio'].emit('cs_update', dict({"ok": True, "msg": "AI 走：" + ai},
                                            **_cs_room_payload(fresh)),
                          room='cs_%d' % rid)
    except Exception:
        pass






def chess_move(rid):

    def _out(payload):
        try:
            D['socketio'].emit('cs_update', payload, room='cs_%d' % rid)
        except Exception:
            pass
        return jsonify(payload)
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return _out({"ok": False, "msg": "房间不存在"})
    if room['status'] != 'playing':
        return _out({"ok": False, "msg": "对局已结束"})
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    if not (is_owner or is_guest):
        return _out({"ok": False, "msg": "你不是本局玩家"})
    if request.form.get("resign") == "1":
        wid = room['guest_id'] if is_owner else room['owner_id']
        if room['mode'] == 1 and is_owner:
            wid = -1
        db.execute("UPDATE chess_rooms SET status='finished', winner_id=?, updated_at=? WHERE id=?",
                   (wid, time.time(), rid))
        db.commit()
        D['_award_game_stats']('chess', rid)
        return _out({"ok": True})
    if room['turn_user_id'] != me['id']:
        return _out({"ok": False, "msg": "还没轮到你"})
    my_white = is_owner
    uci = (request.form.get("uci") or '').strip().lower()
    s = _cs_parse(_cs_room_fen(room))
    legal = _cs_legal(s)
    if uci not in legal:
        return _out({"ok": False, "msg": "不合法的走法"})
    uci_hist = ((room['uci_moves'] or '') + ' ' + uci).strip()
    final_uci = uci_hist
    if room['mode'] == 1 and is_owner:
        s_after = _cs_replay_state(uci_hist)
        fen_after = _cs_fen(s_after)
        st_after, _ = _cs_status(s_after)
        if st_after not in ('checkmate', 'stalemate'):
            db.execute("UPDATE chess_rooms SET board_state=?, uci_moves=?, last_move=?, "
                       "turn_user_id=-1, updated_at=? WHERE id=?",
                       (fen_after, uci_hist, uci, time.time(), rid))
            db.commit()
            resp = dict({"ok": True, "ai_pending": True, "msg": "AI 思考中…"},
                        **_cs_room_payload({**room, "board_state": fen_after, "uci_moves": uci_hist,
                                            "turn_user_id": -1, "last_move": uci}))
            _threading_mod.Thread(target=_chess_ai_play, args=(rid, uci_hist), daemon=True).start()
            return _out(resp)
    sF = _cs_replay_state(final_uci)
    fenF = _cs_fen(sF)
    st, _ = _cs_status(sF)
    h = _cs_fen(sF).split(' 0')[0]
    cnt = sum(1 for i in range(0, len(final_uci.split()) + 1)
              if _cs_fen(_cs_replay_state(' '.join(final_uci.split()[:i]))).split(' 0')[0] == h)
    last_uci = final_uci.split()[-1]
    db.execute("UPDATE chess_rooms SET board_state=?, uci_moves=?, last_move=?, updated_at=? WHERE id=?",
               (fenF, final_uci, last_uci, time.time(), rid))
    db.commit()
    if st == 'checkmate' or st == 'stalemate' or sF['half'] >= 100 or cnt >= 3:
        _cs_finish_room(rid, db, '')
        room2 = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
        return _out(dict({"ok": True, "over": True}, **_cs_room_payload(room2)))
    if st == 'check':
        pass
    if room['mode'] == 1:
        nxt = me['id']
        if final_uci != uci_hist:
            nxt = me['id']
    else:
        nxt = room['guest_id'] if is_owner else room['owner_id']
    db.execute("UPDATE chess_rooms SET turn_user_id=?, updated_at=? WHERE id=?", (nxt, time.time(), rid))
    db.commit()
    room = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    return _out(dict({"ok": True, "msg": "AI 走：" + final_uci.split()[-1] if final_uci != uci_hist else ""},
                        **_cs_room_payload(room)))






def chess_state(rid):
    me = D['current_user']()
    room = D['get_db']().execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    # 观战：任何人可读
    return jsonify(dict({"ok": True}, **_cs_room_payload(room)))






def chess_restart(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id']:
        return jsonify({"ok": False, "msg": "仅房主可重开"})
    if room['mode'] == 0 and room['guest_id'] is None:
        return jsonify({"ok": False, "msg": "暂无对手"})
    db.execute("UPDATE chess_rooms SET board_state=?, uci_moves='', last_move=NULL, status='playing', "
               "turn_user_id=?, winner_id=NULL, stats_awarded=0, updated_at=? WHERE id=?",
               (CS_START_FEN, room['owner_id'], time.time(), rid))
    db.commit()
    _chess_close(rid)
    try:
        fresh = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
        D['socketio'].emit('cs_update', dict({"ok": True}, **_cs_room_payload(fresh)),
                      room='cs_%d' % rid)
        D['socketio'].emit('cs_lobby_update', {}, room='cs_lobby')
    except Exception:
        pass
    return jsonify({"ok": True})






def chess_delete(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id'] and not me['is_super_admin']:
        return jsonify({"ok": False, "msg": "仅房主或超管可删除"})
    db.execute("DELETE FROM chess_rooms WHERE id=?", (rid,))
    db.commit()
    _chess_close(rid)
    try:
        D['socketio'].emit('cs_update', {"closed": True}, room='cs_%d' % rid)
        D['socketio'].emit('cs_lobby_update', {}, room='cs_lobby')
    except Exception:
        pass
    return jsonify({"ok": True})



def _cs_room_payload(room):
    s = _cs_parse(_cs_room_fen(room))
    st, chk = _cs_status(s)
    try:
        _um = room['uci_moves'] or ''
    except Exception:
        _um = ''
    return {
        "fen": _cs_fen(s),
        "status": room['status'],
        "winner_id": room['winner_id'],
        "turn_user_id": room['turn_user_id'],
        "last_move": room['last_move'] or '',
        "uci_moves": _um,
        "state": st,
        "check_color": (s['turn'] if st in ('check', 'checkmate') else None),
        "in_check": chk if isinstance(chk, bool) else (st in ('check', 'checkmate')),
        "black": s['board'].count('b') + s['board'].count('B'),
    }


def _cs_finish_room(rid, db, msg):
    """终局：重放判断 winner 已经由调用方算好传参？此处按当前 FEN 判定将死方。"""
    room = db.execute("SELECT * FROM chess_rooms WHERE id=?", (rid,)).fetchone()
    s = _cs_parse(room['board_state'])
    st, _ = _cs_status(s)
    winner_id = None
    if st == 'checkmate':
        winner_id = room['owner_id'] if s['turn'] == 'b' else (room['guest_id'] or -1)
    if winner_id is None:
        winner_id = 0
    db.execute("UPDATE chess_rooms SET status='finished', winner_id=?, updated_at=? WHERE id=?",
               (winner_id, time.time(), rid))
    db.commit()
    D['_award_game_stats']('chess', rid)
    return winner_id, msg


