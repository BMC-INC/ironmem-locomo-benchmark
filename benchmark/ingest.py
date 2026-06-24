"""LoCoMo -> IronMem ingestion.

Maps each LoCoMo conversation to an IronMem project, and each timestamped
session to one IronMem session (start -> events -> end-with-compression).

Two strategies:
  * "session"  -- conversations only; rely purely on IronMem's compression.
  * "hybrid"   -- conversations PLUS LLM-extracted atomic facts stored via
                  /remember. This is the ablation: it measures how much an
                  explicit fact-extraction layer (what Mem0/Zep do) adds on top
                  of compression. The DELTA between the two is the finding.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import Config
from .ironmem_client import IronMemClient

SESSION_RE = re.compile(r"^session_(\d+)$")


@dataclass
class Turn:
    speaker: str
    text: str
    dia_id: str
    caption: str | None = None

    def as_line(self) -> str:
        line = f"{self.speaker}: {self.text}".strip()
        if self.caption:
            line += f" [shared an image: {self.caption}]"
        return line


@dataclass
class Session:
    key: str
    index: int
    date_time: str | None
    turns: list[Turn] = field(default_factory=list)


@dataclass
class Conversation:
    sample_id: str
    speaker_a: str
    speaker_b: str
    sessions: list[Session]
    qa: list[dict]

    @property
    def num_sessions(self) -> int:
        return len(self.sessions)


def parse_conversation(raw: dict) -> Conversation:
    conv = raw.get("conversation", {})
    indices = sorted(
        int(m.group(1)) for k in conv if (m := SESSION_RE.match(k))
    )
    sessions: list[Session] = []
    for i in indices:
        turns_raw = conv.get(f"session_{i}") or []
        turns = [
            Turn(
                speaker=t.get("speaker", ""),
                text=t.get("text", ""),
                dia_id=t.get("dia_id", ""),
                caption=t.get("blip_caption") or t.get("caption"),
            )
            for t in turns_raw
        ]
        sessions.append(
            Session(
                key=f"session_{i}",
                index=i,
                date_time=conv.get(f"session_{i}_date_time"),
                turns=turns,
            )
        )
    return Conversation(
        sample_id=str(raw.get("sample_id", "")),
        speaker_a=conv.get("speaker_a", ""),
        speaker_b=conv.get("speaker_b", ""),
        sessions=sessions,
        qa=raw.get("qa", []),
    )


def load_conversations(path: str) -> list[Conversation]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [parse_conversation(c) for c in data]


def total_sessions(conversations: list[Conversation]) -> int:
    return sum(c.num_sessions for c in conversations)


# --- fact extraction (hybrid strategy) -------------------------------------

FACT_PROMPT = """Extract the key atomic facts from this conversation session.
Return ONLY a JSON array of short, self-contained statements. Each fact must stand
alone (include who/what and any date or place mentioned). Include only facts that
are explicitly stated in the dialogue. No commentary, just the JSON array.

Session{date}:
{dialog}
"""


def _parse_json_list(text: str) -> list[str]:
    """Robustly pull a JSON array of strings out of an LLM response."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        items = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [str(x).strip() for x in items if str(x).strip()]


async def extract_session_facts(gemini, cfg: Config, session: Session) -> list[str]:
    dialog = "\n".join(t.as_line() for t in session.turns)
    date = f" (dated {session.date_time})" if session.date_time else ""
    text = await gemini.generate(
        cfg.answerer_model,
        FACT_PROMPT.format(date=date, dialog=dialog),
        max_output_tokens=cfg.fact_max_tokens,
        thinking_budget=cfg.fact_thinking_budget,
    )
    return _parse_json_list(text)


# --- ingestion -------------------------------------------------------------

async def ingest_conversation(
    client: IronMemClient,
    cfg: Config,
    conv: Conversation,
    strategy: str,
    *,
    gemini=None,
    on_session_done=None,
) -> str:
    """Ingest one conversation under the given strategy. Returns the project id."""
    project = cfg.project_for(conv.sample_id, strategy)
    extract_facts = strategy == "hybrid"

    for session in conv.sessions:
        session_id = await client.session_start(project)

        # Preserve the session's real date — /event stamps "now", which would
        # otherwise erase the temporal context that LoCoMo category-2 needs.
        if session.date_time:
            await client.record_event(
                project,
                session_id,
                tool="session_meta",
                input_text=f"[Conversation took place on {session.date_time}]",
            )

        for turn in session.turns:
            await client.record_event(
                project, session_id, tool="conversation", input_text=turn.as_line()
            )

        await client.session_end(session_id)  # triggers compression

        if extract_facts and gemini is not None:
            for fact in await extract_session_facts(gemini, cfg, session):
                dated = f"{fact} (as of {session.date_time})" if session.date_time else fact
                await client.remember(
                    project, dated, kind="fact", scope="project", tags="locomo"
                )

        if on_session_done is not None:
            on_session_done()

    return project
