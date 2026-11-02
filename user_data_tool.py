"""
MONO 社区 · 用户数据 打包 / 恢复 工具
====================================
只处理"纯用户数据"四个位置（代码、引擎都不动）：
    data.db             全部账号/帖子/聊天/积分/成绩
    .secret_key         登录密钥（不存在则跳过）
    uploads/            用户上传的图片与文件
    static/avatars/     用户上传的头像

功能：
    1. 打包用户数据  -> data_backups/用户数据_日期_时间.zip
    2. 恢复用户数据  -> 从压缩包覆盖本机数据（恢复前自动把当前数据再备份一份）

安全设计：
    - 打包/恢复前检测 data.db 是否被正在运行的服务占用，占用则拒绝操作
    - 恢复前强制自动备份当前数据（恢复前备份_*.zip），怎么都不至于丢
    - 恢复是"镜像式覆盖"：先清空 uploads/ 和 static/avatars/ 再解包，
      保证恢复后和打包时完全一致
"""
import os
import sys
import time
import zipfile
import sqlite3

try:
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.stderr.reconfigure(errors="replace", line_buffering=True)
except Exception:
    pass
os.environ["PYTHONIOENCODING"] = ":replace"

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data.db")
KEY = os.path.join(BASE, ".secret_key")
UPLOADS = os.path.join(BASE, "uploads")
AVATARS = os.path.join(BASE, "static", "avatars")
BACKUP_DIR = os.path.join(BASE, "data_backups")


def line():
    print("-" * 56)


def db_busy():
    """data.db 是否被正在运行的服务锁住。"""
    if not os.path.isfile(DB):
        return False
    try:
        c = sqlite3.connect(DB, timeout=1)
        c.execute("BEGIN EXCLUSIVE")
        c.rollback()
        c.close()
        return False
    except Exception:
        return True


def collect_files():
    """返回要打包的 (绝对路径, 压缩包内路径) 列表。"""
    items = []
    if os.path.isfile(DB):
        items.append((DB, "data.db"))
    for ext in ("-wal", "-shm", "-journal"):
        p = DB + ext
        if os.path.isfile(p):
            items.append((p, "data.db" + ext))
    if os.path.isfile(KEY):
        items.append((KEY, ".secret_key"))
    for root, _dirs, files in os.walk(UPLOADS):
        for f in files:
            p = os.path.join(root, f)
            items.append((p, "uploads/" + os.path.relpath(p, UPLOADS).replace("\\", "/")))
    if os.path.isdir(AVATARS):
        for root, _dirs, files in os.walk(AVATARS):
            for f in files:
                p = os.path.join(root, f)
                items.append((p, "static/avatars/" + os.path.relpath(p, AVATARS).replace("\\", "/")))
    return items


def do_pack(tag):
    """tag: '用户数据' 或 '恢复前备份'。返回 zip 路径；失败返回 None。"""
    if not os.path.isfile(DB):
        print("[FAIL] 找不到 data.db（本目录还没有任何用户数据）")
        return None
    if db_busy():
        print("[FAIL] data.db 正被服务占用，请先关闭正在运行的服务再打包")
        print("       （关掉服务窗口，或在服务窗口按 Ctrl+C）")
        return None
    items = collect_files()
    if not items:
        print("[FAIL] 没有可打包的用户数据")
        return None
    if not os.path.isdir(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    name = "%s_%s.zip" % (tag, time.strftime("%Y%m%d_%H%M%S"))
    zpath = os.path.join(BACKUP_DIR, name)
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for abs_path, arc in items:
            z.write(abs_path, arc)
            n += 1
        z.writestr("manifest.txt",
                   "MONO 用户数据包\n时间: %s\n文件数: %d\n来源目录: %s\n"
                   % (time.strftime("%Y-%m-%d %H:%M:%S"), n, BASE))
    sz = os.path.getsize(zpath) / 1024.0
    print("[OK] 已生成：%s（%d 个文件，%.1f KB）" % (zpath, n, sz))
    return zpath


def clear_dir(d):
    """清空目录内容（目录本身保留）。"""
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        return 0
    n = 0
    for root, dirs, files in os.walk(d, topdown=False):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
                n += 1
            except Exception:
                pass
        for dd in dirs:
            try:
                os.rmdir(os.path.join(root, dd))
            except Exception:
                pass
    return n


def do_restore(zpath):
    if not os.path.isfile(zpath):
        print("[FAIL] 压缩包不存在：%s" % zpath)
        return False
    try:
        z = zipfile.ZipFile(zpath)
    except Exception as e:
        print("[FAIL] 打不开压缩包：%s" % e)
        return False
    names = z.namelist()
    if "data.db" not in names:
        print("[FAIL] 包里没有 data.db，不是本工具生成的用户数据包")
        z.close()
        return False
    n_up = sum(1 for x in names if x.startswith("uploads/"))
    n_av = sum(1 for x in names if x.startswith("static/avatars/"))
    print("包内内容：data.db%s | 上传文件 %d 个 | 头像 %d 个"
          % (" + .secret_key" if ".secret_key" in names else "", n_up, n_av))
    if db_busy():
        print("[FAIL] data.db 正被服务占用，请先关闭正在运行的服务再恢复")
        z.close()
        return False

    print("[..] 恢复前先把当前用户数据备份一份...")
    if os.path.isfile(DB) and do_pack("恢复前备份") is None:
        print("[FAIL] 当前数据备份失败，已取消恢复（原数据未动）")
        z.close()
        return False

    try:
        print("[..] 清空本机 uploads/ 与 static/avatars/ ...")
        clear_dir(UPLOADS)
        clear_dir(AVATARS)
        print("[..] 解压覆盖...")
        cnt = 0
        for info in z.infolist():
            nm = info.filename
            if nm in ("manifest.txt",) or nm.endswith("/"):
                continue
            if nm == "data.db" or nm.startswith("data.db-") or nm == ".secret_key":
                dst = os.path.join(BASE, os.path.basename(nm))
            elif nm.startswith("uploads/"):
                rel = nm[len("uploads/"):]
                dst = os.path.join(UPLOADS, rel)
            elif nm.startswith("static/avatars/"):
                rel = nm[len("static/avatars/"):]
                dst = os.path.join(AVATARS, rel)
            else:
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(info) as f, open(dst, "wb") as g:
                g.write(f.read())
            cnt += 1
        z.close()
        print("[OK] 恢复完成：共写入 %d 个文件" % cnt)
        print("[提示] 现在运行 python start_server.py 即可。新代码启动时会自动把")
        print("       旧数据库升级到最新结构，不需要任何手动操作。")
        return True
    except Exception as e:
        print("[FAIL] 恢复出错：%s" % e)
        print("       当前数据在恢复前的备份包里，可再恢复回来。")
        return False


def choose_zip():
    """列出 data_backups 里的包让用户选，或手动输入路径。"""
    cands = []
    if os.path.isdir(BACKUP_DIR):
        cands = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".zip"))
    print("可用的压缩包（在 data_backups/ 里）：")
    if cands:
        for i, f in enumerate(cands):
            sz = os.path.getsize(os.path.join(BACKUP_DIR, f)) / 1024.0
            print("  %2d. %s（%.1f KB）" % (i + 1, f, sz))
    else:
        print("  （空）")
    raw = input("输入序号选择，或直接粘贴压缩包完整路径（回车取消）: ").strip().strip('"')
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(cands):
        return os.path.join(BACKUP_DIR, cands[int(raw) - 1])
    return raw


def main():
    print("=" * 56)
    print("  MONO 社区 · 用户数据 打包 / 恢复")
    print("  只动数据（data.db / 密钥 / uploads / 头像），不动代码")
    print("=" * 56)
    while True:
        line()
        print("  1. 打包用户数据（生成压缩包，可拷去别的电脑）")
        print("  2. 恢复用户数据（用压缩包覆盖本机数据）")
        print("  0. 退出")
        line()
        try:
            c = input("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if c == "1":
            do_pack("用户数据")
        elif c == "2":
            zp = choose_zip()
            if zp:
                sure = input("确认覆盖本机用户数据？(y/N): ").strip().lower()
                if sure == "y":
                    do_restore(zp)
                else:
                    print("已取消，未做任何改动")
        elif c == "0" or c == "":
            return


if __name__ == "__main__":
    main()
