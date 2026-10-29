"""思忆集 MCP Server —— 数据边界（壳 ↔ 执行内核的标准协议接口）。

* ``protocol.py`` 最小 MCP stdio 服务端（纯标准库，零 SDK）
* ``store.py``    个人数据门面（只依赖 data/ + repositories/）
* ``tools.py``    siyi_* 工具注册（首批 10 个）
* ``server.py``   入口：``python -m mcp_server.server [--data-dir PATH]``

设计见 docs/kernel-layering-design-2026-08-15.md 第五节。
"""
