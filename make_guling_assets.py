import json
import os
import random

from PIL import Image

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "guling")
os.makedirs(OUT, exist_ok=True)
random.seed(73)

T = 16
COLS = 8


def new_tile():
    return Image.new("RGBA", (T, T), (0, 0, 0, 0))


def noise_fill(img, base, jitter, region=None):
    px = img.load()
    for y in range(T):
        for x in range(T):
            if region and not region(x, y):
                continue
            j = random.randint(-jitter, jitter)
            px[x, y] = (
                max(0, min(255, base[0] + j)),
                max(0, min(255, base[1] + j)),
                max(0, min(255, base[2] + j)),
                255,
            )


def tile_void():
    img = new_tile()
    noise_fill(img, (8, 8, 11), 2)
    return img


def tile_dirt():
    img = new_tile()
    noise_fill(img, (74, 59, 42), 7)
    px = img.load()
    for _ in range(6):
        x, y = random.randint(0, T - 2), random.randint(0, T - 2)
        px[x, y] = (58, 45, 30, 255)
        px[x + 1, y] = (58, 45, 30, 255)
    return img


def tile_brick_floor():
    img = new_tile()
    noise_fill(img, (86, 82, 78), 4)
    px = img.load()
    for x in range(T):
        px[x, 0] = (62, 59, 56, 255)
        px[x, 8] = (62, 59, 56, 255)
    for y in range(T):
        px[0, y] = (62, 59, 56, 255)
        px[8 if y < 8 else 4, y] = (62, 59, 56, 255)
    return img


def tile_wall():
    img = new_tile()
    noise_fill(img, (56, 56, 64), 5)
    px = img.load()
    for x in range(T):
        px[x, 0] = (78, 78, 90, 255)
        px[x, 1] = (70, 70, 82, 255)
    for y in range(T):
        px[0, y] = (44, 44, 52, 255)
    return img


def tile_door():
    img = new_tile()
    noise_fill(img, (94, 69, 42), 5)
    px = img.load()
    for x in range(T):
        px[x, 3] = (66, 47, 27, 255)
        px[x, 12] = (66, 47, 27, 255)
    px[7, 8] = (198, 168, 90, 255)
    px[8, 8] = (198, 168, 90, 255)
    return img


def tile_stairs():
    img = new_tile()
    noise_fill(img, (104, 104, 114), 4)
    px = img.load()
    for y in range(0, T, 4):
        for x in range(T):
            px[x, y] = (70, 70, 80, 255)
            px[x, y + 1] = (128, 128, 140, 255)
    return img


def tile_torch(phase):
    img = tile_wall()
    px = img.load()
    for y in range(7, 14):
        px[7, y] = (107, 74, 42, 255)
        px[8, y] = (84, 58, 33, 255)
    flame = [(232, 168, 64), (240, 208, 96)] if phase == 0 else [(208, 128, 48), (240, 200, 88)]
    fx, fy = 7, 3
    pts = [(0, 2), (1, 1), (1, 2), (2, 1), (1, 3), (2, 2), (0, 1), (2, 0), (1, 0)]
    for i, (dx, dy) in enumerate(pts):
        c = flame[i % 2]
        px[fx + dx, fy + dy] = (c[0], c[1], c[2], 255)
    return img


tiles = [
    tile_void(),
    tile_dirt(),
    tile_brick_floor(),
    tile_wall(),
    tile_door(),
    tile_stairs(),
    tile_torch(0),
    tile_torch(1),
]

atlas = Image.new("RGBA", (COLS * T, T), (0, 0, 0, 0))
for i, t in enumerate(tiles):
    atlas.paste(t, (i * T, 0))
atlas.save(os.path.join(OUT, "tiles.png"))

PAL = {
    "h": (36, 30, 28),
    "s": (232, 184, 138),
    "p": (44, 42, 48),
    "b": (70, 62, 54),
}
CLOTHES = [(58, 110, 170), (170, 62, 62), (74, 150, 92), (190, 158, 60)]

FRONT1 = [
    "....hhhh....",
    "....hhhh....",
    "....ssss....",
    "....ssss....",
    "...cccccc...",
    "..cccccccc..",
    ".cccccccccc.",
    ".cccccccccc.",
    "..cccccccc..",
    "...c.cc.c...",
    "....pp.pp...",
    "....pp.pp...",
    "....pp.pp...",
    "....pp.pp...",
    "....bb.bb...",
    "............",
]
FRONT2 = [r.replace("....pp.pp...", "...pp....pp..") if i >= 10 else r for i, r in enumerate(FRONT1)]
BACK = [r.replace("ssss", "hhhh") for r in FRONT1]
BACK2 = [r.replace("....pp.pp...", "...pp....pp..") if i >= 10 else r for i, r in enumerate(BACK)]
SIDE1 = [
    "............",
    "....hhhh....",
    "....hhhh....",
    "....ssss....",
    "....cccc....",
    "...cccccc...",
    "...cccccc...",
    "...cccccc...",
    "...cccccc...",
    "....c..c....",
    "....pppp....",
    "....pppp....",
    "....pppp....",
    "....pppp....",
    "....b..b....",
    "............",
]
SIDE2 = [r.replace("....pppp....", "...pp..pp....") if i >= 10 else r for i, r in enumerate(SIDE1)]


def draw_rows(rows, cloth):
    img = new_tile()
    px = img.load()
    for y, line in enumerate(rows):
        for x, ch in enumerate(line):
            if ch == ".":
                continue
            if ch == "c":
                c = cloth
            else:
                c = PAL[ch]
            px[x + 2, y] = (c[0], c[1], c[2], 255)
    return img


sprites = []
for cloth in CLOTHES:
    for rows in (FRONT1, FRONT2, BACK1 := BACK, BACK2, SIDE1, SIDE2, [r[::-1] for r in SIDE1], [r[::-1] for r in SIDE2]):
        sprites.append(draw_rows(rows, cloth))

MOLE1 = [
    "................",
    "................",
    "................",
    "................",
    "..kk......kk....",
    ".kkkk....kkkk...",
    ".kkkkkkkkkkkkkk.",
    "kKkkkkkkkkkkkkkk",
    "kkkkkkkkkkkkkkkk",
    ".kkkkkkkkkkkkkkk",
    ".kkkkkkkkkkkkkk.",
    "..kkkkkkkkkkkk..",
    "...kk..kk..kk...",
    "................",
    "................",
    "................",
]
MOLE2 = [r if i not in (12,) else "...kk..kk..kk...".replace("...kk..kk..kk...", "..kk....kk..kk..") for i, r in enumerate(MOLE1)]

MOLE_PAL = {"k": (126, 92, 60), "K": (240, 220, 200)}


def draw_mole(rows):
    img = new_tile()
    px = img.load()
    for y, line in enumerate(rows):
        for x, ch in enumerate(line):
            if ch == ".":
                continue
            c = MOLE_PAL[ch]
            px[x, y] = (c[0], c[1], c[2], 255)
    return img


sprites.append(draw_mole(MOLE1))
sprites.append(draw_mole(MOLE2))

sp_cols = 8
sp_rows = (len(sprites) + sp_cols - 1) // sp_cols
sheet = Image.new("RGBA", (sp_cols * T, sp_rows * T), (0, 0, 0, 0))
for i, s in enumerate(sprites):
    sheet.paste(s, ((i % sp_cols) * T, (i // sp_cols) * T))
sheet.save(os.path.join(OUT, "sprites.png"))

MAP_ROWS = [
    "                                        ",
    "      ############      ############    ",
    "      #,,,,,,,,,#      #,,,,,,,,,#     ",
    "      #,###D###,#      #,###D###,#     ",
    "      #,#.....#,#      #,#.....#,#     ",
    "      #,#.....#,#      #,#.....#,#     ",
    "      #D#.....#D#        #.....#       ",
    "        .....#,#         #####D#        ",
    "#############.# #####################  ",
    ",,,,,,,,,,,,,#.# #,,,,,,,,,,,,,,,,,,,  ",
    ",,,,,,,,,,,,,D.# #,,,,,,,,,,,,,,,,,,,  ",
    ",,,,,,,,,,,,,,#.# #,,,,,,,,,,,,,,,,,,  ",
    "###############.# #####D###############",
    "              #.#     #.#               ",
    "   ########## #.# #####.##### ######### ",
    "   #,,,,,,,,# #.# #.......#  #,,,,,,,,# ",
    "   #,,,,,,,,# #.# #.......#  #,,,,,,,,# ",
    "   #,#######,# #D# #.....,#  #,######,# ",
    "   #,#T...T#,#     #..T..,#  #,##T##,#  ",
    "   #,#.....#D#######.....,#  #,#...#,#  ",
    "   #,#.....#,,,,,,,,#.....D  #D...#,#  ",
    "   #D#####,#,,,,,,,,#,,####  #,#...#,#  ",
    "        #,#,,,,,,,,#,#,##  ###,#...#,#  ",
    "        #D#########D#,#,#  #,#####D#,#  ",
    "        #,,,,,,,,,,,,,,,,,#  #,,,,,,,#  ",
    "        #,,,,,,,,,,,,,,,,,#  #########  ",
    "        ###########D#######             ",
    "                    #                   ",
    "                                        ",
    "                                        ",
]

TILE_CH = {
    " ": 0,
    ",": 1,
    ".": 2,
    "#": 3,
    "D": 4,
    "T": 5,
    "S": 6,
}
mh = len(MAP_ROWS)
mw = max(len(r) for r in MAP_ROWS)
grid = []
for r in MAP_ROWS:
    r = r.ljust(mw)
    grid.append([TILE_CH[c] for c in r])

data = {
    "w": mw,
    "h": mh,
    "tiles": grid,
    "solid": [0, 3],
    "spawn": [20, 26],
}
with open(os.path.join(OUT, "map.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)

print("tiles:", len(tiles), "sprites:", len(sprites), "map:", mw, "x", mh)
