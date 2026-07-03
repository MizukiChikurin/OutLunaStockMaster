"""LLM Provider 抽象层。

统一 OpenAI 直连与 AstrBot 内置 LLM 的调用方式，
使选股要求解析器不依赖具体 LLM 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """LLM 提供商抽象基类。"""

    @abstractmethod
    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: type,
        temperature: float = 0.2,
    ) -> Any:
        """调用 LLM 生成结构化输出。

        Args:
            system_prompt: 系统提示词。
            user_prompt: 用户提示词。
            response_format: 期望返回的结构化类型（Pydantic BaseModel）。
            temperature: 采样温度。

        Returns:
            response_format 类型的实例。
        """

    @property
    @abstractmethod
    def available(self) -> bool:
        """返回当前 Provider 是否可用。"""
