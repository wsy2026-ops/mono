"""仙弈破阵复算（事件账路径）。镜像 templates/xianyi.html 计分：

    SC = [0, 8, 8, 15, 15, 20, 30, 100, 0]（索引 = 敌方棋子类型 1..6）
    玩家走子吃到敌子 → score += SC[type]；踩雷/AI 吃马 → 终局上报 score
该游戏 AI 带 Date.now() 预算回路、棋盘 22x22 随机生成，整局不可复现，
采用「吃子事件明细 + 累计一致性 + 预算上限」复算：
    1) 每类子的 SC 值与前端常量表一致（防止编造 type）
    2) score == Σ SC[type]（累计分必须等于事件账）
    3) 每类子的吃取数不超过其本局可生成预算：spawnTwo 每回合 2 个，
       回合数无上限 → 以「上报的回合数 cnt」为预算
       （cnt 与 eats 数量互相约束：每手最多吃 1 子）
    4) 上报伤害事件中含 PLAYER(8) 无效等一律拒绝

seq 结构：
    {"eats": [{"t": type}, ...], "cnt": 回合数}
"""
SC = [0, 8, 8, 15, 15, 20, 30, 100, 0]


def verify_xianyi(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    eats = payload.get("eats")
    try:
        cnt = int(payload.get("cnt") or 0)
    except (TypeError, ValueError):
        return False, None, "bad_cnt"
    if not isinstance(eats, list):
        return False, None, "bad_eats"
    if len(eats) > 100000:
        return False, None, "too_many_eats"
    if not (0 <= cnt <= 200000):
        return False, None, "cnt_range"

    total = 0
    per_type = {}
    for k, e in enumerate(eats):
        if not isinstance(e, dict):
            return False, None, "eat_type_%d" % k
        try:
            t = int(e.get("t"))
        except (TypeError, ValueError):
            return False, None, "eat_num_%d" % k
        if not (1 <= t <= 6):
            return False, None, "eat_type_range_%d" % k
        per_type[t] = per_type.get(t, 0) + 1
        total += SC[t]

    # 每手最多吃一子：吃子数 ≤ 回合数；且总生成量 ≤ 每回合 2 个
    if len(eats) > cnt:
        return False, None, "eats_over_cnt"
    if 2 * cnt < sum(per_type.values()):
        return False, None, "eats_over_spawn"
    if per_type.get(7, 0):
        return False, None, "ate_jiang"
    if total != claimed_score:
        return False, total, "score_mismatch(%d!=%d)" % (claimed_score, total)
    return True, total, "ok"
