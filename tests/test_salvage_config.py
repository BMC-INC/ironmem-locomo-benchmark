import unittest
from types import SimpleNamespace

from benchmark.run import build_config
from benchmark.salvage import _args_for


class SalvageConfigTests(unittest.TestCase):
    def test_saved_result_metadata_builds_a_current_config(self):
        cli = SimpleNamespace(
            answerer_model=None,
            judge_model=None,
            answer_prompt=None,
            synthesize=None,
            concurrency=4,
            retrieve_limit=25,
            pool=100,
            multi_query=None,
            route=False,
            vertex_project=None,
            vertex_location="us-west1",
            rerank=True,
        )
        saved = {
            "answerer_model": "answer-deployment",
            "judge_model": "judge-deployment",
            "answer_prompt_version": "v2",
            "episodic_reconstruct": True,
            "episodic_episode_limit": 7,
            "episodic_max_original_chars": 1200,
        }

        cfg = build_config(_args_for(saved, cli))

        self.assertEqual(cfg.answerer_model, "answer-deployment")
        self.assertEqual(cfg.judge_model, "judge-deployment")
        self.assertTrue(cfg.episodic_reconstruct)
        self.assertEqual(cfg.episodic_episode_limit, 7)
        self.assertEqual(cfg.episodic_max_original_chars, 1200)
