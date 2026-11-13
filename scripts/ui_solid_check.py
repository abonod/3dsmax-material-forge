# -*- coding: utf-8 -*-
"""纯色模式 + 色板校色的界面可验证性检查

为什么单独写一个
--------------
「不上传图片也能出粗糙乳胶漆」「色板选色和成品色一致」这两件事，
只在后端跑通不算数——用户看的是预览球。所以要真的点一次按钮、
真的拖一次色板，再去看贴图 URL、画面上真实的像素颜色。

为什么全部走 DOM / 像素，不读 JS 内部状态
----------------------------------------
web/app.js 是 ES module，`S`、`ensureMaterial` 都是模块作用域，
CDP 的 Runtime.evaluate 根本看不见它们（typeof 得到 undefined），
早期版本因此写出一堆「看起来通过、其实是取值失败」的假断言。
这里改成只用用户能看见的东西做判断：贴图网格的 img src、
色差提示文本、截图画面的中位色。

用法
----
    python scripts/ui_solid_check.py
    python scripts/ui_solid_check.py --show     # 非 headless，肉眼看
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = SKILL_ROOT / "scripts" / "reports"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import ui_check as U  # noqa: E402

# profile 生命周期工具与 ui_check 共用同一份实现，避免两处行为漂移
EU = U.EU  # noqa: E402


def wait_until(cdp, expr, timeout=25.0, step=0.35):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            last = cdp.js(expr)
        except Exception as e:
            last = "ERR:%s" % e
        if last:
            return last
        time.sleep(step)
    return last


def set_hex(cdp, hexv: str):
    """走真实 UI 改色：填 HEX 输入框并派发 change（setBaseColor 是模块内的，CDP 调不到）"""
    return cdp.js("""(() => {
        const el = document.getElementById('baseColorHex');
        el.value = '%s';
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return el.value;
    })()""" % hexv)


def center_color(shot: Path, frac=0.30):
    """取画面上球体区域的中位色（中位数比均值抗高光与暗边）"""
    from PIL import Image
    im = Image.open(shot).convert("RGB")
    w, h = im.size
    box = im.crop((int(w * frac), int(h * frac), int(w * (1 - frac)), int(h * (1 - frac))))
    px = list(box.getdata())
    px.sort(key=lambda c: c[0] + c[1] + c[2])
    return px[len(px) // 2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    edge = next((p for p in U.EDGE_CANDIDATES if os.path.isfile(p)), None)
    if not edge:
        print("找不到 Edge/Chrome，无法做界面检查")
        return 2

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    http_port = U.free_port(8841)
    cdp_port = U.free_port(http_port + 100)
    # 固定 profile 复用省磁盘，但残留实例占住时新进程会挂到旧实例上、调试端口不监听，
    # 需要 MF_EDGE_PROFILE 换一份干净 profile 兜底（同 ui_check.py）
    profile = Path(os.environ.get("MF_EDGE_PROFILE") or (SKILL_ROOT / "temp" / "edge_profile"))
    profile.mkdir(parents=True, exist_ok=True)

    # v0.8.4：先杀掉仍占用该 profile 的残留 Edge 整棵进程树，再回收历史一次性 profile，
    # 否则新实例会挂到旧实例上、调试端口永不监听（曾是 profile 无限增殖的源头）
    stale = EU.kill_by_profile(profile)
    if stale:
        print("清场：终止了 %d 个占用 profile 的残留 Edge 进程" % stale)
    if not os.environ.get("MF_KEEP_EDGE"):
        for name, mb in EU.reap_stale_profiles(keep=profile):
            print("回收历史 profile：%-26s %6.1f MB" % (name, mb))

    env = os.environ.copy()
    env["MF_PORT"] = str(http_port)
    env["PYTHONIOENCODING"] = "utf-8"
    srv = subprocess.Popen([sys.executable, str(SKILL_ROOT / "app.py")],
                           cwd=str(SKILL_ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % http_port
    print("服务 %s -> %s" % (base, "就绪" if U.wait_http(base + "/api/schema") else "启动失败"))

    flags = ["--remote-debugging-port=%d" % cdp_port,
             "--user-data-dir=%s" % profile,
             "--no-first-run", "--no-default-browser-check",
             "--window-size=1500,1000", "--disable-features=Translate"]
    if not args.show:
        flags.append("--headless=new")
    proc = subprocess.Popen([edge] + flags + ["about:blank"])

    checks = []
    cdp = None
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
            # 默认 profile 被上一次检查的残留实例占住时（连续跑两次的常见坑），
            # 新进程会挂到旧实例上，调试端口永不监听，表现就是这里连不上。
            # 自动换一份带时间戳的干净 profile 重跑一次，省掉人工设 MF_EDGE_PROFILE。
            if not os.environ.get("MF_EDGE_PROFILE"):
                # 清场后仍失败才走兜底：换一次性 profile 重跑，并打标记让子进程退出时回收
                print("CDP 连接失败（清场后仍无法连接），换一次性 profile 重试一次")
                eph = EU.make_ephemeral_profile()
                os.environ["MF_EDGE_PROFILE"] = str(eph)
                os.environ["MF_EDGE_EPHEMERAL"] = "1"
                return subprocess.call(
                    [sys.executable, str(SKILL_ROOT / "scripts" / Path(__file__).name)] + sys.argv[1:],
                    cwd=str(SKILL_ROOT))
            print("CDP 连接失败")
            return 2

        cdp = U.CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.navigate", url=base)
        wait_until(cdp, "document.querySelectorAll('#paramPanel .prow').length > 0")

        def check(name, expr, expect_desc, judge, await_promise=False):
            try:
                val = cdp.js(expr, await_promise=await_promise)
            except Exception as e:
                val = "ERR:%s" % e
            ok = judge(val)
            checks.append({"item": name, "expect": expect_desc, "actual": val, "ok": bool(ok)})
            print("%s %-30s 实际=%-16s 预期=%s"
                  % ("[PASS]" if ok else "[FAIL]", name, str(val)[:16], expect_desc))
            return val

        TEX = "document.querySelector('#texgrid [data-ch=\\\"%s\\\"] img')"

        # ---- 1. 纯色入口在位 ----
        check("纯色按钮在位", "!!document.getElementById('btnSolid')", "True", lambda v: v is True)
        check("纯色按钮初始可用", "document.getElementById('btnSolid').disabled", "False",
              lambda v: v is False)

        # 先套乳胶漆预设再走纯色 = 用户「只有色卡」的真实动作顺序
        check("乳胶漆预设可套用",
              """(() => {const b = Array.from(document.querySelectorAll('#presets button'))
                  .find(x => /乳胶漆/.test(x.textContent)); if (b) b.click(); return !!b;})()""",
              "True", lambda v: v is True)
        time.sleep(0.4)

        cdp.js("document.getElementById('btnSolid').click()")
        # 后端要现算贴图，必须等，不能点完立刻读（早期版本就栽在这）
        wait_until(cdp, "document.querySelectorAll('#texgrid .texcell').length >= 5",
                   timeout=60, step=0.5)
        check("纯色模式出贴图（免上传）",
              "document.querySelectorAll('#texgrid .texcell').length", ">=5",
              lambda v: isinstance(v, int) and v >= 5)
        # 导出解锁发生在贴图 Image.onload 回调里（loadTextures 全部加载完才置 false），
        # 干净 profile 无 HTTP 缓存时预览图首下会明显慢于 texgrid 渲染，直接读会假红。
        wait_until(cdp, "document.getElementById('btnExport').disabled === false",
                   timeout=30, step=0.3)
        check("纯色模式解锁导出", "document.getElementById('btnExport').disabled", "False",
              lambda v: v is False)
        check("albedo 指向烘焙预览图", TEX % "albedo" + ".getAttribute('src') || ''", "含 /preview/albedo.png",
              lambda v: isinstance(v, str) and "/preview/albedo.png" in v)
        check("法线贴图已生成", "!!(" + TEX % "normal" + ")", "True", lambda v: v is True)
        check("高度贴图已生成", "!!(" + TEX % "height" + ")", "True", lambda v: v is True)

        # ---- 2. 法线贴图上有真实起伏（不是一片 128,128,255）----
        n_std = check("法线贴图有真实起伏(std)", """(async () => {
            const img = document.querySelector('#texgrid [data-ch="normal"] img');
            await img.decode();
            const c = document.createElement('canvas');
            c.width = 128; c.height = 128;
            const g = c.getContext('2d');
            g.drawImage(img, 0, 0, 128, 128);
            const d = g.getImageData(0, 0, 128, 128).data;
            let s = 0, n = 0;
            for (let i = 0; i < d.length; i += 4) { s += d[i]; n++; }
            const m = s / n; let v = 0;
            for (let i = 0; i < d.length; i += 4) { v += (d[i] - m) ** 2; }
            return Math.round(Math.sqrt(v / n) * 100) / 100;
        })()""", "> 1.0（纯平面约 0.3）",
            lambda v: isinstance(v, (int, float)) and v > 1.0, await_promise=True)

        s1 = REPORT_DIR / "ui_solid.png"
        cdp.shot(s1)

        # ---- 3. 色板选色：重烤 + 色差回显 ----
        # 注意：tintInfo 上「有没有 ΔE」证明不了重烤——纯色生成时就已写过一次。
        # 真正的证据是 albedo 缩略图 src 的 ?t= 时间戳变了（重烤后才会重渲染）。
        old_src = cdp.js(TEX % "albedo" + ".getAttribute('src') || ''")
        set_hex(cdp, "#2E6E6A")
        new_src = wait_until(
            cdp, "(%s.getAttribute('src')||'') !== %s"
                 % (TEX % "albedo", json.dumps(old_src)), timeout=40, step=0.4)
        check("色板改色触发重烤（缩略图换版）", "!!(%s)" % json.dumps(new_src), "True",
              lambda v: v is True)
        check("换色后 albedo 仍走烘焙预览图", TEX % "albedo" + ".getAttribute('src') || ''",
              "含 /preview/albedo.png",
              lambda v: isinstance(v, str) and "/preview/albedo.png" in v)
        check("色差信息已回显",
              "document.getElementById('tintInfo').textContent", "含 ΔE",
              lambda v: isinstance(v, str) and "ΔE" in v)

        time.sleep(1.2)  # 等预览球把新贴图贴上并出一帧
        s2 = REPORT_DIR / "ui_solid_color1.png"
        cdp.shot(s2)
        mid1 = center_color(s2)
        ok1 = mid1[1] >= mid1[0] - 6 and mid1[2] >= mid1[0] - 20 and mid1[1] > 40
        checks.append({"item": "球面颜色倾向青绿", "expect": "G/B >= R（目标 #2E6E6A）",
                       "actual": list(mid1), "ok": bool(ok1)})
        print("%s %-30s 实际=%-16s 预期=青绿 G/B>=R（目标 #2E6E6A）"
              % ("[PASS]" if ok1 else "[FAIL]", "球面颜色倾向青绿", str(mid1)))

        # ---- 4. 换成暖色，验证不是一次性特例 ----
        # 同步点同样用缩略图 ?t= 换版：光等 tintInfo 里的 ΔE 会立刻为真（上一色残留），
        # 提前截图就会拍到还没更新的球，误判成「重烤失效」。
        old2 = cdp.js(TEX % "albedo" + ".getAttribute('src') || ''")
        set_hex(cdp, "#C24B3A")
        wait_until(cdp, "(%s.getAttribute('src')||'') !== %s"
                   % (TEX % "albedo", json.dumps(old2)), timeout=40, step=0.4)
        time.sleep(1.4)
        s3 = REPORT_DIR / "ui_solid_color2.png"
        cdp.shot(s3)
        mid2 = center_color(s3)
        ok2 = mid2[0] > mid2[1] + 15 and mid2[0] > mid2[2] + 15
        checks.append({"item": "二次换色球面偏红", "expect": "R 明显最大（目标 #C24B3A）",
                       "actual": list(mid2), "ok": bool(ok2)})
        print("%s %-30s 实际=%-16s 预期=R 明显最大（目标 #C24B3A）"
              % ("[PASS]" if ok2 else "[FAIL]", "二次换色球面偏红", str(mid2)))

        # 两次选色的球面颜色必须真的不同，否则说明重烤没生效
        diff = sum(abs(a - b) for a, b in zip(mid1, mid2))
        ok3 = diff > 40
        checks.append({"item": "两次选色画面确有变化", "expect": "通道差之和 > 40",
                       "actual": diff, "ok": bool(ok3)})
        print("%s %-30s 实际=%-16s 预期=通道差之和 > 40"
              % ("[PASS]" if ok3 else "[FAIL]", "两次选色画面确有变化", diff))

        # ---- 5. 纯色 job 也要能导出 .ms ----
        cdp.js("document.getElementById('btnExport').click()")
        wait_until(cdp, "/\\.ms/i.test(document.getElementById('exportResult').innerText || '')",
                   timeout=60, step=0.5)
        exp = cdp.js("(document.getElementById('exportResult').innerText || '').slice(0, 160)")
        ok4 = isinstance(exp, str) and ".ms" in exp.lower()
        checks.append({"item": "纯色模式可导出 .ms", "expect": "结果区出现 .ms 路径",
                       "actual": exp, "ok": bool(ok4)})
        print("%s %-30s 实际=%-16s 预期=结果区出现 .ms 路径"
              % ("[PASS]" if ok4 else "[FAIL]", "纯色模式可导出 .ms", str(exp)[:16]))

        # ---- 6. 低频区域遮罩的两个参数要在面板里可见可调 ----
        for pid, label in (("proc_mask", "区域遮罩"), ("proc_mask_scale", "遮罩尺度")):
            row_ok = bool(cdp.js(
                "!!document.querySelector('.prow[data-pid=\"%s\"] input, .prow[data-pid=\"%s\"] select')"
                % (pid, pid)))
            checks.append({"item": "%s 参数在位" % label,
                           "expect": "面板存在 %s 的可编辑控件" % pid,
                           "actual": row_ok, "ok": row_ok})
            print("%s %-30s 实际=%-16s 预期=面板存在 %s 控件"
                  % ("[PASS]" if row_ok else "[FAIL]", "%s 参数在位" % label, str(row_ok), pid))

    finally:
        try:
            if cdp:
                cdp.close()
        except Exception:
            pass
        # 杀整棵进程树：只 terminate 主进程会留下 GPU/renderer/crashpad 子进程锁住 profile
        EU.kill_proc(proc)
        EU.kill_by_profile(profile, settle=0.5)
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:
            try:
                srv.kill()
            except Exception:
                pass
        EU.release_ephemeral()
        if os.environ.get("MF_EDGE_EPHEMERAL") == "1":
            EU.release_ephemeral(profile)

    passed = sum(1 for c in checks if c["ok"])
    print("\n汇总：%d/%d 项通过" % (passed, len(checks)))
    (REPORT_DIR / "ui_solid_check.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in checks:
        if not c["ok"]:
            print("  未通过：%s（实际=%s）" % (c["item"], c["actual"]))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
