"""Material Forge — 图片生成 3ds Max / V-Ray 材质球的本地 Web 服务

启动：双击 启动.bat  （或 python app.py）
界面：http://127.0.0.1:8765

流程：
    上传图片 -> 推导 PBR 贴图 -> WebGL 材质球实时预览 + 勾选参数 -> 导出 .ms

注意：本程序不启动 3ds Max。导出的是 .ms 脚本，由你在 Max 里手动 Run Script，
      程序侧不做任何 Max 进程交互。
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import ai_enhance, export_guard, max_bridge, params, preflight, texture_engine
from core.exporters import ms_exporter
from core.version import __version__ as CORE_VERSION

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "output"
WEB = BASE / "web"

OUTPUT.mkdir(exist_ok=True)

app = FastAPI(title="Material Forge", version=CORE_VERSION)

# 内存会话表：job_id -> 各类路径
JOBS: dict[str, dict] = {}

# ------------------------------------------------------------------
# 资源边界
# ------------------------------------------------------------------
# 上传上限：贴图推导会把整图读进内存做多通道处理，超大图不只是慢，是直接吃爆内存
MAX_UPLOAD_MB = 32
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_PIXELS = 40_000_000          # 4000 万像素（约 7300×5470）
MAX_SIDE = 9000                  # 单边上限，防止 1×40000 这种退化极值

# 任务生命周期：JOBS 常驻内存 + output/ 只增不减，跑一天就能堆出几个 G
MAX_JOBS = 40                    # 内存里最多留 40 个活跃 job，超出淘汰最旧
JOB_TTL_SECONDS = 24 * 3600      # 磁盘产物保留 24 小时
_PURGE_INTERVAL = 600            # 清理节流：10 分钟内最多扫一次
_last_purge = 0.0

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")

CHANNELS = ["albedo", "normal", "roughness", "height", "ao",
            "metalness", "emissive", "sheen"]


# ------------------------------------------------------------------
# 基础接口
# ------------------------------------------------------------------

@app.get("/api/schema")
def get_schema():
    """参数面板描述（前端据此动态渲染，新增参数无需改前端）"""
    data = params.schema()          # {groups, params, presets}
    data["ai_providers"] = ai_enhance.list_available()
    data["version"] = app.version
    return data


@app.get("/api/health")
def health():
    return {"ok": True}


# ------------------------------------------------------------------
# 上传与推导
# ------------------------------------------------------------------

def _job_dir(job_id: str) -> Path:
    d = OUTPUT / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------
# 任务生命周期：内存淘汰 + 磁盘 TTL
# ------------------------------------------------------------------
def _purge_jobs() -> dict:
    """清理过期/超量的任务产物

    安全边界（重要）：
        只删 OUTPUT 下名字严格匹配 12 位十六进制的目录——那是本程序 upload/solid
        用 uuid4().hex[:12] 生成的 job 目录；output/_uicheck_sample 这类检查脚本
        自己的样本目录名字不匹配，绝不会被误删。清理对象是本工具的一次性缓存，
        不是用户资料：产物一旦导出到别处由用户自己保管。
    """
    global _last_purge
    now = time.time()
    _last_purge = now
    removed = {"mem": 0, "dir": 0, "trimmed": 0}

    # 1. 内存：超过上限按创建时间淘汰最旧
    if len(JOBS) > MAX_JOBS:
        olds = sorted(JOBS.items(), key=lambda kv: kv[1].get("created_at", 0))
        for jid, _info in olds[: len(JOBS) - MAX_JOBS]:
            JOBS.pop(jid, None)
            removed["mem"] += 1

    # 2. 磁盘：超 TTL 的 job 目录整目录删除
    try:
        for d in OUTPUT.iterdir():
            if not d.is_dir() or not _JOB_ID_RE.match(d.name):
                continue
            job = JOBS.get(d.name)
            born = job.get("created_at") if job else None
            if born is None:
                try:
                    born = d.stat().st_mtime
                except OSError:
                    continue
            if now - born < JOB_TTL_SECONDS:
                continue
            try:
                # 有 export/ 说明用户已经导出过：交付物不清，只回收中间过程贴图
                # （source / textures / prev_tex / preview / tex_ai），避免"睡一觉
                # 醒来导出的 .ms 没了"。真要清交付物由用户自己删 output 目录。
                if (d / "export").is_dir():
                    for sub in d.iterdir():
                        if sub.name == "export":
                            continue
                        if sub.is_dir():
                            shutil.rmtree(sub, ignore_errors=True)
                        else:
                            try:
                                sub.unlink()
                            except OSError:
                                pass
                    JOBS.pop(d.name, None)
                    removed["trimmed"] += 1
                    continue
                shutil.rmtree(d, ignore_errors=True)
                JOBS.pop(d.name, None)
                removed["dir"] += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def _maybe_purge() -> None:
    """节流调用：频率上限 10 分钟一次，别让每个请求都去扫目录"""
    if time.time() - _last_purge >= _PURGE_INTERVAL:
        try:
            _purge_jobs()
        except Exception:
            pass


@app.on_event("startup")
def _on_startup():
    """启动即清一次：上次异常退出留下的产物不该继续占地"""
    try:
        _purge_jobs()
    except Exception:
        pass


# 贴图字典里 "_" 开头的键是元信息（_proc / _solid），不参与 URL 与导出
def _tex_urls(job_id: str, tex: dict, sub: str = "textures") -> dict:
    return {k: f"/files/{job_id}/{sub}/{Path(v).name}"
            for k, v in tex.items() if not k.startswith("_")}


def _tex_meta(tex: dict) -> dict:
    """剥掉 "_" 开头的元信息（_proc/_solid/_mask），只留真实贴图路径

    重要：返回的字典会被原样存进 job["textures"] 并直接喂给 ms_exporter，
    必须保证里面全是字符串路径——曾经把 _mask（dict）挂进来当 _mask_info，
    结果 preflight 里 .replace 直接抛 'dict' object has no attribute 'replace'。
    遮罩的米制换算走响应体的 mask 字段带出去，不进这个字典。
    """
    return {k: v for k, v in tex.items() if not k.startswith("_")}


# 粗糙度档位 -> 基准 roughness（与 params.rough_level 的 vray_map 保持一致）
ROUGH_BY_LEVEL = {"matte": 0.85, "semi": 0.55, "glossy": 0.28, "mirror": 0.05}


def _rough_base(values: dict) -> float:
    """程序化纹理的粗糙度基准：跟随用户选的光泽度，而不是写死"""
    if values.get("gloss_mode"):
        g = float(values.get("reflection_glossiness", 0.72) or 0.72)
        return max(0.03, min(0.97, 1.0 - g))
    return float(ROUGH_BY_LEVEL.get(values.get("rough_level"), 0.55))


def _tint_args(values: dict) -> dict:
    return {
        "mode": str(values.get("tint_mode") or "match"),
        "strength": float(values.get("tint_strength", 1.0) or 1.0),
        "chroma": float(values.get("tint_chroma", 0.35) or 0.0),
    }


def _tex_options(values: dict, extra: dict | None = None) -> dict:
    """把参数面板的值翻译成贴图生成选项"""
    opts = {
        "detail": float(values.get("detail", 1.0) or 1.0),
        "normal_strength": float(values.get("normal_strength", 1.0) or 1.0),
        "opacity_cutoff": float(values.get("opacity_cutoff", 0.5) or 0.5),
        "rough_base": _rough_base(values),
        "base_color": str(values.get("base_color") or "#ffffff"),
        "size": 1024,
        # UV 平铺参与遮罩的世界尺度换算：tiling 拉高时不能把米制区块同步缩成复读小抄
        "uv_tiling": float(values.get("uv_tiling", 1.0) or 1.0),
    }
    for k in ("proc_on", "proc_type", "proc_scale", "proc_amount",
              "proc_rough", "proc_albedo", "proc_mask", "proc_mask_scale", "proc_seed"):
        if k in values:
            opts[k] = values[k]
    if extra:
        opts.update(extra)
    # 0.12.0 三通道：开关 + 基准值一起下发。
    # metal_mode 打开时整张图就是金属，base 取 1.0；否则给 0.85，让高度/遮罩把它
    # 调制出 0.5~1 的分布（磨损露底材的常见区间），比从 0 起调更贴近真实金属件。
    for _k in ("metalness_tex_on", "emissive_tex_on", "sheen_tex_on"):
        opts[_k] = bool(values.get(_k, False))
    opts["metal_base"] = 1.0 if bool(values.get("metal_mode", False)) else 0.85
    return opts


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """接收图片，返回 job_id 与贴图集（立即推导，保证首屏就有预览）"""
    ext = Path(file.filename or "input.png").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        raise HTTPException(400, f"不支持的图片格式: {ext}")

    _maybe_purge()

    # 分块读 + 硬上限：以前是 raw = await file.read() 全量进内存，
    # 一张 300MB 的 TIFF 能直接把服务拖死；这里超限立刻断，不落盘。
    chunks, total = [], 0
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"图片超过 {MAX_UPLOAD_MB}MB 上限（已收到 {total / 1048576:.1f}MB），"
                     f"请先压缩或裁剪后再上传")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        raise HTTPException(400, "空文件")

    # 像素上限：推导链路要把整图解码进内存跑多通道，超大图不是慢，是直接 OOM
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            img_w, img_h = im.size
    except Exception as e:
        raise HTTPException(400, f"图片无法解析（已损坏或编码不支持）：{e}")
    if max(img_w, img_h) > MAX_SIDE or img_w * img_h > MAX_PIXELS:
        raise HTTPException(
            413, f"图片尺寸 {img_w}×{img_h} 超出上限"
                 f"（单边 ≤{MAX_SIDE}px、总像素 ≤4000 万），请先缩小再上传")

    job_id = uuid.uuid4().hex[:12]
    jd = _job_dir(job_id)
    src = jd / f"source{ext}"
    src.write_bytes(raw)

    # 立即推导贴图
    try:
        tex_paths = texture_engine.build_texture_set(str(src), str(jd / "textures"))
    except Exception as e:
        raise HTTPException(500, f"贴图推导失败: {e}")

    JOBS[job_id] = {
        "source": str(src),
        "textures": _tex_meta(tex_paths),
        "textures_base": _tex_meta(tex_paths),
        "original_name": file.filename,
        "created_at": time.time(),
        "source_size": [img_w, img_h],
        "source_bytes": total,
    }

    return {
        "job_id": job_id,
        "source_url": f"/files/{job_id}/source{ext}",
        "textures": _tex_urls(job_id, tex_paths),
        "original_name": file.filename,
        # 回显源图尺寸与体积：上传被限流/压缩过时，用户要能立刻看出实际吃进去的是什么
        "source_size": [img_w, img_h],
        "source_bytes": total,
    }


@app.post("/api/derive")
async def derive(payload: dict):
    """按新的推导选项重算贴图（不改参数面板，仅影响贴图本身）"""
    job_id = payload.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在，请重新上传图片")

    options = payload.get("options") or {}
    jd = _job_dir(job_id)
    try:
        tex = texture_engine.build_texture_set(job["source"], str(jd / "textures"), options=options)
    except Exception as e:
        raise HTTPException(500, f"重算失败: {e}")

    job["textures"] = _tex_meta(tex)
    job["textures_base"] = _tex_meta(tex)
    return {"textures": _tex_urls(job_id, tex)}


@app.post("/api/build")
async def build(payload: dict):
    """按当前参数重建贴图集（程序化微结构 + 色板校色），返回可预览的 URL

    为什么预览必须走这里：
        颜色是「烘进 albedo 贴图」的，如果预览仍靠 three 的 material.color
        相乘，用户看到的青色永远和导出的不一致。所以这里直接把烘焙后的
        albedo 吐给预览，前端 material.color 固定为白——所见即所得。
    """
    job_id, res = await _rebuild(payload.get("job_id"), payload,
                                 preview=bool(payload.get("preview")))
    return res


async def _rebuild(job_id, payload: dict, preview: bool = False):
    """重建贴图集

    preview=True 走分级预览：只在 prev_tex/ 下出一张低分辨率图，不回写
    job["textures_base"]。拖动滑杆时用这条路径换跟手感，松手后再由前端
    补一次全尺寸，保证导出的永远是全分辨率且参数最新的贴图。
    """
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在，请重新上传图片或进入纯色模式")

    values = {**params.default_values(), **(payload.get("values") or {})}
    size = int(payload.get("size") or 1024)
    if preview:
        size = min(size, max(160, int(payload.get("preview_size") or 320)))
    extra = {"size": size}
    if payload.get("detail") is not None:
        extra["detail"] = float(payload.get("detail"))

    jd = _job_dir(job_id)
    sub = "prev_tex" if preview else "textures"
    try:
        tex = texture_engine.build_texture_set(job.get("source"), str(jd / sub),
                                               options=_tex_options(values, extra))
    except Exception as e:
        raise HTTPException(500, f"重建贴图失败: {e}")

    mask_info = tex.get("_mask") or {}
    resize_info = tex.get("_resize") or {}
    tex = _tex_meta(tex)
    urls = _tex_urls(job_id, tex, sub)
    # 染色的 albedo 单独落文件，绝不覆盖 textures/albedo.png：
    # 导出端要的是未染色的基准贴图，覆盖会造成「上传已染 → 导出再染一次」的二次衰减
    tint_sub = "prev_tex" if preview else "preview"
    tint_dir = jd / tint_sub
    tint_name = "albedo.png"
    tint_info = {}
    if tex.get("albedo"):
        tint_dir.mkdir(parents=True, exist_ok=True)
        try:
            r = texture_engine.tint_albedo_ex(
                tex["albedo"], str(tint_dir / tint_name),
                str(values.get("base_color") or "#ffffff"), **_tint_args(values))
            urls["albedo"] = f"/files/{job_id}/{tint_sub}/{tint_name}"
            tint_info = {k: r[k] for k in ("mode", "delta_e", "delta_e_before") if k in r}
        except Exception as e:
            raise HTTPException(500, f"染色失败: {e}")

    if preview:
        # 快预览：不写 job["textures"]/["textures_base"]，避免污染导出用的基准贴图
        return job_id, {"job_id": job_id, "textures": urls, "tint": tint_info,
                        "solid": bool(job.get("source") is None),
                        "preview": True, "size": size,
                        "mask": mask_info, "resize": resize_info}

    job["textures_base"] = dict(tex)
    job["values"] = values
    job["textures"] = dict(tex)
    return job_id, {"job_id": job_id, "textures": urls, "tint": tint_info,
                    "solid": bool(job.get("source") is None), "size": size,
                    "mask": mask_info, "resize": resize_info}


@app.post("/api/solid")
async def solid(payload: dict):
    """纯色模式：不上传图片，色板选色 + 程序化微结构直接出材质

    这是乳胶漆/艺术漆的正确入口——用户手里只有色卡，没有法线图。
    """
    _maybe_purge()
    job_id = uuid.uuid4().hex[:12]
    _job_dir(job_id)
    JOBS[job_id] = {"source": None, "textures": {}, "textures_base": {},
                    "original_name": "纯色材质", "created_at": time.time()}
    _, res = await _rebuild(job_id, payload)
    return res


@app.post("/api/ai_enhance")
async def run_ai_enhance(payload: dict):
    """可选：调用外部 AI 材质生成（未配置 Key 时返回 available=false）"""
    job_id = payload.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")

    jd = _job_dir(job_id)
    res, used = ai_enhance.enhance(
        job["source"], str(jd / "textures_ai"),
        provider=payload.get("provider", "auto"),
    )
    if not res:
        return {"available": False, "provider": "none",
                "message": "未配置 AI 服务或调用失败，已使用本地程序化贴图"}

    # 合并：AI 优先，缺失通道保留本地结果
    merged = dict(job["textures"])
    merged.update(res)
    job["textures"] = merged
    return {
        "available": True,
        "provider": used,
        "textures": {k: f"/files/{job_id}/{Path(v).parent.name}/{Path(v).name}" for k, v in merged.items()},
    }


# ------------------------------------------------------------------
# 导出
# ------------------------------------------------------------------

def _resolve_renderer(values: dict, payload: dict) -> str:
    """解析目标渲染器；非法值一律回落到 vray，保证旧前端不带该字段时行为不变。"""
    raw = str((payload or {}).get("renderer") or (values or {}).get("renderer") or "vray").strip().lower()
    return "corona" if raw in ("corona", "cr", "coronamtl") else "vray"


def _extra_channel_plan(merged: dict) -> list[tuple[str, str]]:
    """三通道开关 -> 需要的通道名（只有开了才算数）"""
    return [(c, f) for c, f in (("metalness", "metalness_tex_on"),
                                ("emissive", "emissive_tex_on"),
                                ("sheen", "sheen_tex_on")) if merged.get(f)]


def _ensure_extra_channels(tex_out: Path, final_tex: dict, merged: dict) -> tuple[list, list]:
    """导出时补齐三通道贴图：开关开了但图上没有，就地按同一套算法补生成。

    为什么兜底放在导出端：三通道图是「勾选才出」的，而前端勾通道时并不重跑推导
    （重算按钮只带 detail / normal_strength），于是「勾上金属度贴图 -> 直接导出」
    这条最顺手的路径会拿到一份没有 metalness 槽的 .ms——参数说要、脚本里没有，
    用户以为生效了。导出是最终真相，缺图在这里补，比指望前端多一次往返可靠。
    源图缺（例如纯色模式没 height）补不出来的，走 skipped 明确告知，不静默吞掉。
    """
    made: list[str] = []
    skipped: list[str] = []
    plan = _extra_channel_plan(merged)
    if not plan:
        return made, skipped

    import numpy as np
    from PIL import Image

    def _read(name):
        p = final_tex.get(name)
        if not p or not os.path.exists(p):
            return None
        with Image.open(p) as im:
            return np.asarray(im.convert("L"), dtype=np.float32) / 255.0

    def _save(arr, path):
        u8 = np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)
        Image.fromarray(np.stack([u8] * 3, axis=-1)).save(str(path), format="PNG", optimize=True)
        return str(path)

    height, rough, albedo = _read("height"), _read("roughness"), _read("albedo")
    for chan, _flag in plan:
        if final_tex.get(chan):
            continue                      # 上传时就已生成，直接用
        need = {"metalness": height, "emissive": (albedo if albedo is not None else height),
                "sheen": rough}[chan]
        if need is None:
            skipped.append(chan)
            continue
        try:
            if chan == "metalness":
                base = float(merged.get("metal_base")
                             or (1.0 if merged.get("metal_mode") else 0.85))
                arr = texture_engine.derive_metalness(
                    height, None, base=base,
                    amount=float(merged.get("metal_tex_amount") or 0.35))
            elif chan == "emissive":
                arr = texture_engine.derive_emissive(
                    None, albedo,
                    base=float(merged.get("emis_tex_base") or 1.0),
                    amount=float(merged.get("emis_tex_amount") or 0.6))
            else:
                arr = texture_engine.derive_sheen(
                    rough, None,
                    base=float(merged.get("sheen_tex_base") or 1.0),
                    amount=float(merged.get("sheen_tex_amount") or 0.5))
            final_tex[chan] = _save(arr, tex_out / f"{chan}.png")
            made.append(chan)
        except Exception:
            skipped.append(chan)
    return made, skipped


@app.post("/api/export")
async def export(payload: dict):
    """生成 .ms MaxScript + 贴图集，返回产出物绝对路径"""
    job_id = payload.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在，请重新上传图片")

    values = payload.get("values") or {}
    mat_name = (payload.get("material_name") or "MaterialForge").strip() or "MaterialForge"
    # 渲染器目标：vray / corona。放在最前面解析，目录命名、参数解析、脚本生成三处都要用。
    renderer = _resolve_renderer(values, payload)

    _maybe_purge()

    jd = _job_dir(job_id)
    # 按材质名隔离导出目录。以前同 job 里所有材质共用 <job>/export/textures/，
    # 而贴图文件名是固定的 albedo.png / normal.png……换参数或换材质名再导一次，
    # 后一次就把前一次的图覆盖了——先导出的那份 .ms 在 Max 里执行时读到的是
    # 后一次的颜色（"同一个 job 导出两个材质，颜色串了"的根因）。
    # 现在一材质一目录，.ms 与它的 textures/ 自成一体，整体拷走即可用。
    safe = export_guard.safe_dirname(mat_name)
    # 同一材质名分别导 V-Ray / Corona 时目录必须分开，否则 .ms 与贴图互相覆盖。
    # 用固定子目录而不是给名字直接拼后缀：名字本身叫 xxx_corona 时会拼成 xxx_corona_corona。
    if renderer == "corona" and not safe.lower().endswith("_corona"):
        safe = safe + "_corona"
    export_dir = jd / "export" / safe
    export_dir.mkdir(parents=True, exist_ok=True)

    # 贴图复制到导出目录，保证 .ms 与贴图同包可整体拷走
    tex_out = export_dir / "textures"
    tex_out.mkdir(exist_ok=True)
    final_tex = {}
    # 优先用未染色的基准贴图，避免「上传时已染色 → 导出再染一次」的二次衰减
    src_tex = job.get("textures_base") or job.get("textures") or {}
    for chan, p in src_tex.items():
        if chan.startswith("_") or not os.path.exists(p):
            continue
        dst = tex_out / Path(p).name
        shutil.copy2(p, dst)
        final_tex[chan] = str(dst)

    # 解析参数
    merged_values = {**params.default_values(), **values}
    vray_props = params.resolve(merged_values, renderer)

    # 基础颜色：把染色烘进 albedo 贴图，Max 里不需要额外挂颜色校正节点。
    # 走 tint_albedo_ex：结果主色 = 色板选色（旧乘法会把亮色整体压暗，
    # 这就是「选浅色出来发灰」的根因），仅在白/缺图时直通。
    base_color = str(merged_values.get("base_color") or "#ffffff")
    tint_report = {}
    if final_tex.get("albedo"):
        try:
            r = texture_engine.tint_albedo_ex(
                final_tex["albedo"], str(tex_out / "albedo.png"), base_color,
                **_tint_args(merged_values))
            final_tex["albedo"] = r["path"]
            tint_report = {k: r[k] for k in ("mode", "delta_e", "delta_e_before") if k in r}
        except Exception as e:
            raise HTTPException(500, f"基础颜色染色失败: {e}")


    # 透明度贴图：按当前阈值重新烘焙（上传时用的是默认 0.5）
    if merged_values.get("opacity_on") and job.get("source"):
        try:
            final_tex["opacity"] = texture_engine.derive_opacity_file(
                job["source"], str(tex_out / "opacity.png"),
                cutoff=float(merged_values.get("opacity_cutoff", 0.5) or 0.5),
            )
        except Exception as e:
            raise HTTPException(500, f"透明度贴图生成失败: {e}")

    # 三通道兜底：勾了开关却在导出前没生成图的，这里按同一算法补齐
    extra_made, extra_skipped = _ensure_extra_channels(tex_out, final_tex, merged_values)

    # 材质库路径（safe 已在上面按材质名算好，与导出目录名同源）
    matlib = export_dir / f"{safe}.mat"

    auto_assign = payload.get("auto_assign", True) is not False

    # ---------- 导出前静态 dry-run ----------
    # 先在内存里生成脚本并做静态校验，有问题直接拦在导出前：
    # 不落任何文件，也不必等到进 Max Run Script 才发现报错。
    try:
        draft = ms_exporter.build_mscript(
            mat_name, vray_props, final_tex, str(matlib), auto_assign, renderer=renderer
        )
    except Exception as e:
        raise HTTPException(500, f"脚本生成失败: {e}")

    report = preflight.run(mat_name, draft, final_tex, merged_values,
                           has_source=job.get("source") is not None,
                           renderer=renderer)
    if not report["ok"]:
        raise HTTPException(
            400, detail={"msg": "导出前自检未通过，已拦截（未写入任何文件）", "report": report}
        )

    try:
        ms_info = ms_exporter.export_with_meta(
            material_name=mat_name,
            vray_props=vray_props,
            tex_paths=final_tex,
            out_dir=str(export_dir),
            matlib_path=str(matlib),
            auto_assign=auto_assign,
            logfile_path=str(export_dir / f"{safe}.log"),
            renderer=renderer,
        )
    except Exception as e:
        raise HTTPException(500, f"导出失败: {e}")
    ms_path = ms_info["path"]

    # 附一份参数快照，便于复现
    snapshot = export_dir / "material_params.json"
    snapshot.write_text(
        json.dumps(
            {"material_name": mat_name, "values": merged_values,
             "vray_props": {k: (v if not isinstance(v, tuple) else list(v))
                            for k, v in vray_props.items()},
             "renderer": renderer,
             "tint": tint_report,
             "solid_mode": job.get("source") is None,
             "textures": {k: Path(v).name for k, v in final_tex.items()}},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    # ---------- 导出后自检：唯一校验对象是磁盘上的 .ms ----------
    # 以前到这里直接 return：贴图复制失败在 ms_exporter 里是 except: pass，
    # 用户看到「导出成功」，进 Max 跑脚本才在加载贴图那一行炸。
    # 这里把判定口径统一交给 export_guard，与 preflight、evals 共用同一套规则。
    #
    # 假通过的两条老路都已封死：
    # 1) 读盘异常不再把内存 draft 塞进自检（那等于"没读到也报通过"）；
    # 2) 不再用 gbk 加宽松错误策略硬解，改为按写盘时回传的真实编码严格回读，
    #    否则 UTF-8 兜底产物会被解成乱码并被误判成贴图断链。
    # 读盘失败 / 编码不符 / 内容为空 → selfcheck.ok=False，直接判 FAIL。
    selfcheck = export_guard.check_export_file(
        ms_path, final_tex, merged_values,
        out_dir=str(export_dir),
        has_source=job.get("source") is not None,
        encoding=ms_info["encoding"],
    )
    selfcheck["dir"] = str(export_dir)

    note = "在 3ds Max 中执行 菜单 Scripting > Run Script，选择该 .ms 文件即可生成材质球"
    if extra_made:
        note = "已按开关补生成贴图（%s）；%s" % ("、".join(extra_made), note)
    if extra_skipped:
        note = ("以下通道缺源图未能生成，材质将不含该槽：%s；%s"
                % ("、".join(extra_skipped), note))

    if not selfcheck["ok"]:
        note = export_guard.summarize(selfcheck) + "；" + note

    products = [str(ms_path), str(snapshot)] + list(final_tex.values())
    return {
        "ms_path": str(ms_path),
        "script_url": f"/files/{job_id}/{Path(ms_path).relative_to(jd).as_posix()}",
        "matlib_path": str(matlib),
        "texture_dir": str(export_dir),
        "texture_subdir": str(tex_out),
        "products": products,
        "renderer": renderer,
        "report": report,
        "selfcheck": selfcheck,
        "script_encoding": ms_info["encoding"],
        "auto_assign": auto_assign,
        "extra_channels": {"made": extra_made, "skipped": extra_skipped},
        "note": note,
    }


@app.post("/api/preflight")
async def preflight_api(payload: dict):
    """导出前自检（不落盘）：返回报告，供前端在导出前展示"""
    job_id = payload.get("job_id")
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在，请重新上传图片")

    values = payload.get("values") or {}
    mat_name = (payload.get("material_name") or "MaterialForge").strip() or "MaterialForge"
    renderer = _resolve_renderer(values, payload)
    merged_values = {**params.default_values(), **values}
    vray_props = params.resolve(merged_values, renderer)

    # 校验用「上传阶段的贴图路径」即可，不复制、不染色，纯静态
    try:
        draft = ms_exporter.build_mscript(
            mat_name, vray_props, dict(job["textures"]), "", True, renderer=renderer
        )
    except Exception as e:
        raise HTTPException(500, f"脚本生成失败: {e}")

    return preflight.run(mat_name, draft, dict(job["textures"]), merged_values,
                         preview_only=True, has_source=job.get("source") is not None,
                         renderer=renderer)


@app.post("/api/send_to_max")
def send_to_max(payload: dict):
    """把导出的 .ms 直接送进已经开着的 3ds Max（一键，不必手动 Run Script）

    路径白名单只认 OUTPUT 下的文件：这个接口会让 Max 执行脚本，不能让它变成
    「随便给个路径就能执行」的后门。
    """
    raw = payload.get("ms_path") or ""
    if not raw:
        raise HTTPException(400, "缺少 ms_path")
    try:
        result = max_bridge.send(raw, OUTPUT)
    except ValueError as e:
        raise HTTPException(403, str(e))
    return result


@app.get("/api/max_status")
def max_status():
    """Max 端一键桥是否在线（供 UI 决定按钮文案）"""
    hb = max_bridge.listener_alive()
    listener = BASE / "scripts" / "mf_max_listener.ms"
    return {"listener": hb,
            "listener_script": str(listener) if listener.exists() else "",
            "inbox": str(max_bridge.INBOX)}


@app.post("/api/open_dir")
def open_dir(payload: dict):
    """在资源管理器中打开产物目录（仅允许打开 output 下的目录）"""
    raw = payload.get("path") or ""
    target = Path(raw).resolve()
    base = OUTPUT.resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(403, "只允许打开输出目录内的路径")
    if not target.exists():
        raise HTTPException(404, "目录不存在")
    if os.name == "nt":
        os.startfile(str(target))
    else:
        raise HTTPException(501, "仅支持 Windows")
    return {"ok": True, "path": str(target)}


# ------------------------------------------------------------------
# 批量：多图一次跑完全流程（推导 -> 自检 -> 导出）
# ------------------------------------------------------------------
# 为什么不让前端循环打 /api/upload + /api/export：
#   那样每张图都要两次往返、两次全量参数解析，且中途失败时前端自己拼不出
#   "第几张失败、失败在哪一步"。这里串行跑完全链，逐项回执，谁成功谁失败一目了然。
# 串行而不是并发：贴图推导是 CPU 密集（多通道 numpy），并发只会互相抢核，
#   反而让总耗时变长，还会把内存峰值顶到 MAX_UPLOAD_MB × 并发数。
MAX_BATCH = 20


@app.post("/api/batch_export")
async def batch_export(files: list[UploadFile] = File(...), payload: str = Form("{}")):
    """多张图各出一套材质 .ms（共用当前面板参数）"""
    try:
        cfg = json.loads(payload or "{}")
        if not isinstance(cfg, dict):
            raise ValueError("payload 必须是对象")
    except Exception as e:
        raise HTTPException(400, f"payload 解析失败: {e}")

    values = cfg.get("values") or {}
    renderer = _resolve_renderer(values, cfg)
    prefix = str(cfg.get("name_prefix") or "").strip()
    if not files:
        raise HTTPException(400, "没有收到任何文件")
    if len(files) > MAX_BATCH:
        raise HTTPException(
            413, f"一次最多 {MAX_BATCH} 张（收到 {len(files)} 张），请分批处理")

    items = []
    for f in files:
        fn = f.filename or "material.png"
        try:
            up = await upload(f)            # 复用单图上传的全部校验与推导
            job_id = up["job_id"]
            base = Path(fn).stem or "MaterialForge"
            name = (prefix + base) if prefix else base
            data = await export({           # 复用单图导出的全部自检与守护
                "job_id": job_id,
                "material_name": name,
                "renderer": renderer,
                "values": values,
            })
            sc = data.get("selfcheck") or {}
            items.append({
                "file": fn, "ok": True, "name": name, "job_id": job_id,
                "ms_path": data.get("ms_path"), "dir": data.get("texture_dir"),
                "renderer": data.get("renderer"),
                "selfcheck_ok": bool(sc.get("ok")),
                "size": up.get("source_size"),
            })
        except HTTPException as e:
            detail = e.detail
            msg = detail.get("msg") if isinstance(detail, dict) else str(detail)
            items.append({"file": fn, "ok": False, "error": msg or "导出被拦截"})
        except Exception as e:
            items.append({"file": fn, "ok": False, "error": f"{type(e).__name__}: {e}"})

    ok_n = sum(1 for i in items if i["ok"])
    return {
        "renderer": renderer,
        "total": len(items),
        "ok_count": ok_n,
        "fail_count": len(items) - ok_n,
        "items": items,
    }


# ------------------------------------------------------------------
# 用户自定义预设
# ------------------------------------------------------------------
# 存在用户目录而不是仓库目录：预设是"这个人的口味"，装 skill 包时不该被打包带走，
# 也不该因为更新 skill 被覆盖掉。
USER_PRESET_DIR = Path.home() / ".3dsmax_material_forge"
USER_PRESET_FILE = USER_PRESET_DIR / "user_presets.json"
MAX_USER_PRESETS = 40
_PRESET_KEY_RE = re.compile(r"^[\w\u4e00-\u9fa5 .\-]{1,24}$")


def _load_user_presets() -> dict:
    if not USER_PRESET_FILE.is_file():
        return {}
    try:
        with open(USER_PRESET_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        # 文件坏了不能连带界面崩：当成空预设表，下次保存时整体重写
        return {}


def _write_user_presets(data: dict) -> None:
    USER_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USER_PRESET_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(USER_PRESET_FILE))


@app.get("/api/presets/user")
def list_user_presets():
    data = _load_user_presets()
    return {"presets": data, "count": len(data), "max": MAX_USER_PRESETS,
            "path": str(USER_PRESET_FILE)}


@app.post("/api/presets/user")
def save_user_preset(payload: dict):
    """把当前参数存成命名预设（只存参数值，不存上传图与贴图）"""
    key = str(payload.get("key") or "").strip()
    values = payload.get("values") or {}
    if not key:
        raise HTTPException(400, "预设名不能为空")
    if not _PRESET_KEY_RE.match(key):
        raise HTTPException(400, "预设名只能是中英文、数字、空格、下划线、点、短横线，且不超过 24 字")
    if not isinstance(values, dict) or not values:
        raise HTTPException(400, "没有可保存的参数")
    # 内置预设的键是英文 key、值是中文名，两者都要拦：只拦 key 的话
    # 用户存一个「镜面」会在界面上和内置的「镜面」并排出两颗同名按钮，套哪个都不确定
    _builtin = set(params.PRESET_LABELS.keys()) | set(params.PRESET_LABELS.values())
    if key in _builtin:
        raise HTTPException(409, f"「{key}」与内置预设同名，请换一个名字")

    data = _load_user_presets()
    if key not in data and len(data) >= MAX_USER_PRESETS:
        raise HTTPException(409, f"自定义预设最多 {MAX_USER_PRESETS} 个，请先删掉不用的")

    # 只收参数表里真实存在的键：tex_enabled / base_color 之类的运行时字段
    # 不进预设，否则套用时会把用户的贴图勾选状态一起顶掉
    valid = set(params.default_values().keys())
    clean = {k: v for k, v in values.items() if k in valid}
    if not clean:
        raise HTTPException(400, "参数全部无效，未保存")

    data[key] = clean
    _write_user_presets(data)
    return {"ok": True, "key": key, "count": len(clean),
            "presets": data, "path": str(USER_PRESET_FILE)}


@app.delete("/api/presets/user/{key}")
def delete_user_preset(key: str):
    data = _load_user_presets()
    if key not in data:
        raise HTTPException(404, "该自定义预设不存在")
    del data[key]
    _write_user_presets(data)
    return {"ok": True, "key": key, "presets": data}


# ------------------------------------------------------------------
# 静态资源
# ------------------------------------------------------------------

@app.get("/files/{job_id}/{rest:path}")
def serve_job_file(job_id: str, rest: str):
    # job_id 白名单校验，防止路径穿越
    if not job_id.isalnum():
        raise HTTPException(400, "非法任务 ID")
    target = (OUTPUT / job_id / rest).resolve()
    base = (OUTPUT / job_id).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(403, "越权访问")
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(target))


if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MF_PORT", "8765"))
    print(f"\n  Material Forge 已启动 ->  http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
