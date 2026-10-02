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
