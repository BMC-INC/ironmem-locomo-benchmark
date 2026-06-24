"""Thin async REST client for IronMem.

Routes verified against IronMem v0.4.0 src/server.rs:
    POST /session/start  {project}                       -> {session_id}
    POST /event          {project,session_id,tool,input,output} -> {id}
    POST /session/end    {session_id}                    -> {...}  (triggers compression)
    GET  /context        ?project&query&limit            -> {memories:[{summary,...}]}
    POST /remember       {project,text,kind,scope,tags}  -> {...}
    GET  /status                                         -> {ok,memories,observations,ccr,...}

There is intentionally no /search endpoint — /context with a non-empty query is
IronMem's hybrid (BM25 + vector) retrieval, which is exactly what we want.
"""
from __future__ import annotations

import asyncio

import httpx

from .config import Config


class IronMemError(RuntimeError):
    pass


class IronMemClient:
    def __init__(self, config: Config, http: httpx.AsyncClient) -> None:
        self._cfg = config
        self._http = http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        url = f"{self._cfg.ironmem_url}{path}"
        last: Exception | None = None
        for attempt in range(self._cfg.max_retries):
            try:
                resp = await self._http.request(method, url, json=json, params=params)
            except httpx.TransportError as exc:  # connect/read/timeout
                last = exc
                await self._sleep(attempt)
                continue

            if resp.status_code < 400:
                return resp.json() if resp.content else {}

            # Retry transient server-side / rate-limit failures; fail fast on 4xx.
            if resp.status_code == 429 or resp.status_code >= 500:
                last = IronMemError(f"{resp.status_code}: {resp.text[:200]}")
                await self._sleep(attempt)
                continue

            raise IronMemError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")

        raise IronMemError(
            f"{method} {path} failed after {self._cfg.max_retries} attempts: {last}"
        )

    async def _sleep(self, attempt: int) -> None:
        delay = min(self._cfg.backoff_cap, self._cfg.backoff_base ** attempt)
        await asyncio.sleep(delay)

    # --- endpoints ---------------------------------------------------------

    async def status(self) -> dict:
        return await self._request("GET", "/status")

    async def session_start(self, project: str) -> str:
        r = await self._request("POST", "/session/start", json={"project": project})
        return r["session_id"]

    async def record_event(
        self,
        project: str,
        session_id: str,
        tool: str,
        input_text: str,
        output_text: str = "",
    ) -> dict:
        return await self._request(
            "POST",
            "/event",
            json={
                "project": project,
                "session_id": session_id,
                "tool": tool,
                "input": input_text,
                "output": output_text,
            },
        )

    async def session_end(self, session_id: str) -> dict:
        return await self._request("POST", "/session/end", json={"session_id": session_id})

    async def get_context(self, project: str, query: str, limit: int | None = None) -> list[dict]:
        params = {
            "project": project,
            "query": query,
            "limit": limit or self._cfg.retrieve_limit,
        }
        if self._cfg.rerank:
            params["rerank"] = "1"
        r = await self._request("GET", "/context", params=params)
        return r.get("memories", [])

    async def remember(
        self,
        project: str,
        text: str,
        kind: str = "fact",
        scope: str = "project",
        tags: str | None = None,
    ) -> dict:
        body: dict = {"project": project, "text": text, "kind": kind, "scope": scope}
        if tags:
            body["tags"] = tags
        return await self._request("POST", "/remember", json=body)
