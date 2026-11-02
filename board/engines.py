# -*- coding: utf-8 -*-
"""board/engines.py — 棋类引擎子进程统一管理（规划文档 058 项）

为什么要这个
------------
六款棋里四个带引擎（RAPFI / KataGo / Pikafish / egaroucid），每份实现都自己
维护一张 `{rid: engine}` 字典 + 一把锁 + 一个 atexit 清理。模式完全一样，
但细节不一致：

| 引擎 | 容量上限 | 失败黑名单 | 冷却 |
|---|---|---|---|
| RAPFI（五子棋） | 6 / 24（按内存） | **无** | — |
| KataGo（围棋） | 有 | 有 `_KATAGO_FAILED` | 180s |
| Pikafish（象棋） | 有 | 有 `_PIKAFISH_FAILED` | 180s |
| egaroucid（翻转棋） | 有 | 有 `_EGAROUCID_FAILED` | 180s |

五子棋缺黑名单意味着：引擎反复启动失败时，**每个请求都会再 fork 一次子进程**，
失败一次泄漏一点，最后吃满内存。这是本模块要解决的问题。

提供什么
--------
`EnginePool` —— 懒创建 / 容量上限 / 失败黑名单 + 冷却 / 统一回收。

用法：
    pool = EnginePool("rapfi", factory=lambda rid: _RapfiEngine(rid),
                      max_engines=6, cooldown=180)

    eng = pool.get(rid)        # None = 超容量 或 冷却中（调用方降级到内置 AI）
    pool.mark_failed(rid)      # 引擎启动/通信失败时调，进冷却
    pool.degraded(rid)         # True = 还在冷却
    pool.close(rid)            # 房间结束时释放
    pool.cleanup()             # 进程退出时全清（配 atexit）

子进程回收
----------
`close()` 依次尝试 `close()` / `_kill()` / `kill()` / `terminate()`，
任何一个成功就停 —— 各引擎的清理方法名不统一，这里做兜底。
**绝不用 `p.kill()` 一把梭**：有些引擎要发 quit 命令才能让内部线程正常退出，
直接 SIGKILL 会留下僵尸线程和锁文件。
"""
import atexit
import threading
import time


class EnginePool:
    """引擎实例池（一个房间一个引擎进程）。"""

    def __init__(self, name, factory, max_engines=8, cooldown=180, logger=None):
        """
        name         引擎名（日志用）
        factory      rid -> 引擎对象 的可调用
        max_engines  同时在跑的引擎上限（超过返回 None，调用方降级）
        cooldown     失败后冷却秒数，期间不再尝试启动
        logger       callable(str) 可选，记录失败
        """
        self.name = name
        self.factory = factory
        self.max_engines = max_engines
        self.cooldown = cooldown
        self._log = logger
        self._engines = {}
        self._failed = {}       # rid -> 失败时间戳
        self.lock = threading.Lock()

    # ── 内部 ──
    def _degraded_locked(self, rid):
        t = self._failed.get(rid)
        if t is None:
            return False
        if time.time() - t < self.cooldown:
            return True
        # 冷却过期，给一次重试机会
        self._failed.pop(rid, None)
        return False

    def _kill(self, eng):
        """按顺序尝试各种清理方法，任一成功即停。"""
        for meth in ("close", "_kill", "kill", "terminate", "stop"):
            fn = getattr(eng, meth, None)
            if callable(fn):
                try:
                    fn()
                    return True
                except Exception:
                    continue
        return False

    # ── 公开 ──
    def get(self, rid):
        """取（或懒创建）某个房间的引擎。返回 None 表示降级。"""
        with self.lock:
            if self._degraded_locked(rid):
                return None
            eng = self._engines.get(rid)
            if eng is not None:
                return eng
            if len(self._engines) >= self.max_engines:
                return None
            try:
                eng = self.factory(rid)
            except Exception as e:
                self._failed[rid] = time.time()
                if self._log:
                    self._log("%s: 创建失败 rid=%s %s" % (self.name, rid, e))
                return None
            self._engines[rid] = eng
            return eng

    def mark_failed(self, rid):
        """标记该房间引擎失败，进入冷却。"""
        with self.lock:
            self._failed[rid] = time.time()
            eng = self._engines.pop(rid, None)
        if eng is not None:
            self._kill(eng)
        if self._log:
            self._log("%s: rid=%s 进冷却 %ds" % (self.name, rid, self.cooldown))

    def degraded(self, rid):
        """该房间是否在冷却中。"""
        with self.lock:
            return self._degraded_locked(rid)

    def close(self, rid):
        """房间结束，释放引擎。"""
        with self.lock:
            eng = self._engines.pop(rid, None)
        if eng is not None:
            self._kill(eng)

    def cleanup(self):
        """全清（配 atexit）。"""
        with self.lock:
            engines = list(self._engines.values())
            self._engines.clear()
        for eng in engines:
            try:
                self._kill(eng)
            except Exception:
                pass

    # ── 观测 ──
    def stats(self):
        with self.lock:
            return {
                "engine": self.name,
                "alive": len(self._engines),
                "max": self.max_engines,
                "cooling": sum(1 for rid in self._failed
                               if time.time() - self._failed[rid] < self.cooldown),
            }

    def register_atexit(self):
        """注册进程退出清理。"""
        atexit.register(self.cleanup)
        return self
