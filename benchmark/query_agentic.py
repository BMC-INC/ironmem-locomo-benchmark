"""Track A — agentic answerer (the 'true capability' measure).

Instead of single-shot answering from lossy summaries (see query.py), this gives
the model a `retrieve_original(chunk_id)` tool so it can fetch the VERBATIM
original behind a compressed fact before committing to an answer. It tests how
many of the '~280 gold-present-but-wrong' failures are summary-blur vs reasoning.

`retrieve_and_answer_agentic` mirrors `query.retrieve_and_answer` (plus an extra
n_tool_calls in the return) for a clean A/B against the single-shot baseline.
"""
from __future__ import annotations

from google.genai import types

from .config import Config
from .gemini import GeminiClient
from .ironmem_client import IronMemClient

AGENTIC_SYSTEM = """You are answering a question about a person based on their conversation history.
You are given memory facts retrieved from that history. Each fact carries a chunk_id.
These facts are COMPRESSED summaries and may omit exact dates, names, ordering, or identifiers.

Rules:
- For any temporal question (dates, durations, ordering, "when"/"how long") or multi-hop
  question (chaining facts across events), if the summarized facts leave ANY ambiguity about
  a date, sequence, or specific identifier, you MUST call retrieve_original(chunk_id=...) on the
  most relevant chunk(s) to read the verbatim original BEFORE answering.
- Use ONLY the provided context plus any originals you retrieve. If the answer is still not
  present after retrieval, say "I don't have enough information to answer this question."
- Answer concisely and factually."""

RETRIEVE_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="retrieve_original",
            description=(
                "Fetch the verbatim, uncompressed original text behind a memory chunk_id, "
                "to verify exact dates, names, ordering, or identifiers before answering."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "chunk_id": types.Schema(
                        type=types.Type.STRING,
                        description="A chunk_id from the provided context, e.g. 'mem:142:fact:3'.",
                    )
                },
                required=["chunk_id"],
            ),
        )
    ]
)


def build_agentic_context(memories: list[dict], expansions: list[dict]) -> str:
    """Render retrieved facts WITH their chunk_ids so the model knows what it can
    expand. Prefer per-memory expansion chunks (they carry chunk_id); fall back to
    bare memory summaries when a memory has no chunks."""
    lines: list[str] = []
    i = 0
    for exp in expansions or []:
        for ch in exp.get("chunks", []):
            cid = ch.get("chunk_id")
            text = (ch.get("summary") or ch.get("title") or "").strip()
            if not cid or not text:
                continue
            i += 1
            lines.append(f"[{i}] (chunk_id={cid}) {text}")
    if not lines:
        for j, m in enumerate(memories or [], 1):
            s = (m.get("summary") or "").strip()
            if s:
                lines.append(f"[{j}] {s}")
    return "\n".join(lines)


async def retrieve_and_answer_agentic(
    client: IronMemClient,
    gemini: GeminiClient,
    cfg: Config,
    project: str,
    question: str,
) -> tuple[str, str, list[dict], int]:
    """Returns (generated_answer, retrieved_context_text, raw_memories, n_tool_calls)."""
    resp = await client.get_context_full(project, query=question, limit=cfg.retrieve_limit)
    memories = resp.get("memories", [])
    expansions = resp.get("expansions", [])
    context_text = build_agentic_context(memories, expansions)

    async def _retrieve_original(args: dict) -> str:
        cid = args.get("chunk_id")
        if not cid:
            return "ERROR: missing chunk_id"
        try:
            r = await client.retrieve_original(chunk_id=cid)
        except Exception as exc:
            return f"ERROR: {exc}"
        orig = (r.get("original") or "").strip()
        return orig[:4000] if orig else "(no original text found for that chunk_id)"

    user_text = (
        "Context (compressed facts with chunk_ids):\n"
        f"{context_text or '(no relevant memories found)'}\n\n"
        f"Question: {question}"
    )
    answer, n_calls = await gemini.generate_agentic(
        cfg.answerer_model,
        system_instruction=AGENTIC_SYSTEM,
        user_text=user_text,
        tool=RETRIEVE_TOOL,
        tool_fns={"retrieve_original": _retrieve_original},
        max_output_tokens=cfg.answerer_max_tokens,
        thinking_budget=cfg.answerer_thinking_budget,
        max_steps=4,
    )
    return answer, context_text, memories, n_calls
