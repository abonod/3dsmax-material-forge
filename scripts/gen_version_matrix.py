#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 docs/version-matrix.md（P3 + P5 + P6 + P7 的对外交付物）。

事实来源是 core/compat.py 的 MATRIX 与 LANG_ALIASES，文档只是它的渲染结果。
为什么不让文档手写：这个项目已经吃过"文档比代码旧"的亏（旧 vray-param-gap.md
把已实现的 SSS/雾化标成"缺失"）。版本矩阵比参数表更容易漂，因为它的每一条都
依赖外部软件的发布节奏，手写必然过期。

三档适配等级（与 core/compat.py 完全一致）：
    verified   有真机日志并通过判据器  —— 只有本机基线能到这档
    supported  代码按该版本属性差异做了分支，但未取真机证据
    untested   未做针对性适配，不做承诺

用法：
    python scripts/gen_version_matrix.py            # 写 docs/version-matrix.md
    python scripts/gen_version_matrix.py --check    # 漂移则退出码 1
    python scripts/gen_version_matrix.py --stdout
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.compat import MATRIX, LANG_ALIASES, LOCAL_BASELINE  # noqa: E402

DOC = os.path.join(ROOT, "docs", "version-matrix.md")

LEVEL_NOTE = {
    "verified": "真机日志已验证（日志须通过 scripts/verify_probe_log.py）",
    "supported": "代码已按该版本差异分支，缺真机证据",
    "untested": "未做针对性适配，不做承诺",
}


def build() -> str:
    out = []
    a = out.append
    a("# 版本适配矩阵（自动生成，禁止手改）\n")
    a("> 本文件由 `scripts/gen_version_matrix.py` 从 `core/compat.py` 生成。")
    a("> 改矩阵请改 `core/compat.py`，再跑一次生成器；`--check` 会在漂移时返回非零。\n")
    a("## 一句话结论\n")
    a("**只有本机基线（3ds Max %s %s / V-Ray %s / Corona %s）到 `verified`。**"
      % (LOCAL_BASELINE["max"],
         "中文版" if LOCAL_BASELINE["max_lang"] == "zh" else "英文版",
         LOCAL_BASELINE["vray"], LOCAL_BASELINE["corona"]))
    a("其余版本一律 `supported` 或 `untested`——这不是「打算支持」，是「尚未取证」。"
      "把 `supported` 说成「支持」属于虚假宣传，文档与日志统一用上述三档词。\n")

    a("## 等级语义\n")
    a("| 等级 | 含义 |")
    a("| --- | --- |")
    for k in ("verified", "supported", "untested"):
        a("| `%s` | %s |" % (k, LEVEL_NOTE[k]))
    a("")

    for prod, label in (("max", "3ds Max"), ("vray", "V-Ray"), ("corona", "Corona")):
        vers = MATRIX[prod]
        a("## %s（%d 个版本）\n" % (label, len(vers)))
        a("| 版本 | 等级 | 已知差异 / 依据 |")
        a("| --- | --- | --- |")
        for v in vers:
            d = vers[v]
            feats = "；".join(d.get("features", [])) or "—"
            if d.get("evidence"):
                feats += "（证据：%s）" % d["evidence"]
            a("| %s | `%s` | %s |" % (v, d["level"], feats))
        a("")

    a("## 跨语言：类名本地化映射\n")
    a("中文版 3ds Max 会把插件注册名一起本地化，`execute(\"英文名\")` 在中文版上会静默失败。"
      "所有类名定位必须先对 `classes` 做字符串精确比对。当前覆盖：\n")
    a("| 概念 | 英文注册名 | 中文注册名 |")
    a("| --- | --- | --- |")
    for k, v in LANG_ALIASES.items():
        a("| `%s` | %s | %s |" % (k, "`" + "`, `".join(v["en"]) + "`",
                                  "`" + "`, `".join(v["zh"]) + "`"))
    a("")

    a("## 现场取证方法（把 untested 变 verified 的唯一路径）\n")
    a("1. 在目标机器上导出材质：生成脚本位于 `<out>/<renderer>/*.ms`；")
    a("2. 生成探针：`python scripts/gen_probe.py --ms-dir <out> --out probe.ms`；")
    a("3. 在 3ds Max 里 Run Script，把 Listener 输出存成 `probe.log`；")
    a("4. 判定：`python scripts/verify_probe_log.py probe.log --expect-jobs <N>`；")
    a("5. 只有第 4 步 PASS，且日志里 `COMPAT_LEVEL` 非 `untested`，才允许把该版本改成 `verified`。\n")
    a("日志里的版本证据字段（由 `core/compat.py:probe_ms()` 注入）：")
    a("`MAX_VERSION`、`MAX_LANG`、`ACTIVE_RENDERER`、`VRAY_AVAILABLE`、"
      "`CORONA_AVAILABLE`、`COMPAT_LEVEL`。\n")

    a("## 待补证据清单（P3 / P4 遗留）\n")
    a("| 项 | 现状 | 需要什么 |")
    a("| --- | --- | --- |")
    a("| 英文版 Max | 无证据 | 在英文版 Max 上跑一次探针，确认类名走 en 分支 |")
    a("| 第二渲染器真机 | 仅 V-Ray 6 / Corona 12 | 按上表 `supported` 版本各跑一次 |")
    a("| 色散目视 | 仅属性回读 | 渲染色散材质截图，人眼确认边缘分离 |")
    a("| 法线+凹凸双层目视 | 仅属性回读 | 渲染双层叠加材质截图，确认两层细节同时可见 |")
    a("")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="生成版本适配矩阵文档")
    ap.add_argument("--check", action="store_true", help="仅比对是否漂移")
    ap.add_argument("--stdout", action="store_true", help="打印到标准输出")
    args = ap.parse_args(argv)

    text = build()
    if args.stdout:
        print(text, end="")
        return 0
    if args.check:
        if not os.path.exists(DOC):
            print("[FAIL] 文档不存在：%s" % DOC)
            return 1
        cur = open(DOC, encoding="utf-8").read()
        if cur != text:
            print("[FAIL] docs/version-matrix.md 与 core/compat.py 不一致，请重新生成")
            return 1
        print("[OK] docs/version-matrix.md 与 core/compat.py 一致")
        return 0

    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    open(DOC, "w", encoding="utf-8", newline="\n").write(text)
    print("[OK] 已生成 version-matrix.md（%d 行）" % len(text.splitlines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
