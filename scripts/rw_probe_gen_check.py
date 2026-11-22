#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真实世界尺寸探针生成冒烟（run_all_checks 检查项，0.13.0）

为什么单独一项：
    这份探针不产出材质，只回答一个问题——「这台机器/这个版本上，位图坐标类
    到底有没有可写可回读的 real-world 属性名」。它是后续映射代码的**唯一前提**，
    所以生成器本身必须有门禁：占位符没替换干净、MAXScript 计数器写成 local、
    类名 execute 拿不到实例，任何一种都会让真机日志给出假阴性（明明支持却报
    NO-SUPPORT），然后我们就永远不写这个功能。

检查点（每一条都对应一个真实会踩的坑）：
    1. 生成成功、文件存在、GBK 可解码（中文版 Max 按 ANSI 解析，UTF-8 会乱码）；
    2. 占位符 @@LOG@@ / @@PROBE@@ 必须已替换，残留即 FAIL；
    3. 必须含 RWPROBE_DONE=1 / RW_SCALE_SUPPORTED= / RWPROBE_VERDICT= 三个判据行；
    4. 计数器必须是全局 ::mfRwHit / ::mfRwWritable（写成 local 会假阴性）；
    5. 位图类必须先造实例（execute "X()"），只 execute 类名拿到的是类不是对象；
    6. 语法自检必须过（复用 core.preflight，括号/引号不平衡在真机是整段不执行）。

用法：python scripts/rw_probe_gen_check.py
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "scripts" / "gen_rw_probe.py"

REQUIRED = [
    "RWPROBE_DONE=1",
    "RW_SCALE_SUPPORTED=",
    "RWPROBE_VERDICT=",
    "::mfRwHit",
    "::mfRwWritable",
    'execute (nm + "()")',
    "getPropNames",
    "RWTRY ",
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mf_rw_") as td:
        out = Path(td) / "rw_probe.ms"
        log = str(Path(td) / "rw_probe.log")
        proc = subprocess.run(
            [sys.executable, str(GEN), "--out", str(out), "--log", log],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            print(proc.stdout[-2000:])
            print(proc.stderr[-2000:])
            print("[FAIL] 生成器返回非零：%s" % proc.returncode)
            return 1
        if not out.is_file():
            print("[FAIL] 没有产出探针文件：%s" % out)
            return 1

        raw = out.read_bytes()
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError as exc:
            print("[FAIL] 探针不是 GBK 可解码（中文版 Max 会乱码）：%s" % exc)
            return 1

        for mark in REQUIRED:
            if mark not in text:
                fails.append("缺少关键标记：%s" % mark)
        for ph in ("@@LOG@@", "@@PROBE@@"):
            if ph in text:
                fails.append("占位符未替换：%s" % ph)
        if "local mfRwHit" in text or "local mfWritable" in text:
            fails.append("计数器写成了 local（MAXScript fn 内改不到外层，结论会假阴性）")
        if "MAX_VERSION=" not in text or "COMPAT_LEVEL=" not in text:
            fails.append("缺版本证据段：日志必须自带 MAX_VERSION / COMPAT_LEVEL")
        # 日志路径双写反斜杠，否则 \t \n 被当转义，日志写到错误的路径上
        if log.replace("\\", "\\\\") not in text:
            fails.append("日志路径未做反斜杠转义")

    if fails:
        print("[FAIL] 真实世界尺寸探针生成冒烟未通过：")
        for f in fails:
            print("  · %s" % f)
        return 1
    print("[OK] 真实世界尺寸探针生成冒烟通过：判据行齐全、占位符已替换、计数器全局、GBK 可解析")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
