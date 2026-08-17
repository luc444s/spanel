#!/usr/bin/env bash

_spanel_helper_path() {
  if [ -n "${BASH_SOURCE[0]-}" ]; then
    printf '%s\n' "${BASH_SOURCE[0]}"
    return 0
  fi
  if [ -n "${ZSH_VERSION-}" ]; then
    printf '%s\n' "${(%):-%N}"
    return 0
  fi
  return 1
}

_SPANEL_HELPER_PATH="$(_spanel_helper_path)" || return 1
SPANEL_VENV_PROJECT_ROOT="$(cd "$(dirname "$_SPANEL_HELPER_PATH")/.." && pwd)"
SPANEL_VENV_PATH="$SPANEL_VENV_PROJECT_ROOT/.venv"

_spanel_auto_activate_hook() {
  case "$PWD/" in
    "$SPANEL_VENV_PROJECT_ROOT"/*|"$SPANEL_VENV_PROJECT_ROOT/")
      if [ -f "$SPANEL_VENV_PATH/bin/activate" ] && [ "${VIRTUAL_ENV:-}" != "$SPANEL_VENV_PATH" ]; then
        # shellcheck disable=SC1090
        . "$SPANEL_VENV_PATH/bin/activate"
        export SPANEL_VENV_AUTO_ACTIVE=1
      fi
      ;;
    *)
      if [ "${SPANEL_VENV_AUTO_ACTIVE:-0}" = "1" ] && [ "${VIRTUAL_ENV:-}" = "$SPANEL_VENV_PATH" ]; then
        deactivate >/dev/null 2>&1 || true
        unset SPANEL_VENV_AUTO_ACTIVE
      fi
      ;;
  esac
}

if [ -n "${ZSH_VERSION-}" ]; then
  typeset -ga chpwd_functions
  if [[ ! " ${chpwd_functions[*]} " == *" _spanel_auto_activate_hook "* ]]; then
    chpwd_functions+=(_spanel_auto_activate_hook)
  fi
else
  case ";${PROMPT_COMMAND-};" in
    *";_spanel_auto_activate_hook;"*) ;;
    *) PROMPT_COMMAND="_spanel_auto_activate_hook${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
  esac
fi

_spanel_auto_activate_hook
