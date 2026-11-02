# -*- coding: utf-8 -*-
"""games/saves.py — 统一存档槽位与云同步（规划文档 075 项 · 第二块）

现状（2026-11-01 盘点）
-----------------------
只有两款游戏有云存档，而且两套实现完全不搭：

|          | evolve（进化）                       | kubition（超苦逼）              |
|----------|--------------------------------------|----------------------------------|
| 表       | evolve_saves(user_id PK, data, size, day, race, updated_at) | kubition_saves(..., slot, ...) |
| 保存入口 | POST /api/evolve/sync（藏在联机同步里，每 30 秒顺带） | POST /api/kubition/save |
| 取回入口 | GET /api/evolve/save                 | GET /api/kubition/save           |
| 历史版本 | 无                                   | 有，保留 20 份                   |
| 大小上限 | 400 KB                               | 2 MB                             |

9 款小游戏（24/2048/俄罗斯方块/数独/扫雷/汉诺塔/华容道两族/跳一跳/闲弈/色盲）
**没有进度存档** —— 它们是单局游戏，本来也不需要。

设计：适配器门面，不迁数据
--------------------------
`SaveBackend` 定义统一契约（load / store / history / restore），
各游戏一个后端：

- `EvolveBackend`   → 读写旧表 evolve_saves（`race` 塞进 meta）
- `KubitionBackend` → 读写旧表 kubition_saves + history
- `GenericBackend`  → 新表 game_saves / game_saves_history，给以后的新游戏用

**为什么不把旧表迁到统一表**：玩家存档是丢了就找不回来的东西，
一次性迁移 + 切接口 = 拿真实数据做实验。门面模式下旧接口照常工作，
统一 API 只是多一个入口，出问题最多是新入口不可用。

统一 API（新增，旧接口不动）
----------------------------
    GET  /api/game/save/<game>           取回最新存档
    POST /api/game/save/<game>           保存
    GET  /api/game/save/<game>/history   历史版本
    POST /api/game/save/<game>/restore   恢复某个历史版本
"""
import json
import time

from flask import jsonify, request

from .deps import D


# ══════════════════════════════════════════════════════════════════
# 契约
# ══════════════════════════════════════════════════════════════════
class SaveBackend:
    """一款游戏的存档后端。子类实现 load / store。"""

    game = ""
    label = ""
    max_bytes = 2000000      # 单份存档上限
    min_bytes = 1
    supports_history = False
    supports_slots = False

    def ensure(self, db):
        """懒建表 / 补列。子类按需覆盖。"""
        return

    def load(self, db, uid, slot=1):
        """返回 dict 或 None。dict 至少含 data / size / updated_at。"""
        raise NotImplementedError

    def store(self, db, uid, blob, meta, slot=1):
        """写入。返回 dict（至少 ok / size），失败抛异常。"""
        raise NotImplementedError

    def history(self, db, uid, limit=20):
        """历史版本列表；不支持历史的返回 []。"""
        return []

    def restore(self, db, uid, hid):
        """把某个历史版本恢复成当前存档；不支持返回 None。"""
        return None


# ══════════════════════════════════════════════════════════════════
# evolve（进化）
# ══════════════════════════════════════════════════════════════════
class EvolveBackend(SaveBackend):
    game = "evolve"
    label = "进化"
    max_bytes = 400000
    min_bytes = 100
    supports_history = False
    supports_slots = False

    def ensure(self, db):
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS evolve_saves ("
                "  user_id INTEGER PRIMARY KEY,"
                "  data TEXT NOT NULL DEFAULT '',"
                "  size INTEGER NOT NULL DEFAULT 0,"
                "  day INTEGER NOT NULL DEFAULT 0,"
                "  race TEXT NOT NULL DEFAULT '',"
                "  updated_at REAL NOT NULL DEFAULT 0)")
            db.commit()
        except Exception:
            pass

    def load(self, db, uid, slot=1):
        row = db.execute(
            "SELECT data, size, day, race, updated_at FROM evolve_saves WHERE user_id=?",
            (uid,)).fetchone()
        if not row or not row["data"]:
            return None
        return {
            "data": row["data"],
            "size": row["size"],
            "updated_at": row["updated_at"],
            "meta": {"day": row["day"], "race": row["race"]},
        }

    def store(self, db, uid, blob, meta, slot=1):
        day = int((meta or {}).get("day") or 0)
        race = str((meta or {}).get("race") or "")[:32]
        now = time.time()
        db.execute(
            "INSERT INTO evolve_saves (user_id, data, size, day, race, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "data=excluded.data, size=excluded.size, day=excluded.day, "
            "race=excluded.race, updated_at=excluded.updated_at",
            (uid, blob, len(blob), day, race, now))
        db.commit()
        return {"ok": True, "size": len(blob), "updated_at": now}


# ══════════════════════════════════════════════════════════════════
# kubition（超苦逼冒险者）
# ══════════════════════════════════════════════════════════════════
class KubitionBackend(SaveBackend):
    game = "kubition"
    label = "超苦逼冒险者"
    max_bytes = 2000000
    min_bytes = 20
    supports_history = True
    supports_slots = True
    history_limit = 20

    def ensure(self, db):
        # 建表交给 app.py 的 _kubi_ensure（那边还要补列），这里只兜底
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS kubition_saves ("
                "  user_id INTEGER PRIMARY KEY,"
                "  data TEXT NOT NULL DEFAULT '',"
                "  size INTEGER NOT NULL DEFAULT 0,"
                "  day INTEGER NOT NULL DEFAULT 0,"
                "  slot INTEGER NOT NULL DEFAULT 1,"
                "  updated_at REAL NOT NULL DEFAULT 0)")
            db.commit()
        except Exception:
            pass

    def load(self, db, uid, slot=1):
        row = db.execute(
            "SELECT data, size, day, slot, updated_at FROM kubition_saves WHERE user_id=?",
            (uid,)).fetchone()
        if not row or not row["data"]:
            return None
        return {
            "data": row["data"],
            "size": row["size"],
            "updated_at": row["updated_at"],
            "meta": {"day": row["day"], "slot": row["slot"]},
        }

    def store(self, db, uid, blob, meta, slot=1):
        day = int((meta or {}).get("day") or 0)
        try:
            slot = int((meta or {}).get("slot") or slot or 1)
        except (TypeError, ValueError):
            slot = 1
        now = time.time()
        # 先写历史（保留最近 N 份），覆盖了也能回滚
        db.execute(
            "INSERT INTO kubition_saves_history (user_id,slot,data,size,day,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (uid, slot, blob, len(blob), day, now))
        db.execute(
            "DELETE FROM kubition_saves_history WHERE user_id=? AND id NOT IN ("
            "  SELECT id FROM kubition_saves_history WHERE user_id=? "
            "  ORDER BY created_at DESC LIMIT ?)",
            (uid, uid, self.history_limit))
        db.execute(
            "INSERT INTO kubition_saves (user_id,data,size,day,slot,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "data=excluded.data, size=excluded.size, day=excluded.day, "
            "slot=excluded.slot, updated_at=excluded.updated_at",
            (uid, blob, len(blob), day, slot, now))
        db.commit()
        return {"ok": True, "size": len(blob), "updated_at": now}

    def history(self, db, uid, limit=20):
        rows = db.execute(
            "SELECT id,size,day,slot,created_at FROM kubition_saves_history "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (uid, limit)).fetchall()
        now = time.time()
        return [{"id": r["id"], "size": r["size"], "day": r["day"], "slot": r["slot"],
                 "at": r["created_at"], "ago": max(0, int(now - (r["created_at"] or 0)))}
                for r in rows]

    def restore(self, db, uid, hid):
        row = db.execute(
            "SELECT id,data,size,day,slot FROM kubition_saves_history "
            "WHERE id=? AND user_id=?", (hid, uid)).fetchone()
        if not row:
            return None
        now = time.time()
        # 恢复前先把当前存档也存一份历史，恢复错了还能退回去
        cur = db.execute("SELECT data,size,day,slot FROM kubition_saves WHERE user_id=?",
                         (uid,)).fetchone()
        if cur and cur["data"]:
            db.execute(
                "INSERT INTO kubition_saves_history (user_id,slot,data,size,day,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (uid, cur["slot"], cur["data"], cur["size"], cur["day"], now))
        db.execute(
            "INSERT INTO kubition_saves (user_id,data,size,day,slot,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "data=excluded.data, size=excluded.size, day=excluded.day, "
            "slot=excluded.slot, updated_at=excluded.updated_at",
            (uid, row["data"], row["size"], row["day"], row["slot"], now))
        db.commit()
        return {"ok": True, "day": row["day"], "size": row["size"]}


# ══════════════════════════════════════════════════════════════════
# 通用后端（新游戏用，统一表）
# ══════════════════════════════════════════════════════════════════
class GenericBackend(SaveBackend):
    """新游戏的默认后端：game_saves + game_saves_history 两张统一表。"""

    game = "*"
    label = "通用"
    supports_history = True
    supports_slots = True
    history_limit = 20

    def __init__(self, game, label=None):
        self.game = game
        self.label = label or game

    def ensure(self, db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS game_saves ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  user_id INTEGER NOT NULL,"
            "  game TEXT NOT NULL,"
            "  slot INTEGER NOT NULL DEFAULT 1,"
            "  data TEXT NOT NULL DEFAULT '',"
            "  size INTEGER NOT NULL DEFAULT 0,"
            "  meta TEXT NOT NULL DEFAULT '{}',"
            "  updated_at REAL NOT NULL DEFAULT 0,"
            "  UNIQUE(user_id, game, slot))")
        db.execute(
            "CREATE TABLE IF NOT EXISTS game_saves_history ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  user_id INTEGER NOT NULL,"
            "  game TEXT NOT NULL,"
            "  slot INTEGER NOT NULL DEFAULT 1,"
            "  data TEXT NOT NULL DEFAULT '',"
            "  size INTEGER NOT NULL DEFAULT 0,"
            "  meta TEXT NOT NULL DEFAULT '{}',"
            "  created_at REAL NOT NULL DEFAULT 0)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_gs_hist "
            "ON game_saves_history(user_id, game, created_at DESC)")
        db.commit()

    def load(self, db, uid, slot=1):
        row = db.execute(
            "SELECT data, size, meta, updated_at FROM game_saves "
            "WHERE user_id=? AND game=? AND slot=?", (uid, self.game, slot)).fetchone()
        if not row or not row["data"]:
            return None
        try:
            meta = json.loads(row["meta"] or "{}")
        except Exception:
            meta = {}
        return {"data": row["data"], "size": row["size"],
                "updated_at": row["updated_at"], "meta": meta}

    def store(self, db, uid, blob, meta, slot=1):
        now = time.time()
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        db.execute(
            "INSERT INTO game_saves_history (user_id,game,slot,data,size,meta,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, self.game, slot, blob, len(blob), meta_json, now))
        db.execute(
            "DELETE FROM game_saves_history WHERE user_id=? AND game=? AND id NOT IN ("
            "  SELECT id FROM game_saves_history WHERE user_id=? AND game=? "
            "  ORDER BY created_at DESC LIMIT ?)",
            (uid, self.game, uid, self.game, self.history_limit))
        db.execute(
            "INSERT INTO game_saves (user_id,game,slot,data,size,meta,updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id,game,slot) DO UPDATE SET "
            "data=excluded.data, size=excluded.size, meta=excluded.meta, "
            "updated_at=excluded.updated_at",
            (uid, self.game, slot, blob, len(blob), meta_json, now))
        db.commit()
        return {"ok": True, "size": len(blob), "updated_at": now}

    def history(self, db, uid, limit=20):
        rows = db.execute(
            "SELECT id,size,slot,meta,created_at FROM game_saves_history "
            "WHERE user_id=? AND game=? ORDER BY created_at DESC LIMIT ?",
            (uid, self.game, limit)).fetchall()
        now = time.time()
        out = []
        for r in rows:
            try:
                meta = json.loads(r["meta"] or "{}")
            except Exception:
                meta = {}
            out.append({"id": r["id"], "size": r["size"], "slot": r["slot"],
                        "meta": meta, "at": r["created_at"],
                        "ago": max(0, int(now - (r["created_at"] or 0)))})
        return out

    def restore(self, db, uid, hid):
        row = db.execute(
            "SELECT id,slot,data,size,meta FROM game_saves_history "
            "WHERE id=? AND user_id=? AND game=?", (hid, uid, self.game)).fetchone()
        if not row:
            return None
        now = time.time()
        cur = db.execute(
            "SELECT slot,data,size,meta FROM game_saves "
            "WHERE user_id=? AND game=? AND slot=?", (uid, self.game, row["slot"])).fetchone()
        if cur and cur["data"]:
            db.execute(
                "INSERT INTO game_saves_history (user_id,game,slot,data,size,meta,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, self.game, cur["slot"], cur["data"], cur["size"], cur["meta"], now))
        db.execute(
            "INSERT INTO game_saves (user_id,game,slot,data,size,meta,updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id,game,slot) DO UPDATE SET "
            "data=excluded.data, size=excluded.size, meta=excluded.meta, "
            "updated_at=excluded.updated_at",
            (uid, self.game, row["slot"], row["data"], row["size"], row["meta"], now))
        db.commit()
        return {"ok": True, "size": row["size"]}


# ══════════════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════════════
BACKENDS = {
    "evolve": EvolveBackend(),
    "kubition": KubitionBackend(),
}

# 有专门后端的游戏（其余游戏一律走 GenericBackend）
DEDICATED = set(BACKENDS.keys())


def get_backend(game):
    """取后端：有专门实现用专门的，否则给一个通用后端（懒建表）。"""
    b = BACKENDS.get(game)
    if b is not None:
        return b
    return GenericBackend(game)


def known_games():
    return tuple(BACKENDS.keys())


# ══════════════════════════════════════════════════════════════════
# HTTP 处理（由 games/__init__.py 注册路由，D 里拿 current_user / get_db）
# ══════════════════════════════════════════════════════════════════
def _json_err(msg, code=400, **extra):
    body = {"ok": False, "msg": msg}
    body.update(extra)
    return jsonify(body), code


def api_game_save(game):
    """GET 取回 / POST 保存。"""
    me = D['current_user']()
    if not me:
        return _json_err("请先登录", 401)
    db = D['get_db']()
    be = get_backend(game)
    try:
        be.ensure(db)
    except Exception:
        pass

    if request.method == "GET":
        try:
            slot = int(request.args.get("slot") or 1)
        except (TypeError, ValueError):
            slot = 1
        row = be.load(db, me["id"], slot)
        if not row:
            return _json_err("服务端还没有你的存档", 404, game=game)
        out = {"ok": True, "game": game}
        out.update(row)
        return jsonify(out)

    # POST
    d = request.get_json(silent=True) or {}
    blob = d.get("data")
    if not isinstance(blob, str) or len(blob) < be.min_bytes:
        return _json_err("存档数据无效（至少 %d 字节）" % be.min_bytes)
    if len(blob) > be.max_bytes:
        return _json_err("存档过大（上限 %d 字节）" % be.max_bytes)
    meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
    # 兼容老前端把 day / slot / race 放顶层的写法
    for k in ("day", "slot", "race"):
        if k in d and k not in meta:
            meta[k] = d[k]
    try:
        slot = int(d.get("slot") or meta.get("slot") or 1)
    except (TypeError, ValueError):
        slot = 1
    try:
        res = be.store(db, me["id"], blob, meta, slot)
    except Exception as e:
        return _json_err("写库失败: %s" % e, 500)
    res.update({"game": game})
    return jsonify(res)


def api_game_save_history(game):
    me = D['current_user']()
    if not me:
        return _json_err("请先登录", 401)
    db = D['get_db']()
    be = get_backend(game)
    if not be.supports_history:
        return _json_err("%s 暂不支持历史版本" % (be.label or game), 400)
    try:
        be.ensure(db)
    except Exception:
        pass
    try:
        limit = int(request.args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    items = be.history(db, me["id"], max(1, min(limit, 100)))
    return jsonify({"ok": True, "game": game, "items": items})


def api_game_save_restore(game):
    me = D['current_user']()
    if not me:
        return _json_err("请先登录", 401)
    db = D['get_db']()
    be = get_backend(game)
    if not be.supports_history:
        return _json_err("%s 暂不支持历史版本" % (be.label or game), 400)
    d = request.get_json(silent=True) or {}
    try:
        hid = int(d.get("id") or 0)
    except (TypeError, ValueError):
        hid = 0
    if not hid:
        return _json_err("缺少历史 id")
    try:
        res = be.restore(db, me["id"], hid)
    except Exception as e:
        return _json_err("恢复失败: %s" % e, 500)
    if not res:
        return _json_err("找不到这份历史存档", 404)
    res.update({"game": game})
    return jsonify(res)
