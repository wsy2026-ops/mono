"""生成 MONO 社区 PWA PNG 图标（纯标准库，无第三方依赖）。
图案：深色渐变底 + 白色唱片（黑心）+ 金色音符圆点。"""
import os, struct, zlib, math

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

def _chunk(tag, data):
    c = tag + data
    return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

def make_icon(size):
    rows = []
    cx = cy = size / 2.0
    disc_r = size * 0.345
    hole_r = size * 0.115
    note_x, note_y = size * 0.72, size * 0.30
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            t = (x + y) / (2.0 * size)
            r = int(17 + (58 - 17) * t)
            g = int(17 + (58 - 17) * t)
            b = int(17 + (58 - 17) * t)
            d = math.hypot(x - cx, y - cy)
            if d <= disc_r:
                r, g, b = 247, 247, 247
                if d > disc_r * 0.55:
                    r, g, b = 255, 255, 255
            if d <= hole_r:
                r, g, b = 17, 17, 17
            nd = math.hypot(x - note_x, y - note_y)
            if nd <= size * 0.085:
                r, g, b = 255, 215, 0
            row += bytes((r, g, b))
        rows.append(bytes(row))
    raw = b''.join(rows)
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', ihdr) + \
          _chunk(b'IDAT', zlib.compress(raw, 9)) + _chunk(b'IEND', b'')
    return png

for s in (192, 512):
    path = os.path.join(OUT, "icon-%d.png" % s)
    with open(path, "wb") as f:
        f.write(make_icon(s))
    print("wrote", path, os.path.getsize(path), "bytes")
