"""参数注册表 —— 单一事实来源 (SSOT)

设计目标（对应 Q4「给后续参数留端口」）：
    1. 新增一个材质参数，只需在 PARAMS 里追加一条 Param，前端面板自动渲染、
       后端导出自动映射，无需改动 exporters / app.js。
    2. 每个参数自带多目标映射（vray / three），未来接 Arnold、Corona、.vrmat
       只需新增一个映射字典 + 一个 exporter，不动参数定义。
    3. 支持 extra_vray_props 透传：任意未注册的 VRayMtl 属性可直接写入，
       导出端用 try/catch 包裹，属性不存在不会中断脚本。
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Param:
    id: str                          # 唯一标识
    label: str                       # 中文显示名
    group: str                       # 所属分组
    ptype: str                       # bool | enum | range | float | int | color
    default: Any
    options: Optional[list] = None   # enum: [{value,label,...}]
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: str = ""
    help: str = ""
    # ---- 多目标映射端口 ----
    vray_on: dict = field(default_factory=dict)   # 勾选/命中时写入的 MAXScript 属性
    vray_off: dict = field(default_factory=dict)  # 取消时写入的 MAXScript 属性
    vray_map: dict = field(default_factory=dict)  # 枚举值 -> 属性值
    # 枚举值 -> 一组属性（用于「一个档位同时改好几个通道」的场景，
    # 例如涂层光泽分层：档位一改，反射色阶 + 反射光泽度 + BRDF 通道要一起变）
    vray_map_multi: dict = field(default_factory=dict)
    vray_prop: Optional[str] = None               # 直接数值参数对应的属性名
    vray_as: Optional[str] = None                 # 标量写出形态：None=原值，"color"=按灰度转 color
    enable_by: Optional[str] = None               # 仅在另一开关为 True 时才写入属性
    three: dict = field(default_factory=dict)     # WebGL 预览映射
    depends: Optional[dict] = None                # {"param_id": value} 条件显示


# ============================================================
# 参数定义
#   新增参数：往 PARAMS 追加一条 Param 即可，前后端自动跟随
# ============================================================

PARAMS: list[Param] = [

    # ---------------- 基础 ----------------
    Param(
        id="base_color", label="基础颜色", group="基础", ptype="color", default="#ffffff",
        help="对漫反射贴图整体染色，白=保持原色；导出时把染色烘进 albedo 贴图，Max 里所见即所得",
        three={"color": "value"},
    ),
    Param(
        id="metal_mode", label="金属模式", group="基础", ptype="bool", default=False,
        help="勾选后按金属 PBR 处理：漫反射压暗、反射升满、由 albedo 驱动金属度",
        vray_on={"metalness": 1.0, "brdf_useRoughness": 0, "diffuse": (0.35, 0.35, 0.35)},
        vray_off={"metalness": 0.0, "brdf_useRoughness": 0},
        three={"metalness": 1.0},
    ),
    Param(
        id="metal_tint", label="金属反射染色", group="基础", ptype="color", default="#ffffff",
        depends={"metal_mode": True}, enable_by="metal_mode",
        help="真实金属的反射是带色偏的：球磨铝偏暖灰、铜偏橙、钛偏冷灰。白=不染色（按反射强度灰度写）。"
             "导出时与「反射强度」相乘写成 reflectionColor，不改漫反射",
        vray_prop="reflectionColor",
        vray_as="hex",
        three={"metalTint": "value"},
    ),
    Param(
        id="rough_level", label="粗糙度档位", group="基础", ptype="enum", default="semi",
        options=[
            {"value": "matte",  "label": "哑光",  "roughness": 0.85},
            {"value": "semi",   "label": "半哑光", "roughness": 0.55},
            {"value": "glossy", "label": "亮面",  "roughness": 0.28},
            {"value": "mirror", "label": "镜面",  "roughness": 0.05},
        ],
        help="替代手输数值：勾选档位即写入 VRayMtl 的 roughness",
        vray_prop="roughness",
        vray_map={"matte": 0.85, "semi": 0.55, "glossy": 0.28, "mirror": 0.05},
        three={"roughness": "option.roughness"},
    ),
    Param(
        id="reflection_on", label="开启反射", group="基础", ptype="bool", default=True,
        vray_on={"reflectionColor": (1, 1, 1), "reflection_maxDepth": 5},
        vray_off={"reflectionColor": (0, 0, 0)},
        three={"reflection": True},
    ),
    Param(
        id="fresnel_ior", label="菲涅尔 IOR", group="基础", ptype="range", default=1.55,
        min=1.0, max=3.0, step=0.01,
        help="非金属约 1.45~1.6，玻璃 1.5，钻石 2.4",
        vray_prop="brdf_ior",
        three={"ior": "value"},
    ),

    # ---------------- UV ----------------
    Param(
        id="uv_tiling", label="UV 平铺", group="UV", ptype="range", default=1.0,
        min=0.1, max=20.0, step=0.1,
        help="贴图重复次数：地砖 4~8、木地板长边 2~4。导出写入位图 coordinates.U/V_Tiling，预览同步生效",
        vray_prop="__uv_tiling__",
        three={"repeat": "value"},
    ),
    Param(
        id="uv_rotation", label="UV 旋转", group="UV", ptype="range", default=0.0,
        min=0.0, max=180.0, step=1.0, unit="°",
        help="贴图旋转角度：木纹走向、斜铺地砖。导出换算弧度写入位图坐标，预览同步生效",
        vray_prop="__uv_rotation__",
        three={"rotation": "value"},
    ),

    # ---------------- 细节 ----------------
    Param(
        id="normal_on", label="法线贴图", group="细节", ptype="bool", default=True,
        help="由高度图推导法线，产生表面起伏",
        vray_on={"texmap_bump": "__NORMAL__", "bump_multiplier": 1.0},
        three={"normalMap": True},
    ),
    Param(
        id="normal_strength", label="法线强度", group="细节", ptype="range", default=1.0,
        min=0.0, max=3.0, step=0.05,
        depends={"normal_on": True},
        vray_prop="bump_multiplier",
        three={"normalScale": "value"},
    ),
    Param(
        id="bump_on", label="凹凸贴图", group="细节", ptype="bool", default=False,
        help="用灰度高度图做凹凸，与法线二选一或叠加（需勾选贴图集的「高度」）",
        vray_on={"texmap_bump": "__HEIGHT__", "bump_multiplier": 0.6},
        three={"bumpMap": True},
    ),
    Param(
        id="bump_strength", label="凹凸强度", group="细节", ptype="range", default=0.6,
        min=0.0, max=3.0, step=0.05,
        depends={"bump_on": True},
        enable_by="bump_on",
        help="凹凸起伏强度，与法线强度各自独立调节",
        vray_prop="bump_multiplier",
        three={"bumpScale": "value"},
    ),
    Param(
        id="nb_overlay", label="法线+凹凸叠加", group="细节", ptype="bool", default=True,
        depends={"bump_on": True},
        help="法线与凹凸同时开启时，用 VRayNormalMap 把高度凹凸叠加到法线上；关闭则凹凸覆盖法线（旧行为）",
        three={"nbOverlay": True},
    ),
    Param(
        id="displacement_on", label="置换", group="细节", ptype="bool", default=False,
        help="真实几何位移，需模型有足够细分，导出时接 VRayDisplacementMod",
        vray_on={"texmap_displacement": "__DISPLACE__", "displacement_amount": 0.1},
        three={"displacement": True},
    ),
    Param(
        id="disp_amount", label="置换强度", group="细节", ptype="range", default=0.1,
        min=0.0, max=2.0, step=0.01,
        depends={"displacement_on": True}, enable_by="displacement_on",
        help="位移高度：1.0 = 100 个场景单位（mm 场景下约 10cm）；需模型有足够细分",
        vray_prop="displacement_amount",
        three={"displacementScale": "value"},
    ),
    Param(
        id="disp_subdiv", label="置换细分", group="细节", ptype="range", default=16,
        min=4, max=64, step=1,
        depends={"displacement_on": True}, enable_by="displacement_on",
        help="VRayDisplacementMod 细分面数；导出脚本会给当前选中物体加置换修改器",
        vray_prop="__disp_subdiv",
        three={},
    ),
    Param(
        id="ao_on", label="AO 环境遮蔽", group="细节", ptype="bool", default=True,
        help="把推导出的 AO 以 VRayDirt 方式叠加进漫反射",
        vray_on={"__activate_ao": True},
        three={"aoMap": True},
    ),

    # ---------------- 表面 ----------------
    Param(
        id="coat_on", label="清漆层", group="表面", ptype="bool", default=False,
        help="车漆 / 清漆木器效果",
        vray_on={"coat_amount": 1.0, "coat_ior": 1.5, "coat_glossiness": 0.9},
        three={"clearcoat": 1.0},
    ),
    Param(
        id="emis_on", label="自发光", group="表面", ptype="bool", default=False,
        vray_on={"selfIllumination": (1, 1, 1)},
        three={"emissive": 1.0},
    ),
    Param(
        id="refraction_on", label="折射/透明", group="表面", ptype="bool", default=False,
        help="玻璃、水、透明塑料",
        vray_on={"refractionColor": (1, 1, 1), "refraction_ior": 1.5, "refraction_maxDepth": 5},
        three={"transmission": 1.0},
    ),
    Param(
        id="anisotropy_on", label="各向异性", group="表面", ptype="bool", default=False,
        help="拉丝金属效果；强度与方向见下面两项",
        vray_on={"anisotropy": 0.7, "anisotropy_axis": 0.0},
        vray_off={"anisotropy": 0.0},
        three={"anisotropy": 0.7},
    ),
    Param(
        id="anisotropy_amount", label="各向异性强度", group="表面", ptype="range", default=0.7,
        min=0.0, max=1.0, step=0.05,
        depends={"anisotropy_on": True},
        enable_by="anisotropy_on",
        help="高光沿丝纹拉长的程度：0=各向同性（普通金属），0.6~0.8 接近拉丝铝，1=极端拉丝",
        vray_prop="anisotropy",
        three={"anisotropy": "value"},
    ),
    Param(
        id="anisotropy_axis", label="拉丝方向", group="表面", ptype="range", default=0.0,
        min=0.0, max=1.0, step=0.01,
        depends={"anisotropy_on": True},
        enable_by="anisotropy_on",
        help="0=横向(沿U)，0.5=斜45°，1=纵向(沿V)。导出写 VRayMtl 的 anisotropy_axis（0~1 对应 0~180°）",
        vray_prop="anisotropy_axis",
        three={"anisotropyRotation": "value"},
    ),
    Param(
        id="transparency_on", label="透明度", group="表面", ptype="bool", default=False,
        help="只做透光衰减，不产生折射扭曲；玻璃/水请用上面的「折射/透明」",
        vray_on={"refractionColor": (0.5, 0.5, 0.5), "refraction_maxDepth": 5},
        three={"opacity": True},
    ),
    Param(
        id="transparency_amount", label="透明度强度", group="表面", ptype="range", default=0.5,
        min=0.0, max=1.0, step=0.01,
        depends={"transparency_on": True},
        enable_by="transparency_on",
        help="0 = 完全不透明，1 = 全透明",
        vray_prop="refractionColor",
        vray_as="color",
        three={"opacity": "value"},
    ),

    # ---- P1：反射强度 / 独立反射光泽度 / 清漆粗糙度 ----
    Param(
        id="reflect_strength", label="反射强度", group="表面", ptype="range", default=1.0,
        min=0.0, max=1.0, step=0.01,
        depends={"reflection_on": True}, enable_by="reflection_on",
        help="反射颜色灰度：1 = 最强，压低等于整体削弱反射",
        vray_prop="reflectionColor",
        vray_as="color",
        three={"reflectStrength": "value"},
    ),
    Param(
        id="gloss_mode", label="独立反射光泽度", group="表面", ptype="bool", default=False,
        depends={"reflection_on": True},
        help="开启后改用 reflectionGlossiness 控制反射锐利度（brdf_useRoughness=0），不再由 roughness 反算",
        three={"glossMode": True},
    ),
    Param(
        id="reflection_glossiness", label="反射光泽度", group="表面", ptype="range", default=0.72,
        min=0.0, max=1.0, step=0.01,
        depends={"gloss_mode": True}, enable_by="gloss_mode",
        help="1 = 镜面反射，0.4 = 磨砂。抛光大理石 0.9~0.95，地砖 0.6，木地板 0.45",
        vray_prop="reflection_glossiness",
        three={"reflectGloss": "value"},
    ),
    Param(
        id="coat_roughness", label="清漆粗糙度", group="表面", ptype="range", default=0.10,
        min=0.0, max=1.0, step=0.01,
        depends={"coat_on": True}, enable_by="coat_on",
        help="0 = 晶面清漆，1 = 哑光；导出写 coat_glossiness = 1 - 该值",
        vray_prop="coat_glossiness",
        vray_as="gloss",
        three={"coatRough": "value"},
    ),

    # ---- P2：涂层光泽分层（乳胶漆 / 内墙漆的三档光泽）----
    Param(
        id="sheen_tier", label="涂层光泽分层", group="表面", ptype="enum", default="off",
        depends={"metal_mode": False},
        options=[
            {"value": "off",      "label": "不干预（用精细参数）"},
            {"value": "matte",    "label": "哑光 / 平光"},
            {"value": "eggshell", "label": "蛋壳光"},
            {"value": "satin",    "label": "丝光 / 半光"},
        ],
        help="乳胶漆按光泽度分档售卖的物理量：哑光≈2~5 度、蛋壳光≈10~20 度、丝光≈25~35 度。"
             "选中档位会一次性改写反射灰阶与反射光泽度（走 reflectionGlossiness 通道），"
             "优先级高于「反射强度 / 独立反射光泽度 / 粗糙度档位」；金属模式下属金属反射，不参与本档位",
        vray_map_multi={
            "matte":    {"reflectionColor": (0.10, 0.10, 0.10),
                         "reflection_glossiness": 0.35, "brdf_useRoughness": 0},
            "eggshell": {"reflectionColor": (0.22, 0.22, 0.22),
                         "reflection_glossiness": 0.52, "brdf_useRoughness": 0},
            "satin":    {"reflectionColor": (0.34, 0.34, 0.34),
                         "reflection_glossiness": 0.68, "brdf_useRoughness": 0},
        },
        three={"sheenTier": "value"},
    ),

    # ---- P2：折射光泽度 / 自发光颜色强度 / 透明度贴图 / 薄壁 ----
    Param(
        id="refraction_glossiness", label="折射光泽度", group="表面", ptype="range", default=1.0,
        min=0.0, max=1.0, step=0.01,
        depends={"refraction_on": True}, enable_by="refraction_on",
        help="1 = 清透玻璃，0.3 = 磨砂玻璃 / 喷砂石材",
        vray_prop="refraction_glossiness",
        three={"refractionGloss": "value"},
    ),
    Param(
        id="emis_color", label="自发光颜色", group="表面", ptype="color", default="#ffffff",
        depends={"emis_on": True}, enable_by="emis_on",
        help="自发光颜色，染色在 selfIllumination 上",
        vray_prop="selfIllumination",
        vray_as="hex",
        three={"emissive": "value"},
    ),
    Param(
        id="emis_strength", label="自发光强度", group="表面", ptype="range", default=1.0,
        min=0.0, max=10.0, step=0.1,
        depends={"emis_on": True}, enable_by="emis_on",
        help="自发光倍率（属性不存在时导出端只记日志，不中断脚本）",
        vray_prop="selfIllumination_multiplier",
        three={"emissiveIntensity": "value"},
    ),
    Param(
        id="opacity_on", label="透明度贴图", group="表面", ptype="bool", default=False,
        help="用亮度遮罩做镂空（镂空砖 / 格栅 / 铁丝网），需勾选贴图集的「透明度」",
        vray_on={"texmap_opacity": "__OPACITY__"},
        three={"opacityMap": True},
    ),
    Param(
        id="opacity_cutoff", label="镂空阈值", group="表面", ptype="range", default=0.5,
        min=0.0, max=1.0, step=0.01,
        depends={"opacity_on": True}, enable_by="opacity_on",
        help="越大镂空越多；导出时按此阈值重新烘焙透明度贴图",
        three={},
    ),
    Param(
        id="thin_wall_on", label="薄壁玻璃", group="表面", ptype="bool", default=False,
        help="单面片 / 薄壁玻璃：关闭 doubleSided，避免厚玻璃的二次折射",
        vray_on={"doubleSided": False},
        vray_off={"doubleSided": True},
        three={"thinWall": True},
    ),

    # ---- P2：六项 V-Ray 进阶通道（绒面 / 薄膜 / 色散 / 能量守恒 / 反射与折射深度）----
    # 为什么集中放在这里：这六项都直接落在 VRayMtl 的原生 rollout 上，
    # 不参与贴图烘焙，也不改预览观感的 PBR 语义，属于「导出即可用」的工艺开关。
    Param(
        id="sheen_layer_on", label="绒面 Sheen 层", group="表面", ptype="bool", default=False,
        help="布料 / 地毯 / 麂皮的边缘绒光层（VRayMtl 的 Sheen rollout）。"
             "与上面的「涂层光泽分层」不是一回事：那个是漆面整体光泽档位，这个是掠射角的织物绒感",
        vray_on={"sheen_amount": 0.8, "sheenColor": (1, 1, 1), "sheenGlossiness": 0.45},
        three={"sheenLayer": True},
    ),
    Param(
        id="thin_film_on", label="薄膜干涉", group="表面", ptype="bool", default=False,
        help="VRayMtl 的 Thin film 层：镀膜玻璃、阳极氧化铝、水面油膜的彩虹色。开关打开即写入基础厚度",
        vray_on={"thinFilm_on": True, "thinFilmThickness": 350, "thinFilmIOR": 1.45},
        three={"thinFilm": True},
    ),
    Param(
        id="thin_film_nm", label="薄膜厚度（nm）", group="表面", ptype="range", default=350,
        min=100.0, max=1200.0, step=10.0,
        depends={"thin_film_on": True}, enable_by="thin_film_on",
        help="决定彩虹偏哪个色：300~400nm 偏蓝紫、450~550nm 偏黄绿、600nm 以上偏红铜。"
             "阳极氧化铝常用 200~400nm，水面油膜 300~500nm",
        vray_prop="thinFilmThickness",
        three={"thinFilmNm": "value"},
    ),
    Param(
        id="dispersion_on", label="色散开关", group="表面", ptype="bool", default=False,
        depends={"refraction_on": True}, enable_by="refraction_on",
        help="色散要独立开关，不能靠「强度填 0」兼职关闭：真机核对（2026-11-19）"
             "Corona 的 dispersion 默认 40、V-Ray 的 refraction_dispersion 默认 50，"
             "而我们一旦下发一个数值就会覆盖渲染器原本的默认值。"
             "所以开关关闭时一个色散属性都不写，让渲染器保持默认（两端默认都是关闭态）；"
             "只有勾选本项，才下发强度值并打开使能（V-Ray: refraction_dispersion_on / "
             "Corona: dispersionEnable）。",
    ),
    Param(
        id="dispersion", label="色散强度", group="表面", ptype="range", default=5.0,
        min=0.0, max=100.0, step=0.5,
        depends={"refraction_on": True}, enable_by="refraction_on",
        help="玻璃的色散强度（对应阿贝数反量：0 = 无色散，5 普通玻璃，15+ 水晶 / 棱镜火彩）。"
             "只有开折射且勾选「色散开关」才生效，越大渲染越慢。"
             "量纲已在真机核对（2026-11-19）：V-Ray 侧属性真名是 refraction_dispersion，"
             "默认 50，实测写 0 / 0.5 / 1 / 5 / 50 / 100 均原值回读、无钳制；"
             "Corona 侧属性名 dispersion，默认 40、写入 -10~1000 均不被钳制。"
             "两端量纲一致（均为无量纲强度，量级 0~100），故本参数统一为 0~100 单值下发，"
             "由 exporter 分别映射到各自属性名，并各自打开使能开关"
             "（V-Ray: refraction_dispersion_on / Corona: dispersionEnable）",
        # 键名必须是语义键 "dispersion"（= resolve_* 的字典键），不能填真实属性名
        # refraction_dispersion —— 真实属性名由 ms_exporter 的候选表按渲染器映射
        # （V-Ray: refraction_dispersion / Corona: dispersion）。2026-11-19 端到端回归：
        # 曾把此处置为 refraction_dispersion，导致 Corona 侧 _CORONA_PASSTHROUGH
        # 找不到 "dispersion" 键，玻璃色散被静默丢弃（0 FAIL 但值没落）。
        vray_prop="dispersion",
        three={"dispersion": "value"},
    ),
    Param(
        id="energy_preserve", label="能量守恒 + 修复暗边", group="表面", ptype="bool", default=False,
        help="同时写入 energyPreservation 与 fixDarkEdges：石材 / 玻璃在粗糙度高时不再发黑发灰，"
             "掠射角边缘也不会出现那道脏黑边。属性名不存在的 V-Ray 版本只记日志，不中断脚本",
        vray_on={"energyPreservation": True, "fixDarkEdges": True},
        three={"energyPreserve": True},
    ),
    Param(
        id="reflection_depth", label="反射深度", group="表面", ptype="int", default=5,
        min=1, max=20, step=1,
        depends={"reflection_on": True}, enable_by="reflection_on",
        help="反射光线追踪次数：室内 5 够用，两面镜子对打 / 多反射金属场景提到 8~12，过高只增耗时",
        vray_prop="reflection_maxDepth",
        three={},
    ),
    Param(
        id="refraction_depth", label="折射深度", group="表面", ptype="int", default=5,
        min=1, max=20, step=1,
        depends={"refraction_on": True}, enable_by="refraction_on",
        help="折射光线追踪次数：单层玻璃 5，多层玻璃 / 水晶堆叠提到 8~10，过高明显拖慢",
        vray_prop="refraction_maxDepth",
        three={},
    ),

    # ---- P2：雾化 / 次表面散射 ----
    Param(
        id="fog_on", label="雾化（体积感）", group="进阶", ptype="bool", default=False,
        help="材质内部体积色，做大理石厚度感、玉石、蜡烛、树脂；需配合折射才明显",
        vray_on={"fogColor": (1, 1, 1), "fog_multiplier": 0.2},
        three={"fog": True},
    ),
    Param(
        id="fog_color", label="雾化颜色", group="进阶", ptype="color", default="#ffffff",
        depends={"fog_on": True}, enable_by="fog_on",
        help="体积雾色：玉石用淡青、蜡烛用暖黄、大理石用灰白",
        vray_prop="fogColor",
        vray_as="hex",
        three={"fogColor": "value"},
    ),
    Param(
        id="fog_amount", label="雾化浓度", group="进阶", ptype="range", default=0.2,
        min=0.0, max=1.0, step=0.01,
        depends={"fog_on": True}, enable_by="fog_on",
        help="浓度越高越不透光",
        vray_prop="fog_multiplier",
        three={"fogAmount": "value"},
    ),
    Param(
        id="sss_on", label="次表面散射 SSS", group="进阶", ptype="bool", default=False,
        help="透光石、薄木皮、纸灯：开启 translucency = SSS 模式",
        vray_on={"translucency": 3},
        three={"sss": True},
    ),
    Param(
        id="sss_amount", label="SSS 强度", group="进阶", ptype="range", default=0.5,
        min=0.0, max=1.0, step=0.01,
        depends={"sss_on": True}, enable_by="sss_on",
        vray_prop="sssAmount",
        three={"sssAmount": "value"},
    ),
    Param(
        id="sss_radius", label="SSS 半径", group="进阶", ptype="range", default=1.0,
        min=0.0, max=5.0, step=0.1,
        depends={"sss_on": True}, enable_by="sss_on",
        help="散射半径，越大越通透",
        vray_prop="sssRadius",
        three={"sssRadius": "value"},
    ),

    # ---------------- 质感（程序化微结构，不依赖图片） ----------------
    # 为什么单开一组：这些参数不写进 VRayMtl 任何通道，它们作用于
    # 贴图生成阶段（height / normal / roughness / albedo 的烘焙），
    # 是「纯色图也能出粗糙墙面」的唯一入口，必须与 V-Ray 参数分开表达。
    Param(
        id="proc_on", label="程序化微结构", group="质感", ptype="bool", default=False,
        help="不依赖上传图片，用内部噪声生成滚涂橘皮 / 喷涂哑面等微起伏与高光破碎。"
             "乳胶漆、艺术漆只需一张色卡或直接选色即可出粗糙质感",
        vray_on={}, vray_off={},
        three={"procOn": True},
    ),
    Param(
        id="proc_type", label="纹理类型", group="质感", ptype="enum", default="roller",
        options=[
            {"value": "roller", "label": "滚涂橘皮"},
            {"value": "spray",  "label": "喷涂雾面"},
            {"value": "sand",   "label": "细砂颗粒"},
            {"value": "stucco", "label": "拉毛肌理"},
        ],
        depends={"proc_on": True},
        help="滚涂=乳胶漆默认（中频团块+横向拉伸）；喷涂=均匀细哑面；"
             "细砂=颗粒感明确；拉毛=起伏最大",
        three={"procType": "value"},
    ),
    Param(
        id="proc_scale", label="纹理尺寸", group="质感", ptype="range", default=1.0,
        min=0.2, max=6.0, step=0.1,
        depends={"proc_on": True}, enable_by="proc_on",
        help="数值越大纹理越粗（滚筒毛更长 / 涂层更厚），1.0 约为 1㎡ 墙面的真实橘皮尺度",
        three={"procScale": "value"},
    ),
    Param(
        id="proc_amount", label="起伏强度", group="质感", ptype="range", default=0.6,
        min=0.0, max=1.5, step=0.05,
        depends={"proc_on": True}, enable_by="proc_on",
        help="程序化高度场叠加量：0=完全跟随图片（纯色图即全平），"
             "0.5~0.8=乳胶漆，1.2+=拉毛/艺术漆。最终凹凸幅度仍由「法线强度」控制",
        three={"procAmount": "value"},
    ),
    Param(
        id="proc_rough", label="高光破碎", group="质感", ptype="range", default=0.55,
        min=0.0, max=1.0, step=0.05,
        depends={"proc_on": True}, enable_by="proc_on",
        help="粗糙度抖动幅度——乳胶漆「哑面微闪」的主要来源，比法线更决定质感。"
             "纯平墙面设 0 会立刻变塑料",
        three={"procRough": "value"},
    ),
    Param(
        id="proc_albedo", label="色斑起伏", group="质感", ptype="range", default=0.04,
        min=0.0, max=0.3, step=0.01,
        depends={"proc_on": True}, enable_by="proc_on",
        help="漫反射低频明暗斑驳，避免纯色漆「死板一块」。0.02~0.06 为宜，过大显脏",
        three={"procAlbedo": "value"},
    ),
    Param(
        id="proc_mask", label="区域遮罩", group="质感", ptype="range", default=0.6,
        min=0.0, max=1.0, step=0.05,
        help="2~3 种尺度叠加的低频遮罩：制造大块空间不匀（近似刷痕流挂、墙板接缝、局部脏污），让不同墙段不再是同一张纹理的复制粘贴。0=完全均匀；纯色/供图模式下同样生效（只压低频明暗与粗糙，不动原图细节）",
        three={"procMask": "value"},
    ),
    Param(
        id="proc_mask_scale", label="遮罩尺度（米）", group="质感", ptype="range", default=1.0,
        min=0.3, max=5.0, step=0.1,
        help="遮罩区块的真实尺寸，1.0≈1 米区块（按 UVW 平面贴图 1 UV 单位≈1 米估算）。越大区块越整（一段墙一个色号），越小越碎；UV 平铺过大时区块放不进一块贴图，会被压缩复读并自动削弱强度",
        three={"procMaskScale": "value"},
    ),
    Param(
        id="proc_seed", label="随机种子", group="质感", ptype="int", default=7,
        min=1, max=9999, step=1,
        help="换一个种子即换一张纹理（遮罩与程序化纹理一起换），同种子结果完全可复现；界面上的「换一版」按钮就是随机换种子",
        three={"procSeed": "value"},
    ),

    # ---------------- 色板校色 ----------------
    Param(
        id="tint_mode", label="染色模式", group="基础", ptype="enum", default="match",
        options=[
            {"value": "match",    "label": "校色匹配"},
            {"value": "multiply", "label": "乘法叠加"},
            {"value": "mono",     "label": "单色化"},
        ],
        help="校色匹配=结果主色等于色板选色（默认，推荐）；"
             "乘法叠加=旧算法，结果会比选色暗（保留兼容）；单色化=丢掉原图色相只留明暗",
        three={"tintMode": "value"},
    ),
    Param(
        id="tint_strength", label="染色强度", group="基础", ptype="range", default=1.0,
        min=0.0, max=1.0, step=0.05,
        enable_by="tint_mode",
        help="与旧乘法结果的混合比例：1=完全按选色校准，0=回到旧行为",
        three={"tintStrength": "value"},
    ),
    Param(
        id="tint_chroma", label="保留原图色偏", group="基础", ptype="range", default=0.35,
        min=0.0, max=1.0, step=0.05,
        depends={"tint_mode": "match"},
        help="花纹/木纹用 0.3~0.6 保住纹理里的色相变化；纯色漆设 0 得到彻底单色",
        three={"tintChroma": "value"},
    ),
]

# 预设：一键套用一组参数值
PRESETS: dict[str, dict] = {
    # 数值基准来源：业内通用 VRayMtl 材质速查表（室内材质参数大全 / 金属材质专题）
    # 换算关系：网参多为「反射灰阶 0~255 + 光泽度」，这里统一折成
    #   reflect_strength = 灰阶/255，reflection_glossiness = 光泽度原值（见 PRESET_EXTRAS）

    # 金属（不锈钢基准）：漫射近黑、反射 220、光泽 0.9、菲涅尔 IOR 取高值
    "metal":     {"metal_mode": True,  "rough_level": "glossy", "reflection_on": True,
                  "fresnel_ior": 2.20, "coat_on": False, "normal_on": True,
                  "normal_strength": 0.35, "bump_on": False, "ao_on": True,
                  "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 塑料：反射衰减 + 高光 0.85 + 光泽 0.95，薄清漆层给出塑料的「油亮边」
    "plastic":   {"metal_mode": False, "rough_level": "glossy", "reflection_on": True,
                  "fresnel_ior": 1.50, "coat_on": True, "normal_on": True,
                  "normal_strength": 0.25, "bump_on": False, "ao_on": True,
                  "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 水泥：反射几乎压到 0（灰阶 ~45），光泽差，靠强法线 + AO 出骨料颗粒
    "concrete":  {"metal_mode": False, "rough_level": "matte",  "reflection_on": True,
                  "fresnel_ior": 1.45, "coat_on": False, "normal_on": True,
                  "normal_strength": 1.80, "bump_on": False, "ao_on": True,
                  "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 木纹：清漆木器，反射灰 50~90、光泽 0.75~0.85，清漆粗糙度 0.20 出柔和漆膜高光
    "wood":      {"metal_mode": False, "rough_level": "semi",   "reflection_on": True,
                  "fresnel_ior": 1.52, "coat_on": True, "normal_on": True,
                  "normal_strength": 1.10, "bump_on": False, "ao_on": True,
                  "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 布料：基本不给反射（灰 0~16），质感全靠凹凸 + 高频法线
    "fabric":    {"metal_mode": False, "rough_level": "matte",  "reflection_on": True,
                  "fresnel_ior": 1.45, "coat_on": False, "normal_on": True,
                  "normal_strength": 2.10, "bump_on": True, "bump_strength": 0.85,
                  "ao_on": True, "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 皮革：反射深灰 35、光泽 0.65~0.75，清漆粗糙度调高做皮面油润感（不做镜面清漆）
    "leather":   {"metal_mode": False, "rough_level": "semi",   "reflection_on": True,
                  "fresnel_ior": 1.50, "coat_on": True, "normal_on": True,
                  "normal_strength": 1.45, "bump_on": False, "ao_on": True,
                  "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 瓷砖（白瓷基准）：反射亮灰 133~190、光泽 0.95 接近釉面，釉层用低粗糙清漆
    "tile":      {"metal_mode": False, "rough_level": "glossy", "reflection_on": True,
                  "fresnel_ior": 1.60, "coat_on": True, "normal_on": True,
                  "normal_strength": 0.70, "bump_on": False, "ao_on": True,
                  "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 玻璃：反射纯白 + 折射纯白 + IOR 1.5~1.517 + 折射光泽 1，不挂法线/凹凸
    # 色散要显式开（dispersion_on），否则不下发任何色散属性、保持渲染器默认关闭态
    "glass":     {"metal_mode": False, "rough_level": "mirror", "reflection_on": True,
                  "fresnel_ior": 1.52, "coat_on": False, "normal_on": False,
                  "normal_strength": 0.0, "bump_on": False, "ao_on": False,
                  "refraction_on": True, "refraction_glossiness": 1.0,
                  "dispersion_on": True, "dispersion": 5.0,
                  "transparency_on": False, "anisotropy_on": False, "emis_on": False},

    # ---- 地面三材质（追加在玻璃之后；前端顺序即此字典顺序）----
    # 木地板：光泽 0.75~0.85 折成 0.60（地板比家具漆更哑），纹理起伏靠法线，AO 加深板缝
    "wood_floor": {"metal_mode": False, "rough_level": "semi", "reflection_on": True,
                   "fresnel_ior": 1.50, "coat_on": True, "normal_on": True,
                   "normal_strength": 1.05, "bump_on": False, "ao_on": True,
                   "refraction_on": False, "transparency_on": False,
                   "anisotropy_on": False, "emis_on": False},
    # 地砖：亮面砖体，光泽 0.78，弱法线，无清漆
    "floor_tile": {"metal_mode": False, "rough_level": "glossy", "reflection_on": True,
                   "fresnel_ior": 1.58, "coat_on": False, "normal_on": True,
                   "normal_strength": 0.85, "bump_on": False, "ao_on": True,
                   "refraction_on": False, "transparency_on": False,
                   "anisotropy_on": False, "emis_on": False},
    # 大理石：抛光晶面，光泽 0.9 以上、清漆提亮，法线很弱（石纹靠 albedo），雾化给厚度感
    "marble": {"metal_mode": False, "rough_level": "glossy", "reflection_on": True,
               "fresnel_ior": 1.60, "coat_on": True, "normal_on": True,
               "normal_strength": 0.40, "bump_on": False, "ao_on": True,
               "refraction_on": False, "transparency_on": False,
               "anisotropy_on": False, "emis_on": False},

    # ---- 墙面四材质 + 铝合金两材质（约定顺序：接在大理石之后）----
    # 壁纸：漫反射贴图 + 反射灰 30 + 反射光泽 0.5 + 凹凸 5~20（折成法线 1.30）
    "wallpaper": {"metal_mode": False, "rough_level": "semi", "reflection_on": True,
                  "fresnel_ior": 1.48, "coat_on": False, "normal_on": True,
                  "normal_strength": 1.30, "bump_on": False, "ao_on": True,
                  "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 墙布：比壁纸更哑、反射更低（灰 26），织物起伏靠高频法线 + 叠凹凸
    "wallcloth": {"metal_mode": False, "rough_level": "matte", "reflection_on": True,
                  "fresnel_ior": 1.45, "coat_on": False, "normal_on": True,
                  "normal_strength": 1.80, "bump_on": True, "bump_strength": 0.70,
                  "ao_on": True, "refraction_on": False, "transparency_on": False,
                  "anisotropy_on": False, "emis_on": False},
    # 乳胶漆：反射灰 23、高光 0.25 —— 哑面微粗糙、只有一层极淡的柔光
    # proc_*：乳胶漆的质感本来就不来自图片（用户只会给一张色卡），
    #   所以预设里直接把程序化微结构打开，滚涂橘皮 + 高光破碎，
    #   套用后即便没有任何图片也能出墙面质感。
    "latex_paint": {"metal_mode": False, "rough_level": "matte", "reflection_on": True,
                    "fresnel_ior": 1.45, "coat_on": False, "normal_on": True,
                    "normal_strength": 0.55, "bump_on": False, "ao_on": True,
                    "refraction_on": False, "transparency_on": False,
                    "anisotropy_on": False, "emis_on": False,
                    "proc_on": True, "proc_type": "roller", "proc_scale": 1.0,
                    "proc_amount": 0.55, "proc_rough": 0.60, "proc_albedo": 0.04,
                    "proc_mask": 0.60, "proc_mask_scale": 1.0,
                    "tint_mode": "match", "tint_chroma": 0.0},
    # 铝合金（磨砂）：反射灰 170、光泽 0.85，各向异性关闭，磨砂颗粒靠凹凸
    "alu_frosted": {"metal_mode": True, "rough_level": "semi", "reflection_on": True,
                    "fresnel_ior": 2.00, "coat_on": False, "normal_on": True,
                    "normal_strength": 0.95, "bump_on": True, "bump_strength": 0.55,
                    "ao_on": True, "refraction_on": False, "transparency_on": False,
                    "anisotropy_on": False, "emis_on": False},
    # 铝合金（拉丝）：光泽 0.92 + 各向异性 0.7，高光沿丝纹拉长
    "alu_brushed": {"metal_mode": True, "rough_level": "semi", "reflection_on": True,
                    "fresnel_ior": 2.05, "coat_on": False, "normal_on": True,
                    "normal_strength": 0.65, "bump_on": False, "ao_on": True,
                    "refraction_on": False, "transparency_on": False,
                    "anisotropy_on": True, "emis_on": False},
}

# 新手模式暴露的参数（61 项里只留 5 项）
# 取舍标准：这 5 项是「不懂 V-Ray 也能判断好坏」的观感旋钮；
# 其余 56 项属于工艺细节，一次性平铺只会把新手劝退：25 项进中级档，31 项留进阶档折叠区。
BEGINNER_IDS: list[str] = [
    "base_color",       # 颜色：最直观
    "metal_mode",       # 金属/非金属：二选一，决定材质大类
    "rough_level",      # 粗糙度档（哑光/半光/高光）：比数字更易懂
    "normal_strength",  # 法线强度：控制表面起伏强弱
    "uv_tiling",        # UV 平铺：地面/墙面贴图重复次数，新手必踩
]

# 中级模式额外暴露的参数（三层暴露的第二层）
# 取舍标准：这层是「已经懂点 V-Ray、会照着网参微调」的人真正会动的项——
#   通道开关 + 决定观感的数值 + 倾向性强的工艺开关（清漆/各向异性/折射/染色）。
# 判断准则：一项如果「调了之后肉眼能看出差别、且不需要先理解 V-Ray 的采样机制」，
#   就属于这一层；需要理解置换细分、SSS 半径、薄膜干涉波长这类物理量才算得清的，留在高级。
INTERMEDIATE_IDS: list[str] = [
    "metal_tint",                  # 金属反射染色
    "tint_mode", "tint_strength",  # 贴图染色：拿到客户照片时最常用的一组
    "reflection_on", "fresnel_ior", "reflect_strength",
    "gloss_mode", "reflection_glossiness", "sheen_tier",
    "uv_rotation",                 # 贴图方向不对时必调
    "normal_on", "bump_on", "bump_strength", "nb_overlay",
    "ao_on",                       # 缝隙压暗：观感提升最明显的一档
    "coat_on", "coat_roughness",
    "refraction_on", "transparency_on",
    "anisotropy_on", "anisotropy_amount", "anisotropy_axis",
    "proc_on", "proc_type", "proc_amount",   # 程序化微结构：贴图不够脏时补
]


def tier_ids() -> dict[str, list[str]]:
    """三层参数暴露的完整划分（beg / inter / adv 互斥且全覆盖）

    为什么要在后端算好而不是前端过滤：
        前端的过滤条件一旦与 PARAMS 漂移，就会出现「某个参数三层都看不到」的静默丢失；
        在后端按 PARAMS 顺序求差集，保证三档并集恒等于全部参数，且顺序稳定。
    """
    beg = [p.id for p in PARAMS if p.id in set(BEGINNER_IDS)]
    inter = [p.id for p in PARAMS if p.id in set(INTERMEDIATE_IDS)]
    taken = set(beg) | set(inter)
    adv = [p.id for p in PARAMS if p.id not in taken]
    return {"beginner": beg, "intermediate": inter, "advanced": adv}

# 预设中文名（前端不再硬编码映射表，避免两边漂移）
PRESET_LABELS: dict[str, str] = {
    "metal": "金属", "plastic": "塑料", "concrete": "水泥", "wood": "木纹",
    "fabric": "布料", "leather": "皮革", "tile": "瓷砖", "glass": "玻璃",
    "wood_floor": "木地板", "floor_tile": "地砖", "marble": "大理石",
    "wallpaper": "壁纸", "wallcloth": "墙布", "latex_paint": "乳胶漆",
    "alu_frosted": "铝合金(磨砂)", "alu_brushed": "铝合金(拉丝)",
}

# 预设的专业补充值
# 为什么单独放一张表：上方 PRESETS 只表达「这本预设大致是什么」，
# 而要做到「点一下就出像样的材质」，必须把光泽度、清漆粗糙度、UV 平铺
# 这类决定观感的数值写死。默认值服务于通用场景，不该被当成材质语义。
PRESET_EXTRAS: dict[str, dict] = {
    # 全部走 gloss_mode（reflection_glossiness 通道），数值直接对应网参「光泽度」
    # metal 不锈钢：光泽 0.9 / 反射 220→1.0
    "metal":      {"gloss_mode": True, "reflection_glossiness": 0.90, "reflect_strength": 1.00},
    # plastic：光泽 0.95 折 0.92（V-Ray 下 0.95 以上易出噪点），反射衰减→0.90
    "plastic":    {"gloss_mode": True, "reflection_glossiness": 0.92, "reflect_strength": 0.90,
                   "coat_roughness": 0.04},
    # concrete：灰阶 45→0.18，光泽 0.15（网参「哑面石材」0.85 是有打磨面的，水泥更脏更哑）
    "concrete":   {"gloss_mode": True, "reflection_glossiness": 0.15, "reflect_strength": 0.18},
    # wood：灰阶 50~90→0.35，光泽 0.75~0.85 折 0.72（漆膜略哑）
    "wood":       {"gloss_mode": True, "reflection_glossiness": 0.72, "reflect_strength": 0.35,
                   "coat_roughness": 0.20},
    # fabric：灰阶 0~16→0.12，几乎无高光
    "fabric":     {"gloss_mode": True, "reflection_glossiness": 0.12, "reflect_strength": 0.12},
    # leather：灰阶 35→0.32，光泽 0.65~0.75 折 0.68
    "leather":    {"gloss_mode": True, "reflection_glossiness": 0.68, "reflect_strength": 0.32,
                   "coat_roughness": 0.35},
    # tile 白瓷：灰阶 133~190→0.75，光泽 0.95 折 0.88（釉面留一点柔光）
    "tile":       {"gloss_mode": True, "reflection_glossiness": 0.88, "reflect_strength": 0.75,
                   "coat_roughness": 0.08},
    # glass：反射纯白，清玻璃折射光泽 1.0
    "glass":      {"gloss_mode": True, "reflection_glossiness": 0.98, "reflect_strength": 1.00,
                   "refraction_glossiness": 1.0},
    # wood_floor：光泽 0.75~0.85 折 0.60（地板比家具漆哑、耐磨面层），长边平铺 2
    "wood_floor": {"gloss_mode": True, "reflection_glossiness": 0.60, "reflect_strength": 0.40,
                   "coat_roughness": 0.25, "uv_tiling": 2.0},
    # floor_tile：光泽 0.78，亮面砖规格小、平铺更密
    "floor_tile": {"gloss_mode": True, "reflection_glossiness": 0.80, "reflect_strength": 0.65,
                   "uv_tiling": 2.5},
    # marble：光泽 0.9 以上折 0.92，镜面清漆 + 淡雾化做石材厚度
    "marble":     {"gloss_mode": True, "reflection_glossiness": 0.92, "reflect_strength": 0.85,
                   "coat_roughness": 0.04,
                   "fog_on": True, "fog_amount": 0.08, "fog_color": "#eef2f4",
                   "uv_tiling": 1.5},
    # wallpaper：灰阶 30→0.16，反射光泽 0.5 折 0.45，整卷不重复
    "wallpaper":  {"gloss_mode": True, "reflection_glossiness": 0.45, "reflect_strength": 0.16,
                   "uv_tiling": 1.0},
    # wallcloth：灰阶 26→0.10，织物反射更散，光泽 0.30
    "wallcloth":  {"gloss_mode": True, "reflection_glossiness": 0.30, "reflect_strength": 0.10,
                   "uv_tiling": 1.0},
    # latex_paint：灰阶 23→0.10，高光 0.25 / 光泽 1 —— 反射极弱但高光柔，取 0.35 折中
    "latex_paint": {"gloss_mode": True, "reflection_glossiness": 0.35, "reflect_strength": 0.10,
                    "uv_tiling": 1.0, "sheen_tier": "matte"},
    # alu_frosted：灰阶 170→0.88，光泽 0.85 折 0.78（磨砂模糊感）
    "alu_frosted": {"gloss_mode": True, "reflection_glossiness": 0.78, "reflect_strength": 0.88,
                    "uv_tiling": 1.0, "metal_tint": "#f2efe6"},
    # alu_brushed：光泽 0.92 折 0.88，拉丝方向的细密平铺
    # anisotropy_amount 0.72 / axis 0.0 = 沿 U 横向拉丝（长边方向），需要竖纹把 axis 改 1.0
    "alu_brushed": {"gloss_mode": True, "reflection_glossiness": 0.88, "reflect_strength": 0.90,
                    "uv_tiling": 2.0, "metal_tint": "#f0f2f5",
                    "anisotropy_amount": 0.72, "anisotropy_axis": 0.0},
}


def preset_snapshots() -> dict[str, dict]:
    """把稀疏预设展开成「全部参数」的完整快照

    为什么要展开：
        过去套预设靠「默认值打底 + 预设覆盖」，隐含假设默认值恰好符合该材质；
        一旦默认值调整或新增参数，预设就会静默变质，且用户手动开过的开关
        （如 SSS / 雾化）可能残留到下一个预设上。
        展开成完整快照后，套用即整体替换，行为可预测、可 diff、可审阅。
    """
    dft = default_values()
    out: dict[str, dict] = {}
    for key, sparse in PRESETS.items():
        snap = dict(dft)
        snap.update(sparse)
        snap.update(PRESET_EXTRAS.get(key, {}))
        out[key] = snap
    return out


# 分组顺序（前端渲染顺序）
GROUP_ORDER = ["基础", "UV", "细节", "质感", "表面", "进阶"]


# ============================================================
# 对外接口
# ============================================================

def schema() -> dict:
    """给前端的面板描述（不含 MAXScript 内部映射细节）"""
    return {
        "groups": GROUP_ORDER,
        "params": [
            {
                "id": p.id, "label": p.label, "group": p.group, "ptype": p.ptype,
                "default": p.default, "options": p.options, "min": p.min,
                "max": p.max, "step": p.step, "unit": p.unit,
                "help": p.help, "depends": p.depends,
                "three": p.three,
            }
            for p in PARAMS
        ],
        "presets": PRESETS,
        # 完整快照：套预设 = 整体替换，不再依赖「默认值恰好合适」
        "preset_full": preset_snapshots(),
        "preset_labels": PRESET_LABELS,
        # 新手模式只露这几项（其余收进「进阶参数」折叠区）
        "beginner_ids": BEGINNER_IDS,
        "intermediate_ids": INTERMEDIATE_IDS,
        # 三层暴露的权威划分：前端只按这里切档，不再自己做集合运算
        "tiers": tier_ids(),
        "defaults": default_values(),
    }


def default_values() -> dict:
    return {p.id: p.default for p in PARAMS}


def by_id() -> dict:
    return {p.id: p for p in PARAMS}


def _clamp01(v, dft: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = dft
    return max(0.0, min(1.0, x))


def _apply_vray_as(p: Param, v):
    """按 vray_as 把标量转成目标形态

    color : 0~1 标量 -> 灰度 color
    gloss : 粗糙度 -> 光泽度（1 - 值），V-Ray 的 glossiness 语义
    hex   : "#rrggbb" -> color 三元组
    """
    if p.vray_as == "color":
        x = _clamp01(v)
        return (x, x, x)
    if p.vray_as == "gloss":
        return round(1.0 - _clamp01(v), 4)
    if p.vray_as == "hex":
        return _hex_rgb(v)
    return v


def _hex_rgb(v) -> tuple:
    """'#rrggbb' / '#rgb' -> 0~1 三元组，非法输入回落纯白"""
    s = str(v or "#ffffff").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (1.0, 1.0, 1.0)


def resolve_vray(values: dict) -> dict:
    """把用户勾选结果解析成 MAXScript 属性字典。

    返回形如 {"roughness": 0.55, "metalness": 0.0, "texmap_bump": "__NORMAL__", ...}
    特殊键：
        __DIFFUSE__ / __NORMAL__ / __HEIGHT__ / __ROUGHNESS__  贴图槽占位符，由 exporter 换成真实位图
        __activate_ao                     布尔开关，由 exporter 转成 VRayDirt 组合

    values["tex_enabled"]：贴图集勾选状态 {"albedo":True,"normal":True,...}。
        缺省视为全选（兼容旧调用）。未勾选的通道既不出贴图占位符，
        也视同其对应开关关闭，保证「预览看到的」与「导出的」完全一致。
    """
    tex_enabled = values.get("tex_enabled") or {}

    def tex_on(chan: str) -> bool:
        return bool(tex_enabled.get(chan, True))

    # 贴图通道未勾选 -> 相关开关降级为关闭
    ev = dict(values)
    if not tex_on("normal"):
        ev["normal_on"] = False
    if not tex_on("height"):
        ev["bump_on"] = False
        ev["displacement_on"] = False
    if not tex_on("ao"):
        ev["ao_on"] = False
    if not tex_on("opacity"):
        ev["opacity_on"] = False
    # 色散拆参数（2026-11-19 真机决策）：开关与强度分离，关掉时**一个色散属性都不下发**。
    #   旧写法让 dispersion=5.0 对所有材质无条件下发，等于把 V-Ray 默认 50 / Corona 默认 40
    #   一律改写成 5，属于"用户没动过的参数被静默改动"。
    if not ev.get("dispersion_on", False):
        ev["dispersion"] = None

    out: dict = {}
    for p in PARAMS:
        v = ev.get(p.id, p.default)
        if v is None:
            # 被上面的通道降级 / 色散门控显式置空 -> 不下发，保留渲染器默认
            continue
        if p.ptype == "bool":
            src = p.vray_on if v else p.vray_off
            out.update(src)
        elif p.ptype == "enum" and p.vray_map_multi:
            # 一档多写：档位同时决定多个通道（涂层光泽分层即此类）
            multi = p.vray_map_multi.get(v if v in p.vray_map_multi else "")
            if multi:
                out.update(multi)
        elif p.ptype == "enum" and p.vray_prop:
            key = v if v in p.vray_map else p.default
            out[p.vray_prop] = p.vray_map[key]
        elif p.vray_prop is not None:
            if p.enable_by and not ev.get(p.enable_by, False):
                continue
            if p.id == "metal_tint":
                # 染色不能直接覆盖 reflectionColor，要与反射强度相乘，统一放到后面合成
                continue
            out[p.vray_prop] = _apply_vray_as(p, v)

    # 贴图占位符：勾选才挂
    if tex_on("albedo"):
        out["texmap_diffuse"] = "__DIFFUSE__"
    if tex_on("roughness"):
        out["texmap_roughness"] = "__ROUGHNESS__"

    # 法线与凹凸共用 VRayMtl 的 bump 槽：凹凸优先，其次法线；都关则不写强度
    if ev.get("bump_on", False):
        out["bump_multiplier"] = float(ev.get("bump_strength", 0.6) or 0.0)
    elif ev.get("normal_on", True):
        out["bump_multiplier"] = float(ev.get("normal_strength", 1.0) or 0.0)
    else:
        out.pop("bump_multiplier", None)

    # 透明度强度以折射色灰度表达（不降低已开启「折射/透明」的透明度）
    if ev.get("transparency_on", False):
        amt = float(ev.get("transparency_amount", 0.5) or 0.0)
        amt = max(0.0, min(1.0, amt))
        cur = out.get("refractionColor", (0.0, 0.0, 0.0))
        cur_max = max(cur) if isinstance(cur, (tuple, list)) else float(cur or 0.0)
        c = max(cur_max, amt)
        out["refractionColor"] = (c, c, c)
        # 只做透光衰减：没有单独开折射时用 1.0 的 IOR，避免玻璃畸变
        if not ev.get("refraction_on", False):
            out["refraction_ior"] = 1.0
            out["refraction_maxDepth"] = 5

    # ---- P2：涂层光泽分层（哑光 / 蛋壳光 / 丝光）----
    # 档位是「整层光泽」的语义，一旦选中就必须压过精细参数，否则
    # 用户看到界面写着蛋壳光、导出却还是粗糙度反算的数值，属于静默失效。
    # 金属走的是反射色染色，不吃漆面光泽档位。
    tier = str(ev.get("sheen_tier") or "off")
    tier_on = tier != "off" and not bool(ev.get("metal_mode", False))

    # ---- P1：独立反射光泽度（统一走 reflectionGlossiness 通道）----
    # V-Ray 6 的 VRayMtl 没有独立 roughness 属性（真机 190 项实测），粗糙度必须
    # 反算成 reflection_glossiness 并强制 brdf_useRoughness=0，否则写进去静默丢失。
    # 同理 roughness 键必须无条件出栈，否则它会被映射成第二个 reflection_glossiness，
    # 造成同一属性写两次、后写的值把口径覆盖掉（v0.8.0 真机表现即此）。
    _raw_rough = out.pop("roughness", None)
    try:
        _rough = float(_raw_rough) if _raw_rough not in (None, "") else 0.55
    except (TypeError, ValueError):
        _rough = 0.55
    _rough = max(0.0, min(1.0, _rough))

    out["brdf_useRoughness"] = 0
    if tier_on:
        # 档位（vray_map_multi）已写入 reflection_glossiness，这里只做通道保证与兜底
        out["reflection_glossiness"] = _clamp01(out.get("reflection_glossiness", 0.35), 0.35)
    elif ev.get("gloss_mode", False) and ev.get("reflection_on", True):
        out["reflection_glossiness"] = _clamp01(
            ev.get("reflection_glossiness", 1.0 - _rough), 1.0 - _rough)
    else:
        out["reflection_glossiness"] = _clamp01(1.0 - _rough)

    # ---- P2：金属反射染色（tint × 反射强度 -> reflectionColor）----
    # 真实金属的反射带色偏，但强度仍由「反射强度」控制，
    # 两者相乘才能既保住明暗层级又带出铜/铝的色相。
    if ev.get("metal_mode", False):
        g = _clamp01(ev.get("reflect_strength", 1.0), 1.0)
        tr, tg, tb = _hex_rgb(ev.get("metal_tint") or "#ffffff")
        out["reflectionColor"] = (round(g * tr, 4), round(g * tg, 4), round(g * tb, 4))

    # ---- P1：法线 + 凹凸叠加（exporter 用 VRayNormalMap 合成）----
    if (ev.get("normal_on", True) and ev.get("bump_on", False)
            and tex_on("normal") and tex_on("height")
            and ev.get("nb_overlay", True)):
        out["texmap_bump"] = "__NORMALBUMP__"
        out["bump_multiplier"] = float(ev.get("normal_strength", 1.0) or 0.0)
        # P2（0.11.0）：叠加链路需要「法线层强度」与「凹凸层强度」两个独立标量。
        # 旧实现只下发一个 __nb_bump_mult__，Max 侧再靠 "mult" 子串去猜属性名：
        # V-Ray 的 bump_multiplier 恰好含 mult 所以蒙对了，Corona 的 multiplier 含 mult
        # 但那是**法线强度**，于是凹凸强度把法线强度覆盖掉 —— 两层强度被压成一层。
        # 现在两个值分开传，由各渲染器模板精确落到自己的属性上。
        out["__nb_bump_mult__"] = float(ev.get("bump_strength", 0.6) or 0.0)
        out["__nb_nstr__"] = float(ev.get("normal_strength", 1.0) or 0.0)
        out["__nb_bstr__"] = float(ev.get("bump_strength", 0.6) or 0.0)
    elif (out.get("texmap_bump") == "__NORMAL__"
            and ev.get("normal_on", True) and tex_on("normal")):
        # ---- 仅法线（没有高度图）：同样走 VRayNormalMap，bump 子槽留空 ----
        # 旧行为会把 normal.png 直接塞进 VRayMtl.bump，V-Ray 会把法线 RGB 当高度用，
        # 产生杂乱凹凸。改为显式占位符，由 exporter 生成 VRayNormalMap(normal=法线,
        # bump=留空) 挂到 bump 槽，保证「凹凸槽只挂高度图」这一硬约束。
        out["texmap_bump"] = "__NORMALWRAP__"
        out["bump_multiplier"] = float(ev.get("normal_strength", 1.0) or 0.0)

    # ---- P1：UV 平铺 / 旋转（特殊键，exporter 转成位图坐标；旋转以角度传入）----
    try:
        tiling = float(ev.get("uv_tiling", 1.0) or 1.0)
    except (TypeError, ValueError):
        tiling = 1.0
    try:
        rot = float(ev.get("uv_rotation", 0.0) or 0.0)
    except (TypeError, ValueError):
        rot = 0.0
    try:
        out["__disp_amount"] = float(ev.get("disp_amount", 0.0) or 0.0)
    except (TypeError, ValueError):
        out["__disp_amount"] = 0.0
    out["__uv_tiling__"] = max(0.1, min(20.0, tiling))
    out["__uv_rotation__"] = max(0.0, min(180.0, rot))

    # 用户自定义透传（扩展端口）
    for k, v in (values.get("extra_vray_props") or {}).items():
        out[k] = v
    return out


# ======================================================================
#                     Corona 参数解析（V-Ray 语义 -> Corona 语义）
# ======================================================================
# 设计原则：所有「参数推导逻辑」（勾选项 -> 数值 / 开关联动 / 预设 / 贴图占位符）
# 与渲染器无关，已在 resolve_vray() 里实现且经过真机验证。Corona 只做一层**语义翻译**，
# 不重复实现推导，避免两套逻辑各自漂移。
#
# 翻译的三类差异：
#   1. 光泽度方向相反：V-Ray 的 reflection_glossiness（越大越光滑）
#      → Corona 的 roughness（越大越粗糙），即 rough = 1 - gloss。
#   2. 反射/折射染色合并：V-Ray 用 reflectionColor 给金属染色、refractionColor 给透明染色；
#      Corona 物理材质只有一个 baseColor 负责金属色，透明靠 baseOpacity + refractColor。
#      因此 reflectionColor 直接回灌 baseColor（金属模式下它才是真正的基色）。
#   3. V-Ray 专有项（fog / maxDepth / energyPreservation / brdf_useRoughness 等）
#      Corona 无对应，丢弃但**必须在脚本里显式声明**，不能让用户以为写进去了。

# 语义键直接透传（两边同名同义）
_CORONA_PASSTHROUGH = (
    "metalness", "brdf_ior", "anisotropy", "anisotropy_axis",
    "selfIllumination", "selfIllumination_multiplier",
    "dispersion",
    "coat_amount", "coat_ior",
    "sheen_amount", "sheenColor",
    "sssAmount", "sssRadius",
    "bump_multiplier", "diffuse",
)
# 不在此表：coat_glossiness / sheen_glossiness（Corona 分别用 clearcoatRoughness /
# sheenRoughness，需 1-x 反向，见步骤 2b）。
# 薄膜（thinFilm*）两端的表里都不写：CoronaPhysicalMtl 没有薄膜层通道（已按官方
# coronaConverter.ms 核对），V-Ray 侧 resolve_vray 当前也不输出该键，属两端共同的已知缺口。

# V-Ray 专有、Corona 无对应的键：丢弃并在导出脚本中逐项告知
_CORONA_DROPPED = (
    "brdf_useRoughness", "energyPreservation", "reflection_maxDepth",
    "refraction_maxDepth", "fogColor", "fog_multiplier", "doubleSided",
    "translucency", "option_doubleSided", "brdf_fixDarkEdges",
    "texmap_reflection", "texmap_refraction",
)

# 语义等价或影响可忽略的差异：Corona 端不需要替身，也不必写进告警刷屏
_CORONA_SILENT = {
    "brdf_useRoughness",     # Corona 直接用 roughness，没有这个开关
    "doubleSided", "option_doubleSided",   # Corona 无单/双面限制
    "reflection_maxDepth", "refraction_maxDepth",   # Corona 用全局反弹设置
    "energyPreservation", "brdf_fixDarkEdges",      # Corona 内部已处理
    "translucency",
    "refraction_ior", "refraction_glossiness",   # CoronaPhysicalMtl 只有一个 IOR 与一套粗糙度，
                                                  # 已分别由 brdf_ior / roughness 表达，无需重复写
    "thinFilm_on",          # 物理材质没有薄膜层通道（官方 coronaConverter.ms 中无 thinFilm* 属性）
}

# 丢弃项的中文名，写进 .ms 运行日志时用户能看懂是哪一项
_CORONA_DROP_LABELS = {
    "brdf_useRoughness": "粗糙度开关(brdf_useRoughness)",
    "energyPreservation": "能量守恒(energyPreservation)",
    "reflection_maxDepth": "反射最大深度(reflection_maxDepth)",
    "refraction_maxDepth": "折射最大深度(refraction_maxDepth)",
    "fogColor": "折射吸收色(fogColor)",
    "fog_multiplier": "折射吸收强度(fog_multiplier)",
    "doubleSided": "双面渲染(doubleSided)",
    "option_doubleSided": "双面渲染(option_doubleSided)",
    "translucency": "半透明(translucency)",
    "brdf_fixDarkEdges": "暗边修正(brdf_fixDarkEdges)",
    "texmap_reflection": "反射贴图(texmap_reflection)",
    "texmap_refraction": "折射贴图(texmap_refraction)",
    "reflectionColor": "反射色(reflectionColor)",
    "refractionColor": "折射色(refractionColor)",
}


def resolve_corona(values: dict) -> dict:
    """复用 resolve_vray 的推导结果，翻译成 Corona 语义的属性字典。

    返回的键是**语义键**（不是真实属性名），真实属性名由 ms_exporter 的
    CORONA_PROP_TARGETS 候选链解析，与 V-Ray 侧完全对称。

    两处不能照搬 V-Ray 的地方（都是「一个 Corona 属性要接住两个 V-Ray 属性」）：
      1) 金属色：V-Ray 把金属颜色放在 reflectionColor，Corona 只有 baseColor。
         若不回灌，铜/金这类预设会变成白金属（银色），这是最容易被用户一眼看穿的错。
      2) 有色玻璃：V-Ray 用 refractionColor 着色，Corona 的 baseColor 在折射分量上
         同时充当着色，所以透明时要把折射色写进基色。
    """
    sem = resolve_vray(values)
    out: dict = {}
    consumed: set = set()

    # ---- 1. 同名同义直接透传 ----
    for k in _CORONA_PASSTHROUGH:
        if k in sem:
            out[k] = sem[k]
            consumed.add(k)

    # ---- 2. 光泽度反向：glossiness -> roughness ----
    gloss = sem.get("reflection_glossiness")
    if gloss is not None:
        try:
            g = max(0.0, min(1.0, float(gloss)))
            out["roughness"] = round(1.0 - g, 4)
            consumed.add("reflection_glossiness")
        except (TypeError, ValueError):
            pass

    # ---- 2b. 涂膜 / 光泽绒毛：Corona 用 roughness 语义，需 1-x 反向 ----
    # resolve_vray 产出的键是 V-Ray 属性名 coat_glossiness / sheen_glossiness（1=光滑），
    # 而 CoronaPhysicalMtl 只有 clearcoatRoughness / sheenRoughness（0=光滑），
    # 直接透传会得到「越调越哑」的反向结果。两个属性名已按官方 coronaConverter.ms 核对。
    for _src, _dst in (("coat_glossiness", "coat_roughness"),
                       ("sheen_glossiness", "sheen_roughness")):
        _g = sem.get(_src)
        if _g is not None:
            try:
                out[_dst] = round(1.0 - max(0.0, min(1.0, float(_g))), 4)
                consumed.add(_src)
            except (TypeError, ValueError):
                pass

    # ---- 3. 透明度 -> refractionAmount（折射透射）----
    # 真机核对（本机 Corona + 3ds Max 2024 中文版）：CoronaPhysicalMtl 没有 V-Ray 那种
    # 「refractionColor 着色 + 透射」的双通道，透明只能靠 refractionAmount(0-1) 打开折射。
    # opacityLevel 只是 alpha 遮挡，写在玻璃上会得到一块不透明灰块 —— 两者不可混用。
    transmission = None
    if values.get("transparency_on", False):
        try:
            transmission = round(max(0.0, min(1.0,
                             float(values.get("transparency_amount", 0.5) or 0.0))), 4)
        except (TypeError, ValueError):
            transmission = None
    if transmission is None:
        rc = sem.get("refractionColor")
        if isinstance(rc, (tuple, list)) and rc:
            try:
                if float(max(rc)) > 0.001:
                    transmission = 1.0
                    consumed.add("refractionColor")   # 已用于透射，不再算丢失
            except (TypeError, ValueError):
                transmission = None
    if transmission is not None and transmission > 0.0:
        out["refraction_amount"] = transmission

    # ---- 4a. 金属色回灌基色 ----
    # 触发条件不只看 metal_mode：预设里存在 metalness=1.0 但不开 metal_mode 的组合，
    # 只看开关会漏掉它们，金属色就丢了。
    metalness = out.get("metalness")
    try:
        is_metal = bool(values.get("metal_mode", False)) or (
            metalness is not None and float(metalness) >= 0.5)
    except (TypeError, ValueError):
        is_metal = bool(values.get("metal_mode", False))
    rcol = sem.get("reflectionColor")
    if is_metal and isinstance(rcol, (tuple, list)) and len(rcol) == 3:
        out["diffuse"] = tuple(round(float(c), 4) for c in rcol)
        consumed.add("reflectionColor")

    # ---- 4b. 有色玻璃：透明且折射色非白时，基色用折射色 ----
    tcol = sem.get("refractionColor")
    if transmission is not None and transmission > 0.001 and isinstance(tcol, (tuple, list)) and len(tcol) == 3:
        try:
            if max(float(c) for c in tcol) < 0.999:
                out["diffuse"] = tuple(round(float(c), 4) for c in tcol)
                consumed.add("refractionColor")
        except (TypeError, ValueError):
            pass

    # ---- 4c. 金属度只能二值化 ----
    # 真机核对：CoronaPhysicalMtl 的金属度是 metalnessMode（0=非金属 / 1=金属，整数模式开关）
    # 加 metalnessTexmap，**没有标量金属度属性**。所以 0.3、0.7 这类中间值无法精确表达，
    # 只能按 0.5 阈值二值化；需要真实中间金属度只能靠金属度贴图（metallic 通道）。
    if "metalness" in out:
        try:
            out["metalness"] = 1.0 if float(out["metalness"]) >= 0.5 else 0.0
        except (TypeError, ValueError):
            out.pop("metalness", None)

    # ---- 5. 贴图占位符与控制键原样继承（exporter 按 Corona 槽位重新落位）----
    for k, v in sem.items():
        if k.startswith("__") or k.startswith("texmap_"):
            out[k] = v

    # ---- 6. 丢弃清单：只报「真的有损」的项 ----
    # 旧实现只遍历 _CORONA_DROPPED 白名单，凡是「不在白名单又没被映射」的键会静默消失
    # （实测漏掉未消费的 reflectionColor / refractionColor）。现在对 sem 全集求差，
    # 再用 _CORONA_SILENT 过滤掉语义等价或影响可忽略的项，避免每个材质都刷一长串告警。
    def _near_white(c) -> bool:
        try:
            return len(c) == 3 and all(float(x) >= 0.985 for x in c)
        except (TypeError, ValueError):
            return False

    def _near_black(c) -> bool:
        try:
            return len(c) == 3 and all(float(x) <= 0.02 for x in c)
        except (TypeError, ValueError):
            return True

    dropped: list = []
    for k, v in sem.items():
        if k in consumed or k.startswith("__"):
            continue
        if k.startswith("texmap_") and k not in _CORONA_DROPPED:
            continue          # 贴图槽已按 Corona 槽位重新挂好，不算丢弃
        if k in _CORONA_SILENT:
            continue          # 语义等价或无实际影响，不打扰用户
        # 有默认值的项：等于默认就是「没调过」，不用报
        if k in ("reflectionColor", "refractionColor") and _near_white(v):
            continue
        if k == "fogColor" and _near_black(v):
            continue
        if k == "fog_multiplier" and not v:
            continue
        dropped.append(_CORONA_DROP_LABELS.get(k, k))
    if dropped:
        out["__dropped__"] = dropped
    out["__renderer__"] = "corona"
    return out


def resolve(values: dict, renderer: str = "vray") -> dict:
    """按渲染器分派；renderer 只接受 vray / corona，其余按 vray 处理。"""
    if str(renderer).lower() == "corona":
        return resolve_corona(values)
    return resolve_vray(values)
