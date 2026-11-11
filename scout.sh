#!/usr/bin/env bash
# Local Scout launcher: runs the operator CLI with the Python environment Hermes created for this plugin.
#   alias scout="bash ~/.hermes/plugins/local-scout/scout.sh"
#   scout                 # interactive menu
#   scout insights        # any CLI command, e.g. configure --mode off
# HERMES_HOME selects the profile (default ~/.hermes); LOCAL_SCOUT_PYTHON overrides the interpreter.
set -euo pipefail
home="${HERMES_HOME:-$HOME/.hermes}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
py="${LOCAL_SCOUT_PYTHON:-}"
if [ -z "$py" ]; then
  while IFS= read -r venv; do
    if "$venv/bin/python" -c 'import numpy, sklearn' 2>/dev/null; then py="$venv/bin/python"; break; fi
  done < <(ls -td "$home"/installs/*/environments/*/venv 2>/dev/null || true)
fi
if [ -z "$py" ]; then
  echo "Local Scout: no Hermes plugin environment with numpy and scikit-learn under $home/installs." >&2
  echo "Install the plugin first (hermes plugins install Angerosteles/local-scout --enable) or set LOCAL_SCOUT_PYTHON." >&2
  exit 1
fi
# The CLI runs as the package `plugin`; a private link gives the installed directory that name.
run="${XDG_CACHE_HOME:-$HOME/.cache}/local-scout"
mkdir -p "$run" && chmod 700 "$run"
if [ -e "$run/plugin" ] && [ ! -L "$run/plugin" ]; then
  echo "Local Scout: $run/plugin exists and is not a link; remove it and retry." >&2
  exit 1
fi
ln -sfn "$here" "$run/plugin"
# -P: the current directory is not searched for modules, so relative paths stay the caller's and a local plugin/ is ignored.
export PYTHONPATH="$run${PYTHONPATH:+:$PYTHONPATH}"
exec "$py" -P -m plugin.cli --home "$home" "$@"
