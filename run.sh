#!/usr/bin/env bash
# One-click launcher: installs dependencies on first run and opens the web UI.
set -euo pipefail
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "ffmpeg not found. Install it with: brew install ffmpeg"; exit 1; }
command -v uv >/dev/null || { echo "uv not found. Install it with: brew install uv"; exit 1; }
uv sync --quiet
exec uv run gopro-daily "$@"
