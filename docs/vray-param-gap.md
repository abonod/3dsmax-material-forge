# 参数覆盖实况表（自动生成，禁止手改）

> 本文件由 `scripts/gen_param_gap.py` 从 `core/params.py` 生成，**不要手工编辑**。
> 改了 `params.py` 之后跑一次 `python scripts/gen_param_gap.py` 重新生成；
> `python scripts/gen_param_gap.py --check` 会在文档漂移时返回非零退出码。
>
> 旧版本文档是手写快照（写于 15 参数时代），久未更新，反而把已实现的 SSS / 雾化 /
> 折射光泽度误标成"缺失"。这类"文档比代码旧"的漂移是本次改为生成物的直接原因。

## 总览

| 项 | 数值 |
| --- | --- |
| 参数总数 | 62 |
| 分组数 | 6 |
| [OK] | 49 |
| [WARN] | 7 |
| [LOSS] | 5 |
| [NONE] | 1 |

## 基础（9 项）

| 参数 | 控件 | V-Ray 落点 | Corona 落点 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `base_color` 基础颜色 | color | — | — | [OK] | 经 albedo 贴图通道下发（不写标量颜色属性） |
| `metal_mode` 金属模式 | bool | brdf_useRoughness, diffuse, metalness | diffuse, metalness | [WARN] | Corona 只能二值化 metalnessMode(0/1)，中间值需靠金属度贴图 |
| `metal_tint` 金属反射染色 | color | reflectionColor | diffuse, metalness | [OK] | — |
| `rough_level` 粗糙度档位 | enum | roughness | roughness | [OK] | — |
| `reflection_on` 开启反射 | bool | reflectionColor, reflection_maxDepth | 无落点 | [WARN] | Corona 忽略 reflection_maxDepth（无对应属性，不影响主效果） |
| `fresnel_ior` 菲涅尔 IOR | range | brdf_ior | brdf_ior | [OK] | — |
| `tint_mode` 染色模式 | enum | — | — | [OK] | Python 侧把色偏烘进 albedo，不下发标量属性 |
| `tint_strength` 染色强度 | range | — | — | [OK] | 同上，色偏强度直接改像素 |
| `tint_chroma` 保留原图色偏 | range | — | — | [OK] | 同上，保留原图色偏比例 |

## UV（2 项）

| 参数 | 控件 | V-Ray 落点 | Corona 落点 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `uv_tiling` UV 平铺 | range | __uv_tiling__ | — | [OK] | 写贴图 UV 控制键 __uv_tiling__，由 exporter 落到每个贴图 |
| `uv_rotation` UV 旋转 | range | __uv_rotation__ | — | [OK] | 写贴图 UV 控制键 __uv_rotation__ |

## 细节（9 项）

| 参数 | 控件 | V-Ray 落点 | Corona 落点 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `normal_on` 法线贴图 | bool | bump_multiplier, texmap_bump | bump_multiplier, texmap_bump | [OK] | 走法线槽；与 bump_on 同时开启时由 VRayNormalMap / Corona法线 节点承载两层输入 |
| `normal_strength` 法线强度 | range | bump_multiplier | bump_multiplier | [OK] | 叠加时=V-Ray 材质 bump_multiplier（整体凹凸量）/ Corona法线.multiplier；单独法线时同为材质级 bump_multiplier |
| `bump_on` 凹凸贴图 | bool | bump_multiplier, texmap_bump | texmap_bump | [OK] | 走凹凸槽；与 normal_on 同时开启时作为内层附加凹凸叠加，不抢占法线槽 |
| `bump_strength` 凹凸强度 | range | bump_multiplier | texmap_bump | [OK] | 叠加时=V-Ray VRayNormalMap.bump_multiplier / Corona 法线.additionalBumpStrength（并强制 additionalBumpOn=true）；单独凹凸时=材质级 bump_multiplier |
| `nb_overlay` 法线+凹凸叠加 | bool | — | bump_multiplier, texmap_bump | [OK] | 开启后法线与凹凸各占一层：外层材质 bump_multiplier 管整体，内层节点管凹凸倍率，互不覆盖 |
| `displacement_on` 置换 | bool | displacement_amount, texmap_displacement | texmap_displacement | [OK] | — |
| `disp_amount` 置换强度 | range | displacement_amount | texmap_displacement | [OK] | — |
| `disp_subdiv` 置换细分 | range | __disp_subdiv | texmap_displacement | [OK] | — |
| `ao_on` AO 环境遮蔽 | bool | __activate_ao | — | [NONE] | 不改动标量属性（仅贴图槽 / 前端预览） |

## 表面（27 项）

| 参数 | 控件 | V-Ray 落点 | Corona 落点 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `coat_on` 清漆层 | bool | coat_amount, coat_glossiness, coat_ior | coat_amount, coat_ior, coat_roughness | [OK] | — |
| `emis_on` 自发光 | bool | selfIllumination | selfIllumination, selfIllumination_multiplier | [OK] | — |
| `refraction_on` 折射/透明 | bool | refractionColor, refraction_ior, refraction_maxDepth | refraction_amount | [OK] | — |
| `anisotropy_on` 各向异性 | bool | anisotropy, anisotropy_axis | anisotropy, anisotropy_axis | [OK] | — |
| `anisotropy_amount` 各向异性强度 | range | anisotropy | anisotropy, anisotropy_axis | [OK] | — |
| `anisotropy_axis` 拉丝方向 | range | anisotropy_axis | anisotropy, anisotropy_axis | [OK] | — |
| `transparency_on` 透明度 | bool | refractionColor, refraction_maxDepth | diffuse, refraction_amount | [OK] | — |
| `transparency_amount` 透明度强度 | range | refractionColor | diffuse, refraction_amount | [OK] | — |
| `reflect_strength` 反射强度 | range | reflectionColor | 无落点 | [WARN] | Corona 侧反射强度真实属性名未真机确认，当前不下发（待 probe 取证） |
| `gloss_mode` 独立反射光泽度 | bool | — | roughness | [OK] | — |
| `reflection_glossiness` 反射光泽度 | range | reflection_glossiness | roughness | [OK] | — |
| `coat_roughness` 清漆粗糙度 | range | coat_glossiness | coat_amount, coat_ior, coat_roughness | [OK] | — |
| `sheen_tier` 涂层光泽分层 | enum | — | roughness | [OK] | — |
| `refraction_glossiness` 折射光泽度 | range | refraction_glossiness | refraction_amount | [OK] | — |
| `emis_color` 自发光颜色 | color | selfIllumination | selfIllumination, selfIllumination_multiplier | [OK] | — |
| `emis_strength` 自发光强度 | range | selfIllumination_multiplier | selfIllumination, selfIllumination_multiplier | [OK] | — |
| `opacity_on` 透明度贴图 | bool | texmap_opacity | texmap_opacity | [OK] | — |
| `opacity_cutoff` 镂空阈值 | range | — | texmap_opacity | [OK] | — |
| `thin_wall_on` 薄壁玻璃 | bool | doubleSided | 无落点 | [WARN] | Corona 忽略 doubleSided（无对应属性，不影响主效果） |
| `sheen_layer_on` 绒面 Sheen 层 | bool | sheenColor, sheenGlossiness, sheen_amount | sheenColor, sheen_amount | [WARN] | Corona 走 sheen_amount，强度档位决定数量，无独立开关 |
| `thin_film_on` 薄膜干涉 | bool | thinFilmIOR, thinFilmThickness, thinFilm_on | 无落点 | [LOSS] | CoronaPhysicalMtl 无任何 thinFilm* 属性，属渲染器缺口 |
| `thin_film_nm` 薄膜厚度（nm） | range | thinFilmThickness | 无落点 | [LOSS] | CoronaPhysicalMtl 无任何 thinFilm* 属性，属渲染器缺口 |
| `dispersion_on` 色散开关 | bool | — | dispersion, refraction_amount | [OK] | — |
| `dispersion` 色散强度 | range | dispersion | refraction_amount | [OK] | — |
| `energy_preserve` 能量守恒 + 修复暗边 | bool | energyPreservation, fixDarkEdges | 无落点 | [WARN] | Corona 忽略 energyPreservation（无对应属性，不影响主效果） |
| `reflection_depth` 反射深度 | int | reflection_maxDepth | 无落点 | [WARN] | Corona 忽略 reflection_maxDepth（无对应属性，不影响主效果） |
| `refraction_depth` 折射深度 | int | refraction_maxDepth | refraction_amount | [OK] | — |

## 进阶（6 项）

| 参数 | 控件 | V-Ray 落点 | Corona 落点 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `fog_on` 雾化（体积感） | bool | fogColor, fog_multiplier | 无落点 | [LOSS] | Corona 无对应能力，已归入丢弃清单：fogColor, fog_multiplier |
| `fog_color` 雾化颜色 | color | fogColor | 无落点 | [LOSS] | Corona 无对应能力，已归入丢弃清单：fogColor, fog_multiplier |
| `fog_amount` 雾化浓度 | range | fog_multiplier | 无落点 | [LOSS] | Corona 无对应能力，已归入丢弃清单：fogColor, fog_multiplier |
| `sss_on` 次表面散射 SSS | bool | translucency | sssAmount, sssRadius | [OK] | — |
| `sss_amount` SSS 强度 | range | sssAmount | sssAmount, sssRadius | [OK] | — |
| `sss_radius` SSS 半径 | range | sssRadius | sssAmount, sssRadius | [OK] | — |

## 质感（9 项）

| 参数 | 控件 | V-Ray 落点 | Corona 落点 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `proc_on` 程序化微结构 | bool | — | — | [OK] | 程序化纹理在 Python 侧生成位图后走贴图槽 |
| `proc_type` 纹理类型 | enum | — | — | [OK] | 同上 |
| `proc_scale` 纹理尺寸 | range | — | — | [OK] | 同上 |
| `proc_amount` 起伏强度 | range | — | — | [OK] | 同上 |
| `proc_rough` 高光破碎 | range | — | — | [OK] | 同上 |
| `proc_albedo` 色斑起伏 | range | — | — | [OK] | 同上 |
| `proc_mask` 区域遮罩 | range | — | — | [OK] | 同上 |
| `proc_mask_scale` 遮罩尺度（米） | range | — | — | [OK] | 同上 |
| `proc_seed` 随机种子 | int | — | — | [OK] | 同上 |

## 真实缺口（[LOSS] / [WARN] 项）

[LOSS] 项不是实现缺陷，而是目标渲染器没有该能力（例如 Corona 全系无薄膜层）。
把它们列出来是为了让使用者在选渲染器前就知道取舍，而不是导出后对着
"参数明明调了却没变化"发火。

[WARN] 项是能力存在但表达力受限（金属度二值化、绒层无独立开关），
调不到中间态，需要用贴图通道补。

## 维护约定

1. 新增/修改参数只改 `core/params.py`，然后跑 `python scripts/gen_param_gap.py` 重新生成本表。
2. `scripts/run_all_checks.py` 内置 `param_gap` 一项，用 `--check` 模式校验本表未漂移。
3. 本文件出现在 skill 包内，属于交付文档；手改会在下次打包前被 `--check` 打回。
