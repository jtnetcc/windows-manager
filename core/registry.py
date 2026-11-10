r"""注册表冗余扫描。

**只读扫描，删除必须显式确认，且删之前一定先导出 .reg 备份。**

判定原则：只报「有明确证据指向的东西不存在」的项，不猜。

| 类型 | 证据 |
|---|---|
| 卸载残留 | `InstallLocation` 目录不存在，或 `UninstallString` 指向的文件不存在 |
| 启动项残留 | `Run` / `RunOnce` 里的命令路径不存在 |
| App Paths | 登记的 exe 不存在 |
| COM 组件 | `InprocServer32` 的 DLL 不存在（且所在目录也不存在） |

故意**不扫**的：
- 文件关联（`Classes\\*\shell`）—— 很多是系统自带或第三方动态注册的，
  删了会导致「打开方式」里少一项，收益低风险高
- 服务、驱动 —— 一个判断失误就可能让设备不工作
- 通配符 / 默认值 —— 语义模糊，误判率高

删除时只删**具体的值**，不删整个键（除了卸载项那种整键都是残留的情况）。
"""

from __future__ import annotations

import os
import winreg
from dataclasses import dataclass

HIVES = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
}

HIVE_TEXT = {"HKLM": "HKEY_LOCAL_MACHINE", "HKCU": "HKEY_CURRENT_USER"}

KIND_TEXT = {
    "uninstall": "卸载残留",
    "startup": "启动项",
    "apppath": "App Paths",
    "com": "COM 组件",
}

# 这些目录下的条目一律不报：系统自带组件经常指向 SysWOW64 之类的重定向路径
_SKIP_HINTS = (
    "\\windows\\",
    "\\system32\\",
    "\\syswow64\\",
    "microsoft\\windows\\currentversion\\run",
)


@dataclass
class Issue:
    kind: str            # uninstall / startup / apppath / com
    hive: str            # HKLM / HKCU
    path: str            # 子键路径
    name: str            # 值名；空字符串表示整个键都是残留
    title: str           # 展示名
    reason: str          # 判定依据
    detail: str = ""     # 缺失的具体路径

    @property
    def kind_text(self) -> str:
        return KIND_TEXT.get(self.kind, self.kind)

    @property
    def needs_admin(self) -> bool:
        return self.hive == "HKLM"

    @property
    def key(self) -> str:
        return f"{self.hive}\\{self.path}"


# ---------------------------------------------------------------- 工具


def _open(hive: str, path: str, access=winreg.KEY_READ):
    return winreg.OpenKey(HIVES[hive], path, 0, access)


def _values(hive: str, path: str) -> dict[str, tuple[object, int]]:
    """读一个键下的所有值：{名字: (数据, 类型)}。"""
    out: dict[str, tuple[object, int]] = {}
    try:
        with _open(hive, path) as key:
            index = 0
            while True:
                try:
                    name, data, vtype = winreg.EnumValue(key, index)
                except OSError:
                    break
                out[name] = (data, vtype)
                index += 1
    except OSError:
        pass
    return out


def _subkeys(hive: str, path: str) -> list[str]:
    out: list[str] = []
    try:
        with _open(hive, path) as key:
            index = 0
            while True:
                try:
                    out.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        pass
    return out


def _exe_from_command(command: str) -> str:
    """从命令行里抠出可执行文件路径。"""
    text = (command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else text.strip('"')
    # 没引号：取到第一个空格，但要处理 C:\Program Files\... 这种带空格的
    lower = text.lower()
    for ext in (".exe", ".dll", ".bat", ".cmd", ".com"):
        pos = lower.find(ext)
        if pos > 0:
            return text[: pos + len(ext)]
    return text.split(" ")[0]


def _missing(path: str) -> bool:
    if not path:
        return False
    expanded = os.path.expandvars(path.strip().strip('"'))
    if not expanded or "\\" not in expanded and ":" not in expanded:
        return False
    if any(hint in expanded.lower() for hint in _SKIP_HINTS):
        return False
    return not os.path.exists(expanded)


def _looks_like_path(value: str) -> bool:
    text = (value or "").strip().strip('"')
    return len(text) > 3 and (text[1:3] == ":\\" or text.startswith("\\\\"))


# ---------------------------------------------------------------- 扫描


def _scan_uninstall(out: list[Issue]) -> None:
    roots = [
        ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, root in roots:
        for sub in _subkeys(hive, root):
            path = f"{root}\\{sub}"
            values = _values(hive, path)
            if not values:
                continue

            display = str(values.get("DisplayName", ("", 0))[0] or "").strip()
            if not display:
                # 没有 DisplayName 的条目在「应用和功能」里根本不显示，跳过
                continue

            location = str(values.get("InstallLocation", ("", 0))[0] or "")
            uninstall = str(values.get("UninstallString", ("", 0))[0] or "")
            exe = _exe_from_command(uninstall)

            reasons: list[str] = []
            if _looks_like_path(location) and _missing(location):
                reasons.append("安装目录不存在")
            if _looks_like_path(exe) and _missing(exe):
                reasons.append("卸载程序不存在")

            if reasons:
                out.append(
                    Issue(
                        kind="uninstall",
                        hive=hive,
                        path=path,
                        name="",
                        title=display,
                        reason="，".join(reasons),
                        detail=location or exe,
                    )
                )


def _scan_startup(out: list[Issue]) -> None:
    roots = [
        ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
        ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
    ]
    for hive, path in roots:
        for name, (data, _vtype) in _values(hive, path).items():
            command = str(data or "")
            exe = _exe_from_command(command)
            if not _looks_like_path(exe) or not _missing(exe):
                continue
            out.append(
                Issue(
                    kind="startup",
                    hive=hive,
                    path=path,
                    name=name,
                    title=name or "(默认)",
                    reason="启动命令指向的文件不存在",
                    detail=exe,
                )
            )


def _scan_app_paths(out: list[Issue]) -> None:
    for hive, root in (
        ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ):
        for sub in _subkeys(hive, root):
            path = f"{root}\\{sub}"
            values = _values(hive, path)
            target = str(values.get("", ("", 0))[0] or "")
            if not _looks_like_path(target) or not _missing(target):
                continue
            out.append(
                Issue(
                    kind="apppath",
                    hive=hive,
                    path=path,
                    name="",
                    title=sub,
                    reason="登记的 exe 不存在",
                    detail=target,
                )
            )


def _scan_com(out: list[Issue], limit: int = 600) -> None:
    """扫 COM 组件里 InprocServer32 指向的 DLL 不存在的情况。

    只报「DLL 所在目录也不存在」的 —— 目录还在、只是单个文件没了，
    很可能是重定向或权限问题，不是残留。

    CLSID 有上万个，先把子键全枚举成 list 再筛会慢十倍，
    所以这里**边枚举边判断，够数就停**。
    """
    root = r"SOFTWARE\Classes\CLSID"
    checked = 0
    try:
        with _open("HKLM", root) as key:
            index = 0
            while checked < limit:
                try:
                    clsid = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1

                path = f"{root}\\{clsid}\\InprocServer32"
                values = _values("HKLM", path)
                if not values:
                    continue
                checked += 1

                dll = str(values.get("", ("", 0))[0] or "")
                if not _looks_like_path(dll):
                    continue
                expanded = os.path.expandvars(dll.strip('"'))
                if any(hint in expanded.lower() for hint in _SKIP_HINTS):
                    continue
                folder = os.path.dirname(expanded)
                if folder and not os.path.isdir(folder):
                    out.append(
                        Issue(
                            kind="com",
                            hive="HKLM",
                            path=path,
                            name="",
                            title=clsid,
                            reason="组件所在目录不存在",
                            detail=dll,
                        )
                    )
    except OSError:
        pass


def scan(should_stop=lambda: False, on_progress=None) -> list[Issue]:
    """扫描注册表冗余。只读，不改任何东西。"""
    out: list[Issue] = []
    steps = (
        ("正在检查卸载残留…", _scan_uninstall),
        ("正在检查启动项…", _scan_startup),
        ("正在检查 App Paths…", _scan_app_paths),
        ("正在检查 COM 组件…", lambda o: _scan_com(o)),
    )
    for index, (text, fn) in enumerate(steps):
        if should_stop():
            break
        if on_progress:
            on_progress(int(index / len(steps) * 100), text)
        try:
            fn(out)
        except Exception:
            continue
    if on_progress:
        on_progress(100, "扫描完成")
    return out


# ---------------------------------------------------------------- 备份


_REG_TYPES = {
    winreg.REG_SZ: "string",
    winreg.REG_EXPAND_SZ: "expand",
    winreg.REG_DWORD: "dword",
    winreg.REG_QWORD: "qword",
    winreg.REG_MULTI_SZ: "multi",
    winreg.REG_BINARY: "binary",
}


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _format_value(name: str, data, vtype: int) -> str:
    kind = _REG_TYPES.get(vtype)
    label = f'"{_quote(name)}"' if name else "@"

    if kind in ("string", "expand"):
        text = str(data).replace("\\", "\\\\").replace('"', '\\"')
        return f'{label}="{text}"'
    if kind == "dword":
        return f"{label}=dword:{int(data) & 0xFFFFFFFF:08x}"
    if kind == "qword":
        high = (int(data) >> 32) & 0xFFFFFFFF
        low = int(data) & 0xFFFFFFFF
        return f"{label}=hex(b):{low:08x},{high:08x}"
    if kind == "multi":
        parts = [str(p).replace("\\", "\\\\") for p in (data or [])]
        return f"{label}=hex(7):" + ",".join(
            f"{b:02x}" for b in ("\0".join(parts) + "\0\0").encode("utf-16-le")
        )
    if kind == "binary":
        return f"{label}=hex:" + ",".join(f"{b:02x}" for b in bytes(data or b""))
    return f"; 无法导出（类型 {vtype}）：{name}"


def export_backup(issues: list[Issue], path: str) -> int:
    """把待删除的项导出成 .reg，返回写入的键数。

    备份是删除的前置条件：没有备份就不允许删。
    """
    lines = ["Windows Registry Editor Version 5.00", ""]
    written = 0
    for issue in issues:
        full = f"{HIVE_TEXT[issue.hive]}\\{issue.path}"
        lines.append(f"[{full}]")
        values = _values(issue.hive, issue.path)
        if issue.name:
            if issue.name in values:
                data, vtype = values[issue.name]
                lines.append(_format_value(issue.name, data, vtype))
        else:
            for name, (data, vtype) in values.items():
                lines.append(_format_value(name, data, vtype))
        lines.append("")
        written += 1

    try:
        with open(path, "w", encoding="utf-16") as handle:
            handle.write("\r\n".join(lines))
        return written
    except OSError:
        return 0


# ---------------------------------------------------------------- 删除


def delete(issues: list[Issue]) -> tuple[int, int]:
    """删除指定的项，返回 (成功, 失败)。

    有值的删值，没值的删整个键。HKLM 需要管理员，失败会如实计数。
    """
    ok = 0
    failed = 0
    for issue in issues:
        try:
            if issue.name:
                with _open(
                    issue.hive, issue.path, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.DeleteValue(key, issue.name)
            else:
                _delete_tree(issue.hive, issue.path)
            ok += 1
        except OSError:
            failed += 1
    return ok, failed


def _delete_tree(hive: str, path: str) -> None:
    """递归删除一个键（先清子键，再删自己）。"""
    for sub in _subkeys(hive, path):
        _delete_tree(hive, f"{path}\\{sub}")
    winreg.DeleteKey(HIVES[hive], path)
