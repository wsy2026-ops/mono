"""色盲派对复算。镜像 templates/color_blind.html 的回合时钟：

    回合限时（nextRound 分档）：round<=3 → 5s，<=6 → 4s，<=9 → 3s，之后 2s
    时间到 → 若玩家所在格 !== 目标色 → gameOver；否则进入下一轮
    gameOver 上报的分数就是当前 round 数（初始 1，每过一轮 +1）
色彩本身是前端随机的，不复算；复算的是「每轮活着的时长」可信度：
    1) 轮次限时分档正确（第 i 轮上限 = 上表第 i 档）
    2) 每轮实际耗时 ∈ (0, 上限 + 容差]（容差覆盖切后台/卡顿）
    3) 活过的轮数 == 上报 score；最后一轮必须是失败的
    4) 一旦失败后面不能再有轮次记录
seq 结构：
    {"rounds": [
        {"alive": true,  "ms": 4873},   # 第 1 轮，活过，实际耗时
        {"alive": true,  "ms": 4102},   # 第 2 轮……
        {"alive": false, "ms": 1988},   # 第 3 轮，死，分数即 3
    ]}
"""
TOLERANCE_MS = 2500          # 切后台/卡顿容差
MAX_ROUNDS = 100000


def _limit_ms(round_index):
    # round_index 从 1 开始（前端 nextRound 里 round++ 后显示）
    if round_index <= 3:
        return 5000
    if round_index <= 6:
        return 4000
    if round_index <= 9:
        return 3000
    return 2000


def verify_colorblind(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    rounds = payload.get("rounds")
    if not isinstance(rounds, list):
        return False, None, "bad_rounds"
    if not (1 <= len(rounds) <= MAX_ROUNDS):
        return False, None, "rounds_count"
    for k, r in enumerate(rounds):
        if not isinstance(r, dict):
            return False, None, "round_type_%d" % k
        if not isinstance(r.get("alive"), bool):
            return False, None, "alive_type_%d" % k
        try:
            ms = float(r.get("ms"))
        except (TypeError, ValueError):
            return False, None, "ms_num_%d" % k
        if not (0 < ms <= _limit_ms(k + 1) + TOLERANCE_MS):
            return False, None, "ms_range_%d" % k
    # 只有最后一轮允许失败；中途失败还继续报轮次 = 编造
    for r in rounds[:-1]:
        if r.get("alive") is not True:
            return False, None, "alive_order"
    if rounds[-1].get("alive") is True:
        return False, None, "no_final_death"
    expected = len(rounds)
    if expected != claimed_score:
        return False, expected, "score_mismatch(%d!=%d)" % (claimed_score, expected)
    return True, expected, "ok"
