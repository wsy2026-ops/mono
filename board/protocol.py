# -*- coding: utf-8 -*-
"""board/protocol.py — 棋类对局统一层（规划文档 058 项 · 第一步：统一路由注册）

为什么要这个
------------
`board/__init__.py` 里原来有六个 `_register_xxx()`，每个里面抄一张
`(rule, endpoint, methods, fn)` 列表。六张列表里 **7 条路由是同一个模式**：

    /game/<key>                     <ep>_list          GET
    /api/<key>/<rid>/join           <ep>_join          POST
    /api/<key>/random-join          <ep>_random_join   POST
    /game/<key>/<rid>               <ep>_room          GET
    /api/<key>/<rid>/move           <ep>_move          POST
    /api/<key>/<rid>/state          <ep>_state         GET
    /api/<key>/<rid>/restart        <ep>_restart       POST

新增一款棋就要再抄一遍，抄错一个 endpoint 名就是 `url_for` 全站 500。
这里把「标准七条」生成化，各棋只写自己**多出来的**路由。

零行为变更
----------
rule / endpoint / methods 全部照抄原列表。改完必须验证
`app.url_map` 与改前逐条一致（基线见 /tmp/urlmap_baseline.json）。

后续（058 的其余部分，尚未实现）
--------------------------------
- 标准对局协议：落子 / 悔棋 / 认输 / 超时（依赖 075 的游戏接口契约）
- 统一棋盘渲染组件（模板层）
- 统一观战席
- 统一复盘存取与导出
- 引擎适配器（KataGo / Stockfish / Pikafish / GNU Go / egaroucid / RAPFI）
  子进程必须走 app._proc_stop 回收；失败黑名单 dict + 180s 冷却
"""

# 标准七条路由的「动作名」。函数名约定为 <endpoint前缀>_<动作>。
CORE_ACTIONS = ("list", "join", "random_join", "room", "move", "state", "restart")


def core_rules(key, ep_prefix=None):
    """生成标准七条路由的 (rule, endpoint, methods, action)。

    key       —— URL 里的棋种段，如 'gomoku' / 'go' / 'xq'
    ep_prefix —— endpoint 前缀，默认同 key（chess 的 legal 之类才需要单独处理）
    """
    p = ep_prefix or key
    return [
        ("/game/%s" % key,                    "%s_list" % p,        None,     "list"),
        ("/api/%s/<int:rid>/join" % key,      "%s_join" % p,        ["POST"], "join"),
        ("/api/%s/random-join" % key,         "%s_random_join" % p, ["POST"], "random_join"),
        ("/game/%s/<int:rid>" % key,          "%s_room" % p,        None,     "room"),
        ("/api/%s/<int:rid>/move" % key,      "%s_move" % p,        ["POST"], "move"),
        ("/api/%s/<int:rid>/state" % key,     "%s_state" % p,       None,     "state"),
        ("/api/%s/<int:rid>/restart" % key,   "%s_restart" % p,     ["POST"], "restart"),
    ]


def core_handlers(module, ep_prefix):
    """从模块里按约定取标准七条的处理函数。

    缺哪个就报哪个 —— 比在 add_url_rule 时抛 'NoneType is not callable' 好查。
    """
    out = {}
    for action in CORE_ACTIONS:
        name = "%s_%s" % (ep_prefix, action)
        fn = getattr(module, name, None)
        if fn is None:
            raise AttributeError(
                "board.protocol: 模块 %s 缺少标准处理函数 %s（棋种 %s）"
                % (getattr(module, "__name__", module), name, ep_prefix))
        out[action] = fn
    return out


def register(app, D, key, module, ep_prefix=None, extras=None, socket_events=None):
    """按标准七条 + extras 注册一款棋。

    参数
    ----
    app           Flask 实例
    D             依赖字典（需要 login_required / socketio）
    key           URL 棋种段
    module        该棋种的模块对象
    ep_prefix     endpoint 前缀，默认同 key
    extras        额外路由 [(rule, endpoint, methods, fn), ...]
    socket_events 额外 SocketIO 事件 {event_name: fn}
    """
    LR = D["login_required"]
    p = ep_prefix or key
    handlers = core_handlers(module, p)

    rules = [(rule, ep, methods, handlers[action])
             for rule, ep, methods, action in core_rules(key, p)]
    rules.extend(extras or [])

    for rule, ep, methods, fn in rules:
        app.add_url_rule(rule, ep, LR(fn), methods=methods)

    for ev, fn in (socket_events or {}).items():
        D["socketio"].on_event(ev, fn)

    return len(rules)
