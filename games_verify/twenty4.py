"""24 点复算。镜像 templates/24_puzzle.html：

    题目：4 个 1~20 的随机整数（前端只保留有解题目）
    操作：每次合并两个数（+ - * /，除零拒绝），4 步后剩 1 个数
    正确：剩余的 1 个数 == 24 → score += 10
复算：按 seq 的每题复刻合并演算；复算答对数 == 上报 score / 10。

seq 结构：
    {"rounds": [
        {"nums": [4,6,1,2], "ops": [{"a": i1, "b": i2, "op": "+"} ...]},
        ...
    ]}
    （ops 每个元素作用于「当前 nums 数组」的两个下标，合并结果压回数组，
      与前端 calculate(i1, i2, op) 行为一致。）
"""


def _apply(nums, mv):
    try:
        a = int(mv["a"]); b = int(mv["b"]); op = str(mv["op"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= a < len(nums)) or not (0 <= b < len(nums)) or a == b:
        return None
    x, y = nums[a], nums[b]
    if op == "+":
        r = x + y
    elif op == "-":
        r = x - y
    elif op == "*":
        r = x * y
    elif op == "/":
        if abs(y) < 1e-6:
            return None
        r = x / y
    else:
        return None
    new = [nums[i] for i in range(len(nums)) if i != a and i != b]
    new.append(r)
    return new


def verify_24(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    rounds = payload.get("rounds")
    if not isinstance(rounds, list):
        return False, None, "bad_rounds"
    if len(rounds) > 5000:  # 单局题数上限（防 payload 轰炸）
        return False, None, "too_many_rounds"
    solved = 0
    for k, rnd in enumerate(rounds):
        if not isinstance(rnd, dict):
            return False, None, "round_type_%d" % k
        nums = rnd.get("nums")
        ops = rnd.get("ops")
        if not isinstance(nums, list) or len(nums) != 4 or not isinstance(ops, list):
            return False, None, "round_shape_%d" % k
        nums = list(nums)
        for v in nums:
            if not isinstance(v, (int, float)) or not (1 <= v <= 20) or v != int(v):
                return False, None, "round_num_%d" % k
        nums = [int(v) for v in nums]
        if len(ops) != 3:
            return False, None, "ops_len_%d" % k
        ok = True
        for j, mv in enumerate(ops):
            nums = _apply(nums, mv)
            if nums is None:
                ok = False
                break
        if not ok:
            return False, None, "op_bad_%d_%d" % (k, j)
        if abs(nums[0] - 24) < 1e-6:
            solved += 1
    expected = solved * 10
    if expected != claimed_score:
        return False, expected, "score_mismatch(%d!=%d)" % (claimed_score, expected)
    return True, expected, "ok"
