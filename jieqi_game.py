"""
揭棋（象棋揭棋 / Jieqi）引擎 + 内置 AI + 中文着法记谱
======================================================
规则（与用户提供的标准完全一致）：
- 9×10 标准棋盘，楚河汉界保留。
- 帅 / 将固定摆在原始位置，永久明棋（可见）。
- 双方其余各 15 枚棋子打乱后背面朝上，放回原本对应的初始点位：
  位置 = 身份模板；棋子实体（真身）是随机打乱的。
- 每个回合只能移动一枚己方棋子，二选一：
  ① 移动暗子：按“该暗子所在位置对应的棋子”走法行动（原位模板走法）；
     完成移动（可吃子）后立刻翻面变为明子，永久按真身走棋，模板失效。
  ② 移动明子：按真身正统中国象棋走法行动。
- 揭棋特殊修订：明子的士、相（象）可以过河（士 1 斜步无宫限制；相 2 斜步无河限制，仍塞象眼）。
- 暗子只能走完一步后自动翻开，没有“单纯翻棋”操作；暗子可吃明子/暗子。
- 胜负：将死对方帅/将 胜；困毙（无合法着法且未被将军）判负；认输/超时判负。

棋盘编码：board_state = display + '|' + truth （各 90 字符）
  display：'.' 空；K/k 明将帅；H/h 暗子（H=红暗，h=黑暗）；A..R / a..r 已翻明子。
  truth ：每个位置真身字母（A..R / a..r），仅暗子位置有效，翻面后清为 '.'。
服务端仅将 display 下发给前端，暗子真身不下发，保证双方都看不见暗子身份。
"""

import random
import json
import time

W, H = 9, 10

XQ_INIT = (
    "rnbakabnr"
    "........."
    ".c.....c."
    "p.p.p.p.p"
    "........."
    "........."
    "P.P.P.P.P"
    ".C.....C."
    "........."
    "RNBAKABNR"
)

PIECE_CN_FULL = {
    'K': '帅', 'k': '将', 'A': '仕', 'a': '士', 'B': '相', 'b': '象',
    'N': '马', 'n': '马', 'R': '车', 'r': '车', 'C': '炮', 'c': '炮',
    'P': '兵', 'p': '卒',
}

JQ_VALUE = {
    'K': 10000, 'A': 220, 'B': 220, 'N': 450, 'R': 900, 'C': 450, 'P': 100,
    'k': 10000, 'a': 220, 'b': 220, 'n': 450, 'r': 900, 'c': 450, 'p': 100,
}

X_FILES = '一二三四五六七八九'
X_FILES_BLACK = '123456789'


def _jq_at(board, x, y):
    return board[y * W + x]


def _jq_set(board, x, y, v):
    arr = list(board)
    arr[y * W + x] = v
    return ''.join(arr)


def _jq_in_palace(x, y, red):
    if red:
        return 3 <= x <= 5 and 7 <= y <= 9
    return 3 <= x <= 5 and 0 <= y <= 2


def _jq_path_clear(board, x1, y1, x2, y2):
    """竖线 x1==x2，y1->y2 之间（不含端点）是否无子。"""
    if x1 != x2:
        return False
    step = 1 if y2 > y1 else -1
    yy = y1 + step
    while yy != y2:
        if _jq_at(board, x1, yy) != '.':
            return False
        yy += step
    return True


def _jq_template(x, y):
    """该位置对应的“原位模板”棋子（标准布局字母，含正确大小写）。"""
    return XQ_INIT[y * W + x]


def _jq_piece_moves(board, x, y):
    """该格棋子按揭棋规则的所有落点（不含自将军限制）。board 为 display 串。"""
    p = _jq_at(board, x, y)
    if p == '.':
        return []
    red = p.isupper()
    hidden = p in 'Hh'
    if hidden:
        tmpl = _jq_template(x, y)
        move_type = tmpl.upper()
    else:
        move_type = p.upper()
    moves = []

    def _add(tx, ty):
        if 0 <= tx < W and 0 <= ty < H:
            t = _jq_at(board, tx, ty)
            if t == '.' or t.isupper() != red:
                moves.append((tx, ty))

    if move_type == 'K':
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty = x + dx, y + dy
            if _jq_in_palace(tx, ty, red):
                _add(tx, ty)
    elif move_type == 'A':
        palace = hidden
        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            tx, ty = x + dx, y + dy
            if 0 <= tx < W and 0 <= ty < H and (not palace or _jq_in_palace(tx, ty, red)):
                _add(tx, ty)
    elif move_type == 'B':
        cross = (not hidden)
        for dx, dy in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
            tx, ty = x + dx, y + dy
            if 0 <= tx < W and 0 <= ty < H:
                if (not cross) and red and ty < 5:
                    continue
                if (not cross) and (not red) and ty > 4:
                    continue
                if _jq_at(board, x + dx // 2, y + dy // 2) != '.':
                    continue
                _add(tx, ty)
    elif move_type == 'N':
        for dx, dy, lx, ly in (
            (1, 2, 0, 1), (-1, 2, 0, 1), (1, -2, 0, -1), (-1, -2, 0, -1),
            (2, 1, 1, 0), (-2, 1, -1, 0), (2, -1, 1, 0), (-2, -1, -1, 0),
        ):
            tx, ty = x + dx, y + dy
            if 0 <= tx < W and 0 <= ty < H:
                if _jq_at(board, x + lx, y + ly) == '.':
                    _add(tx, ty)
    elif move_type == 'R':
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty = x + dx, y + dy
            while 0 <= tx < W and 0 <= ty < H:
                t = _jq_at(board, tx, ty)
                if t == '.':
                    _add(tx, ty)
                else:
                    if t.isupper() != red:
                        _add(tx, ty)
                    break
                tx += dx
                ty += dy
    elif move_type == 'C':
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty = x + dx, y + dy
            jumped = False
            while 0 <= tx < W and 0 <= ty < H:
                t = _jq_at(board, tx, ty)
                if not jumped:
                    if t == '.':
                        _add(tx, ty)
                    else:
                        jumped = True
                else:
                    if t != '.':
                        if t.isupper() != red:
                            _add(tx, ty)
                        break
                tx += dx
                ty += dy
    elif move_type == 'P':
        if red:
            _add(x, y - 1)
            if y < 5:
                _add(x - 1, y)
                _add(x + 1, y)
        else:
            _add(x, y + 1)
            if y > 4:
                _add(x - 1, y)
                _add(x + 1, y)
    return moves


def _jq_find_king(board, red):
    target = 'K' if red else 'k'
    for y in range(H):
        for x in range(W):
            if _jq_at(board, x, y) == target:
                return x, y
    return None


def _jq_in_check(board, red):
    """red 方是否被将军（含将帅照面）。board 为 display 串。"""
    k = _jq_find_king(board, red)
    if k is None:
        return True
    kx, ky = k
    for y in range(H):
        for x in range(W):
            p = _jq_at(board, x, y)
            if p == '.' or p.isupper() == red:
                continue
            if (kx, ky) in _jq_piece_moves(board, x, y):
                return True
            if p in 'Kk' and x == kx and _jq_path_clear(board, x, y, kx, ky):
                return True
    return False


def _jq_has_legal_move(display, truth, red):
    """red 方是否还有任意合法着法（不递归，仅供将死/困毙判定）。"""
    for y in range(H):
        for x in range(W):
            p = _jq_at(display, x, y)
            if p == '.' or p.isupper() != red:
                continue
            for tx, ty in _jq_piece_moves(display, x, y):
                if _jq_at(display, tx, ty) in 'Kk':
                    continue
                real = truth[y * W + x] if p in 'Hh' else p
                nd = _jq_set(_jq_set(display, x, y, '.'), tx, ty, real)
                if not _jq_in_check(nd, red):
                    return True
    return False


def _jq_make_move(display, truth, x1, y1, x2, y2):
    """执行一步（display/truth 为 90 字符串）。
    返回 (新display, 新truth, 被吃子或None, 是否将军对方, 对方是否无子可动) 或 None（非法）。"""
    p = _jq_at(display, x1, y1)
    if p == '.':
        return None
    red = p.isupper()
    if (x2, y2) not in _jq_piece_moves(display, x1, y1):
        return None
    if _jq_at(display, x2, y2) in 'Kk':
        return None
    captured = _jq_at(display, x2, y2)
    real = truth[y1 * W + x1] if p in 'Hh' else p
    nd = _jq_set(_jq_set(display, x1, y1, '.'), x2, y2, real)
    nt = _jq_set(_jq_set(truth, x1, y1, '.'), x2, y2, '.')
    if _jq_in_check(nd, red):
        return None
    check = _jq_in_check(nd, not red)
    end = not _jq_has_legal_move(nd, nt, not red)
    return nd, nt, (captured if captured != '.' else None), check, end


def _jq_legal_moves(display, truth, red):
    """red 方所有合法着法（走完不被将军）。返回 [(x1,y1,x2,y2), ...]。"""
    res = []
    for y in range(H):
        for x in range(W):
            p = _jq_at(display, x, y)
            if p == '.' or p.isupper() != red:
                continue
            for tx, ty in _jq_piece_moves(display, x, y):
                if _jq_at(display, tx, ty) in 'Kk':
                    continue
                real = truth[y * W + x] if p in 'Hh' else p
                nd = _jq_set(_jq_set(display, x, y, '.'), tx, ty, real)
                if not _jq_in_check(nd, red):
                    res.append((x, y, tx, ty))
    return res


def _jq_why_illegal(display, truth, x1, y1, x2, y2, red):
    """解释一步为何非法（供前端提示）。"""
    p = _jq_at(display, x1, y1)
    if p == '.' or p.isupper() != red:
        return '请选择己方棋子'
    if (x2, y2) not in _jq_piece_moves(display, x1, y1):
        return '不符合该棋子的走法规则'
    if _jq_at(display, x2, y2) in 'Kk':
        return '不能直接吃掉对方的将/帅'
    real = truth[y1 * W + x1] if p in 'Hh' else p
    nd = _jq_set(display, x1, y1, '.')
    nd = _jq_set(nd, x2, y2, real)
    kpos = _jq_find_king(nd, red)
    epos = _jq_find_king(nd, not red)
    if kpos and epos and kpos[0] == epos[0] and _jq_path_clear(nd, kpos[0], kpos[1], epos[0], epos[1]):
        return '将帅不能照面（飞将违规）'
    if _jq_in_check(nd, red):
        return '你的将/帅正被将军，必须应将（不能送将）'
    return '不符合走法规则'


def _jq_init():
    """生成一局新的揭棋初始局面（随机打乱 15 子）。返回 display|truth。"""
    red_pool = ['R', 'R', 'N', 'N', 'B', 'B', 'A', 'A', 'C', 'C', 'P', 'P', 'P', 'P', 'P']
    black_pool = ['r', 'r', 'n', 'n', 'b', 'b', 'a', 'a', 'c', 'c', 'p', 'p', 'p', 'p', 'p']
    random.shuffle(red_pool)
    random.shuffle(black_pool)
    display = ['.'] * 90
    truth = ['.'] * 90
    display[9 * W + 4] = 'K'
    display[0 * W + 4] = 'k'
    ridx = bidx = 0
    for y in range(H):
        for x in range(W):
            ch = XQ_INIT[y * W + x]
            if ch in 'Kk':
                continue
            if ch == '.':
                continue
            pos = y * W + x
            if ch.isupper():
                real = red_pool[ridx]
                ridx += 1
                display[pos] = 'H'
                truth[pos] = real
            else:
                real = black_pool[bidx]
                bidx += 1
                display[pos] = 'h'
                truth[pos] = real
    return ''.join(display) + '|' + ''.join(truth)


def _jq_xy_to_uci(x, y):
    return chr(97 + x) + str(9 - y)


def _jq_uci_to_xy(s):
    s = (s or '').strip().lower()
    if len(s) < 2:
        return None
    x = ord(s[0]) - 97
    try:
        y = 9 - int(s[1:])
    except ValueError:
        return None
    if not (0 <= x < W and 0 <= y < H):
        return None
    return x, y


def _jq_reveal_char(display, truth, x1, y1):
    p = _jq_at(display, x1, y1)
    return truth[y1 * W + x1] if p in 'Hh' else p


def _jq_move_text(display, truth, x1, y1, x2, y2, idx):
    """单步中文记谱（按翻面后的真身记录，符合揭棋习惯）。idx 从 0 起。"""
    red = (idx % 2 == 0)
    real = _jq_reveal_char(display, truth, x1, y1)
    name = PIECE_CN_FULL.get(real.upper(), '子')
    file = X_FILES[x1] if red else X_FILES_BLACK[x1]
    if y2 == y1:
        direction = '平'
        dist = X_FILES[x2] if red else X_FILES_BLACK[x2]
    else:
        forward = (red and y2 < y1) or ((not red) and y2 > y1)
        direction = '进' if forward else '退'
        kind = real.upper()
        if kind in 'RCP':
            dist = str(abs(y2 - y1))
        else:
            dist = X_FILES[x2] if red else X_FILES_BLACK[x2]
    return name + file + direction + dist


def _jq_eval(display, truth, red):
    score = 0.0
    for y in range(H):
        for x in range(W):
            p = _jq_at(display, x, y)
            if p == '.':
                continue
            if p in 'Hh':
                v = JQ_VALUE[_jq_template(x, y).upper()]
            else:
                v = JQ_VALUE.get(p.upper(), 0)
            score += v if (p.isupper() == red) else -v
    return score


def _jq_project_fen(display, truth, red):
    """把揭棋局面投影成 Pikafish 可用的标准中国象棋 FEN。

    规则：
    - 暗子(H/h) -> 该位置的“原位模板”标准字母（红大写/黑小写），即占位棋子；
    - 明子     -> 真身字母；
    - 明子的士/相若已过河（揭棋特有、标准象棋非法位置）-> 返回 None，
      表示 Pikafish 无法解析该局面，调用方应回退内置 AI。

    FEN 行顺序：y=0 为黑方顶行（对应 FEN 首行）；side：red->'w'，黑->'b'。
    返回 FEN 字符串；若局面对 Pikafish 非法则返回 None。
    """
    rows = []
    for y in range(H):
        row = ''
        empty = 0
        for x in range(W):
            ch = _jq_at(display, x, y)
            if ch == '.':
                empty += 1
                continue
            if empty:
                row += str(empty)
                empty = 0
            if ch in 'Hh':
                row += _jq_template(x, y)
            elif ch in 'Kk':
                row += ch
            else:
                is_red = ch.isupper()
                if ch in 'Aa' and not _jq_in_palace(x, y, is_red):
                    return None
                if ch in 'Bb':
                    crossed = (is_red and y < 5) or ((not is_red) and y > 4)
                    if crossed:
                        return None
                row += ch
        if empty:
            row += str(empty)
        rows.append(row)
    return '/'.join(rows) + (' w' if red else ' b') + ' - - 0 1'


def _jq_ai_move(display, truth, red, depth=2):
    """内置揭棋 AI：返回 (x1,y1,x2,y2) 或 None。"""
    t0 = time.time()
    _MAX_TIME = 1.6
    moves = _jq_legal_moves(display, truth, red)
    if not moves:
        return None

    def _move_score(m):
        x1, y1, x2, y2 = m
        cap = _jq_at(display, x2, y2)
        if cap == '.':
            return 0
        if cap in 'Hh':
            return JQ_VALUE[_jq_template(x2, y2).upper()]
        return JQ_VALUE.get(cap.upper(), 0)

    def _negamax(d, dsp, tr, side, alpha, beta):
        if time.time() - t0 > _MAX_TIME:
            return _jq_eval(dsp, tr, side)
        ms = _jq_legal_moves(dsp, tr, side)
        if not ms:
            return -1e9
        if d <= 0:
            return _jq_eval(dsp, tr, side)
        ms.sort(key=_move_score, reverse=True)
        best = -1e18
        for m in ms:
            r = _jq_make_move(dsp, tr, m[0], m[1], m[2], m[3])
            if r is None:
                continue
            nd, nt, _cap, _chk, _end = r
            s = -_negamax(d - 1, nd, nt, not side, -beta, -alpha)
            if s > best:
                best = s
            if s > alpha:
                alpha = s
            if alpha >= beta:
                break
        return best

    moves.sort(key=_move_score, reverse=True)
    best_m = None
    best_s = -1e18
    alpha = -1e18
    for m in moves:
        r = _jq_make_move(display, truth, m[0], m[1], m[2], m[3])
        if r is None:
            continue
        nd, nt, _cap, chk2, end2 = r
        if end2:
            return m
        s = -_negamax(depth - 1, nd, nt, not red, -1e18, -alpha)
        if s > best_s:
            best_s = s
            best_m = m
        if s > alpha:
            alpha = s
    return best_m or (moves[0] if moves else None)


def _jq_parse(combined):
    """拆分 combined 串为 (display, truth)。"""
    if '|' in combined:
        d, t = combined.split('|', 1)
    else:
        d, t = combined, '.' * 90
    return d, t


def _jq_combine(display, truth):
    return display + '|' + truth


def _jq_move_text_list(combined, uci_moves):
    """给定初始 combined 与 UCI 串，重放生成记谱列表（供调试/落库）。"""
    display, truth = _jq_parse(combined)
    out = []
    for i, uci in enumerate((uci_moves or '').split()):
        if len(uci) < 4:
            continue
        f = _jq_uci_to_xy(uci[:2])
        t = _jq_uci_to_xy(uci[2:4])
        if not f or not t:
            break
        out.append(_jq_move_text(display, truth, f[0], f[1], t[0], t[1], i))
        r = _jq_make_move(display, truth, f[0], f[1], t[0], t[1])
        if r is None:
            break
        display, truth = r[0], r[1]
    return out


def _self_test(steps=60, seed=None):
    if seed is not None:
        random.seed(seed)
    combined = _jq_init()
    display, truth = _jq_parse(combined)
    red = True
    for _ in range(steps):
        moves = _jq_legal_moves(display, truth, red)
        if not moves:
            break
        m = random.choice(moves)
        r = _jq_make_move(display, truth, *m)
        if r is None:
            return False, 'illegal move returned'
        display, truth = r[0], r[1]
        red = not red
    return True, 'ok'


if __name__ == '__main__':
    ok, msg = _self_test(120, seed=12345)
    print('self_test:', ok, msg)
