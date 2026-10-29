#!/usr/bin/env bash
# Turn `sccache --show-stats --stats-format=json` into a run summary.
#
# Reads the JSON on stdin and appends a markdown table to $GITHUB_STEP_SUMMARY.
# Written as a script rather than inline because the JSON has optional keys per
# backend (each stat is either a number or a per-key object) and the parsing is
# long enough that inlining it in the workflow would be unreadable.
#
# Usage:
#   sccache --show-stats --stats-format=json | sccache-summary.sh
#
# Empty or unparseable input is not fatal: this runs under `if: always()`, and a
# missing summary must not turn a successful build into a failed one.
set -euo pipefail

payload=$(cat)
if [[ -z "${payload//[[:space:]]/}" ]]; then
	echo "no sccache stats to summarise" >&2
	exit 0
fi

if [[ -z "${GITHUB_STEP_SUMMARY:-}" ]]; then
	echo "GITHUB_STEP_SUMMARY is not set; nothing to write" >&2
	exit 0
fi

python3 - "$payload" <<'PY'
import json
import os
import sys

raw = sys.argv[1]
try:
    doc = json.loads(raw)
except json.JSONDecodeError:
    print("sccache stats were not valid JSON; skipping summary", file=sys.stderr)
    raise SystemExit(0)


def total(stats: object, key: str) -> int:
    """Sum one stat across backends.

    sccache reports each counter either as a plain number or as a mapping of
    backend name to number, depending on how many were configured.
    """
    if not isinstance(stats, dict):
        return 0
    value = stats.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return sum(v for v in value.values() if isinstance(v, int))
    return 0


stats = doc.get("stats") if isinstance(doc, dict) else None
hits = total(stats, "cache_hits")
misses = total(stats, "cache_misses")
errors = total(stats, "cache_errors")
requests = total(stats, "compile_requests")
considered = hits + misses

rate = f"{round(100 * hits / considered)}%" if considered else "n/a"

lines = [
    "## sccache",
    "",
    "| metric | value |",
    "| --- | --- |",
    f"| compile requests | {requests} |",
    f"| cache hits | {hits} |",
    f"| cache misses | {misses} |",
    f"| cache errors | {errors} |",
    f"| hit rate | {rate} |",
    "",
    "A hit rate of n/a means every request was a miss, or no request reached the "
    "cache at all — the second is what an unreachable backend looks like.",
]

path = os.environ["GITHUB_STEP_SUMMARY"]
with open(path, "a", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
PY
