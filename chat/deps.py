# -*- coding: utf-8 -*-
"""chat/deps.py — 051 依赖注入容器。

app.py 启动时把项目 helper 注入 D；chat 包内一律 `from .deps import D` 后
`D['get_db']()` 取用。这样 chat 包不 import app.py，彻底避免循环 import。
"""
D = {}
