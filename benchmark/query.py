"""Query phase: retrieve context from IronMem, then answer with Gemini."""
from __future__ import annotations

import asyncio
import json
import re

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

ANSWERER_PROMPT_V2 = """You are answering a question about people from their conversation history,
using the numbered context as evidence.

Rules:
1. Answer directly and COMPLETELY. Include every part of the answer the question
   asks for and every qualifier the answer key would contain (who, what, for whom,
   and any "but ..." / "and ..." follow-on clause), not only the first or most
   obvious part. Do not pad with facts the question did not ask about.
2. List questions ("what activities / books / things does X ..."): give EVERY
   matching item from the context, comma-separated. Omit none; invent none.
3. "When" / date questions: resolve relative times ("last Friday", "last year",
   "last weekend") to the anchored absolute date using the conversation's date
   (e.g. "the Friday before July 15, 2023", or "2022"). Never answer with a bare
   relative phrase like "last Friday".
4. Be specific: include the exact detail the question targets (who, what, for
   whom), not just the general topic.
5. Inference questions ("likely", "might", "would probably", "what would X be"):
   commit to your single best inference from the evidence. Do not refuse or hedge.
6. State the answer directly. No preamble like "Based on the context", no source
   numbers. Only if the context contains nothing relevant at all, answer exactly:
   I don't have enough information.

Context:
{context}

Question: {question}

Answer:"""

ANSWERER_PROMPT_V3 = """You are answering a question about people from their conversation history,
using the numbered context as evidence.

Rules:
1. Answer directly and COMPLETELY. Include every part of the answer the question
   asks for and every qualifier the answer key would contain (who, what, for whom,
   and any "but ..." / "and ..." follow-on clause), not only the first or most
   obvious part. Do not pad with facts the question did not ask about.
2. List questions ("what activities / books / things does X ..."): give EVERY
   matching item from the context, comma-separated. Omit none; invent none.
3. "When" / date questions: resolve relative times ("last Friday", "last year",
   "last weekend") to the anchored absolute date using the conversation's date
   (e.g. "the Friday before July 15, 2023", or "2022"). Never answer with a bare
   relative phrase like "last Friday". BUT if the context contains no explicit or
   anchorable date for the event, answer "I don't have enough information" rather
   than guessing or inferring a date.
4. Be specific: include the exact detail the question targets (who, what, for
   whom), not just the general topic.
5. Inference questions ("likely", "might", "would probably", "what would X be"):
   commit to your single best inference from the evidence. Do not refuse or hedge.
   (This does NOT apply to dates: never invent a date the context does not support.)
6. State the answer directly. No preamble like "Based on the context", no source
   numbers. Only if the context contains nothing relevant at all, answer exactly:
   I don't have enough information.

Context:
{context}

Question: {question}

Answer:"""

ANSWERER_PROMPTS = {
    "v1": ANSWERER_PROMPT,
    "v2": ANSWERER_PROMPT_V2,
    "v3": ANSWERER_PROMPT_V3,
}

EXPAND_PROMPT = """You are helping a memory-retrieval system find relevant facts about a \
person from their conversation history. Rewrite the question below as {n} alternative search \
queries that express the same information need with different wording, synonyms, or by \
decomposing it into focused sub-questions. Aim to maximize recall over the memory store.

Return ONLY a JSON array of {n} short query strings, and nothing else.

Question: {question}"""


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
    template = ANSWERER_PROMPTS.get(cfg.answer_prompt_version, ANSWERER_PROMPT)
    prompt = template.format(
        context=context_text or "(no relevant memories found)",
        question=question,
    )
    return await gemini.generate(
        cfg.answerer_model,
        prompt,
        max_output_tokens=cfg.answerer_max_tokens,
        thinking_budget=cfg.answerer_thinking_budget,
    )


def _memory_id(m: dict):
    """The /context memory id. IronMem returns it under `id`; fall back to memory_id."""
    return m.get("id", m.get("memory_id"))


def rrf_fuse(ranked_id_lists: list[list], k: int = 60) -> list:
    """Reciprocal Rank Fusion. Given several rank-ordered id lists, return one id
    list ordered by descending fused score. Ties keep first-seen order (so the
    original question's list, fused first, breaks ties)."""
    scores: dict = {}
    for ids in ranked_id_lists:
        for rank, mid in enumerate(ids):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda mid: scores[mid], reverse=True)


def _parse_variants(raw: str, n: int) -> list[str]:
    """Pull a JSON list of strings out of the model's reply, tolerating code fences
    and stray prose. Returns at most `n` non-empty strings (possibly empty)."""
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):  # strip ```json ... ``` fences
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) >= n:
            break
    return out


async def expand_query(
    gemini: GeminiClient, cfg: Config, question: str, n: int | None = None
) -> list[str]:
    """Ask Gemini once for up to `n` alternative phrasings of `question` (defaults
    to cfg.multi_query). Uses the same model/location as the answerer. Robust to
    parse/API failures (returns [] so the caller falls back to the original alone)."""
    n = cfg.multi_query if n is None else n
    prompt = EXPAND_PROMPT.format(n=n, question=question)
    try:
        raw = await gemini.generate(
            cfg.answerer_model,
            prompt,
            max_output_tokens=cfg.expand_max_tokens,
            thinking_budget=cfg.expand_thinking_budget,
        )
    except Exception:
        return []
    return _parse_variants(raw, n)


async def multi_query_retrieve(
    client: IronMemClient,
    gemini: GeminiClient,
    cfg: Config,
    project: str,
    question: str,
    *,
    n: int | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Expand the question into variants, retrieve a ranked list per variant
    concurrently, RRF-fuse the lists harness-side, and return the top `limit`
    memories. `n`/`limit` default to cfg.multi_query/cfg.retrieve_limit; the router
    passes per-question overrides without mutating the shared cfg. The original
    question is always one of the queries, so this degrades to plain retrieval when
    expansion yields nothing."""
    n = cfg.multi_query if n is None else n
    limit = cfg.retrieve_limit if limit is None else limit
    variants = await expand_query(gemini, cfg, question, n=n)
    # Original first; dedup variants against it (and each other), case-insensitively.
    queries: list[str] = [question]
    seen = {question.strip().lower()}
    for v in variants:
        key = v.strip().lower()
        if key and key not in seen:
            seen.add(key)
            queries.append(v)

    lists = await asyncio.gather(*(
        client.get_context(
            project, query=q, limit=limit, rerank=cfg.rerank, pool=cfg.pool
        )
        for q in queries
    ))

    by_id: dict = {}
    ranked_id_lists: list[list] = []
    for mems in lists:
        ids: list = []
        for m in mems:
            mid = _memory_id(m)
            if mid is None:
                continue
            by_id.setdefault(mid, m)
            ids.append(mid)
        ranked_id_lists.append(ids)

    fused = rrf_fuse(ranked_id_lists, k=60)
    return [by_id[mid] for mid in fused[:limit]]


# --- governed retrieval router ---------------------------------------------
#
# Classify each question by a heuristic on its TEXT ONLY (never the gold category
# label — real retrieval doesn't know it) and pick per-question retrieval params.

# Signals that a question is asking about time / ordering / duration.
_TEMPORAL_RE = re.compile(
    r"\b(when|what date|which date|what day|what year|which year|what month|"
    r"before|after|prior to|how long|how many (?:days|weeks|months|years))\b"
)
# Comparison / chaining words that suggest a multi-hop (multi-entity) question.
_MULTIHOP_RE = re.compile(r"\b(?:more|less|than|compared|both)\b")
# Capitalized proper-noun-like token (length >= 3), used away from sentence start.
_PROPER_RE = re.compile(r"[A-Za-z][A-Za-z']*")


def classify_question(question: str) -> str:
    """Return 'temporal' | 'multi_hop' | 'default' from the question text alone.

    Temporal is checked first (a temporal question like "When did X and Y meet?"
    should route temporal even though it also has multi-hop signals)."""
    q = question or ""
    low = q.lower()

    if _TEMPORAL_RE.search(low):
        return "temporal"

    # multi-hop signals: conjunction, comparatives, possessive chain, or >=2 entities
    if " and " in low:
        return "multi_hop"
    if _MULTIHOP_RE.search(low):
        return "multi_hop"
    if low.count("'s") >= 2:  # possessive chain, e.g. "Alice's ... Bob's ..."
        return "multi_hop"
    # >= 2 distinct capitalized proper-noun-like tokens, excluding the sentence-start
    # word (which is capitalized only by position, not because it's a proper noun).
    proper: set[str] = set()
    for i, w in enumerate(_PROPER_RE.findall(q)):
        if i == 0:
            continue
        if len(w) >= 3 and w[0].isupper():
            proper.add(w)
    if len(proper) >= 2:
        return "multi_hop"

    return "default"


# Per-class retrieval params, grounded in our raw-recall curve (multi-hop &
# temporal have the most top-k headroom). `retrieve_floor` is the minimum top-k for
# the class; the effective limit is max(cfg.retrieve_limit, retrieve_floor). Tune here.
ROUTING_TABLE: dict[str, dict] = {
    "multi_hop": {"multi_query": 3, "retrieve_floor": 20},
    "temporal":  {"multi_query": 0, "retrieve_floor": 25},
    "default":   {"multi_query": 0, "retrieve_floor": 0},
}


def route_params(question_class: str, cfg: Config) -> tuple[int, int]:
    """Map a class to (multi_query_n, retrieve_limit) for this question."""
    spec = ROUTING_TABLE.get(question_class, ROUTING_TABLE["default"])
    return spec["multi_query"], max(cfg.retrieve_limit, spec["retrieve_floor"])


async def retrieve_and_answer(
    client: IronMemClient,
    gemini: GeminiClient,
    cfg: Config,
    project: str,
    question: str,
) -> tuple[str, str, list[dict]]:
    """Returns (generated_answer, retrieved_context_text, raw_memories)."""
    if cfg.route:
        # Governed router: per-question class -> (multi_query_n, retrieve_limit).
        n, limit = route_params(classify_question(question), cfg)
        if n > 0:
            memories = await multi_query_retrieve(
                client, gemini, cfg, project, question, n=n, limit=limit
            )
        else:
            memories = await client.get_context(
                project, query=question, limit=limit, rerank=cfg.rerank, pool=cfg.pool
            )
    elif cfg.multi_query > 0:
        memories = await multi_query_retrieve(client, gemini, cfg, project, question)
    else:
        memories = await client.get_context(project, query=question, limit=cfg.retrieve_limit)
    context_text = build_context(memories)
    answer = await answer_question(gemini, cfg, question, context_text)
    return answer, context_text, memories
