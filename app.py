"""
MONO 社区 v6.7
功能：
  1. 注册 / 登录 / 退出 / 用户头像 / 在线状态
  2. 群聊 + 私聊（好友系统），支持拖拽上传文件，消息5分钟撤回
  3. 敏感词检测，@提及通知
  4. 论坛系统：发帖、评论、点赞、置顶、Markdown + LaTeX 支持
  5. 特效系统：点赞超10渐变特效、管理员专属特效、管理员可授予特效、帖子封面特效
  6. 荣誉牌子系统：管理员自定义文字+特效
  7. 管理后台：定时封号/禁言、帖子管理、特效授予、牌子管理
  8. 系统通知中心
  9. 局域网开放访问
"""

import os
import time
import threading
import random
import shutil
import zipfile
import io
import subprocess
import sqlite3
import secrets
import hashlib
import hmac
import binascii
import uuid
import re
import json
import ssl
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from functools import lru_cache, wraps
import invite_cash as IC
import device_fp as DFP
import mailer as _mail
import app_achievements as _ach
import cache
import app_topics as _topics
import app_fts as _fts
import chat  # 051：独立 IM 模块（私聊/聊天室/SocketIO 事件）
import games  # 拆包：小游戏（扫雷/汉诺塔/华容道/24点/数独/色盲/跳跳/闲弈/成绩/回放/榜单）
import board  # 拆包：棋盘对战（五子棋/围棋/象棋/翻转棋/国际象棋/军棋）
import xqm  # 拆包：象棋大师（匹配/赛季/钱包/抽签/API），规则引擎 xqmaster.py
import ojweb.core  # 拆包：OJ Web 层（题库/比赛/提交/记录/后台），判题机在 oj/ 目录
import pg_shadow  # 052 PG 渐进迁移（影子双写 + 读切流）
import moderation as _moderation  # 053 审核四层防线（cold + Qwen3Guard + 人工队列）
import similarity as _similarity   # 061 内容相似度（TF-IDF + 余弦）
from blocking import run_blocking   # eventlet 迁移：阻塞调用走线程池
import adminpanel  # 拆包：管理后台（56 路由）
from adminpanel.core import (  # 拆包兼容层：签到/任务等仍在用
    _MM_LABEL,
    _ai_code_tables,
    _today_str,
)
# 拆包兼容层：以下符号已搬进 games/mini.py，但 inject_globals / ranking /
# 后台等处仍在用，从包内引回保持老代码零改动（games.mini 不 import app，无循环）。
from games.mini import (  # noqa: E402
    _GAME_FIELD_IS_TIME, _GAME_SCORE_FIELDS, _GAME_SCORE_ROWS, _GAME_TIME_ROWS,
    _board_wins_leaderboard, _fmt_time, _game_reset_sql, _guling_enabled,
    _my_ai_survival,
)
import app_github_oauth as _gh
import app_storage as _storage
from flask import (
    Flask, request, session, redirect, url_for,
    render_template, render_template_string, flash, jsonify, g, send_from_directory, send_file, abort, Response
)
from werkzeug.utils import secure_filename
import imgproc

import sys as _sys_mod
for _s in (_sys_mod.stdout, _sys_mod.stderr):
    try:
        _s.reconfigure(errors="replace", line_buffering=True)
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", ":replace")


def _proc_stop(p, grace=5):
    """结束一个子进程并回收。

    只 terminate/kill 不 wait 的话，进程还挂在进程表里变成 <defunct>，
    父进程不死就一直攒着。所以 kill 之后必须再 wait 一次。
    """
    if p is None:
        return
    try:
        p.terminate()
    except Exception:
        pass
    try:
        p.wait(timeout=grace)
        return
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass
    try:
        p.wait(timeout=grace)
    except Exception:
        pass

import warnings as _warnings_mod
_warnings_mod.filterwarnings("ignore", message=".*doesn't match a supported version.*")
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect as sio_disconnect

import admin_secret
import jieqi_game as _jq
import mono_log as _log_mod
import mono_guard as _guard
import monitor as _monitor

_CODE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.environ.get("MONO_DATA_DIR") or _CODE_DIR)
DB_PATH = os.path.join(BASE_DIR, "data.db")

_log_mod.init_logging(BASE_DIR)
log = _log_mod.get_logger()


def _total_ram_mb():
    """探测本机物理内存总量（MB）。探测失败返回 0（视作大内存，不收紧）。"""
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            pages = os.sysconf("SC_PHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and size > 0:
                return int(pages * size // (1024 * 1024))
    except Exception:
        pass
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        if st.ullTotalPhys:
            return int(st.ullTotalPhys // (1024 * 1024))
    except Exception:
        pass
    return 0


_RAM_MB = _total_ram_mb()
_LOW_MEM = 0 < _RAM_MB <= 3072

SENSITIVE_FILE = os.path.join(BASE_DIR, "sensitive_words.txt")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "avatars")
MUSIC_FOLDER = os.path.join(BASE_DIR, "music")
os.makedirs(MUSIC_FOLDER, exist_ok=True)
MUSIC_ALLOWED_EXT = {'mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac', 'opus'}
MUSIC_MAX_SIZE = 50 * 1024 * 1024
# 上传时一律转成 MP3：FLAC/WAV 动辄几十 MB，手机端解码也吃力。
# 之前设5MB 门槛，结果小文件不转 —— 3MB 的 flac 一样播不出来。
# 「压完反而更大就保留原文件」的逻辑在 _music_compress 里，不会越压越大。
MUSIC_COMPRESS_THRESHOLD = 0
# 统一转成 Opus（装在 .ogg 容器里）。同等主观音质下比 MP3 小 30~50%，
# 96k 是「听不出编码痕迹」的档位，再高只是在浪费带宽。
MUSIC_COMPRESS_BITRATE = "96k"
MUSIC_COMPRESS_TIMEOUT = 300
ALLOWED_EXTENSIONS = {
    # 图片（含手机拍照常见的 webp / heic）
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'heic', 'heif', 'avif', 'tif', 'tiff',
    # 视频
    'mp4', 'avi', 'mov', 'mkv', 'webm', 'm4v', 'wmv', 'flv', '3gp',
    # 音频
    'mp3', 'wav', 'm4a', 'aac', 'ogg', 'oga', 'flac', 'wma', 'opus',
    # 文档
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md', 'csv', 'json',
    # 压缩包
    'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz',
}
MAX_CONTENT_LENGTH = 100 * 1024 * 1024


def _load_secret_key():
    """安全密钥：优先读环境变量 MONO_SECRET_KEY，否则读取/生成本地 .secret_key 文件。
    文件不存在则随机生成并写入，重启后 session 不失效；源码不再含固定密钥。"""
    env_key = os.environ.get("MONO_SECRET_KEY")
    if env_key:
        return env_key
    key_file = os.path.join(BASE_DIR, ".secret_key")
    try:
        with open(key_file, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    except Exception:
        pass
    key = os.urandom(32).hex()
    try:
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key)
    except Exception:
        pass
    return key


app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
# 记住登录：默认的 session 是「浏览器会话」，关掉浏览器就没了。
# 改成 30 天，配合登录时的 session.permanent = True。
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

if os.environ.get('MONO_COOKIE_SECURE', '0') == '1':
    app.config['SESSION_COOKIE_SECURE'] = True

# 094 国际化：界面文案抽到 i18n/*.json，模板里写 {{ t('nav.home') }}。
# 必须在第一次 render_template 之前注册，否则模板里 t 是 undefined。
import i18n as _i18n  # noqa: E402
_i18n.register(app)

socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# 静态资源版本号 = 文件内容 MD5 前 8 位，内容变则 URL 自动变，永不漏改。
_ASSET_VER_CACHE = {}
_NO_VERIFY = ("manifest.json", "icon.svg")

# CDN 域名。设了就把静态资源指过去，没设（或设空）就仍走本站，行为完全不变。
# 走 CDN 的是静态资源，HTML 页面和接口永远回本站 —— 缓存出问题只影响图片和样式。
CDN_BASE = (os.environ.get("MONO_CDN_BASE") or "").strip().rstrip("/")


def _asset_ver(filename):
    fn = (filename or "").replace("\\", "/").lstrip("/")
    if not fn or fn in _NO_VERIFY or fn in _ASSET_VER_CACHE:
        return _ASSET_VER_CACHE.get(fn, "")
    path = os.path.join(app.static_folder, fn)
    try:
        with open(path, "rb") as fh:
            v = hashlib.md5(fh.read()).hexdigest()[:8]
    except OSError:
        v = ""
    _ASSET_VER_CACHE[fn] = v
    return v


@app.url_defaults
def _static_url_defaults(endpoint, values):
    if endpoint == "static":
        fn = values.get("filename")
        if fn and "v" not in values:
            ver = _asset_ver(fn)
            if ver:
                values["v"] = ver


def cdn_url(filename):
    """静态资源 URL。CDN 开着走 CDN，没开走本站。指纹参数由 url_defaults 统一注入。"""
    fn = (filename or "").lstrip("/")
    if not fn:
        return ""
    if CDN_BASE:
        ver = _asset_ver(fn)
        qs = ("?v=" + ver) if ver else ""
        return CDN_BASE + "/static/" + fn + qs
    return url_for("static", filename=fn)


@app.context_processor
def _inject_asset_ver():
    return {"asset": _asset_ver, "cdn_base": CDN_BASE, "cdn_url": cdn_url}


PERMANENT = -1
RECALL_WINDOW = 120   # 034：普通消息 2 分钟内可撤回（文档口径；超时用无痕撤回卡延到 24h）
ONLINE_THRESHOLD = 300

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)

if BASE_DIR != _CODE_DIR and not os.path.exists(SENSITIVE_FILE):
    try:
        _sw_src = os.path.join(_CODE_DIR, "sensitive_words.txt")
        if os.path.exists(_sw_src):
            with open(_sw_src, "rb") as _f1, open(SENSITIVE_FILE, "wb") as _f2:
                _f2.write(_f1.read())
    except Exception:
        pass


@app.route("/static/avatars/<path:filename>")
def _static_avatars(filename):
    """头像固定从数据目录读（云端 MONO_DATA_DIR 时与代码目录分离）。"""
    return send_from_directory(AVATAR_FOLDER, filename)

_LOG_SKIP_PREFIX = ("/static/", "/favicon.ico", "/healthz", "/music/stream/", "/socket.io/")
_SLOW_MS = 1500

_IC_PAGE_BEAT = {}


_ANALYTICS_READY = False


_ACH_READY = False


@app.before_request
def _ach_bootstrap():
    """027 成就系统：建表 + 种子，惰性一次（与 _analytics_bootstrap 同模式）。"""
    global _ACH_READY
    if _ACH_READY:
        return None
    _ACH_READY = True
    try:
        _ach.ensure_schema(get_db())
    except Exception as e:
        _log_mod.event("ach_bootstrap_error", err=str(e)[:120])
    return None


_TOPICS_READY = False


@app.before_request
def _topics_bootstrap():
    """029/030：标签与主题表 + 存量迁移，惰性一次（幂等）。"""
    global _TOPICS_READY
    if _TOPICS_READY:
        return None
    _TOPICS_READY = True
    try:
        _topics.ensure_schema(get_db())
    except Exception as e:
        _log_mod.event("topics_bootstrap_error", err=str(e)[:120])
    return None


_FTS_READY = False


@app.before_request
def _fts_bootstrap():
    """036：私聊 FTS5 索引 + 触发器，惰性一次（幂等；不支持则静默关闭搜索）。"""
    global _FTS_READY
    if _FTS_READY:
        return None
    _FTS_READY = True
    try:
        _fts.ensure_schema(get_db())
    except Exception as e:
        _log_mod.event("fts_bootstrap_error", err=str(e)[:120])
    return None


_COMMENTS_READY = False


@app.before_request
def _comments_bootstrap():
    """031：post_comments.root_id 补列（幂等，已有则不做事）。"""
    global _COMMENTS_READY
    if _COMMENTS_READY:
        return None
    _COMMENTS_READY = True
    try:
        db = get_db()
        _add_column(db, "post_comments", "root_id", "INTEGER DEFAULT NULL")
        db.commit()
    except Exception as e:
        _log_mod.event("comments_bootstrap_error", err=str(e)[:120])
    return None


_FAVS_READY = False


@app.before_request
def _favs_bootstrap():
    """032：favorites 扩展三类对象（post/user/game）+ 公开开关，幂等。"""
    global _FAVS_READY
    if _FAVS_READY:
        return None
    _FAVS_READY = True
    try:
        db = get_db()
        _add_column(db, "favorites", "target_type", "TEXT NOT NULL DEFAULT 'post'")
        _add_column(db, "favorites", "is_public", "INTEGER NOT NULL DEFAULT 0")
        # 存量：target_type 空 → post；post_id 同时作 target_id
        db.execute("UPDATE favorites SET target_type='post' WHERE target_type IS NULL OR target_type=''")
        db.commit()
    except Exception as e:
        _log_mod.event("favs_bootstrap_error", err=str(e)[:120])
    return None


_GH_READY = False


@app.before_request
def _gh_bootstrap():
    """037：users 表 GitHub OAuth 绑定列（幂等）。"""
    global _GH_READY
    if _GH_READY:
        return None
    _GH_READY = True
    try:
        db = get_db()
        _add_column(db, "users", "github_id", "TEXT")
        _add_column(db, "users", "github_login", "TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_github "
                   "ON users(github_id) WHERE github_id IS NOT NULL")
        db.commit()
    except Exception as e:
        _log_mod.event("gh_bootstrap_error", err=str(e)[:120])
    return None


_UA_READY = False


@app.before_request
def _ua_bootstrap():
    """044/046：user_actions 行为埋点表（幂等）。"""
    global _UA_READY
    if _UA_READY:
        return None
    _UA_READY = True
    try:
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id INTEGER NOT NULL DEFAULT 0,
                weight INTEGER NOT NULL DEFAULT 1,
                ip TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_ua_user_ts "
                   "ON user_actions(user_id, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ua_action_ts "
                   "ON user_actions(action, created_at)")
        db.commit()
    except Exception as e:
        _log_mod.event("ua_bootstrap_error", err=str(e)[:120])
    return None


_PL_READY = False


@app.before_request
def _pl_bootstrap():
    """045：points_log 幂等键列（幂等）。"""
    global _PL_READY
    if _PL_READY:
        return None
    _PL_READY = True
    try:
        db = get_db()
        _add_column(db, "points_log", "idem_key", "TEXT")
        # 同用户同幂等键只允许一条（部分索引：NULL/空串不约束）
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pl_idem "
                   "ON points_log(user_id, idem_key) WHERE idem_key IS NOT NULL AND idem_key != ''")
        db.commit()
    except Exception as e:
        _log_mod.event("pl_bootstrap_error", err=str(e)[:120])
    return None


_NP_READY = False


@app.before_request
def _np_bootstrap():
    """047：notif_prefs 通知偏好表（幂等）。"""
    global _NP_READY
    if _NP_READY:
        return None
    _NP_READY = True
    try:
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS notif_prefs (
                user_id INTEGER NOT NULL,
                cat_key TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (user_id, cat_key)
            )
        """)
        db.commit()
    except Exception as e:
        _log_mod.event("np_bootstrap_error", err=str(e)[:120])
    return None


_MSG_COLS_READY = False


@app.before_request
def _msg_cols_bootstrap():
    """051：messages 表补 idem_key（幂等键）与 delivered_at（送达回执）两列。"""
    global _MSG_COLS_READY
    if _MSG_COLS_READY:
        return None
    _MSG_COLS_READY = True
    try:
        db = get_db()
        _add_column(db, "messages", "idem_key", "TEXT")
        _add_column(db, "messages", "delivered_at", "REAL NOT NULL DEFAULT 0")
        # 同发送人同幂等键只允许一条（防重连重发/双击重复提交）
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_idem "
                   "ON messages(user_id, idem_key) WHERE idem_key IS NOT NULL AND idem_key != ''")
        db.commit()
    except Exception as e:
        _log_mod.event("msg_cols_bootstrap_error", err=str(e)[:120])
    return None


_IDX_READY = False


@app.before_request
def _idx_bootstrap():
    """049：核心查询索引补齐（幂等，全部 IF NOT EXISTS）。"""
    global _IDX_READY
    if _IDX_READY:
        return None
    _IDX_READY = True
    try:
        db = get_db()
        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_posts_sec_time ON posts(section_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_posts_thread ON posts(thread_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_pc_post_time ON post_comments(post_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_pc_parent ON post_comments(parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_msg_priv ON messages(is_private, receiver_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_msg_user_time ON messages(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_pl_user_time ON points_log(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_pl_type ON points_log(source_type, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notif_user_read ON notifications(user_id, is_read, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_fav_target ON favorites(target_type, post_id)",
            "CREATE INDEX IF NOT EXISTS idx_ts_user ON thread_subs(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_tagsub_user ON tag_subs(user_id)",
        ):
            try:
                db.execute(ddl)
            except Exception:
                pass
        db.commit()
    except Exception as e:
        _log_mod.event("idx_bootstrap_error", err=str(e)[:120])
    return None


def _track(user_id, action, target_type="", target_id=0, weight=1):
    """044/046 埋点写入。fail-open：写失败绝不影响用户正在做的事。"""
    try:
        db = get_db()
        db.execute(
            "INSERT INTO user_actions (user_id, action, target_type, target_id, weight, ip, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id or 0, action, target_type or "", target_id or 0, weight,
             (getattr(g, "_ip", "") or "")[:60], time.time()))
        db.commit()
    except Exception:
        pass


# ---------------- 044 贡献热力图 ----------------

_UA_ACTION_NAME = {
    "register": "注册加入", "login": "登录", "post": "发帖", "comment": "评论",
    "like": "点赞", "game": "打了一局游戏", "upload": "上传图片",
}


def _ua_heatmap(uid, weeks=26):
    """近 N 周：返回 (日期字符串列表[旧→新], {日期: 次数})。

    服务器 UTC，日期按 +8 国内时区归日（'藏在时区里的 bug' 踩过，
    这里 date() 直接带 +8 hours 偏移）。
    """
    try:
        db = get_db()
        since = time.time() - weeks * 7 * 86400
        counts = {}
        for r in db.execute(
                "SELECT date(created_at, 'unixepoch', '+8 hours') AS d, COUNT(*) AS c "
                "FROM user_actions WHERE user_id=? AND created_at > ? GROUP BY d",
                (uid, since)).fetchall():
            counts[r["d"]] = r["c"]
        import datetime as _dt
        days = []
        base = _dt.datetime.utcnow() + _dt.timedelta(hours=8)
        for i in range(weeks * 7 - 1, -1, -1):
            days.append((base - _dt.timedelta(days=i)).strftime("%Y-%m-%d"))
        return days, counts
    except Exception:
        return [], {}


def _ua_recent(uid, limit=20):
    """最近活动时间线（action 已备中文名）。"""
    try:
        db = get_db()
        rows = db.execute(
            "SELECT action, target_type, target_id, created_at FROM user_actions "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
        return [{"name": _UA_ACTION_NAME.get(r["action"], r["action"]),
                 "target": r["target_id"], "ts": r["created_at"]} for r in rows]
    except Exception:
        return []


def _gh_download_avatar(uid, url):
    """GitHub 头像下载到头像目录（失败静默 None，不挡登录流程）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mono-community"})
        data = urllib.request.urlopen(req, timeout=10).read()
        if not (100 < len(data) <= 2 * 1024 * 1024):
            return None
        fn = "gh_%d.png" % uid
        with open(os.path.join(AVATAR_FOLDER, fn), "wb") as f:
            f.write(data)
        return fn
    except Exception:
        return None


@app.before_request
def _analytics_bootstrap():
    """建分析表 —— 惰性执行，第一次请求时建好。

    原来 _boot_migrate 只在 __main__ 里调，而线上跑的是 waitress/gunicorn
    （不走 __main__），迁移永远不会执行 —— 这跟 init_db 是同一类坑。
    """
    global _ANALYTICS_READY
    if _ANALYTICS_READY:
        return None
    p = request.path or ""
    if p.startswith(("/static/", "/api/", "/socket.io", "/favicon")):
        return None
    _ANALYTICS_READY = True
    try:
        _ensure_analytics_tables()
    except Exception:
        pass
    return None


@app.before_request
def _log_before():
    g._t0 = time.time()
    g._ip = _guard.client_ip(request)

    p = request.path or ""
    if p.startswith(_LOG_SKIP_PREFIX):
        return None
    try:
        ok, _rem, retry = _guard.hit("all:%s" % g._ip, _guard.GLOBAL_PER_MIN, 60)
        if not ok:
            _log_mod.event("global_rate_block", ip=g._ip, path=p[:80])
            _monitor.note_attack("app_rate", g._ip, p, "应用层全站限流")
            log.warning("限流拦截 ip=%s path=%s", g._ip, p)
            resp = jsonify({"ok": False, "msg": "请求过于频繁，请稍后再试"})
            resp.status_code = 429
            resp.headers["Retry-After"] = str(retry)
            return resp
    except Exception:
        pass

    if request.method == "POST" and request.mimetype == "multipart/form-data":
        try:
            ok, _rem, retry = _guard.hit("up:%s" % g._ip, _guard.UPLOAD_PER_DAY, 86400)
            if not ok:
                _log_mod.event("upload_block", ip=g._ip, path=p[:80])
                _monitor.note_attack("upload_block", g._ip, p, "上传次数超限")
                resp = jsonify({"ok": False, "msg": "今日上传次数已达上限，请明天再试"})
                resp.status_code = 429
                resp.headers["Retry-After"] = str(retry)
                return resp
        except Exception:
            pass
    return None


@app.after_request
def _log_after(resp):
    try:
        p = request.path or ""
        if p.startswith(_LOG_SKIP_PREFIX):
            return resp
        t0 = getattr(g, "_t0", None)
        cost = int((time.time() - t0) * 1000) if t0 else -1
        uid = None
        try:
            u = globals().get("current_user")
            u = u() if callable(u) else None
            uid = u["id"] if u else None
        except Exception:
            pass
        ip = getattr(g, "_ip", None) or _guard.client_ip(request)
        if resp.status_code >= 500 or cost >= _SLOW_MS:
            log.warning("REQ %s %s -> %s %dms ip=%s uid=%s",
                        request.method, p, resp.status_code, cost, ip, uid or "-")
        else:
            log.info("REQ %s %s -> %s %dms ip=%s uid=%s",
                     request.method, p, resp.status_code, cost, ip, uid or "-")
        _monitor.note_request(cost if cost and cost > 0 else 0)
        _sql_analyze(p, cost if cost and cost > 0 else 0,
                     getattr(g, "_sql_collect", None))   # 049：N+1/慢请求 SQL dump
    except Exception:
        pass
    return resp


@app.errorhandler(Exception)
def _log_uncaught(e):
    """兜底异常：记录完整 traceback，页面不被吞成空白。"""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, KeyError) and 'Session is disconnected' in str(e):
        # Engine.IO 在客户端拿过期 sid 轮询时抛的。每次重启后必然出现一批，
        # 属于正常竞态而不是故障，返回 400 让客户端换新会话即可，别刷日志。
        return ("", 400)
    if isinstance(e, HTTPException):
        if e.code and e.code >= 500:
            log.error("HTTP %s %s: %s", e.code, request.path, e.description)
        return e
    log.exception("未捕获异常 %s %s", request.method, request.path)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "服务器开小差了，稍后再试"}), 500
    try:
        return render_template("error.html", code=500,
                               title="服务器开小差了",
                               msg="这个问题我们已经记下来了，过一会儿刷新再试试。"), 500
    except Exception:
        return "服务器开小差了，稍后再试", 500


@app.errorhandler(404)
def _not_found(e):
    """404 也给一个像样的页面，而不是白底黑字的默认错误页。"""
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "接口不存在"}), 404
    try:
        return render_template("error.html", code=404,
                               title="这个页面不存在",
                               msg="可能是链接过期了，或者地址打错了。"), 404
    except Exception:
        return "页面不存在", 404


@app.errorhandler(403)
def _forbidden(e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "没有权限"}), 403
    try:
        return render_template("error.html", code=403,
                               title="你没有权限看这个",
                               msg="如果觉得是误判，可以在「申诉」里告诉我们。"), 403
    except Exception:
        return "没有权限", 403


def _rfc2822(ts):
    return time.strftime("%a, %d %b %Y %H:%M:%S +0800", time.localtime(ts or 0))


def _xml_esc(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _abs(path):
    # nginx 反代没带 X-Forwarded-Proto，url_root 是 http:// —— 全站 https，
    # sitemap / feed / robots 里的绝对 URL 必须是 https。
    root = request.url_root.rstrip("/")
    if root.startswith("http://"):
        root = "https://" + root[len("http://"):]
    return root + (path if path.startswith("/") else "/" + path)


@app.route("/feed.xml")
@app.route("/feed.atom")
@app.route("/feed/board/<int:sid>.xml")
@app.route("/feed/board/<int:sid>.atom")
def _feed(sid=None):
    """订阅源。默认 RSS 2.0，加 .atom 后缀（或 ?format=atom）输出 Atom 1.0。

    不带 sid 是全站，带 sid 是单个板块。
    """
    db = get_db()
    _atom = request.path.endswith(".atom")
    try:
        limit = min(max(int(request.args.get("limit", 30)), 1), 100)
    except (TypeError, ValueError):
        limit = 30
    if sid is not None:
        sec = db.execute("SELECT * FROM forum_sections WHERE id=?", (sid,)).fetchone()
        if not sec:
            abort(404)
        sql = ("SELECT p.*, u.username AS uname FROM posts p "
               "LEFT JOIN users u ON p.user_id = u.id "
               "WHERE p.section_id=? AND p.recycle=0 AND p.is_deleted=0 "
               "ORDER BY p.created_at DESC LIMIT ?")
        rows = db.execute(sql, (sid, limit)).fetchall()
        link = _abs("/forum?section=%d" % sid)
        ttl = "60"
    else:
        rows = db.execute(
            "SELECT p.*, u.username AS uname FROM posts p "
            "LEFT JOIN users u ON p.user_id = u.id "
            "WHERE p.recycle=0 AND p.is_deleted=0 "
            "ORDER BY p.created_at DESC LIMIT ?", (limit,)).fetchall()
        link = _abs("/")
        ttl = "30"

    items = []
    rows_meta = []
    for r in rows:
        url = _abs("/post/%s" % r["id"])
        body = r["summary"] or plain_text(r["content"], 300)
        author = r["uname"] or r["username"] or ""
        rows_meta.append((r, url, body, author, r["created_at"]))
        items.append(
            "    <item>\n"
            "      <title>%s</title>\n"
            "      <link>%s</link>\n"
            "      <guid isPermaLink=\"true\">%s</guid>\n"
            "      <pubDate>%s</pubDate>\n"
            "      <author>%s</author>\n"
            "      <description>%s</description>\n"
            "    </item>" % (
                _xml_esc(r["title"]), _xml_esc(url), _xml_esc(url),
                _rfc2822(r["created_at"]),
                _xml_esc(r["uname"] or r["username"] or ""),
                _xml_esc(body),
            ))

    body = "\n".join(items)
    fmt = "atom" if (_atom or (request.args.get("format") or "").lower() == "atom") else "rss"

    if fmt == "atom":
        # Atom 1.0：日期必须 ISO 8601 且带时区，RSS 的 RFC822 不通用
        def iso(ts):
            return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime(ts or 0))
        entries = []
        for r, url, b, au, ts in rows_meta:
            entries.append(
                "  <entry>\n"
                "    <title>%s</title>\n"
                '    <link href="%s" rel="alternate" type="text/html"/>\n'
                '    <id>%s</id>\n'
                "    <updated>%s</updated>\n"
                '    <published>%s</published>\n'
                "    <author><name>%s</name></author>\n"
                "    <summary type=\"text\">%s</summary>\n"
                "  </entry>" % (
                    _xml_esc(r["title"]), _xml_esc(url), _xml_esc(url),
                    iso(ts), iso(ts), _xml_esc(au), _xml_esc(b),
                ))
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<feed xmlns="http://www.w3.org/2005/Atom">\n'
            "  <title>%s</title>\n"
            '  <link href="%s" rel="alternate" type="text/html"/>\n'
            '  <link href="%s" rel="self" type="application/atom+xml"/>\n'
            "  <subtitle>%s</subtitle>\n"
            "  <updated>%s</updated>\n"
            '  <generator>MONO</generator>\n'
            "  <id>%s</id>\n"
            "%s\n"
            "</feed>\n" % (
                _xml_esc(_SITE_TITLE + (" · " + sec["name"] if sid is not None else "")),
                _xml_esc(link), _xml_esc(request.base_url), _xml_esc(_SITE_DESC),
                iso(max([m[4] for m in rows_meta], default=time.time())),
                _xml_esc(link), "\n".join(entries),
            )
        )
        mime = "application/atom+xml"
    else:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
            "  <channel>\n"
            "    <title>%s</title>\n"
            "    <link>%s</link>\n"
            "    <description>%s</description>\n"
            "    <language>zh-cn</language>\n"
            "    <ttl>%s</ttl>\n"
            '    <atom:link href="%s" rel="self" type="application/rss+xml"/>\n'
            "%s\n"
            "  </channel>\n"
            "</rss>\n" % (
                _xml_esc(_SITE_TITLE + (" · " + sec["name"] if sid is not None else "")),
                _xml_esc(link), _xml_esc(_SITE_DESC), ttl,
                _xml_esc(request.url), body,
            )
        )
        mime = "application/rss+xml"

    r = Response(xml, mimetype=mime)
    r.headers["Content-Type"] = mime + "; charset=utf-8"
    r.headers["Cache-Control"] = "public, max-age=600"
    return r


_SITEMAP_STATIC = ("", "/about", "/activities", "/oj",
                   "/forum", "/games", "/ranking", "/achievements", "/points", "/feedback")


@app.route("/sitemap.xml")
def _sitemap():
    """站点地图。只收录有内容价值的公开页，后台/设置类不进。"""
    db = get_db()
    urls = [(p, _ts_of(p)) for p in _SITEMAP_STATIC]

    def _grab(sql, path_tpl):
        try:
            for r in db.execute(sql).fetchall():
                urls.append((path_tpl % r["id"],
                             _row_get(r, "updated_at", 0) or _row_get(r, "created_at", 0)))
        except Exception:
            pass

    _grab("SELECT id, updated_at, created_at FROM posts "
          "WHERE recycle=0 AND is_deleted=0 ORDER BY created_at DESC LIMIT 2000",
          "/post/%s")
    # OJ 题目：只收录「公开 + 已审核」的，比赛题不进 sitemap（赛前泄题）
    _grab("SELECT id FROM oj_problems "
          "WHERE visibility='public' AND status='approved' ORDER BY id ASC LIMIT 2000",
          "/oj/problem/%s")
    _grab("SELECT id, created_at FROM activities WHERE status!='draft' "
          "ORDER BY id DESC LIMIT 200", "/activity/%s")
    _grab("SELECT id FROM users ORDER BY id DESC LIMIT 500", "/user/%s")
    # 板块用 ?section= 筛选，query 不适合进 sitemap loc，
    # 只收录带锚点的板块首页形式，搜索引擎照样能抓到分区。
    try:
        for r in db.execute("SELECT id FROM forum_sections WHERE is_hidden=0 "
                            "ORDER BY sort_order ASC").fetchall():
            urls.append(("/forum#section-%s" % r["id"], 0))
    except Exception:
        pass

    seen = set()
    locs = []
    for path, ts in urls:
        if path in seen:
            continue
        seen.add(path)
        loc = _abs(path)
        if ts:
            locs.append("  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n  </url>"
                        % (_xml_esc(loc),
                           time.strftime("%Y-%m-%d", time.localtime(ts))))
        else:
            locs.append("  <url>\n    <loc>%s</loc>\n  </url>" % _xml_esc(loc))

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           "%s\n</urlset>\n" % "\n".join(locs))
    r = Response(xml, mimetype="application/xml")
    r.headers["Content-Type"] = "application/xml; charset=utf-8"
    r.headers["Cache-Control"] = "public, max-age=3600"
    return r


def _ts_of(path):
    return 0


@app.route("/<path:_vname>.txt")
def _verify_txt(_vname):
    """站点根目录下的 txt 验证文件（微信安全申诉等平台要用）。

    只放行 32 位十六进制文件名，其余一律 404 —— 免得这个路由变成任意文件读取口。
    """
    if not re.fullmatch(r"[0-9a-fA-F]{32}", _vname or ""):
        abort(404)
    path = os.path.join(app.static_folder, _vname + ".txt")
    if not os.path.isfile(path):
        abort(404)
    r = send_from_directory(app.static_folder, _vname + ".txt", mimetype="text/plain")
    r.headers["Content-Type"] = "text/plain; charset=utf-8"
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r


@app.route("/robots.txt")
def _robots():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /settings\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "Disallow: /drive\n"
        "Disallow: /private-chat\n"
        "Disallow: /notifications\n"
        "\n"
        "Sitemap: %s\n" % _abs("/sitemap.xml")
    )
    r = Response(body, mimetype="text/plain")
    r.headers["Content-Type"] = "text/plain; charset=utf-8"
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


@app.route("/healthz")
def _healthz():
    """存活探针：供外部监控（UptimeRobot 等、洛杉矶备用机）探测。

    2026-10-31 起多查两件事——这两件都是「不报错但致命」：
      · 判题题目目录（`/opt/mono/oj/problems` 软链接丢了 → 目录空 → 判题全线 SE）
      · 判题机是否可达（只上报，不判死：判题机重启是正常的）
    目录为空时返回 503，让监控能直接发现软链接丢失。
    """
    ok, detail = True, "ok"
    checks = {}
    try:
        get_db().execute("SELECT 1").fetchone()
        checks["db"] = True
    except Exception as e:
        ok, detail = False, "db: %s" % e
        checks["db"] = False

    pdir = os.environ.get("MONO_OJ_PROBLEMS_DIR", "/opt/mono/oj/problems")
    try:
        n = len([x for x in os.listdir(pdir) if not x.startswith(".")])
    except Exception:
        n = -1
    checks["oj_problems_dir"] = pdir
    checks["oj_problems_count"] = n
    if n <= 0:
        ok = False
        if detail == "ok":
            detail = "oj problems dir empty/missing: %s" % pdir

    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=2) as r:
            checks["judge"] = (r.status == 200)
    except Exception:
        checks["judge"] = False

    return jsonify({"ok": ok, "detail": detail, "checks": checks,
                    "ts": int(time.time())}), (200 if ok else 503)


_cfg_cache = {}
_cfg_loaded = False
_sw_cache = []
_sw_loaded = False

def reload_config_cache():
    global _cfg_cache, _cfg_loaded
    db = get_db()
    rows = db.execute("SELECT key, value FROM site_config").fetchall()
    _cfg_cache = {r['key']: r['value'] for r in rows}
    _cfg_loaded = True

def get_config(key, default=None):
    if not _cfg_loaded:
        reload_config_cache()
    return _cfg_cache.get(key, default)

def set_config(key, value):
    db = get_db()
    db.execute("INSERT INTO site_config (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit()
    reload_config_cache()

def reload_sensitive_cache():
    global _sw_cache, _sw_loaded
    db = get_db()
    rows = db.execute("SELECT word FROM sensitive_words").fetchall()
    words = [r['word'] for r in rows if r['word']]
    words.sort(key=len, reverse=True)
    _sw_cache = words
    _sw_loaded = True

def get_sensitive_words():
    if not _sw_loaded:
        reload_sensitive_cache()
    return _sw_cache

SPAM_PATTERNS = [
    r'https?://[^\s]+',
    r'www\.[^\s]+',
    r'[a-z0-9][a-z0-9-]*\.(com|cn|net|org|xyz|top|vip|club|shop|link)[^\s]*',
    r'(qq群|q群|加群|扣群|微信群|微信号?|威信|薇信|[vV][xX]号?|加我|扫码|私聊加|引流|代练|刷单|兼职)',
    r'(telegram|tg群|飞机群|推特|twitter|instagram)',
]

def _match_words(text):
    case_sensitive = get_config("case_sensitive", "0") == "1"
    fuzzy = get_config("fuzzy_match", "0") == "1"
    hit_words = []
    for w in get_sensitive_words():
        if not w:
            continue
        if case_sensitive:
            if w in text:
                hit_words.append(w)
        else:
            low_text = text.lower()
            low_w = w.lower()
            if fuzzy:
                norm_text = re.sub(r'\s+', '', low_text)
                norm_w = re.sub(r'\s+', '', low_w)
                if norm_w and norm_w in norm_text:
                    hit_words.append(w)
            else:
                if low_w in low_text:
                    hit_words.append(w)
    return hit_words

def _match_spam(text):
    if get_config("spam_autoblock", "1") != "1":
        return False
    for p in SPAM_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False

def content_blocked(text):
    """拦截模式或命中违规话术时返回 (True, 原因)"""
    if not text:
        return (False, "")
    if _match_spam(text):
        return (True, "内容包含违规外链/引流信息，已被拦截")
    mode = get_config("filter_mode", "replace")
    if mode == "block":
        hits = _match_words(text)
        if hits:
            return (True, "内容包含违规敏感词：" + "、".join(hits[:3]))
    return (False, "")

def filter_sensitive(text):
    """按当前模式过滤文本。
    replace：敏感词替换为 ****，正常发送
    warn：原样发送，调用方负责生成预警记录
    返回 (result, hit)"""
    if not text:
        return (text, False)
    mode = get_config("filter_mode", "replace")
    hits = _match_words(text)
    hit = bool(hits)
    if mode == "replace":
        result = text
        for w in hits:
            star = "*" * max(len(w), 1)
            if get_config("case_sensitive", "0") == "1":
                result = result.replace(w, star)
            else:
                result = re.sub(r'(?i)' + re.escape(w), star, result)
        return (result, hit)
    return (text, hit)

def _log_warning(user, scene, content):
    if not user:
        return
    db = get_db()
    db.execute(
        "INSERT INTO warning_logs (user_id, username, scene, content, created_at) VALUES (?,?,?,?,?)",
        (user['id'], user['username'], scene, content[:500], time.time())
    )
    db.commit()

_repeat_locks = {}
_repeat_guard_lock = threading.Lock()
REPEAT_WINDOW = 30.0

# ── 防刷屏：滑动窗口按 (场景, 用户) 计数，超限就拦 ──
_FLOOD_LOG = {}
_FLOOD_LOCK = threading.Lock()
FLOOD_RULES = {
    'chat':          (5, 10.0),   # 群聊：10 秒最多 5 条
    'group':         (5, 10.0),   # 群组：10 秒最多 5 条
    'post':          (3, 60.0),   # 发帖：60 秒最多 3 篇
    'forum_comment': (6, 30.0),   # 论坛回帖：30 秒最多 6 条
}


def flood_check(scope, uid, limit=None, window=None):
    """防刷屏。返回 (ok, wait_seconds)；超限时 wait 是建议等待秒数。"""
    if not uid:
        return True, 0
    lim, win = FLOOD_RULES.get(scope, (5, 10.0))
    if limit:
        lim = limit
    if window:
        win = window
    now = time.time()
    with _FLOOD_LOCK:
        if len(_FLOOD_LOG) > 4000:
            for k in [k for k, v in list(_FLOOD_LOG.items()) if not v or now - v[-1] > 600]:
                _FLOOD_LOG.pop(k, None)
        arr = [t for t in (_FLOOD_LOG.get((scope, uid)) or []) if now - t < win]
        if len(arr) >= lim:
            _FLOOD_LOG[(scope, uid)] = arr
            return False, max(1, int(win - (now - arr[0])) + 1)
        arr.append(now)
        _FLOOD_LOG[(scope, uid)] = arr
    return True, 0


def repeat_guard(scope, payload, window=REPEAT_WINDOW, uid=None):
    """同一用户 + 同一场景 + 同一份内容，window 秒内只放行第一次。
    返回 False 表示判定为连点产生的重复请求，调用方应直接跳过写库。"""
    if uid is None:
        u = current_user()
        uid = u['id'] if u else 0
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        raw = repr(payload)
    key = (uid, scope, hashlib.sha256(raw.encode('utf-8')).hexdigest())
    now = time.time()
    with _repeat_guard_lock:
        if len(_repeat_locks) > 2000:
            for k, ts in list(_repeat_locks.items()):
                if now - ts > window:
                    _repeat_locks.pop(k, None)
        last = _repeat_locks.get(key)
        if last is not None and now - last < window:
            return False
        _repeat_locks[key] = now
    return True

def repeat_release(scope, payload, uid=None):
    """操作最终失败（积分不足、条件不满足等）时把指纹撤掉，别让用户干等 30 秒。"""
    if uid is None:
        u = current_user()
        uid = u['id'] if u else 0
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        raw = repr(payload)
    key = (uid, scope, hashlib.sha256(raw.encode('utf-8')).hexdigest())
    with _repeat_guard_lock:
        _repeat_locks.pop(key, None)

def filter_and_block(content, user, scene):
    """统一处理用户文本：先拦截，再过滤/预警。
    返回 (result_text, error_reason_or_None)。
    error_reason 非空表示被拦截，调用方应中止发送。"""
    blocked, reason = content_blocked(content)
    if blocked:
        return ("", reason)
    filtered, hit = filter_sensitive(content)
    if hit and get_config("filter_mode", "replace") == "warn":
        _log_warning(user, scene, content)
    return (filtered, None)

def extract_mentions(text):
    """从文本中提取@的用户名"""
    if not text:
        return []
    pattern = r'@([\w\u4e00-\u9fa5]+)'
    mentions = re.findall(pattern, text)
    return list(set(mentions))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def ext_reject_msg():
    """上传被拒时说清楚「哪个格式不行」，别只丢一句「文件类型不支持」。"""
    up = request.files.get('file')
    name = (up.filename if up else '') or ''
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if not ext:
        return "这个文件没有扩展名，认不出类型。可上传：图片 / 视频 / 音频 / 文档 / 压缩包"
    return "不支持 .%s 格式。可上传：图片 / 视频 / 音频 / 文档 / 压缩包" % ext

def sanitize_display_name(filename):
    """保留原始文件名（含中文），仅去除路径与非法字符。
    注意：不能用 secure_filename，它会把中文全部剥掉，导致原名丢失。"""
    name = os.path.basename((filename or "").replace("\\", "/"))
    name = re.sub(r'[\r\n\t\x00<>:"/\\|?*]', "_", name).strip().strip(".")
    return name[:200] or "file"

def is_image(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in {'png', 'jpg', 'jpeg', 'gif'}

def is_video(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in {'mp4', 'avi', 'mov'}

_THUMB_LEVELS = ("s", "m")


def _pack_img_meta(info):
    if not info:
        return ''
    try:
        return json.dumps({
            "w": info.get("w"), "h": info.get("h"),
            "s": info.get("thumbs", {}).get("s", ""),
            "m": info.get("thumbs", {}).get("m", ""),
            "sw": info.get("s_w"), "sh": info.get("s_h"),
            "mw": info.get("m_w"), "mh": info.get("m_h"),
        }, separators=(",", ":"))
    except Exception:
        return ''


def _unpack_img_meta(raw):
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_upload_image(abs_path, rel_name):
    """图片落盘后统一处理：压长边 + 转 WebP + 两档缩略图。

    返回 (最终文件名, img_meta字符串)。Pillow 没装或处理失败时原样返回，
    上传永远不因为压缩而失败。文件名可能从 .jpg 变成 .webp，调用方必须用返回值。
    """
    final = rel_name
    try:
        info = imgproc.process(abs_path)
    except Exception:
        info = None
    if not info:
        return final, ''
    if info.get("full"):
        d, b = os.path.split(rel_name)
        final = (d + "/" if d else "") + info["full"]
    # 缩略图路径按 uploads 目录存全，渲染时不用再猜目录层级
    base_dir = os.path.dirname(rel_name).replace("\\", "/")
    thumbs = {}
    for tag in _THUMB_LEVELS:
        r = (info.get("thumbs") or {}).get(tag) or ""
        thumbs[tag] = (base_dir + "/" + r) if (r and base_dir) else r
    info = dict(info)
    info["thumbs"] = thumbs
    return final, _pack_img_meta(info)


def img_src(file_url, meta_raw, level="s"):
    """模板里渲染图片用这个：有缩略图走缩略图，没有走原图。"""
    if not file_url:
        return ""
    meta = _unpack_img_meta(meta_raw)
    rel = meta.get(level) or ""
    if not rel:
        return file_url
    base = file_url.split("?")[0]
    i = base.rfind("/uploads/")
    if i < 0:
        return file_url
    return base[:i + len("/uploads")] + "/" + rel


def img_size(meta_raw, level="s"):
    meta = _unpack_img_meta(meta_raw)
    if level == "s":
        w, h = meta.get("sw"), meta.get("sh")
    else:
        w, h = meta.get("mw"), meta.get("mh")
    try:
        return int(w), int(h)
    except (TypeError, ValueError):
        return 0, 0


_WAL_READY = False
_WAL_LOCK = threading.Lock()


def _tune_conn(db):
    """给单个 SQLite 连接套上并发参数。幂等，重复调用无副作用。

    - busy_timeout：拿不到锁时等待而不是立刻抛 database is locked
    - journal_mode=WAL：读写并发（需落盘，只需成功设置一次，之后新连接自动继承）
    - synchronous=NORMAL：WAL 下的推荐档位，掉电最多丢最后一次提交，写入快很多
    - foreign_keys=ON：开启外键约束（SQLite 默认是关的，属于隐患）
    - 049：set_trace_callback 请求级 SQL 收集（N+1 检测 + 慢请求按需 dump）
    """
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    try:
        db.set_trace_callback(_sql_trace)
    except Exception:
        pass
    global _WAL_READY
    if not _WAL_READY:
        with _WAL_LOCK:
            if not _WAL_READY:
                try:
                    _jm = (os.environ.get("MONO_JOURNAL_MODE") or "WAL").upper()
                    mode = db.execute("PRAGMA journal_mode=%s" % _jm).fetchone()
                    if mode and str(mode[0]).lower() == _jm.lower():
                        _WAL_READY = True
                except Exception:
                    pass
    return db


def get_db():
    if "db" not in g:
        g.db = _tune_conn(sqlite3.connect(DB_PATH))
        g.db.row_factory = sqlite3.Row
    return g.db


# ---------------- 049 慢查询 / N+1 检测 ----------------

_SQL_NORM_RE = re.compile(r"'[^']*'|\b\d+\b")
_NPLUS_THRESHOLD = 5          # 同请求内同模式 SQL 出现这么多次 → 疑似 N+1


def _sql_norm(sql):
    """SQL 归一化：字面量和数字变 ?，让同一模式的不同参数值撞出重复。"""
    return _SQL_NORM_RE.sub("?", (sql or "").strip())[:200]


def _sql_trace(sql):
    """sqlite trace 回调：g._sql_collect 非 None 时收集（请求级），否则零开销。"""
    col = getattr(g, "_sql_collect", None)
    if col is not None:
        try:
            col.append((_sql_norm(sql), time.time()))
        except Exception:
            pass


@app.before_request
def _sql_collect_on():
    """049：每个请求开一个 SQL 收集器（含 /api/track，list append 纳秒级）。"""
    try:
        if (request.path or "").startswith(("/static/", "/socket.io/")):
            g._sql_collect = None
        else:
            g._sql_collect = []
    except Exception:
        g._sql_collect = None


def _sql_analyze(path, cost_ms, col):
    """请求结束：N+1 检测 + 慢请求 dump 该请求的 SQL 序列。"""
    try:
        if not col:
            return
        seen = {}
        for sql, _ts in col:
            seen[sql] = seen.get(sql, 0) + 1
        for sql, n in seen.items():
            if n >= _NPLUS_THRESHOLD and not sql.upper().startswith(("PRAGMA", "BEGIN", "COMMIT")):
                log.warning("N+1? %s -> [%s] x%d", path, sql[:120], n)
        if cost_ms >= _SLOW_MS:
            log.warning("SLOWREQ %s %dms SQLx%d: %s", path, cost_ms, len(col),
                        " | ".join(s[:60] for s, _ in col[:30]))
    except Exception:
        pass

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    """⚠️⚠️ **一次性建库脚本，只能在空库上跑，别拿去当迁移用！**

    这个函数从来没有被调用过（也没人该调用它）。它里面是**非幂等**的：
    - 72 处**裸 `ALTER TABLE ADD COLUMN`**（没有存在性检查）→ 第二次跑直接崩 ✗
    - `UPDATE users SET buff_double_uses=buff_double_uses+1`（第 742 行附近）
      → **每跑一次就给所有用户白送一次双倍 buff** ✗✗
    - `UPDATE users SET deactivated=1 ...` / `DELETE FROM sensitive_words ...`
      → 可能误注销用户、误删敏感词 ✗
    - `INSERT INTO site_announcement (id, content) VALUES (1, '')` → 已存在则报错 ✗

    所以**绝对不能接回启动流程** ✗（2026-10-10 审计过，本来想接回去的，查完发现会炸站）

    **加表 / 加字段的正确做法**：
      1. 在下面的建表区照样写 `CREATE TABLE IF NOT EXISTS` / `_add_column`（给新装环境用 ✓）
      2. **另外写一个幂等迁移脚本**（先 `PRAGMA table_info` 判断再 ALTER ✓）
         —— 参考 `_trash_wip/ai_log_migrate.py`
      3. 本地 + 线上**各手动跑一次** ✓
    """
    db = _tune_conn(sqlite3.connect(DB_PATH))
    db.row_factory = sqlite3.Row
    # 护栏：库非空就拒绝执行，免得有人手滑把生产库跑崩 ✗
    try:
        _has = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if _has:
            _n = db.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
            if _n > 0:
                print('[init_db] 拒绝执行：users 表里已有 %d 行数据。'
                      '这是建库脚本，不是迁移脚本 —— 要改表结构请写幂等迁移。' % _n)
                db.close()
                return
    except Exception:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            avatar        TEXT DEFAULT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            has_effect    INTEGER NOT NULL DEFAULT 0,
            effect_type   TEXT DEFAULT NULL,
            banned_until  REAL NOT NULL DEFAULT 0,
            muted_until   REAL NOT NULL DEFAULT 0,
            created_at    REAL NOT NULL DEFAULT 0,
            last_active   REAL NOT NULL DEFAULT 0,
            badge_text    TEXT DEFAULT NULL,
            badge_effect  TEXT DEFAULT NULL
        )
    """)
    try:
        db.execute("ALTER TABLE users ADD COLUMN last_active REAL NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN badge_text TEXT DEFAULT NULL")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN badge_effect TEXT DEFAULT NULL")
    except:
        pass
    try:
        db.execute("ALTER TABLE messages ADD COLUMN read_at REAL NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE users SET onboarded=1 WHERE last_active > 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN deactivated INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("UPDATE users SET deactivated=1 "
                   "WHERE deactivated=0 AND username GLOB '用户[0-9a-z][0-9a-z][0-9a-z][0-9a-z][0-9a-z]'")
        db.commit()
    except Exception:
        log.exception("补注销标记失败")
    try:
        db.execute("ALTER TABLE users ADD COLUMN user_no TEXT DEFAULT NULL")
    except:
        pass
    try:
        db.execute("UPDATE users SET user_no='00001' WHERE id=1 AND (user_no IS NULL OR user_no='')")
        used = set(r[0] for r in db.execute(
            "SELECT user_no FROM users WHERE user_no IS NOT NULL AND user_no!=''").fetchall())
        rows = db.execute("SELECT id FROM users WHERE user_no IS NULL OR user_no='' ORDER BY id").fetchall()
        for r in rows:
            for _ in range(80):
                no = '%05d' % random.randint(10000, 99999)
                if no not in used:
                    used.add(no)
                    db.execute("UPDATE users SET user_no=? WHERE id=?", (no, r['id']))
                    break
        db.commit()
    except Exception:
        log.exception("补用户号失败")




    try:
        db.execute("ALTER TABLE users ADD COLUMN is_super_admin INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN signature TEXT DEFAULT ''")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN private_chat_perm INTEGER NOT NULL DEFAULT 1")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN exp INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN coins INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN mall_title TEXT DEFAULT ''")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN mall_badge TEXT DEFAULT ''")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN bubble_style TEXT DEFAULT ''")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN gacha_pity INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN buff_double_until REAL NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN buff_double_uses INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("UPDATE users SET buff_double_uses=buff_double_uses+1, buff_double_until=0 "
                   "WHERE buff_double_until>?", (time.time(),))
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN buff_exp_until REAL NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN custom_name_color TEXT DEFAULT ''")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN shield_until REAL NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN fortune_salt TEXT DEFAULT ''")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN ai_quota INTEGER NOT NULL DEFAULT 30")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN ai_quota_reset REAL NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN high_score_xianyi INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN high_score_hanoi INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE reversi_rooms ADD COLUMN move_hist TEXT NOT NULL DEFAULT ''")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN hanoi_best_moves INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN hanoi_best_ms INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN mine_best_ms_beginner INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN mine_best_ms_intermediate INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN mine_best_ms_expert INTEGER NOT NULL DEFAULT 0")
    except:
        pass

    db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT NOT NULL,
            content    TEXT NOT NULL,
            file_url   TEXT DEFAULT NULL,
            file_name  TEXT DEFAULT NULL,
            file_type  TEXT DEFAULT NULL,
            is_private INTEGER NOT NULL DEFAULT 0,
            receiver_id INTEGER DEFAULT NULL,
            created_at REAL NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        )
    """)
    try:
        db.execute("ALTER TABLE messages ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE messages ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE messages ADD COLUMN edit_history TEXT DEFAULT ''")
    except:
        pass
    
    db.execute("""
        CREATE TABLE IF NOT EXISTS friends (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            friend_id   INTEGER NOT NULL,
            status      INTEGER NOT NULL DEFAULT 0, -- 0: pending, 1: accepted
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, friend_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS friend_messages_sent (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            target_id   INTEGER NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, target_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            likes       INTEGER NOT NULL DEFAULT 0,
            is_pinned   INTEGER NOT NULL DEFAULT 0,
            is_locked   INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            summary     TEXT NOT NULL DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS post_likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(post_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS post_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  REAL NOT NULL
        )
    """)
    try:
        db.execute("ALTER TABLE post_comments ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE post_comments ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            type        TEXT NOT NULL,
            content     TEXT NOT NULL,
            link_url    TEXT DEFAULT NULL,
            is_read     INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS site_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS page_blocks (
            page       TEXT NOT NULL,
            key        TEXT NOT NULL,
            content    TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0,
            updated_by TEXT DEFAULT '',
            PRIMARY KEY (page, key)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ic_coders (
            user_id     INTEGER PRIMARY KEY,
            code        TEXT UNIQUE NOT NULL,
            fp_digest   TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    try:
        db.execute("ALTER TABLE ic_coders ADD COLUMN fp_digest TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS ic_invites (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id    INTEGER NOT NULL,
            invitee_id    INTEGER NOT NULL,
            code          TEXT NOT NULL DEFAULT '',
            reg_ip        TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending',
            day1          REAL NOT NULL DEFAULT 0,
            day2          REAL NOT NULL DEFAULT 0,
            streak_start  TEXT DEFAULT NULL,
            qualified_at  REAL DEFAULT NULL,
            reject_reason TEXT NOT NULL DEFAULT '',
            created_at    REAL NOT NULL DEFAULT 0,
            UNIQUE(invitee_id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ic_inv_inviter ON ic_invites(inviter_id, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ic_inv_ip ON ic_invites(reg_ip)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS ic_daily (
            user_id   INTEGER NOT NULL,
            day       TEXT NOT NULL,
            seconds   REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ic_daily_day ON ic_daily(day)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS ic_settlements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            period      TEXT NOT NULL DEFAULT '',
            valid_count INTEGER NOT NULL DEFAULT 0,
            amount      REAL NOT NULL DEFAULT 0,
            status      TEXT NOT NULL DEFAULT 'pending',
            note        TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL DEFAULT 0,
            updated_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, period)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ic_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS changelog (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL DEFAULT '',
            text       TEXT NOT NULL DEFAULT '',
            is_new     INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_games (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            red_id       INTEGER NOT NULL,
            black_id     INTEGER NOT NULL,
            state        TEXT NOT NULL DEFAULT '{}',
            status       TEXT NOT NULL DEFAULT 'playing',
            winner       TEXT,
            reason       TEXT DEFAULT '',
            created_at   REAL NOT NULL DEFAULT 0,
            updated_at   REAL NOT NULL DEFAULT 0,
            last_move_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_queue (
            user_id   INTEGER PRIMARY KEY,
            rating    INTEGER NOT NULL DEFAULT 1000,
            joined_at REAL NOT NULL DEFAULT 0,
            last_poll REAL NOT NULL DEFAULT 0,
            fp        TEXT NOT NULL DEFAULT '',
            ip        TEXT NOT NULL DEFAULT ''
        )
    """)
    try:
        db.execute("ALTER TABLE xqm_queue ADD COLUMN fp TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE xqm_queue ADD COLUMN ip TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_rating (
            user_id    INTEGER PRIMARY KEY,
            rating     INTEGER NOT NULL DEFAULT 1000,
            wins       INTEGER NOT NULL DEFAULT 0,
            losses     INTEGER NOT NULL DEFAULT 0,
            draws      INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_season (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL DEFAULT '',
            start_at   REAL NOT NULL DEFAULT 0,
            end_at     REAL NOT NULL DEFAULT 0,
            closed     INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_season_hist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id   INTEGER NOT NULL,
            season_name TEXT NOT NULL DEFAULT '',
            user_id     INTEGER NOT NULL,
            rating      INTEGER NOT NULL DEFAULT 0,
            wins        INTEGER NOT NULL DEFAULT 0,
            losses      INTEGER NOT NULL DEFAULT 0,
            draws       INTEGER NOT NULL DEFAULT 0,
            rank        INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS game_replays (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game        TEXT NOT NULL,
            sub         TEXT NOT NULL DEFAULT '',
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            score       INTEGER NOT NULL DEFAULT 0,
            kind        TEXT NOT NULL DEFAULT 'score',
            duration_ms INTEGER NOT NULL DEFAULT 0,
            frames      TEXT NOT NULL DEFAULT '[]',
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_gr_board ON game_replays(game, sub, score)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_lineup (
            user_id    INTEGER PRIMARY KEY,
            layout     TEXT NOT NULL DEFAULT '[]',
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_shards (
            user_id    INTEGER NOT NULL,
            type       TEXT NOT NULL,
            shards     INTEGER NOT NULL DEFAULT 0,
            level      INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, type)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xqm_wallet (
            user_id    INTEGER PRIMARY KEY,
            yizhu      INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    _add_column(db, "xqm_games", "loot", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "xqm_games", "log", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "xqm_games", "red_fp", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "xqm_games", "black_fp", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "xqm_games", "red_ip", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "xqm_games", "black_ip", "TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS idx_xqm_games_status ON xqm_games(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_xqm_queue_poll ON xqm_queue(last_poll)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS sensitive_words (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            word     TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT '默认'
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS horn_announcements (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT DEFAULT '',
            content    TEXT NOT NULL,
            link       TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
    """)
    try:
        db.execute("ALTER TABLE horn_announcements ADD COLUMN username TEXT DEFAULT ''")
    except Exception:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS music_tracks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT DEFAULT '',
            title      TEXT NOT NULL,
            artist     TEXT DEFAULT '',
            filename   TEXT NOT NULL,
            size       INTEGER DEFAULT 0,
            duration   REAL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)
    try:
        db.execute("ALTER TABLE music_tracks ADD COLUMN plays INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS music_playlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            track_id   INTEGER NOT NULL,
            position   INTEGER DEFAULT 0,
            added_at   REAL NOT NULL,
            UNIQUE(user_id, track_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS site_announcement (
            id           INTEGER PRIMARY KEY,
            content      TEXT DEFAULT '',
            updated_at   REAL DEFAULT 0,
            updated_by   INTEGER DEFAULT 0,
            pinned_until REAL DEFAULT 0
        )
    """)
    cur = db.execute("SELECT COUNT(*) AS c FROM site_announcement").fetchone()
    if cur['c'] == 0:
        db.execute("INSERT INTO site_announcement (id, content) VALUES (1, '')")
        db.commit()
    db.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER NOT NULL,
            admin_name  TEXT NOT NULL,
            action      TEXT NOT NULL,
            target      TEXT DEFAULT '',
            detail      TEXT DEFAULT '',
            created_at  REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL DEFAULT 0,
            username    TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL DEFAULT '',
            detail      TEXT NOT NULL DEFAULT '',
            ip          TEXT NOT NULL DEFAULT '',
            ua          TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(created_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            filename    TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            size        INTEGER NOT NULL DEFAULT 0,
            mime        TEXT DEFAULT '',
            share_token TEXT DEFAULT NULL,
            downloads   INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ufile_user ON user_files(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ufile_token ON user_files(share_token)")
    _add_column(db, "user_files", "share_expire", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "user_files", "share_pass", "TEXT NOT NULL DEFAULT ''")
    # AI 代码调试：剩余次数（初始 20，用完不自动恢复）
    _add_column(db, "users", "ai_debug_left", "INTEGER NOT NULL DEFAULT 5")
    _add_column(db, "users", "email", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "users", "email_verified", "INTEGER NOT NULL DEFAULT 0")
    db.execute("CREATE TABLE IF NOT EXISTS ai_debug_logs ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
               "problem TEXT, code TEXT, note TEXT, answer TEXT, model TEXT, "
               "created_at REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_aidbg_user ON ai_debug_logs(user_id)")
    # ── AI 用量明细（2026-10-10 加）────────────────────────────────
    # 以前只有「总次数 / 总 token / 按模型分布」的汇总 ✗ 看不到谁在什么时候
    # 问了什么、成没成功、花了多久 ✗ 这里把明细补齐。
    #
    # ⚠️ ai_logs 原来只由「对话助手」写，代码调试根本没进这张表 ✗
    #    所以后台看板的「AI 调用」是漏的 —— 现在两条路都写，用 module 区分。
    _add_column(db, "ai_logs", "module", "TEXT NOT NULL DEFAULT ''")      # chat / debug
    _add_column(db, "ai_logs", "mode", "TEXT NOT NULL DEFAULT ''")        # auto / fast / deep / debug
    _add_column(db, "ai_logs", "latency_ms", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "ai_logs", "ip", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "ai_logs", "question", "TEXT NOT NULL DEFAULT ''")    # 用户输入（截断存）
    _add_column(db, "ai_logs", "answer", "TEXT NOT NULL DEFAULT ''")      # 模型输出（截断存）
    # 调试那边原来只记成功、不记失败 ✗ 也没有 token / 耗时 ✗
    _add_column(db, "ai_debug_logs", "prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "ai_debug_logs", "completion_tokens", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "ai_debug_logs", "latency_ms", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "ai_debug_logs", "success", "INTEGER NOT NULL DEFAULT 1")
    _add_column(db, "ai_debug_logs", "error", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "ai_debug_logs", "ip", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "ai_debug_logs", "out_chars", "INTEGER NOT NULL DEFAULT 0")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ailog_created ON ai_logs(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ailog_module ON ai_logs(module)")
    db.execute("CREATE TABLE IF NOT EXISTS ai_redeem_codes ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, "
               "add_times INTEGER NOT NULL DEFAULT 0, max_uses INTEGER NOT NULL DEFAULT 0, "
               "used_count INTEGER NOT NULL DEFAULT 0, note TEXT DEFAULT '', created_at REAL)")
    # 053：兑换码改为充值余额（amount=元），补列（幂等）
    # ⚠️ 必须先 commit：SQLite 在有未提交事务时 ALTER TABLE 会报错，
    # 而 _add_column 是 try/except 吞异常的，不提交就永远补不上列。
    db.commit()
    for _c, _ddl in (("amount", "REAL NOT NULL DEFAULT 0"),
                     ("valid_from", "TEXT NOT NULL DEFAULT ''"),
                     ("valid_until", "TEXT NOT NULL DEFAULT ''")):
        _add_column(db, "ai_redeem_codes", _c, _ddl)
    db.commit()
    db.execute("CREATE TABLE IF NOT EXISTS ai_redeem_used ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, code_id INTEGER NOT NULL, "
               "user_id INTEGER NOT NULL, add_times INTEGER NOT NULL DEFAULT 0, "
               "created_at REAL)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_airedeem_once ON ai_redeem_used(code_id, user_id)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT NOT NULL DEFAULT '',
            name       TEXT NOT NULL,
            is_public  INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            track_id    INTEGER NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            added_at    REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_pl_user ON playlists(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_plt_pl ON playlist_tracks(playlist_id)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS punishment_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            username     TEXT NOT NULL,
            admin_id     INTEGER NOT NULL,
            admin_name   TEXT NOT NULL,
            ptype        TEXT NOT NULL,            -- ban / mute
            until        REAL NOT NULL,             -- PERMANENT(-1) 或时间戳
            duration_text TEXT DEFAULT '',
            reason        TEXT DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'effective',  -- effective / lifted / revoked
            lifted_at    REAL DEFAULT NULL,
            lift_reason   TEXT DEFAULT '',
            created_at   REAL NOT NULL DEFAULT 0
        )
    """)
    try:
        db.execute("ALTER TABLE punishment_records ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
    except:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS appeals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            punishment_id INTEGER NOT NULL,
            user_id       INTEGER NOT NULL,
            username      TEXT NOT NULL,
            reason        TEXT DEFAULT '',
            screenshot    TEXT DEFAULT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',  -- pending / upheld / revoked
            admin_note    TEXT DEFAULT '',
            reviewer_id   INTEGER DEFAULT NULL,
            reviewer_name TEXT DEFAULT '',
            reviewed_at   REAL DEFAULT NULL,
            created_at    REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id    INTEGER NOT NULL,
            reporter_name  TEXT NOT NULL,
            target_type    TEXT NOT NULL,   -- chat / private / post / comment
            target_id      INTEGER NOT NULL,
            snapshot       TEXT DEFAULT '',
            category       TEXT DEFAULT '',
            status         TEXT NOT NULL DEFAULT 'pending',  -- pending / ignored / punished / escalated
            handler_id     INTEGER DEFAULT NULL,
            handler_note   TEXT DEFAULT '',
            created_at     REAL NOT NULL,
            handled_at     REAL DEFAULT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS recall_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id   INTEGER NOT NULL,
            operator_name TEXT NOT NULL,
            message_id    INTEGER NOT NULL,
            scene         TEXT NOT NULL,   -- chat / private
            content       TEXT DEFAULT '',
            recalled_at   REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS warning_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            scene       TEXT NOT NULL,
            content     TEXT DEFAULT '',
            created_at  REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            is_closed    INTEGER NOT NULL DEFAULT 0,
            file_upload  INTEGER NOT NULL DEFAULT 1,
            all_muted    INTEGER NOT NULL DEFAULT 0,
            guest_readonly INTEGER NOT NULL DEFAULT 0,
            created_at   REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS forum_sections (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            description     TEXT DEFAULT '',
            sort_order     INTEGER NOT NULL DEFAULT 0,
            is_closed      INTEGER NOT NULL DEFAULT 0,
            is_hidden      INTEGER NOT NULL DEFAULT 0,
            is_announcement INTEGER NOT NULL DEFAULT 0,
            created_at     REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS post_tags (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id  INTEGER NOT NULL,
            tag      TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS post_views (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            post_id   INTEGER NOT NULL,
            created_at REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, post_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            blocked_id  INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, blocked_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS comment_likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(comment_id, user_id)
        )
    """)
    for stmt in [
        "ALTER TABLE messages ADD COLUMN room_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE messages ADD COLUMN quote_id INTEGER DEFAULT NULL",
        "ALTER TABLE messages ADD COLUMN mentions TEXT DEFAULT ''",
        "ALTER TABLE messages ADD COLUMN img_meta TEXT DEFAULT ''",
        "ALTER TABLE group_files ADD COLUMN img_meta TEXT DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN section_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE posts ADD COLUMN tags TEXT DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN is_essence INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN comment_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN last_reply_at REAL NOT NULL DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN edited INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN recycle INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE posts ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE post_comments ADD COLUMN parent_id INTEGER DEFAULT NULL",
        "ALTER TABLE post_comments ADD COLUMN like_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE post_comments ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN custom_title TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN total_likes INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            db.execute(stmt)
        except Exception:
            pass
    _seed_rooms(db)
    _seed_sections(db)
    try:
        db.execute("UPDATE messages SET room_id=1 WHERE room_id IS NULL OR room_id=0")
    except Exception:
        pass
    try:
        db.execute("UPDATE posts SET section_id=1 WHERE section_id IS NULL OR section_id=0")
    except Exception:
        pass
    _seed_config(db)
    _seed_sensitive(db)
    db.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            post_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, post_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS dynamics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL DEFAULT '',
            images      TEXT DEFAULT '',          -- JSON 数组：图片 URL
            visibility  TEXT NOT NULL DEFAULT 'public', -- public / friends / self
            post_id     INTEGER DEFAULT NULL,    -- 转发源动态 id
            repost_of   INTEGER DEFAULT NULL,
            created_at  REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS dynamic_likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dynamic_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(dynamic_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS dynamic_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dynamic_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     INTEGER NOT NULL,
            title       TEXT NOT NULL,
            mode        TEXT NOT NULL DEFAULT 'single', -- single / multi
            anonymous   INTEGER NOT NULL DEFAULT 0,
            deadline    REAL DEFAULT 0,              -- 0 表示永久
            closed      INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS poll_options (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id     INTEGER NOT NULL,
            text        TEXT NOT NULL,
            votes       INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS poll_votes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id     INTEGER NOT NULL,
            option_id   INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(poll_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            avatar        TEXT DEFAULT NULL,
            intro         TEXT DEFAULT '',
            category      TEXT DEFAULT '闲聊群',  -- 技术群/游戏群/闲聊群
            announcement  TEXT DEFAULT '',
            owner_id      INTEGER NOT NULL,
            join_mode     TEXT NOT NULL DEFAULT 'public', -- public / audit / private
            member_count  INTEGER NOT NULL DEFAULT 1,
            allow_file    INTEGER NOT NULL DEFAULT 1,
            allow_invite  INTEGER NOT NULL DEFAULT 1,
            all_muted     INTEGER NOT NULL DEFAULT 0,
            group_no      TEXT DEFAULT '',
            is_searchable INTEGER NOT NULL DEFAULT 1,
            created_at    REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            role        TEXT NOT NULL DEFAULT 'member', -- owner / admin / member / black
            muted_until  REAL NOT NULL DEFAULT 0,
            joined_at   REAL NOT NULL DEFAULT 0,
            group_nickname TEXT DEFAULT '',
            UNIQUE(group_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL DEFAULT '',
            file_url    TEXT DEFAULT NULL,
            file_name   TEXT DEFAULT NULL,
            file_type   TEXT DEFAULT NULL,
            is_system   INTEGER NOT NULL DEFAULT 0,
            is_deleted  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_join_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending', -- pending / approved / rejected
            message     TEXT DEFAULT '',
            reject_reason TEXT DEFAULT '',
            expired_at  REAL NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            cover       TEXT DEFAULT '',
            intro       TEXT DEFAULT '',
            category    TEXT DEFAULT '趣味闲聊', -- 技术活动/游戏活动/趣味闲聊
            mode        TEXT NOT NULL DEFAULT 'submission', -- submission / online
            status      TEXT NOT NULL DEFAULT 'upcoming', -- upcoming / ongoing / ended
            room_id     INTEGER DEFAULT NULL,
            start_time  REAL NOT NULL DEFAULT 0,
            end_time    REAL NOT NULL DEFAULT 0,
            created_by  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS activity_participants (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id   INTEGER NOT NULL,
            user_id       INTEGER NOT NULL,
            username      TEXT NOT NULL,
            submission    TEXT DEFAULT '',
            submission_url TEXT DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending', -- pending / approved / rejected / awarded
            award_exp     INTEGER NOT NULL DEFAULT 0,
            created_at    REAL NOT NULL DEFAULT 0
        )
    """)
    try:
        db.execute("ALTER TABLE groups ADD COLUMN group_no TEXT DEFAULT NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_no ON groups(group_no)")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE groups ADD COLUMN is_searchable INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE group_members ADD COLUMN group_nickname TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE group_join_requests ADD COLUMN message TEXT DEFAULT ''")
        db.execute("ALTER TABLE group_join_requests ADD COLUMN reject_reason TEXT DEFAULT ''")
        db.execute("ALTER TABLE group_join_requests ADD COLUMN expired_at REAL NOT NULL DEFAULT 0")
    except Exception:
        pass
    try:
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sensitive_words_word ON sensitive_words(word)")
        db.execute("""
            DELETE FROM sensitive_words WHERE id NOT IN (
                SELECT MIN(id) FROM sensitive_words GROUP BY word
            )
        """)
        db.commit()
    except Exception:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_announcements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            title       TEXT NOT NULL DEFAULT '',
            content     TEXT NOT NULL,
            require_read INTEGER NOT NULL DEFAULT 0,
            read_count  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_announcement_reads (
            announcement_id INTEGER NOT NULL,
            user_id         INTEGER NOT NULL,
            read_at         REAL NOT NULL DEFAULT 0,
            UNIQUE(announcement_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            file_name   TEXT NOT NULL,
            file_url    TEXT NOT NULL,
            file_type   TEXT DEFAULT '',
            file_size   INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_essence (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            message_id  INTEGER NOT NULL,
            operator_id INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(group_id, message_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sign_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            sign_date   TEXT NOT NULL,          -- 'YYYY-MM-DD'
            is_makeup   INTEGER NOT NULL DEFAULT 0,
            reward_exp  INTEGER NOT NULL DEFAULT 0,
            reward_coin INTEGER NOT NULL DEFAULT 0,
            bonus_coin  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, sign_date)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS points_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            delta       INTEGER NOT NULL,        -- 正加负减
            source_type TEXT NOT NULL DEFAULT '',
            reason      TEXT NOT NULL DEFAULT '',
            ref_id      TEXT DEFAULT NULL,       -- 关联业务 id
            admin_id    INTEGER DEFAULT NULL,    -- 人工操作留痕
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_points_log_user ON points_log(user_id, created_at)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            task_key TEXT NOT NULL,
            period   TEXT DEFAULT NULL,          -- daily=日期 / once / ach
            progress INTEGER NOT NULL DEFAULT 0,
            claimed  INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, task_key, period)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS mall_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,           -- title/bubble/effect/badge/consumable
            value       TEXT NOT NULL,           -- 生效值
            cost        INTEGER NOT NULL DEFAULT 0,
            icon        TEXT NOT NULL DEFAULT '🎁',
            desc        TEXT NOT NULL DEFAULT '',
            sort_order  INTEGER NOT NULL DEFAULT 0,
            limited     INTEGER NOT NULL DEFAULT 0,
            removable   INTEGER NOT NULL DEFAULT 1,
            gacha_only  INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            item_id    INTEGER NOT NULL,
            redeemed_at REAL NOT NULL DEFAULT 0,
            is_equipped INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, item_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS gacha_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            rarity     TEXT NOT NULL,
            prize      TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_consumables (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            count    INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, item_key)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS gomoku_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '五子棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,  -- 1=公开可随机加入
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 15x15 字符串
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            win_line TEXT DEFAULT NULL,  -- 5连坐标 JSON
            last_move_x INTEGER DEFAULT NULL,
            last_move_y INTEGER DEFAULT NULL,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS go_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '围棋房间',
            board_size INTEGER NOT NULL DEFAULT 19,  -- 9 / 13 / 19 路
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,  -- 1=公开可随机加入
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- N*N 字符串：. 空 / 1 黑 / 2 白
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            score_json TEXT DEFAULT NULL,  -- 结束时 {'black':..,'white':..,'komi':..}
            last_move_x INTEGER DEFAULT NULL,
            last_move_y INTEGER DEFAULT NULL,
            passes INTEGER NOT NULL DEFAULT 0,  -- 连续 pass 数（双方连续 pass 结束）
            prev_board TEXT DEFAULT NULL,  -- 上一手后的盘面（打劫检测）
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xq_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '象棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 9x10 棋子编码（90 字符）
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            last_move TEXT DEFAULT NULL,  -- "x1y1x2y2"
            uci_moves TEXT DEFAULT '',   -- Pikafish 棋谱（UCI 坐标，空格分隔）
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xq_puzzles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            fen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',      -- 数据来源：内置集合名 / '用户创作'
            diff INTEGER NOT NULL DEFAULT 2,      -- 难度 1~5（1 最简单）
            author_id INTEGER NOT NULL DEFAULT 0, -- 0=系统
            author_name TEXT NOT NULL DEFAULT '系统',
            intro TEXT NOT NULL DEFAULT '',
            best_uci TEXT NOT NULL DEFAULT '',    -- 引擎验证出的红方最优首着（提示用）
            mate_len INTEGER NOT NULL DEFAULT 0,  -- 引擎自对弈红方取胜的回合数（约）
            status TEXT NOT NULL DEFAULT 'approved',  -- approved=公开 / pending / rejected
            plays INTEGER NOT NULL DEFAULT 0,
            solves INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS xq_puzzle_done (
            user_id INTEGER NOT NULL,
            puzzle_id INTEGER NOT NULL,
            solved_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, puzzle_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS reversi_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '黑白棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 黑白棋仅 PvP（0）
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 8x8 字符串（'.'空 / '1'黑 / '2'白）
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            last_move TEXT DEFAULT NULL,  -- "x,y"
            stats_awarded INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS chess_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '国际象棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=PvP 1=PvAI(白=玩家,黑=Stockfish)
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',
            board_state TEXT NOT NULL DEFAULT '',  -- 当前 FEN
            uci_moves TEXT NOT NULL DEFAULT '',
            last_move TEXT DEFAULT NULL,
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            stats_awarded INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS jq_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '揭棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 揭棋：display|truth（各 90 字符）
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            last_move TEXT DEFAULT NULL,  -- "x1y1x2y2"
            uci_moves TEXT DEFAULT '',   -- 棋谱（UCI 坐标，空格分隔）
            move_text TEXT DEFAULT '[]', -- 中文着法记谱（JSON 数组）
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS consumable_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            item_key   TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS board_game_stats (
            user_id      INTEGER NOT NULL,
            game         TEXT NOT NULL,   -- 'gomoku' / 'go' / 'xq' / 'jq'
            wins         INTEGER NOT NULL DEFAULT 0,
            losses       INTEGER NOT NULL DEFAULT 0,
            draws        INTEGER NOT NULL DEFAULT 0,
            games        INTEGER NOT NULL DEFAULT 0,
            ai_best_steps INTEGER NOT NULL DEFAULT 0,  -- 与 AI 对局中人类所走最多手数（取单局最大）
            ai_games     INTEGER NOT NULL DEFAULT 0,
            ai_wins      INTEGER NOT NULL DEFAULT 0,   -- 战胜 AI 的次数
            PRIMARY KEY (user_id, game)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            role       TEXT NOT NULL,           -- 'user' / 'assistant' / 'system'
            content    TEXT NOT NULL,
            model      TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            model      TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            success    INTEGER NOT NULL DEFAULT 1,
            error      TEXT DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS kanban_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL DEFAULT '',
            content    TEXT NOT NULL DEFAULT '',
            author_id  INTEGER NOT NULL DEFAULT 0,
            author_name TEXT NOT NULL DEFAULT '',
            is_pinned  INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        )
    """)
    for _stmt in ("ALTER TABLE mall_items ADD COLUMN limited INTEGER NOT NULL DEFAULT 0",
                  "ALTER TABLE mall_items ADD COLUMN removable INTEGER NOT NULL DEFAULT 1"):
        try:
            db.execute(_stmt)
        except Exception:
            pass
    _seed_mall(db)
    _seed_changelog(db)
    try:
        db.execute("ALTER TABLE posts ADD COLUMN title_glow INTEGER NOT NULL DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE posts ADD COLUMN user_pin_until REAL NOT NULL DEFAULT 0")
    except:
        pass
    _migrate_group_schema(db)
    _CREATE_INDEXES = (
        "CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, is_private, id)",
        "CREATE INDEX IF NOT EXISTS idx_messages_priv ON messages(user_id, receiver_id, is_private)",
        "CREATE INDEX IF NOT EXISTS idx_posts_section ON posts(section_id, recycle, is_pinned, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_comments_post ON post_comments(post_id)",
        "CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read)",
        "CREATE INDEX IF NOT EXISTS idx_groupmsg_gid ON group_messages(group_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_blacklist_blocked ON blacklist(blocked_id)",
        "CREATE INDEX IF NOT EXISTS idx_blacklist_pair ON blacklist(user_id, blocked_id)",
        "CREATE INDEX IF NOT EXISTS idx_friends_a ON friends(user_id, friend_id)",
        "CREATE INDEX IF NOT EXISTS idx_friends_b ON friends(friend_id, user_id)",
        "CREATE INDEX IF NOT EXISTS idx_aihist_user ON ai_chat_history(user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_xqp_status ON xq_puzzles(status)",
    )
    for _idx_sql in _CREATE_INDEXES:
        try:
            db.execute(_idx_sql)
        except Exception:
            pass
    db.commit()
    db.close()

def _gen_user_no(db, tries=80):
    """生成一个没被占用的 5 位用户号。

    纯数字、固定 5 位（从 10000 起，不用 0 开头，免得看起来像验证码）。
    admin 的 00001 是特例，注册流程不会生成到它。
    """
    for _ in range(tries):
        no = '%05d' % random.randint(10000, 99999)
        if not db.execute("SELECT 1 FROM users WHERE user_no=?", (no,)).fetchone():
            return no
    return '%05d' % random.randint(10000, 99999)


_GAME_NAMES = {
    '/game/24': '24 点挑战',
    '/game/sudoku': '数独',
    '/game/klotski': '数字华容道',
    '/game/klotski_cn': '经典华容道',
    '/game/color_blind': '色盲派对',
    '/game/jump': '黑白跳一跳',
    '/game/xianyi': '仙弈破阵',
    '/game/hanoi': '汉诺塔',
    '/game/gomoku': '五子棋',
    '/game/go': '围棋',
    '/game/xq': '中国象棋',
    '/game/xq/puzzles': '象棋残局',
    '/game/chess': '国际象棋',
    '/game/reversi': '黑白棋',
    '/game/jq': '揭棋',
}
_game_log_seen = {}


def _track_game_page():
    """把「玩了哪个游戏」记进活动日志。

    只在进入游戏主页时记一次，同一用户同一游戏 **5 分钟内不重复记**
    —— 否则刷新几下日志就全是同一行。
    对局房间（/game/xxx/<rid>）不记，那边有轮询，会刷屏。
    """
    try:
        name = _GAME_NAMES.get(request.path)
        if not name:
            return
        me = current_user()
        if not me:
            return
        key = (me['id'], request.path)
        now = time.time()
        if now - _game_log_seen.get(key, 0) < 300:
            return
        _game_log_seen[key] = now
        if len(_game_log_seen) > 2000:
            _game_log_seen.clear()
            _game_log_seen[key] = now
        _activity('玩游戏', name, user=me)
    except Exception:
        pass


@app.before_request
def _activity_before():
    """进游戏页时记一条活动（内部有 5 分钟节流，刷新不会刷屏）。"""
    _track_game_page()


def _activity(action, detail='', user=None, ip=None):
    """记一条用户活动，供超管在「后台日志」里查看。

    设计原则：**fail-open** —— 写日志失败绝不能影响用户正在做的事。
    所以整个函数包在 try 里，连 commit 都不抛。
    """
    try:
        db = get_db()
        u = user if user is not None else current_user()
        db.execute(
            "INSERT INTO activity_log (user_id, username, action, detail, ip, ua, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (u['id'] if u else 0,
             (u['username'] if u else '') or '（未登录）',
             action,
             (detail or '')[:200],
             (ip or getattr(g, '_ip', '') or '')[:60],
             (request.headers.get('User-Agent') or '')[:140],
             time.time()))
        db.commit()
    except Exception:
        pass

def _migrate_group_schema(db):
    """为已存在的数据库补齐群组相关新表/列（CREATE TABLE IF NOT EXISTS 不会对已有表加列）。"""
    for col, ddl in (("group_no", "TEXT DEFAULT ''"),
                     ("is_searchable", "INTEGER NOT NULL DEFAULT 1")):
        _add_column(db, "groups", col, ddl)
    _add_column(db, "group_members", "group_nickname", "TEXT DEFAULT ''")
    for col, ddl in (("message", "TEXT DEFAULT ''"),
                     ("reject_reason", "TEXT DEFAULT ''"),
                     ("expired_at", "REAL NOT NULL DEFAULT 0")):
        _add_column(db, "group_join_requests", col, ddl)
    _add_column(db, "groups", "avatar", "TEXT DEFAULT NULL")
    # ⚠️ 这里以前是 `SET is_hidden=1`（每次启动都把官方公告区藏起来），
    # 结果管理员自己也没法取消隐藏 —— 公告区不出现在论坛板块列表里，
    # 发帖表单的板块下拉也就永远选不到它，等于**发布公告的入口没了** ✗
    # 2026-10-09 修：改成确保它可见（幂等）。
    db.execute("UPDATE forum_sections SET is_hidden=0 WHERE is_announcement=1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS oj_contests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            start_at    REAL NOT NULL DEFAULT 0,
            end_at      REAL NOT NULL DEFAULT 0,
            is_public   INTEGER NOT NULL DEFAULT 1,
            rule        TEXT NOT NULL DEFAULT 'ioi',
            created_by  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS oj_problems (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contest_id      INTEGER NOT NULL DEFAULT 0,
            title           TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            input_format    TEXT NOT NULL DEFAULT '',
            output_format   TEXT NOT NULL DEFAULT '',
            samples         TEXT NOT NULL DEFAULT '[]',
            hint            TEXT NOT NULL DEFAULT '',
            time_limit_ms   INTEGER NOT NULL DEFAULT 1000,
            memory_limit_mb INTEGER NOT NULL DEFAULT 256,
            test_count      INTEGER NOT NULL DEFAULT 0,
            created_by      INTEGER NOT NULL DEFAULT 0,
            created_at      REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS oj_testcases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_id  INTEGER NOT NULL,
            idx         INTEGER NOT NULL DEFAULT 1,
            input       TEXT NOT NULL DEFAULT '',
            expected    TEXT NOT NULL DEFAULT '',
            is_sample   INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS oj_submissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_id  INTEGER NOT NULL,
            contest_id  INTEGER NOT NULL DEFAULT 0,
            user_id     INTEGER NOT NULL,
            code        TEXT NOT NULL DEFAULT '',
            verdict     TEXT NOT NULL DEFAULT '',
            time_ms     INTEGER NOT NULL DEFAULT 0,
            memory_kb   INTEGER NOT NULL DEFAULT 0,
            passed      INTEGER NOT NULL DEFAULT 0,
            total       INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)
    _add_column(db, "oj_testcases", "time_limit_ms", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "oj_testcases", "memory_limit_mb", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "oj_testcases", "score", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "oj_submissions", "score", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "oj_submissions", "full_score", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "oj_submissions", "task_id", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "oj_submissions", "detail", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "oj_submissions", "lang", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "oj_problems", "difficulty", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "oj_problems", "tags", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "oj_contests", "rule", "TEXT NOT NULL DEFAULT 'ioi'")
    _add_column(db, "users", "oj_verified", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "users", "oj_verified_at", "REAL DEFAULT 0")
    _add_column(db, "users", "last_checkin", "TEXT DEFAULT ''")
    _add_column(db, "users", "checkin_streak", "INTEGER NOT NULL DEFAULT 0")
    for _gt in ("gomoku_rooms", "go_rooms", "xq_rooms", "jq_rooms",
                "chess_rooms", "reversi_rooms"):
        _add_column(db, _gt, "stats_awarded", "INTEGER NOT NULL DEFAULT 0")
        _add_column(db, _gt, "ai_name", "TEXT NOT NULL DEFAULT ''")
        _add_column(db, _gt, "ai_level", "INTEGER NOT NULL DEFAULT 0")
        # 匹配时记下的双方设备指纹 / IP，结算前用来判断这局是不是自己配自己
        _add_column(db, _gt, "owner_fp", "TEXT NOT NULL DEFAULT ''")
        _add_column(db, _gt, "guest_fp", "TEXT NOT NULL DEFAULT ''")
        _add_column(db, _gt, "owner_ip", "TEXT NOT NULL DEFAULT ''")
        _add_column(db, _gt, "guest_ip", "TEXT NOT NULL DEFAULT ''")
    db.execute("""
        CREATE TABLE IF NOT EXISTS board_game_stats (
            user_id      INTEGER NOT NULL,
            game         TEXT NOT NULL,
            wins         INTEGER NOT NULL DEFAULT 0,
            losses       INTEGER NOT NULL DEFAULT 0,
            draws        INTEGER NOT NULL DEFAULT 0,
            games        INTEGER NOT NULL DEFAULT 0,
            ai_best_steps INTEGER NOT NULL DEFAULT 0,
            ai_games     INTEGER NOT NULL DEFAULT 0,
            ai_wins      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, game)
        )
    """)
    _add_column(db, "board_game_stats", "ai_wins", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "board_game_stats", "rating", "REAL NOT NULL DEFAULT 0")
    _add_column(db, "jq_rooms", "prev_board", "TEXT NOT NULL DEFAULT ''")
    db.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            kind       TEXT NOT NULL DEFAULT 'idea',
            content    TEXT NOT NULL DEFAULT '',
            image      TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'open',
            reply      TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    try:
        rows = db.execute("SELECT id FROM groups WHERE group_no IS NULL OR group_no=''").fetchall()
        for r in rows:
            no = _generate_group_no()
            if no:
                db.execute("UPDATE groups SET group_no=? WHERE id=?", (no, r['id']))
    except Exception:
        pass

def _add_column(db, table, col, ddl):
    try:
        cols = [c['name'] for c in db.execute("PRAGMA table_info(%s)" % table).fetchall()]
        if col not in cols:
            db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ddl))
    except Exception:
        pass


_BOOT_MIGRATIONS = (
    # AI 广场个人中心：余额（元），新用户与存量用户初始 0.1
    ("users", "ai_balance", "REAL NOT NULL DEFAULT 0.1"),
    ("messages", "img_meta", "TEXT DEFAULT ''"),
    ("group_files", "img_meta", "TEXT DEFAULT ''"),
    ("users", "show_online", "INTEGER NOT NULL DEFAULT 1"),
)


def _ensure_analytics_tables():
    """运营分析用的表。幂等，每次启动跑一遍。"""
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS music_play_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            track_id   INTEGER NOT NULL,
            title      TEXT DEFAULT '',
            artist     TEXT DEFAULT '',
            played_ms  INTEGER NOT NULL DEFAULT 0,
            finished   INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_mpl_user ON music_play_logs(user_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_mpl_time ON music_play_logs(created_at)")
    db.commit()


def _boot_migrate():
    """启动时跑的幂等迁移 —— 只补列，不建表、不动数据。

    init_db() 是建库脚本，不能在生产上跑（会拒绝已有数据的库），
    所以新加的列必须在这里补，否则线上直接 500。
    """
    try:
        db = get_db()
        for table, col, ddl in _BOOT_MIGRATIONS:
            _add_column(db, table, col, ddl)
        db.commit()
    except Exception:
        pass

def _seed_rooms(db):
    """4 个固定公共聊天室，永久保留"""
    rooms = [
        (1, "综合大厅", "全站通用闲聊、日常交流、新人发言、自由水聊", 1),
        (2, "技术交流厅", "编程、建站、前端后端、服务器、软硬件、工具问题、技术求助", 2),
        (3, "游戏交流厅", "游戏攻略、联机招募、模组资源、游戏吐槽、开黑组队", 3),
        (4, "资源分享厅", "合规文档、教程、软件工具、学习资料分享（严控违规文件）", 4),
    ]
    for rid, name, desc, order in rooms:
        db.execute(
            "INSERT OR IGNORE INTO chat_rooms (id, name, description, sort_order, created_at) VALUES (?,?,?,?,?)",
            (rid, name, desc, order, time.time())
        )

def _seed_sections(db):
    """5 个固定论坛分区，永久保留"""
    sections = [
        (1, "综合闲聊区", "日常水帖、生活分享、趣味讨论、心情分享、无主题闲聊", 1, 0),
        (2, "技术讨论区", "编程开发、建站、前后端问题、服务器运维、软硬件、工具推荐、技术求助、教程分享", 2, 0),
        (3, "游戏板块", "游戏攻略、联机招募、开黑组队、模组资源、游戏评测、游戏问题求助", 3, 0),
        (4, "资源分享区", "合规文档、学习资料、软件工具、教程合集、免费资源分享（严格风控）", 4, 0),
        (5, "官方公告区", "仅管理员可发帖，普通用户禁止发帖/发评论；发布规则、更新、维护、活动、处罚公示", 5, 1),
    ]
    for sid, name, desc, order, ann in sections:
        db.execute(
            "INSERT OR IGNORE INTO forum_sections (id, name, description, sort_order, is_announcement, created_at) VALUES (?,?,?,?,?,?)",
            (sid, name, desc, order, ann, time.time())
        )

def _seed_config(db):
    defaults = {
        "filter_mode": "replace",
        "case_sensitive": "0",
        "fuzzy_match": "0",
        "spam_autoblock": "1",
        "rate_limit_count": "10",
        "rate_limit_window": "30",
    }
    for k, v in defaults.items():
        try:
            db.execute("INSERT OR IGNORE INTO site_config (key, value) VALUES (?, ?)", (k, v))
        except:
            pass

def _seed_sensitive(db):
    import os as _os
    txt = _os.path.join(BASE_DIR, "sensitive_words.txt")
    if _os.path.exists(txt):
        try:
            with open(txt, "r", encoding="utf-8") as f:
                words = set()
                for line in f:
                    w = line.strip()
                    if not w or w.startswith("#"):
                        continue
                    words.add(w)
                for w in words:
                    db.execute("INSERT OR IGNORE INTO sensitive_words (word, category) VALUES (?, '默认')", (w,))
        except:
            pass

def add_notification(user_id, notif_type, content, link_url=None):
    """添加系统通知。047：先查用户偏好，关闭的分类直接跳过（系统类不可关）。"""
    if _notif_pref_off(user_id, notif_type):
        return
    db = get_db()
    db.execute(
        "INSERT INTO notifications (user_id, type, content, link_url, is_read, created_at) VALUES (?,?,?,?,?,?)",
        (user_id, notif_type, content, link_url, 0, time.time())
    )
    db.commit()
    cache.invalidate_unread(user_id)   # 028：未读缓存失效
    try:
        count = db.execute(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id=? AND is_read=0",
            (user_id,)
        ).fetchone()['cnt']
        socketio.emit('notification', {
            'type': notif_type, 'content': content, 'link_url': link_url, 'count': count
        }, room='user_%d' % user_id)
    except Exception:
        pass

def broadcast_notification(notif_type, content, link_url=None):
    """向全站所有用户发送通知（官方公告 / 社区活动）"""
    db = get_db()
    users = db.execute("SELECT id FROM users").fetchall()
    now = time.time()
    for u in users:
        db.execute(
            "INSERT INTO notifications (user_id, type, content, link_url, is_read, created_at) VALUES (?,?,?,?,?,?)",
            (u['id'], notif_type, content, link_url, 0, now)
        )
    db.commit()
    cache.delete_prefix("unread:")   # 028：全员广播 → 所有未读缓存失效
    try:
        for u in users:
            socketio.emit('notification', {
                'type': notif_type, 'content': content, 'link_url': link_url, 'count': None
            }, room='user_%d' % u['id'])
    except Exception:
        pass

def update_user_active():
    """更新用户最后活跃时间"""
    user = current_user()
    if user:
        try:
            db = get_db()
            db.execute("UPDATE users SET last_active = ? WHERE id = ?", (time.time(), user['id']))
            db.commit()
        except Exception:
            pass

def is_user_online(user):
    """判断用户是否在线"""
    if not user:
        return False
    if not user['last_active']:
        return False
    return (time.time() - float(user['last_active'])) < ONLINE_THRESHOLD

LEVEL_TITLES = {
    1: "萌新", 2: "常驻居民", 3: "活跃用户", 4: "核心成员", 5: "社区大佬",
    6: "业界宗师", 7: "传奇人物", 8: "神话存在", 9: "至尊元老", 10: "社区之光",
}
LEVEL_EXP = [0, 50, 150, 350, 700, 1200, 2000, 3200, 5000, 8000]

def level_for_exp(exp):
    lv = 1
    for i, th in enumerate(LEVEL_EXP):
        if int(exp) >= th:
            lv = i + 1
    return min(lv, 10)

def get_level_title(level, custom=""):
    base = LEVEL_TITLES.get(int(level), "萌新")
    return (custom or base) if custom else base

def add_exp(user_id, delta, reason=None):
    """调整用户经验并重算等级，返回 (new_exp, new_level, leveled_up)。经验加速卡生效期间正经验翻倍。

    045：经验也是积分变动 —— 同步写 points_log（source_type='exp'），
    让流水完整覆盖「币 + 经验」两条线（审计报告能对账）。
    """
    db = get_db()
    u = db.execute("SELECT id,exp,level FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        return None
    final = int(delta)
    if delta > 0:
        try:
            bu = db.execute("SELECT buff_exp_until FROM users WHERE id=?", (user_id,)).fetchone()
            if bu and float(bu['buff_exp_until'] or 0) > time.time():
                final = delta * 2
        except Exception:
            pass
    new_exp = max(0, int(u['exp']) + final)
    new_level = level_for_exp(new_exp)
    leveled_up = new_level > int(u['level'])
    db.execute("UPDATE users SET exp=?, level=? WHERE id=?", (new_exp, new_level, user_id))
    try:
        _cur = db.execute(
            "INSERT INTO points_log (user_id, delta, source_type, reason, ref_id, admin_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, final, 'exp', (reason or '')[:60], None, None, time.time()))
        _pg_mirror_insert('points_log', _cur,
            "INSERT INTO points_log (user_id, delta, source_type, reason, ref_id, admin_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, final, 'exp', (reason or '')[:60], None, None, time.time()))
    except Exception:
        pass
    db.commit()
    if leveled_up:
        add_notification(user_id, 'levelup',
                        f'恭喜！你的等级提升至 {new_level} 级（{get_level_title(new_level)}）',
                        url_for('profile'))
    return (new_exp, new_level, leveled_up)

def add_total_likes(user_id, delta):
    db = get_db()
    db.execute("UPDATE users SET total_likes = MAX(0, total_likes + ?) WHERE id=?", (int(delta), user_id))
    db.commit()


DAILY_COIN_CAP = 200
SIGN_DAILY_BASE = 5
SIGN_MAKEUP_COST = 10
SIGN_MAKEUP_MAX_DAYS = 3
SIGN_EGG_PROB = 0.05
SIGN_EGG_RANGE = (10, 30)
SIGN_MONTH_FULL = 25
SIGN_MONTH_REWARD = 100
SIGN_STREAK_TABLE = [5, 5, 8, 10, 12, 15, 20]
GACHA_COST = 20
GACHA_PITY = 60

BEHAVIOR_COINS = {
    # 063 创作者激励：内容产出类奖励上调（原 post=8 / reply=5），
    # 让「写东西」比「聊天」明显更值钱，日上限 200 兜底防刷。
    'chat':  (1, 10),
    'dm':    (2, 5),
    'post':  (20, 2),      # 8 → 20
    'reply': (8, 3),       # 5 → 8
    'like':  (3, 5),
    'fav':   (3, 3),
}

TASK_DEFS = {
    'daily_chat':    ('今日发言', '在群聊发送 5 条消息', 5, 10, 'daily'),
    'daily_post':    ('今日发帖', '发布 1 篇帖子', 1, 8, 'daily'),
    'daily_comment': ('今日评论', '评论帖子 3 次', 3, 6, 'daily'),
    'daily_like':    ('今日点赞', '为帖子点赞 5 次', 5, 6, 'daily'),
    'daily_dm':      ('今日私信', '发送 1 条私信', 1, 5, 'daily'),
    'daily_fav':     ('今日收藏', '收藏帖子 3 次', 3, 6, 'daily'),
    'newbie_profile':      ('完善资料', '设置个人签名', 1, 30, 'newbie'),
    'newbie_first_post':   ('初来乍到', '发布第一篇帖子', 1, 40, 'newbie'),
    'newbie_join_group':   ('加入组织', '加入一个群组', 1, 20, 'newbie'),
}

# ── 成就定义（025）────────────────────────────────────────────
# (名称, 说明, 目标, 奖励, 进度SQL, 隐藏)
# 进度 SQL 用 ? 占位 uid，必须返回单列单值；写错会静默返回 0，务必逐条实测。
ACH_DEFS = {
    # —— 签到 ——
    'ach_sign_7':      ('小试牛刀', '累计签到 7 天', 7, 50, 'SELECT COUNT(*) FROM sign_logs WHERE user_id=?', 0),
    'ach_sign_30':     ('坚持不懈', '累计签到 30 天', 30, 150, 'SELECT COUNT(*) FROM sign_logs WHERE user_id=?', 0),
    'ach_sign_100':    ('签到王者', '累计签到 100 天', 100, 400, 'SELECT COUNT(*) FROM sign_logs WHERE user_id=?', 0),
    'ach_sign_streak30': ('月度全勤', '连续签到 30 天', 30, 200, 'SELECT IFNULL(checkin_streak,0) FROM users WHERE id=?', 0),
    # —— 创作 ——
    'ach_post_10':     ('活跃楼主', '累计发帖 10 篇', 10, 80, 'SELECT COUNT(*) FROM posts WHERE user_id=? AND is_deleted=0 AND recycle=0', 0),
    'ach_post_50':     ('帖海龙王', '累计发帖 50 篇', 50, 300, 'SELECT COUNT(*) FROM posts WHERE user_id=? AND is_deleted=0 AND recycle=0', 0),
    'ach_comment_50':  ('热心回复', '累计评论 50 次', 50, 150, 'SELECT COUNT(*) FROM post_comments WHERE user_id=? AND is_deleted=0', 0),
    'ach_like_100':    ('万人迷', '累计获赞 100 次', 100, 300, 'SELECT IFNULL(total_likes,0) FROM users WHERE id=?', 0),
    'ach_fav_20':      ('收藏家', '累计收藏 20 篇帖子', 20, 120, 'SELECT COUNT(*) FROM favorites WHERE user_id=?', 0),
    'ach_view_100':    ('潜水员', '浏览帖子 100 次', 100, 60, 'SELECT COUNT(*) FROM post_views WHERE user_id=?', 0),
    # —— 社交 ——
    'ach_friend_5':    ('广交朋友', '添加 5 位好友', 5, 100, 'SELECT COUNT(*) FROM friends WHERE user_id=? AND status=1', 0),
    'ach_dm_50':       ('话痨', '发送 50 条私信', 50, 140, 'SELECT COUNT(*) FROM messages WHERE user_id=? AND is_private=1', 0),
    'ach_group_join':  ('找到组织', '加入一个群组', 1, 30, 'SELECT COUNT(*) FROM group_members WHERE user_id=?', 0),
    'ach_group_post':  ('群内活跃', '在群里发言 30 条', 30, 110, 'SELECT COUNT(*) FROM group_messages WHERE user_id=? AND is_system=0', 0),
    # —— 游戏 ——
    'ach_game_any':    ('第一局', '玩任意一款小游戏', 1, 40, 'SELECT COUNT(*) FROM board_game_stats WHERE user_id=? AND games>0', 0),
    'ach_game_5':      ('游戏玩家', '在棋类对局中涉及 5 款游戏', 5, 130, 'SELECT COUNT(DISTINCT game) FROM board_game_stats WHERE user_id=?', 0),
    'ach_game_win':    ('首胜', '棋类对局获胜一次', 1, 60, 'SELECT IFNULL(SUM(wins),0) FROM board_game_stats WHERE user_id=?', 0),
    'ach_sudoku':      ('数独达人', '数独分数超过 300', 1, 80, 'SELECT COUNT(*) FROM users WHERE id=? AND IFNULL(high_score_sudoku,0)>=300', 0),
    'ach_klotski':     ('出口大师', '华容道任意一关通关', 1, 80, 'SELECT COUNT(*) FROM users WHERE id=? AND (best_time_klotski_4>0 OR best_time_klotski_5>0 OR best_time_klotski_6>0)', 0),
    'ach_hanoi3':     ('三阶不倒', '汉诺塔最少步数通关', 1, 120, 'SELECT COUNT(*) FROM users WHERE id=? AND IFNULL(hanoi_best_moves,999) BETWEEN 1 AND 7', 0),
    'ach_xqm_win':     ('棋坛一霸', '象棋大师对局获胜 10 场', 10, 160, 'SELECT IFNULL(wins,0) FROM xqm_rating WHERE user_id=?', 0),
    'ach_xq_puzzle':   ('残局高手', '解开 10 个象棋残局', 10, 180, 'SELECT COUNT(*) FROM xq_puzzle_done WHERE user_id=?', 0),
    # —— 隐藏（不显示在进度条，解锁时才揭晓）——
    'ach_hidden_night': ('夜猫子', '凌晨还在社区活跃', 1, 150,
        'SELECT COUNT(*) FROM activity_log WHERE user_id=? AND CAST(strftime(\'%H\', datetime(created_at,\'unixepoch\',\'+8 hours\')) AS INTEGER) >= 1 AND CAST(strftime(\'%H\', datetime(created_at,\'unixepoch\',\'+8 hours\')) AS INTEGER) <= 4', 1),
    'ach_hidden_early': ('守夜人', '清晨五点前就开始签到', 1, 150,
        'SELECT COUNT(*) FROM sign_logs WHERE user_id=? AND IFNULL(is_makeup,0)=0', 1),
    'ach_hidden_og':    ('老资格', '注册超过 365 天', 365, 300,
        'SELECT IFNULL(CAST((strftime(\'%s\',\'now\') - created_at)/86400 AS INTEGER),0) FROM users WHERE id=?', 1),
    'ach_hidden_all':   ('全成就', '解锁全部其他成就', 25, 500,
        'SELECT COUNT(*) FROM user_tasks WHERE user_id=? AND period=\'ach\' AND claimed=1', 1),
    'ach_hidden_speed': ('分秒必争', '任一计时类游戏进过前五', 1, 200,
        'SELECT COUNT(*) FROM users WHERE id=? AND (IFNULL(mine_best_ms_beginner,999999)<60000 OR IFNULL(mine_best_ms_expert,999999)<90000 OR IFNULL(hanoi_best_ms,999999)<120000)', 1),
}

for _k, (_n, _d, _t, _r, _s, _hid) in ACH_DEFS.items():
    TASK_DEFS[_k] = (_n, _d, _t, _r, 'achieve')
TASK_GROUPS = ('daily', 'newbie', 'achieve')
TASK_GROUP_LABELS = {'daily': '日常任务', 'newbie': '新手任务', 'achieve': '成就任务'}

SOURCE_LABELS = {
    'sign': '每日签到', 'sign_makeup': '补签', 'sign_month': '月度满签',
    'task': '任务奖励', 'mall': '商城兑换', 'gacha': '扭蛋抽奖',
    'blindbox': '盲盒', 'penalty': '违规扣除', 'admin': '管理员调整',
    'behavior_chat': '发言奖励', 'behavior_dm': '私信奖励', 'behavior_post': '发帖奖励',
    'behavior_reply': '评论奖励', 'behavior_like': '点赞奖励', 'behavior_fav': '收藏奖励',
    'achievement': '成就解锁', 'rank': '榜单奖励',
}

TITLE_COLORS = {
    '暮川寻客': '#16a085', '代码溯游': '#2e86de', '荒野行舟': '#d35400',
    '旧序史官': '#8e44ad', '临岸观潮': '#4b6584', '长夜守序': '#2c3e50',
    '拓荒先驱': '#c0392b', '明理思辨': '#27ae60', '初代住民': '#b9770e',
    '天选之人': '#b8860b',
}

MALL_SEED = [
    ("头衔·暮川寻客", "title", "暮川寻客", 70, "🏅", "挂在名字旁的专属头衔", 1, 0, 1, 0),
    ("头衔·代码溯游", "title", "代码溯游", 80, "🏅", "挂在名字旁的专属头衔", 2, 0, 1, 0),
    ("头衔·荒野行舟", "title", "荒野行舟", 90, "🏅", "挂在名字旁的专属头衔", 3, 0, 1, 0),
    ("头衔·旧序史官", "title", "旧序史官", 140, "👑", "资深长期活跃用户荣耀头衔", 4, 0, 1, 0),
    ("头衔·临岸观潮", "title", "临岸观潮", 100, "🏅", "挂在名字旁的专属头衔", 5, 0, 1, 0),
    ("头衔·长夜守序", "title", "长夜守序", 150, "👑", "长期活跃用户专属头衔", 6, 0, 1, 0),
    ("头衔·拓荒先驱", "title", "拓荒先驱", 180, "🔱", "社区早期参与者荣耀头衔", 7, 0, 1, 0),
    ("头衔·明理思辨", "title", "明理思辨", 110, "🏅", "理性交流爱好者专属头衔", 8, 0, 1, 0),
    ("头衔·初代住民【限定】", "title", "初代住民", 0, "🌟", "仅限注册前 10 名用户获取，商城不可兑换", 9, 1, 1, 0),
    ("头衔·天选之人【扭蛋限定】", "title", "天选之人", 0, "🎰", "扭蛋机传说级限定头衔，仅能通过扭蛋抽出（含 60 抽保底）", 10, 1, 1, 0),
    ("徽章·晨迹", "badge", "silver|晨迹", 90, "🥈", "名字旁浅银简约徽章", 11, 0, 1, 0),
    ("徽章·久驻", "badge", "blue|久驻", 110, "🔵", "名字旁环形纪念徽章", 12, 0, 1, 0),
    ("徽章·拾字", "badge", "green|拾字", 120, "📖", "名字旁书页线条徽章", 13, 0, 1, 0),
    ("徽章·星轨", "badge", "indigo|星轨", 130, "🌌", "名字旁深空线条徽章", 14, 0, 1, 0),
    ("徽章·静思", "badge", "teal|静思", 100, "🧘", "名字旁平衡简约徽章", 15, 0, 1, 0),
    ("徽章·远途", "badge", "orange|远途", 95, "🧭", "名字旁箭头标识徽章", 16, 0, 1, 0),
    ("徽章·云笺", "badge", "cyan|云笺", 125, "✉️", "名字旁信纸造型徽章", 17, 0, 1, 0),
    ("徽章·白烛", "badge", "gray|白烛", 135, "🕯️", "名字旁烛火简约徽章", 18, 0, 1, 0),
    ("徽章·岸屿", "badge", "navy|岸屿", 140, "🏝️", "名字旁孤岛线条徽章", 19, 0, 1, 0),
    ("徽章·时序", "badge", "purple|时序", 150, "🕰️", "名字旁时钟纪念徽章", 20, 0, 1, 0),
    ("气泡·素简", "bubble", "plain", 35, "⬜", "极简直角简约聊天气泡", 21, 0, 1, 0),
    ("气泡·浅弧", "bubble", "soft", 45, "🔘", "柔和小弧度基础气泡", 22, 0, 1, 0),
    ("气泡·墨边", "bubble", "ink", 60, "✒️", "细描边黑白线条气泡", 23, 0, 1, 0),
    ("气泡·星芒", "bubble", "star", 75, "💫", "边角点缀星光气泡", 24, 0, 1, 0),
    ("气泡·渡川", "bubble", "wave", 85, "🌊", "波纹装饰侧边气泡", 25, 0, 1, 0),
    ("气泡·霜序", "bubble", "frost", 95, "❄️", "冷调雅致装饰气泡", 26, 0, 1, 0),
    ("气泡·管理", "bubble", "admin", 0, "🔰", "管理员专属聊天气泡·仅管理员可装备，金蓝辉光", 27, 0, 1, 0),
    ("气泡·至尊", "bubble", "super", 0, "👑", "超级管理员专属聊天气泡·仅超管可装备", 28, 0, 1, 0),
    ("气泡·星河【扭蛋限定】", "bubble", "galaxy", 0, "🌌", "扭蛋机限定聊天气泡·深空星河流光效果", 29, 1, 1, 0),
    ("气泡·纯色", "bubble", "solid", 50, "💬", "默认纯色圆角气泡", 30, 0, 1, 0),
    ("气泡·线条", "bubble", "line", 80, "💬", "简约线条气泡", 31, 0, 1, 0),
    ("气泡·无", "bubble", "none", 30, "💬", "无气泡，纯文字消息", 32, 0, 1, 0),
    ("特效·环光", "effect", "halo", 80, "💫", "头像外围柔和光环特效", 33, 0, 1, 0),
    ("特效·流萤", "effect", "firefly", 95, "🪰", "细碎光点缓慢浮动特效", 34, 0, 1, 0),
    ("特效·月晕", "effect", "moon", 110, "🌙", "朦胧月相环绕头像特效", 35, 0, 1, 0),
    ("特效·风纹", "effect", "wind", 120, "🍃", "流动线条环绕头像特效", 36, 0, 1, 0),
    ("特效·星环", "effect", "starring", 130, "🌟", "双层旋转星光圆环特效", 37, 0, 1, 0),
    ("特效·时序流光", "effect", "flow", 145, "🌈", "渐变循环光影头像特效", 38, 0, 1, 0),
    ("特效·极光", "effect", "aurora", 200, "✨", "头像极光渐变", 39, 0, 1, 0),
    ("特效·星海", "effect", "galaxy", 220, "🌌", "深空星海环绕头像特效", 40, 0, 1, 0),
    ("特效·流星拖尾【扭蛋限定】", "effect", "meteor", 0, "☄️", "扭蛋机限定头像特效·流星划过拖尾光效", 41, 1, 1, 0),
    ("道具·补签笺", "consumable", "makeup_card", 50, "🗓️", "使用后可免积分补签最近 7 日内任意一天，恢复连续签到天数（每月限用 5 张）", 51, 0, 1, 0),
    ("道具·运势重阅券", "consumable", "fortune_reroll", 50, "🔮", "当天已签到后，重新调取一份全新运势（每天仅限重抽 1 次）", 52, 0, 1, 0),
    ("道具·无痕撤回卡", "consumable", "recall_card", 50, "🫥", "撤回已超时限的聊天 / 私信消息，不留撤回记录（超时撤回时自动消耗）", 53, 0, 1, 0),
    ("道具·MONO 盲盒券", "consumable", "blindbox", 30, "🎁", "开启盲盒随机获得 10~100 积分，欧皇可开出 50 / 100 大奖", 54, 0, 1, 0),
    ("道具·标题高亮笺", "consumable", "title_glow", 100, "✨", "为自己的一篇帖子标题启用特殊高亮样式，社区内突出显示", 55, 0, 1, 0),
    ("道具·置顶卡", "consumable", "pin_card", 180, "📌", "将自己发布的帖子置顶 7 天，列表优先展示", 56, 0, 1, 0),
    ("道具·全域传声筒", "consumable", "horn", 60, "📢", "全站顶部横幅滚动广播一条消息（≤100 字，可附一条跳转链接），全部在线用户可见", 57, 0, 1, 0),
    ("特权·双倍积分卡", "consumable", "double_coins_card", 0, "💎", "使用后下一次获得的积分翻倍（仅生效一次），在背包中使用", 58, 0, 1, 1),
    ("特权·经验加速卡", "consumable", "exp_boost", 0, "⚡", "激活后 24 小时内经验值获取翻倍，快速升级利器", 59, 0, 1, 1),
    ("特权·个性昵称色卡", "consumable", "name_color_card", 0, "🎨", "激活后 7 天内可自定义名字旁的显示颜色（输入 hex 色值如 #ff6b6b）", 60, 0, 1, 1),
    ("特权·护盾卡", "consumable", "shield_card", 0, "🛡️", "激活后 24 小时内可免疫一次禁言处罚（自动消耗），到期未触发则作废", 61, 0, 1, 1),
    ("道具·改名卡", "consumable", "rename_card", 30, "✏️", "修改一次社区昵称（2~12 字，中文/字母/数字/下划线），历史发言会同步更新", 62, 0, 1, 0),
]

GACHA_POOL = [
    ("common",    45, ["5~19 积分"]),
    ("uncommon",  40, ["20~29 积分"]),
    ("rare",      10, ["30 积分", "50 积分", "双倍积分卡", "经验加速卡"]),
    ("epic",       4, ["80 积分", "个性昵称色卡", "护盾卡"]),
    ("legendary",  1, ["头衔·天选之人", "气泡·星河", "特效·流星拖尾"]),
]
GACHA_RARITY_LABELS = {'common': '普通', 'uncommon': '优良', 'rare': '稀有', 'epic': '史诗', 'legendary': '传说'}
GACHA_RARITY_WEIGHT = {'common': 60, 'uncommon': 25, 'rare': 10, 'epic': 4, 'legendary': 1}
GACHA_LEGEND_TITLE = "天选之人"


def _seed_mall(db):
    """商城种子商品（幂等：存在则更新，多余则清理，被持有者保留）"""
    for row in MALL_SEED:
        name, typ, val, cost, icon, desc, so, limited, removable, gacha_only = row
        exists = db.execute("SELECT id FROM mall_items WHERE type=? AND value=?", (typ, val)).fetchone()
        if exists:
            db.execute("UPDATE mall_items SET name=?, cost=?, icon=?, desc=?, sort_order=?, limited=?, removable=?, gacha_only=? WHERE id=?",
                       (name, cost, icon, desc, so, limited, removable, gacha_only, exists[0]))
        else:
            db.execute("INSERT INTO mall_items (name, type, value, cost, icon, desc, sort_order, limited, removable, gacha_only) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (name, typ, val, cost, icon, desc, so, limited, removable, gacha_only))
    valid = set((r[1], r[2]) for r in MALL_SEED)
    for it in db.execute("SELECT id, type, value FROM mall_items").fetchall():
        if (it[1], it[2]) not in valid:
            used = db.execute("SELECT 1 FROM user_items WHERE item_id=?", (it[0],)).fetchone()
            if not used:
                db.execute("DELETE FROM mall_items WHERE id=?", (it[0],))
    db.commit()


def get_coins(uid):
    row = get_db().execute("SELECT coins FROM users WHERE id=?", (uid,)).fetchone()
    return int(row['coins']) if row else 0


def award(user_id, delta, source_type, reason, ref_id=None, admin_id=None, idem_key=None):
    """积分唯一入口：正加负减，余额与流水同事务。双倍积分卡对正积分生效一次。

    045：idem_key 幂等键 —— 同用户同键只入账一次（网络重试/重复回调场景），
    命中幂等时不动余额、原样返回当前积分。

    2026-11-01：流水记的是「**实际生效**的变动」，不是「请求的变动」。
    余额最低夹到 0，所以「余额 5 花 10」只会扣 5 —— 流水也必须记 -5，
    否则余额与流水永久对不上（对账页会把它当成「有人直接改库」报出来）。
    """
    if not delta:
        return get_coins(user_id)
    db = get_db()
    try:
        u = db.execute("SELECT id, coins, buff_double_uses FROM users WHERE id=?", (user_id,)).fetchone()
    except sqlite3.OperationalError:
        u = db.execute("SELECT id, coins FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        return 0
    d = int(delta)
    used_buff = 0
    if d > 0 and source_type not in ('penalty', 'admin'):
        try:
            left = int(u['buff_double_uses'] or 0)
        except (IndexError, KeyError, TypeError, ValueError):
            left = 0
        if left > 0:
            d = d * 2
            used_buff = 1
    # 2026-11-01：余额夹到 0，所以「请求的变动 d」和「实际生效的 applied」可能不同。
    # 流水必须记 applied，否则「余额 5 花 10」会变成「余额 0、流水 -10」，永久差 5。
    old_coins = int(u['coins'])
    new_coins = max(0, old_coins + d)
    applied = new_coins - old_coins
    if applied == 0:
        return old_coins                 # 余额已是 0 还要扣 → 什么都没发生，不记流水

    # 045 幂等：先试插入流水，键冲突 → 整个入账作废（余额不动）
    if idem_key:
        try:
            cur = db.execute(
                "INSERT OR IGNORE INTO points_log "
                "(user_id, delta, source_type, reason, ref_id, admin_id, created_at, idem_key) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (user_id, applied, source_type, reason or "", str(ref_id) if ref_id else None,
                 admin_id, time.time(), str(idem_key)[:80]))
            db.commit()
            if cur.rowcount == 0:
                return get_coins(user_id)   # 幂等命中：已入账过
            _pg_mirror_insert('points_log', cur,
                "INSERT OR IGNORE INTO points_log "
                "(user_id, delta, source_type, reason, ref_id, admin_id, created_at, idem_key) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (user_id, applied, source_type, reason or "", str(ref_id) if ref_id else None,
                 admin_id, time.time(), str(idem_key)[:80]))
        except sqlite3.OperationalError:
            idem_key = None                  # 老库没这列 → 走普通路径
    db.execute("UPDATE users SET coins=? WHERE id=?", (new_coins, user_id))
    if used_buff:
        try:
            db.execute("UPDATE users SET buff_double_uses=MAX(0, buff_double_uses-1) WHERE id=?",
                       (user_id,))
        except Exception:
            pass
    if not idem_key:
        _cur = db.execute(
            "INSERT INTO points_log (user_id, delta, source_type, reason, ref_id, admin_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, applied, source_type, reason or "", str(ref_id) if ref_id else None, admin_id, time.time()))
        _pg_mirror_insert('points_log', _cur,
            "INSERT INTO points_log (user_id, delta, source_type, reason, ref_id, admin_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, applied, source_type, reason or "", str(ref_id) if ref_id else None, admin_id, time.time()))
    db.commit()
    return new_coins


# ── 063 创作者激励：贡献值与等级 ──
# 与积分（coins）分离：积分能花，贡献值只衡量「你为社区产出了多少」。
CREATOR_LEVELS = [
    (0,    "新芽", "🌱"),
    (100,  "活跃", "🌿"),
    (500,  "优质", "🌳"),
    (2000, "核心", "⭐"),
    (5000, "元老", "👑"),
]


def creator_stats(uid):
    """算一个人的创作贡献：发帖 / 获赞 / 被评论 / 精华。

    贡献值 = 发帖×10 + 获赞×3 + 被评论×2 + 精华×50
    一条 SQL 聚合，个人主页每次请求算一次（有索引，量级小没问题）。
    """
    empty = {"posts": 0, "likes": 0, "comments": 0, "essence": 0,
             "contribution": 0, "level": 1, "level_name": CREATOR_LEVELS[0][1],
             "level_icon": CREATOR_LEVELS[0][2], "next_at": CREATOR_LEVELS[1][0],
             "to_next": CREATOR_LEVELS[1][0], "next_name": CREATOR_LEVELS[1][1]}
    try:
        db = get_db()
        r = db.execute(
            "SELECT "
            " (SELECT COUNT(*) FROM posts WHERE user_id=? "
            "   AND COALESCE(is_deleted,0)=0 AND COALESCE(recycle,0)=0) AS posts, "
            " (SELECT COALESCE(SUM(likes),0) FROM posts WHERE user_id=? "
            "   AND COALESCE(is_deleted,0)=0) AS likes, "
            " (SELECT COUNT(*) FROM post_comments c JOIN posts p ON p.id=c.post_id "
            "   WHERE p.user_id=? AND COALESCE(c.is_deleted,0)=0 "
            "     AND c.user_id != p.user_id) AS comments, "
            " (SELECT COUNT(*) FROM posts WHERE user_id=? AND COALESCE(is_essence,0)=1 "
            "   AND COALESCE(is_deleted,0)=0) AS essence",
            (uid, uid, uid, uid)).fetchone()
    except Exception:
        return empty

    posts = r["posts"] or 0
    likes = r["likes"] or 0
    comments = r["comments"] or 0
    essence = r["essence"] or 0
    contrib = posts * 10 + likes * 3 + comments * 2 + essence * 50

    lv = 1
    name, icon = CREATOR_LEVELS[0][1], CREATOR_LEVELS[0][2]
    next_at = CREATOR_LEVELS[1][0]
    for i, (need, nm, ic) in enumerate(CREATOR_LEVELS):
        if contrib >= need:
            lv, name, icon = i + 1, nm, ic
            next_at = CREATOR_LEVELS[i + 1][0] if i + 1 < len(CREATOR_LEVELS) else need
    return {"posts": posts, "likes": likes, "comments": comments, "essence": essence,
            "contribution": contrib, "level": lv, "level_name": name, "level_icon": icon,
            "next_at": next_at, "to_next": max(0, next_at - contrib),
            "next_name": (CREATOR_LEVELS[lv][1] if lv < len(CREATOR_LEVELS) else "")}


def behavior_points(uid, key):
    """日常行为积分：单次奖励 + 每日次数上限 + 全局日硬上限，返回实际发放值"""
    if key not in BEHAVIOR_COINS:
        return 0
    coin, cap = BEHAVIOR_COINS[key]
    db = get_db()
    today = _today_str()
    cnt = db.execute(
        "SELECT COUNT(*) c FROM points_log WHERE user_id=? AND source_type=? "
        "AND substr(datetime(created_at,'unixepoch','localtime'),1,10)=?",
        (uid, 'behavior_' + key, today)).fetchone()['c']
    if cnt >= cap:
        return 0
    earned = db.execute(
        "SELECT COALESCE(SUM(delta),0) s FROM points_log WHERE user_id=? AND delta>0 "
        "AND substr(datetime(created_at,'unixepoch','localtime'),1,10)=?",
        (uid, today)).fetchone()['s']
    if int(earned) >= DAILY_COIN_CAP:
        return 0
    award(uid, coin, 'behavior_' + key, SOURCE_LABELS.get('behavior_' + key, '日常行为'))
    return coin


def _is_water_reply(text):
    """水帖过滤：过短 / 连续重复字符 / 纯符号刷屏 视为无效评论，不发奖励"""
    t = (text or '').strip()
    if len(t) < 4:
        return True
    if re.search(r'(.)\1{6,}', t):
        return True
    if re.fullmatch(r'[\s!?。，,.~～:：;；()（）\[\]【】*#]+', t):
        return True
    return False


def _sign_dates(uid):
    rows = get_db().execute("SELECT sign_date FROM sign_logs WHERE user_id=?", (uid,)).fetchall()
    return set(r[0] for r in rows)


def sign_streak(uid, date=None):
    """从 date 往回数连续签到天数（补签自动续上）"""
    if date is None:
        date = _today_str()
    from datetime import date as _d, timedelta
    dates = _sign_dates(uid)
    cur = _d.fromisoformat(date)
    streak = 0
    while cur.isoformat() in dates:
        streak += 1
        cur -= timedelta(days=1)
    return streak


def sign_state(uid):
    db = get_db()
    today = _today_str()
    signed_today = bool(db.execute("SELECT 1 FROM sign_logs WHERE user_id=? AND sign_date=?", (uid, today)).fetchone())
    total = db.execute("SELECT COUNT(*) c FROM sign_logs WHERE user_id=?", (uid,)).fetchone()['c']
    streak = sign_streak(uid)
    month = today[:7]
    month_days = db.execute(
        "SELECT COUNT(*) c FROM sign_logs WHERE user_id=? AND substr(sign_date,1,7)=?", (uid, month)).fetchone()['c']
    month_full = month_days >= SIGN_MONTH_FULL
    month_rewarded = bool(db.execute(
        "SELECT 1 FROM points_log WHERE user_id=? AND source_type='sign_month' AND reason LIKE ? LIMIT 1",
        (uid, month + '%')).fetchone())
    next_reward = SIGN_STREAK_TABLE[(streak % len(SIGN_STREAK_TABLE))]
    makeup_available = []
    from datetime import date as _d, timedelta
    dates = _sign_dates(uid)
    for i in range(1, SIGN_MAKEUP_MAX_DAYS + 1):
        d = (_d.fromisoformat(today) - timedelta(days=i)).isoformat()
        if d not in dates:
            makeup_available.append(d)
    now_d = _d.fromisoformat(today)
    monday = now_d - timedelta(days=now_d.weekday())
    sign_week = [(monday + timedelta(days=i)).isoformat() for i in range(7)
                 if (monday + timedelta(days=i)).isoformat() in dates]
    return {
        'signed_today': signed_today, 'total_days': total, 'streak': streak,
        'month_days': month_days, 'month_full': month_full, 'month_rewarded': month_rewarded,
        'month_reward': SIGN_MONTH_REWARD, 'daily_base': SIGN_DAILY_BASE,
        'next_reward': next_reward, 'makeup_available': makeup_available, 'sign_week': sign_week,
        'fortune': sign_fortune(uid), 'fortune_label': _fortune_label(sign_fortune(uid)),
    }


def sign_fortune(uid):
    """今日运势（1-100）：基于 uid + 日期（或 fortune_salt）稳定哈希。"""
    u = get_db().execute("SELECT fortune_salt FROM users WHERE id=?", (uid,)).fetchone()
    base_salt = (u['fortune_salt'] or _today_str()) if u else _today_str()
    h = int(hashlib.md5(('%s|%s' % (uid, base_salt)).encode('utf-8')).hexdigest()[:8], 16)
    return (h % 100) + 1


def _fortune_label(f):
    if f >= 95: return "鸿运当头"
    if f >= 80: return "大吉"
    if f >= 60: return "中吉"
    if f >= 40: return "小吉"
    if f >= 20: return "平平"
    return "今日宜摸鱼"


def _is_signed_today(uid):
    return bool(get_db().execute("SELECT 1 FROM sign_logs WHERE user_id=? AND sign_date=?",
                                 (uid, _today_str())).fetchone())


def do_sign(uid, makeup=False, makeup_date=None, free=False, window=SIGN_MAKEUP_MAX_DAYS):
    """签到 / 补签。返回 (ok, msg, state)"""
    from datetime import date as _d, timedelta
    db = get_db()
    target = _today_str()
    is_makeup = False
    if makeup:
        if not makeup_date:
            return False, "请选择要补签的日期", sign_state(uid)
        try:
            d = _d.fromisoformat(makeup_date)
        except Exception:
            return False, "日期格式错误", sign_state(uid)
        delta_days = (_d.fromisoformat(_today_str()) - d).days
        if not (1 <= delta_days <= window):
            return False, "只能补签最近 %d 天" % window, sign_state(uid)
        target = d.isoformat()
        is_makeup = True
        _su = db.execute("SELECT is_super_admin FROM users WHERE id=?", (uid,)).fetchone()
        is_super = bool(_su and _su['is_super_admin'])
        if db.execute("SELECT 1 FROM sign_logs WHERE user_id=? AND sign_date=?", (uid, target)).fetchone():
            return False, "该日期已签到", sign_state(uid)
        if not free and not is_super:
            if get_coins(uid) < SIGN_MAKEUP_COST:
                return False, "积分不足，补签需 %d 积分" % SIGN_MAKEUP_COST, sign_state(uid)
            award(uid, -SIGN_MAKEUP_COST, 'sign_makeup', "补签 %s" % target)
    if db.execute("SELECT 1 FROM sign_logs WHERE user_id=? AND sign_date=?", (uid, target)).fetchone():
        return False, "该日期已签到", sign_state(uid)
    base = SIGN_DAILY_BASE
    bonus = 0
    db.execute("INSERT INTO sign_logs (user_id, sign_date, is_makeup, reward_coin, bonus_coin, created_at) VALUES (?,?,?,?,?,?)",
               (uid, target, 1 if is_makeup else 0, 0, 0, time.time()))
    db.commit()
    streak = sign_streak(uid)
    base = SIGN_STREAK_TABLE[((streak - 1) % len(SIGN_STREAK_TABLE))]
    if not is_makeup:
        if random.random() < SIGN_EGG_PROB:
            bonus = random.randint(*SIGN_EGG_RANGE)
    db.execute("UPDATE sign_logs SET reward_coin=?, bonus_coin=? WHERE user_id=? AND sign_date=?",
               (base, bonus, uid, target))
    db.commit()
    if is_makeup:
        award(uid, base, 'sign_makeup', "补签 %s 奖励" % target)
    else:
        award(uid, base, 'sign', "签到奖励 +%d" % base)
        if bonus:
            award(uid, bonus, 'sign', "签到彩蛋 +%d" % bonus)
    if not makeup:
        st = sign_state(uid)
        if st['month_full'] and not st['month_rewarded']:
            award(uid, SIGN_MONTH_REWARD, 'sign_month', "%s 月度满签 %d 天奖励" % (target[:7], SIGN_MONTH_FULL))
    return True, ("补签成功 +%d" % base) if is_makeup else ("签到成功 +%d 积分" % base + (" +彩蛋%d" % bonus if bonus else "")), sign_state(uid)


def _task_period(key):
    if key.startswith('daily_'):
        return _today_str()
    return 'once' if key.startswith('newbie_') else 'ach'


def _ach_progress(uid, key):
    """成就进度按 ACH_DEFS 里的 SQL 现算。

    SQL 写错或表不存在时抛异常 —— 这里不吞，静默返回 0 会让人以为「没进度」，
    排查时看不出是 SQL 问题还是真的没数据。
    """
    d = ACH_DEFS.get(key)
    if not d:
        return 0
    sql = d[4]
    row = get_db().execute(sql, (uid,)).fetchone()
    if not row:
        return 0
    v = row[0]
    return int(v) if v is not None else 0


def task_inc(uid, key, n=1):
    """推进任务进度（不超过目标）"""
    if key not in TASK_DEFS:
        return
    _, _, target, _, _ = TASK_DEFS[key]
    period = _task_period(key)
    db = get_db()
    row = db.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_key=? AND period=?",
                     (uid, key, period)).fetchone()
    if row:
        if row['claimed']:
            return
        newp = min(int(row['progress']) + n, target)
        db.execute("UPDATE user_tasks SET progress=? WHERE id=?", (newp, row['id']))
    else:
        db.execute("INSERT INTO user_tasks (user_id, task_key, period, progress, claimed) VALUES (?,?,?,?,0)",
                   (uid, key, period, min(n, target)))
    db.commit()


def get_task_progress(uid, key):
    """任务当前进度：daily/newbie 读 user_tasks；achieve 动态统计"""
    if key in ACH_DEFS:
        return _ach_progress(uid, key)
    period = _task_period(key)
    row = get_db().execute("SELECT progress FROM user_tasks WHERE user_id=? AND task_key=? AND period=?",
                           (uid, key, period)).fetchone()
    return int(row['progress']) if row else 0


def get_tasks_data(uid):
    """任务列表（36 个任务 + 各自的进度/领取状态）。

    ⚠️ 2026-11-01：原来是「每个任务查 2 条 SQL」（progress + claimed），
    36 个任务 = 45 条，加上页面其它查询，/points 整页要打 104 条 SQL。
    改成「一次查出该用户全部 user_tasks 行 + 字典查找」，45 条 → 1 条。
    ach 类任务的进度本来就是动态统计的（不读 user_tasks），跟原逻辑保持一致。
    """
    db = get_db()
    rows = {}
    try:
        for r in db.execute(
                "SELECT task_key, period, progress, claimed FROM user_tasks "
                "WHERE user_id=?", (uid,)).fetchall():
            rows[(r["task_key"], r["period"])] = r
    except Exception:
        rows = {}

    out = []
    for key, (name, desc, target, reward, group) in TASK_DEFS.items():
        r = rows.get((key, _task_period(key)))
        if key in ACH_DEFS:
            progress = _ach_progress(uid, key)
        else:
            progress = int(r["progress"] or 0) if r else 0
        out.append({
            'key': key, 'name': name, 'desc': desc, 'target': target, 'reward': reward, 'group': group,
            'progress': progress,
            'claimed': bool(r["claimed"]) if r else False,
        })
    return out


def claim_task(uid, key):
    if key not in TASK_DEFS:
        return False, "任务不存在"
    name, _, target, reward, _ = TASK_DEFS[key]
    period = _task_period(key)
    db = get_db()
    row = db.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_key=? AND period=?",
                     (uid, key, period)).fetchone()
    progress = _ach_progress(uid, key) if period == 'ach' else (int(row['progress']) if row else 0)
    if progress < target:
        return False, "任务尚未完成（%d/%d）" % (progress, target)
    if row and row['claimed']:
        return False, "奖励已领取"
    if row:
        db.execute("UPDATE user_tasks SET claimed=1 WHERE id=?", (row['id'],))
    else:
        db.execute("INSERT INTO user_tasks (user_id, task_key, period, progress, claimed) VALUES (?,?,?,?,1)",
                   (uid, key, period, progress))
    db.commit()
    award(uid, reward, 'task', "完成任务「%s」" % name)
    return True, "已领取 %d 积分" % reward


def get_mall_data(uid):
    db = get_db()
    me = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    items = db.execute(
        "SELECT * FROM mall_items WHERE value != 'makeup_card' ORDER BY sort_order ASC").fetchall()
    owned = set(r[0] for r in db.execute("SELECT item_id FROM user_items WHERE user_id=?", (uid,)).fetchall())
    equipped = {}
    for r in db.execute("SELECT item_id FROM user_items WHERE user_id=? AND is_equipped=1", (uid,)).fetchall():
        equipped[r[0]] = True
    consumables = {}
    for r in db.execute("SELECT item_key, count FROM user_consumables WHERE user_id=?", (uid,)).fetchall():
        consumables[r['item_key']] = r['count']
    mall = []
    for it in items:
        mall.append({
            'id': it['id'], 'name': it['name'], 'type': it['type'], 'value': it['value'],
            'cost': it['cost'], 'icon': it['icon'], 'desc': it['desc'],
            'limited': it['limited'], 'removable': it['removable'], 'gacha_only': it['gacha_only'],
            'owned': it['id'] in owned, 'equipped': it['id'] in equipped,
            'count': consumables.get(it['value'], 0) if it['type'] == 'consumable' else 0,
        })
    return {'coins': get_coins(uid), 'goods': mall,
            'title': me['mall_title'] if me else '', 'bubble': me['bubble_style'] if me else ''}


def _mall_equip(uid, item_id, item):
    """装备装饰：同类型互斥（一次只能装备一个头衔/气泡/特效/徽章）"""
    db = get_db()
    typ = item['type']
    val = item['value']
    same_type_ids = db.execute(
        "SELECT ui.item_id FROM user_items ui JOIN mall_items mi ON ui.item_id=mi.id "
        "WHERE ui.user_id=? AND mi.type=? AND ui.is_equipped=1",
        (uid, typ)).fetchall()
    for (iid,) in same_type_ids:
        if iid != item_id:
            old = db.execute("SELECT type, value FROM mall_items WHERE id=?", (iid,)).fetchone()
            if old:
                ot, ov = old['type'], old['value']
                if ot == 'title':
                    db.execute("UPDATE users SET mall_title='' WHERE id=? AND mall_title=?", (uid, ov))
                elif ot == 'bubble':
                    db.execute("UPDATE users SET bubble_style='' WHERE id=? AND bubble_style=?", (uid, ov))
                elif ot == 'effect':
                    db.execute("UPDATE users SET has_effect=0, effect_type=NULL WHERE id=? AND effect_type=?", (uid, ov))
                elif ot == 'badge':
                    color, _, text = ov.partition('|')
                    db.execute("UPDATE users SET badge_text=NULL, badge_effect=NULL WHERE id=? AND badge_text=? AND badge_effect=?",
                               (uid, text, color))
            db.execute("UPDATE user_items SET is_equipped=0 WHERE user_id=? AND item_id=?", (uid, iid))
    if typ == 'title':
        db.execute("UPDATE users SET mall_title=? WHERE id=?", (val, uid))
    elif typ == 'badge':
        color, _, text = val.partition('|')
        db.execute("UPDATE users SET badge_text=?, badge_effect=? WHERE id=?", (text, color, uid))
    elif typ == 'bubble':
        db.execute("UPDATE users SET bubble_style=? WHERE id=?", (val, uid))
    elif typ == 'effect':
        db.execute("UPDATE users SET has_effect=1, effect_type=? WHERE id=?", (val, uid))
    db.execute("UPDATE user_items SET is_equipped=1 WHERE user_id=? AND item_id=?", (uid, item_id))
    db.commit()


def _mall_unequip(uid, item):
    db = get_db()
    typ = item['type']
    val = item['value']
    if typ == 'title':
        db.execute("UPDATE users SET mall_title='' WHERE id=? AND mall_title=?", (uid, val))
    elif typ == 'badge':
        color, _, text = val.partition('|')
        db.execute("UPDATE users SET badge_text=NULL, badge_effect=NULL WHERE id=? AND badge_text=? AND badge_effect=?",
                   (uid, text, color))
    elif typ == 'bubble':
        db.execute("UPDATE users SET bubble_style='' WHERE id=? AND bubble_style=?", (uid, val))
    elif typ == 'effect':
        db.execute("UPDATE users SET has_effect=0, effect_type=NULL WHERE id=? AND effect_type=?", (uid, val))
    db.execute("UPDATE user_items SET is_equipped=0 WHERE user_id=? AND item_id=?", (uid, item['id']))
    db.commit()


def _gacha_unlock(uid, item_id, label):
    """扭蛋抽到传说道具：**只解锁，不自动装备**。

    以前是直接写 mall_title / bubble_style / effect_type —— 等于绕过了道具系统：
    用户的道具库里查不到这件东西，看着就像「没抽到，但头上莫名其妙多了个称号」。
    现在按正常解锁走 user_items，装备与否交给用户去积分商城决定。
    已拥有则按老规则补 200 积分。
    """
    db = get_db()
    row = db.execute("SELECT 1 FROM user_items WHERE user_id=? AND item_id=?",
                     (uid, item_id)).fetchone()
    if row:
        award(uid, 200, 'gacha', "扭蛋传说补偿 +200 积分（已拥有%s）" % label)
        return "已拥有%s → 改为发放 200 积分" % label
    db.execute("INSERT INTO user_items (user_id, item_id, redeemed_at, is_equipped) "
               "VALUES (?,?,?,0)", (uid, item_id, time.time()))
    return "已解锁「%s」，可去积分商城装备" % label


def _gacha_grant(uid, rarity):
    db = get_db()
    for rar, w, prizes in GACHA_POOL:
        if rar == rarity:
            prize = random.choice(prizes)
            break
    detail = ''
    if prize.endswith("积分"):
        m = re.match(r'^(\d+)~\s*(\d+)\s*积分$', prize)
        if m:
            v = random.randint(int(m.group(1)), int(m.group(2)))
            prize = "%d 积分" % v
        else:
            v = int(prize.replace("积分", "").strip())
        award(uid, v, 'gacha', "扭蛋开出 %d 积分" % v)
    elif prize in ("头衔·天选之人",):
        detail = _gacha_unlock(uid, 10, "天选之人")
        if "200 积分" in detail:
            prize = "200 积分（已拥有天选之人）"
    elif prize == "气泡·星河":
        detail = _gacha_unlock(uid, 29, "星河气泡")
        if "200 积分" in detail:
            prize = "200 积分（已拥有星河气泡）"
    elif prize == "特效·流星拖尾":
        detail = _gacha_unlock(uid, 41, "流星拖尾")
        if "200 积分" in detail:
            prize = "200 积分（已拥有流星拖尾）"
    else:
        key_map = {"双倍积分卡": "double_coins_card", "经验加速卡": "exp_boost",
                   "个性昵称色卡": "name_color_card", "护盾卡": "shield_card"}
        key = key_map.get(prize)
        if key:
            db.execute("INSERT INTO user_consumables (user_id, item_key, count) VALUES (?,?,1) "
                       "ON CONFLICT(user_id, item_key) DO UPDATE SET count = count + 1", (uid, key))
            detail = "已存入背包"
    db.execute("INSERT INTO gacha_logs (user_id, rarity, prize, detail, created_at) VALUES (?,?,?,?,?)",
               (uid, rarity, prize, detail, time.time()))
    db.commit()
    return prize, detail


def gacha_pull(uid):
    """单抽：先扣积分（超管免费），保底按『上次传说之后』计数"""
    db = get_db()
    u = db.execute("SELECT is_super_admin FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return None, "用户不存在"
    if not u['is_super_admin']:
        if get_coins(uid) < GACHA_COST:
            return None, "积分不足（需要 %d）" % GACHA_COST
        award(uid, -GACHA_COST, 'gacha', "扭蛋单抽 -%d" % GACHA_COST)
    last_legend = db.execute("SELECT MAX(id) mid FROM gacha_logs WHERE user_id=? AND rarity='legendary'",
                             (uid,)).fetchone()
    if last_legend and last_legend['mid']:
        cnt = db.execute("SELECT COUNT(*) c FROM gacha_logs WHERE user_id=? AND id>?",
                         (uid, last_legend['mid'])).fetchone()['c']
    else:
        cnt = db.execute("SELECT COUNT(*) c FROM gacha_logs WHERE user_id=?", (uid,)).fetchone()['c']
    if cnt + 1 >= GACHA_PITY:
        rarity = "legendary"
    else:
        r = random.random() * 100
        rarity = ("legendary" if r < 1 else "epic" if r < 5 else "rare" if r < 15 else
                  "uncommon" if r < 40 else "common")
    prize, detail = _gacha_grant(uid, rarity)
    return rarity, prize


def get_gacha_data(uid):
    db = get_db()
    recent = db.execute("SELECT * FROM gacha_logs WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    last_legend = db.execute("SELECT MAX(id) mid FROM gacha_logs WHERE user_id=? AND rarity='legendary'",
                             (uid,)).fetchone()
    if last_legend and last_legend['mid']:
        cnt = db.execute("SELECT COUNT(*) c FROM gacha_logs WHERE user_id=? AND id>?",
                         (uid, last_legend['mid'])).fetchone()['c']
    else:
        cnt = db.execute("SELECT COUNT(*) c FROM gacha_logs WHERE user_id=?", (uid,)).fetchone()['c']
    return {
        'coins': get_coins(uid), 'cost': GACHA_COST, 'pity': GACHA_PITY, 'since_legend': cnt,
        'recent': [{'rarity': r['rarity'], 'prize': r['prize'], 'detail': r['detail'],
                    'time': time.strftime("%m-%d %H:%M", time.localtime(r['created_at']))} for r in recent],
    }


def get_consumable_count(uid, key):
    row = get_db().execute("SELECT count FROM user_consumables WHERE user_id=? AND item_key=?",
                           (uid, key)).fetchone()
    return int(row['count']) if row else 0


def consume_item(uid, key, n=1):
    db = get_db()
    cur = db.execute("UPDATE user_consumables SET count = count - ? "
                     "WHERE user_id=? AND item_key=? AND count >= ?", (n, uid, key, n))
    db.commit()
    return cur.rowcount > 0


def can_create_group(user):
    """所有登录用户均可创建群组（已取消等级/注册时间限制）"""
    return user is not None

def register_days(user):
    if not user or not user['created_at']:
        return 0
    return max(0, int((time.time() - float(user['created_at'])) // 86400))

def account_status(user):
    """返回 (状态文案, 剩余时长文案)"""
    if is_active_restriction(user['banned_until']):
        return ("封禁", status_text(user['banned_until']))
    if is_active_restriction(user['muted_until']):
        return ("禁言", status_text(user['muted_until']))
    return ("正常", "")

def count_user_stats(user_id):
    db = get_db()
    posts = db.execute("SELECT COUNT(*) c FROM posts WHERE user_id=? AND is_deleted=0 AND recycle=0", (user_id,)).fetchone()['c']
    comments = db.execute("SELECT COUNT(*) c FROM post_comments WHERE user_id=? AND is_deleted=0", (user_id,)).fetchone()['c']
    dynamics = db.execute("SELECT COUNT(*) c FROM dynamics WHERE user_id=? AND repost_of IS NULL", (user_id,)).fetchone()['c']
    likes = db.execute("SELECT total_likes c FROM users WHERE id=?", (user_id,)).fetchone()['c']
    return posts, comments, dynamics, likes

def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    if isinstance(salt, str):
        salt = binascii.unhexlify(salt)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
    return binascii.hexlify(h).decode(), binascii.hexlify(salt).decode()

def set_user_password(db, uid, new_password):
    """统一设置密码 —— 全站唯一入口。

    必须同时清空 password_ext，否则会出现「改了密码但新密码登不上」：
       从旧游戏中心导入的用户带 password_ext（werkzeug 哈希），
       而 verify_password 只要看到 password_ext 就**只用它**验密码，
       新写入的 password_hash 根本不会被读到。
    """
    h, salt = hash_password(new_password)
    db.execute("UPDATE users SET password_hash=?, salt=?, password_ext=NULL WHERE id=?",
               (h, salt, uid))
    return h, salt


def verify_password(password, salt_hex, hash_hex, password_ext=None):
    if password_ext:
        from werkzeug.security import check_password_hash
        return check_password_hash(password_ext, password)
    calc, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(calc, hash_hex)

def get_user_by_name(username):
    return get_db().execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

def get_user_by_id(uid):
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?", (uid,)
    ).fetchone()

def current_user():
    """当前登录用户。请求级缓存（g._cu）—— 049 的 N+1 检测实测每请求
    重复查 users 表 5-6 次（各路由/模板/after_request 各调一次），
    同请求内 uid 不变结果就不变，缓存掉。"""
    uid = session.get("uid")
    if uid is None:
        return None
    cu = getattr(g, "_cu", None)
    if cu is not None and cu[0] == uid:
        return cu[1]
    u = get_user_by_id(uid)
    try:
        g._cu = (uid, u)
    except Exception:
        pass
    return u

def status_text(value):
    """剩余时长展示精确到分钟：剩 X 天 X 时 X 分"""
    if value == 0:
        return "正常"
    if value == PERMANENT:
        return "永久"
    remain = int(value - time.time())
    if remain <= 0:
        return "已到期"
    days = remain // 86400
    hours = (remain % 86400) // 3600
    minutes = (remain % 3600) // 60
    if remain % 60 > 0:
        minutes += 1
        if minutes == 60:
            minutes = 0
            hours += 1
        if hours == 24:
            hours = 0
            days += 1
    return "剩 %d 天 %d 时 %d 分" % (days, hours, minutes)

def _pg_mirror_insert(table, cur, sql, params):
    """052 PostgreSQL 渐进迁移 · 阶段 1：INSERT 后把同一行镜像到 PG 影子库。

    静默失败（影子不影响主站）；镜像带 SQLite 自增 id，便于行数/逐行比对。
    """
    try:
        import pg_shadow
        pg_shadow.mirror(table, sql, params, getattr(cur, "lastrowid", None))
    except Exception:
        pass


def _row_get(row, key, default=None):
    """安全地从 sqlite3.Row 取字段。

    sqlite3.Row **没有** dict 的 .get() 方法 —— 直接调会抛 AttributeError
    导致整个请求 500。而且 Row[不存在的列] 会抛 IndexError 而不是返回 None。
    这个 helper 把两种情况都兜住，避免再踩。

    典型场景：
        target.get('shield_until')   -> AttributeError -> 禁言 500
        msg.get('room_id')           -> AttributeError -> 编辑消息 500
    """
    if row is None:
        return default
    try:
        v = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if v is None else v


def is_active_restriction(value):
    if value == PERMANENT:
        return True
    if value == 0:
        return False
    return value > time.time()

def are_friends(user_id1, user_id2):
    row = get_db().execute(
        "SELECT * FROM friends WHERE ((user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)) AND status = 1",
        (user_id1, user_id2, user_id2, user_id1)
    ).fetchone()
    return row is not None

def has_blocked(owner_id, target_id):
    """owner 是否已拉黑 target"""
    return get_db().execute(
        "SELECT 1 FROM blacklist WHERE user_id = ? AND blocked_id = ?",
        (owner_id, target_id)).fetchone() is not None

def blocked_between(a_id, b_id):
    """双向任一方向存在拉黑关系"""
    return get_db().execute(
        "SELECT 1 FROM blacklist WHERE (user_id=? AND blocked_id=?) OR (user_id=? AND blocked_id=?)",
        (a_id, b_id, b_id, a_id)).fetchone() is not None

def can_private_chat(me, target):
    """私聊权限判定。优先级：黑名单 > 私聊权限模式 > 默认(仅好友)。
    返回 (allowed, reason)。reason 用于展示，被拉黑时给统一话术不暴露拉黑事实。"""
    if me['id'] == target['id']:
        return False, "不能给自己发私信"
    if has_blocked(target['id'], me['id']):
        return False, "对方暂不接受私信"
    if has_blocked(me['id'], target['id']):
        return False, "你已拉黑对方，请先移出黑名单再私聊"
    if me['is_admin']:
        return True, None
    perm = target['private_chat_perm'] if target['private_chat_perm'] is not None else 1
    if perm == 0:
        return True, None
    if perm == 2:
        return False, "对方已关闭私信功能"
    if are_friends(me['id'], target['id']):
        return True, None
    return False, "对方仅接受好友私信，可先发送好友申请"

def get_friend_status(user_id, target_id):
    row = get_db().execute(
        "SELECT * FROM friends WHERE user_id = ? AND friend_id = ?",
        (user_id, target_id)
    ).fetchone()
    if row:
        return row['status']
    row2 = get_db().execute(
        "SELECT * FROM friends WHERE user_id = ? AND friend_id = ?",
        (target_id, user_id)
    ).fetchone()
    if row2:
        return 2
    return -1

def get_avatar_url(user):
    if user and user['avatar']:
        if user['avatar'] in ('default_avatar.png', 'default.png'):
            return url_for('static', filename='default_avatar.png')
        return url_for('static', filename=f'avatars/{user["avatar"]}')
    return "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='50' fill='%23111111'/%3E%3Ccircle cx='50' cy='38' r='16' fill='%23ffffff'/%3E%3Cellipse cx='50' cy='82' rx='28' ry='22' fill='%23ffffff'/%3E%3C/svg%3E"

def get_user_effect(user):
    """返回用户头像/消息体特效：商城购买的特效优先，其次自动热门特效；
    管理员不再强制金色发光（身份由"管理员"标签标识）。"""
    if not user:
        return None
    if user['has_effect']:
        return user['effect_type'] or 'rainbow'
    return None

def _row_get(row, key, default=None):
    """从 sqlite3.Row / dict 里安全取值。

    各处的 SELECT 只挑自己需要的列，不是每个 Row 都有 mall_badge 这类字段，
    直接 row['mall_badge'] 会抛 IndexError。
    """
    if row is None:
        return default
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


_MD_STRIP = re.compile(r"!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"[*_~`>#]+")
_MD_WS = re.compile(r"\s+")
_SITE_TITLE = "MONO 社区"
_SITE_DESC = "一个安静的地方。聊天、玩游戏、写点东西、记点东西。"
_DEFAULT_OG = "/static/mono-og.jpg"


@lru_cache(maxsize=64)
def _static_img_size(rel_path):
    """量一张 static 里图片的像素尺寸（og 卡片用）。失败返回 (0, 0)。"""
    try:
        import struct
        with open(os.path.join(BASE_DIR, rel_path.lstrip("/")), "rb") as fh:
            head = fh.read(65536)
        # PNG: IHDR 在偏移 16
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return int(w), int(h)
        # JPEG: 扫 SOF0/SOF2
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            m = head[i + 1]
            if m in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return int(w), int(h)
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", head[i + 2:i + 4])[0]
            i += 2 + seg
    except Exception:
        pass
    return 0, 0


def plain_text(raw, limit=150):
    """把 Markdown 正文压成一段适合放进 meta description 的纯文本。"""
    t = raw or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = _MD_STRIP.sub(lambda m: m.group(1) or " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _MD_MARKS.sub("", t)
    t = _MD_WS.sub(" ", t).strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t


def og_context(title=None, desc=None, image=None, url_path=None, type_="website",
               site_name=None, meta=None):
    """组装社交预览元信息，模板里 og_* / twitter_* 共用。"""
    # nginx 反代没带 X-Forwarded-Proto，url_root 会是 http:// —— 本站全站 https，
    # og:image / og:url 必须是绝对 https，微信等抓取端对明文 URL 会降级/拒收。
    abs_root = request.url_root.rstrip("/") if request else ""
    if abs_root.startswith("http://"):
        abs_root = "https://" + abs_root[len("http://"):]
    img = image or _DEFAULT_OG
    rel_img = img
    if img.startswith("/"):
        img = abs_root + img
    path = url_path or (request.path if request else "/")
    iw, ih = _static_img_size(rel_img) if rel_img.startswith("/static/") else (0, 0)
    return {
        "og_site_name": site_name or _SITE_TITLE,
        "og_title": (plain_text(title, 70) if title else _SITE_TITLE),
        "og_desc": (plain_text(desc, 160) if desc else _SITE_DESC),
        "og_image": img,
        "og_url": abs_root + (path if path.startswith("/") else "/" + path),
        "og_type": type_,
        "og_meta": _unpack_img_meta(meta or ""),
        "og_thumb": img_src(img, meta or "", "m") if meta else img,
        "og_img_w": iw or 1200,
        "og_img_h": ih or 630,
    }


def get_user_badges(user):
    """取用户身上**所有**徽章，按优先级排好序返回 list。

    顺序：**管理员授予的牌子 → 商城头衔 → 商城徽章**。

    这三样在 users 表里是三列独立数据，**可以同时拥有** ——
    所以是「同时显示」而不是互相顶掉。
    （用户先后提过两次相反的诉求：先说头衔顶掉了牌子，后说牌子顶掉了头衔；
      结论是两个都要留着，别争优先级。）
    """
    if not user:
        return []
    out = []
    text = _row_get(user, 'badge_text', '')
    effect = _row_get(user, 'badge_effect', '')
    if text and effect:
        out.append({'text': text, 'effect': effect, 'color': None, 'kind': 'admin'})
    title = _row_get(user, 'mall_title', '')
    if title:
        out.append({'text': title, 'effect': 'malltitle', 'kind': 'title',
                    'color': TITLE_COLORS.get(title, '#b58a4a')})
    badge = _row_get(user, 'mall_badge', '')
    if badge:
        color, _, btext = badge.partition('|')
        if btext:
            out.append({'text': btext, 'effect': color or 'gold', 'color': None,
                        'kind': 'mall'})
    return out


@app.template_global()
def badges_html(user):
    """把用户身上的徽章渲染成一串 <span class="tag ...">。

    模板里凡是显示用户名的地方都可以直接用：{{ badges_html(u) }}
    这样「管理员牌子 / 商城头衔 / 商城徽章」三处逻辑只此一份，
    不用在每个模板里各写一遍 if。
    """
    from markupsafe import Markup, escape
    parts = []
    for b in get_user_badges(user):
        cls = 'tag ' + ('malltitle' if b['kind'] == 'title' else 'badge-' + str(b['effect']))
        style = ''
        if b['color']:
            style = ' style="background:%s;color:#fff;border-color:transparent;"' % escape(b['color'])
        parts.append('<span class="%s"%s>%s</span>' % (cls, style, escape(b['text'])))
    return Markup(''.join(parts))


def get_user_badge(user):
    """获取用户荣誉牌子信息。

    优先级：**管理员授予的牌子 > 商城头衔 > 商城徽章**。
    管理员发的牌子是官方身份标识，不能让自己买的头衔顶掉它。
    （曾经是反的 —— 商城头衔排第一，一买就把后台发的荣誉牌子盖没了。）
    """
    if not user:
        return None
    if user['badge_text'] and user['badge_effect']:
        return {'text': user['badge_text'], 'effect': user['badge_effect']}
    if user['mall_title']:
        return {'text': user['mall_title'], 'effect': 'title', 'color': TITLE_COLORS.get(user['mall_title'], '#b58a4a')}
    if user['mall_badge']:
        color, _, text = user['mall_badge'].partition('|')
        return {'text': text, 'effect': color or 'gold'}
    return None

@app.template_filter("excerpt")
def excerpt(text, length=150):
    if not text:
        return ""
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"\$\$.*?\$\$", "", s, flags=re.S)
    s = re.sub(r"(?<!\$)\$(?!\$)[^\$]*?(?<!\$)\$(?!\$)", "", s)
    s = re.sub(r"!\[.*?\]\(.*?\)", "[图片]", s)
    s = re.sub(r"\[([^\]]+)\]\(.*?\)", r"\1", s)
    s = re.sub(r"(\*\*|__)(.*?)\1", r"\2", s)
    s = re.sub(r"(\*|_)(.*?)\1", r"\2", s)
    s = re.sub(r"`{1,3}[^`]*`{1,3}", "", s)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.M)
    s = re.sub(r"^>\s+", "", s, flags=re.M)
    s = re.sub(r"^[-*+]\s+", "", s, flags=re.M)
    s = re.sub(r"^\d+\.\s+", "", s, flags=re.M)
    s = re.sub(r"\$+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= length:
        return s
    return s[:length] + "…"

@app.template_filter("fmttime")
def fmttime(ts, fmt="%m-%d %H:%M"):
    try:
        return time.strftime(fmt, time.localtime(float(ts)))
    except (ValueError, TypeError):
        return ""

@app.template_filter("timeago")
def timeago(ts):
    try:
        now = time.time()
        diff = now - float(ts)
        if diff < 60:
            return "刚刚"
        if diff < 3600:
            return f"{int(diff//60)}分钟前"
        if diff < 86400:
            return f"{int(diff//3600)}小时前"
        if diff < 86400 * 30:
            return f"{int(diff//86400)}天前"
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except:
        return ""

def can_recall(msg):
    """判断消息是否可撤回"""
    if not msg:
        return False
    user = current_user()
    if not user:
        return False
    if msg['is_deleted']:
        return False
    if msg['user_id'] != user['id'] and not user['is_admin']:
        return False
    return (time.time() - float(msg['created_at'])) < RECALL_WINDOW

def get_active_horn():
    """获取当前激活的全域传声筒广播（60 秒内最新一条）。"""
    row = get_db().execute(
        "SELECT id, username, content, link, created_at FROM horn_announcements "
        "WHERE created_at > ? ORDER BY id DESC LIMIT 1",
        (time.time() - 60,)).fetchone()
    if not row:
        return {}
    return {"id": row["id"], "username": row["username"] or "匿名",
            "content": row["content"], "link": row["link"],
            "created_at": row["created_at"],
            "expires_at": row["created_at"] + 60}


@app.route("/api/horn/active")
def api_horn_active():
    h = get_active_horn()
    if not h:
        return jsonify({"ok": True, "horn": None})
    return jsonify({"ok": True, "horn": h})


@app.context_processor
def inject_globals():
    me = current_user()
    user = me
    unread_count = 0
    if user:
        # 028：未读数是每请求必查项，缓存 30 秒（通知到达/已读都会主动失效）
        unread_count = cache.remember(
            "unread:%d" % user['id'], 30,
            lambda: get_db().execute(
                "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND is_read = 0",
                (user['id'],)
            ).fetchone()['cnt'])
    _notice_show, _notice_data = update_notice_should_show(user)
    _notice_pack = dict(_notice_data) if _notice_show else {}

    # 问卷：只在「需要答」时注入（公告会先弹，关掉后问卷再出来）
    _survey_row, _survey_qs = survey_pending_for(user)
    _survey_pack = {}
    if _survey_row:
        try:
            _survey_pack = {
                "id": _survey_row["id"],
                "title": _survey_row["title"],
                "intro": _survey_row["intro"],
                "questions": [
                    {"id": q["id"], "qtype": q["qtype"], "title": q["title"],
                     "options": json.loads(q["options"] or "[]"),
                     "required": bool(q["required"])}
                    for q in _survey_qs
                ],
            }
        except Exception:
            _survey_pack = {}
    return {
        "current_user": user,
        "status_text": status_text,
        "is_active_restriction": is_active_restriction,
        "PERMANENT": PERMANENT,
        "human_size": human_size,
        "get_avatar_url": get_avatar_url,
        "img_src": img_src,
        "img_size": img_size,
        "row_get": _row_get,
        "og_context": og_context,
        "plain_text": plain_text,
        "get_user_by_id": get_user_by_id,
        "get_user_effect": get_user_effect,
        "get_user_badge": get_user_badge,
        "are_friends": are_friends,
        "is_user_online": is_user_online,
        "can_recall": can_recall,
        "get_level_title": get_level_title,
        "register_days": register_days,
        "account_status": account_status,
        "LEVEL_TITLES": LEVEL_TITLES,
        "TITLE_COLORS": TITLE_COLORS,
        "can_create_group": can_create_group,
        "unread_notifications": unread_count,
        "github_oauth_on": _gh.configured(),
        "guling_on": ojweb.core._oj_super(me) and _guling_enabled() if me else False,
        "RECALL_WINDOW": RECALL_WINDOW,
        "recall_card_count": get_consumable_count(me['id'], 'recall_card') if me else 0,
        "active_horn": get_active_horn(),
        "site_announcement": get_active_announcement(),
        "update_notice": _notice_pack,
        "survey_popup": _survey_pack,
        "get_sections": lambda: get_db().execute(
            "SELECT * FROM forum_sections WHERE is_hidden=0 ORDER BY sort_order ASC").fetchall(),
    }
    return context
    return context

def _is_background_request():
    """判断这是后台轮询 / XHR，而不是用户主动点开的页面跳转。

    用途：被 login_required 拦下时，只在「用户真的点了链接」的情况下弹提示。
    否则音乐外壳（/api/music/playlist、/api/music/tracks）、消息轮询等后台请求
    会在 session 里塞进一堆「请先登录」，等用户下次打开任意页面（**包括公开页**）
    时才突然弹出来，看起来非常莫名其妙。
    """
    p = request.path or ""
    if p.startswith("/api/") or p.startswith("/socket.io/"):
        return True
    if (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return True
    return False


def _is_audio_request():
    """音频 / 媒体文件请求。

    这类请求不能像页面那样 302 跳登录页 —— <audio> 不跟随302 去取 HTML，
    结果就是播放器卡在「缓冲中」不给任何提示。
    session 过期时该直接回 401，让前端能识别「要重新登录」并提示用户。
    """
    p = request.path or ""
    return p.startswith("/music/stream/") or p.startswith("/music/audio/")


def _login_next():
    """登录后跳回哪里：只接受站内相对路径，挡掉 //evil.com 这类协议相对地址。"""
    n = (request.values.get('next') or '').strip()
    if not n or not n.startswith('/') or n.startswith('//'):
        return ''
    if n.startswith('/login') or n.startswith('/logout'):
        return ''
    return n


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            # 音频请求回 401：页面跳登录没问题，但 <audio> 拿到的是 HTML
            # 字节流而非音频，只会静默卡在「缓冲中」。
            if _is_audio_request():
                return Response("登录状态已失效", status=401,
                                mimetype="text/plain; charset=utf-8")
            if not _is_background_request():
                flash("请先登录", "warn")
            nxt = request.full_path if request.query_string else request.path
            return redirect(url_for("login", next=nxt))
        _lift_expired(user)
        user = current_user()
        if is_active_restriction(user["banned_until"]):
            session.clear()
            flash("该账号已被封禁：%s" % status_text(user["banned_until"]), "error")
            return redirect(url_for("login"))
        update_user_active()
        return f(*args, **kwargs)
    return wrapper


ANNOUNCEMENT_TTL = 24 * 3600


# ══════════════════════════════════════════════════════════════
#  进化 Evolve 联机（阶段 1：能看到彼此）
#  ★ 设计要点：客户端算资源（放置游戏特性），服务端只存「快照」
#    + 做「交易/战争的结算中介」。阶段 1 只有同步，不做交易。
# ══════════════════════════════════════════════════════════════
_EVOLVE_READY = [False]
EVOLVE_ONLINE_WINDOW = 120.0     # 2 分钟内有心跳算在线


def _evolve_ensure(db):
    if _EVOLVE_READY[0]:
        return
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS evolve_players (
                user_id     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                military    REAL NOT NULL DEFAULT 0,
                economy     REAL NOT NULL DEFAULT 0,
                resources   TEXT NOT NULL DEFAULT '{}',
                day         INTEGER NOT NULL DEFAULT 0,
                race        TEXT NOT NULL DEFAULT '',
                hostility   INTEGER NOT NULL DEFAULT 0,
                last_sync   REAL NOT NULL DEFAULT 0,
                last_seen   REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ev_seen ON evolve_players(last_seen DESC);
            CREATE TABLE IF NOT EXISTS evolve_wars (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                attacker    INTEGER NOT NULL,
                defender    INTEGER NOT NULL,
                won         INTEGER NOT NULL DEFAULT 0,
                loot        TEXT NOT NULL DEFAULT '{}',
                created_at  REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ev_war_def ON evolve_wars(defender, created_at DESC);
            CREATE TABLE IF NOT EXISTS evolve_chat (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL DEFAULT '',
                text       TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ev_chat ON evolve_chat(id DESC);
            CREATE TABLE IF NOT EXISTS evolve_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id     INTEGER NOT NULL,
                to_id       INTEGER NOT NULL,
                give_res    TEXT NOT NULL DEFAULT '',
                give_amt    REAL NOT NULL DEFAULT 0,
                want_res    TEXT NOT NULL DEFAULT '',
                want_amt    REAL NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  REAL NOT NULL DEFAULT 0,
                done_at     REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ev_trade_to ON evolve_trades(to_id, status);
            CREATE TABLE IF NOT EXISTS evolve_orders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                give_res   TEXT NOT NULL DEFAULT '',
                give_amt   REAL NOT NULL DEFAULT 0,
                want_res   TEXT NOT NULL DEFAULT '',
                want_amt   REAL NOT NULL DEFAULT 0,
                status     TEXT NOT NULL DEFAULT 'open',
                created_at REAL NOT NULL DEFAULT 0,
                taken_by   INTEGER NOT NULL DEFAULT 0,
                done_at    REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ev_order ON evolve_orders(status, id DESC);
            CREATE TABLE IF NOT EXISTS evolve_saves (
                user_id    INTEGER PRIMARY KEY,
                data       TEXT NOT NULL DEFAULT '',
                size       INTEGER NOT NULL DEFAULT 0,
                day        INTEGER NOT NULL DEFAULT 0,
                race       TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0
            );
        """)
        # pending_delta：交易/掠夺的资源增减（正负都支持）
        try:
            db.execute("ALTER TABLE evolve_players ADD COLUMN pending_delta TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass
        # pending_loot 用 ALTER 补（老库没有这列）
        try:
            db.execute("ALTER TABLE evolve_players ADD COLUMN pending_loot TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass
        db.commit()
    except Exception:
        pass
    _EVOLVE_READY[0] = True


@app.route('/api/evolve/sync', methods=['POST'])
def api_evolve_sync():
    """进化联机同步。

    客户端每 30 秒调一次：上报自己的军力/经济/资源快照，拿回其他玩家列表。
    """
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401

    db = get_db()
    _evolve_ensure(db)

    data = request.get_json(silent=True) or {}
    try:
        military = float(data.get("military") or 0)
        economy = float(data.get("economy") or 0)
    except (TypeError, ValueError):
        military = economy = 0.0
    res = data.get("resources")
    res_json = json.dumps(res, ensure_ascii=False) if isinstance(res, dict) else "{}"
    if len(res_json) > 20000:
        res_json = "{}"
    day = int(data.get("day") or 0)
    race = str(data.get("race") or "")[:32]

    now = time.time()

    # ★ 完整存档落库（客户端每 30 秒同步时带上来，Evolve 自己的 LZString 压缩串）
    save_blob = data.get("save")
    if isinstance(save_blob, str) and 100 < len(save_blob) <= 400000:
        try:
            db.execute(
                "INSERT INTO evolve_saves (user_id, data, size, day, race, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "data=excluded.data, size=excluded.size, day=excluded.day, "
                "race=excluded.race, updated_at=excluded.updated_at",
                (me["id"], save_blob, len(save_blob), day, race, now))
            db.commit()
        except Exception:
            pass

    try:
        db.execute(
            "INSERT INTO evolve_players "
            "(user_id, name, military, economy, resources, day, race, last_sync, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "name=excluded.name, military=excluded.military, economy=excluded.economy, "
            "resources=excluded.resources, day=excluded.day, race=excluded.race, "
            "last_sync=excluded.last_sync, last_seen=excluded.last_seen",
            (me["id"], me["username"], military, economy, res_json, day, race, now, now))
        db.commit()
    except Exception as e:
        return jsonify({"ok": False, "msg": "写库失败: %s" % e}), 500

    # 拉其他玩家（在线优先，按军力差距排序——打实力相近的才有意义）
    try:
        rows = db.execute(
            "SELECT user_id, name, military, economy, day, race, hostility, last_seen "
            "FROM evolve_players WHERE user_id <> ? AND last_seen > ? "
            "ORDER BY ABS(military - ?) ASC LIMIT 30",
            (me["id"], now - EVOLVE_ONLINE_WINDOW, military)).fetchall()
    except Exception:
        rows = []

    peers = [{
        "id": r["user_id"],
        "name": r["name"] or ("玩家%d" % r["user_id"]),
        "military": round(float(r["military"] or 0), 1),
        "economy": round(float(r["economy"] or 0), 1),
        "day": r["day"] or 0,
        "race": r["race"] or "",
        "hostility": r["hostility"] or 0,
        "online": (now - (r["last_seen"] or 0)) < 60,
    } for r in rows]

    # ★ 把「被掠夺」清单带给对方（取走即清空）
    pending = {}
    try:
        row = db.execute("SELECT pending_loot FROM evolve_players WHERE user_id=?",
                         (me["id"],)).fetchone()
        if row and row["pending_loot"] and row["pending_loot"] != "{}":
            pending = json.loads(row["pending_loot"])
            db.execute("UPDATE evolve_players SET pending_loot='{}' WHERE user_id=?",
                       (me["id"],))
            db.commit()
    except Exception:
        pending = {}

    # ★ 交易产生的资源增减（正负都有）
    delta = {}
    try:
        row2 = db.execute("SELECT pending_delta FROM evolve_players WHERE user_id=?",
                          (me["id"],)).fetchone()
        if row2 and row2["pending_delta"] and row2["pending_delta"] != "{}":
            delta = json.loads(row2["pending_delta"])
            db.execute("UPDATE evolve_players SET pending_delta='{}' WHERE user_id=?",
                       (me["id"],))
            db.commit()
    except Exception:
        delta = {}

    return jsonify({"ok": True, "peers": peers, "pending_loot": pending,
                    "pending_delta": delta,
                    "online_window": EVOLVE_ONLINE_WINDOW})


@app.route('/api/evolve/war', methods=['POST'])
def api_evolve_war():
    """战争结算：赢方掠夺防守方 15% 资源快照。

    说明：客户端算战果（原版 war_campaign 里判定），服务端只做「资源转移记账」。
    这是刻意的取舍——原版是纯前端，服务端无法重算 armyRating（要移植几百行公式）。
    小圈子玩，接受这个信任级别。
    """
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401

    db = get_db()
    _evolve_ensure(db)

    data = request.get_json(silent=True) or {}
    try:
        target = int(data.get("target") or 0)
    except (TypeError, ValueError):
        target = 0
    won = bool(data.get("won"))
    if not target or target == me["id"]:
        return jsonify({"ok": False, "msg": "目标无效"}), 400

    now = time.time()
    loot = {}

    # ★ 按战争规模分级（对应原版 5 档战术）
    #   规模越大 → 抢的种类越多、每种抽成越高
    try:
        tactic = int(data.get("tactic") or 0)
    except (TypeError, ValueError):
        tactic = 0
    tactic = max(0, min(4, tactic))
    # (掠夺种类数, 抽成下限, 抽成上限)   None = 全部种类
    # ★ 抽成在区间内随机——同样的战术每次抢到的不一样，才有赌博感
    TIERS = {
        0: (2, 0.03, 0.08),      # 游击战
        1: (3, 0.05, 0.12),      # 小规模
        2: (4, 0.08, 0.18),      # 中等
        3: (6, 0.12, 0.25),      # 大规模
        4: (None, 0.15, 0.35),   # 全面战争
    }
    kinds, rate_lo, rate_hi = TIERS[tactic]
    rate = random.uniform(rate_lo, rate_hi)

    if won:
        try:
            row = db.execute(
                "SELECT resources, pending_loot FROM evolve_players WHERE user_id=?",
                (target,)).fetchone()
            if row:
                try:
                    res = json.loads(row["resources"] or "{}")
                except Exception:
                    res = {}
                # 过滤掉 0 值和非法值
                valid = {}
                for k, v in (res or {}).items():
                    try:
                        amt = float(v)
                    except (TypeError, ValueError):
                        continue
                    if amt > 0:
                        valid[k] = amt
                # 小规模只抢随机的几种，全面战争抢全部
                if kinds is not None and len(valid) > kinds:
                    picked = random.sample(list(valid.keys()), kinds)
                else:
                    picked = list(valid.keys())
                for k in picked:
                    take = int(valid[k] * rate)
                    if take > 0:
                        loot[k] = take
                # 累加到对方的 pending_loot（可能已有未领的）
                try:
                    old_pending = json.loads(row["pending_loot"] or "{}")
                except Exception:
                    old_pending = {}
                for k, v in loot.items():
                    old_pending[k] = int(old_pending.get(k, 0)) + int(v)
                db.execute(
                    "UPDATE evolve_players SET pending_loot=?, resources=? WHERE user_id=?",
                    (json.dumps(old_pending, ensure_ascii=False),
                     json.dumps({k: max(0, float(v) - loot.get(k, 0))
                                 for k, v in (res or {}).items()}, ensure_ascii=False),
                     target))
        except Exception as e:
            return jsonify({"ok": False, "msg": "结算失败: %s" % e}), 500

    try:
        db.execute(
            "INSERT INTO evolve_wars (attacker, defender, won, loot, created_at) "
            "VALUES (?,?,?,?,?)",
            (me["id"], target, 1 if won else 0,
             json.dumps(loot, ensure_ascii=False), now))
        db.commit()
    except Exception:
        pass

    return jsonify({"ok": True, "loot": loot,
                    "tactic": tactic,
                    "rate": round(rate, 4),
                    "rate_range": [rate_lo, rate_hi],
                    "kinds": (len(loot) if kinds is None else kinds)})


@app.route('/api/evolve/chat', methods=['GET', 'POST'])
def api_evolve_chat():
    """世界频道。GET 拉消息（?after=<id> 增量），POST 发言。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "msg": "内容不能为空"}), 400
        if len(text) > 200:
            text = text[:200]

        now = time.time()
        # 3 秒冷却，防刷屏
        try:
            last = db.execute(
                "SELECT created_at FROM evolve_chat WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (me["id"],)).fetchone()
            if last and (now - (last["created_at"] or 0)) < 3.0:
                return jsonify({"ok": False, "msg": "发太快了，歇一下"}), 429
        except Exception:
            pass

        db.execute(
            "INSERT INTO evolve_chat (user_id, name, text, created_at) VALUES (?,?,?,?)",
            (me["id"], me["username"], text, now))
        db.commit()
        # 只保留最近 500 条
        try:
            db.execute("DELETE FROM evolve_chat WHERE id NOT IN "
                       "(SELECT id FROM evolve_chat ORDER BY id DESC LIMIT 500)")
            db.commit()
        except Exception:
            pass
        return jsonify({"ok": True})

    # GET：after=0 拉最近 30 条，否则拉 id > after
    try:
        after = int(request.args.get("after") or 0)
    except (TypeError, ValueError):
        after = 0
    try:
        if after > 0:
            rows = db.execute(
                "SELECT id, user_id, name, text, created_at FROM evolve_chat "
                "WHERE id > ? ORDER BY id ASC LIMIT 60", (after,)).fetchall()
        else:
            rows = db.execute(
                "SELECT id, user_id, name, text, created_at FROM evolve_chat "
                "ORDER BY id DESC LIMIT 30").fetchall()
            rows = list(reversed(rows))
    except Exception:
        rows = []

    return jsonify({"ok": True, "msgs": [{
        "id": r["id"], "uid": r["user_id"], "name": r["name"],
        "text": r["text"], "at": r["created_at"],
        "mine": r["user_id"] == me["id"],
    } for r in rows]})


def _evolve_add_delta(db, uid, res, amt):
    """给某人的 pending_delta 累加一项（正负都行）。"""
    try:
        row = db.execute("SELECT pending_delta FROM evolve_players WHERE user_id=?",
                         (uid,)).fetchone()
        if not row:
            return
        try:
            cur = json.loads(row["pending_delta"] or "{}")
        except Exception:
            cur = {}
        cur[res] = round(float(cur.get(res, 0)) + float(amt), 2)
        if abs(cur[res]) < 0.01:
            cur.pop(res, None)
        db.execute("UPDATE evolve_players SET pending_delta=? WHERE user_id=?",
                   (json.dumps(cur, ensure_ascii=False), uid))
    except Exception:
        pass


@app.route('/api/evolve/trade/offer', methods=['POST'])
def api_evolve_trade_offer():
    """发起交易：我给 give，换对方的 want。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)

    d = request.get_json(silent=True) or {}
    try:
        to_id = int(d.get("to") or 0)
        give_amt = float(d.get("give_amt") or 0)
        want_amt = float(d.get("want_amt") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "参数错误"}), 400
    give_res = str(d.get("give_res") or "")[:32]
    want_res = str(d.get("want_res") or "")[:32]

    if not to_id or to_id == me["id"]:
        return jsonify({"ok": False, "msg": "目标无效"}), 400
    if not give_res or not want_res:
        return jsonify({"ok": False, "msg": "请选择资源"}), 400
    if give_amt <= 0 or want_amt <= 0:
        return jsonify({"ok": False, "msg": "数量必须大于 0"}), 400

    # 防刷：同一对玩家同时最多 3 条待处理
    try:
        n = db.execute(
            "SELECT COUNT(*) c FROM evolve_trades WHERE from_id=? AND to_id=? AND status='pending'",
            (me["id"], to_id)).fetchone()["c"]
        if n >= 3:
            return jsonify({"ok": False, "msg": "你给对方挂的单太多了，等处理完再说"}), 429
    except Exception:
        pass

    now = time.time()
    db.execute(
        "INSERT INTO evolve_trades (from_id,to_id,give_res,give_amt,want_res,want_amt,status,created_at) "
        "VALUES (?,?,?,?,?,?, 'pending', ?)",
        (me["id"], to_id, give_res, give_amt, want_res, want_amt, now))
    db.commit()
    return jsonify({"ok": True})


@app.route('/api/evolve/trade/respond', methods=['POST'])
def api_evolve_trade_respond():
    """接受 / 拒绝别人发给我的交易。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)

    d = request.get_json(silent=True) or {}
    try:
        tid = int(d.get("id") or 0)
    except (TypeError, ValueError):
        tid = 0
    accept = bool(d.get("accept"))
    if not tid:
        return jsonify({"ok": False, "msg": "参数错误"}), 400

    row = db.execute("SELECT * FROM evolve_trades WHERE id=?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "交易不存在"}), 404
    if row["to_id"] != me["id"]:
        return jsonify({"ok": False, "msg": "这不是发给你的交易"}), 403
    if row["status"] != "pending":
        return jsonify({"ok": False, "msg": "这笔交易已处理过了"}), 400

    now = time.time()
    if not accept:
        db.execute("UPDATE evolve_trades SET status='rejected', done_at=? WHERE id=?", (now, tid))
        db.commit()
        return jsonify({"ok": True, "status": "rejected"})

    # 接受：给双方记增减（客户端下次同步时应用）
    _evolve_add_delta(db, row["from_id"], row["give_res"], -row["give_amt"])
    _evolve_add_delta(db, row["from_id"], row["want_res"], +row["want_amt"])
    _evolve_add_delta(db, row["to_id"], row["give_res"], +row["give_amt"])
    _evolve_add_delta(db, row["to_id"], row["want_res"], -row["want_amt"])
    db.execute("UPDATE evolve_trades SET status='accepted', done_at=? WHERE id=?", (now, tid))
    db.commit()
    return jsonify({"ok": True, "status": "accepted"})


@app.route('/api/evolve/order/create', methods=['POST'])
def api_evolve_order_create():
    """挂一个公开订单：我出 give，换 want。任何解锁这些资源的人都能成交。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)

    d = request.get_json(silent=True) or {}
    give_res = str(d.get("give_res") or "")[:32]
    want_res = str(d.get("want_res") or "")[:32]
    try:
        give_amt = float(d.get("give_amt") or 0)
        want_amt = float(d.get("want_amt") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "数量格式错误"}), 400

    if not give_res or not want_res:
        return jsonify({"ok": False, "msg": "请选择资源"}), 400
    if give_res == want_res:
        return jsonify({"ok": False, "msg": "不能自己换自己"}), 400
    if give_amt <= 0 or want_amt <= 0:
        return jsonify({"ok": False, "msg": "数量必须大于 0"}), 400

    # 每人最多挂 5 单
    try:
        n = db.execute("SELECT COUNT(*) c FROM evolve_orders WHERE user_id=? AND status='open'",
                       (me["id"],)).fetchone()["c"]
        if n >= 5:
            return jsonify({"ok": False, "msg": "你最多同时挂 5 个订单"}), 429
    except Exception:
        pass

    now = time.time()
    db.execute(
        "INSERT INTO evolve_orders (user_id,give_res,give_amt,want_res,want_amt,status,created_at) "
        "VALUES (?,?,?,?,?, 'open', ?)",
        (me["id"], give_res, give_amt, want_res, want_amt, now))
    db.commit()
    return jsonify({"ok": True})


@app.route('/api/evolve/order/list', methods=['GET'])
def api_evolve_order_list():
    """公开订单列表（最近 100 条未成交的）。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)
    try:
        rows = db.execute(
            "SELECT o.*, p.name FROM evolve_orders o "
            "LEFT JOIN evolve_players p ON p.user_id=o.user_id "
            "WHERE o.status='open' ORDER BY o.id DESC LIMIT 100").fetchall()
    except Exception:
        rows = []
    return jsonify({"ok": True, "orders": [{
        "id": r["id"],
        "user_id": r["user_id"],
        "name": r["name"] or ("玩家%d" % r["user_id"]),
        "mine": r["user_id"] == me["id"],
        "give_res": r["give_res"], "give_amt": r["give_amt"],
        "want_res": r["want_res"], "want_amt": r["want_amt"],
        "at": r["created_at"],
    } for r in rows]})


@app.route('/api/evolve/order/take', methods=['POST'])
def api_evolve_order_take():
    """成交别人的订单：我按订单的 want 给，拿订单的 give。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)

    d = request.get_json(silent=True) or {}
    try:
        oid = int(d.get("id") or 0)
    except (TypeError, ValueError):
        oid = 0
    if not oid:
        return jsonify({"ok": False, "msg": "参数错误"}), 400

    row = db.execute("SELECT * FROM evolve_orders WHERE id=?", (oid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "订单不存在"}), 404
    if row["status"] != "open":
        return jsonify({"ok": False, "msg": "这单已经成交或被取消了"}), 400
    if row["user_id"] == me["id"]:
        return jsonify({"ok": False, "msg": "不能成交自己的订单"}), 400

    now = time.time()
    # 下单方：给出去 give，收到 want
    _evolve_add_delta(db, row["user_id"], row["give_res"], -row["give_amt"])
    _evolve_add_delta(db, row["user_id"], row["want_res"], +row["want_amt"])
    # 成交方：收到 give，给出 want
    _evolve_add_delta(db, me["id"], row["give_res"], +row["give_amt"])
    _evolve_add_delta(db, me["id"], row["want_res"], -row["want_amt"])
    db.execute("UPDATE evolve_orders SET status='done', taken_by=?, done_at=? WHERE id=?",
               (me["id"], now, oid))
    db.commit()
    return jsonify({"ok": True})


@app.route('/api/evolve/order/cancel', methods=['POST'])
def api_evolve_order_cancel():
    """取消自己挂的订单。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)
    d = request.get_json(silent=True) or {}
    try:
        oid = int(d.get("id") or 0)
    except (TypeError, ValueError):
        oid = 0
    row = db.execute("SELECT user_id, status FROM evolve_orders WHERE id=?", (oid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "订单不存在"}), 404
    if row["user_id"] != me["id"]:
        return jsonify({"ok": False, "msg": "这不是你的订单"}), 403
    if row["status"] != "open":
        return jsonify({"ok": False, "msg": "订单已结束"}), 400
    db.execute("UPDATE evolve_orders SET status='cancelled', done_at=? WHERE id=?",
               (time.time(), oid))
    db.commit()
    return jsonify({"ok": True})


@app.route('/api/evolve/save', methods=['GET'])
def api_evolve_save():
    """取回服务端存的存档（用于换设备 / 清缓存后恢复）。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)
    try:
        row = db.execute(
            "SELECT data, size, day, race, updated_at FROM evolve_saves WHERE user_id=?",
            (me["id"],)).fetchone()
    except Exception:
        row = None
    if not row or not row["data"]:
        return jsonify({"ok": False, "msg": "服务端还没有你的存档"}), 404
    return jsonify({"ok": True, "data": row["data"], "size": row["size"],
                    "day": row["day"], "race": row["race"],
                    "updated_at": row["updated_at"]})


@app.route('/api/evolve/save/stats', methods=['GET'])
def api_evolve_save_stats():
    """（超管）看所有玩家的存档情况。"""
    me = current_user()
    if not me or not me["is_admin"]:
        return jsonify({"ok": False, "msg": "无权限"}), 403
    db = get_db()
    _evolve_ensure(db)
    try:
        rows = db.execute(
            "SELECT s.user_id, p.name, s.size, s.day, s.race, s.updated_at "
            "FROM evolve_saves s LEFT JOIN evolve_players p ON p.user_id=s.user_id "
            "ORDER BY s.updated_at DESC LIMIT 200").fetchall()
    except Exception:
        rows = []
    total = sum((r["size"] or 0) for r in rows)
    return jsonify({"ok": True,
                    "count": len(rows),
                    "total_bytes": total,
                    "saves": [{
                        "uid": r["user_id"],
                        "name": r["name"] or ("玩家%d" % r["user_id"]),
                        "size": r["size"], "day": r["day"], "race": r["race"],
                        "at": r["updated_at"],
                    } for r in rows]})


@app.route('/api/evolve/trade/list', methods=['GET'])
def api_evolve_trade_list():
    """我发起的 + 我收到的待处理交易。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)

    def _rows(sql, args):
        try:
            return db.execute(sql, args).fetchall()
        except Exception:
            return []

    sent = _rows(
        "SELECT t.*, p.name tname FROM evolve_trades t "
        "LEFT JOIN evolve_players p ON p.user_id=t.to_id "
        "WHERE t.from_id=? AND t.status='pending' ORDER BY t.id DESC LIMIT 20", (me["id"],))
    recv = _rows(
        "SELECT t.*, p.name fname FROM evolve_trades t "
        "LEFT JOIN evolve_players p ON p.user_id=t.from_id "
        "WHERE t.to_id=? AND t.status='pending' ORDER BY t.id DESC LIMIT 20", (me["id"],))

    def pack(r, other_key, other_name_key):
        return {
            "id": r["id"],
            "other_id": r[other_key],
            "other_name": r[other_name_key] or ("玩家%d" % r[other_key]),
            "give_res": r["give_res"], "give_amt": r["give_amt"],
            "want_res": r["want_res"], "want_amt": r["want_amt"],
            "at": r["created_at"],
        }

    return jsonify({"ok": True,
                    "sent": [pack(r, "to_id", "tname") for r in sent],
                    "received": [pack(r, "from_id", "fname") for r in recv]})


@app.route('/api/evolve/wars', methods=['GET'])
def api_evolve_wars():
    """我的战报（最近 20 条）。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _evolve_ensure(db)
    try:
        rows = db.execute(
            "SELECT w.*, "
            "  (SELECT name FROM evolve_players WHERE user_id=w.attacker) aname, "
            "  (SELECT name FROM evolve_players WHERE user_id=w.defender) dname "
            "FROM evolve_wars w WHERE w.attacker=? OR w.defender=? "
            "ORDER BY w.id DESC LIMIT 20", (me["id"], me["id"])).fetchall()
    except Exception:
        rows = []
    out = [{
        "id": r["id"],
        "attacker": r["attacker"], "defender": r["defender"],
        "attacker_name": r["aname"] or ("玩家%d" % r["attacker"]),
        "defender_name": r["dname"] or ("玩家%d" % r["defender"]),
        "won": bool(r["won"]),
        "i_am_attacker": r["attacker"] == me["id"],
        "loot": r["loot"],
        "at": r["created_at"],
    } for r in rows]
    return jsonify({"ok": True, "wars": out})


# ══════════════════════════════════════════════════════════════
#  超苦逼冒险者 · 竞技场（Kubition Arena）
#  单机游戏加一个「挑战在线玩家」的入口。对手数据是快照，
#  打赢打输都只记战绩，不影响双方存档（用户明确说「无所谓」）。
# ══════════════════════════════════════════════════════════════
_KUBI_READY = [False]
KUBI_ONLINE_WINDOW = 300.0      # 5 分钟内有心跳算在线


def _kubi_ensure(db):
    if _KUBI_READY[0]:
        return
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS kubition_players (
                user_id     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                level       INTEGER NOT NULL DEFAULT 1,
                atk         INTEGER NOT NULL DEFAULT 10,
                def         INTEGER NOT NULL DEFAULT 0,
                max_hp      INTEGER NOT NULL DEFAULT 100,
                hp          INTEGER NOT NULL DEFAULT 100,
                day         INTEGER NOT NULL DEFAULT 1,
                season      TEXT NOT NULL DEFAULT '',
                generation  INTEGER NOT NULL DEFAULT 0,
                wins        INTEGER NOT NULL DEFAULT 0,
                losses      INTEGER NOT NULL DEFAULT 0,
                last_sync   REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS kubition_fights (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                attacker    INTEGER NOT NULL,
                defender    INTEGER NOT NULL,
                won         INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_kubi_sync ON kubition_players(last_sync DESC);
            CREATE TABLE IF NOT EXISTS kubition_saves_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                slot       INTEGER NOT NULL DEFAULT 1,
                data       TEXT NOT NULL DEFAULT '',
                size       INTEGER NOT NULL DEFAULT 0,
                day        INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_kubi_hist ON kubition_saves_history(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS kubition_graves (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                place      TEXT NOT NULL DEFAULT '',
                place_name TEXT NOT NULL DEFAULT '',
                day        INTEGER NOT NULL DEFAULT 0,
                cause      TEXT NOT NULL DEFAULT '',
                words      TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_kubi_grave_place ON kubition_graves(place, created_at DESC);
            CREATE TABLE IF NOT EXISTS kubition_saves (
                user_id    INTEGER PRIMARY KEY,
                data       TEXT NOT NULL DEFAULT '',
                size       INTEGER NOT NULL DEFAULT 0,
                day        INTEGER NOT NULL DEFAULT 0,
                slot       INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL DEFAULT 0
            );
        """)
        db.commit()
    except Exception:
        pass
    _KUBI_READY[0] = True


@app.route('/api/kubition/sync', methods=['POST'])
def api_kubi_sync():
    """上报自己的战力快照 + 拿回在线玩家列表。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _kubi_ensure(db)

    d = request.get_json(silent=True) or {}

    def num(k, default=0):
        try:
            return int(float(d.get(k) if d.get(k) is not None else default))
        except (TypeError, ValueError):
            return default

    now = time.time()
    try:
        db.execute(
            "INSERT INTO kubition_players "
            "(user_id,name,level,atk,def,max_hp,hp,day,season,generation,wins,losses,last_sync) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "name=excluded.name, level=excluded.level, atk=excluded.atk, def=excluded.def, "
            "max_hp=excluded.max_hp, hp=excluded.hp, day=excluded.day, "
            "season=excluded.season, generation=excluded.generation, "
            "wins=kubition_players.wins, losses=kubition_players.losses, "
            "last_sync=excluded.last_sync",
            (me["id"], me["username"], num("level", 1), num("atk", 10), num("def", 0),
             num("maxHp", 100), num("hp", 100), num("day", 1),
             str(d.get("season") or "")[:8], num("generation", 0),
             num("wins", 0), num("losses", 0), now))
        db.commit()
    except Exception as e:
        return jsonify({"ok": False, "msg": "写库失败: %s" % e}), 500

    try:
        rows = db.execute(
            "SELECT user_id,name,level,atk,def,max_hp,day,season,generation,wins,losses,last_sync "
            "FROM kubition_players WHERE user_id<>? AND last_sync>? "
            "ORDER BY last_sync DESC LIMIT 30",
            (me["id"], now - KUBI_ONLINE_WINDOW)).fetchall()
    except Exception:
        rows = []

    return jsonify({"ok": True, "players": [{
        "id": r["user_id"], "name": r["name"] or ("玩家%d" % r["user_id"]),
        "level": r["level"], "atk": r["atk"], "def": r["def"],
        "maxHp": r["max_hp"], "day": r["day"], "season": r["season"],
        "generation": r["generation"], "wins": r["wins"], "losses": r["losses"],
        "online": (now - (r["last_sync"] or 0)) < 90,
    } for r in rows]})


@app.route('/api/kubition/save', methods=['GET', 'POST'])
def api_kubi_save():
    """超苦逼冒险者云存档。GET 拉，POST 存。

    只保留每人一份（覆盖），配合「每次出门自动存」。
    """
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _kubi_ensure(db)

    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        blob = d.get("data")
        if not isinstance(blob, str) or len(blob) < 20:
            return jsonify({"ok": False, "msg": "存档数据无效"}), 400
        if len(blob) > 2000000:
            return jsonify({"ok": False, "msg": "存档过大"}), 400
        try:
            day = int(d.get("day") or 0)
            slot = int(d.get("slot") or 1)
        except (TypeError, ValueError):
            day, slot = 0, 1
        now = time.time()
        try:
            # ★ 先写一份历史（保留最近 20 份），这样覆盖了也能回滚
            db.execute(
                "INSERT INTO kubition_saves_history (user_id,slot,data,size,day,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (me["id"], slot, blob, len(blob), day, now))
            db.execute(
                "DELETE FROM kubition_saves_history WHERE user_id=? AND id NOT IN ("
                "  SELECT id FROM kubition_saves_history WHERE user_id=? "
                "  ORDER BY created_at DESC LIMIT 20)",
                (me["id"], me["id"]))

            db.execute(
                "INSERT INTO kubition_saves (user_id,data,size,day,slot,updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "data=excluded.data, size=excluded.size, day=excluded.day, "
                "slot=excluded.slot, updated_at=excluded.updated_at",
                (me["id"], blob, len(blob), day, slot, now))
            db.commit()
        except Exception as e:
            return jsonify({"ok": False, "msg": "写库失败: %s" % e}), 500
        return jsonify({"ok": True, "size": len(blob)})

    # GET
    try:
        row = db.execute(
            "SELECT data,size,day,slot,updated_at FROM kubition_saves WHERE user_id=?",
            (me["id"],)).fetchone()
    except Exception:
        row = None
    if not row or not row["data"]:
        return jsonify({"ok": False, "msg": "云端还没有你的存档"}), 404
    return jsonify({"ok": True, "data": row["data"], "size": row["size"],
                    "day": row["day"], "slot": row["slot"],
                    "updated_at": row["updated_at"]})


@app.route('/api/kubition/grave', methods=['GET', 'POST'])
def api_kubi_grave():
    """墓碑：死亡时留一句遗言，其他玩家在同一个地点能看到。

    POST {place, place_name, day, cause, words}
    GET  ?place=xxx → 最近 12 条墓碑
    """
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _kubi_ensure(db)

    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        place = str(d.get("place") or "")[:40]
        if not place:
            return jsonify({"ok": False, "msg": "地点无效"}), 400
        words = str(d.get("words") or "").strip()[:80]
        if not words:
            return jsonify({"ok": False, "msg": "遗言不能为空"}), 400

        # ★ 敏感词过滤（墓碑是公开内容，比聊天更严：不管 filter_mode / spam_autoblock 都拦）
        try:
            # ① 外链 / 引流 —— 绕过 spam_autoblock 开关，强制拦
            for _p in SPAM_PATTERNS:
                if re.search(_p, words, re.IGNORECASE):
                    return jsonify({"ok": False,
                                    "msg": "遗言不能包含网址或引流信息"}), 400
            # ② 敏感词库
            hits = _match_words(words)
            if hits:
                return jsonify({"ok": False,
                                "msg": "遗言包含不合适的内容，换个说法吧"}), 400
        except Exception:
            pass
        try:
            day = int(d.get("day") or 0)
        except (TypeError, ValueError):
            day = 0
        now = time.time()
        try:
            db.execute(
                "INSERT INTO kubition_graves (user_id,place,place_name,day,cause,words,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (me["id"], place, str(d.get("place_name") or "")[:40], day,
                 str(d.get("cause") or "")[:20], words, now))
            db.commit()
        except Exception as e:
            return jsonify({"ok": False, "msg": "写库失败: %s" % e}), 500
        return jsonify({"ok": True})

    # GET
    place = str(request.args.get("place") or "")[:40]
    if not place:
        return jsonify({"ok": False, "msg": "缺少 place"}), 400
    try:
        rows = db.execute(
            "SELECT g.id, g.user_id, g.place_name, g.day, g.cause, g.words, g.created_at, "
            "       COALESCE(NULLIF(u.username,''), p.name) AS uname "
            "FROM kubition_graves g "
            "LEFT JOIN users u ON u.id = g.user_id "
            "LEFT JOIN kubition_players p ON p.user_id = g.user_id "
            "WHERE g.place = ? ORDER BY g.created_at DESC LIMIT 12",
            (place,)).fetchall()
    except Exception:
        rows = []
    return jsonify({"ok": True, "graves": [{
        "id": r["id"],
        "name": r["uname"] or ("冒险者%d" % r["user_id"]),
        "day": r["day"],
        "cause": r["cause"],
        "words": r["words"],
        "ago": max(0, int(time.time() - (r["created_at"] or 0))),
    } for r in rows]})


@app.route('/api/kubition/save/history', methods=['GET'])
def api_kubi_save_history():
    """列出最近 20 份存档历史。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _kubi_ensure(db)
    try:
        rows = db.execute(
            "SELECT id,size,day,slot,created_at FROM kubition_saves_history "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
            (me["id"],)).fetchall()
    except Exception:
        rows = []
    return jsonify({"ok": True, "items": [{
        "id": r["id"], "size": r["size"], "day": r["day"], "slot": r["slot"],
        "at": r["created_at"],
        "ago": max(0, int(time.time() - (r["created_at"] or 0))),
    } for r in rows]})


@app.route('/api/kubition/save/restore', methods=['POST'])
def api_kubi_save_restore():
    """把某份历史恢复成当前存档。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _kubi_ensure(db)

    d = request.get_json(silent=True) or {}
    try:
        hid = int(d.get("id") or 0)
    except (TypeError, ValueError):
        hid = 0
    if not hid:
        return jsonify({"ok": False, "msg": "缺少历史 id"}), 400

    row = db.execute(
        "SELECT id,data,size,day,slot FROM kubition_saves_history "
        "WHERE id=? AND user_id=?", (hid, me["id"])).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "找不到这份历史存档"}), 404

    now = time.time()
    try:
        # 恢复前先把「当前存档」也存一份历史，避免恢复错了没法退回去
        cur = db.execute("SELECT data,size,day,slot FROM kubition_saves WHERE user_id=?",
                         (me["id"],)).fetchone()
        if cur and cur["data"]:
            db.execute(
                "INSERT INTO kubition_saves_history (user_id,slot,data,size,day,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (me["id"], cur["slot"], cur["data"], cur["size"], cur["day"], now))

        db.execute(
            "INSERT INTO kubition_saves (user_id,data,size,day,slot,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "data=excluded.data, size=excluded.size, day=excluded.day, "
            "slot=excluded.slot, updated_at=excluded.updated_at",
            (me["id"], row["data"], row["size"], row["day"], row["slot"], now))
        db.commit()
    except Exception as e:
        return jsonify({"ok": False, "msg": "恢复失败: %s" % e}), 500

    return jsonify({"ok": True, "day": row["day"], "size": row["size"]})


@app.route('/api/kubition/me', methods=['GET'])
def api_kubi_me():
    """我的竞技场战绩。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _kubi_ensure(db)
    try:
        r = db.execute("SELECT wins, losses FROM kubition_players WHERE user_id=?",
                       (me["id"],)).fetchone()
    except Exception:
        r = None
    return jsonify({"ok": True,
                    "wins": r["wins"] if r else 0,
                    "losses": r["losses"] if r else 0})


@app.route('/api/kubition/result', methods=['POST'])
def api_kubi_result():
    """记录一场竞技场结果。只记战绩，不动存档。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _kubi_ensure(db)

    d = request.get_json(silent=True) or {}
    try:
        opp = int(d.get("opponent") or 0)
    except (TypeError, ValueError):
        opp = 0
    win = bool(d.get("win"))
    if not opp or opp == me["id"]:
        return jsonify({"ok": False, "msg": "对手无效"}), 400

    now = time.time()
    try:
        db.execute(
            "INSERT INTO kubition_fights (attacker,defender,won,created_at) VALUES (?,?,?,?)",
            (me["id"], opp, 1 if win else 0, now))
        col = "wins" if win else "losses"
        db.execute("UPDATE kubition_players SET %s=%s+1 WHERE user_id=?" % (col, col),
                   (me["id"],))
        # 对方也记一笔（输了/赢了），但不动存档
        other = "losses" if win else "wins"
        db.execute("UPDATE kubition_players SET %s=%s+1 WHERE user_id=?" % (other, other),
                   (opp,))
        db.commit()
    except Exception as e:
        return jsonify({"ok": False, "msg": "记录失败: %s" % e}), 500

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════
#  调查问卷（仅超管可发布；用户每天/每份必答一次）
# ══════════════════════════════════════════════════════════════
_SURVEY_READY = [False]


def _survey_ensure(db):
    """幂等建表（懒建，进程内只跑一次）。"""
    if _SURVEY_READY[0]:
        return
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS surveys (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL DEFAULT '',
                intro       TEXT NOT NULL DEFAULT '',
                enabled     INTEGER NOT NULL DEFAULT 0,
                once_only   INTEGER NOT NULL DEFAULT 1,
                created_by  INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS survey_questions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id INTEGER NOT NULL,
                sort      INTEGER NOT NULL DEFAULT 0,
                qtype     TEXT NOT NULL DEFAULT 'single',
                title     TEXT NOT NULL DEFAULT '',
                options   TEXT NOT NULL DEFAULT '[]',
                required  INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS survey_responses (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id  INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                answers    TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_survey_q ON survey_questions(survey_id, sort);
            CREATE INDEX IF NOT EXISTS idx_survey_r ON survey_responses(survey_id, user_id);
        """)
        db.commit()
    except Exception:
        pass
    _SURVEY_READY[0] = True


def _survey_active(db):
    """当前启用中的问卷（没有就返回 None）。"""
    _survey_ensure(db)
    try:
        return db.execute(
            "SELECT * FROM surveys WHERE enabled=1 ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        return None


def _survey_questions(db, sid):
    _survey_ensure(db)
    try:
        return db.execute(
            "SELECT * FROM survey_questions WHERE survey_id=? ORDER BY sort, id", (sid,)).fetchall()
    except Exception:
        return []


def _survey_answered(db, sid, uid):
    try:
        return bool(db.execute(
            "SELECT 1 FROM survey_responses WHERE survey_id=? AND user_id=? LIMIT 1",
            (sid, uid)).fetchone())
    except Exception:
        return False


def survey_pending_for(me):
    """该用户是否还要答问卷 → 返回 (survey_row, questions) 或 (None, [])。"""
    if not me:
        return None, []
    db = get_db()
    s = _survey_active(db)
    if not s:
        return None, []
    if s['once_only'] and _survey_answered(db, s['id'], me['id']):
        return None, []
    qs = _survey_questions(db, s['id'])
    if not qs:
        return None, []
    return s, qs


def get_update_notice():
    """更新公告（进入网站弹窗用）。

    存 site_config['update_notice']，JSON：{enabled, title, content, ver, at}
    """
    raw = get_config('update_notice')
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}
    if not d.get('enabled') or not (d.get('content') or '').strip():
        return {}
    return d


_NOTICE_COL_READY = [False]


def _ensure_notice_col(db):
    """users.notice_seen_date 懒建（全局只跑一次）。"""
    if _NOTICE_COL_READY[0]:
        return
    try:
        db.execute("ALTER TABLE users ADD COLUMN notice_seen_date TEXT NOT NULL DEFAULT ''")
        db.commit()
    except Exception:
        pass
    _NOTICE_COL_READY[0] = True


def update_notice_should_show(me):
    """今天这个用户是否还要弹公告。

    - 登录用户：users.notice_seen_date 不等于今天 → 弹（服务端记，换设备/清缓存都拦不住）
    - 游客：交给前端 localStorage 按「日期 + 版本」判
    """
    _ensure_notice_col(get_db())
    notice = get_update_notice()
    if not notice:
        return False, notice
    if not me:
        return True, notice          # 游客：前端兜底判断
    try:
        row = get_db().execute(
            "SELECT notice_seen_date FROM users WHERE id=?", (me['id'],)).fetchone()
        seen = (row['notice_seen_date'] if row and 'notice_seen_date' in row.keys() else '') or ''
    except Exception:
        seen = ''
    today = time.strftime('%Y-%m-%d')
    return (seen != today), notice


@app.route('/api/survey/submit', methods=['POST'])
def api_survey_submit():
    """提交问卷答案。强制作答，但服务端只校验「必答题非空」。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    db = get_db()
    _survey_ensure(db)
    s = _survey_active(db)
    if not s:
        return jsonify({"ok": False, "msg": "问卷已关闭"}), 400

    data = request.get_json(silent=True) or {}
    answers = data.get("answers") or {}
    if not isinstance(answers, dict):
        return jsonify({"ok": False, "msg": "答案格式错误"}), 400

    qs = _survey_questions(db, s["id"])
    clean = {}
    for q in qs:
        key = str(q["id"])
        val = answers.get(key)
        if q["qtype"] == "multi":
            if not isinstance(val, list):
                val = [] if not val else [val]
            val = [str(x) for x in val]
            if q["required"] and not val:
                return jsonify({"ok": False, "msg": "「%s」是必答题" % q["title"]}), 400
        elif q["qtype"] == "text":
            val = (val or "").strip()[:2000] if isinstance(val, str) else ""
            if q["required"] and not val:
                return jsonify({"ok": False, "msg": "「%s」是必答题" % q["title"]}), 400
        else:  # single
            val = (val or "").strip() if isinstance(val, str) else ""
            if q["required"] and not val:
                return jsonify({"ok": False, "msg": "「%s」是必答题" % q["title"]}), 400
        clean[key] = val

    if s["once_only"] and _survey_answered(db, s["id"], me["id"]):
        return jsonify({"ok": True, "already": True})

    db.execute(
        "INSERT INTO survey_responses (survey_id, user_id, answers, created_at) VALUES (?,?,?,?)",
        (s["id"], me["id"], json.dumps(clean, ensure_ascii=False), time.time()))
    db.commit()
    return jsonify({"ok": True})


@app.route('/api/notice/seen', methods=['POST'])
def api_notice_seen():
    """关闭公告弹窗时调用，记录今天已看（登录用户）。"""
    me = current_user()
    if not me:
        return jsonify({"ok": True, "guest": True})
    try:
        _ensure_notice_col(get_db())
        get_db().execute("UPDATE users SET notice_seen_date=? WHERE id=?",
                         (time.strftime('%Y-%m-%d'), me['id']))
        get_db().commit()
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500
    return jsonify({"ok": True})


def get_active_announcement():
    """获取当前有效公告（content 非空即显示，永久置顶）。"""
    row = get_db().execute(
        "SELECT id, content, updated_at, updated_by, pinned_until FROM site_announcement WHERE id=1"
    ).fetchone()
    if not row or not row['content']:
        return {}
    updater = ''
    if row['updated_by']:
        u = get_db().execute("SELECT username FROM users WHERE id=?", (row['updated_by'],)).fetchone()
        if u:
            updater = u['username']
    return {"content": row['content'],
            "updated_at": row['updated_at'],
            "updated_by": updater,
            "pinned_until": row['pinned_until']}


def update_announcement(content, me=None):
    """更新公告（永久置顶）。content 为空则清空（不显示）。me 用于记录发布者。"""
    db = get_db()
    now = time.time()
    content = (content or '').strip()
    pinned_until = 0 if content else 0
    uid = me['id'] if me else 0
    db.execute(
        "UPDATE site_announcement SET content=?, updated_at=?, pinned_until=?, updated_by=? WHERE id=1",
        (content, now, pinned_until, uid))
    db.commit()
    return get_active_announcement()


_GAME_TABLES = {'gomoku': 'gomoku_rooms', 'go': 'go_rooms', 'xq': 'xq_rooms', 'jq': 'jq_rooms',
                'reversi': 'reversi_rooms', 'chess': 'chess_rooms'}

_MM_GAMES = ('gomoku', 'go', 'xq', 'chess', 'reversi', 'jq')
# 只有围棋和揭棋的表有 prev_board 字段（悔棋靠它回退）。
# 揭棋的初始局面每局随机（会洗牌），没法从初始重放，必须存上一步。
_MM_HAS_PREV = ('go', 'jq')
# 认输/悔棋之后要把新状态推给房间里的两个人。国际象棋和黑白棋没有这个广播函数，
# 它们靠前端轮询，这里给 None 就跳过。
_MM_EMIT = {
    'gomoku': '_gmk_room_broadcast',
    'go': '_go_room_broadcast',
    'xq': '_xq_room_broadcast',
    'jq': '_jq_room_broadcast',
    'chess': None,
    'reversi': None,
}
_MM_LOCK = threading.Lock()
_MM_QUEUE = {}
_MM_FOUND = {}
_MM_TIMEOUT = 180
_MM_PICKUP = 90


def _mm_init_board(game):
    if game == 'gomoku':
        return board.gomoku.GOMOKU_EMPTY_BOARD
    if game == 'go':
        return board.go._go_empty_board(19)
    if game == 'xq':
        return board.xq._xq_init()
    if game == 'chess':
        return board.chess.CS_START_FEN
    if game == 'reversi':
        return _rv_init()
    if game == 'jq':
        return _jq._jq_init()
    return ''


def _mm_sweep(game, now):
    q = _MM_QUEUE.get(game)
    if q:
        _MM_QUEUE[game] = [e for e in q if now - e['at'] < _MM_TIMEOUT]
    f = _MM_FOUND.get(game)
    if f:
        for uid in [u for u, v in f.items() if now - v['at'] > _MM_PICKUP]:
            f.pop(uid, None)


def _mm_create_room(game, uid_a, uid_b, fp_a='', ip_a='', fp_b='', ip_b=''):
    db = get_db()
    now = time.time()
    cols = ['name', 'owner_id', 'guest_id', 'mode', 'is_public', 'status',
            'board_state', 'turn_user_id', 'created_at', 'updated_at',
            'owner_fp', 'guest_fp', 'owner_ip', 'guest_ip']
    vals = [_MM_LABEL.get(game, game) + ' 匹配对局', uid_a, uid_b, 0, 0,
            'playing', _mm_init_board(game), uid_a, now, now,
            (fp_a or '')[:24], (fp_b or '')[:24], (ip_a or '')[:64], (ip_b or '')[:64]]
    if game == 'go':
        cols.insert(1, 'board_size')
        vals.insert(1, 19)
    cur = db.execute("INSERT INTO %s (%s) VALUES (%s)"
                     % (_GAME_TABLES[game], ",".join(cols), ",".join("?" * len(cols))), vals)
    db.commit()
    return cur.lastrowid


def _mm_start(game, uid, fp='', ip=''):
    now = time.time()
    with _MM_LOCK:
        _mm_sweep(game, now)
        f = _MM_FOUND.setdefault(game, {})
        hit = f.pop(uid, None)
        if hit and (now - hit['at']) < 20:
            return {"status": "matched", "rid": hit['rid'],
                    "queue": len(_MM_QUEUE.get(game) or [])}
        q = _MM_QUEUE.setdefault(game, [])

        # 同一台设备 10 秒内只能开一局。两个页面轮流点「开始匹配」
        # 就能自己跟自己下，这个时间差最容易被脚本利用。
        if fp:
            last = _MM_PAIR_FP.get(fp)
            if last and (now - last) < _MM_SAME_DEVICE_GAP:
                return {"status": "cooldown", "queue": len(q),
                        "wait": int(_MM_SAME_DEVICE_GAP - (now - last))}

        # 先看有没有能配的（排在别人前面）
        opp, blocked = _mm_pick_opponent(game, uid, fp, ip, now)
        if opp:
            q.remove(opp)
            rid = _mm_create_room(game, uid, opp['uid'], fp, ip,
                                  opp.get('fp') or '', opp.get('ip') or '')
            _mm_note_pair(uid, opp['uid'], fp, opp.get('fp') or '', now)
            f[uid] = {'rid': rid, 'at': now}
            f[opp['uid']] = {'rid': rid, 'at': now}
            return {"status": "matched", "rid": rid, "queue": len(q),
                    "side": "owner"}

        # 没人能配。把被自己挡在后面的记录挪出队列，免得越积越多，
        # 也免得下次换个指纹就绕过去了。
        for e, why in blocked:
            if e in q:
                q.remove(e)
            _log_mod.event("mm_self_block", game=game, uid=uid, other=e['uid'], why=why)

        if not any(e['uid'] == uid for e in q):
            q.append({'uid': uid, 'at': now, 'fp': fp, 'ip': ip})
            _MM_RECENT[uid] = {'fp': fp, 'ip': ip, 'at': now}
            if len(_MM_RECENT) > 2048:
                for k in [k for k, v in _MM_RECENT.items()
                          if now - (v.get('at') or 0) > 3600]:
                    _MM_RECENT.pop(k, None)
        return {"status": "waiting", "queue": len(q)}


def _mm_poll(game, uid):
    now = time.time()
    with _MM_LOCK:
        _mm_sweep(game, now)
        hit = (_MM_FOUND.get(game) or {}).get(uid)
        if hit:
            return {"status": "matched", "rid": hit['rid']}
        q = _MM_QUEUE.get(game) or []
        if any(e['uid'] == uid for e in q):
            return {"status": "waiting", "queue": len(q)}
        return {"status": "idle", "queue": len(q)}


def _mm_cancel(game, uid):
    with _MM_LOCK:
        q = _MM_QUEUE.get(game) or []
        _MM_QUEUE[game] = [e for e in q if e['uid'] != uid]
        f = _MM_FOUND.get(game)
        if f:
            f.pop(uid, None)


def _mm_game_arg():
    return (request.args.get('game') or request.form.get('game') or '').strip()


# ══════════════════════════════════════════════════════════════
#  匹配防自配
# ══════════════════════════════════════════════════════════════
#  同一个账号在手机和电脑上各点一次匹配，就能跟自己下一局 ——
#  入口拦不住（两个请求都是同一个 uid，都通过了登录校验），
#  所以必须在「配对那一刻」按身份排除。
#
#  两个维度看：
#    uid   同一个人（同账号两个窗口/两台设备）—— 直接拒
#    recent_pair 刚配过的一对 —— 45 秒内不再配（防刷分）
#
#  ⚠️ 2026-10-26 去掉 fp / ip 判据：
#  机房、学校、网吧里同款电脑 + 同浏览器，Canvas 指纹完全一样，出口 IP 也一样，
#  原逻辑「fp 相同直接拒」导致这些场景根本配不上（用户实测反馈）。
#  而「同一台设备快速连开」已经由 _MM_SAME_DEVICE_GAP（10 秒）在匹配入口拦住了，
#  所以 fp 直接拒属于重复防守 + 大面积误伤，删掉。
#  代价：同一个人用两个不同浏览器、两个账号，理论上能自配 —— 这个本来也防不住
#  （换浏览器 fp 就变了），但 10 秒设备闸 + 45 秒同对手闸仍在。

_MM_NEW_USER_DAYS = 7        # 注册不满这么多天的号算「疑似小号」
_MM_RECENT = {}              # uid -> {'fp','ip','at'} 最近一次排队，只为控制内存增长


def _mm_fp():
    """当前请求的设备指纹。取不到就退化成空串（不拦）。"""
    try:
        return DFP.client_fingerprint(request)[:24]
    except Exception:
        return ''


def _mm_ip():
    try:
        return (getattr(g, '_ip', None) or _guard.client_ip(request) or '')[:64]
    except Exception:
        return ''


def _mm_is_new(uid, now):
    """注册不满 _MM_NEW_USER_DAYS 天的号算「疑似小号」。

    ⚠️ 2026-10-26 修：大量老用户 created_at 是 0（早期注册没写这个字段），
    原来写 `float(row['created_at'] or now)` —— 0 是 falsy，会拿 now 顶上，
    算出 `now - now = 0 < 7 天`，于是**所有老用户都被判成新号**，
    导致「同 IP 就拒」一直在误杀机房/宿舍的正常对局。
    现在 created_at <= 0 直接按老号处理。
    """
    row = get_db().execute("SELECT created_at FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return False
    try:
        ca = float(row['created_at'] or 0)
    except (TypeError, ValueError):
        return False
    if ca <= 0:
        return False          # 没有注册时间 = 老数据，当老号
    return (now - ca) < _MM_NEW_USER_DAYS * 86400


def _mm_pick_opponent(game, uid, fp, ip, now):
    """在队列里给 uid 找一个「不是自己」的对手。

    遍历而不是只比队首 —— 队首可能被排除，应该继续往下找。
    返回 (entry, blocked)；entry 为 None 表示队列里没有能配的。
    """
    q = _MM_QUEUE.get(game) or []
    blocked = []
    for e in q:
        why = ''
        if e['uid'] == uid:
            why = 'same_user'
        elif _mm_recent_pair(uid, e['uid'], now):
            why = 'recent_pair'
        if why:
            blocked.append((e, why))
            continue
        return e, blocked
    return None, blocked


# 刚刚配过的一对，短时间���再配。
# 堵的是「两个页面轮流快速开局」——配完一个退出，队列空了，
# 第二个再进来时队列里没人，从哪儿看都像正常匹配。
_MM_PAIR_GAP = 45
_MM_SAME_DEVICE_GAP = 10     # 同一台设备两次开局的最短间隔（秒）
_MM_PAIRS = {}       # frozenset({uid_a, uid_b}) -> 上次配对时间
_MM_PAIR_FP = {}     # fp -> 上次配对时间


def _mm_recent_pair(uid_a, uid_b, now):
    key = frozenset((int(uid_a), int(uid_b)))
    t = _MM_PAIRS.get(key)
    if t and (now - t) < _MM_PAIR_GAP:
        return True
    return False


def _mm_note_pair(uid_a, uid_b, fp_a='', fp_b='', now=None):
    """记一次配对。同一台设备短时间内的两场也算。"""
    t = now if now is not None else time.time()
    _MM_PAIRS[frozenset((int(uid_a), int(uid_b)))] = t
    if len(_MM_PAIRS) > 2048:
        for k in [k for k, v in _MM_PAIRS.items() if t - v > _MM_PAIR_GAP * 4]:
            _MM_PAIRS.pop(k, None)
    for f in (fp_a, fp_b):
        if not f:
            continue
        prev = _MM_PAIR_FP.get(f)
        if prev and (t - prev) < 12:
            _MM_PAIRS[frozenset((int(uid_a), int(uid_b)))] = prev
        _MM_PAIR_FP[f] = t
    if len(_MM_PAIR_FP) > 2048:
        for k in [k for k, v in _MM_PAIR_FP.items() if t - v > 600]:
            _MM_PAIR_FP.pop(k, None)


_AI_NAMES = (
    '陆柒公公', '扫地僧', '落子无悔', '一步三算', '手谈一局', '看棋不语',
    '隔壁班同学', '摸鱼选手', '下课再战', '三思后行', '无名棋士', '老张的猫',
    '逆风翻盘', '先手为敬', '卧龙在野', '菜就多练', '落子如飞', '长考选手',
    '棋逢对手', '中盘发力', '官子之神', '弃子争先', '大龙不死', '妙手偶得',
    '课间十分钟', '晚自习战神', '隔壁桌的', '巷口的猫', '三楼老王', '数学课代表',
    '值日班长', '天台棋社', '一子定音', '声东击西', '虚晃一枪', '步步为营',
    '以退为进', '灵机一动', '不按套路', '平平无奇小天才', '别催我在想', '我想想啊',
    '差一点就赢', '赢一把就睡', '再来一局', '手滑了', '深夜棋手', '深藏不露',
    '路过的高手', '围观群众',
)
_AI_HAS = ('gomoku', 'go', 'xq', 'chess', 'reversi')
_AI_RATING_START = 12.0
_AI_RATING_MIN = 1.0
_AI_RATING_MAX = 20.0
_AI_EDGE = 2.0
_AI_TIME_CURVE = ((1, 100.0), (10, 3000.0), (15, 6000.0), (20, 8000.0))
_XQ_VALUE = {'r': 9.0, 'n': 4.0, 'b': 2.0, 'a': 2.0, 'c': 4.5, 'p': 1.0}
_CS_VALUE = {'q': 9.0, 'r': 5.0, 'b': 3.0, 'n': 3.0, 'p': 1.0}
_AI_TIME_RANGE = {
    'gomoku': (100.0, 8000.0),
    'xq': (150.0, 7000.0),
    'chess': (150.0, 5000.0),
    'reversi': (1.0, 45.0),
    'go': (1.0, 15.0),
}


def _ai_level_of(table, rid):
    """后台线程里读房间的 AI 棋力（不能走 get_db，那里要 Flask 上下文）。"""
    try:
        db = sqlite3.connect(DB_PATH, timeout=10)
        try:
            row = db.execute("SELECT ai_level FROM %s WHERE id=?" % table, (rid,)).fetchone()
            return int(row[0]) if row and row[0] else 0
        finally:
            db.close()
    except Exception:
        return 0


def _ai_curve_ms(lv):
    """用户定的四点折线：1 级 100ms / 10 级 3s / 15 级 6s / 20 级 8s，其余插值。"""
    pts = _AI_TIME_CURVE
    lv = max(1.0, min(20.0, float(lv or 1)))
    if lv <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if lv <= x1:
            return y0 + (y1 - y0) * (lv - x0) / float(x1 - x0)
    return pts[-1][1]


def _ai_time_for(game, lv):
    """1~20 级 → 该引擎的思考量。

    按上面那条曲线的**形状**等比映射到各引擎自己的量级：
    五子棋正好就是 100ms~8s；黑白棋/围棋是秒级的，按同一比例缩放。
    """
    lo, hi = _AI_TIME_RANGE.get(game, (200.0, 6000.0))
    c1 = _ai_curve_ms(1)
    c20 = _ai_curve_ms(20)
    c = _ai_curve_ms(lv)
    return int(round(lo + (hi - lo) * (c - c1) / (c20 - c1)))


# 各引擎按「深度」控制强度的范围（黑白棋用 Edax 的 depth 参数）
_AI_DEPTH_RANGE = {
    'reversi': (2, 30),     # 1 级搜 2 层，20 级搜 30 层
}


def _ai_depth_for(game, lv):
    """AI 等级（1~20）→ 引擎搜索深度。黑白棋：2~30 层。"""
    lo, hi = _AI_DEPTH_RANGE.get(game, (2, 12))
    lv = max(1.0, min(20.0, float(lv or 1)))
    return int(round(lo + (hi - lo) * (lv - 1) / 19.0))


def _ai_depth_for_room(table, rid, default=20):
    """按房间的 AI 棋力算搜索深度（后台线程用，不走 get_db）。"""
    lv = _ai_level_of(table, rid)
    if not lv:
        return default
    return _ai_depth_for('reversi', lv)


def _ai_ms_for(table, rid, default, game=None):
    """房间的 AI 棋力 → 该引擎的思考量；老房间（等级 0）用原来的默认值。"""
    lv = _ai_level_of(table, rid)
    if not lv or not game:
        return default
    return _ai_time_for(game, lv)


def _ai_pick_name(rid):
    if not rid:
        return _AI_NAMES[0]
    return _AI_NAMES[int(rid) % len(_AI_NAMES)]


def _ai_rating_of(uid, game):
    """玩家在这个棋上的评级（1~20）。没有记录返回 0，由调用方当「新玩家」处理。"""
    db = get_db()
    row = db.execute("SELECT rating FROM board_game_stats WHERE user_id=? AND game=?",
                     (uid, game)).fetchone()
    return float(row["rating"] or 0) if row else 0.0


def _ai_level_for(uid, game):
    """1~20 级。新玩家第一把给 14 级；有评级就给「评级 + 2」。"""
    db = get_db()
    if not db.execute("SELECT 1 FROM board_game_stats WHERE user_id=? AND game=?",
                      (uid, game)).fetchone():
        return 14
    r = _ai_rating_of(uid, game)
    if r <= 0:
        return 14
    return int(max(1, min(20, round(r + _AI_EDGE))))


def _ai_rating_apply(uid, game, opp_rating, score, quality=0.5):
    """Elo 更新评级。score：赢 1 / 平 0.5 / 输 0。

    PvP 和 PvAI 都走这一套：对手评级就是对方玩家的评级，或者人机房里的 AI 等级。
    这样「赢一个 18 级的 AI」比「赢一个 5 级的」涨得多，跟人下也一样。

    quality 是「这局下得怎么样」（0~1，0.5 算平均），由 _quality_of 从盘面算出来：
    下得好 → 赢的时候多涨、输的时候少扣；下得烂 → 反过来。
    """
    me = _ai_rating_of(uid, game)
    if me <= 0:
        me = _AI_RATING_START
    exp = 1.0 / (1.0 + 10.0 ** ((float(opp_rating) - me) / 8.0))
    base = 2.0 * (score - exp)
    q = max(0.0, min(1.0, float(quality)))
    mag = 0.75 + 0.5 * q
    delta = base * mag if score >= 0.5 else base * (1.5 - mag)
    new = max(_AI_RATING_MIN, min(_AI_RATING_MAX, me + delta))
    db = get_db()
    db.execute("INSERT OR IGNORE INTO board_game_stats (user_id, game, rating) VALUES (?,?,?)",
               (uid, game, _AI_RATING_START))
    db.execute("UPDATE board_game_stats SET rating=? WHERE user_id=? AND game=?",
               (new, uid, game))


def _gomoku_quality(bs, side):
    """五子棋：数双方的「活线」——成三、成四还留着活口的那种。

    自己路子比对手多，说明攻得凶、也堵得住，就是下得好。
    """
    n = board.gomoku.GOMOKU_N
    if len(bs) != n * n:
        return 0.5
    mine = theirs = 0.0
    for y in range(n):
        for x in range(n):
            v = bs[y * n + x]
            if v not in ('1', '2'):
                continue
            sc = 0.0
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                cnt = 1
                ends = 0
                for step in (1, -1):
                    cx, cy = x + dx * step, y + dy * step
                    while 0 <= cx < n and 0 <= cy < n and bs[cy * n + cx] == v:
                        cnt += 1
                        cx += dx * step
                        cy += dy * step
                    if 0 <= cx < n and 0 <= cy < n and bs[cy * n + cx] == '.':
                        ends += 1
                sc += _gomoku_line_score(cnt, ends)
            if v == side:
                mine += sc
            else:
                theirs += sc
    if mine + theirs <= 0:
        return 0.5
    return mine / (mine + theirs)


def _rv_quality(bs, side):
    """黑白棋：角 + 行动力。这两个是最经典的强弱指标（终局子数反而不准）。"""
    if len(bs) != 64:
        return 0.5
    other = '2' if side == '1' else '1'
    parts = []
    try:
        m_mine = len(_rv_legal_moves(bs, side))
        m_other = len(_rv_legal_moves(bs, other))
        if m_mine + m_other > 0:
            parts.append(m_mine / float(m_mine + m_other))
    except Exception:
        pass
    corners = (0, 7, 56, 63)
    c_mine = sum(1 for i in corners if bs[i] == side)
    c_other = sum(1 for i in corners if bs[i] == other)
    if c_mine + c_other > 0:
        parts.append(c_mine / float(c_mine + c_other))
    return sum(parts) / len(parts) if parts else 0.5


def _material_quality(board, side, values):
    """子力：自己还剩多少子，占双方总和的比例。side '1' 对应大写（红/白）。"""
    mine = theirs = 0.0
    for ch in board:
        v = values.get(ch.lower(), 0.0)
        if v <= 0:
            continue
        if ch.isupper() == (side == '1'):
            mine += v
        else:
            theirs += v
    if mine + theirs <= 0:
        return 0.5
    return mine / (mine + theirs)


def _xq_quality(bs, side):
    """象棋 / 揭棋：看子力。揭棋的盘面是 display|truth，取 truth 那半。"""
    truth = bs.split('|')[-1] if '|' in bs else bs
    if len(truth) != board.xq.XQ_W * board.xq.XQ_H:
        return 0.5
    return _material_quality(truth, side, _XQ_VALUE)


def _cs_quality(bs, side):
    """国际象棋：看子力。盘面是 FEN，数字和空格会被自动跳过。"""
    return _material_quality(bs, side, _CS_VALUE)


def _go_quality(bs, side):
    """围棋：按「封闭区域」数地盘。

    先把空白连成块，再看每块被谁围着：
      · 只被一方围着 → 整块算那方的地（这就是人肉点目时的基本规则）
      · 被双方围着（单官/公共区域）→ 谁的都不算，两边都不计
    比原来「每个空点看固定半径 2 里谁的子多」准得多：
    大围空的中间离墙很远，老算法根本数不到。
    局限：默认盘上棋子都是活的（死子判定得靠引擎，这里不做）。
    """
    n = int(round(len(bs) ** 0.5)) if bs else 0
    if n < 2 or n * n != len(bs):
        return 0.5
    other = '2' if side == '1' else '1'
    mine = float(bs.count(side))
    theirs = float(bs.count(other))
    seen = [False] * (n * n)
    for i in range(n * n):
        if bs[i] != '.' or seen[i]:
            continue
        region = []
        stack = [i]
        seen[i] = True
        touch = set()
        while stack:
            j = stack.pop()
            region.append(j)
            x, y = j % n, j // n
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                cx, cy = x + dx, y + dy
                if not (0 <= cx < n and 0 <= cy < n):
                    continue
                k = cy * n + cx
                v = bs[k]
                if v == '.':
                    if not seen[k]:
                        seen[k] = True
                        stack.append(k)
                else:
                    touch.add(v)
        if len(touch) == 1:
            if side in touch:
                mine += len(region)
            else:
                theirs += len(region)
        # 被双方围着的（单官/公共区域）：谁的都不是，两边都不算 ——
        # 这正是围棋点目的规矩。要是五五分，盘上大片未定空点会把
        # 真正围到的地的优势稀释掉，围得大的反而显得不占优。
    if mine + theirs <= 0:
        return 0.5
    return mine / (mine + theirs)


def _quality_of(game, bs, side, plies, opp_level):
    """这局「下得怎么样」（0~1，0.5 算平均）。

    一半看盘面质量（各棋自己的指标），一半看撑了多久 ——
    撑得比这个等级的对手该有的长度还久，本身就是下得不差。
    """
    q_len = 0.0
    if plies:
        q_len = min(1.0, plies / max(6.0, float(opp_level or 10) * 3.0))
    q_board = None
    try:
        if game == 'gomoku':
            q_board = _gomoku_quality(bs, side)
        elif game == 'reversi':
            q_board = _rv_quality(bs, side)
        elif game in ('xq', 'jq'):
            q_board = _xq_quality(bs, side)
        elif game == 'chess':
            q_board = _cs_quality(bs, side)
        elif game == 'go':
            q_board = _go_quality(bs, side)
    except Exception:
        q_board = None
    if q_board is None:
        return q_len
    return 0.5 * q_len + 0.5 * q_board


def _mm_create_ai_room(game, uid):
    db = get_db()
    now = time.time()
    cols = ['name', 'owner_id', 'mode', 'is_public', 'status',
            'board_state', 'turn_user_id', 'created_at', 'updated_at',
            'ai_name', 'ai_level']
    vals = [_MM_LABEL.get(game, game) + ' 匹配对局', uid, 1, 0, 'playing',
            _mm_init_board(game), uid, now, now, '', 14]
    if game == 'go':
        cols.insert(1, 'board_size')
        vals.insert(1, 19)
    cur = db.execute("INSERT INTO %s (%s) VALUES (%s)"
                     % (_GAME_TABLES[game], ",".join(cols), ",".join("?" * len(cols))), vals)
    rid = cur.lastrowid
    db.execute("UPDATE %s SET ai_name=?, ai_level=? WHERE id=?" % _GAME_TABLES[game],
               (_ai_pick_name(rid), _ai_level_for(uid, game), rid))
    db.commit()
    return rid


def _mm_warm(game, rid):
    try:
        if game == 'gomoku':
            _rapfi_ensure_ready(rid)
        elif game == 'go':
            board.go._katago_warm(rid, 19)
        elif game == 'xq':
            board.xq._pikafish_warm(rid)
        elif game == 'chess':
            _chess_warm(rid)
    except Exception:
        pass


@app.route("/api/mm/ai", methods=["POST"])
def mm_ai():
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})
    game = _mm_game_arg()
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    if game not in _AI_HAS:
        return jsonify({"ok": False, "msg": "这个棋暂时还没有 AI 对手"})
    _mm_cancel(game, me['id'])
    try:
        rid = _mm_create_ai_room(game, me['id'])
    except Exception as e:
        return jsonify({"ok": False, "msg": "创建 AI 对局失败：" + str(e)[:80]})
    threading.Thread(target=_mm_warm, args=(game, rid), daemon=True).start()
    db = get_db()
    row = db.execute("SELECT ai_name, ai_level FROM %s WHERE id=?" % _GAME_TABLES[game],
                     (rid,)).fetchone()
    return jsonify({"ok": True, "rid": rid,
                    "ai_name": row["ai_name"], "ai_level": row["ai_level"]})


_PRESENCE_LOCK = threading.Lock()
_PRESENCE = {}
_AWAY_LIMIT = 120
_AWAY_SWEEP = 20


@app.route("/api/<game>/<int:rid>/alive", methods=["POST"])
def mm_alive(game, rid):
    """前端每 30 秒报一次「我还在」。离开超过两分钟会被判负。"""
    me = current_user()
    if not me or game not in _MM_GAMES:
        return jsonify({"ok": False})
    with _PRESENCE_LOCK:
        _PRESENCE.setdefault((game, rid), {})[me['id']] = time.time()
    return jsonify({"ok": True})


def _mm_away_sweep():
    """后台线程：谁超过两分钟没报心跳，就判他负。

    只对人人对局生效（人机房的对手不会掉线），而且只判「曾经报过心跳」的人 ——
    从没出现过的不判，免得刚开局就误伤。
    """
    while True:
        time.sleep(_AWAY_SWEEP)
        try:
            now = time.time()
            with _PRESENCE_LOCK:
                snapshot = {k: dict(v) for k, v in _PRESENCE.items()}
            for (game, rid), seen in snapshot.items():
                if game not in _MM_GAMES:
                    continue
                try:
                    with app.app_context():
                        db = get_db()
                        room = db.execute("SELECT * FROM %s WHERE id=?" % _GAME_TABLES[game],
                                          (rid,)).fetchone()
                        if (not room or room['status'] != 'playing' or room['mode'] == 1):
                            with _PRESENCE_LOCK:
                                _PRESENCE.pop((game, rid), None)
                            continue
                        for uid, ts in seen.items():
                            if now - ts <= _AWAY_LIMIT:
                                continue
                            if uid not in (room['owner_id'], room['guest_id']):
                                continue
                            win = (room['guest_id'] if uid == room['owner_id']
                                   else room['owner_id'])
                            if not win:
                                continue
                            db.execute("UPDATE %s SET status='finished', winner_id=?, "
                                       "updated_at=? WHERE id=?" % _GAME_TABLES[game],
                                       (win, now, rid))
                            db.commit()
                            try:
                                _award_game_stats(game, rid)
                            except Exception:
                                pass
                            try:
                                fn = _MM_EMIT.get(game)
                                if fn:
                                    globals()[fn](rid)
                            except Exception:
                                pass
                            break
                except Exception:
                    pass
        except Exception:
            pass


def _mm_start_away_sweep():
    t = threading.Thread(target=_mm_away_sweep, daemon=True)
    t.start()
    return t


try:
    _mm_start_away_sweep()
except Exception:
    pass


@app.route("/api/<game>/<int:rid>/resign", methods=["POST"])
def mm_resign(game, rid):
    """统一认输：六个棋共用一个入口。

    人机房认输 → winner_id = -1（表示对方赢）；人人对局 → winner_id = 对手 id。
    认输正常结算（计入评级），所以后面照常调 _award_game_stats。
    """
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    db = get_db()
    tbl = _GAME_TABLES[game]
    room = db.execute("SELECT * FROM %s WHERE id=?" % tbl, (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "对局不存在"})
    if room['status'] != 'playing':
        return jsonify({"ok": False, "msg": "对局已经结束了"})
    if me['id'] not in (room['owner_id'], room['guest_id']):
        return jsonify({"ok": False, "msg": "你不在这个对局里"})
    if room['mode'] == 1:
        win_id = -1
    else:
        win_id = room['guest_id'] if me['id'] == room['owner_id'] else room['owner_id']
    db.execute("UPDATE %s SET status='finished', winner_id=?, updated_at=? WHERE id=?" % tbl,
               (win_id, time.time(), rid))
    db.commit()
    try:
        _award_game_stats(game, rid)
    except Exception:
        pass
    try:
        fn = _MM_EMIT.get(game)
        if fn:
            globals()[fn](rid)
    except Exception:
        pass
    return jsonify({"ok": True, "winner_id": win_id})


_UNDO_LOCK = threading.Lock()
_UNDO_REQ = {}
_UNDO_AI_YES = 0.7
_UNDO_TTL = 30


def _mm_undo_prev(game, room):
    """算出「撤销最后一手」之后的局面，算不出来返回 None。

    各棋能用来回退的信息不一样，所以分开处理：
      五子棋 没有提子，抹掉最后那一子就行（最简单）
      围棋 有 prev_board，直接恢复
      黑白棋 move_hist 重放
      象棋/揭棋 uci_moves 重放
      国际象棋 有现成的 _cs_replay_state
    """
    try:
        if game == 'gomoku':
            bs = room['board_state'] or ''
            x, y = room['last_move_x'], room['last_move_y']
            if x is None or y is None or len(bs) != board.gomoku.GOMOKU_N * board.gomoku.GOMOKU_N:
                return None
            i = int(y) * board.gomoku.GOMOKU_N + int(x)
            if not (0 <= i < len(bs)) or bs[i] == '.':
                return None
            return bs[:i] + '.' + bs[i + 1:]

        if game == 'go':
            return (room['prev_board'] or '') or None

        if game == 'reversi':
            hist = _rv_hist_split(room['move_hist'] or '')
            if not hist:
                return None
            b = _rv_init()
            for (c, v) in hist[:-1]:
                xy = _rv_from_vertex(v)
                if not xy:
                    return None
                b = _rv_apply(b, xy[0], xy[1], board.reversi.RV_BLACK if c == 'b' else board.reversi.RV_WHITE)
            return b

        if game == 'chess':
            mv = (room['uci_moves'] or '').split()
            if not mv:
                return None
            return _cs_fen(_cs_replay_state(' '.join(mv[:-1])))

        if game == 'xq':
            mv = (room['uci_moves'] or '').split()
            if not mv:
                return None
            b = board.xq._xq_init()
            for u in mv[:-1]:
                a = _xq_uci_to_xy(u[:2])
                c = _xq_uci_to_xy(u[2:4])
                if not a or not c:
                    return None
                b = board.xq._xq_make_move(b, a[0], a[1], c[0], c[1])[0]
                if b is None:
                    return None
            return b

        if game == 'jq':
            # 揭棋初始局面每局随机（会洗牌），没法从初始重放，只能靠落子时存的 prev_board
            return (room['prev_board'] or '') or None
    except Exception:
        return None
    return None


def _mm_do_undo(game, rid, prev, room, by=None):
    """真的把局面退回去。

    ⚠️ 悔完必须把 prev_board 清掉：围棋和揭棋靠它回退，
    不清的话再悔一次会退回「上上次」的局面（错位）。
    ⚠️ turn_user_id 要给**悔棋那个人**，不是房主 —— 写死 owner_id 的话
    客方悔完还是轮房主走，看着像悔棋没生效（反馈 #51）。
    """
    db = get_db()
    tbl = _GAME_TABLES[game]
    now = time.time()
    turn = by if by else room['owner_id']
    try:
        if game in _MM_HAS_PREV:
            db.execute("UPDATE %s SET board_state=?, prev_board='', turn_user_id=?, "
                       "updated_at=? WHERE id=?" % tbl, (prev, turn, now, rid))
        else:
            db.execute("UPDATE %s SET board_state=?, turn_user_id=?, updated_at=? WHERE id=?" % tbl,
                       (prev, turn, now, rid))
        db.commit()
    except Exception as e:
        return jsonify({"ok": False, "msg": "悔棋失败：" + str(e)[:60]})
    try:
        fn = _MM_EMIT.get(game)
        if fn:
            globals()[fn](rid)
    except Exception:
        pass
    return jsonify({"ok": True, "done": True})


@app.route("/api/<game>/<int:rid>/undo", methods=["POST"])
def mm_undo(game, rid):
    """请求悔棋。人机房当场决定（七成同意）；人人房记下请求，等对手应答。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    db = get_db()
    room = db.execute("SELECT * FROM %s WHERE id=?" % _GAME_TABLES[game], (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "对局不存在"})
    if room['status'] != 'playing':
        return jsonify({"ok": False, "msg": "对局已经结束了"})
    if me['id'] not in (room['owner_id'], room['guest_id']):
        return jsonify({"ok": False, "msg": "你不在这个对局里"})
    if room['turn_user_id'] == me['id']:
        return jsonify({"ok": False, "msg": "等对手走完这一手再悔棋"})
    prev = _mm_undo_prev(game, room)
    if not prev:
        return jsonify({"ok": False, "msg": "这一步悔不了"})
    if room['mode'] == 1:
        if random.random() > _UNDO_AI_YES:
            return jsonify({"ok": False, "msg": "对手不同意悔棋"})
        return _mm_do_undo(game, rid, prev, room, me['id'])
    with _UNDO_LOCK:
        _UNDO_REQ[(game, rid)] = {'by': me['id'], 'at': time.time()}
    return jsonify({"ok": True, "waiting": True, "msg": "已请求悔棋，等对手回应"})


@app.route("/api/<game>/<int:rid>/undo-poll")
def mm_undo_poll(game, rid):
    """对手那边定时拉这个，看有没有人请求悔棋。"""
    me = current_user()
    if not me or game not in _MM_GAMES:
        return jsonify({"ok": False})
    with _UNDO_LOCK:
        req = _UNDO_REQ.get((game, rid))
        if req and time.time() - req['at'] > _UNDO_TTL:
            _UNDO_REQ.pop((game, rid), None)
            req = None
        pending = bool(req and req['by'] != me['id'])
    return jsonify({"ok": True, "pending": pending})


@app.route("/api/<game>/<int:rid>/undo-reply", methods=["POST"])
def mm_undo_reply(game, rid):
    """对手应答悔棋请求。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    accept = str(request.form.get('accept') or '') == '1'
    with _UNDO_LOCK:
        req = _UNDO_REQ.pop((game, rid), None)
    if not req:
        return jsonify({"ok": False, "msg": "没有待处理的悔棋请求"})
    if req['by'] == me['id']:
        return jsonify({"ok": False, "msg": "不能回应自己的请求"})
    if not accept:
        return jsonify({"ok": True, "rejected": True})
    db = get_db()
    room = db.execute("SELECT * FROM %s WHERE id=?" % _GAME_TABLES[game], (rid,)).fetchone()
    if not room or room['status'] != 'playing':
        return jsonify({"ok": False, "msg": "对局已经结束了"})
    prev = _mm_undo_prev(game, room)
    if not prev:
        return jsonify({"ok": False, "msg": "这一步悔不了"})
    return _mm_do_undo(game, rid, prev, room, req['by'])


FEEDBACK_FOLDER = os.path.join(BASE_DIR, "static", "feedback")
os.makedirs(FEEDBACK_FOLDER, exist_ok=True)

_FB_KINDS = ('idea', 'bug')


def _fb_can_see(me, kind):
    """谁能看哪一类反馈：功能建议管理员和超管都能看，Bug 反馈只有超管能看。"""
    if not me:
        return False
    if kind == 'bug':
        return bool(_row_get(me, 'is_super_admin'))
    return bool(me['is_admin']) or bool(_row_get(me, 'is_super_admin'))


@app.route("/feedback")
@login_required
def feedback_page():
    me = current_user()
    db = get_db()
    # 两个分类谁都能切；能不能看到「别人提的」才看权限
    tabs = list(_FB_KINDS)
    kind = request.args.get('kind') or 'idea'
    if kind not in _FB_KINDS:
        kind = 'idea'
    if _fb_can_see(me, kind):
        rows = db.execute(
            "SELECT f.*, u.username FROM feedback f LEFT JOIN users u ON u.id=f.user_id "
            "WHERE f.kind=? ORDER BY f.id DESC LIMIT 100", (kind,)).fetchall()
        seen_all = True
    else:
        rows = db.execute(
            "SELECT f.*, u.username FROM feedback f LEFT JOIN users u ON u.id=f.user_id "
            "WHERE f.user_id=? AND f.kind=? ORDER BY f.id DESC LIMIT 50",
            (me['id'], kind)).fetchall()
        seen_all = False
    counts = {}
    for k in _FB_KINDS:
        if _fb_can_see(me, k):
            counts[k] = db.execute("SELECT COUNT(*) FROM feedback WHERE kind=?", (k,)).fetchone()[0]
        else:
            counts[k] = db.execute("SELECT COUNT(*) FROM feedback WHERE user_id=? AND kind=?",
                                   (me['id'], k)).fetchone()[0]
    return render_template("feedback.html", user=me, active='feedback',
                           kind=kind, tabs=tabs, rows=rows, counts=counts, seen_all=seen_all,
                           kinds={'idea': '功能建议', 'bug': 'Bug 反馈'})


@app.route("/feedback", methods=["POST"])
@login_required
def feedback_submit():
    me = current_user()
    kind = (request.form.get('kind') or 'idea').strip()
    content = (request.form.get('content') or '').strip()
    if kind not in _FB_KINDS:
        kind = 'idea'
    if len(content) < 5:
        flash("说明太短了，多写两句吧", "error")
        return redirect(url_for('feedback_page'))
    if len(content) > 2000:
        content = content[:2000]
    image = ''
    f = request.files.get('image')
    if f and f.filename:
        if not (allowed_file(f.filename) and is_image(f.filename)):
            flash("图片格式不支持（png / jpg / gif）", "error")
            return redirect(url_for('feedback_page'))
        ext = f.filename.rsplit('.', 1)[1].lower()
        image = "fb_%d_%d.%s" % (me['id'], int(time.time()), ext)
        try:
            f.save(os.path.join(FEEDBACK_FOLDER, image))
        except Exception:
            image = ''
    db = get_db()
    db.execute("INSERT INTO feedback (user_id, kind, content, image, created_at) "
               "VALUES (?,?,?,?,?)", (me['id'], kind, content, image, time.time()))
    db.commit()
    flash("收到了，谢谢反馈！", "ok")
    return redirect(url_for('feedback_page'))


@app.route("/feedback/<int:fid>/reply", methods=["POST"])
@login_required
def feedback_reply(fid):
    """管理员/超管回复一条反馈，顺手通知提交者。"""
    me = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()
    if not row:
        flash("这条反馈不存在", "error")
        return redirect(url_for('feedback_page'))
    if not _fb_can_see(me, row['kind']):
        flash("没有权限回复这一类反馈", "error")
        return redirect(url_for('feedback_page'))
    text = (request.form.get('reply') or '').strip()[:1000]
    if not text:
        flash("回复不能是空的", "error")
        return redirect(url_for('feedback_page', kind=row['kind']))
    db.execute("UPDATE feedback SET reply=?, status='done' WHERE id=?", (text, fid))
    db.commit()
    if row['user_id'] != me['id']:
        try:
            add_notification(row['user_id'], 'feedback',
                             "你的反馈有回复了：" + text[:40],
                             url_for('feedback_page', kind=row['kind']))
        except Exception:
            pass
    flash("回复已发出", "ok")
    return redirect(url_for('feedback_page', kind=row['kind']))


@app.route("/feedback/<int:fid>/done", methods=["POST"])
@login_required
def feedback_done(fid):
    me = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()
    if not row or not _fb_can_see(me, row['kind']):
        return jsonify({"ok": False, "msg": "没有权限"}), 403
    now = 'open' if row['status'] == 'done' else 'done'
    db.execute("UPDATE feedback SET status=? WHERE id=?", (now, fid))
    db.commit()
    return jsonify({"ok": True, "status": now})


@app.route("/api/mm/me")
def mm_me():
    me = current_user()
    if not me:
        return jsonify({"ok": False})
    game = _mm_game_arg()
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    db = get_db()
    row = db.execute("SELECT wins, losses, draws, games, ai_wins, ai_games "
                     "FROM board_game_stats WHERE user_id=? AND game=?",
                     (me['id'], game)).fetchone()
    r = _ai_rating_of(me['id'], game)
    return jsonify({
        "ok": True,
        "rating": round(r, 1) if r > 0 else 0,
        "next_level": _ai_level_for(me['id'], game),
        "games": (row["games"] if row else 0) or 0,
        "wins": (row["wins"] if row else 0) or 0,
        "losses": (row["losses"] if row else 0) or 0,
        "ai_games": (row["ai_games"] if row else 0) or 0,
        "ai_wins": (row["ai_wins"] if row else 0) or 0,
    })


@app.route("/api/mm/pending")
def mm_pending():
    """大厅加载时问一句：有没有还没打完的局。有就让用户决定要不要重连。"""
    me = current_user()
    game = _mm_game_arg()
    if not me or game not in _MM_GAMES:
        return jsonify({"ok": False})
    db = get_db()
    row = db.execute(
        "SELECT id FROM %s WHERE status='playing' AND updated_at > ? "
        "AND (owner_id=? OR guest_id=?) ORDER BY id DESC LIMIT 1" % _GAME_TABLES[game],
        (time.time() - 1800, me['id'], me['id'])).fetchone()
    return jsonify({"ok": True, "rid": row['id'] if row else 0})


@app.route("/api/mm/start", methods=["POST"])
def mm_start():
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})
    game = _mm_game_arg()
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    # 有没打完的局就直接回去，别另开一局把旧的晾在那（半小时内算数）
    db = get_db()
    row = db.execute(
        "SELECT id FROM %s WHERE status='playing' AND updated_at > ? "
        "AND (owner_id=? OR guest_id=?) ORDER BY id DESC LIMIT 1" % _GAME_TABLES[game],
        (time.time() - 1800, me['id'], me['id'])).fetchone()
    if row:
        return jsonify({"ok": True, "status": "matched", "rid": row['id'], "resumed": True})
    r = _mm_start(game, me['id'], _mm_fp(), _mm_ip())
    r['ok'] = True
    return jsonify(r)


@app.route("/api/mm/poll")
def mm_poll():
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})
    game = _mm_game_arg()
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    r = _mm_poll(game, me['id'])
    r['ok'] = True
    return jsonify(r)


@app.route("/api/mm/cancel", methods=["POST"])
def mm_cancel():
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "请先登录"})
    game = _mm_game_arg()
    if game not in _MM_GAMES:
        return jsonify({"ok": False, "msg": "游戏不存在"})
    _mm_cancel(game, me['id'])
    return jsonify({"ok": True})


def _mm_room_is_selfplay(room):
    """这局是不是「自己配自己」。

    配对时已经按 uid / fp / ip 挡过一层，这里是最后一道闸 ——
    万一前面的规则被绕过（改代码、伪造头、以后新增的匹配路径），
    结算这一步还能拦住，胜场和评级都不记。

    返回 (是否自配, 原因)。
    """
    owner = room['owner_id']
    guest = room['guest_id']
    if not owner or not guest:
        return True, 'missing_user'
    if int(owner) == int(guest):
        return True, 'same_user'
    ofp = (room['owner_fp'] or '')[:24]
    gfp = (room['guest_fp'] or '')[:24]
    oip = (room['owner_ip'] or '')[:64]
    gip = (room['guest_ip'] or '')[:64]
    same_fp = bool(ofp and gfp and ofp == gfp)
    same_ip = bool(oip and gip and oip == gip)
    # 2026-10-26：机房/学校同款机器 + 同浏览器 → fp 必然相同，单看 fp 误伤太大。
    # 改成「同 fp 且同 IP 且双方都是新号」才判自配；机房老号正常记战绩。
    if same_fp and same_ip:
        now = time.time()
        if _mm_is_new(int(owner), now) and _mm_is_new(int(guest), now):
            return True, 'same_device_new'
    elif same_ip:
        # 同 IP 不一定是自配（宿舍/网吧正常情况），只有双方都是新号才判死
        now = time.time()
        if _mm_is_new(int(owner), now) and _mm_is_new(int(guest), now):
            return True, 'same_ip_new'
    return False, ''


def _award_game_stats(game, rid):
    """游戏结束时，把胜场/手数累计进 game_stats。只会执行一次（stats_awarded 防重）。"""
    if game not in _GAME_TABLES:
        return
    db = get_db()
    tbl = _GAME_TABLES[game]
    room = db.execute("SELECT * FROM %s WHERE id=?" % tbl, (rid,)).fetchone()
    if not room or room['status'] != 'finished' or room['stats_awarded']:
        return
    if room['mode'] != 1:
        selfplay, why = _mm_room_is_selfplay(room)
        if selfplay:
            _log_mod.event("mm_selfplay_blocked", game=game, rid=rid,
                           owner=room['owner_id'], guest=room['guest_id'], why=why)
            db.execute("UPDATE %s SET stats_awarded=1 WHERE id=?" % tbl, (rid,))
            db.commit()
            return
    owner = room['owner_id']
    guest = room['guest_id']
    winner = room['winner_id']
    mode = room['mode']
    bs = room['board_state'] or ''
    if game in ('xq', 'jq'):
        plies = len((room['uci_moves'] or '').split()) if room['uci_moves'] else 0
    elif game == 'gomoku':
        plies = sum(1 for c in bs if c != '.')
    elif game == 'reversi':
        plies = sum(1 for c in bs if c in '12')
    elif game == 'chess':
        plies = len((room['uci_moves'] or '').split())
    else:
        plies = sum(1 for c in bs if c in '12')
    for uid in (p for p in (owner, guest) if p):
        db.execute("INSERT OR IGNORE INTO board_game_stats (user_id, game) VALUES (?,?)", (uid, game))
        if not winner:
            db.execute("UPDATE board_game_stats SET draws=draws+1, games=games+1 WHERE user_id=? AND game=?",
                       (uid, game))
            sc = 0.5
        elif winner == uid:
            db.execute("UPDATE board_game_stats SET wins=wins+1, games=games+1 WHERE user_id=? AND game=?",
                       (uid, game))
            sc = 1.0
        else:
            db.execute("UPDATE board_game_stats SET losses=losses+1, games=games+1 WHERE user_id=? AND game=?",
                       (uid, game))
            sc = 0.0
        if mode == 1:
            opp = float(room["ai_level"] or 0) or _AI_RATING_START
        else:
            other = guest if uid == owner else owner
            opp = _ai_rating_of(other, game) if other else 0.0
            if opp <= 0:
                opp = _AI_RATING_START
        _ai_rating_apply(uid, game, opp, sc,
                         _quality_of(game, bs, '1' if uid == owner else '2', plies, opp))
        if mode == 1 and uid == owner and plies > 0:
            db.execute("UPDATE board_game_stats SET ai_games=ai_games+1, "
                       "ai_best_steps=CASE WHEN ? > ai_best_steps THEN ? ELSE ai_best_steps END "
                       "WHERE user_id=? AND game=?", (plies, plies, uid, game))
            if winner and winner == uid:
                db.execute("UPDATE board_game_stats SET ai_wins=ai_wins+1 WHERE user_id=? AND game=?",
                           (uid, game))
    db.execute("UPDATE %s SET stats_awarded=1 WHERE id=?" % tbl, (rid,))
    db.commit()


def _ai_survival_leaderboard(games, limit=15):
    """
    与 AI 对战「存活步数榜」。
    排序：战胜过 AI 的玩家排在最前；同按 ai_best_steps（单局最多步数）降序。
    games 可以是单个字符串或字符串列表/元组。
    """
    db = get_db()
    if isinstance(games, str):
        games = (games,)
    if not games:
        return []
    placeholders = ','.join('?' * len(games))
    rows = db.execute(
        "SELECT u.username, "
        "SUM(gs.ai_wins) AS ai_wins, "
        "MAX(gs.ai_best_steps) AS ai_best_steps, "
        "SUM(gs.ai_games) AS ai_games "
        "FROM board_game_stats gs JOIN users u ON u.id=gs.user_id "
        "WHERE gs.game IN (%s) AND gs.ai_games>0 "
        "GROUP BY u.id "
        "ORDER BY ai_wins DESC, ai_best_steps DESC, ai_games ASC "
        "LIMIT ?" % placeholders,
        tuple(games) + (limit,)).fetchall()
    return rows


@app.route("/api/announcement", methods=["GET", "POST"])
@login_required
def api_announcement():
    """GET 公开返回当前公告；POST 仅管理员/超管可改。"""
    if request.method == "GET":
        return jsonify({"ok": True, "announcement": get_active_announcement() or None})
    me = current_user()
    if not (me['is_admin'] or me['is_super_admin']):
        return jsonify({"ok": False, "msg": "无权限"}), 403
    content = request.form.get("content") or (request.get_json(silent=True) or {}).get("content", "")
    a = update_announcement(content, me)
    try:
        socketio.emit('site_announcement', a or {}, broadcast=True)
    except Exception:
        pass
    return jsonify({"ok": True, "announcement": a, "msg": "已清空" if not a else "已发布"})


def _music_new_filename(orig_name):
    ext = (orig_name.rsplit('.', 1)[-1] if '.' in orig_name else 'mp3').lower()
    if ext not in MUSIC_ALLOWED_EXT:
        ext = 'mp3'
    return "%d_%s.%s" % (int(time.time() * 1000), ''.join(random.choice('0123456789abcdef') for _ in range(6)), ext)


@app.route("/music")
@login_required
def music_page():
    me = current_user()
    db = get_db()
    tracks = db.execute(
        "SELECT t.*, "
        " EXISTS(SELECT 1 FROM music_playlist p WHERE p.track_id=t.id AND p.user_id=?) AS in_my_list "
        "FROM music_tracks t ORDER BY t.id DESC", (me['id'],)).fetchall()
    mylist = db.execute(
        "SELECT t.*, p.position FROM music_playlist p JOIN music_tracks t ON t.id=p.track_id "
        "WHERE p.user_id=? ORDER BY p.position ASC", (me['id'],)).fetchall()
    return render_template("music.html", tracks=tracks, mylist=mylist, active='music')


def _music_compress(src_path):
    """用 ffmpeg 把音频压成 Opus（.ogg 容器）。

    返回 (新文件名, 压缩后字节数)；没压/失败返回 (None, 0)，调用方保留原文件。

    ⚠️ 压完必须换文件名后缀 —— 这是踩过的坑：
    之前输出临时文件带 .mp3 后缀，然后 os.replace 回了原名（比如 .flac），
    结果 mp3 数据装在 .flac 文件里，播放器按 flac 解码直接播不出来。
    所以这里返回新名字，由调用方一起更新数据库。

    其他设计要点：
    · ffmpeg 不存在 → (None, 0)，上传照常走（不能因为少个工具就传不了歌）
    · 任何异常都吞掉 → 保留原文件，宁可文件大也不能丢东西
    · 压完反而更大（短音频常见）→ 用原文件
    · 输出先写临时文件，确认没问题再替换，避免中途失败把原文件毁了
    """
    if not shutil.which("ffmpeg"):
        return None, 0
    base = os.path.basename(src_path).rsplit('.', 1)[0]
    tmp = os.path.join(os.path.dirname(src_path), base + ".compress_tmp.ogg")
    final = os.path.join(os.path.dirname(src_path), base + ".ogg")
    try:
        r = run_blocking(subprocess.run,     # 转码几十秒，必须走线程池
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", src_path, "-vn",
             "-c:a", "libopus", "-b:a", MUSIC_COMPRESS_BITRATE,
             "-vbr", "on", "-compression_level", "10",
             "-application", "audio",
             "-map_metadata", "0", tmp],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=MUSIC_COMPRESS_TIMEOUT)
        if r.returncode != 0 or not os.path.isfile(tmp):
            return None, 0
        new_size = os.path.getsize(tmp)
        old_size = os.path.getsize(src_path)
        if new_size <= 0 or new_size >= old_size:
            return None, 0
        os.replace(tmp, final)
        # 原文件删掉：它已经被 mp3 版本取代，留着只会占磁盘
        if os.path.abspath(final) != os.path.abspath(src_path):
            try:
                os.remove(src_path)
            except Exception:
                pass
        return os.path.basename(final), new_size
    except Exception:
        log.exception("音频压缩失败，保留原文件: %s", src_path)
        return None, 0
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


@app.route("/music/upload", methods=["POST"])
@login_required
def music_upload():
    me = current_user()
    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        if request.form.get("ajax"):
            return jsonify({"ok": False, "msg": "请选择音频文件"}), 400
        flash("请选择音频文件", "error")
        return redirect(url_for("music_page"))
    db = get_db()
    results, added = [], 0
    for file in files:
        orig = file.filename
        ext = orig.rsplit('.', 1)[-1].lower() if '.' in orig else ''
        if ext not in MUSIC_ALLOWED_EXT:
            results.append({"ok": False, "msg": "%s：格式不支持" % orig})
            continue
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if size <= 0:
            results.append({"ok": False, "msg": "%s：文件为空" % orig})
            continue
        if size > MUSIC_MAX_SIZE:
            results.append({"ok": False, "msg": "%s：超过 50MB 上限" % orig})
            continue
        title = (request.form.get("title") or "").strip()[:80] or orig.rsplit('.', 1)[0][:80]
        artist = (request.form.get("artist") or "").strip()[:40] or me['username']
        fname = _music_new_filename(orig)
        dest = os.path.join(MUSIC_FOLDER, fname)
        file.save(dest)
        final_size = size
        compressed = False
        if size > MUSIC_COMPRESS_THRESHOLD:
            # 压缩会换后缀（统一成 .ogg/Opus），所以要拿回新文件名一起入库
            new_name, new_size = _music_compress(dest)
            if new_name:
                fname = new_name
                final_size = new_size
                compressed = True
        db.execute(
            "INSERT INTO music_tracks (user_id, username, title, artist, filename, size, duration, created_at) "
            "VALUES (?,?,?,?,?,?,0,?)",
            (me['id'], me['username'], title, artist, fname, final_size, time.time()))
        db.commit()
        row = db.execute("SELECT * FROM music_tracks WHERE filename=?", (fname,)).fetchone()
        if compressed:
            msg = "《%s》已上传（已转成 Opus 并压缩 %.1fMB → %.1fMB）" % (
                title, size / 1048576.0, final_size / 1048576.0)
        else:
            msg = "《%s》已上传" % title
        results.append({"ok": True, "msg": msg, "track": dict(row) if row else None})
        added += 1
    if request.form.get("ajax"):
        return jsonify({"ok": added > 0, "added": added,
                        "msg": "成功 %d 个，失败 %d 个" % (added, len(results) - added) if len(results) > 1 else (results[0]["msg"] if results else "失败"),
                        "results": results})
    ok_n = added
    fail_n = len(results) - ok_n
    if ok_n: flash("成功上传 %d 首" % ok_n, "ok")
    if fail_n: flash("%d 个文件上传失败（格式/大小不符）" % fail_n, "error")
    return redirect(url_for("music_page"))


def _audio_mime(name):
    ext = (name or "").rsplit('.', 1)[-1].lower()
    return {'mp3': 'audio/mpeg', 'm4a': 'audio/mp4', 'aac': 'audio/aac',
            'ogg': 'audio/ogg', 'oga': 'audio/ogg', 'opus': 'audio/ogg', 'wav': 'audio/wav',
            'flac': 'audio/flac', 'webm': 'audio/webm'}.get(ext, 'application/octet-stream')


@app.route("/music/stream/<int:tid>")
@login_required
def music_stream(tid):
    row = get_db().execute("SELECT * FROM music_tracks WHERE id=?", (tid,)).fetchone()
    if not row:
        abort(404)
    path = os.path.join(MUSIC_FOLDER, row['filename'])
    if not os.path.isfile(path):
        abort(404)

    try:
        _rng = request.headers.get('Range') or ''
        if not _rng or _rng.startswith('bytes=0-'):
            _db = get_db()
            _db.execute("UPDATE music_tracks SET plays = COALESCE(plays, 0) + 1 WHERE id=?", (tid,))
            _db.commit()
    except Exception:
        pass

    # 交给 nginx 直接发文件：Python 只负责鉴权，字节传输走 nginx 的 sendfile，
    # Range / 断点续传 / 分片全由 nginx 原生处理，不再占用 Python 线程。
    # 没配内网 location 时（本地开发）自动退回 Flask 自己发。
    if os.environ.get("MONO_ACCEL_MUSIC") == "1":
        from urllib.parse import quote
        resp = Response("", status=200)
        resp.headers["X-Accel-Redirect"] = "/music-files/" + quote(row['filename'])
        resp.headers["Content-Type"] = _audio_mime(row['filename'])
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    resp = send_file(path, conditional=True,
                     download_name="%s.%s" % (row['title'], row['filename'].rsplit('.', 1)[-1]))
    resp.headers['Accept-Ranges'] = 'bytes'
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


@app.route("/music/audio/<int:tid>.<ext>")
@login_required
def music_stream_cached(tid, ext):
    """给 Cloudflare 看的入口：URL 带扩展名它才肯缓存（CF 默认只按扩展名判断）。
    后缀写什么都不影响实际发哪个文件 —— 真正读哪个文件由 music_stream 按数据库决定。
    另外查询串会参与 CF 的缓存键，换编码时改一下就能绕开旧缓存。"""
    return music_stream(tid)


@app.route("/api/music/recommend")
@login_required
def api_music_recommend():
    """推荐 5 首。

    和「最热」不同：这里带随机性，每次刷新换一批。
    前端把上一批的 id 用 `exclude=1,2,3` 传回来，保证这批不和上批重复。
    """
    exclude_raw = (request.args.get("exclude") or "").strip()[:200]
    ex = []
    for part in exclude_raw.split(','):
        part = part.strip()
        if part.isdigit():
            ex.append(int(part))

    db = get_db()
    N = 5

    if ex:
        marks = ','.join('?' * len(ex))
        rows = db.execute(
            "SELECT * FROM music_tracks WHERE id NOT IN (%s) ORDER BY RANDOM() LIMIT ?" % marks,
            tuple(ex) + (N,)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM music_tracks ORDER BY RANDOM() LIMIT ?", (N,)).fetchall()

    if len(rows) < N:
        got = {r['id'] for r in rows}
        for r in db.execute("SELECT * FROM music_tracks ORDER BY RANDOM()").fetchall():
            if len(rows) >= N:
                break
            if r['id'] not in got:
                rows.append(r)
                got.add(r['id'])

    return jsonify({"ok": True, "tracks": [dict(r) for r in rows]})


@app.route("/api/music/hot")
@login_required
def api_music_hot():
    """最热 5 首（按播放量）—— 音乐台首页只展示这些，不再把全部曲目堆上去。"""
    rows = get_db().execute(
        "SELECT * FROM music_tracks ORDER BY COALESCE(plays, 0) DESC, id DESC LIMIT 5").fetchall()
    return jsonify({"ok": True, "tracks": [dict(r) for r in rows]})


@app.route("/api/music/search")
@login_required
def api_music_search():
    """搜歌：标题 / 歌手，最多返回 50 条。"""
    q = (request.args.get("q") or "").strip()[:40]
    if not q:
        return jsonify({"ok": True, "q": "", "tracks": []})
    like = '%' + q + '%'
    rows = get_db().execute(
        "SELECT * FROM music_tracks WHERE title LIKE ? OR artist LIKE ? "
        "ORDER BY COALESCE(plays, 0) DESC, id DESC LIMIT 50",
        (like, like)).fetchall()
    return jsonify({"ok": True, "q": q, "tracks": [dict(r) for r in rows]})


@app.route("/music/delete/<int:tid>", methods=["POST"])
@login_required
def music_delete(tid):
    me = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM music_tracks WHERE id=?", (tid,)).fetchone()
    if not row:
        abort(404)
    if row['user_id'] != me['id'] and not (me['is_admin'] or me['is_super_admin']):
        return jsonify({"ok": False, "msg": "只能删除自己上传的音乐"}), 403
    try:
        os.remove(os.path.join(MUSIC_FOLDER, row['filename']))
    except Exception:
        pass
    db.execute("DELETE FROM music_playlist WHERE track_id=?", (tid,))
    db.execute("DELETE FROM music_tracks WHERE id=?", (tid,))
    db.commit()
    if request.form.get("ajax"):
        return jsonify({"ok": True, "msg": "已删除"})
    flash("已删除《%s》" % row['title'], "ok")
    return redirect(url_for("music_page"))


@app.route("/api/music/tracks")
@login_required
def api_music_tracks():
    rows = get_db().execute("SELECT * FROM music_tracks ORDER BY id DESC").fetchall()
    return jsonify({"ok": True, "tracks": [dict(r) for r in rows]})


@app.route("/api/music/playlist")
@login_required
def api_music_playlist():
    me = current_user()
    rows = get_db().execute(
        "SELECT t.*, p.position FROM music_playlist p JOIN music_tracks t ON t.id=p.track_id "
        "WHERE p.user_id=? ORDER BY p.position ASC", (me['id'],)).fetchall()
    return jsonify({"ok": True, "tracks": [dict(r) for r in rows]})


@app.route("/api/music/playlist/add", methods=["POST"])
@login_required
def api_music_playlist_add():
    me = current_user()
    try:
        tid = int(request.form.get("track_id") or 0)
    except ValueError:
        return jsonify({"ok": False, "msg": "参数错误"}), 400
    db = get_db()
    if not db.execute("SELECT 1 FROM music_tracks WHERE id=?", (tid,)).fetchone():
        return jsonify({"ok": False, "msg": "歌曲不存在"}), 404
    pos = db.execute("SELECT COALESCE(MAX(position),0)+1 AS p FROM music_playlist WHERE user_id=?", (me['id'],)).fetchone()['p']
    db.execute("INSERT OR IGNORE INTO music_playlist (user_id, track_id, position, added_at) VALUES (?,?,?,?)",
               (me['id'], tid, pos, time.time()))
    db.commit()
    return jsonify({"ok": True, "msg": "已加入我的歌单"})


@app.route("/api/music/playlist/remove", methods=["POST"])
@login_required
def api_music_playlist_remove():
    me = current_user()
    try:
        tid = int(request.form.get("track_id") or 0)
    except ValueError:
        return jsonify({"ok": False, "msg": "参数错误"}), 400
    db = get_db()
    db.execute("DELETE FROM music_playlist WHERE user_id=? AND track_id=?", (me['id'], tid))
    db.commit()
    return jsonify({"ok": True, "msg": "已从歌单移除"})


@app.route("/api/music/duration/<int:tid>", methods=["POST"])
@login_required
def api_music_duration(tid):
    try:
        d = float(request.form.get("duration") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    if d <= 0 or d > 3600:
        return jsonify({"ok": False}), 400
    db = get_db()
    db.execute("UPDATE music_tracks SET duration=? WHERE id=? AND duration=0", (d, tid))
    db.commit()
    return jsonify({"ok": True})


MAX_PLAYLISTS = 20
MAX_PL_TRACKS = 200


def _pl_owner_check(pid, me):
    """取歌单并校验归属。返回 (row, error_response)。"""
    row = get_db().execute("SELECT * FROM playlists WHERE id=?", (pid,)).fetchone()
    if not row:
        return None, (jsonify({"ok": False, "msg": "歌单不存在"}), 404)
    if row['user_id'] != me['id'] and not (me['is_admin'] or me['is_super_admin']):
        return None, (jsonify({"ok": False, "msg": "这不是你的歌单"}), 403)
    return row, None


@app.route("/api/music/playlists")
@login_required
def api_playlists():
    """我的歌单 + 公开歌单（管理员/超管的排前面）。"""
    me = current_user()
    db = get_db()
    mine = db.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM playlist_tracks t WHERE t.playlist_id=p.id) AS cnt "
        "FROM playlists p WHERE p.user_id=? ORDER BY p.id DESC", (me['id'],)).fetchall()
    public = db.execute(
        "SELECT p.*, COALESCE(u.is_super_admin,0) AS is_super, COALESCE(u.is_admin,0) AS is_admin, "
        "  (SELECT COUNT(*) FROM playlist_tracks t WHERE t.playlist_id=p.id) AS cnt "
        "FROM playlists p LEFT JOIN users u ON u.id=p.user_id "
        "WHERE p.is_public=1 AND p.user_id!=? "
        "ORDER BY is_super DESC, is_admin DESC, p.id DESC LIMIT 40", (me['id'],)).fetchall()
    return jsonify({"ok": True,
                    "mine": [dict(r) for r in mine],
                    "public": [dict(r) for r in public]})


@app.route("/music/playlist/<int:pid>")
@login_required
def playlist_page(pid):
    """歌单详情页：看曲目、播放；创建者（和管理员）可以编辑。"""
    me = current_user()
    db = get_db()
    pl = db.execute("SELECT * FROM playlists WHERE id=?", (pid,)).fetchone()
    if not pl:
        abort(404)
    is_owner = (pl['user_id'] == me['id']) or me['is_admin'] or me['is_super_admin']
    if not pl['is_public'] and not is_owner:
        abort(403)
    tracks = db.execute(
        "SELECT t.* FROM music_tracks t JOIN playlist_tracks pt ON pt.track_id=t.id "
        "WHERE pt.playlist_id=? ORDER BY pt.position, pt.id", (pid,)).fetchall()
    owner = db.execute(
        "SELECT id, username, user_no, avatar, is_admin, is_super_admin "
        "FROM users WHERE id=?", (pl['user_id'],)).fetchone()
    return render_template("playlist.html", pl=pl, tracks=tracks, owner=owner,
                           is_owner=is_owner, user=me)


@app.route("/api/music/playlist/<int:pid>/tracks")
@login_required
def api_playlist_tracks(pid):
    """歌单里的曲目（私密歌单只有主人和管理员能看）。"""
    me = current_user()
    db = get_db()
    pl = db.execute("SELECT * FROM playlists WHERE id=?", (pid,)).fetchone()
    if not pl:
        return jsonify({"ok": False, "msg": "歌单不存在"}), 404
    if not pl['is_public'] and pl['user_id'] != me['id'] and not (me['is_admin'] or me['is_super_admin']):
        return jsonify({"ok": False, "msg": "这是私密歌单"}), 403
    rows = db.execute(
        "SELECT t.* FROM music_tracks t JOIN playlist_tracks pt ON pt.track_id=t.id "
        "WHERE pt.playlist_id=? ORDER BY pt.position, pt.id", (pid,)).fetchall()
    return jsonify({"ok": True, "id": pl['id'], "name": pl['name'],
                    "is_public": pl['is_public'], "owner": pl['username'],
                    "tracks": [dict(r) for r in rows]})


@app.route("/music/playlist/create", methods=["POST"])
@login_required
def playlist_create():
    me = current_user()
    name = (request.form.get("name") or "").strip()[:30]
    if not name:
        flash("歌单名不能为空", "error")
        return redirect(url_for('music_page'))
    db = get_db()
    n = db.execute("SELECT COUNT(*) AS c FROM playlists WHERE user_id=?", (me['id'],)).fetchone()['c']
    if n >= MAX_PLAYLISTS:
        flash("最多只能建 %d 个歌单" % MAX_PLAYLISTS, "error")
        return redirect(url_for('music_page'))
    db.execute("INSERT INTO playlists (user_id, username, name, is_public, created_at) VALUES (?,?,?,?,?)",
               (me['id'], me['username'], name, 1 if request.form.get('is_public') else 0, time.time()))
    db.commit()
    _activity("创建歌单", name)
    flash("已创建歌单：%s" % name, "ok")
    return redirect(url_for('music_page'))


@app.route("/music/playlist/<int:pid>/delete", methods=["POST"])
@login_required
def playlist_delete(pid):
    me = current_user()
    row, err = _pl_owner_check(pid, me)
    if err:
        return err
    db = get_db()
    db.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
    db.execute("DELETE FROM playlists WHERE id=?", (pid,))
    db.commit()
    _activity("删除歌单", row['name'])
    flash("已删除歌单：%s" % row['name'], "ok")
    return redirect(url_for('music_page'))


@app.route("/music/playlist/<int:pid>/edit", methods=["POST"])
@login_required
def playlist_edit(pid):
    """改名 / 切换公开私密，一个接口搞定。"""
    me = current_user()
    row, err = _pl_owner_check(pid, me)
    if err:
        return err
    db = get_db()
    name = (request.form.get("name") or "").strip()[:30]
    if name:
        db.execute("UPDATE playlists SET name=? WHERE id=?", (name, pid))
    if request.form.get("toggle_public"):
        db.execute("UPDATE playlists SET is_public=? WHERE id=?",
                   (0 if row['is_public'] else 1, pid))
    db.commit()
    flash("已更新歌单", "ok")
    return redirect(url_for('music_page'))


@app.route("/music/playlist/<int:pid>/add/<int:tid>", methods=["POST"])
@login_required
def playlist_add(pid, tid):
    me = current_user()
    row, err = _pl_owner_check(pid, me)
    if err:
        return err
    db = get_db()
    if db.execute("SELECT 1 FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                  (pid, tid)).fetchone():
        flash("这首歌已经在歌单里了", "ok")
        return redirect(url_for('music_page'))
    n = db.execute("SELECT COUNT(*) AS c FROM playlist_tracks WHERE playlist_id=?",
                   (pid,)).fetchone()['c']
    if n >= MAX_PL_TRACKS:
        flash("一个歌单最多 %d 首" % MAX_PL_TRACKS, "error")
        return redirect(url_for('music_page'))
    db.execute("INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?,?,?,?)",
               (pid, tid, n, time.time()))
    db.commit()
    flash("已加入歌单：%s" % row['name'], "ok")
    return redirect(url_for('music_page'))


@app.route("/music/playlist/<int:pid>/remove/<int:tid>", methods=["POST"])
@login_required
def playlist_remove(pid, tid):
    me = current_user()
    row, err = _pl_owner_check(pid, me)
    if err:
        return err
    db = get_db()
    db.execute("DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?", (pid, tid))
    db.commit()
    flash("已移出歌单", "ok")
    return redirect(url_for('music_page'))


@app.route("/music-shell")
def music_shell_page():
    """外壳页：内嵌 iframe 承载真实页面，右下角播放器常驻，跨页面不断播。"""
    initial = request.args.get("p", "/")
    if not initial.startswith("/") or initial.startswith("//"):
        initial = "/"
    return render_template("music_shell.html", initial_path=initial)


@app.route("/api/room/<game>/<int:rid>/delete", methods=["POST"])
@login_required
def api_room_delete(game, rid):
    """删除房间：房主或超管可删。删除后大厅与房间内成员收到通知。"""
    if game not in _GAME_TABLES:
        return jsonify({"ok": False, "msg": "未知游戏类型"}), 404
    me = current_user()
    db = get_db()
    tbl = _GAME_TABLES[game]
    room = db.execute("SELECT * FROM %s WHERE id=?" % tbl, (rid,)).fetchone()
    if not room:
        return jsonify({"ok": False, "msg": "房间不存在"})
    is_owner = room['owner_id'] == me['id']
    is_super = bool(me['is_super_admin'])
    if not (is_owner or is_super):
        return jsonify({"ok": False, "msg": "只有房主或超管可删除房间"}), 403
    db.execute("DELETE FROM %s WHERE id=?" % tbl, (rid,))
    db.commit()
    evt_lobby = {'gomoku': 'gomoku_lobby_update', 'go': 'go_lobby_update',
                 'xq': 'xq_lobby_update', 'jq': 'jq_lobby_update'}[game]
    evt_room = {'gomoku': 'gmk_move', 'go': 'go_move', 'xq': 'xq_move', 'jq': 'jq_move'}[game]
    try:
        socketio.emit(evt_lobby, {}, room='%s_lobby' % game)
        socketio.emit(evt_room, {"closed": True, "msg": "房间已被删除"}, room='%s_%d' % (game, rid))
    except Exception:
        pass
    return jsonify({"ok": True, "msg": "房间已删除"})


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            if request.path.startswith('/api/'):
                return jsonify({"ok": False, "msg": "请先登录"}), 401
            return redirect(url_for("login"))
        if not user["is_admin"]:
            if request.path.startswith('/api/'):
                return jsonify({"ok": False, "msg": "需要管理员权限"}), 403
            flash("需要管理员权限", "error")
            return redirect(url_for("chat"))
        update_user_active()
        return f(*args, **kwargs)
    return wrapper

def super_admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            if request.path.startswith('/api/'):
                return jsonify({"ok": False, "msg": "请先登录"}), 401
            return redirect(url_for("login"))
        if not user["is_super_admin"]:
            if request.path.startswith('/api/'):
                return jsonify({"ok": False, "msg": "仅超级管理员可操作"}), 403
            flash("仅超级管理员可操作", "error")
            return redirect(url_for("admin"))
        update_user_active()
        return f(*args, **kwargs)
    return wrapper

def add_admin_log(admin, action, target="", detail=""):
    db = get_db()
    db.execute(
        "INSERT INTO admin_logs (admin_id, admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?,?)",
        (admin['id'], admin['username'], action, str(target), str(detail), time.time())
    )
    db.commit()

def _lift_expired(user):
    """懒检查：限时处罚到期自动解除并留解除记录（仅在访问时触发）。"""
    if not user:
        return
    db = get_db()
    for col, ptype in (("banned_until", "ban"), ("muted_until", "mute")):
        val = user[col]
        if val not in (0, PERMANENT) and val <= time.time():
            db.execute("UPDATE users SET %s = 0 WHERE id = ?" % col, (user['id'],))
            db.execute(
                "UPDATE punishment_records SET status='lifted', lifted_at=?, lift_reason='到期自动解除' "
                "WHERE user_id=? AND ptype=? AND status='effective' ORDER BY id DESC LIMIT 1",
                (time.time(), user['id'], ptype)
            )
            db.commit()


_FORTUNE_LEVELS = [
    ("大吉", "#d4a017", 6),
    ("中吉", "#2e9e5b", 16),
    ("小吉", "#5aa83c", 22),
    ("中平", "#8a8a94", 32),
    ("凶",   "#d97706", 16),
    ("大凶", "#dc2626", 8),
]

_FORTUNE_GOOD = [
    ("考试", "学的全会，蒙的全对"),
    ("刷题", "一道不会的都没有"),
    ("早起", "一天之计在于晨"),
    ("发呆", "脑子也需要休息"),
    ("和同桌聊天", "友谊就是这么来的"),
    ("偷偷玩手机", "老师今天不查"),
    ("喝奶茶", "糖分即正义"),
    ("听歌", "单曲循环一整天"),
    ("睡午觉", "下午满血复活"),
    ("去小卖部", "抢到最后一个面包"),
    ("被老师表扬", "今天你特别顺眼"),
    ("抢到最后一排", "风水宝地"),
    ("看窗外", "云很好看"),
    ("写作业", "提前写完就是自由"),
    ("举手发言", "答错了也没关系"),
    ("帮同学带饭", "人情是攒出来的"),
    ("整理错题", "错一次就够了"),
    ("早睡", "明天会感谢今晚的自己"),
    ("运动", "出一身汗真爽"),
    ("扶老奶奶过马路", "RP++"),
]

_FORTUNE_BAD = [
    ("迟到", "校门口的老师正等着你"),
    ("上课走神", "回过神来已经讲完了"),
    ("和老师对视", "下一个提问就是你"),
    ("抄作业被发现", "两个人的名字一起上黑板"),
    ("在群里发错消息", "撤回也来不及了"),
    ("忘带饭卡", "中午要靠同学了"),
    ("体育课测八百米", "腿会酸三天"),
    ("和同桌吵架", "明天还要坐一起"),
    ("考试前一晚熬夜", "第二天脑子是浆糊"),
    ("花光零花钱", "接下来一周只能看别人吃"),
    ("穿错校服", "全校只有你不一样"),
    ("把水杯打翻", "作业本要遭殃"),
    ("当着全班回答问题", "手心全是汗"),
    ("把手机带去学校", "心跳一整天"),
    ("忘记值日", "第二天会被点名"),
    ("课上打瞌睡被点名", "全班都在看你"),
    ("借东西忘还", "下次没人借你了"),
    ("深夜刷手机", "明天会后悔"),
    ("嘴硬", "明明是自己错了"),
    ("抢最优解", "越卡越慢"),
]


def _today_fortune(uid, n_good=2, n_bad=2):
    """算今天的运势。

    要求：同一个人在同一天看到的结果**固定不变**（刷新页面不会变），
    但换个人 / 换一天就不一样。所以用 uid + 日期 做哈希种子。
    """
    import hashlib
    day = datetime.now().date().isoformat()
    seed = "%s|%s|%s" % (uid, day, str(app.secret_key)[:24])
    h = hashlib.sha256(seed.encode("utf-8")).digest()

    roll = (h[0] << 8 | h[1]) % 100
    acc = 0
    level = _FORTUNE_LEVELS[-1]
    for name, color, w in _FORTUNE_LEVELS:
        acc += w
        if roll < acc:
            level = (name, color, w)
            break

    def pick(pool, offset, n):
        out, used = [], set()
        for k in range(n):
            idx = (h[offset + k * 2] << 8 | h[offset + k * 2 + 1]) % len(pool)
            for _ in range(len(pool)):
                if idx not in used:
                    break
                idx = (idx + 1) % len(pool)
            used.add(idx)
            out.append(pool[idx])
        return out

    return {
        "level": level[0],
        "color": level[1],
        "good": pick(_FORTUNE_GOOD, 4, n_good),
        "bad": pick(_FORTUNE_BAD, 12, n_bad),
        "day": day,
    }


EXAM_DATE = datetime(2026, 11, 2).date()


def _days_to_exam():
    """距离期中考试还有几天。"""
    today = datetime.now().date()
    exam = EXAM_DATE
    while exam < today:
        exam = exam.replace(year=exam.year + 1)
    return (exam - today).days


def _checkin_state(me):
    """打卡状态：今天打过没 + 连续多少天。"""
    today = datetime.now().date().isoformat()
    last = ""
    streak = 0
    try:
        last = me["last_checkin"] or ""
        streak = me["checkin_streak"] or 0
    except Exception:
        pass
    return {
        "checked": (last == today),
        "streak": streak,
        "last": last,
    }


@app.route("/api/checkin", methods=["POST"])
@login_required
def api_checkin():
    """打卡。

    规则：一天一次；昨天打过就 +1，断了就重新从 1 开始。
    """
    me = current_user()
    db = get_db()
    today = datetime.now().date()
    today_s = today.isoformat()
    yesterday_s = (today - timedelta(days=1)).isoformat()

    st = _checkin_state(me)
    if st["checked"]:
        return jsonify({"ok": True, "already": True, "streak": st["streak"]})

    streak = (st["streak"] + 1) if st["last"] == yesterday_s else 1
    db.execute("UPDATE users SET last_checkin = ?, checkin_streak = ? WHERE id = ?",
               (today_s, streak, me["id"]))
    db.commit()
    return jsonify({"ok": True, "already": False, "streak": streak})



def _home_announcements(limit=8):
    db = get_db()
    return db.execute("""
        SELECT p.id, p.title, p.username, p.created_at, p.is_pinned
        FROM posts p JOIN forum_sections s ON p.section_id = s.id
        WHERE s.is_announcement = 1 AND COALESCE(p.recycle, 0) = 0
          AND COALESCE(p.is_deleted, 0) = 0
        ORDER BY p.is_pinned DESC, p.created_at DESC LIMIT ?
    """, (limit,)).fetchall()


@app.route("/diag")
def diag_page():
    """连接诊断页。

    服务端直出，不经 Service Worker、不进任何缓存 —— 手机上白屏或
    提示「网络不可用」时，用它区分是「网络层就断了」还是「页面渲染出了问题」。

    页面上会显示：出口 IP、浏览器 UA、代理层数，以及三项服务端自检。
    下方脚本再实测一遍接口 / 静态资源 / WebSocket，带耗时。
    """
    checks = []

    try:
        t0 = time.time()
        get_db().execute("SELECT 1").fetchone()
        checks.append({"name": "数据库", "ok": True,
                       "detail": "%.0fms" % ((time.time() - t0) * 1000)})
    except Exception as e:
        checks.append({"name": "数据库", "ok": False, "detail": str(e)[:60]})

    try:
        t0 = time.time()
        sio_ok = hasattr(socketio, "server")
        checks.append({"name": "Socket.IO", "ok": True,
                       "detail": ("就绪" if sio_ok else "未初始化")
                                 + " · %.0fms" % ((time.time() - t0) * 1000)})
    except Exception as e:
        checks.append({"name": "Socket.IO", "ok": False, "detail": str(e)[:60]})

    try:
        t0 = time.time()
        p = request.path
        checks.append({"name": "路由", "ok": True,
                       "detail": "已渲染 · %.0fms" % ((time.time() - t0) * 1000)})
    except Exception as e:
        checks.append({"name": "路由", "ok": False, "detail": str(e)[:60]})

    return render_template(
        "diag.html",
        diag={
            "now": time.strftime("%Y-%m-%d %H:%M:%S"),
            "client_ip": (g._ip if hasattr(g, "_ip") else "?") or "?",
            "ua": request.headers.get("User-Agent") or "(无)",
            "scheme": request.scheme,
            "proto": request.headers.get("X-Forwarded-Proto") or "(无)",
            "host": request.headers.get("Host") or "(无)",
            "depth": os.environ.get("MONO_PROXY_DEPTH")
                     or _guard.TRUSTED_PROXY_DEPTH,
            "checks": checks,
        })


@app.route("/")
def index():
    """首页。

    未登录：只显示时间 + 登录/注册按钮。
    已登录：先显示「距期中考试 N 天」+ 打卡按钮；打卡后才展开今日运势。
    """
    me = current_user()
    if not me:
        # 2026-10-31：未登录首页改成落地页（原来只有一只时钟，陌生人不明所以）。
        # 这三条查询都走小 LIMIT，挂了就降级为空列表，不能让首页 500。
        db = get_db()
        try:
            oj_problems = db.execute("""
                SELECT id, title, difficulty FROM oj_problems
                WHERE visibility='public' AND status='approved'
                ORDER BY id ASC LIMIT 6
            """).fetchall()
        except Exception:
            oj_problems = []
        try:
            hot_posts = db.execute("""
                SELECT id, title, username, created_at FROM posts
                WHERE COALESCE(recycle,0)=0 AND COALESCE(is_deleted,0)=0
                  AND LENGTH(TRIM(title)) >= 4
                ORDER BY is_pinned DESC, created_at DESC LIMIT 5
            """).fetchall()
        except Exception:
            hot_posts = []
        # 064：未登录也能看到编辑精选（优质内容的曝光位不能只给登录用户）
        try:
            essence_posts = db.execute("""
                SELECT id, title, username FROM posts
                WHERE COALESCE(is_essence, 0) = 1
                  AND COALESCE(recycle, 0) = 0 AND COALESCE(is_deleted, 0) = 0
                ORDER BY created_at DESC LIMIT 4
            """).fetchall()
        except Exception:
            essence_posts = []
        return render_template("landing.html", me=None, oj_problems=oj_problems,
                               hot_posts=hot_posts, essence_posts=essence_posts)

    st = _checkin_state(me)
    fortune = _today_fortune(me["id"]) if st["checked"] else None
    return render_template("home.html", me=me, fortune=fortune,
                           checkin=st, exam_days=_days_to_exam(),
                           banners=adminpanel.core._home_banners(),
                           announcements=_home_announcements())


@app.route("/api/captcha/new")
def api_captcha_new():
    """发一道人机验证题。

    这里**不下发答案**。响应里只有：
      chal   挑战串
      bits   PoW 难度（要求 sha256(chal|sol) 前 N 个 bit 为 0）
      tmax   滑块目标位置 —— 这是明文的，故意给。
            位置本身不是秘密（页面上就写着「拖到 78%」），
            真正难的是轨迹和 PoW，不是这个数字。
      token  签名令牌，把这道题和当前设备绑死
    """
    fp = DFP.client_fingerprint(request)
    # 无 crypto.subtle 的旧 WebView（微信 X5 旧内核）：前端会带 nocrypto=1，
    # 发低难度题让它用 mono_pow.js 纯 JS 算 —— 否则前端只能「跳过」，
    # 而跳过 → 下面 _pow_ok 必失败 → 注册死锁（2026-10-24 修）。
    # 权衡：nocrypto 题可被脚本冒名领取，难度从 16 bits 降到 8 bits（256 次哈希），
    # 但滑块轨迹/设备绑定/注册频率限制都还在，脚本成本仍远高于人类。
    bits = DFP.NOCRYPTO_POW_BITS if request.args.get("nocrypto") == "1" else None
    ch = DFP.new_challenge(fp, bits=bits)
    target = random.randint(58, 92)
    session['cap_ctx'] = {
        'fp': fp[:24],
        'at': time.time(),
        'fails': int(session.get('cap_ctx', {}).get('fails') or 0),
    }
    return jsonify({
        "chal": ch["chal"],
        "bits": ch["bits"],
        "tmax": target,
        "tol": 7,
        "token": DFP.make_token(fp, ch),
    })


@app.route("/api/captcha/check", methods=["POST"])
def api_captcha_check():
    """单独校验验证码：拖到了没、轨迹像不像人、PoW 解对没。

    通过后往 session 里写一张凭据，注册时只查凭据不再重算 ——
    PoW 一次几十毫秒，注册时再来一遍纯属浪费，而且会让
    「校验通过」和「注册提交」之间隔着一个可重放的窗口。
    """
    d = request.get_json(silent=True) or {}
    ok, why = _captcha_verify(d)
    if ok:
        session['cap_ok'] = {'at': time.time(), 'fp': DFP.client_fingerprint(request)[:24]}
    return jsonify({"ok": bool(ok), "msg": "" if ok else why})


def _captcha_verify(d):
    """真正的人机判定，领题 → 提交之间用一次。"""
    ctx = session.get('cap_ctx') or {}
    fp = DFP.client_fingerprint(request)

    if not ctx:
        return False, '验证已失效，请刷新页面重试'
    # 领到的题立刻作废 —— 不作废的话脚本能拿一道题无限试错，
    # 拖对了位置再改轨迹重试，绕过所有判定。
    session.pop('cap_ctx', None)

    # 1) 令牌验签 + 设备绑定
    tok = DFP.read_token(d.get('token') or '')
    if not tok:
        return False, '验证令牌无效，请刷新页面重试'
    if tok.get('f') != ctx.get('fp'):
        return False, '验证与当前设备不匹配'
    if time.time() - int(tok.get('i') or 0) > DFP.TOKEN_TTL:
        return False, '验证超时，请重新拖动'

    # 2) PoW：证明客户端花了算力
    sol = str(d.get('sol') or '')
    if not DFP._pow_ok(sol, tok.get('c'), tok.get('b')):
        return False, '算力验证失败，请重试'

    # 3) 位置
    tmax = float(d.get('tmax') or 0)
    try:
        pos = float(d.get('pos'))
    except (TypeError, ValueError):
        return False, '验证数据不完整'
    if tmax <= 0:
        return False, '验证数据不完整'
    if abs(pos - tmax) > 7:
        return False, '没对准，再试一次'

    # 4) 轨迹：这是分辨「人」和「脚本」的关键
    try:
        dur = float(d.get('dur') or 0)
        raw = d.get('path')
        if isinstance(raw, str):
            raw = json.loads(raw or '[]')
        pts = [[float(p[0]), float(p[1])] for p in (raw or [])
               if isinstance(p, (list, tuple)) and len(p) == 2]
    except Exception:
        return False, '轨迹数据异常'
    tok_ok, why, detail = DFP.track_score(pts, tmax, dur)
    if not tok_ok:
        return False, '操作看起来不像真人（%s）' % why

    DFP.note_track(request, fp, True)
    # 题必须一次性作废。不作废的话，同一道题能换不同用户名反复提交 = 批量刷号。
    session.pop('cap_ctx', None)
    return True, ''


def _captcha_ok(form):
    """注册时校验人机验证。

    判定在 /api/captcha/check 就做完了，这里只查那张凭据 ——
    凭据一次性、有时效、绑设备。
    """
    ok = session.get('cap_ok') or {}
    if not ok:
        return False, '请先完成人机验证'
    if time.time() - float(ok.get('at') or 0) > 600:
        session.pop('cap_ok', None)
        return False, '验证超时，请重新拖动'
    if ok.get('fp') != DFP.client_fingerprint(request)[:24]:
        return False, '验证与当前设备不匹配'
    session.pop('cap_ok', None)
    return True, ''


@app.route("/api/check-username")
def api_check_username():
    """注册页实时校验：这个名字能不能用。
    规则与注册逻辑保持一致，别一边说能用一边又注册失败。"""
    u = (request.args.get("u") or "").strip()
    if not u:
        return jsonify({"ok": False, "msg": "请输入用户名"})
    if len(u) < 2:
        return jsonify({"ok": False, "msg": "至少 2 个字"})
    if len(u) > 16:
        return jsonify({"ok": False, "msg": "最多 16 个字"})
    if not re.fullmatch(r"[\w\u4e00-\u9fa5]+", u):
        return jsonify({"ok": False, "msg": "只能用中文、字母、数字、下划线"})
    if get_db().execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
        return jsonify({"ok": False, "msg": "这个名字已经被用了"})
    return jsonify({"ok": True, "msg": "可以用"})


@app.route("/api/onboard-done", methods=["POST"])
@login_required
def onboard_done():
    """新用户点掉引导（或完成任意一步）后调用，之后首页不再把人引到关于页。"""
    me = current_user()
    db = get_db()
    db.execute("UPDATE users SET onboarded=1 WHERE id=?", (me['id'],))
    db.commit()
    return jsonify({"ok": True})


JOIN_US_URL = "https://www.luogu.com.cn/team/122142"
TEAM_MEMBERS = [
    {
        "id": "owner",
        "name": "站长",
        "emoji": "👑",
        "role": "站长 · 全栈开发",
        "bio": "这个站点的发起人和主要维护者，后端、前端、服务器都自己扛。"
               "信奉「先跑起来，再优化」。",
        "tags": ["Python", "Flask", "运维"],
        "no_photo_text": "占位：换成你自己的介绍",
    },
    {
        "id": "dev",
        "name": "开发",
        "emoji": "💻",
        "role": "开发",
        "bio": "负责功能开发和游戏玩法设计，喜欢把想法直接写成能跑的东西。",
        "tags": ["游戏开发", "C++"],
    },
    {
        "id": "design",
        "name": "设计",
        "emoji": "🎨",
        "role": "产品体验",
        "bio": "不写代码，但负责从用户视角挑毛病，让东西不至于只有程序员觉得好用。",
        "tags": ["产品体验"],
    },
    {
        "id": "ops",
        "name": "运营",
        "emoji": "🧭",
        "role": "社区运营",
        "bio": "负责日常维护、活动组织和社区秩序。",
        "tags": ["社区运营"],
    },
]

_TEAM_PHOTO_EXTS = ("jpg", "jpeg", "png", "webp", "gif")


def _team_photo(member_id):
    """在 static/team/ 里找成员头像，返回 URL；找不到返回 None。"""
    folder = os.path.join(app.static_folder, "team")
    for ext in _TEAM_PHOTO_EXTS:
        for fn in ("%s.%s" % (member_id, ext), "%s.%s" % (member_id, ext.upper())):
            if os.path.exists(os.path.join(folder, fn)):
                return url_for("static", filename="team/" + fn)
    return None


_SCALE_TTL = 300
_scale_cache = {"at": 0.0, "data": None, "busy": False}

_TZ_CN = timezone(timedelta(hours=8))


def fmt_cn(ts, pattern="%Y-%m-%d %H:%M"):
    """时间戳 → 北京时间字符串；出问题返回空串（页面会自动少显示一行）。"""
    try:
        return datetime.fromtimestamp(float(ts), _TZ_CN).strftime(pattern)
    except Exception:
        return ""

_CODE_EXTS = {".py": "Python", ".html": "HTML", ".js": "JavaScript",
              ".css": "CSS", ".sh": "Shell", ".cmd": "Batch"}
# ⚠️ _shots / _trash_wip 必须排除：
# 它们是截图工作目录和临时目录，不是站点源码。_shots 里若留着 Chrome 的
# user-data-dir（profile/、p/），里面会塞进浏览器扩展的打包 JS ——
# 2026-10-08 实测一个腾讯文档扩展就虚报了 66,095 行 JavaScript（占 97%），
# 让「代码总行数」从 7.7 万虚涨到 14.5 万。
_SCALE_SKIP_CODE = {"__pycache__", "node_modules", ".git", ".vscode", ".idea",
                    "uploads", "music", "backups", "logs", "data_backups",
                    "_shots", "_trash_wip",
                    "katago", "stockfish", "egaroucid", "pikafish", "rapfi", "gnugo"}
# _removed_* 前缀一律排除：废弃文件收容目录不是站点源码。
# 2026-10-22 实测：线上孤儿目录 _removed_20261018/ 里一具 three.module.js
# （53044 行）让「关于我们」页代码量虚涨 5 万 +，增量显示「+53044 行」。
_SCALE_SKIP_PREFIX = ("_removed_",)
_SCALE_SKIP_SIZE = {".git", "__pycache__", "_shots", "_trash_wip"}


def _scale_skip_dir(name, base_skip):
    """目录是否跳过：命中集合，或 _removed_ 前缀。"""
    return name in base_skip or name.startswith(_SCALE_SKIP_PREFIX)


def human_size(n, mb_digits=1):
    """字节数 → 好读的字符串。mb_digits 控制 MB 档的小数位
    （代码体积只有 2 MB 出头，1 位小数看不出日常增减，要 2 位）。"""
    n = float(n or 0)
    if n < 1024:
        return "%d B" % int(n)
    if n < 1048576:
        return "%.0f KB" % (n / 1024.0)
    if n < 1073741824:
        return ("%." + str(mb_digits) + "f MB") % (n / 1048576.0)
    return "%.2f GB" % (n / 1073741824.0)


def human_int(n):
    """整数 → 带千分位。"""
    try:
        return "{:,}".format(int(n or 0))
    except (TypeError, ValueError):
        return str(n)


def _compute_scale():
    """真正干活的那个：遍历目录，统计体积、代码行数与字符数。"""
    root = app.root_path

    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _scale_skip_dir(d, _SCALE_SKIP_SIZE)]
        for fn in filenames:
            try:
                total_bytes += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass

    langs, code_files, code_bytes, code_chars = {}, 0, 0, 0
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _scale_skip_dir(d, _SCALE_SKIP_CODE)]
        for fn in filenames:
            lang = _CODE_EXTS.get(os.path.splitext(fn)[1].lower())
            if not lang:
                continue
            fp = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp)
                code_bytes += st.st_size
                if st.st_mtime > newest:
                    newest = st.st_mtime
                with open(fp, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            lines = raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)
            text = raw.decode("utf-8", errors="ignore")
            code_chars += len(text.replace("\r\n", "\n").replace("\r", "\n"))
            langs[lang] = langs.get(lang, 0) + lines
            code_files += 1

    total_lines = sum(langs.values())
    breakdown = [{"lang": k, "lines": v, "lines_str": human_int(v),
                  "pct": round(v * 100.0 / total_lines, 1) if total_lines else 0}
                 for k, v in sorted(langs.items(), key=lambda x: -x[1])]

    return {
        "code_lines": total_lines,
        "code_lines_str": human_int(total_lines),
        "code_files": code_files,
        "code_files_str": human_int(code_files),
        "code_size_str": human_size(code_bytes, 2),
        "total_size_str": human_size(total_bytes),
        "code_chars": code_chars,
        "code_chars_str": human_int(code_chars),
        "updated_str": fmt_cn(newest) if newest else "",
        "langs": breakdown,
    }


_SCALE_HIST = os.path.join(BASE_DIR, "scale_history.json")


def _scale_read_prev():
    """读上次快照；文件缺失或损坏都当作没有（不影响主流程）。"""
    try:
        with open(_SCALE_HIST, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _scale_write_prev(data):
    try:
        with open(_SCALE_HIST, "w", encoding="utf-8") as fh:
            json.dump({"lines": data.get("code_lines", 0),
                       "chars": data.get("code_chars", 0),
                       "at": time.time()}, fh)
    except Exception:
        pass


def _scale_refresh():
    """在后台线程里重算，**绝不让访问者等目录遍历**。"""
    try:
        data = _compute_scale()
        if data:
            prev = _scale_read_prev()
            if prev:
                dl = data.get("code_lines", 0) - int(prev.get("lines") or 0)
                if dl:
                    data["delta_lines"] = dl
                    data["delta_at_str"] = fmt_cn(prev.get("at") or 0, "%m-%d %H:%M")
                    _scale_write_prev(data)
            else:
                _scale_write_prev(data)
            _scale_cache["data"] = data
            _scale_cache["at"] = time.time()
    except Exception as e:
        _log_mod.event("about_scale_failed", err=str(e)[:120])
    finally:
        _scale_cache["busy"] = False


def project_scale():
    """实时统计项目规模。

    性能设计：目录遍历在服务器上要几百毫秒，所以
      · 缓存有效期内 → 直接返回
      · 已过期      → **先把旧数据返回给访客**，同时起后台线程重算
      · 进程刚启动（还没数据）→ 同步算一次，保证页面有内容
    出任何问题都返回 None，页面会自动隐藏这个板块。
    """
    now = time.time()
    data = _scale_cache["data"]

    if data is None:
        _scale_cache["busy"] = True
        _scale_refresh()
        data = _scale_cache["data"]
    elif (now - _scale_cache["at"]) > _SCALE_TTL and not _scale_cache["busy"]:
        _scale_cache["busy"] = True
        threading.Thread(target=_scale_refresh, daemon=True).start()

    if not data:
        return None
    out = dict(data)
    out["at_str"] = fmt_cn(_scale_cache["at"], "%m-%d %H:%M") if _scale_cache["at"] else ""
    return out


def _seed_changelog(db):
    """首次运行把原来写死在模板里的更新日志导入库。
    表里已有数据就跳过，所以后台改过的内容不会被覆盖。"""
    try:
        if db.execute("SELECT COUNT(*) c FROM changelog").fetchone()['c'] > 0:
            return
        from changelog_seed import CHANGELOG_SEED
    except Exception as e:
        print("[日志] 历史记录导入跳过:", e)
        return
    try:
        total = len(CHANGELOG_SEED)
        now = time.time()
        for i, (date, text) in enumerate(CHANGELOG_SEED):
            db.execute(
                "INSERT INTO changelog (date, text, is_new, sort_order, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (date, text, 1 if i == 0 else 0, total - i, now, now))
        db.commit()
        print("[日志] 已导入 %d 条历史更新记录" % total)
    except Exception as e:
        print("[日志] 历史记录导入失败:", e)


ABOUT_BLOCKS = [
    ("顶部", "hero_badge", "徽章文字", "陆柒工作室 · LIUQI STUDIO", False),
    ("顶部", "hero_title", "主标题", "我们是一群初三学生，把课余时间花在写代码上", False),
    ("顶部", "hero_lead", "导语段落", "陆柒工作室是我们几个初中生凑起来的小组，2026 年 1 月成立，到现在还是三个人。白天上课写作业，晚上和周末敲代码，寒暑假是出活最快的时候。没有指导老师，也没有经费，碰上问题就自己查、自己试，实在绕不过去就换个思路。", True),
    ("顶部", "hero_hint", "日志提示文字", "📖 从第一行代码到现在，每一步都写在更新日志里 ——", False),
    ("顶部", "hero_hint_link", "日志提示的链接文字", "有空翻翻？", False),

    ("01 我们是谁", "who_desc", "小标题下的说明", "三个人，课余时间，没有办公室。", False),
    ("01 我们是谁", "who_p1", "正文第 1 段", "最开始只是几个同学随口聊到「要不做个网站」。后来真做出来了 —— 从只能在自己电脑上打开的一个网页，到现在这台挂在外面的小服务器。中间踩过的坑，大概比写过的代码还多。", True),
    ("01 我们是谁", "who_p2", "正文第 2 段", "分工很随意，谁想到什么就做什么。服务器是自己配的，域名是自己申请的，限流和备份也是自己写的。没有现成的方案可以抄，每一步都得自己踩一遍。", True),
    ("01 我们是谁", "who_p3", "正文第 3 段", "目标谈不上宏大。能把脑子里的东西变成别人真能用上的网站，这件事本身就挺有意思；要是正好有同学也用得上，那就更好了。", True),

    ("02 我们在做什么", "doing_desc", "小标题下的说明", "下面这些都在本站，是我们一块一块写出来的。", False),
    ("02 我们在做什么", "feat1_t", "卡片 1 标题", "💬 聊天与社区", False),
    ("02 我们在做什么", "feat1_d", "卡片 1 描述", "群聊、私聊都能用，文件拖进去就发，图片视频点开就能看；论坛、动态、群组、活动也都在这里。", True),
    ("02 我们在做什么", "feat2_t", "卡片 2 标题", "♟ 棋类对弈", False),
    ("02 我们在做什么", "feat2_d", "卡片 2 描述", "中国象棋、围棋、五子棋、揭棋、黑白棋，可以约同学一起下，也可以和电脑下。", True),
    ("02 我们在做什么", "feat3_t", "卡片 3 标题", "🎮 小游戏", False),
    ("02 我们在做什么", "feat3_d", "卡片 3 描述", "24 点、数独、汉诺塔、色盲派对、仙弈破阵、象棋残局……玩法都是自己想的，代码也是一行行敲的。", True),
    ("02 我们在做什么", "feat4_t", "卡片 4 标题", "🎵 音乐台", False),
    ("02 我们在做什么", "feat4_d", "卡片 4 描述", "一个切到别的页面也不会停的播放器，可以自己传歌、排歌单，边逛站边听。", True),
    ("02 我们在做什么", "feat5_t", "卡片 5 标题", "💰 积分与装扮", False),
    ("02 我们在做什么", "feat5_d", "卡片 5 描述", "签到、做任务、逛商城，攒积分换头像特效和各种小徽章。", True),
    ("02 我们在做什么", "feat6_t", "卡片 6 标题", "🔒 自己维护", False),
    ("02 我们在做什么", "feat6_d", "卡片 6 描述", "服务器、域名、自动备份、挡住外来的恶意请求，全是我们自己摸索着搭起来的。", True),

    ("03 团队成员", "team_desc", "小标题下的说明", "就这几个人。", False),

    ("04 我们的规模", "scale_desc", "小标题下的说明", "页面上的数字是打开时现场数出来的，代码改过它就会变。", False),
    ("04 我们的规模", "scale_note", "底部说明", "代码行数只统计我们自己写的源码，不含上传的音乐图片与备份文件等；整站体积则是服务器上整个站点目录的大小。", True),

    ("加入我们", "join_title", "标题", "要不要一起来？", False),
    ("加入我们", "join_text", "正文", "欢迎喜欢折腾的同学加入。会不会写代码不重要，肯学、肯动手就行。写代码、画图、写文案、提建议，或者单纯来吐槽哪里难用，都算参与。", True),
    ("加入我们", "join_tip", "底部小字", "点击后跳转到我们的团队主页", False),
]


CHANGELOG_BLOCKS = [
    ("页头", "cl_title", "页面标题", "更新日志", False),
    ("页头", "cl_lead", "标题下的引言", "那不是我们的黑历史，那是我们的来时路。", False),
    ("页头", "cl_sub", "引言下方的小字", "希望您可以花费一些时间看看我们的历程。", False),
]


def _page_blocks(page, defs):
    """模板里的默认值打底，被超管改过的用库里的覆盖。"""
    d = {b[1]: b[3] for b in defs}
    try:
        for r in get_db().execute(
                "SELECT key, content FROM page_blocks WHERE page=?", (page,)).fetchall():
            if (r['content'] or '').strip():
                d[r['key']] = r['content']
    except Exception:
        pass
    return d


def about_blocks():
    return _page_blocks('about', ABOUT_BLOCKS)


def changelog_blocks():
    return _page_blocks('changelog', CHANGELOG_BLOCKS)


def changelog_entries():
    return get_db().execute(
        "SELECT * FROM changelog ORDER BY sort_order DESC, id DESC").fetchall()


@app.route("/achievements")
def achievements_page():
    """027 成就图鉴墙：全部成就定义 + 全站解锁数 + 我的进度（登录才显示我的）。"""
    me = current_user()
    mine = _ach.user_summary(me['id']) if me else None
    if me:
        groups = mine["groups"]
    else:
        groups = _ach.all_groups_public()
    total = sum(g["total"] for g in groups)
    return render_template("achievements.html", mine=mine, groups=groups, total=total)


def _build_records():
    """ranking 页的「各游戏纪录保持者」明细（028：缓存 5 分钟的生产者）。"""
    db = get_db()
    records = []
    for field, name in _GAME_SCORE_ROWS:
        r = db.execute("SELECT username, %s AS v FROM users WHERE %s > 0 "
                       "ORDER BY %s DESC LIMIT 1" % (field, field, field)).fetchone()
        if r:
            records.append({"game": name, "kind": "score", "who": r['username'],
                            "v": str(r['v']),
                            "url": url_for('game_rank_page', field=field)})
    for field, name in _GAME_TIME_ROWS:
        r = db.execute("SELECT username, %s AS v FROM users WHERE %s > 0 "
                       "ORDER BY %s ASC LIMIT 1" % (field, field, field)).fetchone()
        if r:
            records.append({"game": name, "kind": "time", "who": r['username'],
                            "v": _fmt_time(r['v'], field),
                            "url": url_for('game_rank_page', field=field)})
    return records


@app.route("/ranking")
def ranking_page():
    """027 全局排行榜：成就总分榜 + 各游戏当前纪录保持者。"""
    me = current_user()
    db = get_db()
    ach_rank = cache.remember("rank:ach", cache.DEFAULT_TTL,
                               lambda: _ach.ach_score_rank(20))
    ach_rows = []
    my_uid = me['id'] if me else None
    for r in ach_rank:
        d = dict(r)
        d['title'] = _ach.title_for(d['score'])
        d['me'] = (d['uid'] == my_uid)
        ach_rows.append(d)
    # 各游戏纪录保持者（缓存 5 分钟）
    records = cache.remember("rank:records", cache.DEFAULT_TTL, _build_records)
    # 积分榜（缓存 5 分钟）：当前持有积分 top 20 + 我的名次
    def _points_rank():
        rows = db.execute(
            "SELECT id, username, coins FROM users WHERE coins > 0 "
            "ORDER BY coins DESC LIMIT 20").fetchall()
        return [dict(r) for r in rows]
    points_rows = cache.remember("rank:points", cache.DEFAULT_TTL, _points_rank)
    my_points = my_rank = None
    if me:
        my_points = db.execute("SELECT coins FROM users WHERE id=?",
                               (me['id'],)).fetchone()['coins']
        my_rank = db.execute(
            "SELECT COUNT(*) c FROM users WHERE coins > ?", (my_points,)).fetchone()['c'] + 1
    return render_template("ranking.html", ach_rows=ach_rows, records=records,
                           points_rows=points_rows, my_points=my_points,
                           my_rank=my_rank)


@app.route("/tag/<path:name>")
def tag_page(name):
    """029 标签页：该标签下的帖子列表 + 关注按钮。"""
    me = current_user()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    tag, rows, total, subbed = _topics.tag_page(
        name, page=page, user_id=(me['id'] if me else None))
    if not tag:
        abort(404)
    pages = max(1, (total + 19) // 20)
    subs = get_db().execute("SELECT COUNT(*) c FROM tag_subs WHERE tag_id=?",
                            (tag['id'],)).fetchone()['c']
    return render_template("tag.html", tag=tag, rows=rows, total=total, page=page,
                           pages=pages, subbed=subbed, sub_count=subs, me=me)


@app.route("/hot")
def hot_page():
    """029 话题热榜：每日聚合近 7 天标签的帖子量与互动量。"""
    rows, day = _topics.hot_list()
    return render_template("hot.html", rows=rows, day=day)


@app.route("/thread/<int:thread_id>")
def thread_page(thread_id):
    """030 主题页：主题下帖子列表 + 订阅按钮。"""
    me = current_user()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    t, rows, total, subbed, board = _topics.thread_page(
        thread_id, page=page, user_id=(me['id'] if me else None))
    if not t:
        abort(404)
    pages = max(1, (total + 19) // 20)
    return render_template("thread.html", t=t, rows=rows, total=total, page=page,
                           pages=pages, subbed=subbed, board=board, me=me)


@app.route("/api/tag/sub/<path:name>", methods=["POST"])
@login_required
def api_tag_sub(name):
    me = current_user()
    tag = get_db().execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if not tag:
        return {"ok": False, "msg": "标签不存在"}, 404
    on = _topics.toggle_tag_sub(me['id'], tag['id'])
    return {"ok": True, "subbed": on}


@app.route("/api/thread/sub/<int:thread_id>", methods=["POST"])
@login_required
def api_thread_sub(thread_id):
    me = current_user()
    on = _topics.toggle_thread_sub(me['id'], thread_id)
    return {"ok": True, "subbed": on}


@app.route("/api/threads")
def api_threads():
    """发帖表单联动：某版块下的主题列表。"""
    try:
        board_id = int(request.args.get("board_id", "0"))
    except ValueError:
        board_id = 0
    if not board_id:
        return {"ok": False, "msg": "缺版块"}, 400
    rows = _topics.threads_for_post_select(board_id)
    return {"ok": True,
            "threads": [{"id": r['id'], "name": r['name'],
                         "is_default": bool(r['is_default'])} for r in rows]}


@app.route("/api/tags/hot")
def api_tags_hot():
    """发帖表单的热门标签提示。"""
    db = get_db()
    rows = db.execute("SELECT name, use_count FROM tags WHERE use_count > 0 "
                      "ORDER BY use_count DESC, id DESC LIMIT ?",
                      (10,)).fetchall()
    return {"ok": True,
            "tags": [{"name": r['name'], "use_count": r['use_count']} for r in rows]}


@app.route("/api/pm/search")
@login_required
def api_pm_search():
    """036 私聊全文检索：关键词 + 对方 + 时间范围。"""
    me = current_user()
    q = (request.args.get("q") or "").strip()
    if not q:
        return {"ok": False, "msg": "请输入关键词"}, 400
    try:
        peer = int(request.args.get("peer", "0") or 0)
    except ValueError:
        peer = 0
    try:
        since = float(request.args.get("since", "0") or 0) / 1000.0
        until = float(request.args.get("until", "0") or 0) / 1000.0
    except ValueError:
        since = until = 0.0
    rows = _fts.search_private(me['id'], q, peer_id=peer, since=since, until=until)
    names = {}
    for r in rows:
        for uid in (r['user_id'], r['receiver_id']):
            if uid and uid not in names:
                u = get_user_by_id(uid)
                names[uid] = u['username'] if u else str(uid)
    return {"ok": True,
            "items": [{"id": r['id'], "peer": (r['receiver_id'] if r['user_id'] == me['id']
                                               else r['user_id']),
                       "peer_name": names.get(r['receiver_id'] if r['user_id'] == me['id']
                                              else r['user_id'], ''),
                       "content": (r['content'] or '')[:200],
                       "file_name": r['file_name'],
                       "ts": int(r['created_at'] * 1000)} for r in rows]}


# ==================== 032 收藏夹（三类对象统一模型） ====================

_FAV_TYPES = ('post', 'user', 'game')
_FAV_TYPE_NAME = {'post': '帖子', 'user': '用户', 'game': '游戏'}


def _fav_toggle(uid, target_type, target_id, make_public=0):
    """收藏/取消收藏（post_id 列复用为 target_id）。返回 (favorited, row_id)。"""
    db = get_db()
    row = db.execute(
        "SELECT id FROM favorites WHERE user_id=? AND target_type=? AND post_id=?",
        (uid, target_type, target_id)).fetchone()
    if row:
        db.execute("DELETE FROM favorites WHERE id=?", (row['id'],))
        db.commit()
        return False, None
    cur = db.execute(
        "INSERT INTO favorites (user_id, post_id, created_at, target_type, is_public) "
        "VALUES (?,?,?,?,?)",
        (uid, target_id, time.time(), target_type, make_public))
    db.commit()
    return True, cur.lastrowid


def _fav_counts(target_type, target_id):
    """对象被收藏次数（用户维度的 is_public 无关）。"""
    return get_db().execute(
        "SELECT COUNT(*) c FROM favorites WHERE target_type=? AND post_id=?",
        (target_type, target_id)).fetchone()['c']


@app.route("/favorites")
@login_required
def favorites_page():
    """032 我的收藏夹：按类型分组。"""
    me = current_user()
    db = get_db()
    groups = []
    for t in _FAV_TYPES:
        if t == 'post':
            rows = db.execute(
                "SELECT f.id AS fid, f.is_public, f.created_at, p.id AS pid, p.title "
                "FROM favorites f JOIN posts p ON p.id = f.post_id "
                "WHERE f.user_id=? AND f.target_type='post' AND p.is_deleted=0 AND p.recycle=0 "
                "ORDER BY f.id DESC", (me['id'],)).fetchall()
        elif t == 'user':
            rows = db.execute(
                "SELECT f.id AS fid, f.is_public, f.created_at, u.id AS pid, u.username AS title "
                "FROM favorites f JOIN users u ON u.id = f.post_id "
                "WHERE f.user_id=? AND f.target_type='user' ORDER BY f.id DESC",
                (me['id'],)).fetchall()
        else:
            rows = db.execute(
                "SELECT id AS fid, is_public, created_at, post_id AS pid, post_id AS title "
                "FROM favorites WHERE user_id=? AND target_type='game' ORDER BY id DESC",
                (me['id'],)).fetchall()
        groups.append({"type": t, "name": _FAV_TYPE_NAME[t], "rows": rows})
    return render_template("favorites.html", groups=groups)


@app.route("/user/<int:user_id>/favorites")
def public_favorites(user_id):
    """032 公开收藏夹分享视图（只显示 is_public=1 的条目）。"""
    target = get_user_by_id(user_id)
    if not target:
        abort(404)
    db = get_db()
    posts = db.execute(
        "SELECT p.id, p.title FROM favorites f JOIN posts p ON p.id = f.post_id "
        "WHERE f.user_id=? AND f.target_type='post' AND f.is_public=1 "
        "AND p.is_deleted=0 AND p.recycle=0 ORDER BY f.id DESC", (user_id,)).fetchall()
    users = db.execute(
        "SELECT u.id, u.username AS title FROM favorites f JOIN users u ON u.id = f.post_id "
        "WHERE f.user_id=? AND f.target_type='user' AND f.is_public=1 ORDER BY f.id DESC",
        (user_id,)).fetchall()
    games = db.execute(
        "SELECT post_id AS id, post_id AS title FROM favorites "
        "WHERE user_id=? AND target_type='game' AND is_public=1 ORDER BY id DESC",
        (user_id,)).fetchall()
    return render_template("public_favorites.html", target=target, posts=posts,
                           users=users, games=games)


@app.route("/api/fav/<target_type>/<int:target_id>", methods=["POST"])
@login_required
def api_fav(target_type, target_id):
    """032 三类对象收藏 toggle。"""
    if target_type not in _FAV_TYPES:
        return {"ok": False, "msg": "不支持的收藏类型"}, 400
    me = current_user()
    if target_type == 'user':
        if target_id == me['id']:
            return {"ok": False, "msg": "不能收藏自己"}, 400
        if not get_user_by_id(target_id):
            return {"ok": False, "msg": "用户不存在"}, 404
    elif target_type == 'post':
        if not get_db().execute("SELECT 1 FROM posts WHERE id=?", (target_id,)).fetchone():
            return {"ok": False, "msg": "帖子不存在"}, 404
    make_public = 1 if request.form.get("public") or request.args.get("public") else 0
    faved, _ = _fav_toggle(me['id'], target_type, target_id, make_public)
    return {"ok": True, "favorited": faved,
            "count": _fav_counts(target_type, target_id)}


@app.route("/api/fav-visibility/<int:fav_id>", methods=["POST"])
@login_required
def api_fav_visibility(fav_id):
    """032 切换某条收藏的公开/私有。"""
    me = current_user()
    row = get_db().execute("SELECT * FROM favorites WHERE id=? AND user_id=?",
                           (fav_id, me['id'])).fetchone()
    if not row:
        return {"ok": False, "msg": "收藏不存在"}, 404
    new = 0 if row['is_public'] else 1
    get_db().execute("UPDATE favorites SET is_public=? WHERE id=?", (new, fav_id))
    get_db().commit()
    return {"ok": True, "is_public": new}


# ==================== 037 GitHub OAuth 登录 ====================

@app.route("/login/github")
def login_github():
    """发起 GitHub 授权（state 防 CSRF）。未配置密钥时优雅降级。

    state 存**最近 5 个**的待决队列：用户连点发起授权 / 开两个标签页授权时，
    回调带任一未消费 state 都放行（消费掉该个）。只存一个会被后发起的覆盖，
    GitHub 手里握着先前的 state 回来直接被弹 —— 实测连点两次必炸。
    """
    if not _gh.configured():
        flash("GitHub 登录暂未配置", "error")
        return redirect(url_for('login'))
    state = _gh.new_state()
    states = list(session.get('gh_states') or [])
    states.append(state)
    session['gh_states'] = states[-5:]
    nxt = request.args.get("next") or ""
    if nxt.startswith("/"):
        session['gh_next'] = nxt
    return redirect(_gh.authorize_url(request, state))


@app.route("/login/github/callback")
def login_github_callback():
    """GitHub 回调：state 校验 → code 换 token → 拉 profile → 登录/建号/绑定。"""
    if not _gh.configured() and not _gh.mock_mode():
        flash("GitHub 登录暂未配置", "error")
        return redirect(url_for('login'))
    s_in = request.args.get("state") or ""
    states = list(session.get('gh_states') or [])
    if s_in not in states:
        flash("授权会话已过期，请重新点一次 GitHub 登录", "error")
        return redirect(url_for('login'))
    states.remove(s_in)
    session['gh_states'] = states
    if _gh.mock_mode():
        profile = _gh.mock_profile()
    else:
        code = request.args.get("code") or ""
        token = _gh.exchange_code(code) if code else None
        if not token:
            flash("GitHub 授权失败", "error")
            return redirect(url_for('login'))
        try:
            profile = _gh.fetch_profile(token)
        except Exception:
            profile = None
        if not profile or not profile.get("id"):
            flash("获取 GitHub 用户信息失败", "error")
            return redirect(url_for('login'))
    db = get_db()
    # 绑定流程优先（已登录用户发起）
    bind_uid = session.pop("gh_bind_uid", None)
    if bind_uid:
        ok, msg = _gh.bind_user(db, bind_uid, profile)
        flash(msg, "ok" if ok else "error")
        return redirect(url_for('profile'))
    try:
        from ai_config import DEBUG_INIT_QUOTA as _diq
    except Exception:
        _diq = 0
    user, is_new = _gh.login_or_create(db, profile, gen_user_no=_gen_user_no,
                                       debug_quota=_diq)
    session.permanent = True
    session["uid"] = user["id"]
    _track(user["id"], "login", "auth", 0, weight=2)   # 044/046 埋点（GitHub 登录）
    if is_new:
        session["gh_onboard"] = profile      # 引导页取昵称/头像用
        return redirect(url_for('github_finish'))
    nxt = session.pop("gh_next", "") or url_for('index')
    return redirect(nxt)


@app.route("/login/github/finish", methods=["GET", "POST"])
@login_required
def github_finish():
    """037 首次 OAuth 登录引导页：补站内昵称（可选）+ 采用 GitHub 头像（可选）。"""
    me = current_user()
    profile = session.get("gh_onboard") or {}
    if request.method == "POST":
        db = get_db()
        new_name = (request.form.get("username") or "").strip()
        if new_name and new_name != me['username']:
            if len(new_name) > 20:
                flash("用户名不能超过 20 个字符", "error")
                return render_template("github_onboard.html", profile=profile, me=me)
            exist = get_user_by_name(new_name)
            if exist and exist['id'] != me['id']:
                flash("该用户名已被占用，换一个试试", "error")
                return render_template("github_onboard.html", profile=profile, me=me)
            db.execute("UPDATE users SET username=? WHERE id=?", (new_name, me['id']))
            db.commit()
        if request.form.get("use_avatar") and profile.get("avatar_url"):
            fn = _gh_download_avatar(me['id'], profile["avatar_url"])
            if fn:
                db.execute("UPDATE users SET avatar=? WHERE id=?", (fn, me['id']))
                db.commit()
        session.pop("gh_onboard", None)
        flash("欢迎加入 MONO 社区", "ok")
        return redirect(url_for('index'))
    return render_template("github_onboard.html", profile=profile, me=me)


@app.route("/settings/github/bind")
@login_required
def settings_github_bind():
    """已登录用户发起 GitHub 绑定。"""
    if not _gh.configured():
        flash("GitHub 登录暂未配置", "error")
        return redirect(url_for('profile'))
    session["gh_bind_uid"] = current_user()['id']
    state = _gh.new_state()
    states = list(session.get('gh_states') or [])
    states.append(state)
    session['gh_states'] = states[-5:]
    return redirect(_gh.authorize_url(request, state))


@app.route("/settings/github/unbind", methods=["POST"])
@login_required
def settings_github_unbind():
    _gh.unbind_user(get_db(), current_user()['id'])
    flash("已解绑 GitHub 账号", "ok")
    return redirect(url_for('profile'))


@app.route("/api/upload-image", methods=["POST"])
@login_required
def api_upload_image():
    """042/043：编辑器图片上传（拖拽 / 粘贴 / 选择框通用入口）。

    复用 _save_upload_image 压缩管线（压长边 + WebP + 两档缩略图）。
    返回 {"ok":true,"url":...,"name":...} —— 前端直接拼 Markdown 图片语法。
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return {"ok": False, "msg": "没有文件"}, 400
    if not is_image(f.filename):
        return {"ok": False, "msg": "只支持图片文件"}, 400
    ext = f.filename.rsplit(".", 1)[1].lower() if "." in f.filename else "png"
    fn = uuid.uuid4().hex + "." + ext
    dest = os.path.join(app.config['UPLOAD_FOLDER'], fn)
    try:
        f.save(dest)
    except Exception:
        return {"ok": False, "msg": "保存失败"}, 500
    stored, _meta = _save_upload_image(dest, fn)
    # 050：配了对象存储就同步一份，拿外网 URL（没配则本地 URL 不变）
    local_url = url_for("uploaded_file", filename=stored)
    remote = _storage.put(dest, "uploads/" + stored, content_type="image/webp")
    _track(current_user()['id'], "upload", "image", 0)   # 044/046 埋点
    return {"ok": True,
            "url": remote or local_url,
            "name": sanitize_display_name(f.filename)[:60]}


@app.route("/api/track", methods=["POST"])
def api_track():
    """046 前端埋点上报（sendBeacon/JSON）。fail-open：永远 200，绝不因埋点报错。"""
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}
    me = current_user()
    uid = me['id'] if me else 0
    evts = data.get("events") or []
    dur = data.get("dur") or 0
    ref = (data.get("ref") or "")[:200]
    path = (data.get("path") or "")[:120]
    scroll = data.get("scroll") or 0
    for e in evts[:20]:
        if not isinstance(e, dict):
            continue
        action = str(e.get("action") or "")[:30]
        if not action:
            continue
        extra = {}
        if e.get("key"):
            extra["target"] = str(e["key"])[:60]
        if e.get("msg"):
            extra["target"] = str(e["msg"])[:120]
        _track(uid, action, "web", 0, weight=1)
        if extra.get("target"):
            # 前端明细（click key / error msg）留一条 web_extra，聚合时拆
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO user_actions (user_id, action, target_type, target_id, weight, ip, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uid, action, "web:" + extra["target"][:40], 0, 1, "", time.time()))
                db.commit()
            except Exception:
                pass
    # 停留时长单独一条（看板停留分析用）
    if isinstance(dur, (int, float)) and 0 < dur < 6 * 3600:
        _track(uid, "stay", "web", int(dur), weight=1)
        try:
            db = get_db()
            db.execute(
                "INSERT INTO user_actions (user_id, action, target_type, target_id, weight, ip, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, "stay", "web:" + path, int(dur), 1, ref[:60], time.time()))
            db.commit()
        except Exception:
            pass
    return {"ok": True}


_TB_START_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} [\d:]+).*?(ERROR|Traceback|Exception)")


@app.route("/about")
def about():
    """关于我们：介绍陆柒工作室（公开页面，无需登录）"""
    members = [dict(m, photo=_team_photo(m["id"])) for m in TEAM_MEMBERS]
    return render_template("about.html", members=members, join_url=JOIN_US_URL,
                           blocks=about_blocks(), scale=project_scale(),
                           og=og_context(title="关于 MONO · 来一起折腾",
                                         desc="谁会不喜欢折腾呢？一个安静的小地方，欢迎来看看。",
                                         site_name="MONO 社区"))


DRIVE_QUOTA = 200 * 1024 * 1024
DRIVE_MAX_FILE = 50 * 1024 * 1024
DRIVE_FOLDER = os.path.join(BASE_DIR, "drive")
os.makedirs(DRIVE_FOLDER, exist_ok=True)


def _drive_user_dir(uid):
    p = os.path.join(DRIVE_FOLDER, str(uid))
    os.makedirs(p, exist_ok=True)
    return p


def _human(n):
    try:
        n = float(n or 0)
    except (ValueError, TypeError):
        return '0 B'
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return ('%.0f %s' % (n, u)) if u == 'B' else ('%.1f %s' % (n, u))
        n /= 1024.0
    return '%.1f TB' % n


@app.route("/drive")
@login_required
def drive_page():
    me = current_user()
    db = get_db()
    rows = db.execute(
        "SELECT * FROM user_files WHERE user_id=? ORDER BY id DESC", (me['id'],)).fetchall()
    used = sum((r['size'] or 0) for r in rows)
    return render_template("drive.html", files=rows, used=used,
                           quota=DRIVE_QUOTA, max_file=DRIVE_MAX_FILE)


@app.route("/drive/upload", methods=["POST"])
@login_required
def drive_upload():
    me = current_user()
    f = request.files.get('file')
    if not f or not f.filename:
        flash("请选择文件", "error")
        return redirect(url_for('drive_page'))
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size <= 0:
        flash("文件是空的", "error")
        return redirect(url_for('drive_page'))
    if size > DRIVE_MAX_FILE:
        flash("单个文件不能超过 %s" % _human(DRIVE_MAX_FILE), "error")
        return redirect(url_for('drive_page'))

    db = get_db()
    used = db.execute("SELECT COALESCE(SUM(size),0) AS s FROM user_files WHERE user_id=?",
                      (me['id'],)).fetchone()['s']
    if used + size > DRIVE_QUOTA:
        flash("空间不够了（已用 %s / %s）" % (_human(used), _human(DRIVE_QUOTA)), "error")
        return redirect(url_for('drive_page'))

    orig = sanitize_display_name(f.filename)[:120] or 'file'
    ext = os.path.splitext(orig)[1][:12]
    stored = uuid.uuid4().hex + ext
    f.save(os.path.join(_drive_user_dir(me['id']), stored))
    db.execute(
        "INSERT INTO user_files (user_id, username, filename, stored_name, size, mime, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (me['id'], me['username'], orig, stored, size,
         (f.mimetype or '')[:80], time.time()))
    db.commit()
    _activity("上传文件", '%s（%s）' % (orig, _human(size)))
    flash("已上传：%s" % orig, "ok")
    return redirect(url_for('drive_page'))


def _drive_file_or_404(fid, me=None):
    row = get_db().execute("SELECT * FROM user_files WHERE id=?", (fid,)).fetchone()
    if not row:
        abort(404)
    if me and row['user_id'] != me['id'] and not (me['is_admin'] or me['is_super_admin']):
        abort(403)
    return row


@app.route("/drive/download/<int:fid>")
@login_required
def drive_download(fid):
    me = current_user()
    row = _drive_file_or_404(fid, me)
    path = os.path.join(_drive_user_dir(row['user_id']), row['stored_name'])
    if not os.path.isfile(path):
        abort(404)
    db = get_db()
    db.execute("UPDATE user_files SET downloads = downloads + 1 WHERE id=?", (fid,))
    db.commit()
    return send_file(path, as_attachment=True, download_name=row['filename'])


@app.route("/drive/delete/<int:fid>", methods=["POST"])
@login_required
def drive_delete(fid):
    me = current_user()
    row = _drive_file_or_404(fid, me)
    try:
        os.remove(os.path.join(_drive_user_dir(row['user_id']), row['stored_name']))
    except Exception:
        pass
    db = get_db()
    db.execute("DELETE FROM user_files WHERE id=?", (fid,))
    db.commit()
    _activity("删除文件", row['filename'])
    flash("已删除：%s" % row['filename'], "ok")
    return redirect(url_for('drive_page'))


@app.route("/drive/share/<int:fid>", methods=["POST"])
@login_required
def drive_share(fid):
    """生成 / 取消分享链接。取消后旧链接立即失效。"""
    me = current_user()
    row = _drive_file_or_404(fid, me)
    db = get_db()
    if row['share_token']:
        db.execute("UPDATE user_files SET share_token=NULL, share_expire=0, share_pass='' "
                   "WHERE id=?", (fid,))
        msg = "已取消分享"
    else:
        try:
            hours = int(request.form.get("expire") or 0)
        except (TypeError, ValueError):
            hours = 0
        hours = hours if hours in (0, 1, 24, 168, 720) else 0
        pwd = (request.form.get("pass") or "").strip()[:16]
        expire = int(time.time()) + hours * 3600 if hours else 0
        db.execute("UPDATE user_files SET share_token=?, share_expire=?, share_pass=? WHERE id=?",
                   (secrets.token_urlsafe(12), expire, pwd, fid))
        msg = "已生成分享链接" + ("（%d 天后失效）" % (hours // 24) if hours >= 24 else
                                ("（1 小时后失效）" if hours else "（永久有效）"))
    db.commit()
    _activity("分享文件", row['filename'])
    flash(msg, "ok")
    return redirect(url_for('drive_page'))


@app.route("/s/<token>")
def drive_shared(token):
    """分享链接：**不需要登录**就能下载（拿到链接的人都能下）。"""
    row = get_db().execute(
        "SELECT * FROM user_files WHERE share_token=?", (token,)).fetchone()
    if not row:
        return render_template("error.html", code=404, title="链接已失效",
                               msg="这个分享链接不存在，或者已经被取消了。"), 404
    exp = row['share_expire'] if 'share_expire' in row.keys() else 0
    if exp and time.time() > exp:
        return render_template("error.html", code=410, title="链接已过期",
                               msg="这个分享链接已经过期了，找分享的人重新生成一个吧。"), 410
    pwd = (row['share_pass'] if 'share_pass' in row.keys() else '') or ''
    if pwd:
        if request.method == "POST":
            if (request.form.get("pass") or "") == pwd:
                session['share_ok_' + token] = 1
                return redirect(url_for('drive_shared', token=token))
            return render_template("share_pass.html", token=token, bad=True), 403
        if not session.get('share_ok_' + token):
            return render_template("share_pass.html", token=token, bad=False)
    path = os.path.join(_drive_user_dir(row['user_id']), row['stored_name'])
    if not os.path.isfile(path):
        return render_template("error.html", code=404, title="文件已被删除",
                               msg="分享的文件已经不在了。"), 404
    db = get_db()
    db.execute("UPDATE user_files SET downloads = downloads + 1 WHERE id=?", (row['id'],))
    db.commit()
    return send_file(path, as_attachment=True, download_name=row['filename'])


@app.route("/api/debug/stream_check/<int:tid>")
@super_admin_required
def api_debug_stream_check(tid):
    """服务端侧自检某一首：文件在不在、能不能读到、头部字节对不对。"""
    row = get_db().execute("SELECT * FROM music_tracks WHERE id=?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "曲目不存在"}), 404
    path = os.path.join(MUSIC_FOLDER, row['filename'])
    info = {"ok": True, "id": tid, "title": row['title'], "file": row['filename']}
    info['exists'] = os.path.isfile(path)
    if info['exists']:
        info['size'] = os.path.getsize(path)
        try:
            with open(path, 'rb') as fh:
                head = fh.read(16)
            info['head_hex'] = head.hex()
            info['looks_mp3'] = head[:3] == b'ID3' or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
        except Exception as e:
            info['ok'] = False
            info['read_error'] = str(e)[:120]
    else:
        info['ok'] = False
        info['msg'] = '文件在磁盘上不存在'
    return jsonify(info)


@app.route("/.well-known/assetlinks.json")
def assetlinks_json():
    """TWA（安卓 APK）的域名验证文件。

    安卓靠这个文件确认「这个网站和那个 App 是同一个主人」，
    验证通过后 App 打开时不会顶一条地址栏 —— 看起来才像原生应用。
    指纹跟着签名密钥走，换密钥要同步改这里。
    """
    data = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "cd.cc.mono_craft.twa",
            "sha256_cert_fingerprints": [
                "1E:08:A9:03:AE:F9:C3:A7:21:51:0B:64:EC:76:4D:01:D3:D0:94:EB:95:41:61:B6:25:44:EA:8F:18:7B:59:53"
            ],
        },
    }]
    return Response(json.dumps(data), mimetype='application/json')


@app.route("/changelog")
def changelog_page():
    """更新日志：从「关于我们」里拆出来的独立页。
    原来塞在关于页里，四十多条把页面撑得特别长，也没人会翻到底。
    条目存在 changelog 表里，由超管在后台维护。"""
    return render_template("changelog.html", entries=changelog_entries(),
                           blocks=changelog_blocks(),
                           og=og_context(title="更新日志 · MONO 社区",
                                         desc="那不是我们的黑历史，那是我们的来时路。",
                                         site_name="MONO 社区"))


@app.route("/favicon.ico")
def favicon_ico():
    """浏览器会无条件请求 /favicon.ico（即使页面里已声明 icon.svg），
    没有这个路由就会每次页面加载都刷一条 404。直接指向 SVG 图标即可。"""
    return redirect(url_for("static", filename="icon.svg"))

@app.route("/search")
@login_required
def search():
    """全局搜索：用户 / 帖子 / 群组 / 动态 / 聊天记录 / 群消息 / 群文件"""
    db = get_db()
    q = (request.args.get("q") or "").strip()
    results = {"users": [], "posts": [], "groups": [], "dynamics": [],
               "messages": [], "group_msgs": [], "files": []}
    if q:
        like = "%" + q + "%"
        results["users"] = db.execute(
            "SELECT id, username, avatar, signature, is_admin, user_no FROM users "
            "WHERE deactivated=0 AND (username LIKE ? OR signature LIKE ? OR badge_text LIKE ? OR user_no LIKE ?) "
            "ORDER BY (username LIKE ?) DESC, (user_no LIKE ?) DESC, id DESC LIMIT 20",
            (like, like, like, like, q, q)
        ).fetchall()
        results["posts"] = db.execute(
            "SELECT id, user_id, username, title, summary, likes, comment_count, created_at "
            "FROM posts WHERE is_deleted=0 AND recycle=0 AND (title LIKE ? OR content LIKE ? OR summary LIKE ?) "
            "ORDER BY (title LIKE ?) DESC, last_reply_at DESC, id DESC LIMIT 20",
            (like, like, like, q)
        ).fetchall()
        results["groups"] = db.execute(
            "SELECT id, name, avatar, intro, category, member_count FROM groups "
            "WHERE is_searchable=1 AND (name LIKE ? OR intro LIKE ? OR category LIKE ?) "
            "ORDER BY (name LIKE ?) DESC, member_count DESC LIMIT 20",
            (like, like, like, q)
        ).fetchall()
        results["dynamics"] = db.execute(
            "SELECT id, user_id, username, content, created_at FROM dynamics "
            "WHERE visibility='public' AND content LIKE ? ORDER BY id DESC LIMIT 20",
            (like,)
        ).fetchall()
        results["messages"] = db.execute(
            "SELECT id, username, content, room_id, created_at FROM messages "
            "WHERE is_private=0 AND is_deleted=0 AND content LIKE ? "
            "ORDER BY id DESC LIMIT 20",
            (like,)
        ).fetchall()
        me = current_user()
        if me:
            results["group_msgs"] = db.execute(
                "SELECT m.id, m.username, m.content, m.group_id, m.created_at, g.name AS group_name "
                "FROM group_messages m JOIN groups g ON g.id = m.group_id "
                "WHERE m.is_deleted=0 AND m.is_system=0 AND m.content LIKE ? "
                "  AND m.group_id IN (SELECT group_id FROM group_members WHERE user_id=?) "
                "ORDER BY m.id DESC LIMIT 20",
                (like, me['id'])
            ).fetchall()
            results["files"] = db.execute(
                "SELECT f.id, f.file_name, f.group_id, f.username, f.file_size, f.created_at, "
                "       g.name AS group_name "
                "FROM group_files f JOIN groups g ON g.id = f.group_id "
                "WHERE f.file_name LIKE ? "
                "  AND f.group_id IN (SELECT group_id FROM group_members WHERE user_id=?) "
                "ORDER BY f.id DESC LIMIT 20",
                (like, me['id'])
            ).fetchall()
    return render_template("search.html", q=q, results=results)

@app.route("/inbox")
@login_required
def inbox_page():
    return render_template("inbox.html", user=current_user(), active="inbox")


@app.route("/api/unread_count")
@login_required
def api_unread_count():
    user = current_user()
    cnt = cache.remember(
        "unread:%d" % user['id'], 30,
        lambda: get_db().execute(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id=? AND is_read=0",
            (user['id'],)
        ).fetchone()['cnt'])
    return {"count": cnt}


@app.route("/api/inbox")
@login_required
def api_inbox():
    """汇总所有未读：系统通知 / 私信 / 好友申请 / 评论回复 / 站内@提及。

    每类给一个 count 和一个跳转链接，前端据此渲染分类入口与总红点。
    """
    me = current_user()
    db = get_db()
    uid = me['id']

    def one(sql, args=()):
        try:
            return db.execute(sql, args).fetchone()['c'] or 0
        except Exception:
            return 0

    notif = one("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (uid,))
    dms = one("SELECT COUNT(*) c FROM messages WHERE receiver_id=? AND is_private=1 "
              "AND read_at=0 AND is_deleted=0", (uid,))
    reqs = one("SELECT COUNT(*) c FROM friend_requests WHERE receiver_id=? AND status='pending'",
               (uid,)) if _has_table(db, "friend_requests") else 0
    replies = one("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0 "
                  "AND type='reply'", (uid,))
    mentions = one("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0 "
                   "AND type='mention'", (uid,))

    items = [
        {"key": "notification", "name": "系统通知", "count": notif,
         "link": url_for('notifications'), "icon": "bell"},
        {"key": "dm", "name": "私信", "count": dms,
         "link": url_for('messages_page'), "icon": "chat"},
        {"key": "friend", "name": "好友申请", "count": reqs,
         "link": url_for('friends'), "icon": "user"},
        {"key": "reply", "name": "回复我的", "count": replies,
         "link": url_for('forum'), "icon": "reply"},
        {"key": "mention", "name": "@ 我", "count": mentions,
         "link": url_for('notifications'), "icon": "at"},
    ]
    total = sum(i["count"] for i in items)
    try:
        socketio.emit("inbox_update", {"total": total}, room="user_%d" % uid)
    except Exception:
        pass
    return {"total": total, "items": items}


def _has_table(db, name):
    try:
        return bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception:
        return False

@app.route("/sw.js")
def sw_js():
    from flask import send_file, make_response
    resp = make_response(send_file(os.path.join(_CODE_DIR, "static", "sw.js"),
                                   mimetype="application/javascript"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp

# ---------------- 047 通知中心：分类与偏好 ----------------

# 通知分类（047）：tab 归类 + 每类的可关闭性（系统类强制送达，不可关）
_NOTIF_CATS = {
    "all":     {"name": "全部",    "types": None, "closable": False},
    "interact": {"name": "互动",   "types": ("comment", "reply", "like", "thread",
                                              "private_message", "mention"), "closable": True},
    "social":  {"name": "社交",    "types": ("friend_request",), "closable": True},
    "honor":   {"name": "荣誉",    "types": ("achievement", "badge", "levelup"), "closable": True},
    "points":  {"name": "积分",    "types": ("points",), "closable": True},
    "system":  {"name": "系统",    "types": ("ban", "ban_lift", "mute", "mute_lift",
                                              "admin_grant", "official", "activity",
                                              "move", "feedback", "essence", "appeal_new",
                                              "appeal_result", "group_invite", "group_accept",
                                              "group_reject", "group_dissolved"), "closable": False},
}


def _notif_pref_off(user_id, notif_type):
    """用户是否关闭了该类型通知（系统类永远不关）。fail-open：查不动就当没关。"""
    for key, meta in _NOTIF_CATS.items():
        if meta["types"] and notif_type in meta["types"]:
            if not meta["closable"]:
                return False
            break
    try:
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM notif_prefs WHERE user_id=? AND cat_key=?", (user_id, key)).fetchone()
        return bool(row)
    except Exception:
        return False


@app.route("/notif-prefs", methods=["GET", "POST"])
@login_required
def notif_prefs():
    """047 通知偏好设置：可关互动/社交/荣誉/积分四类，系统类强制送达。"""
    me = current_user()
    db = get_db()
    if request.method == "POST":
        off_keys = [k for k in request.form.getlist("off") if k in _NOTIF_CATS
                    and _NOTIF_CATS[k]["closable"]]
        db.execute("DELETE FROM notif_prefs WHERE user_id=?", (me['id'],))
        for k in off_keys:
            db.execute("INSERT OR IGNORE INTO notif_prefs (user_id, cat_key, created_at) "
                       "VALUES (?,?,?)", (me['id'], k, time.time()))
        db.commit()
        flash("通知偏好已保存", "ok")
        return redirect(url_for('notif_prefs'))
    cur = {r["cat_key"] for r in db.execute(
        "SELECT cat_key FROM notif_prefs WHERE user_id=?", (me['id'],)).fetchall()}
    cats = [{"key": k, "name": v["name"], "closable": v["closable"], "off": k in cur}
            for k, v in _NOTIF_CATS.items() if k != "all"]
    return render_template("notif_prefs.html", cats=cats)


@app.route("/notifications")
@login_required
def notifications():
    user = current_user()
    db = get_db()
    cat = (request.args.get("cat") or "").strip()
    where = "user_id = ?"
    params = [user['id']]
    if cat and cat in _NOTIF_CATS and _NOTIF_CATS[cat]["types"]:
        types = _NOTIF_CATS[cat]["types"]
        where += " AND type IN (%s)" % ",".join("?" * len(types))
        params += list(types)
    notifs = db.execute(
        "SELECT * FROM notifications WHERE %s ORDER BY id DESC LIMIT 300" % where,
        params).fetchall()
    # 各分类未读数（tab 上显示）
    cat_counts = {}
    for key, meta in _NOTIF_CATS.items():
        types = meta["types"]
        if not types:
            continue
        cat_counts[key] = db.execute(
            "SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0 "
            "AND type IN (%s)" % ",".join("?" * len(types)),
            [user['id']] + list(types)).fetchone()["c"]
    total_unread = db.execute(
        "SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
        (user['id'],)).fetchone()["c"]
    return render_template("notifications.html", notifications=notifs, cat=cat,
                           cat_counts=cat_counts, total_unread=total_unread)

@app.route("/notif/read/<int:nid>", methods=["POST"])
@login_required
def notif_read(nid):
    me = current_user()
    db = get_db()
    n = db.execute("SELECT * FROM notifications WHERE id=? AND user_id=?", (nid, me['id'])).fetchone()
    if n:
        db.execute("UPDATE notifications SET is_read=1 WHERE id=?", (nid,))
        db.commit()
        if n['link_url']:
            return redirect(n['link_url'])
    return redirect(url_for('notifications'))

@app.route("/notif/delete/<int:nid>", methods=["POST"])
@login_required
def notif_delete(nid):
    me = current_user()
    db = get_db()
    db.execute("DELETE FROM notifications WHERE id=? AND user_id=?", (nid, me['id']))
    db.commit()
    return redirect(url_for('notifications'))

@app.route("/notif/delete-batch", methods=["POST"])
@login_required
def notif_delete_batch():
    """批量删除通知：前端把选中的 id 以 ids[] 提交（用 form 属性关联，不嵌套表单）。"""
    me = current_user()
    db = get_db()
    ids = []
    for v in request.form.getlist("ids"):
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    ids = ids[:500]          # 一次最多 500 条，防刷
    if ids:
        marks = ",".join("?" * len(ids))
        db.execute("DELETE FROM notifications WHERE user_id=? AND id IN (%s)" % marks,
                   tuple([me['id']] + ids))
        db.commit()
        flash("已删除 %d 条通知" % len(ids), "ok")
    return redirect(url_for("notifications"))


@app.route("/notif/delete-all", methods=["POST"])
@login_required
def notif_delete_all():
    """清空全部通知。"""
    me = current_user()
    db = get_db()
    n = db.execute("SELECT COUNT(*) AS c FROM notifications WHERE user_id=?",
                   (me['id'],)).fetchone()['c']
    db.execute("DELETE FROM notifications WHERE user_id=?", (me['id'],))
    db.commit()
    flash("已清空 %d 条通知" % n, "ok")
    return redirect(url_for("notifications"))


@app.route("/notif/read-all", methods=["POST"])
@login_required
def notif_read_all():
    me = current_user()
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0", (me['id'],))
    db.commit()
    cache.invalidate_unread(me['id'])   # 028：已读 → 未读缓存失效
    return redirect(url_for('notifications'))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        db = get_db()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        # 2026-11-01：邮箱可选。填了就必须过验证码，不填照常注册。
        email = (request.form.get("email") or "").strip().lower()
        email_code = (request.form.get("email_code") or "").strip()

        if not username or not password:
            flash("用户名和密码不能为空", "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")
        if len(username) > 20:
            flash("用户名不能超过 20 个字符", "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")
        if len(password) < 4:
            flash("密码至少 4 位", "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")
        if password != password2:
            flash("两次输入的密码不一致", "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")
        if get_user_by_name(username):
            flash("该用户名已被注册", "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")

        # 邮箱可选：填了才校验
        if email:
            if not _mail.valid_email(email):
                flash("邮箱格式不对", "error")
                return render_template("register.html", inv_open=IC.act_state() == "running")
            _ex = db.execute("SELECT id FROM users WHERE email=? AND email_verified=1",
                             (email,)).fetchone()
            if _ex:
                flash("这个邮箱已经被别的账号绑定了", "error")
                return render_template("register.html", inv_open=IC.act_state() == "running")
            _ok, _why = _mail.verify_code(db, email, "register", email_code)
            if not _ok:
                flash(_why or "验证码不对", "error")
                return render_template("register.html", inv_open=IC.act_state() == "running")

        fp_digest = DFP.mark_digest(request.form.get("fp") or "") \
            or DFP.client_fingerprint(request)[:24]
        if DFP.too_many_bad(fp_digest):
            flash("验证失败次数过多，请刷新页面重新开始", "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")
        if DFP.reg_today(fp_digest) >= 3:
            flash("这台设备今天注册得有点多，明天再来吧", "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")

        cap_ok, cap_msg = _captcha_ok(request.form)
        if not cap_ok:
            DFP.note_track(request, fp_digest, False)
            flash(cap_msg, "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")

        ip = getattr(g, "_ip", None) or _guard.client_ip(request)
        ok, why = _guard.check_register(ip)
        if not ok:
            _log_mod.event("register_block", ip=ip, user=username)
            _monitor.note_attack("register_block", ip, "/register", "注册频率超限")
            flash(why, "error")
            return render_template("register.html", inv_open=IC.act_state() == "running")

        h, salt = hash_password(password)
        db = get_db()
        cur = db.execute(
            "INSERT INTO users (username, password_hash, salt, is_admin, banned_until, muted_until, created_at, last_active, user_no, ai_debug_left, email, email_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (username, h, salt, 0, 0, 0, time.time(), time.time(), _gen_user_no(db), DEBUG_INIT_QUOTA,
             email or None, 1 if email else 0),
        )
        new_uid = int(cur.lastrowid)
        DFP.note_track(request, fp_digest, True, kind="reg")
        _track(new_uid, "register")   # 044/046 埋点
        try:
            IC.ensure_code(db, new_uid, fp_digest)
        except Exception:
            pass

        invite_code = (request.form.get("invite_code") or "").strip()
        if invite_code:
            try:
                row = IC.find_by_code(db, invite_code)
                if not row:
                    flash("邀请码无效，该邀请关系未建立", "warn")
                else:
                    ok2, why = IC.bind_invite(db, row["user_id"], new_uid,
                                              row["code"], ip, fp_digest)
                    if not ok2 and why:
                        flash(why, "warn")
            except Exception:
                pass

        db.commit()
        _log_mod.event("register", user=username, ip=ip)
        flash("注册成功，请登录", "ok")
        return redirect(url_for("login"))
    return render_template("register.html", inv_open=IC.act_state() == "running")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        ip = getattr(g, "_ip", None) or _guard.client_ip(request)

        ok, why = _guard.check_login_attempt(ip, username)
        if not ok:
            _log_mod.event("login_block", ip=ip, user=username)
            _monitor.note_attack("login_block", ip, "/login", "登录尝试被退避拦截")
            flash(why, "error")
            return render_template("login.html")

        user = get_user_by_name(username)
        # 也支持用 UID 登录（反馈 #34）：纯数字就当 UID 再查一次
        # ⚠️ 只在用户名查不到时才查 UID —— 否则会误伤那些名字本身就叫数字的老账号 ✗
        # ⚠️ 这里不能直接用 `db` —— 那个变量要到下面才赋值 ✗ 会 NameError
        # ⚠️ user_no 是**补零到 5 位**存的（00001）✗ 用户打「1」得能认 ✓
        if not user and username.isdigit():
            try:
                _dbc = get_db()
                user = _dbc.execute("SELECT * FROM users WHERE user_no=?",
                                    (username,)).fetchone()
                if not user:
                    user = _dbc.execute("SELECT * FROM users WHERE user_no=?",
                                        (username.zfill(5),)).fetchone()
            except Exception:
                user = None
        if not user or not verify_password(password, user["salt"], user["password_hash"],
                                           user["password_ext"] if 'password_ext' in user.keys() else None):
            tip = _guard.note_login_fail(ip, username)
            _log_mod.event("login_fail", user=username, ip=ip)
            _monitor.note_attack("login_fail", ip, "/login", "密码错误（疑似撞库）")
            flash(tip, "error")
            return render_template("login.html")
        if is_active_restriction(user["banned_until"]):
            _log_mod.event("login_banned", uid=user["id"], user=username)
            flash("该账号已被封禁：%s，无法登录" % status_text(user["banned_until"]), "error")
            return render_template("login.html")
        session.clear()
        session.permanent = True
        session["uid"] = user["id"]
        db = get_db()
        db.execute("UPDATE users SET last_active = ? WHERE id = ?", (time.time(), user["id"]))
        db.commit()
        _ach_after_submit(user["id"])   # 027：老玩家 retro —— 登录即补扫存量成就
        _track(user["id"], "login", "auth", 0, weight=2)   # 044/046 埋点（登录权重 2：留存信号）
        _guard.note_login_success(ip, username)
        _log_mod.event("login_ok", uid=user["id"], ip=ip)
        _activity("登录", "登录成功", user=user, ip=ip)
        flash("登录成功", "ok")
        return redirect(_login_next() or url_for("index"))
    return render_template("login.html")


@app.route("/api/mail/code", methods=["POST"])
def api_mail_code():
    """发邮箱验证码。purpose: bind / login / reset"""
    _j = request.json if request.is_json else request.form
    email = (_j.get('email') or '').strip()
    purpose = (_j.get('purpose') or 'bind').strip()
    # register：注册时可选绑邮箱，**此时还没登录**，所以不能要求 me
    if purpose not in ('bind', 'login', 'reset', 'register'):
        return jsonify({"ok": False, "msg": '用途不对'}), 400
    db = get_db()
    me = current_user()
    ip = getattr(g, '_ip', None) or _guard.client_ip(request)
    if purpose in ('bind', 'reset'):
        if not me:
            return jsonify({"ok": False, "msg": '请先登录'}), 403
    ok, why = _guard.check_mail_send(ip, email)
    if not ok:
        return jsonify({"ok": False, "msg": why}), 429
    ok, msg = _mail.send_code(db, email, purpose)
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 400
    _log_mod.event("mail_code_sent", user=me['username'] if me else '', purpose=purpose, ip=ip)
    return jsonify({"ok": True, "msg": msg})


@app.route("/api/mail/bind", methods=["POST"])
def api_mail_bind():
    """绑定邮箱（设置页调用）。"""
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": '请先登录'}), 403
    _j = request.json if request.is_json else request.form
    email = (_j.get('email') or '').strip().lower()
    code = (_j.get('code') or '').strip()
    db = get_db()
    if not _mail.valid_email(email):
        return jsonify({"ok": False, "msg": '邮箱格式不对'}), 400
    if db.execute("SELECT 1 FROM users WHERE email=? AND id<>?", (email, me['id'])).fetchone():
        return jsonify({"ok": False, "msg": '这个邮箱已被别的账号绑定'}), 409
    ok, why = _mail.verify_code(db, email, 'bind', code)
    if not ok:
        return jsonify({"ok": False, "msg": why}), 400
    db.execute("UPDATE users SET email=?, email_verified=1 WHERE id=?", (email, me['id']))
    db.commit()
    _log_mod.event("mail_bind", uid=me['id'], user=me['username'])
    return jsonify({"ok": True, "msg": '绑定成功', "email": email})


@app.route("/api/mail/login", methods=["POST"])
def api_mail_login():
    """邮箱 + 验证码登录。"""
    _j = request.json if request.is_json else request.form
    email = (_j.get('email') or '').strip().lower()
    code = (_j.get('code') or '').strip()
    db = get_db()
    if not _mail.valid_email(email):
        return jsonify({"ok": False, "msg": '邮箱格式不对'}), 400
    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        return jsonify({"ok": False, "msg": '没有账号绑定这个邮箱'}), 404
    ok, why = _mail.verify_code(db, email, 'login', code)
    if not ok:
        return jsonify({"ok": False, "msg": why}), 400
    if is_active_restriction(user["banned_until"]):
        return jsonify({"ok": False, "msg": '该账号已被封禁：%s，无法登录' % status_text(user["banned_until"])}), 403
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    db.execute("UPDATE users SET last_active=? WHERE id=?", (time.time(), user["id"]))
    db.commit()
    _ach_after_submit(user["id"])   # 027：邮箱码登录同样 retro 补扫
    _track(user["id"], "login", "auth", 0, weight=2)   # 044/046 埋点
    _activity("登录", "邮箱验证码登录", user=user, ip=_guard.client_ip(request))
    return jsonify({"ok": True, "msg": '登录成功'})


@app.route("/api/mail/reset", methods=["POST"])
def api_mail_reset():
    """邮箱验证码重置密码。"""
    _j = request.json if request.is_json else request.form
    email = (_j.get('email') or '').strip().lower()
    code = (_j.get('code') or '').strip()
    pwd = (_j.get('password') or '').strip()
    db = get_db()
    if not _mail.valid_email(email):
        return jsonify({"ok": False, "msg": '邮箱格式不对'}), 400
    if len(pwd) < 6 or len(pwd) > 64:
        return jsonify({"ok": False, "msg": '密码长度要在 6 到 64 位'}), 400
    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        return jsonify({"ok": False, "msg": '没有账号绑定这个邮箱'}), 404
    ok, why = _mail.verify_code(db, email, 'reset', code)
    if not ok:
        return jsonify({"ok": False, "msg": why}), 400
    set_user_password(db, user["id"], pwd)
    db.commit()
    _log_mod.event("mail_reset", uid=user["id"], user=user["username"])
    return jsonify({"ok": True, "msg": '密码已重置，去登录吧'})


@app.route("/logout")
def logout():
    _activity("退出登录", "主动退出")
    session.clear()
    flash("已退出登录", "ok")
    return redirect(url_for("login"))

@app.route("/upload-avatar", methods=["POST"])
@login_required
def upload_avatar():
    user = current_user()
    if 'avatar' not in request.files:
        flash("请选择图片", "error")
        return redirect(request.referrer or url_for('profile'))
    file = request.files['avatar']
    if file.filename == '':
        flash("请选择图片", "error")
        return redirect(request.referrer or url_for('profile'))
    if file and allowed_file(file.filename) and is_image(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"avatar_{user['id']}_{int(time.time())}.{ext}"
        file.save(os.path.join(AVATAR_FOLDER, filename))
        db = get_db()
        db.execute("UPDATE users SET avatar = ? WHERE id = ?", (filename, user['id']))
        db.commit()
        flash("头像更新成功", "ok")
    else:
        flash("不支持的文件格式", "error")
    return redirect(request.referrer or url_for('profile'))

@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    me = current_user()
    old_pw = request.form.get("old_password") or ""
    new_pw = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not old_pw or not new_pw or not confirm:
        flash("请填写完整密码信息", "error")
        return redirect(request.referrer or url_for('profile'))
    if not verify_password(old_pw, me['salt'], me['password_hash'],
                           _row_get(me, 'password_ext')):
        flash("当前密码不正确", "error")
        return redirect(request.referrer or url_for('profile'))
    if len(new_pw) < 4:
        flash("新密码长度至少 4 位", "error")
        return redirect(request.referrer or url_for('profile'))
    if new_pw != confirm:
        flash("两次输入的新密码不一致", "error")
        return redirect(request.referrer or url_for('profile'))
    db = get_db()
    set_user_password(db, me['id'], new_pw)
    db.commit()
    session.clear()
    flash("密码已修改，请使用新密码重新登录", "ok")
    return redirect(url_for('login'))

@app.route("/profile")
@login_required
def profile():
    user = current_user()
    # 063：创作贡献（与积分分离，只衡量内容产出）
    # 103：我的帖子表现（曝光 / 点赞 / 评论）—— 作者至少能看到自己写的东西有没有人看
    my_posts = []
    try:
        my_posts = get_db().execute(
            "SELECT p.id, p.title, COALESCE(p.view_count,0) v, COALESCE(p.likes,0) l, "
            "  COALESCE(p.is_essence,0) ess, p.created_at, "
            "  (SELECT COUNT(*) FROM post_comments c WHERE c.post_id=p.id "
            "     AND COALESCE(c.is_deleted,0)=0) cm "
            "FROM posts p WHERE p.user_id=? AND COALESCE(p.is_deleted,0)=0 "
            "  AND COALESCE(p.recycle,0)=0 ORDER BY v DESC, p.id DESC LIMIT 20",
            (user['id'],)).fetchall()
    except Exception:
        my_posts = []
    return render_template("profile.html", user=user,
                           creator=creator_stats(user['id']),
                           my_posts=my_posts)

@app.route("/settings")
@login_required
def settings():
    """统一设置页：把原先散在各处的改密码 / 签名 / 头像 / 黑名单 / 主题收进一处。
    表单仍然提交到原来那几个 POST 路由（它们都改成回 referrer 了），这里只负责聚合展示。"""
    me = current_user()
    db = get_db()
    blocked = db.execute("""
        SELECT u.id, u.username, u.avatar, b.created_at AS blocked_at
        FROM blacklist b JOIN users u ON b.blocked_id = u.id
        WHERE b.user_id = ? ORDER BY b.created_at DESC
    """, (me['id'],)).fetchall()
    return render_template("settings.html", user=me, blocked=blocked)

@app.route("/update-signature", methods=["POST"])
@login_required
def update_signature():
    me = current_user()
    raw = (request.form.get("signature") or "").strip()[:50]
    filtered, block_err = filter_and_block(raw, me, "signature")
    if block_err:
        flash(block_err, "error")
        return redirect(request.referrer or url_for("profile"))
    db = get_db()
    db.execute("UPDATE users SET signature = ? WHERE id = ?", (filtered, me['id']))
    db.commit()
    task_inc(me['id'], 'newbie_profile')
    flash("签名已更新", "ok")
    return redirect(request.referrer or url_for("profile"))

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    """服务上传文件。
    带 ?name=原始文件名 时以附件方式下载并还原原名；
    以二进制原样发送（conditional 完整响应），确保文档内容与原文件一致。"""
    display_name = request.args.get("name")
    if display_name:
        display_name = sanitize_display_name(display_name)
        real_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        if real_ext and not display_name.lower().endswith('.' + real_ext):
            display_name += '.' + real_ext
        return send_from_directory(
            app.config['UPLOAD_FOLDER'], filename,
            as_attachment=True, download_name=display_name
        )
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/friends")
@login_required
def friends():
    user = current_user()
    db = get_db()
    friends_list = db.execute("""
        SELECT u.* FROM friends f 
        JOIN users u ON (f.friend_id = u.id AND f.user_id = ?) OR (f.user_id = u.id AND f.friend_id = ?)
        WHERE f.status = 1 AND u.id != ?
    """, (user['id'], user['id'], user['id'])).fetchall()
    
    requests = db.execute("""
        SELECT u.* FROM friends f 
        JOIN users u ON f.user_id = u.id
        WHERE f.friend_id = ? AND f.status = 0
    """, (user['id'],)).fetchall()
    
    return render_template("friends.html", friends=friends_list, requests=requests)

@app.route("/add-friend/<int:user_id>", methods=["POST"])
@login_required
def add_friend(user_id):
    me = current_user()
    if me['id'] == user_id:
        flash("不能添加自己为好友", "error")
        return redirect(request.referrer or url_for('chat'))
    target = get_user_by_id(user_id)
    if not target:
        flash("用户不存在", "error")
        return redirect(request.referrer or url_for('chat'))

    if has_blocked(user_id, me['id']):
        _log_warning(me, "friend_blocked", "-> %s" % target['username'])
        flash("好友请求已发送", "ok")
        return redirect(request.referrer or url_for('friends'))
    if has_blocked(me['id'], user_id):
        flash("你已拉黑对方，请先移出黑名单", "error")
        return redirect(request.referrer or url_for('friends'))

    db = get_db()
    existing = db.execute(
        "SELECT * FROM friends WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)",
        (me['id'], user_id, user_id, me['id'])
    ).fetchone()
    
    if existing:
        if existing['status'] == 1:
            flash("你们已经是好友了", "warn")
        else:
            flash("好友请求已存在", "warn")
    else:
        db.execute(
            "INSERT INTO friends (user_id, friend_id, status, created_at) VALUES (?,?,?,?)",
            (me['id'], user_id, 0, time.time())
        )
        db.commit()
        add_notification(
            user_id,
            'friend_request',
            f'{me["username"]} 请求添加你为好友',
            url_for('friends')
        )
        flash("好友请求已发送", "ok")
    return redirect(request.referrer or url_for('friends'))

@app.route("/accept-friend/<int:user_id>", methods=["POST"])
@login_required
def accept_friend(user_id):
    me = current_user()
    db = get_db()
    db.execute(
        "UPDATE friends SET status = 1 WHERE user_id = ? AND friend_id = ?",
        (user_id, me['id'])
    )
    db.commit()
    add_notification(
        user_id,
        'friend_accept',
        f'{me["username"]} 接受了你的好友请求',
        url_for('private_chat', user_id=me['id'])
    )
    flash("已接受好友请求", "ok")
    return redirect(url_for('friends'))

@app.route("/reject-friend/<int:user_id>", methods=["POST"])
@login_required
def reject_friend(user_id):
    me = current_user()
    db = get_db()
    db.execute(
        "DELETE FROM friends WHERE user_id = ? AND friend_id = ?",
        (user_id, me['id'])
    )
    db.commit()
    flash("已拒绝好友请求", "ok")
    return redirect(url_for('friends'))

@app.route("/delete-friend/<int:user_id>", methods=["POST"])
@login_required
def delete_friend(user_id):
    """单方删除即解除双向好友关系"""
    me = current_user()
    if me['id'] == user_id:
        return redirect(url_for('friends'))
    db = get_db()
    db.execute(
        "DELETE FROM friends WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)",
        (me['id'], user_id, user_id, me['id'])
    )
    db.commit()
    flash("已删除该好友", "ok")
    return redirect(request.referrer or url_for('friends'))

@app.route("/api/friend-status")
@login_required
def api_friend_status():
    """返回当前用户所有好友的在线状态，供前端轮询实时刷新"""
    me = current_user()
    db = get_db()
    rows = db.execute("""
        SELECT u.id, u.last_active FROM friends f
        JOIN users u ON (f.friend_id = u.id AND f.user_id = ?) OR (f.user_id = u.id AND f.friend_id = ?)
        WHERE f.status = 1 AND u.id != ?
    """, (me['id'], me['id'], me['id'])).fetchall()
    now = time.time()
    result = {}
    for r in rows:
        la = r['last_active'] or 0
        result[r['id']] = (now - float(la)) < ONLINE_THRESHOLD if la else False
    return jsonify(result)

def _fmt_ms(ms):
    s = (ms or 0) / 1000.0
    if s <= 0:
        return ""
    return ("%d:%05.2f" % (int(s // 60), s % 60)) if s >= 60 else ("%.2f 秒" % s)


def _user_game_stats(u):
    keys = u.keys()

    def g(k):
        return u[k] if k in keys else 0

    scores, times = [], []
    for k, nm in _GAME_SCORE_ROWS:
        if g(k):
            scores.append({"n": nm, "v": str(g(k))})
    for k, nm in _GAME_TIME_ROWS:
        if g(k):
            times.append({"n": nm, "v": _fmt_ms(g(k))})
    ach = []
    streak = g("checkin_streak") or 0
    if streak >= 7:
        ach.append("连签 7 天")
    if streak >= 30:
        ach.append("连签 30 天")
    if (g("coins") or 0) >= 1000:
        ach.append("积分破千")
    if g("mine_best_ms_expert"):
        ach.append("通关扫雷高级")
    if g("best_time_klotski_cn_4"):
        ach.append("通关三国华容道")
    if len(scores) >= 5:
        ach.append("五款游戏上榜")
    if len(scores) + len(times) >= 10:
        ach.append("全能玩家")
    return {"scores": scores, "times": times, "ach": ach, "checkin": streak,
            "coins": g("coins") or 0, "level": g("level") or 0, "exp": g("exp") or 0}


@app.route("/user/<int:user_id>")
@login_required
def user_page(user_id):
    me = current_user()
    db = get_db()
    target = get_user_by_id(user_id)
    if not target:
        abort(404)
    if has_blocked(target['id'], me['id']):
        abort(404)
    stat = account_status(target)
    posts, comments, dynamics, likes = count_user_stats(target['id'])
    tab = request.args.get("tab", "posts")
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    perp = 10
    offset = (page - 1) * perp

    if tab == "posts":
        items = db.execute(
            "SELECT * FROM posts WHERE user_id=? AND is_deleted=0 AND recycle=0 ORDER BY id DESC LIMIT ? OFFSET ?",
            (target['id'], perp, offset)).fetchall()
        total = posts
    elif tab == "favorites":
        items = db.execute(
            "SELECT p.* FROM favorites f JOIN posts p ON p.id=f.post_id "
            "WHERE f.user_id=? AND p.is_deleted=0 AND p.recycle=0 ORDER BY f.id DESC LIMIT ? OFFSET ?",
            (target['id'], perp, offset)).fetchall()
        total = db.execute("SELECT COUNT(*) c FROM favorites WHERE user_id=?", (target['id'],)).fetchone()['c']
    elif tab == "dynamics":
        items = db.execute(
            "SELECT * FROM dynamics WHERE user_id=? AND repost_of IS NULL ORDER BY id DESC LIMIT ? OFFSET ?",
            (target['id'], perp, offset)).fetchall()
        total = dynamics
    else:
        items = db.execute(
            "SELECT c.*, p.title AS post_title, p.id AS post_id FROM post_comments c "
            "JOIN posts p ON p.id=c.post_id WHERE c.user_id=? AND c.is_deleted=0 ORDER BY c.id DESC LIMIT ? OFFSET ?",
            (target['id'], perp, offset)).fetchall()
        total = comments

    pages = max(1, (total + perp - 1) // perp)
    sections = get_db().execute("SELECT * FROM forum_sections WHERE is_hidden=0 ORDER BY sort_order ASC").fetchall()
    gstat = _user_game_stats(target)
    return render_template("user.html", target=target, stat=stat, gstat=gstat,
                          posts=posts, comments=comments, dynamics=dynamics, likes=likes,
                          tab=tab, items=items, page=page, pages=pages,
                          get_level_title=get_level_title, sections=sections,
                          register_days=register_days,
                          ach=_ach.user_summary(target['id']),
                          fav_user=(current_user() and bool(get_db().execute(
                              "SELECT 1 FROM favorites WHERE user_id=? AND target_type='user' AND post_id=?",
                              (current_user()['id'], target['id'])).fetchone())),
                          fav_count=_fav_counts('user', target['id']),
                          heatmap=_ua_heatmap(target['id'])[1],
                          heat_days=_ua_heatmap(target['id'])[0],
                          recent_actions=_ua_recent(target['id']))

@app.route("/favorite/<int:post_id>", methods=["POST"])
@login_required
def favorite_post(post_id):
    me = current_user()
    db = get_db()
    post = db.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        return jsonify({"error": "帖子不存在"}), 404
    existing = db.execute("SELECT 1 FROM favorites WHERE user_id=? AND post_id=?", (me['id'], post_id)).fetchone()
    if existing:
        db.execute("DELETE FROM favorites WHERE user_id=? AND post_id=?", (me['id'], post_id))
        fav = False
    else:
        db.execute("INSERT INTO favorites (user_id, post_id, created_at) VALUES (?,?,?)", (me['id'], post_id, time.time()))
        fav = True
        task_inc(me['id'], 'daily_fav')
        behavior_points(me['id'], 'fav')
    db.commit()
    return jsonify({"favorited": fav})

def _parse_dyn_images(raw):
    """把 dynamics.images 的 JSON 字符串安全地解析成列表。

    库里存的是字符串（形如 [ 加 "/uploads/a.jpg" 加 ] 的 JSON 文本），
    而模板里是逐项遍历 d.images —— 不解析就会**逐字符迭代字符串**，
    生成一堆 src 为单字符的垃圾 img 标签（每条动态都刷 404），
    而真正的图片反而显示不出来。
    """
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, str) and x.strip()]
    try:
        v = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str) and x.strip()]


@app.route("/dynamics")
@login_required
def dynamics_feed():
    me = current_user()
    db = get_db()
    tab = request.args.get("tab", "all")
    rows = db.execute("SELECT * FROM dynamics ORDER BY id DESC LIMIT 150").fetchall()
    items = []
    originals = {}
    for d in rows:
        vis = d['visibility']
        if vis == 'public':
            ok = True
        elif vis == 'self':
            ok = (d['user_id'] == me['id'])
        else:
            ok = (d['user_id'] == me['id']) or are_friends(me['id'], d['user_id'])
        if not ok:
            continue
        if tab == "friends" and vis == 'public' and d['user_id'] != me['id']:
            if not are_friends(me['id'], d['user_id']):
                continue
        author = get_user_by_id(d['user_id'])
        d = dict(d)
        d['author'] = author
        d['images'] = _parse_dyn_images(d.get('images'))
        d['like_count'] = db.execute("SELECT COUNT(*) c FROM dynamic_likes WHERE dynamic_id=?", (d['id'],)).fetchone()['c']
        d['comment_count'] = db.execute("SELECT COUNT(*) c FROM dynamic_comments WHERE dynamic_id=?", (d['id'],)).fetchone()['c']
        d['liked'] = bool(db.execute("SELECT 1 FROM dynamic_likes WHERE dynamic_id=? AND user_id=?", (d['id'], me['id'])).fetchone())
        if d['repost_of']:
            orig = db.execute("SELECT * FROM dynamics WHERE id=?", (d['repost_of'],)).fetchone()
            if orig:
                originals[d['id']] = dict(orig)
        items.append(d)
    return render_template("dynamics.html", items=items, originals=originals, tab=tab)

@app.route("/create-dynamic", methods=["POST"])
@login_required
def create_dynamic():
    me = current_user()
    content = (request.form.get("content") or "").strip()
    visibility = request.form.get("visibility", "public")
    images = (request.form.get("images") or "").strip()
    if visibility not in ("public", "friends", "self"):
        visibility = "public"
    if not content and not images:
        return jsonify({"error": "动态内容不能为空"}), 400
    if len(content) > 500:
        return jsonify({"error": "动态文字上限 500 字"}), 400
    if content:
        content, err = filter_and_block(content, me, "dynamic")
        if err:
            return jsonify({"error": err}), 400
    import json as _json
    img_list = []
    if images:
        try:
            img_list = [x for x in _json.loads(images) if isinstance(x, str)][:9]
        except Exception:
            img_list = []
    db = get_db()
    if not repeat_guard("create_dynamic", [content, visibility, images], uid=me['id']):
        return jsonify({"error": "这条动态刚刚已经发出去了"}), 400
    cur = db.execute(
        "INSERT INTO dynamics (user_id, username, content, images, visibility, created_at) VALUES (?,?,?,?,?,?)",
        (me['id'], me['username'], content, _json.dumps(img_list, ensure_ascii=False), visibility, time.time())
    )
    db.commit()
    add_exp(me['id'], 3)
    _activity("发动态", (content or "")[:60])
    return jsonify({"ok": True, "id": cur.lastrowid})

@app.route("/dynamic/<int:did>/like", methods=["POST"])
@login_required
def dynamic_like(did):
    me = current_user()
    db = get_db()
    d = db.execute("SELECT * FROM dynamics WHERE id=?", (did,)).fetchone()
    if not d:
        return jsonify({"error": "动态不存在"}), 404
    existing = db.execute("SELECT 1 FROM dynamic_likes WHERE dynamic_id=? AND user_id=?", (did, me['id'])).fetchone()
    if existing:
        db.execute("DELETE FROM dynamic_likes WHERE dynamic_id=? AND user_id=?", (did, me['id']))
        liked = False
    else:
        db.execute("INSERT INTO dynamic_likes (dynamic_id, user_id, created_at) VALUES (?,?,?)", (did, me['id'], time.time()))
        liked = True
        if d['user_id'] != me['id']:
            add_notification(d['user_id'], 'like', f'{me["username"]} 赞了你的动态', url_for('dynamics_feed'))
            add_exp(d['user_id'], 1)
    db.commit()
    cnt = db.execute("SELECT COUNT(*) c FROM dynamic_likes WHERE dynamic_id=?", (did,)).fetchone()['c']
    return jsonify({"liked": liked, "count": cnt})

@app.route("/dynamic/<int:did>/comment", methods=["POST"])
@login_required
def dynamic_comment(did):
    me = current_user()
    content = (request.form.get("content") or "").strip()
    if not content:
        return jsonify({"error": "评论不能为空"}), 400
    if len(content) > 500:
        return jsonify({"error": "评论上限 500 字"}), 400
    content, err = filter_and_block(content, me, "dynamic_comment")
    if err:
        return jsonify({"error": err}), 400
    db = get_db()
    d = db.execute("SELECT * FROM dynamics WHERE id=?", (did,)).fetchone()
    if not d:
        return jsonify({"error": "动态不存在"}), 404
    if not repeat_guard("dynamic_comment", [did, content], uid=me['id']):
        return jsonify({"error": "这条评论刚刚已经发出去了"}), 400
    db.execute("INSERT INTO dynamic_comments (dynamic_id, user_id, username, content, created_at) VALUES (?,?,?,?,?)",
               (did, me['id'], me['username'], content, time.time()))
    db.commit()
    if d['user_id'] != me['id']:
        add_notification(d['user_id'], 'comment', f'{me["username"]} 评论了你的动态', url_for('dynamics_feed'))
    return jsonify({"ok": True})

@app.route("/dynamic/<int:did>/repost", methods=["POST"])
@login_required
def dynamic_repost(did):
    me = current_user()
    db = get_db()
    d = db.execute("SELECT * FROM dynamics WHERE id=?", (did,)).fetchone()
    if not d:
        return jsonify({"error": "动态不存在"}), 404
    db.execute("INSERT INTO dynamics (user_id, username, content, images, visibility, repost_of, created_at) VALUES (?,?,?,?,?,?,?)",
               (me['id'], me['username'], '', '', 'self', did, time.time()))
    db.commit()
    add_exp(me['id'], 1)
    return jsonify({"ok": True})

@app.route("/dynamic/<int:did>/delete", methods=["POST"])
@login_required
def dynamic_delete(did):
    me = current_user()
    db = get_db()
    d = db.execute("SELECT * FROM dynamics WHERE id=?", (did,)).fetchone()
    if not d:
        return jsonify({"error": "动态不存在"}), 404
    if d['user_id'] != me['id'] and not me['is_admin']:
        return jsonify({"error": "无权限"}), 403
    db.execute("DELETE FROM dynamics WHERE id=?", (did,))
    db.execute("DELETE FROM dynamic_likes WHERE dynamic_id=?", (did,))
    db.execute("DELETE FROM dynamic_comments WHERE dynamic_id=?", (did,))
    db.commit()
    return jsonify({"ok": True})

@app.route("/poll/<int:poll_id>/vote", methods=["POST"])
@login_required
def poll_vote(poll_id):
    me = current_user()
    db = get_db()
    poll = db.execute("SELECT * FROM polls WHERE id=?", (poll_id,)).fetchone()
    if not poll:
        return jsonify({"error": "投票不存在"}), 404
    if poll['closed'] or (poll['deadline'] and poll['deadline'] > 0 and time.time() > poll['deadline']):
        return jsonify({"error": "投票已关闭"}), 400
    if db.execute("SELECT 1 FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, me['id'])).fetchone():
        return jsonify({"error": "你已经投过票了"}), 400
    opts = [int(x) for x in request.form.getlist("options") if x.isdigit()]
    if not opts:
        return jsonify({"error": "请选择选项"}), 400
    if poll['mode'] == 'single' and len(opts) > 1:
        return jsonify({"error": "该投票为单选"}), 400
    valid = {v['id'] for v in db.execute("SELECT id FROM poll_options WHERE poll_id=?", (poll_id,)).fetchall()}
    if not all(o in valid for o in opts):
        return jsonify({"error": "非法选项"}), 400
    now = time.time()
    for o in opts:
        db.execute("INSERT INTO poll_votes (poll_id, option_id, user_id, created_at) VALUES (?,?,?,?)",
                   (poll_id, o, me['id'], now))
        db.execute("UPDATE poll_options SET votes = votes + 1 WHERE id=?", (o,))
    db.commit()
    return jsonify({"ok": True})

@app.route("/poll/<int:poll_id>/close", methods=["POST"])
@login_required
def poll_close(poll_id):
    me = current_user()
    if not me['is_admin']:
        return jsonify({"error": "无权限"}), 403
    db = get_db()
    db.execute("UPDATE polls SET closed=1 WHERE id=?", (poll_id,))
    db.commit()
    return jsonify({"ok": True})

def get_group(gid):
    return get_db().execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()

def get_group_by_no(no):
    return get_db().execute("SELECT * FROM groups WHERE group_no=?", (no,)).fetchone()

def _generate_group_no():
    """生成唯一 8 位群号"""
    db = get_db()
    import random
    for _ in range(20):
        no = str(random.randint(10000000, 99999999))
        if not db.execute("SELECT 1 FROM groups WHERE group_no=?", (no,)).fetchone():
            return no
    return None

def group_membership(gid, uid):
    return get_db().execute("SELECT * FROM group_members WHERE group_id=? AND user_id=?", (gid, uid)).fetchone()

def group_role(gid, uid):
    m = group_membership(gid, uid)
    return m['role'] if m else None

def can_manage_group(gid, uid):
    """超管视为拥有等同群主权限（隐形成员，不写入 group_members）。"""
    u = get_user_by_id(uid)
    if u and u['is_super_admin']:
        return True
    r = group_role(gid, uid)
    return r in ('owner', 'admin')

def is_super_in_group(gid, uid):
    """超管是否隐身进入该群组（不需要在群成员列表里）。"""
    u = get_user_by_id(uid)
    return bool(u and u['is_super_admin'])

@app.route("/groups")
@login_required
def groups():
    me = current_user()
    db = get_db()
    q = (request.args.get("q") or "").strip()
    my = db.execute(
        "SELECT g.* FROM groups g JOIN group_members m ON m.group_id=g.id "
        "WHERE m.user_id=? ORDER BY m.joined_at DESC", (me['id'],)).fetchall()
    if q:
        disc = db.execute(
            "SELECT * FROM groups WHERE is_searchable=1 AND join_mode IN ('public','audit') "
            "AND (name LIKE ? OR group_no LIKE ?) AND id NOT IN "
            "(SELECT group_id FROM group_members WHERE user_id=?) "
            "ORDER BY member_count DESC, id DESC LIMIT 30",
            (f"%{q}%", f"%{q}%", me['id'])).fetchall()
    else:
        disc = db.execute(
            "SELECT * FROM groups WHERE is_searchable=1 AND join_mode IN ('public','audit') AND id NOT IN "
            "(SELECT group_id FROM group_members WHERE user_id=?) "
            "ORDER BY member_count DESC, id DESC LIMIT 30", (me['id'],)).fetchall()
    return render_template("groups.html", my=my, disc=disc, can_create=can_create_group(me), q=q)

@app.route("/create-group", methods=["GET", "POST"])
@login_required
def create_group():
    me = current_user()
    db = get_db()
    if request.method == "POST":
        if not can_create_group(me):
            flash("登录后方可创建群组；最多创建 3 个、加入 20 个", "error")
            return redirect(url_for('groups'))
        created = db.execute("SELECT COUNT(*) c FROM groups WHERE owner_id=?", (me['id'],)).fetchone()['c']
        joined = db.execute("SELECT COUNT(*) c FROM group_members WHERE user_id=?", (me['id'],)).fetchone()['c']
        if created >= 3:
            flash("你已创建 3 个群组（上限）", "error"); return redirect(url_for('groups'))
        if joined >= 20:
            flash("你已加入 20 个群组（上限）", "error"); return redirect(url_for('groups'))
        name = (request.form.get("name") or "").strip()
        if not name or len(name) > 30:
            flash("群名称需 1-30 字", "error"); return redirect(url_for('groups'))
        intro = (request.form.get("intro") or "").strip()[:200]
        category = request.form.get("category", "闲聊群")
        if category not in ("技术群", "游戏群", "闲聊群"):
            category = "闲聊群"
        jm = request.form.get("join_mode", "public")
        if jm not in ("public", "audit", "private"):
            jm = "public"
        is_searchable = 1 if request.form.get("is_searchable") else 0
        group_no = _generate_group_no()
        if not group_no:
            flash("群号生成失败，请重试", "error"); return redirect(url_for('groups'))
        if not repeat_guard("create_group", [name, category, jm, intro], window=8.0, uid=me['id']):
            flash("这个群刚刚已经建好了，没有重复创建", "ok")
            return redirect(url_for('groups'))
        cur = db.execute(
            "INSERT INTO groups (name, owner_id, join_mode, category, intro, group_no, is_searchable, member_count, created_at) "
            "VALUES (?,?,?,?,?,?,?,1,?)", (name, me['id'], jm, category, intro, group_no, is_searchable, time.time()))
        gid = cur.lastrowid
        db.execute("INSERT INTO group_members (group_id, user_id, role, joined_at) VALUES (?,?,?,?)",
                   (gid, me['id'], 'owner', time.time()))
        db.commit()
        flash("群组创建成功", "ok")
        return redirect(url_for('group_page', gid=gid))
    return render_template("create_group.html", can_create=can_create_group(me))

@app.route("/group/<int:gid>")
@login_required
def group_page(gid):
    me = current_user()
    db = get_db()
    g = get_group(gid)
    if not g:
        abort(404)
    role = group_role(gid, me['id'])
    is_super = bool(me['is_super_admin'])
    if is_super and not role:
        role = 'owner'
    if not role:
        return render_template("group.html", g=g, is_member=False, role=None,
                           members=[], messages=[], requests=[], online=set(),
                           files=[], essence=[], announcements=[], can_manage=False,
                           read_ids=set(), essence_ids=set(), recallable_ids=set(),
                           unread_announcements=0)
    banned = (role == 'black')
    members = db.execute(
        "SELECT m.*, u.username, u.avatar, u.level FROM group_members m "
        "JOIN users u ON u.id=m.user_id WHERE m.group_id=? ORDER BY "
        "CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, m.joined_at ASC", (gid,)).fetchall()
    messages = db.execute(
        "SELECT * FROM group_messages WHERE group_id=? AND is_system=0 ORDER BY id DESC LIMIT 50", (gid,)).fetchall()[::-1]
    requests = []
    if (role in ('owner', 'admin') or is_super) and g['join_mode'] == 'audit':
        requests = db.execute(
            "SELECT r.*, u.username, u.avatar FROM group_join_requests r "
            "JOIN users u ON u.id=r.user_id WHERE r.group_id=? AND r.status='pending' AND (r.expired_at=0 OR r.expired_at>?)",
            (gid, time.time())).fetchall()
    files = db.execute(
        "SELECT f.*, u.avatar FROM group_files f JOIN users u ON u.id=f.user_id WHERE f.group_id=? ORDER BY f.created_at DESC LIMIT 50",
        (gid,)).fetchall()
    essence = db.execute(
        "SELECT e.*, m.content, m.file_url, m.file_name, m.created_at as msg_at, u.username, u.avatar "
        "FROM group_essence e JOIN group_messages m ON m.id=e.message_id "
        "JOIN users u ON u.id=m.user_id WHERE e.group_id=? ORDER BY e.created_at DESC",
        (gid,)).fetchall()
    announcements = db.execute(
        "SELECT a.*, u.username FROM group_announcements a JOIN users u ON u.id=a.user_id WHERE a.group_id=? ORDER BY a.created_at DESC",
        (gid,)).fetchall()
    read_ids = set()
    unread_announcements = 0
    for a in announcements:
        read = db.execute("SELECT 1 FROM group_announcement_reads WHERE announcement_id=? AND user_id=?",
                         (a['id'], me['id'])).fetchone()
        if read:
            read_ids.add(a['id'])
        else:
            unread_announcements += 1
    if role != 'black' and announcements and announcements[0]['id'] not in read_ids:
        a0 = announcements[0]
        db.execute("INSERT OR IGNORE INTO group_announcement_reads (announcement_id, user_id, read_at) VALUES (?,?,?)",
                   (a0['id'], me['id'], time.time()))
        db.execute("UPDATE group_announcements SET read_count = (SELECT COUNT(*) FROM group_announcement_reads WHERE announcement_id=?) WHERE id=?",
                   (a0['id'], a0['id']))
        db.commit()
        read_ids.add(a0['id'])
        unread_announcements = max(0, unread_announcements - 1)
    essence_ids = {e['message_id'] for e in essence}
    now = time.time()
    recallable_ids = set()
    can_manage = can_manage_group(gid, me['id'])
    for m in messages:
        if m['is_system'] or m['is_deleted']:
            continue
        if can_manage or (m['user_id'] == me['id'] and (now - float(m['created_at'])) <= 120):
            recallable_ids.add(m['id'])
    online = {int(r['user_id']) for r in db.execute(
        "SELECT DISTINCT user_id FROM group_messages WHERE group_id=?", (gid,)).fetchall()}
    return render_template("group.html", g=g, is_member=True, role=role, banned=banned,
                          members=members, messages=messages, requests=requests,
                          online=online, can_manage=can_manage,
                          files=files, essence=essence, announcements=announcements,
                          read_ids=read_ids, essence_ids=essence_ids, recallable_ids=recallable_ids,
                          unread_announcements=unread_announcements,
                          is_super=is_super)

@app.route("/group/<int:gid>/join", methods=["POST"])
@login_required
def group_join(gid):
    me = current_user()
    db = get_db()
    g = get_group(gid)
    def _resp(err=None, ok_msg=None, redir=None):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            if err: return jsonify({"error": err}), 400
            return jsonify({"ok": True})
        if err: flash(err, "error")
        if ok_msg: flash(ok_msg, "success")
        return redirect(redir or url_for("groups"))
    if not g:
        return _resp("群组不存在")
    existing = group_membership(gid, me['id'])
    if existing:
        if existing['role'] == 'black':
            return _resp("你已被本群拉黑，无法加入")
        return _resp("你已在群内")
    if g['join_mode'] == 'private':
        return _resp("该群为私密群，仅限群内邀请加入")
    if g['join_mode'] == 'audit':
        msg = (request.form.get("message") or "").strip()[:200]
        db.execute("DELETE FROM group_join_requests WHERE group_id=? AND user_id=? AND status='pending' AND expired_at>? AND expired_at<?",
                   (gid, me['id'], 0, time.time()))
        db.execute("INSERT OR IGNORE INTO group_join_requests (group_id, user_id, username, status, message, created_at, expired_at) "
                   "VALUES (?,?,?,?,?,?,?)", (gid, me['id'], me['username'], 'pending', msg, time.time(), time.time()+7*86400))
        db.commit()
        add_notification(g['owner_id'], 'group_request',
                        f'{me["username"]} 申请加入群组「{g["name"]}」', url_for('group_page', gid=gid))
        return _resp(None, "入群申请已提交，等待管理员审核（7 天内有效）")
    _group_add_member(gid, me['id'], 'member')
    _group_sys_msg(gid, f'欢迎 {me["username"]} 加入群组')
    _show_latest_announcement(gid, me['id'])
    task_inc(me['id'], 'newbie_join_group')
    return _resp(None, "已成功加入群组", url_for("group_page", gid=gid))

@app.route("/group/<int:gid>/request/<int:rid>/<string:action>", methods=["POST"])
@login_required
def group_request_handle(gid, rid, action):
    me = current_user()
    def _resp(err=None, ok_msg=None):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            if err: return jsonify({"error": err}), 400
            return jsonify({"ok": True})
        if err: flash(err, "error")
        if ok_msg: flash(ok_msg, "success")
        return redirect(url_for("group_page", gid=gid))
    if not can_manage_group(gid, me['id']):
        return _resp("无权限")
    db = get_db()
    req = db.execute("SELECT * FROM group_join_requests WHERE id=? AND group_id=?", (rid, gid)).fetchone()
    if not req:
        return _resp("请求不存在")
    if req['status'] != 'pending':
        return _resp("该申请已处理")
    if req['expired_at'] and req['expired_at'] > 0 and req['expired_at'] < time.time():
        return _resp("申请已过期")
    grp = get_group(gid)
    if not grp:
        return _resp("群组不存在")
    gname = grp["name"]
    if action == 'approve':
        db.execute("UPDATE group_join_requests SET status='approved' WHERE id=?", (rid,))
        existing = group_membership(gid, req['user_id'])
        if not existing:
            _group_add_member(gid, req['user_id'], 'member')
            _group_sys_msg(gid, f'欢迎 {req["username"]} 加入群组')
            _show_latest_announcement(gid, req['user_id'])
            task_inc(req['user_id'], 'newbie_join_group')
            add_notification(req['user_id'], 'group_accept',
                            f'你已通过「{gname}」的入群审核', url_for('group_page', gid=gid))
    elif action == 'reject':
        reason = (request.form.get("reason") or "").strip()[:200]
        db.execute("UPDATE group_join_requests SET status='rejected', reject_reason=? WHERE id=?", (reason, rid))
        msg = f'你加入「{gname}」的申请被拒绝'
        if reason: msg += f'，理由：{reason}'
        add_notification(req['user_id'], 'group_reject', msg, url_for('groups'))
    else:
        return _resp("未知操作")
    db.commit()
    return _resp(None, "已处理入群申请")

@app.route("/group/invite/<int:user_id>", methods=["GET", "POST"])
@login_required
def group_invite(user_id):
    me = current_user()
    db = get_db()
    target = get_user_by_id(user_id)
    if not target or target['id'] == me['id']:
        flash("用户不存在", "error"); return redirect(url_for('friends'))
    my_groups = db.execute(
        "SELECT g.* FROM groups g JOIN group_members m ON m.group_id=g.id "
        "WHERE m.user_id=? AND m.role IN ('owner','admin')", (me['id'],)).fetchall()
    if request.method == "POST":
        try:
            gid = int(request.form.get("gid") or 0)
        except (ValueError, TypeError):
            gid = 0
        g = get_group(gid)
        if not g or group_role(gid, me['id']) not in ('owner', 'admin'):
            flash("无权限邀请到该群", "error"); return redirect(url_for('group_invite', user_id=user_id))
        if not g['allow_invite']:
            flash("该群已关闭成员邀请", "error"); return redirect(url_for('group_invite', user_id=user_id))
        if group_role(gid, target['id']) == 'black':
            flash("该用户已被本群拉黑", "error"); return redirect(url_for('group_invite', user_id=user_id))
        if group_role(gid, target['id']):
            flash("对方已在群内", "warn"); return redirect(url_for('group_invite', user_id=user_id))
        _group_add_member(gid, target['id'], 'member')
        add_notification(target['id'], 'group_invite',
                        f'{me["username"]} 邀请你加入群组「{g["name"]}」', url_for('group_page', gid=gid))
        flash("已发送入群邀请", "ok"); return redirect(url_for('friends'))
    return render_template("group_invite.html", target=target, my_groups=my_groups)

def _group_add_member(gid, uid, role):
    db = get_db()
    db.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, role, joined_at) VALUES (?,?,?,?)",
               (gid, uid, role, time.time()))
    db.execute("UPDATE groups SET member_count = (SELECT COUNT(*) FROM group_members WHERE group_id=? AND role!='black') WHERE id=?",
               (gid, gid))
    db.commit()

@app.route("/group/<int:gid>/member/<int:uid>/<string:action>", methods=["POST"])
@login_required
def group_member_action(gid, uid, action):
    me = current_user()
    db = get_db()
    def _resp(err=None, ok_msg=None):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            if err: return jsonify({"error": err}), 400
            return jsonify({"ok": True})
        if err: flash(err, "error")
        if ok_msg: flash(ok_msg, "success")
        return redirect(url_for("group_page", gid=gid))
    g = get_group(gid)
    if not g:
        return _resp("群组不存在")
    my_role = group_role(gid, me['id'])
    tgt_role = group_role(gid, uid)
    is_super = bool(me['is_super_admin'])
    if not is_super:
        if not my_role:
            return _resp("你不是本群成员")
        if not tgt_role:
            return _resp("对方不在群内")
        if action in ('kick', 'mute', 'unmute', 'ban', 'unban', 'set_admin', 'set_member'):
            if my_role not in ('owner', 'admin'):
                return _resp("仅群主/管理员可管理成员")
            if my_role != 'owner' and action in ('set_admin', 'set_member', 'ban', 'unban'):
                return _resp("仅群主可任免管理员/拉黑成员")
            if my_role == 'admin' and action in ('kick', 'mute', 'unmute') and tgt_role in ('owner', 'admin'):
                return _resp("管理员无法操作群主或其他管理员")
            if my_role == 'owner' and tgt_role == 'owner':
                return _resp("无法操作自己（群主）")
    else:
        if uid == me['id']:
            return _resp("不能操作自己")
    if action == 'kick':
        db.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (gid, uid))
        _group_sys_msg(gid, f'{get_user_by_id(uid)["username"]} 已被移出群组')
        if my_role == 'owner' and request.form.get('black'):
            db.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, role, joined_at) VALUES (?,?,?,?)",
                       (gid, uid, 'black', time.time()))
            _group_sys_msg(gid, f'{get_user_by_id(uid)["username"]} 已被禁止加入本群')
    elif action == 'ban':
        db.execute("UPDATE group_members SET role='black' WHERE group_id=? AND user_id=?", (gid, uid))
        _group_sys_msg(gid, f'{get_user_by_id(uid)["username"]} 已被禁止加入本群')
    elif action == 'unban':
        db.execute("DELETE FROM group_members WHERE group_id=? AND user_id=? AND role='black'", (gid, uid))
    elif action == 'mute':
        try:
            days = int(request.form.get("days", "1")); hours = int(request.form.get("hours", "0")); mins = int(request.form.get("minutes", "0"))
        except ValueError:
            return _resp("参数错误")
        until = time.time() + days*86400 + hours*3600 + mins*60
        db.execute("UPDATE group_members SET muted_until=? WHERE group_id=? AND user_id=?", (until, gid, uid))
        _group_sys_msg(gid, f'（禁言）{get_user_by_id(uid)["username"]} 被禁言 {days}天{hours}时{mins}分')
    elif action == 'unmute':
        db.execute("UPDATE group_members SET muted_until=0 WHERE group_id=? AND user_id=?", (gid, uid))
    elif action == 'set_admin':
        db.execute("UPDATE group_members SET role='admin' WHERE group_id=? AND user_id=?", (gid, uid))
    elif action == 'set_member':
        db.execute("UPDATE group_members SET role='member' WHERE group_id=? AND user_id=?", (gid, uid))
    else:
        return _resp("未知操作")
    db.commit()
    return _resp(None, "成员操作已执行")

@app.route("/group/<int:gid>/settings", methods=["POST"])
@login_required
def group_settings(gid):
    me = current_user()
    if not can_manage_group(gid, me['id']):
        return jsonify({"error": "无权限"}), 403
    db = get_db()
    g = get_group(gid)
    ann = (request.form.get("announcement") or g['announcement'] or '')[:500]
    jm = request.form.get("join_mode", g['join_mode'])
    if jm not in ("public", "audit", "private"):
        jm = g['join_mode']
    allow_file = 1 if request.form.get("allow_file") else 0
    allow_invite = 1 if request.form.get("allow_invite") else 0
    all_muted = 1 if request.form.get("all_muted") else 0
    is_searchable = 1 if request.form.get("is_searchable") else 0
    name = (request.form.get("name") or g['name'] or '群组')[:30]
    intro = (request.form.get("intro") or g['intro'] or '')[:200]
    db.execute("UPDATE groups SET announcement=?, join_mode=?, allow_file=?, allow_invite=?, all_muted=?, is_searchable=?, name=?, intro=? WHERE id=?",
               (ann, jm, allow_file, allow_invite, all_muted, is_searchable, name, intro, gid))
    if me['is_super_admin'] or group_role(gid, me['id']) == 'owner':
        new_owner = request.form.get("transfer_to")
        if new_owner and new_owner.isdigit():
            nid = int(new_owner)
            if group_role(gid, nid) in ('admin', 'member'):
                db.execute("UPDATE group_members SET role='admin' WHERE group_id=? AND user_id=?", (gid, me['id']))
                db.execute("UPDATE group_members SET role='owner' WHERE group_id=? AND user_id=?", (gid, nid))
                _group_sys_msg(gid, f'群组已转让给 {get_user_by_id(nid)["username"]}')
    if request.form.get("dissolve") and (me['is_super_admin'] or group_role(gid, me['id']) == 'owner'):
        db.execute("DELETE FROM group_messages WHERE group_id=?", (gid,))
        db.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
        db.execute("DELETE FROM group_join_requests WHERE group_id=?", (gid,))
        db.execute("DELETE FROM group_announcement_reads WHERE announcement_id IN (SELECT id FROM group_announcements WHERE group_id=?)", (gid,))
        db.execute("DELETE FROM group_announcements WHERE group_id=?", (gid,))
        db.execute("DELETE FROM group_files WHERE group_id=?", (gid,))
        db.execute("DELETE FROM group_essence WHERE group_id=?", (gid,))
        db.execute("DELETE FROM groups WHERE id=?", (gid,))
        db.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"ok": True, "dissolved": True})
        flash("群组已解散", "info")
        return redirect(url_for("groups"))
    db.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True})
    flash("群设置已保存", "success")
    return redirect(url_for("group_page", gid=gid))

@app.route("/group/<int:gid>/force-dissolve", methods=["POST"])
@super_admin_required
def group_force_dissolve(gid):
    db = get_db()
    g = get_group(gid)
    if not g:
        return jsonify({"error": "群组不存在"}), 404
    db.execute("DELETE FROM group_messages WHERE group_id=?", (gid,))
    db.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
    db.execute("DELETE FROM group_join_requests WHERE group_id=?", (gid,))
    db.execute("DELETE FROM groups WHERE id=?", (gid,))
    db.commit()
    add_notification(g['owner_id'], 'group_dissolved',
                    f'你的群组「{g["name"]}」因违规已被超级管理员强制解散', None)
    return jsonify({"ok": True})

@app.route("/group/<int:gid>/revoke-owner", methods=["POST"])
@super_admin_required
def group_revoke_owner(gid):
    db = get_db()
    if not get_group(gid):
        return jsonify({"error": "群组不存在"}), 404
    db.execute("UPDATE group_members SET role='member' WHERE group_id=? AND role='owner'", (gid,))
    db.commit()
    return jsonify({"ok": True})


def _group_msg_redirect(gid):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True})
    return redirect(url_for('group_page', gid=gid))

@app.route("/group/<int:gid>/announcement/create", methods=["GET", "POST"])
@login_required
def group_announcement_create(gid):
    me = current_user()
    g = get_group(gid)
    if not g:
        abort(404)
    if not can_manage_group(gid, me['id']):
        if request.method == "GET":
            flash("仅群主/管理员可发布公告", "error"); return redirect(url_for('group_page', gid=gid))
        return jsonify({"error": "无权限"}), 403
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()[:100]
        content = (request.form.get("content") or "").strip()[:1000]
        require_read = 1 if request.form.get("require_read") else 0
        if not title or not content:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"error": "标题和内容必填"}), 400
            flash("标题和内容必填", "error"); return redirect(url_for('group_page', gid=gid))
        db = get_db()
        db.execute("INSERT INTO group_announcements (group_id, user_id, title, content, require_read, read_count, created_at) "
                   "VALUES (?,?,?,?,?,0,?)", (gid, me['id'], title, content, require_read, time.time()))
        db.commit()
        _group_sys_msg(gid, "📢 新公告：%s" % title)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"ok": True})
        flash("公告已发布", "ok"); return redirect(url_for('group_page', gid=gid))
    return render_template_string("""
    {% extends "base.html" %}
    {% block title %}发布群公告{% endblock %}
    {% set active = 'groups' %}
    {% block content %}
    <div class="card narrow" style="margin:24px auto;">
      <h2>发布群公告</h2>
      <p class="sub">群：{{ g.name }}</p>
      <form method="post">
        <label>标题</label>
        <input type="text" name="title" maxlength="100" required>
        <label>内容</label>
        <textarea name="content" maxlength="1000" rows="5" required></textarea>
        <label style="display:flex;gap:6px;align-items:center;"><input type="checkbox" name="require_read"> 要求成员阅读</label>
        <button type="submit" class="btn block">发布公告</button>
        <a href="{{ url_for('group_page', gid=g.id) }}" class="btn ghost block" style="margin-top:8px;">取消</a>
      </form>
    </div>
    {% endblock %}
    """, g=g)

@app.route("/group/<int:gid>/announcement/<int:aid>/delete", methods=["POST"])
@login_required
def group_announcement_delete(gid, aid):
    me = current_user()
    if not can_manage_group(gid, me['id']):
        return jsonify({"error": "无权限"}), 403
    db = get_db()
    db.execute("DELETE FROM group_announcements WHERE id=? AND group_id=?", (aid, gid))
    db.execute("DELETE FROM group_announcement_reads WHERE announcement_id=?", (aid,))
    db.commit()
    return _group_msg_redirect(gid)

@app.route("/group/<int:gid>/announcement/<int:aid>/read", methods=["POST"])
@login_required
def group_announcement_read(gid, aid):
    me = current_user()
    db = get_db()
    if not group_membership(gid, me['id']):
        return jsonify({"error": "你不在该群"}), 403
    read = db.execute("SELECT 1 FROM group_announcement_reads WHERE announcement_id=? AND user_id=?",
                     (aid, me['id'])).fetchone()
    if not read:
        db.execute("INSERT OR IGNORE INTO group_announcement_reads (announcement_id, user_id, read_at) VALUES (?,?,?)",
                   (aid, me['id'], time.time()))
        db.execute("UPDATE group_announcements SET read_count = (SELECT COUNT(*) FROM group_announcement_reads WHERE announcement_id=?) WHERE id=?",
                   (aid, aid))
        db.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True})
    return redirect(url_for('group_page', gid=gid))

@app.route("/group/<int:gid>/upload-file", methods=["POST"])
@login_required
def group_upload_file(gid):
    me = current_user()
    g = get_group(gid)
    if not g:
        return jsonify({"error": "群组不存在"}), 404
    db = get_db()
    m = group_membership(gid, me['id'])
    if not m:
        return jsonify({"error": "你不在该群"}), 403
    role = m['role']
    if role == 'black':
        return jsonify({"error": "你已被禁止加入该群"}), 403
    if g['allow_file'] != 1 and role not in ('owner', 'admin'):
        return jsonify({"error": "本群已关闭文件上传"}), 403
    file = request.files.get('file')
    if not file or file.filename == '':
        return jsonify({"error": "请选择文件"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": ext_reject_msg()}), 400
    folder = os.path.join(app.config['UPLOAD_FOLDER'], "group_%d" % gid)
    os.makedirs(folder, exist_ok=True)
    ext = file.filename.rsplit('.', 1)[1].lower()
    stored = "group_%d/%s.%s" % (gid, uuid.uuid4().hex, ext)
    gpath = os.path.join(app.config['UPLOAD_FOLDER'], stored)
    file.save(gpath)
    img_meta = ''
    if is_image(file.filename):
        ftype = 'image'
        stored, img_meta = _save_upload_image(gpath, stored)
        file_url = url_for('uploaded_file', filename=stored)
        try:
            fsize = os.path.getsize(os.path.join(app.config['UPLOAD_FOLDER'], stored))
        except Exception:
            fsize = 0
    elif is_video(file.filename):
        ftype = 'video'
        file_url = url_for('uploaded_file', filename=stored)
        try:
            fsize = os.path.getsize(gpath)
        except Exception:
            fsize = 0
    else:
        ftype = 'file'
        file_url = url_for('uploaded_file', filename=stored)
        try:
            fsize = os.path.getsize(gpath)
        except Exception:
            fsize = 0
    db.execute("INSERT INTO group_files (group_id, user_id, username, file_name, file_url, file_type, file_size, img_meta, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)",
               (gid, me['id'], me['username'], sanitize_display_name(file.filename), file_url, ftype, fsize, img_meta, time.time()))
    db.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True, "file_url": file_url, "file_type": ftype, "img_meta": img_meta})
    flash("文件已上传", "ok"); return redirect(url_for('group_page', gid=gid))

@app.route("/group/file/<int:fid>/download")
@login_required
def group_file_download(fid):
    me = current_user()
    db = get_db()
    f = db.execute("SELECT * FROM group_files WHERE id=?", (fid,)).fetchone()
    if not f:
        abort(404)
    role = group_role(f['group_id'], me['id'])
    if not role or role == 'black':
        return jsonify({"error": "无权限"}), 403
    rel = f['file_url'].replace('/uploads/', '').replace('/upload/', '')
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], rel,
                                   as_attachment=True, download_name=f['file_name'])
    except Exception:
        abort(404)

@app.route("/group/file/<int:fid>/delete", methods=["POST"])
@login_required
def group_file_delete(fid):
    me = current_user()
    db = get_db()
    f = db.execute("SELECT * FROM group_files WHERE id=?", (fid,)).fetchone()
    if not f:
        return jsonify({"error": "文件不存在"}), 404
    role = group_role(f['group_id'], me['id'])
    if role not in ('owner', 'admin') and f['user_id'] != me['id']:
        return jsonify({"error": "无权限删除该文件"}), 403
    rel = f['file_url'].replace('/uploads/', '').replace('/upload/', '')
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], rel))
    except Exception:
        pass
    db.execute("DELETE FROM group_files WHERE id=?", (fid,))
    db.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True})
    flash("文件已删除", "ok"); return redirect(url_for('group_page', gid=f['group_id']))

@app.route("/group/<int:gid>/message/<int:mid>/essence", methods=["POST"])
@login_required
def group_message_essence(gid, mid):
    me = current_user()
    if not can_manage_group(gid, me['id']):
        return jsonify({"error": "无权限"}), 403
    db = get_db()
    msg = db.execute("SELECT * FROM group_messages WHERE id=? AND group_id=?", (mid, gid)).fetchone()
    if not msg:
        return jsonify({"error": "消息不存在"}), 404
    existing = db.execute("SELECT * FROM group_essence WHERE group_id=? AND message_id=?", (gid, mid)).fetchone()
    if existing:
        db.execute("DELETE FROM group_essence WHERE id=?", (existing['id'],))
        state = "removed"
    else:
        db.execute("INSERT INTO group_essence (group_id, message_id, operator_id, created_at) VALUES (?,?,?,?)",
                   (gid, mid, me['id'], time.time()))
        state = "added"
    db.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True, "state": state})
    flash("操作成功", "ok"); return redirect(url_for('group_page', gid=gid))

@app.route("/group/<int:gid>/message/<int:mid>/recall", methods=["POST"])
@login_required
def group_message_recall(gid, mid):
    me = current_user()
    g = get_group(gid)
    if not g:
        return jsonify({"error": "群组不存在"}), 404
    db = get_db()
    msg = db.execute("SELECT * FROM group_messages WHERE id=? AND group_id=?", (mid, gid)).fetchone()
    if not msg:
        return jsonify({"error": "消息不存在"}), 404
    my_role = group_role(gid, me['id'])
    can = False
    if me['is_admin'] or me['is_super_admin']:
        can = True
    elif my_role in ('owner', 'admin'):
        can = True
    elif msg['user_id'] == me['id'] and (time.time() - float(msg['created_at'])) <= 120:
        can = True
    if not can:
        return jsonify({"error": "该消息不可撤回"}), 403
    db.execute("UPDATE group_messages SET is_deleted=1, content='消息已撤回' WHERE id=?", (mid,))
    db.commit()
    socketio.emit("group_message_recalled", {"message_id": mid, "group_id": gid}, room='group_%d' % gid)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True})
    return redirect(url_for('group_page', gid=gid))

@app.route("/group/<int:gid>/leave", methods=["POST"])
@login_required
def group_leave(gid):
    me = current_user()
    g = get_group(gid)
    if not g:
        return jsonify({"error": "群组不存在"}), 404
    role = group_role(gid, me['id'])
    if not role:
        return _group_msg_redirect(gid)
    if role == 'owner':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": "群主需先转让群主才能退群"}), 400
        flash("群主需先转让群主才能退群", "error"); return redirect(url_for('group_page', gid=gid))
    db = get_db()
    db.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (gid, me['id']))
    _group_sys_msg(gid, "%s 退出了群组" % me['username'])
    db.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True})
    flash("已退出群组", "ok"); return redirect(url_for('groups'))

@app.route("/group/<int:gid>/invite-link")
@login_required
def group_invite_link(gid):
    """生成私密群一次性邀请链接（code = gid:timestamp，10 分钟内有效）。"""
    me = current_user()
    g = get_group(gid)
    if not g:
        abort(404)
    role = group_role(gid, me['id'])
    if role in (None, 'black'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": "你不在该群"}), 403
        flash("你不在该群", "error"); return redirect(url_for('group_page', gid=gid))
    code = "%d:%d" % (gid, int(time.time()))
    link = url_for('group_join_by_code', code=code, _external=True)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True, "link": link})
    return render_template_string("""
    {% extends "base.html" %}
    {% block title %}邀请链接{% endblock %}
    {% set active = 'groups' %}
    {% block content %}
    <div class="card narrow" style="margin:24px auto;">
      <h2>群邀请链接</h2>
      <p class="sub">「{{ g.name }}」· 链接 10 分钟内有效，可多次使用</p>
      <input id="ilink" readonly value="{{ link }}" style="width:100%;padding:8px;margin:8px 0;">
      <button class="btn block" onclick="navigator.clipboard.writeText(document.getElementById('ilink').value)">复制链接</button>
      <a href="{{ back }}" class="btn ghost block" style="margin-top:8px;">返回群</a>
    </div>
    {% endblock %}
    """, link=link, g=g, back=url_for('group_page', gid=gid))

@app.route("/group/join-by-code")
@login_required
def group_join_by_code():
    me = current_user()
    code = request.args.get("code") or ""
    if ":" not in code:
        flash("无效邀请码", "error"); return redirect(url_for('groups'))
    try:
        gid_s, ts_s = code.split(":", 1)
        gid = int(gid_s); ts = int(ts_s)
    except ValueError:
        flash("无效邀请码", "error"); return redirect(url_for('groups'))
    if time.time() - ts > 600:
        flash("邀请链接已过期（10 分钟）", "error"); return redirect(url_for('groups'))
    g = get_group(gid)
    if not g:
        flash("群组不存在", "error"); return redirect(url_for('groups'))
    existing = group_membership(gid, me['id'])
    if existing:
        if existing['role'] == 'black':
            flash("你已被本群拉黑，无法加入", "error"); return redirect(url_for('groups'))
        return redirect(url_for('group_page', gid=gid))
    _group_add_member(gid, me['id'], 'member')
    _group_sys_msg(gid, "欢迎 %s 加入群组" % me['username'])
    _show_latest_announcement(gid, me['id'])
    flash("已通过邀请加入群组", "ok"); return redirect(url_for('group_page', gid=gid))

def _group_sys_msg(gid, text):
    db = get_db()
    db.execute("INSERT INTO group_messages (group_id, user_id, username, content, is_system, created_at) "
               "VALUES (?,0,'系统',?,1,?)", (gid, text, time.time()))
    db.commit()

def _show_latest_announcement(gid, uid):
    """新成员入群时推送最新群公告（若未读则插入系统消息 + 已读记录）。"""
    db = get_db()
    a = db.execute("SELECT * FROM group_announcements WHERE group_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                   (gid,)).fetchone()
    if not a:
        return
    read = db.execute("SELECT 1 FROM group_announcement_reads WHERE announcement_id=? AND user_id=?",
                     (a['id'], uid)).fetchone()
    if read:
        return
    _group_sys_msg(gid, "📢 最新群公告：%s" % a['content'])
    db.execute("INSERT OR IGNORE INTO group_announcement_reads (announcement_id, user_id, read_at) VALUES (?,?,?)",
               (a['id'], uid, time.time()))
    db.execute("UPDATE group_announcements SET read_count = (SELECT COUNT(*) FROM group_announcement_reads WHERE announcement_id=?) WHERE id=?",
               (a['id'], a['id']))
    db.commit()

def _group_can_send(gid, uid):
    """群消息发送权限：必须是普通成员/管理员/群主，且未被群内禁言、未被全员禁言（管理/群主不受全员禁言限制）。"""
    role = group_role(gid, uid)
    if role in (None, 'black'):
        return False, "你不在该群或无权限发言"
    if role == 'member':
        m = group_membership(gid, uid)
        if m and is_active_restriction(m['muted_until']):
            return False, "你被群内禁言"
        g = get_group(gid)
        if g and g['all_muted']:
            return False, "本群已开启全员禁言"
    return True, ""

ACTIVITY_CATEGORIES = ["技术活动", "游戏活动", "趣味闲聊"]

def _activity_auto_status(a):
    """根据时间计算活动实际状态：未开始 → 进行中 → 已结束"""
    now = time.time()
    if a['status'] == 'ended':
        return 'ended'
    if now < float(a['start_time'] or 0):
        return 'upcoming'
    if float(a['end_time'] or 0) and now >= float(a['end_time']):
        return 'ended'
    return 'ongoing'

def refresh_activity_status(db):
    """惰性刷新所有活动状态（在列表/详情页调用）"""
    rows = db.execute("SELECT id, status, start_time, end_time FROM activities").fetchall()
    changed = False
    for a in rows:
        new_status = _activity_auto_status(a)
        if new_status != a['status']:
            db.execute("UPDATE activities SET status=? WHERE id=?", (new_status, a['id']))
            changed = True
    if changed:
        db.commit()

@app.route("/activities")
def activities_page():
    db = get_db()
    refresh_activity_status(db)
    rows = db.execute(
        "SELECT a.*, (SELECT COUNT(*) FROM activity_participants p WHERE p.activity_id=a.id) AS pcount "
        "FROM activities a ORDER BY CASE a.status WHEN 'ongoing' THEN 0 WHEN 'upcoming' THEN 1 ELSE 2 END, "
        "a.start_time DESC").fetchall()
    me = current_user()
    joined = set()
    if me:
        joined = {r['activity_id'] for r in db.execute(
            "SELECT activity_id FROM activity_participants WHERE user_id=?", (me['id'],)).fetchall()}
    return render_template("activities.html", activities=rows, joined=joined)

@app.route("/create-activity", methods=["GET", "POST"])
@admin_required
def create_activity():
    me = current_user()
    db = get_db()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:60]
        intro = (request.form.get("intro") or "").strip()[:2000]
        category = request.form.get("category") or "趣味闲聊"
        if category not in ACTIVITY_CATEGORIES:
            category = "趣味闲聊"
        mode = request.form.get("mode") or "submission"
        if mode not in ("submission", "online"):
            mode = "submission"
        room_id = request.form.get("room_id", type=int)
        try:
            st = time.mktime(time.strptime((request.form.get("start_time") or "").strip(), "%Y-%m-%dT%H:%M"))
            et = time.mktime(time.strptime((request.form.get("end_time") or "").strip(), "%Y-%m-%dT%H:%M"))
        except Exception:
            flash("请填写有效的开始/结束时间", "error")
            return redirect(url_for("create_activity"))
        if not name or et <= st:
            flash("活动名称必填，且结束时间需晚于开始时间", "error")
            return redirect(url_for("create_activity"))
        cover = ""
        file = request.files.get("cover")
        if file and file.filename and is_image(file.filename):
            fn = f"{uuid.uuid4().hex}_{sanitize_display_name(file.filename)}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
            cover = url_for('uploaded_file', filename=fn)
        cur = db.execute(
            "INSERT INTO activities (name, cover, intro, category, mode, status, room_id, start_time, end_time, "
            "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (name, cover, intro, category, mode, 'upcoming', room_id if mode == 'online' else None,
             st, et, me['id'], time.time()))
        aid = cur.lastrowid
        db.commit()
        refresh_activity_status(db)
        broadcast_notification('activity', f'🎉 新活动上线：{name}', url_for('activity_page', aid=aid))
        add_admin_log(me, "创建活动", name, f"模式={mode}")
        flash("活动创建成功", "ok")
        return redirect(url_for("activity_page", aid=aid))
    rooms = db.execute("SELECT id, name FROM chat_rooms ORDER BY id").fetchall()
    return render_template("create_activity.html", rooms=rooms, categories=ACTIVITY_CATEGORIES)

@app.route("/activity/<int:aid>")
def activity_page(aid):
    db = get_db()
    refresh_activity_status(db)
    a = db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    parts = db.execute(
        "SELECT p.*, u.avatar, u.level FROM activity_participants p JOIN users u ON u.id=p.user_id "
        "WHERE p.activity_id=? ORDER BY CASE p.status WHEN 'awarded' THEN 0 ELSE 1 END, p.id DESC", (aid,)).fetchall()
    me = current_user()
    mine = None
    if me:
        mine = db.execute("SELECT * FROM activity_participants WHERE activity_id=? AND user_id=?",
                          (aid, me['id'])).fetchone()
    room = None
    if a['room_id']:
        room = db.execute("SELECT id, name FROM chat_rooms WHERE id=?", (a['room_id'],)).fetchone()
    return render_template("activity.html", a=a, parts=parts, mine=mine, room=room,
                           og=og_context(title=(a['name'] or '') + " · MONO 活动",
                                         desc=a['intro'] or a['name'],
                                         type_="article",
                                         site_name="MONO 社区"))

@app.route("/activity/<int:aid>/join", methods=["POST"])
@login_required
def activity_join(aid):
    """线上集合模式：报名参与，+5 经验"""
    me = current_user()
    db = get_db()
    refresh_activity_status(db)
    a = db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    if a['status'] != 'ongoing':
        flash("仅进行中的活动可参与", "error")
        return redirect(url_for("activity_page", aid=aid))
    exist = db.execute("SELECT 1 FROM activity_participants WHERE activity_id=? AND user_id=?",
                       (aid, me['id'])).fetchone()
    if exist:
        flash("你已参与过该活动", "error")
        return redirect(url_for("activity_page", aid=aid))
    db.execute("INSERT INTO activity_participants (activity_id, user_id, username, status, created_at) "
               "VALUES (?,?,?,'approved',?)", (aid, me['id'], me['username'], time.time()))
    db.commit()
    add_exp(me['id'], 5)
    flash("参与成功，经验 +5", "ok")
    return redirect(url_for("activity_page", aid=aid))

@app.route("/activity/<int:aid>/submit", methods=["POST"])
@login_required
def activity_submit(aid):
    """投稿征集模式：提交作品（帖子链接或文字说明），首次提交 +5 经验"""
    me = current_user()
    db = get_db()
    refresh_activity_status(db)
    a = db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a or a['mode'] != 'submission':
        abort(404)
    if a['status'] != 'ongoing':
        flash("仅进行中的活动可投稿", "error")
        return redirect(url_for("activity_page", aid=aid))
    submission = (request.form.get("submission") or "").strip()[:1000]
    sub_url = (request.form.get("submission_url") or "").strip()[:300]
    if not submission and not sub_url:
        flash("请填写投稿内容或作品链接", "error")
        return redirect(url_for("activity_page", aid=aid))
    if submission:
        submission, block_err = filter_and_block(submission, me, "activity")
        if block_err:
            flash(f"投稿内容被拦截：{block_err}", "error")
            return redirect(url_for("activity_page", aid=aid))
    exist = db.execute("SELECT * FROM activity_participants WHERE activity_id=? AND user_id=?",
                       (aid, me['id'])).fetchone()
    if exist:
        db.execute("UPDATE activity_participants SET submission=?, submission_url=?, status='pending' WHERE id=?",
                   (submission, sub_url, exist['id']))
        db.commit()
        flash("投稿已更新，等待管理员审核", "ok")
    else:
        db.execute("INSERT INTO activity_participants (activity_id, user_id, username, submission, submission_url, "
                   "status, created_at) VALUES (?,?,?,?,?,'pending',?)",
                   (aid, me['id'], me['username'], submission, sub_url, time.time()))
        db.commit()
        add_exp(me['id'], 5)
        flash("投稿成功，经验 +5，等待管理员审核", "ok")
    return redirect(url_for("activity_page", aid=aid))

@app.route("/activity/<int:aid>/participant/<int:pid>/<string:action>", methods=["POST"])
@admin_required
def activity_participant_action(aid, pid, action):
    """管理员审核投稿 / 颁奖：approve / reject / award"""
    me = current_user()
    db = get_db()
    p = db.execute("SELECT * FROM activity_participants WHERE id=? AND activity_id=?", (pid, aid)).fetchone()
    a = db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not p or not a:
        abort(404)
    if action == "approve":
        db.execute("UPDATE activity_participants SET status='approved' WHERE id=?", (pid,))
        db.commit()
        add_notification(p['user_id'], 'activity', f'你在活动「{a["name"]}」的投稿已通过审核',
                         url_for('activity_page', aid=aid))
    elif action == "reject":
        db.execute("UPDATE activity_participants SET status='rejected' WHERE id=?", (pid,))
        db.commit()
        add_notification(p['user_id'], 'activity', f'你在活动「{a["name"]}」的投稿未通过审核',
                         url_for('activity_page', aid=aid))
    elif action == "award":
        if p['status'] == 'awarded':
            flash("该投稿已颁奖，不能重复颁奖", "error")
            return redirect(url_for("activity_page", aid=aid))
        bonus = request.form.get("bonus", type=int) or 20
        bonus = max(1, min(bonus, 200))
        db.execute("UPDATE activity_participants SET status='awarded', award_exp=? WHERE id=?", (bonus, pid))
        db.commit()
        add_exp(p['user_id'], bonus)
        add_notification(p['user_id'], 'activity', f'🏆 恭喜！你在活动「{a["name"]}」中获奖，经验 +{bonus}',
                         url_for('activity_page', aid=aid))
        add_admin_log(me, "活动颁奖", p['username'], f"活动={a['name']} 经验+{bonus}")
    else:
        abort(400)
    return redirect(url_for("activity_page", aid=aid))

@app.route("/activity/<int:aid>/status/<string:new_status>", methods=["POST"])
@admin_required
def activity_set_status(aid, new_status):
    """管理员手动流转状态（如提前结束）"""
    me = current_user()
    db = get_db()
    a = db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a or new_status not in ("upcoming", "ongoing", "ended"):
        abort(404)
    db.execute("UPDATE activities SET status=? WHERE id=?", (new_status, aid))
    now = time.time()
    if new_status == "ongoing":
        db.execute("UPDATE activities SET start_time=MIN(start_time, ?), end_time=MAX(end_time, ?) WHERE id=?",
                   (now, now + 3600, aid))
    db.commit()
    add_admin_log(me, "活动状态变更", a['name'], f"→ {new_status}")
    flash("活动状态已更新", "ok")
    return redirect(url_for("activity_page", aid=aid))

@app.route("/activity/<int:aid>/delete", methods=["POST"])
@super_admin_required
def activity_delete(aid):
    me = current_user()
    db = get_db()
    a = db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    db.execute("DELETE FROM activity_participants WHERE activity_id=?", (aid,))
    db.execute("DELETE FROM activities WHERE id=?", (aid,))
    db.commit()
    add_admin_log(me, "删除活动", a['name'])
    flash("活动已删除", "ok")
    return redirect(url_for("activities_page"))


@app.route("/activity/<int:aid>/edit", methods=["POST"])
@super_admin_required
def activity_edit(aid):
    """超管编辑任意活动：改名/改介绍/改时间/改状态。"""
    me = current_user()
    db = get_db()
    a = db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a:
        return jsonify({"ok": False, "msg": "活动不存在"}), 404
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or a['name']).strip()[:60]
    intro = (data.get("intro") if data.get("intro") is not None else a['intro']).strip()[:2000]
    new_status = data.get("status") or a['status']
    if new_status not in ('upcoming', 'ongoing', 'finished'):
        new_status = a['status']
    db.execute("UPDATE activities SET name=?, intro=?, status=? WHERE id=?",
               (name, intro, new_status, aid))
    db.commit()
    add_admin_log(me, "编辑活动", name, "状态=%s" % new_status)
    return jsonify({"ok": True, "msg": "活动已更新"})

@app.route("/blacklist")
@login_required
def blacklist_page():
    me = current_user()
    db = get_db()
    rows = db.execute("""
        SELECT u.id, u.username, u.avatar, b.created_at AS blocked_at
        FROM blacklist b JOIN users u ON b.blocked_id = u.id
        WHERE b.user_id = ? ORDER BY b.created_at DESC
    """, (me['id'],)).fetchall()
    return render_template("blacklist.html", blocked=rows)

@app.route("/block/<int:user_id>", methods=["POST"])
@login_required
def block_user(user_id):
    me = current_user()
    target = get_user_by_id(user_id)
    if not target or target['id'] == me['id']:
        flash("无法拉黑该用户", "error")
        return redirect(request.referrer or url_for('friends'))
    if target['is_admin'] and not me['is_super_admin']:
        flash("无法拉黑管理员", "error")
        return redirect(request.referrer or url_for('friends'))
    db = get_db()
    db.execute("INSERT OR IGNORE INTO blacklist (user_id, blocked_id, created_at) VALUES (?,?,?)",
               (me['id'], user_id, time.time()))
    db.execute("DELETE FROM friends WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)",
               (me['id'], user_id, user_id, me['id']))
    db.commit()
    _log_warning(me, "block_user", "拉黑 %s" % target['username'])
    flash("已将 %s 加入黑名单" % target['username'], "ok")
    return redirect(request.referrer or url_for('blacklist_page'))

@app.route("/unblock/<int:user_id>", methods=["POST"])
@login_required
def unblock_user(user_id):
    me = current_user()
    db = get_db()
    db.execute("DELETE FROM blacklist WHERE user_id = ? AND blocked_id = ?", (me['id'], user_id))
    db.commit()
    flash("已移出黑名单", "ok")
    return redirect(request.referrer or url_for('blacklist_page'))

@app.route("/messages")
@login_required
def messages_page():
    return render_template("messages.html", user=current_user(), active="messages")

@app.route("/forum")
def forum():
    db = get_db()
    user = current_user()
    try:
        sec_id = int(request.args.get("section", "0"))
    except ValueError:
        sec_id = 0
    sort = request.args.get("sort", "new")
    if sort not in ("new", "reply", "hot"):
        sort = "new"
    sections = db.execute(
        "SELECT * FROM forum_sections WHERE is_hidden=0 ORDER BY sort_order ASC").fetchall()

    # 2026-11-01：原来只过滤 recycle，软删（is_deleted=1）的帖子还挂在列表里
    where = "p.recycle = 0 AND COALESCE(p.is_deleted, 0) = 0"
    params = []
    if sec_id:
        where += " AND p.section_id = ?"
        params.append(sec_id)
    if user:
        where += " AND p.user_id NOT IN (SELECT user_id FROM blacklist WHERE blocked_id = ?)"
        params.append(user['id'])
    _now = time.time()
    if sort == "reply":
        order = "p.is_pinned DESC, COALESCE(p.last_reply_at, p.created_at) DESC"
    elif sort == "hot":
        # 060 务实版：互动加权 / 时间衰减（7 天半衰期）。
        # 不做协同过滤 —— 全站帖子数还没到能跑协同过滤的量级，
        # 规则排序就能解决「早期好帖沉底」这个真问题。
        order = (
            "p.is_pinned DESC, ("
            "  COALESCE(p.likes, 0) * 3.0"
            "  + (SELECT COUNT(*) FROM post_comments c WHERE c.post_id = p.id AND c.is_deleted = 0) * 5.0"
            "  + COALESCE(p.view_count, 0) * 0.2"
            ") / (1.0 + (CAST(strftime('%s','now') AS REAL) - p.created_at) / 604800.0) DESC"
        )
    else:
        order = "p.is_pinned DESC, p.created_at DESC"
    posts = db.execute(f"""
        SELECT p.*, u.avatar, u.is_admin, u.has_effect, u.effect_type, u.badge_text, u.badge_effect, u.mall_title, u.mall_badge,
            s.name AS section_name,
            (SELECT COUNT(*) FROM post_comments c WHERE c.post_id = p.id AND c.is_deleted=0) AS real_comment_count
        FROM posts p
        LEFT JOIN users u ON p.user_id = u.id
        LEFT JOIN forum_sections s ON p.section_id = s.id
        WHERE {where}
        ORDER BY {order}
    """, params).fetchall()

    user_likes = set()
    if user:
        likes = db.execute("SELECT post_id FROM post_likes WHERE user_id = ?", (user['id'],)).fetchall()
        user_likes = {l['post_id'] for l in likes}

    cur_section = db.execute("SELECT * FROM forum_sections WHERE id=?", (sec_id,)).fetchone() if sec_id else None
    # 064：编辑精选（is_essence=1，最多 4 篇，横向展示在列表上方）
    try:
        essence_posts = db.execute("""
            SELECT p.id, p.title, p.username, p.view_count,
                   (SELECT COUNT(*) FROM post_comments c
                    WHERE c.post_id = p.id AND c.is_deleted = 0) AS real_comment_count
            FROM posts p
            WHERE COALESCE(p.is_essence, 0) = 1
              AND COALESCE(p.recycle, 0) = 0 AND COALESCE(p.is_deleted, 0) = 0
            ORDER BY p.created_at DESC LIMIT 4
        """).fetchall()
    except Exception:
        essence_posts = []

    return render_template("forum.html", posts=posts, user_likes=user_likes,
                           sections=sections, cur_section=cur_section, sec_id=sec_id, sort=sort,
                           essence_posts=essence_posts,
                           now_timestamp=time.time(),
                           board_threads=(_topics.threads_for_post_select(sec_id)
                                          if sec_id else []))

@app.route("/post/<int:post_id>")
def view_post(post_id):
    db = get_db()
    user = current_user()
    post = db.execute("""
        SELECT p.*, u.avatar, u.is_admin, u.has_effect, u.effect_type, u.badge_text, u.badge_effect, u.mall_title, u.mall_badge,
            s.name AS section_name, s.is_announcement AS section_is_announcement
        FROM posts p LEFT JOIN users u ON p.user_id = u.id
        LEFT JOIN forum_sections s ON p.section_id = s.id
        WHERE p.id = ?
    """, (post_id,)).fetchone()
    if not post:
        abort(404)
    # 2026-11-01：软删的帖子对普通用户 404（管理员还能看，方便恢复）
    _deleted = False
    try:
        _deleted = bool(post['is_deleted'])
    except (IndexError, KeyError):
        pass
    if (post['recycle'] or _deleted) and (not user or not user['is_admin']):
        abort(404)
    if user and user['id'] != post['user_id']:
        blk = db.execute("SELECT 1 FROM blacklist WHERE user_id=? AND blocked_id=?",
                         (post['user_id'], user['id'])).fetchone()
        if blk:
            abort(404)

    if user and user['id'] != post['user_id']:
        cur = db.execute("INSERT OR IGNORE INTO post_views (user_id, post_id, created_at) VALUES (?,?,?)",
                         (user['id'], post_id, time.time()))
        if cur.rowcount:
            db.execute("UPDATE posts SET view_count = view_count + 1 WHERE id = ?", (post_id,))
        db.commit()

    comments = db.execute("""
        SELECT c.*, u.avatar, u.is_admin, u.has_effect, u.effect_type, u.badge_text, u.badge_effect, u.mall_title, u.mall_badge
        FROM post_comments c LEFT JOIN users u ON c.user_id = u.id
        WHERE c.post_id = ? ORDER BY c.created_at ASC
    """, (post_id,)).fetchall()

    liked = False
    favorited = False
    comment_liked = set()
    if user:
        like_row = db.execute("SELECT * FROM post_likes WHERE post_id = ? AND user_id = ?",
                             (post_id, user['id'])).fetchone()
        liked = like_row is not None
        fav_row = db.execute("SELECT 1 FROM favorites WHERE user_id=? AND post_id=?",
                            (user['id'], post_id)).fetchone()
        favorited = fav_row is not None
        cl = db.execute(
            "SELECT comment_id FROM comment_likes WHERE user_id=? AND comment_id IN "
            "(SELECT id FROM post_comments WHERE post_id=?)", (user['id'], post_id)).fetchall()
        comment_liked = {r['comment_id'] for r in cl}

    top_comments = [c for c in comments if not c['parent_id']]
    children = {}
    for c in comments:
        if c['parent_id']:
            children.setdefault(c['parent_id'], []).append(c)
    tag_list = [t for t in (post['tags'] or '').split(',') if t]
    poll = None
    p = db.execute("SELECT * FROM polls WHERE post_id = ?", (post_id,)).fetchone()
    if p:
        popts = db.execute("SELECT * FROM poll_options WHERE poll_id = ? ORDER BY id ASC", (p['id'],)).fetchall()
        ptotal = sum(o['votes'] for o in popts)
        pvoted = False
        if user:
            pvoted = bool(db.execute("SELECT 1 FROM poll_votes WHERE poll_id=? AND user_id=?",
                                  (p['id'], user['id'])).fetchone())
        poll_closed = bool(p['closed']) or (p['deadline'] > 0 and time.time() > p['deadline'])
        poll = {"p": p, "opts": popts, "total": ptotal, "voted": pvoted, "closed": poll_closed}
    # 061：相关讨论（TF-IDF + 余弦，数据少时可能为空，不抛异常）
    try:
        _related = _similarity.related_posts(get_db(), post['id'], limit=3)
    except Exception:
        _related = []

    return render_template("view_post.html", post=post, comments=comments,
                          top_comments=top_comments, children=children,
                          related_posts=_related,
                          liked=liked, favorited=favorited, comment_liked=comment_liked,
                          tag_list=tag_list, poll=poll,
                          post_tags=[r['name'] for r in _topics.post_tags(post['id'])],
                          post_thread=(get_db().execute(
                              "SELECT id, name FROM threads WHERE id=?",
                              (post['thread_id'],)).fetchone()
                              if post['thread_id'] else None),
                          related=_topics.related_posts(post['id']),
                          og=og_context(
                              title=(post['title'] or '') + " · MONO 社区",
                              desc=post['summary'] or post['content'],
                              type_="article",
                              site_name="MONO 社区",
                          ))

@app.route("/create-post", methods=["GET", "POST"])
@login_required
def create_post():
    user = current_user()
    if is_active_restriction(user["muted_until"]):
        flash("你已被禁言，无法发帖", "error")
        return redirect(url_for("forum"))

    db = get_db()
    sections = db.execute(
        "SELECT * FROM forum_sections WHERE is_hidden=0 ORDER BY sort_order ASC").fetchall()
    if request.method == "POST":
        _ok, _wait = flood_check('post', user['id'])
        if not _ok:
            flash("发帖太快了，请等 %d 秒后再发" % _wait, "warn")
            return redirect(url_for("forum"))
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        summary = (request.form.get("summary") or "").strip()[:200]
        try:
            section_id = int(request.form.get("section_id", "1"))
        except ValueError:
            section_id = 1
        section = db.execute("SELECT * FROM forum_sections WHERE id=?", (section_id,)).fetchone()
        if not section or section['is_hidden']:
            flash("板块不存在", "error")
            return render_template("edit_post.html", sections=sections)
        if section['is_closed'] and not user['is_admin']:
            flash("该板块已关闭发帖", "error")
            return render_template("edit_post.html", sections=sections)
        if section['is_announcement'] and not user['is_admin']:
            flash("官方公告区仅管理员可发帖", "error")
            return render_template("edit_post.html", sections=sections)

        if not title or not content:
            flash("标题和内容不能为空", "error")
            return render_template("edit_post.html", sections=sections)

        filtered_title, title_err = filter_and_block(title, user, "forum_post")
        if title_err:
            flash(title_err, "error")
            return render_template("edit_post.html", sections=sections)
        filtered_content, content_err = filter_and_block(content, user, "forum_post")
        if content_err:
            flash(content_err, "error")
            return render_template("edit_post.html", sections=sections)
        if summary:
            summary, sum_err = filter_and_block(summary, user, "forum_post")
            if sum_err:
                flash(sum_err, "error")
                return render_template("edit_post.html", sections=sections)

        # 060 附赠：无意义内容前置拦截（标题「1」+ 正文「1」这种直接不让发）
        _ml_hit, _ml_why = _moderation.meaningless_check(filtered_title, filtered_content)
        if _ml_hit:
            flash("这条帖子没有实际内容（%s），写点正经的吧" % _ml_why, "error")
            return render_template("edit_post.html", sections=sections)

        if not repeat_guard("create_post", [section_id, filtered_title, filtered_content], uid=user['id']):
            dup = db.execute(
                "SELECT id FROM posts WHERE user_id=? AND title=? ORDER BY id DESC LIMIT 1",
                (user['id'], filtered_title)).fetchone()
            flash("这条帖子刚刚已经发出去了，没有重复发布", "ok")
            if dup:
                return redirect(url_for("view_post", post_id=dup['id']))
            return redirect(url_for("forum"))

        now = time.time()
        # 030：主题归属（默认主题兜底）
        try:
            thread_id = int(request.form.get("thread_id", "0") or 0)
        except ValueError:
            thread_id = 0
        if thread_id:
            thr = db.execute("SELECT id, board_id FROM threads WHERE id=?", (thread_id,)).fetchone()
            if not thr or thr['board_id'] != section_id:
                thread_id = 0
        if not thread_id:
            thr = db.execute(
                "SELECT id FROM threads WHERE slug=?",
                ('s%d-default' % section_id,)).fetchone()
            thread_id = thr['id'] if thr else 0
        cursor = db.execute(
            "INSERT INTO posts (user_id, username, title, content, summary, likes, is_pinned, section_id, "
            "thread_id, view_count, comment_count, last_reply_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?)",
            (user['id'], user['username'], filtered_title, filtered_content, summary, 0, 0,
             section_id, thread_id, now, now, now),
        )
        post_id = cursor.lastrowid
        db.commit()
        _topics.on_post_created(post_id, thread_id)
        # 053 审核：② cold 同步 + ③ Qwen3Guard 异步入队（绝不挡发布）
        try:
            _moderation.submit(db, "post", post_id, user['id'], filtered_title, filtered_content)
        except Exception:
            pass
        # 029：用户点选了就用选的；留空从标题自动提取（冷启动让标签体系有数据）
        tag_names = _topics.parse_tags(request.form.get("tags")) \
            or _topics.auto_tags(filtered_title)
        _topics.set_post_tags(post_id, tag_names)
        # 053：用户没选标签 → AI 打标签（异步，追加不覆盖）
        if not _topics.parse_tags(request.form.get("tags")):
            try:
                _moderation.enqueue_tag(db, post_id, filtered_title, filtered_content)
            except Exception:
                pass
        add_exp(user['id'], 5)
        task_inc(user['id'], 'daily_post')
        task_inc(user['id'], 'newbie_first_post')
        behavior_points(user['id'], 'post')
        _track(user['id'], "post", "post", post_id)   # 044/046 埋点
        _topics.notify_thread_subs(thread_id, post_id, filtered_title, user['id'])  # 030 订阅未读

        if section['is_announcement']:
            broadcast_notification('official', f'📣 官方公告：{filtered_title}', url_for('view_post', post_id=post_id))

        pt = (request.form.get("poll_title") or "").strip()
        popts = [p.strip() for p in request.form.getlist("poll_options") if p.strip()][:12]
        pmode = request.form.get("poll_mode", "single")
        panon = 1 if request.form.get("poll_anonymous") else 0
        pdeadline = 0.0
        pd = (request.form.get("poll_deadline") or "").strip()
        if pd:
            try:
                pdeadline = time.mktime(time.strptime(pd, "%Y-%m-%dT%H:%M"))
            except Exception:
                pdeadline = 0.0
        if pt and len(popts) >= 2:
            if pmode not in ("single", "multi"):
                pmode = "single"
            pc = db.execute(
                "INSERT INTO polls (post_id, title, mode, anonymous, deadline, created_at) VALUES (?,?,?,?,?,?)",
                (post_id, pt, pmode, panon, pdeadline, time.time()))
            pid = pc.lastrowid
            for o in popts:
                db.execute("INSERT INTO poll_options (poll_id, text) VALUES (?,?)", (pid, o))
            db.commit()

        mentions = extract_mentions(filtered_content)
        for mentioned_name in mentions:
            mentioned_user = get_user_by_name(mentioned_name)
            if mentioned_user and mentioned_user['id'] != user['id']:
                blk = db.execute("SELECT 1 FROM blacklist WHERE user_id=? AND blocked_id=?",
                                 (mentioned_user['id'], user['id'])).fetchone()
                if blk:
                    continue
                add_notification(
                    mentioned_user['id'],
                    'mention_post',
                    f'{user["username"]} 在帖子中@了你',
                    url_for('view_post', post_id=post_id)
                )
        db.commit()
        _activity("发帖", (title or "")[:60])
        flash("帖子发布成功", "ok")
        return redirect(url_for("view_post", post_id=post_id))

    try:
        preselect = int(request.args.get("section", "0") or 0)
    except (TypeError, ValueError):
        preselect = 0
    return render_template("edit_post.html", sections=sections, preselect=preselect)


@app.route("/edit-post/<int:post_id>", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        abort(404)
    if post['user_id'] != user['id'] and not user['is_admin']:
        flash("没有权限编辑此帖子", "error")
        return redirect(url_for("forum"))
    
    if post['is_locked'] and not user['is_admin']:
        flash("帖子已被锁定，无法编辑", "error")
        return redirect(url_for("view_post", post_id=post_id))
    sections = db.execute(
        "SELECT * FROM forum_sections WHERE is_hidden=0 ORDER BY sort_order ASC").fetchall()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        summary = (request.form.get("summary") or "").strip()[:200]

        if not title or not content:
            flash("标题和内容不能为空", "error")
            return render_template("edit_post.html", post=post, sections=sections)

        filtered_title, title_err = filter_and_block(title, user, "forum_post")
        if title_err:
            flash(title_err, "error")
            return render_template("edit_post.html", post=post, sections=sections)
        filtered_content, content_err = filter_and_block(content, user, "forum_post")
        if content_err:
            flash(content_err, "error")
            return render_template("edit_post.html", post=post, sections=sections)
        if summary:
            summary, sum_err = filter_and_block(summary, user, "forum_post")
            if sum_err:
                flash(sum_err, "error")
                return render_template("edit_post.html", post=post, sections=sections)

        if not repeat_guard("edit_post", [post_id, filtered_title, filtered_content], uid=user['id']):
            flash("这次的修改已经保存过了", "ok")
            return redirect(url_for("view_post", post_id=post_id))

        db.execute(
            "UPDATE posts SET title = ?, content = ?, summary = ?, edited = 1, updated_at = ? WHERE id = ?",
            (filtered_title, filtered_content, summary, time.time(), post_id)
        )
        db.commit()
        flash("帖子更新成功（已标记编辑）", "ok")
        return redirect(url_for("view_post", post_id=post_id))

    return render_template("edit_post.html", post=post, sections=sections)

@app.route("/delete-post/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("帖子不存在", "error")
        return redirect(url_for("forum"))
    if post['user_id'] != user['id'] and not user['is_admin']:
        flash("没有权限删除此帖子", "error")
        return redirect(url_for("forum"))

    db.execute("UPDATE posts SET recycle = 1 WHERE id = ?", (post_id,))
    db.commit()
    if user['is_admin'] and post['user_id'] != user['id']:
        add_admin_log(user, "post_recycle", post_id, post['username'])
    flash("帖子已移入回收站", "ok")
    return redirect(url_for("forum"))

@app.route("/restore-post/<int:post_id>", methods=["POST"])
@admin_required
def restore_post(post_id):
    user = current_user()
    db = get_db()
    db.execute("UPDATE posts SET recycle = 0 WHERE id = ?", (post_id,))
    db.commit()
    add_admin_log(user, "post_restore", post_id, "")
    flash("帖子已从回收站恢复", "ok")
    return redirect(request.referrer or url_for("admin_recycle"))

@app.route("/purge-post/<int:post_id>", methods=["POST"])
@super_admin_required
def purge_post(post_id):
    """彻底删除（仅超管，仅回收站内帖子）"""
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ? AND recycle = 1", (post_id,)).fetchone()
    if not post:
        flash("仅可彻底删除回收站中的帖子", "error")
        return redirect(request.referrer or url_for("admin_recycle"))
    db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.execute("DELETE FROM post_likes WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM post_comments WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM post_views WHERE post_id = ?", (post_id,))
    db.commit()
    add_admin_log(user, "post_purge", post_id, post['username'])
    flash("帖子已彻底删除", "ok")
    return redirect(request.referrer or url_for("admin_recycle"))

@app.route("/like-post/<int:post_id>", methods=["POST"])
@login_required
def like_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        return jsonify({"error": "帖子不存在"}), 404
    if post['user_id'] == user['id']:
        return jsonify({"error": "不能给自己点赞"}), 400
    
    existing = db.execute("SELECT * FROM post_likes WHERE post_id = ? AND user_id = ?", 
                         (post_id, user['id'])).fetchone()
    if existing:
        db.execute("DELETE FROM post_likes WHERE post_id = ? AND user_id = ?", (post_id, user['id']))
        db.execute("UPDATE posts SET likes = likes - 1 WHERE id = ?", (post_id,))
        liked = False
    else:
        db.execute("INSERT INTO post_likes (post_id, user_id, created_at) VALUES (?,?,?)",
                  (post_id, user['id'], time.time()))
        db.execute("UPDATE posts SET likes = likes + 1 WHERE id = ?", (post_id,))
        liked = True
        add_notification(
            post['user_id'],
            'like',
            f'{user["username"]} 赞了你的帖子',
            url_for('view_post', post_id=post_id)
        )
        add_exp(post['user_id'], 1)
        add_total_likes(post['user_id'], 1)
        # 063：被赞也给作者积分。idem 键带点赞者 id —— 同一个人反复点赞取消刷不了分。
        try:
            award(post['user_id'], 2, 'post_liked', '帖子被赞',
                  ref_id=post_id, idem_key='liked:%d:%d' % (post_id, user['id']))
        except Exception:
            pass
        task_inc(user['id'], 'daily_like')
        behavior_points(user['id'], 'like')
        if liked:
            _track(user['id'], "like", "post", post_id)   # 044/046 埋点

    db.commit()
    new_likes = db.execute("SELECT likes FROM posts WHERE id = ?", (post_id,)).fetchone()['likes']

    author = get_user_by_id(post['user_id'])
    if new_likes >= 10 and author and not author['is_admin'] and not author['has_effect']:
        db.execute("UPDATE users SET has_effect = 1, effect_type = 'popular' WHERE id = ?", (author['id'],))
        db.commit()

    return jsonify({"liked": liked, "likes": new_likes})

@app.route("/comment-post/<int:post_id>", methods=["POST"])
@login_required
def comment_post(post_id):
    user = current_user()
    if is_active_restriction(user["muted_until"]):
        flash("你已被禁言", "error")
        return redirect(url_for("view_post", post_id=post_id))
    
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("评论内容不能为空", "error")
        return redirect(url_for("view_post", post_id=post_id))

    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post or post['recycle']:
        flash("帖子不存在", "error")
        return redirect(url_for("forum"))
    if post['is_locked'] and not user['is_admin']:
        flash("帖子已被锁定，无法评论", "error")
        return redirect(url_for("view_post", post_id=post_id))
    if post['user_id'] != user['id']:
        # 035：黑名单双向拦截 —— 对方拉黑我 / 我拉黑对方，都不能在对方帖子下评论
        blk = db.execute("SELECT 1 FROM blacklist WHERE user_id=? AND blocked_id=?",
                         (post['user_id'], user['id'])).fetchone()
        if blk:
            abort(404)
        blk2 = db.execute("SELECT 1 FROM blacklist WHERE user_id=? AND blocked_id=?",
                          (user['id'], post['user_id'])).fetchone()
        if blk2:
            flash("你已拉黑该用户，取消拉黑后才能评论 TA 的帖子", "error")
            return redirect(url_for("view_post", post_id=post_id))

    filtered, block_err = filter_and_block(content, user, "forum_comment")
    if block_err:
        flash(block_err, "error")
        return redirect(url_for("view_post", post_id=post_id))

    parent_id = request.form.get("parent_id")
    parent = None
    if parent_id:
        parent = db.execute("SELECT * FROM post_comments WHERE id = ? AND post_id = ?",
                            (parent_id, post_id)).fetchone()
        if not parent:
            parent_id = None

    # 031 盖楼两级：回复「楼中楼」时自动提升到其根评论下（结构永远只有两层）
    root_id = None
    if parent:
        if parent['parent_id']:
            # parent 自己是楼中楼 → 把回复挂到它的 root 下，仍通知 parent 作者
            root_id = parent['parent_id']
            parent_id = parent['parent_id']
            parent = db.execute("SELECT * FROM post_comments WHERE id = ?",
                                (root_id,)).fetchone()
        else:
            root_id = parent['id']

    now = time.time()
    if not repeat_guard("post_comment", [post_id, filtered], uid=user['id']):
        flash("这条评论刚刚已经发出去了", "ok")
        return redirect(url_for("view_post", post_id=post_id))
    cursor = db.execute(
        "INSERT INTO post_comments (post_id, user_id, username, content, parent_id, root_id, created_at) VALUES (?,?,?,?,?,?,?)",
        (post_id, user['id'], user['username'], filtered, parent_id or None, root_id, now)
    )
    comment_id = cursor.lastrowid
    db.execute("UPDATE posts SET comment_count = comment_count + 1, last_reply_at = ? WHERE id = ?",
               (now, post_id))
    db.commit()
    # 053 审核：评论同样走 cold + Qwen3Guard（异步，不挡发布）
    try:
        _moderation.submit(db, "comment", comment_id, user['id'], "", filtered)
    except Exception:
        pass
    add_exp(user['id'], 2)
    task_inc(user['id'], 'daily_comment')
    if not _is_water_reply(content):
        behavior_points(user['id'], 'reply')
    _track(user['id'], "comment", "post", post_id)   # 044/046 埋点

    if post['user_id'] != user['id']:
        add_notification(
            post['user_id'],
            'comment',
            f'{user["username"]} 评论了你的帖子',
            url_for('view_post', post_id=post_id)
        )
    if parent and parent['user_id'] not in (user['id'], post['user_id']):
        add_notification(
            parent['user_id'],
            'reply',
            f'{user["username"]} 回复了你的评论',
            url_for('view_post', post_id=post_id)
        )

    mentions = extract_mentions(filtered)
    for mentioned_name in mentions:
        mentioned_user = get_user_by_name(mentioned_name)
        if mentioned_user and mentioned_user['id'] != user['id']:
            blk = db.execute("SELECT 1 FROM blacklist WHERE user_id=? AND blocked_id=?",
                             (mentioned_user['id'], user['id'])).fetchone()
            if blk:
                continue
            add_notification(
                mentioned_user['id'],
                'mention_comment',
                f'{user["username"]} 在评论中@了你',
                url_for('view_post', post_id=post_id)
            )

    return redirect(url_for("view_post", post_id=post_id))

@app.route("/like-comment/<int:comment_id>", methods=["POST"])
@login_required
def like_comment(comment_id):
    user = current_user()
    db = get_db()
    c = db.execute("SELECT * FROM post_comments WHERE id = ?", (comment_id,)).fetchone()
    if not c or c['is_deleted']:
        return jsonify({"error": "评论不存在"}), 404
    existing = db.execute("SELECT 1 FROM comment_likes WHERE comment_id=? AND user_id=?",
                          (comment_id, user['id'])).fetchone()
    if existing:
        db.execute("DELETE FROM comment_likes WHERE comment_id=? AND user_id=?", (comment_id, user['id']))
        db.execute("UPDATE post_comments SET like_count = MAX(like_count - 1, 0) WHERE id = ?", (comment_id,))
        liked = False
    else:
        db.execute("INSERT INTO comment_likes (comment_id, user_id, created_at) VALUES (?,?,?)",
                   (comment_id, user['id'], time.time()))
        db.execute("UPDATE post_comments SET like_count = like_count + 1 WHERE id = ?", (comment_id,))
        liked = True
    db.commit()
    cnt = db.execute("SELECT like_count FROM post_comments WHERE id=?", (comment_id,)).fetchone()['like_count']
    return jsonify({"liked": liked, "likes": cnt})

@app.route("/delete-comment/<int:comment_id>", methods=["POST"])
@login_required
def delete_comment(comment_id):
    user = current_user()
    db = get_db()
    c = db.execute("SELECT * FROM post_comments WHERE id = ?", (comment_id,)).fetchone()
    if not c:
        flash("评论不存在", "error")
        return redirect(request.referrer or url_for("forum"))
    if c['user_id'] != user['id'] and not user['is_admin']:
        flash("没有权限删除该评论", "error")
        return redirect(url_for("view_post", post_id=c['post_id']))
    db.execute("UPDATE post_comments SET is_deleted = 1, content = '[该评论已删除]' WHERE id = ?", (comment_id,))
    db.execute("UPDATE posts SET comment_count = MAX(comment_count - 1, 0) WHERE id = ?", (c['post_id'],))
    db.commit()
    if user['is_admin'] and c['user_id'] != user['id']:
        add_admin_log(user, "comment_delete", comment_id, c['username'])
    flash("评论已删除", "ok")
    return redirect(url_for("view_post", post_id=c['post_id']))

@app.route("/edit-comment/<int:comment_id>", methods=["POST"])
@login_required
def edit_comment(comment_id):
    """编辑评论：本人或管理员/超管。"""
    user = current_user()
    db = get_db()
    c = db.execute("SELECT * FROM post_comments WHERE id = ?", (comment_id,)).fetchone()
    if not c:
        return jsonify({"error": "评论不存在"}), 404
    if c['is_deleted']:
        return jsonify({"error": "评论已删除，无法编辑"}), 403
    if c['user_id'] != user['id'] and not (user['is_admin'] or user['is_super_admin']):
        return jsonify({"error": "没有权限编辑该评论"}), 403
    new_content = (request.form.get("content") or "").strip()
    if not new_content:
        return jsonify({"error": "内容不能为空"}), 400
    if len(new_content) > 2000:
        return jsonify({"error": "内容过长"}), 400
    filtered, block_err = filter_and_block(new_content, user, "comment")
    if block_err:
        return jsonify({"error": block_err}), 400
    db.execute("UPDATE post_comments SET content = ?, edited = 1 WHERE id = ?", (filtered, comment_id))
    db.commit()
    if user['is_admin'] and c['user_id'] != user['id']:
        add_admin_log(user, "comment_edit", comment_id, c['username'])
    return jsonify({"ok": True})

@app.route("/pin-post/<int:post_id>", methods=["POST"])
@admin_required
def pin_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("帖子不存在", "error")
        return redirect(url_for("forum"))

    new_pin = 0 if post['is_pinned'] else 1
    db.execute("UPDATE posts SET is_pinned = ? WHERE id = ?", (new_pin, post_id))
    db.commit()
    add_admin_log(user, "post_pin" if new_pin else "post_unpin", post_id, post['username'])
    flash("已置顶" if new_pin else "已取消置顶", "ok")
    return redirect(request.referrer or url_for("forum"))

@app.route("/essence-post/<int:post_id>", methods=["POST"])
@admin_required
def essence_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("帖子不存在", "error")
        return redirect(url_for("forum"))
    new_val = 0 if post['is_essence'] else 1
    db.execute("UPDATE posts SET is_essence = ? WHERE id = ?", (new_val, post_id))
    db.commit()
    add_admin_log(user, "post_essence" if new_val else "post_unessence", post_id, post['username'])

    # 063：精华奖励 50 分。用 idem_key 幂等 —— 取消再加不会重复发。
    if new_val:
        try:
            award(post['user_id'], 50, 'essence_reward', '帖子被评为精华',
                  ref_id=post_id, idem_key='essence:%d' % post_id)
        except Exception:
            pass

    if new_val and post['user_id'] != user['id']:
        add_notification(post['user_id'], 'essence',
                         '你的帖子《%s》被评为精华' % post['title'][:30],
                         url_for('view_post', post_id=post_id))
    flash("已设为精华" if new_val else "已取消精华", "ok")
    return redirect(request.referrer or url_for("view_post", post_id=post_id))

@app.route("/lock-post/<int:post_id>", methods=["POST"])
@admin_required
def lock_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("帖子不存在", "error")
        return redirect(url_for("forum"))
    new_val = 0 if post['is_locked'] else 1
    db.execute("UPDATE posts SET is_locked = ? WHERE id = ?", (new_val, post_id))
    db.commit()
    add_admin_log(user, "post_lock" if new_val else "post_unlock", post_id, post['username'])
    flash("帖子已锁定（禁止评论/编辑）" if new_val else "帖子已解锁", "ok")
    return redirect(request.referrer or url_for("view_post", post_id=post_id))

@app.route("/move-post/<int:post_id>", methods=["POST"])
@admin_required
def move_post(post_id):
    user = current_user()
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        flash("帖子不存在", "error")
        return redirect(url_for("forum"))
    try:
        target = int(request.form.get("section_id", "0"))
    except ValueError:
        target = 0
    section = db.execute("SELECT * FROM forum_sections WHERE id=?", (target,)).fetchone()
    if not section:
        flash("目标板块不存在", "error")
        return redirect(url_for("view_post", post_id=post_id))
    db.execute("UPDATE posts SET section_id = ? WHERE id = ?", (target, post_id))
    db.commit()
    add_admin_log(user, "post_move", post_id, "-> %s" % section['name'])
    if post['user_id'] != user['id']:
        add_notification(post['user_id'], 'move',
                         '你的帖子《%s》已被移动到「%s」' % (post['title'][:30], section['name']),
                         url_for('view_post', post_id=post_id))
    flash("已移动到「%s」" % section['name'], "ok")
    return redirect(request.referrer or url_for("view_post", post_id=post_id))

@app.route("/become-admin", methods=["GET", "POST"])
@login_required
def become_admin():
    user = current_user()
    if user["is_super_admin"]:
        flash("你已经是超级管理员了", "ok")
        return redirect(url_for("admin"))
    if request.method == "POST":
        if not admin_secret.available():
            flash("本实例没有配置管理员通行证（MONO_ADMIN_SALT / MONO_ADMIN_HASH）", "error")
            return render_template("become_admin.html")
        passcode = request.form.get("passcode") or ""
        if admin_secret.verify_admin_passcode(passcode):
            db = get_db()
            db.execute("UPDATE users SET is_admin = 1, is_super_admin = 1 WHERE id = ?", (user["id"],))
            db.commit()
            add_admin_log(user, "become_super", user["username"], "通过通行证成为超级管理员")
            flash("恭喜，你已获得超级管理员权限", "ok")
            return redirect(url_for("admin"))
        else:
            flash("管理员通行证错误", "error")
    return render_template("become_admin.html")

REPORT_CATEGORIES = ["广告引流", "辱骂引战", "违规内容", "刷屏骚扰", "恶意文件", "其他违规"]

@app.route("/my-punishments")
@login_required
def my_punishments():
    user = current_user()
    db = get_db()
    records = db.execute(
        "SELECT * FROM punishment_records WHERE user_id = ? ORDER BY id DESC", (user['id'],)
    ).fetchall()
    appealed = {r['punishment_id'] for r in db.execute(
        "SELECT punishment_id FROM appeals WHERE user_id = ?", (user['id'],)).fetchall()}
    return render_template("my_punishments.html",
                          records=records, appealed=appealed, REPORT_CATEGORIES=REPORT_CATEGORIES)

@app.route("/appeal/new/<int:pid>", methods=["POST"])
@login_required
def appeal_new(pid):
    me = current_user()
    db = get_db()
    rec = db.execute("SELECT * FROM punishment_records WHERE id = ? AND user_id = ?",
                     (pid, me['id'])).fetchone()
    if not rec:
        flash("处罚记录不存在或不属于你", "error")
        return redirect(url_for("my_punishments"))
    if rec['status'] != 'effective':
        flash("该处罚已解除/撤销，无法申诉", "warn")
        return redirect(url_for("my_punishments"))
    existing = db.execute("SELECT * FROM appeals WHERE punishment_id = ? AND user_id = ?",
                         (pid, me['id'])).fetchone()
    if existing:
        flash("你已对该处罚发起过申诉", "warn")
        return redirect(url_for("my_punishments"))
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("申诉理由不能为空", "error")
        return redirect(url_for("my_punishments"))
    screenshot = None
    if 'screenshot' in request.files:
        f = request.files['screenshot']
        if f and f.filename != '' and allowed_file(f.filename) and is_image(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            fn = f"appeal_{me['id']}_{pid}_{int(time.time())}.{ext}"
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
            screenshot = url_for('uploaded_file', filename=fn)
    db.execute(
        "INSERT INTO appeals (punishment_id, user_id, username, reason, screenshot, status, created_at) "
        "VALUES (?,?,?,?,?, 'pending', ?)",
        (pid, me['id'], me['username'], reason, screenshot, time.time())
    )
    db.commit()
    sup = db.execute("SELECT id FROM users WHERE is_super_admin = 1 LIMIT 1").fetchone()
    if sup:
        add_notification(sup['id'], 'appeal_new',
                         f'{me["username"]} 提交了一份处罚申诉，等待终审', url_for('admin_appeals'))
    flash("申诉已提交，等待超级管理员终审", "ok")
    return redirect(url_for("my_punishments"))

# 2026-11-02：可举报对象从 4 种扩到 7 种。
# 加一种类型 = 在这里加名字 + 下面加一段快照查询，别的地方不用动。
REPORT_TARGETS = ("chat", "private", "post", "comment", "user", "ai", "music")


def _report_snapshot(db, target_type, target_id):
    """取被举报对象的文字快照。

    为什么要存快照：举报处理是异步的，等管理员看到时原文可能已经删了/改了，
    没有快照就只能凭举报人一句话判断。
    """
    if target_type in ("chat", "private"):
        m = db.execute("SELECT content, username FROM messages WHERE id = ?", (target_id,)).fetchone()
        return ("[%s] %s" % (m['username'], m['content'])) if m else ""
    if target_type == "post":
        p = db.execute("SELECT title, content, username FROM posts WHERE id = ?", (target_id,)).fetchone()
        return ("[%s] %s | %s" % (p['username'], p['title'], (p['content'] or "")[:200])) if p else ""
    if target_type == "comment":
        c = db.execute("SELECT content, username FROM post_comments WHERE id = ?", (target_id,)).fetchone()
        return ("[%s] %s" % (c['username'], c['content'])) if c else ""
    if target_type == "user":
        u = db.execute("SELECT id, username, COALESCE(level,0) lv, COALESCE(coins,0) co "
                       "FROM users WHERE id = ?", (target_id,)).fetchone()
        return ("[用户] %s（Lv.%s / %s 分）" % (u['username'], u['lv'], u['co'])) if u else ""
    if target_type == "ai":
        a = db.execute("SELECT role, content FROM ai_square_msgs WHERE id = ?", (target_id,)).fetchone()
        return ("[AI 广场 · %s] %s" % (a['role'], (a['content'] or "")[:200])) if a else ""
    if target_type == "music":
        t = db.execute("SELECT title, artist, username FROM music_tracks WHERE id = ?",
                       (target_id,)).fetchone()
        return ("[音乐 · %s] %s - %s" % (t['username'], t['title'], t['artist'])) if t else ""
    return ""


@app.route("/report/<target_type>/<int:target_id>", methods=["POST"])
@login_required
def submit_report(target_type, target_id):
    me = current_user()
    if target_type not in REPORT_TARGETS:
        flash("不支持的举报类型", "error")
        return redirect(request.referrer or url_for("chat"))
    category = (request.form.get("category") or "其他违规")
    if category not in REPORT_CATEGORIES:
        category = "其他违规"
    reason = (request.form.get("reason") or "").strip()[:200]
    db = get_db()
    # 这一列是后加的：原来代码读了 reason，但表里没有这列、也没写库，
    # 用户辛辛苦苦填的理由等于白填。
    _add_column(db, "reports", "reason", "TEXT DEFAULT ''")
    # 防重复：同一个人对同一个对象，在管理员处理完之前只能举报一次。
    # 否则一个人连点十下就能把后台刷满。
    dup = db.execute(
        "SELECT id FROM reports WHERE reporter_id=? AND target_type=? AND target_id=? "
        "AND status='pending' LIMIT 1", (me['id'], target_type, target_id)).fetchone()
    if dup:
        flash("你已经举报过了，管理员还没处理完，不用重复提交", "warn")
        return redirect(request.referrer or url_for("chat"))
    snapshot = _report_snapshot(db, target_type, target_id)
    if not snapshot:
        flash("举报对象不存在", "error")
        return redirect(request.referrer or url_for("chat"))
    db.execute(
        "INSERT INTO reports (reporter_id, reporter_name, target_type, target_id, snapshot, "
        "category, reason, status, created_at) VALUES (?,?,?,?,?,?,?,'pending',?)",
        (me['id'], me['username'], target_type, target_id, snapshot, category, reason, time.time())
    )
    db.commit()
    flash("举报已提交，管理员将尽快处理", "ok")
    return redirect(request.referrer or url_for("chat"))

ROOM_ONLINE = {}
SID_INFO = {}
SIO_IP = {}
MSG_RATE = {}
PRESENCE = {}
_MSG_RATE_LOCK = threading.Lock()

def get_room(rid):
    try:
        rid = int(rid)
    except Exception:
        return None
    return get_db().execute("SELECT * FROM chat_rooms WHERE id=?", (rid,)).fetchone()

def role_of(u):
    if not u:
        return "guest"
    if u['is_super_admin']:
        return "super"
    if u['is_admin']:
        return "admin"
    return "user"

def online_list(rid):
    db = get_db()
    out = []
    for uid in ROOM_ONLINE.get(rid, set()):
        u = db.execute("SELECT id,username,is_admin,is_super_admin FROM users WHERE id=?", (uid,)).fetchone()
        if u:
            out.append({"user_id": u['id'], "username": u['username'],
                        "role": "super" if u['is_super_admin'] else ("admin" if u['is_admin'] else "user")})
    out.sort(key=lambda x: (x['role'] != "super", x['role'] != "admin", x['username']))
    return out

def broadcast_online(rid):
    socketio.emit("online_update", {"room_id": rid, "online": online_list(rid)}, room=rid)

def parse_mentions(text):
    if not text:
        return []
    return re.findall(r'@([^\s@]{1,20})', text)

def rate_allow(user_id):
    cnt = int(get_config("rate_limit_count", "10"))
    win = int(get_config("rate_limit_window", "30"))
    now = time.time()
    with _MSG_RATE_LOCK:
        lst = [t for t in MSG_RATE.get(user_id, []) if now - t < win]
        MSG_RATE[user_id] = lst
        if len(lst) >= cnt:
            _log_mod.event("rate_block", uid=user_id, kind="msg", cnt=len(lst))
            return False
        MSG_RATE[user_id].append(now)
    return True

def serialize_msg(m, quote=None, mentions=None, ucache=None):
    """序列化聊天消息，附带发送者展示信息（头像/特效/牌子/管理员标识）"""
    uid = m['user_id']
    if ucache is not None and uid in ucache:
        u = ucache[uid]
    else:
        u = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if ucache is not None:
            ucache[uid] = u
    # 徽章：管理员牌子 / 商城头衔 / 商城徽章是 users 表里**三列独立数据**，
    # 各渲染各的（同时显示）。以前这里用 get_user_badge() 只取「优先级最高的那一个」，
    # 另外两个就永远显示不出来 ✗ 而且还得专门把商城头衔屏蔽掉 ✗ 现在不需要了。
    return {
        "id": m['id'], "user_id": m['user_id'], "username": m['username'],
        "content": m['content'], "file_url": m['file_url'], "file_name": m['file_name'],
        "file_type": m['file_type'], "room_id": (m['room_id'] if 'room_id' in m.keys() else 0),
        "is_deleted": m['is_deleted'], "created_at": m['created_at'],
        "quote": quote, "mentions": mentions or [],
        "avatar": get_avatar_url(u),
        "effect": get_user_effect(u),
        "badge_text": _row_get(u, 'badge_text', '') or None,
        "badge_effect": _row_get(u, 'badge_effect', '') or None,
        "mall_title": _row_get(u, 'mall_title', '') or None,
        "mall_title_color": (TITLE_COLORS.get(_row_get(u, 'mall_title', '')) if _row_get(u, 'mall_title', '') else None),
        "mall_badge": _row_get(u, 'mall_badge', '') or None,
        "bubble_style": u['bubble_style'] if u else None,
        "name_color": u['custom_name_color'] if u else None,
        "is_admin": bool(u['is_admin']) if u else False,
    }

SIO_IP = {}
PRESENCE_TTL = 90
_ONLINE_CACHE = {"at": 0, "list": []}


def _online_now():
    """全站在线用户。复用 PRESENCE（心跳维护），带宽限期防刷新误判。"""
    now = time.time()
    if now - _ONLINE_CACHE["at"] < 20:
        return _ONLINE_CACHE["list"]
    live = {uid for uid, ts in list(PRESENCE.items()) if now - ts <= PRESENCE_TTL}
    out = []
    if live:
        try:
            qs = ",".join("?" * len(live))
            rows = get_db().execute(
                "SELECT id, username, avatar, is_admin, is_super_admin FROM users "
                "WHERE id IN (%s) AND show_online!=0" % qs, tuple(live)).fetchall()
            out = [{"id": r["id"], "username": r["username"],
                    "avatar": r["avatar"],
                    "role": ("super" if r["is_super_admin"]
                             else ("admin" if r["is_admin"] else "user"))}
                   for r in rows]
            out.sort(key=lambda x: (x["role"] != "super", x["role"] != "admin", x["username"]))
        except Exception:
            out = []
    _ONLINE_CACHE["at"] = now
    _ONLINE_CACHE["list"] = out
    return out


@app.route("/api/online")
@login_required
def api_online():
    lst = _online_now()
    return {"ok": True, "count": len(lst), "online": lst[:60]}


@socketio.on('connect')
def p2_connect():
    """连接时让已登录用户加入个人房间，用于实时推送通知/私信。"""
    try:
        ip = _guard.client_ip(request)
        ok, why = _guard.sio_accept(ip)
        if not ok:
            _log_mod.event("sio_reject", ip=ip, why=why)
            _monitor.note_attack("sio_reject", ip, "/socket.io/",
                                 "长连接被拒（%s）" % why)
            return False
        SIO_IP[request.sid] = ip
    except Exception:
        pass
    user = current_user()
    if user:
        join_room('user_%d' % user['id'], request.sid)
        PRESENCE[user['id']] = time.time()
        _ONLINE_CACHE["at"] = 0
        try:
            socketio.emit("presence_update",
                          {"count": len(_online_now())}, broadcast=True)
        except Exception:
            pass

        # 051：连接建立后补推离线期间发给他的未读私信（断线重连不错过）
        try:
            chat.IM_STATE.backfill_private(user['id'])
        except Exception:
            pass

def _game_socket_join(tbl, tag, data):
    """黑白棋/国际象棋房间实时订阅（校验身份 + 房间权限）。"""
    user = current_user()
    if not user:
        return
    data = data or {}
    try:
        rid = int(data.get('rid'))
    except (TypeError, ValueError):
        return
    room = get_db().execute("SELECT * FROM %s WHERE id=?" % tbl, (rid,)).fetchone()
    if not room:
        return
    is_owner = room['owner_id'] == user['id']
    is_guest = room['guest_id'] is not None and room['guest_id'] == user['id']
    if not (is_owner or is_guest) and room['status'] != 'waiting':
        return
    join_room('%s_%d' % (tag, rid), request.sid)


@socketio.on('join')
def p2_join(data):
    user = current_user()
    if not user:
        return
    room_name = data.get('room')
    if isinstance(room_name, str) and room_name.startswith('group_'):
        sid = request.sid
        prev = SID_INFO.pop(sid, None)
        if prev:
            try: leave_room(prev['room_id'], sid)
            except Exception: pass
        join_room(room_name, sid)
        SID_INFO[sid] = {"user_id": user['id'], "username": user['username'], "room_id": room_name}
        return
    room = get_room(data.get('room_id', 1)) or get_room(1)
    if not room:
        return
    rid = room['id']
    sid = request.sid
    prev = SID_INFO.pop(sid, None)
    if prev:
        try: leave_room(prev['room_id'], sid)
        except Exception: pass
        ROOM_ONLINE.setdefault(prev['room_id'], set()).discard(prev['user_id'])
        broadcast_online(prev['room_id'])
    join_room(rid, sid)
    SID_INFO[sid] = {"user_id": user['id'], "username": user['username'], "room_id": rid}
    ROOM_ONLINE.setdefault(rid, set()).add(user['id'])
    PRESENCE[user['id']] = time.time()
    emit("joined", {"room_id": rid, "online": online_list(rid)})
    broadcast_online(rid)

@socketio.on('leave')
def p2_leave(data=None):
    sid = request.sid
    info = SID_INFO.pop(sid, None)
    if info:
        try: leave_room(info['room_id'], sid)
        except Exception: pass
        ROOM_ONLINE.setdefault(info['room_id'], set()).discard(info['user_id'])
        broadcast_online(info['room_id'])

@socketio.on('heartbeat')
def p2_heartbeat():
    user = current_user()
    if (user):
        PRESENCE[user['id']] = time.time()
        update_user_active()

@socketio.on('group_send')
def group_send(data):
    def _fail(m):
        emit("error", {"msg": m, "from": "group_send"})
        return {"ok": False, "error": m}

    user = current_user()
    if not user:
        return _fail("请先登录")
    _ok, _wait = flood_check('group', user['id'])
    if not _ok:
        return _fail("发言太快了，请等 %d 秒后再发" % _wait)
    try:
        gid = int(data.get('group_id') or 0)
    except (ValueError, TypeError):
        return _fail("参数错误")
    g = get_group(gid)
    if not g:
        return _fail("群组不存在")
    if is_active_restriction(user['banned_until']):
        return _fail("你已被封禁")
    if is_active_restriction(user['muted_until']):
        return _fail("你被全站禁言：" + status_text(user['muted_until']))
    ok, err = _group_can_send(gid, user['id'])
    if not ok:
        return _fail(err)
    content = (data.get('content') or '').strip()
    file_url = data.get('file_url') or None
    file_name = data.get('file_name') or None
    file_type = data.get('file_type') or None
    if file_url and not g['allow_file']:
        file_url = file_name = file_type = None
    if not content and not file_url:
        return _fail("消息不能为空")
    if not user['is_admin']:
        if content and re.fullmatch(r'[\s\W_]+', content):
            return _fail("请勿发送无意义内容")
        if not rate_allow(user['id']):
            return _fail("发言过于频繁，请稍后再试")
    final = content[:1000] if content else ''
    if final:
        final, block_err = filter_and_block(final, user, 'group')
        if block_err:
            return _fail(block_err)
    db = get_db()
    cur = db.execute(
        "INSERT INTO group_messages (group_id, user_id, username, content, file_url, file_name, file_type, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (gid, user['id'], user['username'], final, file_url, file_name, file_type, time.time()))
    mid = cur.lastrowid
    db.commit()
    msg = db.execute("SELECT * FROM group_messages WHERE id=?", (mid,)).fetchone()
    mentioned = []
    for nick in set(parse_mentions(final)):
        tu = get_user_by_name(nick)
        if not tu:
            continue
        blk = db.execute("SELECT 1 FROM blacklist WHERE user_id=? AND blocked_id=?",
                         (tu['id'], user['id'])).fetchone()
        if blk:
            continue
        if group_role(gid, tu['id']):
            mentioned.append(tu['username'])
            add_notification(tu['id'], 'mention',
                            '%s 在群「%s」@了你' % (user['username'], g['name']),
                            url_for('group_page', gid=gid))
    if '@全体成员' in final or '@all' in final or '@所有人' in final:
        members = db.execute("SELECT user_id FROM group_members WHERE group_id=? AND role!='black'",
                             (gid,)).fetchall()
        for mm in members:
            if mm['user_id'] == user['id']:
                continue
            add_notification(mm['user_id'], 'mention',
                            '%s 在群「%s」@了全体成员' % (user['username'], g['name']),
                            url_for('group_page', gid=gid))
    db.commit()
    payload = serialize_msg(msg, quote=None, mentions=mentioned)
    task_inc(user['id'], 'daily_chat')
    behavior_points(user['id'], 'chat')
    socketio.emit("group_new_message", payload, room='group_%d' % gid)
    return {"ok": True, "msg": payload}

@app.route("/api/group/<int:gid>/messages")
@login_required
def api_group_messages(gid):
    me = current_user()
    if not group_role(gid, me['id']):
        return jsonify({"error": "无权限"}), 403
    db = get_db()
    limit = min(int(request.args.get("limit", "50")), 100)
    after = int(request.args.get("after", "0"))
    if after > 0:
        rows = db.execute(
            "SELECT * FROM group_messages WHERE group_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (gid, after, limit)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM group_messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
            (gid, limit)).fetchall()[::-1]
    ucache = {}
    return jsonify([serialize_msg(m, quote=None, mentions=None, ucache=ucache) for m in rows])

@socketio.on('disconnect')
def p2_disconnect():
    sid = request.sid
    try:
        _ip = SIO_IP.pop(sid, None)
        if _ip:
            _guard.sio_release(_ip)
    except Exception:
        pass
    info = SID_INFO.pop(sid, None)
    if info:
        ROOM_ONLINE.setdefault(info['room_id'], set()).discard(info['user_id'])
        broadcast_online(info['room_id'])
    try:
        u = current_user()
        if u and not socketio.server.manager.has_room("user_%d" % u['id']):
            PRESENCE.pop(u['id'], None)
            _ONLINE_CACHE["at"] = 0
            socketio.emit("presence_update",
                          {"count": len(_online_now())}, broadcast=True)
    except Exception:
        pass

@app.route("/api/achievements")
@login_required
def api_achievements():
    """成就进度。首页只取「最接近达成的几个」，隐藏成就解锁前不返回。

    limit=0 返回全部（个人主页用）。数据复用 get_tasks_data，不另起统计口径。
    """
    uid = current_user()['id']
    try:
        limit = max(0, min(int(request.args.get('limit', 4)), 50))
    except (TypeError, ValueError):
        limit = 4

    all_t = get_tasks_data(uid)
    ach = [t for t in all_t if t['group'] == 'achieve']

    icons = {
        'ach_sign_7': '📅', 'ach_sign_30': '📅', 'ach_sign_100': '👑',
        'ach_sign_streak30': '🔥',
        'ach_post_10': '📚', 'ach_post_50': '🌊', 'ach_comment_50': '🗨️',
        'ach_like_100': '🌟', 'ach_fav_20': '⭐', 'ach_view_100': '👀',
        'ach_friend_5': '👥', 'ach_dm_50': '✉️', 'ach_group_join': '🚪',
        'ach_group_post': '💬',
        'ach_game_any': '🎮', 'ach_game_5': '🕹️', 'ach_game_win': '🏆',
        'ach_sudoku': '🔢', 'ach_klotski': '🧩', 'ach_hanoi3': '🗼',
        'ach_xqm_win': '♟️', 'ach_xq_puzzle': '🎯',
        'ach_hidden_night': '🌙', 'ach_hidden_early': '🌅',
        'ach_hidden_og': '🎖️', 'ach_hidden_all': '👑', 'ach_hidden_speed': '⚡',
    }

    items = []
    hidden_locked = 0
    for t in ach:
        d = ACH_DEFS.get(t['key'])
        hidden = bool(d and d[5])
        if hidden and not t['claimed']:
            hidden_locked += 1
            continue          # 隐藏成就没解锁就不出现
        pct = int(t['progress'] * 100 / t['target']) if t['target'] else 0
        items.append({
            'key': t['key'], 'name': t['name'], 'desc': t['desc'],
            'icon': icons.get(t['key'], '🎯'),
            'progress': t['progress'], 'target': t['target'],
            'reward': t['reward'], 'claimed': t['claimed'],
            'hidden': hidden,
            'ready': (not t['claimed'] and t['progress'] >= t['target']),
            'pct': min(100, pct),
        })

    done = [i for i in items if i['claimed']]
    ready = [i for i in items if i['ready']]
    doing = [i for i in items if not i['claimed'] and not i['ready']]

    # 优先级：可领取 > 进度百分比高 > 进度绝对值高
    doing.sort(key=lambda x: (x['pct'], x['progress']), reverse=True)
    ordered = ready + doing + done

    total_all = len(ach)
    return {
        "ok": True,
        "total": total_all,
        "done": len(done),
        "ready": len(ready),
        "hidden_locked": hidden_locked,
        "items": ordered[:limit] if limit else ordered,
        "all": ordered,
    }


@app.route("/leaderboard")
@login_required
def leaderboard_page():
    return render_template("leaderboard.html", user=current_user(), active="games")


@app.route("/api/music/play", methods=["POST"])
@login_required
def api_music_play():
    """前端上报一次音乐播放。played_ms 是实际听了多久，不是歌曲总长。

    一次听 10 秒就退出也算「听了一会儿」—— 统计听多久必须用真实播放时长，
    否则用 duration 汇总出来的「人均收听时长」全是假的。
    """
    me = current_user()
    data = request.get_json(silent=True) or {}
    tid = int(data.get('track_id') or 0)
    if tid <= 0:
        return jsonify({"ok": False, "msg": "缺少 track_id"}), 400
    ms = max(0, min(int(data.get('played_ms') or 0), 6 * 3600 * 1000))
    if ms < 3000:
        return jsonify({"ok": True, "skipped": 1})     # 不足 3 秒不记，避免误触
    tr = None
    try:
        tr = get_db().execute(
            "SELECT title, artist FROM music_tracks WHERE id=?", (tid,)).fetchone()
    except Exception:
        pass
    try:
        get_db().execute(
            "INSERT INTO music_play_logs (user_id, track_id, title, artist, played_ms, finished, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (me['id'], tid,
             (tr['title'] if tr else (data.get('title') or '')),
             (tr['artist'] if tr else (data.get('artist') or '')),
             ms, 1 if data.get('finished') else 0, time.time()))
        get_db().commit()
    except Exception:
        return jsonify({"ok": False, "msg": "记录失败"}), 500
    return jsonify({"ok": True})


@app.route("/api/leaderboard/all")
@login_required
def api_leaderboard_all():
    """跨游戏全局排行榜：把每个用户每款游戏的最好成绩拉出来，按分数统一排名。

    时间类项目（华容道/扫雷/汉诺塔）分数越小越好，单独归到「速通榜」，
    避免和分数制混排 —— 秒数混进分数榜里毫无意义。
    """
    db = get_db()
    uid = current_user()['id']
    cols = set(r[1] for r in db.execute("PRAGMA table_info(users)").fetchall())
    sc_fields = [(f, n) for f, n in _GAME_SCORE_ROWS if f in cols]
    tm_fields = [(f, n) for f, n in _GAME_TIME_ROWS if f in cols]

    # 不同游戏量纲不同（分数 vs 秒数），不做跨游戏归一化 —— 加权平均没有公信力。
    score_rows = []
    for field, name in sc_fields:
        rows = db.execute(
            "SELECT id, username, %s AS sc FROM users WHERE IFNULL(%s,0)>0 "
            "ORDER BY %s DESC LIMIT 5" % (field, field, field)).fetchall()
        for r in rows:
            score_rows.append({
                "user_id": r['id'], "username": r['username'],
                "game": name, "field": field, "score": r['sc'], "lower_better": 0,
                "mine": r['id'] == uid,
            })

    time_rows = []
    for field, name in tm_fields:
        rows = db.execute(
            "SELECT id, username, %s AS sc FROM users WHERE IFNULL(%s,0)>0 "
            "ORDER BY %s ASC LIMIT 5" % (field, field, field)).fetchall()
        for r in rows:
            time_rows.append({
                "user_id": r['id'], "username": r['username'],
                "game": name, "field": field, "ms": r['sc'], "lower_better": 1,
                "display": _fmt_ms(r['sc']), "mine": r['id'] == uid,
            })

    # 我的最好成绩：每款游戏取一条
    mine = []
    me_row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if me_row:
        for field, name in _GAME_SCORE_ROWS:
            v = _row_get(me_row, field, 0) or 0
            if v > 0:
                mine.append({"game": name, "value": v, "display": str(v), "lower_better": 0})
        for field, name in _GAME_TIME_ROWS:
            v = _row_get(me_row, field, 0) or 0
            if v > 0:
                mine.append({"game": name, "value": v, "display": _fmt_ms(v), "lower_better": 1})

    # 我的总排名：在每个项目里排第几
    my_ranks = []
    for field, name in sc_fields:
        mine_v = _row_get(me_row, field, 0) or 0
        if mine_v <= 0:
            continue
        better = db.execute(
            "SELECT COUNT(*) c FROM users WHERE IFNULL(%s,0)>?" % field, (mine_v,)).fetchone()['c']
        total = db.execute(
            "SELECT COUNT(*) c FROM users WHERE IFNULL(%s,0)>0" % field).fetchone()['c']
        if total > 0:
            my_ranks.append({"game": name, "rank": better + 1, "total": total,
                             "display": str(mine_v)})
    for field, name in tm_fields:
        mine_v = _row_get(me_row, field, 0) or 0
        if mine_v <= 0:
            continue
        better = db.execute(
            "SELECT COUNT(*) c FROM users WHERE IFNULL(%s,0)>0 AND %s<?" % (field, field),
            (mine_v,)).fetchone()['c']
        total = db.execute(
            "SELECT COUNT(*) c FROM users WHERE IFNULL(%s,0)>0" % field).fetchone()['c']
        if total > 0:
            my_ranks.append({"game": name, "rank": better + 1, "total": total,
                             "display": _fmt_ms(mine_v)})

    return {
        "ok": True,
        "score_board": score_rows,
        "time_board": time_rows,
        "mine": mine,
        "my_ranks": sorted(my_ranks, key=lambda x: x['rank'])[:8],
        "game_count": len(_GAME_SCORE_ROWS) + len(_GAME_TIME_ROWS),
    }


@app.route("/api/users/mention")
@login_required
def api_users_mention():
    """@ 提及的候选人：按前缀匹配用户名，附带互关/好友优先。"""
    q = (request.args.get("q") or "").strip().lstrip("@")[:20]
    if not q:
        return {"ok": True, "users": []}
    db = get_db()
    me = current_user()
    try:
        rows = db.execute(
            "SELECT id, username, avatar FROM users "
            "WHERE username LIKE ? AND id != ? AND (banned_until IS NULL OR banned_until=0) "
            "ORDER BY (id=?) DESC, LENGTH(username) ASC LIMIT 8",
            ("%" + q + "%", me['id'], me['id'])).fetchall()
    except Exception:
        return {"ok": True, "users": []}
    return {"ok": True, "users": [{"id": r["id"], "username": r["username"],
                                  "avatar": r["avatar"]} for r in rows]}


@app.route("/api/room/<int:rid>/search")
@login_required
def api_room_search(rid):
    """在当前聊天室里搜消息。

    全站搜索只能搜到「聊天室记录」的最近若干条，房间内翻旧账翻不到 —— 这个补上。
    """
    q = (request.args.get("q") or "").strip()[:30]
    if not q:
        return jsonify({"ok": True, "rows": [], "q": ""})
    rows = get_db().execute(
        "SELECT id, username, content, created_at FROM messages "
        "WHERE room_id=? AND is_private=0 AND is_deleted=0 AND content LIKE ? "
        "ORDER BY id DESC LIMIT 40",
        (rid, '%' + q + '%')).fetchall()
    return jsonify({"ok": True, "q": q, "rows": [dict(r) for r in rows]})


@app.route("/api/room/<int:rid>/messages")
@login_required
def api_room_messages(rid):
    room = get_room(rid)
    if not room:
        return jsonify([])
    before = request.args.get("before", "0")
    after = request.args.get("after", "0")
    try:
        limit = min(int(request.args.get("limit", "50")), 100)
    except (ValueError, TypeError):
        limit = 50
    try:
        before = int(before)
    except Exception:
        before = 0
    try:
        after = int(after)
    except Exception:
        after = 0
    db = get_db()
    if after and after > 0:
        rows = db.execute(
            "SELECT * FROM messages WHERE room_id=? AND is_private=0 AND is_deleted=0 AND id>? "
            "ORDER BY id ASC LIMIT ?", (rid, after, limit)).fetchall()
    elif before and before > 0:
        rows = db.execute(
            "SELECT * FROM messages WHERE room_id=? AND is_private=0 AND is_deleted=0 AND id<? "
            "ORDER BY id DESC LIMIT ?", (rid, before, limit)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM messages WHERE room_id=? AND is_private=0 AND is_deleted=0 "
            "ORDER BY id DESC LIMIT ?", (rid, limit)).fetchall()
    rows = list(reversed(rows))
    out = []
    ucache = {}
    for m in rows:
        quote = None
        if m['quote_id']:
            q = db.execute("SELECT id,user_id,username,content,is_deleted FROM messages WHERE id=?",
                         (m['quote_id'],)).fetchone()
            if q:
                if q['is_deleted']:
                    quote = {"id": q['id'], "deleted": True}
                else:
                    quote = {"id": q['id'], "username": q['username'], "snippet": (q['content'] or '')[:80]}
        out.append(serialize_msg(m, quote=quote, ucache=ucache))
    return jsonify(out)

def _compress_video(path, max_side=1280, crf=28):
    """用 ffmpeg 把视频压小（H.264 + AAC，长边限制到 1280）。

    压完返回新的文件路径（统一是 .mp4），没压成返回原路径。
    手机随手拍的视频动辄几十上百 MB，不压的话上传和播放都很吃力。

    和音频压缩一样的原则：**永远 fail-open** ——
    没装 ffmpeg / 抛异常 / 压完更大，一律保留原文件，绝不让压缩失败变成上传失败。
    """
    if not shutil.which("ffmpeg"):
        return path
    base = os.path.splitext(path)[0]
    tmp = base + ".cmp.mp4"
    try:
        r = run_blocking(subprocess.run,     # 视频转码最长 600 秒，绝对不能在事件循环里跑
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", path,
             "-vf", "scale='min(%d,iw)':-2" % max_side,
             "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
             "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart",
             tmp],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        if r.returncode != 0 or not os.path.isfile(tmp):
            return path
        if os.path.getsize(tmp) <= 0 or os.path.getsize(tmp) >= os.path.getsize(path):
            return path
        newpath = base + ".mp4"
        os.replace(tmp, newpath)
        if newpath != path:
            try:
                os.remove(path)
            except Exception:
                pass
        return newpath
    except Exception:
        log.exception("视频压缩失败，保留原文件: %s", path)
        return path
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


@app.route("/points")
@login_required
def points_center():
    me = current_user()
    tab = request.args.get("tab", "sign")
    sign = sign_state(me['id'])
    tasks = get_tasks_data(me['id'])
    mall = get_mall_data(me['id'])
    gacha = get_gacha_data(me['id'])
    coins = get_coins(me['id'])
    consumables = {}
    db = get_db()
    for r in db.execute("SELECT item_key, count FROM user_consumables WHERE user_id=?", (me['id'],)).fetchall():
        consumables[r['item_key']] = r['count']
    log_rows = pg_shadow.read_rows(db,
        "SELECT * FROM points_log WHERE user_id=? ORDER BY id DESC LIMIT 20", (me['id'],))
    logs = [{"delta": r['delta'], "reason": r['reason'],
             "source": SOURCE_LABELS.get(r['source_type'], r['source_type']),
             "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r['created_at']))} for r in log_rows]
    log_total = pg_shadow.read_rows(db,
        "SELECT COUNT(*) c FROM points_log WHERE user_id=?", (me['id'],))[0]['c']
    return render_template("points.html", active="points", tab=tab,
                           sign=sign, tasks=tasks, mall=mall, gacha=gacha,
                           coins=coins, consumables=consumables,
                           logs=logs, log_total=log_total)


@app.route("/api/points/ledger")
@login_required
def api_points_ledger():
    me = current_user()
    page = max(1, int(request.args.get("page", 1)))
    per = 20
    db = get_db()
    total = pg_shadow.read_rows(db,
        "SELECT COUNT(*) c FROM points_log WHERE user_id=?", (me['id'],))[0]['c']
    rows = pg_shadow.read_rows(db,
        "SELECT * FROM points_log WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (me['id'], per, (page - 1) * per))
    logs = [{"delta": r['delta'], "reason": r['reason'],
             "source": SOURCE_LABELS.get(r['source_type'], r['source_type']),
             "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r['created_at']))} for r in rows]
    return jsonify({"ok": True, "logs": logs, "total": total, "page": page, "per": per})


@app.route("/api/sign", methods=["POST"])
@login_required
def api_sign():
    me = current_user()
    ok, msg, state = do_sign(me['id'])
    return jsonify({"ok": ok, "msg": msg, "state": state})


@app.route("/api/sign/makeup", methods=["POST"])
@login_required
def api_sign_makeup():
    me = current_user()
    date = request.form.get("date") or ""
    ok, msg, state = do_sign(me['id'], makeup=True, makeup_date=date)
    return jsonify({"ok": ok, "msg": msg, "state": state})


@app.route("/api/tasks/claim/<key>", methods=["POST"])
@login_required
def api_task_claim(key):
    me = current_user()
    if not repeat_guard("task_claim", [key], uid=me['id']):
        return jsonify({"ok": False, "msg": "奖励已领取，不要重复点"}), 400
    ok, msg = claim_task(me['id'], key)
    if not ok:
        repeat_release("task_claim", [key], uid=me['id'])
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/mall/redeem/<int:item_id>", methods=["POST"])
@login_required
def api_mall_redeem(item_id):
    me = current_user()
    db = get_db()
    item = db.execute("SELECT * FROM mall_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return jsonify({"ok": False, "msg": "商品不存在"}), 404
    if item['value'] == 'makeup_card':
        return jsonify({"ok": False, "msg": "该道具已下架，暂不可购买"}), 400
    is_super = bool(me['is_super_admin'])
    if item['gacha_only'] and not is_super:
        return jsonify({"ok": False, "msg": "该商品仅扭蛋可得"}), 400
    if item['limited'] and not is_super:
        return jsonify({"ok": False, "msg": "限定物品不可购买"}), 400
    if item['type'] == 'bubble' and item['value'] in ('admin', 'super'):
        ok_role = (item['value'] == 'admin' and me['is_admin']) or (item['value'] == 'super' and me['is_super_admin'])
        if not ok_role:
            return jsonify({"ok": False, "msg": "无权限购买"}), 403
    if not is_super and get_coins(me['id']) < item['cost']:
        return jsonify({"ok": False, "msg": "积分不足"}), 400
    if not repeat_guard("mall_redeem", [item_id], window=5.0, uid=me['id']):
        return jsonify({"ok": False, "msg": "正在处理上一次购买，请稍候"}), 400
    if item['type'] == 'consumable':
        if not is_super:
            award(me['id'], -item['cost'], 'mall', "购买「%s」" % item['name'])
        db.execute("INSERT INTO user_consumables (user_id, item_key, count) VALUES (?,?,1) "
                   "ON CONFLICT(user_id, item_key) DO UPDATE SET count = count + 1",
                   (me['id'], item['value']))
        db.commit()
        return jsonify({"ok": True, "msg": "购买成功：%s" % item['name']})
    if db.execute("SELECT 1 FROM user_items WHERE user_id=? AND item_id=?",
                  (me['id'], item_id)).fetchone():
        repeat_release("mall_redeem", [item_id], uid=me['id'])
        return jsonify({"ok": False, "msg": "已拥有该商品"}), 400
    if not is_super:
        award(me['id'], -item['cost'], 'mall', "兑换「%s」" % item['name'])
    db.execute("INSERT INTO user_items (user_id, item_id, redeemed_at, is_equipped) VALUES (?,?,?,0)",
               (me['id'], item_id, time.time()))
    db.commit()
    return jsonify({"ok": True, "msg": "兑换成功：%s" % item['name']})


@app.route("/api/mall/equip/<int:item_id>", methods=["POST"])
@login_required
def api_mall_equip(item_id):
    me = current_user()
    db = get_db()
    item = db.execute("SELECT * FROM mall_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return jsonify({"ok": False, "msg": "商品不存在"}), 404
    if not db.execute("SELECT 1 FROM user_items WHERE user_id=? AND item_id=?",
                      (me['id'], item_id)).fetchone():
        return jsonify({"ok": False, "msg": "尚未拥有"}), 400
    if item['type'] == 'bubble' and item['value'] in ('admin', 'super'):
        ok_role = (item['value'] == 'admin' and me['is_admin']) or (item['value'] == 'super' and me['is_super_admin'])
        if not ok_role:
            return jsonify({"ok": False, "msg": "无权限装备"}), 403
    _mall_equip(me['id'], item_id, item)
    return jsonify({"ok": True, "msg": "已装备"})


@app.route("/api/mall/unequip/<int:item_id>", methods=["POST"])
@login_required
def api_mall_unequip(item_id):
    me = current_user()
    db = get_db()
    item = db.execute("SELECT * FROM mall_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return jsonify({"ok": False, "msg": "商品不存在"}), 404
    if not db.execute("SELECT 1 FROM user_items WHERE user_id=? AND item_id=?",
                      (me['id'], item_id)).fetchone():
        return jsonify({"ok": False, "msg": "尚未拥有"}), 400
    _mall_unequip(me['id'], item)
    return jsonify({"ok": True, "msg": "已卸下"})


@app.route("/api/gacha/pull", methods=["POST"])
@login_required
def api_gacha_pull():
    me = current_user()
    if not repeat_guard("gacha_pull", [], window=3.0, uid=me['id']):
        return jsonify({"ok": False, "msg": "上一次抽奖还在处理，稍等一下再点"}), 400
    rarity, prize = gacha_pull(me['id'])
    if rarity is None:
        repeat_release("gacha_pull", [], uid=me['id'])
        return jsonify({"ok": False, "msg": prize or "抽奖失败"}), 400
    return jsonify({
        "ok": True,
        "rarity": rarity,
        "rarity_label": GACHA_RARITY_LABELS.get(rarity, rarity),
        "prize": prize,
        "coins": get_coins(me['id']),
    })


@app.route("/api/item/use/<key>", methods=["POST"])
@login_required
def api_item_use(key):
    """消耗道具：补签笺/运势重阅/无痕撤回卡/盲盒/标题高亮/置顶卡/传声筒/双倍积分/经验加速/昵称色/护盾"""
    me = current_user()
    if get_consumable_count(me['id'], key) <= 0:
        return jsonify({"ok": False, "msg": "道具数量不足"}), 400
    if not repeat_guard("item_use", [key], window=3.0, uid=me['id']):
        return jsonify({"ok": False, "msg": "上一次使用还在处理，稍等一下再点"}), 400
    db = get_db()
    now = time.time()

    def _consume():
        consume_item(me['id'], key, 1)
        db.execute("INSERT INTO consumable_logs (user_id, item_key, detail, created_at) VALUES (?,?,?,?)",
                   (me['id'], key, "使用", now))
        db.commit()

    if key == 'makeup_card':
        date = (request.form.get("date") or "").strip()
        if not date:
            return jsonify({"ok": False, "msg": "请选择要补签的日期（最近 7 天内，格式 YYYY-MM-DD）"}), 400
        from datetime import date as _d
        try:
            d = _d.fromisoformat(date)
        except Exception:
            return jsonify({"ok": False, "msg": "日期格式错误"}), 400
        delta_days = (_d.fromisoformat(_today_str()) - d).days
        if not (1 <= delta_days <= 7):
            return jsonify({"ok": False, "msg": "只能补签最近 7 天"}), 400
        month_used = db.execute("SELECT COUNT(*) c FROM consumable_logs WHERE user_id=? AND item_key='makeup_card' "
                                "AND substr(datetime(created_at,'unixepoch','localtime'),1,7)=?",
                                (me['id'], _today_str()[:7])).fetchone()['c']
        if month_used >= 5:
            return jsonify({"ok": False, "msg": "本月补签笺已达上限（5 张）"}), 400
        ok, msg, state = do_sign(me['id'], makeup=True, makeup_date=date, free=True, window=7)
        if not ok:
            return jsonify({"ok": False, "msg": msg}), 400
        _consume()
        return jsonify({"ok": True, "msg": "补签成功（免积分）"})
    if key == 'fortune_reroll':
        if not _is_signed_today(me['id']):
            return jsonify({"ok": False, "msg": "请先签到后再重抽运势"}), 400
        new_salt = "%s|%d" % (_today_str(), random.randint(10000, 99999))
        db.execute("UPDATE users SET fortune_salt=? WHERE id=?", (new_salt, me['id']))
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "运势已刷新"})
    if key == 'recall_card':
        return jsonify({"ok": False, "msg": "请到消息界面，对超时消息执行撤回时自动消耗"}), 400
    if key == 'blindbox':
        # 2026-11-01：原来是 random.randint(10, 100)，均值 55 > 券价 30
        # → 每开一个净赚 25 分，可以无限循环印钞（实测 480 次净造出 12715 分，
        #   占全站存量 32825 的 39%）。
        # 改成加权分布：文案「10~100，欧皇开 100」不变，但期望值降到 27 < 30（成本）：
        #   80% → 10~30（均 20） / 18% → 40~60（均 50） / 2% → 100
        #   EV = 0.8×20 + 0.18×50 + 0.02×100 = 27
        _r = random.random()
        if _r < 0.80:
            v = random.randint(10, 30)
        elif _r < 0.98:
            v = random.randint(40, 60)
        else:
            v = 100
        award(me['id'], v, 'blindbox', "盲盒开出 %d 积分" % v)
        _consume()
        return jsonify({"ok": True, "msg": "恭喜！盲盒开出 %d 积分" % v})
    if key == 'title_glow':
        pid = int(request.form.get("post_id") or 0)
        p = db.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (pid, me['id'])).fetchone()
        if not p:
            return jsonify({"ok": False, "msg": "帖子不存在或非你所有"}), 400
        db.execute("UPDATE posts SET title_glow=1 WHERE id=?", (pid,))
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "帖子标题已高亮"})
    if key == 'pin_card':
        pid = int(request.form.get("post_id") or 0)
        p = db.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (pid, me['id'])).fetchone()
        if not p:
            return jsonify({"ok": False, "msg": "帖子不存在或非你所有"}), 400
        db.execute("UPDATE posts SET user_pin_until=? WHERE id=?", (now + 7 * 86400, pid))
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "帖子已置顶 7 天"})
    if key == 'horn':
        content = (request.form.get("content") or "").strip()[:100]
        link = (request.form.get("link") or "").strip()[:200]
        if not content:
            return jsonify({"ok": False, "msg": "广播内容不能为空"}), 400
        last = db.execute("SELECT created_at FROM horn_announcements ORDER BY created_at DESC LIMIT 1").fetchone()
        if last and now - last['created_at'] < 60:
            wait = int(60 - (now - last['created_at']) + 1)
            return jsonify({"ok": False, "msg": "上一条广播还在展示中，请约 %d 秒后再试" % wait}), 400
        day_used = db.execute("SELECT COUNT(*) c FROM consumable_logs WHERE user_id=? AND item_key='horn' "
                              "AND substr(datetime(created_at,'unixepoch','localtime'),1,10)=?",
                              (me['id'], _today_str())).fetchone()['c']
        if day_used >= 5:
            return jsonify({"ok": False, "msg": "今天的传声筒次数已用完（每日 5 次），明天再来吧"}), 400
        db.execute("INSERT INTO horn_announcements (username, content, link, created_at) VALUES (?,?,?,?)",
                   (me['username'], content, link, time.time()))
        db.commit()
        # 实时推给所有在线用户，别让他们靠刷新才看得到
        try:
            socketio.emit('horn', {
                'username': me['username'], 'content': content, 'link': link,
                'created_at': time.time(), 'expires_at': time.time() + 60,
            }, broadcast=True)
        except Exception:
            pass
        _consume()
        return jsonify({"ok": True, "msg": "广播已发送，全站顶部显示 60 秒"})
    if key == 'double_coins_card':
        db.execute("UPDATE users SET buff_double_uses=buff_double_uses+1 WHERE id=?", (me['id'],))
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "已生效：下一次获得的积分翻倍"})
    if key == 'exp_boost':
        db.execute("UPDATE users SET buff_exp_until=? WHERE id=?", (now + 86400, me['id']))
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "已激活 24 小时经验加速"})
    if key == 'name_color_card':
        color = (request.form.get("color") or "").strip()
        if not re.fullmatch(r"^#[0-9a-fA-F]{6}$", color):
            return jsonify({"ok": False, "msg": "颜色格式错误（例：#ff6b6b）"}), 400
        db.execute("UPDATE users SET custom_name_color=? WHERE id=?", (color.lower(), me['id']))
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "昵称颜色已应用"})
    if key == 'rename_card':
        newname = (request.form.get("newname") or "").strip()
        if not (2 <= len(newname) <= 12):
            return jsonify({"ok": False, "msg": "昵称需要 2~12 个字"}), 400
        if not re.fullmatch(r"[\w\u4e00-\u9fa5\-]+", newname):
            return jsonify({"ok": False, "msg": "昵称只能包含中文、字母、数字、下划线和短横线"}), 400
        if newname == me['username']:
            return jsonify({"ok": False, "msg": "新昵称和当前昵称一样"}), 400
        if db.execute("SELECT 1 FROM users WHERE username=? AND id<>?",
                      (newname, me['id'])).fetchone():
            return jsonify({"ok": False, "msg": "这个昵称已经被占用了，换一个吧"}), 400
        _filtered, _bad = filter_and_block(newname, me, "nickname")
        if _bad:
            return jsonify({"ok": False, "msg": _bad}), 400
        old = me['username']
        db.execute("UPDATE users SET username=? WHERE id=?", (newname, me['id']))
        # 历史发言里的昵称是冗余存储的，一起改掉，否则旧帖子会显示一个不存在的名字
        for _t in ("posts", "messages", "group_messages", "post_comments"):
            try:
                db.execute("UPDATE %s SET username=? WHERE user_id=?" % _t, (newname, me['id']))
            except Exception:
                pass
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "改名成功：%s → %s" % (old, newname),
                        "username": newname})

    if key == 'shield_card':
        db.execute("UPDATE users SET shield_until=? WHERE id=?", (now + 86400, me['id']))
        db.commit()
        _consume()
        return jsonify({"ok": True, "msg": "已激活 24 小时护盾"})
    return jsonify({"ok": False, "msg": "暂不支持该道具"}), 400


@app.template_global()
def format_time(sec):
    # 2026-10-31：游客（user=None）时模板会传进 Jinja Undefined，
    # 原来只判 None → int(Undefined) 直接 500。/games 的 500 就是这条。
    if not sec:
        return "--:--"
    try:
        m, s = divmod(int(sec), 60)
    except Exception:
        return "--:--"
    return "%02d:%02d" % (m, s)


@app.template_global()
def format_ms(ms):
    if not ms or ms <= 0:
        return "--:--.--"
    total = float(ms) / 1000.0
    m = int(total // 60)
    s = total - m * 60
    return "%d:%05.2f" % (m, s)


@app.template_filter("jsstr")
def jsstr(s):
    """把值安全地放进 JS 模板字符串（反引号）里。

    Jinja 的 HTML 自动转义管不到反引号和 ${，房间名之类的用户输入
    直接写进 `...` 模板字符串会被逃逸执行。这里补上这三样。
    """
    if s is None:
        return ""
    return (str(s).replace("\\", "\\\\")
                  .replace("`", "\\`")
                  .replace("${", "\\${"))


def _ach_after_submit(uid):
    """成绩入库后跑一次成就扫描（内部全兜异常，绝不影响提交主链路）。"""
    cache.invalidate_game()   # 028：成绩变化 → 榜单缓存失效（放最前，扫描失败也要清）
    try:
        _ach.check(uid, award, add_notification)
    except Exception as e:
        _log_mod.event("ach_check_error", uid=uid, err=str(e)[:120])


_ach.bind_db(get_db)
_topics.bind_db(get_db)
_topics.start_hot_loop(app)   # 029：每日热榜聚合 daemon（90s 后首跑，之后每 24h）
_fts.bind_db(get_db)


def _score_gate(game, data, claimed):
    """游戏成绩复算闸门（games_verify/ 九款全量）。

    - 前端带 seq（输入序列）→ 服务端重放复算，不一致拒绝入库并落事件日志；
    - 前端不带 seq（旧客户端 / 缓存页）→ 放行但打点 legacy_no_seq，灰度期可观测。
    返回 None 放行，返回 (jsonify, status) 拒绝。
    """
    from games_verify import verify_game_result
    seq = data.get('seq') if isinstance(data, dict) else None
    me = None
    try:
        me = current_user()
    except Exception:
        pass
    if not seq:
        _log_mod.event("game_verify_legacy", game=game,
                       uid=(me['id'] if me else ''), claimed=str(claimed)[:20])
        return None
    ok, expected, reason = verify_game_result(game, data, claimed)
    if not ok:
        _log_mod.event("game_verify_fail", game=game,
                       uid=(me['id'] if me else ''), reason=reason,
                       expected=str(expected)[:20], claimed=str(claimed)[:20])
        return (jsonify({"ok": False,
                         "msg": "成绩校验未通过：本局过程与服务端复算不一致"}), 403)
    return None


def _submit_score_common(field, game=None):
    me = current_user()
    if not me:
        return jsonify({"ok": False, "msg": "未登录"}), 401
    data = request.get_json() or {}
    score = int(data.get('score') or 0)
    if score < 0:
        return jsonify({"ok": False, "msg": "无效分数"}), 400
    if game:
        g = _score_gate(game, data, score)
        if g:
            return g
    db = get_db()
    new_best = False
    if score > (me[field] or 0):
        db.execute("UPDATE users SET %s=? WHERE id=?" % field, (score, me['id']))
        db.commit()
        new_best = True
        _ach_after_submit(me['id'])   # 027：破纪录才跑成就扫描（字段没变扫也白扫）
        _track(me['id'], "game", "game", game or field, weight=max(1, score // 100 + 1))  # 044/046 埋点
    rank_list = db.execute(
        "SELECT username, %s AS sc FROM users ORDER BY %s DESC LIMIT 10" % (field, field)).fetchall()
    ranks = [{"username": r['username'], "score": r['sc']} for r in rank_list]
    return jsonify({"ok": True, "new_best": new_best, "rank_list": ranks})


SILENCE_ITEMS = {
    "page1": {"inheritable": False}, "page2": {"inheritable": False},
    "page3": {"inheritable": False}, "page4": {"inheritable": False},
    "page5": {"inheritable": False}, "page6": {"inheritable": True},
    "page7": {"inheritable": True}, "page8": {"inheritable": False},
    "page9": {"inheritable": False}, "page10": {"inheritable": True},
    "page11": {"inheritable": False}, "page12": {"inheritable": True},
    "page13": {"inheritable": False}, "page14": {"inheritable": False},
    "page15": {"inheritable": False}, "page16": {"inheritable": False},
    "page17": {"inheritable": True}, "page18": {"inheritable": True},
    "page19": {"inheritable": False}, "page20": {"inheritable": True},
    "photo1": {"inheritable": False}, "photo2": {"inheritable": False},
    "photo3": {"inheritable": False}, "photo4": {"inheritable": True},
    "photo5": {"inheritable": True}, "photo6": {"inheritable": False},
    "photo7": {"inheritable": False}, "photo8": {"inheritable": True},
    "note1": {"inheritable": False}, "note2": {"inheritable": False},
    "note3": {"inheritable": False}, "note4": {"inheritable": False},
    "note5": {"inheritable": True}, "note6": {"inheritable": False},
    "note7": {"inheritable": False}, "note8": {"inheritable": False},
    "key_hall": {"inheritable": False}, "key_reading": {"inheritable": False},
    "key_archive": {"inheritable": False}, "key_basement": {"inheritable": True},
    "key_safe": {"inheritable": False}, "key_book": {"inheritable": False},
    "key_attic": {"inheritable": True}, "key_mirror": {"inheritable": True},
    "key_final": {"inheritable": True}, "key_fragment1": {"inheritable": True},
    "key_fragment2": {"inheritable": True}, "key_fragment3": {"inheritable": True},
    "key_fragment4": {"inheritable": True}, "key_fragment5": {"inheritable": True},
    "key_tea": {"inheritable": False}, "key_dorm": {"inheritable": False},
    "key_wash": {"inheritable": False}, "key_cell": {"inheritable": False},
    "key_lab": {"inheritable": True}, "key_second_basement": {"inheritable": True},
    "flashlight": {"inheritable": False}, "tweezers": {"inheritable": False},
    "match": {"inheritable": False}, "rope": {"inheritable": False},
    "hammer": {"inheritable": False}, "magnifier": {"inheritable": False},
    "tape": {"inheritable": False}, "knife": {"inheritable": False},
    "battery": {"inheritable": False}, "notebook": {"inheritable": True},
    "pencil": {"inheritable": False}, "compass": {"inheritable": False},
    "screwdriver": {"inheritable": False}, "lubricant": {"inheritable": False},
    "gas_mask": {"inheritable": False}, "candle": {"inheritable": False},
    "watch": {"inheritable": False}, "medicine": {"inheritable": False},
    "silver_coin": {"inheritable": True}, "thread": {"inheritable": False},
    "chalk": {"inheritable": False}, "copper_bookmark": {"inheritable": False},
    "map": {"inheritable": False}, "uniform": {"inheritable": False},
    "memory1": {"inheritable": True}, "memory2": {"inheritable": True},
    "memory3": {"inheritable": True}, "memory4": {"inheritable": True},
    "memory5": {"inheritable": True}, "shadow_book": {"inheritable": True},
    "curse_mark": {"inheritable": True}, "curse_talisman": {"inheritable": True},
    "ink_bottle": {"inheritable": False}, "old_clock_key": {"inheritable": True},
    "fixed_photo": {"inheritable": True},
}


def load_guard_policy():
    """把 site_config 里的 guard_* 覆盖到 mono_guard 的默认策略上。"""
    try:
        rows = get_db().execute(
            "SELECT key, value FROM site_config WHERE key LIKE 'guard\\_%' ESCAPE '\\'"
        ).fetchall()
    except Exception:
        return {}
    kw = {}
    for r in rows:
        k = (r["key"] or "")[len("guard_"):]
        if k:
            kw[k] = r["value"]
    applied = _guard.configure(**kw) if kw else {}
    if applied:
        log.info("访问防护策略已从配置覆盖: %s", applied)
    return applied


try:
    with app.app_context():
        load_guard_policy()
except Exception as _gp_err:
    print("[guard] 策略加载失败（使用默认值）:", _gp_err)


try:
    _monitor.start(BASE_DIR)
except Exception as _mon_err:
    print("[监控] 采集线程启动失败（不影响主业务）:", _mon_err)



GOMOKU_P1_NAME = "黑"
GOMOKU_P2_NAME = "白"

import queue as _queue_mod
import threading as _threading_mod
import subprocess as _subprocess_mod
import atexit as _atexit_mod

GO_P1_NAME = "黑"
GO_P2_NAME = "白"
import json as _json
import requests as _requests
from ai_config import (
    get_ai_key, MODELS, DEFAULT_MODEL,
    model_url, model_extra_body, model_provider, model_label,
    MAX_USER_INPUT_CHAR, MAX_CONTEXT_TOKENS, MAX_OUTPUT_TOKENS,
    RATE_LIMIT_SECONDS, RATE_LIMIT_HOUR_MAX, RATE_LIMIT_DAY_MAX,
    MAX_HISTORY_ROUND, SYSTEM_PROMPT,
    _check_rate_limit, _acquire_concurrent, _release_concurrent,
    estimate_tokens, check_prompt_injection,
    pick_busy_model, model_done,
    CHAT_MODES, DEFAULT_CHAT_MODE, MAX_CHAT_OUTPUT_CHAR, MAX_CHAT_TOKENS, CHAT_POOL, MAX_CHAT_TOKENS,
    DEBUG_MODEL, DEBUG_INPUT_CHAR, DEBUG_OUTPUT_CHAR, DEBUG_INIT_QUOTA,
    DEBUG_SYSTEM_PROMPT, DEBUG_EMPTY_CODE_HINT, count_effective,
)


_ALLOWED_MODELS = set(MODELS.keys())


# ================= AI 广场（2026-10-25 大改：/ai 重做）=================
# 结构：导航「AI 广场」下拉三个标签 —— AI 对话 / 个人中心 / 说明文档。
# 对话 UI：一个输入框 + 右下角模型切换 + 发送。模型池由超管后台配置（page_blocks 存储）。
AI_SQUARE_PAGE = "ai_square"
AI_SQUARE_DEFAULT_CFG = {
    "models": ["kimi-k3.1-pro", "kimi-k3.1-pro-highspeed", "kimi-k3",
                "glm-5.3", "glm-5.3-flash"],
    "default": "kimi-k3.1-pro",
}

# 模型展示信息默认值。价格单位：元 / 百万 tokens（0 = 免费，两项都为 0 显示「限时免费」）
AI_SQUARE_INFO = {
    "glm-5.3": {
        "desc": "旗舰模型，强推理、代码与复杂智能体任务。",
        "price_in": 8, "price_out": 28, "ctx": "-",
    },
    "glm-5.3-flashx": {
        "desc": "极速高并发，承载大规模请求。",
        "price_in": 2, "price_out": 7, "ctx": "1M",
    },
    "glm-5.3-flash": {
        "desc": "均衡极速，支持图文、视频多模态。",
        "price_in": 0.8, "price_out": 2.8, "ctx": "-",
    },
    "glm-5.2": {
        "desc": "长上下文稳定，业务落地稳妥可靠。",
        "price_in": 8, "price_out": 28, "ctx": "1M",
    },
    "glm-5.1": {
        "desc": "擅长长链路 Agent，处理多步骤连续任务。",
        "price_in": 6, "price_out": 24,
        "price_in_hi": 8, "price_out_hi": 28, "hi_threshold": 32000, "ctx": "32K",
    },
    "glm-5-turbo": {
        "desc": "高速低价，通用场景降低调用成本。",
        "price_in": 5, "price_out": 22,
        "price_in_hi": 7, "price_out_hi": 26, "hi_threshold": 32000, "ctx": "32K",
    },
    "glm-4.5-air": {
        "desc": "轻量实惠，适配简单问答与基础文本。",
        "price_in": 0, "price_out": 0, "ctx": "-",
    },
    "glm-ocr": {
        "desc": "文档图片 OCR，识别文字、表格、票据。",
        "price_in": 0.2, "price_out": 0.2, "ctx": "-",
    },
    "glm-z1-air": {
        "desc": "推理模型，适合需要多步思考的问题。",
        "price_in": 0, "price_out": 0, "ctx": "-",
    },
    # 阶跃星辰 Step 家族（价格：输入 / 输出，元/百万 tokens，缓存未命中）
    "step-5-preview": {
        "desc": "阶跃旗舰，百万上下文，图文视频、金融与代码 Agent 强。",
        "price_in": 7, "price_out": 20, "ctx": "1M",
    },
    "step-3.7-flash": {
        "desc": "高速多模态，原生图视频理解，工具调用稳定可靠。",
        "price_in": 1.35, "price_out": 8.1, "ctx": "-",
    },
    "step-3.5-flash": {
        "desc": "高速推理，擅长代码、数学与长链路 Agent 任务。",
        "price_in": 0.7, "price_out": 2.1, "ctx": "-",
    },
    "step-3.5-flash-2603": {
        "desc": "Agent 专项优化，支持推理档位，可控降本提速。",
        "price_in": 0.7, "price_out": 2.1, "ctx": "-",
    },
    # 月之暗面 Kimi K2.x（文本），元/百万 tokens
    "kimi-k2.7-code": {
        "desc": "K2.7 代码专用，面向编程与工程任务的专项优化。",
        "price_in": 6.5, "price_out": 27, "ctx": "-",
    },
    "kimi-k2.7-code-highspeed": {
        "desc": "K2.7 代码专用极速版，低延迟高并发，适合批量代码任务。",
        "price_in": 13, "price_out": 54, "ctx": "-",
    },
    "kimi-k2.6": {
        "desc": "K2.6 通用模型，长上下文与工具调用稳定。",
        "price_in": 6.5, "price_out": 27, "ctx": "-",
    },
    # 月之暗面 Kimi K3 / K3.1（2026-10-31 接入）
    # 官方人民币定价（2026-10-31 官网截图确认）
    "kimi-k3.1-pro": {
        "desc": "K3.1 Pro：2.8 万亿参数旗舰，100 万上下文，代码与长程 Agent 强。",
        "price_in": 13, "price_out": 68, "ctx": "1M",
    },
    "kimi-k3.1-pro-highspeed": {
        "desc": "K3.1 Pro 极速版，同等智能，出字速度约为普通版 2~3 倍。",
        "price_in": 26, "price_out": 136, "ctx": "1M",
    },
    "kimi-k3": {
        "desc": "K3：2.8 万亿参数开源旗舰，100 万上下文，综合能力均衡。",
        "price_in": 20, "price_out": 100, "ctx": "1M",
    },
    # 小米 MiMo-V2.6（全模态：文本/图片/视频/音频），元/百万 tokens
    "mimo-v2.6-pro": {
        "desc": "全模态旗舰，文本/图片/视频/音频理解，深度思考与工具调用。",
        "price_in": 3, "price_out": 6, "ctx": "1M",
    },
    "mimo-v2.6-flash": {
        "desc": "全模态轻量版，快而省，日常问答与多模态理解。",
        "price_in": 1, "price_out": 2, "ctx": "1M",
    },
    "mimo-v2.6-pro-ultraspeed": {
        "desc": "极速版旗舰，低延迟高并发，适合实时交互场景。",
        "price_in": 30, "price_out": 60, "ctx": "1M",
    },
    # DeepSeek-V4.1-Flash：分时计价（空闲 / 高峰），单位元/百万 tokens
    "deepseek-flash": {
        "desc": "DeepSeek-V4.1-Flash：高性价比推理，分时计价（高峰 / 空闲）。",
        "price_in": 1, "price_out": 4,          # 空闲时段
        "peak_in": 2, "peak_out": 8,            # 高峰时段
        "ctx": "128K",
    },
}


def _as_price(v):
    """价格转 float（非法值按 0 处理）。"""
    try:
        return max(0.0, float(str(v).strip() or 0))
    except Exception:
        return 0.0


# DeepSeek 等分时计价模型用的中国法定节假日（北京时间日期，每年需按国务院公告更新）
CN_HOLIDAYS = {
    "2026-01-01", "2026-01-02", "2026-01-03",                                    # 元旦
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-21", "2026-02-22",                                                  # 春节
    "2026-04-04", "2026-04-05", "2026-04-06",                                    # 清明
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",        # 劳动节
    "2026-06-19", "2026-06-20", "2026-06-21",                                    # 端午
    "2026-09-25", "2026-09-26", "2026-09-27",                                    # 中秋
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07", "2026-10-08",                                    # 国庆
}


def ai_square_is_peak():
    """是否处于高峰时段（决定用贵价还是便宜价）。

    规则（用户 2026-10-25 定）：
      1. 法定节假日 → 一律便宜（空闲）
      2. 其余按星期：周六日 → 便宜；周一到周五看时段
      3. 工作日 9:00-12:00、14:00-18:00 → 高峰（贵），其他时间 → 空闲（便宜）
    """
    import datetime as _dt
    bj = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    # 1) 法定节假日：整天都算空闲
    if bj.strftime("%Y-%m-%d") in CN_HOLIDAYS:
        return False
    # 2) 周末：整天都算空闲
    if bj.weekday() >= 5:
        return False
    # 3) 工作日看时段
    h = bj.hour + bj.minute / 60.0
    return (9 <= h < 12) or (14 <= h < 18)


def ai_square_free(info):
    """两项价格都为 0 → 免费（显示「限时免费」，不扣费）。"""
    return _as_price(info.get("price_in")) <= 0 and _as_price(info.get("price_out")) <= 0


def ai_square_model_info(cfg, m):
    """单个模型的合并信息（后台配置 > 代码默认值），含 free 标记。"""
    base = AI_SQUARE_INFO.get(m) or {}
    over = (cfg.get("info") or {}).get(m) or {}
    merged = {
        "name": (over.get("name") or "").strip() or m,   # 显示名（后台可改，ID 不变）
        "desc": over.get("desc") or base.get("desc", ""),
        "price_in": _as_price(over.get("price_in", base.get("price_in", 0))),
        "price_out": _as_price(over.get("price_out", base.get("price_out", 0))),
        # 分段计价：输入 >= hi_threshold 时改用 hi 档价格（留空则与基础档相同）
        "price_in_hi": _as_price(over.get("price_in_hi", base.get("price_in_hi", 0))),
        "price_out_hi": _as_price(over.get("price_out_hi", base.get("price_out_hi", 0))),
        "hi_threshold": int(over.get("hi_threshold", base.get("hi_threshold", 0)) or 0),
        "ctx": over.get("ctx") or base.get("ctx", "-"),
        # 分时计价（DeepSeek）：peak_* 为空表示不分时
        "peak_in": _as_price(over.get("peak_in", base.get("peak_in", 0))),
        "peak_out": _as_price(over.get("peak_out", base.get("peak_out", 0))),
        # vision / ocr / provider 都在 ai_config.MODELS 里（模型能力），不在 AI_SQUARE_INFO
        "vision": bool((MODELS.get(m) or {}).get("vision")),
        "ocr": bool((MODELS.get(m) or {}).get("ocr")),
        "provider": (MODELS.get(m) or {}).get("provider", ""),
    }
    merged["free"] = ai_square_free(merged)
    return merged

AI_SQUARE_SYSTEM = """你是「小柒」，MONO 社区（mono-craft.cc.cd）的 AI 助手。
回答简洁、准确、直接，不说套话、不堆感叹号。
擅长：编程问答、算法讲解、社区功能解答（积分/OJ/论坛/游戏/象棋）。
不知道就说不知道，绝不编造。用户问社区的事，如实答；问代码，认真讲。"""


def ai_square_config(db):
    """读 AI 广场配置（可用模型 + 默认模型 + 展示信息）。存 page_blocks 的 ai_square/config。"""
    try:
        row = db.execute(
            "SELECT content FROM page_blocks WHERE page=? AND key='config'",
            (AI_SQUARE_PAGE,)).fetchone()
        if row and row["content"]:
            cfg = json.loads(row["content"])
            models = [m for m in (cfg.get("models") or []) if m in MODELS]
            if models:
                d = cfg.get("default")
                if d not in models:
                    d = models[0]
                return {"models": models, "default": d, "info": cfg.get("info") or {}}
    except Exception:
        pass
    return dict(AI_SQUARE_DEFAULT_CFG, info={})


def ai_square_save(db, models, default, by, info=None):
    cfg = {"models": models, "default": default}
    if info is not None:
        cfg["info"] = info
    db.execute(
        "INSERT INTO page_blocks (page, key, content, updated_at, updated_by) "
        "VALUES (?, 'config', ?, ?, ?) "
        "ON CONFLICT(page, key) DO UPDATE SET content=excluded.content, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (AI_SQUARE_PAGE, json.dumps(cfg, ensure_ascii=False), time.time(), by or ""))
    db.commit()


@app.route("/ai")
@login_required
def ai_square_home():
    return redirect(url_for("ai_square_chat"))


def _ai_square_render(tab):
    db = get_db()
    cfg = ai_square_config(db)
    # 悬停描述卡片（标题/描述/消耗速度/思考强度/上下文窗口）—— 文案由用户提供
    desc = {m: ai_square_model_info(cfg, m) for m in cfg["models"]}
    labels = {"chat": "AI 对话", "me": "个人中心", "docs": "说明文档"}
    me = current_user()
    try:
        balance = float(me["ai_balance"]) if me["ai_balance"] is not None else 0.1
    except Exception:
        balance = 0.1
    # 向下取整到分（0.9997 → 0.99），不四舍五入
    import math as _math
    balance = _math.floor(balance * 100 + 1e-9) / 100.0
    return render_template("ai_square.html", user=me, tab=tab,
                           tab_label=labels.get(tab, "AI 对话"),
                           models=cfg["models"], default_model=cfg["default"],
                           model_desc=desc, balance=balance,
                           is_admin=bool(me["is_admin"]))


@app.route("/ai/chat")
@login_required
def ai_square_chat():
    return _ai_square_render("chat")


@app.route("/ai/me")
@login_required
def ai_square_me():
    return _ai_square_render("me")


@app.route("/ai/docs")
@login_required
def ai_square_docs():
    return _ai_square_render("docs")


# ---------- AI 广场：多对话（会话列表 + 消息，存库跨设备） ----------
def _aisq_ensure(db):
    """懒建表（幂等）。"""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS ai_square_convs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT NOT NULL DEFAULT '新对话',
            model      TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ai_square_msgs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id    INTEGER NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            cost       REAL NOT NULL DEFAULT 0,
            model      TEXT NOT NULL DEFAULT '',
            images     TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_aisq_conv_user ON ai_square_convs(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_aisq_msg_conv ON ai_square_msgs(conv_id, id);
    """)
    # 老库补 model 列（幂等）
    try:
        cols = [r["name"] for r in db.execute("PRAGMA table_info(ai_square_msgs)").fetchall()]
        if "model" not in cols:
            db.execute("ALTER TABLE ai_square_msgs ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        if "images" not in cols:
            db.execute("ALTER TABLE ai_square_msgs ADD COLUMN images TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    db.commit()


def _aisq_usage_table(db):
    """AI 广场消费流水表（删会话不影响统计）。

    ⚠️ 2026-10-26：原来个人中心饼图直接统计 ai_square_msgs.cost，
    删掉会话时消息一并删除 → 使用记录凭空消失（用户报的 bug）。
    改成独立流水：每次扣费写一条，会话删了流水还在。
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_square_usage (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            conv_id    INTEGER NOT NULL DEFAULT 0,
            model      TEXT NOT NULL DEFAULT '',
            provider   TEXT NOT NULL DEFAULT '',
            cost       REAL NOT NULL DEFAULT 0,
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_aisq_usage_user "
               "ON ai_square_usage(user_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_aisq_usage_time "
               "ON ai_square_usage(created_at)")
    db.commit()


def _aisq_usage_log(db, uid, conv_id, model, provider, cost, pt=0, ct=0):
    """记一条消费流水（失败不影响主流程）。"""
    try:
        db.execute(
            "INSERT INTO ai_square_usage "
            "(user_id, conv_id, model, provider, cost, prompt_tokens, completion_tokens, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, conv_id or 0, model or '', provider or '', float(cost or 0),
             int(pt or 0), int(ct or 0), time.time()))
        db.commit()
    except Exception:
        pass


def _aisq_own_conv(db, conv_id, uid):
    return db.execute("SELECT * FROM ai_square_convs WHERE id=? AND user_id=?",
                      (conv_id, uid)).fetchone()


@app.route("/api/ai/square/convs", methods=["GET"])
@login_required
def api_aisq_convs():
    """会话列表（按最近更新排）。"""
    me = current_user()
    db = get_db()
    _aisq_ensure(db)
    _aisq_usage_table(db)
    rows = db.execute(
        "SELECT c.id, c.title, c.model, c.updated_at, "
        "(SELECT COUNT(*) FROM ai_square_msgs m WHERE m.conv_id=c.id) AS n "
        "FROM ai_square_convs c WHERE c.user_id=? ORDER BY c.updated_at DESC LIMIT 100",
        (me["id"],)).fetchall()
    return jsonify({"ok": True, "convs": [
        {"id": r["id"], "title": r["title"], "model": r["model"],
         "n": r["n"], "updated_at": r["updated_at"]} for r in rows]})


@app.route("/api/ai/square/convs", methods=["POST"])
@login_required
def api_aisq_conv_create():
    """新建会话（可带初始模型）。"""
    me = current_user()
    db = get_db()
    _aisq_ensure(db)
    cfg = ai_square_config(db)
    j = request.json if request.is_json else {}
    model = j.get("model") or cfg["default"]
    if model not in cfg["models"]:
        model = cfg["default"]
    now = time.time()
    cur = db.execute(
        "INSERT INTO ai_square_convs (user_id, title, model, created_at, updated_at) "
        "VALUES (?, '新对话', ?, ?, ?)", (me["id"], model, now, now))
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "model": model})


@app.route("/api/ai/square/convs/<int:cid>", methods=["DELETE"])
@login_required
def api_aisq_conv_delete(cid):
    me = current_user()
    db = get_db()
    _aisq_ensure(db)
    if not _aisq_own_conv(db, cid, me["id"]):
        return jsonify({"ok": False, "msg": "会话不存在"}), 404
    db.execute("DELETE FROM ai_square_msgs WHERE conv_id=?", (cid,))
    db.execute("DELETE FROM ai_square_convs WHERE id=?", (cid,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/ai/square/convs/<int:cid>/msgs", methods=["GET"])
@login_required
def api_aisq_conv_msgs(cid):
    me = current_user()
    db = get_db()
    _aisq_ensure(db)
    conv = _aisq_own_conv(db, cid, me["id"])
    if not conv:
        return jsonify({"ok": False, "msg": "会话不存在"}), 404
    rows = db.execute(
        "SELECT id, role, content, cost, images FROM ai_square_msgs WHERE conv_id=? ORDER BY id",
        (cid,)).fetchall()
    def _imgs(v):
        try:
            return json.loads(v) if v else []
        except Exception:
            return []
    return jsonify({"ok": True, "model": conv["model"], "title": conv["title"],
                    "msgs": [{"role": r["role"], "content": r["content"],
                              "cost": r["cost"], "images": _imgs(r["images"])}
                             for r in rows]})


@app.route("/api/ai/square/convs/<int:cid>/append", methods=["POST"])
@login_required
def api_aisq_conv_append(cid):
    """追加一轮（user + assistant + 费用），并更新会话标题/模型/时间。"""
    me = current_user()
    db = get_db()
    _aisq_ensure(db)
    conv = _aisq_own_conv(db, cid, me["id"])
    if not conv:
        return jsonify({"ok": False, "msg": "会话不存在"}), 404
    j = request.json if request.is_json else {}
    user_msg = (j.get("user") or "").strip()
    ai_msg = (j.get("assistant") or "").strip()
    cost = float(j.get("cost") or 0)
    model = j.get("model") or conv["model"]
    imgs_list = [x for x in (j.get("images") or [])
                 if isinstance(x, str) and x.startswith("/static/uploads/ai/")][:4]
    if not user_msg:
        return jsonify({"ok": False, "msg": "缺少内容"}), 400
    now = time.time()
    db.execute("INSERT INTO ai_square_msgs (conv_id, role, content, cost, model, images, created_at) "
               "VALUES (?, 'user', ?, 0, '', ?, ?)",
               (cid, user_msg[:4000],
                json.dumps(imgs_list, ensure_ascii=False) if imgs_list else "", now))
    if ai_msg:
        db.execute("INSERT INTO ai_square_msgs (conv_id, role, content, cost, model, created_at) "
                   "VALUES (?, 'assistant', ?, ?, ?, ?)",
                   (cid, ai_msg[:20000], cost, model, now))
    # 首次提问用它当标题（截断 24 字）
    title = conv["title"]
    if (not title or title == "新对话") and user_msg:
        title = user_msg.replace("\n", " ")[:24]
    db.execute("UPDATE ai_square_convs SET title=?, model=?, updated_at=? WHERE id=?",
               (title, model, now, cid))
    db.commit()
    return jsonify({"ok": True, "title": title, "model": model})


# AI 广场：积分兑换余额的商品（以后要加档位，往这里加就行）
AI_SQUARE_GOODS = [
    {"id": "g05", "cost": 10000, "amount": 0.5, "label": "¥0.50 AI 余额"},
    {"id": "g2", "cost": 30000, "amount": 2.0, "label": "¥2.00 AI 余额"},
    {"id": "g5", "cost": 60000, "amount": 5.0, "label": "¥5.00 AI 余额"},
]


@app.route("/api/ai/square/pdf", methods=["POST"])
@login_required
def api_aisq_pdf():
    """PDF → 图片（前 N 页），用于 glm-ocr / 多模态模型识别。"""
    me = current_user()
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "msg": "没有收到文件"}), 400
    fname = (f.filename or "").lower()
    if not fname.endswith(".pdf"):
        return jsonify({"ok": False, "msg": "只支持 PDF 文件"}), 400
    data = f.read()
    if len(data) > 10 * 1024 * 1024:
        return jsonify({"ok": False, "msg": "PDF 太大，请上传 10MB 以内的"}), 400
    try:
        import fitz   # PyMuPDF
    except Exception:
        return jsonify({"ok": False, "msg": "服务器缺少 PDF 解析组件"}), 503

    MAX_PAGES = 10
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        n = min(len(doc), MAX_PAGES)
        _dir = os.path.join(app.root_path, "static", "uploads", "ai")
        os.makedirs(_dir, exist_ok=True)
        import secrets as _sec
        urls = []
        for i in range(n):
            pix = doc[i].get_pixmap(dpi=150)
            fn = "pdf_%d_%d_%s_p%d.png" % (me["id"], int(time.time() * 1000),
                                           _sec.token_hex(3), i + 1)
            pix.save(os.path.join(_dir, fn))
            urls.append("/static/uploads/ai/" + fn)
        doc.close()
    except Exception as e:
        return jsonify({"ok": False, "msg": "PDF 解析失败：%s" % str(e)[:60]}), 400

    return jsonify({"ok": True, "images": urls, "pages": n,
                    "total": len(doc) if False else n,
                    "msg": "已转换 %d 页" % n})


@app.route("/api/ai/square/goods", methods=["GET"])
@login_required
def api_aisq_goods():
    """可兑换商品 + 当前积分/余额。"""
    me = current_user()
    return jsonify({
        "ok": True,
        "coins": get_coins(me["id"]),
        "balance": round(float(me["ai_balance"] or 0), 2),
        "goods": AI_SQUARE_GOODS,
    })


@app.route("/api/ai/square/exchange", methods=["POST"])
@login_required
def api_aisq_exchange():
    """积分兑换 AI 余额：扣积分（走 award 流水）→ 加余额。"""
    me = current_user()
    j = request.json if request.is_json else {}
    gid = (j.get("goods") or "").strip()
    goods = next((g for g in AI_SQUARE_GOODS if g["id"] == gid), None)
    if not goods:
        return jsonify({"ok": False, "msg": "商品不存在"}), 400

    uid = me["id"]
    coins = get_coins(uid)
    if coins < goods["cost"]:
        return jsonify({"ok": False,
                        "msg": "积分不足：需要 %d，你当前有 %d" % (goods["cost"], coins)}), 400

    # 扣积分（唯一入口，带流水）。不传 idem_key：允许重复兑换（每次都是一笔新消费）
    award(uid, -goods["cost"], "ai_balance", "兑换 " + goods["label"])
    # 加余额
    db = get_db()
    db.execute("UPDATE users SET ai_balance = COALESCE(ai_balance, 0) + ? WHERE id=?",
               (goods["amount"], uid))
    db.commit()
    new_coins = get_coins(uid)
    new_bal = db.execute("SELECT ai_balance FROM users WHERE id=?", (uid,)).fetchone()["ai_balance"]
    _log_mod.event("ai_square_exchange", user=me["username"],
                   goods=goods["id"], coins=goods["cost"], amount=goods["amount"])
    return jsonify({"ok": True,
                    "msg": "兑换成功：%d 积分 → %s" % (goods["cost"], goods["label"]),
                    "coins": new_coins, "balance": round(float(new_bal or 0), 2)})


@app.route("/api/ai/square/stats", methods=["GET"])
@login_required
def api_aisq_stats():
    """个人中心：按模型统计花费（只算自己的会话）。"""
    me = current_user()
    db = get_db()
    _aisq_ensure(db)
    _aisq_usage_table(db)
    rows = db.execute(
        "SELECT model, SUM(cost) AS cost, COUNT(*) AS n "
        "FROM ai_square_usage WHERE user_id = ? "
        "GROUP BY model ORDER BY cost DESC", (me["id"],)).fetchall()
    total = 0.0
    items = []
    for r in rows:
        c = float(r["cost"] or 0)
        total += c
        items.append({"model": r["model"] or "其他", "cost": round(c, 6), "n": r["n"]})
    return jsonify({"ok": True, "total": round(total, 6), "items": items})


@app.route("/api/ai/square", methods=["POST"])
@login_required
def api_ai_square_chat():
    """AI 广场对话（SSE 流式）。model 由前端选，但必须在后台配置的可用列表内。"""
    me = current_user()
    db = get_db()
    _j = request.json if request.is_json else {}
    msg = (_j.get("message") or "").strip()
    history = _j.get("history") or []

    _aisq_ensure(db)
    _aisq_usage_table(db)
    cfg = ai_square_config(db)
    model = _j.get("model") or cfg["default"]
    if model not in cfg["models"]:
        model = cfg["default"]

    # OCR 模型允许空文字（只吃图），其余必须有问题
    is_ocr = bool((MODELS.get(model) or {}).get("ocr"))
    if not msg and not is_ocr:
        return jsonify({"ok": False, "msg": "说点什么吧"}), 400
    if len(msg) > 2000:
        return jsonify({"ok": False, "msg": "消息太长了，精简到 2000 字以内"}), 400

    if not _guard.ai_global_budget_ok():
        used, cap = _guard.ai_global_status()
        return jsonify({"ok": False,
                        "msg": "今日 AI 用量已达上限（%d/%d），明天自动恢复" % (used, cap)}), 503

    # 计费：免费模型不检查余额；付费模型余额 <= 0 直接拒绝
    minfo = ai_square_model_info(cfg, model)
    is_free = minfo["free"]
    try:
        balance = float(me["ai_balance"]) if me["ai_balance"] is not None else 0.0
    except Exception:
        balance = 0.0
    if not is_free and balance <= 0:
        return jsonify({"ok": False, "msg": "余额不足，请联系管理员充值后再使用付费模型"}), 402

    # 多模态：前端传 data URL 数组（最多 4 张），仅支持 vision 模型
    images = _j.get("images") or []
    if not isinstance(images, list):
        images = []
    # 支持两种：data URL（前端直传）和 /static/uploads/ai/ 路径（PDF 转出来的图）
    _raw_imgs = [x for x in images[:4] if isinstance(x, str)]
    images = []
    for _x in _raw_imgs:
        if _x.startswith("data:image/") and len(_x) < 8_000_000:
            images.append(_x)
        elif _x.startswith("/static/uploads/ai/"):
            # 服务端读文件转 base64（模型要 base64）
            try:
                import base64 as _b64m
                _p = os.path.join(app.root_path, _x.lstrip("/"))
                with open(_p, "rb") as _fh:
                    _b = _b64m.b64encode(_fh.read()).decode()
                _e = _p.rsplit(".", 1)[-1].lower()
                _mime = "jpeg" if _e in ("jpg", "jpeg") else _e
                images.append("data:image/%s;base64,%s" % (_mime, _b))
            except Exception:
                pass
    if images and not (MODELS.get(model) or {}).get("vision"):
        return jsonify({"ok": False,
                        "msg": "当前模型不支持图片，请切换到多模态模型（如 GLM-5.3-flash）"}), 400

    messages = [{"role": "system", "content": AI_SQUARE_SYSTEM + _i18n.ai_lang_hint()}]
    for h in (history or [])[-4:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:1500]})
    # 图片落盘（用于历史回放），同时把 base64 传给模型
    saved_imgs = []
    if images:
        try:
            import base64 as _b64, secrets as _sec
            _dir = os.path.join(app.root_path, "static", "uploads", "ai")
            os.makedirs(_dir, exist_ok=True)
            for _idx, _img in enumerate(_raw_imgs):
                if _img.startswith("/static/uploads/ai/"):
                    saved_imgs.append(_img)   # PDF 转的图已经在盘上，直接复用
                    continue
                try:
                    _head, _b64data = _img.split(",", 1)
                    _ext = "png" if "png" in _head else ("webp" if "webp" in _head else "jpg")
                    _fn = "ai_%d_%d_%s.%s" % (me["id"], int(time.time() * 1000),
                                              _sec.token_hex(3), _ext)
                    with open(os.path.join(_dir, _fn), "wb") as _f:
                        _f.write(_b64.b64decode(_b64data))
                    saved_imgs.append("/static/uploads/ai/" + _fn)
                except Exception:
                    pass
        except Exception:
            saved_imgs = []

    if is_ocr:
        # glm-ocr 的 content 数组**必须含一个非空 text block**（空串也报 1214），
        # 只放 image_url 同样报 1214 —— 用固定短指令占位，不影响识别结果。
        if not images:
            return jsonify({"ok": False, "msg": "OCR 模型需要图片，请先上传图片或 PDF"}), 400
        _parts = [{"type": "text", "text": "识别图片文字"}]
        for u in images:
            _parts.append({"type": "image_url", "image_url": {"url": u}})
        messages = [{"role": "user", "content": _parts}]
    elif images:
        parts = [{"type": "text", "text": msg}]
        for img in images:
            parts.append({"type": "image_url", "image_url": {"url": img}})
        messages.append({"role": "user", "content": parts})
    else:
        messages.append({"role": "user", "content": msg})

    acc_holder = [""]
    _chk = [0]      # 余额检查计数器

    def gen():
        # ---- OCR 模型：走 layout_parsing 端点（chat/completions 会 1214） ----
        if is_ocr:
            try:
                headers = {"Authorization": "Bearer " + get_ai_key(model),
                           "Content-Type": "application/json"}
                _base = model_url(model)
                # 从 chat/completions 地址推出 layout_parsing 地址（requests 兜底用）
                _ocr_url = _base.replace("/chat/completions", "/layout_parsing")
                _texts = []
                _pt = _ct = 0

                # 优先用官方 zai-sdk；不可用时回落到 requests
                _cli = None
                try:
                    from zai import ZhipuAiClient
                    _cli = ZhipuAiClient(api_key=get_ai_key(model))
                except Exception:
                    _cli = None

                # layout_parsing 一次只收一个 file，多图逐张处理
                for _img in images:
                    _md, _uu = "", {}
                    if _cli is not None:
                        _resp = _cli.layout_parsing.create(model=model, file=_img)
                        try:
                            _md = getattr(_resp, "md_results", "") or ""
                        except Exception:
                            _md = ""
                        if not _md and hasattr(_resp, "model_dump"):
                            try:
                                _md = (_resp.model_dump() or {}).get("md_results") or ""
                            except Exception:
                                pass
                        _uu = getattr(_resp, "usage", None) or {}
                        if not isinstance(_uu, dict) and hasattr(_uu, "model_dump"):
                            try:
                                _uu = _uu.model_dump() or {}
                            except Exception:
                                _uu = {}
                    else:
                        rr = _requests.post(_ocr_url, json={"model": model, "file": _img},
                                            headers=headers, timeout=180)
                        rr.raise_for_status()
                        _jd = rr.json()
                        _md = _jd.get("md_results") or ""
                        _uu = _jd.get("usage") or {}
                    if _md:
                        _texts.append(_md)
                    _pt += int(_uu.get("prompt_tokens") or 0)
                    _ct += int(_uu.get("completion_tokens") or 0)
                content = "\n\n---\n\n".join(_texts) if _texts else "（未识别到文字）"
                yield "data: " + json.dumps({"t": content}, ensure_ascii=False) + "\n\n"
                _u = {"prompt_tokens": _pt, "completion_tokens": _ct}
                _pt = int(_u.get("prompt_tokens") or 0)
                _ct = int(_u.get("completion_tokens") or 0)
                _cost = (_pt / 1e6) * minfo["price_in"] + (_ct / 1e6) * minfo["price_out"]
                try:
                    _cn = sqlite3.connect(DB_PATH, timeout=10)
                    try:
                        if _cost > 0 and not is_free:
                            _cn.execute("UPDATE users SET ai_balance = "
                                        "MAX(0, COALESCE(ai_balance,0) - ?) WHERE id=?",
                                        (_cost, me["id"]))
                        _cn.execute(
                            "INSERT INTO ai_square_usage (user_id, conv_id, model, provider, "
                            "cost, prompt_tokens, completion_tokens, created_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (me["id"], conv_id or 0, model, minfo.get("provider") or "",
                             float(_cost or 0), _pt, _ct, time.time()))
                        _cn.commit()
                    finally:
                        _cn.close()
                except Exception as _e:
                    app.logger.warning("[ai_square] OCR 扣费/流水失败: %s", _e)
                yield "data: " + json.dumps(
                    {"done": True, "cost": round(_cost, 6), "free": is_free,
                     "images": saved_imgs}, ensure_ascii=False) + "\n\n"
            except Exception as e:
                yield "data: " + json.dumps(
                    {"t": "\n[OCR 出错了：%s]" % str(e)[:80]}, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"done": True}, ensure_ascii=False) + "\n\n"
            return

        try:
            headers = {"Authorization": "Bearer " + get_ai_key(model),
                       "Content-Type": "application/json"}
            payload = {
                "model": model, "messages": messages,
                "max_tokens": min(MODELS[model]["max_tokens"], MAX_CHAT_TOKENS),
                "temperature": MODELS[model]["temperature"], "stream": True,
                # 要 usage（精确 token 数）才能按量扣费
                "stream_options": {"include_usage": True},
            }
            r = _requests.post(model_url(model), json=payload, headers=headers,
                               stream=True, timeout=180)
            r.raise_for_status()
            r.encoding = "utf-8"
            usage = None
            for raw in r.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith("data: "):
                    raw = raw[6:]
                if raw.strip() == "[DONE]":
                    break
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if d.get("usage"):
                    usage = d["usage"]
                delta = (d.get("choices") or [{}])[0].get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    acc_holder[0] += piece
                    yield "data: " + json.dumps({"t": piece}, ensure_ascii=False) + "\n\n"
                    # 生成中余额兜底：每 20 段查一次，<=0 立刻中断（防并发超支）
                    if not is_free:
                        _chk[0] += 1
                        if _chk[0] % 20 == 0:
                            try:
                                _bc = sqlite3.connect(DB_PATH)
                                try:
                                    _bv = _bc.execute(
                                        "SELECT ai_balance FROM users WHERE id=?",
                                        (me["id"],)).fetchone()
                                    _bal_now = float((_bv or [0])[0] or 0)
                                finally:
                                    _bc.close()
                                if _bal_now <= 0:
                                    yield "data: " + json.dumps(
                                        {"t": "\n\n[余额已耗尽，回答中断]"},
                                        ensure_ascii=False) + "\n\n"
                                    break
                            except Exception:
                                pass

            # ---- 扣费（免费模型跳过）----
            cost = 0.0
            if not is_free:
                pt = int((usage or {}).get("prompt_tokens") or 0)
                ct = int((usage or {}).get("completion_tokens") or 0)
                if not (pt or ct):
                    # 供应商没给 usage → 估算兜底（保证不白嫖）
                    def _txt_of(c):
                        if isinstance(c, list):
                            return " ".join(p.get("text", "") for p in c
                                            if isinstance(p, dict))
                        return c or ""
                    pt = sum(estimate_tokens(_txt_of(m["content"])) for m in messages)
                    ct = estimate_tokens(acc_holder[0]) if acc_holder else 0
                pin, pout = minfo["price_in"], minfo["price_out"]
                # 分时计价（DeepSeek）：高峰时段用 peak 价
                if minfo.get("peak_in") and ai_square_is_peak():
                    pin = minfo["peak_in"]
                    pout = minfo.get("peak_out") or pout
                # 分段计价：输入 token 达到阈值走 hi 档（未配 hi 则同基础档）
                else:
                    thr = int(minfo.get("hi_threshold") or 0)
                    if thr > 0 and pt >= thr and minfo.get("price_in_hi"):
                        pin = minfo["price_in_hi"]
                        pout = minfo.get("price_out_hi") or pout
                cost = (pt / 1000000.0) * pin + (ct / 1000000.0) * pout
                # 扣费 + 记流水（都用独立连接，generator 里 request context 已失效）
                try:
                    _conn = sqlite3.connect(DB_PATH, timeout=10)
                    try:
                        if cost > 0:
                            _conn.execute(
                                "UPDATE users SET ai_balance = "
                                "MAX(0, COALESCE(ai_balance, 0) - ?) WHERE id=?",
                                (cost, me["id"]))
                        # 流水：免费模型也记（cost=0），后台统计调用次数要用
                        _conn.execute(
                            "INSERT INTO ai_square_usage (user_id, conv_id, model, provider, "
                            "cost, prompt_tokens, completion_tokens, created_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (me["id"], conv_id or 0, model, minfo.get("provider") or "",
                             float(cost or 0), pt, ct, time.time()))
                        _conn.commit()
                    finally:
                        _conn.close()
                except Exception as _e:
                    app.logger.warning("[ai_square] 扣费/流水失败 uid=%s cost=%s: %s",
                                       me["id"], cost, _e)
            yield "data: " + json.dumps(
                {"done": True, "cost": round(cost, 6), "free": is_free,
                 "images": saved_imgs},
                ensure_ascii=False) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps(
                {"t": "\n[出错了：%s]" % str(e)[:60]}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/admin/ai-square", methods=["GET", "POST"])
@login_required
def admin_ai_square():
    """超管：设置 AI 广场可用模型 + 默认模型。"""
    me = current_user()
    if not me["is_admin"]:
        abort(403)
    db = get_db()
    if request.method == "POST":
        picked = [m for m in request.form.getlist("models") if m in MODELS]
        # 按后台拖拽的顺序排序（model_order 是逗号分隔的模型名）
        _order_raw = (request.form.get("model_order") or "").strip()
        if _order_raw:
            _order = [x.strip() for x in _order_raw.split(",") if x.strip()]
            picked.sort(key=lambda mm: _order.index(mm) if mm in _order else 999)
        if not picked:
            flash("至少要勾选一个模型", "err")
            return redirect(url_for("admin_ai_square"))
        default = request.form.get("default") or picked[0]
        if default not in picked:
            default = picked[0]
        # 描述 + 三项属性（按模型逐个存）
        info = {}
        for m in picked:
            info[m] = {
                "name": (request.form.get("name_" + m) or "").strip()[:40] or m,
                "desc": (request.form.get("desc_" + m) or "").strip()[:600],
                "price_in": _as_price(request.form.get("price_in_" + m)),
                "price_out": _as_price(request.form.get("price_out_" + m)),
                "price_in_hi": _as_price(request.form.get("price_in_hi_" + m)),
                "price_out_hi": _as_price(request.form.get("price_out_hi_" + m)),
                "peak_in": _as_price(request.form.get("peak_in_" + m)),
                "peak_out": _as_price(request.form.get("peak_out_" + m)),
                "hi_threshold": 32000 if (request.form.get("price_in_hi_" + m) or
                                          request.form.get("price_out_hi_" + m)) else 0,
            }
        ai_square_save(db, picked, default, me["username"], info)
        flash("AI 广场模型设置已保存", "ok")
        return redirect(url_for("admin_ai_square"))
    cfg = ai_square_config(db)
    info = {m: ai_square_model_info(cfg, m) for m in cfg["models"]}
    # 排序：已启用的按配置顺序在前，未启用的按名字排在后面
    _rest = [x for x in sorted(MODELS.keys()) if x not in cfg["models"]]
    return render_template("admin_ai_square.html", cfg=cfg, info=info,
                           all_models=sorted(MODELS.keys()),
                           all_models_sorted=cfg["models"] + _rest)


@app.route("/ai-old")
@login_required
def ai_assistant():
    me = current_user()
    rows = get_db().execute(
        "SELECT role, content, created_at FROM ai_chat_history "
        "WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (me['id'], MAX_HISTORY_ROUND * 2)
    ).fetchall()
    history = [{'role': r['role'], 'content': r['content']} for r in reversed(rows)]
    quota = me['ai_quota'] if 'ai_quota' in me.keys() else 30
    today_start = time.mktime(time.localtime())
    today_start = today_start - (today_start % 86400)
    used = get_db().execute(
        "SELECT COUNT(*) AS c FROM ai_chat_history WHERE user_id=? AND role='user' AND created_at>=?",
        (me['id'], today_start)).fetchone()['c'] if quota and quota > 0 else 0
    debug_left = me['ai_debug_left'] if 'ai_debug_left' in me.keys() else DEBUG_INIT_QUOTA
    if debug_left is None:
        debug_left = 0
    return render_template("ai.html", user=me, history=history,
                           ai_quota=quota, ai_used=used,
                           debug_left=debug_left,
                           # 053：常规模式（auto）暂时下架——只是不在界面上显示，
                           # 代码和配置原样保留（用户 2026-10-25 定）。
                           # 默认模式改 fast，免得 localStorage 空的用户落到隐藏的模式上。
                           chat_modes={k: v for k, v in CHAT_MODES.items() if k != "auto"},
                           default_mode=("fast" if DEFAULT_CHAT_MODE == "auto" else DEFAULT_CHAT_MODE),
                           debug_input_char=DEBUG_INPUT_CHAR,
                           debug_output_char=DEBUG_OUTPUT_CHAR,
                           max_input_char=MAX_USER_INPUT_CHAR,
                           debug_model_label=model_label(DEBUG_MODEL))


@app.route("/api/ai/history", methods=["GET"])
@login_required
def ai_history():
    me = current_user()
    rows = get_db().execute(
        "SELECT role, content FROM ai_chat_history "
        "WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (me['id'], MAX_HISTORY_ROUND * 2)
    ).fetchall()
    history = [{'role': r['role'], 'content': r['content']} for r in reversed(rows)]
    return jsonify({"ok": True, "history": history})


@app.route("/api/ai/clear", methods=["POST"])
@login_required
def ai_clear():
    me = current_user()
    db = get_db()
    db.execute("DELETE FROM ai_chat_history WHERE user_id=?", (me['id'],))
    db.commit()
    return jsonify({"ok": True})


def _cut_effective(text, limit):
    """把文本按「有效字数」截断（英文按半个字），超了补省略号。"""
    if count_effective(text) <= limit:
        return text, False
    n = 0.0
    out = []
    for ch in text:
        n += 0.5 if ord(ch) < 128 else 1.0
        if n > limit:
            break
        out.append(ch)
    return ''.join(out).rstrip() + '……', True


# 同一台设备名下所有账号共享「AI 调代码」当日总额度。
# 单账号发号很容易（换个邮箱/手机号就一个新号），
# 只按 uid 限的次数用光 5 次换号还能再薅 5 次，额度等于没有上限。
# 设备指纹这一层不区分账号 —— 换号不改变这台机器能薅到的总量。
#
# 不含 IP：学校/宿舍是 NAT 出口，一个 IP 后面是整个年级的人，
# 按 IP 收口会误伤正常同学（邀请活动已经因为这个去掉过 IP 限制）。
#
# ⚠️ 必须落库：放内存里服务一重启就清零，等于随时能重置额度。
AI_FP_DAILY_CAP = 5


def _ai_fp_tables(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_fp_quota (
            fp       TEXT NOT NULL,
            day      TEXT NOT NULL,
            used     INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (fp, day)
        )
    """)
    db.commit()


def _ai_fp_budget_ok(fp):
    """这台设备今天还能调几次 AI 调代码。取不到指纹就放行（不拦）。"""
    if not fp:
        return True, ''
    fp = fp[:24]
    day = time.strftime('%Y-%m-%d')
    db = get_db()
    try:
        _ai_fp_tables(db)
        db.execute("DELETE FROM ai_fp_quota WHERE day<>? AND updated_at<?",
                   (day, time.time() - 3 * 86400))
        db.execute("INSERT OR IGNORE INTO ai_fp_quota (fp, day, used, updated_at) "
                   "VALUES (?,?,0,?)", (fp, day, time.time()))
        row = db.execute("SELECT used FROM ai_fp_quota WHERE fp=? AND day=?",
                         (fp, day)).fetchone()
        used = int(row['used'] or 0) if row else 0
        if used >= AI_FP_DAILY_CAP:
            db.commit()
            return False, '这台设备今天的 AI 调代码次数已用完（%d/%d），明天再来' % (
                AI_FP_DAILY_CAP, AI_FP_DAILY_CAP)
        db.execute("UPDATE ai_fp_quota SET used=used+1, updated_at=? WHERE fp=? AND day=?",
                   (time.time(), fp, day))
        db.commit()
    except Exception:
        return True, ''
    return True, ''


def _ai_once(model, messages, max_tokens, thinking=False, level=None):
    """非流式调一次模型，返回 (ok, 文本或错误, usage)。

    ⚠️ DeepSeek 是**推理模型**，思考 token 也算进 max_tokens ✗
    题面/代码一长，思考就可能把额度吃光 → 正文是空串 ✗
    而空串如果当成成功返回，用户看到的就是「转半天啥也没有」✗
    （2026-10-10 真实事故：NOIP 题 + 长代码，8000 全被思考吃掉）
    所以这里**空正文一律当失败**，并给一句人话解释 ✓
    """
    headers = {'Authorization': 'Bearer ' + get_ai_key(model),
               'Content-Type': 'application/json'}
    payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens,
               'temperature': MODELS[model]['temperature'], 'stream': False}
    payload.update(model_extra_body(model, thinking, level))
    try:
        r = _requests.post(model_url(model), json=payload, headers=headers, timeout=180)
        if r.status_code == 400 and ('effort' in payload or 'thinking' in payload):
            payload.pop('effort', None)
            payload.pop('thinking', None)
            r = _requests.post(model_url(model), json=payload, headers=headers, timeout=180)
        if r.status_code == 400 and max_tokens > 8192:
            # 有些模型不认太大的 max_tokens，退回一个保守值再试一次
            payload['max_tokens'] = 8192
            r = _requests.post(model_url(model), json=payload, headers=headers, timeout=180)
        if r.status_code != 200:
            return False, 'AI 服务暂时不可用（HTTP %d）' % r.status_code, {}
        j = r.json()
        choice = (j.get('choices') or [{}])[0]
        ch = choice.get('message') or {}
        text = (ch.get('content') or '').strip()
        usage = j.get('usage') or {}
        finish = choice.get('finish_reason') or ''
        if not text:
            ct = int(usage.get('completion_tokens') or 0)
            if finish == 'length':
                return False, ('模型把 %d 个 token 全用在思考上了，没写出正文。'
                               '把题面或代码精简一点再试一次。' % ct), usage
            return False, '模型这次没返回内容，请再试一次。', usage
        return True, text, usage
    except Exception as e:
        return False, '网络异常，请稍后再试（%s）' % str(e)[:60], {}


@app.route("/api/ai/debug", methods=["POST"])
@login_required
def ai_debug():
    """AI 代码调试：题面必填、说明必填、代码可选。每次消耗一次调试次数。"""
    me = current_user()
    db = get_db()
    _j = request.json if request.is_json else {}
    problem = (_j.get('problem') or request.form.get('problem') or '').strip()
    code = (_j.get('code') or request.form.get('code') or '').strip()
    note = (_j.get('note') or request.form.get('note') or '').strip()

    if not problem:
        return jsonify({"ok": False, "msg": '题面必填'}), 400
    if not note:
        return jsonify({"ok": False, "msg": '说明必填 —— 说说你卡在哪，或者你觉得错在哪'}), 400
    total = count_effective(problem) + count_effective(code) + count_effective(note)
    if total > DEBUG_INPUT_CHAR:
        return jsonify({"ok": False,
                        "msg": '内容太长了（%.0f / %d 字，英文按半个字算），精简一下' % (total, DEBUG_INPUT_CHAR)}), 400
    if check_prompt_injection(problem + '\n' + code + '\n' + note):
        return jsonify({"ok": False, "msg": '内容包含不安全指令，已被拦截'}), 400

    left = me['ai_debug_left'] if 'ai_debug_left' in me.keys() else DEBUG_INIT_QUOTA
    left = 0 if left is None else int(left)
    if left <= 0:
        return jsonify({"ok": False, "msg": '调试次数已用完，可以用兑换码补充'}), 403

    ok, msg = _ai_fp_budget_ok(_mm_fp())
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 429

    ok, msg = _check_rate_limit(me['id'])
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 429
    if not _acquire_concurrent():
        return jsonify({"ok": False, "msg": '系统繁忙，请稍后再试'}), 503

    user_msg = ('【题面】\n' + problem
                + '\n\n【代码】\n' + (code if code else DEBUG_EMPTY_CODE_HINT)
                + '\n\n【说明】\n' + note)
    messages = [{'role': 'system', 'content': DEBUG_SYSTEM_PROMPT + _i18n.ai_lang_hint()},
                {'role': 'user', 'content': user_msg}]
    _t0 = time.time()
    try:
        # DeepSeek 是推理模型，思考 token 也计入 max_tokens ——
        # ⚠️ 2026-10-10 事故：题面+代码一长，8000 被思考全吃掉、正文空串 ✗
        #    2026-10-17 再提到 32000（_ai_once 里有 400 时退回 8192 的兜底）；
        #    真正的长度上限仍由下面的 _cut_effective(2000 有效字) 兜住。
        ok, answer, usage = _ai_once(DEBUG_MODEL, messages, 32000)
    finally:
        _release_concurrent()

    _pt = int((usage or {}).get('prompt_tokens') or 0)
    _ct = int((usage or {}).get('completion_tokens') or 0)
    _lat = int((time.time() - _t0) * 1000)
    _ip = _ai_ip()

    if not ok:
        # ⚠️ 以前这里直接 return，**失败完全不留痕** ✗
        # 想查「谁在用、是不是老失败」就查不到 ✗ 现在两张表都记。
        try:
            db.execute(
                "INSERT INTO ai_debug_logs (user_id, problem, code, note, answer, model, created_at, "
                "prompt_tokens, completion_tokens, latency_ms, success, error, ip, out_chars) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (me['id'], problem[:4000], code[:8000], note[:2000], '', DEBUG_MODEL,
                 time.time(), _pt, _ct, _lat, 0, str(answer)[:200], _ip, 0))
            db.commit()
        except Exception:
            pass
        _ai_log(db, me['id'], DEBUG_MODEL, _pt, _ct, False, str(answer)[:200],
                module='debug', mode='debug', latency_ms=_lat, ip=_ip,
                question=problem[:2000], answer='')
        return jsonify({"ok": False, "msg": answer}), 502

    answer, cut = _cut_effective(answer, DEBUG_OUTPUT_CHAR)
    left -= 1
    try:
        db.execute("UPDATE users SET ai_debug_left=? WHERE id=?", (left, me['id']))
        db.execute("INSERT INTO ai_debug_logs (user_id, problem, code, note, answer, model, created_at, "
                   "prompt_tokens, completion_tokens, latency_ms, success, error, ip, out_chars) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (me['id'], problem[:4000], code[:8000], note[:2000], answer[:8000],
                    DEBUG_MODEL, time.time(), _pt, _ct, _lat, 1, '', _ip, len(answer or '')))
        db.commit()
    except Exception:
        pass
    # 调试调用也要进 ai_logs —— 以前只写 ai_debug_logs，
    # 所以后台看板的「AI 调用」统计**完全漏掉了代码调试** ✗
    _ai_log(db, me['id'], DEBUG_MODEL, _pt, _ct, True, '',
            module='debug', mode='debug', latency_ms=_lat, ip=_ip,
            question=problem[:2000], answer=(answer or '')[:2000])
    return jsonify({"ok": True, "answer": answer, "left": left, "cut": cut,
                    "chars": int(count_effective(answer)),
                    "tokens": usage.get('completion_tokens') or 0})


@app.route("/api/ai/chat", methods=["POST"])
@login_required
def ai_chat():
    """流式 SSE 聊天接口（前端用 fetch + ReadableStream 解析）"""
    me = current_user()
    db = get_db()
    _t0 = time.time()          # 用于记录耗时

    _j = request.json if request.is_json else {}
    user_content = (_j.get('message') or request.form.get('message') or '').strip()
    ai_image = (_j.get('image') or '').strip()
    if ai_image and not ai_image.startswith('data:image/'):
        ai_image = ''
    if len(ai_image) > 6 * 1024 * 1024:
        return jsonify({"ok": False, "msg": '图片太大了，换一张小一点的'}), 400
    _mode_key = str(_j.get('mode') or request.form.get('mode') or DEFAULT_CHAT_MODE)
    _mode = CHAT_MODES.get(_mode_key) or CHAT_MODES[DEFAULT_CHAT_MODE]
    ai_thinking = bool(_mode['thinking'])
    ai_level = _mode['level']
    if not user_content and not ai_image:
        return jsonify({"ok": False, "msg": '请输入内容'}), 400
    if not user_content:
        return jsonify({"ok": False, "msg": '请输入内容'}), 400
    if len(user_content) > MAX_USER_INPUT_CHAR:
        return jsonify({"ok": False, "msg": f'输入内容过长（{len(user_content)} > {MAX_USER_INPUT_CHAR}），请精简'}), 400

    if check_prompt_injection(user_content):
        _ai_log(db, me['id'], '', 0, 0, False, 'prompt injection detected',
                module='chat', mode=_mode_key, latency_ms=0,
                ip=_guard.client_ip(request), question=user_content)
        return jsonify({"ok": False, "msg": '消息包含不安全内容，已被拦截'}), 400

    if not _guard.ai_global_budget_ok():
        used, cap = _guard.ai_global_status()
        _log_mod.event("ai_global_break", used=used, cap=cap)
        _monitor.note_attack("ai_global_break", _guard.client_ip(request), "/api/ai/chat",
                             "全站 AI 预算熔断 %d/%d" % (used, cap))
        return jsonify({"ok": False, "msg": '今日全站 AI 用量已达上限（%d/%d），明天自动恢复' % (used, cap)}), 503

    _ip = getattr(g, "_ip", None) or _guard.client_ip(request)
    ok, msg = _guard.check_ai_ip(_ip)
    if not ok:
        _log_mod.event("ai_ip_block", ip=_ip, uid=me['id'])
        _monitor.note_attack("ai_ip_block", _ip, "/api/ai/chat", "单 IP AI 用量超限")
        return jsonify({"ok": False, "msg": msg}), 429

    ok, msg = _check_rate_limit(me['id'])
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 429

    _guard.ai_global_consume()

    quota = me['ai_quota'] if 'ai_quota' in me.keys() else 30
    if quota == 0:
        return jsonify({"ok": False, "msg": '你的 AI 对话配额已关闭，请联系管理员'}), 403
    if quota and quota > 0:
        today_start = time.mktime(time.localtime())
        today_start = today_start - (today_start % 86400)
        used = db.execute(
            "SELECT COUNT(*) AS c FROM ai_chat_history WHERE user_id=? AND role='user' AND created_at>=?",
            (me['id'], today_start)).fetchone()['c']
        if used >= quota:
            return jsonify({"ok": False, "msg": f'今日 AI 对话已用完（{quota}/{quota}），明天再来或联系管理员'}), 429

    hist_rows = db.execute(
        "SELECT role, content FROM ai_chat_history "
        "WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (me['id'], MAX_HISTORY_ROUND * 2)
    ).fetchall()
    history = list(reversed(hist_rows))
    while len(history) > MAX_HISTORY_ROUND * 2:
        history.pop(0)

    model = _mode['model'] or pick_busy_model(CHAT_POOL)

    messages = [{'role': 'system', 'content': SYSTEM_PROMPT + _i18n.ai_lang_hint()}]
    for r, c in history:
        messages.append({'role': r, 'content': c})
    if ai_image:
        messages.append({'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': ai_image}},
            {'type': 'text', 'text': user_content or '请描述这张图片'},
        ]})
    else:
        messages.append({'role': 'user', 'content': user_content})
    def _txt(c):
        if isinstance(c, list):
            return ' '.join(p.get('text', '') for p in c if isinstance(p, dict))
        return c or ''
    total_tokens = sum(estimate_tokens(_txt(m['content'])) for m in messages) + MODELS[model]['max_tokens']
    if total_tokens > MAX_CONTEXT_TOKENS:
        model_done(model)
        return jsonify({"ok": False, "msg": '对话上下文过大，请新建对话清空历史'}), 400

    now = time.time()
    _cur = db.execute(
        "INSERT INTO ai_chat_history (user_id, role, content, model, created_at) VALUES (?,?,?,?,?)",
        (me['id'], 'user', user_content, model, now))
    db.commit()
    _user_msg_id = _cur.lastrowid
    prompt_tokens_pre = estimate_tokens(user_content) + sum(estimate_tokens(c) for _, c in history) + estimate_tokens(SYSTEM_PROMPT)

    if not _acquire_concurrent():
        try:
            db.execute("DELETE FROM ai_chat_history WHERE id=?", (_user_msg_id,))
            db.commit()
        except Exception:
            pass
        model_done(model)
        return jsonify({"ok": False, "msg": '系统繁忙，请稍后再试'}), 503

    # 兜底候选也只能是对话池内的模型 —— 不能让对话悄悄落到 DeepSeek 上
    _candidates = [model] + [m for m in CHAT_POOL if m != model]

    def _gen():
        import sqlite3 as _sq
        full = []
        prompt_tokens = 0
        completion_tokens = 0
        ok = True
        err = ''
        used_model = _candidates[0]
        try:
            resp = None
            for attempt, m in enumerate(_candidates):
                # key 与接口地址都按模型所属供应商取 —— DeepSeek 和智谱不是同一套
                headers = {
                    'Authorization': 'Bearer ' + get_ai_key(m),
                    'Content-Type': 'application/json',
                }
                _url = model_url(m)
                payload = {
                    'model': m,
                    'messages': messages,
                    'max_tokens': min(MODELS[m]['max_tokens'], MAX_CHAT_TOKENS),
                    'temperature': MODELS[m]['temperature'],
                    'stream': True,
                }
                payload.update(model_extra_body(m, ai_thinking, ai_level))
                _think_field = 'effort' if 'effort' in payload else (
                    'thinking' if 'thinking' in payload else None)
                try:
                    _r = _requests.post(_url, json=payload, headers=headers, stream=True, timeout=120)
                    if _r.status_code == 400 and _think_field:
                        # 供应商不认这个思考参数：摘掉重试一次
                        _r.close()
                        payload.pop(_think_field, None)
                        _r = _requests.post(_url, json=payload, headers=headers, stream=True, timeout=120)
                    if _r.status_code != 200:
                        _r.close()
                        if attempt == len(_candidates) - 1:
                            ok = False
                            err = 'HTTP %d (model %s)' % (_r.status_code, m)
                            yield 'data: ' + _json.dumps({'type': 'error', 'msg': 'AI 服务暂时不可用，请稍后再试'}, ensure_ascii=False) + '\n\n'
                            return
                        continue
                    resp = _r
                    used_model = m
                    break
                except Exception:
                    if attempt == len(_candidates) - 1:
                        raise
                    continue
            if resp is None:
                ok = False
                err = 'all models failed'
                yield 'data: ' + _json.dumps({'type': 'error', 'msg': 'AI 服务暂时不可用，请稍后再试'}, ensure_ascii=False) + '\n\n'
                return
            for line in resp.iter_lines():
                if not line:
                    continue
                s = line.decode('utf-8', errors='replace')
                if not s.startswith('data:'):
                    continue
                payload_str = s[5:].strip()
                if payload_str == '[DONE]':
                    break
                try:
                    obj = _json.loads(payload_str)
                    choices = obj.get('choices') or []
                    if choices:
                        delta = choices[0].get('delta') or {}
                        # 推理模型（DeepSeek）的思考过程走 reasoning_content，正文才走 content。
                        # 开着「思考」时把思考也推给前端，否则气泡会空转十几秒。
                        think = delta.get('reasoning_content') or ''
                        if think and ai_thinking:
                            yield 'data: ' + _json.dumps({'type': 'thinking', 'content': think}, ensure_ascii=False) + '\n\n'
                        content = delta.get('content') or ''
                        if content:
                            full.append(content)
                            yield 'data: ' + _json.dumps({'type': 'delta', 'content': content}, ensure_ascii=False) + '\n\n'
                    usage = obj.get('usage')
                    if usage:
                        prompt_tokens = usage.get('prompt_tokens', prompt_tokens)
                        completion_tokens = usage.get('completion_tokens', completion_tokens)
                except Exception:
                    continue
            resp.close()
            yield 'data: ' + _json.dumps({'type': 'done'}, ensure_ascii=False) + '\n\n'
        except GeneratorExit:
            raise
        except Exception as e:
            ok = False
            err = str(e)[:200]
            yield 'data: ' + _json.dumps({'type': 'error', 'msg': '网络异常，请重试'}, ensure_ascii=False) + '\n\n'
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            _release_concurrent()
            model_done(used_model, ok)
            if used_model != model:
                model_done(model, ok)
            try:
                ans = ''.join(full)
                conn = _sq.connect(DB_PATH)
                if ans:
                    conn.execute(
                        "INSERT INTO ai_chat_history (user_id, role, content, model, prompt_tokens, completion_tokens, created_at) VALUES (?,?,?,?,?,?,?)",
                        (me['id'], 'assistant', ans, used_model, prompt_tokens, completion_tokens, time.time()))
                conn.execute(
                    "INSERT INTO ai_logs (user_id, model, prompt_tokens, completion_tokens, success, "
                    "error, created_at, module, mode, latency_ms, ip, question, answer) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (me['id'], model, prompt_tokens, completion_tokens, 1 if ok else 0, err,
                     time.time(), 'chat', _mode_key, int((time.time() - _t0) * 1000),
                     _ip or '', user_content[:2000], ans[:2000]))
                conn.commit()
                conn.close()
            except Exception as e:
                import sys as _s
                print('[ai] finally save error:', e, file=_s.stderr)

    return Response(_gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    })


def _ai_log(db, user_id, model, pt, ct, success, err='', module='', mode='',
            latency_ms=0, ip='', question='', answer=''):
    """写一条 AI 用量明细。

    module: 'chat'（对话助手）/ 'debug'（代码调试）
    ⚠️ 这个函数**不该抛异常**：记录失败不能把用户的正常请求带崩 ✗
    """
    try:
        db.execute(
            "INSERT INTO ai_logs (user_id, model, prompt_tokens, completion_tokens, success, "
            "error, created_at, module, mode, latency_ms, ip, question, answer) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, model, pt, ct, 1 if success else 0, (err or '')[:200], time.time(),
             module, mode, int(latency_ms or 0), (ip or '')[:64],
             (question or '')[:2000], (answer or '')[:2000]))
        db.commit()
    except Exception:
        pass


def _ai_ip():
    """取请求方 IP（记录用，取不到就空串）。"""
    try:
        return request.headers.get('X-Forwarded-For', '').split(',')[0].strip() \
            or request.remote_addr or ''
    except Exception:
        return ''


AI_LOG_PAGE_SIZE = 50


# ══════════════════════════════════════════════════════════════
# AI 调代码 · 兑换码（2026-10-17）
# ══════════════════════════════════════════════════════════════

def _ai_code_ok(db, row, uid):
    """这个码现在还能不能被 uid 兑换。返回 (ok, why)。

    有效期格式 YYYY-MM-DD，空串 = 不限。
    valid_until 当天仍然有效（到当天 23:59:59）。
    """
    import datetime as _dt
    if not row:
        return False, '兑换码不存在'
    vf = (row['valid_from'] or '').strip()
    vu = (row['valid_until'] or '').strip()
    today = _dt.date.today()
    try:
        if vf and today < _dt.date.fromisoformat(vf):
            return False, '这个兑换码还没到生效时间（%s 起）' % vf
    except ValueError:
        pass
    try:
        if vu and today > _dt.date.fromisoformat(vu):
            return False, '这个兑换码已过期（%s 止）' % vu
    except ValueError:
        pass
    if row['used_count'] >= row['max_uses']:
        return False, '这个兑换码已经被领完了'
    if db.execute("SELECT 1 FROM ai_redeem_used WHERE code_id=? AND user_id=?",
                  (row['id'], uid)).fetchone():
        return False, '你已经兑换过这个码了'
    return True, ''


@app.route("/api/ai/redeem", methods=["POST"])
@login_required
def api_ai_redeem():
    me = current_user()
    db = get_db()
    _ai_code_tables(db)
    code = (request.json.get("code") if request.is_json else request.form.get("code")) or ""
    code = str(code).strip().upper()[:32]
    if not code:
        return jsonify({"ok": False, "msg": "请输入兑换码"}), 400

    row = db.execute("SELECT * FROM ai_redeem_codes WHERE code=?", (code,)).fetchone()
    ok, why = _ai_code_ok(db, row, me["id"])
    if not ok:
        return jsonify({"ok": False, "msg": why}), 400

    # 053：兑换码充值到 AI 广场余额（amount 元）
    amount = float(row["amount"] or 0) if "amount" in row.keys() else 0.0
    if amount <= 0:
        return jsonify({"ok": False, "msg": "这个兑换码无效（未设置金额）"}), 400
    db.execute("UPDATE users SET ai_balance = COALESCE(ai_balance, 0) + ? WHERE id=?",
               (amount, me["id"]))
    db.execute("UPDATE ai_redeem_codes SET used_count=used_count+1 WHERE id=?", (row["id"],))
    db.execute("INSERT INTO ai_redeem_used (code_id, user_id, created_at) VALUES (?,?,?)",
               (row["id"], me["id"], time.time()))
    db.commit()
    _log_mod.event("ai_code_redeem", user=me["username"], code=code, amount=amount)
    newbal = db.execute("SELECT ai_balance FROM users WHERE id=?", (me["id"],)).fetchone()["ai_balance"]
    return jsonify({"ok": True, "msg": "兑换成功，余额 +¥%.2f" % amount,
                    "balance": round(float(newbal or 0), 2)})


# ── 后台：服务器状态（超管）────────────────────────────────────
@app.route("/kanban")
@admin_required
def kanban():
    me = current_user()
    return render_template("kanban.html", user=me)


def _day_bucket(start_ts, n=14):
    """生成最近 n 天的 (日期标签, 当天0点时间戳) 列表（由旧到新）。"""
    import datetime as _dt
    out = []
    now = time.time()
    today_mid = int(now) - (int(now) % 86400)
    for i in range(n - 1, -1, -1):
        day_ts = today_mid - i * 86400
        d = _dt.datetime.fromtimestamp(day_ts)
        out.append((d.strftime('%m-%d'), day_ts))
    return out


@app.route("/api/kanban/stats")
@admin_required
def kanban_stats():
    """看板统计数据：总量卡片 + 近 14 天趋势 + 各模块分布。"""
    db = get_db()
    now = time.time()
    today_start = int(now) - (int(now) % 86400)

    def count(tbl, cond=''):
        q = "SELECT COUNT(*) c FROM %s" % tbl
        if cond: q += " WHERE " + cond
        return db.execute(q).fetchone()['c']

    cards = {
        'users': count('users'),
        'today_users': count('users', 'created_at >= %d' % today_start),
        'messages': count('messages'),
        'today_messages': count('messages', 'created_at >= %d' % today_start),
        'posts': count('posts'),
        'today_posts': count('posts', 'created_at >= %d' % today_start),
        'comments': count('post_comments'),
        'dynamics': count('dynamics'),
        'groups': count('groups'),
        'activities': count('activities'),
        'ai_calls': count('ai_logs'),
        'ai_tokens': db.execute("SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0) s FROM ai_logs").fetchone()['s'],
        'reports': count('reports'),
        'punishments': count('punishment_records'),
        'online_7d': count('users', 'last_active >= %d' % (now - 7*86400)),
    }

    days = _day_bucket(now)
    trend = {'labels': [d[0] for d in days]}
    for key, tbl, col in [
        ('users', 'users', 'created_at'),
        ('messages', 'messages', 'created_at'),
        ('posts', 'posts', 'created_at'),
        ('dynamics', 'dynamics', 'created_at'),
        ('ai', 'ai_logs', 'created_at'),
    ]:
        vals = []
        for label, day_ts in days:
            end = day_ts + 86400
            c = db.execute(
                "SELECT COUNT(*) c FROM %s WHERE %s >= ? AND %s < ?" % (tbl, col, col),
                (day_ts, end)).fetchone()['c']
            vals.append(c)
        trend[key] = vals

    rooms = [dict(r) for r in db.execute(
        "SELECT id, name, created_at FROM chat_rooms ORDER BY id LIMIT 10").fetchall()]
    sections = [dict(s) for s in db.execute(
        "SELECT id, name FROM forum_sections ORDER BY sort_order LIMIT 20").fetchall()]
    sec_posts = {}
    for s in db.execute("SELECT section_id, COUNT(*) c FROM posts GROUP BY section_id").fetchall():
        sec_posts[s['section_id']] = s['c']
    ai_model_dist = [dict(r) for r in db.execute(
        "SELECT model, COUNT(*) c, SUM(prompt_tokens+completion_tokens) tokens FROM ai_logs "
        "WHERE model!='' GROUP BY model ORDER BY c DESC LIMIT 10").fetchall()]
    game_stats = {}
    for f in ['high_score_24', 'high_score_sudoku', 'high_score_klotski',
              'high_score_color_blind', 'high_score_jump', 'high_score_xianyi',
              'high_score_hanoi']:
        game_stats[f.replace('high_score_', '')] = db.execute(
            "SELECT COUNT(*) c FROM users WHERE %s > 0" % f).fetchone()['c']
    top_posts = [dict(r) for r in db.execute(
        "SELECT id, title, view_count, likes, comment_count FROM posts "
        "ORDER BY (view_count + likes*5 + comment_count*10) DESC LIMIT 10").fetchall()]

    return jsonify({
        "ok": True,
        "cards": cards,
        "trend": trend,
        "rooms": rooms,
        "sections": sections,
        "sec_posts": sec_posts,
        "ai_model_dist": ai_model_dist,
        "game_stats": game_stats,
        "top_posts": top_posts,
    })


@app.route("/api/kanban/create", methods=["POST"])
@login_required
def kanban_create():
    """发布看板卡片（管理员/超管）。"""
    me = current_user()
    if not (me['is_admin'] or me['is_super_admin']):
        return jsonify({"ok": False, "msg": "无权限"}), 403
    title = (request.form.get("title") or "").strip()[:50]
    content = (request.form.get("content") or "").strip()
    if not title and not content:
        return jsonify({"ok": False, "msg": "标题和内容不能都为空"})
    is_pinned = 1 if request.form.get("is_pinned") else 0
    db = get_db()
    if not repeat_guard("kanban_create", [title, content, is_pinned], window=5.0, uid=me['id']):
        return jsonify({"ok": False, "msg": "这张卡片刚刚已经发布了"})
    cur = db.execute(
        "INSERT INTO kanban_items (title, content, author_id, author_name, is_pinned, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (title, content, me['id'], me['username'], is_pinned, time.time(), time.time()))
    db.commit()
    add_admin_log(me, "kanban_create", cur.lastrowid, title[:30])
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.route("/api/kanban/<int:kid>/update", methods=["POST"])
@login_required
def kanban_update(kid):
    """编辑看板卡片（管理员/超管）。"""
    me = current_user()
    if not (me['is_admin'] or me['is_super_admin']):
        return jsonify({"ok": False, "msg": "无权限"}), 403
    item = get_db().execute("SELECT * FROM kanban_items WHERE id=?", (kid,)).fetchone()
    if not item:
        return jsonify({"ok": False, "msg": "卡片不存在"}), 404
    title = (request.form.get("title") if request.form.get("title") is not None else item['title']).strip()[:50]
    content = (request.form.get("content") if request.form.get("content") is not None else item['content']).strip()
    is_pinned = 1 if request.form.get("is_pinned") else item['is_pinned']
    db = get_db()
    db.execute(
        "UPDATE kanban_items SET title=?, content=?, is_pinned=?, updated_at=? WHERE id=?",
        (title, content, is_pinned, time.time(), kid))
    db.commit()
    add_admin_log(me, "kanban_update", kid, title[:30])
    return jsonify({"ok": True})


@app.route("/api/kanban/<int:kid>/delete", methods=["POST"])
@login_required
def kanban_delete(kid):
    """删除看板卡片（管理员/超管）。"""
    me = current_user()
    if not (me['is_admin'] or me['is_super_admin']):
        return jsonify({"ok": False, "msg": "无权限"}), 403
    item = get_db().execute("SELECT * FROM kanban_items WHERE id=?", (kid,)).fetchone()
    if not item:
        return jsonify({"ok": False, "msg": "卡片不存在"}), 404
    db = get_db()
    db.execute("DELETE FROM kanban_items WHERE id=?", (kid,))
    db.commit()
    add_admin_log(me, "kanban_delete", kid, (item['title'] or '')[:30])
    return jsonify({"ok": True})


_ENGINE_GC_INTERVAL = 60
_ENGINE_ABANDON_SEC = 60 * 60
_WAITING_STALE_SEC = 48 * 3600


# 引擎二进制名（用于识别孤儿进程）
_ENGINE_BINARIES = ("pikafish", "katago", "pbrain-rapfi", "egaroucid", "gnugo")


def _sweep_orphan_engines():
    """杀掉父进程不是本进程的引擎子进程（泄漏的孤儿）。

    正常的引擎进程由 wsgi 主进程 fork，ppid 就是主进程 pid。
    一旦 ppid 变成 1（被 init 收养）或者别的进程，说明启动它的父进程已经死了，
    这个引擎再也没人管 —— 它会一直阻塞在读 stdin，白占 PID 和内存。

    2026-11-01 实测：线上有 4 个 pikafish 孤儿跑了 15 天（测试脚本留下的），
    现有 _engine_gc_pass 只管「数据库里还有房间」的引擎，管不到这种。

    只认 argv[0] 的 basename，避免误杀命令行里带引擎名的 bash 测试脚本。
    """
    me = os.getpid()
    killed = []
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except Exception:
        return killed
    for pid_s in pids:
        pid = int(pid_s)
        if pid == me:
            continue
        try:
            with open("/proc/%s/cmdline" % pid_s, "rb") as f:
                argv0 = f.read().split(b"\x00")[0].decode("utf-8", "ignore")
        except Exception:
            continue
        if not argv0:
            continue
        base = os.path.basename(argv0).lower()
        if not any(b in base for b in _ENGINE_BINARIES):
            continue
        try:
            with open("/proc/%s/stat" % pid_s, "r") as f:
                ppid = int(f.read().split()[3])
        except Exception:
            continue
        if ppid == me:
            continue                      # 自己 fork 的，正常
        try:
            os.kill(pid, 9)   # SIGKILL
            killed.append(pid)
            _log_mod.event("engine_orphan_killed", pid=pid, name=base, ppid=ppid)
        except Exception:
            pass
    return killed


def _engine_gc_pass():
    """回收一次：只处理确实长时间无活动的房间，副作用可控。"""
    now = time.time()
    conn = None
    try:
        conn = _tune_conn(sqlite3.connect(DB_PATH, timeout=10))
        conn.row_factory = sqlite3.Row
        for tbl, close_fn in (("gomoku_rooms", _rapfi_close_room),
                              ("go_rooms", _katago_close_room),
                              ("xq_rooms", board.xq._pikafish_close_room),
                              ("chess_rooms", _chess_close),
                              ("jq_rooms", None)):
            try:
                rows = conn.execute(
                    "SELECT id FROM %s WHERE status='playing' AND updated_at < ?" % tbl,
                    (now - _ENGINE_ABANDON_SEC,)).fetchall()
                for r in rows:
                    if close_fn is not None:
                        try:
                            close_fn(r['id'])
                        except Exception:
                            pass
                    try:
                        conn.execute(
                            "UPDATE %s SET status='finished', winner_id=NULL, updated_at=? WHERE id=?" % tbl,
                            (now, r['id']))
                    except Exception:
                        pass
                if rows:
                    conn.commit()
            except Exception:
                pass
        for tbl in ("gomoku_rooms", "go_rooms", "xq_rooms", "chess_rooms", "jq_rooms", "reversi_rooms"):
            try:
                cur = conn.execute(
                    "DELETE FROM %s WHERE status='waiting' AND guest_id IS NULL AND updated_at < ?" % tbl,
                    (now - _WAITING_STALE_SEC,))
                if cur.rowcount:
                    conn.commit()
            except Exception:
                pass
        try:
            idle_keys = [k for k, ts in list(board.xq._XQP_ENGINE_LAST.items())
                         if k.startswith('xqp_') and now - ts > 30 * 60]
            for k in idle_keys:
                try:
                    board.xq._pikafish_close_room(k)
                except Exception:
                    pass
                try:
                    board.xq._XQP_ENGINE_LAST.pop(k, None)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _engine_gc_loop():
    while True:
        try:
            time.sleep(_ENGINE_GC_INTERVAL)
            _engine_gc_pass()
            _sweep_orphan_engines()   # 顺带清理父进程已死的孤儿引擎
        except Exception:
            time.sleep(_ENGINE_GC_INTERVAL)


def _start_engine_gc():
    """启动弃局回收后台线程（守护线程，随主进程退出）。"""
    try:
        t = threading.Thread(target=_engine_gc_loop, daemon=True, name="engine-gc")
        t.start()
        print(" [引擎回收] 弃局房间回收线程已启动（每 %ds 扫描一次）" % _ENGINE_GC_INTERVAL)
    except Exception:
        pass


def _print_engine_report():
    """打印四大 AI 引擎可用性报告（探测在 import 时已真实启动握手验证）。

    不可用时区分「文件缺失」与「引擎起不来」，并提示对局会自动降级内置 AI。
    """
    try:
        def _rel(p):
            if not p:
                return ''
            try:
                return os.path.relpath(p, BASE_DIR)
            except Exception:
                return str(p)
        items = (
            ("国际象棋", "Stockfish", board.chess._STOCKFISH_AVAILABLE, board.chess._STOCKFISH_EXE),
            ("中国象棋", "Pikafish", board.xq._PIKAFISH_AVAILABLE, board.xq._PIKAFISH_EXE),
            ("围棋", "KataGo" if board.go._KATAGO_AVAILABLE else ("GNU Go 兜底" if board.go._GNUGO_AVAILABLE else "无可用引擎"),
             board.go._GO_AI_AVAILABLE,
             board.go._KATAGO_EXE if board.go._KATAGO_AVAILABLE else board.go._GNUGO_EXE),
            ("五子棋", "RAPFI", board.gomoku._RAPFI_AVAILABLE, board.gomoku._RAPFI_EXE),
            ("黑白棋", "Egaroucid", board.reversi._EGAROUCID_AVAILABLE, board.reversi._EGAROUCID_EXE),
        )
        print(" 游戏引擎检测（PvAI 对局用）：")
        for gname, ename, avail, exe in items:
            if avail:
                print("   ✓ %s AI（%s）   可用    %s" % (gname, ename, _rel(exe)))
            else:
                if exe and not os.path.isfile(exe):
                    print("   ✗ %s AI（%s）   不可用  引擎文件缺失（%s），请检查是否拷贝完整" % (gname, ename, _rel(exe)))
                else:
                    print("   ✗ %s AI（%s）   不可用  引擎无法启动（常见：32 位系统 / 缺 VC 运行库），已自动降级为内置 AI" % (gname, ename))
        print("   （标 ✗ 的引擎对应 PvAI 对局将用内置 AI 顶替，PvP 不受影响）")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════

# ---------------- 051 独立 IM 模块初始化 ----------------
# 把 app.py 的 helper 注入 chat 包（依赖注入，避免循环 import），
# 由 chat/__init__.init 完成路由与 SocketIO 事件注册。
chat.init(app, socketio, deps={
    "app": app,
    "socketio": socketio,
    "url_for": url_for,
    "login_required": login_required,
    "current_user": current_user,
    "get_db": get_db,
    "get_user_by_id": get_user_by_id,
    "get_user_by_name": get_user_by_name,
    "has_blocked": has_blocked,
    "are_friends": are_friends,
    "can_private_chat": can_private_chat,
    "add_notification": add_notification,
    "add_admin_log": add_admin_log,
    "fmt_cn": fmt_cn,
    "allowed_file": allowed_file,
    "is_image": is_image,
    "is_video": is_video,
    "sanitize_display_name": sanitize_display_name,
    "ext_reject_msg": ext_reject_msg,
    "_save_upload_image": _save_upload_image,
    "_compress_video": _compress_video,
    "filter_and_block": filter_and_block,
    "repeat_guard": repeat_guard,
    "task_inc": task_inc,
    "behavior_points": behavior_points,
    "add_exp": add_exp,
    "get_avatar_url": get_avatar_url,
    "get_user_effect": get_user_effect,
    "get_consumable_count": get_consumable_count,
    "consume_item": consume_item,
    "get_room": get_room,
    "can_recall": can_recall,
    "flood_check": flood_check,
    "is_active_restriction": is_active_restriction,
    "status_text": status_text,
    "extract_mentions": extract_mentions,
    "parse_mentions": parse_mentions,
    "rate_allow": rate_allow,
    "serialize_msg": serialize_msg,
    "_row_get": _row_get,
    "_log_warning": _log_warning,
    "TITLE_COLORS": TITLE_COLORS,
})

games.init(app, deps={
    "login_required": login_required,
    "super_admin_required": super_admin_required,
    "current_user": current_user,
    "get_db": get_db,
    "get_config": get_config,
    "get_user_by_id": get_user_by_id,
    "get_user_by_name": get_user_by_name,
    "add_notification": add_notification,
    "add_admin_log": add_admin_log,
    "add_exp": add_exp,
    "task_inc": task_inc,
    "behavior_points": behavior_points,
    "_ach_after_submit": _ach_after_submit,
    "cache": cache,
    "fmt_cn": fmt_cn,
    "filter_and_block": filter_and_block,
    "is_active_restriction": is_active_restriction,
    "status_text": status_text,
    "get_avatar_url": get_avatar_url,
    "get_user_effect": get_user_effect,
    "_ach_after_submit": _ach_after_submit,
    "_score_gate": _score_gate,
    "_submit_score_common": _submit_score_common,
    "_oj_super": ojweb.core._oj_super,
    "_fmt_ms": _fmt_ms,
})

board.init(app, deps={
    "login_required": login_required,
    "current_user": current_user,
    "get_db": get_db,
    "socketio": socketio,
    "app": app,
    "_LOW_MEM": _LOW_MEM,
    "_ai_ms_for": _ai_ms_for,
    "_ai_depth_for": _ai_depth_for_room,
    "_ai_level_of_room": _ai_level_of,
    "_ai_survival_leaderboard": _ai_survival_leaderboard,
    "_award_game_stats": _award_game_stats,
    "_proc_stop": _proc_stop,
    "_game_socket_join": _game_socket_join,
    "og_context": og_context,
    "repeat_guard": repeat_guard,
    "_mm_fp": _mm_fp,
    "_mm_ip": _mm_ip,
    "_mm_is_new": _mm_is_new,
})



xqm.init(app, deps={
    "login_required": login_required,
    "current_user": current_user,
    "get_db": get_db,
    "socketio": socketio,
    "app": app,
    "DB_PATH": DB_PATH,
    "TITLE_COLORS": TITLE_COLORS,
    "_LOW_MEM": _LOW_MEM,
    "_ach_after_submit": _ach_after_submit,
    "_ai_ms_for": _ai_ms_for,
    "_ai_depth_for": _ai_depth_for_room,
    "_ai_level_of_room": _ai_level_of,
    "_ai_survival_leaderboard": _ai_survival_leaderboard,
    "_award_game_stats": _award_game_stats,
    "_boot_migrate": _boot_migrate,
    "_compress_video": _compress_video,
    "_fmt_ms": _fmt_ms,
    "_game_socket_join": _game_socket_join,
    "_log_mod": _log_mod,
    "_log_warning": _log_warning,
    "_mm_fp": _mm_fp,
    "_mm_ip": _mm_ip,
    "_mm_is_new": _mm_is_new,
    "_oj_super": ojweb.core._oj_super,
    "_print_engine_report": _print_engine_report,
    "_proc_stop": _proc_stop,
    "_row_get": _row_get,
    "_save_upload_image": _save_upload_image,
    "_score_gate": _score_gate,
    "_start_engine_gc": _start_engine_gc,
    "_submit_score_common": _submit_score_common,
    "_tune_conn": _tune_conn,
    "_activity": _activity,
    "add_admin_log": add_admin_log,
    "add_exp": add_exp,
    "add_notification": add_notification,
    "allowed_file": allowed_file,
    "are_friends": are_friends,
    "behavior_points": behavior_points,
    "cache": cache,
    "can_private_chat": can_private_chat,
    "can_recall": can_recall,
    "consume_item": consume_item,
    "ext_reject_msg": ext_reject_msg,
    "extract_mentions": extract_mentions,
    "filter_and_block": filter_and_block,
    "flood_check": flood_check,
    "fmt_cn": fmt_cn,
    "get_avatar_url": get_avatar_url,
    "get_config": get_config,
    "get_consumable_count": get_consumable_count,
    "get_room": get_room,
    "get_user_by_id": get_user_by_id,
    "get_user_by_name": get_user_by_name,
    "get_user_effect": get_user_effect,
    "has_blocked": has_blocked,
    "is_active_restriction": is_active_restriction,
    "is_image": is_image,
    "is_video": is_video,
    "og_context": og_context,
    "parse_mentions": parse_mentions,
    "rate_allow": rate_allow,
    "repeat_guard": repeat_guard,
    "repeat_release": repeat_release,
    "sanitize_display_name": sanitize_display_name,
    "serialize_msg": serialize_msg,
    "status_text": status_text,
    "task_inc": task_inc,
})

ojweb.init(app, deps={
    "login_required": login_required,
    "current_user": current_user,
    "get_db": get_db,
    "fmt_cn": fmt_cn,
    "og_context": og_context,
    "repeat_guard": repeat_guard,
    "DB_PATH": DB_PATH,
    "_TZ_CN": _TZ_CN,
    "_tune_conn": _tune_conn,
    "app": app,
})

adminpanel.init(app, deps={
    "login_required": login_required,
    "admin_required": admin_required,
    "super_admin_required": super_admin_required,
    "current_user": current_user,
    "get_db": get_db,
    "get_config": get_config,
    "set_config": set_config,
    "get_user_by_id": get_user_by_id,
    "get_user_by_name": get_user_by_name,
    "get_tasks_data": get_tasks_data,
    "add_admin_log": add_admin_log,
    "add_notification": add_notification,
    "add_exp": add_exp,
    "award": award,
    "consume_item": consume_item,
    "get_consumable_count": get_consumable_count,
    "level_for_exp": level_for_exp,
    "status_text": status_text,
    "hash_password": hash_password,
    "reload_sensitive_cache": reload_sensitive_cache,
    "_row_get": _row_get,
    "_add_column": _add_column,
    "_ach": _ach,
    "_activity": _activity,
    "_monitor": _monitor,
    "_log_mod": _log_mod,
    "log": log,
    "socketio": socketio,
    "app": app,
    "DB_PATH": DB_PATH,
    "BASE_DIR": BASE_DIR,
    "_CODE_DIR": _CODE_DIR,
    "ONLINE_THRESHOLD": ONLINE_THRESHOLD,
    "PERMANENT": PERMANENT,
    "REPORT_CATEGORIES": REPORT_CATEGORIES,
    "AI_LOG_PAGE_SIZE": AI_LOG_PAGE_SIZE,
    "MUSIC_FOLDER": MUSIC_FOLDER,
    "ABOUT_BLOCKS": ABOUT_BLOCKS,
    "CHANGELOG_BLOCKS": CHANGELOG_BLOCKS,
    "about_blocks": about_blocks,
    "changelog_blocks": changelog_blocks,
    "changelog_entries": changelog_entries,
    "_SLOW_MS": _SLOW_MS,
})


@app.route("/api/ai/support", methods=["POST"])
def ai_support_chat():
    """053 AI 客服（悬浮窗）：SSE 流式，HK 代理 LA 的 Hunyuan-0.5B。

    输入也要过审核（第①.5 广告规则 + ② cold 模型）——客服窗口不能成为
    绕过审核的后门。输出由 system prompt 约束（不泄敏感信息）。
    """
    _j = request.json if request.is_json else {}
    msg = (_j.get("message") or "").strip()
    history = _j.get("history") or []
    if not msg:
        return jsonify({"ok": False, "msg": "说点什么吧"}), 400
    if len(msg) > 500:
        return jsonify({"ok": False, "msg": "问题太长啦，精简到 500 字以内"}), 400

    # 输入审核：广告规则 + cold
    try:
        ad_hit, _why = _moderation.ad_check(msg)
        if ad_hit:
            return jsonify({"ok": False, "msg": "消息包含广告内容，已拦截"}), 400
        import cold_guard
        cg = cold_guard.judge(msg)
        # 客服场景要更宽容：cold 对短问句误判率高（实测「你是谁？」被判 0.72 bad，
        # 问号像挑衅），所以只拦「高置信度明确违规」——score≥0.85 且长度>12 字。
        # 正常提问宁可放过（GLM 有兜底），不能把「你是谁」这种问题拒之门外。
        if (cg.get("level") == "bad" and (cg.get("score") or 0) >= 0.85
                and len(msg) > 12):
            return jsonify({"ok": False, "msg": "消息包含不友善内容，换个说法吧"}), 400
    except Exception:
        pass

    import ai_support
    if not ai_support.available():
        return jsonify({"ok": False, "msg": "AI 客服暂时打盹了，稍后再试或去 /ai"}), 503

    # 053 深度嵌入：规则识别意图 → 执行工具 → 结果注入 prompt
    import ai_tools
    tool_note = ""
    action = None
    direct_reply = ""
    try:
        me = current_user()
        tool, args = ai_tools.detect(msg)
        if tool:
            res = ai_tools.run(tool, args, get_db(), me)
            if res.get("ok"):
                tool_note = res.get("say") or ""
                action = res.get("action")
                direct_reply = res.get("reply") or ""
    except Exception:
        pass

    # 053 知识库：关键词命中 → 相关文档全文注入 system（文档化 + 按需查阅）
    if not direct_reply:
        try:
            import knowledge
            keys = knowledge.match(msg)
            if keys:
                docs = knowledge.lookup(keys)
                if docs:
                    tool_note = (tool_note + "\n\n" if tool_note else "") + docs
        except Exception:
            pass

    # 工具命中 → 模板直出（0.5B 会无视工具结果瞎编，实测见 2026-10-24 日志）
    if direct_reply:
        def gen_direct():
            yield "data: " + json.dumps({"t": direct_reply}, ensure_ascii=False) + "\n\n"
            if action:
                yield "data: " + json.dumps({"action": action}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        return Response(gen_direct(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    msgs = ai_support.build_messages(history, msg, tool_note)

    def gen():
        try:
            for piece in ai_support.stream_chat(msgs):
                yield "data: " + json.dumps({"t": piece}, ensure_ascii=False) + "\n\n"
            if action:
                yield "data: " + json.dumps({"action": action}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        except Exception:
            yield "data: " + json.dumps({"t": "\n[出错了，稍后再试]"}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# 053：AI 审核结果回收线程（每 10s 从 Redis 收 LA 的判定结果，更新 ai_moderation）
_moderation.start_sweeper(app, get_db)


@app.route("/admin/ai-review")
@admin_required
def admin_ai_review():
    """053 审核第四层：人工复核队列。"""
    me = current_user()
    db = get_db()
    status = request.args.get("status") or "flagged"
    if status not in ("flagged", "hidden", "cleared", "pending", "reviewed"):
        status = "flagged"
    rows = _moderation.pending_list(db, status, 100)
    return render_template("admin_ai_review.html", rows=rows, status=status,
                           stats=_moderation.stats(db), qstats=_ai_queue_stats())


@app.route("/admin/ai-review/<int:mid>/<action>", methods=["POST"])
@admin_required
def admin_ai_review_action(mid, action):
    me = current_user()
    if action not in ("clear", "hide", "delete"):
        return redirect(url_for("admin_ai_review"))
    db = get_db()
    # 先取一份记录：驳回/删除要通知作者（原来只改状态，作者完全不知道）
    _rec = db.execute(
        "SELECT user_id, target_type, title, cold_why, llm_why "
        "FROM ai_moderation WHERE id=?", (mid,)).fetchone()
    _ok = _moderation.review(db, mid, action, me['id'])
    add_admin_log(me, "ai_review_" + action, "moderation:%d" % mid)

    # 062 收口：把审核结果告诉作者（只对 hide / delete，clear 不用打扰）
    if _ok and _rec and action in ("hide", "delete"):
        try:
            _uid = _rec["user_id"]
            if _uid:
                try:
                    _why = (_rec["cold_why"] or _rec["llm_why"] or "").strip()
                except Exception:
                    _why = ""
                _kind = "帖子" if _rec["target_type"] == "post" else "评论"
                _title = (_rec["title"] or "").strip()[:30]
                _verb = "已被删除" if action == "delete" else "已被隐藏"
                _content = ("你的%s《%s》未通过审核，%s。" % (_kind, _title, _verb)
                            if _title else "你的%s未通过审核，%s。" % (_kind, _verb))
                if _why:
                    _content += "原因：%s" % _why
                if action == "hide":
                    _content += "（修改后可联系管理员恢复）"
                add_notification(_uid, "system", _content, url_for("forum"))
        except Exception:
            pass

    flash("已处理", "ok")
    return redirect(url_for("admin_ai_review", status=request.form.get("back") or "flagged"))


def _ai_queue_stats():
    try:
        import ai_queue
        return ai_queue.stats()
    except Exception:
        return {}


if __name__ == "__main__":
    import argparse
    import sys
    with app.app_context():
        _boot_migrate()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true",
                        help="开发调试模式：使用 Flask 调试服务器（仅建议本机使用）")
    args = parser.parse_args()
    print("=" * 60)
    print(" MONO 社区 v6.7 已启动（局域网模式）")
    print(" 本机访问：http://127.0.0.1:%d" % args.port)
    if args.host in ("0.0.0.0", "", "::"):
        print(" 局域网访问：http://<本机IP>:%d（同一 WiFi/局域网内的设备可访问）" % args.port)
    else:
        print(" 局域网访问：http://%s:%d" % (args.host, args.port))
    try:
        with app.app_context():
            _wc = get_db().execute("SELECT COUNT(*) as c FROM sensitive_words").fetchone()['c']
        print(" 敏感词库已加载：%d 个" % _wc)
    except Exception:
        print(" 敏感词库：待初始化（0 个，管理端可导入词条）")
    _print_engine_report()
    print("=" * 60)
    _start_engine_gc()
    if args.debug:
        socketio.run(app, host=args.host, port=args.port, debug=True, use_reloader=False)
    else:
        try:
            from waitress import serve as _waitress_serve
        except ImportError:
            print("[!] 未安装 waitress，改用 Flask 内置服务器（并发能力有限）")
            socketio.run(app, host=args.host, port=args.port, debug=False, use_reloader=False)
        else:
            _waitress_serve(socketio.sockio_mw,
                            host=args.host, port=args.port,
                            threads=64, channel_timeout=60,
                            clear_untrusted_proxy_headers=False)
