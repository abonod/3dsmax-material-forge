#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MaxScript 静态体检 —— 本机无法无头运行 3ds Max，交付前的唯一自动校验手段。

检查项：
  1. 文件编码（MaxScript 在中文 Windows 上按 ANSI/GBK 解析，UTF-8 BOM 会乱码）
  2. 括号 / 引号是否平衡（跳过注释与字符串内容）
  3. 关键 API 是否齐全（材质创建、贴图、材质库、反射式写入）
  4. 凹凸/法线通道语义（凹凸槽必须挂高度图，法线图只走 normal 槽）
  5. 脚本内引用的贴图绝对路径是否真实存在

用法：
    python verify_ms.py <file.ms | 目录>
退出码：0 = 全部通过；1 = 存在错误级问题。
"""

from __future__ import annotations

import os
import re
import sys

# --- 关键 API 白名单：缺失即报错级 ---
# 材质库一项曾写 saveTempMaterialLibrary，但 3ds Max 2024 已移除该接口
# （连 newMaterialLibrary / loadTempMaterialLibrary 一起没了），
# 继续按老接口判断会让每份脚本恒定吃一条 WARN，等于给体检器自带噪声。
REQUIRED_API = {
    "材质创建": ["VRayMtl", "PhysicalMaterial", "StandardMaterial"],
    "贴图加载": ["Bitmaptexture", "bitmap"],
    "材质库": ["loadMaterialLibrary", "saveMaterialLibrary"],
    "反射式写入": ["getPropNames", "hasProperty", "setProperty"],
    "错误兜底": ["catch"],
}

# 贴图路径：C:\xxx\yyy.png 这类绝对路径
# 同时兼容 @"D:/a/b.png"、@"D:\\a\\b.png"、相对路径不入内
TEX_PATH_RE = re.compile(r'@?"([A-Za-z]:[\\/][^"]+?\.(?:png|jpe?g|tga|exr|hdr|bmp|tif{1,2}))"', re.I)


def read_script(path: str) -> tuple[str, str, bool]:
    """返回 (文本, 编码描述, 是否致命 BOM)。

    判定顺序模拟 3ds Max 的实际行为：中文 Windows 上它按 ANSI/GBK 解析，
    所以 GBK 能解通就算正常；带 UTF-8 BOM 的文件 GBK 必然解不通，才是致命问题。
    """
    raw = open(path, "rb").read()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    gbk_ok = True
    try:
        gbk_text = raw.decode("gbk")
    except UnicodeDecodeError:
        gbk_ok = False
        gbk_text = ""
    try:
        utf8_text = raw.decode("utf-8-sig")
        utf8_ok = True
    except UnicodeDecodeError:
        utf8_ok = False
        utf8_text = ""

    if has_bom:
        # BOM + GBK 解不通 = 典型的 UTF-8 with BOM，Max 里易乱码
        return (utf8_text if utf8_ok else raw.decode("gbk", errors="replace"),
                "utf-8 with BOM", True)
    if gbk_ok and utf8_ok:
        return gbk_text, "兼容（GBK/UTF-8 均可解码，按 GBK 读取）", False
    if gbk_ok:
        return gbk_text, "gbk", False
    if utf8_ok:
        return utf8_text, "utf-8（无 BOM）", False
    return raw.decode("gbk", errors="replace"), "gbk?（含非法字节）", False


def strip_noise(src: str) -> str:
    """去掉 -- 行注释、/* */ 块注释和字符串内容，只留下结构字符。"""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            out.append('"')
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if src[i] == '"':
                    out.append('"')
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


def check_balance(clean: str) -> list[str]:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    errors: list[str] = []
    line = 1
    for ch in clean:
        if ch == "\n":
            line += 1
        elif ch in "([{":
            stack.append((ch, line))
        elif ch in ")]}":
            if not stack:
                errors.append(f"第 {line} 行出现多余的 '{ch}'")
            elif stack[-1][0] != pairs[ch]:
                op, ln = stack.pop()
                errors.append(f"第 {ln} 行的 '{op}' 与第 {line} 行的 '{ch}' 不匹配")
            else:
                stack.pop()
    for op, ln in stack:
        errors.append(f"第 {ln} 行的 '{op}' 未闭合")
    if clean.count('"') % 2 != 0:
        errors.append("双引号数量为奇数，可能存在未闭合字符串")
    return errors


def check_api(src: str) -> list[str]:
    missing: list[str] = []
    for label, names in REQUIRED_API.items():
        hit = [n for n in names if n in src]
        if not hit:
            missing.append(f"{label}：未找到 {' / '.join(names)} 中的任何一个")
    return missing


def check_hashquote(src: str) -> list[str]:
    """MaxScript 不支持 #"字符串" 形式的 name 字面量。

    实测 Max 2024 报：-- 语法错误: 位于 string，需要 "(" or "["
    只允许裸写 #标识符，否则必须改用 (toName "xxx")。
    """
    bad: list[str] = []
    for i, line in enumerate(src.splitlines(), 1):
        code = line.split("--")[0]
        if '#"' in code:
            bad.append(f"第 {i} 行: {line.strip()[:80]}")
    return bad


# --- bump 通道语义：凹凸槽必须挂高度图，法线图只能走 normal 槽（evals id=1 硬约束）---
NB_CALL_RE = re.compile(
    r'mfAttachNormalBump\s+\S+\s+@?("(?:[^"\\]|\\.)*")\s+'
    r'(?:@?("(?:[^"\\]|\\.)*")|undefined)')
TEX_CALL_RE = re.compile(
    r'mfAttachTex\s+\S+\s+#\(([^)]*)\)\s+@?("(?:[^"\\]|\\.)*")')

HEIGHT_WORDS = ("height", "hgt", "bump", "disp", "凹凸", "高度")
NORMAL_WORDS = ("normal", "nrm", "法线")


def unquote_ms(lit: str) -> str:
    """去掉 MaxScript 字符串两端的引号、前导 @ 与转义。"""
    s = lit.strip()
    if s.startswith("@"):
        s = s[1:]
    return s.strip('"').replace("\\\\", "\\")


def classify_map(path: str) -> str:
    """按文件名判定贴图语义通道：height / normal / unknown。"""
    base = os.path.basename(path.replace("\\", "/")).lower()
    if not base:
        return "unknown"
    if any(w in base for w in NORMAL_WORDS):
        return "normal"
    if any(w in base for w in HEIGHT_WORDS):
        return "height"
    return "unknown"


def check_bump_channel(src: str) -> tuple[list[str], list[str], list[str]]:
    """校验凹凸/法线通道的挂载语义。

    返回 (错误, 提示, 通过项)。判定依据是文件名语义，因为脚本里
    贴图路径由 texture_engine 按通道名落盘（height.png / normal.png）。
    """
    errs: list[str] = []
    warns: list[str] = []
    oks: list[str] = []
    bump_single: list[str] = []
    has_height_map = False

    for m in NB_CALL_RE.finditer(src):
        nmap = unquote_ms(m.group(1))
        cn = classify_map(nmap)
        if cn == "height":
            errs.append(f"VRayNormalMap 的 normal 子槽挂了高度图：{os.path.basename(nmap)}")
        elif cn == "unknown":
            warns.append(f"VRayNormalMap 的 normal 子槽贴图无法按文件名判定通道：{os.path.basename(nmap)}")
        if m.group(2) is None:
            # 第二参为 undefined：只有法线、没有高度图，bump 子槽留空
            if cn == "normal":
                oks.append("仅法线：已用 VRayNormalMap 包装（normal 槽=法线图，bump 子槽留空）")
        else:
            hmap = unquote_ms(m.group(2))
            ch = classify_map(hmap)
            if ch == "normal":
                errs.append(f"VRayNormalMap 的 bump 子槽挂了法线图（凹凸槽应挂高度图）：{os.path.basename(hmap)}")
            elif ch == "unknown":
                warns.append(f"VRayNormalMap 的 bump 子槽贴图无法按文件名判定通道：{os.path.basename(hmap)}")
            else:
                has_height_map = True
            if cn == "normal" and ch == "height":
                oks.append("法线与凹凸分槽正确（normal 槽=法线图，bump 槽=高度图）")

    for m in TEX_CALL_RE.finditer(src):
        slots = [s.strip().strip('"').strip().lower()
                 for s in m.group(1).split(",") if s.strip()]
        path = unquote_ms(m.group(2))
        kind = classify_map(path)
        if kind == "height":
            has_height_map = True
        if "bump" in slots:
            bump_single.append(f"{os.path.basename(path)}（语义:{kind}）")

    if bump_single and NB_CALL_RE.search(src):
        warns.append("bump 槽既有叠加节点又有单图挂载，后写入的会覆盖前者："
                     + "、".join(bump_single))

    for item in bump_single:
        base = item.split("（")[0]
        if "语义:height" in item:
            oks.append(f"凹凸槽挂高度图正确：{base}")
        elif "语义:normal" in item:
            if has_height_map:
                errs.append(f"凹凸槽挂了法线图（同脚本已有高度图，应挂高度图）：{base}")
            else:
                warns.append(f"凹凸槽挂了法线图且无高度图可用（单图降级，非标准但可出凹凸）：{base}")
        else:
            warns.append(f"凹凸槽贴图无法按文件名判定通道，请人工确认是否为高度图：{base}")

    if not NB_CALL_RE.search(src) and not bump_single:
        warns.append("未发现任何凹凸/法线挂载语句（纯参数材质可忽略）")
    return errs, warns, oks


def check_textures(src: str) -> tuple[list[str], list[str]]:
    paths = sorted(set(TEX_PATH_RE.findall(src)))
    exist, lack = [], []
    for p in paths:
        (exist if os.path.exists(p) else lack).append(p)
    return exist, lack


def verify(path: str) -> bool:
    src, enc, bom = read_script(path)
    print(f"\n=== {path} ===")
    print(f"  编码: {enc}{' (带 BOM，Max 里易乱码)' if bom else ''}  行数: {src.count(chr(10)) + 1}")

    ok = True
    if bom:
        print("  [ERR] 文件带 UTF-8 BOM，MaxScript 可能解析异常，请另存为 ANSI/GBK")
        ok = False

    bal = check_balance(strip_noise(src))
    if bal:
        ok = False
        print(f"  [ERR] 结构不平衡 {len(bal)} 处：")
        for e in bal[:10]:
            print(f"        - {e}")
    else:
        print("  [OK ] 括号/引号平衡")

    hq = check_hashquote(src)
    if hq:
        ok = False
        print(f"  [ERR] 非法 name 字面量 #\"...\" {len(hq)} 处（Max 会报“位于 string，需要 \"(\" or \"[\"”，须改为 #标识符）：")
        for e in hq[:10]:
            print(f"        - {e}")
    else:
        print("  [OK ] name 字面量写法合法（无 #\"...\"）")

    miss = check_api(src)
    if miss:
        print(f"  [WARN] 关键 API 缺失 {len(miss)} 项：")
        for m in miss:
            print(f"        - {m}")
    else:
        print("  [OK ] 关键 API 齐全")

    bump_err, bump_warn, bump_ok = check_bump_channel(src)
    if bump_err:
        ok = False
        print(f"  [ERR] 凹凸/法线通道语义错误 {len(bump_err)} 处（凹凸必须挂高度图）：")
        for e in bump_err:
            print(f"        - {e}")
    if bump_warn:
        print(f"  [WARN] 凹凸/法线通道 {len(bump_warn)} 处需确认：")
        for w in bump_warn:
            print(f"        - {w}")
    if bump_ok and not bump_err:
        print(f"  [OK ] 凹凸/法线通道语义正确：{'；'.join(bump_ok[:3])}")

    exist, lack = check_textures(src)
    if exist:
        print(f"  [OK ] 贴图路径有效 {len(exist)} 个")
    if lack:
        ok = False
        print(f"  [ERR] 贴图路径不存在 {len(lack)} 个（移动 output 目录会导致失效）：")
        for p in lack[:10]:
            print(f"        - {p}")
    if not exist and not lack:
        print("  [INFO] 脚本内未引用外部贴图（纯参数材质）")

    print("  结论:", "通过" if ok else "存在错误，修复后再交付")
    return ok


def main() -> int:
    targets = sys.argv[1:]
    if not targets:
        print(__doc__)
        return 2
    files: list[str] = []
    for t in targets:
        if os.path.isdir(t):
            for root, _dirs, names in os.walk(t):
                files += [os.path.join(root, f) for f in sorted(names) if f.lower().endswith(".ms")]
        elif t.lower().endswith(".ms"):
            files.append(t)
        else:
            print(f"跳过（不是 .ms）: {t}")
    if not files:
        print("未找到任何 .ms 文件")
        return 2
    results = [verify(f) for f in files]
    print(f"\n总计 {len(files)} 个文件，通过 {sum(results)} 个")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
