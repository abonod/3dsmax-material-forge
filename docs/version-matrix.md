# 版本适配矩阵（自动生成，禁止手改）

> 本文件由 `scripts/gen_version_matrix.py` 从 `core/compat.py` 生成。
> 改矩阵请改 `core/compat.py`，再跑一次生成器；`--check` 会在漂移时返回非零。

## 一句话结论

**只有本机基线（3ds Max 2024 中文版 / V-Ray 6 / Corona 12）到 `verified`。**
其余版本一律 `supported` 或 `untested`——这不是「打算支持」，是「尚未取证」。把 `supported` 说成「支持」属于虚假宣传，文档与日志统一用上述三档词。

## 等级语义

| 等级 | 含义 |
| --- | --- |
| `verified` | 真机日志已验证（日志须通过 scripts/verify_probe_log.py） |
| `supported` | 代码已按该版本差异分支，缺真机证据 |
| `untested` | 未做针对性适配，不做承诺 |

## 3ds Max（6 个版本）

| 版本 | 等级 | 已知差异 / 依据 |
| --- | --- | --- |
| 2021 | `untested` | saveTempMaterialLibrary 仍存在（2022+ 移除） |
| 2022 | `untested` | saveTempMaterialLibrary 已移除，必须用 saveMaterialLibrary |
| 2023 | `untested` | bitmap 的 apply 参数行为微调 |
| 2024 | `verified` | 本机真机基线：中文版，saveTempMaterialLibrary 已移除（证据：docs/evidence/probe_max2024_vray62006_corona12.log（C1–C6 全 PASS，MAX_VERSION=26000 / MAX_LANG=zh / COMPAT_LEVEL=supported / BAD=0）） |
| 2025 | `supported` | 类注册表改为延迟注册，classes 字符串比对仍有效 |
| 2026 | `supported` | 材质编辑器槽位访问路径未变，meditMaterials 仍可用 |

## V-Ray（3 个版本）

| 版本 | 等级 | 已知差异 / 依据 |
| --- | --- | --- |
| 5 | `untested` | 无 coat_bump / 无 refraction_dispersion_on |
| 6 | `verified` | 本机真机基线：6.20.06，coat_bump 与 refraction_dispersion 均可用（证据：同上探针日志：V-Ray 侧法线/凹凸叠加与色散真机回读 + docs/evidence 四张目视 PNG） |
| 7 | `supported` | 属性名向后兼容；新增 thin film 不影响既有槽位 |

## Corona（5 个版本）

| 版本 | 等级 | 已知差异 / 依据 |
| --- | --- | --- |
| 9 | `untested` | Normal 节点无 additionalBump* 三件套 |
| 10 | `untested` | additionalBump 系列引入，属性名可能与本机不同 |
| 11 | `supported` | additionalBump[On/Strength] 齐备 |
| 12 | `verified` | 本机真机基线：Corona 12，additionalBump 三属性齐备（证据：同上探针日志：Corona 侧 additionalBumpOn 回读为 true + docs/evidence 目视 PNG） |
| 13 | `supported` | 物理材质类名保持 _Corona物理材质 |

## 跨语言：类名本地化映射

中文版 3ds Max 会把插件注册名一起本地化，`execute("英文名")` 在中文版上会静默失败。所有类名定位必须先对 `classes` 做字符串精确比对。当前覆盖：

| 概念 | 英文注册名 | 中文注册名 |
| --- | --- | --- |
| `vray_mtl` | `VRayMtl`, `VRay_Mtl` | `VRay_材质`, `VRayMtl` |
| `vray_normalmap` | `VRayNormalMap` | `VRay_法线贴图`, `VRayNormalMap` |
| `corona_mtl` | `CoronaMtl`, `CoronaPhysicalMtl` | `_Corona物理材质`, `Corona物理材质` |
| `corona_normal` | `CoronaNormal` | `Corona法线`, `CoronaNormal` |
| `corona_disp` | `CoronaDisplacementMod` | `Corona置换修改器`, `CoronaDisplacementMod` |

## 现场取证方法（把 untested 变 verified 的唯一路径）

1. 在目标机器上导出材质：生成脚本位于 `<out>/<renderer>/*.ms`；
2. 生成探针：`python scripts/gen_probe.py --ms-dir <out> --out probe.ms`；
3. 在 3ds Max 里 Run Script，把 Listener 输出存成 `probe.log`；
4. 判定：`python scripts/verify_probe_log.py probe.log --expect-jobs <N>`；
5. 只有第 4 步 PASS，且日志里 `COMPAT_LEVEL` 非 `untested`，才允许把该版本改成 `verified`。

日志里的版本证据字段（由 `core/compat.py:probe_ms()` 注入）：
`MAX_VERSION`、`MAX_LANG`、`ACTIVE_RENDERER`、`VRAY_AVAILABLE`、`CORONA_AVAILABLE`、`COMPAT_LEVEL`。

## 待补证据清单（P3 / P4 遗留）

| 项 | 现状 | 需要什么 |
| --- | --- | --- |
| 英文版 Max | 无证据 | 在英文版 Max 上跑一次探针，确认类名走 en 分支 |
| 第二渲染器真机 | 仅 V-Ray 6 / Corona 12 | 按上表 `supported` 版本各跑一次 |
| 色散目视 | 仅属性回读 | 渲染色散材质截图，人眼确认边缘分离 |
| 法线+凹凸双层目视 | 仅属性回读 | 渲染双层叠加材质截图，确认两层细节同时可见 |

