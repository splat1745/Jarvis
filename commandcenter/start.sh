#!/usr/bin/env bash
# JARVIS Command Center launcher (delegates to the cross-platform bootstrap)
cd "$(dirname "$0")"
exec python3 start.py "$@"
