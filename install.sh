#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Job Radar's one-command installer currently supports macOS." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

uv_bin="$(command -v uv || true)"
if [[ -z "$uv_bin" ]]; then
  installer_file="$(mktemp)"
  trap 'rm -f "$installer_file"' EXIT
  curl -fsSL https://astral.sh/uv/install.sh -o "$installer_file"
  UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$installer_file"
  uv_bin="$HOME/.local/bin/uv"
  rm -f "$installer_file"
  trap - EXIT
fi

if [[ "${JOB_RADAR_INSTALL_TEST_MODE:-}" != "1" ]]; then
  if ! command -v ollama >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
      brew install ollama
    else
      ollama_installer_file="$(mktemp)"
      trap 'rm -f "$ollama_installer_file"' EXIT
      curl -fsSL https://ollama.com/install.sh -o "$ollama_installer_file"
      sh "$ollama_installer_file"
      rm -f "$ollama_installer_file"
      trap - EXIT
    fi
  fi
  if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1 && brew list --formula ollama >/dev/null 2>&1; then
      brew services start ollama
    elif [[ -d /Applications/Ollama.app ]]; then
      open -a Ollama
    elif command -v ollama >/dev/null 2>&1; then
      mkdir -p "$HOME/Library/Application Support/JobRadar"
      nohup ollama serve > "$HOME/Library/Application Support/JobRadar/ollama.log" 2>&1 </dev/null &
    fi
  fi
fi

"$uv_bin" sync --locked
"$uv_bin" run --no-sync playwright install chromium
"$uv_bin" run --no-sync job-radar install-service

if [[ "${JOB_RADAR_INSTALL_TEST_MODE:-}" == "1" ]]; then
  exit 0
fi

port="${JOB_RADAR_PORT:-8787}"
ready=0
for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:${port}/api/status" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "$ready" != "1" ]]; then
  echo "Job Radar was installed but did not start. Check ~/Library/Application Support/JobRadar/service.stderr.log" >&2
  exit 1
fi

open "http://127.0.0.1:${port}/#home"
echo "Job Radar is ready at http://127.0.0.1:${port}/"
