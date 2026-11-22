# 参数注册表扩展指南

`core/params.py` 的 `PARAMS` 列表是单一事实来源（SSOT）。改参数只改这里，前端面板、three.js 预览、MaxScript 导出三条链自动跟随。

## 分组与三档暴露

`group` 只允许落在六个已有分组里，顺序即面板顺序：

| 分组 | 参数数 | 面向谁 |
|------|--------|--------|
| 基础 | 9 | 新手（base_color / metal_mode / rough_level / normal_strength / uv_tiling）+ 进阶 |
| UV | 2 | 新手（平铺）/ 进阶（旋转、偏移） |
| 细节 | 10 | 进阶 |
| 质感 | 9 | 进阶（程序化麻面与区域遮罩） |
| 表面 | 29 | 进阶 |
| 进阶 | 6 | 进阶 |

合计 62 项。v0.8.0 新增 9 项（v0.9.2 起表面组再拆出 `dispersion_on`，见下）：

| id | 类型 | 作用 |
|----|------|------|
| `sheen_tier` | enum off/matte/eggshell/satin | 涂层光泽分层，一档同时写 `reflectionColor` / `reflection_glossiness` / `brdf_useRoughness`（`vray_map_multi`） |
| `metal_tint` | color | 金属反射染色，与 `reflect_strength` 相乘后合成 `reflectionColor`，不会顶掉强度层级 |
| `sheen_layer_on` | bool | V-Ray Sheen 层开关，布料绒感；开启时一次写 `sheen_amount`/`sheenColor`/`sheenGlossiness` |
| `thin_film_on` | bool | 薄膜干涉开关；开启时写 `thinFilm_on`/`thinFilmThickness`/`thinFilmIOR` |
| `thin_film_nm` | range | 薄膜厚度（nm），依赖 `thin_film_on`，肥皂泡 300~450 / 阳极氧化铝 200~600 |
| `dispersion_on` | bool | 色散开关，关掉时不下发任何色散属性（保留渲染器默认 Corona 40 / V-Ray 50）；开启才写强度并使能 |
| `dispersion` | range | 色散强度 0~100，玻璃边缘彩边，依赖 `refraction_on` + `dispersion_on`；两端量纲一致（probeF/probeG 实测无钳制） |
| `energy_preserve` | bool | 一次写 `energyPreservation` + `fixDarkEdges`，压掉过亮和暗边 |
| `reflection_depth` / `refraction_depth` | int | 反射/折射深度，多层玻璃与镜面互反射 |

面板有三档（v0.8.2 起），默认新手档：

- **新手档**：只渲染 `BEGINNER_IDS` 里的 5 项（`base_color / metal_mode / rough_level / normal_strength / uv_tiling`），其余 57 项折进「中级 + 进阶参数」`<details>` 折叠区。
- **中级档**：渲染 `BEGINNER_IDS` + `INTERMEDIATE_IDS` = 30 项（日常够用 + 需要时微调），剩下的 32 项进阶参数折进「进阶参数」折叠区。
- **进阶档**：62 项全平铺，无折叠区。

三档划分由后端 `tiers` 字段下发（`beginner` / `intermediate` / `advanced`），前端只负责铺开当前档集合、折叠其余，不再自己猜哪些该露。三档互斥且并集等于全部 61 项，`tier_ids()` 负责切分。

`BEGINNER_IDS` 是**独立白名单，不等于「基础」分组**：基础组里的 `reflection_on`、`fresnel_ior` 被换成了 `normal_strength`、`uv_tiling`，因为后者对新手第一眼的观感影响更大。加参数时若想让新手看见，必须同时把它加进 `BEGINNER_IDS`，否则只出现在折叠区。

## 预设 = 62 项完整快照

`PRESETS` / `PRESET_LABELS` / `PRESET_EXTRAS` 三张表同键同序，定义 **33 个预设**，`preset_snapshots()` 把每个预设展开成**全部 62 项的完整快照**。

预设按两轴组织：第一轴是族群 `PRESET_FAMILIES`（7 族 + 前端「全部」chip），第二轴是族内预设按钮，顺序即前端按钮顺序。

| 族群 | 预设 |
| --- | --- |
| 金属 | 金属、黄铜、镜面、拉丝不锈钢、铝合金(磨砂)、铝合金(拉丝)、阳极氧化铝、镂空铁艺/纱帘 |
| 石材陶瓷 | 水泥、文化石/砌砖墙、瓷砖、地砖、大理石、微水泥、玉石/灯罩布 |
| 木作 | 木纹、木地板、未上漆原木 |
| 织物皮革 | 布料、皮革、丝绒(天鹅绒)、麂皮、地毯 |
| 玻璃亚克力 | 玻璃、磨砂玻璃、有色玻璃、亚克力透光板、水 |
| 涂料墙面 | 乳胶漆、壁纸、墙布 |
| 特殊材质 | 塑料、发光灯箱 |

`family_issues()` 自检族群表：无重复归族、无幽灵键、无漏归类、无缺中文名。

源数据分两层：`PRESETS` 存该材质**与默认值不同**的稀疏差异，`PRESET_EXTRAS` 存额外补充项，展开时按 `default_values()` → `PRESETS` → `PRESET_EXTRAS` 三层覆盖。

扩成完整快照的原因写在 `preset_snapshots()` 的 docstring 里：过去靠「默认值打底 + 预设覆盖」，隐含假设默认值恰好符合该材质；默认值一调或新增参数，预设就静默变质，而且用户手动开过的开关（SSS / 雾化）会残留到下一个预设上。

套用预设走的是整体替换，不是"改几个默认值"。这有两个后果：

1. 切预设不会残留上一个材质的开关——比如从「玻璃」切到「木纹」，`refraction` 相关项会被明确写回默认值，而不是留着上一次的开启状态。
2. 用户手动改过的参数会被预设覆盖掉，UI 上要给出可撤销路径（当前靠用户自己记住，改预设一次即全改）。

**预设键与中文标签不是同一套字符串**，别靠标签猜 key：木纹是 `wood`（IOR 1.55），木地板是 `wood_floor`（IOR 1.5）。UI 自动化里必须按中文标签反查 key。

## Param 字段

| 字段 | 含义 |
|------|------|
| `id` | 唯一标识，也是 `/api/export` 提交 dict 的 key |
| `label` | 面板中文名 |
| `group` | 分组（基础 / 细节 / 表面 / …），决定面板分区 |
| `ptype` | `bool` / `enum` / `range` / `float` / `int` / `color` |
| `default` | 默认值 |
| `options` | `enum` 专用：`[{value, label, ...}]`，可携带业务数值 |
| `min` / `max` / `step` / `unit` | `range`/`float` 的滑块约束与单位 |
| `help` | 面板提示文字，写成"为什么这么调"而不是"这是什么" |
| `vray_on` / `vray_off` | 勾选 / 取消时写入的 MAXScript 属性 dict |
| `vray_prop` | 数值参数直接对应的属性名 |
| `vray_map` | 枚举值 → 属性值 |
| `three` | WebGL 预览映射 |
| `depends` | 条件显示：`{"param_id": value}` 成立时才显示本项 |

## 三种典型映射写法

**开关型**（勾选开、取消关，两边都要写，否则取消勾选后属性残留）：

```python
Param(
    id="coat_on", label="清漆层", group="表面", ptype="bool", default=False,
    vray_on={"coat_amount": 1.0, "coat_ior": 1.5, "coat_glossiness": 0.9},
    three={"clearcoat": 1.0},
)
```

**枚举型**（档位 → 数值，`vray_prop` 指定目标属性，`vray_map` 给映射）：

```python
Param(
    id="rough_level", label="粗糙度档位", group="基础", ptype="enum", default="semi",
    options=[{"value": "matte", "label": "哑光", "roughness": 0.85}, ...],
    vray_prop="roughness",
    vray_map={"matte": 0.85, "semi": 0.55, "glossy": 0.28, "mirror": 0.05},
)
```

**连续型**（滑块直连属性）：

```python
Param(
    id="fresnel_ior", label="菲涅尔 IOR", group="基础", ptype="range",
    default=1.55, min=1.0, max=3.0, step=0.01,
    vray_prop="brdf_ior",
    three={"ior": "value"},
)
```

## 各向异性三件套（拉丝金属）

| 参数 | 类型 | 写入 V-Ray | 写入 three.js |
|------|------|-----------|---------------|
| `anisotropy_on` | bool | 开：`anisotropy=0.7` + `anisotropy_axis=0.0`；关：`anisotropy=0.0` | `anisotropy=0.7` |
| `anisotropy_amount` | range 0~1 | `vray_prop="anisotropy"` 直连 | `anisotropy: "value"` |
| `anisotropy_axis` | range 0~1 | `vray_prop="anisotropy_axis"` 直连 | `anisotropyRotation: "value"`（×π 转弧度） |

两个子项都带 `enable_by="anisotropy_on"`，关闭各向异性时面板置灰——否则新手在关着的状态下猛调方向，看不到任何变化。

**方向的单位是坑**：`anisotropy_axis` 在 VRayMtl 里是 0~1 归一化值，对应 0~180°（既不是弧度也不是角度），所以参数域直接取 0~1、不做换乘；只有 three.js 侧需要 ×π 换成弧度。改这里别顺手"统一成角度"，会让方向翻倍。

`alu_brushed` 预设同时给了 `anisotropy_amount=0.72` 与 `anisotropy_axis=0.0`（沿 U 横向拉丝，配 `uv_tiling=2.0`）；要竖纹把 `axis` 改 1.0。

## 特殊标记

`vray_on` 里的值可以是字符串标记而非真实属性，由导出器解释：

- `__HEIGHT__` / `__NORMALWRAP__` / `__NORMALBUMP__` —— 法线/高度系语义槽，共占 `bump`：`__HEIGHT__` 直挂高度图；`__NORMALWRAP__` 只包法线（bump 子槽留空）；`__NORMALBUMP__` 法线进 normal 子槽、高度进 bump 子槽。
- `__NORMAL__` —— 遗留标记，`resolve_vray()` 在仅法线场景直接输出 `__NORMALWRAP__`，导出器遇到旧数据里的 `__NORMAL__` 也统一改写为 `__NORMALWRAP__`。
- `__DIFFUSE__` / `__ROUGHNESS__` / `__DISPLACE__` / `__OPACITY__` —— 常规语义贴图槽，导出器按 `TEX_SLOTS` 找到实际槽位后挂对应 bitmap。
- `__AO__` —— 指向 diffuse 槽，但实际走污垢纹理包漫反射位图的非破坏性叠加。
- `__activate_ao` —— 触发污垢纹理的 AO 叠加动作。

新增这类标记要同步改 `ms_exporter.py` 的贴图块处理逻辑，否则标记会被当成普通字符串值写进属性。

## three 映射的三种取值

| 写法 | 含义 |
|------|------|
| `{"metalness": 1.0}` | 字面量，直接写进预览材质 |
| `{"brdf_ior": "value"}` / `{"ior": "value"}` | 占位：用本参数当前值 |
| `{"roughness": "option.roughness"}` | 取 `options` 里当前选中项的某个字段 |

## `vray_as`：标量值的目标形态转换

`range` 参数直接绑 `vray_prop` 时，值会原样写入属性。需要变形时用 `vray_as`：

| `vray_as` | 输入 | 输出 | 典型用途 |
|---|---|---|---|
| `color` | 0~1 标量 | 灰度 `(x, x, x)` | 反射强度 `reflectionColor`、透明度 `refractionColor` |
| `gloss` | 粗糙度 0~1 | `1 - 值`（光泽度） | 清漆粗糙度 `coat_roughness` → `coat_glossiness` |
| `hex` | `"#rrggbb"` | `(r, g, b)` 0~1 三元组 | 颜色型属性：`selfIllumination`、`fogColor` |

颜色型参数（`ptype="color"`）必须配 `vray_as="hex"`，否则字符串会被当成普通值写进 MAXScript 而报错。

## 双槽叠加（凹凸 + 法线）

`normal_on` 与 `bump_on` 原本互斥。`nb_overlay` 勾选后，法线贴图不再抢占 bump 槽，而是先用 `VRayNormalMap` 包一层再并进 bump 链，两者同存。导出器里由 `__NORMALWRAP__` / `__NORMALBUMP__` 标记决定挂接形态：只有法线时 bump 子槽留空，法线+高度时两个子槽各就各位。节点类名走本地化解析（中文版 V-Ray 的注册名是 `VRay_法线贴图`），不要写死 `VRayNormalMap`。

## depends 条件显示

`depends={"normal_on": True}` 表示只有法线开关打开时，这个滑块才出现在面板上。用它避免一堆不可用控件糊在用户脸上。

注意 `depends` 只管显示，不管导出——导出端仍会收到该参数的值，需要导出器自己判断前置开关是否成立。

## 新增一个参数的检查清单

1. 在 `PARAMS` 追加一条 `Param`，`id` 唯一、`group` 必须是已有五个分组之一。
2. 决定它归谁看：要出现在新手视图就加进 `BEGINNER_IDS`，否则只落在「进阶」折叠区。
3. 若目标是新属性名：在 `PROP_ALIASES` 加关键词组，**并且在模板 `mfAssign` 的 `case pname of` 加同一分支**。
4. 若某个预设该给它一个非默认值，在 `preset_snapshots()` 的预设表里补——**漏了就会在套预设时被清成默认值**，这是最容易出的静默 bug。
5. 给 `three` 映射，否则预览不跟着变（用户会以为参数无效）。
6. 若需要条件显示，填 `depends`。
7. 起服务，`GET /api/schema` 确认新参数出现。
8. 批量导出全部预设的 `.ms`（V-Ray / Corona 各 33 个，共 66 个），再跑 `python scripts/verify_ms.py <输出目录>` 做静态体检（它会遍历目录下所有 `.ms`，应 66/66 通过）。它**不会自己生成脚本**，必须先导出。
9. 改过前端就跑 `python scripts/ui_check.py`（33 项断言，含新手 5 项 / 中级 30 项 / 进阶 62 项 / 33 预设逐个快照回填 / 族群 chip 过滤与回全 / 窄屏 1000px 布局 / 预览球出帧）。
10. 需要端到端确认时，先 `POST /api/preflight` 看 `blocking` 是否为 0，再让用户 Run Script 回贴 Listener。

## 接新渲染器

设计上留了端口：一个渲染器 = 一个映射字段 + 一个 exporter。新增 Arnold / Corona 时，加 `arnold_on` / `arnold_map` 之类的字段并写新的 exporter，不动已有参数定义和 `app.js`。不要为了接新渲染器去改 `Param` 的既有语义。

## 全部 62 项清单（按档位标注）

由 `core/params.py` 的 `PARAMS` 直接生成，档位来自 `BEGINNER_IDS`(5) / `INTERMEDIATE_IDS`(25) / 其余(32)。改参数后必须同步本表，`scripts/registry_check.py` 会核对行数与分组数。

| 分组 | id | 类型 | 默认值 | 档位 |
|------|----|------|--------|------|
| 基础 | `base_color` | color | #ffffff | 新手 |
| 基础 | `metal_mode` | bool | false | 新手 |
| 基础 | `metal_tint` | color | #ffffff | 中级 |
| 基础 | `rough_level` | enum | semi | 新手 |
| 基础 | `reflection_on` | bool | true | 中级 |
| 基础 | `fresnel_ior` | range | 1.55 | 中级 |
| 基础 | `tint_mode` | enum | match | 中级 |
| 基础 | `tint_strength` | range | 1 | 中级 |
| 基础 | `tint_chroma` | range | 0.35 | 进阶 |
| UV | `uv_tiling` | range | 1 | 新手 |
| UV | `uv_rotation` | range | 0 | 中级 |
| 细节 | `normal_on` | bool | true | 中级 |
| 细节 | `normal_strength` | range | 1 | 新手 |
| 细节 | `bump_on` | bool | false | 中级 |
| 细节 | `bump_strength` | range | 0.6 | 中级 |
| 细节 | `metalness_tex_on` | bool | false | 中级 |
| 细节 | `nb_overlay` | bool | true | 中级 |
| 细节 | `displacement_on` | bool | false | 进阶 |
| 细节 | `disp_amount` | range | 0.1 | 进阶 |
| 细节 | `disp_subdiv` | range | 16 | 进阶 |
| 细节 | `ao_on` | bool | true | 中级 |
| 质感 | `proc_on` | bool | false | 中级 |
| 质感 | `proc_type` | enum | roller | 中级 |
| 质感 | `proc_scale` | range | 1 | 进阶 |
| 质感 | `proc_amount` | range | 0.6 | 中级 |
| 质感 | `proc_rough` | range | 0.55 | 进阶 |
| 质感 | `proc_albedo` | range | 0.04 | 进阶 |
| 质感 | `proc_mask` | range | 0.6 | 进阶 |
| 质感 | `proc_mask_scale` | range | 1 | 进阶 |
| 质感 | `proc_seed` | int | 7 | 进阶 |
| 表面 | `coat_on` | bool | false | 中级 |
| 表面 | `emis_on` | bool | false | 进阶 |
| 表面 | `emissive_tex_on` | bool | false | 进阶 |
| 表面 | `refraction_on` | bool | false | 中级 |
| 表面 | `anisotropy_on` | bool | false | 中级 |
| 表面 | `anisotropy_amount` | range | 0.7 | 中级 |
| 表面 | `anisotropy_axis` | range | 0 | 中级 |
| 表面 | `transparency_on` | bool | false | 中级 |
| 表面 | `transparency_amount` | range | 0.5 | 进阶 |
| 表面 | `reflect_strength` | range | 1 | 中级 |
| 表面 | `gloss_mode` | bool | false | 中级 |
| 表面 | `reflection_glossiness` | range | 0.72 | 中级 |
| 表面 | `coat_roughness` | range | 0.1 | 中级 |
| 表面 | `sheen_tier` | enum | off | 中级 |
| 表面 | `refraction_glossiness` | range | 1 | 进阶 |
| 表面 | `emis_color` | color | #ffffff | 进阶 |
| 表面 | `emis_strength` | range | 1 | 进阶 |
| 表面 | `opacity_on` | bool | false | 进阶 |
| 表面 | `opacity_cutoff` | range | 0.5 | 进阶 |
| 表面 | `thin_wall_on` | bool | false | 进阶 |
| 表面 | `sheen_layer_on` | bool | false | 进阶 |
| 表面 | `sheen_tex_on` | bool | false | 进阶 |
| 表面 | `thin_film_on` | bool | false | 进阶 |
| 表面 | `thin_film_nm` | range | 350 | 进阶 |
| 表面 | `dispersion_on` | bool | false | 进阶 |
| 表面 | `dispersion` | range | 5 | 进阶 |
| 表面 | `energy_preserve` | bool | false | 进阶 |
| 表面 | `reflection_depth` | int | 5 | 进阶 |
| 表面 | `refraction_depth` | int | 5 | 进阶 |
| 进阶 | `fog_on` | bool | false | 进阶 |
| 进阶 | `fog_color` | color | #ffffff | 进阶 |
| 进阶 | `fog_amount` | range | 0.2 | 进阶 |
| 进阶 | `sss_on` | bool | false | 进阶 |
| 进阶 | `sss_amount` | range | 0.5 | 进阶 |
| 进阶 | `sss_radius` | range | 1 | 进阶 |

合计 62 项：新手 5 / 中级 25 / 进阶 32。
