# -*- coding: utf-8 -*-
"""本地截图工具（配套 shot_page.js）

为什么要它：站点内容渲染在 `<iframe id="appFrame">` 里，而且所有页面都要登录。
这个脚本负责两件麻烦事 —— 生成会话 cookie、确认本地服务在跑 —— 然后把活交给
shot_page.js（headless Chromium + CDP）。

用法：
    python shot_page.py <页面路径> <输出名> [宽] [高] [full] [点击选择器] [再点选择器]

例子：
    # 打开图鉴、点开「帅」那张卡、截演示盘
    python shot_page.py /game/xqmaster/codex demo 1280 900 "" '[data-t="king"]'

    # 移动端竖屏
    python shot_page.py /game/xqmaster/codex demo_m 390 844 "" '[data-t="king"]'

    # 横屏态（页内 JS 自己会设 data-xqm-land）
    SHOT_EVAL='d.documentElement.setAttribute("data-xqm-land","1")' \
        python shot_page.py /game/xqmaster/codex demo_l 844 390 "" '[data-t="king"]'

截图落在 `_shots/`（已在 .gitignore 里）。

⚠️ 本地服务没起的话脚本会提示你先跑：
    python app.py --host 127.0.0.1 --port 5000
"""
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

NODE_CANDIDATES = [
    os.environ.get('NODE_BIN') or '',
    'node',
    'node.exe',
]


def find_node():
    for p in NODE_CANDIDATES:
        if p == 'node' or os.path.exists(p):
            return p
    return 'node'


def server_alive(port=5000):
    try:
        urllib.request.urlopen('http://127.0.0.1:%d/login' % port, timeout=3)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def make_cookie():
    """用 Flask 的签名器直接签一个 uid 会话 —— 不用知道任何人的密码。"""
    import app as A
    db = sqlite3.connect(A.DB_PATH)
    db.row_factory = sqlite3.Row
    u = db.execute('SELECT id, username FROM users ORDER BY id LIMIT 1').fetchone()
    db.close()
    if not u:
        raise SystemExit('本地库里没有用户，先注册一个')
    from flask.sessions import SecureCookieSessionInterface
    ser = SecureCookieSessionInterface().get_signing_serializer(A.app)
    print('  用 uid=%s (%s)' % (u['id'], u['username']))
    return 'session=' + ser.dumps({'uid': u['id']})


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    if not server_alive():
        print('本地服务没起。先跑：')
        print('  python app.py --host 127.0.0.1 --port 5000')
        return 1

    print('生成会话 cookie…')
    os.environ['SHOT_COOKIE'] = make_cookie()

    node = find_node()
    args = [node, os.path.join(ROOT, 'shot_page.js')] + sys.argv[1:]
    return subprocess.call(args)


if __name__ == '__main__':
    sys.exit(main())
