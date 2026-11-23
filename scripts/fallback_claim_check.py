# -*- coding: utf-8 -*-
"""降级文案与降级实现一致性检查（+ 反例自测）

为什么要有这个脚本
----
「降级到 PhysicalMaterial」这件事在仓库里漂移过两次，方向相反：

  1) 0.11.2 之前：实现一个参数都不写，文案却写「参数会尽量映射」——把失败说成近似，
     用户拿到空材质球却以为近似生效。修法是把文案改成「裸材质 / 不做任何还原补偿」。
  2) 0.12.0 之后：tpl_common.FALLBACK 新增「3.5 降级近似映射（通用 PBR 重写）」，
     基色 / 粗糙度 / 金属度 / IOR / 自发光 / 透明 / 贴图真的写进去了，
     而降级分支的文案还停在「不写入任何参数/贴图，只得到一个同名空材质球」——
     把已经做到的事说成没做，用户看到 [FALLBACK] 写入行反而怀疑日志在骗人。

两次都是「文案与实现相反」，靠人工读代码发现不了（两处相隔 500 行、处于不同文件）。
所以这里把「文案承诺」与「实现事实」钉成一条机器判据，任何一边改动而另一边不跟，
下次跑门禁直接 FAIL。判据刻意双向：既拦「实现不写却宣称近似」，也拦「实现写了却宣称什么都没写」。

判据
----
C1  FALLBACK 段真实存在写入（mfSetOn mtl 调用 ≥ MIN_WRITES 处）——近似重写不是空话
C2  tpl_vray / tpl_corona 的降级 WARN 不得出现「不写入任何参数」「空材质球」这类
    与 C1 冲突的断言
C3  同一段文案必须提到 [FALLBACK] 或「近似」，否则用户不知道后面那些行是什么
C4  SKILL.md 的降级说明与 C1 一致（不得再写「不写入任何参数与贴图」）
C5  真实生成物冒烟：V-Ray / Corona 两份 .ms 里同时含有降级文案与 FALLBACK 段
    （防「模板里改对了、拼装顺序把它丢掉了」）
C7  AI 增强 provider 的「可用性声明」与 enhance() 实现同向：enhance 是空实现（只
    return None / 只有 TODO）的 provider，available() 不得靠环境变量返回 True——
    否则用户配了 Key 看到「可用」，调用却静默回落（0.12.0 真实存在过：ScenarioProvider
    看 SCENARIO_API_KEY 却说可用了两个版本）。空实现还必须给出非空 reason，
    否则回落时用户只能猜「是不是 Key 配错了」。反向也拦：有真实现却恒声明不可用。

反例自测（--selftest）
----
8 个反例，任一未被判 FAIL 即整体失败退出：
  N1 文案写回「不写入任何参数/贴图」        -> C2 必须报错
  N2 删掉 FALLBACK 全部 mfSetOn mtl 写入     -> C1 必须报错
  N3 文案去掉 [FALLBACK] / 近似 字样         -> C3 必须报错
  N4 SKILL.md 写回「不写入任何参数与贴图」  -> C4 必须报错
  N5 拼装顺序里剔除 FALLBACK 段              -> C5 必须报错
  N6 文案改成提到「近似」但同时说「不写贴图」 -> C2 必须报错（混合口径也算矛盾）
  N7 ScenarioProvider.available 改回看环境变量 -> C7 必须报错
  N8 删掉空实现 provider 的 reason            -> C7 必须报错

用法
----
    python scripts/fallback_claim_check.py            # 正例
    python scripts/fallback_claim_check.py --selftest # 反例自测
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MIN_WRITES = 6  # FALLBACK 至少要写：基色/粗糙/金属/IOR/自发光/贴图

# 与实现冲突的说法（C2）
CONTRADICTIONS = [
    "不写入任何参数",
    "不写任何参数",
    "一张贴图都不写",
    "不写入任何参数与贴图",
    "空材质球",
    "不做任何还原补偿",
]

TARGETS = {
    "vray": ROOT / "core" / "exporters" / "tpl_vray.py",
    "corona": ROOT / "core" / "exporters" / "tpl_corona.py",
}
COMMON = ROOT / "core" / "exporters" / "tpl_common.py"
SKILL = ROOT / "SKILL.md"
AI = ROOT / "core" / "ai_enhance.py"


def _warn_block(text: str) -> str:
    """取「降级」相关的 WARN 文案行（只审这几行，避免把注释里的历史事故当现行文案）"""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s.startswith("mfLog"):
            continue
        if ("降级" in s) or ("裸材质" in s) or ("空材质球" in s) or ("不等价" in s):
            out.append(s)
    return "\n".join(out)


def _is_stub(fn: ast.AST) -> bool:
    """方法体是否只有 docstring / pass / return None（= 没接线）。"""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant
    ) and isinstance(body[0].value.value, str):
        body = body[1:]
    for n in body:
        if isinstance(n, ast.Pass):
            continue
        if isinstance(n, ast.Return):
            v = n.value
            if v is None or (isinstance(v, ast.Constant) and v.value is None):
                continue
            return False
        return False
    return True


def _always_false(fn: ast.AST) -> bool:
    body = [n for n in fn.body if not isinstance(n, ast.Pass)]
    body = [n for n in body if not (isinstance(n, ast.Expr)
                                    and isinstance(getattr(n, "value", None), ast.Constant)
                                    and isinstance(n.value.value, str))]
    return (len(body) == 1 and isinstance(body[0], ast.Return)
            and isinstance(body[0].value, ast.Constant) and body[0].value.value is False)


def check(roots: dict) -> list[str]:
    """返回问题列表，空列表 = 通过。roots 供反例自测替换文件来源。"""
    probs: list[str] = []

    common = roots["common"].read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'FALLBACK\s*=\s*r?"""(.*?)"""', common, re.S)
    if not m:
        probs.append("C1 FALLBACK 段在 tpl_common.py 里找不到（近似重写被删除或改名）")
        fb = ""
    else:
        fb = m.group(1)
    n_writes = len(re.findall(r"mfSetOn\s+mtl\s", fb))
    if n_writes < MIN_WRITES:
        probs.append(
            f"C1 FALLBACK 段只有 {n_writes} 处 mfSetOn mtl 写入（应 ≥{MIN_WRITES}）："
            "降级近似重写已被削掉，文案必须同步改成「不写」口径")
    if "汇总：写入" not in fb:
        probs.append("C1 FALLBACK 段缺收尾汇总行（写入/跳过计数），用户无法一眼判断近似到底成没成")

    for name, p in roots["tpl"].items():
        if not p.exists():
            probs.append(f"C2 {name} 模板文件缺失: {p}")
            continue
        block = _warn_block(p.read_text(encoding="utf-8", errors="ignore"))
        if not block:
            probs.append(f"C2 {name} 找不到任何降级 WARN 文案（降级分支被删或文案被改写）")
            continue
        for bad in CONTRADICTIONS:
            if bad in block:
                probs.append(
                    f"C2 {name} 降级文案含「{bad}」，而 FALLBACK 实际写入 {n_writes} 处："
                    "文案与实现相反")
        if ("[FALLBACK]" not in block) and ("近似" not in block):
            probs.append(
                f"C3 {name} 降级文案既没提 [FALLBACK] 也没提「近似」，"
                "用户会把随后那些 [FALLBACK] 行当成无关噪声")

    skill = roots["skill"].read_text(encoding="utf-8", errors="ignore")
    if "降级" in skill:
        seg = skill[max(0, skill.find("降级是降级") - 200): skill.find("降级是降级") + 900]
        for bad in ("不写入任何参数与贴图", "不写入任何参数/贴图", "一个参数、一张贴图都不写"):
            if bad in seg:
                probs.append(f"C4 SKILL.md 降级说明仍写「{bad}」，与 FALLBACK 实现冲突")
        if n_writes >= MIN_WRITES and ("FALLBACK" not in seg) and ("近似" not in seg):
            probs.append("C4 SKILL.md 降级说明没提通用 PBR 近似段，用户不知道降级并非空材质")
    else:
        probs.append("C4 SKILL.md 不见「降级」说明（文档段落被删）")

    # C5 拼装顺序：FALLBACK 段必须真的被拼进 _TEMPLATE，且位于参数下发（MID）之后、收尾（TAIL）之前。
    # 这一项盯的是「模板里改对了、拼装时漏掉/放错位」，静态读文件即可，不依赖 import。
    exp = roots["exporter"].read_text(encoding="utf-8", errors="ignore")
    m2 = re.search(r"_TEMPLATE\s*=\s*\((.*?)\)\n", exp, re.S)
    if not m2:
        probs.append("C5 ms_exporter.py 里找不到 _TEMPLATE 拼装块（结构被改写，判据需同步）")
    else:
        block = m2.group(1)
        for tok in ("_TPL_MID", "_TPL_FALLBACK", "_TPL_TAIL"):
            if tok not in block:
                probs.append(f"C5 _TEMPLATE 拼装块缺 {tok}")
        if ("_TPL_FALLBACK" in block) and ("_TPL_MID" in block) and ("_TPL_TAIL" in block):
            if not (block.index("_TPL_MID") < block.index("_TPL_FALLBACK") < block.index("_TPL_TAIL")):
                probs.append("C5 FALLBACK 段拼装顺序错：必须在参数下发(MID)之后、收尾(TAIL)之前，"
                             "否则近似重写会先于真属性写入执行，被后续赋值覆盖")

    # C6 生成物冒烟：真跑一次拼装，确认降级文案与近似段同时出现在同一份 .ms
    try:
        from core.exporters.ms_exporter import build_mscript
        props = {"diffuse": (0.8, 0.2, 0.2), "roughness": 0.35, "metalness": 0.0,
                 "brdf_ior": 1.5, "bump_multiplier": 1.0}
        for r in ("vray", "corona"):
            ms = build_mscript("检查用材质", props, {"albedo": "C:/tmp/a.png"}, renderer=r)
            if "降级" not in ms:
                probs.append(f"C6 {r} 生成物里没有降级分支文案")
            if "3.5 降级近似映射" not in ms:
                probs.append(f"C6 {r} 生成物缺「3.5 降级近似映射」段")
            if "[FALLBACK]" not in ms:
                probs.append(f"C6 {r} 生成物里没有 FALLBACK 写入行")
    except Exception as exc:  # noqa: BLE001
        probs.append(f"C6 生成物冒烟失败: {exc!r}")

    # C7 AI 增强 provider：available() 的声明必须与 enhance() 的实现同向。
    # 这类坑和降级文案是同一族病——说得出来的事和代码做过的事对不上，
    # 只不过一个在日志文案里说谎，一个在「可用性」字段里说谎。
    ai_p = roots.get("ai")
    if ai_p is None or not ai_p.exists():
        probs.append("C7 core/ai_enhance.py 缺失（AI 增强端口被删，判据需同步）")
    else:
        try:
            tree = ast.parse(ai_p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError as exc:
            probs.append(f"C7 core/ai_enhance.py 语法错误，无法静态审查: {exc}")
            tree = None
        if tree is not None:
            seen = 0
            for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
                if not cls.name.endswith("Provider") or cls.name == "BaseProvider":
                    continue
                seen += 1
                fns = {f.name: f for f in cls.body
                       if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
                enh, avail = fns.get("enhance"), fns.get("available")
                stub = enh is None or _is_stub(enh)
                reason = ""
                for st in cls.body:
                    if isinstance(st, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == "reason" for t in st.targets):
                        if isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
                            reason = st.value.value.strip()
                env_wired = bool(avail) and bool(
                    re.search(r"\b(environ|getenv|requires_key)\b", ast.dump(avail)))
                if stub and env_wired:
                    probs.append(
                        f"C7 {cls.name}.enhance() 是空实现（只 return None / 只有 TODO），"
                        "available() 却按环境变量宣称可用——用户配好 Key 看到「可用」，"
                        "调用却静默回落")
                if stub and not reason:
                    probs.append(
                        f"C7 {cls.name}.enhance() 是空实现却没有为非空 reason，"
                        "回落时用户只能猜是不是 Key 配错了")
                if (not stub) and avail is not None and _always_false(avail):
                    probs.append(
                        f"C7 {cls.name}.enhance() 有真实实现，available() 却恒返回 False："
                        "做好的能力被声明成不可用，等同另一种口径不一致")
            if not seen:
                probs.append("C7 core/ai_enhance.py 里找不到任何 provider 类（判据需同步）")

    return probs


def _roots(base: Path) -> dict:
    return {
        "common": base / "core" / "exporters" / "tpl_common.py",
        "tpl": {
            "vray": base / "core" / "exporters" / "tpl_vray.py",
            "corona": base / "core" / "exporters" / "tpl_corona.py",
        },
        "exporter": base / "core" / "exporters" / "ms_exporter.py",
        "skill": base / "SKILL.md",
        "ai": base / "core" / "ai_enhance.py",
    }


def _stage(tmp: Path) -> Path:
    """把被检查的文件复制到临时目录，反例在副本上改，绝不碰真实文件"""
    for rel in ("core/exporters/tpl_common.py", "core/exporters/tpl_vray.py",
                "core/exporters/tpl_corona.py", "core/exporters/ms_exporter.py",
                "core/ai_enhance.py", "SKILL.md"):
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    # C5 需要真实模块：临时目录里要有可导入的 core 包
    for rel in ("core/__init__.py", "core/exporters/__init__.py"):
        src = ROOT / rel
        if src.exists():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp / rel)
    return tmp


def selftest() -> int:
    cases: list[tuple[str, str, str, str]] = [
        # key, 说明, 变体类型, 关键参数
        ("N1", "文案写回「不写入任何参数/贴图」", "patch_text", "core/exporters/tpl_vray.py|近似重写|不写入任何参数/贴图"),
        ("N2", "FALLBACK 写入被全部删掉", "strip_fallback", ""),
        ("N3", "文案去掉 FALLBACK/近似 字样", "patch_text", "core/exporters/tpl_corona.py|近似重写|重写"),
        ("N4", "SKILL.md 写回旧口径", "patch_skill", ""),
        ("N5", "拼装顺序丢失 FALLBACK 段", "patch_text", "core/exporters/ms_exporter.py|    + _TPL_FALLBACK|"),
        ("N6", "混合口径：提近似又说没写贴图", "patch_text", "core/exporters/tpl_corona.py|近似重写|近似重写（不写入任何参数/贴图）"),
        ("N7", "空实现 provider 按环境变量宣称可用", "patch_text",
         "core/ai_enhance.py|        return False  # 未接入：不因环境变量存在就宣称可用|        return bool(os.environ.get(\"SCENARIO_API_KEY\"))"),
        ("N8", "空实现 provider 没有回落原因", "patch_text",
         "core/ai_enhance.py|    reason = \"预留位：endpoint 与认证头尚未接入，配置 SCENARIO_API_KEY 也不会生效；当前请用 generic-http 或本地程序化推导\"|"),
    ]
    failed = 0
    for key, desc, kind, arg in cases:
        with tempfile.TemporaryDirectory() as td:
            tmp = _stage(Path(td))
            ok = True
            if kind == "patch_text":
                fname, old, new = arg.split("|", 2)
                tgt = tmp / fname
                t = tgt.read_text(encoding="utf-8")
                if old not in t:
                    print(f"  {key} 反例构造失败：{fname} 里找不到锚点 {old!r}")
                    ok = False
                else:
                    tgt.write_text(t.replace(old, new, 1), encoding="utf-8")
            elif kind == "strip_fallback":
                tgt = tmp / "core" / "exporters" / "tpl_common.py"
                t = tgt.read_text(encoding="utf-8")
                t2 = re.sub(r"mfSetOn\s+mtl\s", "mfSetOnNever mtl ", t)
                if t2 == t:
                    print(f"  {key} 反例构造失败：没找到 mfSetOn mtl 写入")
                    ok = False
                tgt.write_text(t2, encoding="utf-8")
            elif kind == "patch_skill":
                tgt = tmp / "SKILL.md"
                t = tgt.read_text(encoding="utf-8")
                anchor = "降级是降级"
                if anchor not in t:
                    print(f"  {key} 反例构造失败：SKILL.md 找不到锚点")
                    ok = False
                else:
                    tgt.write_text(
                        t.replace(anchor, "降级时脚本**不写入任何参数与贴图**", 1),
                        encoding="utf-8")
            if not ok:
                failed += 1
                continue
            probs = check(_roots(tmp))
            if probs:
                print(f"  {key} PASS  {desc} → 判 FAIL（{probs[0][:60]}…）")
            else:
                print(f"  {key} FAIL  {desc} → 竟然通过了，判据没有判别力")
                failed += 1
    print()
    if failed:
        print(f"反例自测汇总：{failed} 例未被抓住，判据失效")
        return 1
    print(f"反例自测汇总：{len(cases)}/{len(cases)} 全部被抓住（ALL PASS）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="声明与实现一致性检查（降级文案 + AI provider 可用性）")
    ap.add_argument("--selftest", action="store_true", help="跑 8 个反例，验证判据有判别力")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    probs = check(_roots(ROOT))
    if probs:
        print("声明-实现一致性检查：FAIL")
        for p in probs:
            print(f"  [FAIL] {p}")
        return 1
    n = len(re.findall(r"mfSetOn\s+mtl\s",
                       re.search(r'FALLBACK\s*=\s*r?"""(.*?)"""',
                                 COMMON.read_text(encoding="utf-8"), re.S).group(1)))
    print(f"声明-实现一致性检查：V-Ray / Corona 两侧降级文案与 FALLBACK 实现一致（写入点 {n} 处）")
    print("声明-实现一致性检查：AI provider 的 available() 与 enhance() 实现同向，空实现均带 reason")
    n_prov = len([c for c in ast.parse(
        AI.read_text(encoding="utf-8", errors="ignore")).body
        if isinstance(c, ast.ClassDef) and c.name.endswith("Provider")
        and c.name != "BaseProvider"])
    print(f"汇总：{len(TARGETS)} 个渲染器文案 + SKILL.md + 生成物冒烟 + {n_prov} 个 AI provider，ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
