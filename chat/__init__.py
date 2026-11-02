# -*- coding: utf-8 -*-
"""chat/ — 站点功能规划第 051 项 · 独立 IM 模块。

从 app.py 抽出的聊天/私信业务包：
  private.py   私聊线（页面/发送/会话列表）
  room.py      聊天室线（页面/发言/撤回/编辑/上传）
  socket.py    SocketIO 事件（私信推送/正在输入/撤回广播）
  state.py     051 消息可靠性：幂等键 / 送达回执 / 离线补推 / 状态机

设计取舍（为什么不直接用 Flask Blueprint）：
  Blueprint 会把 endpoint 改成 `private.chat`，全站几百处 url_for('private_chat')
  和模板/JS 引用的路由名全部要跟着改 —— 收益为零、风险极大。
  所以用 app.add_url_rule(**显式命名 endpoint**)，路由名与搬迁前完全一致，
  对调用方零感知。表结构仍与社区内容同库（messages），为后续分库预留。

依赖注入：app.py 启动时调 chat.init(app, socketio, deps={...})，
业务代码里一律从 .deps 的 D 取 helper，不 import app（避免循环依赖）。
"""
from . import deps as _deps

D = _deps.D
IM_STATE = None    # chat.state.State 实例（init 时建）。
# ⚠️ 不能叫 state：会和子模块 state.py 撞名，
# `from . import state` 会拿到这个 None 而不是模块（实测踩坑）。


def init(app, sio, deps=None):
    """由 app.py 在启动尾部调用一次。deps 是 helper 字典。"""
    if deps:
        D.update(deps)
    from . import state as _state_mod
    global IM_STATE
    IM_STATE = _state_mod.State(sio)
    from . import private, room, socket as _socket
    private.register(app, D)
    room.register(app, D)
    _socket.register(sio, D, IM_STATE)
