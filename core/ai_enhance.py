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

    def available(self) -> bool:
        return False

    def enhance(self, src_path: str, out_dir: str, options: dict) -> Optional[dict]:
        """返回 {通道名: 路径}，失败或不可用返回 None"""
        return None


class ScenarioProvider(BaseProvider):
    """Scenario.gg 材质生成（需 SCENARIO_API_KEY）

    预留实现位：填入官方 endpoint 与认证头即可启用。
    """
    name = "scenario"

    def available(self) -> bool:
        return bool(os.environ.get("SCENARIO_API_KEY"))

    def enhance(self, src_path, out_dir, options):
        # TODO: 接入 Scenario /v1/generate/texture 或 image-to-texture 接口
        return None


class GenericHTTPProvider(BaseProvider):
    """通用 HTTP 材质生成端点（需 MATERIAL_AI_ENDPOINT + MATERIAL_AI_KEY）

    只要外部服务返回 {"albedo": "...", "normal": "...", ...} 的 URL 或 base64，
    本方法即可把结果下载到 out_dir 并返回路径字典。
    """
    name = "generic-http"

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
    return [
        {"name": p.name, "available": p.available()}
        for p in PROVIDERS.values()
    ]


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
