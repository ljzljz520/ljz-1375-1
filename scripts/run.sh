#!/usr/bin/env bash
# 启动渔港记忆展厅：编辑器 http://localhost:8000/ ，公开展厅 http://localhost:8000/site/
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 run.py
