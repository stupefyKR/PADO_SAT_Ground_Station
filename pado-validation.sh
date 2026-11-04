#!/usr/bin/env bash
set -Eeuo pipefail
export PADO_ENVIRONMENT=validation
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/pado-gs.sh" "$@"
