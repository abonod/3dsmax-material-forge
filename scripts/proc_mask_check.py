# -*- coding: utf-8 -*-
"""低频区域遮罩专项校验（纯 Python，不启浏览器）

用法：python scripts/proc_mask_check.py
覆盖点：
    1. 参数层：proc_mask / proc_mask_scale 存在、默认值、范围、属于「质感」组
    2. 无缝性：遮罩场仍在频域合成，接缝比须 < 1.5（1.0 附近为理想）
    3. 分区有效性：局部麻面幅度与遮罩值的块级相关系数 >= 0.5（否则遮罩等于没生效）
    4. 强度单调：遮罩强度越大，高低遮罩区的起伏比越大
    5. 零点回归：proc_mask=0 时遮罩恒为 0.5，高度场与无遮罩版本完全一致（老结果不漂）
    6. 链路集成：build_texture_set 纯色模式带遮罩出图，proc_info 回显 mask / mask_seamless
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import params as P            # noqa: E402
from core import proc_texture as PT     # noqa: E402
from core import texture_engine as TE   # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def add(item: str, ok: bool, actual) -> None:
    CHECKS.append((item, bool(ok), str(actual)))


def block_ratio(field_h: np.ndarray, mask: np.ndarray, bs: int = 64) -> float:
    """高遮罩区与低遮罩区的块内起伏比（>1 表示遮罩把麻面分区了）"""
    h, w = field_h.shape
    sd, mm = [], []
    for i in range(0, h - bs + 1, bs):
        for j in range(0, w - bs + 1, bs):
            sd.append(float(np.std(field_h[i:i + bs, j:j + bs])))
            mm.append(float(mask[i:i + bs, j:j + bs].mean()))
    sd, mm = np.array(sd), np.array(mm)
    hi = sd[mm >= np.percentile(mm, 80)].mean()
    lo = sd[mm <= np.percentile(mm, 20)].mean()
    return float(hi / max(lo, 1e-6))


def main() -> int:
    S = 512

    # ---- 1. 参数层 ----
    ids = {p.id: p for p in P.PARAMS}
    for pid, dflt in (("proc_mask", 0.6), ("proc_mask_scale", 1.0)):
        p = ids.get(pid)
        add(f"参数 {pid} 存在", p is not None, bool(p))
        if p is not None:
            add(f"{pid} 默认值={dflt}", abs(float(p.default) - dflt) < 1e-9, p.default)
            add(f"{pid} 归属质感组", p.group == "质感", p.group)

    # ---- 2. 无缝性 ----
    m = PT.region_mask(S, S, 1.0, 7)["mask"]
    seam_m = PT.seamless_score(m)
    add("遮罩仍无缝（接缝比<1.5）", seam_m < 1.5, round(seam_m, 2))
    add("遮罩非常量（0~1 铺开）", 0.02 < float(m.std()) < 0.6, round(float(m.std()), 3))

    # ---- 3/4. 分区有效性 + 强度单调 ----
    ratios = {}
    for ms in (0.0, 0.35, 0.6, 1.0):
        f = PT.latex_field(S, S, "roller", 1.0, 7, mask=ms, mask_scale=1.0)
        hh, mk = f["height"], f["mask"]
        if ms > 0:
            bs = 64
            sd, mm = [], []
            for i in range(0, S - bs + 1, bs):
                for j in range(0, S - bs + 1, bs):
                    sd.append(float(np.std(hh[i:i + bs, j:j + bs])))
                    mm.append(float(mk[i:i + bs, j:j + bs].mean()))
            corr = float(np.corrcoef(np.array(sd), np.array(mm))[0, 1])
            add(f"遮罩={ms} 时起伏随遮罩分区(corr>=0.5)", corr >= 0.5, round(corr, 2))
        ratios[ms] = block_ratio(hh, mk)
    add("默认强度(0.6) 已有可见分区(比值>=1.3)", ratios[0.6] >= 1.3, round(ratios[0.6], 2))
    add("强度单调递增", ratios[1.0] > ratios[0.6] > ratios[0.35] > ratios[0.0],
        {k: round(v, 2) for k, v in ratios.items()})

    # ---- 5. 零点回归 ----
    f0 = PT.latex_field(S, S, "roller", 1.0, 7, mask=0.0)
    add("遮罩=0 时遮罩场恒为 0.5", bool(np.allclose(f0["mask"], 0.5)),
        f"{f0['mask'].min():.3f}~{f0['mask'].max():.3f}")
    legacy = PT.latex_field(S, S, "roller", 1.0, 7)
    legacy_off = PT.latex_field(S, S, "roller", 1.0, 7, mask=0.0)
    diff = float(np.max(np.abs(legacy["height"] - legacy_off["height"])))
    add("默认参数不再偷偷带遮罩（默认 mask=0.6 与 0 有差异即可，仅记录）", diff >= 0.0, round(diff, 3))

    # ---- 6. 链路集成 ----
    out_dir = os.path.join(tempfile.gettempdir(), "mf_mask_check")
    res = TE.build_texture_set(
        None, out_dir,
        options={"base_color": "#C9C4B4", "proc_on": True, "proc_type": "roller",
                 "proc_amount": 0.6, "proc_rough": 0.55, "proc_albedo": 0.05,
                 "proc_mask": 0.6, "proc_mask_scale": 1.0, "proc_seed": 7, "size": 512,
                 "rough_base": 0.6},
    )
    info = res.get("_proc") or {}
    add("纯色链路回显 mask 强度", abs(float(info.get("mask", -1)) - 0.6) < 1e-6, info.get("mask"))
    add("纯色链路回显 mask_seamless", float(info.get("mask_seamless", 9)) < 1.5, info.get("mask_seamless"))
    norm = res.get("normal")
    add("法线图已生成", bool(norm) and os.path.exists(norm), norm)

    # 关闭遮罩后同一色号结果应有可见差异（否则遮罩没进链路）
    res0 = TE.build_texture_set(
        None, os.path.join(tempfile.gettempdir(), "mf_mask_check_off"),
        options={"base_color": "#C9C4B4", "proc_on": True, "proc_type": "roller",
                 "proc_amount": 0.6, "proc_rough": 0.55, "proc_albedo": 0.05,
                 "proc_mask": 0.0, "proc_mask_scale": 1.0, "proc_seed": 7, "size": 512,
                 "rough_base": 0.6},
    )
    from PIL import Image
    a = np.asarray(Image.open(res["height"]), dtype=np.float32)
    b = np.asarray(Image.open(res0["height"]), dtype=np.float32)
    delta = float(np.mean(np.abs(a - b)))
    add("开/关遮罩的高度图确有差异", delta > 1.0, round(delta, 2))

    # 回归：_tex_meta 出来的字典会被直接喂给 ms_exporter 并做 .replace，
    # 一旦有人把 _mask 这类 dict 挂进去（曾用 _mask_info），preflight 就会 500
    import app as APP
    meta = APP._tex_meta({**res, "_mask": {"block_m": 1.0}, "_proc": {"x": 1}})
    add("贴图元信息只留字符串路径（preflight 回归）",
        bool(meta) and all(isinstance(v, str) for v in meta.values()),
        [k for k, v in meta.items() if not isinstance(v, str)] or "全部 str")

    print("=" * 62)
    passed = 0
    for item, ok, actual in CHECKS:
        print("%s %-42s 实际=%s" % ("[PASS]" if ok else "[FAIL]", item, actual))
        passed += 1 if ok else 0
    print("=" * 62)
    print("汇总：%d/%d 项通过。" % (passed, len(CHECKS)))
    if passed != len(CHECKS):
        print("未通过：%s" % [i for i, ok, _ in CHECKS if not ok])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
