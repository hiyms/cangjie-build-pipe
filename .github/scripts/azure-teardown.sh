#!/usr/bin/env bash
# Tear down a single ephemeral runner: delete its Azure resource group and
# force-deregister the runner in case the VM never finished its job.
#
# Usage:
#   GH_TOKEN=<pat> azure-teardown.sh <repo> <runner_name> <resource_group>
set -euo pipefail

repo=${1:?repo required}
name=${2:?runner name required}
rg=${3:?resource group required}
: "${GH_TOKEN:?GH_TOKEN required}"

if out=$(az group show -n "$rg" -o none 2>&1); then
	# --no-wait returns as soon as the request is accepted, so a blocked or
	# transient failure would be reported as success while the VM keeps
	# billing until the hourly orphan reaper notices. Dispatch, then poll.
	az group delete -n "$rg" --yes --no-wait
	echo "Resource group $rg delete dispatched; waiting for it to disappear."
	deadline=$((SECONDS + 900))
	while ((SECONDS < deadline)); do
		if ! az group exists -n "$rg" -o tsv | grep -qi true; then
			echo "Resource group $rg is gone."
			break
		fi
		sleep 15
	done
	if az group exists -n "$rg" -o tsv | grep -qi true; then
		echo "::error::Resource group $rg still exists after 15 minutes; it is still billing." >&2
		exit 1
	fi
elif grep -qiE 'ResourceNotFound|could not be found|not found' <<<"$out"; then
	echo "Resource group $rg already gone."
else
	# Never treat a failed query as absence: that is how a live group leaks.
	echo "::error::Could not query resource group $rg: $out" >&2
	exit 1
fi

id=$(gh api "/repos/${repo}/actions/runners" \
	--jq "[.runners[] | select(.name == \"${name}\")][0].id // empty")
if [[ -n "$id" ]]; then
	echo "Force-deleting runner registration $id"
	gh api -X DELETE "/repos/${repo}/actions/runners/${id}"
else
	echo "Runner $name already deregistered."
fi
