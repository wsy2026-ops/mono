# -*- coding: utf-8 -*-
"""ai_queue.py — 053 AI 任务队列（HK 侧：入队 + 取结果）。

架构（简化版文档设计）：
  HK enqueue → Redis 队列 'ai' → LA RQ worker 消费 → 调本机 llama-server
  → 结果写回 Redis hash 'ai:result:<job_id>' → HK 读取

为什么不用 HTTP 回调：少暴露一个公网端点（HK 不用开 /internal/ai/callback），
LA 已有 redis 密码，回写 hash 一步到位。代价是 HK 侧要轮询/读结果——
审核场景本来就是异步的，前端不需要实时拿结果。

任务类型：
  moderate  —— 审核一段文本（LA 的 Qwen3Guard）
  tag       —— 给帖子打标签（Hunyuan，P1 第二阶段）

用法（HK）：
    job = ai_queue.enqueue_moderate("post", 123, "要审核的文本")
    ...
    r = ai_queue.result(job.id)   # None=还没好；{"level":...}=好了
"""
import json
import os
import time

QUEUE_NAME = "ai"
RESULT_PREFIX = "ai:result:"
JOB_TTL = 7 * 86400  # 结果保留 7 天

_redis = None


def _pw():
    try:
        return open("/opt/mono-ai/redis_pw").read().strip()
    except Exception:
        return os.environ.get("MONO_REDIS_PW") or ""


def conn():
    """惰性建连（失败返回 None，调用方降级）。"""
    global _redis
    if _redis is not None:
        try:
            _redis.ping()
            return _redis
        except Exception:
            _redis = None
    try:
        import redis
        _redis = redis.Redis(host="127.0.0.1", port=6379, password=_pw(),
                             socket_timeout=3, decode_responses=True)
        _redis.ping()
        return _redis
    except Exception:
        _redis = None
        return None


def available():
    return conn() is not None


def _enqueue(func_path, *args, **meta):
    """入队一个任务。返回 job_id 或 None（redis 不可用/入队失败）。"""
    r = conn()
    if r is None:
        return None
    try:
        from rq import Queue
        q = Queue(QUEUE_NAME, connection=r)
        job = q.enqueue(func_path, *args, job_timeout=180, result_ttl=600,
                        failure_ttl=3600, meta=meta)
        # 记一份元信息，方便 HK 侧查「这条帖子审到哪了」
        r.hset(RESULT_PREFIX + job.id, mapping={
            "status": "queued", "type": meta.get("type", ""),
            "target": "%s:%s" % (meta.get("target_type", ""), meta.get("target_id", "")),
            "at": str(time.time()),
        })
        r.expire(RESULT_PREFIX + job.id, JOB_TTL)
        return job.id
    except Exception:
        return None


def enqueue_moderate(target_type, target_id, text):
    """审核任务。返回 job_id 或 None。"""
    return _enqueue("worker.moderate", text,
                    type="moderate", target_type=target_type, target_id=target_id)


def enqueue_tag(target_type, target_id, title, body, tags):
    """打标签任务（Hunyuan）。tags 是允许的标签集列表。"""
    return _enqueue("worker.tag_post", title, body, tags,
                    type="tag", target_type=target_type, target_id=target_id)


def result(job_id):
    """取任务结果。返回 dict：
    {"status": "queued|running|done|failed", "level": ..., "score": ..., ...}
    或 None（job 不存在/redis 不可用）。
    """
    if not job_id:
        return None
    r = conn()
    if r is None:
        return None
    try:
        h = r.hgetall(RESULT_PREFIX + job_id)
        if not h:
            return None
        if "result" in h:
            try:
                data = json.loads(h["result"])
                data.setdefault("status", h.get("status", "done"))
                return data
            except Exception:
                return {"status": h.get("status", "done")}
        return {"status": h.get("status", "queued")}
    except Exception:
        return None


def stats():
    """队列概览（admin 面板用）。"""
    r = conn()
    if r is None:
        return {"ok": False}
    try:
        from rq import Queue
        q = Queue(QUEUE_NAME, connection=r)
        return {"ok": True, "queued": len(q), "started": len(q.started_job_registry),
                "failed": len(q.failed_job_registry),
                "finished": len(q.finished_job_registry)}
    except Exception as e:
        return {"ok": False, "err": str(e)[:80]}
