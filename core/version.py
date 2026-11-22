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

__version__ = "0.11.2"
VERSION_TUPLE = tuple(int(x) for x in __version__.split("."))
# 别名：scripts/docs_sync.py 用 getattr(V, "VERSION", "") 取版本号回写文档。
# 这里必须有同名常量，否则取到空串，文档里的版本号会被写成「当前 」（静默清空而非报错）。
VERSION = __version__

# 最近一次版本的变更摘要，打包时会写进 dist/BUILD_INFO.json，方便回溯"这包有啥"
RELEASE_NOTES = {
    "0.11.2": [
        "【高·导出后自检查的是内存不是磁盘】自检此前校验内存里的 draft 字符串，磁盘上的 .ms 与 draft 不一致时依旧判 ok=True，等于用户真正拿到的那份文件从未被检查过。改为只认磁盘：core/export_guard.check_export_file() 读盘并严格解码，文件不存在 / 编码无法识别 / 内容为空三种情况一律 ok=False；core/exporters/ms_exporter.export_with_meta() 回传真实落盘结果（path / encoding / bytes / sha256 / chars），export() 退化为薄封装，不再存在「按内存判断成功」的口径",
        "【高·中文脚本编码乱码】脚本按 GBK 落盘（3ds Max 中文版 fileIn 需要），旧自检用 utf-8 读，中文脚本要么解码异常要么读出错字。export_guard._detect_encoding() 改为 utf-8 严格 → gbk 严格两级探测，两级都解不开即报「编码无法识别」；app.py 三处导出改调 check_export_file() 并把 script_encoding 回给前端，用户能直接看到这次落盘到底是什么编码。scripts/export_guard_check.py 新增 part_selfcheck_disk 反例：磁盘上放坏文件，自检必须判 FAIL",
        "【中·降级文案不实】逐级降级到 PhysicalMaterial 时，脚本实际上一个参数、一张贴图都不写（后续全是 [SKIP]），旧文案却写「参数会尽量映射」，把失败描述成近似。tpl_vray.py / tpl_corona.py 两处降级分支改为如实文案「裸材质 / 不做任何还原补偿」，SKILL.md 新增「降级是降级，不是近似」一段，说明落到这一步只会得到一个同名空材质球（材质名带 _降级_ 前缀），不是近似效果",
        "【中·文档数字漂移】SKILL.md / README 的参数项数、分组数、evals 条数、检查项数、断言数、版本号全靠手抄，已出现「文档写 61 项、代码实际 62 项」这类漂移。新增 scripts/docs_sync.py：所有数字由 core/params.py、evals/evals.json、core/version.py、scripts/run_all_checks.py 的 CHECKS 现场计算后按「定位 + 重写」写回文档，分组表与类名对照表放进 AUTO 标记块整块生成；--check 逐字比对并在不一致时打印首个差异行号，--write 落盘后立刻复核幂等",
        "【中·文档类名与真机证伪冲突】文档写 CoronaNormal / CoronaDisplacementMod，而真机注册表已判定这两个名字在本机不存在（normal_map_class_rejected / displacement_modifier_rejected）。core/compat.py 清空这两项的 en 候选；tpl_corona.py 内部函数改名为 mfNormalWrapClass，不再用与已证伪类名同形的名字误导读者，候选链只保留真机确认的中文名；scripts/compat_check.py 新增 E 项「证伪名零残留」，对全仓 Markdown 扫描注册表里的已证伪名称，命中即 FAIL",
        "【版本号 SSOT 自身有缺口】docs_sync.py 用 getattr(version, \"VERSION\", \"\") 取版本，而 version.py 只定义了 __version__，取到空串会把文档版本号写成「当前 」（静默清空而非报错）。已补 VERSION 别名；同时把 SKILL.md 的版本说明改成受管格式——原写法用反引号包裹，正则匹配不到，等于版本号在文档里从未被同步过",
        "【门禁扩容】run_all_checks 由 12 项扩到 14 项：新增 docs_sync（文档与代码一致）与 docs_sync_neg（反例自测，含「行内数字改错」「AUTO 块被手改」「证伪名写回文档」三例，任一不被判 FAIL 即整体非零退出）；package_skill.py 新增打包前文档门禁，不一致或有证伪名残留时中止打包、dist 保持原样，且该门禁不受 --skip-verify 绕过",
        "实测：run_all_checks 14 项全 PASS；p_series_audit 正例 8/8、--selftest 反例 6/6；MAXScript 仅静态体检，未在 3ds Max 中真机编译，本机 Max 未启动",
    ],
    "0.11.1": [
        "【P4·真机结案】色散与法线凹凸叠加不再是「只到机制」：V-Ray 侧渲出 dispersion 与 normal_bump，Corona 侧渲出 normal_bump，全部人工目视 + 像素量化。凹凸可信度做了开关对照——ON 与 OFF 平均绝对差 27.41、76% 像素差>10、细节梯度 2.82 vs 2.01，证明确实是凹凸贴图在起作用，而不是 64 段球体的多边形轮廓假象",
        "【真机缺陷·visual_probe 语法错】模板第 153 行 `StandardMaterial name:\"__MF_SENTINEL__` 缺闭合引号，真机 fileIn 直接报「位于 bad，需要 <因子>」，此前从未真机跑过所以没暴露",
        "【真机缺陷·V-Ray 降采样静默落空】gen_visual_probe 写的是 progressive_maxSubdivs / dmc_minSubDivs，真机（V-Ray 6.20.06）根本没有这两个属性，整段 try 静默吞掉，导致单张 480×360 渲图跑 900s。改按真机实名 progressive_max_render_time / progressive_maxSamples / progressive_noise_threshold / adaptiveSubdivision_minRate|maxRate|threshold 下发，并关掉 GI",
        "【假通过缺口·探针从未带版本证据】gen_probe 生成器当时根本没注入 compat 探测段，日志里既无 COMPAT_LEVEL 也无 BAD 行——也就是说 P3/P5/P6/P7 宣称的「verify_probe_log.py --require-compat 强制版本证据」在真机上必然判 FAIL，而它此前只在自测里跑过，属典型的机制在、证据无。现改为调用 core/compat.probe_ms_logfn() 注入，版本证据同时写进 Listener 与日志文件",
        "【P1 补强·槽位回读 BAD 计数】探针新增每个贴图槽「写完再读回、读回为空即 BAD」的校验并输出独立 BAD 行，判据器 C5 由「无 [FAIL] 行」升级为「BAD=0」，杜绝「槽里有东西但其实是上一 job 残留」的误判",
        "【回归拦截】probe_gen_check 增加 MAX_VERSION / MAX_LANG / COMPAT_LEVEL / BAD 四类标记断言：生成器一旦漏注入探测段，静态检查当场 FAIL，不用等到换机器跑真机才发现",
        "【反查·P0–P7 落地审计】新增 scripts/p_series_audit.py：把八项修复各自的核心断言固化成判据（不认文档承诺，只认代码事实），并配 6 个反例自测验证判据具备判别力——P5 顺序倒置 / P6 去掉类表比对 / P7 verified 缺 evidence / P7 证据未随包分发 / P2 全局删 additionalBumpOn / P0 文档改一字，全部必须判 FAIL。反例的替换串一律锚定键名与结构、不锚定具体文案，否则取证文案一改，反例就退化成永远 PASS 的假覆盖（P7 首个反例正是这样失效的）。两项已注册进 run_all_checks（p_series / p_series_neg），回退即整体非零退出",
        "【反查事故·文档水印】docs/vray-param-gap.md 被带 AI 水印的编辑器保存过一次，正文未改但 --check 报漂移，等于「文档一致性」这项检查在说谎。gen_param_gap.py 增加 strip_watermarks() 剔除头尾水印后再比对，且保留「正文改一字仍 FAIL」的反例，不允许借净化放过真实漂移",
        "【反查事故·证据含糊】core/compat.py 里三个 verified 项的 evidence 原本只写「本机叠加链真机日志」，无法判断是哪个文件、跑到什么程度。改为指向归档日志与关键字段值（MAX_VERSION=26000 / MAX_LANG=zh / COMPAT_LEVEL=supported / BAD=0）及四张目视 PNG，并重生成 version-matrix.md",
        "【证据归档】真机日志 docs/evidence/probe_max2024_vray62006_corona12.log 与四张 PNG 归档进 docs/evidence，证据台账同步改写：P4 由「待目视」转「已目视」，P3/P5/P6/P7 明确标注「只有本机单版本，跨版本仍无真机」",
        "【反查事故·证据文件根本没进包】package_skill.py 的 EXCLUDE_SUFFIX 把 .log 全局排除，而 compat.py 三个 verified 项的 evidence 正指向 docs/evidence/*.log——即「包里声称支持某版本，支撑证据却不随包」，用户拿到包无法自证，是静默断链，且此前没有任何一项检查覆盖它（自测只验证字段在不在，不验证文件在不在）。修法三条：① docs/evidence 下 .log 走白名单进包；② 新增 verify_evidence_chain()，出包前先预演将要进包的文件清单，凡代码/文档引用的证据文件不在清单内即中止打包（dist 保持原样，不产半成品包）；③ 同一实现由 p_series_audit 的 P7 判据复用并配反例（把白名单改回空即须 FAIL），避免打包门禁与日常审计成为两套各自漂移的判据",
        "实测：verify_probe_log.py --selftest 12 例全过（含历史假通过场景）；真机探针 C1–C6 全 PASS（MAX_VERSION=26000 / MAX_LANG=zh / COMPAT_LEVEL=supported / BAD=0）；run_all_checks 12 项全 PASS（含 p_series 8/8 正例、p_series_neg 6/6 反例）",
    ],
    "0.11.0": [
        "【P0·文档自相矛盾修复】vray-param-gap.md 改为从 core/params.py 自动生成（scripts/gen_param_gap.py --check 幂等）：此前 Sheen / Thin film 两项写「无」却标 ✅，图例与正文互相打脸。现在状态标注由 vray_prop / vray_on / vray_off / vray_map 字段推导，代码改了文档不跟着改会直接 `--check` 失败，不再有手写空间",
        "【P1·假通过机制性阻断】探针日志新增绑定校验：每份材质 fileIn 后必须先清空 medit 24 槽（for i=1 to 24 用 StandardMaterial 覆盖）再载入再回读，并在日志里落 BIND_OK=<文件名>|<材质名>；scripts/verify_probe_log.py 把「无 BIND_MISMATCH = 通过」升级为硬判据——fileIn [FAIL] 但回读有结果、或回读材质名与预期不符，一律判 FAIL。历史教训（探针路径少一层子目录导致 4 份 fileIn 全 FAIL、却回读上一轮残留材质而误判通过）现在无法再复现",
        "【P1·判据器自测 9 例】verify_probe_log.py --selftest 覆盖 9 类假通过形态：fileIn 失败、缺绑定证据、未跑完、VERDICT=FAIL、槽位校验>0、含 [FAIL] 行、降级计数>0 却报 OK、缺版本证据 + require_compat、job 数不符，全部按期望判 FAIL；该自测已注册进 run_all_checks，机制失效会被每次交付拦住",
        "【P2·法线+凹凸叠加打通】不是加参数而是重做装配策略：开启 nb_overlay 后两层都进包装器——V-Ray 侧法线进 VRayNormalMap.normal_map、凹凸进其 bump_map，材质 texmap_bump 只挂包装器本体；Corona 侧法线进 CoronaNormal.normalMap、凹凸进 additionalBump，且必须同时置位 additionalBumpOn 并回读确认为 true（旧实现只写贴图、开关留默认 false，真机表现是「凹凸挂上了但完全没效果」却仍打印成功，属典型的写入成功≠生效）。强度分两层下发：法线强度归材质 bump_multiplier（节点无独立法线强度属性，日志显式声明该约束），凹凸强度归节点 bump_multiplier，两者不再被同一个含 mult 字样的循环一锅端",
        "【P3·跨语言类名硬校验】scripts/compat_check.py 新增中英别名覆盖检查：LANG_ALIASES 里登记的本地化注册名必须真的出现在模板候选链里，只写进文档不写进代码会直接 FAIL（第一版就抓出 VRay_材质 / Corona置换修改器 两个缺口，其中置换修改器的真实缺口是检查器扫描范围漏了 tpl_common.py）",
        "【P3·版本等级诚实性】MATRIX 中标 verified 的版本必须恰好等于本机基线且必须带 evidence 字段——「打算支持」写成「已验证」会被机器拦下。当前只有 Max 2024 / V-Ray 6 / Corona 12 三个 verified，其余为 supported / untested",
        "【P4·目视取证管线】新增 scripts/gen_visual_probe.py：生成可在 Max 里一键跑的 visual_probe.ms（固定相机+主光+受光立方体+深色背景，720×540，vfb:off 不弹窗），逐个渲出色散 / 法线凹凸叠加的 PNG，并把待目视项写进 visual_manifest.json（passed 初值必须为 null，未目视就填 true 视为伪造证据）。scripts/visual_gen_check.py 校验生成物结构与 GBK 编码，已注册进 run_all_checks",
        "【P5·3ds Max 版本适配】材质库保存改双向降级：先 saveMaterialLibrary（2022+），失败再 saveTempMaterialLibrary（2021 及更早）；preflight 的「已移除接口」规则同步细分——newMaterialLibrary / loadTempMaterialLibrary 仍判 ERROR，saveTempMaterialLibrary 允许保留但必须出现在新接口之后作为第二级降级，顺序反了判 ERROR",
        "【P6·V-Ray 版本适配】V-Ray 材质创建改为「Material.classes 字符串精确比对（VRayMtl / VRay_Mtl / VRay_材质）→ execute 英文名」，命中即打印实际类名；类是类表命中还是 execute 求值失败，日志能直接区分，不再出现「静默降级成 PhysicalMaterial 但日志一切正常」",
        "【P7·Corona 版本适配】新增 core/compat.py 作为版本矩阵唯一事实源（Max 2021–2026 / V-Ray 5–7 / Corona 9–13，每项标 verified / supported / untested 与 features），生成 docs/version-matrix.md；探测段 @@COMPATPROBE@@ 注入每份 .ms，运行时输出 MAX_VERSION / MAX_LANG / ACTIVE_RENDERER / VRAY_AVAILABLE / CORONA_AVAILABLE / COMPAT_LEVEL，日志里能直接看出「在什么版本上跑的、跑到哪个能力等级」",
        "【能力降级可见化】新增 mf_DEGRADED 计数与 mfDegrade 函数：版本能力缺失（如旧 Corona 无 additionalBumpOn）不再混进 OK，VERDICT 会输出 DEGRADED 并附降级条数；判据器同时校验「DEGRADED>0 却报 OK」为矛盾并判 FAIL",
        "实测：run_all_checks --fast 8 项全 PASS（evals / 导出隔离 / 注册表一致 / 贴图元信息 20/20 / 判据器自测 9/9 / 探针生成 / 兼容矩阵 17/17 / 目视取证 11/11）；本机 Max 未启动，全部为静态与生成侧验证",
    ],
    "0.10.0": [
        "【真机取证·第4轮】probeG：6 份材质（alu_brushed / marble / glass × corona / vray）逐份 fileIn 后按材质名 findMtlByName 回读，彻底修掉 probeE 阶段「medit 24 槽互相覆盖只能读到最后一槽」的取证缺陷。每份先清空槽再载入再回读，日志 452 行、0 error / 0 FAIL",
        "【贴图链路·真机证实】Corona：baseTexmap/baseRoughnessTexmap 为 Bitmaptexture、baseBumpTexmap 为「Corona法线」，内层 normalMap 正确绑定同名 normal 图；V-Ray：texmap_diffuse/texmap_roughness 为 Bitmaptexture、texmap_bump 为「VRay_法线贴图」，内层 normal_map 正确绑定。两侧每份材质挂图属性数 3（无图材质 0），链路完整",
        "【分层强度策略固化】强度只归外层槽（Corona baseBumpMapAmount / V-Ray texmap_bump_multiplier，实测 0.65 与 0.4 各自落值正确），内层包装器 multiplier 恒 1.0。若两层都写，降强度会被平方衰减，且回读只能看到一个值无法判断谁生效。已写入 ondevice_registry.json 的 texmap_chain_policy",
        "【色散拆参数】新增 dispersion_on 开关，与 dispersion 强度分离：开关关闭时一个色散属性都不下发，让渲染器保留自己的默认（真机实测 Corona dispersion=40 + dispersionEnable=false、VRayMtl refraction_dispersion=50 + refraction_dispersion_on=false）。此前 dispersion=5.0 无条件下发，等于把所有材质的色散从默认值改成了 5",
        "【量纲决策有证据】原来假设 V-Ray 色散是 0~1、Corona 是 0~100 需换算，实测两端默认值分别为 50 与 40、写入 0/0.02/0.2/1/5/40/100/1000 全部原值回读无钳制，判定为同一无量纲 0~100 标度，故取消换算，单值直接映射到各自属性名",
        "【静态自检补两类断言】_SPECS 新增「凹凸槽位」与「凹凸强度属性」：Corona 必须出现 baseBumpTexmap / baseBumpMapAmount，V-Ray 必须出现 texmap_bump / texmap_bump_multiplier，并把对端的名字列入已证伪名单，交叉写错直接判 error 而不是 warn",
        "【假通过再收口】继承 0.9.1 的 _strip_comments 去注释判定，新增断言同样只认去注释后的代码；负向用例（故意把 Corona 写成 texmap_bump、把 V-Ray 写成 baseBumpTexmap）实测均判 error，正向 16 份预设 ×2 渲染器 192 条断言全 ok、0 warn、0 error",
        "【文档同步缺陷修复】参数注册表 references/params-registry.md 补齐 dispersion_on 行，并修正此前未随拆参数更新的计数：表面组 26→27、合计 61→62、折叠区 56→57、进阶档 61→62。registry_check 由 FAIL 转 PASS",
        "实测：run_all_checks 6 项全 PASS（evals / 导出隔离 / 注册表文档一致 / 贴图元信息 / 主界面 / 纯色像素）；本机 Max 保持运行未强杀",
    ],
    "0.9.1": [
        "【真机核对】在本机 Corona + 3ds Max 2024 中文版上完成 CoronaPhysicalMtl 属性名核对（Material.classes 全枚举 + 148 项属性 dump + 端到端跑预设回读），CORONA_PROP_TARGETS 由「文档推断」转为「真机验证」",
        "【重大修正】Corona 类名同样会被中文版本地化：物理材质显示为「_Corona物理材质」，直接 execute \"CoronaPhysicalMtl()\" 在本机会失败（此前认为 Corona 类名是英文注册不用本地化探测，是错的）。材质创建改为「英文名 → 中文本地化名 → 按属性特征扫描 Material.classes」三级候选链，扫描判据是同时具备 baseRoughness 与 metalnessMode，与类名文本无关",
        "【语义修正】透明不再写 opacityLevel 而是 refractionAmount：真机确认 Corona 的透明是折射透射（refractionAmount 0-1），opacityLevel 只做 alpha 遮挡，此前把玻璃映射到 opacityLevel 会得到一块不透明灰块。端到端实测 glass 预设 refractionAmount=1.0、baseRoughness=0.02、baseIor=1.52 全部正确落值",
        "【语义修正】金属度只能二值化：物理材质没有标量金属度，只有 metalnessMode（整数枚举 0/1）与 metalnessTexmap，0.3/0.7 这类中间值无法表达，resolve_corona 改为按 0.5 阈值二值化；候选链剔除并不存在的 baseMetalness（实测 metal 预设 metalnessMode=1、baseIor=2.2 正确）",
        "【语义修正】色散两段式：真机确认 dispersion 默认 40 且需 dispersionEnable=true 才生效，新增 CORONA_PROP_ON_SWITCH 写入使能开关（glass 预设 disperson=5 + dispersionEnable=true 实测生效）",
        "【命名修正】按真机 dump 收紧属性名：opacityLevel、selfIllumColor/selfIllumLevel、sssAmount/sssRadius、baseAnisoRotation、refractionAmount；废弃文档推断的 refractIOR、levelOpacity、colorSelfIllum、levelSelfIllum、baseSss*",
        "【已知缺陷收口】物理材质无薄膜层（无任何 thinFilm* 属性），薄膜两项从透传与丢弃表摘除；V-Ray 侧 resolve_vray 同样不输出该键，属两端共同缺口，已在代码注释与 SKILL.md 标注",
        "实测：16 份 Corona 预设脚本重新生成，baseMetalness/thinFilm/refractIOR 残留计数均为 0，metalnessMode/refractionAmount/dispersionEnable 命中正确；metal 与 glass 两套 .ms 在真机 fileIn 无报错（6+7 项属性全部写入，0 FAIL）",
    ],
    "0.9.0": [
        "【新能力】导出目标可切换：新增 renderer 参数（vray / corona），Corona 侧生成 CoronaPhysicalMtl 脚本，创建失败时按 CoronaPhysicalMtl → CoronaLegacyMtl → PhysicalMaterial 逐级降级，与 V-Ray 侧的多级兜底对称",
        "【翻译层】新增 resolve_corona：光泽度→baseRoughness 反向、透明度→baseOpacity、coat/sheen/SSS/薄膜/色散/各向异性按 base* 候选取名，语义键与 V-Ray 侧共用同一套 SSOT，真实属性名由 CORONA_PROP_TARGETS 候选链解析",
        "【正确性】金属色回灌基色：V-Ray 把金属色放在 reflectionColor，Corona 只有一个 baseColor 承接，不回灌会让铜/铝预设变成白金属。触发条件不只看 metal_mode（预设里存在 metalness=1.0 且不开开关的组合），实测 alu_brushed (0.847,0.854,0.865)、alu_frosted (0.835,0.825,0.794) 正确成色",
        "【正确性】丢弃项改为全集求差：原实现只遍历白名单，凡是「不在白名单又没被映射」的键会静默消失（实测漏掉未消费的 reflectionColor / refractionColor）。现在对语义键全集求差，并过滤语义等价项（brdf_useRoughness / doubleSided / 反射折射深度 / energyPreservation 等）不刷屏",
        "【缺陷修复】Corona 无依赖使能开关：新增 CORONA_PROP_ON_SWITCH / CORONA_PROP_FOLLOW 空表，修复此前把 refraction_dispersion_on、thinFilm_on 写到 CoronaPhysicalMtl 上的属性不存在 FAIL（Corona 侧色散只有数值、薄膜由 IOR+厚度直接生效）",
        "【节点适配】法线经 CoronaNormal 包装、AO 用 CoronaAO 叠加漫反射、置换修改器按当前渲染器优先的候选链创建（CoronaDisplacementMod ⇄ VRayDisplacementMod）",
        "【隔离】导出目录按渲染器分开，修复材质名本身以 _corona 结尾时被拼成 xxx_corona_corona；同名材质分别导两套不再互相覆盖 .ms 与贴图",
        "【界面】新增目标渲染器切换段：参数面板标题、导出结果卡文案、导出前自检的环境段随目标渲染器切换；本机未装 Corona 只给 WARN 不阻断（脚本自带降级）",
        "实测：V-Ray / Corona 两套脚本静态自检 ok=True、阻断 0；HTTP 全链路 upload→derive→export(vray)→export(corona) 通过，Corona 脚本中 V-Ray 专有属性残留为 0",
    ],
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
