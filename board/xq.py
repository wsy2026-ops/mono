# -*- coding: utf-8 -*-
"""board/xq.py — 中国象棋（Pikafish 引擎 + 房间对战 + 残局闯关）。

从 D['app'].py 搬迁。helper 一律走 deps.D 注入（不 import D['app']）。
"""
import json
import os
import time
import atexit as _atexit_mod
import queue as _queue_mod
import subprocess as _subprocess_mod
import threading as _threading_mod

from flask import abort, jsonify, render_template, request
from flask_socketio import join_room

from .deps import D
from blocking import run_blocking
from .go import _engine_degraded
from games.mini import _board_wins_leaderboard, _my_ai_survival

XQ_W, XQ_H = 9, 10


def _xq_init():
    rows = [
        "rnbakabnr",
        ".........",
        ".c.....c.",
        "p.p.p.p.p",
        ".........",
        ".........",
        "P.P.P.P.P",
        ".C.....C.",
        ".........",
        "RNBAKABNR",
    ]
    return "".join(rows)


def _xq_at(board, x, y):
    return board[y * XQ_W + x]


def _xq_set(board, x, y, v):
    arr = list(board)
    arr[y * XQ_W + x] = v
    return "".join(arr)


def _xq_in_palace(x, y, red):
    if red:
        return 3 <= x <= 5 and 7 <= y <= 9
    return 3 <= x <= 5 and 0 <= y <= 2


def _xq_piece_moves(board, x, y):
    """该棋子按规则的落点（不含自将军限制）。"""
    p = _xq_at(board, x, y)
    if p == '.':
        return []
    red = p.isupper()
    moves = []

    def _add(tx, ty):
        if 0 <= tx < XQ_W and 0 <= ty < XQ_H:
            t = _xq_at(board, tx, ty)
            if t == '.' or t.isupper() != red:
                moves.append((tx, ty))

    if p in 'Kk':
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty = x + dx, y + dy
            if _xq_in_palace(tx, ty, red):
                _add(tx, ty)
    elif p in 'Aa':
        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            tx, ty = x + dx, y + dy
            if _xq_in_palace(tx, ty, red):
                _add(tx, ty)
    elif p in 'Bb':
        for dx, dy in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
            tx, ty = x + dx, y + dy
            if not (0 <= tx < XQ_W and 0 <= ty < XQ_H):
                continue
            if red and ty < 5:
                continue
            if not red and ty > 4:
                continue
            if _xq_at(board, x + dx // 2, y + dy // 2) != '.':
                continue
            _add(tx, ty)
    elif p in 'Nn':
        for dx, dy, lx, ly in ((1, 2, 0, 1), (-1, 2, 0, 1), (1, -2, 0, -1), (-1, -2, 0, -1),
                               (2, 1, 1, 0), (-2, 1, -1, 0), (2, -1, 1, 0), (-2, -1, -1, 0)):
            tx, ty = x + dx, y + dy
            if 0 <= tx < XQ_W and 0 <= ty < XQ_H:
                if _xq_at(board, x + lx, y + ly) == '.':
                    _add(tx, ty)
    elif p in 'Rr':
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty = x + dx, y + dy
            while 0 <= tx < XQ_W and 0 <= ty < XQ_H:
                t = _xq_at(board, tx, ty)
                if t == '.':
                    _add(tx, ty)
                else:
                    if t.isupper() != red:
                        _add(tx, ty)
                    break
                tx += dx
                ty += dy
    elif p in 'Cc':
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty = x + dx, y + dy
            jumped = False
            while 0 <= tx < XQ_W and 0 <= ty < XQ_H:
                t = _xq_at(board, tx, ty)
                if not jumped:
                    if t == '.':
                        _add(tx, ty)
                    else:
                        jumped = True
                else:
                    if t != '.':
                        if t.isupper() != red:
                            _add(tx, ty)
                        break
                tx += dx
                ty += dy
    elif p in 'Pp':
        if red:
            _add(x, y - 1)
            if y < 5:
                _add(x - 1, y)
                _add(x + 1, y)
        else:
            _add(x, y + 1)
            if y > 4:
                _add(x - 1, y)
                _add(x + 1, y)
    return moves


def _xq_find_king(board, red):
    target = 'K' if red else 'k'
    for y in range(XQ_H):
        for x in range(XQ_W):
            if _xq_at(board, x, y) == target:
                return x, y
    return None


def _xq_path_clear(board, x1, y1, x2, y2):
    """竖线 x1==x2，y1->y2 之间（不含端点）是否无子。"""
    if x1 != x2:
        return False
    step = 1 if y2 > y1 else -1
    yy = y1 + step
    while yy != y2:
        if _xq_at(board, x1, yy) != '.':
            return False
        yy += step
    return True


def _xq_in_check(board, red):
    """red 方是否被将军。"""
    k = _xq_find_king(board, red)
    if k is None:
        return True
    kx, ky = k
    for y in range(XQ_H):
        for x in range(XQ_W):
            p = _xq_at(board, x, y)
            if p == '.' or p.isupper() == red:
                continue
            if (kx, ky) in _xq_piece_moves(board, x, y):
                return True
            if p in 'Kk' and x == kx and _xq_path_clear(board, x, y, kx, ky):
                return True
    return False


def _xq_legal_moves(board, red):
    """red 方所有合法着法（走完后不被将军）。"""
    res = []
    for y in range(XQ_H):
        for x in range(XQ_W):
            p = _xq_at(board, x, y)
            if p == '.' or p.isupper() != red:
                continue
            for tx, ty in _xq_piece_moves(board, x, y):
                if _xq_at(board, tx, ty) in 'Kk':
                    continue
                nb = _xq_set(_xq_set(board, x, y, '.'), tx, ty, p)
                if not _xq_in_check(nb, red):
                    res.append((x, y, tx, ty))
    return res


def _xq_make_move(board, x1, y1, x2, y2):
    """执行一步。返回 (新盘面, 被吃子或None, 是否将军对方, 对方是否无子可动)。非法返回 (None,...)。"""
    p = _xq_at(board, x1, y1)
    if p == '.':
        return None, None, False, False
    red = p.isupper()
    if (x2, y2) not in _xq_piece_moves(board, x1, y1):
        return None, None, False, False
    if _xq_at(board, x2, y2) in 'Kk':
        return None, None, False, False
    captured = _xq_at(board, x2, y2)
    nb = _xq_set(_xq_set(board, x1, y1, '.'), x2, y2, p)
    if _xq_in_check(nb, red):
        return None, None, False, False
    check = _xq_in_check(nb, not red)
    end = not _xq_legal_moves(nb, not red)
    return nb, (captured if captured != '.' else None), check, end


def _xq_why_illegal(board, x1, y1, x2, y2, red):
    """解释一步为何非法（用于给前端更清晰的提示）。"""
    p = _xq_at(board, x1, y1)
    if p == '.' or p.isupper() != red:
        return "请选择己方棋子"
    if (x2, y2) not in _xq_piece_moves(board, x1, y1):
        return "不符合该棋子的走法规则"
    if _xq_at(board, x2, y2) in 'Kk':
        return "不能直接吃掉对方的将 / 帅"
    nb = _xq_set(_xq_set(board, x1, y1, '.'), x2, y2, p)
    kpos = _xq_find_king(nb, red)
    epos = _xq_find_king(nb, not red)
    if kpos and epos and kpos[0] == epos[0] and _xq_path_clear(nb, kpos[0], kpos[1], epos[0], epos[1]):
        return "将帅不能照面（飞将违规）"
    if _xq_in_check(nb, red):
        return "你的将 / 帅正被将军，必须应将（不能走会让将/帅送吃的棋）"
    return "不符合走法规则"


def _xq_replay(uci_moves):
    """重放整局，返回每一步后的棋盘列表与"该步是否将军"。"""
    moves = (uci_moves or '').split()
    boards = [_xq_init()]
    checks = []
    b = _xq_init()
    for uci in moves:
        if len(uci) < 4:
            continue
        f = _xq_uci_to_xy(uci[:2])
        t = _xq_uci_to_xy(uci[2:4])
        if f is None or t is None:
            break
        p = _xq_at(b, f[0], f[1])
        if p == '.':
            break
        red = p.isupper()
        nb, _, _, _ = _xq_make_move(b, f[0], f[1], t[0], t[1])
        if nb is None:
            break
        checks.append(_xq_in_check(nb, not red))
        boards.append(nb)
        b = nb
    return boards, checks


def _xq_check_repetition(uci_moves):
    """检测三次重复局面与长将。返回 ('perpetual_check', loser_red) / ('draw', None) / (None, None)。"""
    moves = (uci_moves or '').split()
    if len(moves) < 3:
        return None, None
    boards, checks = _xq_replay(uci_moves)
    if not boards:
        return None, None
    final = boards[-1]
    target_side = (len(boards) - 1) % 2 == 0
    cnt = sum(1 for i in range(len(boards)) if (i % 2 == 0) == target_side and boards[i] == final)
    if cnt < 3:
        return None, None
    n = len(checks)
    last_mover_red = (n - 1) % 2 == 0
    seq = [checks[n - 1 - k] for k in range(3) if (n - 1 - k) >= 0]
    if len(seq) >= 3 and all(seq):
        return 'perpetual_check', last_mover_red
    return 'draw', None


def _xq_finish_repetition(rid, final_board, final_uci):
    """若构成重复/长将则结束对局并返回 True。"""
    kind, loser_red = _xq_check_repetition(final_uci)
    if kind is None:
        return False
    db = D['get_db']()
    room = db.execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
    if kind == 'perpetual_check':
        if room['mode'] == 1:
            win_id = -1 if loser_red else room['owner_id']
        else:
            win_id = room['guest_id'] if loser_red else room['owner_id']
    else:
        win_id = 0
    db.execute(
        "UPDATE xq_rooms SET board_state=?, status='finished', winner_id=?, updated_at=? WHERE id=?",
        (final_board, win_id, time.time(), rid))
    db.commit()
    return True


XQ_PIECE_VALUE = {'K': 10000, 'A': 200, 'B': 200, 'N': 400, 'R': 900, 'C': 450, 'P': 100,
                  'k': 10000, 'a': 200, 'b': 200, 'n': 400, 'r': 900, 'c': 450, 'p': 100}


XQ_POS_BONUS = {
    'P': {
        'base': 100,
        'crossed': 160, 'deep': 220,
    },
    'N': {'center': 30},
    'C': {'open': 20},
    'R': {'open': 25},
}


def _xq_eval(board, red):
    """静态评估：red 视角（正=红优）。子力 + 位置 + 少量随机。"""
    score = 0.0
    for y in range(XQ_H):
        for x in range(XQ_W):
            p = _xq_at(board, x, y)
            if p == '.':
                continue
            up = p.upper()
            v = XQ_PIECE_VALUE[up]
            if up == 'P':
                if p == 'P' and y <= 4:
                    v += XQ_POS_BONUS['P']['deep'] if y <= 3 else XQ_POS_BONUS['P']['crossed']
                elif p == 'p' and y >= 5:
                    yy = 9 - y
                    v += XQ_POS_BONUS['P']['deep'] if yy <= 3 else XQ_POS_BONUS['P']['crossed']
            elif up == 'N':
                if 2 <= x <= 6 and ((p == 'N' and 2 <= y <= 7) or (p == 'n' and 2 <= 9 - y <= 7)):
                    v += XQ_POS_BONUS['N']['center']
            if p.isupper() == red:
                score += v
            else:
                score -= v
    return score


def _xq_ai_move(board, red, depth=2):
    """内置象棋 AI：极小极大 + alpha-beta 剪枝（默认 2 层）。
    着法按"吃子价值"排序加速剪枝；被将死/困毙返回 None。"""
    import time as _t
    t0 = _t.time()
    moves = _xq_legal_moves(board, red)
    if not moves:
        return None
    _MAX_TIME = 1.6

    def _move_score(m):
        x1, y1, x2, y2 = m
        cap = _xq_at(board, x2, y2)
        return (XQ_PIECE_VALUE.get(cap.upper(), 0) if cap != '.' else 0)

    moves.sort(key=_move_score, reverse=True)

    def _negamax(bd, side, d, alpha, beta, root=False):
        if _t.time() - t0 > _MAX_TIME and not root:
            return _xq_eval(bd, side)
        ms = _xq_legal_moves(bd, side)
        if not ms:
            return -1e9
        if d <= 0:
            return _xq_eval(bd, side)
        ms.sort(key=lambda m: XQ_PIECE_VALUE.get(_xq_at(bd, m[2], m[3]).upper(), 0) if _xq_at(bd, m[2], m[3]) != '.' else 0, reverse=True)
        best = -1e18
        for m in ms:
            nb, captured, check, _end = _xq_make_move(bd, m[0], m[1], m[2], m[3])
            if nb is None:
                continue
            s = -_negamax(nb, not side, d - 1, -beta, -alpha)
            if s > best:
                best = s
            if s > alpha:
                alpha = s
            if alpha >= beta:
                break
        return best

    best_m = None
    best_s = -1e18
    alpha = -1e18
    for m in moves:
        nb, captured, check, end = _xq_make_move(board, m[0], m[1], m[2], m[3])
        if nb is None:
            continue
        if end:
            return m
        s = -_negamax(nb, not red, depth - 1, -1e18, -alpha)
        if s > best_s:
            best_s = s
            best_m = m
        if s > alpha:
            alpha = s
    if best_m is None:
        best_m = moves[0]
    return best_m


_PIKAFISH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pikafish')


_PIKAFISH_MAX_ENGINES = 2 if D['_LOW_MEM'] else 4


_PIKAFISH_TIME_MS = 5000


def _pikafish_ok(exe):
    """轻量验证 pikafish.exe 能真实启动并响应 uci 握手（<1s）。
    拦截「文件存在但系统跑不起来」的场景（32 位系统、缺运行库等）。"""
    try:
        p = _subprocess_mod.Popen([exe],
                                  stdin=_subprocess_mod.PIPE,
                                  stdout=_subprocess_mod.PIPE,
                                  stderr=_subprocess_mod.DEVNULL,
                                  cwd=os.path.dirname(exe))
        try:
            out, _ = p.communicate(b'uci\nquit\n', timeout=15)
        except Exception:
            D['_proc_stop'](p, grace=3)
            return False
        return p.returncode == 0 and b'uciok' in (out or b'')
    except Exception:
        return False


def _pikafish_detect():
    """找 Pikafish 引擎。

    Linux 用源码编译出的 pikafish（无扩展名），Windows 用 pikafish.exe。

    ⚠️ 2026-10-26 修：原来只在 `board/pikafish/` 下找，但线上引擎实际放在
    `/opt/mono/app/pikafish/`——路径差一级，`_PIKAFISH_EXE` 恒为 None，
    象棋全程降级到 `_xq_ai_move(depth=2)` 的弱 AI（用户反馈「AI 降智严重」）。
    现在按顺序找：board/pikafish → 项目根/pikafish。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [
        _PIKAFISH_DIR,                                    # board/pikafish
        os.path.join(os.path.dirname(here), 'pikafish'),   # 项目根/pikafish
    ]
    for d in dirs:
        for name in ('pikafish', 'pikafish.exe'):
            exe = os.path.join(d, name)
            if not os.path.isfile(exe):
                continue
            if _pikafish_ok(exe):
                return exe
    return None


_PIKAFISH_EXE = _pikafish_detect()


_PIKAFISH_AVAILABLE = bool(_PIKAFISH_EXE)


_PIKAFISH_ENGINES = {}


_PIKAFISH_FAILED = {}          # rid -> 失败时间戳


_PIKAFISH_FAIL_COOLDOWN = 180   # 秒；失败后这么久再给一次机会


_PIKAFISH_WARM_EV = {}


_XQP_ENGINE_LAST = {}


_PIKAFISH_LOCK = _threading_mod.Lock()


def _xq_xy_to_uci(x, y):
    return chr(97 + x) + str(9 - y)


def _xq_uci_to_xy(s):
    s = s.strip().lower()
    if len(s) < 2:
        return None
    x = ord(s[0]) - 97
    try:
        y = 9 - int(s[1:])
    except ValueError:
        return None
    if not (0 <= x < XQ_W and 0 <= y < XQ_H):
        return None
    return x, y


class _PikafishEngine(object):
    """每房间一个 Pikafish 子进程，UCI 协议通信。

    与 rapfi/katago 同款防护：独立读线程 + 队列 + 真超时，
    避免引擎在目标机器（含 Win7/老 CPU/缺运行库）上卡死导致无限等待。
    """

    def __init__(self, exe):
        self.exe = exe
        self.proc = None
        self.ready = False
        self.moves = []
        self.q = None
        self.reader = None
        self.movetime = _PIKAFISH_TIME_MS

    def start(self):
        self.proc = _subprocess_mod.Popen(
            [self.exe], stdin=_subprocess_mod.PIPE, stdout=_subprocess_mod.PIPE,
            stderr=_subprocess_mod.DEVNULL, cwd=os.path.dirname(self.exe))
        self.q = _queue_mod.Queue()
        self.reader = _threading_mod.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self._cmd('uci', stop='uciok', timeout=15)
        self._cmd('isready', stop='readyok', timeout=15)
        self._cmd('ucinewgame', expect=False)
        self.ready = True

    def _read_loop(self):
        try:
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    break
                self.q.put(raw.decode('utf-8', errors='replace').strip())
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

    def _cmd(self, line, stop=None, timeout=90, expect=True):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError('pikafish not running')
        try:
            while True:
                self.q.get_nowait()
        except _queue_mod.Empty:
            pass
        try:
            self.proc.stdin.write((line + '\n').encode('utf-8'))
            self.proc.stdin.flush()
        except Exception:
            raise RuntimeError('pikafish write failed')
        if not expect:
            return []
        out = []
        end = time.time() + max(timeout, 5)
        while time.time() < end:
            try:
                s = self.q.get(timeout=0.25)
            except _queue_mod.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError('pikafish closed')
                continue
            if s is None:
                raise RuntimeError('pikafish closed')
            out.append(s)
            if stop and s == stop:
                break
            if s.startswith('bestmove'):
                break
        else:
            raise RuntimeError('pikafish timeout')
        return out

    def set_position(self, uci_moves):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError('pikafish not running')
        line = 'position startpos moves ' + ' '.join(uci_moves) if uci_moves else 'position startpos'
        try:
            self.proc.stdin.write((line + '\n').encode('utf-8'))
            self.proc.stdin.flush()
        except Exception:
            raise RuntimeError('pikafish write failed')

    def set_fen(self, fen, uci_moves=()):
        """用 FEN 直接摆子（揭棋投影局面 / 残局对局用；Pikafish 支持任意合法 FEN）。

        uci_moves 非空时在同一 position 命令里追加走子历史（UCI 坐标）。
        """
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError('pikafish not running')
        mv = list(uci_moves or [])
        line = 'position fen ' + fen
        if mv:
            line += ' moves ' + ' '.join(str(m).lower() for m in mv)
        try:
            self.proc.stdin.write((line + '\n').encode('utf-8'))
            self.proc.stdin.flush()
        except Exception:
            raise RuntimeError('pikafish write failed')

    def genmove(self):
        out = self._cmd('go movetime %d' % self.movetime)
        for s in out:
            if s.startswith('bestmove'):
                parts = s.split()
                if len(parts) < 2 or parts[1] == '(none)':
                    return None
                uci = parts[1][:4].lower()
                mv = _xq_uci_to_xy(uci[:2])
                to = _xq_uci_to_xy(uci[2:4])
                if mv and to:
                    return (mv[0], mv[1], to[0], to[1])
                return None
        return None

    def play(self, x1, y1, x2, y2):
        self.moves.append(_xq_xy_to_uci(x1, y1) + _xq_xy_to_uci(x2, y2))

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
        self.ready = False


def _pikafish_get(rid):
    """取房间的中国象棋引擎。

    ⚠️ 2026-10-17：_PIKAFISH_FAILED 原来是永久 set，失败一次该房间就只能走
    纯 Python 兜底 AI，重开也没用。改成记时间戳 + 冷却，和围棋的
    _KATAGO_FAILED 一个路子。之前 NNUE 版本不匹配时全站象棋 AI 就是这么哑掉的。
    """
    if not _PIKAFISH_AVAILABLE:
        return None
    with _PIKAFISH_LOCK:
        _bad = _PIKAFISH_FAILED.get(rid)
        if _bad is not None:
            if time.time() - _bad < _PIKAFISH_FAIL_COOLDOWN:
                return None
            _PIKAFISH_FAILED.pop(rid, None)
    with _PIKAFISH_LOCK:
        eng = _PIKAFISH_ENGINES.get(rid)
        if eng is not None:
            eng.movetime = D['_ai_ms_for']('xq_rooms', rid, _PIKAFISH_TIME_MS, 'xq')
            return eng
        if len(_PIKAFISH_ENGINES) >= _PIKAFISH_MAX_ENGINES:
            return None
        eng = _PikafishEngine(_PIKAFISH_EXE)
        eng.movetime = D['_ai_ms_for']('xq_rooms', rid, _PIKAFISH_TIME_MS, 'xq')
        try:
            eng.start()
        except Exception:
            try:
                eng.close()
            except Exception:
                pass
            _PIKAFISH_FAILED[rid] = time.time()
            return None
        _PIKAFISH_ENGINES[rid] = eng
        eng.movetime = D['_ai_ms_for']('xq_rooms', rid, _PIKAFISH_TIME_MS, 'xq')
        return eng


def _pikafish_warm(rid):
    ev = _threading_mod.Event()
    with _PIKAFISH_LOCK:
        _PIKAFISH_WARM_EV[rid] = ev

    def _do():
        try:
            _pikafish_get(rid)
        except Exception:
            pass
        finally:
            ev.set()
    _threading_mod.Thread(target=_do, daemon=True).start()


def _pikafish_await(rid, timeout=15):
    ev = None
    with _PIKAFISH_LOCK:
        ev = _PIKAFISH_WARM_EV.get(rid)
    if ev is not None:
        ev.wait(timeout)
        if not ev.is_set():
            return None
    return _pikafish_get(rid)


def _pikafish_disable(rid):
    """象棋引擎出错：关掉并降级。"""
    with _PIKAFISH_LOCK:
        _PIKAFISH_WARM_EV.pop(rid, None)
        eng = _PIKAFISH_ENGINES.pop(rid, None)
        _PIKAFISH_FAILED[rid] = time.time()
    if eng is not None:
        try:
            eng.close()
        except Exception:
            pass


def _pikafish_close_room(rid):
    with _PIKAFISH_LOCK:
        _PIKAFISH_WARM_EV.pop(rid, None)
        eng = _PIKAFISH_ENGINES.pop(rid, None)
    if eng is not None:
        try:
            eng.close()
        except Exception:
            pass


def _pikafish_shutdown_all():
    with _PIKAFISH_LOCK:
        engs = list(_PIKAFISH_ENGINES.values())
        _PIKAFISH_ENGINES.clear()
    for eng in engs:
        try:
            eng.close()
        except Exception:
            pass


@_atexit_mod.register


def _pikafish_cleanup():
    _pikafish_shutdown_all()




def xq_socket_join(data):
    user = D['current_user']()
    if not user:
        return
    rid = data.get('rid')
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return
    room = D['get_db']().execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return
    is_owner = room['owner_id'] == user['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == user['id']
    if not (is_owner or is_guest) and room['status'] != 'waiting':
        return
    join_room('xq_%d' % rid, request.sid)


def _xq_payload(room):
    lm = None
    if room['last_move']:
        try:
            parts = room['last_move'].split(',')
            lm = [int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])]
        except Exception:
            lm = None
    board = room['board_state'] or _xq_init()
    to_move_red = room['turn_user_id'] == room['owner_id']
    in_check = bool(_xq_in_check(board, to_move_red)) if room['status'] == 'playing' else False
    return {
        "board": board,
        "status": room['status'],
        "winner_id": room['winner_id'],
        "turn_user_id": room['turn_user_id'],
        "last_move": lm,
        "uci_moves": room['uci_moves'] or '',
        "in_check": in_check,
    }


def _xq_emit(rid):
    room = D['get_db']().execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
    payload = _xq_payload(room)
    try:
        D['socketio'].emit('xq_move', payload, room='xq_%d' % rid)
    except Exception:
        pass
    D['_award_game_stats']('xq', rid)
    return dict(payload, ok=True)


def _xq_ai_play(rid, uci_hist, player_id):
    """后台线程：AI 执黑应手。

    玩家走子已在 HTTP 请求内落库并返回，这里只负责 AI 那一步，
    不阻塞前端点击（Pikafish 每步思考 5 秒，同步等会让界面冻结）。

    turn_user_id = -1 表示「AI 思考中」。无论成功失败，都必须把回合交回玩家，
    否则房间会永久卡在 -1。
    """
    def _give_back():
        """兜底：把回合交回玩家，避免房间永久卡死。"""
        try:
            with D['app'].app_context():
                db = D['get_db']()
                db.execute("UPDATE xq_rooms SET turn_user_id=? WHERE id=? AND turn_user_id=-1",
                           (player_id, rid))
                db.commit()
                _xq_emit(rid)
        except Exception:
            pass

    try:
        with D['app'].app_context():
            db = D['get_db']()
            room = db.execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
            if not room or room['status'] != 'playing' or room['turn_user_id'] != -1:
                return
            board = room['board_state']

        ai = None
        try:
            # eventlet 下必须走 tpool：这里最坏要等 15 秒，直接跑会卡住整个事件循环
            def _ask_engine():
                eng = _pikafish_await(rid)
                if eng is not None and eng.ready:
                    eng.set_position([u for u in uci_hist.split() if u])
                    return eng.genmove()
                return None
            ai = run_blocking(_ask_engine)
        except Exception:
            _pikafish_disable(rid)
        if ai is None:
            ai = _xq_ai_move(board, False)

        with D['app'].app_context():
            db = D['get_db']()
            room = db.execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
            if not room or room['status'] != 'playing' or room['turn_user_id'] != -1:
                return
            board = room['board_state']

            if ai is None:
                db.execute("UPDATE xq_rooms SET status='finished', winner_id=?, "
                           "turn_user_id=?, updated_at=? WHERE id=?",
                           (player_id, player_id, time.time(), rid))
                db.commit()
                _pikafish_close_room(rid)
                _xq_emit(rid)
                return

            ax1, ay1, ax2, ay2 = ai
            nb2, cap2, chk2, end2 = _xq_make_move(board, ax1, ay1, ax2, ay2)
            if nb2 is None:
                _give_back()
                return

            ai_uci = _xq_xy_to_uci(ax1, ay1) + _xq_xy_to_uci(ax2, ay2)
            hist2 = (uci_hist + ' ' + ai_uci).strip()
            lm = "%d,%d,%d,%d" % (ax1, ay1, ax2, ay2)

            if end2:
                db.execute("UPDATE xq_rooms SET board_state=?, status='finished', "
                           "winner_id=-1, last_move=?, uci_moves=?, turn_user_id=?, "
                           "updated_at=? WHERE id=?",
                           (nb2, lm, hist2, player_id, time.time(), rid))
                db.commit()
                _pikafish_close_room(rid)
                _xq_emit(rid)
                return

            db.execute("UPDATE xq_rooms SET board_state=?, last_move=?, uci_moves=?, "
                       "turn_user_id=?, updated_at=? WHERE id=?",
                       (nb2, lm, hist2, player_id, time.time(), rid))
            db.commit()
            if _xq_finish_repetition(rid, nb2, hist2):
                _xq_emit(rid)
                return
            _xq_emit(rid)
    except Exception:
        _give_back()


def _xq_lobby_broadcast():
    """通知象棋大厅所有在线客户端刷新房间列表。"""
    try:
        D['socketio'].emit('xq_lobby_update', {}, room='xq_lobby')
    except Exception:
        pass


def _xq_room_broadcast(rid, guest_id=None):
    """通知房间内用户状态已更新（如有人加入）。"""
    try:
        D['socketio'].emit('xq_room_update', {'rid': rid, 'guest_id': guest_id}, room='xq_%d' % rid)
    except Exception:
        pass






def xq_list():
    db = D['get_db']()
    me = D['current_user']()
    public_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM xq_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.is_public=1 AND r.status='waiting' AND r.mode=0 AND r.owner_id != ? "
        "ORDER BY r.id DESC LIMIT 20",
        (me['id'],)).fetchall()
    my_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM xq_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.owner_id=? OR r.guest_id=? ORDER BY r.id DESC LIMIT 20",
        (me['id'], me['id'])).fetchall()
    return render_template("xq_list.html",
                           public_rooms=public_rooms, my_rooms=my_rooms, user=me,
                           ai_lb=D['_ai_survival_leaderboard'](('xq','jq')),
                           my_ai=_my_ai_survival(('xq','jq')),
                           blb_rows=_board_wins_leaderboard('xq'))






def xq_join(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
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
        "UPDATE xq_rooms SET guest_id=?, status='playing', turn_user_id=?, updated_at=? WHERE id=?",
        (me['id'], room['owner_id'], time.time(), rid))
    db.commit()
    _xq_emit(rid)
    _xq_room_broadcast(rid, guest_id=me['id'])
    _xq_lobby_broadcast()
    return jsonify({"ok": True})






def xq_random_join():
    me = D['current_user']()
    db = D['get_db']()
    rows = db.execute(
        "SELECT id FROM xq_rooms WHERE is_public=1 AND status='waiting' AND mode=0 AND owner_id != ? "
        "AND (guest_id IS NULL) ORDER BY RANDOM() LIMIT 5",
        (me['id'],)).fetchall()
    for row in rows:
        r = db.execute(
            "UPDATE xq_rooms SET guest_id=?, status='playing', turn_user_id=owner_id, updated_at=? "
            "WHERE id=? AND status='waiting' AND guest_id IS NULL",
            (me['id'], time.time(), row['id']))
        if r.rowcount:
            db.commit()
            _xq_emit(row['id'])
            _xq_room_broadcast(row['id'], guest_id=me['id'])
            _xq_lobby_broadcast()
            return jsonify({"ok": True, "rid": row['id']})
    return jsonify({"ok": False, "msg": "暂无可加入的公开房间"})






def xq_room(rid):
    me = D['current_user']()
    room = D['get_db']().execute(
        "SELECT r.*, ou.username as owner_name, gu.username as guest_name "
        "FROM xq_rooms r "
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
    return render_template("xq_room.html", room=room, user=me,
                           is_owner=is_owner, is_guest=is_guest, is_super=is_super,
                           ai_builtin=(not _PIKAFISH_AVAILABLE)
                           or _engine_degraded(_PIKAFISH_FAILED, rid, _PIKAFISH_FAIL_COOLDOWN))






def xq_move(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
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
    play_as = ''
    player_red = is_owner
    board = room['board_state'] or _xq_init()

    if request.form.get("resign") == "1":
        if room['mode'] == 1:
            win_id = -1
        else:
            win_id = room['guest_id'] if player_red else room['owner_id']
        db.execute(
            "UPDATE xq_rooms SET status='finished', winner_id=?, updated_at=? WHERE id=?",
            (win_id, time.time(), rid))
        db.commit()
        _pikafish_close_room(rid)
        return _xq_emit(rid)

    if room['turn_user_id'] != me['id']:
        return jsonify({"ok": False, "msg": "还没轮到你"})

    try:
        x1 = int(request.form.get("x1", "-1"))
        y1 = int(request.form.get("y1", "-1"))
        x2 = int(request.form.get("x2", "-1"))
        y2 = int(request.form.get("y2", "-1"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数错误"})
    if not (0 <= x1 < XQ_W and 0 <= y1 < XQ_H and 0 <= x2 < XQ_W and 0 <= y2 < XQ_H):
        return jsonify({"ok": False, "msg": "坐标越界"})
    p = _xq_at(board, x1, y1)
    if p == '.' or p.isupper() != player_red:
        return jsonify({"ok": False, "msg": "请选择己方棋子"})
    nb, captured, check, end = _xq_make_move(board, x1, y1, x2, y2)
    if nb is None:
        return jsonify({"ok": False, "msg": _xq_why_illegal(board, x1, y1, x2, y2, player_red)})

    if end:
        if is_super and play_as == 'black':
            win_id = room['owner_id']
        elif is_super and play_as == 'red':
            win_id = room['guest_id'] or room['owner_id']
        else:
            win_id = me['id']
        db.execute(
            "UPDATE xq_rooms SET board_state=?, status='finished', winner_id=?, last_move=?, updated_at=? WHERE id=?",
            (nb, win_id, "%d,%d,%d,%d" % (x1, y1, x2, y2), time.time(), rid))
        db.commit()
        _pikafish_close_room(rid)
        return _xq_emit(rid)

    if room['mode'] == 1 and is_owner:
        player_uci = _xq_xy_to_uci(x1, y1) + _xq_xy_to_uci(x2, y2)
        uci_hist = ((room['uci_moves'] or '') + ' ' + player_uci).strip()
        db.execute(
            "UPDATE xq_rooms SET board_state=?, last_move=?, uci_moves=?, "
            "turn_user_id=-1, updated_at=? WHERE id=?",
            (nb, "%d,%d,%d,%d" % (x1, y1, x2, y2), uci_hist, time.time(), rid))
        db.commit()
        _threading_mod.Thread(target=_xq_ai_play, args=(rid, uci_hist, me['id']),
                              daemon=True).start()
        return _xq_emit(rid)

    uci = _xq_xy_to_uci(x1, y1) + _xq_xy_to_uci(x2, y2)
    next_turn = room['guest_id'] if is_owner else room['owner_id']
    db.execute(
        "UPDATE xq_rooms SET board_state=?, last_move=?, uci_moves=?, turn_user_id=?, updated_at=? WHERE id=?",
        (nb, "%d,%d,%d,%d" % (x1, y1, x2, y2), (room['uci_moves'] or '') + ' ' + uci,
         next_turn, time.time(), rid))
    db.commit()
    if _xq_finish_repetition(rid, nb, (room['uci_moves'] or '') + ' ' + uci):
        return _xq_emit(rid)
    return _xq_emit(rid)






def xq_state(rid):
    me = D['current_user']()
    room = D['get_db']().execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"}), 404
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    # 观战：任何人可读
    return jsonify(dict(_xq_payload(room), ok=True))






def xq_restart(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id']:
        return jsonify({"ok": False, "msg": "仅房主可重开"})
    db.execute(
        "UPDATE xq_rooms SET board_state=?, status='playing', winner_id=NULL, last_move=NULL, "
        "uci_moves='', turn_user_id=owner_id, updated_at=? WHERE id=?",
        (_xq_init(), time.time(), rid))
    db.commit()
    _pikafish_close_room(rid)
    if room['mode'] == 1:
        _pikafish_warm(rid)
    return jsonify({"ok": True})






def xq_puzzles_list():
    me = D['current_user']()
    db = D['get_db']()
    sys_rows = db.execute(
        "SELECT * FROM xq_puzzles WHERE status='approved' AND author_id=0 "
        "ORDER BY diff ASC, id ASC LIMIT 300").fetchall()
    user_rows = db.execute(
        "SELECT * FROM xq_puzzles WHERE status='approved' AND author_id!=0 "
        "ORDER BY id DESC LIMIT 200").fetchall()
    mine_rows = db.execute(
        "SELECT * FROM xq_puzzles WHERE author_id=? ORDER BY id DESC LIMIT 50",
        (me['id'],)).fetchall()
    done_ids = [r['puzzle_id'] for r in db.execute(
        "SELECT puzzle_id FROM xq_puzzle_done WHERE user_id=?", (me['id'],)).fetchall()]
    return render_template("xq_puzzles.html", user=me,
                           sys_rows=sys_rows, user_rows=user_rows, mine_rows=mine_rows,
                           done_ids=set(done_ids),
                           sys_done=sum(1 for r in sys_rows if r['id'] in done_ids),
                           total_sys=len(sys_rows))






def xq_puzzle_new_page():
    builtin = D['get_db']().execute(
        "SELECT id, title, fen, diff FROM xq_puzzles WHERE status='approved' AND author_id=0 "
        "ORDER BY diff ASC, id ASC LIMIT 200").fetchall()
    return render_template("xq_puzzle_new.html", user=D['current_user'](), builtin=builtin)






def xq_puzzle_create():
    me = D['current_user']()
    title = (request.form.get("title") or "").strip()[:40]
    intro = (request.form.get("intro") or "").strip()[:300]
    fen = (request.form.get("fen") or "").strip()
    if not title:
        return jsonify({"ok": False, "msg": "请填写残局名称"})
    board = _xq_fen_to_board(fen)
    if board is None:
        return jsonify({"ok": False, "msg": "FEN 不合法：需 10 行 9 列、双方各一枚将帅、且为红方先行（w/r）"})
    ok, verr = _xq_pz_validate_setup(board)
    if not ok:
        return jsonify({"ok": False, "msg": verr})
    if not _xq_legal_moves(board, True):
        return jsonify({"ok": False, "msg": "该局面红方已无子可动，不能作为残局"})
    if not _xq_legal_moves(board, False):
        return jsonify({"ok": False, "msg": "该局面黑方已无子可动，太简单了，换一个吧"})
    db = D['get_db']()
    hint = None
    try:
        hint = _xq_pz_engine_move(fen, [])
    except Exception:
        hint = None
    best_uci = _xq_xy_to_uci(*hint[:2]) + _xq_xy_to_uci(*hint[2:]) if hint else ''
    majors = sum(1 for c in board if c in 'RNBCP')
    blacks = sum(1 for c in board if c in 'rnbcp')
    diff = 1
    if majors - blacks >= 3:
        diff = 1
    elif majors > blacks:
        diff = 2
    elif majors == blacks:
        diff = 3
    elif majors < blacks:
        diff = 5 if blacks - majors >= 2 else 4
    diff = min(5, max(1, diff))
    cur = db.execute(
        "INSERT INTO xq_puzzles (title, fen, source, diff, author_id, author_name, intro, "
        "best_uci, status, created_at) VALUES (?,?,?,?,?,?,?,?,'approved',?)",
        (title, fen, '用户创作', diff, me['id'], me['username'], intro, best_uci, time.time()))
    db.commit()
    return jsonify({"ok": True, "pid": cur.lastrowid, "diff": diff})






def xq_puzzle_page(pid):
    me = D['current_user']()
    pz = D['get_db']().execute(
        "SELECT * FROM xq_puzzles WHERE id=?", (pid,)).fetchone()
    if not pz:
        abort(404)
    if pz['status'] != 'approved' and pz['author_id'] != me['id'] and not me['is_super_admin']:
        abort(404)
    board = _xq_fen_to_board(pz['fen'])
    if board is None:
        abort(404)
    db = D['get_db']()
    done = db.execute(
        "SELECT 1 FROM xq_puzzle_done WHERE user_id=? AND puzzle_id=?",
        (me['id'], pid)).fetchone() is not None
    if pz['author_id'] != 0 and pz['author_id'] != me['id'] and not me['is_super_admin']:
        done = False
    return render_template("xq_puzzle_room.html", user=me, pz=pz,
                           start_board=board, done=done,
                           ai_builtin=not _PIKAFISH_AVAILABLE)






def xq_puzzle_move(pid):
    me = D['current_user']()
    db = D['get_db']()
    pz = db.execute("SELECT * FROM xq_puzzles WHERE id=?", (pid,)).fetchone()
    if not pz or pz['status'] != 'approved':
        return jsonify({"ok": False, "msg": "残局不存在或未公开"})
    board0 = _xq_fen_to_board(pz['fen'])
    if board0 is None:
        return jsonify({"ok": False, "msg": "残局数据异常"})
    hist = (request.form.get("hist") or "").strip()
    moves = hist.split()
    if len(moves) % 2 != 0:
        return jsonify({"ok": False, "msg": "还没轮到你"})
    for uci in moves:
        if len(uci) < 4:
            return jsonify({"ok": False, "msg": "走子历史异常"})
        f = _xq_uci_to_xy(uci[:2]); t = _xq_uci_to_xy(uci[2:4])
        if f is None or t is None:
            return jsonify({"ok": False, "msg": "走子历史异常"})
        nb, _, _, _ = _xq_make_move(board0, f[0], f[1], t[0], t[1])
        if nb is None:
            return jsonify({"ok": False, "msg": "走子历史异常"})
        board0 = nb
    try:
        x1 = int(request.form.get("x1", "-1")); y1 = int(request.form.get("y1", "-1"))
        x2 = int(request.form.get("x2", "-1")); y2 = int(request.form.get("y2", "-1"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数错误"})
    if not (0 <= x1 < XQ_W and 0 <= y1 < XQ_H and 0 <= x2 < XQ_W and 0 <= y2 < XQ_H):
        return jsonify({"ok": False, "msg": "坐标越界"})
    p = _xq_at(board0, x1, y1)
    if p == '.' or not p.isupper():
        return jsonify({"ok": False, "msg": "请选择红方棋子"})
    nb, captured, check, end = _xq_make_move(board0, x1, y1, x2, y2)
    if nb is None:
        return jsonify({"ok": False, "msg": _xq_why_illegal(board0, x1, y1, x2, y2, True)})
    last_move = [x1, y1, x2, y2]
    player_uci = _xq_xy_to_uci(x1, y1) + _xq_xy_to_uci(x2, y2)
    uci_full = ((hist + ' ' + player_uci).strip()) if hist else player_uci

    def _record_play():
        try:
            db.execute("UPDATE xq_puzzles SET plays=plays+1 WHERE id=?", (pid,))
            db.commit()
        except Exception:
            pass

    if len(moves) == 0:
        _record_play()

    kind, loser_red = _xq_pz_rep_info(pz['fen'], uci_full)
    if kind is not None:
        if kind == 'perpetual_check':
            res = 'lose' if loser_red else 'win'
        else:
            res = 'draw'
        if res == 'win':
            _xq_pz_mark_solved(me['id'], pid)
        _pikafish_close_room(_xq_pz_engine_key(pid))
        return jsonify(_xq_pz_payload(nb, uci_full, 'finished', res, last_move,
                                      '红方长将，判负' if (kind == 'perpetual_check' and loser_red)
                                      else '红方取胜！' if res == 'win'
                                      else '黑方长将，红胜！' if (kind == 'perpetual_check' and not loser_red)
                                      else '三次重复局面，和棋'))
    if end:
        _xq_pz_mark_solved(me['id'], pid)
        _pikafish_close_room(_xq_pz_engine_key(pid))
        return jsonify(_xq_pz_payload(nb, uci_full, 'finished', 'win', last_move, '🎉 将死黑将，残局破解成功！'))

    ai = None
    try:
        _pz_key = _xq_pz_engine_key(pid)
        eng = _pikafish_get(_pz_key)
        if eng is not None:
            eng.set_fen(pz['fen'], uci_full.split())
            ai = eng.genmove()
            if ai is not None:
                _XQP_ENGINE_LAST[_pz_key] = time.time()
    except Exception:
        _pikafish_disable(_xq_pz_engine_key(pid))
    if ai is None:
        try:
            ai = _xq_ai_move(nb, False)
        except Exception:
            ai = None
    if ai is None:
        return jsonify({"ok": False, "msg": "引擎无着可走，请刷新后重试"})
    ax1, ay1, ax2, ay2 = ai
    nb2, cap2, chk2, end2 = _xq_make_move(nb, ax1, ay1, ax2, ay2)
    if nb2 is None:
        return jsonify({"ok": False, "msg": "引擎着法异常，请重试"})
    ai_uci = _xq_xy_to_uci(ax1, ay1) + _xq_xy_to_uci(ax2, ay2)
    uci_full2 = (uci_full + ' ' + ai_uci).strip()
    last_move2 = [ax1, ay1, ax2, ay2]
    kind2, loser_red2 = _xq_pz_rep_info(pz['fen'], uci_full2)
    if kind2 is not None:
        if kind2 == 'perpetual_check':
            res2 = 'win' if not loser_red2 else 'lose'
        else:
            res2 = 'draw'
        if res2 == 'win':
            _xq_pz_mark_solved(me['id'], pid)
        _pikafish_close_room(_xq_pz_engine_key(pid))
        return jsonify(_xq_pz_payload(nb2, uci_full2, 'finished', res2, last_move2,
                                      '红方取胜！' if res2 == 'win'
                                      else '三次重复局面，和棋' if res2 == 'draw'
                                      else '对局结束'))
    if end2:
        _pikafish_close_room(_xq_pz_engine_key(pid))
        return jsonify(_xq_pz_payload(nb2, uci_full2, 'finished', 'lose', last_move2, '💀 你被将死，残局挑战失败'))
    return jsonify(_xq_pz_payload(nb2, uci_full2, 'playing', '', last_move2,
                                  '', pz['best_uci'] or ''))






def xq_puzzle_hint(pid):
    pz = D['get_db']().execute("SELECT * FROM xq_puzzles WHERE id=?", (pid,)).fetchone()
    if not pz or pz['status'] != 'approved':
        return jsonify({"ok": False, "msg": "残局不存在"})
    hist = (request.form.get("hist") or "").strip()
    if len(hist.split()) % 2 != 0:
        return jsonify({"ok": False, "msg": "还没轮到你"})
    mv = None
    try:
        mv = _xq_pz_engine_move(pz['fen'], hist.split())
    except Exception:
        mv = None
    if mv is None:
        if not hist and pz['best_uci'] and len(pz['best_uci']) == 4:
            f = _xq_uci_to_xy(pz['best_uci'][:2]); t = _xq_uci_to_xy(pz['best_uci'][2:4])
            if f and t:
                mv = (f[0], f[1], t[0], t[1])
    if mv is None:
        return jsonify({"ok": False, "msg": "引擎不可用，暂时无法提示"})
    return jsonify({"ok": True, "x1": mv[0], "y1": mv[1], "x2": mv[2], "y2": mv[3]})






def xq_puzzle_giveup(pid):
    _pikafish_close_room(_xq_pz_engine_key(pid))
    return jsonify({"ok": True})






def xq_puzzle_delete(pid):
    me = D['current_user']()
    db = D['get_db']()
    pz = db.execute("SELECT * FROM xq_puzzles WHERE id=?", (pid,)).fetchone()
    if not pz:
        return jsonify({"ok": False, "msg": "残局不存在"})
    if pz['author_id'] != me['id'] and not me['is_super_admin']:
        return jsonify({"ok": False, "msg": "仅作者或超管可删除"})
    if pz['author_id'] == 0 and not me['is_super_admin']:
        return jsonify({"ok": False, "msg": "内置残局不可删除"})
    db.execute("DELETE FROM xq_puzzles WHERE id=?", (pid,))
    db.execute("DELETE FROM xq_puzzle_done WHERE puzzle_id=?", (pid,))
    db.commit()
    _pikafish_close_room(_xq_pz_engine_key(pid))
    return jsonify({"ok": True})



def _xq_fen_to_board(fen):
    """解析中国象棋 FEN 首段 → 内部 90 字符盘面；失败返回 None。

    内部棋子编码与 FEN 一致：红 RNBAKCP（大写，帅仕相马车炮兵），黑 rnbakcp。
    残局一律要求红方先行（FEN 第 2 段为 w / r）。
    """
    try:
        toks = (fen or '').strip().split()
        part = toks[0]
    except IndexError:
        return None
    if not part:
        return None
    rows = part.split('/')
    if len(rows) != 10:
        return None
    out = []
    for row in rows:
        line = ''
        for ch in row:
            if ch.isdigit():
                n = int(ch)
                if n < 1 or n > 9:
                    return None
                line += '.' * n
            elif ch in 'RNBAKCP' or ch in 'rnbakcp':
                line += ch
            else:
                return None
        if len(line) != 9:
            return None
        out.append(line)
    board = ''.join(out)
    if board.count('K') != 1 or board.count('k') != 1:
        return None
    if len(toks) < 2 or toks[1].lower() not in ('w', 'r'):
        return None
    return board


def _xq_pz_validate_setup(board):
    """残局摆位合法性校验（棋规层面）。

    规则：帅/将必须在己方九宫格内；仕/士只能在九宫斜线交叉点
    （九宫四角 + 中心共 5 点）；相/象不得过河（必须在己方半场）。
    返回 (ok, err_msg)。
    """
    red_advisor = ((3, 7), (5, 7), (4, 8), (3, 9), (5, 9))
    black_advisor = ((3, 0), (5, 0), (4, 1), (3, 2), (5, 2))
    for i, c in enumerate(board):
        if c == '.':
            continue
        x, y = i % 9, i // 9
        row = y + 1
        col = x + 1
        if c == 'K':
            if not (3 <= x <= 5 and 7 <= y <= 9):
                return False, '红『帅』必须在下方九宫格内（虚线框），当前在第 %d 行第 %d 列' % (row, col)
        elif c == 'k':
            if not (3 <= x <= 5 and 0 <= y <= 2):
                return False, '黑『将』必须在上方九宫格内（虚线框），当前在第 %d 行第 %d 列' % (row, col)
        elif c == 'A':
            if (x, y) not in red_advisor:
                return False, '红『仕』只能摆在九宫斜线交叉点（四个角与中心），当前在第 %d 行第 %d 列' % (row, col)
        elif c == 'a':
            if (x, y) not in black_advisor:
                return False, '黑『士』只能摆在九宫斜线交叉点（四个角与中心），当前在第 %d 行第 %d 列' % (row, col)
        elif c == 'B':
            if y < 5:
                return False, '红『相』不能过河（须在红方半场），当前在第 %d 行第 %d 列' % (row, col)
        elif c == 'b':
            if y > 4:
                return False, '黑『象』不能过河（须在黑方半场），当前在第 %d 行第 %d 列' % (row, col)
    return True, ''


def _xq_pz_rep_info(fen, uci_moves):
    """残局场景重复/长将判定。返回 ('perpetual_check', loser_red) / ('draw', None) / (None, None)。

    入参为 FEN 字符串，内部先解析成盘面再重放。
    """
    board0 = _xq_fen_to_board(fen)
    if board0 is None:
        return None, None
    moves = (uci_moves or '').split()
    if len(moves) < 3:
        return None, None
    boards, checks = _xq_pz_replay(board0, uci_moves)
    if not boards:
        return None, None
    final = boards[-1]
    target_side = (len(boards) - 1) % 2 == 0
    cnt = sum(1 for i in range(len(boards)) if (i % 2 == 0) == target_side and boards[i] == final)
    if cnt < 3:
        return None, None
    n = len(checks)
    last_mover_red = (n - 1) % 2 == 0
    seq = [checks[n - 1 - k] for k in range(3) if (n - 1 - k) >= 0]
    if len(seq) >= 3 and all(seq):
        return 'perpetual_check', last_mover_red
    return 'draw', None


def _xq_pz_engine_key(pid):
    return 'xqp_%d' % pid


def _xq_pz_mark_solved(uid, pid):
    """首次通关：写进度表并 solves+1；重复通关不重复计数。"""
    try:
        db = D['get_db']()
        cur = db.execute(
            "INSERT OR IGNORE INTO xq_puzzle_done (user_id, puzzle_id, solved_at) VALUES (?,?,?)",
            (uid, pid, time.time()))
        if cur.rowcount:
            db.execute("UPDATE xq_puzzles SET solves=solves+1 WHERE id=?", (pid,))
            db.commit()
    except Exception:
        pass


def _xq_pz_payload(board, uci_moves, status, result, last_move, msg='', hint_uci=''):
    to_move_red = (len((uci_moves or '').split()) % 2) == 0
    in_check = bool(_xq_in_check(board, to_move_red)) if status == 'playing' else False
    return {
        "ok": True,
        "board": board,
        "uci_moves": uci_moves or '',
        "status": status,
        "result": result,
        "red_turn": to_move_red,
        "in_check": in_check,
        "last_move": last_move,
        "msg": msg,
        "hint_uci": hint_uci,
    }


def _xq_pz_engine_move(fen, uci_moves):
    """让 Pikafish 在当前局面走一步（当前轮走方）。返回 (x1,y1,x2,y2) 或 None。"""
    if not _PIKAFISH_AVAILABLE:
        return None
    try:
        eng = _PikafishEngine(_PIKAFISH_EXE)
        eng.start()
        try:
            eng.set_fen(fen, uci_moves)
            mv = eng.genmove()
            return mv
        finally:
            eng.close()
    except Exception:
        return None



def _xq_pz_replay(board0, uci_moves):
    """从残局初始盘面重放走子，返回 (boards, checks)；任一走子非法返回 (None, None)。"""
    moves = (uci_moves or '').split()
    boards = [board0]
    checks = []
    b = board0
    for uci in moves:
        if len(uci) < 4:
            return None, None
        f = _xq_uci_to_xy(uci[:2])
        t = _xq_uci_to_xy(uci[2:4])
        if f is None or t is None:
            return None, None
        p = _xq_at(b, f[0], f[1])
        if p == '.':
            return None, None
        nb, _, _, _ = _xq_make_move(b, f[0], f[1], t[0], t[1])
        if nb is None:
            return None, None
        checks.append(_xq_in_check(nb, not p.isupper()))
        boards.append(nb)
        b = nb
    return boards, checks







def xq_legal(rid):
    """返回当前轮到的一方所有合法着法 [[x1,y1,x2,y2],...]，供前端高亮提示。

    2026-11-01 修：拆包时这段是坏的 —— `current_user()` / `get_db()` 没走
    `D[...]`（NameError），`board.xq._xq_init()` / `board.xq._xq_legal_moves(...)`
    更是把局部变量当模块用（UnboundLocalError）。等于这个接口从拆包起就没通过。
    """
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM xq_rooms WHERE id=?", (rid,)).fetchone()
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
    board = room['board_state'] or _xq_init()
    red = is_owner
    moves = _xq_legal_moves(board, red)
    return jsonify({"ok": True, "moves": [list(m) for m in moves]})


def _xq_pz_diff_from_mate(mate_plies):
    """按引擎自对弈取胜步数估算难度 1~5。"""
    if mate_plies <= 3:
        return 1
    if mate_plies <= 6:
        return 2
    if mate_plies <= 10:
        return 3
    if mate_plies <= 16:
        return 4
    return 5


