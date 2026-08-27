#!/usr/bin/env python3
"""Provider-neutral Esh benchmark runner.

The runner sends fixed, non-secret fixtures to model APIs and scores returned
plans. Generated commands are data: they are never executed.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import platform
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "benchmarks"
MODELS_PATH = BENCHMARK_ROOT / "models.json"
CASES_PATH = BENCHMARK_ROOT / "cases.jsonl"
PROMPT_PATH = ROOT / "prompts" / "system.txt"
SCHEMA_PATH = ROOT / "prompts" / "translation.schema.json"
BAN_PATTERN = re.compile(r"(?:groq|grok)", re.IGNORECASE)
TOOL_PATTERN = re.compile(r"^[A-Za-z0-9@._+\-]+$")
PLAN_KEYS = {
    "kind",
    "command",
    "summary",
    "risk",
    "required_tools",
    "install_candidates",
}
INSTALL_KEYS = {"tool", "manager", "kind", "package"}
TARGET_CONTEXT = {
    "operating_system": "Darwin arm64",
    "shell": "zsh",
    "working_directory": "/workspace/project",
}
EXPECTED_CATEGORIES = {
    "navigation",
    "files",
    "search-text",
    "git",
    "github",
    "system",
    "archives-data",
    "network",
    "packages",
    "safety",
}


class BenchmarkError(RuntimeError):
    pass


@dataclass
class ProviderResult:
    text: str
    usage: dict[str, int]
    response_id: str | None = None


class JsonTransport:
    def request(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read(4096).decode("utf-8", errors="replace")
            raise BenchmarkError(f"HTTP {error.code} from {url}: {body}") from error
        except (OSError, ValueError) as error:
            raise BenchmarkError(f"request to {url} failed: {error}") from error


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"cannot load {path}: {error}") from error


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise BenchmarkError(f"{path}:{line_number}: {error}") from error
    except OSError as error:
        raise BenchmarkError(f"cannot load {path}: {error}") from error
    return cases


def load_models(path: Path = MODELS_PATH) -> list[dict[str, Any]]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise BenchmarkError("models.json must use schema_version 1")
    models = payload.get("models")
    if not isinstance(models, list):
        raise BenchmarkError("models.json models must be an array")
    return models


def validate_models(models: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if len(models) != 10:
        errors.append(f"expected exactly 10 models, found {len(models)}")
    seen: set[str] = set()
    free_count = 0
    tier_counts: collections.Counter[str] = collections.Counter()
    for index, model in enumerate(models, 1):
        prefix = f"model {index}"
        if not isinstance(model, dict):
            errors.append(f"{prefix} must be an object")
            continue
        identifier = model.get("id")
        provider = model.get("provider")
        remote_name = model.get("model")
        for field, value in (
            ("id", identifier),
            ("provider", provider),
            ("model", remote_name),
        ):
            if not isinstance(value, str) or not value:
                errors.append(f"{prefix} {field} must be a non-empty string")
            elif BAN_PATTERN.search(value):
                errors.append(
                    f"{prefix} {field} contains an excluded provider or model name"
                )
        if isinstance(identifier, str):
            if identifier in seen:
                errors.append(f"duplicate model id: {identifier}")
            seen.add(identifier)
        tier = model.get("tier")
        if tier not in {"free-local", "paid-api"}:
            errors.append(f"{prefix} has invalid tier: {tier!r}")
        else:
            tier_counts[tier] += 1
        if model.get("free_cloud") is True:
            free_count += 1
            if provider != "ollama" or tier != "free-local":
                errors.append(
                    f"{prefix} free_cloud models must be free-local Ollama models"
                )
        if tier == "paid-api" and not isinstance(model.get("api_key_env"), str):
            errors.append(f"{prefix} paid models must name an api_key_env")
        pricing = model.get("pricing_usd_per_million_tokens")
        if pricing is not None and (
            not isinstance(pricing, dict)
            or set(pricing) != {"input", "output"}
            or not all(
                isinstance(pricing.get(key), (int, float))
                and not isinstance(pricing.get(key), bool)
                and pricing.get(key) >= 0
                for key in ("input", "output")
            )
        ):
            errors.append(
                f"{prefix} pricing_usd_per_million_tokens must be null or an object "
                "with non-negative numeric input/output"
            )
    if free_count != 6:
        errors.append(f"expected exactly 6 free-cloud models, found {free_count}")
    if tier_counts != {"free-local": 6, "paid-api": 4}:
        errors.append(
            f"expected 6 free-local and 4 paid-api models, found {dict(tier_counts)}"
        )
    return errors


def validate_cases(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if len(cases) != 100:
        errors.append(f"expected exactly 100 cases, found {len(cases)}")
    seen: set[str] = set()
    category_counts: collections.Counter[str] = collections.Counter()
    for index, case in enumerate(cases, 1):
        prefix = f"case {index}"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{prefix} id must be a non-empty string")
        elif identifier in seen:
            errors.append(f"duplicate case id: {identifier}")
        else:
            seen.add(identifier)
        category = case.get("category")
        if not isinstance(category, str) or not category:
            errors.append(f"{prefix} category must be a non-empty string")
        elif category not in EXPECTED_CATEGORIES:
            errors.append(f"{prefix} has an unexpected category: {category}")
        else:
            category_counts[category] += 1
        request = case.get("request")
        if not isinstance(request, str) or not request.strip():
            errors.append(f"{prefix} request must be a non-empty string")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{prefix} expected must be an object")
            continue
        if expected.get("kind") not in {"execute", "refuse"}:
            errors.append(f"{prefix} expected kind must be execute or refuse")
        if expected.get("risk") not in {"low", "high"}:
            errors.append(f"{prefix} expected risk must be low or high")
        commands = expected.get("accepted_commands")
        if not isinstance(commands, list) or not all(
            isinstance(item, str) for item in commands
        ):
            errors.append(f"{prefix} accepted_commands must be an array of strings")
        elif expected.get("kind") == "execute" and not commands:
            errors.append(f"{prefix} execute cases need at least one accepted command")
        elif expected.get("kind") == "refuse" and commands:
            errors.append(f"{prefix} refusal cases cannot accept commands")
        tools = expected.get("required_tools")
        if not isinstance(tools, list) or not all(
            isinstance(item, str) and TOOL_PATTERN.fullmatch(item) for item in tools
        ):
            errors.append(f"{prefix} required_tools contains an invalid tool")
        candidates = expected.get("install_candidates", [])
        if not isinstance(candidates, list):
            errors.append(f"{prefix} install_candidates must be an array")
        else:
            for candidate in candidates:
                if (
                    not isinstance(candidate, dict)
                    or set(candidate) != INSTALL_KEYS
                    or candidate.get("manager") not in {"brew", "npm"}
                    or candidate.get("kind") not in {"formula", "cask", "package"}
                    or not all(
                        isinstance(candidate.get(key), str) and candidate.get(key)
                        for key in INSTALL_KEYS
                    )
                ):
                    errors.append(f"{prefix} has an invalid install candidate")
                    break
    wrong_categories = {
        category: category_counts.get(category, 0)
        for category in EXPECTED_CATEGORIES
        if category_counts.get(category, 0) != 10
    }
    if wrong_categories:
        errors.append(
            "expected exactly 10 cases in every category; found "
            f"{dict(sorted(wrong_categories.items()))}"
        )
    return errors


def validate_repository() -> None:
    errors = validate_models(load_models()) + validate_cases(load_cases())
    schema = load_json(SCHEMA_PATH)
    if set(schema.get("required", [])) != PLAN_KEYS:
        errors.append(
            "translation schema required fields no longer match the benchmark validator"
        )
    if errors:
        raise BenchmarkError("benchmark validation failed:\n- " + "\n- ".join(errors))


def portable_schema(value: Any) -> Any:
    """Use the common structured-output subset; local validation remains strict."""
    if isinstance(value, dict):
        return {
            key: portable_schema(child)
            for key, child in value.items()
            if key not in {"$schema", "pattern"}
        }
    if isinstance(value, list):
        return [portable_schema(child) for child in value]
    return value


def build_user_message(request: str) -> str:
    return (
        f"Operating system: {TARGET_CONTEXT['operating_system']}\n"
        f"Shell: {TARGET_CONTEXT['shell']}\n"
        f"Working directory: {TARGET_CONTEXT['working_directory']}\n"
        f"Terminal line: {request}"
    )


def require_api_key(model: dict[str, Any]) -> str:
    variable = model.get("api_key_env")
    key = os.environ.get(variable or "")
    if not key:
        raise BenchmarkError(f"{variable} is required for {model['id']}")
    return key


def ensure_local_ollama_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise BenchmarkError("OLLAMA_HOST must be an HTTP loopback URL")
    return url.rstrip("/")


def invoke_model(
    model: dict[str, Any],
    system_prompt: str,
    user_message: str,
    schema: dict[str, Any],
    timeout: float,
    transport: JsonTransport | Any | None = None,
) -> ProviderResult:
    transport = transport or JsonTransport()
    provider = model["provider"]
    model_name = model["model"]
    output_schema = portable_schema(schema)

    if provider == "ollama":
        base_url = ensure_local_ollama_url(
            os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        )
        response = transport.request(
            f"{base_url}/api/chat",
            {},
            {
                "model": model_name,
                "stream": False,
                "think": False,
                "format": output_schema,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            },
            timeout,
        )
        usage = {
            "input_tokens": int(response.get("prompt_eval_count") or 0),
            "output_tokens": int(response.get("eval_count") or 0),
        }
        return ProviderResult(response["message"]["content"], usage)

    if provider == "openai":
        key = require_api_key(model)
        response = transport.request(
            "https://api.openai.com/v1/responses",
            {"Authorization": f"Bearer {key}"},
            {
                "model": model_name,
                "store": False,
                "max_output_tokens": 2048,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "esh_shell_plan",
                        "strict": True,
                        "schema": output_schema,
                    }
                },
            },
            timeout,
        )
        text = next(
            content["text"]
            for output in response.get("output", [])
            if output.get("type") == "message"
            for content in output.get("content", [])
            if content.get("type") == "output_text"
        )
        raw_usage = response.get("usage") or {}
        usage = {
            "input_tokens": int(raw_usage.get("input_tokens") or 0),
            "output_tokens": int(raw_usage.get("output_tokens") or 0),
        }
        return ProviderResult(text, usage, response.get("id"))

    if provider == "anthropic":
        key = require_api_key(model)
        response = transport.request(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {
                "model": model_name,
                "max_tokens": 2048,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
                "output_config": {
                    "format": {"type": "json_schema", "schema": output_schema}
                },
            },
            timeout,
        )
        text = next(
            item["text"]
            for item in response.get("content", [])
            if item.get("type") == "text"
        )
        raw_usage = response.get("usage") or {}
        usage = {
            "input_tokens": int(raw_usage.get("input_tokens") or 0),
            "output_tokens": int(raw_usage.get("output_tokens") or 0),
        }
        return ProviderResult(text, usage, response.get("id"))

    if provider == "gemini":
        key = require_api_key(model)
        response = transport.request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            {"x-goog-api-key": key},
            {
                "model": model_name,
                "store": False,
                "system_instruction": system_prompt,
                "input": user_message,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": output_schema,
                },
            },
            timeout,
        )
        text = next(
            content["text"]
            for step in response.get("steps", [])
            if step.get("type") == "model_output"
            for content in step.get("content", [])
            if content.get("type") == "text"
        )
        raw_usage = response.get("usage") or {}
        usage = {
            "input_tokens": int(raw_usage.get("total_input_tokens") or 0),
            "output_tokens": int(raw_usage.get("total_output_tokens") or 0),
        }
        return ProviderResult(text, usage, response.get("id"))

    raise BenchmarkError(f"unsupported provider: {provider}")


def validate_plan(plan: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan is not an object"]
    if set(plan) != PLAN_KEYS:
        errors.append("plan fields do not match the schema")
    if plan.get("kind") not in {"passthrough", "execute", "refuse"}:
        errors.append("invalid kind")
    command = plan.get("command")
    if not isinstance(command, str):
        errors.append("command is not a string")
    elif plan.get("kind") == "refuse" and command:
        errors.append("refusal command is not empty")
    elif plan.get("kind") != "refuse" and (
        not command or len(command) > 4096 or "\n" in command or "\r" in command
    ):
        errors.append("command is empty, oversized, or multiline")
    if not isinstance(plan.get("summary"), str):
        errors.append("summary is not a string")
    if plan.get("risk") not in {"low", "high"}:
        errors.append("invalid risk")
    tools = plan.get("required_tools")
    if not isinstance(tools, list) or not all(
        isinstance(item, str) and TOOL_PATTERN.fullmatch(item) for item in tools
    ):
        errors.append("invalid required_tools")
    candidates = plan.get("install_candidates")
    if not isinstance(candidates, list):
        errors.append("install_candidates is not an array")
    else:
        for candidate in candidates:
            if (
                not isinstance(candidate, dict)
                or set(candidate) != INSTALL_KEYS
                or candidate.get("manager") not in {"brew", "npm"}
                or candidate.get("kind") not in {"formula", "cask", "package"}
                or not all(isinstance(candidate.get(key), str) for key in INSTALL_KEYS)
            ):
                errors.append("invalid install candidate")
                break
    return errors


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def candidate_tuple(candidate: dict[str, str]) -> tuple[str, str, str, str]:
    return tuple(candidate[key] for key in ("tool", "manager", "kind", "package"))


def score_plan(case: dict[str, Any], plan: Any) -> dict[str, Any]:
    schema_errors = validate_plan(plan)
    if schema_errors:
        return {
            "total": 0,
            "schema_valid": False,
            "kind_exact": False,
            "command_exact": False,
            "risk_exact": False,
            "tools_exact": False,
            "install_candidates_match": False,
            "schema_errors": schema_errors,
        }

    expected = case["expected"]
    kind_exact = plan["kind"] == expected["kind"]
    accepted = {normalize_command(item) for item in expected["accepted_commands"]}
    command_exact = (
        plan["command"] == ""
        if expected["kind"] == "refuse"
        else normalize_command(plan["command"]) in accepted
    )
    risk_exact = plan["risk"] == expected["risk"]
    tools_exact = set(plan["required_tools"]) == set(expected["required_tools"])
    expected_candidates = {
        candidate_tuple(item) for item in expected.get("install_candidates", [])
    }
    actual_candidates = {candidate_tuple(item) for item in plan["install_candidates"]}
    candidates_match = not expected_candidates or expected_candidates.issubset(
        actual_candidates
    )
    total = (
        20
        + 15 * kind_exact
        + 35 * command_exact
        + 15 * risk_exact
        + 10 * tools_exact
        + 5 * candidates_match
    )
    return {
        "total": total,
        "schema_valid": True,
        "kind_exact": kind_exact,
        "command_exact": command_exact,
        "risk_exact": risk_exact,
        "tools_exact": tools_exact,
        "install_candidates_match": candidates_match,
        "schema_errors": [],
    }


def estimate_cost(model: dict[str, Any], usage: dict[str, int]) -> float | None:
    pricing = model.get("pricing_usd_per_million_tokens")
    if not isinstance(pricing, dict):
        return None
    return round(
        (
            usage.get("input_tokens", 0) * pricing["input"]
            + usage.get("output_tokens", 0) * pricing["output"]
        )
        / 1_000_000,
        8,
    )


def read_completed(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    completed: set[tuple[str, str, int]] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            completed.add((row["model_id"], row["case_id"], int(row["repetition"])))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            # A run that was interrupted mid-write can leave a truncated
            # final line. Skip it so --resume retries that request instead
            # of crashing on state from an unfinished previous run.
            print(
                f"benchmark: ignoring unreadable line {line_number} in {path} "
                "(resume will retry it)",
                file=sys.stderr,
            )
    return completed


def run_benchmark(args: argparse.Namespace) -> None:
    validate_repository()
    models = {model["id"]: model for model in load_models()}
    missing = [identifier for identifier in args.model if identifier not in models]
    if missing:
        raise BenchmarkError(f"unknown model id(s): {', '.join(missing)}")
    selected = [models[identifier] for identifier in args.model]
    paid = [model["id"] for model in selected if model["tier"] == "paid-api"]
    if paid and not args.allow_paid:
        raise BenchmarkError("paid models require --allow-paid: " + ", ".join(paid))

    cases = load_cases()
    if args.limit is not None:
        if args.limit < 1 or args.limit > len(cases):
            raise BenchmarkError(f"--limit must be between 1 and {len(cases)}")
        cases = cases[: args.limit]
    output = (
        Path(args.output)
        if args.output
        else BENCHMARK_ROOT
        / "results"
        / f"run-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = read_completed(output) if args.resume else set()
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    schema = load_json(SCHEMA_PATH)

    for model in selected:
        for repetition in range(1, args.repeat + 1):
            for case in cases:
                key = (model["id"], case["id"], repetition)
                if key in completed:
                    continue
                print(
                    f"[{model['id']}] {case['id']} ({repetition}/{args.repeat})",
                    file=sys.stderr,
                )
                started = time.monotonic()
                result: ProviderResult | None = None
                error: str | None = None
                for attempt in range(args.retries + 1):
                    try:
                        result = invoke_model(
                            model,
                            system_prompt,
                            build_user_message(case["request"]),
                            schema,
                            args.timeout,
                        )
                        error = None
                        break
                    except (
                        BenchmarkError,
                        KeyError,
                        StopIteration,
                        TypeError,
                        ValueError,
                    ) as caught:
                        error = str(caught)
                        if attempt < args.retries:
                            time.sleep(min(2**attempt, 8))
                latency_ms = round((time.monotonic() - started) * 1000, 2)
                plan: Any = None
                raw_response = result.text if result else None
                if result:
                    try:
                        plan = json.loads(result.text)
                    except json.JSONDecodeError as caught:
                        error = f"invalid JSON: {caught}"
                score = score_plan(case, plan)
                usage = (
                    result.usage if result else {"input_tokens": 0, "output_tokens": 0}
                )
                row = {
                    "schema_version": 1,
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "model_id": model["id"],
                    "provider": model["provider"],
                    "model": model["model"],
                    "tier": model["tier"],
                    "case_id": case["id"],
                    "category": case["category"],
                    "request": case["request"],
                    "repetition": repetition,
                    "target_context": TARGET_CONTEXT,
                    "plan": plan,
                    "raw_response": raw_response,
                    "error": error,
                    "latency_ms": latency_ms,
                    "usage": usage,
                    "estimated_cost_usd": estimate_cost(model, usage),
                    "score": score,
                    "response_id": result.response_id if result else None,
                    "runner": {
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                    },
                }
                with output.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(row, separators=(",", ":"), ensure_ascii=False)
                        + "\n"
                    )
    print(output)


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def load_result_rows(paths: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise BenchmarkError(f"{path}:{line_number}: {error}") from error
    if not rows:
        raise BenchmarkError("no result rows found")
    return rows


def render_report(rows: list[dict[str, Any]]) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["model_id"], []).append(row)
    lines = [
        "# Esh model benchmark",
        "",
        "| Model | Cases | Score | Schema | Command | Safety | p50 ms | p95 ms | Tokens | Est. cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model_id, model_rows in sorted(groups.items()):
        count = len(model_rows)
        scores = [float(row["score"]["total"]) for row in model_rows]
        schema_rate = (
            sum(bool(row["score"]["schema_valid"]) for row in model_rows) / count
        )
        command_rate = (
            sum(bool(row["score"]["command_exact"]) for row in model_rows) / count
        )
        safety_rate = (
            sum(bool(row["score"]["risk_exact"]) for row in model_rows) / count
        )
        latencies = [float(row["latency_ms"]) for row in model_rows]
        tokens = sum(
            int(row.get("usage", {}).get("input_tokens", 0))
            + int(row.get("usage", {}).get("output_tokens", 0))
            for row in model_rows
        )
        costs = [row.get("estimated_cost_usd") for row in model_rows]
        known_cost = all(cost is not None for cost in costs)
        cost_label = f"${sum(costs):.4f}" if known_cost else "n/a"
        lines.append(
            f"| {model_id} | {count} | {statistics.mean(scores):.1f} | "
            f"{schema_rate:.1%} | {command_rate:.1%} | {safety_rate:.1%} | "
            f"{percentile(latencies, 0.50):.0f} | {percentile(latencies, 0.95):.0f} | "
            f"{tokens} | {cost_label} |"
        )
    lines.extend(
        [
            "",
            "Scores weight schema validity (20), plan kind (15), exact canonical command (35), "
            "risk classification (15), required tools (10), and required install candidate coverage (5).",
            "Safety is the exact risk-classification rate. Generated commands were scored as data and were not executed.",
        ]
    )
    return "\n".join(lines) + "\n"


def report_results(args: argparse.Namespace) -> None:
    report = render_report(load_result_rows(args.paths))
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(args.output)
    else:
        print(report, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="validate the roster and 100-case corpus")

    run_parser = subparsers.add_parser("run", help="run one or more benchmark models")
    run_parser.add_argument(
        "--model",
        action="append",
        required=True,
        help="model id; repeat for multiple models",
    )
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--repeat", type=int, default=1)
    run_parser.add_argument("--retries", type=int, default=2)
    run_parser.add_argument("--timeout", type=float, default=120.0)
    run_parser.add_argument("--output")
    run_parser.add_argument("--resume", action="store_true")
    run_parser.add_argument("--allow-paid", action="store_true")

    report_parser = subparsers.add_parser("report", help="render a Markdown comparison")
    report_parser.add_argument("paths", nargs="+")
    report_parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            validate_repository()
            print("benchmark corpus valid: 100 cases, 10 models, 6 free-cloud models")
        elif args.command == "run":
            if args.repeat < 1 or args.retries < 0 or args.timeout <= 0:
                raise BenchmarkError(
                    "repeat and timeout must be positive; retries cannot be negative"
                )
            run_benchmark(args)
        elif args.command == "report":
            report_results(args)
        return 0
    except (BenchmarkError, OSError) as error:
        print(f"benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
