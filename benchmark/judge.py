"""Scoring phase: LLM judge on Vertex AI Gemini 2.5 Pro."""
from __future__ import annotations

import re

from .config import Config

JUDGE_PROMPT = """You are evaluating whether an AI assistant's answer is correct.

Question: {question}
Ground Truth Answer: {ground_truth}
Generated Answer: {generated_answer}

Score 1 if the generated answer is factually consistent with the ground truth.
Score 0 if it is incorrect, incomplete in a material way, or contradicts the ground truth.

Respond with only: {{"score": 0}} or {{"score": 1}}"""

_SCORE_RE = re.compile(r'"score"\s*:\s*([01])')
_FALLBACK_RE = re.compile(r"\b([01])\b")


def normalize_ground_truth(answer, category: int) -> str:
    """Open-domain (category 3) gold answers carry an explanation after a
    semicolon; LoCoMo/mem0 score against the part before it."""
    s = str(answer)
    if category == 3 and ";" in s:
        s = s.split(";", 1)[0].strip()
    return s


def extract_score(text: str) -> int:
    m = _SCORE_RE.search(text)
    if m:
        return int(m.group(1))
    m = _FALLBACK_RE.search(text)
    return int(m.group(1)) if m else 0


async def judge_answer(
    cfg: Config,
    clients,
    question: str,
    ground_truth,
    generated_answer: str,
    category: int,
) -> int:
    prompt = JUDGE_PROMPT.format(
        question=question,
        ground_truth=normalize_ground_truth(ground_truth, category),
        generated_answer=generated_answer,
    )
    text = await clients.gemini.generate(
        cfg.judge_model,
        prompt,
        max_output_tokens=cfg.judge_max_tokens,
        thinking_budget=cfg.judge_thinking_budget,
    )
    return extract_score(text)
