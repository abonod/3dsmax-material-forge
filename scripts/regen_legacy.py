# -*- coding: utf-8 -*-
"""历史导出产物批量重导出 / 修复（P2 可验证性 - 第 4 项）

背景
----
早期导出器写成 ``mfAssign mtl #"属性名" 值``，而 3ds Max 的 name 字面量不支持
``#"..."`` 这种引号写法，导入时直接报「位于 string，需要 ( 或 [」。
导出器虽然已经修掉，但历史 .ms 仍然是坏的，用户翻出旧文件跑一次就会再踩一次。

做法
----
1. 优先「重导出」：导出目录里若有 material_params.json 快照，就按当前导出器重新生成，
   既保证参数保真，又顺带获得环境自检段、textures/ 相对路径、自动赋材质。
2. 快照缺失时退化为「安全文本修复」：``#"x"`` -> ``#x``（合法标识符）或 ``(toName "x")``。
3. 旧文件一律先备份成 ``<name>.legacy_bak``，不做任何物理删除。
4. 生成前后都跑一遍静态预检，报告写入 JSON，便于复查。

用法
----
    python scripts/regen_legacy.py                 # 只扫描，打印计划（dry-run）
    python scripts/regen_legacy.py --apply         # 真正执行（会先备份）
    python scripts/regen_legacy.py --apply --root "D:\\其他\\output"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from core import params, preflight  # noqa: E402
from core.exporters import ms_exporter  # noqa: E402

HASHQUOTE = re.compile(r'#"[^"\n]*"')
IDENT = re.compile(r"^[A-Za-z_]\w*$")


def read_script(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("gbk", "utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def count_bad(text: str) -> int:
    return len(HASHQUOTE.findall(text))


def fix_hashquote(text: str) -> tuple[str, int]:
    """把 #"名称" 改成 Max 认识的写法，返回 (新文本, 修复处数)"""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        n += 1
        name = m.group(0)[2:-1]
        return "#" + name if IDENT.match(name) else '(toName "%s")' % name

    return HASHQUOTE.sub(repl, text), n


def scan(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() == ".ms":
                out.append(p)
    return sorted(out)


def regen_from_snapshot(snap_path: Path, ms_path: Path, apply: bool) -> dict:
    """按快照重导出；返回执行结果（不写盘时只返回计划）"""
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"action": "skip", "ok": False, "detail": f"快照读取失败: {e}"}

    values = snap.get("values") or {}
    name = snap.get("material_name") or ms_path.stem
    export_dir = ms_path.parent
    tex_dir = export_dir / "textures"
    tex_paths = {}
    if tex_dir.is_dir():
        for chan, fn in (snap.get("textures") or {}).items():
            cand = tex_dir / fn
            if cand.is_file():
                tex_paths[chan] = str(cand)

    if not tex_paths:
        return {"action": "skip", "ok": False, "detail": "快照指向的贴图已不存在，无法重导出"}

    try:
        merged = {**params.default_values(), **values}
        vray_props = params.resolve_vray(merged)
        draft = ms_exporter.build_mscript(
            name, vray_props, tex_paths, str(export_dir / "matlib.mat"), True
        )
    except Exception as e:
        return {"action": "skip", "ok": False, "detail": f"脚本生成失败: {e}"}

    report = preflight.run(name, draft, tex_paths, merged)
    if not report["ok"]:
        bad = [i for i in report["items"] if i["level"] == "error"]
        return {"action": "skip", "ok": False,
                "detail": "重生成后预检未通过: " + (bad[0]["detail"] if bad else "未知")}

    plan = {
        "action": "regen",
        "ok": True,
        "detail": f"按快照重导出（{len(values)} 项参数 / {len(tex_paths)} 张贴图），"
                  f"预检通过（阻断 {report['blocking']} / 提示 {report['warnings']}）",
        "material": name,
    }
    if not apply:
        return plan

    backup = ms_path.with_suffix(ms_path.suffix + ".legacy_bak")
    if not backup.exists():
        backup.write_bytes(ms_path.read_bytes())
        plan["backup"] = str(backup)

    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name)
    try:
        new_path = ms_exporter.export(
            material_name=name, vray_props=vray_props, tex_paths=tex_paths,
            out_dir=str(export_dir), matlib_path=str(export_dir / f"{safe}.mat"),
            auto_assign=True,
        )
    except Exception as e:
        plan.update(ok=False, detail=f"重导出失败: {e}")
        return plan
    plan["ms_path"] = str(new_path)
    return plan


def fix_in_place(ms_path: Path, apply: bool) -> dict:
    """快照缺失时的兜底：就地修复 #"..." 写法"""
    text = read_script(ms_path)
    n = count_bad(text)
    if not n:
        return {"action": "clean", "ok": True, "detail": "无 #\"...\" 写法，无需处理"}

    fixed, n2 = fix_hashquote(text)
    items = preflight.check_script(fixed)
    errs = [i for i in items if i["level"] == "error"]
    if errs:
        return {"action": "skip", "ok": False,
                "detail": "文本修复后仍有语法问题: " + errs[0]["detail"]}

    plan = {"action": "fix_text", "ok": True,
            "detail": f"修复 {n2} 处 #\"...\"（无参数快照，仅做最小语法修复）"}
    if not apply:
        return plan

    backup = ms_path.with_suffix(ms_path.suffix + ".legacy_bak")
    if not backup.exists():
        backup.write_bytes(ms_path.read_bytes())
        plan["backup"] = str(backup)
    ms_path.write_bytes(fixed.encode("gbk"))
    plan["ms_path"] = str(ms_path)
    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=None,
                    help="扫描根目录，可重复；默认 output/")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只 dry-run）")
    args = ap.parse_args()

    roots = [Path(r) for r in args.root] if args.root else [SKILL_ROOT / "output"]
    files = scan(roots)
    print("扫描根目录: %s" % ", ".join(str(r) for r in roots))
    print("发现 .ms 文件: %d 个\n" % len(files))

    results = []
    for ms in files:
        snap = ms.parent / "material_params.json"
        if snap.is_file():
            res = regen_from_snapshot(snap, ms, args.apply)
        else:
            res = fix_in_place(ms, args.apply)
        # 重导出走不通（例如贴图已被清掉）时，只要文件里有坏写法，就退化为最小文本修复，
        # 否则最需要救的那份反而会被跳过
        if not res["ok"] and count_bad(read_script(ms)):
            alt = fix_in_place(ms, args.apply)
            alt["detail"] = "重导出不可用（%s），退化为文本修复：%s" % (res["detail"], alt["detail"])
            alt["fallback"] = True
            res = alt
        res["file"] = str(ms)
        results.append(res)
        flag = "OK  " if res["ok"] else "SKIP"
        print("[%s] %-9s %s\n       %s" % (flag, res["action"], ms.name, res["detail"]))

    bad_before = sum(count_bad(read_script(p)) for p in files)
    print("\n汇总：%d 个文件 · 待修坏点 %d 处 · 可处理 %d 个 · 跳过 %d 个"
          % (len(files), bad_before,
             sum(1 for r in results if r["ok"] and r["action"] != "clean"),
             sum(1 for r in results if not r["ok"])))

    out_dir = SKILL_ROOT / "scripts" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    rp = out_dir / ("regen_legacy_%s.json" % ("apply" if args.apply else "dryrun"))
    rp.write_text(json.dumps({"apply": args.apply, "roots": [str(r) for r in roots],
                              "files": results}, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    print("报告: %s" % rp)
    if not args.apply:
        print("\n当前是 dry-run，未改动任何文件。加 --apply 执行。")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
