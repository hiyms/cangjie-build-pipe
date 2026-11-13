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
#   #feat/windows-cross                                   (default URL, given ref)
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

trim_into() {
	local __value="$2"
	__value="${__value#"${__value%%[![:space:]]*}"}" # drop leading whitespace
	__value="${__value%"${__value##*[![:space:]]}"}" # drop trailing whitespace
	printf -v "$1" '%s' "$__value"
}

emit() {
	local name="$1" raw ref=""
	trim_into raw "$2"
	[[ -z "$raw" ]] && return 0

	local url_part="$raw"
	if [[ "$raw" == *"#"* ]]; then
		local before_hash="${raw%%#*}"
		local after_hash="${raw#*#}"
		trim_into url_part "$before_hash"
		trim_into ref "$after_hash"
	fi

	# An empty URL is how a caller asks for the default repository of that name
	# with its own ref, e.g. `#feat/windows-cross`. A bare `#` names no ref and
	# no URL, so there is nothing to override with.
	if [[ "$raw" == *"#"* && -z "$ref" ]]; then
		echo "$name: '$raw' ends with '#' and no ref; write '#ref' for the default repository, or URL#ref" >&2
		exit 1
	fi
	if [[ "$ref" == *[[:space:]]* ]]; then
		echo "$name: '$raw' has whitespace inside the ref" >&2
		exit 1
	fi
	# A ref beginning with '-' reaches git as an option (`git fetch origin --upload-pack=...`).
	if [[ "$ref" == -* ]]; then
		echo "$name: '$raw' has a ref beginning with '-', which git reads as an option" >&2
		exit 1
	fi
	if [[ -z "$url_part" ]]; then
		printf -- '--repo-tag\n%s=%s\n' "$name" "$ref"
		return 0
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
	if [[ -n "$ref" ]]; then
		printf -- '--repo-tag\n%s=%s\n' "$name" "$ref"
	fi
}

emit compiler "${COMPILER_SOURCE:-}"
emit runtime "${RUNTIME_SOURCE:-}"
emit tools "${TOOLS_SOURCE:-}"
emit stdx "${STDX_SOURCE:-}"
