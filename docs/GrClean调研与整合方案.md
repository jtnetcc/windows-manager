# GrClean 调研与整合方案

调研对象：<https://github.com/graent/grclean>
调研时间：2026-11-07
对照项目：Windows系统管家（本地 `C:/Users/Jt/WorkBuddy AI/内存占用`）

---

## 一、项目概况

| 项 | 内容 |
|---|---|
| 形态 | Electron 28 + Vue 3 + TypeScript 5 + Vite 5，electron-builder 打包 NSIS/便携版 |
| 规模 | `src/main/engine/` 下 39 个引擎模块，`src/renderer/views/` 下 26 个功能页 |
| 平台 | 代码里 `build:mac` / `build:linux` 脚本齐全，但 README 只承诺 Windows x64 |
| 后端 | 一个轻量 PHP 服务，仅做版本更新与内容分发，不参与功能 |
| 主题 | 8 套主题 + 全局字号缩放 |
| 语言 | 9 种内置语言（i18n-packs.ts 有 96 KB） |

功能面覆盖：垃圾清理、社交软件专清（微信/QQ/企业微信）、大文件、重复文件、启动项、
卸载残留、注册表、安装包、日志、磁盘与驱动、健康度评分、目录迁移、系统优化、
系统精简、计划任务、右键菜单、回收站、软件卸载、系统快照、自定义清理规则。

## 二、许可结论：不能合并代码

`LICENSE` 是作者自拟的 **GrClean Non-Commercial License 1.0**（基于 Apache 2.0 改写），
不是开源许可证。三条硬限制：

1. **禁止商业化** —— 不得销售、转售、捆绑或任何付费行为
2. **禁止二次分发** —— 不得重新发布、镜像，或以任何渠道提供给第三方
3. **禁止去除/篡改** 版权信息与完整性校验

第 2 条明确写到「不得分发衍生作品（Derivative Works）」，第 12 条规定违者授权自动终止。

**因此：**

- ❌ 不能把它的代码拷进我们的项目
- ❌ 不能把它打包进我们的发行版
- ✅ 可以学习功能设计、交互流程、工程决策（这些属于思想，不受版权保护）
- ✅ 可以自己重新实现同样的功能
- ⚠️ 如果确实要用它的代码，需要联系作者拿商业/合作授权

下面的方案全部建立在「**自己实现，只借鉴思路**」的前提上。

## 三、值得借鉴的六个工程决策

这部分是本次调研最大的收获，比功能清单更值钱。

### 1. 路径保护的「三道防线 + fail-safe」

`engine/protected.ts` 的判定顺序值得照抄思路：

1. **无法规范化 → 视为受保护**（`fs.realpathSync` 失败就返回 `true`）
2. 磁盘根目录 → 保护
3. 系统关键文件精确匹配（`pagefile.sys` / `hiberfil.sys` / `bootmgr`）→ 保护
4. 系统目录前缀 → 保护（`C:\Windows`、`Program Files`、`ProgramData`…）
5. 用户主目录根 → 保护（防止整盘用户数据被清）
6. 应用自身所在目录 → 保护（防止自残）

关键设计：系统级清理项（Windows.old、SoftwareDistribution\Download）都在 `C:\Windows`
之下，硬编码前缀会把它们一并拦死。它的解法是 `allowSystemPrefixes` —— **必须同时满足
两个条件才放行**：① 用户显式勾选了该清理项；② 候选路径确实落在该项声明的根目录内。
避免「勾一次、全盘放行」。

另外 `C:\Windows` 不硬编码，而是运行时读 `WINDIR`/`SystemDrive`，兼容系统盘不是 C: 的机器。

### 2. 流式扫描：分批 + 让权，且**必须串行**

`engine/junk.ts` 用 `async generator`，每 40 条 `yield` 一次，每批之后
`await setImmediate` 让出事件循环，保证「取消」能立刻响应。

代码里有一段注释特别值得记：他们**试过**多规则并发遍历，结果在单块系统盘上多路深度
递归 + 每文件同步 `statSync` 造成磁盘 IO 争用，事件循环饿死，表现为「扫描卡住、计数不涨」。
最后退回串行流式。**并发扫盘不是更快，是更慢。**

### 3. 扫描阶段就把「删不掉的东西」过滤掉

`junk.ts` 里有四类预过滤，都是为了减少「清理失败」提示：

- 系统标记文件：`desktop.ini`、`thumbs.db`、`ehthumbs.db` —— 删了没收益，Windows 会重建
- 快捷方式 `.lnk` —— 没空间收益，且常被 Shell 占用
- **易锁定文件**（`.tmp/.temp/.lock/.partial/*~`）：扫描时用 `open(r)` 试探，打不开就
  直接从列表隐藏，既不列出也不在结束时报失败
- 系统盘 Windows 下的高危位置：`system32\config`、`winsxs`、`drivers`、`servicing` 等
  25 个关键子目录 + `.sys/.cat/.drv` 扩展名，命中即跳过

### 4. 启动项「禁用」= 改名加后缀，不删除

`engine/startup.ts` 汇总三个来源：注册表 Run/RunOnce（HKCU/HKLM/WOW6432Node）、
启动文件夹（当前用户 + 所有用户）、任务计划（`schtasks`）。

禁用动作：

- 注册表项 → `Rename-ItemProperty` 把值名改成 `原名.grdisabled`
- 启动文件夹 → 文件改名 `x.lnk` → `x.lnk.grdisabled`
- 计划任务 → `schtasks /change /disable`

全程可逆，不丢数据，随时能恢复。比「直接删注册表值」安全一个数量级。

### 5. 健康度评分：纯函数 + 11 项加权 + 动态建议

`engine/score.ts` 是全项目最值得学的一块，因为它**不依赖任何 Node API**，纯函数好单测：

- 11 项因子，权重和为 100：剩余空间 28 / 容量 5 / 垃圾 12 / 重复 8 / 社交缓存 8 /
  注册表 4 / 启动项 7 / 磁盘健康 11 / 文件系统与 4K 对齐 7 / 更新积压 5 / 可释放系统文件 5
- 两个打分曲线：`largerBetter(v, fullAt, zeroAt, max)` 与 `smallerBetter(...)`，
  超过/低于阈值取满分或 0，中间线性
- 分级：≥85 优秀 / ≥70 良好 / ≥50 一般 / ≥30 较差 / 其余危险
- **建议是动态生成的**：只对「得分 < 满分 70%」的因子出建议，每条带具体数字，
  而不是笼统一句「建议清理」

一个细节：剩余空间同时算百分比和绝对值，取**较差**的那个，避免 2TB 盘剩 100GB 被算作健康。

### 6. 用户自定义清理规则存成 JSON

`rules/<id>.json`，字段：`paths`（支持 `%ENV%` 与 `~` 展开）、`max_age_days`、
`min_size_kb`、`enabled`。加载时容错跳过非法文件。文件名做 `[a-zA-Z0-9_-]` 净化，
防止路径穿越。

## 四、功能差距对照

| 能力 | 我们 | GrClean | 差距 |
|---|---|---|---|
| 实时监控（CPU/内存/磁盘/网络） | ✅ 更细 | 无 | 我们领先 |
| 内存优化（清工作集+待命） | ✅ 原生 API | 无 | 我们领先 |
| 进程管理 / 进程树 | ✅ | 无 | 我们领先 |
| 软件列表与卸载 | ✅ 基础 | ✅ 带残留扫描 | 中等 |
| 主题 | ✅ 6 套 | ✅ 8 套 | 持平 |
| **健康度评分** | ❌ | ✅ 11 项 | **大** |
| **垃圾清理** | ❌ | ✅ 规则化 | **大** |
| **启动项管理** | ❌ | ✅ 三来源 | **大** |
| 大文件 / 重复文件 | ❌ | ✅ | 中 |
| 回收站 | ❌ | ✅ | 小 |
| 注册表清理 / 卸载残留 | ❌ | ✅ | 中（风险高） |
| 目录迁移 / 系统快照 | ❌ | ✅ | 大 |
| 多语言 / 字号缩放 | ❌ | ✅ | 小 |

结论：我们是「监控器」，它是「清理器」。**不冲突，正好互补** —— 但清理类功能必须
建立在可靠的安全边界上，否则一次误删就毁掉口碑。

## 五、整合路线

### P1 —— 优先做（直接补齐核心缺口，风险可控）

1. **安全层 `core/safety.py`**（前置，其它功能都依赖它）
   - `canonicalize(path)` → `Path.resolve()`，失败即视为受保护
   - 六条保护规则照搬上面的思路
   - `is_protected(path, allow_prefixes=None)` 纯函数，先写单测
2. **健康度评分 `core/health.py`**
   - 纯函数 `compute_score(snapshot) -> ScoreResult`
   - 数据源：现有 `MetricsSnapshot` + 新增磁盘健康（见下）+ 启动项数 + 垃圾体积
   - 新增「体检」页，展示总分仪表盘 + 11 项明细条 + 动态建议
3. **垃圾清理 `core/cleaner.py` + `ui/page_clean.py`**
   - 内置规则先覆盖低风险区：`%TEMP%`、浏览器缓存（Edge/Chrome/Firefox）、
     Windows 临时目录、缩略图缓存、日志目录
   - 扫描用 QThread + 分批 `emit`，每批 `msleep(0)` 让权（对应它的 `setImmediate`）
   - 扫描阶段就做四项预过滤（标记文件 / .lnk / 锁定文件试探 / Windows 高危子目录）
   - 删除前必须走 `safety.is_protected()`，默认进回收站而不是直接删
4. **启动项管理 `core/startup.py` + `ui/page_startup.py`**
   - 三来源：winreg（Run/RunOnce × HKCU/HKLM/WOW6432Node）、启动文件夹、`schtasks`
   - 禁用 = 改名加 `.off` 后缀，可逆
5. **磁盘健康采集**（喂给评分）
   - `Get-PhysicalDisk` → MediaType / HealthStatus / BusType（**已实测可行，无需管理员**）
   - `fsutil behavior query DisableDeleteNotify` → TRIM
   - `Get-Partition` 的 Offset → 4K 对齐判断

> ⚠️ TRIM 有个坑：`DisableDeleteNotify = 0` 表示「禁用删除通知」这项是关的，
> 即 **TRIM 已开启**。中文输出会显示「已禁用」，直接读字符串会判断反。

### P2 —— 次优先

- **大文件分析**：按目录流式遍历，`>200MB` 才收，结果按体积排序
- **重复文件**：先按体积分组，只对同尺寸文件算哈希（三段式：大小 → 头部 4KB → 全量）
- **回收站**：`SHQueryRecycleBin` / `SHEmptyRecycleBin`（ctypes，不用外部程序）
- **用户自定义规则**：JSON 存 `%APPDATA%\Windows系统管家\rules\`

### P3 —— 谨慎评估

- 注册表清理：收益低、风险高，建议只做「只读检测 + 备份后删除」，且默认关闭
- 卸载残留 / 目录迁移 / 系统快照：工程量大，且容易造成用户数据事故

## 六、Windows + macOS 双平台架构

这是本次要提前想清楚的事，因为**现在不做抽象，以后就要重写**。

### 6.1 现状与障碍

`core/` 里现在有四处硬绑 Windows：

| 文件 | 依赖 |
|---|---|
| `core/winmem.py` | `NtQuerySystemInformation` / `NtSetSystemInformation` / `SeProfileSingleProcessPrivilege` |
| `core/optimizer.py` | `EnumWindows` / `GetWindowTextLengthW` / `TerminateProcess` |
| `core/apps.py` | 注册表 Uninstall 三处键 |
| `core/metrics.py` | 引用 `winmem` 取内存；`nvidia-smi` |

UI 层（`ui/`）是纯 PySide6，本身跨平台，基本不用改。

### 6.2 推荐结构

```
core/
  platform/
    __init__.py        # 按 sys.platform 选实现，导出统一接口
    base.py            # Protocol：SystemBackend
    windows.py         # 现有 winmem + optimizer 的 Win32 部分
    macos.py           # mach / psutil / diskutil 实现
  health.py            # 纯函数评分（跨平台）
  safety.py            # 路径保护（跨平台，按平台给不同前缀表）
  cleaner.py           # 清理执行器（跨平台，规则由 JSON 提供）
  rules/builtin.json   # 内置清理规则，按平台分组
ui/                    # PySide6，不改
```

`SystemBackend` 协议先定这几个方法，够用且好实现：

```python
class SystemBackend(Protocol):
    def memory_snapshot(self) -> MemSnapshot: ...
    def optimize_memory(self, deep: bool) -> OptimizeResult: ...
    def process_list(self, limit: int) -> list[dict]: ...
    def installed_apps(self) -> list[InstalledApp]: ...
    def startup_entries(self) -> list[StartupEntry]: ...
    def disk_health(self) -> list[DiskHealth]: ...
```

### 6.3 能力映射表

| 能力 | Windows | macOS |
|---|---|---|
| 内存明细 | `NtQuerySystemInformation(80)` | `host_statistics64` / `vm_stat` |
| 释放内存 | `NtSetSystemInformation(80,2/4)` | 无等价接口（见 6.4） |
| 进程列表 / 进程树 | psutil | psutil |
| 已装软件 | 注册表 Uninstall 三键 | `/Applications` + `pkgutil --pkgs` |
| 启动项 | Run 键 + 启动文件夹 + schtasks | `~/Library/LaunchAgents`、`/Library/LaunchDaemons` plist |
| 磁盘健康 | `Get-PhysicalDisk` / `fsutil` | `diskutil info` / `smartctl` |
| 垃圾目录 | `%TEMP%`、`LocalAppData` | `~/Library/Caches`、`/tmp`、`~/Library/Logs` |
| 回收站 | `SHQueryRecycleBin` | `~/.Trash` |
| 权限 | UAC 提权（`ShellExecuteW runas`） | 完全磁盘访问权限 + 辅助功能授权 |

### 6.4 必须提前接受的三个现实

1. **macOS 上没有「一键清内存」这种东西。** macOS 的内存管理策略（主动压缩、按需回收
   inactive 页）和 Windows 完全不同，第三方工具所谓「释放内存」要么是 `purge`（需 root，
   且会拖慢后续性能），要么纯粹是心理安慰。Mac 侧的对应功能应该诚实地改成
   **「找出内存大户 + 引导退出/重启」**，而不是假装能清。
2. **macOS 需要用户手动授权「完全磁盘访问权限」**，否则扫不到 `~/Library` 下的缓存。
   首次启动要引导用户去系统设置里勾选，且勾选后需要重启进程才生效。
3. **分发成本**：macOS 不签名不公证的话，用户下载后会被 Gatekeeper 拦住，必须
   右键→打开才能运行。要正经分发得有 Apple Developer 账号（99 美元/年）+ 公证流程。

### 6.5 三条路线对比

| 路线 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）** | 保持 Python + PySide6，抽 `core/platform/` | 一份 UI、一份业务，功能天然同步；现有代码 90% 可复用 | Mac 上不是原生观感；包体大（~36MB）；需处理权限/公证 |
| B | 重写为 Electron + Vue（和 GrClean 同栈） | 一套 UI 通吃三端，生态成熟，主题/多语言现成 | 全部重写，Windows 侧现有成果作废；内存占用比现在还高 |
| C | 内核 Python 做本地服务，UI 各自原生（Win: PySide6 / Mac: SwiftUI） | 各自原生体验最好 | 两套 UI 维护翻倍，功能极易不同步 |

**建议走 A。** 理由：我们已经有 6 套主题、8 个功能页、完整的内存优化链路，这些
重写成本远大于收益；而 PySide6 本身是跨平台的，真正需要隔离的只有那四个文件里的
系统调用。

### 6.6 落地步骤（不影响当前 Windows 功能）

1. 定义 `core/platform/base.py` 的 Protocol（先只抽内存/进程/已装软件三项）
2. 把 `winmem` + `optimizer` 的 Win32 部分搬进 `core/platform/windows.py`，
   原模块改成转发，保证现有 UI 一行不用改
3. `core/metrics.py` 里对 `winmem` 的直接引用改成 `backend.memory_snapshot()`
4. 新建 `core/platform/macos.py` 空实现（`NotImplementedError`），先把接口跑通
5. 后续在 Mac 上逐个补实现，UI 侧只加「当前平台不支持」的降级提示

这样 Windows 功能不会因为跨平台改造而中断，Mac 侧可以慢慢补。

## 七、风险清单

| 风险 | 说明 | 应对 |
|---|---|---|
| 误删用户数据 | 清理类功能最致命的问题 | 强制走 `safety.is_protected()`；默认删到回收站；删除前出预览清单 |
| 许可证 | GrClean 代码不可用 | 只借鉴思路，自己实现；文档留痕说明来源 |
| 杀软误报 | 清理 + 提权 + 改注册表，行为特征像恶意软件 | 不混淆、不静默删除、开源、代码签名 |
| macOS 权限 | 完全磁盘访问 / 辅助功能 | 首次启动引导 + 权限状态检测 + 降级提示 |
| 功能膨胀 | 26 个功能页会让 UI 变成杂物间 | 按「监控 / 清理 / 工具」三组分区，不做全量对标 |

## 八、下一步建议

先做 **P1 的安全层 + 健康度评分**：这两块不涉及删除操作，零风险，但能立刻让产品从
「监控器」变成「能给出结论的工具」，也是后续所有清理功能的公共地基。
垃圾清理和启动项管理随后跟上，两者都需要安全层先就位。
