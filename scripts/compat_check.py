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


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    check_a_doc()
    check_b_aliases()
    check_c_honesty()
    check_d_ondevice_props()
    for s in OKS:
        print(s)
    for s in FAILS:
        print(s)
    print("\n结论：%s（OK %d / FAIL %d）"
          % ("PASS" if not FAILS else "FAIL", len(OKS), len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
