"""数字华容道复算（templates/klotski.html）。

前端状态：
    tiles = [1, 2, ..., N-1, 0]（0 为空格，在数组末位），经 SIZE*100 步随机打乱
    点击：仅当点击格与空格上下左右相邻才交换；非相邻点击被前端忽略、不记步
    胜利：tiles == [1, 2, ..., N-1, 0]
复算：按 seq 提交初始盘面与有效点击序列，重放交换；终局须为顺序排列。
时间（秒）沿用服务端速度下界。

seq 结构：
    {"size": 4, "tiles0": [..打乱后的初始盘..], "clicks": [idx, ...]}
"""


def verify_klotski(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    try:
        size = int(payload.get("size") or 0)
    except (TypeError, ValueError):
        return False, None, "bad_size"
    if size not in (4, 5, 6):
        return False, None, "bad_size"
    total = size * size
    tiles0 = payload.get("tiles0")
    clicks = payload.get("clicks")
    if not isinstance(tiles0, list) or len(tiles0) != total:
        return False, None, "bad_tiles0"
    if sorted(tiles0) != list(range(total)):
        return False, None, "tiles0_perm"
    if not isinstance(clicks, list):
        return False, None, "bad_clicks"
    if len(clicks) > 100000:  # 防炸：不可能的有效步数上限
        return False, None, "too_many_clicks"

    tiles = list(tiles0)
    empty = tiles.index(0)
    for i, idx in enumerate(clicks):
        if not isinstance(idx, int) or not (0 <= idx < total):
            return False, None, "click_range_%d" % i
        if idx == empty:
            return False, None, "click_empty_%d" % i
        # 相邻判定：同行差 1 或同列差 1（与前端 handleClick 一致）
        if not ((abs(idx - empty) == 1 and idx // size == empty // size) or
                abs(idx - empty) == size):
            return False, None, "click_adjacent_%d" % i
        tiles[idx], tiles[empty] = tiles[empty], tiles[idx]
        empty = idx

    if tiles != list(range(1, total)) + [0]:
        return False, None, "not_solved"
    return True, len(clicks), "ok"
