# -*- coding: utf-8 -*-
"""Edge profile 生命周期治理（v0.8.4）

背景
----
ui_check.py / ui_solid_check.py / capture_presets.py 都用 headless Edge + CDP 做界面断言。
历史实现有三个泄漏源，跑一天能堆出 1.5GB：

1. ``proc.terminate()`` 只杀 Edge **主进程**，GPU/renderer/crashpad/utility 等子进程
   继续存活并锁住 ``--user-data-dir``，下一轮 start 时新进程会「挂到旧实例」上，
   调试端口永不监听 -> CDP 失败。
2. 失败时的兜底策略是「换一份带时间戳的干净 profile 重跑」，但这份 profile
   从不回收，于是每次失败都永久多出约 76MB。
3. 兜底分支重新 spawn 子进程，父进程 finally 又去 terminate 自己那一个实例，
   残留子进程越积越多。

本模块提供四个原语，供上述脚本复用：

- ``kill_tree(pid)``            按进程树强杀（taskkill /T /F），杜绝孤儿
- ``kill_by_profile(dir)``      精确杀掉命令行里 ``--user-data-dir=<该目录>`` 的 Edge 进程
- ``safe_rmtree(path)``         容忍占用/只读的删除（先杀占用、失败重试）
- ``reap_stale_profiles()``     清理 ``temp/edge_profile_*`` 历史残留（排除正在用的）

安全约束：只匹配本 skill ``temp`` 下的 profile 路径，绝不碰用户日常 Edge 实例。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMP_DIR = SKILL_ROOT / "temp"
DEFAULT_PROFILE = TEMP_DIR / "edge_profile"

# 本进程内登记、需要在退出时回收的临时 profile
_EPHEMERAL: "set[str]" = set()


# --------------------------------------------------------------------------- #
# 基础原语
# --------------------------------------------------------------------------- #
def _run_ps(script: str, timeout: int = 30) -> str:
    """跑一段 PowerShell，统一按 UTF-8 取回 stdout。"""
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ""
    return p.stdout.decode("utf-8", "replace")


def _norm(p) -> str:
    """路径归一：绝对化 + 分隔符统一 + 大小写折叠（Windows 语义）。"""
    return os.path.normcase(os.path.abspath(str(p)))


def edge_pids(profile=None) -> "list[int]":
    """返回命令行中 user-data-dir 等于 profile 的 Edge 进程 PID（profile=None 时返全部）。"""
    out = _run_ps(
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
    )
    want = _norm(profile) if profile else None
    pids = []
    for line in out.splitlines():
        pid_s, _, cmdline = line.partition("\t")
        pid_s = pid_s.strip()
        if not pid_s.isdigit():
            continue
        if want is None:
            pids.append(int(pid_s))
            continue
        val = _profile_from_cmdline(cmdline)
        if val is not None and _norm(val) == want:
            pids.append(int(pid_s))
    return sorted(set(pids))


# profile 路径本身可能含空格（如 "D:\\Marvis skill\\..."），命令行有两种写法：
#   主进程  "--user-data-dir=D:\\Marvis skill\\temp\\edge_profile"
#   子进程  --user-data-dir="D:\\Marvis skill\\temp\\edge_profile"
# 因此按「值是否被引号包裹」分别提取，不能简单按空格切分。
_RE_PROF_DQ = re.compile(r'--user-data-dir="([^"]*)"', re.I)
_RE_PROF_WRAPPED = re.compile(r'"--user-data-dir=([^"]*)"', re.I)
_RE_PROF_PLAIN = re.compile(r"--user-data-dir=(\S+)")


def _profile_from_cmdline(cmdline: str):
    """从 Edge 命令行里取出 --user-data-dir 的值；取不到返回 None。"""
    for rx in (_RE_PROF_DQ, _RE_PROF_WRAPPED, _RE_PROF_PLAIN):
        m = rx.search(cmdline)
        if m:
            return m.group(1).rstrip("\\")
    return None


def kill_tree(pid: int, timeout: int = 10) -> bool:
    """按进程树强杀（含子进程）。返回是否执行了 kill。"""
    if not pid:
        return False
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        try:
            os.kill(pid, 9)
            return True
        except Exception:
            return False


def kill_proc(proc, timeout: int = 10) -> None:
    """杀 Popen 对象及其整棵子树；替代裸 proc.terminate()。"""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        pass
    kill_tree(getattr(proc, "pid", 0), timeout=timeout)
    try:
        proc.wait(timeout=timeout)
    except Exception:
        pass


def kill_by_profile(profile=None, settle: float = 1.0) -> int:
    """杀掉占用指定 profile 的全部 Edge 进程，返回杀掉的 PID 数。

    这是「固定 profile 复用」方案的必要前置：不清残留实例，
    新进程会被 Edge 单实例机制接管，调试端口永远不监听。
    """
    pids = edge_pids(profile)
    # 先杀非 crashpad 的主干，再兜底全杀
    for pid in pids:
        kill_tree(pid)
    if pids and settle:
        time.sleep(settle)
        for pid in edge_pids(profile):
            kill_tree(pid)
        time.sleep(0.3)
    return len(pids)


def safe_rmtree(path, retries: int = 3) -> bool:
    """删目录，容忍被占用：先杀相关 Edge，再重试。"""
    p = Path(path)
    if not p.exists():
        return True
    for i in range(retries):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return True
        if i == 0:
            kill_by_profile(p, settle=0.5)
        time.sleep(0.5 * (i + 1))
        # Windows 只读属性会导致删除失败，清掉只读位再试
        for root, dirs, files in os.walk(p):
            for n in files:
                try:
                    os.chmod(os.path.join(root, n), 0o666)
                except Exception:
                    pass
    return not p.exists()


# --------------------------------------------------------------------------- #
# 临时 profile 登记（兜底分支用）
# --------------------------------------------------------------------------- #
def make_ephemeral_profile(tag: str = "edge_profile") -> Path:
    """新建一份一次性 profile，并登记为退出时自动回收。"""
    p = TEMP_DIR / time.strftime(tag + "_%H%M%S")
    p.mkdir(parents=True, exist_ok=True)
    _EPHEMERAL.add(_norm(p))
    return p


def release_ephemeral(profile=None) -> None:
    """回收登记过的一次性 profile：先杀进程再删目录。"""
    targets = [_norm(profile)] if profile is not None else list(_EPHEMERAL)
    for t in targets:
        kill_by_profile(t, settle=0.5)
        safe_rmtree(t)
        _EPHEMERAL.discard(t)


# --------------------------------------------------------------------------- #
# 历史残留巡检
# --------------------------------------------------------------------------- #
def reap_stale_profiles(keep=None, min_age_s: int = 0) -> "list[tuple[str, float]]":
    """清理 temp 下所有 ``edge_profile_*`` 残留，返回 [(名字, 释放MB)]。

    keep: 需要保留的 profile 路径（通常传当前正在用的那份）。
    min_age_s: 只清理最后修改早于该秒数的目录（默认 0 = 全清）。
    """
    keep_norm = _norm(keep) if keep else None
    freed = []
    if not TEMP_DIR.is_dir():
        return freed
    now = time.time()
    for entry in sorted(TEMP_DIR.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("edge_profile_"):
            continue
        if keep_norm and _norm(entry) == keep_norm:
            continue
        try:
            newest = max((f.stat().st_mtime for f in entry.rglob("*") if f.is_file()),
                         default=entry.stat().st_mtime)
        except Exception:
            newest = now
        if min_age_s and (now - newest) < min_age_s:
            continue
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        kill_by_profile(entry, settle=0.3)
        if safe_rmtree(entry):
            freed.append((entry.name, round(size / 1024 / 1024, 1)))
    return freed


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    if mode == "status":
        profs = [p for p in TEMP_DIR.glob("edge_profile*") if p.is_dir()] if TEMP_DIR.is_dir() else []
        for p in sorted(profs):
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            print("%-28s %8.1f MB  占用进程=%d"
                  % (p.name, size / 1024 / 1024, len(edge_pids(p))))
    elif mode == "reap":
        res = reap_stale_profiles(keep=(os.environ.get("MF_EDGE_PROFILE") or DEFAULT_PROFILE))
        total = sum(m for _, m in res)
        for n, m in res:
            print("清理 %-28s %7.1f MB" % (n, m))
        print("共清理 %d 个 profile，释放 %.1f MB" % (len(res), total))
    elif mode == "kill":
        target = os.environ.get("MF_EDGE_PROFILE") or DEFAULT_PROFILE
        n = kill_by_profile(target)
        print("已终止 %d 个占用 %s 的 Edge 进程" % (n, target))
    else:
        print("用法: python _edge_util.py [status|reap|kill]")
