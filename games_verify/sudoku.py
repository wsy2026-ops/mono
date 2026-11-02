"""数独复算。镜像 templates/sudoku.html 计分规则：

    关卡：puzzle（给定格）+ solution（唯一解），holes 逐关增加
    填数：只能填 hole 格；对 +15，错 -10（可反复错，分数下限 0）
    通关：整盘与 solution 一致，+40 秒继续
    终局：时间耗尽结算 state.score
复算：按 seq 的 fills 序列重放 ±15/-10；并校验 solution 是合法数独、
且所有 given 格与 solution 一致。时间轴不可复算，不做校验。

seq 结构：
    {"levels": [
        {"puzzle": [[9x9 int]], "solution": [[9x9 int]],
         "fills": [{"r": r, "c": c, "v": v}, ...]}, ...
    ]}
每关 fills 只记录**有效填数**（对或错都记，与前端一致）。
"""


def _check_solution(sol):
    if not isinstance(sol, list) or len(sol) != 9:
        return False
    for row in sol:
        if not isinstance(row, list) or len(row) != 9:
            return False
        if sorted(row) != list(range(1, 10)):
            return False
    for c in range(9):
        if sorted(sol[r][c] for r in range(9)) != list(range(1, 10)):
            return False
    for br in (0, 3, 6):
        for bc in (0, 3, 6):
            box = [sol[br + r][bc + c] for r in range(3) for c in range(3)]
            if sorted(box) != list(range(1, 10)):
                return False
    return True


def verify_sudoku(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    levels = payload.get("levels")
    if not isinstance(levels, list):
        return False, None, "bad_levels"
    if not (1 <= len(levels) <= 100):
        return False, None, "level_count"
    score = 0
    for k, lv in enumerate(levels):
        if not isinstance(lv, dict):
            return False, None, "lv_type_%d" % k
        puzzle, sol = lv.get("puzzle"), lv.get("solution")
        if not _check_solution(sol) or not isinstance(puzzle, list) or len(puzzle) != 9:
            return False, None, "lv_shape_%d" % k
        for r in range(9):
            row = puzzle[r]
            if not isinstance(row, list) or len(row) != 9:
                return False, None, "lv_row_%d" % k
            for c in range(9):
                v = row[c]
                if not isinstance(v, int) or not (0 <= v <= 9):
                    return False, None, "lv_cell_%d" % k
                # 给定格必须与解一致（0 表示 hole）
                if v != 0 and v != sol[r][c]:
                    return False, None, "lv_given_%d_%d_%d" % (k, r, c)
        holes = sum(1 for r in range(9) for c in range(9) if puzzle[r][c] == 0)
        fills = lv.get("fills")
        if not isinstance(fills, list) or len(fills) > 81:
            return False, None, "lv_fills_%d" % k
        filled = 0
        for f in fills:
            if not isinstance(f, dict):
                return False, None, "fill_type_%d" % k
            try:
                r, c, v = int(f["r"]), int(f["c"]), int(f["v"])
            except (KeyError, TypeError, ValueError):
                return False, None, "fill_num_%d" % k
            if not (0 <= r < 9 and 0 <= c < 9 and 1 <= v <= 9):
                return False, None, "fill_range_%d" % k
            if puzzle[r][c] != 0:
                return False, None, "fill_given_%d" % k   # 只能填 hole 格
            if v == sol[r][c]:
                score = score + 15
                filled += 1
                puzzle[r][c] = v   # 前端填对后即变 given，不可再填
            else:
                score = max(0, score - 10)
        # 终盘须等于 solution（否则本局是中途死的，score 不在此关后变化——
        # 前端时间到即结束，填完才 nextLevel，所以只有最后一关可以不完整）
        if k < len(levels) - 1 and filled != holes:
            return False, None, "lv_incomplete_%d" % k
    if score != claimed_score:
        return False, score, "score_mismatch(%d!=%d)" % (claimed_score, score)
    return True, score, "ok"
