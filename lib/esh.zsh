# Core Esh behavior. This file is sourced by both the plugin and bin/esh.

if [[ -z ${ESH_ROOT:-} ]]; then
  typeset -g ESH_ROOT="${${(%):-%N}:A:h:h}"
fi

typeset -g ESH_MODEL="${ESH_MODEL:-qwen3:4b-instruct}"
typeset -g ESH_API_URL="${ESH_API_URL:-http://127.0.0.1:11434}"
typeset -g ESH_TIMEOUT="${ESH_TIMEOUT:-45}"
typeset -g ESH_ENABLED="${ESH_ENABLED:-1}"

typeset -ga _ESH_OPTION_MANAGERS
typeset -ga _ESH_OPTION_KINDS
typeset -ga _ESH_OPTION_PACKAGES
typeset -g _ESH_PREPARED_COMMAND=''
typeset -g _ESH_PREPARED_KIND=''

_esh_error() {
  print -u2 -r -- "esh: $*"
}

_esh_preview() {
  # ANSI SGR 3 is italic; 90 is bright black/dim gray on standard terminals.
  printf '\033[3;90m%s\033[0m\n' "$1"
}

_esh_have() {
  (( $+commands[$1] )) || (( $+functions[$1] )) || (( $+builtins[$1] ))
}

_esh_require_runtime() {
  local missing=()
  _esh_have curl || missing+=(curl)
  _esh_have jq || missing+=(jq)

  if (( ${#missing} )); then
    _esh_error "missing runtime dependency: ${missing[*]}"
    _esh_error 'run `esh setup` to install supported dependencies'
    return 1
  fi
}

_esh_validate_plan() {
  command jq -ce '
    type == "object" and
    ((.kind == "passthrough") or (.kind == "execute") or (.kind == "refuse")) and
    (.command | type == "string") and
    (.summary | type == "string") and
    ((.risk == "low") or (.risk == "high")) and
    (.required_tools | type == "array" and all(.[]; type == "string" and test("^[A-Za-z0-9@._+-]+$"))) and
    (.install_candidates | type == "array" and all(.[];
      type == "object" and
      (.tool | type == "string") and
      ((.manager == "brew") or (.manager == "npm")) and
      ((.kind == "formula") or (.kind == "cask") or (.kind == "package")) and
      (.package | type == "string")
    )) and
    (if .kind == "refuse" then .command == "" else (.command | length > 0 and length <= 4096) end)
  ' >/dev/null
}

_esh_request_plan() {
  local input="$1"
  local system_prompt schema user_message payload response content plan

  _esh_require_runtime || return 1

  _esh_api_is_local || {
    _esh_error 'ESH_API_URL must use localhost or a loopback address'
    return 1
  }

  system_prompt="$(<"$ESH_ROOT/prompts/system.txt")" || {
    _esh_error 'cannot read the system prompt'
    return 1
  }
  schema="$(<"$ESH_ROOT/prompts/translation.schema.json")" || {
    _esh_error 'cannot read the response schema'
    return 1
  }

  user_message="Operating system: $(uname -s) $(uname -m)
Shell: zsh
Working directory: $PWD
Terminal line: $input"

  payload="$(command jq -cn \
    --arg model "$ESH_MODEL" \
    --arg system "$system_prompt" \
    --arg input "$user_message" \
    --argjson schema "$schema" \
    '{
      model: $model,
      stream: false,
      think: false,
      format: $schema,
      options: {temperature: 0},
      messages: [
        {role: "system", content: $system},
        {role: "user", content: $input}
      ]
    }')" || {
      _esh_error 'could not construct the local-model request'
      return 1
    }

  response="$(command curl --silent --show-error --fail-with-body \
    --max-time "$ESH_TIMEOUT" \
    --header 'Content-Type: application/json' \
    --data-binary "$payload" \
    "$ESH_API_URL/api/chat")" || {
      _esh_error "local model unavailable at $ESH_API_URL; run \`esh setup\`"
      return 1
    }

  content="$(print -r -- "$response" | command jq -er '.message.content')" || {
    local api_error
    api_error="$(print -r -- "$response" | command jq -r '.error // "invalid Ollama response"' 2>/dev/null)"
    _esh_error "$api_error"
    return 1
  }

  plan="$(print -r -- "$content" | command jq -ce '.')" || {
    _esh_error 'the local model returned invalid JSON'
    return 1
  }

  if ! print -r -- "$plan" | _esh_validate_plan; then
    _esh_error 'the local model returned a plan that failed validation'
    return 1
  fi

  print -r -- "$plan"
}

_esh_api_is_local() {
  [[ "$ESH_API_URL" == http://127.0.0.1(|:<->) ||
     "$ESH_API_URL" == http://localhost(|:<->) ||
     "$ESH_API_URL" == http://\[::1\](|:<->) ]]
}

_esh_validate_command() {
  local command_text="$1"

  if [[ -z "$command_text" || ${#command_text} -gt 4096 ]]; then
    _esh_error 'refusing an empty or oversized command'
    return 1
  fi

  if [[ "$command_text" == *$'\n'* || "$command_text" == *$'\r'* || "$command_text" == *$'\0'* ]]; then
    _esh_error 'refusing a command containing control characters'
    return 1
  fi

  # Ask zsh to lex the command. This catches unmatched quotes and malformed syntax.
  local -a words
  words=("${(z)command_text}") 2>/dev/null || {
    _esh_error 'refusing a command with invalid zsh syntax'
    return 1
  }

  (( ${#words} )) || {
    _esh_error 'refusing a command without executable content'
    return 1
  }
}

_esh_primary_tool() {
  local command_text="$1" token name
  local -a words
  words=("${(z)command_text}") 2>/dev/null || return 1

  for token in "${words[@]}"; do
    # Skip leading variable assignments.
    if [[ "$token" == *=* ]]; then
      name="${token%%=*}"
      if [[ -n "$name" && "$name" != [0-9]* && "$name" != *[^A-Za-z0-9_]* ]]; then
        continue
      fi
    fi

    case "$token" in
      command|builtin|noglob|nocorrect|time)
        continue
        ;;
      '('|')'|'{'|'}'|'!'|';'|'&&'|'||'|'|')
        continue
        ;;
    esac

    # Paths cannot be resolved through a package name safely.
    [[ "$token" == */* ]] && return 1
    print -r -- "$token"
    return 0
  done

  return 1
}

_esh_pad_operators() {
  # Insert spaces around command separators so a chained or substituted
  # command cannot hide a dangerous word from the checks below by omitting
  # whitespace, e.g. "true;rm -rf ." or "$(rm -rf /)".
  local text=" ${1:l} "
  text="${text//;/ ; }"
  text="${text//&/ & }"
  text="${text//[(]/ ( }"
  text="${text//[)]/ ) }"
  print -r -- "$text"
}

_esh_is_install_command() {
  local padded
  padded="$(_esh_pad_operators "$1")"
  [[ "$padded" == *' brew install '* ||
     "$padded" == *' npm install '* ||
     "$padded" == *' npm i '* ||
     "$padded" == *' npm add '* ||
     "$padded" == *' npx '* ||
     "$padded" == *' pnpm add '* ||
     "$padded" == *' yarn add '* ||
     "$padded" == *' pip install '* ||
     "$padded" == *' pip3 install '* ||
     "$padded" == *' gem install '* ||
     "$padded" == *' cargo install '* ||
     "$padded" == *' go install '* ]]
}

_esh_is_high_risk() {
  local padded
  padded="$(_esh_pad_operators "$1")"
  [[ "$padded" == *' sudo '* ||
     "$padded" == *' doas '* ||
     "$padded" == *' rm '* ||
     "$padded" == *' rmdir '* ||
     "$padded" == *' dd '* ||
     "$padded" == *' mkfs'* ||
     "$padded" == *' diskutil erase'* ||
     "$padded" == *' git reset --hard '* ||
     "$padded" == *' git clean -'* ||
     "$padded" == *' git push '* ||
     "$padded" == *' gh repo delete '* ||
     "$padded" == *' chmod -r'* ||
     "$padded" == *' chown -r'* ||
     "$padded" == *' kill -9 '* ||
     "$padded" == *' curl '*'| sh '* ||
     "$padded" == *' curl '*'| bash '* ||
     "$padded" == *' wget '*'| sh '* ||
     "$padded" == *'> /dev/'* ]]
}

_esh_read_reply() {
  local prompt="$1"
  local reply

  if [[ -n ${ESH_TEST_REPLY:-} ]]; then
    reply="$ESH_TEST_REPLY"
  elif [[ -r /dev/tty ]]; then
    read -r "reply?$prompt" </dev/tty || return 1
  else
    return 1
  fi

  print -r -- "$reply"
}

_esh_confirm() {
  local prompt="$1"
  local reply

  if [[ ${ESH_ASSUME_YES:-0} == 1 ]]; then
    return 0
  fi

  reply="$(_esh_read_reply "$prompt [y/N] ")" || return 1
  [[ "${reply:l}" == y || "${reply:l}" == yes ]]
}

_esh_valid_package_name() {
  [[ -n "$1" &&
     "$1" != *[^A-Za-z0-9@._+/-]* &&
     "$1" != -* &&
     "$1" != /* &&
     "$1" != *'..'* ]]
}

_esh_verify_brew() {
  local kind="$1" package="$2"
  _esh_have brew || return 1
  _esh_valid_package_name "$package" || return 1

  case "$kind" in
    formula)
      command brew info --formula --json=v2 "$package" >/dev/null 2>&1
      ;;
    cask)
      command brew info --cask --json=v2 "$package" >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

_esh_verify_npm() {
  local package="$1" tool="$2" metadata
  _esh_have npm || return 1
  _esh_valid_package_name "$package" || return 1

  metadata="$(command npm view "$package" name bin --json 2>/dev/null)" || return 1
  print -r -- "$metadata" | command jq -e --arg tool "$tool" '
    (.name | type == "string") and
    (
      ((.bin | type == "object") and (.bin | has($tool))) or
      ((.bin | type == "string") and ((.name | split("/") | last) == $tool))
    )
  ' >/dev/null 2>&1
}

_esh_add_install_option() {
  local manager="$1" kind="$2" package="$3"
  local i

  for (( i = 1; i <= ${#_ESH_OPTION_PACKAGES}; i++ )); do
    if [[ "${_ESH_OPTION_MANAGERS[$i]}" == "$manager" &&
          "${_ESH_OPTION_KINDS[$i]}" == "$kind" &&
          "${_ESH_OPTION_PACKAGES[$i]}" == "$package" ]]; then
      return 0
    fi
  done

  _ESH_OPTION_MANAGERS+=("$manager")
  _ESH_OPTION_KINDS+=("$kind")
  _ESH_OPTION_PACKAGES+=("$package")
}

_esh_collect_install_options() {
  local tool="$1" plan="$2"
  local manager kind package

  _ESH_OPTION_MANAGERS=()
  _ESH_OPTION_KINDS=()
  _ESH_OPTION_PACKAGES=()

  while IFS=$'\t' read -r manager kind package; do
    [[ -n "$manager" && -n "$kind" && -n "$package" ]] || continue
    _esh_valid_package_name "$package" || continue

    if [[ "$manager" == brew ]] && _esh_verify_brew "$kind" "$package"; then
      _esh_add_install_option "$manager" "$kind" "$package"
    elif [[ "$manager" == npm && "$kind" == package ]] && _esh_verify_npm "$package" "$tool"; then
      _esh_add_install_option "$manager" "$kind" "$package"
    fi
  done < <(print -r -- "$plan" | command jq -r --arg tool "$tool" '
    .install_candidates[]? |
    select(.tool == $tool) |
    [.manager, .kind, .package] | @tsv
  ')

  # A same-name package is a useful conservative fallback, but it is still
  # shown only after the package manager verifies it exists and exposes the CLI.
  if _esh_verify_brew formula "$tool"; then
    _esh_add_install_option brew formula "$tool"
  fi
  if _esh_verify_brew cask "$tool"; then
    _esh_add_install_option brew cask "$tool"
  fi
  if _esh_verify_npm "$tool" "$tool"; then
    _esh_add_install_option npm package "$tool"
  fi
}

_esh_install_option_command() {
  local manager="$1" kind="$2" package="$3"
  if [[ "$manager" == brew ]]; then
    print -r -- "brew install --$kind ${(q)package}"
  else
    print -r -- "npm install --global ${(q)package}"
  fi
}

_esh_offer_install() {
  local tool="$1" plan="$2"
  local i selection manager kind package install_command

  _esh_error "required tool not found: $tool"
  _esh_collect_install_options "$tool" "$plan"

  if (( ! ${#_ESH_OPTION_PACKAGES} )); then
    _esh_error 'no verified Homebrew or npm installation option was found'
    return 1
  fi

  print -u2 -r -- 'Verified installation options:'
  for (( i = 1; i <= ${#_ESH_OPTION_PACKAGES}; i++ )); do
    install_command="$(_esh_install_option_command \
      "${_ESH_OPTION_MANAGERS[$i]}" \
      "${_ESH_OPTION_KINDS[$i]}" \
      "${_ESH_OPTION_PACKAGES[$i]}")"
    print -u2 -r -- "  $i) $install_command"
  done
  print -u2 -r -- '  0) cancel'

  if [[ -n ${ESH_INSTALL_SELECTION:-} ]]; then
    selection="$ESH_INSTALL_SELECTION"
  else
    selection="$(_esh_read_reply 'Choose an option [0]: ')" || return 1
  fi
  [[ -n "$selection" ]] || selection=0
  [[ "$selection" == <-> ]] || {
    _esh_error 'invalid selection'
    return 1
  }
  (( selection >= 1 && selection <= ${#_ESH_OPTION_PACKAGES} )) || return 1

  manager="${_ESH_OPTION_MANAGERS[$selection]}"
  kind="${_ESH_OPTION_KINDS[$selection]}"
  package="${_ESH_OPTION_PACKAGES[$selection]}"
  install_command="$(_esh_install_option_command "$manager" "$kind" "$package")"

  _esh_confirm "Run $install_command?" || {
    _esh_error 'installation cancelled'
    return 1
  }

  if [[ "$manager" == brew ]]; then
    command brew install "--$kind" "$package" || return 1
  else
    command npm install --global "$package" || return 1
  fi

  rehash
  _esh_have "$tool" || {
    _esh_error "$tool is still unavailable after installation"
    return 1
  }
}

_esh_prepare_plan() {
  local plan="$1" original_input="$2" execution_mode="${3:-run}"
  local kind command_text summary risk tool primary_tool
  local -a required_tools

  kind="$(print -r -- "$plan" | command jq -r '.kind')"
  command_text="$(print -r -- "$plan" | command jq -r '.command')"
  summary="$(print -r -- "$plan" | command jq -r '.summary')"
  risk="$(print -r -- "$plan" | command jq -r '.risk')"

  if [[ "$kind" == refuse ]]; then
    _esh_error "refused: $summary"
    return 1
  fi

  _esh_validate_command "$command_text" || return 1

  if [[ "$kind" == passthrough && "$command_text" != "$original_input" ]]; then
    _esh_error 'the model changed a command marked as passthrough'
    return 1
  fi

  if [[ "$kind" != passthrough ]] && _esh_is_install_command "$command_text"; then
    _esh_error 'the model attempted to bypass the verified installation flow'
    return 1
  fi

  if [[ "$execution_mode" == run ]]; then
    required_tools=("${(@f)$(print -r -- "$plan" | command jq -r '.required_tools[]?')}")
    primary_tool="$(_esh_primary_tool "$command_text")" || primary_tool=''
    [[ -n "$primary_tool" ]] && required_tools+=("$primary_tool")
    for tool in "${required_tools[@]}"; do
      [[ -n "$tool" ]] || continue
      if ! _esh_have "$tool"; then
        _esh_offer_install "$tool" "$plan" || return 1
      fi
    done

    if [[ "$risk" == high ]] || _esh_is_high_risk "$command_text" || _esh_is_install_command "$command_text"; then
      _esh_preview "$command_text" >&2
      _esh_confirm 'Run this high-risk command?' || {
        _esh_error 'command cancelled'
        return 1
      }
    fi
  fi

  _ESH_PREPARED_COMMAND="$command_text"
  _ESH_PREPARED_KIND="$kind"
}

_esh_run_input() {
  local input="$1" plan

  plan="$(_esh_request_plan "$input")" || return 1
  _esh_prepare_plan "$plan" "$input" run || return 1

  if [[ "$_ESH_PREPARED_KIND" == execute ]]; then
    _esh_preview "$_ESH_PREPARED_COMMAND"
  fi

  builtin eval "$_ESH_PREPARED_COMMAND"
}

_esh_should_bypass() {
  local input="$1"
  [[ "$ESH_ENABLED" != 1 ||
     -z "${input//[[:space:]]/}" ||
     "$input" == ' '* ||
     "$input" == esh(|' '*) ||
     "$input" == exit(|' '*) ||
     "$input" == logout(|' '*) ||
     "$input" == fg(|' '*) ||
     "$input" == bg(|' '*) ||
     "$input" == jobs(|' '*) ]]
}

_esh_accept_raw() {
  zle accept-line
}

_esh_accept_line() {
  local input="$BUFFER" plan

  if _esh_should_bypass "$input"; then
    zle accept-line
    return
  fi

  zle -I
  plan="$(_esh_request_plan "$input")" || {
    zle -M 'esh could not reach the local model; run `esh setup` or press Ctrl-X Enter to run raw'
    return 1
  }
  _esh_prepare_plan "$plan" "$input" run || {
    zle reset-prompt
    return 1
  }

  if [[ "$_ESH_PREPARED_KIND" == execute ]]; then
    BUFFER="$_ESH_PREPARED_COMMAND"
    CURSOR=${#BUFFER}
    _esh_preview "$BUFFER"
  fi

  zle accept-line
}

_esh_api_ready() {
  _esh_have curl || return 1
  command curl --silent --fail --max-time 2 "$ESH_API_URL/api/tags" >/dev/null 2>&1
}

_esh_model_ready() {
  _esh_api_ready || return 1
  command curl --silent --fail --max-time 5 "$ESH_API_URL/api/tags" |
    command jq -e --arg model "$ESH_MODEL" '
      any(.models[]?; .name == $model or .model == $model)
    ' >/dev/null 2>&1
}

_esh_setup_dependency() {
  local tool="$1" kind="$2" package="$3"
  _esh_have "$tool" && return 0

  _esh_have brew || {
    _esh_error "$tool is missing and Homebrew is unavailable"
    return 1
  }
  _esh_verify_brew "$kind" "$package" || {
    _esh_error "Homebrew could not verify $package"
    return 1
  }

  local install_command
  install_command="$(_esh_install_option_command brew "$kind" "$package")"
  _esh_confirm "Install required dependency with $install_command?" || return 1
  command brew install "--$kind" "$package" || return 1
  rehash
  _esh_have "$tool"
}

_esh_setup() {
  local i

  _esh_setup_dependency jq formula jq || return 1
  _esh_setup_dependency ollama cask ollama-app || return 1

  if ! _esh_api_ready; then
    print -r -- 'Starting the local Ollama service...'
    if [[ "$(uname -s)" == Darwin ]] && _esh_have open; then
      command open -gja Ollama || true
    else
      command ollama serve >"${TMPDIR:-/tmp}/esh-ollama.log" 2>&1 &!
    fi

    for i in {1..15}; do
      _esh_api_ready && break
      sleep 1
    done
  fi

  _esh_api_ready || {
    _esh_error 'Ollama is installed but its local service did not start'
    _esh_error 'start Ollama, then run `esh setup` again'
    return 1
  }

  if ! _esh_model_ready; then
    _esh_confirm "Download local model $ESH_MODEL?" || return 1
    command ollama pull "$ESH_MODEL" || return 1
  fi

  print -r -- 'Esh is ready. Open a new zsh or source esh.plugin.zsh.'
}

_esh_doctor() {
  local failed=0 tool

  printf '%-12s %s\n' zsh "$ZSH_VERSION"
  for tool in curl jq brew npm ollama; do
    if _esh_have "$tool"; then
      printf '%-12s %s\n' "$tool" "${commands[$tool]:-${functions[$tool]:-available}}"
    else
      printf '%-12s %s\n' "$tool" missing
      [[ "$tool" == curl || "$tool" == jq || "$tool" == ollama ]] && failed=1
    fi
  done
  printf '%-12s %s\n' api "$(_esh_api_ready && print ready || print unavailable)"
  printf '%-12s %s\n' model "$(_esh_model_ready && print "$ESH_MODEL ready" || print "$ESH_MODEL unavailable")"

  return "$failed"
}

_esh_usage() {
  print -r -- 'Usage:'
  print -r -- '  esh translate <plain English>  Print the planned shell command'
  print -r -- '  esh run <plain English>        Plan, preview, and run it'
  print -r -- '  esh doctor                     Check local dependencies'
  print -r -- '  esh setup                      Install/prepare Ollama and the model'
  print -r -- '  esh help                       Show this help'
}

esh_cli() {
  local subcommand="${1:-help}"
  (( $# )) && shift

  case "$subcommand" in
    translate)
      (( $# )) || {
        _esh_error 'translate needs a plain-English request'
        return 2
      }
      local plan
      plan="$(_esh_request_plan "$*")" || return 1
      _esh_prepare_plan "$plan" "$*" translate || return 1
      print -r -- "$_ESH_PREPARED_COMMAND"
      ;;
    run)
      (( $# )) || {
        _esh_error 'run needs a plain-English request'
        return 2
      }
      _esh_run_input "$*"
      ;;
    doctor)
      _esh_doctor
      ;;
    setup)
      _esh_setup
      ;;
    help|-h|--help)
      _esh_usage
      ;;
    *)
      _esh_error "unknown subcommand: $subcommand"
      _esh_usage >&2
      return 2
      ;;
  esac
}

# When the library is sourced as a plugin, expose the same CLI without
# requiring bin/esh to be added to PATH.
esh() {
  esh_cli "$@"
}
