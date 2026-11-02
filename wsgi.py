"""MONO 社区 · 云端启动入口（Render / Linux 服务器用）。

学校/本机仍用 一键启动.cmd → start_server.py（带诊断自修复，别动）；
本入口做四件事：
    1. 读出数据目录（MONO_DATA_DIR，未设则与代码同目录）
    2. 本地没有数据库时，先从 Neon（外部 PostgreSQL）拉回快照
    3. 导入 app（完成建表 / 引擎探测 / 目录创建）
    4. waitress 起服务 + 启动后台快照线程（定期把数据存回 Neon）

SocketIO 走长轮询（与学校机器同款配置，客户端自动回退，无需改动）。
"""
import os
import signal

# 2026-11-02：忽略 SIGHUP。
# 起因：/etc/logrotate.d/mono 的 postrotate 里有 `systemctl kill -s HUP mono`，
# 而 **Python 默认不处理 SIGHUP，收到就退出** —— 于是每天 00:00 日志轮转时
# 服务被杀一次（systemd Restart=always 5 秒后拉起，用户看到 502）。
# logrotate 配置已经改成 copytruncate（不再发 HUP），这里再兜一道：
# 以后不管谁发 HUP，都不会把站点弄挂。
try:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
except Exception:
    pass

_port = int(os.environ.get("PORT", "5000"))
_code_dir = os.path.dirname(os.path.abspath(__file__))
_data_dir = os.path.abspath(os.environ.get("MONO_DATA_DIR") or _code_dir)

# 2026-10-31：Neon 快照通道停用（配额已尽，每天失败 2,000+ 次刷日志）。
# 异地备份已由 backup_sync.sh 走 rsync 到洛杉矶备用机，这条 PostgreSQL 通道不再需要。
# 想恢复：把 MONO_SNAPSHOT 设成 1（或删掉这条环境变量）。
_snap_off = (os.environ.get("MONO_SNAPSHOT", "1") or "1").strip() == "0"

try:
    if not _snap_off:
        import cloud_snapshot as _snap_mod
        _snap_mod.restore_if_needed(_code_dir, _data_dir)
except Exception as _e:
    print("[快照] 恢复阶段异常（忽略不影响启动）:", _e, flush=True)

from waitress import serve
from app import app

try:
    from app import _boot_migrate
    with app.app_context():
        _boot_migrate()
except Exception as _e:
    print("[迁移] 启动迁移失败（忽略不影响服务）:", _e, flush=True)

try:
    # 2026-10-31：拆包后 `_oj_ensure_sweeper` 在 ojweb/core.py，不在 app 里。
    # 之前这里一直 import 失败（日志里那行 `cannot import name`），等于重启后
    # 判题巡检线程要等第一个访问 /oj 的人才会起来。
    from ojweb.core import _oj_ensure_sweeper
    _oj_ensure_sweeper()
except Exception as _e:
    print("[OJ] 判题巡检线程启动失败（忽略不影响服务）:", _e, flush=True)

if __name__ == "__main__":
    try:
        if not _snap_off:
            import cloud_snapshot as _snap_mod
            _snap_mod.start_watcher(_code_dir, _data_dir)
    except Exception as _e:
        print("[快照] 启动失败（忽略不影响服务）:", _e, flush=True)

    _bind = (os.environ.get("MONO_BIND") or "0.0.0.0").strip() or "0.0.0.0"

    print("MONO 社区（云端模式）监听 %s:%d" % (_bind, _port), flush=True)
    serve(app, host=_bind, port=_port, threads=32,
          channel_timeout=300, connection_limit=500, asyncore_use_poll=True)
