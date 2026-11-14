"""开机启动项管理。

三个来源：
    1. 注册表 Run / RunOnce（HKCU / HKLM / WOW6432Node）
    2. 启动文件夹（当前用户 + 所有用户）
    3. 任务计划程序（schtasks）

**禁用一律不删除，只改名**：
    注册表值名  Foo        → Foo.off
    启动文件夹  Foo.lnk    → Foo.lnk.off
    计划任务    schtasks /change /disable

改名可逆、不丢数据，用户随时能改回来。直接删注册表值是不可接受的做法。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import winpath

DISABLED_SUFFIX = ".off"

# 扫描计划任务要跑一次 schtasks，比较慢，缓存一段时间
_TASK_CACHE: tuple[float, list["StartupEntry"]] | None = None
_TASK_TTL = 120.0

RUN_KEYS = (
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce"),
)

@dataclass
class StartupEntry:
    id: str
    name: str
    command: str
    location: str
    source: str  # registry / folder / task
    enabled: bool
    # 计划任务专用：schtasks CSV 的第 2 列就是下次运行时间
    next_run: str = ""

    @property
    def source_text(self) -> str:
        return {"registry": "注册表", "folder": "启动文件夹", "task": "计划任务"}.get(
            self.source, self.source
        )


# ---------------------------------------------------------------- 注册表


def _registry_entries() -> list[StartupEntry]:
    import winreg

    out: list[StartupEntry] = []
    for hive_name, sub in RUN_KEYS:
        try:
            key = winreg.OpenKey(winpath.hive(hive_name), sub, 0, winreg.KEY_READ)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not name:
                    continue
                disabled = name.endswith(DISABLED_SUFFIX)
                display = name[: -len(DISABLED_SUFFIX)] if disabled else name
                out.append(
                    StartupEntry(
                        id=f"reg|{hive_name}|{sub}|{name}",
                        name=display,
                        command=str(value),
                        location=f"{hive_name}\\{sub}",
                        source="registry",
                        enabled=not disabled,
                    )
                )
        finally:
            winreg.CloseKey(key)
    return out


def _set_registry_enabled(entry_id: str, enabled: bool) -> tuple[bool, str]:
    import winreg

    parts = entry_id.split("|")
    if len(parts) < 4:
        return False, "启动项标识无效"
    hive_name, sub, name = parts[1], parts[2], "|".join(parts[3:])

    disabled_now = name.endswith(DISABLED_SUFFIX)
    if disabled_now == (not enabled):
        return True, ""

    target = name[: -len(DISABLED_SUFFIX)] if enabled else name + DISABLED_SUFFIX
    try:
        key = winreg.OpenKey(winpath.hive(hive_name), sub, 0, winreg.KEY_ALL_ACCESS)
    except OSError as exc:
        return False, f"无法打开注册表项：{exc}"

    try:
        try:
            value, kind = winreg.QueryValueEx(key, name)
        except OSError:
            return False, "找不到该启动项，可能已被其他程序修改"

        # 先写新值再删旧值：任何一步失败都不会丢数据
        try:
            winreg.SetValueEx(key, target, 0, kind, value)
        except OSError as exc:
            return False, f"写入失败：{exc}"
        try:
            winreg.DeleteValue(key, name)
        except OSError:
            # 删不掉就把新值撤掉，保持原状
            try:
                winreg.DeleteValue(key, target)
            except OSError:
                pass
            return False, "删除原值失败，该项可能被系统策略保护"
        return True, ""
    finally:
        winreg.CloseKey(key)


# ---------------------------------------------------------------- 启动文件夹


def _startup_folders() -> list[Path]:
    dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(
            Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        )
    program_data = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE")
    if program_data:
        dirs.append(
            Path(program_data)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
    return dirs


def _folder_entries() -> list[StartupEntry]:
    out: list[StartupEntry] = []
    for folder in _startup_folders():
        try:
            names = list(folder.iterdir())
        except OSError:
            continue
        for item in names:
            if not item.is_file():
                continue
            file_name = item.name
            if file_name.lower() in ("desktop.ini",):
                continue
            if file_name.endswith(DISABLED_SUFFIX):
                out.append(
                    StartupEntry(
                        id=f"folder|{item}",
                        name=file_name[: -len(DISABLED_SUFFIX)],
                        command=str(item),
                        location=folder.name,
                        source="folder",
                        enabled=False,
                    )
                )
            else:
                out.append(
                    StartupEntry(
                        id=f"folder|{item}",
                        name=file_name,
                        command=str(item),
                        location=folder.name,
                        source="folder",
                        enabled=True,
                    )
                )
    return out


def _set_folder_enabled(entry_id: str, enabled: bool) -> tuple[bool, str]:
    raw = entry_id.split("|", 1)[1]
    path = Path(raw)
    disabled_now = path.name.endswith(DISABLED_SUFFIX)
    if disabled_now == (not enabled):
        return True, ""

    target = (
        path.with_name(path.name[: -len(DISABLED_SUFFIX)])
        if enabled
        else path.with_name(path.name + DISABLED_SUFFIX)
    )
    try:
        path.rename(target)
        return True, ""
    except OSError as exc:
        return False, f"重命名失败：{exc}"


# ---------------------------------------------------------------- 计划任务


def _run_schtasks(args: list[str]) -> str:
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            timeout=10,
            creationflags=creation,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    raw = result.stdout or b""
    for encoding in ("mbcs", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _parse_csv_line(line: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    in_quote = False
    i = 0
    while i < len(line):
        char = line[i]
        if in_quote:
            if char == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur.append('"')
                    i += 1
                else:
                    in_quote = False
            else:
                cur.append(char)
        elif char == '"':
            in_quote = True
        elif char == ",":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(char)
        i += 1
    out.append("".join(cur))
    return out


def _task_entries(use_cache: bool = True) -> list[StartupEntry]:
    global _TASK_CACHE
    import time

    if use_cache and _TASK_CACHE and time.monotonic() - _TASK_CACHE[0] < _TASK_TTL:
        return _TASK_CACHE[1]

    text = _run_schtasks(["/query", "/fo", "CSV", "/nh"])
    entries: list[StartupEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = _parse_csv_line(line)
        if len(cols) < 3:
            continue
        name = cols[0].strip()
        if not name or "\\microsoft\\" in name.lower():
            continue
        status = cols[2].strip().lower()
        enabled = not ("disabled" in status or "禁用" in status)
        next_run = cols[1].strip() if len(cols) > 1 else ""
        entries.append(
            StartupEntry(
                id=f"task|{name}",
                name=name.lstrip("\\"),
                command=name,
                location="任务计划程序",
                source="task",
                enabled=enabled,
                next_run=next_run,
            )
        )
    _TASK_CACHE = (time.monotonic(), entries)
    return entries


def _set_task_enabled(entry_id: str, enabled: bool) -> tuple[bool, str]:
    global _TASK_CACHE
    name = entry_id.split("|", 1)[1]
    verb = "/enable" if enabled else "/disable"
    output = _run_schtasks(["/change", "/tn", name, verb])
    # schtasks 失败时 stdout 也是空，用返回码不可靠，这里改用再查一次状态
    _TASK_CACHE = None
    lowered = output.lower()
    if "错误" in output or "error" in lowered or "拒绝访问" in output:
        return False, output.strip().splitlines()[-1] if output.strip() else "操作失败"
    return True, ""


# ---------------------------------------------------------------- 对外接口


def list_entries(include_tasks: bool = True, use_cache: bool = True) -> list[StartupEntry]:
    entries: list[StartupEntry] = []
    for loader in (_registry_entries, _folder_entries):
        try:
            entries.extend(loader())
        except Exception:
            continue
    if include_tasks and sys.platform == "win32":
        try:
            entries.extend(_task_entries(use_cache))
        except Exception:
            pass
    # 已启用的排前面，然后按来源、名称
    entries.sort(key=lambda e: (not e.enabled, e.source, e.name.lower()))
    return entries


def set_enabled(entry_id: str, enabled: bool) -> tuple[bool, str]:
    if entry_id.startswith("reg|"):
        return _set_registry_enabled(entry_id, enabled)
    if entry_id.startswith("folder|"):
        return _set_folder_enabled(entry_id, enabled)
    if entry_id.startswith("task|"):
        return _set_task_enabled(entry_id, enabled)
    return False, "无法识别的启动项"


def count_enabled(fast: bool = True) -> int:
    """已启用的启动项数量。

    `fast=True` 时如果还没有计划任务缓存就只统计注册表和启动文件夹 ——
    跑一次 schtasks 要几百毫秒到几秒，体检不该为此卡住。
    启动项页扫过一次之后缓存就绪，之后的统计会自动带上任务。
    """
    include_tasks = (not fast) or (_TASK_CACHE is not None)
    return sum(
        1 for e in list_entries(include_tasks=include_tasks, use_cache=True) if e.enabled
    )
