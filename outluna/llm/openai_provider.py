"""OpenAI 直连 LLM Provider。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from outluna.config import settings
from outluna.llm.base import LLMProvider

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class OpenAILLMProvider(LLMProvider):
    """通过 OpenAI 兼容 API 调用 LLM。"""

    def __init__(self):
        self.client: AsyncOpenAI | None = None
        if settings.llm_api_key:
            try:
                from openai import AsyncOpenAI

                self.client = AsyncOpenAI(
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url or None,
                )
            except ImportError:
                pass

    @property
    def available(self) -> bool:
        """是否已配置 API Key。"""
        return self.client is not None

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: type,
        temperature: float = 0.2,
    ) -> Any:
        """调用 OpenAI 结构化输出。"""
        if not self.client:
            raise RuntimeError("OpenAI LLM Provider 未初始化，请检查 OUTLUNA_LLM_API_KEY")

        response = await self.client.beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            response_format=response_format,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("LLM 未返回结构化内容")
        return parsed
