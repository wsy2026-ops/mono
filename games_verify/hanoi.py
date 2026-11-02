"""汉诺塔复算。

前端（templates/hanoi.html）状态：
    pegs = [[level, level-1, ..., 1], [], []]   # 三根柱，栈顶在数组末尾
    每步 {from, to, disk}：disk 必须是源柱顶盘且小于目标柱顶盘（空柱视为 +∞）
    胜利条件：pegs[2] 装满 level 个盘

复算：按 seq 从初始状态重放，终局必须满足胜利条件，且重放步数 == 上报 moves。
ms（用时）无法由序列复算，沿用服务端既有速度下界（app.py 侧已校验）。
"""
MAX_LEVEL = 9


def verify_hanoi(payload, claimed_score):
    """payload = 完整请求 data：level/moves/ms 在顶层，seq 是走法数组 mvHist。"""
    try:
        level = int(payload.get("level") or 0)
        moves = int(payload.get("moves") or 0)
    except (TypeError, ValueError):
        return False, None, "bad_number"
    seq = payload.get("seq")
    if level < 2 or level > MAX_LEVEL:
        return False, None, "bad_level"
    if not isinstance(seq, list) or (seq and len(seq) != moves) or not seq:
        return False, None, ("no_seq" if not seq else "seq_mismatch_moves")

    pegs = [list(range(level, 0, -1)), [], []]
    for i, mv in enumerate(seq):
        if not isinstance(mv, dict):
            return False, None, "seq_type_%d" % i
        try:
            src = int(mv.get("from"))
            dst = int(mv.get("to"))
            disk = int(mv.get("disk"))
        except (TypeError, ValueError):
            return False, None, "seq_number_%d" % i
        if src not in (0, 1, 2) or dst not in (0, 1, 2) or src == dst:
            return False, None, "seq_peg_%d" % i
        if not pegs[src]:
            return False, None, "seq_empty_%d" % i
        if pegs[src][-1] != disk:
            return False, None, "seq_notop_%d" % i
        if pegs[dst] and disk > pegs[dst][-1]:
            return False, None, "seq_order_%d" % i
        pegs[src].pop()
        pegs[dst].append(disk)

    if len(pegs[2]) != level:
        return False, None, "not_solved"
    # 复算通过：level 为复算出的成绩（阶数），moves 为重放步数
    return True, level, "ok"
