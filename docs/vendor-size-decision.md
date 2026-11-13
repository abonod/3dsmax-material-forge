---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 89f2995f43cde2f2717e466bdd92de27_0fbb81eadf5711f1b140525400de85a5
    ReservedCode1: zjt9pJq4NUPNJIYPvecAvYhyyx6kjfx4uSbROH47x/LMeuCdjhOSa0LbnQsp+E8vfdTkPnWVvrAlqzYUQ+u7VyHyAC5fVzF6NSHa/IkVXNK3bYANyzgCaf2J4Gu1GoLWY+vInyiCzM5v3UODXR1i53Nb809cybow/igiWr/jmX3LzWC9TJs5NWUFreQ=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 89f2995f43cde2f2717e466bdd92de27_0fbb81eadf5711f1b140525400de85a5
    ReservedCode2: zjt9pJq4NUPNJIYPvecAvYhyyx6kjfx4uSbROH47x/LMeuCdjhOSa0LbnQsp+E8vfdTkPnWVvrAlqzYUQ+u7VyHyAC5fVzF6NSHa/IkVXNK3bYANyzgCaf2J4Gu1GoLWY+vInyiCzM5v3UODXR1i53Nb809cybow/igiWr/jmX3LzWC9TJs5NWUFreQ=
---

# vendor 体积裁剪评估（v0.8.2）

## 结论

**不裁剪**。`web/vendor` 现有 2.03MB，两个 three 文件都得留。

## 事实

| 文件 | 体积 | 角色 |
|------|------|------|
| `three.core.js` | 1.44MB（61162 行） | 数学/几何/材质/渲染核心，WebGLRenderer 的底座 |
| `three.module.js` | 643KB（19537 行） | 对外入口，`import {...} from './three.core.js'` 后组装 renderer 与全量再导出 |

- `web/app.js` 只 `import * as THREE from "./vendor/three.module.js"`，未直接引 core。
- 但 `three.module.js` 内部对 `./three.core.js` 有 2 处 import（值导入 + 再导出），删掉 core 会立刻 `Module not found`。
- 二者不是同一份代码的两份拷贝：module 里只有 2 行 `export`，业务实体在 core 里。

## 曾考虑的方案与否定理由

| 方案 | 否决理由 |
|------|----------|
| 删除 three.core.js，只留 module | module 依赖 core，删了直接崩 |
| 合并两个文件 | 需自建打包链（rollup/esbuild），引入构建步骤与锁版本问题，与"零构建、直接起 FastAPI 就能跑"的设计冲突 |
| 用自定义构建只保留 `WebGLRenderer + Sphere/Box + MeshStandardMaterial` | 理论可到 ~400KB，但需维护构建配置、每次升级 three 都要重跑，收益（约 1.6MB 磁盘）低于维护成本；且预览要用到环境光照、色彩空间转换等零散模块，裁剪边界极易出错 |
| gzip 预压缩由服务端下发 | 本地 `file://` 与 FastAPI 静态目录场景收益有限，且增加缓存与解压路径 |

## 复评触发条件

满足任一条再考虑裁剪：

1. 包体超过 3MB（当前 0.58MB 的 .skill 里前端是主体，还有余量）；
2. three 升级到体积显著增大的版本（如引入 WebGPU 双后端）；
3. 需要把预览窗口做成离线单 HTML 分发（那时才值得上单文件打包）。
*（内容由AI生成，仅供参考）*
