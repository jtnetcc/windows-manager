"""清理中心：一个导航项，页内分段切换四件事。

原来这四件事各占一行侧栏（垃圾清理 / 软件专清 / 重复文件 / 回收中心），
加上磁盘空间，同一个问题「哪里能腾空间」要在五六行里找。用户的心智是
「我想清点东西」，不是「我这份垃圾属于哪个类别」。

分段按**操作粒度**划分，不按目录类型：

- 常规清理：按规则扫全盘，一次清光，不用想
- 按软件：只清某个软件的缓存，能看清它吃了多少
- 重复文件：按内容哈希找副本，要人判断留哪份
- 回收站：清完之后的反悔入口

磁盘空间单独一个入口（那边是「看空间去哪了」，不是「清理」）。

四个子页都是第一次切过去才建：进程里存着几百个文件项，多数人打开只想清
常规垃圾。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from . import theme_dark as T
from .components import Segmented, label

SEGMENTS = [
    ("clean", "常规清理"),
    ("appclean", "按软件"),
    ("dup", "重复文件"),
    ("recycle", "回收站"),
]

SEGMENT_SUBS = {
    "clean": "只清理可再生的临时文件与缓存",
    "appclean": "按软件清理缓存与已接收的图片视频",
    "dup": "按内容哈希找出重复副本",
    "recycle": "回收站内容一览，按体积排序，可还原或清空",
}

SEGMENT_KEYS = {key for key, _text in SEGMENTS}

# 第一次切到某段时要主动派一次扫描的（其余段等用户点按钮）。
# 这两页原来靠主窗口的 LAZY_STARTERS 触发，收进壳里之后由壳负责。
_AUTO_START = {"appclean": "start_scan", "recycle": "start_scan"}


class CleanupPage(QWidget):
    segment_changed = Signal(str, str)

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self._store = store
        self._tasks = tasks

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.seg = Segmented(SEGMENTS)
        head.addWidget(self.seg)
        head.addStretch(1)
        self.lbl_note = label("", 11, T.TEXT_DIM)
        head.addWidget(self.lbl_note, 0, Qt.AlignVCenter)
        root.addLayout(head)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self._pages: dict[str, QWidget] = {}
        self._started: set[str] = set()
        self._key = SEGMENTS[0][0]
        self.seg.changed.connect(self._on_segment)
        self._on_segment(self._key)

    # ---------------------------------------------------------- 分段

    def _build(self, key: str) -> QWidget | None:
        if key == "clean":
            from .page_clean import CleanPage

            return CleanPage(self._store, self._tasks)
        if key == "appclean":
            from .page_appclean import AppCleanPage

            return AppCleanPage(self._store, self._tasks)
        if key == "dup":
            from .page_dup import DupPage

            return DupPage(self._store, self._tasks)
        if key == "recycle":
            from .page_recycle import RecyclePage

            return RecyclePage(self._tasks)
        return None

    def _ensure(self, key: str) -> QWidget | None:
        page = self._pages.get(key)
        if page is not None:
            return page
        page = self._build(key)
        if page is None:
            return None
        self._pages[key] = page
        self.stack.addWidget(page)
        return page

    def _on_segment(self, key: str) -> None:
        if key not in SEGMENT_KEYS:
            return
        page = self._ensure(key)
        if page is None:
            return
        self._key = key
        self.stack.setCurrentWidget(page)
        self.segment_changed.emit(key, SEGMENT_SUBS.get(key, ""))

        # 首次进入才派扫描：和以前 LAZY_STARTERS 的时机保持一致
        if key in _AUTO_START and key not in self._started:
            self._started.add(key)
            starter = getattr(page, _AUTO_START[key], None)
            if starter is not None:
                starter()

    def set_segment(self, key: str) -> None:
        """外部切段（跨页跳转、换肤还原）。和用户点击走同一条路。"""
        if key in SEGMENT_KEYS:
            self.seg.set_current(key)

    def ensure_segment(self, key: str) -> QWidget | None:
        """建出某个分段但不切过去。

        体检的「全面体检」要一次派出垃圾扫描和查重，两个分段都得存在，
        但屏幕只能显示一个 —— 所以不能走 set_segment。
        """
        return self._ensure(key)

    def segment_page(self, key: str) -> QWidget | None:
        """只取已建出来的分段，不新建。

        托盘/后台任务的回填走这里：没建过的页面不必为了送一份结果把它造出来，
        用户切过去时会自己重扫。
        """
        return self._pages.get(key)

    def current_segment(self) -> str:
        return self._key

    def subtitle(self) -> str:
        return SEGMENT_SUBS.get(self._key, "")

    # ---------------------------------------------------------- 生命周期

    def start_scan(self) -> None:
        """F5：只重扫当前分段。"""
        page = self._pages.get(self._key)
        starter = getattr(page, "start_scan", None) if page else None
        if starter is not None:
            starter()

    def shutdown(self) -> None:
        """退出时把已建出来的分段都收一遍。"""
        for page in self._pages.values():
            handler = getattr(page, "shutdown", None)
            if handler is not None:
                handler()
