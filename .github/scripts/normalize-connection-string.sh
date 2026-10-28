#!/usr/bin/env bash
# Normalise the Azure Storage connection string for sccache's azblob backend.
#
# sccache 0.19.0 builds its request URL by appending "/<container>/<path>" to the
# endpoint it reads from the connection string. A `BlobEndpoint` that already
# ends in "/" therefore yields "...windows.net//sccache/...", and Azure reads the
# empty segment as the container name and answers InvalidUri. The connection
# string `az storage account show-connection-string` returns always has that
# trailing slash.
#
# Usage:
#   normalized=$(normalize-connection-string.sh "$SCCACHE_AZURE_CONNECTION_STRING")
#
# Prints the connection string with trailing slashes stripped from every
# "*Endpoint" value. Exits non-zero on empty input so a missing secret fails
# loudly instead of quietly disabling the cache.
set -euo pipefail

cs=${1:?connection string required}
if [[ -z "$cs" ]]; then
	echo "connection string is empty" >&2
	exit 1
fi

# Split on ';', strip a trailing '/' from any key ending in Endpoint, rejoin.
out=""
IFS=';' read -ra parts <<<"$cs"
for part in "${parts[@]}"; do
	[[ -z "$part" ]] && continue
	key=${part%%=*}
	value=${part#*=}
	if [[ "$key" == *Endpoint ]]; then
		while [[ "$value" == */ ]]; do value="${value%/}"; done
	fi
	if [[ -n "$out" ]]; then out+=";"; fi
	out+="$key=$value"
done

printf '%s\n' "$out"
