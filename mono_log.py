"""MONO 社区 · 结构化日志模块

为什么需要：
    在此之前全站只有散落的 print()，输出混进 systemd 的 server.log，
    日志轮转后就再也查不到「昨天下午那个用户为什么发不出帖」。
    出问题时只能靠猜，无法回溯。

设计原则：
    1. 零侵入 —— 失败绝不抛出，日志系统坏掉不能影响业务
    2. 双通道 —— 同时写文件（留存、可追溯）和 stdout（保持原有运维习惯）
    3. 按天轮转 —— server.log.YYYY-MM-DD，默认保留 14 天
    4. 静默降级 —— 磁盘满 / 无权限时只打印一行警告，继续跑

本模块只依赖标准库。
"""
import logging
import logging.handlers
import os
import sys
import time

_LOGGER_NAME = "mono"
_initialized = False
LOG_DIR_NAME = "logs"
LOG_BASENAME = "mono.log"
KEEP_DAYS = 14


def _safe_stdout_handler():
    """stdout 处理器：对外输出保留原有观感（不重复时间戳，因为 systemd 会加）。"""
    try:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        return h
    except Exception:
        return None


def init_logging(data_dir, level=logging.INFO, keep_days=KEEP_DAYS):
    """初始化日志。可重复调用，只会真正初始化一次。

    data_dir：日志落到 <data_dir>/logs/ 下（与 data.db 同级的持久目录）
    返回 logger 对象；任何异常都返回一个可用的 logger（降级到只输出 stdout）。
    """
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)
    if _initialized:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    for h in list(logger.handlers):
        try:
            logger.removeHandler(h)
        except Exception:
            pass

    sh = _safe_stdout_handler()
    if sh is not None:
        logger.addHandler(sh)

    try:
        log_dir = os.path.join(data_dir, LOG_DIR_NAME)
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.TimedRotatingFileHandler(
            os.path.join(log_dir, LOG_BASENAME),
            when="midnight", backupCount=keep_days, encoding="utf-8", delay=False,
        )
        fh.suffix = "%Y-%m-%d"
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    except Exception as e:
        try:
            logger.warning("[日志] 文件日志初始化失败，仅输出到控制台: %s", e)
        except Exception:
            pass

    _initialized = True
    return logger


def get_logger():
    """取 logger；未初始化时也能用（走默认 stdout）。"""
    return logging.getLogger(_LOGGER_NAME)


def info(msg, *args):
    try:
        get_logger().info(msg, *args)
    except Exception:
        pass


def warn(msg, *args):
    try:
        get_logger().warning(msg, *args)
    except Exception:
        pass


def error(msg, *args):
    try:
        get_logger().error(msg, *args)
    except Exception:
        pass


def exception(msg, *args):
    """记录异常并带 traceback，用于 except 块内。"""
    try:
        get_logger().exception(msg, *args)
    except Exception:
        pass


def access(method, path, status, cost_ms, ip=None, uid=None):
    """请求日志：一行一条，便于事后统计异常来源。

    高频静态资源不调用此函数（由调用方过滤），避免日志膨胀。
    """
    try:
        get_logger().info(
            "REQ %s %s -> %s %dms ip=%s uid=%s",
            method, path, status, cost_ms, ip or "-", uid if uid else "-")
    except Exception:
        pass


def event(name, **fields):
    """关键业务事件：注册 / 登录失败 / 限流触发 / AI 调用 等。

    统一为 "EVENT <name> k=v k=v" 格式，方便 grep 与后续统计。
    """
    try:
        if fields:
            kv = " ".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
            get_logger().info("EVENT %s %s", name, kv)
        else:
            get_logger().info("EVENT %s", name)
    except Exception:
        pass
