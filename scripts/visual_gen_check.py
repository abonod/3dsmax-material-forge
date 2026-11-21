#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""目视取证生成器冒烟检查（P4 的交付门禁）。

检查生成器能否真正产出「可判定」的取证材料，而不是产出一堆看似成功的空壳：
    1. visual_probe.ms 存在且为 GBK（中文版 Max Listener 用 GBK 才不会乱码）；
    2. 脚本里必须含 VISUAL_ITEM / VISUAL_DONE / VISUAL_SCRIPT_RUN 三类证据标记，
       缺任何一个都没法判定"到底渲了几张、成没成功"；
    3. 待目视项清单 visual_manifest.json 必须对每个项显式列出 png 路径与判据
       （expect 字段），且 passed 初值必须是 null —— 未目视就写 true 等于伪造证据；
    4. 场景搭建设置必须存在（相机/灯光/清空 medit），否则渲染结果不可比较。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []
OKS = []


def ok(m):
    OKS.append("[OK] " + m)


def bad(m):
    FAILS.append("[FAIL] " + m)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    tmp = tempfile.mkdtemp(prefix="mf_visual_")
    ms_dir = os.path.join(tmp, "ms")
    for sub in ("vray", "corona"):
        d = os.path.join(ms_dir, sub)
        os.makedirs(d, exist_ok=True)
        for name in ("mf_dispersion.ms", "mf_overlay.ms"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write("-- fake ms for check\n")
    out = os.path.join(tmp, "visual")

    r = subprocess.run([sys.executable, "scripts/gen_visual_probe.py",
                        "--ms-dir", ms_dir, "--out", out],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        bad("生成器退出码 %d：%s" % (r.returncode, (r.stderr or r.stdout)[-300:]))
        return _report()

    ms_path = os.path.join(out, "visual_probe.ms")
    man_path = os.path.join(out, "visual_manifest.json")
    if not os.path.exists(ms_path):
        bad("未生成 visual_probe.ms")
        return _report()
    ok("生成 visual_probe.ms")

    raw = open(ms_path, "rb").read()
    try:
        text = raw.decode("gbk")
        ok("visual_probe.ms 为 GBK 编码")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
        bad("visual_probe.ms 不是 GBK，中文版 Max 会出现乱码")

    for marker in ("VISUAL_ITEM=", "VISUAL_DONE=", "VISUAL_SCRIPT_RUN=1"):
        if marker in text:
            ok("含证据标记 %s" % marker)
        else:
            bad("缺少证据标记 %s，日志无法判定渲染结果" % marker)

    for token, why in (("Freecamera", "相机"), ("OmniLight", "灯光"),
                       ("meditMaterials[i] = StandardMaterial()", "清空材质槽")):
        if token in text:
            ok("场景含%s" % why)
        else:
            bad("场景缺少%s（%s），渲染结果不可比较" % (why, token))

    if not os.path.exists(man_path):
        bad("未生成 visual_manifest.json")
        return _report()
    try:
        man = json.load(open(man_path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        bad("visual_manifest.json 解析失败：%s" % e)
        return _report()

    items = man.get("items", [])
    if not items:
        bad("清单为空：没有任何待目视项")
    for it in items:
        if not it.get("expect"):
            bad("目视项 %s 缺 expect 判据字段" % it.get("key"))
        elif not it.get("png"):
            bad("目视项 %s 缺 png 路径" % it.get("key"))
        elif it.get("passed") is not None:
            bad("目视项 %s 的 passed 初值不是 null（未目视即判合格，属伪造证据）" % it.get("key"))
        else:
            ok("目视项 %s 判据与 png 齐备，passed 待人工填" % it.get("key"))

    if man.get("jobs_found", 0) < 2:
        bad("jobs_found=%s，色散与叠加两个场景未能同时被识别" % man.get("jobs_found"))
    else:
        ok("识别到 %d 个材质作业" % man["jobs_found"])

    return _report()


def _report() -> int:
    for s in OKS:
        print(s)
    for s in FAILS:
        print(s)
    print("\n结论：%s（OK %d / FAIL %d）"
          % ("PASS" if not FAILS else "FAIL", len(OKS), len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
