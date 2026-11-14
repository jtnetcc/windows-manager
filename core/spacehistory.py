"""空间变化历史：每次扫完记一笔顶层目录快照，回答「我没装东西，C 盘怎么又满了」。

只存顶层目录的体积（十几个数字），一份快照几百字节，一天最多一条。
存整个目录树的话，一个月就是几百 MB，得不偿失。

页面只问两个问题：哪个目录在涨、涨了多少。所以这里不做时间序列插值，
就是「拿最早那份和最新那份比」。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

from .config import config_dir
from . import applog

PATH = config_dir() / "space_history.json"
VERSION = 1
KEEP_DAYS = 120          # 留四个月，再多对「最近怎么涨的」没有帮助
ROOT_FILES = "（根目录散落文件）"

_LOCK = threading.Lock()


@dataclass
class Snapshot:
    at: float
    used: int
    total: int
    dirs: dict[str, int] = field(default_factory=dict)


@dataclass
class Growth:
    name: str
    delta: int
    now: int
    before: int

    @property
    def up(self) -> bool:
        return self.delta > 0


def _load() -> list[Snapshot]:
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = []
    for item in raw.get("items", []):
        try:
            items.append(
                Snapshot(
                    at=float(item["at"]),
                    used=int(item.get("used", 0)),
                    total=int(item.get("total", 0)),
                    dirs={k: int(v) for k, v in item.get("dirs", {}).items()},
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    items.sort(key=lambda s: s.at)
    return items


def _save(items: list[Snapshot]) -> None:
    payload = {
        "version": VERSION,
        "items": [
            {"at": s.at, "used": s.used, "total": s.total, "dirs": s.dirs}
            for s in items
        ],
    }
    try:
        PATH.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError:
        applog.silenced("spacehistory._save")


def record(root, used: int, total: int) -> None:
    """把一次扫描的顶层结果记进历史。同一天重复扫描只留最后一次。

    目录名用显示名而不是完整路径：路径里带盘符和用户名，
    换台机器或改了用户名就对不上了。
    """
    dirs: dict[str, int] = {}
    for node in getattr(root, "children", []):
        name = node.name if node.is_dir else ROOT_FILES
        dirs[name] = dirs.get(name, 0) + node.size
    if not dirs:
        return

    snap = Snapshot(at=time.time(), used=used, total=total, dirs=dirs)
    with _LOCK:
        items = _load()
        # 同一天里连着扫几次只留最后一份（覆盖）；但隔了两小时以上就单独记一笔，
        # 这样「上午扫过一次、晚上又扫」也能看出当天谁在长。
        if (
            items
            and _same_day(items[-1].at, snap.at)
            and snap.at - items[-1].at < 2 * 3600
        ):
            items[-1] = snap
        else:
            items.append(snap)
        cutoff = time.time() - KEEP_DAYS * 86400
        items = [s for s in items if s.at >= cutoff][-400:]
        _save(items)


def _same_day(first: float, second: float) -> bool:
    a = time.localtime(first)
    b = time.localtime(second)
    return (a.tm_year, a.tm_yday) == (b.tm_year, b.tm_yday)


def history(days: int = 30) -> list[Snapshot]:
    with _LOCK:
        items = _load()
    if not items:
        return []
    cutoff = time.time() - days * 86400
    window = [s for s in items if s.at >= cutoff]
    return window or items[-1:]


def growth(days: int = 30, limit: int = 6) -> list[Growth]:
    """最近 `days` 天里涨得最多的目录。数据不足两份就返回空。"""
    window = history(days)
    if len(window) < 2:
        return []
    first, last = window[0], window[-1]
    out: list[Growth] = []
    for name, size in last.dirs.items():
        before = first.dirs.get(name, 0)
        delta = size - before
        if abs(delta) < 32 * 1024 ** 2:   # 32 MB 以下算噪音
            continue
        out.append(Growth(name=name, delta=delta, now=size, before=before))
    out.sort(key=lambda g: -g.delta)
    return out[:limit]


def span(days: int = 30) -> str:
    """「8 月 12 日至今」这种描述，给界面当副标题。"""
    window = history(days)
    if len(window) < 2:
        return "再扫一次就能看到变化"
    a = time.strftime("%m-%d", time.localtime(window[0].at))
    b = time.strftime("%m-%d", time.localtime(window[-1].at))
    return f"{a} 至今（{len(window)} 次记录）"


def clear() -> None:
    with _LOCK:
        try:
            PATH.unlink()
        except OSError:
            applog.silenced("spacehistory.clear")
