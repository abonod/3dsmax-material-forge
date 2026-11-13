# V-Ray 材质参数 vs 现有参数 —— 待补充清单

对照对象：3ds Max 中 **VRayMtl**（V-Ray 6 Update 2，兼容 V-Ray 5）全部可用参数。
现有实现：`core/params.py` 参数注册表，共 15 个参数（3 组：基础 / 细节 / 表面）。

图例：✅ 已覆盖　⚠️ 已覆盖但写死不可调　❌ 缺失

---

## 一、现状盘点

| V-Ray 分区 | VRayMtl 属性 | 现有参数 | 状态 | 说明 |
|---|---|---|---|---|
| Diffuse | `diffuse` + 贴图 | `base_color`（新增）+ `texmap_diffuse` | ✅ | 色板染色烘进 albedo，Max 里无额外节点 |
| Diffuse | `roughness` | `rough_level` + `texmap_roughness` | ✅ | 四档 + 贴图 |
| Diffuse | `metalness` | `metal_mode` | ✅ | 开关式 0/1 |
| Reflection | `reflectionColor` | `reflection_on` | ⚠️ | 固定纯白，无法做弱反射石材 |
| Reflection | `reflectionGlossiness` | 无（靠 roughness 间接） | ⚠️ | 石材/地砖需要独立光泽度 |
| Reflection | `reflection_maxDepth` | `reflection_on` | ✅ | 固定 5 |
| Reflection | `brdf_ior` | `fresnel_ior` | ✅ | 1.0~3.0 |
| Reflection | `brdf_useRoughness` | `metal_mode` | ⚠️ | 仅金属模式置 1 |
| Reflection | `anisotropy` / `anisotropy_axis` | `anisotropy_on` | ⚠️ | 固定 0.7 / 0°，无数值滑杆 |
| Refraction | `refractionColor` / `refraction_ior` / `refraction_maxDepth` | `refraction_on`、`transparency_on/amount` | ✅ | 折射与透光衰减两条路径 |
| Refraction | `refractionGlossiness` | 无 | ❌ | 磨砂/喷砂玻璃做不出来 |
| Refraction | `fogColor` / `fogMultiplier` | 无 | ❌ | 大理石深层乳白、玉石厚度感缺失 |
| Translucency | SSS（`translucency`/`sssAmount`/`radius`） | 无 | ❌ | 薄木皮、玉石、透光石失效 |
| Coat | `coat_amount` / `coat_ior` | `coat_on` | ⚠️ | 固定 1.0 / 1.5 |
| Coat | `coat_glossiness` | `coat_on` | ⚠️ | 固定 0.9，哑光清漆地板与镜面大理石无法区分 |
| Coat | `coat_bump` | 无 | ❌ | 清漆层微观起伏（橘皮漆） |
| Sheen | `sheen_amount` / `sheenColor` | 无 | ✅ | 布料绒感 |
| Thin film | `thinFilmThickness` / `thinFilmIOR` | 无 | ✅ | 镀膜玻璃、油膜彩虹 |
| Bump | `texmap_bump` / `bump_multiplier` | `normal_on` / `bump_on` / 两个强度 | ⚠️ | 单槽互斥：法线 **或** 凹凸，不能叠加 |
| Displacement | `texmap_displacement` / `displacement_amount` | `displacement_on` | ⚠️ | 固定 0.1，无细分/偏移 |
| Self Illum | `selfIllumination` + multiplier | `emis_on` | ⚠️ | 固定纯白，无强度、无颜色 |
| Opacity | `opacity` map / cutout | 无 | ❌ | 镂空砖、格栅、树叶做不了 |
| Options | `doubleSided` / 薄壁玻璃 | 无 | ❌ | 单面玻璃、薄片材质 |
| Options | `energyPreservation` / `fixDarkEdges` | 无 | ✅ | 石材/玻璃边缘发黑无法修 |
| 位图 | Bitmap `coordinates`（U/V Tiling、Offset、Angle） | 无 | ❌ | **地面材质必需**：砖缝尺度、木纹走向 |

---

## 二、建议补的参数（按优先级）

### P1 —— 建议本轮就加（地面三材质直接受影响，改动小）

| # | 新参数 | 建议 id / 类型 | 写入的 VRayMtl 属性 | 为什么需要 |
|---|---|---|---|---|
| 1 | UV 平铺次数 | `uv_tiling` / float 0.1~20 | Bitmap `coordinates.U_Tiling` / `V_Tiling` | 地砖要 4×4、木地板要 2×8，没有它砖缝比例永远不对 |
| 2 | UV 旋转角度 | `uv_rotation` / range 0~180 | Bitmap `coordinates.U_Angle` 等 | 木纹走向、斜铺地砖 |
| 3 | 反射光泽度 | `reflection_glossiness` / range 0~1 | `reflectionGlossiness`（并置 `brdf_useRoughness=0`） | 抛光大理石 0.95 / 地砖 0.6 / 木地板 0.45，纯靠 roughness 无法表达 |
| 4 | 反射强度 | `reflection_strength` / range 0~1 | `reflectionColor` 灰度 | 石材反射比金属弱，现在一律纯白 |
| 5 | 清漆粗糙度 | `coat_roughness` / range 0~1 | `coat_glossiness` = 1 - 值 | 木地板哑光清漆、大理石晶面差别靠它 |
| 6 | 凹凸/法线叠加 | 改现有 `bump_on` 逻辑 | 用 `VRayNormalMap` 包裹法线再进 bump 槽 | 木纹既要花纹起伏又要细纹，现在二选一 |

### P2 —— 值得加（提升物理真实度，中等改动）

| # | 新参数 | 建议 id / 类型 | 属性 | 用途 |
|---|---|---|---|---|
| 7 | 雾化颜色/浓度 | `fog_on`、`fog_multiplier` | `fogColor` / `fogMultiplier` | 大理石厚度感、玉石、蜡烛、树脂 |
| 8 | 半透明 SSS | `sss_on`、`sss_amount`、`sss_radius` | `translucency` / `sssAmount` / `sssRadius` | 透光石、薄木皮、纸灯 |
| 9 | 折射光泽度 | `refraction_glossiness` | `refractionGlossiness` | 磨砂玻璃、喷砂石材 |
| 10 | 自发光颜色/强度 | `emis_color`、`emis_strength` | `selfIllumination` + multiplier | 灯箱、背光石 |
| 11 | 透明度贴图 | `opacity_on` | `opacity` 槽 | 镂空砖、格栅、铁丝网 |
| 12 | 双面/薄壁 | `thin_wall_on` | `doubleSided` / `thinWall` | 玻璃、单面片 |
| 13 | 置换强度/细分 | `disp_amount`、`disp_subdiv` | `displacement_amount`、modifier 细分 | 石材真实凹凸 |

### P3 —— 锦上添花（专业向，按需）

| # | 新参数 | 属性 | 用途 |
|---|---|---|---|
| 14 | 各向异性强度/角度 | `anisotropy`（-1~1）、`anisotropy_axis` | 拉丝金属、木纹顺光 |
| 15 | Sheen | `sheen_amount` / `sheenColor` | 绒布、地毯 |
| 16 | 薄膜 | `thin_film_thickness` / `thin_film_ior` | 镀膜玻璃、油膜 |
| 17 | 色散 | `dispersion` / `abbe` | 水晶、棱镜 |
| 18 | 能量守恒 / 边缘修复 | `energyPreservation` / `fixDarkEdges` | 玻璃边缘发黑 |
| 19 | 反射/折射深度 | `reflection_maxDepth`、`refraction_maxDepth` | 大场景性能 |

---

### P3 收口（v0.8.0 已实现）

P3 表里 15~19 五项已全部落地，命名与落地方式：

| # | 原计划 | 实际参数 | 备注 |
|---|--------|----------|------|
| 15 | Sheen | `sheen_layer_on` | 一个开关写 `sheen_amount`/`sheenColor`/`sheenGlossiness`，避免拆四个控件 |
| 16 | 薄膜 | `thin_film_on` + `thin_film_nm` | IOR 随开关一起写（1.45），只把厚度做成可调 |
| 17 | 色散 | `dispersion` | `refraction_on` 才生效，Abbe 反向用数值表达（0=无色散，越大彩边越强） |
| 18 | 能量守恒 | `energy_preserve` | 同时写 `energyPreservation` + `fixDarkEdges` |
| 19 | 深度 | `reflection_depth` / `refraction_depth` | 分别依赖反射/折射开关，默认 5 与原固定值一致，不改变老预设观感 |

> 预设观感零漂移的保障：默认值一律取 V-Ray 原生默认，新参数只在用户主动开启时才写入非默认值，因此 16 个预设的渲染结果与 v0.7.2 完全一致（除乳胶漆/两种铝新增的染色与光泽档位外，这两处是刻意的观感修正）。

## 三、确认结论

### 3.1 三套预设默认值 —— 已确认（2026-11-11 定稿，作为基线冻结）

| 预设 | 粗糙档 | IOR | 清漆 | 法线强度 | 备注 |
|---|---|---|---|---|---|
| 木地板 | 半哑光 0.55 | 1.50 | 开（0.9） | 1.10 | 板缝靠 AO，纹理靠法线 |
| 地砖 | 亮面 0.28 | 1.55 | 关 | 0.90 | 亮面砖体，建议后续接 UV 平铺 |
| 大理石 | 亮面 0.28 | 1.60 | 开 | 0.45 | 石纹走 albedo，表面要极平 |

三套预设完整字段（与 `PRESETS` 已一致，无需再改）：

| 字段 | 木地板 | 地砖 | 大理石 |
|---|---|---|---|
| `metal_mode` | false | false | false |
| `rough_level` | semi (0.55) | glossy (0.28) | glossy (0.28) |
| `reflection_on` | true | true | true |
| `fresnel_ior` | 1.50 | 1.55 | 1.60 |
| `coat_on` | **true** | **false** | **true** |
| `normal_on` / `normal_strength` | true / 1.10 | true / 0.90 | true / 0.45 |
| `bump_on` | false | false | false |
| `ao_on` | true | true | true |
| `refraction_on` / `transparency_on` | false / false | false / false | false / false |
| `anisotropy_on` / `emis_on` | false / false | false / false | false / false |
| `base_color` | 跟随色板 | 跟随色板 | 跟随色板 |

> 语义提醒：三套预设都走 `brdf_useRoughness = 1`，VRayMtl 会以 roughness 反算光泽度，
> 即 0.28 粗糙 ≈ 0.72 光泽、0.55 粗糙 ≈ 0.45 光泽。清单 P1 第 3 项（独立 `reflectionGlossiness`）
> 就是为了在同 roughness 下单独拉高/拉低反射锐度。

### 3.2 范围裁决 —— 已确认（2026-11-11）

1. **P1 六项全加**（含第 6 项凹凸/法线叠加，修原互斥逻辑）。
2. **P2 七项一并排期并实现**。
3. **UV 平铺做预览可视化**（three.js 贴图 repeat/rotation 实时跟随）。

## 四、落地状态（2026-11-11 完成）

`PARAMS` 由 15 项扩到 **38 项**，全部经后端 + 前端双链路实测。（v0.6.0 起为 **40 项**：追加各向异性强度 `anisotropy_amount` 与拉丝方向 `anisotropy_axis`，本文其余数字均为当时的基线记录。）

| 项 | 实现 id | 落点 | 验证 |
|---|---|---|---|
| UV 平铺 | `uv_tiling`(0.1~20) | Bitmap `U_Tiling`/`V_Tiling`；预览 `map.repeat` | 平铺 1→6 预览边缘密度 +233% |
| UV 旋转 | `uv_rotation`(0~180°) | `W_Angle`；预览 `map.rotation` | 旋转 0→45° 梯度直方图距离 68.9 |
| 反射光泽度 | `reflect_strength` + `gloss_mode` + `reflection_glossiness` | `reflectionColor` 灰度、`reflectionGlossiness` | `.ms` 内 `reflectionColor (color 204 204 204)` |
| 清漆粗糙度 | `coat_roughness` | `coat_glossiness = 1 - 值` | `.ms` 内 `coat_glossiness` 命中 |
| 凹凸+法线叠加 | `nb_overlay` | 法线走 `VRayNormalMap` 包裹后进 bump 槽 | `.ms` 内两者同存 |
| 雾化 | `fog_on`/`fog_color`/`fog_amount` | `fogColor` 三元组、`fog_multiplier`；预览 `attenuationColor/Distance` + 透射吸收 | 浓度 0.1→1.0 直方图距离 137.6；雾色 白→#3FB0C8 蓝红比 +2.6% |
| SSS | `sss_on`/`sss_amount`/`sss_radius` | `translucency=3`、`sssAmount`、`sssRadius`；预览 thickness+transmission | 开→关 直方图距离 8.3 |
| 折射光泽度 | `refraction_glossiness` | `refractionGlossiness`；预览 roughness 抬升 | 1.0→0.2 直方图距离 33.0 |
| 自发光 | `emis_on`/`emis_color`/`emis_strength` | `selfIllumination` (color) + multiplier | `.ms` 内 `selfIllumination (color 255 204 68)` |
| 透明度贴图 | `opacity_on`/`opacity_cutoff` | `opacity` 槽 + alphaMap | 贴图键含 opacity |
| 薄壁双面 | `thin_wall_on` | `doubleSided` 反置 | `.ms` 内 `doubleSided` 命中 |
| 置换 | `displacement_on`/`disp_amount`/`disp_subdiv` | `VRayDisplacementMod` + 高度图 | `.ms` `MF_DISP_AMT=35.00`、`MF_DISP_SUB=24` |

单位约定：`disp_amount` 1.0 = 100 个场景单位（mm 场景下约 10cm）。

## 五、真机环境差异记录（v0.8.1，2026-11-13）

以下四条是中文版 3ds Max 2024 + V-Ray 6.20.06 真机跑 16 套预设时踩出来的，写进文档避免下轮重踩。

### 5.1 类名本地化：不能用英文类名硬造节点

| 节点 | 英文写法 | 实测可用写法 | 说明 |
|---|---|---|---|
| 法线贴图 | `VRayNormalMap()` | 注册名 `VRay_法线贴图` | `VRayNormalMap` 类在中文版恒为 `undefined` |
| 污垢 AO | `VRayDirt()` / `VRay_Dirt()` | `VRay_污垢` | 前两种均返回 `undefined`，必须按候选链逐级探测 |

落点：`ms_exporter.py` 的 `mfDirtClass()` 按 `VRayDirt → VRay_Dirt → VRay_污垢` 顺序探测，命中即用，三态失败才报 FAIL。
`VRayMtl` 本身不本地化，可直接 `VRayMtl()`。

### 5.2 渲染器类名形如 `V_Ray_6__update_2_1`

`findString (toLower n) "vray"` 匹配不到（真实串是 `v_ray_6__update_2_1`）。判定逻辑改为 `vray / v_ray / ray` 三者任一命中即视为 V-Ray，避免误报 WARN。

### 5.3 材质库接口在 Max 2024 已移除

探针实测（`D:\mfv081\matlib_probe.txt`）：

| 写法 | 结果 |
|---|---|
| `newMaterialLibrary "x"` | `undefined` |
| `newMaterialLibrary()` | `undefined` |
| `saveMaterialLibrary lib` | `UndefinedClass` |
| `loadTempMaterialLibrary "x.mat"` | `UndefinedClass` |
| `saveTempMaterialLibrary lib path` | 需 `MaterialLibrary` 对象，传数组报「操作需要材质库，得到的是 #(...)」 |

结论：v0.8.0 的 16/16 全 FAIL 不是写法 bug，是**接口层面缺失**。v0.8.1 改为能力探测式降级——`loadMaterialLibrary` 拿不到库对象就输出 `[SKIP]`，不再计 FAIL；材质始终留在材质编辑器里，用户可手动另存 `.mat`。

### 5.4 依赖开关必须紧跟主参数

V-Ray 6 里 dispersion / SSS / thinFilm 的数值属性在没有对应 `*_on` 开关时写进去不生效（表现为"参数写了但渲染没变化"）。三组开关已补齐：

| 参数 | 依赖开关 |
|---|---|
| `dispersion` | `refraction_dispersion_on` |
| `sss` | `translucency_on` |
| `thinFilm` | `thinFilm_on`（厚度须 min/max 成对写） |

---

*生成时间：2026-11-11　对照版本：VRayMtl（V-Ray 6）/ 现有 params.py 38 参数*
