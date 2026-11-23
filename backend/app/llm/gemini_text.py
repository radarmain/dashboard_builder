"""Thin text / tool-use wrapper around gemini-3.8-flash (Interactions API, stateless).

Provider-agnostic on purpose: `Message`, `ToolSpec`, `ToolCall`, `LLMResult` carry nothing
Gemini-specific except an opaque `provider_state` (the model's own steps, incl. thought
signatures) that the caller stores on the assistant message and passes back unchanged on the
next call. That lets this plug into the chat agent loop as a fallback for Bedrock later.

Implemented: `generate_text`, `generate` (one model call, optionally with tools) and
`tool_round_trip` (call -> run the tool(s) -> call again). Nothing is wired into chat/import yet.

Unlike decisions.py this DOES raise `GeminiError` (typed, message has no input text) so a
fallback caller can tell "Gemini unavailable" from a bad answer. Honors RDB_GEMINI_ENABLED.

SECURITY: only send sanitized metadata. No row values unless `row_samples_allowed()` is true
(RDB_GEMINI_ALLOW_ROW_SAMPLES), and never credentials. Nothing here logs prompts or outputs.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.llm.gemini_client import get_client
from app.llm.gemini_config import get_gemini_settings

log = logging.getLogger("rdb.llm.text")

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class GeminiError(Exception):
    """Gemini call failed or is disabled. `reason` is an exception class name or 'disabled'."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(f"gemini {reason}" + (f" (status {status})" if status else ""))
        self.reason = reason
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status in RETRYABLE_STATUS or "Timeout" in self.reason


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema object


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str = ""  # for role="tool": the JSON-encoded tool result
    tool_calls: list[ToolCall] = field(default_factory=list)  # role="assistant"
    tool_call_id: str | None = None  # role="tool"
    name: str | None = None  # role="tool": the tool's name
    provider_state: Any = None  # role="assistant": LLMResult.provider_state, passed back as is


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int | None = None  # reported separately when the SDK exposes it
    total_tokens: int = 0

    def __add__(self, o: "Usage") -> "Usage":
        think = (
            None
            if self.thinking_tokens is None and o.thinking_tokens is None
            else ((self.thinking_tokens or 0) + (o.thinking_tokens or 0))
        )
        return Usage(
            self.input_tokens + o.input_tokens,
            self.output_tokens + o.output_tokens,
            think,
            self.total_tokens + o.total_tokens,
        )


@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    latency_ms: int
    model: str
    provider: str = "gemini"
    provider_state: Any = None

    @property
    def stop_reason(self) -> Literal["end", "tool_use"]:
        return "tool_use" if self.tool_calls else "end"


def row_samples_allowed() -> bool:
    """Callers must check this before putting any row values in a prompt."""
    return get_gemini_settings().allow_row_samples


def _text_block(text: str) -> list[dict]:
    return [{"type": "text", "text": text}]


def _to_steps(messages: list[Message]) -> list[dict]:
    steps: list[dict] = []
    for m in messages:
        if m.role == "user":
            steps.append({"type": "user_input", "content": _text_block(m.text)})
        elif m.role == "assistant":
            if m.provider_state:
                steps.extend(m.provider_state)  # exactly as received (thought signatures)
            elif m.text:
                steps.append({"type": "model_output", "content": _text_block(m.text)})
        else:
            steps.append(
                {
                    "type": "function_result",
                    "name": m.name or "",
                    "call_id": m.tool_call_id or "",
                    "result": _text_block(m.text),
                }
            )
    return steps


def _usage(u: Any) -> Usage:
    if u is None:
        return Usage()
    think = getattr(u, "total_thought_tokens", None)
    return Usage(
        input_tokens=int(getattr(u, "total_input_tokens", 0) or 0),
        output_tokens=int(getattr(u, "total_output_tokens", 0) or 0),
        thinking_tokens=int(think) if think is not None else None,
        total_tokens=int(getattr(u, "total_tokens", 0) or 0),
    )


def generate(
    messages: list[Message],
    *,
    tools: list[ToolSpec] | None = None,
    system: str | None = None,
    model: str | None = None,
    thinking_level: Literal["low", "medium", "high"] = "low",  # "minimal" is rejected by 3.8
    max_output_tokens: int | None = None,
) -> LLMResult:
    """One model call. Raises GeminiError on any failure or when Gemini is disabled."""
    s = get_gemini_settings()
    client = get_client(s.text_timeout_seconds, attempts=1) if s.enabled else None
    if client is None:
        raise GeminiError("disabled")
    model = model or s.default_model
    gen_cfg: dict[str, Any] = {"thinking_level": thinking_level}
    if max_output_tokens:
        gen_cfg["max_output_tokens"] = max_output_tokens
    kwargs: dict[str, Any] = {
        "model": model,
        "input": _to_steps(messages),
        "store": False,  # stateless: we own the history, Google keeps no interaction
        "generation_config": gen_cfg,
        "timeout": s.text_timeout_seconds,
    }
    if system:
        kwargs["system_instruction"] = system
    if tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]
    started = time.perf_counter()
    try:
        it = client.interactions.create(**kwargs)
        steps = list(getattr(it, "steps", None) or [])
        calls = [
            ToolCall(str(st.id), str(st.name), dict(st.arguments or {}))
            for st in steps
            if getattr(st, "type", None) == "function_call"
        ]
        result = LLMResult(
            text=getattr(it, "output_text", None) or "",
            tool_calls=calls,
            usage=_usage(getattr(it, "usage", None)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            model=model,
            provider_state=[st.model_dump(mode="json", exclude_none=True) for st in steps],
        )
    except Exception as e:  # noqa: BLE001
        status = getattr(e, "status_code", None) or getattr(e, "code", None)
        log.warning(
            "gemini generate failed model=%s error=%s status=%s latency_ms=%d",
            model,
            type(e).__name__,
            status,
            int((time.perf_counter() - started) * 1000),
        )
        raise GeminiError(type(e).__name__, status if isinstance(status, int) else None) from None
    u = result.usage
    log.info(
        "gemini generate ok model=%s latency_ms=%d in=%d out=%d thinking=%s tool_calls=%d",
        model,
        result.latency_ms,
        u.input_tokens,
        u.output_tokens,
        u.thinking_tokens,
        len(calls),
    )
    return result


def generate_text(prompt: str, *, system: str | None = None, **kw: Any) -> LLMResult:
    """Plain text generation from one user prompt."""
    return generate([Message("user", prompt)], system=system, **kw)


def tool_round_trip(
    prompt: str,
    tools: list[ToolSpec],
    execute: Callable[[ToolCall], Any],
    *,
    system: str | None = None,
    **kw: Any,
) -> LLMResult:
    """Call -> run any requested tools via `execute(call)` (JSON-able result) -> call again.

    If the model asks for no tool, returns the first result. Usage of both calls is summed. If the
    second call asks for yet more tools they are returned unexecuted (the caller's loop decides).
    """
    user = Message("user", prompt)
    first = generate([user], tools=tools, system=system, **kw)
    if not first.tool_calls:
        return first
    msgs = [
        user,
        Message("assistant", first.text, first.tool_calls, provider_state=first.provider_state),
    ]
    for call in first.tool_calls:
        msgs.append(
            Message(
                "tool", json.dumps(execute(call), default=str), tool_call_id=call.id, name=call.name
            )
        )
    second = generate(msgs, tools=tools, system=system, **kw)
    second.usage = first.usage + second.usage
    second.latency_ms += first.latency_ms
    return second
