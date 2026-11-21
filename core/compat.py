# -*- coding: utf-8 -*-
"""跨版本 / 跨语言 / 跨渲染器的兼容矩阵与运行时探测（P3 + P5 + P6 + P7 的事实来源）。

为什么单独一个模块：
    此前"支持哪些版本"只散落在文档与记忆里，代码里没有任何东西能证明它支持，
    也没有任何东西能在用户现场发现"我说支持 2026，但用户装的是 2026 且挂了"。
    本模块把三件事集中成机器可读事实：
      1. LANG_ALIASES —— 同一对象的英文注册名与中文本地化名映射；
      2. MATRIX       —— Max / V-Ray / Corona 各版本的适配等级（verified / supported / untested）；
      3. probe_ms()   —— 注入到生成脚本里的运行时探测段，把现场真实版本写进日志。

适配等级语义（不许含糊，文档与日志用同一套词）：
    verified  —— 有真机日志证据，且日志通过 verify_probe_log.py 判定
    supported —— 代码按该版本的属性差异做了分支，但本机无该版本，未取真机证据
    untested  —— 未做任何针对性适配，不做承诺

诚实原则：本机仅 3ds Max 2024 中文版 + V-Ray 6.20.06 + Corona 12，
所以除这三项外一律不允许标 verified。任何把 untested 写成"支持"的表述都是造假。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1) 类名 / 属性名语言别名（中文版 Max 会把注册名一起本地化）
#    key: 概念名；en: 英文注册名；zh: 中文版注册名
#    说明：模板里生成类名候选链时必须「先按 classes 字符串精确比对」，
#    不能靠 execute("英文名")，因为 MaxScript 只接受 ASCII 标识符，
#    中文注册名用 execute 拼串必然静默失败（历史踩坑，已写进记忆）。
# ---------------------------------------------------------------------------
LANG_ALIASES = {
    "vray_mtl":        {"en": ["VRayMtl", "VRay_Mtl"], "zh": ["VRay_材质", "VRayMtl"]},
    "vray_normalmap":  {"en": ["VRayNormalMap"],       "zh": ["VRay_法线贴图", "VRayNormalMap"]},
    "corona_mtl":      {"en": ["CoronaMtl", "CoronaPhysicalMtl"], "zh": ["_Corona物理材质", "Corona物理材质"]},
    "corona_normal":   {"en": ["CoronaNormal"],        "zh": ["Corona法线", "CoronaNormal"]},
    "corona_disp":     {"en": ["CoronaDisplacementMod"], "zh": ["Corona置换修改器", "CoronaDisplacementMod"]},
}

# ---------------------------------------------------------------------------
# 2) 版本矩阵
#    features: 该版本上已知的关键能力差异，供文档与代码分支引用
# ---------------------------------------------------------------------------
MATRIX = {
    "max": {
        "2021": {"level": "untested", "features": ["saveTempMaterialLibrary 仍存在（2022+ 移除）"]},
        "2022": {"level": "untested", "features": ["saveTempMaterialLibrary 已移除，必须用 saveMaterialLibrary"]},
        "2023": {"level": "untested", "features": ["bitmap 的 apply 参数行为微调"]},
        "2024": {"level": "verified", "features": ["本机真机基线：中文版，saveTempMaterialLibrary 已移除"],
                 "evidence": "docs/evidence/probe_max2024_vray62006_corona12.log（C1–C6 全 PASS，MAX_VERSION=26000 / MAX_LANG=zh / COMPAT_LEVEL=supported / BAD=0）"},
        "2025": {"level": "supported", "features": ["类注册表改为延迟注册，classes 字符串比对仍有效"]},
        "2026": {"level": "supported", "features": ["材质编辑器槽位访问路径未变，meditMaterials 仍可用"]},
    },
    "vray": {
        "5":  {"level": "untested", "features": ["无 coat_bump / 无 refraction_dispersion_on"]},
        "6":  {"level": "verified", "features": ["本机真机基线：6.20.06，coat_bump 与 refraction_dispersion 均可用"],
               "evidence": "同上探针日志：V-Ray 侧法线/凹凸叠加与色散真机回读 + docs/evidence 四张目视 PNG"},
        "7":  {"level": "supported", "features": ["属性名向后兼容；新增 thin film 不影响既有槽位"]},
    },
    "corona": {
        "9":  {"level": "untested", "features": ["Normal 节点无 additionalBump* 三件套"]},
        "10": {"level": "untested", "features": ["additionalBump 系列引入，属性名可能与本机不同"]},
        "11": {"level": "supported", "features": ["additionalBump[On/Strength] 齐备"]},
        "12": {"level": "verified", "features": ["本机真机基线：Corona 12，additionalBump 三属性齐备"],
               "evidence": "同上探针日志：Corona 侧 additionalBumpOn 回读为 true + docs/evidence 目视 PNG"},
        "13": {"level": "supported", "features": ["物理材质类名保持 _Corona物理材质"]},
    },
}

# 本机基线（用于文档生成与自检对照，不许随手改）
LOCAL_BASELINE = {"max": "2024", "max_lang": "zh", "vray": "6", "corona": "12"}


def levels() -> dict:
    """返回 {产品: {版本: level}}，供文档与检查脚本共用。"""
    return {prod: {v: d["level"] for v, d in sorted(vers.items())}
            for prod, vers in MATRIX.items()}


def verified_count() -> int:
    return sum(1 for vers in MATRIX.values() for d in vers.values() if d["level"] == "verified")


# ---------------------------------------------------------------------------
# 3) 运行时探测段（注入到生成脚本开头，把现场版本写进日志）
#    设计要点：探测本身绝不能抛异常，任何一项失败都必须输出 UNKNOWN 而不是中断脚本。
#    日志格式固定，便于 verify_probe_log.py 与人工核对：
#        MAX_VERSION=26000 / MAX_LANG=zh / ACTIVE_RENDERER=V-Ray / VRAY_VER=62006
#        CORONA_VER=120000 / COMPAT_LEVEL=verified|supported|untested|unknown
# ---------------------------------------------------------------------------
def probe_ms() -> str:
    return r'''    -- ================= 运行时版本探测（P3/P5/P6/P7） =================
    -- 目的：让每一份真机日志自带"在哪台机器、哪个版本、哪种语言"的证据。
    -- 此前日志里没有版本信息，跨版本结论无法追溯，也无法判断某次 PASS 是否只对
    -- 单一版本成立。这里只读不写，探测失败一律记 UNKNOWN，绝不中断脚本。
    -- 注意：本段必须在 mfNormalMapClass 等函数定义**之后**调用（模板已保证）。
    fn mfCompatProbe = (
        local maxVer = 0
        try ( maxVer = (maxVersion())[1] ) catch ( maxVer = 0 )
        format "MAX_VERSION=%\n" (maxVer as string)
        -- 语言判定：中文版会把注册名一起本地化，所以"能不能在类表里找到中文名"
        -- 就是最可靠的判据；不要去猜 UI 语言，也不要用 maxVersion 代替。
        local lang = "en"
        try (
            local zhHit = false
            for c in TextureMap.classes while (not zhHit) do (
                local cs = c as string
                if ((findString cs "法线") != undefined or (findString cs "材质") != undefined) do zhHit = true
            )
            if zhHit do lang = "zh"
        ) catch ( lang = "unknown" )
        format "MAX_LANG=%\n" lang
        local rname = "unknown"
        try ( rname = (renderers.current as string) ) catch ( rname = "unknown" )
        format "ACTIVE_RENDERER=%\n" rname
        -- 可用性而不是版本号：MaxScript 没有稳定的跨渲染器版本查询 API，
        -- 硬编版本号会写出"看起来精确、其实编的"证据，比 UNKNOWN 更坏。
        local vok = "absent"
        try (
            for c in Material.classes while (vok != "present") do (
                if ((findString (c as string) "V-Ray") != undefined or (findString (c as string) "VRay") != undefined) do vok = "present"
            )
        ) catch ( vok = "UNKNOWN" )
        format "VRAY_AVAILABLE=%\n" vok
        local cok = "absent"
        try (
            for c in TextureMap.classes while (cok != "present") do (
                if ((findString (c as string) "Corona") != undefined) do cok = "present"
            )
        ) catch ( cok = "UNKNOWN" )
        format "CORONA_AVAILABLE=%\n" cok
        -- 能力等级：直接查类表里有没有法线贴图类，不引用模板内的函数名。
        -- 为什么不用 mfNormalMapClass()：V-Ray 与 Corona 模板各自实现同名函数，
        -- 在共享探测段里调用会因"当前模板没定义该函数"而抛错，跨渲染器就不成立。
        local lvl = "untested"
        try (
            local hit = false
            for c in TextureMap.classes while (not hit) do (
                local cs = c as string
                if ((findString cs "法线") != undefined or (findString cs "NormalMap") != undefined or (findString cs "Normal") != undefined) do hit = true
            )
            if hit do lvl = "supported"
        ) catch ( lvl = "untested" )
        format "COMPAT_LEVEL=%\n" lvl
        true
    )
    mfCompatProbe()
    -- ================================================================
'''

def probe_ms_logfn(log_fn: str = "mfP") -> str:
    """返回把版本证据写进"日志函数"的探测段。

    probe_ms() 用的是 format（只进 Listener）。探针脚本必须把 COMPAT_LEVEL /
    MAX_VERSION / MAX_LANG 落到日志文件里，否则 verify_probe_log.py --require-compat
    读不到这些行，真机日志会判 FAIL，跨版本取证无从谈起。
    """
    import re

    txt = probe_ms()
    # 逐行把 format "X=%\n" (expr) / format "X=%\n" ident 改写成 log_fn ("X=" + (expr))
    # 注意：源文件里是"反斜杠 + n"两个字面字符，必须用 re.escape 拼，不能写 \\n（那是换行符）
    # 表达式有的是 (maxVer as string)，有的直接是标识符 lvl / rname，两种都要吃下
    pat = re.compile(
        r'^\s*format "([A-Z_0-9]+)=%' + re.escape("\\n") + r'" (.+?)\s*$', re.M
    )

    def _repl(m):
        name, expr = m.group(1), m.group(2)
        if not (expr.startswith("(") and expr.endswith(")")):
            expr = "(" + expr + ")"
        return '%s ("%s=" + %s)' % (log_fn, name, expr)
    out, n = pat.subn(_repl, txt)
    if n < 5:
        raise RuntimeError("compat 探测段改写失败：只改了 %d 行，格式可能已变" % n)
    return out
