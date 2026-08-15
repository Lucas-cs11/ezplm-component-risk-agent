"""
custom_llm.py - 兼容 manbouapi.com 的 LLM 驱动

处理 manbouapi.com 的非标准 OpenAI API 格式
"""

import json
import requests
from typing import List, Dict, Any, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable


class ManbouOpenAI(BaseChatModel):
    """Manbou API compatible LLM wrapper with tool support"""

    model: str = "gpt-4o-mini"
    api_key: str
    base_url: str = "https://www.manbouapi.com"
    temperature: float = 0.7
    max_tokens: int = 2048

    @property
    def _llm_type(self) -> str:
        return "manbou_openai"

    def bind_tools(
        self,
        tools: List[Dict[str, Any]] | List[Any],
        tool_choice: Optional[str | Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        """Support tool binding - delegate to parent's default implementation"""
        # Create a wrapper runnable that handles tool calls
        from langchain_core.runnables import RunnablePassthrough
        return RunnablePassthrough() | self

    def _call(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        """Call the LLM synchronously"""
        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return result.generations[0][0].text

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate chat response"""
        # Convert langchain messages to OpenAI format
        api_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                api_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                api_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                api_messages.append({"role": "assistant", "content": msg.content})

        # Prepare request
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        data = {
            "model": self.model,
            "messages": api_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if stop:
            data["stop"] = stop

        # Make request to manbouapi
        try:
            # Try with /v1/chat/completions first (standard OpenAI endpoint)
            url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
            response = requests.post(url, json=data, headers=headers, timeout=60)
            response.raise_for_status()

            result_data = response.json()

            # Extract the response text
            if "choices" in result_data and len(result_data["choices"]) > 0:
                content = result_data["choices"][0].get("message", {}).get("content", "")
            else:
                # Fallback for non-standard responses
                content = str(result_data)

            # Create ChatGeneration
            generation = ChatGeneration(
                message=AIMessage(content=content),
                generation_info={"finish_reason": "stop"},
            )
            return ChatResult(generations=[generation])

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to call LLM API: {e}")
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"Failed to parse LLM response: {e}")

