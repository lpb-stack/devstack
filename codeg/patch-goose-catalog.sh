#!/usr/bin/env bash
# Kept for muscle-memory: same as ./codeg.sh patch
exec "$(cd "$(dirname "$0")" && pwd)/codeg.sh" patch "${@}"
