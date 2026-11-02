"""邀请得现金 —— 活动核心逻辑。

和 xqmaster 一样是纯逻辑模块：不碰 Flask、不碰 request，
只吃 (db, 参数) 返回结果，路由层负责取 session / request。

状态流转
--------
被邀请记录 ic_invites.status：

    pending   已绑定，等被邀请人攒够「连续两天、每天在线满 1 小时」
    qualified 已达标，计入邀请人的有效邀请数
    rejected  永久失效（IP 风控 / 互邀 / 邀请码无效），不再翻案
    paid      已随结算打款（活动期内不会走到，留给后续周期）

达标判定（status 内部再记进度，不另开表）：
    day1 / day2  达标那两天的 unix 时间戳，0 = 未达成
    streak_start 第一天日期（YYYY-MM-DD），用来保证「连续」不是跳日

结算 ic_settlements.status：
    pending  已算出金额，等人工打款
    paid     已打款
"""

import os
import time
import secrets
import datetime

PERIOD = "2026-10"

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# 邀请码格式：**6 位纯数字**（2026-10-09 从「8 位字母数字混合」改过来，方便口头/短信传）。
# 6 位数字只有 100 万种组合，所以 gen_code 的重试循环必须留着 —— 撞了就再抽一次。
CODE_DIGITS = 6

DEFAULT_TEXT = {
    "title": "邀请得现金",
    "subtitle": "把你的朋友叫来 MONO，达标就拿现金",
    "rule_1": "活动对象",
    "rule_2": "怎么算有效邀请",
    "rule_3": "风控",
    "rule_4": "奖励标准",
    "rule_5": "结算",
    "rule_6": "其他",
    # ⚠️ 这里以前写的是「9 月 25 日 23:59 之前注册的老用户才有名额」✗
    # 但 eligible() 在 2026-10-09 就已经取消注册时间限制了 ✗ 文案没跟着改 ✗
    # 线上靠 page_blocks 的覆盖盖住了，所以页面上看不出问题；
    # 可一旦覆盖丢了/新装环境，就会显示成把新用户挡在门外的旧规则 ✗
    "rule_1_body": "全体 MONO 用户，注册后系统会给你一个专属邀请码。",
    "rule_2_body": "朋友在注册页填你的邀请码完成注册后，需要在活动期间挑任意两天来站点，并且每天累计在线满 1 小时。两天都达标，这笔邀请就算数（不要求挨着）。",
    "rule_3_body": "同一台设备上的账号不能互相邀请（系统会记录设备指纹，换账号也认得出）。这是为了防止开小号互刷 —— 同一设备上的多个账号各自都可以正常参与活动，只是不能互相拉。",
    "rule_4_body": "前 8 人按 0.5 元/人结算；从第 9 人起，超出部分按 1 元/人结算。前 8 人始终是 0.5 元/人，分段累加。",
    "rule_5_body": "活动结束后统一核算，金额发到你的钱包，请确保收款方式可用。",
    "rule_6_body": "邀请关系一经绑定不可修改、不可转移。禁止互相邀请、禁止用小号给自己刷人数（同一台设备上的账号不能互邀）。一经发现本次邀请作废且取消资格。",
    "faq_title": "常见问题",
    "faq_1": "我什么时候算达标？",
    "faq_1_body": "被邀请的朋友注册之后，在活动期间任意两天来站点、每天在线满 1 小时，就算一次有效邀请。两天不要求挨着，10 号和 15 号各来满一小时一样算数。",
    "faq_2": "朋友注册时没填我的邀请码怎么办？",
    "faq_2_body": "邀请关系只能在注册那一步建立，注册完成后无法补填。所以要提醒朋友先拿邀请码再注册。",
    "faq_3": "同一台设备只能有一个人参与吗？",
    "faq_3_body": "不是。同一台设备上的多个账号各自都能参与，只是不能互相邀请（防止开小号对刷）。另外同一 WiFi、同一学校出口这类共用网络的情况不受任何限制 —— 你和同学各拉各的，互不影响。",
    "faq_4": "金额怎么算的？",
    "faq_4_body": "分段累加：前 8 人每人 0.5 元，超出 8 人的部分每人 1 元。例：邀到 10 人 = 8×0.5 + 2×1 = 6 元；邀到 8 人 = 8×0.5 = 4 元；邀到 12 人 = 8×0.5 + 4×1 = 8 元。",
    "faq_5": "活动什么时候结束？",
    "faq_5_body": "活动时间从 10 月 10 日 00:00 到 10 月 16 日 23:59。期间随时可以邀请，但请提醒朋友尽快达标 —— 活动结束后新注册的不再计入。",
}

_ELIGIBLE_BEFORE = datetime.datetime(2026, 9, 25, 23, 59, 59).timestamp()
_ACT_START = datetime.datetime(2026, 10, 9, 20, 0, 0).timestamp()
_ACT_END = datetime.datetime(2026, 10, 16, 23, 59, 59).timestamp()

# 调试用：IC_FORCE_STATE=before|running|over 可以强制活动时间状态，
# 方便在活动开始前预览页面、结束后查结算。线上不设这个变量。
_force = (os.environ.get("IC_FORCE_STATE") or "").strip().lower()

# 分段累加：前 TIER_1_MIN 人按 TIER_2_RATE 结算，超出部分按 TIER_1_RATE 结算。
# 2026-10-10 调整：门槛 10 → 8 人，超出单价 0.8 → 1.0 元。
TIER_1_MIN = 8
TIER_1_RATE = 1.0
TIER_2_RATE = 0.5

DAILY_SECONDS = 3600


def now():
    return time.time()


def day_str(ts=None):
    ts = now() if ts is None else ts
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def act_state(ts=None):
    if _force in ("before", "running", "over"):
        return _force
    ts = now() if ts is None else ts
    if ts < _ACT_START:
        return "before"
    if ts > _ACT_END:
        return "over"
    return "running"


def _cn_ts(ts, with_clock=True):
    """把时间戳转成「10 月 16 日 23:59」这种中文写法，跨平台一致。"""
    d = datetime.datetime.fromtimestamp(ts)
    s = "%d 月 %d 日" % (d.month, d.day)
    if with_clock:
        s += " %02d:%02d" % (d.hour, d.minute)
    return s


def act_window():
    st = act_state()
    start, end = _ACT_START, _ACT_END
    if st == "running" and not (start <= now() <= end):
        start, end = now() - 86400, now() + 86400 * 4
    return {
        "start": start,
        "end": end,
        "start_text": _cn_ts(start),
        "end_text": _cn_ts(end),
    }


def eligible(user):
    """是否符合参与资格。

    2026-10-09 起**取消注册时间限制** —— 所有登录用户都能参加。
    （原来要求 9 月 25 日之前注册，等于把新用户直接挡在门外。）
    """
    return bool(user)


WECHAT_MAX = 40


def norm_wechat(v):
    """规范化微信号：去空白、限长。只做清洗，不校验格式。"""
    return (v or "").strip()[:WECHAT_MAX]


def wechat_used_by(db, wechat, exclude_uid=0):
    """这个微信号是不是已经被别人填过了（风控：一号只能领一份）。"""
    w = norm_wechat(wechat)
    if not w:
        return 0
    row = db.execute("SELECT user_id FROM ic_coders WHERE wechat=? AND user_id<>? LIMIT 1",
                     (w, int(exclude_uid or 0))).fetchone()
    return int(row["user_id"]) if row else 0


def set_wechat(db, user_id, wechat):
    """保存微信号。

    风控：**同一个微信号只能被一个账号使用** —— 否则一个人开十个号填同一个微信，
    结算时十份都打给他。这里直接拒绝，让他换自己的号。
    """
    w = norm_wechat(wechat)
    if not db.execute("SELECT 1 FROM ic_coders WHERE user_id=?", (user_id,)).fetchone():
        return False, "请先回到活动页领取邀请码"
    if w:
        other = wechat_used_by(db, w, user_id)
        if other:
            return False, "这个微信号已被其他账号填写，请填自己的微信号"
    db.execute("UPDATE ic_coders SET wechat=? WHERE user_id=?", (w, user_id))
    db.commit()
    return True, ""


def get_wechat(db, user_id):
    row = db.execute("SELECT wechat FROM ic_coders WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return ""
    try:
        return row["wechat"] or ""
    except (IndexError, KeyError):
        return ""


def gen_code(db):
    """生成唯一邀请码：6 位纯数字。

    ⚠️ 首位**不用 0**：抽到 045102 这种码，用户口头报码或手输时极容易漏掉那个 0
    （「082846」被念成「82846」），然后就对不上、要来找客服 ✗
    代价只是码池从 100 万缩到 90 万，无所谓。
    """
    for _ in range(60):
        c = secrets.choice("123456789") + "".join(
            secrets.choice("0123456789") for _ in range(CODE_DIGITS - 1))
        if not db.execute("SELECT 1 FROM ic_coders WHERE code=?", (c,)).fetchone():
            return c
    # 极小概率：连抽 60 次都撞（码表接近满）。退化成 7 位继续保证唯一。
    for _ in range(60):
        c = secrets.choice("123456789") + "".join(
            secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        if not db.execute("SELECT 1 FROM ic_coders WHERE code=?", (c,)).fetchone():
            return c
    raise RuntimeError("邀请码池已满，无法生成新码")


def ensure_code(db, user_id, fp_digest=""):
    """取邀请码，没有就发一个。返回 (code, is_new)。

    fp_digest 只在第一次发码时写入 —— 设备指纹会变（换浏览器、清缓存），
    但已经参与过的人不该因为指纹变了就变成另一个人。
    """
    row = db.execute("SELECT code FROM ic_coders WHERE user_id=?", (user_id,)).fetchone()
    if row:
        return row["code"], False
    code = gen_code(db)
    db.execute("INSERT INTO ic_coders (user_id, code, fp_digest, created_at) VALUES (?,?,?,?)",
               (user_id, code, (fp_digest or "")[:24], now()))
    db.commit()
    return code, True


def find_by_code(db, code):
    if not code:
        return None
    c = (code or "").strip().upper()
    if not c:
        return None
    return db.execute("SELECT * FROM ic_coders WHERE code=?", (c,)).fetchone()


def regen_all_codes(db, dry_run=False):
    """把码表里所有码重新生成为 6 位数字（一次性迁移）。

    ⚠️ 老码会**全部作废** —— 已经发出去、别人已经填过的码就失效了。
    2026-10-09 用户明确要求这么做（改成 6 位纯数字，老码不要了）。

    返回 [(user_id, 老码, 新码), ...]。
    """
    rows = db.execute("SELECT user_id, code FROM ic_coders ORDER BY user_id").fetchall()
    out = []
    used = set()
    for r in rows:
        uid, old = r[0], r[1]
        # 先临时占位，避免新码之间自己撞（首位同样不用 0）
        while True:
            new = secrets.choice("123456789") + "".join(
                secrets.choice("0123456789") for _ in range(CODE_DIGITS - 1))
            if new in used:
                continue
            if db.execute("SELECT 1 FROM ic_coders WHERE code=? AND user_id!=?",
                          (new, uid)).fetchone():
                continue
            break
        used.add(new)
        out.append((uid, old, new))
        if not dry_run:
            db.execute("UPDATE ic_coders SET code=? WHERE user_id=?", (new, uid))
    if not dry_run:
        db.commit()
    return out


def amount_for(n):
    """分段累加：前 8 人每人 0.5 元，超出 8 人的部分每人 1 元。

    例：邀 10 人 = 8×0.5 + 2×1 = 6 元；邀 12 人 = 8×0.5 + 4×1 = 8 元。
    （以前是「按最终人数取一档」——满门槛全部按高档算，不分段。）
    """
    n = max(0, int(n or 0))
    base = min(n, TIER_1_MIN) * TIER_2_RATE
    extra = max(0, n - TIER_1_MIN) * TIER_1_RATE
    return round(base + extra, 2)


def rate_for(n):
    """**下一位**邀请人的单价（边际单价），不是总额的平均单价。"""
    n = max(0, int(n or 0))
    return TIER_1_RATE if n >= TIER_1_MIN else TIER_2_RATE


def next_tier(n):
    """距离下一档还差几个。"""
    n = max(0, int(n or 0))
    if n >= TIER_1_MIN:
        return None
    return TIER_1_MIN - n


def settle_period():
    return PERIOD


def valid_count(db, user_id):
    r = db.execute(
        "SELECT COUNT(*) c FROM ic_invites WHERE inviter_id=? AND status IN ('qualified','paid')",
        (user_id,)).fetchone()
    return int(r["c"]) if r else 0


def pending_count(db, user_id):
    r = db.execute(
        "SELECT COUNT(*) c FROM ic_invites WHERE inviter_id=? AND status='pending'",
        (user_id,)).fetchone()
    return int(r["c"]) if r else 0


def reward_of(db, user_id):
    n = valid_count(db, user_id)
    return {
        "valid": n,
        "pending": pending_count(db, user_id),
        "rate": rate_for(n),
        "amount": amount_for(n),
        "tier": 1 if n >= TIER_1_MIN else 2,
        "to_next": next_tier(n),
    }


def settlement_of(db, user_id):
    row = db.execute("SELECT * FROM ic_settlements WHERE user_id=? AND period=?",
                     (user_id, settle_period())).fetchone()
    return dict(row) if row else None


def settlement_map(db, period=None):
    period = period or settle_period()
    rows = db.execute("SELECT * FROM ic_settlements WHERE period=?", (period,)).fetchall()
    out = {}
    for r in rows:
        out[int(r["user_id"])] = dict(r)
    return out


def _ancestor_ids(db, uid, limit=40):
    """沿邀请链往上找祖先（谁把这个人拉来的，以及拉他的人的拉的人……）。"""
    out = set()
    cur = int(uid)
    for _ in range(limit):
        row = db.execute(
            "SELECT inviter_id FROM ic_invites WHERE invitee_id=?", (cur,)).fetchone()
        if not row:
            break
        p = int(row["inviter_id"])
        if p in out or p == cur:
            break
        out.add(p)
        cur = p
    return out


def _fp_of(db, uid):
    row = db.execute("SELECT fp_digest FROM ic_coders WHERE user_id=?", (uid,)).fetchone()
    return (row["fp_digest"] or "") if row else ""


def bind_invite(db, inviter_id, invitee_id, code, reg_ip, fp_digest=""):
    """注册时建立邀请关系。

    返回 (ok, reason)。失败原因都是给用户看的短句。
    这里不抛异常 —— 注册主流程不能因为活动出问题就失败。
    """
    if not inviter_id or not invitee_id:
        return False, ""
    if int(inviter_id) == int(invitee_id):
        return False, "不能填写自己的邀请码"

    inviter = db.execute("SELECT * FROM users WHERE id=?", (inviter_id,)).fetchone()
    if not inviter:
        return False, "邀请码无效"
    if not eligible(inviter):
        return False, "该邀请码的账号没有活动资格"
    if act_state() != "running":
        return False, "活动不在进行中"

    if db.execute("SELECT 1 FROM ic_invites WHERE invitee_id=?", (invitee_id,)).fetchone():
        return False, "该账号已绑定过邀请关系"

    # 互邀：A 邀了 B，B 不能再反过来邀 A（会形成环或对刷）
    if int(inviter_id) in _ancestor_ids(db, invitee_id):
        return False, "不能邀请曾邀请过你的人"
    if int(invitee_id) in _ancestor_ids(db, inviter_id):
        return False, "不能邀请自己已经邀请过的人"

    # 同一设备上的人互相邀请 = 小号互刷。两种都要查：
    #   a) 新人和邀请人同设备 —— 老板拿自己的另一台设备刷
    #   b) 新人和邀请人已有的某个下线同设备 —— 几个小号互相刷
    fp = (fp_digest or "").strip()
    if fp:
        if _fp_of(db, inviter_id) == fp:
            return False, "同一设备上的账号不能互相邀请"
        same = db.execute(
            "SELECT 1 FROM ic_coders c JOIN ic_invites i ON i.invitee_id = c.user_id"
            " WHERE i.inviter_id = ? AND c.fp_digest = ? LIMIT 1",
            (inviter_id, fp)).fetchone()
        if same:
            return False, "同一设备上的账号不能互相邀请"

    # ⚠️ 这里原来还有一条「同一个 IP 只有第一个账号能参与」的判定 ✗
    # 2026-10-10 用户确认**去掉** ✗ 原因：
    #   手机流量走运营商 CGNAT，一个公网 IP 后面是成百上千个真实用户 ✗
    #   → 同校/同城的学生用流量时几乎必然撞 IP，第一个注册的占坑、
    #     后面的人**永久被拒**（这条查全表、没有时间窗）✗ 而且换不掉 IP、没法申诉 ✗
    #   而它想防的「同 WiFi 开小号」，上面那段**设备指纹**已经精确挡住了 ✓
    # 所以：reg_ip 仍然照记（留着查滥用用 ✓），但**不再参与拦截** ✓
    ip = (reg_ip or "").strip()

    db.execute(
        "INSERT INTO ic_invites (inviter_id, invitee_id, code, reg_ip, status, created_at)"
        " VALUES (?,?,?,?,'pending',?)",
        (inviter_id, invitee_id, code or "", ip, now()))
    db.commit()
    return True, ""


def record_online(db, user_id, seconds):
    """累加某天的在线时长。只在心跳里调，单次增量很小。"""
    if not user_id or seconds <= 0:
        return
    day = day_str()
    db.execute(
        "INSERT INTO ic_daily (user_id, day, seconds) VALUES (?,?,?)"
        " ON CONFLICT(user_id, day) DO UPDATE SET seconds = seconds + ?",
        (user_id, day, float(seconds), float(seconds)))


def _day_map(db, user_id, days=14):
    """取最近若干天的在线时长，key 是 YYYY-MM-DD。"""
    out = {}
    if not user_id:
        return out
    since = day_str(now() - days * 86400)
    rows = db.execute(
        "SELECT day, seconds FROM ic_daily WHERE user_id=? AND day>=?",
        (user_id, since)).fetchall()
    for r in rows:
        out[r["day"]] = float(r["seconds"])
    return out


def _floating_day(base_day):
    """相对某个日期往后推 n 天，返回 YYYY-MM-DD。"""
    d = datetime.datetime.strptime(base_day, "%Y-%m-%d")
    return (d + datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def check_invites(db, inviter_id, limit=200):
    """重算某个邀请人名下所有 pending 记录的达标状态。

    判定：活动期间**任意两天**，每天累计在线满 1 小时。
    不要求相邻 —— 10 号和 15 号各来满一小时同样算数。
    取满足条件的最早两天来记达标时间。
    """
    rows = db.execute(
        "SELECT * FROM ic_invites WHERE inviter_id=? AND status='pending' LIMIT ?",
        (inviter_id, limit)).fetchall()
    if not rows:
        return 0
    changed = 0
    ts = now()
    for r in rows:
        dm = _day_map(db, r["invitee_id"])
        if not dm:
            continue
        full = sorted(d for d, s in dm.items() if s >= DAILY_SECONDS)
        if len(full) < 2:
            continue
        d1 = datetime.datetime.strptime(full[0], "%Y-%m-%d")
        d2 = datetime.datetime.strptime(full[1], "%Y-%m-%d")
        db.execute(
            "UPDATE ic_invites SET status='qualified', day1=?, day2=?,"
            " streak_start=?, qualified_at=? WHERE id=?",
            (d1.timestamp(), d2.timestamp(), full[0], ts, r["id"]))
        changed += 1
    if changed:
        db.commit()
    return changed


def invite_records(db, inviter_id, limit=200):
    rows = db.execute(
        "SELECT i.*, u.username, u.created_at AS u_created"
        " FROM ic_invites i LEFT JOIN users u ON u.id = i.invitee_id"
        " WHERE i.inviter_id=? ORDER BY"
        " CASE i.status WHEN 'qualified' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,"
        " i.created_at DESC LIMIT ?",
        (inviter_id, limit)).fetchall()
    out = []
    ts = now()
    for r in rows:
        d = dict(r)
        d["progress_text"] = _progress_text(db, d, ts)
        d["qualified_text"] = ""
        if d.get("qualified_at"):
            d["qualified_text"] = datetime.datetime.fromtimestamp(
                float(d["qualified_at"])).strftime("%m-%d %H:%M")
        out.append(d)
    return out


def _progress_text(db, row, ts=None):
    """给记录列表显示一句进度：今天还差多少分钟。"""
    if row["status"] == "qualified":
        return "已达标"
    if row["status"] == "rejected":
        return row["reject_reason"] or "已失效"
    if row["status"] == "paid":
        return "已达标 · 已结算"
    secs = _day_map(db, row["invitee_id"]).get(day_str(ts), 0.0)
    if secs > 0:
        need = DAILY_SECONDS - secs
        return "今日在线 %d 分钟，还差 %d 分钟" % (
            int(secs // 60), int(need // 60) + (1 if need % 60 else 0))
    return "今日尚未在线，满 1 小时后才开始计算"


def _text_override(db, key):
    row = db.execute("SELECT content FROM page_blocks WHERE page='invite_cash' AND key=?",
                     (key,)).fetchone()
    if row and row["content"]:
        return row["content"]
    return DEFAULT_TEXT.get(key, "")


def texts(db):
    out = {}
    for k in DEFAULT_TEXT:
        out[k] = _text_override(db, k)
    return out


def set_text(db, key, content, who=""):
    if key not in DEFAULT_TEXT:
        return False
    db.execute(
        "INSERT INTO page_blocks (page, key, content, updated_at, updated_by)"
        " VALUES ('invite_cash', ?,?,?,?)"
        " ON CONFLICT(page, key) DO UPDATE SET content=excluded.content,"
        " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, content or "", now(), who or ""))
    db.commit()
    return True


def text_keys():
    return sorted(DEFAULT_TEXT.keys())


def meta_get(db, key, default=""):
    row = db.execute("SELECT value FROM ic_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(db, key, value):
    db.execute(
        "INSERT INTO ic_meta (key, value) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)))
    db.commit()


def admin_table(db, period=None):
    """超管看的结算总表：每个参与者一行 —— 微信号 / 邀请人数 / 应付金额。

    按「应付金额降序」排，金额相同按人数降序 —— 打款时从上往下打最省事。
    没微信号的也列出来（标红），方便去催。
    """
    period = period or settle_period()
    rows = db.execute(
        "SELECT i.inviter_id, COUNT(*) c FROM ic_invites i"
        " WHERE i.status IN ('qualified','paid') GROUP BY i.inviter_id").fetchall()
    out = []
    for r in rows:
        uid = int(r["inviter_id"])
        n = int(r["c"])
        wx = get_wechat(db, uid)
        u = db.execute("SELECT username, user_no FROM users WHERE id=?", (uid,)).fetchone()
        st = db.execute("SELECT * FROM ic_settlements WHERE user_id=? AND period=?",
                        (uid, period)).fetchone()
        dup = bool(wx) and bool(wechat_used_by(db, wx, uid))
        if not wx:
            amt, status, note = 0.0, "forfeit", "未填微信号"
        elif dup:
            amt, status, note = 0.0, "forfeit", "微信号重复"
        else:
            amt = amount_for(n)
            status = (st["status"] if st else "pending")
            note = (st["note"] if st else "") or ""
        out.append({
            "uid": uid,
            "username": (u["username"] if u else str(uid)),
            "user_no": (u["user_no"] if u else ""),
            "wechat": wx,
            "count": n,
            "amount": amt,
            "status": status,
            "note": note,
        })
    out.sort(key=lambda x: (-x["amount"], -x["count"]))
    total = round(sum(x["amount"] for x in out), 2)
    return {"rows": out, "total": total, "period": period,
            "payable": len([x for x in out if x["amount"] > 0])}


def make_settlements(db, period=None, threshold=1):
    """按最终有效人数给所有有有效邀请的人生成结算单。

    幂等：已存在的 (user_id, period) 不覆盖，除非金额变了才更新。
    只给 valid_count >= threshold 的人建单，避免给 0 人建一堆空记录。
    """
    period = period or settle_period()
    rows = db.execute(
        "SELECT inviter_id, COUNT(*) c FROM ic_invites"
        " WHERE status IN ('qualified','paid') GROUP BY inviter_id").fetchall()
    ts = now()
    made = 0
    for r in rows:
        n = int(r["c"])
        if n < threshold:
            continue
        amt = amount_for(n)
        # 没填微信号 = 自动放弃领奖资格（结算单照建，但金额归零并标明原因）
        wx = get_wechat(db, r["inviter_id"])
        if not wx:
            amt = 0.0
            st, note = "forfeit", "未填写微信号，自动放弃领奖资格"
        elif wechat_used_by(db, wx, r["inviter_id"]):
            # 风控：同一个微信号挂在多个账号上 → 两边都作废，别让人一号多领
            amt = 0.0
            st, note = "forfeit", "微信号与其他账号重复，自动放弃领奖资格"
        else:
            st, note = "pending", ""
        old = db.execute(
            "SELECT * FROM ic_settlements WHERE user_id=? AND period=?",
            (r["inviter_id"], period)).fetchone()
        if old:
            if (abs(float(old["amount"]) - amt) > 0.001 or int(old["valid_count"]) != n
                    or (old["status"] or "") != st):
                db.execute(
                    "UPDATE ic_settlements SET valid_count=?, amount=?, status=?, note=?,"
                    " updated_at=? WHERE id=?", (n, amt, st, note, ts, old["id"]))
        else:
            db.execute(
                "INSERT INTO ic_settlements (user_id, period, valid_count, amount,"
                " status, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (r["inviter_id"], period, n, amt, st, note, ts, ts))
            made += 1
    db.commit()
    return made


def _backfill_fp(db, uid, fp=""):
    """老用户第一次进活动页时把设备指纹记上。

    之前注册的账号 ic_coders 里没有 fp_digest（那时候还没这概念），
    不补的话「同设备不能互邀」这条对他们一直是空的。
    只在当前为空时写一次 —— 换设备登录不该把老记录改掉。
    """
    if not fp:
        return
    try:
        db.execute("UPDATE ic_coders SET fp_digest=? WHERE user_id=? AND IFNULL(fp_digest,'')=''",
                   (fp[:24], uid))
        db.commit()
    except Exception:
        pass


def dashboard(db, user, fp=""):
    """活动页需要的全部数据。"""
    uid = int(user["id"])
    ok = eligible(user)
    _wx = get_wechat(db, user["id"]) if user else ""
    code, _ = ensure_code(db, uid) if ok else ("", False)
    if ok:
        _backfill_fp(db, uid, fp)
    state = act_state()
    if ok and state == "running":
        check_invites(db, uid)
    rw = reward_of(db, uid) if ok else {
        "valid": 0, "pending": 0, "rate": TIER_2_RATE, "amount": 0.0,
        "tier": 2, "to_next": TIER_1_MIN}
    st = settlement_of(db, uid) if ok else None
    return {
        "eligible": ok,
        "wechat": _wx,
        "has_wechat": bool(_wx),
        "code": code,
        "state": state,
        "window": act_window(),
        "reward": rw,
        "settlement": st,
        "records": invite_records(db, uid) if ok else [],
        "texts": texts(db),
    }
