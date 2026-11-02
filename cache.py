# -*- coding: utf-8 -*-
"""cache.py — 站点功能规划第 028 项 · 可插拔缓存层。

设计取舍（2C2G 香港机的现实）：
  - **默认进程内 TTL 缓存**：零依赖、零新增内存压力，单机部署下完全够用；
  - **可选 Redis 后端**：设了 REDIS_URL 且 redis 库可连才启用
    （第 069 项多机部署的前置，届时改环境变量即可，调用方无感）；
  - **铁律：缓存不可用绝不影响站点** —— 后端 ping 失败、反序列化失败、
    任何异常一律按 cache miss 处理，调用方回源 DB。宁可慢，不可挂。

用法：
    import cache
    cache.remember("rank:top:%s" % field, 300, lambda: db_query())
    cache.delete_prefix("rank:")          # 成绩提交后失效
    cache.delete("unread:%d" % uid)      # 新通知到达后失效
"""
import json
import os
import threading
import time

DEFAULT_TTL = 300          # 5 分钟：榜单/热榜口径（028 方案原文）
_MEM_MAX = 20000           # 进程内容量上限，超过触发一次惰性清理

_backend = "memory"        # "memory" | "redis"
_redis = None
_lock = threading.Lock()
_mem = {}                  # key -> (expire_at, value)
_stats = {"hits": 0, "misses": 0, "errors": 0, "sets": 0}


def _try_redis():
    """REDIS_URL 存在且连得上才用 Redis；否则永远留内存后端。"""
    global _backend, _redis
    url = (os.environ.get("REDIS_URL") or "").strip()
    if not url:
        return
    try:
        import redis  # noqa: 延迟 import，没装 redis 包的机器走内存后端
        cli = redis.from_url(url, socket_connect_timeout=1.0,
                             socket_timeout=1.0, decode_responses=False)
        cli.ping()
        _redis = cli
        _backend = "redis"
    except Exception:
        _redis = None
        _backend = "memory"


_try_redis()


def backend():
    return _backend


def stats():
    return dict(_stats)


# ---------------- 基础读写 ----------------

def get(key):
    try:
        if _backend == "redis":
            raw = _redis.get(key)
            if raw is None:
                _stats["misses"] += 1
                return None
            _stats["hits"] += 1
            return json.loads(raw)
        with _lock:
            item = _mem.get(key)
            if item is None:
                _stats["misses"] += 1
                return None
            exp, val = item
            if exp and exp < time.time():
                _mem.pop(key, None)
                _stats["misses"] += 1
                return None
            _stats["hits"] += 1
            return val
    except Exception:
        _stats["errors"] += 1
        return None


def set(key, value, ttl=DEFAULT_TTL):
    try:
        if ttl is None or ttl <= 0:
            ttl = DEFAULT_TTL
        if _backend == "redis":
            _redis.setex(key, int(ttl), json.dumps(value, ensure_ascii=False))
            _stats["sets"] += 1
            return True
        with _lock:
            if len(_mem) >= _MEM_MAX:
                now = time.time()
                for k in [k for k, (e, _) in _mem.items() if e and e < now]:
                    _mem.pop(k, None)
            _mem[key] = (time.time() + ttl, value)
            _stats["sets"] += 1
            return True
    except Exception:
        _stats["errors"] += 1
        return False


def delete(key):
    try:
        if _backend == "redis":
            _redis.delete(key)
            return True
        with _lock:
            _mem.pop(key, None)
            return True
    except Exception:
        _stats["errors"] += 1
        return False


def delete_prefix(prefix):
    """按前缀批量失效。内存后端扫 dict；Redis 用 scan（不全量 keys，防阻塞）。"""
    try:
        if _backend == "redis":
            cursor = 0
            while True:
                cursor, keys = _redis.scan(cursor, match=prefix + "*", count=200)
                if keys:
                    _redis.delete(*keys)
                if not cursor:
                    break
            return True
        with _lock:
            for k in [k for k in _mem if k.startswith(prefix)]:
                _mem.pop(k, None)
            return True
    except Exception:
        _stats["errors"] += 1
        return False


def remember(key, ttl, producer, *args):
    """缓存穿透标准式：先读缓存，未命中调 producer() 并回填。

    producer 抛异常不会写缓存（避免把错误结果钉住），由调用方 try 兜。
    """
    val = get(key)
    if val is not None:
        return val
    val = producer(*args)
    try:
        set(key, val, ttl)
    except Exception:
        pass
    return val


# ---------------- 业务 key 约定 ----------------
# rank:top:<field>        游戏榜单 top10（_game_score_rank）
# rank:ach                成就总分榜
# rank:records            各游戏纪录保持者
# rank:unlock             图鉴全站解锁数
# unread:<uid>            未读通知数

RANK_PREFIX = "rank:"


def invalidate_game():
    """成绩/成就变化 → 榜单类缓存全失效（5 分钟内自然重建，成本可忽略）。"""
    delete_prefix(RANK_PREFIX)


def invalidate_unread(uid):
    delete("unread:%d" % uid)
