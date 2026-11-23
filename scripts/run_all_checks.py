# -*- coding: utf-8 -*-
"""统一检查入口：一条命令跑完全部静态/行为检查

为什么要有这个脚本：
    0.8.2 的检查散在 6 个地方（evals / registry / proc_mask / ui / ui_solid /
    export_guard），改完代码要人工记住跑哪几个，漏一个就可能把回归带出去。
    这里全部串起来，任何一项失败即非零退出，可直接挂到提交前或 CI。

用法：
    python scripts/run_all_checks.py            # 全量（含 Edge 截图，约 3 分钟）
    python scripts/run_all_checks.py --fast     # 跳过两个 UI 检查（约 15 秒）
    python scripts/run_all_checks.py --json     # 机器可读汇总
    python scripts/run_all_checks.py --only evals,export_guard
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECKS = [
    # key,            名称,                 命令,                             是否需要浏览器
    ("evals",         "evals 断言+产物回归",  ["evals/runner.py", "--artifact"], False),
    ("export_guard",  "导出隔离/自检/生命周期", ["scripts/export_guard_check.py"], False),
    ("registry",      "注册表文档一致",        ["scripts/registry_check.py"],     False),
    # P12/第3条：文档数字与类名由代码生成，漂移必然 FAIL。
    # 两个入口各司其职：docs_sync 是硬门禁（幂等 --check），docs_sync_neg 是反例自测，
    # 证明"把数字改错""写回已证伪的类名"真的会被抓住，而不是永远绿灯。
    ("docs_sync",     "文档数字/类名一致性",    ["scripts/docs_sync.py"],          False),
    ("docs_sync_neg", "文档漂移门禁反例自测",   ["scripts/docs_sync.py", "--self-test"], False),
    ("proc_mask",     "贴图元信息与遮罩",      ["scripts/proc_mask_check.py"],    False),
    # P1 机制性阻断：判据器自测 + 探针生成冒烟。放在这里是为了保证「防假通过的机制」
    # 本身每次交付前都被验证一遍 —— 机制失效比功能失效更危险，因为它会静默放行错误。
    ("probe_verdict", "真机探针判据器自测",    ["scripts/verify_probe_log.py", "--selftest"], False),
    ("probe_gen",     "探针生成冒烟",          ["scripts/probe_gen_check.py"],    False),
    # P3/P5/P6/P7：兼容矩阵一致性 + 中英类名别名覆盖 + 版本等级诚实性。
    # 这项守的是"文档别变成营销文案"——标 verified 的版本必须有本机真机证据。
    ("compat",        "兼容矩阵与语言覆盖",    ["scripts/compat_check.py"],       False),
    # P4：目视取证生成器冒烟 + 结构校验。visual_gen_check 在临时目录里跑生成器，
    # 不往 out/ 写任何东西 —— 检查项必须无副作用，否则会把产物目录搞脏。
    ("visual_check",  "目视取证结构校验",      ["scripts/visual_gen_check.py"],   False),
    # P3：真实世界尺寸 UV 的取证前置。这份探针不产出材质，只回答
    # 「位图坐标类上有没有可写可回读的 real-world 属性名」——
    # 拿到真机日志之前不写映射代码，避免重演 0.9.x 属性名靠推理的静默失效。
    ("rw_probe",      "真实世界尺寸探针生成",  ["scripts/rw_probe_gen_check.py"], False),
    ("p_series",      "P0-P7 落地审计",        ["scripts/p_series_audit.py"],     False),
    ("p_series_neg",  "P0-P7 审计反例自测",     ["scripts/p_series_audit.py", "--selftest"], False),
    # 一键送进 Max 的 inbox 协议自测：跨进程 + 会让 Max 执行脚本，安全边界
    # （路径白名单/哈希/token/超时）必须每次交付前被验一遍，不能只靠真机跑通一次。
    ("max_bridge",    "送进 Max 桥协议自测",    ["scripts/selftest_max_bridge.py"], False),
    # 降级文案 vs 降级实现：这条漂过两次且方向相反（先「尽量映射」实际不写、
    # 后「不写任何参数」实际已重写），两处相隔 500 行分属不同文件，人工读不出来。
    # 正例盯现行一致性，反例证明判据有判别力（任一反例不被抓住即整体失败）。
    ("fallback_claim", "降级文案一致性",        ["scripts/fallback_claim_check.py"], False),
    ("fallback_neg",   "降级文案判据反例自测",  ["scripts/fallback_claim_check.py", "--selftest"], False),
    ("ui",            "主界面结构",            ["scripts/ui_check.py"],          True),
    ("ui_solid",      "纯色模式像素",          ["scripts/ui_solid_check.py"],    True),
]

# 各脚本的收尾行格式不一，统一按关键字捞最后一条结论
TAIL_RE = re.compile(r"(汇总[:：]|结论[:：]|ALL PASS|全部通过)")


def _summary(stdout: str) -> str:
    hits = [ln.strip() for ln in stdout.splitlines() if TAIL_RE.search(ln)]
    if hits:
        return hits[-1][:60]
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    return (lines[-1][:60] if lines else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="3dsmax-material-forge 统一检查入口")
    ap.add_argument("--fast", action="store_true", help="跳过需要浏览器的 UI 检查")
    ap.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    ap.add_argument("--only", help="只跑指定项，逗号分隔：evals,export_guard,registry,proc_mask,ui,ui_solid")
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",")} if args.only else None
    picked = [c for c in CHECKS if (only is None or c[0] in only) and not (args.fast and c[3])]
    if not picked:
        print("没有匹配的检查项")
        return 2

    rows, failed = [], 0
    for key, label, cmd, _browser in picked:
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable] + cmd, cwd=str(ROOT), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=1800)
            rc, out, err = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", "超时 1800s"
        cost = time.time() - t0
        ok = rc == 0
        failed += 0 if ok else 1
        rows.append({"key": key, "name": label, "ok": ok, "rc": rc,
                     "seconds": round(cost, 1), "summary": _summary(out)})
        if not args.json:
            flag = "PASS" if ok else "FAIL"
            print("[%s] %-18s %6.1fs  %s" % (flag, label, cost, rows[-1]["summary"]))
            if not ok:
                # 失败时把尾部输出贴出来，省得再单独跑一遍
                for ln in (out.splitlines() + err.splitlines())[-25:]:
                    print("       | " + ln)

    ok_all = failed == 0
    if args.json:
        print(json.dumps({"ok": ok_all, "failed": failed, "total": len(rows), "checks": rows},
                         ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 62)
        print("总计 %d 项检查，%s" % (len(rows), "全部通过" if ok_all else "%d 项失败" % failed))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
