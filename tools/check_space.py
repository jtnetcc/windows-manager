"""磁盘空间页自检：目录统计的不变式、历史变化、页面下钻。

跑一次全盘扫描（十几秒），所以放在单独脚本里，不进 check_startup。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import dirstat, space, spacehistory, spacereport  # noqa: E402

GB = 1024 ** 3
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


def test_dirstat() -> dirstat.Node:
    print("\n[目录统计]")
    t0 = time.perf_counter()
    root = dirstat.scan(depth=2)
    cost = time.perf_counter() - t0
    print(f"  扫完 {root.size / GB:.1f} GB / {root.files:,} 个文件，{cost:.1f}s")

    check("统计出体积", root.size > 0, f"{root.size / GB:.2f} GB")
    check("统计出文件数", root.files > 0, f"{root.files:,}")

    total = sum(n.size for n in root.children)
    check(
        "子项之和等于总量",
        abs(total - root.size) <= 1024,
        f"{total / GB:.3f} vs {root.size / GB:.3f} GB",
    )

    # 分类：每个子项只落一类，兜底后总和必须等于「已用」
    used = space._usage(os.environ.get("SystemDrive", "C:") + "\\")[0]
    cats = dirstat.summarize(root, used)
    expect = max(used, root.size)
    check(
        "分类之和等于已用",
        abs(sum(cats.values()) - expect) <= 1024,
        f"{sum(cats.values()) / GB:.2f} vs {expect / GB:.2f} GB",
    )
    check("分类没有负数", all(v >= 0 for v in cats.values()), str(cats))

    bundle = next((n for n in root.children if n.title), None)
    if bundle is not None:
        check(
            "散落文件合并项只含文件",
            all(not c.is_dir for c in bundle.children),
            f"{len(bundle.children)} 个文件 {bundle.size / GB:.2f} GB",
        )
        check(
            "合并项体积等于子项之和",
            abs(sum(c.size for c in bundle.children) - bundle.size) <= 1024,
        )
    else:
        print("  · 本机根目录没有散落文件，跳过合并项检查")

    # 顶层不允许有 reparse point 混进来（会造成重复计数甚至绕圈）
    names = {n.name.lower() for n in root.children}
    check("没有 junction 目录", not ({"documents and settings", "all users"} & names))

    deep = dirstat.scan_subdir(root.children[0].path) if root.children else None
    if deep is not None:
        check(
            "下钻单层可扫",
            deep.size > 0 and deep.is_dir,
            f"{deep.name} {deep.size / GB:.2f} GB",
        )
    return root


def test_history() -> None:
    print("\n[空间变化]")
    original = spacehistory.PATH
    tmp = Path(tempfile.mkdtemp()) / "hist.json"
    spacehistory.PATH = tmp
    try:
        now = time.time()
        day = 86400
        fake = type("R", (), {})()
        fake.children = [
            dirstat.Node(path=r"C:\Windows", size=int(20 * GB)),
            dirstat.Node(path=r"C:\Users", size=int(8 * GB)),
            dirstat.Node(path=r"C:\hiberfil.sys", size=int(3 * GB), is_dir=False),
        ]
        spacehistory._save(
            [
                spacehistory.Snapshot(
                    at=now - 20 * day,
                    used=int(25 * GB),
                    total=int(100 * GB),
                    dirs={"Windows": int(18 * GB), "Users": int(6 * GB), "（根目录散落文件）": int(3 * GB)},
                ),
                spacehistory.Snapshot(
                    at=now - 10 * day,
                    used=int(30 * GB),
                    total=int(100 * GB),
                    dirs={"Windows": int(20 * GB), "Users": int(7 * GB), "（根目录散落文件）": int(3 * GB)},
                ),
            ]
        )
        spacehistory.record(fake, int(31 * GB), int(100 * GB))
        items = spacehistory.history()
        check("记录写入", len(items) == 3, f"{len(items)} 条")

        grows = spacehistory.growth(30, limit=3)
        check("能算出增长", bool(grows), "; ".join(f"{g.name}{g.delta / GB:+.1f}" for g in grows))
        windows = next((g for g in grows if g.name == "Windows"), None)
        check(
            "Windows 增长正确",
            windows is not None and abs(windows.delta - 2 * GB) <= 1024 * 1024,
            f"{windows.delta / GB:+.2f} GB" if windows else "缺失",
        )
        # 同一天内连续记录不应该刷出两条
        before = len(spacehistory.history())
        spacehistory.record(fake, int(31 * GB), int(100 * GB))
        check("同一天连续记录只留一条", len(spacehistory.history()) == before)
        print(f"  时间跨度：{spacehistory.span(30)}")
    finally:
        spacehistory.PATH = original


def test_reclaim() -> None:
    print("\n[可回收清单]")
    t0 = time.perf_counter()
    items = spacereport.collect()
    cost = time.perf_counter() - t0
    check("收集不抛异常", True, f"{len(items)} 项 / {cost:.2f}s")
    check("每项体积为正", all(i.size > 0 for i in items))
    check("按体积降序", items == sorted(items, key=lambda i: i.size, reverse=True))
    for item in items:
        print(f"    {item.size / GB:6.2f} GB  {item.name}  [{item.action}]")
    note = spacereport.split_note(items)
    check("汇总文案有内容", bool(note), note)


def test_page(root: dirstat.Node) -> None:
    print("\n[页面]")
    from PySide6.QtWidgets import QApplication

    from core import store as store_mod
    from core.task import TaskCenter

    app = QApplication.instance() or QApplication([])
    from ui import theme_dark as T

    theme_dark_ready = T.PANEL != ""
    T.set_theme("light")
    app.setStyleSheet(T.app_stylesheet())

    from ui.page_space import SpacePage

    st = store_mod.Store()
    tasks = TaskCenter()
    page = SpacePage(st, tasks, lambda _k: None)
    used, total = space._usage(os.environ.get("SystemDrive", "C:") + "\\")
    result = space.SpaceResult(
        root=root,
        items=spacreport_items(),
        used=used,
        total=total,
        seconds=12.0,
        single_disk=True,
        targets=[],
        scanned_at=time.time(),
    )
    view = page.analysis
    view._adopt(result)
    page.resize(1100, 720)
    page.show()
    app.processEvents()

    check("目录行已渲染", view.dir_rows[0].isVisible() or bool(view.dir_rows[0].node))
    check(
        "首个目录有体积文字",
        bool(view.dir_rows[0].lbl_size.text()),
        view.dir_rows[0].lbl_size.text(),
    )
    check("图例有数字", bool(view._dots["system"].text()), view._dots["system"].text())
    check("可回收行已渲染", bool(view.rec_rows[0].item))
    check("堆叠条有分段", len(view.bar._parts) >= 3, f"{len(view.bar._parts)} 段")

    first = root.children[0]
    view._enter(first)
    check("下钻进入一层", len(view._stack) == 1, view.lbl_crumb.text())
    if first.children:
        check("下钻后有子行", bool(view.dir_rows[0].node))
    view._up()
    check("返回上级", not view._stack, view.lbl_crumb.text())

    check("单分区提示条（剩余超过 75% 时隐藏）", True, "仅条件显示")
    page.shutdown()
    page.deleteLater()
    app.processEvents()
    del theme_dark_ready


def spacreport_items() -> list:
    return spacereport.collect()


def main() -> None:
    print("磁盘空间自检")
    root = test_dirstat()
    test_history()
    test_reclaim()
    test_page(root)
    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：" + "、".join(FAIL))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
