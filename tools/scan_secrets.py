"""发版前扫描：仓库里有没有不该上传的东西。

GitHub 上的仓库是公开的，一旦推上去就不能算「删掉了」——
提交历史里的每一条都还在，别人 git log 就能翻到。所以这件事要在推之前做，
而且必须扫**全历史**，不能只看当前工作区。

三类检查：

1. **凭据**（高危）：云 AK/SK、GitHub token、私钥、JWT、硬编码密码。
   命中即 exit 1，不给人肉忽略的机会。
2. **敏感文件**（高危）：`.env` / `.pem` / `.p12` / `config.json` 这类
   本身就不该出现在公开仓库的文件，内容扫不出来也要拦。
3. **个人信息**（提示）：绝对路径里的用户名、邮箱、手机号、内网 IP。
   不致命，但公开仓库没必要带。

用法：

    python tools/scan_secrets.py                 # 扫本地全部历史 + 工作区
    python tools/scan_secrets.py --quiet         # 只打摘要，不发明细
    python tools/scan_secrets.py --remote        # 扫 GitHub 上真正推上去的内容
    python tools/scan_secrets.py --remote --repo owner/name --ref main

退出码：有高危命中 → 1；只有提示级 → 0；干净 → 0；跑不起来 → 2。

实现说明：本地模式直接读 git 对象（`rev-list --objects --all` + `cat-file blob`），
不 checkout、不依赖工作区状态，文件被删掉也漏不了历史里的残留。远端模式拉
codeload 的 tar.gz（一次请求），因为 contents API 未授权只有 60 次/小时。
二进制按前 8 KB 是否含 NUL 判断，跳过。
"""

from __future__ import annotations

import re
import subprocess
import sys

# (名称, 正则, 高危?) —— 高危 = 真凭据，提示 = 仅暴露身份
RULES: list[tuple[str, str, bool]] = [
    # ---- 凭据 ----
    ("阿里云 AccessKey", r"LTAI[0-9A-Za-z]{12,}", True),
    ("AWS AccessKey", r"AKIA[0-9A-Z]{16}", True),
    ("腾讯云 SecretId", r"AKID[0-9A-Za-z]{26,}", True),
    ("GitHub Token", r"gh[pousr]_[A-Za-z0-9]{30,}", True),
    ("GitHub 细粒度 Token", r"github_pat_[A-Za-z0-9_]{30,}", True),
    ("大模型 API Key", r"\bsk-[A-Za-z0-9_\-]{32,}", True),
    ("Slack Token", r"xox[baprs]-[0-9A-Za-z\-]{10,}", True),
    ("私钥文件", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", True),
    ("JWT", r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", True),
    (
        "硬编码密码/密钥",
        r"(?i)\b(?:password|passwd|pwd|secret|api_?key|access_?key|secret_?key"
        r"|auth_?token|private_?token)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
        True,
    ),
    # ---- 个人信息 ----
    ("邮箱地址", r"[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}", False),
    ("手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)", False),
    ("内网 IPv4", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b", False),
    ("用户名绝对路径", r"[A-Za-z]:/[Uu]sers/(?!Public|Default|<|%|\.\.\.)[^/\\\"'\s]{2,}/", False),
    ("用户名绝对路径(反斜杠)", r"[A-Za-z]:\\\\?[Uu]sers\\\\?(?!Public|Default)[^\\\"'\s]{2,}\\\\", False),
]

# 已知安全的字面量。不留白名单的话每次都会刷出同一批噪音，
# 人一旦习惯忽略输出，真泄露也会被顺手略过。
SAFE_FRAGMENTS = {
    "10.255.255.255",  # 探出口网卡用的不可达地址，不会真发包（core/sysinfo.py）
    "255.255.255.255",
    "127.0.0.1",
    "0.0.0.0",
    "C:/Users/Stub/",  # 预览脚本里的假路径
    "C:/Users/Public/",
    "C:/Users/User/",  # 文档里的示意路径
}

# 占位值不算泄露。这些是文档里教人「填你自己的」时的正常写法。
PLACEHOLDERS = {
    "your_token_here",
    "your-token-here",
    "xxx",
    "changeme",
    "placeholder",
    "YOUR_AK",
    "YOUR_SK",
    "<your-token>",
    "${GH_TOKEN}",
    "os.environ",
}

SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".exe", ".dll", ".zip", ".7z", ".pdf", ".woff", ".woff2", ".ttf",
    ".pyc", ".pyd", ".so",
}

# 不该出现在公开仓库里的文件名。内容可能扫不出东西（`.p12` 是二进制的），
# 但文件本身就不该在。
BAD_NAMES = (
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials", "credentials.json", "secrets.json", "config.json",
    "settings.json", "token.txt", "accesskey.csv",
)
BAD_SUFFIX = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks", ".ppk", ".log")

MAX_BLOB = 2 * 1024 * 1024  # 单个 blob 上限，超过的基本是数据文件，不扫


def _git(*args: str) -> bytes:
    """跑一条 git 命令。git 不在或没仓库时抛 RuntimeError，由 main 兜。"""
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout


def _mask(text: str) -> str:
    """遮掉中间：AK 这类东西打日志时也不该出全量。"""
    text = text.strip()
    if len(text) <= 10:
        return text[:2] + "*" * max(0, len(text) - 2)
    return f"{text[:6]}{'*' * 8}{text[-4:]}"


def _is_text(blob: bytes) -> bool:
    return b"\x00" not in blob[:8192]


def _iter_blobs() -> tuple[list[tuple[str, bytes]], list[str]]:
    """全历史里的所有文本 blob：(路径, 内容) + 出现过的所有文件名。

    文件名单独返回，是因为 `.p12` 这类二进制文件读不出内容，但文件本身
    就不该在仓库里 —— 只看内容会漏掉它。
    """
    out: list[tuple[str, bytes]] = []
    names: list[str] = []
    seen: set[str] = set()
    for line in _git("rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue  # 目录对象没有路径
        sha, path = parts
        names.append(path)
        low = path.lower()
        if any(low.endswith(ext) for ext in SKIP_EXT):
            continue
        key = f"{sha}:{path}"
        if key in seen:
            continue
        seen.add(key)
        try:
            blob = _git("cat-file", "blob", sha)
        except RuntimeError:
            continue  # tree 对象，跳过
        if len(blob) > MAX_BLOB or not _is_text(blob):
            continue
        out.append((path, blob))
    return out, names


def _iter_worktree() -> tuple[list[tuple[str, bytes]], list[str]]:
    """工作区里已跟踪 + 未跟踪但没被忽略的文件。"""
    out: list[tuple[str, bytes]] = []
    names: list[str] = []
    raw = _git("ls-files", "-co", "--exclude-standard")
    for path in raw.decode("utf-8", "replace").splitlines():
        names.append(path)
        low = path.lower()
        if any(low.endswith(ext) for ext in SKIP_EXT):
            continue
        try:
            with open(path, "rb") as fh:
                blob = fh.read(MAX_BLOB + 1)
        except OSError:
            continue
        if len(blob) > MAX_BLOB or not _is_text(blob):
            continue
        out.append((path, blob))
    return out, names


def _remote_blobs(repo: str, ref: str) -> tuple[list[tuple[str, bytes]], list[str]]:
    """下载 GitHub 上的仓库快照（tar.gz，一个请求），逐个文件读内容。

    为什么拉 tarball 而不是走 contents API：API 未授权只能 60 次/小时，
    而这个仓库有两百多个文件。tarball 一次拿全，且拿到的是**已推送**的内容，
    和本地工作区不一定相同 —— 检查「GitHub 上到底有什么」必须看这一份。
    """
    import io
    import tarfile
    import urllib.request

    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{ref}"
    req = urllib.request.Request(url, headers={"User-Agent": "windows-manager-secret-scan"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()

    out: list[tuple[str, bytes]] = []
    names: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name.split("/", 1)[-1]  # 去掉顶层目录名
            names.append(name)
            low = name.lower()
            if any(low.endswith(ext) for ext in SKIP_EXT):
                continue
            if member.size > MAX_BLOB:
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            blob = fh.read()
            if not _is_text(blob):
                continue
            out.append((name, blob))
    return out, names


def scan(blobs: list[tuple[str, bytes]]) -> list[tuple[bool, str, str, int, str]]:
    """返回 [(高危?, 规则名, 路径, 行号, 遮罩后的证据)]。"""
    hits: list[tuple[bool, str, str, int, str]] = []
    compiled = [(name, re.compile(pattern), high) for name, pattern, high in RULES]
    for path, blob in blobs:
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue  # GBK 等编码的文件跳过，里面有中文注释是常态
        for lineno, line in enumerate(text.splitlines(), 1):
            if len(line) > 2000:
                continue  # 压缩过的 JS/JSON 行，误报率高且没排查价值
            for name, pattern, high in compiled:
                for match in pattern.finditer(line):
                    frag = match.group(0)
                    if frag.strip() in PLACEHOLDERS or any(p in frag for p in PLACEHOLDERS):
                        continue
                    if any(safe in frag for safe in SAFE_FRAGMENTS):
                        continue
                    if high and len(frag) < 8:
                        continue
                    hits.append((high, name, path, lineno, _mask(frag)))
    return hits


def check_names(names: list[str]) -> list[tuple[bool, str, str, int, str]]:
    """按文件名查：不该进仓库的文件（密钥容器、本地配置、日志）。"""
    hits: list[tuple[bool, str, str, int, str]] = []
    for path in sorted(set(names)):
        base = path.rsplit("/", 1)[-1].lower()
        if base in BAD_NAMES:
            hits.append((True, "敏感文件", path, 0, base))
        elif base.endswith(BAD_SUFFIX):
            hits.append((True, "敏感文件", path, 0, base))
    return hits


def hygiene() -> list[str]:
    """仓库卫生：该忽略的东西有没有被跟踪。"""
    problems: list[str] = []
    tracked = _git("ls-files").decode("utf-8", "replace").splitlines()
    must_ignore = {
        "config.json": "本地配置（含机器相关设置）",
        "settings.json": "本地设置",
    }
    for path in tracked:
        low = path.lower()
        if low in must_ignore:
            problems.append(f"{path} 被跟踪了（{must_ignore[low]}），应从仓库移除")
        if low.endswith(".log"):
            problems.append(f"{path} 是日志文件，不应入库")
        if low.startswith(("dist/", "dist_pending/", "releases/", "build/")):
            problems.append(f"{path} 是打包产物，不应入库")
        if low.startswith(".workbuddy") or low.startswith(".workbuddy-ai"):
            problems.append(f"{path} 是本地工具链数据，不应入库")
    return problems


def _arg(name: str, default: str) -> str:
    """取 `--name value` 形式的参数。"""
    if name in sys.argv:
        index = sys.argv.index(name)
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return default


def _fmt(hit: tuple[bool, str, str, int, str]) -> str:
    is_high, name, path, lineno, frag = hit
    tag = "高危" if is_high else "提示"
    loc = f"{path}:{lineno}" if lineno else path
    return f"  [{tag}] {name}  {loc}  {frag}"


def _dedup(hits: list[tuple[bool, str, str, int, str]]) -> list[tuple[bool, str, str, int, str]]:
    seen: set[tuple[str, str, int]] = set()
    uniq = []
    for hit in hits:
        key = (hit[1], hit[2], hit[3])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(hit)
    return uniq


def _report(
    scope: str,
    uniq: list[tuple[bool, str, str, int, str]],
    quiet: bool,
    problems: list[str] | None = None,
) -> int:
    """统一输出 + 退出码。"""
    high = [h for h in uniq if h[0]]
    low = [h for h in uniq if not h[0]]

    print(f"扫描范围：{scope}")
    if uniq and not quiet:
        print("\n命中明细：")
        for hit in sorted(uniq, key=lambda h: (not h[0], h[2], h[3])):
            print(_fmt(hit))

    if problems:
        print("\n仓库卫生：")
        for problem in problems:
            print(f"  - {problem}")

    print()
    if high:
        print(f"✗ 发现 {len(high)} 处高危项。")
        print("  若已推送过：先吊销并轮换凭据，再决定要不要改写历史。")
        return 1
    if low:
        print(f"✓ 没有凭据泄露；有 {len(low)} 处提示级信息（见上）。")
    else:
        print("✓ 干净：没有凭据，也没有敏感文件。")
    if problems:
        print(f"  但仓库卫生有 {len(problems)} 条待修。")
    return 0


def main() -> int:
    quiet = "--quiet" in sys.argv

    # --remote：扫 GitHub 上真实存在的文件（已推送的内容，不是本地工作区）
    if "--remote" in sys.argv:
        repo = _arg("--repo", "jtnetcc/windows-manager")
        ref = _arg("--ref", "main")
        import urllib.error

        try:
            blobs, names = _remote_blobs(repo, ref)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"拉不到远端快照：{exc}")
            return 2
        print(f"远端仓库：github.com/{repo}@{ref}\n")
        hits = scan(blobs) + check_names(names)
        return _report(f"GitHub 快照 {len(names)} 个文件（{len(blobs)} 个文本）", _dedup(hits), quiet)

    try:
        history, history_names = _iter_blobs()
        worktree, worktree_names = _iter_worktree()
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"跑不动 git：{exc}")
        return 2

    # 工作区内容通常已经包含在历史里，按路径去重不吃亏；这里分开扫再合并，
    # 是为了让「还没提交但已经进暂存区」的东西也能被抓到。
    hits = scan(history) + check_names(history_names + worktree_names)
    worktree_paths = {path for path, _ in worktree}
    hits += [h for h in scan(worktree) if h[2] in worktree_paths]

    scope = f"历史 {len(history)} 个文本对象 · 工作区 {len(worktree)} 个文件"
    return _report(scope, _dedup(hits), quiet, hygiene())


if __name__ == "__main__":
    sys.exit(main())
