"""窗口四角圆角。

两个系统两套做法，都不改窗口结构：

- **Win11（build ≥ 22000）**：DWM 有原生圆角，`DWMWA_WINDOW_CORNER_PREFERENCE`
  一条属性就够，带抗锯齿、保留阴影、拖动吸附全都正常。
- **Win10**：没有这条属性，退回 `SetWindowRgn` —— 给窗口设一个圆角矩形裁剪区。
  代价是边缘没有抗锯齿（高 DPI 下 1px 的锯齿，肉眼几乎看不出），
  好处是不用自己画边框、不用处理最大化吸附。

为什么不做无边框窗口 + 自绘阴影：那套东西要自己处理拖拽、边缘缩放、最大化、
贴边分屏、任务栏缩略图，出错面比收益大得多。

**回滚**：不调用 `install()` 就是原样，什么都不改。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject

from core import applog

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWCP_DONOTROUND = 1


def _win11() -> bool:
    try:
        return sys.getwindowsversion().build >= 22000
    except AttributeError:
        return False


def _try_dwm(hwnd: int, radius_round: bool = True) -> bool:
    """Win11 原生圆角。失败返回 False（比如被系统策略关掉了）。"""
    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        value = ctypes.c_int(DWMWCP_ROUND if radius_round else DWMWCP_DONOTROUND)
        code = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_int(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return code == 0
    except Exception:
        return False


class RoundedCorners(QObject):
    """给一个窗口装圆角，窗口大小变化时重设裁剪区。

    Win11 装一次就完事；Win10 每次尺寸变化都要重设（裁剪区是绝对像素）。
    """

    def __init__(self, window, radius: int = 12):
        super().__init__(window)
        self._window = window
        self._radius = radius
        self._native = False          # Win11 走 DWM，不碰裁剪区
        self._hwnd = 0

    def install(self) -> None:
        try:
            self._hwnd = int(self._window.winId())
        except Exception:
            return

        if _win11() and _try_dwm(self._hwnd):
            self._native = True
            return

        # Win10：裁剪区方案
        self._window.installEventFilter(self)
        self._apply_region()

    # -- Win10 ------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._window and event.type() in (
            QEvent.Resize,
            QEvent.Show,
            QEvent.WindowStateChange,
        ):
            self._apply_region()
        return False

    def _apply_region(self) -> None:
        try:
            hwnd = int(self._window.winId())
            gdi = ctypes.WinDLL("gdi32", use_last_error=True)
            user = ctypes.WinDLL("user32", use_last_error=True)

            if self._window.isMaximized() or self._window.isFullScreen():
                # 最大化要直角：Windows 最大化时本来就铺满工作区
                user.SetWindowRgn(wintypes.HWND(hwnd), wintypes.HRGN(0), True)
                return

            width = self._window.width()
            height = self._window.height()
            if width <= 0 or height <= 0:
                return

            r = self._radius * 2            # CreateRoundRectRgn 的参数是椭圆宽高
            region = gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, r, r)
            if region:
                user.SetWindowRgn(wintypes.HWND(hwnd), region, True)
                # SetWindowRgn 接管了这块区域，之后由系统释放，不用 DeleteObject
        except Exception:
            applog.silenced("ui.winround.apply")
