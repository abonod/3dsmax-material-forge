"""AI 增强端口（Q2 方案 C 的增强侧，可插拔）

设计意图：
    本机无 CUDA（RX 580 / Polaris），本地跑 SD 不可行，因此默认走
    texture_engine.py 的本地程序化推导。本模块只提供「可选的在线增强」
    接口，配了 API Key 才启用，失败静默回落本地结果，绝不让主流程失败。

扩展方式（新增一家服务商只需加一个 Provider 子类并注册）：
    class XxxProvider(BaseProvider):
        name = "xxx"
        def enhance(self, src_path, out_dir, options): ...
    PROVIDERS["xxx"] = XxxProvider
"""

from __future__ import annotations

import os
from typing import Optional


class BaseProvider:
    name = "base"
    requires_key = True
    # 不可用时给用户看的理由。不要留空——available() 为 False 而没有任何解释，
    # 用户只能猜「是不是 Key 配错了」，这正是本模块最容易说谎的地方（见下）。
    reason = ""

    def available(self) -> bool:
        return False

    def enhance(self, src_path: str, out_dir: str, options: dict) -> Optional[dict]:
        """返回 {通道名: 路径}，失败或不可用返回 None"""
        return None


class ScenarioProvider(BaseProvider):
    """Scenario.gg 材质生成（需 SCENARIO_API_KEY）

    预留实现位：填入官方 endpoint 与认证头即可启用。

    诚实性约束（踩过）：旧实现 available() 只看 SCENARIO_API_KEY 在不在，
    于是「配了 Key」=「可用」，而 enhance() 里只有 TODO、恒返回 None——
    用户在设置里认真填了 Key，得到的是「可用」却是静默回落，且日志里没有一行
    说明为什么。声明必须跟实现同向：接口没接完，available() 就只能是 False，
    并把原因写进 reason。等真接上了再把 available() 放开、同时补门禁。
    """
    name = "scenario"
    reason = "预留位：endpoint 与认证头尚未接入，配置 SCENARIO_API_KEY 也不会生效；当前请用 generic-http 或本地程序化推导"

    def available(self) -> bool:
        return False  # 未接入：不因环境变量存在就宣称可用

    def enhance(self, src_path, out_dir, options):
        # TODO: 接入 Scenario /v1/generate/texture 或 image-to-texture 接口
        return None


class GenericHTTPProvider(BaseProvider):
    """通用 HTTP 材质生成端点（需 MATERIAL_AI_ENDPOINT + MATERIAL_AI_KEY）

    只要外部服务返回 {"albedo": "...", "normal": "...", ...} 的 URL 或 base64，
    本方法即可把结果下载到 out_dir 并返回路径字典。
    """
    name = "generic-http"
    reason = "未配置 MATERIAL_AI_ENDPOINT（可选 MATERIAL_AI_KEY 作 Bearer）；这是目前唯一真正接好线的 provider"

    def available(self) -> bool:
        return bool(os.environ.get("MATERIAL_AI_ENDPOINT"))

    def enhance(self, src_path, out_dir, options):
        endpoint = os.environ.get("MATERIAL_AI_ENDPOINT")
        key = os.environ.get("MATERIAL_AI_KEY", "")
        if not endpoint:
            return None
        try:
            import json
            import urllib.request

            boundary = "----MaterialForgeBoundary"
            with open(src_path, "rb") as f:
                data = f.read()
            body = (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="image"; filename="{os.path.basename(src_path)}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

            req = urllib.request.Request(endpoint, data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            if key:
                req.add_header("Authorization", f"Bearer {key}")

            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.loads(resp.read().decode())

            out = {}
            for chan in ("albedo", "normal", "roughness", "height", "ao"):
                url = payload.get(chan)
                if not url:
                    continue
                p = os.path.join(out_dir, f"{chan}_ai.png")
                urllib.request.urlretrieve(url, p)
                out[chan] = p
            return out or None
        except Exception:
            return None


PROVIDERS: dict[str, BaseProvider] = {
    "scenario": ScenarioProvider(),
    "generic-http": GenericHTTPProvider(),
}


def list_available() -> list[dict]:
    """每个 provider 的可用性与不可用原因。

    reason 只在 available 为 False 时有意义，且必须与 available() 的判断同源——
    别在这里另写一套判断条件，否则又会出现「列表说可用、调用却回落」。
    """
    out = []
    for p in PROVIDERS.values():
        ok = p.available()
        out.append({"name": p.name, "available": ok,
                    "reason": "" if ok else p.reason})
    return out


def explain(provider: str = "auto") -> str:
    """人话版「为什么没生效」，供接口在回落时原文回给用户。"""
    if provider != "auto":
        p = PROVIDERS.get(provider)
        if p is None:
            return f"没有名为 {provider} 的 provider（可选：{'/'.join(PROVIDERS)}）"
        return "" if p.available() else (p.reason or f"{provider} 不可用")
    reasons = [f"{p.name}：{p.reason or '不可用'}" for p in PROVIDERS.values()
               if not p.available()]
    if reasons:
        return "；".join(reasons)
    return "已配置的服务都调不通（检查 endpoint 是否可访问、返回体是否含 albedo/normal 等通道字段）"


def enhance(src_path: str, out_dir: str, provider: str = "auto",
            options: Optional[dict] = None) -> tuple[dict, str]:
    """尝试 AI 增强，返回 (贴图路径字典, 实际使用的 provider 名)

    失败时返回 ({}, "none")，调用方应回落本地推导结果。
    """
    options = options or {}
    targets = list(PROVIDERS.values()) if provider == "auto" else [PROVIDERS.get(provider)]
    for p in targets:
        if p is None or not p.available():
            continue
        res = p.enhance(src_path, out_dir, options)
        if res:
            return res, p.name
    return {}, "none"
