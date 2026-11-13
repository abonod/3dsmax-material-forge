# -*- coding: utf-8 -*-
"""程序化纹理场 —— 不依赖任何图片的「材质微结构」生成器

为什么需要这一层：
    旧管线把 height / normal / roughness 全部从「上传图片的亮度」派生。
    纯色图片（乳胶漆最典型的输入就是一张色卡）亮度恒定 -> 高度恒为 0 ->
    法线全平 -> 粗糙度也全平，于是纯色乳胶漆在 V-Ray 里就是个塑料球，
    粗糙感完全消失。而乳胶漆真正的「哑面微粗糙」在渲染里主要来自两件事：
        1) 高光破碎（roughness 在 0.5~0.75 之间小幅抖动）——贡献最大
        2) 极轻的滚涂橘皮（height 起伏，法线强度 0.3~0.6）
    这两件事都可以用「与图像无关、且可无缝平铺」的噪声场合成。

关键约束：无缝平铺
    UV 平铺（uv_tiling）> 1 时，贴图左右/上下边界必须连续，否则墙面会出现
    规则网格接缝。常规 value-noise 做不到；这里用频域合成：
        白噪声 FFT -> 径向带通滤波 -> 逆 FFT
    频域滤波等价于循环卷积，输出天然以图像尺寸为周期，因此 100% 无缝。

对外只暴露 latex_field()：给定 (h, w, kind, scale, seed, mask, mask_scale) 返回 0~1 的场
    height  起伏（喂给 derive_normal / derive_ao）
    rough   粗糙度扰动（喂给 derive_roughness 的调制项）
    albedo  低频明暗斑驳（喂给漫反射的轻微起伏，避免「死板纯色」）
    mask    低频区域遮罩（喂给 roughness 的分段偏移，也用来核对无缝性）
所有通道都是 float32、形状 (h, w)，无第三方依赖（只用 numpy）。

均匀麻面的局限：
    bands 合成的只是「各向同性的麻面」，一张图铺满整面墙，每一段墙都一模一样，
    一眼就能看出复制粘贴。真实乳胶漆墙面有大尺度的空间不匀（刷痕流挂、板缝两侧
    的浓淡、局部脏污与补漆），必须是多尺度的低频分量——见 region_mask()。
"""

from __future__ import annotations

import numpy as np

# 纹理类型 -> (各频带循环数, 权重, 各向异性拉伸, 形状指数)
#   循环数 = 该频带在整张贴图里重复几次；除以 scale 后即为实际使用值
#   各向异性 > 1 表示沿 X 方向拉长（滚涂的滚轮走向）
KINDS: dict[str, dict] = {
    # 滚涂橘皮：中频团块为主，轻微横向拉伸，是乳胶漆的默认形态
    "roller": {"bands": [(3.0, 0.22), (11.0, 0.50), (30.0, 0.28)],
               "aniso": 2.0, "shape": 0.85},
    # 喷涂：高频细粒为主，几乎无方向性，观感均匀哑面
    "spray": {"bands": [(2.0, 0.14), (16.0, 0.46), (46.0, 0.40)],
              "aniso": 1.0, "shape": 1.0},
    # 细砂 / 艺术漆：高频密集 + 中频轻起伏，颗粒感明确
    "sand": {"bands": [(4.0, 0.18), (20.0, 0.44), (64.0, 0.38)],
             "aniso": 1.0, "shape": 1.35},
    # 拉毛 / 肌理漆：低频大团块 + 高频毛刺，起伏最大
    "stucco": {"bands": [(2.5, 0.46), (8.0, 0.34), (28.0, 0.20)],
               "aniso": 1.0, "shape": 1.5},
}

CYCLE_MIN, CYCLE_MAX = 1.5, 200.0


def _robust_norm(a: np.ndarray, lo_p: float = 0.5, hi_p: float = 99.5) -> np.ndarray:
    """按分位裁剪后归一化，避免少数孤立极值把整张图压扁"""
    lo = float(np.percentile(a, lo_p))
    hi = float(np.percentile(a, hi_p))
    if hi - lo < 1e-8:
        return np.zeros_like(a, dtype=np.float32)
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def _band(h: int, w: int, cycles: float, seed: int, aniso: float = 1.0,
          cmin: float = CYCLE_MIN) -> np.ndarray:
    """单个倍频程的带通噪声，频域实现，天然无缝平铺"""
    cycles = float(np.clip(cycles, cmin, min(CYCLE_MAX, min(h, w) / 3.0)))
    rng = np.random.default_rng(seed)
    white = rng.standard_normal((h, w)).astype(np.float32)
    F = np.fft.rfft2(white)

    fy = np.fft.fftfreq(h)[:, None] * h           # 纵向循环数
    fx = np.fft.rfftfreq(w)[None, :] * w          # 横向循环数
    # 各向异性：X 方向频率被压低 -> 纹理沿 X 拉长
    fe = np.sqrt((fx / max(aniso, 1e-3)) ** 2 + (fy * max(aniso, 1e-3)) ** 2)
    fe = np.maximum(fe, 1e-3)

    # 对数高斯带通：中心 cycles，宽度约 ±0.7 个倍频程
    logr = np.log(fe / cycles)
    amp = np.exp(-0.5 * (logr / 0.7) ** 2)
    amp = amp / np.power(fe, 0.35)                # 轻微压高频，避免塑料颗粒感
    amp[0, 0] = 0.0                               # 去掉直流分量

    out = np.fft.irfft2(F * amp, s=(h, w)).astype(np.float32)
    return _robust_norm(out)


def _combine(h: int, w: int, bands, scale: float, seed: int, aniso: float) -> np.ndarray:
    scale = float(max(scale, 0.05))
    acc = np.zeros((h, w), dtype=np.float32)
    for i, (cyc, weight) in enumerate(bands):
        acc += _band(h, w, cyc / scale, seed + 977 * i, aniso) * float(weight)
    return _robust_norm(acc)


# ---------------- 低频区域遮罩 ----------------
# 为什么要这一层：上面的 bands 只给出「均匀麻面」——同一张图铺满整面墙，每一段墙都长得一样，
# 一眼就是复制粘贴。真实乳胶漆墙面存在大尺度的空间不匀：
#   1) 滚筒起落/收边留下的竖向刷痕与流挂；
#   2) 石膏板接缝、阴阳角两侧的浓淡差；
#   3) 局部脏污、补漆、打磨过度的深浅。
# 这三样都是低频、大尺度的，与麻面（中高频）根本不在一个频段。
# 做法：叠 3 个尺度各差一档的低频带通噪声，全部来自频域循环卷积，仍然 100% 无缝；
#       再做分位拉伸 + S 曲线，让遮罩「要么接近 0、要么接近 1」，形成成片分区，
#       而不是整体发灰的一层雾。
# (循环数, 权重, 各向异性)：aniso < 1 表示沿 Y 方向拉长 -> 竖向流挂/刷痕
MASK_LAYERS = [(1.6, 0.56, 1.00), (4.2, 0.29, 0.26), (8.8, 0.15, 1.00)]
MASK_CYCLE_MIN = 0.8


def _scurve(a: np.ndarray, k: float = 2.4) -> np.ndarray:
    """中段线性拉伸 + smoothstep：把遮罩推向两端，得到成片的「有/无」区域"""
    x = np.clip((np.asarray(a, dtype=np.float32) - 0.5) * float(k) + 0.5, 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


# ---------------- 遮罩的世界尺度换算 ----------------
# 贴图按真实世界尺度铺（UVW 平面贴图下 1 个 UV 单位 ≈ 1 米）时，遮罩区块尺寸与 uv_tiling
# 强耦合：tiling 越大，一块贴图覆盖的真实面积越小，同一张遮罩会按 tiling 次数原样复读。
# 这里把「米制区块尺寸」换算成「每块贴图内的循环数」，把耦合从参数语义里拆出去。
UV_UNIT_M = 1.0        # 1 个 UV 单位近似 1 米
MASK_BLOCK_M = 1.0     # proc_mask_scale=1.0 对应约 1 米区块
MASK_CPT_FLOOR = 1.0   # 每块贴图至少保留 1 个完整循环，再低就不是「区块」而是整块倾斜


def mask_geometry(mask_scale: float = 1.0, uv_tiling: float = 1.0,
                  uv_unit_m: float = UV_UNIT_M) -> dict:
    """米制遮罩尺寸 -> 贴图空间循环数，并给出复读风险判定

    block_m          用户要的区块世界尺寸
    tile_m           一块贴图覆盖的世界尺寸
    cycles_per_tile  实际使用的每块贴图循环数（已卡下限）
    block_m_actual   卡下限后能兑现的区块尺寸
    clamped          目标区块放不进一块贴图（必然按 tiling 次数复读）
    damp             复读风险下的强度衰减系数
    """
    block_m = max(float(mask_scale), 0.05) * MASK_BLOCK_M
    tile_m = float(uv_unit_m) / max(float(uv_tiling), 0.05)
    want = tile_m / block_m
    cpt = max(want, MASK_CPT_FLOOR)
    clamped = want < MASK_CPT_FLOOR - 1e-6
    # 目标区块放不进一块贴图时必须削弱强度：图案会整块复读，越强越像贴了张重复的纸
    damp = 1.0 if not clamped else max(0.35, want / MASK_CPT_FLOOR)
    return {
        "block_m": round(block_m, 3),
        "tile_m": round(tile_m, 3),
        "cycles_per_tile": round(cpt, 3),
        "block_m_actual": round(tile_m / cpt, 3),
        "clamped": bool(clamped),
        "damp": round(damp, 3),
    }


def region_mask(h: int, w: int, scale: float = 1.0,
                seed: int = 7,
                cycles_per_tile: float | None = None) -> dict[str, np.ndarray]:
    """低频区域遮罩场（0~1，无缝）

    mask   : 综合遮罩（大区域 + 流挂 + 斑块的加权合成），决定哪里麻面完整、哪里被抹平
    streak : 竖向拉长的流挂/刷痕分量，供调试与后续单独使用
    dirt   : 中频脏污/补漆斑块

    scale          : 米制区块倍数（旧语义，循环数 = 基准 / scale）
    cycles_per_tile: 每块贴图内的循环数（新语义，由 mask_geometry 换算，
                     已含 uv_tiling 的世界尺度补偿），给出时优先于 scale
    """
    mult = float(cycles_per_tile) if cycles_per_tile else 1.0 / float(max(scale, 0.2))
    mult = max(float(mult), 1e-3)
    acc = np.zeros((h, w), dtype=np.float32)
    streak = np.zeros((h, w), dtype=np.float32)
    for i, (cyc, weight, aniso) in enumerate(MASK_LAYERS):
        b = _band(h, w, cyc * mult, int(seed) + 8209 + 977 * i, aniso, cmin=MASK_CYCLE_MIN)
        if i == 1:
            streak = b
        acc += b * float(weight)
    mask = _scurve(_robust_norm(acc, 1.0, 99.0), 2.4)
    dirt = _scurve(_robust_norm(
        _band(h, w, 6.0 * mult, int(seed) + 9403, 1.0, cmin=MASK_CYCLE_MIN), 2.0, 98.0), 3.0)
    return {"mask": mask, "streak": streak, "dirt": dirt}


def latex_field(h: int, w: int, kind: str = "roller", scale: float = 1.0,
                seed: int = 7, mask: float = 0.6,
                mask_scale: float = 1.0,
                mask_field: dict | None = None,
                mask_amount: float | None = None) -> dict[str, np.ndarray]:
    """生成乳胶漆类材质的程序化场

    kind  : roller / spray / sand / stucco（未知值回落 roller）
    scale : 纹理尺寸倍数，>1 更粗（滚涂大橘皮），<1 更细
    seed  : 随机种子，同参数同结果（可复现）
    mask  : 低频区域遮罩强度 0~1（0=均匀麻面，0.6=乳胶漆默认，1=分区最明显）
    mask_scale : 遮罩特征尺寸倍数，>1 区块更大更整，<1 更碎
    mask_field : 外部算好的遮罩场（engine 层统一生成，供图/纯色两种模式共用同一张遮罩）
    mask_amount: 外部算好的遮罩强度（已含 uv_tiling 复读衰减），给出时覆盖 mask
    """
    cfg = KINDS.get(str(kind or "").strip().lower(), KINDS["roller"])
    bands = cfg["bands"]
    aniso = float(cfg["aniso"])
    shape_p = float(cfg["shape"])
    raw_ms = mask_amount if mask_amount is not None else (mask if mask is not None else 0.0)
    ms = float(np.clip(float(raw_ms), 0.0, 1.0))
    if mask_field is not None and ms > 1e-4:
        mf = mask_field
    elif ms > 1e-4:
        mf = region_mask(h, w, mask_scale, int(seed) + 5501)
    else:
        mf = None

    # ---- 高度场：多倍频叠加 + 形状塑形 ----
    height = _combine(h, w, bands, scale, int(seed), aniso)
    # 塑形：指数 > 1 让高点更尖（砂粒/毛刺），< 1 让高点更圆（橘皮团块）
    height = np.power(np.clip(height, 0.0, 1.0), shape_p)
    height = _robust_norm(height)

    # ---- 粗糙度扰动：独立种子，避免与高度完全相关（否则高光与凹凸同相，很假）----
    rough = _combine(h, w, [(6.0, 0.35), (22.0, 0.45), (70.0, 0.20)],
                     scale, int(seed) + 3011, 1.0)

    # ---- 漫反射低频斑驳：只取低频，幅度由参数控制 ----
    albedo = _combine(h, w, [(1.6, 0.7), (5.0, 0.3)], 1.0, int(seed) + 7717, 1.0)

    # ---- 低频区域遮罩：大块空间不匀，让不同墙段不再是复制粘贴 ----
    if mf is None:
        mask_field = np.full((h, w), 0.5, dtype=np.float32)
    else:
        mask_field = mf["mask"]
        # 高度：遮罩低谷把麻面压平（涂薄/打磨过），峰区保留完整橘皮，
        # 同时遮罩本身带一点低频起伏（刷痕/流挂造成的宏观厚薄）。
        # 关键：调制后按原场的起伏总量重新等比缩放，而不是再做分位拉伸——
        # 分位拉伸会把分区对比压回去（实测比值从 1.37 掉到 1.1 以下），等比缩放才留得住分区。
        base_std = float(np.std(height - 0.5)) or 0.16
        rel = ((height - 0.5) * (1.0 - 0.88 * ms * (1.0 - mf["mask"]))
               + (mf["mask"] - 0.5) * 0.30 * ms)
        cur_std = float(np.std(rel)) or 1e-6
        rel = rel * (base_std / cur_std)
        height = np.clip(0.5 + rel, 0.0, 1.0)
        # 漫反射：遮罩与脏污斑块合成低频明暗，交给 proc_albedo 控制幅度
        blocking = 0.58 * mf["mask"] + 0.42 * mf["dirt"]
        albedo = np.clip(0.35 * albedo + 0.65 * blocking, 0.0, 1.0)

    return {"height": height, "rough": rough, "albedo": albedo, "mask": mask_field}


def seamless_score(field: np.ndarray) -> float:
    """验收指标：左右/上下边界接缝强度

    返回「边界相邻差」与「内部相邻差」的比值。理想无缝 ≈ 1.0；
    有接缝时会显著大于 1（实测 value-noise 拼接约 6~20）。
    """
    a = np.asarray(field, dtype=np.float32)
    if a.ndim != 2 or a.shape[0] < 4 or a.shape[1] < 4:
        return 1.0
    inner_x = float(np.mean(np.abs(np.diff(a, axis=1))))
    inner_y = float(np.mean(np.abs(np.diff(a, axis=0))))
    edge_x = float(np.mean(np.abs(a[:, 0] - a[:, -1])))
    edge_y = float(np.mean(np.abs(a[0, :] - a[-1, :])))
    inner = max((inner_x + inner_y) * 0.5, 1e-6)
    edge = (edge_x + edge_y) * 0.5
    return float(edge / inner)
