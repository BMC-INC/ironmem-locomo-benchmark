#!/usr/bin/env python3
"""Extract failed question ids from a benchmark result artifact.

The output is a simple JSON list that can be passed to
scripts/build_canary_dataset.py --question-ids. This keeps regression gates
reproducible after each canary instead of hand-copying failed ids from logs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing input file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def failed_ids(data: dict[str, Any], *, include_errors: bool) -> list[str]:
    rows = data.get("per_question")
    if not isinstance(rows, list):
        raise SystemExit("Input artifact must contain per_question[]")

    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        qid = row.get("question_id")
        if not qid:
            continue
        score = row.get("score")
        failed_score = score is not None and float(score) < 0.5
        failed_error = include_errors and bool(row.get("error"))
        if failed_score or failed_error:
            ids.append(str(qid))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="benchmark result JSON")
    parser.add_argument("--output", required=True, help="JSON list of failed question ids")
    parser.add_argument(
        "--include-errors",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include rows with an error even if score is missing",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    src = _resolve(args.input)
    out = _resolve(args.output)
    ids = failed_ids(_load(src), include_errors=args.include_errors)
    if out.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing output without --force: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ids, indent=2) + "\n")
    print(f"failed question ids: {len(ids)}")
    print(f"output: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
