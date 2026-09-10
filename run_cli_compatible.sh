#!/usr/bin/env bash
# 本机已有兼容容器和隔离 Python 环境的 CLI 入口；不启动 Web/Telegram。
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$project_dir/.infra/harness-venv/pyvenv.cfg" || ! -f "$project_dir/.infra/python/bin/python3.11" ]]; then
    printf '%s\n' '尚未准备本机隔离运行环境。请使用 Python 3.11 安装 requirements-harness.txt，或参照 docs/runtime-rules-play.md。' >&2
    exit 2
fi
docker_args=(-i)
if [[ -t 0 && -t 1 ]]; then docker_args+=(-t); fi
exec docker run --rm "${docker_args[@]}" \
    -v "$project_dir/.infra/python:/opt/python:ro" \
    -v "$project_dir/.infra:/runtime" \
    -v "$project_dir:/app" -w /app \
    -e PYTHONDONTWRITEBYTECODE=1 -e HARNESS_WORK_DIR=/tmp/storyworld-harness \
    node:16 /runtime/harness-venv/bin/python run_cli.py "$@"
