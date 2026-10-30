#!/usr/bin/env bash
# Translate four per-repo "source" inputs into the --repo-url / --repo-tag
# argv that `cangjie-build fetch` expects.
#
# Each source input is the clone URL, optionally followed by `#` and the branch,
# tag or commit to build from:
#
#   https://atomgit.com/owner/repo.git                     (ref = the global --tag)
#   https://atomgit.com/owner/repo.git#1.0.2               (tag)
#   https://atomgit.com/owner/repo.git#011f9c8             (commit)
#   https://atomgit.com/owner/repo.git#feat/windows-cross  (branch; slashes allowed)
#   atomgit.com/owner/repo.git#feat/windows-cross          (scheme-less; https:// prepended)
#   <empty>                                                (no override)
#
# `#` separates because a URL already carries colons: in the scheme, and in a
# port. A branch name carries slashes, so neither a colon nor a slash can be the
# separator without guessing.
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

	local url_part="$raw" branch=""
	if [[ "$raw" == *"#"* ]]; then
		url_part="${raw%%#*}"
		branch="${raw#*#}"
	fi

	# A colon in the path is the separator this script used to accept. Left alone
	# it reaches git as part of the URL, and the clone fails with a 403 about a
	# repository named after the branch, which says nothing about the typo.
	local after_scheme="$url_part"
	if [[ "$url_part" == *"://"* ]]; then
		after_scheme="${url_part#*://}"
	fi
	if [[ "$after_scheme" == */* && "/${after_scheme#*/}" == *:* ]]; then
		echo "$name: '$raw' separates the ref with ':' in the path; write URL#ref instead" >&2
		exit 1
	fi

	# scp-like `git@host:owner/repo.git` carries its path after a colon. Prefixing
	# it with https:// would put a colon in the host, and the clone would fail far
	# from this line, so it is refused here instead.
	if [[ "$url_part" != *"://"* && "${url_part%%/*}" == *:* ]] &&
		[[ ! "${url_part%%/*}" =~ :[0-9]+$ ]]; then
		echo "$name: '$raw' looks like scp syntax; write it as ssh://git@host/path/to/repo.git" >&2
		exit 1
	fi

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
