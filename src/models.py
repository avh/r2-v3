import os
import json
from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx


class Backend(ABC):
    @abstractmethod
    async def stream_chat(self, messages: list[dict], config: dict) -> AsyncIterator[str]:
        ...

    @abstractmethod
    async def chat(self, messages: list[dict], config: dict, tools: list[dict] | None = None) -> dict:
        ...


class OmlxBackend(Backend):
    async def stream_chat(self, messages: list[dict], config: dict) -> AsyncIterator[str]:
        url = config.get("mlx_url", "http://localhost:8000/v1/chat/completions")
        api_key = os.environ.get("MLX_API_KEY", "")
        model_name = config["model"].split(":", 1)[1]

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
        }
        if config.get("thinking"):
            payload["thinking"] = True
            if config.get("budget_tokens"):
                payload["budget_tokens"] = config["budget_tokens"]

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    # thinking content arrives in a separate field on some MLX servers
                    thinking = delta.get("thinking") or delta.get("reasoning_content")
                    if thinking:
                        yield f"\x00THINK\x00{thinking}"
                    text = delta.get("content") or ""
                    if text:
                        yield text

    async def chat(self, messages: list[dict], config: dict, tools: list[dict] | None = None) -> dict:
        url = config.get("mlx_url", "http://localhost:8000/v1/chat/completions")
        api_key = os.environ.get("MLX_API_KEY", "")
        model_name = config["model"].split(":", 1)[1]
        payload: dict = {"model": model_name, "messages": messages}
        if tools:
            payload["tools"] = tools
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()


class OpenAIBackend(Backend):
    async def stream_chat(self, messages: list[dict], config: dict) -> AsyncIterator[str]:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        model_name = config["model"].split(":", 1)[1] if ":" in config["model"] else config["model"]
        url = "https://api.openai.com/v1/chat/completions"

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
        }

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content") or ""
                    if text:
                        yield text

    async def chat(self, messages: list[dict], config: dict, tools: list[dict] | None = None) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        model_name = config["model"].split(":", 1)[1] if ":" in config["model"] else config["model"]
        url = "https://api.openai.com/v1/chat/completions"
        payload: dict = {"model": model_name, "messages": messages}
        if tools:
            payload["tools"] = tools
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()


class OllamaBackend(Backend):
    async def stream_chat(self, messages: list[dict], config: dict) -> AsyncIterator[str]:
        raise NotImplementedError("Ollama backend not yet implemented")

    async def chat(self, messages: list[dict], config: dict, tools: list[dict] | None = None) -> dict:
        raise NotImplementedError("Ollama backend not yet implemented")


def get_backend(model_string: str) -> Backend:
    prefix = model_string.split(":")[0].lower()
    if prefix == "omlx":
        return OmlxBackend()
    if prefix == "openai":
        return OpenAIBackend()
    if prefix == "ollama":
        return OllamaBackend()
    # default: treat bare model names as openai-compatible
    return OpenAIBackend()
