# -*- coding: utf-8 -*-
"""V-Ray 专属模板片段：本地化类名解析 + 材质创建"""

VRAY_FNS = r"""    -- 污垢（AO）纹理类名同样本地化：中文版 V-Ray 6 实测为 "VRay_污垢"，
    -- 真机实证可用名是 VRay_污垢，首位必须优先它；英文字面量 VRayDirt / VRay_Dirt
    -- 在本机中文版均不存在（UndefinedClass），只作英文环境下的候选链末位降级。
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
        local cands = #("VRay_法线贴图", "VRayNormalMap", "VRay_法线")
        -- 第一轮：按类注册表字符串名精确比对（真机 classof 显示为 VRay_法线贴图）
        for n in cands do (
            for c in textureMap.classes where ((c as string) == n) do (
                local ok = false
                try ( local o = c(); ok = true ) catch ( ok = false )
                if ok do return c
            )
        )
        -- 第二轮：execute 英文标识符，终归可能据此创建成功
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

    fn mfAttachNormalBump mtl nf bf nstr bstr = (
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
            -- 真机实证（probeE 2026-11-19）：VRay_法线贴图 的内层法线输入槽属性名是
            -- normal_map，不是 normal。旧写法 mfFindTexSlot #("normal") 靠子串碰运气，
            -- 换成 mfFindTexSlotAny 精确优先，避免命中其它含 normal 的属性。
            local ns = mfFindTexSlotAny combo #("normal_map", "normalMap", "normal")
            if ns != undefined do (
                okN = (mfSetTexSlot combo ns (mfBitmap nf) "法线层")
            )
            if bf != undefined do (
                -- 内层凹凸槽：VRayNormalMap 用 bump_map / bump 两级候选，禁止用 AND 语义的
                -- mfFindTexSlot 硬匹配单个关键词，否则本地化属性名一改就静默落空。
                local bs = mfFindTexSlotAny combo #("bump_map", "bumpMap", "bump")
                if bs != undefined do (
                    okB = (mfSetTexSlot combo bs (mfBitmap bf) "凹凸叠加层")
                )
            )
            -- P2：强度分成两层独立下发，不再用一个 "mult" 子串循环把两者一锅端。
            --   · 法线层强度 -> 材质 bump_multiplier（整体凹凸量，由 exporter 的标量赋值落）
            --   · 凹凸层强度 -> VRayNormalMap.bump_multiplier（节点内层倍率，只该动凹凸）
            -- 旧写法对 combo 上所有含 mult 的属性统统写同一个值，若节点未来新增第二个
            -- mult 属性就会被连带改动，属于"看起来对、改一处伤两处"的隐患。
            if bf != undefined do (
                local bmp = undefined
                -- 真机取证（Max2024+V-Ray6.20.06）：VRayNormalMap 的凹凸倍率属性真名是
                -- bump_map_multiplier（带下划线），旧候选 #("bump_multiplier", ...) 全部落空，
                -- 结果法线贴图挂上了、凹凸强度却没下发，渲出来是平滑面 —— 目视 P4 就是这样
                -- 抓到"结构 OK 但效果不对"的假通过。真机名必须排在首位。
                for cand in #("bump_map_multiplier", "bump_multiplier", "bumpMultiplier", "bumpAmount") while bmp == undefined do (
                    for p in (getPropNames combo) while bmp == undefined do (
                        if ((toLower (p as string)) == (toLower cand)) do bmp = p
                    )
                )
                if bmp != undefined then (
                    if (not (mfSetNumChecked combo bmp bstr "凹凸层强度")) do
                        mfLog ("  [WARN] 凹凸层强度未生效（目标值 " + (bstr as string) + "）")
                ) else (
                    mfLog "  [WARN] VRayNormalMap 上未找到凹凸倍率属性，叠加层强度未下发"
                )
            )
            if (nstr > 0.0 and nstr != 1.0) do (
                -- 真机取证：VRayNormalMap 确实有独立法线强度属性 normal_map_multiplier，
                -- 此前"节点无独立法线强度属性、只能靠材质 bump_multiplier"的判断是错的，
                -- 导致法线层强度无法单独调。现在优先下发节点级倍率，材质级作为兜底。
                local nmp = undefined
                for cand in #("normal_map_multiplier", "normalMapMultiplier") while nmp == undefined do (
                    for p in (getPropNames combo) while nmp == undefined do (
                        if ((toLower (p as string)) == (toLower cand)) do nmp = p
                    )
                )
                if nmp != undefined then (
                    if (not (mfSetNumChecked combo nmp nstr "法线层强度")) do
                        mfLog ("  [WARN] 法线层强度未生效（目标值 " + (nstr as string) + "）")
                ) else (
                    mfLog ("  [INFO] 法线层强度 " + (nstr as string) + " 节点无倍率属性，由材质 bump_multiplier 承担")
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

"""


VRAY_CREATE = r"""    -- ---------------- 1. 创建材质 ----------------
    local isVray = false
    local mtl = undefined
    -- 跨语言（P3）：中文版 Max 的材质注册名可能被本地化成 VRay_材质，
    -- 直接 execute 英文名会静默失败并降级到 PhysicalMaterial（用户只会看到"效果不对"）。
    -- 因此先对 Material.classes 做字符串精确比对（中英候选统一登记在 core/compat.py），
    -- 类表命中就按类实例化，未命中再退回 execute 英文名。
    local vrayCls = undefined
    for cand in #("VRayMtl", "VRay_Mtl", "VRay_材质", "VRay材质") while vrayCls == undefined do (
        for c in Material.classes while vrayCls == undefined do (
            if ((c as string) == cand) do vrayCls = c
        )
    )
    if vrayCls != undefined then (
        try ( mtl = vrayCls(); isVray = true ) catch ( isVray = false )
        if isVray do mfLog ("  [OK] V-Ray 材质类命中: " + (vrayCls as string))
    ) else (
        try ( mtl = VRayMtl(); isVray = true ) catch ( isVray = false )
    )
    if isVray then ( mfLog "[OK] VRayMtl 创建成功" )
    else (
        mfLog "**** [WARN] VRayMtl 不可用：V-Ray 未安装 / 未加载 / 未授权 ****"
        mfLog "****          已降级为 PhysicalMaterial：VRayMtl 专属属性一个都不会写，改由 3.5 段按通用 PBR 近似重写 ****"
        mfLog "****          近似范围：基色 / 粗糙度 / 金属度 / 折射率 / 自发光 / 透明 / 漫反射与法线凹凸贴图，逐项结果见下方 [FALLBACK] 行与汇总计数 ****"
        mfLog "****          与 VRayMtl 不等价（无折射透射与色散、无涂膜/绒毛/SSS 分层、无置换材质槽）；需要正确效果请装好 V-Ray 后重新导出 ****"
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

"""

