# 证据台账（Evidence Ledger，0.11.0 起）

这份文件只做一件事：把「已经真机验过的」和「还没验、只是按推理写好的」分开列清楚。

为什么要单独建台账：项目里最容易发生的不是代码写错，而是**证据被夸大**——
"在本机跑过一次没报错"被记成"已验证"，"打算支持 2021"被记成"已支持 2021"。
一旦台账上写着"支持"，使用者在别的机器上踩坑时，会先怀疑自己而不是怀疑文档。

台账规则：
1. 只有**本机真机跑出的日志或截图**才能进「已验证」；
2. 静态检查通过 ≠ 已验证，那是「机制在位」；
3. 每一行必须能追到一份具体文件（日志 / png / json），写不出文件路径的就是没验过。

---

## 1. 当前真机基线

| 项 | 值 | 证据 |
|---|---|---|
| 3ds Max | 2024（中文版） | `docs/ondevice-0.10.0-5items.txt`、`docs/p0-ondevice-0.8.3.txt` |
| V-Ray | 6.20.06（`C:\ProgramData\Autodesk\ApplicationPlugins\VRay3dsMax2024`） | 同上 |
| Corona | 12（`C:\Program Files\Chaos\Corona\...`） | `docs/corona-ondevice-0.9.1.txt` |
| 语言 | 中文（类名本地化：`VRay_法线贴图`、`Corona法线`、`Corona置换修改器`、`_Corona物理材质`） | 同上 |

结论：**三个产品都只有一个版本被真机验证过**。矩阵里其余版本都是 supported / untested。

---

## 2. P3 / P5 / P6 / P7：跨版本适配的证据状态

| 改进项 | 机制状态 | 真机证据 | 说明 |
|---|---|---|---|
| P3 英文版 Max | 探测段已注入，日志输出 `MAX_LANG` | **无** | 本机只有中文版，无法取证。代码侧已把中英注册名都进候选链（`compat_check.py` B 项硬校验），但"英文版能跑"这句话目前只能写"代码路径覆盖"，不能写"已验证" |
| P3 第二渲染器 | V-Ray + Corona 双模板齐备 | 有（两渲染器各一轮真机） | 这是八项里唯一真正双渲染器都验过的部分 |
| P5 Max 低/高版本 | 材质库保存双向降级（`saveMaterialLibrary` → `saveTempMaterialLibrary`）；`saveTempMaterialLibrary` 仍被 preflight 允许，但必须排在后面 | **无** | 本机无 2021 及更早、也无 2025/2026。降级顺序写反会被 preflight 判 ERROR |
| P6 V-Ray 版本 | 材质创建改类表精确比对 + execute 兜底；属性候选链多层 | 仅 6.20.06 | V-Ray 5 / 7 未验。属性名差异（如 `texmap_bump_multiplier` 在 5.x 是否同名）没有任何证据 |
| P7 Corona 版本 | 能力缺失走 `mfDegrade`，日志标 `DEGRADED` 而不是假装 OK | 仅 Corona 12 | Corona 9 及更早缺 `additionalBumpOn`，代码里已按"能力缺失"降级，但没有真机验证该分支 |

### 想要把某行从「无」变成「有」，必须做的事

将来的验证只有一种形态被承认：**换机器/换版本跑一次探针，把原始日志放进 `docs/`**。

```
# 在目标机器上（装好目标 Max + 渲染器）
python scripts/gen_probe.py --ms-dir out --out out/probe      # 生成探针
# 在 Max 里 Run Script 跑 out/probe/probe.ms，Listener 存成 xxx.log
python scripts/verify_probe_log.py xxx.log --require-compat    # 判据器
```

`--require-compat` 会强制要求日志里出现版本证据行（`MAX_VERSION` / `COMPAT_LEVEL` 等），
缺了直接判 FAIL —— 这样"在某版本上验过"这句话永远带着版本号，不会再出现
"验过，但不知道验的是哪个版本"的记录。

---

## 3. P4：目视证据（色散 / 法线凹凸叠加）

属性回读能证明"值写进去了"，证明不了"渲出来对不对"。色散回读 `refraction_dispersion=12`
是绿的，但渲染可能完全看不到彩虹边；法线凹凸两个槽都回读成功，叠加后也可能互相吃掉细节。

### 已就绪（机制）

```
python scripts/gen_visual_probe.py --ms-dir <含 vray/ corona/ 的目录> --out out/visual
```

产出：
- `out/visual/visual_probe.ms` —— 固定场景（相机 + 主光 + 受光立方体 + 深色背景，720×540，`vfb:off`），逐个材质渲 PNG
- `out/visual/visual_manifest.json` —— 待目视项清单，每项含判据 `expect` 与 `passed: null`

清单里 `passed` 初值强制为 `null`，`visual_gen_check.py` 会校验这一点：
**没看过图就填 true 属于伪造证据**，检查会直接拦。

### 真机取证结果（2026-11-20，Max 2024 中文版 / V-Ray 6.20.06 / Corona 12）
| 目视项 | 判据 | 状态 | 证据文件 | 结论 |
|---|---|---|---|---|
| dispersion 色散（V-Ray） | 高光边缘彩虹分离；无分离即不合格 | 已目视 | docs/evidence/dispersion_vray_ON.png | 折射棋盘处红蓝分离明显，通过 |
| normal_bump 叠加（V-Ray） | 大尺度起伏与细粒凹凸同时可见 | 已目视 | docs/evidence/normal_bump_vray_ON.png | 细密纹理起伏可见，通过 |
| normal_bump 对照（V-Ray，关凹凸） | 与 ON 图应有明确差异 | 已量化 | docs/evidence/normal_bump_vray_OFF.png | 平均绝对差 27.41、76% 像素差>10、梯度 2.82 vs 2.01，凹凸真实生效（非几何面片假象） |
| normal_bump 叠加（Corona） | 同上 | 已目视 | docs/evidence/normal_bump_corona_ON.png | 高光区棋盘格扭曲，微观起伏生效，通过 |

取证中修掉的两个真机缺陷（旧逻辑都会把它们吞成"静默通过"）：
1. visual_probe 模板第 153 行 `StandardMaterial name:"__MF_SENTINEL__` 缺闭合引号，真机 fileIn 直接语法错误；
2. V-Ray 降采样用了真机不存在的属性名 progressive_maxSubdivs / dmc_minSubDivs（真机实名 progressive_max_render_time / progressive_maxSamples / progressive_noise_threshold / adaptiveSubdivision_*），整段 try 静默落空，单张渲图耗时 900s。

### 3.1 探针真机取证（P3/P5/P6/P7 的版本证据链）
- 流程：python scripts/gen_probe.py --ms-dir <ms> --out probe.ms --log probe_run.log → 真机 fileIn → python scripts/verify_probe_log.py <log> --expect-jobs 2 --require-compat
- 证据：docs/evidence/probe_max2024_vray62006_corona12.log
- 结果：C1–C6 全 PASS（SCRIPT_RUN=1 / FILEIN 2/2 / BIND_MISMATCH=0 / VERDICT=OK / BAD=0 / COMPAT_LEVEL=supported / MAX_VERSION=26000 / MAX_LANG=zh）
- 修复的假通过缺口：gen_probe 此前**没有**注入版本探测段，日志不含 COMPAT_LEVEL 与 BAD 行 —— 即 P3/P5/P6/P7 声称的"--require-compat 强制版本证据"在真机上必然判 FAIL，此前从未真机跑过，属"机制在、证据无"。现改用 compat.probe_ms_logfn() 注入，并新增槽位回读 BAD 计数；probe_gen_check 对该层做回归拦截。

## 3.2 P0–P7 落地审计与三处「文档/证据说谎」事故（0.11.1）

八项修复散落在模板、脚本、文档三处，靠回忆核对必然遗漏。0.11.1 把「到底改没改」变成可执行判据：

```
python scripts/p_series_audit.py            # 8 项逐条判据
python scripts/p_series_audit.py --selftest # 6 个反例，验证判据有判别力
```

两项均已注册进 `run_all_checks`（`p_series` / `p_series_neg`），任何一项回退即整体非零退出。

| 项 | 判据 | 本次实测 |
|---|---|---|
| P0 | `gen_param_gap.py --check` 返回 0（文档必须由 params.py 生成） | PASS |
| P1 | 判据器自测 12 例 + 探针含 BIND_MISMATCH / BAD 行 / 哨兵与逐 job 清槽 | PASS |
| P2 | Corona 需 additionalBumpOn + additionalBump + normalMap + getProperty 回读；V-Ray 需 normal_map + bump_map | PASS |
| P3 | compat_check 25 项 OK，别名链覆盖 5 类，verified 必带 evidence | PASS |
| P4 | 取证生成器 + docs/evidence 真机 PNG ≥ 4 张 + 台账声明已目视 | PASS（4 张） |
| P5 | saveMaterialLibrary 在 saveTempMaterialLibrary 之前且 both 有 catch 兜底 | PASS |
| P6 | Material.classes 精确比对且含中文本地化名 | PASS |
| P7 | 矩阵覆盖 Max2021–2026 / V-Ray5–7 / Corona9–13 + 探测段 + verified 必带 evidence + 证据文件随包分发 | PASS |

反例自测（判据必须能拦住）：
- P5 顺序倒置 → FAIL　- P6 去掉类表比对 → FAIL　- P7 verified 缺 evidence 字段 → FAIL
- P7 证据未随包分发（白名单改回空）→ FAIL　- P2 全局删掉 additionalBumpOn → FAIL　- P0 文档改一个字 → FAIL

### 事故一：自动生成的文档被外部编辑器加水印，导致一致性检查说谎

`docs/vray-param-gap.md` 被带 AI 水印的编辑器打开并保存过，文件头多了 `AIGC:` YAML 块、文件尾多了「内容由AI生成」声明，正文一字未改，`--check` 却报漂移。
处理：`gen_param_gap.py` 增加 `strip_watermarks()`，比较前剔除这两类水印；同时保留反例——正文改一个字仍判 FAIL，不允许借「净化」把真实漂移放过去。

### 事故二：verified 的证据字段写成了「本机叠加链真机日志」

`core/compat.py` 里 Max2024 / V-Ray6 / Corona12 三项 `verified` 的 evidence 只有一句含糊描述，看的人无法判断是哪个文件、跑到什么程度。
处理：改为指向归档文件与关键字段值（`docs/evidence/probe_max2024_vray62006_corona12.log`，附 MAX_VERSION / MAX_LANG / COMPAT_LEVEL / BAD 与目视 PNG），并重生成 `docs/version-matrix.md`。

### 事故三：证据文件根本没进包，证据链在分发环节断掉

`package_skill.py` 的 `EXCLUDE_SUFFIX` 把 `.log` 全局排除（本意是挡调试残留），而 `core/compat.py` 三个 `verified` 项的 `evidence` 正指向 `docs/evidence/*.log`。
后果：包里写着「支持 Max2024 / V-Ray6 / Corona12」，支撑这份结论的真机日志却没随包——用户拿到包无法自证，且**没有任何一项检查覆盖它**（反例自测只验证字段在不在，不验证文件在不在），属静默断链。

处理三条：
1. `docs/evidence` 下的 `.log` 走白名单进包（只对证据目录放行，别处 `.log` 仍排除）；
2. 新增 `verify_evidence_chain()`：出包前先**预演**将要进包的文件清单，凡代码/文档引用的 `docs/evidence/*` 不在清单内即中止打包，dist 保持原样、不产半成品包；
3. 同一实现被 `p_series_audit.py` 的 P7 判据复用，并配反例（把白名单改回空必须 FAIL）——否则打包门禁与日常审计会变成两套各自漂移的判据。

## 4. 这张台账怎么用

- **对外说明能力时**：只引「已验证」列，`无` 的行必须说"代码路径已覆盖，未在目标版本验证"。
- **改代码前**：先看要动的那项是不是只有机制没有证据，动完必须补该行的验证方式。
- **每次交付**：`python scripts/compat_check.py` 会强制 `MATRIX` 里 verified 的版本恰好等于本机基线，
  想在矩阵里给新版本加 verified，必须同时改 `LOCAL_BASELINE` 并附 `evidence` 字段——这道门就是防台账被手写成宣传材料。
