# -*- coding: utf-8 -*-
"""knowledge.py — 053 社区知识库引擎（文档化 + 按需查阅）。

架构（2026-10-25 从「SYSTEM 塞全文」升级）：
  第一级：目录索引（DOCS 的 keywords）—— 用户问题关键词命中哪些文档
  第二级：按需取全文 —— 命中的文档全文拼进 system，模型据此回答
好处：知识可无限扩容（不撑 prompt），回答时只带相关文档（省 token、更聚焦）。

新增文档：knowledge/ 下加 .md，然后在 DOCS 注册（文件 + 关键词）。
"""
import os
import threading

KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")

# 文档注册表：key → (文件名, [匹配关键词])
DOCS = {
    "account": ("10_account.md", ["注册", "登录", "密码", "邮箱", "验证码", "改名",
                                  "改密码", "头像", "github", "账号", "绑定"]),
    "chat": ("15_chat.md", ["聊天室", "私信", "私聊", "水群", "撤回", "屏蔽", "聊天",
                            "表情", "发消息", "正在输入"]),
    "admins": ("18_admins.md", ["管理员", "站长", "admin", "chendong", "bxl",
                                "wangyichen", "管理团队", "谁是管理", "管理员有谁",
                                "管理员是谁", "官方账号"]),
    "team": ("17_team.md", ["陆柒", "工作室", "成员", "社长", "站长", "wsy", "bxl",
                            "wyc", "陆柒公公", "招新", "加入", "团队", "谁开发的",
                            "洛谷"]),
    "points": ("20_points.md", ["积分", "金币", "coins", "签到", "商城", "兑换",
                                "抽奖", "流水", "积分上限", "花费"]),
    "sign": ("21_sign.md", ["签到", "补签", "连签", "断签", "每日签到", "日历"]),
    "mall": ("22_mall.md", ["商城", "买", "购买", "商品", "头衔", "改名卡", "经验卡",
                            "抽奖", "退"]),
    "music": ("25_music.md", ["音乐", "歌", "听歌", "上传歌", "播放列表", "背景音",
                              "music", "歌曲"]),
    "forum": ("30_forum.md", ["发帖", "帖子", "评论", "板块", "标签", "精华", "热榜",
                              "论坛", "订阅", "关注", "删帖", "编辑帖"]),
    "inbox": ("35_inbox.md", ["通知", "收件箱", "未读", "红点", "铃铛", "提到",
                              "inbox", "消息提醒"]),
    "oj": ("40_oj.md", ["oj", "判题", "提交", "比赛", "题库", "编译错误", "超时",
                        "答案错误", "题", "刷题"]),
    "oj_problems": ("41_oj_problems.md", ["题库", "题目", "难度", "题面", "筛选",
                                          "通过率", "数据范围"]),
    "oj_submit": ("42_oj_submit.md", ["提交", "状态码", "评测", "排队", "通过",
                                      "答案错", "超时", "编译错误", "运行错误", "提交记录"]),
    "oj_contest": ("43_oj_contest.md", ["比赛", "竞赛", "排名规则", "罚时", "封榜",
                                        "vp", "contest"]),
    "oj_debug": ("44_oj_debug.md", ["卡题", "排错", "不会", "本地能过", "对拍",
                                    "优化", "调试", "边界", "答案错", "超时"]),
    "chess": ("45_chess.md", ["中国象棋", "房间对战", "pikafish", "引擎", "残局",
                              "闯关", "象走田", "马走日", "炮", "白脸将"]),
    "xiangqi": ("50_xiangqi.md", ["象棋大师", "匹配", "赛季", "技能", "棋子", "段位",
                                  "天梯", "抽签", "魔改"]),
    "match": ("51_match.md", ["匹配不到", "掉线", "重连", "结算", "申诉", "对手不走",
                              "挂机", "思考很久", "思考中", "卡住"]),
    "season": ("52_season.md", ["赛季", "段位", "青铜", "白银", "黄金", "铂金",
                                "钻石", "软重置", "结算奖励"]),
    "games": ("60_games.md", ["游戏", "小游戏", "扫雷", "汉诺塔", "华容道", "24点",
                              "数独", "色盲", "跳一跳", "闲弈", "玩"]),
    "games_minesweeper": ("61_games_minesweeper.md", ["扫雷", "地雷", "插旗",
                                                      "雷数", "难度"]),
    "games_puzzle": ("62_games_puzzle.md", ["汉诺塔", "华容道", "数独", "滑块",
                                            "圆盘", "9×9", "步数"]),
    "games_casual": ("63_games_casual.md", ["24点", "色盲", "跳一跳", "闲弈",
                                            "算24", "蓄力", "连击"]),
    "level": ("71_level.md", ["等级", "经验", "升级", "lv", "每日上限"]),
    "badge": ("72_badge.md", ["勋章", "佩戴", "头衔", "装饰", "装扮", "限定"]),
    "achievement": ("73_achievement.md", ["成就", "解锁", "进度", "成就榜", "大奖"]),
    "ranking": ("74_ranking.md", ["排行榜", "榜单", "排名", "纪录", "上榜"]),
    "faq_account": ("91_faq_account.md", ["忘记密码", "收不到", "验证码", "头像不显示",
                                          "注销", "账号被盗", "改名多少钱"]),
    "faq_content": ("92_faq_content.md", ["敏感词", "被隐藏", "禁言", "误封", "没加分",
                                          "消息发不出", "审核"]),
    "faq_oj": ("93_faq_oj.md", ["排队", "本地能过", "判题机", "看别人代码", "卡题"]),
    "faq_game": ("94_faq_game.md", ["匹配不到人", "引擎慢", "引擎卡", "掉线", "区别",
                                    "纪录怎么算", "思考"]),
    "faq_access": ("95_faq_access.md", ["访问慢", "手机", "app", "pwa", "播放卡",
                                        "上传失败", "白屏", "报错", "缓存"]),
    "appeal": ("96_appeal.md", ["申诉", "反馈", "误杀", "误判", "建议", "bug",
                                "举报", "找管理员"]),
    "about": ("97_about.md", ["mono", "社区", "关于", "站点", "开发者", "自建",
                              "技术", "团队", "是谁"]),
    "changelog": ("98_changelog.md", ["更新", "版本", "新功能", "changelog", "日志"]),
    "rules": ("99_rules.md", ["规则", "违规", "封号", "禁言", "红线", "处罚",
                              "广告", "刷屏", "作弊"]),
}

_cache = {}
_lock = threading.Lock()


def available():
    return os.path.isdir(KB_DIR) and len(DOCS) > 0


def _load(fname):
    """读文档全文（带缓存）。"""
    with _lock:
        if fname in _cache:
            return _cache[fname]
    try:
        with open(os.path.join(KB_DIR, fname), encoding="utf-8") as f:
            text = f.read()
    except Exception:
        text = ""
    with _lock:
        _cache[fname] = text
    return text


def match(msg):
    """关键词命中文档，返回文档 key 列表（按命中数排序，最多 3 篇）。"""
    t = (msg or "").lower()
    if not t:
        return []
    hits = []
    for key, (_fname, kws) in DOCS.items():
        n = sum(1 for w in kws if w in t)
        if n:
            hits.append((n, key))
    hits.sort(reverse=True)
    return [k for _n, k in hits[:3]]


def lookup(keys):
    """取文档全文。keys 是 match() 返回的列表。返回拼接的文档文本。"""
    out = []
    for k in keys[:3]:
        entry = DOCS.get(k)
        if not entry:
            continue
        text = _load(entry[0])
        if text:
            out.append(text)
    return "\n\n".join(out)


def index_brief():
    """目录索引（给 SYSTEM 用的简短版：让模型知道有哪些资料可查）。"""
    lines = []
    for key, (fname, kws) in DOCS.items():
        title = fname.split("_", 1)[-1].replace(".md", "")
        lines.append("- %s（相关词：%s）" % (title, "、".join(kws[:6])))
    return "\n".join(lines)
