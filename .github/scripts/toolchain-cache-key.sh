#!/usr/bin/env bash
# Print the blob object name for a built toolchain.
#
# The identifier itself is derived in Python from the toolchain's pinned inputs
# (llvm-mingw tag, LLVM commit, openssl version, target triple; or the ncurses /
# libedit versions for the static libs), so bumping any pinned version changes
# the name and the previous object stops being reachable. Maintaining the string
# by hand here would let a version bump keep reusing a stale blob.
#
# The object lives in the storage account's `toolchain` container, which the
# lifecycle rule matches by its full path prefix (`toolchain/`), so the name
# itself carries no prefix. The rule deletes objects older than 60 days, which
# bounds how long a superseded toolchain lingers after its key changed.
#
# Usage: toolchain-cache-key.sh <linux-x64|windows-x64>
set -euo pipefail

target=${1:?target required (linux-x64|windows-x64)}
case "$target" in
linux-x64 | windows-x64) ;;
*)
	echo "unknown target: $target" >&2
	exit 1
	;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
key=$(uv run --quiet cangjie-build toolchain-cache-key --target "$target")
printf '%s.tar.gz\n' "$key"
