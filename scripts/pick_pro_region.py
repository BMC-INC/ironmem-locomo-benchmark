"""Print a Vertex region where Gemini 2.5 Pro can take our load right now — or NONE.

For each region: a single-call probe, then an 8-way concurrent BURST. A region
passes only if >=6/8 burst calls return 200 (a proxy that a concurrency-8 sweep
won't 429-storm). Prints the first passing region, preferring specific regions
over `global` (its load-balancer can route to a saturated region). Prints NONE
if no region can currently handle concurrent Pro load, so the caller can skip and
stay armed instead of burning tokens against a saturated quota.

Uses the SAME Application Default Credentials the benchmark uses (no gcloud CLI).
"""
from __future__ import annotations
import asyncio
import httpx
from google.auth import default
from google.auth.transport.requests import Request

PROJECT = "your-gcp-project"
REGIONS = ["us-west1", "us-east4", "us-east1", "us-east5",
           "europe-west1", "us-central1", "global"]
BURST = 8
NEED = 6
BODY = {"contents": [{"role": "user", "parts": [{"text": "OK"}]}],
        "generationConfig": {"maxOutputTokens": 8, "temperature": 0}}


def host(region: str) -> str:
    return "aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com"


def url(region: str) -> str:
    return (f"https://{host(region)}/v1/projects/{PROJECT}/locations/{region}"
            f"/publishers/google/models/gemini-2.5-pro:generateContent")


async def probe_region(client: httpx.AsyncClient, region: str, headers: dict) -> bool:
    try:  # cheap single-call gate first
        r = await client.post(url(region), json=BODY, headers=headers, timeout=30)
        if r.status_code != 200:
            return False
    except Exception:
        return False
    async def one() -> int:
        try:
            r = await client.post(url(region), json=BODY, headers=headers, timeout=30)
            return r.status_code
        except Exception:
            return 0
    codes = await asyncio.gather(*(one() for _ in range(BURST)))
    return sum(1 for c in codes if c == 200) >= NEED


async def amain() -> int:
    creds, _ = default()
    creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    async with httpx.AsyncClient() as client:
        for region in REGIONS:
            if await probe_region(client, region, headers):
                print(region)
                return 0
    print("NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
