#!/usr/bin/env bash
# Short entry point for the validation environment.
set -Eeuo pipefail
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/pado-validation.sh" "$@"
