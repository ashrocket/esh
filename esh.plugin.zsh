# Esh zsh plugin. Source this file from an interactive zsh.

typeset -g ESH_ROOT="${${(%):-%N}:A:h}"
source "$ESH_ROOT/lib/esh.zsh"

if [[ -o interactive ]] && (( ${+widgets} )); then
  zle -N esh-accept-line _esh_accept_line
  zle -N esh-accept-raw _esh_accept_raw

  bindkey -M emacs '^M' esh-accept-line 2>/dev/null
  bindkey -M viins '^M' esh-accept-line 2>/dev/null
  bindkey -M emacs '^X^M' esh-accept-raw 2>/dev/null
  bindkey -M viins '^X^M' esh-accept-raw 2>/dev/null
fi
