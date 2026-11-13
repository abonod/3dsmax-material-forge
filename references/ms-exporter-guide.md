---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 89f2995f43cde2f2717e466bdd92de27_ab49ad6adda811f1bff2525400cd780f
    ReservedCode1: GvM9G56mF4ktc//LIHfW6A9CHEk2KFWqUwvaN17uYEN41gP6ZToC0rP9SJgw9y6H5ODpZ+sBBYPl7A7FGU4+jO54YETxKQ0li7Ur1n/q5LoyvxFvlayGVfcom/8oqyUW/CJe0BJ/8MgP86r2ZvSJEzlXost1c6kra+KU4Ea8Evrd7ED6wlDgNvRzsk8=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 89f2995f43cde2f2717e466bdd92de27_ab49ad6adda811f1bff2525400cd780f
    ReservedCode2: GvM9G56mF4ktc//LIHfW6A9CHEk2KFWqUwvaN17uYEN41gP6ZToC0rP9SJgw9y6H5ODpZ+sBBYPl7A7FGU4+jO54YETxKQ0li7Ur1n/q5LoyvxFvlayGVfcom/8oqyUW/CJe0BJ/8MgP86r2ZvSJEzlXost1c6kra+KU4Ea8Evrd7ED6wlDgNvRzsk8=
---

# MaxScript 导出机制深入说明

改 `core/exporters/ms_exporter.py`、或排查"参数在 3ds Max 里没生效"时读这份。

## 为什么脚本要这么写

目标环境是 3ds Max 2024 + V-Ray 6.x。工具的**运行期**不拉起 Max 进程，`.ms` 生成后由用户在 Max 里 `Run Script` 执行。既然脚本落地时没有运行期回环，它就得设计成"猜错也不炸"：属性名失配只记日志不中断，材质类不可用就降级，贴图找不到就回落。

开发期也不能靠无头实例回归——本机 `3dsmax.exe -silent` / `3dsmaxcmd.exe` / `3dsmaxbatch.exe` 都卡在 ADLSDK 许可校验（退出码 -8）。所以质量只能靠两侧静态手段兜：导出前 `preflight` 拦参数与贴图，导出后 `verify_ms.py` 体检语法与编码。

这条约束推导出下面全部设计。

## 编码与换行

中文 Windows 上 MaxScript 按系统 ANSI（GBK）解析脚本文件。带 BOM 的 UTF-8 会让中文材质名、中文路径乱码甚至语法报错。所以：

1. 优先 `encoding="gbk", newline="\r\n"` 写盘。
2. 仅当内容含 GBK 无法表示的字符（`UnicodeEncodeError`）时，回退 UTF-8 **无 BOM**。

文件名会被 `safe` 化：非字母数字且非 `_-` 的字符一律替换为 `_`，空名回落 `material`。所以材质名里的空格、中文标点不会变成非法文件名。

## 关键函数

| 函数 | 职责 |
|------|------|
| `_ms_name(name)` | 材质名 → MaxScript 合法 name：只输出裸 `#标识符`，非法字符走 `(toName "x")` 回落。**绝不输出 `#"字符串"`**——Max 不接受这种写法，会报 `位于 string，需要 "(" or "["` |
| `_ms_escape(path)` | 把 Windows 路径转成 MaxScript 字符串字面量（反斜杠是路径分隔符，不能当转义符处理） |
| `_ms_value(v)` | Python 值 → MaxScript 字面量：bool→`true/false`，tuple→`(r,g,b)` 颜色，str→带引号，数值原样 |
| `build_mscript(...)` | 套用 `_TEMPLATE`，填充材质名、时间戳、属性块、贴图块、AUTOASSIGN 开关 |
| `export(...)` | 建目录、生成脚本、按 GBK/UTF-8 策略落盘，返回 `.ms` 路径 |

## 模板内建的三段自保逻辑

这三段都写在 `_TEMPLATE` 里，改模板时不要删：

**1. 环境自检段（脚本开头）**

执行时先探 `(maxVersion())[1]`、`classof renderers.current`、`VRayMtl` 类是否可用，结果打进 Listener。判断逻辑是 `findString (toLower rendererName) "vray"`——**当前渲染器不是 V-Ray 也照常建 VRayMtl**，只是提示用户切渲染器，不阻断。全部用 `catch` 兜住，探测失败写"未知"，不影响后续。

**2. 贴图相对路径解析 `mfResolveTex`**

贴图路径优先按「脚本同级的 `textures/` 目录」解析，找不到再回落到脚本里写死的绝对路径。这就是「`.ms` 与 `textures/` 必须整目录搬走」的技术原因——搬到新机器上，同级目录能命中，绝对路径必然失效。

**3. 自动赋材质 `MF_AUTOASSIGN`**

`@@AUTOASSIGN@@` 由 Python 侧注入布尔值（默认 `true`）。为 `true` 时：有选中物体 → 直接赋给所有选中物体；无选中物体 → 只提示材质已放入材质编辑器。这是"新手不用懂怎么赋材质"的实现方式，关掉它脚本就只往编辑器放材质。

材质落位之后还会在原点建一个半径 60、64 段的预览球（`<材质名>_Ball`）并赋上材质，方便直接看效果。它**会进入你的场景**，导出成品前记得删。建球失败只记 `[WARN]`，不影响材质。

## 材质落位：24 个槽位的选择策略

材质建好后遍历材质编辑器的 24 个槽位，找一个空槽放进；**没有空槽时覆盖 1 号槽并打 `[WARN]`**。所以重复跑脚本不会堆满编辑器，但会顶掉你手动放在 1 号位的东西——排查"我的材质怎么没了"时先看这条。

## 三级属性写入策略

模板内是逐级降级的，别绕过它直接 `setProperty`：

1. `mfSet obj p v` —— `hasProperty` 精确匹配后 `setProperty`，整个写入包在 `try/catch` 里。成功 `mf_OK += 1`，失败 `mf_FAIL += 1` 并 `mfLog`。
2. `mfAssign obj pname v` —— 精确名不存在时，取 `PROP_ALIASES[pname]` 的关键词组，用 `getPropNames` 遍历属性名做**关键词命中**匹配（`mfHit`），命中即写。
3. 都没命中就只记日志。单个属性失败永远不中断脚本。

所以 V-Ray 6/7/8 之间属性改名不会让脚本失效——只要关键词还落在新名字里。

## 属性别名表（PROP_ALIASES）

每个 key 是一组关键词组，按顺序尝试，全部小写子串匹配：

| 原始属性 | 关键词组 |
|---|---|
| `metalness` | `metalness` |
| `roughness` | `roughness` |
| `brdf_ior` | `fresnel,ior` → `brdf,ior` |
| `brdf_useRoughness` | `use,roughness` |
| `diffuse` | `diffuse,color` |
| `reflectionColor` | `reflection,color` → `reflection` |
| `reflection_maxDepth` | `reflection,depth` |
| `refractionColor` | `refraction,color` |
| `refraction_ior` | `refraction,ior` |
| `refraction_maxDepth` | `refraction,depth` |
| `selfIllumination` | `self,illum` |
| `coat_amount` | `coat,amount` → `coat` |
| `coat_ior` | `coat,ior` |
| `coat_glossiness` | `coat,gloss` |
| `anisotropy` | `anisotropy,amount` → `anisotropy` |
| `anisotropy_axis` | `anisotropy,axis` |
| `bump_multiplier` | `bump,mult` |
| `displacement_amount` | `displacement,amount` |
| `reflection_glossiness` | `reflection,gloss` |
| `refraction_glossiness` | `refraction,gloss` |
| `coat_roughness` | `coat,gloss` |
| `fogColor` | `fog,color` |
| `fog_multiplier` | `fog,mult` |
| `translucency` | `translucency` |
| `sssAmount` | `sss,amount` |
| `sssRadius` | `sss,radius` |
| `selfIllumination_multiplier` | `self,illum,mult` → `self,illum,strength` |
| `doubleSided` | `double,sided` |

共 28 组。注意 `coat_roughness` 与 `coat_glossiness` **指向同一关键词组 `coat,gloss`**——VRayMtl 只有 coat glossiness 一个属性，粗糙度是它的反向语义，谁后写谁生效。同理 `reflection_glossiness` 与 `reflection_maxDepth` 都命中 `reflection` 前缀，所以关键词组必须两个词一起给（`reflection,gloss` / `reflection,depth`），单给 `reflection` 会互相抢。

新增别名要**两处同时改**：Python 侧 `PROP_ALIASES`，模板内 `mfAssign` 的 `case pname of` 分支。只改一处等于没改——这是最常见的"改了没效果"原因。

## 贴图槽识别

`TEX_SLOTS` 把语义通道映射到属性关键词：

| 占位符 | 目标属性 | 说明 |
|--------|----------|------|
| `__DIFFUSE__` | `diffuse` | 基础色贴图 |
| `__HEIGHT__` | `bump` | 高度图，**直挂凹凸槽**的唯一合法贴图 |
| `__NORMALWRAP__` | `bump` | 仅法线：包一层 V-Ray 法线贴图节点，`bump` 子槽留空 |
| `__NORMALBUMP__` | `bump` | 法线+高度叠加：法线进 normal 子槽、高度进 bump 子槽 |
| `__NORMAL__` | — | 旧占位符，导出时统一改写成 `__NORMALWRAP__` |
| `__ROUGHNESS__` | `roughness` | 粗糙 |
| `__DISPLACE__` | `displacement` | 置换 |
| `__AO__` | `diffuse` | 走 VRayDirt 叠加，不占独立槽（见下节） |
| `__OPACITY__` | `opacity` | 透明度 |

凹凸槽的口径：**VRayMtl.bump 里只允许出现高度图，或包着法线图的法线节点**。法线图绝不直挂 `bump`——V-Ray 会把法线 RGB 当高度采样，出乱七八糟的假凹凸。

| 场景 | 生成语句 | 结果 |
|------|----------|------|
| 法线 + 高度（勾 `bump_on` + 勾 height） | `mfAttachNormalBump mtl <normal> <height>` | normal 子槽=法线图，bump 子槽=高度图 |
| 只有法线（无 height） | `mfAttachNormalBump mtl <normal> undefined` | normal 子槽=法线图，bump 子槽留空 |
| 只有高度（无 normal） | `mfAttachTex mtl #("bump") <height>` | 高度图直挂凹凸槽 |

### V-Ray 类名的本地化陷阱

V-Ray 插件类名随界面语言本地化：英文版是 `VRayNormalMap`，中文版注册名是 `VRay_法线贴图`（V-Ray 6.20.06 实测）。脚本里直接写 `VRayNormalMap` 字面量在中文环境下会**静默返回 `undefined`**，导致法线叠加分支被跳过、法线图被降级直挂 `mtl.bump`——不报错、日志只有一行 WARN，极难察觉。

因此 `mfNormalMapClass()` 不写死类名，按三级解析：

1. 候选名 `#("VRayNormalMap", "VRay_法线贴图", "VRay_法线")` 依次 `execute` 取类；
2. 兜底遍历 `textureMap.classes`，取「名称含 vray 且含 法线/normal 且不含 凹凸/bump2normal」且能实例化的类；
3. 全失败才降级直挂 `bump` 并记 WARN。

新增任何 V-Ray 节点类都要走同样的解析思路，别写死类名。

挂贴图前必须校验属性值的类型：只接受 `textureMap` 类型或 `undefined`。否则会把 `diffuse_multiplier` 这类数值属性误当贴图槽塞进一张 bitmap，材质直接变黑。贴图实例由 `mfBitmap` 统一创建，失败只记日志。

## AO 的非破坏性叠加

AO 不做成独立贴图槽（VRayMtl 没有 AO 槽），而是用 `VRayDirt` 包住已有的漫反射位图：

- 原漫反射位图挂到 `VRayDirt.unoccludedColor`，遮蔽区域自然变暗。
- 仅在"VRayMtl 材质 + VRayDirt 类可用 + 已有 diffuse 贴图"三者同时成立时执行，否则跳过并记日志。
- 因此 AO 叠加不会覆盖用户已挂的贴图，重复执行也不会叠加两层。

## 材质降级链

`VRayMtl` → `PhysicalMaterial` → `StandardMaterial`，每级构造都包 `try/catch`。三者全失败才 `messageBox` 提示并 `throw` 中止——这是唯一允许中断脚本的情形，因为此时继续跑没有意义。

## 自诊断

脚本末尾在 MAXScript Listener 打印成功/失败计数与明细，格式：

```
[OK] ...    [FAIL] ...
[OK] 材质库已保存: <path>
```

用户回贴 Listener 输出仍是排查的主渠道——尤其"参数写了但 Max 里没变"这类问题，Listener 里的 `[FAIL]` 明细能直接指到是哪个属性名失配。但不再是唯一渠道：`core/preflight.py` 能在导出前静态拦一批环境层问题，`scripts/verify_ms.py` 能体检已有 `.ms`。顺序是：先静态体检 → 再看 preflight 阻断项 → 最后才麻烦用户贴 Listener。

`verify_ms.py` 除了语法/编码/贴图路径，还会按文件名语义校验凹凸链路：`mfAttachNormalBump` 的 normal 子槽必须是法线图、bump 子槽必须是高度图（颠倒即 ERR）；`bump` 子槽为空表示只做法线扰动，记 OK；若 `bump` 槽里直接是法线图且脚本另带高度图，升 ERR；只有法线图、无高度图且没走法线节点包装时记 WARN（占位符或类名解析出了问题）。

## 导出前 preflight 拦什么

`POST /api/preflight` 与导出共用同一套检查，`error` 级（`blocking`）非空时导出被拒。四个维度：

| 维度 | 检查内容 |
|------|---------|
| 环境 | 扫 `ApplicationPlugins` / Chaos 安装目录找 V-Ray 与版本（读 `PackageContents.xml` 的 `AppVersion`）。探不到只报 `warn`，**不臆报版本** |
| 名称 | 空 / 非法字符 / 过长 / 中文材质名 |
| 贴图 | 文件存在性、能否用 GBK 编码、路径总长度 |
| 脚本 | 已生成 `.ms` 的语法级校验：`#"` 字面量、括号与引号平衡、`case` 分支重复、顶层包裹、GBK 可写 |

设计原则是**只做可确定性判定**：探不到的写"未知"走 `warn`，不猜。所以 `blocking` 为空不等于"Max 里一定跑得通"，只表示没有确定性错误。

## 材质库

执行末尾调 `saveTempMaterialLibrary lib MF_MATLIB`，把材质写进 `.mat`。整段包在 `try/catch` 里，失败只提示"材质已在编辑器中"——`.mat` 写不出来不该让用户以为材质没做成功。

## 已知边界

- 目标环境按 3ds Max 2024 / V-Ray 6.x 编写，V-Ray 7/8 靠别名降级兼容，未做实测。
- 预览球的 three.js 效果只是近似，不等于 V-Ray 最终渲染结果。
- 置换（`texmap_displacement`）需要模型有足够细分才能看出效果，脚本只写材质参数，不改几何体。
*（内容由AI生成，仅供参考）*
