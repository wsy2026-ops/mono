#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""084 每日运营报表：每天早上一封邮件，写清楚昨天到底发生了什么。

设计原则（跟这份文档里其他 28 天的项目不一样的地方）：
- **不建数仓**。15MB 的库，8 条聚合查询直接查主库，几十毫秒，不会影响线上。
- **不建调度系统**。crontab 一行，跟备份、watchdog 平级。
- **只写结论，不堆图**。运营报表的价值是「读完知道该干什么」，
  不是「给你 12 张折线图自己找规律」。

用法：
    python daily_report.py                  # 发昨天的报表
    python daily_report.py --dry-run        # 只打印，不发邮件（本地看效果）
    python daily_report.py --date 2026-10-31
    python daily_report.py --days 1         # 只发最近 1 天（默认就是 1）

cron（每天早上 8:00）：
    0 8 * * * cd /opt/mono/app && /opt/mono/venv/bin/python daily_report.py \
        >> /opt/mono/app/logs/daily_report.log 2>&1
"""

import argparse
import datetime
import os
import shlex
import sqlite3
import subprocess
import sys
import time

DB_PATH = os.environ.get("MONO_DB") or "/opt/mono/app/data.db"
# 收件人只从环境变量取。没配就不发 —— 开源版不该带任何人的私人邮箱。
TO_EMAIL = (os.environ.get("MONO_REPORT_TO") or "").strip()
RESEND_ENDPOINT = "https://api.resend.com/emails"
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ─────────────────────────── 基础工具 ───────────────────────────

def load_service_env():
    """从 mono.service 捞环境变量。

    ⚠️ cron 启动的进程继承不到 systemd 的 Environment=，
    RESEND 的 key 必须手动补，否则邮件永远发不出去（watchdog 踩过这个坑）。
    """
    try:
        out = subprocess.run(
            ["systemctl", "show", "mono", "-p", "Environment", "--value"],
            capture_output=True, text=True, timeout=8).stdout.strip()
        for kv in shlex.split(out):
            if "=" in kv:
                k, v = kv.split("=", 1)
                if k and v:
                    os.environ.setdefault(k, v)
    except Exception:
        pass


def conn():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def q(db, sql, args=(), default=0):
    """执行查询，出错返回 default —— 报表不能因为某张表不存在就整个挂掉。"""
    try:
        r = db.execute(sql, args).fetchone()
        if r is None:
            return default
        v = r[0]
        return default if v is None else v
    except Exception:
        return default


def rows(db, sql, args=()):
    try:
        return db.execute(sql, args).fetchall()
    except Exception:
        return []


def pct(now, prev):
    """环比。prev 为 0 时不说百分比（除以 0 没意义，说「新增」）。"""
    if not prev:
        return "新增" if now else "—"
    d = (now - prev) / float(prev) * 100.0
    if abs(d) < 1:
        return "持平"
    return "%+.0f%%" % d


def day_bounds(day):
    """本地时区的一天：00:00 ~ 次日 00:00。"""
    start = datetime.datetime.combine(day, datetime.time.min)
    return start.timestamp(), (start + datetime.timedelta(days=1)).timestamp()


# ─────────────────────────── 取数 ───────────────────────────

def collect(day):
    db = conn()
    try:
        s, e = day_bounds(day)
        # 前 7 天（不含当天）的日均，用来做基线
        p_start = s - 7 * 86400

        def avg7(sql, key=None):
            """近 7 天日均（用 7 天总和 / 7，缺数据的日子算 0 —— 这才叫日均）。"""
            v = q(db, sql, (p_start, s), 0)
            return float(v) / 7.0

        m = {"date": day.isoformat(), "weekday": WEEKDAYS[day.weekday()]}

        # 活跃用户：有任意行为记录的独立用户
        m["dau"] = q(db, "SELECT COUNT(DISTINCT user_id) FROM user_actions "
                         "WHERE created_at>=? AND created_at<? AND user_id IS NOT NULL", (s, e))
        # ⚠️ 日均活跃必须「先按天去重、再取平均」。
        # 直接 COUNT(DISTINCT user_id) 跨 7 天再 ÷7 是错的 ——
        # 那算的是「7 天里一共来过多少人 ÷7」，会把 8.3 算成 3.4，
        # 结论就从「腰斩」变成「持平」。（2026-11-01 踩）
        m["dau_avg"] = float(q(
            db,
            "SELECT COALESCE(SUM(c), 0) FROM ("
            "  SELECT COUNT(DISTINCT user_id) c FROM user_actions "
            "  WHERE created_at>=? AND created_at<? AND user_id IS NOT NULL "
            "  GROUP BY CAST((created_at + 28800) / 86400 AS INTEGER))",
            (p_start, s), 0)) / 7.0

        # 新增注册
        m["reg"] = q(db, "SELECT COUNT(*) FROM user_actions "
                         "WHERE action='register' AND created_at>=? AND created_at<?", (s, e))
        m["reg_avg"] = avg7("SELECT COUNT(*) FROM user_actions "
                            "WHERE action='register' AND created_at>=? AND created_at<?")

        # 发帖 / 评论（用业务表，比埋点准）
        m["posts"] = q(db, "SELECT COUNT(*) FROM posts WHERE created_at>=? AND created_at<? "
                           "AND COALESCE(is_deleted,0)=0 AND COALESCE(recycle,0)=0", (s, e))
        m["posts_avg"] = avg7("SELECT COUNT(*) FROM posts WHERE created_at>=? AND created_at<? "
                              "AND COALESCE(is_deleted,0)=0 AND COALESCE(recycle,0)=0")
        m["comments"] = q(db, "SELECT COUNT(*) FROM post_comments WHERE created_at>=? "
                              "AND created_at<? AND COALESCE(is_deleted,0)=0", (s, e))
        m["comments_avg"] = avg7("SELECT COUNT(*) FROM post_comments WHERE created_at>=? "
                                 "AND created_at<? AND COALESCE(is_deleted,0)=0")

        # 游戏局数
        m["games"] = q(db, "SELECT COUNT(*) FROM game_replays WHERE created_at>=? AND created_at<?",
                       (s, e))
        m["games_avg"] = avg7("SELECT COUNT(*) FROM game_replays WHERE created_at>=? AND created_at<?")
        m["game_list"] = [(r["game"], r["n"]) for r in rows(
            db, "SELECT game, COUNT(*) n FROM game_replays WHERE created_at>=? AND created_at<? "
                "GROUP BY game ORDER BY n DESC LIMIT 6", (s, e))]

        # 积分（排除 exp，那是经验不是积分 —— 对账踩过的坑）
        m["pts_in"] = q(db, "SELECT COALESCE(SUM(delta),0) FROM points_log "
                            "WHERE delta>0 AND source_type!='exp' AND created_at>=? AND created_at<?",
                        (s, e))
        m["pts_out"] = q(db, "SELECT COALESCE(SUM(delta),0) FROM points_log "
                             "WHERE delta<0 AND source_type!='exp' AND created_at>=? AND created_at<?",
                         (s, e))
        m["pts_rows"] = q(db, "SELECT COUNT(*) FROM points_log WHERE source_type!='exp' "
                              "AND created_at>=? AND created_at<?", (s, e))

        # 前端报错 / 攻击拦截
        m["errors"] = q(db, "SELECT COUNT(*) FROM user_actions WHERE action='js_error' "
                            "AND created_at>=? AND created_at<?", (s, e))
        m["errors_avg"] = avg7("SELECT COUNT(*) FROM user_actions WHERE action='js_error' "
                               "AND created_at>=? AND created_at<?")
        m["attacks"] = q(db, "SELECT COALESCE(SUM(count),0) FROM attack_events "
                             "WHERE ts>=? AND ts<?", (s, e))

        # 昨日热门帖子（按新增评论 + 点赞）
        m["hot_posts"] = [(r["title"], r["likes"], r["cm"]) for r in rows(
            db,
            "SELECT p.title, COALESCE(p.likes,0) likes, "
            "  (SELECT COUNT(*) FROM post_comments c WHERE c.post_id=p.id AND c.is_deleted=0) cm "
            "FROM posts p WHERE COALESCE(p.is_deleted,0)=0 AND COALESCE(p.recycle,0)=0 "
            "ORDER BY cm DESC, likes DESC LIMIT 3")]

        # 昨日新用户
        m["new_users"] = [r["username"] for r in rows(
            db, "SELECT username FROM users WHERE created_at>=? AND created_at<? "
                "ORDER BY created_at LIMIT 10", (s, e))]

        # 系统状态（最新一条采样）
        smp = rows(db, "SELECT * FROM monitor_samples ORDER BY ts DESC LIMIT 1")
        if smp:
            r = smp[0]
            # 采样列可能是 NULL（刚写入那一瞬间），一律兜成 0 —— 报表不能因此崩掉
            du = r["disk_used_gb"] or 0
            dt = r["disk_total_gb"] or 0
            m["disk_pct"] = (du / dt * 100.0) if dt else 0
            m["mem_used"] = r["mem_used_mb"] or 0
            m["mem_total"] = r["mem_total_mb"] or 0
            m["swap_used"] = r["swap_used_mb"] or 0
            m["cpu"] = r["cpu_pct"] or 0
        else:
            m.update({"disk_pct": 0, "mem_used": 0, "mem_total": 0, "swap_used": 0, "cpu": 0})

        # 最后兜一道：所有数值字段不许是 None（模板里 %.0f / %d 遇到 None 会直接炸）
        for k in ("dau", "dau_avg", "reg", "reg_avg", "posts", "posts_avg",
                  "comments", "comments_avg", "games", "games_avg",
                  "pts_in", "pts_out", "pts_rows", "errors", "errors_avg",
                  "attacks", "disk_pct", "mem_used", "mem_total", "swap_used", "cpu"):
            if m.get(k) is None:
                m[k] = 0
        return m
    finally:
        db.close()


# ─────────────────────────── 结论 ───────────────────────────

def build_insights(m):
    """把数字翻译成「该干什么」。这是整份报表唯一值钱的部分。"""
    out = []

    # 1) 活跃度趋势
    dau, avg = m["dau"], m["dau_avg"]
    if dau == 0:
        out.append("昨天**一个人都没来**。先看是不是站挂了（watchdog 没报就说明是没人来，不是故障）。")
    elif avg and dau >= avg * 1.5:
        out.append("活跃 %d 人，是近 7 天日均（%.1f）的 %.1f 倍 —— 有波峰，想想昨天发生了什么，能复制就复制。"
                   % (dau, avg, dau / avg))
    elif avg and dau <= avg * 0.5:
        out.append("活跃 %d 人，只有近 7 天日均（%.1f）的一半 —— 在走下坡，去群里问一句比猜要准。" % (dau, avg))
    else:
        out.append("活跃 %d 人，跟近 7 天日均（%.1f）差不多。" % (dau, avg))

    # 2) 内容产出
    if m["posts"] == 0 and m["comments"] == 0:
        out.append("昨天**没有新帖也没有评论**。内容是这类社区唯一的问题 —— 没有内容，其他功能做得再好也没人来第二次。")
    elif m["posts"] == 0:
        out.append("昨天没有新帖，但有 %d 条评论 —— 有人在聊，说明老帖还活着。" % m["comments"])
    else:
        out.append("昨天新增 %d 帖 / %d 条评论。" % (m["posts"], m["comments"]))

    # 3) 新用户
    if m["reg"] == 0:
        out.append("没有新注册。如果连续一周都是 0，说明外面的入口（APK / 分享链接 / 群）没有把人带进来。")
    else:
        names = "、".join(m["new_users"][:5])
        out.append("新注册 %d 人：%s。**去欢迎一下** —— 第一次进来没人理，基本不会来第二次。" % (m["reg"], names))

    # 4) 报错
    if m["errors"] and m["errors_avg"] and m["errors"] > max(5, m["errors_avg"] * 2):
        out.append("⚠️ 前端报错 %d 次，是近 7 天日均（%.1f）的 %.1f 倍 —— 去 /admin/metrics 看是哪个页面。"
                   % (m["errors"], m["errors_avg"], m["errors"] / m["errors_avg"]))

    # 5) 资源
    if m["disk_pct"] >= 80:
        out.append("⚠️ 磁盘已用 %.0f%% —— 该清理了（备份目录是大头）。" % m["disk_pct"])
    if m["mem_total"] and m["mem_used"] / m["mem_total"] >= 0.85:
        out.append("⚠️ 内存已用 %.0f%% —— 考虑升级或查泄漏。" % (m["mem_used"] / m["mem_total"] * 100))

    # 6) 游戏
    if m["game_list"]:
        top = m["game_list"][0]
        out.append("游戏里 %s 最热（%d 局）。" % (top[0], top[1]))
    elif m["dau"] > 0:
        out.append("昨天没人玩游戏 —— 游戏中心的入口可能不够显眼。")

    return out


# ─────────────────────────── 渲染 ───────────────────────────

def render_text(m, insights):
    L = []
    L.append("MONO 运营日报 · %s（%s）" % (m["date"], m["weekday"]))
    L.append("=" * 46)
    L.append("")
    L.append("%-12s %8s %10s %8s" % ("指标", "昨日", "近7日均", "环比"))
    L.append("-" * 46)
    for label, key in (("活跃用户", "dau"), ("新增注册", "reg"), ("发帖", "posts"),
                       ("评论", "comments"), ("游戏局数", "games"), ("前端报错", "errors")):
        L.append("%-12s %8d %10.1f %8s" % (label, m[key], m.get(key + "_avg", 0),
                                           pct(m[key], m.get(key + "_avg", 0))))
    L.append("")
    L.append("积分：发放 +%d / 消耗 %d（%d 笔）" % (m["pts_in"], m["pts_out"], m["pts_rows"]))
    L.append("攻击拦截：%d 次" % m["attacks"])
    L.append("系统：CPU %.0f%% · 内存 %d/%d MB · Swap %d MB · 磁盘 %.0f%%"
             % (m["cpu"], m["mem_used"], m["mem_total"], m["swap_used"], m["disk_pct"]))
    L.append("")
    L.append("【结论】")
    for i, s in enumerate(insights, 1):
        L.append("%d. %s" % (i, s.replace("**", "")))
    if m["hot_posts"]:
        L.append("")
        L.append("【热门帖子】")
        for t, likes, cm in m["hot_posts"]:
            L.append("  · %s（赞 %d / 评 %d）" % (t, likes, cm))
    L.append("")
    L.append("—— 数据来自 %s，报表脚本 daily_report.py" % os.path.basename(DB_PATH))
    return "\n".join(L)


def render_html(m, insights):
    # 颜色按「这个指标涨是好事还是坏事」判断，不是简单看正负号。
    # 活跃/发帖/评论/游戏：涨=好（绿）跌=差（红）
    # 前端报错：反过来 —— 报错减少是好事
    METRICS = (("活跃用户", "dau", "up"), ("新增注册", "reg", "up"),
               ("发帖", "posts", "up"), ("评论", "comments", "up"),
               ("游戏局数", "games", "up"), ("前端报错", "errors", "down"))

    def cell_color(now, avg, better):
        if not avg:
            return "#8a8a8a"
        if abs(now - avg) <= avg * 0.01:
            return "#8a8a8a"
        good = (now > avg) if better == "up" else (now < avg)
        return "#1e8449" if good else "#c0392b"

    rows_html = []
    for label, key, better in METRICS:
        avg = m.get(key + "_avg", 0)
        p = pct(m[key], avg)
        rows_html.append(
            "<tr>"
            "<td style='padding:8px 10px;border-bottom:1px solid #eee;'>%s</td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;font-weight:700;'>%d</td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;color:#8a8a8a;'>%.1f</td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;color:%s;'>%s</td>"
            "</tr>" % (label, m[key], avg, cell_color(m[key], avg, better), p))

    ins_html = "".join(
        "<li style='margin:0 0 8px;line-height:1.65;'>%s</li>"
        % s.replace("**", "") for s in insights)

    hot_html = ""
    if m["hot_posts"]:
        hot_html = (
            "<h3 style='margin:22px 0 10px;font-size:15px;color:#111;'>热门帖子</h3>"
            "<ul style='margin:0;padding-left:20px;color:#333;line-height:1.8;font-size:13.5px;'>"
            + "".join("<li>%s <span style='color:#8a8a8a;'>（赞 %d / 评 %d）</span></li>" % (t, l, c)
                      for t, l, c in m["hot_posts"])
            + "</ul>")

    return """<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
     max-width:640px;margin:0 auto;padding:24px 20px;background:#fff;color:#111;">
  <div style="border-bottom:2px solid #111;padding-bottom:12px;margin-bottom:18px;">
    <div style="font-size:19px;font-weight:700;">MONO 运营日报</div>
    <div style="font-size:13px;color:#8a8a8a;margin-top:4px;">%s · %s</div>
  </div>

  <table style="width:100%%;border-collapse:collapse;font-size:13.5px;">
    <tr style="background:#fafafa;">
      <th style="padding:8px 10px;text-align:left;font-weight:600;color:#666;">指标</th>
      <th style="padding:8px 10px;text-align:right;font-weight:600;color:#666;">昨日</th>
      <th style="padding:8px 10px;text-align:right;font-weight:600;color:#666;">近7日均</th>
      <th style="padding:8px 10px;text-align:right;font-weight:600;color:#666;">环比</th>
    </tr>
    %s
  </table>

  <div style="margin:16px 0;padding:12px 14px;background:#fafafa;border-radius:8px;font-size:13px;color:#333;line-height:1.9;">
    积分：发放 <b>+%d</b> / 消耗 <b>%d</b>（%d 笔）<br>
    攻击拦截：<b>%d</b> 次<br>
    系统：CPU %.0f%% · 内存 %d/%d MB · Swap %d MB · 磁盘 %.0f%%
  </div>

  <h3 style="margin:22px 0 10px;font-size:15px;">结论</h3>
  <ol style="margin:0;padding-left:20px;font-size:13.5px;color:#222;">%s</ol>

  %s

  <div style="margin-top:26px;padding-top:12px;border-top:1px solid #eee;font-size:11.5px;color:#aaa;">
    由 daily_report.py 自动生成 · 数据源 %s
  </div>
</div>""" % (m["date"], m["weekday"], "".join(rows_html),
             m["pts_in"], m["pts_out"], m["pts_rows"], m["attacks"],
             m["cpu"], m["mem_used"], m["mem_total"], m["swap_used"], m["disk_pct"],
             ins_html, hot_html, os.path.basename(DB_PATH))


# ─────────────────────────── 发送 ───────────────────────────

def send(subject, text, html, to=None):
    """Resend 直发（带 html）。失败返回 False，不抛异常。

    ⚠️ 必须用 requests（或至少带正常 User-Agent）：
    Resend 的 API 在 Cloudflare 后面，**默认 UA `Python-urllib/3.x` 会被拦，
    返回 HTTP 403 + `error code: 1010`**（2026-11-01 踩）。
    """
    key = os.environ.get("MONO_RESEND_KEY") or os.environ.get("RESEND_API_KEY")
    frm = os.environ.get("MONO_RESEND_FROM") or os.environ.get("RESEND_FROM")
    if not key or not frm:
        sys.stderr.write("  (!) 没有 RESEND key，跳过发送\n")
        return False
    payload = {
        "from": frm, "to": [to or TO_EMAIL], "subject": subject,
        "text": text, "html": html,
    }
    try:
        import requests
        r = requests.post(RESEND_ENDPOINT, json=payload, timeout=20,
                          headers={"Authorization": "Bearer " + key})
        if r.status_code not in (200, 201):
            sys.stderr.write("  (!) Resend 返回 %s: %s\n" % (r.status_code, r.text[:200]))
            return False
        return True
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write("  (!) 发送失败: %s\n" % e)
        return False

    # 没装 requests 的兜底：urllib + 正常 UA
    import json
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        RESEND_ENDPOINT, data=body, method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "User-Agent": "mono-daily-report/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status in (200, 201)
    except Exception as e:
        sys.stderr.write("  (!) 发送失败: %s\n" % e)
        return False


def main():
    ap = argparse.ArgumentParser(description="MONO 每日运营报表")
    ap.add_argument("--date", help="指定日期 YYYY-MM-DD（默认昨天）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发邮件")
    ap.add_argument("--to", help="收件人（默认 %s）" % TO_EMAIL)
    args = ap.parse_args()
    to = args.to or TO_EMAIL

    load_service_env()

    if args.date:
        day = datetime.date.fromisoformat(args.date)
    else:
        day = datetime.date.today() - datetime.timedelta(days=1)

    if not os.path.exists(DB_PATH):
        sys.stderr.write("找不到数据库: %s\n" % DB_PATH)
        return 1

    m = collect(day)
    insights = build_insights(m)
    text = render_text(m, insights)
    html = render_html(m, insights)

    if args.dry_run:
        print(text)
        return 0

    subject = "MONO 日报 %s（%s）· 活跃 %d · 发帖 %d · 评论 %d" % (
        m["date"], m["weekday"], m["dau"], m["posts"], m["comments"])
    ok = send(subject, text, html, to=to)
    print("%s 日报 %s → %s" % ("已发送" if ok else "发送失败", m["date"], to))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
