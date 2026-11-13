# -*- coding: utf-8 -*-
"""导出产物自检（单一事实来源）

为什么单独成模块
----------------
「导出界面显示成功 → 进 3ds Max Run Script 才报贴图缺失」这条链路上，判定口径
必须只有一份。app.py（导出接口）、core/preflight.py（导出前预检）、
evals/runner.py（回归用例）三处如果各写一套，就会出现「预检过了、导出照样断链」
的口径漂移。所以把规则收在这里，三边共用。

自检做两件事
------------
1. 产物侧：tex_paths 里每个文件是否真的躺在磁盘上、是否 0 字节；
2. 脚本侧：把 .ms 里出现的贴图绝对路径全部抓出来，逐个回查磁盘——
   这才是 Max 打开时才炸的那批引用，比「按开关猜通道」准得多。
   通道完整性（开了 normal_on 却没有法线图）只作提示，不阻断导出：
   开关与贴图的映射在 params.resolve_vray 里有条件分支，硬判会误伤。
"""
from __future__ import annotations

import os
import re

# 参数开关 → 期望存在的贴图通道（保守映射：只用于提示，不阻断导出）
_CHANNEL_SWITCHES = (
    ("normal", ("normal_on",)),
    ("height", ("bump_on", "displacement_on")),
    ("ao", ("ao_on",)),
    ("opacity", ("opacity_on",)),
)

# 抓 .ms 里的贴图绝对路径：MaxScript 字符串里反斜杠是单写或双写，两种都吃
_TEX_REF = re.compile(
    r'"([A-Za-z]:[^"]*?\.(?:png|jpe?g|tif?f?|exr|bmp|tga|hdr))"', re.I
)

# 与 ms_exporter 保持一致的"脚本同级 textures/"约定
TEX_SUBDIR = "textures"


def safe_dirname(name: str, fallback: str = "material") -> str:
    """材质名 → 磁盘安全目录名

    和 ms_exporter.export() 里生成 .ms 文件名用的是同一套规则（alnum + _-）：
    目录名与 .ms 文件名同源，用户看到「铝合金_拉丝_.ms 在 铝合金_拉丝_/ 里」不会错乱。
    """
    s = "".join(c if (c.isalnum() or c in "_-") else "_" for c in (name or ""))
    s = s.strip("_")
    return s or fallback


def expected_channels(values: dict, has_source: bool = True) -> list[str]:
    """按开关推导「应该有」的贴图通道（仅用于缺失提示）"""
    values = values or {}
    # albedo 与 roughness 是恒生成通道（build_texture_set 里无条件写盘），
    # 不受任何开关控制，所以它们必须常在期望列表里，否则丢图查不出来。
    out = ["albedo", "roughness"]
    for chan, keys in _CHANNEL_SWITCHES:
        if chan == "opacity" and not has_source:
            continue
        if any(bool(values.get(k)) for k in keys):
            out.append(chan)
    return out


def script_texture_refs(script: str) -> list[str]:
    """.ms 全文里引用到的贴图绝对路径（去重保序）"""
    seen, out = set(), []
    for m in _TEX_REF.finditer(script or ""):
        p = m.group(1).replace("\\\\", "\\")
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def check_export(
    script: str,
    tex_paths: dict,
    values: dict | None = None,
    out_dir: str | None = None,
    has_source: bool = True,
) -> dict:
    """导出后自检：产物在不在、脚本引用有没有断链

    返回结构（前端结果卡与 evals 断言都吃这个结构）：
        ok           : 无缺失、无断链
        checked      : 实际校验的贴图数
        missing      : [{"channel","path","reason"}] 产物级缺失
        broken_refs  : [路径] 脚本引用了但磁盘上没有
        refs         : 脚本里抓到的贴图引用数
        absent       : [通道] 开关开着但没对应贴图（仅提示）
        packed       : {"dir","count"} 导出目录下 textures/ 的实际文件数
    """
    values = values or {}
    tex_paths = {k: v for k, v in (tex_paths or {}).items() if not k.startswith("_")}

    missing: list[dict] = []
    for chan, p in tex_paths.items():
        if not p:
            missing.append({"channel": chan, "path": "", "reason": "路径为空"})
            continue
        try:
            if not os.path.isfile(p):
                missing.append({"channel": chan, "path": str(p), "reason": "文件不存在"})
            elif os.path.getsize(p) == 0:
                missing.append({"channel": chan, "path": str(p), "reason": "文件为 0 字节"})
        except OSError as e:
            missing.append({"channel": chan, "path": str(p), "reason": f"无法读取：{e}"})

    refs = script_texture_refs(script)
    broken = [r for r in refs if not os.path.isfile(r)]

    absent = [c for c in expected_channels(values, has_source) if not tex_paths.get(c)]

    packed = {"dir": "", "count": 0}
    if out_dir:
        td = os.path.join(out_dir, TEX_SUBDIR)
        if os.path.isdir(td):
            files = [f for f in os.listdir(td) if os.path.isfile(os.path.join(td, f))]
            packed = {"dir": td, "count": len(files)}

    return {
        "ok": not missing and not broken,
        "checked": len(tex_paths),
        "missing": missing,
        "broken_refs": broken,
        "refs": len(refs),
        "absent": absent,
        "packed": packed,
    }


def summarize(result: dict) -> str:
    """一句话人话摘要（写进 note / 日志）"""
    if not result:
        return ""
    if result.get("ok"):
        n = result.get("checked", 0)
        packed = (result.get("packed") or {}).get("count", 0)
        return f"自检通过：{n} 张贴图全部到位，导出目录已打包 {packed} 张"
    parts = []
    miss = result.get("missing") or []
    if miss:
        parts.append("缺 %d 张（%s）" % (len(miss), miss[0].get("reason", "")))
    broken = result.get("broken_refs") or []
    if broken:
        parts.append("脚本断链 %d 处（%s）" % (len(broken), os.path.basename(broken[0])))
    return "自检异常：" + "；".join(parts)
