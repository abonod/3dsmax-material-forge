"""MaxScript 导出器

为什么这样设计（重要）：
    本机 3dsmaxbatch 因许可校验无法无头运行，导出脚本无法在本机自动验证。
    因此脚本必须做到「属性名失配也不报错」：

    1. 反射式写入 —— 不硬编码 VRayMtl 属性名。先按原名精确写，失败再按关键词
       在 getPropNames 里匹配同类属性。V-Ray 6/7/8 改名也不失效。
    2. 全 try/catch —— 任何一个属性写入失败只记日志，绝不中断整段脚本。
    3. 双分支 —— VRayMtl 不可用时自动降级 PhysicalMaterial，脚本仍能跑完。
    4. 贴图槽类型校验 —— 只认值为 textureMap 类型或 undefined 的属性，
       避免把 diffuse_multiplier 这类数值属性误当贴图槽。
    5. 自诊断 —— 执行结束在 Listener 打印成功/失败清单，便于回传定位。

MaxScript 语法红线（本文件已逐条规避）：
    * catch 后不能是空表达式 `()`，统一写 `catch ( 0 )`
    * 字符串与 name 值拼接必须 `(x as string)`
    * `for .. in .. while .. do` / `if .. do ..` 用法合规
    * 顶层用 `( ... )` 包裹，诊断计数器用 global，避免作用域捕获差异
    * 文件按 GBK 写入（中文 Windows 的 ANSI 代码页），避免中文乱码

模板占位符统一用 @@KEY@@，避免与 MaxScript 的 {} 语法冲突。
"""

from __future__ import annotations

import datetime
import hashlib
import os
import shutil
from typing import Any

import math

# ------------------------------------------------------------------
# VRayMtl 属性精确映射表（v0.8.1 重建，单一真源）
#   语义键 -> (精确候选名元组, 值类型)
#   候选名按优先级排列：首个为本机 3ds Max 2024 + V-Ray 6.20.06 真机实测
#   （VRayMtl 共 190 项属性）确认存在的名字，其后为旧版别名兜底。
#   值类型：
#     color  = Vector/Color 三元组
#     float / int / bool = 标量
#     gloss  = 语义是「粗糙度」，写入前需 1-x 反算成光泽度
#     gray   = 语义是「强度」，写入时按灰度转换成 Color
#     switch = 语义是「开关」，V-Ray 侧类型可能是 Integer 0/1
#   这张表是 Python 侧唯一的属性名来源，MAXScript 模板里不再保存任何别名，
#   从根本上消除「两份别名表各自漂移」的问题。
# ------------------------------------------------------------------
PROP_TARGETS: dict[str, tuple[tuple[str, ...], str]] = {
    # ---- 基础 ----
    "diffuse":               (("Diffuse",), "color"),
    "diffuse_roughness":     (("diffuse_roughness",), "float"),
    "metalness":             (("reflection_metalness", "metalness"), "float"),
    "brdf_useRoughness":     (("brdf_useRoughness",), "bool"),
    "roughness":             (("reflection_glossiness", "roughness"), "gloss"),
    "reflection_glossiness": (("reflection_glossiness",), "float"),
    "reflectionColor":       (("Reflection", "reflectionColor"), "color"),
    "reflection_maxDepth":   (("reflection_maxDepth",), "int"),
    "brdf_ior":              (("reflection_ior", "brdf_ior"), "float"),
    "anisotropy":            (("anisotropy",), "float"),
    # 真机：VRayMtl.anisotropy_axis 是 float（0~1 对应 0~180°）。这里曾误标 int，
    # 标签虽不参与换算，但会误导后来人补 int() 强转——那会把 0.5（45° 斜纹）截断成 0（横向）。
    "anisotropy_axis":       (("anisotropy_axis",), "float"),
    "doubleSided":           (("option_doubleSided", "doubleSided"), "bool"),
    # ---- 折射 / 透明 ----
    "refractionColor":       (("Refraction", "refractionColor"), "color"),
    "refraction_ior":        (("refraction_ior",), "float"),
    "refraction_maxDepth":   (("refraction_maxDepth",), "int"),
    "refraction_glossiness": (("refraction_glossiness",), "float"),
    "fogColor":              (("refraction_fogColor", "fogColor"), "color"),
    "fog_multiplier":        (("refraction_fogMult", "fog_multiplier"), "float"),
    "dispersion":            (("refraction_dispersion", "dispersion"), "float"),
    # ---- 半透 / SSS ----
    "translucency":          (("translucency_on", "translucency"), "int"),   # Integer 枚举，勿压成布尔
    "sssAmount":             (("translucency_amount", "sssAmount"), "float"),
    "sssRadius":             (("translucency_thickness", "sssRadius"), "float"),
    # ---- 光泽涂层 sheen（V-Ray 6 无 sheen_amount，强度以颜色表达）----
    "sheen_amount":          (("sheen_color",), "gray"),
    "sheenColor":            (("sheen_color", "sheenColor"), "color"),
    "sheenGlossiness":       (("sheen_glossiness", "sheenGlossiness"), "float"),
    # ---- 清漆 coat ----
    "coat_amount":           (("coat_amount",), "float"),
    "coat_glossiness":       (("coat_glossiness",), "float"),
    "coat_ior":              (("coat_ior",), "float"),
    # ---- 薄膜 / 能量 ----
    "thinFilm_on":           (("thinFilm_on",), "bool"),
    "thinFilmThickness":     (("thinFilm_thickness_min",), "float"),
    "thinFilmIOR":           (("thinFilm_ior",), "float"),
    "energyPreservation":    (("preservationMode", "energyPreservation"), "switch"),
    "fixDarkEdges":          (("brdf_fixDarkEdges", "fixDarkEdges"), "bool"),
    # ---- 自发光 / 凹凸 ----
    "selfIllumination":      (("selfIllumination",), "color"),
    "selfIllumination_multiplier": (("selfIllumination_multiplier",), "float"),
    "bump_multiplier":       (("texmap_bump_multiplier", "bump_multiplier"), "float"),
}

# 依赖开关：某参数一旦要写，必须同时把它的使能开关打开，否则 V-Ray 侧
# 数值写进去了却不生效（真机上「静默无效」最常见的原因）。
PROP_ON_SWITCH: dict[str, dict[str, Any]] = {
    "dispersion":        {"refraction_dispersion_on": True},
    "thinFilmThickness": {"thinFilm_on": True},
    "thinFilmIOR":       {"thinFilm_on": True},
}

# Corona 侧没有这些「使能开关」：色散只有数值 refractDispersion、薄膜由 IOR+厚度直接生效。
# 沿用 V-Ray 的开关名会往 CoronaPhysicalMtl 上写不存在的属性（实测会报 [FAIL]
# refraction_dispersion_on），所以 Corona 用空表。
# 真机核对：CoronaPhysicalMtl 的 dispersion 是「强度 + dispersionEnable 开关」两段式，
# 只写 dispersion 不打开开关等于没写（默认 dispersionEnable=false）。
CORONA_PROP_ON_SWITCH: dict[str, dict[Any, Any]] = {
    # 2026-11-22 真机修正：本机 Corona 物理材质 getPropNames 实证使能真名是
    # refractDispersionEnable（旧版/文档口径写作 dispersionEnable）。保留双候选，
    # 由 mfAssign 按属性存在性取第一个，避免往不存在的名字上写而静默失效。
    "dispersion": {("refractDispersionEnable", "dispersionEnable"): True},
}

# Corona 的薄膜没有 min/max 随机区间，无需成对写入
CORONA_PROP_FOLLOW: dict[str, tuple[str, ...]] = {}

# 联动写入：真机上某些量成对出现，只写一半会不生效或产生非确定结果
# （薄膜厚度是 min~max 随机区间，只写 min 而 max 还停在 400 会得到 400 的非法区间）
PROP_FOLLOW: dict[str, tuple[str, ...]] = {
    "thinFilmThickness": ("thinFilm_thickness_max",),
}

# 无法映射到 VRayMtl 标量、由 exporter 单独处理的语义键
PROP_NO_SCALAR: dict[str, str] = {
    "displacement_amount": "置换强度由 VRayDisplacementMod 修改器承担，不写材质属性",
}

# 贴图槽关键词（按语义通道）
# 贴图槽精确属性名（本机 V-Ray 6.20.06 实测：全部为 texmap_* 形式）
TEX_SLOTS_EXACT: dict[str, list[str]] = {
    "diffuse":     ["texmap_diffuse"],
    "bump":        ["texmap_bump"],
    "roughness":   ["texmap_roughness"],
    "displacement": ["texmap_displacement"],
    "opacity":     ["texmap_opacity"],
}

TEX_SLOTS: dict[str, list[str]] = {
    "__DIFFUSE__":  ["diffuse"],
    "__NORMAL__":   ["bump"],
    "__HEIGHT__":   ["bump"],
    "__NORMALBUMP__": ["bump"],
    "__ROUGHNESS__": ["roughness"],
    "__DISPLACE__": ["displacement"],
    "__AO__":       ["diffuse"],
    "__OPACITY__":  ["opacity"],
}


def _ms_escape(path: str) -> str:
    """MaxScript verbatim 字符串：用 @ 前缀，内部反斜杠不转义"""
    p = path.replace("\\", "/")
    return f'@"{p}"'


def _ms_value(v: Any) -> str:
    """把 Python 值序列化成 MaxScript 字面量"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (tuple, list)) and len(v) == 3:
        r, g, b = (max(0, min(255, int(round(float(x) * 255)))) for x in v)
        return f"(color {r} {g} {b})"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


# ------------------------------------------------------------------
# MAXScript 模板
# ------------------------------------------------------------------
# 模板片段来源：公共部分（tpl_common）与 V-Ray 专属部分（tpl_vray）分开存放，
# Corona 复用同一份公共片段，拼接顺序与 0.8.4 的内联模板保持一致。
from .tpl_common import (  # noqa: E402
    HEAD as _TPL_HEAD,
    FUNCS as _TPL_FUNCS,
    ENV as _TPL_ENV,
    MID as _TPL_MID,
    TAIL as _TPL_TAIL,
)
from .tpl_vray import VRAY_FNS as _TPL_VRAY_FNS, VRAY_CREATE as _TPL_VRAY_CREATE  # noqa: E402
from ..compat import probe_ms as _compat_probe_ms  # noqa: E402
from .tpl_corona import CORONA_FNS as _TPL_CORONA_FNS, CORONA_CREATE as _TPL_CORONA_CREATE  # noqa: E402

_TEMPLATE = (
    _TPL_HEAD
    + _TPL_FUNCS
    + _TPL_VRAY_FNS
    + _TPL_ENV
    + _TPL_VRAY_CREATE
    + _TPL_MID
    + _TPL_TAIL
)

# ======================================================================
#                     Corona 专属候选表与模板
# ======================================================================
# Corona 物理材质（CoronaPhysicalMtl）的贴图槽/属性名与 V-Ray 完全不同体系，必须独立成表。
# 旧版 CoronaMtl 的属性名（colorDiffuse / texmapDiffuse / levelReflect ...）作为后备候选并列，
# 让同一份脚本在 Corona 7+ 与更老的安装上都能尽量命中。
#
# 候选链里**故意不混入 glossiness**：Legacy CoronaMtl 的 glossiness 与 Corona 的 roughness
# 语义相反，若把粗糙度值写进 glossiness 会产生「越调越亮」的反向结果。
# 宁可 SKIP 并在日志里显式报出，也不写错方向的数值。
# 2026-11 已按本机 Corona 12 随包官方脚本 coronaConverter.ms 的**物理材质**分支逐条核对
# （该脚本里体验旧 CoronaMtl 与 CoronaPhysicalMtl 两套属性名，只有 outputMaterial.* 那批才是物理材质）：
#   漫反射 baseColor / 粗糙度 baseRoughness / IOR baseIor / 各向异性 baseAnisotropy+baseAnisoRotation
#   / 不透明度 opacityLevel / 色散 dispersion / 涂膜 clearcoatAmount+clearcoatIor+clearcoatRoughness
#   / 光泽绒毛 sheenAmount+sheenColor+sheenRoughness / SSS sssAmount+sssRadius
#   / 凹凸 baseBumpTexmap+baseBumpMapAmount / 自发光 selfIllumColor+selfIllumLevel。
# 两个真机以外拿不到的结论：
#   1) 物理材质**没有数值金属度属性**，只有 metalnessMode(0/1 模式开关) + metalnessTexmap，
#      因此 metalness 语义只能落到 metalnessMode（写入 0/1，靠 MaxScript 的 float→int 隐式转换）。
#   2) 物理材质**没有薄膜层**（无 thinFilm* 属性），薄膜两项摘除；V-Ray 侧 resolve_vray 当前
#      也不输出该键，属两端共同缺口，不是 Corona 独有。
CORONA_PROP_TARGETS = {
    "diffuse":                     (("baseColor", "basecolor", "colorDiffuse"), "color"),
    # 真机确认：物理材质只有 metalnessMode（0/1 模式开关）+ metalnessTexmap，无标量金属度，
    # baseMetalness 这个常见误写的名字在本机 Corona 上并不存在，已从候选链剔除。
    "metalness":                   (("metalnessMode", "metalness"), "float"),
    "roughness":                   (("baseRoughness", "roughness"), "float"),
    "brdf_ior":                    (("baseIor", "ior"), "float"),
    "anisotropy":                  (("baseAnisotropy", "anisotropy"), "float"),
    "anisotropy_axis":             (("baseAnisoRotation", "anisotropyRotation"), "float"),
    # 透明：真机核对确认 Corona 的「透明」是 refractionAmount（折射透射），
    # opacityLevel 只做 alpha 遮挡；两者同名易混，语义键必须分开。
    "refraction_amount":           (("refractionAmount", "refraction"), "float"),
    "opacity":                     (("opacityLevel", "levelOpacity", "opacity"), "float"),
    "dispersion":                  (("dispersion", "refractDispersion"), "float"),
    "coat_amount":                 (("clearcoatAmount",), "float"),
    "coat_ior":                    (("clearcoatIor",), "float"),
    "coat_roughness":              (("clearcoatRoughness",), "float"),
    "sheen_amount":                (("sheenAmount",), "float"),
    "sheenColor":                  (("sheenColor",), "color"),
    "sheen_roughness":             (("sheenRoughness",), "float"),
    "sssAmount":                   (("sssAmount", "baseSssAmount", "levelTranslucency"), "float"),
    "sssRadius":                   (("sssRadius", "baseSssRadius"), "float"),
    "bump_multiplier":             (("baseBumpMapAmount", "bumpAmount"), "float"),
    "selfIllumination":            (("selfIllumColor", "colorSelfIllum"), "color"),
    "selfIllumination_multiplier": (("selfIllumLevel", "levelSelfIllum"), "float"),
}

# 贴图槽精确名（按关键词首词索引，与 TEX_SLOTS_EXACT 同构）
# 物理材质槽是「贴图 + 开关」两件套（baseTexmap / baseTexmapOn），只挂贴图不置 On 等于没挂，
# 开关由 tpl_common 的 mfTexmapOn 在落槽后自动置 true。
CORONA_TEX_SLOTS_EXACT = {
    "base":      ["baseTexmap", "texmapBase", "texmapDiffuse"],
    "bump":      ["baseBumpTexmap", "texmapBump", "texmapBumpMap"],
    "roughness": ["baseRoughnessTexmap", "texmapBaseRoughness", "texmapRoughness"],
    "opacity":   ["texmapOpacity"],
}

# Corona 的置换不走材质槽（Corona 材质无 displacement 输入），由收尾段的
# Corona置换修改器 承担（无英文注册名，见 registry 的 displacement_modifier_rejected），
# 因此这里不列 __DISPLACE__ / __DISP__。
_SLOT_MAP_CORONA = {
    "__DIFFUSE__":   (["base"],      "albedo"),
    "__HEIGHT__":    (["bump"],      "height"),
    "__ROUGHNESS__": (["roughness"], "roughness"),
    "__OPACITY__":   (["opacity"],   "opacity"),
}

# Corona 的 AO 叠加：CoronaAO 节点自带 colorUnoccluded / colorOccluded，
# 把漫反射位图接进 colorUnoccluded 即可实现「AO 压暗、原图保留」。
_AO_BLOCK_CORONA = r'''    (
        -- AO 叠加（Corona）：把漫反射位图接进 CoronaAO 的 colorUnoccluded，遮挡区域压暗
        local aoSlot = mfFindSlotEx mtl #("baseTexmap", "texmapBase", "texmapDiffuse") #("base", "diffuse")
        local baseBm = undefined
        local dirt = undefined
        if aoSlot != undefined do (
            try ( baseBm = getProperty mtl aoSlot ) catch ( baseBm = undefined )
        )
        local isCoronaMat = ((findString (toLower ((classof mtl) as string)) "corona") != undefined)
        if isCoronaMat do (
            local dirtCls = mfCoronaDirtClass()
            if dirtCls != undefined do (
                try ( dirt = dirtCls() ) catch ( dirt = undefined )
            )
        )
        if dirt != undefined then (
            mfSetOn dirt #("colorUnoccluded", "unoccludedColor") (color 255 255 255)
            mfSetOn dirt #("colorOccluded", "occludedColor") (color 30 30 30)
            if baseBm != undefined do (
                -- 2026-11-22 真机 getPropNames（CoronaAO，18 属性）实证：未遮挡位图槽
                -- texmapUnoccluded 必须同时打开 texmapUnoccludedOn，否则位图不参与
                -- 求值，AO 只剩纯色，漫反射贴图等于丢失。
                mfSetOn dirt #("texmapUnoccluded") baseBm
                mfSetOn dirt #("texmapUnoccludedOn") true
            )
            if aoSlot != undefined then (
                try (
                    setProperty mtl aoSlot dirt
                    mf_OK += 1
                    mfLog ("  [OK] AO 已用 " + ((classof dirt) as string) + " 叠加（漫反射贴图保留）")
                ) catch (
                    mf_FAIL += 1
                    mfLog "  [FAIL] AO 挂接失败"
                )
            ) else (
                mfLog "  [WARN] 未找到基色槽，AO 跳过（属可选项降级，不计失败）"
            )
        ) else (
            mfLog ("  [WARN] Corona AO 纹理类不可用（已试 CoronaAO / Corona_AO），AO 未挂接；可手动使用: " + {ao_esc})
        )
    )'''

_CORONA_TEMPLATE = (
    _TPL_HEAD
    + _TPL_FUNCS
    + _TPL_CORONA_FNS
    + _TPL_ENV
    + _TPL_CORONA_CREATE
    + _TPL_MID
    + _TPL_TAIL
)


def build_mscript(
    material_name: str,
    vray_props: dict,
    tex_paths: dict,
    matlib_path: str = "",
    auto_assign: bool = True,
    logfile_path: str = "",
    renderer: str = "vray",
) -> str:
    """生成完整的 .ms 脚本字符串

    vray_props : resolve_vray() 的输出，特殊键 __NORMAL__ / __HEIGHT__ 等为贴图占位
    tex_paths  : {"albedo": "...", "normal": "...", ...}
    auto_assign: 有选中物体时是否自动赋材质（True = 新手友好，导完即见效果）
    renderer   : "vray" / "corona"，决定属性候选表、贴图槽名、AO 块与模板片段
    """
    # ---- 1. 分离标量 / 贴图占位 / 控制特殊键 ----
    scalars: dict = {}
    tex_specs: dict = {}
    specials: dict = {}
    for k, v in vray_props.items():
        if isinstance(v, str) and v.startswith("__") and v.endswith("__"):
            tex_specs[v] = True
        elif k.startswith("__"):
            specials[k] = v
        else:
            scalars[k] = v

    # ---- 0. 渲染器分派：候选表 / 槽名 / 模板全部按渲染器切换 ----
    is_corona = str(renderer).lower() == "corona"
    # 依赖开关与联动表按渲染器切换：V-Ray 的使能开关在 Corona 上不存在
    on_switch_map = CORONA_PROP_ON_SWITCH if is_corona else PROP_ON_SWITCH
    follow_map = CORONA_PROP_FOLLOW if is_corona else PROP_FOLLOW
    targets = CORONA_PROP_TARGETS if is_corona else PROP_TARGETS
    tex_exact = CORONA_TEX_SLOTS_EXACT if is_corona else TEX_SLOTS_EXACT

    # UV 平铺 / 旋转：位图坐标用弧度
    try:
        uv_tile = float(specials.get("__uv_tiling__", 1.0) or 1.0)
    except (TypeError, ValueError):
        uv_tile = 1.0
    try:
        uv_angle = float(specials.get("__uv_rotation__", 0.0) or 0.0)
    except (TypeError, ValueError):
        uv_angle = 0.0
    try:
        nb_bump = float(specials.get("__nb_bump_mult__", 0.0) or 0.0)
    except (TypeError, ValueError):
        nb_bump = 0.0
    # P2：法线层强度独立下发。缺省回落到 bump_multiplier（resolve_vray 里写的是法线强度）
    try:
        nb_nstr = float(specials.get("__nb_nstr__", specials.get("bump_multiplier", 1.0)) or 0.0)
    except (TypeError, ValueError):
        nb_nstr = 1.0
    try:
        nb_bstr = float(specials.get("__nb_bstr__", nb_bump) or 0.0)
    except (TypeError, ValueError):
        nb_bstr = nb_bump

    # ---- 2. 标量赋值语句 ----
    lines = []
    # MaxScript 不支持 #"字符串" 形式的 name 字面量（会报“位于 string，需要 "(" or "["”），
    # 只能裸写 #标识符；属性名非法时退化为字符串参数走别名匹配。
    def _ms_name(k):
        s = str(k)
        ok = s and (s[0].isalpha() or s[0] == "_") and all(c.isalnum() or c == "_" for c in s)
        return f"#{s}" if ok else None

    def _cands(k: str) -> tuple[str, ...]:
        """语义键 -> 候选属性名元组；表外键退化为键名本身（保留扩展端口透传能力）"""
        tgt = targets.get(str(k))
        if tgt is not None:
            return tgt[0]
        return (str(k),)

    def _norm_value(k: str, v: Any) -> Any:
        """按语义把值转成 V-Ray 侧应有的形态"""
        tgt = targets.get(str(k))
        kind = tgt[1] if tgt is not None else ""
        if kind == "gloss":
            # 语义是粗糙度，而 V-Ray 6 只有 reflection_glossiness（光泽度），需 1-x
            try:
                return round(1.0 - max(0.0, min(1.0, float(v))), 4)
            except (TypeError, ValueError):
                return v
        if kind == "gray":
            # sheen_amount 之类没有独立强度属性，强度以颜色灰度表达
            try:
                g = round(max(0.0, min(1.0, float(v))), 4)
                return (g, g, g)
            except (TypeError, ValueError):
                return v
        if kind == "switch":
            try:
                return 1 if float(v) else 0
            except (TypeError, ValueError):
                return 1 if v else 0
        return v

    def _assign_line(k: str, v: Any) -> str:
        cs = ", ".join(f'"{c}"' for c in _cands(k))
        return f'    mfAssign mtl #({cs}) {_ms_value(_norm_value(k, v))}'

    def _follow_lines(k: str, v: Any) -> list[str]:
        out: list[str] = []
        for f in follow_map.get(str(k), ()):
            out.append(f'    mfAssign mtl #("{f}") {_ms_value(_norm_value(k, v))}')
        return out

    # 依赖开关必须先写：数值参数只有在其使能开关打开后才会被渲染器采信
    # 开关值按参数实际取值决定——参数为 0/关时开关写 False，避免「色散=0 但开关亮着」
    # 这种半开状态（真机核对：Corona 的 dispersionEnable=true 而 dispersion 缺失时，
    # 渲染结果会回落到默认 40 的观感，与用户设的 0 不一致）。
    emitted: set[str] = set()
    for k, v in scalars.items():
        for sw_key, sw_val in on_switch_map.get(str(k), {}).items():
            # 使能属性名允许写成元组做多候选（不同渲染器版本真名不同），
            # mfAssign 收到候选数组会按属性存在性取第一个。
            sw_name = '","'.join(str(x) for x in sw_key) if isinstance(sw_key, (tuple, list)) else str(sw_key)
            if sw_name in emitted:
                continue
            try:
                active = bool(float(v))
            except (TypeError, ValueError):
                active = bool(v)
            emitted.add(sw_name)
            lines.append(_assign_line(sw_name, sw_val if active else (not sw_val)))

    for k, v in scalars.items():
        if str(k) in PROP_NO_SCALAR:
            msg = PROP_NO_SCALAR[str(k)]
            if is_corona:
                # 真机 Modifier.classes 枚举实证：Corona 置换修改器注册名已本地化为
                # 「Corona置换修改器」；英文名在本机不存在（真机证伪，见 registry 的 displacement_modifier_rejected）
                msg = msg.replace("VRayDisplacementMod", "Corona置换修改器")
            lines.append(f'    mfLog "  [INFO] {k}：{msg}"')
            continue
        lines.append(_assign_line(k, v))
        lines.extend(_follow_lines(k, v))
    if not lines:
        lines.append('    mfLog "  (无标量参数)"')
    assignments = "\n".join(lines)

    # ---- 3. 贴图挂接语句 ----
    tex_lines = []

    def attach_line(kws: list[str], path: str) -> str:
        # 精确属性名优先，关键词兜底（mfFindSlotEx 内部处理）
        ex = tex_exact.get(kws[0], [])
        kw_str = ", ".join(f'"{k}"' for k in kws)
        ex_str = ", ".join(f'"{k}"' for k in ex)
        return f"    mfAttachTex mtl #({ex_str}) #({kw_str}) {_ms_escape(path)}"

    # 法线不走 slot_map：统一由 __NORMALWRAP__ / __NORMALBUMP__ 的 VRayNormalMap 分支处理，
    # 保证 VRayMtl 的凹凸槽里只出现高度图
    _SLOT_MAP_VRAY = {
        "__DIFFUSE__":   (["diffuse"],      "albedo"),
        "__HEIGHT__":    (["bump"],         "height"),
        "__ROUGHNESS__": (["roughness"],    "roughness"),
        "__DISPLACE__":  (["displacement"], "height"),
        "__OPACITY__":   (["opacity"],      "opacity"),
        "__DISP__":      (["displacement"], "height"),
    }
    slot_map = _SLOT_MAP_CORONA if is_corona else _SLOT_MAP_VRAY

    # 兼容旧数据/直传：__NORMAL__ 的语义是「法线图」，一律转成 VRayNormalMap 包装，
    # 不再直挂 VRayMtl.bump（凹凸槽只允许挂高度图）
    if "__NORMAL__" in tex_specs:
        tex_specs.pop("__NORMAL__", None)
        tex_specs["__NORMALWRAP__"] = True

    # 法线 + 凹凸叠加：合成节点优先，缺高度图则退化为「仅法线包装」，bump 子槽留空
    if "__NORMALBUMP__" in tex_specs:
        n_p = tex_paths.get("normal")
        h_p = tex_paths.get("height")
        _nums = f"{nb_nstr:.4f} {nb_bstr:.4f}"
        if n_p and h_p:
            tex_lines.append(
                f"    mfAttachNormalBump mtl {_ms_escape(n_p)} {_ms_escape(h_p)} {_nums}")
        elif n_p:
            tex_lines.append(
                f"    mfAttachNormalBump mtl {_ms_escape(n_p)} undefined {nb_nstr:.4f} 0.0")
        tex_specs.pop("__NORMALBUMP__", None)

    # 仅法线（没有高度图）：同样包一层 VRayNormalMap，bump 子槽留空，
    # 保证凹凸槽只挂高度图，法线不再被直挂到 VRayMtl.bump
    if "__NORMALWRAP__" in tex_specs:
        n_p = tex_paths.get("normal")
        if n_p:
            tex_lines.append(
                f"    mfAttachNormalBump mtl {_ms_escape(n_p)} undefined {nb_nstr:.4f} 0.0")
        tex_specs.pop("__NORMALWRAP__", None)

    # 漫反射不再无条件挂载：由 params.resolve_vray() 在贴图集勾选 albedo 时
    # 输出 texmap_diffuse=__DIFFUSE__ 占位符驱动，避免未勾选仍被应用到材质

    for token, info in slot_map.items():
        if token not in tex_specs:
            continue
        kws, chan = info
        p = tex_paths.get(chan)
        if not p:
            continue
        tex_lines.append(attach_line(kws, p))

    # AO：V-Ray 下用污垢纹理（真机类名 VRay_污垢）叠加，原漫反射位图写进 texmap_unoccluded_color
    ao_path = tex_paths.get("ao")
    if vray_props.get("__activate_ao") and ao_path and is_corona:
        tex_lines.append(_AO_BLOCK_CORONA.replace("{ao_esc}", _ms_escape(ao_path)))
    elif vray_props.get("__activate_ao") and ao_path:
        ao_esc = _ms_escape(ao_path)
        tex_lines.append(f'''    (
        -- AO 叠加：仅 V-Ray 材质 + 污垢纹理（VRay_污垢）可用时执行
        local aoSlot = mfFindSlotEx mtl #("texmap_diffuse") #("diffuse")
        local baseBm = undefined
        local dirt = undefined
        if aoSlot != undefined do (
            try ( baseBm = getProperty mtl aoSlot ) catch ( baseBm = undefined )
        )
        if isVray do (
            local dirtCls = mfDirtClass()
            if dirtCls != undefined do (
                try ( dirt = dirtCls() ) catch ( dirt = undefined )
            )
        )
        if dirt != undefined then (
            mfSetOn dirt #("radius", "半径") 24.0
            mfSetOn dirt #("subdivs", "细分") 16
            mfSetOn dirt #("occluded_color") (color 30 30 30)
            -- 2026-11-22 真机修复：旧实现把漫反射位图塞进 unoccludedColor（Color 类型），
            -- setProperty 必然失败又被静默回退成纯白，于是 AO 一开 albedo 贴图整体丢失，
            -- 日志却照打「漫反射贴图保留」（写入失败被当成成功）。真机回读实测：
            -- texmap_diffuse 处的污垢纹理内部 texmap 为 undefined。真机 getPropNames 全量枚举
            -- （44 个属性）证实：污垢纹理没有 texmap 槽，真正的位图入口是
            -- texmap_unoccluded_color（配套 texmap_unoccluded_color_on / _multiplier）。
            -- 位图必须落到该槽并打开 _on 开关，写后回读确认，失败必须计入 mf_FAIL。
            local baseKept = false
            if baseBm != undefined do (
                local bn = toLower ((classof baseBm) as string)
                if ((findString bn "dirt") != undefined or (findString bn "污垢") != undefined) do (
                    local inner = undefined
                    try ( inner = getProperty baseBm #texmap_unoccluded_color ) catch ( inner = undefined )
                    if inner != undefined do baseBm = inner
                )
                if (isKindOf baseBm TextureMap) then (
                    try (
                        setProperty dirt #texmap_unoccluded_color baseBm
                        setProperty dirt #texmap_unoccluded_color_on true
                        local back = undefined
                        try ( back = getProperty dirt #texmap_unoccluded_color ) catch ( back = undefined )
                        if (back == baseBm) do baseKept = true
                    ) catch ( baseKept = false )
                ) else (
                    baseKept = (mfSetOn dirt #("unoccluded_color") baseBm)
                )
            )
            if (not baseKept) do (
                mfSetOn dirt #("unoccluded_color") (color 255 255 255)
                mf_FAIL += 1
                mfLog "  [FAIL] AO 叠加未能接回原漫反射贴图（VRayDirt.texmap 写入或回读失败），albedo 已丢失"
            )
            if aoSlot != undefined then (
                try (
                    setProperty mtl aoSlot dirt
                    mf_OK += 1
                    local keepTxt = "（原漫反射已丢失，AO 层内为纯色）"
                    if baseKept do keepTxt = "（漫反射贴图保留在 AO 未遮挡槽）"
                    mfLog ("  [OK] AO 已用 " + ((classof dirt) as string) + " 叠加" + keepTxt)
                ) catch (
                    mf_FAIL += 1
                    mfLog "  [FAIL] AO 挂接失败"
                )
            ) else (
                mf_FAIL += 1
                mfLog "  [WARN] 未找到漫反射槽，AO 跳过"
            )
        ) else (
            mfLog ("  [WARN] 污垢(AO)纹理类不可用（已试 VRayDirt / VRay_Dirt / VRay_污垢），AO 未挂接；可手动使用: " + {ao_esc})
        )
    )''')

    if not tex_lines:
        tex_lines.append('    mfLog "  (无贴图)"')
    textures = "\n".join(tex_lines)

    # ---- 4. 组装 ----
    texlist = " #(" + ", ".join(_ms_escape(p) for p in tex_paths.values()) + ")"
    matlib = _ms_escape(matlib_path) if matlib_path else '""'

    out = _CORONA_TEMPLATE if is_corona else _TEMPLATE
    if is_corona:
        dropped = vray_props.get("__dropped__") or []
        if dropped:
            out = out.replace("@@DROPPED@@", "；".join(str(x) for x in dropped))
        else:
            out = out.replace("@@DROPPED@@", "")
    out = out.replace("@@MATNAME@@", material_name.replace('"', ""))
    out = out.replace("@@TIMESTAMP@@", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    # 置换修改器参数
    disp_on = any(k == "texmap_displacement" and str(v).startswith("__")
                  for k, v in vray_props.items())
    try:
        disp_sub = int(specials.get("__disp_subdiv", 0) or 0)
    except (TypeError, ValueError):
        disp_sub = 0
    try:
        disp_amt = float(specials.get("__disp_amount", 0.0) or 0.0)
    except (TypeError, ValueError):
        disp_amt = 0.0
    if not disp_amt:
        try:
            disp_amt = float(vray_props.get("displacement_amount", 0.0) or 0.0)
        except (TypeError, ValueError):
            disp_amt = 0.0
    disp_tex = (tex_paths.get("height") or "").replace("\\", "/") if disp_on else ""

    out = out.replace("@@DISPON@@", "true" if disp_on else "false")
    out = out.replace("@@DISPAMT@@", f"{disp_amt * 100.0:.2f}")
    out = out.replace("@@DISPSUB@@", str(disp_sub))
    out = out.replace("@@DISPTEX@@", disp_tex)
    out = out.replace("@@UVTILE@@", f"{uv_tile:.4f}")
    out = out.replace("@@UVANGLE@@", f"{uv_angle:.4f}")
    out = out.replace("@@NBBUMP@@", f"{nb_bump:.4f}")
    # 现场版本探测段：内容来自 core/compat.py（版本矩阵的同一份事实来源），
    # 生成脚本会把 MAX_VERSION / MAX_LANG / ACTIVE_RENDERER / VRAY_AVAILABLE /
    # CORONA_AVAILABLE / COMPAT_LEVEL 打进日志，让每份真机记录自带版本证据。
    out = out.replace("@@COMPATPROBE@@", _compat_probe_ms())
    out = out.replace("@@NBNSTR@@", f"{nb_nstr:.4f}")
    out = out.replace("@@NBBSTR@@", f"{nb_bstr:.4f}")
    # 严格模式恒开：校验失败必须计入 FAIL 并让 run_all_checks 判红，
    # 不提供「关掉严格模式让日志变绿」的开关 —— 那正是假通过的入口。
    out = out.replace("@@STRICT@@", "true")
    out = out.replace("@@AUTOASSIGN@@", "true" if auto_assign else "false")
    out = out.replace("@@TEXLIST@@", texlist)
    out = out.replace("@@MATLIB@@", matlib)
    out = out.replace("@@LOGFILE@@", _ms_escape(logfile_path) if logfile_path else '""')
    out = out.replace("@@ASSIGNMENTS@@", assignments)
    out = out.replace("@@TEXTURES@@", textures)
    return out


def _write_script(path: str, script: str) -> str:
    """按「GBK 优先、UTF-8 兜底」落盘，返回真正写进文件的编码名。

    编码必须回传给调用方：导出后自检要按同一编码回读。此前自检固定用
    gbk + errors="replace" 读盘，遇到 UTF-8 兜底产物会把正常中文解成乱码，
    再把好脚本误判成贴图断链/材质名错乱（乱码误判的根源）。
    """
    try:
        with open(path, "w", encoding="gbk", newline="\r\n") as f:
            f.write(script)
        return "gbk"
    except UnicodeEncodeError:
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(script)
        return "utf-8"


def export_with_meta(material_name: str, vray_props: dict, tex_paths: dict,
                     out_dir: str, matlib_path: str = "", auto_assign: bool = True,
                     pack_textures: bool = True, logfile_path: str = "",
                     renderer: str = "vray") -> dict:
    """导出 .ms 并回传落盘证据：path / encoding / bytes / sha256。

    为什么单独要这一层：导出后自检的唯一合法输入是「磁盘上那份 .ms」，
    而「写盘时到底用了哪个编码」只有写盘这一侧知道，猜编码必然出错。
    因此 write → 按实际编码严格回读 → 与内存脚本逐字符比对，不一致直接抛错，
    让导出当场失败，而不是把半截/乱码文件当成功交给用户。

    编码说明：MaxScript 在中文 Windows 上按系统 ANSI（GBK）解析脚本文件，
    带 BOM 的 UTF-8 容易导致中文材质名/路径乱码甚至语法报错，因此优先写 GBK；
    仅当内容含 GBK 无法表示的字符时回退 UTF-8（无 BOM）。

    贴图打包（pack_textures）：把 5 通道贴图复制到 .ms 同级的 textures/ 目录。
    脚本运行时会先找「脚本同级 textures/」，命中就用相对位置，回退才是绝对路径。
    不复制的话，用户把导出目录整体拷到别的机器/别的盘，贴图就会回退到原绝对路径而断链。
    """
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in material_name) or "material"
    path = os.path.join(out_dir, f"{safe}.ms")

    if pack_textures and tex_paths:
        texdir = os.path.join(out_dir, "textures")
        try:
            os.makedirs(texdir, exist_ok=True)
        except Exception:
            texdir = ""
        if texdir:
            for _ch, src in tex_paths.items():
                try:
                    if not src or not os.path.isfile(src):
                        continue
                    dst = os.path.join(texdir, os.path.basename(src))
                    if os.path.abspath(src) != os.path.abspath(dst):
                        shutil.copy2(src, dst)
                except Exception:
                    # 单张贴图复制失败不影响脚本生成，运行期仍会回退到绝对路径
                    pass

    script = build_mscript(material_name, vray_props, tex_paths, matlib_path, auto_assign,
                           logfile_path, renderer=renderer)
    encoding = _write_script(path, script)
    raw = open(path, "rb").read()
    with open(path, "r", encoding=encoding, newline="") as f:
        disk_text = f.read()
    if disk_text.replace("\r\n", "\n") != script.replace("\r\n", "\n"):
        raise RuntimeError(f"写盘回读不一致（{encoding}）：{path}")
    return {
        "path": path,
        "encoding": encoding,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "chars": len(script),
    }


def export(material_name: str, vray_props: dict, tex_paths: dict,
           out_dir: str, matlib_path: str = "", auto_assign: bool = True,
           pack_textures: bool = True, logfile_path: str = "",
           renderer: str = "vray") -> str:
    """导出 .ms 文件，返回路径（保持旧接口；需要落盘证据请用 export_with_meta）"""
    return export_with_meta(material_name, vray_props, tex_paths, out_dir,
                            matlib_path, auto_assign, pack_textures,
                            logfile_path, renderer)["path"]


if __name__ == "__main__":
    import json
    from core.params import default_values, resolve_vray

    props = resolve_vray({**default_values(), "normal_on": True, "ao_on": True})
    print(json.dumps(props, ensure_ascii=False, indent=2))
