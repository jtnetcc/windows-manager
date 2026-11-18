"""大文件页的选项一致性检查（不联网、不真扫盘）。

验三件事：
1. 类型只有一个控件（胶囊），旧的「扫描类型」下拉和「目录…」按钮已移除；
2. 范围默认「所有磁盘」，且下拉里包含每个本地盘；
3. 点类型胶囊同时改变「当前过滤」与「下次扫描类型」，并调低体积门槛。

用法：python tools/check_bigfile_options.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import bigfile, config  # noqa: E402
from ui import theme_dark  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.page_bigfile import BigFilePage  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())

    window = MainWindow(settings)
    window.switch_page("bigfile")
    app.processEvents()

    pages = [w for w in window.findChildren(BigFilePage)]
    assert pages, "没找到大文件页"
    page = pages[0]

    # 1. 重复控件已移除
    assert not hasattr(page, "kind_box"), "「扫描类型」下拉还在"
    assert not hasattr(page, "btn_dir"), "「目录…」按钮还在"
    assert len(page._chips) == 1 + len(bigfile.CATEGORIES), "类型胶囊数量不对"

    # 2. 范围：默认所有磁盘，且含每个盘符
    assert page.root_box.count() == len(bigfile.scope_items())
    default_text = page.root_box.currentText()
    assert default_text == "所有磁盘", f"默认范围不是所有磁盘：{default_text}"
    drives = bigfile.preset_roots(bigfile.DEFAULT_SCOPE)
    assert drives, "没有枚举到本地磁盘"
    letters = {root[0].upper() for root in drives}
    texts = [page.root_box.itemText(i) for i in range(page.root_box.count())]
    for letter in letters:
        if letter == os.environ.get("SystemDrive", "C:")[0].upper():
            assert any("系统盘" in t for t in texts), "系统盘项缺失"
        else:
            assert any(t.startswith(f"{letter}") for t in texts), f"{letter} 盘项缺失"
    print(f"范围：{default_text}（{len(drives)} 个盘：{', '.join(sorted(letters))}）✓")

    # 3. 类型胶囊：一次点击同时改过滤和扫描类型
    page._chips["video"].click()
    app.processEvents()
    assert page._filter == "video" and page._scan_kind == "video", "胶囊没有同时设置过滤与扫描类型"
    suggested = bigfile.suggest_min_mb("video")
    from ui.page_bigfile import SIZES

    # 阈值下拉只有 1/5/10/50/100/200/500/1024 这几档，30 MB 会落到最近的一档
    nearest = min(SIZES, key=lambda item: abs(item[0] - suggested))[0]
    assert SIZES[page.size_box.currentIndex()][0] == nearest, "切换类型没有调低体积门槛"
    assert SIZES[page.size_box.currentIndex()][0] < 200, "阈值没有降到 200 MB 以下"
    assert all(chip.isEnabled() for chip in page._chips.values()), "胶囊不该再被置灰"
    print(f"类型：点「视频」→ 过滤=video，扫描类型=video，阈值 {suggested} MB ✓")

    # 4. 多根扫描：两个不同目录（尽量跨盘）都要被扫到
    import shutil
    import tempfile

    from core import bigfile as bf

    roots = []
    for base in (tempfile.gettempdir(), "D:////"):
        try:
            folder = Path(tempfile.mkdtemp(prefix="wmt_", dir=base))
        except OSError:
            continue
        payload = folder / "sample.bin"
        payload.write_bytes(b"0" * (2 * 1024 * 1024))
        roots.append(str(folder))

    assert len(roots) >= 1, "没能创建测试目录"
    result = bf.scan_ex(roots, min_size=1 * 1024 * 1024, limit=50)
    hit = {f.name for f in result.files}
    assert "sample.bin" in hit, "多根扫描没有扫到测试文件"
    print(f"多根扫描：{len(roots)} 个目录 → 命中 {len(result.files)} 个文件 ✓")
    for folder in roots:
        shutil.rmtree(folder, ignore_errors=True)

    # 5. 类型扫描时进度要「动」：扩展名筛掉的文件也必须计入进度
    folder = Path(tempfile.mkdtemp(prefix="wmp_"))
    try:
        for i in range(1200):
            (folder / f"f{i}.txt").write_bytes(b"x")
        (folder / "pic.jpg").write_bytes(b"0" * (2 * 1024 * 1024))
        progress: list[int] = []
        bf.scan_ex(
            [str(folder)], 1 * 1024 * 1024, kinds=["image"],
            on_progress=lambda seen, hit: progress.append(seen),
        )
        middle = [s for s in progress if 0 < s < 1201]
        assert middle, f"类型扫描全程没有中间进度：{progress[:5]}"
        print(f"类型扫描进度：中间上报 {len(middle)} 次（{middle[:3]}…）✓")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    window._shutdown()
    app.quit()
    print("全部通过")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
