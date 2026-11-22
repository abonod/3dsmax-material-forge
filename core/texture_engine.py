"""贴图推导引擎 —— 单张图片 -> PBR 贴图集

本地程序化路径（Q2 方案 C 的底座）：不依赖 GPU / CUDA / 联网。
输出通道：
    albedo    漫反射（原图，可做轻微去光照）
    height    高度（亮度 + 频率混合）
    normal    法线（由 height 用 Sobel 推导，OpenGL 约定 G=+Y，匹配 V-Ray / three.js）
    roughness 粗糙度（亮度反相 + 局部细节密度）
    ao        环境遮蔽（高度局部均值差近似）

AI 增强走 ai_enhance.py，失败自动回落本模块。
"""

from __future__ import annotations

import os
import shutil
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

from . import proc_texture

MAX_SIDE = 2048


def _size_plan(ow: int, oh: int) -> dict:
    """原图超过 MAX_SIDE 时，算出真实落盘尺寸并给出说明

    上限刻意不放开：单边 2048 对本机 V-Ray / Corona 的墙面近景够用，再大会让
    PIL 派生链（法线 / AO / 程序化场）耗时翻倍、.ms 打包体积暴涨。
    但静默缩放不可接受——用户传 6000px 原图，会默认导出也是 6000px。
    所以这里如实算出目标尺寸，由界面显式提示，不做「悄悄缩了不说」。
    """
    if max(ow, oh) <= MAX_SIDE:
        return {"downscaled": False, "original": [ow, oh], "target": [ow, oh],
                "max_side": MAX_SIDE}
    scale = MAX_SIDE / max(ow, oh)
    tw, th = max(1, int(ow * scale)), max(1, int(oh * scale))
    return {"downscaled": True, "original": [ow, oh], "target": [tw, th],
            "max_side": MAX_SIDE,
            "note": "原图 %d×%d 超过单边 %dpx 上限，已等比缩到 %d×%d 再派生通道；"
                    "近景细节以 %dpx 为准，需要更高精度请先裁切原图、只留要用的区域"
                    % (ow, oh, MAX_SIDE, tw, th, MAX_SIDE)}


# ---------------------------- 基础工具 ----------------------------

def _load_rgb(path: str) -> Image.Image:
    im = Image.open(path).convert("RGB")
    w, h = im.size
    scale = min(1.0, MAX_SIDE / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return im


def _to_arr(im: Image.Image) -> np.ndarray:
    return np.asarray(im, dtype=np.float32) / 255.0


def _to_img(arr: np.ndarray) -> Image.Image:
    arr = np.clip(arr, 0.0, 1.0)
    return Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8))


def _luma(arr: np.ndarray) -> np.ndarray:
    # Rec.709 亮度
    return (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2])


def _gauss(a: np.ndarray, radius: float) -> np.ndarray:
    """单通道高斯模糊（借 PIL，无 scipy 依赖）"""
    if radius <= 0:
        return a.copy()
    img = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    img = img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(img, dtype=np.float32) / 255.0


def _normalize(a: np.ndarray) -> np.ndarray:
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-6:
        return np.zeros_like(a)
    return (a - lo) / (hi - lo)


def _sobel(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """3x3 Sobel 梯度，边界用边缘复制"""
    p = np.pad(gray, 1, mode="edge")
    # | -1 0 1 |  水平梯度
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    # | -1 -2 -1 | 垂直梯度 转置形式
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return gx, gy


# ---------------------------- 通道推导 ----------------------------

def derive_albedo(arr: np.ndarray, decontaminate: bool = True) -> np.ndarray:
    """漫反射：适度去掉图片自带的明暗光照，让材质更"平"，反射由渲染器负责"""
    if not decontaminate:
        return arr.copy()
    lum = _luma(arr)[..., None]
    flat = _gauss(lum[..., 0], radius=max(arr.shape[0], arr.shape[1]) * 0.02)[..., None]
    flat = np.clip(flat, 0.08, 0.92)
    out = arr / flat * float(np.mean(flat))
    # 保留一点原始明暗，避免完全死板
    out = out * 0.75 + arr * 0.25
    return np.clip(out, 0.0, 1.0)


def derive_height(arr: np.ndarray, detail: float = 1.0) -> np.ndarray:
    """高度：亮度基线 + 高频细节"""
    lum = _luma(arr)
    base = _gauss(lum, radius=1.2)
    hi = lum - base
    h = _normalize(lum * 0.65 + _normalize(hi) * 0.35 * detail)
    return np.clip(h, 0.0, 1.0)


def derive_normal(height: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """由高度推导法线，OpenGL 约定（G = +Y）"""
    gx, gy = _sobel(height)
    nx = -gx * strength * 2.0
    ny = gy * strength * 2.0
    nz = np.ones_like(nx)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    length[length < 1e-8] = 1.0
    return np.stack([nx / length, ny / length, nz / length], axis=-1) * 0.5 + 0.5


def derive_roughness(arr: np.ndarray, height: np.ndarray,
                     floor: float = 0.18, ceil: float = 0.95) -> np.ndarray:
    """粗糙度：越亮越哑 + 表面细节越密越粗糙"""
    lum = _luma(arr)
    detail = _normalize(np.abs(lum - _gauss(lum, radius=1.5)))
    r = 0.55 * (1.0 - _normalize(lum)) + 0.45 * _normalize(detail)
    r = _gauss(r, radius=1.0)
    r = np.clip(r, 0.0, 1.0)
    return floor + (ceil - floor) * r


def derive_ao(height: np.ndarray, radius: float = 8.0, strength: float = 1.0) -> np.ndarray:
    """AO：局部均值高于自身高度处判为凹陷"""
    near = _gauss(height, radius=radius)
    ao = 1.0 - np.clip((near - height) * 3.0 * strength, 0.0, 1.0)
    return _gauss(np.clip(ao, 0.0, 1.0), radius=1.0)


def derive_opacity(arr: np.ndarray, cutoff: float = 0.5, invert: bool = False) -> np.ndarray:
    """按亮度阈值生成透明度遮罩（白=不透明，黑=镂空）

    用于镂空砖、格栅、铁丝网这类"贴在图上就是孔洞"的材质；
    cutoff 越大镂空越多，invert 用于暗底反选。轻微羽化避免锯齿硬切。
    """
    gray = _luma(arr)
    cut = float(max(0.0, min(1.0, cutoff)))
    a = (gray >= cut).astype(np.float32)
    a = _gauss(a, radius=0.8)
    if invert:
        a = 1.0 - a
    return np.clip(a, 0.0, 1.0)


def derive_opacity_file(src_path: str, dst_path: str, cutoff: float = 0.5) -> str:
    """按指定阈值单独重算透明度贴图（导出时按 opacity_cutoff 烘焙）"""
    im = _load_rgb(src_path)
    a = derive_opacity(_to_arr(im), cutoff=cutoff)
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    _to_img(np.stack([a] * 3, axis=-1)).save(dst_path, format="PNG", optimize=True)
    return dst_path


# ---------------------------- 基础颜色（色板调色） ----------------------------

def hex_to_rgb(color: str) -> tuple[float, float, float]:
    """'#rrggbb' / 'rrggbb' / '#rgb' -> (r, g, b) 0~1，非法值回落白色"""
    s = (color or "#ffffff").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (1.0, 1.0, 1.0)
    try:
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (1.0, 1.0, 1.0)


def tint_albedo(src_path: str, dst_path: str, color: str,
                mode: str = "match", strength: float = 1.0,
                chroma: float = 0.35) -> str:
    """把基础颜色染到漫反射贴图上，输出染色后的 albedo（兼容旧签名）"""
    return tint_albedo_ex(src_path, dst_path, color,
                          mode=mode, strength=strength, chroma=chroma)["path"]


def _srgb_to_linear(a: np.ndarray) -> np.ndarray:
    a = np.clip(a, 0.0, 1.0)
    return np.where(a <= 0.04045, a / 12.92,
                    np.power((a + 0.055) / 1.055, 2.4)).astype(np.float32)


def _linear_to_srgb(a: np.ndarray) -> np.ndarray:
    a = np.clip(a, 0.0, 1.0)
    return np.where(a <= 0.0031308, a * 12.92,
                    1.055 * np.power(a, 1.0 / 2.4) - 0.055).astype(np.float32)


def _linear_to_lab(a: np.ndarray) -> np.ndarray:
    """线性 RGB -> CIELAB(D65)，仅用于量化「选色 vs 结果」的色差"""
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
    xyz = a @ m.T
    white = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    t = xyz / white
    eps, kappa = 216.0 / 24389.0, 24389.0 / 27.0
    f = np.where(t > eps, np.cbrt(t), (kappa * t + 16.0) / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    A = 500.0 * (f[..., 0] - f[..., 1])
    B = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, A, B], axis=-1)


def color_delta_e(color: str) -> float:
    """选色自身的 LAB 基准（供色差计算复用）"""
    c = np.array(hex_to_rgb(color), dtype=np.float32)[None, None, :]
    return _linear_to_lab(_srgb_to_linear(c))[0, 0]


def tint_albedo_ex(src_path: str, dst_path: str, color: str,
                   mode: str = "match", strength: float = 1.0,
                   chroma: float = 0.35) -> dict:
    """色板染色 v2 —— 目标是「结果主色 = 选色」，而不是「选色 × 原图」

    旧实现是 sRGB 空间逐通道相乘（arr * C）。它有两个硬伤：
        1) 亮度衰减：原图均值亮度 L 不是 1，结果均值 ≈ L × C。一张偏亮的
           米色墙纸（L≈0.85）染 #F2EDE4，出来比选色暗一截，用户看到的就是
           「色板选的颜色和渲染出来的不一样」。
        2) 空间错误：albedo 是反射率，物理上应在线性光空间相乘；在 sRGB
           空间相乘会让中间调偏暗、饱和度漂移。

    v2 的做法（mode="match"）：
        1) 转线性空间，取逐像素相对明暗 shade = L / mean(L)
        2) out = C_lin × shade  -> 颜色由选色决定，明暗结构由原图决定
        3) 二次校准：把结果平均亮度缩放回选色亮度（这一步保证「所见即所
           选」，是旧实现最大的缺口）
        4) 色度残差回掺 chroma：保住花纹/木纹的色相变化；纯色漆设 0 即
           完全按选色单色化

    参数：
        mode     : match（默认，校色匹配）/ multiply（旧乘法，兼容）/ mono（单色化）
        strength : 0~1，与旧乘法结果的混合比例，1=完全用新模式
        chroma   : 0~1，原图色度变化保留率（花纹材质建议 0.3~0.6，纯色漆 0）
    返回：
        {"path": 输出路径, "delta_e": 染色前后平均色与选色的色差, ...}
    """
    target = hex_to_rgb(color)
    norm_mode = str(mode or "match").strip().lower()
    out_path = dst_path
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # 白色 = 保持原色，直通（不做归一化，避免「选白反而变亮」的意外）
    if target == (1.0, 1.0, 1.0):
        if os.path.abspath(src_path) != os.path.abspath(out_path):
            shutil.copy2(src_path, out_path)
        return {"path": out_path, "delta_e": 0.0, "mode": "identity",
                "mean_color": "#ffffff", "target": color}

    arr = _to_arr(_load_rgb(src_path))                    # sRGB 0~1
    lin = _srgb_to_linear(arr)
    c_lin = _srgb_to_linear(np.array(target, dtype=np.float32)[None, None, :])

    legacy = lin * c_lin                                  # 旧行为基准

    if norm_mode == "multiply":
        out_lin = legacy
    else:
        lum = _luma(lin)
        mean_l = float(np.clip(float(lum.mean()), 1e-3, 4.0))
        shade = np.clip(lum / mean_l, 0.15, 6.0)[..., None]
        out_lin = c_lin * shade

        # 色度残差：保住原图的色相起伏（花纹、木纹、污渍）
        keep = float(np.clip(chroma, 0.0, 1.0)) if norm_mode != "mono" else 0.0
        if keep > 0.0:
            chroma_field = lin / np.maximum(lum[..., None], 1e-4)
            out_lin = out_lin * (1.0 + keep * (chroma_field - 1.0))

        # 二次校准：结果平均亮度 = 选色亮度（ΔE 的来源主要就靠这一步收敛）
        tgt_lum = float(max(_luma(c_lin)[0, 0], 1e-4))
        cur_lum = float(np.clip(float(_luma(out_lin).mean()), 1e-4, 4.0))
        out_lin = out_lin * float(np.clip(tgt_lum / cur_lum, 0.55, 1.8))

        # 与旧结果混合，给「别把我原来的色调全改掉」留出口
        k = float(np.clip(strength, 0.0, 1.0))
        if k < 1.0:
            out_lin = legacy + (out_lin - legacy) * k

    out_lin = np.clip(out_lin, 0.0, 1.0)
    _to_img(_linear_to_srgb(out_lin)).save(out_path, format="PNG", optimize=True)

    # 量化：平均色 vs 选色的 ΔE(Lab 欧氏，<2 肉眼基本分不出)
    # 注意：两侧必须都是 (3,) 的 Lab 向量，早期版本把均值算成了标量 L，
    # 拿 L 去减 a/b 通道会得到一个虚高的「色差」，会误导验收。
    mean_lab = _linear_to_lab(out_lin.reshape(-1, 3).mean(axis=0)[None, :])[0]
    tgt_lab = _linear_to_lab(c_lin.reshape(1, 3))[0]
    delta_e = float(np.linalg.norm(mean_lab - tgt_lab))
    # 旧实现同口径色差，便于对比「优化了多少」
    legacy_lab = _linear_to_lab(legacy.reshape(-1, 3).mean(axis=0)[None, :])[0]
    return {
        "path": out_path,
        "delta_e": round(delta_e, 2),
        "delta_e_before": round(float(np.linalg.norm(legacy_lab - tgt_lab)), 2),
        "mode": norm_mode,
        "target": color,
    }


# ---------------------------- 主入口 ----------------------------

def build_texture_set(src_path: Optional[str], out_dir: str,
                      progress: Optional[Callable[[str, float], None]] = None,
                      options: Optional[dict] = None) -> dict:
    """生成整套贴图，返回 {通道名: 绝对路径}

    src_path 现在允许为 None：此时进入「纯色模式」——不读任何图片，
    以 options["base_color"] 为底色，靠 proc_texture 的程序化场产生
    height / normal / roughness / AO。这是乳胶漆「只给一个色号就要出粗糙墙面」
    的关键路径。

    options 相关键：
        decontaminate  是否去光照（纯色模式忽略）
        detail         细节强度 0~3
        normal_strength 法线强度（这里只影响高度转法线的陡峭度，Max 端另有 bump_multiplier）
        opacity_cutoff 透明度阈值
        proc_on        是否叠加程序化纹理
        proc_type      roller / spray / sand / stucco
        proc_scale     纹理尺寸倍数（>1 更粗）
        proc_amount    起伏强度 0~1.5
        proc_rough     粗糙度扰动幅度 0~1
        proc_albedo    漫反射斑驳 0~0.3
        proc_mask      低频区域遮罩强度 0~1（0=均匀麻面，0.6=默认，1=分区最明显）
        proc_mask_scale 遮罩特征尺寸倍数（>1 区块更大更整，<1 更碎）
        proc_seed      随机种子
        rough_base     程序化模式下的粗糙度基准（由光泽度换算而来）
        size           纯色模式的贴图边长
    """
    options = options or {}
    os.makedirs(out_dir, exist_ok=True)

    def tick(msg, p):
        if progress:
            progress(msg, p)

    solid = src_path is None or not str(src_path).strip()
    resize_info = {}

    if solid:
        tick("生成纯色底", 0.1)
        size_req = int(options.get("size", 1024) or 1024)
        size = int(np.clip(size_req, 256, MAX_SIDE))
        if size != size_req:
            # 纯色尺寸被夹同样要说，否则用户填 4096 拿到 2048 会以为是 bug
            resize_info = {
                "downscaled": True, "original": [size_req, size_req],
                "target": [size, size], "max_side": MAX_SIDE,
                "note": "纯色尺寸 %dpx 超出单边 %dpx 上限，已按 %dpx 生成"
                        % (size_req, MAX_SIDE, size),
            }
        base_rgb = np.array(hex_to_rgb(str(options.get("base_color") or "#ffffff")),
                            dtype=np.float32)
        arr = np.ones((size, size, 3), dtype=np.float32) * base_rgb
        # 纯色没有「原图光照」可去，直接拿来当底色
        albedo = arr.copy()
        height = np.zeros((size, size), dtype=np.float32)
        rough = np.full((size, size), 0.6, dtype=np.float32)
        ao = np.ones((size, size), dtype=np.float32)
        opacity = np.ones((size, size), dtype=np.float32)
    else:
        tick("读取图片", 0.05)
        with Image.open(src_path) as _probe:
            resize_info = _size_plan(*_probe.size)
        im = _load_rgb(src_path)
        arr = _to_arr(im)

        tick("生成漫反射", 0.2)
        albedo = derive_albedo(arr, decontaminate=options.get("decontaminate", True))

        tick("生成高度", 0.4)
        height = derive_height(arr, detail=float(options.get("detail", 1.0)))

        tick("生成粗糙度", 0.7)
        rough = derive_roughness(arr, height)

        tick("生成 AO", 0.8)
        ao = derive_ao(height)

        tick("生成透明度", 0.85)
        opacity = derive_opacity(arr, cutoff=float(options.get("opacity_cutoff", 0.5)))

    # ---------------- 低频区域遮罩（纯色 / 供图两种模式共用） ----------------
    # 原实现把遮罩埋在 latex_field 里，只有 proc_on=True 才生效，供图模式等于没有。
    # 现在遮罩在 engine 层统一生成：程序化分支把它传给 latex_field（避免重复生成），
    # 非程序化分支自己拿它调低频明暗/粗糙/找平起伏，两条路径吃的是同一张遮罩。
    h, w = height.shape[:2]
    amt_m = float(np.clip(float(options.get("proc_mask", 0.6) or 0.0), 0.0, 1.0))
    mask_scale = float(np.clip(float(options.get("proc_mask_scale", 1.0) or 1.0), 0.2, 8.0))
    uv_tiling = float(options.get("uv_tiling", 1.0) or 1.0)
    # 世界尺度补偿：遮罩按米制尺寸换算成贴图空间循环数，uv_tiling 拉高时不再被缩成复读小抄
    mask_geom = proc_texture.mask_geometry(mask_scale, uv_tiling)
    mask_amt = amt_m * float(mask_geom["damp"])
    mask_field = None
    if amt_m > 1e-4:
        mask_field = proc_texture.region_mask(
            h, w, seed=int(options.get("proc_seed", 7) or 7) + 5501,
            cycles_per_tile=mask_geom["cycles_per_tile"])
    mask_info = None
    if mask_field is not None and mask_amt > 1e-4:
        mask_info = {
            "amount": round(amt_m, 3),
            "amount_effective": round(mask_amt, 3),
            "scale": mask_scale,
            "uv_tiling": round(uv_tiling, 3),
            "block_m": mask_geom["block_m"],
            "block_m_actual": mask_geom["block_m_actual"],
            "tile_m": mask_geom["tile_m"],
            "cycles_per_tile": mask_geom["cycles_per_tile"],
            "repeat_risk": bool(mask_geom["clamped"]),
            "seamless": round(proc_texture.seamless_score(mask_field["mask"]), 3),
        }

    # ---------------- 程序化微结构叠加 ----------------
    proc_info = None
    if bool(options.get("proc_on", False)):
        tick("生成程序化纹理", 0.9)
        field = proc_texture.latex_field(
            h, w,
            kind=str(options.get("proc_type") or "roller"),
            scale=float(options.get("proc_scale", 1.0) or 1.0),
            seed=int(options.get("proc_seed", 7) or 7),
            mask=mask_amt,
            mask_scale=mask_scale,
            mask_field=mask_field,
            mask_amount=mask_amt,
        )
        amount = float(np.clip(float(options.get("proc_amount", 0.6) or 0.0), 0.0, 1.5))

        # 高度：图像结构按 amount 让位给程序化场（纯色时图像结构本就是常数）
        h_proc = 0.5 + (field["height"] - 0.5) * amount
        if solid or float(np.std(height)) < 1e-3:
            height = np.clip(h_proc, 0.0, 1.0)
        else:
            height = _normalize(height * max(0.0, 1.0 - 0.85 * amount) + h_proc * 0.85 * amount)

        # 粗糙度：以基准值为中心做小幅抖动（哑面微闪的来源，幅度刻意克制：
        # 乳胶漆真实 roughness 波动只有 ±0.1 左右，抖过头会变成磨砂金属）
        amt_r = float(np.clip(float(options.get("proc_rough", 0.5) or 0.0), 0.0, 1.0))
        base_r = float(np.clip(float(options.get("rough_base", 0.6) or 0.6), 0.05, 0.95))
        # 粗糙度里也带一份遮罩：脏污/补漆段更哑、涂厚段更亮，让高光沿墙面自然分段
        rough = np.clip(base_r + (field["rough"] - 0.5) * 0.45 * amt_r
                        + (field["mask"] - 0.5) * 0.34 * amt_r * mask_amt, 0.03, 0.98)

        # 漫反射：极轻微低频斑驳，避免「死板纯色」
        amt_a = float(np.clip(float(options.get("proc_albedo", 0.04) or 0.0), 0.0, 0.3))
        if amt_a > 0.0:
            mod = 1.0 + amt_a * (field["albedo"] - 0.5) * 2.0
            albedo = np.clip(albedo * mod[..., None], 0.0, 1.0)

        ao = derive_ao(height)
        proc_info = {
            "type": str(options.get("proc_type") or "roller"),
            "scale": float(options.get("proc_scale", 1.0) or 1.0),
            "amount": amount,
            "mask": amt_m,
            "mask_effective": round(mask_amt, 3),
            "mask_scale": mask_scale,
            "mask_seamless": round(proc_texture.seamless_score(field["mask"]), 3),
            "seamless": round(proc_texture.seamless_score(field["height"]), 3),
        }
    elif mask_info is not None:
        # 供图 / 关闭程序化的纯色：遮罩不碰图片自带的高度细节，只压低频明暗、粗糙与找平起伏
        # P0-1：这就是「遮罩只在程序化模式生效、供图模式白给」的补丁
        m = mask_field["mask"]
        dm = (m - 0.5) * 2.0
        amt_a = float(np.clip(float(options.get("proc_albedo", 0.04) or 0.0), 0.0, 0.3))
        gain_a = float(np.clip(0.06 + amt_a * 1.6, 0.0, 0.30))
        albedo = np.clip(albedo * (1.0 + gain_a * mask_amt * dm[..., None]), 0.0, 1.0)
        amt_r = float(np.clip(float(options.get("proc_rough", 0.5) or 0.0), 0.0, 1.0))
        rough = np.clip(rough + dm * 0.34 * amt_r * mask_amt * 0.5, 0.03, 0.98)
        height = np.clip(height + dm * 0.04 * mask_amt, 0.0, 1.0)
        ao = derive_ao(height)

    tick("生成法线", 0.95)
    normal = derive_normal(height, strength=float(options.get("normal_strength", 1.0)))

    outputs = {
        "albedo": _to_img(albedo),
        "height": _to_img(height),
        "normal": _to_img(normal),
        "roughness": _to_img(np.stack([rough] * 3, axis=-1)),
        "ao": _to_img(np.stack([ao] * 3, axis=-1)),
        "opacity": _to_img(np.stack([opacity] * 3, axis=-1)),
    }

    paths = {}
    for name, img in outputs.items():
        p = os.path.join(out_dir, f"{name}.png")
        img.save(p, format="PNG", optimize=True)
        paths[name] = p

    paths["_proc"] = proc_info or {}
    paths["_mask"] = mask_info or {}
    paths["_resize"] = resize_info or {}
    paths["_solid"] = bool(solid)
    tick("完成", 1.0)
    return paths

