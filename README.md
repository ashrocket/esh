# esh

Type what you mean. Esh turns it into a zsh command with a local LLM, shows you the command, and runs it.

```console
% gh list repositories
gh repo list                 # rendered in light gray italics
ashrocket/esh  public  ...
```

Esh uses [Ollama](https://ollama.com/) on `localhost`. Your prompt, current directory, and generated command stay on your computer—there is no hosted-model fallback.

## How it behaves

- Ordinary shell commands pass through unchanged.
- Plain English and approximate CLI syntax are translated when you press Enter.
- A translated command is printed with ANSI light-gray italic styling, then run.
- High-risk or destructive commands require confirmation.
- If a command needs a missing executable, Esh verifies suggested packages against Homebrew/npm, offers only verified choices, and asks before installing anything.
- A leading space or `Ctrl-X Enter` runs the line raw without asking the model.

The Enter-key widget matters: a command-not-found hook cannot fix input like `gh list repositories`, because `gh` exists and only its subcommand is wrong.

## Requirements

- zsh 5.8 or newer
- macOS with Homebrew (the setup path is currently macOS-first)
- `curl` and `jq`
- Ollama plus a local model; the default is `qwen3:4b-instruct`

The setup command can offer verified Homebrew installations for missing `jq` and Ollama. It always asks first. Pulling the default model requires a few gigabytes of disk space and separate approval.

## Install

```zsh
git clone https://github.com/ashrocket/esh.git ~/.esh
~/.esh/install.zsh
exec zsh
esh setup
```

Or source `esh.plugin.zsh` from your plugin manager instead of running the installer.

Configuration is optional:

```zsh
export ESH_MODEL='qwen3:4b-instruct'
export ESH_API_URL='http://127.0.0.1:11434' # loopback URLs only
export ESH_TIMEOUT=45
```

Set `ESH_ENABLED=0` to temporarily disable Enter-key translation.

## CLI

The plugin is the just-type-English interface. The included CLI is useful for scripts and debugging:

```zsh
esh translate 'gh list repositories'
esh run 'show my GitHub repositories'
esh doctor
esh setup
```

## Safety model

Generated output is treated as untrusted even though the model is local. Esh requires schema-valid JSON, rejects control characters and malformed zsh, prevents translated commands from bypassing the package-install flow, and independently flags dangerous command patterns. Package names proposed by the model are hints only: they must resolve through Homebrew or npm, and npm packages must expose the missing executable under the expected name.

No shell translator can make arbitrary generated commands perfectly safe. Read the gray command as it runs, keep backups, and use the raw bypass for commands the model should not interpret.

## Development

```zsh
zsh -n esh.plugin.zsh lib/esh.zsh bin/esh install.zsh tests/run.zsh
zsh tests/run.zsh
expect tests/widget.exp # optional PTY verification on macOS
```

The integration tests are offline and replace Ollama, GitHub CLI, Homebrew, and npm with deterministic fakes.

## License

MIT
