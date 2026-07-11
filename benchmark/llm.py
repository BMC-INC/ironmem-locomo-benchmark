"""LLM backend selection shared by benchmark entrypoints."""
from __future__ import annotations

import os

from .config import Config
from .gemini import GeminiClient
from .openai_client import OpenAIClient


def selected_backend() -> str:
    return (os.environ.get("BENCH_LLM") or "vertex").strip().lower()


def configure_models(cfg: Config, backend: str) -> None:
    if backend != "openai":
        return
    if cfg.answerer_model.startswith("gemini"):
        cfg.answerer_model = os.environ.get("OPENAI_ANSWERER_MODEL", "gpt-4.1")
    if cfg.judge_model.startswith("gemini"):
        cfg.judge_model = os.environ.get("OPENAI_JUDGE_MODEL", "gpt-4.1")


def create_llm_client(cfg: Config, backend: str | None = None):
    backend = backend or selected_backend()
    configure_models(cfg, backend)
    if backend == "vertex":
        return GeminiClient(cfg)
    if backend == "openai":
        return OpenAIClient(cfg)
    raise ValueError(f"Unsupported BENCH_LLM={backend!r}; expected 'vertex' or 'openai'")


async def close_llm_client(client) -> None:
    close = getattr(client, "aclose", None)
    if close is not None:
        await close()
