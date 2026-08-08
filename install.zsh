#!/bin/zsh

set -eu

root="${0:A:h}"
zdotdir="${ZDOTDIR:-$HOME}"
zshrc="$zdotdir/.zshrc"
source_line="source ${(q)root}/esh.plugin.zsh"

mkdir -p "$zdotdir"
touch "$zshrc"

if grep -Fqx -- "$source_line" "$zshrc"; then
  print -r -- "Esh is already configured in $zshrc"
else
  print -r -- '' >> "$zshrc"
  print -r -- "$source_line" >> "$zshrc"
  print -r -- "Added Esh to $zshrc"
fi

print -r -- 'Open a new terminal, then run: esh setup'
