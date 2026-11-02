"""真华容道（横刀立马等四布局）复算。镜像 templates/klotski_cn.html：

    网格 4 列 5 行，piece = {id, w, h, x, y}（网格坐标，左上原点）
    移动（endDrag）：逐格目标吸附；X/Y 只变一轴（斜拖归约为 X 向），
        从 dragStart 向 target 逐格检查 isPositionValid，全通过才落子，steps++
    isPositionValid：不越界 + 与其它 piece 无 AABB 重叠
    胜利：caocao 位于 (1, 3)

seq 结构：
    {"layout_id": 1, "moves": [{"id": "zhangfei", "x": 0, "y": 2}, ...]}
"""
COLS, ROWS = 4, 5

LAYOUTS = {
    1: [("caocao", 2, 2, 1, 0), ("zhangfei", 1, 2, 0, 0), ("zhaoyun", 1, 2, 3, 0),
        ("guanyu", 2, 1, 1, 2), ("machao", 1, 2, 0, 2), ("huangzhong", 1, 2, 3, 2),
        ("zu1", 1, 1, 1, 3), ("zu2", 1, 1, 2, 3),
        ("zu3", 1, 1, 0, 4), ("zu4", 1, 1, 3, 4)],
    2: [("caocao", 2, 2, 1, 0), ("huangzhong", 1, 2, 0, 0), ("machao", 1, 2, 3, 0),
        ("zu7", 1, 1, 0, 2), ("guanyu", 2, 1, 1, 2), ("zu8", 1, 1, 3, 2),
        ("zhangfei", 1, 2, 0, 3), ("zu9", 1, 1, 1, 3), ("zu10", 1, 1, 2, 3),
        ("zhaoyun", 1, 2, 3, 3)],
    3: [("caocao", 2, 2, 0, 0), ("zhangfei", 1, 2, 2, 0), ("zhaoyun", 1, 2, 3, 0),
        ("guanyu", 2, 1, 0, 2), ("machao", 1, 2, 0, 3), ("zu5", 1, 1, 1, 3),
        ("zu6", 1, 1, 2, 3), ("huangzhong", 1, 2, 3, 3), ("zu7", 1, 1, 0, 4),
        ("zu8", 1, 1, 3, 4)],
    4: [("caocao", 2, 2, 1, 0), ("huangzhong", 1, 2, 0, 0), ("machao", 1, 2, 3, 0),
        ("zu1", 1, 1, 0, 2), ("zu2", 1, 1, 1, 2), ("zu3", 1, 1, 2, 2),
        ("zu4", 1, 1, 3, 2), ("zhangfei", 1, 2, 0, 3), ("guanyu", 2, 1, 1, 3),
        ("zhaoyun", 1, 2, 3, 3)],
}


def _valid(pieces, pid, x, y, w, h):
    if x < 0 or x + w > COLS or y < 0 or y + h > ROWS:
        return False
    for p in pieces:
        if p["id"] == pid:
            continue
        if (x < p["x"] + p["w"] and x + w > p["x"] and
                y < p["y"] + p["h"] and y + h > p["y"]):
            return False
    return True


def verify_klotski_cn(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    try:
        layout_id = int(payload.get("layout_id") or 0)
    except (TypeError, ValueError):
        return False, None, "bad_layout"
    init = LAYOUTS.get(layout_id)
    if not init:
        return False, None, "bad_layout"
    moves = payload.get("moves")
    if not isinstance(moves, list) or len(moves) > 20000:
        return False, None, "bad_moves"

    pieces = [{"id": i, "w": w, "h": h, "x": x, "y": y} for (i, w, h, x, y) in init]
    for k, mv in enumerate(moves):
        if not isinstance(mv, dict):
            return False, None, "mv_type_%d" % k
        pid = mv.get("id")
        try:
            tx, ty = int(mv.get("x")), int(mv.get("y"))
        except (TypeError, ValueError):
            return False, None, "mv_num_%d" % k
        piece = next((p for p in pieces if p["id"] == pid), None)
        if piece is None:
            return False, None, "mv_id_%d" % k
        sx, sy = piece["x"], piece["y"]
        if tx == sx and ty == sy:
            return False, None, "mv_same_%d" % k
        # 与前端一致：X 变则 Y 归位逐格查 X；Y 变则 X 归位逐格查 Y
        if tx != sx:
            ty = sy
            step = 1 if tx > sx else -1
            for x in range(sx + step, tx + step, step):
                if not _valid(pieces, pid, x, sy, piece["w"], piece["h"]):
                    return False, None, "mv_block_x_%d" % k
        else:
            step = 1 if ty > sy else -1
            for y in range(sy + step, ty + step, step):
                if not _valid(pieces, pid, sx, y, piece["w"], piece["h"]):
                    return False, None, "mv_block_y_%d" % k
        piece["x"], piece["y"] = tx, ty

    cao = next(p for p in pieces if p["id"] == "caocao")
    if not (cao["x"] == 1 and cao["y"] == 3):
        return False, None, "not_solved"
    return True, len(moves), "ok"
