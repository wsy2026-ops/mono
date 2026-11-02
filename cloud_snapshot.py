"""MONO 社区 · 云端远程快照（Neon / 任意 PostgreSQL）

为什么需要：
    Render 等免费档托管没有持久磁盘，实例重启 / 重新部署会清空本地文件，
    data.db 里的用户、帖子、积分、聊天记录全部丢失。
    本模块把数据定期快照到外部 PostgreSQL（Neon 免费档即可），冷启动时自动拉回。

设计原则：
    1. 零侵入 —— SQLite 仍是唯一主库，不改动任何业务 SQL
    2. 静默降级 —— 未配置 DSN 或未装 psycopg2 时直接跳过，绝不影响主流程
    3. 只在内容变化时上传 —— 空闲时完全不碰 Neon（让它 scale to zero，省额度）
    4. 单主原则 —— 同一时间只让一处（云端 / 学校）往 Neon 写快照，否则会互相覆盖

配置方式（任一）：
    - 环境变量 MONO_NEON_DSN = postgresql://user:pass@host/db?sslmode=require
    - 项目根目录放 neon_dsn.txt（一行连接串）

可选环境变量：
    MONO_SNAPSHOT_INTERVAL  本地检查间隔秒数，默认 120
    MONO_SNAPSHOT_MEDIA     设为 0 关闭媒体文件快照（uploads/ + static/avatars/）
"""
import atexit
import gzip
import hashlib
import io
import os
import signal
import sqlite3
import sys as _sys
import threading
import time
import zipfile

_KEY_DB = "mono:data.db"
_KEY_MEDIA = "mono:media.zip"
MAX_MEDIA_BYTES = 50 * 1024 * 1024
DSN_FILE_NAMES = ("neon_dsn.txt", "neon_dsn")

_SNAPSHOT_SKIP_TABLES = ("monitor_samples", "attack_events", "ip_geo")


def _log(msg):
    try:
        print("[快照] " + str(msg), flush=True)
    except Exception:
        pass
    try:
        _lm = _sys.modules.get("mono_log")
        if _lm is not None:
            _lm.info("[快照] %s", msg)
    except Exception:
        pass


def get_dsn(code_dir):
    dsn = (os.environ.get("MONO_NEON_DSN") or "").strip()
    if dsn:
        return dsn
    for name in DSN_FILE_NAMES:
        path = os.path.join(code_dir, name)
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    return ""


def _connect(dsn):
    import psycopg2
    return psycopg2.connect(dsn, connect_timeout=20)


def _ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mono_snapshot (
                key        text PRIMARY KEY,
                data       bytea NOT NULL,
                size       bigint NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
    conn.commit()


def download(key, dsn):
    """取回 (blob, updated_at)；不存在返回 (None, None)"""
    conn = _connect(dsn)
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("select data, updated_at from mono_snapshot where key = %s", (key,))
            row = cur.fetchone()
        if not row:
            return None, None
        return bytes(row[0]), row[1]
    finally:
        conn.close()


def upload(key, blob, dsn):
    import psycopg2
    conn = _connect(dsn)
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mono_snapshot(key, data, size) VALUES(%s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                SET data = EXCLUDED.data, size = EXCLUDED.size, updated_at = now()
            """, (key, psycopg2.Binary(blob), len(blob)))
        conn.commit()
    finally:
        conn.close()


def make_db_snapshot(db_path):
    """用 SQLite 在线备份 API 生成一致快照（不阻塞写入、不丢 WAL 内容）

    快照里会剔除 _SNAPSHOT_SKIP_TABLES 里的运维数据表 —— 原因见该常量的注释
    （否则 monitor_samples 的持续写入会让快照每 2 分钟都判定为「有变化」）。
    """
    tmp = db_path + ".snap"
    for p in (tmp, tmp + "-wal", tmp + "-shm"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    src = sqlite3.connect(db_path, timeout=20)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            dropped = False
            for t in _SNAPSHOT_SKIP_TABLES:
                try:
                    dst.execute("DROP TABLE IF EXISTS %s" % t)
                    dropped = True
                except Exception:
                    pass
            if dropped:
                dst.commit()
                try:
                    dst.execute("VACUUM")
                except Exception:
                    pass
        finally:
            dst.close()
    finally:
        src.close()
    # 分块 gzip 后再返回：库里有大量文本测试数据，压缩率很高。
    # 关键是不把整个库读进内存 —— 之前 101MB 的库会同时占用「原始 + 压缩」两份，
    # 峰值 200MB+，而快照线程每两分钟就跑一次。
    out = io.BytesIO()
    with open(tmp, "rb") as src:
        with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6) as gz:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                gz.write(chunk)
    try:
        os.remove(tmp)
    except Exception:
        pass
    return out.getvalue()


def _maybe_gunzip(blob):
    """靠 gzip 魔数区分新旧快照：老快照是未压缩的裸 SQLite，必须还能恢复。"""
    if blob and len(blob) > 2 and blob[:2] == b"\x1f\x8b":
        return gzip.decompress(blob)
    return blob


def make_media_zip(data_dir):
    """打包 uploads/ 与 static/avatars/；空或超限返回 None"""
    buf = io.BytesIO()
    total = 0
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sub in ("uploads", os.path.join("static", "avatars")):
            root = os.path.join(data_dir, sub)
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    fp = os.path.join(dirpath, fn)
                    try:
                        total += os.path.getsize(fp)
                        if total > MAX_MEDIA_BYTES:
                            return None
                        z.write(fp, os.path.relpath(fp, data_dir))
                        count += 1
                    except Exception:
                        continue
    if count == 0:
        return None
    return buf.getvalue()


def restore_media(data_dir, dsn):
    """恢复媒体文件（已存在的跳过，不覆盖）"""
    blob, _ts = download(_KEY_MEDIA, dsn)
    if not blob:
        return 0
    blob = _maybe_gunzip(blob)
    n = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            parts = name.replace("\\", "/").split("/")
            if name.startswith("/") or ".." in parts:
                continue
            target = os.path.join(data_dir, *parts)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    continue
                with z.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                n += 1
            except Exception:
                continue
    return n


def restore_if_needed(code_dir, data_dir):
    """本地无数据库时从 Neon 拉回；返回 True 表示发生了恢复"""
    dsn = get_dsn(code_dir)
    if not dsn:
        return False
    try:
        import psycopg2
    except Exception:
        _log("未安装 psycopg2，跳过远程快照")
        return False

    db_path = os.path.join(data_dir, "data.db")
    restored = False
    try:
        if (not os.path.exists(db_path)) or os.path.getsize(db_path) == 0:
            blob, ts = download(_KEY_DB, dsn)
            if blob:
                os.makedirs(data_dir, exist_ok=True)
                blob = _maybe_gunzip(blob)
                with open(db_path, "wb") as f:
                    f.write(blob)
                _log("已从 Neon 恢复数据库：%d 字节，快照时间 %s" % (len(blob), ts))
                restored = True
            else:
                _log("Neon 中暂无快照，按全新数据库启动")
    except Exception as e:
        _log("数据库恢复失败（不影响启动）：%s" % str(e)[:160])

    if os.environ.get("MONO_SNAPSHOT_MEDIA", "1") != "0":
        try:
            n = restore_media(data_dir, dsn)
            if n:
                _log("已从 Neon 恢复 %d 个媒体文件" % n)
        except Exception as e:
            _log("媒体恢复失败（不影响启动）：%s" % str(e)[:160])
    return restored


class SnapshotWatcher(threading.Thread):
    def __init__(self, code_dir, data_dir, interval=120):
        super().__init__(daemon=True, name="mono-snapshot")
        self.code_dir = code_dir
        self.data_dir = data_dir
        self.interval = max(20, int(interval))
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._db_hash = None
        self._media_sig_cached = None
        self._last_ok = None
        self._last_err = None
        self._uploads = 0
        self._final_done = False

    @staticmethod
    def _calc_media_sig(data_dir):
        sig = []
        for sub in ("uploads", os.path.join("static", "avatars")):
            root = os.path.join(data_dir, sub)
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    fp = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(fp)
                        sig.append((os.path.relpath(fp, data_dir), int(st.st_mtime), st.st_size))
                    except Exception:
                        continue
        sig.sort()
        return tuple(sig)

    def _upload_db(self, dsn, db_path):
        blob = make_db_snapshot(db_path)
        digest = hashlib.sha256(blob).hexdigest()
        if digest == self._db_hash:
            return False
        upload(_KEY_DB, blob, dsn)
        self._db_hash = digest
        self._last_ok = time.strftime("%H:%M:%S")
        self._uploads += 1
        _log("数据已快照至 Neon（压缩后 %d KB）" % max(1, len(blob) // 1024))
        return True

    def _upload_media(self, dsn):
        if os.environ.get("MONO_SNAPSHOT_MEDIA", "1") == "0":
            return False
        sig = self._calc_media_sig(self.data_dir)
        if sig == self._media_sig_cached:
            return False
        blob = make_media_zip(self.data_dir)
        self._media_sig_cached = sig
        if not blob:
            return False
        upload(_KEY_MEDIA, blob, dsn)
        _log("媒体已快照至 Neon（%d KB）" % max(1, len(blob) // 1024))
        return True

    def run(self):
        """整体加护栏：任何异常都只记日志，绝不让线程静默死掉。"""
        try:
            dsn = get_dsn(self.code_dir)
            db_path = os.path.join(self.data_dir, "data.db")
            try:
                self._media_sig_cached = self._calc_media_sig(self.data_dir)
            except Exception:
                self._media_sig_cached = None
            _log("远程快照已启用：每 %d 秒检查一次（仅内容变化时上传）" % self.interval)
            self._stop_evt.wait(15)
            while not self._stop_evt.is_set():
                try:
                    if os.path.exists(db_path):
                        self._upload_db(dsn, db_path)
                    self._upload_media(dsn)
                    self._last_err = None
                except Exception as e:
                    self._last_err = str(e)[:160]
                    _log("快照失败（下一轮重试）：%s" % self._last_err)
                self._stop_evt.wait(self.interval)
        except Exception as e:
            _log("快照线程异常退出：%s" % str(e)[:200])

    def final_snapshot(self):
        """退出前最后快照（SIGTERM / atexit 触发）"""
        with self._lock:
            if self._final_done:
                return
            self._final_done = True
        try:
            dsn = get_dsn(self.code_dir)
            db_path = os.path.join(self.data_dir, "data.db")
            if dsn and os.path.exists(db_path):
                self._upload_db(dsn, db_path)
                _log("退出前已做最后一次快照")
        except Exception as e:
            _log("退出前快照失败：%s" % str(e)[:160])

    def status(self):
        return {
            "uploads": self._uploads,
            "last_ok": self._last_ok,
            "last_err": self._last_err,
            "interval": self.interval,
        }

    def stop(self):
        self._stop_evt.set()


def start_watcher(code_dir, data_dir):
    """启动后台快照线程；未配置 DSN 时返回 None"""
    dsn = get_dsn(code_dir)
    if not dsn:
        _log("未配置 MONO_NEON_DSN，远程快照未启用")
        return None
    try:
        import psycopg2
    except Exception:
        _log("未安装 psycopg2，远程快照未启用")
        return None
    try:
        interval = int(os.environ.get("MONO_SNAPSHOT_INTERVAL", "120") or 120)
    except Exception:
        interval = 120
    w = SnapshotWatcher(code_dir, data_dir, interval=interval)
    w.start()

    def _shutdown(*_a):
        try:
            w.stop()
            w.final_snapshot()
        except Exception:
            pass

    atexit.register(_shutdown)
    for sig in ("SIGTERM", "SIGINT"):
        try:
            s = getattr(signal, sig, None)
            if s is not None:
                signal.signal(s, lambda *_a: (_shutdown(), os._exit(0)))
        except Exception:
            pass
    global _ACTIVE_WATCHER
    _ACTIVE_WATCHER = w
    return w


_ACTIVE_WATCHER = None


def get_status():
    w = _ACTIVE_WATCHER
    return w.status() if w is not None else None
