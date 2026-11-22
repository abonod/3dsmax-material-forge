-- ==================================================================
-- mf_max_listener.ms — 3ds Max 端「一键送进 Max」监听器
--
-- 装一次，之后产品里点「送进 Max」，材质直接落到当前打开的场景里，不用手动
-- 找 .ms 再 Run Script。
--
-- 用法（任选其一）：
--   1) 菜单 Scripting > Run Script，选本文件；看到 [MF-BRIDGE] 已启动 即可
--   2) 把本文件拖进 3ds Max 视口
-- 关闭：
--   mfMaxBridgeStop()
--
-- 安全边界（与 core/max_bridge.py 对应，三条都不可省）：
--   * token：请求里的 token 必须与 inbox/token 完全一致，否则拒绝
--   * sha256：执行前重算脚本哈希，与请求里登记的不符就拒绝（防掉包）
--   * 只执行请求里点名的那个 .ms，不扫描目录里其他文件
--
-- 这里刻意不写任何自动下载/自动连网逻辑：监听器只读本地 inbox。
-- ==================================================================

global mfMaxBridgeTimer
global mfMaxBridgeDir
global mfMaxBridgeToken
global mfMaxBridgeBusy

-- 热重载保护（真机踩过）：重跑本脚本时上一轮的 timer 可能还在跑，它的 Tick 会在
-- 「函数已重定义一半」的空隙里调用还没定义好的函数，直接报
-- 「类型错误: 调用需要函数或类，得到的是 undefined」。
-- 所以先不重置 timer 变量，先把它停干净，再往下走。
if mfMaxBridgeTimer != undefined do (
    try ( mfMaxBridgeTimer.Stop() ) catch ()
    try ( dotNet.removeEventHandler mfMaxBridgeTimer "Tick" mfMBTick ) catch ()
)
mfMaxBridgeTimer = undefined
if mfMaxBridgeBusy == undefined do mfMaxBridgeBusy = false

fn mfMBLog msg = (
    format "[MF-BRIDGE] %\n" msg
)

fn mfMBReadAll f = (
    local s = ""
    try ( s = ((dotNetClass "System.IO.File").ReadAllText f) ) catch ( s = "" )
    s
)

fn mfMBField txt key = (
    -- 从 {"key": "value", ...} 里抠出字符串字段，避免为读两行 JSON 引入解析器
    local val = ""
    try (
        local re = dotNetClass "System.Text.RegularExpressions.Regex"
        local pat = "\"" + key + "\"\s*:\s*\"([^\"]*)\""
        local m = re.Match txt pat
        if m.Success do val = (m.Groups.Item 1).Value
    ) catch ( val = "" )
    val
)

fn mfMBSha256 path = (
    local out = ""
    local fs = undefined
    try (
        fs = (dotNetClass "System.IO.File").OpenRead path
        local sha = (dotNetClass "System.Security.Cryptography.SHA256").Create()
        local bytes = sha.ComputeHash fs
        -- 不能写 bytes[i].ToString "x2"：MAXScript 遍历 byte[] 时自动解包成 Integer，
        -- 拿不到 .ToString，整段会掉进 catch 返回空串（真机踩过，表现为「哈希不符」）。
        -- BitConverter 给 "AB-CD-..." 大写，去了横杠再转小写，才与 Python 侧一致。
        local hex = ""
        try ( hex = ((dotNetClass "System.BitConverter").ToString bytes) ) catch ( hex = "" )
        hex = substituteString hex "-" ""
        out = toLower hex
        sha.Dispose()
    ) catch ( out = "" )
    if fs != undefined do ( try ( fs.Close() ) catch () )
    out
)

fn mfMBEsc s = (
    -- 写回执前把反斜杠/引号/换行转义掉，否则 res.json 会变成非法 JSON
    local t = substituteString s "\\" "\\\\"
    t = substituteString t "\"" "\\\""
    t = substituteString t "\n" " "
    t = substituteString t "\r" ""
    t
)

fn mfMBPid = (
    -- 别用 processID：MAXScript 没有这个标识符，写进 JSON 会变成 "pid":undefined，
    -- 整个回执就成了非法 JSON，产品侧解析必然失败（真机踩过）。
    local p = "0"
    try (
        p = (((dotNetClass "System.Diagnostics.Process").GetCurrentProcess()).Id) as string
    ) catch ( p = "0" )
    p
)

fn mfMBWriteRes job ok msg = (
    local js = "{\"ok\":" + (if ok then "true" else "false") + \
               ",\"msg\":\"" + (mfMBEsc msg) + "\"" + \
               ",\"pid\":" + (mfMBPid()) + "}"
    try (
        (dotNetClass "System.IO.File").WriteAllText (mfMaxBridgeDir + job + ".res.json") js
    ) catch ( mfMBLog ("回执写入失败：" + getCurrentException()) )
)

fn mfMBHeartbeat = (
    local js = "{\"pid\":" + (mfMBPid()) + \
               ",\"ts\":" + ((timeStamp()) as string) + \
               ",\"ver\":\"" + "0.11.3" + "\"}"
    try (
        (dotNetClass "System.IO.File").WriteAllText (mfMaxBridgeDir + "listener.hb") js
    ) catch ()
)

fn mfMBTick sender args = (
    if mfMaxBridgeBusy do return()
    mfMaxBridgeBusy = true
    try (
        mfMBHeartbeat()
        local reqs = getFiles (mfMaxBridgeDir + "*.req.json")
        if reqs.count > 0 do (
            local f = reqs[1]
            local txt = mfMBReadAll f
            local tok = mfMBField txt "token"
            local target = mfMBField txt "ms_path"
            local want = mfMBField txt "sha256"
            local job = filenameFromPath f
            if job.count > 9 do job = substring job 1 (job.count - 9)

            if tok != mfMaxBridgeToken then (
                mfMBWriteRes job false "token 不匹配，拒绝执行"
                try ( deleteFile f ) catch ()
                mfMBLog "拒绝：token 不匹配"
            ) else if not (doesFileExist target) then (
                mfMBWriteRes job false ("找不到脚本：" + target)
                try ( deleteFile f ) catch ()
            ) else if (mfMBSha256 target) != want then (
                mfMBWriteRes job false "脚本内容与登记哈希不符（可能被改动），拒绝执行"
                try ( deleteFile f ) catch ()
                mfMBLog "拒绝：哈希不符"
            ) else (
                local ok = true
                local msg = "已执行"
                try ( fileIn target ) catch (
                    ok = false
                    msg = getCurrentException()
                )
                try ( deleteFile f ) catch ()
                mfMBWriteRes job ok msg
                mfMBLog (if ok then ("已执行 " + target) else ("执行失败：" + msg))
            )
        )
    ) catch (
        mfMBLog ("轮询异常：" + getCurrentException())
    )
    mfMaxBridgeBusy = false
)

fn mfMaxBridgeStop = (
    if mfMaxBridgeTimer != undefined do (
        try ( mfMaxBridgeTimer.Stop() ) catch ()
        try ( dotNet.removeEventHandler mfMaxBridgeTimer "Tick" mfMBTick ) catch ()
        mfMaxBridgeTimer = undefined
        mfMBLog "已停止"
    )
    true
)

fn mfMaxBridgeStart dir: = (
    mfMaxBridgeStop()
    local base = ""
    try (
        base = ((dotNetClass "System.Environment").GetFolderPath \
               ((dotNetClass "System.Environment+SpecialFolder").LocalApplicationData))
    ) catch ( base = "" )
    if base == "" do (
        mfMBLog "取不到 LocalApplicationData，需要手动指定 dir:"
        return false
    )
    mfMaxBridgeDir = if dir == unsupplied then (base + "\\MarvisMaterialForge\\max_inbox\\") else dir
    if mfMaxBridgeDir[mfMaxBridgeDir.count] != "\\" do mfMaxBridgeDir += "\\"
    makeDir mfMaxBridgeDir all:true

    local tokenFile = mfMaxBridgeDir + "token"
    if not (doesFileExist tokenFile) then (
        mfMBLog "inbox 里没有 token：请先在材质锻造工具里导出过一次，再运行本脚本"
        return false
    )
    mfMaxBridgeToken = mfMBReadAll tokenFile
    mfMaxBridgeToken = substituteString mfMaxBridgeToken "\r" ""
    mfMaxBridgeToken = substituteString mfMaxBridgeToken "\n" ""
    mfMaxBridgeToken = trimLeft mfMaxBridgeToken
    mfMaxBridgeToken = trimRight mfMaxBridgeToken

    mfMaxBridgeTimer = dotNetObject "System.Windows.Forms.Timer"
    mfMaxBridgeTimer.Interval = 400
    dotNet.addEventHandler mfMaxBridgeTimer "Tick" mfMBTick
    mfMaxBridgeTimer.Start()
    mfMBHeartbeat()
    mfMBLog ("已启动，监听目录：" + mfMaxBridgeDir)
    true
)

-- 载入即启动
mfMaxBridgeStart()
