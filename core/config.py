"""配置持久化与开机自启管理。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "MemoryKeeper"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = config_dir() / "config.json"


@dataclass
class Settings:
    interval_ms: int = 1000
    process_interval_ms: int = 3000
    auto_optimize: bool = False
    auto_threshold: int = 85
    whitelist: list[str] = field(default_factory=list)
    start_minimized: bool = False
    minimize_to_tray: bool = True
    autostart: bool = False
    deep_mode: bool = False
    # "system" / "dark" / "light"，旧的皮肤名会自动映射
    theme: str = "system"
    # 空字符串表示跟随 Windows 系统强调色
    accent: str = ""
    # 告警
    alerts_enabled: bool = True
    alert_sound: bool = True
    alert_cpu: int = 90
    alert_cpu_seconds: int = 60
    alert_memory: int = 90
    alert_disk_free_gb: float = 5.0
    # 统计
    total_freed: int = 0
    optimize_count: int = 0
    last_freed: int = 0
    last_optimize_ts: float = 0.0

    def save(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    @classmethod
    def load(cls) -> "Settings":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        # dataclass 不做类型校验，字段名对就照单全收：「interval_ms": "1000"
        # 也能进来，最后崩在 QTimer.setInterval 里，表现成「程序打不开」。
        # 这里按默认值的类型逐个过一遍，类型不对就用默认值 —— 配置文件坏掉
        # 只应该让某一项回到默认，不该让程序起不来。
        default = cls()
        values = {}
        for name, value in raw.items():
            base = getattr(default, name, None)
            if isinstance(base, bool):
                values[name] = bool(value)
            elif isinstance(base, int):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[name] = int(value)
            elif isinstance(base, float):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[name] = float(value)
            elif isinstance(base, str):
                if isinstance(value, str):
                    values[name] = value
            elif isinstance(base, list) and isinstance(value, list):
                values[name] = [str(item) for item in value]
        return cls(**values)


# ---------------------------------------------------------------- 开机自启


def _launch_command() -> str:
    """构造开机自启要执行的命令。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    script = Path(sys.argv[0]).resolve()
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{script}" --minimized'


def set_autostart(enabled: bool) -> bool:
    """写入 / 删除 HKCU 下的 Run 项。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, APP_NAME, 0, winreg.REG_SZ, _launch_command()
                )
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def is_autostart_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False
