"""垃圾清理页：勾选清理项 → 扫描 → 清理。

界面上的三条约定：

1. 每项右侧写清「清的是什么」，不写权限提示 —— 程序默认以管理员身份启动，
   「需要管理员权限」这类文案只会变成噪音。
2. 扫描结果按分类拆开显示，用户能一眼看出空间主要被谁占着。
3. 扫描和删除都丢给任务中心，几万个文件也不会把界面卡住。
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import cleaner, filelock, winmem
from core.metrics import fmt_bytes
from core.store import JunkState

from . import theme_dark as T
from .components import Card, CheckBox, MiniBar, RankList, SegmentBar, label

# 分类构成条用的颜色：固定顺序取色，同一个分类在条形图和列表里颜色一致
CATEGORY_COLORS = (T.ACCENT, T.CYAN, T.GREEN, T.AMBER, T.PURPLE, T.BLUE, T.RED)


def _dot(color: str) -> QLabel:
    """分类前面的小色点。QLabel 会自己画样式表背景，省一个自绘控件。"""
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background: {color}; border-radius: 4px; border: none;")
    return dot


def _names(holders: list[filelock.Holder], limit: int = 3) -> str:
    """把程序名拼成「Chrome、Edge」；超出 limit 补「等 N 个程序」。"""
    names = "、".join(h.name for h in holders[:limit])
    rest = len(holders) - limit
    return f"{names} 等 {len(holders)} 个程序" if rest > 0 else names


def _close_label(holders: list[filelock.Holder]) -> str:
    """主按钮文案。一个程序就点名，多个程序只报个数 —— 按钮放不下三个名字。"""
    if len(holders) == 1:
        return f"关闭 {holders[0].name} 并清理"
    return f"关闭 {len(holders)} 个程序并清理"


class RuleRow(QWidget):
    """一条清理项：复选框 + 名称 + 说明 + 体积。

    整行可点：名称、说明、空白处点一下都能切换勾选。只有 16px 的方框可点
    太考验鼠标精度了。
    """

    HEIGHT = 28

    def __init__(self, rule: cleaner.Rule, checked: bool, parent=None):
        super().__init__(parent)
        self.rule = rule
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self._tooltip())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(8)

        self.check = CheckBox()
        self.check.setChecked(checked)
        layout.addWidget(self.check, 0)

        name = label(rule.name, 11, T.TEXT)
        name.setFixedWidth(104)
        layout.addWidget(name, 0)

        note = label(rule.note or rule.category, 10, T.TEXT_DIM)
        layout.addWidget(note, 1)

        self.lbl_size = label("—", 11, T.TEXT_DIM)
        self.lbl_size.setFixedWidth(72)
        self.lbl_size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.lbl_size, 0)

    def _tooltip(self) -> str:
        paths = "\n".join(self.rule.paths[:6])
        if len(self.rule.paths) > 6:
            paths += f"\n… 共 {len(self.rule.paths)} 处"
        return f"{self.rule.note or self.rule.category}\n\n清理位置：\n{paths}"

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.check.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        if self._hover:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.HOVER))
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(0, 1, 0, -1), 5, 5
            )
            painter.end()

    def reset(self) -> None:
        self.lbl_size.setText("—")
        self.lbl_size.setStyleSheet(
            f"color: {T.TEXT_DIM}; background: transparent; border: none;"
        )

    def set_result(self, count: int, size: int) -> None:
        if count:
            self.lbl_size.setText(fmt_bytes(size))
            self.lbl_size.setStyleSheet(
                f"color: {T.TEXT}; background: transparent; border: none;"
            )
        else:
            self.lbl_size.setText("无")
            self.lbl_size.setStyleSheet(
                f"color: {T.TEXT_DIM}; background: transparent; border: none;"
            )


class FileListDialog(QDialog):
    """扫描结果明细：按体积排序的文件清单，点「查看」就能核对。"""

    LIMIT = 3000

    def __init__(self, items: list[cleaner.FileItem], parent=None):
        super().__init__(parent)
        self.setWindowTitle("待清理文件")
        self.setModal(True)
        self.resize(780, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(8)

        ordered = sorted(items, key=lambda i: i.size, reverse=True)
        total = sum(i.size for i in ordered)
        root.addWidget(
            label(
                f"共 {len(ordered)} 个文件 · {fmt_bytes(total)}",
                13,
                T.TEXT,
                QFont.DemiBold,
            )
        )
        root.addWidget(
            label("按体积从大到小排列，这些都是可再生的缓存，删除后会自动重建。",
                  10, T.TEXT_DIM)
        )

        self.lst = QListWidget()
        self.lst.setUniformItemSizes(True)
        for item in ordered[: self.LIMIT]:
            entry = QListWidgetItem(f"{fmt_bytes(item.size):>10}    {item.path}")
            entry.setToolTip(item.path)
            self.lst.addItem(entry)
        root.addWidget(self.lst, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        if len(ordered) > self.LIMIT:
            footer.addWidget(
                label(f"列表只显示最大的 {self.LIMIT} 个", 10, T.TEXT_DIM)
            )
        footer.addStretch(1)
        btn = QPushButton("关闭")
        btn.setObjectName("Ghost")
        btn.clicked.connect(self.accept)
        footer.addWidget(btn)
        root.addLayout(footer)


class CleanPage(QWidget):
    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._items: list[cleaner.FileItem] = []
        # 清理完成后的 (删除数, 失败数, 释放字节)，用来把汇总卡切成「结果」视图
        self._last_clean: tuple[int, int, int] | None = None
        # 只在下一次 store 更新时渲染结果视图：否则别处触发扫描也会显示上一条「已清理」
        self._pending_done = False
        self._phase = "idle"      # idle / scanning / ready / cleaning / done
        self._admin = winmem.is_admin()
        # 没清掉的文件被谁占着（filelock.Holder）。查到之后主按钮变成
        # 「关闭 X 并清理」—— 只给「重试」的话用户不知道要关谁，点几次都一样
        self._locks: list[filelock.Holder] = []
        self._checking_locks = False
        # 结果视图的正文，锁检查是异步的，回来之后再拼一次
        self._detail_base = ""
        self._done_note = ""

        # 数据由任务中心写进 store，页面只订阅
        self.store.junk_changed.connect(self._render_junk)
        self.tasks.progress.connect(self._on_task_progress)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        row = QHBoxLayout()
        row.setSpacing(9)

        # ---------------------------------------------------------- 左：清理项
        list_card = Card(padding=12, spacing=0)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 0)
        head.setSpacing(6)
        head.addWidget(label("清理项", 14, T.TEXT, QFont.DemiBold))
        head.addStretch(1)
        self.btn_all = QPushButton("全不选")
        self.btn_all.setObjectName("Ghost")
        self.btn_all.setFixedHeight(24)
        self.btn_all.setCursor(Qt.PointingHandCursor)
        self.btn_all.clicked.connect(self._toggle_all)
        head.addWidget(self.btn_all)
        list_card.body().addLayout(head)
        list_card.body().addSpacing(6)

        self.rule_rows: list[RuleRow] = []
        for rule in cleaner.builtin_rules():
            # 默认勾选「安全项」：社交缓存含用户真正收到过的图片视频，不自动勾。
            # needs_admin 只在普通权限下才退回不勾 —— 打包后的程序默认带管理员。
            checked = not rule.default_off and (self._admin or not rule.needs_admin)
            widget = RuleRow(rule, checked)
            widget.check.toggled.connect(self._sync_all_button)
            list_card.body().addWidget(widget)
            self.rule_rows.append(widget)

        list_card.body().addStretch(1)
        hint = label(
            "只清可再生缓存。系统、程序与文档目录不会被碰到；"
            "社交缓存含已收的图片视频，需要手动勾选。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        list_card.body().addWidget(hint)
        row.addWidget(list_card, 2)

        # ---------------------------------------------------------- 右：汇总
        summary = Card(padding=14, spacing=6)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self.lbl_title = label("可释放", 14, T.TEXT, QFont.DemiBold)
        top.addWidget(self.lbl_title)
        top.addStretch(1)
        self.lbl_time = label("尚未扫描", 10, T.TEXT_DIM)
        top.addWidget(self.lbl_time)
        summary.body().addLayout(top)

        self.lbl_size = label("—", 30, T.TEXT, QFont.DemiBold)
        summary.body().addWidget(self.lbl_size)

        self.lbl_detail = label("点「扫描垃圾」开始，约 1 秒", 10, T.TEXT_DIM)
        self.lbl_detail.setWordWrap(True)
        summary.body().addWidget(self.lbl_detail)

        self.progress = MiniBar(4)
        self.progress.hide()
        summary.body().addWidget(self.progress)
        summary.body().addSpacing(2)

        self.bar = SegmentBar(height=8)
        self.bar.hide()
        summary.body().addWidget(self.bar)

        self.break_box = QVBoxLayout()
        self.break_box.setSpacing(1)
        summary.body().addLayout(self.break_box)
        self._break_rows: list[QWidget] = []

        summary.body().addSpacing(4)
        self.top_title = label("占用最多", 10, T.TEXT_DIM)
        self.top_title.hide()
        summary.body().addWidget(self.top_title)
        self.top_list = RankList(rows=5, elide_width=140)
        self.top_list.hide()
        summary.body().addWidget(self.top_list)

        summary.body().addStretch(1)

        note = label(
            "扫描只读，不会改动文件。删除的都是可再生缓存，程序会自动重建。",
            10,
            T.TEXT_DIM,
        )
        note.setWordWrap(True)
        summary.body().addWidget(note)

        self.btn_view = QPushButton("查看文件清单")
        self.btn_view.setObjectName("Ghost")
        self.btn_view.setFixedHeight(26)
        self.btn_view.setCursor(Qt.PointingHandCursor)
        self.btn_view.clicked.connect(self._open_file_list)
        self.btn_view.hide()
        summary.body().addWidget(self.btn_view)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_action = QPushButton("扫描垃圾")
        self.btn_action.setObjectName("Primary")
        self.btn_action.setFixedHeight(34)
        self.btn_action.setCursor(Qt.PointingHandCursor)
        self.btn_action.clicked.connect(self._on_action)
        buttons.addWidget(self.btn_action, 1)

        self.btn_alt = QPushButton("重新扫描")
        self.btn_alt.setObjectName("Ghost")
        self.btn_alt.setFixedHeight(34)
        self.btn_alt.setCursor(Qt.PointingHandCursor)
        self.btn_alt.clicked.connect(self._on_alt)
        self.btn_alt.hide()
        buttons.addWidget(self.btn_alt, 0)
        summary.body().addLayout(buttons)

        row.addWidget(summary, 1)
        root.addLayout(row, 1)

        self._sync_buttons()

    # ---------------------------------------------------------- 交互

    def _selected_rows(self) -> list[RuleRow]:
        """参与「全选/全不选」的行：社交缓存这类默认关闭的不算在内。"""
        return [r for r in self.rule_rows if not r.rule.default_off]

    def _toggle_all(self) -> None:
        rows = self._selected_rows()
        target = not all(r.check.isChecked() for r in rows)
        for row in rows:
            row.check.setChecked(target)
        for row in self.rule_rows:
            if row.rule.default_off:
                row.check.setChecked(False)
        self._sync_all_button()

    def _sync_all_button(self) -> None:
        rows = self._selected_rows()
        all_on = bool(rows) and all(r.check.isChecked() for r in rows)
        self.btn_all.setText("全不选" if all_on else "全选")

    def selected_rules(self) -> list[str]:
        return [r.rule.id for r in self.rule_rows if r.check.isChecked()]

    def _sync_buttons(self) -> None:
        scanning = self._phase == "scanning"
        cleaning = self._phase == "cleaning"
        busy = scanning or cleaning
        holders = self._closable_locks()

        self.btn_action.setEnabled(not busy)
        if scanning:
            self.btn_action.setText("扫描中…")
        elif cleaning:
            self.btn_action.setText("清理中…")
        elif self._phase == "ready":
            self.btn_action.setText("立即清理")
        elif self._phase == "done" and self._items:
            # 查占用在后台跑，按钮先保持「重试」：查询通常不到一秒，
            # 但万一排在别的任务后面，按钮也不能一直显示「检查占用…」又不能点
            if holders:
                self.btn_action.setText(_close_label(holders))
            else:
                self.btn_action.setText(f"重试剩余 {len(self._items)} 个")
        elif self._phase == "done":
            self.btn_action.setText("再次扫描")
        else:
            self.btn_action.setText("扫描垃圾")

        if busy:
            self.btn_alt.setText("取消")
            self.btn_alt.show()
        elif self._phase == "ready":
            self.btn_alt.setText("重新扫描")
            self.btn_alt.show()
        elif self._phase == "done" and self._items and holders:
            # 用户可能已经自己把程序关了：留一条不关程序的退路
            self.btn_alt.setText("直接重试")
            self.btn_alt.show()
        else:
            self.btn_alt.hide()

        # 只有拿到扫描结果才谈得上「清单」
        self.btn_view.setVisible(bool(self._items) and not busy)

        for row in self.rule_rows:
            row.check.setEnabled(not busy)

    # ---------------------------------------------------------- 占用者

    def _closable_locks(self) -> list[filelock.Holder]:
        return [h for h in self._locks if h.closable]

    def _blocked_locks(self) -> list[filelock.Holder]:
        return [h for h in self._locks if not h.closable]

    def _check_locks(self) -> None:
        """清理完还剩东西：问一下是谁占着，界面才好给出「关闭 X 并清理」。"""
        paths = [i.path for i in self._items]
        if not paths or self._checking_locks:
            return
        if not filelock.available() or self.tasks.is_busy("junk_locks"):
            return
        self._checking_locks = True
        self.tasks.submit(
            "junk_locks", "检查文件占用", lambda report, stop: filelock.lockers(paths)
        )

    def _update_detail(self) -> None:
        """结果视图的正文：查到占用者就把名字说出来，查不到保持通用文案。"""
        if self._phase != "done":
            return
        holders = self._closable_locks()
        blocked = self._blocked_locks()
        if holders:
            self.lbl_detail.setText(
                f"还有 {len(self._items)} 个文件被「{_names(holders, 2)}」占用"
            )
        elif blocked:
            # 这类进程（explorer、svchost）关掉代价太大，只能等重启
            self.lbl_detail.setText(
                f"还有 {len(self._items)} 个文件被「{_names(blocked, 2)}」占用，"
                "属于系统程序、不会自动关；重启电脑后再试"
            )
        elif self._detail_base:
            self.lbl_detail.setText(self._detail_base)

    def _on_action(self) -> None:
        if self._items and self._phase in ("ready", "done"):
            if self._phase == "done" and self._closable_locks():
                self._confirm_unlock()
            else:
                # 重试同一批文件不再弹确认框：用户已经确认过一次，每点一次都弹
                # 一遍只会让人以为按钮没反应
                self._confirm_clean(ask=self._phase != "done")
        else:
            self.start_scan()

    def _on_alt(self) -> None:
        if self._phase in ("scanning", "cleaning"):
            self._cancel()
        else:
            self.start_scan()

    def _cancel(self) -> None:
        for task_id in ("junk", "junk_clean", "junk_unlock"):
            if self.tasks.cancel(task_id):
                self.lbl_detail.setText("正在停止…")
                return

    def start_scan(self) -> None:
        rule_ids = self.selected_rules()
        if not rule_ids:
            self.lbl_detail.setText("请先勾选至少一项")
            return
        if self.tasks.is_busy("junk"):
            return

        self._items = []
        self._last_clean = None
        self._pending_done = False
        self._locks = []
        self._checking_locks = False
        self._done_note = ""
        self._detail_base = ""
        for row in self.rule_rows:
            row.reset()
        self.lbl_size.setText("—")
        self.lbl_size.setStyleSheet(
            f"color: {T.TEXT}; background: transparent; border: none;"
        )
        self.lbl_title.setText("可释放")
        self.lbl_detail.setText("正在扫描…")
        self.lbl_time.setText("扫描中")
        self._clear_breakdown()
        self._render_top([])
        self.bar.hide()
        self.progress.set_value(0, T.ACCENT)
        self.progress.show()
        self._phase = "scanning"
        self._sync_buttons()

        self.tasks.submit("junk", "扫描垃圾文件", self._make_scan_task(rule_ids))

    def _make_scan_task(self, rule_ids: list[str]):
        """构造扫描任务：在工作线程里扫，返回 JunkState 由主窗口写进 store。"""

        def run(report, should_stop):
            # 名字表只取一次：builtin_rules() 里含社交目录探测，反复调用很慢
            names = {r.id: r.name for r in cleaner.builtin_rules()}
            order = [rid for rid in rule_ids if rid in names]
            total_rules = max(1, len(order))
            items: list[cleaner.FileItem] = []
            seen = 0

            def on_progress(rule_id: str, batch: int) -> None:
                nonlocal seen
                seen += batch
                index = order.index(rule_id) + 1 if rule_id in order else 1
                report(
                    min(99, int(index * 100 / total_rules)),
                    f"正在扫描{names.get(rule_id, rule_id)} · 已找到 {seen} 个",
                )

            for chunk in cleaner.scan(
                rule_ids, should_stop=should_stop, on_progress=on_progress
            ):
                items.extend(chunk)
                if should_stop():
                    return None

            return JunkState(
                items=items,
                total=sum(i.size for i in items),
                update_backlog=cleaner.update_backlog(items),
                social=sum(i.size for i in items if i.category == "社交缓存"),
                scanned_at=time.time(),
            )

        return run

    def _confirm_clean(self, ask: bool = True) -> None:
        if not self._items:
            self.lbl_detail.setText("没有可清理的文件")
            return
        total = sum(i.size for i in self._items)
        if ask:
            answer = QMessageBox.question(
                self,
                "确认清理",
                f"将删除 {len(self._items)} 个文件，共 {fmt_bytes(total)}。\n\n"
                "这些都是可再生的临时文件与缓存，删除后程序会自动重建。\n"
                "系统目录、程序目录和文档目录不会被动到。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        items = list(self._items)
        self._phase = "cleaning"
        self.lbl_detail.setText("正在清理…")
        self.progress.set_value(0, T.ACCENT)
        self.progress.show()
        self._sync_buttons()
        self.tasks.submit("junk_clean", "清理垃圾文件", self._make_clean_task(items))

    def _make_clean_task(self, items: list[cleaner.FileItem]):
        def run(report, should_stop):
            def on_progress(done: int, total: int) -> None:
                report(int(done * 100 / max(1, total)), f"已删除 {done}/{total}")

            return cleaner.delete_items(
                items, should_stop=should_stop, on_progress=on_progress
            )

        return run

    def _unlock_box(self, holders: list[filelock.Holder]):
        """关程序前的确认框：关哪些、为什么要关、有什么代价。

        和 `exec()` 分开，只为截图工具能单独抓到这一帧。
        """
        box = QMessageBox(self)
        box.setWindowTitle("关闭程序并清理")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"还有 {len(self._items)} 个文件被「{_names(holders)}」占用，"
            "需要先关闭这些程序。"
        )
        box.setInformativeText(
            "程序会先被要求正常关闭，没保存的内容可能丢失，建议先保存。\n"
            "一时没退出的会被强制结束。"
        )
        go = box.addButton(f"关闭 {_names(holders, 2)} 并清理", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(go)
        return box, go

    def _confirm_unlock(self) -> None:
        holders = self._closable_locks()
        if not holders:
            self._confirm_clean(ask=False)
            return
        box, go = self._unlock_box(holders)
        box.exec()
        if box.clickedButton() is not go:
            return
        self._start_unlock(holders)

    def _start_unlock(self, holders: list[filelock.Holder]) -> None:
        items = list(self._items)
        self._phase = "cleaning"
        self.lbl_detail.setText(f"正在关闭 {_names(holders, 2)}…")
        self.progress.set_value(0, T.ACCENT)
        self.progress.show()
        self._sync_buttons()
        self.tasks.submit(
            "junk_unlock",
            "关闭程序并清理",
            self._make_unlock_task(items, holders),
        )

    def _make_unlock_task(
        self, items: list[cleaner.FileItem], holders: list[filelock.Holder]
    ):
        """先关程序再删。

        程序退出到句柄释放之间有一两帧的延迟，关完立刻删会白删一批 ——
        那两个文件又会以「被占用」的身份回到清单里。
        """

        def run(report, should_stop):
            report(4, f"正在关闭 {_names(holders, 2)}")
            closed, _failed = filelock.close(holders, should_stop=should_stop)
            if should_stop():
                return None
            time.sleep(0.4)

            def on_progress(done: int, total: int) -> None:
                report(40 + int(done * 60 / max(1, total)), f"已删除 {done}/{total}")

            deleted, failed, freed, remaining = cleaner.delete_items(
                items, should_stop=should_stop, on_progress=on_progress
            )
            return deleted, failed, freed, remaining, closed

        return run

    def _open_file_list(self) -> None:
        if not self._items:
            return
        FileListDialog(self._items, self).exec()

    # ---------------------------------------------------------- 任务回调

    def _on_task_progress(self, task_id: str, percent: int, text: str) -> None:
        if task_id not in ("junk", "junk_clean", "junk_unlock"):
            return
        self.progress.set_value(percent / 100, T.ACCENT)
        if text:
            self.lbl_detail.setText(f"{text}…")

    def _on_task_finished(self, task_id: str, result) -> None:
        if task_id == "junk":
            # 扫描结果已经由 store 的 junk_changed 渲染过了，这里只管按钮态
            self.progress.hide()
            self._phase = "ready" if self._items else "idle"
            self._sync_buttons()
            return

        if task_id == "junk_locks":
            self._checking_locks = False
            # 查的过程中用户可能已经重新扫描：结论只对「刚清完的那批」有效
            if self._phase == "done" and self._items:
                self._locks = list(result or [])
                self._update_detail()
            self._sync_buttons()
            return

        if task_id not in ("junk_clean", "junk_unlock") or result is None:
            return

        if task_id == "junk_unlock":
            deleted, failed, freed, remaining, closed = result
            # 关了几个程序要写进结果文案，否则用户看不出「关闭并清理」做了什么
            self._done_note = f"已关闭 {closed} 个程序" if closed else ""
        else:
            deleted, failed, freed, remaining = result
            self._done_note = ""

        self._last_clean = (deleted, failed, freed)
        # 没删掉的写回 store：列表、体检分数都跟着变成真实值
        self._pending_done = True
        self.store.set_junk_items(
            remaining,
            sum(i.size for i in remaining),
            cleaner.update_backlog(remaining),
        )

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id == "junk":
            self.progress.hide()
            if self.store.junk.ready:
                # 取消扫描只是没拿到新结果，上一次的清单还在，直接回填
                self._render_junk()
            else:
                self._phase = "idle"
                self._items = []
                self.lbl_detail.setText("已取消")
                self.lbl_time.setText("尚未扫描")
                self._sync_buttons()
            return

        if task_id in ("junk_clean", "junk_unlock"):
            self.progress.hide()
            self._phase = "ready" if self._items else "idle"
            self.lbl_detail.setText("清理已中断，仍有文件可清理")
            self._sync_buttons()
            return

        if task_id == "junk_locks":
            # 查询被打断/抛异常时任务中心发的是 cancelled，不处理的话
            # 「正在检查占用」的标志会一直立着，按钮再也点不动
            self._checking_locks = False
            self._sync_buttons()

    def shutdown(self) -> None:
        """换肤重建界面前调用：让任务停下来，别让 QThread 在运行中被析构。"""
        for task_id in ("junk", "junk_clean", "junk_locks", "junk_unlock"):
            if self.tasks.is_busy(task_id):
                self.tasks.cancel(task_id)

    # ---------------------------------------------------------- 渲染

    def _render_junk(self) -> None:
        """store 里的垃圾数据变了就重画。扫描完、清理完都走这里。"""
        state = self.store.junk
        self._items = list(state.items)
        self.progress.hide()
        # 清单换了，上一批「谁占着这些文件」的结论就作废
        self._locks = []
        self._checking_locks = False
        self._detail_base = ""

        if self._pending_done:
            self._pending_done = False
            self._render_done(state)
            return

        self.lbl_title.setText("可释放")

        if not state.items:
            self._phase = "idle"
            self.lbl_size.setText("0 KB")
            self.lbl_size.setStyleSheet(
                f"color: {T.TEXT_DIM}; background: transparent; border: none;"
            )
            self.lbl_detail.setText("没有发现可清理的垃圾")
            self.lbl_time.setText(_stamp(state.scanned_at))
            self.bar.hide()
            self._clear_breakdown()
            self._render_top([])
            for row in self.rule_rows:
                row.reset()
            self._sync_buttons()
            return

        self._phase = "ready"
        self.lbl_size.setText(fmt_bytes(state.total))
        self.lbl_size.setStyleSheet(
            f"color: {T.TEXT}; background: transparent; border: none;"
        )
        summary = cleaner.rule_summary(state.items)
        self.lbl_detail.setText(
            f"{len(state.items)} 个文件 · 命中 {len(summary)} 项规则"
        )
        self.lbl_time.setText(_stamp(state.scanned_at))
        self._render_breakdown(summary)
        self._render_rows(summary)
        self._render_top(state.items)
        self._sync_buttons()

    def _render_rows(self, summary) -> None:
        """把每条规则的体积回填到左侧列表。"""
        hits = {result.rule_id: result for result in summary}
        for row in self.rule_rows:
            result = hits.get(row.rule.id)
            if result is None:
                row.set_result(0, 0)
            else:
                row.set_result(result.count, result.size)

    def _render_top(self, items: list[cleaner.FileItem]) -> None:
        """右卡「占用最多」：给 5 个最大文件，让人知道空间到底被谁吃了。"""
        if not items:
            self.top_title.hide()
            self.top_list.hide()
            self.top_list.clear()
            return
        biggest = sorted(items, key=lambda i: i.size, reverse=True)[:5]
        self.top_list.set_rows(
            [(os.path.basename(i.path), "", fmt_bytes(i.size)) for i in biggest]
        )
        self.top_title.show()
        self.top_list.show()

    def _render_done(self, state) -> None:
        """清理完成：大数字改成「已释放」，并说明还剩多少没删掉。"""
        deleted, failed, freed = self._last_clean or (0, 0, 0)
        self._phase = "done"
        remaining = len(state.items)
        if deleted:
            self.lbl_title.setText("已释放")
            self.lbl_size.setText(fmt_bytes(freed) if freed else "0 KB")
            self.lbl_size.setStyleSheet(
                f"color: {T.GREEN}; background: transparent; border: none;"
            )
        else:
            # 一个都没删掉时别用绿色写「已释放 0 KB」，那看着像成功
            self.lbl_title.setText("未释放")
            self.lbl_size.setText("0 KB")
            self.lbl_size.setStyleSheet(
                f"color: {T.TEXT_DIM}; background: transparent; border: none;"
            )

        parts: list[str] = []
        if self._done_note:
            parts.append(self._done_note)
            self._done_note = ""
        if deleted:
            parts.append(f"已清理 {deleted} 个文件")
        if failed:
            parts.append(f"{failed} 个文件正被程序占用")
        detail = " · ".join(parts) or "没有可清理的文件"
        self._detail_base = detail
        self.lbl_detail.setText(detail)
        self.lbl_time.setText("刚刚完成")
        self.bar.hide()
        self._clear_breakdown()
        remaining_summary = cleaner.rule_summary(state.items) if remaining else []
        self._render_rows(remaining_summary)
        if remaining:
            self._render_breakdown(remaining_summary, bar=False)
        self._render_top(state.items)
        self._sync_buttons()

        # 还有没清掉的：查一下是谁占着。查到就把主按钮换成「关闭 X 并清理」，
        # 只给「重试剩余 N 个」的话用户不知道该关谁。
        # 带 self 作为 context：换肤重建后旧页面已经销毁，延后一帧的回调
        # 不能再去碰已经被删掉的按钮
        if remaining:
            QTimer.singleShot(0, self, self._check_locks)

    def _clear_breakdown(self) -> None:
        for widget in self._break_rows:
            # 先摘掉父级：只 deleteLater 的话控件要等事件循环才真正消失，
            # 下一帧会把旧分类和新的叠在一起画出来
            widget.setParent(None)
            widget.deleteLater()
        self._break_rows.clear()
        self.bar.set_segments([])

    def _render_breakdown(self, summary, bar: bool = True) -> None:
        self._clear_breakdown()
        if not summary:
            return

        by_category: dict[str, list[int]] = {}
        for result in summary:
            entry = by_category.setdefault(result.category, [0, 0])
            entry[0] += result.count
            entry[1] += result.size

        ordered = sorted(by_category.items(), key=lambda kv: kv[1][1], reverse=True)
        colors = {
            category: CATEGORY_COLORS[index % len(CATEGORY_COLORS)]
            for index, (category, _) in enumerate(ordered)
        }

        if bar:
            total = sum(size for _count, size in by_category.values()) or 1
            self.bar.set_segments(
                [(size / total, colors[category]) for category, (_c, size) in ordered]
            )
            self.bar.show()

        for category, (count, size) in ordered:
            line = QWidget()
            line.setFixedHeight(20)
            layout = QHBoxLayout(line)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            layout.addWidget(_dot(colors[category]), 0)
            layout.addWidget(label(category, 10, T.TEXT_SUB), 0)
            layout.addWidget(label(f"{count} 个", 10, T.TEXT_DIM), 0)
            layout.addStretch(1)
            layout.addWidget(label(fmt_bytes(size), 10, T.TEXT_SUB), 0)
            line.setToolTip(f"{category}：{count} 个文件，{fmt_bytes(size)}")
            self.break_box.addWidget(line)
            # 父卡片已经显示出来了，后加的控件不会自动 show，得手动叫一次
            line.show()
            self._break_rows.append(line)


def _stamp(scanned_at: float) -> str:
    if not scanned_at:
        return "尚未扫描"
    return time.strftime("%H:%M 扫描", time.localtime(scanned_at))
