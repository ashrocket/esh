# Esh model benchmark

This benchmark compares the same Esh command-planning prompt across 100 natural-language terminal requests and ten models. It is separate from the product runtime: Esh itself remains local-only and has no hosted fallback.

## Roster

The committed roster contains six local Ollama models that can run on the manual free-cloud workflow and four paid APIs:

| Path | Models |
|---|---|
| Free/local | Gemma 3 270M, Qwen 3 0.6B, Gemma 3 1B, Qwen 3 1.7B, Qwen 3 4B Instruct, Qwen 3 8B |
| Paid/API | GPT-5.6 Terra, GPT-5.6 Sol, Claude Sonnet 5, Gemini 3.6 Flash |

Groq and xAI Grok are intentionally excluded. Validation fails if a model id, provider, or upstream model name contains `groq` or `grok`.

Model names, tiers, source links, and known token prices live in `models.json`. API prices change, so refresh them from the linked provider documentation immediately before a paid run. A missing price is reported as `n/a`, never guessed.

## Corpus and score

`cases.jsonl` contains exactly ten cases in each of ten categories: navigation, files, search/text, Git, GitHub, system, archives/data, network, packages, and safety. Every request targets the same fixed context: macOS on arm64, zsh, and `/workspace/project`.

Each result is scored out of 100:

- schema validity: 20
- plan kind: 15
- canonical command: 35
- risk classification: 15
- required tools: 10
- required install candidate coverage: 5

Generated commands are untrusted data. The harness does not execute them. It validates and compares JSON strings only.

## Local use

The runner uses only the Python standard library:

```console
python3 benchmarks/runner.py validate
python3 -m unittest discover -s benchmarks/tests
```

Run one local Ollama model after starting Ollama and pulling the model:

```console
ollama pull qwen3:4b-instruct
python3 benchmarks/runner.py run \
  --model ollama-qwen3-4b-instruct \
  --output benchmarks/results/qwen3-4b-instruct.jsonl
python3 benchmarks/runner.py report benchmarks/results/qwen3-4b-instruct.jsonl
```

Use `--limit 5` for a smoke test and `--resume` to continue an interrupted output file. The same output path plus model, case, and repetition form the resume key.

## Free cloud run

Open the repository's Actions tab, choose **Free model benchmark**, select one model or `all`, choose a request limit, and run the workflow. It is `workflow_dispatch`-only: pushes and pull requests never download models or consume benchmark runner time. Each model runs in a fresh job and uploads its JSONL result, Markdown summary, Ollama log, and resolved model metadata.

The workflow installs the pinned official Ollama release after checking its published SHA-256 file. It does not use API keys or hosted model providers.

## Paid APIs

Paid adapters are implemented but deliberately have no automatic workflow. A paid run needs both the provider key and the explicit `--allow-paid` flag:

```console
OPENAI_API_KEY=... python3 benchmarks/runner.py run \
  --model openai-gpt-5.6-terra \
  --limit 5 \
  --allow-paid \
  --output benchmarks/results/openai-smoke.jsonl
```

Supported key variables are `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `GEMINI_API_KEY`. Keys are used only as request headers and are not written to results. Set a provider-side budget and start with `--limit 5`; the flag is an execution acknowledgement, not a spending cap.

## Result format

JSONL rows preserve the case, returned plan, raw model text, latency, token usage, estimated cost when known, score components, response id, and runner provenance. They contain no credential values. Results are ignored by Git so experiments do not silently become product fixtures.
