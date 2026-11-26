import httpx
import pytest
from pydantic import BaseModel

from sopguard import llm


class Out(BaseModel):
    ok: bool


def _gemini(handler):
    m = llm.GeminiLLM(
        "k",
        "test-model-" + str(id(handler)),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=3,
    )
    return m


def test_extract_json_variants():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('sure! {"a": [1, 2]} thanks') == {"a": [1, 2]}
    with pytest.raises(llm.LLMError):
        llm.extract_json("no json")


def test_retry_after_rate_limit_then_success(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text='{"error": {"details": [{"retryDelay": "0.01s"}]}}')
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
            },
        )

    out, usage = _gemini(handler).complete_json("t", "sys", "user", Out)
    assert out.ok and usage.attempts == 2 and usage.tokens == 5


def test_daily_quota_is_fatal_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, text="Quota exceeded: GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    with pytest.raises(llm.FatalLLMError):
        _gemini(handler).complete_json("t", "s", "u", Out)
    assert len(calls) == 1


def test_schema_repair_then_give_up():
    def handler(req):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"nope": 1}'}]}}]})

    with pytest.raises(llm.LLMError, match="failed after 3 attempts"):
        _gemini(handler).complete_json("t", "s", "u", Out)


def test_groq_malformed_generation_is_re_asked_not_fatal():
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(400, text='{"error": {"code": "json_validate_failed"}}')
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}})

    m = llm.OpenAICompatLLM(
        "k", "openai/gpt-oss-20b", "https://x", "groq", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    out, usage = m.complete_json("t", "s", "u", Out)
    assert out.ok and len(calls) == 2
    assert b"response_format" not in calls[0].content and b"reasoning_effort" in calls[0].content
