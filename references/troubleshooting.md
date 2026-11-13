# 故障排查

按"现象 → 先查什么"组织。排错顺序比结论重要：先确认现象发生在哪一段（服务 / 推导 / 导出 / Max 执行），别跳步。

## 服务起不来

1. `python --version` 确认 ≥3.10。
2. `pip install -r requirements.txt`（fastapi / uvicorn / python-multipart / Pillow / numpy）。
3. 端口占用：默认 8765。`netstat -ano | findstr :8765` 查占用进程，或设 `MF_PORT=8790` 换端口。
4. 直接 `python app.py` 看控制台报错，比双击 bat 更容易看到堆栈（bat 窗口可能一闪而过）。

启动成功的判据是 `GET /api/health` 返回 200，不是"窗口还在"。

## 上传失败

- `POST /api/upload` 是 multipart，字段名必须是 `file`。用 curl 时别写成 `-F image=@...`。
- 图太大或格式冷门会退化推导质量。优先用 PNG / JPG，短边建议 ≥512px；低于 256px 推导出的法线、AO 基本没意义。
- 返回里没有 `job_id` 就是没成功，不要拿上一步的旧 `job_id` 继续导出。

## 贴图看起来不对

| 现象 | 原因与处理 |
|------|-----------|
| AO 全黑 / 全白 | 输入图本身对比度极端；换正常曝光的参考图 |
| 法线像"反的"，凸起变凹陷 | Max 的 bump 通道对法线方向敏感，先在 Max 里把 bump 强度调低确认，再决定是否需要在导出端翻转绿通道 |
| 贴图有明显接缝 | 参考图不是可平铺纹理；本工具不做 seamless 处理，这是输入问题不是 bug |
| 金属材质发灰 | `metal_mode` 未勾选；勾选后漫反射会被压暗到 0.35，由 albedo 驱动金属度 |

## 在 3ds Max 里执行 .ms 报错

**第一步永远是看 Listener**。脚本自带诊断输出，`[OK]` / `[FAIL]` 逐行列出属性写入结果，比猜快十倍。

| 现象 | 排查方向 |
|------|---------|
| 中文材质名 / 路径乱码，或直接语法报错 | 编码问题。确认文件是 GBK 无 BOM（`verify_ms.py` 会报实际编码）。用编辑器另存时选"ANSI"，别存成 UTF-8 with BOM——BOM 会让 Max 把前几个字节当语法的一部分 |
| 报 `-- 语法错误` 但看起来没问题 | 括号或引号不平衡。跑 `verify_ms.py` |
| 报 `位于 string，需要 "(" or "["` | 出现了 `#"字符串"` 写法。Max 不接受这种 name 字面量，只认裸 `#标识符` 或 `(toName "x")`。改 `ms_exporter._ms_name()`，别手改产物 |
| 材质创建成功但全白/全黑 | 贴图丢了。先看 `.ms` 同级有没有 `textures/` 目录（脚本优先按同级目录解析）；同级没有才回落脚本里写死的绝对路径，所以整个 `export/` 目录搬动时 `textures/` 必须一起走 |
| `[FAIL]` 大量出现，材质基本没参数 | 属性名全没命中。多半是 V-Ray 版本差异，按 `references/ms-exporter-guide.md` 扩 `PROP_ALIASES` + 模板 case |
| `VRayMtl` 不存在，出来的是 Physical / Standard | V-Ray 没装或被禁。脚本已降级，材质能用但参数语义不同，需要告知用户 |
| 材质球不显示 | 脚本会创建预览球；若场景单位或视图裁剪异常，按 `Z` 键缩放到全部 |
| 场景里多了个球 | 预览球行为，脚本正常产物。导出成品前删掉即可，或用 `MF_AUTOASSIGN`/模板开关控制 |
| 原来放在 1 号槽的材质不见了 | 材质编辑器 24 槽全满时脚本会覆盖 1 号槽并打 `[WARN]`，Listener 里能查到 |

## 参数在 Max 里没生效（最常见）

按顺序自查：

1. `verify_ms.py` 过不过——结构坏了先修结构。
2. Listener 里对应属性是 `[FAIL]` 还是根本没出现？没出现说明 `params.py` 里没写映射，或者 `depends` 前置开关没开。
3. `[FAIL]` 则说明属性名没命中：查 `PROP_ALIASES` 有没有这一条，以及模板 `mfAssign` 的 `case` 里有没有同一分支。**两处都要有。**
4. 开关型参数取消勾选后属性还在？检查 `vray_off` 是否漏写。

## 面板操作异常（网页侧）

| 现象 | 排查方向 |
|------|---------|
| 点预设后参数没变 | 先确认点的是预设下拉的选项而不是标签文字；再看 `/api/export` 提交体里 `preset` 是否为中文标签——前端按标签反查 key，传 key 会静默落回默认 |
| 开关点不动 / 点了没反应 | 检查是否有透明浮层吃掉点击（`pointer-events`）。历史上踩过一次：开关的 CSS 伪元素盖住了 input，JS 完全没收到事件，只能靠 CDP 打 `click()` 才暴露 |
| 中文显示成方块/问号 | 页面 `charset` 与文件实际编码不一致；Python 侧写 HTML 时显式 `charset=utf-8` |
| 导出前弹出红色阻断 | 这是 preflight 在工作：`blocking` 项必须清零才能导出。看返回里 `blocking` 数组的 `message`，改对应项而不是绕过 |
| 导出按钮点了没下载 | 看浏览器 DevTools 的 Network，`POST /api/export` 是否 200；被 preflight 拦时返回非 200 且带 `blocking` 明细 |

## 自检脚本怎么用

- `python scripts/verify_ms.py <文件或目录>` —— 静态体检**已存在的** `.ms`：语法结构、编码、占位符残留、凹凸/法线通道指向、贴图路径存在性。它不生成脚本，要先导出。传目录会递归扫所有 `.ms`，全通过退出码 0。
  - 通道指向这条按**文件名语义**判定（`texture_engine` 按通道名落盘，故 `height.png` / `normal.png` 可直接识别）：`mfAttachNormalBump` 的 normal 子槽必须是法线图、bump 子槽必须是高度图，颠倒记 ERR；`mfAttachNormalBump ... undefined`（仅法线包装、bump 子槽留空）记 OK；`mfAttachTex #("bump")` 只挂法线图且脚本里没有任何高度图时记 WARN（说明占位符或 V-Ray 类名解析失败、退回旧降级路径），若同脚本另有高度图却没用上则升为 ERR。
- `python scripts/ui_check.py` —— 无头浏览器跑 UI 全链路，26 项断言（含真实上传一张样例图、新手 5 项、中级 30 项、进阶 61 项、16 个预设逐个快照回填、窄屏 1000px 布局、预览球真出帧、报告卡阻断数）。改前端后必跑。
- 两个脚本的输出都会写进 `scripts/reports/`，`ui_check.json` 里逐条 `pass/fail`，失败项带 `detail`。**失败时先看 `detail` 别急着改代码**。
- 截图出现空白：页面滚动位置不对，`ui_check.py` 里截图前统一 `scrollIntoView` —— 新增截图点要沿用这个处理。
- `verify_ms.py` 报「贴图路径不存在」但脚本在 Max 里能挂上贴图？说明 `.ms` 同级有 `textures/`，运行时按相对路径命中了。静态体检只认脚本里的绝对路径，这条 ERR 在跨机搬运场景可放行，但别把同级目录漏拷。

## 不要做的事

- **不要反复试无头启动 Max。** 本机已实测可通过 `3dsmax.exe -silent -U driver.ms`（driver 内 `fileIn` 目标 `.ms`，末尾 `quitMAX #noPrompt`，日志 `createFile` 外写）验证导出脚本，单次约 50~60 秒，外层用 `WaitForExit`/`subprocess.wait(timeout=240)` 兜底强杀防弹窗卡死。
  - **两个必须前置的动作**：① driver 里先把 `renderers.current = (V_Ray_6__update_2_1())` 切成 V-Ray——默认是 `Arnold:Arnold`，V-Ray 材质节点分支不会按 V-Ray 语义走；② driver 用 **GBK** 写盘，否则中文日志乱码。
  - 一次只验证一个脚本，别指望 GUI 里的场景状态；验证完即 `quitMAX`，不留残留实例。
- **V-Ray 类名会本地化，别写死英文类名。** 中文版 V-Ray 6.20.06 里 `VRayNormalMap` 这个字面量**恒为 `undefined`**（注册名是 `VRay_法线贴图`，`VRayBump2Normal` 则是 `VRay_凹凸__法线`），而 `VRayMtl` 反而不本地化。写死类名的后果是静默降级：法线图被直挂 `mtl.bump`，V-Ray 把法线 RGB 当高度采样，出杂乱凹凸且不报错。脚本侧统一用 `mfNormalMapClass()` 的候选名+注册表遍历解析。
- **不要为了验证而重启 3ds Max 或改动用户场景。** 让用户自己在 Max 里 Run Script 并回贴 Listener 输出，成本更低。
- 不要修改 `output/` 下历史任务的产物当作"修复"——重新导出一次更干净。
- 不要把 UI 断言写成"元素存在就算过"。开关是否存在与点击后参数是否真的变，是两件事——历史 bug 全部藏在后者里。
- 同理，"画布尺寸正常"不等于"预览球画出来了"：空 canvas 也能量出 458x571。渲染是否真的发生要看渲染循环埋点（`__mfFrames` / `__mfTris` / `__mfMesh`），别退回只看 `getBoundingClientRect`。
- 窄屏断言要按断点算：`style.css` 的 1180px 断点只把左右栏 300/320 收窄到 260/280，中列靠 `1fr` 顶住，所以要断言中列实际像素不被压塌，而不是断言"有 3 列"。computed 的 `gridTemplateColumns` 带 `px`，取值必须 `parseFloat`，直接 `Number()` 会得到 NaN。
