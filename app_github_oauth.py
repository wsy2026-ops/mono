# -*- coding: utf-8 -*-
"""app_github_oauth.py — 站点功能规划第 037 项 · GitHub OAuth 第三方登录。

密钥（不写入代码仓库，走 systemd Environment=）：
    GITHUB_OAUTH_CLIENT_ID      GitHub OAuth App 的 Client ID
    GITHUB_OAUTH_CLIENT_SECRET  Client Secret
    GITHUB_OAUTH_MOCK=1         测试开关：callback 不请求 GitHub，返回假 profile
                                （仅本地/CI 用，线上绝不设置）

流程：
    /login/github          存 state（防 CSRF）→ 跳 GitHub 授权页
    /login/github/callback 校验 state → code 换 token → 拉 profile →
                            github_id 命中直接登录；未命中建号 → 首次进引导页
    /login/github/finish   引导页：补站内昵称（可选）+ 用 GitHub 头像（可选）
    绑定 / 解绑：已登录用户在设置页发起，回调时绑到当前 session 账号
"""
import os
import re
import secrets
import time
import urllib.parse
import urllib.request

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_USER = "https://api.github.com/user"

# 回调地址**写死**，不从 request.url_root 推导 —— 外部回调地址是契约，
# 必须与 GitHub OAuth App 登记的逐字节一致。VPN/代理篡改请求头时
# 推导值会漂移导致 GitHub 拒授（实测：VPN 下点击被拒、直连输 URL 成功）。
# 换域名时改这里，或用环境变量 GITHUB_OAUTH_REDIRECT 覆盖。
# 回调地址兜底值（动态推导优先，见 _redirect_uri）。
# 2026-10-23 换域名 example.com： authorize_url 改走 _redirect_uri(request)
# 按请求 Host 动态生成，从此换域名只用在 GitHub OAuth App 加回调 URL，
# 不用再改代码。这个常量只剩「环境变量显式覆盖」一个用途。
FIXED_REDIRECT_URI = (os.environ.get("GITHUB_OAUTH_REDIRECT") or "").strip()

_NAME_RE = re.compile(r"[^0-9A-Za-z_\-\u4e00-\u9fa5]")


# ---------------- 配置 ----------------

def configured():
    return bool(os.environ.get("GITHUB_OAUTH_CLIENT_ID")
                and os.environ.get("GITHUB_OAUTH_CLIENT_SECRET"))


def mock_mode():
    return os.environ.get("GITHUB_OAUTH_MOCK") == "1"


def _redirect_uri(request_obj):
    """回调地址。nginx 反代不带 X-Forwarded-Proto，url_root 是 http:// ——
    全站 https，GitHub OAuth App 里登记的也是 https，这里必须强制 https。"""
    root = request_obj.url_root.rstrip("/")
    if root.startswith("http://"):
        root = "https://" + root[len("http://"):]
    return root + "/login/github/callback"


# ---------------- 授权与令牌 ----------------

def new_state():
    return secrets.token_urlsafe(16)


def authorize_url(request_obj, state):
    q = urllib.parse.urlencode({
        "client_id": os.environ.get("GITHUB_OAUTH_CLIENT_ID", ""),
        # 动态 host：访客从哪个域名进来就回哪个域名（GitHub OAuth App 需登记全部域名）
        "redirect_uri": os.environ.get("GITHUB_OAUTH_REDIRECT") or _redirect_uri(request_obj),
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    })
    return AUTHORIZE_URL + "?" + q


def exchange_code(code):
    """code → access_token（失败返回 None）。"""
    body = urllib.parse.urlencode({
        "client_id": os.environ.get("GITHUB_OAUTH_CLIENT_ID", ""),
        "client_secret": os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", ""),
        "code": code,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        import json
        data = json.loads(r.read().decode())
    return data.get("access_token")


def fetch_profile(access_token):
    """access_token → GitHub 用户信息 dict。失败返回 None。"""
    import json
    req = urllib.request.Request(API_USER, headers={
        "Authorization": "token " + access_token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "mono-community-oauth",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        u = json.loads(r.read().decode())
    return {
        "id": u.get("id"),
        "login": u.get("login") or "",
        "name": u.get("name") or "",
        "avatar_url": u.get("avatar_url") or "",
        "email": u.get("email") or "",
    }


def mock_profile():
    """GITHUB_OAUTH_MOCK=1 时的假 profile（测试专用）。"""
    return {"id": 100000001, "login": "octocat",
            "name": "The Octocat", "avatar_url": "", "email": ""}


# ---------------- 用户名与建号 ----------------

def slug_username(raw):
    """GitHub login → 站内合法用户名（保留中英文数字-_，最长 20）。"""
    s = _NAME_RE.sub("", (raw or "")).strip()
    if not s:
        s = "ghuser"
    return s[:20]


def unique_username(db, base):
    """撞名加 4 位随机后缀，直到唯一。"""
    candidate = base
    for _ in range(20):
        if not db.execute("SELECT 1 FROM users WHERE username=?",
                          (candidate,)).fetchone():
            return candidate
        candidate = (base[:15] + "_" + secrets.token_hex(2))
    return base[:12] + str(int(time.time()))


def login_or_create(db, profile, gen_user_no=None, debug_quota=0):
    """github_id 命中 → (user, False)；未命中 → 建号 → (user, True)。

    OAuth 用户无密码（password_hash/salt 留空），密码登录不可用 —— 备选登录方式
    仍是原用户名密码（037 文档要求「不强制」），OAuth 用户以后可在设置里补密码。
    gen_user_no/debug_quota 由 app.py 注入（避免循环 import）。
    """
    row = db.execute("SELECT * FROM users WHERE github_id=?",
                     (str(profile["id"]),)).fetchone()
    if row:
        return row, False
    username = unique_username(db, slug_username(profile.get("login")))
    now = time.time()
    user_no = gen_user_no(db) if gen_user_no else None
    cur = db.execute(
        "INSERT INTO users (username, password_hash, salt, is_admin, banned_until, "
        "muted_until, created_at, last_active, user_no, ai_debug_left, "
        "github_id, github_login) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (username, "", "", 0, 0, 0, now, now, user_no, debug_quota,
         str(profile["id"]), (profile.get("login") or "")[:40]))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return row, True


def bind_user(db, uid, profile):
    """把 GitHub 账号绑到已登录用户。返回 (ok, msg)。"""
    other = db.execute("SELECT id FROM users WHERE github_id=? AND id != ?",
                       (str(profile["id"]), uid)).fetchone()
    if other:
        return False, "该 GitHub 账号已绑定到其他用户"
    db.execute("UPDATE users SET github_id=?, github_login=? WHERE id=?",
               (str(profile["id"]), (profile.get("login") or "")[:40], uid))
    db.commit()
    return True, "绑定成功"


def unbind_user(db, uid):
    db.execute("UPDATE users SET github_id=NULL, github_login=NULL WHERE id=?", (uid,))
    db.commit()
