# -*- coding: utf-8 -*-
"""从 core/params.py 自动生成「参数覆盖实况表」（docs/vray-param-gap.md）。

为什么要有这个生成器：
    docs/vray-param-gap.md 原先是手写快照（写于 15 参数时代），实现涨到 62 参数后
    没人回头改它，于是它声称"SSS / fogColor / refractionGlossiness 整块缺失"，
    而这三项早就实现了。手写对照表必然与代码漂移，且漂移方向永远是"文档比代码旧"，
    做缺口评估的人会被它反向误导。所以这张表改为生成物，永不手改。

用法：
    python scripts/gen_param_gap.py            # 重新生成 docs/vray-param-gap.md
    python scripts/gen_param_gap.py --check    # 只校验是否漂移（CI / run_all_checks 用）
    python scripts/gen_param_gap.py --stdout   # 打印到标准输出，不写盘

状态图例：
    [OK]   该参数在当前代码路径下确实落到了渲染器属性上
    [WARN] 落下去了，但值被固定（用户不可调）或语义被改写（如金属度二值化）
    [LOSS] 目标渲染器无对应能力，属性被丢弃（有损，属渲染器缺口）
    [NONE] 参数不改动任何标量属性（只驱动贴图/前端预览）
"""

from __future__ import annotations

import argparse
import re
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import params as P  # noqa: E402

OUT_PATH = ROOT / "docs" / "vray-param-gap.md"

GEN_HEADER = """# 参数覆盖实况表（自动生成，禁止手改）

> 本文件由 `scripts/gen_param_gap.py` 从 `core/params.py` 生成，**不要手工编辑**。
> 改了 `params.py` 之后跑一次 `python scripts/gen_param_gap.py` 重新生成；
> `python scripts/gen_param_gap.py --check` 会在文档漂移时返回非零退出码。
>
> 旧版本文档是手写快照（写于 15 参数时代），久未更新，反而把已实现的 SSS / 雾化 /
> 折射光泽度误标成"缺失"。这类"文档比代码旧"的漂移是本次改为生成物的直接原因。

"""


# 手工语义覆盖表：这些参数的"落点"不在标量属性里，靠 diff 判不出来，
# 只能按已知语义标注。条目必须少而准，改这里等于改文档口径，要同步 thinking。
_OVERRIDES = {
    "base_color":   ("[OK]",  "经 albedo 贴图通道下发（不写标量颜色属性）"),
    "uv_tiling":    ("[OK]",  "写贴图 UV 控制键 __uv_tiling__，由 exporter 落到每个贴图"),
    "uv_rotation":  ("[OK]",  "写贴图 UV 控制键 __uv_rotation__"),
    "tint_mode":    ("[OK]",  "Python 侧把色偏烘进 albedo，不下发标量属性"),
    "tint_strength": ("[OK]", "同上，色偏强度直接改像素"),
    "tint_chroma":  ("[OK]",  "同上，保留原图色偏比例"),
    "proc_on":      ("[OK]",  "程序化纹理在 Python 侧生成位图后走贴图槽"),
    "proc_type":    ("[OK]",  "同上"),
    "proc_scale":   ("[OK]",  "同上"),
    "proc_amount":  ("[OK]",  "同上"),
    "proc_rough":   ("[OK]",  "同上"),
    "proc_albedo":  ("[OK]",  "同上"),
    "proc_mask":    ("[OK]",  "同上"),
    "proc_mask_scale": ("[OK]", "同上"),
    "proc_seed":    ("[OK]",  "同上"),
    "normal_on":    ("[OK]",  "走法线槽；与 bump_on 同时开启时由 VRayNormalMap / Corona法线 节点承载两层输入"),
    "bump_on":      ("[OK]",  "走凹凸槽；与 normal_on 同时开启时作为内层附加凹凸叠加，不抢占法线槽"),
    # P2 之后强度是「两层独立下发」，落点不是一个属性而是一组，必须逐层写清，
    # 否则读者会以为两个强度都只是材质级 bump_multiplier 的别名。
    "normal_strength": ("[OK]", "叠加时=V-Ray 材质 bump_multiplier（整体凹凸量）/ Corona法线.multiplier；单独法线时同为材质级 bump_multiplier"),
    "bump_strength":   ("[OK]", "叠加时=V-Ray VRayNormalMap.bump_multiplier / Corona 法线.additionalBumpStrength（并强制 additionalBumpOn=true）；单独凹凸时=材质级 bump_multiplier"),
    "nb_overlay":      ("[OK]", "开启后法线与凹凸各占一层：外层材质 bump_multiplier 管整体，内层节点管凹凸倍率，互不覆盖"),
}


def _sample_value(p: P.Param):
    """造一个「与默认值不同」的样本来触发该参数的映射分支。"""
    if p.ptype == "bool":
        return not bool(p.default)
    if p.ptype == "enum":
        opts = [o.get("value") for o in (p.options or []) if isinstance(o, dict)]
        for o in opts:
            if o != p.default:
                return o
        return p.default
    if p.ptype == "color":
        return "#123456"
    if p.ptype in ("range", "float", "int"):
        d = float(p.default or 0.0)
        lo = p.min if p.min is not None else 0.0
        hi = p.max if p.max is not None else d + 1.0
        cand = d + (hi - d) * 0.5 if abs(hi - d) > 1e-9 else d - 1.0
        if abs(cand - d) < 1e-9:
            cand = lo
        if p.ptype == "int":
            return int(round(cand))
        return round(float(cand), 4)
    return p.default


def _enable_parents(values: dict, p: P.Param) -> dict:
    """把 depends 里的父开关打开，否则子参数的下发会被门控吞掉。"""
    for parent, need in (p.depends or {}).items():
        if need is not None:
            try:
                values[parent] = need
            except Exception:
                pass
    return values


def _norm(v):
    """把值规范化成可比较的形态，浮点统一 4 位，元组转 tuple。"""
    if isinstance(v, (list, tuple)):
        return tuple(round(float(x), 4) if isinstance(x, (int, float)) else x for x in v)
    if isinstance(v, float):
        return round(v, 4)
    return v


def _diff_keys(a: dict, b: dict) -> set:
    keys = set(a) | set(b)
    out = set()
    for k in keys:
        if k.startswith("__"):
            continue
        va, vb = a.get(k, "<absent>"), b.get(k, "<absent>")
        if va == "<absent>" or vb == "<absent>":
            out.add(k)
            continue
        try:
            if _norm(va) != _norm(vb):
                out.add(k)
        except Exception:
            if va != vb:
                out.add(k)
    return out


def build_rows() -> list[dict]:
    base = P.default_values()
    v0 = P.resolve_vray(dict(base))
    c0 = P.resolve_corona(dict(base))

    rows = []
    for p in P.PARAMS:
        sample = _sample_value(p)
        vals = dict(base)
        _enable_parents(vals, p)
        vals[p.id] = sample
        try:
            v1 = P.resolve_vray(dict(vals))
            c1 = P.resolve_corona(dict(vals))
        except Exception as e:  # 单个参数不许拖垮整张表
            rows.append({
                "id": p.id, "label": p.label, "group": p.group, "ptype": p.ptype,
                "vray": "解析异常: %s" % e, "corona": "解析异常", "status": "[NONE]",
                "note": "resolve 抛错，需修复",
            })
            continue

        vd = _diff_keys(v0, v1)
        cd = _diff_keys(c0, c1)

        # V-Ray 侧落点：真实属性名
        vray_props = []
        for key in ("vray_on", "vray_off"):
            d = getattr(p, key) or {}
            if isinstance(d, dict):
                vray_props.extend(str(k) for k in d)
        if p.vray_prop:
            vray_props.append(str(p.vray_prop))
        vray_props = sorted(set(vray_props))

        # 被 Corona 丢弃的键：vd 与 _CORONA_DROPPED 的交集
        dropped = sorted(set(vd) & set(P._CORONA_DROPPED))
        silent = sorted(set(vd) & set(P._CORONA_SILENT))

        if not vd and not cd:
            status = "[NONE]"
            note = "不改动标量属性（仅贴图槽 / 前端预览）"
        elif dropped and not cd:
            # 被丢弃的键全部属于 _CORONA_SILENT（深度、双面、暗边修正这类 Corona 没有
            # 对应概念的开关）时，不算有损，只是该项在 Corona 上无意义。
            if set(dropped) <= set(P._CORONA_SILENT):
                status = "[WARN]"
                note = "Corona 忽略 %s（无对应属性，不影响主效果）" % ", ".join(dropped)
            else:
                status = "[LOSS]"
                note = "Corona 无对应能力，已归入丢弃清单：%s" % ", ".join(dropped)
        elif vd and not cd:
            status = "[LOSS]"
            note = "Corona 侧无落点（语义等价或忽略）：%s" % ", ".join(silent or sorted(vd))
        else:
            status = "[OK]"
            note = ""
        if p.id == "metal_mode":
            status, note = "[WARN]", "Corona 只能二值化 metalnessMode(0/1)，中间值需靠金属度贴图"
        if p.id == "sheen_layer_on":
            status, note = "[WARN]", "Corona 走 sheen_amount，强度档位决定数量，无独立开关"
        if p.id in ("thin_film_on", "thin_film_nm"):
            status, note = "[LOSS]", "CoronaPhysicalMtl 无任何 thinFilm* 属性，属渲染器缺口"
        if p.id == "refraction_glossiness":
            # 表里曾把它算成 [OK]（改它会让 corona 输出的 __dropped__ 变化，被误当成落点）。
            # 实际上 Corona 只有一套 roughness，折射侧无处单独落。
            status, note = "[WARN]", ("Corona 只有一套 roughness，折射光泽无处单独生效；"
                                      "调反射光泽会让折射一起变哑，导出时显式告警")
        if p.id == "sss_on":
            status, note = "[WARN]", ("Corona 无 translucency 厚度通道，改用 SSS 近似"
                                      "（sssAmount/sssRadius 已写入），薄壁透光观感有差")
        if p.id == "reflect_strength":
            status, note = "[WARN]", "Corona 侧反射强度真实属性名未真机确认，当前不下发（待 probe 取证）"
        if p.id in _OVERRIDES:
            status, note = _OVERRIDES[p.id]

        rows.append({
            "id": p.id, "label": p.label, "group": p.group, "ptype": p.ptype,
            "vray": ", ".join(vray_props) if vray_props else "—",
            "corona": ", ".join(sorted(cd)) if cd else ("—" if not vd else "无落点"),
            "status": status, "note": note,
        })
    return rows


def render(rows: list[dict]) -> str:
    buf = io.StringIO()
    buf.write(GEN_HEADER)
    total = len(rows)
    stat = {}
    for r in rows:
        stat[r["status"]] = stat.get(r["status"], 0) + 1

    buf.write("## 总览\n\n")
    buf.write("| 项 | 数值 |\n| --- | --- |\n")
    buf.write("| 参数总数 | %d |\n" % total)
    buf.write("| 分组数 | %d |\n" % len({r["group"] for r in rows}))
    for k in ("[OK]", "[WARN]", "[LOSS]", "[NONE]"):
        buf.write("| %s | %d |\n" % (k, stat.get(k, 0)))
    buf.write("\n")

    order = []
    for r in rows:
        if r["group"] not in order:
            order.append(r["group"])
    for g in order:
        sub = [r for r in rows if r["group"] == g]
        buf.write("## %s（%d 项）\n\n" % (g, len(sub)))
        buf.write("| 参数 | 控件 | V-Ray 落点 | Corona 落点 | 状态 | 说明 |\n")
        buf.write("| --- | --- | --- | --- | --- | --- |\n")
        for r in sub:
            buf.write("| `%s` %s | %s | %s | %s | %s | %s |\n" % (
                r["id"], r["label"], r["ptype"],
                r["vray"].replace("|", "\\|"),
                r["corona"].replace("|", "\\|"),
                r["status"], r["note"] or "—",
            ))
        buf.write("\n")

    buf.write("""## 真实缺口（[LOSS] / [WARN] 项）

[LOSS] 项不是实现缺陷，而是目标渲染器没有该能力（例如 Corona 全系无薄膜层）。
把它们列出来是为了让使用者在选渲染器前就知道取舍，而不是导出后对着
"参数明明调了却没变化"发火。

[WARN] 项是能力存在但表达力受限（金属度二值化、绒层无独立开关），
调不到中间态，需要用贴图通道补。

## 维护约定

1. 新增/修改参数只改 `core/params.py`，然后跑 `python scripts/gen_param_gap.py` 重新生成本表。
2. `scripts/run_all_checks.py` 内置 `param_gap` 一项，用 `--check` 模式校验本表未漂移。
3. 本文件出现在 skill 包内，属于交付文档；手改会在下次打包前被 `--check` 打回。
""")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 外部编辑器回写的水印净化
#   事故：用带 AI 水印的编辑器打开这份自动生成的 md 再保存，会在文件头插入
#   一段 AIGC: ... 的 YAML 块、在文件尾插入「内容由AI生成」声明。
#   正文一个字没改，--check 却判漂移，于是「文档一致性」这项检查开始说谎。
#   这里在比较前剔除这两类水印，只让正文参与比对。
# ---------------------------------------------------------------------------
_AIGC_HEAD = re.compile(r"\A---\s*\nAIGC:.*?\n---\s*\n", re.S)
_AIGC_TAIL = re.compile(r"\n*\*（内容由AI生成[^\n]*\*\s*\Z")


def strip_watermarks(text: str) -> str:
    return _AIGC_TAIL.sub("", _AIGC_HEAD.sub("", text)).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验参数覆盖实况表")
    ap.add_argument("--check", action="store_true", help="只校验，不写盘；漂移返回 1")
    ap.add_argument("--stdout", action="store_true", help="打印到标准输出")
    args = ap.parse_args(argv)

    text = render(build_rows())
    if args.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
        return 0
    if args.check:
        raw = OUT_PATH.read_text(encoding="utf-8") if OUT_PATH.exists() else ""
        old = strip_watermarks(raw)
        if old == text.strip():
            print("[OK] docs/vray-param-gap.md 与 params.py 一致")
            return 0
        if strip_watermarks(text) == old:
            print("[OK] docs/vray-param-gap.md 正文一致（仅文件头尾被外部编辑器加了水印，可忽略）")
            return 0
        print("[FAIL] docs/vray-param-gap.md 已漂移，请执行 python scripts/gen_param_gap.py")
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print("[OK] 已生成 %s（%d 行）" % (OUT_PATH.name, len(text.splitlines())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
