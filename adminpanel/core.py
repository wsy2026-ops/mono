# -*- coding: utf-8 -*-
"""adminpanel/core.py — 管理后台（用户管理/内容审核/惩罚/数据/监控/AI 管理）。

从 D['app'].py 搬迁。56 个路由 + 21 个 helper。共享 helper 走 deps.D 注入。
"""
import os
import re
import secrets
import shutil
import time

from flask import flash, jsonify, redirect, render_template, request, url_for

import pg_shadow  # 052 PG 渐进迁移（读切流：纯 points_log 查询优先 PG）
from .deps import D
from games.mini import (_GAME_FIELD_IS_TIME, _GAME_SCORE_FIELDS, _GAME_SCORE_ROWS,
                        _GAME_TIME_ROWS, _game_reset_sql)



def admin_home_banners():
    """首页轮播图管理（仅超管）。"""
    if request.method == "POST":
        raw = (request.form.get("banners") or "").strip()[:4000]
        D['set_config']("home_banners", raw)
        flash("轮播图已保存", "ok")
        return redirect(url_for("admin_home_banners"))
    return render_template("admin_home_banners.html",
                           raw=D['get_config']("home_banners", "") or "",
                           preview=_home_banners())






def admin_metrics():
    """046 全站行为埋点看板：漏斗 / 停留 / 来源 / 错误率 / 行为分布。"""
    db = D['get_db']()
    now = time.time()
    d30 = now - 30 * 86400

    def cnt(action, since=None, uid=None):
        q = "SELECT COUNT(*) c FROM user_actions WHERE action=?"
        p = [action]
        if since:
            q += " AND created_at>?"
            p.append(since)
        if uid:
            q += " AND user_id=?"
            p.append(uid)
        return db.execute(q, p).fetchone()["c"]

    # 漏斗：近 30 天注册的人，后续各步转化
    new_uids = [r["user_id"] for r in db.execute(
        "SELECT DISTINCT user_id FROM user_actions WHERE action='register' AND created_at>?",
        (d30,)).fetchall()]
    funnel = [("注册", len(new_uids))]
    for label, act in [("发帖", "post"), ("评论", "comment"),
                       ("玩游戏", "game"), ("上传图片", "upload")]:
        if not new_uids:
            funnel.append((label, 0))
            continue
        qs = ",".join("?" * len(new_uids))
        n = db.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM user_actions "
            "WHERE action=? AND user_id IN (%s)" % qs, [act] + new_uids).fetchone()["c"]
        funnel.append((label, n))
    # 7 日留存：近 30 天注册且注册 7 天后仍有登录行为的
    retained = 0
    if new_uids:
        for u in new_uids:
            reg_ts = db.execute(
                "SELECT MIN(created_at) t FROM user_actions WHERE user_id=? AND action='register'",
                (u,)).fetchone()["t"]
            if reg_ts and db.execute(
                    "SELECT 1 FROM user_actions WHERE user_id=? AND action='login' "
                    "AND created_at>? LIMIT 1", (u, reg_ts + 7 * 86400)).fetchone():
                retained += 1
    funnel.append(("7 日留存", retained))

    # 页面停留 TOP10（stay 的 target_id = 秒数）
    stays = db.execute(
        "SELECT target_type, COUNT(*) n, AVG(target_id) avg_s, MAX(target_id) max_s "
        "FROM user_actions WHERE action='stay' AND created_at>? "
        "GROUP BY target_type ORDER BY n DESC LIMIT 10", (d30,)).fetchall()

    # 来源渠道 TOP10（stay 行的 ip 字段存了 referrer）
    refs = db.execute(
        "SELECT ip, COUNT(*) n FROM user_actions WHERE action='stay' AND ip!='' "
        "AND created_at>? GROUP BY ip ORDER BY n DESC LIMIT 10", (d30,)).fetchall()

    # 错误率（近 30 天 js_error / 总会话）
    errs = cnt("js_error", d30)
    visits = cnt("visit", d30) or 1

    # 行为分布（近 30 天各 action 计数）
    dist = db.execute(
        "SELECT action, COUNT(*) c FROM user_actions WHERE created_at>? "
        "GROUP BY action ORDER BY c DESC LIMIT 15", (d30,)).fetchall()

    # 近 14 天每日活跃（有任意埋点的不重名用户）
    daily = db.execute(
        "SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(DISTINCT user_id) u "
        "FROM user_actions WHERE created_at>? GROUP BY d ORDER BY d",
        (now - 14 * 86400,)).fetchall()

    return render_template("admin_metrics.html", funnel=funnel, stays=stays,
                           refs=refs, errs=errs, visits=visits, dist=dist,
                           daily=daily)






def admin_points_audit():
    """045 积分审计报告：流水查询 + 用户汇总 + 异常检测。"""
    db = D['get_db']()
    q_user = (request.args.get("u") or "").strip()
    q_type = (request.args.get("t") or "").strip()
    page = max(1, int(request.args.get("page", "1") or 1))
    per = 50
    where, params = "1=1", []
    if q_user:
        where += " AND (p.user_id = ? OR u.username LIKE ?)"
        params += [q_user, "%" + q_user + "%"]
    if q_type:
        where += " AND p.source_type = ?"
        params.append(q_type)
    total = db.execute(
        "SELECT COUNT(*) c FROM points_log p LEFT JOIN users u ON u.id=p.user_id WHERE " + where,
        params).fetchone()["c"]
    logs = db.execute(
        "SELECT p.*, u.username FROM points_log p LEFT JOIN users u ON u.id=p.user_id "
        "WHERE " + where + " ORDER BY p.id DESC LIMIT ? OFFSET ?",
        params + [per, (page - 1) * per]).fetchall()
    # 用户汇总 TOP20（净发放）
    # ⚠️ 2026-11-01：points_log 里混着 source_type='exp' 的**经验**流水（045 把币和经验
    # 记进了同一张表），积分对账必须排除它，否则每个人的「净发放」都被经验抬高。
    top_users = db.execute(
        "SELECT p.user_id, u.username, SUM(p.delta) net, COUNT(*) n "
        "FROM points_log p LEFT JOIN users u ON u.id=p.user_id "
        "WHERE p.source_type != 'exp' "
        "GROUP BY p.user_id ORDER BY net DESC LIMIT 20").fetchall()
    # 异常：近 24h 单用户发放 TOP10
    since = time.time() - 86400
    anomalies = db.execute(
        "SELECT p.user_id, u.username, SUM(p.delta) s, COUNT(*) n "
        "FROM points_log p LEFT JOIN users u ON u.id=p.user_id "
        "WHERE p.created_at > ? AND p.delta > 0 GROUP BY p.user_id "
        "ORDER BY s DESC LIMIT 10", (since,)).fetchall()
    # 对账：积分流水净额 vs 当前余额（差异 = 直接改库 / 历史数据）
    # ⚠️ 2026-11-01 修：原来把 exp（经验）流水也算进来，73 人里报 8 人不一致，
    # 实际排除经验后只剩 4 人（admin 测试账号 + 3 个历史遗留）。
    # 排除条件必须写在 JOIN ON 里 —— 写 WHERE 会连「一条流水都没有的用户」一起过滤掉。
    drift = db.execute(
        "SELECT u.id, u.username, u.coins, COALESCE(SUM(p.delta),0) net "
        "FROM users u LEFT JOIN points_log p "
        "  ON p.user_id = u.id AND p.source_type != 'exp' "
        "GROUP BY u.id HAVING u.coins != net ORDER BY ABS(u.coins - net) DESC LIMIT 15"
    ).fetchall()
    types = pg_shadow.read_rows(db,
        "SELECT DISTINCT source_type FROM points_log ORDER BY source_type")

    # ── 092 积分经济看板（2026-11-01）──
    # 判断经济健不健康看三件事：存量分布（中位数）、消费率、有没有印钞渠道。
    # 光看「总存量」会被少数大户带偏 —— 中位数 6 分才是大多数人的真实体感。
    def _one(sql, args=()):
        try:
            r = db.execute(sql, args).fetchone()
            return (r[0] or 0), (r[1] or 0) if r and len(r) > 1 else 0
        except Exception:
            return 0, 0

    coins = sorted([(r[0] or 0) for r in db.execute("SELECT coins FROM users").fetchall()])

    def _q(pct):
        if not coins:
            return 0
        return coins[min(len(coins) - 1, int(len(coins) * pct))]

    econ = {
        "users": len(coins),
        "total": sum(coins),
        "avg": (sum(coins) / float(len(coins))) if coins else 0,
        "median": _q(0.5),
        "p75": _q(0.75),
        "p90": _q(0.9),
        "max": coins[-1] if coins else 0,
        "zero": sum(1 for v in coins if v == 0),
    }
    econ["zero_pct"] = econ["zero"] * 100.0 / econ["users"] if econ["users"] else 0

    inn = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN delta>0 THEN delta ELSE 0 END),0) FROM points_log "
        "WHERE source_type!='exp'").fetchone()[0] or 0
    out = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN delta<0 THEN -delta ELSE 0 END),0) FROM points_log "
        "WHERE source_type!='exp'").fetchone()[0] or 0
    # ⚠️ 消费率必须只算「玩家自己花的」。
    # 全量消耗里混着 ai_balance -40000（管理员兑换）和 achievement -51030
    # （成就重算回收），这两笔是系统动作，算进去会把消费率虚报成 105.8%
    # （真实玩家消费率 31.3%）。分开列，别混。
    out_player = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN delta<0 THEN -delta ELSE 0 END),0) FROM points_log "
        "WHERE source_type NOT IN ('exp','ai_balance','achievement')").fetchone()[0] or 0
    econ["in"], econ["out"] = inn, out
    econ["out_player"] = out_player
    econ["out_sys"] = out - out_player
    econ["net"] = inn - out
    econ["burn"] = (out_player * 100.0 / inn) if inn else 0

    # 玩法净收益：把「买券」的成本归到玩法名下，否则盲盒看着像纯印钞。
    # 净额为正 = 这个玩法在给玩家发钱（印钞机）；为负 = 在回收积分（健康）。
    play = []
    b_in_n, b_in_s = _one("SELECT COUNT(*), COALESCE(SUM(delta),0) FROM points_log "
                          "WHERE source_type='blindbox' AND delta>0")
    b_c_n, b_c_s = _one("SELECT COUNT(*), COALESCE(SUM(-delta),0) FROM points_log "
                        "WHERE source_type='mall' AND delta<0 AND reason LIKE '%盲盒%'")
    if b_in_n or b_c_n:
        play.append({"name": "盲盒", "n": b_in_n, "in": b_in_s, "cost": b_c_s,
                     "net": b_in_s - b_c_s})
    g_in_n, g_in_s = _one("SELECT COUNT(*), COALESCE(SUM(delta),0) FROM points_log "
                          "WHERE source_type='gacha' AND delta>0")
    g_c_n, g_c_s = _one("SELECT COUNT(*), COALESCE(SUM(-delta),0) FROM points_log "
                        "WHERE source_type='gacha' AND delta<0")
    if g_in_n or g_c_n:
        play.append({"name": "扭蛋", "n": g_c_n, "in": g_in_s, "cost": g_c_s,
                     "net": g_in_s - g_c_s})
    m_c_n, m_c_s = _one("SELECT COUNT(*), COALESCE(SUM(-delta),0) FROM points_log "
                        "WHERE source_type='mall' AND delta<0 AND reason NOT LIKE '%盲盒%'")
    if m_c_n:
        play.append({"name": "商城（不含盲盒券）", "n": m_c_n, "in": 0, "cost": m_c_s,
                     "net": -m_c_s})

    # 近 14 天每日产出 / 消耗
    daily = db.execute(
        "SELECT date(created_at,'unixepoch','+8 hours') d, "
        "  COALESCE(SUM(CASE WHEN delta>0 THEN delta ELSE 0 END),0) inn, "
        "  COALESCE(SUM(CASE WHEN delta<0 THEN -delta ELSE 0 END),0) out "
        "FROM points_log WHERE source_type!='exp' AND created_at>? "
        "GROUP BY d ORDER BY d DESC", (time.time() - 14 * 86400,)).fetchall()

    return render_template("admin_points_audit.html", logs=logs, total=total,
                           page=page, pages=max(1, (total + per - 1) // per),
                           top_users=top_users, anomalies=anomalies, drift=drift,
                           types=types, q_user=q_user, q_type=q_type,
                           econ=econ, play=play, daily=daily)






def admin_clawback_preview(uid):
    """045 clawback 影响范围预览：扣积分会连带什么。"""
    db = D['get_db']()
    u = db.execute("SELECT id, username, coins, exp, level FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return {"ok": False, "msg": "用户不存在"}, 404
    ach = D['_ach'].user_summary(uid)
    new_level = D['level_for_exp'](max(0, int(u['exp'] or 0)))  # 经验不动，等级保持
    return {"ok": True,
            "user": {"id": u['id'], "username": u['username'],
                     "coins": u['coins'], "exp": u['exp'], "level": u['level']},
            "impact": {
                "coins_after": max(0, int(u['coins'] or 0)),
                "level_keep": new_level,
                "ach_count": (ach or {}).get("unlocked", 0) if isinstance(ach, dict) else 0,
                "note": "积分扣减不影响已解锁成就与等级（成就/等级为荣誉记录，不回收）；"
                        "若需连等级一起降，请另行处理 exp。"}}






def admin_clawback():
    """045 管理员积分回收（走 D['award'] 统一入口，自动留流水）。"""
    me = D['current_user']()
    db = D['get_db']()
    try:
        uid = int(request.form.get("uid", "0") or 0)
        amount = int(request.form.get("amount", "0") or 0)
    except ValueError:
        flash("参数不合法", "error")
        return redirect(url_for('admin_points_audit'))
    reason = (request.form.get("reason") or "").strip()[:80] or "管理员回收"
    if uid <= 0 or amount <= 0:
        flash("参数不合法", "error")
        return redirect(url_for('admin_points_audit'))
    target = D['get_user_by_id'](uid)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for('admin_points_audit'))
    new_coins = D['award'](uid, -amount, 'penalty', reason, admin_id=me['id'],
                      idem_key="clawback:%d:%d" % (uid, int(time.time())))
    D['add_notification'](uid, 'points',
                     '管理员回收了你的 %d 积分（%s）' % (amount, reason),
                     url_for('profile'))
    D['add_admin_log'](me, "points_clawback", uid,
                  "-%d（%s）余 %s" % (amount, reason, new_coins))
    flash("已回收 %s 的 %d 积分，当前余额 %s" % (target['username'], amount, new_coins), "ok")
    return redirect(url_for('admin_points_audit'))


# ---------------- 048 性能面板（解析 mono.log，零新表） ----------------






def admin_perf():
    """048 性能面板：状态码分布 / 慢请求 Top / 5xx 明细与采样 / 系统趋势。"""
    codes, slow, errs, tbs = _perf_parse_log()
    total = sum(codes.values()) or 1
    buckets = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
    for c, n in codes.items():
        buckets[c[0] + "xx"] = buckets.get(c[0] + "xx", 0) + n
    # 系统趋势（monitor_samples 近 24h，req_avg_ms）
    samples = []
    try:
        db = D['get_db']()
        rows = db.execute(
            "SELECT ts, req_count, req_avg_ms, cpu_pct, mem_used_mb "
            "FROM monitor_samples WHERE ts > ? ORDER BY ts DESC LIMIT 48",
            (time.time() - 86400,)).fetchall()
        samples = [dict(r) for r in rows][::-1]
    except Exception:
        pass
    return render_template("admin_perf.html", codes=codes, buckets=buckets,
                           total=total, slow=slow, errs=errs, tbs=tbs,
                           samples=samples, slow_ms=D['_SLOW_MS'])






def admin_activity():
    """后台日志：谁在什么时候干了什么（登录、玩游戏、发帖…）+ 当前在线的人。

    只有超管能看 —— 这里能看到全站用户的行为轨迹和 IP，权限必须收紧。
    """
    db = D['get_db']()
    q_user = (request.args.get("user") or "").strip()
    q_action = (request.args.get("action") or "").strip()
    q_ip = (request.args.get("ip") or "").strip()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (ValueError, TypeError):
        page = 1
    PER = 80

    where, args = [], []
    if q_user:
        where.append("(username LIKE ? OR user_id = ?)")
        args += ['%' + q_user + '%', int(q_user) if q_user.isdigit() else -1]
    if q_action:
        where.append("action = ?")
        args.append(q_action)
    if q_ip:
        where.append("ip LIKE ?")
        args.append('%' + q_ip + '%')
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""

    total = db.execute("SELECT COUNT(*) AS c FROM activity_log %s" % sql_where, args).fetchone()['c']
    rows = db.execute(
        "SELECT * FROM activity_log %s ORDER BY id DESC LIMIT ? OFFSET ?" % sql_where,
        args + [PER, (page - 1) * PER]).fetchall()
    actions = [r[0] for r in db.execute(
        "SELECT DISTINCT action FROM activity_log ORDER BY action").fetchall()]

    now = time.time()
    online = db.execute(
        "SELECT id, username, user_no, avatar, last_active FROM users "
        "WHERE deactivated=0 AND last_active >= ? ORDER BY last_active DESC LIMIT 100",
        (now - D['ONLINE_THRESHOLD'],)).fetchall()
    recent = db.execute(
        "SELECT id, username, user_no, avatar, last_active FROM users "
        "WHERE deactivated=0 AND last_active >= ? ORDER BY last_active DESC LIMIT 60",
        (now - 86400,)).fetchall()

    pages = max(1, (total + PER - 1) // PER)
    return render_template("admin_activity.html", rows=rows, total=total, page=page, pages=pages,
                           q_user=q_user, q_action=q_action, q_ip=q_ip, actions=actions,
                           online=online, recent=recent, online_threshold=D['ONLINE_THRESHOLD'])






def admin_debug():
    """超管调试台。

    真机上没法开控制台，出了问题只能靠猜。这个页面把该看的都摊出来：
    环境信息、前端错误日志、音频流自检、服务端最近异常。
    """
    logs = []
    try:
        logdir = os.path.join(D['BASE_DIR'], "logs")
        files = sorted([f for f in os.listdir(logdir) if f.startswith("mono.log")])
        if files:
            with open(os.path.join(logdir, files[-1]), 'r', encoding='utf-8', errors='ignore') as fh:
                tail = fh.readlines()[-400:]
            for ln in tail:
                if any(k in ln for k in ('Traceback', '未捕获异常', 'ERROR', 'Exception', 'error')):
                    logs.append(ln.rstrip()[:220])
            logs = logs[-40:]
    except Exception:
        pass

    music = []
    try:
        for r in D['get_db']().execute(
                "SELECT id, title, artist, filename, size, duration, COALESCE(plays,0) AS plays "
                "FROM music_tracks ORDER BY id DESC LIMIT 30"):
            p = os.path.join(D['MUSIC_FOLDER'], r['filename'])
            music.append({
                'id': r['id'], 'title': r['title'], 'artist': r['artist'],
                'size': r['size'], 'duration': r['duration'], 'plays': r['plays'],
                'exists': os.path.isfile(p),
                'real_size': (os.path.getsize(p) if os.path.isfile(p) else 0),
                'filename': r['filename'],
            })
    except Exception:
        pass

    return render_template("admin_debug.html", logs=logs, music=music,
                           music_folder=D['MUSIC_FOLDER'], base_dir=D['BASE_DIR'])






def admin_pages():
    """站点文案：关于我们页 + 更新日志页头的所有文字。只有超管能改。"""
    db = D['get_db']()
    all_defs = [('about', b) for b in D['ABOUT_BLOCKS']] + [('changelog', b) for b in D['CHANGELOG_BLOCKS']]
    if request.method == "POST":
        me = D['current_user']()
        saved = 0
        for page, b in all_defs:
            key, default = b[1], b[3]
            if key not in request.form:
                continue
            val = (request.form.get(key) or "").strip()
            row = db.execute("SELECT content FROM page_blocks WHERE page=? AND key=?",
                             (page, key)).fetchone()
            old = (row['content'] if row else '') or ''
            if val == old:
                continue
            if not val or val == default:
                db.execute("DELETE FROM page_blocks WHERE page=? AND key=?", (page, key))
            else:
                db.execute(
                    "INSERT INTO page_blocks (page, key, content, updated_at, updated_by) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(page, key) DO UPDATE SET "
                    "content=EXCLUDED.content, updated_at=EXCLUDED.updated_at, "
                    "updated_by=EXCLUDED.updated_by",
                    (page, key, val, time.time(), me['username']))
            saved += 1
        db.commit()
        if saved:
            D['_activity']("改站点文案", "%d 处" % saved)
        flash("已保存 %d 处改动" % saved if saved else "没有检测到改动", "ok")
        return redirect(url_for("admin_pages"))

    def grouped(defs):
        groups, seen = [], {}
        for sec, key, label, default, multi in defs:
            if sec not in seen:
                seen[sec] = []
                groups.append((sec, seen[sec]))
            seen[sec].append({"key": key, "label": label, "default": default, "multi": multi})
        return groups

    over = {'about': {}, 'changelog': {}}
    for r in db.execute("SELECT page, key, content FROM page_blocks").fetchall():
        if r['page'] in over:
            over[r['page']][r['key']] = r['content']
    return render_template("admin_pages.html",
                           about_groups=grouped(D['ABOUT_BLOCKS']),
                           changelog_groups=grouped(D['CHANGELOG_BLOCKS']),
                           about_over=over['about'],
                           changelog_over=over['changelog'],
                           about_eff=D['about_blocks'](),
                           changelog_eff=D['changelog_blocks']())






def admin_pages_reset():
    """把某一处文案恢复成代码里的默认值（删掉库里的覆盖）"""
    page = (request.form.get("page") or 'about').strip()
    key = (request.form.get("key") or "").strip()
    if key:
        db = D['get_db']()
        db.execute("DELETE FROM page_blocks WHERE page=? AND key=?", (page, key))
        db.commit()
        flash("已恢复默认", "ok")
    return redirect(url_for("admin_pages"))






def admin_changelog():
    """更新日志条目维护。只有超管能加/改/删。"""
    db = D['get_db']()
    if request.method == "POST":
        date = (request.form.get("date") or "").strip()[:20]
        text = (request.form.get("text") or "").strip()[:500]
        if not date or not text:
            flash("日期和内容都要填", "error")
            return redirect(url_for("admin_changelog"))
        top = db.execute("SELECT COALESCE(MAX(sort_order),0) m FROM changelog").fetchone()['m']
        db.execute(
            "INSERT INTO changelog (date, text, is_new, sort_order, created_at, updated_at) "
            "VALUES (?,?,0,?,?,?)",
            (date, text, top + 1, time.time(), time.time()))
        db.commit()
        D['_activity']("加更新日志", "%s %s" % (date, text[:40]))
        flash("已添加一条", "ok")
        return redirect(url_for("admin_changelog"))
    return render_template("admin_changelog.html", entries=D['changelog_entries'](),
                           today=_today_str())






def admin_changelog_edit(cid):
    db = D['get_db']()
    date = (request.form.get("date") or "").strip()[:20]
    text = (request.form.get("text") or "").strip()[:500]
    if not date or not text:
        flash("日期和内容都要填", "error")
        return redirect(url_for("admin_changelog"))
    db.execute("UPDATE changelog SET date=?, text=?, updated_at=? WHERE id=?",
               (date, text, time.time(), cid))
    db.commit()
    flash("已更新", "ok")
    return redirect(url_for("admin_changelog"))






def admin_changelog_delete(cid):
    db = D['get_db']()
    row = db.execute("SELECT date, text FROM changelog WHERE id=?", (cid,)).fetchone()
    db.execute("DELETE FROM changelog WHERE id=?", (cid,))
    db.commit()
    if row:
        D['_activity']("删更新日志", "%s %s" % (row['date'], row['text'][:40]))
    flash("已删除", "ok")
    return redirect(url_for("admin_changelog"))






def admin_force_unfriend(user_id):
    """超管强制解除某用户的所有好友关系。"""
    me = D['current_user']()
    db = D['get_db']()
    t = D['get_user_by_id'](user_id)
    if not t:
        flash("用户不存在", "error")
        return redirect(request.referrer or url_for('friends'))
    db.execute(
        "DELETE FROM friends WHERE user_id=? OR friend_id=?",
        (user_id, user_id))
    db.commit()
    D['add_admin_log'](me, "force_unfriend", t['username'], "强制解除好友关系")
    flash("已强制解除 %s 的所有好友关系" % t['username'], "ok")
    return redirect(request.referrer or url_for('friends'))






def api_admin_force_unfriend():
    me = D['current_user']()
    data = request.get_json(silent=True) or {}
    try:
        uid = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数非法"}), 400
    t = D['get_user_by_id'](uid)
    if not t:
        return jsonify({"ok": False, "msg": "用户不存在"}), 404
    db = D['get_db']()
    db.execute("DELETE FROM friends WHERE user_id=? OR friend_id=?", (uid, uid))
    db.commit()
    D['add_admin_log'](me, "force_unfriend", t['username'], "强制解除好友关系")
    return jsonify({"ok": True, "msg": "已解除 %s 的所有好友" % t['username']})






def admin_recycle():
    db = D['get_db']()
    posts = db.execute("""
        SELECT p.*, s.name AS section_name FROM posts p
        LEFT JOIN forum_sections s ON p.section_id = s.id
        WHERE p.recycle = 1 ORDER BY p.updated_at DESC
    """).fetchall()
    return render_template("admin_recycle.html", posts=posts)






def admin_recycle_bulk():
    """回收站批量恢复 / 批量彻底删除。"""
    user = D['current_user']()
    action = (request.form.get("action") or "").strip()
    ids = [int(x) for x in request.form.getlist("ids") if str(x).strip().isdigit()]
    if not ids:
        flash("没有选中任何帖子", "error")
        return redirect(url_for("admin_recycle"))
    if action not in ("restore", "purge"):
        flash("未知操作", "error")
        return redirect(url_for("admin_recycle"))
    if action == "purge" and not user['is_super_admin']:
        flash("只有超级管理员可以彻底删除", "error")
        return redirect(url_for("admin_recycle"))
    db = D['get_db']()
    if action == "restore":
        real = _restore_posts(db, ids)
        D['add_admin_log'](user, "post_restore", ",".join(str(i) for i in real),
                      "批量恢复 %d 篇" % len(real))
        flash("已从回收站恢复 %d 篇帖子" % len(real), "ok")
    else:
        real = _purge_posts(db, ids)
        D['add_admin_log'](user, "post_purge", ",".join(str(i) for i in real),
                      "批量彻底删除 %d 篇" % len(real))
        flash("已彻底删除 %d 篇帖子（不可恢复）" % len(real), "ok")
    return redirect(url_for("admin_recycle"))






def admin_messages():
    """消息管理：按关键词 / 房间 / 状态筛选聊天消息。"""
    db = D['get_db']()
    kw = (request.args.get("q") or "").strip()
    room_id = (request.args.get("room") or "").strip()
    only_deleted = request.args.get("deleted") == "1"
    where, args = [], []
    if kw:
        where.append("(m.content LIKE ? OR m.username LIKE ?)")
        args += ["%" + kw + "%", "%" + kw + "%"]
    if room_id.isdigit():
        where.append("m.room_id = ?")
        args.append(int(room_id))
    if only_deleted:
        where.append("m.is_deleted = 1")
    sql = ("SELECT m.*, r.name AS room_name FROM messages m "
           "LEFT JOIN chat_rooms r ON m.room_id = r.id ")
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY m.id DESC LIMIT 300"
    msgs = db.execute(sql, args).fetchall()
    rooms = db.execute("SELECT id, name FROM chat_rooms ORDER BY sort_order, id").fetchall()
    return render_template("admin_messages.html", msgs=msgs, rooms=rooms,
                           q=kw, cur_room=room_id, only_deleted=only_deleted)






def admin_messages_bulk():
    """批量删除聊天消息（软删除，与单条删除行为一致）。"""
    user = D['current_user']()
    ids = [int(x) for x in request.form.getlist("ids") if str(x).strip().isdigit()]
    back = request.form.get("back") or url_for("admin_messages")
    if not ids:
        flash("没有选中任何消息", "error")
        return redirect(back)
    db = D['get_db']()
    q = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT id, room_id FROM messages WHERE is_deleted = 0 AND id IN (%s)" % q,
        ids).fetchall()
    real = [r['id'] for r in rows]
    if real:
        q2 = ",".join("?" * len(real))
        db.execute("UPDATE messages SET is_deleted = 1, content = '[该消息已被管理员删除]' "
                   "WHERE id IN (%s)" % q2, real)
        db.commit()
        for r in rows:
            try:
                D['socketio'].emit("message_deleted",
                              {"message_id": r['id'], "room_id": r['room_id']},
                              room=r['room_id'])
            except Exception:
                pass
    D['add_admin_log'](user, "chat_delete", ",".join(str(i) for i in real),
                  "批量删除 %d 条消息" % len(real))
    flash("已删除 %d 条消息" % len(real), "ok")
    return redirect(back)






def admin_clear_comments(post_id):
    """管理员一键清空帖子下全部评论"""
    user = D['current_user']()
    db = D['get_db']()
    n = db.execute("SELECT COUNT(*) c FROM post_comments WHERE post_id=? AND is_deleted=0", (post_id,)).fetchone()['c']
    db.execute("UPDATE post_comments SET is_deleted = 1, content = '[该评论已删除]' WHERE post_id = ?", (post_id,))
    db.execute("UPDATE posts SET comment_count = 0 WHERE id = ?", (post_id,))
    db.commit()
    D['add_admin_log'](user, "comments_clear", post_id, "清空%d条" % n)
    flash("已清空该帖全部评论（%d 条）" % n, "ok")
    return redirect(url_for("view_post", post_id=post_id))






def admin_add_admin():
    me = D['current_user']()
    username = (request.form.get("username") or "").strip()
    target = D['get_user_by_name'](username)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    if target["id"] == me["id"]:
        flash("不能操作自己", "error")
        return redirect(url_for("admin"))
    if target["is_admin"]:
        flash("该用户已是管理员", "warn")
        return redirect(url_for("admin"))
    db = D['get_db']()
    db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (target["id"],))
    db.commit()
    D['add_admin_log'](me, "add_admin", username, "授予普通管理员")
    D['add_notification'](
        target["id"], "admin_grant",
        f'你已被超级管理员 {me["username"]} 设为普通管理员', None
    )
    flash("已添加普通管理员：%s" % username, "ok")
    return redirect(url_for("admin"))






def admin_remove_admin(user_id):
    me = D['current_user']()
    target = D['get_user_by_id'](user_id)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    if target["id"] == me["id"]:
        flash("不能移除自己", "error")
        return redirect(url_for("admin"))
    if target["is_super_admin"]:
        flash("无法移除超级管理员", "error")
        return redirect(url_for("admin"))
    db = D['get_db']()
    db.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (target["id"],))
    db.commit()
    D['add_admin_log'](me, "remove_admin", target["username"], "撤销普通管理员")
    D['add_notification'](
        target["id"], "admin_revoke",
        f'你已被超级管理员 {me["username"]} 撤销管理员权限', None
    )
    flash("已移除管理员：%s" % target["username"], "ok")
    return redirect(url_for("admin"))






def admin():
    """超管/管理员后台：顶部数据看板 + 下方详细数据（用户、帖子）。

    看板数据全部用 _n() 包住 —— 任何一张表缺列都不该让整个后台打不开。
    """
    db = D['get_db']()

    # 用户列表：搜索 + 分页（原来一次全查，人多了后台打不开）
    uq = (request.args.get('uq') or '').strip()
    try:
        upage = max(1, int(request.args.get('upage') or 1))
    except (TypeError, ValueError):
        upage = 1
    UPER = 20
    uw, uargs = "deactivated=0", []
    if uq:
        if uq.isdigit():
            uw += " AND id=?"
            uargs.append(int(uq))
        else:
            uw += " AND username LIKE ?"
            uargs.append("%" + uq + "%")
    try:
        utotal = db.execute("SELECT COUNT(*) c FROM users WHERE " + uw, uargs).fetchone()['c']
    except Exception:
        utotal = 0
    upages = max(1, (utotal + UPER - 1) // UPER)
    if upage > upages:
        upage = upages
    users = db.execute(
        "SELECT * FROM users WHERE " + uw + " ORDER BY id ASC LIMIT ? OFFSET ?",
        uargs + [UPER, (upage - 1) * UPER]).fetchall()

    posts = db.execute("SELECT * FROM posts ORDER BY created_at DESC").fetchall()

    now = time.time()
    day0 = D['_monitor'].day_start_ts()

    def _n(sql, args=()):
        try:
            r = db.execute(sql, args).fetchone()
            return int((r[0] if r else 0) or 0)
        except Exception:
            return 0

    dash = {
        'users_total':  _n("SELECT COUNT(*) FROM users WHERE deactivated=0"),
        'users_online': _n("SELECT COUNT(*) FROM users WHERE deactivated=0 AND last_active >= ?",
                           (now - D['ONLINE_THRESHOLD'],)),
        'users_today':  _n("SELECT COUNT(*) FROM users WHERE deactivated=0 AND created_at >= ?", (day0,)),
        'users_banned': _n("SELECT COUNT(*) FROM users WHERE deactivated=0 AND banned_until != 0 "
                           "AND (banned_until = ? OR banned_until > ?)", (D['PERMANENT'], now)),
        'users_muted':  _n("SELECT COUNT(*) FROM users WHERE deactivated=0 AND muted_until != 0 "
                           "AND (muted_until = ? OR muted_until > ?)", (D['PERMANENT'], now)),
        'admins':       _n("SELECT COUNT(*) FROM users WHERE deactivated=0 AND is_admin = 1"),
        'posts_total':    _n("SELECT COUNT(*) FROM posts WHERE recycle = 0"),
        'posts_today':    _n("SELECT COUNT(*) FROM posts WHERE recycle = 0 AND created_at >= ?",
                             (day0,)),
        'comments_total': _n("SELECT COUNT(*) FROM post_comments"),
        'dynamics_total': _n("SELECT COUNT(*) FROM dynamics"),
        'recycle_total':  _n("SELECT COUNT(*) FROM posts WHERE recycle = 1"),
        'reports_pending': _n("SELECT COUNT(*) FROM reports WHERE status = 'pending'"),
        'appeals_pending': _n("SELECT COUNT(*) FROM appeals WHERE status = 'pending'"),
    }

    mon = {'sample': {}, 'summary': {}, 'services': {}}
    try:
        mon['sample'] = D['_monitor'].latest_sample() or {}
    except Exception:
        pass
    try:
        mon['summary'] = D['_monitor'].attack_summary() or {}
    except Exception:
        pass
    try:
        mon['services'] = D['_monitor'].service_status() or {}
    except Exception:
        pass

    game_ranks = []
    for _f, _label in _GAME_SCORE_FIELDS.items():
        try:
            _order = "ASC" if _f in _GAME_FIELD_IS_TIME else "DESC"
            rows = db.execute(
                "SELECT username, %s AS v FROM users WHERE %s > 0 ORDER BY %s %s LIMIT 3"
                % (_f, _f, _f, _order)).fetchall()
            if rows:
                game_ranks.append({
                    'field': _f, 'label': _label, 'rows': rows,
                    'is_time': _f in _GAME_FIELD_IS_TIME,
                })
        except Exception:
            pass

    # 论坛「官方公告区」的板块 id —— 后台那个「发布论坛公告」入口要用它跳转
    _ann_sec = db.execute(
        "SELECT id FROM forum_sections WHERE is_announcement=1 ORDER BY id LIMIT 1").fetchone()
    return render_template("admin.html", users=users, posts=posts,
                           uq=uq, upage=upage, upages=upages, utotal=utotal,
                           dash=dash, mon=mon, game_ranks=game_ranks,
                           game_fields=_GAME_SCORE_FIELDS,
                           game_time_fields=_GAME_FIELD_IS_TIME,
                           ann_section_id=(_ann_sec['id'] if _ann_sec else 0))






def admin_ban():
    me = D['current_user']()
    target_id = request.form.get("user_id")
    target = D['get_user_by_id'](target_id)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    ok, err = _check_punish_permission(me, target)
    if not ok:
        flash(err, "error")
        return redirect(url_for("admin"))
    mode = request.form.get("mode")
    if mode == "permanent" and not me["is_super_admin"]:
        flash("仅超级管理员可执行永久处罚", "error")
        return redirect(url_for("admin"))
    db = D['get_db']()
    reason = (request.form.get("reason") or "").strip()
    if mode == "lift":
        _lift_punishment(db, me, target, "ban", "管理员手动解除")
        D['add_notification'](target['id'], 'ban_lift', f'你的封禁已被 {me["username"]} 解除', None)
        flash("已解除封禁：%s" % target["username"], "ok")
        return redirect(url_for("admin"))
    until = _calc_until(request.form)
    db.execute("UPDATE users SET banned_until = ? WHERE id = ?", (until, target["id"]))
    _record_punishment(db, me, target, "ban", until, reason, _duration_text(request.form))
    _st = D['status_text'](until)
    D['add_notification'](target['id'], 'ban', f'你被管理员封禁了：{_st}', None)
    flash("封号操作已执行：%s -> %s" % (target["username"], D['status_text'](until)), "ok")
    return redirect(url_for("admin"))






def admin_mute():
    me = D['current_user']()
    target_id = request.form.get("user_id")
    target = D['get_user_by_id'](target_id)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    ok, err = _check_punish_permission(me, target)
    if not ok:
        flash(err, "error")
        return redirect(url_for("admin"))
    mode = request.form.get("mode")
    if mode == "permanent" and not me["is_super_admin"]:
        flash("仅超级管理员可执行永久处罚", "error")
        return redirect(url_for("admin"))
    db = D['get_db']()
    reason = (request.form.get("reason") or "").strip()
    if mode == "lift":
        _lift_punishment(db, me, target, "mute", "管理员手动解除")
        D['add_notification'](target['id'], 'mute_lift', f'你的禁言已被 {me["username"]} 解除', None)
        flash("已解除禁言：%s" % target["username"], "ok")
        return redirect(url_for("admin"))
    until = _calc_until(request.form)
    if float(D['_row_get'](target, 'shield_until', 0) or 0) > time.time():
        db.execute("UPDATE users SET shield_until=0 WHERE id=?", (target["id"],))
        D['consume_item'](target["id"], 'shield_card', 1) if D['get_consumable_count'](target["id"], 'shield_card') > 0 else None
        db.execute("INSERT INTO consumable_logs (user_id, item_key, detail, created_at) VALUES (?,?,?,?)",
                   (target["id"], 'shield_card', "免疫一次禁言处罚", time.time()))
        db.commit()
        D['add_notification'](target['id'], 'mute_lift', f'护盾卡生效：免疫了来自 {me["username"]} 的禁言处罚', None)
        flash("护盾卡已生效，本次禁言被豁免：%s" % target["username"], "ok")
        return redirect(url_for("admin"))
    db.execute("UPDATE users SET muted_until = ? WHERE id = ?", (until, target["id"]))
    _record_punishment(db, me, target, "mute", until, reason, _duration_text(request.form))
    _st = D['status_text'](until)
    D['add_notification'](target['id'], 'mute', f'你被管理员禁言了：{_st}', None)
    flash("禁言操作已执行：%s -> %s" % (target["username"], D['status_text'](until)), "ok")
    return redirect(url_for("admin"))






def admin_give_effect(user_id):
    me = D['current_user']()
    target = D['get_user_by_id'](user_id)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    if target['is_super_admin'] and not me['is_super_admin']:
        flash("无权给超管授予", "error")
        return redirect(url_for("admin"))
    if target['id'] == me['id']:
        flash("不能给自己授予", "error")
        return redirect(url_for("admin"))

    effect_type = request.form.get("effect_type") or "rainbow"
    action = request.form.get("action", "give")

    db = D['get_db']()
    if action == "remove":
        db.execute("UPDATE users SET has_effect = 0, effect_type = NULL WHERE id = ?", (user_id,))
        flash("已移除特效", "ok")
    else:
        db.execute("UPDATE users SET has_effect = 1, effect_type = ? WHERE id = ?", (effect_type, user_id))
        D['add_notification'](
            user_id,
            'effect',
            f'管理员授予了你特效：{effect_type}',
            None
        )
        flash("已授予特效", "ok")
    db.commit()
    return redirect(url_for("admin"))






def admin_give_badge(user_id):
    target = D['get_user_by_id'](user_id)
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    me = D['current_user']()
    if target['is_super_admin'] and not me['is_super_admin']:
        flash("无权给超管授予", "error")
        return redirect(url_for("admin"))
    # 这条路由本身已经是 @D['super_admin_required']，所以「自己给自己」不用拦 ——
    # 超管想挂个牌子在自己名字上，没必要非得开个小号来点 ✗（2026-10-10 放开）
    badge_text = (request.form.get("badge_text") or "").strip()
    badge_effect = request.form.get("badge_effect") or "gold"
    action = request.form.get("action", "give")
    
    db = D['get_db']()
    if action == "remove":
        db.execute("UPDATE users SET badge_text = NULL, badge_effect = NULL WHERE id = ?", (user_id,))
        flash("已移除荣誉牌子", "ok")
    else:
        if not badge_text:
            flash("牌子文字不能为空", "error")
            return redirect(url_for("admin"))
        if len(badge_text) > 10:
            flash("牌子文字不能超过10个字符", "error")
            return redirect(url_for("admin"))
        db.execute("UPDATE users SET badge_text = ?, badge_effect = ? WHERE id = ?", 
                  (badge_text, badge_effect, user_id))
        D['add_notification'](
            user_id,
            'badge',
            f'管理员授予了你荣誉牌子：{badge_text}',
            None
        )
        flash("已授予荣誉牌子", "ok")
    db.commit()
    return redirect(url_for("admin"))






def admin_batch_posts():
    user = D['current_user']()
    act = request.form.get("act") or ""
    ids = [int(i) for i in request.form.getlist("ids") if str(i).strip().isdigit()]
    if not ids:
        flash("没有选中任何帖子", "warn")
        return redirect(url_for("admin"))
    db = D['get_db']()
    q = ",".join("?" * len(ids))
    if act == "delete":
        db.execute("DELETE FROM posts WHERE id IN (%s)" % q, ids)
        db.execute("DELETE FROM post_likes WHERE post_id IN (%s)" % q, ids)
        db.execute("DELETE FROM post_comments WHERE post_id IN (%s)" % q, ids)
        db.commit()
        for i in ids:
            D['add_admin_log'](user, "post_delete", i, "批量删除")
        flash("已批量删除 %d 个帖子" % len(ids), "ok")
    elif act in ("pin", "unpin"):
        db.execute("UPDATE posts SET is_pinned = ? WHERE id IN (%s)" % q,
                   tuple([1 if act == "pin" else 0] + ids))
        db.commit()
        flash("已批量%s %d 个帖子" % ("置顶" if act == "pin" else "取消置顶", len(ids)), "ok")
    else:
        flash("未知操作", "error")
    return redirect(url_for("admin"))






def admin_delete_post(post_id):
    db = D['get_db']()
    db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.execute("DELETE FROM post_likes WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM post_comments WHERE post_id = ?", (post_id,))
    db.commit()
    flash("帖子已删除", "ok")
    return redirect(url_for("admin"))






def admin_appeals():
    me = D['current_user']()
    db = D['get_db']()
    appeals = db.execute(
        "SELECT a.*, p.duration_text, p.ptype, p.reason as punish_reason FROM appeals a "
        "LEFT JOIN punishment_records p ON a.punishment_id = p.id ORDER BY a.id DESC"
    ).fetchall()
    return render_template("admin_appeals.html", appeals=appeals, is_super=me['is_super_admin'])






def admin_appeal_review(aid, action):
    me = D['current_user']()
    db = D['get_db']()
    ap = db.execute("SELECT * FROM appeals WHERE id = ?", (aid,)).fetchone()
    if not ap or ap['status'] != 'pending':
        flash("申诉不存在或已审核", "error")
        return redirect(url_for("admin_appeals"))
    note = (request.form.get("admin_note") or "").strip()
    rec = db.execute("SELECT * FROM punishment_records WHERE id = ?", (ap['punishment_id'],)).fetchone()
    if action == "uphold":
        db.execute("UPDATE appeals SET status='upheld', admin_note=?, reviewer_id=?, reviewer_name=?, reviewed_at=? WHERE id=?",
                   (note, me['id'], me['username'], time.time(), aid))
        result_text = "申诉被驳回，处罚维持"
    elif action == "revoke":
        if rec:
            col = "banned_until" if rec['ptype'] == "ban" else "muted_until"
            db.execute("UPDATE users SET %s = 0 WHERE id = ?" % col, (rec['user_id'],))
            db.execute("UPDATE punishment_records SET status='revoked', lifted_at=?, lift_reason='申诉成功撤销' "
                       "WHERE id = ?", (time.time(), rec['id']))
        db.execute("UPDATE appeals SET status='revoked', admin_note=?, reviewer_id=?, reviewer_name=?, reviewed_at=? WHERE id=?",
                   (note, me['id'], me['username'], time.time(), aid))
        result_text = "申诉通过，处罚已撤销"
    else:
        flash("未知操作", "error")
        return redirect(url_for("admin_appeals"))
    db.commit()
    D['add_notification'](ap['user_id'], 'appeal_result',
                     f'你的申诉已处理：{result_text}', None)
    flash(result_text, "ok")
    return redirect(url_for("admin_appeals"))






def admin_reports():
    me = D['current_user']()
    db = D['get_db']()
    reports = db.execute(
        "SELECT * FROM reports ORDER BY id DESC"
    ).fetchall()
    return render_template("admin_reports.html", reports=reports,
                          is_super=me['is_super_admin'], REPORT_CATEGORIES=D['REPORT_CATEGORIES'])






def admin_report_ignore(rid):
    me = D['current_user']()
    db = D['get_db']()
    r = db.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
    if not r or r['status'] != 'pending':
        flash("举报不存在或已处理", "error")
        return redirect(url_for("admin_reports"))
    note = (request.form.get("handler_note") or "").strip()
    db.execute("UPDATE reports SET status='ignored', handler_id=?, handler_note=? WHERE id=?",
               (me['id'], note, rid))
    db.commit()
    D['add_notification'](r['reporter_id'], 'report_result',
                     f'你提交的举报（{r["category"]}）已处理：忽略', None)
    flash("已忽略该举报", "ok")
    return redirect(url_for("admin_reports"))






def admin_report_punish(rid):
    me = D['current_user']()
    db = D['get_db']()
    r = db.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
    if not r or r['status'] != 'pending':
        flash("举报不存在或已处理", "error")
        return redirect(url_for("admin_reports"))
    target_user = None
    if r['target_type'] in ("chat", "private"):
        m = db.execute("SELECT user_id FROM messages WHERE id = ?", (r['target_id'],)).fetchone()
        if m:
            target_user = D['get_user_by_id'](m['user_id'])
    elif r['target_type'] == "post":
        p = db.execute("SELECT user_id FROM posts WHERE id = ?", (r['target_id'],)).fetchone()
        if p:
            target_user = D['get_user_by_id'](p['user_id'])
    elif r['target_type'] == "comment":
        c = db.execute("SELECT user_id FROM post_comments WHERE id = ?", (r['target_id'],)).fetchone()
        if c:
            target_user = D['get_user_by_id'](c['user_id'])
    ptype = request.form.get("ptype") or "mute"
    if ptype not in ("ban", "mute"):
        ptype = "mute"
    if request.form.get("mode") == "permanent" and not me['is_super_admin']:
        flash("仅超级管理员可永久处罚", "error")
        return redirect(url_for("admin_reports"))
    if target_user and not target_user['is_admin']:
        ok, err = _check_punish_permission(me, target_user)
        if ok:
            until = _calc_until(request.form)
            col = "banned_until" if ptype == "ban" else "muted_until"
            db.execute("UPDATE users SET %s = ? WHERE id = ?" % col, (until, target_user['id']))
            _record_punishment(db, me, target_user, ptype, until,
                               "举报处理：" + r['category'], _duration_text(request.form))
    note = (request.form.get("handler_note") or "").strip()
    db.execute("UPDATE reports SET status='punished', handler_id=?, handler_note=? WHERE id=?",
               (me['id'], note, rid))
    db.commit()
    if target_user:
        D['add_exp'](target_user['id'], -15)
        _st = D['status_text'](_calc_until(request.form))
        D['add_notification'](target_user['id'], 'ban' if ptype == 'ban' else 'mute',
                         f'你因被举报（{r["category"]}）被{me["username"]}处罚：{_st}', None)
        D['add_notification'](r['reporter_id'], 'report_result',
                         f'你提交的举报（{r["category"]}）已处理：已对违规用户执行{("封禁" if ptype=="ban" else "禁言")}', None)
    flash("已对该举报执行处罚", "ok")
    return redirect(url_for("admin_reports"))






def admin_report_escalate(rid):
    me = D['current_user']()
    if me['is_super_admin']:
        flash("你是超级管理员，可直接处理", "warn")
        return redirect(url_for("admin_reports"))
    db = D['get_db']()
    r = db.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
    if not r or r['status'] != 'pending':
        flash("举报不存在或已处理", "error")
        return redirect(url_for("admin_reports"))
    note = (request.form.get("handler_note") or "").strip()
    db.execute("UPDATE reports SET status='escalated', handler_id=?, handler_note=? WHERE id=?",
               (me['id'], note, rid))
    db.commit()
    sup = db.execute("SELECT id FROM users WHERE is_super_admin = 1 LIMIT 1").fetchone()
    if sup:
        D['add_notification'](sup['id'], 'report_escalated',
                         f'普通管理员 {me["username"]} 上报了一条疑似严重违规举报（{r["category"]}）', url_for('admin_reports'))
    flash("已上报超级管理员", "ok")
    return redirect(url_for("admin_reports"))






def admin_sensitive():
    me = D['current_user']()
    db = D['get_db']()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            word = (request.form.get("word") or "").strip()
            cat = (request.form.get("category") or "默认").strip()
            if word:
                db.execute("INSERT OR IGNORE INTO sensitive_words (word, category) VALUES (?,?)", (word, cat))
                D['add_admin_log'](me, "sensitive_add", word, cat)
        elif action == "delete":
            wid = request.form.get("word_id")
            db.execute("DELETE FROM sensitive_words WHERE id = ?", (wid,))
            D['add_admin_log'](me, "sensitive_del", wid, "")
        elif action == "import":
            raw = request.form.get("bulk") or ""
            cnt = 0
            for line in raw.splitlines():
                w = line.strip()
                if w and not w.startswith("#"):
                    db.execute("INSERT OR IGNORE INTO sensitive_words (word, category) VALUES (?, '批量导入')", (w,))
                    cnt += 1
            D['add_admin_log'](me, "sensitive_import", "", "导入 %d 个" % cnt)
        elif action == "set_mode":
            D['set_config']("filter_mode", request.form.get("filter_mode", "replace"))
            D['set_config']("case_sensitive", "1" if request.form.get("case_sensitive") else "0")
            D['set_config']("fuzzy_match", "1" if request.form.get("fuzzy_match") else "0")
            D['set_config']("spam_autoblock", "1" if request.form.get("spam_autoblock") else "0")
            D['add_admin_log'](me, "sensitive_mode", "", "切换模式=%s" % D['get_config']("filter_mode"))
        db.commit()
        D['reload_sensitive_cache']()
        flash("敏感词库已更新", "ok")
        return redirect(url_for("admin_sensitive"))
    rows = db.execute("SELECT * FROM sensitive_words ORDER BY id DESC").fetchall()
    grouped = {}
    for w in rows:
        grouped.setdefault(w["category"] or "默认", []).append(w)
    return render_template("admin_sensitive.html", words=rows, grouped=grouped, total=len(rows),
                          filter_mode=D['get_config']("filter_mode", "replace"),
                          case_sensitive=D['get_config']("case_sensitive", "0"),
                          fuzzy_match=D['get_config']("fuzzy_match", "0"),
                          spam_autoblock=D['get_config']("spam_autoblock", "1"))






def admin_punishments():
    db = D['get_db']()
    records = db.execute("SELECT * FROM punishment_records ORDER BY id DESC LIMIT 200").fetchall()
    return render_template("admin_punishments.html", records=records)






def admin_recall_logs():
    db = D['get_db']()
    logs = db.execute("SELECT * FROM recall_logs ORDER BY id DESC LIMIT 200").fetchall()
    return render_template("admin_recall_logs.html", logs=logs)






def admin_admin_logs():
    db = D['get_db']()
    logs = db.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 200").fetchall()
    return render_template("admin_admin_logs.html", logs=logs)






def admin_monitor():
    return render_template("admin_monitor.html")






def api_monitor_live():
    """实时面板：服务器指标 + 攻击总览 + 服务状态。"""
    sample = D['_monitor'].latest_sample() or {}
    return jsonify({
        "ok": True,
        "sample": sample,
        "summary": D['_monitor'].attack_summary(),
        "services": D['_monitor'].service_status(),
        "server_time": time.time(),
    })






def api_monitor_series():
    """趋势数据：服务器指标时间序列 + 攻击事件时间序列。"""
    minutes = _mon_range()
    return jsonify({
        "ok": True,
        "minutes": minutes,
        "samples": D['_monitor'].sample_series(minutes),
        "attacks": D['_monitor'].attack_series(minutes),
    })






def api_monitor_sources():
    """攻击来源：Top IP（带归属地）+ 国家分布。"""
    try:
        days = int(request.args.get("days", "0"))
    except (ValueError, TypeError):
        days = 0
    return jsonify({
        "ok": True,
        "ips": D['_monitor'].top_attack_ips(limit=40, days=days or None),
        "countries": D['_monitor'].geo_distribution(days=days or None),
    })






def api_monitor_events():
    """攻击事件明细（支持分页，供前端滚动加载）。"""
    try:
        limit = int(request.args.get("limit", "60"))
    except (ValueError, TypeError):
        limit = 60
    try:
        offset = int(request.args.get("offset", "0"))
    except (ValueError, TypeError):
        offset = 0
    limit = max(1, min(limit, 300))
    offset = max(0, offset)
    return jsonify({
        "ok": True,
        "total": D['_monitor'].count_events(),
        "offset": offset,
        "events": D['_monitor'].recent_events(limit, offset),
    })






def admin_api_deep():
    """深度分析：用户结构 / 内容 / 社交 / 游戏 / 积分 / AI / 音乐 / 归属地 / 作弊信号。

    拆成 9 个 section，前端按需拉 —— 一次全塞会让后台首屏等太久。
    每个 section 独立 try，任一维度数据缺失不影响其他维度渲染。
    """
    db = D['get_db']()
    now = time.time()
    try:
        days = max(7, min(int(request.args.get('days', 30)), 90))
    except (TypeError, ValueError):
        days = 30
    since = now - days * 86400

    # ⚠️ 无参 SQL 必须调 db.execute(sql) 而不是 db.execute(sql, ()) ——
    # sqlite3 见到空 tuple 但语句里没有 ? 会抛「Incorrect number of bindings」，
    # 而下面 except 会把它吞成 None，症状是「数据全是 0」而不是报错，极难查。
    def q1(sql, args=None):
        try:
            return db.execute(sql, args).fetchone() if args else db.execute(sql).fetchone()
        except Exception:
            return None

    def qa(sql, args=None):
        try:
            rows = db.execute(sql, args).fetchall() if args else db.execute(sql).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    sec = {}

    # ───────── 1. 用户结构 ─────────
    u = {}
    r = q1("SELECT COUNT(*) c FROM users")
    u["total"] = r["c"] if r else 0
    r = q1("SELECT COUNT(*) c FROM users WHERE created_at>?", (since,))
    u["new"] = r["c"] if r else 0
    r = q1("SELECT COUNT(DISTINCT user_id) c FROM activity_log WHERE created_at>?", (since,))
    u["active"] = r["c"] if r else 0
    u["admin"] = (q1("SELECT COUNT(*) c FROM users WHERE is_admin=1") or {"c": 0})["c"]
    u["super"] = (q1("SELECT COUNT(*) c FROM users WHERE is_super_admin=1") or {"c": 0})["c"]
    u["banned"] = (q1("SELECT COUNT(*) c FROM users WHERE banned_until>0") or {"c": 0})["c"]
    u["muted"] = (q1("SELECT COUNT(*) c FROM users WHERE muted_until>0") or {"c": 0})["c"]
    u["deactivated"] = (q1("SELECT COUNT(*) c FROM users WHERE deactivated=1") or {"c": 0})["c"]
    u["onboarded"] = (q1("SELECT COUNT(*) c FROM users WHERE onboarded=1") or {"c": 0})["c"]
    u["profile_sig"] = (q1("SELECT COUNT(*) c FROM users WHERE signature IS NOT NULL AND signature!=''") or {"c": 0})["c"]
    u["profile_avatar"] = (q1("SELECT COUNT(*) c FROM users WHERE avatar IS NOT NULL AND avatar!=''") or {"c": 0})["c"]
    u["profile_title"] = (q1("SELECT COUNT(*) c FROM users WHERE custom_title IS NOT NULL AND custom_title!=''") or {"c": 0})["c"]
    u["profile_effect"] = (q1("SELECT COUNT(*) c FROM users WHERE has_effect=1") or {"c": 0})["c"]
    u["with_title"] = (q1("SELECT COUNT(*) c FROM users WHERE mall_title IS NOT NULL AND mall_title!=''") or {"c": 0})["c"]
    u["with_badge"] = (q1("SELECT COUNT(*) c FROM users WHERE mall_badge IS NOT NULL AND mall_badge!=''") or {"c": 0})["c"]
    # 等级分布
    u["levels"] = qa("SELECT level lv, COUNT(*) c FROM users GROUP BY level ORDER BY level LIMIT 15")
    # 注册时间段（按小时看他们什么时候注册）
    u["reg_hour"] = qa("SELECT CAST(strftime('%H', created_at,'unixepoch','+8 hours') AS INTEGER) h, "
                       "COUNT(*) c FROM users GROUP BY h ORDER BY h")
    # 活跃时段（按小时，跨天汇总）
    u["active_hour"] = qa("SELECT CAST(strftime('%H', created_at,'unixepoch','+8 hours') AS INTEGER) h, "
                          "COUNT(*) c FROM activity_log WHERE created_at>? GROUP BY h ORDER BY h", (since,))
    sec["users"] = u

    # ───────── 2. 活跃与留存 ─────────
    a = {}
    a["dau"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, "
                  "COUNT(DISTINCT user_id) dau, COUNT(*) pv "
                  "FROM activity_log WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    a["new_daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                        "FROM users WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    a["action"] = qa("SELECT action, COUNT(*) c, COUNT(DISTINCT user_id) u, "
                     "MAX(created_at) last FROM activity_log WHERE created_at>? "
                     "GROUP BY action ORDER BY c DESC LIMIT 30", (since,))
    # 活跃用户排行
    a["top_users"] = qa("SELECT username, COUNT(*) acts, COUNT(DISTINCT date(created_at,'unixepoch','+8 hours')) days "
                        "FROM activity_log WHERE created_at>? AND user_id>0 "
                        "GROUP BY user_id ORDER BY acts DESC LIMIT 20", (since,))
    # 周内分布
    a["dow"] = qa("SELECT CAST(strftime('%w', created_at,'unixepoch','+8 hours') AS INTEGER) w, "
                  "COUNT(DISTINCT user_id) users, COUNT(*) acts "
                  "FROM activity_log WHERE created_at>? GROUP BY w ORDER BY w", (since,))
    # 留存：注册后第 N 周是否还活跃（粗口径：注册后 30 天内是否有 activity）
    a["retention"] = qa(
        "SELECT u.id uid, u.created_at reg, "
        "(SELECT COUNT(*) FROM activity_log l WHERE l.user_id=u.id AND l.created_at>u.created_at) acts "
        "FROM users u WHERE u.created_at>? LIMIT 500", (since,))
    sec["activity"] = a

    # ───────── 3. 内容 ─────────
    c = {}
    c["posts"] = (q1("SELECT COUNT(*) c FROM posts") or {"c": 0})["c"]
    c["posts_week"] = (q1("SELECT COUNT(*) c FROM posts WHERE created_at>?", (now-7*86400,)) or {"c": 0})["c"]
    c["posts_recycle"] = (q1("SELECT COUNT(*) c FROM posts WHERE recycle=1") or {"c": 0})["c"]
    c["comments"] = (q1("SELECT COUNT(*) c FROM post_comments WHERE is_deleted=0") or {"c": 0})["c"]
    c["likes"] = (q1("SELECT COUNT(*) c FROM post_likes") or {"c": 0})["c"]
    c["favs"] = (q1("SELECT COUNT(*) c FROM favorites") or {"c": 0})["c"]
    c["views"] = (q1("SELECT COUNT(*) c FROM post_views") or {"c": 0})["c"]
    c["dynamics"] = (q1("SELECT COUNT(*) c FROM dynamics") or {"c": 0})["c"]
    c["polls"] = (q1("SELECT COUNT(*) c FROM polls") or {"c": 0})["c"]
    c["post_daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                         "FROM posts WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    c["comment_daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                            "FROM post_comments WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    c["sections"] = qa("SELECT s.name, COUNT(p.id) posts FROM forum_sections s "
                       "LEFT JOIN posts p ON p.section_id=s.id GROUP BY s.id ORDER BY posts DESC")
    c["top_posts"] = qa("SELECT title, username, likes, view_count, "
                        "(SELECT COUNT(*) FROM post_comments WHERE post_id=posts.id AND is_deleted=0) cmts "
                        "FROM posts WHERE recycle=0 ORDER BY likes DESC, view_count DESC LIMIT 10")
    c["top_authors"] = qa("SELECT username, COUNT(*) posts, SUM(likes) likes FROM posts "
                          "WHERE recycle=0 GROUP BY user_id ORDER BY posts DESC LIMIT 10")
    sec["content"] = c

    # ───────── 4. 社交 ─────────
    s = {}
    s["friends"] = (q1("SELECT COUNT(*) c FROM friends WHERE status=1") or {"c": 0})["c"]
    s["reqs"] = (q1("SELECT COUNT(*) c FROM friend_requests WHERE status='pending'") or {"c": 0})["c"]
    s["blacklist"] = (q1("SELECT COUNT(*) c FROM blacklist") or {"c": 0})["c"]
    s["groups"] = (q1("SELECT COUNT(*) c FROM groups") or {"c": 0})["c"]
    s["members"] = (q1("SELECT COUNT(*) c FROM group_members") or {"c": 0})["c"]
    s["rooms"] = (q1("SELECT COUNT(*) c FROM chat_rooms") or {"c": 0})["c"]
    s["group_msgs"] = (q1("SELECT COUNT(*) c FROM group_messages") or {"c": 0})["c"]
    s["group_msgs_week"] = (q1("SELECT COUNT(*) c FROM group_messages WHERE created_at>?", (now-7*86400,)) or {"c": 0})["c"]
    s["priv_msgs"] = (q1("SELECT COUNT(*) c FROM messages WHERE is_private=1") or {"c": 0})["c"]
    s["chat_msgs"] = (q1("SELECT COUNT(*) c FROM messages WHERE is_private=0") or {"c": 0})["c"]
    s["notifs"] = (q1("SELECT COUNT(*) c FROM notifications") or {"c": 0})["c"]
    s["notifs_week"] = (q1("SELECT COUNT(*) c FROM notifications WHERE created_at>?", (now-7*86400,)) or {"c": 0})["c"]
    s["notif_types"] = qa("SELECT type, COUNT(*) c FROM notifications GROUP BY type ORDER BY c DESC")
    s["chat_top"] = qa("SELECT username, COUNT(*) c FROM group_messages GROUP BY username ORDER BY c DESC LIMIT 15")
    s["priv_top"] = qa("SELECT username, COUNT(*) c FROM messages WHERE is_private=1 "
                       "GROUP BY username ORDER BY c DESC LIMIT 10")
    s["group_daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                          "FROM group_messages WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    s["fri_list"] = qa("SELECT user_id, username, status, created_at FROM friends LIMIT 30")
    sec["social"] = s

    # ───────── 5. 游戏 ─────────
    g = {}
    g["board"] = qa("SELECT game, COUNT(DISTINCT user_id) players, SUM(games) plays, "
                    "SUM(wins) wins, SUM(losses) losses, SUM(draws) draws "
                    "FROM board_game_stats GROUP BY game ORDER BY players DESC")
    g["board_total_users"] = (q1("SELECT COUNT(DISTINCT user_id) c FROM board_game_stats") or {"c": 0})["c"]
    g["board_total_plays"] = (q1("SELECT IFNULL(SUM(games),0) c FROM board_game_stats") or {"c": 0})["c"]
    g["replays"] = (q1("SELECT COUNT(*) c FROM game_replays") or {"c": 0})["c"]
    g["ach_unlocked"] = (q1("SELECT COUNT(*) c FROM game_achievements") or {"c": 0})["c"]
    g["game_stats_rows"] = (q1("SELECT COUNT(*) c FROM game_stats") or {"c": 0})["c"]
    g["silence"] = qa("SELECT user_id, total_achievements FROM game_stats ORDER BY total_achievements DESC LIMIT 10")
    cols = set(r[1] for r in qa("PRAGMA table_info(users)")) if False else set(
        r[1] for r in db.execute("PRAGMA table_info(users)").fetchall())
    mini = []
    for field, name in _GAME_SCORE_ROWS:
        if field not in cols:
            continue
        n = (q1("SELECT COUNT(*) c FROM users WHERE IFNULL(%s,0)>0" % field) or {"c": 0})["c"]
        top = (q1("SELECT MAX(%s) m FROM users" % field) or {"m": 0})["m"] or 0
        avg = (q1("SELECT IFNULL(AVG(%s),0) m FROM users WHERE IFNULL(%s,0)>0" % (field, field)) or {"m": 0})["m"] or 0
        mini.append({"name": name, "players": n, "best": int(top or 0), "avg": round(avg or 0, 1)})
    mini.sort(key=lambda x: (-x["players"], -x["best"]))
    g["mini"] = mini
    times = []
    for field, name in _GAME_TIME_ROWS:
        if field not in cols:
            continue
        n = (q1("SELECT COUNT(*) c FROM users WHERE IFNULL(%s,0)>0" % field) or {"c": 0})["c"]
        best = (q1("SELECT MIN(%s) m FROM users WHERE IFNULL(%s,0)>0" % (field, field)) or {"m": 0})["m"] or 0
        times.append({"name": name, "players": n, "best_ms": int(best or 0)})
    times.sort(key=lambda x: (-x["players"], x["best_ms"] if x["best_ms"] else 10**12))
    g["time"] = times
    g["xqm"] = qa("SELECT user_id, rating, wins, losses, draws FROM xqm_rating ORDER BY rating DESC LIMIT 10")
    g["xqp"] = (q1("SELECT COUNT(*) c FROM xq_puzzle_done") or {"c": 0})["c"]
    g["puzzle_ranking"] = qa("SELECT user_id, COUNT(*) solved FROM xq_puzzle_done GROUP BY user_id ORDER BY solved DESC LIMIT 10")
    g["hanoi"] = qa("SELECT username, hanoi_best_moves, hanoi_best_ms FROM users "
                     "WHERE IFNULL(hanoi_best_moves,0)>0 ORDER BY hanoi_best_moves LIMIT 10")
    sec["games"] = g

    # ───────── 6. 积分与商城 ─────────
    p = {}
    p["total_log"] = (q1("SELECT COUNT(*) c FROM points_log") or {"c": 0})["c"]
    p["sources"] = qa("SELECT source_type, COUNT(*) c, IFNULL(SUM(delta),0) amt "
                      "FROM points_log GROUP BY source_type ORDER BY amt DESC")
    p["week_delta"] = (q1("SELECT IFNULL(SUM(delta),0) s FROM points_log WHERE created_at>?", (now-7*86400,)) or {"s": 0})["s"]
    p["holders"] = (q1("SELECT COUNT(*) c FROM users WHERE IFNULL(coins,0)>0") or {"c": 0})["c"]
    p["coin_sum"] = (q1("SELECT IFNULL(SUM(coins),0) s FROM users") or {"s": 0})["s"]
    p["rich"] = qa("SELECT username, coins, level FROM users ORDER BY coins DESC LIMIT 10")
    p["poor"] = qa("SELECT username, coins FROM users ORDER BY coins ASC LIMIT 10")
    p["items"] = (q1("SELECT COUNT(*) c FROM user_items") or {"c": 0})["c"]
    p["item_types"] = qa("SELECT mi.type, COUNT(*) c FROM user_items ui JOIN mall_items mi ON mi.id=ui.item_id "
                         "GROUP BY mi.type ORDER BY c DESC")
    p["gacha"] = (q1("SELECT COUNT(*) c FROM gacha_logs") or {"c": 0})["c"]
    p["gacha_top"] = qa("SELECT user_id, rarity, COUNT(*) c FROM gacha_logs GROUP BY user_id, rarity "
                        "ORDER BY c DESC LIMIT 10")
    p["consumables"] = qa("SELECT item_key, IFNULL(SUM(count),0) c FROM user_consumables GROUP BY item_key ORDER BY c DESC")
    p["redeemed"] = qa("SELECT mi.name, COUNT(*) c FROM user_items ui JOIN mall_items mi ON mi.id=ui.item_id "
                       "GROUP BY ui.item_id ORDER BY c DESC LIMIT 12")
    sec["points"] = p

    # ───────── 7. AI ─────────
    ai = {}
    ai["chats"] = (q1("SELECT COUNT(*) c FROM ai_chat_history") or {"c": 0})["c"]
    ai["users"] = (q1("SELECT COUNT(DISTINCT user_id) c FROM ai_chat_history") or {"c": 0})["c"]
    ai["week"] = (q1("SELECT COUNT(*) c FROM ai_chat_history WHERE created_at>?", (now-7*86400,)) or {"c": 0})["c"]
    ai["daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                     "FROM ai_chat_history WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    ai["top"] = qa("SELECT user_id, COUNT(*) c FROM ai_chat_history GROUP BY user_id ORDER BY c DESC LIMIT 10")
    ai["codes"] = (q1("SELECT COUNT(*) c FROM ai_redeem_codes") or {"c": 0})["c"]
    ai["codes_used"] = (q1("SELECT COUNT(*) c FROM ai_redeem_used") or {"c": 0})["c"]
    ai["debug_left"] = (q1("SELECT IFNULL(SUM(ai_debug_left),0) s FROM users") or {"s": 0})["s"]
    ai["quota"] = (q1("SELECT IFNULL(SUM(ai_quota),0) s FROM users") or {"s": 0})["s"]
    sec["ai"] = ai

    # ───────── 8. 音乐 ─────────
    m = {"listeners": 0, "plays": 0, "total_ms": 0, "daily": [], "tracks": [],
         "listeners_list": [], "has_data": False}
    r = q1("SELECT COUNT(DISTINCT user_id) u, COUNT(*) c, IFNULL(SUM(played_ms),0) ms "
           "FROM music_play_logs WHERE created_at>?", (since,))
    if r and r["c"]:
        m["has_data"] = True
        m["listeners"] = r["u"] or 0
        m["plays"] = r["c"] or 0
        m["total_ms"] = r["ms"] or 0
        m["daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(DISTINCT user_id) u, "
                        "COUNT(*) c, IFNULL(SUM(played_ms),0) ms FROM music_play_logs "
                        "WHERE created_at>? GROUP BY d ORDER BY d", (since,))
        m["tracks"] = qa("SELECT track_id, title, artist, COUNT(*) plays, "
                         "IFNULL(SUM(played_ms),0) ms, COUNT(DISTINCT user_id) listeners, "
                         "SUM(finished) finished FROM music_play_logs WHERE created_at>? "
                         "GROUP BY track_id ORDER BY ms DESC LIMIT 15", (since,))
        m["listeners_list"] = qa("SELECT username, COUNT(*) plays, IFNULL(SUM(l.played_ms),0) ms, "
                                 "COUNT(DISTINCT l.track_id) tracks FROM music_play_logs l "
                                 "JOIN users u ON u.id=l.user_id WHERE l.created_at>? "
                                 "GROUP BY l.user_id ORDER BY ms DESC LIMIT 15", (since,))
    else:
        m["tracks"] = qa("SELECT id, title, artist, plays, IFNULL(duration,0) dur FROM music_tracks ORDER BY plays DESC")
    sec["music"] = m

    # ───────── 9. 归属地与作弊信号 ─────────
    g2 = {}
    g2["geo"] = qa("SELECT country, region, city, isp, COUNT(*) c FROM ip_geo GROUP BY country, region, city "
                   "ORDER BY c DESC LIMIT 15")
    g2["isp"] = qa("SELECT isp, COUNT(*) c FROM ip_geo GROUP BY isp ORDER BY c DESC LIMIT 10")
    g2["attacks"] = (q1("SELECT COUNT(*) c FROM attack_events WHERE ts>?", (since,)) or {"c": 0})["c"]
    g2["attack_kinds"] = qa("SELECT kind, COUNT(*) c, SUM(count) n FROM attack_events WHERE ts>? "
                             "GROUP BY kind ORDER BY n DESC LIMIT 10", (since,))
    g2["warnings"] = (q1("SELECT COUNT(*) c FROM warning_logs WHERE created_at>?", (since,)) or {"c": 0})["c"]
    g2["punishments"] = (q1("SELECT COUNT(*) c FROM punishment_records WHERE created_at>?", (since,)) or {"c": 0})["c"]
    g2["reports"] = (q1("SELECT COUNT(*) c FROM reports") or {"c": 0})["c"]
    g2["appeals"] = (q1("SELECT COUNT(*) c FROM appeals") or {"c": 0})["c"]
    g2["banned"] = qa("SELECT username, banned_until, muted_until FROM users "
                      "WHERE banned_until>0 OR muted_until>0 LIMIT 20")
    # 可疑成绩：明显超出人类极限的分数
    sus = []
    for field, name, cap in [("high_score_sudoku", "数独", 3000), ("high_score_24", "24点", 300),
                             ("high_score_2048", "2048", 100000),
                             ("high_score_tetris", "俄罗斯方块", 1000000),
                             ("high_score_jump", "跳一跳", 200), ("high_score_color_blind", "色盲", 200),
                             ("high_score_xianyi", "闲弈", 5000)]:
        if field in cols:
            for r2 in qa("SELECT id, username, %s v FROM users WHERE %s>? LIMIT 5" % (field, field), (cap,)):
                sus.append({"name": name, "username": r2["username"], "value": r2["v"], "cap": cap})
    g2["suspect"] = sus
    g2["ic_coders"] = (q1("SELECT COUNT(*) c FROM ic_coders") or {"c": 0})["c"]
    g2["sensitive"] = (q1("SELECT COUNT(*) c FROM sensitive_words") or {"c": 0})["c"]
    sec["security"] = g2

    # ───────── 10. 成长曲线 ─────────
    gr = {}
    gr["level_dist"] = qa("SELECT level lv, COUNT(*) c, AVG(coins) ac, AVG(exp) ae "
                          "FROM users GROUP BY level ORDER BY level LIMIT 20")
    gr["coin_buckets"] = qa(
        "SELECT CASE WHEN coins=0 THEN '0' WHEN coins<50 THEN '1-49' "
        "WHEN coins<200 THEN '50-199' WHEN coins<500 THEN '200-499' "
        "WHEN coins<1000 THEN '500-999' WHEN coins<3000 THEN '1000-2999' ELSE '3000+' END b, "
        "COUNT(*) c FROM users GROUP BY b ORDER BY MIN(coins)")
    gr["likes_dist"] = qa(
        "SELECT CASE WHEN total_likes=0 THEN '0' WHEN total_likes<10 THEN '1-9' "
        "WHEN total_likes<50 THEN '10-49' WHEN total_likes<100 THEN '50-99' ELSE '100+' END b, "
        "COUNT(*) c FROM users GROUP BY b ORDER BY MIN(total_likes)")
    gr["sign_streak"] = qa(
        "SELECT CASE WHEN IFNULL(checkin_streak,0)=0 THEN '0' WHEN checkin_streak<7 THEN '1-6' "
        "WHEN checkin_streak<30 THEN '7-29' WHEN checkin_streak<100 THEN '30-99' ELSE '100+' END b, "
        "COUNT(*) c FROM users GROUP BY b ORDER BY MIN(checkin_streak)")
    gr["top_exp"] = qa("SELECT username, level, exp, coins FROM users ORDER BY exp DESC LIMIT 10")
    gr["days_since_reg"] = qa(
        "SELECT CASE WHEN (strftime('%s','now')-created_at)/86400 < 7 THEN '本周' "
        "WHEN (strftime('%s','now')-created_at)/86400 < 30 THEN '7-30天' "
        "WHEN (strftime('%s','now')-created_at)/86400 < 90 THEN '30-90天' ELSE '90天+' END b, "
        "COUNT(*) c FROM users GROUP BY b "
        "ORDER BY MIN((strftime('%s','now')-created_at)/86400)")
    sec["growth"] = gr

    # ───────── 11. 签到与日常任务 ─────────
    sk = {}
    sk["sign_total"] = (q1("SELECT COUNT(*) c FROM sign_logs") or {"c": 0})["c"]
    sk["sign_week"] = (q1("SELECT COUNT(*) c FROM sign_logs WHERE created_at>?", (now-7*86400,)) or {"c": 0})["c"]
    sk["sign_makeup"] = (q1("SELECT COUNT(*) c FROM sign_logs WHERE is_makeup=1") or {"c": 0})["c"]
    sk["sign_users"] = (q1("SELECT COUNT(DISTINCT user_id) c FROM sign_logs") or {"c": 0})["c"]
    sk["sign_daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                          "FROM sign_logs WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    sk["sign_month"] = qa("SELECT strftime('%Y-%m', created_at,'unixepoch','+8 hours') m, "
                          "COUNT(*) c, COUNT(DISTINCT user_id) u FROM sign_logs GROUP BY m ORDER BY m LIMIT 8")
    sk["sign_hour"] = qa("SELECT CAST(strftime('%H', created_at,'unixepoch','+8 hours') AS INTEGER) h, "
                         "COUNT(*) c FROM sign_logs GROUP BY h ORDER BY h")
    sk["tasks"] = {}
    try:
        for t in D['get_tasks_data'](q1("SELECT id FROM users WHERE is_admin=0 LIMIT 1")['id']):
            if t['group'] != 'daily':
                continue
            sk["tasks"][t['key']] = {"name": t['name'], "progress": t['progress'],
                                     "target": t['target'], "claimed": t['claimed']}
    except Exception:
        pass
    sk["task_daily"] = qa("SELECT task_key, COUNT(*) c, SUM(claimed) claimed FROM user_tasks "
                          "WHERE created_at>? GROUP BY task_key ORDER BY c DESC LIMIT 20", (since,))
    sk["achievement_daily"] = qa("SELECT task_key, COUNT(*) c FROM user_tasks "
                                 "WHERE period='ach' AND created_at>? GROUP BY task_key "
                                 "ORDER BY c DESC LIMIT 30", (since,))
    sec["checkin"] = sk

    # ───────── 12. 商城与扭蛋 ─────────
    sh = {}
    sh["mall_items"] = (q1("SELECT COUNT(*) c FROM mall_items") or {"c": 0})["c"]
    sh["mall_by_type"] = qa("SELECT type, COUNT(*) c, IFNULL(MIN(cost),0) mn, "
                            "IFNULL(MAX(cost),0) mx FROM mall_items GROUP BY type ORDER BY c DESC")
    sh["owned_items"] = (q1("SELECT COUNT(*) c FROM user_items") or {"c": 0})["c"]
    sh["equipped"] = (q1("SELECT COUNT(*) c FROM user_items WHERE is_equipped=1") or {"c": 0})["c"]
    sh["gacha_total"] = (q1("SELECT COUNT(*) c FROM gacha_logs") or {"c": 0})["c"]
    sh["gacha_rarity"] = qa("SELECT rarity, COUNT(*) c FROM gacha_logs GROUP BY rarity ORDER BY c DESC")
    sh["gacha_daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                           "FROM gacha_logs WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    sh["gacha_users"] = (q1("SELECT COUNT(DISTINCT user_id) c FROM gacha_logs") or {"c": 0})["c"]
    sh["consumables"] = qa("SELECT item_key, IFNULL(SUM(count),0) c, COUNT(DISTINCT user_id) u "
                           "FROM user_consumables GROUP BY item_key ORDER BY c DESC")
    sh["consume_log"] = qa("SELECT item_key, COUNT(*) c FROM consumable_logs "
                           "WHERE created_at>? GROUP BY item_key ORDER BY c DESC", (since,))
    sh["equipped_list"] = qa("SELECT u.username, mi.name, mi.type FROM user_items ui "
                             "JOIN mall_items mi ON mi.id=ui.item_id "
                             "JOIN users u ON u.id=ui.user_id WHERE ui.is_equipped=1 LIMIT 30")
    sec["shop"] = sh

    # ───────── 13. 帖子与互动细节 ─────────
    pd = {}
    pd["title_len"] = qa("SELECT LENGTH(title)/10*10 b, COUNT(*) c FROM posts WHERE recycle=0 "
                         "GROUP BY b ORDER BY b LIMIT 12")
    pd["body_len"] = qa("SELECT CASE WHEN LENGTH(content)<100 THEN '<100' "
                        "WHEN LENGTH(content)<500 THEN '100-500' "
                        "WHEN LENGTH(content)<2000 THEN '500-2000' "
                        "WHEN LENGTH(content)<5000 THEN '2000-5000' ELSE '5000+' END b, COUNT(*) c "
                        "FROM posts WHERE recycle=0 GROUP BY b ORDER BY MIN(LENGTH(content))")
    pd["post_hour"] = qa("SELECT CAST(strftime('%H', created_at,'unixepoch','+8 hours') AS INTEGER) h, "
                         "COUNT(*) c FROM posts GROUP BY h ORDER BY h")
    pd["zero_comment"] = (q1("SELECT COUNT(*) c FROM posts p WHERE p.recycle=0 AND NOT EXISTS "
                             "(SELECT 1 FROM post_comments x WHERE x.post_id=p.id AND x.is_deleted=0)")
                          or {"c": 0})["c"]
    pd["never_liked"] = (q1("SELECT COUNT(*) c FROM posts WHERE recycle=0 AND likes=0") or {"c": 0})["c"]
    pd["never_viewed"] = (q1("SELECT COUNT(*) c FROM posts WHERE recycle=0 AND view_count=0") or {"c": 0})["c"]
    pd["with_cover"] = (q1("SELECT COUNT(*) c FROM posts WHERE recycle=0 AND (image IS NOT NULL "
                           "AND image!='' OR cover IS NOT NULL AND cover!='')") or {"c": 0})["c"]
    pd["recent"] = qa("SELECT id, title, username, likes, view_count, created_at FROM posts "
                      "WHERE recycle=0 ORDER BY created_at DESC LIMIT 10")
    sec["post"] = pd

    # ───────── 14. 站点健康 ─────────
    h2 = {}
    h2["http_errors"] = (q1("SELECT COUNT(*) c FROM attack_events WHERE kind LIKE '%error%' "
                            "AND ts>?", (since,)) or {"c": 0})["c"]
    h2["rate_limited"] = (q1("SELECT COUNT(*) c FROM attack_events WHERE kind LIKE '%rate%' "
                             "AND ts>?", (since,)) or {"c": 0})["c"]
    h2["sio_reject"] = (q1("SELECT COUNT(*) c FROM attack_events WHERE kind='sio_reject' "
                           "AND ts>?", (since,)) or {"c": 0})["c"]
    h2["pw_fail"] = (q1("SELECT COUNT(*) c FROM attack_events WHERE kind LIKE '%pass%' "
                         "AND ts>?", (since,)) or {"c": 0})["c"]
    h2["captcha_fail"] = (q1("SELECT COUNT(*) c FROM attack_events WHERE kind LIKE '%captcha%' "
                             "AND ts>?", (since,)) or {"c": 0})["c"]
    h2["attack_daily"] = qa("SELECT date(ts,'unixepoch','+8 hours') d, COUNT(*) c "
                            "FROM attack_events WHERE ts>? GROUP BY d ORDER BY d", (since,))
    h2["ip_top"] = qa("SELECT ip, COUNT(*) c FROM attack_events WHERE ts>? GROUP BY ip "
                      "ORDER BY c DESC LIMIT 10", (since,))
    h2["path_top"] = qa("SELECT path, COUNT(*) c FROM attack_events WHERE ts>? GROUP BY path "
                        "ORDER BY c DESC LIMIT 10", (since,))
    h2["ua_top"] = qa("SELECT ua, COUNT(*) c FROM activity_log WHERE ua IS NOT NULL AND ua!='' "
                      "GROUP BY ua ORDER BY c DESC LIMIT 8", (since,))
    h2["sensitive_cats"] = qa("SELECT category, COUNT(*) c FROM sensitive_words GROUP BY category "
                              "ORDER BY c DESC LIMIT 10")
    sec["health"] = h2

    # ───────── 15. 全站排行榜（分项目） ─────────
    lk = {}
    lk["score_boards"] = []
    for field, name in _GAME_SCORE_ROWS:
        if field not in cols:
            continue
        rows = qa("SELECT username, %s v FROM users WHERE IFNULL(%s,0)>0 "
                  "ORDER BY %s DESC LIMIT 8" % (field, field, field))
        if rows:
            lk["score_boards"].append({"name": name, "rows": rows})
    lk["time_boards"] = []
    for field, name in _GAME_TIME_ROWS:
        if field not in cols:
            continue
        rows = qa("SELECT username, %s v FROM users WHERE IFNULL(%s,0)>0 "
                  "ORDER BY %s ASC LIMIT 8" % (field, field, field))
        if rows:
            lk["time_boards"].append({"name": name, "rows": rows})
    lk["total_projects"] = len(lk["score_boards"]) + len(lk["time_boards"])
    sec["leaderboard"] = lk

    # ───────── 16. 消息与通知流 ─────────
    nf = {}
    nf["total"] = (q1("SELECT COUNT(*) c FROM notifications") or {"c": 0})["c"]
    nf["unread"] = (q1("SELECT COUNT(*) c FROM notifications WHERE is_read=0") or {"c": 0})["c"]
    nf["read"] = (q1("SELECT COUNT(*) c FROM notifications WHERE is_read=1") or {"c": 0})["c"]
    nf["week"] = (q1("SELECT COUNT(*) c FROM notifications WHERE created_at>?", (now-7*86400,)) or {"c": 0})["c"]
    nf["read_rate"] = (round((q1("SELECT COUNT(*) c FROM notifications WHERE is_read=1") or {"c":0})['c']
                    * 100.0 / max(1, (q1("SELECT COUNT(*) c FROM notifications") or {"c":0})['c']), 1))
    nf["types"] = qa("SELECT type, COUNT(*) c, SUM(is_read=0) unread FROM notifications "
                     "GROUP BY type ORDER BY c DESC LIMIT 15")
    nf["daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                     "FROM notifications WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    nf["by_user"] = qa("SELECT username, COUNT(*) c FROM notifications GROUP BY username "
                        "ORDER BY c DESC LIMIT 12")
    nf["recent"] = qa("SELECT type, content, link_url, is_read, created_at FROM notifications "
                      "ORDER BY created_at DESC LIMIT 20")
    nf["priv_daily"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                          "FROM messages WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    nf["priv_pairs"] = qa(
        "SELECT a.username x, b.username y, COUNT(*) c FROM messages m "
        "JOIN users a ON a.id=m.user_id JOIN users b ON b.id=m.receiver_id "
        "WHERE m.is_private=1 GROUP BY m.user_id, m.receiver_id ORDER BY c DESC LIMIT 12")
    nf["group_daily2"] = qa("SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c "
                            "FROM group_messages WHERE created_at>? GROUP BY d ORDER BY d", (since,))
    nf["active_hours"] = qa("SELECT CAST(strftime('%H', created_at,'unixepoch','+8 hours') AS INTEGER) h, "
                            "COUNT(*) c FROM messages GROUP BY h ORDER BY h")
    nf["file_uploads"] = (q1("SELECT COUNT(*) c FROM group_files") or {"c": 0})["c"]
    nf["file_types"] = qa("SELECT file_type, COUNT(*) c FROM group_files GROUP BY file_type ORDER BY c DESC")
    nf["music_msgs"] = (q1("SELECT COUNT(*) c FROM messages WHERE content LIKE '%http%'") or {"c": 0})["c"]
    sec["notify"] = nf

    # ───────── 17. 系统运行 ─────────
    sy = {}
    try:
        ms = qa("SELECT id, ts, cpu_pct, load1, load5, load15, mem_used_mb FROM monitor_samples "
                "WHERE ts>? ORDER BY id DESC LIMIT 1", (now-3600,))
        sy["sample"] = ms[0] if ms else {}
        agg = qa("SELECT IFNULL(AVG(cpu_pct),0) cpu, IFNULL(MAX(cpu_pct),0) cpu_max, "
                 "IFNULL(AVG(load1),0) load, IFNULL(AVG(mem_used_mb),0) mem, "
                 "IFNULL(MAX(mem_used_mb),0) mem_max, COUNT(*) n "
                 "FROM monitor_samples WHERE ts>?", (since,))
        sy["avg"] = agg[0] if agg else {}
    except Exception:
        sy["sample"] = {}; sy["avg"] = {}
    try:
        sy["cpu_hist"] = qa("SELECT id, cpu_pct FROM monitor_samples WHERE ts>? ORDER BY id ASC LIMIT 60",
                            (now-3600,))
    except Exception:
        sy["cpu_hist"] = []
    sy["webhook"] = (q1("SELECT COUNT(*) c FROM site_config WHERE key LIKE '%webhook%'") or {"c": 0})["c"]
    sy["announcements"] = (q1("SELECT COUNT(*) c FROM site_announcement") or {"c": 0})["c"]
    sy["horn"] = (q1("SELECT COUNT(*) c FROM horn_announcements") or {"c": 0})["c"]
    sy["changelog"] = (q1("SELECT COUNT(*) c FROM changelog") or {"c": 0})["c"]
    sy["admin_logs"] = (q1("SELECT COUNT(*) c FROM admin_logs WHERE created_at>?", (since,)) or {"c": 0})["c"]
    sy["admin_kinds"] = qa("SELECT action, COUNT(*) c FROM admin_logs WHERE created_at>? "
                           "GROUP BY action ORDER BY c DESC LIMIT 12", (since,))
    sy["recall"] = (q1("SELECT COUNT(*) c FROM recall_logs WHERE recalled_at>?", (since,)) or {"c": 0})["c"]
    sy["recycle"] = (q1("SELECT COUNT(*) c FROM posts WHERE recycle=1") or {"c": 0})["c"]
    sy["db_tables"] = len(qa("SELECT name FROM sqlite_master WHERE type='table'"))
    sy["db_indexes"] = len(qa("SELECT name FROM sqlite_master WHERE type='index' "
                              "AND name NOT LIKE 'sqlite_%'"))
    sy["db_views"] = len(qa("SELECT name FROM sqlite_master WHERE type='view'"))
    sy["upload_files"] = (q1("SELECT COUNT(*) c FROM user_files") or {"c": 0})["c"]
    sy["tables_top"] = qa("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 60")
    try:
        # D['app'] 是本模块的 Flask 单例，比 current_app 更可靠 ——
        # 包装器（@D['admin_required']）里 current_app 可能已被别的逻辑压过。
        sy["route_count"] = len(list(D['app'].url_map.iter_rules()))
    except Exception:
        sy["route_count"] = 0
    # root_path 在线上（waitress 直接跑 module 时）可能指向站点根而不是代码目录，
    # 所以直接数 D['_CODE_DIR'] 下的 templates，不依赖 root_path。
    try:
        _tpl = os.path.join(D['_CODE_DIR'], "templates")
        sy["template_count"] = len([t for t in os.listdir(_tpl) if t.endswith(".html")])
    except Exception:
        sy["template_count"] = 0
    sec["system"] = sy

    # ───────── 18. 设备与体验 ─────────
    ex = {}
    try:
        ex["ua_raw"] = qa("SELECT ua, COUNT(*) c FROM activity_log WHERE ua!='' "
                          "AND created_at>? GROUP BY ua ORDER BY c DESC LIMIT 20", (since,))
        ua_all = " ".join([r["ua"] for r in ex["ua_raw"]])
        ex["has_mobile"] = 1 if ("Mobile" in ua_all or "Android" in ua_all) else 0
        ex["has_wechat"] = 1 if "MicroMessenger" in ua_all else 0
        ex["has_safari"] = 1 if "Safari" in ua_all else 0
        ex["has_chrome"] = 1 if "Chrome" in ua_all else 0
    except Exception:
        ex["ua_raw"] = []
    _onb = (q1("SELECT COUNT(*) c FROM users WHERE onboarded=1") or {"c": 0})['c']
    _tot = (q1("SELECT COUNT(*) c FROM users") or {"c": 0})['c']
    ex["onboard_rate"] = round(_onb * 100.0 / max(1, _tot), 1)
    ex["onboard_week"] = (q1("SELECT COUNT(*) c FROM users WHERE onboarded=1 AND created_at>?",
                             (now-7*86400,)) or {"c": 0})["c"]
    ex["thumb_users"] = (q1("SELECT COUNT(DISTINCT user_id) c FROM group_files WHERE img_meta!=''")
                         or {"c": 0})["c"]
    ex["shortcut_hint"] = 0
    sec["experience"] = ex

    return jsonify({"ok": True, "days": days, "sections": sec})






def admin_insights():
    return render_template("admin_insights.html", active="admin")






def admin_api_insights():
    """运营分析数据：日活曲线、功能使用排行、游戏热度、音乐收听。"""
    db = D['get_db']()
    try:
        days = max(7, min(int(request.args.get('days', 30)), 90))
    except (TypeError, ValueError):
        days = 30
    since = time.time() - days * 86400

    # ── 日活 / 新增 / 活跃时长 ──
    dau = []
    rows = db.execute(
        "SELECT date(created_at,'unixepoch','+8 hours') d, "
        "COUNT(DISTINCT user_id) dau, COUNT(*) pv "
        "FROM activity_log WHERE created_at>? GROUP BY d ORDER BY d", (since,)).fetchall()
    cur_map = {r['d']: (r['dau'], r['pv']) for r in rows}
    sign_map = {r['d']: r['c'] for r in db.execute(
        "SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c FROM posts "
        "WHERE created_at>? GROUP BY d", (since,)).fetchall()}
    reg_map = {r['d']: r['c'] for r in db.execute(
        "SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(*) c FROM users "
        "WHERE created_at>? GROUP BY d", (since,)).fetchall()}
    # 补齐没有活动��日期（否则曲线会跳空）
    d0 = time.localtime(since)
    cursor = time.mktime((d0.tm_year, d0.tm_mon, d0.tm_mday, 0, 0, 0, 0, 0, -1)) - 28800
    labels, v_dau, v_post, v_reg = [], [], [], []
    while cursor < time.time():
        key = time.strftime('%Y-%m-%d', time.localtime(cursor))
        a, b = cur_map.get(key, (0, 0))
        labels.append(key[5:])
        v_dau.append(a)
        v_post.append(sign_map.get(key, 0))
        v_reg.append(reg_map.get(key, 0))
        cursor += 86400

    # ── 功能使用排行（activity_log.action）──
    acts = db.execute(
        "SELECT action, COUNT(*) c, COUNT(DISTINCT user_id) u "
        "FROM activity_log WHERE created_at>? GROUP BY action ORDER BY c DESC LIMIT 14",
        (since,)).fetchall()

    # ── 游戏热度 ──
    games = db.execute(
        "SELECT game, COUNT(DISTINCT user_id) players, SUM(games) plays, SUM(wins) wins "
        "FROM board_game_stats GROUP BY game ORDER BY players DESC").fetchall()

    # ── 小游戏高分（单机类，users 列）──
    cols = set(r[1] for r in db.execute("PRAGMA table_info(users)").fetchall())
    mini = []
    for field, name in _GAME_SCORE_ROWS:
        if field not in cols:
            continue
        n = db.execute("SELECT COUNT(*) c FROM users WHERE IFNULL(%s,0)>0" % field).fetchone()['c']
        top = db.execute("SELECT MAX(%s) m FROM users" % field).fetchone()['m'] or 0
        if n > 0 or (top or 0) > 0:
            mini.append({"name": name, "players": n, "best": int(top)})
    mini.sort(key=lambda x: x['players'], reverse=True)

    # ── 音乐收听 ──
    music = {"listeners": 0, "plays": 0, "total_ms": 0, "days": [], "tracks": [], "listeners_list": []}
    try:
        m0 = db.execute(
            "SELECT COUNT(DISTINCT user_id) u, COUNT(*) c, IFNULL(SUM(played_ms),0) ms "
            "FROM music_play_logs WHERE created_at>?", (since,)).fetchone()
        music["listeners"] = m0['u'] or 0
        music["plays"] = m0['c'] or 0
        music["total_ms"] = m0['ms'] or 0
        mrows = db.execute(
            "SELECT date(created_at,'unixepoch','+8 hours') d, COUNT(DISTINCT user_id) u, "
            "COUNT(*) c, IFNULL(SUM(played_ms),0) ms FROM music_play_logs "
            "WHERE created_at>? GROUP BY d ORDER BY d", (since,)).fetchall()
        mm = {r['d']: (r['u'], r['c'], r['ms']) for r in mrows}
        music["days"] = [{"d": labels[i], "listeners": mm.get(labels[i], (0, 0, 0))[0]}
                         for i in range(len(labels))] if False else []
        cur2 = time.mktime((d0.tm_year, d0.tm_mon, d0.tm_mday, 0, 0, 0, 0, 0, -1)) - 28800
        for i in range(len(labels)):
            key = time.strftime('%Y-%m-%d', time.localtime(cur2))
            u, c, ms = mm.get(key, (0, 0, 0))
            music["days"].append({"d": labels[i], "listeners": u, "plays": c, "ms": ms})
            cur2 += 86400
        music["tracks"] = [dict(r) for r in db.execute(
            "SELECT title, artist, COUNT(*) plays, IFNULL(SUM(played_ms),0) ms, "
            "COUNT(DISTINCT user_id) listeners FROM music_play_logs "
            "WHERE created_at>? GROUP BY track_id ORDER BY ms DESC LIMIT 10", (since,)).fetchall()]
        music["listeners_list"] = [dict(r) for r in db.execute(
            "SELECT u.username, COUNT(*) plays, IFNULL(SUM(l.played_ms),0) ms "
            "FROM music_play_logs l JOIN users u ON u.id=l.user_id "
            "WHERE l.created_at>? GROUP BY l.user_id ORDER BY ms DESC LIMIT 10", (since,)).fetchall()]
    except Exception:
        pass

    # ── 智能判断 ──
    insights = _gen_insights(db, days, labels, v_dau, acts, games, mini, music)

    return jsonify({
        "ok": True, "days": days,
        "labels": labels,
        "dau": v_dau, "posts": v_post, "regs": v_reg,
        "actions": [dict(r) for r in acts],
        "games": [dict(r) for r in games],
        "mini_games": mini,
        "music": music,
        "insights": insights,
    })






def admin_rooms():
    me = D['current_user']()
    db = D['get_db']()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = (request.form.get("name") or "").strip()
            desc = (request.form.get("description") or "").strip()
            if name:
                order = db.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS o FROM chat_rooms").fetchone()['o']
                db.execute("INSERT INTO chat_rooms (name,description,sort_order,created_at) VALUES (?,?,?,?)",
                           (name, desc, order, time.time()))
                D['add_admin_log'](me, "room_add", name, desc)
        elif action == "edit":
            rid = request.form.get("room_id")
            name = (request.form.get("name") or "").strip()
            desc = (request.form.get("description") or "").strip()
            if rid and name:
                db.execute("UPDATE chat_rooms SET name=?,description=? WHERE id=?", (name, desc, rid))
                D['add_admin_log'](me, "room_edit", rid, name)
        elif action == "toggle":
            rid = request.form.get("room_id")
            field = request.form.get("field")
            if rid and field in ("is_closed", "file_upload", "all_muted", "guest_readonly"):
                db.execute("UPDATE chat_rooms SET %s = 1 - %s WHERE id=?" % (field, field), (rid,))
                D['add_admin_log'](me, "room_toggle", rid, field)
        elif action == "delete":
            rid = request.form.get("room_id")
            cnt = db.execute("SELECT COUNT(*) c FROM messages WHERE room_id=?", (rid,)).fetchone()['c']
            if rid and cnt == 0:
                db.execute("DELETE FROM chat_rooms WHERE id=?", (rid,))
                D['add_admin_log'](me, "room_delete", rid, "")
        db.commit()
        return redirect(url_for("admin_rooms"))
    rooms = db.execute("SELECT * FROM chat_rooms ORDER BY sort_order ASC").fetchall()
    return render_template("admin_rooms.html", rooms=rooms)






def api_admin_give_points():
    """超管手动发/扣积分（强制写 admin_id 留痕），支持用户名或用户 ID"""
    me = D['current_user']()
    uid_raw = (request.form.get("user_id") or "").strip()
    delta = request.form.get("delta", type=int)
    reason = (request.form.get("reason") or "").strip()
    target = None
    if uid_raw.isdigit():
        target = D['get_user_by_id'](int(uid_raw))
    if not target:
        target = D['get_user_by_name'](uid_raw)
    if not target:
        return jsonify({"ok": False, "msg": "用户不存在"}), 404
    if not delta or abs(delta) > 10000:
        return jsonify({"ok": False, "msg": "积分数值非法（-10000 ~ 10000）"}), 400
    if delta < 0 and not reason:
        return jsonify({"ok": False, "msg": "扣分必须填写原因"}), 400
    D['award'](target['id'], delta, 'admin', reason or "管理员调整", admin_id=me['id'])
    D['add_admin_log'](me, "积分调整", target['username'],
                  "%+d 积分（%s）" % (delta, reason or "调整"))
    return jsonify({"ok": True, "msg": "已为 %s 调整 %+d 积分" % (target['username'], delta)})






def admin_set_ai_quota():
    """超管设置单个用户的 AI 每日对话轮数上限（-1=不限 0=禁止 N=每日上限）。"""
    me = D['current_user']()
    uid = request.form.get("user_id", type=int)
    quota = request.form.get("ai_quota", type=int)
    target = D['get_user_by_id'](uid) if uid else None
    if not target:
        flash("用户不存在", "error")
        return redirect(url_for("admin"))
    if quota is None or quota < -1 or quota > 9999:
        flash("配额数值非法（-1 ~ 9999）", "error")
        return redirect(url_for("admin"))
    db = D['get_db']()
    db.execute("UPDATE users SET ai_quota=? WHERE id=?", (quota, target['id']))
    db.commit()
    quota_txt = { -1: '不限', 0: '禁止使用' }.get(quota, '每日 %d 轮' % quota)
    D['add_admin_log'](me, "AI配额", target['username'], quota_txt)
    flash("已将 %s 的 AI 配额设为：%s" % (target['username'], quota_txt), "ok")
    return redirect(url_for("admin"))






def api_admin_users_batch():
    """对一批用户执行同一个操作。"""
    me = D['current_user']()
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()
    raw_ids = data.get('user_ids') or []

    if action not in _BATCH_ACTION_LABEL:
        return jsonify({"ok": False, "msg": "未知操作"}), 400
    if action in _BATCH_SUPER_ONLY and not me['is_super_admin']:
        return jsonify({"ok": False, "msg": "该操作仅超级管理员可用"}), 403

    ids = []
    for x in raw_ids:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v and v not in ids:
            ids.append(v)
    if not ids:
        return jsonify({"ok": False, "msg": "请先选择用户"}), 400
    if len(ids) > 500:
        return jsonify({"ok": False, "msg": "一次最多处理 500 个用户"}), 400

    db = D['get_db']()
    qmarks = ','.join('?' * len(ids))
    users = db.execute(
        "SELECT id, username, is_admin, is_super_admin FROM users WHERE id IN (%s)" % qmarks,
        ids).fetchall()

    skipped = []
    targets = []
    for u in users:
        if u['id'] == me['id']:
            skipped.append('%s（你自己）' % u['username'])
            continue
        if u['is_super_admin'] and not me['is_super_admin']:
            skipped.append('%s（超管）' % u['username'])
            continue
        if action == 'deactivate' and u['is_super_admin']:
            skipped.append('%s（超管不可注销）' % u['username'])
            continue
        targets.append(u)

    if not targets:
        return jsonify({"ok": False, "msg": "没有可操作的用户：%s" % '、'.join(skipped[:3])}), 400

    tids = [u['id'] for u in targets]
    tq = ','.join('?' * len(tids))
    now = time.time()
    detail = ''
    extra = {}

    if action == 'ai_quota':
        try:
            quota = int(data.get('ai_quota'))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "配额数值非法"}), 400
        if quota < -1 or quota > 9999:
            return jsonify({"ok": False, "msg": "配额需在 -1 ~ 9999 之间"}), 400
        db.execute("UPDATE users SET ai_quota=? WHERE id IN (%s)" % tq, [quota] + tids)
        detail = {(-1): '不限', 0: '禁止'}.get(quota, '每日 %d 轮' % quota)

    elif action in ('ban', 'mute'):
        try:
            _d = float(data.get('days') or 0)
            _h = float(data.get('hours') or 0)
            _m = float(data.get('minutes') or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "时长格式非法"}), 400
        if _d <= 0 and _h <= 0 and _m <= 0:
            return jsonify({"ok": False, "msg": "请填写封禁/禁言时长（不能全为 0）"}), 400
        until = _calc_until(_FakeForm(data))
        if until is None:
            return jsonify({"ok": False, "msg": "时长非法"}), 400
        col = 'banned_until' if action == 'ban' else 'muted_until'
        reason = (data.get('reason') or '').strip()[:120]
        db.execute("UPDATE users SET %s=? WHERE id IN (%s)" % (col, tq), [until] + tids)
        for u in targets:
            db.execute(
                "INSERT INTO punishment_records (user_id, username, admin_id, admin_name, "
                "ptype, until, duration_text, reason, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (u['id'], u['username'], me['id'], me['username'], action, until,
                 _duration_text_from_until(until), reason or '（未填写）', 'effective', now))
        detail = '%s%s' % (_duration_text_from_until(until), ('：' + reason) if reason else '')

    elif action in ('lift_ban', 'lift_mute'):
        col = 'banned_until' if action == 'lift_ban' else 'muted_until'
        ptype = 'ban' if action == 'lift_ban' else 'mute'
        db.execute("UPDATE users SET %s=0 WHERE id IN (%s)" % (col, tq), tids)
        db.execute("UPDATE punishment_records SET status='lifted', lifted_at=? "
                   "WHERE ptype=? AND status='effective' AND user_id IN (%s)" % tq,
                   [now, ptype] + tids)
        detail = '已解除'

    elif action in ('effect_give', 'effect_remove'):
        eff = (data.get('effect_type') or '').strip()
        allowed = {'rainbow', 'fire', 'ocean', 'forest', 'purple', 'sunset',
                   'neon', 'galaxy', 'popular'}
        if eff not in allowed:
            return jsonify({"ok": False, "msg": "特效类型非法"}), 400
        if action == 'effect_give':
            db.execute("UPDATE users SET effect_type=?, has_effect=1 WHERE id IN (%s)" % tq,
                       [eff] + tids)
            detail = '授予 %s' % eff
        else:
            db.execute("UPDATE users SET effect_type='', has_effect=0 WHERE id IN (%s)" % tq, tids)
            detail = '移除特效'

    elif action in ('badge_give', 'badge_remove'):
        if action == 'badge_give':
            text = (data.get('badge_text') or '').strip()[:10]
            eff = (data.get('badge_effect') or 'gold').strip()
            allowed = {'gold', 'silver', 'purple', 'fire', 'ocean', 'forest', 'dark', 'neon'}
            if not text:
                return jsonify({"ok": False, "msg": "请填写牌子文字"}), 400
            if eff not in allowed:
                return jsonify({"ok": False, "msg": "牌子样式非法"}), 400
            db.execute("UPDATE users SET badge_text=?, badge_effect=? WHERE id IN (%s)" % tq,
                       [text, eff] + tids)
            detail = '%s（%s）' % (text, eff)
        else:
            db.execute("UPDATE users SET badge_text='', badge_effect='' WHERE id IN (%s)" % tq, tids)
            detail = '移除荣誉牌子'

    elif action == 'reset_game':
        db.execute("UPDATE users SET %s WHERE id IN (%s)" % (_game_reset_sql(), tq), tids)
        detail = '全部成绩清零'

    elif action == 'reset_password':
        creds = []
        for u in targets:
            pw = _rand_password()
            h, salt = D['hash_password'](pw)
            db.execute("UPDATE users SET password_hash=?, salt=?, password_ext=NULL WHERE id=?",
                       (h, salt, u['id']))
            creds.append({'username': u['username'], 'password': pw})
        extra['credentials'] = creds
        detail = '重置了 %d 个账号的密码' % len(creds)

    elif action == 'deactivate':
        try:
            _bakdir = os.path.join(D['BASE_DIR'], "backups")
            os.makedirs(_bakdir, exist_ok=True)
            _bak = os.path.join(_bakdir, "data.db.bak-before-deactivate-%s" % time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(D['DB_PATH'], _bak)
            D['add_admin_log'](me, "注销前备份", os.path.basename(_bak), "自动备份，便于误注销后恢复")
        except Exception:
            D['log'].exception("注销前备份失败（继续执行注销）")
        done = []
        for u in targets:
            new_name = _gen_deactivated_name(db)
            pw = _rand_password(16)
            h, salt = D['hash_password'](pw)
            sets = ["username=?", "password_hash=?", "salt=?", "password_ext=NULL",
                    "banned_until=?", "muted_until=0", "avatar=?", "deactivated=1"]
            args = [new_name, h, salt, D['PERMANENT'], 'default_avatar.png']
            for col in _DEACTIVATE_CLEAR:
                if col == 'avatar':
                    continue
                try:
                    db.execute("SELECT %s FROM users LIMIT 0" % col)
                    sets.append("%s=?" % col)
                    args.append(0 if col == 'has_effect' else '')
                except Exception:
                    pass
            db.execute("UPDATE users SET %s WHERE id=?" % ','.join(sets), args + [u['id']])
            done.append('%s→%s' % (u['username'], new_name))
        detail = '注销 %d 个账号：%s' % (len(done), '、'.join(done[:5]))

    db.commit()
    D['add_admin_log'](me, _BATCH_ACTION_LABEL[action],
                  '%d 个用户' % len(targets), detail)
    msg = '已对 %d 个用户执行「%s」' % (len(targets), _BATCH_ACTION_LABEL[action])
    if skipped:
        msg += '；跳过 %d 个（%s）' % (len(skipped), '、'.join(skipped[:2]))
    return jsonify({"ok": True, "msg": msg, "count": len(targets),
                    "skipped": skipped, **extra})






def api_admin_set_ai_quota():
    """API 版设置 AI 配额（供 AJAX 调用）。"""
    me = D['current_user']()
    data = request.get_json(silent=True) or {}
    uid = data.get('user_id')
    quota = data.get('ai_quota')
    try:
        uid = int(uid); quota = int(quota)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数非法"}), 400
    target = D['get_user_by_id'](uid) if uid else None
    if not target:
        return jsonify({"ok": False, "msg": "用户不存在"}), 404
    if quota < -1 or quota > 9999:
        return jsonify({"ok": False, "msg": "配额数值非法（-1 ~ 9999）"}), 400
    db = D['get_db']()
    db.execute("UPDATE users SET ai_quota=? WHERE id=?", (quota, target['id']))
    db.commit()
    quota_txt = { -1: '不限', 0: '禁止使用' }.get(quota, '每日 %d 轮' % quota)
    D['add_admin_log'](me, "AI配额", target['username'], quota_txt)
    return jsonify({"ok": True, "msg": "已将 %s 的 AI 配额设为：%s" % (target['username'], quota_txt)})






def admin_ai_codes():
    """生成 / 查看 AI 广场兑换码（充值余额）。

    POST 参数：
        count     一次生成几个
        max_uses  每个码最多几人兑换
        amount    每个码充值多少元
        note      备注（比如「国庆活动」）
    """
    db = D['get_db']()
    _ai_code_tables(db)

    if request.method == "POST":
        try:
            count = max(1, min(50, int(request.form.get("count") or 1)))
            max_uses = max(1, min(9999, int(request.form.get("max_uses") or 1)))
            amount = round(max(0.01, min(10000.0, float(request.form.get("amount") or 0))), 2)
        except (TypeError, ValueError):
            flash("参数不合法", "error")
            return redirect(url_for("admin_ai_codes"))
        note = (request.form.get("note") or "").strip()[:120]
        vf = (request.form.get("valid_from") or "").strip()[:10]
        vu = (request.form.get("valid_until") or "").strip()[:10]
        import re as _re
        for v in (vf, vu):
            if v and not _re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                flash("有效期格式应为 2026-01-01，或留空不限", "error")
                return redirect(url_for("admin_ai_codes"))
        made = []
        for _ in range(count):
            c = _gen_ai_code(db)
            if not c:
                break
            db.execute(
                "INSERT INTO ai_redeem_codes (code, note, max_uses, amount, created_by, created_at, valid_from, valid_until) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (c, note, max_uses, amount, D['current_user']()["id"], time.time(), vf, vu))
            made.append(c)
        db.commit()
        D['_log_mod'].event("ai_code_make", user=D['current_user']()["username"],
                       n=len(made), amount=amount, maxu=max_uses)
        if made:
            flash("已生成 %d 个兑换码（每个 ¥%.2f）：%s" % (len(made), amount, "、".join(made)), "ok")
        else:
            flash("生成失败，请重试", "error")
        return redirect(url_for("admin_ai_codes"))

    rows = db.execute(
        "SELECT c.*, u.username AS byname FROM ai_redeem_codes c "
        "LEFT JOIN users u ON c.created_by=u.id "
        "ORDER BY c.id DESC LIMIT 200").fetchall()
    import datetime as _dt
    return render_template("admin_ai_codes.html", rows=rows,
                           now_d=_dt.date.today().isoformat())






def admin_ai_code_del(cid):
    db = D['get_db']()
    _ai_code_tables(db)
    db.execute("DELETE FROM ai_redeem_codes WHERE id=?", (cid,))
    db.execute("DELETE FROM ai_redeem_used WHERE code_id=?", (cid,))
    db.commit()
    flash("已删除", "ok")
    return redirect(url_for("admin_ai_codes"))


# ── 前台：兑换 ──────────────────────────────────────────────────






def admin_server_status():
    """本机（香港主站）+ 洛杉矶备用机的状态。只有超管能看。

    LA 用 TCP 探测 22 端口测可达性和延迟，不跨机 SSH，不在页面上暴露敏感信息。
    """
    import shutil as _shutil

    # ── 本机 ──
    def _read_uptime():
        try:
            with open("/proc/uptime") as f:
                return float(f.read().split()[0])
        except Exception:
            return 0

    def _mem():
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for ln in f:
                    k, _, v = ln.partition(":")
                    info[k.strip()] = int(v.strip().split()[0])  # kB
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            return total, avail
        except Exception:
            return 0, 0

    def _disk(path="/"):
        try:
            u = _shutil.disk_usage(path)
            return u.total, u.free
        except Exception:
            return 0, 0

    try:
        with open("/proc/loadavg") as f:
            la = f.read().split()[:3]
    except Exception:
        la = ["0", "0", "0"]

    up = _read_uptime()
    mem_total, mem_avail = _mem()
    d_total, d_free = _disk("/")

    def _fmt_gb(b):
        return "%.1f GB" % (b / 1073741824.0)

    def _fmt_up(sec):
        d = int(sec // 86400)
        h = int(sec % 86400 // 3600)
        m = int(sec % 3600 // 60)
        return "%d 天 %d 小时 %d 分" % (d, h, m) if d else "%d 小时 %d 分" % (h, m)

    local = {
        "uptime": _fmt_up(up),
        "load": " ".join(la),
        "mem_total": _fmt_gb(mem_total * 1024),
        "mem_used_p": round((mem_total - mem_avail) * 100.0 / mem_total, 1) if mem_total else 0,
        "disk_total": _fmt_gb(d_total),
        "disk_used_p": round((d_total - d_free) * 100.0 / d_total, 1) if d_total else 0,
    }

    # ── 洛杉矶备用机：TCP 探测 ──
    import socket as _socket
    la_info = {"host": "64.83.18.51", "ok": False, "ms": None, "err": ""}
    t0 = time.time()
    try:
        sk = _socket.create_connection((la_info["host"], 22), timeout=4)
        sk.close()
        la_info["ok"] = True
        la_info["ms"] = round((time.time() - t0) * 1000)
    except Exception as e:
        la_info["err"] = str(e)[:80]

    return render_template("admin_server.html", local=local, la=la_info)






def admin_api_aisq_users():
    """AI 广场用户用量列表（搜索 + 分页，供后台看板下拉加载）。"""
    db = D['get_db']()
    q = (request.args.get('q') or '').strip()
    try:
        offset = max(0, int(request.args.get('offset') or 0))
        limit = min(50, max(1, int(request.args.get('limit') or 20)))
    except (TypeError, ValueError):
        offset, limit = 0, 20

    where, args = "", []
    if q:
        where = " AND (u.username LIKE ? OR CAST(a.user_id AS TEXT) = ?)"
        args = ["%" + q + "%", q]

    try:
        rows = db.execute(
            "SELECT a.user_id uid, u.username, COUNT(*) n, IFNULL(SUM(a.cost),0) c, "
            "MAX(a.created_at) last_at "
            "FROM ai_square_usage a LEFT JOIN users u ON u.id = a.user_id "
            "WHERE 1=1" + where +
            " GROUP BY a.user_id ORDER BY c DESC LIMIT ? OFFSET ?",
            args + [limit, offset]).fetchall()
        total = db.execute(
            "SELECT COUNT(DISTINCT a.user_id) c FROM ai_square_usage a "
            "LEFT JOIN users u ON u.id = a.user_id WHERE 1=1" + where, args).fetchone()['c']
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

    items = [{"uid": r["uid"], "name": r["username"] or ("uid " + str(r["uid"])),
              "n": r["n"], "cost": round(float(r["c"] or 0), 6),
              "last_at": r["last_at"] or 0} for r in rows]
    return jsonify({"ok": True, "items": items, "total": total,
                    "has_more": offset + len(items) < total})


def admin_aisq_user(uid):
    """某个用户在 AI 广场的全部对话。"""
    db = D['get_db']()
    u = db.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return "用户不存在", 404
    convs = db.execute(
        "SELECT c.id, c.title, c.model, c.created_at, c.updated_at, "
        "  (SELECT COUNT(*) FROM ai_square_msgs m WHERE m.conv_id=c.id) msgs, "
        "  (SELECT IFNULL(SUM(m.cost),0) FROM ai_square_msgs m WHERE m.conv_id=c.id) cost "
        "FROM ai_square_convs c WHERE c.user_id=? ORDER BY c.updated_at DESC LIMIT 300",
        (uid,)).fetchall()
    usage = db.execute(
        "SELECT COUNT(*) n, IFNULL(SUM(cost),0) c FROM ai_square_usage WHERE user_id=?",
        (uid,)).fetchone()
    import time as _t
    convs = [dict(c) for c in convs]
    for c in convs:
        ts = c.get("updated_at") or c.get("created_at") or 0
        c["time_str"] = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(ts)) if ts else "—"
    return render_template("admin_aisq_user.html", target=u, convs=convs, usage=usage)


def admin_aisq_conv(cid):
    """单个对话的消息明细。"""
    db = D['get_db']()
    c = db.execute(
        "SELECT c.*, u.username FROM ai_square_convs c LEFT JOIN users u ON u.id=c.user_id "
        "WHERE c.id=?", (cid,)).fetchone()
    if not c:
        return "对话不存在", 404
    msgs = db.execute(
        "SELECT id, role, content, cost, model, images, created_at "
        "FROM ai_square_msgs WHERE conv_id=? ORDER BY id", (cid,)).fetchall()
    import time as _t
    msgs = [dict(x) for x in msgs]
    for x in msgs:
        ts = x.get("created_at") or 0
        x["time_str"] = _t.strftime("%m-%d %H:%M", _t.localtime(ts)) if ts else "—"
    conv = dict(c)
    _ts = conv.get("updated_at") or 0
    conv["time_str"] = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(_ts)) if _ts else "—"
    return render_template("admin_aisq_conv.html", conv=conv, msgs=msgs)


def admin_survey():
    """调查问卷管理（仅超管）。GET 看列表，POST 保存。"""
    import json as _json
    import time as _t

    db = D['get_db']()
    D['_survey_ensure'](db) if '_survey_ensure' in D else None
    # 直接用 app 的建表函数（通过模块引用）
    try:
        from app import _survey_ensure as _se
        _se(db)
    except Exception:
        pass

    if request.method == "POST":
        sid = int(request.form.get("sid") or 0)
        title = (request.form.get("title") or "").strip()[:120]
        intro = (request.form.get("intro") or "").strip()[:2000]
        once_only = 1 if request.form.get("once_only") else 0
        enabled = 1 if request.form.get("enabled") else 0
        raw_qs = request.form.get("questions") or "[]"
        try:
            qs = _json.loads(raw_qs)
            if not isinstance(qs, list):
                qs = []
        except Exception:
            qs = []

        if not title:
            flash("标题不能为空", "error")
            return redirect(url_for("admin_survey"))

        now = _t.time()
        if sid:
            db.execute("UPDATE surveys SET title=?, intro=?, once_only=?, enabled=? WHERE id=?",
                       (title, intro, once_only, enabled, sid))
        else:
            cur = db.execute(
                "INSERT INTO surveys (title, intro, enabled, once_only, created_by, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (title, intro, enabled, once_only, D['current_user']()['id'], now))
            sid = cur.lastrowid
        db.execute("DELETE FROM survey_questions WHERE survey_id=?", (sid,))
        for i, q in enumerate(qs):
            db.execute(
                "INSERT INTO survey_questions (survey_id, sort, qtype, title, options, required) "
                "VALUES (?,?,?,?,?,?)",
                (sid, i, q.get("qtype", "single"), (q.get("title") or "")[:300],
                 _json.dumps(q.get("options") or [], ensure_ascii=False),
                 1 if q.get("required") else 0))
        # 同时只允许一份启用（简单起见）
        if enabled:
            db.execute("UPDATE surveys SET enabled=0 WHERE id<>?", (sid,))
        db.commit()
        flash("问卷已保存", "ok")
        return redirect(url_for("admin_survey"))

    rows = db.execute("SELECT * FROM surveys ORDER BY id DESC").fetchall()
    surveys = []
    for r in rows:
        d = dict(r)
        try:
            d["qcount"] = db.execute("SELECT COUNT(*) c FROM survey_questions WHERE survey_id=?",
                                     (r["id"],)).fetchone()["c"]
            d["rcount"] = db.execute("SELECT COUNT(*) c FROM survey_responses WHERE survey_id=?",
                                     (r["id"],)).fetchone()["c"]
        except Exception:
            d["qcount"] = d["rcount"] = 0
        d["questions"] = [dict(q) for q in db.execute(
            "SELECT * FROM survey_questions WHERE survey_id=? ORDER BY sort, id", (r["id"],))]
        for q in d["questions"]:
            try:
                q["options"] = _json.loads(q["options"] or "[]")
            except Exception:
                q["options"] = []
        surveys.append(d)

    return render_template("admin_survey.html", surveys=surveys)


def admin_survey_toggle(sid=0):
    import time as _t
    sid = int(sid or 0)
    db = D['get_db']()
    row = db.execute("SELECT enabled FROM surveys WHERE id=?", (sid,)).fetchone()
    if row:
        newv = 0 if row["enabled"] else 1
        db.execute("UPDATE surveys SET enabled=? WHERE id=?", (newv, sid))
        if newv:
            db.execute("UPDATE surveys SET enabled=0 WHERE id<>?", (sid,))
        db.commit()
    return redirect(url_for("admin_survey"))


def admin_survey_delete(sid=0):
    sid = int(sid or 0)
    db = D['get_db']()
    db.execute("DELETE FROM survey_questions WHERE survey_id=?", (sid,))
    db.execute("DELETE FROM survey_responses WHERE survey_id=?", (sid,))
    db.execute("DELETE FROM surveys WHERE id=?", (sid,))
    db.commit()
    return redirect(url_for("admin_survey"))


def admin_survey_results(sid=0):
    """问卷结果统计（每题选项计数 + 文本答案列表）。"""
    import json as _json
    sid = int(sid or 0)
    db = D['get_db']()
    s = db.execute("SELECT * FROM surveys WHERE id=?", (sid,)).fetchone()
    if not s:
        return "问卷不存在", 404
    qs = [dict(q) for q in db.execute(
        "SELECT * FROM survey_questions WHERE survey_id=? ORDER BY sort, id", (sid,))]
    for q in qs:
        try:
            q["options"] = _json.loads(q["options"] or "[]")
        except Exception:
            q["options"] = []

    resps = db.execute(
        "SELECT r.*, u.username FROM survey_responses r LEFT JOIN users u ON u.id=r.user_id "
        "WHERE r.survey_id=? ORDER BY r.id DESC", (sid,)).fetchall()

    total = len(resps)
    stats = []
    for q in qs:
        key = str(q["id"])
        counts = {}
        texts = []
        for r in resps:
            try:
                ans = _json.loads(r["answers"] or "{}")
            except Exception:
                ans = {}
            v = ans.get(key)
            if q["qtype"] == "multi":
                for x in (v or []):
                    counts[x] = counts.get(x, 0) + 1
            elif q["qtype"] == "text":
                if v:
                    texts.append({"user": r["username"] or ("uid %d" % r["user_id"]), "text": v})
            else:
                if v:
                    counts[v] = counts.get(v, 0) + 1
        stats.append({"q": q, "counts": counts, "texts": texts[:200]})

    return render_template("admin_survey_results.html", s=dict(s), stats=stats,
                           total=total, resps=[dict(r) for r in resps[:100]])


def admin_update_notice():
    """更新公告（进入网站弹窗，每人每天至少显示一次）。"""
    import json as _json
    import time as _time

    if request.method == "POST":
        enabled = bool(request.form.get("enabled"))
        title = (request.form.get("title") or "").strip()[:80]
        content = (request.form.get("content") or "").strip()[:8000]
        try:
            old = _json.loads(D['get_config']("update_notice", "") or "{}")
        except Exception:
            old = {}
        ver = int(old.get("ver") or 0)
        # 内容或标题变了才递增版本（游客的 localStorage 靠版本号区分）
        if content != (old.get("content") or "") or title != (old.get("title") or ""):
            ver += 1
        if ver < 1:
            ver = 1
        data = {"enabled": enabled, "title": title, "content": content,
                "ver": ver, "at": _time.time()}
        D['set_config']("update_notice", _json.dumps(data, ensure_ascii=False))
        return redirect(url_for("admin_update_notice"))

    try:
        cur = _json.loads(D['get_config']("update_notice", "") or "{}")
    except Exception:
        cur = {}
    return render_template("admin_update_notice.html", cur=cur)


def admin_aisq_usage():
    """AI 广场用量统计（按模型 / 按天 / 按用户）。

    数据源是 ai_square_usage 流水表（删会话不影响）。
    """
    db = D['get_db']()
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS ai_square_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, conv_id INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
            cost REAL NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0)""")
        db.commit()
    except Exception:
        pass

    import datetime as _dt
    now = time.time()
    today0 = now - (now % 86400) - 8 * 3600   # 北京时间当天 0 点（UTC 时间戳）

    def one(sql, args=()):
        try:
            return db.execute(sql, args).fetchone()
        except Exception:
            return None

    total = one("SELECT COUNT(*) n, IFNULL(SUM(cost),0) c, "
                "IFNULL(SUM(prompt_tokens),0) pt, IFNULL(SUM(completion_tokens),0) ct "
                "FROM ai_square_usage")
    today = one("SELECT COUNT(*) n, IFNULL(SUM(cost),0) c FROM ai_square_usage "
                "WHERE created_at >= ?", (today0,))
    by_model = db.execute(
        "SELECT model, provider, COUNT(*) n, IFNULL(SUM(cost),0) c, "
        "IFNULL(SUM(prompt_tokens),0) pt, IFNULL(SUM(completion_tokens),0) ct "
        "FROM ai_square_usage GROUP BY model ORDER BY c DESC").fetchall()
    by_day = db.execute(
        "SELECT date(created_at, 'unixepoch', '+8 hours') d, COUNT(*) n, "
        "IFNULL(SUM(cost),0) c FROM ai_square_usage "
        "GROUP BY d ORDER BY d DESC LIMIT 14").fetchall()
    by_user = db.execute(
        "SELECT u.id, u.username, COUNT(*) n, IFNULL(SUM(a.cost),0) c "
        "FROM ai_square_usage a LEFT JOIN users u ON u.id = a.user_id "
        "GROUP BY a.user_id ORDER BY c DESC LIMIT 20").fetchall()
    return render_template("admin_aisq_usage.html", total=total, today=today,
                           by_model=by_model, by_day=by_day, by_user=by_user)


def admin_ai_logs():
    """AI 助手用量明细（对话助手 + 代码调试）。"""
    db = D['get_db']()
    f_user = (request.args.get('u') or '').strip()
    f_module = (request.args.get('m') or '').strip()
    f_ok = (request.args.get('ok') or '').strip()
    try:
        page = max(1, int(request.args.get('p') or 1))
    except (TypeError, ValueError):
        page = 1

    where, args = [], []
    if f_module in ('chat', 'debug'):
        where.append("l.module=?")
        args.append(f_module)
    if f_ok in ('1', '0'):
        where.append("l.success=?")
        args.append(int(f_ok))
    if f_user:
        # 支持按 uid 或用户名搜
        if f_user.isdigit():
            where.append("(l.user_id=? OR u.username LIKE ?)")
            args += [int(f_user), '%' + f_user + '%']
        else:
            where.append("u.username LIKE ?")
            args.append('%' + f_user + '%')
    wsql = ("WHERE " + " AND ".join(where)) if where else ""

    total = db.execute(
        "SELECT COUNT(*) c FROM ai_logs l LEFT JOIN users u ON l.user_id=u.id " + wsql,
        args).fetchone()['c']
    pages = max(1, (total + D['AI_LOG_PAGE_SIZE'] - 1) // D['AI_LOG_PAGE_SIZE'])
    page = min(page, pages)

    rows = db.execute(
        "SELECT l.*, u.username FROM ai_logs l LEFT JOIN users u ON l.user_id=u.id "
        + wsql + " ORDER BY l.id DESC LIMIT ? OFFSET ?",
        args + [D['AI_LOG_PAGE_SIZE'], (page - 1) * D['AI_LOG_PAGE_SIZE']]).fetchall()

    # 汇总（跟着筛选条件走，不是全站）
    agg = db.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(l.success),0) ok_n, "
        "COALESCE(SUM(l.prompt_tokens+l.completion_tokens),0) tok, "
        "COALESCE(AVG(NULLIF(l.latency_ms,0)),0) avg_ms "
        "FROM ai_logs l LEFT JOIN users u ON l.user_id=u.id " + wsql, args).fetchone()

    by_module = db.execute(
        "SELECT l.module, COUNT(*) n, COALESCE(SUM(l.prompt_tokens+l.completion_tokens),0) tok "
        "FROM ai_logs l LEFT JOIN users u ON l.user_id=u.id " + wsql +
        " GROUP BY l.module ORDER BY n DESC", args).fetchall()

    top_users = db.execute(
        "SELECT l.user_id, u.username, COUNT(*) n, "
        "COALESCE(SUM(l.prompt_tokens+l.completion_tokens),0) tok, "
        "COALESCE(SUM(CASE WHEN l.success=0 THEN 1 ELSE 0 END),0) fail_n "
        "FROM ai_logs l LEFT JOIN users u ON l.user_id=u.id " + wsql +
        " GROUP BY l.user_id ORDER BY n DESC LIMIT 12", args).fetchall()

    return render_template("admin_ai_logs.html", rows=rows, total=total, page=page,
                           pages=pages, agg=agg, by_module=by_module,
                           top_users=top_users, f_user=f_user, f_module=f_module,
                           f_ok=f_ok)



def _today_str():
    return time.strftime("%Y-%m-%d")


def _home_banners():
    """读首页轮播图配置，返回 [{image, title, link}, ...]。

    每行格式：图片路径|标题|跳转链接 —— 标题和链接都可以省略。
    """
    raw = (D['get_config']("home_banners", "") or "").strip()
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        img = parts[0] if parts else ""
        title = parts[1] if len(parts) > 1 else ""
        link = parts[2] if len(parts) > 2 else ""
        if img or title:
            out.append({"image": img, "title": title, "link": link})
    return out


def _perf_parse_log(tail_lines=6000):
    """解析 mono.log 尾部 REQ 行 + 异常块 → (状态码分布, 慢请求Top, 5xx明细, traceback块)。

    面板数据源就是应用日志本身（_log_after 每请求一行），不额外建表。
    """
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "mono.log")
    codes = {}
    slow = []
    errs = []
    tbs = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-tail_lines:]
    except Exception:
        return codes, slow, errs, tbs
    in_tb = False
    tb_buf = []
    for ln in lines:
        m = _REQ_LINE_RE.search(ln)
        if m:
            in_tb = False
            method, path, code, ms, ip, uid = m.groups()
            codes[code] = codes.get(code, 0) + 1
            ms_i = int(ms)
            if ms_i >= D['_SLOW_MS']:
                slow.append({"method": method, "path": path, "code": code,
                             "ms": ms_i, "ip": ip, "uid": uid, "raw": ln.strip()[:160]})
            if code.startswith("5"):
                errs.append({"path": path, "code": code, "ms": ms_i,
                             "ip": ip, "raw": ln.strip()[:160]})
            continue
        # traceback 块收集（errorhandler 写的多行异常）
        if "Traceback (most recent call last)" in ln:
            in_tb = True
            tb_buf = [ln.rstrip()]
            continue
        if in_tb:
            tb_buf.append(ln.rstrip())
            if len(tb_buf) > 40:
                in_tb = False
            if ln.strip() and not ln.startswith(" ") and "Traceback" not in ln and len(tb_buf) > 3:
                tbs.append("\n".join(tb_buf[-25:]))
                in_tb = False
                tb_buf = []
    if tb_buf and len(tb_buf) > 3:
        tbs.append("\n".join(tb_buf[-25:]))
    slow.sort(key=lambda x: -x["ms"])
    return codes, slow[:15], errs[-30:], tbs[-3:]


def _restore_posts(db, ids):
    real = _recycle_pick(db, ids)
    if real:
        q = ",".join("?" * len(real))
        db.execute("UPDATE posts SET recycle = 0 WHERE id IN (%s)" % q, real)
        db.commit()
    return real


def _purge_posts(db, ids):
    """彻底删除帖子及其全部关联数据。"""
    real = _recycle_pick(db, ids)
    if real:
        q = ",".join("?" * len(real))
        for t in ("post_likes", "post_comments", "post_tags", "post_views"):
            db.execute("DELETE FROM %s WHERE post_id IN (%s)" % (t, q), real)
        db.execute("DELETE FROM posts WHERE id IN (%s)" % q, real)
        db.commit()
    return real


def _calc_until(form):
    mode = form.get("mode")
    if mode == "permanent":
        return D['PERMANENT']
    if mode == "lift":
        return 0
    try:
        days = float(form.get("days") or 0)
    except ValueError:
        days = 0
    try:
        hours = float(form.get("hours") or 0)
    except ValueError:
        hours = 0
    try:
        minutes = float(form.get("minutes") or 0)
    except ValueError:
        minutes = 0
    if abs(days - 99) < 1e-9 and hours == 0 and minutes == 0:
        return D['PERMANENT']
    total_seconds = (days * 86400) + (hours * 3600) + (minutes * 60)
    if total_seconds <= 0:
        total_seconds = 600
    return time.time() + total_seconds


def _duration_text(form):
    mode = form.get("mode")
    if mode == "permanent":
        return "永久"
    if mode == "lift":
        return "解除"
    try:
        days = int(float(form.get("days") or 0))
    except ValueError:
        days = 0
    try:
        hours = int(float(form.get("hours") or 0))
    except ValueError:
        hours = 0
    try:
        minutes = int(float(form.get("minutes") or 0))
    except ValueError:
        minutes = 0
    return "%d天%d时%d分" % (days, hours, minutes)


def _record_punishment(db, me, target, ptype, until, reason, duration_text):
    db.execute(
        "INSERT INTO punishment_records (user_id, username, admin_id, admin_name, ptype, until, duration_text, reason, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,'effective',?)",
        (target['id'], target['username'], me['id'], me['username'], ptype, until, duration_text, reason, time.time())
    )
    db.commit()


def _lift_punishment(db, me, target, ptype, reason):
    col = "banned_until" if ptype == "ban" else "muted_until"
    db.execute("UPDATE users SET %s = 0 WHERE id = ?" % col, (target['id'],))
    db.execute(
        "UPDATE punishment_records SET status='lifted', lifted_at=?, lift_reason=? "
        "WHERE user_id=? AND ptype=? AND status='effective'",
        (time.time(), reason, target['id'], ptype)
    )
    db.commit()


def _check_punish_permission(me, target):
    """返回 (ok, error_msg)。普通管理员权限封锁。"""
    if target["id"] == me["id"]:
        return (False, "不能对自己操作")
    if target["is_super_admin"]:
        return (False, "不能处罚超级管理员")
    if target["is_admin"] and not me["is_super_admin"]:
        return (False, "普通管理员不能处罚其他管理员")
    return (True, "")


def _mon_range():
    """从查询串解析时间范围（分钟）。"""
    try:
        m = int(request.args.get("minutes", "180"))
    except (ValueError, TypeError):
        m = 180
    return max(5, min(m, 60 * 24 * 90))   # 2026-11-01：上限从 30 天放到 90 天


def _gen_insights(db, days, labels, dau, acts, games, mini, music):
    """从上面那些原始数字里读出结论 —— 管理员不想自己看图找规律。"""
    out = []
    n = len(dau)
    if not n:
        return out

    # 1) 周六效应：你说过社团课只在周六
    dow_stat = [0] * 7
    for i, lb in enumerate(labels):
        pass
    try:
        rr = db.execute(
            "SELECT CAST(strftime('%w', created_at,'unixepoch','+8 hours') AS INTEGER) w, "
            "COUNT(DISTINCT user_id) c FROM activity_log WHERE created_at>? GROUP BY w",
            (time.time() - days * 86400,)).fetchall()
        for r in rr:
            dow_stat[r['w']] = r['c']
    except Exception:
        pass
    names = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    best = max(range(7), key=lambda i: dow_stat[i])
    worst = min(range(7), key=lambda i: dow_stat[i])
    if dow_stat[best] > 0 and dow_stat[best] >= max(1, dow_stat[worst]) * 3:
        out.append({
            "level": "warn", "icon": "📅",
            "title": "%s 是绝对高峰（%d 人），%s 只有 %d 人" % (names[best], dow_stat[best], names[worst], dow_stat[worst]),
            "text": "活跃度高度集中在单日，其他时间几乎没人。注意留存 —— 高峰日来的人，"
                    "如果一周后不再出现，说明他们只是「路过」。",
        })

    # 2) 最近 7 天 vs 前 7 天
    if n >= 14:
        a = sum(dau[-7:]) / 7.0
        b = sum(dau[-14:-7]) / 7.0
        if b > 0:
            pct = (a - b) * 100.0 / b
            if pct >= 20:
                out.append({"level": "good", "icon": "📈", "title": "近 7 天日均 %.1f，比上周涨了 %.0f%%" % (a, pct),
                            "text": "上升期。可以趁这个势头在群里推一把活动，把势头接住。"})
            elif pct <= -20:
                out.append({"level": "warn", "icon": "📉", "title": "近 7 天日均 %.1f，比上周跌了 %.0f%%" % (a, -pct),
                            "text": "在走下坡。先别加功能，去群里问一句「最近怎么没人玩了」，比猜要准。"})
            else:
                out.append({"level": "flat", "icon": "➡️", "title": "近 7 天日均 %.1f，与上周基本持平" % a,
                            "text": "横盘。通常是「没有新的理由回来」—— 需要一个每周固定的由头。"})

    # 3) 游戏热度
    if games:
        top = games[0]
        total_players = sum(g['players'] for g in games)
        share = top['players'] * 100.0 / max(1, total_players)
        out.append({"level": "good", "icon": "🎮",
                    "title": "%s 最热：%d 人玩过" % (_MM_LABEL.get(top['game'], top['game']), top['players']),
                    "text": "占所有棋类玩家的 %.0f%%。想推活动就推这款 —— 已有 %d 人在玩，不用重新教育。"
                            % (share, top['players'])})
        # 冷门游戏
        cold = [g for g in games if g['players'] <= 2]
        if len(cold) >= 3:
            out.append({"level": "warn", "icon": "🧊",
                        "title": "%d 款棋类几乎没人玩（≤2 人）" % len(cold),
                        "text": "分别是 " + "、".join(_MM_LABEL.get(g['game'], g['game']) for g in cold[:6])
                                + "。可以考虑合并或下架，把入口留给有人玩的。"})

    # 4) 单机游戏参与度
    if mini:
        zero = [m for m in mini if m['players'] == 0]
        if zero:
            out.append({"level": "warn", "icon": "🎯",
                        "title": "%d 款单机游戏 0 人玩过" % len(zero),
                        "text": "包括 " + "、".join(m['name'] for m in zero[:5])
                                + "。它们在游戏中心占着位置，但零流量 —— 要么推广，要么下架。"})
        if mini and mini[0]['players'] > 0:
            out.append({"level": "flat", "icon": "⭐",
                        "title": "单机类里 %s 参与最多（%d 人）" % (mini[0]['name'], mini[0]['players']),
                        "text": "最高分 %d。其余单机游戏的参与度可以参照这个思路优化引导。"
                                % mini[0]['best']})

    # 5) 音乐
    if music["plays"] > 0:
        avg = music["total_ms"] / float(music["plays"]) / 1000.0
        out.append({"level": "good", "icon": "🎧",
                    "title": "音乐被听了 %d 次，%d 个人听过" % (music["plays"], music["listeners"]),
                    "text": "人均单次 %.0f 秒，累计 %s。这是站内最私人的行为 —— 愿意听歌的人黏性通常更高。"
                            % (avg, _fmt_dur(music["total_ms"]))})
    elif music["listeners"] == 0:
        out.append({"level": "flat", "icon": "🎧",
                    "title": "音乐功能还没有播放数据",
                    "text": "从今天起开始记录（每次听超过 3 秒才算）。一周后回来看这个数，"
                            "就能判断音乐到底有没有人用。"})

    # 6) 内容产出
    try:
        pw = db.execute("SELECT COUNT(*) c FROM posts WHERE created_at>?",
                        (time.time() - 7 * 86400,)).fetchone()['c']
        pw2 = db.execute("SELECT COUNT(*) c FROM posts WHERE created_at>?",
                         (time.time() - 14 * 86400,)).fetchone()['c'] - pw
        if pw <= 2:
            out.append({"level": "warn", "icon": "📝",
                        "title": "近 7 天只发了 %d 篇帖子" % pw,
                        "text": "论坛基本停摆。发帖是需要动脑的成本，初中生不会为社区自发写作 —— "
                                "内容得靠运营（活动、话题、老师带头）灌，而不是等。"})
    except Exception:
        pass

    # 7) 僵尸注册
    try:
        total_users = db.execute("SELECT COUNT(*) c FROM users WHERE is_admin=0").fetchone()['c']
        active30 = db.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM activity_log WHERE created_at>?",
            (time.time() - 30 * 86400,)).fetchone()['c']
        if total_users > 10:
            rate = active30 * 100.0 / total_users
            out.append({"level": "warn" if rate < 30 else "flat", "icon": "🧟",
                        "title": "%d 个注册用户里，近 30 天活跃的只有 %d 人（%.0f%%）" % (total_users, active30, rate),
                        "text": "剩下 %d 人是「僵尸注册」。清理掉能让注册数变诚实，"
                                "也能让活跃率这个指标真正反映情况。"
                                % (total_users - active30)})
    except Exception:
        pass

    return out


_BATCH_SUPER_ONLY = {'ai_quota', 'reset_game', 'reset_password', 'deactivate'}


_BATCH_ACTION_LABEL = {
    'ai_quota': '设置 AI 配额',
    'ban': '封禁',
    'mute': '禁言',
    'lift_ban': '解除封禁',
    'lift_mute': '解除禁言',
    'effect_give': '授予特效',
    'effect_remove': '移除特效',
    'badge_give': '授予荣誉牌子',
    'badge_remove': '移除荣誉牌子',
    'reset_game': '重置游戏成绩',
    'reset_password': '重置密码',
    'deactivate': '注销账号',
}


_DEACTIVATE_CLEAR = (
    'signature', 'custom_name_color', 'badge_text', 'badge_effect',
    'effect_type', 'avatar', 'has_effect', 'mall_title', 'title_text',
)


def _gen_deactivated_name(db, tries=30):
    """生成一个唯一的「用户12345」形式的注销用户名。

    为什么是**纯数字**：原来的字母版本（用户be3mh）看着像乱码，
    用户反馈分不清是用户名还是系统串。数字好认也好念。
    为什么不用「已注销_<id>」：那样一眼能看出是被注销的账号，
    对当事人不太友好；用中性名字更自然，同时原用户名被释放出来供他人注册。
    """
    for _ in range(tries):
        name = '用户' + ''.join(secrets.choice('0123456789') for _ in range(5))
        exists = db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone()
        if not exists:
            return name
    return '用户' + str(int(time.time()))[-6:]


def _rand_password(n=10):
    """生成随机密码（去掉容易混淆的 0/O/1/l/I）。"""
    alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(n))


class _FakeForm(dict):
    """让 _calc_until 也能吃 JSON 数据。"""
    def get(self, k, default=None):
        v = dict.get(self, k, default)
        return v


def _duration_text_from_until(until):
    """把绝对时间戳转回「X天Y时Z分」的描述（用于处罚档案）。"""
    if until == D['PERMANENT']:
        return '永久'
    if not until:
        return '已解除'
    left = int(until - time.time())
    if left <= 0:
        return '已到期'
    d, left = divmod(left, 86400)
    h, left = divmod(left, 3600)
    m = left // 60
    parts = []
    if d: parts.append('%d 天' % d)
    if h: parts.append('%d 时' % h)
    if m or not parts: parts.append('%d 分' % m)
    return ' '.join(parts)


def _ai_code_tables(db):
    """兑换码两张表。在路由里第一次访问时创建，跟全站迁移风格一致。

    ⚠️ 线上老库的表是早期壳子建的旧 schema（add_times，没有 per_use/
    created_by/valid_from/valid_until/used_at），CREATE IF NOT EXISTS 不会补列，
    必须逐列 D['_add_column'] 迁移，否则 INSERT/SELECT 直接 no such column。
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_redeem_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT NOT NULL UNIQUE,
            note        TEXT NOT NULL DEFAULT '',
            max_uses    INTEGER NOT NULL DEFAULT 1,
            used_count  INTEGER NOT NULL DEFAULT 0,
            per_use     INTEGER NOT NULL DEFAULT 1,
            created_by  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0,
            valid_from  TEXT NOT NULL DEFAULT '',
            valid_until TEXT NOT NULL DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_redeem_used (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            code_id  INTEGER NOT NULL,
            user_id  INTEGER NOT NULL,
            used_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(code_id, user_id)
        )
    """)
    D['_add_column'](db, "ai_redeem_codes", "per_use", "INTEGER NOT NULL DEFAULT 1")
    D['_add_column'](db, "ai_redeem_codes", "created_by", "INTEGER NOT NULL DEFAULT 0")
    D['_add_column'](db, "ai_redeem_codes", "valid_from", "TEXT NOT NULL DEFAULT ''")
    D['_add_column'](db, "ai_redeem_codes", "valid_until", "TEXT NOT NULL DEFAULT ''")
    D['_add_column'](db, "ai_redeem_used", "used_at", "REAL NOT NULL DEFAULT 0")
    db.commit()


def _gen_ai_code(db, n=12):
    """12 位大写字母+数字，去掉易混淆的 0/O/1/I。"""
    import random as _r
    ALPHA = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    for _ in range(60):
        c = ''.join(_r.choice(ALPHA) for _ in range(n))
        if not db.execute("SELECT 1 FROM ai_redeem_codes WHERE code=?", (c,)).fetchone():
            return c
    return None


# ── 后台：AI 兑换码管理 ────────────────────────────────────────



_MM_LABEL = {'gomoku': '五子棋', 'go': '围棋', 'xq': '象棋',
             'chess': '国际象棋', 'reversi': '黑白棋', 'jq': '揭棋'}


_REQ_LINE_RE = re.compile(
    r"REQ (\w+) (\S+) -> (\d{3}) (-?\d+)ms ip=(\S+) uid=(\S+)")


def _recycle_pick(db, ids):
    """从传入 id 里筛出确实在回收站中的帖子。"""
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT id FROM posts WHERE recycle = 1 AND id IN (%s)" % q, ids).fetchall()
    return [r['id'] for r in rows]


def _fmt_dur(ms):
    s = int(ms or 0) / 1000.0
    if s <= 0:
        return "0 分钟"
    if s < 3600:
        return "%d 分钟" % int(s / 60)
    return "%d 小时 %d 分" % (int(s // 3600), int((s % 3600) / 60))


