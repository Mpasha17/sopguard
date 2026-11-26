"""Small, dependency-light LLM client layer used by the semantic judge, the demo agent and the simulator.

Providers: Gemini (default for evals, generous free tier), any OpenAI-compatible endpoint (Groq, Ollama,
OpenRouter, vLLM ...), and :class:`FakeLLM` - a deterministic offline provider so tests and the demo run
without keys. Every call returns ``(validated_pydantic_object, Usage)``; no free-form text leaks through.

Failures are loud: after retries a provider raises :class:`LLMError`. Callers decide what that means
(the supervisor applies the rule's fail mode; the eval marks the scenario *degraded* and excludes it,
so a fallback can never silently pass as an LLM result).
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens (input, output) at paid list price - used only to *estimate* cost; free tier costs $0.
PRICES = {
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-flash-lite-latest": (0.10, 0.40),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "llama-3.1-8b-instant": (0.05, 0.08),
}


def estimate_cost(model: str, tin: int, tout: int) -> float:
    pin, pout = PRICES.get(model, (0.10, 0.40))
    return (tin * pin + tout * pout) / 1e6


@dataclass
class Usage:
    provider: str = "none"
    model: str = "-"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    attempts: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


class LLMError(RuntimeError):
    pass


class RetryableLLMError(LLMError):
    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class FatalLLMError(LLMError):
    pass


def extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise LLMError(f"no JSON in model output: {text[:120]!r}")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    return json.loads(text[start : end + 1])


class LLM:
    name = "base"
    model = "-"

    def complete_json(
        self, step: str, system: str, user: str, schema: type[T], context: dict[str, Any] | None = None
    ) -> tuple[T, Usage]:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name}:{self.model}"

    @property
    def is_fake(self) -> bool:
        return False


Responder = Callable[[str, str, str, type[BaseModel], dict[str, Any]], BaseModel]


class FakeLLM(LLM):
    """Deterministic offline provider. ``responder`` produces the structured answer from the call context."""

    name = "fake"

    def __init__(self, responder: Responder, model: str = "fake-v1") -> None:
        self.responder = responder
        self.model = model

    @property
    def is_fake(self) -> bool:
        return True

    def complete_json(self, step, system, user, schema, context=None):  # type: ignore[override]
        t0 = time.perf_counter()
        out = self.responder(step, system, user, schema, context or {})
        if not isinstance(out, schema):
            out = schema.model_validate(out)
        u = Usage(
            provider="fake",
            model=self.model,
            input_tokens=(len(system) + len(user)) // 4,
            output_tokens=len(out.model_dump_json()) // 4,
            latency_ms=(time.perf_counter() - t0) * 1000,
            attempts=1,
        )
        return out, u  # type: ignore[return-value]


class _RateLimiter:
    """Polite client-side pacing: at most ``rpm`` requests per minute per provider, shared across threads."""

    def __init__(self, rpm: float) -> None:
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)

    def penalize(self, seconds: float) -> None:
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


class _HTTPLLM(LLM):
    def __init__(
        self, api_key: str, model: str, timeout: float = 45.0, rpm: float = 0, max_retries: int = 8,
        client: httpx.Client | None = None,
    ) -> None:  # fmt: skip
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.client = client or httpx.Client(timeout=timeout)
        self.limiter = _RateLimiter(rpm)

    def _call(self, system: str, user: str) -> tuple[str, int, int]:
        raise NotImplementedError

    def complete_json(self, step, system, user, schema, context=None):  # type: ignore[override]
        usage = Usage(provider=self.name, model=self.model)
        prompt = user
        last: Exception | None = None
        t_start = time.perf_counter()
        for attempt in range(1, self.max_retries + 1):
            usage.attempts = attempt
            self.limiter.wait()
            try:
                text, tin, tout = self._call(system, prompt)
                usage.input_tokens += tin
                usage.output_tokens += tout
                obj = schema.model_validate(extract_json(text))
                usage.latency_ms = (time.perf_counter() - t_start) * 1000
                return obj, usage
            except FatalLLMError:
                raise
            except RetryableLLMError as e:
                last = e
                wait = e.retry_after if e.retry_after else min(60.0, 2.0 * 2**attempt) + random.random()
                self.limiter.penalize(wait)
                time.sleep(wait)
            except (ValidationError, json.JSONDecodeError, LLMError) as e:
                last = e
                prompt = (
                    f"{user}\n\nYour previous answer was invalid: {str(e)[:300]}\n"
                    f"Return ONLY a JSON object with fields {list(schema.model_fields)}."
                )
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                time.sleep(min(20.0, 1.0 * 2**attempt))
        raise LLMError(f"{self.describe()} failed after {self.max_retries} attempts: {last!r}"[:600])

    @staticmethod
    def _check(r: httpx.Response) -> None:
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("retry-after")
            m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', r.text)
            delay = float(ra) if ra and ra.replace(".", "").isdigit() else (float(m.group(1)) if m else None)
            if r.status_code == 429 and ("PerDay" in r.text or "per day" in r.text.lower()):
                raise FatalLLMError(f"daily quota exhausted: HTTP 429 {r.text[:200]}")
            raise RetryableLLMError(f"HTTP {r.status_code}: {r.text[:200]}", delay)
        if r.status_code >= 400:
            raise FatalLLMError(f"HTTP {r.status_code}: {r.text[:300]}")


class GeminiLLM(_HTTPLLM):
    name = "gemini"

    def _call(self, system: str, user: str) -> tuple[str, int, int]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        r = self.client.post(url, json=body, headers={"x-goog-api-key": self.api_key}, timeout=self.timeout)
        self._check(r)
        data = r.json()
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as e:
            raise LLMError(f"gemini: empty candidate {str(data)[:200]}") from e
        u = data.get("usageMetadata") or {}
        return text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)


class OpenAICompatLLM(_HTTPLLM):
    def __init__(self, api_key: str, model: str, base_url: str, name: str = "openai-compat", **kw: Any) -> None:
        super().__init__(api_key, model, **kw)
        self.base_url = base_url.rstrip("/")
        self.name = name

    def _call(self, system: str, user: str) -> tuple[str, int, int]:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        r = self.client.post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        self._check(r)
        data = r.json()
        u = data.get("usage") or {}
        return data["choices"][0]["message"]["content"] or "", u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def get_llm(spec: str, fake: Responder | None = None, rpm: float | None = None) -> LLM:
    """Build a client from a spec: ``fake`` | ``gemini[:model]`` | ``groq[:model]`` | ``ollama[:model]``."""
    kind, _, model = spec.partition(":")
    kind = kind.lower()
    rpm = float(os.getenv("SOPGUARD_RPM", "0")) if rpm is None else rpm
    if kind == "fake":
        if fake is None:
            raise LLMError("fake LLM requested without a responder")
        return FakeLLM(fake, model=f"fake-{getattr(fake, '__name__', 'responder').replace('_responder', '')}")
    if kind == "gemini":
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY not set")
        return GeminiLLM(key, model or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"), rpm=rpm)
    if kind == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY not set")
        return OpenAICompatLLM(key, model or "openai/gpt-oss-20b", "https://api.groq.com/openai/v1", "groq", rpm=rpm)
    if kind == "ollama":
        return OpenAICompatLLM(
            "ollama", model or "qwen2.5:3b", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"), "ollama",
            timeout=180, rpm=rpm,
        )  # fmt: skip
    raise LLMError(f"unknown LLM spec {spec!r}")
