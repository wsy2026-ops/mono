"""MONO 社区 · 访问防护底座（限流 / 真实 IP / 登录退避）

为什么需要：
    站里原有的限流只有 rate_allow(user_id)，全部按「用户 ID」计数。
    这留下两个大洞：
      1. 未登录入口（注册、登录、找回、静态探测）完全不受限 —— 攻击者最爱的就是这些；
      2. 攻击者批量注册小号即可绕过用户级限额，尤其能刷爆按 token 计费的 AI。
    本模块补上「IP 维度」这一层，作为整站防护的地基。

设计原则：
    1. 纯内存、零依赖 —— 所有计数在进程内，不碰数据库（限流绝不能成为性能瓶颈）
    2. 失败放行（fail-open）—— 内部出错时允许请求通过，绝不因防护逻辑本身把站点打死
    3. 自动清理 —— 后台惰性清理过期桶，避免长期运行内存膨胀
    4. 可配置 —— 阈值集中在顶部，也可通过 site_config 动态调整

关于真实 IP：
    上 Cloudflare / Nginx 反代后，request.remote_addr 会变成代理的 IP，
    此时必须从 X-Forwarded-For / CF-Connecting-IP 取真实访客 IP，否则限流形同虚设。
    但直接信任这些头又会被伪造绕过，所以这里用「可信代理层数」来控制信任边界。
"""
import os
import threading
import time

# Nginx 在 443 上终结 TLS 再转发给 waitress，源站不对外暴露，
# 所以 X-Forwarded-For 里的值只可能来自本机 Nginx，外部无法伪造。
# 深度按入口形态区分：
#   橙云（经 Cloudflare）：CF → Nginx → 应用，XFF 最右侧是 CF 节点，
#                          真人在 XFF 里再往左一位；CF 也会带 CF-Connecting-IP。
#   灰云（直连）：访客 → Nginx → 应用，XFF 只有访客一个地址。
# 两种都是「Nginx 前面恰好 1 个可信代理」，所以深度固定 1。
# 设 0 会让所有请求记成 127.0.0.1（同设备互邀防护、IP 记录全部失效）。
TRUSTED_PROXY_DEPTH = int(
    (os.environ.get("MONO_PROXY_DEPTH") or "1").strip() or "1")


# ── Cloudflare 回源网段 ────────────────────────────────────────────
# ⚠️ 2026-10-17 实测事故：`CF-Connecting-IP` 原来被**无条件信任** ✗
#    可这个站是**灰云直连**（没有 CF）✗ nginx 也不会设置这个头 ✗
#    → 任何人加一个 `CF-Connecting-IP: 9.9.9.9` 就能冒充任意 IP ✗
#    → 全站 IP 维度的防护（限流 / 对局风控 / 邀请活动）全部形同虚设 ✗
# 修法：**只有直接来源确实是 Cloudflare 时才认这个头** ✓
#   灰云直连时 remote_addr 是 127.0.0.1（本机 nginx）→ 不在下面任何网段 → 忽略 ✓
#   以后真上了橙云，remote_addr 会变成 CF 节点 IP → 自动开始信任 ✓
# 网段来源：https://www.cloudflare.com/ips/  （变动不频繁，半年核一次即可）
_CF_CIDRS = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)

try:
    import ipaddress as _ipaddress
    _CF_NETS = [_ipaddress.ip_network(c) for c in _CF_CIDRS]
except Exception:
    _CF_NETS = []


def _from_cloudflare(addr):
    """直接来源是不是 Cloudflare 的回源节点。

    ⚠️ 判的是 **remote_addr（直连对端）**，不是请求头里的任何东西 ✗
    头是攻击者能随便写的，对端地址他改不了 ✓
    """
    if not addr or not _CF_NETS:
        return False
    try:
        ip = _ipaddress.ip_address(addr)
    except ValueError:
        return False
    for net in _CF_NETS:
        try:
            if ip in net:
                return True
        except TypeError:      # IPv4 地址和 IPv6 网段比较会抛
            continue
    return False


def client_ip(req):
    """从请求中解析真实客户端 IP。

    信任模型：
      - TRUSTED_PROXY_DEPTH == 0：直接返回 remote_addr（不信任任何头，防伪造）
      - TRUSTED_PROXY_DEPTH >= 1：
          1) 只有**直连对端确实是 Cloudflare 节点**时，才认 CF-Connecting-IP
          2) 否则按层数从 X-Forwarded-For 右侧取
          3) 都没有就退回 remote_addr
    任何异常都退回 remote_addr，绝不抛出。
    """
    try:
        if TRUSTED_PROXY_DEPTH <= 0:
            return (req.remote_addr or "0.0.0.0")

        peer = (req.remote_addr or "").strip()
        peer_is_cf = _from_cloudflare(peer)

        # ⚠️ 这个头**必须先验来源**再用 ✗ 否则等于把 IP 交给攻击者填 ✗
        cf = (req.headers.get("CF-Connecting-IP") or "").strip()
        if cf and peer_is_cf:
            return cf

        xff = (req.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                # 经 CF 时链路上多一跳（CF 自己也会写进 XFF），所以层数 +1 ✓
                # 否则会取到 CF 节点的 IP，全站又变成共用一批限流桶 ✗
                depth = TRUSTED_PROXY_DEPTH + (1 if peer_is_cf else 0)
                idx = max(0, len(parts) - depth)
                return parts[idx]

        return (peer or "0.0.0.0")
    except Exception:
        try:
            return (req.remote_addr or "0.0.0.0")
        except Exception:
            return "0.0.0.0"


_buckets = {}
_bucket_lock = threading.Lock()
_last_gc = [0.0]
_GC_INTERVAL = 300
_MAX_BUCKETS = 20000


def _gc(now, max_window):
    """惰性清理：删除所有时间戳都已过期的桶。

    攻击者可以伪造大量不同 IP 来制造新桶（内存膨胀），
    所以这里除了按时间清理，还加了桶数量硬上限兜底。
    """
    if now - _last_gc[0] < _GC_INTERVAL:
        return
    _last_gc[0] = now
    dead = [k for k, v in _buckets.items()
            if not v or now - v[-1] > max_window]
    for k in dead:
        _buckets.pop(k, None)
    if len(_buckets) > _MAX_BUCKETS:
        items = sorted(_buckets.items(), key=lambda kv: kv[1][-1] if kv[1] else 0)
        for k, _ in items[:len(_buckets) - _MAX_BUCKETS]:
            _buckets.pop(k, None)


def hit(key, limit, window_sec, cost=1):
    """记一次访问并判断是否超限。

    返回 (allowed, remaining, retry_after_sec)：
      - allowed=False 表示应拦截，retry_after 是建议等待秒数
    纯内存滑动窗口；任何异常都返回放行（fail-open）。
    """
    try:
        now = time.time()
        with _bucket_lock:
            _gc(now, max(window_sec * 2, 3600))
            lst = [t for t in _buckets.get(key, []) if now - t < window_sec]
            if len(lst) >= limit:
                retry = int(window_sec - (now - lst[0])) + 1
                _buckets[key] = lst
                return False, 0, max(1, retry)
            for _ in range(max(1, cost)):
                lst.append(now)
            _buckets[key] = lst
            return True, max(0, limit - len(lst)), 0
    except Exception:
        return True, 1, 0


def peek(key, window_sec):
    """只看当前计数，不写入。用于退避判断。"""
    try:
        now = time.time()
        with _bucket_lock:
            return len([t for t in _buckets.get(key, []) if now - t < window_sec])
    except Exception:
        return 0


def reset(key):
    """清空某个 key 的计数（登录成功后清除失败记录）。"""
    try:
        with _bucket_lock:
            _buckets.pop(key, None)
    except Exception:
        pass


BACKOFF_STEPS = [0, 0, 0, 5, 15, 60, 180, 600, 1800, 3600]
USER_FAIL_THRESHOLD = 3
IP_FAIL_THRESHOLD = 25

_fail_state = {}
_fail_lock = threading.Lock()


def login_backoff_remaining(key):
    """返回还需等待的秒数；0 表示可以尝试登录。"""
    try:
        now = time.time()
        with _fail_lock:
            st = _fail_state.get(key)
            if not st:
                return 0
            left = int(st.get("until", 0) - now)
            return max(0, left)
    except Exception:
        return 0


def login_fail(key, threshold=USER_FAIL_THRESHOLD):
    """记录一次登录失败，返回当前已累计的失败次数。"""
    try:
        now = time.time()
        with _fail_lock:
            if len(_fail_state) > 10000:
                for k in [k for k, v in _fail_state.items() if v.get("until", 0) < now]:
                    _fail_state.pop(k, None)
            st = _fail_state.get(key) or {"n": 0, "until": 0}
            st["n"] += 1
            if st["n"] >= threshold:
                idx = min(st["n"] - threshold + 3, len(BACKOFF_STEPS) - 1)
                wait = BACKOFF_STEPS[idx]
                if wait:
                    st["until"] = now + wait
            _fail_state[key] = st
            return st["n"]
    except Exception:
        return 0


def login_ok(key):
    """登录成功：清掉失败计数。"""
    try:
        with _fail_lock:
            _fail_state.pop(key, None)
    except Exception:
        pass


def backoff_seconds_for(fail_count, threshold=USER_FAIL_THRESHOLD):
    """给定失败次数，返回下一次应等待的秒数（用于提示文案）。"""
    try:
        n = int(fail_count)
        if n < threshold:
            return 0
        idx = min(n - threshold + 3, len(BACKOFF_STEPS) - 1)
        return BACKOFF_STEPS[idx]
    except Exception:
        return 0


REGISTER_PER_HOUR = 10
REGISTER_PER_DAY = 40
LOGIN_PER_MIN = 60
GLOBAL_PER_MIN = 3000
UPLOAD_PER_DAY = 200
AI_PER_IP_DAY = 600
AI_GLOBAL_DAY_MAX = 3000

_TUNABLE = (
    "REGISTER_PER_HOUR", "REGISTER_PER_DAY", "LOGIN_PER_MIN", "GLOBAL_PER_MIN",
    "UPLOAD_PER_DAY", "AI_PER_IP_DAY", "AI_GLOBAL_DAY_MAX",
    "SIO_MAX_PER_IP", "SIO_CONNECT_PER_MIN", "TRUSTED_PROXY_DEPTH",
)


def configure(**kw):
    """运行时调整策略（app.py 从 site_config 读取后调用）。

    只接受 _TUNABLE 里的正整数键，非法值直接忽略（fail-safe）。
    返回实际生效的配置字典，便于启动时打印确认。
    """
    g = globals()
    applied = {}
    for k, v in kw.items():
        if k not in _TUNABLE:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n < 0:
            continue
        if k == "GLOBAL_PER_MIN" and n == 0:
            continue
        g[k] = n
        applied[k] = n
    return applied


def current_policy():
    """返回当前策略快照，供管理端展示。"""
    g = globals()
    return {k: g.get(k) for k in _TUNABLE}


def check_register(ip):
    """注册入口检查。返回 (ok, msg)。"""
    ok, _, retry = hit("reg:h:%s" % ip, REGISTER_PER_HOUR, 3600)
    if not ok:
        return False, "该网络注册过于频繁，请 %d 分钟后再试" % max(1, retry // 60)
    ok, _, _ = hit("reg:d:%s" % ip, REGISTER_PER_DAY, 86400)
    if not ok:
        return False, "该网络今日注册数已达上限"
    return True, ""


def check_login_attempt(ip, username):
    """登录尝试前的检查（IP 频率 + 账号/ IP 退避）。

    账号级退避是撞库的直接防线；IP 级退避阈值高得多（见 IP_FAIL_THRESHOLD），
    以免校园网里一个人输错密码导致全校被锁。
    """
    if username:
        left = login_backoff_remaining("u:%s" % username.lower())
        if left > 0:
            return False, "该账号连续登录失败，请 %d 秒后再试" % left
    left = login_backoff_remaining("ip:%s" % ip)
    if left > 0:
        return False, "尝试次数过多，请 %d 秒后再试" % left
    ok, _, retry = hit("login:%s" % ip, LOGIN_PER_MIN, 60)
    if not ok:
        return False, "请求过于频繁，请 %d 秒后再试" % retry
    return True, ""


def note_login_fail(ip, username):
    """记录登录失败，返回给用户看的提示文案。"""
    n_user = 0
    if username:
        n_user = login_fail("u:%s" % username.lower(), USER_FAIL_THRESHOLD)
    login_fail("ip:%s" % ip, IP_FAIL_THRESHOLD)
    wait = backoff_seconds_for(n_user, USER_FAIL_THRESHOLD)
    if wait:
        return "用户名或密码错误（连续失败 %d 次，请等待 %d 秒后重试）" % (n_user, wait)
    return "用户名或密码错误"


def note_login_success(ip, username):
    """登录成功，清掉该 IP 与账号的失败计数。"""
    login_ok("ip:%s" % ip)
    if username:
        login_ok("u:%s" % username.lower())


def check_ai_ip(ip):
    """AI 入口的单 IP 日限额。"""
    ok, _, _ = hit("ai:ip:%s" % ip, AI_PER_IP_DAY, 86400)
    if not ok:
        return False, "该网络今日 AI 使用已达上限，请明天再试"
    return True, ""


MAIL_PER_IP_HOUR = 10
MAIL_PER_MAIL_HOUR = 3


def check_mail_send(ip, email):
    """发邮箱验证码的频控：单 IP 和单邮箱各一份。

    邮件接口一旦被人拿去轰炸，收件人的邮箱会进垃圾箱，
    域名信誉也会掉 —— 所以发信比登录更该掐紧。
    """
    ok, _, _ = hit("mail:ip:%s" % ip, MAIL_PER_IP_HOUR, 3600)
    if not ok:
        return False, "发信太频繁了，等一小时再来"
    if email:
        ok, _, _ = hit("mail:to:%s" % email.strip().lower(), MAIL_PER_MAIL_HOUR, 3600)
        if not ok:
            return False, "这个邮箱发得太频繁了，等一小时再来"
    return True, ""


_ai_global = {"day": "", "count": 0}
_ai_glock = threading.Lock()


def _today():
    return time.strftime("%Y-%m-%d")


def ai_global_budget_ok():
    """当天全站 AI 调用是否还在预算内。超限返回 False —— 熔断。"""
    try:
        with _ai_glock:
            d = _today()
            if _ai_global["day"] != d:
                _ai_global["day"] = d
                _ai_global["count"] = 0
            return _ai_global["count"] < AI_GLOBAL_DAY_MAX
    except Exception:
        return True


def ai_global_consume():
    """消耗一次全局 AI 预算，返回当天累计用量。"""
    try:
        with _ai_glock:
            d = _today()
            if _ai_global["day"] != d:
                _ai_global["day"] = d
                _ai_global["count"] = 0
            _ai_global["count"] += 1
            return _ai_global["count"]
    except Exception:
        return 0


def ai_global_status():
    """返回 (当天用量, 上限)，供管理端展示。"""
    try:
        with _ai_glock:
            d = _today()
            if _ai_global["day"] != d:
                return 0, AI_GLOBAL_DAY_MAX
            return _ai_global["count"], AI_GLOBAL_DAY_MAX
    except Exception:
        return 0, AI_GLOBAL_DAY_MAX


SIO_MAX_PER_IP = 50
SIO_CONNECT_PER_MIN = 120

_sio_conns = {}
_sio_lock = threading.Lock()


def sio_accept(ip):
    """是否允许建立新连接。返回 (ok, reason)。"""
    try:
        now = time.time()
        ok, _, _ = hit("sio:c:%s" % ip, SIO_CONNECT_PER_MIN, 60)
        if not ok:
            return False, "connect_rate"
        with _sio_lock:
            cur = _sio_conns.get(ip, 0)
            if cur >= SIO_MAX_PER_IP:
                return False, "too_many_conns"
            _sio_conns[ip] = cur + 1
        return True, ""
    except Exception:
        return True, ""


def sio_release(ip):
    """连接断开时释放计数。"""
    try:
        with _sio_lock:
            cur = _sio_conns.get(ip, 0)
            if cur <= 1:
                _sio_conns.pop(ip, None)
            else:
                _sio_conns[ip] = cur - 1
    except Exception:
        pass
