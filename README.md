# 3ds Max Material Forge

上传一张图 → 程序化推导 PBR 五通道贴图 → 在网页里勾选参数并实时预览材质球 → 导出一份可直接在 3ds Max 中 `Run Script` 打开的材质脚本（V-Ray 的 VRayMtl 或 Corona 的 CoronaPhysicalMtl，同一套参数出两版）。

**核心设计约束：整个程序运行期间，不启动、不调用、不依赖 3ds Max 进程。**

---

## 1. 运行链路（100% 离线，零 DCC 依赖）

```
图片 ──> texture_engine.py ──> albedo / height / normal / roughness / ao （5 张 PNG）
                                     │
用户勾选 ──> params.py ──> resolve_vray()   ──> VRayMtl 属性字典
                        └─> resolve_corona() ──> CoronaPhysicalMtl 属性字典
                                     │
                                     ▼
                        ms_exporter.py ──> <材质名>.ms （纯文本，GBK/CRLF）
                                     │
                                     ▼
                     在 3ds Max 里 Run Script ──> 生成材质 + 预览球 + .mat 材质库
```

Python 侧只做三件事：图像推导、参数组织、文本脚本生成。没有任何 `subprocess` 拉起 `3dsmax.exe` 或 `3dsmaxbatch.exe`。

## 2. 产物说明

| 产物 | 何时生成 | 说明 |
|------|----------|------|
| `textures/*.png` | 程序内即时生成 | PBR 五通道贴图，纯 PIL + numpy 计算，不需要 GPU |
| `<材质名>.ms` | 程序内即时生成 | MaxScript 脚本，纯文本，可离线产出、可直接审阅 |
| `<材质名>.mat` | 在 3ds Max 执行 `.ms` 时 | 材质库是 Autodesk 二进制私有格式，只能由 3ds Max 自身写入 |

> `.mat` 无法脱离 3ds Max 生成，这是格式本身决定的，不是实现取巧。因此 `.ms` 里内置了 `saveTempMaterialLibrary`，执行时会把材质同时写进 `.mat`，一次操作两种产物。

## 3. .ms 的容错设计

由于本机无法无头验证脚本，脚本按「属性名失配也不报错」的原则编写：

1. **反射式写入** —— 不硬编码 VRayMtl 属性名，先精确写，失败再按关键词在 `getPropNames` 里降级匹配，V-Ray 6/7/8 改名不失效。
2. **全 try/catch** —— 单个属性写入失败只记日志，不中断脚本。
3. **材质双分支** —— `VRayMtl` 不存在时自动降级 `PhysicalMaterial` → `StandardMaterial`。
4. **贴图槽类型校验** —— 只认值为 `textureMap` 或 `undefined` 的属性，避免把 `diffuse_multiplier` 这类数值属性误当贴图槽。
5. **AO 非破坏性叠加** —— 用 `VRayDirt` 包住原漫反射位图（`unoccludedColor = albedo`），不会覆盖已挂的贴图。
6. **自诊断** —— 执行结束在 MAXScript Listener 打印成功/失败明细与材质类型。

## 3.1 Corona 目标（0.9.0 新增）

导出时选择 `renderer = corona`，同一份 61 参数与贴图会翻译成 Corona 语义：

| 语义 | V-Ray | Corona | 差异原因 |
|------|-------|--------|----------|
| 基色 | `diffuse` | `baseColor` | 金属色在 V-Ray 存于 `reflectionColor`，Corona 只有 `baseColor`，需回灌 |
| 粗糙 | `reflection_glossiness` | `baseRoughness` | 光泽与粗糙方向相反，翻译时取 1−x |
| 色散 | 数值 + `refraction_dispersion_on` | `refractDispersion` | Corona 无使能开关 |
| 薄膜 | `thinFilm_on` + 厚度区间 | `baseThinFilmIor` / `baseThinFilmThickness` | 无开关、无区间 |
| 法线 | `VRay_法线贴图` / `VRayNormalMap` | `CoronaNormal` 包装 | 类名走候选链探测；缺类时法线直挂 bump 并 WARN |
| AO | `VRay_污垢` | `CoronaAO` | 同上 |
| 置换 | `VRayDisplacementMod` | `CoronaDisplacementMod` | 按当前渲染器优先 |

未搬过去的参数会汇总成一行 `mfDropped` 日志（如非金属预设的 `reflectionColor`），属于语义等价或 Corona 天然如此，不视为失败。

## 4. 安装与启动（面向新用户）

### 4.1 环境要求

| 项 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Windows 10 / 11 | `启动.bat` 为 Windows 批处理；macOS / Linux 可手动 `python app.py`（程序本体跨平台） |
| Python | 3.9+（推荐 3.11） | `启动.bat` 优先复用 Marvis 内置 runtime（`python311`），否则回退到 PATH 上的 `python` |
| 浏览器 | Chrome / Edge 新版 | 材质球预览依赖 WebGL |
| 3ds Max | 2024（其他版本一般可用） | 仅在执行 `.ms` 时需要；程序本体全程不依赖 Max 进程 |
| V-Ray | 6.x（6/7/8 属性名自适应） | 未安装时 `.ms` 自动降级 `PhysicalMaterial` → `StandardMaterial` |
| Corona | 6+（可选） | 目标渲染器选 Corona 时使用；未安装时降级 `CoronaPhysicalMtl` → `CoronaLegacyMtl` → `PhysicalMaterial` |

Python 依赖（见 `requirements.txt`）：`fastapi` / `uvicorn` / `python-multipart` / `Pillow` / `numpy`。

### 4.2 安装方式 A：使用打包件（推荐）

`.skill` 是标准 zip 容器（可改名为 `.zip` 用压缩软件打开）。

1. 到 [Releases](https://github.com/abonod/3dsmax-material-forge/releases) 下载 `3dsmax-material-forge-0.8.4.skill`；
2. 在 Marvis 中导入该 skill，或用压缩软件解压到任意目录；
3. 进入解压目录，双击 `启动.bat`，浏览器会自动打开 `http://127.0.0.1:8765`。

### 4.3 安装方式 B：从源码运行

```bash
git clone git@github.com:abonod/3dsmax-material-forge.git
cd 3dsmax-material-forge
pip install -r requirements.txt
python app.py
```

`启动.bat` 会先探测 `127.0.0.1:8765` 是否已监听，未监听才拉 Python 并尝试补装缺失依赖，因此首次运行需联网；内网或离线环境请先手动执行 `pip install -r requirements.txt`（可挂国内镜像源）。

### 4.4 常见问题

| 现象 | 原因与处理 |
|------|------------|
| 端口被占用 | `启动.bat` 发现 8765 已监听会直接打开页面而不重复启动；确需换端口时修改 `app.py` 中 `uvicorn.run` 的端口参数 |
| `.ms` 在英文 Windows 打开乱码 | `.ms` 按 GBK + CRLF（无 BOM）写出以贴合中文版 MaxScript；建议材质名与贴图名使用英文/拼音，或将文件以本机 ANSI 编码另存后再执行 |
| 想用 GPU 加速贴图 | 无需 GPU：五通道全部由 PIL + numpy 计算；`core/ai_enhance.py` 的在线增强为可选，不配 Key 时自动回落本地算法 |
| 未装 V-Ray 能导出吗 | 能。`.ms` 会降级为 `PhysicalMaterial`，`VRayDirt` 等专属属性被跳过，并在 MAXScript Listener 打印失败明细 |
| 材质库 `.mat` 没生成 | `.mat` 是 Autodesk 私有二进制格式，只能由 3ds Max 在执行 `.ms` 时写入，无法离线生成 |

## 5. 目录结构

```
3dsmax-material-forge/
├── 启动.bat                   Windows 一键启动（探测端口 / 选 Python / 开浏览器）
├── requirements.txt           Python 依赖清单
├── LICENSE                    MIT 许可证
├── SKILL.md                   Skill 说明与调用契约
├── app.py                     FastAPI 服务（上传/推导/预览/导出）
├── core/
│   ├── params.py              参数注册表 + 分组 + 预设 + resolve_vray 映射
│   ├── texture_engine.py      PBR 五通道推导
│   ├── ai_enhance.py          可插拔在线 AI 增强接口（默认回落本地）
│   └── exporters/ms_exporter.py   MaxScript 生成器
├── web/                       前端（three.js 材质球实时预览 + 勾选式面板）
├── probe/                     已停用的历史探测脚本（不参与运行）
└── output/<job_id>/           每次导出的产物
```

## 6. 使用

双击 `启动.bat`（安装步骤见 4.2 / 4.3），浏览器打开 `http://127.0.0.1:8765`：拖入图片 → 调参数看预览 → 导出 → 得到 `.ms` 与 `textures/`。

在 3ds Max 中：`Scripting > Run Script` 选择该 `.ms`。

## 7. 扩展端口

- 新增 V-Ray 参数：`core/params.py` 加一条 `Param` 定义即可自动出现在面板上。
- 新增属性名别名：`core/exporters/ms_exporter.py` 的 `PROP_ALIASES` 与模板内 `case` 各加一行。
- AI 增强：`core/ai_enhance.py` 实现 `BaseProvider` 子类即可接入在线模型。

## 8. 已知边界

- 本机 GPU 为 AMD RX 580，无 CUDA，无法本地跑 Stable Diffusion；贴图走本地算法或在线 API。
- `.ms` 的最终正确性只能在 3ds Max 中验证；本程序只做静态结构体检（括号/引号/关键 API 齐全）。
- 材质球预览基于 three.js，是近似效果，不等于 V-Ray 最终渲染。

## 9. 许可证

本项目采用 [MIT License](LICENSE)：可自由使用、修改、分发与商用，保留版权声明即可。
