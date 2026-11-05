#!/usr/bin/env bash
# Kept for muscle-memory: full control now lives in codeg.sh
exec "$(cd "$(dirname "$0")" && pwd)/codeg.sh" "${1:-start}" "${@:2}"
