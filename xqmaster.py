"""中国象棋大师 —— 对局核心逻辑。

纯函数模块：只处理棋局状态，不碰 Flask、不碰数据库、不碰 socket。
这样服务端和测试都能直接用，客户端那份 JS 也照着这里的规则写。

状态结构（可直接 json 序列化）：
{
  "board": 10 行 × 9 列，元素是 None 或棋子 dict,
  "turn": "red" / "black",
  "ap": 剩余行动点,
  "round": 回合数,
  "nodmg": 连续无伤害的回合数,
  "dmg_round": 本轮是否有人受伤,
  "over": 是否已结束,
  "winner": "red"/"black"/"draw"/None,
  "reason": 结束原因,
}
棋子 dict：{"t": 类型, "s": 阵营, "hp": 当前血, "mh": 最大血, "atk": 攻击, "id": 唯一号, "mv": 本回合是否移动过,
         "acts": 本回合剩余行动次数, "irn": 铁壁生效过的 ply, "frz": 被震击锁到哪个 ply,
         "gh": 潜行生效的 ply, "ghn": 潜行剩余免疫次数, "fatk": 轻骑的免费攻击}

走法与攻击的统一约定（新增棋子一律照此解释，前端 targetsOf 必须一致）：
  · 「直线 N」   = 上下左右四方向，最多 N 格，遇到棋子就停下
  · 「相邻」     = 上下左右四方向的相邻 1 格
  · 攻击「直线 N 格」= 四方向上、距离不超过 N 的**第一个**棋子；不穿子。
    所以弓（直线 3 格）打得到 2~3 格外的第一个敌人，但打不到贴着它的那个。
  · 只有明确写了「可越过棋子」的（鹰）才忽略路径上的棋子。
  · 攻击范围里写「不可攻击」的棋子，rng 恒为空集。

行动点模型：全局每回合 3 点行动点（state["ap"]），每个棋子另有 acts（本回合还能行动几次）。
  一次行动同时消耗 1 点 ap 和 1 次 acts。鼓（催战 / 战鼓）只抬高 acts，不抬高 ap，
  加成取最大值不累加 —— 否则帝 + 鼓 会让行动点失控。
"""

import random

ROWS = 10
COLS = 9
PIECES = {
    "king":     {"zh": ("帅", "帅"), "hp": 300, "atk": 30, "trait": "坐镇",
                 "tdesc": "九宫范围内的己方棋子受到伤害降低 40%",
                 "grade": "legend"},
    "advisor":  {"zh": ("仕", "仕"), "hp": 120, "atk": 25, "trait": "护主",
                 "tdesc": "相邻的九宫内友方棋子受到攻击时，替其分担 20% 伤害",
                 "grade": "rare"},
    "elephant": {"zh": ("相", "相"), "hp": 140, "atk": 25, "trait": "守垒",
                 "tdesc": "受到的远程伤害（车/炮）额外减少 25%",
                 "grade": "rare"},
    "horse":    {"zh": ("马", "马"), "hp": 110, "atk": 55, "trait": "踏营",
                 "tdesc": "本回合先移动、再攻击时伤害 +20%",
                 "grade": "epic"},
    "chariot":  {"zh": ("车", "车"), "hp": 150, "atk": 60, "trait": "破甲",
                 "tdesc": "攻击时无视目标 25% 的减伤",
                 "grade": "legend"},
    "cannon":   {"zh": ("炮", "炮"), "hp": 100, "atk": 70, "trait": "轰击",
                 "tdesc": "对目标身后 1 格造成 40% 溅射伤害",
                 "grade": "legend"},
    "lord":     {"zh": ("君", "君"), "hp": 500, "atk": 100, "trait": "守玺",
                 "tdesc": "全军受到的伤害降低 10%",
                 "grade": "mythic"},
    "grain":    {"zh": ("粮", "粮"), "hp": 130, "atk": 0, "trait": "补济",
                 "tdesc": "相邻友方每回合恢复 30 血量",
                 "grade": "legend"},
    "pawn":     {"zh": ("兵", "兵"), "hp": 80, "atk": 35, "trait": "死战",
                 "tdesc": "越过河界后伤害增加 30%",
                 "grade": "common"},
    # ── 扩展第一批 · 普通 6 ──
    "zu":       {"zh": ("卒", "卒"), "hp": 70, "atk": 30, "trait": "老卒",
                 "tdesc": "越过河界后攻击翻倍",
                 "grade": "common"},
    "min":      {"zh": ("民", "民"), "hp": 90, "atk": 30, "trait": "征召",
                 "tdesc": "阵亡时相邻友军回复 15 血量",
                 "grade": "common"},
    "shi":      {"zh": ("矢", "矢"), "hp": 60, "atk": 35, "trait": "齐射",
                 "tdesc": "与另一个矢相邻时攻击 +30%",
                 "grade": "common"},
    "pai":      {"zh": ("牌", "牌"), "hp": 100, "atk": 30, "trait": "掩护",
                 "tdesc": "相邻友军受到的溅射伤害减半",
                 "grade": "common"},
    "tan":      {"zh": ("探", "探"), "hp": 80, "atk": 35, "trait": "游走",
                 "tdesc": "每回合可以移动两次",
                 "grade": "common"},
    "huo":      {"zh": ("火", "火"), "hp": 70, "atk": 40, "trait": "纵火",
                 "tdesc": "攻击附带 10 点溅射，自身也受伤 5 点",
                 "grade": "common"},
    # ── 扩展第一批 · 卓越 13 ──
    "dun":      {"zh": ("盾", "盾"), "hp": 140, "atk": 30, "trait": "铁壁",
                 "tdesc": "每回合受到的第一次伤害减半",
                 "grade": "epic"},
    "gong":     {"zh": ("弓", "弓"), "hp": 90, "atk": 55, "trait": "劲射",
                 "tdesc": "攻击距离 3 格，但打不到相邻的敌人",
                 "grade": "epic"},
    "nu":       {"zh": ("弩", "弩"), "hp": 100, "atk": 50, "trait": "穿透",
                 "tdesc": "攻击同时伤害路径上的第二个目标",
                 "grade": "epic"},
    "mao":      {"zh": ("矛", "矛"), "hp": 130, "atk": 45, "trait": "拒马",
                 "tdesc": "被攻击时，攻击者受到 30% 反伤",
                 "grade": "epic"},
    "fu":       {"zh": ("斧", "斧"), "hp": 120, "atk": 65, "trait": "劈山",
                 "tdesc": "攻击无视目标的一切减伤",
                 "grade": "epic"},
    "chui":     {"zh": ("锤", "锤"), "hp": 130, "atk": 50, "trait": "震击",
                 "tdesc": "命中后目标下回合不能移动",
                 "grade": "epic"},
    "qi":       {"zh": ("旗", "旗"), "hp": 110, "atk": 0, "trait": "号令",
                 "tdesc": "相邻友军攻击 +10%",
                 "grade": "epic"},
    "gu":       {"zh": ("鼓", "鼓"), "hp": 110, "atk": 0, "trait": "催战",
                 "tdesc": "相邻友军每回合可多行动一次",
                 "grade": "epic"},
    "zhen":     {"zh": ("阵", "阵"), "hp": 130, "atk": 0, "trait": "结阵",
                 "tdesc": "相邻友军减伤 12%",
                 "grade": "epic"},
    "yao":      {"zh": ("药", "药"), "hp": 110, "atk": 0, "trait": "施药",
                 "tdesc": "回合结束时，相邻友军回复 15 血量",
                 "grade": "epic"},
    "chi":      {"zh": ("斥", "斥"), "hp": 90, "atk": 40, "trait": "侦察",
                 "tdesc": "显示敌方棋子的攻击范围",
                 "grade": "epic"},
    "ci":       {"zh": ("刺", "刺"), "hp": 100, "atk": 60, "trait": "暗杀",
                 "tdesc": "对生命低于 40% 的目标伤害翻倍",
                 "grade": "epic"},
    "jing":     {"zh": ("镜", "镜"), "hp": 120, "atk": 40, "trait": "反照",
                 "tdesc": "受到攻击时，反弹 25% 伤害给攻击者",
                 "grade": "epic"},
    # ── 扩展第二批 · 传说 6 ──
    "hu":       {"zh": ("虎", "虎"), "hp": 150, "atk": 80, "trait": "猛扑",
                 "tdesc": "击杀后立即再行动一次",
                 "grade": "legend"},
    "bao":      {"zh": ("豹", "豹"), "hp": 130, "atk": 65, "trait": "疾行",
                 "tdesc": "每回合可以行动两次",
                 "grade": "legend"},
    "ying":     {"zh": ("鹰", "鹰"), "hp": 130, "atk": 70, "trait": "翱翔",
                 "tdesc": "移动时无视阻挡",
                 "grade": "legend"},
    "lang":     {"zh": ("狼", "狼"), "hp": 140, "atk": 60, "trait": "群猎",
                 "tdesc": "相邻友军每有 1 个，攻击提升 8%",
                 "grade": "legend"},
    "yi":       {"zh": ("医", "医"), "hp": 150, "atk": 0, "trait": "回春",
                 "tdesc": "主动行动可治疗相邻友军，回复其 25% 生命上限",
                 "grade": "legend"},
    "jiang":    {"zh": ("匠", "匠"), "hp": 150, "atk": 0, "trait": "铸兵",
                 "tdesc": "相邻友军攻击提升 15%",
                 "grade": "legend"},
    # ── 扩展第二批 · 神话 2 ──
    "di":       {"zh": ("帝", "帝"), "hp": 320, "atk": 60, "trait": "御驾",
                 "tdesc": "全军每回合行动点 +1",
                 "grade": "mythic"},
    "long":     {"zh": ("龙", "龙"), "hp": 300, "atk": 110, "trait": "龙威",
                 "tdesc": "攻击时无视目标 30% 的减伤",
                 "grade": "mythic"},
    # ── 扩展第三批 · 优良 8（蓝色那档，原先只有仕 / 相两枚）──
    # 定位是「便宜的新玩法」，**不做卓越棋子的廉价版**（用户 2026-10-08 明确要求）。
    # 每个都开一个别处没有的机制：换位 / 替伤 / 蓄力 / 集火标记 / 禁疗 / 复活 / 软控 / DoT。
    "yi2":      {"zh": ("移", "移"), "hp": 95, "atk": 0, "trait": "移形",
                 "tdesc": "可与相邻友军交换位置",
                 "grade": "rare"},
    "wei":      {"zh": ("卫", "卫"), "hp": 110, "atk": 26, "trait": "替死",
                 "tdesc": "相邻友军将受到致命伤害时，由它代受（一场一次）",
                 "grade": "rare"},
    "xu":       {"zh": ("蓄", "蓄"), "hp": 90, "atk": 30, "trait": "蓄势",
                 "tdesc": "本回合不攻击，则下回合攻击翻倍",
                 "grade": "rare"},
    "biao":     {"zh": ("标", "标"), "hp": 95, "atk": 34, "trait": "标记",
                 "tdesc": "被它攻击过的敌人，友军对其伤害提升 15%",
                 "grade": "rare"},
    "duan":     {"zh": ("断", "断"), "hp": 100, "atk": 32, "trait": "断粮",
                 "tdesc": "命中后目标下回合不能回血",
                 "grade": "rare"},
    "can":      {"zh": ("残", "残"), "hp": 85, "atk": 36, "trait": "残兵",
                 "tdesc": "阵亡时以 30% 生命复活（一场一次）",
                 "grade": "rare"},
    "bian":     {"zh": ("鞭", "鞭"), "hp": 92, "atk": 34, "trait": "缠斗",
                 "tdesc": "命中后目标下回合移动格数 -1",
                 "grade": "rare"},
    "teng":     {"zh": ("藤", "藤"), "hp": 95, "atk": 30, "trait": "缠绕",
                 "tdesc": "己方回合结束时，相邻敌人损失 6 点生命",
                 "grade": "rare"},
}

GRADES = {
    "mythic": {"zh": "神话", "c": "#D92027"},
    "legend": {"zh": "传说", "c": "#E07B10"},
    "epic":   {"zh": "卓越", "c": "#8E3FD6"},
    "rare":   {"zh": "优良", "c": "#2C6FE0"},
    "common": {"zh": "普通", "c": "#3B9A5F"},
}

RED_KING_MV = [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]
ADV_MV      = [(-1,-1),(-1,1),(1,-1),(1,1)]
ELE_MV      = [(-2,-2),(-2,2),(2,-2),(2,2)]
HORSE_MV    = [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]
DIR4        = [(-1,0),(1,0),(0,-1),(0,1)]

AP_PER_TURN = 3
NODMG_DRAW = 10
REDUCTION_CAP = 0.70
CANNON_SPLASH = 0.40
CANNON_SCREEN = 0.30
CHARIOT_PIERCE = 0.25
CHARIOT_SWEEP = 0.30
HORSE_CHARGE = 0.20
HORSE_AURA = 0.15
KING_AURA = 0.40
KING_MENACE = 0.20
LORD_WARD = 0.10          # 守玺：全军受到的伤害 -10%
LORD_MIGHT = 0.08         # 天威：每阵亡一名己方棋子，全军攻击 +8%
LORD_MIGHT_CAP = 0.40     # 天威上限
LINEUP_TOTAL = 16
GRAIN_HEAL = 30           # 补济：相邻友方每回合回血
GRAIN_WARD = 0.20         # 振饱：两格范围内友方减伤
UNLOCK_COST = 30           # 初始一套之外的棋子，解锁都要 30 碎片（写死）
INITIAL_PIECES = ("king", "advisor", "elephant", "horse", "chariot", "cannon", "pawn")


def is_locked_type(t):
    """初始的一套棋子之外，都得先花 30 碎片解锁，之后才能升级、上阵。"""
    return t not in INITIAL_PIECES          # 一方的棋子总数（帅/君二选一，所以不是 17）
ADVISOR_GUARD = 0.20
ADVISOR_VENGEANCE = 0.30
ELEPHANT_ARMOR = 0.25
ELEPHANT_FIELD = 0.20
PAWN_RUSH = 0.30
PAWN_GRIT = 0.25

# ── 扩展第一批的常量（普通 6 + 卓越 13）──
ZU_RIVER = 1.0            # 老卒：过河后攻击翻倍（+100%）
ZU_RUI = 0.30             # 锐卒：过河后额外 +30%
MIN_NEAR_HEAL = 15        # 征召：阵亡时相邻友军回血
MIN_ALL_HEAL = 5          # 乡勇：阵亡时全军回血
SHI_VOLLEY = 0.30         # 齐射：与另一个矢相邻时攻击 +30%
SHI_RAIN = 0.50           # 箭雨：对目标相邻敌人造成半伤
PAI_SPLASH_HALF = 0.50    # 掩护：相邻友军受到的溅射伤害减半
PAI_WARD = 0.08           # 重牌：相邻友军受到的伤害 -8%
TAN_FREE_ATK = 1          # 轻骑：移动后每回合一次免费攻击
HUO_SPLASH = 10           # 纵火：溅射固定伤害
HUO_LIAO = 8              # 燎原：溅射伤害 +8
HUO_SELF = 5              # 纵火：自身受伤
DUN_IRON = 0.50           # 铁壁：每回合首次受伤减半
DUN_ROCK = 0.10           # 磐石：相邻友军减伤
GONG_CHAIN = 0.50         # 连珠：未击杀则追加半伤
NU_PIERCE = 0.50          # 穿透伤害衰减（重弩取消衰减）
MAO_THORN = 0.30          # 拒马：反伤
MAO_THORN_HALF = 0.15     # 枪阵：相邻友军获得一半反伤
FU_TRUE = 0.15            # 开山：附带目标当前生命 15% 的真实伤害
QI_ORDER = 0.10           # 号令：相邻友军攻击 +10%
QI_BANNER = 0.05          # 大纛：全军攻击 +5%
GU_ACTS = 1               # 催战 / 战鼓：行动次数 +1
ZHEN_FORM = 0.12          # 结阵：相邻友军减伤 12%
ZHEN_DOUBLE = 2.0         # 方圆：阵与阵相邻时效果翻倍
YAO_NEAR = 15             # 施药：回合结束相邻友军回血
YAO_ALL = 8               # 良方：全军每回合回血
CI_THRESHOLD = 0.40       # 暗杀：目标生命低于 40% 触发
CI_MULT = 2.0             # 暗杀：伤害翻倍
JING_MIRROR = 0.25        # 反照
JING_MIRROR_G2 = 0.45     # 明镜
CHI_FIRST_AP = 1          # 先机：先手方首回合行动点 +1

# ── 扩展第二批的常量（传说 6 + 神话 2）──
DI_AP = 1                 # 御驾：全军每回合行动点 +1
DI_MIGHT = 0.15           # 亲征：全军攻击 +15%
LONG_WARD = 0.30          # 龙威：无视目标 30% 减伤
HU_MIGHT = 0.20           # 百兽：连续击杀每次 +20%（不封顶，回合开始清零）
BAO_SHADOW = 1            # 残影：移动后免疫一次
YING_PIERCE = 0.20        # 鹰眼：无视 20% 减伤
LANG_PACK = 0.08          # 群猎：每个相邻友军 +8%
LANG_LEAD = 0.12          # 头狼：相邻友军 +12%
YI_HEAL = 0.25            # 回春：回复目标 25% 生命上限
JIANG_FORGE = 0.15        # 铸兵：相邻友军 +15%
JIANG_MASTER = 0.08       # 神工：全军 +8%

# ── 扩展第三批的常量（优良 8：便宜的新玩法，不是卓越的廉价版）──
YI_SWAP = 1               # 移形：可与相邻友军换位
YI_SWAP_G2 = 2            # 挪移：距离放宽到 2 格
WEI_SAVE_HEAL = 0.30      # 死士：代受之后自己回复 30% 生命
XU_MULT = 2.0             # 蓄势：蓄力后的攻击倍率
BIAO_MARK = 0.15          # 标记：友军对标记目标的伤害加成
BIAO_TURNS = 1            # 标记持续回合
BIAO_TURNS_G2 = 2         # 军令
DUAN_TURNS = 1            # 断粮 / 断援持续回合
CAN_REVIVE = 0.30         # 残兵：复活时的生命比例
CAN_REVIVE_G2 = 0.50      # 不屈
BIAN_SLOW = 1             # 缠斗：目标下回合移动格数 -1
BIAN_SLOW_CAP = 3         # 不限格数的棋子被缠住后，下回合最多走几格
BIAN_DOWN = 0.10          # 铁鞭：同时降攻 10%
TENG_DOT = 6              # 缠绕：相邻敌人回合末掉血
TENG_DOT_G2 = 12          # 荆棘

# 鞭·缠斗要按「走法步数」扣，所以得知道每种棋子的基础步数（没列的一律算 1 步）
MOVE_STEPS = {"grain": 2, "tan": 2, "chi": 3, "long": 3, "mao": 2,
              "hu": 2, "lang": 2, "shao": 2, "bian": 2, "bao": 2, "ying": 3, "ji": 2}
# 跳跃走法（日字 / 田字）没有「步数」可言，缠斗对它们不生效
JUMP_MOVE = ("horse", "qi2", "elephant")

NO_ATTACK = ("grain", "qi", "gu", "zhen", "yao", "yi", "jiang", "yi2")   # 攻击范围恒为空
# 走法与攻击分别算、不能把 mv 直接并进 rng 的棋子
SPLIT_MV_RNG = ("chariot", "cannon", "shi", "nu", "gong", "mao", "tan", "chi", "ci",
                "hu", "bao", "ying", "lang", "long",
                "bian", "biao", "duan")
# 走法无视阻挡的棋子（翱翔 / 腾云）
FLY_MOVE = ("ying",)

BOSS_TYPES = ("king", "lord", "di")     # 主帅位，三选一

PIECE_COUNTS = {"king": 1, "lord": 1, "grain": 2, "advisor": 2, "elephant": 2, "horse": 2,
                "chariot": 2, "cannon": 2, "pawn": 5,
                "zu": 8, "min": 6, "shi": 4, "pai": 4, "tan": 3, "huo": 3,
                "dun": 2, "gong": 2, "nu": 2, "mao": 2, "fu": 2, "chui": 2,
                "qi": 2, "gu": 1, "zhen": 2, "yao": 2, "chi": 2, "ci": 2, "jing": 2,
                "hu": 2, "bao": 2, "ying": 2, "lang": 2, "yi": 2, "jiang": 2,
                "di": 1, "long": 1,
                "yi2": 2, "wei": 2, "xu": 2, "biao": 2,
                "duan": 2, "can": 1, "bian": 2, "teng": 2}

MAX_LEVEL = 30
SHARDS_PER_DROP = 30
WIN_DROPS = 5
LOSE_DROPS = 2
DRAW_DROPS = 3
GROWTH_STEP = 0.02
TRAIT_STEP = 0.02
TRAIT_CAP_STEPS = 4

YIZHU = {"win": 120, "lose": 50, "draw": 80}

LOTTERY = {
    "cost": 10,
    "grades": [
        {"grade": "mythic", "weight": 3},
        {"grade": "legend", "weight": 9},
        {"grade": "epic",   "weight": 25},
        {"grade": "rare",   "weight": 28},
        {"grade": "common", "weight": 35},
    ],
    "amounts": [
        {"count": 30, "weight": 5},
        {"count": 20, "weight": 15},
        {"count": 10, "weight": 35},
        {"count": 5,  "weight": 45},
    ],
}


def _weighted(pairs, rnd):
    total = sum(w for _, w in pairs)
    x = rnd.random() * total
    acc = 0.0
    for item, w in pairs:
        acc += w
        if x < acc:
            return item
    return pairs[-1][0]


def roll_lottery(rnd):
    """按 LOTTERY 权重抽一次：(grade, type, count)。品质池为空时顺延到下一档。"""
    grades = LOTTERY["grades"]
    order = [g["grade"] for g in grades]
    idx = order.index(_weighted([(g["grade"], g["weight"]) for g in grades], rnd))
    while True:
        grade = order[idx]
        pool = [t for t in PIECE_COUNTS if PIECES[t]["grade"] == grade]
        if pool:
            break
        idx = (idx + 1) % len(order)
    amount = _weighted([(a["count"], a["weight"]) for a in LOTTERY["amounts"]], rnd)
    return grade, rnd.choice(pool), amount

NEW_TRAITS = {
    "grain":    {"name": "振饱", "desc": "两格范围内的友方获得 20% 减伤"},
    "lord":     {"name": "天威", "desc": "每阵亡一名己方棋子，全军攻击提升 8%（上限 40%）"},
    "king":     {"name": "御敌", "desc": "与敌方将帅处于同一直线且无遮挡时，敌方全部棋子攻击降低 20%"},
    "advisor":  {"name": "死卫", "desc": "被消灭的瞬间，对击杀者施加攻击降低 30% 的打击，持续 1 回合"},
    "elephant": {"name": "掠阵", "desc": "身处敌方半场时，周围 1 格敌方棋子攻击降低 20%"},
    "horse":    {"name": "八面威", "desc": "周围两格内每存在一名敌方棋子，自身攻击提升 15%"},
    "chariot":  {"name": "横扫", "desc": "攻击命中时，对目标同直线相邻一格的敌方单位造成 30% 伤害"},
    "cannon":   {"name": "重炮", "desc": "攻击时若炮架是己方棋子，本次攻击伤害提升 30%"},
    "pawn":     {"name": "浴血", "desc": "越过河界后，单次受到的伤害不超过自身生命上限的 25%"},
    "zu":       {"name": "锐卒", "desc": "越过河界后额外提升 30% 攻击"},
    "min":      {"name": "乡勇", "desc": "阵亡时全军回复 5 血量"},
    "shi":      {"name": "箭雨", "desc": "攻击时对目标相邻的敌人造成半伤"},
    "pai":      {"name": "重牌", "desc": "相邻友军受到的伤害降低 8%"},
    "tan":      {"name": "轻骑", "desc": "移动后可再攻击一次（每回合一次）"},
    "huo":      {"name": "燎原", "desc": "纵火的溅射伤害提升 8 点"},
    "dun":      {"name": "磐石", "desc": "相邻友军受到的伤害降低 10%"},
    "gong":     {"name": "连珠", "desc": "攻击后若未击杀，追加一次半伤"},
    "nu":       {"name": "重弩", "desc": "穿透伤害不再衰减"},
    "mao":      {"name": "枪阵", "desc": "相邻友军也获得一半反伤"},
    "fu":       {"name": "开山", "desc": "攻击附带目标当前生命 15% 的真实伤害"},
    "chui":     {"name": "碎地", "desc": "震击同时使目标相邻的敌人也不能移动"},
    "qi":       {"name": "大纛", "desc": "全军攻击 +5%，旗在场上才生效"},
    "gu":       {"name": "战鼓", "desc": "全军每回合都可多行动一次"},
    "zhen":     {"name": "方圆", "desc": "阵与阵相邻时，结阵效果翻倍"},
    "yao":      {"name": "良方", "desc": "全军每回合回复 8 血量"},
    "chi":      {"name": "先机", "desc": "先手方首回合行动点 +1"},
    "ci":       {"name": "影袭", "desc": "击杀后潜行，下回合免疫一次攻击"},
    "jing":     {"name": "明镜", "desc": "反弹提升到 45%，且反弹无视减伤"},
    "hu":       {"name": "百兽", "desc": "连续击杀时攻击提升 20%（可叠加）"},
    "bao":      {"name": "残影", "desc": "移动后，本回合受到的第一次攻击无效"},
    "ying":     {"name": "鹰眼", "desc": "攻击时无视目标 20% 的减伤"},
    "lang":     {"name": "头狼", "desc": "相邻友军攻击提升 12%"},
    "yi":       {"name": "妙手", "desc": "治疗的溢出部分转为护盾"},
    "jiang":    {"name": "神工", "desc": "己方所有棋子攻击提升 8%"},
    "di":       {"name": "亲征", "desc": "帝在场时，己方所有棋子攻击提升 15%"},
    "long":     {"name": "腾云", "desc": "每回合可以无视阻挡移动一次"},
    "yi2":      {"name": "挪移", "desc": "可与 2 格内的友军交换位置"},
    "wei":      {"name": "死士", "desc": "代受之后自己回复 30% 生命"},
    "xu":       {"name": "满弓", "desc": "蓄力后的那一击不会被闪避"},
    "biao":     {"name": "军令", "desc": "标记持续 2 回合"},
    "duan":     {"name": "断援", "desc": "目标下回合无法获得任何增益"},
    "can":      {"name": "不屈", "desc": "复活时生命提升到 50%"},
    "bian":     {"name": "铁鞭", "desc": "缠斗同时使目标攻击降低 10%"},
    "teng":     {"name": "荆棘", "desc": "缠绕的伤害提升到 12 点"},
}


def upgrade_cost(level):
    """从 level 升到 level+1 的碎片费用：1-4 级 20，之后每 5 级一组 +10。"""
    if level <= 4:
        group = 0
    else:
        group = (level - 5) // 5 + 1
    return 20 + 10 * group


def piece_growth(t, level):
    """等级 → 成长包。非 5 倍数级交替 +2% HP/ATK（先 HP），
    5 倍数级特性 +2pp（20 级封顶），20 级解锁第二特性。"""
    level = max(1, min(int(level or 1), MAX_LEVEL))
    n_ups = (level - 1) - level // 5
    hp_ups = (n_ups + 1) // 2
    atk_ups = n_ups // 2
    d = PIECES[t]
    mh = int(round(d["hp"] * (1 + GROWTH_STEP * hp_ups)))
    atk = int(round(d["atk"] * (1 + GROWTH_STEP * atk_ups)))
    return {"lv": level, "hp": mh, "mh": mh, "atk": atk,
            "tr": TRAIT_STEP * min(level // 5, TRAIT_CAP_STEPS),
            "g2": level >= 20}


def default_layout():
    """红方视角的开局摆位。"""
    out = []
    back = ["chariot", "horse", "elephant", "advisor", "king",
            "advisor", "elephant", "horse", "chariot"]
    for c, t in enumerate(back):
        out.append([t, 9, c])
    out.append(["cannon", 7, 1])
    out.append(["cannon", 7, 7])
    for c in (0, 2, 4, 6, 8):
        out.append(["pawn", 6, c])
    return out


def piece_zone(t, r, c):
    """红方视角的摆位限制：帅/仕限九宫，其余限己方半场。"""
    if not isinstance(r, int) or not isinstance(c, int):
        return False
    if not in_board(r, c) or r < 5:
        return False
    if t in ("king", "lord", "di", "advisor") and not in_palace("red", r, c):
        return False
    return True


def validate_lineup(layout):
    """校验自定义阵容：格式、区域、数量、重叠。返回 (ok, msg)。"""
    if not isinstance(layout, list):
        return False, "阵容格式错误"
    cnt = {}
    seen = set()
    for item in layout:
        if not (isinstance(item, (list, tuple)) and len(item) == 3):
            return False, "阵容格式错误"
        t, r, c = item[0], item[1], item[2]
        if t not in PIECES:
            return False, "存在未知棋子"
        if (r, c) in seen:
            return False, "有棋子叠在同一格"
        seen.add((r, c))
        if not piece_zone(t, r, c):
            return False, PIECES[t]["zh"][0] + "不能摆在那个位置"
        cnt[t] = cnt.get(t, 0) + 1
    # 帅 / 君 / 帝 三选一：加起来只能上阵一个
    if sum(cnt.get(t, 0) for t in BOSS_TYPES) != 1:
        return False, "帅、君、帝只能上阵一个"
    # 各类型不得超过上限（照着中国象棋：仕2 相2 马2 车2 炮2 兵5，粮2）
    for t, n in PIECE_COUNTS.items():
        if t in BOSS_TYPES:
            continue
        if cnt.get(t, 0) > n:
            return False, "%s最多 %d 枚" % (PIECES[t]["zh"][0], n)
    if len(layout) != LINEUP_TOTAL:
        return False, "一共要摆 %d 枚棋子，现在摆了 %d 枚" % (LINEUP_TOTAL, len(layout))
    return True, ""


def new_state(red_layout=None, black_layout=None,
              red_levels=None, black_levels=None):
    board = [[None] * COLS for _ in range(ROWS)]
    nid = [0]

    def put(t, side, r, c, level):
        nid[0] += 1
        g = piece_growth(t, level)
        board[r][c] = {"t": t, "s": side, "hp": g["hp"], "mh": g["mh"],
                       "atk": g["atk"], "id": nid[0], "mv": False,
                       "lv": g["lv"], "tr": g["tr"], "g2": g["g2"], "acts": 1,
                       "ad": True}   # ad：蓄·蓄势用，初始 True 免得刚上场就白送一次蓄力

    def place(side, layout, levels):
        lv = levels or {}
        for item in layout:
            t, r, c = item[0], item[1], item[2]
            if side == "black":
                r, c = 9 - r, c
            put(t, side, r, c, lv.get(t, 1))

    place("black", black_layout or default_layout(), black_levels)
    place("red", red_layout or default_layout(), red_levels)

    st = {"board": board, "turn": "red", "ap": AP_PER_TURN, "round": 1, "ply": 0,
          "nodmg": 0, "dmg_round": False, "over": False,
          "winner": None, "reason": ""}
    _recompute_acts(st, "red")
    _recompute_acts(st, "black")
    st["ap"] = _ap_max(st, "red")       # 帝·御驾在开局就要生效
    # 斥 · 先机：先手方（红）首回合行动点 +1
    for r in range(ROWS):
        for c in range(COLS):
            q = board[r][c]
            if q and q["s"] == "red" and q["t"] == "chi" and q.get("g2"):
                st["ap"] += CHI_FIRST_AP
                break
        else:
            continue
        break
    return st


def in_board(r, c):
    return 0 <= r < ROWS and 0 <= c < COLS


def in_palace(side, r, c):
    if not (3 <= c <= 5):
        return False
    return r >= 7 if side == "red" else r <= 2


def crossed_river(side, r):
    return r <= 4 if side == "red" else r >= 5


def piece_at(state, r, c):
    if not in_board(r, c):
        return None
    return state["board"][r][c]


def find_piece(state, pid):
    for r in range(ROWS):
        for c in range(COLS):
            p = state["board"][r][c]
            if p and p["id"] == pid:
                return p, r, c
    return None, -1, -1


# 八方向：所有「相邻友军 / 相邻敌人」类的光环与触发都用它
# （走法与攻击里的「相邻」是四方向，见文件头的约定）
AROUND8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _own_near(state, r, c, side, t, g2=False):
    """八方向相邻是否有己方指定类型的存活棋子。返回那个棋子或 None。"""
    for dr, dc in AROUND8:
        q = piece_at(state, r + dr, c + dc)
        if q and q["s"] == side and q["t"] == t and q["hp"] > 0 and (q.get("g2") or not g2):
            return q
    return None


def _own_count_near(state, r, c, side, t, exclude_id=None):
    """八方向相邻的己方指定类型棋子数量。"""
    n = 0
    for dr, dc in AROUND8:
        q = piece_at(state, r + dr, c + dc)
        if q and q["s"] == side and q["t"] == t and q["hp"] > 0 and q["id"] != exclude_id:
            n += 1
    return n


def _has_own(state, side, t, g2=False):
    """全盘是否存在己方指定类型的存活棋子。"""
    for r in range(ROWS):
        for c in range(COLS):
            q = state["board"][r][c]
            if q and q["s"] == side and q["t"] == t and q["hp"] > 0 and (q.get("g2") or not g2):
                return True
    return False


def _stunned(state, p):
    """震击：被锁住的棋子在本 ply 不能移动（仍可攻击）。"""
    return p.get("frz") is not None and state.get("ply", 0) <= p["frz"]


def _can_heal(state, p):
    """断粮（断）：被断的棋子下回合不能回血。"""
    return not (p.get("nh") is not None and state.get("ply", 0) <= p["nh"])


def _can_buff(state, p):
    """断援（断 20 级）：被断的棋子下回合拿不到任何增益（护盾 / 光环）。"""
    return not (p.get("nb") is not None and state.get("ply", 0) <= p["nb"])


def _dot_damage(state, target, amount, events, r, c):
    """持续伤害（藤·缠绕）：纯环境伤害。

    刻意**不走** _apply_damage —— 它不该触发镜的反照、不该消耗盾的铁壁、
    也不该被算进「无伤害回合」的判和计数（用户 2026-10-08 明确要求）。
    护盾照样能挡（护盾就是护盾），斩杀主帅也照样结束对局。
    """
    if not target or amount <= 0:
        return
    if target.get("sh"):
        absorb = min(target["sh"], amount)
        target["sh"] -= absorb
        amount -= absorb
        events.append({"k": "shield", "r": r, "c": c, "v": absorb, "id": target["id"]})
        if amount <= 0:
            return
    target["hp"] = max(0, target["hp"] - amount)
    events.append({"k": "dot", "r": r, "c": c, "v": amount, "id": target["id"]})
    if target["hp"] <= 0:
        state["board"][r][c] = None
        events.append({"k": "kill", "r": r, "c": c, "id": target["id"],
                       "t": target["t"], "s": target["s"]})
        if target["t"] in BOSS_TYPES:
            who = ("红方" if target["s"] == "red" else "黑方") + PIECES[target["t"]]["zh"][0]
            _finish(state, "black" if target["s"] == "red" else "red",
                    "斩首成功 —— " + who + "血量归零")


def _ap_max(state, side):
    """该方本回合的行动点上限。

    基础 3 点，帝·御驾 +1。**同类加成取最大值不累加** —— 否则帝 + 鼓（以及
    以后任何同类效果）会让行动点彻底失控。鼓只抬高棋子行动次数，不在这一档里。
    """
    bonus = DI_AP if _has_own(state, side, "di") else 0
    return AP_PER_TURN + bonus


def _recompute_acts(state, side):
    """回合开始时重算该方每个棋子的行动次数。

    默认 1 次；探的游走自带 2 次；鼓的催战 / 战鼓各 +1，但取最大值不累加
    （否则帝 + 鼓 会让行动点彻底失控）。
    """
    gu_all = False
    gu_near = set()
    for r in range(ROWS):
        for c in range(COLS):
            q = state["board"][r][c]
            if not (q and q["s"] == side and q["t"] == "gu" and q["hp"] > 0):
                continue
            if q.get("g2"):
                gu_all = True
            for dr, dc in AROUND8:
                if in_board(r + dr, c + dc):
                    gu_near.add((r + dr, c + dc))
    for r in range(ROWS):
        for c in range(COLS):
            p = state["board"][r][c]
            if not p or p["s"] != side:
                continue
            base = 2 if p["t"] in ("tan", "bao") else 1
            bonus = GU_ACTS if (gu_all or (r, c) in gu_near) else 0
            p["acts"] = max(base, 1 + bonus)
            p.pop("fatk", None)      # 轻骑的免费攻击每回合重置
            p.pop("fatd", None)
            p.pop("kb", None)        # 虎·百兽的连续击杀数
            p.pop("cl", None)        # 龙·腾云的无视阻挡次数
            if p["t"] == "long" and p.get("g2"):
                p["cl"] = 1          # 腾云：每回合一次无视阻挡
            # 蓄势（蓄）：上一回合没出手 → 这一回合的攻击翻倍。
            # ad 初始为 True（当它是「上一回合出过手」），免得刚上场就白送一次蓄力。
            p["ch"] = 1 if p.get("ad") is False else 0
            p["ad"] = False


def _reflect_ratio(state, dfn, dfn_r, dfn_c):
    """返回 (反伤比例, 是否无视减伤)。

    镜·反照 25%（明镜 45% 且无视减伤）；矛·拒马 30%；
    枪阵（矛 20 级）让相邻友军也拿到一半反伤。
    """
    if dfn["t"] == "jing":
        return (JING_MIRROR_G2, True) if dfn.get("g2") else (JING_MIRROR, False)
    if dfn["t"] == "mao":
        return MAO_THORN, False
    for dr, dc in AROUND8:
        q = piece_at(state, dfn_r + dr, dfn_c + dc)
        if q and q["s"] == dfn["s"] and q["hp"] > 0 and q["t"] == "mao" and q.get("g2"):
            return MAO_THORN_HALF, False
    return 0.0, False


def _splash_adjust(state, target, r, c, amount):
    """掩护（牌）：相邻有己方牌的棋子，受到的溅射伤害减半。"""
    if _own_near(state, r, c, target["s"], "pai"):
        return max(1, int(round(amount * PAI_SPLASH_HALF)))
    return amount


def legal_targets(state, r, c):
    """返回 (可走格集合, 可攻击格集合, 可治疗格集合)，元素是 (row, col)。

    各种棋子的走法逐个实现 —— 车和炮的「移动」和「攻击」范围是故意不一致的。
    第三个集合是「可以施加友方动作的目标格」：
      · 医·回春 → 施加治疗
      · 移·移形 → 交换位置
    所以里面恰恰是**自己人占住的格子**，别跟 mv 一起过滤掉。
    """
    p = piece_at(state, r, c)
    if not p:
        return set(), set(), set()
    t, side = p["t"], p["s"]
    mv, rng, heal = set(), set(), set()

    def add_mv(a, b):
        if in_board(a, b):
            mv.add((a, b))

    def add_rng(a, b):
        if in_board(a, b):
            rng.add((a, b))

    def add_heal(a, b):
        if in_board(a, b):
            heal.add((a, b))

    fwd = -1 if side == "red" else 1

    if t in ("king", "lord"):
        for dr, dc in RED_KING_MV:
            if in_palace(side, r + dr, c + dc):
                add_mv(r + dr, c + dc)
                add_rng(r + dr, c + dc)
    elif t == "grain":
        # 直走 1~2 格，可过河；被棋子挡住就停；不参与攻击（不加 rng）
        for dr, dc in DIR4:
            for step in (1, 2):
                a, b = r + dr * step, c + dc * step
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
    elif t == "advisor":
        for dr, dc in ADV_MV:
            if in_palace(side, r + dr, c + dc):
                add_mv(r + dr, c + dc)
    elif t == "elephant":
        for dr, dc in ELE_MV:
            add_mv(r + dr, c + dc)
    elif t == "horse":
        for dr, dc in HORSE_MV:
            add_mv(r + dr, c + dc)
    elif t == "chariot":
        for dr, dc in DIR4:
            k = 1
            while True:
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if k <= 3:
                    add_rng(a, b)
                if state["board"][a][b]:
                    break
                add_mv(a, b)
                k += 1
    elif t == "cannon":
        for dr, dc in DIR4:
            screen = False
            k = 1
            while True:
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                occ = state["board"][a][b] is not None
                if not screen:
                    if occ:
                        screen = True
                    else:
                        add_mv(a, b)
                elif occ:
                    add_rng(a, b)
                    break
                k += 1
    elif t == "pawn":
        add_mv(r + fwd, c)
        if crossed_river(side, r):
            add_mv(r, c - 1)
            add_mv(r, c + 1)
    # ── 扩展第一批：普通 6 ──
    elif t in ("zu", "min", "pai", "huo", "teng", "wei", "xu", "can"):
        # 卒 / 民 / 牌 / 火 / 藤 / 卫 / 蓄 / 残：走法「直线 1」、攻击「相邻」。
        # ⚠️ 走法要判占位 —— 不判的话被占的格子会被前端高亮成「可走」，
        # 点下去才报「走不过去」。攻击范围照样加（那是打人，不是走人）。
        for dr, dc in DIR4:
            a, b = r + dr, c + dc
            if in_board(a, b) and not state["board"][a][b]:
                add_mv(a, b)
            add_rng(a, b)
    elif t == "shi":
        # 矢：走法「直线 1」/ 攻击「直线 2 格」
        for dr, dc in DIR4:
            add_mv(r + dr, c + dc)
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if state["board"][a][b]:
                    add_rng(a, b)
                    break
    elif t == "tan":
        # 探：走法「直线 1~2」/ 攻击「相邻」
        for dr, dc in DIR4:
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
            add_rng(r + dr, c + dc)
    # ── 扩展第一批：卓越 13 ──
    elif t in ("dun", "fu", "chui"):
        # 盾 / 斧 / 锤：走法「直线 1」/ 攻击「相邻」
        for dr, dc in DIR4:
            add_mv(r + dr, c + dc)
            add_rng(r + dr, c + dc)
    elif t == "gong":
        # 弓：走法「直线 1」/ 攻击「直线 3 格」，但打不到相邻的敌人
        for dr, dc in DIR4:
            add_mv(r + dr, c + dc)
            for k in (1, 2, 3):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if state["board"][a][b]:
                    if k >= 2:
                        add_rng(a, b)
                    break
    elif t == "nu":
        # 弩：走法「直线 1」/ 攻击「直线 2 格」（穿透在结算里追加第二个目标）
        for dr, dc in DIR4:
            add_mv(r + dr, c + dc)
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if state["board"][a][b]:
                    add_rng(a, b)
                    break
    elif t == "mao":
        # 矛：走法「直线 1~2」/ 攻击「直线 2 格」
        for dr, dc in DIR4:
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if state["board"][a][b]:
                    add_rng(a, b)
                    break
    elif t in ("qi", "gu", "zhen", "yao"):
        # 旗 / 鼓 / 阵 / 药：走法「直线 1」，不可攻击
        for dr, dc in DIR4:
            add_mv(r + dr, c + dc)
    elif t == "chi":
        # 斥：走法「直线 1~3」/ 攻击「相邻」
        for dr, dc in DIR4:
            for k in (1, 2, 3):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
            add_rng(r + dr, c + dc)
    elif t == "ci":
        # 刺：走法「直线 1，可斜走 1」/ 攻击「相邻」（斜向只走不打）
        for dr, dc in DIR4:
            add_mv(r + dr, c + dc)
            add_rng(r + dr, c + dc)
        for dr, dc in ADV_MV:
            add_mv(r + dr, c + dc)
    elif t == "jing":
        # 镜：走法「直线 1」/ 攻击「相邻」
        for dr, dc in DIR4:
            add_mv(r + dr, c + dc)
            add_rng(r + dr, c + dc)
    # ── 扩展第二批：传说 6 ──
    elif t in ("hu", "lang", "bian", "duan"):
        # 虎 / 狼 / 鞭 / 断：走法「直线 1~2」/ 攻击「相邻」
        for dr, dc in DIR4:
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
            add_rng(r + dr, c + dc)
    # ── 扩展第三批：优良 8 ──
    elif t == "biao":
        # 标：走法「直线 1~2」/ 攻击「直线 2 格」（要够得着才谈得上标记）
        for dr, dc in DIR4:
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if state["board"][a][b]:
                    add_rng(a, b)
                    break
                    break
    elif t == "bao":
        # 豹：走法「直线 1~2，可斜走 1」/ 攻击「相邻」（斜向只走不打）
        for dr, dc in DIR4:
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
            add_rng(r + dr, c + dc)
        for dr, dc in ADV_MV:
            a, b = r + dr, c + dc
            if in_board(a, b) and not state["board"][a][b]:
                add_mv(a, b)
    elif t == "ying":
        # 鹰·翱翔：走法「直线 1~3」无视阻挡（但不能落在有子的格上）/ 攻击「相邻」
        for dr, dc in DIR4:
            for k in (1, 2, 3):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if not state["board"][a][b]:
                    add_mv(a, b)
            add_rng(r + dr, c + dc)
    elif t == "lang":
        # 狼：走法「直线 1~2」/ 攻击「相邻」
        for dr, dc in DIR4:
            for k in (1, 2):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b) or state["board"][a][b]:
                    break
                add_mv(a, b)
            add_rng(r + dr, c + dc)
    elif t in ("yi", "jiang"):
        # 医 / 匠：走法「直线 1」，不可攻击
        for dr, dc in DIR4:
            a, b = r + dr, c + dc
            if in_board(a, b) and not state["board"][a][b]:
                add_mv(a, b)
    elif t == "yi2":
        # 移：走法「直线 1」，不可攻击；第三个集合装「可以换位的友军」。
        # 移形只能换相邻的，挪移（20 级）放宽到 2 格。
        for dr, dc in DIR4:
            a, b = r + dr, c + dc
            if in_board(a, b) and not state["board"][a][b]:
                add_mv(a, b)
        _sw = YI_SWAP_G2 if p.get("g2") else YI_SWAP
        for _dr in range(-_sw, _sw + 1):
            for _dc in range(-_sw, _sw + 1):
                if (_dr == 0 and _dc == 0) or abs(_dr) + abs(_dc) > _sw:
                    continue
                _q = piece_at(state, r + _dr, c + _dc)
                if _q and _q["s"] == side and _q["hp"] > 0:
                    add_heal(r + _dr, c + _dc)
    # ── 扩展第二批：神话 2 ──
    elif t == "di":
        # 帝：同帅 —— 九宫内横竖斜 1 格，走与打是同一批格
        for dr, dc in RED_KING_MV:
            if in_palace(side, r + dr, c + dc):
                add_mv(r + dr, c + dc)
                add_rng(r + dr, c + dc)
    elif t == "long":
        # 龙：走法「直线 1~3」（腾云时无视阻挡）/ 攻击「直线 3 格内」的第一个棋子
        fly = bool(p.get("cl"))
        for dr, dc in DIR4:
            for k in (1, 2, 3):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if state["board"][a][b]:
                    if not fly:
                        break
                    continue
                add_mv(a, b)
            for k in (1, 2, 3):
                a, b = r + dr * k, c + dc * k
                if not in_board(a, b):
                    break
                if state["board"][a][b]:
                    add_rng(a, b)
                    break

    # 医·回春：可以主动治疗八方向相邻的己方棋子
    if t == "yi":
        # 回春：只有「受伤且没被断粮」的相邻友军才是合法目标。
        # ⚠️ 不在这里筛的话，legal_targets 说能治、apply_action 又拒绝，
        # 前端会高亮一个点下去报错的格子（贪心 AI 模拟就是这么发现的）。
        for dr, dc in AROUND8:
            q = piece_at(state, r + dr, c + dc)
            if q and q["s"] == side and q["hp"] > 0 and q["hp"] < q["mh"] \
               and _can_heal(state, q):
                add_heal(r + dr, c + dc)

    if t not in SPLIT_MV_RNG:
        rng |= mv
    if t in NO_ATTACK:
        rng = set()          # 粮 / 旗 / 鼓 / 阵 / 药 / 医 / 匠 不能攻击

    # 自己人占住的格不能走、也不能打
    for s in (mv, rng):
        for (a, b) in list(s):
            q = state["board"][a][b]
            if q and q["s"] == side:
                s.discard((a, b))
    # 治疗目标反过来：只保留己方棋子占住的格
    for (a, b) in list(heal):
        q = state["board"][a][b]
        if not q or q["s"] != side:
            heal.discard((a, b))
    # 震击：被锁住的棋子不能移动，但仍然可以攻击、可以治疗
    if p.get("frz") is not None and state.get("ply", 0) <= p["frz"]:
        mv = set()
    # 缠斗（鞭）：下回合移动距离 -1。
    # 直线走法按步数扣（下限 1 格）；车 / 炮 这类不限格数的压到 3 格；
    # 日字 / 田字这种跳跃走法没有「步数」，不受影响。
    if p.get("sl") is not None and state.get("ply", 0) <= p["sl"] and t not in JUMP_MOVE:
        if t in ("chariot", "cannon"):
            lim = BIAN_SLOW_CAP
        else:
            lim = max(1, MOVE_STEPS.get(t, 1) - BIAN_SLOW)
        mv = set(x for x in mv if abs(x[0] - r) + abs(x[1] - c) <= lim)
    # 走法：被棋子占住的格子一律不能落子（攻击范围不受影响）。
    # 各分支自己判占位容易漏（兵 / 医 / 移 都漏过），统一在这里兜一道。
    mv = set(x for x in mv if not state["board"][x[0]][x[1]])
    return mv, rng, heal


def _find_king(state, side):
    for r in range(ROWS):
        for c in range(COLS):
            p = state["board"][r][c]
            if p and p["s"] == side and p["t"] in BOSS_TYPES:
                return p, r, c
    return None, -1, -1


def _open_line(state, r1, c1, r2, c2):
    """两点同直线且中间无棋子。"""
    if r1 == r2:
        lo, hi = min(c1, c2), max(c1, c2)
        return all(state["board"][r1][x] is None for x in range(lo + 1, hi))
    if c1 == c2:
        lo, hi = min(r1, r2), max(r1, r2)
        return all(state["board"][y][c1] is None for y in range(lo + 1, hi))
    return False


def calc_damage(state, atk, atk_r, atk_c, dfn, dfn_r, dfn_c):
    """返回 (最终伤害, 溅射基准伤害, 说明列表)。
    初始特性 + 养成增强（tr）+ 20 级第二特性（g2）都在这里结算。
    结算顺序固定：减伤 → 增伤 → 无视减伤 → 真实伤害；反伤在 apply_action 里单独算。"""
    notes = []
    base = float(atk["atk"])
    ab = atk.get("tr", 0.0)
    db = dfn.get("tr", 0.0)

    # 降攻（仕·死卫 / 戟·钩拒）：dbf 存的是「生效到哪个 ply」
    if atk.get("dbf") is not None and state.get("ply", 0) <= atk["dbf"]:
        _dbfv = atk.get("dbfv") or ADVISOR_VENGEANCE
        _dbfn = atk.get("dbfn") or "死卫"
        base *= 1 - _dbfv
        notes.append("%s -%d%%" % (_dbfn, round(_dbfv * 100)))

    # 断援（断 20 级）：被断的棋子下回合拿不到任何来自友军光环的增益
    # （自己的特性如踏营/八面威不受影响 —— 那些是它自己的能力，不是「获得」的增益）
    _nb_atk = atk.get("nb") is not None and state.get("ply", 0) <= atk["nb"]

    dkq, dkr, dkc = _find_king(state, dfn["s"])
    if dkq and dkq["t"] == "king" and dkq.get("g2"):
        aq, ar_, ac_ = _find_king(state, atk["s"])
        if aq and _open_line(state, dkr, dkc, ar_, ac_):
            base *= 1 - KING_MENACE
            notes.append("御敌 -20%")

    # 振饱（粮 20 级追加）：防守方两格范围内有粮 → 减伤 20%
    for _dr in (-2, -1, 0, 1, 2):
        for _dc in (-2, -1, 0, 1, 2):
            if not _dr and not _dc:
                continue
            _g = piece_at(state, dfn_r + _dr, dfn_c + _dc)
            if _g and _g["s"] == dfn["s"] and _g["t"] == "grain" and _g.get("g2") and _g["hp"] > 0:
                base *= 1 - GRAIN_WARD
                notes.append("振饱 -%d%%" % round(GRAIN_WARD * 100))
                break
        else:
            continue
        break

    # 守玺：防守方阵中有君，全军受到的伤害降低 10%
    if dkq and dkq["t"] == "lord":
        base *= 1 - LORD_WARD
        notes.append("守玺 -10%")

    # 天威（君 20 级追加）：每阵亡一名己方棋子，全军攻击 +8%，上限 40%
    akq, _akr, _akc = _find_king(state, atk["s"])
    if akq and akq["t"] == "lord" and akq.get("g2"):
        lost = max(0, LINEUP_TOTAL - alive_count(state, atk["s"]))
        if lost:
            boost = min(LORD_MIGHT_CAP, lost * LORD_MIGHT)
            base *= 1 + boost
            notes.append("天威 +%d%%" % round(boost * 100))

    if atk["t"] == "pawn" and crossed_river(atk["s"], atk_r):
        base *= 1 + PAWN_RUSH + ab
        notes.append("死战 +%d%%" % round((PAWN_RUSH + ab) * 100))
    if atk["t"] == "horse" and atk.get("mv"):
        base *= 1 + HORSE_CHARGE + ab
        notes.append("踏营 +%d%%" % round((HORSE_CHARGE + ab) * 100))
    if atk["t"] == "horse" and atk.get("g2"):
        n = 0
        for dr in (-2, -1, 0, 1, 2):
            for dc in (-2, -1, 0, 1, 2):
                if not dr and not dc:
                    continue
                q = piece_at(state, atk_r + dr, atk_c + dc)
                if q and q["s"] != atk["s"]:
                    n += 1
        if n:
            base *= 1 + HORSE_AURA * n
            notes.append("八面威 +%d%%" % round(HORSE_AURA * n * 100))
    if atk["t"] == "cannon" and atk.get("g2"):
        dr = (dfn_r > atk_r) - (dfn_r < atk_r)
        dc = (dfn_c > atk_c) - (dfn_c < atk_c)
        r, c = atk_r + dr, atk_c + dc
        while (r, c) != (dfn_r, dfn_c) and in_board(r, c):
            q = state["board"][r][c]
            if q:
                if q["s"] == atk["s"]:
                    base *= 1 + CANNON_SCREEN
                    notes.append("重炮 +30%")
                break
            r += dr
            c += dc
    # ── 扩展第一批的进攻方修正 ──
    # 老卒：卒过河后攻击翻倍；锐卒（20 级）再过河额外 +30%
    if atk["t"] == "zu" and crossed_river(atk["s"], atk_r):
        mult = 1 + ZU_RIVER
        if atk.get("g2"):
            mult *= 1 + ZU_RUI
        base *= mult
        notes.append("老卒 ×%.1f" % mult)
    # 齐射：与另一个矢相邻时攻击 +30%
    if atk["t"] == "shi" and _own_count_near(state, atk_r, atk_c, atk["s"], "shi", atk["id"]):
        base *= 1 + SHI_VOLLEY
        notes.append("齐射 +30%")
    # 暗杀：对生命低于 40% 的目标伤害翻倍
    if atk["t"] == "ci" and dfn["mh"] > 0 and dfn["hp"] < dfn["mh"] * CI_THRESHOLD:
        base *= CI_MULT
        notes.append("暗杀 ×2")
    # 号令：相邻有己方旗 → 攻击 +10%
    if not _nb_atk and _own_near(state, atk_r, atk_c, atk["s"], "qi"):
        base *= 1 + QI_ORDER
        notes.append("号令 +10%")
    # 大纛（旗 20 级）：己方场上有旗 → 全军攻击 +5%
    if not _nb_atk and _has_own(state, atk["s"], "qi", g2=True):
        base *= 1 + QI_BANNER
        notes.append("大纛 +5%")

    # ── 扩展第二批的进攻方修正 ──
    # 亲征（帝 20 级）：帝在场 → 己方全军攻击 +15%
    if not _nb_atk and _has_own(state, atk["s"], "di", g2=True):
        base *= 1 + DI_MIGHT
        notes.append("亲征 +15%")
    # 群猎（狼）：每个相邻友军 +8%
    if atk["t"] == "lang":
        _n = 0
        for dr, dc in AROUND8:
            q = piece_at(state, atk_r + dr, atk_c + dc)
            if q and q["s"] == atk["s"] and q["hp"] > 0:
                _n += 1
        if _n:
            base *= 1 + LANG_PACK * _n
            notes.append("群猎 +%d%%" % round(LANG_PACK * _n * 100))
    # 头狼（狼 20 级）：相邻友军攻击 +12%
    if not _nb_atk and _own_near(state, atk_r, atk_c, atk["s"], "lang", g2=True):
        base *= 1 + LANG_LEAD
        notes.append("头狼 +12%")
    # 铸兵（匠）：相邻友军攻击 +15%
    if not _nb_atk and _own_near(state, atk_r, atk_c, atk["s"], "jiang"):
        base *= 1 + JIANG_FORGE
        notes.append("铸兵 +15%")
    # 神工（匠 20 级）：己方场上有匠 → 全军攻击 +8%
    if not _nb_atk and _has_own(state, atk["s"], "jiang", g2=True):
        base *= 1 + JIANG_MASTER
        notes.append("神工 +8%")
    # 百兽（虎 20 级）：本回合每连续击杀一次，攻击 +20%（不封顶）
    if atk["t"] == "hu" and atk.get("g2") and atk.get("kb"):
        base *= 1 + HU_MIGHT * atk["kb"]
        notes.append("百兽 +%d%%" % round(HU_MIGHT * atk["kb"] * 100))

    # ── 扩展第三批的进攻方修正 ──
    # 蓄势（蓄）：本回合不攻击，则下回合攻击翻倍
    if atk["t"] == "xu" and atk.get("ch"):
        base *= XU_MULT
        notes.append("蓄势 ×%g" % XU_MULT)
    # 标记（标）：被标过的敌人，标记方的所有人打它都更疼
    if dfn.get("mk") is not None and state.get("ply", 0) <= dfn["mk"] \
       and atk["s"] == dfn.get("mks"):
        base *= 1 + BIAO_MARK
        notes.append("标记 +%d%%" % round(BIAO_MARK * 100))

    eq = None
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if not dr and not dc:
                continue
            q = piece_at(state, atk_r + dr, atk_c + dc)
            if q and q["s"] == dfn["s"] and q["t"] == "elephant" and q.get("g2") \
               and crossed_river(dfn["s"], atk_r + dr):
                eq = q
                break
        if eq:
            break
    if eq:
        base *= 1 - ELEPHANT_FIELD
        notes.append("掠阵 -20%")

    red = 0.0
    if dkq and dkq["t"] in ("king", "lord") and in_palace(dfn["s"], dfn_r, dfn_c):
        aura = KING_AURA + dkq.get("tr", 0.0)
        red = 1 - (1 - red) * (1 - aura)
        notes.append("坐镇 -%d%%" % round(aura * 100))
    if dfn["t"] == "elephant" and atk["t"] in ("chariot", "cannon"):
        armor = ELEPHANT_ARMOR + db
        red = 1 - (1 - red) * (1 - armor)
        notes.append("守垒 -%d%%" % round(armor * 100))
    # 结阵：相邻有己方阵 → 减伤 12%；方圆（阵 20 级且阵与阵相邻）翻倍
    _zh = _own_near(state, dfn_r, dfn_c, dfn["s"], "zhen")
    if _zh:
        form = ZHEN_FORM
        if _zh.get("g2"):
            _zp, _zr, _zc = find_piece(state, _zh["id"])
            if _zr >= 0 and _own_count_near(state, _zr, _zc, dfn["s"], "zhen", _zh["id"]):
                form *= ZHEN_DOUBLE
        red = 1 - (1 - red) * (1 - form)
        notes.append("结阵 -%d%%" % round(form * 100))
    # 磐石（盾 20 级）：相邻有己方盾 → 减伤 10%
    if _own_near(state, dfn_r, dfn_c, dfn["s"], "dun", g2=True):
        red = 1 - (1 - red) * (1 - DUN_ROCK)
        notes.append("磐石 -%d%%" % round(DUN_ROCK * 100))
    # 重牌（牌 20 级）：相邻有己方牌 → 减伤 8%
    if _own_near(state, dfn_r, dfn_c, dfn["s"], "pai", g2=True):
        red = 1 - (1 - red) * (1 - PAI_WARD)
        notes.append("重牌 -%d%%" % round(PAI_WARD * 100))
    # ── 扩展第三批的防守方修正 ──
    # 断援（断 20 级）：被断的棋子下回合拿不到任何减伤光环
    if dfn.get("nb") is not None and state.get("ply", 0) <= dfn["nb"]:
        if red > 0:
            notes.append("断援 减伤失效")
        red = 0.0
    red = min(red, REDUCTION_CAP)

    if atk["t"] == "chariot" and red > 0:
        pierce = CHARIOT_PIERCE + ab
        red *= 1 - pierce
        notes.append("破甲 无视%d%%" % round(pierce * 100))
    # 龙威：龙无视目标 30% 的减伤
    if atk["t"] == "long" and red > 0:
        red *= 1 - LONG_WARD
        notes.append("龙威 无视%d%%" % round(LONG_WARD * 100))
    # 鹰眼（鹰 20 级）：无视目标 20% 的减伤
    if atk["t"] == "ying" and atk.get("g2") and red > 0:
        red *= 1 - YING_PIERCE
        notes.append("鹰眼 无视%d%%" % round(YING_PIERCE * 100))
    # 劈山：斧无视目标的一切减伤（连铁壁一起穿）
    if atk["t"] == "fu":
        red = 0.0
        notes.append("劈山 无视减伤")

    dmg = max(1, int(round(base * (1 - red))))
    # 铁壁：盾每回合受到的第一次伤害减半
    if dfn["t"] == "dun" and atk["t"] != "fu" \
       and dfn.get("irn") != state.get("ply", 0) and dmg > 1:
        dmg = max(1, int(round(dmg * (1 - DUN_IRON))))
        notes.append("铁壁 -50%")
    # 开山（斧 20 级）：附带目标当前生命 15% 的真实伤害
    if atk["t"] == "fu" and atk.get("g2"):
        extra = max(1, int(round(dfn["hp"] * FU_TRUE)))
        dmg += extra
        notes.append("开山 真实 +%d" % extra)

    return dmg, int(round(base)), notes


def total_hp(state, side):
    hp = mx = 0
    for r in range(ROWS):
        for c in range(COLS):
            p = state["board"][r][c]
            if p and p["s"] == side:
                hp += p["hp"]
                mx += p["mh"]
    return hp, mx


def alive_count(state, side):
    n = 0
    for r in range(ROWS):
        for c in range(COLS):
            p = state["board"][r][c]
            if p and p["s"] == side:
                n += 1
    return n


def _finish(state, winner, reason):
    state["over"] = True
    state["winner"] = winner
    state["reason"] = reason


def _adjacent_advisors(state, r, c, side):
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if not dr and not dc:
                continue
            q = piece_at(state, r + dr, c + dc)
            if q and q["s"] == side and q["t"] == "advisor":
                out.append(q)
    return out


def _on_min_death(state, target, r, c, events):
    """征召 / 乡勇：民阵亡时给相邻友军（20 级时改为全军）回血。"""
    for dr, dc in AROUND8:
        o = piece_at(state, r + dr, c + dc)
        if o and o["s"] == target["s"] and o["hp"] > 0 and o["hp"] < o["mh"] and _can_heal(state, o):
            heal = min(MIN_NEAR_HEAL, o["mh"] - o["hp"])
            if heal > 0:
                o["hp"] += heal
                events.append({"k": "heal", "r": r + dr, "c": c + dc, "v": heal, "id": o.get("id")})
    if not target.get("g2"):
        return
    for rr in range(ROWS):
        for cc in range(COLS):
            o = state["board"][rr][cc]
            if o and o["s"] == target["s"] and o["hp"] > 0 and o["hp"] < o["mh"] and _can_heal(state, o):
                heal = min(MIN_ALL_HEAL, o["mh"] - o["hp"])
                if heal > 0:
                    o["hp"] += heal
                    events.append({"k": "heal", "r": rr, "c": cc, "v": heal, "id": o.get("id")})


def _apply_damage(state, target, amount, events, r, c, tag="", killer=None):
    """扣血并处理阵亡。返回是否致死。浴血：过河的兵单次伤害封顶 25% 生命上限。"""
    if not target or amount <= 0:
        return False
    if target.get("g2") and target["t"] == "pawn" and crossed_river(target["s"], r):
        cap = max(1, int(round(target["mh"] * PAWN_GRIT)))
        if amount > cap:
            amount = cap
    # 妙手（医 20 级）留下的护盾先挡一道；全挡住了就不算受伤
    if target.get("sh"):
        absorb = min(target["sh"], amount)
        target["sh"] -= absorb
        amount -= absorb
        events.append({"k": "shield", "r": r, "c": c, "v": absorb, "id": target["id"]})
        if amount <= 0:
            return False
    # 替死（卫）：这一下会打死人，旁边还站着没用过替死的卫 —— 由它来挨。
    # 卫自己挨打时不会替自己死（target 是卫时跳过）。
    if amount >= target["hp"] and target["t"] != "wei":
        _guard = None
        for dr, dc in AROUND8:
            q = piece_at(state, r + dr, c + dc)
            if q and q["t"] == "wei" and q["s"] == target["s"] and q["hp"] > 0 \
               and not q.get("sd"):
                _guard = q
                break
        if _guard is not None:
            gr, gc = find_piece(state, _guard["id"])[1:]
            if gr >= 0:
                _guard["sd"] = 1
                events.append({"k": "guard", "fr": r, "fc": c, "tr": gr, "tc": gc,
                               "id": _guard["id"], "save": target["id"]})
                _dead = _apply_damage(state, _guard, amount, events, gr, gc, tag=tag)
                # 死士（20 级）：代受之后自己回复 30% 生命。
                # ⚠️ 必须等伤害结算完再回 —— 先回再挨打的话，回的那点血当场又被扣掉，
                # 等于没回（卫该死还是死）。
                if not _dead and _guard.get("g2") and _can_buff(state, _guard):
                    _h = max(1, int(round(_guard["mh"] * WEI_SAVE_HEAL)))
                    _real = min(_h, max(0, _guard["mh"] - _guard["hp"]))
                    if _real > 0:
                        _guard["hp"] += _real
                        events.append({"k": "heal", "r": gr, "c": gc, "v": _real,
                                       "id": _guard["id"], "note": "死士"})
                return _dead
    # 铁壁：记下这一 ply 已经挨过一次直击（减半在 calc_damage 里算，溅射不吃铁壁）
    if target["t"] == "dun" and not tag:
        target["irn"] = state.get("ply", 0)
    target["hp"] = max(0, target["hp"] - amount)
    events.append({"k": "dmg", "r": r, "c": c, "v": amount, "id": target["id"], "tag": tag})
    if target["hp"] <= 0:
        # 残兵（残）：一场一次 —— 倒下时以 30%（不屈 50%）生命站起来，不算阵亡
        if target["t"] == "can" and not target.get("rev"):
            target["rev"] = 1
            _rate = CAN_REVIVE_G2 if target.get("g2") else CAN_REVIVE
            target["hp"] = max(1, int(round(target["mh"] * _rate)))
            target["sh"] = 0
            events.append({"k": "revive", "r": r, "c": c, "id": target["id"],
                           "v": target["hp"]})
            return False
        state["board"][r][c] = None
        events.append({"k": "kill", "r": r, "c": c, "id": target["id"], "t": target["t"], "s": target["s"]})
        if killer and target.get("g2") and target["t"] == "advisor":
            killer["dbf"] = state.get("ply", 0) + 2
            killer["dbfn"] = "死卫"
            killer["dbfv"] = ADVISOR_VENGEANCE
        if target["t"] == "min":
            _on_min_death(state, target, r, c, events)
        if target["t"] in BOSS_TYPES:
            who = ("红方" if target["s"] == "red" else "黑方") + PIECES[target["t"]]["zh"][0]
            _finish(state, "black" if target["s"] == "red" else "red",
                    "斩首成功 —— " + who + "血量归零")
        return True
    return False


def apply_action(state, side, fr, fc, tr, tc):
    """执行一次「移动」或「攻击」。

    返回 (ok, msg, events)。ok=False 时 state 不被修改。
    服务端唯一入口 —— 客户端传什么都不信任，一律在这里重新校验。
    """
    if state.get("over"):
        return False, "对局已结束", []
    if state["turn"] != side:
        return False, "还没轮到你", []
    if state["ap"] <= 0:
        return False, "本回合行动点已用完", []

    p = piece_at(state, fr, fc)
    if not p:
        return False, "起点没有棋子", []
    if p["s"] != side:
        return False, "这不是你的棋子", []
    if p.get("acts", 1) <= 0:
        return False, "这个棋子本回合已经行动过了", []

    mv, rng, heal = legal_targets(state, fr, fc)
    target = piece_at(state, tr, tc)
    events = []

    if target and target["s"] != side and (tr, tc) in rng:
        # ── 攻击 ──
        dmg, base, notes = calc_damage(state, p, fr, fc, target, tr, tc)
        events.append({"k": "attack", "fr": fr, "fc": fc, "tr": tr, "tc": tc, "notes": notes})

        # 影袭（刺 20 级）：潜行状态下免疫一次攻击
        immune = target.get("gh") == state.get("ply", 0) and target.get("ghn", 0) > 0
        imm_name = "残影" if target["t"] == "bao" else "影袭"

        # 反伤先算好 —— 防守方即使这一击被打死，也照样把伤害弹回去
        rr, ref_ignore = _reflect_ratio(state, target, tr, tc)
        ref_dmg = max(1, int(round((base if ref_ignore else dmg) * rr))) if rr > 0 else 0

        killed = False
        advisor = None
        share = 0
        if immune:
            target["ghn"] -= 1
            notes.append(imm_name + " 免疫")
            events.append({"k": "miss", "r": tr, "c": tc, "id": target["id"], "why": imm_name})
        else:
            # 护主：九宫内友方棋子由相邻的士分担 20% 伤害
            if in_palace(target["s"], tr, tc):
                advs = _adjacent_advisors(state, tr, tc, target["s"])
                if advs:
                    advisor = advs[0]
                    share = max(1, int(round(dmg * (ADVISOR_GUARD + advisor.get("tr", 0.0)))))
                    dmg -= share

            killed = _apply_damage(state, target, dmg, events, tr, tc, killer=p)
            if not state.get("over"):
                state["nodmg"] = 0
                state["dmg_round"] = True

            # 标记（标）：给目标打上标记，标记方的所有人打它都更疼
            if p["t"] == "biao" and not state.get("over") and target["hp"] > 0:
                _turns = BIAO_TURNS_G2 if p.get("g2") else BIAO_TURNS
                target["mk"] = state.get("ply", 0) + _turns * 2
                target["mks"] = side
                events.append({"k": "mark", "r": tr, "c": tc, "id": target["id"],
                               "turns": _turns})

            # 断粮（断）：目标下回合不能回血；断援（20 级）连增益一起断
            if p["t"] == "duan" and not state.get("over") and target["hp"] > 0:
                _until = state.get("ply", 0) + DUAN_TURNS * 2
                target["nh"] = _until
                if p.get("g2"):
                    target["nb"] = _until
                events.append({"k": "cut", "r": tr, "c": tc, "id": target["id"],
                               "buff": bool(p.get("g2"))})

            # 缠斗（鞭）：命中后目标下回合移动距离 -1；铁鞭（20 级）再降攻 10%
            if p["t"] == "bian" and not state.get("over") and target["hp"] > 0:
                target["sl"] = state.get("ply", 0) + 2
                if p.get("g2"):
                    target["dbf"] = state.get("ply", 0) + 2
                    target["dbfn"] = "铁鞭"
                    target["dbfv"] = BIAN_DOWN

            # 连珠（弓 20 级）：攻击后若未击杀，追加一次半伤
            if p["t"] == "gong" and p.get("g2") and not killed and not state.get("over"):
                _apply_damage(state, target, max(1, int(round(dmg * GONG_CHAIN))),
                              events, tr, tc, tag="chain", killer=p)

            # 穿透（弩）：继续沿直线命中第二个目标；重弩不衰减
            if p["t"] == "nu" and not state.get("over"):
                dr = 0 if tr == fr else (1 if tr > fr else -1)
                dc = 0 if tc == fc else (1 if tc > fc else -1)
                r2, c2 = tr + dr, tc + dc
                while in_board(r2, c2):
                    q = state["board"][r2][c2]
                    if q:
                        if q["s"] != side:
                            pd = dmg if p.get("g2") else max(1, int(round(dmg * NU_PIERCE)))
                            _apply_damage(state, q, pd, events, r2, c2, tag="pierce", killer=p)
                        break
                    r2 += dr
                    c2 += dc

            # 龙·穿透：攻击同时伤害路径上的第二个目标，且不衰减
            if p["t"] == "long" and not state.get("over"):
                dr = 0 if tr == fr else (1 if tr > fr else -1)
                dc = 0 if tc == fc else (1 if tc > fc else -1)
                r2, c2 = tr + dr, tc + dc
                while in_board(r2, c2):
                    q = state["board"][r2][c2]
                    if q:
                        if q["s"] != side:
                            _apply_damage(state, q, dmg, events, r2, c2, tag="pierce", killer=p)
                        break
                    r2 += dr
                    c2 += dc

            # 箭雨（矢 20 级）：对目标相邻的敌人造成半伤
            if p["t"] == "shi" and p.get("g2") and not state.get("over"):
                sd = max(1, int(round(dmg * SHI_RAIN)))
                for dr, dc in AROUND8:
                    q = piece_at(state, tr + dr, tc + dc)
                    if q and q["s"] != side:
                        _apply_damage(state, q, _splash_adjust(state, q, tr + dr, tc + dc, sd),
                                      events, tr + dr, tc + dc, tag="splash", killer=p)

            # 纵火（火）：目标相邻的敌人各受固定溅射，自身也受伤
            if p["t"] == "huo" and not state.get("over"):
                hd = HUO_SPLASH + (HUO_LIAO if p.get("g2") else 0)
                for dr, dc in AROUND8:
                    q = piece_at(state, tr + dr, tc + dc)
                    if q and q["s"] != side:
                        _apply_damage(state, q, _splash_adjust(state, q, tr + dr, tc + dc, hd),
                                      events, tr + dr, tc + dc, tag="splash", killer=p)
                if not state.get("over"):
                    _apply_damage(state, p, HUO_SELF, events, fr, fc, tag="burn")
                    state["nodmg"] = 0
                    state["dmg_round"] = True

            # 震击（锤）：命中后目标下回合不能移动；碎地（20 级）连相邻敌人一起锁
            if p["t"] == "chui" and not state.get("over"):
                if target["hp"] > 0:
                    target["frz"] = state.get("ply", 0) + 1
                if p.get("g2"):
                    for dr, dc in AROUND8:
                        q = piece_at(state, tr + dr, tc + dc)
                        if q and q["s"] != side:
                            q["frz"] = state.get("ply", 0) + 1

            # 影袭（刺 20 级）：击杀后潜行，下回合免疫一次攻击
            if p["t"] == "ci" and p.get("g2") and killed and not state.get("over"):
                p["gh"] = state.get("ply", 0) + 1
                p["ghn"] = 1

            # 百兽（虎 20 级）：击杀累积连续数，没打死就清零
            if p["t"] == "hu" and p.get("g2"):
                if killed:
                    p["kb"] = p.get("kb", 0) + 1
                else:
                    p.pop("kb", None)

            # 猛扑（虎）：击杀后立即再行动一次 —— 这里补回一次预算，
            # 下面统一扣 1 次，净效果就是这一击不消耗行动次数
            if p["t"] == "hu" and killed and not state.get("over"):
                p["acts"] = p.get("acts", 0) + 1
                events.append({"k": "extra", "r": fr, "c": fc, "id": p["id"], "why": "猛扑"})

            # 轰击：炮对目标身后 1 格溅射
            if p["t"] == "cannon" and not state.get("over"):
                dr = 0 if tr == fr else (1 if tr > fr else -1)
                dc = 0 if tc == fc else (1 if tc > fc else -1)
                br, bc = tr + dr, tc + dc
                behind = piece_at(state, br, bc)
                if behind and behind["s"] != side:
                    sd = max(1, int(round(base * (CANNON_SPLASH + p.get("tr", 0.0)))))
                    _apply_damage(state, behind, _splash_adjust(state, behind, br, bc, sd),
                                  events, br, bc, tag="splash", killer=p)

            # 横扫：车对目标同直线背后一格的敌方单位造成 30% 伤害
            if p["t"] == "chariot" and p.get("g2") and not state.get("over"):
                sr, sc = tr + (1 if tr > fr else -1 if tr < fr else 0), \
                         tc + (1 if tc > fc else -1 if tc < fc else 0)
                sweep_t = piece_at(state, sr, sc)
                if sweep_t and sweep_t["s"] != side:
                    sd = max(1, int(round(base * CHARIOT_SWEEP)))
                    _apply_damage(state, sweep_t, sd, events, sr, sc, tag="sweep", killer=p)

            # 舍身结算
            if advisor and share > 0 and not state.get("over"):
                ar, ac = -1, -1
                for rr in range(ROWS):
                    for cc in range(COLS):
                        q = state["board"][rr][cc]
                        if q and q["id"] == advisor["id"]:
                            ar, ac = rr, cc
                            break
                    if ar >= 0:
                        break
                if ar >= 0:
                    _apply_damage(state, advisor, share, events, ar, ac, tag="guard", killer=p)

        # 反伤：算伤害、能击杀，但走独立的 reflect 事件 ——
        # 不触发虎的再行动 / 刺的潜行这类「击杀后」特性。
        # 被影袭免疫掉的那一击不算打中，所以也不反弹。
        if ref_dmg > 0 and not immune and not state.get("over"):
            events.append({"k": "reflect", "fr": tr, "fc": tc, "tr": fr, "tc": fc,
                           "v": ref_dmg, "src": target["t"], "id": p["id"]})
            _apply_damage(state, p, ref_dmg, events, fr, fc, tag="reflect")
            state["nodmg"] = 0
            state["dmg_round"] = True

        p["mv"] = False   # 攻击后本回合的移动加成消耗掉
        p["ad"] = True    # 蓄·蓄势用：这一回合出手了，下回合不蓄力

    elif p["t"] == "yi2" and target and target["s"] == side and (tr, tc) in heal:
        # ── 移形：和友军换位（第三种动作，既不移动也不攻击）──
        # 注意要排在回春前面：移的目标也是「己方 + 在 heal 集合里」，顺序反了就变成治疗了。
        state["board"][fr][fc], state["board"][tr][tc] = target, p
        events.append({"k": "swap", "fr": fr, "fc": fc, "tr": tr, "tc": tc,
                       "a": p["id"], "b": target["id"]})

    elif target and target["s"] == side and (tr, tc) in heal:
        # ── 回春：花一次行动治疗相邻友军 ──
        if not _can_heal(state, target):
            return False, "目标被断粮，治疗无效", []
        amt = max(1, int(round(target["mh"] * YI_HEAL)))
        need = max(0, target["mh"] - target["hp"])
        real = min(amt, need)
        target["hp"] += real
        over = amt - real
        events.append({"k": "heal", "r": tr, "c": tc, "v": real, "id": target["id"],
                       "by": p["id"], "note": "回春"})
        # 妙手（医 20 级）：治不进去的那部分变成护盾，上限不超过生命上限
        if over > 0 and p.get("g2") and _can_buff(state, target):
            cur = target.get("sh", 0)
            add = min(over, max(0, target["mh"] - cur))
            if add > 0:
                target["sh"] = cur + add
                events.append({"k": "shieldup", "r": tr, "c": tc, "v": add,
                               "id": target["id"], "notes": ["妙手 护盾 +%d" % add]})

    elif not target and (tr, tc) in mv:
        # ── 移动 ──
        state["board"][fr][fc] = None
        state["board"][tr][tc] = p
        p["mv"] = True
        # 轻骑（探 20 级）：移动后获得一次免费攻击，每回合只发一次
        if p["t"] == "tan" and p.get("g2") and not p.get("fatd"):
            p["fatk"] = TAN_FREE_ATK
            p["fatd"] = 1
        # 腾云（龙 20 级）：这次移动把无视阻挡的机会用掉了
        if p.get("cl"):
            p.pop("cl", None)
        # 残影（豹 20 级）：移动之后，对手这一轮第一次打它无效
        if p["t"] == "bao" and p.get("g2"):
            p["gh"] = state.get("ply", 0) + 1
            p["ghn"] = BAO_SHADOW
        events.append({"k": "move", "fr": fr, "fc": fc, "tr": tr, "tc": tc, "id": p["id"]})

    else:
        return False, "这个位置走不过去", []

    state["ap"] -= 1
    # 轻骑的免费攻击不消耗行动次数
    if target and p.get("fatk"):
        p["fatk"] = 0
    else:
        p["acts"] = max(0, p.get("acts", 1) - 1)
    if state["ap"] <= 0:
        _end_turn(state, events)
    _check_draw(state, events)
    return True, "", events


def _end_turn(state, events):
    # 缠绕（藤）：己方回合结束时，相邻敌人掉血。
    # 走 _dot_damage —— 纯环境伤害，不触发反伤 / 铁壁 / 猛扑，也不计入判和。
    for r in range(ROWS):
        for c in range(COLS):
            g = state["board"][r][c]
            if not (g and g["s"] == state["turn"] and g["t"] == "teng" and g["hp"] > 0):
                continue
            dot = TENG_DOT_G2 if g.get("g2") else TENG_DOT
            for dr, dc in AROUND8:
                o = piece_at(state, r + dr, c + dc)
                if o and o["s"] != g["s"] and o["hp"] > 0:
                    _dot_damage(state, o, dot, events, r + dr, c + dc)
    state["turn"] = "black" if state["turn"] == "red" else "red"
    state["ply"] = state.get("ply", 0) + 1
    if state["turn"] == "red":
        state["round"] += 1
        if state["dmg_round"]:
            state["nodmg"] = 0
            state["dmg_round"] = False
        else:
            state["nodmg"] += 1
    for r in range(ROWS):
        for c in range(COLS):
            q = state["board"][r][c]
            if q and q["s"] == state["turn"]:
                q["mv"] = False
            if q and q.get("dbf") is not None and state.get("ply", 0) > q["dbf"]:
                for _k in ("dbf", "dbfn", "dbfv"):
                    q.pop(_k, None)
            # 标记（标）/ 断粮 / 断援 到期的清理
            if q:
                for _k in ("mk", "nh", "nb"):
                    if q.get(_k) is not None and state.get("ply", 0) > q[_k]:
                        q.pop(_k, None)
                        if _k == "mk":
                            q.pop("mks", None)
    _recompute_acts(state, state["turn"])
    state["ap"] = _ap_max(state, state["turn"])
    # 补济：该方回合开始时，粮的相邻友方回血
    for r in range(ROWS):
        for c in range(COLS):
            g = state["board"][r][c]
            if not (g and g["s"] == state["turn"] and g["t"] == "grain" and g["hp"] > 0):
                continue
            for dr, dc in AROUND8:
                o = piece_at(state, r + dr, c + dc)
                if o and o["s"] == g["s"] and o["hp"] > 0 and o["hp"] < o["mh"] and _can_heal(state, o):
                    heal = min(GRAIN_HEAL, o["mh"] - o["hp"])
                    if heal > 0:
                        o["hp"] += heal
                        events.append({"k": "heal", "r": r + dr, "c": c + dc,
                                       "v": heal, "id": o.get("id")})
    # 施药：该方回合开始时，药的相邻友军回血（药的 20 级改成全军回血，统一在下面结算）
    for r in range(ROWS):
        for c in range(COLS):
            y = state["board"][r][c]
            if not (y and y["s"] == state["turn"] and y["t"] == "yao" and y["hp"] > 0):
                continue
            if y.get("g2"):
                continue
            for dr, dc in AROUND8:
                o = piece_at(state, r + dr, c + dc)
                if o and o["s"] == y["s"] and o["hp"] > 0 and o["hp"] < o["mh"] and _can_heal(state, o):
                    heal = min(YAO_NEAR, o["mh"] - o["hp"])
                    if heal > 0:
                        o["hp"] += heal
                        events.append({"k": "heal", "r": r + dr, "c": c + dc,
                                       "v": heal, "id": o.get("id")})
    # 良方（药 20 级）：只要该方场上还有 20 级的药，全军每回合回血（不叠加）
    if _has_own(state, state["turn"], "yao", g2=True):
        for r in range(ROWS):
            for c in range(COLS):
                o = state["board"][r][c]
                if o and o["s"] == state["turn"] and o["hp"] > 0 and o["hp"] < o["mh"] and _can_heal(state, o):
                    heal = min(YAO_ALL, o["mh"] - o["hp"])
                    if heal > 0:
                        o["hp"] += heal
                        events.append({"k": "heal", "r": r, "c": c, "v": heal, "id": o.get("id")})
    events.append({"k": "turn", "turn": state["turn"], "round": state["round"]})


def _check_draw(state, events):
    if state.get("over"):
        return
    if state["nodmg"] >= NODMG_DRAW:
        _finish(state, "draw", "连续 %d 回合双方都没有造成伤害，判和" % NODMG_DRAW)
        events.append({"k": "over", "winner": "draw"})


def agree_draw(state, reason="双方同意和棋"):
    """接受求和，判和结束。"""
    if state.get("over"):
        return False, "对局已结束", []
    _finish(state, "draw", reason)
    return True, "", []


def end_turn(state, side):
    """主动结束当前回合，剩余行动点作废。"""
    if state.get("over"):
        return False, "对局已结束"
    if state["turn"] != side:
        return False, "还没轮到你"
    events = []
    _end_turn(state, events)
    _check_draw(state, events)
    return True, "", events


def resign(state, side):
    """认输。"""
    if state.get("over"):
        return False, "对局已结束"
    _finish(state, "black" if side == "red" else "red", "对方认输")
    return True, ""


def elo_delta(my_rating, opp_rating, score):
    """score: 1 胜 / 0.5 和 / 0 负。K=32。"""
    exp = 1.0 / (1.0 + 10 ** ((opp_rating - my_rating) / 400.0))
    return int(round(32 * (score - exp)))
