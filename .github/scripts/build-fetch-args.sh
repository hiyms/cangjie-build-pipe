#!/usr/bin/env bash
# Translate four per-repo "source" inputs into the --repo-url / --repo-tag
# argv that `cangjie-build fetch` expects.
#
# Each source input is the repository URL, optionally followed by `:` and the
# branch, tag or commit to build from:
#
#   https://atomgit.com/owner/repo.git                     (ref = the global --tag)
#   https://atomgit.com/owner/repo.git:1.0.2               (tag)
#   https://atomgit.com/owner/repo.git:011f9c8             (commit)
#   https://atomgit.com/owner/repo.git:feat/windows-cross  (branch; slashes allowed)
#   atomgit.com/owner/repo.git:feat/windows-cross          (scheme-less; https:// prepended)
#   <empty>                                                (no override)
#
# Usage:
#   COMPILER_SOURCE=... RUNTIME_SOURCE=... TOOLS_SOURCE=... STDX_SOURCE=... \
#     build-fetch-args.sh
#
# Prints the argv tokens, one per line, on stdout. The caller is expected to
# read them into a bash array, e.g.
#
#   mapfile -t fetch_args < <(.github/scripts/build-fetch-args.sh)
#   uv run cangjie-build fetch "${fetch_args[@]}"
set -euo pipefail

emit() {
	local name="$1" raw="$2"
	raw="${raw#"${raw%%[![:space:]]*}"}" # trim leading whitespace
	raw="${raw%"${raw##*[![:space:]]}"}" # trim trailing whitespace
	[[ -z "$raw" ]] && return 0

	local scheme="" rest="$raw"
	if [[ "$raw" =~ ^([A-Za-z][A-Za-z0-9+.-]*://)(.*)$ ]]; then
		scheme="${BASH_REMATCH[1]}"
		rest="${BASH_REMATCH[2]}"
	fi

	# The ref separator is a colon in the path, never the scheme colon and never
	# a port colon. Branch names contain slashes (feat/windows-cross), so the
	# path has to be separated from the host before the ref is split off, and a
	# git refname can never contain a colon, so the last path colon is the one.
	local hostport="$rest" path=""
	if [[ "$rest" == */* ]]; then
		hostport="${rest%%/*}"
		path="/${rest#*/}"
	fi

	# scp-like `git@host:owner/repo.git` puts the path after a colon instead of
	# a slash. Accepting it here would emit a URL with a colon in the host, and
	# the clone would fail far from this line, so it is refused instead.
	if [[ -z "$scheme" && "$hostport" == *:* && ! "${hostport##*:}" =~ ^[0-9]+$ ]]; then
		echo "$name: '$raw' looks like scp syntax; write it as ssh://git@host/path/to/repo.git" >&2
		exit 1
	fi

	local url_path="$path" branch=""
	if [[ "$path" == *:* ]]; then
		url_path="${path%:*}"
		branch="${path##*:}"
	fi

	local url_part="${scheme}${hostport}${url_path}"
	case "$url_part" in
	http://* | https://* | git://* | ssh://*) ;;
	*) url_part="https://$url_part" ;;
	esac

	printf -- '--repo-url\n%s=%s\n' "$name" "$url_part"
	if [[ -n "$branch" ]]; then
		printf -- '--repo-tag\n%s=%s\n' "$name" "$branch"
	fi
}

emit compiler "${COMPILER_SOURCE:-}"
emit runtime "${RUNTIME_SOURCE:-}"
emit tools "${TOOLS_SOURCE:-}"
emit stdx "${STDX_SOURCE:-}"
