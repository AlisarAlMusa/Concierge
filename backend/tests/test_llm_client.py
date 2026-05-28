"""Unit tests for GroqLLMClient.

Mocked ``AsyncGroq.chat.completions.create`` only. No network. Validates the
provider boundary, the retry policy, and the response-normalization contract
without coupling to live Groq infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from groq import APIConnectionError, APIError, APIStatusError

from app.core.errors import ExternalServiceError
from app.services.agent_service import LLMResponse
from app.services.llm_client import GroqLLMClient


# ----- Minimal Groq response object surface ---------------------------------
@dataclass
class _Fn:
    name: str
    arguments: str  # JSON string per OpenAI/Groq contract


@dataclass
class _ToolCall:
    id: str
    function: _Fn
    type: str = "function"


@dataclass
class _Msg:
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Completion:
    choices: list[_Choice]


# ----- Fake AsyncGroq client ------------------------------------------------
class _FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeCompletions ran out of queued responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


@dataclass
class _FakeAsyncGroq:
    chat: _FakeChat = field(init=False)
    completions: _FakeCompletions = field(init=False)

    def __init__(self, responses: list[Any]) -> None:
        self.completions = _FakeCompletions(responses)
        self.chat = _FakeChat(self.completions)


# ----- Helpers --------------------------------------------------------------
def _client(
    responses: list[Any],
    *,
    max_attempts: int = 3,
) -> tuple[GroqLLMClient, _FakeAsyncGroq]:
    fake = _FakeAsyncGroq(responses)
    client = GroqLLMClient(
        client=fake,  # type: ignore[arg-type]
        model="llama-3.3-70b-versatile",
        max_attempts=max_attempts,
        backoff_base_seconds=0.0,  # no sleeping in tests
        backoff_max_seconds=0.0,
    )
    return client, fake


def _text_completion(content: str | None) -> _Completion:
    return _Completion(choices=[_Choice(message=_Msg(content=content))])


def _tool_call_completion(
    *calls: tuple[str, str, str],
    content: str | None = None,
) -> _Completion:
    tool_calls = [_ToolCall(id=cid, function=_Fn(name=n, arguments=a)) for (cid, n, a) in calls]
    return _Completion(choices=[_Choice(message=_Msg(content=content, tool_calls=tool_calls))])


def _api_status_error(status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://api.groq.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return APIStatusError(message=f"http {status_code}", response=response, body=None)


def _api_connection_error() -> APIConnectionError:
    request = httpx.Request("POST", "https://api.groq.test/v1/chat/completions")
    return APIConnectionError(request=request)


_MESSAGES = [{"role": "user", "content": "hi"}]
_TOOLS = [
    {
        "type": "function",
        "function": {"name": "rag_search", "description": "x", "parameters": {}},
    }
]


# ----- Response normalization -----------------------------------------------
async def test_basic_text_response_normalizes_to_llm_response():
    client, fake = _client([_text_completion("Hello!")])

    result = await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=100)

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello!"
    assert result.tool_calls == []
    # The agent's tools/messages are passed through unchanged.
    sent = fake.completions.calls[0]
    assert sent["messages"] == _MESSAGES
    assert sent["tools"] == _TOOLS
    assert sent["tool_choice"] == "auto"
    assert sent["max_tokens"] == 100
    assert sent["model"] == "llama-3.3-70b-versatile"


async def test_tool_call_response_normalizes_arguments_as_string():
    client, _ = _client([_tool_call_completion(("call_1", "rag_search", '{"query": "hours"}'))])

    result = await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=100)

    assert result.content is None
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "rag_search"
    # Arguments stays a JSON string — ToolRegistry decodes it.
    assert tc.arguments == '{"query": "hours"}'


async def test_text_with_tool_calls_normalizes_both():
    """Some providers emit both content and tool_calls; both must be preserved."""
    client, _ = _client(
        [
            _tool_call_completion(
                ("call_1", "rag_search", "{}"),
                content="thinking...",
            )
        ]
    )

    result = await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=100)

    assert result.content == "thinking..."
    assert len(result.tool_calls) == 1


async def test_empty_tools_omits_tool_choice():
    """When the caller passes no tools, don't send tool_choice to the provider."""
    client, fake = _client([_text_completion("plain")])

    await client.tool_complete(messages=_MESSAGES, tools=[], max_tokens=50)

    sent = fake.completions.calls[0]
    assert "tools" not in sent
    assert "tool_choice" not in sent


# ----- Retry policy ---------------------------------------------------------
async def test_retry_on_connection_error_then_success():
    client, fake = _client(
        [_api_connection_error(), _text_completion("ok")],
        max_attempts=3,
    )

    result = await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert result.content == "ok"
    assert len(fake.completions.calls) == 2  # one retry consumed


async def test_retry_on_5xx_then_success():
    client, fake = _client(
        [_api_status_error(503), _text_completion("ok")],
        max_attempts=3,
    )

    result = await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert result.content == "ok"
    assert len(fake.completions.calls) == 2


async def test_retry_on_429_then_success():
    client, fake = _client(
        [_api_status_error(429), _text_completion("ok")],
        max_attempts=3,
    )

    result = await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert result.content == "ok"
    assert len(fake.completions.calls) == 2


async def test_max_attempts_exhausted_raises_external_service_error():
    client, fake = _client(
        [_api_connection_error(), _api_connection_error(), _api_connection_error()],
        max_attempts=3,
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert "max retries" in str(excinfo.value)
    assert len(fake.completions.calls) == 3


async def test_400_not_retried_raises_external_service_error():
    """Non-retryable 4xx surfaces immediately — bad request is deterministic."""
    client, fake = _client(
        [_api_status_error(400), _text_completion("never reached")],
        max_attempts=3,
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert "HTTP 400" in str(excinfo.value)
    assert len(fake.completions.calls) == 1  # no retry


async def test_401_not_retried_raises_external_service_error():
    """Auth failures must not burn through the retry budget."""
    client, fake = _client([_api_status_error(401)], max_attempts=3)

    with pytest.raises(ExternalServiceError):
        await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert len(fake.completions.calls) == 1


async def test_unexpected_exception_translates_to_external_service_error():
    """Any unknown failure mode is wrapped so the orchestrator's contract holds."""
    client, fake = _client([RuntimeError("boom")], max_attempts=3)

    with pytest.raises(ExternalServiceError) as excinfo:
        await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert "boom" in str(excinfo.value)
    assert len(fake.completions.calls) == 1  # not retried


async def test_other_api_error_not_retried():
    """Generic APIError (e.g. malformed response) is not retried."""

    class _OddApiError(APIError):
        def __init__(self) -> None:
            self.message = "odd"
            self.request = httpx.Request("POST", "https://api.groq.test")
            Exception.__init__(self, "odd")
            self.body = None

    client, fake = _client([_OddApiError()], max_attempts=3)

    with pytest.raises(ExternalServiceError):
        await client.tool_complete(messages=_MESSAGES, tools=_TOOLS, max_tokens=10)

    assert len(fake.completions.calls) == 1


# ----- Constructor validation ----------------------------------------------
def test_invalid_max_attempts_raises():
    fake = _FakeAsyncGroq([])
    with pytest.raises(ValueError):
        GroqLLMClient(client=fake, model="m", max_attempts=0)  # type: ignore[arg-type]


def test_invalid_backoff_raises():
    fake = _FakeAsyncGroq([])
    with pytest.raises(ValueError):
        GroqLLMClient(client=fake, model="m", backoff_base_seconds=-1.0)  # type: ignore[arg-type]
