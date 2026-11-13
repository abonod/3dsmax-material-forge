# -*- coding: utf-8 -*-
"""一键打包 3dsmax-material-forge.skill

为什么要有这个脚本
------------------
打包曾经是手工操作：右键压缩 → 改扩展名 → 拖进 dist。手工活的两个后果都被验证过：
    1. 包里装的是旧代码（dist 里的 params.py 不认识壁纸/拉丝预设，前端却在显示它们，
       用户装上后点预设没反应）；
    2. 旧包把 output/（几十 MB 的 job 产物、贴图、临时 .ms）一起打进去，包体虚胖 1003KB。

所以这里把「体检 → 打版本号 → 打包 → 留档」固化下来，一条命令完成，不通过体检就不出包。

做法
----
1. 版本号从 core/version.py 取（SSOT），顺手把它同步进 SKILL.md 的 frontmatter；
2. 体检：导入 core 做一次 schema 自检（参数数 / 预设计数 / 预设快照完整性），
   再把 16 个预设全部导出成 .ms 跑 verify_ms 静态体检，任何一项不过就中止；
3. 打包：只收源码与文档，output / temp / __pycache__ / scripts/reports / dist 一律排除；
4. 留档：旧包改名 .bak_时间戳，同时写 dist/BUILD_INFO.json（版本 / 文件数 / sha256 / 变更摘要）。

用法
----
    python scripts/package_skill.py                # 体检 + 打包
    python scripts/package_skill.py --skip-verify  # 跳过体检（仅改文档时用，慎用）
    python scripts/package_skill.py --no-bakcup    # 不保留旧包备份
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = SKILL_ROOT.parent / "dist"
PKG_NAME = "3dsmax-material-forge"

# 排除规则：目录名前缀匹配 + 文件名后缀匹配
EXCLUDE_DIRS = {"output", "temp", "__pycache__", "dist", "node_modules", ".git", ".idea", "reports"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".bak", ".log", ".err")
EXCLUDE_PREFIX = ("temp_", "temp", "tools_verify")   # 根目录调试残留（temp_*.ms / tools_verify_*.py）不许进包
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}


def read_version() -> str:
    sys.path.insert(0, str(SKILL_ROOT))
    from core.version import __version__  # noqa: WPS433

    return __version__


def sync_skill_md_version(version: str) -> bool:
    """把版本号写进 SKILL.md 的 frontmatter（幂等）"""
    md = SKILL_ROOT / "SKILL.md"
    lines = md.read_text(encoding="utf-8").split("\n")
    if not lines or lines[0].strip() != "---":
        print("  [warn] SKILL.md 没有 frontmatter，跳过版本同步")
        return False

    # 只在前置元数据区（第一条 --- 到第二条 ---）里找 version
    end = next((i for i, l in enumerate(lines[1:], start=1) if l.strip() == "---"), None)
    if end is None:
        print("  [warn] SKILL.md frontmatter 未闭合，跳过版本同步")
        return False

    newline = f"version: {version}"
    for i in range(1, end):
        if lines[i].startswith("version:"):
            if lines[i].strip() == newline:
                return False
            lines[i] = newline
            md.write_text("\n".join(lines), encoding="utf-8")
            print(f"  SKILL.md 版本号更新为 {version}")
            return True

    lines.insert(2, newline)  # name 之后
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"  SKILL.md 写入版本号 {version}")
    return True


def schema_selfcheck() -> dict:
    """SSOT 一致性自检：参数数、预设计数、快照完整性"""
    sys.path.insert(0, str(SKILL_ROOT))
    from core import params as P  # noqa: WPS433

    sc = P.schema()
    n_params = len(P.PARAMS)
    n_presets = len(P.PRESETS)
    checks: list[tuple[str, bool, str]] = []

    checks.append(("schema.params 与 PARAMS 等长", len(sc["params"]) == n_params, f"{len(sc['params'])} vs {n_params}"))
    ids = [p.id for p in P.PARAMS]
    checks.append(("参数 id 无重复", len(set(ids)) == len(ids), f"{len(ids)} 项 / 去重 {len(set(ids))}"))
    checks.append(("预设三表键一致",
                   set(P.PRESETS) == set(P.PRESET_LABELS) == set(P.PRESET_EXTRAS),
                   f"{len(P.PRESETS)}/{len(P.PRESET_LABELS)}/{len(P.PRESET_EXTRAS)}"))
    snaps = P.preset_snapshots()
    bad = [k for k, v in snaps.items() if len(v) != n_params]
    checks.append(("每个预设快照覆盖全部参数", not bad, "缺失: %s" % bad if bad else "16/16 完整"))
    checks.append(("BEGINNER_IDS 全部存在", all(b in ids for b in P.BEGINNER_IDS),
                   f"{len(P.BEGINNER_IDS)} 项"))
    # resolve 一遍，确保每套预设都能解析出具体的 V-Ray 属性（不是空 dict）
    empty = [k for k, v in snaps.items() if not P.resolve_vray(v)]
    checks.append(("每套预设可解析出 V-Ray 属性", not empty, "空: %s" % empty if empty else f"{n_presets}/{n_presets}"))

    ok = all(c[1] for c in checks)
    for name, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:<28}{detail}")
    return {"ok": ok, "params": n_params, "presets": n_presets}


def ms_selfcheck(workdir: Path) -> dict:
    """把全部预设导出成 .ms 并跑 verify_ms 静态体检"""
    sys.path.insert(0, str(SKILL_ROOT))
    from core import params as P  # noqa: WPS433
    from core.exporters import ms_exporter as E  # noqa: WPS433

    out = workdir / "ms_export"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    for key, snap in P.preset_snapshots().items():
        E.export(P.PRESET_LABELS[key].replace("(", "_").replace(")", "_"),
                 P.resolve_vray(snap), {}, str(out))

    r = subprocess.run([sys.executable, str(SKILL_ROOT / "scripts" / "verify_ms.py"), str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in r.stdout.split("\n") if "总计" in l or "结论" in l and "错误" in l]
    passed = r.returncode == 0
    print(f"  [{'PASS' if passed else 'FAIL'}] MAXScript 静态体检          "
          + (tail[-1].strip() if tail else r.stdout.strip().split("\n")[-1][:80]))
    if not passed:
        print(r.stdout[-1500:])
        print(r.stderr[-800:])
    return {"ok": passed, "detail": (tail[-1].strip() if tail else "")}


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn in EXCLUDE_NAMES or fn.endswith(EXCLUDE_SUFFIX) or fn.startswith(EXCLUDE_PREFIX):
                continue
            p = Path(dirpath) / fn
            yield p


def report_stale(keep_versions: int = 2, keep_baks: int = 1) -> list[Path]:
    """列出 dist 里该清理的旧包，但不自己删。

    为什么不在这里删：打包脚本是日常高频动作，一旦内置物理删除，
    一次手误就把回滚包清了（且不可恢复）。这里只做"点名"，
    真正的删除交给外部（操作系统回收站 / 人），保持可回滚。

    留档策略：最新 keep_versions 个版本包 + keep_baks 份 .bak 回滚点。
    老逻辑每次打包都 rename 出 .bak_时间戳却从不回收，
    实测攒了 5 份同名 .bak（每份 0.56MB），既看不出哪份能回滚，
    也让"哪个才是当前包"变得含糊。
    """
    stale: list[Path] = []
    vers = sorted(DIST_DIR.glob(f"{PKG_NAME}-*.skill"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    stale += vers[keep_versions:]
    baks = sorted(DIST_DIR.glob(f"{PKG_NAME}*.bak_*"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    stale += baks[keep_baks:]

    if stale:
        print("  可清理（请用回收站删除，勿物理删除）：")
        for p in stale:
            print(f"    - {p}")
    else:
        print("  无需清理旧包")
    return stale


def build_zip(version: str) -> tuple[Path, list[str], str]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    final = DIST_DIR / f"{PKG_NAME}.skill"
    versioned = DIST_DIR / f"{PKG_NAME}-{version}.skill"

    # 同版本重复打包才留档：跨版本时上一版 *.skill 本身就是回滚点，无需再复制一份 .bak
    # （老逻辑对 final 与 versioned 各备份一次，一次打包就吐两份内容相同的 .bak，纯冗余）
    if versioned.exists():
        bak = versioned.with_name(versioned.name + f".bak_{datetime.now():%Y%m%d_%H%M%S}")
        shutil.move(str(versioned), str(bak))
        print(f"  同版本旧包留档 -> {bak.name}")
    if final.exists():
        print(f"  覆盖当前包 {final.name}（上一版回滚点在 *-旧版本.skill）")

    entries: list[str] = []
    with zipfile.ZipFile(versioned, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(iter_files(SKILL_ROOT)):
            arc = f"{PKG_NAME}/{p.relative_to(SKILL_ROOT).as_posix()}"
            z.write(p, arc)
            entries.append(arc)

    shutil.copy2(versioned, final)
    report_stale()
    h = hashlib.sha256(versioned.read_bytes()).hexdigest()
    return versioned, entries, h


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 3dsmax-material-forge.skill")
    ap.add_argument("--skip-verify", action="store_true", help="跳过体检（仅文档改动时可用）")
    ap.add_argument("--no-bakcup", action="store_true", help="不保留旧包备份")
    args = ap.parse_args()

    version = read_version()
    print(f"打包 {PKG_NAME} v{version}")
    print(f"SKILL_ROOT = {SKILL_ROOT}")
    print(f"DIST_DIR   = {DIST_DIR}")

    print("\n[1/4] 同步版本号")
    sync_skill_md_version(version)

    print("\n[2/4] 体检")
    if args.skip_verify:
        print("  [skip] 按 --skip-verify 跳过体检")
        info = {"params": None, "presets": None}
    else:
        sc = schema_selfcheck()
        if not sc["ok"]:
            print("\n体检未通过，中止打包（不产出新包，dist 保持原样）")
            return 1
        tmp = SKILL_ROOT / "temp" / "_package_check"
        tmp.mkdir(parents=True, exist_ok=True)
        ms = ms_selfcheck(tmp)
        if not ms["ok"]:
            print("\nMAXScript 体检未通过，中止打包")
            return 1
        info = {"params": sc["params"], "presets": sc["presets"]}

    print("\n[3/4] 打包")
    if args.no_bakcup:
        # 不在这里物理删除：只提示，删除交由外部（回收站）执行，保留可回滚余地
        olds = sorted(DIST_DIR.glob(f"{PKG_NAME}*.skill"))
        if olds:
            print("  --no-bakcup：以下旧包需要清理（请用回收站删除）")
            for f in olds:
                print(f"    - {f}")
    path, entries, sha = build_zip(version)
    size_kb = path.stat().st_size / 1024

    print("\n[4/4] 写 BUILD_INFO")
    build_info = {
        "name": PKG_NAME,
        "version": version,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha,
        "entries": len(entries),
        "params": info.get("params"),
        "presets": info.get("presets"),
        "release_notes": _notes(version),
        "files": entries,
    }
    bi = DIST_DIR / "BUILD_INFO.json"
    bi.write_text(json.dumps(build_info, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成：{path}")
    print(f"  版本   v{version}")
    print(f"  条目   {len(entries)} 个文件")
    print(f"  体积   {size_kb:.0f} KB")
    print(f"  sha256 {sha[:16]}...")
    print(f"  清单   {bi}")
    return 0


def _notes(version: str) -> list[str]:
    sys.path.insert(0, str(SKILL_ROOT))
    from core.version import RELEASE_NOTES  # noqa: WPS433

    return RELEASE_NOTES.get(version, [])


if __name__ == "__main__":
    sys.exit(main())
