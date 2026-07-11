"""OpenAI-compatible backend with Azure Managed Identity support."""
from __future__ import annotations

import asyncio
import os
import random
import time

import httpx

from .config import Config

_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
_IMDS_TOKEN_URL = "http://169.254.169.254/metadata/identity/oauth2/token"
_COGNITIVE_SERVICES_RESOURCE = "https://cognitiveservices.azure.com/"


class OpenAIClient:
    """Chat Completions client for Azure OpenAI or the public OpenAI API."""

    def __init__(self, cfg: Config, *, http: httpx.AsyncClient | None = None) -> None:
        self._cfg = cfg
        self._azure_endpoint = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
        self._api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
        self._api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("AZURE_OPENAI_API_KEY")
            or ""
        ).strip()
        self._base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self._use_mi = os.environ.get("AZURE_USE_MI", "1") == "1"
        self._http = http or httpx.AsyncClient(timeout=cfg.request_timeout)
        self._owns_http = http is None
        self._mi_token: str | None = None
        self._mi_expires_at = 0.0
        if not self._azure_endpoint and not self._api_key:
            raise RuntimeError(
                "Set AZURE_OPENAI_ENDPOINT for Managed Identity or OPENAI_API_KEY "
                "for the public OpenAI backend"
            )
        if self._azure_endpoint and not self._use_mi and not self._api_key:
            raise RuntimeError("AZURE_USE_MI=0 requires AZURE_OPENAI_API_KEY")

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _aad_token(self) -> str:
        now = time.time()
        if self._mi_token and now < self._mi_expires_at - 60:
            return self._mi_token
        response = await self._http.get(
            _IMDS_TOKEN_URL,
            params={
                "api-version": "2018-02-01",
                "resource": _COGNITIVE_SERVICES_RESOURCE,
            },
            headers={"Metadata": "true"},
        )
        response.raise_for_status()
        data = response.json()
        self._mi_token = str(data["access_token"])
        self._mi_expires_at = now + float(data.get("expires_in", 3600))
        return self._mi_token

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        max_output_tokens: int,
        temperature: float = 0.0,
        thinking_budget: int | None = None,
    ) -> str:
        del thinking_budget  # OpenAI reasoning is controlled by the deployment.
        last: Exception | None = None
        for attempt in range(self._cfg.max_retries):
            try:
                url, headers, body = await self._request(model, prompt, max_output_tokens, temperature)
                response = await self._http.post(url, headers=headers, json=body)
                if response.status_code in _RETRYABLE:
                    last = RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
                    await self._sleep(attempt, response.headers.get("retry-after"))
                    continue
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
                data = response.json()
                return (data["choices"][0]["message"]["content"] or "").strip()
            except (httpx.TimeoutException, httpx.TransportError, ConnectionError) as exc:
                last = exc
                await self._sleep(attempt)
        raise RuntimeError(
            f"OpenAI-compatible call to {model} failed after "
            f"{self._cfg.max_retries} attempts: {last}"
        )

    async def _request(self, model: str, prompt: str, max_tokens: int, temperature: float):
        if self._azure_endpoint:
            headers = {"Content-Type": "application/json"}
            if self._use_mi:
                headers["Authorization"] = f"Bearer {await self._aad_token()}"
            else:
                headers["api-key"] = self._api_key
            url = (
                f"{self._azure_endpoint}/openai/deployments/{model}/chat/completions"
                f"?api-version={self._api_version}"
            )
            body = {
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": max_tokens,
            }
            # Reasoning deployments reject temperature=0 and other non-defaults.
            if temperature in (1, 1.0):
                body["temperature"] = temperature
            return url, headers, body

        return (
            f"{self._base_url}/chat/completions",
            {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )

    async def generate_agentic(self, *args, **kwargs):
        raise NotImplementedError("OpenAI backend does not support agentic tool use")

    async def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        try:
            server_delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            server_delay = 0.0
        ceiling = min(self._cfg.backoff_cap, self._cfg.backoff_base ** attempt)
        jittered = ceiling / 2 + random.uniform(0, ceiling / 2)
        await asyncio.sleep(max(server_delay, jittered))
