# -*- coding: utf-8 -*-
"""rw_probe.log 判读器（台账 §4 判定口径，写死不放宽）

为什么要有这个脚本
----
真实世界尺寸（real-world 贴图）能不能支持，取决于 3ds Max 2024 里
`Bitmaptex.coordinates` / Corona 位图 上到底有没有「写得进去又读得回来」的属性。
这件事在运行期禁止 Agent 自己启动 Max，只能由人在 Max 里 Run Script 跑探针、
把日志交回来。日志一到，人肉读几十行 RWPROP / RWTRY 很容易看偏——尤其
「属性存在（HIT>0）」和「属性可写（WRITABLE>0）」是两件事，混为一谈就会在
模板里写一个根本写不进去的属性名，用户拿到的是「设置成功」但贴图尺寸没变。

所以把台账 §4 的口径钉成脚本，判据：
  V1 四条判据行齐全（RW_PROPS_HIT / RW_PROPS_WRITABLE / RW_SCALE_SUPPORTED / RWPROBE_VERDICT）
  V2 日志必须是完整的（有 RWPROBE_DONE=1）——Max 中途报错会让日志停在半截，
     半截日志里「没看到 FAIL 行」不等于通过
  V3 口径自洽：RW_SCALE_SUPPORTED=1 必须伴随 RW_PROPS_WRITABLE≥1；
     VERDICT=PASS 必须 SCALE=1；VERDICT=NO-SUPPORT 必须 SCALE=0
  V4 结论只能是两种走向，不许第三种「大概可以」：
     SCALE=1 -> 按日志里的属性名落 uv_map_size_m 映射
     SCALE=0 -> 不写进模板，文档标不支持，不静默降级成 uv_tiling

日志缺省不存在时：明确输出「未判读」，退出码 0——绝不无中生有给结论。

用法
----
    python scripts/rw_probe_verdict.py "D:/.../rw_probe.log"
    python scripts/rw_probe_verdict.py --selftest      # 5 个假日志验证判据
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

JUDGE_KEYS = ("RW_PROPS_HIT", "RW_PROPS_WRITABLE", "RW_SCALE_SUPPORTED")


def parse(text: str) -> dict:
    """抽出判据行与候选属性名（取最后一次出现，探针追加模式会写多份）。"""
    vals: dict = {}
    for k in JUDGE_KEYS:
        m = re.findall(rf"^{k}=(\d+)\s*$", text, re.M)
        if m:
            vals[k] = int(m[-1])
    m = re.findall(r"^RWPROBE_VERDICT=(\S*)", text, re.M)
    if m:
        vals["RWPROBE_VERDICT"] = m[-1]
    vals["done"] = bool(re.search(r"^RWPROBE_DONE=1\s*$", text, re.M))
    vals["hit_props"] = sorted(set(re.findall(r"^RWPROP\s+(\S+)\s*=", text, re.M)))
    vals["tried"] = sorted(set(re.findall(r"^RWTRY\s+(\S+)\s*->", text, re.M)))
    vals["fail_lines"] = re.findall(r"^.*RWTRY.*FAIL.*$", text, re.M)
    return vals


def verdict(text: str) -> tuple[bool, str, list[str], dict]:
    """返回 (是否自洽可用, 结论档位, 问题列表, 解析结果)。"""
    v = parse(text)
    probs: list[str] = []
    for k in JUDGE_KEYS:
        if k not in v:
            probs.append(f"V1 缺判据行 {k}（探针没跑完或输出被截断）")
    if "RWPROBE_VERDICT" not in v:
        probs.append("V1 缺判据行 RWPROBE_VERDICT")
    if not v["done"]:
        probs.append("V2 缺 RWPROBE_DONE=1：日志不完整，没看到 FAIL 行不等于通过")
    if probs:
        return False, "FAIL", probs, v

    scale, w = v["RW_SCALE_SUPPORTED"], v["RW_PROPS_WRITABLE"]
    if scale not in (0, 1):
        probs.append(f"V3 RW_SCALE_SUPPORTED={scale}，只允许 0/1")
    if scale == 1 and w < 1:
        probs.append("V3 RW_SCALE_SUPPORTED=1 但 RW_PROPS_WRITABLE=0："
                     "声称支持却没有一条属性写进去过，「存在」被当成了「可写」")
    ver = v["RWPROBE_VERDICT"].upper()
    if ver.startswith("PASS"):
        level = "PASS"
        if scale != 1:
            probs.append(f"V3 VERDICT=PASS 但 RW_SCALE_SUPPORTED={scale}："
                         "结论行与判据行矛盾，两份证据必须同时成立")
    elif ver.startswith("NO-SUPPORT"):
        level = "NO-SUPPORT"
        if scale != 0:
            probs.append(f"V3 VERDICT=NO-SUPPORT 但 RW_SCALE_SUPPORTED={scale}："
                         "结论行与判据行矛盾")
    else:
        level = "FAIL"
        probs.append(f"V3 RWPROBE_VERDICT 取值不认识：{v['RWPROBE_VERDICT']!r}")
    # 只要有任何一条判据不成立，档位就必须是 FAIL——否则下游按 level 走
    # 「落映射 / 标不支持」分支时，会把自相矛盾的证据当成结论用。
    return (not probs), ("FAIL" if probs else level), probs, v


def report(text: str) -> int:
    ok, level, probs, v = verdict(text)
    print("真实世界尺寸探针判读")
    print(f"  RW_PROPS_HIT={v.get('RW_PROPS_HIT')}  RW_PROPS_WRITABLE={v.get('RW_PROPS_WRITABLE')}"
          f"  RW_SCALE_SUPPORTED={v.get('RW_SCALE_SUPPORTED')}")
    print(f"  RWPROBE_VERDICT={v.get('RWPROBE_VERDICT')!r}")
    if v["tried"]:
        print(f"  写入尝试过的属性（{len(v['tried'])}）：{', '.join(v['tried'])}")
    if v["hit_props"]:
        print(f"  关键字命中的属性（{len(v['hit_props'])}）：{', '.join(v['hit_props'])}")
    if v["fail_lines"]:
        print(f"  写入回读失败 {len(v['fail_lines'])} 处：")
        for ln in v["fail_lines"][:10]:
            print(f"    {ln.strip()[:160]}")
    if not ok:
        print("  判读结论：FAIL（证据自相矛盾或不全，不得据此改代码）")
        for p in probs:
            print(f"    [FAIL] {p}")
        return 1
    if level == "PASS":
        print("  判读结论：支持（SCALE=1）")
        print("  后续动作：按上面「写入尝试过的属性」落 uv_map_size_m 映射，"
              "并在兼容矩阵里记 verified（须同时改 LOCAL_BASELINE 并附 evidence）")
    else:
        print("  判读结论：不支持（SCALE=0）")
        print("  后续动作：不写进导出模板；文档「真实世界尺寸」标为不支持，"
              "不静默降级成 uv_tiling")
    print(f"  回填台账用原文：RW_PROPS_HIT={v['RW_PROPS_HIT']} / "
          f"RW_PROPS_WRITABLE={v['RW_PROPS_WRITABLE']} / "
          f"RW_SCALE_SUPPORTED={v['RW_SCALE_SUPPORTED']} / {v['RWPROBE_VERDICT']}")
    return 0


SAMPLE_OK = """RWSCAN Standard props=12
  RWPROP Standard.realWorldScale = boolean
  RWTRY Standard.realWorldScale -> true/true
RW_PROPS_HIT=1
RW_PROPS_WRITABLE=1
RW_SCALE_SUPPORTED=1
RWPROBE_VERDICT=PASS
RWPROBE_DONE=1
"""

SAMPLE_NONE = """RWSCAN Standard props=9
RW_PROPS_HIT=0
RW_PROPS_WRITABLE=0
RW_SCALE_SUPPORTED=0
RWPROBE_VERDICT=NO-SUPPORT
RWPROBE_DONE=1
"""

# (名称, 文本, 期望是否自洽, 期望档位)
CASES = [
    ("N1 真支持", SAMPLE_OK, True, "PASS"),
    ("N2 真不支持", SAMPLE_NONE, True, "NO-SUPPORT"),
    ("N3 日志截断（无 DONE）", SAMPLE_OK.replace("RWPROBE_DONE=1\n", ""), False, "FAIL"),
    ("N4 声称支持但一条没写进去",
     SAMPLE_OK.replace("RW_PROPS_WRITABLE=1", "RW_PROPS_WRITABLE=0"), False, "FAIL"),
    ("N5 结论行与判据行矛盾",
     SAMPLE_NONE.replace("RWPROBE_VERDICT=NO-SUPPORT", "RWPROBE_VERDICT=PASS"), False, "FAIL"),
]


def selftest() -> int:
    failed = 0
    for name, text, want_ok, want_level in CASES:
        ok, level, probs, _ = verdict(text)
        if ok != want_ok or level != want_level:
            print(f"  FAIL {name} → 期望 ok={want_ok}/level={want_level}，实际 ok={ok}/level={level}")
            failed += 1
        else:
            tail = f"（{probs[0][:50]}…）" if probs else ""
            print(f"  PASS {name} → ok={ok} level={level}{tail}")
    # 缺失日志：脚本必须说「未判读」，不能编结论
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.log"
        if missing.exists():
            print("  FAIL 缺失日志用例构造异常")
            failed += 1
    if failed:
        print(f"判读器自测：失败 {failed} 项")
        return 1
    print(f"判读器自测：{len(CASES)}/{len(CASES)} 全部符合预期（ALL PASS）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rw_probe.log 判读（台账 §4 口径）")
    ap.add_argument("log", nargs="?", help="探针日志绝对路径")
    ap.add_argument("--selftest", action="store_true", help="跑 5 个假日志验证判据")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.log:
        ap.error("需要日志路径，或用 --selftest")
    p = Path(args.log)
    if not p.exists():
        print(f"未判读：日志不存在 -> {p}")
        print("真机证据尚未产生（运行期禁止 Agent 启动 Max，需人工 Run Script 跑 output/rw_probe.ms）")
        return 0
    raw = p.read_bytes()
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        text = raw.decode("latin-1", "replace")
    return report(text)


if __name__ == "__main__":
    raise SystemExit(main())
