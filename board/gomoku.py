# -*- coding: utf-8 -*-
"""board/gomoku.py — 五子棋（RAPFI 引擎 + 房间对战）。

从 D['app'].py 搬迁。helper 一律走 deps.D 注入（不 import D['app']）。
"""
import json
import os
import re
import time
import atexit as _atexit_mod
import queue as _queue_mod
import subprocess as _subprocess_mod
import threading as _threading_mod

from flask import jsonify, render_template, request

from .deps import D
from blocking import run_blocking
from .engines import EnginePool
from games.mini import _board_wins_leaderboard, _my_ai_survival

GOMOKU_N = 15


GOMOKU_EMPTY_BOARD = "." * (GOMOKU_N * GOMOKU_N)


# ⚠️ 2026-11-01 修：原来指向 board/rapfi/engine —— 拆包后这个目录不存在，
# 引擎实际在项目根 rapfi/engine（/opt/mono/app/rapfi/engine）。
# 跟 reversi 的 egaroucid 是同一个坑（那边 10-26 修过），导致五子棋一直用降级 AI。
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RAPFI_DIR = os.path.join(_APP_ROOT, 'rapfi', 'engine')


_RAPFI_CANDIDATES = (
    'pbrain-rapfi-linux-clang-avx2',
    'pbrain-rapfi-linux-clang-avxvnni',
    'pbrain-rapfi-linux-clang-avx512',
    'pbrain-rapfi-linux-clang-avx512vnni',
    'pbrain-rapfi-linux-clang-sse',
    'pbrain-rapfi-macos-apple-silicon',
    'pbrain-rapfi-windows-avx2.exe',
    'pbrain-rapfi-windows-sse.exe')








_RAPFI_MAX_ENGINES = 6 if D['_LOW_MEM'] else 24


_RAPFI_TURN_MS = 5000


_RAPFI_MATCH_MS = 900000


_RAPFI_MOVE_TIMEOUT = 12.0


class _RapfiEngine:
    """单个五子棋房间对应的 Rapfi 引擎子进程。"""

    def __init__(self, rid):
        self.rid = rid
        self.lock = _threading_mod.Lock()
        self.p = None
        self.q = None
        self.reader = None
        self.broken = False
        self.need_board = True

    def _spawn(self):
        self.p = _subprocess_mod.Popen(
            [_RAPFI_EXE],
            stdin=_subprocess_mod.PIPE, stdout=_subprocess_mod.PIPE,
            stderr=_subprocess_mod.DEVNULL, text=True, bufsize=1,
            encoding='utf-8', errors='replace', cwd=_RAPFI_DIR)
        self.q = _queue_mod.Queue()
        self.reader = _threading_mod.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        lines = self._send_raw('START 15', timeout=20)
        if not lines or lines[-1] != 'OK':
            raise RuntimeError('engine START failed: %r' % (lines[-3:] if lines else None))
        self._send_raw('INFO TIMEOUT_TURN %d'
                       % D['_ai_ms_for']('gomoku_rooms', self.rid, _RAPFI_TURN_MS, 'gomoku'),
                       timeout=2, expect_reply=False)
        self._send_raw('INFO TIMEOUT_MATCH %d' % _RAPFI_MATCH_MS, timeout=2, expect_reply=False)
        self.need_board = True

    def _read_loop(self):
        try:
            for line in self.p.stdout:
                if line:
                    self.q.put(line.strip())
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

    def _send_raw(self, cmd, timeout=9.0, expect_reply=True):
        while True:
            try:
                self.q.get_nowait()
            except _queue_mod.Empty:
                break
        try:
            self.p.stdin.write(cmd + '\n')
            self.p.stdin.flush()
        except Exception:
            return None
        if not expect_reply:
            return []
        lines = []
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.q.get(timeout=0.25)
            except _queue_mod.Empty:
                continue
            if line is None:
                return None
            if line:
                lines.append(line)
            if line in ('OK', 'ERROR', 'bye') or re.match(r'^[=\s]*\d+,\d+\s*$', line):
                break
        else:
            return None
        return lines

    @staticmethod
    def _board_cmd(board):
        parts = ['BOARD']
        for y in range(GOMOKU_N):
            for x in range(GOMOKU_N):
                v = _gomoku_at(board, x, y)
                if v != '.':
                    parts.append('%d,%d,%s' % (x, y, v))
        parts.append('DONE')
        return '\n'.join(parts)

    def ask_move(self, board, last_x=None, last_y=None):
        """请求引擎落子。返回 (x, y)；失败返回 None。"""
        with self.lock:
            if self.broken or self.p is None or self.p.poll() is not None:
                try:
                    self._spawn()
                    self.broken = False
                except Exception:
                    self.broken = True
                    return None
            if self.need_board:
                lines = self._send_raw(self._board_cmd(board), timeout=_RAPFI_MOVE_TIMEOUT)
            elif last_x is not None and last_y is not None:
                lines = self._send_raw('TURN %d,%d' % (last_x, last_y), timeout=_RAPFI_MOVE_TIMEOUT)
            else:
                lines = self._send_raw(self._board_cmd(board), timeout=_RAPFI_MOVE_TIMEOUT)
            if not lines:
                self._kill()
                self.broken = True
                self.need_board = True
                return None
            for line in reversed(lines):
                m = re.match(r'^[=\s]*(\d+),(\d+)\s*$', line)
                if m:
                    self.need_board = False
                    return int(m.group(1)), int(m.group(2))
            self._kill()
            self.broken = True
            self.need_board = True
            return None

    def _kill(self):
        if self.p is not None:
            try:
                if self.p.poll() is None:
                    try:
                        self.p.stdin.write('END\n')
                        self.p.stdin.flush()
                    except Exception:
                        pass
                    D['_proc_stop'](self.p, grace=3)
            except Exception:
                pass
        self.p = None
        self.q = None

    def close(self):
        with self.lock:
            self._kill()


# 引擎池：懒创建 / 容量上限 / 失败冷却 / 统一回收（board/engines.py）
_RAPFI_POOL = EnginePool(
    "rapfi",
    factory=lambda rid: _RapfiEngine(rid),
    max_engines=_RAPFI_MAX_ENGINES,
    cooldown=180,
    logger=lambda m: None,
).register_atexit()


def _rapfi_get_engine(rid):
    return _RAPFI_POOL.get(rid)


def _rapfi_ensure_ready(rid):
    """确保引擎进程已就绪（房间创建时预热）。"""
    if not _RAPFI_AVAILABLE:
        return False
    eng = _rapfi_get_engine(rid)
    if eng is None:
        return False
    with eng.lock:
        if eng.p is None or eng.p.poll() is not None:
            try:
                eng._spawn()
                eng.broken = False
                return True
            except Exception:
                eng.broken = True
                _RAPFI_POOL.mark_failed(rid)   # 进冷却，别每个请求都重 fork 子进程
                return False
    return True


def _rapfi_ai_move(rid, board, last_x, last_y):
    """Rapfi 落子；不可用时返回 None（调用方降级内置 AI）。"""
    if not _RAPFI_AVAILABLE:
        return None
    eng = _rapfi_get_engine(rid)
    if eng is None:
        return None
    return eng.ask_move(board, last_x, last_y)


def _rapfi_close_room(rid):
    _RAPFI_POOL.close(rid)


def _rapfi_cleanup():
    _RAPFI_POOL.cleanup()


# ── 复盘：记录着法历史（058）──
_GOMOKU_HIST_READY = [False]


def _gomoku_ensure_hist(db):
    """懒建 uci_moves 列（复盘用）。列已存在时 ALTER 会抛异常，吞掉即可。"""
    if _GOMOKU_HIST_READY[0]:
        return
    try:
        db.execute("ALTER TABLE gomoku_rooms ADD COLUMN uci_moves TEXT NOT NULL DEFAULT ''")
        db.commit()
    except Exception:
        pass
    _GOMOKU_HIST_READY[0] = True


def _gomoku_hist_append(room, x, y):
    """把这一手追加进 uci_moves（'x,y x,y ...'）。"""
    try:
        prev = room['uci_moves'] or ''
    except (IndexError, KeyError):
        prev = ''
    return (prev + ' %d,%d' % (x, y)).strip()


def _gomoku_parse_board(s):
    return s if s else GOMOKU_EMPTY_BOARD


def _gomoku_at(board, x, y):
    return board[y * GOMOKU_N + x]


def _gomoku_set(board, x, y, v):
    arr = list(board)
    arr[y * GOMOKU_N + x] = v
    return "".join(arr)


def _gomoku_check_win(board, x, y, player):
    """返回是否五连，以及五个坐标列表 [(x,y), ...]"""
    directions = [(1, 0), (0, 1), (1, 1), (1, -1)]
    for dx, dy in directions:
        line = [(x, y)]
        for k in (1, -1):
            nx, ny = x + dx * k, y + dy * k
            while 0 <= nx < GOMOKU_N and 0 <= ny < GOMOKU_N and _gomoku_at(board, nx, ny) == player:
                line.append((nx, ny))
                nx += dx * k; ny += dy * k
        if len(line) >= 5:
            line.sort()
            return True, json.dumps(line[:5])
    return False, None


def _gomoku_line_score(count, open_ends):
    """连子评分：五连最高，活四/冲四/活三等依次递减"""
    if count >= 5:
        return 1000000
    if count == 4:
        return 100000 if open_ends == 2 else 50000
    if count == 3:
        return 20000 if open_ends == 2 else 5000
    if count == 2:
        return 2000 if open_ends == 2 else 400
    if count == 1:
        return 60 if open_ends == 2 else 10
    return 0


def _gomoku_point_score(board, x, y, player):
    """在 (x,y) 落 player 的四个方向连子得分之和"""
    total = 0
    for dx, dy in [(1, 0), (0, 1), (1, 1), (1, -1)]:
        count = 1
        open_ends = 0
        nx, ny = x + dx, y + dy
        while 0 <= nx < GOMOKU_N and 0 <= ny < GOMOKU_N and _gomoku_at(board, nx, ny) == player:
            count += 1
            nx += dx
            ny += dy
        if 0 <= nx < GOMOKU_N and 0 <= ny < GOMOKU_N and _gomoku_at(board, nx, ny) == '.':
            open_ends += 1
        nx, ny = x - dx, y - dy
        while 0 <= nx < GOMOKU_N and 0 <= ny < GOMOKU_N and _gomoku_at(board, nx, ny) == player:
            count += 1
            nx -= dx
            ny -= dy
        if 0 <= nx < GOMOKU_N and 0 <= ny < GOMOKU_N and _gomoku_at(board, nx, ny) == '.':
            open_ends += 1
        total += _gomoku_line_score(count, open_ends)
    return total


def _gomoku_ai_move(board):
    """评分制 AI：攻防加权（攻击×1.03 + 防守×1.0），识别活四/冲四/活三等局面"""
    empties = [(x, y) for y in range(GOMOKU_N) for x in range(GOMOKU_N) if _gomoku_at(board, x, y) == '.']
    if not empties:
        return None
    if len(empties) == GOMOKU_N * GOMOKU_N:
        return GOMOKU_N // 2, GOMOKU_N // 2
    if len(empties) == GOMOKU_N * GOMOKU_N - 1:
        cx, cy = GOMOKU_N // 2, GOMOKU_N // 2
        if _gomoku_at(board, cx, cy) == '.':
            return cx, cy
        return cx + 1, cy
    best = None
    best_score = -1
    cands = set()
    for y in range(GOMOKU_N):
        for x in range(GOMOKU_N):
            if _gomoku_at(board, x, y) != '.':
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < GOMOKU_N and 0 <= ny < GOMOKU_N and _gomoku_at(board, nx, ny) == '.':
                            cands.add((nx, ny))
    for (x, y) in cands or empties:
        attack = _gomoku_point_score(board, x, y, GOMOKU_P2)
        defense = _gomoku_point_score(board, x, y, GOMOKU_P1)
        score = attack * 1.03 + defense
        if attack >= 1000000:
            return x, y
        if defense >= 1000000:
            score += 800000
        if score > best_score:
            best_score = score
            best = (x, y)
    if best:
        return best
    cx, cy = GOMOKU_N // 2, GOMOKU_N // 2
    empties.sort(key=lambda p: abs(p[0] - cx) + abs(p[1] - cy))
    return empties[0]


def _gmk_lobby_broadcast():
    """通知五子棋大厅所有在线客户端刷新房间列表。"""
    try:
        D['socketio'].emit('gmk_lobby_update', {}, room='gmk_lobby')
    except Exception:
        pass


def _gmk_room_broadcast(rid, guest_id=None):
    """通知房间内用户状态已更新（如有人加入）。"""
    try:
        D['socketio'].emit('gmk_room_update', {'rid': rid, 'guest_id': guest_id}, room='gmk_%d' % rid)
    except Exception:
        pass






def gomoku_list():
    db = D['get_db']()
    me = D['current_user']()
    public_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM gomoku_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.is_public=1 AND r.status='waiting' AND r.mode=0 AND r.owner_id != ? "
        "ORDER BY r.id DESC LIMIT 20",
        (me['id'],)).fetchall()
    my_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM gomoku_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.owner_id=? OR r.guest_id=? ORDER BY r.id DESC LIMIT 20",
        (me['id'], me['id'])).fetchall()
    return render_template("gomoku_list.html",
                           public_rooms=public_rooms, my_rooms=my_rooms, user=me,
                           ai_lb=D['_ai_survival_leaderboard']('gomoku'),
                           my_ai=_my_ai_survival('gomoku'),
                           blb_rows=_board_wins_leaderboard('gomoku'))






def gomoku_join(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['mode'] == 1:
        return jsonify({"ok": False, "msg": "人机房间无需加入，直接进入即可"})
    if room['status'] != 'waiting':
        return jsonify({"ok": False, "msg": "房间已开始/结束"})
    if room['owner_id'] == me['id']:
        return jsonify({"ok": False, "msg": "不能加入自己的房间"})
    if room['guest_id'] and room['guest_id'] != me['id']:
        return jsonify({"ok": False, "msg": "房间已满"})
    db.execute("UPDATE gomoku_rooms SET guest_id=?, status='playing', turn_user_id=?, updated_at=? WHERE id=?",
               (me['id'], room['owner_id'], time.time(), rid))
    db.commit()
    _gmk_room_broadcast(rid, guest_id=me['id'])
    _gmk_lobby_broadcast()
    return jsonify({"ok": True, "msg": "加入成功"})






def gomoku_random_join():
    """随机加入一个公开的 PvP 等待房间。"""
    me = D['current_user']()
    db = D['get_db']()
    rooms = db.execute(
        "SELECT id FROM gomoku_rooms WHERE is_public=1 AND status='waiting' AND mode=0 "
        "AND owner_id != ? AND (guest_id IS NULL OR guest_id = ?) ORDER BY RANDOM() LIMIT 5",
        (me['id'], me['id'])).fetchall()
    for row in rooms:
        rid = row['id']
        cur = db.execute(
            "UPDATE gomoku_rooms SET guest_id=?, status='playing', turn_user_id=owner_id, updated_at=? "
            "WHERE id=? AND status='waiting' AND (guest_id IS NULL OR guest_id=?)",
            (me['id'], time.time(), rid, me['id']))
        if cur.rowcount:
            db.commit()
            _gmk_room_broadcast(rid, guest_id=me['id'])
            _gmk_lobby_broadcast()
            return jsonify({"ok": True, "rid": rid})
    return jsonify({"ok": False, "msg": "暂无可加入的公开房间"})






def gomoku_room(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute(
        "SELECT r.*, ou.username as owner_name, gu.username as guest_name "
        "FROM gomoku_rooms r "
        "LEFT JOIN users ou ON ou.id=r.owner_id "
        "LEFT JOIN users gu ON gu.id=r.guest_id "
        "WHERE r.id=?", (rid,)
    ).fetchone()
    if not room:
        return "房间不存在", 404
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    is_spectator = not (is_owner or is_guest)   # 观战只读（落子另有 owner/guest 校验）
    return render_template("gomoku_room.html", room=room, user=me, N=GOMOKU_N,
                           is_owner=is_owner, is_guest=is_guest,
                           ai_builtin=not _RAPFI_AVAILABLE,
                           og=D['og_context'](title="来下一盘五子棋 · MONO",
                                         desc="好友开了个五子棋房间，就等你了。",
                                         site_name="MONO 社区"))


def _gomoku_payload_from_db(rid):
    """读库构造五子棋 payload（与 move 接口返回结构保持一致）。"""
    db = D['get_db']()
    fin = db.execute(
        "SELECT board_state, status, winner_id, win_line, turn_user_id, last_move_x, last_move_y "
        "FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    if not fin:
        return None
    win_line_arr = None
    if fin['win_line']:
        try:
            win_line_arr = json.loads(fin['win_line'])
        except Exception:
            win_line_arr = None
    return {
        "board": fin['board_state'],
        "status": fin['status'],
        "winner_id": fin['winner_id'],
        "win_line": win_line_arr,
        "turn_user_id": fin['turn_user_id'],
        "last_move": [fin['last_move_x'], fin['last_move_y']],
    }


def _gomoku_emit(rid):
    """广播五子棋房间最新状态。"""
    payload = _gomoku_payload_from_db(rid)
    if payload is None:
        return None
    try:
        D['socketio'].emit('gmk_move', payload, room='gmk_%d' % rid)
    except Exception:
        pass
    D['_award_game_stats']('gomoku', rid)
    return payload


def _gomoku_ai_play(rid, board, x, y, player_id):
    """后台线程：Rapfi 执白应手（失败降级内置评分 AI）。

    玩家落子已在 HTTP 请求内落库并返回，这里只负责 AI 那一步，
    不阻塞前端（Rapfi 每步预算 5 秒）。turn_user_id = -1 表示「AI 思考中」，
    无论成功失败都必须交回玩家，否则房间会永久卡住。
    """
    def _give_back():
        try:
            with D['app'].app_context():
                db = D['get_db']()
                db.execute("UPDATE gomoku_rooms SET turn_user_id=? WHERE id=? AND turn_user_id=-1",
                           (player_id, rid))
                db.commit()
                _gomoku_emit(rid)
        except Exception:
            pass

    try:
        with D['app'].app_context():
            db = D['get_db']()
            room = db.execute("SELECT * FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
            if not room or room['status'] != 'playing' or room['turn_user_id'] != -1:
                return

        ai = None
        try:
            ai = run_blocking(_rapfi_ai_move, rid, board, x, y)   # 引擎最多 5 秒
        except Exception:
            ai = None
        if not ai:
            try:
                ai = _gomoku_ai_move(board)
            except Exception:
                ai = None

        with D['app'].app_context():
            db = D['get_db']()
            room = db.execute("SELECT * FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
            if not room or room['status'] != 'playing' or room['turn_user_id'] != -1:
                return
            if not ai:
                _give_back()
                return
            ax, ay = ai
            cur = _gomoku_parse_board(room['board_state'])
            if _gomoku_at(cur, ax, ay) != '.':
                _give_back()
                return
            board2 = _gomoku_set(cur, ax, ay, GOMOKU_P2)
            win2, win_line2 = _gomoku_check_win(board2, ax, ay, GOMOKU_P2)
            if win2:
                nstatus, nturn, nwinner = 'finished', None, -1
            elif '.' not in board2:
                nstatus, nturn, nwinner = 'finished', None, None
            else:
                nstatus, nturn, nwinner = 'playing', player_id, None
            db.execute(
                "UPDATE gomoku_rooms SET board_state=?, turn_user_id=?, status=?, winner_id=?, "
                "win_line=?, last_move_x=?, last_move_y=?, uci_moves=?, updated_at=? WHERE id=?",
                (board2, nturn, nstatus, nwinner, win_line2, ax, ay,
                 _gomoku_hist_append(room, ax, ay), time.time(), rid))
            db.commit()
            if nstatus == 'finished':
                try:
                    _rapfi_close_room(rid)
                except Exception:
                    pass
            _gomoku_emit(rid)
    except Exception:
        _give_back()






def gomoku_move(rid):
    me = D['current_user']()
    data = request.get_json(silent=True) or request.form
    try:
        x = int(data.get("x"))
        y = int(data.get("y"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "坐标非法"})
    if not (0 <= x < GOMOKU_N and 0 <= y < GOMOKU_N):
        return jsonify({"ok": False, "msg": "坐标越界"})
    db = D['get_db']()
    _gomoku_ensure_hist(db)
    room = db.execute("SELECT * FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['status'] != 'playing':
        return jsonify({"ok": False, "msg": "对局未进行中"})
    if me['id'] == room['owner_id']:
        player = GOMOKU_P1
    elif me['id'] == room['guest_id']:
        player = GOMOKU_P2
    else:
        return jsonify({"ok": False, "msg": "观战者不能落子"})
    if room['turn_user_id'] != me['id']:
        return jsonify({"ok": False, "msg": "还没轮到你"})
    board = _gomoku_parse_board(room['board_state'])
    if _gomoku_at(board, x, y) != '.':
        return jsonify({"ok": False, "msg": "此位置已有棋子"})
    board = _gomoku_set(board, x, y, player)
    win, win_line = _gomoku_check_win(board, x, y, player)
    if win:
        next_status = 'finished'
        next_turn = None
        winner = me['id']
    else:
        if '.' not in board:
            next_status = 'finished'; next_turn = None; winner = None
        else:
            next_turn = (room['guest_id'] if me['id'] == room['owner_id'] else room['owner_id'])
            next_status = 'playing'
            winner = None
    ai_pending = (not win and next_status == 'playing'
                  and room['mode'] == 1 and me['id'] == room['owner_id'])
    hist = _gomoku_hist_append(room, x, y)      # 复盘用
    if ai_pending:
        db.execute(
            "UPDATE gomoku_rooms SET board_state=?, turn_user_id=-1, status=?, winner_id=?, win_line=?, "
            "last_move_x=?, last_move_y=?, uci_moves=?, updated_at=? WHERE id=?",
            (board, next_status, winner, win_line, x, y, hist, time.time(), rid))
        db.commit()
        _threading_mod.Thread(
            target=_gomoku_ai_play, args=(rid, board, x, y, me['id']), daemon=True).start()
    else:
        db.execute(
            "UPDATE gomoku_rooms SET board_state=?, turn_user_id=?, status=?, winner_id=?, win_line=?, "
            "last_move_x=?, last_move_y=?, uci_moves=?, updated_at=? WHERE id=?",
            (board, next_turn, next_status, winner, win_line, x, y, hist, time.time(), rid))
        db.commit()
    _row = db.execute("SELECT status FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    if _row and _row['status'] == 'finished':
        try:
            _rapfi_close_room(rid)
        except Exception:
            pass
    fin = db.execute(
        "SELECT board_state, status, winner_id, win_line, turn_user_id, last_move_x, last_move_y "
        "FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    win_line_arr = None
    if fin['win_line']:
        try:
            win_line_arr = json.loads(fin['win_line'])
        except Exception:
            win_line_arr = None
    payload = {
        "board": fin['board_state'],
        "status": fin['status'],
        "winner_id": fin['winner_id'],
        "win_line": win_line_arr,
        "turn_user_id": fin['turn_user_id'],
        "last_move": [fin['last_move_x'], fin['last_move_y']],
    }
    try:
        D['socketio'].emit('gmk_move', payload, room='gmk_%d' % rid)
    except Exception:
        pass
    D['_award_game_stats']('gomoku', rid)
    return jsonify(dict(payload, ok=True))






def gomoku_state(rid):
    """返回房间最新状态（前端轮询同步 PvP 局面）。"""
    me = D['current_user']()
    room = D['get_db']().execute("SELECT * FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"}), 404
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    # 观战：任何人可读（落子另有 owner/guest 校验）
    win_line_arr = None
    if room['win_line']:
        try:
            win_line_arr = json.loads(room['win_line'])
        except Exception:
            win_line_arr = None
    return jsonify({
        "ok": True,
        "board": room['board_state'],
        "status": room['status'],
        "winner_id": room['winner_id'],
        "win_line": win_line_arr,
        "turn_user_id": room['turn_user_id'],
        "last_move": [room['last_move_x'], room['last_move_y']],
    })






def gomoku_restart(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM gomoku_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id']:
        return jsonify({"ok": False, "msg": "仅房主可重开"})
    db.execute(
        "UPDATE gomoku_rooms SET board_state=?, status='playing', winner_id=NULL, win_line=NULL, "
        "last_move_x=NULL, last_move_y=NULL, turn_user_id=owner_id, updated_at=? WHERE id=?",
        (GOMOKU_EMPTY_BOARD, time.time(), rid))
    db.commit()
    try:
        _rapfi_close_room(rid)
    except Exception:
        pass
    return jsonify({"ok": True})



GOMOKU_P1 = "1"


GOMOKU_P2 = "2"



def _rapfi_detect_exe():
    """探测可用的引擎 exe：AVX2 优先，老 CPU（不支持 AVX2）自动回退 SSE 兼容版。

    通过真实启动 + START 握手验证，避免仅靠 CPU 型号猜测。
    全部失败返回 None（调用方降级为内置评分 AI）。
    """
    for name in _RAPFI_CANDIDATES:
        path = os.path.join(_RAPFI_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            p = _subprocess_mod.Popen(
                [path],
                stdin=_subprocess_mod.PIPE, stdout=_subprocess_mod.PIPE,
                stderr=_subprocess_mod.DEVNULL, text=True, bufsize=1,
                encoding='utf-8', errors='replace', cwd=_RAPFI_DIR)
        except Exception:
            continue
        q = _queue_mod.Queue()

        def _rd():
            try:
                for line in p.stdout:
                    if line:
                        q.put(line.strip())
            except Exception:
                pass
            try:
                q.put(None)
            except Exception:
                pass

        _threading_mod.Thread(target=_rd, daemon=True).start()
        ok = False
        try:
            p.stdin.write('START 15\n')
            p.stdin.flush()
            end = time.time() + 6
            while time.time() < end:
                try:
                    line = q.get(timeout=0.3)
                except _queue_mod.Empty:
                    continue
                if line is None:
                    break
                if line == 'OK':
                    ok = True
                    break
        except Exception:
            pass
        finally:
            try:
                p.stdin.write('END\n')
                p.stdin.flush()
            except Exception:
                pass
            D['_proc_stop'](p, grace=3)
        if ok:
            return path
    return None


try:
    _RAPFI_EXE = _rapfi_detect_exe()
except Exception:
    _RAPFI_EXE = None
_RAPFI_AVAILABLE = bool(_RAPFI_EXE)


