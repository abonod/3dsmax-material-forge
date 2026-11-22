# -*- coding: utf-8 -*-
"""max_bridge 自测：不需要真 3ds Max 在线，用「假 listener」跑通协议与安全边界。

为什么要这一层：
    inbox 协议的两端跨进程（Python 产品 ↔ MAXScript 监听器），最容易坏的地方
    不是业务逻辑，而是「写盘时机、原子性、超时、以及别人能不能借这个口子执行
    任意脚本」。这些都能在纯 Python 里验完，不必每次都开 Max。

跑法：python scripts/selftest_max_bridge.py
退出码 0 = 全过；非 0 = 有失败项（会被 run_all_checks 当门禁失败）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import max_bridge  # noqa: E402

CASES: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CASES.append((name, ok, detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  " + detail) if detail and not ok else ""))


def _sandbox() -> Path:
    """每个用例独立 inbox，避免污染真实 %LOCALAPPDATA%"""
    d = Path(tempfile.mkdtemp(prefix="mf_bridge_"))
    max_bridge.INBOX = d
    max_bridge.TOKEN_FILE = d / "token"
    max_bridge.HEARTBEAT = d / "listener.hb"
    return d


def _fake_listener(box: Path, *, reply: bool = True, tamper: bool = False) -> threading.Thread:
    """模拟 Max 端：读到 req 就写 res（或故意不回，模拟 Max 卡住）"""

    def _run():
        deadline = time.time() + 6
        while time.time() < deadline:
            reqs = sorted(box.glob("*.req.json"))
            if reqs:
                req = reqs[0]
                try:
                    data = json.loads(req.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    time.sleep(0.05)
                    continue
                job = req.name[: -len(".req.json")]
                if tamper:
                    data["sha256"] = "0" * 64
                ok = data.get("token") == max_bridge.token() and data.get("sha256") == max_bridge.sha256_of(Path(data["ms_path"]))
                if reply:
                    out = {"ok": ok, "msg": "执行成功" if ok else "token 或哈希不匹配"}
                    (box / (job + ".res.json")).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
                try:
                    req.unlink()
                except OSError:
                    pass
                return
            time.sleep(0.05)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def main() -> int:
    print("max_bridge 自测：")

    # 1. token 稳定且落盘
    box = _sandbox()
    t1 = max_bridge.token()
    t2 = max_bridge.token()
    check("token 幂等且写盘", t1 == t2 and len(t1) == 32 and max_bridge.TOKEN_FILE.exists())

    # 2. 心跳：无文件 → 不在线；新鲜 → 在线；过期 → 不在线
    check("无心跳判离线", max_bridge.listener_alive()["alive"] is False)
    max_bridge.HEARTBEAT.write_text('{"pid": 123}', encoding="utf-8")
    alive = max_bridge.listener_alive()
    check("新鲜心跳判在线并带上 pid", alive["alive"] is True and alive["pid"] == 123)
    old = time.time() - 60
    import os as _os
    _os.utime(max_bridge.HEARTBEAT, (old, old))
    stale = max_bridge.listener_alive()
    check("过期心跳判离线且原因可辨", stale["alive"] is False and stale["reason"] == "stale")

    # 3. listener 离线 → 自动走剪贴板，且命令正确
    box = _sandbox()
    root = Path(tempfile.mkdtemp(prefix="mf_root_"))
    script = root / "mat.ms"
    script.write_text("-- empty\n", encoding="utf-8")
    res = max_bridge.send(script, root, timeout=1)
    check("离线时降级到剪贴板", res["mode"] == "clipboard" and Path(script) == Path(script))
    check("剪贴板命令含 fileIn 与完整路径",
          res.get("command", "").startswith("fileIn @\"") and str(script) in res.get("command", ""))

    # 4. listener 在线 → 走 inbox 并拿到回执
    box = _sandbox()
    max_bridge.HEARTBEAT.write_text('{"pid": 1}', encoding="utf-8")
    _fake_listener(box)
    res = max_bridge.send(script, root, timeout=4)
    check("在线时走 inbox 且执行成功", res["mode"] == "inbox" and res["ok"] is True, str(res))
    check("回执与请求文件被清理", not list(box.glob("*.req.json")) and not list(box.glob("*.res.json")))

    # 5. 哈希被篡改 → Max 端拒绝，产品如实报失败（不假装成功）
    box = _sandbox()
    max_bridge.HEARTBEAT.write_text('{"pid": 1}', encoding="utf-8")
    _fake_listener(box, tamper=True)
    res = max_bridge.send(script, root, timeout=4)
    check("哈希不符时上报失败", res["ok"] is False and "不匹配" in res.get("msg", ""), str(res))

    # 6. Max 端不回应 → 超时失败，不悬挂
    box = _sandbox()
    max_bridge.HEARTBEAT.write_text('{"pid": 1}', encoding="utf-8")
    _fake_listener(box, reply=False)
    t0 = time.time()
    res = max_bridge.send(script, root, timeout=1.5)
    cost = time.time() - t0
    check("无回执时按超时失败返回", res["ok"] is False and "回执" in res.get("msg", ""), str(res))
    check("超时受 timeout 约束：%s 秒" % round(cost, 2), 1.2 <= cost <= 3.0)

    # 7. 安全边界：目录外文件 / 非 .ms / 不存在 → 一律拒绝
    outside = Path(tempfile.mkdtemp(prefix="mf_outside_")) / "evil.ms"
    outside.write_text("-- evil\n", encoding="utf-8")
    for label, target in (("导出目录之外", outside),
                          ("非 .ms 后缀", root / "note.txt"),
                          ("不存在的文件", root / "ghost.ms")):
        try:
            if label == "非 .ms 后缀":
                (root / "note.txt").write_text("x", encoding="utf-8")
            max_bridge.send(target, root, timeout=1)
            check("拒绝" + label, False, "竟然没报错")
        except ValueError:
            check("拒绝" + label, True)

    # 8. 剪贴板命令生成（纯函数，不碰系统剪贴板）
    cmd = max_bridge.clipboard_text(Path(r"D:\a b\材质.ms"))
    check("剪贴板命令带引号包裹中文路径", cmd == 'fileIn @"D:\\a b\\材质.ms"', cmd)

    failed = [c for c in CASES if not c[1]]
    print("\n自测结论：%d 例，失败 %d 项" % (len(CASES), len(failed)))
    for name, _ok, detail in failed:
        print("  - %s  %s" % (name, detail))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
