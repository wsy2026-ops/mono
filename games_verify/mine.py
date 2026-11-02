"""扫雷复算。镜像 templates/mine.html：

    模式：beginner 9x9/10, intermediate 16x16/40, expert 30x16/99
    布雷：首次点击后 placeMines —— 首击及周围 3x3 为安全区，其余洗牌取雷
    操作：左键 open（0  adj 格触发 flood 展开）、右键插旗、已开数字格 chord
    胜利：opened == 总格数 - 雷数（即所有安全格揭开）

seq 结构：
    {"mode": "beginner",
     "mines": [idx, ...],              # 雷位线性索引（row*cols+col）
     "ops": [{"t": "o"|"f"|"c", "i": idx} ...]}   # 有效操作序列
复算：校验雷数与合法布雷 → 重放 ops → 终局所有安全格 opened。
时间（ms）无法复算，沿用服务端速度下界。
"""
MODES = {"beginner": (9, 9, 10),
         "intermediate": (16, 16, 40),
         "expert": (30, 16, 99)}


def verify_mine(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    mode = payload.get("mode")
    geo = MODES.get(mode)
    if not geo:
        return False, None, "bad_mode"
    cols, rows, mine_total = geo
    total = cols * rows

    mines = payload.get("mines")
    ops = payload.get("ops")
    if not isinstance(mines, list) or not isinstance(ops, list):
        return False, None, "bad_seq"
    if len(mines) != mine_total:
        return False, None, "mine_count"
    if not all(isinstance(m, int) and 0 <= m < total for m in mines):
        return False, None, "mine_range"
    if len(set(mines)) != mine_total:
        return False, None, "mine_dup"
    if len(ops) > 20000:
        return False, None, "too_many_ops"

    mine_set = set(mines)
    adj = {}
    for i in range(total):
        if i in mine_set:
            continue
        x, y = i % cols, i // cols
        n = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                ax, ay = x + dx, y + dy
                if 0 <= ax < cols and 0 <= ay < rows and (ay * cols + ax) in mine_set:
                    n += 1
        adj[i] = n

    opened, flags = set(), set()
    first = True

    def flood(i0):
        stack = [i0]
        while stack:
            i = stack.pop()
            if i in opened or i in flags or i in mine_set:
                continue
            opened.add(i)
            if adj[i] == 0:
                x, y = i % cols, i // cols
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ax, ay = x + dx, y + dy
                        if 0 <= ax < cols and 0 <= ay < rows:
                            stack.append(ay * cols + ax)

    for k, op in enumerate(ops):
        if not isinstance(op, dict):
            return False, None, "op_type_%d" % k
        t = op.get("t")
        i = op.get("i")
        if not isinstance(i, int) or not (0 <= i < total):
            return False, None, "op_range_%d" % k
        if t == "o":
            if i in opened or i in flags:
                return False, None, "op_open_dup_%d" % k
            if first:
                # 首击必须安全（前端 placeMines 的保证）
                x, y = i % cols, i // cols
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ax, ay = x + dx, y + dy
                        if 0 <= ax < cols and 0 <= ay < rows and \
                                (ay * cols + ax) in mine_set:
                            return False, None, "first_unsafe_%d" % k
                first = False
            if i in mine_set:
                return False, None, "op_open_mine_%d" % k
            flood(i)
        elif t == "f":
            # f = 插旗/拔旗切换（前端 toggleFlag，允许反复切）
            if i in opened:
                return False, None, "op_flag_opened_%d" % k
            if len(flags) >= mine_total and i not in flags:
                pass  # 旗数上限是前端 UI 约束，铺旗可超；不阻断复算
            if i in flags:
                flags.discard(i)
            else:
                flags.add(i)
        elif t == "c":  # chord：周围旗数与数字相等才展开
            if i not in opened or i in mine_set:
                return False, None, "op_chord_state_%d" % k
            if adj[i] == 0:
                return False, None, "op_chord_zero_%d" % k
            x, y = i % cols, i // cols
            n_flags = 0
            around = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    ax, ay = x + dx, y + dy
                    if 0 <= ax < cols and 0 <= ay < rows:
                        j = ay * cols + ax
                        around.append(j)
                        if j in flags:
                            n_flags += 1
            if n_flags != adj[i]:
                return False, None, "op_chord_mismatch_%d" % k
            for j in around:
                if j in opened or j in flags:
                    continue
                if j in mine_set:
                    return False, None, "op_chord_mine_%d" % k
                flood(j)
        else:
            return False, None, "op_t_%d" % k

    if len(opened) != total - mine_total:
        return False, None, "not_cleared"
    return True, len(ops), "ok"
