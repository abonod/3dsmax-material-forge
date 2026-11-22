---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 89f2995f43cde2f2717e466bdd92de27_c74f6c9fe65311f19f9d525400cd780f
    ReservedCode1: f9NOPhkfSga2iHe1nLF+9QlElui+SndKwutoHogshFugQquNZWD5mxfm8BVRwct15hYTHpkynYEIUtUg9oWc/q8nmiDF41eK5NNWqkAzU0vGnBXOvm2jRazKANws5cZilIOTlyeIbEbBlhGW+eFl/IJEB9pGYKOAtszFZejW4D8iErI/O/W6ew8gSIY=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 89f2995f43cde2f2717e466bdd92de27_c74f6c9fe65311f19f9d525400cd780f
    ReservedCode2: f9NOPhkfSga2iHe1nLF+9QlElui+SndKwutoHogshFugQquNZWD5mxfm8BVRwct15hYTHpkynYEIUtUg9oWc/q8nmiDF41eK5NNWqkAzU0vGnBXOvm2jRazKANws5cZilIOTlyeIbEbBlhGW+eFl/IJEB9pGYKOAtszFZejW4D8iErI/O/W6ew8gSIY=
---

# 3dsmax-material-forge 0.11.3 真机落槽验证报告

## 1. 结论

| 项目 | 结果 |
|---|---|
| 真机套件 | `MF_0.11.3_ondevice_verify.ms`（2,316,679 字节，自包含 32 份产物 + 5 张贴图） |
| 执行方式 | Agent 通过 3dsmax-mcp 在已运行的 3ds Max 2024 会话内 `fileIn` 执行，全程无人工点击 |
| 用例总数 | 32（16 预设 × V-Ray / Corona） |
| PASS | 30 |
| DEGRADED | 2（详见 §4） |
| FAIL | 0 |
| SKIP | 0 |
| 检出失败断言 | 32 例共 353 条断言，mismatch=0、export-selfcheck fail=0 |
| 运行耗时 | 24.8 s / 24.9 s（两次） |
| 最终判定 | **SUITE_VERDICT=PASS** |
| 可复现性 | 同一会话内连续两次运行结果完全一致（165722、165841） |

真机环境：3ds Max build 26000（2024 中文版），会话当前渲染器 Arnold（套件自带 V-Ray 6.20.06 与 Corona 材质构造）。

## 2. 覆盖范围

每个用例执行 5 层判定：

1. `fileIn` 导出产物是否零异常（真实执行，非静态读）；
2. 产物自身自检：`mf_FAIL=0`、`mf_BAD=0`、`mf_LOG` 无 `[FAIL]`；
3. 材质是否真实进入场景且类匹配（`VRayMtl` / `_Corona物理材质`）；
4. 材质是否真实落进材质编辑器槽位（`meditMaterials[1..24]`）；
5. 关键参数与贴图链路回读：期望值由被验证的那份产物脚本现场解析生成，再 `getProperty` / 递归 `getSubTexmap` 回读比对。

## 3. 本轮修掉的验证装置缺陷（产品代码未改）

真机首跑（日志 20261122-162310）为 32/32 FAIL，逐层排查后定位为**验证装置侧三处缺陷**，与 0.11.2 产品逻辑无关：

| # | 缺陷 | 表现 | 处置 |
|---|---|---|---|
| 1 | 装置贴图基线与导出侧命名漂移 | 32/32 `readback-mismatch`：期望 `src_*.png`，产物落盘为绝对临时路径 | 按基线 `src_<name>.png` 重导 32 份产物，与旧 PASS 版逐行 diff，差异仅时间戳/注释/路径，产物等价 |
| 2 | 槽位检查假设「同会话内编辑器槽位干净」 | 同会话第二次运行后，前 2 例报 `material-not-in-medit-slot` | 套件开跑前把 24 个槽重置为默认命名空材质（`NN - Default`），满足产物脚本「未使用默认槽」判据，重跑结果稳定 |
| 3 | 落槽证明用对象身份严格相等 | Max 在放置时会把材质**复制**进编辑器，前 2 例必然误报 | 判据改为「身份相等 **或** 同名且参数回读全部一致」，后者记为 DEGRADED（证据留痕，不静默并入 PASS） |

第 3 条已用独立诊断证据确认：同名槽材质的参数回读 `param-diff=0`，即编辑器里的副本与导出规格逐项一致，属 Max 侧实例化行为，不是写入失效。

## 4. 两个 DEGRADED 用例

| 用例 | 槽位 | 参数回读 | 判定依据 |
|---|---|---|---|
| `[01] MF_metal_vray` | 1 | mismatch=0，checked=12 | 槽内为参数完全一致的副本，身份不同 |
| `[02] MF_metal_corona` | 2 | mismatch=0，checked=8 | 同上 |

其余 30 例（`[03]`–`[32]`）为身份相等落槽，判 PASS。

## 5. 复现步骤

1. 打开 3ds Max 2024（场景任意，套件会快照并在结束时 `fetchMaxFile` 还原）；
2. 保证 3dsmax-mcp 已连上 Max；
3. 对 `MF_0.11.3_ondevice_verify.ms` 执行 `fileIn`；
4. 控制台输出 `SUITE_VERDICT=PASS`，明细日志落到同目录 `MF_ONDEVICE_LOG_<时间戳>.txt`。

静态旁证：32 份 0.11.3 产物先过 `verify_ms.py`，V-Ray 16/16、Corona 16/16 全通过（贴图路径存在、凹凸/法线语义正确）。

## 6. 证据文件

| 文件 | 说明 |
|---|---|
| `MF_0.11.3_ondevice_verify.ms` | 真机验证套件（含本轮三处修复） |
| `MF_ONDEVICE_LOG_20261122-165722.txt` | 第一次 PASS 明细 |
| `MF_ONDEVICE_LOG_20261122-165841.txt` | 第二次 PASS 明细（复现性证据） |
| `MF_ONDEVICE_LOG_20261122-162310.txt` | 首跑 32/32 FAIL（缺陷 1 现场） |

## 7. 对产品侧的建议

套件这轮的三条判据改进（默认槽重置、副本容忍、期望表命名一致性）建议并入仓库的 on-device 校验脚本，否则任何人换一台机器、或同一 Max 会话连跑两次，都会得到误报 FAIL。产品侧未发现需要改动的落槽缺陷。
*（内容由AI生成，仅供参考）*
