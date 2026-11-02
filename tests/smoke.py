#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冒烟测试 —— 在临时目录里从零建库，然后把关键页面渲染一遍。

    python tests/smoke.py

做三件事：
  1. 用 `scripts/init_db.py` 在临时目录建一个全新的库（不碰你的 data.db）
  2. 用 Flask test client 渲染一批页面，断言 200 且没有模板残留
  3. 如果本机有 node，顺手把渲染出来的内联 <script> 过一遍语法检查

CI 里跑的就是这个。任何一步失败退出码非 0。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# 有些环境（比如装了新版 werkzeug 又用着 flask 2.2）没有 __version__，
# flask 的 test_client 会直接 AttributeError。补一个垫片，不影响真实运行。
try:
    import werkzeug
    if not hasattr(werkzeug, "__version__"):
        werkzeug.__version__ = "2.3.8"
except Exception:
    pass

PAGES = [
    "/", "/forum", "/login", "/register", "/games", "/points", "/about",
    "/chat", "/settings", "/profile", "/user/1", "/ranking", "/achievements",
]


def build_db(data_dir):
    env = dict(os.environ)
    env["MONO_DATA_DIR"] = data_dir
    env.setdefault("MONO_SECRET_KEY", "smoke-test-only")
    print("[1/3] 建库 → %s" % data_dir)
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "init_db.py"),
         "--admin", "smoke", "smoke-password-123"],
        env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit("建库失败")


def render_pages():
    print("[2/3] 渲染 %d 个页面" % len(PAGES))
    from app import app
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = 1
    bad = []
    for p in PAGES:
        r = c.get(p, follow_redirects=True)
        h = r.get_data(as_text=True)
        if r.status_code != 200:
            bad.append((p, "HTTP %s" % r.status_code))
        elif "{{ tr(" in h or "{{ LANG" in h or "Undefined" in h:
            bad.append((p, "模板未渲染干净"))
        else:
            print("      ✓ %-14s %6d 字节" % (p, len(h)))
    return bad


def check_js():
    if not shutil.which("node"):
        print("[3/3] 没有 node，跳过 JS 语法检查")
        return []
    print("[3/3] 检查内联脚本语法")
    from app import app
    c = app.test_client()
    bad = []
    for p in ("/", "/forum", "/games", "/chat"):
        h = c.get(p, follow_redirects=True).get_data(as_text=True)
        for i, m in enumerate(re.finditer(
                r"<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", h, re.I)):
            code = m.group(1)
            if len(code.strip()) < 20:
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as f:
                f.write(code)
                tmp = f.name
            r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
            os.unlink(tmp)
            if r.returncode != 0:
                bad.append((p, "script#%d" % i, (r.stderr or "").strip().splitlines()[:2]))
    if not bad:
        print("      ✓ 全部通过")
    return bad


def main():
    data_dir = tempfile.mkdtemp(prefix="mono-smoke-")
    os.environ["MONO_DATA_DIR"] = data_dir
    os.environ.setdefault("MONO_SECRET_KEY", "smoke-test-only")
    try:
        build_db(data_dir)
        bad = render_pages()
        bad += check_js()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)

    if bad:
        print("\n失败 %d 项：" % len(bad))
        for b in bad:
            print("  ", b)
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
