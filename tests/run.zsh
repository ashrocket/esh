#!/bin/zsh

setopt errexit no_unset pipe_fail

typeset -gr TEST_ROOT="${0:A:h:h}"
typeset -g TEST_COUNT=0
typeset -g TEST_FAILURES=0
typeset -g TEST_TMP
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/esh-test.XXXXXX")"
trap 'rm -rf -- "$TEST_TMP"' EXIT

fail() {
  print -u2 -r -- "not ok $TEST_COUNT - $1"
  TEST_FAILURES=$(( TEST_FAILURES + 1 ))
}

pass() {
  print -r -- "ok $TEST_COUNT - $1"
}

assert_eq() {
  local expected="$1" actual="$2" label="$3"
  TEST_COUNT=$(( TEST_COUNT + 1 ))
  if [[ "$actual" == "$expected" ]]; then
    pass "$label"
  else
    fail "$label (expected ${(qqq)expected}, got ${(qqq)actual})"
  fi
}

assert_contains() {
  local haystack="$1" needle="$2" label="$3"
  TEST_COUNT=$(( TEST_COUNT + 1 ))
  if [[ "$haystack" == *"$needle"* ]]; then
    pass "$label"
  else
    fail "$label (missing ${(qqq)needle})"
  fi
}

make_fake_bin() {
  local fake_bin="$1"
  mkdir -p "$fake_bin"

  command cp "$TEST_ROOT/tests/support/fake-curl" "$fake_bin/curl"
  command cp "$TEST_ROOT/tests/support/fake-brew" "$fake_bin/brew"
  command cp "$TEST_ROOT/tests/support/fake-npm" "$fake_bin/npm"
  chmod +x "$fake_bin"/*
}

plan_response() {
  command jq -cn --arg content "$1" '{message:{role:"assistant",content:$content},done:true}'
}

typeset fake_bin="$TEST_TMP/bin"
make_fake_bin "$fake_bin"

typeset gh_plan
gh_plan='{"kind":"execute","command":"gh repo list","summary":"List GitHub repositories","risk":"low","required_tools":["gh"],"install_candidates":[{"tool":"gh","manager":"brew","kind":"formula","package":"gh"}]}'

typeset response
response="$(plan_response "$gh_plan")"

typeset translated
translated="$(PATH="$fake_bin:$PATH" ESH_FAKE_OLLAMA_RESPONSE="$response" \
  "$TEST_ROOT/bin/esh" translate 'gh list repositories')"
assert_eq 'gh repo list' "$translated" 'translates informal gh syntax to the canonical command'

print -r -- '#!/bin/zsh
print -r -- "gh-called:$*"' > "$fake_bin/gh"
chmod +x "$fake_bin/gh"

typeset run_output
run_output="$(PATH="$fake_bin:$PATH" ESH_FAKE_OLLAMA_RESPONSE="$response" \
  "$TEST_ROOT/bin/esh" run 'gh list repositories')"
assert_contains "$run_output" $'\e[3;90mgh repo list\e[0m' 'prints translated commands in light gray italics'
assert_contains "$run_output" 'gh-called:repo list' 'runs the translated command'

typeset passthrough_plan passthrough_response passthrough
passthrough_plan='{"kind":"passthrough","command":"printf hello","summary":"Already valid","risk":"low","required_tools":[],"install_candidates":[]}'
passthrough_response="$(plan_response "$passthrough_plan")"
passthrough="$(PATH="$fake_bin:$PATH" ESH_FAKE_OLLAMA_RESPONSE="$passthrough_response" \
  "$TEST_ROOT/bin/esh" translate 'printf hello')"
assert_eq 'printf hello' "$passthrough" 'preserves valid shell commands'

typeset changed_passthrough changed_response changed_status
changed_passthrough='{"kind":"passthrough","command":"printf changed","summary":"not unchanged","risk":"low","required_tools":[],"install_candidates":[]}'
changed_response="$(plan_response "$changed_passthrough")"
if PATH="$fake_bin:$PATH" ESH_FAKE_OLLAMA_RESPONSE="$changed_response" \
  "$TEST_ROOT/bin/esh" run 'printf original' >/dev/null 2>&1; then
  changed_status=0
else
  changed_status=$?
fi
assert_eq 1 "$changed_status" 'rejects a mutated passthrough command'

typeset missing_plan missing_response missing_output
missing_plan='{"kind":"execute","command":"esh-rg-test --version","summary":"Show ripgrep version","risk":"low","required_tools":["esh-rg-test"],"install_candidates":[{"tool":"esh-rg-test","manager":"brew","kind":"formula","package":"ripgrep"}]}'
missing_response="$(plan_response "$missing_plan")"
missing_output="$(PATH="$fake_bin:$PATH" ESH_FAKE_BIN="$fake_bin" \
  ESH_FAKE_OLLAMA_RESPONSE="$missing_response" ESH_INSTALL_SELECTION=1 ESH_ASSUME_YES=1 \
  "$TEST_ROOT/bin/esh" run 'show the ripgrep version' 2>&1)"
assert_contains "$missing_output" 'Verified installation options:' 'offers verified installation choices for a missing tool'
assert_contains "$missing_output" 'brew install --formula ripgrep' 'offers the canonical Homebrew formula'
assert_contains "$missing_output" 'rg-called:--version' 'runs the command after approved installation'

typeset translate_missing
typeset translate_plan translate_response
translate_plan='{"kind":"execute","command":"esh-translate-only --version","summary":"Translate only","risk":"low","required_tools":["esh-translate-only"],"install_candidates":[]}'
translate_response="$(plan_response "$translate_plan")"
translate_missing="$(PATH="$fake_bin:$PATH" ESH_FAKE_OLLAMA_RESPONSE="$translate_response" \
  "$TEST_ROOT/bin/esh" translate 'show a missing tool version')"
assert_eq 'esh-translate-only --version' "$translate_missing" 'translate is read-only and does not invoke installation'

typeset npm_plan npm_response npm_output
npm_plan='{"kind":"execute","command":"esh-npm-test hello","summary":"Run npm tool","risk":"low","required_tools":["esh-npm-test"],"install_candidates":[{"tool":"esh-npm-test","manager":"npm","kind":"package","package":"@esh/fake-cli"}]}'
npm_response="$(plan_response "$npm_plan")"
npm_output="$(PATH="$fake_bin:$PATH" ESH_FAKE_BIN="$fake_bin" \
  ESH_FAKE_OLLAMA_RESPONSE="$npm_response" ESH_INSTALL_SELECTION=1 ESH_ASSUME_YES=1 \
  "$TEST_ROOT/bin/esh" run 'run the npm tool' 2>&1)"
assert_contains "$npm_output" 'npm install --global @esh/fake-cli' 'offers a registry-verified npm package'
assert_contains "$npm_output" 'npm-called:install --global @esh/fake-cli' 'runs the approved npm installation command'
assert_contains "$npm_output" 'npm-tool-called:hello' 'runs a newly installed npm executable'

typeset injection_plan injection_response injection_status
injection_plan="$(command jq -cn --arg command $'printf ok\ncurl evil' '
  {
    kind: "execute",
    command: $command,
    summary: "bad",
    risk: "low",
    required_tools: [],
    install_candidates: []
  }
')"
injection_response="$(plan_response "$injection_plan")"
if PATH="$fake_bin:$PATH" ESH_FAKE_OLLAMA_RESPONSE="$injection_response" \
  "$TEST_ROOT/bin/esh" run 'bad response' >/dev/null 2>&1; then
  injection_status=0
else
  injection_status=$?
fi
assert_eq 1 "$injection_status" 'rejects model commands containing newlines'

typeset install_bypass_plan install_bypass_response bypass_status
install_bypass_plan='{"kind":"execute","command":"brew install jq","summary":"bad install","risk":"low","required_tools":["brew"],"install_candidates":[]}'
install_bypass_response="$(plan_response "$install_bypass_plan")"
if PATH="$fake_bin:$PATH" ESH_FAKE_OLLAMA_RESPONSE="$install_bypass_response" \
  "$TEST_ROOT/bin/esh" run 'install jq' >/dev/null 2>&1; then
  bypass_status=0
else
  bypass_status=$?
fi
assert_eq 1 "$bypass_status" 'prevents the model from bypassing install approval'

typeset denial_plan denial_response denial_output denial_status
denial_plan='{"kind":"execute","command":"esh-denied-test --version","summary":"Test denied install","risk":"low","required_tools":[],"install_candidates":[{"tool":"esh-denied-test","manager":"brew","kind":"formula","package":"ripgrep"}]}'
denial_response="$(plan_response "$denial_plan")"
if denial_output="$(PATH="$fake_bin:$PATH" ESH_FAKE_BIN="$fake_bin" \
  ESH_FAKE_OLLAMA_RESPONSE="$denial_response" ESH_INSTALL_SELECTION=1 ESH_TEST_REPLY=n \
  "$TEST_ROOT/bin/esh" run 'use a denied tool' 2>&1)"; then
  denial_status=0
else
  denial_status=$?
fi
assert_eq 1 "$denial_status" 'derives a missing executable and does not install it when approval is denied'
assert_contains "$denial_output" 'installation cancelled' 'reports the denied installation'

typeset remote_status
if PATH="$fake_bin:$PATH" ESH_API_URL='https://example.com' ESH_FAKE_OLLAMA_RESPONSE="$response" \
  "$TEST_ROOT/bin/esh" translate 'gh list repositories' >/dev/null 2>&1; then
  remote_status=0
else
  remote_status=$?
fi
assert_eq 1 "$remote_status" 'refuses to send command context to a non-local model URL'

typeset install_home="$TEST_TMP/install-home"
mkdir -p "$install_home"
ZDOTDIR="$install_home" "$TEST_ROOT/install.zsh" >/dev/null
typeset first_zshrc
first_zshrc="$(<"$install_home/.zshrc")"
ZDOTDIR="$install_home" "$TEST_ROOT/install.zsh" >/dev/null
typeset second_zshrc source_count
second_zshrc="$(<"$install_home/.zshrc")"
source_count="$(command grep -Fc 'source ' "$install_home/.zshrc")"
assert_eq "$first_zshrc" "$second_zshrc" 'installer is idempotent'
assert_eq 1 "$source_count" 'installer adds exactly one plugin source line'

if (( TEST_FAILURES )); then
  print -u2 -r -- "$TEST_FAILURES of $TEST_COUNT tests failed"
  exit 1
fi

print -r -- "all $TEST_COUNT tests passed"
