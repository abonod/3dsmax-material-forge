/* Material Forge — 前端主逻辑
 * 职责：参数面板动态渲染 / 贴图加载 / three.js 材质球实时预览 / 导出 .ms
 */
import * as THREE from "./vendor/three.module.js";

/* ==================================================================
 * 状态
 * ================================================================== */
const S = {
  schema: null,
  values: {},
  jobId: null,
  texUrls: {},
  textures: {},
  // 烘焙后的 albedo（颜色已烘进贴图）；为空表示预览仍靠 material.color 相乘
  bakedAlbedo: null,
  // 贴图集勾选状态：只有勾选的通道才会应用到材质球（并同步到导出）
  // 三通道（metalness/emissive/sheen）默认关闭：主流材质用不到，与后端 Param 默认一致
  texOn: { albedo: true, normal: true, roughness: true, height: true, ao: true, opacity: true,
           metalness: false, emissive: false, sheen: false },
  shape: "sphere",
  lightMode: "studio",
  spin: true,
  defaults: {},
  // 三档（v0.8.2）：新手 5 项 / 中级 30 项 / 进阶 61 项；数量由 schema.tiers 决定，勿写死
  mode: "beginner",
  advancedOpen: false,
  // 最近一次自检报告（导出拦截 / 手动自检都会刷新）
  lastReport: null,
  // 目标渲染器：vray / corona。只影响导出脚本形态与参数翻译，预览球两种都能看
  renderer: "vray",
  // 批量队列（0.12.0）：一次拖入多张图时不再吞掉后面的，全部排队等「批量导出」
  batchFiles: [],
  // 我的预设（0.12.0）：后端返回 {名字: {参数: 值}}，与内置 preset_full 结构一致
  userPresets: {},
};

const ROUGH_MAP = { matte: 0.85, semi: 0.55, glossy: 0.28, mirror: 0.06 };

const $ = (id) => document.getElementById(id);
const toastEl = $("toast");

function toast(msg, isErr = false) {
  toastEl.textContent = msg;
  toastEl.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (toastEl.className = "toast"), 2600);
}

/* ==================================================================
 * 会话状态持久化（0.12.0）
 *
 * 为什么必须做：调了 30 多项参数、按了导出，浏览器一刷新全部回默认值，
 * 只能从头再调一遍——这是用户群里反馈最多的一条。存 localStorage 而不是
 * sessionStorage：关掉标签页明天打开也该还在。
 *
 * 存什么、不存什么：
 *   存 参数值 / 通道勾选 / 预览形状 / 灯光档 / 目标渲染器 / 是否纯色模式；
 *   不存 上传的原图（几 MB 二进制塞 localStorage 会直接超 5MB 配额，
 *   而且配额塞满会让整个写入失败）。所以刷新后：纯色模式自动重建，
 *   图片模式只恢复参数并明确提示需要重新上传。
 * ================================================================== */
const STORE_KEY = "mf_state_v1";
let _saveTimer = 0;

function persistState() {
  try {
    const ids = new Set((S.schema?.params || []).map((p) => p.id));
    const values = {};
    for (const k of Object.keys(S.values || {})) {
      if (ids.size === 0 || ids.has(k)) values[k] = S.values[k];
    }
    localStorage.setItem(STORE_KEY, JSON.stringify({
      schemaVersion: S.schema?.version || "",
      values,
      texOn: S.texOn,
      shape: S.shape,
      lightMode: S.lightMode,
      renderer: S.renderer,
      solidMode: !!S.solidMode,
      savedAt: Date.now(),
    }));
  } catch (e) {
    // 隐私模式 / 配额满：持久化失败绝不能影响正常使用，静默跳过
  }
}

function scheduleSave() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(persistState, 400);
}

function readSavedState() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;   // 旧版本写坏的数据（或非 JSON）直接忽略，不让它卡住启动
  }
}

/** 把形状 / 灯光按钮的 active 状态对齐到 S（按钮点击才改 class，恢复时要手动补） */
function syncShapeLightUI() {
  const seg = (id, key, attr) => {
    const box = $(id);
    if (!box) return;
    box.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("active", b.dataset[attr] === key);
    });
  };
  seg("shapeSeg", S.shape, "shape");
  seg("lightSeg", S.lightMode, "light");
}

function restoreSavedState(saved) {
  if (!saved || typeof saved !== "object") return null;
  const ids = new Set((S.schema?.params || []).map((p) => p.id));
  for (const [k, val] of Object.entries(saved.values || {})) {
    if (ids.has(k)) S.values[k] = val;      // 只认当前版本存在的参数，旧键丢弃
  }
  if (saved.texOn && typeof saved.texOn === "object") {
    for (const [k, on] of Object.entries(saved.texOn)) {
      if (k in S.texOn) S.texOn[k] = on === true;
    }
  }
  if (typeof saved.shape === "string") S.shape = saved.shape;
  if (typeof saved.lightMode === "string") S.lightMode = saved.lightMode;
  if (saved.renderer === "vray" || saved.renderer === "corona") S.renderer = saved.renderer;
  S.values.base_color = normalizeHex(S.values.base_color || "#ffffff");
  return saved;
}

/* ==================================================================
 * three.js 场景
 * ================================================================== */
const vp = $("viewport");
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.outputColorSpace = THREE.SRGBColorSpace;
vp.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x101318);

const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
camera.position.set(0, 0.55, 3.9);

let mesh = null;
let material = null;
const pmrem = new THREE.PMREMGenerator(renderer);
pmrem.compileEquirectangularShader();
let envRT = null;

/* 环境光板：主光 + 轮廓光 + 补光，让金属有映照内容 */
const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
keyLight.position.set(3, 4, 3);
const rimLight = new THREE.DirectionalLight(0x8899ff, 1.1);
rimLight.position.set(-3, 2, -3);
const fillLight = new THREE.DirectionalLight(0xffffff, 0.5);
fillLight.position.set(0, -2, 2);
scene.add(keyLight, rimLight, fillLight);
scene.add(new THREE.AmbientLight(0xffffff, 0.18));

/* 程序化 equirect 环境贴图（无外部 HDR 依赖） */
function makeEnvTexture(mode) {
  const W = 1024, H = 512;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d");

  const g = ctx.createLinearGradient(0, 0, 0, H);
  if (mode === "outdoor") {
    g.addColorStop(0.00, "#9fc4ee");
    g.addColorStop(0.42, "#dfe9f4");
    g.addColorStop(0.52, "#7c848c");
    g.addColorStop(1.00, "#3a3d40");
  } else if (mode === "dark") {
    g.addColorStop(0.00, "#242830");
    g.addColorStop(0.48, "#1b1e24");
    g.addColorStop(0.52, "#0e1014");
    g.addColorStop(1.00, "#08090b");
  } else {
    g.addColorStop(0.00, "#eef2f8");
    g.addColorStop(0.40, "#aab3bf");
    g.addColorStop(0.52, "#565d67");
    g.addColorStop(1.00, "#22262c");
  }
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);

  // 灯斑
  const blot = (x, y, rx, ry, alpha) => {
    const rg = ctx.createRadialGradient(x, y, 0, x, y, Math.max(rx, ry));
    rg.addColorStop(0, `rgba(255,255,255,${alpha})`);
    rg.addColorStop(1, "rgba(255,255,255,0)");
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(rx / Math.max(rx, ry), ry / Math.max(rx, ry));
    ctx.translate(-x, -y);
    ctx.fillStyle = rg;
    ctx.beginPath();
    ctx.arc(x, y, Math.max(rx, ry), 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  };

  if (mode === "studio") {
    blot(230, 110, 170, 110, 0.95);
    blot(760, 130, 140, 95, 0.85);
    blot(500, 40, 240, 70, 0.6);
  } else if (mode === "outdoor") {
    blot(300, 120, 130, 90, 0.9);
    blot(820, 200, 90, 60, 0.35);
  } else {
    blot(180, 150, 120, 260, 0.75);
    blot(840, 120, 70, 200, 0.45);
  }

  const tex = new THREE.CanvasTexture(c);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function applyLightMode(mode) {
  S.lightMode = mode;
  if (envRT) envRT.dispose();
  const src = makeEnvTexture(mode);
  envRT = pmrem.fromEquirectangular(src);
  scene.environment = envRT.texture;
  src.dispose();

  if (mode === "dark") {
    scene.background = new THREE.Color(0x0b0d11);
    keyLight.intensity = 3.0; rimLight.intensity = 2.0; fillLight.intensity = 0.25;
  } else if (mode === "outdoor") {
    scene.background = new THREE.Color(0x1b2028);
    keyLight.intensity = 2.6; rimLight.intensity = 0.8; fillLight.intensity = 0.7;
  } else {
    scene.background = new THREE.Color(0x14171c);
    keyLight.intensity = 2.2; rimLight.intensity = 1.1; fillLight.intensity = 0.5;
  }
  if (material) material.envMapIntensity = mode === "dark" ? 1.35 : 1.0;
  if (material) material.needsUpdate = true;
}

/* ---------- 几何 ---------- */
function buildGeometry(shape) {
  switch (shape) {
    case "cube":
      return new THREE.BoxGeometry(1.7, 1.7, 1.7, 160, 160, 160);
    case "plane":
      return new THREE.PlaneGeometry(2.6, 2.6, 260, 260);
    case "torus":
      return new THREE.TorusKnotGeometry(0.85, 0.3, 400, 60, 2, 3);
    default:
      return new THREE.SphereGeometry(1.15, 200, 130);
  }
}

function applyShape(shape) {
  S.shape = shape;
  if (!material) return;
  if (mesh) {
    scene.remove(mesh);
    mesh.geometry.dispose();
  }
  mesh = new THREE.Mesh(buildGeometry(shape), material);
  scene.add(mesh);
  resize();
}

/* ---------- 材质 ---------- */
function ensureMaterial() {
  if (material) return material;
  material = new THREE.MeshPhysicalMaterial({
    color: 0xffffff, metalness: 0.0, roughness: 0.55,
    clearcoat: 0, clearcoatRoughness: 0.08,
    ior: 1.55, transmission: 0,
    envMapIntensity: 1.0,
    side: THREE.DoubleSide,
    transparent: false,
  });
  scene.add(new THREE.Mesh(buildGeometry(S.shape), material));
  mesh = scene.children.find((o) => o.isMesh);
  return material;
}

function setTexture(key, img) {
  const tex = new THREE.Texture(img);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy();

  if (key === "albedo") tex.colorSpace = THREE.SRGBColorSpace;
  else if (key === "normal") tex.colorSpace = THREE.NoColorSpace;
  else tex.colorSpace = THREE.NoColorSpace;

  if (key === "normal") {
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  }
  if (key === "ao") tex.channel = 0;
  tex.needsUpdate = true;
  S.textures[key] = tex;
}

function updateMaterial() {
  const m = ensureMaterial();
  const v = S.values;
  const T = S.textures;
  const on = S.texOn;

  // 基础颜色：颜色已经被后端烘进 albedo 贴图（albedo 里就是成品色），
  // 此时 material.color 必须回白，否则等于把颜色乘两遍——这正是过去
  // 「预览比选色暗一截、和导出对不上」的根因。未烘焙（旧流程）才回退相乘。
  const baseHex = normalizeHex(v.base_color || "#ffffff");
  m.color.set(S.bakedAlbedo ? "#ffffff" : baseHex);

  // ---- UV 平铺 / 旋转：作用于全部贴图，与导出端位图坐标一一对应 ----
  const tiling = Math.max(0.1, Math.min(20, Number(v.uv_tiling ?? 1) || 1));
  const rotDeg = Math.max(0, Math.min(180, Number(v.uv_rotation ?? 0) || 0));
  for (const key of Object.keys(T)) {
    const t = T[key];
    t.center.set(0.5, 0.5);
    t.repeat.set(tiling, tiling);
    t.rotation = (rotDeg * Math.PI) / 180;
    t.needsUpdate = true;
  }

  // ---- 贴图：通道勾选 && 对应参数开关 同时成立才应用 ----
  m.map = on.albedo ? T.albedo || null : null;
  m.roughnessMap = on.roughness ? T.roughness || null : null;
  m.aoMap = v.ao_on && on.ao ? T.ao || null : null;
  m.aoMapIntensity = 0.85;

  // 法线 / 凹凸：three 两者可共存，等价于导出端 VRayNormalMap 叠加
  m.normalMap = v.normal_on && on.normal ? T.normal || null : null;
  const ns = v.normal_strength ?? 1;
  if (m.normalMap) m.normalScale.set(ns * 0.85, ns * 0.85);

  const bs = v.bump_strength ?? 0.6;
  // 未开启叠加：凹凸覆盖法线（保持旧行为，便于对比叠加效果）
  if (v.bump_on && v.nb_overlay === false && on.height) m.normalMap = null;
  m.bumpMap = v.bump_on && on.height ? T.height || null : null;
  m.bumpScale = m.bumpMap ? 0.06 * bs : 0;

  // 置换
  const dispAmt = Math.max(0, Number(v.disp_amount ?? 0.1) || 0);
  m.displacementMap = v.displacement_on && on.height ? T.height || null : null;
  m.displacementScale = m.displacementMap ? 0.5 * dispAmt : 0;
  m.displacementBias = m.displacementMap ? -0.25 * dispAmt : 0;

  // 金属 / 粗糙：独立反射光泽度开启时用 1-gloss 反算，对应 V-Ray reflectionGlossiness
  m.metalness = v.metal_mode ? 1.0 : 0.0;
  // 0.12.0：有专用金属度贴图就用它（能表达 0~1 中间值与分布）；
  // 没有才退回旧行为——metal_mode 打开时拿 albedo 当金属遮罩。
  if (v.metalness_tex_on && on.metalness && T.metalness) {
    m.metalnessMap = T.metalness;
    m.metalness = 1.0;              // 模式给满，具体金属度由贴图值决定
  } else {
    m.metalnessMap = v.metal_mode && on.albedo ? T.albedo || null : null;
  }
  const baseRough = ROUGH_MAP[v.rough_level] ?? 0.55;
  const gloss = Math.max(0, Math.min(1, Number(v.reflection_glossiness ?? 0.72)));
  m.roughness = v.gloss_mode ? 1 - gloss : baseRough;

  // 涂层光泽分层：与 core/params.py 的 resolve_vray 同语义——档位优先于精细参数，
  // 金属不吃漆面档位。三档光泽值与后端 vray_map_multi 保持一致，避免预览和导出打架。
  const TIER_GLOSS = { matte: 0.35, eggshell: 0.52, satin: 0.76 };
  const tier = !v.metal_mode && v.sheen_tier && v.sheen_tier !== "off" ? v.sheen_tier : "";
  if (tier && TIER_GLOSS[tier] != null) m.roughness = 1 - TIER_GLOSS[tier];

  // Sheen 层：three 原生支持，直接映射 V-Ray sheen_amount / sheenColor / sheenGlossiness
  try {
    m.sheen = v.sheen_layer_on ? 0.8 : 0.0;
    if ("sheenColor" in m) m.sheenColor = new THREE.Color(normalizeHex(v.sheen_color || "#ffffff"));
    if ("sheenRoughness" in m) m.sheenRoughness = 1 - Math.max(0, Math.min(1, Number(v.sheen_gloss ?? 0.45)));
    // 0.12.0 绒面分布图：褶皱处更绒、压平处更亮
    if ("sheenColorMap" in m) {
      m.sheenColorMap = v.sheen_tex_on && on.sheen ? T.sheen || null : null;
    }
  } catch (e) { /* 老版本 three 无 sheen */ }

  // 反射强度：直接削弱环境反射能量
  const rs = Math.max(0, Math.min(1, Number(v.reflect_strength ?? 1)));
  m.envMapIntensity = v.reflection_on ? (S.lightMode === "dark" ? 1.35 : 1.0) * rs : 0.12;

  // IOR / 清漆 / 折射 / 透明度 / 各向异性
  m.ior = v.fresnel_ior ?? 1.55;
  m.clearcoat = v.coat_on ? 1.0 : 0.0;
  m.clearcoatRoughness = Math.max(0, Math.min(1, Number(v.coat_roughness ?? 0.1)));
  const ta = v.transparency_on
    ? Math.max(0, Math.min(1, Number(v.transparency_amount ?? 0.5)))
    : 0;
  m.transmission = v.refraction_on ? 1.0 : 0.0;
  m.opacity = v.transparency_on ? 1 - ta : 1;
  m.thickness = v.refraction_on ? 0.6 : 0;
  try {
    // 强度跟 anisotropy_amount，方向跟 anisotropy_axis（0~1 映射 0~π 弧度，与 V-Ray 0~180° 对齐）
    const anAmt = v.anisotropy_on
      ? Math.max(0, Math.min(1, Number(v.anisotropy_amount ?? 0.7)))
      : 0;
    m.anisotropy = anAmt;
    if ("anisotropyRotation" in m) {
      const anAxis = Math.max(0, Math.min(1, Number(v.anisotropy_axis ?? 0)));
      m.anisotropyRotation = anAxis * Math.PI;
    }
  } catch (e) { /* 老版本 three 无此属性 */ }

  // 雾化（体积感）：three 用 attenuationDistance 近似，雾色对应 V-Ray fogColor
  const fogOn = !!v.fog_on;
  const fogAmt = Math.max(0, Math.min(1, Number(v.fog_amount ?? 0.2)));
  try {
    m.attenuationColor = new THREE.Color(normalizeHex(v.fog_color || "#ffffff"));
    m.attenuationDistance = fogOn ? Math.max(0.05, 2.0 - 1.8 * fogAmt) : Infinity;
  } catch (e) { /* 老版本 three 无衰减属性 */ }

  // 雾吸收：浓度越高越不透光，软件渲染下也能直观看到变化
  if (fogOn && m.transmission > 0) {
    m.transmission = m.transmission * (1 - 0.55 * fogAmt);
  }

  // 次表面散射：用厚度 + 少量透射近似；放在雾吸收之后，保证雾同时开启时开关仍可感知
  if (v.sss_on) {
    const sa = Math.max(0, Math.min(1, Number(v.sss_amount ?? 0.5)));
    const sr = Math.max(0, Number(v.sss_radius ?? 1));
    m.thickness = (0.4 + 1.2 * sa) * Math.max(0.2, sr * 0.5);
    m.transmission = Math.max(m.transmission, 0.18 * sa);
  }

  // 折射光泽度：模糊折射由粗糙度承载
  if (v.refraction_on && !v.gloss_mode && !tier) {
    const rg = Math.max(0, Math.min(1, Number(v.refraction_glossiness ?? 1)));
    m.roughness = Math.max(m.roughness, (1 - rg) * 0.6);
  }


  // 透明度贴图（镂空）：alphaMap 读灰度，白 = 不透明
  m.alphaMap = v.opacity_on && on.opacity ? T.opacity || null : null;
  m.alphaTest = m.alphaMap ? 0.05 : 0;

  // 薄壁玻璃：单面渲染（球体内壁不再遮挡，接近薄壁玻璃观感）
  m.side = v.thin_wall_on ? THREE.FrontSide : THREE.DoubleSide;

  // 全局透明标记：折射 / 透明 / 镂空 / SSS 透射 任一成立
  m.transparent = !!v.refraction_on || (v.transparency_on && ta > 0) || !!m.alphaMap
    || (!!v.sss_on && m.transmission > 0);

  // 自发光
  // 0.12.0：有专用自发光贴图就用它（局部发光）；没有才退回拿 albedo 当发光遮罩
  m.emissiveMap = v.emis_on && v.emissive_tex_on && on.emissive && T.emissive
    ? T.emissive
    : (v.emis_on && on.albedo ? T.albedo || null : null);
  m.emissive = new THREE.Color(v.emis_on ? normalizeHex(v.emis_color || "#ffffff") : 0x000000);
  m.emissiveIntensity = v.emis_on ? Math.max(0, Number(v.emis_strength ?? 1)) * 0.35 : 0;

  m.needsUpdate = true;
  scheduleSave();          // 所有参数变更最终都会走到这里，一处挂载即可全覆盖

  const texCount = Object.values(on).filter(Boolean).length;
  const texTotal = Object.keys(on).length;   // 0.12.0：通道数已随三通道扩到 9，别再写死
  $("statLine").textContent =
    `贴图 ${texCount}/${texTotal} · UV ${tiling.toFixed(1)}×${tiling.toFixed(1)} / ${Math.round(rotDeg)}° · ` +
    `颜色 ${baseHex.toUpperCase()} · 粗糙 ${m.roughness.toFixed(2)} · ` +
    `光泽 ${v.gloss_mode ? gloss.toFixed(2) : "—"} · 凹凸 ${m.bumpMap || m.normalMap ? bs.toFixed(2) : "关"} · ` +
    `雾 ${fogOn ? `${fogAmt.toFixed(2)} ${String(v.fog_color || "#ffffff").toUpperCase()}` : "关"} · ` +
    `SSS ${v.sss_on ? "开" : "关"} · 折射光泽 ${v.refraction_on && !v.gloss_mode ? Number(v.refraction_glossiness ?? 1).toFixed(2) : "—"}`;
}

/* ---------- 交互：拖拽旋转 + 滚轮缩放 ---------- */
let dragging = false, lastX = 0, lastY = 0;
let rotX = 0.12, rotY = 0.5, targetRotX = 0.12, targetRotY = 0.5;
let camZ = 3.9;

renderer.domElement.addEventListener("pointerdown", (e) => {
  dragging = true; lastX = e.clientX; lastY = e.clientY;
  renderer.domElement.setPointerCapture(e.pointerId);
});
renderer.domElement.addEventListener("pointerup", (e) => {
  dragging = false;
  try { renderer.domElement.releasePointerCapture(e.pointerId); } catch (_) {}
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  targetRotY += (e.clientX - lastX) * 0.008;
  targetRotX += (e.clientY - lastY) * 0.008;
  targetRotX = Math.max(-1.45, Math.min(1.45, targetRotX));
  lastX = e.clientX; lastY = e.clientY;
});
renderer.domElement.addEventListener("wheel", (e) => {
  e.preventDefault();
  camZ = Math.max(1.9, Math.min(8, camZ + e.deltaY * 0.0022));
}, { passive: false });

/* ---------- 尺寸 ---------- */
function resize() {
  const w = vp.clientWidth || 1;
  const h = vp.clientHeight || 1;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
new ResizeObserver(resize).observe(vp);

/* ---------- 渲染循环 ---------- */
function animate() {
  requestAnimationFrame(animate);
  if (S.spin && !dragging) targetRotY += 0.0032;

  rotX += (targetRotX - rotX) * 0.1;
  rotY += (targetRotY - rotY) * 0.1;
  if (mesh) {
    mesh.rotation.x = rotX;
    mesh.rotation.y = rotY;
    mesh.rotation.z = 0;
  }
  camera.position.z += (camZ - camera.position.z) * 0.12;
  camera.lookAt(0, 0, 0);
  renderer.render(scene, camera);
  // 可测试性埋点：ui_check 借此确认预览球真的在出帧、真的画了三角形，
  // 而不是只挂着一块尺寸正常的空 canvas。
  window.__mfFrames = (window.__mfFrames || 0) + 1;
  window.__mfTris = renderer.info.render.triangles;
  window.__mfMesh = !!mesh;
}

resize();
applyLightMode("studio");
animate();

/* ==================================================================
 * 后端通信
 * ================================================================== */
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    let detail = null;
    try {
      const j = await res.json();
      detail = j.detail;
    } catch (_) {}
    // 后端拦截时 detail 是 {msg, report} 结构：既要拿到人话，也要拿到报告卡
    if (detail && typeof detail === "object") {
      msg = detail.msg || msg;
    } else if (detail) {
      msg = detail;
    }
    const err = new Error(msg);
    err.report = (detail && detail.report) || null;
    throw err;
  }
  return res.json();
}

/* ==================================================================
 * 基础颜色（色板）
 * ================================================================== */
const QUICK_COLORS = [
  ["#FFFFFF", "原色"], ["#F4EFE6", "米白"], ["#E7DCC8", "象牙"],
  ["#E3C79B", "浅橡"], ["#C89B6B", "橡木"], ["#8B5A2B", "红木"],
  ["#6B4A32", "胡桃"], ["#43301F", "深胡桃"], ["#B9B7B2", "浅灰"],
  ["#8C8F91", "水泥"], ["#6E7073", "深灰"], ["#E8E1D1", "米黄石"],
];

function normalizeHex(v) {
  let s = String(v || "").trim().replace(/^#/, "");
  if (s.length === 3) s = s.split("").map((c) => c + c).join("");
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return "#ffffff";
  return "#" + s.toLowerCase();
}

/* 三处色板控件（左栏取色器 / 左栏十六进制 / 参数面板）保持同步 */
function syncColorUI(src) {
  const hex = normalizeHex(S.values.base_color || "#ffffff");
  const c1 = $("baseColor");
  if (c1 && c1 !== src && c1.value.toLowerCase() !== hex) c1.value = hex;
  const t1 = $("baseColorHex");
  if (t1 && t1 !== src) t1.value = hex.toUpperCase();
  document.querySelectorAll('.prow[data-pid="base_color"] input[type="color"]').forEach((el) => {
    if (el !== src && el.value.toLowerCase() !== hex) el.value = hex;
  });
  document.querySelectorAll("#quickColors .qcolor").forEach((el) => {
    el.classList.toggle("active", normalizeHex(el.dataset.hex) === hex);
  });
}

function setBaseColor(hex) {
  S.values.base_color = normalizeHex(hex);
  syncColorUI(null);
  updateMaterial();
  // 颜色是要烘进 albedo 的（导出端就是这么做的），所以预览也必须重烤一遍，
  // 否则预览还在用 material.color 相乘，用户看到的和导出的永远不是同一个颜色。
  scheduleRebuild();
}

/* ---- 贴图重建（防抖 + 分级）：拖滑杆走低分辨率快预览，松手再出全尺寸 ---- */
let rebuildTimer = null;
let rebuildSeq = 0;
let fastTimer = null;

// 拖动中的快预览分辨率：320² 的像素量只有 1024² 的 1/10，滑杆跟手感靠它
const PREVIEW_SIZE = 320;

function scheduleRebuild(delay = 220) {
  if (!S.jobId) return;
  clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(() => rebuildPreview(false), delay);
}

/* 拖动中：先出低分辨率，时序上永远被随后的全图请求打断 */
function scheduleFastPreview(delay = 90) {
  if (!S.jobId) return;
  clearTimeout(fastTimer);
  fastTimer = setTimeout(() => rebuildPreview(true), delay);
}

async function rebuildPreview(preview = false) {
  if (!S.jobId) return;
  const seq = ++rebuildSeq;
  const dot = $("rebuildDot");
  if (dot) { dot.style.display = ""; dot.classList.toggle("fast", preview); }
  try {
    const data = await api("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: S.jobId,
        values: { ...S.values },
        detail: parseFloat($("detail")?.value ?? "1") || 1,
        preview,
        preview_size: PREVIEW_SIZE,
      }),
    });
    if (seq !== rebuildSeq) return;         // 有更新的请求在路上，丢弃旧结果
    S.texUrls = data.textures;
    S.bakedAlbedo = data.textures?.albedo || null;   // 已染色的 albedo
    S.textures = {};
    S.previewing = !!preview;               // 标记：当前屏上是快预览，导出前必须补全图
    S.lastMask = data.mask || null;         // 后端算的米制换算，比前端估算准，提示优先用它
    updateMaskHint();
    S.lastResize = data.resize || null;
    updateResizeHint();
    renderTexGrid(data.textures);
    loadTextures(data.textures);
    showTint(data.tint);
  } catch (e) {
    if (seq === rebuildSeq) toast("贴图重建失败：" + e.message, true);
  } finally {
    if (seq === rebuildSeq && dot) { dot.style.display = "none"; dot.classList.remove("fast"); }
  }
}

/* 色差回显：让「选色 vs 成品」的差异可量化，而不是靠肉眼猜 */
function showTint(t) {
  const el = $("tintInfo");
  if (!el) return;
  if (!t || t.mode === "identity") { el.textContent = ""; return; }
  const de = Number(t.delta_e ?? 0);
  const before = t.delta_e_before;
  const judge = de < 2 ? "完全一致" : de < 5 ? "肉眼几乎无差" : "仍有可见偏差";
  el.textContent = before != null
    ? `色差 ΔE ${de.toFixed(1)}（旧算法 ${Number(before).toFixed(1)}）· ${judge}`
    : `色差 ΔE ${de.toFixed(1)} · ${judge}`;
  el.classList.toggle("warn", de >= 5);
}

function renderQuickColors() {
  const box = $("quickColors");
  if (!box) return;
  box.innerHTML = "";
  for (const [hex, name] of QUICK_COLORS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "qcolor";
    b.dataset.hex = hex;
    b.title = `${name} ${hex}`;
    b.style.background = hex;
    b.addEventListener("click", () => setBaseColor(hex));
    box.appendChild(b);
  }
  syncColorUI(null);
}

/* ==================================================================
 * 参数面板渲染
 * ================================================================== */
function renderPanel() {
  const panel = $("paramPanel");
  panel.innerHTML = "";

  // 三层暴露：新手 5 项 / 中级 +25 项 / 进阶 +31 项（划分由后端 tiers 给出，
  // 前端只做「当前档位铺开的集合」与「折叠区剩余的集合」，不再猜哪些该露）
  const tiers = S.schema.tiers || {
    beginner: S.schema.beginner_ids || [],
    intermediate: [],
    advanced: [],
  };
  const begIds = tiers.beginner || [];
  const midIds = tiers.intermediate || [];
  const shown = S.mode === "advanced" ? [...begIds, ...midIds, ...(tiers.advanced || [])]
    : S.mode === "intermediate" ? [...begIds, ...midIds]
      : [...begIds];

  const box = document.createElement("div");
  box.className = "group";
  const h = document.createElement("h3");
  h.textContent = S.mode === "advanced" ? `全部参数（${shown.length} 项）`
    : S.mode === "intermediate" ? `常用 + 微调（${shown.length} 项）`
      : `常用（${shown.length} 项，够用）`;
  box.appendChild(h);
  for (const id of shown) {
    const p = S.schema.params.find((x) => x.id === id);
    if (p) box.appendChild(buildRow(p));
  }
  panel.appendChild(box);

  const folded = S.mode === "advanced" ? []
    : S.mode === "intermediate" ? (tiers.advanced || [])
      : [...midIds, ...(tiers.advanced || [])];

  if (folded.length) {
    // 折叠区：新手档同时收着中级与高级，标注清楚，避免用户以为参数丢了
    const det = document.createElement("details");
    det.className = "advbox";
    det.open = S.advancedOpen === true;
    const sum = document.createElement("summary");
    sum.textContent = S.mode === "intermediate"
      ? `进阶参数（${folded.length} 项，一般不用动）`
      : `中级 + 进阶参数（${folded.length} 项，想细调再展开）`;
    det.appendChild(sum);
    appendGroups(det, (p) => folded.includes(p.id));
    det.addEventListener("toggle", () => { S.advancedOpen = det.open; });
    panel.appendChild(det);
  }

  syncDepends();
}

/* 按分组把参数塞进容器，keep(p) 决定该项是否保留 */
function appendGroups(container, keep) {
  for (const group of S.schema.groups) {
    const items = S.schema.params.filter((p) => p.group === group && keep(p));
    if (!items.length) continue;

    const box = document.createElement("div");
    box.className = "group";
    const h = document.createElement("h3");
    h.textContent = group;
    box.appendChild(h);

    for (const p of items) box.appendChild(buildRow(p));
    container.appendChild(box);
  }
}

function buildRow(p) {
  const row = document.createElement("div");
  row.className = "prow";
  row.dataset.pid = p.id;

  const left = document.createElement("div");
  const lab = document.createElement("label");
  lab.textContent = p.label;
  left.appendChild(lab);
  if (p.help) {
    const s = document.createElement("span");
    s.className = "help";
    s.textContent = p.help;
    left.appendChild(s);
  }

  const right = document.createElement("div");
  right.className = "ctrl";

  if (p.ptype === "bool") {
    // 用 div 承载而非 label：彻底绕开 label 对内部控件的隐式激活转发，
    // 部分浏览器/内嵌 WebView 会因此二次 toggle，表现为勾选后取消不掉
    const sw = document.createElement("div");
    sw.className = "switch";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = "cb_" + p.id;
    cb.checked = S.values[p.id] === true;
    cb.addEventListener("change", () => {
      S.values[p.id] = cb.checked === true;
      cb.checked = S.values[p.id]; // 状态与视觉强制对齐，杜绝回弹
      afterValueChange(p);
    });
    const i = document.createElement("i");
    i.setAttribute("aria-hidden", "true");
    sw.appendChild(cb); sw.appendChild(i);
    right.appendChild(sw);

  } else if (p.ptype === "enum") {
    const sel = document.createElement("select");
    for (const o of p.options || []) {
      const op = document.createElement("option");
      op.value = o.value;
      op.textContent = o.label;
      sel.appendChild(op);
    }
    sel.value = S.values[p.id];
    sel.addEventListener("change", () => {
      S.values[p.id] = sel.value;
      afterValueChange(p);
    });
    right.appendChild(sel);

  } else if (p.ptype === "color") {
    const inp = document.createElement("input");
    inp.type = "color";
    inp.className = "colorpick";
    inp.value = normalizeHex(S.values[p.id]);
    inp.addEventListener("input", () => {
      S.values[p.id] = inp.value;
      syncColorUI(inp);
      updateMaterial();
    });
    right.appendChild(inp);

  } else if (p.ptype === "range" || p.ptype === "float") {
    const wrap = document.createElement("div");
    const num = document.createElement("div");
    num.className = "num";
    num.textContent = Number(S.values[p.id]).toFixed(2);
    const rg = document.createElement("input");
    rg.type = "range";
    rg.min = p.min ?? 0; rg.max = p.max ?? 1; rg.step = p.step ?? 0.01;
    rg.value = S.values[p.id];
    rg.addEventListener("input", () => {
      const v = parseFloat(rg.value);
      S.values[p.id] = v;
      num.textContent = v.toFixed(2);
      afterValueChange(p, true);
    });
    // 松手后立刻补一张全尺寸图：拖动过程只给低分辨率趋势，收手才看细节
    rg.addEventListener("change", () => {
      if (REBUILD_IDS.has(p.id)) scheduleRebuild(0);
    });
    wrap.appendChild(rg); wrap.appendChild(num);
    right.appendChild(wrap);

    // 种子换版：数字滑杆挑不出「好看的那一版」，得能一键换
    if (p.id === "proc_seed") {
      const btn = document.createElement("button");
      btn.className = "btn mini reseed";
      btn.type = "button";
      btn.textContent = "换一版";
      btn.title = "随机换一个种子，同参数下重新出一版纹理";
      btn.addEventListener("click", () => {
        const v = Math.floor(Math.random() * 997) + 1;
        S.values.proc_seed = v;
        rg.value = v;
        num.textContent = Number(v).toFixed(0);
        scheduleRebuild(0);
      });
      right.appendChild(btn);
    }
  }

  row.appendChild(left);
  row.appendChild(right);
  return row;
}

function afterValueChange(p, live = false) {
  syncDepends();
  if (p.id === "normal_strength") {
    $("nstr").value = S.values[p.id];
    $("nstrVal").textContent = Number(S.values[p.id]).toFixed(2);
  }
  updateMaterial();
  // 只对「改变贴图像素」的参数触发重建：颜色、程序化微结构、粗糙基准、法线强度。
  // 其它参数（IOR、雾化、清漆…）只影响材质属性，重建贴图纯属浪费算力。
  if (REBUILD_IDS.has(p.id)) {
    if (live) {
      scheduleFastPreview();     // 拖动中：先给低分辨率趋势，别让人对着旧图等 6 秒
      scheduleRebuild(420);      // 兜底：即使没触发 change，停手后也补一张全尺寸
    } else {
      scheduleRebuild();
    }
  }
  updateMaskHint();
}

/* 遮罩尺度的米制回显：把 proc_mask_scale 换算成真实世界区块尺寸，
   并提示 uv_tiling 过大时「区块放不进一块贴图、必然复读」这件事 */
function maskWorldInfo() {
  const ms = Number(S.values.proc_mask_scale ?? 1) || 1;   // 1.0 ≈ 1 米区块
  const tiling = Number(S.values.uv_tiling ?? 1) || 1;
  // 后端回显的换算优先：它用的常数（1 UV 单位≈1 米、最小循环数）与真正生成贴图的代码同源，
  // 只有滑块刚改、后端还没回来时才回落到本地估算
  const m = S.lastMask;
  if (m && Math.abs(Number(m.scale ?? -1) - ms) < 1e-6
        && Math.abs(Number(m.uv_tiling ?? -1) - tiling) < 1e-6) {
    return {
      block: Number(m.block_m) || ms,
      tile: Number(m.tile_m) || 1 / tiling,
      cpt: Number(m.cycles_per_tile) || 1,
      clamped: !!m.repeat_risk,
      actual: Number(m.block_m_actual ?? m.block_m ?? ms),
      effective: Number(m.amount_effective ?? 1),
    };
  }
  const block = ms;
  const tile = 1 / tiling;                                  // 一块贴图覆盖的米数
  const cpt = Math.max(tile / block, 1);
  return { block, tile, cpt, clamped: tile / block < 1 - 1e-6, actual: tile / cpt,
           effective: Math.min(1, tile / block) };
}

function updateMaskHint() {
  const row = document.querySelector('.prow[data-pid="proc_mask_scale"]');
  if (!row) return;
  let el = row.querySelector(".mask-hint");
  if (!el) {
    el = document.createElement("span");
    el.className = "mask-hint";
    const lab = row.querySelector("label");
    if (lab) lab.after(el); else row.firstChild.appendChild(el);
  }
  const i = maskWorldInfo();
  const tiling = Number(S.values.uv_tiling ?? 1) || 1;
  let txt = `≈ ${i.actual.toFixed(2)} 米区块`;
  if (i.clamped) {
    txt += ` · 平铺 ${tiling.toFixed(2)} 时一块贴图只有 ${i.tile.toFixed(2)} 米，`;
    txt += "区块放不下会被压缩复读，强度已自动削弱，建议调小尺度或降低平铺";
  }
  if (i.effective != null && i.effective < 0.999) {
    txt += `（实际生效 ${(i.effective * 100).toFixed(0)}%）`;
  }
  el.classList.toggle("warn", i.clamped);
  el.textContent = txt;
}

/* 缩图提示：贴图集有单边 2048px 上限，原图更大时如实告知落盘尺寸，
   不能让用户以为「传多大就导出多大」——静默缩放是本节要消灭的问题 */
function updateResizeHint() {
  const el = $("resizeHint");
  if (!el) return;
  const r = S.lastResize;
  if (!r || !r.downscaled) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  el.style.display = "";
  el.textContent = r.note || `原图已按单边 ${r.max_side}px 上限缩放`;
}

const REBUILD_IDS = new Set([
  "base_color", "tint_mode", "tint_strength", "tint_chroma",
  "proc_on", "proc_type", "proc_scale", "proc_amount", "proc_rough",
  "proc_albedo", "proc_mask", "proc_mask_scale", "proc_seed", "rough_level", "gloss_mode",
  "reflection_glossiness", "normal_strength", "uv_tiling",
]);

/* depends 条件显隐 */
function syncDepends() {
  for (const p of S.schema.params) {
    if (!p.depends) continue;
    const row = document.querySelector(`.prow[data-pid="${p.id}"]`);
    if (!row) continue;
    let show = true;
    for (const [k, want] of Object.entries(p.depends)) {
      if (S.values[k] !== want) { show = false; break; }
    }
    row.classList.toggle("dim", !show);
  }
  syncTexDeps();
}

/* 贴图集勾选对参数的影响：凹凸取自高度图、法线需要法线图 */
function syncTexDeps() {
  const on = S.texOn || {};
  const deps = [
    ["bump_on", on.height === false, "凹凸取自高度贴图，请先勾选「高度」"],
    ["bump_strength", on.height === false || S.values.bump_on !== true, "需开启凹凸并勾选高度贴图"],
    ["normal_strength", on.normal === false || S.values.normal_on !== true, "需开启法线并勾选法线贴图"],
    ["opacity_on", on.opacity === false, "需勾选「透明度」贴图"],
    ["opacity_cutoff", on.opacity === false || S.values.opacity_on !== true, "需开启透明度贴图"],
  ];
  for (const [pid, lack, txt] of deps) {
    const row = document.querySelector(`.prow[data-pid="${pid}"]`);
    if (!row) continue;
    row.classList.toggle("dim", lack);
    let tip = row.querySelector(".dep-tip");
    if (lack) {
      if (!tip) {
        tip = document.createElement("span");
        tip.className = "dep-tip";
        const lab = row.querySelector("label");
        if (lab) lab.after(tip); else row.firstChild.appendChild(tip);
      }
      tip.textContent = txt;
    } else if (tip) {
      tip.remove();
    }
  }
}

function renderPresets() {
  const box = $("presets");
  const famBox = $("presetFams");
  const names = S.schema.preset_labels || {};
  const snaps = S.schema.preset_full || S.schema.presets || {};
  const fams = S.schema.preset_families || {};
  const famLabels = S.schema.preset_family_labels || {};
  // 第一轴默认「全部」：既是总览入口，也保证预设按钮默认全量存在于 DOM
  if (!S.presetFam) S.presetFam = "all";
  const famOf = {};
  for (const [fk, keys] of Object.entries(fams)) {
    (keys || []).forEach((k) => { famOf[k] = fk; });
  }

  box.innerHTML = "";
  if (famBox) famBox.innerHTML = "";

  // 第一轴：族群 chip
  if (famBox) {
    const mkFam = (fk, label) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.dataset.fam = fk;
      if (fk === S.presetFam) b.classList.add("active");
      b.addEventListener("click", () => { S.presetFam = fk; renderPresets(); });
      famBox.appendChild(b);
    };
    mkFam("all", "全部");
    for (const fk of Object.keys(fams)) mkFam(fk, famLabels[fk] || fk);
  }

  // 第二轴：该族下的预设按钮
  for (const [key, vals] of Object.entries(snaps)) {
    if (S.presetFam !== "all" && famOf[key] !== S.presetFam) continue;
    const b = document.createElement("button");
    b.textContent = names[key] || key;
    b.dataset.key = key;
    b.title = "套用整套参数快照（覆盖全部 " + Object.keys(vals).length + " 项）";
    if (S.presetKey === key) b.classList.add("active");
    b.addEventListener("click", () => {
      S.values = { ...S.defaults, ...vals, base_color: S.values.base_color || "#ffffff" };
      S.presetKey = key;
      renderPanel();
      syncColorUI(null);
      updateMaterial();
      box.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      toast(`已套用预设：${names[key] || key}（${Object.keys(vals).length} 项参数已整体替换）`);
    });
    box.appendChild(b);
  }
}


/* ==================================================================
 * 贴图集缩略
 * ================================================================== */
const TEX_LABEL = {
  albedo: "漫反射", normal: "法线", roughness: "粗糙度", height: "高度", ao: "AO", opacity: "透明度",
  metalness: "金属度", emissive: "自发光", sheen: "绒面",
};

function renderTexGrid(urls) {
  const grid = $("texgrid");
  grid.innerHTML = "";
  // 顺序固定：基础通道在前，三通道在后；没出图的通道自然跳过（url 为空）
  for (const key of ["albedo", "normal", "roughness", "height", "ao", "opacity",
                     "metalness", "emissive", "sheen"]) {
    const url = urls[key];
    if (!url) continue;

    const cell = document.createElement("div");
    cell.className = "texcell" + (S.texOn[key] === false ? " off" : "");
    cell.dataset.ch = key;

    const img = document.createElement("img");
    img.src = url + "?t=" + Date.now();
    cell.appendChild(img);

    // 勾选框：同样用 div 承载 + input 铺满，不套 label，
    // 避免 label 的隐式激活转发导致点一次翻两次（与参数面板开关同一坑）
    const box = document.createElement("div");
    box.className = "texchk";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = S.texOn[key] !== false;
    cb.addEventListener("change", () => {
      S.texOn[key] = cb.checked === true;
      cb.checked = S.texOn[key]; // 状态与视觉强制对齐
      cell.classList.toggle("off", !S.texOn[key]);
      updateMaterial();
      syncDepends();
    });
    const mark = document.createElement("i");
    mark.setAttribute("aria-hidden", "true");
    box.appendChild(cb); box.appendChild(mark);
    cell.appendChild(box);

    const s = document.createElement("span");
    s.textContent = TEX_LABEL[key];
    cell.appendChild(s);

    // 点缩略图任意处也能切换；命中勾选框本身时交给 change，避免双重翻转
    cell.addEventListener("click", (e) => {
      if (e.target === cb) return;
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event("change"));
    });

    grid.appendChild(cell);
  }
}

/* 贴图 URL -> HTMLImageElement -> THREE.Texture */
function loadTextures(urls) {
  const keys = Object.keys(urls);
  let left = keys.length;
  const onDone = () => {
    if (--left === 0) {
      updateMaterial();
      $("vpEmpty").style.display = "none";
      $("btnExport").disabled = false;
      $("btnPreflight").disabled = false;
    }
  };
  for (const k of keys) {
    const im = new Image();
    im.crossOrigin = "anonymous";
    im.onload = () => { setTexture(k, im); onDone(); };
    im.onerror = () => { onDone(); };
    im.src = urls[k] + "?t=" + Date.now();
  }
}

/* ==================================================================
 * 上传
 * ================================================================== */
const dropzone = $("dropzone");
const fileInput = $("fileInput");

dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const fs = Array.from(fileInput.files || []);
  if (fs.length) pickFiles(fs);
  fileInput.value = "";     // 允许重复选同一批文件（批量导第二批时常用）
});
["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("over"); }));
dropzone.addEventListener("drop", (e) => {
  const fs = Array.from(e.dataTransfer.files || []);
  if (fs.length) pickFiles(fs);
});

/* 一张 = 常规单材质流程；多张 = 进批量队列。
   旧版只认 files[0]，一次拖 20 张会静默丢掉 19 张，是批量需求最直接的痛点。 */
function pickFiles(files) {
  const imgs = files.filter((f) =>
    /^image\//.test(f.type || "") || /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(f.name || ""));
  if (!imgs.length) { toast("这些文件里没有可用的图片", true); return; }
  if (imgs.length === 1) {
    S.batchFiles = [];
    syncBatchUI();
    handleUpload(imgs[0]);
    return;
  }
  S.batchFiles = imgs;
  syncBatchUI();
  dropzone.querySelector(".dz-main").textContent = `已选 ${imgs.length} 张（批量模式）`;
  dropzone.querySelector(".dz-sub").textContent = "点右栏「批量导出」开始；只想单张请一次只拖一张";
  toast(`已选 ${imgs.length} 张，可一次导出 ${imgs.length} 套材质`);
}

function syncBatchUI() {
  const n = (S.batchFiles || []).length;
  const btn = $("btnBatch");
  const hint = $("batchHint");
  if (btn) {
    btn.disabled = n === 0;
    btn.textContent = n
      ? `批量导出 ${n} 套材质（${S.renderer === "corona" ? "Corona" : "V-Ray"}）`
      : "批量导出（多张图 → 多套材质）";
  }
  if (hint) hint.textContent = n ? `${n} 张排队中：参数取右栏当前面板，材质名沿用各自文件名` : "";
}

async function handleUpload(file) {
  dropzone.querySelector(".dz-main").textContent = "解析中...";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const data = await api("/api/upload", { method: "POST", body: fd });
    S.jobId = data.job_id;
    S.texUrls = data.textures;
    S.textures = {};
    S.bakedAlbedo = null;
    S.solidMode = false;
    S.lastResize = data.resize || null;
    updateResizeHint();
    renderTexGrid(data.textures);
    loadTextures(data.textures);
    $("btnRedo").disabled = false;
    dropzone.querySelector(".dz-main").textContent = file.name;
    dropzone.querySelector(".dz-sub").textContent = "点击可更换图片";
    const base = file.name.replace(/\.[^.]+$/, "").replace(/[^\w\u4e00-\u9fa5-]/g, "_");
    $("matName").value = base || "MaterialForge";
    toast("贴图已生成");
    // 上传后立刻按当前面板重烤一次：程序化微结构 / 色板校色都要落到贴图上
    scheduleRebuild(60);
  } catch (e) {
    dropzone.querySelector(".dz-main").textContent = "拖入图片，或点击选择";
    // 区分「服务没在跑」和「后端拒绝该文件」：前者是 fetch 直接抛 TypeError
    const offline = e instanceof TypeError ||
      /Failed to fetch|NetworkError|Load failed|network/i.test(e.message || "");
    if (offline) {
      toast("连接不上本地服务（127.0.0.1:8765）。请确认「Material Forge 服务」窗口仍在运行，再刷新本页重试。", true);
    } else {
      toast("上传失败：" + e.message, true);
    }
  }
}

/* ==================================================================
 * 重算贴图
 * ================================================================== */
$("detail").addEventListener("input", (e) => {
  $("detailVal").textContent = Number(e.target.value).toFixed(2);
});
$("nstr").addEventListener("input", (e) => {
  const v = parseFloat(e.target.value);
  $("nstrVal").textContent = v.toFixed(2);
  S.values.normal_strength = v;
  // 只同步面板内同名控件，不整块重建（重建会销毁正在操作的 DOM，引出状态回弹）
  const row = document.querySelector('.prow[data-pid="normal_strength"]');
  if (row) {
    const rg = row.querySelector('input[type="range"]');
    const num = row.querySelector(".num");
    if (rg) rg.value = v;
    if (num) num.textContent = v.toFixed(2);
  }
  updateMaterial();
});

$("btnRedo").addEventListener("click", async () => {
  if (!S.jobId) return;
  const btn = $("btnRedo");
  btn.disabled = true; btn.textContent = "重算中...";
  try {
    const data = await api("/api/derive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: S.jobId,
        options: {
          detail: parseFloat($("detail").value),
          normal_strength: parseFloat($("nstr").value),
        },
      }),
    });
    S.texUrls = data.textures;
    S.textures = {};
    S.bakedAlbedo = null;
    S.lastResize = data.resize || null;
    updateResizeHint();
    renderTexGrid(data.textures);
    loadTextures(data.textures);
    toast("贴图已按新强度重算");
    scheduleRebuild(60);
  } catch (e) {
    toast("重算失败：" + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "按当前强度重算贴图";
  }
});

/* ==================================================================
 * 纯色模式：不上传任何图片
 * 乳胶漆的粗糙感本来就不该靠法线图——真实墙面的麻面来自滚涂的微起伏，
 * 这件事完全可以按物理尺度程序化生成，色板选个颜色就够出图。
 * ================================================================== */
$("btnSolid").addEventListener("click", async () => {
  const btn = $("btnSolid");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "生成中...";
  try {
    const data = await api("/api/solid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        values: { ...S.values },
        detail: parseFloat($("detail")?.value ?? "1") || 1,
      }),
    });
    S.jobId = data.job_id;
    S.solidMode = true;
    S.texUrls = data.textures;
    S.bakedAlbedo = data.textures?.albedo || null;
    S.textures = {};
    S.lastResize = data.resize || null;
    updateResizeHint();
    renderTexGrid(data.textures);
    loadTextures(data.textures);
    $("btnRedo").disabled = false;
    $("matName").value = "LatexPaint_" + normalizeHex(S.values.base_color).slice(1).toUpperCase();
    $("dropzone").querySelector(".dz-main").textContent = "纯色模式（无上传图）";
    $("dropzone").querySelector(".dz-sub").textContent = "点此仍可换成图片素材";
    $("vpEmpty").style.display = "none";
    showTint(data.tint);
    toast("纯色材质已生成，改色板即可换色");
  } catch (e) {
    toast("纯色模式失败：" + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
});

/* ==================================================================
 * 基础颜色控件（左栏）
 * ================================================================== */
$("baseColor").addEventListener("input", (e) => setBaseColor(e.target.value));
$("baseColorHex").addEventListener("change", (e) => {
  const hex = normalizeHex(e.target.value);
  e.target.value = hex.toUpperCase();
  setBaseColor(hex);
});
$("btnColorReset").addEventListener("click", () => {
  setBaseColor("#ffffff");
  toast("基础颜色已复位");
});

/* ==================================================================
 * 工具栏
 * ================================================================== */
$("shapeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  $("shapeSeg").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  applyShape(b.dataset.shape);
});
$("lightSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  $("lightSeg").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  applyLightMode(b.dataset.light);
});
$("btnSpin").addEventListener("click", () => {
  S.spin = !S.spin;
  $("btnSpin").textContent = S.spin ? "停止旋转" : "开始旋转";
});

/* ==================================================================
 * 模式切换：新手 / 进阶
 * 新手只露 5 项常用参数 + 三步指引；进阶展开全部参数（数量取自 schema），按原分组铺开
 * ================================================================== */
function setMode(mode) {
  // 三档归一化：不认识的值一律退回 beginner，杜绝 S.mode 出现脏值后前端全露/全空
  S.mode = ["beginner", "intermediate", "advanced"].includes(mode) ? mode : "beginner";
  const seg = $("modeSeg");
  if (seg) {
    seg.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === S.mode);
    });
  }
  const steps = $("steps");
  // 三步指引只在纯新手档显示，中级起用户已经知道流程
  if (steps) steps.style.display = S.mode === "beginner" ? "" : "none";
  if (S.schema) renderPanel();
}

$("modeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  setMode(b.dataset.mode);
  const tiers = (S.schema && S.schema.tiers) || null;
  const cnt = (k) => (tiers && tiers[k] ? tiers[k].length : 0);
  const msg = S.mode === "advanced"
    ? `已切到进阶模式：${(S.schema && S.schema.params ? S.schema.params.length : 0)} 项参数全部展开`
    : S.mode === "intermediate"
      ? `已切到中级模式：常用 ${cnt("beginner") + cnt("intermediate")} 项，其余 ${cnt("advanced")} 项折叠`
      : `已切到新手模式：只显示 ${cnt("beginner") || 5} 项常用参数`;
  toast(msg);
});

/* ==================================================================
 * 导出
 * ================================================================== */
function payloadBody() {
  return {
    job_id: S.jobId,
    material_name: $("matName").value,
    renderer: S.renderer,
    values: { ...S.values, tex_enabled: { ...S.texOn } },
  };
}

$("btnExport").addEventListener("click", async () => {
  if (!S.jobId) return;
  const btn = $("btnExport");
  btn.disabled = true; btn.textContent = "导出中...";
  try {
    // 屏上若还停着拖动时的低分辨率快预览，先补一张全尺寸再导出，
    // 否则导出拿到的是上一版参数的全图，所见非所得
    if (S.previewing) {
      btn.textContent = "补算全尺寸...";
      await rebuildPreview(false);
    }
    const data = await api("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadBody()),
    });
    S.lastReport = data.report || null;
    showExport(data);
    toast("导出完成：" + data.ms_path);
  } catch (e) {
    // 被后端 dry-run 拦下：不当成普通失败，要把报告卡打出来告诉用户改哪里
    if (e.report) {
      S.lastReport = e.report;
      showBlocked(e.message, e.report);
      toast("已拦截导出：" + e.message, true);
    } else {
      toast("导出失败：" + e.message, true);
    }
  } finally {
    btn.disabled = false; btn.textContent = "导出 MaxScript (.ms)";
  }
});

/* 渲染器切换：只改导出目标，不重烤贴图（两种渲染器共用同一套 PBR 通道） */
function setRenderer(r) {
  S.renderer = ["vray", "corona"].includes(r) ? r : "vray";
  const seg = $("rendererSeg");
  if (seg) seg.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.renderer === S.renderer));
  const isCr = S.renderer === "corona";
  const title = $("paneTitle");
  if (title) title.textContent = isCr ? "Corona 参数" : "V-Ray 参数";
  const hint = $("rendererHint");
  if (hint) hint.textContent = isCr
    ? "Corona 出 CoronaPhysicalMtl（未装则自动降级 PhysicalMaterial）；V-Ray 专有项会被跳过并写进日志"
    : "V-Ray 出 VRayMtl；两套脚本分别落在带后缀的目录里，互不覆盖";
  // 已导出的旧结果不再代表当前目标，清掉避免误读
  const box = $("exportResult");
  if (box) box.innerHTML = "";
  syncBatchUI();     // 批量按钮文案里带渲染器名，切换后要同步
}

$("rendererSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  setRenderer(b.dataset.renderer);
  toast(S.renderer === "corona" ? "导出目标：Corona" : "导出目标：V-Ray");
});

/* ==================================================================
 * 批量导出（0.12.0）
 *
 * 场景：手上 20 张木纹，一张张拖、一张张导出要点 40 次。这里一次全传，
 * 后端逐张 upload→export，共用当前面板参数与渲染器，材质名沿用文件名。
 * 单项失败不中断整批（后端逐项回执），所以结果必须逐条展示而不是一句"成功"。
 * ================================================================== */
function showBatch(d) {
  const box = $("exportResult");
  const items = d.items || [];
  const bad = d.fail_count || 0;
  const rows = items.map((x) => x.ok
    ? `<div class="line"><b>${x.name}${x.selfcheck_ok === false ? "（有告警）" : ""}</b><span>${x.ms_path || ""}</span></div>`
    : `<div class="line"><b style="color:#ff6b6b">${x.file}</b><span>失败：${x.error || "未知原因"}</span></div>`
  ).join("");
  const firstDir = (items.find((x) => x.ok && x.dir) || {}).dir || "";
  box.innerHTML =
    `<div class="${bad ? "bad" : "ok"}">批量导出：成功 ${d.ok_count} / ${d.total}${bad ? `，失败 ${bad}` : ""}</div>` +
    rows +
    (firstDir
      ? `<button class="btn ghost" onclick="openDir('${firstDir.replace(/\\/g, "\\\\")}')">打开输出目录</button>`
      : "") +
    `<div class="tip">每套材质独占一个子目录（.ms + textures/），整目录拷到 Max 机器即可用；<br />` +
    `参数取当前面板（含通道勾选与目标渲染器），想逐张不同参数请单张导出。</div>`;
}

$("btnBatch").addEventListener("click", async () => {
  const files = S.batchFiles || [];
  if (!files.length) return;
  const btn = $("btnBatch");
  const keep = btn.textContent;
  btn.disabled = true;
  btn.textContent = `批量导出中…（${files.length} 张，请稍候）`;
  try {
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    fd.append("payload", JSON.stringify(payloadBody()));
    // FormData 交给浏览器自己带 boundary，这里绝不能手写 Content-Type
    const data = await api("/api/batch_export", { method: "POST", body: fd });
    showBatch(data);
    toast(`批量导出完成：成功 ${data.ok_count} / ${data.total}`);
  } catch (e) {
    toast("批量导出失败：" + e.message, true);
  } finally {
    btn.textContent = keep;
    syncBatchUI();
  }
});

/* ==================================================================
 * 我的预设（0.12.0）
 *
 * 内置 33 个是作者调的参数组合；用户自己调顺手的那套（比如"这个客户的灰泥"）
 * 之前无处安放，关掉页面就没了。存在后端用户目录（~/.3dsmax_material_forge），
 * 不写 localStorage：预设是资产，换浏览器、重装前端都不该丢。
 * 只存参数表里的键：base_color 跟着当前色板走，tex_enabled 属于会话状态，
 * 后端也会再过滤一遍（双保险）。
 * ================================================================== */
async function refreshUserPresets() {
  try {
    const d = await api("/api/presets/user");
    S.userPresets = d.presets || {};
    S.userPresetMax = d.max || 40;
  } catch (e) {
    S.userPresets = S.userPresets || {};    // 服务不在线时保留上一次的，不清空成空壳
  }
  renderUserPresets();
}

function renderUserPresets() {
  const box = $("userPresets");
  if (!box) return;
  const names = Object.keys(S.userPresets || {});
  const cnt = $("mineCount");
  if (cnt) cnt.textContent = String(names.length);
  box.innerHTML = "";
  if (!names.length) {
    const tip = document.createElement("span");
    tip.className = "empty";
    tip.textContent = "调好参数点「存当前参数」，下次一键套用";
    box.appendChild(tip);
    return;
  }
  for (const name of names) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.key = name;
    b.textContent = name;
    b.title = "套用我保存的预设（覆盖全部参数）";
    b.addEventListener("click", (ev) => {
      if (ev.target.classList.contains("del")) return;
      applyUserPreset(name);
    });
    const del = document.createElement("span");
    del.className = "del";
    del.textContent = "×";
    del.title = "删除该预设";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteUserPreset(name);
    });
    b.appendChild(del);
    box.appendChild(b);
  }
}

function applyUserPreset(name) {
  const vals = (S.userPresets || {})[name];
  if (!vals) return;
  S.values = { ...S.defaults, ...vals, base_color: S.values.base_color || "#ffffff" };
  S.presetKey = null;
  renderPanel();
  syncColorUI(null);
  updateMaterial();
  document.querySelectorAll("#presets button").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll("#userPresets button").forEach((x) => x.classList.remove("active"));
  const me = document.querySelector(`#userPresets button[data-key="${name}"]`);
  if (me) me.classList.add("active");
  scheduleSave();
  toast(`已套用我的预设：${name}`);
}

async function saveUserPreset() {
  const def = ($("matName").value || "").trim() || "我的预设";
  const name = window.prompt("给这套参数起个名字（中英文/数字/空格/下划线，最多 24 字）", def);
  if (name === null) return;
  const key = name.trim();
  if (!key) { toast("名字不能为空", true); return; }
  const values = {};
  for (const [k, v] of Object.entries(S.values || {})) {
    if (k === "tex_enabled") continue;    // 勾选状态不进预设（后端也会过滤）
    values[k] = v;
  }
  try {
    const d = await api("/api/presets/user", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, values }),
    });
    S.userPresets = d.presets || {};
    renderUserPresets();
    toast(`已保存预设「${key}」（${d.count} 项参数）`);
  } catch (e) {
    toast("保存预设失败：" + e.message, true);
  }
}

async function deleteUserPreset(name) {
  if (!window.confirm(`删除预设「${name}」？该操作不可撤销。`)) return;
  try {
    const d = await api("/api/presets/user/" + encodeURIComponent(name), { method: "DELETE" });
    S.userPresets = d.presets || {};
    renderUserPresets();
    toast(`已删除预设「${name}」`);
  } catch (e) {
    toast("删除预设失败：" + e.message, true);
  }
}

$("btnSavePreset").addEventListener("click", saveUserPreset);

$("btnPreflight").addEventListener("click", async () => {
  if (!S.jobId) return;
  const btn = $("btnPreflight");
  btn.disabled = true; btn.textContent = "自检中...";
  try {
    const r = await api("/api/preflight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadBody()),
    });
    S.lastReport = r;
    const box = $("exportResult");
    box.innerHTML =
      `<div class="${r.ok ? "ok" : "bad"}">${r.ok ? "自检通过，可以导出" : "自检发现阻断项，导出会被拦截"}</div>` +
      reportHtml(r);
    toast(r.ok ? "自检通过" : "自检未通过", !r.ok);
  } catch (e) {
    toast("自检失败：" + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "导出前自检";
  }
});

/* 自检报告卡：把 preflight 的 items 按 错误/提示/通过 三层渲染 */
function reportHtml(r) {
  if (!r || !Array.isArray(r.items)) return "";
  const LV = { error: "阻断", warn: "提示", ok: "通过" };
  const rows = r.items
    .map((i) => `<li class="rp-${i.level}"><span class="rp-tag">${LV[i.level] || i.level}</span>` +
                `<b>${i.title}</b><span class="rp-detail">${i.detail}</span></li>`)
    .join("");
  const envLine = r.env
    ? `<div class="rp-env">${r.env.target === "corona" ? "Corona" : "V-Ray"}：` +
      `${r.env.installed ? "已安装 " + (r.env.version || "") + "（Max " + (r.env.max_year || "?") + "）" : "未检测到"} · ` +
      `当前渲染器：${r.env.current_renderer || "待 Max 运行时探测"}</div>`
    : "";
  return `<div class="report"><div class="rp-head">自检报告 · 阻断 ${r.blocking || 0} · 提示 ${r.warnings || 0}</div>` +
         `<ul class="rp-list">${rows}</ul>${envLine}</div>`;
}

// 导出后自检卡：告诉用户「贴图到底齐不齐」，而不是只报一句导出成功
// 数据来自后端 export_guard.check_export()，与 preflight、evals 同一套判定
function selfcheckHtml(sc) {
  if (!sc) return "";
  const miss = (sc.missing || []).map(m => `缺 ${m.channel}：${m.reason}${m.path ? "（" + m.path + "）" : ""}`);
  const broken = (sc.broken_refs || []).map(p => `.ms 引用断链：${p}`);
  const absent = (sc.absent || []).map(c => `开关已开但没生成 ${c} 贴图，该通道在 Max 里为空`);
  const rows = miss.concat(broken, absent);
  const packed = (sc.packed || {}).count || 0;
  const head = sc.ok
    ? `导出自检 · ${sc.checked || 0} 张贴图全部到位 · 打包 ${packed} 张 · 脚本 ${sc.refs || 0} 处引用无断链`
    : `导出自检 · ${rows.length} 处待处理（文件已生成，补图后重导或进 Max 前手动补）`;
  return `<div class="report"><div class="rp-head">${head}</div>` +
    (rows.length ? `<ul class="rp-list">${rows.map(t => `<li>${t}</li>`).join("")}</ul>` : "") +
    (sc.dir ? `<div class="rp-env">导出目录：${sc.dir}</div>` : "") +
    `</div>`;
}

function showExport(d) {
  const box = $("exportResult");
  const line = (k, v) => `<div class="line"><b>${k}</b><span>${v}</span></div>`;
  const bad = d.selfcheck && d.selfcheck.ok === false;
  box.innerHTML =
    `<div class="${bad ? "bad" : "ok"}">${bad ? "导出完成（有告警）" : "导出成功"}</div>` +
    line(".ms", d.ms_path) +
    line("导出目录", d.texture_dir || "") +
    line("贴图", (d.selfcheck && d.selfcheck.packed && d.selfcheck.packed.count)
      ? `${d.texture_dir}\\textures\\ · ${d.selfcheck.packed.count} 张`
      : (d.texture_subdir || d.texture_dir || "")) +
    `<div class="tip">用法：3ds Max 菜单 Scripting &rarr; Run Script，选择上面这个 .ms 文件。<br />` +
    `脚本会自动创建 ${d.renderer === "corona" ? "CoronaPhysicalMtl" : "VRayMtl"}、连好贴图` +
    `${d.auto_assign === false ? "" : "，并把材质赋给当前选中的物体"}；` +
    `贴图按「脚本同级 textures/」相对查找，整个文件夹搬走也不丢图。<br />` +
    `材质库：本版 3ds Max 已移除脚本入库接口，材质会落在材质编辑器的空槽里，需要归档请用「材质编辑器 &rarr; 另存材质库」。</div>` +
    selfcheckHtml(d.selfcheck) +
    reportHtml(d.report) +
    `<button class="btn ghost" onclick="openDir('${d.texture_dir.replace(/\\/g, "\\\\")}')">打开输出目录</button>` +
    `<button class="btn ghost" onclick="copyPath('${d.ms_path.replace(/\\/g, "\\\\")}')">复制 .ms 路径</button>` +
    `<button class="btn" onclick="sendToMax('${d.ms_path.replace(/\\/g, "\\\\")}')">送进 Max</button>` +
    `<div class="tip" id="sendMaxNote"></div>`;
}

window.sendToMax = async (p) => {
  const note = $("sendMaxNote");
  if (note) note.textContent = "正在送进 3ds Max…";
  try {
    const r = await api("/api/send_to_max", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ms_path: p }),
    });
    if (r.ok) {
      toast(r.mode === "inbox" ? "已送进 Max" : "命令已复制到剪贴板");
      if (note) note.textContent = r.msg || "";
    } else {
      toast("送进 Max 失败", true);
      if (note) note.textContent = r.msg || "送进失败";
    }
  } catch (e) {
    toast("送进 Max 失败：" + e.message, true);
    if (note) note.textContent = "送进失败：" + e.message;
  }
};

window.copyPath = async (p) => {
  try {
    await navigator.clipboard.writeText(p);
    toast("已复制路径：" + p);
  } catch (e) {
    toast("复制失败，请手动复制：" + p, true);
  }
};

function showBlocked(msg, r) {
  const box = $("exportResult");
  box.innerHTML = `<div class="bad">导出被拦截</div><div class="tip">${msg}</div>` + reportHtml(r);
}

window.openDir = async (p) => {
  try {
    await api("/api/open_dir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p }),
    });
  } catch (e) {
    toast("打开目录失败：" + e.message, true);
  }
};

/* ==================================================================
 * 启动
 * ================================================================== */
(async function boot() {
  try {
    S.schema = await api("/api/schema");
    S.values = {};
    for (const p of S.schema.params) S.values[p.id] = p.default;
    S.values.base_color = normalizeHex(S.values.base_color || "#ffffff");
    S.defaults = { ...S.values };

    // 恢复上次会话（参数 / 通道勾选 / 形状 / 灯光 / 渲染器）
    const saved = restoreSavedState(readSavedState());
    renderQuickColors();
    renderPresets();
    refreshUserPresets();     // 我的预设：失败不阻塞启动，只留空列表
    setMode(S.mode);
    ensureMaterial();
    // 恢复顺序：先灯光（重建环境贴图），再形状（applyShape 依赖 material 已存在）
    applyLightMode(S.lightMode);
    applyShape(S.shape);
    syncShapeLightUI();
    updateMaterial();
    $("engineHint").textContent = `渲染引擎：three.js ${THREE.REVISION} · 输出：V-Ray 6 VRayMtl`;

    // 纯色模式可以完整重建（源图本来就是程序化生成的）；图片模式只能恢复参数
    if (saved && saved.solidMode) {
      $("btnSolid").click();
      toast("已恢复上次的参数与纯色材质");
    } else if (saved) {
      toast("已恢复上次的参数，图片需重新上传", true);
    }
  } catch (e) {
    $("engineHint").textContent = "初始化失败：" + e.message;
  }
})();
