# -*- coding: utf-8 -*-
"""Corona 专属模板片段：类名候选链 + 材质创建 + 属性探针

与 V-Ray 的三处本质差异（决定了这里不能直接复用 tpl_vray）：
    1. 材质类：CoronaPhysicalMtl（Corona 7+ 物理材质）优先，CoronaLegacyMtl(旧 CoronaMtl)
       降级 —— 两者的属性名完全不同，候选链必须按「先物理后 Legacy」的顺序各自命中。
    2. 粗糙度语义相反：V-Ray 用 glossiness（0=粗糙），Corona 用 roughness（0=光滑）。
       数值转换在 Python 侧（params.resolve_corona）完成，脚本只负责写。
    3. 法线要走 Corona法线 节点、置换要走 Corona置换修改器（真机注册名是中文，英文名已证伪）。

类名为什么用 execute 候选链而不是直接 new：
    Corona 的类注册名在不同大版本 / 不同语言包里出现过 CoronaPhysicalMtl_Fake / _CoronaPhysicalMtl
    这类变体，直接 new 一个不存在的类会抛错中断整个脚本。

属性探针（mfProbeMtl）的存在理由：
    当前开发机未安装 Corona，属性名为「文档 + 社区实测」推断，无法真机核对。
    真机跑一次即可在 Listener / 日志里看到材质的真实属性名，回传后收紧 CORONA_PROP_TARGETS。
    这不是调试残留，是跨设备验证的唯一通道，保留到真机核对完成为止。
"""

CORONA_FNS = r"""    -- ---------------- Corona 类名解析（候选链，全部 try 包裹）----------------
    -- Corona 的类名在中文版会被本地化（真机实测：物理材质显示为「_Corona物理材质」，
    -- 直接用英文 CoronaPhysicalMtl() 会失败），所以全部类名走「英文名 → 中文名 → 属性特征扫描」
    -- 三级候选链，不硬编码任何单一名字。
    -- AO（污垢）：Corona 的 AO 是独立贴图节点 CoronaAO，
    -- 与 V-Ray 的污垢纹理（中文版类名 VRay_污垢）用法一致（作为普通 textureMap 挂到凹凸/自发光槽）。
    -- ---------------- Corona 材质类解析（真机核对后重写）----------------
    -- 真机核对结论（本机 Corona + 3ds Max 2024 中文版，Material.classes 枚举 + 实例化验证）：
    --   1) 中文版 classof 显示名被本地化为「_Corona物理材质」，直接 execute "CoronaPhysicalMtl()"
    --      **会失败**——这与 V-Ray 的中文类名问题同源，Corona 并非「英文类名不用管」。
    --   2) 类名文本随语言包变化，硬编码任何单一名字都注定踩坑。
    -- 因此：先试英文/中文候选名，全部失败后按**属性特征**扫描 Material.classes——
    -- 同时具备 baseRoughness 与 metalnessMode 的材质类只有 Corona 物理材质，与类名无关。
    fn mfCoronaPhysicalMtl = (
        local cands = #("CoronaPhysicalMtl", "_Corona物理材质", "Corona物理材质", \
                        "_CoronaPhysicalMtl", "CoronaPhysical")
        for n in cands do (
            local inst = undefined
            try ( inst = execute (n + "()") ) catch ( inst = undefined )
            if inst != undefined do (
                local pns = for p in (getPropNames inst) collect (toLower (p as string))
                if (((findItem pns "baseroughness") > 0) and ((findItem pns "metalnessmode") > 0)) do return inst
            )
        )
        for c in Material.classes do (
            local inst = undefined
            try ( inst = c() ) catch ( inst = undefined )
            if inst != undefined do (
                local pns = for p in (getPropNames inst) collect (toLower (p as string))
                if (((findItem pns "baseroughness") > 0) and ((findItem pns "metalnessmode") > 0)) do return inst
            )
        )
        undefined
    )

    -- 旧版材质：CoronaLegacyMtl（6 及更早叫 CoronaMtl），中文界面显示名「Corona传统材质」。
    -- 特征：colorDiffuse + glossiness（与物理材质的 baseColor + baseRoughness 明显不同）。
    fn mfCoronaLegacyMtl = (
        local cands = #("CoronaLegacyMtl", "Corona传统材质", "CoronaMtl", "_CoronaLegacyMtl", "_CoronaMtl")
        for n in cands do (
            local inst = undefined
            try ( inst = execute (n + "()") ) catch ( inst = undefined )
            if inst != undefined do (
                local pns = for p in (getPropNames inst) collect (toLower (p as string))
                if (((findItem pns "colordiffuse") > 0) and ((findItem pns "glossiness") > 0)) do return inst
            )
        )
        undefined
    )

    -- 优先物理材质，退化旧版材质
    fn mfCoronaMtl = (
        local m = mfCoronaPhysicalMtl()
        if m == undefined do m = mfCoronaLegacyMtl()
        m
    )

    fn mfCoronaDirtClass = (
        local cands = #("CoronaAO", "Corona_AO", "_CoronaAO")
        for n in cands do (
            local c = undefined
            try ( c = execute n ) catch ( c = undefined )
            if c != undefined do return c
        )
        for c in textureMap.classes do (
            local s = c as string
            local sl = toLower s
            local isCorona = ((findString sl "corona") != undefined)
            local isAo = (((findString sl "ao") != undefined) or ((findString sl "dirt") != undefined) or ((findString sl "occlusion") != undefined))
            if (isCorona and isAo) do (
                local instOk = false
                try ( local o = c(); instOk = true ) catch ( instOk = false )
                if instOk do return c
            )
        )
        undefined
    )

    -- 法线：Corona 官方要求法线贴图经 Corona法线 节点包装
    --（直接挂 bitmap 会按 sRGB 解读法线 RGB，导致表面发灰、凹凸发平）。
    fn mfNormalWrapClass = (
        -- 2026-11-19 真机（Corona 12 + Max 2024 中文版）TextureMap 枚举实证：
        --   法线包装器没有英文注册名（英文名 execute 一律落空），真实注册名是本地化的
        --   "Corona法线"；"Corona法线" 已写进 core/ondevice_registry.json，
        --   而英文写法进了同文件的 normal_map_class_rejected（证伪清单）。
        -- 中文标识符不能靠 execute 拼字符串求值（MaxScript 只接受 ASCII 标识符），
        -- 所以先按「类注册表里的字符串名」精确比对 textureMap.classes，最后再语义扫描兜底。
        local cands = #("Corona法线", "Corona_法线")
        for n in cands do (
            for c in textureMap.classes where ((c as string) == n) do (
                local ok = false
                try ( local o = c(); ok = true ) catch ( ok = false )
                if ok do return c
            )
        )
        for n in cands do (
            local c = undefined
            try ( c = execute n ) catch ( c = undefined )
            if c != undefined do return c
        )
        for c in textureMap.classes do (
            local s = c as string
            local sl = toLower s
            local isCorona = ((findString sl "corona") != undefined)
            local isNormal = ((findString s "法线") != undefined or (findString sl "normal") != undefined)
            local isBumpish = ((findString s "凹凸") != undefined or (findString sl "bump2normal") != undefined)
            if (isCorona and isNormal and (not isBumpish)) do (
                local instOk = false
                try ( local o = c(); instOk = true ) catch ( instOk = false )
                if instOk do return c
            )
        )
        undefined
    )

    -- 法线(+可选凹凸) -> Corona法线 -> mtl.texmapBump
    -- 2026-11-19 真机（Corona 12 + Max 2024 中文版）getPropNames 实证，Corona法线 节点仅 10 项：
    --   multiplier(强度) / normalMap(主输入槽) / addGamma / flipred / flipgreen / swapRedGreen /
    --   gammaWarning / additionalBump(附加凹凸输入) / additionalBumpOn / additionalBumpStrength
    -- 注意 normalMap 与 additionalBump 都是「输入槽」，且都不能用 mfHit 的 AND 语义定位
    -- （四个同义关键词不可能同时出现在一个属性名里），必须走 mfFindTexSlotAny 的 OR 语义。
    fn mfAttachNormalBump mtl nf bf nstr bstr = (
        local combo = undefined
        local comboClass = mfNormalWrapClass()
        if comboClass != undefined do (
            try ( combo = comboClass() ) catch ( combo = undefined )
        )
        if combo == undefined then (
            mfDegrade "Corona" "Normal 节点" "未安装 Corona 或版本过低，降级为单层 bump（可能出现发灰）"
            mfAttachTex mtl #("baseBumpTexmap", "texmapBump", "texmapBumpMap") #("bump") nf
        ) else (
            local okN = false
            local okB = false
            local ns = mfFindTexSlotAny combo #("normalMap", "normal", "inputTexmap", "texmapNormal", "mapNormal")
            if ns != undefined do (
                okN = (mfSetTexSlot combo ns (mfBitmap nf) "法线层")
            )
            -- P2 关键修复（2026-11-20）：法线+凹凸叠加在 Corona 上是**三个属性**才能成立：
            --   additionalBump(贴图) + additionalBumpOn(开关) + additionalBumpStrength(强度)
            -- 旧实现只写了贴图，开关保持默认 false，真机表现为「凹凸图挂上了但完全没效果」，
            -- 而日志因为只校验 setProperty 不抛错，照样打印「叠加已挂载」。这是典型的
            -- 「写入成功 ≠ 生效」误判，现在开关与强度都必须写并回读校验。
            local okOn = false
            if bf != undefined do (
                local bs = mfFindTexSlotAny combo #("additionalBump", "bumpMap", "bump")
                if bs != undefined do (
                    okB = (mfSetTexSlot combo bs (mfBitmap bf) "凹凸叠加层")
                )
                if okB do (
                    -- 开关：优先精确名 additionalBumpOn，兼容布尔型候选
                    local sw = undefined
                    for cand in #("additionalBumpOn", "additionalBumpEnable", "additionalBumpEnabled") while sw == undefined do (
                        for p in (getPropNames combo) while sw == undefined do (
                            if ((toLower (p as string)) == (toLower cand)) do sw = p
                        )
                    )
                    if sw != undefined then (
                        try ( setProperty combo sw true; okOn = true ) catch ( okOn = false )
                        if (not okOn) do (
                            mfLog "  [FAIL] 凹凸叠加层：additionalBumpOn 开关置位失败，叠加不会生效"
                            mf_BAD += 1
                        )
                        -- 回读确认开关真的变成 true
                        if okOn do (
                            local rbon = undefined
                            local gotOn = false
                            try ( rbon = getProperty combo sw; gotOn = true ) catch ( gotOn = false )
                            if (not gotOn or rbon != true) do (
                                mfLog "  [FAIL] 凹凸叠加层：additionalBumpOn 回读非 true，叠加不会生效"
                                mf_BAD += 1
                                okOn = false
                            )
                        )
                    ) else (
                        -- 版本能力缺失而非实现错误：Corona 9 及更早版本没有附加凹凸开关，
                        -- 这里记降级（DEGRADED），不记 BAD，否则老版本永远判红。
                        mfDegrade "Corona Normal" "additionalBumpOn" "Corona 9 及更早版本没有附加凹凸开关，叠加层无法启用"
                    )
                    -- 凹凸层强度：additionalBumpStrength（不是 multiplier！multiplier 是法线强度）
                    local bstrProp = undefined
                    for cand in #("additionalBumpStrength", "additionalBumpAmount") while bstrProp == undefined do (
                        for p in (getPropNames combo) while bstrProp == undefined do (
                            if ((toLower (p as string)) == (toLower cand)) do bstrProp = p
                        )
                    )
                    if bstrProp != undefined then (
                        if (not (mfSetNumChecked combo bstrProp bstr "凹凸层强度")) do
                            mfLog ("  [WARN] 凹凸层强度未生效（目标值 " + (bstr as string) + "）")
                    ) else (
                        mfDegrade "Corona Normal" "additionalBumpStrength" "旧版 Corona 无附加凹凸强度属性，强度按默认"
                    )
                )
            )
            -- 法线层强度 -> multiplier（精确名，绝不用 "mult" 子串，否则会误伤
            -- additionalBumpStrength 那种不含 mult 的属性、又会把 multiplier 当凹凸强度用）
            if (nstr > 0.0) do (
                local multProp = undefined
                for p in (getPropNames combo) while multProp == undefined do (
                    if ((toLower (p as string)) == "multiplier") do multProp = p
                )
                if multProp != undefined then (
                    if (not (mfSetNumChecked combo multProp nstr "法线层强度")) do
                        mfLog ("  [WARN] 法线层强度未生效（目标值 " + (nstr as string) + "）")
                ) else (
                    mfLog "  [WARN] Corona Normal 上未找到 multiplier，法线层强度保持默认"
                )
            )
            local slot = mfFindSlotEx mtl #("baseBumpTexmap", "texmapBump", "texmapBumpMap", "bumpMap") #("bump")
            if slot != undefined then (
                try (
                    setProperty mtl slot combo
                    try ( mfTexmapOn mtl slot ) catch ( 0 )
                    mf_OK += 1
                    local tag = "法线+凹凸叠加"
                    if bf == undefined do tag = "仅法线(Corona法线 包装)"
                    local bsTxt = okB as string
                    if bf == undefined do bsTxt = "留空"
                    mfLog ("  [OK] " + tag + "已挂载 (normal=" + (okN as string) + " / bump=" + bsTxt + ")")
                    true
                ) catch (
                    mf_FAIL += 1
                    mfLog "  [FAIL] Corona法线 挂载失败"
                    false
                )
            ) else (
                mf_FAIL += 1
                mfLog "  [FAIL] 材质上未找到 bump 槽，法线节点跳过"
                false
            )
        )
    )

    -- 属性探针：把材质上与本次映射相关的真实属性名打进日志。
    -- 用途：当前开发机无 Corona 时，用户真机跑一次即可回传这份清单，据此收紧候选表。
    fn mfProbeMtl mtl = (
        local hits = #()
        for p in (getPropNames mtl) do (
            local pl = toLower (p as string)
            if (((findString pl "rough") != undefined) or ((findString pl "ior") != undefined) or \
                ((findString pl "metal") != undefined) or ((findString pl "bump") != undefined) or \
                ((findString pl "opacity") != undefined) or ((findString pl "basecolor") != undefined) or \
                ((findString pl "base") != undefined)) do append hits (p as string)
        )
        mfLog ("[PROBE] " + ((classof mtl) as string) + " 关键属性 " + (hits.count as string) + " 项")
        local line = "  [PROBE] "
        local shown = 0
        for h in hits while shown < 40 do ( line += h + " "; shown += 1 )
        if hits.count > shown do line += "...(共 " + (hits.count as string) + " 项，完整清单把全局变量 mfProbeMaterial 设为 true 后重跑)"
        mfLog line
        local dev = false
        try ( dev = (mfProbeMaterial == true) ) catch ( dev = false )
        if dev do (
            mfLog "  [PROBE] ===== 全量属性开始 ====="
            for p in (getPropNames mtl) do mfLog ("    [PROP] " + (p as string))
            mfLog "  [PROBE] ===== 全量属性结束 ====="
        )
        true
    )

    format "\n===== Material Forge (Corona) 开始 =====\n"
    format "材质名称: %\n" MF_MATNAME
    format "贴图数量: %\n" MF_TEXTURES.count

"""


CORONA_CREATE = r"""    -- ---------------- 1. 创建材质（Corona 物理材质优先，Legacy 降级）----------------
    local mfKind = ""
    local mtl = undefined
    if (not mfDegrade) do (
        -- 类名解析统一走 mfCoronaMtl()（英文名 → 中文本地化名 → 按属性特征扫描 Material.classes）。
        -- 真机确认：中文版直接 execute "CoronaPhysicalMtl()" 在本机会失败，必须走这条链。
        try ( mtl = mfCoronaMtl() ) catch ( mtl = undefined )
        if mtl != undefined do mfKind = (classof mtl) as string
    )
    if mtl != undefined then (
        mfLog ("[OK] " + mfKind + " 创建成功")
    ) else (
        mfLog "**** [WARN] Corona 材质不可用：Corona 未安装 / 未加载 / 未授权 ****"
        mfLog "****          已降级为 PhysicalMaterial 裸材质：不写入任何参数/贴图（后续每个 [SKIP] 都源于此），只得到一个同名空材质球 ****"
        mfLog "****          本产物与 Corona 材质不等价，也不会做任何还原补偿；需要正确效果请装好 Corona 后重新导出 ****"
        try ( mtl = PhysicalMaterial() ) catch ( mtl = undefined )
        if mtl == undefined do (
            try ( mtl = StandardMaterial() ) catch ( mtl = undefined )
        )
        try ( MF_MATNAME = MF_MATNAME + "_降级_" + ((classof mtl) as string) ) catch ( 0 )
    )
    if (mtl != undefined and mfKind != "" and not mfIsCoronaRender) do (
        mfLog "**** [WARN] 当前渲染器不是 Corona：材质已创建，但视口/渲染需切到 Corona 才能看到正确效果 ****"
        mfLog "****          切换方法：Render Setup (F10) > Renderer > Corona ****"
    )
    if mtl == undefined then (
        messageBox "无法创建任何材质（CoronaPhysicalMtl / CoronaLegacyMtl / PhysicalMaterial 均失败）" title:"Material Forge"
        throw "Material Forge: 材质创建失败"
    )
    mtl.name = MF_MATNAME
    try ( mfProbeMtl mtl ) catch ( 0 )
    -- V-Ray 专有、Corona 无对应的参数：显式报出，避免用户以为已经写进材质
    local mfDropped = "@@DROPPED@@"
    if mfDropped != "" do (
        mfLog ("  [INFO] 以下 V-Ray 专有参数在 Corona 下无对应，已跳过: " + mfDropped)
    )

"""
