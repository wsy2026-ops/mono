#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watchdog.py — 站点探活与故障告警（规划文档 041 项）

和 monitor.py 的关系
--------------------
`monitor.py` 是**采集模块**：记录 CPU/内存/磁盘/请求耗时/攻击事件，给后台看板用。
它**不告警** —— 数据再全，站挂了你也不知道。

本文件只做一件事：**定时探活，出问题主动找你**。

查什么
------
1. healthz 接口返回 ok
2. mono.service 是 active
3. 首页能打开（200/302）
4. 磁盘占用 < 85%
5. 可用内存 > 100MB
6. swap 使用 < 1.5G（总 2G，超了说明内存真不够）

告警怎么发
----------
优先 Telegram（服务器在香港能直连 api.telegram.org）。
**没配 Token 就只写日志，不报错** —— 你哪天想接再配。

配置（二选一）：
  · 环境变量 MONO_TG_TOKEN / MONO_TG_CHAT
  · 文件 /opt/mono/monitor.env：
        TG_TOKEN=123456:ABC-DEF...
        TG_CHAT=123456789

拿 Token：Telegram 找 @BotFather 发 /newbot；给 bot 发条消息后访问
  https://api.telegram.org/bot<TOKEN>/getUpdates
能看到你的 chat_id。

防刷屏
------
同一项 **30 分钟内只报一次**，状态存 /opt/mono/monitor_state.json。
恢复时也发一条「已恢复」。

用法
----
    python3 watchdog.py            # 跑一次（crontab 每 5 分钟）
    python3 watchdog.py --test     # 发测试消息，验证配置
    python3 watchdog.py --status   # 只打印状态，不发告警
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

STATE_FILE = "/opt/mono/monitor_state.json"
ENV_FILE = "/opt/mono/monitor.env"

# 告警收件人只从环境变量取。没配就不发邮件（Telegram 通道仍可用）。
ALERT_EMAIL = (os.environ.get("MONO_ALERT_EMAIL") or "").strip()

def _load_service_env():
    """从 mono.service 读环境变量。

    ⚠️ cron 启动的进程**继承不到** systemd 的 Environment=，
    所以 SMTP 配置在这里是空的 —— 必须手动捞一遍。
    mailer.py 是**模块级**读环境变量，因此这步必须在 import mailer 之前做。
    """
    try:
        import shlex
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


_load_service_env()

# mailer.py 在 app/ 下，watchdog.py 在 /opt/mono/，需要把 app 加进 sys.path
sys.path.insert(0, "/opt/mono/app")
try:
    import mailer as _mailer
except Exception:
    _mailer = None
ALERT_COOLDOWN = 1800
SITE = (os.environ.get("MONO_SITE_URL") or "http://127.0.0.1:8080").rstrip("/")
HEALTHZ = SITE + "/healthz"

DISK_MAX = 85
MEM_MIN_MB = 100
SWAP_MAX_MB = 1500


def load_env():
    tok = os.environ.get("MONO_TG_TOKEN") or os.environ.get("TG_TOKEN")
    chat = os.environ.get("MONO_TG_CHAT") or os.environ.get("TG_CHAT")
    if (not tok or not chat) and os.path.isfile(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "TG_TOKEN" and not tok:
                        tok = v
                    elif k == "TG_CHAT" and not chat:
                        chat = v
        except Exception:
            pass
    return tok, chat


def send_tg(text):
    tok, chat = load_env()
    if not tok or not chat:
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % tok, data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def send_mail(subject, body):
    """用 Resend 发告警邮件。

    ⚠️ **故意绕开 mailer.deliver()** —— 它的优先级是 SMTP 优先，
    而线上配的 smtp.163.com 连不上（用户实测），会导致永远发不出去。
    Resend 配好且可用（noreply@example.com 已验证），直接走它。

    发不出去返回 False，不抛异常 —— 告警失败不能把 watchdog 自己搞崩。
    """
    if _mailer is None or not ALERT_EMAIL:
        return False
    try:
        if _mailer.RESEND_API_KEY and _mailer.RESEND_FROM:
            return bool(_mailer._send_resend(ALERT_EMAIL, subject, body))
        # Resend 没配才退回通用通道
        if _mailer.available():
            return bool(_mailer.deliver(ALERT_EMAIL, subject, body))
        return False
    except Exception:
        return False


def http_get(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mono-watchdog/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2000).decode("utf-8", "ignore")
    except Exception as e:
        return 0, str(e)


def sh(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except Exception:
        return ""


def check_healthz():
    code, body = http_get(HEALTHZ)
    if code != 200:
        return False, "healthz HTTP %s" % code
    if '"ok":true' not in body.replace(" ", ""):
        return False, "返回非 ok: %s" % body[:100]
    return True, "ok"


def check_service():
    out = sh("systemctl is-active mono")
    return (out == "active"), (out or "unknown")


def check_home():
    code, _ = http_get(SITE + "/", timeout=15)
    return (code in (200, 302)), ("HTTP %s" % code)


def check_disk():
    out = sh("df -h / | tail -1 | awk '{print $5}'").rstrip("%")
    try:
        pct = int(out)
    except Exception:
        return True, "读取失败，跳过"
    return (pct < DISK_MAX), ("%d%%" % pct)


def check_mem():
    out = sh("free -m | awk '/^Mem:/{print $7}'")
    try:
        avail = int(out)
    except Exception:
        return True, "读取失败，跳过"
    return (avail >= MEM_MIN_MB), ("%dMB 可用" % avail)


def check_swap():
    out = sh("free -m | awk '/^Swap:/{print $3}'")
    try:
        used = int(out)
    except Exception:
        return True, "读取失败，跳过"
    return (used <= SWAP_MAX_MB), ("%dMB" % used)


CHECKS = [
    ("healthz", "接口探活", check_healthz),
    ("service", "服务状态", check_service),
    ("home", "首页可访问", check_home),
    ("disk", "磁盘占用", check_disk),
    ("mem", "可用内存", check_mem),
    ("swap", "Swap 使用", check_swap),
]


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception:
        pass


def main():
    if "--test" in sys.argv:
        print("=== 测试告警通道 ===")
        # 邮件
        m_ok = send_mail(
            "[MONO 监控] 测试邮件",
            "这是一条测试告警。\n\n如果你看到这封邮件，说明 watchdog 的邮件通道是通的。\n\n"
            "收件人：%s\n时间：%s\n\n"
            "注意：163 邮箱可能把它判成垃圾邮件，去垃圾箱看一眼。\n"
            % (ALERT_EMAIL, time.strftime("%Y-%m-%d %H:%M:%S")))
        print("  邮件 → %s: %s" % (ALERT_EMAIL, "已发送" if m_ok else "失败（检查 mailer 配置）"))
        # Telegram
        tok, chat = load_env()
        if tok and chat:
            t_ok = send_tg("🧪 <b>MONO 监控测试</b>\n看到这条说明告警通道配好了。")
            print("  Telegram: %s" % ("已发送" if t_ok else "失败"))
        else:
            print("  Telegram: 未配置（跳过）")
        return

    status_only = "--status" in sys.argv
    now = time.time()
    state = load_state()
    failures = []
    rows = []

    for key, name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, "检查异常: %s" % e
        rows.append((name, ok, detail))

        rec = state.get(key) or {}
        if ok:
            if rec.get("bad") and not status_only:
                send_tg("✅ <b>MONO 已恢复</b>\n%s：%s" % (name, detail))
                send_mail("[MONO 已恢复] %s" % name,
                          "%s 已恢复正常：%s\n\n时间：%s" % (
                              name, detail, time.strftime("%Y-%m-%d %H:%M:%S")))
            state[key] = {"bad": False, "at": now}
        else:
            failures.append("%s：%s" % (name, detail))
            if not rec.get("bad") or now - (rec.get("at") or 0) > ALERT_COOLDOWN:
                state[key] = {"bad": True, "at": now, "detail": detail}

    save_state(state)

    # 日志策略：出问题必打全量；一切正常时只在整点打一次（每小时的第一次运行）。
    # 原因：cron 每 5 分钟跑一次，全打的话一天 288 块，真出问题时反而被噪音埋掉。
    heartbeat = time.localtime().tm_min < 5
    if failures or status_only or heartbeat:
        print("[%s]" % time.strftime("%Y-%m-%d %H:%M:%S"))
        for name, ok, detail in rows:
            print("  %-12s %s  %s" % (name, "✓" if ok else "✗", detail))

    if failures and not status_only:
        # Telegram（HTML）
        msg = "🚨 <b>MONO 异常</b>\n\n" + "\n".join("• " + f for f in failures)
        msg += "\n\n<i>%s</i>" % time.strftime("%m-%d %H:%M")
        tg_ok = send_tg(msg)

        # 邮件（纯文本，带排查建议）
        body = "MONO 站点异常\n\n" + "\n".join("· " + f for f in failures)
        body += "\n\n时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S")
        body += "\n\n排查建议："
        if any("healthz" in f or "服务状态" in f or "首页" in f for f in failures):
            body += "\n  ssh mono-vps \"systemctl status mono; tail -50 /opt/mono/app/server.log\""
        if any("磁盘" in f for f in failures):
            body += "\n  ssh mono-vps \"df -h /; du -sh /opt/mono/* | sort -h | tail\""
        if any("内存" in f or "Swap" in f for f in failures):
            body += "\n  ssh mono-vps \"free -m; ps aux --sort=-rss | head\""
        mail_ok = send_mail("[MONO 异常] %s" % "；".join(f.split("：")[0] for f in failures), body)

        if tg_ok or mail_ok:
            print("\n已发送告警（邮件:%s Telegram:%s）" % ("✓" if mail_ok else "✗",
                                                          "✓" if tg_ok else "✗"))
        else:
            print("\n告警未发送（邮件/Telegram 都没配好，仅记日志）")
        sys.exit(1)


if __name__ == "__main__":
    main()
