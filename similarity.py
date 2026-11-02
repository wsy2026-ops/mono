# -*- coding: utf-8 -*-
"""similarity.py — 内容相似度（规划文档 061 项）

现状
----
帖子之间没有任何关联，一篇看完就断了。

方案：TF-IDF + 余弦相似度，**不调 embedding API**（省钱，且中文短文本
TF-IDF 的效果没有想象中差）。

中文分词
--------
不引 jieba（多一个依赖、多一份内存），用**字符 2-gram**：
「五子棋怎么玩」→ 五子 / 子棋 / 棋怎 / 怎么 / 么玩
短文本上 2-gram 的召回率反而比精确分词高，代价是特征维度大一点。

已知局限
--------
全站 13 篇帖子时，每篇的「相关」基本就是「剩下那 12 篇里最像的 3 篇」，
信息量有限。但**框架先立着**，等帖子到几百篇自然就有意义了 —— 那时候
只需要把 `related_posts` 里的实时计算换成预计算 + 缓存，接口不用改。
"""
import math
import re
from collections import Counter

# 停用词（2-gram 用不上太多，主要是过滤纯符号噪声）
_PUNCT = re.compile(r"[\s!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~，。！？、；：（）【】《》…—]+")

# 太常见的 2-gram 没有区分度（"怎么""什么""可以"…），权重会被 IDF 自然压低，
# 但这里再挡一层，减少噪声
_STOP_BIGRAMS = {
    "怎么", "什么", "可以", "这个", "那个", "我们", "你们", "他们",
    "一个", "没有", "就是", "还是", "因为", "所以", "但是", "如果",
    "现在", "已经", "自己", "知道", "觉得", "应该", "可能", "不是",
}


def tokenize(text):
    """中文 2-gram + 英文/数字整词。"""
    text = (text or "").lower()
    text = _PUNCT.sub(" ", text)
    tokens = []

    # 英文单词 / 数字
    tokens.extend(re.findall(r"[a-z0-9_]{2,}", text))

    # 中文连续段 → 2-gram
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) == 1:
            tokens.append(seg)
            continue
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in _STOP_BIGRAMS:
                tokens.append(bg)

    return tokens


def _doc_text(row):
    """拼一篇帖子的可比较文本。

    权重顺序：标签 > 标题 > 正文
    - 标签重复 3 次：人工/规则标过的主题，最可信
    - 标题重复 2 次：作者自己浓缩过的
    - 正文截 2000 字：长文尾部通常是闲聊，权重反而低
    """
    title = (row["title"] or "").strip()
    body = (row["summary"] or row["content"] or "").strip()
    try:
        tags = (row["tags"] or "").strip()
    except Exception:
        tags = ""
    parts = [title, title]
    if tags:
        parts.extend([tags, tags, tags])
    parts.append(body[:2000])
    return " ".join(parts)


def _tfidf_matrix(texts):
    """返回 [{token: tfidf}, ...] 和 idf 表。"""
    docs = [Counter(tokenize(t)) for t in texts]
    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(set(d.keys()))

    idf = {}
    for tok, cnt in df.items():
        idf[tok] = math.log((n + 1.0) / (cnt + 1.0)) + 1.0

    vecs = []
    for d in docs:
        total = sum(d.values()) or 1
        vec = {}
        for tok, cnt in d.items():
            vec[tok] = (cnt / float(total)) * idf.get(tok, 1.0)
        vecs.append(vec)
    return vecs


def _cosine(a, b):
    if not a or not b:
        return 0.0
    # 遍历较短的那个
    if len(a) > len(b):
        a, b = b, a
    dot = 0.0
    for tok, w in a.items():
        w2 = b.get(tok)
        if w2:
            dot += w * w2
    if not dot:
        return 0.0
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _by_vectors(db, post_id, limit=3, min_score=0.45):
    """用预计算的 bge 向量算相似度。

    向量由 /opt/tagging/bge/rebuild_vecs.py 离线算好存进 post_vecs 表
    （每篇 1536 字节），这里只做 numpy 点积 —— **不加载模型，零额外内存**。

    min_score=0.45 是实测出来的：bge 的相似度分布比 TF-IDF 高，
    0.5 以上基本是同类话题，0.45 留一点余量。
    """
    import numpy as np
    rows = db.execute(
        "SELECT pv.post_id, pv.vec, p.title, p.username, p.view_count "
        "FROM post_vecs pv JOIN posts p ON p.id = pv.post_id "
        "WHERE COALESCE(p.recycle,0)=0 AND COALESCE(p.is_deleted,0)=0").fetchall()
    if len(rows) < 2:
        return []

    ids = [r["post_id"] for r in rows]
    if post_id not in ids:
        return []

    mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    me = ids.index(post_id)
    sims = mat @ mat[me]

    out = []
    for j in np.argsort(-sims):
        if j == me:
            continue
        sc = float(sims[j])
        if sc < min_score:
            break
        r = rows[j]
        try:
            cm = db.execute(
                "SELECT COUNT(*) c FROM post_comments WHERE post_id=? AND is_deleted=0",
                (r["post_id"],)).fetchone()["c"]
        except Exception:
            cm = 0
        out.append({
            "id": r["post_id"],
            "title": r["title"],
            "username": r["username"],
            "score": round(sc, 3),
            "real_comment_count": cm,
        })
        if len(out) >= limit:
            break
    return out


def related_posts(db, post_id, limit=3, pool=200):
    """找出与 post_id 最像的帖子。

    返回 [{"id", "title", "username", "score", "real_comment_count"}, ...]
    数据不够时返回空列表，绝不抛异常（详情页不能因为推荐挂掉）。
    """
    # ① 优先用 bge 预计算向量（更准，见 _by_vectors 注释）
    try:
        vec_hit = _by_vectors(db, post_id, limit)
        if vec_hit:
            return vec_hit
    except Exception:
        pass          # numpy 缺失 / 表不存在 → 静默回退 TF-IDF

    # ② 回退：TF-IDF（没有向量时用，比如新帖还没跑 rebuild_vecs）
    try:
        rows = db.execute(
            "SELECT id, title, summary, content, username, view_count, tags "
            "FROM posts "
            "WHERE COALESCE(recycle,0)=0 AND COALESCE(is_deleted,0)=0 "
            "ORDER BY created_at DESC LIMIT ?", (pool,)).fetchall()
    except Exception:
        return []

    if len(rows) < 2:
        return []

    idx_of = {r["id"]: i for i, r in enumerate(rows)}
    if post_id not in idx_of:
        return []

    texts = [_doc_text(r) for r in rows]
    vecs = _tfidf_matrix(texts)

    me = idx_of[post_id]
    scored = []
    for i, r in enumerate(rows):
        if r["id"] == post_id:
            continue
        s = _cosine(vecs[me], vecs[i])
        if s > 0.02:                      # 阈值压很低：宁可多推，也别一片空白
            scored.append((s, r))

    scored.sort(key=lambda x: -x[0])
    out = []
    for s, r in scored[:limit]:
        try:
            cm = db.execute(
                "SELECT COUNT(*) c FROM post_comments WHERE post_id=? AND is_deleted=0",
                (r["id"],)).fetchone()["c"]
        except Exception:
            cm = 0
        out.append({
            "id": r["id"],
            "title": r["title"],
            "username": r["username"],
            "score": round(s, 3),
            "real_comment_count": cm,
        })
    return out
