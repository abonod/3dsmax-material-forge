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
    "anisotropy_axis":       (("anisotropy_axis",), "int"),
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
_TEMPLATE = r'''/* ==================================================================
   Material Forge - 自动生成脚本
   材质名称 : @@MATNAME@@
   生成时间 : @@TIMESTAMP@@
   目标环境 : 3ds Max 2024 / V-Ray 6.x（VRayMtl 不可用时降级 PhysicalMaterial）
   ------------------------------------------------------------------
   用法：3ds Max 菜单 Scripting > Run Script，选择本文件即可。
        脚本会自动创建材质球、载入贴图、写入材质编辑器槽位、保存材质库。
   ================================================================== */
(
    global mf_LOG  = #()
    global mf_OK   = 0
    global mf_FAIL = 0

    local MF_TEXTURES = @@TEXLIST@@
    local MF_MATNAME  = "@@MATNAME@@"
    local MF_MATLIB   = @@MATLIB@@
    local MF_UV_TILE  = @@UVTILE@@          -- UV 平铺次数（U/V 同值）
    local MF_UV_ANGLE = @@UVANGLE@@          -- UV 旋转（度，对应坐标 W_Angle）
    local MF_NB_BUMP  = @@NBBUMP@@           -- 法线+凹凸叠加时内层凹凸倍率
    local MF_DISP_ON  = @@DISPON@@           -- 是否给选中物体加置换修改器
    local MF_DISP_AMT = @@DISPAMT@@          -- 置换强度
    local MF_DISP_SUB = @@DISPSUB@@          -- 置换细分
    local MF_DISP_TEX = "@@DISPTEX@@"        -- 置换高度图
    local MF_AUTOASSIGN = @@AUTOASSIGN@@     -- 是否自动把材质赋给当前选中物体

    global MF_LOGFILE = @@LOGFILE@@      -- 空串表示不落盘；非空时同时写运行日志（实机排查/批量复盘用）
    fn mfLog s = (
        append mf_LOG s
        format "%\n" s
        if (MF_LOGFILE != "") do (
            try (
                local fh = openFile MF_LOGFILE mode:"a"
                if (fh != undefined) do ( format "%\n" s to:fh; close fh )
            ) catch ( 0 )
        )
    )

    -- 贴图路径解析：优先「脚本同级的 textures 目录」，其次原绝对路径。
    -- 这样整个导出目录可以整体搬走/改名，贴图不会因为绝对路径失效而丢图。
    fn mfResolveTex f = (
        local base = ""
        try ( base = getFilenamePath (getSourceFileName()) ) catch ( base = "" )
        local r = f
        if (base != "") do (
            local cand = base + "textures/" + (filenameFromPath f)
            if (doesFileExist cand) then ( r = cand )
            else if (doesFileExist f) then ( r = f )
            else r = cand
        )
        r
    )

    -- 精确写（无类型守卫）：贴图槽等非标量目标用
    fn mfSet obj p v = (
        if (hasProperty obj p) then (
            try (
                setProperty obj p v
                mf_OK += 1
                true
            ) catch (
                mf_FAIL += 1
                mfLog ("  [FAIL] " + (p as string))
                false
            )
        ) else false
    )

    -- 值转换：把脚本侧字面量转成目标属性当前的类型；转不了就拒绝，绝不静默写入
    -- 返回 #(ok, 值或错误说明)
    fn mfCoerce cur v = (
        local cc = classof cur
        local vc = classof v
        if (cc == Color) then (
            if (vc == Color) then #(true, v)
            else #(false, ("需要 Color，收到 " + (vc as string)))
        ) else if (cc == BooleanClass) then (
            if (vc == BooleanClass) then #(true, v)
            else if (vc == Integer) then #(true, (v != 0))
            else if (vc == Float) then #(true, (v != 0.0))
            else #(false, ("需要 Boolean，收到 " + (vc as string)))
        ) else if (cc == Integer) then (
            if (vc == Integer) then #(true, v)
            else if (vc == BooleanClass) then #(true, (if v then 1 else 0))
            else if (vc == Float) then #(true, (v as integer))
            else #(false, ("需要 Integer，收到 " + (vc as string)))
        ) else if (cc == Float) then (
            if (vc == Float) then #(true, v)
            else if (vc == Integer) then #(true, (v as float))
            else if (vc == BooleanClass) then #(true, (if v then 1.0 else 0.0))
            else #(false, ("需要 Float，收到 " + (vc as string)))
        ) else if (cc == UndefinedClass) then (
            #(false, "该属性是贴图槽，拒绝写入标量")
        ) else (
            #(false, ("不支持的目标类型 " + (cc as string)))
        )
    )

    -- 带类型守卫的写入：读当前值拿类型 -> 转换 -> 写 -> 逐条可读日志
    fn mfSetTyped obj p v = (
        local cur = undefined
        local okRead = false
        try ( cur = getProperty obj p; okRead = true ) catch ( okRead = false )
        if (not okRead) then (
            mf_FAIL += 1
            mfLog ("  [FAIL] 读取失败: " + (p as string))
            false
        ) else (
            local res = mfCoerce cur v
            if (res[1] != true) then (
                mf_FAIL += 1
                mfLog ("  [TYPE] " + (p as string) + " : " + (res[2] as string) + "，已拒绝写入")
                false
            ) else (
                try (
                    setProperty obj p res[2]
                    mf_OK += 1
                    mfLog ("  [OK] " + (p as string) + " = " + (res[2] as string))
                    true
                ) catch (
                    mf_FAIL += 1
                    mfLog ("  [FAIL] 写入异常: " + (p as string))
                    false
                )
            )
        )
    )

    -- 属性名同时包含全部关键词即命中
    fn mfHit pn kws = (
        local ok = true
        for k in kws where (findString pn k == undefined) do ok = false
        ok
    )

    fn mfSetByKeywords obj kws v = (
        local hit = undefined
        for p in (getPropNames obj) while hit == undefined do (
            if (mfHit (toLower (p as string)) kws) do hit = p
        )
        if hit != undefined then ( mfSetTyped obj hit v )
        else (
            mf_FAIL += 1
            mfLog ("  [FAIL] 关键字无命中: " + (kws as string))
            false
        )
    )

    -- 精确优先：按候选名逐个 hasProperty，命中即带类型守卫写入；
    -- 全部候选都不存在才记一条可读日志（旧版此处静默 +1，脚本跑完看不出哪里没写进去）
    fn mfAssign obj cands v = (
        local hit = undefined
        for c in cands while hit == undefined do (
            local pn = c as name
            if (hasProperty obj pn) do hit = pn
        )
        if (hit == undefined) then (
            mf_FAIL += 1
            mfLog ("  [SKIP] 属性不存在: " + (cands[1] as string) + "（候选 " + (cands.count as string) + " 个全不匹配）")
            false
        ) else ( mfSetTyped obj hit v )
    )

    -- 材质是否挂了任意贴图（扫描全部 textureMap 类型属性）
    fn mfHasAnyTexture m = (
        local has = false
        for p in (getPropNames m) while (not has) do (
            local v = undefined
            try ( v = getProperty m p ) catch ( v = undefined )
            if (v != undefined and (superClassOf v == textureMap)) do has = true
        )
        has
    )

    -- 判断材质编辑器槽位是否为「未使用的默认槽」
    -- 踩坑记录 1：Max 2024 里 1~24 号槽默认都挂着材质对象，永远不为 undefined，
    -- 所以不能再用 meditMaterials[i] == undefined 判空（那样恒不成立，每套都会覆盖第 1 槽）。
    -- 踩坑记录 2（0.8.3 实机验证暴露）：同一版本里默认槽的类并不固定，实测为
    -- PhysicalMaterial（名字 "02 - Default"）；只认 Standardmaterial 会让判空恒为 false，
    -- 于是「24 槽全被占用」→ 又退化成覆盖第 1 槽。用户自己起的名字（如 ProtectedMat）
    -- 不含默认编号，因此不会被误判为空槽。
    -- 现判据：名字命中原生默认命名（名字带 # 编号，或形如 01 - Default / 01 - 默认）+ 全属性无任何贴图。
    fn mfEmptyMeditSlot m = (
        local r = false
        try (
            if (m != undefined) do (
                local nm = m.name
                local isDefaultName = (matchPattern nm pattern:"*#*") or (matchPattern nm pattern:"* - Default") or (matchPattern nm pattern:"* - 默认")
                if (isDefaultName and (not (mfHasAnyTexture m))) do r = true
            )
        ) catch ( r = false )
        r
    )

    -- 找贴图槽：属性名含关键词，且值为 textureMap 或未赋值
    fn mfFindTexSlot obj kws = (
        local found = undefined
        local cur = undefined
        local got = false
        for p in (getPropNames obj) while found == undefined do (
            if (mfHit (toLower (p as string)) kws) do (
                cur = undefined
                got = false
                try ( cur = getProperty obj p; got = true ) catch ( got = false )
                if got do (
                    if cur == undefined then found = p
                    else if ((superClassOf cur) == textureMap) do found = p
                )
            )
        )
        found
    )

    -- 位图 UV 坐标：平铺 + 旋转（W_Angle 单位为度；属性名按版本反射兜底）
    fn mfApplyUV b = (
        if (MF_UV_TILE != 1.0 or MF_UV_ANGLE != 0.0) do (
            local co = undefined
            try ( co = b.coords ) catch ( co = b.coordinates )
            if co == undefined do try ( co = b.coordinates ) catch ( 0 )
            if co != undefined then (
                try ( co.U_Tiling = MF_UV_TILE ) catch ( 0 )
                try ( co.V_Tiling = MF_UV_TILE ) catch ( 0 )
                if (MF_UV_ANGLE != 0.0) do (
                    local ap = undefined
                    for nm in #("W_Angle", "U_V_Angle", "UV_Rotation", "angle") while ap == undefined do (
                        for p in (getPropNames co) while ap == undefined do (
                            if ((toLower (p as string)) == (toLower nm)) do ap = p
                        )
                    )
                    if ap == undefined do (
                        for p in (getPropNames co) while ap == undefined do (
                            if ((findString (toLower (p as string)) "angle") != undefined) do ap = p
                        )
                    )
                    if ap != undefined then (
                        try (
                            setProperty co ap MF_UV_ANGLE
                            mfLog ("  [OK] UV 旋转 " + (MF_UV_ANGLE as string) + "° -> " + (ap as string))
                        ) catch (
                            mfLog "  [WARN] UV 旋转赋值失败"
                        )
                    ) else (
                        mfLog "  [WARN] 未找到坐标旋转属性，UV 旋转跳过"
                    )
                )
            ) else (
                mfLog "  [WARN] 该贴图无坐标对象，UV 平铺/旋转跳过"
            )
        )
        b
    )

    -- 建位图贴图节点（自动套用 UV 平铺 / 旋转）
    fn mfBitmap f = (
        local rf = mfResolveTex f
        local b = Bitmaptexture fileName:rf
        b.name = (filenameFromPath f)
        try ( b.alphaSource = 0 ) catch ( b.alphaSource = 0 )
        mfApplyUV b
    )

    -- 贴图槽定位：先按精确属性名，再退回关键词（跨语言/旧版本兜底）
    fn mfFindSlotEx obj exNames kws = (
        local found = undefined
        for c in exNames while found == undefined do (
            local pn = c as name
            if (hasProperty obj pn) do found = pn
        )
        if (found == undefined) do found = mfFindTexSlot obj kws
        found
    )

    -- 把贴图接到指定语义槽
    fn mfAttachTex mtl exNames kws f = (
        local slot = mfFindSlotEx mtl exNames kws
        if slot == undefined then (
            mf_FAIL += 1
            mfLog ("  [FAIL] 未找到贴图槽: " + (kws[1] as string))
            false
        ) else (
            local bm = mfBitmap f
            try (
                setProperty mtl slot bm
                mf_OK += 1
                mfLog ("  [OK] tex -> " + (slot as string))
                true
            ) catch (
                mf_FAIL += 1
                mfLog ("  [FAIL] tex -> " + (slot as string))
                false
            )
        )
    )

    -- 按候选属性名写值：兼容中文本地化的属性名（如 污垢纹理的 radius/半径）
    fn mfSetOn obj kws val = (
        local pns = for p in (getPropNames obj) collect (p as string)
        local hit = false
        for k in kws while (not hit) do (
            local m = for p in pns where (toLower p) == (toLower k) collect p
            if m.count > 0 do (
                try ( setProperty obj m[1] val; hit = true ) catch ( hit = false )
            )
        )
        hit
    )

    -- 污垢（AO）纹理类名同样本地化：中文版 V-Ray 6 实测为 "VRay_污垢"，
    -- 英文字面量 VRayDirt / VRay_Dirt 均不存在，直接调用会静默降级。
    fn mfDirtClass = (
        local cands = #("VRayDirt", "VRay_Dirt", "VRay_污垢")
        for n in cands do (
            local c = undefined
            try ( c = execute n ) catch ( c = undefined )
            if c != undefined do return c
        )
        for c in textureMap.classes do (
            local s = c as string
            local sl = toLower s
            local isVRay = ((findString sl "vray") != undefined)
            local isDirt = (((findString s "污垢") != undefined) or ((findString sl "dirt") != undefined))
            if (isVRay and isDirt) do (
                local instOk = false
                try ( local o = c(); instOk = true ) catch ( instOk = false )
                if instOk do return c
            )
        )
        undefined
    )

    -- 法线（+ 可选凹凸）：VRayNormalMap(normal_map=法线, bump_map=高度) -> mtl.bump
    -- bf 传 undefined 表示没有高度图：bump 子槽留空，只做法线扰动，
    -- 避免把法线 RGB 当高度用（V-Ray 下会产生杂乱凹凸）

    -- V-Ray 插件类名会随界面语言本地化（中文版为 "VRay_法线贴图"），
    -- 直接写字面量 VRayNormalMap 在中文环境下恒为 undefined，会导致静默降级。
    -- 这里按候选名 + 类注册表语义遍历解析，保证跨语言拿到法线贴图类。
    fn mfNormalMapClass = (
        local cands = #("VRayNormalMap", "VRay_法线贴图", "VRay_法线")
        for n in cands do (
            local c = undefined
            try ( c = execute n ) catch ( c = undefined )
            if c != undefined do return c
        )
        for c in textureMap.classes do (
            local s = c as string
            local sl = toLower s
            local isVRay = ((findString sl "vray") != undefined)
            local isNormal = (((findString s "法线") != undefined) or ((findString sl "normal") != undefined))
            local isBumpish = (((findString s "凹凸") != undefined) or ((findString sl "bump2normal") != undefined))
            if (isVRay and isNormal and (not isBumpish)) do (
                local instOk = false
                try ( local o = c(); instOk = true ) catch ( instOk = false )
                if instOk do return c
            )
        )
        undefined
    )

    fn mfAttachNormalBump mtl nf bf = (
        local combo = undefined
        local comboClass = mfNormalMapClass()
        if comboClass != undefined do (
            try ( combo = comboClass() ) catch ( combo = undefined )
        )
        if combo == undefined then (
            mfLog "  [WARN] V-Ray 法线贴图节点不可用，降级为仅法线"
            mfAttachTex mtl #("texmap_bump") #("bump") nf
        ) else (
            local okN = false
            local okB = false
            local ns = mfFindTexSlot combo #("normal")
            if ns != undefined do (
                try ( setProperty combo ns (mfBitmap nf); okN = true ) catch ( okN = false )
            )
            if bf != undefined do (
                local bs = mfFindTexSlot combo #("bump")
                if bs != undefined do (
                    try ( setProperty combo bs (mfBitmap bf); okB = true ) catch ( okB = false )
                )
            )
            if (MF_NB_BUMP > 0.0) do (
                for p in (getPropNames combo) where ((findString (toLower (p as string)) "mult") != undefined) do (
                    try ( setProperty combo p MF_NB_BUMP ) catch ( 0 )
                )
            )
            local slot = mfFindSlotEx mtl #("texmap_bump") #("bump")
            if slot != undefined then (
                try (
                    setProperty mtl slot combo
                    mf_OK += 1
                    local tag = "法线+凹凸叠加"
                    if bf == undefined do tag = "仅法线(VRayNormalMap 包装)"
                    local bsTxt = okB as string
                    if bf == undefined do bsTxt = "留空"
                    mfLog ("  [OK] " + tag + "已挂载 (normal=" + (okN as string) + " / bump=" + bsTxt + ")")
                    true
                ) catch (
                    mf_FAIL += 1
                    mfLog "  [FAIL] 法线+凹凸节点挂载失败"
                    false
                )
            ) else (
                mf_FAIL += 1
                mfLog "  [FAIL] 未找到 bump 槽，法线+凹凸跳过"
                false
            )
        )
    )

    format "\n===== Material Forge 开始 =====\n"
    format "材质名称: %\n" MF_MATNAME
    format "贴图数量: %\n" MF_TEXTURES.count

    -- ---------------- 0. 环境自检（不引用 VRayMtl 名字，避免变量未定义抛错）----------------
    local mfMaxVer = "未知"
    try ( mfMaxVer = ((maxVersion())[1]) as string ) catch ( mfMaxVer = "未知" )
    local mfRender = "未知"
    try ( mfRender = (classof renderers.current) as string ) catch ( mfRender = "未知" )
    -- 渲染器判定：中文版类名是「V_Ray_6__update_2_1」，直接搜 "vray" 会漏（下划线断开），
    -- 先把非字母数字字符剥掉再匹配，兼容 V_Ray / V-Ray / VRay 各种写法。
    local mfRenderNorm = ""
    for i = 1 to mfRender.count do (
        local cl = toLower (substring mfRender i 1)
        if ((findString "abcdefghijklmnopqrstuvwxyz0123456789" cl) != undefined) do (
            mfRenderNorm += cl
        )
    )
    local mfIsVRayRender = ((findString mfRenderNorm "vray") != undefined)
    mfLog ("[ENV] 3ds Max 版本号: " + mfMaxVer)
    mfLog ("[ENV] 当前渲染器: " + mfRender)
    mfLog ("[ENV] 贴图目录策略: 优先脚本同级 textures/ ，回退绝对路径")

    -- 强制降级钩子：运行前把全局变量 mfForceDegrade 设为 true，
    -- 就能在「已装 V-Ray」的机器上验证降级分支（回归自检用），日常使用不必设置。
    local mfDegrade = false
    try ( mfDegrade = (mfForceDegrade == true) ) catch ( mfDegrade = false )
    if mfDegrade do (
        mfLog "**** [ENV] 已启用强制降级（mfForceDegrade=true），本次不创建 VRayMtl ****"
    )

    -- ---------------- 1. 创建材质 ----------------
    local isVray = false
    local mtl = undefined
    if (not mfDegrade) do (
        try ( mtl = VRayMtl(); isVray = true ) catch ( isVray = false )
    )
    if isVray then ( mfLog "[OK] VRayMtl 创建成功" )
    else (
        mfLog "**** [WARN] VRayMtl 不可用：V-Ray 未安装 / 未加载 / 未授权 ****"
        mfLog "****          已自动降级为 PhysicalMaterial，参数会尽量映射，效果与 VRayMtl 不等价 ****"
        try ( mtl = PhysicalMaterial() ) catch ( mtl = undefined )
        if mtl == undefined do (
            try ( mtl = StandardMaterial() ) catch ( mtl = undefined )
        )
        try ( MF_MATNAME = MF_MATNAME + "_降级_" + ((classof mtl) as string) ) catch ( 0 )
    )
    if (isVray and not mfIsVRayRender) do (
        mfLog "**** [WARN] 当前渲染器不是 V-Ray：VRayMtl 已创建，但视口/渲染需切到 V-Ray 才能看到正确效果 ****"
        mfLog "****          切换方法：Render Setup (F10) > Renderer > V-Ray 6 ****"
    )
    if mtl == undefined then (
        messageBox "无法创建任何材质（VRayMtl / PhysicalMaterial / StandardMaterial 均失败）" title:"Material Forge"
        throw "Material Forge: 材质创建失败"
    )
    mtl.name = MF_MATNAME

    -- ---------------- 2. 写入标量与颜色参数 ----------------
    mfLog "--- 参数写入 ---"
@@ASSIGNMENTS@@

    -- ---------------- 3. 挂接贴图 ----------------
    mfLog "--- 贴图挂接 ---"
@@TEXTURES@@

    -- ---------------- 4. 创建材质球对象 ----------------
    mfLog "--- 场景对象 ---"
    local sph = undefined
    try ( sph = Sphere radius:60 pos:[0,0,0] segments:64 ) catch ( sph = undefined )
    if sph != undefined then (
        sph.name = MF_MATNAME + "_Ball"
        sph.material = mtl
        try ( sph.wirecolor = color 128 128 128 ) catch ( sph.wirecolor = color 128 128 128 )
        mfLog "[OK] 已创建预览球并赋予材质"
    ) else (
        mfLog "[WARN] 预览球创建失败，材质仍在材质编辑器中"
    )

    -- ---------------- 5. 放入材质编辑器槽位 ----------------
    -- 5.1 已有同名槽 -> 复用，重复跑同一材质不会堆一堆副本
    local target = 0
    for i = 1 to 24 while target == 0 do (
        local em = meditMaterials[i]
        if em != undefined do (
            local same = false
            try ( same = (em.name == mtl.name) ) catch ( same = false )
            if same do target = i
        )
    )
    local reused = (target > 0)
    -- 5.2 没有同名槽，找未使用的默认槽
    if target == 0 do (
        for i = 1 to 24 while target == 0 do (
            if (mfEmptyMeditSlot meditMaterials[i]) do target = i
        )
    )
    -- 5.3 24 槽全被占用才兜底覆盖第 1 槽
    local forced = false
    if target == 0 do ( target = 1; forced = true )
    meditMaterials[target] = mtl
    if forced then (
        mfLog "[WARN] 材质编辑器 24 槽均已占用，覆盖第 1 槽"
    ) else if reused then (
        mfLog ("[OK] 材质已回填材质编辑器原槽位 " + (target as string) + " 号")
    ) else (
        mfLog ("[OK] 材质已放入材质编辑器槽位 " + (target as string) + " 号")
    )

    -- ---------------- 6. 保存材质库（可选，能力探测式降级）----------------
    -- 3ds Max 2024 已移除 newMaterialLibrary / loadTempMaterialLibrary 等旧接口，
    -- 无库文件可用时一律 [SKIP] 而不是 [FAIL]，避免把环境能力缺失误报成失败。
    if MF_MATLIB != "" do (
        local lib = undefined
        try (
            if (doesFileExist MF_MATLIB) do lib = loadMaterialLibrary MF_MATLIB
        ) catch ( lib = undefined )
        if lib == undefined then (
            mfLog ("  [SKIP] 未找到可写材质库文件，材质仅保留在材质编辑器中（可手动另存为: " + MF_MATLIB + "）")
        ) else (
            try (
                lib[mtl.name] = mtl
                saveMaterialLibrary lib
                mf_OK += 1
                mfLog ("  [OK] 材质库已保存: " + MF_MATLIB)
            ) catch (
                mfLog ("  [SKIP] 材质库追加失败: " + (getCurrentException()))
            )
        )
    )

    -- ---------------- 6.5 置换修改器（仅作用于当前选中物体）----------------
    if (MF_DISP_ON and MF_DISP_SUB > 0) do (
        if (selection.count == 0) then (
            mfLog "[WARN] 置换细分未生效：请先选中物体再执行脚本"
        ) else (
            for o in selection do (
                local dm = undefined
                try ( dm = VRayDisplacementMod() ) catch ( dm = undefined )
                if dm == undefined then (
                    mfLog "[WARN] VRayDisplacementMod 不可用，置换细分跳过"
                ) else (
                    try (
                        for p in (getPropNames dm) do (
                            local lp = toLower (p as string)
                            if ((findString lp "amount") != undefined) do ( try ( setProperty dm p MF_DISP_AMT ) catch ( 0 ) )
                            if ((findString lp "subdiv") != undefined) do ( try ( setProperty dm p MF_DISP_SUB ) catch ( 0 ) )
                        )
                        if (MF_DISP_TEX != "") do (
                            local ts = mfFindTexSlot dm #("texmap")
                            if ts != undefined do ( try ( setProperty dm ts (mfBitmap MF_DISP_TEX) ) catch ( 0 ) )
                        )
                        addModifier o dm
                        mfLog ("[OK] 置换修改器 -> " + o.name + "（细分 " + (MF_DISP_SUB as string) + "）")
                    ) catch (
                        mfLog ("[FAIL] 置换修改器添加失败: " + o.name)
                    )
                )
            )
        )
    )

    -- ---------------- 6.6 自动赋给当前选中物体 ----------------
    if MF_AUTOASSIGN do (
        if (selection.count == 0) then (
            mfLog "[INFO] 当前未选中物体：材质已放入材质编辑器，可拖给模型"
        ) else (
            local n = 0
            for o in selection do (
                try ( o.material = mtl; n += 1 ) catch ( 0 )
            )
            mfLog ("[OK] 材质已自动赋给 " + (n as string) + " 个选中物体")
        )
    )

    -- ---------------- 7. 自诊断输出 ----------------
    format "\n========== Material Forge 执行结果 ==========\n"
    for s in mf_LOG do format "%\n" s
    format "---------------------------------------------\n"
    format "成功: %   失败: %\n" mf_OK mf_FAIL
    format "材质类型: %\n" (classof mtl)
    format "材质名称: %\n" mtl.name
    format "=============================================\n"

    try ( select sph ) catch ( 0 )
    try ( max zoomext sel all ) catch ( 0 )
    format "完成。\n"
)
'''


def build_mscript(
    material_name: str,
    vray_props: dict,
    tex_paths: dict,
    matlib_path: str = "",
    auto_assign: bool = True,
    logfile_path: str = "",
) -> str:
    """生成完整的 .ms 脚本字符串

    vray_props : resolve_vray() 的输出，特殊键 __NORMAL__ / __HEIGHT__ 等为贴图占位
    tex_paths  : {"albedo": "...", "normal": "...", ...}
    auto_assign: 有选中物体时是否自动赋材质（True = 新手友好，导完即见效果）
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
        tgt = PROP_TARGETS.get(str(k))
        if tgt is not None:
            return tgt[0]
        return (str(k),)

    def _norm_value(k: str, v: Any) -> Any:
        """按语义把值转成 V-Ray 侧应有的形态"""
        tgt = PROP_TARGETS.get(str(k))
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
        for f in PROP_FOLLOW.get(str(k), ()):
            out.append(f'    mfAssign mtl #("{f}") {_ms_value(_norm_value(k, v))}')
        return out

    # 依赖开关必须先写：数值参数只有在其使能开关打开后才会被 V-Ray 采信
    emitted: set[str] = set()
    for k in scalars:
        for sw_name, sw_val in PROP_ON_SWITCH.get(str(k), {}).items():
            if sw_name in emitted:
                continue
            emitted.add(sw_name)
            lines.append(_assign_line(sw_name, sw_val))

    for k, v in scalars.items():
        if str(k) in PROP_NO_SCALAR:
            lines.append(f'    mfLog "  [INFO] {k}：{PROP_NO_SCALAR[str(k)]}"')
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
        ex = TEX_SLOTS_EXACT.get(kws[0], [])
        kw_str = ", ".join(f'"{k}"' for k in kws)
        ex_str = ", ".join(f'"{k}"' for k in ex)
        return f"    mfAttachTex mtl #({ex_str}) #({kw_str}) {_ms_escape(path)}"

    # 法线不走 slot_map：统一由 __NORMALWRAP__ / __NORMALBUMP__ 的 VRayNormalMap 分支处理，
    # 保证 VRayMtl 的凹凸槽里只出现高度图
    slot_map = {
        "__DIFFUSE__":   (["diffuse"],      "albedo"),
        "__HEIGHT__":    (["bump"],         "height"),
        "__ROUGHNESS__": (["roughness"],    "roughness"),
        "__DISPLACE__":  (["displacement"], "height"),
        "__OPACITY__":   (["opacity"],      "opacity"),
        "__DISP__":      (["displacement"], "height"),
    }

    # 兼容旧数据/直传：__NORMAL__ 的语义是「法线图」，一律转成 VRayNormalMap 包装，
    # 不再直挂 VRayMtl.bump（凹凸槽只允许挂高度图）
    if "__NORMAL__" in tex_specs:
        tex_specs.pop("__NORMAL__", None)
        tex_specs["__NORMALWRAP__"] = True

    # 法线 + 凹凸叠加：合成节点优先，缺高度图则退化为「仅法线包装」，bump 子槽留空
    if "__NORMALBUMP__" in tex_specs:
        n_p = tex_paths.get("normal")
        h_p = tex_paths.get("height")
        if n_p and h_p:
            tex_lines.append(
                f"    mfAttachNormalBump mtl {_ms_escape(n_p)} {_ms_escape(h_p)}")
        elif n_p:
            tex_lines.append(
                f"    mfAttachNormalBump mtl {_ms_escape(n_p)} undefined")
        tex_specs.pop("__NORMALBUMP__", None)

    # 仅法线（没有高度图）：同样包一层 VRayNormalMap，bump 子槽留空，
    # 保证凹凸槽只挂高度图，法线不再被直挂到 VRayMtl.bump
    if "__NORMALWRAP__" in tex_specs:
        n_p = tex_paths.get("normal")
        if n_p:
            tex_lines.append(
                f"    mfAttachNormalBump mtl {_ms_escape(n_p)} undefined")
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

    # AO：V-Ray 下用 VRayDirt 叠加，原漫反射位图保留在 unoccludedColor
    ao_path = tex_paths.get("ao")
    if vray_props.get("__activate_ao") and ao_path:
        ao_esc = _ms_escape(ao_path)
        tex_lines.append(f'''    (
        -- AO 叠加：仅 V-Ray 材质 + VRayDirt 可用时执行
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
            mfSetOn dirt #("occludedColor", "遮挡颜色") (color 30 30 30)
            if baseBm != undefined then (
                if not (mfSetOn dirt #("unoccludedColor", "未遮挡颜色") baseBm) do
                    mfSetOn dirt #("unoccludedColor", "未遮挡颜色") (color 255 255 255)
            ) else (
                mfSetOn dirt #("unoccludedColor", "未遮挡颜色") (color 255 255 255)
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

    out = _TEMPLATE
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
    out = out.replace("@@AUTOASSIGN@@", "true" if auto_assign else "false")
    out = out.replace("@@TEXLIST@@", texlist)
    out = out.replace("@@MATLIB@@", matlib)
    out = out.replace("@@LOGFILE@@", _ms_escape(logfile_path) if logfile_path else '""')
    out = out.replace("@@ASSIGNMENTS@@", assignments)
    out = out.replace("@@TEXTURES@@", textures)
    return out


def export(material_name: str, vray_props: dict, tex_paths: dict,
           out_dir: str, matlib_path: str = "", auto_assign: bool = True,
           pack_textures: bool = True, logfile_path: str = "") -> str:
    """导出 .ms 文件，返回路径

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

    script = build_mscript(material_name, vray_props, tex_paths, matlib_path, auto_assign, logfile_path)
    try:
        with open(path, "w", encoding="gbk", newline="\r\n") as f:
            f.write(script)
    except UnicodeEncodeError:
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(script)
    return path


if __name__ == "__main__":
    import json
    from core.params import default_values, resolve_vray

    props = resolve_vray({**default_values(), "normal_on": True, "ao_on": True})
    print(json.dumps(props, ensure_ascii=False, indent=2))
