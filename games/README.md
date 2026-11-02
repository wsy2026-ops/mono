# games/ — 游戏中心

> 规划文档 075「游戏中心统一协议层」的落地说明。
> 最后更新：2026-11-01

## 075 统一了什么

| 层 | 统一前 | 统一后 |
|---|---|---|
| 成绩字段 | 3 张手写表（`_GAME_SCORE_FIELDS` / `_GAME_SCORE_ROWS` / `_GAME_TIME_ROWS`），加游戏要改三处 | `games/protocol.py` 的 `GAMES` 注册表 |
| 提交入口 | 11 个 `/api/submit_xxx_score` | 1 个 `/api/game/submit/<game>` |
| 存档 | evolve 和 kubition 各一套完全不同的实现 | `/api/game/save/<game>` 门面（底层各用各的表） |
| 服务端复算 | `games_verify/` 9 款 | 不变（026 已完成，10-20） |

**没统一、也不打算统一的**：前端游戏逻辑（初始化 / 输入处理 / 步进）。
11 款游戏规则本质不同（2048 是合并、扫雷是布雷、华容道是滑块），
硬抽一个"引擎"只会得到一堆 `if game == ...`。文档那句话的真实诉求是
**"新增游戏不用重复写存档和结算"**——这两块已经达成。

## 新增一款游戏要改哪里

### 后端（必做 2 步）

**第 1 步：加一条 `GameSpec`** —— `games/protocol.py`

```python
GAMES = {
    # ...
    "snake": GameSpec("snake", "贪吃蛇", score_field="high_score_snake",
                      verified=False, legacy="/api/submit_snake_score"),
}
```

| 字段 | 说明 |
|---|---|
| `key` | 游戏标识，也是 URL 里的 `<game>`，同时是 `games_verify` 的 game 参数 |
| `name` | 中文名，排行榜和后台展示用 |
| `score_field` | 落库字段（users 表列名）。只记时间的游戏填 `None` |
| `time_fields` | 时间/步数字段元组，可能多个（华容道 4/5/6、扫雷三难度） |
| `unit` | `time_fields` 的单位：`'ms'` 或 `'s'` |
| `verified` | 是否有服务端复算 |
| `handler` | 自定义提交函数的**名字**（在 `games.mini` 里）。`None` = 纯转发 |
| `legacy` | 旧接口路径，兼容期保留 |

**第 2 步：写提交函数** —— `games/mini.py`

纯分数游戏**不用写**，统一入口会自动转发 `_submit_score_common`。

有额外逻辑的（比如要先记个"最大方块"），写一个函数，然后把名字填进 `handler`：

```python
def submit_snake_score():
    """贪吃蛇：先把本局最长长度记进 users.max_len_snake，再走通用流程。"""
    db = D['get_db']()
    me = D['current_user']()
    if me:
        try:
            mx = int((request.get_json() or {}).get('max_len') or 0)
        except (TypeError, ValueError):
            mx = 0
        if mx > 0:
            db.execute("UPDATE users SET max_len_snake=? WHERE id=? AND IFNULL(max_len_snake,0)<?",
                       (mx, me['id'], mx))
            db.commit()
    return D['_submit_score_common']('high_score_snake', game='snake')
```

### 后端（建议做）：写服务端复算器

`games_verify/` 里加一个 `verify_snake(payload, claimed)`，然后注册进 `VERIFIERS`：

```python
# games_verify/__init__.py
VERIFIERS = {
    # ...
    "snake": verify_snake,
}
```

**不写会怎样**：`verify_game_result` 对未注册的游戏 **fail-open 放行**，
也就是改前端 JS 就能刷榜。新游戏上线前建议补上。

### 前端（2 步）

**提交成绩**：

```js
fetch('/api/game/submit/snake', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
        score: 1234,
        seq: { /* 输入序列，服务端复算用；没有就留空 */ }
    })
}).then(r => r.json()).then(d => {
    if (!d.ok) console.warn(d.msg);
    else console.log('新纪录:', d.new_best, '榜单:', d.rank_list);
});
```

**存档（如果需要）**：

```js
// 存
fetch('/api/game/save/snake', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({data: JSON.stringify(state), meta: {day: 3}, slot: 1})
});

// 读
fetch('/api/game/save/snake?slot=1').then(r => r.json()).then(d => {
    if (d.ok) state = JSON.parse(d.data);
});
```

存档接口（4 个）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/game/save/<game>?slot=1` | 取回 |
| POST | `/api/game/save/<game>` | 保存（`data` 必填，`meta`/`slot` 可选） |
| GET | `/api/game/save/<game>/history` | 历史版本（需后端支持 history） |
| POST | `/api/game/save/<game>/restore` | 恢复某个历史版本，body `{"id": 12}` |

**不写后端会怎样**：`get_backend(game)` 找不到专门后端时，
自动给你一个 `GenericBackend`，读写统一的 `game_saves` / `game_saves_history` 两张表（懒建）。
也就是说**存档功能开箱即用**，不用建表。

## 存档后端现状

| 游戏 | 后端 | 表 | 历史 | 上限 |
|---|---|---|---|---|
| evolve | `EvolveBackend` | `evolve_saves` | 无 | 400 KB |
| kubition | `KubitionBackend` | `kubition_saves` + history | 20 份 | 2 MB |
| 其他（新游戏） | `GenericBackend` | `game_saves` + history | 20 份 | 2 MB |

**为什么不把旧表迁到统一表**：玩家存档丢了找不回来。
一次性迁移 + 切接口 = 拿真实数据做实验。门面模式下旧接口照常工作，
出问题最多是新入口不可用。

## 已知限制

1. **旧接口还在**（`/api/submit_xxx_score`）。前端 2026-11-01 已全部迁到统一入口，
   但旧接口保留兼容期，防止有外部脚本/缓存页面还在调。
2. **2048 / tetris 没有服务端复算**（`verified=False`）。要补的话
   2048 需要复算合并序列，tetris 需要复算方块下落——工作量不小。
3. **`game_saves` 是懒建表**，不在 `init_db()` 里（那个函数是死代码，接回启动流程会炸站）。
