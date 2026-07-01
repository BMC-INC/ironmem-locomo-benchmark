"""Central configuration for the IronMem LoCoMo benchmark harness.

All secrets are read from the environment (or a gitignored .env). Nothing
secret is ever hard-coded here — this repo is public.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Best-effort load of a gitignored .env at the repo root.

    override=False so an already-exported shell variable always wins over a
    blank line in .env (e.g. ANTHROPIC_API_KEY exported in your shell).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


_load_dotenv()

# LoCoMo question categories. Integer codes come straight from locomo10.json and
# match snap-research/locomo + the mem0 benchmark harness exactly.
CATEGORY_MAP: dict[int, str] = {
    1: "multi_hop",
    2: "temporal",
    3: "open_domain",
    4: "single_hop",
    5: "adversarial",
}

# Categories counted in the headline "overall" score. Category 5 (adversarial) is
# excluded to stay apples-to-apples with mem0's published numbers; it is still
# answered, judged, and reported separately under "adversarial".
SCORED_CATEGORIES: frozenset[int] = frozenset({1, 2, 3, 4})

DEFAULT_IRONMEM_URL = "http://localhost:37778"
# Both answerer and judge run on Vertex AI Gemini 2.5 Pro. Auth is ADC:
#   gcloud auth application-default login --project=<project>
DEFAULT_ANSWERER_MODEL = "gemini-2.5-pro"
DEFAULT_JUDGE_MODEL = "gemini-2.5-pro"
DEFAULT_VERTEX_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT") or "queueflow-sentinel"
DEFAULT_VERTEX_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"


@dataclass
class Config:
    """Runtime configuration. Defaults are overridable via env and CLI flags."""

    ironmem_url: str = field(
        default_factory=lambda: os.environ.get("IRONMEM_URL", DEFAULT_IRONMEM_URL)
    )
    # Vertex AI (Gemini). Project/location come from env or the queueflow-sentinel
    # default; credentials are Application Default Credentials (no API key).
    vertex_project: str = field(default_factory=lambda: DEFAULT_VERTEX_PROJECT)
    vertex_location: str = field(default_factory=lambda: DEFAULT_VERTEX_LOCATION)
    answerer_model: str = DEFAULT_ANSWERER_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    # Answerer prompt version: "v1" = original, "v2" = failure-targeted
    # (exhaustive list aggregation, no-preamble brevity, commit-to-inference).
    answer_prompt_version: str = "v1"
    # Multi-hop master aggregator: when enabled, route multi-hop questions through
    # evidence quotes + logic trace + concise final answer. Other question classes
    # keep the normal answerer path.
    synthesize: bool = False
    synthesis_model: str | None = None  # None -> use answerer_model

    # Per-call token budgets. Gemini 2.5 Pro always "thinks", so max_output_tokens
    # leaves headroom for thinking tokens on top of the visible answer.
    answerer_max_tokens: int = 1024
    answerer_thinking_budget: int = 512
    judge_max_tokens: int = 512
    judge_thinking_budget: int = 128
    fact_max_tokens: int = 3072
    fact_thinking_budget: int = 1024
    # Query expansion (multi-query): a short JSON list of rephrasings, so cheap.
    expand_max_tokens: int = 512
    expand_thinking_budget: int = 256

    # Retrieval
    retrieve_limit: int = 10
    # When true, ask IronMem to LLM-rerank a wider candidate pool down to
    # retrieve_limit (server-side ?rerank=1). Off by default; opt in via --rerank.
    rerank: bool = False
    # Optional candidate-pool override before reranking (server-side ?pool=).
    # None leaves the server default (2×limit). Set via --pool for recall@25/@50.
    pool: int | None = None
    # Multi-query expansion: when > 0, expand the question into this many variant
    # queries, retrieve per-variant, then RRF-fuse harness-side. 0 = OFF (single query).
    multi_query: int = 0
    # Governed retrieval router: when True, classify each question by a heuristic on
    # its TEXT (never the gold category) and pick per-question retrieval params from
    # the routing table in query.py. False = OFF (behavior unchanged).
    route: bool = False

    # Concurrency + retry/backoff. Gemini 2.5 Pro runs on dynamic shared quota,
    # which throttles with 429s under sustained load — so we keep concurrency
    # modest and give each call generous retry headroom (with jitter, see
    # gemini.py) to ride out throttling instead of erroring the question.
    max_concurrency: int = 8
    ingest_concurrency: int = 3
    max_retries: int = 10
    backoff_base: float = 1.6
    backoff_cap: float = 60.0
    request_timeout: float = 120.0

    project_prefix: str = "/benchmark/locomo/"

    def project_for(self, sample_id: str, strategy: str) -> str:
        """IronMem project namespace for one conversation + ingest strategy."""
        return f"{self.project_prefix}{sample_id}__{strategy}"
