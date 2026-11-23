#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真实世界尺寸探针生成冒烟（run_all_checks 检查项）

为什么单独一项：
    这份探针不产出材质，只回答一个问题——「这台机器/这个版本上，位图坐标类
    到底有没有可写可回读的 real-world 属性名」。它是后续映射代码的**唯一前提**，
    所以生成器本身必须有门禁：占位符没替换干净、MAXScript 计数器写成 local、
    类名 execute 拿不到实例，任何一种都会让真机日志给出假阴性（明明支持却报
    NO-SUPPORT），然后我们就永远不写这个功能。

检查点（每一条都对应一个真实会踩的坑）：
    1. 生成成功、文件存在、GBK 可解码（中文版 Max 按 ANSI 解析，UTF-8 会乱码）；
    2. 占位符 @@LOG@@ / @@PROBE@@ 必须已替换，残留即 FAIL；
    3. 必须含 RWPROBE_RUN_BEGIN= / RWPROBE_DONE=1 / RW_SCALE_SUPPORTED= / RWPROBE_VERDICT=；
    4. 计数器必须是全局 ::mfHitCount / ::mfRwWritable（写成 local 会假阴性）；
    5. **全局计数器名不得与任何 fn 同名**——2026-11-23 真机踩到：计数器叫
       ::mfRwHit、谓词函数也叫 mfRwHit，首遍靠脚本作用域里 fn 优先侥幸跑通，
       第二遍 fileIn 时该名字已是数字 0，第 119 行「调用需要函数或类，得到的是: 0」
       当场崩、日志停在半截。撞名只有在真机第二次执行才炸，静态读根本看不出来；
    6. 位图类必须先造实例（execute "X()"），只 execute 类名拿到的是类不是对象；
    7. 语法自检必须过（复用 core.preflight，括号/引号不平衡在真机是整段不执行）。

    反例自测（N1-N4）证明上面第 3/4/5 条真有判别力：把生成物篡改成撞名、
    删掉分段头、计数器降级成 local、timestamp 不加 as string，判据必须抓到，
    否则这一项等于永远绿灯。

用法：python scripts/rw_probe_gen_check.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "scripts" / "gen_rw_probe.py"

REQUIRED = [
    "RWPROBE_RUN_BEGIN=",
    "((timestamp()) as string)",
    "RWPROBE_DONE=1",
    "RW_SCALE_SUPPORTED=",
    "RWPROBE_VERDICT=",
    "::mfHitCount",
    "::mfRwWritable",
    'execute (nm + "()")',
    "getPropNames",
    "RWTRY ",
]


def strip_comments(text: str) -> str:
    """去掉 -- 注释行：注释里提到旧名字不该算撞名。"""
    return "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("--"))


def check_text(text: str, log_path: str) -> list[str]:
    fails: list[str] = []
    for mark in REQUIRED:
        if mark not in text:
            fails.append("缺少关键标记：%s" % mark)
    for ph in ("@@LOG@@", "@@PROBE@@"):
        if ph in text:
            fails.append("占位符未替换：%s" % ph)
    if "local mfHitCount" in text or "local mfWritable" in text:
        fails.append("计数器写成了 local（MAXScript fn 内改不到外层，结论会假阴性）")

    code = strip_comments(text)
    fn_names = set(re.findall(r"^\s*fn\s+([A-Za-z_]\w*)", code, re.M))
    glob_names = set(re.findall(r"^\s*::([A-Za-z_]\w*)\s*=", code, re.M))
    clash = sorted(fn_names & glob_names)
    if clash:
        fails.append("全局计数器与 fn 同名：%s——首遍侥幸、第二遍 fileIn 必崩（调用需要函数或类，得到的是数字）"
                     % ", ".join(clash))

    if "MAX_VERSION=" not in text or "COMPAT_LEVEL=" not in text:
        fails.append("缺版本证据段：日志必须自带 MAX_VERSION / COMPAT_LEVEL")
    # 日志路径双写反斜杠，否则 \t \n 被当转义，日志写到错误的路径上
    if log_path.replace("\\", "\\\\") not in text:
        fails.append("日志路径未做反斜杠转义")
    return fails


# 反例：每种篡改都必须被 check_text 抓到，漏抓即整体 FAIL
def _negatives(text: str) -> list[tuple[str, str]]:
    return [
        ("N1 计数器与谓词函数同名",
         text.replace("fn mfIsRwProp p = (", "fn mfHitCount p = (")
             .replace("local hit = mfIsRwProp p", "local hit = mfHitCount p")),
        ("N2 分段头被删（多段日志无法切分）",
         text.replace('mfP ("RWPROBE_RUN_BEGIN=" + ((timestamp()) as string))', 'mfP ""')),
        ("N3 计数器降级成 local",
         text.replace("::mfHitCount = 0", "local mfHitCount = 0")),
        ("N4 timestamp() 未 as string（真机第 20 行类型错误，整份脚本中断）",
         text.replace("((timestamp()) as string)", "(timestamp())")),
    ]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
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

        fails = check_text(text, log)
        if fails:
            print("[FAIL] 真实世界尺寸探针生成冒烟未通过：")
            for f in fails:
                print("  · %s" % f)
            return 1

        # 反例：判据对篡改必须敏感
        neg_total = len(_negatives(text))
        leaked = []
        for name, hacked in _negatives(text):
            if hacked == text:
                leaked.append("%s（篡改未生效，用例本身失效）" % name)
            elif not check_text(hacked, log):
                leaked.append("%s（判据没抓到）" % name)
        if leaked:
            print("[FAIL] 探针门禁反例未被抓住：")
            for l in leaked:
                print("  · %s" % l)
            return 1

    print("[OK] 真实世界尺寸探针生成冒烟通过：判据行齐全、占位符已替换、计数器全局且不与 fn 撞名、"
          "分段头在位、GBK 可解析；反例 %d/%d 全被抓" % (neg_total, neg_total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
