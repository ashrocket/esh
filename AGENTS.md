# Esh contributor guidance

## Product contract

Esh is a zsh plugin that turns plain-English terminal input into shell commands with a local Ollama model. The interactive Enter-key widget must:

1. Preserve valid shell input unchanged.
2. Translate informal input before execution.
3. Print translated commands in dim/light-gray italics.
4. Execute low-risk translations immediately.
5. Require approval for high-risk commands.
6. Offer only registry-verified Homebrew or npm choices for missing tools, then require approval before installation.

No command-planning request may leave localhost. Do not add hosted-model fallbacks.

The provider comparison under `benchmarks/` is research tooling, not a runtime fallback. Keep it isolated from the plugin and require `--allow-paid` before any paid API call. Never add Groq or xAI Grok to the roster. Never execute a model-generated benchmark command.

## Architecture

- `esh.plugin.zsh` wires the zsh line editor to the library.
- `lib/esh.zsh` owns model calls, response validation, safety checks, installation choices, and the CLI.
- `bin/esh` exposes `translate`, `run`, `doctor`, and `setup` outside the widget.
- `prompts/` contains the model contract and its JSON schema.
- `tests/run.zsh` is an offline integration suite with fake Ollama and package-manager executables.
- `benchmarks/` contains the fixed 100-request corpus, ten-model roster, provider-neutral runner, and offline adapter tests.
- `.github/workflows/benchmark.yml` is a manual-only free Ollama benchmark; ordinary CI validates fixtures but performs no inference.

## Verification

Run these before committing:

```zsh
zsh -n esh.plugin.zsh lib/esh.zsh bin/esh install.zsh tests/run.zsh
zsh tests/run.zsh
shellcheck -s bash bin/esh
expect tests/widget.exp
python3 benchmarks/runner.py validate
python3 -m unittest discover -s benchmarks/tests
```

Review `git diff --check` and the complete staged diff. For widget changes, also verify behavior in an interactive zsh PTY.
