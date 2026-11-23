#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成「真实世界尺寸 UV」真机枚举探针（P3，2026-11-22）

为什么不是直接写代码：
    让贴图按物理尺寸平铺（一块瓷砖 0.8m）在 Max 里只有一条路——位图的
    coordinates 切到 real-world scale 并给米数，再配合物体上 Real-World
    Map Size 的 UVW。但这条路上的**属性名**本项目一点证据都没有：
    BitmapUVGen 在 2021/2024/2025 是否同名、Corona 位图是不是另一套坐标类、
    中文版有没有本地化——全是未证实项。

    项目在 0.9.x 已经吃过一次「属性名靠推理」的亏（Corona 色散/AO 属性名
    写错，真机静默不生效）。所以这里先取证、后写码：真机把属性名吐出来，
    拿到日志再落映射，绝不猜。

探针做什么：
    A) 枚举标准 Bitmaptex 的 coordinates，以及 Corona 位图（类名取自真机
       注册表，不硬编码猜测名）的属性名与类型，命中
       realWorld/realScale/worldScale 关键字的逐条列出来；
    B) 对命中的属性真的写一次再回读（Boolean 写 true、数值写 0.8），
       写入失败/回读不符都记 FAIL —— 只看"属性存在"不等于"能写"；
    C) 输出 RW_SCALE_SUPPORTED=0/1 与 RWPROBE_VERDICT，判定器与人工都读得懂。

用法：
    python scripts/gen_rw_probe.py --out output/rw_probe.ms --log output/rw_probe.log
    # 在 Max 里 Scripting > Run Script 跑 rw_probe.ms，然后：
    # 把日志给 Agent 解析，或先看 RWPROBE_VERDICT 一行

约束：
    - 运行期不启动 3ds Max（本脚本只生成 .ms，不执行）；
    - 成品必须 GBK 可编码（中文版 Max 按 ANSI 解析脚本）；
    - 生成后先过 preflight 语法自检，不通过拒绝写盘。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

TEMPLATE = r'''-- Material Forge 真实世界尺寸 UV 探针（自动生成，请勿手改）
-- 生成器：scripts/gen_rw_probe.py
-- 用途：枚举位图坐标类上的 real-world scale 属性名，并验证可写可回读。
-- 本脚本只读属性 + 写 3 个候选值，不创建材质、不改渲染器设置。

(
    local mfLogPath = "@@LOG@@"

    fn mfP msg = (
        format "%\n" msg
        if (mfLogPath != "") do (
            local fs = openFile mfLogPath mode:"a"
            if (fs != undefined) do (
                format "%\n" msg to:fs
                close fs
            )
        )
    )

    -- timestamp() 返回整数不是字符串：直接拼串在真机第 20 行报「类型错误」，
    -- 必须显式 as string，否则分段头写不出来、整份脚本当场中断。
    mfP ("RWPROBE_RUN_BEGIN=" + ((timestamp()) as string))
    mfP "===== Material Forge 真实世界尺寸 UV 探针 ====="
@@PROBE@@

    -- 关键字命中：属性名里带 realworld / realscale / worldscale 才算候选
    fn mfIsRwProp p = (
        local t = toLower (p as string)
        (((findString t "realworld") != undefined) or ((findString t "realscale") != undefined) or ((findString t "worldscale") != undefined))
    )

    -- 只写不验等于没验：写完必须回读，回读不符一律按失败记
    fn mfTryBool obj prop = (
        local ok = false
        try (
            setProperty obj prop true
            local v = getProperty obj prop
            ok = (v == true)
        ) catch ( ok = false )
        ok
    )

    fn mfTryNum obj prop val = (
        local ok = false
        try (
            setProperty obj prop val
            local v = (getProperty obj prop) as float
            ok = ((abs (v - val)) < 0.001)
        ) catch ( ok = false )
        ok
    )

    -- 计数器必须用全局：MAXScript 的 fn 里改外部 local 是按值捕获，
    -- 写成 local 会导致循环里加了但外面读还是 0，结论行直接假阴性。
    -- 名字不得与任何 fn 同名：曾经叫 mfRwHit，与谓词 fn mfRwHit 撞名，
    -- 首遍靠脚本作用域里 fn 优先侥幸跑通，第二遍 fileIn 时 mfRwHit 已是数字 0，
    -- 第 119 行「调用需要函数或类，得到的是: 0」当场崩、日志停在半截。
    -- 谓词函数同时改名 mfIsRwProp：旧会话里 mfRwHit 仍以数字 0 残留在全局，
    -- 只要还有个 fn 叫这个名，重载时一样会被那个数字盖掉。
    ::mfHitCount = 0
    ::mfRwWritable = 0

    -- dump 属性并试写：label 是日志前缀，full 为 true 时全量列（坐标类属性少），
    -- 否则只列关键字命中项（位图类属性多，全列会把日志刷成几百行）
    fn mfScan obj label full = (
        local props = #()
        try ( props = getPropNames obj ) catch ( props = #() )
        mfP ("RWSCAN " + label + " props=" + (props.count as string))
        for p in props do (
            local hit = mfIsRwProp p
            if (hit) do ::mfHitCount += 1
            if (full or hit) do (
                local v = undefined
                try ( v = getProperty obj p ) catch ( v = undefined )
                local vs = "undefined"
                if (v != undefined) do (
                    try ( vs = (v as string) ) catch ( vs = "?" )
                )
                mfP ("  RWPROP " + label + "." + (p as string) + " = " + vs)
            )
            if (hit) do (
                local okB = mfTryBool obj p
                local okN = false
                if (not okB) do okN = mfTryNum obj p 0.8
                if (okB or okN) do ::mfRwWritable += 1
                local how = "FAIL"
                if (okB) do how = "OK(bool true)"
                if (okN) do how = "OK(num 0.8)"
                mfP ("  RWTRY " + label + "." + (p as string) + " -> " + how)
            )
        )
    )

    -- execute 一个类名只拿到类本身，要枚举属性必须造实例；
    -- 而实例构造失败（缺渲染器/类名改了）也要显式告警，不能当成"属性为空"。
    fn mfNewInst nm = (
        local o = undefined
        try ( o = execute (nm + "()") ) catch ( o = undefined )
        o
    )

    -- A) 标准位图的坐标类：这条是 Corona/V-Ray 之外的通用路径，必须先看
    local bt = undefined
    try ( bt = Bitmaptex() ) catch ( bt = undefined )
    if (bt == undefined) then (
        mfP "RWBITMAP Standard -> CREATE_FAIL"
    ) else (
        mfP "RWBITMAP Standard -> OK"
        local co = undefined
        try ( co = bt.coordinates ) catch ( co = undefined )
        if (co == undefined) then (
            mfP "RWCOORDS Standard -> NONE"
        ) else (
            mfP ("RWCOORDS Standard class=" + ((classof co) as string))
            mfScan co "coords" true
        )
    )

    -- B) 两个渲染器各自的位图类：类名本身也可能不存在（没装/改了名），
    --    所以先探类，再探属性，不存在就明说 ABSENT，不要静默略过。
    -- B) Corona 位图：类名取 core/ondevice_registry.json 里的真机实证名（中文版是本地化名），
    --    不写未证实的英文猜测名。V-Ray 侧 2026-11-22 真机已判定自有位图类不存在，
    --    位图走标准 Bitmaptex —— 即 A 段那条路，所以这里不再重复，也不写证伪名。
    local cb = mfNewInst "@@CORONA_BITMAP@@"
    if (cb == undefined) then (
        mfP "RWBITMAP CoronaTexmap -> ABSENT(本机无该注册名；英文版可能另有类名，需补候选后重跑)"
    ) else (
        mfP "RWBITMAP CoronaTexmap -> OK"
        mfScan cb "corona" false
        local cc = undefined
        try ( cc = cb.coordinates ) catch ( cc = undefined )
        if (cc == undefined) then mfP "RWCOORDS coronaCoords -> NONE" else (
            mfP ("RWCOORDS coronaCoords class=" + ((classof cc) as string))
            mfScan cc "coronaCoords" true
        )
    )

    -- C) 结论行：supported 只看"关键字命中且真写进去且回读一致"
    mfP "-----------------------------------"
    mfP ("RW_PROPS_HIT=" + (::mfHitCount as string))
    mfP ("RW_PROPS_WRITABLE=" + (::mfRwWritable as string))
    local sup = 0
    if (::mfRwWritable > 0) do sup = 1
    mfP ("RW_SCALE_SUPPORTED=" + (sup as string))
    if (sup == 1) then (
        mfP "RWPROBE_VERDICT=PASS(存在可写可回读的 real-world 属性，可按日志里的属性名落映射)"
    ) else (
        mfP "RWPROBE_VERDICT=NO-SUPPORT(没找到可写的 real-world 属性，真实世界尺寸这条路在本机/本版本不成立，不要写进导出模板)"
    )
    mfP "RWPROBE_DONE=1"
)

-- 日志路径尽量用绝对路径；若这里为空，把 Listener 内容另存为 .log 也可
'''


def _ms_escape(path: str) -> str:
    """MAXScript 字符串里反斜杠要双写，否则 \t \n 会被当转义。"""
    return path.replace("\\", "\\\\").replace('"', '""')


def _corona_texmap_class() -> str:
    """Corona 位图类名从真机注册表取，绝不硬编码英文猜测名。

    2026-11-22 真机：V-Ray 侧自有位图类不存在（走标准 Bitmaptex）；
    Corona 侧位图是本地化注册名，注册表 texture_classes 里有实证名单。
    """
    import json

    reg = json.loads((Path(__file__).resolve().parent.parent / "core" /
                      "ondevice_registry.json").read_text(encoding="utf-8"))
    for name in reg.get("renderers", {}).get("corona", {}).get("texture_classes", []):
        if name.endswith("位图") or name.lower().endswith("bitmap"):
            return name
    return ""


def build(log: str = "") -> str:
    from core import compat as _compat

    probe = _compat.probe_ms_logfn("mfP")
    text = (TEMPLATE
            .replace("@@LOG@@", _ms_escape(log))
            .replace("@@CORONA_BITMAP@@", _corona_texmap_class())
            .replace("@@PROBE@@", probe))
    return text


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="生成真实世界尺寸 UV 真机枚举探针")
    ap.add_argument("--out", required=True, help="探针脚本输出路径（.ms）")
    ap.add_argument("--log", default="", help="真机上把日志追加写到该绝对路径（取证归档用）")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    text = build(log=args.log)

    # 语法自检：括号/引号/GBK 可编码。探针要在中文版 Max 里跑，
    # 一个没闭合的括号在真机上是整段不执行、Listener 只丢一行红字。
    from core import preflight as _pf

    errs = [i for i in _pf.check_script(text) if i.get("level") == "ERROR"]
    if errs:
        print("[FAIL] 生成的探针未通过语法自检，拒绝写盘：")
        for e in errs:
            print("  · %s：%s" % (e.get("title"), e.get("detail")))
        return 1

    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="gbk", errors="replace", newline="\r\n") as fh:
        fh.write(text)
    print("真实世界尺寸探针已生成：%s" % out)
    if args.log:
        print("真机日志将追加到：%s" % args.log)
    print("用法：在 3ds Max 里 Scripting > Run Script 选它，"
          "然后看日志里 RWPROBE_VERDICT 一行（RW_SCALE_SUPPORTED=1 才可落映射）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
