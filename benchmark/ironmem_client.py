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

    async def get_context(
        self,
        project: str,
        query: str,
        limit: int | None = None,
        *,
        rerank: bool | None = None,
        pool: int | None = None,
    ) -> list[dict]:
        """Hybrid retrieval. Per-call `rerank`/`pool` override the config defaults
        so the funnel probe can sweep limit/pool/rerank without mutating cfg."""
        params: dict = {
            "project": project,
            "query": query,
            "limit": limit or self._cfg.retrieve_limit,
        }
        use_rerank = self._cfg.rerank if rerank is None else rerank
        if use_rerank:
            params["rerank"] = "1"
        use_pool = self._cfg.pool if pool is None else pool
        if use_pool:
            params["pool"] = use_pool
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

    async def get_context_full(
        self,
        project: str,
        query: str,
        limit: int | None = None,
        *,
        rerank: bool | None = None,
        pool: int | None = None,
    ) -> dict:
        """Like get_context but returns the FULL /context response, including
        `expansions` (per-memory chunks with chunk_id handles). Track A's agentic
        answerer needs those chunk_ids to call /retrieve_original."""
        params: dict = {
            "project": project,
            "query": query,
            "limit": limit or self._cfg.retrieve_limit,
        }
        use_rerank = self._cfg.rerank if rerank is None else rerank
        if use_rerank:
            params["rerank"] = "1"
        use_pool = self._cfg.pool if pool is None else pool
        if use_pool:
            params["pool"] = use_pool
        return await self._request("GET", "/context", params=params)

    async def retrieve_original(
        self,
        *,
        chunk_id: str | None = None,
        memory_id: int | None = None,
        observation_id: int | None = None,
    ) -> dict:
        """Pull back the verbatim, uncompressed original behind a chunk_id (CCR).
        Returns {original, bytes, source_start, source_end, ...}."""
        body: dict = {}
        if chunk_id is not None:
            body["chunk_id"] = chunk_id
        if memory_id is not None:
            body["memory_id"] = memory_id
        if observation_id is not None:
            body["observation_id"] = observation_id
        return await self._request("POST", "/retrieve_original", json=body)
