"""真机类名登记表（ondevice registry）加载与对照。

为什么需要这一层
----------------
`preflight.check_template_structure` 之前的类名检查只看「候选链字面量存不存在」，
于是出现典型的假通过：模板里写着 `#("CoronaNormal", ...)`、`#("CoronaDisplacementMod")`，
字面量确实存在，检查报 OK，但真机注册名其实是「Corona法线」「Corona置换修改器」，
候选链第一条永远落空、第二条又是猜的名字 —— 静态全绿，真机静默不生效。

这个模块把真机枚举出来的名字固化成唯一真源（`ondevice_registry.json`），
让静态自检的判定依据从「模板里有什么字符串」变成「这个名字是不是真机上真的存在」。
产物：任何模板里出现某能力却没有出现真机名 -> 至少 WARN；出现了已证伪的名字 -> ERROR。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REGISTRY_PATH = Path(__file__).with_name("ondevice_registry.json")

_CACHE: dict | None = None


def load(force: bool = False) -> dict:
    global _CACHE
    if _CACHE is None or force:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            _CACHE = json.load(f)
    return _CACHE


def renderer(renderer: str) -> dict:
    """取某渲染器的登记项；未知渲染器返回空 dict（调用方按「无登记」处理）"""
    key = str(renderer or "").strip().lower()
    return dict(load().get("renderers", {}).get(key) or {})


def host_line() -> str:
    h = load().get("host", {})
    return " / ".join(v for v in (h.get("max"), h.get("corona"), h.get("vray")) if v)


def get_class(renderer: str, key: str, default=None):
    return renderer_map_get(renderer, key, default)


def renderer_map_get(renderer: str, key: str, default=None):
    return renderer(renderer).get(key, default)


# ---------------------------------------------------------------- 语义对照表
# 每个语义项：(语义标签, 必须出现的真机名候选, 已证伪的名字, 触发词)
# 触发词用来判断「这份脚本到底有没有碰这个能力」，避免给纯标量材质脚本刷一堆无关告警。
_SPECS = {
    "corona": [
        ("物理材质类名", ["_Corona物理材质", "Corona物理材质"], [], ["CoronaPhysicalMtl", "mfCoronaMtlClass"]),
        ("法线节点类名", ["Corona法线"], ["CoronaNormal", "CoronaNormalMap", "Corona_Normal"], ["Normal", "法线"]),
        ("法线输入槽", ["normalMap"], ["texmapNormal"], ["mfAttachNormalBump"]),
        ("附加凹凸槽", ["additionalBump"], [], ["mfAttachNormalBump"]),
        ("置换修改器类名", ["Corona置换修改器"], ["CoronaDisplacementMod"], ["DisplacementMod", "置换"]),
        ("色散使能属性", ["dispersionEnable"], [], ["dispersion"]),
        # probeG 2026-11-20 真机回读：Corona 凹凸走 baseBumpTexmap + baseBumpMapAmount，
        # 内层 wrapper 的 multiplier 恒 1.0（强度只归外层，避免平方衰减）
        ("凹凸槽位", ["baseBumpTexmap"], ["texmap_bump"], ["mfAttachNormalBump", "BumpTexmap", "mfAttachTex"]),
        ("凹凸强度属性", ["baseBumpMapAmount"], ["texmap_bump_multiplier"], ["BumpMapAmount", "bump_multiplier", "bumpAmount"]),
    ],
    "vray": [
        ("法线节点类名", ["VRay_法线贴图", "VRayNormalMap"], [], ["Normal", "法线"]),
        ("法线输入槽", ["normal_map"], ["normal"], ["mfAttachNormalBump"]),
        ("置换修改器类名", ["VRayDisplacementMod"], [], ["DisplacementMod", "置换"]),
        ("色散属性真名", ["refraction_dispersion"], ["dispersion"], ["dispersion"]),
        # probeG 2026-11-20 真机回读：V-Ray 凹凸走 texmap_bump + texmap_bump_multiplier，
        # 内层 wrapper 的 normal_map_multiplier 恒 1.0
        ("凹凸槽位", ["texmap_bump"], ["baseBumpTexmap"], ["mfAttachNormalBump", "BumpTexmap", "mfAttachTex"]),
        ("凹凸强度属性", ["texmap_bump_multiplier"], ["baseBumpMapAmount"], ["bump_multiplier", "BumpMapAmount"]),
    ],
}


def _strip_comments(script: str) -> str:
    """剔除 MAXScript 行注释（-- 之后）后再做名字判定。

    否则注释里出现的真机名（「旧写法 VRayNormalMap 取不到」这类说明）
    会让检查误判为「已使用真机名」，形成另一种假通过。
    """
    out = []
    for line in script.splitlines():
        idx = line.find("--")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _uses(script: str, name: str) -> bool:
    """判断脚本里是否「真的用了这个名字」。

    ASCII 标识符必须按词边界匹配：真机名 normal_map 内含子串 normal，
    若用 `in` 判断，会把正确写法误判成「用了已证伪的 normal」。
    中文名无词边界概念，直接子串判断。
    """
    if not name:
        return False
    if all(ord(ch) < 128 for ch in name):
        return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", script) is not None
    return name in script


def check_script(script: str, renderer_name: str = "vray") -> list[dict]:
    """拿脚本正文对照真机登记表，返回 preflight 同构的 items。

    判定顺序：
      1. 真机名命中 -> OK
      2. 没命中真机名、但出现已证伪的名字 -> ERROR（这是真机 100% 落空的写法）
      3. 没命中真机名、脚本其它地方又确实在做这件事 -> WARN（可能靠候选链兜底，需真机复核）
      4. 不涉及该能力 -> 不产出条目
    """
    items: list[dict] = []
    reg = renderer(renderer_name)
    if not reg:
        return items

    specs = _SPECS.get(str(renderer_name).strip().lower(), [])
    code = _strip_comments(script)
    for label, expected, rejected, triggers in specs:
        hit = next((n for n in expected if _uses(code, n)), None)
        if hit:
            items.append({
                "level": "ok",
                "title": f"真机类名对照:{label}",
                "detail": f"命中真机登记名「{hit}」",
            })
            continue
        bad = next((n for n in rejected if _uses(code, n)), None)
        touched = any(t in script for t in triggers)
        if bad:
            items.append({
                "level": "error",
                "title": f"真机类名对照:{label}",
                "detail": f"使用了真机不存在的名字「{bad}」，"
                          f"真机登记名为「{'/'.join(expected)}」——静态通过但运行会静默不生效",
            })
        elif touched:
            items.append({
                "level": "warn",
                "title": f"真机类名对照:{label}",
                "detail": f"脚本涉及该能力但未出现真机登记名「{'/'.join(expected)}」，"
                          f"依赖候选链兜底，建议真机复核",
            })
    return items


def known_names(renderer_name: str | None = None) -> set[str]:
    """汇总所有已知（真机存在）的类名/属性名，供脚本侧做「未登记名字」巡检"""
    data = load()
    rends = data.get("renderers", {})
    pool: list[dict] = []
    if renderer_name:
        r = rends.get(str(renderer_name).lower())
        if r:
            pool.append(r)
    else:
        pool.extend(rends.values())
    names: set[str] = set()
    for r in pool:
        for v in r.values():
            if isinstance(v, str):
                names.add(v)
            elif isinstance(v, list):
                names.update(x for x in v if isinstance(x, str))
    return names


def unknown_class_names(script: str, renderer_name: str | None = None) -> list[str]:
    """抽出脚本里「看着像类名但没在真机登记表里出现过」的候选链字面量。

    只扫候选链 `#("X", "Y")` 与 execute("X()") 两类写法，且只认首字母大写 / 含中文的项，
    避免把属性名（baseBumpTexmap 这类小写开头的）误判成类名。
    """
    known = known_names(renderer_name)
    cands: set[str] = set()
    for grp in re.findall(r"#\(([^)]*)\)", script):
        for s in re.findall(r"\"([^\"]+)\"", grp):
            cands.add(s)
    for s in re.findall(r"execute\s*\(\s*\"([^\"]+?)\s*\(\s*\)\"", script):
        cands.add(s)
    out = []
    for c in sorted(cands):
        if not c:
            continue
        first = c[0]
        looks_class = first.isupper() or ("\u4e00" <= first <= "\u9fff")
        if not looks_class:
            continue
        if c in known:
            continue
        out.append(c)
    return out
