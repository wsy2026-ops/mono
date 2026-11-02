"""MONO 社区 · 访问防护策略配置工具

用途：
    mono_guard.py 的所有阈值都支持用 site_config 的 guard_* 键覆盖，
    但直接手写 SQL 容易出错（键名拼错、值写错、忘了重启）。
    本脚本把这件事变成一条安全命令。

最常用的场景：
    本项目架构是 **Cloudflare → Nginx → waitress**，共 2 层，
    所以 TRUSTED_PROXY_DEPTH 必须设为 **2**（不是 1）。
    设错的后果：所有请求的 remote_addr 都会变成 Cloudflare 的 IP，
    导致全站用户共用同一批限流桶（注册/登录/Socket 连接都会被误伤）。
        python guard_config.py proxy 2

用法：
    python guard_config.py list                # 显示当前生效策略与来源
    python guard_config.py proxy               # 查看当前代理层数（只读）
    python guard_config.py proxy 2             # CF + Nginx（本项目架构，必做）
    python guard_config.py proxy 1             # 只有 CF（0=直连，1=CF，2=CF+Nginx）
    python guard_config.py set GLOBAL_PER_MIN 5000
    python guard_config.py unset GLOBAL_PER_MIN   # 恢复代码默认值
    python guard_config.py check               # 自检：当前配置是否与部署方式匹配

改完需要重启服务才生效：systemctl restart mono
"""
import argparse
import os
import sqlite3
import sys

PREFIX = "guard_"


def _resolve_dirs(args):
    code_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.abspath(args.data_dir or os.environ.get("MONO_DATA_DIR") or code_dir)
    return data_dir, os.path.join(data_dir, "data.db")


def _connect(db_path):
    if not os.path.exists(db_path):
        print("✗ 找不到数据库：%s" % db_path)
        sys.exit(1)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def _load_defaults():
    """读 mono_guard 的代码默认值，作为对照基准。"""
    try:
        import mono_guard
        return mono_guard.current_policy(), None
    except Exception as e:
        return {}, e


def cmd_list(db_path):
    defaults, err = _load_defaults()
    if err:
        print("(!) 无法导入 mono_guard（%s），只显示已覆盖项" % err)

    c = _connect(db_path)
    try:
        rows = c.execute(
            "SELECT key, value FROM site_config WHERE key LIKE ? ORDER BY key",
            (PREFIX + "%",)).fetchall()
    finally:
        c.close()

    overrides = {r["key"][len(PREFIX):]: r["value"] for r in rows}

    print("=" * 62)
    print(" 当前访问防护策略")
    print("=" * 62)
    print("%-24s %-12s %-12s %s" % ("策略项", "代码默认", "实际生效", "来源"))
    print("-" * 62)
    for k in sorted(set(list(defaults.keys()) + list(overrides.keys()))):
        d = defaults.get(k, "-")
        o = overrides.get(k)
        eff = o if o is not None else d
        src = "配置覆盖" if o is not None else "默认"
        flag = ""
        if o is not None and str(d) != str(o):
            flag = "  ← 已改"
        print("%-24s %-12s %-12s %s%s" % (k, d, eff, src, flag))
    print("-" * 62)
    if not overrides:
        print("（没有任何配置覆盖，全部使用代码默认值）")
    print()
    print("提示：改完需重启服务 —— systemctl restart mono")
    return 0


def cmd_set(db_path, key, value):
    key = key.upper().replace(PREFIX.upper(), "")
    defaults, _ = _load_defaults()
    if defaults and key not in defaults:
        print("✗ 未知策略项：%s" % key)
        print("  可用项：%s" % ", ".join(sorted(defaults.keys())))
        return 1
    try:
        n = int(value)
    except ValueError:
        print("✗ 值必须是整数：%s" % value)
        return 1
    if n < 0:
        print("✗ 值不能为负")
        return 1
    if key == "GLOBAL_PER_MIN" and n == 0:
        print("✗ GLOBAL_PER_MIN 不能为 0（会导致全站请求被拦）")
        return 1

    c = _connect(db_path)
    try:
        c.execute(
            "INSERT INTO site_config(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (PREFIX + key, str(n)))
        c.commit()
    finally:
        c.close()
    print("✓ 已设置 %s = %s" % (key, n))
    if defaults:
        old = defaults.get(key, "-")
        if str(old) != str(n):
            print("   （原代码默认值：%s）" % old)
    print("   重启后生效：systemctl restart mono")
    return 0


def cmd_unset(db_path, key):
    key = key.upper().replace(PREFIX.upper(), "")
    c = _connect(db_path)
    try:
        cur = c.execute("DELETE FROM site_config WHERE key=?", (PREFIX + key,))
        c.commit()
        n = cur.rowcount
    finally:
        c.close()
    if n:
        print("✓ 已移除 %s 的配置覆盖，将使用代码默认值" % key)
        print("   重启后生效：systemctl restart mono")
    else:
        print("（%s 本来就没有配置覆盖）" % key)
    return 0


def cmd_proxy(db_path, depth=None):
    """上 Cloudflare 后的关键步骤。

    不传 depth 时**只读取当前值，绝不修改**。
    （旧版本这里 default="1"，导致「python guard_config.py proxy」这种看起来像查询的
      命令会把已配好的值静默改成 1 —— 本项目就因此把 2 改成过 1。）
    """
    if depth is None:
        c = _connect(db_path)
        try:
            row = c.execute("SELECT value FROM site_config WHERE key=?",
                            (PREFIX + "TRUSTED_PROXY_DEPTH",)).fetchone()
        finally:
            c.close()
        cur = row[0] if row else None
        defaults, _ = _load_defaults()
        code_default = defaults.get("TRUSTED_PROXY_DEPTH")
        if cur is None:
            print("TRUSTED_PROXY_DEPTH = %s（代码默认值，未被 site_config 覆盖）" % code_default)
        else:
            print("TRUSTED_PROXY_DEPTH = %s（来自 site_config）" % cur)
        print()
        print("  ↑ 只读显示，未做任何修改。要修改必须显式给出层数：")
        print("    python guard_config.py proxy 2   # Cloudflare + Nginx（本项目当前架构）")
        print("    python guard_config.py proxy 1   # 只有 Cloudflare")
        print("    python guard_config.py proxy 0   # 直连，无反向代理")
        return 0

    try:
        n = int(depth)
    except ValueError:
        print("✗ 层数必须是整数：0=直连，1=Cloudflare，2=Cloudflare+Nginx")
        return 1
    if n not in (0, 1, 2):
        print("✗ 只支持 0 / 1 / 2（0=直连，1=Cloudflare，2=Cloudflare+Nginx）")
        return 1

    desc = {
        0: "直连（不信任任何 X-Forwarded-For，最安全）",
        1: "Cloudflare 反代（从 CF-Connecting-IP / XFF 取真实访客 IP）",
        2: "Cloudflare + 一层 Nginx（按层数从 XFF 右侧取）",
    }[n]

    rc = cmd_set(db_path, "TRUSTED_PROXY_DEPTH", n)
    if rc == 0:
        print()
        print("  含义：%s" % desc)
        if n == 0:
            print("  若已启用 Cloudflare 却保持 0，所有用户会被当成同一个 IP，")
            print("     限流会误伤（注册/登录/Socket 连接都会被拦）。")
        else:
            print("  若尚未启用反代却设为 %d，攻击者可伪造 X-Forwarded-For" % n)
            print("     伪装成任意 IP，绕过全部限流。请确认 CF 已开启橙色云朵。")
    return rc


def cmd_check(db_path):
    """自检：TRUSTED_PROXY_DEPTH 与部署方式是否匹配。"""
    c = _connect(db_path)
    try:
        row = c.execute("SELECT value FROM site_config WHERE key=?",
                        (PREFIX + "TRUSTED_PROXY_DEPTH",)).fetchone()
    finally:
        c.close()
    depth = int(row["value"]) if row else 0

    print("=" * 62)
    print(" 防护配置自检")
    print("=" * 62)
    print("TRUSTED_PROXY_DEPTH = %d" % depth)
    print()
    if depth == 0:
        print("  当前假定：公网 IP 直连，不信任任何代理头。")
        print("  ✓ 适合：应用直接监听公网端口（前面没有 Nginx / Cloudflare）。")
        print("  ✗ 若前面有 Nginx 或 Cloudflare，所有请求的来源都会变成代理的 IP，")
        print("    全站用户共用同一个限流桶（注册/登录/Socket 连接都会被误伤）。")
        print("    修复：python guard_config.py proxy 1 && systemctl restart mono")
    else:
        print("  当前假定：前面有 %d 层可信反向代理。" % depth)
        print("  ✓ 适合：应用只监听 127.0.0.1，由 Nginx 转发（1 层）；")
        print("    或 Cloudflare → Nginx → 应用（2 层，此时应设为 2）。")
        print("  ✗ 若其实没有反代，攻击者可伪造 X-Forwarded-For 绕过全部限流。")
        print("    修复：python guard_config.py proxy 0 && systemctl restart mono")
    print()
    print("提示：Nginx 配置里必须带 proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for，")
    print("      否则本设置无效（应用拿不到真实来源）。")
    print()
    print("验证真实 IP 是否解析正确：")
    print("  访问站点后查看日志 —— tail -5 logs/mono.log")
    print("  若 ip= 显示访客真实公网 IP（而非 127.0.0.1 / 172.x / CF 网段），即正确。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="MONO 访问防护策略配置工具")
    ap.add_argument("--data-dir", default=None, help="数据目录（含 data.db）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list", help="显示当前策略")
    sub.add_parser("check", help="自检配置与部署方式是否匹配")

    p_set = sub.add_parser("set", help="设置某项策略")
    p_set.add_argument("key")
    p_set.add_argument("value")

    p_un = sub.add_parser("unset", help="移除覆盖，恢复代码默认值")
    p_un.add_argument("key")

    p_px = sub.add_parser("proxy", help="设置反向代理层数（上 CF 后必做）")
    p_px.add_argument("depth", nargs="?", default=None,
                      help="0=直连，1=CF，2=CF+Nginx；不传则只显示当前值（只读）")

    args = ap.parse_args()
    data_dir, db_path = _resolve_dirs(args)

    if args.cmd == "set":
        return cmd_set(db_path, args.key, args.value)
    if args.cmd == "unset":
        return cmd_unset(db_path, args.key)
    if args.cmd == "proxy":
        return cmd_proxy(db_path, args.depth)
    if args.cmd == "check":
        return cmd_check(db_path)
    return cmd_list(db_path)


if __name__ == "__main__":
    sys.exit(main())
