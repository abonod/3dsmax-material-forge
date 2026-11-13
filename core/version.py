# -*- coding: utf-8 -*-
"""版本号的单一事实来源（SSOT）

为什么单独放一个文件而不是写进 SKILL.md 或 package.json：
    版本号在三个地方要用——SKILL.md 的 frontmatter（给人看/host 识别）、
    打包脚本（决定 dist 文件名）、运行期接口（排查用户现场版本）。
    任何一处手写都会漂移。这里只写一次，其余由 scripts/package_skill.py 同步。

改版本号的规矩（语义化版本）：
    MAJOR  —— 导出格式或参数 id 破坏性变更（老 .ms / 老预设快照不再兼容）
    MINOR  —— 新增预设、新增参数（0.7.0 属于这一类：新增纯色模式 + 程序化微结构，+10 参数）
    PATCH  —— 文案、UI、文档、bugfix，不动数据结构
"""

from __future__ import annotations

__version__ = "0.8.4"
VERSION_TUPLE = tuple(int(x) for x in __version__.split("."))

# 最近一次版本的变更摘要，打包时会写进 dist/BUILD_INFO.json，方便回溯"这包有啥"
RELEASE_NOTES = {
    "0.8.4": [
        "【资源泄漏根因1】Edge 进程树未回收：ui_check / ui_solid_check / capture_presets 收尾只对 Edge 主进程 terminate，GPU/renderer/crashpad/utility 等子进程继续存活并锁住 --user-data-dir。新增 scripts/_edge_util.py（kill_tree / kill_proc / kill_by_profile / safe_rmtree / reap_stale_profiles），收尾一律按进程树强杀，退出后占用 profile 的进程数实测为 0",
        "【资源泄漏根因2】兜底 profile 无限增殖：CDP 连接失败时旧逻辑新建 edge_profile_<时间戳> 重跑且从不回收，每次失败永久多约 76MB（实测累积 13 份、933MB）。改为启动前先清场（杀掉占用同一 profile 的残留实例）再复用固定 profile，兜底分支仅在清场后仍失败时启用，且登记为一次性 profile 并在 finally 回收（MF_EDGE_EPHEMERAL=1 跨进程传递标记）",
        "【资源泄漏根因3】profile 目录堆积：新增 reap_stale_profiles，脚本启动时自动清理 temp 下历史 edge_profile_* 残留；MF_KEEP_EDGE=1 可跳过，便于调试期保留现场",
        "修复 profile 路径解析：路径含空格（D:\\Marvis skill\\...）时按空格切分命令行会漏配进程，改为按引号包裹的三种写法分别正则提取，实测 16 个 msedge 进程中精确匹配到 8 个占用者且不误伤日常浏览器",
        "新增运维入口：python scripts/_edge_util.py status|kill|reap，可查看各 profile 体积与占用进程数、按 profile 精确清理、批量回收历史残留",
        "实测：ui_check 改造后 26/26 全过（无回归），退出后残留进程 0、profile 恒定单份 69.7MB；skill 根目录由 1491MB 降至 79.8MB（清理旧包 2 个 + output 残留 26 个 + 历史 profile 14 份）",
    ],
    "0.8.3": [
        "【缺陷1】贴图同名覆盖彻底修复：导出目录改为 <job>/export/<材质名>/textures/，一材质一目录，同一张源图导出的多个材质各自持有自己的 albedo/normal/... ，先导出的 .ms 不会再读到后一次导出的颜色",
        "【缺陷2】导出后贴图自检落地：新增 core/export_guard.py，导出返回前逐张 os.path.exists 校验贴图、按 script_url 反查 .ms 里的 bitmapTexture 引用是否断链，结果以 selfcheck{ok,checked,missing,broken_refs,packed} 随导出返回，缺图不再静默成功",
        "【缺陷2】preflight 联动：贴图通道完整性由正则猜测改为读取实际导出结果，缺通道给 WARN；expected_channels 把 albedo/roughness 列为恒生成通道，恒生成图丢失也能查出",
        "【缺陷3】上传与生命周期收敛：上传改分块读 + 32MB / 单边 9000px / 总像素 4000 万三道上限（超限 413，不进内存），JOBS 加 MAX_JOBS 上限按 created_at 淘汰，产物目录加 TTL（有 export/ 的只回收中间过程，交付物保留），启动时先清一次",
        "【缺陷4】0.8.2 新功能补回归：evals 从 14 条扩到 17 条，新增材质隔离 / 导出自检 / 三层参数+打包通道三条用例，并给 artifact 轨挂 _probe_isolation / _probe_selfcheck / _probe_tiers / _probe_pack 四个探针，直接调接口不依赖 Max；探针已用负样本验证具备判别力（错期望必报错）",
        "【检查统一】新增 scripts/export_guard_check.py（33 项行为断言：隔离/自检/上传边界/TTL/内存淘汰）与 scripts/run_all_checks.py + run_all_checks.ps1，一条命令串起 evals / 导出 / 注册表 / 贴图元信息 / UI / 纯色像素共 6 项，任一失败即非零退出；--fast 跳过两个 Edge 截图检查（约 15 秒）",
        "导出接口补充 script_url 相对化（<材质名>/xxx.ms）与源图尺寸、体积回显（source_size / source_bytes），上传被限流压缩后用户能立刻看出实际吃进去的是什么",
        "前端导出结果卡新增自检块：打包张数、缺失通道、断链引用三行直显，出问题不再只靠 note 一句话",
        "运行日志落盘：export() 新增 logfile_path，脚本经 MF_LOGFILE 把每次 fileIn 的完整日志追加到 <导出目录>/<材质名>.log（含 [OK]/[SKIP]/[FAIL] 与落槽号），实机跑完可直接读文件定位，不再依赖 Listener 手抄",
        "P0 三项实机证明（3ds Max 2024 + V-Ray 6.20.06 中文版，无头 fileIn）：第 1 槽放入用户自己的 ProtectedMat 不被覆盖，白落 2 槽、米黄落 3 槽；同一份 .ms 连跑两次复用原槽且材质总数仍为 1；材质上已知贴图路径全部 exists 且指向同级 textures/，目录内 6 张通道图齐全；材质库缺失降级为 [SKIP] 而非 [FAIL]，日志无 FAIL 行。证据见 docs/p0-ondevice-0.8.3.txt",
    ],
    "0.8.2": [
        "【P0】材质编辑器落槽修复：meditMaterials[i] == undefined 在 3ds Max 2024 恒为假（默认槽自带 Standard 材质），原逻辑必然覆盖第 1 槽。改为 mfEmptyMeditSlot 三重判定（undefined / 空材质名 / 无 diffuse 属性）并按名复用已存在的同名材质，16 份预设不再互相踩踏",
        "【P0】贴图随导出目录打包：export() 新增 pack_textures，把 albedo/normal/roughness/height/ao 复制到 <导出目录>/textures/，脚本优先同级相对路径，整包挪机器不断链",
        "【P0】导出引导落到界面：导出结果卡改为三步操作引导（限时 20 秒内 Run Script → 看材质编辑器 → 查报错行），新增「复制 .ms 路径」「打开目录」两个按钮，不再只丢一句 note",
        "【P1】参数三层暴露：新增 INTERMEDIATE_IDS（25 项）与 tiers 划分，界面新增「中级」档（常用 5 + 微调 25 = 30 项），进阶 31 项折叠收起；三档互斥且全覆盖 61 项",
        "【P1】evals 机器评分器：新增 evals/runner.py（答案断言 + 产物断言 + JSON 报告）与 14 条 machine 断言，金标准 14/14 PASS、负样本 7/7 判 FAIL，评分器具备真实判别力而非恒定 PASS",
        "【P1】preflight 预检补齐：新增模板结构检查（本地化类名 / 已移除接口 / 槽位判空回归守卫）与参数取值域检查，越界值在生成前即被拦截",
        "【P1】体检器噪声清理：verify_ms 的材质库关键 API 从已移除的 saveTempMaterialLibrary 改为 loadMaterialLibrary / saveMaterialLibrary，每份脚本恒定的假 WARN 消失",
        "ui_check 增加中级档 4 项断言（可见 30 项 / 含光泽分层 / 不露色散 / 可切回新手），26/26 通过",
        "【P2】dist 瘦身：打包脚本新增 report_stale()，按「最新 2 个版本包 + 1 份回滚包」点名待清理项，物理删除交给回收站，避免脚本内置不可恢复删除",
        "【P2】参数注册表守卫固化：scripts/registry_check.py 校验文档与代码的分组数 / 61 项 id 覆盖 / 三档互斥并集 / 过时口径，文档补齐全量清单",
        "【P2】UI 检查自动兜底：默认 Edge profile 被上一次检查的残留实例占住时（CDP 端口不监听），自动换带时间戳的干净 profile 重试一次，不再需要人工设 MF_EDGE_PROFILE",
        "【P2】vendor 裁剪评估结论：three.module.js(643KB) 只做 import 与再导出，three.core.js(1.44MB) 承载几何与材质基础，二者职责不同不可合并，暂不裁剪（见 docs/vendor-size-decision.md）",
    ],
    "0.8.1": [
        "参数落点重做：PROP_TARGETS 改为由真机实测属性表驱动的精确候选链，彻底弃用模糊关键词匹配，修复 reflectionColor→reflection_exitColor 一类别名错位",
        "模板别名表改由 PROP_TARGETS 自动生成，消除「别名表 / 目标表」两份定义互相漂移的问题",
        "mfSet 增加类型守卫：值类型与目标属性不符时不再硬写，逐项输出 [OK]/[SKIP] 日志便于现场定位",
        "补齐三个依赖开关：dispersion→refraction_dispersion_on、sss→translucency_on、thinFilm→thinFilm_on，薄膜厚度按 min/max 成对写入",
        "渲染器判定改为宽松匹配（vray / v_ray / ray 三种写法都能命中），不再因类名形如 V_Ray_GPU_6__update_2_1 而误报 WARN",
        "AO 节点改用本地化类名兜底：VRayDirt→VRay_Dirt→VRay_污垢 逐级探测，中文版 V-Ray 下 15/16 预设成功叠加污垢",
        "材质库保存降级为能力探测：3ds Max 2024 已移除 newMaterialLibrary / loadTempMaterialLibrary，无库文件时输出 [SKIP] 而非 [FAIL]，不再把环境能力缺失误报成失败",
        "真机自检结果：参数写入 OK=235 / FAIL=0，贴图通道（albedo/normal/roughness/height/ao）全部命中",
    ],
    "0.8.0": [
        "预设参数落点重做，材质在 Max 中首次完整成型",
    ],
    "0.7.2": [
        "遮罩彻底与程序化场解耦：供图（上传图片）与纯色模式同样吃低频遮罩，只压低频明暗/粗糙/找平，不动原图高频细节",
        "遮罩尺度改为世界尺度补偿：按 uv_tiling 把「米」换算成贴图内循环数，区块放不进一块贴图时自动削弱强度并在 mask_info 里回显 repeat_risk",
        "新增 /api/build 的 preview 参数：滑动中先出 320² 快预览（实测 1024² 全图 4.0s → 快预览 0.54s，约 7 倍），松手补全尺寸",
        "拖动中直接点导出会先补算全尺寸再导出，避免把低分辨率预览当成品",
        "遮罩与种子参数不再挂在 proc_on 下：纯色/供图模式也能调；「换一版」按钮可一键换种子",
        "遮罩尺度滑杆标签与提示改为米制（1.0≈1 米区块）并显示实际生效百分比",
        "校验脚本支持 MF_EDGE_PROFILE 环境变量切换浏览器 profile，修复残留 headless 实例导致的 CDP 连接失败",
        "evals 补充遮罩重复与滑杆卡顿两条用例（8 → 10 条）",
    ],
    "0.7.1": [
        "新增低频区域遮罩：程序化场里叠 3 种尺度（大区域 / 竖向流挂 / 中频脏污斑块）的低频带通噪声，仍然 100% 无缝平铺",
        "遮罩按分位拉伸 + S 曲线塑形，形成成片分区而非整体发灰：低谷区麻面被压平（涂薄/打磨），峰区保留完整橘皮并带宏观厚薄",
        "高度调制后按原场起伏总量等比缩放（不再做分位拉伸），实测分区比 1.37 → 1.61、起伏与遮罩的块级相关 0.87，分区不会被打平",
        "遮罩同时驱动 roughness 与 albedo：脏污/补漆段更哑、涂厚段更亮，不同墙段不再是同一张纹理的复制粘贴",
        "新增参数 proc_mask（区域遮罩强度，默认 0.6）、proc_mask_scale（遮罩尺度，默认 1.0），参数总数 50 → 52",
        "proc_info 回显 mask / mask_scale / mask_seamless，可核对遮罩强度与无缝性",
        "新增 scripts/proc_mask_check.py：遮罩专项校验 19 项（参数/无缝/分区相关/强度单调/零点回归/链路集成）；ui_solid_check 断言 16 → 18 项",
    ],
    "0.7.0": [
        "乳胶漆不再依赖法线图：新增纯色模式（/api/solid），不传任何图片也能算出带真实微起伏的 albedo/height/normal/roughness/ao",
        "新增程序化微结构（proc_texture）：按物理尺度生成滚涂麻面，并叠加低频云状不匀，避免纯色墙面像塑料",
        "色板染色改为校色匹配（tint_albedo_ex 的 match 模式）：选什么色，成品 albedo 的平均色就收敛到该色，实测 ΔE ≤ 0.03",
        "染色三模式可选：match（默认，校色匹配）/ mono（保留明暗只换色相）/ multiply（传统乘法，会偏暗）",
        "预览改为烘焙式：改色直接重烤 albedo 走贴图，不再靠 material.color 乘色（旧做法会二次相乘导致发灰）",
        "前端新增纯色模式入口、色差回显（ΔE）、重烤中指示；改色/改程序化参数触发防抖重烤",
        "新增 scripts/ui_solid_check.py：纯色模式与色板校色的界面专项检查，16 项断言",
        "参数总数 40 → 50，分组 5 → 6（新增「质感」组承载程序化微结构参数）",
    ],
    "0.6.0": [
        "新增 5 套墙面/铝材质预设：壁纸、墙布、乳胶漆、铝合金(磨砂)、铝合金(拉丝)，预设总数 11 → 16",
        "参数总数 38 → 40：暴露 anisotropy_amount（各向异性强度）、anisotropy_axis（拉丝方向）",
        "各向异性关闭时显式归零 anisotropy，避免在 Max 里重复执行残留上一次的 0.72",
        "前端参数计数改为从 /api/schema 动态取值，不再写死数字",
        "ui_check 参数断言改为按 schema 动态计算；output 被清空时自动生成上传样本图",
        "新增 scripts/package_skill.py：一键体检 + 打包 + 版本号同步 + BUILD_INFO 落盘",
    ],
}
