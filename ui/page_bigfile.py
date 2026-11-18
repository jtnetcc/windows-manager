"""大文件页：扫描指定范围里的大文件，按格式筛选，勾选后删除到回收站。"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import bigfile, safety, trash
from core.metrics import fmt_bytes
from core.store import BigFileState

from . import theme_dark as T
from .components import (
    Card,
    ComboBox,
    copy_text,
    font,
    label,
    make_table,
    reveal_file,
)

SIZES = [
    (1, "≥ 1 MB"),
    (5, "≥ 5 MB"),
    (10, "≥ 10 MB"),
    (30, "≥ 30 MB"),
    (50, "≥ 50 MB"),
    (100, "≥ 100 MB"),
    (200, "≥ 200 MB"),
    (500, "≥ 500 MB"),
    (1024, "≥ 1 GB"),
]
DEFAULT_SIZE_MB = 200
DEFAULT_SIZE_INDEX = [value for value, _text in SIZES].index(DEFAULT_SIZE_MB)
LIMIT = 800  # 单次结果上限：再多列表也没法看，按体积取前 N 个

# 类型的唯一来源：胶囊条（原来还有一个「扫描类型」下拉，和它重复，已合并）。
TYPE_LABEL = {"all": "全部"}
TYPE_LABEL.update(dict(bigfile.CATEGORIES))

SORT_NAMES = {1: "名称", 2: "类型", 3: "大小", 4: "时间", 5: "目录"}


def _size_index(mb: int) -> int:
    """找最接近的一档阈值（切类型时用建议值，可能不在表里）。"""
    best = 0
    for index, (value, _text) in enumerate(SIZES):
        if abs(value - mb) < abs(SIZES[best][0] - mb):
            best = index
    return best


class BigFilePage(QWidget):
    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._files: list[bigfile.BigFile] = []
        self._rows: list[bigfile.BigFile] = []
        # 默认「所有磁盘」：只看 C 盘会漏掉装在其它分区的游戏、素材、镜像
        self._scopes = bigfile.scope_items()
        self._roots = bigfile.preset_roots(bigfile.DEFAULT_SCOPE)
        self._checked: set[str] = set()
        self._filter = "all"
        self._scan_kind = "all"
        self._sort_col = 3        # 默认按体积从大到小
        self._sort_desc = True
        self._just_cleaned = False  # 刚删完时的空列表，别提示成「没找到」
        self._filling = False
        self._custom_index = 0

        self.store.bigfile_changed.connect(self._render_bigfile)
        self.tasks.progress.connect(self._on_task_progress)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        card = Card(padding=12, spacing=8)

        # ---------------- 第一行：范围 + 阈值 + 扫描
        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)

        self.root_box = ComboBox()
        self.root_box.addItems([text for _key, text in self._scopes])
        self.root_box.setCurrentIndex(
            [key for key, _text in self._scopes].index(bigfile.DEFAULT_SCOPE)
        )
        self.root_box.setFixedHeight(28)
        self.root_box.setFixedWidth(136)
        self.root_box.setToolTip(
            "扫描范围。默认所有本地磁盘；\n"
            + "\n".join(self._roots)
        )
        self.root_box.activated.connect(self._on_root_changed)
        toolbar.addWidget(self.root_box)

        # 注意：这里原来还有一个「扫描类型」下拉和一个「目录…」按钮。
        # 前者和下面的类型胶囊完全重复（同样是 8 个类型），后者和范围下拉里的
        # 「自定义目录…」做的是同一件事 —— 两处都删了，见 6.49。
        self.size_box = ComboBox()
        self.size_box.addItems([text for _size, text in SIZES])
        self.size_box.setCurrentIndex(DEFAULT_SIZE_INDEX)
        self.size_box.setFixedHeight(28)
        self.size_box.setFixedWidth(124)
        self.size_box.setToolTip("小于这个体积的文件不进列表")
        toolbar.addWidget(self.size_box)

        self.btn_scan = QPushButton("开始扫描")
        self.btn_scan.setObjectName("Primary")
        self.btn_scan.setFixedHeight(28)
        self.btn_scan.clicked.connect(self.start_scan)
        toolbar.addWidget(self.btn_scan)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("Ghost")
        self.btn_stop.clicked.connect(self._stop_scan)
        self.btn_stop.hide()
        toolbar.addWidget(self.btn_stop)

        toolbar.addStretch(1)
        self.lbl_stat = label("选好范围后点「开始扫描」", 11, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_stat)
        card.body().addLayout(toolbar)

        # ---------------- 第二行：类型（筛选 + 扫描范围，一个控件管两件事）
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        self._chips: dict[str, QPushButton] = {}
        for key, text in [("all", "全部")] + bigfile.CATEGORIES:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFixedHeight(24)
            chip.setStyleSheet(self._chip_style())
            chip.setToolTip(
                "选中后：当前列表按这一类过滤；再点「开始扫描」就只扫这一类"
            )
            chip.clicked.connect(lambda _c=False, k=key: self._on_type_selected(k))
            self._chip_group.addButton(chip)
            self._chips[key] = chip
            chips.addWidget(chip)
        self._chips["all"].setChecked(True)
        chips.addStretch(1)
        self.lbl_total = label("", 10, T.TEXT_DIM)
        chips.addWidget(self.lbl_total)
        card.body().addLayout(chips)

        # ---------------- 表格
        self.table = make_table(
            ["", "文件名", "类型", "大小", "修改时间", "所在目录"], menu=True
        )
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setColumnWidth(0, 30)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(2, 66)
        self.table.setColumnWidth(3, 92)
        self.table.setColumnWidth(4, 118)

        # 排序自己做：QTableWidget 自带的排序会打乱行号和 _rows 的对应，
        # 勾选态就错行了。点表头 -> 重排 _rows -> 重填，勾选按路径走不会错。
        # 箭头直接写进表头文字：QHeaderView 自带的排序三角在自定义样式下
        # 会画到表头下边缘外面，看着像多出一小块。
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_by)
        self.table.setToolTip("点表头可以按名称、类型、大小、时间、目录排序")

        card.add(self.table, 1)

        # 空结果时表格一片空白，看着像没加载出来。盖一个居中的说明上去，
        # 不接鼠标事件，不挡右键菜单。
        self.empty = QLabel("", self.table)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.empty.setStyleSheet(
            f"color: {T.TEXT_DIM}; background: transparent; font-size: 12px;"
        )
        self.empty.hide()

        # ---------------- 底部：全选 + 删除
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self.btn_all = QPushButton("全选")
        self.btn_all.setObjectName("Ghost")
        self.btn_all.setFixedHeight(26)
        self.btn_all.clicked.connect(self._check_all)
        bottom.addWidget(self.btn_all)

        self.btn_invert = QPushButton("反选")
        self.btn_invert.setObjectName("Ghost")
        self.btn_invert.setFixedHeight(26)
        self.btn_invert.clicked.connect(self._invert)
        bottom.addWidget(self.btn_invert)

        # 排序状态放这儿：筛选条右侧要放「当前 N 个 · 体积」，两段文字挤在
        # 一行会被卡片边缘裁掉，底部这块本来就有空白。
        self.lbl_sort = label("按大小 · 降序", 10, T.TEXT_DIM)
        self.lbl_sort.setToolTip("点表头可以换排序方式，再点一次反向")
        bottom.addSpacing(10)
        bottom.addWidget(self.lbl_sort)

        bottom.addStretch(1)
        self.lbl_sel = label("已选 0 项", 11, T.TEXT_DIM)
        bottom.addWidget(self.lbl_sel)

        self.btn_delete = QPushButton("删除到回收站")
        self.btn_delete.setObjectName("Primary")
        self.btn_delete.setFixedHeight(26)
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self.delete_checked)
        bottom.addWidget(self.btn_delete)
        card.body().addLayout(bottom)

        root.addWidget(card, 1)

        hint = label(
            "范围默认是「所有磁盘」（含 C/D/E… 全部本地盘），也可以在左侧下拉里"
            "选单个盘、用户目录或自定义目录；类型胶囊选中后立刻过滤当前列表，"
            "再点「开始扫描」就只扫这一类，切类型会自动调低体积门槛。"
            "点表头能按名称/大小/时间排序。扫描只读文件属性，删除一律走系统回收站，"
            "误删可还原；系统保护目录不参与删除，休眠/分页文件会标灰不可选。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._update_roots_hint()
        self._sync_empty_hint()

    # ---------------------------------------------------------- 样式

    @staticmethod
    def _chip_style() -> str:
        return (
            f"QPushButton {{ background: transparent; border: 1px solid {T.BORDER};"
            f" border-radius: 12px; padding: 0 9px; color: {T.TEXT_DIM};"
            f" font-size: 11px; }}"
            f"QPushButton:hover {{ background: {T.HOVER}; color: {T.TEXT}; }}"
            f"QPushButton:checked {{ background: {T.ACCENT_SOFT};"
            f" border-color: {T.ACCENT}; color: {T.ACCENT}; }}"
            # 类型扫描后，没扫的类目会置灰。默认禁用色和普通色太接近，
            # 用边框色当文字色，一眼能看出「这次没扫这类」。
            f"QPushButton:disabled {{ color: {T.BORDER_STRONG};"
            f" border-color: {T.BORDER_SOFT}; background: transparent; }}"
        )

    # ---------------------------------------------------------- 范围

    def _root_scope_text(self) -> str:
        if not self._roots:
            return "未选择范围"
        if len(self._roots) == 1:
            path = self._roots[0]
            home = os.path.expanduser("~")
            if path.lower().startswith(home.lower()):
                return "~" + path[len(home):]
            return path
        # 盘符根（`C:\`）和普通目录要说清楚，别把「视频+图片+音乐」说成 3 个磁盘
        if all(len(root) == 3 and root.endswith(":\\") for root in self._roots):
            return f"{len(self._roots)} 个磁盘"
        return f"{len(self._roots)} 个目录"

    def _update_roots_hint(self) -> None:
        text = self._root_scope_text()
        self.lbl_stat.setToolTip("\n".join(self._roots))
        if not self._files:
            suffix = "" if self._scan_kind == "all" else f" · 只看{bigfile.CATEGORY_LABEL[self._scan_kind]}"
            self.lbl_stat.setText(f"范围：{text}{suffix}")

    def _custom_scope_index(self) -> int:
        return [key for key, _text in self._scopes].index("custom")

    def _on_root_changed(self, index: int) -> None:
        key = self._scopes[index][0] if 0 <= index < len(self._scopes) else ""
        if key == "custom":
            if not self._pick_dir():
                # 取消选择就退回上一个范围，别让下拉框停在「自定义目录…」上
                self.root_box.blockSignals(True)
                self.root_box.setCurrentIndex(self._preset_index())
                self.root_box.blockSignals(False)
            return
        roots = bigfile.preset_roots(key)
        if not roots:
            self.lbl_stat.setText("这个范围没有可用目录（磁盘未挂载或目录不存在）")
            return
        self._roots = roots
        self._custom_index = index
        self.root_box.setToolTip("扫描范围：\n" + "\n".join(roots))
        self._update_roots_hint()

    def _pick_dir(self) -> bool:
        start = self._roots[0] if self._roots else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择要扫描的目录", start)
        if not path:
            return False
        self._roots = [path]
        self.root_box.blockSignals(True)
        self.root_box.setCurrentIndex(self._custom_scope_index())
        self.root_box.blockSignals(False)
        self.root_box.setToolTip("扫描范围：\n" + path)
        self._update_roots_hint()
        return True

    def _preset_index(self) -> int:
        return getattr(self, "_custom_index", 0)

    # ---------------------------------------------------------- 类型

    def _on_type_selected(self, key: str) -> None:
        """类型胶囊：一个控件同时决定「当前列表过滤」和「下次扫描范围」。

        原来是两个控件干同一件事 —— 上面一个「扫描类型」下拉负责扫描范围，
        下面一排胶囊负责结果过滤，8 个类型写了两遍。用户看到的是「这两排
        一模一样，到底该点哪个」。现在合并：点胶囊立刻过滤当前结果，
        再点「开始扫描」就只扫这一类。
        """
        self._filter = key
        self._scan_kind = key
        chip = self._chips.get(key)
        try:
            if chip is not None and not chip.isChecked():
                chip.setChecked(True)
        except RuntimeError:
            # 页面已被换肤重建，这条点击落在已销毁的控件上，忽略即可
            return
        self._apply_filter()

        # 切类型时把体积门槛调到这一类文件合理的大小：图片、音频还卡在
        # 200 MB 的话，等于什么都看不到。用户仍可手动改。
        self.size_box.setCurrentIndex(_size_index(bigfile.suggest_min_mb(key)))
        size_text = SIZES[self.size_box.currentIndex()][1]
        if self._files:
            self.lbl_stat.setText(
                f"类型：{TYPE_LABEL.get(key, key)} · 阈值 {size_text}"
                " · 点「开始扫描」只扫这一类"
            )
        else:
            self._update_roots_hint()

    def _kind_tooltip(self, key: str) -> str:
        if key == "all":
            return "扫描全部类型，只按体积筛"
        if key == "installer":
            return (
                "只扫安装包：msi / iso / wim / appx 等，"
                "exe 只收文件名像安装程序的（setup / install / 安装 / 更新…），"
                "普通程序本体不算"
            )
        exts = bigfile.extensions_for(key)
        shown = " ".join(exts[:12])
        tail = f" 等 {len(exts)} 种格式" if len(exts) > 12 else ""
        return f"只扫{bigfile.CATEGORY_LABEL[key]}：{shown}{tail}"

    def _sync_type_chips(self) -> None:
        """扫描结果回来后，把胶囊恢复成这次实际扫的类型。

        换肤会重建页面，不恢复的话：明明按「视频」扫的，重建后胶囊停回「全部」。
        """
        key = self._scan_kind if self._scan_kind in self._chips else "all"
        try:
            self._chips[key].setChecked(True)
        except RuntimeError:
            return
        self._filter = "all"          # 结果本身已经只含这一类，不需要再过滤
        self._apply_filter()

    # ---------------------------------------------------------- 扫描

    def start_scan(self) -> None:
        if self.tasks.is_busy("bigfile"):
            return
        kind_key = self._scan_kind
        self._just_cleaned = False
        self._files = []
        self._rows = []
        self._checked.clear()
        self.table.setRowCount(0)
        self.lbl_total.setText("")
        kind_text = "" if kind_key == "all" else bigfile.CATEGORY_LABEL[kind_key]
        self.lbl_stat.setText(f"正在扫描{kind_text}…")
        self.btn_scan.setEnabled(False)
        self.btn_stop.show()
        self._sync_selection()

        min_size = SIZES[self.size_box.currentIndex()][0] * 1024 * 1024
        self.tasks.submit(
            "bigfile",
            "扫描大文件",
            self._make_task(list(self._roots), min_size, kind_key),
        )

    def _make_task(self, roots: list[str], min_size: int, kind_key: str = "all"):
        def run(report, should_stop):
            kind_text = "" if kind_key == "all" else bigfile.CATEGORY_LABEL[kind_key]
            kinds = None if kind_key == "all" else [kind_key]
            result = bigfile.scan_ex(
                roots,
                min_size,
                limit=LIMIT,
                kinds=kinds,
                should_stop=should_stop,
                on_progress=lambda seen, found: report(
                    0,
                    f"正在扫描{kind_text} · 已走过 {seen:,} 个文件 · 命中 {found} 个",
                ),
            )
            if should_stop():
                return None
            return BigFileState(
                files=result.files,
                total=sum(f.size for f in result.files),
                scanned_at=time.time(),
                kinds=[kind_key],
                min_size=min_size,
                truncated=result.truncated,
            )

        return run

    def _stop_scan(self) -> None:
        if self.tasks.is_busy("bigfile"):
            self.tasks.cancel("bigfile")
            self.lbl_stat.setText("正在停止…")
        elif self.tasks.is_busy("bigfile_clean"):
            self.tasks.cancel("bigfile_clean")
            self.lbl_stat.setText("正在停止删除…")

    def _on_task_progress(self, task_id: str, _percent: int, text: str) -> None:
        if task_id in ("bigfile", "bigfile_clean") and text:
            self.lbl_stat.setText(text)

    def _on_task_finished(self, task_id: str, result) -> None:
        if task_id == "bigfile":
            self.btn_scan.setEnabled(True)
            self.btn_stop.hide()
            return
        if task_id != "bigfile_clean":
            return

        self.btn_stop.hide()
        if result is None:
            self.lbl_stat.setText("已取消删除")
            return
        deleted, failed, freed, remaining = result
        self._just_cleaned = True
        self.store.set_bigfile_files(remaining)
        self._checked &= {f.path for f in remaining}
        parts = [f"已删除 {deleted} 个文件，释放 {fmt_bytes(freed)}"]
        if failed:
            parts.append(f"{failed} 个被占用或无权限")
        self.lbl_stat.setText(" · ".join(parts))
        if failed:
            QMessageBox.information(
                self,
                "部分文件没删掉",
                f"{failed} 个文件没能送进回收站，通常是正在被程序使用，"
                "或所在目录没有权限。关闭相关程序后可以重试。",
            )

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id == "bigfile":
            self.btn_scan.setEnabled(True)
            self.btn_stop.hide()
            self.lbl_stat.setText("已取消")
        elif task_id == "bigfile_clean":
            self.btn_stop.hide()
            self.lbl_stat.setText("已取消删除")

    def shutdown(self) -> None:
        self.tasks.cancel_many("bigfile", "bigfile_clean")

    def _render_bigfile(self) -> None:
        state = self.store.bigfile
        self._files = list(state.files)
        self._checked &= {f.path for f in self._files}
        self.btn_scan.setEnabled(True)
        self.btn_stop.hide()

        # 恢复这次扫描用的类型与阈值。换肤会重建页面，不恢复的话
        # 明明按「视频」扫的，重建后就变回「全部类型 + 200 MB」。
        kind = (state.kinds or ["all"])[0] if state.kinds else "all"
        if kind not in bigfile.CATEGORY_LABEL and kind != "all":
            kind = "all"
        self._scan_kind = kind
        if state.min_size:
            self.size_box.setCurrentIndex(
                _size_index(max(1, round(state.min_size / 1048576)))
            )
        self._refresh_chips()
        self._sync_type_chips()
        if self._files:
            stamp = (
                time.strftime("%H:%M", time.localtime(state.scanned_at))
                if state.scanned_at
                else ""
            )
            parts = [f"扫描完成 · {stamp}" if stamp else "扫描完成"]
            if not state.kinds or state.kinds[0] == "all":
                parts.append(f"{len(self._files)} 个")
            else:
                parts.append(f"{bigfile.CATEGORY_LABEL[kind]} {len(self._files)} 个")
            if state.truncated:
                parts.append(f"结果已截到体积最大的 {LIMIT} 个")
            self.lbl_stat.setText(" · ".join(parts))
        else:
            # 「为什么是空的」交给表格中间那行提示说，这里只报时间，
            # 两处写同一句话会显得啰嗦。
            stamp = (
                time.strftime("%H:%M", time.localtime(state.scanned_at))
                if state.scanned_at
                else ""
            )
            self.lbl_stat.setText(f"扫描完成 · {stamp}" if stamp else "扫描完成")

    # ---------------------------------------------------------- 渲染

    def _refresh_chips(self) -> None:
        summary = bigfile.summarize(self._files)
        total_count = len(self._files)
        total_size = sum(f.size for f in self._files)
        self._chips["all"].setText(f"全部 {total_count}" if total_count else "全部")
        self._chips["all"].setToolTip(
            f"全部类型：{total_count} 个 · {fmt_bytes(total_size)}"
            if total_count
            else "还没有扫描结果"
        )
        for key, text in bigfile.CATEGORIES:
            count, size = summary.get(key, (0, 0))
            chip = self._chips[key]
            chip.setText(f"{text} {count}" if count else text)
            chip.setToolTip(
                f"{text}：{count} 个 · {fmt_bytes(size)}" if count else f"没有{text}文件"
            )

    def _apply_filter(self) -> None:
        if self._filter == "all":
            self._rows = list(self._files)
        else:
            self._rows = [f for f in self._files if f.category == self._filter]
        self._sort_rows()

    # ---------------------------------------------------------- 排序

    def _sort_rows(self) -> None:
        key = {
            1: lambda f: f.name.lower(),
            2: lambda f: f.category_label,
            3: lambda f: f.size,
            4: lambda f: f.mtime,
            5: lambda f: f.folder.lower(),
        }.get(self._sort_col)
        if key is not None:
            self._rows.sort(key=key, reverse=self._sort_desc)
        shown = sum(f.size for f in self._rows)
        # 「↓」在 10px 下渲染成一根竖线，用中文说清序向
        order = "降序" if self._sort_desc else "升序"
        self.lbl_sort.setText(f"按{SORT_NAMES[self._sort_col]} · {order}")
        self.lbl_total.setText(
            f"当前 {len(self._rows)} 个 · {fmt_bytes(shown)}" if self._rows else ""
        )
        self._fill_table()

    def _sort_by(self, col: int) -> None:
        if col not in SORT_NAMES:
            return
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            # 名字/类型/目录升序顺手，体积/时间降序才看得见重点
            self._sort_desc = col in (3, 4)
        self._sort_rows()

    def _fill_table(self) -> None:
        self._filling = True
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._rows))

        for index, item in enumerate(self._rows):
            check = QTableWidgetItem()
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            # 系统文件不给勾：勾了也只能得到「删除失败」，不如一开始就说清楚
            if not item.locked:
                flags |= Qt.ItemIsUserCheckable
            check.setFlags(flags)
            check.setCheckState(
                Qt.Checked
                if item.path in self._checked and not item.locked
                else Qt.Unchecked
            )
            self.table.setItem(index, 0, check)

            # 名字后面直接写「系统文件」：光靠复选框不同（Qt 的勾选框禁用了
            # 也还是那个方框）和字色变浅，用户根本看不出这行不能选
            name_item = QTableWidgetItem(
                f"{item.name}（系统文件）" if item.locked else item.name
            )
            name_item.setFont(font(12))
            if item.locked:
                name_item.setForeground(QColor(T.TEXT_DIM))
                name_item.setToolTip(
                    f"{item.path}\n系统文件，删除会影响休眠/分页，"
                    "要处理请到「系统瘦身」"
                )
            else:
                name_item.setToolTip(f"{item.path}\n类型：{item.category_label}")
            self.table.setItem(index, 1, name_item)

            type_item = QTableWidgetItem(
                "系统文件" if item.locked else item.category_label
            )
            type_item.setFont(font(11))
            type_item.setForeground(QColor(T.TEXT_DIM))
            type_item.setToolTip(
                f".{item.ext}" if item.ext else item.category_label
            )
            self.table.setItem(index, 2, type_item)

            size_item = QTableWidgetItem(fmt_bytes(item.size))
            size_item.setFont(font(11))
            size_item.setForeground(QColor(T.AMBER))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(index, 3, size_item)

            date_item = QTableWidgetItem(
                time.strftime("%Y-%m-%d %H:%M", time.localtime(item.mtime))
            )
            date_item.setFont(font(11))
            date_item.setForeground(QColor(T.TEXT_DIM))
            self.table.setItem(index, 4, date_item)

            folder_item = QTableWidgetItem(item.folder)
            folder_item.setFont(font(11))
            folder_item.setForeground(QColor(T.TEXT_SUB))
            folder_item.setToolTip(item.folder)
            self.table.setItem(index, 5, folder_item)

        self.table.setUpdatesEnabled(True)
        self._filling = False
        self._sync_selection()
        self._sync_empty_hint()

    def _threshold_text(self) -> str:
        return SIZES[self.size_box.currentIndex()][1][2:]

    def _sync_empty_hint(self) -> None:
        if getattr(self, "empty", None) is None:
            return
        if self._rows:
            self.empty.hide()
            return
        if not self.store.bigfile.ready:
            # 还没扫过，别写成「没有找到」，那会让人以为扫过了
            self.empty.setText("选好扫描范围和类型，点「开始扫描」")
        elif not self._files and self._just_cleaned:
            self.empty.setText("选中的文件已经处理完了\n可以换个范围或类型继续扫描")
        elif not self._files:
            name = (
                "文件"
                if self._scan_kind == "all"
                else bigfile.CATEGORY_LABEL.get(self._scan_kind, "文件")
            )
            self.empty.setText(
                f"这个范围里没有超过 {self._threshold_text()} 的{name}\n"
                "可以降低体积阈值，或换个扫描类型再试"
            )
        else:
            name = bigfile.CATEGORY_LABEL.get(self._filter, "这一类")
            self.empty.setText(f"「{name}」下没有符合条件的文件")
        self._place_empty()
        self.empty.show()

    def _place_empty(self) -> None:
        label = getattr(self, "empty", None)
        if label is None:
            return
        top = self.table.horizontalHeader().height()
        label.setGeometry(
            8, top, max(0, self.table.width() - 16), max(0, self.table.height() - top)
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_empty()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 0:
            return
        row = item.row()
        if not (0 <= row < len(self._rows)):
            return
        path = self._rows[row].path
        if item.checkState() == Qt.Checked:
            self._checked.add(path)
        else:
            self._checked.discard(path)
        self._sync_selection()

    # ---------------------------------------------------------- 选择

    def _check_all(self) -> None:
        self._checked |= {f.path for f in self._rows if not f.locked}
        self._sync_checkboxes()

    def _invert(self) -> None:
        shown = {f.path for f in self._rows if not f.locked}
        self._checked = (self._checked - shown) | (shown - self._checked)
        self._sync_checkboxes()

    def _sync_checkboxes(self) -> None:
        self._filling = True
        for row, item in enumerate(self._rows):
            cell = self.table.item(row, 0)
            if cell is None:
                continue
            cell.setCheckState(
                Qt.Checked if item.path in self._checked else Qt.Unchecked
            )
        self._filling = False
        self._sync_selection()

    def _sync_selection(self) -> None:
        chosen = [f for f in self._files if f.path in self._checked and not f.locked]
        size = sum(f.size for f in chosen)
        self.lbl_sel.setText(
            f"已选 {len(chosen)} 项 · {fmt_bytes(size)}" if chosen else "已选 0 项"
        )
        self.btn_delete.setEnabled(bool(chosen))
        self.btn_delete.setText(
            f"删除到回收站（{len(chosen)}）" if chosen else "删除到回收站"
        )

    # ---------------------------------------------------------- 删除

    def delete_checked(self) -> None:
        chosen = [f for f in self._files if f.path in self._checked and not f.locked]
        if not chosen:
            return
        total = sum(f.size for f in chosen)
        # 程序目录里的文件在安全层就被拦下，删不掉的会算进「失败」。
        # 与其让用户看到「失败 6 个」以为自己搞坏了什么，不如提前说清楚。
        blocked = [f for f in chosen if safety.is_protected(f.path)]
        extra = ""
        if blocked:
            extra = (
                f"\n\n其中 {len(blocked)} 个位于系统或程序目录，会被自动跳过"
                "（这些位置删了会让软件无法运行）。"
            )
        answer = QMessageBox.question(
            self,
            "删除到回收站",
            f"把选中的 {len(chosen)} 个文件（合计 {fmt_bytes(total)}）送进回收站？\n\n"
            f"这是系统回收站，误删可以从回收站还原。{extra}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self.tasks.is_busy("bigfile_clean"):
            return
        self.btn_delete.setEnabled(False)
        self.btn_stop.show()
        self.lbl_stat.setText("正在删除…")
        self.tasks.submit(
            "bigfile_clean", "删除大文件", self._make_delete_task(chosen)
        )

    def _make_delete_task(self, files: list[bigfile.BigFile]):
        def run(report, should_stop):
            def on_progress(done: int, total: int, freed: int) -> None:
                report(
                    int(done * 100 / max(1, total)),
                    f"已处理 {done}/{total} 个 · 释放 {fmt_bytes(freed)}",
                )

            return bigfile.delete_to_trash(
                files, should_stop=should_stop, on_progress=on_progress
            )

        return run

    # ---------------------------------------------------------- 右键菜单

    def _file_at(self, row: int) -> bigfile.BigFile | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def _show_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        item = self._file_at(row)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("打开文件位置").triggered.connect(
            lambda: reveal_file(item.path)
        )
        menu.addAction("复制路径").triggered.connect(lambda: copy_text(item.path))
        menu.addSeparator()
        action = menu.addAction("只看这个目录")
        action.triggered.connect(lambda: self._filter_folder(item.folder))
        menu.addSeparator()
        menu.addAction("删除到回收站").triggered.connect(lambda: self._recycle_one(item))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _filter_folder(self, folder: str) -> None:
        """把列表临时限制在这个目录下，方便成堆清理（比如一个游戏目录）。"""
        self._filter = "all"
        self._chips["all"].setChecked(True)
        matched = [f for f in self._files if f.folder.lower().startswith(folder.lower())]
        self._rows = matched
        self._sort_rows()
        self.lbl_total.setText(f"{folder} · {len(matched)} 个")

    def _recycle_one(self, item: bigfile.BigFile) -> None:
        answer = QMessageBox.question(
            self,
            "删除到回收站",
            f"把 {item.name}（{fmt_bytes(item.size)}）送进回收站？\n\n{item.path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        ok, _failed = trash.recycle([item.path])
        if ok:
            remaining = [f for f in self._files if f.path != item.path]
            self.store.set_bigfile_files(remaining)
            self._checked.discard(item.path)
            self.lbl_stat.setText("已送进回收站")
        else:
            QMessageBox.warning(self, "删除失败", "文件可能正在被使用，或没有权限。")
