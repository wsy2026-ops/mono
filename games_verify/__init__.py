"""games_verify —— 游戏成绩服务端复算框架。

背景：九款游戏的排行榜成绩原先全部「前端算分直接上报」，改前端 JS 即可刷榜，
榜单没有公信力。本目录把每款游戏的核心逻辑以 Python 复刻一份，按前端提交的
输入序列（走法/落子/蓄力/点击）在服务端重放，重算出的分数与上报值比对，
不一致即拒绝入库。

协议：
    前端 POST 提交 {score: N, seq: {...}}  —— seq 的结构见各游戏 verifier 文档。
    老客户端（无 seq）放行但在日志打 legacy_no_seq 标记，灰度期可观测。

用法（app.py 侧）：
    from games_verify import verify_game_result
    ok, expected, reason = verify_game_result("hanoi", payload, claimed_score)
    if not ok:
        _log_mod.event("game_verify_fail", game="hanoi", reason=reason, ...)
        return jsonify({"ok": False, "msg": "成绩校验未通过"}), 400
"""
from .hanoi import verify_hanoi
from .klotski import verify_klotski
from .klotski_cn import verify_klotski_cn
from .mine import verify_mine
from .twenty4 import verify_24
from .sudoku import verify_sudoku
from .xianyi import verify_xianyi
from .jump import verify_jump
from .colorblind import verify_colorblind
from .g2048 import verify_2048
from .tetris import verify_tetris

# game key -> verifier(payload, claimed_score) -> (ok, expected, reason)
VERIFIERS = {
    "hanoi": verify_hanoi,
    "klotski": verify_klotski,
    "klotski_cn": verify_klotski_cn,
    "mine": verify_mine,
    "24": verify_24,
    "sudoku": verify_sudoku,
    "xianyi": verify_xianyi,
    "jump": verify_jump,
    "colorblind": verify_colorblind,
    "2048": verify_2048,
    "tetris": verify_tetris,
}


def verify_game_result(game, payload, claimed_score):
    """总入口。返回 (ok, expected_score, reason)。

    - ok=True 放行；expected 为重算分（用于日志核对，可为 None）
    - ok=False 拒绝入库，reason 供日志定位
    未注册的游戏一律放行（fail-open，新游戏接入前不影响现有链路）。
    """
    fn = VERIFIERS.get(game)
    if fn is None:
        return True, None, "no_verifier"
    try:
        return fn(payload, claimed_score)
    except Exception as e:  # 复算器自身 bug 不能变成 500 打脸用户
        return False, None, "verifier_error:%s" % type(e).__name__
