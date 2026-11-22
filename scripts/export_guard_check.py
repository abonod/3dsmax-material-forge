# -*- coding: utf-8 -*-
"""导出一致性检查：贴图隔离 / 导出自检 / 任务生命周期

为什么要有这个脚本：
    0.8.2 之前的三个坑都不是「语法错」，静态检查、单测都抓不到：
      1) 贴图固定命名 + 同 job 共享 textures/ → 换材质重导，旧 .ms 读到新颜色；
      2) 贴图复制失败是 except: pass，导出照样报成功，进 Max 才炸；
      3) 上传全量读内存、job 常驻、产物无回收。
    这三个都是「行为」问题，只能真跑一遍导出链路才看得见。本脚本直接调
    app.py 的 async 端点（不起 HTTP 服务、不起 3ds Max），把行为钉死。

用法：python scripts/export_guard_check.py    （退出码 0 = 全过）
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

import numpy as np
from PIL import Image
from starlette.datastructures import UploadFile

import app as A
from core import export_guard

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def _png(w=384, h=384) -> bytes:
    rng = np.random.default_rng(7)
    base = (rng.random((h, w)) * 40 + 110).astype("uint8")
    arr = np.stack([base, base, base], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return buf.getvalue()


def part_isolation():
    print("[1] 材质隔离：同名贴图不得互相覆盖")
    up = asyncio.run(A.upload(UploadFile(filename="wall.png", file=io.BytesIO(_png()))))
    job = up["job_id"]
    check(job, "上传成功 job=%s" % job)
    check(up.get("source_size") == [384, 384], "源图尺寸回显 %s" % (up.get("source_size"),))

    outs = []
    for name, color in (("墙面_白", "#f2f0ea"), ("墙面_米黄", "#e6d3a8")):
        res = asyncio.run(A.export({"job_id": job, "material_name": name,
                                    "values": {"base_color": color}}))
        outs.append(res)
        check(bool(res.get("ms_path")), "导出[%s] 生成 .ms" % name)

    d1, d2 = outs[0]["texture_dir"], outs[1]["texture_dir"]
    check(d1 != d2, "两材质目录不同：%s / %s" % (os.path.basename(d1), os.path.basename(d2)))
    a1 = os.path.join(d1, "textures", "albedo.png")
    a2 = os.path.join(d2, "textures", "albedo.png")
    check(os.path.isfile(a1) and os.path.isfile(a2), "两套 albedo.png 都在")
    check(open(a1, "rb").read() != open(a2, "rb").read(), "两套 albedo.png 内容不同（未覆盖）")

    sc = outs[0].get("selfcheck") or {}
    check(bool(sc), "导出返回带 selfcheck")
    check(sc.get("ok") is True, "自检通过（missing=%s）" % (sc.get("missing"),))
    check(sc.get("checked", 0) >= 1, "checked=%s" % sc.get("checked"))
    check(sc.get("packed", {}).get("count", 0) >= 1, "打包 %s 张" % sc.get("packed", {}).get("count"))
    check(not sc.get("broken_refs"), "无断链引用")

    rel = outs[0]["script_url"].replace("/files/%s/" % job, "")
    check(os.path.isfile(os.path.join(str(A.OUTPUT), job, rel.replace("/", os.sep))),
          "script_url 指向真实文件：%s" % rel)
    return job, d2


def part_selfcheck(out_dir):
    print("[2] 导出自检：缺图/断链必须报出来，不许静默成功")
    ghost = os.path.join(out_dir, "textures", "ghost.png")
    sc = export_guard.check_export(
        'bitmapTexture filename:"%s"' % ghost.replace("\\", "\\\\"),
        {"normal": ghost}, A.params.default_values(), out_dir=out_dir, has_source=True)
    check(sc["ok"] is False, "缺图时 ok=False")
    check(bool(sc["missing"]), "缺图进入 missing：%s" % (sc["missing"][:1],))
    check(bool(sc["broken_refs"]), "脚本断链进入 broken_refs")

    root = Path(tempfile.mkdtemp(prefix="mf_guard_"))
    good = root / "albedo.png"
    Image.new("RGB", (4, 4), (180, 180, 180)).save(good)
    script = 'bitmapTexture filename:"%s"' % good.as_posix()
    sc2 = export_guard.check_export(script, {"albedo": str(good)},
                                    A.params.default_values(), out_dir=str(root))
    check(sc2["ok"] is True, "完整场景判 ok（不误报）")
    good.unlink()
    sc3 = export_guard.check_export(script, {"albedo": str(good)},
                                    A.params.default_values(), out_dir=str(root))
    check(sc3["ok"] is False, "删图后判失败")

    expect = export_guard.expected_channels({"normal_on": True, "bump_on": True,
                                             "ao_on": True, "opacity_on": True}, True)
    check(set(expect) == {"albedo", "roughness", "normal", "height", "ao", "opacity"},
          "全开关通道集合 = 六通道（实际 %s）" % expect)
    solid = export_guard.expected_channels(A.params.default_values(), False)
    check("opacity" not in solid, "纯色模式不期望 opacity（实际 %s）" % solid)


def part_selfcheck_disk(job):
    print("[2b] 自检只认磁盘：读盘失败/编码不符必须判 FAIL，不吃内存草稿")
    base = {"job_id": job, "values": {}}

    # 2b-1 正常链路：自检对象就是磁盘上那份 .ms，且编码来自写盘侧
    one = asyncio.run(A.export({**base, "material_name": "自检_磁盘"}))
    sc = one.get("selfcheck") or {}
    src = sc.get("source") or {}
    check(sc.get("ok") is True, "正常导出判 ok")
    check(os.path.abspath(str(src.get("file"))) == os.path.abspath(str(one.get("ms_path"))),
          "自检对象 = 落盘 .ms（%s）" % os.path.basename(str(src.get("file"))))
    check(src.get("encoding") == one.get("script_encoding"),
          "自检编码 = 实际写盘编码（%s）" % (src.get("encoding"),))
    check(int(src.get("bytes") or 0) > 0, "自检读到非空文件（%s 字节）" % (src.get("bytes"),))

    # 2b-2 乱码误判回归：材质名带 GBK 编不出的字符 → 导出层走 UTF-8 兜底
    #       （preflight 会拦这类名字，这里绕过 UI 直接打导出层，专测回读编码）
    utf_dir = str(Path(tempfile.mkdtemp(prefix="mf_utf8_")))
    try:
        meta = A.ms_exporter.export_with_meta(
            material_name="墙面🔥", vray_props={}, tex_paths={}, out_dir=utf_dir,
            pack_textures=False)
        check(meta["encoding"] == "utf-8",
              "UTF-8 兜底被触发（encoding=%s）" % (meta["encoding"],))
        good = export_guard.check_export_file(
            meta["path"], {}, A.params.default_values(), out_dir=utf_dir,
            has_source=False, encoding=meta["encoding"])
        check(good["ok"] is True, "UTF-8 产物按真实编码回读判 ok（GBK 硬解乱码误判已消除）")
        wrong = export_guard.check_export_file(
            meta["path"], {}, A.params.default_values(), out_dir=utf_dir,
            has_source=False, encoding="gbk")
        check(wrong["ok"] is False and "读盘失败" in str(wrong.get("file_error") or ""),
              "编码不符（UTF-8 内容按 GBK 读）判 FAIL：%s" % (wrong.get("file_error"),))
    finally:
        import shutil as _sh
        _sh.rmtree(utf_dir, ignore_errors=True)

    # 2b-3 / 2b-4 读盘失败与空文件：一律 FAIL，没有"回退内存草稿"这条路
    tmp = Path(tempfile.mkdtemp(prefix="mf_disk_"))
    try:
        ghost = str(tmp / "nope.ms")
        sc2 = export_guard.check_export_file(ghost, {}, A.params.default_values())
        check(sc2["ok"] is False and "读盘失败" in str(sc2.get("file_error") or ""),
              "文件不存在判 FAIL（不回退草稿）：%s" % (sc2.get("file_error"),))

        empty = tmp / "empty.ms"
        empty.write_bytes(b"")
        sc3 = export_guard.check_export_file(str(empty), {}, A.params.default_values())
        check(sc3["ok"] is False and "为空" in str(sc3.get("file_error") or ""),
              "空脚本判 FAIL：%s" % (sc3.get("file_error"),))
    finally:
        for f in tmp.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass

    # 2b-5 源码层护栏：假通过的两条老路不许回来
    app_src = Path(ROOT, "app.py").read_text(encoding="utf-8")
    check('errors="replace"' not in app_src, 'app.py 不再用 errors="replace" 硬解脚本')
    check("export_guard.check_export(" not in app_src,
          "app.py 自检不再直接传内存文本（必须走 check_export_file）")
    check("final_script = draft" not in app_src, "app.py 不再把内存 draft 回退给自检")


def part_limits():
    print("[3] 上传边界：超限必须被拒，且不进内存")
    from fastapi import HTTPException

    def probe(raw, name):
        try:
            asyncio.run(A.upload(UploadFile(filename=name, file=io.BytesIO(raw))))
            return 0
        except HTTPException as e:
            return e.status_code

    check(probe(b"\x89PNG\r\n\x1a\n" + b"\0" * (33 * 1024 * 1024), "big.png") == 413, "33MB 被拒 413")
    wide = io.BytesIO()
    Image.new("RGB", (A.MAX_SIDE + 1, 60), (128, 128, 128)).save(wide, "PNG")
    check(probe(wide.getvalue(), "wide.png") == 413, "超单边像素被拒 413")
    huge = io.BytesIO()
    Image.new("L", (6500, 6500), 128).save(huge, "PNG")
    check(probe(huge.getvalue(), "huge.png") == 413, "超总像素被拒 413")
    check(probe(b"\x89PNG\r\n\x1a\nbroken", "bad.png") == 400, "损坏文件被拒 400")
    check(probe(b"", "empty.png") == 400, "空文件被拒 400")


def part_lifecycle(job):
    print("[4] 任务生命周期：TTL 回收中间过程，交付物保留")
    jd = os.path.join(str(A.OUTPUT), job)
    A.JOBS[job]["created_at"] = time.time() - A.JOB_TTL_SECONDS - 10
    removed = A._purge_jobs()
    check(removed.get("trimmed", 0) >= 1 or removed.get("dir", 0) >= 1, "TTL 触发 %s" % (removed,))
    check(os.path.isdir(os.path.join(jd, "export")), "export/ 交付物保留")
    check(not os.path.isdir(os.path.join(jd, "textures")), "中间过程贴图已回收")
    check(job not in A.JOBS, "过期 job 已出内存表")

    # 内存上限：塞满后按 created_at 淘汰最旧
    keep = dict(A.JOBS)
    try:
        now = time.time()
        for i in range(A.MAX_JOBS + 5):
            A.JOBS["p%04d" % i] = {"created_at": now + i}
        removed2 = A._purge_jobs()
        check(removed2.get("mem", 0) >= 5, "内存淘汰 %s 条" % removed2.get("mem"))
        check(len(A.JOBS) <= A.MAX_JOBS, "内存表不超上限（%d）" % len(A.JOBS))
    finally:
        A.JOBS.clear()
        A.JOBS.update(keep)


def main():
    job, out_dir = part_isolation()
    part_selfcheck(out_dir)
    part_selfcheck_disk(job)
    part_limits()
    part_lifecycle(job)
    print()
    if FAILS:
        print("结论：%d 项失败" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("结论：ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
