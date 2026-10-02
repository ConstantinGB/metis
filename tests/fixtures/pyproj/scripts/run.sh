#!/usr/bin/env bash
set -e
source scripts/lib.sh
log "running"
python alpha/cli.py "$@"
