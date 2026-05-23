"""Shared model-calling utility for Tool Agents."""

import asyncio
import inspect
import json
from typing import Callable

from src.models import get_backend

TA_DEFAULT_MODEL = "omlx:mlx-community/Qwen3-VL-4B-Instruct-MLX-4bit"


async def run_ta_model(question: str, system_prompt: str, ta_session) -> str:
    """Stream a TA model response to the TA pane, return the full answer text."""
    pa_config = ta_session.pa_session.config
    model = pa_config.get("ta_model", TA_DEFAULT_MODEL)
    ta_config = {
        "model": model,
        "thinking": False,
        "mlx_url": pa_config.get("mlx_url", "http://localhost:8000/v1/chat/completions"),
    }
    backend = get_backend(model)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    ta_id = ta_session.session_id
    agent = ta_session.agent_name
    answer = ""
    try:
        async for chunk in backend.stream_chat(messages, ta_config):
            if not chunk.startswith("\x00THINK\x00"):
                answer += chunk
                await ta_session.send({
                    "type": "ta_message", "ta_session_id": ta_id, "agent_name": agent,
                    "role": "answer", "text": chunk, "partial": True,
                })
    except Exception as e:
        return f"Error: {e}"
    await ta_session.send({
        "type": "ta_message", "ta_session_id": ta_id, "agent_name": agent,
        "role": "answer", "text": "", "partial": False,
    })
    return answer.strip()


async def run_ta_with_tools(
    question: str,
    system_prompt: str,
    tools: list[dict],
    tool_handlers: dict[str, Callable],
    ta_session,
) -> str:
    """Run a tool-calling loop, then stream the final answer.

    The model calls tools (MCP-style function calling) to gather data, then
    produces a natural-language answer which is streamed to the TA pane.
    """
    pa_config = ta_session.pa_session.config
    model = pa_config.get("ta_tools_model", "openai:gpt-4o-mini")
    ta_config = {
        "model": model,
        "mlx_url": pa_config.get("mlx_url", "http://localhost:8000/v1/chat/completions"),
    }
    backend = get_backend(model)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    for _ in range(5):
        try:
            response = await backend.chat(messages, ta_config, tools=tools)
        except Exception as e:
            return f"Error calling model: {e}"

        choice = response["choices"][0]
        msg = choice["message"]
        finish_reason = choice.get("finish_reason", "stop")

        if finish_reason != "tool_calls":
            content = msg.get("content") or ""
            ta_id = ta_session.session_id
            agent = ta_session.agent_name
            await ta_session.send({
                "type": "ta_message", "ta_session_id": ta_id, "agent_name": agent,
                "role": "answer", "text": content, "partial": True,
            })
            await ta_session.send({
                "type": "ta_message", "ta_session_id": ta_id, "agent_name": agent,
                "role": "answer", "text": "", "partial": False,
            })
            return content.strip()

        # Execute each tool call and append results
        messages.append(msg)
        for tc in msg.get("tool_calls", []):
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                fn_args = {}
            handler = tool_handlers.get(fn_name)
            if handler is None:
                result = f"Unknown tool: {fn_name}"
            else:
                try:
                    result = await handler(**fn_args) if inspect.iscoroutinefunction(handler) else handler(**fn_args)
                except Exception as e:
                    result = f"Tool error: {e}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": str(result),
            })

    return "Error: tool-calling loop limit reached"
