"""Classify LoCoMo regression-set failures using saved retrieved contexts.

This is a diagnostic harness, not an oracle. The labels are deliberately
conservative and every row carries the raw signals used to assign the label so
manual review and future training data stay auditable.

Usage:
  .venv/bin/python scripts/classify_flips.py \
      --regression-set results/regression/regression_set_upg8_vs_upg11.json \
      --output results/regression/flip_classification_upg8_vs_upg11.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "being",
    "by", "did", "do", "does", "for", "from", "had", "has", "have", "he",
    "her", "hers", "him", "his", "how", "i", "in", "into", "is", "it",
    "its", "me", "my", "of", "on", "or", "our", "ours", "she", "that",
    "the", "their", "theirs", "them", "they", "this", "to", "was", "we",
    "were", "what", "when", "where", "which", "who", "whom", "why", "with",
    "you", "your", "yours",
}

MONTHS = {
    "january": "1",
    "jan": "1",
    "february": "2",
    "feb": "2",
    "march": "3",
    "mar": "3",
    "april": "4",
    "apr": "4",
    "may": "5",
    "june": "6",
    "jun": "6",
    "july": "7",
    "jul": "7",
    "august": "8",
    "aug": "8",
    "september": "9",
    "sep": "9",
    "sept": "9",
    "october": "10",
    "oct": "10",
    "november": "11",
    "nov": "11",
    "december": "12",
    "dec": "12",
}


def _resolve(path: str) -> Path:
    src = Path(path)
    if not src.is_absolute():
        src = REPO_ROOT / src
    return src


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing input file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise SystemExit(f"{path} is not a regression set with an entries list")
    return data


def _normalize(text: str | None) -> str:
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9]+", text)
    words = [MONTHS.get(w, w) for w in words]
    return " ".join(words)


def _tokens(text: str | None, keep_stop: bool = False) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    out: list[str] = []
    for word in words:
        word = MONTHS.get(word, word)
        if not keep_stop and (word in STOP_WORDS or len(word) < 2):
            continue
        out.append(word)
    return out


def _salient_tokens(text: str | None) -> set[str]:
    return set(_tokens(text))


def _coverage(needle_tokens: set[str], haystack: str | None) -> float:
    if not needle_tokens:
        return 0.0
    hay = set(_tokens(haystack))
    return round(len(needle_tokens & hay) / len(needle_tokens), 4)


def _split_context_items(context: str | None) -> list[dict[str, Any]]:
    """Split IronMem retrieved_context into ranked items when possible."""
    text = context or ""
    matches = list(re.finditer(r"(?m)^\[(\d+)\]\s+", text))
    if not matches:
        return [{"rank": None, "text": text}] if text else []
    items: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        items.append({"rank": int(match.group(1)), "text": text[start:end].strip()})
    return items


def _phrase_variants(text: str | None) -> set[str]:
    normalized = _normalize(text)
    variants = {normalized} if normalized else set()
    raw = text or ""
    # Keep list-style ground truths useful without overfitting to punctuation.
    for part in re.split(r"\s*(?:;|\||/|\band\b|\bor\b)\s*", raw, flags=re.IGNORECASE):
        part_norm = _normalize(part)
        if part_norm:
            variants.add(part_norm)
    return {v for v in variants if len(v) >= 2}


def _evidence_presence(ground_truth: str | None, context: str | None) -> dict[str, Any]:
    gt_tokens = _salient_tokens(ground_truth)
    ctx_norm = _normalize(context)
    variants = _phrase_variants(ground_truth)
    # Split variants are useful for list-style answers, but a lone fragment from
    # a longer answer ("once" from "once or twice a year") is too weak to prove
    # the evidence is present.
    strict_variants = {
        v for v in variants
        if len(_tokens(v)) >= 2 or len(gt_tokens) <= 1
    }
    exact = sorted(v for v in strict_variants if v and v in ctx_norm)
    coverage = _coverage(gt_tokens, context)

    strong = bool(exact) or coverage >= 0.67
    partial = not strong and coverage >= 0.34

    evidence_rank = None
    evidence_snippet = None
    for item in _split_context_items(context):
        item_norm = _normalize(item["text"])
        item_tokens = set(_tokens(item["text"]))
        item_exact = any(v in item_norm for v in strict_variants)
        item_overlap = len(gt_tokens & item_tokens)
        if item_exact or (gt_tokens and item_overlap / len(gt_tokens) >= 0.5):
            evidence_rank = item["rank"]
            evidence_snippet = item["text"][:500]
            break

    return {
        "exact_ground_truth_match": bool(exact),
        "exact_variants": exact[:8],
        "ground_truth_token_count": len(gt_tokens),
        "ground_truth_context_coverage": coverage,
        "evidence_present": strong,
        "partial_evidence_present": partial,
        "evidence_rank": evidence_rank,
        "evidence_snippet": evidence_snippet,
    }


def _context_shape(context: str | None, question: str | None) -> dict[str, Any]:
    items = _split_context_items(context)
    top = items[:10]
    synthesized = 0
    source_linked = 0
    unrelated = 0
    q_tokens = _salient_tokens(question)
    for item in top:
        text = item["text"].lower()
        if "synthesized" in text or "derived" in text:
            synthesized += 1
        if "locomo" in text or "source" in text or "as of" in text:
            source_linked += 1
        item_tokens = set(_tokens(item["text"]))
        if q_tokens and len(q_tokens & item_tokens) == 0:
            unrelated += 1
    top_n = len(top)
    return {
        "context_item_count": len(items),
        "top10_item_count": top_n,
        "top10_synthesized_or_derived": synthesized,
        "top10_source_linked": source_linked,
        "top10_no_question_token_overlap": unrelated,
        "top10_synthesized_density": round(synthesized / top_n, 4) if top_n else 0.0,
        "top10_unrelated_density": round(unrelated / top_n, 4) if top_n else 0.0,
    }


def _bridge_signals(entry: dict[str, Any], candidate_context: str | None) -> dict[str, Any]:
    question_tokens = _salient_tokens(entry.get("question"))
    ground_truth_tokens = _salient_tokens(entry.get("ground_truth"))
    ctx_tokens = set(_tokens(candidate_context))
    return {
        "question_token_overlap": sorted(question_tokens & ctx_tokens),
        "ground_truth_token_overlap": sorted(ground_truth_tokens & ctx_tokens),
        "question_overlap_count": len(question_tokens & ctx_tokens),
        "ground_truth_overlap_count": len(ground_truth_tokens & ctx_tokens),
    }


def _classify(entry: dict[str, Any]) -> dict[str, Any]:
    bucket = entry.get("bucket")
    baseline_context = (entry.get("baseline") or {}).get("retrieved_context")
    candidate_context = (entry.get("candidate") or {}).get("retrieved_context")
    question = entry.get("question")
    ground_truth = entry.get("ground_truth")

    baseline_evidence = _evidence_presence(ground_truth, baseline_context)
    candidate_evidence = _evidence_presence(ground_truth, candidate_context)
    baseline_shape = _context_shape(baseline_context, question)
    candidate_shape = _context_shape(candidate_context, question)
    bridge = _bridge_signals(entry, candidate_context)

    if bucket not in {"lost", "stable_wrong"}:
        label = "not_classified"
        reason = "only lost and stable_wrong rows are failure-classified"
    elif baseline_evidence["evidence_present"] and not candidate_evidence["evidence_present"]:
        label = "retrieval_demotion"
        reason = "ground-truth evidence is present in baseline context and absent from candidate context"
    elif candidate_evidence["evidence_present"]:
        late_rank = (
            candidate_evidence["evidence_rank"] is not None
            and candidate_evidence["evidence_rank"] > 8
        )
        dense_synth = candidate_shape["top10_synthesized_density"] >= 0.5
        dense_unrelated = candidate_shape["top10_unrelated_density"] >= 0.5
        if late_rank or dense_synth or dense_unrelated:
            label = "attention_dilution"
            reason = "candidate context contains ground-truth evidence, but it is late or surrounded by noisy synthesized/unrelated items"
        else:
            label = "answerer_failure"
            reason = "candidate context contains ground-truth evidence, but the candidate answer was judged wrong"
    elif entry.get("category") == "multi_hop" and (
        candidate_evidence["partial_evidence_present"]
        or (bridge["question_overlap_count"] >= 2 and bridge["ground_truth_overlap_count"] > 0)
    ):
        label = "partial_bridge"
        reason = "candidate context carries partial hop evidence but not enough explicit ground-truth evidence"
    elif baseline_evidence["partial_evidence_present"] and not candidate_evidence["partial_evidence_present"]:
        label = "retrieval_demotion"
        reason = "partial ground-truth evidence is stronger in baseline than candidate context"
    else:
        label = "unknown"
        reason = "heuristics could not identify enough evidence to assign a specific failure type"

    return {
        "join_key": entry.get("join_key"),
        "bucket": bucket,
        "category": entry.get("category"),
        "category_int": entry.get("category_int"),
        "question": question,
        "ground_truth": ground_truth,
        "baseline_score": entry.get("baseline_score"),
        "candidate_score": entry.get("candidate_score"),
        "baseline_answer": entry.get("baseline_answer"),
        "candidate_answer": entry.get("candidate_answer"),
        "classification": label,
        "reason": reason,
        "signals": {
            "baseline_evidence": baseline_evidence,
            "candidate_evidence": candidate_evidence,
            "baseline_context_shape": baseline_shape,
            "candidate_context_shape": candidate_shape,
            "candidate_bridge": bridge,
        },
        "baseline_context_sha256": hashlib.sha256((baseline_context or "").encode()).hexdigest(),
        "candidate_context_sha256": hashlib.sha256((candidate_context or "").encode()).hexdigest(),
    }


def _summarize(classifications: list[dict[str, Any]]) -> dict[str, Any]:
    by_class = Counter(row["classification"] for row in classifications)
    by_bucket = Counter(row["bucket"] for row in classifications)
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    by_bucket_category: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))

    for row in classifications:
        cat = row["category"] or "unknown"
        bucket = row["bucket"] or "unknown"
        label = row["classification"]
        by_category[cat][label] += 1
        by_bucket_category[bucket][cat][label] += 1

    return {
        "classified_rows": len(classifications),
        "by_class": dict(sorted(by_class.items())),
        "by_bucket": dict(sorted(by_bucket.items())),
        "by_category": {
            cat: dict(sorted(counter.items()))
            for cat, counter in sorted(by_category.items())
        },
        "by_bucket_category": {
            bucket: {
                cat: dict(sorted(counter.items()))
                for cat, counter in sorted(categories.items())
            }
            for bucket, categories in sorted(by_bucket_category.items())
        },
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    src = _resolve(args.regression_set)
    data = _load_json(src)
    wanted_buckets = set(args.buckets)
    entries = [e for e in data["entries"] if e.get("bucket") in wanted_buckets]
    classifications = [_classify(entry) for entry in entries]

    return {
        "artifact": "ironmem_locomo_flip_classification",
        "artifact_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_regression_set": {
            "path": str(src.relative_to(REPO_ROOT) if src.is_relative_to(REPO_ROOT) else src),
            "sha256": _sha256(src),
            "baseline_run": data.get("baseline_run"),
            "candidate_run": data.get("candidate_run"),
        },
        "classified_buckets": sorted(wanted_buckets),
        "summary": _summarize(classifications),
        "classifications": classifications,
    }


def _write_json(path: Path, data: dict[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"Refusing to overwrite existing output without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify failures in a LoCoMo regression set")
    parser.add_argument("--regression-set", required=True, help="Regression set JSON from build_flip_set.py")
    parser.add_argument("--output", required=True, help="Output classification JSON")
    parser.add_argument(
        "--buckets",
        nargs="+",
        default=["lost", "stable_wrong"],
        help="Buckets to classify; defaults to lost and stable_wrong",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite output if it already exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = _resolve(args.output)
    data = build(args)
    _write_json(output, data, args.force)

    summary = data["summary"]
    print("=== Flip Classification Built ===")
    print(f"output: {output}")
    print(f"classified_rows: {summary['classified_rows']}")
    print("by_class:")
    for label, count in summary["by_class"].items():
        print(f"  {label}: {count}")
    print("by_category:")
    for category, counts in summary["by_category"].items():
        rendered = ", ".join(f"{label}={count}" for label, count in counts.items())
        print(f"  {category}: {rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
