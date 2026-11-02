"""
MONO 社区 · 一键启动 + 全套自检（局域网版）
=====================================
启动前自动完成：运行环境 / 关键文件 / 依赖模块 / sqlite3 / 游戏引擎 / 数据库 / 端口
七项诊断，缺依赖自动安装，端口被占自动清理，然后启动服务。

用法：
    python start_server.py                # 诊断 + 启动（默认 5000 端口）
    python start_server.py --port 8080
    python start_server.py --check-only   # 只诊断不启动（排查问题用）

说明：
    - 局域网模式：waitress 仅监听 IPv4（默认 0.0.0.0），公网无法直连
    - 前台运行，服务日志直接显示在当前终端；按 Ctrl+C 停止
    - 本脚本对控制台编码做了护栏：Win7 / 任何代码页下都不会因为打印中文而崩溃
"""
import os
import sys
import time
import socket
import subprocess
import argparse
import platform

HERE = os.path.dirname(os.path.abspath(__file__))
APP = "app.py"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace", line_buffering=True)
    except Exception:
        try:
            import io
            _enc = getattr(_s, "encoding", None) or "gbk"
            _new = io.TextIOWrapper(_s.buffer, encoding=_enc,
                                    errors="replace", line_buffering=True)
            if _s is sys.stdout:
                sys.stdout = _new
            else:
                sys.stderr = _new
        except Exception:
            pass
os.environ["PYTHONIOENCODING"] = ":replace"
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "0")
_STDOUT_OK = True
try:
    sys.stdout.write("")
    sys.stdout.flush()
except Exception:
    _STDOUT_OK = False
if not _STDOUT_OK:
    try:
        _LOG_FALLBACK = open(os.path.join(HERE, "start_server.log"), "a",
                             encoding="utf-8", errors="replace")
        sys.stdout = _LOG_FALLBACK
        sys.stderr = _LOG_FALLBACK
    except Exception:
        try:
            sys.stdout = open(os.devnull, "w")
            sys.stderr = open(os.devnull, "w")
        except Exception:
            pass
import warnings as _warnings_mod
_warnings_mod.filterwarnings("ignore", message=".*doesn't match a supported version.*")

LOCKED_DEPS = [("flask", "2.2.5"), ("flask_socketio", "5.3.6"),
               ("socketio", "5.11.0"), ("engineio", "4.9.1"),
               ("waitress", "2.1.2")]
PIP_ARGS = ["flask==2.2.5", "flask-socketio==5.3.6", "python-socketio==5.11.0",
            "python-engineio==4.9.1", "waitress==2.1.2", "requests==2.31.0"]


def line(ch="-"):
    try:
        print(ch * 62)
    except Exception:
        pass


def say(tag, msg):
    """统一输出格式：[OK] / [FAIL] / [WARN] / [..]，任何终端都不崩。"""
    print(" %s %s" % (tag, msg))


def _decode(raw):
    if not raw:
        return ""
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")



def diag_python():
    line("=")
    say("[1/7]", "运行环境")
    v = "%d.%d.%d" % sys.version_info[:3]
    arch, _ = platform.architecture()
    say("[..]", "Python %s（%s）%s" % (v, arch, sys.executable))
    if v.startswith("3.6") or v.startswith("3.5"):
        say("[FAIL]", "Python 版本过旧（需 3.7+），依赖装不上是正常的")
    say("[..]", "系统：%s %s" % (platform.system(), platform.release()))
    if arch == "32bit":
        say("[WARN]", "32 位 Python：围棋/象棋等 64 位引擎跑不起来，"
                      "对应 PvAI 会自动降级为内置 AI（游戏本身不受影响）")
    return 0


def diag_files():
    line("=")
    say("[2/7]", "关键文件")
    need = [APP, "start_server.py", "admin_secret.py", "ai_config.py",
            "sensitive_words.txt"]
    bad = 0
    for f in need:
        if os.path.isfile(os.path.join(HERE, f)):
            say("[OK]", f)
        else:
            say("[FAIL]", "缺少 %s（请确认整个 test6 文件夹拷贝完整）" % f)
            bad += 1
    for d in ("static", "templates"):
        if not os.path.isdir(os.path.join(HERE, d)):
            say("[FAIL]", "缺少 %s 文件夹（请完整拷贝 test6）" % d)
            bad += 1
    tdir = os.path.join(HERE, "templates")
    n = len([x for x in os.listdir(tdir) if x.endswith(".html")]) if os.path.isdir(tdir) else 0
    if n >= 20:
        say("[OK]", "templates/ 模板目录（%d 个页面）" % n)
    else:
        say("[FAIL]", "templates/ 模板目录异常（只找到 %d 个页面，请重新拷贝）" % n)
        bad += 1
    try:
        probe = os.path.join(HERE, "_write_test.tmp")
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        say("[OK]", "目录可写")
    except Exception as e:
        say("[FAIL]", "目录不可写：%s（把 test6 移出只读/受限目录）" % e)
        bad += 1
    return bad


def _pip_ok():
    try:
        return subprocess.run([sys.executable, "-m", "pip", "--version"],
                              capture_output=True, timeout=120).returncode == 0
    except Exception:
        return False


def _repair_pip():
    """修复 pip：先 ensurepip 自带修复器，再从镜像/官方下载 get-pip.py。"""
    say("[..]", "pip 异常，先尝试 ensurepip 自带修复器...")
    try:
        subprocess.run([sys.executable, "-m", "ensurepip", "--default-pip", "--upgrade"],
                       capture_output=True, timeout=600)
    except Exception:
        pass
    if _pip_ok():
        say("[OK]", "pip 已由 ensurepip 修复")
        return True
    say("[..]", "ensurepip 失败，改用 get-pip.py 重装（清华镜像优先）...")
    for url in ("https://pypi.tuna.tsinghua.edu.cn/packages/get-pip.py",
                "https://bootstrap.pypa.io/get-pip.py"):
        gp = os.path.join(HERE, "get-pip.py")
        try:
            import urllib.request
            urllib.request.urlretrieve(url, gp)
            r = subprocess.run([sys.executable, gp, "--user", "--upgrade"],
                               capture_output=True, timeout=900)
            os.remove(gp)
            if r.returncode == 0 and _pip_ok():
                say("[OK]", "pip 已由 get-pip.py 修复")
                return True
        except Exception as e:
            say("[WARN]", "get-pip.py（%s）失败：%s" % (url.split('/')[2], e))
    if os.path.exists(os.path.join(HERE, "get-pip.py")):
        try:
            os.remove(os.path.join(HERE, "get-pip.py"))
        except Exception:
            pass
    return False


def _pin_pip_for_old_python():
    """Python 3.7/3.8 需要旧版 pip/setuptools 才能正常装包。"""
    pv = sys.version_info[0] * 10 + sys.version_info[1]
    pins = []
    if pv < 38:
        pins = ["pip<23", "setuptools<70", "wheel<0.40"]
    elif pv < 39:
        pins = ["pip<25"]
    if not pins:
        return
    say("[..]", "Python %s 使用兼容约束：%s" % (".".join(map(str, sys.version_info[:2])), " ".join(pins)))
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--upgrade"] + pins,
                       capture_output=True, timeout=600)
    except Exception:
        pass


def diag_deps():
    line("=")
    say("[3/7]", "依赖模块（缺失自动装，pip 损坏自动修）")
    if not _pip_ok():
        if not _repair_pip():
            say("[FAIL]", "pip 无法修复，请重新安装 Python（勾选 Add to PATH）")
            return 1
    _pin_pip_for_old_python()
    missing = []
    for mod, ver in LOCKED_DEPS:
        try:
            m = __import__(mod)
            got = getattr(m, "__version__", "") or getattr(m, "version", "")
            say("[OK]", "%s %s" % (mod, got))
        except Exception as e:
            say("[FAIL]", "%s 未安装（%s）" % (mod, e))
            missing.append(mod)
    if missing:
        say("[..]", "正在安装缺失依赖（固定版本，适配 Python 3.7）...")
        cmds = [[sys.executable, "-m", "pip", "install", "--user", "--force-reinstall"] + PIP_ARGS,
                [sys.executable, "-m", "pip", "install", "--user", "--force-reinstall",
                 "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"] + PIP_ARGS]
        done = False
        for c in cmds:
            try:
                r = subprocess.run(c, capture_output=True, timeout=600)
                if r.returncode == 0:
                    done = True
                    break
            except Exception:
                continue
        if not done:
            say("[FAIL]", "依赖自动安装失败，请手动执行：")
            print("      " + " ".join(cmds[0][3:]))
            return 1
        bad = 0
        for mod, ver in LOCKED_DEPS:
            try:
                __import__(mod)
                say("[OK]", "%s 已装好（复检通过）" % mod)
            except Exception:
                say("[FAIL]", "%s 安装后仍不可用" % mod)
                bad += 1
        return bad
    return 0


def _repair_sqlite():
    """sqlite3 模块缺失时自动补：从官方/华为镜像下载与本机 Python 同版本的
    embeddable 包，取出 sqlite3.dll 和 _sqlite3.pyd 放到对应位置。"""
    ver = "%d.%d.%d" % sys.version_info[:3]
    suffix = "win32" if platform.architecture()[0] == "32bit" else "amd64"
    name = "python-%s-embed-%s.zip" % (ver, suffix)
    urls = ["https://mirrors.huaweicloud.com/python/%s/%s" % (ver, name),
            "https://www.python.org/ftp/python/%s/%s" % (ver, name)]
    say("[..]", "正在自动修复 sqlite3（下载 %s）..." % name)
    zp = os.path.join(HERE, "_sqlite_fix.zip")
    import urllib.request
    data = None
    for u in urls:
        try:
            urllib.request.urlretrieve(u, zp)
            data = zp
            break
        except Exception as e:
            say("[WARN]", "下载失败（%s）：%s" % (u.split('/')[2], e))
    if not data:
        if os.path.exists(zp):
            try:
                os.remove(zp)
            except Exception:
                pass
        say("[FAIL]", "自动修复失败（下载不了）。手动方案：")
        print("      A. 用官方安装器 python-%s.exe 选 Repair（修复，不丢包）" % ver)
        print("      B. 从别的电脑复制两个文件：sqlite3.dll 放 Python 根目录，")
        print("         _sqlite3.pyd 放 Lib\lib-dynload\（位数要一致）")
        return 1
    try:
        import zipfile
        z = zipfile.ZipFile(zp)
        exe_dir = os.path.dirname(sys.executable)
        pyd_dir = os.path.join(sys.prefix, "Lib", "lib-dynload")
        for member, dst in (("sqlite3.dll", exe_dir), ("_sqlite3.pyd", pyd_dir)):
            hit = [i for i in z.namelist() if os.path.basename(i) == member]
            if not hit:
                continue
            os.makedirs(dst, exist_ok=True)
            with z.open(hit[0]) as f, open(os.path.join(dst, member), "wb") as g:
                g.write(f.read())
            say("[OK]", "已放置 %s -> %s" % (member, dst))
        z.close()
        try:
            import shutil
            shutil.rmtree(os.path.join(sys.prefix, "Lib", "__pycache__"), ignore_errors=True)
        except Exception:
            pass
        import sqlite3
        say("[OK]", "sqlite3 修复成功（版本 %s）" % sqlite3.sqlite_version)
        return 0
    except Exception as e:
        say("[FAIL]", "sqlite3 修复失败：%s" % e)
        return 1
    finally:
        try:
            os.remove(zp)
        except Exception:
            pass


def diag_sqlite():
    line("=")
    say("[4/7]", "sqlite3（精简版 Python 常缺失，缺了会自动补）")
    try:
        import sqlite3
        say("[OK]", "sqlite3 %s" % sqlite3.sqlite_version)
        return 0
    except Exception as e:
        say("[FAIL]", "sqlite3 不可用：%s" % e)
        say("[..]", "Python 目录：%s" % sys.prefix)
        return _repair_sqlite()


def diag_engines():
    line("=")
    say("[5/7]", "游戏引擎文件（PvAI 用；缺失只影响 AI 强度，游戏照常能玩）")
    items = [("国际象棋 Stockfish", os.path.join("stockfish", "stockfish-windows-x86-64-universal.exe")),
             ("中国象棋 Pikafish", os.path.join("pikafish", "pikafish.exe")),
             ("围棋 KataGo 主力", os.path.join("katago", "katago.exe")),
             ("围棋 KataGo 兜底", os.path.join("katago", "katago_eigen.exe")),
             ("围棋 GNU Go 兜底", os.path.join("gnugo", "gnugo.exe")),
             ("五子棋 RAPFI", os.path.join("rapfi", "engine")),
             ("黑白棋 Egaroucid", os.path.join("egaroucid", "Egaroucid_Console.exe"))]
    have_any_engine = False
    for name, rel in items:
        p = os.path.join(HERE, rel)
        if os.path.exists(p):
            say("[OK]", "%s：%s" % (name, rel))
            have_any_engine = True
        else:
            say("[WARN]", "%s：未找到 %s" % (name, rel))
    if not have_any_engine:
        say("[WARN]", "没有任何引擎文件：所有人机对战都会用内置 AI（依然可玩）")


def diag_db():
    line("=")
    say("[6/7]", "数据库")
    db = os.path.join(HERE, "data.db")
    if not os.path.isfile(db):
        say("[WARN]", "data.db 不存在（首次启动会自动创建）")
        return 0
    try:
        sz = os.path.getsize(db) / 1024.0
        say("[OK]", "data.db（%.1f KB）" % sz)
        return 0
    except Exception as e:
        say("[FAIL]", "data.db 无法读取：%s" % e)
        return 1



def find_listening_pids(port):
    """返回正在监听该端口的 PID 集合。
    注意：只有「状态确实是 LISTENING 且 PID 合法」才算占用。
    netstat 超时 / 执行失败 / 输出解析不出，一律按「没有占用」处理，
    绝不能把失败当成占用去 taskkill（曾出现误杀自己/误报占用的连锁问题）。"""
    pids = set()
    try:
        if sys.platform.startswith("win"):
            try:
                raw = subprocess.run(["netstat", "-ano"],
                                     capture_output=True, timeout=6).stdout
            except Exception:
                return pids
            tag = ("LISTENING", "listening", "监听")
            for ln in _decode(raw).splitlines():
                parts = ln.split()
                if len(parts) < 5:
                    continue
                if not parts[0].upper().startswith("TCP"):
                    continue
                if parts[3] not in tag and parts[3].upper() != "LISTENING":
                    continue
                addr = parts[1].rsplit(":", 1)
                if len(addr) == 2 and addr[1] == str(port) and parts[4].isdigit():
                    pids.add(parts[4])
        else:
            out = subprocess.run(["lsof", "-ti", "tcp:%d" % port],
                                 capture_output=True, timeout=10).stdout
            for pid in _decode(out).split():
                if pid.strip().isdigit():
                    pids.add(pid.strip())
    except Exception:
        pass
    return pids


def port_is_free(port, timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return False
    except Exception:
        return True


def _pid_running(pid):
    """Windows 下判断进程是否真的存在（tasklist 失败时保守返回 True，不误杀）。"""
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid, "/NH"],
                           capture_output=True, timeout=6)
        out = _decode(r.stdout)
        return ("No tasks" not in out) and ("没有运行的任务" not in out) and (str(pid) in out or "PID" in out)
    except Exception:
        return True


def cleanup_port(port, wait_max=10):
    """清理端口上的旧 MONO 服务。
    安全策略（重要）：只在「TCP 连接确实成功」时才判定端口被占用；
    并且只结束确实存在的旧进程，且当前进程自身 PID 绝不结束。"""
    if port_is_free(port):
        say("[OK]", "端口 %d 空闲" % port)
        return True
    pids = find_listening_pids(port)
    if not pids:
        say("[WARN]", "端口 %d 疑似被占用但查不到占用进程，直接尝试启动" % port)
        return False
    pids = set(p for p in pids if p != str(os.getpid()))
    if not pids:
        say("[WARN]", "端口 %d 占用者就是本进程，跳过清理" % port)
        return False
    say("[..]", "端口 %d 被旧服务占用（PID: %s），正在清理..."
        % (port, ", ".join(sorted(pids))))
    for pid in sorted(pids):
        if not _pid_running(pid):
            continue
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=10)
            say("[OK]", "已结束旧进程 PID=%s" % pid)
        except Exception as e:
            say("[WARN]", "结束进程 %s 失败：%s" % (pid, e))
    for _ in range(wait_max * 2):
        if port_is_free(port):
            say("[OK]", "端口 %d 已释放" % port)
            return True
        time.sleep(0.5)
    say("[WARN]", "端口 %d 仍可能被占用，继续尝试启动" % port)
    return False


def main():
    parser = argparse.ArgumentParser(description="MONO 社区 一键启动 + 自检（局域网版）")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--check-only", action="store_true",
                        help="只做诊断，不启动服务（排查问题用）")
    args = parser.parse_args()

    line("=")
    print("  MONO 社区 v6.7 · 一键启动 + 自检（局域网版）")
    line("=")

    os.chdir(HERE)
    errors = 0
    errors += diag_python()
    errors += diag_files()
    errors += diag_deps()
    errors += diag_sqlite()
    diag_engines()
    errors += diag_db()

    line("=")
    say("[7/7]", "端口")
    if args.check_only:
        cleanup_port(args.port)
        line("=")
        if errors:
            say("[结果]", "诊断完成：发现 %d 个 FAIL 项，请把本窗口全部输出贴给维护者" % errors)
        else:
            say("[结果]", "诊断完成：全部通过，可以正常启动")
        line("=")
        return

    print("  启动服务：%s --host %s --port %d%s"
          % (APP, args.host, args.port, " --debug" if args.debug else ""))
    cleanup_port(args.port)

    cmd = [sys.executable, APP, "--host", args.host, "--port", str(args.port)]
    if args.debug:
        cmd.append("--debug")
    log_path = os.path.join(HERE, "server_run.log")
    lf = None
    try:
        lf = open(log_path, "wb", buffering=0)
    except Exception as e:
        say("[WARN]", "无法创建日志文件 %s：%s（改为直接输出）" % (log_path, e))
    try:
        proc = subprocess.Popen(cmd, cwd=HERE,
                                stdout=(lf or None), stderr=subprocess.STDOUT)
    except Exception as e:
        say("[FAIL]", "启动失败：%s" % e)
        if lf:
            lf.close()
        sys.exit(1)
    line("=")
    print("  [已启动] PID=%d" % proc.pid)
    print("  本机访问：  http://127.0.0.1:%d" % args.port)
    print("  局域网访问：http://<本机IP>:%d" % args.port)
    print("  服务日志：  server_run.log（异常退出时这里会自动打印末尾内容）")
    print("  按 Ctrl+C 停止服务。")
    line("=")
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n [停止] 收到 Ctrl+C，正在结束服务（PID=%d）..." % proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                if sys.platform.startswith("win"):
                    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=10)
                else:
                    proc.kill()
            except Exception:
                pass
        print(" [完成] 服务已停止。")
    if lf:
        try:
            lf.close()
        except Exception:
            pass
    if proc.returncode not in (0, None):
        line("=")
        say("[FAIL]", "服务异常退出（退出码 %s）。" % proc.returncode)
        try:
            if os.path.isfile(log_path):
                with open(log_path, "rb") as f:
                    txt = _decode(f.read())
                tail = [x for x in txt.replace("\r\n", "\n").split("\n") if x.strip()][-40:]
                if tail:
                    print("  -------- 服务端最后 40 行输出（server_run.log）--------")
                    for t in tail:
                        print("  " + t)
                    print("  ------------------------------------------------------")
        except Exception:
            pass
        print("  排查方法：上面就是服务自己的报错原文；把它连同本窗口的")
        print("  [FAIL]/[WARN] 行一起发出来，可直接定位。")
        line("=")


if __name__ == "__main__":
    main()
