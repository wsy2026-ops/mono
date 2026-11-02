# -*- coding: utf-8 -*-
"""ai_tools.py — AI 助手的工具层（053 深度嵌入）。

设计原则（2026-10-24 实测后的定论）：
  **0.5B 模型靠不住 —— 意图识别用规则，工具命中时回答用模板直出，不经过模型。**
  实测：工具结果完全正确（action 精确），但 Hunyuan-0.5B 会无视系统给的数据自己
  编（「音乐库里有《琵琶曲》的官方单曲《琵琶行》」「《琵琶行》是元曲经典」）。
  所以：工具命中 → 模板拼出成品回答（reply）+ action；模型只负责闲聊兜底。

流程：用户消息 → detect() 规则识别 → run() 执行 → 有 reply 就直接发（跳过模型）
     → 没有工具命中 → 走 Hunyuan 闲聊。
"""
import re

# 页面跳转映射
PAGES = {
    "论坛": "/forum", "帖子": "/forum", "社区": "/forum",
    "题库": "/oj/problems", "oj": "/oj", "判题": "/oj/problems", "比赛": "/oj/contests",
    "排行榜": "/ranking", "榜单": "/ranking", "排名": "/ranking",
    "象棋": "/games", "游戏": "/games", "小游戏": "/games",
    "音乐": "/music", "歌": "/music",
    "商城": "/mall", "积分商城": "/mall",
    "个人中心": "/profile", "个人主页": "/profile", "我的": "/profile",
    "积分": "/points", "签到": "/points",
    "成就": "/achievements", "勋章": "/achievements",
    "热榜": "/hot", "热门": "/hot",
    "ai": "/ai", "助手": "/ai",
    "首页": "/", "主页": "/",
}

_MUSIC_Q = [
    re.compile(r"(?:有没有|有|找|搜|查).{0,8}(?:歌|音乐|曲|歌名|曲子)"),
    re.compile(r"《([^》]{1,30})》"),
    re.compile(r"([^\s，。？！]{1,20})\s*(?:这首歌|这首曲子|那个歌)"),
    re.compile(r"(?:播放|放一首|放一下|来一首|来首|听一下|想听|我要听)\s*(.{0,20})"),
]
_POST_Q = [
    re.compile(r"(?:有没有人|有人).{0,6}(?:讨论|发|问|写|聊)"),
    re.compile(r"(?:搜|找|查).{0,8}(?:帖|文章|话题)"),
    re.compile(r"(?:关于|有关|相关)"),
]
_POINTS_Q = re.compile(r"(?:我|俺|自己).{0,4}(?:有多少|多少|几).{0,3}积?分|我的?积分")
_PROFILE_Q = re.compile(r"我的?(?:等级|勋章|成就|贡献|经验|排名)")
_GOTO_Q = re.compile(r"(?:打开|去|进入|看看|跳转到?)\s*(?:一下)?\s*(?:那个)?\s*(.{2,10})")


def _title_from(text):
    m = re.search(r"《([^》]{1,30})》", text)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"(?:有没有|有|找|搜|查|播放|放一首|来一首|想听)\s*(?:一首|一下)?\s*(.{1,20}?)"
        r"\s*(?:这首歌|这首曲子|的音乐|的曲子|吗|嘛|？|\?|$)", text)
    if m:
        return m.group(1).strip(" 的")
    return ""


def detect(text):
    """规则识别意图。返回 (tool, args_dict) 或 (None, {})。"""
    t = (text or "").strip()
    if not t:
        return None, {}
    if _POINTS_Q.search(t):
        return "my_points", {}
    if _PROFILE_Q.search(t):
        return "my_profile", {}
    for pat in _POST_Q:
        if pat.search(t):
            # 「X 相关的帖子」：X 在「相关」左边 —— 切分后剥疑问词比正则可靠
            m = None
            if "相关" in t and "关于" not in t and "有关" not in t:
                head = t.rsplit("相关", 1)[0]
                head = re.sub(r"^(?:.*?(?:有没有|请问|社区里|论坛里|里|有))", "", head)
                head = re.sub(r"^(?:有没有|有|问)", "", head).strip("，。？！ 的")
                if 2 <= len(head) <= 12:
                    kw = head
                    return "search_posts", {"keyword": kw}
            m = (re.search(
                    r"(?:关于|有关)\s*(.{2,20}?)\s*(?:的|吗|嘛|呢|？|\?|帖子|文章|$)", t)
                 or re.search(
                    r"(?:讨论|问|写|聊|搜|找|查)\s*(?:一下)?\s*(.{2,20}?)(?:的|吗|嘛|呢|？|\?|帖子|文章|$)", t))
            kw = m.group(1).strip() if m else _title_from(t)
            kw = re.sub(r"^(?:过|关于|有关|相关)", "", kw).strip()
            # 「有没有关于」里藏着「有关」→ 捕获组可能从「于/关」起头，剥掉
            kw = re.sub(r"^[于关的是和跟与]+", "", kw).strip()
            kw = re.sub(r"(帖子|文章|讨论)$", "", kw).strip()
            return "search_posts", {"keyword": kw}
    for pat in _MUSIC_Q:
        if pat.search(t):
            return "search_music", {"keyword": _title_from(t)}
    m = _GOTO_Q.search(t)
    if m:
        target = m.group(1).strip()
        for k, path in PAGES.items():
            if k in target:
                return "goto_page", {"label": k, "path": path}
    return None, {}


def _fmt_dur(sec):
    try:
        s = int(float(sec or 0))
        return "%d:%02d" % (s // 60, s % 60)
    except Exception:
        return ""


def run(tool, args, db, user=None):
    """执行工具。返回 dict：
    {"ok", "say": 原始结果, "reply": 模板直出的成品回答（有就跳过模型）,
     "action": 前端动作}
    """
    try:
        if tool == "search_music":
            return _search_music(db, args.get("keyword") or "")
        if tool == "search_posts":
            return _search_posts(db, args.get("keyword") or "")
        if tool == "my_points":
            return _my_points(db, user)
        if tool == "my_profile":
            return _my_profile(db, user)
        if tool == "goto_page":
            label = args.get("label")
            return {"ok": True,
                    "say": "页面已定位：%s" % label,
                    "reply": "好的，正在打开「%s」→ 点下面的按钮过去。" % label,
                    "action": {"type": "goto", "path": args.get("path"), "label": label}}
    except Exception as e:
        return {"ok": False, "say": "工具执行出错：%s" % str(e)[:60],
                "reply": "", "action": None}
    return {"ok": False, "say": "", "reply": "", "action": None}


def _search_music(db, kw):
    kw = (kw or "").strip()
    if not kw:
        rows = db.execute(
            "SELECT id, title, artist, duration FROM music_tracks ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if not rows:
            return {"ok": True, "say": "音乐库是空的",
                    "reply": "音乐库现在还是空的，可以去 /music 页面传几首。", "action": None}
        names = "、".join("《%s》" % r["title"] for r in rows)
        return {"ok": True, "say": "最近上传：" + names,
                "reply": "音乐库最近上传的是：%s。想听哪首直接说歌名。" % names,
                "action": None}

    rows = db.execute(
        "SELECT id, title, artist, duration FROM music_tracks "
        "WHERE title LIKE ? OR artist LIKE ? ORDER BY id DESC LIMIT 5",
        ("%" + kw + "%", "%" + kw + "%")).fetchall()
    if not rows:
        short = kw[:2]
        if len(short) >= 2:
            rows = db.execute(
                "SELECT id, title, artist, duration FROM music_tracks "
                "WHERE title LIKE ? ORDER BY id DESC LIMIT 5",
                ("%" + short + "%",)).fetchall()
    if not rows:
        return {"ok": True, "say": "音乐库里没有和「%s」相关的歌" % kw,
                "reply": "音乐库里没有找到和「%s」相关的歌。\n"
                         "可以去 /music 页面上传，或者跟我说说想听什么风格，我帮你找找相近的。" % kw,
                "action": None}

    tracks = [{"id": r["id"], "title": r["title"], "artist": r["artist"],
               "duration": _fmt_dur(r["duration"])} for r in rows]
    names = "、".join("《%s》%s" % (t["title"], (" · " + t["artist"]) if t["artist"] else "")
                     for t in tracks)
    exact = [t for t in tracks if kw in t["title"]]
    pick = (exact or tracks)[0]
    if exact:
        reply = "音乐库里有：%s\n要播放《%s》吗？" % (names, pick["title"])
    else:
        reply = ("音乐库里找到 %d 首相关的：%s\n没有完全叫「%s」的，最接近的是《%s》（%s）。\n"
                 "要播放《%s》吗？" % (len(tracks), names, kw, pick["title"],
                                   pick["duration"], pick["title"]))
    return {"ok": True, "say": "音乐库：" + names, "reply": reply,
            "action": {"type": "confirm_play", "track": pick, "candidates": tracks}}


def _search_posts(db, kw):
    kw = (kw or "").strip()
    if not kw:
        return {"ok": False, "say": "", "reply": "", "action": None}
    rows = db.execute(
        "SELECT id, title FROM posts WHERE (title LIKE ? OR content LIKE ?) "
        "AND is_deleted=0 AND recycle=0 ORDER BY id DESC LIMIT 5",
        ("%" + kw + "%", "%" + kw + "%")).fetchall()
    if not rows:
        return {"ok": True, "say": "没有和「%s」相关的帖子" % kw,
                "reply": "论坛里暂时没有和「%s」相关的帖子。\n要不要发一篇问问？点下面去论坛 →" % kw,
                "action": {"type": "goto", "path": "/forum", "label": "论坛"}}
    posts = [{"id": r["id"], "title": r["title"], "url": "/post/%d" % r["id"]} for r in rows]
    reply = "论坛里找到 %d 篇和「%s」相关的帖子，点标题直达：" % (len(posts), kw)
    return {"ok": True, "say": reply, "reply": reply,
            "action": {"type": "show_posts", "posts": posts}}


def _my_points(db, user):
    if not user:
        return {"ok": True, "say": "未登录", "reply": "你还没登录，登录后才能看积分哦。",
                "action": None}
    coins = user["coins"] if "coins" in user.keys() else 0
    rows = db.execute(
        "SELECT delta, reason FROM points_log WHERE user_id=? ORDER BY id DESC LIMIT 3",
        (user["id"],)).fetchall()
    recent = "；".join("%+d（%s）" % (r["delta"], (r["reason"] or "")[:14]) for r in rows)
    reply = "你现在有 %d 积分。\n最近几笔：%s\n想看完整流水点下面 →" % (
        coins, recent or "暂无记录")
    return {"ok": True, "say": reply, "reply": reply,
            "action": {"type": "goto", "path": "/points", "label": "积分"}}


def _my_profile(db, user):
    if not user:
        return {"ok": True, "say": "未登录", "reply": "你还没登录，登录后才能看等级。",
                "action": None}
    lv = user["level"] if "level" in user.keys() else 1
    exp = user["exp"] if "exp" in user.keys() else 0
    reply = "你现在是 Lv.%s，经验 %s。\n去个人中心看看勋章和成就吧 →" % (lv, exp)
    return {"ok": True, "say": reply, "reply": reply,
            "action": {"type": "goto", "path": "/profile", "label": "个人中心"}}
