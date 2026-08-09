import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARK_ROOT))

import runner  # noqa: E402


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, url, headers, payload, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "payload": payload, "timeout": timeout}
        )
        return self.response


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.schema = runner.load_json(runner.SCHEMA_PATH)
        self.system = runner.PROMPT_PATH.read_text(encoding="utf-8")
        self.user = runner.build_user_message("List my GitHub repositories.")

    def test_committed_contract_is_valid(self):
        runner.validate_repository()
        self.assertEqual(100, len(runner.load_cases()))
        self.assertEqual(10, len(runner.load_models()))

    def test_excluded_names_are_rejected(self):
        models = runner.load_models()
        models[0] = {**models[0], "model": "grok-test"}
        errors = runner.validate_models(models)
        self.assertTrue(any("excluded" in error for error in errors))

    def test_perfect_plan_scores_100(self):
        case = runner.load_cases()[0]
        plan = {
            "kind": "execute",
            "command": "pwd",
            "summary": "Show current directory",
            "risk": "low",
            "required_tools": [],
            "install_candidates": [],
        }
        self.assertEqual(100, runner.score_plan(case, plan)["total"])

    def test_invalid_plan_scores_zero(self):
        case = runner.load_cases()[0]
        score = runner.score_plan(case, {"kind": "execute", "command": "pwd"})
        self.assertEqual(0, score["total"])
        self.assertFalse(score["schema_valid"])

    def test_ollama_request_is_loopback_and_structured(self):
        transport = FakeTransport(
            {
                "message": {"content": "{}"},
                "prompt_eval_count": 12,
                "eval_count": 4,
            }
        )
        model = runner.load_models()[0]
        with patch.dict(
            os.environ, {"OLLAMA_HOST": "http://127.0.0.1:11434"}, clear=False
        ):
            result = runner.invoke_model(
                model, self.system, self.user, self.schema, 5, transport
            )
        call = transport.calls[0]
        self.assertEqual("http://127.0.0.1:11434/api/chat", call["url"])
        self.assertFalse(call["payload"]["stream"])
        self.assertNotIn("$schema", call["payload"]["format"])
        self.assertEqual({"input_tokens": 12, "output_tokens": 4}, result.usage)

    def test_non_loopback_ollama_is_rejected(self):
        model = runner.load_models()[0]
        with patch.dict(
            os.environ, {"OLLAMA_HOST": "https://example.com"}, clear=False
        ):
            with self.assertRaises(runner.BenchmarkError):
                runner.invoke_model(
                    model, self.system, self.user, self.schema, 5, FakeTransport({})
                )

    def test_openai_responses_contract(self):
        transport = FakeTransport(
            {
                "id": "resp_test",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "{}"}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
        model = next(
            item for item in runner.load_models() if item["provider"] == "openai"
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            result = runner.invoke_model(
                model, self.system, self.user, self.schema, 5, transport
            )
        call = transport.calls[0]
        self.assertEqual("https://api.openai.com/v1/responses", call["url"])
        self.assertFalse(call["payload"]["store"])
        self.assertEqual("json_schema", call["payload"]["text"]["format"]["type"])
        self.assertEqual("{}", result.text)

    def test_anthropic_messages_contract(self):
        transport = FakeTransport(
            {
                "id": "msg_test",
                "content": [{"type": "text", "text": "{}"}],
                "usage": {"input_tokens": 11, "output_tokens": 6},
            }
        )
        model = next(
            item for item in runner.load_models() if item["provider"] == "anthropic"
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
            runner.invoke_model(
                model, self.system, self.user, self.schema, 5, transport
            )
        call = transport.calls[0]
        self.assertEqual("https://api.anthropic.com/v1/messages", call["url"])
        self.assertEqual(
            "json_schema", call["payload"]["output_config"]["format"]["type"]
        )

    def test_gemini_interactions_contract(self):
        transport = FakeTransport(
            {
                "id": "interaction_test",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "{}"}],
                    }
                ],
                "usage": {"total_input_tokens": 12, "total_output_tokens": 7},
            }
        )
        model = next(
            item for item in runner.load_models() if item["provider"] == "gemini"
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            result = runner.invoke_model(
                model, self.system, self.user, self.schema, 5, transport
            )
        call = transport.calls[0]
        self.assertEqual(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            call["url"],
        )
        self.assertFalse(call["payload"]["store"])
        self.assertEqual(
            "application/json", call["payload"]["response_format"]["mime_type"]
        )
        self.assertEqual({"input_tokens": 12, "output_tokens": 7}, result.usage)

    def test_paid_key_is_required(self):
        model = next(
            item for item in runner.load_models() if item["provider"] == "openai"
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(runner.BenchmarkError):
                runner.invoke_model(
                    model, self.system, self.user, self.schema, 5, FakeTransport({})
                )

    def test_report_renders_metrics(self):
        row = {
            "model_id": "fixture-model",
            "latency_ms": 25,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "estimated_cost_usd": None,
            "score": {
                "total": 100,
                "schema_valid": True,
                "command_exact": True,
                "risk_exact": True,
            },
        }
        report = runner.render_report([row])
        self.assertIn("fixture-model", report)
        self.assertIn("100.0", report)
        self.assertIn("not executed", report)

    def test_run_writes_scored_jsonl_without_executing_the_command(self):
        plan = {
            "kind": "execute",
            "command": "pwd",
            "summary": "Show current directory",
            "risk": "low",
            "required_tools": [],
            "install_candidates": [],
        }
        provider_result = runner.ProviderResult(
            json.dumps(plan), {"input_tokens": 10, "output_tokens": 5}, "fixture"
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.jsonl"
            args = Namespace(
                model=["ollama-gemma3-270m"],
                allow_paid=False,
                limit=1,
                output=str(output),
                resume=False,
                repeat=1,
                retries=0,
                timeout=5,
            )
            with patch.object(runner, "invoke_model", return_value=provider_result):
                runner.run_benchmark(args)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(100, row["score"]["total"])
            self.assertEqual("pwd", row["plan"]["command"])
            self.assertEqual("fixture", row["response_id"])

    def test_run_clears_error_after_a_successful_retry(self):
        plan = {
            "kind": "execute",
            "command": "pwd",
            "summary": "Show current directory",
            "risk": "low",
            "required_tools": [],
            "install_candidates": [],
        }
        provider_result = runner.ProviderResult(
            json.dumps(plan), {"input_tokens": 10, "output_tokens": 5}, "fixture"
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.jsonl"
            args = Namespace(
                model=["ollama-gemma3-270m"],
                allow_paid=False,
                limit=1,
                output=str(output),
                resume=False,
                repeat=1,
                retries=1,
                timeout=5,
            )
            with patch.object(runner.time, "sleep", return_value=None), patch.object(
                runner,
                "invoke_model",
                side_effect=[runner.BenchmarkError("transient"), provider_result],
            ):
                runner.run_benchmark(args)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertIsNone(row["error"])
            self.assertEqual(100, row["score"]["total"])

    def test_result_loader_rejects_empty_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(runner.BenchmarkError):
                runner.load_result_rows([str(path)])


if __name__ == "__main__":
    unittest.main()
