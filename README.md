# Windows系统管家

Windows 桌面系统管理工具。PySide6 写的单文件 exe，**纯本地、不联网**。

监控 + 清理 + 体检 + 一键优化，不做系统精简这类高风险操作。

## 功能

| 模块 | 说明 |
|---|---|
| 实时监控 | CPU / 内存 / 磁盘 / 网络 / 进程，趋势图支持悬停读数 |
| 系统信息 | SMBIOS 直读，45ms 出全部数据 |
| 垃圾清理 | 10 条规则，含社交软件缓存（微信/QQ/企业微信/钉钉，默认不勾选） |
| 重复文件 | 三段式哈希，可还原 |
| 大文件 | 阈值扫描 |
| 回收站 | 列表 / 按体积排序 / 还原 / 清空 |
| 启动项 | 注册表 / 启动文件夹 / 计划任务 |
| 计划任务 | 独立页，显示下次运行时间，可批量启停 |
| 注册表 | 4 类失效条目，**强制备份后**才允许删除 |
| 软件 | 已安装列表，可卸载 / 打开目录 |
| 体检 | 11 项加权评分，权重 100 |
| 一键优化 | 扫垃圾 → 清理 → 整理内存 → 出建议 |

## 架构

```
UI 层   ui/page_*.py        订阅 store 信号 → 派任务 → 渲染
状态层  core/store.py       单一数据源，apply() 是唯一写入口
编排层  core/task.py        串行任务中心（进度 / 取消 / 失败兜底）
能力层  core/*.py           cleaner / dup / registry / recycle / health …
```

一条数据的完整路径：

```
用户点扫描 → tasks.submit("junk", fn) → 串行执行 → 返回 JunkState
→ store.apply → junk_changed 信号 → 清理页/概览页/体检页同时更新
```

**页面不持有业务数据，也不自己起线程。**

## 运行

```bash
pip install -r requirements.txt
python main.py
```

依赖只有 `PySide6-Essentials` 和 `psutil`。

## 打包

```bash
python tools/build.py            # 用当前版本打包 + 归档
python tools/build.py --bump     # PATCH +1 再打包（发新版）
```

产物 `dist/Windows系统管家.exe`（约 36 MB，onefile，启动即申请管理员）。

## 版本与回滚

版本号唯一来源是 `core/version.py`。

```bash
python tools/releases.py              # 列出所有归档版本
python tools/releases.py --to 0.0.2   # 回滚到该版本
```

每次打包会把源码 zip + exe + manifest 存进 `releases/vX.Y.Z/`。
回滚会先把当前状态备份，所以回滚本身也能反悔。

## 安全设计

- 文件删除一律走回收站，不直接 `rm`
- 注册表删除前强制导出 `.reg` 备份，备份失败就取消删除
- 路径保护三道防线（`core/safety.py`），系统目录靠白名单显式放行
- 非管理员时自动跳过需要提权的清理项，避免「扫出来全删不掉」

## 文档

- [`docs/交接文档.md`](docs/交接文档.md) —— 架构、设计决策、踩过的坑
- [`docs/待接入排查与下步方案.md`](docs/待接入排查与下步方案.md) —— 未完成项与优先级
- [`docs/整改方案.md`](docs/整改方案.md) —— 重构路线图

## 当前版本

v0.0.3 · 内测
