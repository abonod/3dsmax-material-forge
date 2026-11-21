#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成真机探针脚本（P1 的另一半：让日志具备可判定的证据）。

为什么要"哨兵材质"而不是"清空 medit"：
    历史事故里，fileIn 全部失败，但探针回读的是上一轮残留在 medit 里的旧材质，
    属性齐全、名字也对，于是被当成"本次载入成功"。单纯清空 medit 只能让回读变
    undefined（能发现问题，但分不清是'没载入'还是'载入了但没放第1槽'）。
    这里改为在 fileIn 前把 meditMaterials[1] 显式置为一个**名字独一无二的哨兵**：
      - fileIn 后仍是哨兵 → 材质脚本根本没写进该槽 → BIND_MISMATCH += 1
      - 变成了别的材质但名字/类型不符 → 载入错对象 → BIND_MISMATCH += 1
    只有"名字等于期望 + 类型命中渲染器特征"才算绑定成立。

探针输出（供 scripts/verify_probe_log.py 判定）：
    FILEIN <renderer>/<file> -> OK|FAIL
    BIND_MISMATCH=<n>
    SCRIPT_RUN=1

用法：
    python scripts/gen_probe.py --ms-dir <生成目录> --out <探针.ms>
    python scripts/gen_probe.py --ms-dir <生成目录> --out <探针.ms> --expect 石材
"""

from __future__ import annotations

import argparse
import os
import sys

HEADER = '''-- Material Forge 真机探针（自动生成，请勿手改）
-- 生成器：scripts/gen_probe.py
-- 判定器：scripts/verify_probe_log.py
-- 说明：本脚本只做"载入 + 绑定校验"，不创建材质、不改渲染器设置。

(
    local mfJobs = #(
%(jobs)s
    )
    local mfBindMiss = 0
    local mfBadSlots = 0
    local mfLines = #()
    -- 日志落盘：Listener 输出靠人工复制既慢又容易漏，跨版本取证还必须把日志归档进 docs/
    local mfLogPath = "%(log)s"

    fn mfP msg = (
        append mfLines msg
        format "%%\\n" msg
        if (mfLogPath != "") do (
            local fs = openFile mfLogPath mode:"a"
            if (fs != undefined) do (
                format "%%\\n" msg to:fs
                close fs
            )
        )
    )

    -- 哨兵：名字独一无二，fileIn 若真的覆盖了第 1 槽，这个材质必然消失
    local SENTINEL = "__MF_PROBE_SENTINEL_DO_NOT_USE__"

    fn mfArmSentinel = (
        local s = StandardMaterial name:SENTINEL
        meditMaterials[1] = s
        s
    )

    fn mfIsSentinel o = (
        if o == undefined then true else (
            (classof o == StandardMaterial) and (o.name == SENTINEL)
        )
    )

    fn mfRunJob renderer sub file expectName expectClass = (
        local tag = renderer + "/" + file
        -- 1) 先清空 24 个槽，杀掉上一 job 的残留（旧事故的直接成因）
        for i = 1 to 24 do (
            try ( meditMaterials[i] = StandardMaterial name:("__MF_CLEAR_" + (i as string)) ) catch ( 0 )
        )
        -- 2) 再埋哨兵，作为"是否被覆盖"的判据
        mfArmSentinel()
        local path = sub + "\\\\" + file
        local loaded = false
        try ( fileIn path; loaded = true ) catch (
            loaded = false
        )
        if (not loaded) do (
            mfP ("FILEIN " + tag + " -> FAIL(抛异常)")
            mfBindMiss += 1
            return false
        )
        -- 3) 绑定校验：第 1 槽必须已不是哨兵
        local got = undefined
        try ( got = meditMaterials[1] ) catch ( got = undefined )
        if (mfIsSentinel got) do (
            mfP ("FILEIN " + tag + " -> FAIL(材质未写入第1槽，回读仍是哨兵；fileIn 很可能因路径/渲染器缺失而静默失败)")
            mfBindMiss += 1
            return false
        )
        -- 4) 名字与类型必须与期望一致
        local nmOk = true
        if (expectName != undefined and expectName != "") do (
            nmOk = (got.name == expectName)
        )
        local clOk = true
        if (expectClass != undefined and expectClass != "") do (
            clOk = ((classof got) as string == expectClass)
        )
        if (not nmOk or not clOk) do (
            mfP ("FILEIN " + tag + " -> FAIL(绑定对象不符：期望 " + expectName + "/" + expectClass + "，实得 " + (got.name) + "/" + ((classof got) as string) + ")")
            mfBindMiss += 1
            return false
        )
        -- 5) 槽位回读校验（C5）：每个贴图槽写完必须能再读回非 undefined，否则记 BAD
        --    只数"槽里有东西"是不够的——旧事故就是"看起来有槽，其实写的是上一 job 的残留"。
        local texN = 0
        local slotBad = 0
        for p in (getPropNames got) do (
            local v = undefined
            try ( v = getProperty got p ) catch ( v = undefined )
            if (v != undefined and isKindOf v TextureMap) do (
                texN += 1
                local v2 = undefined
                try ( v2 = getProperty got p ) catch ( v2 = undefined )
                if (v2 == undefined) do slotBad += 1
            )
        )
        mfBadSlots += slotBad
        mfP ("FILEIN " + tag + " -> OK（材质 " + got.name + "，贴图槽 " + (texN as string) + " 个，回读失败 " + (slotBad as string) + " 个）")
        true
    )

    -- 版本探测段（P3/P5/P6/P7）：让每份真机日志自带机器/版本/语言证据。
    -- 用 probe_ms_logfn 而不是 probe_ms，因为探测段必须同时写进日志文件，
    -- 否则 verify_probe_log.py --require-compat 真机必然判 FAIL（此前就是漏了这一步）。
%(probe)s

    mfP "===== Material Forge 真机探针 ====="
    local total = 0
    local pass = 0
    for j in mfJobs do (
        total += 1
        local ok = mfRunJob j[1] j[2] j[3] j[4] j[5]
        if ok do pass += 1
    )

    mfP "-----------------------------------"
    mfP ("BIND_MISMATCH=" + (mfBindMiss as string))
    mfP ("BAD=" + (mfBadSlots as string))
    mfP ("JOBS_PASS=" + (pass as string) + "/" + (total as string))
    mfP "SCRIPT_RUN=1"
    mfP ("VERDICT=" + (if mfBindMiss == 0 and pass == total then "OK" else "FAIL"))
    mfP "==================================="
)
'''


def collect(ms_dir: str, expect: str | None):
    """扫描 <ms_dir>/<renderer>/<file>.ms，产出 (renderer, 子目录, 文件名, 期望名, 期望类)。"""
    rows = []
    for renderer in sorted(os.listdir(ms_dir)):
        sub = os.path.join(ms_dir, renderer)
        if not os.path.isdir(sub):
            continue
        for f in sorted(os.listdir(sub)):
            if not f.lower().endswith(".ms"):
                continue
            rows.append((renderer, sub, f, expect or "", ""))
    return rows


def build(rows, log: str = "") -> str:
    jobs = []
    for renderer, sub, f, expect, cls in rows:
        jobs.append(
            '        #(%s, %s, %s, %s, %s)'
            % (
                _q(renderer), _q(sub.replace("\\", "\\\\")), _q(f),
                _q(expect or ""), _q(cls or ""),
            )
        )
    # 日志路径要转义反斜杠并做引号转义，否则 MaxScript 字符串会吃掉路径分隔符
    logQ = log.replace("\\", "\\\\") if log else ""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(script_dir)
    if root not in sys.path:
        sys.path.insert(0, root)
    from core import compat as _compat

    probe = _compat.probe_ms_logfn("mfP")
    return HEADER % {"jobs": ",\n".join(jobs), "log": logQ, "probe": probe}


def _q(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="生成真机探针脚本")
    ap.add_argument("--ms-dir", required=True, help="生成的 ms 根目录（其下按渲染器分子目录）")
    ap.add_argument("--out", required=True, help="探针脚本输出路径")
    ap.add_argument("--expect", default="", help="期望的材质名（所有 job 共用）")
    ap.add_argument("--log", default="", help="探针运行时把日志追加写到该文件（真机取证归档用）")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.ms_dir):
        print("[FAIL] ms 目录不存在：%s" % args.ms_dir)
        return 1
    rows = collect(args.ms_dir, args.expect)
    if not rows:
        print("[FAIL] %s 下没有找到任何 .ms（注意渲染器子目录层级）" % args.ms_dir)
        return 1

    text = build(rows, log=args.log)
    # 生成即自检：语法错（如跨行 + 续写）必须在这里挡住，不能等真机 fileIn 才炸
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core import preflight as _pf

        errs = [i for i in _pf.check_script(text) if i.get("level") == "ERROR"]
    except Exception as exc:  # 自检本身不可用时不阻断生成，但必须显式告警
        print("[WARN] 语法自检不可用：%s" % exc)
        errs = []
    if errs:
        print("[FAIL] 生成的探针脚本未通过语法自检，拒绝写盘：")
        for e in errs:
            print("  · %s：%s" % (e.get("title"), e.get("detail")))
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    # MaxScript 在中文 Windows 上按 ANSI/GBK 解析，必须写 GBK 而非 UTF-8
    with open(args.out, "w", encoding="gbk", errors="replace", newline="\r\n") as fh:
        fh.write(text)

    print("探针已生成：%s（%d 个 job）" % (args.out, len(rows)))
    for r in rows:
        print("  · %s/%s" % (r[0], r[2]))
    print("提示：把探针输出保存为日志后，运行 "
          "python scripts/verify_probe_log.py <日志> --expect-jobs %d" % len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
