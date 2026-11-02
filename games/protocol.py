# -*- coding: utf-8 -*-
"""games/protocol.py — 游戏中心统一契约（规划文档 075 项 · 第一步）

为什么要这个
------------
现状（2026-11-01 盘点）：
- 11 个 `submit_xxx_score()` 接口，其中 6 个是纯薄壳（一行转发），
  5 个有各自的额外逻辑（2048 记最大方块 / 汉诺塔按阶数 / 三个时间类）。
- 22 个成绩字段散落在 `_GAME_SCORE_FIELDS`、`_GAME_SCORE_ROWS`、`_GAME_TIME_ROWS`
  三张表里，加一款游戏要同时改三处，漏一处就是排行榜空白。
- 服务端复算在 `games_verify/`，覆盖 9 款；2048 / tetris 没有复算（fail-open）。

本文件做的是：**把「一款游戏是什么」收敛成一张注册表**。
之后新增游戏只加一条 `GameSpec`，不再改三张字段表。

契约内容（本文件只有数据，不碰 DB、不 import app）
--------------------------------------------------
- `GameSpec`   —— 一款游戏的元数据
- `GAMES`      —— 注册表（key 与 `games_verify` 的 game 参数一致）
- `get()` / `by_field()` / `verified_keys()`

后续（075 的其余部分，尚未实现）
--------------------------------
- 统一存档槽位与云同步
- 前端从 11 个旧接口迁移到 `/api/game/submit/<game>`
- 统一的「初始化 / 输入处理 / 步进」前端契约
"""


class GameSpec:
    """一款游戏的元数据。

    字段说明
    --------
    key         游戏标识，与 games_verify 的 game 参数一致（'24' / 'hanoi' / ...）
    name        中文名，用于排行榜和后台展示
    score_field 分数落库字段（users 表列名），None 表示这款游戏只记时间
    time_fields 时间/步数落库字段元组，可能多个（华容道 4/5/6、扫雷三难度）
    unit        time_fields 的存储单位：'ms' 毫秒 / 's' 秒
    verified    是否在 games_verify/ 有服务端复算（False = fail-open 放行）
    handler     自定义提交逻辑的函数名（在 games.mini 里）；None = 纯转发
    legacy      旧提交接口路径（兼容期保留，前端迁移完可删）
    """

    __slots__ = ("key", "name", "score_field", "time_fields", "unit",
                 "verified", "handler", "legacy")

    def __init__(self, key, name, score_field=None, time_fields=(), unit="ms",
                 verified=False, handler=None, legacy=None):
        self.key = key
        self.name = name
        self.score_field = score_field
        self.time_fields = tuple(time_fields)
        self.unit = unit
        self.verified = verified
        # handler：自定义提交逻辑的函数名（在 games.mini 里），None 表示纯转发
        self.handler = handler
        self.legacy = legacy

    @property
    def fields(self):
        """这款游戏涉及的全部落库字段。"""
        out = []
        if self.score_field:
            out.append(self.score_field)
        out.extend(self.time_fields)
        return out

    def __repr__(self):
        return "<GameSpec %s %s>" % (self.key, self.name)


# ── 注册表 ──
# 顺序即后台展示顺序。key 必须与 games_verify.VERIFIERS 的键一致。
GAMES = {
    # ── 纯分数、有复算、无额外逻辑（统一入口可直接转发）──
    "24":         GameSpec("24", "24 点", score_field="high_score_24",
                           verified=True, legacy="/api/submit_24_score"),
    "sudoku":     GameSpec("sudoku", "数独", score_field="high_score_sudoku",
                           verified=True, legacy="/api/submit_sudoku_score"),
    "jump":       GameSpec("jump", "跳一跳", score_field="high_score_jump",
                           verified=True, legacy="/api/submit_jump_score"),
    "xianyi":     GameSpec("xianyi", "仙弈破阵", score_field="high_score_xianyi",
                           verified=True, legacy="/api/submit_xianyi_score"),
    "colorblind": GameSpec("colorblind", "色盲派对", score_field="high_score_color_blind",
                           verified=True, legacy="/api/submit_color_blind_score"),

    # ── 纯分数、无复算 ──
    "tetris":     GameSpec("tetris", "俄罗斯方块", score_field="high_score_tetris",
                           verified=True, legacy="/api/submit_tetris_score"),

    # ── 有额外逻辑（handler 指向 games.mini 里的旧函数，统一入口按名分派）──
    "2048":       GameSpec("2048", "2048", score_field="high_score_2048",
                           verified=True, handler="submit_2048_score",
                           legacy="/api/submit_2048_score"),
    "hanoi":      GameSpec("hanoi", "汉诺塔", score_field="high_score_hanoi",
                           time_fields=("hanoi_best_moves", "hanoi_best_ms"),
                           unit="ms", verified=True, handler="submit_hanoi_score",
                           legacy="/api/submit_hanoi_score"),
    "klotski":    GameSpec("klotski", "华容道", score_field="high_score_klotski",
                           time_fields=("best_time_klotski_4", "best_time_klotski_5",
                                        "best_time_klotski_6"),
                           unit="s", verified=True, handler="submit_klotski_time",
                           legacy="/api/submit_klotski_time"),
    "klotski_cn": GameSpec("klotski_cn", "三国华容道", score_field="high_score_klotski",
                           time_fields=("best_time_klotski_cn_1", "best_time_klotski_cn_2",
                                        "best_time_klotski_cn_3", "best_time_klotski_cn_4"),
                           unit="s", verified=True, handler="submit_klotski_cn_time",
                           legacy="/api/submit_klotski_cn_time"),
    "mine":       GameSpec("mine", "扫雷", score_field=None,
                           time_fields=("mine_best_ms_beginner", "mine_best_ms_intermediate",
                                        "mine_best_ms_expert"),
                           unit="ms", verified=True, handler="submit_mine_time",
                           legacy="/api/submit_mine_time"),
}


def get(key):
    """按游戏 key 取规格；未知返回 None。"""
    return GAMES.get(key)


def by_field(field):
    """按落库字段反查游戏规格（后台排行榜、成就扫描用）。"""
    for spec in GAMES.values():
        if field in spec.fields:
            return spec
    return None


def all_keys():
    return tuple(GAMES.keys())


def verified_keys():
    """有服务端复算的游戏。"""
    return tuple(k for k, s in GAMES.items() if s.verified)


def unverified_keys():
    """没有复算、fail-open 放行的游戏 —— 这些最容易刷榜，优先补。"""
    return tuple(k for k, s in GAMES.items() if not s.verified)


def field_map():
    """{落库字段: 中文名}，给后台和排行榜格式化用。

    替代原来 `_GAME_SCORE_FIELDS` 里手写的那张表。
    """
    out = {}
    for spec in GAMES.values():
        if spec.score_field:
            out[spec.score_field] = spec.name
        for f in spec.time_fields:
            out[f] = "%s · %s" % (spec.name, f.rsplit("_", 1)[-1])
    return out
