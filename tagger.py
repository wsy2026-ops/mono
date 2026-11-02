# -*- coding: utf-8 -*-
"""tagger.py — 帖子自动打标签（029 标签体系 / 061 相似推荐的前置）

为什么要打标签
--------------
061 的 TF-IDF 相似推荐现在是「纯文本 2-gram」，有标签会准得多；
029 的标签体系表建了半年，`post_tags` 一行数据都没有 —— 表是空的，功能等于没有。

打标签的两条路
--------------
1. **规则匹配**（本文件默认）：关键词 + 标题权重。13 篇帖子够用，
   零成本、零延迟、结果可预测。缺点是换个领域就要加规则。
2. **LLM 生成**：洛杉矶那台跑的是 `qwen3guard-0.6b`，**是内容审核模型**
   （只会输出 `Safety: Safe / Categories: None`），打不了标签。
   真要用 LLM 得换个通用小模型（如 Qwen2.5-0.5B-Instruct，约 400MB），
   或者走香港的智谱池（几乎免费）。

所以本文件先把规则版跑通，`suggest_tags_via_llm()` 留了接口，换模型后直接接。
"""
import re

# 标签规则：(标签名, 正则, 权重加成)
# 顺序即优先级 —— 同一篇命中多个时，前面的更容易进最终 3 个
# 标签规则从 tag_library 统一读（100 个），不再在这里手写
try:
    from tag_library import TAG_LIBRARY
    TAG_RULES = [(name, rx) for name, _desc, rx in TAG_LIBRARY]
except Exception:
    # 兜底：tag_library 缺失时用最小集，绝不让打标签整个挂掉
    TAG_RULES = [
        ("公告", r"公告|通报|通知|处罚|封禁|守则|规范"),
        ("比赛", r"比赛|月赛|竞赛|悬赏|报名"),
        ("算法", r"算法|复杂度"),
        ("Python", r"Python|Flask"),
        ("C\+\+", r"C\+\+|C语言"),
        ("游戏", r"游戏|五子棋|围棋|象棋"),
        ("教程", r"教程|入门|怎么|如何"),
        ("资源", r"资源|下载|工具|软件"),
        ("反馈", r"建议|反馈|意见"),
        ("讨论", r"讨论|编程|代码|开发"),
        ("闲聊", r"闲聊|随便聊聊|大家好"),
    ]


# 灌水判定（和 moderation.meaningless_check 口径一致，但这里只打标不拦截）
_WATER = re.compile(r"^[\s\W]{0,3}$|^\d{1,6}$|^[a-zA-Z]{1,3}$")

MAX_TAGS = 3


def is_water(title, body):
    """灌水帖：标题极短 / 纯数字 / 纯符号。这类不打标签。"""
    t = (title or "").strip()
    b = (body or "").strip()
    if len(t) <= 2 and len(b) <= 4:
        return True
    if _WATER.match(t or ""):
        return True
    return False


def suggest_tags(title, body, limit=MAX_TAGS):
    """规则打标签。返回 [tag, ...]（可能为空）。"""
    t = (title or "").strip()
    b = (body or "").strip()
    if is_water(t, b):
        return []

    # ① 标题命中优先：标题是作者自己浓缩过的主题，比正文可信得多
    title_hits = []
    for order, (name, pat) in enumerate(TAG_RULES):
        if re.search(pat, t, re.I):
            title_hits.append((order, name))
    if title_hits:
        title_hits.sort()
        return [n for _o, n in title_hits[:limit]]

    # ② 标题没命中才看正文 —— 只取 2 个（正文里"游戏""有人"太容易误伤）
    body_hits = []
    for order, (name, pat) in enumerate(TAG_RULES):
        if re.search(pat, b[:2000], re.I):
            body_hits.append((order, name))
    body_hits.sort()
    return [n for _o, n in body_hits[:2]]


def suggest_tags_via_llm(title, body, limit=MAX_TAGS):
    """LLM 打标签（预留）。

    现在的本地模型是 qwen3guard（审核专用），干不了这个。
    换成通用模型后，把 prompt 改成「输出 3 个标签，逗号分隔」即可。
    暂时直接回退到规则版。
    """
    return suggest_tags(title, body, limit)


def apply_tags(db, post_id, tags):
    """写入标签。**必须写 tags + posts_tags 两张表** ——
    页面渲染走的是 `_topics.post_tags()`（JOIN tags），
    不是 `posts.tags` 字段，也不是 `post_tags` 表。

    （2026-11-01 踩过：先写了 post_tags，页面一个字都不显示。）
    """
    import time as _t
    tags = [x.strip() for x in (tags or []) if x and x.strip()][:MAX_TAGS]
    if not tags:
        return []
    now = _t.time()
    db.execute("UPDATE posts SET tags=? WHERE id=?", (",".join(tags), post_id))
    db.execute("DELETE FROM posts_tags WHERE post_id=?", (post_id,))
    for name in tags:
        row = db.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            tid = row["id"]
        else:
            cur = db.execute(
                "INSERT INTO tags (name, use_count, created_at) VALUES (?,0,?)", (name, now))
            tid = cur.lastrowid
        db.execute("INSERT OR IGNORE INTO posts_tags (post_id, tag_id) VALUES (?,?)",
                   (post_id, tid))
        db.execute(
            "UPDATE tags SET use_count=(SELECT COUNT(*) FROM posts_tags WHERE tag_id=?) WHERE id=?",
            (tid, tid))
    return tags
