# -*- coding: utf-8 -*-
"""blocking.py — 阻塞调用兼容层（为 eventlet 迁移铺路）

为什么需要这个
--------------
eventlet 是**单进程协程**：任何阻塞调用（子进程等待、ffmpeg 转码）都会
卡住整个事件循环 —— 全站所有人的请求一起卡。

`eventlet.tpool.execute(fn)` 把阻塞调用丢到**真实线程池**执行，
当前协程让出 CPU，其他请求照常处理。

但 threading 模式下没有 tpool。所以这层做兼容：
- 装了 eventlet → 走 tpool（不卡主循环）
- 没装 → 直接调用（行为跟现在完全一样）

**这意味着可以先把调用点全改好，再装 eventlet，两边都正确。**

2026-11-01 审计出的 7 个阻塞点
------------------------------
| 位置 | 阻塞时长 |
|---|---|
| board/xq.py `_pikafish_await` | ≤15s（在 HTTP 请求里等引擎！）|
| board/chess.py `ask_move` / `bestmove` | ≤10s |
| board/go.py 读 KataGo stdout | ≤5s |
| board/gomoku.py `_rapfi_ai_move` | ≤5s |
| board/reversi.py `_egaroucid_move` | ≤5s |
| app.py `_compress_music`（ffmpeg） | 数十秒 |
| app.py 视频压缩（ffmpeg） | 数十秒 |
"""
import functools

try:
    from eventlet import tpool as _tpool
    HAS_EVENTLET = True
except Exception:
    _tpool = None
    HAS_EVENTLET = False


def run_blocking(fn, *args, **kwargs):
    """执行可能阻塞的函数。

    eventlet 下走 tpool（真线程池，不卡协程）；
    threading 下直接调用（跟现在一样）。
    """
    if _tpool is None:
        return fn(*args, **kwargs)
    # 老版本 eventlet 的 tpool.execute 不收 kwargs，用 partial 兜住
    if kwargs:
        return _tpool.execute(functools.partial(fn, **kwargs), *args)
    return _tpool.execute(fn, *args)


def submit_background(fn, *args, **kwargs):
    """后台执行、不关心结果（比如异步 AI 应手）。

    eventlet 下用 greenthread，threading 下用普通线程。
    """
    try:
        if HAS_EVENTLET:
            import eventlet
            return eventlet.spawn_n(fn, *args, **kwargs)
        import threading
        t = threading.Thread(target=functools.partial(fn, **kwargs) if kwargs else fn,
                             args=args, daemon=True)
        t.start()
        return t
    except Exception:
        return None
