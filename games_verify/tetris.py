"""俄罗斯方块复算。镜像 templates/tetris.html。

规则
----
- 10×20 棋盘，7 种方块（I/O/T/S/Z/J/L），纯随机生成
- 消行得分：1/2/3/4 行 = 100/300/500/800，再乘当前等级
- 等级 = floor(累计消行 / 10) + 1，消行后才更新
- 软降 / 硬降 **不加分**，只有消行加分

seq 结构
--------
    {"pieces": [
        {"i": 方块索引 0-6, "x": 左列, "y": 顶行, "m": [[0/1, ...], ...]},
        ...
    ]}

`m` 是该方块**旋转后的矩阵**（前端 rotateM 的结果），服务端用它判断占格。
每放置一个方块就检查：不越界、不与已有方块重叠，然后消行计分。

能防住什么
----------
- 直接报高分：分数必须与放置序列算出来的一致，对不上就拒
- 方块重叠 / 越界：立刻拒绝

**防不住的（已知限制）**
- 伪造方块序列：只挑 I 方块刷分。前端是纯随机、没有种子，服务端无法验证序列真实性。
  要堵这个洞得把前端随机改成「带种子的 PRNG」，前后端用同一种子复算 —— 那是下一步。
  当前效果：把「改个数字就能刷榜」升级成「必须堆出真能消行的布局」，成本高得多。
"""

COLS, ROWS = 10, 20

# 与前端 SHAPES 顺序严格一致（索引 i 就是 seq 里的 i）
SHAPES = [
    [[1, 1, 1, 1]],            # 0 I
    [[1, 1], [1, 1]],          # 1 O
    [[0, 1, 0], [1, 1, 1]],    # 2 T
    [[0, 1, 1], [1, 1, 0]],    # 3 S
    [[1, 1, 0], [0, 1, 1]],    # 4 Z
    [[1, 0, 0], [1, 1, 1]],    # 5 J
    [[0, 0, 1], [1, 1, 1]],    # 6 L
]

BASE = [0, 100, 300, 500, 800]
MAX_PIECES = 100000


def _norm_matrix(m):
    """校验并规范化矩阵：非空、矩形、只含 0/1、最大 4×4、至少一格。"""
    if not isinstance(m, list) or not m:
        return None
    if len(m) > 4:
        return None
    w = None
    out = []
    for row in m:
        if not isinstance(row, list) or not row or len(row) > 4:
            return None
        if w is None:
            w = len(row)
        elif len(row) != w:
            return None
        r = []
        for v in row:
            if type(v) is not int or v not in (0, 1):
                return None
            r.append(v)
        out.append(r)
    if not any(v for row in out for v in row):
        return None
    return out


def _clear_lines(grid):
    """原地消行，返回消掉的行数。"""
    cleared = 0
    r = ROWS - 1
    while r >= 0:
        if all(grid[r]):
            grid.pop(r)
            grid.insert(0, [0] * COLS)
            cleared += 1
        else:
            r -= 1
    return cleared


def verify_tetris(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"

    pieces = payload.get("pieces")
    if not isinstance(pieces, list):
        return False, None, "bad_pieces"
    if len(pieces) > MAX_PIECES:
        return False, None, "too_many_pieces"

    grid = [[0] * COLS for _ in range(ROWS)]
    total_lines = 0
    total_score = 0

    for k, p in enumerate(pieces):
        if not isinstance(p, dict):
            return False, None, "piece_type_%d" % k

        idx = p.get("i")
        if type(idx) is not int or not (0 <= idx < len(SHAPES)):
            return False, None, "piece_idx_%d" % k

        x, y = p.get("x"), p.get("y")
        if type(x) is not int or type(y) is not int:
            return False, None, "piece_pos_%d" % k

        m = _norm_matrix(p.get("m"))
        if m is None:
            return False, None, "piece_matrix_%d" % k

        h, w = len(m), len(m[0])
        if x < 0 or x + w > COLS or y + h > ROWS:
            return False, None, "piece_oob_%d" % k
        # y 允许为负：旋转后方块可能部分溢出顶部，前端 lock 时只落 Y>=0 的格子

        for r in range(h):
            for c in range(w):
                if not m[r][c]:
                    continue
                Y = y + r
                if Y < 0:
                    continue
                if grid[Y][x + c]:
                    return False, None, "piece_overlap_%d" % k

        for r in range(h):
            for c in range(w):
                if not m[r][c]:
                    continue
                Y = y + r
                if Y < 0:
                    continue
                grid[Y][x + c] = idx + 1

        cleared = _clear_lines(grid)
        if cleared:
            level = total_lines // 10 + 1        # 用「消行前」的等级，与前端一致
            base = BASE[cleared] if cleared < len(BASE) else 800
            total_score += base * level
            total_lines += cleared

    if total_score != claimed_score:
        return False, total_score, "score_mismatch(%d!=%d)" % (claimed_score, total_score)
    return True, total_score, "ok"
