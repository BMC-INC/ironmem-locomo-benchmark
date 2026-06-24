"""Vertex AI Gemini backend for the LoCoMo harness.

Replaces the Anthropic (answerer/Claude-judge) and OpenAI (gpt-4o-judge) SDK
calls with a single Vertex AI Gemini path via the google-genai SDK.

Auth is Application Default Credentials:
    gcloud auth application-default login --project=<project>

Gemini 2.5 models are "thinking" models. For 2.5 Pro thinking cannot be fully
disabled (min budget 128), so every call leaves headroom in max_output_tokens
for the hidden thinking tokens plus the visible answer. Token budgets per call
type live in Config and are tunable from the CLI if needed.
"""
from __future__ import annotations

import asyncio

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .config import Config

# HTTP status codes worth retrying (transient): rate limit + server-side.
_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


class GeminiClient:
    """Async Vertex AI Gemini client with bounded retry/backoff."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = genai.Client(
            vertexai=True,
            project=cfg.vertex_project,
            location=cfg.vertex_location,
        )

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        max_output_tokens: int,
        temperature: float = 0.0,
        thinking_budget: int | None = None,
    ) -> str:
        kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if thinking_budget is not None:
            # include_thoughts=False -> we never need the thought text back.
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget, include_thoughts=False
            )
        config = types.GenerateContentConfig(**kwargs)

        last: Exception | None = None
        for attempt in range(self._cfg.max_retries):
            try:
                resp = await self._client.aio.models.generate_content(
                    model=model, contents=prompt, config=config
                )
                return _extract_text(resp)
            except genai_errors.APIError as exc:
                if getattr(exc, "code", None) in _RETRYABLE:
                    last = exc
                    await self._sleep(attempt)
                    continue
                raise
            except (asyncio.TimeoutError, ConnectionError) as exc:
                last = exc
                await self._sleep(attempt)
                continue
        raise RuntimeError(
            f"Gemini call to {model} failed after {self._cfg.max_retries} attempts: {last}"
        )

    async def _sleep(self, attempt: int) -> None:
        delay = min(self._cfg.backoff_cap, self._cfg.backoff_base ** attempt)
        await asyncio.sleep(delay)


def _extract_text(resp) -> str:
    """Pull visible text out of a response.

    response.text raises (or returns None) when the only parts are thinking
    parts or the candidate was blocked, so fall back to scanning parts and,
    finally, returning "" so one bad answer scores 0 rather than crashing the run.
    """
    try:
        txt = resp.text
        if txt:
            return txt.strip()
    except Exception:
        pass
    try:
        for cand in resp.candidates or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "thought", False):
                    continue
                t = getattr(part, "text", None)
                if t:
                    return t.strip()
    except Exception:
        pass
    return ""
