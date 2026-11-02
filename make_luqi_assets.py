import os
from PIL import Image, ImageChops

OUT = r"E:\9.2\文件夹\test6\static\luqi"
SRC = (r"C:\Users\weiha\.workbuddy-ai\clipboard-images"
       r"\clipboard-2026-09-24T15-10-51-643Z-179964ef.png")
os.chdir(OUT)

for f in os.listdir("."):
    if f.endswith(".png") and f not in ("logo-v1.png", "icon-v1.png"):
        os.remove(f)

im = Image.open(SRC).convert("RGB")
im.paste((255, 255, 255), (1215, 1405, 1536, 1536))
im.save("logo-v1.png", optimize=True)


def rgba(box):
    c = im.crop(box)
    r, g, b = c.split()
    mn = ImageChops.darker(ImageChops.darker(r, g), b)
    c = c.convert("RGBA")
    c.putalpha(mn.point(lambda v: min(255, max(0, int((255 - v) * 1.25)))))
    return c


def shrink(img, target_h):
    if img.height <= target_h:
        return img
    return img.resize((round(img.width * target_h / img.height), target_h), Image.LANCZOS)


def runs_of(img, min_gap):
    a = img.split()[3]
    w, h = img.size
    cols = [sum(1 for y in range(h) if a.getpixel((x, y)) > 40) for x in range(w)]
    out, start = [], None
    for x, v in enumerate(cols):
        if v > 0 and start is None:
            start = x
        elif v == 0 and start is not None:
            out.append((start, x))
            start = None
    if start is not None:
        out.append((start, w))
    merged = []
    for s, e in out:
        if merged and s - merged[-1][1] < min_gap:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged, cols


mark_src = rgba((378, 272, 1162, 998))
shrink(mark_src, 440).save("mark-v1.png", optimize=True)

name = rgba((378, 1012, 1162, 1198))
en = rgba((378, 1196, 1162, 1296))


def split(img, prefix, min_gap, target_h, expected):
    segs, cols = runs_of(img, min_gap)
    fixed = []
    for s, e in segs:
        if e - s < 1.75 * (img.width / expected):
            fixed.append((s, e))
            continue
        lo, hi = s + int((e - s) * 0.35), s + int((e - s) * 0.65)
        cut = min(range(lo, hi), key=lambda x: cols[x])
        fixed.append((s, cut))
        fixed.append((cut, e))
    for i, (s, e) in enumerate(fixed, 1):
        c = img.crop((max(0, s - 3), 0, min(img.width, e + 3), img.height))
        shrink(c, target_h).save("%s-%d.png" % (prefix, i), optimize=True)
    return len(fixed)


n1 = split(name, "name", 8, 160, 5)
n2 = split(en, "en", 6, 86, 11)
print("中文 %d 段、英文 %d 段" % (n1, n2))

icon = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
m = mark_src.copy()
m.thumbnail((760, 760), Image.LANCZOS)
icon.alpha_composite(m, ((1024 - m.width) // 2, (1024 - m.height) // 2))
icon.convert("RGB").save("icon-v1.png", optimize=True)

tot = 0
for f in sorted(os.listdir(".")):
    if f.endswith(".png"):
        s = os.path.getsize(f)
        tot += s
        print("  %-16s %7.1fKB" % (f, s / 1024))
print("总计 %.0fKB" % (tot / 1024))
