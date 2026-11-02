"""跳一跳复算（物理引擎镜像）。逐帧复刻 templates/jump.html：

常量: chargeSpeed=1.2, maxPower=100, minValidPower=8,
      powerToVx=0.12, jumpAngleRatio=1.2, gravity=0.75,
      player {x:100, w:30, h:30}, platformHeight=20
平台: gap∈[100,220], width∈[70,130], y∈[150,430]（生成时随机浮动）
起跳: vx = power*0.12, vy = -vx*1.2
空中每帧: vy += 0.75; player.x += vx; player.y += vy
落地（与前端逐字一致）:
    vy > 0 &&
    player.x + w > p.x && player.x < p.x + p.width &&       // 水平区间重叠
    player.y + h >= p.y &&                                 // 脚底到达平台顶及以上
    player.y + h <= p.y + p.height + vy                    // 脚底未穿过平台底（含穿透帧）
    → 落到平台顶；仅当该平台是 platforms[1] 时计分：
       落点中心距平台中心 <5 → +3，否则 +1
   掉出画布: player.y > canvas.height + 100 → 终局
JS 与 Python 同为 IEEE754 双精度，公式逐字一致 → 结果可 100% 复算。

seq 结构:
    {"canvas_h": 画布高,
     "jumps": [{
        "power": 蓄力值,
        "x0": 起跳前 player.x,
        "y0": 起跳前 player.y（顶部坐标），
        "tx": 目标平台 x, "ty": 目标平台 y, "tw": 目标平台 width
     }, ...]}
"""
CHARGE_SPEED = 1.2
MAX_POWER = 100.0
MIN_VALID_POWER = 8
POWER_TO_VX = 0.12
JUMP_ANGLE_RATIO = 1.2
GRAVITY = 0.75
PLAYER_W = 30
PLAYER_H = 30
PLATFORM_H = 20
MAX_FRAMES = 50000


def verify_jump(data, claimed_score):
    payload = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return False, None, "bad_seq"
    jumps = payload.get("jumps")
    if not isinstance(jumps, list):
        return False, None, "bad_jumps"
    if len(jumps) > 100000:
        return False, None, "too_many"
    try:
        canvas_h = float(payload.get("canvas_h"))
    except (TypeError, ValueError):
        return False, None, "bad_canvas"
    if not (200 <= canvas_h <= 4000):
        return False, None, "bad_canvas"

    score = 0
    for k, j in enumerate(jumps):
        if not isinstance(j, dict):
            return False, None, "jump_type_%d" % k
        try:
            power = float(j["power"])
            x0 = float(j["x0"]); y0 = float(j["y0"])
            tx = float(j["tx"]); ty = float(j["ty"]); tw = float(j["tw"])
        except (KeyError, TypeError, ValueError):
            return False, None, "jump_num_%d" % k
        if not (0.0 <= power <= MAX_POWER + 1e-9) or not (0.0 < tw <= 4000.0):
            return False, None, "jump_range_%d" % k
        if not (200.0 <= ty <= 450.0):   # 平台 y ∈ [150,430] 的小数容差
            return False, None, "jump_ty_%d" % k
        if power <= MIN_VALID_POWER:
            continue                     # 松手无效跳：不跳、不计分、不换台

        vx = power * POWER_TO_VX
        vy = -vx * JUMP_ANGLE_RATIO
        x, y = x0, y0                    # player.x / player.y（顶部坐标）
        landed = False
        for _ in range(MAX_FRAMES):
            vy += GRAVITY
            x += vx
            y += vy
            foot = y + PLAYER_H
            # 与前端逐字一致的落地判定（对 platforms[1]）
            if (vy > 0 and
                    x + PLAYER_W > tx and x < tx + tw and
                    foot >= ty and foot <= ty + PLATFORM_H + vy):
                landed = True
                center = x + PLAYER_W / 2
                dist = abs(center - (tx + tw / 2))
                score += 3 if dist < 5 else 1
                break
            if y > canvas_h + 100:
                break                     # 掉出画布，终局
        if not landed:
            break                         # 本跳失败 → 终局，后续 jumps 无效
    if score != claimed_score:
        return False, score, "score_mismatch(%d!=%d)" % (claimed_score, score)
    return True, score, "ok"
