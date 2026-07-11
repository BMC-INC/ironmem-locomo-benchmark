import unittest
from unittest.mock import patch

from benchmark.config import Config


class LlmFactoryTests(unittest.TestCase):
    def test_openai_backend_maps_gemini_defaults_to_explicit_deployments(self):
        from benchmark.llm import configure_models

        cfg = Config()
        with patch.dict(
            "os.environ",
            {
                "OPENAI_ANSWERER_MODEL": "answer-deployment",
                "OPENAI_JUDGE_MODEL": "judge-deployment",
            },
            clear=True,
        ):
            configure_models(cfg, "openai")

        self.assertEqual(cfg.answerer_model, "answer-deployment")
        self.assertEqual(cfg.judge_model, "judge-deployment")

    def test_openai_backend_preserves_models_selected_on_cli(self):
        from benchmark.llm import configure_models

        cfg = Config(answerer_model="gpt-custom-a", judge_model="gpt-custom-j")
        with patch.dict("os.environ", {}, clear=True):
            configure_models(cfg, "openai")

        self.assertEqual(cfg.answerer_model, "gpt-custom-a")
        self.assertEqual(cfg.judge_model, "gpt-custom-j")

    def test_unknown_backend_is_rejected(self):
        from benchmark.llm import create_llm_client

        with self.assertRaisesRegex(ValueError, "Unsupported BENCH_LLM"):
            create_llm_client(Config(), "anthropic")
