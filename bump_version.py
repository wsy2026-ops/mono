import os, re, sys

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'base.html')


def main():
    raw = open(BASE, 'rb').read()
    crlf = raw.count(b'\r\n') > 0
    # 把所有行尾形式（\r\n、单个 \r、连续好几个 \r）统一收敛成一个 \n。
    # 必须收敛成「一个」而不是逐个替换 —— 否则一串 \r 会被拆成好几行。
    # 不这么做的话，下面 replace('\n','\r\n') 会把现成的 \r\n 又转一次变成 \r\r\n，
    # 每跑一次每行就多一个 \r（历史遗留已经攒到 35 个/行了）。
    s = re.sub(r'\r+\n|\r+|\n', '\n', raw.decode('utf-8'))
    m = re.search(r'MONO 社区 v(\d+)\.(\d+)\.(\d+)', s)
    if not m:
        print('未找到版本号', file=sys.stderr)
        return 1
    maj, mid, pat = (int(x) for x in m.groups())
    pat += 1
    # 满十进一，且**不使用 0**：3.5.10 → 3.6.1（不是 3.6.0）。
    # 版本号从 1 起算，用户 2026-10-21 定的规矩。
    if pat >= 10:
        pat, mid = 1, mid + 1
    if mid >= 10:
        mid, maj = 1, maj + 1
    ver = '%d.%d.%d' % (maj, mid, pat)
    s = re.sub(r'MONO 社区 v\d+\.\d+\.\d+', 'MONO 社区 v' + ver, s, count=1)
    s = re.sub(r"(manifest\.json'\) \}\}\?v=)\d+\.\d+\.\d+", r'\g<1>' + ver, s, count=1)
    open(BASE, 'wb').write((s.replace('\n', '\r\n') if crlf else s).encode('utf-8'))
    print(ver)
    return 0


if __name__ == '__main__':
    sys.exit(main())
