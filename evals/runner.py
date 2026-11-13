# -*- coding: utf-8 -*-
"""evals 机器评分器

背景：evals.json 里每条只写了自然语言 expected_output，靠人眼看，
14 条跑一轮全靠肉眼比对，改参数时根本没法回归。这里给每条挂上
`machine` 断言，做到「有回答文本就自动打分，有产物就自动体检」。

断言结构（evals.json 每条的 machine 字段）：
    must:      [ "子串" | {"any": ["a","b"], "hint": "这组为什么必须命中"} ]
    must_not:  [ {"any": [...], "hint": "命中即视为说错"} ]
    threshold: 0.8            # 默认 0.8
    artifact:  {              # 可选：直接生成 .ms 做产物级断言
        "preset": "wallpaper",
        "contains": ["#(\"uv_tiling\")"],
        "not_contains": ["VRayDirt"],
        "verify": true        # 是否跑 verify_ms 体检（默认 true）
    }

用法：
    python evals/runner.py                        # 断言自检 + 清单
    python evals/runner.py --answers a.json       # 对答案打分
    python evals/runner.py --answers a.json --artifact   # 同时跑产物断言
    python evals/runner.py --answers a.json --json

answers 格式：[{"id": 1, "answer": "..."}, ...]
退出码：全部 pass 为 0，否则 1（无 answers 时只看自检结果）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

EVALS_PATH = os.path.join(ROOT, "evals", "evals.json")


# ----------------------------------------------------------------------
# 断言归一化
# ----------------------------------------------------------------------
def _norm_checks(raw) -> list[dict]:
    """把 must/must_not 里的字符串与 dict 统一成 {any: [...], hint: ''}"""
    out: list[dict] = []
    for item in raw or []:
        if isinstance(item, str):
            out.append({"any": [item], "hint": ""})
        elif isinstance(item, dict):
            anyv = item.get("any")
            if isinstance(anyv, str):
                anyv = [anyv]
            out.append({"any": [str(x) for x in (anyv or [])], "hint": item.get("hint", "")})
    return out


def _hit(group: dict, text_lower: str) -> bool:
    return any(a.lower() in text_lower for a in group["any"] if a)


NEG_WORDS = ("不", "勿", "禁", "别", "无需", "避免", "而非", "不能", "不要", "不得", "绝不", "切勿")


def _hit_negative(group: dict, text_lower: str) -> bool:
    """must_not 命中判定：跳过否定语境

    为什么不直接子串匹配：
        expected_output 里天然写着「不得承诺自动生成 .mat 文件」「不要建议把 proc_on 关掉」
        「不要把卡归因到显卡」这类**否定式表述**，裸子串匹配会把老师的正确答案判成错误。
        这里在命中位置往前 12 个字符里找否定词，命中即视为「在讲不要这么做」，放过。
    """
    for a in group["any"]:
        if not a:
            continue
        start = 0
        low = a.lower()
        while True:
            idx = text_lower.find(low, start)
            if idx < 0:
                break
            ctx = text_lower[max(0, idx - 12):idx]
            if not any(w in ctx for w in NEG_WORDS):
                return True
            start = idx + len(low)
    return False


def score_answer(ev: dict, answer: str) -> dict:
    """按 machine 断言给一条回答打分（0~1）"""
    m = ev.get("machine") or {}
    must = _norm_checks(m.get("must"))
    must_not = _norm_checks(m.get("must_not"))
    thr = float(m.get("threshold", 0.8))

    low = (answer or "").lower()
    hit, miss = [], []
    for g in must:
        (hit if _hit(g, low) else miss).append(g)

    violations = [g for g in must_not if _hit_negative(g, low)]
    score = (len(hit) / len(must)) if must else 1.0
    if violations:
        # 说错一条比少说一条严重：压到阈值以下，避免"命中大半但结论错"被判过
        score = min(score, 0.4)

    return {
        "id": ev.get("id"),
        "score": round(score, 3),
        "pass": score >= thr and not violations,
        "threshold": thr,
        "hit": [g["any"] for g in hit],
        "miss": [{"any": g["any"], "hint": g["hint"]} for g in miss],
        "violations": [{"any": g["any"], "hint": g["hint"]} for g in violations],
    }


# ----------------------------------------------------------------------
# 0.8.2 新功能回归探针：打包通道 / 三层参数 / 导出自检 / 材质隔离
#
# 为什么单独做探针而不是写进 contains 断言：
#   contains 只能验证「脚本里有没有某段字」，验证不了「导出的一个材质一目录
#   不互相覆盖」「贴图丢了会不会被检出」这类行为。这里直接调被侧面（纯函数
#   或临时目录），不依赖 Max，跑得动也跑得快。
# ----------------------------------------------------------------------
def _probe_pack(base_values: dict, cases: list) -> list:
    """贴图打包通道回归：开关组合 → 应打包哪些图"""
    from core import export_guard

    probs: list[str] = []
    for case in cases or []:
        v = dict(base_values)
        v.update(case.get("overrides") or {})
        got = export_guard.expected_channels(v, has_source=True)
        name = case.get("name") or "case"
        for c in case.get("expect") or []:
            if c not in got:
                probs.append(f"打包[{name}] 缺通道 {c}（实际 {got}）")
        for c in case.get("expect_absent") or []:
            if c in got:
                probs.append(f"打包[{name}] 不应含通道 {c}（实际 {got}）")
    return probs


def _probe_tiers(spec: dict) -> list:
    """三层参数回归：分层是否存在、引用是否有效、是否覆盖全部参数"""
    from core import params

    probs: list[str] = []
    tiers = params.tier_ids()
    if not tiers:
        return ["tier_ids() 为空，三层参数没生效"]
    by_id = params.by_id()
    for name, min_n in (spec.get("min_count") or {}).items():
        ids = tiers.get(name)
        if ids is None:
            probs.append(f"分层 {name} 不存在（现有 {sorted(tiers)}）")
            continue
        if len(ids) < int(min_n):
            probs.append(f"分层 {name} 仅 {len(ids)} 个参数，期望 ≥{min_n}")
        ghost = [i for i in ids if i not in by_id]
        if ghost:
            probs.append(f"分层 {name} 引用了不存在的参数：{ghost[:5]}")
    if spec.get("must_cover_all", True):
        declared = {i for ids in tiers.values() for i in ids}
        missing = [p for p in by_id if p not in declared]
        if missing:
            probs.append(f"{len(missing)} 个参数未归入任何分层：{missing[:5]}")
    return probs


def _probe_selfcheck(base_values: dict) -> list:
    """导出自检回归：完整场景须通过，缺图/断链必须被检出（不许静默成功）"""
    import shutil as _shutil
    from pathlib import Path

    from core import export_guard

    probs: list[str] = []
    root = Path(tempfile.mkdtemp(prefix="mf_selfcheck_"))
    try:
        tdir = root / "textures"
        tdir.mkdir(parents=True, exist_ok=True)
        img = tdir / "albedo.png"
        try:
            from PIL import Image
            Image.new("RGB", (4, 4), (200, 200, 200)).save(img)
        except Exception as e:
            return [f"无法生成测试贴图：{type(e).__name__}: {e}"]

        tex = {"albedo": str(img)}
        script = f'bitmapTexture filename:"{img.as_posix()}"'

        sc = export_guard.check_export(script, tex, dict(base_values),
                                       out_dir=str(root), has_source=True)
        if not sc.get("ok"):
            probs.append("完整场景被自检误判失败：" + export_guard.summarize(sc))
        if sc.get("checked") != 1:
            probs.append(f"checked 计数异常：{sc.get('checked')}（应 1）")

        img.unlink()
        sc2 = export_guard.check_export(script, tex, dict(base_values),
                                       out_dir=str(root), has_source=True)
        if sc2.get("ok"):
            probs.append("贴图缺失仍报 ok —— 自检形同虚设（缺陷 2 复发）")
        if not sc2.get("missing"):
            probs.append("缺图未进入 missing 列表")

        outside = root / "nope" / "albedo.png"
        sc3 = export_guard.check_export(
            f'bitmapTexture filename:"{outside.as_posix()}"', {}, dict(base_values),
            out_dir=str(root), has_source=True)
        if not sc3.get("broken_refs"):
            probs.append("脚本引用断链未被检出")
    finally:
        _shutil.rmtree(root, ignore_errors=True)
    return probs


def _probe_isolation() -> list:
    """材质隔离回归：两个材质名必须落到不同目录，同名贴图不得互相覆盖"""
    import shutil as _shutil
    from pathlib import Path

    from core import export_guard

    probs: list[str] = []
    names = ["MF_Eval_A", "MF_Eval B/带斜杠"]
    dirs = [export_guard.safe_dirname(n) for n in names]
    if len(set(dirs)) != 2:
        probs.append(f"不同材质名映射到同一目录：{dirs}")
    bad_chars = set('\\/:*?"<>|')
    if any(set(d) & bad_chars for d in dirs):
        probs.append(f"safe_dirname 未清理非法字符：{dirs}")
    if not all(dirs):
        probs.append("safe_dirname 返回空目录名")

    root = Path(tempfile.mkdtemp(prefix="mf_iso_"))
    try:
        from PIL import Image
        blobs = []
        for d, color in zip(dirs, [(255, 0, 0), (0, 0, 255)]):
            tdir = root / d / "textures"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "albedo.png").write_bytes(b"")
            Image.new("RGB", (4, 4), color).save(tdir / "albedo.png")
            blobs.append((tdir / "albedo.png").read_bytes())
        if blobs[0] == blobs[1]:
            probs.append("两个材质的 albedo.png 内容相同 —— 同名覆盖仍会发生（缺陷 1 复发）")
        for d in dirs:
            if not (root / d / "textures" / "albedo.png").is_file():
                probs.append(f"材质目录 {d} 下贴图缺失")
    except Exception as e:
        probs.append(f"隔离探针执行异常：{type(e).__name__}: {e}")
    finally:
        _shutil.rmtree(root, ignore_errors=True)
    return probs


# ----------------------------------------------------------------------
# 产物断言：不看回答，直接生成 .ms 体检
# ----------------------------------------------------------------------
def check_artifact(spec: dict) -> dict:
    import contextlib
    import io

    from core import params                      # noqa: E402
    from core.exporters import ms_exporter       # noqa: E402

    preset = spec.get("preset")
    snaps = params.preset_snapshots()
    if preset and preset not in snaps:
        return {"ok": False, "detail": f"预设不存在：{preset}"}

    values = dict(params.default_values())
    if preset:
        values.update(snaps[preset])
    values.update(spec.get("overrides") or {})
    script = ms_exporter.build_mscript("MF_eval", params.resolve_vray(values), {}, "")

    problems: list[str] = []
    for s in spec.get("contains") or []:
        if s not in script:
            problems.append(f"缺少必需片段：{s}")
    for s in spec.get("not_contains") or []:
        if s in script:
            problems.append(f"出现禁止片段：{s}")

    # 关键常量断言：直接读脚本头部的 local MF_XXX = 值，
    # 比"包含某片段"更能锁住数值回归（例如壁纸 uv_tiling 必须是 1.0）
    locs = {n: float(v) for n, v in re.findall(r"local\s+(MF_\w+)\s*=\s*(-?[\d.]+)", script)}
    for name, want in (spec.get("expect_locals") or {}).items():
        got = locs.get(name)
        if got is None:
            problems.append(f"未找到常量 {name}")
        elif abs(float(got) - float(want)) > 1e-6:
            problems.append(f"常量 {name}={got}，期望 {want}")

    verify_note = ""
    if spec.get("verify", True):
        try:
            import verify_ms  # noqa: E402   来自 scripts/
            fd, tmp = tempfile.mkstemp(suffix=".ms")
            os.close(fd)
            with open(tmp, "w", encoding="gbk", errors="replace") as f:
                f.write(script)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):     # verify() 直接 print，必须接住
                ok_v = verify_ms.verify(tmp)
            os.unlink(tmp)
            if not ok_v:
                errs = [ln.strip() for ln in buf.getvalue().splitlines() if "[ERR]" in ln]
                problems.append("体检未过：" + "；".join(errs[:3] or ["见 verify_ms 输出"]))
            warns = [ln.strip() for ln in buf.getvalue().splitlines() if "[WARN]" in ln]
            verify_note = f"体检通过（{len(warns)} 条 WARN）" if ok_v else "体检未过"
        except Exception as e:  # 体检器自身异常也要暴露，不能静默放过
            problems.append(f"体检执行异常：{type(e).__name__}: {e}")

    # ---- 0.8.2 新功能回归（此前 runner 里 pack_textures / tiers 命中为 0） ----
    if spec.get("tiers"):
        problems.extend(_probe_tiers(spec["tiers"]))
    if spec.get("pack"):
        problems.extend(_probe_pack(values, spec["pack"]))
    if spec.get("selfcheck"):
        problems.extend(_probe_selfcheck(values))
    if spec.get("isolation"):
        problems.extend(_probe_isolation())

    detail = "通过"
    if problems:
        detail = "；".join(problems)
    elif verify_note:
        detail = verify_note
    return {"ok": not problems, "detail": detail,
            "preset": preset, "lines": len(script.splitlines())}


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def load_evals() -> list[dict]:
    with open(EVALS_PATH, encoding="utf-8") as f:
        return json.load(f)["evals"]


def self_check(evals: list[dict]) -> list[str]:
    """断言本身的体检：缺 machine、any 为空、must 为空都要报出来"""
    problems: list[str] = []
    for ev in evals:
        m = ev.get("machine")
        if not m:
            problems.append(f"eval {ev.get('id')} 未配置 machine 断言")
            continue
        for key in ("must", "must_not"):
            for g in _norm_checks(m.get(key)):
                if not g["any"]:
                    problems.append(f"eval {ev.get('id')} 的 {key} 存在空 any")
        if not _norm_checks(m.get("must")):
            problems.append(f"eval {ev.get('id')} 的 must 为空（无法评分）")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="evals 机器评分器")
    ap.add_argument("--answers", help="回答 JSON 文件：[{id, answer}]")
    ap.add_argument("--artifact", action="store_true", help="同时跑产物级断言（会生成 .ms）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    evals = load_evals()
    problems = self_check(evals)

    answers = {}
    if args.answers:
        with open(args.answers, encoding="utf-8") as f:
            for row in json.load(f):
                answers[int(row["id"])] = row.get("answer", "")

    rows, art_rows = [], []
    for ev in evals:
        if answers:
            rows.append(score_answer(ev, answers.get(int(ev["id"]), "")))
        if args.artifact and (ev.get("machine") or {}).get("artifact"):
            art_rows.append({"id": ev["id"], **check_artifact(ev["machine"]["artifact"])})

    ok = not problems
    if rows:
        ok = ok and all(r["pass"] for r in rows)
    if art_rows:
        ok = ok and all(r["ok"] for r in art_rows)

    if args.json:
        print(json.dumps({"ok": ok, "count": len(evals), "self_check": problems,
                          "answers": rows, "artifacts": art_rows},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"evals 共 {len(evals)} 条；machine 断言自检：{'通过' if not problems else '有问题'}")
    for p in problems:
        print("  [断言问题]", p)
    if not answers:
        for ev in evals:
            m = ev.get("machine") or {}
            n_must = len(_norm_checks(m.get("must")))
            n_not = len(_norm_checks(m.get("must_not")))
            flag = " +产物" if m.get("artifact") else ""
            print(f"  #{ev['id']:<2} must {n_must} 组 / must_not {n_not} 组{flag}")
    if rows:
        print("\n--- 回答评分 ---")
        for r in rows:
            print(f"  #{r['id']:<2} {r['score']:.2f} {'PASS' if r['pass'] else 'FAIL'}")
            for g in r["miss"]:
                print(f"        漏: {g['any'][:3]} {g['hint']}")
            for g in r["violations"]:
                print(f"        错: {g['any'][:3]} {g['hint']}")
        avg = sum(r["score"] for r in rows) / len(rows)
        n_pass = sum(1 for r in rows if r["pass"])
        print(f"  平均分 {avg:.3f} · 通过 {n_pass}/{len(rows)}")
    if art_rows:
        print("\n--- 产物断言 ---")
        for a in art_rows:
            print(f"  #{a['id']:<2} {'PASS' if a['ok'] else 'FAIL'} {a['detail']}"
                  f"（{a.get('lines', 0)} 行）")

    print("\n结论：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
