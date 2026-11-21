"""导出前静态预检（dry-run）+ 本机 V-Ray 环境自检

为什么需要这一层：
    历史故障中，导入 .ms 报错的根因不在参数逻辑，而在两处「环境层」问题：
      1) VRayMtl 在当前 3ds Max 里根本不存在（未装 V-Ray / 渲染器未切换），
         脚本运行到 VRayMtl() 才炸；
      2) 贴图用绝对路径写入，目录一挪就丢图。
    这两类问题都能在「导出前」用纯静态检查判定，不必等到进 Max 才发现。
    因此这里只做可确定性判定的检查，不做任何推测性断言：
      * 环境：扫描 V-Ray 插件目录（best-effort，探测不到只报 warn，不谎报版本）
      * 名称：空 / 非法字符 / 过长 / 中文
      * 贴图：存在性、可编码性（GBK）、路径长度
      * 脚本：对已生成的 .ms 做语法级静态校验（#" 字面量、括号与引号平衡、
              case 分支重复、顶层包裹、GBK 可写）
    返回结构同时用于前端「自检报告卡」渲染，避免报告与检查两套逻辑漂移。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import export_guard, ondevice_registry

# 3ds Max 插件目录候选（Chaos 官方安装位置）
PLUGIN_ROOTS = [
    r"C:\ProgramData\Autodesk\ApplicationPlugins",
    r"C:\Program Files\Chaos Group\V-Ray",
    r"C:\Program Files\Chaos\V-Ray",
]

# Corona 的安装位置（渲染器本体不是 V-Ray 那条 Chaos 插件链目录）
CORONA_ROOTS = [
    r"C:\ProgramData\Autodesk\ApplicationPlugins",
    r"C:\Program Files\Corona",
    r"C:\Program Files\Chaos\Corona",
    r"C:\Program Files\Chaos Group\Corona",
]

# 报告级别
OK, WARN, ERROR = "ok", "warn", "error"


# ------------------------------------------------------------------
# 环境自检
# ------------------------------------------------------------------
def probe_corona() -> dict:
    """探测本机 Corona 安装情况（best-effort，只报能找到的事实）

    与 probe_vray 同一口径：扫描标准目录 + 读 PackageContents.xml 的 AppVersion。
    Corona 目录名常见形如 Corona_10、Corona Renderer 12、Corona 9 for 3ds Max 2024，
    版本号优先取 XML，其次才从目录名里抽数字。
    """
    dirs: list[str] = []
    for root in CORONA_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            for name in sorted(os.listdir(root)):
                if "corona" not in name.lower():
                    continue
                dirs.append(os.path.join(root, name))
        except OSError:
            continue

    version = ""
    for d in dirs:
        pkg = os.path.join(d, "PackageContents.xml")
        if not os.path.isfile(pkg):
            continue
        try:
            text = Path(pkg).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = re.search(r'AppVersion\s*=\s*"([\d.]+)"', text)
        if m:
            version = m.group(1)
            break
    if not version:
        for d in dirs:
            m = re.search(r"corona[\s_-]*(?:renderer[\s_-]*)?(\d+(?:\.\d+)?)", Path(d).name.lower())
            if m:
                version = m.group(1)
                break

    max_year = ""
    for d in dirs:
        m = re.search(r"(20\d{2})", Path(d).name)
        if m:
            max_year = m.group(1)

    return {"installed": bool(dirs), "dirs": dirs, "version": version, "max_year": max_year}


def probe_vray() -> dict:
    """探测本机 V-Ray 安装情况（best-effort，只报能找到的事实）"""
    dirs: list[str] = []
    for root in PLUGIN_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            for name in sorted(os.listdir(root)):
                low = name.lower()
                if low.startswith("vray"):
                    dirs.append(os.path.join(root, name))
        except OSError:
            continue

    # 版本号：优先读 ApplicationPlugins 下 PackageContents.xml 的 AppVersion
    #（这是 Chaos 安装器写入的权威字段，比猜目录名可靠），其次才看路径里的数字
    version = ""
    plugin_dirs = [d for d in dirs if os.path.isdir(os.path.join(d, "plugins"))]
    for d in plugin_dirs:
        pkg = os.path.join(d, "PackageContents.xml")
        if not os.path.isfile(pkg):
            continue
        try:
            text = Path(pkg).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = re.search(r'AppVersion\s*=\s*"([\d.]+)"', text)
        if m:
            version = m.group(1)
            break
    if not version:
        for d in dirs:
            m = re.match(r"^vray[_-]?(?:adv[_-]?)?(\d{4,6})", Path(d).name.lower())
            if m:
                version = m.group(1)
                break

    # 插件目录名里带年份 -> 对应 3ds Max 版本
    max_year = ""
    for d in dirs:
        m = re.search(r"(20\d{2})", Path(d).name)
        if m:
            max_year = m.group(1)

    return {
        "installed": bool(dirs),
        "dirs": dirs,
        "version": version,
        "max_year": max_year,
    }


# ------------------------------------------------------------------
# .ms 静态语法校验（与 scripts/verify_ms.py 同规则，导出路径内联一份避免跨目录依赖）
# ------------------------------------------------------------------
def _mask_strings(src: str) -> str:
    """把字符串字面量内容抹成空格，避免内容里的括号/引号干扰结构校验"""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        # verbatim @"..."（MaxScript 支持）
        if c == "@" and i + 1 < n and src[i + 1] == '"':
            out.append('@ ')
            i += 2
            while i < n:
                if src[i] == '"':
                    if i + 1 < n and src[i + 1] == '"':
                        out.append("  ")
                        i += 2
                        continue
                    out.append(" ")
                    i += 1
                    break
                if src[i] == "\n":
                    out.append("\n")
                else:
                    out.append(" ")
                i += 1
            continue
        if c == '"':
            out.append(" ")
            i += 1
            while i < n:
                if src[i] == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if src[i] == '"':
                    out.append(" ")
                    i += 1
                    break
                if src[i] == "\n":
                    out.append("\n")
                else:
                    out.append(" ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_script(script: str) -> list[dict]:
    """对生成的 .ms 文本做静态校验，返回问题项列表"""
    items: list[dict] = []

    # 1) 禁用 #"字符串" name 字面量（Max 报「位于 string，需要 ( 或 [」）
    hits = re.findall(r'#"[^"\n]*"', script)
    if hits:
        items.append({
            "level": ERROR,
            "title": "脚本含非法 name 字面量",
            "detail": f'发现 {len(hits)} 处 #"..." 写法（如 {hits[0]}），'
                      f"3ds Max 会报「位于 string，需要 ( 或 [」",
        })
    else:
        items.append({"level": OK, "title": "name 字面量语法", "detail": "无 #\"...\" 写法"})

    masked = _mask_strings(script)

    # 2) 括号平衡
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    broken = None
    for idx, ch in enumerate(masked):
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                line = masked.count("\n", 0, idx) + 1
                broken = f"第 {line} 行出现多余的 {ch}"
                break
            stack.pop()
    if broken:
        items.append({"level": ERROR, "title": "括号不平衡", "detail": broken})
    elif stack:
        line = masked.count("\n", 0, len(masked)) + 1
        items.append({"level": ERROR, "title": "括号不平衡",
                      "detail": f"缺少闭合 {len(stack)} 个（文件末尾位于第 {line} 行）"})
    else:
        items.append({"level": OK, "title": "括号配对", "detail": "()/[]/{} 全部闭合"})

    # 3) 引号偶数（字符串未闭合）
    if masked.count('"') % 2 == 1:
        items.append({"level": ERROR, "title": "字符串未闭合",
                      "detail": '引号数量为奇数'})

    # 4) 顶层结构必须是 ( ... )
    head = script.lstrip()
    if not head.startswith("(") and not head.startswith("/*"):
        items.append({"level": ERROR, "title": "顶层结构缺失",
                      "detail": "脚本未用 ( ... ) 包裹"})

    # 5) case 分支重复（Max 会报重复 case）
    cases = re.findall(r"^\s*#([A-Za-z_]\w*)\s*:", masked, re.M)
    dups = {c for c in cases if cases.count(c) > 1}
    if dups:
        items.append({"level": ERROR, "title": "case 分支重复",
                      "detail": "重复分支: " + ", ".join(sorted(dups))})
    else:
        items.append({"level": OK, "title": "case 分支唯一",
                      "detail": f"{len(cases)} 个分支无重复"})

    # 6) catch 空表达式（历史坑：catch () 语法错误）
    if re.search(r"catch\s*\(\s*\)", masked):
        items.append({"level": ERROR, "title": "catch 空表达式",
                      "detail": "catch () 应写 catch ( 0 )"})

    # 7) GBK 可写性（中文 Windows 下 Max 按 ANSI 解析）
    try:
        script.encode("gbk")
        items.append({"level": OK, "title": "编码可写", "detail": "GBK(ANSI) 编码无损"})
    except UnicodeEncodeError as e:
        bad = script[e.start:e.end]
        items.append({
            "level": ERROR, "title": "编码不可写",
            "detail": f"含 GBK 无法表示的字符「{bad}」，Max 解析会出乱码",
        })

    # 8) 跨行运算符续写（真机事故 2026-11-20：探针脚本把长字符串折成两行，
    #    次行以 + 开头，Max 解析直接报「位于 +，需要 <因子>」，整个脚本跑不动。
    #    MAXScript 没有隐式续行，任何行首二元运算符都是语法错误。
    #    只判 "+" 与 and/or：* 会被注释框线 "****" 误伤，- 会被 "--" 注释误伤。）
    lead_ops = []
    for ln, line in enumerate(masked.split("\n"), start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("--"):
            continue
        if re.match(r"^(\+|\band\b|\bor\b)\s", stripped):
            lead_ops.append((ln, stripped[:40]))
    if lead_ops:
        sample = "; ".join(f"第 {ln} 行「{txt}」" for ln, txt in lead_ops[:3])
        items.append({
            "level": ERROR, "title": "跨行运算符续写",
            "detail": f"发现 {len(lead_ops)} 处行首二元运算符（{sample}），"
                      f"MAXScript 无隐式续行，真机 fileIn 会报语法错误",
        })
    else:
        items.append({"level": OK, "title": "无跨行运算符", "detail": "未出现行首 + 续行"})

    return items


# ------------------------------------------------------------------
# 模板结构预检（P1 补齐：把「历史上真实踩过、且能静态判定」的坑全部前移到这里）
# ------------------------------------------------------------------
def _strip_comments(src: str) -> str:
    """去掉 -- 行注释与 /* */ 块注释，保留字符串内容

    为什么只用行号无关的裸文本判断还不够：
        模板里大量注释在解释「为什么不能用 meditMaterials[i] == undefined」
        「2024 已移除 loadTempMaterialLibrary」，若连注释一起匹配，
        每份脚本都会恒定报警，预检就失去信噪比。宁可多写一个剥离函数。
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':                      # 字符串原样保留
            out.append(c)
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "-" and src.startswith("--", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_template_structure(script: str, renderer: str = "vray") -> list[dict]:
    """检查脚本里几处「踩过坑的写法」是否还在

    每一条都对应一次真实故障或一次已修的回归点：
      * VRayDirt / VRayNormalMap 在中文版是本地化类名，直接写字面量会静默降级
        -> 必须走 candidate 链探测；
      * 3ds Max 2024 移除了 newMaterialLibrary / loadTempMaterialLibrary，
        脚本里若还留着这些调用，运行时必炸 -> 属错误级；
      * meditMaterials[i] == undefined 在 2024 恒为假（默认槽带 Standard 材质），
        用它判空会永远覆盖第 1 槽 -> 属回归守卫，出现即告警；
      * 贴图路径策略必须是「脚本同级 textures/ 优先 + 绝对路径回退」，
        少了相对查找，用户搬走整个导出目录就丢图。
    """
    items: list[dict] = []
    code = _strip_comments(script)   # 注释里本身在解释这些坑，必须剥离后再判

    # 1) 本地化类名必须走候选链。中文版 V-Ray 的 VRayNormalMap/VRayDirt 会本地化成
    #    VRay_法线贴图 / VRay_污垢；Corona 侧同理（CoronaNormal / CoronaAO 在不同大版本里
    #    注册名有差异），所以两套渲染器都按「候选链是否存在」判，而不是放行字面量。
    cls_pairs = (("CoronaNormal", "法线贴图节点"), ("CoronaAO", "污垢(AO)节点")) \
        if str(renderer).lower() == "corona" else \
        (("VRayNormalMap", "法线贴图节点"), ("VRayDirt", "污垢(AO)节点"))
    for cls, label in cls_pairs:
        has_chain = re.search(rf'#\(\s*"{cls}"', code) is not None
        direct = re.search(rf'(?<![\w#"]){cls}\s*\(', code) is not None
        if has_chain:
            items.append({"level": OK, "title": f"{label}类名探测",
                          "detail": f"已按候选链探测 {cls} / 本地化名"})
        elif direct:
            items.append({"level": WARN, "title": f"{label}类名探测",
                          "detail": f'直接调用字面量 {cls}()，中文版 V-Ray 下通常为 undefined，'
                                    f"会静默降级（应改为候选链探测）"})

    # 2) 材质库接口：区分"死透的接口"与"旧版专用但需要降级路径"的接口
    #    newMaterialLibrary / loadTempMaterialLibrary 在 2024 已移除且本工具不再使用，
    #    出现即说明是老代码残留 —— 直接 ERROR。
    #    saveTempMaterialLibrary 是 2021 及更早唯一的保存接口：为了跨版本必须保留，
    #    但只允许作为 saveMaterialLibrary 失败后的第二级降级，且顺序不能反
    #    （先旧后新会让 2024 在第一级就抛错，日志会误标降级）。
    hard_dead = [n for n in ("newMaterialLibrary", "loadTempMaterialLibrary")
                 if re.search(rf"\b{n}\b", code)]
    has_legacy_save = re.search(r"\bsaveTempMaterialLibrary\b", code) is not None
    has_modern_save = re.search(r"\bsaveMaterialLibrary\b", code) is not None
    if hard_dead:
        items.append({"level": ERROR, "title": "材质库接口",
                      "detail": "调用了 3ds Max 2024 已移除的接口：" + ", ".join(hard_dead)
                                + "（运行必报错）"})
    elif has_legacy_save and not has_modern_save:
        items.append({"level": ERROR, "title": "材质库接口",
                      "detail": "只调用了 saveTempMaterialLibrary：2022+ 已移除，"
                                "应改为先 saveMaterialLibrary、失败再降级旧接口"})
    elif has_legacy_save and has_modern_save:
        if code.index("saveMaterialLibrary") < code.index("saveTempMaterialLibrary"):
            items.append({"level": OK, "title": "材质库接口",
                          "detail": "新旧双路：先 saveMaterialLibrary，失败降级 "
                                    "saveTempMaterialLibrary（兼容 2021 及更早）"})
        else:
            items.append({"level": ERROR, "title": "材质库接口",
                          "detail": "降级顺序反了：saveTempMaterialLibrary 出现在 "
                                    "saveMaterialLibrary 之前，2024 会在第一级抛错"})
    elif has_modern_save:
        if "[SKIP]" in code:
            items.append({"level": OK, "title": "材质库接口",
                          "detail": "已改为能力探测，无库时输出 [SKIP] 不计失败"})
        else:
            items.append({"level": WARN, "title": "材质库接口",
                          "detail": "调用了材质库接口但未见 [SKIP] 降级分支，缺库时可能中断"})
    elif re.search(r"\bmeditMaterials\b", code):
        items.append({"level": OK, "title": "材质库接口",
                      "detail": "本脚本只入材质编辑器槽位，不碰材质库接口"})

    # 3) 材质编辑器槽位判空的回归守卫（P0-1 修过的点，别再退回去）
    if re.search(r"meditMaterials\s*\[[^\]]+\]\s*==\s*undefined", code):
        items.append({"level": WARN, "title": "材质编辑器槽位判定",
                      "detail": "仍用 meditMaterials[i] == undefined 判空：3ds Max 2024 "
                                "默认槽带 Standard 材质，此判断恒为假，会永远覆盖第 1 槽"})

    # 4) 贴图相对路径策略
    if "textures" in code and re.search(r"getFilenamePath", code):
        items.append({"level": OK, "title": "贴图路径策略",
                      "detail": "优先脚本同级 textures/，回退绝对路径"})
    elif re.search(r"@?\"[A-Za-z]:[\\/].+\.(?:png|jpe?g|tga|exr|hdr|bmp|tif{1,2})\"", code, re.I):
        items.append({"level": WARN, "title": "贴图路径策略",
                      "detail": "脚本只含绝对路径贴图，搬动导出目录会丢图"})

    # 5) 真机类名对照（解决「字面量存在就判 OK」的假通过）
    #    前四条检查的判据都是「模板里有没有这段字符串」，天然存在假通过：
    #    #("CoronaNormal") 字面量在，检查就绿，可真机注册名是「Corona法线」。
    #    这一条改用 core/ondevice_registry.json（真机探针实测枚举）作唯一真源，
    #    判据变成「这个名字真机上到底存不存在」。
    try:
        items.extend(ondevice_registry.check_script(code, renderer))
    except Exception as e:                                    # 登记表缺失不能拖垮预检
        items.append({"level": WARN, "title": "真机类名对照",
                      "detail": f"真机登记表不可用（{e}），本次跳过对照，导出后需真机复核"})

    return items


def check_value_domain(values: dict) -> list[dict]:
    """按参数注册表的 min/max 校验数值域（拿不到注册表就跳过，不猜范围）"""
    items: list[dict] = []
    try:
        from . import params as _params  # 延迟导入：避免 core 内模块初始化顺序耦合
    except Exception:
        return items

    bad: list[str] = []
    for p in _params.PARAMS:
        # 数值型参数在本项目里分 range（滑杆）与 int（整数）两类，
        # 只认 "float" 会漏掉 29 项滑杆参数，域校验等于形同虚设
        if p.ptype not in ("range", "float", "int", "slider"):
            continue
        if p.id not in values:
            continue
        v = values.get(p.id)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            bad.append(f"{p.label}({p.id}) 不是数值：{v!r}")
            continue
        lo = p.min if p.min is not None else None
        hi = p.max if p.max is not None else None
        if lo is not None and v < lo:
            bad.append(f"{p.label}({p.id})={v} 低于下限 {lo}")
        if hi is not None and v > hi:
            bad.append(f"{p.label}({p.id})={v} 超出上限 {hi}")

    if bad:
        items.append({"level": ERROR, "title": "参数取值域",
                      "detail": "；".join(bad[:6]) + ("…" if len(bad) > 6 else "")})
    else:
        n = len([p for p in _params.PARAMS if p.ptype in ("float", "int", "slider") and p.id in values])
        items.append({"level": OK, "title": "参数取值域", "detail": f"{n} 个数值项均在合法区间内"})
    return items


# ------------------------------------------------------------------
# 综合预检
# ------------------------------------------------------------------
def run(
    material_name: str,
    script: str,
    tex_paths: dict,
    values: dict | None = None,
    preview_only: bool = False,
    has_source: bool = True,
    renderer: str = "vray",
) -> dict:
    """执行全部预检，返回报告

    参数：
        material_name : 目标材质名
        script        : 已生成的 .ms 全文（内存态，不落盘也能校验）
        tex_paths     : {"albedo": 绝对路径, ...} 实际要写入脚本的贴图
        has_source    : 是否有上传源图（纯色模式没源图，不要求 opacity 通道）
        renderer      : vray / corona，决定环境段探测哪个渲染器、脚本段按哪种材质自检
    """
    items: list[dict] = []
    values = values or {}

    # ---- 1. 名称 ----
    name = (material_name or "").strip()
    if not name:
        items.append({"level": ERROR, "title": "材质名称", "detail": "名称为空"})
    elif len(name) > 60:
        items.append({"level": WARN, "title": "材质名称",
                      "detail": f"长度 {len(name)}，建议 ≤60 字符"})
    elif re.search(r'["\\/:*?<>|]', name):
        items.append({"level": ERROR, "title": "材质名称",
                      "detail": '含非法字符 " \\ / : * ? < > | ，会导致脚本或文件名异常'})
    else:
        items.append({"level": OK, "title": "材质名称", "detail": f"「{name}」可用"})

    if re.search(r"[\u4e00-\u9fa5]", name):
        items.append({"level": WARN, "title": "名称含中文",
                      "detail": "中文名可运行，但材质库/跨机器交换建议英文名"})

    # ---- 2. 贴图 ----
    missing = [p for p in tex_paths.values() if p and not os.path.isfile(p)]
    if missing:
        items.append({"level": ERROR, "title": "贴图文件",
                      "detail": f"{len(missing)} 张贴图不存在：" + os.path.basename(missing[0])})
    elif tex_paths:
        items.append({"level": OK, "title": "贴图文件",
                      "detail": f"{len(tex_paths)} 张全部存在"})
    else:
        items.append({"level": WARN, "title": "贴图文件", "detail": "本次未挂载任何贴图"})

    # 通道完整性：开关开着却没有对应贴图，进 Max 就是「勾了法线但没有法线图」的空转。
    # 只提示不阻断——开关到贴图的映射在 params.resolve_vray 里有条件分支，
    # 硬判成 ERROR 会把合法组合误拦在导出前。
    absent = [c for c in export_guard.expected_channels(values, has_source)
              if not tex_paths.get(c)]
    if absent and tex_paths:
        items.append({
            "level": WARN, "title": "贴图通道",
            "detail": "开关已开但缺 " + " / ".join(absent) + " 贴图，进 Max 后该通道为空",
        })

    long_paths = [p for p in tex_paths.values() if p and len(p) > 180]
    if long_paths:
        items.append({"level": WARN, "title": "路径过长",
                      "detail": f"{len(long_paths)} 条贴图路径超 180 字符，老版本 Max 可能读不到"})

    bad_enc = []
    for p in [*tex_paths.values(), name]:
        if not p:
            continue
        try:
            p.encode("gbk")
        except UnicodeEncodeError:
            bad_enc.append(p)
    if bad_enc:
        items.append({"level": ERROR, "title": "路径编码",
                      "detail": "路径含 GBK 无法表示的字符，Max 中会变成乱码路径"})

    # ---- 3. 脚本静态校验 ----
    items.extend(check_script(script))

    # ---- 3b. 模板结构预检（本地化类名 / 已移除接口 / 槽位判空 / 路径策略）----
    items.extend(check_template_structure(script, renderer))

    # ---- 3c. 参数取值域 ----
    items.extend(check_value_domain(values))

    # ---- 4. 参数面 ----
    enabled_bools = [k for k, v in values.items() if v is True]
    items.append({
        "level": OK, "title": "参数概要",
        "detail": f"启用开关 {len(enabled_bools)} 个 · 参数项 {len(values)} 个",
    })

    # ---- 5. 环境（按目标渲染器探测；另一家只作为备用信息）----
    is_corona = str(renderer).lower() == "corona"
    vray_env = probe_vray()
    corona_env = probe_corona()
    target_name = "Corona" if is_corona else "V-Ray"
    target_env = corona_env if is_corona else vray_env
    if target_env["installed"]:
        ver = f" v{target_env['version']}" if target_env.get("version") else ""
        items.append({"level": OK, "title": f"{target_name} 环境",
                      "detail": f"检测到插件目录{ver}（{Path(target_env['dirs'][-1]).name}）"})
    else:
        items.append({
            "level": WARN, "title": f"{target_name} 环境",
            "detail": f"未在标准目录检测到 {target_name}。脚本自带降级逻辑（Corona→PhysicalMaterial / "
                      f"V-Ray→PhysicalMaterial），但效果与原材质不等价；若已装在非标准路径可忽略此项",
        })
    alt_name, alt_env = ("V-Ray", vray_env) if is_corona else ("Corona", corona_env)
    if alt_env["installed"]:
        items.append({"level": OK, "title": f"{alt_name}（备用）",
                      "detail": f"本机也装有 {alt_name}，但当前导出目标是 {target_name}，不会用到它"})
    # env 结构对前端保持向后兼容：installed/version 始终指「本次目标渲染器」
    env = dict(target_env)
    env["target"] = "corona" if is_corona else "vray"
    env["vray"] = vray_env
    env["corona"] = corona_env

    blocking = [i for i in items if i["level"] == ERROR]
    warnings = [i for i in items if i["level"] == WARN]
    return {
        "ok": not blocking,
        "blocking": len(blocking),
        "warnings": len(warnings),
        "items": items,
        "env": env,
        "preview_only": preview_only,
    }
