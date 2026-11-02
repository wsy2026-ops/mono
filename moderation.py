# -*- coding: utf-8 -*-
"""moderation.py — 053 审核四层防线（HK 侧中枢）。

四层（文档设计）：
  ① ToolGood.Words 词库硬过滤 —— 现有 filter_and_block，发布前同步挡（已有）
  ② roberta-base-cold（HK onnx，49ms 同步）—— cold_guard.judge
  ③ Qwen3Guard-0.6B（LA 异步队列）—— ai_queue.enqueue_moderate
  ④ 人工审核队列 —— /admin/ai-review

数据流：
  发帖/评论成功 → submit() 写 ai_moderation(pending)
    → ② cold 同步判 → 更新 cold_level/score
    → ③ 入队 LA，记 job_id
  → sweep() 后台线程每 10s 取 job 结果 → 更新 llm_level/score
  → 状态机：
      任一层 bad      → status='hidden'（自动隐藏，等人工复核）
      任一层 suspect  → status='flagged'（进人工队列）
      两层都 ok/skip  → status='cleared'

设计原则：**审核绝不挡发布**（异步）；模型不可用降级为 skip（不误伤）；
自动隐藏只隐藏不删（人工复核可恢复）。
"""
import json
import requests
import threading
import time

TABLE = "ai_moderation"

_ensured = False
_ensure_lock = threading.Lock()
_sweep_started = False


def ensure_table(db):
    """建表（幂等）。参照 MEMORY 规则：代码里 CREATE IF NOT EXISTS，不碰 init_db。"""
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        db.execute("""
            CREATE TABLE IF NOT EXISTS ai_moderation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,      -- post / comment / pm
                target_id INTEGER NOT NULL,
                user_id INTEGER,
                title TEXT,
                body TEXT,
                cold_level TEXT,                -- ok/suspect/bad/skip
                cold_score REAL,
                cold_why TEXT,                  -- 规则层命中的具体原因（给作者看）
                llm_level TEXT,                 -- ok/suspect/bad/skip
                llm_score REAL,
                llm_why TEXT,
                job_id TEXT,
                status TEXT DEFAULT 'pending',  -- pending/cleared/flagged/hidden/reviewed
                reviewed_by INTEGER,
                reviewed_at REAL,
                created_at REAL,
                updated_at REAL
            )""")
        try:
            db.execute("ALTER TABLE ai_moderation ADD COLUMN cold_why TEXT")
        except Exception:
            pass
        db.execute("CREATE INDEX IF NOT EXISTS idx_aim_status ON ai_moderation(status, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_aim_job ON ai_moderation(job_id)")
        db.commit()
        _ensured = True


def _row_get(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def meaningless_check(title, body):
    """无意义内容检测（纯规则，不走 LLM）。

    拦的是「标题 1、正文 1」这类 —— 不判断观点对错，只看有没有实际信息。
    **阈值故意定得很松**：宁可漏过几条灌水，也别误杀正常帖。
    唯一会放宽的是带图的帖子（图片帖正文短是正常的）。
    """
    t = (title or "").strip()
    b = (body or "").strip()

    # 带图的帖子不卡正文长度（图片本身就是内容）
    has_media = ("<img" in b.lower()) or ("![" in b) or ("<video" in b.lower())

    if len(t) < 2:
        return True, "标题至少要有 2 个字"
    if not has_media and len(b) < 5:
        return True, "正文至少要有 5 个字"

    # 单字符重复：1111 / aaaa / 。。。。
    if len(set(t)) <= 1:
        return True, "标题是同一个字重复"
    if not has_media and len(b) <= 20 and len(set(b)) <= 2:
        return True, "正文是重复字符"

    # 标题正文一模一样且都很短
    if t == b and len(t) <= 8:
        return True, "标题和正文完全一样，且都很短"

    # 有效文字占比过低（一屏符号/表情）
    if not has_media and len(b) >= 10:
        valid = sum(1 for c in b if c.isalnum() or "\u4e00" <= c <= "\u9fff")
        if valid / float(len(b)) < 0.3:
            return True, "正文里几乎没有文字"

    return False, ""


def submit(db, target_type, target_id, user_id, title, body):
    """发布成功后调用：cold 同步判 + LA 入队 + 写表。

    返回 moderation 行 id（失败返回 None，绝不影响发布）。
    """
    try:
        ensure_table(db)
        text = ((title or "") + "\n" + (body or "")).strip()
        now = time.time()
        cur = db.execute(
            "INSERT INTO ai_moderation (target_type, target_id, user_id, title, body, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?, 'pending', ?, ?)",
            (target_type, target_id, user_id, (title or "")[:200], (body or "")[:4000],
             now, now))
        mid = cur.lastrowid
        db.commit()

        # ①.4 无意义内容层（标题 1 / 正文 1 这种）
        # ①.5 广告规则层（LLM 不擅长广告，规则兜底）
        ml_hit, ml_why = meaningless_check(title, body)
        ad_hit, ad_why = ad_check(text)
        if ml_hit:
            cold = {"level": "bad", "score": 1.0, "why": "无意义内容：%s" % ml_why}
        elif ad_hit:
            cold = {"level": "bad", "score": 1.0, "why": "广告规则命中：%s" % ad_why}
        else:
            # ② HK cold（同步，49ms 量级；不可用则 skip）
            # 标题与正文**分开判**再取严重者 —— 拼一起判时正常标题会稀释正文的
            # 脏话（实测「[审核测试] xxx」+「你个废物…」拼起来只判 0.07）。
            cold = {"level": "skip", "score": 0.0}
            try:
                import cold_guard
                cands = []
                if (body or "").strip():
                    cands.append(cold_guard.judge(body))
                if (title or "").strip():
                    cands.append(cold_guard.judge(title))
                if cands:
                    rank = {"bad": 3, "suspect": 2, "ok": 1, "skip": 0}
                    cold = max(cands, key=lambda c: (rank.get(c.get("level"), 0),
                                                     c.get("score") or 0))
            except Exception:
                pass
        db.execute("UPDATE ai_moderation SET cold_level=?, cold_score=?, cold_why=?, updated_at=? WHERE id=?",
                   (cold.get("level", "skip"), cold.get("score", 0.0),
                    (cold.get("why") or "")[:200], time.time(), mid))
        db.commit()

        # ③ LA Qwen3Guard（异步入队）
        jid = None
        try:
            import ai_queue
            jid = ai_queue.enqueue_moderate(target_type, target_id, text)
        except Exception:
            pass
        if jid:
            db.execute("UPDATE ai_moderation SET job_id=?, updated_at=? WHERE id=?",
                       (jid, time.time(), mid))
            db.commit()

        # cold 已判 bad → 先隐藏（不等 LA）
        if cold.get("level") == "bad":
            db.execute("UPDATE ai_moderation SET status='hidden', updated_at=? WHERE id=?",
                       (time.time(), mid))
            _hide_target(db, target_type, target_id)
            db.commit()
        return mid
    except Exception:
        return None


def _hide_target(db, target_type, target_id):
    """自动隐藏（只隐藏不删，人工可恢复）。"""
    try:
        if target_type == "post":
            # 帖子表是 posts，没有 is_hidden —— 用 recycle=1（回收站，列表不显示、
            # 管理员可恢复；列表页过滤条件是 recycle=0 AND is_deleted=0）
            db.execute("UPDATE posts SET recycle=1 WHERE id=?", (target_id,))
        elif target_type == "comment":
            # 评论表是 post_comments，无隐藏列 —— 软删（可恢复）
            db.execute("UPDATE post_comments SET is_deleted=1 WHERE id=?", (target_id,))
        db.commit()
    except Exception:
        pass


def _unhide_target(db, target_type, target_id):
    try:
        if target_type == "post":
            db.execute("UPDATE posts SET recycle=0 WHERE id=?", (target_id,))
        elif target_type == "comment":
            db.execute("UPDATE post_comments SET is_deleted=0 WHERE id=?", (target_id,))
        db.commit()
    except Exception:
        pass


def sweep_once(db):
    """把 LA 结果收回来（由后台线程周期调用）。返回处理条数。

    同时回收两类任务（用 ai:result:<job_id> hash 里的 type 区分）：
      moderate → ai_moderation 表的审核结果
      tag      → ai_tag_jobs 的打标签结果（set_post_tags 追加，不覆盖用户选的）
    """
    import ai_queue
    n = _do_tag_jobs(db)
    try:
        rows = db.execute(
            "SELECT * FROM ai_moderation WHERE status IN ('pending','flagged','hidden') "
            "AND job_id IS NOT NULL AND job_id <> '' AND llm_level IS NULL "
            "ORDER BY id LIMIT 50").fetchall()
    except Exception:
        rows = []

    for r in rows:
        res = ai_queue.result(_row_get(r, "job_id"))
        if not res or res.get("status") not in ("done", "failed"):
            continue
        level = res.get("level") or ("skip" if res.get("status") == "failed" else "ok")
        db.execute("UPDATE ai_moderation SET llm_level=?, llm_score=?, llm_why=?, updated_at=? "
                   "WHERE id=?",
                   (level, res.get("score"), (res.get("why") or "")[:120], time.time(),
                    _row_get(r, "id")))
        # 状态机
        cold_lv = _row_get(r, "cold_level") or "skip"
        if "bad" in (cold_lv, level):
            db.execute("UPDATE ai_moderation SET status='hidden' WHERE id=? AND status='pending'",
                       (_row_get(r, "id"),))
            _hide_target(db, _row_get(r, "target_type"), _row_get(r, "target_id"))
        elif "suspect" in (cold_lv, level):
            db.execute("UPDATE ai_moderation SET status='flagged' WHERE id=? AND status='pending'",
                       (_row_get(r, "id"),))
        else:
            db.execute("UPDATE ai_moderation SET status='cleared' WHERE id=? AND status='pending'",
                       (_row_get(r, "id"),))
        n += 1
    db.commit()
    return n


def start_sweeper(flask_app, get_db_fn):
    """后台线程：每 10 秒收一次 LA 结果。

    ⚠️ get_db 依赖 flask.g（请求上下文），后台线程必须自己包 app_context。
    """
    global _sweep_started
    if _sweep_started:
        return
    _sweep_started = True

    def loop():
        while True:
            try:
                with flask_app.app_context():
                    db = get_db_fn()
                    sweep_once(db)
                    db.close()
            except Exception:
                pass
            time.sleep(10)

    t = threading.Thread(target=loop, daemon=True, name="ai-mod-sweep")
    t.start()


def pending_list(db, status="flagged", limit=100):
    try:
        ensure_table(db)
        return db.execute(
            "SELECT m.*, u.username FROM ai_moderation m "
            "LEFT JOIN users u ON u.id = m.user_id "
            "WHERE m.status = ? ORDER BY m.id DESC LIMIT ?", (status, limit)).fetchall()
    except Exception:
        return []


def review(db, mid, action, admin_id):
    """人工复核。action: clear（通过，恢复显示）/ hide（确认隐藏）/ delete（删除内容）。"""
    try:
        row = db.execute("SELECT * FROM ai_moderation WHERE id=?", (mid,)).fetchone()
        if not row:
            return False
        tt, tid = _row_get(row, "target_type"), _row_get(row, "target_id")
        if action == "clear":
            _unhide_target(db, tt, tid)
            db.execute("UPDATE ai_moderation SET status='reviewed', reviewed_by=?, reviewed_at=? WHERE id=?",
                       (admin_id, time.time(), mid))
        elif action == "hide":
            _hide_target(db, tt, tid)
            db.execute("UPDATE ai_moderation SET status='hidden', reviewed_by=?, reviewed_at=? WHERE id=?",
                       (admin_id, time.time(), mid))
        elif action == "delete":
            if tt == "post":
                db.execute("UPDATE posts SET is_deleted=1 WHERE id=?", (tid,))
            elif tt == "comment":
                db.execute("UPDATE post_comments SET is_deleted=1 WHERE id=?", (tid,))
            db.execute("UPDATE ai_moderation SET status='hidden', reviewed_by=?, reviewed_at=? WHERE id=?",
                       (admin_id, time.time(), mid))
        db.commit()
        return True
    except Exception:
        return False


def stats(db):
    """审核概览（admin 用）。"""
    try:
        ensure_table(db)
        rows = db.execute(
            "SELECT status, COUNT(*) c FROM ai_moderation GROUP BY status").fetchall()
        d = {r["status"]: r["c"] for r in rows}
        return {"pending": d.get("pending", 0), "flagged": d.get("flagged", 0),
                "hidden": d.get("hidden", 0), "cleared": d.get("cleared", 0),
                "reviewed": d.get("reviewed", 0), "total": sum(d.values())}
    except Exception:
        return {}

# ---------------- 广告规则层（第 0.5 层）----------------
# Qwen3Guard 是安全模型，天生不擅长广告识别（实测「加微信领课程」「代刷积分」
# 都判 Safe）。广告交给规则，比调 prompt 可靠得多。
import re as _re

_AD_PATTERNS = [
    _re.compile(r"(加|联系|扫码).{0,6}(微信|vx|VX|weixin|qq|QQ|企鹅)"),
    _re.compile(r"(微信|vx|VX|weixin|QQ|qq)\s*[:：]\s*[a-zA-Z0-9_-]{5,}"),
    _re.compile(r"代(刷|练|考|写|做|打)"),
    _re.compile(r"(领取|免费|送).{0,8}(课程|资料|教程|福利|名额)"),
    _re.compile(r"(私聊|私信|联系).{0,10}(价格|收费|接单|优惠)"),
    _re.compile(r"(群号|Q群|企鹅群)\s*[:：]?\s*\d{5,}"),
    _re.compile(r"(充值|返利|优惠券|代理|招商).{0,10}(微信|联系|私聊)"),
]


def ad_check(text):
    """广告规则判定。返回 (命中, 匹配片段)。"""
    t = text or ""
    for pat in _AD_PATTERNS:
        m = pat.search(t)
        if m:
            return True, m.group(0)[:30]
    return False, ""


def enqueue_tag(db, post_id, title, body):
    """发帖时用户没选标签 → AI 打标签（HK 本地异步，走 GLM-Z1-Air）。

    2026-10-25 从「LA 本地 Hunyuan + RQ 队列」迁回 HK 直调 GLM：
    key 在 HK（不往 LA 传密钥），且省掉 RQ 入队/redis 回写/sweep 三件套。
    任务存 ai_tag_jobs（job_id='local'），由 sweeper 每轮消费最多 3 条。
    """
    try:
        top = db.execute(
            "SELECT name FROM tags ORDER BY use_count DESC LIMIT 30").fetchall()
        tagset = [t["name"] for t in top]
        if not tagset:
            return None
        ensure_table(db)
        db.execute("CREATE TABLE IF NOT EXISTS ai_tag_jobs ("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, job_id TEXT, "
                   "status TEXT DEFAULT 'pending', tags TEXT, created_at REAL, updated_at REAL)")
        db.execute("INSERT INTO ai_tag_jobs (post_id, job_id, created_at) VALUES (?,?,?)",
                   (post_id, "local", time.time()))
        db.commit()
        return "local"
    except Exception:
        return None


# ---------------- GLM-Z1-Air 打标签（HK 直调智谱）----------------
_TAG_SYSTEM = ("你是社区内容分类助手。给帖子生成 1-2 个标签。\n"
               "规则：\n"
               "1. 优先从「已有标签」里选最贴切的\n"
               "2. 已有标签都不合适时，才可自创 1 个新标签\n"
               "3. 标签必须是 2-6 个字的名词（如：图论、Python、题解、象棋），"
               "绝不能用句子、动词短语或整个标题\n"
               "4. 已有标签：%s\n"
               "只输出 JSON 数组，如 [\"算法\"]，不要解释。")


def _glm_tag_once(title, body, tagset):
    """调智谱 GLM-Z1-Air 打标签（非流式）。返回原始文本。"""
    import ai_config
    model = "glm-z1-air"
    headers = {"Authorization": "Bearer " + ai_config.get_ai_key(model),
               "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _TAG_SYSTEM % "、".join(tagset)},
            {"role": "user", "content": "标题：%s\n正文：%s" % (title[:120], (body or "")[:800])},
        ],
        "max_tokens": 1024, "temperature": 0.3,
    }
    try:
        payload.update(ai_config.model_extra_body(model, thinking=False))
    except Exception:
        pass
    r = requests.post(ai_config.model_url(model), json=payload,
                      headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def _do_tag_jobs(db, limit=3):
    """消费 pending 的本地打标签任务（sweeper 每轮调用）。返回处理条数。"""
    try:
        rows = db.execute(
            "SELECT * FROM ai_tag_jobs WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,)).fetchall()
    except Exception:
        return 0
    n = 0
    for r in rows:
        try:
            post = db.execute("SELECT title, content FROM posts WHERE id=?",
                              (r["post_id"],)).fetchone()
            if not post:
                db.execute("UPDATE ai_tag_jobs SET status='skipped', updated_at=? WHERE id=?",
                           (time.time(), r["id"]))
                continue
            tagset = [t["name"] for t in db.execute(
                "SELECT name FROM tags ORDER BY use_count DESC LIMIT 30").fetchall()]
            raw = _glm_tag_once(post["title"], post["content"], tagset)
            arr = re.findall(r'"([^"]+)"', raw)
            picked = [t for t in arr if t][:2]
            db.execute("UPDATE ai_tag_jobs SET status=?, tags=?, updated_at=? WHERE id=?",
                       ("done", json.dumps(picked, ensure_ascii=False), time.time(), r["id"]))
            if picked:
                import app_topics as _topics
                old = [t["name"] for t in _topics.post_tags(r["post_id"])]
                cleaned = [t for t in (clean_tag(db, x) for x in picked)
                           if t and t not in old]
                _topics.set_post_tags(r["post_id"], (old + cleaned)[:6])
            n += 1
        except Exception:
            db.execute("UPDATE ai_tag_jobs SET status='failed', updated_at=? WHERE id=?",
                       (time.time(), r["id"]))
    db.commit()
    return n

# ---------------- AI 标签清洗归并（混合模式的护栏）----------------
# 只允许 2-6 字中英文数字名词（防「动态规划入门题推荐」这种标题式长串）
_TAG_OK = _re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9++#]{2,6}$")
# 含这些碎片的标签直接扔（动词/疑问/泛词）
_TAG_BAD_PARTS = ("推荐", "怎么写", "求", "怎么", "分享", "讨论", "解析", "详解",
                  "笔记", "日记", "小结", "问题求解", "小白", "萌新")


def clean_tag(db, t):
    """清洗 AI 自创标签：白名单优先 → 格式 → 坏词 → 前缀归并。

    返回规范化标签名，或 None（丢弃）。
    """
    t = (t or "").strip().strip("《》[]\"'`")
    if not t:
        return None
    try:
        row = db.execute("SELECT name FROM tags WHERE name=?", (t,)).fetchone()
        if row:
            return t
    except Exception:
        pass
    if not _TAG_OK.match(t):
        return None
    if any(b in t for b in _TAG_BAD_PARTS):
        return None
    try:
        row = db.execute(
            "SELECT name FROM tags WHERE name LIKE ? ORDER BY use_count DESC LIMIT 1",
            (t[:2] + "%",)).fetchone()
        if row:
            return row["name"]
    except Exception:
        pass
    return t
