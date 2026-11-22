#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""兼容矩阵一致性检查（P3 / P5 / P6 / P7 的交付门禁）。

检查三件事，任何一项不满足即非零退出：

  A. 文档同步    docs/version-matrix.md 必须与 core/compat.py 完全一致。
                 手改文档是这类项目最典型的漂移来源，必须机器拦截。
  B. 语言别名覆盖  LANG_ALIASES 里登记的中英文注册名，必须真的出现在模板的类名
                 候选链里。只写进矩阵、模板却不认，等于宣称支持中文版却不支持。
  C. 等级诚实性    MATRIX 中标 verified 的版本，必须恰好是本机基线（LOCAL_BASELINE），
                 且必须带 evidence 字段；非基线版本一旦标 verified 即视为造假。

为什么把"诚实性"做成硬检查：
    版本矩阵最容易变成营销文案——把"打算支持"写成"支持"，把"跑过一次没报错"写成
    "已验证"。检查脚本不拦，文档三周后就会变成谎言，而使用者会拿它当承诺。
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.compat import LANG_ALIASES, MATRIX, LOCAL_BASELINE  # noqa: E402

FAILS = []
OKS = []


def ok(msg):
    OKS.append("[OK] " + msg)


def bad(msg):
    FAILS.append("[FAIL] " + msg)


def check_a_doc():
    r = subprocess.run([sys.executable, "scripts/gen_version_matrix.py", "--check"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode == 0:
        ok("A 文档与 core/compat.py 一致")
    else:
        bad("A docs/version-matrix.md 与代码不一致，请跑 python scripts/gen_version_matrix.py")


def check_b_aliases():
    # 置换修改器、贴图槽定位等共享逻辑在 tpl_common.py 里，扫描范围必须包含它，
    # 否则检查器会把"代码在公共模板里"误报成"缺中文本地化名"（第一版就踩了这个）。
    files = {
        "vray": ["core/exporters/tpl_vray.py", "core/exporters/tpl_common.py"],
        "corona": ["core/exporters/tpl_corona.py", "core/exporters/tpl_common.py"],
    }
    blob = {}
    for grp, paths in files.items():
        text = ""
        for p in paths:
            fp = os.path.join(ROOT, p)
            if os.path.exists(fp):
                text += open(fp, encoding="utf-8", errors="replace").read()
        blob[grp] = text

    plan = [
        ("vray_mtl", "vray"), ("vray_normalmap", "vray"),
        ("corona_mtl", "corona"), ("corona_normal", "corona"), ("corona_disp", "corona"),
    ]
    for key, grp in plan:
        aliases = LANG_ALIASES[key]
        text = blob[grp]
        missing_en = [a for a in aliases["en"] if a not in text]
        missing_zh = [a for a in aliases["zh"] if a not in text]
        # 中文名允许"类表兜底"式处理：只要模板里出现过该中文名即可，不要求逐个命中，
        # 但英文名必须至少出现一个，否则模板连主流注册名都没认。
        # 特例：en 为空表示该对象在真机上根本没有英文注册名（已证伪，见
        # ondevice_registry.json 的 *_rejected），此时只要求中文名全覆盖。
        if not aliases["en"]:
            ok("B %s：无英文注册名（真机证伪），只校验中文本地化名 %s" % (key, aliases["zh"]))
            if missing_zh:
                bad("B %s：模板缺少中文本地化名 %s（中文版 Max 会静默落空）" % (key, missing_zh))
            continue
        if all(a not in text for a in aliases["en"]):
            bad("B %s：模板中未出现任何英文注册名 %s" % (key, aliases["en"]))
        elif missing_en:
            ok("B %s：英文候选部分命中（缺 %s，允许）" % (key, missing_en))
        else:
            ok("B %s：英文注册名全覆盖" % key)
        if missing_zh:
            bad("B %s：模板缺少中文本地化名 %s（中文版 Max 会静默落空）" % (key, missing_zh))
        else:
            ok("B %s：中文本地化名全覆盖" % key)


def check_c_honesty():
    baseline = {"max": LOCAL_BASELINE["max"],
                "vray": LOCAL_BASELINE["vray"],
                "corona": LOCAL_BASELINE["corona"]}
    for prod, vers in MATRIX.items():
        for v, d in vers.items():
            if d["level"] == "verified":
                if v != baseline[prod]:
                    bad("C %s %s 被标 verified，但本机基线是 %s：无真机证据的版本不得标 verified"
                        % (prod, v, baseline[prod]))
                elif not d.get("evidence"):
                    bad("C %s %s 标 verified 但缺 evidence 字段" % (prod, v))
                else:
                    ok("C %s %s verified 且有证据描述（%s）" % (prod, v, d["evidence"]))
            elif d["level"] not in ("supported", "untested"):
                bad("C %s %s 等级 %s 非法（只允许 verified/supported/untested）"
                    % (prod, v, d["level"]))
    # 每个产品至少要有一个 verified，否则等于什么都没验证
    for prod in MATRIX:
        if sum(1 for d in MATRIX[prod].values() if d["level"] == "verified") == 0:
            bad("C %s 没有任何 verified 版本：至少本机基线必须验证过" % prod)
        else:
            ok("C %s 存在 verified 基线" % prod)


def check_d_ondevice_props():
    """D. 真机属性名回归：ondevice_registry.json 里固化的真机名，必须真的写进模板候选链。

    为什么必须做成硬检查：P4 目视取证抓到过一类最阴的缺陷 —— 法线/凹凸"结构全对、
    效果为零"。VRayNormalMap 的凹凸倍率真名是 bump_map_multiplier，旧候选写的是
    bump_multiplier，for 循环一个都没命中，强度静默不下发，日志照样打 [OK]，
    渲出来的球是光滑的。只有把真机名和模板候选绑成断言，才不会二次踩。
    """
    import json
    reg_path = os.path.join(ROOT, "core", "ondevice_registry.json")
    if not os.path.exists(reg_path):
        bad("D 缺少 core/ondevice_registry.json，无法做真机属性回归")
        return
    reg = json.load(open(reg_path, encoding="utf-8"))
    tpl = {
        "vray": os.path.join(ROOT, "core/exporters/tpl_vray.py"),
        "corona": os.path.join(ROOT, "core/exporters/tpl_corona.py"),
    }
    text = {}
    for k, fp in tpl.items():
        text[k] = open(fp, encoding="utf-8", errors="replace").read() if os.path.exists(fp) else ""

    pairs = [
        ("vray", "normal_map_input_slot"),
        ("vray", "normal_map_bump_slot"),
        ("vray", "normal_map_strength_prop"),
        ("vray", "bump_amount_prop"),
        ("corona", "normal_map_input_slot"),
        ("corona", "normal_map_bump_slot"),
        ("corona", "normal_map_bump_on_prop"),
        ("corona", "normal_map_strength_prop"),
    ]
    for rk, field in pairs:
        val = (reg.get("renderers", {}).get(rk) or {}).get(field)
        if not val:
            continue
        if val in text[rk]:
            ok("D %s.%s=%s 已写进模板候选链" % (rk, field, val))
        else:
            bad("D %s.%s=%s 未出现在 tpl_%s.py，真机名未落地（会静默落空）"
                % (rk, field, val, rk))


def check_e_rejected_names():
    """E. 证伪类名零残留：真机已证伪的名字不许留在模板、兼容表与文档里。

    为什么必须硬校验：审计抓到的正是这个 —— 文档把 `CoronaNormal` /
    `CoronaDisplacementMod` 当成 Corona 法线包装器与置换修改器的候选名写给用户，
    而真机注册表早就把它们放进了 rejected 字段（英文名在本机 execute 必然落空）。
    用户照着文档去排查只会越查越糊。这里让「文档/代码出现证伪名」必然退出码非 0。
    """
    import json
    import glob
    reg_path = os.path.join(ROOT, "core", "ondevice_registry.json")
    if not os.path.exists(reg_path):
        bad("E 缺少 core/ondevice_registry.json，无法做证伪名扫描")
        return
    reg = json.load(open(reg_path, encoding="utf-8")).get("renderers", {})
    toks = []
    for rk in reg:
        for field, val in reg[rk].items():
            if field.endswith("_rejected") and isinstance(val, list):
                toks += val
    # 属性级证伪名（normal / dispersion 这类单词级）不适合做全仓字符串扫描，
    # 只保留类名级（首字母大写、长度 > 4）来扫，避免误报普通词。
    toks = sorted({t for t in toks if len(t) > 4 and t[0].isupper()})
    if not toks:
        bad("E 注册表里没有任何 rejected 类名，证伪清单疑似丢失")
        return

    targets = [
        os.path.join(ROOT, "core", "compat.py"),
        os.path.join(ROOT, "core", "exporters", "tpl_corona.py"),
        os.path.join(ROOT, "core", "exporters", "tpl_vray.py"),
        os.path.join(ROOT, "core", "exporters", "tpl_common.py"),
        os.path.join(ROOT, "core", "exporters", "ms_exporter.py"),
        os.path.join(ROOT, "SKILL.md"),
        os.path.join(ROOT, "README.md"),
    ]
    targets += sorted(glob.glob(os.path.join(ROOT, "references", "*.md")))
    targets += sorted(glob.glob(os.path.join(ROOT, "docs", "*.md")))

    # 2026-11-22 真机：AO 污垢纹理的英文类名（VRayDirt / VRay_Dirt）在本机中文版确为
    # UndefinedClass，但它们必须保留在候选链里作降级探测——英文版 3ds Max 下这两个名字
    # 是有效的，删掉等于让英文环境失去 AO。所以「证伪名零残留」不能按字符串出现即报，
    # 否则会逼着人删掉合法的兼容候选。
    # 正确口径：证伪名**单独出现**（= 当成唯一可用名写死）判 FAIL；
    #           同一行与真机实证名同现（候选链 / 对照说明）视为合法用法，只计数不报。
    verified = set()
    for rk in reg:
        for field, val in reg[rk].items():
            if field.endswith("_rejected"):
                continue
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                if isinstance(v, str) and len(v) > 3:
                    verified.add(v)

    hits, paired = [], 0
    for fp in targets:
        if not os.path.exists(fp):
            continue
        body = open(fp, encoding="utf-8", errors="replace").read()
        for i, line in enumerate(body.splitlines(), 1):
            for t in toks:
                if t in line:
                    if any(v in line for v in verified):
                        paired += 1
                        continue
                    hits.append("%s:%d 残留证伪名 %s（该行无真机实证名同现）"
                                % (os.path.relpath(fp, ROOT), i, t))
    if hits:
        for h in hits[:20]:
            bad("E " + h)
    else:
        ok("E 证伪类名零残留（扫描 %d 名 / %d 文件；候选链与对照说明放行 %d 处）"
           % (len(toks), len(targets), paired))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    check_a_doc()
    check_b_aliases()
    check_c_honesty()
    check_d_ondevice_props()
    check_e_rejected_names()
    for s in OKS:
        print(s)
    for s in FAILS:
        print(s)
    print("\n结论：%s（OK %d / FAIL %d）"
          % ("PASS" if not FAILS else "FAIL", len(OKS), len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
