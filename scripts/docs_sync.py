# -*- coding: utf-8 -*-
"""文档事实同步器：SKILL.md / README.md 里的数字与类名不再手抄

为什么要有它
------------
审计里最刺眼的一类缺陷不是代码写错，而是「文档手抄的数字」与代码常量各走各的：
SKILL.md 写 61 项 / 6 分组 / 表面 26 / 检查 6 项 / evals 17 条 / 版本 0.9.0，
而 `core/params.py` 实际 PARAMS=62、表面 27、run_all_checks 12 项、evals 20 条、
版本 0.11.1。人按文档去排障就会扑空，Agent 按文档去改代码就会改错地方。
第二类是同源的「类名漂移」：文档里写 `CoronaNormal` / `CoronaDisplacementMod`，
而真机注册表 `core/ondevice_registry.json` 已经把这两个名字判定为不存在
（`normal_map_class_rejected` / `displacement_modifier_rejected`）。

做法
----
1. 数字：全部由代码现场计算（params / GROUP_ORDER / BEGINNER_IDS /
   INTERMEDIATE_IDS / evals.json / version.py / run_all_checks.CHECKS /
   各检查脚本的断言数），再按「定位 + 重写」方式写回文档；
2. 表格：分组表、类名对照表放在 `<!-- AUTO:xxx begin/end -->` 标记块里，
   整块由本脚本生成，不手改；
3. 幂等：`--check` 模式重新生成一遍与磁盘文档逐字比对，不一致即 FAIL 并打印
   第一处差异行号；`--write` 模式才落盘。写后立刻自检一次，保证可重复。
4. 反例：对全仓 Markdown 扫描注册表里「已证伪」的类名/属性名，命中即 FAIL
   （生成块内部也只报数量不列字面，避免把错名重新散布进文档）。

用法
----
    python scripts/docs_sync.py            # 只检查，不一致 exit 1（门禁用这个）
    python scripts/docs_sync.py --write    # 按代码现状重写文档
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import params as P          # noqa: E402
from core import version as V         # noqa: E402

SKILL = ROOT / "SKILL.md"
README = ROOT / "README.md"
REGISTRY = ROOT / "core" / "ondevice_registry.json"
EVALS = ROOT / "evals" / "evals.json"

BEGIN = "<!-- AUTO:%s begin -->"
END = "<!-- AUTO:%s end -->"
GENERATED_NOTE = "> 本节由 `scripts/docs_sync.py` 从代码现场生成，勿手改；改代码后跑 `python scripts/docs_sync.py --write`。"


# ---------------------------------------------------------------- 事实采集
def _count_asserts(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return len(re.findall(pattern, path.read_text(encoding="utf-8"), re.M))


def collect_facts() -> dict:
    params = list(P.PARAMS)
    group_counts = {}
    for p in params:
        group_counts[p.group] = group_counts.get(p.group, 0) + 1
    order = [g for g in P.GROUP_ORDER if g in group_counts]
    labels = {}
    for p in params:
        labels.setdefault(p.group, []).append(p.label)

    evals = json.loads(EVALS.read_text(encoding="utf-8"))
    evals_total = len(evals.get("evals", [])) if isinstance(evals, dict) else len(evals)

    checks_total = 0
    raws = (ROOT / "scripts" / "run_all_checks.py").read_text(encoding="utf-8")
    m = re.search(r"^CHECKS\s*=\s*\[(.*?)^\]", raws, re.S | re.M)
    if m:
        checks_total = len(re.findall(r"^\s*\(", m.group(1), re.M))

    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))["renderers"]

    return {
        "params_total": len(params),
        "groups_total": len(order),
        "group_order": order,
        "group_counts": group_counts,
        "group_labels": labels,
        "beginner": len(P.BEGINNER_IDS),
        "inter_ids": len(P.INTERMEDIATE_IDS),
        "inter_total": len(P.BEGINNER_IDS) + len(P.INTERMEDIATE_IDS),
        "presets": len(P.PRESETS),
        "evals_total": evals_total,
        "checks_total": checks_total,
        "version": getattr(V, "VERSION", ""),
        "guard_asserts": _count_asserts(ROOT / "scripts" / "export_guard_check.py", r"^\s*check\("),
        "ui_asserts": _count_asserts(ROOT / "scripts" / "ui_check.py", r"^\s*check\("),
        "reg": reg,
    }


# ---------------------------------------------------------------- 生成块
def build_params_block(f: dict) -> str:
    rows = ["| 分组 | 数量 | 组内参数（前 4 个） |", "|------|------|------------------|"]
    for g in f["group_order"]:
        sample = "、".join(f["group_labels"].get(g, [])[:4])
        if len(f["group_labels"].get(g, [])) > 4:
            sample += " 等"
        rows.append(f"| {g} | {f['group_counts'][g]} | {sample} |")
    rows.append(f"| **合计** | **{f['params_total']}** | {f['groups_total']} 个分组 |")
    return "\n".join([GENERATED_NOTE, ""] + rows)


def build_classname_block(f: dict) -> str:
    reg = f["reg"]
    v, c = reg["vray"], reg["corona"]
    rows = [
        "| 项 | V-Ray | Corona | 来源 |",
        "|----|-------|--------|------|",
        f"| 材质类 | `{v['material_class']}` | `{c['material_class']}` | 真机 classof 回读 |",
        f"| 法线包装器类 | `{v['normal_map_class']}` | `{c['normal_map_class']}` | 真机类表枚举 |",
        f"| 法线内层槽 | `{v['normal_map_input_slot']}` | `{c['normal_map_input_slot']}` | 真机属性回读 |",
        f"| 凹凸槽 | `{v['bump_texmap_slot']}` | `{c['bump_texmap_slot']}` | 真机属性回读 |",
        f"| 置换修改器类 | `{v['displacement_modifier']}` | `{c['displacement_modifier']}` | 真机 Modifier 类表 |",
    ]
    banned = []
    for key in ("normal_map_class_rejected", "displacement_modifier_rejected"):
        banned += c.get(key, [])
    banned += v.get("normal_map_class_rejected", [])
    banned += v.get("normal_map_input_slot_rejected", []) + v.get("dispersion_prop_rejected", [])
    seen, uniq = set(), []
    for b in banned:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    # 证伪名只报数量、不列字面：字面一旦写进文档，就等于把错名重新散布给用户，
    # 且 compat_check 的 E 项（证伪名零残留）会直接 FAIL。唯一可查清单是注册表。
    tail = (
        f"另有 {len(uniq)} 个类名/属性名写法已被真机证伪（英文注册名在本机不存在），"
        "文档与脚本都不得再把它们当候选名使用；"
        "逐条清单见 `core/ondevice_registry.json` 的 `*_rejected` 字段。"
    )
    return "\n".join([GENERATED_NOTE, ""] + rows + ["", tail])


def build_blocks(f: dict) -> dict:
    return {
        "params": build_params_block(f),
        "classnames": build_classname_block(f),
    }


# ---------------------------------------------------------------- 行内数字重写
def inline_subs(f: dict) -> list[tuple[re.Pattern, str, str]]:
    subs: list[tuple[re.Pattern, str, str]] = []

    def add(pattern: str, repl: str, label: str) -> None:
        subs.append((re.compile(pattern), repl, label))

    add(r"唯一事实来源（\d+ 项 / \d+ 分组）",
        f"唯一事实来源（{f['params_total']} 项 / {f['groups_total']} 分组）", "params 计数")
    add(r"版本号 SSOT（当前 \d+(?:\.\d+){1,3}）",
        f"版本号 SSOT（当前 {f['version']}）", "版本号")
    # SKILL.md「版本与打包」段的历史写法带反引号，与上面那条模式对不上，
    # 等于版本号在这一段里从来没被同步过（bump 之后会留下旧版本号）。
    add(r"（当前 `\d+(?:\.\d+){1,3}`）",
        f"（当前 `{f['version']}`）", "版本号（受管写法）")
    add(r"## 参数体系：\d+ 项怎么组织的",
        f"## 参数体系：{f['params_total']} 项怎么组织的", "参数体系标题")
    for g in f["group_order"]:
        add(r"\| " + re.escape(g) + r" \| \d+ \|",
            f"| {g} | {f['group_counts'][g]} |", f"{g} 组计数")
    add(r"新手 \d+ 项（`BEGINNER_IDS`）",
        f"新手 {f['beginner']} 项（`BEGINNER_IDS`）", "新手档")
    add(r"中级 \d+ 项（新手 \+ `INTERMEDIATE_IDS` \d+ 项）",
        f"中级 {f['inter_total']} 项（新手 + `INTERMEDIATE_IDS` {f['inter_ids']} 项）", "中级档")
    add(r"进阶 \d+ 项全平铺", f"进阶 {f['params_total']} 项全平铺", "进阶档")
    add(r"同一套 \d+ 参数", f"同一套 {f['params_total']} 参数", "双渲染器参数数")
    add(r"统一检查入口（\d+ 项串跑", f"统一检查入口（{f['checks_total']} 项串跑", "检查项数")
    add(r"evals\.json 回归用例（\d+ 条）", f"evals.json 回归用例（{f['evals_total']} 条）", "evals 条数")
    add(r"导出行为断言 \d+ 项", f"导出行为断言 {f['guard_asserts']} 项", "export_guard 断言数")
    add(r"文档 ↔ 代码的 \d+ 项与档位一致性",
        f"文档 ↔ 代码的 {f['params_total']} 项与档位一致性", "registry_check 口径")
    add(r"独立端口 \+ Edge CDP，\d+ 项断言",
        f"独立端口 + Edge CDP，{f['ui_asserts']} 项断言", "ui_check 断言数")
    return subs


# ---------------------------------------------------------------- 渲染
def render(text: str, f: dict) -> tuple[str, list[str]]:
    """按代码现状重写文档文本，返回 (新文本, 命中的定位标签)"""
    hits: list[str] = []
    blocks = build_blocks(f)

    for name, body in blocks.items():
        b, e = BEGIN % name, END % name
        if b in text and e in text:
            pre, rest = text.split(b, 1)
            _, post = rest.split(e, 1)
            text = pre + b + "\n" + body + "\n" + e + post
            hits.append(f"AUTO:{name}")

    for pat, repl, label in inline_subs(f):
        text, n = pat.subn(repl, text)
        if n:
            hits.append(f"{label}×{n}")
    return text, hits


def _strip_generated(text: str) -> str:
    for name in ("params", "classnames"):
        b, e = BEGIN % name, END % name
        if b in text and e in text:
            pre, rest = text.split(b, 1)
            _, post = rest.split(e, 1)
            text = pre + "\n" + post
    return text


def banned_tokens(f: dict) -> list[str]:
    reg = f["reg"]
    v, c = reg["vray"], reg["corona"]
    toks: list[str] = []
    for key in ("normal_map_class_rejected", "displacement_modifier_rejected"):
        toks += c.get(key, [])
    toks += v.get("normal_map_input_slot_rejected", []) + v.get("dispersion_prop_rejected", [])
    toks = [t for t in toks if t and t not in ("normal", "dispersion")]
    return sorted(set(toks))


def md_targets() -> list[Path]:
    files = [SKILL, README]
    for sub in ("references", "docs"):
        d = ROOT / sub
        if d.is_dir():
            files += sorted(d.glob("*.md"))
    return [p for p in files if p.exists()]


def check_banned(f: dict, targets: list[Path] | None = None) -> list[str]:
    bad = []
    toks = banned_tokens(f)
    for p in (targets if targets is not None else md_targets()):
        body = _strip_generated(p.read_text(encoding="utf-8"))
        for i, line in enumerate(body.splitlines(), 1):
            for t in toks:
                if t in line:
                    bad.append(f"{p.name}:{i} 出现被真机证伪的名字 `{t}` → {line.strip()[:100]}")
    return bad


def _first_diff(a: str, b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<缺行>"
        y = lb[i] if i < len(lb) else "<缺行>"
        if x != y:
            return (f"第 {i + 1} 行不一致：\n  文档现状: {x.strip()[:160]}\n"
                    f"  代码要求: {y.strip()[:160]}")
    return "内容一致但换行结构不同"


# ---------------------------------------------------------------- 反例自测
def self_test() -> int:
    """反例自测：证明「文档漂移」与「证伪名残留」真的会被判 FAIL，而不是永远绿灯。

    门禁最怕的不是漏判，而是「看起来在检查、其实什么都不检查」。所以这里对每种
    漂移都构造一份被污染的文档，断言 render() 必须把它改回来、check_banned() 必须
    命中；再对真实文档断言「没有漂移」。任一断言不成立即退出码 1。
    """
    import tempfile

    fails: list[str] = []
    oks: list[str] = []

    def ok(msg):
        oks.append("[OK] " + msg)

    def bad(msg):
        fails.append("[FAIL] " + msg)

    f = collect_facts()
    skill_raw = SKILL.read_text(encoding="utf-8")

    # 0) 真实文档必须已同步（自测自带一次全量校验）
    for p in md_targets():
        raw = p.read_text(encoding="utf-8")
        want, _ = render(raw, f)
        if want != raw:
            bad(f"真实文档未同步：{p.name} —— " + _first_diff(raw, want))
        else:
            ok(f"真实文档已同步：{p.name}")

    # 1) 反例 A：行内数字被改错（参数总数）
    needle = f"{f['params_total']} 项 / {f['groups_total']} 分组"
    if needle not in skill_raw:
        bad(f"反例 A 无法构造：SKILL.md 里找不到「{needle}」，说明行内数字段本身丢了")
    else:
        polluted = skill_raw.replace(needle, "999 项 / 1 分组", 1)
        want, _ = render(polluted, f)
        if want == polluted:
            bad("反例 A 未被拦截：把参数总数改成 999 项 / 1 分组后 render() 竟认为无需修改")
        elif needle not in want:
            bad("反例 A 修复不完整：render() 没有把参数总数改回代码事实")
        else:
            ok("反例 A 被拦截：行内参数计数漂移会被改回代码事实")

    # 2) 反例 B：AUTO 生成块内容被手改
    begin, end = "<!-- AUTO:params begin -->", "<!-- AUTO:params end -->"
    if begin not in skill_raw or end not in skill_raw:
        bad("反例 B 无法构造：SKILL.md 缺 AUTO:params 标记块")
    else:
        i, j = skill_raw.index(begin) + len(begin), skill_raw.index(end)
        polluted = skill_raw[:i] + "\n| 手改行 | 999 |\n" + skill_raw[j:]
        want, _ = render(polluted, f)
        if "手改行" in want:
            bad("反例 B 未被拦截：AUTO:params 块里的手改内容没有被生成结果覆盖")
        else:
            ok("反例 B 被拦截：AUTO:params 块手改会被整块重写")

    # 3) 反例 C：证伪类名被写回文档
    toks = banned_tokens(f)
    if not toks:
        bad("反例 C 无法构造：注册表里没有 rejected 名字，证伪清单疑似丢失")
    else:
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "bad.md"
            fake.write_text(f"| 法线 | `{toks[0]}` 包装位图 |\n", encoding="utf-8")
            hit = check_banned(f, [fake])
            if not hit:
                bad(f"反例 C 未被拦截：文档里写入已被证伪的名字 {toks[0]} 竟然没有报错")
            else:
                ok(f"反例 C 被拦截：证伪名 {toks[0]} 出现在 Markdown 里会 FAIL")
        # 真实全仓扫描必须干净
        real = check_banned(f)
        if real:
            bad(f"真实文档里仍有证伪名残留 {len(real)} 处")
        else:
            ok("真实文档证伪名零残留")

    for line in oks:
        print(line)
    for line in fails:
        print(line)
    print("\n结论：%s（OK %d / FAIL %d）" % ("PASS" if not fails else "FAIL", len(oks), len(fails)))
    return 0 if not fails else 1


# ---------------------------------------------------------------- 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SKILL.md / README.md 事实同步器")
    ap.add_argument("--write", action="store_true", help="按代码现状重写文档（默认只检查）")
    ap.add_argument("--self-test", action="store_true",
                    help="反例自测：证明文档漂移与证伪名残留一定会被判 FAIL")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    f = collect_facts()
    rc = 0

    for p in md_targets():
        raw = p.read_text(encoding="utf-8")
        want, hits = render(raw, f)
        if args.write:
            if want != raw:
                p.write_text(want, encoding="utf-8")
                print(f"[WRITE] {p.name} 已同步：{', '.join(hits)}")
            else:
                print(f"[OK] {p.name} 无需改动")
        else:
            if want != raw:
                rc = 1
                print(f"[FAIL] {p.name} 与代码事实不一致 —— {_first_diff(raw, want)}")
            else:
                print(f"[OK] {p.name} 数字/类名与代码一致（{'、'.join(hits) if hits else '无自动项'}）")

    bad = check_banned(f)
    if bad:
        rc = 1
        print(f"[FAIL] 证伪类名残留 {len(bad)} 处：")
        for line in bad:
            print("   " + line)
    else:
        print(f"[OK] 全仓 Markdown 未出现注册表已证伪的类名（共查 {len(banned_tokens(f))} 个）")

    if args.write:
        # 写后立即按 check 口径复跑一遍，保证幂等
        f2 = collect_facts()
        for p in md_targets():
            raw = p.read_text(encoding="utf-8")
            want, _ = render(raw, f2)
            if want != raw:
                print(f"[FAIL] 写入不幂等：{p.name} —— {_first_diff(raw, want)}")
                return 1
        print("[OK] 写后复核：全部文档幂等")
        return 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
