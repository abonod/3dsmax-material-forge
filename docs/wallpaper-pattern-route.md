---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 89f2995f43cde2f2717e466bdd92de27_2185eb8ede7611f1a0ca525400de85a5
    ReservedCode1: DnsEBVHL3/xd20TtXb+hkeWWGORoq4fnAuiChxck7MA2wZBKvQoj9ffLHdPsCVbWwSxZ6leZ4QonUYvnnkSixqSCn0SkntM/WDH/RlOSEEffu/a/BdDHWd03E2gooWe2admh35MpxzakAtQitfPKIRfSm3iEkPob7ByA2mTUeBuYVlqvd6yiejQmR5E=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 89f2995f43cde2f2717e466bdd92de27_2185eb8ede7611f1a0ca525400de85a5
    ReservedCode2: DnsEBVHL3/xd20TtXb+hkeWWGORoq4fnAuiChxck7MA2wZBKvQoj9ffLHdPsCVbWwSxZ6leZ4QonUYvnnkSixqSCn0SkntM/WDH/RlOSEEffu/a/BdDHWd03E2gooWe2admh35MpxzakAtQitfPKIRfSm3iEkPob7ByA2mTUeBuYVlqvd6yiejQmR5E=
---

# 壁纸花纹：技术路线决策记录（ADR-001）

> 状态：已决定（v0.6.0）
> 决策人：用户 + 本工具维护方
> 影响范围：壁纸(`wallpaper`)、墙布(`wallcloth`)预设；`core/texture_engine.py` 职责边界；`docs/` 与 `evals`

## 1. 问题是什么

壁纸材质的辨识度，九成来自**花纹图案**（花卉、几何、条纹、浮雕压花），只有一成来自纸感（哑光、微微的吸收、压纹起伏）。

当前工具 `core/texture_engine.py` 是**派生式**引擎——它从用户给的参考图里算出五通道：

| 函数 | 产出 |
| --- | --- |
| `derive_albedo` | 去反光的 albedo |
| `derive_height` | 灰度高度 |
| `derive_normal` | 由高度求法线 |
| `derive_roughness` | 结合亮度与高度求粗糙度 |
| `derive_ao` | 由高度求 AO |
| `tint_albedo` | 整体改色 |

也就是说：**给它一张有花纹的图，它能派生出这个花纹的全套 PBR 通道；给它一张白纸，它只能给你一张白纸。** 它没有任何"从零画出一朵花"的能力，一行图案合成代码都没有。

所以问题不是"参数少一个"，而是"花纹图案这个信息源从哪来"。

## 2. 四条候选路线

| 路线 | 做法 | 优点 | 致命问题 |
| --- | --- | --- | --- |
| **A. 程序化节点生成** | 在 `.ms` 里用 V-Ray 程序纹理/OSL 拼花纹（Voronoi、条纹、噪波叠加） | 零贴图体积、可参数化 | ① 现有引擎全无图案合成能力，等于重写；② OSL 在中文版 V-Ray / Max 各版本的注册与编译行为不稳定，正是本项目最忌讳的"跑起来才发现报错"；③ 程序化花卉/大马士革纹在近景一定露馅，只能做几何与条纹；④ `.ms` 体积与调试成本暴涨 |
| **B. 内置无缝花纹模板库** | skill 自带 N 张可平铺花纹 PNG，UI 里选一张挂 diffuse | 开箱即用、效果好、不依赖用户素材 | ① 包体变大（每张 512² ≤ 200KB，12 张约 2MB）；② 版权必须干净（只能自绘/CC0），不能拿品牌样册图；③ 仍解决不了"客户指定那款花纹" |
| **C. 用户供图 / 样册派生（现状能力）** | 用户给花纹图（样册照、厂商 HD 贴图、设计稿），走 `build_texture_set` 派生五通道 | 与现有引擎零冲突、可复现客户指定的那一款、无版权风险 | 花纹质量取决于用户素材；不可平铺的图会露接缝——**但这是素材问题，不是工具问题** |
| **D. C + B 混合** | 默认 C，另附可选模板库做"占位预览" | 兼顾 | 实现成本 = B |

## 3. 决定

**主线走 C；B 列为 P2 可选增强，暂不排期；A 明确否决。**

理由按权重排：

1. **能力边界**：C 用的是引擎已有的五个 `derive_*` 函数，改动量约等于零；A 需要新写图案合成器并承担 Max 端 OSL 兼容风险，投入产出比最差。
2. **真实性**：壁纸是"设计品"，花纹就是客户要的那一款。程序化生成再漂亮也是"类似的东西"，场景里就变成返工。
3. **可交付性**：`uv_tiling=1.0` 已经在 `wallpaper` 预设里锁死（整卷不重复平铺），花纹图直接 1:1 上墙，物面 UV 与卷材实际比例一致——这是壁纸最要紧的一条，B/C 都天然满足，A 还要额外处理。
4. **包体与版权**：B 的模板库每加一张都要审版权、涨体积，而它解决的问题（"我手上没图"）可以用一句话指引用户找一张样册图替代。

## 4. 职责边界（写清楚，避免以后再吵）

工具负责：

- 把花纹图挂到 diffuse（`vray_map` 槽位），并保持 `uv_tiling = 1.0`；
- 从花纹图派生 height / normal（**花纹的压花起伏**就在这里出来，`normal_strength` 1.30 是壁纸的默认落点）；
- 派生 roughness / ao，去反光（`derive_albedo` 的 decontaminate，避免样册照里的高光污染颜色）；
- 支持 `tint_albedo` 整体改色（客户要"同款花纹换个色"）；
- 交付 `.ms` + 同级 `textures/`，并在说明里讲清花纹来自用户图。

工具不负责：

- 不生成花纹图案本身（无合成器，也不打算加）；
- 不保证用户图可无缝平铺——不可平铺的图在 `uv_tiling>1` 时会露接缝，`wallpaper` 预设给 `1.0` 正是为了回避这个问题；
- 不内置品牌样册素材。

## 5. 交付时怎么对用户说

- 用户给了花纹图 → 正常派生，交付后说明：花纹来自你的图，压花起伏由它派生。
- 用户没图、只说"要个碎花壁纸" → 不要假装能生成。明确告知：工具不造花纹，需要一张花纹图（样册照即可，正对拍、光均匀、别带反光）；同时可以先给纸感参数（哑光、反射 0.16、法线 1.30）让他先搭场景。

## 6. 什么情况下改主意

出现以下任一信号，重开这条 ADR：

- 用户反复要求"直接给我生成花纹"且频次超过每 5 次壁纸需求 1 次；
- 出现确定的 CC0 无缝花纹素材源且体积可控（≤1MB/10 张）；
- V-Ray 6 侧的 OSL 在目标用户机器上跑通且稳定（需真机证据，不接受"应该可以"）。

## 7. 与验收的关系

壁纸/墙布两条 `evals` 的通过标准不含"花纹长得对"——只含：预设命中、`uv_tiling=1`、通道挂接正确、`.ms` 静态体检通过、以及交付说明里讲清花纹来源。视觉上像不像，用 `scripts/reports/baseline/壁纸.png`、`墙布.png` 做基线对比，而不是凭印象。
*（内容由AI生成，仅供参考）*
