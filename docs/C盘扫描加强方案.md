# C 盘扫描加强方案

> 起因：现在很多整机 / 笔记本出厂就是「一块硬盘一个分区」，系统、软件、个人文件
> 全在 C 盘。这类机器上「扫 C 盘」等于「扫全盘」，而当前实现的三处设定正好在这种
> 场景下失效。下面是实测结论与改造方案。

---

## 一、现状实测（本机，C 盘 115.1 GB / 已用 43.9 GB）

| 测法 | 用时 | 扫到文件 | 统计体积 |
|---|---|---|---|
| `os.walk` + `os.stat`（现有 `bigfile.scan_ex`） | 47.9 s | 35.3 万 | 34.6 GB |
| `os.scandir` + `entry.stat()` | 40.6 s | 39.4 万 | 44.6 GB |
| 同上（文件系统缓存已热） | 14.1 s | 39.4 万 | 44.6 GB |

两个结论：

1. **scandir 版快 15%，且多扫到 4 万个文件、10 GB。** 不是玄学：`os.stat` 走
   `GetFileAttributesEx`，遇到受保护文件直接拒绝访问；而 `FindFirstFile` 枚举目录时
   已经把大小一起返回了，`entry.stat()` 用的就是这份数据。现有实现每个文件都要再
   发一次系统调用，既慢又更容易失败。
2. **单分区 512 GB 盘按 100~150 万文件估，冷盘 2~4 分钟，热盘 40~60 秒。** 这个量级
   必须有进度、可取消、结果缓存，不能阻塞 UI。

系统盘根目录实际长这样（本机）：

```
C:\hiberfil.sys   3.10 GB
C:\pagefile.sys   1.88 GB
C:\swapfile.sys   0.02 GB
```

**5 GB 就裸露在根目录**，而现在的「大文件」页默认范围是用户目录，看不见它们。

---

## 二、当前实现的三处失效点

### 1. 默认范围是「用户目录」，等于只扫 `C:\Users\<你>`

`bigfile.preset_roots("home")` → `winpath.home_roots()`。整块盘只有一个分区的用户，
游戏装 `C:\Games`、迅雷默认下到 `C:\迅雷下载`、部分软件数据落 `C:\ProgramData` —— 
全部扫不到。要看得手动在下拉里切到「整个磁盘」，而多数人不会。

### 2. 只有文件清单，没有目录聚合

用户拿到的是一张「2 GB 以上的文件」列表。但单分区出问题的地方往往不是某个大文件：

- `C:\Windows\Installer` 几个 GB，全是碎的 msi
- `C:\ProgramData\Package Cache` 几个 GB
- `C:\Users\<你>\AppData\Local\Packages` 几十个文件夹各几百 MB
- `C:\Windows\SoftwareDistribution` 更新残留

这些「单文件都不大、目录整体很大」的情况，按文件列出来一条都看不见。

### 3. `SKIP_DIR_NAMES` 把 `windows` 整个跳过了

```python
SKIP_DIR_NAMES = {"$recycle.bin", "system volume information",
                  "windows", "winsxs", "node_modules", ...}
```

对大文件扫描（怕慢）合理，但它同时让「系统到底占了多少」永远算不出来 —— 单分区机器
上 Windows 目录常占 20~40 GB，是最大的一块，而现在这块在界面上是空白。

### 附加问题

- **云盘占位文件被当大文件**：OneDrive「文件按需」的本地文件逻辑大小几十 GB、实际占 0，
  现在会被列进大文件清单（`FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000`）。
- **根目录系统文件没标注**：`hiberfil.sys` 扫出来能勾选，点了删除被 `safety` 拦下，
  用户只看到「失败」。
- **体检只看剩余百分比**：`larger_better(percent, 30, 10)` 对 1 TB 盘剩 20%（200 GB）
  和 128 GB 盘剩 20%（25 GB）给同样的分。单分区大容量盘会误判成「健康」。

---

## 三、方案：从「列大文件」改成「回答空间去哪了」

一句话：**一次遍历拿到全盘目录占用，配上「哪些能回收」，再按「这台机器有几个分区」
决定给清理建议还是迁移建议。**

### 3.1 新增 `core/dirstat.py` —— 目录占用树

```python
@dataclass
class Node:
    path: str
    size: int            # 含全部子目录
    files: int
    children: list[Node] # 按 size 降序，超出 top_n 的并进 "(其他 N 项)"
    errors: int          # 无权限读不了的条目数

def scan(root: str, depth: int = 3, min_size: int = 64 << 20,
         should_stop=..., on_progress=None) -> Node
```

实现要点（每条都有踩坑依据）：

| 要点 | 做法 | 为什么 |
|---|---|---|
| 遍历方式 | `os.scandir` + `entry.stat(follow_symlinks=False)` | 实测快 15%、多扫 11% 文件 |
| 目录求和 | **递归返回值逐层累加** | POC 里试过「事后按路径深度合并」，`C:\Users` 9.81 GB 被算成 0.29 GB |
| reparse point | `st_file_attributes & 0x400` 跳过 | 否则 `C:\Users\All Users`、`C:\Documents and Settings` 会绕圈 |
| 硬链接 | `(st_dev, st_ino)` 去重 | WinSxS 有大量硬链接，不去重会虚高 |
| 云盘占位 | `attrs & 0x00400000` 单独计 `cloud_size` | 不计入「占用」，但要在界面上说明「另有 X GB 在云端」 |
| 权限 | 读不到的条目记 `errors`，不中断 | `System Volume Information`、`$Recycle.Bin` 普通权限进不去 |
| 并行 | 顶层目录 `ThreadPoolExecutor(4)`（SSD）/ 串行（HDD） | scandir 释放 GIL；机械盘并行反而抖动，按 `diskhealth` 的介质类型选 |
| 进度 | 每 500 文件回调「已扫 N / 已 X GB / 当前目录」 | 2 分钟没有反馈的界面等于卡死 |
| 缓存 | 结果落 `store`，带时间戳 | 切页不重扫 |

「标准 / 深度」两档：标准只到第 3 层且不展开 `WinSxS`、`Installer`、`Package Cache`
内部（这几个用专项接口单独取数）；深度模式才全展开。

### 3.2 新增 `core/spacereport.py` —— 可回收清单

不新写扫描，**把已有能力聚合成一张按体积排序的表**，缺的补上：

| 项 | 数据来源 | 状态 |
|---|---|---|
| 休眠文件 | `slim._hiber_size()` | 已有 |
| Windows.old | `slim._old_size()` | 已有 |
| 内存转储 | `slim._dump_size()` | 已有 |
| WinSxS 可回收 | `slim`（DISM AnalyzeComponentStore，30~60 s，手动触发） | 已有 |
| 更新缓存 / 传递优化 / 日志 / 社交缓存 | `cleaner` 规则 | 已有 |
| 回收站 | `recycle` | 已有 |
| 重复文件 | `dup` | 已有 |
| 页面文件 | `stat` + 注册表 `PagingFiles` | **新增** |
| 系统还原点 | `vssadmin list shadowstorage`（exe 已是管理员） | **新增** |
| 搜索索引 | `stat Windows.edb` | **新增** |
| WSL / Docker 虚拟磁盘 | 定位 `*.vhdx` + `stat` | **新增** |
| Store 应用数据 | `AppData\Local\Packages` 目录聚合 | **新增** |
| Windows Installer 缓存 | `stat` 目录 | **只统计，不提供删除** |

每项统一结构：

```python
@dataclass
class Reclaim:
    key: str; name: str; size: int
    action: str        # clean / slim / adjust / open / none
    page: str          # 点进去看哪个页面
    risk: str          # "安全" / "关闭后无法休眠" / "需先确认不需要回退"
    admin: bool
```

「只统计不给动作」的三项（`WinSxS` 内部、`Windows\Installer`、`System Volume
Information`）是刻意的：删了会让软件卸载/修复失败，或破坏系统还原。给数字是为了让
用户知道空间在哪，不是让他点。

### 3.3 新增 `ui/page_space.py` —— 界面

布局见对话里的线框图：

1. **空间去向**：一条堆叠条（系统 / 应用 / 用户 / 系统文件 / 可用）+ 五个数，点一段下钻
2. **目录占用**：可下钻的列表，每行「名称 · 占比条 · 大小」，底部有面包屑回上级
3. **可回收**：右侧按体积排序，每行一个动作按钮，合计显示在标题
4. **单分区提示条**：仅当检测到一个固定分区时出现

行右键菜单复用现有约定：`打开目录` / `复制路径` / `在这里扫大文件` / `在这里查重复`。

### 3.4 单分区识别与体检口径

```python
def layout() -> str:  # "single" / "multi"
    fixed = [p for p in psutil.disk_partitions(all=False) if p.fstype]
    return "single" if len(fixed) <= 1 else "multi"
```

- 总览页「磁盘」KPI 副标题在 `single` 时补一句「仅 1 个分区」
- 体检的系统盘项改成**取「剩余百分比」与「剩余绝对 GB」中较差者**：
  `score = min(larger_better(percent, 30, 10), larger_better(free_gb, 40, 8))`
  128 GB 盘剩 25 GB 与 1 TB 盘剩 200 GB 不再同分
- `single` 且剩余 < 15% 时，建议文案换成「单分区机型，系统与数据共用 C 盘」+ 具体动作，
  而不是笼统的「清理大文件」

### 3.5 顺手修掉的三处

1. `bigfile` 默认范围 `home` → `system`（系统盘），下拉新增「系统盘（含系统目录）」
2. 扫到 `hiberfil.sys` / `pagefile.sys` / `swapfile.sys` / `MEMORY.DMP` 时标灰 +
   「系统文件」标签，不可勾选，点击提示走「系统瘦身」
3. 云盘占位文件（`RECALL_ON_DATA_ACCESS`）不计入大文件列表，改成一行汇总
   「另有 N 个云端文件 X GB，占用本地 0」

---

## 四、性能预算（按实测外推）

| 场景 | 文件数 | 冷盘 | 热盘 |
|---|---|---|---|
| 本机（已用 44 GB） | 39 万 | 40 s | 14 s |
| 256 GB 单分区 | 60 万 | 60~90 s | 25 s |
| 512 GB 单分区 | 120 万 | 2~3 min | 50 s |
| 1 TB 单分区（半满） | 200 万 | 4~6 min | 1.5 min |

对策：标准模式首屏先出「前两层」（只遍历顶层 + 一层，几秒），出结果后后台继续深挖并
就地刷新；深度模式给明确提示「约 3 分钟」；结果缓存 10 分钟。

---

## 五、明确不做的事

| 候选 | 不做的原因 |
|---|---|
| USN Journal（`FSCTL_ENUM_USN_DATA`） | 只能拿到文件名和父目录，**没有文件大小**，算不了占用 |
| 直接读 `$MFT` | 要原始卷句柄，几乎必被安全软件拦，兼容性灾难 |
| 自动压缩卷 / 新建分区 | 动分区表 = 数据风险，只提供「打开磁盘管理」入口 |
| 自动删 `Windows\Installer` | 删完软件无法卸载/修复，收益几百 MB 风险极高 |
| 一键把 C 盘文件「搬到 D 盘」 | 软件路径写进注册表，搬完就坏；只能逐个应用提供官方迁移方式 |
| 抽 `TaskPage` 基类统一各页回调 | 见交接文档 6.12：各页 finished 里做的事差别很大，抽了更难读 |

---

## 六、分阶段

**阶段一（核心，建议先做）**
`core/dirstat.py` + `ui/page_space.py` 的「空间去向 + 目录占用」+ 下钻 + 缓存 +
大文件页默认范围改系统盘、标灰系统文件、过滤占位文件。约 500 行。

**阶段二**
`core/spacereport.py`（收口 13 项可回收清单，新增 5 项）+ 右侧可回收卡片 +
单分区识别 + 体检按绝对 GB 判定。

**阶段三（可选）**
空间变化历史：每次扫描存一份顶层目录快照，页面显示「近 30 天哪几个目录在涨」。
单分区用户最痛的点其实是「我没装东西，C 盘怎么又满了」，这一项直接回答它。

---

## 七、待确认

1. **入口**：升级现有「大文件」页（改名为「磁盘空间」，加分段）还是新增独立导航项？
   侧栏已有 15 项，倾向升级；但「C 盘空间」作为独立入口更醒目。
2. **首次打开是否自动扫描**：自动扫体验好，但 2 分钟的等待需要明确的进度与取消；
   手动点则每次都从零开始。
3. **阶段三要不要做**：空间变化历史需要长期落盘数据（每周一份小快照，几十 KB）。

---

## 八、落地情况（v0.0.33，按本方案实现）

三个待确认项的选择：**升级现有大文件页**、**三个阶段全做**、**先快后全**。

### 已实现

| 方案条目 | 落地 |
|---|---|
| 3.1 `core/dirstat.py` | 已实现。scandir 遍历、递归求和、跳 reparse point、硬链接去重、云端占位单列、根目录散落文件合并、depth=2 + 按需下钻、按介质选并行度 |
| 3.2 `core/spacereport.py` | 已实现 11 项（休眠 / 页面文件 / 转储 / Windows.old / 搜索索引 / 安装缓存 / 商店应用 / 虚拟磁盘 / 回收站 / 还原点 / 组件存储）。还原点与组件存储是「深入分析」才跑 |
| 3.3 `ui/page_space.py` | 已实现。大文件页升级为「磁盘空间」，分「空间分析 / 大文件」两段 |
| 3.4 单分区识别 | 已实现。`HealthSnapshot.partition_count`，详情写「（仅 1 个分区）」，建议文案区分单分区 |
| 3.4 体检按绝对 GB | 原本已是「百分比与绝对 GB 取较差者」，无需改 |
| 3.5 大文件页修正 | 已实现。默认范围改系统盘、系统文件标灰并写「（系统文件）」、云盘占位文件不列 |
| 阶段三 空间变化 | 已实现 `core/spacehistory.py` + 页面内的「近 30 天」区块 |

### 与原方案的三处偏差

1. **「先快后全」不是两遍扫描，是一遍 + 增量回传**。原方案写「先出前两层，后台继续」，
   实际做下来一遍扫描本来就是十几秒，两遍反而更慢。改成每扫完一个顶层目录就用
   `TaskCenter.partial` 回传一次，界面收到一行画一行 —— 用户 2 秒内看到 `C:/Windows`
   出现，比「前两层」更连续。
2. **迁移建议没有做成自动动作**。单分区机器没有第二块盘可迁；多分区机器上的
   「搬到 D 盘」要改注册表和软件路径，风险远大于收益。落地成：单分区提示条 +
   「怎么办」弹窗给有序动作清单，不给一键按钮。
3. **`Windows\Installer`、`System Volume Information`、WinSxS 内部只统计不给动作**。
   方案里本来就写了，实现时进一步把「动作按钮」限定在确实能安全执行的操作上。

### 实测数据（本机 C 盘 115 GB / 已用 44 GB / 39 万文件）

- 全盘扫描 12.5 s（文件系统缓存热）/ 40.6 s（冷），目录树节点 57 个，内存增量 0.5 MB
- 可回收清单 0.16 s，11 项
- 自检：`tools/check_space.py` 覆盖不变式（子项之和 = 总量、分类之和 = 已用）、
  历史增减、页面下钻与返回
