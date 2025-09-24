from __future__ import annotations

from typing import Any, Dict, Sequence

from openai import AsyncOpenAI


class OpenAIClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def generate_daily_message(self, prompt_messages: Sequence[Dict[str, Any]]) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=list(prompt_messages),
            max_completion_tokens=600,
        )
        return response.choices[0].message.content.strip()
