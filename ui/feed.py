"""把最近一帧数据补喂给刚建出来的页面。

页面是按需创建的，从建出来到下一个采样周期之间是空的，看着像「这页没数据」。
这件事原来在主窗口建页、主窗口切页、监控页建分段三处各写了一遍，每遍都要
判 getattr / hasattr，加一种数据源就得改三处。

`proc` 统一成 `(rows, total, leaks)` 三元组：主窗口把它拼好再传，监控页存的
本来就是三元组，两边都省一层判断。
"""

from __future__ import annotations


def feed(page, snap=None, proc=None) -> None:
    """给页面补一帧数据；页面没实现对应方法就跳过。"""
    if snap is not None:
        handler = getattr(page, "update_metrics", None)
        if handler is not None:
            handler(snap)
    if proc is not None:
        handler = getattr(page, "update_processes", None)
        if handler is not None:
            handler(*proc)
