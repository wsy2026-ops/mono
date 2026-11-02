"""2048 复算。镜像 templates/2048.html 的移动/合并逻辑。

规则
----
- 4×4 棋盘，开局 2 个方块（2 或 4）
- 每次移动：所有方块向该方向压缩，相邻同值合并（一个方块一次移动只合并一次），
  得分 += 合并后的值
- 棋盘有变化才算有效移动，之后随机生成一个新方块（90% 是 2，10% 是 4）

为什么 seq 要带「生成的新方块」
------------------------------
2048 有随机性：同样的移动序列，随机生成的方块不同，最终分数就不同。
所以前端必须把每步生成的新方块也报上来，服务端才能精确重放。

seq 结构
--------
    {
      "start": [[r, c, v], ...],                    # 开局方块（1~2 个）
      "moves": [{"d": 0|1|2|3, "s": [r, c, v]}, ...] # d: 0上 1右 2下 3左；s: 本步生成的新方块
    }

坐标系：r 行（0 在上），c 列（0 在左），v 方块值。

能防住什么
----------
分数必须与序列完全一致 —— 直接报高分会被拒。
想刷榜只能编造一条「真能得这么高分」的移动序列，难度不低于真玩。
"""

SIZE = 4
DIR_UP, DIR_RIGHT, DIR_DOWN, DIR_LEFT = 0, 1, 2, 3
VALID_DIRS = (DIR_UP, DIR_RIGHT, DIR_DOWN, DIR_LEFT)
VALID_VALS = (2, 4)

MAX_MOVES = 200000      # 防 payload 轰炸（正常一局几千步）


def _lines(dir_):
    """返回 SIZE 条线，每条线是从「移动方向最远端」到「最近端」的坐标序列。

    与前端 linesFor(dir) 一一对应：
      dir=3 左 → 从 c=0 往 c=3
      dir=1 右 → 从 c=3 往 c=0
      dir=0 上 → 从 r=0 往 r=3
      dir=2 下 → 从 r=3 往 r=0
    """
    lines = []
    for a in range(SIZE):
        line = []
        for b in range(SIZE):
            if dir_ == DIR_LEFT:
                r, c = a, b
            elif dir_ == DIR_RIGHT:
                r, c = a, SIZE - 1 - b
            elif dir_ == DIR_UP:
                r, c = b, a
            else:  # DIR_DOWN
                r, c = SIZE - 1 - b, a
            line.append((r, c))
        lines.append(line)
    return lines


def _move(board, dir_):
    """在 board 上原地执行一次移动。返回 (gained, moved)。"""
    gained = 0
    moved = False
    for line in _lines(dir_):
        vals = [board[r][c] for (r, c) in line if board[r][c]]
        out = []
        i = 0
        while i < len(vals):
            if i + 1 < len(vals) and vals[i] == vals[i + 1]:
                nv = vals[i] * 2
                gained += nv
                out.append(nv)
                i += 2          # 合并后跳过被吞的那个（对应前端 justMerged）
            else:
                out.append(vals[i])
                i += 1
        for idx, (r, c) in enumerate(line):
            nv = out[idx] if idx < len(out) else 0
            if board[r][c] != nv:
                moved = True
            board[r][c] = nv
    return gained, moved


def _int3(item):
    """校验 [r, c, v] 三个都是 int（bool 不算）。"""
    if not isinstance(item, (list, tuple)) or len(item) != 3:
        return None
    r, c, v = item
    for x in (r, c, v):
        if type(x) is not int:
            return None
    return r, c, v


def verify_2048(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"

    start = payload.get("start")
    moves = payload.get("moves")
    if not isinstance(start, list) or not isinstance(moves, list):
        return False, None, "bad_shape"
    if len(moves) > MAX_MOVES:
        return False, None, "too_many_moves"
    if not (1 <= len(start) <= 2):
        return False, None, "bad_start_count"

    board = [[0] * SIZE for _ in range(SIZE)]
    for k, item in enumerate(start):
        got = _int3(item)
        if got is None:
            return False, None, "start_item_%d" % k
        r, c, v = got
        if not (0 <= r < SIZE and 0 <= c < SIZE):
            return False, None, "start_pos_%d" % k
        if board[r][c]:
            return False, None, "start_dup_%d" % k
        if v not in VALID_VALS:
            return False, None, "start_val_%d" % k
        board[r][c] = v

    total = 0
    for i, mv in enumerate(moves):
        if not isinstance(mv, dict):
            return False, None, "move_type_%d" % i
        d = mv.get("d")
        if d not in VALID_DIRS:
            return False, None, "move_dir_%d" % i

        gained, moved = _move(board, d)
        if not moved:
            # 前端不该记录无效移动（棋盘没变化就不该有这一步）
            return False, None, "noop_move_%d" % i
        total += gained

        got = _int3(mv.get("s"))
        if got is None:
            return False, None, "spawn_%d" % i
        r, c, v = got
        if not (0 <= r < SIZE and 0 <= c < SIZE):
            return False, None, "spawn_pos_%d" % i
        if board[r][c]:
            return False, None, "spawn_occupied_%d" % i
        if v not in VALID_VALS:
            return False, None, "spawn_val_%d" % i
        board[r][c] = v

    if total != claimed_score:
        return False, total, "score_mismatch(%d!=%d)" % (claimed_score, total)
    return True, total, "ok"
