"""系统瘦身自检：数据层往返 + 页面交互。

只碰能安全回滚的项：
- 开关用 HKCU 的「搜索框网页建议」（改完立刻还原）；
- 需要管理员的项只在非管理员时验证「拒绝」这条路，不真的写；
- 空间项只统计体积，绝不执行删除 —— 删休眠文件、Windows.old 是用户的决定。

用法：python tools/check_slim.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config, health, slim, winmem
from ui import theme_dark
from ui.main_window import MainWindow


def check_core() -> None:
    print("== 数据层 ==")
    keys = [item.key for item in slim.TOGGLES]
    assert len(keys) == len(set(keys)), "开关 key 重复"
    for item in slim.TOGGLES:
        assert item.group in {key for key, _t, _d in slim.GROUPS}, item.key
        assert item.effect, f"{item.key} 缺少影响说明"

    states = slim.states()
    assert set(states) == set(keys)
    print(f"开关 {len(keys)} 项，当前关闭 {slim.disabled_count()} 项")
    print(f"空间项 {len(slim.SPACES)} 项：{', '.join(i.name for i in slim.SPACES)}")

    # HKCU 往返：关 → 读回 False → 开 → 读回 True
    before = slim.BY_KEY["search_web"].read()
    assert slim.apply("search_web", False) == ""
    assert slim.BY_KEY["search_web"].read() is False, "关闭后状态没变"
    assert slim.apply("search_web", True) == ""
    assert slim.BY_KEY["search_web"].read() is True, "恢复后状态没变"
    print(f"search_web 往返正常（原值 {before}）")

    if not winmem.is_admin():
        err = slim.apply("update_auto", False)
        assert "管理员" in err, f"非管理员应提示权限，实际：{err!r}"
        assert slim.BY_KEY["update_auto"].read() is True, "失败后状态被改了"
        print("非管理员时拒绝写入，状态未被改动")

    sizes = slim.scan()
    assert set(sizes) == {item.key for item in slim.SPACES}
    print("体积：", " · ".join(f"{k}={v // 1024 ** 2}MB" for k, v in sizes.items()))

    assert health.FACTOR_PAGE.get("rel") == "slim", "体检页的「可释放系统文件」没接过来"


def check_ui() -> None:
    print("== 页面 ==")
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_theme("dark")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.show()
    window._theme_watch.stop()
    window.switch_page("slim")

    def verify() -> None:
        try:
            _verify()
        finally:
            window._shutdown()
            app.quit()

    def _verify() -> None:
        page = window.page_slim
        assert len(page._rows) == len(slim.TOGGLES), "开关行没建全"
        print(f"页面 {len(page._rows)} 个开关行 / {len(page._spaces)} 个空间行")

        row = page._rows["search_web"]
        assert row.switch.isChecked(), "初始应为开启"
        before = page.lbl_stat.text()

        row.switch.setChecked(False)  # 模拟点开关
        assert not slim.BY_KEY["search_web"].read(), "点击后没写进注册表"
        assert row.lbl_state.text() == "已关闭", row.lbl_state.text()
        after = page.lbl_stat.text()
        assert after != before, "顶部汇总没跟着更新"
        print(f"关闭一项：{before} → {after}")

        row.switch.setChecked(True)
        assert slim.BY_KEY["search_web"].read(), "再打开没恢复"
        assert row.lbl_state.text() == "已开启"
        print("再打开：状态与注册表一致")

        # 空间清理的回写：不真的删文件，直接喂一个结果给回调
        said: list[str] = []
        page.message.connect(said.append)
        space = page._spaces["hibernation"]
        page._pending_space = "hibernation"
        space.set_busy(True)
        page._on_task_finished("slim_space", (200 * 1024 ** 2, "模拟清理完成"))
        assert page._pending_space == "", "清理完 _pending_space 没复位"
        assert not space._busy, "清理完按钮还卡在「处理中」"
        assert said and "200 MB" in said[-1], said
        print("空间清理回写：", said[-1])

        # 休眠文件在任何机器上都该露出来（要么有体积，要么关着能重新开）
        assert not page._spaces["hibernation"].isHidden(), "休眠那行被藏了"
        print(
            "空间行可见性：",
            {k: not r.isHidden() for k, r in page._spaces.items()},
        )
        print("UI 自检通过")

    QTimer.singleShot(2600, verify)
    app.exec()


if __name__ == "__main__":
    check_core()
    check_ui()
