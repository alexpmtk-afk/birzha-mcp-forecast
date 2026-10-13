#!/usr/bin/env bash
# Read-only download of 22 source month artifacts; no MOEX/API candle fetching.
set -euo pipefail
: "${GH_TOKEN:?read-only GitHub Actions token required}"
: "${GITHUB_REPOSITORY:?repository required}"
destination="${1:?destination folder required}"
mkdir -p "$destination"
gh run download 38440763073 --repo "$GITHUB_REPOSITORY" --name imoex-intraday-2025-01-duckdb --dir "$destination/imoex-intraday-2025-01-duckdb"
for month in 02 03; do
  gh run download 38441296639 --repo "$GITHUB_REPOSITORY" --name "imoex-intraday-2025-${month}-duckdb" --dir "$destination/imoex-intraday-2025-${month}-duckdb"
done
gh run download 38443892422 --repo "$GITHUB_REPOSITORY" --pattern "imoex-intraday-*-duckdb" --dir "$destination"
echo SOURCE_FOLDER_INVENTORY
find "$destination" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort
