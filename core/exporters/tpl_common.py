# -*- coding: utf-8 -*-
"""渲染器无关的 .ms 公共片段（V-Ray / Corona 共用，单一事实来源）

为什么要拆：
    Corona 与 V-Ray 的差异只在「材质怎么建、属性叫什么、法线/AO 用什么类」这三处；
    槽位判空、材质编辑器落槽、贴图路径解析、日志落盘、UV 处理、置换修改器、
    自动赋值、自诊断这些 280+ 行逻辑两个渲染器完全一样。复制一份必然漂移，
    所以这里只保留一份，两个 exporter 各自拼自己的 body。

拼接顺序（与 0.8.4 的 _TEMPLATE 逐字节等价）：
    HEAD + FUNCS + <渲染器专属函数> + ENV + <渲染器专属创建> + MID + TAIL
"""

HEAD = r"""/* ==================================================================
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
    global mf_BAD  = 0     -- 槽位写入/回读校验失败计数（P1 阻断用，>0 视为不可信）
    global mf_DEGRADED = 0 -- 版本能力降级计数（P5/P6/P7）：目标版本根本没有该能力，
                           -- 属于「诚实的降级」而不是「实现出错」，因此不计入 FAIL，
                           -- 但必须显式计数并写进日志，禁止静默降级成「看起来全绿」。

    local MF_TEXTURES = @@TEXLIST@@
    local MF_MATNAME  = "@@MATNAME@@"
    local MF_MATLIB   = @@MATLIB@@
    local MF_UV_TILE  = @@UVTILE@@          -- UV 平铺次数（U/V 同值）
    local MF_UV_ANGLE = @@UVANGLE@@          -- UV 旋转（度，对应坐标 W_Angle）
    local MF_NB_BUMP  = @@NBBUMP@@           -- 法线+凹凸叠加时内层凹凸倍率（=凹凸层强度）
    local MF_NB_NSTR  = @@NBNSTR@@           -- 法线层强度（叠加时勿被凹凸强度覆盖，P2）
    local MF_NB_BSTR  = @@NBBSTR@@           -- 凹凸层强度（P2，显式独立）
    local MF_STRICT   = @@STRICT@@           -- 严格模式：校验失败即计入 FAIL，日志全量扫描不可忽略
    local MF_DISP_ON  = @@DISPON@@           -- 是否给选中物体加置换修改器
    local MF_DISP_AMT = @@DISPAMT@@          -- 置换强度
    local MF_DISP_SUB = @@DISPSUB@@          -- 置换细分
    local MF_DISP_TEX = "@@DISPTEX@@"        -- 置换高度图
    local MF_AUTOASSIGN = @@AUTOASSIGN@@     -- 是否自动把材质赋给当前选中物体

    global MF_LOGFILE = @@LOGFILE@@      -- 空串表示不落盘；非空时同时写运行日志（实机排查/批量复盘用）
"""


FUNCS = r"""    fn mfLog s = (
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

    -- 写贴图槽 + **回读校验**（P1/P2 共用，2026-11-20 补）
    -- 为什么必须回读：此前 setProperty 抛错被 catch (0) 吞掉，日志照样打印「已挂载」，
    -- 探针回读到的是场景里残留的旧材质，于是链路其实断开而日志全绿 —— 这就是「假通过」。
    -- 本函数把「写 → 读回 → 比对对象身份」做成原子操作，任何一步不成立就显式记账。
    fn mfSetTexSlot obj slot bmp label = (
        if slot == undefined do (
            mfLog ("  [FAIL] " + label + "：未找到目标属性槽")
            mf_BAD += 1
            return false
        )
        local wrote = false
        try ( setProperty obj slot bmp; wrote = true ) catch (
            mfLog ("  [FAIL] " + label + "：写入抛出异常")
            mf_BAD += 1
            return false
        )
        local rb = undefined
        local got = false
        try ( rb = getProperty obj slot; got = true ) catch ( got = false )
        if (not got) do (
            mfLog ("  [FAIL] " + label + "：回读失败")
            mf_BAD += 1
            return false
        )
        if (rb != bmp) do (
            mfLog ("  [FAIL] " + label + "：回读对象与写入对象不一致（写入被静默忽略）")
            mf_BAD += 1
            return false
        )
        true
    )

    -- 写数值属性 + 回读校验（浮点按 1e-3 容差比较，避免渲染器内部做量化后被判失败）
    -- 版本能力降级记录（P5/P6/P7）：当目标版本（Max / V-Ray / Corona）根本没有
    -- 某个属性或接口时，「写不进去」不等于「代码写错」。把这种情况与实现失败区分开，
    -- 否则老版本上永远红一片，等于逼着使用者忽略日志——那才是真正的假通过。
    -- 规则：属性不存在 -> mfDegrade（计入 DEGRADED，建议性提示）；
    --       属性存在但写入/回读不一致 -> 仍走 mf_BAD（实现问题，必须红）。
    fn mfDegrade objName propName why = (
        mf_DEGRADED += 1
        mfLog ("  [CAP] " + objName + " 缺少 " + propName + "（" + why + "），已降级而非失败")
        true
    )

    fn mfSetNumChecked obj slot val label = (
        if slot == undefined do return false
        try ( setProperty obj slot val ) catch (
            mfLog ("  [WARN] " + label + "：数值属性写入异常")
            return false
        )
        local rb = undefined
        local got = false
        try ( rb = getProperty obj slot; got = true ) catch ( got = false )
        if (not got) do return false
        if ((abs ((rb as float) - (val as float))) > 0.001) do (
            mfLog ("  [WARN] " + label + "：写入 " + (val as string) + " 回读 " + (rb as string))
            return false
        )
        true
    )


    -- 与 mfFindTexSlot 的分工，2026-11-19 真机踩坑后补：
    --   mfHit 是「属性名必须同时包含 kws 里所有关键词」的 AND 语义，用来定位同一属性名的
    --   多词组合（如 #("base","rough") 命中 baseRoughnessTexmap）；
    --   但同义候选（normalMap / normal / inputTexmap 这类「同一个语义的几种写法」）必须按 OR
    --   逐个试。此前 mfAttachNormalBump 把同义候选塞进 mfHit，结果四个关键词要求同时包含，
    --   任何真实属性名都不可能满足 → 静默返回 undefined，真机表现为
    --   「Corona法线 节点建出来了但 normal=false，法线图根本没接到节点输入槽」。
    fn mfFindTexSlotAny obj cands = (
        local found = undefined
        -- 第一轮：候选名精确相等
        for k in cands while found == undefined do (
            local kn = toLower (k as string)
            for p in (getPropNames obj) while found == undefined do (
                if ((toLower (p as string)) == kn) do (
                    local cur = undefined
                    local got = false
                    try ( cur = getProperty obj p; got = true ) catch ( got = false )
                    if got do (
                        if cur == undefined then found = p
                        else if ((superClassOf cur) == textureMap) do found = p
                    )
                )
            )
        )
        -- 第二轮：候选名子串包含（旧版本 / 本地化变体兜底）
        if (found == undefined) do (
            for k in cands while found == undefined do (
                local kn = toLower (k as string)
                for p in (getPropNames obj) while found == undefined do (
                    if ((findString (toLower (p as string)) kn) != undefined) do (
                        local cur = undefined
                        local got = false
                        try ( cur = getProperty obj p; got = true ) catch ( got = false )
                        if got do (
                            if cur == undefined then found = p
                            else if ((superClassOf cur) == textureMap) do found = p
                        )
                    )
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

    -- Corona 物理材质的贴图槽是「贴图 + 开关」两件套：
    --   baseTexmap -> baseTexmapOn、baseRoughnessTexmap -> baseRoughnessTexmapOn，
    --   texmapOpacity -> texmapOnOpacity（On 夹在中间，是唯一不规则的一个）。
    -- 只挂贴图不置 On，渲染时该贴图根本不参与着色，用户只会看到一片纯色，
    -- 因此落槽后必须补置。非 Corona 材质上这些属性不存在，函数自动返回 false。
    fn mfTexmapOn mtl slot = (
        local done = false
        if ((findString (toLower ((classof mtl) as string)) "corona") != undefined) then (
            local sn = slot as string
            local cands = #()
            if ((substring sn 1 6) == "texmap") do append cands ("texmapOn" + (substring sn 7 -1))
            append cands (sn + "On")
            local pns = for p in (getPropNames mtl) collect (p as string)
            for c in cands while (not done) do (
                local hit = for p in pns where ((toLower p) == (toLower c)) collect p
                if hit.count > 0 do (
                    try (
                        setProperty mtl (hit[1] as name) true
                        done = true
                        mfLog ("  [OK] " + (hit[1] as string) + " = true")
                    ) catch ( done = false )
                )
            )
        )
        done
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
                try ( mfTexmapOn mtl slot ) catch ( 0 )
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

"""


ENV = r"""    -- ---------------- 0. 环境自检（不引用 VRayMtl 名字，避免变量未定义抛错）----------------
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
    -- Corona 同理：类名可能是 Corona / Corona_11 / CRNCorona 等，剥掉非字母数字后再匹配，
    -- 另外兼容 "CRN" 缩写写法（部分构建直接注册为 CRNCorona）。
    local mfIsCoronaRender = (((findString mfRenderNorm "corona") != undefined) or ((findString mfRenderNorm "crn") != undefined))
    mfLog ("[ENV] 3ds Max 版本号: " + mfMaxVer)
    mfLog ("[ENV] 当前渲染器: " + mfRender)
    mfLog ("[ENV] 渲染器判定: V-Ray=" + (mfIsVRayRender as string) + "  Corona=" + (mfIsCoronaRender as string))
    mfLog ("[ENV] 贴图目录策略: 优先脚本同级 textures/ ，回退绝对路径")

    -- 强制降级钩子：运行前把全局变量 mfForceDegrade 设为 true，
    -- 就能在「已装对应渲染器」的机器上验证降级分支（回归自检用），日常使用不必设置。
    local mfDegrade = false
    try ( mfDegrade = (mfForceDegrade == true) ) catch ( mfDegrade = false )
    if mfDegrade do (
        mfLog "**** [ENV] 已启用强制降级（mfForceDegrade=true），本次不创建目标渲染器材质 ****"
    )

"""


MID = r"""    -- ---------------- 2. 写入标量与颜色参数 ----------------
    mfLog "--- 参数写入 ---"
@@ASSIGNMENTS@@

    -- ---------------- 3. 挂接贴图 ----------------
    mfLog "--- 贴图挂接 ---"
@@TEXTURES@@

"""


TAIL = r"""    -- ---------------- 4. 创建材质球对象 ----------------
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
            -- 3ds Max 版本差异（P5）：2022 起移除了 saveTempMaterialLibrary 家族，
            -- 2021 及更早则相反。两代 API 必须双向兼容，否则「本机 2024 通过」
            -- 在 2021 上会直接走进 catch 变成静默跳过。
            local saved = false
            try (
                lib[mtl.name] = mtl
                saveMaterialLibrary lib
                saved = true
            ) catch ( saved = false )
            if (not saved) do (
                try (
                    saveTempMaterialLibrary lib
                    saved = true
                ) catch ( saved = false )
            )
            if saved then (
                mf_OK += 1
                mfLog ("  [OK] 材质库已保存: " + MF_MATLIB)
            ) else (
                mfLog ("  [SKIP] 材质库追加失败（两代 API 均不可用）: " + (getCurrentException()))
            )
        )
    )

    -- ---------------- 6.5 置换修改器（仅作用于当前选中物体）----------------
    if (MF_DISP_ON and MF_DISP_SUB > 0) do (
        if (selection.count == 0) then (
            mfLog "[WARN] 置换细分未生效：请先选中物体再执行脚本"
        ) else (
            for o in selection do (
                -- 置换修改器候选链（2026-11-19 真机 Modifier.classes 枚举实证）：
                --   Corona 真实注册名是本地化的「Corona置换修改器」，英文名已证伪（registry 的
                --   displacement_modifier_rejected 字段），在本机（Corona 12 + Max 2024 中文版）
                --   execute 英文名求值必然失败；中文标识符又不能拼进
                --   execute，所以必须退回类注册表字符串匹配后再实例化。
                --   V-Ray 仍是 VRayDisplacementMod，execute 可直接求值（真机 addModifier 通过）。
                local dm = undefined
                local dmKind = ""
                local dmHow = ""
                local dmCorona = mfIsCoronaRender
                local dmOrder = #("VRayDisplacementMod", "Corona置换修改器", "Displace")
                if dmCorona do dmOrder = #("Corona置换修改器", "VRayDisplacementMod", "Displace")
                for cn in dmOrder while dm == undefined do (
                    -- ① execute 求值：仅英文标识符可用（MAXScript 标识符不支持中文，
                    --   "Corona置换修改器()" 走这条路必然失败，靠 ② 兜底）
                    try ( dm = execute (cn + "()") ) catch ( dm = undefined )
                    if (dm != undefined) do dmHow = "execute"
                    -- ② 类注册表精确匹配后实例化；c() 为主，createInstance 为兜底
                    if (dm == undefined) do (
                        for c in Modifier.classes while dm == undefined do (
                            if ((toLower (c as string)) == (toLower cn)) do (
                                try ( dm = c() ) catch ( dm = undefined )
                                if (dm != undefined) then ( dmHow = "ctor" ) else (
                                    try ( dm = createInstance c ) catch ( dm = undefined )
                                    if (dm != undefined) do dmHow = "createInstance"
                                )
                            )
                        )
                    )
                    if (dm != undefined) do dmKind = cn
                )
                -- 真机事实（2026-11-19 probeC.log）：Corona置换修改器 可直接 c() 实例化、
                --   addModifier 通过，属性仅 10 项：texmap / waterLevel / levelMin / levelMax /
                --   useWaterLevel / mapSource / overrideSize / sizeType / screenSize / worldSize。
                --   没有 amount、没有 subdiv，强度概念只有 worldSize(世界尺寸) 与 waterLevel。

                if dm == undefined then (
                    mfLog "[WARN] 本机无可用置换修改器（VRayDisplacementMod / Corona置换修改器 / Displace），置换跳过"
                ) else (
                    try (
                        -- Corona 与 V-Ray 的置换参数模型完全不同，不能共用一套写值逻辑：
                        --   V-Ray  : amount(高度) + subdiv(细分) + texmap
                        --   Corona : texmap + mapSource + overrideSize + sizeType
                        --            + worldSize/screenSize + waterLevel/levelMin/levelMax
                        --            没有 amount、也没有 subdiv（置换质量由网格密度决定）
                        -- 此前统一按 amount/subdiv 关键字刷属性，Corona 下是 0 命中且静默当成功。
                        local isCoronaDm = false
                        if (dmCorona and (dmKind == "Corona置换修改器")) do isCoronaDm = true
                        if isCoronaDm then (
                            -- Corona 置换参数按**精确属性名**写，禁止再用 findString 模糊匹配：
                            --   "waterlevel" 会同时命中 useWaterLevel，把布尔开关覆写成 0.0；
                            --   "worldsize" 与 "screensize" 是互斥的两种尺寸模式，模糊匹配会两边都写。
                            -- sizeType / mapSource 保持插件默认（真机枚举为 sizeType=0 / mapSource=1），
                            --   语义未实测前不做猜测性覆盖。
                            local dmProps = for p in (getPropNames dm) collect (p as string)
                            if ((findItem dmProps "overrideSize") > 0) do ( try ( setProperty dm #overrideSize true ) catch ( 0 ) )
                            if ((findItem dmProps "worldSize") > 0) do ( try ( setProperty dm #worldSize MF_DISP_AMT ) catch ( 0 ) )
                            if ((findItem dmProps "useWaterLevel") > 0) do ( try ( setProperty dm #useWaterLevel false ) catch ( 0 ) )
                            if ((findItem dmProps "waterLevel") > 0) do ( try ( setProperty dm #waterLevel 0.5 ) catch ( 0 ) )
                            if ((findItem dmProps "levelMin") > 0) do ( try ( setProperty dm #levelMin 0.0 ) catch ( 0 ) )
                            if ((findItem dmProps "levelMax") > 0) do ( try ( setProperty dm #levelMax 1.0 ) catch ( 0 ) )
                            if (MF_DISP_TEX != "") do (
                                local ts = mfFindTexSlotAny dm #("texmap")
                                if ts != undefined do ( try ( setProperty dm ts (mfBitmap MF_DISP_TEX) ) catch ( 0 ) )
                            )
                            addModifier o dm
                            mfLog ("[OK] 置换修改器(" + dmKind + "/" + dmHow + ") -> " + o.name + "（" + (dmProps.count as string) + " 属性，worldSize " + (MF_DISP_AMT as string) + "；Corona 无细分参数，已忽略）")
                        ) else (
                            for p in (getPropNames dm) do (
                                local lp = toLower (p as string)
                                if ((findString lp "amount") != undefined) do ( try ( setProperty dm p MF_DISP_AMT ) catch ( 0 ) )
                                if ((findString lp "subdiv") != undefined) do ( try ( setProperty dm p MF_DISP_SUB ) catch ( 0 ) )
                            )
                            if (MF_DISP_TEX != "") do (
                                local ts = mfFindTexSlotAny dm #("texmap")
                                if ts != undefined do ( try ( setProperty dm ts (mfBitmap MF_DISP_TEX) ) catch ( 0 ) )
                            )
                            addModifier o dm
                            mfLog ("[OK] 置换修改器(" + dmKind + ") -> " + o.name + "（细分 " + (MF_DISP_SUB as string) + "）")
                        )
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

    -- ---------------- 6.9 现场版本探测（P3/P5/P6/P7） ----------------
@@COMPATPROBE@@

    -- ---------------- 7. 自诊断输出 ----------------
    format "\n========== Material Forge 执行结果 ==========\n"
    for s in mf_LOG do format "%\n" s
    format "---------------------------------------------\n"
    -- P1 阻断：把「槽位写入/回读校验失败」单独成行输出，并以固定标记 VERDICT 收尾。
    -- 判据在 Python 侧（scripts/verify_probe_log.py），只有同时满足
    --   VERDICT=OK 且 SCRIPT_RUN=1 且 BIND_MISMATCH=0 且 BAD=0 且无 [FAIL]
    -- 才允许认为真机链路成立。此前只 grep "成功: N 失败: 0" 就放行，
    -- 而 FAIL 计数根本不含「属性写入被静默忽略」这类问题，于是全绿也白搭。
    format "槽位校验失败: %\n" mf_BAD
    format "版本能力降级: %\n" mf_DEGRADED
    format "严格模式: %\n" (MF_STRICT as string)
    local verdict = "OK"
    if (mf_FAIL > 0 or mf_BAD > 0) do verdict = "FAIL"
    if (not MF_STRICT) do verdict = "UNVERIFIED"
    -- DEGRADED：脚本本身跑通、校验无失败，但目标版本缺少某能力（如旧版 Corona 无
    -- additionalBump、旧版 Max 无 saveMaterialLibrary）。这类结果不能报 OK，也不能报
    -- FAIL——报 OK 等于宣称"该版本功能完整"，报 FAIL 会把版本差异误判成实现缺陷。
    if (verdict == "OK" and mf_DEGRADED > 0) do verdict = "DEGRADED"
    format "VERDICT=%\n" verdict
    format "成功: %   失败: %\n" mf_OK mf_FAIL
    format "材质类型: %\n" (classof mtl)
    format "材质名称: %\n" mtl.name
    format "=============================================\n"

    try ( select sph ) catch ( 0 )
    try ( max zoomext sel all ) catch ( 0 )
    format "完成。\n"
)"""

