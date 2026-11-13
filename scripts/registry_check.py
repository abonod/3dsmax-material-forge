# -*- coding: utf-8 -*-
"""参数注册表守卫：核对 references/params-registry.md 与 core/params.py 是否一致。

为什么需要它：
    注册表是给人（和未来的自己）看的文档，最容易在"加了个参数"之后忘记同步，
    结果文档说 61 项、代码已经 63 项，排障时按文档找参数会扑空。
    这个脚本把"文档和代码是否同源"变成一条可执行的断言。

核对四件事：
    1. 分组数量：六个分组的参数数逐个比对
    2. id 覆盖：每个参数 id 是否在文档里出现过（缺一个就报）
    3. 档位划分：新手 / 中级 / 进阶 三档互斥、并集等于全部参数
    4. 过时表述：文档里不该再出现"两种模式""35 项折叠"这类旧口径

退出码：0 = 全部一致；1 = 存在不一致。
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

from core import params as P  # noqa: E402

DOC = ROOT + r"\references\params-registry.md"

# 旧口径：这些词出现在文档里就说明没跟着三档改造更新
STALE_WORDS = ("面板有两种模式", "## 分组与双模式", "其余 35 项")


def main() -> int:
    doc = open(DOC, encoding="utf-8").read()
    fails: list[str] = []

    # 1) 分组数量
    print("== 分组数量 ==")
    doc_cnt: dict[str, int] = {}
    for name, n in re.findall(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|", doc, re.M):
        name = name.strip()
        if name in ("分组", "id", "档位") or set(name) <= set("-: "):
            continue
        doc_cnt[name] = int(n)
    code_cnt = collections.Counter(p.group for p in P.PARAMS)
    for g in P.GROUP_ORDER:
        a, b = code_cnt.get(g, 0), doc_cnt.get(g)
        ok = a == b
        print(f"  [{'OK ' if ok else 'DIFF'}] {g}: 代码={a} 文档={b}")
        if not ok:
            fails.append(f"分组「{g}」数量不一致：代码 {a} / 文档 {b}")
    print(f"  合计: 代码={len(P.PARAMS)}")

    # 2) id 覆盖
    print("== id 覆盖 ==")
    # 文档里 id 通常被反引号包着（`uv_tiling`），所以左右边界只排除"字母数字下划线"，
    # 不能把反引号算作边界字符，否则表里写得好好的 id 会被判成"没提及"。
    missing = [p.id for p in P.PARAMS
               if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(p.id)}(?![A-Za-z0-9_])", doc)]
    print("  未被文档提及的 id:", missing or "无")
    if missing:
        fails.append(f"{len(missing)} 个参数未写进文档：{', '.join(missing[:8])}")

    # 3) 档位划分
    print("== 档位划分 ==")
    beg, mid = set(P.BEGINNER_IDS), set(P.INTERMEDIATE_IDS)
    allids = {p.id for p in P.PARAMS}
    adv = allids - beg - mid
    print(f"  新手={len(beg)} 中级={len(mid)} 进阶={len(adv)} 合计={len(beg) + len(mid) + len(adv)}")
    if beg & mid:
        fails.append(f"新手档与中级档重叠：{sorted(beg & mid)}")
    if beg | mid | adv != allids:
        fails.append("三档并集不等于全部参数（存在未归档参数）")
    unknown = (beg | mid) - allids
    if unknown:
        fails.append(f"档位里出现不存在的 id：{sorted(unknown)}")

    # 4) 过时表述
    print("== 过时表述 ==")
    stale = []
    for line_no, line in enumerate(doc.splitlines(), 1):
        for kw in STALE_WORDS:
            if kw in line:
                stale.append(f"L{line_no} [{kw}] {line.strip()[:60]}")
    print("  " + ("\n  ".join(stale) if stale else "无"))
    if stale:
        fails.append(f"文档残留 {len(stale)} 处旧口径")

    print()
    if fails:
        print("结论：FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("结论：PASS（文档与代码一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
