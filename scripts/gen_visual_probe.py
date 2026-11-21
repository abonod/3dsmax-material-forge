#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""目视取证脚本生成器（P4：把「回读可信」升级为「效果可信」）。

为什么需要它：
    属性回读只能证明「值写进去了」，证明不了「渲染出来对不对」。
    色散（dispersion）写进 refraction_dispersion 后回读 0.9 是绿的，但渲染可能
    完全看不到彩虹边——原因可能是相机距离、光源角度、物体尺寸。
    法线+凹凸叠加同理：两个槽都回读成功，叠加后也可能互相吃掉细节。
    这类问题只有眼睛能判，所以必须生成「能一键渲出对比图」的脚本。

本版修复（2026-11-20 真机取证暴露，全部为真实缺陷）：
    D1 从不切换渲染器：脚本只在 Arnold 下渲染，Corona/V-Ray 材质根本不生效，
       渲出来必然是空图 —— 现在按 job 的渲染器切换 renderers.current，切不到就
       明确报 VISUAL_RENDERER_MISS 并跳过，绝不拿别的渲染器糊弄。
    D2 输出名只用 key：vray 与 corona 两版同名互相覆盖，4 个 job 最后只剩 2 张图，
       且内容一模一样 —— 现在文件名带渲染器后缀，并逐个校验文件体积。
    D3 不校验载入结果：fileIn 失败后仍拿第 1 槽残留材质渲染 —— 现在先埋哨兵、
       fileIn 后再验哨兵与材质类名，任一不符就判 LOADFAIL/CLASSMISMATCH。
    D4 不校验出图有效性：现在要求文件存在且体积 > 2000 字节，否则 RENDERFAIL。

产出：
    <out>/visual_probe.ms              真机 Run Script 即可产图
    <out>/visual_manifest.json         待目视清单（每项必须人眼看过才能填 passed=true）

运行期纪律：本脚本只生成文件，绝不启动 3ds Max。

用法：
    python scripts/gen_visual_probe.py --ms-dir out --out out/visual
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 目视项定义：key -> (材质文件匹配关键字, 说明, 必须看到的判据)
VISUAL_ITEMS = [
    {
        "key": "dispersion",
        "match": "dispersion",
        "title": "色散（Dispersion）",
        "expect": "高光边缘出现彩虹分离；立方体棱线附近色边明显；无色彩分离即判不合格",
    },
    {
        "key": "normal_bump",
        "match": "overlay",
        "title": "法线 + 凹凸双层叠加",
        "expect": "大尺度法线起伏与细粒凹凸纹理同时可见；只看到一层即判不合格",
    },
]

RENDERERS = [
    ("vray", "VRay"),
    ("corona", "Corona"),
]

MS_TEMPLATE = r'''-- ============ Material Forge 目视取证脚本（P4）============
-- 由 scripts/gen_visual_probe.py 生成，勿手改。
-- 用途：搭一个可判场景（彩色棋盘围盒 + 相机 + 主光/补光），按 job 指定渲染器渲 PNG，
--       供人眼判定色散 / 法线凹凸叠加是否真的生效。
--
-- 本版修复的真机缺陷（均为实测暴露，不是猜测）：
--   F1 渲染器枚举 API 写错：renderer 是全局 production 设置对象，上面没有 .classes，
--      必须用 RendererClass.classes，否则抛「未知属性:classes 位于 #production」。
--   F2 V-Ray 会被选中 GPU 版（V_Ray_GPU_6__update_2_1），本机 RX580 无 CUDA，
--      GPU 版渲不出正确结果 —— 现在 V-Ray 优先取类名不含 GPU 的 CPU 版。
--   F3 材质 ms 自己会 delete objects 并重建预览球，脚本先建的相机/灯光/围盒会一起被删，
--      导致渲染时相机不存在、画面只剩光点 —— 现在场景构建改成函数，在 fileIn 之后重建。
--   F4 纯色背景下玻璃球几乎隐形，色散无从判断 —— 现在加彩色棋盘围盒，折射才看得出彩边。
--   F5 输出名只用 key，vray/corona 两版互相覆盖 —— 现在文件名带渲染器后缀。
--   F6 不校验载入结果，fileIn 失败后仍拿残留材质渲染 —— 现在埋哨兵 + 验类名。
--   F7 Checker 是 2D 贴图无 w_tiling；Standard__Legacy 无 selfIllumMapOn，
--      可选属性一律 try 包裹，不让增强项打断整个取证脚本。
(
    global vLog = #()
    fn vSay s = ( append vLog s; format "%\n" s )

    -- 必须用原样字符串 @，否则路径里的 \t \n 会被当转义（真机实测：\temp 变成 TAB，渲染直接失败）
    local OUTDIR = @"@@OUTDIR@@"
    makeDir OUTDIR all:true

    fn mfIsTarget cn kw = (
        if kw == "corona" then (findString cn "Corona" != undefined)
        else ((findString cn "V_Ray" != undefined) or (findString cn "VRay" != undefined))
    )

    -- 切换渲染器，返回实际生效的类名；MISS 表示本机没装，SWITCHFAIL 表示切不过去
    fn mfSetRenderer kw = (
        local picked = undefined
        if kw == "vray" then (
            for c in RendererClass.classes do (
                local cn = c as string
                if (picked == undefined) and (mfIsTarget cn kw) and (findString cn "GPU" == undefined) do picked = c
            )
        )
        for c in RendererClass.classes do (
            if picked == undefined and (mfIsTarget (c as string) kw) do picked = c
        )
        if picked == undefined then "MISS" else (
            local nm = "SWITCHFAIL"
            try ( renderers.current = picked(); nm = (picked as string) ) catch ( nm = "SWITCHFAIL" )
            nm
        )
    )

    fn mfClassOk mtl kw = (
        mfIsTarget ((classof mtl) as string) kw
    )

    -- 彩色棋盘材质：玻璃折射/色散需要高频彩色内容才看得见
    fn mfEnvMaterial = (
        local chk = Checker()
        try ( chk.color1 = color 210 90 60 ) catch ( 0 )
        try ( chk.color2 = color 60 120 210 ) catch ( 0 )
        try ( chk.coordinates.u_tiling = 8 ) catch ( 0 )
        try ( chk.coordinates.v_tiling = 8 ) catch ( 0 )
        local m = StandardMaterial name:"MF_EnvMat" diffuse:(color 190 190 190) diffuseMap:chk
        try ( m.selfIllumAmount = 70 ) catch ( 0 )
        try ( m.selfIllumMap = chk ) catch ( 0 )
        try ( m.selfIllumMapOn = true ) catch ( 0 )
        m
    )

    -- 场景每次现搭：材质 ms 会 delete objects，提前建的全会被删掉（F3）
    fn mfMakeScene = (
        local envM = mfEnvMaterial()
        local S = 1200.0
        local pl = Plane length:S width:S pos:[0,0,-60] name:"MF_EnvFloor"
        pl.material = envM
        local pb = Plane length:S width:S pos:[0,S/2,0] name:"MF_EnvBack"
        pb.rotation = eulerangles 90 0 0
        pb.material = envM
        local pwx = Plane length:S width:S pos:[-S/2,0,0] name:"MF_EnvLeft"
        pwx.rotation = eulerangles 0 90 0
        pwx.material = envM
        local pe = Plane length:S width:S pos:[S/2,0,0] name:"MF_EnvRight"
        pe.rotation = eulerangles 0 -90 0
        pe.material = envM
        local pt = Plane length:S width:S pos:[0,0,S/2] name:"MF_EnvTop"
        pt.rotation = eulerangles 180 0 0
        pt.material = envM

        local cam = Freecamera pos:[150,-150,90] name:"MF_VisualCam"
        local tgt = Targetobject pos:[0,0,30]
        cam.target = tgt
        local k = OmniLight pos:[120,-80,160] multiplier:1.6 name:"MF_VisualKey"
        k.rgb = color 255 250 245
        local f = OmniLight pos:[-140,-40,110] multiplier:0.7 name:"MF_VisualFill"
        f.rgb = color 210 225 255
        backgroundColor = color 40 44 52
        #(cam, k, f)
    )

    -- 按主体包围盒取景，围盒(MF_Env*)不参与，否则相机被拉到 1200 单位外
    fn mfFrame cam k f = (
        local geos = for o in geometry where (findString o.name "MF_Env" == undefined) collect o
        if geos.count == 0 then false else (
            local mn = geos[1].min
            local mx = geos[1].max
            for o in geos do (
                mn = [amin mn.x o.min.x, amin mn.y o.min.y, amin mn.z o.min.z]
                mx = [amax mx.x o.max.x, amax mx.y o.max.y, amax mx.z o.max.z]
            )
            local c = (mn + mx) / 2.0
            local r = (length (mx - mn)) / 2.0
            if r < 1.0 do r = 45.0
            cam.pos = c + [r * 1.9, -r * 1.9, r * 1.2]
            cam.target.pos = c
            k.pos = c + [r * 1.6, -r * 1.3, r * 1.9]
            f.pos = c + [-r * 1.7, -r * 0.7, r * 1.2]
            true
        )
    )

    -- 渲图耗时主要压在采样上：720×540 一张要跑十几分钟，取证只要"看得出彩边/看得出颗粒"，
    -- 分辨率降到 480×360 并把采样压到最低档，单张进分钟级，才可能把 4 张跑完。
    renderWidth = @@WIDTH@@
    renderHeight = @@HEIGHT@@
    fn mfCheapenSamples = (
        -- 属性名各版本不一，全部 try 包裹，压不下去也只是慢，不会中断取证
        try ( renderers.current.dmc_minSubDivs = 1 ) catch ( 0 )
        try ( renderers.current.dmc_maxSubDivs = 4 ) catch ( 0 )
        try ( renderers.current.dmc_adaptiveThreshold = 0.1 ) catch ( 0 )
        try ( renderers.current.adaptiveSubdivision = true ) catch ( 0 )
        -- 真机实测的 V-Ray 6 属性名（progressive_maxSubdivs / dmc_minSubDivs 都是猜的，
        -- 一个都不存在，导致"降采样"整段 try 静默全落空，单张仍跑 15 分钟）
        try ( renderers.current.progressive_max_render_time = 2 ) catch ( 0 )
        try ( renderers.current.progressive_maxSamples = 24 ) catch ( 0 )
        try ( renderers.current.progressive_noise_threshold = 0.05 ) catch ( 0 )
        try ( renderers.current.adaptiveSubdivision_minRate = 1 ) catch ( 0 )
        try ( renderers.current.adaptiveSubdivision_maxRate = 4 ) catch ( 0 )
        try ( renderers.current.adaptiveSubdivision_threshold = 0.1 ) catch ( 0 )
        try ( renderers.current.options_gi_enabled = false ) catch ( 0 )
        -- Corona 用 passLimit/timeLimit 控时（真机属性名 progressive_passLimit / progressive_timeLimit）
        try ( renderers.current.progressive_passLimit = 16 ) catch ( 0 )
        try ( renderers.current.progressive_timeLimit = 90 ) catch ( 0 )
        0
    )
    mfCheapenSamples()

    local jobs = @@JOBS@@
    local total = 0
    local done = 0
    for j in jobs do (
        total += 1
        local rk = j[1]
        local f = j[2]
        local key = j[3]
        local png = OUTDIR + "\\" + key + "_" + rk + ".png"
        local rname = mfSetRenderer rk
        if (rname == "MISS" or rname == "SWITCHFAIL") then (
            vSay ("VISUAL_ITEM=" + key + "|" + rk + "|RENDERER_MISS|" + png + "|-")
        ) else (
            vSay ("VISUAL_RENDERER=" + rk + ":" + rname)
            -- 清空全部材质槽：不清则渲染回读可能命中上一 job 的残留材质（踩坑复现过）
            for i = 1 to 24 do ( try ( meditMaterials[i] = StandardMaterial() ) catch ( 0 ) )
            meditMaterials[1] = StandardMaterial name:"__MF_SENTINEL__"
            local okLoad = false
            try ( fileIn f; okLoad = true ) catch ( okLoad = false )
            local mtl = undefined
            local sentinel = true
            try ( mtl = meditMaterials[1]; sentinel = (mtl.name == "__MF_SENTINEL__") ) catch ( sentinel = true )
            if (not okLoad) then (
                vSay ("VISUAL_ITEM=" + key + "|" + rk + "|LOADFAIL|" + png + "|-")
            ) else if (sentinel) then (
                vSay ("VISUAL_ITEM=" + key + "|" + rk + "|NOTSLOTTED|" + png + "|-")
            ) else if (not (mfClassOk mtl rk)) then (
                vSay ("VISUAL_ITEM=" + key + "|" + rk + "|CLASSMISMATCH|" + png + "|" + (((classof mtl) as string)))
            ) else (
                -- 场景必须在 fileIn 之后搭（F3）；材质脚本自建的对象保留，只补环境/相机/灯
                local sc = mfMakeScene()
                local cam2 = sc[1]
                local geos = for o in geometry where (findString o.name "MF_Env" == undefined) collect o
                if geos.count == 0 do (
                    local b2 = Box length:120 width:120 height:120 pos:[0,0,60] name:"MF_VisualTarget"
                    b2.material = mtl
                )
                -- 材质脚本执行时会自己切渲染器，这里以 job 目标为准再切一次（F1/F2）
                local rname2 = mfSetRenderer rk
                if (rname2 == "MISS" or rname2 == "SWITCHFAIL") then (
                    vSay ("VISUAL_ITEM=" + key + "|" + rk + "|RENDERER_LOST|" + png + "|-")
                )
                local framed = mfFrame cam2 sc[2] sc[3]
                if (not framed) do vSay ("VISUAL_WARN=" + key + "_" + rk + ":FRAME_EMPTY")
                local okRender = false
                try (
                    local bmp = render camera:cam2 outputfile:png vfb:off
                    try ( close bmp ) catch ( 0 )
                    okRender = true
                ) catch (
                    okRender = false
                    vSay ("VISUAL_EXCEPTION=" + key + "_" + rk + ":" + (getCurrentException()))
                )
                local sz = 0
                if (doesFileExist png) do ( try ( sz = getFileSize png ) catch ( sz = 0 ) )
                if (okRender and sz > 2000) then (
                    done += 1
                    vSay ("VISUAL_ITEM=" + key + "|" + rk + "|OK|" + png + "|" + (((classof mtl) as string)))
                    vSay ("VISUAL_SIZE=" + key + "_" + rk + "=" + (sz as string))
                ) else (
                    vSay ("VISUAL_ITEM=" + key + "|" + rk + "|RENDERFAIL|" + png + "|" + (sz as string))
                )
            )
        )
    )
    vSay ("VISUAL_DONE=" + (done as string))
    vSay ("VISUAL_TOTAL=" + (total as string))
    vSay "VISUAL_SCRIPT_RUN=1"
    vSay "\n---- 目视判定提示 ----"
    vSay "色散：透过球体折射的格子边缘应有彩虹分离；无彩边判不合格。"
    vSay "法线凹凸：大尺度起伏与细颗粒应同时可见；只看到一层判不合格。"
    vSay "同一 key 的 vray / corona 两图若完全一致，说明材质未生效，直接判不合格。"
    vSay "结论请回填 visual_manifest.json，不要只看本日志。"
)
'''


def build(ms_dir: str, out_dir: str, only: str | None = None, key: str | None = None,
          width: int = 480, height: int = 360) -> tuple[str, list]:
    """生成目视脚本正文与期望项清单（key x 渲染器 全组合）。

    only 用于分批渲染：一次渲 4 张会让 Max 阻塞数分钟、MCP 管道直接断开
    （真机实测 RequestOutcomeUnknown），所以允许按渲染器分两批跑。
    """
    jobs = []
    items = []
    for rk, _hint in RENDERERS:
        if only and rk != only:
            continue
        sub = os.path.join(ms_dir, rk)
        if not os.path.isdir(sub):
            continue
        files = [f for f in sorted(os.listdir(sub)) if f.lower().endswith(".ms")]
        for it in VISUAL_ITEMS:
            if key and it["key"] != key:
                continue
            hit = next((f for f in files if it["match"] in f.lower()), None)
            if not hit:
                continue
            full = os.path.join(sub, hit)
            jobs.append((rk, full))
            items.append({
                "key": it["key"],
                "renderer": rk,
                "title": it["title"] + " / " + rk,
                "expect": it["expect"],
                "material_found": True,
                "material": full,
                "png": os.path.join(out_dir, "%s_%s.png" % (it["key"], rk)),
                "passed": None,
                "reviewed_by": "",
                "comment": "",
            })

    job_ms = ",\n        ".join(
        '#("%s", @"%s", "%s")' % (rk, f, next((i["key"] for i in VISUAL_ITEMS if i["match"] in f.lower()), "unknown"))
        for rk, f in jobs
    )
    ms = MS_TEMPLATE.replace("@@OUTDIR@@", out_dir)
    ms = ms.replace("@@WIDTH@@", str(int(width)))
    ms = ms.replace("@@HEIGHT@@", str(int(height)))
    ms = ms.replace("@@JOBS@@", "#(\n        " + job_ms + "\n    )" if jobs else "#()")
    return ms, items


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="生成目视取证脚本")
    ap.add_argument("--ms-dir", default="out", help="素材 ms 根目录（其下 vray/ corona/）")
    ap.add_argument("--out", default="out/visual", help="输出目录")
    ap.add_argument("--only", default=None, choices=["vray", "corona"],
                    help="只渲染指定渲染器的作业（分批跑，避免长时间阻塞）")
    ap.add_argument("--key", default=None, choices=["dispersion", "normal_bump"],
                    help="只渲染指定目视项（单张跑，隔离渲染崩溃）")
    ap.add_argument("--width", type=int, default=480, help="渲图宽（取证只需看清彩边/颗粒，默认 480）")
    ap.add_argument("--height", type=int, default=360, help="渲图高（默认 360）")
    ap.add_argument("--json", action="store_true", help="只打印清单 JSON")
    args = ap.parse_args(argv)

    ms, items = build(os.path.abspath(args.ms_dir), os.path.abspath(args.out), args.only, args.key,
                                args.width, args.height)
    manifest = {
        "generated_by": "scripts/gen_visual_probe.py",
        "note": "本清单每项必须人工看过 PNG 才能填 passed=true，禁止凭属性回读直接判定；"
                "同一 key 的 vray / corona 两图若完全一致，说明材质未生效，直接判不合格。",
        "items": items,
        "jobs_found": len(items),
    }
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    from pathlib import Path
    Path(os.path.abspath(args.out)).mkdir(parents=True, exist_ok=True)
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core import preflight as _pf

        errs = [i for i in _pf.check_script(ms) if i.get("level") == "ERROR"]
    except Exception as exc:
        print("[WARN] 语法自检不可用：%s" % exc)
        errs = []
    if errs:
        print("[FAIL] 生成的目视脚本未通过语法自检，拒绝写盘：")
        for e in errs:
            print("  · %s：%s" % (e.get("title"), e.get("detail")))
        return 1

    ms_path = os.path.join(os.path.abspath(args.out), "visual_probe.ms")
    # 3ds Max 中文版按 ANSI(GBK) 解析脚本
    with open(ms_path, "w", encoding="gbk", newline="\r\n") as f:
        f.write(ms)
    man_path = os.path.join(os.path.abspath(args.out), "visual_manifest.json")
    # 分批渲染时不能把另一批的结论冲掉：按 (key, renderer) 合并旧清单
    if os.path.exists(man_path):
        try:
            with open(man_path, encoding="utf-8") as f:
                old = json.load(f)
            keep = {(i.get("key"), i.get("renderer")): i for i in old.get("items", [])}
            merged = []
            for it in manifest["items"]:
                prev = keep.pop((it["key"], it["renderer"]), None)
                if prev:
                    it["passed"] = prev.get("passed")
                    it["reviewed_by"] = prev.get("reviewed_by", "")
                    it["comment"] = prev.get("comment", "")
                merged.append(it)
            merged.extend(keep.values())
            manifest["items"] = merged
        except Exception as exc:
            print("[WARN] 旧清单合并失败（按新清单写）：%s" % exc)
    with open(man_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("目视脚本：%s" % ms_path)
    print("待目视清单：%s（%d 张图，每个关键字 vray + corona 各一张）"
          % (man_path, len(items)))
    if not items:
        print("[FAIL] 未匹配到任何材质 ms，检查 --ms-dir 下是否有 vray/ corona/ 子目录")
        return 1
    for i in items:
        print("  · %s" % os.path.basename(i["png"]))
    print("真机：在 3ds Max 里 Run Script 该文件即可自动渲图，"
          "然后把 Listener 存成 visual_probe.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
