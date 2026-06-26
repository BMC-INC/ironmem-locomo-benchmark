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
import random

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

    async def _call_with_retry(self, model: str, contents, config):
        """generate_content with the same bounded retry/backoff as generate()."""
        last: Exception | None = None
        for attempt in range(self._cfg.max_retries):
            try:
                return await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
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
            f"Gemini agentic call to {model} failed after {self._cfg.max_retries} attempts: {last}"
        )

    async def generate_agentic(
        self,
        model: str,
        *,
        system_instruction: str,
        user_text: str,
        tool: "types.Tool",
        tool_fns: dict,
        max_output_tokens: int,
        temperature: float = 0.0,
        thinking_budget: int | None = None,
        max_steps: int = 4,
    ) -> tuple[str, int]:
        """Run a tool-use loop: the model may call functions declared in `tool`;
        `tool_fns` maps name -> async callable(args: dict) -> str. Returns
        (final_text, n_tool_calls). On step exhaustion, forces a tool-less answer."""

        def mk_config(with_tools: bool):
            kw: dict = {
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "system_instruction": system_instruction,
            }
            if thinking_budget is not None:
                kw["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=thinking_budget, include_thoughts=False
                )
            if with_tools:
                kw["tools"] = [tool]
            return types.GenerateContentConfig(**kw)

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
        cfg_tools = mk_config(True)
        n_calls = 0
        for _step in range(max_steps):
            resp = await self._call_with_retry(model, contents, cfg_tools)
            cand = (resp.candidates or [None])[0]
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) or []
            calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
            if not calls:
                return _extract_text(resp), n_calls
            contents.append(content)  # the model's function-call turn
            out_parts = []
            for fc in calls:
                n_calls += 1
                args = dict(fc.args) if fc.args else {}
                fn = tool_fns.get(fc.name)
                try:
                    result = await fn(args) if fn else f"ERROR: unknown tool {fc.name}"
                except Exception as exc:  # never let a tool error kill the answer
                    result = f"ERROR: {exc}"
                out_parts.append(
                    types.Part.from_function_response(name=fc.name, response={"output": result})
                )
            contents.append(types.Content(role="user", parts=out_parts))
        # Step budget exhausted — force a final answer with tools disabled.
        resp = await self._call_with_retry(model, contents, mk_config(False))
        return _extract_text(resp), n_calls

    async def _sleep(self, attempt: int) -> None:
        # Equal jitter: a floor wait that grows with attempt, plus a random half.
        # The jitter desynchronizes many concurrent retries so they don't slam
        # the shared quota in lockstep (which is what caused mass 429 failures).
        ceiling = min(self._cfg.backoff_cap, self._cfg.backoff_base ** attempt)
        delay = ceiling / 2 + random.uniform(0, ceiling / 2)
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
