"""AstrBot 内置 LLM Provider。"""

from __future__ import annotations

import json
import re
from typing import Any

from outluna.llm.base import LLMProvider


class AstrBotLLMProvider(LLMProvider):
    """通过 AstrBot 的 Context 调用其已配置的 LLM。

    适用于插件运行场景，自动复用用户在 AstrBot WebUI 中配置的模型。
    """

    def __init__(self, context: Any, event: Any):
        """初始化。

        Args:
            context: AstrBot 的 star.Context 实例。
            event: AstrBot 的 AstrMessageEvent 实例，用于获取当前 provider。
        """
        self.context = context
        self.event = event

    @property
    def available(self) -> bool:
        """AstrBot 是否已配置 LLM。"""
        try:
            prov = self.context.get_using_provider(self.event.unified_msg_origin)
            return prov is not None
        except Exception:
            return False

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: type,
        temperature: float = 0.2,
    ) -> Any:
        """调用 AstrBot LLM，并尝试将文本结果解析为结构化对象。"""
        prov = self.context.get_using_provider(self.event.unified_msg_origin)
        if prov is None:
            raise RuntimeError("AstrBot 未配置可用的 LLM Provider")

        provider_id = prov.meta().id
        # 将 Pydantic 模型 schema 追加到系统提示词，提高 JSON 输出稳定性
        enhanced_system = system_prompt
        if hasattr(response_format, "model_json_schema"):
            schema_json = json.dumps(
                response_format.model_json_schema(), ensure_ascii=False, indent=2
            )
            enhanced_system = (
                f"{system_prompt}\n\n"
                f"你必须只输出一个合法的 JSON 对象，严格符合以下 JSON Schema，\n"
                f"不要包含任何解释、markdown 代码块标记或其他内容：\n{schema_json}"
            )

        llm_resp = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=user_prompt,
            system_prompt=enhanced_system,
            temperature=temperature,
        )

        text = llm_resp.completion_text
        if not text:
            raise RuntimeError("AstrBot LLM 返回为空")

        return self._parse_response(text, response_format)

    @staticmethod
    def _parse_response(text: str, response_format: type) -> Any:
        """从 LLM 文本响应中提取 JSON 并解析为 Pydantic 模型。"""
        text_stripped = text.strip()

        # 去掉 markdown 代码块标记
        if text_stripped.startswith("```"):
            lines = text_stripped.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text_stripped = "\n".join(lines).strip()

        # 尝试直接解析
        try:
            data = json.loads(text_stripped)
            return response_format(**data)
        except json.JSONDecodeError:
            pass

        # 兜底：从文本中提取第一个 JSON 对象
        match = re.search(r"\{.*\}", text_stripped, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return response_format(**data)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"LLM 返回内容不是合法 JSON：{text[:200]}") from exc

        raise RuntimeError(f"LLM 返回内容不是合法 JSON：{text[:200]}")
