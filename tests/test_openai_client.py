import json
import unittest
from unittest.mock import patch

import httpx

from benchmark.config import Config


class OpenAIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_azure_managed_identity_request_uses_deployment_and_bearer_token(self):
        from benchmark.openai_client import OpenAIClient

        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.host == "169.254.169.254":
                return httpx.Response(
                    200,
                    json={"access_token": "mi-token", "expires_in": "3600"},
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  answer  "}}]},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        cfg = Config(max_retries=1)
        with patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
                "AZURE_OPENAI_API_VERSION": "2024-10-21",
                "AZURE_USE_MI": "1",
            },
            clear=True,
        ):
            client = OpenAIClient(cfg, http=http)
            result = await client.generate(
                "judge-deployment",
                "score this",
                max_output_tokens=64,
                temperature=0,
            )

        self.assertEqual(result, "answer")
        self.assertEqual(len(requests), 2)
        token_request, completion_request = requests
        self.assertEqual(token_request.headers["Metadata"], "true")
        self.assertEqual(
            token_request.url.params["resource"],
            "https://cognitiveservices.azure.com/",
        )
        self.assertEqual(completion_request.headers["Authorization"], "Bearer mi-token")
        self.assertIn(
            "/openai/deployments/judge-deployment/chat/completions",
            completion_request.url.path,
        )
        body = json.loads(completion_request.content)
        self.assertEqual(body["max_completion_tokens"], 64)
        self.assertNotIn("temperature", body)

        await http.aclose()

    async def test_managed_identity_token_is_cached_between_calls(self):
        from benchmark.openai_client import OpenAIClient

        token_calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if request.url.host == "169.254.169.254":
                token_calls += 1
                return httpx.Response(
                    200,
                    json={"access_token": "mi-token", "expires_in": "3600"},
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.dict(
            "os.environ",
            {"AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com"},
            clear=True,
        ):
            client = OpenAIClient(Config(max_retries=1), http=http)
            for _ in range(2):
                await client.generate("deployment", "prompt", max_output_tokens=8)

        self.assertEqual(token_calls, 1)
        await http.aclose()

    async def test_public_openai_requires_api_key(self):
        from benchmark.openai_client import OpenAIClient

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "AZURE_OPENAI_ENDPOINT.*OPENAI_API_KEY"):
                OpenAIClient(Config())
