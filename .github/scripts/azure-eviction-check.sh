#!/usr/bin/env bash
# Report whether the runner VM was evicted by Azure instead of failing on its own.
#
# A Spot VM that loses capacity disappears without shutting down. The runner
# process dies with it, GitHub reports "lost communication with the server", and
# the build log ends mid-step — indistinguishable from a real build failure in
# the Actions UI. Querying the activity log tells the two apart: an eviction
# shows up as a platform-initiated deallocate/delete on the VM, while a failing
# build leaves the VM running until the cleanup job deletes the whole group.
#
# Usage:
#   azure-eviction-check.sh <resource_group> <vm_name> [lookback_hours]
#
# Always exits 0 — this is diagnostic output for an already-finished run, not a
# gate. Prints a GitHub notice on eviction and stays silent otherwise.
#
# A "not found" answer is only trusted when the query actually said not-found.
# Treating any non-zero exit as absence would turn a throttled or unauthorised
# query into a confident — and possibly wrong — eviction notice.
set -euo pipefail

rg=${1:?resource group required}
vm=${2:?vm name required}
lookback_hours=${3:-12}
if [[ ! "$lookback_hours" =~ ^[0-9]+$ ]]; then
	echo "lookback_hours must be a positive integer, got '$lookback_hours'" >&2
	exit 0
fi

# Returns 0 when the resource exists, 1 when it is genuinely absent, and exits
# the script when the query itself failed (throttling, bad credentials, ...).
resource_missing() {
	local label="$1"
	shift
	local out
	if out=$("$@" 2>&1); then
		return 1
	fi
	if grep -qiE 'ResourceNotFound|could not be found|not found' <<<"$out"; then
		return 0
	fi
	echo "azure-eviction-check: could not query $label: $out" >&2
	exit 0
}

if resource_missing "resource group $rg" az group show -n "$rg"; then
	echo "Resource group $rg is already gone; nothing to inspect."
	exit 0
fi

if ! resource_missing "VM $vm" az vm show -g "$rg" -n "$vm"; then
	echo "VM $vm still present in $rg; the run ended on its own."
	exit 0
fi

start_time=$(date -u -d "${lookback_hours} hours ago" +%Y-%m-%dT%H:%M:%SZ)
if ! events=$(az monitor activity-log list \
	-g "$rg" \
	--start-time "$start_time" \
	--query "[?status.value=='Succeeded' && (contains(operationName.value,'virtualMachines/deallocate') || contains(operationName.value,'virtualMachines/delete'))].{time:eventTimestamp,op:operationName.value,caller:caller}" \
	-o tsv 2>&1); then
	# Never claim "no events" when the query itself failed.
	echo "Could not read the activity log for $rg: $events" >&2
	exit 0
fi

if [[ -z "$events" ]]; then
	echo "VM $vm is gone but the activity log has no successful deallocate/delete event for it." >&2
	exit 0
fi

echo "VM $vm disappeared; activity log entries:"
echo "$events" | sort

echo "::notice::VM $vm disappeared and Azure logged a successful deallocate/delete. If the caller above is not the deployment service principal, the VM was reclaimed by Azure (Spot eviction) — re-run the workflow. If it was deleted by our own cleanup, the job failed on its own and the log above the break is the real story."
