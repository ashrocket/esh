# Free-model baseline: GitHub Actions, 2026-08-08

This is the first Esh command-planning baseline for the six free Ollama models in the ten-model roster. Five models completed all 100 requests. Qwen 3 0.6B reached 99 requests but exceeded the three-hour job guardrail, so its metrics are partial and are not ranked with the completed runs.

## Run provenance

- Benchmark commit: [`d328c1a8a9164a589a54f4e1ce76c294af15a4bf`](https://github.com/ashrocket/esh/commit/d328c1a8a9164a589a54f4e1ce76c294af15a4bf)
- Full workflow: [GitHub Actions run 31279543913](https://github.com/ashrocket/esh/actions/runs/31279543913)
- Runner: GitHub-hosted `ubuntu-latest`, Python 3.12, Ollama 0.32.6
- Settings: one repetition, 100 requests, 120-second request timeout, two retries, two model jobs in parallel, 180-minute job limit
- Safety: generated commands were scored as strings and never executed

## Results

| Model | Ollama artifact | Size | Cases | Request errors | Score | Schema | Command | Risk | p50 ms | p95 ms | Job |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Qwen 3 8B | `qwen3:8b` (`500a1f067a9f`) | 5.2 GB | 100/100 | 1 | 75.5 | 99% | 53% | 87% | 12,575 | 24,720 | Completed |
| Qwen 3 4B Instruct | `qwen3:4b-instruct` (`0edcdef34593`) | 2.5 GB | 100/100 | 0 | 75.2 | 100% | 49% | 86% | 11,311 | 18,464 | Completed |
| Qwen 3 1.7B | `qwen3:1.7b` (`8f68893c685c`) | 1.4 GB | 100/100 | 0 | 61.9 | 100% | 31% | 44% | 5,944 | 9,262 | Completed |
| Gemma 3 1B | `gemma3:1b` (`8648f39daa8f`) | 815 MB | 100/100 | 0 | 40.0 | 98% | 2% | 82% | 12,832 | 14,071 | Completed |
| Gemma 3 270M | `gemma3:270m` (`e7d36fb2c3b3`) | 291 MB | 100/100 | 0 | 27.3 | 100% | 1% | 15% | 2,332 | 4,081 | Completed |
| Qwen 3 0.6B | `qwen3:0.6b` (`7df6b6e09427`) | 522 MB | 99/100 | 35 | 32.3* | 67.7%* | 4.0%* | 57.6%* | 3,838* | 363,300* | Timed out |

`*` Qwen 3 0.6B metrics describe the retained 99-row partial artifact. Thirty-five requests exhausted all retries, the final `safety-010` case did not run, and GitHub cancelled the benchmark step at the 180-minute job limit.

“Risk” is exact risk-classification accuracy. “Command” requires the exact canonical command in the case fixture. The overall score weights schema validity (20), plan kind (15), canonical command (35), risk (15), required tools (10), and install-candidate coverage (5).

## Decision

Keep `qwen3:4b-instruct` as Esh's default local model. The 8B model gained only 0.3 points, while its Ollama artifact is 2.7 GB larger and it produced the only request error among the five completed models: `search-text-001` exhausted its request timeout. The 4B model also had lower p50 and p95 latency in this run.

Qwen 3 1.7B is the only plausible smaller fallback from this set, but it lost 13.3 points overall and its exact risk classification fell to 44%. The Gemma models did not produce canonical commands reliably enough for Esh. Qwen 3 0.6B is disqualified on this cloud runner configuration because it did not finish the corpus within three hours.

These results measure one exact-string command-planning corpus on shared CPU runners, not general model intelligence. A future baseline should add repetitions or semantic command equivalence before treating sub-point differences as meaningful.

The four paid API models remain configured but were not called in this run. They require explicit credentials, `--allow-paid`, and a separately authorized spend cap.
