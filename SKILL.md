---
name: 3dsmax-material-forge
version: 0.8.4
description: "把参考图推导为 PBR 参数并生成可在 3ds Max 执行的 V-Ray 材质脚本（.ms，含 VRayMtl 属性别名降级、贴图挂接、.mat 落库）。触发场景：用户要求「照这张图做个材质」「这面墙要这种质感」「帮我转成 PBR」「调 VRayMtl 的反射/粗糙度/清漆/凹凸/置换」「批量导出材质脚本」「做个壁纸/墙布/乳胶漆/铝合金(磨砂·拉丝)材质」「拉丝金属的各向异性方向怎么调」，或已有 .ms 在 Max 里执行报错、参数没生效需要按属性名失配排查；也用于扩展该工具（加参数、加属性别名、接 Arnold/Corona）。不用于建模几何体、最终渲染出图、GPU 生成本地贴图。"
metadata:
  AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 89f2995f43cde2f2717e466bdd92de27_aa45efeadda811f1bff2525400cd780f
    ReservedCode1: QiDg2Z04OkXU3VSDXuLfC/8eCo+eqPib2vrWy0Oc49Jc/Dla0tyNk85r4WuKCgjMUSi2FSCVz1hmoM49XukxN0FNBlGnBCGoGvHvOdEOohxIs3iBu3mAURcmMsriDnr22prY/jQfjwp5Y/e0MJYAvzdIEMy4llWhe9gJ1Vm9q0ZDydqV4oZV3NHY26s=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 89f2995f43cde2f2717e466bdd92de27_aa45efeadda811f1bff2525400cd780f
    ReservedCode2: QiDg2Z04OkXU3VSDXuLfC/8eCo+eqPib2vrWy0Oc49Jc/Dla0tyNk85r4WuKCgjMUSi2FSCVz1hmoM49XukxN0FNBlGnBCGoGvHvOdEOohxIs3iBu3mAURcmMsriDnr22prY/jQfjwp5Y/e0MJYAvzdIEMy4llWhe9gJ1Vm9q0ZDydqV4oZV3NHY26s=
---



# 3ds Max Material Forge

这个 skill 回答的是一个很具体的痛点：手上有张参考图，想要一个"看起来就是那个材质"的 V-Ray 材质，但不想在 3ds Max 的材质编辑器里凭感觉拧几十个参数。

它把这件事拆成三段可以各自验证的动作：**图像推导 → 参数组织 → 脚本生成**。前两段完全离线，第三段产出纯文本 `.ms`，真正的材质实例由 3ds Max 自己在执行脚本时创建。

## 什么时候用

- 用户丢来一张图，说"照这个做个材质""这面墙我要这种质感""帮我转成 PBR"。
- 用户要调 VRayMtl 的反射、粗糙度、清漆、凹凸、置换，但表述是业务语言而不是属性名。
- 用户要一次做好几个材质，想批量出 `.ms` 而不是一个个手点。
- 用户已有 `.ms` 脚本在 Max 里执行报错或参数没生效，需要按属性名失配的思路排查。
- 用户要扩展这套工具：加参数、加属性别名、接新渲染器（Arnold / Corona）。

## 什么时候不用

- 用户要的是**建模**（生成几何体），不是材质 —— 这属于 Blender / Max 建模范畴。
- 用户要的是 V-Ray **最终渲染出图**，本工具只到材质与预览球近似效果为止。
- 用户要用 GPU 跑 Stable Diffusion 生成本地贴图 —— 本项目按机器能力设计为本地算法或在线 API，不假装有 CUDA。

## 硬约束：运行期不碰 3ds Max 进程

工具的**运行期**（服务、导出、预检）不 `subprocess` 拉起 `3dsmax.exe`、`3dsmaxcmd.exe` 或 `3dsmaxbatch.exe`。用户跑面板时不需要开着 Max。

但**开发期**允许用无头实例做语法复现，实测可行：

```powershell
& "D:\Autodesk\3ds Max 2024\3dsmax.exe" -silent -U MAXScript driver.ms
```

`driver.ms` 里用 `try(fileIn @"<待测>.ms")catch(format "%" (getCurrentException()))` 把结果写文件，异常信息带行号。注意两点：`-silent` 仍会加载完整 UI 框架，一次要等 1~3 分钟，别当热重载用；`loadScript` 在 Max 2024 是 `UndefinedClass`，必须用 `fileIn`。这条通道只用于「静态体检说不清、又确实要确认能否编译执行」时，日常交付仍以静态体检为准。

`.mat` 材质库是 Autodesk 私有二进制格式，只能由 3ds Max 自己写；`.ms` 内已内置 `saveTempMaterialLibrary`，执行时把材质同时落进 `.mat`，所以不需要为「本工具能产出 `.mat`」额外折腾。

## 三条执行路径

先判断用户处在哪条路径，再动手。

### 路径 A：交互式调材质（用户要看预览、要反复改）

```powershell
& "D:\Marvis skill\3dsmax-material-forge\启动.bat"
```

服务默认监听 `http://127.0.0.1:8765`（可用 `MF_PORT` 环境变量改端口）。

#### 新手三步（面板默认就是这条路）

给完全不懂 V-Ray 的人看的操作，只有三步：

1. **拖图** —— 把参考图拖进左栏，五通道贴图自动推导出来（albedo / height / normal / roughness / ao）。
2. **挑个预设 + 改两个值** —— 默认「新手档」只露 5 项：基础色、金属模式、粗糙档位、法线强度、UV 平铺。挑一个最像的预设（16 个：金属/塑料/水泥/木纹/布料/皮革/瓷砖/玻璃/木地板/地砖/大理石/壁纸/墙布/乳胶漆/铝合金(磨砂)/铝合金(拉丝)），然后只用改「基础色」和「粗糙档位」。想细调就展开底部的「进阶」折叠区，那里是其余 35 项，可以完全不管。
3. **点「导出前自检」再导出** —— 自检会把「贴图缺失 / 脚本语法 / 编码 / 非法材质名」一次查完，出绿色「自检通过，可以导出」再点导出；红色就是有阻断项，报告卡上会写是哪一项、怎么改。

导出得到两个东西：`.ms` 脚本 + `textures/` 文件夹。两者是**绑定**的——`.ms` 用相对路径找贴图，所以整个 `export/` 目录连同 `textures/` 一起搬走，别只拿 `.ms`。

**在 Max 里怎么用**：`Scripting > Run Script` 选那个 `.ms`。不用先建材质球——脚本会把材质自动放进材质编辑器 1 号位，并且**如果运行前选中了物体，会直接赋给选中物体**。想要更省事：先选中物体再 Run Script，一步到位。

路径成交互式时，不要替用户跑完全程然后一句"已完成"——用户需要自己在面板里试参数，那是这个工具的核心价值。

### 路径 B：无头批量出材质（用户不要界面，只要产物）

直接打 HTTP 接口。四步，每步都可单独重试：

| 步骤 | 接口 | 关键返回 |
|------|------|----------|
| 上图 | `POST /api/upload`（multipart，字段 `file`） | `job_id`、`textures` 五通道路径 |
| 重算贴图 | `POST /api/derive` | 更新后的 `textures` |
| 导出 | `POST /api/export`（带参数 dict） | `ms_path`、`script_url`、`texture_dir`、`products` |
| 可选 AI 增强 | `POST /api/ai_enhance` | `available`、`provider` |
| 导出前自检 | `POST /api/preflight`（带参数 dict） | `ok`、`blocking`、`warnings`、`items[]`、`env` |

另有 `GET /api/schema` 拿全部参数定义与 AI provider 列表、`GET /api/health` 探活、`POST /api/open_dir` 打开输出目录、`GET /files/{job_id}/{path}` 取静态文件。环境信息（Max 版本 / V-Ray 路径）只随 `POST /api/preflight` 的 `env` 字段返回，没有单独的环境接口。

批量场景下先 `GET /api/schema` 读参数注册表，再按 `Param.id` 组 dict 提交，比猜字段名可靠得多。**批量一定要先打 `POST /api/preflight`**：脚本层错误（括号、`#` 字面量、GBK 不可编码）在批量里会被放大成整批失败，且启用了无 V-Ray 的机器还会顺便拿到降级提示。

### 路径 C：改代码 / 排错 / 扩展

先看代码，不要先改代码。单一事实来源在 `core/params.py`：一个参数只声明一次，前端面板、WebGL 预览、MaxScript 映射三条链自动跟随。新增参数不要动 `app.js` 和 exporter。

细节见下节索引。

## 目录与职责

```
3dsmax-material-forge/
├── SKILL.md                     本文件（Agent 入口）
├── README.md                    面向人的项目说明
├── app.py                       FastAPI 服务（上传/推导/导出/预检，端口 8765）
├── core/
│   ├── params.py                参数注册表 PARAMS —— 唯一事实来源（61 项 / 6 分组）
│   ├── version.py               版本号 SSOT（当前 0.8.3）+ 各版本变更摘要
│   ├── preflight.py             导出前自检：环境探测 + 模板结构/取值域校验 + 综合报告
│   ├── export_guard.py          导出后自检：贴图存在性 + .ms 引用断链 + 通道完整性
│   ├── texture_engine.py        PBR 五通道推导（PIL + numpy，实测写出 albedo/normal/height/roughness/ao[/opacity]）
│   ├── ai_enhance.py            BaseProvider 可插拔在线增强，默认回落本地
│   └── exporters/ms_exporter.py PROP_ALIASES / TEX_SLOTS / MaxScript 模板
├── web/                         前端（three.js 材质球预览，新手/中级/进阶三档）
├── scripts/
│   ├── verify_ms.py             .ms 静态体检
│   ├── regen_legacy.py          历史产物批量重导出 / 坏写法修复（默认 dry-run）
│   ├── ui_check.py              界面可验证性检查（独立端口 + Edge CDP，26 项断言）
│   ├── registry_check.py        参数注册表守卫：文档 ↔ 代码的 61 项与档位一致性
│   ├── export_guard_check.py    导出行为断言 33 项：材质隔离 / 自检 / 上传边界 / TTL
│   ├── run_all_checks.py        统一检查入口（6 项串跑，--fast 跳过 Edge 截图）
│   ├── capture_presets.py       真机 GUI 出图基线（逐预设截预览球 → reports/baseline/）
│   ├── package_skill.py         一键体检 + 打包 + 版本号同步 + dist 瘦身 → ../dist/*.skill
│   └── reports/                 上述脚本的 JSON 报告与截图 / baseline 基线图
├── references/                  按需加载的深入文档
├── docs/                        决策记录与参数缺口分析（wallpaper-pattern-route.md 等）
├── run_all_checks.ps1          统一检查的 PowerShell 包装（探测解释器，转调 run_all_checks.py）
├── evals/                       evals.json 回归用例（17 条）+ runner.py 机器评分器（答案/产物双断言）
├── probe/                       历史探测脚本，不参与运行
└── output/<job_id>/             source.png + textures/（中间过程）+ export/<材质名>/（.ms + textures/，一材质一目录）
```

## 参数体系：61 项怎么组织的

`core/params.py` 是单一事实来源，一条 `Param` 同时驱动面板、WebGL 预览、MaxScript 映射三条链。

| 分组 | 数量 | 什么时候看 |
|------|------|------------|
| 基础 | 9 | 基础色、金属模式、粗糙档位、反射开关、菲涅尔 IOR 等 |
| UV | 2 | 贴图旋转、偏移 |
| 细节 | 9 | 凹凸、置换、AO 叠加、污渍 |
| 表面 | 26 | 反射/清漆/各向异性/SSS 等表面光学 |
| 质感 | 9 | 程序化微结构（纯色模式用）：麻面尺度、起伏强度、云状不匀、**低频区域遮罩强度/尺度**、染色模式等 |
| 进阶 | 6 | 雾色、BRDF 模式、渲染器相关 |

三档暴露（v0.8.2 起）：新手 5 项（`BEGINNER_IDS`）、中级 30 项（新手 + `INTERMEDIATE_IDS` 25 项）、进阶 61 项全平铺。新手可见的 5 项**不是**简单取「基础」组：`base_color / metal_mode / rough_level / normal_strength / uv_tiling`（把基础组里的反射开关、菲涅尔 IOR 换成了法线强度和 UV 平铺，因为这两项对新手观感影响最大）。三档划分由后端 `tiers` 下发、互斥且并集等于 61 项；预设不是"改几个默认值"，而是 `preset_snapshots()` 展开出的**61 项完整快照**，套用即整体替换——所以切预设不会残留上一个材质的开关。

加参数只改 `params.py`，加完跑一遍 `python scripts/run_all_checks.py`（或 `--fast` 跳过两个 Edge 截图检查）即可，不要手改 `app.js`。

## 交付前必须做的一步：静态体检

因为运行期不跑 Max，`.ms` 出门前必须过两道闸，顺序不能反：

**第一道：面板自检（用户视角，也是脚本被拦截的原因）**

面板上的「导出前自检」调 `POST /api/preflight`，`core/preflight.py` 一次返回环境 + 脚本两类结论：

| 层级 | 检查内容 |
|------|----------|
| error（阻断导出） | 贴图文件缺失、`#` 字面量写法错误、括号不平衡、`catch()` 空捕获、非法材质名、非 GBK 可编码字符 |
| warn（仅提示） | V-Ray 未安装（会走降级路径）、参数取默认值、贴图尺寸异常 |
| env | 当前 Max 版本、V-Ray 版本与安装位、当前渲染器 |

导出接口自身也带 dry-run 拦截：有 error 项时直接返回阻断结果，不会写出半成品 `.ms`。

**第二道：脚本静态体检（Agent 视角）**

```powershell
python "D:\Marvis skill\3dsmax-material-forge\scripts\verify_ms.py" "<生成的>.ms"
```

检查括号/引号平衡、关键 API 齐全、贴图路径真实存在、编码可被 Max 读取。退出码非 0 不准交付。

**界面侧再核一遍**（改过前端才需要）：

```powershell
python "D:\Marvis skill\3dsmax-material-forge\scripts\ui_check.py"
```

独立端口起服务 + 独立 profile 拉 headless Edge，走 CDP 真实点击，26 项断言覆盖：新手 5 项 / 中级 30 项 / 进阶 61 项 / 三步引导 / 16 个预设逐个快照回填 / 上传后自检按钮解锁 / 贴图通道生成 / 点自检出报告卡（含 V-Ray 环境行）/ 窄屏 1000px 布局（无横向滚动、中列不被压塌、参数控件不溢出、预设按钮不越界、画布仍有面积）/ 预览球真出帧（帧数递增 + 三角形数 + 网格已挂载，挡住「画布在、什么都没画」）。报告落 `scripts/reports/ui_check.json`，截图四张。

**第三道：一条命令全跑（0.8.3 起）**

```powershell
python "D:\Marvis skill\3dsmax-material-forge\scripts\run_all_checks.py"          # 全量，约 3 分钟
python "D:\Marvis skill\3dsmax-material-forge\scripts\run_all_checks.py" --fast   # 跳过两个 Edge 截图检查，约 15 秒
.\run_all_checks.ps1 -Fast                                                        # PowerShell 包装，参数同上
```

| 检查项 | 脚本 | 抓什么 |
|--------|------|--------|
| evals 断言+产物回归 | `evals/runner.py --artifact` | 17 条用例的答案断言 + 8 条产物断言（含材质隔离/自检/三层参数/打包通道探针） |
| 导出隔离/自检/生命周期 | `scripts/export_guard_check.py` | 33 项行为断言：两材质目录不串、缺图必报、上传 413、TTL 与内存淘汰 |
| 注册表文档一致 | `scripts/registry_check.py` | 文档与代码的 61 项、分档互斥并集 |
| 贴图元信息与遮罩 | `scripts/proc_mask_check.py` | 20 项：preflight 贴图元信息只留字符串、遮罩尺度参数 |
| 主界面结构 | `scripts/ui_check.py` | 26 项 CDP 真实点击断言 |
| 纯色模式像素 | `scripts/ui_solid_check.py` | 18 项纯色模式像素统计断言 |

任一失败即非零退出，失败项会直接贴出尾部输出，不用再单独重跑。

**导出产物结构与自检字段（0.8.3 起）**

```
output/<job_id>/
├── source.png
├── textures/                 中间过程（TTL 到点回收）
└── export/
    └── <材质名>/             ← 一材质一目录，同名贴图不再互相覆盖
        ├── <材质名>.ms
        └── textures/albedo.png · normal.png · height.png · roughness.png · ao.png [· opacity.png]
```

导出返回的 `selfcheck` 字段（`core/export_guard.py`）：

| 字段 | 含义 |
|------|------|
| `ok` | 全部贴图存在且 `.ms` 内引用未断链 |
| `checked` | 校验了几张贴图 |
| `missing[]` | 缺失通道 `{channel, path, reason}` |
| `broken_refs[]` | `.ms` 里指向不存在文件的 `bitmapTexture` |
| `packed.count` / `packed.dir` | 打包张数与目标目录 |

`ok=false` 时界面会在导出结果卡顶部红字标出缺失通道，**不要当成成功交付**。恒生成通道（albedo/roughness）丢失同样会被查出——`expected_channels` 把它们列为基线。


## 壁纸/墙布：花纹从哪来（别答错）

一句话：**工具不生成花纹图案，花纹来自用户的图。**

`core/texture_engine.py` 是派生式引擎（`derive_albedo/height/normal/roughness/ao`），给它花纹图它派生整套 PBR 通道；给它白纸它就只给白纸。程序化生成花纹（OSL/节点拼图）已评估并否决——键入 `.ms` 后在中文版 V-Ray 上的注册行为不稳定，且程序化花卉近景必露馅。

用户没给图、只要"碎花壁纸"时的正确回答：说明工具不造花纹、需要一张花纹图（样册正面拍、光匀、别带反光），同时先把纸感参数报给他（哑光、反射 0.16、法线 1.30、整卷不重复平铺 `uv_tiling=1.0`）。

完整决策依据、四条候选路线对比、改主意的触发条件，见 `docs/wallpaper-pattern-route.md`。

## 乳胶漆：不用法线图也要有粗糙感（纯色模式）

真实墙面的麻面来自滚涂微起伏，跟"图案"是两件事——所以它**可以**程序化生成，不必依赖用户上传的法线图。走这条路就是纯色模式。

**什么时候用**：用户只给一个颜色（色卡号、HEX、或"米白墙"），没有素材图。这时不要让他去网上找法线图，直接走纯色模式。

**怎么触发**：
- 界面：左栏「纯色模式（无图生成）」按钮 `#btnSolid`，命中即调 `POST /api/solid`
- 接口：`{"values": {...}, "detail": 1.0}`，返回 `{job_id, textures, tint, solid}`，贴图在 `output/<job_id>/textures/`，不生成 `source.png`

**它在算什么**：`texture_engine.proc_texture()` 按物理尺度铺一层滚涂麻面（高频细颗粒），再叠一层低频云状不匀（避免纯色墙面像塑料板），最后乘上 2~3 种尺度叠加的低频区域遮罩（`region_mask()`：刷痕/流挂/板缝浓淡/局部脏污），让每段墙的麻面浓淡不同、不会一眼看出复制粘贴，据此派生 height → normal → roughness。纯色模式下 normal 贴图的红通道标准差约 24（纯平面只有 0.3），roughness 有真实离散度，所以光打上去会有麻面起伏，不是一张发光塑料板。

**参数在「质感」组**（9 项）：麻面尺度、起伏强度、颗粒密度、云状不匀强度/尺度、区域遮罩强度/尺度、染色模式、染色强度。默认值已调到乳胶漆观感，别乱改大——起伏强度拉高就成水泥墙了。区域遮罩强度 0 就是完全均匀的麻面（老效果），0.6 是默认，1.0 分区最明显；遮罩尺度是**米制**（1.0 ≈ 1 米区块），>1 区块更大更整，<1 更碎。

**0.7.2 起遮罩不再只属于程序化场**：供图（上传图片）模式与纯色模式都会跑同一套低频遮罩，只压低频明暗、粗糙与找平，原图的高频细节（贴图自身的花纹/砂粒）不会被抹掉。因此 `proc_mask`、`proc_mask_scale`、`proc_seed` 三个滑杆不再挂在 `proc_on` 下，纯色/供图时同样可调。

**遮罩尺度的世界尺度补偿**：导出端 `uv_tiling` 决定一块贴图覆盖多少米（平铺 2 → 一块 0.5 米）。遮罩按贴图空间生成，若区块尺寸（米）大于一块贴图的米数，大块色差会被跟着缩成重复图案。生成时会按 `uv_tiling` 把米换算成贴图内循环数（`MASK_CPT_FLOOR` 保底），放不下就自动削弱强度，并在返回的 `mask` 字段里回显：

| 字段 | 含义 |
|------|------|
| `block_m` / `block_m_actual` | 想要 / 实际得到的区块米数 |
| `tile_m` | 一块贴图覆盖的米数（= 1 / uv_tiling） |
| `cycles_per_tile` | 每块贴图内塞几个遮罩循环 |
| `repeat_risk` | 是否已发生复读（true 时强度被自动下调） |
| `amount_effective` | 实际生效的遮罩强度 |

**滑杆的跟手感（0.7.2）**：`/api/build` 支持 `preview=true` + `preview_size`，拖动时先出 320² 快预览（实测 1024² 全尺寸 4.0s → 快预览 0.54s，约 7 倍），松手后前端自动补一张全尺寸；拖动期间直接点导出会先补算全尺寸再导出，不会把低分辨率预览当成品。前端另有「换一版」按钮，一键随机换 `proc_seed`。

**和 `normal_strength` 的关系**：`normal_strength` 是最终法线贴图的凹凸强度（Max 侧同时映射到 bump），纯色模式下它仍然有效，两者是"生成多少微结构"和"凹凸放多强"的关系，不是替代。

## 漆面光泽分档 / 金属染色 / 六项进阶通道（0.8.0）

**用户说「哑光 / 蛋壳光 / 丝光」时不要让他自己算参数**——这是乳胶漆的实际分档售货口径，工具里就是 `sheen_tier` 一个枚举：

| 档位 | reflectionColor | reflection_glossiness | 对应观感 |
|------|-----------------|----------------------|----------|
| `off` | 不写 | 不写 | 交给精细参数（老行为） |
| `matte` | 0.10 | 0.35 | 哑光 / 平光，约 2~5 度 |
| `eggshell` | 0.22 | 0.52 | 蛋壳光，约 10~20 度 |
| `satin` | 0.34 | 0.68 | 丝光 / 半光，约 25~35 度 |

- **一档多写**：档位同时改反射灰阶与光泽度，并强制 `brdf_useRoughness=0` 走 `reflectionGlossiness` 通道（`vray_map_multi` 机制）。回答时要说清这条，否则用户以为只是换个粗糙度档。
- **优先级**：档位压过 `gloss_mode` / `reflection_glossiness` / `rough_level` 的精细值；`metal_mode=True` 时档位不参与（金属反射不吃漆面光泽），预览与导出同语义。
- 三版对比（哑光/蛋壳/丝光）就走三档各导一份，别让用户手改反射参数。

**金属反射染色 `metal_tint`**：真实金属反射带色偏（球磨铝暖灰、铜偏橙、钛冷灰）。导出时是 `metal_tint × reflect_strength -> reflectionColor`，**不会顶掉强度层级**，也不改漫反射。默认白=不染色。注意金属的倒影颜色在 three 预览里无法真实体现（three 的金属反射色即 base color），只能看导出后的 V-Ray 结果，这点要如实告知，别宣称预览可见。

**六项进阶通道**（都在「表面」组，默认关闭，只在开启时写非默认值，所以 16 个预设观感零漂移）：

| 参数 | 写什么 | 什么时候提 |
|------|--------|-----------|
| `sheen_layer_on` | `sheen_amount`=0.8 / `sheenColor`=白 / `sheenGlossiness`=0.45（整包写） | 布料、地毯、绒面的边缘绒感 |
| `thin_film_on` + `thin_film_nm` | `thinFilm_on` / `thinFilmThickness` / `thinFilmIOR` | 肥皂泡、油膜、阳极氧化铝色斑；厚度决定色相 300~450 |
| `dispersion` | `dispersion` | 玻璃边缘彩边，0 无色散 / 5 普通玻璃 / 15+ 水晶火彩 |
| `energy_preserve` | `energyPreservation` + `fixDarkEdges` | 石材、玻璃在高粗糙下不再发黑、掠射角脏黑边 |
| `reflection_depth` / `refraction_depth` | `reflection_maxDepth` / `refraction_maxDepth` | 两面镜子对打、多层玻璃堆叠才需要提到 8~12，默认 5 与原固定值一致 |

`dispersion`、`reflection_depth`、`refraction_depth`、`thin_film_nm` 都有 `enable_by`，对应母开关关着时**不会**写进 .ms——回答里要避免"改了没反应"的困惑，先确认母开关。

## 色板选色：为什么以前对不上，现在怎么对的

**旧做法的病**：预览把颜色乘在 `material.color` 上（`albedo × 颜色`），导出又把颜色乘进贴图——颜色乘了两遍，且乘法只会变暗不会变亮，所以选了浅色发灰、选了饱和色发闷，跟色卡差得远。

**现在的做法**：烘焙式。改色 → 重烤 albedo（`tint_albedo_ex`）→ 预览球直接用烘焙后的贴图，`material.color` 固定为白，不再二次相乘。

**染色三模式**（参数 `tint_mode`）：

| 模式 | 算法 | 适用 | 实测偏差 |
|------|------|------|----------|
| `match`（默认） | 按平均色做 Lab 校色匹配，把成品均色拉到选色 | 乳胶漆、纯色墙面、涂料 | ΔE ≤ 0.03（肉眼无差） |
| `mono` | 保留原图明暗，只把色相/饱和度换成选色 | 想保留素材纹理明暗层次的布/纸 | ΔE ≤ 0.03 |
| `multiply` | 传统 RGB 乘算（旧行为） | 只在需要"染色"物理直觉时用 | ΔE 6~18（明显偏暗） |

判据来自 `texture_engine.color_delta_e()`，界面右下 `#tintInfo` 会直接显示 `ΔE x.x`，超过 1.0 就说明这次选的色和成品色差得能看出来（通常意味着选了 multiply 或素材本身明暗极端）。

**注意 `base_color` 的语义**：它既是纯色模式的底色，也是染色目标色。上传图 + 白色时走直通（不重烤，零损耗），非白才触发染色。

## 版本与打包

版本号只有一个来源：`core/version.py` 的 `__version__`（当前 `0.8.0`）。SKILL.md frontmatter 的 `version` 由打包脚本自动同步，**不要手改**。

```
python scripts/package_skill.py     # 体检（schema 自检 + 16 预设 MAXScript 静态体检）→ 打包 → 写清单
```

不过体检查就不出包，dist 保持原样。产物：

- `dist/3dsmax-material-forge.skill`（稳定名，双击/安装用这个）
- `dist/3dsmax-material-forge-<版本>.skill`（留档，回溯用）
- `dist/BUILD_INFO.json`（版本、时间、文件数、sha256、参数/预设计数、变更摘要）
- 旧包自动改名为 `*.bak_<时间戳>`

**装错了版本怎么认**：看 `dist/BUILD_INFO.json` 的 `version`，或包内 `SKILL.md` 的 `version:` 行，或 `python -c "from core.version import __version__;print(__version__)"`。低于 0.6.0 的包不含壁纸/墙布/乳胶漆/铝合金预设，也不含各向异性强度与方向两个参数——症状是"预设按钮点了没反应""找不到各向异性滑杆"。低于 0.7.0 的包没有纯色模式（乳胶漆非得配法线图才有粗糙感），也没有色板校色（选色与成品色会差一截）——症状是"找不到纯色模式按钮""选色发灰"。

改版本号按语义化版本：破坏性格式变更改 MAJOR，加预设/加参数/加模式改 MINOR（0.7.0 就是纯色模式 + 程序化微结构 +10 参数），只动文案 UI 改 PATCH。

## 参考文件索引

需要时再读，不要一次全加载：

- `references/ms-exporter-guide.md` —— MaxScript 生成机制：反射式写入、属性别名降级、贴图槽类型校验、AO 叠加、双分支降级。改导出逻辑或排查"参数在 Max 里没生效"时读。
- `references/params-registry.md` —— 参数注册表扩展：怎么加一个参数并让它同时出现在面板、预览和 `.ms` 里；映射字段（`vray_on` / `vray_off` / `vray_map` / `vray_prop` / `three` / `depends`）语义。
- `references/troubleshooting.md` —— 启动失败、端口占用、贴图异常、Max 执行报错、V-Ray 版本差异的排查顺序。
- `docs/wallpaper-pattern-route.md` —— 壁纸花纹技术路线（ADR-001）：为什么不做程序化生成、工具与素材的职责边界、什么情况下改主意。

## 验收标准

一次交付算完成，需要同时满足：

1. `output/<job_id>/` 下 `source.png`、`textures/*.png`（albedo / height / normal / roughness / ao 五张）、`export/*.ms` 都在。
2. 面板预检 `blocking == 0`（导出没被拦截），且 `verify_ms.py` 通过。
3. 明确告诉用户 `.ms` 的绝对路径和它在 Max 里的执行方式（`Scripting > Run Script`，选中物体则自动赋材质）。
4. 提醒 `.ms` 与同级 `textures/` 目录绑定，搬动要整目录一起走。
5. 如果用户问过效果对不对——说明这里的预览只是近似，最终以 V-Ray 渲染为准，并给出在 Listener 里看什么的指引。
6. 动过前端或参数注册表，再附 `ui_check.py` 的 26/26 结论与报告路径；动过视觉表现（新预设、材质观感）再附 `capture_presets.py` 的基线图路径。
7. 壁纸/墙布类交付，必须说明花纹来自用户提供的图（工具不生成花纹），见 `docs/wallpaper-pattern-route.md`。
7.1 纯色模式或色板染色相关改动，再附 `ui_solid_check.py` 的 18/18 结论（纯色免上传出贴图、法线有真实起伏、两次选色画面确有变化、纯色 job 可导出 `.ms`）。
7.2 动过区域遮罩、分级预览或参数解耦，再附 `proc_mask_check.py` 的 20/20 结论；导出链路改动另跑一遍上传→全尺寸重建→导出→`verify_ms.py` 的端到端烟测（`temp/smoke_e2e_export.py`），确认导出拿到的仍是全分辨率基准贴图。
*（内容由AI生成，仅供参考）*
