#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针生成冒烟检查（run_all_checks 的一个检查项）。

检查内容：
  1. 导出真机 ms（V-Ray + Corona 各一份，法线+凹凸叠加场景）到临时目录；
  2. 用 scripts/gen_probe.py 生成探针；
  3. 探针必须是 GBK 可解析（MaxScript 在中文 Windows 按 ANSI 解析，UTF-8 会乱码）、
     必须含 SCRIPT_RUN/BIND_MISMATCH/VERDICT 三个证据标记、job 数与 ms 文件数一致、
     且每个 job 路径都指向真实存在的文件。

为什么单独做这一项：gen_probe 的模板是 %-格式化的，一旦有人改模板时漏写一个百分号，
输出的 MaxScript 会变成 `format "BIND_MISMATCH=%\n"` 这类正确形态或者更糟的语法错误，
而静态体检 verify_ms 未必能覆盖到探针。这里直接跑一遍端到端产出，把问题挡在交付前。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import params as P                       # noqa: E402
from core.exporters import ms_exporter as M        # noqa: E402


def build_ms(root_dir: str) -> list[tuple[str, str]]:
    vals = P.default_values()
    vals.update({
        "normal_on": True, "bump_on": True, "nb_overlay": True,
        "normal_strength": 0.85, "bump_strength": 0.42,
    })
    tex = {"normal": r"D:\t\n.png", "height": r"D:\t\h.png", "albedo": r"D:\t\a.png"}
    made = []
    for r in ("vray", "corona"):
        d = os.path.join(root_dir, r)
        os.makedirs(d, exist_ok=True)
        pr = P.resolve_corona(vals) if r == "corona" else P.resolve_vray(vals)
        ms = M.build_mscript("mf冒烟", pr, tex, renderer=r)
        fp = os.path.join(d, "%s_smoke.ms" % r)
        with open(fp, "w", encoding="gbk", errors="replace") as fh:
            fh.write(ms)
        made.append((r, fp))
    return made


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    errs = []
    with tempfile.TemporaryDirectory() as td:
        made = build_ms(td)
        out = os.path.join(td, "probe.ms")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "gen_probe.py"),
             "--ms-dir", td, "--out", out, "--expect", "mf冒烟"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            print("[FAIL] gen_probe 执行失败（returncode=%d）" % r.returncode)
            return 1

        raw = open(out, "rb").read()
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            print("[FAIL] 探针不是合法 GBK（MaxScript 在中文 Windows 按 ANSI 解析）")
            return 1

        # 版本证据（MAX_VERSION / MAX_LANG / COMPAT_LEVEL）与 BAD 行必须出现在生成的探针里：
        # 探针若不含探测段，真机日志永远让 verify_probe_log.py --require-compat 判 FAIL，
        # 而那种"生成器漏注入"只在真机回读时才暴露，必须在这一层拦死。
        for token in ("SCRIPT_RUN=1", "BIND_MISMATCH=", "VERDICT=",
                      "MAX_VERSION=", "MAX_LANG=", "COMPAT_LEVEL=", '"BAD="'):
            if token not in text:
                errs.append("探针缺少证据标记：%s" % token)

        jobs = re.findall(r'#\("(\w+)",\s*"(.+?)",\s*"(.+?)"', text)
        if len(jobs) != len(made):
            errs.append("job 数 %d 与 ms 文件数 %d 不一致" % (len(jobs), len(made)))
        for renderer, sub, fname in jobs:
            fp = os.path.join(sub.replace("\\\\", "\\"), fname)
            if not os.path.isfile(fp):
                errs.append("job 指向不存在的文件：%s/%s" % (renderer, fname))

        # 哨兵逻辑必须存在：这是「绑定校验」的实质，不是可选项
        if "__MF_PROBE_SENTINEL_DO_NOT_USE__" not in text:
            errs.append("探针缺少哨兵材质逻辑，绑定校验形同虚设")

    if errs:
        for e in errs:
            print("[FAIL] " + e)
        return 1
    print("探针生成冒烟通过：%d 个 job，证据标记齐全，哨兵逻辑在位" % len(made))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
