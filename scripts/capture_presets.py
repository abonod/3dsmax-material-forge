# -*- coding: utf-8 -*-
"""真机 GUI 出图基线：逐个预设点一遍，把预览球截成基线图

为什么需要它
------------
无头断言（ui_check.py）只能证明「DOM 结构对、快照回填对」，证明不了「这材质看起来像不像壁纸」。
而无头 -silent 启动 3ds Max 跑一套会卡在 Begin setting up global configuration，
所以「真机端到端」这一层只能落在 Web GUI 的实时渲染上：真浏览器、真 WebGL、真帧。

产出
----
scripts/reports/baseline/
    <预设中文名>.png      预览球截图（裁到 canvas 区域，不含页面装饰）
    baseline.json         版本号、浏览器、每张图的 sha256/尺寸、以及该预设的关键参数快照
之后的视觉回归就是「新图 vs 基线图」的差异，而不是凭记忆判断。

用法
----
    python scripts/capture_presets.py            # 5 个新材质，带窗口（真机 GUI）
    python scripts/capture_presets.py --all      # 16 个预设全跑
    python scripts/capture_presets.py --headless # 无头跑（CI 用，出图一致性相同）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_check as U  # noqa: E402  复用 CDP 客户端 / 端口 / 服务拉起

SKILL_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = SKILL_ROOT / "scripts" / "reports" / "baseline"
NEW_PRESETS = ["壁纸", "墙布", "乳胶漆", "铝合金(磨砂)", "铝合金(拉丝)"]


def shot_clip(cdp, path: Path, clip: dict):
    data = cdp.call("Page.captureScreenshot", format="png", clip=clip)["data"]
    path.write_bytes(base64.b64decode(data))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="跑全部预设")
    ap.add_argument("--headless", action="store_true", help="无头模式（默认带窗口）")
    ap.add_argument("--keep", action="store_true", help="结束后保留浏览器")
    args = ap.parse_args()

    edge = next((p for p in U.EDGE_CANDIDATES if os.path.isfile(p)), None)
    if not edge:
        print("找不到 Edge/Chrome")
        return 2

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    http_port = U.free_port(8899)
    cdp_port = U.free_port(http_port + 100)
    # 固定复用一份 profile（否则每次跑都新建 30~65MB 缓存，temp 会随次数膨胀到 GB 级）
    profile = SKILL_ROOT / "temp" / "edge_profile"
    profile.mkdir(parents=True, exist_ok=True)

    # v0.8.4：先清掉仍占用该 profile 的残留 Edge 进程树，再回收历史一次性 profile
    stale = U.EU.kill_by_profile(profile)
    if stale:
        print("清场：终止了 %d 个占用 profile 的残留 Edge 进程" % stale)
    if not os.environ.get("MF_KEEP_EDGE"):
        for name, mb in U.EU.reap_stale_profiles(keep=profile):
            print("回收历史 profile：%-26s %6.1f MB" % (name, mb))

    env = os.environ.copy()
    env["MF_PORT"] = str(http_port)
    env["PYTHONIOENCODING"] = "utf-8"
    srv = subprocess.Popen([sys.executable, str(SKILL_ROOT / "app.py")], cwd=str(SKILL_ROOT),
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % http_port
    if not U.wait_http(base + "/api/schema"):
        srv.terminate()
        print("服务启动失败")
        return 2
    print("服务 %s 就绪" % base)

    flags = ["--remote-debugging-port=%d" % cdp_port, "--user-data-dir=%s" % profile,
             "--no-first-run", "--no-default-browser-check",
             "--window-size=1500,1000", "--disable-features=Translate"]
    if args.headless:
        flags.append("--headless=new")
    proc = subprocess.Popen([edge] + flags + ["about:blank"])

    records = []
    try:
        ws_url = None
        t0 = time.time()
        while time.time() - t0 < 40 and not ws_url:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json" % cdp_port, timeout=2) as r:
                    for t in json.loads(r.read().decode()):
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            ws_url = t["webSocketDebuggerUrl"]
                            break
            except Exception:
                time.sleep(0.5)
        if not ws_url:
            print("CDP 连接失败")
            return 2

        cdp = U.CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.navigate", url=base)

        t0 = time.time()
        while time.time() - t0 < 40:
            n = cdp.js("document.querySelectorAll('#presets button').length")
            if isinstance(n, int) and n > 0:
                break
            time.sleep(0.4)
        print("预设按钮 %s 个，开始逐个出图" % cdp.js("document.querySelectorAll('#presets button').length"))

        labels = NEW_PRESETS if not args.all else [
            cdp.js("document.querySelectorAll('#presets button')[%d].textContent.trim()" % i)
            for i in range(cdp.js("document.querySelectorAll('#presets button').length"))
        ]

        version = cdp.js("(async function(){const s=await (await fetch('/api/schema')).json();"
                         "return (s.presets||{}).wallpaper? 'ok':'miss';})()", await_promise=True)

        for label in labels:
            clicked = cdp.js(
                "(function(){const bs=Array.from(document.querySelectorAll('#presets button'));"
                "const t=bs.find(b=>b.textContent.trim()==='%s');"
                "if(!t) return false;t.click();return true;})()" % label.replace("'", "\\'"))
            if not clicked:
                print("  [skip] 找不到预设按钮 %s" % label)
                continue

            time.sleep(1.2)  # 等一帧以上，让材质真正换上去

            box = cdp.js(
                "(function(){const c=document.querySelector('#viewport canvas');"
                "if(!c) return null;const r=c.getBoundingClientRect();"
                "return {x:r.x,y:r.y,width:r.width,height:r.height};})()")
            if not box or box.get("width", 0) < 10:
                print("  [skip] %s 取不到 canvas 尺寸" % label)
                continue

            snap = cdp.js(
                "(async function(){const s=await (await fetch('/api/schema')).json();"
                "const lb=s.preset_labels||{};const key=Object.keys(lb).find(k=>lb[k]==='%s');"
                "const f=(s.preset_full||{})[key]||{};return {key:key,"
                "anisotropy_on:f.anisotropy_on,anisotropy_amount:f.anisotropy_amount,"
                "anisotropy_axis:f.anisotropy_axis,gloss:f.reflection_glossiness,"
                "reflect:f.reflect_strength,uv:f.uv_tiling,normal:f.normal_strength};})()"
                % label.replace("'", "\\'"), await_promise=True)

            fname = label.replace("(", "_").replace(")", "_") + ".png"
            dst = BASE_DIR / fname
            shot_clip(cdp, dst, {"x": box["x"], "y": box["y"],
                                 "width": box["width"], "height": box["height"], "scale": 1})
            sha = hashlib.sha256(dst.read_bytes()).hexdigest()
            kb = dst.stat().st_size / 1024
            records.append({"preset": label, "key": (snap or {}).get("key"), "file": fname,
                            "size_kb": round(kb, 1), "sha256": sha[:16],
                            "canvas": [round(box["width"]), round(box["height"])],
                            "params": snap})
            print("  [ok] %-12s %-6.0fKB canvas=%dx%d %s"
                  % (label, kb, box["width"], box["height"], sha[:12]))

        frames = cdp.js("window.__mfFrames || 'n/a'")
        info = {
            "version": _pkg_version(),
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "browser": os.path.basename(edge),
            "headless": bool(args.headless),
            "schema_probe": version,
            "frames": frames,
            "shots": records,
        }
        (BASE_DIR / "baseline.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n基线 %d 张 -> %s" % (len(records), BASE_DIR))
        print("清单 -> %s" % (BASE_DIR / "baseline.json"))
        return 0 if records else 1
    finally:
        if not args.keep:
            # 杀整棵进程树：只 terminate 主进程会留下子进程锁住 profile，导致下轮 CDP 失败
            U.EU.kill_proc(proc)
            U.EU.kill_by_profile(profile, settle=0.5)
            try:
                srv.terminate()
                srv.wait(timeout=10)
            except Exception:
                try:
                    srv.kill()
                except Exception:
                    pass


def _pkg_version() -> str:
    sys.path.insert(0, str(SKILL_ROOT))
    try:
        from core.version import __version__

        return __version__
    except Exception:
        return "unknown"


if __name__ == "__main__":
    sys.exit(main())
