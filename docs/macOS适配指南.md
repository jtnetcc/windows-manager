# macOS 适配指南

这份文档写给「在 Mac 上把这套代码跑起来」的人。先说结论：

**不是加个 `if sys.platform` 就能跑，也不是缺几行兼容代码。** 这个项目的系统层
（约 40 个模块）直接调用 Windows API；UI 层（`ui/`）和状态层（`core/store.py`）
可以原样复用。合理的目标是先让**一个页面**在 macOS 上真正跑通，再逐页推进。

当前状态：`tools/build_macos.py` 能打出 `.app` + `.dmg`，但启动时
`main.py::_require_windows()` 会弹「只支持 Windows」并退出 —— 这是有意的，
避免用户看到 `ModuleNotFoundError: No module named 'winreg'`。

---

## 一、先建立平台层，别到处写 if

现状是所有模块直接 `import winreg` / `ctypes.windll...`，在各处加分支会失控。
建议第一步只做结构，不改行为：

```
core/platform/
  __init__.py        暴露 backend（按 sys.platform 选）
  base.py            抽象接口（下面那张表的左侧）
  win32/             现有实现搬进来（先只做 re-export，不改逻辑）
    __init__.py      把 core.winmem / winpath / startup … 包一层
  darwin/            新实现（初期可以全是 NotImplementedError + 明确文案）
```

`core/*.py` 里把 `import winreg` 换成 `from .platform import backend`，
其余逻辑不动。这样 macOS 上「哪些没实现」是显式的，而不是 ImportError。

参考接口划分（按现有模块的真实职责，不是照搬 Windows API）：

| 能力 | Windows 实现 | macOS 对应 |
|---|---|---|
| 内存总量/占用/可回收 | `core/winmem.py`（GlobalMemoryStatusEx + 工作集） | `sysctl hw.memsize` + `vm_stat` |
| 路径常识（系统盘、用户目录、桌面、固定盘） | `core/winpath.py` | `/`、`~/Desktop`、`/Volumes` |
| 进程列表 / 结束进程 | `core/optimizer.py`（psutil + OpenProcess） | psutil 跨平台，结束进程用 `SIGTERM`→`SIGKILL` |
| 开机启动 | `core/startup.py`（Run 键 / 启动文件夹 / schtasks） | `~/Library/LaunchAgents/*.plist` |
| 单实例 | `core/single_instance.py`（命名互斥体） | `fcntl.flock` 或 `QLocalServer` |
| 磁盘健康 / 通电时间 | `core/diskhealth.py`（DeviceIoControl + SMART） | `diskutil info -plist`（无 SMART 时给「不支持」） |
| 回收站体积与清空 | `core/recycle.py` / `core/trash.py`（$I 解析 / SHFileOperation） | `~/.Trash` 统计 + `os.remove` |
| 系统信息 / 激活 | `core/sysinfo.py` / `core/activation.py` | `system_profiler -json`；激活无对应概念 |
| 系统瘦身开关 | `core/slim.py`（注册表 / 服务 / 电源） | 初期整体隐藏（`page_slim` 显示「此平台不支持」） |
| 系统强调色 | `ui/theme_dark.py`（DwmGetColorizationColor） | `defaults read -g AppleAccentColor` |
| 窗口圆角 | `ui/winround.py`（DWM / SetWindowRgn） | 直接 return（macOS 窗口自带圆角） |

## 二、建议的推进顺序

1. **让 UI 空跑起来**：`core/platform/darwin` 全部返回占位值（0 / 空列表 / False），
   `main.py` 去掉 `_require_windows()` 在 darwin 上的硬退出，确认主窗口能开、
   10 个页面能切、不崩。
2. **性能监控页**（`ui/pages_monitor.py` + `core/metrics.py`）：这一页只依赖 psutil，
   改动最小，先把它跑成真数据 —— 这是验证平台层抽象是否合适的探针。
3. **系统信息页**（`core/sysinfo.py` + `core/diskinfo.py`）：`system_profiler -json`
   一次给全，注意它单次调用 1~3 秒，必须放任务线程。
4. **清理中心**（`core/cleaner.py`）：规则表里 `%LOCALAPPDATA%` 这类路径要换成
   平台目录，规则本身的语义（缓存目录、保留策略）可以复用。
5. **开机启动 / 系统瘦身 / 注册表**：前两个按 LaunchAgents 重写，第三个在
   macOS 上**没有对应概念**，直接隐藏入口，不要做「伪注册表」。

## 三、动手前必须知道的几件事

- **打包也要在 Mac 上做**：`hdiutil`、`.app` 结构、`codesign` 都是 macOS 自带，
  PyInstaller 不支持交叉编译。见 `docs/打包说明.md` 第四节。
- **UI 层有 Windows 假设**：`ui/main_window.py` 里有 `ctypes.windll`（标题栏配色）、
  `ui/winround.py`、托盘 `QSystemTrayIcon` 行为差异（macOS 上托盘是菜单栏图标）。
- **不要 fork 一份 macOS 分支**：两边一起改进 UI 会立刻分叉。用平台层 + 运行时分支。
- **测试仍可用**：`tools/check_*.py` 大多是纯逻辑断言，去掉 Windows 依赖后
  可以直接在 macOS 上跑，是迁移时的回归网。
- **标题里的「Windows系统管家」**：真要发布 macOS 版，先决定是否改名 ——
  这会影响安装包、图标、`config_dir()`（现在是 `%APPDATA%\MemoryKeeper`，
  macOS 应该放 `~/Library/Application Support/MemoryKeeper`）。

## 四、Mac 上第一次上手

```bash
# 1. 拿代码
git clone https://github.com/jtnetcc/windows-manager.git
cd windows-manager

# 2. 环境（Python 3.10+，建议 3.13）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-build.txt   # 要打包时才需要

# 3. 当前会被平台守卫拦住，这是预期的
python main.py

# 4. 验证打包链路能通（产物是壳，但流程可验）
python tools/build_macos.py --no-dmg
```

改造期间建议把 `main.py` 的守卫改成「darwin 上允许启动、但功能页给占位」，
而不是直接退出 —— 否则你没法边改边看。
