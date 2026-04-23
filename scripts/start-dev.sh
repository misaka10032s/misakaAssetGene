#!/usr/bin/env bash
set -euo pipefail

export MISAKA_ENV="${MISAKA_ENV:-dev}"
export VITE_MISAKA_ENV="${VITE_MISAKA_ENV:-dev}"

python ./scripts/dev_stack.py
