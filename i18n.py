# -*- coding: utf-8 -*-
"""i18n.py — 094 国际化与多语言（界面层）

范围（和规划文档一致）
----------------------
只做**界面文案**：导航、按钮、表单标签、空状态、错误提示。
帖子正文、用户名、游戏名、AI 回复这些**用户内容一律不翻** ——
机器翻译质量不可控，而且改的是别人的话。

为什么不用 Flask-Babel / gettext
--------------------------------
本项目 142 个模板、4.8 万行。Babel 那套（extract → .po → compile）
要求把中文当 msgid 写进模板，等于模板里永远留着中文、改文案要动两处，
还要多一个编译步骤和 .mo 二进制文件进仓库。
这里用最土但最好维护的办法：**点分 key + 每语言一个 JSON**。
模板里写 `{{ t('nav.home') }}`，`i18n/en.json` 里一行一个 key。

回退链
------
当前语言 → 中文 → key 本身。
任何一层缺了都不会白屏，最差你会在页面上看到 `nav.home` 这种字样 ——
丑，但比 500 好，而且一眼能看出漏了哪个 key。

新增语言
--------
1. 复制 `i18n/zh.json` 成 `i18n/xx.json`，翻译 value；
2. 在 LANGS 里加一行 `"xx": "显示名"`。
不需要改任何模板。
"""
import json
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
I18N_DIR = os.path.join(BASE_DIR, "i18n")

# 语言代码 → 下拉里显示的名字。第一项是默认语言。
LANGS = {
    "zh": "简体中文",
    "en": "English",
}
DEFAULT_LANG = "zh"

_cache = {}          # lang -> (mtime, dict)
_MISS = object()
_missing_keys = set()


def _load(lang):
    """读语言文件，按 mtime 缓存（改 JSON 不用重启）。"""
    path = os.path.join(I18N_DIR, "%s.json" % lang)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    hit = _cache.get(lang)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _cache[lang] = (mtime, data)
    return data


def normalize(lang):
    """把 en-US / EN / zh-CN 这类写法收敛成受支持的语言码。"""
    if not lang:
        return None
    code = str(lang).strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGS else None


def current_lang():
    """当前语言：session → cookie → 默认。

    ⚠️ 故意**不按 Accept-Language 自动切英文** —— 站内 73 个用户全中文，
    浏览器是英文的中国人不少，自动切过去会让人以为站坏了。
    想换语言请自己点右上角的切换。
    """
    try:
        from flask import has_request_context, request, session
        if not has_request_context():
            return DEFAULT_LANG
        lang = normalize(session.get("lang"))
        if lang:
            return lang
        lang = normalize(request.cookies.get("lang"))
        if lang:
            return lang
    except Exception:
        pass
    return DEFAULT_LANG


def tr(key, **kw):
    """取一条文案。缺 key 时回退中文，再回退 key 本身。

    ⚠️ 名字故意不叫 `t` —— 模板里 `{% for t in tasks %}` 这种循环变量到处都是
    （14 个模板这么写），一旦局部变量盖住全局函数，`{{ t('x') }}` 会变成
    "dict object is not callable"，直接 500。用 `tr` 避开。
    """
    lang = current_lang()
    val = _load(lang).get(key, _MISS)
    if val is _MISS:
        val = _load(DEFAULT_LANG).get(key, _MISS)
    if val is _MISS:
        if key not in _missing_keys:
            _missing_keys.add(key)
        return key
    if kw:
        try:
            return str(val).format(**kw)
        except Exception:
            return str(val)
    return str(val)


# ---------------------------------------------------------------- 格式化
def fmt_date(ts, style="date"):
    """按语言格式化时间。style: date / datetime / short。"""
    if ts is None or ts == "":
        return ""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return str(ts)
    lt = time.localtime(ts)
    lang = current_lang()
    if lang == "en":
        if style == "datetime":
            return time.strftime("%b %d, %Y %H:%M", lt)
        if style == "short":
            return time.strftime("%b %d %H:%M", lt)
        return time.strftime("%b %d, %Y", lt)
    if style == "datetime":
        return time.strftime("%Y-%m-%d %H:%M", lt)
    if style == "short":
        return time.strftime("%m-%d %H:%M", lt)
    return time.strftime("%Y-%m-%d", lt)


def fmt_num(n):
    """数字千分位。中文和英文都用逗号分组，这里统一处理。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return "{:,}".format(n)


def fmt_rel(ts):
    """相对时间：刚刚 / 3 分钟前 / 2 小时前 …（英文：just now / 3m ago）"""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    d = int(time.time() - ts)
    if d < 60:
        return tr("time.just_now")
    if d < 3600:
        return tr("time.min_ago", n=d // 60)
    if d < 86400:
        return tr("time.hour_ago", n=d // 3600)
    if d < 86400 * 30:
        return tr("time.day_ago", n=d // 86400)
    return fmt_date(ts)


# ---------------------------------------------------------------- 接线
def register(app):
    """挂到 Flask 上：模板全局函数 + 切换路由 + 上下文变量。"""
    from flask import redirect, request, session

    app.jinja_env.globals["tr"] = tr
    app.jinja_env.globals["current_lang"] = current_lang
    app.jinja_env.globals["fmt_date"] = fmt_date
    app.jinja_env.globals["fmt_num"] = fmt_num
    app.jinja_env.globals["fmt_rel"] = fmt_rel

    @app.context_processor
    def _inject_lang():
        return {
            "LANG": current_lang(),
            "LANGS": LANGS,
            "js_dict": js_dict(),
        }

    @app.route("/lang/<code>")
    def set_lang(code):
        """切换语言后跳回原页面（只允许站内相对地址，防开放重定向）。"""
        code = normalize(code) or DEFAULT_LANG
        session["lang"] = code
        nxt = request.args.get("next") or request.referrer or "/"
        # 只接受站内相对路径：`//evil.com`、`/\evil.com`（浏览器会把反斜杠当斜杠）
        # 都算外链，一律打回首页。
        if (not nxt.startswith("/") or nxt.startswith("//")
                or "\\" in nxt or "\r" in nxt or "\n" in nxt):
            nxt = "/"
        resp = redirect(nxt)
        # 同时写 cookie：session 掉了语言也不丢
        resp.set_cookie("lang", code, max_age=3600 * 24 * 365, samesite="Lax")
        return resp

    return app


def langs():
    """当前支持的语言（给管理页/报告用）。"""
    return dict(LANGS)


def ai_lang_hint():
    """英文界面下给大模型的附加指令：一律用英文回答。

    为什么拼进 system 字符串、而不是单独加一条 system 消息：
    部分模型（尤其本地 Qwen 的 chat template）**只接受一条 system 且必须在最前**，
    多给一条直接 400。所以统一在原有 system 后面接一段。

    注意：只影响**模型输出**，不改用户提问原文 ——
    用户用中文问、但界面是英文时，模型会答英文，这是他要的效果。
    """
    if current_lang() != "en":
        return ""
    return ("\n\nThe user has switched this site's interface to English. "
            "Always reply in English, even if the question or the material above "
            "is written in Chinese. Only use another language when the user "
            "explicitly asks for it.")


def js_dict(lang=None):
    """JS 弹窗词典：**中文原文 → 英文**。

    模板里的 JS 写 `alert(__('网络异常'))`，`__()` 拿中文原文当 key 去查这张表。
    中文界面返回空 dict（`__()` 查不到就原样返回中文，正好是对的）。

    为什么不给中文也建一份：中文原文本身就是 key，identity 映射毫无意义，
    还会让每页多背十几 KB。
    """
    lang = lang or current_lang()
    if lang == DEFAULT_LANG:
        return {}
    path = os.path.join(I18N_DIR, "js_%s.json" % lang)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    hit = _cache.get("js:" + lang)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _cache["js:" + lang] = (mtime, data)
    return data
