#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
site="${root}/_site"
rm -rf "$site"
mkdir -p "$site/reports"
cp -a "$root/web/." "$site/"
if [[ -d "$root/docs/reports" ]]; then
  cp -a "$root/docs/reports/." "$site/reports/"
fi
echo "built $site"
