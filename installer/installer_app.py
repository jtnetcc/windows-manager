"""安装程序（Windows）。

不是第三方安装包的封装，而是一个**自带界面的安装器**：用 PyInstaller 打成
一个 exe，里面塞着真正的程序（payload/app.exe）和卸载器。

为什么自己做而不是用 Inno Setup / NSIS：

- 这台机器和用户的机器上都没有那两个工具链，装一次要管理员权限，
  发版脚本就没法「一条命令出包」；
- 我们只需要四件事：复制文件、建快捷方式、写卸载登记、留一个卸载器。
  Qt 已经在依赖里，界面不用额外学一套脚本语言；
- 全程**按用户安装**（%LOCALAPPDATA%\\Programs），不触发 UAC，装完就能用。

产物：Windows系统管家-v{版本}-setup.exe
静默安装：setup.exe /S            装到默认目录
          setup.exe /S /D=D:\\Tools\\wm   指定目录
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

APP_ID = "Windows系统管家"
PUBLISHER = "jtnetcc"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"


# ---------------------------------------------------------------- 路径


def payload_dir() -> Path:
    """PyInstaller 解包目录；源码运行时是脚本旁边的 payload/。"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "payload" if base else Path(__file__).parent / "payload"


def manifest() -> dict:
    try:
        return json.loads((payload_dir() / "manifest.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {"name": APP_ID, "version": "0.0.0", "exe": f"{APP_ID}.exe"}


def default_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Programs" / APP_ID


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


# ---------------------------------------------------------------- 安装动作


def make_shortcut(link: Path, target: Path, desc: str) -> bool:
    """用 WScript.Shell 建 .lnk。

    不引 pywin32：为一行快捷方式多一个依赖不值得。路径里的单引号在 PowerShell
    里要用两个单引号转义，这里统一处理。
    """
    def q(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut({q(str(link))}); "
        f"$s.TargetPath = {q(str(target))}; "
        f"$s.WorkingDirectory = {q(str(target.parent))}; "
        f"$s.IconLocation = {q(str(target))} + ',0'; "
        f"$s.Description = {q(desc)}; "
        "$s.Save()"
    )
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return link.exists() or result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def write_registry(target: Path) -> None:
    data_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "MemoryKeeper"
    info = manifest()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        values = {
            "DisplayName": APP_ID,
            "DisplayVersion": str(info.get("version", "")),
            "Publisher": PUBLISHER,
            "DisplayIcon": str(target / info.get("exe", f"{APP_ID}.exe")),
            "InstallLocation": str(target),
            "UninstallString": f'"{target / "uninstall.exe"}"',
            "QuietUninstallString": f'"{target / "uninstall.exe"}" /S',
            "InstallDate": __import__("time").strftime("%Y%m%d"),
            "NoModify": 1,
            "NoRepair": 1,
            "EstimatedSize": max(1, int(_tree_size(target) / 1024)),
        }
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ if isinstance(value, str) else winreg.REG_DWORD, value)
    _ = data_dir  # 数据目录由程序自己管理，这里只登记卸载信息


def _tree_size(root: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                pass
    return total


def install(target: Path, desktop: bool, report) -> None:
    info = manifest()
    exe_name = info.get("exe", f"{APP_ID}.exe")

    report(10, "准备目录…")
    target.mkdir(parents=True, exist_ok=True)

    running = target / exe_name
    if running.exists():
        # 程序正在跑的话文件是锁的，先让用户关掉，别装到一半报「拒绝访问」
        try:
            running.rename(running)
        except OSError:
            raise RuntimeError("程序正在运行，请先退出再安装。") from None

    report(30, "复制程序文件…")
    for item in payload_dir().iterdir():
        if item.name in ("manifest.json", "icon.png"):
            continue
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    report(60, "创建快捷方式…")
    make_shortcut(start_menu_dir() / f"{APP_ID}.lnk", target / exe_name, APP_ID)
    make_shortcut(
        start_menu_dir() / f"卸载 {APP_ID}.lnk", target / "uninstall.exe", "卸载"
    )
    if desktop:
        make_shortcut(desktop_dir() / f"{APP_ID}.lnk", target / exe_name, APP_ID)
    else:
        stale = desktop_dir() / f"{APP_ID}.lnk"
        if stale.exists():
            stale.unlink(missing_ok=True)

    report(80, "登记卸载信息…")
    write_registry(target)

    report(100, "完成")


# ---------------------------------------------------------------- 界面


class SetupWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.info = manifest()
        self.setWindowTitle(f"安装 {APP_ID}")
        self.setFixedSize(560, 380)
        self.setWindowIcon(QIcon(str(payload_dir() / "icon.png")))
        # 安装器不引 ui/ 那一套主题（那是主程序的东西，也不该被拖进来），
        # 但字体必须显式指定：Qt 的默认字体在部分环境里选不到中文字形，
        # 整个界面会变成一排方框。
        self.setStyleSheet(
            """
            QWidget { background: #F7FAFA; color: #17201F;
                      font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
            QLabel { background: transparent; }
            QLineEdit { background: #FFFFFF; border: 1px solid #E1E9E9;
                        border-radius: 8px; padding: 0 10px; min-height: 30px; }
            QPushButton { background: #FFFFFF; border: 1px solid #E1E9E9;
                          border-radius: 8px; min-height: 30px; padding: 0 16px; }
            QPushButton:hover { background: #EDF4F4; }
            QPushButton#Install { background: #17877A; border: 1px solid #17877A;
                                  color: #FFFFFF; font-weight: 600; }
            QPushButton#Install:hover { background: #126F65; border-color: #126F65; }
            QPushButton#Install:disabled { background: #E7EFEF; color: #94A1A3; border-color: transparent; }
            QCheckBox { background: transparent; spacing: 8px; }
            QProgressBar { background: #E3EAEA; border: none; border-radius: 3px; }
            QProgressBar::chunk { background: #17877A; border-radius: 3px; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        icon = QLabel()
        pix = QPixmap(str(payload_dir() / "icon.png"))
        if not pix.isNull():
            icon.setPixmap(pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(icon, 0, Qt.AlignTop)

        title = QVBoxLayout()
        title.setSpacing(2)
        name = QLabel(f"安装 {APP_ID}")
        name.setFont(self.font())
        name.setStyleSheet("font-size:18px;font-weight:600;")
        sub = QLabel(f"版本 {self.info.get('version', '')} · 免费开源 · 只在本机运行")
        sub.setStyleSheet("color:#5b6668;font-size:12px;")
        title.addWidget(name)
        title.addWidget(sub)
        head.addLayout(title, 1)
        root.addLayout(head)

        root.addSpacing(4)
        root.addWidget(QLabel("安装位置"))
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.edit_path = QLineEdit(str(default_dir()))
        browse = QPushButton("浏览…")
        browse.setFixedWidth(84)
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.edit_path, 1)
        path_row.addWidget(browse, 0)
        root.addLayout(path_row)

        self.chk_desktop = QCheckBox("在桌面创建快捷方式")
        self.chk_desktop.setChecked(True)
        root.addWidget(self.chk_desktop)

        self.lbl_state = QLabel("装完可直接从开始菜单启动，不需要管理员权限。")
        self.lbl_state.setStyleSheet("color:#5b6668;font-size:12px;")
        self.lbl_state.setWordWrap(True)
        root.addWidget(self.lbl_state)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        root.addWidget(self.bar)
        root.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(88)
        cancel.clicked.connect(self.close)
        self.btn_install = QPushButton("安装")
        self.btn_install.setFixedWidth(96)
        self.btn_install.setObjectName("Install")
        self.btn_install.setDefault(True)
        self.btn_install.clicked.connect(self._run)
        actions.addWidget(cancel)
        actions.addWidget(self.btn_install)
        root.addLayout(actions)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择安装位置", self.edit_path.text())
        if chosen:
            self.edit_path.setText(str(Path(chosen) / APP_ID))

    def _report(self, percent: int, text: str) -> None:
        self.bar.setValue(percent)
        self.lbl_state.setText(text)
        QApplication.processEvents()

    def _run(self) -> None:
        target = Path(self.edit_path.text().strip() or str(default_dir()))
        self.btn_install.setEnabled(False)
        try:
            install(target, self.chk_desktop.isChecked(), self._report)
        except Exception as exc:  # noqa: BLE001 — 安装失败要说人话
            self.lbl_state.setText(f"安装失败：{exc}")
            self.btn_install.setEnabled(True)
            return

        self.lbl_state.setText(f"已安装到 {target}")
        self.btn_install.setText("完成")
        self.btn_install.setEnabled(True)
        try:
            self.btn_install.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.btn_install.clicked.connect(self.close)
        # 装完直接启动：多数人装完就想看见东西
        try:
            os.startfile(str(target / self.info.get("exe", f"{APP_ID}.exe")))  # noqa: S606
        except OSError:
            pass


def silent_install(argv: list[str]) -> int:
    """静默安装：/S 或 --silent，可用 /D=<目录> 或 --dir=<目录> 指定位置。"""
    target = default_dir()
    for arg in argv:
        for prefix in ("/D=", "--dir="):
            if arg.startswith(prefix):
                target = Path(arg[len(prefix):])
    try:
        install(target, True, lambda *_a: None)
    except Exception as exc:  # noqa: BLE001
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1
    print(f"已安装到 {target}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "/S" in argv or "--silent" in argv:
        return silent_install(argv)

    app = QApplication(sys.argv)
    window = SetupWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
