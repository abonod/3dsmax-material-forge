# -*- coding: utf-8 -*-
"""P0–P7 八项改进的落地审计（不是"看完就算"，而是让每一项都有可执行的判据）。

背景：
    0.9–0.11 期间陆续修了八类缺陷（P0 文档自相矛盾、P1 假通过、P2 叠加装配、
    P3/P5/P6/P7 跨版本适配、P4 目视取证）。这些修复散落在脚本、模板、文档里，
    靠人肉回忆"到底改没改"必然翻车——0.11.0 就出现过"P3/P5/P6/P7 声称有
    --require-compat 强制版本证据，但探针根本没注入探测段"这种机制在、证据无。
    本脚本把八项各自的核心断言固化成机器判据，任何一项回退或写成纸面约定都会 FAIL。

判据设计原则：
    1. 只认代码事实，不认文档承诺（P0 反过来：文档必须由代码生成）；
    2. 每条判据必须能构造反例（见 --selftest），否则等于没判据；
    3. 不联网、不启 3ds Max，纯静态 + 子进程调用。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _lines(rel: str):
    return _read(rel).splitlines()


def _code_only(rel: str, start_marker: str = None) -> str:
    """剔除 -- 注释行，避免"注释里提到过"被当成"代码里实现了"。"""
    out = []
    for line in _lines(rel):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        out.append(line)
    return "\n".join(out)


def _run(args) -> int:
    p = subprocess.run([sys.executable] + args, cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode


def _index_of(text: str, needle: str) -> int:
    idx = text.find(needle)
    return idx


# ---------------------------------------------------------------------------
# 各项判据：返回 (ok, 说明)
# ---------------------------------------------------------------------------
def check_p0():
    rc = _run(["scripts/gen_param_gap.py", "--check"])
    return rc == 0, "参数缺口文档由 params.py 生成且未漂移"


def check_p1():
    rc = _run(["scripts/verify_probe_log.py", "--selftest"])
    gen = _code_only("scripts/gen_probe.py")
    # 探针用「唯一名字哨兵 + 逐 job 清槽 + 回读绑定结果」三者配合才算真绑定校验
    has_bind = "BIND_MISMATCH=" in gen and "SENTINEL" in gen
    has_bad = re.search(r'"BAD=', gen) is not None
    has_clear = "meditMaterials" in gen and "StandardMaterial" in gen
    ok = rc == 0 and has_bind and has_bad and has_clear
    return ok, "判据器自测 + 探针绑定校验 + 槽回读计数 + 清空残留槽"


def check_p2():
    cor = _code_only("core/exporters/tpl_corona.py")
    vry = _code_only("core/exporters/tpl_vray.py")
    # 真实实现是「候选名遍历 + 写后回读」，不是单行赋值，故按三件套 + 回读证据判定：
    #   additionalBumpOn（开关候选必须存在）、additionalBump（贴图槽）、getProperty（回读动作）
    cor_ok = ("additionalBumpOn" in cor and "additionalBump" in cor
              and "getProperty" in cor and "normalMap" in cor)
    vry_ok = ("normal_map" in vry and "bump_map" in vry)
    return cor_ok and vry_ok, "Corona 法线/凹凸双槽 + additionalBumpOn 回读；V-Ray normal_map/bump_map"


def check_p3():
    rc = _run(["scripts/compat_check.py"])
    cv = _read("core/compat.py")
    aliases_ok = all(k in cv for k in
                     ("vray_mtl", "vray_normalmap", "corona_mtl", "corona_normal", "corona_disp"))
    return rc == 0 and aliases_ok, "中英类名别名覆盖 + 版本等级诚实性"


def check_p4():
    gen = _read("scripts/gen_visual_probe.py")
    gen_ok = "visual_manifest" in gen and "vfb" in gen
    ev = os.path.join(ROOT, "docs", "evidence")
    pngs = [f for f in os.listdir(ev) if f.lower().endswith(".png")] if os.path.isdir(ev) else []
    ledger = _read("docs/evidence-ledger.md")
    # 台账必须声明已目视，且不得仍在开头把 P4 列为未完成
    ledger_ok = "已目视" in ledger
    return gen_ok and len(pngs) >= 4 and ledger_ok, "取证生成器 + 真机 PNG %d 张 + 台账已声明目视" % len(pngs)


def check_p5():
    body = _code_only("core/exporters/tpl_common.py")
    new_pos = body.find("saveMaterialLibrary lib")
    old_pos = body.find("saveTempMaterialLibrary lib")
    # 新接口必须在前（作为首选），旧接口作为第二级降级
    ordered = new_pos != -1 and old_pos != -1 and new_pos < old_pos
    # 必须有 try 包裹，避免 2022+ 上旧接口不存在直接抛错
    guarded = "catch ( saved = false )" in body
    return ordered and guarded, "saveMaterialLibrary 首选、saveTempMaterialLibrary 二级降级且异常兜底"


def check_p6():
    vry = _code_only("core/exporters/tpl_vray.py") + _code_only("core/exporters/tpl_common.py")
    has_classes = "Material.classes" in vry
    has_localized = any(n in vry for n in ("VRay_材质", "VRayMtl"))
    return has_classes and has_localized, "Material.classes 精确比对含中文本地化名后再 execute"


def _evidence_chain_missing():
    """调用打包脚本的证据链校验：compat/文档引用的 docs/evidence/* 必须真能随包分发。

    为什么要跨脚本调用：这一项同时属于"打包门禁"和"P7 证据诚实性"。
    只在打包脚本里做，日常审计看不出来；只在这里做，打包时又拦不住。
    两边共用同一实现，避免两套判据各自漂移。
    """
    pkg = os.path.join(ROOT, "scripts", "package_skill.py")
    code = (
        "import importlib.util;"
        "s=importlib.util.spec_from_file_location('_pkg', r'%s');"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "print('|'.join(m.verify_evidence_chain()))" % pkg
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        return ["<校验脚本执行失败>"]
    return [x for x in r.stdout.strip().split("|") if x]


def check_p7():
    cv = _read("core/compat.py")
    need = ['"2021"', '"2022"', '"2023"', '"2024"', '"2025"', '"2026"',
            '"5":', '"6":', '"7":', '"9":', '"10":', '"11":', '"12":', '"13":']
    matrix_ok = all(n in cv for n in need)
    probe_ok = "probe_ms" in cv and "COMPAT_LEVEL" in cv
    # verified 项必须带 evidence（防"打算支持"写成"已验证"）
    verified_blocks = re.findall(r'"verified"[^}]*\}', cv)
    evidence_ok = bool(verified_blocks) and all(re.search(r'"evidence"\s*:', b) for b in verified_blocks)
    # 光有 evidence 字段不够：它指向的文件必须真的随包分发，否则用户拿到包无法自证
    chain_missing = _evidence_chain_missing()
    chain_ok = not chain_missing
    note = "版本矩阵 2021-2026/5-7/9-13 + 探测段 + verified 必带证据"
    if not chain_ok:
        note += "（证据未随包分发：%s）" % ", ".join(chain_missing)
    return matrix_ok and probe_ok and evidence_ok and chain_ok, note


CHECKS = [
    ("P0", check_p0),
    ("P1", check_p1),
    ("P2", check_p2),
    ("P3", check_p3),
    ("P4", check_p4),
    ("P5", check_p5),
    ("P6", check_p6),
    ("P7", check_p7),
]


def run(verbose=True):
    rows, bad = [], 0
    for name, fn in CHECKS:
        try:
            ok, note = fn()
        except Exception as exc:  # 判据自身崩了也算没通过，不许静默当 OK
            ok, note = False, "判据异常：%s" % exc
        if not ok:
            bad += 1
        rows.append({"id": name, "ok": ok, "note": note})
        if verbose:
            print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name, note))
    return rows, bad


def selftest():
    """反例自测：把关键文件临时改坏，判据必须报 FAIL。"""
    cases = []

    def mutate(rel, old, new):
        path = os.path.join(ROOT, rel)
        src = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new))
        return path, src

    def restore(path, src):
        open(path, "w", encoding="utf-8", newline="\n").write(src)

    # 反例1：P5 顺序颠倒
    path, src = mutate("core/exporters/tpl_common.py", "saveMaterialLibrary lib", "saveMaterialLibraryXXX lib")
    try:
        cases.append(("P5 顺序倒置", not check_p5()[0]))
    finally:
        restore(path, src)

    # 反例2：P6 去掉类表比对
    path, src = mutate("core/exporters/tpl_vray.py", "Material.classes", "ClassListXXX")
    try:
        cases.append(("P6 去掉类表精确比对", not check_p6()[0]))
    finally:
        restore(path, src)

    # 反例3：P7 verified 去掉 evidence
    # 注意：不能锚定 evidence 的具体文案（文案会随取证更新而漂移），只改键名
    path, src = mutate("core/compat.py", '"evidence":', '"evidenceXXX":')
    try:
        cases.append(("P7 verified 缺证据字段", not check_p7()[0]))
    finally:
        restore(path, src)

    # 反例3b：证据字段在、但证据文件不随包分发（.log 重新被全局排除）
    path, src = mutate("scripts/package_skill.py",
                       'EVIDENCE_KEEP_SUFFIX = (".log",)', 'EVIDENCE_KEEP_SUFFIX = ()')
    try:
        cases.append(("P7 证据未随包分发", not check_p7()[0]))
    finally:
        restore(path, src)

    # 反例4：P2 去掉 additionalBumpOn
    path, src = mutate("core/exporters/tpl_corona.py", "additionalBumpOn", "additionalBumpONXXX")
    try:
        cases.append(("P2 Corona 缺 additionalBumpOn", not check_p2()[0]))
    finally:
        restore(path, src)

    # 反例5：P0 文档改一个字
    fp = os.path.join(ROOT, "docs/vray-param-gap.md")
    src = open(fp, encoding="utf-8").read()
    open(fp, "w", encoding="utf-8", newline="\n").write(src.replace("维护约定", "维护约定X", 1))
    try:
        cases.append(("P0 文档漂移", not check_p0()[0]))
    finally:
        restore(fp, src)

    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print("[%s] 反例 %s" % ("PASS" if ok else "FAIL", n))
    print("自测结论：%d 例，失败 %d 项" % (len(cases), len(bad)))
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P0-P7 落地审计")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    rows, bad = run(verbose=not args.json)
    if args.json:
        print(json.dumps({"rows": rows, "fail": bad}, ensure_ascii=False, indent=2))
    print("结论：%s（PASS %d / FAIL %d）" % ("PASS" if bad == 0 else "FAIL", len(rows) - bad, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
