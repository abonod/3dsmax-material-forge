#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真机探针日志判据器（P1 机制性阻断的核心）。

背景（这个脚本存在的原因）：
    旧流程判断"真机验证通过"的方式是 grep 日志里有没有 "成功: N  失败: 0"。
    这个判据有两个致命漏洞：
      1) 探针里 fileIn 失败被 `catch ( 0 )` 吞掉，`失败` 计数不增，日志照旧全绿；
      2) fileIn 全失败后，探针回读的是场景里**上一轮残留的旧材质**，属性齐全、
         名字看着也对，于是"回读成功"被当成"新材质挂载成功"。
    两个漏洞叠加 → 完全没验证，却得到一份绿色的验收记录。这就是"假通过"。

    本脚本把判据从"有没有报错"改成"链路是否被绑定证明"：
      必须同时满足 5 条硬条件才算 PASS，任何一条缺失、缺失证据也算 FAIL
      （旧判据是"没看到问题就放行"，这里改成"没看到证明就不放行"）。

硬条件（缺一即 FAIL）：
    C1 SCRIPT_RUN=1                 探针真的跑到了结尾（没跑完就没资格谈结论）
    C2 FILEIN 全部 OK 且数量达标     每份材质 ms 都被 fileIn 成功，数量与期望一致
    C3 BIND_MISMATCH=0              回读到的材质，名字与类型都与期望完全一致
    C4 VERDICT=OK                   脚本内自检结论为 OK（内部已把 FAIL/BAD 汇总）
    C5 BAD=0 且无 [FAIL] 行          槽位写入/回读校验零失败

用法：
    python scripts/verify_probe_log.py <log.txt>
    python scripts/verify_probe_log.py <log.txt> --expect-jobs 4 --expect-material 石材
    python scripts/verify_probe_log.py --selftest      # 自测判据本身

退出码：0 = PASS（全部硬条件满足），1 = FAIL（附逐条原因）
"""

from __future__ import annotations

import argparse
import re
import sys

RE_SCRIPT_RUN = re.compile(r"^\s*SCRIPT_RUN\s*=\s*(\d+)\s*$", re.M)
RE_BIND = re.compile(r"^\s*BIND_MISMATCH\s*=\s*(\d+)\s*$", re.M)
RE_VERDICT = re.compile(r"^\s*VERDICT\s*=\s*([A-Z_]+)\s*$", re.M)
RE_BAD = re.compile(r"^\s*(?:BAD|槽位校验失败)\s*[:=]\s*(\d+)\s*$", re.M)
# 真机输出形如「FILEIN corona/xx.ms -> OK（材质 xxx，贴图槽 12 个）」，
# 旧正则强制行尾为 OK，导致真机合格日志被判 0 条（假不通过）。
RE_FILEIN_OK = re.compile(r"^\s*FILEIN\s+\S+\s*->\s*OK\b.*$", re.M)
RE_FILEIN_FAIL = re.compile(r"^\s*FILEIN\s+\S+\s*->\s*FAIL", re.M)
RE_FAIL_LINE = re.compile(r"^\s*\[FAIL\]", re.M)
RE_DEGRADED = re.compile(r"^\s*(?:版本能力降级|DEGRADED)\s*[:=]\s*(\d+)\s*$", re.M)
RE_COMPAT_LEVEL = re.compile(r"^\s*COMPAT_LEVEL\s*=\s*(\S+)\s*$", re.M)
RE_MAX_VER = re.compile(r"^\s*MAX_VERSION\s*=\s*(\S+)\s*$", re.M)
RE_MAX_LANG = re.compile(r"^\s*MAX_LANG\s*=\s*(\S+)\s*$", re.M)


def _read_log(path: str) -> str:
    """按候选编码读取探针日志，取中文可辨识的那一版。

    中文 Windows 上 3ds Max 的 Listener/format 输出落盘是 ANSI(GBK)，
    而判据器此前固定按 UTF-8 读，中文行全部变成替换符，
    「槽位校验失败: 0」这类判据直接匹配不到 —— 真机合格日志被判 FAIL（假不通过）。
    """
    raw = open(path, "rb").read()
    marks = ("槽位校验失败", "成功:", "材质类型", "版本能力降级")
    best, best_hits = None, -1
    for enc in ("utf-8-sig", "gbk", "utf-16", "big5"):
        try:
            t = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        hits = sum(1 for m in marks if m in t)
        if hits > best_hits:
            best, best_hits = t, hits
        if hits == len(marks):
            break
    if best is None or best_hits <= 0:
        return raw.decode("utf-8", errors="replace")
    return best


def verify(text: str, expect_jobs: int | None = None,
           expect_material: str | None = None,
           require_compat: bool = False) -> tuple[bool, list[str]]:
    """返回 (是否通过, 逐条判据说明)。说明行以 [OK]/[FAIL]/[WARN] 开头。"""
    notes: list[str] = []
    ok = True

    def bad(msg: str) -> None:
        nonlocal ok
        ok = False
        notes.append("[FAIL] " + msg)

    def good(msg: str) -> None:
        notes.append("[OK] " + msg)

    # C1 探针跑完
    m = RE_SCRIPT_RUN.search(text)
    if not m:
        bad("C1 缺少 SCRIPT_RUN 标记：探针未执行到结尾（可能是语法错误、渲染器缺失或中途异常），"
            "此日志不具备任何验收资格")
    elif m.group(1) != "1":
        bad("C1 SCRIPT_RUN=%s，非 1：探针未完整执行" % m.group(1))
    else:
        good("C1 SCRIPT_RUN=1，探针完整执行")

    # C2 fileIn 全部成功 + 数量达标
    n_ok = len(RE_FILEIN_OK.findall(text))
    n_fail = len(RE_FILEIN_FAIL.findall(text))
    if n_fail > 0:
        bad("C2 存在 %d 条 FILEIN -> FAIL（fileIn 失败，后续回读必为残留旧材质）" % n_fail)
    if expect_jobs is not None:
        if n_ok != expect_jobs:
            bad("C2 FILEIN 成功 %d 条，期望 %d 条（少跑即漏验，多跑即清单不一致）"
                % (n_ok, expect_jobs))
        else:
            good("C2 FILEIN 成功 %d/%d 条" % (n_ok, expect_jobs))
    elif n_ok == 0:
        bad("C2 没有任何 FILEIN -> OK 记录：材质脚本并未真正载入")
    else:
        good("C2 FILEIN 成功 %d 条" % n_ok)

    # C3 绑定一致
    m = RE_BIND.search(text)
    if not m:
        bad("C3 缺少 BIND_MISMATCH 标记：无法证明回读对象就是刚载入的对象"
            "（这正是历史假通过的直接成因，缺证据一律不通过）")
    elif m.group(1) != "0":
        bad("C3 BIND_MISMATCH=%s：回读到的材质与本次载入的材质不是同一个" % m.group(1))
    else:
        good("C3 BIND_MISMATCH=0，回读对象与载入对象绑定一致")

    # C4 脚本内自检结论
    m = RE_VERDICT.search(text)
    if not m:
        bad("C4 缺少 VERDICT 标记：脚本未给出自检结论")
    elif m.group(1) == "DEGRADED":
        # 版本能力降级：脚本跑通、校验零失败，但目标版本缺某项能力。
        # 既不能当 OK（会宣称功能完整），也不能当 FAIL（会把版本差异误判成缺陷）。
        dm = RE_DEGRADED.search(text)
        good("C4 VERDICT=DEGRADED：版本能力降级 %s 项（功能非完整，需人工确认降级项可接受）"
             % (dm.group(1) if dm else "?"))
    elif m.group(1) != "OK":
        bad("C4 VERDICT=%s，脚本自检未通过" % m.group(1))
    else:
        good("C4 VERDICT=OK")

    # C4b 降级计数与结论必须自洽：日志写着有降级却报 OK，等于宣称"该版本功能完整"，
    # 通常是生成脚本版本过旧或人为改过结论，一律不采信。
    dm = RE_DEGRADED.search(text)
    vm = RE_VERDICT.search(text)
    if dm and int(dm.group(1)) > 0 and vm and vm.group(1) == "OK":
        bad("C4 版本能力降级 %s 项但 VERDICT=OK：结论与降级计数矛盾，不可采信" % dm.group(1))

    # C5 槽位校验零失败
    bads = [int(x) for x in RE_BAD.findall(text)]
    if not bads:
        bad("C5 缺少 槽位校验失败/BAD 计数：无法证明每个槽位写入后都回读成功")
    elif any(b != 0 for b in bads):
        bad("C5 槽位校验失败 %s 项，存在写入未生效" % bads)
    else:
        good("C5 槽位校验失败 0 项")

    n_failline = len(RE_FAIL_LINE.findall(text))
    if n_failline:
        bad("C5 日志含 %d 条 [FAIL] 行，详见日志" % n_failline)

    # C6 版本证据（--require-compat 时强制）：让每次 PASS 都能追溯到具体版本，
    # 否则跨版本结论无从谈起——同一份日志在 2024 通过，不能代表 2026 也通过。
    if require_compat:
        lv = RE_COMPAT_LEVEL.search(text)
        mv = RE_MAX_VER.search(text)
        lg = RE_MAX_LANG.search(text)
        if not lv:
            bad("C6 缺少 COMPAT_LEVEL 标记：日志无法证明运行环境版本（重新用新版探针生成器取证）")
        elif lv.group(1) == "untested":
            bad("C6 COMPAT_LEVEL=untested：现场类表未命中法线节点，渲染器链路可疑")
        else:
            good("C6 COMPAT_LEVEL=%s" % lv.group(1))
        if not mv or mv.group(1) in ("0", "unknown"):
            bad("C6 缺少有效 MAX_VERSION（3ds Max 版本未取证）")
        else:
            good("C6 MAX_VERSION=%s" % mv.group(1))
        if not lg or lg.group(1) == "unknown":
            bad("C6 缺少有效 MAX_LANG（语言分支未取证）")
        else:
            good("C6 MAX_LANG=%s" % lg.group(1))

    # 附带核对：期望材质名是否在日志中出现（若给出）
    if expect_material:
        if expect_material in text:
            good("材质名 '%s' 出现在日志中" % expect_material)
        else:
            notes.append("[WARN] 日志未出现期望材质名 '%s'，请确认是否验错了对象"
                         % expect_material)

    return ok, notes


def _selftest() -> int:
    """用构造日志验证判据本身：好日志必须 PASS，各类坏日志必须 FAIL。"""
    good = """
SCRIPT_RUN=1
FILEIN vray/a.ms -> OK
FILEIN vray/b.ms -> OK
BIND_MISMATCH=0
槽位校验失败: 0
VERDICT=OK
"""
    cases = [
        ("正常日志", good, True),
        ("fileIn 失败（历史假通过场景）", good.replace("FILEIN vray/b.ms -> OK", "FILEIN vray/b.ms -> FAIL"), False),
        ("缺 BIND_MISMATCH（无法证明绑定）", "\n".join(l for l in good.splitlines() if "BIND_MISMATCH" not in l), False),
        ("未跑完（缺 SCRIPT_RUN）", "\n".join(l for l in good.splitlines() if "SCRIPT_RUN" not in l), False),
        ("VERDICT=FAIL", good.replace("VERDICT=OK", "VERDICT=FAIL"), False),
        ("槽位校验失败>0", good.replace("槽位校验失败: 0", "槽位校验失败: 3"), False),
        ("含 [FAIL] 行", good + "\n  [FAIL] 凹凸叠加层：additionalBumpOn 开关置位失败", False),
        # 版本能力降级：必须放行（不是实现缺陷），但结论要标 DEGRADED
        ("VERDICT=DEGRADED（版本缺能力）",
         good.replace("VERDICT=OK", "VERDICT=DEGRADED\n版本能力降级: 2"), True),
        # 有降级但 VERDICT 仍写 OK：属于自检逻辑没跟上，判 FAIL
        ("DEGRADED>0 但 VERDICT=OK",
         good + "\n版本能力降级: 1", False),
        # 真机回归（2026-11-20）：Max 输出把中文说明缀在 FILEIN 行尾，
        # 旧正则要求行尾严格是 OK，导致合格日志被判 C2=0 条。
        ("FILEIN 行带中文后缀说明（真机形态）",
         good.replace("FILEIN vray/a.ms -> OK",
                      "FILEIN vray/a.ms -> OK（材质 mf_a_vray，贴图槽 9 个）"), True),
    ]
    fails = 0
    for name, txt, want in cases:
        ok, notes = verify(txt)
        hit = (ok == want)
        print("%s %s → %s（期望 %s）" % ("[OK]" if hit else "[FAIL]", name,
                                         "PASS" if ok else "FAIL",
                                         "PASS" if want else "FAIL"))
        if not hit:
            fails += 1
            for n in notes:
                print("      " + n)
    # C6 版本证据核对（P3/P5/P6/P7 取证前提）
    compat = good + "\nCOMPAT_LEVEL=supported\nMAX_VERSION=26000\nMAX_LANG=zh"
    ok, _ = verify(compat, require_compat=True)
    print("%s 含版本证据 + require_compat → %s（期望 PASS）"
          % ("[OK]" if ok else "[FAIL]", "PASS" if ok else "FAIL"))
    fails += 0 if ok else 1
    ok, _ = verify(good, require_compat=True)
    print("%s 缺版本证据 + require_compat → %s（期望 FAIL）"
          % ("[OK]" if not ok else "[FAIL]", "FAIL" if not ok else "PASS"))
    fails += 0 if not ok else 1

    # 数量核对
    ok, _ = verify(good, expect_jobs=3)
    if ok:
        print("[FAIL] 期望 job 数不符时应 FAIL，实际放行")
        fails += 1
    else:
        print("[OK] 期望 job 数不符 → FAIL")

    # 真机编码回归：3ds Max 在中文 Windows 上把 Listener 输出按 ANSI(GBK) 落盘，
    # 判据器若硬读 UTF-8，中文判据行会变乱码，合格日志被判 C5 缺失。
    import os as _os
    import tempfile as _tf
    with _tf.NamedTemporaryFile("wb", suffix=".log", delete=False) as fh:
        fh.write(good.encode("gbk"))
        tmp = fh.name
    try:
        ok, _ = verify(_read_log(tmp), expect_jobs=2)
        print("%s GBK 落盘日志经多编码读取 → %s（期望 PASS）"
              % ("[OK]" if ok else "[FAIL]", "PASS" if ok else "FAIL"))
        fails += 0 if ok else 1
    finally:
        _os.unlink(tmp)

    print("自测 %s（失败 %d 项）" % ("通过" if fails == 0 else "未通过", fails))
    return 0 if fails == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真机探针日志判据器")
    ap.add_argument("log", nargs="?", help="探针日志文件路径")
    ap.add_argument("--expect-jobs", type=int, default=None, help="期望的 FILEIN 成功条数")
    ap.add_argument("--expect-material", default=None, help="期望出现的材质名")
    ap.add_argument("--require-compat", action="store_true",
                    help="额外要求版本证据（COMPAT_LEVEL / MAX_VERSION / MAX_LANG），跨版本取证时必须打开")
    ap.add_argument("--selftest", action="store_true", help="自测判据本身")
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")

    if args.selftest:
        return _selftest()
    if not args.log:
        ap.print_help()
        return 2

    try:
        text = _read_log(args.log)
    except OSError as e:
        print("[FAIL] 无法读取日志：%s" % e)
        return 1

    ok, notes = verify(text, args.expect_jobs, args.expect_material, args.require_compat)
    for n in notes:
        print(n)
    print("\n结论：%s" % ("PASS（链路已被绑定证明）" if ok else "FAIL（存在假通过风险，不可采信）"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
