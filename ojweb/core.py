# -*- coding: utf-8 -*-
"""ojweb/core.py — MONO OJ 的 Web 层（题库/比赛/提交/记录/后台管理）。

从 D['app'].py 搬迁。判题机是 oj/ 目录的 judge_server.py（HTTP :8081），
这里通过 _oj_api 与它通信。helper 一律走 deps.D 注入（不 import D['app']）。
"""
from datetime import datetime
import io
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import zipfile

from flask import flash, jsonify, redirect, render_template, request, url_for

from .deps import D

OJ_JUDGE = "http://127.0.0.1:8081"


OJ_TEST_PROBLEM = 2


OJ_DATA_DIR = os.path.join(os.environ.get("MONO_DATA_DIR") or os.path.dirname(os.path.abspath(__file__)), "oj_data")


OJ_PROBLEMS_DIR = os.path.join(OJ_DATA_DIR, "problems")


OJ_JUDGE_PROBLEMS = "/opt/mono/oj/problems"


OJ_MAX_ZIP_MB = 64


OJ_RULES = {
    "ioi": "IOI 赛制（每题取最高分，求和排名）",
    "oi":  "OI 赛制（每题取最后一次提交，求和排名）",
    "acm": "ACM 赛制（过题数优先，罚时 = 过题用时 + 20 分钟/次错误）",
    "ldu": "乐多赛制（每题取最后一次提交，不罚时，鼓励多交）",
}


_OJ_SUB_TASKS = {}


_OJ_SUB_LOCK = threading.Lock()


_OJ_TRACKING = set()


_OJ_KICKED = set()


_OJ_SWEEP_STARTED = False




def _oj_dt_local(ts):
    try:
        ts = float(ts or 0)
    except Exception:
        ts = 0
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, D['_TZ_CN']).strftime("%Y-%m-%dT%H:%M")


def _oj_admin_required():
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return None, redirect(url_for("login"))
    if not me["is_admin"]:
        flash("只有管理员能操作比赛", "error")
        return None, redirect(url_for("oj_home"))
    return me, None


def _oj_problem_dir(pid):
    d = os.path.join(OJ_PROBLEMS_DIR, str(pid))
    os.makedirs(d, exist_ok=True)
    return d


def _oj_fmt_time(a, b):
    def one(ms):
        return "%.2f s" % (ms / 1000.0)
    return one(a) if a == b else "%s ~ %s" % (one(a), one(b))


def _oj_fmt_mem(a, b):
    return "%d MB" % a if a == b else "%d MB ~ %d MB" % (a, b)


def _oj_limits(pid, ft=1000, fm=256):
    row = D['get_db']().execute("""
        SELECT MIN(NULLIF(time_limit_ms, 0)) AS t0, MAX(time_limit_ms) AS t1,
               MIN(NULLIF(memory_limit_mb, 0)) AS m0, MAX(memory_limit_mb) AS m1
        FROM oj_testcases WHERE problem_id=?
    """, (pid,)).fetchone()
    t0 = (row["t0"] if row else 0) or ft
    t1 = (row["t1"] if row else 0) or ft
    m0 = (row["m0"] if row else 0) or fm
    m1 = (row["m1"] if row else 0) or fm
    return {"t_min": t0, "t_max": t1, "m_min": m0, "m_max": m1,
            "time_text": _oj_fmt_time(t0, t1), "mem_text": _oj_fmt_mem(m0, m1)}


def _oj_limits_map(problems):
    out = {}
    for p in problems:
        out[p["id"]] = _oj_limits(p["id"], p["time_limit_ms"] or 1000,
                                  p["memory_limit_mb"] or 256)
    return out


def _oj_super(me):
    return bool(me["is_super_admin"])


def _oj_locked(me, pid=None):
    """是否锁定提交。

    原来的「入门测试」机制已废弃（测试题被删），现在不再锁。
    留这个函数是为了兼容调用方，未来要加限制在这里加。
    """
    return False


def _oj_dur(sec):
    if sec is None or sec < 0:
        return ""
    if sec < 60:
        return "%.1fs" % sec
    if sec < 3600:
        return "%.2fmin" % (sec / 60.0)
    return "%.2fh" % (sec / 3600.0)


def _oj_leaderboard(cid, me, rule=None):
    """排行榜计算。按赛制分支：

    - ioi : 每题取最高分，求和
    - oi  : 每题取最后一次提交的得分，求和
    - acm : 过题数优先，罚时 = 过题用时 + 20 分钟/次错误
    - ldu : 乐多赛制 —— 无限次提交；AC 时按「100 - 5×错误次数」折算，
            下限 60 分；未 AC（含 PAC 部分通过）不计算罚分，按通过比例给分

    rule 为 None 时从比赛记录里读。
    """
    db = D['get_db']()
    ct = db.execute("SELECT start_at, end_at, rule FROM oj_contests WHERE id=?",
                    (cid,)).fetchone()
    c_start = (ct["start_at"] or 0) if ct else 0
    c_end = (ct["end_at"] or 0) if ct else 0
    # ★ 显式传入的 rule 优先（测试和调用方指定）；没传才用比赛记录
    if rule is None:
        rule = (ct["rule"] if ct and ct["rule"] else "ioi")

    probs = db.execute("""
        SELECT id, title, difficulty FROM oj_problems WHERE contest_id=? ORDER BY id ASC
    """, (cid,)).fetchall()
    pid_list = [p["id"] for p in probs]

    full_map = {}
    for r in db.execute("SELECT problem_id, SUM(score) AS s FROM oj_testcases GROUP BY problem_id"):
        full_map[r["problem_id"]] = r["s"] or 0

    conds = ["contest_id=?", "verdict != ''"]
    args = [cid]
    if pid_list:
        conds.append("problem_id IN (%s)" % ",".join("?" * len(pid_list)))
        args.extend(pid_list)
    if c_start > 0:
        conds.append("created_at >= ?")
        args.append(c_start)
    if c_end > 0:
        conds.append("created_at <= ?")
        args.append(c_end)

    # 拿该比赛所有有效提交（按时间升序，方便算「第一次 AC 前错了几次」）
    subs = []
    if pid_list:
        for r in db.execute(
            "SELECT user_id, problem_id, score, verdict, created_at "
            "FROM oj_submissions WHERE %s ORDER BY created_at ASC" % " AND ".join(conds),
            tuple(args)):
            subs.append({
                "uid": r["user_id"], "pid": r["problem_id"],
                "score": r["score"] or 0, "verdict": r["verdict"] or "",
                "ts": r["created_at"] or 0,
            })

    # 按 (uid, pid) 分组
    by_user_prob = {}
    for x in subs:
        by_user_prob.setdefault((x["uid"], x["pid"]), []).append(x)

    AC_VERDICTS = ("AC",)
    WRONG_VERDICTS = ("WA", "TLE", "MLE", "RE", "OLE", "SE")

    per = {}      # (uid, pid) -> {"score": 分数, "ts": 首次有效时间, "full": 是否满分}
    penalty = {}  # (uid, pid) -> 罚时分钟（ACM 用）
    seen = {}

    for (uid, pid), lst in by_user_prob.items():
        seen[uid] = True
        full = full_map.get(pid) or 0
        lst_sorted = sorted(lst, key=lambda x: x["ts"])

        # ── 第一次 AC 的位置 ──
        first_ac_idx = None
        for idx, x in enumerate(lst_sorted):
            if x["verdict"] in AC_VERDICTS:
                first_ac_idx = idx
                break

        if rule == "acm":
            # 过题数优先，罚时 = AC 用时 + 20 分钟 × AC 前错误次数
            if first_ac_idx is None:
                continue
            ac_sub = lst_sorted[first_ac_idx]
            wrongs = sum(1 for x in lst_sorted[:first_ac_idx] if x["verdict"] in WRONG_VERDICTS)
            used = (ac_sub["ts"] - c_start) if c_start > 0 else 0
            penalty[(uid, pid)] = (used / 60.0) + 20.0 * wrongs
            per[(uid, pid)] = {"score": full or ac_sub["score"] or 1,
                               "ts": ac_sub["ts"], "full": True}

        elif rule == "oi":
            # 每题取最后一次提交
            last = lst_sorted[-1]
            per[(uid, pid)] = {"score": last["score"], "ts": last["ts"],
                               "full": bool(full > 0 and last["score"] >= full)}

        elif rule == "ldu":
            # ★ 乐多赛制
            # AC 了 → 100 分基准，每次 AC 前的错误扣 5 分，下限 60
            # 没 AC → 按最高分比例给分，不扣罚分（PAC 不罚）
            if first_ac_idx is not None:
                wrongs = sum(1 for x in lst_sorted[:first_ac_idx] if x["verdict"] in WRONG_VERDICTS)
                pct = max(60, 100 - 5 * wrongs)
                base = full if full > 0 else 100
                got = base * pct / 100.0
                per[(uid, pid)] = {"score": got, "ts": lst_sorted[first_ac_idx]["ts"],
                                   "full": True, "penalty_pct": pct, "wrongs": wrongs}
            else:
                best = max(x["score"] for x in lst_sorted)
                last = lst_sorted[-1]
                per[(uid, pid)] = {"score": best, "ts": last["ts"],
                                   "full": False, "penalty_pct": None, "wrongs": 0}

        else:
            # ioi：取最高分（同分取更早）
            best = None
            for x in lst_sorted:
                if best is None or x["score"] > best["score"] or \
                   (x["score"] == best["score"] and x["ts"] < best["ts"]):
                    best = x
            per[(uid, pid)] = {"score": best["score"], "ts": best["ts"],
                               "full": bool(full > 0 and best["score"] >= full)}

    users = {}
    if seen:
        marks = ",".join("?" * len(seen))
        for u in db.execute("SELECT id, username, user_no FROM users WHERE id IN (%s)" % marks,
                            tuple(seen.keys())):
            users[u["id"]] = dict(u)

    board = []
    for uid in seen:
        u = users.get(uid) or {"username": "已注销", "user_no": ""}
        cells = {}
        total = 0.0
        tsum = 0.0
        solved = 0
        last = 0
        pen_total = 0.0
        for p in probs:
            got = per.get((uid, p["id"]))
            if not got:
                cells[p["id"]] = {"score": 0, "time": "", "full": False, "tried": False}
                continue
            sc = got["score"]
            ts = got["ts"]
            rel = (ts - c_start) if c_start > 0 else None
            cell = {
                "score": round(sc, 1),
                "time": _oj_dur(rel),
                "full": got["full"],
                "tried": True,
            }
            # 乐多：显示扣分情况（只在真扣了分时显示）
            if rule == "ldu" and got.get("penalty_pct") is not None:
                deducted = 100 - got["penalty_pct"]
                if deducted > 0:
                    cell["penalty"] = "-%d" % deducted
            # ACM：显示罚时（0 不显示）
            if rule == "acm":
                pen = penalty.get((uid, p["id"])) or 0
                if pen > 0:
                    cell["penalty"] = "%d" % round(pen)
                pen_total += pen
            cells[p["id"]] = cell
            total += sc
            if got["full"] and rel is not None:
                tsum += rel
            if got["full"]:
                solved += 1
            last = max(last, ts)
        board.append({
            "username": u["username"],
            "user_no": u.get("user_no") or "",
            "cells": cells,
            "total": round(total, 1),
            "total_time": _oj_dur(tsum) if (c_start > 0 and tsum > 0) else "",
            "penalty_total": round(pen_total) if rule == "acm" else None,
            "solved": solved,
            "last": last,
            "is_me": uid == me["id"],
        })

    if rule == "acm":
        # 过题数降序 → 罚时升序 → 最后提交升序
        board.sort(key=lambda x: (-x["solved"], x["penalty_total"] or 0, x["last"], x["username"]))
    else:
        board.sort(key=lambda x: (-x["total"], x["last"], x["username"]))

    for i, row in enumerate(board, 1):
        row["rank"] = i

    pstats = []
    for i, p in enumerate(probs):
        fl = full_map.get(p["id"]) or 0
        ac = 0
        sub = 0
        for uid in seen:
            got = per.get((uid, p["id"]))
            if got:
                sub += 1
                if got["full"]:
                    ac += 1
        pstats.append({"id": p["id"], "title": p["title"],
                       "letter": chr(65 + i) if i < 26 else str(i + 1),
                       "ac": ac, "sub": sub})

    return pstats, full_map, board


def _oj_write_result(sub_id, task, uid):
    if task.get("status") != "done":
        return False
    db = D['_tune_conn'](sqlite3.connect(D['DB_PATH'], timeout=10))
    try:
        wrote = False
        if sub_id:
            cur = db.execute("""
                UPDATE oj_submissions SET verdict=?, time_ms=?, memory_kb=?,
                       passed=?, total=?, score=?, full_score=?, detail=?
                WHERE id=? AND verdict=''
            """, (task.get("verdict") or "", task.get("time_ms") or 0,
                  task.get("memory_kb") or 0, task.get("passed") or 0,
                  task.get("total") or 0, task.get("score") or 0,
                  task.get("full_score") or 0,
                  json.dumps(task.get("tests") or [], ensure_ascii=False),
                  sub_id))
            wrote = cur.rowcount > 0
        if task.get("verdict") == "AC" and uid:
            db.execute("UPDATE users SET oj_verified=1, oj_verified_at=? WHERE id=?",
                       (time.time(), uid))
        db.commit()
        return wrote
    finally:
        db.close()


def _oj_kick(sub_id, code, pid, std, o2, uid):
    try:
        r = _oj_api("/judge", {"code": code, "problem_id": int(pid),
                               "std": std, "o2": bool(o2)}, timeout=30)
        tid = r.get("task_id")
        if not tid:
            raise RuntimeError(r.get("msg") or "判题机没有返回任务号")
    except Exception:
        _oj_write_result(sub_id, {
            "status": "done", "verdict": "SE", "time_ms": 0, "memory_kb": 0,
            "passed": 0, "total": 0, "score": 0, "full_score": 0,
        }, uid)
        return

    db = D['_tune_conn'](sqlite3.connect(D['DB_PATH'], timeout=10))
    try:
        db.execute("UPDATE oj_submissions SET task_id=? WHERE id=?", (tid, sub_id))
        db.commit()
    finally:
        db.close()

    with _OJ_SUB_LOCK:
        _OJ_SUB_TASKS[tid] = {"sub_id": sub_id, "pid": int(pid),
                              "uid": uid, "std": std, "o2": o2}
        _OJ_TRACKING.add(tid)
    _oj_track(tid)


def _oj_track(tid):
    try:
        for _ in range(900):
            time.sleep(2)
            try:
                r = _oj_api("/status/" + tid, timeout=10)
            except Exception as e:
                if getattr(e, "code", None) == 404:
                    with _OJ_SUB_LOCK:
                        info = _OJ_SUB_TASKS.pop(tid, None)
                    if info:
                        _oj_write_result(info.get("sub_id"), {
                            "status": "done", "verdict": "SE", "time_ms": 0,
                            "memory_kb": 0, "passed": 0, "total": 0,
                            "score": 0, "full_score": 0,
                        }, info.get("uid"))
                    return
                continue
            task = r.get("task") or {}
            if task.get("status") != "done":
                continue
            with _OJ_SUB_LOCK:
                info = _OJ_SUB_TASKS.pop(tid, None)
            if info:
                _oj_write_result(info.get("sub_id"), task, info.get("uid"))
            return
    finally:
        with _OJ_SUB_LOCK:
            _OJ_TRACKING.discard(tid)


def _oj_resume_pending():
    db = D['_tune_conn'](sqlite3.connect(D['DB_PATH'], timeout=10))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("""
            SELECT id, task_id, user_id, problem_id, code, lang, created_at
            FROM oj_submissions
            WHERE verdict='' AND created_at > ?
            ORDER BY id DESC LIMIT 50
        """, (time.time() - 86400,)).fetchall()
    except Exception:
        return
    finally:
        db.close()
    for r in rows:
        tid = r["task_id"]
        if not tid:
            if r["id"] in _OJ_KICKED or (time.time() - (r["created_at"] or 0)) < 20:
                continue
            _OJ_KICKED.add(r["id"])
            parts = (r["lang"] or "c++17").split("|")
            threading.Thread(target=_oj_kick, args=(
                r["id"], r["code"] or "", r["problem_id"], parts[0] or "c++17",
                len(parts) > 1 and parts[1] == "o2", r["user_id"]), daemon=True).start()
            continue
        with _OJ_SUB_LOCK:
            if tid in _OJ_TRACKING:
                continue
            _OJ_TRACKING.add(tid)
            _OJ_SUB_TASKS.setdefault(tid, {"sub_id": r["id"], "pid": r["problem_id"],
                                           "uid": r["user_id"], "std": "c++17",
                                           "o2": True})
        threading.Thread(target=_oj_track, args=(tid,), daemon=True).start()


def _oj_sweep_loop():
    while True:
        time.sleep(60)
        try:
            _oj_resume_pending()
        except Exception:
            pass


def _oj_ensure_sweeper():
    global _OJ_SWEEP_STARTED
    if _OJ_SWEEP_STARTED:
        return
    _OJ_SWEEP_STARTED = True
    threading.Thread(target=_oj_sweep_loop, daemon=True).start()


def _oj_parse_tests(zip_bytes):
    out = []
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = zf.namelist()

    def base(nm):
        return nm.split("/")[-1].lower()

    pairs = {}
    for nm in names:
        b = base(nm)
        m = re.match(r"^(?:input)?(\d+)\.in$", b) or re.match(r"^input(\d+)\.txt$", b)
        if m:
            pairs.setdefault(int(m.group(1)), {})["in"] = nm
            continue
        m = re.match(r"^(?:output|answer)?(\d+)\.out$", b) or re.match(r"^(?:output|answer)(\d+)\.txt$", b)
        if m:
            pairs.setdefault(int(m.group(1)), {})["out"] = nm

    idxs = sorted(k for k in pairs if "in" in pairs[k] and "out" in pairs[k])
    for i in idxs:
        inp = zf.read(pairs[i]["in"]).decode("utf-8", "replace")
        exp = zf.read(pairs[i]["out"]).decode("utf-8", "replace")
        out.append((i, inp, exp))

    return out, sorted(set(pairs.keys()))


def _oj_save_tests(pid, tests, cfg=None):
    d = _oj_problem_dir(pid)
    for f in os.listdir(d):
        try:
            os.remove(os.path.join(d, f))
        except Exception:
            pass
    for idx, inp, exp in tests:
        with open(os.path.join(d, f"{idx}.in"), "w", encoding="utf-8") as f:
            f.write(inp)
        with open(os.path.join(d, f"{idx}.out"), "w", encoding="utf-8") as f:
            f.write(exp)

    db = D['get_db']()

    # ★ 没显式给 cfg 时，从 oj_testcases 自动生成每点的分数/时限/内存。
    #   否则 problem.json 里 cases 是空的 → 判题机每点 score=0 →
    #   full_score 兜底成 len(tests)*100（7 个点就变成 700 分）。
    if cfg is None:
        cfg = {}
        try:
            for r in db.execute(
                "SELECT idx, score, time_limit_ms, memory_limit_mb "
                "FROM oj_testcases WHERE problem_id=? ORDER BY idx", (pid,)).fetchall():
                cfg[str(r["idx"])] = {
                    "score": r["score"] or 0,
                    "time_ms": r["time_limit_ms"] or 0,
                    "memory_mb": r["memory_limit_mb"] or 0,
                }
        except Exception:
            cfg = {}

    lim = _oj_limits(pid)
    with open(os.path.join(d, "problem.json"), "w", encoding="utf-8") as f:
        json.dump({"id": str(pid), "tests": len(tests), "cases": cfg,
                   "time_limit_ms": lim["t_max"],
                   "memory_limit_mb": lim["m_max"]},
                  f, ensure_ascii=False)
    db.execute("UPDATE oj_problems SET time_limit_ms=?, memory_limit_mb=? WHERE id=?",
               (lim["t_max"], lim["m_max"], pid))
    db.commit()




def oj_admin():
    me, redir = _oj_admin_required()
    if redir:
        return redir
    db = D['get_db']()
    rows = db.execute("""
        SELECT c.*, (SELECT COUNT(*) FROM oj_problems p WHERE p.contest_id = c.id) AS pcount
        FROM oj_contests c ORDER BY c.created_at DESC
    """).fetchall()
    contests = [dict(r) for r in rows]
    for c in contests:
        c["start_label"] = D['fmt_cn'](c["start_at"]) if c["start_at"] else "未设置"
        c["end_label"] = D['fmt_cn'](c["end_at"]) if c["end_at"] else "未设置"
    return render_template("oj_admin.html", me=me, contests=contests, active="oj")




def oj_admin_contest_new():
    me, redir = _oj_admin_required()
    if redir:
        return redir
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("比赛标题不能为空", "error")
        return redirect(url_for("oj_admin"))
    db = D['get_db']()
    cur = db.execute("""
        INSERT INTO oj_contests (title, description, start_at, end_at, is_public, created_by, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (title[:120], "", 0, 0, 1, me["id"], time.time()))
    db.commit()
    flash("比赛已创建，接着添加题目吧", "ok")
    return redirect(url_for("oj_admin_contest", cid=cur.lastrowid))




def oj_admin_contest(cid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    db = D['get_db']()
    c = db.execute("SELECT * FROM oj_contests WHERE id=?", (cid,)).fetchone()
    if not c:
        flash("比赛不存在", "error")
        return redirect(url_for("oj_admin"))
    probs = db.execute("""
        SELECT * FROM oj_problems WHERE contest_id=? ORDER BY id ASC
    """, (cid,)).fetchall()
    return render_template("oj_admin_contest.html", me=me, contest=dict(c),
                           problems=[dict(p) for p in probs],
                           limits=_oj_limits_map(probs),
                           rules=OJ_RULES, active="oj")




def oj_admin_contest_save(cid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    title = (request.form.get("title") or "").strip()[:120]
    desc = (request.form.get("description") or "").strip()[:4000]
    start = request.form.get("start_at") or ""
    end = request.form.get("end_at") or ""
    pub = 1 if request.form.get("is_public") else 0
    rule = (request.form.get("rule") or "ioi").strip().lower()
    if rule not in OJ_RULES:
        rule = "ioi"

    def parse_dt(v):
        if not v:
            return 0
        try:
            return datetime.strptime(v, "%Y-%m-%dT%H:%M").replace(tzinfo=D['_TZ_CN']).timestamp()
        except Exception:
            return 0

    db = D['get_db']()
    db.execute("""
        UPDATE oj_contests SET title=?, description=?, start_at=?, end_at=?,
               is_public=?, rule=? WHERE id=?
    """, (title, desc, parse_dt(start), parse_dt(end), pub, rule, cid))
    db.commit()
    flash("已保存", "ok")
    return redirect(url_for("oj_admin_contest", cid=cid))




def oj_admin_contest_delete(cid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    db = D['get_db']()
    db.execute("DELETE FROM oj_contests WHERE id=?", (cid,))
    db.commit()
    flash("比赛已删除", "ok")
    return redirect(url_for("oj_admin"))




def oj_admin_problem_new(cid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    title = (request.form.get("title") or "").strip() or "新题目"
    db = D['get_db']()
    cur = db.execute("""
        INSERT INTO oj_problems (contest_id, title, description, created_by, created_at)
        VALUES (?,?,?,?,?)
    """, (cid, title[:120], "", me["id"], time.time()))
    db.commit()
    return redirect(url_for("oj_admin_problem", pid=cur.lastrowid))




def oj_admin_problem(pid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    db = D['get_db']()
    p = db.execute("SELECT * FROM oj_problems WHERE id=?", (pid,)).fetchone()
    if not p:
        flash("题目不存在", "error")
        return redirect(url_for("oj_admin"))
    c = db.execute("SELECT * FROM oj_contests WHERE id=?", (p["contest_id"],)).fetchone()
    cases = db.execute("""
        SELECT idx, length(input) AS in_len, length(expected) AS out_len, is_sample,
               time_limit_ms, memory_limit_mb, score
        FROM oj_testcases WHERE problem_id=? ORDER BY idx ASC
    """, (pid,)).fetchall()
    samples = []
    try:
        samples = json.loads(p["samples"] or "[]")
    except Exception:
        samples = []
    return render_template("oj_admin_problem.html", me=me, problem=dict(p),
                           contest=dict(c) if c else None,
                           limits=_oj_limits(pid, p["time_limit_ms"] or 1000,
                                             p["memory_limit_mb"] or 256),
                           cases=[dict(x) for x in cases], samples=samples, active="oj")




def oj_admin_problem_save(pid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    f = request.form
    db = D['get_db']()
    raw_samples = f.getlist("sample_in")
    raw_outs = f.getlist("sample_out")
    samples = []
    for i, si in enumerate(raw_samples):
        so = raw_outs[i] if i < len(raw_outs) else ""
        if (si or "").strip() == "" and (so or "").strip() == "":
            continue
        samples.append({"in": si, "out": so})

    db.execute("""
        UPDATE oj_problems SET title=?, description=?, input_format=?, output_format=?,
               hint=?, samples=?, difficulty=?, tags=?
        WHERE id=?
    """, (
        (f.get("title") or "").strip()[:120],
        (f.get("description") or "")[:20000],
        (f.get("input_format") or "")[:4000],
        (f.get("output_format") or "")[:4000],
        (f.get("hint") or "")[:8000],
        json.dumps(samples, ensure_ascii=False),
        (f.get("difficulty") or "").strip()[:20],
        (f.get("tags") or "").strip()[:200],
        pid,
    ))
    db.commit()
    flash("题目已保存（%d 组样例）" % len(samples), "ok")
    return redirect(url_for("oj_admin_problem", pid=pid))




def oj_admin_problem_upload(pid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    up = request.files.get("zipfile")
    if not up or not up.filename:
        flash("请选择 zip 文件", "error")
        return redirect(url_for("oj_admin_problem", pid=pid))

    data = up.read()
    if len(data) > OJ_MAX_ZIP_MB * 1024 * 1024:
        flash("压缩包太大了（上限 %d MB）" % OJ_MAX_ZIP_MB, "error")
        return redirect(url_for("oj_admin_problem", pid=pid))

    name = (up.filename or "").lower()
    if not name.endswith(".zip"):
        flash("只支持 .zip 格式（.rar 请先转成 zip）", "error")
        return redirect(url_for("oj_admin_problem", pid=pid))

    try:
        tests, all_idx = _oj_parse_tests(data)
    except Exception as e:
        flash("压缩包解析失败：" + str(e)[:120], "error")
        return redirect(url_for("oj_admin_problem", pid=pid))

    if not tests:
        flash("没找到有效的测试点。zip 里需要 1.in/1.out、2.in/2.out…（或 input1.txt/output1.txt）", "error")
        return redirect(url_for("oj_admin_problem", pid=pid))

    db = D['get_db']()
    prow = db.execute("SELECT samples, time_limit_ms, memory_limit_mb FROM oj_problems WHERE id=?",
                      (pid,)).fetchone()
    t_ms = int((prow["time_limit_ms"] if prow else None) or 1000)
    m_mb = int((prow["memory_limit_mb"] if prow else None) or 256)
    db.execute("DELETE FROM oj_testcases WHERE problem_id=?", (pid,))
    total = len(tests)
    base = max(1, round(100.0 / max(1, total)))
    for i, (idx, inp, exp) in enumerate(tests, 1):
        db.execute("""
            INSERT INTO oj_testcases
                (problem_id, idx, input, expected, is_sample,
                 time_limit_ms, memory_limit_mb, score)
            VALUES (?,?,?,?,?,?,?,?)
        """, (pid, i, inp, exp, 1 if i <= 2 else 0, t_ms, m_mb, base))
    db.execute("UPDATE oj_problems SET test_count=? WHERE id=?", (len(tests), pid))

    cur_samples = []
    try:
        cur_samples = json.loads((prow["samples"] if prow else "") or "[]")
    except Exception:
        cur_samples = []
    if not cur_samples:
        auto = []
        for idx, inp, exp in tests[:2]:
            auto.append({"in": inp, "out": exp})
        db.execute("UPDATE oj_problems SET samples=? WHERE id=?",
                   (json.dumps(auto, ensure_ascii=False), pid))

    db.commit()

    cfg = {}
    for i in range(1, len(tests) + 1):
        cfg[str(i)] = {"time_ms": t_ms, "memory_mb": m_mb, "score": base}
    _oj_save_tests(pid, [(i, inp, exp) for i, (_, inp, exp) in enumerate(tests, 1)],
                   cfg=cfg)

    missing = [k for k in all_idx if k not in [t[0] for t in tests]]
    msg = "已导入 %d 个测试点" % len(tests)
    if missing:
        msg += "（编号 %s 缺少配对文件，已跳过）" % ",".join(str(m) for m in missing[:8])
    flash(msg, "ok")
    return redirect(url_for("oj_admin_problem", pid=pid))




def oj_admin_problem_cases(pid):
    me, redir = _oj_admin_required()
    if redir:
        return redir

    db = D['get_db']()
    rows = db.execute("SELECT idx FROM oj_testcases WHERE problem_id=? ORDER BY idx",
                      (pid,)).fetchall()
    if not rows:
        flash("还没有测试点", "error")
        return redirect(url_for("oj_admin_problem", pid=pid))

    f = request.form
    cfg = {}
    for r in rows:
        i = r["idx"]
        try:
            t = int(f.get("t_%d" % i) or 1000)
        except Exception:
            t = 1000
        try:
            m = int(f.get("m_%d" % i) or 256)
        except Exception:
            m = 256
        try:
            sc = int(f.get("s_%d" % i) or 0)
        except Exception:
            sc = 0
        t = max(100, min(20000, t))
        m = max(16, min(1024, m))
        sc = max(0, min(1000, sc))
        db.execute("""UPDATE oj_testcases SET time_limit_ms=?, memory_limit_mb=?, score=?
                      WHERE problem_id=? AND idx=?""", (t, m, sc, pid, i))
        cfg[str(i)] = {"time_ms": t, "memory_mb": m, "score": sc}

    db.commit()

    tests = db.execute("""SELECT idx, input, expected FROM oj_testcases
                          WHERE problem_id=? ORDER BY idx ASC""", (pid,)).fetchall()
    _oj_save_tests(pid, [(r["idx"], r["input"], r["expected"]) for r in tests], cfg=cfg)

    total = sum(v["score"] for v in cfg.values())
    flash("测试点配置已保存（总分 %d）" % total, "ok")
    return redirect(url_for("oj_admin_problem", pid=pid))




def oj_admin_problem_delete(pid):
    me, redir = _oj_admin_required()
    if redir:
        return redir
    db = D['get_db']()
    p = db.execute("SELECT contest_id FROM oj_problems WHERE id=?", (pid,)).fetchone()
    cid = p["contest_id"] if p else 0
    db.execute("DELETE FROM oj_problems WHERE id=?", (pid,))
    db.execute("DELETE FROM oj_testcases WHERE problem_id=?", (pid,))
    db.commit()
    shutil.rmtree(os.path.join(OJ_PROBLEMS_DIR, str(pid)), ignore_errors=True)
    flash("题目已删除", "ok")
    return redirect(url_for("oj_admin_contest", cid=cid))


def _oj_api(path, payload=None, timeout=60):
    import urllib.request
    url = OJ_JUDGE + path
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))




def oj_home():
    me = D['current_user']()
    # 2026-10-31：题库改为「免登录只读」——分享出去的题能直接看，搜索引擎也能抓。
    # 个人状态（我的记录 / 我出的题 / 审核入口 / 判题机状态）只在登录后计算。
    _oj_ensure_sweeper()
    verified = bool(me["oj_verified"]) if (me and "oj_verified" in me.keys()) else False

    # 旧的「入门测试」机制已废弃（测试题被删了），不再强制跳转
    # 2026-10-31：连带删掉每页都去判题机拉 /problems 的死代码（模板根本没用到，
    # 却让每次打开题库都同步等判题机最多 10 秒）。

    judge_status = None
    if me and me["is_admin"]:
        try:
            judge_status = _oj_api("/health", timeout=4)
        except Exception:
            judge_status = {"ok": False}

    contests = []
    try:
        db = D['get_db']()
        rows = db.execute("""
            SELECT id, title, start_at, end_at, is_public
            FROM oj_contests
            WHERE is_public = 1
            ORDER BY start_at DESC LIMIT 50
        """).fetchall()
        now = time.time()
        for r in rows:
            st = r["start_at"] or 0
            et = r["end_at"] or 0
            if now < st:
                state, cls = "未开始", "wait"
            elif now <= et:
                state, cls = "进行中", "running"
            else:
                state, cls = "已结束", "ended"
            contests.append({
                "id": r["id"],
                "title": r["title"],
                "start_label": D['fmt_cn'](st, "%m-%d %H:%M") if st else "待定",
                "duration_label": ("%d 分钟" % int((et - st) / 60)) if (et > st) else "待定",
                "state_text": state,
                "state_class": cls,
            })
    except Exception:
        contests = []

    # ══════ 题库：只显示「公开 + 已审核」的题 ══════
    db = D['get_db']()
    problems = []
    try:
        rows = db.execute("""
            SELECT p.id, p.title, p.difficulty, p.tags, p.test_count,
                   p.created_by, p.created_at,
                   COALESCE(NULLIF(u.username,''), '匿名') AS author
            FROM oj_problems p
            LEFT JOIN users u ON u.id = p.created_by
            WHERE p.visibility = 'public' AND p.status = 'approved'
            ORDER BY p.id ASC
        """).fetchall()
        problems = [dict(r) for r in rows]
    except Exception as e:
        print("[oj] 题库读取失败:", e)
        problems = []

    my_status = {}
    if me:
        try:
            for r in db.execute(
                "SELECT problem_id, "
                "       MAX(CASE WHEN verdict='AC' THEN 1 ELSE 0 END) AS ac, "
                "       COUNT(*) AS tries "
                "FROM oj_submissions WHERE user_id=? GROUP BY problem_id",
                (me["id"],)).fetchall():
                my_status[r["problem_id"]] = {"ac": bool(r["ac"]), "tries": r["tries"]}
        except Exception:
            pass

    ac_map = {}
    try:
        for r in db.execute(
            "SELECT problem_id, COUNT(DISTINCT user_id) AS ac_users "
            "FROM oj_submissions WHERE verdict='AC' GROUP BY problem_id").fetchall():
            ac_map[r["problem_id"]] = r["ac_users"]
    except Exception:
        pass

    for p in problems:
        pid = p.get("id")
        st = my_status.get(pid, {})
        p["my_state"] = "ac" if st.get("ac") else ("tried" if st.get("tries") else "todo")
        p["my_tries"] = st.get("tries", 0)
        p["ac_users"] = ac_map.get(pid, 0)
        p["is_mine"] = bool(me) and (p.get("created_by") == me["id"])

    # ══════ 我出的题（含待审/驳回），只给自己看 ══════
    my_problems = []
    if me:
        try:
            rows = db.execute("""
                SELECT id, title, difficulty, visibility, status, reject_reason, created_at
                FROM oj_problems WHERE created_by = ?
                ORDER BY created_at DESC LIMIT 50
            """, (me["id"],)).fetchall()
            my_problems = [dict(r) for r in rows]
        except Exception:
            my_problems = []

    # ══════ 待审核数（超管可见） ══════
    pending_count = 0
    if me and me["is_admin"]:
        try:
            pending_count = db.execute(
                "SELECT COUNT(*) c FROM oj_problems WHERE status='pending'").fetchone()["c"]
        except Exception:
            pending_count = 0

    return render_template("oj_home.html", me=me, verified=verified,
                           problems=problems,
                           my_problems=my_problems, pending_count=pending_count,
                           judge_status=judge_status, active="oj",
                           last_result=None)




def _oj_can_edit(me, row):
    """能否编辑/删除这道题：作者本人 或 超管。"""
    if not me or not row:
        return False
    if me["is_admin"]:
        return True
    return row["created_by"] == me["id"]


def oj_problem_new():
    """出题页（新建）。所有人可用。提交后 status=pending，等超管审核。"""
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    return render_template("oj_problem_form.html", me=me, active="oj",
                           problem=None, is_new=True, error=None, cases=[])


def oj_problem_create():
    """提交新题（POST）。"""
    import json as _json
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    if request.method != "POST":
        return redirect(url_for("oj_problem_new"))

    title = (request.form.get("title") or "").strip()[:120]
    desc = (request.form.get("description") or "").strip()
    infmt = (request.form.get("input_format") or "").strip()
    outfmt = (request.form.get("output_format") or "").strip()
    hint = (request.form.get("hint") or "").strip()
    diff = (request.form.get("difficulty") or "").strip()[:20]
    tags = (request.form.get("tags") or "").strip()[:120]
    visibility = (request.form.get("visibility") or "public").strip()
    if visibility not in ("public", "contest"):
        visibility = "public"
    try:
        tl = int(request.form.get("time_limit_ms") or 1000)
        ml = int(request.form.get("memory_limit_mb") or 256)
    except (TypeError, ValueError):
        tl, ml = 1000, 256

    samples = []
    for i in range(1, 4):
        si = (request.form.get("sample_in_%d" % i) or "").strip()
        so = (request.form.get("sample_out_%d" % i) or "").strip()
        if si or so:
            samples.append({"in": si, "out": so})

    if not title or not desc:
        return render_template("oj_problem_form.html", me=me, active="oj",
                               problem=dict(request.form), is_new=True,
                               error="标题和题目描述不能为空", cases=[])

    db = D['get_db']()
    status = "approved" if me["is_admin"] else "pending"
    now = time.time()
    cur = db.execute(
        "INSERT INTO oj_problems "
        "(contest_id, title, description, input_format, output_format, "
        " samples, hint, time_limit_ms, memory_limit_mb, test_count, "
        " created_by, created_at, difficulty, tags, visibility, status) "
        "VALUES (0,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)",
        (title, desc, infmt, outfmt, _json.dumps(samples, ensure_ascii=False),
         hint, tl, ml, me["id"], now, diff, tags, visibility, status))
    db.commit()
    pid = cur.lastrowid
    flash("已提交，等待超管审核" if status == "pending" else "已创建（超管免审）", "ok")
    return redirect(url_for("oj_problem_edit", pid=pid))


def oj_problem_edit(pid):
    """编辑自己的题。"""
    import json as _json
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    db = D['get_db']()
    row = db.execute("SELECT * FROM oj_problems WHERE id=?", (pid,)).fetchone()
    if not row:
        flash("题目不存在", "error")
        return redirect(url_for("oj_home"))
    if not _oj_can_edit(me, row):
        flash("只能编辑自己出的题", "error")
        return redirect(url_for("oj_home"))

    problem = dict(row)
    try:
        problem["samples_list"] = _json.loads(problem.get("samples") or "[]")
    except Exception:
        problem["samples_list"] = []

    cases = db.execute(
        "SELECT id, idx, input, expected, is_sample FROM oj_testcases "
        "WHERE problem_id=? ORDER BY idx", (pid,)).fetchall()
    return render_template("oj_problem_form.html", me=me, active="oj",
                           problem=problem, is_new=False, error=None,
                           cases=[dict(c) for c in cases])


def oj_problem_update(pid):
    """保存编辑（POST）。"""
    import json as _json
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    db = D['get_db']()
    row = db.execute("SELECT * FROM oj_problems WHERE id=?", (pid,)).fetchone()
    if not row or not _oj_can_edit(me, row):
        flash("无权修改", "error")
        return redirect(url_for("oj_home"))

    title = (request.form.get("title") or "").strip()[:120]
    desc = (request.form.get("description") or "").strip()
    infmt = (request.form.get("input_format") or "").strip()
    outfmt = (request.form.get("output_format") or "").strip()
    hint = (request.form.get("hint") or "").strip()
    diff = (request.form.get("difficulty") or "").strip()[:20]
    tags = (request.form.get("tags") or "").strip()[:120]
    visibility = (request.form.get("visibility") or "public").strip()
    if visibility not in ("public", "contest"):
        visibility = "public"
    try:
        tl = int(request.form.get("time_limit_ms") or 1000)
        ml = int(request.form.get("memory_limit_mb") or 256)
    except (TypeError, ValueError):
        tl, ml = 1000, 256

    samples = []
    for i in range(1, 4):
        si = (request.form.get("sample_in_%d" % i) or "").strip()
        so = (request.form.get("sample_out_%d" % i) or "").strip()
        if si or so:
            samples.append({"in": si, "out": so})

    if not title or not desc:
        flash("标题和描述不能为空", "error")
        return redirect(url_for("oj_problem_edit", pid=pid))

    new_status = row["status"]
    if not me["is_admin"] and row["status"] == "approved":
        new_status = "pending"

    db.execute(
        "UPDATE oj_problems SET title=?, description=?, input_format=?, output_format=?, "
        "samples=?, hint=?, time_limit_ms=?, memory_limit_mb=?, "
        "difficulty=?, tags=?, visibility=?, status=? WHERE id=?",
        (title, desc, infmt, outfmt, _json.dumps(samples, ensure_ascii=False),
         hint, tl, ml, diff, tags, visibility, new_status, pid))
    db.commit()
    flash("已保存" + ("，等待重新审核" if new_status == "pending" else ""), "ok")
    return redirect(url_for("oj_problem_edit", pid=pid))


def oj_problem_delete_own(pid):
    """删除自己出的题（POST）。"""
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    db = D['get_db']()
    row = db.execute("SELECT * FROM oj_problems WHERE id=?", (pid,)).fetchone()
    if not row or not _oj_can_edit(me, row):
        flash("无权删除", "error")
        return redirect(url_for("oj_home"))
    db.execute("DELETE FROM oj_testcases WHERE problem_id=?", (pid,))
    db.execute("DELETE FROM oj_problems WHERE id=?", (pid,))
    db.commit()
    flash("已删除", "ok")
    return redirect(url_for("oj_home"))


def oj_problem_upload_own(pid):
    """出题者自己上传测试数据 ZIP（1.in/1.out/2.in/2.out…）。"""
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    db = D['get_db']()
    row = db.execute("SELECT * FROM oj_problems WHERE id=?", (pid,)).fetchone()
    if not row or not _oj_can_edit(me, row):
        flash("无权操作", "error")
        return redirect(url_for("oj_home"))

    f = request.files.get("zipfile")
    if not f or not f.filename:
        flash("请选择 ZIP 文件", "error")
        return redirect(url_for("oj_problem_edit", pid=pid))
    if not f.filename.lower().endswith(".zip"):
        flash("只支持 .zip 格式", "error")
        return redirect(url_for("oj_problem_edit", pid=pid))

    raw = f.read()
    if len(raw) > 60 * 1024 * 1024:
        flash("ZIP 超过 60MB，太大了", "error")
        return redirect(url_for("oj_problem_edit", pid=pid))

    try:
        tests = _oj_parse_tests(raw)
    except Exception as e:
        flash("解析 ZIP 失败：%s" % e, "error")
        return redirect(url_for("oj_problem_edit", pid=pid))

    if not tests:
        flash("ZIP 里没找到成对的 .in/.out 文件", "error")
        return redirect(url_for("oj_problem_edit", pid=pid))

    try:
        _oj_save_tests(pid, tests)
    except Exception as e:
        flash("保存测试数据失败：%s" % e, "error")
        return redirect(url_for("oj_problem_edit", pid=pid))

    # 同步 test_count
    try:
        db.execute("UPDATE oj_problems SET test_count=? WHERE id=?", (len(tests), pid))
        db.commit()
    except Exception:
        pass

    flash("已导入 %d 个测试点" % len(tests), "ok")
    return redirect(url_for("oj_problem_edit", pid=pid))


def oj_contest_problems(cid):
    """比赛赛题管理：从「比赛专用题」里挑题挂到这场比赛中。"""
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    if not me["is_admin"]:
        flash("只有管理员能管理比赛赛题", "error")
        return redirect(url_for("oj_home"))

    db = D['get_db']()
    c = db.execute("SELECT * FROM oj_contests WHERE id=?", (cid,)).fetchone()
    if not c:
        flash("比赛不存在", "error")
        return redirect(url_for("oj_contest_list"))

    # 这场比赛已有的题
    attached = db.execute(
        "SELECT id, title, difficulty FROM oj_problems "
        "WHERE contest_id=? ORDER BY id", (cid,)).fetchall()

    # 可选的比赛专用题（未挂到任何比赛 + 已审核）
    pool = db.execute(
        "SELECT id, title, difficulty, created_by FROM oj_problems "
        "WHERE visibility='contest' AND status='approved' AND (contest_id=0 OR contest_id IS NULL) "
        "ORDER BY id").fetchall()

    return render_template("oj_contest_problems.html", me=me, active="oj_contest",
                           contest=dict(c), attached=[dict(r) for r in attached],
                           pool=[dict(r) for r in pool])


def oj_contest_attach(cid):
    """把某道题挂到比赛（POST，pid）。"""
    me = D['current_user']()
    if not me or not me["is_admin"]:
        flash("无权操作", "error")
        return redirect(url_for("oj_home"))
    pid = request.form.get("pid")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        flash("题目无效", "error")
        return redirect(url_for("oj_contest_problems", cid=cid))
    db = D['get_db']()
    db.execute("UPDATE oj_problems SET contest_id=? WHERE id=?", (cid, pid))
    db.commit()
    flash("已加入比赛", "ok")
    return redirect(url_for("oj_contest_problems", cid=cid))


def oj_contest_detach(cid):
    """从比赛移除（POST，pid）。"""
    me = D['current_user']()
    if not me or not me["is_admin"]:
        flash("无权操作", "error")
        return redirect(url_for("oj_home"))
    pid = request.form.get("pid")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        flash("题目无效", "error")
        return redirect(url_for("oj_contest_problems", cid=cid))
    db = D['get_db']()
    db.execute("UPDATE oj_problems SET contest_id=0 WHERE id=? AND contest_id=?", (pid, cid))
    db.commit()
    flash("已移出比赛", "ok")
    return redirect(url_for("oj_contest_problems", cid=cid))


def oj_admin_review():
    """超管审核队列。"""
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    if not me["is_admin"]:
        flash("只有管理员能审核", "error")
        return redirect(url_for("oj_home"))
    db = D['get_db']()
    rows = db.execute(
        "SELECT p.id, p.title, p.difficulty, p.visibility, p.status, p.created_at, "
        "       COALESCE(NULLIF(u.username,''), '匿名') AS author "
        "FROM oj_problems p LEFT JOIN users u ON u.id = p.created_by "
        "WHERE p.status = 'pending' ORDER BY p.created_at ASC").fetchall()
    return render_template("oj_admin_review.html", me=me, active="oj",
                           pending=[dict(r) for r in rows])


def oj_admin_review_do(pid):
    """审核（POST）：action=approve / reject。"""
    me = D['current_user']()
    if not me or not me["is_admin"]:
        flash("无权操作", "error")
        return redirect(url_for("oj_home"))
    action = (request.form.get("action") or "").strip()
    reason = (request.form.get("reason") or "").strip()[:200]
    db = D['get_db']()
    if action == "approve":
        db.execute("UPDATE oj_problems SET status='approved', reviewer_id=?, "
                   "reviewed_at=?, reject_reason='' WHERE id=?",
                   (me["id"], time.time(), pid))
        flash("已通过", "ok")
    elif action == "reject":
        db.execute("UPDATE oj_problems SET status='rejected', reviewer_id=?, "
                   "reviewed_at=?, reject_reason=? WHERE id=?",
                   (me["id"], time.time(), reason, pid))
        flash("已驳回", "ok")
    db.commit()
    return redirect(url_for("oj_admin_review"))


def oj_contest_list():
    """公开赛独立页（从 oj_home 拆出来）。

    2026-10-31：免登录可看比赛列表（题目仍要登录才进得去）。
    """
    me = D['current_user']()

    db = D['get_db']()
    contests = []
    try:
        rows = db.execute(
            "SELECT id, title, start_at, end_at, is_public "
            "FROM oj_contests WHERE is_public = 1 "
            "ORDER BY start_at DESC LIMIT 50").fetchall()
        now = time.time()
        for r in rows:
            st = r["start_at"] or 0
            et = r["end_at"] or 0
            if now < st:
                state, cls = "未开始", "wait"
            elif now <= et:
                state, cls = "进行中", "running"
            else:
                state, cls = "已结束", "ended"
            # 题目数
            try:
                pc = db.execute("SELECT COUNT(*) c FROM oj_problems WHERE contest_id=?",
                                (r["id"],)).fetchone()["c"]
            except Exception:
                pc = 0
            contests.append({
                "id": r["id"],
                "title": r["title"],
                "start_label": D['fmt_cn'](st, "%m-%d %H:%M") if st else "待定",
                "duration_label": ("%d 分钟" % int((et - st) / 60)) if (et > st) else "待定",
                "state_text": state,
                "state_class": cls,
                "problem_count": pc,
            })
    except Exception:
        contests = []

    # 待审数（给导航角标）
    pending_count = 0
    if me and me["is_admin"]:
        try:
            pending_count = db.execute(
                "SELECT COUNT(*) c FROM oj_problems WHERE status='pending'").fetchone()["c"]
        except Exception:
            pending_count = 0

    return render_template("oj_contests.html", me=me, contests=contests,
                           pending_count=pending_count, active="oj_contest")


def oj_contest(cid):
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    db = D['get_db']()
    c = db.execute("SELECT * FROM oj_contests WHERE id=?", (cid,)).fetchone()
    if not c:
        flash("比赛不存在", "error")
        return redirect(url_for("oj_home"))
    if not c["is_public"] and not me["is_admin"]:
        flash("这场比赛还没公开", "error")
        return redirect(url_for("oj_home"))

    now = time.time()
    start_at = c["start_at"] or 0
    end_at = c["end_at"] or 0
    if now < start_at:
        state, cls = "未开始", "wait"
    elif now <= end_at:
        state, cls = "进行中", "running"
    else:
        state, cls = "已结束", "ended"

    # ★ 赛前隐藏：比赛没开始前，题目对普通用户完全不可见（超管除外）
    is_before = now < start_at
    hide_problems = is_before and not me["is_admin"]

    if hide_problems:
        probs = []
    else:
        probs = db.execute("""
            SELECT id, title, difficulty, tags, time_limit_ms, memory_limit_mb, test_count
            FROM oj_problems WHERE contest_id=? ORDER BY id ASC
        """, (cid,)).fetchall()

    if hide_problems:
        board_probs, full_map, board = [], {}, []
    else:
        board_probs, full_map, board = _oj_leaderboard(cid, me, c["rule"] or "ioi")

    mine = {}
    pid_list = [p["id"] for p in probs]
    if pid_list:
        marks = ",".join("?" * len(pid_list))
        for r in db.execute("""
            SELECT problem_id, MAX(score) AS sc FROM oj_submissions
            WHERE user_id=? AND verdict != '' AND problem_id IN (%s)
            GROUP BY problem_id
        """ % marks, tuple([me["id"]] + pid_list)):
            mine[r["problem_id"]] = r["sc"] or 0

    return render_template("oj_contest.html", me=me, contest=dict(c),
                           problems=[dict(p) for p in probs],
                           limits=_oj_limits_map(probs),
                           board_probs=board_probs, full_map=full_map, board=board,
                           mine=mine, hide_problems=hide_problems,
                           rule_name=OJ_RULES.get(c["rule"] or "ioi", "IOI"),
                           state=state, state_class=cls, active="oj_contest")




def oj_contest_rank(cid):
    return redirect(url_for("oj_contest", cid=cid) + "#rank")




def oj_record(sid):
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    s = _oj_record_row(D['get_db'](), sid)
    if not s:
        flash("评测记录不存在", "error")
        return redirect(url_for("oj_home"))
    mine = (s["user_id"] == me["id"]) or _oj_super(me)
    return render_template("oj_record.html", me=me, sub=dict(s),
                           tests=_oj_load_tests(s["detail"]),
                           mine=mine,
                           lang_label=_oj_lang_label(s["lang"]),
                           code_len=len((s["code"] or "").encode("utf-8")),
                           pending=not (s["verdict"] or ""),
                           active="oj")




def oj_record_json(sid):
    me = D['current_user']()
    if not me:
        return jsonify({"ok": False})
    s = _oj_record_row(D['get_db'](), sid)
    if not s:
        return jsonify({"ok": False})
    if s["user_id"] != me["id"] and not _oj_super(me):
        return jsonify({"ok": False})
    return jsonify({
        "ok": True,
        "verdict": s["verdict"] or "",
        "score": s["score"] or 0,
        "full_score": s["full_score"] or 0,
        "passed": s["passed"] or 0,
        "total": s["total"] or 0,
        "time_ms": s["time_ms"] or 0,
        "memory_kb": s["memory_kb"] or 0,
        "tests": _oj_load_tests(s["detail"]),
    })




def oj_records():
    me = D['current_user']()
    if not me:
        flash("请先登录", "error")
        return redirect(url_for("login"))
    db = D['get_db']()

    scope = (request.args.get("scope") or "mine").strip()
    if scope != "all" or not _oj_super(me):
        scope = "mine"

    pid_raw = (request.args.get("pid") or "").strip()
    pid = int(pid_raw) if pid_raw.isdigit() else 0

    uid_raw = (request.args.get("uid") or "").strip()
    uid_filter = 0
    if _oj_super(me) and uid_raw.isdigit():
        uid_filter = int(uid_raw)

    try:
        page = max(1, int(request.args.get("page") or 1))
    except Exception:
        page = 1

    where = ["1=1"]
    args = []
    if scope == "mine":
        where.append("s.user_id=?")
        args.append(me["id"])
    if pid:
        where.append("s.problem_id=?")
        args.append(pid)
    if uid_filter:
        where.append("s.user_id=?")
        args.append(uid_filter)
    w = " AND ".join(where)

    total = db.execute("SELECT COUNT(*) AS c FROM oj_submissions s WHERE " + w,
                       args).fetchone()["c"]
    per = 50
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)

    rows = db.execute("""
        SELECT s.id, s.problem_id, s.user_id, s.verdict, s.score, s.full_score,
               s.time_ms, s.memory_kb, s.passed, s.total, s.lang, s.created_at,
               p.title AS ptitle, u.username AS uname, u.user_no AS uno
        FROM oj_submissions s
        LEFT JOIN oj_problems p ON p.id = s.problem_id
        LEFT JOIN users u ON u.id = s.user_id
        WHERE %s
        ORDER BY s.id DESC LIMIT ? OFFSET ?
    """ % w, args + [per, (page - 1) * per]).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        d["lang_label"] = _oj_lang_label(r["lang"])
        items.append(d)

    focus = None
    if pid:
        focus = db.execute("SELECT id, title FROM oj_problems WHERE id=?", (pid,)).fetchone()

    uid_name = ""
    if uid_filter:
        ur = db.execute("SELECT username FROM users WHERE id=?", (uid_filter,)).fetchone()
        uid_name = ur["username"] if ur else ("用户 " + str(uid_filter))

    return render_template("oj_records.html", me=me, items=items,
                           scope=scope, pid=pid,
                           uid_filter=uid_filter, uid_name=uid_name,
                           focus=dict(focus) if focus else None,
                           total=total, page=page, pages=pages,
                           has_prev=page > 1, has_next=page < pages,
                           active="oj")




def oj_problem(pid):
    me = D['current_user']()
    # 2026-10-31：题目页免登录只读（题面/样例公开，便于分享与收录）；提交仍要求登录。
    if me and _oj_locked(me, pid):
        flash("先通过入门测试才能做题", "error")
        return redirect(url_for("oj_home"))

    db = D['get_db']()
    p = db.execute("SELECT * FROM oj_problems WHERE id=?", (pid,)).fetchone()
    if not p:
        flash("题目不存在", "error")
        return redirect(url_for("oj_home"))

    # 比赛专用题不对外：未登录只能看 visibility='public' 的题，防止赛前泄题
    if not me:
        try:
            _vis = p["visibility"] or "public"
        except Exception:
            _vis = "public"
        if _vis != "public":
            flash("这道题属于比赛，登录后才能查看", "error")
            return redirect(url_for("oj_home"))

    c = db.execute("SELECT * FROM oj_contests WHERE id=?", (p["contest_id"],)).fetchone()

    count_note = ""
    if c:
        _now = time.time()
        if (c["start_at"] or 0) > _now:
            count_note = "这场比赛还没开始，现在提交不会计入成绩"
        elif (c["end_at"] or 0) > 0 and _now > c["end_at"]:
            count_note = "这场比赛已经结束，现在提交不会计入成绩"

    samples = []
    try:
        samples = json.loads(p["samples"] or "[]")
    except Exception:
        samples = []

    if not samples:
        rows = db.execute("""
            SELECT input, expected FROM oj_testcases
            WHERE problem_id=? ORDER BY idx ASC LIMIT 2
        """, (pid,)).fetchall()
        samples = [{"in": r["input"], "out": r["expected"]} for r in rows]

    # 未登录：只给题面 + 样例 + 登录 CTA，不渲染 IDE（避免游客点了提交拿到一坨 302 HTML）
    if not me:
        return render_template("oj_problem_public.html", me=None,
                               problem=dict(p),
                               contest=dict(c) if c else None,
                               limits=_oj_limits(pid, p["time_limit_ms"] or 1000,
                                                 p["memory_limit_mb"] or 256),
                               count_note=count_note, samples=samples, active="oj",
                               og=D['og_context'](
                                   title="MONO OJ · " + (p["title"] or "").strip(),
                                   desc=p["description"] or p["title"],
                                   type_="article",
                                   site_name="MONO OJ",
                               ))

    langs = {}
    try:
        j = _oj_api("/health", timeout=4)
        langs = j.get("languages") or {}
    except Exception:
        pass
    if not langs:
        langs = {"c++98": "C++98", "c++11": "C++11", "c++14": "C++14",
                 "c++17": "C++17", "c++20": "C++20"}

    last = db.execute("""
        SELECT id, verdict, time_ms, memory_kb, passed, total, created_at, code
        FROM oj_submissions WHERE problem_id=? AND user_id=?
        ORDER BY id DESC LIMIT 1
    """, (pid, me["id"])).fetchone()

    best = db.execute("""
        SELECT MAX(score) AS sc, MAX(full_score) AS fs, COUNT(*) AS n
        FROM oj_submissions WHERE problem_id=? AND user_id=? AND verdict != ''
    """, (pid, me["id"])).fetchone()
    best_score = (best["sc"] or 0) if best else 0
    best_full = (best["fs"] or 0) if best else 0
    if best_full > 0 and best_score >= best_full:
        my_state, my_state_cls = "已通过", "done"
    elif best_score > 0:
        my_state, my_state_cls = "已得 %d 分" % best_score, "tried"
    elif best and best["n"]:
        my_state, my_state_cls = "还没得分", "tried"
    else:
        my_state, my_state_cls = "", ""

    return render_template("oj_problem.html", me=me, problem=dict(p),
                           contest=dict(c) if c else None,
                           limits=_oj_limits(pid, p["time_limit_ms"] or 1000,
                                             p["memory_limit_mb"] or 256),
                           my_state=my_state, my_state_cls=my_state_cls,
                           count_note=count_note,
                           samples=samples, langs=langs,
                           last=dict(last) if last else None, active="oj",
                           og=D['og_context'](
                               title="MONO OJ · " + (p["title"] or "").strip(),
                               desc=p["description"] or p["title"],
                               type_="article",
                               site_name="MONO OJ",
                           ))




def oj_submit():
    me = D['current_user']()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})

    code = (request.form.get("code") or "").strip()
    pid = request.form.get("problem_id") or ""
    std = (request.form.get("std") or "c++14").lower()
    o2 = 1 if request.form.get("o2") else 0

    if not code:
        return jsonify({"ok": False, "msg": "代码不能为空"})
    if len(code) > 65536:
        return jsonify({"ok": False, "msg": "代码太长了（上限 64KB）"})
    if not pid.isdigit():
        return jsonify({"ok": False, "msg": "题目编号不对"})
    if _oj_locked(me, pid):
        return jsonify({"ok": False, "msg": "先通过入门测试才能提交"})

    db = D['get_db']()
    p = db.execute("SELECT * FROM oj_problems WHERE id=?", (int(pid),)).fetchone()
    if not p:
        return jsonify({"ok": False, "msg": "题目不存在"})

    if not D['repeat_guard']("oj_submit", [pid, std, o2, code], window=10.0, uid=me['id']):
        return jsonify({"ok": False, "msg": "同一份代码刚刚已经提交过了，等判完再交"})

    # ★ 比赛相关的时间闸门
    contest_id = p["contest_id"] or 0
    if contest_id:
        c = db.execute("SELECT start_at, end_at FROM oj_contests WHERE id=?",
                       (contest_id,)).fetchone()
        if c:
            now_ts = time.time()
            if now_ts < (c["start_at"] or 0):
                return jsonify({"ok": False, "msg": "比赛还没开始，不能提交"})
            if now_ts > (c["end_at"] or 0):
                # 赛后允许练习提交，但记成 contest_id=0（不进榜单）
                contest_id = 0

    cur = db.execute("""
        INSERT INTO oj_submissions (problem_id, contest_id, user_id, code,
                                    verdict, time_ms, memory_kb, passed, total,
                                    created_at, task_id, lang)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (int(pid), contest_id, me["id"], code,
          "", 0, 0, 0, 0, time.time(), "", std + ("|o2" if o2 else "")))
    db.commit()
    sub_id = cur.lastrowid

    _oj_ensure_sweeper()
    threading.Thread(target=_oj_kick,
                     args=(sub_id, code, int(pid), std, bool(o2), me["id"]),
                     daemon=True).start()

    return jsonify({"ok": True, "sub_id": sub_id})




def oj_test():
    me = D['current_user']()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})

    code = (request.form.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "msg": "代码不能为空"})
    if len(code) > 65536:
        return jsonify({"ok": False, "msg": "代码太长了"})

    try:
        r = _oj_api("/judge", {"code": code, "problem_id": OJ_TEST_PROBLEM}, timeout=30)
    except Exception as e:
        return jsonify({"ok": False, "msg": "判题机没响应：" + str(e)[:100]})

    tid = r.get("task_id")
    if not tid:
        return jsonify({"ok": False, "msg": r.get("msg") or "提交失败"})

    with _OJ_SUB_LOCK:
        _OJ_SUB_TASKS[tid] = {"sub_id": None, "pid": OJ_TEST_PROBLEM,
                              "uid": me["id"], "std": "c++14", "o2": True}
        _OJ_TRACKING.add(tid)
    _oj_ensure_sweeper()
    threading.Thread(target=_oj_track, args=(tid,), daemon=True).start()

    return jsonify({"ok": True, "task_id": tid})






def oj_test_status(tid):
    me = D['current_user']()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})

    if not re.match(r"^[a-f0-9]{8,32}$", tid or ""):
        return jsonify({"ok": False, "msg": "无效的任务号"})

    try:
        r = _oj_api("/status/" + tid, timeout=15)
    except Exception as e:
        return jsonify({"ok": False, "msg": "查询失败：" + str(e)[:80]})

    task = r.get("task") or {}

    if task.get("status") == "done":
        with _OJ_SUB_LOCK:
            info = _OJ_SUB_TASKS.get(tid)
        if info:
            _oj_write_result(info.get("sub_id"), task, info.get("uid"))

    return jsonify({"ok": True, "task": task})



def _oj_lang_label(lang):
    parts = (lang or "").split("|")
    name = (parts[0] or "c++17").upper()
    return name + (" · O2" if len(parts) > 1 and parts[1] == "o2" else " · 无优化")


def _oj_load_tests(raw):
    try:
        tests = json.loads(raw or "[]")
    except Exception:
        return []
    return tests if isinstance(tests, list) else []


def _oj_record_row(db, sid):
    return db.execute("""
        SELECT s.*, p.title AS problem_title, p.contest_id AS p_contest,
               u.username AS uname, u.user_no AS uno
        FROM oj_submissions s
        LEFT JOIN oj_problems p ON p.id = s.problem_id
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.id=?
    """, (sid,)).fetchone()


