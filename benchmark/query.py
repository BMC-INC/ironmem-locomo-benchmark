"""Query phase: retrieve context from IronMem, then answer with Gemini."""
from __future__ import annotations

from .config import Config
from .gemini import GeminiClient
from .ironmem_client import IronMemClient

ANSWERER_PROMPT = """You are answering a question about a person based on their conversation history.
Use ONLY the provided context to answer. If the context does not contain enough
information, say "I don't have enough information to answer this question."

Context:
{context}

Question: {question}

Answer concisely and factually."""


def build_context(memories: list[dict]) -> str:
    """Render retrieved IronMem memories into a numbered context block.

    Each memory's text lives in its `summary` field (IronMem stores the
    compressed session memory there).
    """
    lines: list[str] = []
    for i, m in enumerate(memories, 1):
        summary = (m.get("summary") or "").strip()
        if not summary:
            continue
        tags = (m.get("tags") or "").strip()
        suffix = f"  (tags: {tags})" if tags else ""
        lines.append(f"[{i}] {summary}{suffix}")
    return "\n".join(lines)


async def answer_question(gemini: GeminiClient, cfg: Config, question: str, context_text: str) -> str:
    prompt = ANSWERER_PROMPT.format(
        context=context_text or "(no relevant memories found)",
        question=question,
    )
    return await gemini.generate(
        cfg.answerer_model,
        prompt,
        max_output_tokens=cfg.answerer_max_tokens,
        thinking_budget=cfg.answerer_thinking_budget,
    )


async def retrieve_and_answer(
    client: IronMemClient,
    gemini: GeminiClient,
    cfg: Config,
    project: str,
    question: str,
) -> tuple[str, str, list[dict]]:
    """Returns (generated_answer, retrieved_context_text, raw_memories)."""
    memories = await client.get_context(project, query=question, limit=cfg.retrieve_limit)
    context_text = build_context(memories)
    answer = await answer_question(gemini, cfg, question, context_text)
    return answer, context_text, memories
