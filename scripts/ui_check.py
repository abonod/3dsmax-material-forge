# -*- coding: utf-8 -*-
"""前端可验证性检查（P2 第 1 项）

用途
----
把「新手模式是否真的只露 5 项」「预设是不是整套快照替换」「自检按钮/导出拦截卡是否在位」
这些只能靠肉眼看的点，变成一条命令能跑完的自动检查，并留下截图。

做法
----
1. 起一个独立端口（MF_PORT）的临时服务实例，不打扰日常在跑的那个；
2. 用独立 user-data-dir 拉一个 Edge（headless），通过 CDP 直接驱动页面；
3. 用 Runtime.evaluate 采集 DOM 状态做断言，用 Page.captureScreenshot 留证。

用法
----
    python scripts/ui_check.py            # 跑检查并截图
    python scripts/ui_check.py --show     # 不 headless，肉眼可看（调试用）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = SKILL_ROOT / "scripts" / "reports"

# 同目录的 profile 生命周期工具（杀进程树 / 清场 / 回收一次性 profile）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _edge_util as EU  # noqa: E402

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def pick_sample_image():
    """挑一张真实图片做上传样本：优先历史 job 的 source.png，其次任意大一点的 png/jpg"""
    cands = sorted((SKILL_ROOT / "output").glob("*/source.png"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if cands:
        return cands[0]
    for pat in ("*/textures/*.png", "*.png", "*.jpg"):
        got = sorted((SKILL_ROOT / "output").glob(pat),
                     key=lambda p: p.stat().st_mtime, reverse=True)
        got = [p for p in got if p.stat().st_size > 20000]
        if got:
            return got[0]
    return _make_sample_image()


def _make_sample_image() -> Path:
    """output 被清空时现场生成一张可上传的样例图

    为什么要有这一手：ui_check 依赖「output 下有一张历史 job 的 source.png」，
    而 output 属于运行期产物、随时可能被清理，一清就等于把检查脚本自己弄挂
    （曾经因为 output 为空，UI 检查在真实上传那步直接抛 RuntimeError）。
    这里用纯标准库写一张 512x512 的木纹噪声 PNG（zlib + struct，不引第三方依赖），
    生成的图落到 output/_uicheck_sample/，下次会被优先候选逻辑复用。
    """
    import struct
    import zlib

    out_dir = SKILL_ROOT / "output" / "_uicheck_sample"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "source.png"
    if dst.exists() and dst.stat().st_size > 20000:
        return dst

    size = 512
    rows = []
    for y in range(size):
        row = bytearray()
        row.append(0)  # filter type: none
        for x in range(size):
            # 木纹：沿 x 拉长的正弦条纹 + 确定性伪噪，得到纹理明确的图，上传后不会全白
            wave = (x * 0.08 + 3.0 * ((x * 7 + y * 13) % 17) / 17.0)
            v = int(128 + 60 * __import__("math").sin(wave) + 25 * (((x * 31 + y * 57) % 29) / 29.0 - 0.5))
            v = 0 if v < 0 else (255 if v > 255 else v)
            row += bytes((v, max(0, v - 18), max(0, v - 45)))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    dst.write_bytes(png)
    return dst


def free_port(start: int = 8791) -> int:
    for p in range(start, start + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("找不到空闲端口")


def wait_http(url: str, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


class CDP:
    """够用就好的 CDP 客户端：一次请求一次响应，不处理事件订阅"""

    def __init__(self, ws_url: str):
        import websocket  # 本地已具备
        self.ws = websocket.create_connection(ws_url, max_size=None, timeout=60)
        self.i = 0

    def call(self, method: str, **params):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    def js(self, expr: str, await_promise: bool = False):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise, userGesture=True)
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            raise RuntimeError("JS 异常: %s" % r["exceptionDetails"].get("text"))
        return res.get("value")

    def shot(self, path: Path):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        import base64
        path.write_bytes(base64.b64decode(data))

    def set_files(self, selector: str, paths):
        """给 <input type=file> 挂真实文件（CDP 原生能力，DOM 里无法伪造）"""
        self.call("DOM.enable")
        doc = self.call("DOM.getDocument")
        node = self.call("DOM.querySelector", nodeId=doc["root"]["nodeId"],
                         selector=selector)
        node_id = node.get("nodeId")
        if not node_id:
            raise RuntimeError("找不到选择器 %s 对应的节点" % selector)
        self.call("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=node_id)
        return node_id

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="带界面运行，便于肉眼确认")
    ap.add_argument("--keep", action="store_true", help="结束后保留 Edge 实例")
    args = ap.parse_args()

    edge = next((p for p in EDGE_CANDIDATES if os.path.isfile(p)), None)
    if not edge:
        print("找不到 Edge/Chrome，无法做界面检查")
        return 2

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    http_port = free_port(8791)
    cdp_port = free_port(http_port + 100)
    # 复用同一个 profile 目录：早期每次跑都新建 uiprof_<时间戳>，跑 20 多次就堆了 1.1GB 浏览器缓存。
    # 浏览器配置对断言没有意义（每次都从 about:blank 开始），固定一份即可，temp 恒定在 ~60MB。
    # 但固定目录有个坑：上一轮崩溃残留的 headless 实例还占着 profile，
    # 新一次 start 会被 Edge 判定为「同一实例」而直接挂过去，
    # 新开的调试端口永远不监听，表现为 CDP 连接失败。所以允许用环境变量换一份 profile 兜底。
    profile = Path(os.environ.get("MF_EDGE_PROFILE") or (SKILL_ROOT / "temp" / "edge_profile"))
    profile.mkdir(parents=True, exist_ok=True)

    # v0.8.4：启动前彻底清场——先杀掉仍占用该 profile 的残留 Edge 整棵进程树，
    # 再顺手回收 temp 下历史遗留的 edge_profile_* 目录。
    # 不清场就会出现「新进程挂到旧实例、调试端口永不监听」的老坑，
    # 而那正是历史上被迫新建带时间戳 profile、最终堆出 1.5GB 的源头。
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
    ok_srv = wait_http(base + "/api/schema")
    print("服务 %s -> %s" % (base, "就绪" if ok_srv else "启动失败"))

    flags = ["--remote-debugging-port=%d" % cdp_port,
             "--user-data-dir=%s" % profile,
             "--no-first-run", "--no-default-browser-check",
             "--window-size=1500,1000", "--disable-features=Translate"]
    if not args.show:
        flags.append("--headless=new")
    proc = subprocess.Popen([edge] + flags + ["about:blank"])

    checks = []
    shots = []
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

        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.navigate", url=base)

        # 等参数面板真的渲染出来（schema 到手 + DOM 铺完）
        t0 = time.time()
        while time.time() - t0 < 40:
            n = cdp.js("document.querySelectorAll('#paramPanel .prow').length")
            if isinstance(n, int) and n > 0:
                break
            time.sleep(0.4)

        def check(name, expr, expect_desc, judge):
            try:
                val = cdp.js(expr)
            except Exception as e:
                val = "ERR:%s" % e
            ok = judge(val)
            checks.append({"item": name, "expect": expect_desc, "actual": val, "ok": bool(ok)})
            print("%s %-28s 实际=%-8s 预期=%s"
                  % ("[PASS]" if ok else "[FAIL]", name, val, expect_desc))
            return val

        # offsetParent 在 headless 下对 closed <details> 不可靠，改为「有高度 且 所属折叠区已展开」
        VISIBLE = ("Array.from(document.querySelectorAll('#paramPanel .prow')).filter(e => {"
                   "const d = e.closest('details');"
                   "return e.offsetHeight > 0 && (!d || d.open);}).length")

        check("新手模式为默认", "document.querySelector('#modeSeg .active').dataset.mode",
              "beginner", lambda v: v == "beginner")
        check("新手模式可见参数数", VISIBLE, "5", lambda v: v == 5)
        check("进阶参数可折叠", "!!document.querySelector('#paramPanel details.advbox')",
              "True", lambda v: v is True)
        # 参数总数不再写死：从 /api/schema 实际取，避免新增参数后断言漂移
        param_n = cdp.js(
            "(async function(){const s = await (await fetch('/api/schema')).json();"
            "return (s.params || []).length;})()", await_promise=True)
        check("进阶折叠内含全部其余项",
              "document.querySelectorAll('#paramPanel details.advbox .prow').length",
              "=%s（总参数 %s - 新手 5）" % (param_n - 5, param_n),
              lambda v: isinstance(v, int) and v == param_n - 5)
        check("三步引导在位",
              "document.querySelectorAll('#steps li').length", ">=3", lambda v: isinstance(v, int) and v >= 3)
        # 预设按钮数不再写死：期望值从 /api/schema 的 preset_labels 实际数量取，
        # 这样新增材质预设时断言自动跟随（曾经写死 11，新增 5 套后误报）
        preset_n = cdp.js(
            "(async function(){const s = await (await fetch('/api/schema')).json();"
            "return Object.keys(s.preset_labels || {}).length;})()", await_promise=True)
        check("预设按钮数与标签一致",
              "document.querySelectorAll('#presets button').length",
              "=%s 且 >=16" % preset_n,
              lambda v: isinstance(v, int) and preset_n == v and v >= 16)
        check("自检按钮初始禁用", "document.getElementById('btnPreflight').disabled",
              "True", lambda v: v is True)

        s1 = REPORT_DIR / "ui_beginner.png"
        cdp.shot(s1)
        shots.append(str(s1))

        # 切到进阶模式
        cdp.js("document.querySelector('#modeSeg [data-mode=\"advanced\"]').click()")
        time.sleep(0.6)
        check("切进阶后可见参数数", VISIBLE, "=%s" % param_n,
              lambda v: isinstance(v, int) and v == param_n)
        check("切进阶后无折叠区", "!!document.querySelector('#paramPanel details.advbox')",
              "False", lambda v: v is False)
        s2 = REPORT_DIR / "ui_advanced.png"
        cdp.shot(s2)
        shots.append(str(s2))

        # 预设是不是整套快照：对全部预设逐个校验。
        # 做法：每个预设挑一个「与默认值差异最大」的数值参数当探针，先推到离快照最远的边界，
        # 再点该预设按钮，读回值应等于快照值。必须按「中文标签反查 key」——
        # 木纹=wood、木地板=wood_floor，两者 IOR 不同，靠标签猜 key 会误判。
        preset_rows = cdp.js("""(async function(){
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            const s = await (await fetch('/api/schema')).json();
            const full = s.preset_full || {}, labels = s.preset_labels || {};
            const dft = {}; (s.params || []).forEach(p => { dft[p.id] = p.default; });
            const nums = (s.params || []).filter(p => ['range','float','int'].indexOf(p.ptype) >= 0);
            const out = [];
            for (const key of Object.keys(full)) {
                const snap = full[key] || {};
                const label = labels[key] || key;
                let probe = null, best = 0;
                for (const p of nums) {
                    const sv = snap[p.id], dv = dft[p.id];
                    if (typeof sv === 'number' && typeof dv === 'number' && Math.abs(sv - dv) > best) {
                        best = Math.abs(sv - dv); probe = p.id;
                    }
                }
                if (!probe) { out.push({key:key, label:label, ok:false, why:'该预设无差异化数值参数'}); continue; }
                const btn = Array.from(document.querySelectorAll('#presets button'))
                    .find(x => x.textContent.trim() === label);
                if (!btn) { out.push({key:key, label:label, ok:false, why:'按钮缺失'}); continue; }
                const inp = document.querySelector('.prow[data-pid="' + probe + '"] input');
                if (!inp) { out.push({key:key, label:label, ok:false, why:'参数行缺失:' + probe}); continue; }
                const target = snap[probe];
                const lo = Number(inp.min), hi = Number(inp.max);
                const dirty = (Math.abs(target - lo) > Math.abs(target - hi)) ? lo : hi;
                inp.value = dirty;
                inp.dispatchEvent(new Event('input', {bubbles:true}));
                inp.dispatchEvent(new Event('change', {bubbles:true}));
                const before = Number(inp.value);
                btn.click();
                await sleep(150);
                const inp2 = document.querySelector('.prow[data-pid="' + probe + '"] input');
                const after = inp2 ? Number(inp2.value) : null;
                out.push({key:key, label:label, param:probe, expect:target,
                          before:before, after:after,
                          ok: after !== null && Math.abs(after - target) < 1e-6 && before !== after});
            }
            return out;
        })()""", await_promise=True)
        preset_rows = preset_rows or []
        ok_rows = [r for r in preset_rows if r.get("ok")]
        for r in preset_rows:
            print("   %s %-10s「%s」%-22s 改乱=%-7s 回填=%-7s 快照=%s%s"
                  % ("[ok ]" if r.get("ok") else "[BAD]", r.get("key"), r.get("label"),
                     r.get("param") or "-", r.get("before"), r.get("after"), r.get("expect"),
                     ("  <- " + r["why"]) if r.get("why") else ""))
        checks.append({"item": "预设逐个快照校验",
                       "expect": "全部预设均回填为快照值",
                       "actual": "%d/%d" % (len(ok_rows), len(preset_rows)),
                       "ok": len(preset_rows) >= 11 and len(ok_rows) == len(preset_rows)})
        print("%s %-28s %d/%d 个预设快照回填正确"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "预设逐个快照校验",
                 len(ok_rows), len(preset_rows)))

        # 回到新手模式确认状态一致
        cdp.js("document.querySelector('#modeSeg [data-mode=\"beginner\"]').click()")
        time.sleep(0.5)
        check("切回新手后可见参数数", VISIBLE, "5", lambda v: v == 5)

        # ---- 中级档（P1 三层暴露）：25 项可见，且比新手多、比高级少 ----
        cdp.js("document.querySelector('#modeSeg [data-mode=\"intermediate\"]').click()")
        time.sleep(0.5)
        check("中级档可见参数数", VISIBLE, "30", lambda v: v == 30)
        # 中级档必须铺出「涂层光泽分层」（sheen_tier）这类日常够用的项
        check("中级档含涂层光泽分层",
              "document.getElementById('paramPanel').innerText.indexOf('涂层光泽分层') >= 0",
              "True", lambda v: v is True)
        # 色散 / 薄膜厚度属于高级专区，中级档不该铺出来，否则三档失去意义
        check("中级档不显示色散",
              "document.getElementById('paramPanel').innerText.indexOf('色散') >= 0",
              "False", lambda v: v is False)

        # 回到新手模式确认状态一致
        cdp.js("document.querySelector('#modeSeg [data-mode=\"beginner\"]').click()")
        time.sleep(0.4)
        check("中级切回新手", VISIBLE, "5", lambda v: v == 5)

        # 导出按钮在未上传时应保持禁用（防呆）
        check("导出按钮初始禁用", "document.getElementById('btnExport').disabled",
              "True", lambda v: v is True)

        # ---------- 全链路：上传 -> 自检启用 -> 点自检 -> 报告卡可渲染 ----------
        # 用 CDP 原生 setFileInputFiles 挂真实图片，走的是用户真实操作路径（change 事件）
        src_img = pick_sample_image()
        print("   上传样本：%s" % src_img)
        cdp.set_files("#fileInput", [src_img])

        uploaded = False
        for _ in range(60):                      # 最多等 30s
            if cdp.js("document.getElementById('btnPreflight').disabled") is False:
                uploaded = True
                break
            time.sleep(0.5)
        checks.append({"item": "上传后自检按钮解锁", "expect": "disabled=False",
                       "actual": not uploaded, "ok": uploaded})
        print("%s %-28s 上传后 disabled=%s"
              % ("[PASS]" if uploaded else "[FAIL]", "上传后自检按钮解锁",
                 "False" if uploaded else "True"))

        prev = cdp.js("Array.from(document.querySelectorAll('#texgrid .texcell')).length")
        checks.append({"item": "上传后生成贴图缩略", "expect": ">=3", "actual": prev,
                       "ok": isinstance(prev, int) and prev >= 3})
        print("%s %-28s 贴图通道=%s（预期 >=3）"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "上传后生成贴图缩略", prev))

        cdp.js("document.getElementById('btnPreflight').click()")
        rendered = False
        for _ in range(40):                      # 最多等 20s
            if cdp.js("document.querySelectorAll('#exportResult .report .rp-head').length > 0"):
                rendered = True
                break
            time.sleep(0.5)
        rep = cdp.js("""(function(){
            const h = document.querySelector('#exportResult .report .rp-head');
            const box = document.getElementById('exportResult');
            return {head: h ? h.textContent.trim() : '',
                    items: box.querySelectorAll('.rp-list li').length,
                    tags: box.querySelectorAll('.rp-tag').length,
                    env: (box.querySelector('.rp-env') || {}).textContent || '',
                    cls: box.firstElementChild ? box.firstElementChild.className : ''};
        })()""") or {}
        checks.append({"item": "点自检后报告卡渲染", "expect": "有 .rp-head 与条目",
                       "actual": rep, "ok": bool(rendered and rep.get("items", 0) >= 1)})
        print("%s %-28s %s | 条目=%s 环境行=%s"
              % ("[PASS]" if rendered else "[FAIL]", "点自检后报告卡渲染",
                 rep.get("head"), rep.get("items"),
                 (rep.get("env") or "")[:48]))

        s3 = REPORT_DIR / "ui_report_card.png"
        # 报告卡在页面下方，先滚进视口再截，否则截到的是顶部空白
        cdp.js("document.getElementById('exportResult').scrollIntoView({block:'center'})")
        time.sleep(0.6)
        cdp.shot(s3)
        shots.append(str(s3))

        # ---------- 窄屏布局（<1180px 断点）----------
        # 断点只把左右栏 300/320 收窄到 260/280，中列 1fr 会被压缩，
        # 所以窄屏只需确认三件事：中列没被压塌、参数控件没被挤出面板、整页没有横向滚动。
        # 切进阶模式测——38 行参数全在，才有覆盖意义。
        cdp.js("document.querySelector('#modeSeg [data-mode=\"advanced\"]').click()")
        time.sleep(0.6)
        NARROW_W, NARROW_H = 1000, 720
        cdp.call("Emulation.setDeviceMetricsOverride", width=NARROW_W, height=NARROW_H,
                 deviceScaleFactor=1, mobile=False)
        time.sleep(0.9)
        nar = cdp.js("""(function(){
            const layout = document.querySelector('.layout');
            const cols = getComputedStyle(layout).gridTemplateColumns
                .split(' ').map(v => parseFloat(v)).filter(v => !isNaN(v));
            const mid = cols.length === 3 ? cols[1] : 0;
            const pr = document.getElementById('presets').getBoundingClientRect();
            let btnOut = 0, btnZero = 0, btnCount = 0;
            document.querySelectorAll('#presets button').forEach(b => {
                btnCount++;
                const r = b.getBoundingClientRect();
                if (r.width < 8) btnZero++;
                if (r.right > pr.right + 1 || r.left < pr.left - 1) btnOut++;
            });
            const rows = Array.from(document.querySelectorAll('.prow'));
            let rowOut = 0, narrowInput = 0, mainCtl = 0;
            rows.forEach(row => {
                const rr = row.getBoundingClientRect();
                // 主控件：range / select / 数字框，设计宽 108px，窄屏下限给 90
                row.querySelectorAll('.ctrl input[type=range], .ctrl select, .ctrl input[type=number]')
                   .forEach(el => {
                       mainCtl++;
                       const ir = el.getBoundingClientRect();
                       if (ir.right > rr.right + 1 || ir.left < rr.left - 1) rowOut++;
                       if (ir.width < 90) narrowInput++;
                   });
                // 颜色块：设计宽 54px，窄屏下限给 40
                row.querySelectorAll('.ctrl input[type=color]').forEach(el => {
                    mainCtl++;
                    const ir = el.getBoundingClientRect();
                    if (ir.right > rr.right + 1 || ir.left < rr.left - 1) rowOut++;
                    if (ir.width < 40) narrowInput++;
                });
            });
            const cv = document.querySelector('canvas');
            const cr = cv ? cv.getBoundingClientRect() : {width: 0, height: 0};
            return {vw: window.innerWidth,
                    scrollW: document.documentElement.scrollWidth,
                    cols: cols, mid: mid,
                    btnCount: btnCount, btnOut: btnOut, btnZero: btnZero,
                    rowCount: rows.length, rowOut: rowOut, narrowInput: narrowInput,
                    mainCtl: mainCtl,
                    canvasW: Math.round(cr.width), canvasH: Math.round(cr.height)};
        })()""") or {}
        print("   窄屏 %dx%d 列宽=%s 中列=%s 参数行=%s 画布=%sx%s"
              % (NARROW_W, NARROW_H, nar.get("cols"), nar.get("mid"),
                 nar.get("rowCount"), nar.get("canvasW"), nar.get("canvasH")))

        checks.append({"item": "窄屏无横向滚动", "expect": "scrollWidth <= 视口宽",
                       "actual": "%s <= %s" % (nar.get("scrollW"), nar.get("vw")),
                       "ok": isinstance(nar.get("scrollW"), (int, float))
                             and isinstance(nar.get("vw"), (int, float))
                             and nar["scrollW"] <= nar["vw"] + 1})
        print("%s %-28s scrollWidth=%s 视口宽=%s"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "窄屏无横向滚动",
                 nar.get("scrollW"), nar.get("vw")))

        checks.append({"item": "窄屏中列未被压塌", "expect": ">=360",
                       "actual": nar.get("mid"), "ok": (nar.get("mid") or 0) >= 360})
        print("%s %-28s 中列=%spx"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "窄屏中列未被压塌", nar.get("mid")))

        checks.append({"item": "窄屏参数控件不溢出", "expect": "溢出 0 行 / 窄控件 0 个",
                       "actual": "溢出 %s / 偏窄 %s / 控件 %s 个 / 共 %s 行"
                                 % (nar.get("rowOut"), nar.get("narrowInput"),
                                    nar.get("mainCtl"), nar.get("rowCount")),
                       "ok": nar.get("rowOut") == 0 and nar.get("narrowInput") == 0
                             and (nar.get("mainCtl") or 0) >= 11
                             and (nar.get("rowCount") or 0) >= 11})
        print("%s %-28s 溢出=%s 偏窄=%s（控件 %s 个 / 共 %s 行）"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "窄屏参数控件不溢出",
                 nar.get("rowOut"), nar.get("narrowInput"),
                 nar.get("mainCtl"), nar.get("rowCount")))

        checks.append({"item": "窄屏预设按钮不溢出", "expect": "全部在位且不越界",
                       "actual": "共 %s 个 / 越界 %s / 零宽 %s"
                                 % (nar.get("btnCount"), nar.get("btnOut"), nar.get("btnZero")),
                       "ok": (nar.get("btnCount") or 0) >= 11 and nar.get("btnOut") == 0
                             and nar.get("btnZero") == 0})
        print("%s %-28s 共 %s 个 越界=%s 零宽=%s"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "窄屏预设按钮不溢出",
                 nar.get("btnCount"), nar.get("btnOut"), nar.get("btnZero")))

        checks.append({"item": "窄屏预览画布仍有面积", "expect": ">=240x160",
                       "actual": "%sx%s" % (nar.get("canvasW"), nar.get("canvasH")),
                       "ok": (nar.get("canvasW") or 0) >= 240 and (nar.get("canvasH") or 0) >= 160})
        print("%s %-28s 画布=%sx%s"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "窄屏预览画布仍有面积",
                 nar.get("canvasW"), nar.get("canvasH")))

        s4 = REPORT_DIR / "ui_narrow.png"
        cdp.shot(s4)
        shots.append(str(s4))
        cdp.call("Emulation.clearDeviceMetricsOverride")
        time.sleep(0.5)

        # ---------- 预览球是真渲染，不是一块空 canvas ----------
        # 尺寸断言挡不住「画布在、什么都没画」的情况，所以读 app.js 渲染循环埋的帧数与三角形数：
        # 帧数必须持续增长，三角形数必须等于球体真实细分量级（半径 60 段 x 64 段）。
        f1 = cdp.js("window.__mfFrames || 0") or 0
        time.sleep(0.8)
        rnd = cdp.js("""(function(){
            return {frames: window.__mfFrames || 0,
                    tris: window.__mfTris || 0,
                    mesh: !!window.__mfMesh};
        })()""") or {}
        print("   渲染帧 %s -> %s，三角形=%s，材质网格挂载=%s"
              % (f1, rnd.get("frames"), rnd.get("tris"), rnd.get("mesh")))

        checks.append({"item": "预览球持续出帧", "expect": "0.8s 内帧数增长",
                       "actual": "%s -> %s" % (f1, rnd.get("frames")),
                       "ok": (rnd.get("frames") or 0) > f1 and (rnd.get("frames") or 0) >= 10})
        print("%s %-28s 帧数 %s -> %s"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "预览球持续出帧",
                 f1, rnd.get("frames")))

        tri = rnd.get("tris") or 0
        checks.append({"item": "预览球真画出三角形", "expect": ">=3000 且网格已挂载",
                       "actual": "%s 三角形 / mesh=%s" % (tri, rnd.get("mesh")),
                       "ok": tri >= 3000 and bool(rnd.get("mesh"))})
        print("%s %-28s 三角形=%s mesh=%s"
              % ("[PASS]" if checks[-1]["ok"] else "[FAIL]", "预览球真画出三角形",
                 tri, rnd.get("mesh")))

    finally:
        if cdp:
            cdp.close()
        if not args.keep:
            # 必须杀整棵进程树：只 terminate 主进程会留下 GPU/renderer/crashpad 子进程，
            # 它们继续锁住 --user-data-dir，下一轮就被迫换 profile（v0.8.4 泄漏根因）
            EU.kill_proc(proc)
            EU.kill_by_profile(profile, settle=0.5)
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
        time.sleep(0.3)
        # 兜底分支新建的一次性 profile 在这里回收；固定 profile 保留复用
        EU.release_ephemeral()
        if os.environ.get("MF_EDGE_EPHEMERAL") == "1":
            EU.release_ephemeral(profile)

    passed = sum(1 for c in checks if c["ok"])
    print("\n汇总：%d/%d 项通过" % (passed, len(checks)))
    rp = REPORT_DIR / "ui_check.json"
    rp.write_text(json.dumps({"http": base, "checks": checks, "screenshots": shots},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告: %s" % rp)
    for s in shots:
        print("截图: %s" % s)
    return 0 if passed == len(checks) and checks else 1


if __name__ == "__main__":
    sys.exit(main())
