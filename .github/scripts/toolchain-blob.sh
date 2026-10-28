#!/usr/bin/env bash
# Store and fetch the built cross toolchain in Azure Blob Storage.
#
# The GitHub Actions cache API caps a repository at 10 GB and rate-limits
# writes under load; sccache was moved off it for the same reason. The mingw
# toolchain is one ~2 GB tree that only changes when its pinned versions change,
# which makes it a good fit for a plain blob object rather than a cache service.
#
# Usage:
#   CANGJIE_CACHE_CONNECTION_STRING=... toolchain-blob.sh restore <container> <blob> <dest_dir>
#   CANGJIE_CACHE_CONNECTION_STRING=... toolchain-blob.sh save    <container> <blob> <src_dir>
#
# Arguments are positional after the action, so restore/save both take three.
#   CANGJIE_CACHE_CONNECTION_STRING=... toolchain-blob.sh exists  <container> <blob>
#
# `restore` extracts into <dest_dir> and prints hit=true or hit=false on stdout.
# `save` uploads a tar of <src_dir>. `exists` prints true or false.
#
# Every failure is loud: a broken cache must never be reported as a miss,
# because a silent miss costs a full 30-60 minute LLVM build next run.
set -euo pipefail

: "${CANGJIE_CACHE_CONNECTION_STRING:?CANGJIE_CACHE_CONNECTION_STRING required}"

action=${1:?action required (restore|save|exists)}
container=${2:?container required}
blob=${3:?blob name required}

# --no-progress is only accepted by upload/download, not by show.
az_blob() {
	az storage blob "$@" \
		--connection-string "$CANGJIE_CACHE_CONNECTION_STRING" \
		--container-name "$container"
}

blob_exists() {
	# A definite not-found is a miss; anything else that fails is an error, so a
	# throttled or unauthorised probe never masquerades as an empty cache.
	local out
	if out=$(az_blob show --name "$blob" -o none 2>&1); then
		return 0
	fi
	if grep -qiE 'BlobNotFound|not found|404' <<<"$out"; then
		return 1
	fi
	echo "::error::Could not query $container/$blob: $out" >&2
	return 2
}

case "$action" in
restore)
	dest=${4:?destination directory required}
	set +e
	blob_exists
	rc=$?
	set -e
	if ((rc == 2)); then
		exit 1
	elif ((rc == 1)); then
		echo "hit=false"
		exit 0
	fi
	tmp=$(mktemp -d)
	trap 'rm -rf "$tmp"' EXIT
	if ! az_blob download --name "$blob" --file "$tmp/toolchain.tar.gz" --no-progress; then
		echo "::error::Blob $container/$blob exists but could not be downloaded." >&2
		exit 1
	fi
	mkdir -p "$dest"
	# gzip rather than zstd: tar supports it without an extra package, and
	# system_deps does not install zstd.
	tar -xzf "$tmp/toolchain.tar.gz" -C "$dest"
	echo "Restored $container/$blob into $dest"
	echo "hit=true"
	;;
save)
	src=${4:?source directory required}
	if [[ ! -d "$src" ]]; then
		echo "::error::$src does not exist; nothing to cache." >&2
		exit 1
	fi
	tmp=$(mktemp -d)
	trap 'rm -rf "$tmp"' EXIT
	tar -czf "$tmp/toolchain.tar.gz" -C "$(dirname "$src")" "$(basename "$src")"
	echo "Archive size: $(du -h "$tmp/toolchain.tar.gz" | cut -f1)"
	az_blob upload --name "$blob" --file "$tmp/toolchain.tar.gz" --overwrite --no-progress
	echo "Uploaded $container/$blob"
	;;
exists)
	set +e
	blob_exists
	rc=$?
	set -e
	if ((rc == 2)); then
		exit 1
	elif ((rc == 1)); then
		echo "false"
	else
		echo "true"
	fi
	;;
*)
	echo "unknown action $action" >&2
	exit 1
	;;
esac
