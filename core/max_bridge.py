# -*- coding: utf-8 -*-
"""一键送进 3ds Max：inbox 文件协议 + 剪贴板降级。

为什么不用「拉起 3dsmaxbatch 跑脚本」：
    3dsmaxbatch 会开一个新进程、一个新场景，材质球落到那个临时场景里，用户当前
    正在做的场景什么都不会变——看着像成功，实际什么都没发生。所以这里只认
    「送进已经开着的那个 Max」。

两条通路，按可用性自动选：

1) inbox 协议（真·一键，需要在 Max 里装一次 listener）
   产品侧把 {ms_path, sha256, token} 写进 inbox/xxx.req.json；
   Max 里的 mf_max_listener.ms 每 400ms 轮询，校验通过就 fileIn，再写 xxx.res.json。
   产品侧等这个回执，超时判失败。listener 每轮续写 listener.hb 当心跳，
   产品侧据此判断「Max 端到底有没有装 listener」。

2) 剪贴板降级（零安装）
   listener 不在线时，把 `fileIn @"..."` 整条命令塞进剪贴板并尝试把 Max 拉到前台。
   用户切到 MAXScript 监听器按 Ctrl+E 再回车，等价于手动 Run Script，但少两步。

安全边界（这个模块会让 Max 执行脚本，必须设死）：
  * 只接受 .ms 后缀；
  * 只接受 allowed_root（导出目录）下的文件，防止借接口执行任意脚本；
  * req 带 sha256，listener 校验哈希不符拒绝执行（防写入后被掉包）；
  * req 带共享 token（inbox/token，首次生成，仅本机当前用户可读）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

# inbox 放用户本地数据目录，不放 %TEMP%（会被清理，且便于用户自己排查）
INBOX = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MarvisMaterialForge" / "max_inbox"
TOKEN_FILE = INBOX / "token"
HEARTBEAT = INBOX / "listener.hb"

# listener 每 400ms 写一次心跳；3 秒没更新按「没装」处理
HEARTBEAT_FRESH = 3.0
DEFAULT_TIMEOUT = 10.0


def _ensure_inbox() -> Path:
    INBOX.mkdir(parents=True, exist_ok=True)
    return INBOX


def token() -> str:
    """inbox 共享密钥：首次调用生成，之后读盘复用。"""
    _ensure_inbox()
    if TOKEN_FILE.exists():
        try:
            t = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if t:
                return t
        except OSError:
            pass
    t = uuid.uuid4().hex
    TOKEN_FILE.write_text(t, encoding="utf-8")
    return t


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def listener_alive(max_age: float = HEARTBEAT_FRESH) -> dict:
    """读心跳判断 Max 端 listener 是否在跑。

    返回值里带上 pid 与秒数，是为了在 UI 上说清楚「装了但没在跑」和「压根没装」
    的区别——这两种情况用户的处理动作不同。
    """
    if not HEARTBEAT.exists():
        return {"alive": False, "reason": "no_heartbeat", "pid": None, "age": None}
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except OSError:
        return {"alive": False, "reason": "stat_failed", "pid": None, "age": None}
    pid = None
    try:
        # utf-8-sig：Max 侧 File.WriteAllText 会写 BOM，直读会带 \ufeff 让 json 解析失败
        data = json.loads(HEARTBEAT.read_text(encoding="utf-8-sig") or "{}")
        pid = data.get("pid")
    except (OSError, ValueError):
        pass
    if age <= max_age:
        return {"alive": True, "reason": "fresh", "pid": pid, "age": round(age, 2)}
    return {"alive": False, "reason": "stale", "pid": pid, "age": round(age, 2)}


def _check_target(ms_path: Path, allowed_root: Path) -> None:
    """执行前把「能不能碰这个文件」一次判死，避免把校验散在调用方。"""
    if not ms_path.exists():
        raise ValueError("文件不存在：%s" % ms_path)
    if ms_path.suffix.lower() != ".ms":
        raise ValueError("只接受 .ms 脚本：%s" % ms_path.name)
    try:
        root = allowed_root.resolve()
        target = ms_path.resolve()
    except OSError as e:
        raise ValueError("路径无法解析：%s" % e)
    if root != target and root not in target.parents:
        raise ValueError("拒绝执行导出目录之外的文件：%s" % target)


def send(ms_path: str | Path, allowed_root: str | Path, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """把 .ms 送进 Max。返回 {mode, ok, msg, ...}，mode=inbox / clipboard。"""
    ms = Path(ms_path)
    _check_target(ms, Path(allowed_root))

    hb = listener_alive()
    if hb["alive"]:
        res = _send_inbox(ms, timeout=timeout)
        if res.get("ok"):
            return {"mode": "inbox", "ok": True,
                    "msg": "已在 Max 里执行：%s" % ms.name, "detail": res}
        # 有回执但报错，或没等到回执 → 不静默降级，如实把原因带出去
        return {"mode": "inbox", "ok": False,
                "msg": res.get("msg") or "Max 端没有回执", "detail": res,
                "heartbeat": hb}

    # 没装 listener（或心跳过期）→ 剪贴板
    cmd = 'fileIn @"%s"' % ms
    copied = _copy_clipboard(cmd)
    activated = _activate_max()
    if copied:
        tip = ("Max 端未安装一键桥（listener）。命令已复制到剪贴板：切到 3ds Max 的 "
               "MAXScript 监听器（F11），Ctrl+E 粘贴后回车即可执行。"
               "装一次 listener 后就能真正一键：详见 docs/max-bridge.md。")
        return {"mode": "clipboard", "ok": True, "msg": tip,
                "command": cmd, "activated": activated, "heartbeat": hb}
    return {"mode": "clipboard", "ok": False,
            "msg": "剪贴板写入失败，请手动复制这条命令到 MAXScript 监听器：%s" % cmd,
            "command": cmd, "heartbeat": hb}


def _send_inbox(ms: Path, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """inbox 通路：写 req → 等 res。超时按失败返回，不假装成功。"""
    box = _ensure_inbox()
    job = uuid.uuid4().hex
    req = box / ("%s.req.json" % job)
    res = box / ("%s.res.json" % job)
    payload = {
        "ms_path": str(ms),
        "sha256": sha256_of(ms),
        "token": token(),
        "ts": time.time(),
        "pid": os.getpid(),
    }
    tmp = req.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(req)          # 原子落地，避免 listener 读到半截 JSON

    deadline = time.time() + timeout
    while time.time() < deadline:
        if res.exists():
            try:
                data = json.loads(res.read_text(encoding="utf-8-sig") or "{}")
            except (OSError, ValueError) as e:
                return {"ok": False, "msg": "Max 端回执无法解析：%s" % e}
            finally:
                for p in (res, req):
                    try:
                        p.unlink()
                    except OSError:
                        pass
            return {"ok": bool(data.get("ok")), "msg": data.get("msg") or "",
                    "job": job, "raw": data}
        time.sleep(0.1)

    for p in (req, res):
        try:
            p.unlink()
        except OSError:
            pass
    return {"ok": False,
            "msg": "等 %.0f 秒没有收到 Max 回执（listener 可能卡在加载大贴图，或 Max 正忙）" % timeout,
            "job": job}


# ------------------------------------------------------------------
# 剪贴板 / 前台激活（纯 ctypes，不依赖 PowerShell，中文路径不乱码）
# ------------------------------------------------------------------
def _copy_clipboard(text: str) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalLock.restype = ctypes.c_void_p

        buf = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buf)
        handle = k32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = k32.GlobalLock(handle)
        if not ptr:
            return False
        ctypes.memmove(ptr, buf, size)
        k32.GlobalUnlock(handle)
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            return bool(u32.SetClipboardData(CF_UNICODETEXT, handle))
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


def _activate_max() -> bool:
    """把 3ds Max 主窗口拉到前台。找不到就当没做成，由调用方降级提示。"""
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not u32.IsWindowVisible(hwnd):
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, b, n + 1)
                cls = ctypes.create_unicode_buffer(256)
                u32.GetClassNameW(hwnd, cls, 256)
                title = b.value or ""
                cname = (cls.value or "").lower()
                if "3ds max" in title.lower() or "3dsmax" in cname:
                    found.append(hwnd)
            return True

        u32.EnumWindows(_cb, 0)
        if not found:
            return False
        h = found[0]
        u32.ShowWindow(h, 9)          # SW_RESTORE
        return bool(u32.SetForegroundWindow(h))
    except Exception:
        return False


def install_listener(ms_template: str | Path) -> dict:
    """把 listener 的装载命令准备出来（供 UI / 文档展示，不自动执行）。"""
    p = Path(ms_template)
    if not p.exists():
        return {"ok": False, "msg": "listener 脚本不存在：%s" % p}
    return {"ok": True,
            "command": 'fileIn @"%s"' % p,
            "path": str(p),
            "msg": "在 3ds Max 的 MAXScript 监听器里执行上面这条命令，即可装上监听"} 


def clipboard_text(ms_path: str | Path) -> str:
    """给 UI / 测试用的纯函数：生成要粘贴的命令行。"""
    return 'fileIn @"%s"' % Path(ms_path)
