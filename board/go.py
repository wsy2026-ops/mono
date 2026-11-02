# -*- coding: utf-8 -*-
"""board/go.py — 围棋（KataGo 主引擎 + GNU Go 兜底 + 房间对战）。

从 D['app'].py 搬迁。helper 一律走 deps.D 注入（不 import D['app']）。
"""
import json
import os
import random
import time
import atexit as _atexit_mod
import queue as _queue_mod
import subprocess as _subprocess_mod
import threading as _threading_mod

from flask import abort, jsonify, render_template, request
from flask_socketio import join_room

from .deps import D
from blocking import run_blocking
from games.mini import _board_wins_leaderboard, _my_ai_survival

GO_SIZES = (9, 13, 19)


GO_P1 = "1"


GO_P2 = "2"


GO_KOMI = {9: 1, 13: 2, 19: 4}


# ── 复盘：记录着法历史（058）──
_GO_HIST_READY = [False]


def _go_ensure_hist(db):
    """懒建 uci_moves 列。列已存在时 ALTER 抛异常，吞掉。"""
    if _GO_HIST_READY[0]:
        return
    try:
        db.execute("ALTER TABLE go_rooms ADD COLUMN uci_moves TEXT NOT NULL DEFAULT ''")
        db.commit()
    except Exception:
        pass
    _GO_HIST_READY[0] = True


def _go_hist_append(room, tok):
    """追加一手：落子记 'x,y'，虚手记 'pass'。"""
    try:
        prev = room['uci_moves'] or ''
    except (IndexError, KeyError):
        prev = ''
    return (prev + ' ' + tok).strip()


def _go_empty_board(n):
    return "." * (n * n)


def _go_at(board, x, y, n):
    return board[y * n + x]


def _go_set(board, x, y, v, n):
    arr = list(board)
    arr[y * n + x] = v
    return "".join(arr)


def _go_group_info(board, x, y, n):
    """返回 (该组棋子坐标集合, 该组气点集合)。"""
    color = _go_at(board, x, y, n)
    stones = set()
    libs = set()
    stack = [(x, y)]
    while stack:
        cx, cy = stack.pop()
        if (cx, cy) in stones:
            continue
        stones.add((cx, cy))
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if 0 <= nx < n and 0 <= ny < n:
                v = _go_at(board, nx, ny, n)
                if v == color:
                    stack.append((nx, ny))
                elif v == '.':
                    libs.add((nx, ny))
    return stones, libs


def _go_apply(board, x, y, player, n):
    """落子并提子。返回 (新盘面, 被提棋子坐标列表)；自杀返回 (None, [])。"""
    board2 = _go_set(board, x, y, player, n)
    opp = GO_P2 if player == GO_P1 else GO_P1
    captured = []
    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= nx < n and 0 <= ny < n and _go_at(board, nx, ny, n) == opp:
            stones, libs = _go_group_info(board2, nx, ny, n)
            if not libs:
                for sx, sy in stones:
                    board2 = _go_set(board2, sx, sy, '.', n)
                    captured.append((sx, sy))
    _, my_libs = _go_group_info(board2, x, y, n)
    if not my_libs:
        return None, []
    return board2, captured


def _go_is_legal(board, x, y, player, n, prev_board):
    if not (0 <= x < n and 0 <= y < n):
        return False, "坐标越界"
    if _go_at(board, x, y, n) != '.':
        return False, "该位置已有棋子"
    board2, captured = _go_apply(board, x, y, player, n)
    if board2 is None:
        return False, "禁止自杀（落子后无气）"
    if prev_board is not None and len(captured) == 1 and board2 == prev_board:
        return False, "打劫：禁止立即提回"
    return True, ""


def _go_scores(board, n):
    """简化数子法：黑/白得分 = 子数 + 各自完全包围的空域（双方都可到达的空点不计）。"""
    black = board.count(GO_P1)
    white = board.count(GO_P2)
    seen = set()
    black_area = 0
    white_area = 0
    for y in range(n):
        for x in range(n):
            if (x, y) in seen or _go_at(board, x, y, n) != '.':
                continue
            pts = set()
            colors = set()
            stack = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in pts:
                    continue
                pts.add((cx, cy))
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < n and 0 <= ny < n:
                        v = _go_at(board, nx, ny, n)
                        if v == '.':
                            stack.append((nx, ny))
                        elif v in (GO_P1, GO_P2):
                            colors.add(v)
            seen |= pts
            if colors == {GO_P1}:
                black_area += len(pts)
            elif colors == {GO_P2}:
                white_area += len(pts)
    return black + black_area, white + white_area


def _go_judge(board, n):
    """对局结束判定：返回 (胜方字符, 得分 dict)。"""
    bs, ws = _go_scores(board, n)
    komi = GO_KOMI.get(n, 4)
    diff = bs - ws
    if diff > 2 * komi:
        winner = GO_P1
    else:
        winner = GO_P2
    return winner, {"black": bs, "white": ws, "komi": komi, "diff": diff}


def _go_ai_move(board, n, player):
    """内置围棋 AI：启发式评分选点；无合法点返回 None（表示 pass）。"""
    opp = GO_P2 if player == GO_P1 else GO_P1
    cx0, cy0 = (n - 1) / 2.0, (n - 1) / 2.0
    best = None
    best_score = -1e18
    for y in range(n):
        for x in range(n):
            if _go_at(board, x, y, n) != '.':
                continue
            ok, _ = _go_is_legal(board, x, y, player, n, None)
            if not ok:
                continue
            board2, captured = _go_apply(board, x, y, player, n)
            score = len(captured) * 40.0
            _, my_libs = _go_group_info(board2, x, y, n)
            score += len(my_libs) * 3.0
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < n and 0 <= ny < n:
                    v = _go_at(board, nx, ny, n)
                    if v == player:
                        score += 6.0
                    elif v == opp:
                        score += 3.0
                        stones, libs = _go_group_info(board, nx, ny, n)
                        if len(libs) == 1:
                            score += 25.0
                        elif len(libs) == 2:
                            score += 8.0
            score += max(0.0, n - (abs(x - cx0) + abs(y - cy0))) * 0.5
            score += random.random() * 0.5
            if score > best_score:
                best_score = score
                best = (x, y)
    return best


_KATAGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'katago')


_KATAGO_MAX_ENGINES = 1 if D['_LOW_MEM'] else 4


def _katago_override(max_time=5):
    if D['_LOW_MEM']:
        return ("maxVisits=1000,maxTime=%d,numSearchThreads=2,nnMaxBatchSize=1,"
                "nnCacheSizePowerOfTwo=16,nnMutexPoolSizePowerOfTwo=12" % max_time)
    return "maxVisits=1000,maxTime=%d" % max_time


_KATAGO_OVERRIDE = _katago_override()


_GTP_LETTERS = "ABCDEFGHJKLMNOPQRST"


def _go_xy_to_gtp(x, y, n):
    return _GTP_LETTERS[x] + str(n - y)


def _go_gtp_to_xy(s, n):
    s = s.strip().upper()
    if s == "PASS":
        return None
    col = s[0]
    try:
        x = _GTP_LETTERS.index(col)
        y = n - int(s[1:])
    except (ValueError, IndexError):
        return None
    if not (0 <= x < n and 0 <= y < n):
        return None
    return x, y


def _katago_version_ok(exe):
    """轻量验证 katago.exe 能在本机真实运行（version 命令不加载权重，<2s）。
    用于拦截「文件存在但系统跑不起来」（32 位系统、缺运行库、Win7 兼容等）。"""
    try:
        p = _subprocess_mod.Popen([exe, 'version'],
                                  stdout=_subprocess_mod.PIPE,
                                  stderr=_subprocess_mod.DEVNULL,
                                  cwd=os.path.dirname(exe))
        try:
            out, _ = p.communicate(timeout=20)
        except Exception:
            D['_proc_stop'](p, grace=3)
            return False
        return p.returncode == 0 and b'KataGo' in (out or b'')
    except Exception:
        return False


_KATAGO_CANDIDATES = ["katago", "katago.exe", "katago_opencl.exe", "katago_eigen.exe"]


def _katago_detect():
    """按候选顺序探测 katago 可执行文件与权重模型，返回 (exe, model) 或 (None, None)。

    除文件存在外还会真实运行一次 version 命令：exe 在本机起不来时
    直接判不可用（调用方降级为下一个候选 / 内置 AI），避免建房后反复尝试死引擎。
    """
    model = None
    try:
        for f in sorted(os.listdir(_KATAGO_DIR)):
            if f.endswith(".bin.gz"):
                model = os.path.join(_KATAGO_DIR, f)
                break
    except Exception:
        pass
    if not model:
        return None, None
    for name in _KATAGO_CANDIDATES:
        exe = os.path.join(_KATAGO_DIR, name)
        if not os.path.isfile(exe):
            continue
        if not _katago_version_ok(exe):
            continue
        return exe, model
    return None, None


_KATAGO_EXE, _KATAGO_MODEL = _katago_detect()


_KATAGO_AVAILABLE = bool(_KATAGO_EXE)


_GNUGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gnugo")


_GNUGO_LEVEL = 8


_GNUGO_EXE = None


_GNUGO_AVAILABLE = False


def _gnugo_detect():
    d = _GNUGO_DIR
    try:
        for f in sorted(os.listdir(d)):
            low = f.lower()
            if low.endswith(".exe") or low == "gnugo":
                p = os.path.join(d, f)
                if os.path.isfile(p):
                    return p
    except Exception:
        pass
    return None


class _GnugoEngine(object):
    """GNU Go 引擎封装，接口与 _KatagoEngine 完全一致（genmove/play/close/ready）。"""

    def __init__(self, exe, n):
        self.exe = exe
        self.n = n
        self.proc = None
        self.ready = False
        self.q = None

    def start(self):
        cmd = [self.exe, "--mode", "gtp", "--level", str(_GNUGO_LEVEL),
               "--chinese-rules", "--capture-all-dead"]
        self.proc = _subprocess_mod.Popen(
            cmd, stdin=_subprocess_mod.PIPE, stdout=_subprocess_mod.PIPE,
            stderr=_subprocess_mod.DEVNULL, cwd=os.path.dirname(self.exe))
        self.q = _queue_mod.Queue()
        t = _threading_mod.Thread(target=self._read_loop, daemon=True)
        t.start()
        for c in ("boardsize %d" % self.n, "komi 7.5", "clear_board"):
            self._cmd(c, timeout=60)
        self.ready = True

    def _read_loop(self):
        try:
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    break
                self.q.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
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

    def _cmd(self, line, timeout=60):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("gnugo not running")
        try:
            while True:
                self.q.get_nowait()
        except _queue_mod.Empty:
            pass
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        out = []
        end = time.time() + max(timeout, 5)
        while time.time() < end:
            try:
                raw = self.q.get(timeout=0.25)
            except _queue_mod.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError("gnugo closed")
                continue
            if raw is None:
                raise RuntimeError("gnugo closed")
            if raw == "":
                break
            out.append(raw)
        else:
            raise RuntimeError("gnugo timeout")
        if not out:
            return ""
        first = out[0]
        if first.startswith("?"):
            raise RuntimeError("gnugo error: " + " ".join(out))
        return first[2:].strip() if len(first) > 2 else ""

    def genmove(self, color):
        return _go_gtp_to_xy(self._cmd("genmove %s" % color, timeout=60), self.n)

    def play(self, color, x, y):
        self._cmd("play %s %s" % (color, _go_xy_to_gtp(x, y, self.n)), timeout=20)

    def close(self):
        try:
            if self.proc is not None:
                try:
                    self.proc.stdin.write(b"quit\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                D['_proc_stop'](self.proc)
        except Exception:
            pass
        self.proc = None
        self.ready = False


_GNUGO_EXE = _gnugo_detect()
if _GNUGO_EXE:
    try:
        p = _subprocess_mod.Popen([_GNUGO_EXE, "--mode", "gtp", "--level", "1"],
                                  stdin=_subprocess_mod.PIPE, stdout=_subprocess_mod.PIPE,
                                  stderr=_subprocess_mod.DEVNULL, cwd=_GNUGO_DIR)
        p.stdin.write(b"name\n")
        p.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < 20:
            line = p.stdout.readline()
            if not line:
                break
            if line.strip().startswith(b"="):
                _GNUGO_AVAILABLE = True
                break
        try:
            p.stdin.write(b"quit\n")
            p.stdin.flush()
        except Exception:
            pass
        D['_proc_stop'](p, grace=3)
    except Exception:
        _GNUGO_AVAILABLE = False


_GO_AI_AVAILABLE = _KATAGO_AVAILABLE or _GNUGO_AVAILABLE


_GO_ENGINE_LABEL = "KataGo" if _KATAGO_AVAILABLE else ("GNU Go（兜底）" if _GNUGO_AVAILABLE else None)


_KATAGO_ENGINES = {}


_KATAGO_FAILED = {}          # rid -> 失败时间戳（见 _go_engine_get 的冷却逻辑）


_KATAGO_WARM_EV = {}


_KATAGO_LOCK = _threading_mod.Lock()


class _KatagoEngine(object):
    """每房间一个 KataGo 子进程，GTP 文本协议通信。

    与 rapfi 引擎同款防护：独立读线程 + 队列 + 真超时，
    引擎在目标机器（含 Win7/老 CPU/缺运行库）上起不来或卡死时，
    调用方能在超时后降级为内置 AI，而不是无限等待。
    """

    def __init__(self, exe, model, n):
        self.exe = exe
        self.model = model
        self.n = n
        self.proc = None
        self.ready = False
        self.q = None
        self.reader = None
        self.lock = _threading_mod.Lock()
        self.override = _KATAGO_OVERRIDE

    def start(self):
        cmd = [self.exe, "gtp", "-model", self.model,
               "-override-config", self.override]
        _gtp_cfg = os.path.join(os.path.dirname(self.exe), "gtp.cfg")
        if os.path.isfile(_gtp_cfg):
            cmd = cmd[:2] + ["-config", _gtp_cfg] + cmd[2:]
        try:
            self.proc = _subprocess_mod.Popen(
                cmd, stdin=_subprocess_mod.PIPE, stdout=_subprocess_mod.PIPE,
                stderr=_subprocess_mod.DEVNULL, cwd=os.path.dirname(self.exe))
        except Exception:
            self.proc = None
            raise
        self.q = _queue_mod.Queue()
        self.reader = _threading_mod.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        # ⚠️ 2026-10-17 修 #37：KataGo 在 gtp 模式下**先要加载完模型**才会响应第一条命令，
        #    那个 .bin.gz 有 100MB，在 2C/2G 的机器上实测要好几分钟（220s 都不够）。
        #    原来 boardsize/komi 只给 90s 超时 → 必超时 → start() 抛异常
        #    → _KATAGO_FAILED 永久黑名单 → 静默降级到 gnugo（棋力差一大截）。
        #    现在：先等模型加载完（给足 8 分钟），再发初始化命令。
        self._wait_model_loaded(timeout=480)
        for c in ("boardsize %d" % self.n, "komi 7.5"):
            self._cmd(c, timeout=60)
        self.ready = True

    def _wait_model_loaded(self, timeout=480):
        """等 KataGo 把模型加载完。

        KataGo 加载期间会往 stdout 打一串日志（Loaded NN weights / GTP ready 之类），
        等到出现 '=' 开头的响应行，或看到 ready 字样，才算真的能收命令。
        这里不做别的，只把时间等够 —— 因为 `_cmd` 的 90s 默认值撑不到加载完。
        """
        end = time.time() + max(timeout, 30)
        seen = 0
        while time.time() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError("katago exited while loading model")
            try:
                raw = self.q.get(timeout=0.5)
            except _queue_mod.Empty:
                continue
            if raw is None:
                raise RuntimeError("katago closed while loading model")
            seen += 1
            s = raw.strip()
            # KataGo 就绪后会回一个空的 '=' 行；有些版本直接打 ready/GTP
            if s.startswith('=') or 'ready' in s.lower() or 'loaded' in s.lower():
                return
        # 超时不抛异常：也许它只是安静地加载完了，交给后面的 _cmd 去说话
        return

    def _read_loop(self):
        try:
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    break
                self.q.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
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
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError("katago not running")
        try:
            while True:
                self.q.get_nowait()
        except _queue_mod.Empty:
            pass
        try:
            self.proc.stdin.write((line + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except Exception:
            raise RuntimeError("katago write failed")
        out = []
        end = time.time() + max(timeout, 5)
        while time.time() < end:
            try:
                raw = self.q.get(timeout=0.25)
            except _queue_mod.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError("katago closed")
                continue
            if raw is None:
                raise RuntimeError("katago closed")
            if raw == "":
                break
            out.append(raw)
        else:
            raise RuntimeError("katago timeout")
        if not out:
            return ""
        first = out[0]
        if first.startswith("?"):
            raise RuntimeError("katago error: " + " ".join(out))
        return first[2:].strip() if len(first) > 2 else ""

    def genmove(self, color):
        return _go_gtp_to_xy(self._cmd("genmove %s" % color, timeout=45), self.n)

    def play(self, color, x, y):
        self._cmd("play %s %s" % (color, _go_xy_to_gtp(x, y, self.n)), timeout=20)

    def close(self):
        try:
            if self.proc is not None:
                try:
                    self.proc.stdin.write(b"quit\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                D['_proc_stop'](self.proc)
        except Exception:
            pass
        self.proc = None
        self.ready = False


# ⚠️ 2026-10-17 修 #37：_KATAGO_FAILED 原来是永久黑名单，
#    失败一次该房间就再也拿不到 KataGo，只能等进程重启 —— 用户重开一局都没用。
#    改成「N 分钟内不重试」，让引擎真的挂掉时能自愈，也不会每次请求都去撞一次。


_KATAGO_FAIL_COOLDOWN = 180          # 秒


def _go_engine_get(rid, n):
    """获取房间的围棋引擎，按 KataGo → GNU Go 兜底链；都不可用返回 None。"""
    with _KATAGO_LOCK:
        _bad = _KATAGO_FAILED.get(rid)
        if _bad is not None:
            if time.time() - _bad < _KATAGO_FAIL_COOLDOWN:
                return None
            # 冷却过了，给引擎一次重试机会
            _KATAGO_FAILED.pop(rid, None)
    with _KATAGO_LOCK:
        eng = _KATAGO_ENGINES.get(rid)
        if eng is not None:
            return eng
        if len(_KATAGO_ENGINES) >= _KATAGO_MAX_ENGINES:
            return None
        if _KATAGO_AVAILABLE:
            eng = _KatagoEngine(_KATAGO_EXE, _KATAGO_MODEL, n)
            eng.override = _katago_override(D['_ai_ms_for']('go_rooms', rid, 5, 'go'))
        elif _GNUGO_AVAILABLE:
            eng = _GnugoEngine(_GNUGO_EXE, n)
        else:
            return None
        try:
            eng.start()
        except Exception:
            try:
                eng.close()
            except Exception:
                pass
            _KATAGO_FAILED[rid] = time.time()
            return None
        _KATAGO_ENGINES[rid] = eng
        return eng


def _katago_get(rid, n):
    """兼容旧名字：围棋引擎获取（KataGo → GNU Go 兜底链）。"""
    return _go_engine_get(rid, n)


def _engine_degraded(failed, rid, cooldown):
    """这个房间当前是不是真的在降级（引擎用不了、走内置 AI）。

    冷却已过的 rid 顺手清掉，别让 dict 只增不减。
    """
    t = failed.get(rid)
    if t is None:
        return False
    if time.time() - t < cooldown:
        return True
    failed.pop(rid, None)
    return False


def _katago_warm(rid, n):
    """创建 PvAI 房间后后台预热 KataGo，避免第一手久等。"""
    ev = _threading_mod.Event()
    with _KATAGO_LOCK:
        _KATAGO_WARM_EV[rid] = ev

    def _do():
        try:
            _katago_get(rid, n)
        except Exception:
            pass
        finally:
            ev.set()
    _threading_mod.Thread(target=_do, daemon=True).start()


def _katago_await(rid, n, timeout=30):
    """等待房间引擎就绪（预热中等待最多 timeout 秒），返回 engine 或 None。"""
    ev = None
    with _KATAGO_LOCK:
        ev = _KATAGO_WARM_EV.get(rid)
    if ev is not None:
        ev.wait(timeout)
        if not ev.is_set():
            return None
    return _katago_get(rid, n)


def _katago_disable(rid):
    """引擎出错：关闭并标记该房间降级为内置 AI。"""
    with _KATAGO_LOCK:
        _KATAGO_WARM_EV.pop(rid, None)
        eng = _KATAGO_ENGINES.pop(rid, None)
        _KATAGO_FAILED[rid] = time.time()
    if eng is not None:
        try:
            eng.close()
        except Exception:
            pass


def _katago_close_room(rid):
    """房间重开/结束时释放 KataGo 进程。"""
    with _KATAGO_LOCK:
        _KATAGO_WARM_EV.pop(rid, None)
        eng = _KATAGO_ENGINES.pop(rid, None)
    if eng is not None:
        try:
            eng.close()
        except Exception:
            pass


def _katago_shutdown_all():
    with _KATAGO_LOCK:
        engs = list(_KATAGO_ENGINES.values())
        _KATAGO_ENGINES.clear()
    for eng in engs:
        try:
            eng.close()
        except Exception:
            pass


@_atexit_mod.register


def _katago_cleanup():
    _katago_shutdown_all()




def go_socket_join(data):
    """围棋房间实时推送订阅。"""
    user = D['current_user']()
    if not user:
        return
    rid = data.get('rid')
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return
    room = D['get_db']().execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return
    is_owner = room['owner_id'] == user['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == user['id']
    if not (is_owner or is_guest) and room['status'] != 'waiting':
        return
    join_room('go_%d' % rid, request.sid)


def _go_payload(room):
    score = None
    if room['score_json']:
        try:
            score = json.loads(room['score_json'])
        except Exception:
            score = None
    return {
        "board": room['board_state'],
        "status": room['status'],
        "winner_id": room['winner_id'],
        "score": score,
        "turn_user_id": room['turn_user_id'],
        "last_move": [room['last_move_x'], room['last_move_y']],
        "passes": room['passes'] or 0,
    }


def _go_emit(rid):
    room = D['get_db']().execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
    payload = _go_payload(room)
    try:
        D['socketio'].emit('go_move', payload, room='go_%d' % rid)
    except Exception:
        pass
    D['_award_game_stats']('go', rid)
    return dict(payload, ok=True)


def _go_lobby_broadcast():
    """通知围棋大厅所有在线客户端刷新房间列表。"""
    try:
        D['socketio'].emit('go_lobby_update', {}, room='go_lobby')
    except Exception:
        pass


def _go_room_broadcast(rid, guest_id=None):
    """通知房间内用户状态已更新（如有人加入）。"""
    try:
        D['socketio'].emit('go_room_update', {'rid': rid, 'guest_id': guest_id}, room='go_%d' % rid)
    except Exception:
        pass






def go_list():
    db = D['get_db']()
    me = D['current_user']()
    public_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM go_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.is_public=1 AND r.status='waiting' AND r.mode=0 AND r.owner_id != ? "
        "ORDER BY r.id DESC LIMIT 20",
        (me['id'],)).fetchall()
    my_rooms = db.execute(
        "SELECT r.*, u.username as owner_name FROM go_rooms r "
        "LEFT JOIN users u ON u.id=r.owner_id "
        "WHERE r.owner_id=? OR r.guest_id=? ORDER BY r.id DESC LIMIT 20",
        (me['id'], me['id'])).fetchall()
    return render_template("go_list.html",
                           public_rooms=public_rooms, my_rooms=my_rooms, user=me,
                           ai_lb=D['_ai_survival_leaderboard']('go'),
                           my_ai=_my_ai_survival('go'),
                           blb_rows=_board_wins_leaderboard('go'))






def go_join(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
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
        "UPDATE go_rooms SET guest_id=?, status='playing', turn_user_id=?, updated_at=? WHERE id=?",
        (me['id'], room['owner_id'], time.time(), rid))
    db.commit()
    _go_room_broadcast(rid, guest_id=me['id'])
    _go_lobby_broadcast()
    return jsonify({"ok": True})






def go_random_join():
    me = D['current_user']()
    db = D['get_db']()
    rows = db.execute(
        "SELECT id FROM go_rooms WHERE is_public=1 AND status='waiting' AND mode=0 AND owner_id != ? "
        "AND (guest_id IS NULL) ORDER BY RANDOM() LIMIT 5",
        (me['id'],)).fetchall()
    for row in rows:
        r = db.execute(
            "UPDATE go_rooms SET guest_id=?, status='playing', turn_user_id=owner_id, updated_at=? "
            "WHERE id=? AND status='waiting' AND guest_id IS NULL",
            (me['id'], time.time(), row['id']))
        if r.rowcount:
            db.commit()
            _go_room_broadcast(row['id'], guest_id=me['id'])
            _go_lobby_broadcast()
            return jsonify({"ok": True, "rid": row['id']})
    return jsonify({"ok": False, "msg": "暂无可加入的公开房间"})






def go_room(rid):
    me = D['current_user']()
    room = D['get_db']().execute(
        "SELECT r.*, ou.username as owner_name, gu.username as guest_name "
        "FROM go_rooms r "
        "LEFT JOIN users ou ON ou.id=r.owner_id "
        "LEFT JOIN users gu ON gu.id=r.guest_id "
        "WHERE r.id=?", (rid,)
    ).fetchone()
    if not room:
        abort(404)
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    is_spectator = not (is_owner or is_guest)   # 观战只读
    return render_template("go_room.html", room=room, user=me, N=room['board_size'],
                           is_owner=is_owner, is_guest=is_guest,
                           ai_builtin=(not _GO_AI_AVAILABLE)
                           or _engine_degraded(_KATAGO_FAILED, rid, _KATAGO_FAIL_COOLDOWN),
                           og=D['og_context'](title="来下一盘围棋 · MONO",
                                         desc="好友开了个围棋房间，就等你了。",
                                         site_name="MONO 社区"))






def go_move(rid):
    me = D['current_user']()
    db = D['get_db']()
    _go_ensure_hist(db)
    room = db.execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['status'] != 'playing':
        return jsonify({"ok": False, "msg": "对局已结束"})
    n = room['board_size']
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    if not (is_owner or is_guest):
        return jsonify({"ok": False, "msg": "你不是本局玩家"})
    if room['turn_user_id'] != me['id']:
        return jsonify({"ok": False, "msg": "还没轮到你"})
    board = room['board_state'] or _go_empty_board(n)
    is_pass = request.form.get("pass") == "1"

    if is_pass:
        if room['mode'] == 1:
            winner, score = _go_judge(board, n)
            win_id = room['owner_id'] if winner == GO_P1 else -1
            db.execute(
                "UPDATE go_rooms SET status='finished', winner_id=?, score_json=?, passes=passes+1, "
                "uci_moves=?, updated_at=? WHERE id=?",
                (win_id, json.dumps(score), _go_hist_append(room, 'pass'), time.time(), rid))
            db.commit()
            _katago_close_room(rid)
            return _go_emit(rid)
        passes = (room['passes'] or 0) + 1
        if passes >= 2:
            winner, score = _go_judge(board, n)
            win_id = room['owner_id'] if winner == GO_P1 else room['guest_id']
            db.execute(
                "UPDATE go_rooms SET status='finished', winner_id=?, score_json=?, passes=?, "
                "uci_moves=?, updated_at=? WHERE id=?",
                (win_id, json.dumps(score), passes, _go_hist_append(room, 'pass'), time.time(), rid))
            db.commit()
            _katago_close_room(rid)
            return _go_emit(rid)
        next_turn = room['guest_id'] if is_owner else room['owner_id']
        db.execute(
            "UPDATE go_rooms SET turn_user_id=?, passes=?, uci_moves=?, updated_at=? WHERE id=?",
            (next_turn, passes, _go_hist_append(room, 'pass'), time.time(), rid))
        db.commit()
    else:
        try:
            x = int(request.form.get("x", "-1"))
            y = int(request.form.get("y", "-1"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "参数错误"})
        player = GO_P1 if is_owner else GO_P2
        ok, msg = _go_is_legal(board, x, y, player, n, room['prev_board'])
        if not ok:
            return jsonify({"ok": False, "msg": msg})
        board2, _cap = _go_apply(board, x, y, player, n)
        prev = room['board_state']
        if room['mode'] == 1:
            db.execute(
                "UPDATE go_rooms SET board_state=?, prev_board=?, last_move_x=?, last_move_y=?, "
                "passes=0, turn_user_id=-1, uci_moves=?, updated_at=? WHERE id=?",
                (board2, prev, x, y, _go_hist_append(room, '%d,%d' % (x, y)), time.time(), rid))
            db.commit()
            _threading_mod.Thread(
                target=_go_ai_play, args=(rid, n, board2, prev, x, y, me['id']),
                daemon=True).start()
        else:
            next_turn = room['guest_id'] if is_owner else room['owner_id']
            db.execute(
                "UPDATE go_rooms SET board_state=?, prev_board=?, last_move_x=?, last_move_y=?, passes=0, "
                "turn_user_id=?, uci_moves=?, updated_at=? WHERE id=?",
                (board2, prev, x, y, next_turn,
                 _go_hist_append(room, '%d,%d' % (x, y)), time.time(), rid))
            db.commit()
    return _go_emit(rid)


def _go_ai_play(rid, n, board2, prev, x, y, player_id):
    """后台线程：KataGo 执白应手（失败/超时降级内置 AI）。

    玩家落子已在 HTTP 请求内落库并返回，这里只负责 AI 那一步。
    turn_user_id = -1 表示「AI 思考中」，无论成功失败都必须交回玩家。
    """
    def _give_back():
        try:
            with D['app'].app_context():
                db = D['get_db']()
                db.execute("UPDATE go_rooms SET turn_user_id=? WHERE id=? AND turn_user_id=-1",
                           (player_id, rid))
                db.commit()
                _go_emit(rid)
        except Exception:
            pass

    try:
        with D['app'].app_context():
            db = D['get_db']()
            room = db.execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
            if not room or room['status'] != 'playing' or room['turn_user_id'] != -1:
                return

        ai = None
        try:
            # eventlet 下必须走 tpool：KataGo 最坏 45-60 秒，直接跑会卡死全站
            def _ask_engine():
                _e = _katago_await(rid, n)
                if _e is not None and _e.ready:
                    _e.play('B', x, y)
                    return _e.genmove('W')
                return None
            ai = run_blocking(_ask_engine)
        except Exception:
            _katago_disable(rid)
        if ai is None:
            try:
                ai = _go_ai_move(board2, n, GO_P2)
            except Exception:
                ai = None

        with D['app'].app_context():
            db = D['get_db']()
            room = db.execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
            if not room or room['status'] != 'playing' or room['turn_user_id'] != -1:
                return

            if ai is None:
                _give_back()
                return
            ax, ay = ai
            board3, _cap2 = _go_apply(board2, ax, ay, GO_P2, n)
            if board3 is None:
                _give_back()
                return
            db.execute(
                "UPDATE go_rooms SET board_state=?, prev_board=?, last_move_x=?, last_move_y=?, "
                "passes=0, turn_user_id=?, uci_moves=?, updated_at=? WHERE id=?",
                (board3, board2, ax, ay, player_id,
                 _go_hist_append(room, '%d,%d' % (ax, ay)), time.time(), rid))
            db.commit()
            _go_emit(rid)
    except Exception:
        _give_back()






def go_state(rid):
    me = D['current_user']()
    room = D['get_db']().execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"}), 404
    is_owner = room['owner_id'] == me['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == me['id']
    # 观战：任何人可读
    return jsonify(dict(_go_payload(room), ok=True))






def go_restart(rid):
    me = D['current_user']()
    db = D['get_db']()
    room = db.execute("SELECT * FROM go_rooms WHERE id=?", (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    if room['owner_id'] != me['id']:
        return jsonify({"ok": False, "msg": "仅房主可重开"})
    n = room['board_size']
    db.execute(
        "UPDATE go_rooms SET board_state=?, status='playing', winner_id=NULL, score_json=NULL, "
        "last_move_x=NULL, last_move_y=NULL, passes=0, prev_board=NULL, turn_user_id=owner_id, updated_at=? WHERE id=?",
        (_go_empty_board(n), time.time(), rid))
    db.commit()
    _katago_close_room(rid)
    if room['mode'] == 1:
        _katago_warm(rid, n)
    return jsonify({"ok": True})




