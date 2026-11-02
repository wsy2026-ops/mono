"""设备识别 —— 给「邀请得现金」活动做反刷。

为什么不用现成的指纹库
--------------------
这个站是 polling-only（waitress 不支持 WebSocket upgrade），
任何爬虫都能完整模拟一个真人会话：正常发 HTTP、正常轮询、正常等间隔。
指纹库里的「浏览器特征」他们手上全都有，比对等于没有。

所以这里的思路不是「像不像浏览器」，而是三件事：

1. 设备指纹：把一堆稳定特征拼起来哈希，认出「同一个客户端」。
   关键点：**IP 不进指纹**。机房 NAT 后面成千上万用户共享一个出口，
   拿 IP 当设备标识会把整栋楼认成一台机器。
2. 成本：领题时下发一个需要算力的 PoW（哈希cash），提交时必须验算。
   纯 JS 刷子也能做，但每题多花 ~80ms。批量刷号就不经济了。
3. 行为：真人拖滑块有加速度和过冲修正，脚本是匀速直线。
   服务端按轨迹算熵值，匀速直线直接判死。

指纹会变（换浏览器、清缓存），所以指纹只用来「限流」和「关联」，
**绝不用来单独判定是否是人**——单独用必然被绕过。
"""

import hashlib
import math
import time
import secrets
import json

HB_BUDGET = 16          # PoW 难度：要求前 N 个 bit 为 0，约 2^N 次哈希
NOCRYPTO_POW_BITS = 8      # 无 crypto.subtle 的旧 WebView 降级题（前端 mono_pow.js
                           # 纯 JS 算，256 次哈希 <1s；脚本冒领的成本仍高于人类）
HB_MAX_TRY = 4000000    # PoW 上限，防止死循环
TOKEN_TTL = 900         # token 有效期 15 分钟


# ══════════════════════════════════════════════════════════════
#  设备指纹
# ══════════════════════════════════════════════════════════════

FP_SALT = "mono-fp-v1"


def _sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"|")
    return h.hexdigest()


def client_fingerprint(req):
    """算一个稳定的设备指纹。

    刻意**不包含 IP**：学校/宿舍/网吧是 NAT 出口，几十上百人共享一个 IP，
    拿它当设备标识会把一整栋楼认成同一台机器。
    也不包含屏幕尺寸等容易被改的量。
    """
    ua = req.headers.get("User-Agent") or ""
    lang = req.headers.get("Accept-Language") or ""
    enc = req.headers.get("Accept-Encoding") or ""
    dt = req.headers.get("Sec-CH-UA-Platform") or ""
    model = req.headers.get("Sec-CH-UA-Mobile") or ""
    # 客户端能提供的一组小熵：canvas/webgl 指纹值由前端算好塞在头里
    marks = req.headers.get("X-FP-Marks") or ""
    base = _sha(FP_SALT, ua, lang, enc, dt, model, marks)
    return base


def mark_digest(marks):
    """把前端送来的特征串哈希，服务端只存哈希不存原文。

    原始特征（canvas 指纹等）有部分还原能力，不落盘更安全。
    """
    if not marks:
        return ""
    return hashlib.sha256(("m:" + marks).encode("utf-8", "replace")).hexdigest()[:24]


# ══════════════════════════════════════════════════════════════
#  PoW：领题时下发 nonce，提交时验算
# ══════════════════════════════════════════════════════════════

def _pow_ok(sol, chal, bits):
    """验算：sha256(chal|sol) 的前 bits 个 bit 是不是全 0。"""
    try:
        d = hashlib.sha256(("%s|%s" % (chal, sol)).encode("utf-8")).digest()
    except Exception:
        return False
    nbits = int(bits)
    full, rem = divmod(nbits, 8)
    for i in range(full):
        if d[i] != 0:
            return False
    if rem:
        if (d[full] >> (8 - rem)) & ((1 << rem) - 1):
            return False
    return True


def new_challenge(fp_digest="", bits=None):
    """生成一道题。

    chal 同时绑了设备指纹：把答案跟设备绑死，
    一道题不能拿去另一个设备用。
    """
    chal = secrets.token_hex(16)
    return {
        "chal": chal,
        "bits": int(bits) if bits else HB_BUDGET,
        "fp": (fp_digest or "")[:24],
    }


# ══════════════════════════════════════════════════════════════
#  轨迹熵：判断是「人拖的」还是「脚本挪的」
# ══════════════════════════════════════════════════════

def _entropy(seq):
    """香农熵，归一化到 0~1。真人的位置序列熵明显高于脚本。"""
    n = len(seq)
    if n < 3:
        return 0.0
    freq = {}
    for v in seq:
        k = round(v, 2)
        freq[k] = freq.get(k, 0) + 1
    h = 0.0
    for c in freq.values():
        p = c / n
        h -= p * math.log(p, 2)
    return h / math.log(n, 2) if n > 1 else 0.0


def track_score(points, target_pct, duration_ms):
    """给一条拖动轨迹打分，返回 (ok, reason, detail)。

    points 形如 [[t, x], ...]，t 毫秒、x 像素（或百分比）。
    判定四件事：点数、速度曲线是否有变化、末段是否过冲修正、耗时。
    """
    detail = {}
    if not points or len(points) < 6:
        return False, "轨迹点太少", detail
    if duration_ms < 220 or duration_ms > 20000:
        return False, "拖动耗时异常", detail

    xs = [p[1] for p in points]
    ts = [p[0] for p in points]

    dx = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    dt = [max(1, ts[i + 1] - ts[i]) for i in range(len(ts) - 1)]

    speeds = [abs(dx[i]) / dt[i] for i in range(len(dx))]
    maxv = max(speeds) if speeds else 0.0
    detail["max_speed"] = round(maxv, 3)
    detail["points"] = len(points)
    detail["duration"] = int(duration_ms)

    if maxv <= 0.01:
        return False, "滑块没动", detail

    # 匀速判定：速度变异系数太小 = 脚本
    mean_v = sum(speeds) / len(speeds)
    var = sum((v - mean_v) ** 2 for v in speeds) / len(speeds)
    cv = (var ** 0.5) / mean_v if mean_v > 0 else 0.0
    detail["cv"] = round(cv, 3)
    if cv < 0.18:
        return False, "移动速度过于均匀", detail

    # 方向反转次数：真人会有微调（过冲再拉回），脚本一路到底
    turns = 0
    for i in range(1, len(dx)):
        if dx[i] > 0.4 and dx[i - 1] < -0.4:
            turns += 1
        elif dx[i] < -0.4 and dx[i - 1] > 0.4:
            turns += 1
    detail["turns"] = turns
    if turns == 0 and len(points) < 12:
        return False, "没有一次方向修正", detail

    # 停顿：真人拖到接近目标会短暂停一下
    slow = sum(1 for i in range(len(speeds)) if speeds[i] < mean_v * 0.25)
    detail["slow_steps"] = slow
    if slow == 0 and len(points) < 14:
        return False, "移动过程没有停顿", detail

    # 熵
    e = _entropy(xs)
    detail["entropy"] = round(e, 3)
    if e < 0.55:
        return False, "移动路径熵过低", detail

    return True, "", detail


# ══════════════════════════════════════════════════════════════
#  token：把「领题时的题」和「提交时的答」串起来
# ══════════════════════════════════════════════════════════════

def make_token(fp_digest, challenge, issued=None):
    t = {
        "f": (fp_digest or "")[:24],
        "c": challenge["chal"],
        "b": challenge["bits"],
        "i": int(issued or time.time()),
        "n": secrets.token_hex(6),
    }
    raw = json.dumps(t, separators=(",", ":"), sort_keys=True)
    sig = _sha(FP_SALT, raw)[:32]
    return raw + "." + sig


def read_token(token):
    """拆并验签。返回 dict 或 None。"""
    if not token or "." not in str(token):
        return None
    try:
        raw, sig = str(token).rsplit(".", 1)
    except ValueError:
        return None
    if _sha(FP_SALT, raw)[:32] != sig:
        return None
    try:
        t = json.loads(raw)
    except Exception:
        return None
    if not isinstance(t, dict) or "c" not in t or "b" not in t:
        return None
    return t


# ══════════════════════════════════════════════════════════════
#  行为留痕：同一台设备今天干过什么
# ══════════════════════════════════════════════════════════════

# fp -> {"n": 今天提交次数, "bad": 连续失败, "at": 上次时间, "reg": 今天注册数}
_BEHAVIOR = {}


def _prune(now_ts):
    """清掉 24 小时没动静的记录，防止内存无限涨。"""
    if len(_BEHAVIOR) < 512:
        return
    for k in [k for k, v in _BEHAVIOR.items() if now_ts - (v.get("at") or 0) > 86400]:
        _BEHAVIOR.pop(k, None)


def note_track(req, fp, passed, kind="cap"):
    """记一笔行为。返回该设备今天的状态。"""
    t = time.time()
    _prune(t)
    day = int(t // 86400)
    v = _BEHAVIOR.setdefault(fp, {"n": 0, "bad": 0, "at": 0, "reg": 0, "d": day})
    if v.get("d") != day:
        v.update({"n": 0, "bad": 0, "reg": 0, "d": day})
    v["n"] = v.get("n", 0) + 1
    v["at"] = t
    v["bad"] = 0 if passed else v.get("bad", 0) + 1
    if kind == "reg":
        v["reg"] = v.get("reg", 0) + 1
    return v


def behavior_of(fp):
    v = _BEHAVIOR.get(fp)
    if not v:
        return {"n": 0, "bad": 0, "reg": 0}
    return v


def too_many_bad(fp, limit=3):
    """连续失败太多 —— 前面是在瞎试，不是人。"""
    return _BEHAVIOR.get(fp, {}).get("bad", 0) >= limit


def reg_today(fp):
    return _BEHAVIOR.get(fp, {}).get("reg", 0)
