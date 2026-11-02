"""
同步 Evolve 静态资源的缓存版本号。

用法（在部署前跑一次）：
    python update_evolve_ver.py

它读 templates/base.html 里的站点版本号（如 v8.5.8），
把 static/evolve/index.html 里 main.js?v=xxx 换成同版本号，
强制浏览器重新拉取 2.3MB 的 main.js（否则改了代码用户看不到）。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(ROOT, "templates", "base.html")
INDEX = os.path.join(ROOT, "static", "evolve", "index.html")


def main():
    if not os.path.exists(BASE):
        print("找不到 templates/base.html")
        return 1
    if not os.path.exists(INDEX):
        print("找不到 static/evolve/index.html")
        return 1

    # 读站点版本号
    txt = io.open(BASE, encoding="utf-8", errors="ignore").read()
    m = re.search(r"v(\d+\.\d+\.\d+)", txt)
    if not m:
        print("base.html 里没找到版本号")
        return 1
    ver = m.group(1)

    # 更新 index.html
    raw = io.open(INDEX, "rb").read()
    crlf = b"\r\n" in raw
    s = raw.decode("utf-8").replace("\r\n", "\n")

    old = re.search(r'src="evolve/main\.js(\?v=[^"]*)?"', s)
    if not old:
        print("index.html 里没找到 main.js 引用")
        return 1

    s2 = re.sub(r'src="evolve/main\.js(\?v=[^"]*)?"',
                'src="evolve/main.js?v=%s"' % ver, s)
    out = s2.replace("\n", "\r\n") if crlf else s2
    io.open(INDEX, "wb").write(out.encode("utf-8"))

    print("已把 main.js 版本号更新为 v%s" % ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
